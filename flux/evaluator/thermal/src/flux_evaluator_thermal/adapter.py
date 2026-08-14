"""Thermal backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md,
docs/decisions.md D64/D65): real steady-state thermal simulation via 3D-ICE (EPFL ESL, GPLv3 —
`esl-epfl/3d-ice` — see `build.py`'s module docstring for the real, multi-step build-environment
story). Closes the "thermal" slice of docs/gap-analysis.md G6 ("System-level effects absent (NoC,
chiplets, DRAM detail, thermal)") — NoC was already real via `evaluators/booksim`/`evaluators/
noxim`; thermal had no real model at all before D64.

**D65: real multi-die (chiplet) thermal stacking.** A `floorplan` block's `die` index (default 0,
backward compatible with D64's single-die scope) groups hierarchy entries onto real, separate,
physically stacked silicon layers — a higher `die` index sits physically closer to the heat sink.
This is **thermal stacking only**: real conductive heat coupling between dies, verified to be
genuinely non-trivial (a lower-power die stacked farther from the heat sink can run *hotter* than
a higher-power die closer to it, because it both sits farther from the heat sink and absorbs
conducted heat from the die above — confirmed with a real, hand-built two-die stack before this
module supported more than one). It is **not** a chiplet inter-die (D2D) *interconnect* model —
data movement between dies is a genuinely separate, NoC-style concern (`evaluators/booksim`'s own
territory), not addressed here. Still no transient simulation, no microchannel liquid cooling
(3D-ICE supports both — real, checked capabilities this adapter doesn't reach yet, not limitations
of the tool). `Candidate.workload` is required by the ABI and hashed into `Result.provenance`, but
(like `evaluators/booksim`'s NoC traffic and `evaluators/cacti`'s SRAM characterization) its
content drives nothing — 3D-ICE has no workload concept, only a floorplan + declared power.
`Candidate.mapping` must be `None` — no mapping concept either.

Reports two real metrics: `peak_temp_c` (the hottest modeled block's own steady-state average
temperature, across every die) and `avg_temp_c` (every modeled block's average, weighted by its
own physical area — not a naive per-block mean, which would let a tiny, hot block and a huge, cool
block count equally). Both in real degrees Celsius (3D-ICE itself reports Kelvin; converted here,
not left for a caller to get wrong).
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

from .architecture_translator import architecture_ir_to_3dice_stack
from .build import ensure_3dice_binary
from .errors import NotExpressibleError

_KELVIN_TO_CELSIUS = 273.15
_HEADER_RE = re.compile(r"^%\s*Time\(s\)\s*(.*)$")


def _parse_tflp_output(text: str, block_names: tuple[str, ...]) -> dict[str, float]:
    """Parses a real 3D-ICE `Tflp(..., average, final)` output file: one comment header naming
    each block (`name(K)`, tab-separated, in floorplan declaration order) and exactly one real
    data row (`final` restricts output to the converged steady-state result only). Returns
    {block_name: temperature_k}. Raises `RuntimeError` — not a silent partial result — if the
    header's own block names don't match what was actually requested.
    """
    header_names: list[str] | None = None
    data_row: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        header_match = _HEADER_RE.match(stripped)
        if header_match:
            header_names = [tok.strip().removesuffix("(K)") for tok in header_match.group(1).split("\t") if tok.strip()]
            continue
        if stripped.startswith("%"):
            continue
        data_row = [tok.strip() for tok in stripped.split("\t") if tok.strip()]
    if header_names is None or data_row is None:
        raise RuntimeError(f"could not parse a 3D-ICE Tflp output — no header/data row found:\n{text}")
    if len(data_row) - 1 != len(header_names):
        raise RuntimeError(
            f"3D-ICE Tflp output has {len(header_names)} named blocks but {len(data_row) - 1} data "
            f"values — expected them to match:\n{text}"
        )
    if set(header_names) != set(block_names):
        raise RuntimeError(
            f"3D-ICE Tflp output names {sorted(header_names)} don't match the requested blocks "
            f"{sorted(block_names)}"
        )
    return {name: float(value) for name, value in zip(header_names, data_row[1:])}


class ThermalEvaluator:
    """Runs real 3D-ICE against a translated, real (possibly multi-die) floorplan (see
    `architecture_translator.architecture_ir_to_3dice_stack`)."""

    name = "thermal"

    def __init__(self, *, timeout_s: float = 300.0) -> None:
        self.timeout_s = timeout_s
        self._binary_path: Path | None = None
        self._build_dir: Path | None = None
        self._build_lock = threading.Lock()

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.arch, dict):
            raise NotExpressibleError(
                "ThermalEvaluator v0.1 requires an inline Architecture IR dict as Candidate.arch "
                "with at least one hierarchy entry declaring both `floorplan` and `attrs.power_w` "
                "(see architecture_translator.py) — no result-store hash resolution yet."
            )
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "ThermalEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet) — 3D-ICE has no workload concept at all "
                "(see module docstring), but it's still required and hashed for provenance, same "
                "as every other evaluator here."
            )
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "ThermalEvaluator v0.1 does not use Mapping IR: 3D-ICE characterizes a physical "
                "floorplan, which has no mapping concept — leave Candidate.mapping as None."
            )

        stack = architecture_ir_to_3dice_stack(candidate.arch)
        workload_hash = flux_ir.content_hash(candidate.workload)
        arch_hash = flux_ir.content_hash(candidate.arch)

        binary_path = self._ensure_binary()
        run_dir = Path(tempfile.mkdtemp(prefix="flux-thermal-run-"))
        for die in stack.dies:
            (run_dir / f"die{die.index}.flp").write_text(die.flp_content)
        (run_dir / "chip.stk").write_text(stack.stk_content)

        proc = subprocess.run(
            [str(binary_path), "chip.stk"],
            cwd=run_dir, capture_output=True, text=True, timeout=self.timeout_s,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"3D-ICE-Emulator failed (exit={proc.returncode}) for stack {stack.stk_content!r}.\n"
                f"--- stdout (tail) ---\n{proc.stdout[-4000:]}\n--- stderr (tail) ---\n{proc.stderr[-4000:]}"
            )

        # One real, separate Tflp output per die (docs/decisions.md D65) — parsed and merged into
        # a single {block_name: temperature_k} map spanning the whole stack; block names are
        # already unique across the whole design (architecture_translator's own dedup logic).
        temps_k: dict[str, float] = {}
        for die in stack.dies:
            output_path = run_dir / die.output_file_name
            if not output_path.exists():
                raise RuntimeError(
                    f"3D-ICE-Emulator exited 0 but die {die.stack_name!r}'s own output "
                    f"{die.output_file_name!r} is missing.\n--- stdout (tail) ---\n{proc.stdout[-4000:]}"
                )
            die_block_names = tuple(b.name for b in die.blocks)
            temps_k.update(_parse_tflp_output(output_path.read_text(), die_block_names))

        total_area = sum(b.width_um * b.height_um for b in stack.blocks)
        avg_temp_k = sum(temps_k[b.name] * b.width_um * b.height_um for b in stack.blocks) / total_area
        peak_block = max(stack.blocks, key=lambda b: temps_k[b.name])
        peak_temp_k = temps_k[peak_block.name]

        avg_temp_c = avg_temp_k - _KELVIN_TO_CELSIUS
        peak_temp_c = peak_temp_k - _KELVIN_TO_CELSIUS

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "avg_temp_c" in metrics:
            result_metrics["avg_temp_c"] = Estimate(
                value=avg_temp_c, ci_low=avg_temp_c, ci_high=avg_temp_c, unit="C", method=Method.SIMULATED,
            )
        if not metrics or "peak_temp_c" in metrics:
            result_metrics["peak_temp_c"] = Estimate(
                value=peak_temp_c, ci_low=peak_temp_c, ci_high=peak_temp_c, unit="C", method=Method.SIMULATED,
            )

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none (placeholder, same as every other adapter here)"),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(
                limiter=Limiter.THERMAL,
                per_level_utilisation={f"{name}_temp_c": temp_k - _KELVIN_TO_CELSIUS for name, temp_k in temps_k.items()},
                top_costs=(peak_block.name,),
            ),
            provenance=Provenance(
                evaluator="3d-ice@real",
                inputs={
                    "workload_hash": workload_hash,
                    "arch_hash": arch_hash,
                    "blocks": [b.name for b in stack.blocks],
                    "dies": [d.index for d in stack.dies],
                    "chip_length_um": stack.chip_length_um,
                    "chip_width_um": stack.chip_width_um,
                },
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        # v0.1: sequential, same note as every other adapter here.
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _ensure_binary(self) -> Path:
        with self._build_lock:
            if self._binary_path is not None and self._binary_path.exists():
                return self._binary_path
            build_dir = Path(tempfile.mkdtemp(prefix="flux-thermal-build-"))
            self._binary_path = ensure_3dice_binary(build_dir, timeout_s=self.timeout_s)
            self._build_dir = build_dir
            return self._binary_path
