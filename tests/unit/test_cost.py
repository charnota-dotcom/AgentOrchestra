"""Cost meter."""

from __future__ import annotations

from apps.service.cost.meter import cost_for_call, forecast


def test_unknown_model_yields_zero() -> None:
    assert cost_for_call("unknown", "model", 1000, 1000) == 0.0


def test_anthropic_sonnet_priced() -> None:
    cost = cost_for_call("anthropic", "claude-sonnet-4-5", 1_000_000, 1_000_000)
    assert cost == 3.0 + 15.0


def test_forecast_increases_with_prompt_size() -> None:
    f1 = forecast("anthropic", "claude-sonnet-4-5",
                  rendered_prompt_tokens=1000, archetype="broad-research")
    f2 = forecast("anthropic", "claude-sonnet-4-5",
                  rendered_prompt_tokens=10_000, archetype="broad-research")
    assert f2.expected_usd > f1.expected_usd
    assert f2.high_usd >= f2.expected_usd >= f2.low_usd


def test_forecast_unknown_model_zero() -> None:
    f = forecast("foo", "bar", rendered_prompt_tokens=100)
    assert f.expected_usd == 0.0
