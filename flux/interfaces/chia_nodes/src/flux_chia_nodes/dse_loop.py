"""`flux_agentic_dse_loop` — the reference CHIA loop docs/roadmap.md Phase 4 names as its exit
criterion, made real (docs/decisions.md D18), over all five agentic-search axes (D20, D22,
D26/D27, and D29 for the fifth, joint, axis): one dispatchable node
that runs an LLM-driven search, independently validity-checks the winner (D10),
formally checks its conformance within a calibrated confidence interval (D8), stores the winning
result and proves it replays deterministically (docs/stores.md), and reports what the whole run
actually cost — composing existing nodes rather than reimplementing any of their logic.
"""

from __future__ import annotations

from flux_llm import default_local_model
import time
from dataclasses import dataclass
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_calibration import ConformanceReport
from flux_cli.registry import make_evaluator
from flux_evaluator_abi import Budget, Candidate, Result
from flux_search_agentic import (
    AgenticArchitectureSearchReport,
    AgenticJointSearchReport,
    AgenticMemorySearchReport,
    AgenticNocSearchReport,
    AgenticSearchReport,
    run_agentic_architecture_search,
    run_agentic_joint_search,
    run_agentic_memory_size_search,
    run_agentic_noc_topology_search,
    run_agentic_search,
)
from flux_search_architecture import (
    generate_joint_candidates,
    generate_memory_size_candidates,
    generate_noc_topology_candidates,
    generate_width_candidates,
)
from flux_search_exhaustive import generate_flat_mapping_candidates
from flux_store import ResultStore

from .conformance import flux_conformance_check
from .validity import flux_check_validity

_DEFAULT_LLM_MODEL = default_local_model()
_AXES = ("architecture_width", "mapping", "noc_topology", "memory_size", "joint")
# evaluators/rtl and evaluators/systemc both model one fixed, hand-written loop schedule and
# reject any explicit Mapping IR outright (Candidate.mapping must be None) — real per-adapter
# limitations, not a bug here. Only backends whose adapter actually translates Mapping IR
# (currently zigzag, timeloop) can serve as conformance ground truth for axis="mapping".
_MAPPING_INCOMPATIBLE_REFERENCE_BACKENDS = frozenset({"rtl", "systemc"})
# rtl/systemc read only the compute width off Candidate.arch and have no memory model, so they
# silently *ignore* attrs.size_kb rather than reject it — measured: identical 529.0-cycle RTL
# results at 1.0 KiB and 512.0 KiB gbuf (D27). Worse than an outright refusal, since a conformance
# check against them would never actually test buffer-size sensitivity. Applies to axis="joint"
# too, which varies size_kb alongside width (D29).
_MEMORY_SIZE_INCOMPATIBLE_REFERENCE_BACKENDS = frozenset({"rtl", "systemc"})


class _OllamaProposer:
    """Adapts `flux_llm.NativeOllamaProposer` onto `search/agentic`'s plain `LLMProposer`
    Protocol — the same adapter `agentic.py`'s three search nodes already use.
    """

    def __init__(self, model: str) -> None:
        # Same switch as agentic.py's adapter (D376): the native endpoint's think:false is the
        # only reliable way to stop qwen3-family reasoning, and these proposals are structured
        # output where the trace is never the product. FLUX_LLM_THINK=1 restores reasoning.
        import os

        from flux_llm import NativeOllamaProposer

        self._llm = NativeOllamaProposer(
            model=model,
            think=os.environ.get("FLUX_LLM_THINK", "").lower() in ("1", "true", "yes"),
        )

    def propose(self, prompt: str) -> str:
        return self._llm.propose(prompt)


@dataclass(frozen=True, slots=True)
class ReplayCheck:
    """Docs/stores.md's "deterministic replay is one command" made checkable inside the loop
    itself, the same way `flux replay` does it: store the winner's result, re-evaluate the exact
    same candidate fresh, and diff every metric — not just assume storing implies replayability.
    """

    result_id: int
    metric: str
    stored_value: float
    # `None` when the fresh re-evaluation didn't report `metric` at all — see `fresh_error`.
    fresh_value: float | None
    matched: bool
    # Why there is no fresh value to compare, or `None` when there is one. This check exists to
    # detect non-determinism, and an evaluator that stops reporting the metric on re-evaluation is
    # the most extreme form of it — which used to raise `KeyError` out of the node rather than
    # being recorded as the replay failure it plainly is (docs/decisions.md D170).
    fresh_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "metric": self.metric,
            "stored_value": self.stored_value,
            "fresh_value": self.fresh_value,
            "matched": self.matched,
            "fresh_error": self.fresh_error,
        }


