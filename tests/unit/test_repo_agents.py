"""Tests for repo-aware Agent functionality."""

from __future__ import annotations

import pytest

from apps.service.store.events import EventStore
from apps.service.types import Agent, Workspace


@pytest.mark.asyncio
async def test_agent_persists_workspace_id(store: EventStore) -> None:
    ws = Workspace(name="proj", repo_path="/tmp/proj")
    await store.insert_workspace(ws)
    agent = Agent(
        name="Repo agent",
        provider="claude-cli",
        model="sonnet",
        workspace_id=ws.id,
    )
    await store.insert_agent(agent)

    fetched = await store.get_agent(agent.id)
    assert fetched is not None
    assert fetched.workspace_id == ws.id


@pytest.mark.asyncio
async def test_agent_workspace_can_be_unset(store: EventStore) -> None:
    ws = Workspace(name="proj", repo_path="/tmp/proj")
    await store.insert_workspace(ws)
    agent = Agent(
        name="A",
        provider="claude-cli",
        model="sonnet",
        workspace_id=ws.id,
    )
    await store.insert_agent(agent)
    agent.workspace_id = None
    await store.update_agent(agent)
    fetched = await store.get_agent(agent.id)
    assert fetched is not None
    assert fetched.workspace_id is None


@pytest.mark.asyncio
async def test_agent_workspace_default_is_none(store: EventStore) -> None:
    agent = Agent(name="Plain", provider="claude-cli", model="sonnet")
    await store.insert_agent(agent)
    fetched = await store.get_agent(agent.id)
    assert fetched is not None
    assert fetched.workspace_id is None


def test_claude_cli_session_passes_cwd_through_provider(monkeypatch) -> None:
    """ClaudeCLIChatSession stashes the cwd so create_subprocess_exec sees it."""
    from apps.service.providers import claude_cli
    from apps.service.types import (
        BlastRadiusPolicy,
        CardMode,
        CostPolicy,
        PersonalityCard,
        SandboxTier,
    )

    monkeypatch.setattr(claude_cli, "_claude_binary", lambda: "/usr/bin/claude")
    card = PersonalityCard(
        name="x",
        archetype="agent",
        description="",
        template_id="agent",
        provider="claude-cli",
        model="sonnet",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )
    sess = claude_cli.ClaudeCLIChatSession(card, system="hi", cwd="/tmp/foo")
    assert sess.cwd == "/tmp/foo"


def test_gemini_cli_session_passes_cwd_through_provider(monkeypatch) -> None:
    from apps.service.providers import gemini_cli
    from apps.service.types import (
        BlastRadiusPolicy,
        CardMode,
        CostPolicy,
        PersonalityCard,
        SandboxTier,
    )

    monkeypatch.setattr(gemini_cli, "_gemini_binary", lambda: "/usr/bin/gemini")
    card = PersonalityCard(
        name="x",
        archetype="agent",
        description="",
        template_id="agent",
        provider="gemini-cli",
        model="gemini-2.5-pro",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )
    sess = gemini_cli.GeminiCLIChatSession(card, system=None, cwd="/tmp/bar")
    assert sess.cwd == "/tmp/bar"
