"""Timeloop+Accelergy backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md).

v0.1 status, mirroring evaluators/zigzag: Workload IR translation is unconditional
(workload_translator.py). Architecture IR translation exists but is narrow
(architecture_translator.py's module docstring has the exact scope — single `meshX` spatial
dimension, uniform memories). Mapping IR translation exists (mapping_translator.py) but only
when `Candidate.arch` is also a translated Architecture IR document, and only for temporal loop
order — spatial mapping stays exactly as fixed by the architecture translator's own
`maximize_dims` constraint (mapping_translator.py's module docstring has the exact scope and why
that boundary is where it is).

**Multi-op (multi-layer) workloads, real support (docs/decisions.md D62)** — closing the gap D59/
D60 both found and named directly (`mlp-ffn0.yaml` rejected outright: "TimeloopEvaluator v0.1
evaluates exactly one op per call"). Unlike `evaluators/zigzag`'s own multi-layer support (a
single call into ZigZag's own Python API, which aggregates internally), Timeloop itself has no
native multi-layer problem shape — each real Docker invocation evaluates exactly one problem
instance. This adapter now runs one real, separate Timeloop invocation per `einsum` op (against
the identical fixed architecture+mapping-constraints, only the problem instance varies) and
aggregates the results itself (`_aggregate_stats`): cycles and energy accumulate across layers
(the real, sequential cost of running every layer on the same hardware); area is asserted
identical across every layer's own run, not silently averaged — a real, checked invariant (area
is a property of the fixed hardware, not the workload), not an assumption; utilization is combined
as a cycles-weighted average, the standard way to combine per-phase figures into one summary
number (a raw sum would give a meaningless value over 100% for more than one layer). **Explicit
Mapping IR is not supported for multi-op workloads** — Mapping IR is inherently per-op
(`for_op: <id>`), so which op an explicit mapping would apply to is genuinely ambiguous across
several; `Candidate.mapping` must be `None` for any multi-op workload, letting Timeloop's own
mapper search each layer independently, same as the existing single-op `mapping=None` path
already does.

**Real sparsity, via Timeloop's own real `sparse_optimizations`/`densities` mechanism (docs/
decisions.md D78)**: `op["sparsity"]` (Workload IR) and a memory hierarchy entry's
`attrs.sparse_optimizations` (Architecture IR) — see `workload_translator.py`'s and
`architecture_translator.py`'s own module docstrings for the exact shape and the real, hands-on
verification behind the `hypergeometric`-only, `gating`-only v0.1 scope. **Single-op workloads
only** — resolving a `target`/`condition_on`/`sparsity` Flux tensor name to a Timeloop dataspace
name needs one unambiguous op to resolve tensor roles against, the same tensor-role-resolution
constraint `mapping_translator.py`'s per-op scope already has; a multi-op workload declaring
either raises `NotExpressibleError` rather than silently applying the first op's own resolution
to every layer.

Runs Timeloop via Docker (`timeloopaccelergy/accelergy-timeloop-infrastructure`) rather than a
local install: PyTimeloop's islpy+Barvinok build is a genuine, documented adoption barrier
(docs/gap-analysis.md G11), and "most users end up in the provided Docker image, which then becomes the
de-facto interface" — so building this adapter around a local Timeloop build would be modelling
a workflow almost nobody actually uses. This is the same reasoning that already appears in
docs/roadmap.md's build-vs-reuse table for `rtl`/`hammer`-class evaluators generally.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
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
from .mapping_translator import (
    mapping_ir_to_timeloop_constraints,
    spatial_dim_for_timeloop_architecture,
)
from .workload_translator import (
    einsum_op_to_timeloop_instance,
    flux_tensor_to_timeloop_dataspace,
    op_sparsity_to_timeloop_densities,
)

_REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
_DEFAULT_IMAGE = "timeloopaccelergy/accelergy-timeloop-infrastructure"


def _driver_script(*, include_mapping_constraints: bool, prefix: str = "/work") -> str:
    """The script both runners execute. `prefix` is the container mount point for the Docker path
    and the working directory for the local one (docs/decisions.md D206)."""
    names = ["arch.yaml", "components.yaml", "variables.yaml", "mapper.yaml", "problem.yaml"]
    if include_mapping_constraints:
        names.append("mapping_constraints.yaml")
    files_literal = ", ".join(f'"{prefix}/{n}"' for n in names)
    return (
        "import timeloopfe.v4 as tl\n\n"
        f"spec = tl.Specification.from_yaml_files([{files_literal}])\n"
        f'tl.call_mapper(spec, output_dir="{prefix}/outputs", log_to="{prefix}/outputs/run.log")\n'
    )


_LOCAL_ENV_VAR = "FLUX_TIMELOOP_LOCAL"
# Spellings that mean "off". A bare `export FLUX_TIMELOOP_LOCAL` leaves it empty, and `=0`/`=false`
# read as off to anyone who writes them; treating any of the three as truthy opts callers in by
# accident (docs/decisions.md D206).
_OFF_SPELLINGS = ("", "0", "false")


def local_runner_requested() -> bool:
    """Whether the environment asks for the hermetic runner. One definition, because tests that
    gate on this must agree with the adapter that acts on it — two copies drift the moment either
    side gains a spelling."""
    return os.environ.get(_LOCAL_ENV_VAR, "") not in _OFF_SPELLINGS


def local_timeloop_available() -> bool:
    """Is a hermetic (non-Docker) Timeloop usable right now — binary on PATH and front-end
    importable? Checked, never assumed: `flux_backend_health`'s own posture (D156)."""
    if shutil.which("timeloop-mapper") is None:
        return False
    try:
        import timeloopfe.v4  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "not usable"
        return False
    return True


