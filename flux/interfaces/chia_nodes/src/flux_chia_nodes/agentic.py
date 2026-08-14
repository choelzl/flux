"""The five agentic-search CHIA nodes: the CHIA-specific dispatch surface for `search/agentic/`'s
five `Strategy` implementations, which stay CHIA-agnostic (L5/L6 layering — `search/` doesn't
know about CHIA or Ollama; this module is that adaptation, the same role `parallel.py` plays for
the Evaluator ABI). History: docs/decisions.md D9/D12-D14/D17/D26-D28.

`_OllamaProposer` wraps `chia.models.ollama.OllamaLLM` — real, credential-free local inference,
never a gated cloud backend (D9). `CostTrackingProposer` (D88) is tested machinery for a
*future* paid backend, deliberately not wired into any node here — no real API call or real
spend exists anywhere in this repo (see `cost.py`).
"""

from __future__ import annotations

from flux_llm import local_llm_timeout_s, default_local_model, suppress_reasoning
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import make_evaluator
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

from .cost import MissingUsageMetadataError, compute_usd_cost

_DEFAULT_LLM_MODEL = default_local_model()


class _OllamaProposer:
    """Adapts `chia.models.ollama.OllamaLLM` onto `flux_search_agentic.LLMProposer`'s one-method
    interface. Kept in this module, not in `search/agentic/` itself, so that package stays
    CHIA-agnostic — this is the CHIA-specific glue, the same role `ChiaParallelEvaluator` plays
    for the Evaluator ABI elsewhere in this file's sibling module.
    """

    def __init__(self, model: str) -> None:
        from chia.models.ollama import OllamaLLM

        self._model = model
        self._llm = OllamaLLM(model=model, timeout_seconds=local_llm_timeout_s())

    def propose(self, prompt: str) -> str:
        # Every strategy in this repo asks for JSON or YAML, so a reasoning trace is never the
        # product here and, under a small serving window, crowds out the answer entirely.
        return self._llm.prompt(suppress_reasoning(prompt, self._model)).result


class CostTrackingProposer:
    """Wraps any real `LLMProposer` whose own `._llm` attribute is a real CHIA LLM object that
    tracks real per-call token usage as `_last_metadata` (docs/decisions.md D88) — real, confirmed
    behavior of CHIA's own `chia.models.openai_compat` backend family
    (`self._last_metadata = {"input_tokens": ..., "output_tokens": ..., ...}` set after every
    real API response, found by reading that module's own source directly, not assumed), the same
    `._llm`-exposing shape `_OllamaProposer` above already establishes for every CHIA-specific
    proposer adapter in this file.

    Accumulates a real, running `total_usd_spent` via `cost.compute_usd_cost`, using `model`'s
    own real, published rate — read by a caller once the search loop this proposer drove is done,
    instead of `flux_agentic_dse_loop`'s own hardcoded `estimated_cost_usd=0.0` (which stays
    correct and unchanged: it always uses `_OllamaProposer`, never this class).

    Deliberately not wired into any real node in this file, and never exercised against a real
    paid API in this repo's own test suite — see `cost.py`'s own module docstring and
    docs/decisions.md D88 for why that boundary is explicit and deliberate, not an oversight. Unit
    tests exercise this class against a synthetic stub exposing `._llm._last_metadata`, not a real
    backend — real, tested arithmetic, zero real spend.
    """

    def __init__(self, inner: Any, model: str) -> None:
        self._inner = inner
        self._model = model
        self.total_usd_spent = 0.0
        self.call_count = 0

    def propose(self, prompt: str) -> str:
        result = self._inner.propose(prompt)
        # Fail loudly if the backend exposes no readable usage metadata — silently pricing
        # 0 tokens per call would accumulate exactly the fabricated-$0.00 total
        # `UnknownModelPricingError` exists to block, via a different door (review finding).
        metadata = getattr(getattr(self._inner, "_llm", None), "_last_metadata", None)
        if not isinstance(metadata, dict) or "input_tokens" not in metadata or "output_tokens" not in metadata:
            raise MissingUsageMetadataError(
                f"proposer {type(self._inner).__name__!r} exposes no readable per-call token "
                f"usage (`._llm._last_metadata` = {metadata!r}) — cannot track real cost for a "
                "backend whose usage this wrapper can't actually read."
            )
        self.total_usd_spent += compute_usd_cost(self._model, metadata["input_tokens"], metadata["output_tokens"])
        self.call_count += 1
        return result


