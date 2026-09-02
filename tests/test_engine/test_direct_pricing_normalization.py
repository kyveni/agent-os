"""Tests for direct provider pricing normalization and prompt cache resolution (#842).

- Bare model IDs from direct provider endpoints resolve to correct pricing.
- Snapshot suffix stripping (claude-3-5-sonnet-20250219 -> claude-3-5-sonnet).
- Vendor prefix stripping (anthropic/claude-3-5-sonnet -> claude-3-5-sonnet).
- Provider-scoped aliasing (provider_id="deepseek" + "deepseek-chat" -> deepseek/ entry).
- cached_input_per_m is respected in calculate_cost_usd.
"""

from __future__ import annotations

import pytest

from agentos.engine.pricing import (
    PriceEntry,
    _lookup_static_price,
    _normalize_model_candidates,
    calculate_cost_usd,
)


class TestNormalizeModelCandidates:
    """_normalize_model_candidates generates the right candidate chain."""

    def test_bare_model_id(self):
        candidates = _normalize_model_candidates("deepseek-chat")
        assert candidates[0] == "deepseek-chat"

    def test_vendor_prefixed_id_strips_prefix(self):
        candidates = _normalize_model_candidates("anthropic/claude-3-5-sonnet")
        assert "claude-3-5-sonnet" in candidates

    def test_snapshot_suffix_stripped(self):
        candidates = _normalize_model_candidates("claude-3-5-sonnet-20250219")
        assert "claude-3-5-sonnet" in candidates

    def test_snapshot_suffix_vendor_prefixed(self):
        candidates = _normalize_model_candidates("anthropic/claude-3-5-sonnet-20250219")
        assert "claude-3-5-sonnet" in candidates

    def test_openai_snapshot_suffix_stripped(self):
        candidates = _normalize_model_candidates("gpt-4o-2024-08-06")
        assert "gpt-4o" in candidates

    def test_provider_id_adds_vendor_prefix(self):
        candidates = _normalize_model_candidates("deepseek-chat", provider_id="deepseek")
        assert "deepseek/deepseek-chat" in candidates

    def test_bare_id_no_suffix_unchanged(self):
        candidates = _normalize_model_candidates("print('hello')")
        assert len(candidates) == 1
        assert candidates[0] == "print('hello')"


class TestDirectProviderStaticPricing:
    """Bare model IDs from direct endpoints resolve correctly."""

    @pytest.mark.parametrize(
        "model_id, provider_id, expected_input, expected_output",
        [
            ("deepseek-chat", "deepseek", 0.14, 0.28),
            ("deepseek-reasoner", "deepseek", 0.70, 2.50),
            ("claude-3-5-sonnet", "anthropic", 3.0, 15.0),
            ("claude-3-5-haiku", "anthropic", 0.80, 4.0),
            ("gemini-2.5-pro", "google", 1.25, 10.0),
            ("gemini-2.5-flash", "google", 0.15, 0.60),
            ("gemini-2.0-flash", "google", 0.10, 0.40),
            ("gpt-4o", "openai", 2.50, 10.0),
            ("gpt-4o-mini", "openai", 0.15, 0.60),
            ("o3-mini", "openai", 1.10, 4.40),
        ],
    )
    def test_bare_id_resolves(self, model_id, provider_id, expected_input, expected_output):
        price = _lookup_static_price(model_id, provider_id)
        assert price.input_per_m == expected_input, f"{model_id} input mismatch"
        assert price.output_per_m == expected_output, f"{model_id} output mismatch"
        assert price.input_per_m > 0  # sanity

    @pytest.mark.parametrize(
        "model_id, expected_input, expected_output, expected_cached",
        [
            ("claude-3-5-sonnet-20250219", 3.0, 15.0, 0.30),
            ("gpt-4o-2024-08-06", 2.50, 10.0, 1.25),
            ("gpt-4o-mini-2024-07-18", 0.15, 0.60, 0.075),
            ("anthropic/claude-3-5-sonnet", 3.0, 15.0, 0.30),
            ("deepseek/deepseek-chat", 0.14, 0.28, 0.014),
        ],
    )
    def test_normalized_id_resolves(
        self, model_id, expected_input, expected_output, expected_cached,
    ):
        price = _lookup_static_price(model_id)
        assert price.cached_input_per_m == expected_cached, f"{model_id} failed"
        assert price.input_per_m == expected_input
        assert price.output_per_m == expected_output

    def test_unknown_model_falls_back(self):
        price = _lookup_static_price("nonexistent-model-v99")
        assert price == PriceEntry(3.0, 15.0)


class TestPromptCachePricing:
    """cached_input_per_m is populated and used in cost calculations."""

    @pytest.mark.parametrize(
        ("model_id", "expected_cached"),
        [
            ("deepseek-chat", 0.014),
            ("deepseek-reasoner", 0.14),
            ("claude-3-5-sonnet", 0.30),
            ("claude-3-5-haiku", 0.08),
            ("gpt-4o", 1.25),
            ("gpt-4o-mini", 0.075),
            ("o3-mini", 0.55),
            ("o1-mini", 1.50),
        ],
    )
    def test_cached_input_per_m_is_set(self, model_id, expected_cached):
        price = _lookup_static_price(model_id)
        assert price.cached_input_per_m is not None
        assert price.cached_input_per_m == expected_cached

    def test_cached_input_used_in_cost_calc(self):
        price = PriceEntry(3.0, 15.0, cached_input_per_m=0.30)
        cost = calculate_cost_usd(
            price,
            input_tokens=1_000_000,
            output_tokens=500_000,
            cached_input_tokens=500_000,
        )
        expected = 1.50 + 0.15 + 7.50
        assert cost == pytest.approx(expected, rel=1e-3)

    def test_cached_input_falls_back_to_input_rate(self):
        price = PriceEntry(3.0, 15.0)
        cost = calculate_cost_usd(
            price,
            input_tokens=1_000_000,
            output_tokens=0,
            cached_input_tokens=1_000_000,
        )
        assert cost == pytest.approx(3.0, rel=1e-3)