def _materialise_accelergy_env(work: Path) -> Path:
    """Copy Accelergy's estimation plug-ins into `work` and write a config pointing at the copy.

    Both halves are required, and for reasons that only show up under Nix (docs/decisions.md D205):
    Accelergy finds estimators with `os.walk(..., followlinks=False)`, and every directory in a Nix
    environment is a symlink, so an un-copied plug-in tree is invisible; and the CACTI wrapper
    writes its scratch directory *inside its own plug-in directory*, which is read-only in the
    store. Copying dereferences and makes them writable at once.

    Returns the HOME to run under — Accelergy caches this config with absolute paths, so reusing
    the caller's real HOME would silently pick up a previous environment's store paths.
    """
    import accelergy  # noqa: F401  (import proves it is installed before we look for its data)

    prefix = Path(sys.prefix)
    source = prefix / "share/accelergy/estimation_plug_ins"
    if not source.is_dir():
        raise RuntimeError(
            f"accelergy is importable but {source} does not exist — no estimation plug-ins to "
            "run with, so every component would fall back to the 0%-accuracy dummy estimator "
            "(docs/decisions.md D138 rejects those results anyway)."
        )
    plugins = work / "accelergy_plug_ins"
    plugins.mkdir()
    for entry in sorted(source.iterdir()):
        # Plug-ins are directories today, but a stray file here would make `copytree` raise
        # `NotADirectoryError` — an error about our copy loop, blamed on Accelergy's layout.
        if entry.is_dir():
            shutil.copytree(entry, plugins / entry.name, symlinks=False)
        else:
            shutil.copy2(entry, plugins / entry.name, follow_symlinks=True)
    for path in plugins.rglob("*"):
        path.chmod(path.stat().st_mode | 0o200)

    home = work / "home"
    (home / ".config/accelergy").mkdir(parents=True)
    (home / ".config/accelergy/accelergy_config.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "0.4",
                "estimator_plug_ins": [str(plugins)],
                # Empty, and verified so rather than assumed: this Accelergy installs no
                # primitive-component libraries anywhere (`share/accelergy/` holds only
                # `estimation_plug_ins`, and the package ships no YAML at all). The key stays
                # present because Accelergy rewrites a config that is missing keys, and a rewrite
                # is how absolute store paths from a previous environment come back (D205).
                "primitive_components": [],
                "compound_components": [],
                "math_functions": [],
                "python_plug_ins": [],
                "table_plug_ins": {"roots": []},
            },
            sort_keys=False,
        )
    )
    return home