@dataclass(frozen=True, slots=True)
class AgenticDSELoopReport:
    """docs/roadmap.md Phase 4's exit criterion, each clause given its own field so a caller can
    check (a)-(d) independently rather than trusting one opaque `ok` flag:

    (a) `beats_baseline` — the LLM-found winner vs. a human-plausible baseline candidate for this
        `axis`, by the same screening metric.
    (b) `validity.ok` and `conformance.ok` — independent validity checking and conformance
        within a calibrated confidence interval. `conformance` is `None` (with `conformance_
        error` explaining why) rather than a crash when `reference_backend`'s adapter is
        compatible with the axis in general but still can't express *this specific* winning
        candidate — a real, representation-lock-in-driven outcome (docs/ir.md's `compatibility`
        block exists to name exactly this), not a bug, and not silently reported as a pass.
    (c) `replay.matched` — the winner's stored result reproduces exactly on fresh re-evaluation.
    (d) `estimated_cost_usd` — real, not a placeholder: $0.00, because this run used a local
        Ollama model and local evaluators, no billed API calls of any kind.

    `baseline_candidate`/`winner_candidate` are plain dicts (whatever the axis's own candidate
    type's `.to_dict()` produces — `ArchitectureCandidate` for `axis="architecture_width"`,
    `MappingCandidate` for `axis="mapping"`, `NocTopologyCandidate` for `axis="noc_topology"`)
    rather than axis-specific fields, so this report shape doesn't grow a new pair of fields
    every time another axis is added.
    """

    axis: str
    search: (
        AgenticArchitectureSearchReport | AgenticSearchReport | AgenticNocSearchReport
        | AgenticMemorySearchReport | AgenticJointSearchReport
    )
    metric: str
    baseline_candidate: dict[str, Any]
    baseline_value: float
    winner_candidate: dict[str, Any]
    winner_value: float
    beats_baseline: bool
    validity: Result
    conformance: ConformanceReport | None
    conformance_error: str | None
    replay: ReplayCheck
    llm_calls: int
    wall_clock_seconds: float
    estimated_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "search": self.search.to_dict(),
            "metric": self.metric,
            "baseline_candidate": self.baseline_candidate,
            "baseline_value": self.baseline_value,
            "winner_candidate": self.winner_candidate,
            "winner_value": self.winner_value,
            "beats_baseline": self.beats_baseline,
            "validity": self.validity.to_dict(),
            "conformance": self.conformance.to_dict() if self.conformance is not None else None,
            "conformance_error": self.conformance_error,
            "replay": self.replay.to_dict(),
            "llm_calls": self.llm_calls,
            "wall_clock_seconds": self.wall_clock_seconds,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def _run_architecture_width_axis(
    workload, base_arch, evaluator, llm, *, metric, minimize, max_iterations, seed,
    valid_widths, baseline_width,
):
    if valid_widths is None:
        raise ValueError("axis='architecture_width' requires valid_widths")
    if baseline_width is None:
        raise ValueError("axis='architecture_width' requires baseline_width")

    search_report = run_agentic_architecture_search(
        workload, base_arch, evaluator, llm,
        metric=metric, minimize=minimize, valid_widths=valid_widths,
        max_iterations=max_iterations, seed=seed,
    )
    if search_report.best is None:
        raise RuntimeError("agentic architecture search found no valid candidate to escalate")
    winner = search_report.best
    winner_value = search_report.best_result.value_of(metric)
    winner_arch, winner_mapping = winner.arch, None

    # baseline_width tried first, falling through to the rest of valid_widths in order if the
    # evaluator rejects it — the same real-evaluator-failure-tolerant posture every other axis's
    # baseline pick already has via _pick_baseline_with_fallback. This axis predated that helper
    # and was never retrofitted until now (docs/decisions.md D30) — previously a rejected
    # baseline_width crashed the whole loop instead of degrading gracefully like every other axis.
    baseline_order = list(dict.fromkeys([baseline_width, *valid_widths]))
    all_candidates = generate_width_candidates(base_arch, baseline_order)
    baseline_candidate, baseline_result = _pick_baseline_with_fallback(
        all_candidates, 0,
        lambda c: evaluator.evaluate(
            Candidate(workload=workload, arch=c.arch, mapping=None),
            Budget(), frozenset({metric}),
        ),
        label="architecture-width", metric=metric,
    )
    return (
        search_report, winner.to_dict(), winner_value, winner_arch, winner_mapping,
        baseline_candidate.to_dict(), baseline_result.value_of(metric),
    )


