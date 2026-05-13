from __future__ import annotations

from pathlib import Path


def test_start_cmd_includes_codex_probe_and_verdict_gate() -> None:
    text = Path("scripts/start.cmd").read_text(encoding="utf-8", errors="replace")
    assert "where codex" in text
    assert "set CODEX_OK=0" in text
    assert "codex exec" in text
    assert "set CODEX_PROBE_RC" in text
    assert "if \"!CLAUDE_OK!\"==\"0\" if \"!GEMINI_OK!\"==\"0\" if \"!CODEX_OK!\"==\"0\"" in text


def test_first_run_subscriptions_mentions_codex_and_detection() -> None:
    text = Path("apps/gui/windows/first_run.py").read_text(encoding="utf-8", errors="replace")
    assert "Codex CLI" in text
    assert "_which('codex')" in text
