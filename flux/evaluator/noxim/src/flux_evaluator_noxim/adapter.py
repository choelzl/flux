"""Noxim backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md,
docs/decisions.md D32): real 2D-mesh NoC simulation via Noxim
(github.com/davidepatti/noxim, GPLv2, a SystemC-based cycle-accurate NoC simulator — genuinely
independent of Booksim2, a different codebase and simulation core), serving as this repo's first
real `reference_backend` for the `noc_topology` axis's conformance check (docs/roadmap.md's
"Immediate next actions" item 1, D22/D24's still-open gap: "no evaluator here can yet serve as
independent NoC ground truth").

v0.1 scope, narrower than `evaluators/booksim`'s by design, not by oversight: Noxim has no torus
network at all (checked against its C++ source, `architecture_translator.py`'s module docstring
has the full accounting) — this adapter only ever covers the 2D-mesh slice of the noc_topology
candidate space. A torus/3D/6D winning candidate raises `NotExpressibleError` here, the same
honest "doesn't apply to this candidate" outcome `flux_agentic_dse_loop` already handles for every
other axis/backend mismatch (docs/decisions.md D29's docstring), not a crash.

`Candidate.arch`/`Candidate.workload`/`Candidate.mapping` requirements mirror
`evaluators/booksim` exactly (inline Architecture IR dict with `interconnect.noc`, inline
Workload IR dict hashed but not traffic-driving, `Candidate.mapping` must be `None` — Noxim's
synthetic traffic generation has no mapping concept either).

No independent functional checker exists (same honest gap `evaluators/booksim`/`evaluators/
zigzag`/`evaluators/timeloop` already have) — `Result.validity.ok` is a placeholder `True`.

Noxim itself is neither vendored nor pip-installed: `_ensure_noxim_binary` clones and builds it
on first use per `NoximEvaluator` instance (same "fetch an external resource once, cache it"
shape every other real-tool adapter here uses). Its own `build.sh` self-provisions SystemC 2.3.1
(built from source via `./configure && make && make install`, ~a few minutes) and yaml-cpp (via
CMake) under its own `bin/libs/` — needs `git`, `g++`, `make`, `cmake`, and `curl` or `wget` on
`PATH` (`cmake` is the one real gap found empirically, the same way `flex`/`bison` were for
Booksim2 — see `flake.nix`'s `.#default` shell, which now provides it), plus outbound network
access to clone Noxim itself, clone `yaml-cpp` from GitHub, and download the SystemC 2.3.1 tarball
from accellera.org — a real, larger one-time cost than Booksim2's single self-contained clone.
SystemC builds a shared library (`libsystemc.so`), not just a static one, so every simulation
subprocess needs `LD_LIBRARY_PATH` pointing at it — confirmed empirically (the built binary
otherwise fails to even start), not assumed.

**Real, hard-won CLI details, found by actually running the binary, not read off `-help`
alone**: Noxim needs both `-config` *and* `-power` YAML files to exist, or a working directory
containing `power.yaml`/`noxim.yaml` — omit either and it prints a one-line warning and
`exit(0)` with *zero* simulation output, no error, easy to mistake for a real (empty) result;
this adapter always passes both explicitly, pointing at Noxim's own bundled
`config_examples/default_configMesh.yaml` and `bin/power.yaml` (every other simulation parameter
is overridden via CLI flags anyway, so the specific base file barely matters — confirmed by
Noxim's own config comment: "Each parameter is overwritten when corresponding command line value
is set"). Noxim also refuses `packet_size < 2` outright (`architecture_translator.py` handles
this at the IR-translation layer, not here).
"""

from __future__ import annotations

import os
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

from .architecture_translator import architecture_ir_to_noxim_args, noxim_cli_args
from .errors import NotExpressibleError

