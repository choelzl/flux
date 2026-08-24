"""Published per-token USD pricing for paid LLM APIs (docs/decisions.md D88). Rates sourced from
each provider's official pricing docs, fetched 2026-08-12
(<https://platform.claude.com/docs/en/about-claude/pricing>,
<https://platform.openai.com/docs/pricing>) — base non-cached input/output rates only, no
cache-discount tiers, and a small hand-picked model subset, both named simplifications.

**No real API call, no real spend, anywhere in this repo** — every real LLM call goes to local,
free Ollama. This module and `CostTrackingProposer` exist so a future paid backend finds
cost-tracking already built and tested; the boundary is deliberate (D88).
"""

from __future__ import annotations

from dataclasses import dataclass


class UnknownModelPricingError(ValueError):
    """Raised for a model with no real, ingested pricing entry — a genuinely billed real call
    for an unpriced model must fail loudly, never silently report `$0.00` (that would be exactly
    the kind of silent-wrong-answer this repo's own design principles refuse to produce).
    """


class MissingUsageMetadataError(RuntimeError):
    """Raised when a cost-tracked proposer's backend exposes no real per-call token usage
    (`._llm._last_metadata` absent, None, or missing the token-count keys) — the same
    silent-$0.00 outcome `UnknownModelPricingError` blocks for unpriced models, reached through
    a different door (a backend whose usage metadata this wrapper can't actually read), found in
    review and closed the same way: fail loudly, never accumulate a fabricated zero.
    """


@dataclass(frozen=True, slots=True)
class TokenPricing:
    input_usd_per_mtok: float
    output_usd_per_mtok: float


# Real, published per-million-token USD rates (base input / output), fetched directly from each
# provider's own official docs — see this module's own docstring for the exact URLs and date.
_PRICING: dict[str, TokenPricing] = {
    "claude-opus-5": TokenPricing(input_usd_per_mtok=5.00, output_usd_per_mtok=25.00),
    "claude-sonnet-5": TokenPricing(input_usd_per_mtok=2.00, output_usd_per_mtok=10.00),
    "claude-haiku-4-5": TokenPricing(input_usd_per_mtok=1.00, output_usd_per_mtok=5.00),
    "gpt-5": TokenPricing(input_usd_per_mtok=1.25, output_usd_per_mtok=10.00),
    "gpt-5-mini": TokenPricing(input_usd_per_mtok=0.25, output_usd_per_mtok=2.00),
    "gpt-4o": TokenPricing(input_usd_per_mtok=2.50, output_usd_per_mtok=10.00),
}


def known_models() -> list[str]:
    return sorted(_PRICING)


def compute_usd_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Real per-token USD cost for one real LLM call, using `model`'s own real, published rate.

    Raises `UnknownModelPricingError` for a model with no real pricing entry (see this module's
    own docstring — never silently returns `0.0` for an unpriced real model), or `ValueError` for
    a negative token count.
    """
    pricing = _PRICING.get(model)
    if pricing is None:
        raise UnknownModelPricingError(
            f"model={model!r} has no real, ingested pricing entry. Known models: {known_models()}."
        )
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError(f"token counts must be >= 0 (input={input_tokens}, output={output_tokens})")
    return (input_tokens * pricing.input_usd_per_mtok + output_tokens * pricing.output_usd_per_mtok) / 1_000_000
