"""Seed bundled archetype cards.

Loads the templates from packs/archetypes/ and creates one card per
template.  Idempotent: skips templates that already have a matching
archetype in the store.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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
_CODE_EDIT_ARCHETYPE = "code-edit"
_CODE_EDIT_LEGACY_NAME = "Code Edit"
_CODE_EDIT_NEW_NAME = "Code Planning Assistant"
_CODE_EDIT_LEGACY_TEMPLATE_PREFIX = "You are a Code Edit agent"


# Archetype-specific defaults.  Cards bind a template to a provider, model,
# budget, and policy.
_CARD_DEFAULTS: dict[str, dict[str, Any]] = {
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
        name="Code Planning Assistant",
        description=(
            "Chat assistant for planning code changes, reviewing tradeoffs, "
            "and drafting implementation steps. It does not apply edits "
            "or run a tool loop from this card today."
        ),
        provider="claude-cli",
        model="claude-sonnet-4-6",
        # Forced to chat for now: agentic dispatch through the
        # Claude Code CLI's own tool loop is V5 work and surfaces a
        # clear deferred-feature error today.  Switching to chat
        # mode keeps the card usable as a "describe the change you
        # want" assistant until the agentic CLI bridge lands.
        mode=CardMode.CHAT,
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
    "ui-architect": dict(
        name="UI Architect",
        description="Read-only PySide6 UI hierarchy mapper with Mermaid output.",
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
        stale_minutes=45,
    ),
    "logic-liaison": dict(
        name="Logic Liaison",
        description="Read-only PySide6 signal/thread boundary mapper with Mermaid output.",
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
        stale_minutes=45,
    ),
}


# Extra Gemini-CLI variants of the chat archetypes so multi-vendor
# parallelism works out of the box (Claude + Gemini side-by-side via
# their respective CLIs, no API keys).  These reuse the same template
# as the primary card; only the card-level provider/model differ.
_GEMINI_VARIANTS: dict[str, dict[str, Any]] = {
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
    created: list[PersonalityCard] = []
    for path in sorted(PACK_PATH.glob("*.md")):
        template = load_template(path)
        defaults = _CARD_DEFAULTS.get(template.archetype)
        if not defaults:
            log.warning("no card defaults for archetype %s", template.archetype)
            continue

        # Resolve the template_id we'll attach cards to.  Reuse the
        # existing template row when present; otherwise insert the new
        # bundled template now.
        existing_template = await store.get_template_by_archetype(template.archetype)
        if existing_template is None:
            await store.insert_template(template)
            template_id = template.id
        else:
            template_id = existing_template.id
            if template.archetype == _CODE_EDIT_ARCHETYPE and (
                existing_template.name == _CODE_EDIT_LEGACY_NAME
                or existing_template.body.startswith(_CODE_EDIT_LEGACY_TEMPLATE_PREFIX)
                or existing_template.name != template.name
                or existing_template.body != template.body
                or existing_template.variables != template.variables
                or existing_template.content_hash != template.content_hash
            ):
                existing_template.name = template.name
                existing_template.body = template.body
                existing_template.variables = list(template.variables)
                existing_template.content_hash = template.content_hash
                await store.update_template(existing_template)

        # Primary card — keyed by archetype for backwards-compatible
        # idempotency.
        existing_card = next(
            (
                c
                for c in existing_cards
                if c.archetype == template.archetype and c.name == defaults["name"]
            ),
            None,
        )
        if template.archetype == _CODE_EDIT_ARCHETYPE and existing_card is None:
            existing_card = next(
                (
                    c
                    for c in existing_cards
                    if c.archetype == template.archetype and c.name == _CODE_EDIT_LEGACY_NAME
                ),
                None,
            )

        if existing_card is None:
            card = PersonalityCard(
                archetype=template.archetype,
                template_id=template_id,
                **defaults,
            )
            await store.insert_card(card)
            created.append(card)
            existing_names.add(card.name)
            log.info("seeded card: %s", template.archetype)
        elif template.archetype == _CODE_EDIT_ARCHETYPE and (
            existing_card.name != defaults["name"]
            or existing_card.description != defaults["description"]
            or existing_card.provider != defaults["provider"]
            or existing_card.model != defaults["model"]
            or existing_card.mode != defaults["mode"]
            or existing_card.template_id != template_id
            or existing_card.cost != defaults["cost"]
            or existing_card.blast_radius != defaults["blast_radius"]
            or existing_card.sandbox_tier != defaults["sandbox_tier"]
            or existing_card.stale_minutes != defaults["stale_minutes"]
            or existing_card.max_turns != defaults["max_turns"]
        ):
            existing_card.name = defaults["name"]
            existing_card.description = defaults["description"]
            existing_card.template_id = template_id
            existing_card.provider = defaults["provider"]
            existing_card.model = defaults["model"]
            existing_card.mode = defaults["mode"]
            existing_card.cost = defaults["cost"]
            existing_card.blast_radius = defaults["blast_radius"]
            existing_card.sandbox_tier = defaults["sandbox_tier"]
            existing_card.stale_minutes = defaults["stale_minutes"]
            existing_card.max_turns = defaults.get("max_turns", existing_card.max_turns)
            await store.update_card(existing_card)
            existing_names.discard(_CODE_EDIT_LEGACY_NAME)
            existing_names.add(existing_card.name)

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