_NOXIM_REPO_URL = "https://github.com/davidepatti/noxim.git"
# Fixed simulation-duration parameters, not exposed via the Architecture IR (they're properties
# of how long to simulate for convergence, not of the architecture itself — the same scoping
# choice `evaluators/booksim` makes for `sim_type: "latency"`, just a different mechanism).
# 10000 cycles / 1000-cycle warmup was checked empirically against this repo's own
# noc-mesh-2d-v1.yaml-shaped candidates to give "Received/Ideal flits Ratio" close to 1.0 (a
# converged, non-saturated run) at realistic injection rates — not a default carried over
# unchecked from Noxim's own example configs.
_SIM_CYCLES = 10000
_WARMUP_CYCLES = 1000
# Noxim's own `-seed` defaults to time() (its own `-help` output, confirmed by a real observation
# here: two identical back-to-back runs gave different Global average delay values with no seed
# passed) — unlike Booksim2, which gives the exact same result run over run at fixed parameters
# with no seed flag at all. A fixed seed is required for this adapter's results to be
# reproducible/pinnable in tests the same way every other adapter's real numbers already are.
_RNG_SEED = 0
# C++ ostream prints a double in scientific notation outside a narrow range, so a
# `[\d.]+` capture silently truncates it: a real 1234567.0 prints as "1.23457e+06" and
# parsed as 1.23457, and a real 1e-07 parsed as 1 — wrong by 10**6 and 10**7, with no
# error anywhere (docs/decisions.md D181). `evaluators/timeloop` and `evaluators/dramsim3`
# already accepted exponents; these two did not.
_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_DELAY_LABEL_RE = re.compile(r"Global average delay \(cycles\):")
_DELAY_RE = re.compile(rf"Global average delay \(cycles\):\s*({_FLOAT})")
_THROUGHPUT_RE = re.compile(rf"Network throughput \(flits/cycle\):\s*({_FLOAT})")


