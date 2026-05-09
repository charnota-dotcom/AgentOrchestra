"""RunDispatcher.

Drives a Run from QUEUED to AWAITING_APPROVAL (the human-review state)
by:

1. Loading the card, instruction, and (optional) workspace.
2. Transitioning the Run state through the canonical state machine.
3. Opening a ChatSession via the provider registry.
4. Sending the rendered instruction; consuming the StreamEvent
   iterator and persisting Step + Event rows.
5. Saving the final assistant text as an Artifact (kind=transcript).
6. Cost-accounting via the cost meter.
7. Optionally creating a worktree (reserved for future archetypes that
   touch files; the V1 archetypes are research/QA and do not).

The dispatcher is fire-and-forget: callers receive the run_id, then
the live UI streams via the EventBus.  Errors are emitted as
``RUN_COMPLETED`` events with state=ABORTED + error text.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from apps.service.cost.meter import cost_for_call
from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.tools import WorktreeToolset, serialize_invocations
from apps.service.providers.registry import get_provider
from apps.service.store.events import EventStore
from apps.service.types import (
    Artifact,
    ArtifactKind,
    Branch,
    CardMode,
    Event,
    EventKind,
    EventSource,
    PersonalityCard,
    Run,
    RunState,
    Step,
    StepKind,
    Workspace,
    assert_run_transition,
    long_id,
    short_id,
    utc_now,
)
from apps.service.worktrees import git_cli as g
from apps.service.worktrees.manager import WorktreeManager

log = logging.getLogger(__name__)


class DispatchError(Exception):
    pass


class RunDispatcher:
    """Stateless service object — one instance shared across all runs."""

    def __init__(
        self,
        store: EventStore,
        manager: WorktreeManager,
        bus: EventBus,
    ) -> None:
        self.store = store
        self.manager = manager
        self.bus = bus
        # Track in-flight runs so the GUI can request cancellation.
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        *,
        workspace_id: str | None,
        card_id: str,
        instruction_id: str,
        rendered_text: str,
        base_branch: str | None = None,
    ) -> Run:
        """Create a Run row and start executing it in the background."""

        card = await self.store.get_card(card_id)
        if not card:
            raise DispatchError(f"unknown card: {card_id}")

        # Agentic cards require a workspace; chat cards don't.
        if card.mode is CardMode.AGENTIC and not workspace_id:
            raise DispatchError(
                "agentic cards require a workspace; pick one in Settings first",
            )

        run = Run(
            workspace_id=workspace_id or "",
            card_id=card.id,
            instruction_id=instruction_id,
            state=RunState.QUEUED,
        )
        await self.store.insert_run(run)
        await self.store.append_event(
            Event(
                source=EventSource.DISPATCH_RUN,
                kind=EventKind.RUN_STARTED,
                run_id=run.id,
                workspace_id=run.workspace_id or None,
                payload={"card": card.name, "model": card.model, "mode": card.mode.value},
                text=f"Run started: {card.name}",
            )
        )

        if card.mode is CardMode.AGENTIC:
            workspace = await self.store.get_workspace(workspace_id or "")
            if not workspace:
                raise DispatchError(f"unknown workspace: {workspace_id}")
            coro = self._execute_agentic(
                run, card, workspace, rendered_text, base_branch=base_branch
            )
        else:
            coro = self._execute(run, card, rendered_text)

        task = asyncio.create_task(coro, name=f"run-{run.id}")
        self._tasks[run.id] = task

        def _cleanup(_t: asyncio.Task) -> None:
            self._tasks.pop(run.id, None)

        task.add_done_callback(_cleanup)
        return run

    async def cancel(self, run_id: str, reason: str = "user requested") -> bool:
        task = self._tasks.get(run_id)
        if not task:
            return False
        task.cancel()
        await self.store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.RUN_COMPLETED,
                run_id=run_id,
                payload={"state": "aborted", "reason": reason},
                text=f"aborted: {reason}",
            )
        )
        return True

    async def approve(
        self,
        run_id: str,
        *,
        note: str | None = None,
        merge_mode: str = "clean",
    ) -> None:
        run = await self.store.get_run(run_id)
        if not run:
            raise DispatchError(f"unknown run: {run_id}")
        if run.state is not RunState.REVIEWING:
            raise DispatchError(
                f"approve requires REVIEWING; run is {run.state.value}",
            )
        # If this run is worktree-bound, merge into the base branch first.
        if run.branch_id:
            try:
                await self.manager.approve_and_merge(
                    run.branch_id,
                    mode=merge_mode,  # type: ignore[arg-type]
                )
            except Exception as exc:
                raise DispatchError(f"merge failed: {exc}") from exc
        await self._transition_run(run, RunState.MERGED)
        await self.store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.RUN_COMPLETED,
                run_id=run.id,
                payload={"state": "merged", "note": note},
                text=note or "approved",
            )
        )

    async def reject(self, run_id: str, reason: str) -> None:
        run = await self.store.get_run(run_id)
        if not run:
            raise DispatchError(f"unknown run: {run_id}")
        if run.state is not RunState.REVIEWING:
            raise DispatchError(
                f"reject requires REVIEWING; run is {run.state.value}",
            )
        if run.branch_id:
            try:
                await self.manager.reject(run.branch_id, reason)
            except Exception:
                log.exception("worktree reject failed")
        await self._transition_run(run, RunState.REJECTED)
        await self.store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.RUN_COMPLETED,
                run_id=run.id,
                payload={"state": "rejected", "reason": reason},
                text=f"rejected: {reason}",
            )
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _open_chat_with_fallbacks(
        self,
        card: PersonalityCard,
    ) -> tuple[Any, PersonalityCard]:
        """Open a ChatSession against card.provider; on open failure try
        each declared fallback in order.  Returns (session, effective_card).
        """
        attempts: list[tuple[str, str]] = [(card.provider, card.model)]
        for fb in card.fallbacks:
            p = fb.get("provider")
            m = fb.get("model")
            if p and m:
                attempts.append((p, m))
        last_exc: Exception | None = None
        for provider_name, model in attempts:
            try:
                provider = get_provider(provider_name)
                effective = card.model_copy(update={"provider": provider_name, "model": model})
                session = await provider.open_chat(effective, system=card.description)
                if (provider_name, model) != (card.provider, card.model):
                    log.info(
                        "fallback engaged: %s/%s",
                        provider_name,
                        model,
                    )
                return session, effective
            except Exception as exc:
                log.warning("provider %s/%s open failed: %s", provider_name, model, exc)
                last_exc = exc
        raise DispatchError(f"all providers failed: {last_exc}" if last_exc else "no providers")

    async def _execute(self, run: Run, card: PersonalityCard, rendered_text: str) -> None:
        try:
            await self._transition_run(run, RunState.PLANNING)
            await self._transition_run(run, RunState.EXECUTING)

            session, effective_card = await self._open_chat_with_fallbacks(card)
            card = effective_card

            seq = 0
            full_text_parts: list[str] = []
            tokens_in = tokens_out = 0
            t0 = time.monotonic()

            try:
                async for ev in session.send(rendered_text):
                    if ev.kind == "text_delta":
                        full_text_parts.append(ev.text)
                        await self.store.append_event(
                            Event(
                                source=EventSource.DISPATCH_RUN,
                                kind=EventKind.LLM_CALL_COMPLETED,
                                run_id=run.id,
                                payload={"delta": ev.text[:200]},
                                text="",  # deltas don't go to FTS
                            )
                        )
                    elif ev.kind == "assistant_message":
                        # Accumulated text — record as a Step.
                        seq += 1
                        step = Step(
                            id=short_id(10),
                            run_id=run.id,
                            seq=seq,
                            kind=StepKind.LLM_CALL,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            cost_usd=cost_for_call(
                                card.provider,
                                card.model,
                                tokens_in,
                                tokens_out,
                            ),
                            payload={"finished_at": ev.payload.get("finished_at")},
                        )
                        await self.store.insert_step(step)
                    elif ev.kind == "usage":
                        tokens_in = int(ev.payload.get("input_tokens") or 0)
                        tokens_out = int(ev.payload.get("output_tokens") or 0)
                    elif ev.kind == "error":
                        raise DispatchError(ev.text or "provider error")
                    elif ev.kind == "finish":
                        break
            finally:
                await session.close()

            full_text = "".join(full_text_parts)

            cost = cost_for_call(card.provider, card.model, tokens_in, tokens_out)
            run.cost_usd = cost
            run.cost_tokens = tokens_in + tokens_out
            await self.store.db.execute(
                "UPDATE runs SET cost_usd = ?, cost_tokens = ? WHERE id = ?",
                (run.cost_usd, run.cost_tokens, run.id),
            )
            await self.store.db.commit()

            artifact = Artifact(
                id=long_id(),
                run_id=run.id,
                kind=ArtifactKind.TRANSCRIPT,
                title=f"{card.name} — final response",
                body=full_text,
            )
            await self.store.insert_artifact(artifact)

            duration_s = int(time.monotonic() - t0)
            await self.store.append_event(
                Event(
                    source=EventSource.DISPATCH_RUN,
                    kind=EventKind.RUN_STATE_CHANGED,
                    run_id=run.id,
                    payload={
                        "state": "reviewing",
                        "cost_usd": cost,
                        "tokens": tokens_in + tokens_out,
                        "duration_s": duration_s,
                        "artifact_id": artifact.id,
                    },
                    text=f"ready for review: ${cost:.4f}, {duration_s}s",
                )
            )
            # EXECUTING -> REVIEWING is a legal transition.
            await self._transition_run(run, RunState.REVIEWING)

        except asyncio.CancelledError:
            await self._fail(run, "cancelled")
            raise
        except Exception as exc:
            log.exception("run %s failed", run.id)
            await self._fail(run, str(exc))

    async def _execute_agentic(
        self,
        run: Run,
        card: PersonalityCard,
        workspace: Workspace,
        rendered_text: str,
        *,
        base_branch: str | None,
    ) -> None:
        """Worktree-bound dispatch path: create a branch, run the agent
        loop with the file-touching tools, commit per turn, capture the
        final diff as an artifact.
        """
        branch: Branch | None = None
        try:
            await self._transition_run(run, RunState.PLANNING)

            # 1. Create the worktree.
            branch = await self.manager.create(
                run.id,
                workspace,
                card,
                base_branch=base_branch,
                include_uncommitted=False,
            )
            # Persist the branch_id back onto the Run.
            await self.store.db.execute(
                "UPDATE runs SET branch_id = ? WHERE id = ?",
                (branch.id, run.id),
            )
            await self.store.db.commit()

            await self._transition_run(run, RunState.EXECUTING)

            # 2. Open the agent loop with the worktree toolset.
            toolset = WorktreeToolset(worktree=Path(branch.worktree_path))
            provider = get_provider(card.provider)

            tokens_in = tokens_out = 0
            seq = 0
            t0 = time.monotonic()
            commit_count = 0

            try:
                async for ev in provider.run_with_tools(
                    card,
                    system=card.description,
                    user_message=rendered_text,
                    executor=toolset,
                    max_turns=card.max_turns,
                ):
                    if ev.kind == "usage":
                        tokens_in = int(ev.payload.get("input_tokens") or 0)
                        tokens_out = int(ev.payload.get("output_tokens") or 0)
                    elif ev.kind == "tool_call":
                        await self.store.append_event(
                            Event(
                                source=EventSource.DISPATCH_RUN,
                                kind=EventKind.TOOL_CALLED,
                                run_id=run.id,
                                branch_id=branch.id,
                                workspace_id=workspace.id,
                                payload=ev.payload,
                                text=f"tool: {ev.payload.get('name')}",
                            )
                        )
                    elif ev.kind == "tool_result":
                        seq += 1
                        step = Step(
                            id=short_id(10),
                            run_id=run.id,
                            seq=seq,
                            kind=StepKind.TOOL_CALL,
                            payload=ev.payload,
                        )
                        await self.store.insert_step(step)
                    elif ev.kind == "assistant_message":
                        # A textual narration block from the agent.
                        if ev.text:
                            await self.store.append_event(
                                Event(
                                    source=EventSource.DISPATCH_RUN,
                                    kind=EventKind.LLM_CALL_COMPLETED,
                                    run_id=run.id,
                                    branch_id=branch.id,
                                    workspace_id=workspace.id,
                                    payload={},
                                    text=ev.text[:8000],
                                )
                            )
                    elif ev.kind == "turn_end":
                        # If anything was written this turn, commit it.
                        written = toolset.reset_written()
                        if written and commit_count < card.max_commits_per_run:
                            try:
                                turn = ev.payload.get("turn", commit_count + 1)
                                msg = (
                                    f"[{card.archetype}] Turn {turn} "
                                    f"({len(written)} files)\n\n"
                                    f"run: {run.id}\n"
                                    f"card: {card.name}@{card.version}\n"
                                    f"model: {card.provider}/{card.model}\n"
                                    f"prompt-hash: {long_id(8)}"
                                )
                                await self.manager.commit(
                                    branch.id,
                                    list(written),
                                    msg,
                                    no_verify=card.skip_pre_commit_hooks,
                                )
                                commit_count += 1
                            except Exception as exc:
                                log.warning("commit failed: %s", exc)
                    elif ev.kind == "error":
                        raise DispatchError(ev.text or "agent error")
                    elif ev.kind == "finish":
                        break
            finally:
                pass

            # 3. Cost accounting.
            cost = cost_for_call(card.provider, card.model, tokens_in, tokens_out)
            run.cost_usd = cost
            run.cost_tokens = tokens_in + tokens_out
            await self.store.db.execute(
                "UPDATE runs SET cost_usd = ?, cost_tokens = ? WHERE id = ?",
                (cost, run.cost_tokens, run.id),
            )
            await self.store.db.commit()

            # 4. Capture the final diff as a DIFF artifact.
            diff_text = ""
            try:
                diff_text = await g.diff(
                    Path(workspace.repo_path),
                    branch.base_ref,
                    branch.agent_branch_name,
                )
            except Exception as exc:
                log.warning("diff capture failed: %s", exc)

            await self.store.insert_artifact(
                Artifact(
                    id=long_id(),
                    run_id=run.id,
                    kind=ArtifactKind.DIFF,
                    title=f"{card.name} — diff vs {branch.base_branch_name}",
                    body=diff_text or "(no changes)",
                )
            )

            # Tool-call audit artifact.
            await self.store.insert_artifact(
                Artifact(
                    id=long_id(),
                    run_id=run.id,
                    kind=ArtifactKind.TRANSCRIPT,
                    title=f"{card.name} — tool timeline",
                    body=serialize_invocations(toolset.invocations) or "(no tool calls)",
                )
            )

            duration_s = int(time.monotonic() - t0)
            await self.store.append_event(
                Event(
                    source=EventSource.DISPATCH_RUN,
                    kind=EventKind.RUN_STATE_CHANGED,
                    run_id=run.id,
                    branch_id=branch.id,
                    workspace_id=workspace.id,
                    payload={
                        "state": "reviewing",
                        "cost_usd": cost,
                        "tokens": tokens_in + tokens_out,
                        "duration_s": duration_s,
                        "commits": commit_count,
                        "files_changed": len(toolset.invocations),
                    },
                    text=(
                        f"ready for review · ${cost:.4f} · {duration_s}s · "
                        f"{commit_count} save points"
                    ),
                )
            )

            # 5. Hand off to the WorktreeManager review state and the Run
            #    REVIEWING state.  Approval/rejection is the user's call.
            await self.manager.request_review(branch.id)
            await self._transition_run(run, RunState.REVIEWING)

        except asyncio.CancelledError:
            await self._fail(run, "cancelled")
            if branch is not None:
                try:
                    await self.manager.abandon(branch.id, "cancelled")
                except Exception:
                    pass
            raise
        except Exception as exc:
            log.exception("agentic run %s failed", run.id)
            await self._fail(run, str(exc))
            if branch is not None:
                try:
                    await self.manager.abandon(branch.id, f"failed: {exc}")
                except Exception:
                    pass

    async def _fail(self, run: Run, error: str) -> None:
        try:
            await self._transition_run(run, RunState.ABORTED)
        except Exception:
            pass
        await self.store.append_event(
            Event(
                source=EventSource.DISPATCH_RUN,
                kind=EventKind.RUN_COMPLETED,
                run_id=run.id,
                payload={"state": "aborted", "error": error},
                text=f"aborted: {error}",
            )
        )

    async def _transition_run(self, run: Run, to: RunState) -> None:
        assert_run_transition(run.state, to)
        run.state = to
        run.state_changed_at = utc_now()
        await self.store.update_run_state(run.id, to)
        await self.store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.RUN_STATE_CHANGED,
                run_id=run.id,
                payload={"to": to.value},
                text=to.value,
            )
        )


def _serialize_event_for_payload(ev: Event) -> dict[str, Any]:
    return {
        "id": ev.id,
        "seq": ev.seq,
        "kind": ev.kind.value,
        "source": ev.source.value,
        "run_id": ev.run_id,
        "step_id": ev.step_id,
        "occurred_at": ev.occurred_at.isoformat(),
        "text": ev.text,
        "payload": ev.payload,
    }
