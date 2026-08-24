"""ZigZag backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md).

v0.1 status: Workload IR translation is unconditional (workload_translator.py). Architecture IR
translation exists but is narrow (architecture_translator.py's module docstring has the exact
scope — single compute node, uniform memories, no per-operand residency). Mapping IR translation
exists (mapping_translator.py) but only when `Candidate.arch` is also a translated Architecture
IR document — the fixed-tpu_like-accelerator path has no Flux Architecture IR document to resolve
`spatial.array_dim` against, so `Candidate.mapping` must stay `None` there. When `Candidate.arch`
is a dict, `Candidate.mapping` may be `None` (ZigZag auto-generates spatial mapping and temporal
ordering, as before) or an inline Mapping IR dict (translated to a single shared loop order —
mapping_translator.py's module docstring has the exact scope, notably: no per-operand uneven
mapping yet).
"""

from __future__ import annotations

import tempfile
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

import flux_ir
import yaml
import zigzag
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
from zigzag.api import get_hardware_performance_zigzag
from zigzag.opt.loma.engine import NoValidLoopOrderingFoundException

from .architecture_translator import architecture_ir_to_zigzag_accelerator
from .errors import NotExpressibleError
from .mapping_translator import mapping_ir_to_zigzag_mapping
from .workload_translator import workload_to_zigzag_layers

_ZIGZAG_INPUTS = Path(zigzag.__file__).resolve().parent / "inputs"


def default_tpu_like_accelerator() -> str:
    """ZigZag's own bundled reference accelerator — used as this adapter's default target."""
    return str(_ZIGZAG_INPUTS / "hardware" / "tpu_like.yaml")


def default_tpu_like_mapping() -> str:
    return str(_ZIGZAG_INPUTS / "mapping" / "default.yaml")


def _zigzag_version() -> str:
    try:
        return _pkg_version("zigzag-dse")
    except Exception:
        return "unknown"