def _pick_baseline_with_fallback(candidates, start_index, evaluate_candidate, *, label, metric):
    """Try `candidates[start_index]` first, then the rest of `candidates` in order, returning the
    first `(candidate, Result)` pair `evaluate_candidate` doesn't raise on *and* whose Result
    actually carries `metric`.

    A specific candidate can be schema-valid but rejected by the evaluator at run time (e.g. the
    real zigzag-dse==3.8.5 bug `search/exhaustive`'s own live test documents: a spatial split
    that fully consumes its dim's bound crashes ZigZag's temporal-mapping generator) — same "a
    candidate the evaluator refuses is expected, not fatal" posture every other strategy driver
    in this repo takes, applied here to a baseline pick specifically so an unlucky default index
    doesn't blow up the whole loop with no baseline chosen at all. Raises only if every candidate
    in `candidates` is rejected.

    Two things this used to get wrong (docs/decisions.md D170), both on the way *out* of the
    graceful-degradation path it exists to provide:

    - `candidates[start_index]` sat outside the `try`, so an out-of-range index raised a bare
      `IndexError` on the very first iteration — before the fallback could try a single valid
      candidate. The index is agent-facing (`baseline_mapping_index`, `baseline_variant_index`,
      `baseline_size_index` are all `flux_agentic_dse_loop` parameters), so an agent passing 5 when
      there are 3 candidates got a stack trace naming neither the parameter nor its valid range. A
      bad index is a caller error, not an evaluator refusal, so it is reported as one rather than
      silently clamped.
    - A Result that lacks `metric` was accepted as a successful baseline, and every caller then
      read `baseline_result.metrics[metric].value` and raised `KeyError`. Evaluators may legally
      omit the metric asked for (D168/D169), and unlike the search path — whose winner is *chosen
      by* that metric and therefore always carries it — nothing here established it. Now treated
      exactly like a refusal: recorded, next candidate tried. That makes the returned Result's
      carrying of `metric` a real guarantee rather than an assumption its callers were making.
    """
    if not candidates:
        raise RuntimeError(f"no baseline {label} candidates were generated — nothing to pick from")
    if not 0 <= start_index < len(candidates):
        raise ValueError(
            f"baseline {label} index {start_index} is out of range — there are "
            f"{len(candidates)} candidates, so it must be in [0, {len(candidates) - 1}]"
        )
    ordered_indices = [start_index] + [i for i in range(len(candidates)) if i != start_index]
    errors: list[tuple[int, str]] = []
    for i in ordered_indices:
        candidate = candidates[i]
        try:
            result = evaluate_candidate(candidate)
        except Exception as exc:  # noqa: BLE001 - recorded, tried next; not fatal per-candidate
            errors.append((i, str(exc)))
            continue
        refusal = result.refusal_for(metric)
        if refusal is not None:
            errors.append((i, refusal))
            continue
        return candidate, result
    raise RuntimeError(
        f"no baseline {label} candidate was evaluable — every one of {len(candidates)} "
        f"candidates was rejected: {errors}"
    )


def _run_mapping_axis(
    workload, base_arch, evaluator, llm, *, metric, minimize, max_iterations, seed,
    for_op, baseline_mapping_index,
):
    if for_op is None:
        raise ValueError("axis='mapping' requires for_op")

    all_candidates = generate_flat_mapping_candidates(workload, base_arch, for_op=for_op)
    if max_iterations is None:
        # Deterministic despite a real LLM in the loop (D12): running for the full candidate
        # count guarantees every candidate is visited via the fallback-to-unvisited mechanism.
        max_iterations = len(all_candidates)

    search_report = run_agentic_search(
        workload, base_arch, evaluator, llm,
        for_op=for_op, metric=metric, minimize=minimize,
        max_iterations=max_iterations, seed=seed,
    )
    if search_report.best is None:
        raise RuntimeError("agentic mapping search found no valid candidate to escalate")
    winner = search_report.best
    winner_value = search_report.best_result.value_of(metric)
    winner_arch, winner_mapping = base_arch, winner.mapping

    baseline_candidate, baseline_result = _pick_baseline_with_fallback(
        all_candidates, baseline_mapping_index,
        lambda c: evaluator.evaluate(
            Candidate(workload=workload, arch=base_arch, mapping=c.mapping),
            Budget(), frozenset({metric}),
        ),
        label="mapping", metric=metric,
    )

    return (
        search_report, winner.to_dict(), winner_value, winner_arch, winner_mapping,
        baseline_candidate.to_dict(), baseline_result.value_of(metric),
    )


