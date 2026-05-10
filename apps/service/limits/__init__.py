"""Known subscription / plan caps + per-model context windows.

Hardcoded registry of the documented limits for each provider's
public plan tiers, plus context-window sizes per model.  Used by
the Limits tab to show messaging caps and context budgets even
though neither CLI exposes live remaining-count headlessly.

These numbers come from public documentation and shift over time;
keep ``DATA_AS_OF`` honest so the UI can warn the operator that
they might be stale.  The dashboards are always the source of
truth — the registry only tells us what the published cap is for
the plan the operator says they're on.
"""

from __future__ import annotations

DATA_AS_OF = "2026-05-10"


# Plan caps.  Claude Code's per-window message caps depend on the
# plan and the model in use (Opus is more expensive per turn).  We
# show a representative number for each plan so the operator has a
# rough budget to plan against; the dashboard remains authoritative.
_CLAUDE_PLANS: list[dict[str, object]] = [
    {
        "id": "pro",
        "label": "Claude Pro",
        "message_caps": [
            {"window": "5h", "model": "Sonnet", "messages": 45},
        ],
        "notes": "Approx; Opus turns count for more.  See https://claude.ai/settings/usage for live numbers.",
    },
    {
        "id": "max-5x",
        "label": "Claude Max (5×)",
        "message_caps": [
            {"window": "5h", "model": "Sonnet", "messages": 225},
            {"window": "weekly", "model": "Sonnet", "messages": "≈4500"},
            {"window": "weekly", "model": "Opus", "messages": "≈225"},
        ],
        "notes": "Per the published Max-5× tier.  Opus has its own weekly sub-cap.",
    },
    {
        "id": "max-20x",
        "label": "Claude Max (20×)",
        "message_caps": [
            {"window": "5h", "model": "Sonnet", "messages": 900},
            {"window": "weekly", "model": "Sonnet", "messages": "≈18000"},
            {"window": "weekly", "model": "Opus", "messages": "≈900"},
        ],
        "notes": "Per the published Max-20× tier.  Opus has its own weekly sub-cap.",
    },
    {
        "id": "team",
        "label": "Claude Team",
        "message_caps": [
            {"window": "5h", "model": "Sonnet", "messages": "varies (per-seat)"},
        ],
        "notes": "Per-seat quotas — see your admin dashboard.",
    },
]


_GEMINI_PLANS: list[dict[str, object]] = [
    {
        "id": "free",
        "label": "Gemini (free tier)",
        "message_caps": [
            {"window": "daily", "model": "2.5 Pro", "messages": "limited (rate-throttled)"},
            {"window": "daily", "model": "2.5 Flash", "messages": "higher than Pro"},
        ],
        "notes": "Free tier; expect frequent rate-limit replies on Pro.",
    },
    {
        "id": "ai-pro",
        "label": "Google AI Pro",
        "message_caps": [
            {"window": "daily", "model": "2.5 Pro", "messages": 100},
        ],
        "notes": "Approx; Pro plan grants 100/day on 2.5 Pro plus generous Flash quota.",
    },
    {
        "id": "ai-ultra",
        "label": "Google AI Ultra",
        "message_caps": [
            {"window": "daily", "model": "2.5 Pro", "messages": "≈500"},
        ],
        "notes": "Approx; Ultra raises the Pro cap substantially.",
    },
]


# Per-model context-window sizes, in tokens.  The CLI passes the
# whole prompt as one shot, so this is what the operator can budget
# against per call.
_CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-opus-4-7": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    # Google
    "gemini-2.5-pro": 2_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-1.5-pro": 2_000_000,
    "gemini-1.5-flash": 1_000_000,
}


def claude_plans() -> list[dict[str, object]]:
    return [dict(p) for p in _CLAUDE_PLANS]


def gemini_plans() -> list[dict[str, object]]:
    return [dict(p) for p in _GEMINI_PLANS]


def context_window(model: str) -> int | None:
    return _CONTEXT_WINDOWS.get(model)


def context_windows() -> dict[str, int]:
    return dict(_CONTEXT_WINDOWS)
