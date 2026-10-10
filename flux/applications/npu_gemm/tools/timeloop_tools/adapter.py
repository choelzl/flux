"""Timeloop+Accelergy backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md).

Workload IR translation is unconditional. Architecture IR translation is narrow (single `meshX`
spatial dimension, uniform memories; see architecture_translator.py). Timeloop's own mapper
searches the mapping: `Candidate.mapping` must be `None` (D957).

Multi-op workloads (D62): Timeloop has no multi-layer problem shape, so each `einsum` op is
its own invocation against the same architecture, aggregated by `_aggregate_stats`: cycles and
energy add up, area is asserted identical across layers, utilization is cycles-weighted.

Sparsity (D78) uses Timeloop's `sparse_optimizations`/`densities`, from `op["sparsity"]` and a
memory level's `attrs.sparse_optimizations`. Single-op workloads only: tensor names resolve to
dataspaces against one op, so a multi-op workload declaring sparsity raises
`NotExpressibleError`.

Runs Timeloop via Docker (`timeloopaccelergy/accelergy-timeloop-infrastructure`) by default,
since a local build is a real adoption barrier; `use_local` selects a hermetic install.
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
from flux_evaluator_abi.tools import tails
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

from .architecture_translator import architecture_ir_to_timeloop_architecture_yaml
from .errors import NotExpressibleError
from .workload_translator import (
    einsum_op_to_timeloop_instance,
    flux_tensor_to_timeloop_dataspace,
    op_sparsity_to_timeloop_densities,
)

_REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
_DEFAULT_IMAGE = "timeloopaccelergy/accelergy-timeloop-infrastructure"


def _driver_script(*, prefix: str = "/work") -> str:
    """The script both runners execute. `prefix` is the container mount point for the Docker path
    and the working directory for the local one."""
    names = ["arch.yaml", "components.yaml", "variables.yaml", "mapper.yaml", "problem.yaml"]
    files_literal = ", ".join(f'"{prefix}/{n}"' for n in names)
    return (
        "import timeloopfe.v4 as tl\n\n"
        f"spec = tl.Specification.from_yaml_files([{files_literal}])\n"
        f'tl.call_mapper(spec, output_dir="{prefix}/outputs", log_to="{prefix}/outputs/run.log")\n'
    )


_LOCAL_ENV_VAR = "FLUX_TIMELOOP_LOCAL"
# Spellings that mean "off": empty (a bare `export`), `0` and `false`.
_OFF_SPELLINGS = ("", "0", "false")


def local_runner_requested() -> bool:
    """Whether the environment asks for the hermetic runner. Tests gate on this same
    definition so they agree with the adapter."""
    return os.environ.get(_LOCAL_ENV_VAR, "") not in _OFF_SPELLINGS


def local_timeloop_available() -> bool:
    """Whether a hermetic (non-Docker) Timeloop is usable: binary on PATH and front-end
    importable."""
    if shutil.which("timeloop-mapper") is None:
        return False
    try:
        import timeloopfe.v4  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "not usable"
        return False
    return True


def _materialise_accelergy_env(work: Path) -> Path:
    """Copy Accelergy's estimation plug-ins into `work` and write a config pointing at the copy.

    Needed under Nix (D205): Accelergy walks with `followlinks=False`, so symlinked plug-ins
    are invisible, and CACTI writes scratch files inside its read-only plug-in directory.
    Copying dereferences and makes them writable.

    Returns the HOME to run under: Accelergy caches this config with absolute paths, so the
    caller's HOME could carry a previous environment's store paths.
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
        # skip stray files: `copytree` would raise `NotADirectoryError` on them
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
                # Empty: this Accelergy installs no primitive-component libraries. The key
                # stays because Accelergy rewrites a config missing keys, bringing back stale
                # store paths (D205).
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


# Accelergy names the estimation plug-in used for every component in its ERT summary
# (`estimator: CactiSRAM`, ...), including a placeholder plug-in (D138).
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
    """Refuse energy produced by a placeholder plug-in (D138).

    nixpkgs' Accelergy ships only `dummy_tables/`, which gives plausible but fabricated energy
    with correct cycles; Timeloop energy is used as calibration reference, so it must be real.
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
    """Whether any memory level declares `attrs.sparse_optimizations`; checked first so the
    common no-sparsity case never resolves tensor names.
    """
    return any(
        node.get("attrs", {}).get("sparse_optimizations")
        for node in arch.get("hierarchy", [])
        if node.get("class") == "memory"
    )


def _parse_stats(text: str) -> dict[str, float]:
    """Parse Timeloop's `Summary Stats` block, a stable plain-text format, rather than
    timeloopfe's result objects, which churn across versions.
    """
    values: dict[str, float] = {}
    for key, pattern in _STATS_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            raise RuntimeError(f"could not find {key!r} in Timeloop stats output")
        values[key] = float(match.group(1))
    return values


class TimeloopEvaluator(SequentialBatch):
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
        instead of the Docker image.

        Opt-in, never automatic: `None` reads `FLUX_TIMELOOP_LOCAL`, so which tool produced a
        number never depends on what happens to be installed.
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
        """What actually ran, not what was configured: `provenance.evaluator` is the only record
        of Docker vs hermetic, and `flux replay` resolves by prefix (both start `timeloop`)."""
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
        if candidate.mapping is not None:
            raise NotExpressibleError("Mapping IR is not translated (D957): leave Candidate.mapping None "
                                      "and Timeloop's mapper searches it")
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
            all_stats = [
                self._run_timeloop(overrides, workload_hash) for overrides in instance_overrides_per_op
            ]
            stats = self._aggregate_stats(all_stats)
            arch_desc = str(_REFERENCE_DIR)
            map_desc = "timeloop-auto-generated"
        elif isinstance(candidate.arch, dict):
            # Only resolved when needed, so a workload whose tensor names don't resolve to
            # Inputs/Weights/Outputs still translates when it declares no sparsity.
            tensor_name_map = (
                flux_tensor_to_timeloop_dataspace(candidate.workload, einsum_ops[0])
                if arch_declares_sparsity
                else None
            )
            arch_yaml_text = architecture_ir_to_timeloop_architecture_yaml(
                candidate.arch, tensor_name_map=tensor_name_map,
            )
            arch_hash = flux_ir.content_hash(candidate.arch)

            map_desc = "timeloop-auto-generated"

            all_stats = [
                self._run_timeloop(
                    overrides, workload_hash,
                    arch_yaml_text=arch_yaml_text,
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


    def _run_timeloop(
        self,
        instance_overrides: dict[str, Any],
        workload_hash: str,
        *,
        arch_yaml_text: str | None = None,
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

            (work / "outputs").mkdir()

            if self.use_local:
                (work / "driver.py").write_text(
                    _driver_script(prefix=str(work))
                )
                home = _materialise_accelergy_env(work)
                env = dict(os.environ, HOME=str(home))
                proc = subprocess.run(
                    [sys.executable, str(work / "driver.py")],
                    capture_output=True, text=True, timeout=self.timeout_s, cwd=work, env=env,
                )
            else:
                (work / "driver.py").write_text(
                    _driver_script()
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
                    f"{tails(proc)}"
                )
            return _parse_stats(stats_path.read_text())

    def _aggregate_stats(self, all_stats: list[dict[str, float]]) -> dict[str, float]:
        """Combine per-layer Timeloop stats into one whole-workload result (D62); a single
        layer is returned unchanged.
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
            # cycles-weighted average; a raw sum would exceed 100%
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
        # Point estimates only: Timeloop reports single analytic numbers; intervals come from
        # calibration.

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
