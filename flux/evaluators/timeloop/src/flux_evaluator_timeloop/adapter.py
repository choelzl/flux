"""Timeloop+Accelergy backend adapter implementing the Flux Evaluator ABI (docs/04.md §4.4).

v0.1 status, mirroring evaluators/zigzag: Workload IR translation is unconditional
(workload_translator.py). Architecture IR translation exists but is narrow
(architecture_translator.py's module docstring has the exact scope — single `meshX` spatial
dimension, uniform memories). Mapping IR translation exists (mapping_translator.py) but only
when `Candidate.arch` is also a translated Architecture IR document, and only for temporal loop
order — spatial mapping stays exactly as fixed by the architecture translator's own
`maximize_dims` constraint (mapping_translator.py's module docstring has the exact scope and why
that boundary is where it is).

Runs Timeloop via Docker (`timeloopaccelergy/accelergy-timeloop-infrastructure`) rather than a
local install: PyTimeloop's islpy+Barvinok build is a genuine, documented adoption barrier
(docs/03.md G11), and "most users end up in the provided Docker image, which then becomes the
de-facto interface" — so building this adapter around a local Timeloop build would be modelling
a workflow almost nobody actually uses. This is the same reasoning that already appears in
docs/05.md's build-vs-reuse table for `rtl`/`hammer`-class evaluators generally.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import flux_ir
import yaml
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

from .architecture_translator import architecture_ir_to_timeloop_architecture_yaml
from .errors import NotExpressibleError
from .mapping_translator import mapping_ir_to_timeloop_constraints
from .workload_translator import einsum_op_to_timeloop_instance

_REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
_DEFAULT_IMAGE = "timeloopaccelergy/accelergy-timeloop-infrastructure"


def _driver_script(*, include_mapping_constraints: bool) -> str:
    files = [
        "/work/arch.yaml", "/work/components.yaml", "/work/variables.yaml",
        "/work/mapper.yaml", "/work/problem.yaml",
    ]
    if include_mapping_constraints:
        files.append("/work/mapping_constraints.yaml")
    files_literal = ", ".join(f'"{f}"' for f in files)
    return (
        "import timeloopfe.v4 as tl\n\n"
        f"spec = tl.Specification.from_yaml_files([{files_literal}])\n"
        'tl.call_mapper(spec, output_dir="/work/outputs", log_to="/work/outputs/run.log")\n'
    )

_STATS_PATTERNS = {
    "cycles": re.compile(r"^Cycles:\s*(\d+)", re.MULTILINE),
    "energy_uj": re.compile(r"^Energy:\s*([\d.eE+-]+)\s*uJ", re.MULTILINE),
    "area_mm2": re.compile(r"^Area:\s*([\d.eE+-]+)\s*mm\^2", re.MULTILINE),
    "utilization_pct": re.compile(r"^Utilization:\s*([\d.eE+-]+)%", re.MULTILINE),
}


def _parse_stats(text: str) -> dict[str, float]:
    """Parse Timeloop's `Summary Stats` block (a stable, documented plain-text format — see
    reference/README or any timeloop-mapper.stats.txt) rather than going through timeloopfe's
    Python result-object API, which is more prone to churn across timeloopfe versions.
    """
    values: dict[str, float] = {}
    for key, pattern in _STATS_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            raise RuntimeError(f"could not find {key!r} in Timeloop stats output")
        values[key] = float(match.group(1))
    return values


class TimeloopEvaluator:
    """Binds a fixed Timeloop architecture+mapper to the Evaluator ABI; translates
    `Candidate.workload` from Flux Workload IR on every call.
    """

    name = "timeloop"

    def __init__(self, *, image: str = _DEFAULT_IMAGE, timeout_s: float = 300.0) -> None:
        self.image = image
        self.timeout_s = timeout_s

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "TimeloopEvaluator v0.1 requires an inline Workload IR dict as "
                "Candidate.workload (no result-store hash resolution yet)."
            )

        ops = candidate.workload.get("ops", [])
        einsum_ops = [op for op in ops if op.get("kind") == "einsum"]
        if not einsum_ops:
            raise NotExpressibleError(
                f"workload {candidate.workload.get('id')!r} has no 'einsum' ops; Timeloop "
                "cannot evaluate data_dependent or compute_kernel ops (docs/00-decisions.md D1)."
            )
        if len(einsum_ops) > 1:
            raise NotExpressibleError(
                f"workload {candidate.workload.get('id')!r} has {len(einsum_ops)} einsum ops; "
                "TimeloopEvaluator v0.1 evaluates exactly one op per call (no multi-layer "
                "workloads yet — see evaluators/zigzag for the analogous limitation)."
            )
        instance_overrides = einsum_op_to_timeloop_instance(einsum_ops[0])
        workload_hash = flux_ir.content_hash(candidate.workload)

        if candidate.arch is None:
            if candidate.mapping is not None:
                raise NotExpressibleError(
                    "TimeloopEvaluator v0.1 only translates Mapping IR when Candidate.arch is "
                    "also an inline Architecture IR dict (mapping_translator.py needs it to "
                    "validate target level names); leave Candidate.mapping as None to use the "
                    "bound reference accelerator+mapper."
                )
            stats = self._run_timeloop(instance_overrides, workload_hash)
            arch_desc = str(_REFERENCE_DIR)
            map_desc = "timeloop-auto-generated"
        elif isinstance(candidate.arch, dict):
            arch_yaml_text = architecture_ir_to_timeloop_architecture_yaml(candidate.arch)
            arch_hash = flux_ir.content_hash(candidate.arch)

            if candidate.mapping is None:
                mapping_constraints = None
                map_desc = "timeloop-auto-generated"
            elif isinstance(candidate.mapping, dict):
                mapping_constraints = mapping_ir_to_timeloop_constraints(
                    candidate.mapping, candidate.arch, einsum_ops[0]
                )
                map_desc = f"translated:{flux_ir.content_hash(candidate.mapping)}"
            else:
                raise NotExpressibleError(
                    "TimeloopEvaluator v0.1 requires an inline Mapping IR dict as "
                    "Candidate.mapping (no result-store hash resolution yet), or None to let "
                    "Timeloop's own mapper search unconstrained."
                )

            stats = self._run_timeloop(
                instance_overrides, workload_hash,
                arch_yaml_text=arch_yaml_text, mapping_constraints=mapping_constraints,
            )
            arch_desc = f"translated:{arch_hash}"
        else:
            raise NotExpressibleError(
                "TimeloopEvaluator v0.1 only accepts Candidate.arch as None or an inline "
                "Architecture IR dict (translated via architecture_translator.py)."
            )

        return self._to_result(stats, workload_hash, arch_desc, map_desc)

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _run_timeloop(
        self,
        instance_overrides: dict[str, int],
        workload_hash: str,
        *,
        arch_yaml_text: str | None = None,
        mapping_constraints: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        with tempfile.TemporaryDirectory(prefix=f"flux-timeloop-{workload_hash[:12]}-") as tmp:
            work = Path(tmp)
            reference_files = ["components.yaml", "variables.yaml", "mapper.yaml"]
            if arch_yaml_text is None:
                reference_files.append("arch.yaml")
            else:
                (work / "arch.yaml").write_text(arch_yaml_text)
            for name in reference_files:
                shutil.copy(_REFERENCE_DIR / name, work / name)

            problem = yaml.safe_load((_REFERENCE_DIR / "problem_base.yaml").read_text())
            problem["problem"]["instance"].update(instance_overrides)
            (work / "problem.yaml").write_text(yaml.safe_dump(problem, sort_keys=False))

            if mapping_constraints is not None:
                (work / "mapping_constraints.yaml").write_text(
                    yaml.safe_dump({"mapspace_constraints": mapping_constraints}, sort_keys=False)
                )

            (work / "driver.py").write_text(
                _driver_script(include_mapping_constraints=mapping_constraints is not None)
            )
            (work / "outputs").mkdir()

            proc = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{work}:/work",
                    "-e",
                    "PYTHONPATH=/usr/local/src/timeloopfe",
                    "--entrypoint",
                    "python3",
                    self.image,
                    "/work/driver.py",
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
            stats_path = work / "outputs" / "timeloop-mapper.stats.txt"
            if proc.returncode != 0 or not stats_path.exists():
                raise RuntimeError(
                    "Timeloop run failed "
                    f"(exit={proc.returncode}, stats file exists={stats_path.exists()}).\n"
                    f"--- stdout (tail) ---\n{proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{proc.stderr[-4000:]}"
                )
            return _parse_stats(stats_path.read_text())

    def _to_result(
        self, stats: dict[str, float], workload_hash: str, arch_desc: str, map_desc: str
    ) -> Result:
        energy_pj = stats["energy_uj"] * 1e6  # Timeloop reports uJ; Result convention is pJ
        cycles = stats["cycles"]
        area_mm2 = stats["area_mm2"]
        utilization = stats["utilization_pct"] / 100.0

        result_metrics = {
            "energy_pj": Estimate(
                value=energy_pj, ci_low=energy_pj, ci_high=energy_pj, unit="pJ", method=Method.ANALYTIC
            ),
            "latency_cycles": Estimate(
                value=cycles, ci_low=cycles, ci_high=cycles, unit="cycles", method=Method.ANALYTIC
            ),
            "area_mm2": Estimate(
                value=area_mm2, ci_low=area_mm2, ci_high=area_mm2, unit="mm^2", method=Method.ANALYTIC
            ),
        }
        # Point estimates only, same v0.1 caveat as evaluators/zigzag: Timeloop reports single
        # analytic numbers and there's no calibration store yet (Phase 2) to derive real CIs.

        limiter = Limiter.COMPUTE if utilization > 0.5 else Limiter.MEMORY

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none-v0.1"),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(
                limiter=limiter, per_level_utilisation={"pe_array": utilization}
            ),
            provenance=Provenance(
                evaluator=f"timeloop-docker@{self.image}",
                inputs={"workload_hash": workload_hash, "accelerator": arch_desc, "mapping": map_desc},
            ),
            escalation=Escalation(recommended=False),
        )
