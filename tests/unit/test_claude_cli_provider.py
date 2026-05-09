"""ClaudeCLIProvider — registration + healthcheck + missing-binary error path."""

from __future__ import annotations

import pytest

from apps.service.providers import claude_cli
from apps.service.providers.registry import get_provider, known_providers
from apps.service.types import (
    BlastRadiusPolicy,
    CardMode,
    CostPolicy,
    PersonalityCard,
    ProviderError,
    SandboxTier,
)


def test_registry_includes_claude_cli() -> None:
    assert "claude-cli" in known_providers()
    provider = get_provider("claude-cli")
    assert provider.name == "claude-cli"


def test_resolve_model_aliases_known_names() -> None:
    assert claude_cli._resolve_model("claude-sonnet-4-5") == "sonnet"
    assert claude_cli._resolve_model("claude-haiku-4-5") == "haiku"
    assert claude_cli._resolve_model("claude-opus-4-7") == "opus"


def test_resolve_model_pass_through_for_unknown() -> None:
    assert claude_cli._resolve_model("custom-tuning-foo") == "custom-tuning-foo"
    assert claude_cli._resolve_model("sonnet") == "sonnet"
    assert claude_cli._resolve_model("") is None


@pytest.mark.asyncio
async def test_healthcheck_reflects_binary_presence(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "_claude_binary", lambda: None)
    p = claude_cli.ClaudeCLIProvider()
    assert (await p.healthcheck()) is False

    monkeypatch.setattr(claude_cli, "_claude_binary", lambda: "/usr/local/bin/claude")
    assert (await p.healthcheck()) is True


def _make_card(provider: str = "claude-cli") -> PersonalityCard:
    return PersonalityCard(
        name="Demo",
        archetype="demo",
        description="d",
        template_id="t",
        provider=provider,
        model="claude-sonnet-4-5",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )


@pytest.mark.asyncio
async def test_open_chat_raises_when_binary_missing(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "_claude_binary", lambda: None)
    p = claude_cli.ClaudeCLIProvider()
    with pytest.raises(ProviderError, match="claude"):
        await p.open_chat(_make_card())


@pytest.mark.asyncio
async def test_open_chat_rejects_wrong_provider_card(monkeypatch) -> None:
    # Even with the binary present, a card targeting a different
    # provider must not be served by this adapter.
    monkeypatch.setattr(claude_cli, "_claude_binary", lambda: "/usr/local/bin/claude")
    p = claude_cli.ClaudeCLIProvider()
    with pytest.raises(ProviderError, match="not claude-cli"):
        await p.open_chat(_make_card(provider="anthropic"))


@pytest.mark.asyncio
async def test_run_with_tools_is_deferred() -> None:
    p = claude_cli.ClaudeCLIProvider()

    class _Stub:
        def tools(self):
            return []

        async def execute(self, *_a, **_kw):
            raise AssertionError("should not be called")

    stream = p.run_with_tools(
        _make_card(),
        system=None,
        user_message="hi",
        executor=_Stub(),
    )
    events = [ev async for ev in stream]
    assert len(events) == 1
    assert events[0].kind == "error"
    assert "agentic" in events[0].text


def test_render_prompt_single_turn(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "_claude_binary", lambda: "/usr/local/bin/claude")
    s = claude_cli.ClaudeCLIChatSession(_make_card())
    s._history = [{"role": "user", "content": "hello"}]
    assert s._render_prompt() == "hello"


def test_render_prompt_multi_turn_inlines_history(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli, "_claude_binary", lambda: "/usr/local/bin/claude")
    s = claude_cli.ClaudeCLIChatSession(_make_card(), system="be terse")
    s._history = [
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": "pong"},
        {"role": "user", "content": "again?"},
    ]
    rendered = s._render_prompt()
    assert "System: be terse" in rendered
    assert "User: ping" in rendered
    assert "Assistant: pong" in rendered
    assert rendered.endswith("User: again?")
