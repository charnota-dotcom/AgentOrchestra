"""Pre-flight linter rules."""

from __future__ import annotations

from apps.service.linter.preflight import (
    Severity,
    has_blocking,
    lint,
)


def test_short_text_is_blocked() -> None:
    issues = lint("hi")
    assert any(i.rule == "too-short" for i in issues)
    assert has_blocking(issues)


def test_vague_terms_warn() -> None:
    issues = lint("Please clean up the code and make it better.  We want various improvements.")
    rules = {i.rule for i in issues}
    assert "vague-language" in rules
    assert all(i.severity is not Severity.ERROR for i in issues if i.rule == "vague-language")


def test_secret_pattern_blocks() -> None:
    text = (
        "Use API key sk-ant-api03-DEADBEEFCAFE12345678901234567890ABCDEFGH "
        "to authenticate the agent please."
    )
    issues = lint(text)
    assert any(i.rule == "leaked-secret" for i in issues)
    assert has_blocking(issues)


def test_destructive_warn() -> None:
    text = "Run `rm -rf build/` and continue with the rest of the build script."
    issues = lint(text)
    assert any(i.rule == "destructive-language" for i in issues)


def test_qa_archetype_requires_run_reference() -> None:
    issues = lint(
        "Please review the implementation and ensure it is correct.",
        archetype="qa-on-fix",
    )
    rules = {i.rule for i in issues}
    assert any(r.startswith("archetype:qa-on-fix") for r in rules)


def test_clean_text_passes() -> None:
    text = (
        "Goal: research approaches for desktop multi-agent orchestrators.  "
        "Index findings; cite sources; deliver a top-of-mind summary "
        "with the strongest leads."
    )
    issues = lint(text, archetype="broad-research")
    assert not has_blocking(issues)


def test_ui_architect_requires_target_and_diagram_reference() -> None:
    issues = lint(
        "Please inspect the GUI and summarize structure.",
        archetype="ui-architect",
    )
    rules = {i.rule for i in issues}
    assert any(r.startswith("archetype:ui-architect") for r in rules)


def test_logic_liaison_with_target_and_mermaid_passes() -> None:
    text = (
        "Target path: apps/gui/main.py. Map signal-slot boundaries and "
        "produce a Mermaid diagram of logic flow."
    )
    issues = lint(text, archetype="logic-liaison")
    assert not any(i.rule.startswith("archetype:logic-liaison") for i in issues)