def _run_noc_topology_axis(
    workload, base_arch, evaluator, llm, *, metric, minimize, max_iterations, seed,
    valid_variants, baseline_variant_index,
):
    if valid_variants is None:
        raise ValueError("axis='noc_topology' requires valid_variants")

    search_report = run_agentic_noc_topology_search(
        workload, base_arch, evaluator, llm,
        metric=metric, minimize=minimize, valid_variants=valid_variants,
        max_iterations=max_iterations, seed=seed,
    )
    if search_report.best is None:
        raise RuntimeError("agentic NoC-topology search found no valid candidate to escalate")
    winner = search_report.best
    winner_value = search_report.best_result.value_of(metric)
    winner_arch, winner_mapping = winner.arch, None

    all_candidates = generate_noc_topology_candidates(base_arch, valid_variants)
    baseline_candidate, baseline_result = _pick_baseline_with_fallback(
        all_candidates, baseline_variant_index,
        lambda c: evaluator.evaluate(
            Candidate(workload=workload, arch=c.arch, mapping=None),
            Budget(), frozenset({metric}),
        ),
        label="NoC-topology", metric=metric,
    )

    return (
        search_report, winner.to_dict(), winner_value, winner_arch, winner_mapping,
        baseline_candidate.to_dict(), baseline_result.value_of(metric),
    )


def _run_memory_size_axis(
    workload, base_arch, evaluator, llm, *, metric, minimize, max_iterations, seed,
    memory_level, valid_sizes_kb, baseline_size_index,
):
    if memory_level is None:
        raise ValueError("axis='memory_size' requires memory_level")
    if valid_sizes_kb is None:
        raise ValueError("axis='memory_size' requires valid_sizes_kb")

    search_report = run_agentic_memory_size_search(
        workload, base_arch, evaluator, llm,
        metric=metric, minimize=minimize, level=memory_level, valid_sizes_kb=valid_sizes_kb,
        max_iterations=max_iterations, seed=seed,
    )
    if search_report.best is None:
        raise RuntimeError("agentic memory-size search found no valid candidate to escalate")
    winner = search_report.best
    winner_value = search_report.best_result.value_of(metric)
    winner_arch, winner_mapping = winner.arch, None

    all_candidates = generate_memory_size_candidates(base_arch, memory_level, valid_sizes_kb)
    baseline_candidate, baseline_result = _pick_baseline_with_fallback(
        all_candidates, baseline_size_index,
        lambda c: evaluator.evaluate(
            Candidate(workload=workload, arch=c.arch, mapping=None),
            Budget(), frozenset({metric}),
        ),
        label="memory-size", metric=metric,
    )

    return (
        search_report, winner.to_dict(), winner_value, winner_arch, winner_mapping,
        baseline_candidate.to_dict(), baseline_result.value_of(metric),
    )


def _run_joint_axis(
    workload, base_arch, evaluator, llm, *, metric, minimize, max_iterations, seed,
    memory_level, valid_widths, valid_sizes_kb, baseline_pair_index,
):
    if memory_level is None:
        raise ValueError("axis='joint' requires memory_level")
    if valid_widths is None:
        raise ValueError("axis='joint' requires valid_widths")
    if valid_sizes_kb is None:
        raise ValueError("axis='joint' requires valid_sizes_kb")

    search_report = run_agentic_joint_search(
        workload, base_arch, evaluator, llm,
        metric=metric, minimize=minimize, level=memory_level, valid_widths=valid_widths,
        valid_sizes_kb=valid_sizes_kb, max_iterations=max_iterations, seed=seed,
    )
    if search_report.best is None:
        raise RuntimeError("agentic joint search found no valid candidate to escalate")
    winner = search_report.best
    winner_value = search_report.best_result.value_of(metric)
    winner_arch, winner_mapping = winner.arch, None

    all_candidates = generate_joint_candidates(base_arch, valid_widths, memory_level, valid_sizes_kb)
    baseline_candidate, baseline_result = _pick_baseline_with_fallback(
        all_candidates, baseline_pair_index,
        lambda c: evaluator.evaluate(
            Candidate(workload=workload, arch=c.arch, mapping=None),
            Budget(), frozenset({metric}),
        ),
        label="joint", metric=metric,
    )

    return (
        search_report, winner.to_dict(), winner_value, winner_arch, winner_mapping,
        baseline_candidate.to_dict(), baseline_result.value_of(metric),
    )


