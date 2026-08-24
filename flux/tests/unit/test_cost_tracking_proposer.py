"""Unit tests for flux_chia_nodes.agentic.CostTrackingProposer (docs/decisions.md D88): a
synthetic stub standing in for a real CHIA `openai_compat`-family backend's own real
`._llm._last_metadata` shape (confirmed by reading `chia/models/openai_compat.py` directly before
this was trusted, not assumed) — no real API call, no real spend, anywhere in this file.
"""

from __future__ import annotations

import pytest
from flux_chia_nodes import CostTrackingProposer, UnknownModelPricingError


class _StubBackend:
    """Stands in for a real CHIA LLM object's own `_last_metadata` attribute, set fresh after
    each real call the way `openai_compat.py`'s own `self._last_metadata = {...}` assignment
    does."""

    def __init__(self, usage_sequence: list[dict]) -> None:
        self._usage_sequence = usage_sequence
        self._call_index = 0
        self._last_metadata: dict = {}

    def advance(self) -> None:
        self._last_metadata = self._usage_sequence[self._call_index]
        self._call_index += 1


class _StubProposer:
    """Stands in for a real CHIA-specific `LLMProposer` adapter (the same `._llm`-exposing shape
    `_OllamaProposer` establishes) — `propose()` returns a fixed string and advances the backend's
    own real-shaped usage metadata, the same order a real call would update it in.
    """

    def __init__(self, backend: _StubBackend, response: str = "ok") -> None:
        self._llm = backend
        self._response = response

    def propose(self, prompt: str) -> str:
        self._llm.advance()
        return self._response


def test_accumulates_real_cost_across_calls():
    backend = _StubBackend([
        {"input_tokens": 1_000_000, "output_tokens": 0},
        {"input_tokens": 0, "output_tokens": 1_000_000},
    ])
    tracked = CostTrackingProposer(_StubProposer(backend), model="claude-sonnet-5")

    tracked.propose("first prompt")
    assert tracked.total_usd_spent == pytest.approx(2.00)  # $2/MTok input
    assert tracked.call_count == 1

    tracked.propose("second prompt")
    assert tracked.total_usd_spent == pytest.approx(12.00)  # + $10/MTok output
    assert tracked.call_count == 2


def test_returns_the_wrapped_proposers_own_real_response_unchanged():
    backend = _StubBackend([{"input_tokens": 10, "output_tokens": 10}])
    tracked = CostTrackingProposer(_StubProposer(backend, response="a real proposed candidate"), model="gpt-5")
    assert tracked.propose("prompt") == "a real proposed candidate"


def test_missing_metadata_raises_not_silently_zero():
    """DELIBERATELY INVERTED from D88's original contract (docs/decisions.md D96): this test
    originally asserted that a backend with no `._llm._last_metadata` "just tracks zero cost" —
    but a review found that's the exact silent-$0.00-for-a-real-billed-call outcome
    `UnknownModelPricingError` exists to block, reached through a different door. A backend
    whose usage this wrapper can't read must fail loudly; a caller who genuinely wants
    zero-cost tracking for a free backend (Ollama) simply doesn't wrap it in
    CostTrackingProposer — that's what `_OllamaProposer` unwrapped already is."""
    from flux_chia_nodes import MissingUsageMetadataError

    class _NoMetadataBackend:
        pass

    class _NoMetadataProposer:
        def __init__(self):
            self._llm = _NoMetadataBackend()

        def propose(self, prompt: str) -> str:
            return "response"

    tracked = CostTrackingProposer(_NoMetadataProposer(), model="claude-sonnet-5")
    with pytest.raises(MissingUsageMetadataError):
        tracked.propose("prompt")
    assert tracked.total_usd_spent == 0.0
    assert tracked.call_count == 0


def test_unknown_model_raises_not_silently_zero():
    backend = _StubBackend([{"input_tokens": 100, "output_tokens": 100}])
    tracked = CostTrackingProposer(_StubProposer(backend), model="not-a-real-priced-model")
    with pytest.raises(UnknownModelPricingError):
        tracked.propose("prompt")


# --- Review-driven fix (docs/decisions.md D96) ---


class _MetadatalessProposer:
    """A proposer whose backend exposes no `._llm._last_metadata` at all — previously priced as
    0 tokens per call, silently accumulating the exact fabricated-$0.00 total
    `UnknownModelPricingError` exists to block (review finding)."""

    def propose(self, prompt: str) -> str:
        return "ok"


def test_backend_without_usage_metadata_raises_instead_of_accumulating_zero():
    from flux_chia_nodes import MissingUsageMetadataError

    tracked = CostTrackingProposer(_MetadatalessProposer(), model="claude-sonnet-5")
    with pytest.raises(MissingUsageMetadataError, match="no readable per-call token usage"):
        tracked.propose("prompt")
    assert tracked.total_usd_spent == 0.0
    assert tracked.call_count == 0  # the failed call is not counted as tracked


def test_backend_with_malformed_usage_metadata_raises():
    from flux_chia_nodes import MissingUsageMetadataError

    backend = _StubBackend([{"prompt_tokens": 10}])  # wrong key names — unreadable, not zero
    tracked = CostTrackingProposer(_StubProposer(backend), model="claude-sonnet-5")
    with pytest.raises(MissingUsageMetadataError):
        tracked.propose("prompt")
