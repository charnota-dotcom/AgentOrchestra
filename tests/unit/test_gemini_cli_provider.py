"""GeminiCLIProvider — registration + healthcheck + missing-binary error path."""

from __future__ import annotations

import pytest

from apps.service.providers import gemini_cli
from apps.service.providers.registry import get_provider, known_providers
from apps.service.types import (
    BlastRadiusPolicy,
    CardMode,
    CostPolicy,
    PersonalityCard,
    ProviderError,
    SandboxTier,
)


def test_registry_includes_gemini_cli() -> None:
    assert "gemini-cli" in known_providers()
    provider = get_provider("gemini-cli")
    assert provider.name == "gemini-cli"


def test_resolve_model_aliases_known_names() -> None:
    assert gemini_cli._resolve_model("gemini-2.5-pro") == "gemini-2.5-pro"
    assert gemini_cli._resolve_model("gemini-2.5-flash") == "gemini-2.5-flash"
    assert gemini_cli._resolve_model("pro") == "gemini-2.5-pro"
    assert gemini_cli._resolve_model("flash") == "gemini-2.5-flash"


def test_resolve_model_pass_through_for_unknown() -> None:
    assert gemini_cli._resolve_model("custom-tuning-foo") == "custom-tuning-foo"
    assert gemini_cli._resolve_model("gemini-2.0-pro-tuned-x") == "gemini-2.0-pro-tuned-x"
    assert gemini_cli._resolve_model("") is None


def test_strip_cli_noise_drops_known_banners() -> None:
    raw = "Loaded cached credentials.\nUsing model gemini-2.5-pro\nHello world\n"
    assert gemini_cli._strip_cli_noise(raw) == "Hello world"


def test_strip_cli_noise_passes_clean_output_through() -> None:
    assert gemini_cli._strip_cli_noise("just a reply") == "just a reply"


@pytest.mark.asyncio
async def test_healthcheck_reflects_binary_presence(monkeypatch) -> None:
    monkeypatch.setattr(gemini_cli, "_gemini_binary", lambda: None)
    p = gemini_cli.GeminiCLIProvider()
    assert (await p.healthcheck()) is False

    monkeypatch.setattr(gemini_cli, "_gemini_binary", lambda: "/usr/local/bin/gemini")
    assert (await p.healthcheck()) is True


def _make_card(provider: str = "gemini-cli") -> PersonalityCard:
    return PersonalityCard(
        name="Demo",
        archetype="demo",
        description="d",
        template_id="t",
        provider=provider,
        model="gemini-2.5-pro",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )


@pytest.mark.asyncio
async def test_open_chat_raises_when_binary_missing(monkeypatch) -> None:
    monkeypatch.setattr(gemini_cli, "_gemini_binary", lambda: None)
    p = gemini_cli.GeminiCLIProvider()
    with pytest.raises(ProviderError, match="gemini"):
        await p.open_chat(_make_card())


@pytest.mark.asyncio
async def test_open_chat_rejects_wrong_provider_card(monkeypatch) -> None:
    monkeypatch.setattr(gemini_cli, "_gemini_binary", lambda: "/usr/local/bin/gemini")
    p = gemini_cli.GeminiCLIProvider()
    with pytest.raises(ProviderError, match="not gemini-cli"):
        await p.open_chat(_make_card(provider="google"))


@pytest.mark.asyncio
async def test_run_with_tools_is_deferred() -> None:
    p = gemini_cli.GeminiCLIProvider()

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
    monkeypatch.setattr(gemini_cli, "_gemini_binary", lambda: "/usr/local/bin/gemini")
    s = gemini_cli.GeminiCLIChatSession(_make_card())
    s._history = [{"role": "user", "content": "hello"}]
    assert s._render_prompt() == "hello"


def test_render_prompt_single_turn_with_system(monkeypatch) -> None:
    monkeypatch.setattr(gemini_cli, "_gemini_binary", lambda: "/usr/local/bin/gemini")
    s = gemini_cli.GeminiCLIChatSession(_make_card(), system="be terse")
    s._history = [{"role": "user", "content": "hello"}]
    rendered = s._render_prompt()
    assert "System: be terse" in rendered
    assert rendered.endswith("User: hello")


def test_render_prompt_multi_turn_inlines_history(monkeypatch) -> None:
    monkeypatch.setattr(gemini_cli, "_gemini_binary", lambda: "/usr/local/bin/gemini")
    s = gemini_cli.GeminiCLIChatSession(_make_card(), system="be terse")
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
