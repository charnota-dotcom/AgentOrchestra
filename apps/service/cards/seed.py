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
    CardMode,
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
        # Defaults to claude-cli so a Max-plan user gets working cards
        # with no API key.  Override per-card to "anthropic" + key for
        # heavy programmatic use.
        provider="claude-cli",
        model="claude-sonnet-4-5",
        cost=CostPolicy(
            soft_cap_usd=0.50, hard_cap_usd=2.00, soft_cap_tokens=200_000, hard_cap_tokens=600_000
        ),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=999,  # research can read freely
            network_egress_requires_approval=False,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=45,
    ),
    "narrow-research": dict(
        name="Narrow Research",
        description="Deep dive on one topic with citations.",
        provider="claude-cli",
        model="claude-sonnet-4-5",
        cost=CostPolicy(
            soft_cap_usd=0.40, hard_cap_usd=1.50, soft_cap_tokens=150_000, hard_cap_tokens=500_000
        ),
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
        provider="claude-cli",
        model="claude-sonnet-4-5",
        cost=CostPolicy(
            soft_cap_usd=0.30, hard_cap_usd=1.00, soft_cap_tokens=100_000, hard_cap_tokens=300_000
        ),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=10,
            network_egress_requires_approval=True,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=60,
    ),
    "code-edit": dict(
        name="Code Edit",
        description=(
            "Tool-using agent that edits files in an isolated worktree "
            "branch.  Changes are committed per turn and merged into "
            "the base branch only when you approve."
        ),
        provider="anthropic",
        model="claude-sonnet-4-5",
        mode=CardMode.AGENTIC,
        cost=CostPolicy(
            soft_cap_usd=0.75, hard_cap_usd=3.00, soft_cap_tokens=300_000, hard_cap_tokens=900_000
        ),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=15,
            network_egress_requires_approval=True,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=90,
        max_turns=12,
    ),
    "red-team": dict(
        name="Red Team",
        description="Adversarial reviewer that tries to break a target run's diff.",
        provider="claude-cli",
        model="claude-sonnet-4-5",
        cost=CostPolicy(
            soft_cap_usd=0.40, hard_cap_usd=1.50, soft_cap_tokens=150_000, hard_cap_tokens=400_000
        ),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=999,
            network_egress_requires_approval=False,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=45,
    ),
    "tracker": dict(
        name="Tracker",
        description=(
            "Watcher agent that observes other runs and emits HandoffCards"
            " so the next agent (or human) can pick up the work cleanly."
        ),
        provider="claude-cli",
        model="claude-haiku-4-5",
        cost=CostPolicy(
            soft_cap_usd=0.10, hard_cap_usd=0.50, soft_cap_tokens=50_000, hard_cap_tokens=200_000
        ),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=999,
            network_egress_requires_approval=False,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=20,
    ),
    "consensus": dict(
        name="Cross-vendor Consensus",
        description=(
            "Asks several vendors the same question in parallel and uses a "
            "judge model to synthesise a single answer."
        ),
        # Judge defaults to the CLI; the candidates are picked at
        # dispatch time and can mix providers freely.
        provider="claude-cli",
        model="claude-sonnet-4-5",
        cost=CostPolicy(
            soft_cap_usd=1.50,
            hard_cap_usd=5.00,
            soft_cap_tokens=400_000,
            hard_cap_tokens=1_200_000,
        ),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=999,
            network_egress_requires_approval=False,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=60,
    ),
}


# Extra Gemini-CLI variants of the chat archetypes so multi-vendor
# parallelism works out of the box (Claude + Gemini side-by-side via
# their respective CLIs, no API keys).  These reuse the same template
# as the primary card; only the card-level provider/model differ.
_GEMINI_VARIANTS: dict[str, dict] = {
    "broad-research": dict(
        name="Broad Research (Gemini)",
        description="Wide-net research with indexed findings — via Gemini CLI.",
        provider="gemini-cli",
        model="gemini-2.5-pro",
        cost=CostPolicy(
            soft_cap_usd=0.50, hard_cap_usd=2.00, soft_cap_tokens=200_000, hard_cap_tokens=600_000
        ),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=999,
            network_egress_requires_approval=False,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=45,
    ),
    "narrow-research": dict(
        name="Narrow Research (Gemini)",
        description="Deep dive on one topic with citations — via Gemini CLI.",
        provider="gemini-cli",
        model="gemini-2.5-pro",
        cost=CostPolicy(
            soft_cap_usd=0.40, hard_cap_usd=1.50, soft_cap_tokens=150_000, hard_cap_tokens=500_000
        ),
        sandbox_tier=SandboxTier.DEVCONTAINER,
        blast_radius=BlastRadiusPolicy(
            file_count_threshold=999,
            network_egress_requires_approval=False,
            deletion_requires_approval=True,
            push_requires_approval=True,
        ),
        stale_minutes=30,
    ),
}


async def seed_default_cards(store: EventStore) -> list[PersonalityCard]:
    existing_cards = await store.list_cards()
    existing_names = {c.name for c in existing_cards}
    # Map archetype → template_id of an already-seeded card.  When we
    # add variants to an installed instance the template row already
    # exists in the DB but the InstructionTemplate object loaded from
    # disk has a fresh UUID — using that fresh UUID as the variant's
    # template_id would violate the foreign key.  Reuse the existing
    # template_id instead.
    existing_template_ids: dict[str, str] = {c.archetype: c.template_id for c in existing_cards}
    created: list[PersonalityCard] = []
    for path in sorted(PACK_PATH.glob("*.md")):
        template = load_template(path)
        defaults = _CARD_DEFAULTS.get(template.archetype)
        if not defaults:
            log.warning("no card defaults for archetype %s", template.archetype)
            continue

        # Resolve the template_id we'll attach cards to.  If we're
        # seeding this archetype for the first time, insert the
        # template now and use the in-memory UUID.  Otherwise pull the
        # ID off any pre-existing card for the same archetype so foreign
        # keys line up.
        if template.archetype in existing_template_ids:
            template_id = existing_template_ids[template.archetype]
        else:
            await store.insert_template(template)
            template_id = template.id

        # Primary card — keyed by archetype for backwards-compatible
        # idempotency.
        if template.archetype not in existing_template_ids:
            card = PersonalityCard(
                archetype=template.archetype,
                template_id=template_id,
                **defaults,
            )
            await store.insert_card(card)
            created.append(card)
            existing_names.add(card.name)
            existing_template_ids[card.archetype] = template_id
            log.info("seeded card: %s", template.archetype)

        # Extra Gemini variants — keyed by name so adding new variants
        # in code seeds them on the next service start without
        # disturbing primary cards.
        variant = _GEMINI_VARIANTS.get(template.archetype)
        if variant and variant["name"] not in existing_names:
            v_card = PersonalityCard(
                archetype=template.archetype,
                template_id=template_id,
                **variant,
            )
            await store.insert_card(v_card)
            created.append(v_card)
            existing_names.add(v_card.name)
            log.info("seeded variant card: %s", v_card.name)
    return created
