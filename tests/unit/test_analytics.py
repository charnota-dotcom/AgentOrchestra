from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.service.main import Handlers
from apps.service.types import (
    Artifact,
    ArtifactKind,
    BlastRadiusPolicy,
    CardMode,
    CostPolicy,
    Instruction,
    InstructionTemplate,
    PersonalityCard,
    Run,
    RunState,
    SandboxTier,
    Step,
    StepKind,
)


@pytest.mark.asyncio
async def test_analytics_summary_aggregates_metrics(store) -> None:
    template = InstructionTemplate(
        id="t-analytics",
        name="Analytics",
        archetype="demo",
        body="body",
        variables=[],
        version=1,
        content_hash="hash",
    )
    await store.insert_template(template)
    card = PersonalityCard(
        id="c-analytics",
        name="Analytics Card",
        archetype="demo",
        description="d",
        template_id=template.id,
        provider="claude-cli",
        model="claude-sonnet-4-5",
        mode=CardMode.CHAT,
        cost=CostPolicy(),
        blast_radius=BlastRadiusPolicy(),
        sandbox_tier=SandboxTier.DEVCONTAINER,
    )
    await store.insert_card(card)
    ins = Instruction(
        id="i-analytics",
        template_id=template.id,
        template_version=1,
        card_id=card.id,
        rendered_text="hello",
        variables={},
    )
    await store.insert_instruction(ins)

    run1 = Run(card_id=card.id, instruction_id=ins.id, state=RunState.REVIEWING, cost_tokens=100)
    run1.cost_usd = 1.0
    run1.last_plan_turn = 1
    await store.insert_run(run1)
    run2 = Run(card_id=card.id, instruction_id=ins.id, state=RunState.ABORTED, cost_tokens=50)
    run2.cost_usd = 0.4
    await store.insert_run(run2)

    await store.insert_step(
        Step(
            run_id=run1.id,
            seq=1,
            kind=StepKind.TOOL_CALL,
            payload={"name": "read_file", "is_error": False},
        )
    )
    await store.insert_step(
        Step(
            run_id=run2.id,
            seq=1,
            kind=StepKind.TOOL_CALL,
            payload={"name": "write_file", "content": {"error": "boom"}},
        )
    )
    await store.insert_artifact(
        Artifact(
            run_id=run1.id,
            kind=ArtifactKind.DIFF,
            title="d",
            body="abcd",
        )
    )

    summary = await store.analytics_summary(days=7)
    assert summary["kpis"]["run_count"] >= 2
    assert summary["kpis"]["success_count"] >= 1
    assert summary["kpis"]["hallucination_rate"] > 0
    assert summary["kpis"]["token_efficiency"] > 0
    assert isinstance(summary["trend"], list)
    assert len(summary["runs"]) >= 2
    first = summary["runs"][0]
    assert "is_hallucination" in first
    assert "plan_latency" in first


@pytest.mark.asyncio
async def test_analytics_leaderboard_groups(store) -> None:
    leaderboard = await store.analytics_leaderboard(days=7, group_by="provider", min_samples=1)
    assert leaderboard["group_by"] == "provider"
    assert "rows" in leaderboard


@pytest.mark.asyncio
async def test_handlers_analytics_rpcs(store, tmp_path) -> None:
    h = Handlers(
        store=store,
        manager=SimpleNamespace(),  # type: ignore[arg-type]
        dispatcher=SimpleNamespace(),  # type: ignore[arg-type]
        data_dir=tmp_path,
    )
    summary = await h.analytics_summary({"days": 7})
    leaderboard = await h.analytics_leaderboard({"days": 7, "group_by": "card"})
    assert "kpis" in summary
    assert leaderboard["group_by"] == "card"
