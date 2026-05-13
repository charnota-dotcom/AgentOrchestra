"""CodexCLIProvider - registration + healthcheck + basic guardrails."""

from __future__ import annotations

import pytest

from apps.service.providers import codex_cli
from apps.service.providers.registry import get_provider, known_providers
from apps.service.types import (
    BlastRadiusPolicy,
    CardMode,
    CostPolicy,
    PersonalityCard,
    ProviderError,
    SandboxTier,
)


def test_registry_includes_codex_cli() -> None:
    assert "codex-cli" in known_providers()
    provider = get_provider("codex-cli")
    assert provider.name == "codex-cli"


def test_resolve_model_aliases_known_names() -> None:
    assert codex_cli._resolve_model("gpt-5.3-codex") == "gpt-5.3-codex"
    assert codex_cli._resolve_model("gpt-5.2-codex") == "gpt-5.2-codex"
    assert codex_cli._resolve_model("gpt-5-codex") == "gpt-5-codex"
    assert codex_cli._resolve_model("codex-mini-latest") == "codex-mini-latest"


def test_resolve_model_pass_through_for_unknown() -> None:
    assert codex_cli._resolve_model("custom-openai-model") == "custom-openai-model"
    assert codex_cli._resolve_model("") is None


@pytest.mark.asyncio
async def test_healthcheck_uses_auth_probe(monkeypatch) -> None:
    seen: list[float] = []

    async def _probe(*, timeout: float = 20.0) -> tuple[bool, str]:
        seen.append(timeout)
        return True, "ok"

    monkeypatch.setattr(codex_cli, "_probe_codex_auth", _probe)
    p = codex_cli.CodexCLIProvider()
    assert (await p.healthcheck()) is True

    async def _probe_fail(*, timeout: float = 20.0) -> tuple[bool, str]:
        seen.append(timeout)
        return False, "not signed in"

    monkeypatch.setattr(codex_cli, "_probe_codex_auth", _probe_fail)
    assert (await p.healthcheck()) is False
    assert seen == [8.0, 8.0]


def _make_card(provider: str = "codex-cli") -> PersonalityCard:
    return PersonalityCard(
        name="Demo",
        archetype="demo",
        description="d",
        template_id="t",
        provider=provider,
        model="gpt-5.3-codex",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )


@pytest.mark.asyncio
async def test_open_chat_raises_when_binary_missing(monkeypatch) -> None:
    monkeypatch.setattr(codex_cli, "_codex_binary", lambda: None)
    p = codex_cli.CodexCLIProvider()
    with pytest.raises(ProviderError, match="codex"):
        await p.open_chat(_make_card())


@pytest.mark.asyncio
async def test_open_chat_rejects_wrong_provider_card(monkeypatch) -> None:
    monkeypatch.setattr(codex_cli, "_codex_binary", lambda: "/usr/local/bin/codex")
    p = codex_cli.CodexCLIProvider()
    with pytest.raises(ProviderError, match="not codex-cli"):
        await p.open_chat(_make_card(provider="openai"))
