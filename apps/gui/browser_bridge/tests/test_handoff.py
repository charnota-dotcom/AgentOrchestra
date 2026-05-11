"""Tests for the three client-side handoff format renderers.

The service has its own copy of the formatter at
``apps/service/main.py:_format_drone_handoff``; the two
implementations should produce byte-identical output for any given
input.  These tests pin the GUI side; the service-side tests cover
the parity.
"""

from __future__ import annotations

from apps.gui.browser_bridge import render_handoff


def _sample_transcript() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "How should I structure the auth flow?"},
        {
            "role": "assistant",
            "content": "OAuth 2.0 with PKCE for the public client is a fine starting point.",
        },
        {"role": "user", "content": "What about session timeout?"},
    ]


# --- continuation -----------------------------------------------------------


def test_continuation_includes_persona_skills_and_transcript() -> None:
    out = render_handoff(
        "continuation",
        persona="You speak in haiku.",
        role="worker",
        skills=["/research-deep", "/cite-sources"],
        transcript=_sample_transcript(),
    )
    assert "Pick up from the last user message" in out
    assert "You speak in haiku." in out
    assert "/research-deep /cite-sources" in out
    assert "How should I structure the auth flow?" in out
    assert "OAuth 2.0" in out
    assert "session timeout" in out


def test_continuation_with_empty_transcript_omits_history_block() -> None:
    out = render_handoff(
        "continuation",
        persona="You are friendly.",
        role="supervisor",
        skills=[],
        transcript=[],
    )
    assert "Conversation so far" not in out
    assert "End of prior conversation" not in out


def test_continuation_with_no_persona_or_skills_still_works() -> None:
    out = render_handoff(
        "continuation",
        persona="",
        role="worker",
        skills=[],
        transcript=_sample_transcript(),
    )
    # Role line still present, transcript present, no missing-input crashes.
    assert "Worker" in out
    assert "session timeout" in out


# --- fork -------------------------------------------------------------------


def test_fork_includes_role_persona_skills_no_transcript() -> None:
    out = render_handoff(
        "fork",
        persona="You speak in haiku.",
        role="worker",
        skills=["/research-deep"],
        transcript=_sample_transcript(),
    )
    assert "worker drone" in out
    assert "You speak in haiku." in out
    assert "/research-deep" in out
    # No prior turns should leak through.
    assert "session timeout" not in out
    assert "OAuth 2.0" not in out


def test_fork_with_no_persona_still_emits_role_intro() -> None:
    out = render_handoff(
        "fork",
        persona="",
        role="auditor",
        skills=[],
        transcript=[],
    )
    assert "auditor drone" in out


# --- plain ------------------------------------------------------------------


def test_plain_emits_user_assistant_turns_only() -> None:
    out = render_handoff(
        "plain",
        persona="should be ignored",
        role="should also be ignored",
        skills=["should-not-appear"],
        transcript=_sample_transcript(),
    )
    assert "should be ignored" not in out
    assert "should-not-appear" not in out
    assert "User:" in out
    assert "Assistant:" in out
    assert "session timeout" in out


def test_plain_with_empty_transcript_returns_empty_block() -> None:
    out = render_handoff(
        "plain",
        persona="",
        role="worker",
        skills=[],
        transcript=[],
    )
    assert out.strip() == ""


# --- output is always newline-terminated ------------------------------------


def test_all_formats_end_with_newline() -> None:
    for kind in ("continuation", "fork", "plain"):
        out = render_handoff(
            kind,
            persona="x",
            role="worker",
            skills=["/y"],
            transcript=_sample_transcript(),
        )
        assert out.endswith("\n")
