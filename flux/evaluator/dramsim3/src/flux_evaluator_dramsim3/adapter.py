"""DRAMsim3 backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md,
docs/decisions.md D74): real DRAM bank/refresh-aware timing, energy, and power via DRAMsim3
(University of Maryland Memory-Systems Research, MIT — real, published research: Li et al.,
"DRAMsim3: a Cycle-accurate, Thermal-Capable DRAM Simulator," IEEE CAL). Closes the one real piece
docs/gap-analysis.md G6 still names as open once evaluators/booksim (NoC) and evaluators/thermal
(chip thermal + chiplet stacking) closed the rest: DRAM bank/refresh detail.

**v0.1 scope, deliberately narrow, named explicitly**: single DRAM channel only (DRAMsim3 itself
supports multiple channels; this adapter raises `NotExpressibleError` rather than silently
averaging or picking one if a config's own real output ever reports more than one — none of
DRAMsim3's single-channel bundled configs do, confirmed directly, not assumed). Synthetic traffic
only (`architecture_translator.py`'s own module docstring explains why, the same honest gap
`evaluators/booksim` already has for NoC traffic). `Candidate.mapping` must be `None` — DRAMsim3
has no mapping concept.

**A real, honest "same metric name, different underlying quantity" case, the same trap D37
(CACTI) and D38 (gem5) already found and named explicitly rather than silently conflated**:
`latency_cycles` here is DRAMsim3's own real, physical DRAM clock cycles at the configured DDR
speed grade (e.g. tCK=0.63ns for a DDR4-3200 config) — a real, meaningful quantity, but not the
same abstract "cycle" ZigZag/Timeloop report for a whole accelerator's own execution. `energy_pj`/
`power_w` are the DRAM subsystem's own real access energy/power, not a whole-workload number.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path

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

from .architecture_translator import architecture_ir_to_dramsim3_params
from .build import ensure_dramsim3_binary
from .errors import NotExpressibleError

_CHANNEL_HEADER_RE = re.compile(r"^##\s*Statistics of Channel\s+(\d+)", re.MULTILINE)
_STAT_LINE_RE = re.compile(r"^(\S+)\s*=\s*([-\d.eE+]+)\s*#", re.MULTILINE)


def _parse_dramsim3_output(text: str) -> dict[str, float]:
    channels = sorted({int(m.group(1)) for m in _CHANNEL_HEADER_RE.finditer(text)})
    if len(channels) != 1:
        raise NotExpressibleError(
            f"DRAMsim3 output reports {len(channels)} channels ({channels!r}) — "
            "evaluators/dramsim3 v0.1 only supports exactly one."
        )
    return {m.group(1): float(m.group(2)) for m in _STAT_LINE_RE.finditer(text)}


class DramSim3Evaluator:
    """Runs real DRAMsim3 against a real, bundled, published timing config named by Architecture
    IR (see `architecture_translator.py`)."""

    name = "dramsim3"

    def __init__(self, *, timeout_s: float = 300.0) -> None:
        self.timeout_s = timeout_s
        self._binary_path: Path | None = None
        self._configs_dir: Path | None = None
        self._build_lock = threading.Lock()

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.arch, dict):
            raise NotExpressibleError(
                "DramSim3Evaluator v0.1 requires an inline Architecture IR dict as "
                "Candidate.arch with a memory hierarchy entry declaring attrs.dramsim3_config "
                "(see architecture_translator.py) — no result-store hash resolution yet."
            )
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "DramSim3Evaluator v0.1 requires an inline Workload IR dict as "
                "Candidate.workload (no result-store hash resolution yet) — its content isn't "
                "used to drive DRAM traffic (see module docstring), but it's still required and "
                "hashed for provenance, same as every other evaluator here."
            )
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "DramSim3Evaluator v0.1 does not use Mapping IR: DRAMsim3's synthetic traffic "
                "generation has no mapping concept — leave Candidate.mapping as None."
            )

        params = architecture_ir_to_dramsim3_params(candidate.arch)
        workload_hash = flux_ir.content_hash(candidate.workload)
        arch_hash = flux_ir.content_hash(candidate.arch)

        binary_path, configs_dir = self._ensure_binary()
        config_path = configs_dir / f"{params.config_name}.ini"
        if not config_path.exists():
            raise NotExpressibleError(
                f"DRAMsim3 has no bundled config named {params.config_name!r} at {config_path} "
                "— see DRAMsim3's own configs/ directory for real, valid names "
                "(e.g. 'DDR4_8Gb_x8_3200', 'LPDDR4_8Gb_x16_4266')."
            )

        run_dir = Path(tempfile.mkdtemp(prefix="flux-dramsim3-run-"))
        proc = subprocess.run(
            [
                str(binary_path), str(config_path),
                "--stream", params.stream, "-c", str(params.cycles),
            ],
            cwd=run_dir, capture_output=True, text=True, timeout=self.timeout_s,
        )
        output_path = run_dir / "dramsim3.txt"
        if proc.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"DRAMsim3 failed (exit={proc.returncode}) for config {params.config_name!r}.\n"
                f"--- stdout (tail) ---\n{proc.stdout[-4000:]}\n--- stderr (tail) ---\n{proc.stderr[-4000:]}"
            )
        stats = _parse_dramsim3_output(output_path.read_text())

        latency_cycles = stats["average_read_latency"]
        energy_pj = stats["total_energy"]
        power_w = stats["average_power"] / 1000.0  # DRAMsim3 reports mW

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "latency_cycles" in metrics:
            result_metrics["latency_cycles"] = Estimate(
                value=latency_cycles, ci_low=latency_cycles, ci_high=latency_cycles,
                unit="cycles", method=Method.SIMULATED,
            )
        if not metrics or "energy_pj" in metrics:
            result_metrics["energy_pj"] = Estimate(
                value=energy_pj, ci_low=energy_pj, ci_high=energy_pj, unit="pJ", method=Method.SIMULATED,
            )
        if not metrics or "power_w" in metrics:
            result_metrics["power_w"] = Estimate(
                value=power_w, ci_low=power_w, ci_high=power_w, unit="W", method=Method.SIMULATED,
            )

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none (placeholder, same as every other adapter here)"),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(
                limiter=Limiter.MEMORY,
                per_level_utilisation={
                    "average_bandwidth_gbps": stats.get("average_bandwidth", 0.0),
                    "num_act_cmds": stats.get("num_act_cmds", 0.0),
                    "num_ref_cmds": stats.get("num_ref_cmds", 0.0),
                },
            ),
            provenance=Provenance(
                evaluator="dramsim3@real",
                inputs={
                    "workload_hash": workload_hash,
                    "arch_hash": arch_hash,
                    "dramsim3_config": params.config_name,
                    "cycles": params.cycles,
                    "stream": params.stream,
                },
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        # v0.1: sequential, same note as every other adapter here.
        return [self.evaluate(c, budget, metrics) for c in candidates]

    @staticmethod
    def _provided_dramsim3() -> tuple[Path, Path] | None:
        """A prebuilt DRAMsim3 plus its `configs/`, if the environment supplies both
        (docs/decisions.md D148).

        Both halves are required. The binary alone is useless here — this adapter selects a real
        `.ini` by name — and accepting a binary without configs is exactly how the CACTI package
        shipped something that ran and then crashed on missing runtime data (D145). Checked
        together, so a partial installation falls through to the clone-and-build below rather than
        half-working.
        """
        import os
        import shutil as _shutil

        env_bin = os.environ.get("DRAMSIM3_BIN")
        binary = None
        if env_bin:
            candidate = Path(env_bin) / "dramsim3" if Path(env_bin).is_dir() else Path(env_bin)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                binary = candidate
        if binary is None:
            on_path = _shutil.which("dramsim3")
            binary = Path(on_path) if on_path else None
        if binary is None:
            return None

        env_configs = os.environ.get("DRAMSIM3_CONFIGS")
        roots = [Path(env_configs)] if env_configs else []
        # `<prefix>/bin/dramsim3` -> `<prefix>/share/dramsim3/configs`, the layout nixchip installs.
        roots.append(binary.parent.parent / "share" / "dramsim3" / "configs")
        for root in roots:
            if root.is_dir() and any(root.glob("*.ini")):
                return binary, root
        return None

    def _ensure_binary(self) -> tuple[Path, Path]:
        with self._build_lock:
            if self._binary_path is not None and self._binary_path.exists():
                return self._binary_path, self._configs_dir
            provided = self._provided_dramsim3()
            if provided is not None:
                self._binary_path, self._configs_dir = provided
                return provided
            work_dir = Path(tempfile.mkdtemp(prefix="flux-dramsim3-build-"))
            binary_path, configs_dir = ensure_dramsim3_binary(work_dir, timeout_s=self.timeout_s)
            self._binary_path = binary_path
            self._configs_dir = configs_dir
            return binary_path, configs_dir
