"""Booksim2 backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md,
docs/decisions.md D5/D6): real NoC simulation — 2D and 3D k-ary n-cube networks — via
Booksim2 (github.com/booksim/booksim2, BSD-3-Clause, the standard reference NoC simulator).
**Real chiplet inter-die (D2D) interconnect** (docs/decisions.md D66): an architecture declaring
`interconnect.chiplet_noc` instead of `interconnect.noc` is dispatched to a genuinely different
Booksim2 topology family (`anynet` — a real, arbitrary router/node connectivity file with real
per-link latency, not a KNCube parameter) via the same binary, same real cycle-accurate simulator.
This is real *thermal-adjacent* D2D **interconnect** modeling, distinct from
`evaluator/thermal`'s own real multi-die **thermal** stacking (D65) — two genuinely separate
concerns about a chiplet system, each real, neither substituting for the other.

v0.1 status: `Candidate.arch` must be an inline Architecture IR dict with an `interconnect.noc`
block `architecture_translator.py` can translate (topology in {"mesh", "torus"}, uniform
`dimensions`) — `None` is not accepted (there is no fixed default NoC the way
`evaluator/rtl`/`evaluator/systemc` have a fixed default `mac_array`). `Candidate.workload` is
still required by the ABI and still hashed into `Result.provenance`, but its content doesn't
drive simulated traffic — see `architecture_translator.py`'s module docstring for why, honestly,
not silently. `Candidate.mapping` must be `None`: Booksim2's synthetic traffic generation has no
mapping concept.

No independent functional checker exists for this adapter (unlike `evaluator/rtl`'s real
self-check against a Python reference) — `Result.validity.ok` is a placeholder `True`, same
honest gap `evaluator/zigzag`/`evaluator/timeloop` already have, not a claim of a real check
that doesn't exist.

Booksim2 itself is neither vendored nor pip-installed: `_ensure_booksim_binary` clones and builds
it on first use per `BooksimEvaluator` instance (same "fetch an external resource once, cache it"
shape `evaluator/timeloop` already uses for its Docker image, and `evaluator/systemc` uses for
its own compiled binary). Needs `git`, `g++`, `make`, `flex`, and `bison` on `PATH` — `flex`/
`bison` are the one real gap found empirically (every other Booksim2 source file builds with
plain g++); see `flake.nix`'s `.#default` shell, which now provides both.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import flux_ir
from flux_evaluator_abi.tools import tails, ToolSource, build_step, clone
from flux_evaluator_abi import (
    SequentialBatch,
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)

from .architecture_translator import (
    ChipletTopology,
    architecture_ir_to_booksim_config,
    architecture_ir_to_chiplet_anynet,
    dump_booksim_config,
)
from .errors import NotExpressibleError

_BOOKSIM_REPO_URL = "https://github.com/booksim/booksim2.git"
# Take the LAST match, never the first: Booksim2 prints this line once per sample period during
# warm-up and again in its final "Overall Traffic Statistics" section, and only the last reflects
# the converged window. Parsing the first silently reported warm-up numbers (D25).
# The float pattern must accept an exponent — a bare `[\d.]+` truncates C++'s default scientific
# formatting, reading "1.23457e+06" as 1.23457 with no error (D181).
_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_LATENCY_RE = re.compile(rf"^Packet latency average\s*=\s*({_FLOAT})", re.MULTILINE)
_HOPS_RE = re.compile(rf"^Hops average\s*=\s*({_FLOAT})", re.MULTILINE)


class BooksimEvaluator(SequentialBatch):
    """Runs a real Booksim2 simulation of a translated Architecture IR `interconnect.noc` block.
    The NoC-DSE counterpart to `evaluator/rtl`/`evaluator/systemc` for compute: same "real
    external tool, thin adapter, fail loudly outside its scope" shape, different domain.
    """

    name = "booksim"

    def __init__(self, *, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s
        self._source = ToolSource("booksim", self._build)      # cloned and built once (D437)

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.arch, dict):
            raise NotExpressibleError(
                "BooksimEvaluator v0.1 requires an inline Architecture IR dict as "
                "Candidate.arch with an interconnect.noc block (see architecture_translator.py) "
                "— there is no fixed default NoC to fall back to."
            )
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "BooksimEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet) — its content isn't used to drive "
                "traffic (see module docstring), but it's still required and hashed for "
                "provenance, same as every other evaluator here."
            )
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "BooksimEvaluator v0.1 does not use Mapping IR: Booksim2's synthetic traffic "
                "generation has no mapping concept — leave Candidate.mapping as None."
            )

        workload_hash = flux_ir.content_hash(candidate.workload)
        arch_hash = flux_ir.content_hash(candidate.arch)

        # Real chiplet inter-die (D2D) interconnect (docs/decisions.md D66/D67) is a genuinely
        # different Booksim2 topology family (anynet, not KNCube) — checked first so an
        # architecture declaring `interconnect.chiplet_noc` never falls through to the KNCube
        # path's own, unrelated NotExpressibleError.
        if "chiplet_noc" in candidate.arch.get("interconnect", {}):
            chiplet = architecture_ir_to_chiplet_anynet(candidate.arch)
            packet_latency, hops_average = self._run_chiplet_booksim(chiplet, arch_hash)
            # A real, accurate label — D67 generalized this beyond a fixed "2 dies, 1 link"
            # shape, so the die/link counts have to reflect the actual topology, not be hardcoded.
            topology_label = f"anynet-chiplet-{chiplet.die_count}die-{chiplet.d2d_link_count}link"
        else:
            config = architecture_ir_to_booksim_config(candidate.arch)
            packet_latency, hops_average = self._run_booksim(config, arch_hash)
            topology_label = f"{config['topology']}-k{config['k']}-n{config['n']}"

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "latency_cycles" in metrics:
            result_metrics["latency_cycles"] = Estimate(
                value=packet_latency, ci_low=packet_latency, ci_high=packet_latency,
                unit="cycles", method=Method.SIMULATED,
            )

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none (placeholder, same as zigzag/timeloop)"),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(
                limiter=Limiter.NOC,
                per_level_utilisation={"hops_average": hops_average} if hops_average is not None else {},
            ),
            provenance=Provenance(
                evaluator="booksim2@real",
                inputs={
                    "workload_hash": workload_hash,
                    "arch_hash": arch_hash,
                    "topology": topology_label,
                },
            ),
            escalation=Escalation(recommended=False),
        )


    def _ensure_booksim_binary(self) -> Path:
        return self._source.ensure()

    def _build(self, work_dir: Path) -> Path:
        src_dir = clone(_BOOKSIM_REPO_URL, work_dir / "booksim2", what="Booksim2",
                        timeout_s=self.timeout_s) / "src"
        build_step(["make", "-j2"], cwd=src_dir, what="Booksim2 build", timeout_s=self.timeout_s,
                   hint="needs flex and bison on PATH (nix develop .#default provides both); "
                        "every other Booksim2 source file builds with plain g++")
        return src_dir / "booksim"

    def _run_chiplet_booksim(self, chiplet: ChipletTopology, arch_hash: str) -> tuple[float, float | None]:
        """Real chiplet inter-die (D2D) interconnect (docs/decisions.md D66) — same Booksim2
        binary, same real cycle-accurate simulator, a genuinely different topology (`anynet`, an
        arbitrary router/node connectivity file with real per-link latency) from `_run_booksim`'s
        own KNCube path. Output parsing is unchanged — verified directly, not assumed, that
        Booksim2's `anynet` network prints the identical "Packet latency average"/"Hops average"
        lines `_LATENCY_RE`/`_HOPS_RE` already parse for KNCube runs, via the same
        `TrafficManager` stats printer regardless of topology.
        """
        binary_path = self._ensure_booksim_binary()
        with tempfile.TemporaryDirectory(prefix=f"flux-booksim-chiplet-run-{arch_hash[:12]}-") as tmp:
            (Path(tmp) / "chiplet.anynet").write_text(chiplet.anynet_file_content)
            config_path = Path(tmp) / "booksim.cfg"
            full_config = {**chiplet.config, "sim_type": "latency"}
            config_path.write_text(dump_booksim_config(full_config))

            sim_proc = subprocess.run(
                [str(binary_path), str(config_path)],
                capture_output=True, text=True, cwd=tmp, timeout=self.timeout_s,
            )
            latency_matches = list(_LATENCY_RE.finditer(sim_proc.stdout))
            if not latency_matches:
                raise RuntimeError(
                    "Could not find a 'Packet latency average = N' line in Booksim2 output "
                    "(chiplet anynet run).\n"
                    f"{tails(sim_proc)}"
                )
            hops_match = _HOPS_RE.search(sim_proc.stdout)
            return (
                float(latency_matches[-1].group(1)),
                float(hops_match.group(1)) if hops_match else None,
            )

    def _run_booksim(self, config: dict[str, Any], arch_hash: str) -> tuple[float, float | None]:
        binary_path = self._ensure_booksim_binary()
        with tempfile.TemporaryDirectory(prefix=f"flux-booksim-run-{arch_hash[:12]}-") as tmp:
            config_path = Path(tmp) / "booksim.cfg"
            full_config = {**config, "sim_type": "latency"}
            config_path.write_text(dump_booksim_config(full_config))

            sim_proc = subprocess.run(
                [str(binary_path), str(config_path)],
                capture_output=True, text=True, cwd=tmp, timeout=self.timeout_s,
            )
            # The *last* match, not the first (see _LATENCY_RE's comment) — Booksim2 reprints
            # "Packet latency average" once per sample period before the final, converged
            # "Overall Traffic Statistics" value.
            latency_matches = list(_LATENCY_RE.finditer(sim_proc.stdout))
            if not latency_matches:
                raise RuntimeError(
                    "Could not find a 'Packet latency average = N' line in Booksim2 output.\n"
                    f"{tails(sim_proc)}"
                )
            hops_match = _HOPS_RE.search(sim_proc.stdout)
            return (
                float(latency_matches[-1].group(1)),
                float(hops_match.group(1)) if hops_match else None,
            )