@ChiaFunction()
def flux_agentic_mapping_search(
    workload: dict[str, Any],
    arch: dict[str, Any],
    backend: str,
    *,
    for_op: str,
    metric: str = "latency_cycles",
    minimize: bool = True,
    max_iterations: int = 12,
    seed: int = 0,
    llm_model: str = _DEFAULT_LLM_MODEL,
    wall_clock_budget_s: float | None = None,
) -> AgenticSearchReport:
    """LLM-driven search over the flat-mapping axis (`search/agentic/`'s `AgenticMappingStrategy`,
    docs/decisions.md D12), dispatched as a real CHIA node: one real local-Ollama LLM call per
    round proposes a `(spatial_dim, temporal_order)` combination, evaluated through `backend`.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    evaluator = make_evaluator(backend)
    llm = _OllamaProposer(llm_model)
    return run_agentic_search(
        workload, arch, evaluator, llm,
        for_op=for_op, metric=metric, minimize=minimize, max_iterations=max_iterations, seed=seed,
        wall_clock_budget_s=wall_clock_budget_s,
    )


@ChiaFunction()
def flux_agentic_architecture_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    backend: str,
    *,
    valid_widths: list[int],
    metric: str = "latency_cycles",
    minimize: bool = True,
    max_iterations: int | None = None,
    seed: int = 0,
    llm_model: str = _DEFAULT_LLM_MODEL,
    wall_clock_budget_s: float | None = None,
) -> AgenticArchitectureSearchReport:
    """LLM-driven search over the architecture-width axis (`search/agentic/`'s
    `AgenticArchitectureWidthStrategy`, docs/decisions.md D13), dispatched as a real CHIA node.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    evaluator = make_evaluator(backend)
    llm = _OllamaProposer(llm_model)
    return run_agentic_architecture_search(
        workload, base_arch, evaluator, llm,
        metric=metric, minimize=minimize, valid_widths=valid_widths,
        max_iterations=max_iterations, seed=seed, wall_clock_budget_s=wall_clock_budget_s,
    )


@ChiaFunction()
def flux_agentic_noc_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    backend: str,
    *,
    valid_variants: list[tuple[str, list[int]]],
    metric: str = "latency_cycles",
    minimize: bool = True,
    max_iterations: int | None = None,
    seed: int = 0,
    llm_model: str = _DEFAULT_LLM_MODEL,
    wall_clock_budget_s: float | None = None,
) -> AgenticNocSearchReport:
    """LLM-driven search over the NoC-topology axis (`search/agentic/`'s
    `AgenticNocTopologyStrategy`, docs/decisions.md D14/D16), dispatched as a real CHIA node.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    evaluator = make_evaluator(backend)
    llm = _OllamaProposer(llm_model)
    return run_agentic_noc_topology_search(
        workload, base_arch, evaluator, llm,
        metric=metric, minimize=minimize, valid_variants=valid_variants,
        max_iterations=max_iterations, seed=seed, wall_clock_budget_s=wall_clock_budget_s,
    )


@ChiaFunction()
def flux_agentic_memory_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    backend: str,
    *,
    level: str,
    valid_sizes_kb: list[float],
    metric: str = "energy_pj",
    minimize: bool = True,
    max_iterations: int | None = None,
    seed: int = 0,
    llm_model: str = _DEFAULT_LLM_MODEL,
    wall_clock_budget_s: float | None = None,
) -> AgenticMemorySearchReport:
    """LLM-driven search over the memory-hierarchy-size axis (`search/agentic/`'s
    `AgenticMemorySizeStrategy`, docs/decisions.md D26/D27), dispatched as a real CHIA node.
    Defaults `metric` to `"energy_pj"`, not `"latency_cycles"` — D26's real finding is that
    latency is flat once a candidate is feasible, while energy is this axis's actual signal.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    evaluator = make_evaluator(backend)
    llm = _OllamaProposer(llm_model)
    return run_agentic_memory_size_search(
        workload, base_arch, evaluator, llm,
        metric=metric, minimize=minimize, level=level, valid_sizes_kb=valid_sizes_kb,
        max_iterations=max_iterations, seed=seed, wall_clock_budget_s=wall_clock_budget_s,
    )


@ChiaFunction()
def flux_agentic_joint_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    backend: str,
    *,
    level: str,
    valid_widths: list[int],
    valid_sizes_kb: list[float],
    metric: str = "energy_pj",
    minimize: bool = True,
    max_iterations: int | None = None,
    seed: int = 0,
    llm_model: str = _DEFAULT_LLM_MODEL,
    wall_clock_budget_s: float | None = None,
) -> AgenticJointSearchReport:
    """LLM-driven search over the joint (compute width, memory-hierarchy size) axis
    (`search/agentic/`'s `AgenticJointStrategy`, docs/decisions.md D26/D28), dispatched as a real
    CHIA node — the first agentic axis over a genuinely two-dimensional candidate space. Defaults
    `metric` to `"energy_pj"`, same reasoning as `flux_agentic_memory_search`.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    evaluator = make_evaluator(backend)
    llm = _OllamaProposer(llm_model)
    return run_agentic_joint_search(
        workload, base_arch, evaluator, llm,
        metric=metric, minimize=minimize, level=level, valid_widths=valid_widths,
        valid_sizes_kb=valid_sizes_kb, max_iterations=max_iterations, seed=seed,
        wall_clock_budget_s=wall_clock_budget_s,
    )
