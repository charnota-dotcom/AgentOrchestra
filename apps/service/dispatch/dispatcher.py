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
    Instruction,
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
        # Plan-approval gates: one event per Run that's waiting for the
        # human to nod off the plan before executing.
        self._plan_gates: dict[str, asyncio.Event] = {}

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
            workspace_id=workspace_id,
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

    async def replay(
        self,
        original_run_id: str,
        *,
        provider_override: str | None = None,
        model_override: str | None = None,
        instruction_override: str | None = None,
    ) -> Run:
        """Re-execute a past run.  Reuses the original instruction and
        card unless overrides are supplied; provider/model overrides
        clone the card so the original's accounting stays intact.
        """
        original = await self.store.get_run(original_run_id)
        if not original:
            raise DispatchError(f"unknown run: {original_run_id}")
        card = await self.store.get_card(original.card_id)
        if not card:
            raise DispatchError(f"original card missing: {original.card_id}")

        cur = await self.store.db.execute(
            "SELECT rendered_text, template_id, template_version FROM instructions WHERE id = ?",
            (original.instruction_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise DispatchError("original instruction missing")
        rendered = instruction_override or row["rendered_text"]

        effective_card = card
        if provider_override or model_override:
            effective_card = card.model_copy(
                update={
                    "id": long_id(),
                    "provider": provider_override or card.provider,
                    "model": model_override or card.model,
                }
            )
            await self.store.insert_card(effective_card)

        if instruction_override:
            new_ins = Instruction(
                id=long_id(),
                template_id=row["template_id"],
                template_version=row["template_version"],
                card_id=effective_card.id,
                rendered_text=instruction_override,
                variables={},
            )
            await self.store.insert_instruction(new_ins)
            instruction_id = new_ins.id
        else:
            instruction_id = original.instruction_id

        await self.store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.RUN_STARTED,
                run_id=original_run_id,
                payload={
                    "replay_of": original_run_id,
                    "provider": effective_card.provider,
                    "model": effective_card.model,
                },
                text=(
                    f"replay of {original_run_id} on "
                    f"{effective_card.provider}/{effective_card.model}"
                ),
            )
        )

        return await self.dispatch(
            workspace_id=original.workspace_id or None,
            card_id=effective_card.id,
            instruction_id=instruction_id,
            rendered_text=rendered,
        )

    async def approve_plan(self, run_id: str) -> bool:
        """Release the plan-approval gate for a paused Run."""
        gate = self._plan_gates.get(run_id)
        if not gate:
            return False
        gate.set()
        await self.store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.APPROVAL_GRANTED,
                run_id=run_id,
                payload={"phase": "plan"},
                text="plan approved",
            )
        )
        return True

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
                                # Truncation kept generous (was 200, which
                                # cut every CLI-provider reply mid-sentence
                                # since those send one big chunk rather
                                # than a token stream).
                                payload={"delta": ev.text[:8000]},
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

            # Plan-act split (optional, agentic-only).  Generates a
            # written plan via a chat session, persists it as a PLAN
            # artifact, and waits for the user to call runs.approve_plan
            # before invoking the tool-using agent loop.
            if card.requires_plan:
                await self._plan_phase(run, card, rendered_text)

            await self._transition_run(run, RunState.EXECUTING)

            # 2. Open the agent loop with the worktree toolset.
            sandbox = await self._open_sandbox(card, Path(branch.worktree_path))
            mcp_tools, mcp_clients = await self._open_mcp_tools(card)
            toolset = WorktreeToolset(
                worktree=Path(branch.worktree_path),
                sandbox=sandbox,
                mcp_tools=mcp_tools,
            )
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
                        # Hard cost cap mid-run.  Soft cap emits a warning
                        # event; hard cap aborts the run.
                        running = cost_for_call(
                            card.provider,
                            card.model,
                            tokens_in,
                            tokens_out,
                        )
                        if running > card.cost.hard_cap_usd:
                            raise DispatchError(
                                f"hard cost cap exceeded: "
                                f"${running:.4f} > ${card.cost.hard_cap_usd:.2f}"
                            )
                        if running > card.cost.soft_cap_usd and not getattr(
                            self,
                            "_warned_" + run.id,
                            False,
                        ):
                            setattr(self, "_warned_" + run.id, True)
                            await self.store.append_event(
                                Event(
                                    source=EventSource.SYSTEM,
                                    kind=EventKind.RUN_STATE_CHANGED,
                                    run_id=run.id,
                                    payload={
                                        "warning": "soft_cost_cap",
                                        "running_cost_usd": running,
                                        "soft_cap_usd": card.cost.soft_cap_usd,
                                    },
                                    text=(
                                        f"soft cost cap reached: ${running:.4f} > "
                                        f"${card.cost.soft_cap_usd:.2f}"
                                    ),
                                )
                            )
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
                if sandbox is not None:
                    try:
                        await sandbox.close()
                    except Exception:
                        log.warning("sandbox close failed", exc_info=True)
                for client in mcp_clients:
                    try:
                        await client.close()
                    except Exception:
                        log.warning("mcp client close failed", exc_info=True)

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

            # 6. Auto-QA: if the card requested it, dispatch a chat-style
            #    QA-on-fix run targeting this run's diff.  Failures here
            #    don't fail the parent run.
            if card.auto_qa:
                try:
                    await self._dispatch_auto_qa(run, card, diff_text)
                except Exception as exc:
                    log.warning("auto-QA dispatch failed: %s", exc)

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

    async def _open_mcp_tools(self, card: PersonalityCard):
        """Resolve any MCP server names in card.tool_allowlist against
        the registry, open trusted ones, and return (tool_dict, clients).
        Untrusted / blocked / unknown / non-stdio entries are skipped
        with a logged warning so a Run never exposes a tool the user
        hasn't explicitly trusted.
        """
        from apps.service.dispatch.tools import MCPRunTimeTool, ToolDef
        from apps.service.mcp.client import MCPClient
        from apps.service.mcp.registry import MCPTrust, list_servers

        if not card.tool_allowlist:
            return {}, []
        catalog = {s.name: s for s in list_servers()}
        out_tools: dict[str, MCPRunTimeTool] = {}
        clients: list[MCPClient] = []
        for name in card.tool_allowlist:
            entry = catalog.get(name)
            if entry is None:
                log.warning("mcp server %r not in registry; skipping", name)
                continue
            if entry.trust is not MCPTrust.TRUSTED:
                log.warning(
                    "mcp server %r trust=%s; skipping (mark TRUSTED to use)",
                    name,
                    entry.trust.value,
                )
                continue
            if entry.transport.value != "stdio":
                log.warning(
                    "mcp server %r transport=%s not yet supported; skipping",
                    name,
                    entry.transport.value,
                )
                continue
            client = MCPClient(
                command=entry.command,
                args=entry.args or [],
                env=entry.env or {},
            )
            try:
                await client.open()
                tool_list = await client.list_tools()
            except Exception as exc:
                log.warning("mcp server %r open failed: %s", name, exc)
                await client.close()
                continue
            clients.append(client)
            for t in tool_list:
                public_name = f"mcp:{name}:{t.name}"
                tool_def = ToolDef(
                    name=public_name,
                    description=t.description or f"MCP tool {t.name} on {name}",
                    input_schema=t.input_schema,
                )

                async def _invoke(
                    _real_name: str,
                    params: dict,
                    _c: MCPClient = client,
                    _real: str = t.name,
                ) -> dict:
                    return await _c.call_tool(_real, params)

                out_tools[public_name] = MCPRunTimeTool(
                    server_name=name,
                    tool_name=t.name,
                    definition=tool_def,
                    invoke_fn=_invoke,
                )
        return out_tools, clients

    async def _open_sandbox(self, card: PersonalityCard, worktree: Path):
        """Open a sandbox per the card's tier; fall back to local on
        Docker errors and emit a warning event.
        """
        tier = card.sandbox_tier.value
        if tier == "docker":
            try:
                from apps.service.sandbox.docker import DockerSandbox

                return await DockerSandbox.open_async(worktree)
            except Exception as exc:
                log.warning("docker sandbox unavailable; falling back: %s", exc)
                await self.store.append_event(
                    Event(
                        source=EventSource.SYSTEM,
                        kind=EventKind.RUN_STATE_CHANGED,
                        payload={
                            "warning": "docker_sandbox_unavailable",
                            "fallback": "local",
                            "error": str(exc),
                        },
                        text=f"docker sandbox unavailable: {exc}",
                    )
                )
        # Default: no sandbox object — toolset uses direct FS I/O, which
        # is the V1 LocalSandbox behavior.
        return None

    async def _plan_phase(
        self,
        run: Run,
        card: PersonalityCard,
        rendered_text: str,
    ) -> None:
        """Generate a written plan via chat, persist it as a PLAN artifact,
        await human approval before continuing.
        """
        provider = get_provider(card.provider)
        plan_card = card.model_copy(update={"id": long_id(), "mode": CardMode.CHAT})
        plan_prompt = (
            "Before doing any work, produce a SHORT plan for what you will do "
            "and what you will NOT do.  Use bullets.  Do not call any tools.\n\n"
            f"Task:\n{rendered_text}"
        )
        session = await provider.open_chat(plan_card, system="Plan first; act later.")
        plan_text_parts: list[str] = []
        try:
            async for ev in session.send(plan_prompt):
                if ev.kind == "text_delta":
                    plan_text_parts.append(ev.text)
                elif ev.kind == "error":
                    raise DispatchError(ev.text or "plan-phase error")
                elif ev.kind == "finish":
                    break
        finally:
            await session.close()

        plan_text = "".join(plan_text_parts) or "(no plan produced)"
        await self.store.insert_artifact(
            Artifact(
                id=long_id(),
                run_id=run.id,
                kind=ArtifactKind.PLAN,
                title=f"{card.name} — plan (awaiting approval)",
                body=plan_text,
            )
        )

        # Pause for approval.
        await self._transition_run(run, RunState.AWAITING_APPROVAL)
        await self.store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.APPROVAL_REQUESTED,
                run_id=run.id,
                payload={"phase": "plan", "artifact_kind": "plan"},
                text="plan ready; awaiting approval",
            )
        )

        gate = asyncio.Event()
        self._plan_gates[run.id] = gate
        try:
            await gate.wait()
        finally:
            self._plan_gates.pop(run.id, None)

    async def _dispatch_auto_qa(
        self,
        parent: Run,
        parent_card: PersonalityCard,
        diff_text: str,
    ) -> None:
        """Find the QA-on-fix card and dispatch a chat-style run pointing at
        the parent's diff.  Best-effort: silently no-ops if there's no
        QA card seeded.
        """
        cur = await self.store.db.execute(
            "SELECT id FROM cards WHERE archetype = 'qa-on-fix' LIMIT 1",
        )
        row = await cur.fetchone()
        if not row:
            return
        qa_card_id = row["id"]

        # Construct a minimal rendered prompt from the parent's diff.
        diff_excerpt = diff_text[:6000] if diff_text else "(no changes)"
        rendered = (
            f"You are a QA agent reviewing run {parent.id} produced by "
            f"the {parent_card.name} card.\n\n"
            f"Verify the diff below for: regressions, missing edge cases,"
            f" secret leaks, broken error handling, public-API breakage.\n\n"
            f"Diff:\n```\n{diff_excerpt}\n```\n\n"
            f"End with an explicit verdict: APPROVE, REQUEST CHANGES, or BLOCK."
        )
        # Persist a stub instruction so the FK lights up.
        cur = await self.store.db.execute(
            "SELECT template_id FROM cards WHERE id = ?",
            (qa_card_id,),
        )
        r = await cur.fetchone()
        if not r:
            return
        ins = Instruction(
            id=long_id(),
            template_id=r["template_id"],
            template_version=1,
            card_id=qa_card_id,
            rendered_text=rendered,
            variables={"target_run_id": parent.id, "focus": "auto-QA"},
        )
        await self.store.insert_instruction(ins)

        await self.dispatch(
            workspace_id=parent.workspace_id or None,
            card_id=qa_card_id,
            instruction_id=ins.id,
            rendered_text=rendered,
        )

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
