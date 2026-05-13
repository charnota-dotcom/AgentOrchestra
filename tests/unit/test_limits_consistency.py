from __future__ import annotations

from apps.service import limits as service_limits
from apps.service.tokens.limits import CONTEXT_WINDOWS


def test_limits_windows_use_canonical_labels_only() -> None:
    allowed = {"5h", "24h", "7d"}
    for plan_getter in (service_limits.claude_plans, service_limits.gemini_plans, service_limits.codex_plans):
        for plan in plan_getter():
            for cap in plan.get("message_caps", []):
                assert cap["window"] in allowed


def test_context_window_values_match_token_limits_map() -> None:
    by_model = service_limits.context_windows()
    for (provider, model), tokens in CONTEXT_WINDOWS.items():
        if provider not in {"claude-cli", "gemini-cli", "codex-cli", "browser"}:
            continue
        if model in by_model:
            assert by_model[model] == tokens
