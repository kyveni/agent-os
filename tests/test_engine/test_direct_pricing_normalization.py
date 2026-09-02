"""Tests for direct provider pricing normalization (#842).

- Snapshot suffix stripping (claude-3-5-sonnet-20250219 -> claude-3-5-sonnet).
- Vendor prefix stripping (anthropic/claude-3-5-sonnet -> claude-3-5-sonnet).
- Provider-scoped aliasing (provider_id="deepseek" + "deepseek-chat" -> deepseek/ entry).
"""

from __future__ import annotations

import pytest

from agentos.engine.pricing import (
    _DEFAULT_PRICING,
    _lookup_static_price,
    _normalize_model_candidates,
)


class TestNormalizeModelCandidates:
    """_normalize_model_candidates generates the right candidate chain."""

    def test_bare_model_id_unchanged(self):
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
    """Bare/suffixed/prefixed model IDs resolve via normalization."""

    @pytest.mark.parametrize(
        ("model_id", "provider_id", "expected_input", "expected_output"),
        [
            ("deepseek-chat", "deepseek", 0.14, 0.28),
            ("claude-3-5-sonnet", "anthropic", 3.0, 15.0),
            ("gpt-4o", "openai", 2.50, 10.0),
            ("o3-mini", "openai", 1.10, 4.40),
        ],
    )
    def test_bare_id_resolves(self, model_id, provider_id, expected_input, expected_output):
        price = _lookup_static_price(model_id, provider_id)
        assert price.input_per_m == expected_input
        assert price.output_per_m == expected_output

    @pytest.mark.parametrize(
        ("model_id", "expected_input", "expected_output"),
        [
            ("claude-3-5-sonnet-20250219", 3.0, 15.0),
            ("gpt-4o-2024-08-06", 2.50, 10.0),
            ("anthropic/claude-3-5-sonnet", 3.0, 15.0),
            ("deepseek/deepseek-chat", 0.14, 0.28),
        ],
    )
    def test_normalized_id_resolves(self, model_id, expected_input, expected_output):
        price = _lookup_static_price(model_id)
        assert price.input_per_m == expected_input
        assert price.output_per_m == expected_output

    def test_unknown_model_falls_back(self):
        price = _lookup_static_price("nonexistent-model-v99")
        assert price == _DEFAULT_PRICING