# Accelergy names the estimation plug-in it used for every component in its ERT summary
# (`estimator: CactiSRAM`, `CactiDRAM`, `Library`, ...). A placeholder plug-in names itself too,
# which is what makes this checkable (docs/decisions.md D138).
_DUMMY_ESTIMATOR_MARKERS = ("dummy", "placeholder")
_ESTIMATOR_RE = re.compile(r"^\s*estimator:\s*(\S+)", re.MULTILINE)


def estimators_used(outputs_dir: Path) -> set[str]:
    """Every estimation plug-in Accelergy actually used, read from its own ERT summary."""
    found: set[str] = set()
    for name in ("timeloop-mapper.ERT_summary.yaml", "timeloop-mapper.ERT.yaml"):
        path = outputs_dir / name
        if path.is_file():
            found.update(_ESTIMATOR_RE.findall(path.read_text()))
    return found


def reject_placeholder_estimators(outputs_dir: Path) -> None:
    """Refuse energy produced by a placeholder plug-in (docs/decisions.md D133/D138).

    nixpkgs' Accelergy ships *only* `dummy_tables/`, so a hermetic build assembled from packaged
    parts would run, produce plausible output, and report fabricated energy while cycles stayed
    correct — and this repo records Timeloop energy as calibration *reference* values. Half-right
    output is the worst shape available: it passes a smoke test and poisons the residual pool.
    Cheap to check because Accelergy says which plug-in it used.
    """
    used = estimators_used(outputs_dir)
    fake = sorted(e for e in used if any(m in e.lower() for m in _DUMMY_ESTIMATOR_MARKERS))
    if fake:
        raise RuntimeError(
            f"Accelergy used placeholder estimation plug-in(s) {fake} — the energy numbers are "
            f"fabricated, not physical (estimators seen: {sorted(used)}). Refusing rather than "
            "recording them; a real plug-in set (the Docker image's) is required."
        )


_STATS_PATTERNS = {
    "cycles": re.compile(r"^Cycles:\s*(\d+)", re.MULTILINE),
    "energy_uj": re.compile(r"^Energy:\s*([\d.eE+-]+)\s*uJ", re.MULTILINE),
    "area_mm2": re.compile(r"^Area:\s*([\d.eE+-]+)\s*mm\^2", re.MULTILINE),
    "utilization_pct": re.compile(r"^Utilization:\s*([\d.eE+-]+)%", re.MULTILINE),
}


