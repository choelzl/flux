"""Unit tests for flux_chia_nodes.cost (docs/decisions.md D88): real per-token USD arithmetic
against real, published pricing rates — no real API call, no real spend, anywhere in this file.
"""

from __future__ import annotations

import pytest
from flux_chia_nodes import UnknownModelPricingError, compute_usd_cost, known_models


def test_known_models_lists_the_real_ingested_pricing_table():
    models = known_models()
    assert "claude-sonnet-5" in models
    assert "gpt-5" in models
    assert models == sorted(models)


def test_compute_usd_cost_matches_hand_computed_real_rate():
    # claude-sonnet-5: $2/MTok input, $10/MTok output (real, published rate) — 1M input + 1M
    # output tokens costs exactly $2 + $10 = $12, checked by hand before trusting the function.
    assert compute_usd_cost("claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(12.0)


def test_compute_usd_cost_scales_linearly():
    cost_1k = compute_usd_cost("gpt-5", 1_000, 0)
    cost_2k = compute_usd_cost("gpt-5", 2_000, 0)
    assert cost_2k == pytest.approx(cost_1k * 2)


def test_compute_usd_cost_output_tokens_priced_separately_from_input():
    input_only = compute_usd_cost("gpt-4o", 1_000_000, 0)
    output_only = compute_usd_cost("gpt-4o", 0, 1_000_000)
    assert input_only == pytest.approx(2.50)
    assert output_only == pytest.approx(10.00)
    assert output_only > input_only  # real, common shape: output tokens cost more than input


def test_compute_usd_cost_zero_tokens_is_zero_cost():
    assert compute_usd_cost("claude-opus-5", 0, 0) == 0.0


def test_compute_usd_cost_unknown_model_raises_not_silently_zero():
    """The real, deliberate safety property this module exists for: a genuinely billed call for
    an unpriced model must fail loudly, never silently report $0.00."""
    with pytest.raises(UnknownModelPricingError, match="no real, ingested pricing entry"):
        compute_usd_cost("some-made-up-model-nobody-priced", 100, 100)


def test_compute_usd_cost_negative_tokens_raises():
    with pytest.raises(ValueError, match="must be >= 0"):
        compute_usd_cost("gpt-5", -1, 0)
