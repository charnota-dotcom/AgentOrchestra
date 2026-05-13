from __future__ import annotations

import pytest

from apps.service.cards.seed import seed_default_cards
from apps.service.types import BlastRadiusPolicy, CardMode, CostPolicy, InstructionTemplate, PersonalityCard, SandboxTier, long_id


@pytest.mark.asyncio
async def test_seed_includes_phase2_mapper_archetypes(store) -> None:
    await seed_default_cards(store)
    cards = await store.list_cards()
    names = {c.name for c in cards}
    archetypes = {c.archetype for c in cards}
    assert "UI Architect" in names
    assert "Logic Liaison" in names
    assert "ui-architect" in archetypes
    assert "logic-liaison" in archetypes


@pytest.mark.asyncio
async def test_seed_is_idempotent_for_phase2_mapper_archetypes(store) -> None:
    await seed_default_cards(store)
    first = await store.list_cards()
    first_count = len(first)

    await seed_default_cards(store)
    second = await store.list_cards()
    second_count = len(second)
    assert second_count == first_count


@pytest.mark.asyncio
async def test_code_edit_card_is_truthful_chat_assistant(store) -> None:
    await seed_default_cards(store)
    cards = await store.list_cards()
    card = next(c for c in cards if c.archetype == "code-edit")
    assert card.name == "Code Planning Assistant"
    assert card.mode.value == "chat"
    assert "does not apply edits" in card.description.lower()


@pytest.mark.asyncio
async def test_seed_updates_legacy_code_edit_rows_in_place(store) -> None:
    legacy_template = InstructionTemplate(
        id=long_id(),
        name="Code Edit",
        archetype="code-edit",
        body="You are a Code Edit agent.\n\nUse write_file to edit code.",
        variables=[],
        version=1,
        content_hash="legacy",
    )
    await store.insert_template(legacy_template)
    legacy_card = PersonalityCard(
        name="Code Edit",
        archetype="code-edit",
        description="Legacy editing assistant",
        template_id=legacy_template.id,
        provider="claude-cli",
        model="claude-sonnet-4-5",
        mode=CardMode.AGENTIC,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )
    await store.insert_card(legacy_card)

    await seed_default_cards(store)

    card = await store.get_card_by_archetype("code-edit")
    template = await store.get_template_by_archetype("code-edit")
    assert card is not None
    assert template is not None
    assert card.name == "Code Planning Assistant"
    assert card.mode.value == "chat"
    assert card.description.startswith("Chat assistant for planning code changes")
    assert template.name == "Code Planning Assistant"
    assert "write_file" not in template.body
