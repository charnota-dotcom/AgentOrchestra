"""Cross-vendor consensus runner.

Takes a single question and a list of (provider, model) candidates,
runs each in parallel as an isolated chat session, then asks a judge
model to synthesise a single answer.  Each candidate's transcript is
saved as an Artifact on the consensus Run; the judge's synthesis is
the final TRANSCRIPT artifact.

This is intentionally a separate function rather than another branch
in the dispatcher because the orchestration is fan-out + fan-in
specific.  It's invoked from the runs.consensus RPC which lives in
the service entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from apps.service.cost.meter import cost_for_call
from apps.service.dispatch.bus import EventBus
from apps.service.providers.registry import get_provider
from apps.service.store.events import EventStore
from apps.service.types import (
    Artifact,
    ArtifactKind,
    CardMode,
    CostPolicy,
    Event,
    EventKind,
    EventSource,
    Instruction,
    PersonalityCard,
    Run,
    RunState,
    long_id,
)

log = logging.getLogger(__name__)


@dataclass
class ConsensusCandidate:
    provider: str
    model: str
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class ConsensusResult:
    run_id: str
    candidates: list[ConsensusCandidate] = field(default_factory=list)
    judge_text: str = ""
    judge_tokens_in: int = 0
    judge_tokens_out: int = 0
    cost_usd: float = 0.0


async def run_consensus(
    store: EventStore,
    bus: EventBus,
    *,
    question: str,
    judge_provider: str,
    judge_model: str,
    candidates: list[tuple[str, str]],
    judge_instructions: str | None = None,
    consensus_card_id: str,
    consensus_template_id: str,
) -> ConsensusResult:
    """Fan out the question, then synthesise.

    Returns a ConsensusResult and persists everything as a Run in
    REVIEWING state.  Caller (the RPC handler) is responsible for
    surfacing the result to the GUI.
    """

    # Persist a stub Instruction so the Run has a valid FK target.
    rendered = (
        f"Cross-vendor consensus question:\n\n{question}\n\n"
        f"Judge instructions: {judge_instructions or '(default)'}"
    )
    instruction = Instruction(
        id=long_id(),
        template_id=consensus_template_id,
        template_version=1,
        card_id=consensus_card_id,
        rendered_text=rendered,
        variables={"question": question, "judge": f"{judge_provider}/{judge_model}"},
    )
    await store.insert_instruction(instruction)

    run = Run(
        workspace_id="",
        card_id=consensus_card_id,
        instruction_id=instruction.id,
        state=RunState.QUEUED,
    )
    await store.insert_run(run)
    await store.append_event(
        Event(
            source=EventSource.DISPATCH_RUN,
            kind=EventKind.RUN_STARTED,
            run_id=run.id,
            payload={"mode": "consensus", "candidates": candidates},
            text=f"consensus run: {len(candidates)} candidates",
        )
    )

    # Promote through PLANNING -> EXECUTING (simulated transitions).
    await _transition(store, run, RunState.PLANNING)
    await _transition(store, run, RunState.EXECUTING)

    # Fan out.
    cands: list[ConsensusCandidate] = await asyncio.gather(
        *(_run_candidate(store, run, q, p, m) for p, m in candidates for q in [question]),
        return_exceptions=False,
    )

    # Build the judge prompt.
    candidate_block = "\n\n".join(
        f"## Candidate #{i + 1} — {c.provider}/{c.model}\n\n" + (c.text or f"(error: {c.error})")
        for i, c in enumerate(cands)
    )
    judge_prompt = (
        f"{rendered}\n\n## Candidate answers\n\n{candidate_block}\n\n"
        "Now synthesise per the instructions above."
    )

    judge_card = PersonalityCard(
        name="Consensus Judge",
        archetype="consensus",
        description="Judge for cross-vendor consensus.",
        template_id=consensus_template_id,
        provider=judge_provider,
        model=judge_model,
        mode=CardMode.CHAT,
        cost=CostPolicy(),
    )
    judge_text = ""
    judge_in = judge_out = 0
    try:
        provider = get_provider(judge_provider)
        session = await provider.open_chat(judge_card, system="You are the judge.")
        try:
            async for ev in session.send(judge_prompt):
                if ev.kind == "text_delta":
                    judge_text += ev.text
                elif ev.kind == "usage":
                    judge_in = int(ev.payload.get("input_tokens") or 0)
                    judge_out = int(ev.payload.get("output_tokens") or 0)
                elif ev.kind == "error":
                    raise RuntimeError(ev.text)
                elif ev.kind == "finish":
                    break
        finally:
            await session.close()
    except Exception as exc:
        judge_text = f"(judge failed: {exc})"

    # Persist artifacts.
    for i, c in enumerate(cands):
        await store.insert_artifact(
            Artifact(
                id=long_id(),
                run_id=run.id,
                kind=ArtifactKind.TRANSCRIPT,
                title=f"Candidate #{i + 1} — {c.provider}/{c.model}",
                body=c.text or f"(error: {c.error})",
            )
        )

    await store.insert_artifact(
        Artifact(
            id=long_id(),
            run_id=run.id,
            kind=ArtifactKind.SUMMARY,
            title=f"Consensus synthesis ({judge_provider}/{judge_model})",
            body=judge_text,
        )
    )

    cost = sum(
        cost_for_call(c.provider, c.model, c.tokens_in, c.tokens_out) for c in cands
    ) + cost_for_call(judge_provider, judge_model, judge_in, judge_out)
    await store.db.execute(
        "UPDATE runs SET cost_usd = ?, cost_tokens = ? WHERE id = ?",
        (cost, judge_in + judge_out + sum(c.tokens_in + c.tokens_out for c in cands), run.id),
    )
    await store.db.commit()

    await _transition(store, run, RunState.REVIEWING)
    await store.append_event(
        Event(
            source=EventSource.DISPATCH_RUN,
            kind=EventKind.RUN_STATE_CHANGED,
            run_id=run.id,
            payload={"state": "reviewing", "cost_usd": cost, "candidates": len(cands)},
            text=f"consensus ready: ${cost:.4f}, {len(cands)} candidates",
        )
    )

    return ConsensusResult(
        run_id=run.id,
        candidates=cands,
        judge_text=judge_text,
        judge_tokens_in=judge_in,
        judge_tokens_out=judge_out,
        cost_usd=cost,
    )


async def _run_candidate(
    store: EventStore,
    parent: Run,
    question: str,
    provider_name: str,
    model: str,
) -> ConsensusCandidate:
    cand = ConsensusCandidate(provider=provider_name, model=model)
    t0 = time.monotonic()
    try:
        provider = get_provider(provider_name)
        card = PersonalityCard(
            name=f"{provider_name}/{model}",
            archetype="consensus-candidate",
            description="",
            template_id="",  # placeholder; we don't validate this for ad-hoc candidates
            provider=provider_name,
            model=model,
            mode=CardMode.CHAT,
            cost=CostPolicy(),
        )
        session = await provider.open_chat(card)
        try:
            async for ev in session.send(question):
                if ev.kind == "text_delta":
                    cand.text += ev.text
                elif ev.kind == "usage":
                    cand.tokens_in = int(ev.payload.get("input_tokens") or 0)
                    cand.tokens_out = int(ev.payload.get("output_tokens") or 0)
                elif ev.kind == "error":
                    raise RuntimeError(ev.text)
                elif ev.kind == "finish":
                    break
        finally:
            await session.close()
    except Exception as exc:
        cand.error = str(exc)
        log.warning("candidate %s/%s failed: %s", provider_name, model, exc)
    cand.duration_s = time.monotonic() - t0
    return cand


async def _transition(store: EventStore, run: Run, to: RunState) -> None:
    run.state = to
    await store.update_run_state(run.id, to)
    await store.append_event(
        Event(
            source=EventSource.SYSTEM,
            kind=EventKind.RUN_STATE_CHANGED,
            run_id=run.id,
            payload={"to": to.value},
            text=to.value,
        )
    )