def _arch_declares_sparse_optimizations(arch: dict[str, Any]) -> bool:
    """Whether any memory hierarchy entry declares `attrs.sparse_optimizations` (docs/
    decisions.md D78) — checked before paying the cost/risk of resolving tensor names, so the
    common case (no sparsity anywhere) never calls `flux_tensor_to_timeloop_dataspace` at all.
    """
    return any(
        node.get("attrs", {}).get("sparse_optimizations")
        for node in arch.get("hierarchy", [])
        if node.get("class") == "memory"
    )


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

    def __init__(
        self,
        *,
        image: str = _DEFAULT_IMAGE,
        timeout_s: float = 300.0,
        use_local: bool | None = None,
    ) -> None:
        """`use_local=True` runs a hermetic Timeloop (binary on PATH, `timeloopfe` importable)
        instead of the Docker image — see docs/decisions.md D204/D205/D206 for what that took.

        The default is **opt-in, not automatic**: `None` reads `FLUX_TIMELOOP_LOCAL`. Silently
        switching runners based on what happens to be installed would change which tool produced a
        number without the caller asking, and `provenance.evaluator` is the only place that
        difference is visible.
        """
        self.image = image
        self.timeout_s = timeout_s
        if use_local is None:
            use_local = local_runner_requested()
        if use_local and not local_timeloop_available():
            raise RuntimeError(
                "use_local was requested but no hermetic Timeloop is usable: needs "
                "`timeloop-mapper` on PATH and an importable `timeloopfe`."
            )
        self.use_local = use_local

    @property
    def evaluator_id(self) -> str:
        """What actually ran, not what was configured — `provenance.evaluator` is the only record
        distinguishing a Docker result from a hermetic one, and `flux replay` resolves a backend
        from it by prefix (both still start `timeloop`)."""
        return "timeloop-nix@local" if self.use_local else f"timeloop-docker@{self.image}"

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
                "cannot evaluate data_dependent or compute_kernel ops (docs/decisions.md D1)."
            )
        if len(einsum_ops) > 1 and candidate.mapping is not None:
            raise NotExpressibleError(
                f"workload {candidate.workload.get('id')!r} has {len(einsum_ops)} einsum ops "
                "and an explicit Candidate.mapping — Mapping IR is inherently per-op "
                "(for_op: <id>), so which op an explicit mapping applies to is ambiguous across "
                "several (docs/decisions.md D62). Pass Candidate.mapping=None for a multi-op "
                "workload to let Timeloop's own mapper search each layer independently."
            )
        arch_declares_sparsity = isinstance(candidate.arch, dict) and _arch_declares_sparse_optimizations(
            candidate.arch
        )
        any_op_declares_sparsity = any(op.get("sparsity") for op in einsum_ops)
        if len(einsum_ops) > 1 and (any_op_declares_sparsity or arch_declares_sparsity):
            raise NotExpressibleError(
                f"workload {candidate.workload.get('id')!r} has {len(einsum_ops)} einsum ops and "
                "declares real sparsity (op.sparsity and/or arch attrs.sparse_optimizations) — "
                "resolving a Flux tensor name to a Timeloop dataspace name needs one unambiguous "
                "op (docs/decisions.md D78), the same per-op tensor-role-resolution scope "
                "explicit Mapping IR already has. Sparsity is not supported for multi-op "
                "workloads v0.1."
            )

        instance_overrides_per_op = [einsum_op_to_timeloop_instance(op) for op in einsum_ops]
        for overrides, op in zip(instance_overrides_per_op, einsum_ops):
            densities = op_sparsity_to_timeloop_densities(candidate.workload, op)
            if densities is not None:
                overrides["densities"] = densities
        workload_hash = flux_ir.content_hash(candidate.workload)

        if candidate.arch is None:
            if candidate.mapping is not None:
                raise NotExpressibleError(
                    "TimeloopEvaluator v0.1 only translates Mapping IR when Candidate.arch is "
                    "also an inline Architecture IR dict (mapping_translator.py needs it to "
                    "validate target level names); leave Candidate.mapping as None to use the "
                    "bound reference accelerator+mapper."
                )
            all_stats = [
                self._run_timeloop(overrides, workload_hash) for overrides in instance_overrides_per_op
            ]
            stats = self._aggregate_stats(all_stats)
            arch_desc = str(_REFERENCE_DIR)
            map_desc = "timeloop-auto-generated"
        elif isinstance(candidate.arch, dict):
            if candidate.mapping is not None and not isinstance(candidate.mapping, dict):
                raise NotExpressibleError(
                    "TimeloopEvaluator v0.1 requires an inline Mapping IR dict as "
                    "Candidate.mapping (no result-store hash resolution yet), or None to let "
                    "Timeloop's own mapper search unconstrained."
                )
            timeloop_spatial_dim = spatial_dim_for_timeloop_architecture(
                candidate.mapping, candidate.arch, einsum_ops[0]
            )
            # Only resolved when actually needed (docs/decisions.md D78): the common case (no
            # sparse_optimizations anywhere) never calls flux_tensor_to_timeloop_dataspace, so an
            # unrelated workload whose tensor names don't cleanly resolve to Inputs/Weights/
            # Outputs by rank still translates exactly as before.
            tensor_name_map = (
                flux_tensor_to_timeloop_dataspace(candidate.workload, einsum_ops[0])
                if arch_declares_sparsity
                else None
            )
            arch_yaml_text = architecture_ir_to_timeloop_architecture_yaml(
                candidate.arch, spatial_dim=timeloop_spatial_dim, tensor_name_map=tensor_name_map,
            )
            arch_hash = flux_ir.content_hash(candidate.arch)

            if candidate.mapping is None:
                mapping_constraints = None
                map_desc = "timeloop-auto-generated"
            else:
                mapping_constraints = mapping_ir_to_timeloop_constraints(
                    candidate.mapping, candidate.arch, einsum_ops[0]
                )
                map_desc = f"translated:{flux_ir.content_hash(candidate.mapping)}"

            all_stats = [
                self._run_timeloop(
                    overrides, workload_hash,
                    arch_yaml_text=arch_yaml_text, mapping_constraints=mapping_constraints,
                )
                for overrides in instance_overrides_per_op
            ]
            stats = self._aggregate_stats(all_stats)
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
        instance_overrides: dict[str, Any],
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

            (work / "outputs").mkdir()

            if self.use_local:
                (work / "driver.py").write_text(
                    _driver_script(
                        include_mapping_constraints=mapping_constraints is not None,
                        prefix=str(work),
                    )
                )
                home = _materialise_accelergy_env(work)
                env = dict(os.environ, HOME=str(home))
                proc = subprocess.run(
                    [sys.executable, str(work / "driver.py")],
                    capture_output=True, text=True, timeout=self.timeout_s, cwd=work, env=env,
                )
            else:
                (work / "driver.py").write_text(
                    _driver_script(include_mapping_constraints=mapping_constraints is not None)
                )
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
            reject_placeholder_estimators(work / "outputs")
            stats_path = work / "outputs" / "timeloop-mapper.stats.txt"
            if proc.returncode != 0 or not stats_path.exists():
                raise RuntimeError(
                    "Timeloop run failed "
                    f"(exit={proc.returncode}, stats file exists={stats_path.exists()}).\n"
                    f"--- stdout (tail) ---\n{proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{proc.stderr[-4000:]}"
                )
            return _parse_stats(stats_path.read_text())

    def _aggregate_stats(self, all_stats: list[dict[str, float]]) -> dict[str, float]:
        """Combine one or more real, per-layer Timeloop stats dicts into one whole-workload
        result (docs/decisions.md D62). A single-op workload (the common case) short-circuits to
        that one dict unchanged — this function only actually combines anything for a real
        multi-op workload.
        """
        if len(all_stats) == 1:
            return all_stats[0]

        areas = {s["area_mm2"] for s in all_stats}
        if len(areas) != 1:
            raise RuntimeError(
                f"Timeloop reported {len(areas)} different area_mm2 values across layers of the "
                f"same workload/architecture ({sorted(areas)}) — expected exactly one, since "
                "area is a property of the fixed hardware, not the workload being run through "
                "it; this is a real inconsistency to investigate, not something to silently "
                "average or pick one of arbitrarily."
            )
        total_cycles = sum(s["cycles"] for s in all_stats)
        return {
            "cycles": total_cycles,
            "energy_uj": sum(s["energy_uj"] for s in all_stats),
            "area_mm2": areas.pop(),
            # Cycles-weighted average — the standard way to combine per-phase utilization into
            # one summary figure; a raw sum would give a meaningless value over 100% once more
            # than one layer is involved.
            "utilization_pct": sum(s["cycles"] * s["utilization_pct"] for s in all_stats) / total_cycles,
        }

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
                evaluator=self.evaluator_id,
                inputs={"workload_hash": workload_hash, "accelerator": arch_desc, "mapping": map_desc},
            ),
            escalation=Escalation(recommended=False),
        )