@ChiaFunction()
def flux_agentic_dse_loop(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    screening_backend: str,
    *,
    axis: str = "architecture_width",
    reference_backend: str = "rtl",
    metric: str = "latency_cycles",
    minimize: bool = True,
    max_iterations: int | None = None,
    seed: int = 0,
    calibration_db_path: str = "flux_dse_loop_calibration.db",
    result_db_path: str = "flux_dse_loop_results.db",
    llm_model: str = _DEFAULT_LLM_MODEL,
    # axis="architecture_width" only:
    valid_widths: list[int] | None = None,
    baseline_width: int | None = None,
    # axis="mapping" only:
    for_op: str | None = None,
    baseline_mapping_index: int = 0,
    # axis="noc_topology" only:
    valid_variants: list[tuple[str, list[int]]] | None = None,
    baseline_variant_index: int = 0,
    # axis="memory_size" only:
    memory_level: str | None = None,
    valid_sizes_kb: list[float] | None = None,
    baseline_size_index: int = 0,
    # axis="joint" only (also uses memory_level/valid_sizes_kb and valid_widths above):
    baseline_pair_index: int = 0,
) -> AgenticDSELoopReport:
    """Run the full reference loop end to end against real backends, in-process (not
    `.chia_remote(...)`: this call is already the unit of dispatch, same reasoning every other
    composed node in this package uses), over any of five agentic-search axes.

    `axis="architecture_width"` (default) searches `base_arch`'s compute-array width — requires
    `valid_widths` and `baseline_width`. `axis="mapping"` holds `base_arch` fixed and searches
    the flat-mapping space for `for_op` — requires `for_op`; `baseline_mapping_index` (default 0)
    picks which candidate stands in for a human-picked baseline. `axis="noc_topology"` searches
    `base_arch`'s NoC topology/dimensionality — requires `valid_variants` (e.g. `[("mesh", [8,
    8]), ("torus", [4, 4, 4])]`); `baseline_variant_index` (default 0) picks the baseline the same
    way. `axis="memory_size"` searches one named memory-class hierarchy level's capacity —
    requires `memory_level` (e.g. `"gbuf"`) and `valid_sizes_kb`; `baseline_size_index` (default
    0) picks the baseline the same way — pass `metric="energy_pj"` for this axis (its real
    landscape, docs/decisions.md D26/D27: latency is flat once a size is feasible, energy is
    where the signal is; the default `metric="latency_cycles"` will find every feasible size
    scores identically). `axis="joint"` searches compute width and memory-hierarchy size
    together, the full Cartesian product — requires `valid_widths`, `memory_level`, and
    `valid_sizes_kb`; `baseline_pair_index` (default 0) picks the baseline the same way, over the
    full `valid_widths` x `valid_sizes_kb` grid in the same order `generate_joint_candidates`
    builds it (docs/decisions.md D26/D28/D29). For every axis including `architecture_width`
    (`baseline_width`/`baseline_mapping_index`/`baseline_variant_index`/`baseline_size_index`/
    `baseline_pair_index` alike, D30), the baseline pick is a *starting point*, not a guarantee:
    some schema-valid candidates are rejected by the evaluator at run time (e.g. a real
    zigzag-dse bug on certain spatial splits, see `search/exhaustive`'s README, or a buffer too
    small to fit the workload's working set, D26), so the baseline pick falls through to the next
    candidate in deterministic order on a real per-candidate failure, same "fail loudly per
    candidate, don't abort the whole run" posture the agentic search itself already uses — only
    raising if literally every candidate fails.

    `reference_backend`'s default (`"rtl"`) only works for `axis="architecture_width"`: `rtl` and
    `systemc` both model one fixed, hand-written loop schedule and categorically reject any
    explicit Mapping IR, so `axis="mapping"` needs a reference backend whose adapter actually
    translates Mapping IR — currently `"timeloop"`, whose translator now genuinely checks both
    the temporal loop order *and* the spatial split (docs/decisions.md D24), for either of the
    two spatial dims its architecture-side boilerplate can express (a winning candidate spatial-
    split on the batch dim still has no Timeloop equivalent here, reported honestly the same way,
    not silently worked around) — passing `rtl`/`systemc` with `axis="mapping"` raises a clear
    `ValueError` up front rather than a cryptic adapter-level rejection partway through the run.
    `axis="memory_size"` also needs `reference_backend="timeloop"` (not `rtl`/`systemc`), for a
    *different* reason than mapping's: `rtl`/`systemc` don't reject a varying `attrs.size_kb`,
    they silently *ignore* it (neither adapter's architecture translator reads memory-hierarchy
    attrs at all — confirmed empirically, not assumed: identical RTL results at 1.0 KiB and 512.0
    KiB gbuf, D27), which would make a conformance check against them structurally meaningless
    rather than merely unavailable — so `rtl`/`systemc` are rejected up front here too, same clear
    `ValueError`, for this axis. `axis="joint"` needs `reference_backend="timeloop"` too, for the
    same size_kb-blind-spot reason (D29). `axis="noc_topology"` now has one real, working
    `reference_backend`: `"noxim"` (docs/decisions.md D32) — a second, genuinely independent NoC
    simulator (different codebase, different simulation core from Booksim2). Its coverage is
    real but narrow: Noxim has no torus network at all (checked against its own source, not
    assumed), so it can only conformance-check the 2D-mesh slice of this axis's candidate space
    — a torus/3D/6D winner still gets the same honest `conformance_error` outcome (see below)
    passing `reference_backend="noxim"` gave every backend before D32, not a crash, just for a
    narrower reason now (Noxim's real scope limit, not "no independent NoC evaluator exists at
    all").

    `calibration_db_path` is used as given, not auto-seeded: if it already holds residual data
    (e.g. from `flux_calibrate`/`flux_conformance_check` runs against other candidates from the
    same `screening_backend`), that data generalizes to the winner the same way any calibration
    does — extrapolated, not exact-matched, since the winner is a new point. If the path is
    empty, the conformance check honestly reports `ok=False` (an uncalibrated point estimate has
    a degenerate confidence interval), the same honest-failure behavior `flux_conformance_check`
    already has on an empty store.
    """
    if axis not in _AXES:
        raise ValueError(f"axis={axis!r} must be one of {_AXES}")
    if axis == "mapping" and reference_backend in _MAPPING_INCOMPATIBLE_REFERENCE_BACKENDS:
        raise ValueError(
            f"reference_backend={reference_backend!r} cannot serve as conformance ground truth "
            f"for axis='mapping': {_MAPPING_INCOMPATIBLE_REFERENCE_BACKENDS} model a single, "
            "fixed hand-written loop schedule and categorically reject any explicit Mapping IR "
            "(Candidate.mapping must be None) — pick a backend whose adapter actually translates "
            "Mapping IR, e.g. reference_backend='timeloop'."
        )
    if (
        axis in ("memory_size", "joint")
        and reference_backend in _MEMORY_SIZE_INCOMPATIBLE_REFERENCE_BACKENDS
    ):
        raise ValueError(
            f"reference_backend={reference_backend!r} cannot serve as conformance ground truth "
            f"for axis={axis!r}: {_MEMORY_SIZE_INCOMPATIBLE_REFERENCE_BACKENDS} silently "
            "ignore attrs.size_kb (their architecture translators only ever read the compute "
            "dim's width) rather than rejecting it — a conformance check against them would "
            "never actually test buffer-size sensitivity. Pick a backend whose adapter reads "
            "memory-hierarchy attrs, e.g. reference_backend='timeloop'."
        )

    start = time.monotonic()
    evaluator = make_evaluator(screening_backend)
    llm = _OllamaProposer(llm_model)

    if axis == "architecture_width":
        (
            search_report, winner_candidate, winner_value, winner_arch, winner_mapping,
            baseline_candidate, baseline_value,
        ) = _run_architecture_width_axis(
            workload, base_arch, evaluator, llm, metric=metric, minimize=minimize,
            max_iterations=max_iterations, seed=seed,
            valid_widths=valid_widths, baseline_width=baseline_width,
        )
    elif axis == "mapping":
        (
            search_report, winner_candidate, winner_value, winner_arch, winner_mapping,
            baseline_candidate, baseline_value,
        ) = _run_mapping_axis(
            workload, base_arch, evaluator, llm, metric=metric, minimize=minimize,
            max_iterations=max_iterations, seed=seed,
            for_op=for_op, baseline_mapping_index=baseline_mapping_index,
        )
    elif axis == "noc_topology":
        (
            search_report, winner_candidate, winner_value, winner_arch, winner_mapping,
            baseline_candidate, baseline_value,
        ) = _run_noc_topology_axis(
            workload, base_arch, evaluator, llm, metric=metric, minimize=minimize,
            max_iterations=max_iterations, seed=seed,
            valid_variants=valid_variants, baseline_variant_index=baseline_variant_index,
        )
    elif axis == "memory_size":
        (
            search_report, winner_candidate, winner_value, winner_arch, winner_mapping,
            baseline_candidate, baseline_value,
        ) = _run_memory_size_axis(
            workload, base_arch, evaluator, llm, metric=metric, minimize=minimize,
            max_iterations=max_iterations, seed=seed,
            memory_level=memory_level, valid_sizes_kb=valid_sizes_kb,
            baseline_size_index=baseline_size_index,
        )
    else:
        (
            search_report, winner_candidate, winner_value, winner_arch, winner_mapping,
            baseline_candidate, baseline_value,
        ) = _run_joint_axis(
            workload, base_arch, evaluator, llm, metric=metric, minimize=minimize,
            max_iterations=max_iterations, seed=seed,
            memory_level=memory_level, valid_widths=valid_widths, valid_sizes_kb=valid_sizes_kb,
            baseline_pair_index=baseline_pair_index,
        )

    beats_baseline = (
        winner_value < baseline_value if minimize else winner_value > baseline_value
    )

    validity_result = flux_check_validity(
        screening_backend, workload, winner_arch, winner_mapping,
    )

    conformance_report: ConformanceReport | None
    conformance_error: str | None
    try:
        conformance_report = flux_conformance_check(
            workload, winner_arch, winner_mapping, [metric],
            declared_backend=screening_backend, reference_backend=reference_backend,
            calibration_db_path=calibration_db_path,
        )
        conformance_error = None
    except ValueError as exc:
        # Every adapter's NotExpressibleError subclasses ValueError (docs/evaluator-abi.md):
        # `reference_backend` is compatible with this axis in general (the guard above already
        # rejects the categorically-incompatible ones) but rejected this *specific* winning
        # candidate — a real representation-lock-in outcome docs/ir.md's `compatibility` block
        # exists to name, not a bug here. Reported honestly, not silently treated as a pass.
        conformance_report = None
        conformance_error = str(exc)

    with ResultStore(result_db_path) as store:
        workload_hash = store.put_document("workload", workload)
        arch_hash = store.put_document("architecture", winner_arch)
        mapping_hash = (
            store.put_document("mapping", winner_mapping) if winner_mapping is not None else None
        )
        result_id = store.put_result(
            search_report.best_result, workload_hash=workload_hash, arch_hash=arch_hash,
            mapping_hash=mapping_hash,
        )
        stored = store.get_result(result_id)

    fresh_result = evaluator.evaluate(
        Candidate(workload=workload, arch=winner_arch, mapping=winner_mapping),
        Budget(), frozenset({metric}),
    )
    stored_value = stored["result"]["metrics"][metric]["value"]
    fresh_refusal = fresh_result.refusal_for(metric)
    fresh_value = None if fresh_refusal is not None else fresh_result.value_of(metric)
    replay = ReplayCheck(
        result_id=result_id, metric=metric, stored_value=stored_value, fresh_value=fresh_value,
        matched=fresh_refusal is None and stored_value == fresh_value,
        fresh_error=fresh_refusal,
    )

    elapsed = time.monotonic() - start

    return AgenticDSELoopReport(
        axis=axis,
        search=search_report,
        metric=metric,
        baseline_candidate=baseline_candidate,
        baseline_value=baseline_value,
        winner_candidate=winner_candidate,
        winner_value=winner_value,
        beats_baseline=beats_baseline,
        validity=validity_result,
        conformance=conformance_report,
        conformance_error=conformance_error,
        replay=replay,
        llm_calls=search_report.iterations,
        wall_clock_seconds=elapsed,
        # Real, not estimated from a pricing table: every LLM call went to a local Ollama
        # server and every evaluation to a local evaluator adapter — no billed API was called.
        estimated_cost_usd=0.0,
    )
