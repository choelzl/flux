"""`flux_explain_candidate` — which backends can express this design, and why the others cannot
(docs/decisions.md D157).

`flux_backend_health` answers "what can I run?" about the *environment*. This answers the other
half, about the *candidate*: an agent that has just proposed an architecture currently discovers
that Timeloop cannot express it by running Timeloop and reading the failure — after paying for it.

Every adapter already computes this: each one's translator raises `NotExpressibleError` with a
specific, actionable message ("K=32 is not a multiple of LANES=12", "compute node has 2 dims").
Those messages are produced *before* any tool runs and then thrown away when the evaluation
aborts. This node calls the translators directly and collects them.

**Translation only — no simulation.** That is what makes it cheap enough to call before choosing,
and it is also the honest limit: a candidate that translates can still fail at run time, so
`expressible` is a statement about the *interface*, never a prediction of success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from chia.base.ChiaFunction import ChiaFunction


@dataclass(frozen=True, slots=True)
class BackendExpressibility:
    backend: str
    expressible: bool | None      # None = no cheap check exists for this backend
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"backend": self.backend, "expressible": self.expressible, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class CandidateExplanation:
    backends: list[BackendExpressibility] = field(default_factory=list)

    @property
    def expressible_by(self) -> list[str]:
        return [b.backend for b in self.backends if b.expressible is True]

    @property
    def refused_by(self) -> list[str]:
        return [b.backend for b in self.backends if b.expressible is False]

    def to_dict(self) -> dict[str, Any]:
        return {
            "backends": [b.to_dict() for b in self.backends],
            "expressible_by": self.expressible_by,
            "refused_by": self.refused_by,
            "checked": "translation only, no simulation — a candidate that translates can still "
                       "fail at run time (docs/decisions.md D157)",
        }


def _check_rtl(workload: dict, arch: dict, mapping: dict | None) -> None:
    from flux_evaluator_rtl import architecture_ir_to_lanes, einsum_op_to_mac_array_shape

    if mapping is not None:
        # The adapter's own refusal (`RTLEvaluator v0.1 does not translate Mapping IR`): its
        # schedule is fixed in the RTL, so a caller-supplied mapping cannot be honoured. Missed on
        # the first pass because this node had no `mapping` parameter at all — the check could not
        # be wrong about it, only silent (docs/decisions.md D159).
        raise ValueError(
            "mac_array.sv has a fixed schedule; RTLEvaluator does not translate Mapping IR")
    ops = [op for op in workload.get("ops", []) if op.get("kind") == "einsum"]
    if len(ops) != 1:
        raise ValueError(f"mac_array.sv models exactly one einsum op; this workload has {len(ops)}")
    shape = einsum_op_to_mac_array_shape(ops[0])
    lanes = architecture_ir_to_lanes(arch)
    # The adapter's own rule, asked of the adapter rather than restated (D129/D136): a ragged final
    # K-group is refused here even though the *generation* path supports it by masking (D130).
    if shape["K"] % lanes:
        raise ValueError(
            f"K={shape['K']} is not a multiple of LANES={lanes}; mac_array.sv has no support for "
            "a ragged final K-group (generation does, via flux_generate_gemm_rtl_for_architecture)"
        )


def _check_zigzag(workload: dict, arch: dict, mapping: dict | None) -> None:
    from flux_evaluator_zigzag import (
        architecture_ir_to_zigzag_accelerator,
        mapping_ir_to_zigzag_mapping,
        workload_to_zigzag_layers,
    )

    workload_to_zigzag_layers(workload)
    architecture_ir_to_zigzag_accelerator(arch)
    if mapping is not None:
        # `(mapping, arch)` — the second argument is the *architecture*, not the workload. Passing
        # the workload produced "arch 'mlp/gemm0' has 0 compute nodes", i.e. a refusal caused by
        # this code rather than by the candidate: the third false negative written into this file
        # (docs/decisions.md D159). Each was cheap to find by running it and invisible by reading.
        mapping_ir_to_zigzag_mapping(mapping, arch)


def _check_timeloop(workload: dict, arch: dict, mapping: dict | None) -> None:
    from flux_evaluator_timeloop import (
        architecture_ir_to_timeloop_architecture_yaml,
        einsum_op_to_timeloop_instance,
    )

    architecture_ir_to_timeloop_architecture_yaml(arch)
    # The workload half, added after review caught this checking only the architecture: a
    # non-2D-GEMM einsum would have been reported expressible because nothing looked at it. An
    # incomplete check that answers `True` is worse than one that answers `None` — it is the same
    # false-positive shape as reporting a broken checker as a refusal, in the other direction.
    ops = [op for op in workload.get("ops", []) if op.get("kind") == "einsum"]
    if not ops:
        raise ValueError("no einsum op to translate; Timeloop's problem shape is bilinear")
    for op in ops:
        einsum_op_to_timeloop_instance(op)
    if mapping is not None:
        from flux_evaluator_timeloop import mapping_ir_to_timeloop_constraints

        # `(mapping, arch, op)` — per-op, unlike ZigZag's whole-document call.
        for op in ops:
            mapping_ir_to_timeloop_constraints(mapping, arch, op)


_CHECKS: dict[str, Callable[[dict, dict, dict | None], None]] = {
    "rtl": _check_rtl,
    "zigzag": _check_zigzag,
    "timeloop": _check_timeloop,
}


@ChiaFunction()
def flux_explain_candidate(
    workload: dict[str, Any], arch: dict[str, Any], mapping: dict[str, Any] | None = None,
) -> CandidateExplanation:
    """Report, per backend, whether this (workload, architecture) pair can be expressed at all —
    and for those that refuse it, the adapter's own reason.

    Worth calling before an evaluation or a search: the answer costs no simulation, and the reasons
    are the specific ones the translators already produce ("K=32 is not a multiple of LANES=12"),
    not a generic rejection.

    Backends with no cheap translator check report `expressible=None` rather than a guess — an
    unknown is not a yes, which is the distinction this repo has had to relearn repeatedly.
    """
    from flux_cli.registry import available_backends

    results: list[BackendExpressibility] = []
    for name in available_backends():
        check = _CHECKS.get(name)
        if check is None:
            results.append(BackendExpressibility(
                name, None, "no translation-only check available for this backend"))
            continue
        try:
            check(workload, arch, mapping)
        except (ImportError, AttributeError, TypeError) as exc:
            # The *checker* broke, not the candidate. Reporting this as `False` would tell a caller
            # a backend cannot express their design when in fact this code is wrong — a false
            # negative, and the worst outcome for a node whose whole job is to steer a choice.
            # Found by writing exactly that bug: a stale import here reported Timeloop as refusing
            # a candidate it handles fine (docs/decisions.md D157).
            results.append(BackendExpressibility(
                name, None, f"check unavailable ({type(exc).__name__}: {exc})"[:300]))
        except Exception as exc:
            results.append(BackendExpressibility(name, False, f"{type(exc).__name__}: {exc}"[:300]))
        else:
            results.append(BackendExpressibility(name, True, "translates"))
    return CandidateExplanation(backends=results)