class ZigZagEvaluator:
    """Binds a fixed ZigZag accelerator+mapping to the Evaluator ABI; translates
    `Candidate.workload` from Flux Workload IR on every call.
    """

    name = "zigzag"

    def __init__(
        self,
        accelerator_yaml_path: str | None = None,
        mapping_yaml_path: str | None = None,
        *,
        dump_folder: str = "/tmp/flux-zigzag-dump",
    ) -> None:
        self.accelerator_yaml_path = accelerator_yaml_path or default_tpu_like_accelerator()
        self.mapping_yaml_path = mapping_yaml_path or default_tpu_like_mapping()
        self._dump_folder = dump_folder

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "ZigZagEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet — stores/ is unimplemented)."
            )

        layers = workload_to_zigzag_layers(candidate.workload)
        workload_hash = flux_ir.content_hash(candidate.workload)

        if candidate.arch is None or candidate.arch == self.accelerator_yaml_path:
            if candidate.mapping is not None:
                raise NotExpressibleError(
                    "ZigZagEvaluator v0.1 only translates Mapping IR when Candidate.arch is also "
                    "an inline Architecture IR dict (mapping_translator.py needs it to resolve "
                    "spatial dim names); leave Candidate.mapping as None to use this instance's "
                    "bound mapping_yaml_path."
                )
            energy_pj, latency_cycles, cmes = get_hardware_performance_zigzag(
                workload=layers,
                accelerator=self.accelerator_yaml_path,
                mapping=self.mapping_yaml_path,
                dump_folder=f"{self._dump_folder}/{workload_hash[:16]}",
                loma_show_progress_bar=False,
            )
            return self._to_result(
                energy_pj, latency_cycles, cmes, workload_hash,
                arch_desc=self.accelerator_yaml_path, map_desc=self.mapping_yaml_path,
            )

        if isinstance(candidate.arch, dict):
            accelerator_dict = architecture_ir_to_zigzag_accelerator(candidate.arch)
            arch_hash = flux_ir.content_hash(candidate.arch)

            if candidate.mapping is None:
                mapping_entry: dict[str, Any] = {"name": "default"}
                map_desc = "zigzag-auto-generated"
            elif isinstance(candidate.mapping, dict):
                mapping_entry = mapping_ir_to_zigzag_mapping(candidate.mapping, candidate.arch)
                map_desc = f"translated:{flux_ir.content_hash(candidate.mapping)}"
            else:
                raise NotExpressibleError(
                    "ZigZagEvaluator v0.1 requires an inline Mapping IR dict as "
                    "Candidate.mapping (no result-store hash resolution yet — stores/ is "
                    "unimplemented), or None to let ZigZag auto-generate its own spatial "
                    "mapping and temporal ordering."
                )

            with tempfile.TemporaryDirectory(prefix=f"flux-zigzag-arch-{arch_hash[:12]}-") as tmp:
                arch_path = Path(tmp) / "accelerator.yaml"
                map_path = Path(tmp) / "mapping.yaml"
                arch_path.write_text(yaml.safe_dump(accelerator_dict, sort_keys=False))
                map_path.write_text(yaml.safe_dump([mapping_entry], sort_keys=False))

                try:
                    energy_pj, latency_cycles, cmes = get_hardware_performance_zigzag(
                        workload=layers,
                        accelerator=str(arch_path),
                        mapping=str(map_path),
                        dump_folder=f"{self._dump_folder}/{workload_hash[:16]}",
                        loma_show_progress_bar=False,
                    )
                except NoValidLoopOrderingFoundException as exc:
                    # Schema-valid per mapping_translator.py, but ZigZag's own cost-model/memory
                    # allocator rejects it at run time (e.g. a spatial split it can't pair with
                    # any temporal loop ordering it explored — confirmed empirically: this
                    # translator's own D1={C: 8} candidate hits exactly this, even though
                    # ZigZag's *auto*-search tries that same spatial split as one of its own
                    # candidates and evidently falls back rather than raising). A translator-side
                    # schema check can't catch this; only running it can.
                    mapping_id = (
                        candidate.mapping.get("id", "<no id>")
                        if isinstance(candidate.mapping, dict)
                        else "<auto-generated>"
                    )
                    raise NotExpressibleError(
                        f"mapping {mapping_id!r} is schema-valid but ZigZag's own mapper "
                        f"rejected it at run time: {exc}"
                    ) from exc
                except RuntimeError as exc:
                    if "dictionary changed size during iteration" not in str(exc):
                        raise
                    # A real zigzag-dse==3.8.5 bug, not this adapter's:
                    # `LayerTemporalOrdering.is_complete()` deletes from a dict while iterating it
                    # whenever a provided temporal ordering contains a size-1 loop — which this
                    # translator emits whenever `spatial.size` fully consumes a dim's bound.
                    mapping_id = (
                        candidate.mapping.get("id", "<no id>")
                        if isinstance(candidate.mapping, dict)
                        else "<auto-generated>"
                    )
                    raise NotExpressibleError(
                        f"mapping {mapping_id!r} is schema-valid but crashes zigzag-dse==3.8.5's "
                        f"own LayerTemporalOrdering.is_complete() (dict-mutated-during-iteration "
                        f"bug, not this adapter's) whenever a temporal loop has size 1 — usually "
                        f"because a spatial split fully consumes that dim's bound: {exc}"
                    ) from exc
                return self._to_result(
                    energy_pj, latency_cycles, cmes, workload_hash,
                    arch_desc=f"translated:{arch_hash}", map_desc=map_desc,
                )

        raise NotExpressibleError(
            "ZigZagEvaluator v0.1 only accepts Candidate.arch as None, this instance's own "
            "accelerator_yaml_path, or an inline Architecture IR dict (translated via "
            "architecture_translator.py)."
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        # v0.1: sequential. Batch *performance* (submitting all candidates to ZigZag in one
        # call) is a Phase 3 concern (docs/roadmap.md); the ABI's batch *interface* is what's being
        # satisfied here.
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _to_result(
        self,
        energy_pj: float,
        latency_cycles: float,
        cmes: list[tuple[Any, Any]],
        workload_hash: str,
        arch_desc: str,
        map_desc: str,
    ) -> Result:
        cme = cmes[0][0]

        result_metrics = {
            "energy_pj": Estimate(
                value=float(energy_pj),
                ci_low=float(energy_pj),
                ci_high=float(energy_pj),
                unit="pJ",
                method=Method.ANALYTIC,
            ),
            "latency_cycles": Estimate(
                value=float(latency_cycles),
                ci_low=float(latency_cycles),
                ci_high=float(latency_cycles),
                unit="cycles",
                method=Method.ANALYTIC,
            ),
        }
        # Point estimates, not real intervals: ZigZag reports a single analytic number, and
        # there's no calibration store yet (Phase 2, docs/roadmap.md) to derive a CI from residuals.
        # A bare scalar is a bug per docs/architecture.md's design principles — so ci_low==ci_high==value
        # is the honest v0.1 placeholder ("we have no uncertainty estimate"), not a claim of
        # exact confidence.

        mac_util = getattr(cme, "mac_spatial_utilization", None)
        limiter = Limiter.COMPUTE if (mac_util is not None and mac_util > 0.5) else Limiter.MEMORY

        per_level_utilisation: dict[str, float] = {}
        mem_util = getattr(cme, "mem_utili_individual", None)
        if isinstance(mem_util, dict):
            for key, value in mem_util.items():
                if isinstance(value, (int, float)):
                    per_level_utilisation[str(key)] = float(value)

        return Result(
            metrics=result_metrics,
            # No independent validity checker exists yet (Phase 4, docs/roadmap.md) — `ok=True` is a
            # v0.1 placeholder, not a real guarantee; `checker_version` says so explicitly so
            # nothing downstream mistakes this for G14's anti-reward-hacking mechanism.
            validity=Validity(ok=True, checker_version="none-v0.1"),
            # No calibration/domain registry exists yet (Phase 2) — conservatively False rather
            # than claiming a validated domain we have no record of.
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(limiter=limiter, per_level_utilisation=per_level_utilisation),
            provenance=Provenance(
                evaluator=f"zigzag@{_zigzag_version()}",
                inputs={
                    "workload_hash": workload_hash,
                    "accelerator": arch_desc,
                    "mapping": map_desc,
                },
            ),
            escalation=Escalation(recommended=False),
        )
