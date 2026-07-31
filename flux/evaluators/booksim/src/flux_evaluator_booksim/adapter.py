"""Booksim2 backend adapter implementing the Flux Evaluator ABI (docs/04.md §4.4,
docs/00-decisions.md D5/D6): real NoC simulation — 2D and 3D k-ary n-cube networks — via
Booksim2 (github.com/booksim/booksim2, BSD-3-Clause, the standard reference NoC simulator).

v0.1 status: `Candidate.arch` must be an inline Architecture IR dict with an `interconnect.noc`
block `architecture_translator.py` can translate (topology in {"mesh", "torus"}, uniform
`dimensions`) — `None` is not accepted (there is no fixed default NoC the way
`evaluators/rtl`/`evaluators/systemc` have a fixed default `mac_array`). `Candidate.workload` is
still required by the ABI and still hashed into `Result.provenance`, but its content doesn't
drive simulated traffic — see `architecture_translator.py`'s module docstring for why, honestly,
not silently. `Candidate.mapping` must be `None`: Booksim2's synthetic traffic generation has no
mapping concept.

No independent functional checker exists for this adapter (unlike `evaluators/rtl`'s real
self-check against a Python reference) — `Result.validity.ok` is a placeholder `True`, same
honest gap `evaluators/zigzag`/`evaluators/timeloop` already have, not a claim of a real check
that doesn't exist.

Booksim2 itself is neither vendored nor pip-installed: `_ensure_booksim_binary` clones and builds
it on first use per `BooksimEvaluator` instance (same "fetch an external resource once, cache it"
shape `evaluators/timeloop` already uses for its Docker image, and `evaluators/systemc` uses for
its own compiled binary). Needs `git`, `g++`, `make`, `flex`, and `bison` on `PATH` — `flex`/
`bison` are the one real gap found empirically (every other Booksim2 source file builds with
plain g++); see `flake.nix`'s `.#default` shell, which now provides both.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import flux_ir
from flux_evaluator_abi import (
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

from .architecture_translator import architecture_ir_to_booksim_config, dump_booksim_config
from .errors import NotExpressibleError

_BOOKSIM_REPO_URL = "https://github.com/booksim/booksim2.git"
_LATENCY_RE = re.compile(r"^Packet latency average\s*=\s*([\d.]+)", re.MULTILINE)
_HOPS_RE = re.compile(r"^Hops average\s*=\s*([\d.]+)", re.MULTILINE)


class BooksimEvaluator:
    """Runs a real Booksim2 simulation of a translated Architecture IR `interconnect.noc` block.
    The NoC-DSE counterpart to `evaluators/rtl`/`evaluators/systemc` for compute: same "real
    external tool, thin adapter, fail loudly outside its scope" shape, different domain.
    """

    name = "booksim"

    def __init__(self, *, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s
        self._binary_path: Path | None = None
        self._build_lock = threading.Lock()

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

        config = architecture_ir_to_booksim_config(candidate.arch)
        workload_hash = flux_ir.content_hash(candidate.workload)
        arch_hash = flux_ir.content_hash(candidate.arch)

        packet_latency, hops_average = self._run_booksim(config, arch_hash)

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
                    "topology": f"{config['topology']}-k{config['k']}-n{config['n']}",
                },
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        # v0.1: sequential, same note as every other adapter here — the ABI's batch *interface*
        # is satisfied; batch *performance* is a Phase 3 concern (docs/05.md).
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _ensure_booksim_binary(self) -> Path:
        with self._build_lock:
            if self._binary_path is not None and self._binary_path.exists():
                return self._binary_path
            work_dir = Path(tempfile.mkdtemp(prefix="flux-booksim-build-"))
            clone_proc = subprocess.run(
                ["git", "clone", "--depth", "1", _BOOKSIM_REPO_URL, str(work_dir / "booksim2")],
                capture_output=True, text=True, timeout=self.timeout_s,
            )
            if clone_proc.returncode != 0:
                raise RuntimeError(
                    f"git clone of Booksim2 failed (exit={clone_proc.returncode}).\n"
                    f"--- stderr (tail) ---\n{clone_proc.stderr[-4000:]}"
                )
            src_dir = work_dir / "booksim2" / "src"
            build_proc = subprocess.run(
                ["make", "-j2"], capture_output=True, text=True, cwd=src_dir, timeout=self.timeout_s,
            )
            if build_proc.returncode != 0:
                raise RuntimeError(
                    f"Booksim2 build failed (exit={build_proc.returncode}) — needs flex and "
                    "bison on PATH (nix develop .#default provides both); every other Booksim2 "
                    "source file builds with plain g++.\n"
                    f"--- stdout (tail) ---\n{build_proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{build_proc.stderr[-4000:]}"
                )
            binary_path = src_dir / "booksim"
            self._binary_path = binary_path
            return binary_path

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
            latency_match = _LATENCY_RE.search(sim_proc.stdout)
            if not latency_match:
                raise RuntimeError(
                    "Could not find a 'Packet latency average = N' line in Booksim2 output.\n"
                    f"--- stdout (tail) ---\n{sim_proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{sim_proc.stderr[-4000:]}"
                )
            hops_match = _HOPS_RE.search(sim_proc.stdout)
            return (
                float(latency_match.group(1)),
                float(hops_match.group(1)) if hops_match else None,
            )
