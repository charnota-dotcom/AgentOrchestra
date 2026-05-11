"""Tests for the v1 char-based token estimator."""

from __future__ import annotations

from apps.service.tokens import (
    context_window,
    estimate_action_total,
    estimate_tokens,
)

# --- estimate_tokens ---------------------------------------------------------


def test_empty_returns_zero() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("   ") == 0
    assert estimate_tokens("\n\t") == 0


def test_short_input_rounds_up_to_one() -> None:
    # Anything non-empty after strip() returns >= 1 so the operator
    # sees "I sent something" rather than "0 tokens" for a one-char msg.
    assert estimate_tokens("a") == 1
    assert estimate_tokens("hi") == 1
    assert estimate_tokens("test") == 1  # 4 chars // 4 = 1


def test_approximation_is_char_over_four() -> None:
    # 400 chars → ~100 tokens (English-prose rule of thumb).
    text = "a" * 400
    assert estimate_tokens(text) == 100


def test_provider_model_args_accepted_and_ignored() -> None:
    # The signature exists so v2 can plug in real tokenisers; v1 must
    # accept them without crashing.
    assert estimate_tokens("hello world", provider="claude-cli", model="claude-sonnet-4-6") == 2


# --- estimate_action_total ---------------------------------------------------


def test_action_total_handles_dict_shape() -> None:
    action = {
        "transcript": [
            {"role": "user", "content": "a" * 400},  # ~100 tokens
            {"role": "assistant", "content": "b" * 800},  # ~200 tokens
        ],
    }
    assert estimate_action_total(action) == 300


def test_action_total_includes_system_prompt() -> None:
    action = {"transcript": [{"role": "user", "content": "a" * 400}]}
    assert estimate_action_total(action, system_prompt="x" * 200) == 50 + 100


def test_action_total_handles_missing_transcript() -> None:
    assert estimate_action_total({}) == 0
    assert estimate_action_total({"transcript": None}) == 0
    assert estimate_action_total(None) == 0


def test_action_total_skips_empty_entries() -> None:
    action = {
        "transcript": [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "a" * 400},
        ],
    }
    assert estimate_action_total(action) == 100


def test_action_total_reads_tool_call_input_dict() -> None:
    # PR 3 transcript-entry kinds: tool_call.input is a dict; the
    # estimator flattens it to a readable string so its size counts.
    action = {
        "transcript": [
            {"role": "user", "content": "x" * 40},  # 10
            {
                "role": "tool_call",
                "tool": "Bash",
                "input": {"command": "y" * 96},  # flattens to "command=yyy..." ≈ 104 chars → 26
            },
        ],
    }
    total = estimate_action_total(action)
    # Range check rather than exact — the dict-flatten format may
    # tweak future revisions; we just want to confirm the entry counted.
    assert total > 10


# --- context_window ----------------------------------------------------------


def test_known_model_returns_window() -> None:
    assert context_window("claude-cli", "claude-sonnet-4-6") == 200_000
    assert context_window("gemini-cli", "gemini-2.5-pro") == 1_000_000


def test_unknown_pair_returns_none() -> None:
    assert context_window("claude-cli", "claude-sonnet-9-9") is None
    assert context_window("unknown-provider", "any-model") is None


def test_empty_args_return_none() -> None:
    assert context_window("", "claude-sonnet-4-6") is None
    assert context_window("claude-cli", "") is None
    assert context_window("", "") is None


def test_browser_provider_shares_model_names() -> None:
    # Browser mode reuses the underlying model's window; the table
    # must include each browser-targetable model.
    assert context_window("browser", "claude-sonnet-4-6") == 200_000
    assert context_window("browser", "gemini-2.5-pro") == 1_000_000