class NoximEvaluator:
    """Runs a real Noxim simulation of a translated Architecture IR `interconnect.noc` block.
    A second, independent NoC evaluator alongside `evaluators/booksim` — same Evaluator ABI
    shape, different underlying tool, for conformance-checking purposes (docs/decisions.md D32).
    """

    name = "noxim"

    def __init__(self, *, timeout_s: float = 600.0) -> None:
        # Noxim's own SystemC-from-source build is much slower than Booksim2's plain-g++ build
        # (checked: several minutes, not seconds), hence a longer default timeout than
        # BooksimEvaluator's 120s.
        self.timeout_s = timeout_s
        self._repo_dir: Path | None = None
        self._binary_path: Path | None = None
        self._systemc_lib_dir: Path | None = None
        self._build_lock = threading.Lock()

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.arch, dict):
            raise NotExpressibleError(
                "NoximEvaluator v0.1 requires an inline Architecture IR dict as Candidate.arch "
                "with an interconnect.noc block (see architecture_translator.py) — there is no "
                "fixed default NoC to fall back to."
            )
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "NoximEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet) — its content isn't used to drive "
                "traffic (see evaluators/booksim's equivalent note — the same real "
                "representational gap applies here), but it's still required and hashed for "
                "provenance, same as every other evaluator here."
            )
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "NoximEvaluator v0.1 does not use Mapping IR: Noxim's synthetic traffic "
                "generation has no mapping concept — leave Candidate.mapping as None."
            )

        config = architecture_ir_to_noxim_args(candidate.arch)
        workload_hash = flux_ir.content_hash(candidate.workload)
        arch_hash = flux_ir.content_hash(candidate.arch)

        avg_delay, throughput = self._run_noxim(config, arch_hash)

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "latency_cycles" in metrics:
            result_metrics["latency_cycles"] = Estimate(
                value=avg_delay, ci_low=avg_delay, ci_high=avg_delay,
                unit="cycles", method=Method.SIMULATED,
            )

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none (placeholder, same as booksim/zigzag/timeloop)"),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(
                limiter=Limiter.NOC,
                per_level_utilisation=(
                    {"network_throughput_flits_per_cycle": throughput}
                    if throughput is not None else {}
                ),
            ),
            provenance=Provenance(
                evaluator="noxim@real",
                inputs={
                    "workload_hash": workload_hash,
                    "arch_hash": arch_hash,
                    "topology": f"mesh-{config['dimx']}x{config['dimy']}",
                },
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        # v0.1: sequential, same note as every other adapter here — the ABI's batch *interface*
        # is satisfied; batch *performance* is a Phase 3 concern (docs/roadmap.md).
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _ensure_noxim_binary(self) -> tuple[Path, Path, Path]:
        with self._build_lock:
            if (
                self._repo_dir is not None
                and self._binary_path is not None
                and self._systemc_lib_dir is not None
                and self._binary_path.exists()
            ):
                return self._repo_dir, self._binary_path, self._systemc_lib_dir
            work_dir = Path(tempfile.mkdtemp(prefix="flux-noxim-build-"))
            repo_dir = work_dir / "noxim"
            clone_proc = subprocess.run(
                ["git", "clone", "--depth", "1", _NOXIM_REPO_URL, str(repo_dir)],
                capture_output=True, text=True, timeout=self.timeout_s,
            )
            if clone_proc.returncode != 0:
                raise RuntimeError(
                    f"git clone of Noxim failed (exit={clone_proc.returncode}).\n"
                    f"--- stderr (tail) ---\n{clone_proc.stderr[-4000:]}"
                )
            build_proc = subprocess.run(
                ["./build.sh"], capture_output=True, text=True, cwd=repo_dir, timeout=self.timeout_s,
            )
            if build_proc.returncode != 0:
                raise RuntimeError(
                    f"Noxim build failed (exit={build_proc.returncode}) — needs git, g++, make, "
                    "cmake, and curl/wget on PATH (nix develop .#default provides cmake; the "
                    "rest are already there for evaluators/booksim). build.sh self-provisions "
                    "SystemC 2.3.1 and yaml-cpp from source under bin/libs/.\n"
                    f"--- stdout (tail) ---\n{build_proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{build_proc.stderr[-4000:]}"
                )
            binary_path = repo_dir / "bin" / "noxim"
            systemc_lib_dir = repo_dir / "bin" / "libs" / "systemc-2.3.1" / "lib-linux64"
            self._repo_dir = repo_dir
            self._binary_path = binary_path
            self._systemc_lib_dir = systemc_lib_dir
            return repo_dir, binary_path, systemc_lib_dir

    def _run_noxim(self, config: dict[str, Any], arch_hash: str) -> tuple[float, float | None]:
        repo_dir, binary_path, systemc_lib_dir = self._ensure_noxim_binary()
        base_config = repo_dir / "config_examples" / "default_configMesh.yaml"
        power_config = repo_dir / "bin" / "power.yaml"

        args = [
            str(binary_path),
            "-config", str(base_config),
            "-power", str(power_config),
            *noxim_cli_args(config),
            "-warmup", str(_WARMUP_CYCLES),
            "-sim", str(_SIM_CYCLES),
            "-seed", str(_RNG_SEED),
        ]
        # Merge, don't replace, os.environ: the binary itself only needs LD_LIBRARY_PATH added
        # (confirmed empirically — it fails to even start without libsystemc.so findable), but a
        # bare env={} would also drop PATH/HOME/locale vars subprocess.run's child doesn't
        # otherwise need to function correctly, an unrelated way to break the run.
        run_env = {**os.environ, "LD_LIBRARY_PATH": str(systemc_lib_dir)}
        with tempfile.TemporaryDirectory(prefix=f"flux-noxim-run-{arch_hash[:12]}-") as tmp:
            sim_proc = subprocess.run(
                args, capture_output=True, text=True, cwd=tmp, timeout=self.timeout_s,
                env=run_env,
            )
            delay_match = _DELAY_RE.search(sim_proc.stdout)
            if not delay_match:
                # Distinguish "the line is absent" from "the line is there and its value is not a
                # number" (docs/decisions.md D183). Real Noxim prints `-nan` for the average delay
                # when no flit is ever received, which happens at genuinely low injection rates —
                # observed from a real run at `-pir 0.000001`. Reporting that as "could not find
                # the line" sends the reader looking for a parsing or version problem when the
                # simulation simply carried no traffic.
                if _DELAY_LABEL_RE.search(sim_proc.stdout):
                    raise RuntimeError(
                        "Noxim reported a non-numeric 'Global average delay (cycles)' value "
                        "(it prints `-nan` when no flit is received at all). The simulation ran "
                        "but carried no traffic — raise the injection rate or lengthen the run.\n"
                        f"--- stdout (tail) ---\n{sim_proc.stdout[-4000:]}"
                    )
                raise RuntimeError(
                    "Could not find a 'Global average delay (cycles):' line in Noxim output.\n"
                    f"--- stdout (tail) ---\n{sim_proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{sim_proc.stderr[-4000:]}"
                )
            throughput_match = _THROUGHPUT_RE.search(sim_proc.stdout)
            return (
                float(delay_match.group(1)),
                float(throughput_match.group(1)) if throughput_match else None,
            )
