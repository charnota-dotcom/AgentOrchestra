"""Seed bundled archetype cards.

Loads the templates from packs/archetypes/ and creates one card per
template.  Idempotent: skips templates that already have a matching
archetype in the store.
"""

from __future__ import annotations

import logging
from pathlib import Path

from apps.service.store.events import EventStore
from apps.service.templates.engine import load_template
from apps.service.types import (
    BlastRadiusPolicy,
    CostPolicy,
    PersonalityCard,
    SandboxTier,
)

log = logging.getLogger(__name__)


PACK_PATH = Path(__file__).resolve().parents[2].parent / "packs" / "archetypes"


# Archetype-specific defaults.  Cards bind a template to a provider, model,
# budget, and policy.
_CARD_DEFAULTS: dict[str, dict] = {
    "broad-research": dict(
        name="Broad Research",
        description="Wide-net research with indexed findings.",
        provider="anthropic",
        model="claude-sonnet-4-5",
        cost=CostPolicy(soft_cap_usd=0.50, hard_cap_usd=2.00,
                        soft_cap_tokens=200_000, hard_cap_tokens=600_000),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=999,            # research can read freely
            network_egress_requires_approval=False,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=45,
    ),
    "narrow-research": dict(
        name="Narrow Research",
        description="Deep dive on one topic with citations.",
        provider="anthropic",
        model="claude-sonnet-4-5",
        cost=CostPolicy(soft_cap_usd=0.40, hard_cap_usd=1.50,
                        soft_cap_tokens=150_000, hard_cap_tokens=500_000),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=999,
            network_egress_requires_approval=False,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=30,
    ),
    "qa-on-fix": dict(
        name="QA on Fix",
        description="Adversarial review of another agent's diff.",
        provider="anthropic",
        model="claude-sonnet-4-5",
        cost=CostPolicy(soft_cap_usd=0.30, hard_cap_usd=1.00,
                        soft_cap_tokens=100_000, hard_cap_tokens=300_000),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=10,
            network_egress_requires_approval=True,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=60,
    ),
}


async def seed_default_cards(store: EventStore) -> list[PersonalityCard]:
    existing = {c.archetype for c in await store.list_cards()}
    created: list[PersonalityCard] = []
    for path in sorted(PACK_PATH.glob("*.md")):
        template = load_template(path)
        if template.archetype in existing:
            continue
        defaults = _CARD_DEFAULTS.get(template.archetype)
        if not defaults:
            log.warning("no card defaults for archetype %s", template.archetype)
            continue
        await store.insert_template(template)
        card = PersonalityCard(
            archetype=template.archetype,
            template_id=template.id,
            **defaults,
        )
        await store.insert_card(card)
        created.append(card)
        log.info("seeded card: %s", template.archetype)
    return created
