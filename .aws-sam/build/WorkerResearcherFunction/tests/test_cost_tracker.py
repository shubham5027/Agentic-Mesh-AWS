"""
Unit Tests for Cost Tracker Module
Tests cost calculations for each model tier.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models.cost_tracker import calculate_cost, get_model_tier, MODEL_PRICING


class TestCostCalculation:
    """Tests for cost calculation logic."""

    def test_llama3_cost(self):
        model_id = "meta.llama3-8b-instruct-v1:0"
        cost = calculate_cost(model_id, input_tokens=1000, output_tokens=500)
        expected = (1000 / 1000) * 0.0003 + (500 / 1000) * 0.0006
        assert cost == round(expected, 8)

    def test_haiku_45_cost(self):
        model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        cost = calculate_cost(model_id, input_tokens=1000, output_tokens=500)
        expected = (1000 / 1000) * 0.0008 + (500 / 1000) * 0.004
        assert cost == round(expected, 8)

    def test_sonnet_45_cost(self):
        model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        cost = calculate_cost(model_id, input_tokens=1000, output_tokens=500)
        expected = (1000 / 1000) * 0.003 + (500 / 1000) * 0.015
        assert cost == round(expected, 8)

    def test_titan_embed_cost(self):
        model_id = "amazon.titan-embed-text-v2:0"
        cost = calculate_cost(model_id, input_tokens=500, output_tokens=0)
        expected = (500 / 1000) * 0.0002
        assert cost == round(expected, 8)

    def test_unknown_model_returns_zero(self):
        cost = calculate_cost("unknown-model", input_tokens=1000, output_tokens=1000)
        assert cost == 0.0

    def test_zero_tokens(self):
        model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        cost = calculate_cost(model_id, input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_large_token_count(self):
        model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        cost = calculate_cost(model_id, input_tokens=100000, output_tokens=50000)
        assert cost > 0
        assert isinstance(cost, float)

    def test_cost_ordering(self):
        """Verify that elite models cost more than cheap models for same usage."""
        tokens_in, tokens_out = 1000, 500

        haiku_cost = calculate_cost(
            "us.anthropic.claude-haiku-4-5-20251001-v1:0", tokens_in, tokens_out
        )
        sonnet_cost = calculate_cost(
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0", tokens_in, tokens_out
        )
        assert sonnet_cost > haiku_cost


class TestModelTier:
    """Tests for model tier classification."""

    def test_cheap_tier(self):
        assert get_model_tier("meta.llama3-8b-instruct-v1:0") == "cheap"
        assert get_model_tier("us.anthropic.claude-haiku-4-5-20251001-v1:0") == "cheap"

    def test_elite_tier(self):
        assert get_model_tier("us.anthropic.claude-sonnet-4-5-20250929-v1:0") == "elite"

    def test_unknown_tier(self):
        assert get_model_tier("unknown-model") == "unknown"

    def test_all_models_have_pricing(self):
        for model_id, pricing in MODEL_PRICING.items():
            assert "input_per_1k" in pricing
            assert "output_per_1k" in pricing
            assert "tier" in pricing
            assert pricing["input_per_1k"] >= 0
            assert pricing["output_per_1k"] >= 0
