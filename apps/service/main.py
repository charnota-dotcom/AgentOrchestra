"""Orchestrator service entrypoint.

Boots the SQLite store, seeds default cards, starts the JSONL watcher,
mounts the JSON-RPC server, and serves until SIGINT/SIGTERM.

Run with: ``agentorchestra-service`` (installed by pyproject scripts).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path
from typing import Any

import uvicorn

from apps.service.agents import FOLLOWUP_PRESETS, followup_instruction
from apps.service.cards.seed import seed_default_cards
from apps.service.cost.meter import forecast as cost_forecast
from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.dispatcher import RunDispatcher
from apps.service.dispatch.drift_sentinel import DriftSentinel
from apps.service.flows import FlowExecutor
from apps.service.ingestion.hook_installer import (
    install as install_hook,
)
from apps.service.ingestion.hook_installer import (
    status as hook_status,
)
from apps.service.ingestion.hook_installer import (
    uninstall as uninstall_hook,
)
from apps.service.ingestion.jsonl_watcher import JSONLWatcher
from apps.service.ipc.server import JsonRpcServer
from apps.service.linter.preflight import lint
from apps.service.mcp import registry as mcp_registry
from apps.service.providers.registry import known_providers
from apps.service.secrets.keyring_store import hook_token
from apps.service.store.events import EventStore
from apps.service.templates.engine import render
from apps.service.types import (
    Agent,
    Event,
    EventKind,
    EventSource,
    Flow,
    Instruction,
    long_id,
    utc_now,
)
from apps.service.worktrees.manager import WorktreeManager

log = logging.getLogger(__name__)


DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "agentorchestra"


def _render_transcript(agent: Agent) -> str:
    """Fold an agent's structured transcript into a flat prompt string.

    The CLI adapters take a single prompt; chat history is preserved
    by inlining it the same way ``ClaudeCLIChatSession._render_prompt``
    does.  Done at the orchestrator layer (rather than in each
    provider) because the agent's transcript includes the user's
    follow-up task seed for spawned agents — provider-level history
    folding would lose that context.
    """
    parts: list[str] = []
    if agent.system:
        parts.append(f"System: {agent.system}")
    for m in agent.transcript:
        role = "User" if m.get("role") == "user" else "Assistant"
        parts.append(f"{role}: {m.get('content', '')}")
    return "\n\n".join(parts)


def _data_dir() -> Path:
    p = DEFAULT_DATA_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# RPC method handlers
# ---------------------------------------------------------------------------


class Handlers:
    def __init__(
        self,
        store: EventStore,
        manager: WorktreeManager,
        dispatcher: RunDispatcher,
        flow_executor: FlowExecutor | None = None,
    ) -> None:
        self.store = store
        self.manager = manager
        self.dispatcher = dispatcher
        self.flow_executor = flow_executor or FlowExecutor(store)

    async def workspaces_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [w.model_dump(mode="json") for w in await self.store.list_workspaces()]

    async def workspaces_register(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["path"])
        ws = await self.manager.register_workspace(
            path,
            name=params.get("name"),
            default_base_branch=params.get("default_base_branch", "main"),
        )
        return ws.model_dump(mode="json")

    async def workspaces_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.store.delete_workspace(params["workspace_id"])
        return {"removed": bool(ok)}

    async def cards_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in await self.store.list_cards()]

    async def runs_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        runs = await self.store.list_runs(workspace_id=params.get("workspace_id"))
        return [r.model_dump(mode="json") for r in runs]

    async def search(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.store.search(params["query"], limit=params.get("limit", 50))

    async def lint_instruction(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        issues = lint(
            params["text"],
            archetype=params.get("archetype"),
            variables=params.get("variables"),
        )
        return [
            {
                "rule": i.rule,
                "severity": i.severity.value,
                "message": i.message,
                "field": i.field,
                "suggestion": i.suggestion,
            }
            for i in issues
        ]

    async def cost_forecast(self, params: dict[str, Any]) -> dict[str, Any]:
        f = cost_forecast(
            params["provider"],
            params["model"],
            rendered_prompt_tokens=params["rendered_prompt_tokens"],
            archetype=params.get("archetype"),
        )
        return {
            "low_usd": f.low_usd,
            "high_usd": f.high_usd,
            "expected_usd": f.expected_usd,
            "rationale": f.rationale,
        }

    async def render_template(self, params: dict[str, Any]) -> dict[str, Any]:
        template = await self.store.get_template(params["template_id"])
        if not template:
            raise ValueError(f"unknown template: {params['template_id']}")
        rendered = render(template, params.get("variables", {}))
        ins = Instruction(
            id=long_id(),
            template_id=template.id,
            template_version=template.version,
            card_id=params["card_id"],
            rendered_text=rendered,
            variables=params.get("variables", {}),
        )
        await self.store.insert_instruction(ins)
        return {"instruction_id": ins.id, "rendered_text": rendered}

    async def templates_get(self, params: dict[str, Any]) -> dict[str, Any]:
        template = await self.store.get_template(params["template_id"])
        if not template:
            raise ValueError(f"unknown template: {params['template_id']}")
        return {
            "id": template.id,
            "name": template.name,
            "archetype": template.archetype,
            "version": template.version,
            "variables": [
                {
                    "name": v.name,
                    "label": v.label,
                    "kind": v.kind,
                    "required": v.required,
                    "default": v.default,
                    "help": v.help,
                    "options": v.options,
                }
                for v in template.variables
            ],
        }

    async def runs_dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        run = await self.dispatcher.dispatch(
            workspace_id=params.get("workspace_id"),
            card_id=params["card_id"],
            instruction_id=params["instruction_id"],
            rendered_text=params["rendered_text"],
        )
        return {"run_id": run.id, "state": run.state.value}

    async def runs_approve(self, params: dict[str, Any]) -> dict[str, Any]:
        await self.dispatcher.approve(params["run_id"], note=params.get("note"))
        return {"ok": True}

    async def runs_reject(self, params: dict[str, Any]) -> dict[str, Any]:
        await self.dispatcher.reject(params["run_id"], params.get("reason", ""))
        return {"ok": True}

    async def runs_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.dispatcher.cancel(
            params["run_id"],
            params.get("reason", "user requested"),
        )
        return {"ok": ok}

    async def runs_approve_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.dispatcher.approve_plan(params["run_id"])
        return {"ok": ok}

    async def runs_consensus(self, params: dict[str, Any]) -> dict[str, Any]:
        from apps.service.dispatch.consensus import run_consensus

        # Locate the bundled consensus card+template so we have valid FKs.
        cur = await self.store.db.execute(
            "SELECT id, template_id FROM cards WHERE archetype = 'consensus' LIMIT 1",
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError("consensus card not seeded; restart the service to seed it")
        candidates = [(c["provider"], c["model"]) for c in (params.get("candidates") or [])]
        if len(candidates) < 2:
            raise ValueError("need at least two candidates")
        result = await run_consensus(
            self.store,
            self.dispatcher.bus,
            question=params["question"],
            judge_provider=params.get("judge_provider", "anthropic"),
            judge_model=params.get("judge_model", "claude-sonnet-4-5"),
            candidates=candidates,
            judge_instructions=params.get("judge_instructions"),
            consensus_card_id=row["id"],
            consensus_template_id=row["template_id"],
        )
        return {
            "run_id": result.run_id,
            "candidates": [
                {
                    "provider": c.provider,
                    "model": c.model,
                    "tokens_in": c.tokens_in,
                    "tokens_out": c.tokens_out,
                    "error": c.error,
                    "duration_s": c.duration_s,
                }
                for c in result.candidates
            ],
            "cost_usd": result.cost_usd,
        }

    async def runs_replay(self, params: dict[str, Any]) -> dict[str, Any]:
        run = await self.dispatcher.replay(
            params["run_id"],
            provider_override=params.get("provider"),
            model_override=params.get("model"),
            instruction_override=params.get("instruction"),
        )
        return {"run_id": run.id, "state": run.state.value}

    async def runs_artifacts(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        cur = await self.store.db.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at",
            (params["run_id"],),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def providers(self, params: dict[str, Any]) -> list[str]:
        return known_providers()

    async def mcp_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [mcp_registry.to_dict(s) for s in mcp_registry.list_servers()]

    async def mcp_add(self, params: dict[str, Any]) -> dict[str, Any]:
        s = mcp_registry.add_server(
            name=params["name"],
            transport=params["transport"],
            command=params.get("command", ""),
            args=params.get("args") or [],
            url=params.get("url", ""),
            env=params.get("env") or {},
        )
        return mcp_registry.to_dict(s)

    async def mcp_trust(self, params: dict[str, Any]) -> dict[str, Any]:
        s = mcp_registry.trust_server(params["id"])
        if not s:
            raise ValueError(f"unknown mcp server: {params['id']}")
        return mcp_registry.to_dict(s)

    async def mcp_block(self, params: dict[str, Any]) -> dict[str, Any]:
        s = mcp_registry.block_server(params["id"])
        if not s:
            raise ValueError(f"unknown mcp server: {params['id']}")
        return mcp_registry.to_dict(s)

    async def mcp_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = mcp_registry.remove_server(params["id"])
        return {"ok": ok}

    async def dictation_status(self, params: dict[str, Any]) -> dict[str, Any]:
        from apps.service.dictation.whisper import is_available

        return {"available": is_available()}

    async def dictation_transcribe(self, params: dict[str, Any]) -> dict[str, Any]:
        from apps.service.dictation.whisper import (
            TranscriptionOptions,
            transcribe_file,
        )

        path = Path(params["audio_path"])
        text = await asyncio.to_thread(
            transcribe_file,
            path,
            TranscriptionOptions(
                model_size=params.get("model_size", "base"),
                language=params.get("language"),
            ),
        )
        return {"text": text}

    # ------------------------------------------------------------------
    # Flow Canvas RPCs
    # ------------------------------------------------------------------

    async def flows_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        flows = await self.store.list_flows()
        return [
            {
                "id": f.id,
                "name": f.name,
                "description": f.description,
                "version": f.version,
                "nodes": f.nodes,
                "edges": f.edges,
                "updated_at": f.updated_at.isoformat(),
            }
            for f in flows
        ]

    async def flows_get(self, params: dict[str, Any]) -> dict[str, Any]:
        flow = await self.store.get_flow(params["id"])
        if not flow:
            raise ValueError(f"unknown flow: {params['id']}")
        return {
            "id": flow.id,
            "name": flow.name,
            "description": flow.description,
            "version": flow.version,
            "nodes": flow.nodes,
            "edges": flow.edges,
        }

    async def flows_create(self, params: dict[str, Any]) -> dict[str, Any]:
        flow = Flow(
            name=params.get("name") or "Untitled flow",
            description=params.get("description", ""),
            nodes=params.get("nodes", []),
            edges=params.get("edges", []),
        )
        await self.store.insert_flow(flow)
        return {"id": flow.id}

    async def flows_update(self, params: dict[str, Any]) -> dict[str, Any]:
        flow = await self.store.get_flow(params["id"])
        if not flow:
            raise ValueError(f"unknown flow: {params['id']}")
        flow.name = params.get("name", flow.name)
        flow.description = params.get("description", flow.description)
        flow.nodes = params.get("nodes", flow.nodes)
        flow.edges = params.get("edges", flow.edges)
        flow.updated_at = utc_now()
        await self.store.update_flow(flow)
        return {"id": flow.id, "version": flow.version}

    async def flows_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.store.delete_flow(params["id"])
        return {"deleted": bool(ok)}

    async def flows_dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        flow = await self.store.get_flow(params["flow_id"])
        if not flow:
            raise ValueError(f"unknown flow: {params['flow_id']}")
        run = await self.flow_executor.dispatch(flow)
        return {"run_id": run.id}

    async def flows_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.flow_executor.cancel(params["run_id"])
        return {"cancelled": bool(ok)}

    async def flows_approve_human(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.flow_executor.approve_human(
            params["run_id"], params["node_id"], bool(params.get("approved", True))
        )
        return {"ok": bool(ok)}

    # ------------------------------------------------------------------
    # Plain chat — no card, no template, no state machine.
    # ------------------------------------------------------------------

    async def chat_send(self, params: dict[str, Any]) -> dict[str, Any]:
        """Send one message to one provider and return the full reply.

        Bypasses the dispatcher / Run state machine entirely.  This is
        the "lay-person" path: a chat box, a model dropdown, optional
        skills / thinking-depth annotations.  No worktrees, no cost
        caps, no review state.

        ``params``:
          provider: "claude-cli" | "gemini-cli" | "anthropic" | "google" | ...
          model:    e.g. "claude-sonnet-4-5" or "gemini-2.5-pro"
          message:  the user's message text
          system:   optional system prompt (e.g. derived from skills + thinking)
        """
        from apps.service.providers.protocol import ChatSession  # noqa: F401
        from apps.service.providers.registry import get_provider
        from apps.service.types import (
            BlastRadiusPolicy,
            CardMode,
            CostPolicy,
            PersonalityCard,
            SandboxTier,
        )

        provider_name = params["provider"]
        model = params["model"]
        message = params["message"]
        system = params.get("system") or ""

        # Ephemeral card so we can reuse the existing provider
        # adapters without duplicating their auth / env wiring.
        # ``archetype`` and ``template_id`` must be slug-safe
        # (alphanumeric + hyphens) per PersonalityCard's validator.
        card = PersonalityCard(
            name="(chat)",
            archetype="chat",
            description="ephemeral chat card",
            template_id="chat",
            provider=provider_name,
            model=model,
            mode=CardMode.CHAT,
            cost=CostPolicy(),
            blast_radius=BlastRadiusPolicy(),
            sandbox_tier=SandboxTier.DEVCONTAINER,
        )
        provider = get_provider(provider_name)
        session = await provider.open_chat(card, system=system or None)
        chunks: list[str] = []
        try:
            async for ev in session.send(message):
                if ev.kind == "text_delta":
                    chunks.append(ev.text)
                elif ev.kind == "error":
                    raise RuntimeError(ev.text or "provider error")
                elif ev.kind == "finish":
                    break
        finally:
            await session.close()
        return {"reply": "".join(chunks)}

    # ------------------------------------------------------------------
    # Named agents — persistent conversations with follow-up linkage.
    # ------------------------------------------------------------------

    async def agents_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [a.model_dump(mode="json") for a in await self.store.list_agents()]

    async def agents_get(self, params: dict[str, Any]) -> dict[str, Any]:
        agent = await self.store.get_agent(params["id"])
        if not agent:
            raise ValueError(f"unknown agent: {params['id']}")
        return agent.model_dump(mode="json")

    async def agents_create(self, params: dict[str, Any]) -> dict[str, Any]:
        agent = Agent(
            name=(params.get("name") or "Unnamed agent").strip(),
            provider=params["provider"],
            model=params["model"],
            system=params.get("system", ""),
        )
        await self.store.insert_agent(agent)
        return agent.model_dump(mode="json")

    async def agents_send(self, params: dict[str, Any]) -> dict[str, Any]:
        """Append the user's message to the agent's transcript and
        get a reply.  Multi-turn — every turn after the first builds
        on the prior transcript.
        """
        from apps.service.providers.registry import get_provider

        agent = await self.store.get_agent(params["agent_id"])
        if not agent:
            raise ValueError(f"unknown agent: {params['agent_id']}")
        message = params["message"]
        agent.transcript.append({"role": "user", "content": message})

        # Fold transcript + system into a single prompt for the CLI
        # adapter (which doesn't accept structured messages in
        # headless mode).  Fresh providers keep their own session
        # state per call; persisting it in our store is the
        # source-of-truth.
        prompt = _render_transcript(agent)

        provider = get_provider(agent.provider)
        # Use an ephemeral card to reuse the existing provider auth.
        from apps.service.types import (
            BlastRadiusPolicy,
            CardMode,
            CostPolicy,
            PersonalityCard,
            SandboxTier,
        )

        card = PersonalityCard(
            name=f"(agent {agent.name})",
            archetype="agent",
            description="ephemeral card for a named agent",
            template_id="agent",
            provider=agent.provider,
            model=agent.model,
            mode=CardMode.CHAT,
            cost=CostPolicy(),
            blast_radius=BlastRadiusPolicy(),
            sandbox_tier=SandboxTier.DEVCONTAINER,
        )
        session = await provider.open_chat(card, system=agent.system or None)
        chunks: list[str] = []
        try:
            async for ev in session.send(prompt):
                if ev.kind == "text_delta":
                    chunks.append(ev.text)
                elif ev.kind == "error":
                    raise RuntimeError(ev.text or "provider error")
                elif ev.kind == "finish":
                    break
        finally:
            await session.close()
        reply = "".join(chunks)
        agent.transcript.append({"role": "assistant", "content": reply})
        await self.store.update_agent(agent)
        return {"reply": reply, "agent": agent.model_dump(mode="json")}

    async def agents_spawn_followup(self, params: dict[str, Any]) -> dict[str, Any]:
        """Spawn a new agent that builds on a parent's transcript.

        ``params``:
          parent_id: id of the agent to follow up on
          name:      display name of the new agent (e.g. "Smith Reviewer")
          preset:    one of FOLLOWUP_PRESETS keys, or "custom"
          custom:    instruction text when preset == "custom"
          provider / model: optional overrides — defaults to the parent's
        """
        parent = await self.store.get_agent(params["parent_id"])
        if not parent:
            raise ValueError(f"unknown parent agent: {params['parent_id']}")

        instruction = followup_instruction(params.get("preset", "custom"), params.get("custom", ""))
        if not instruction:
            raise ValueError("follow-up instruction is empty")

        # Build the new agent's transcript: prior conversation as
        # context, then the follow-up instruction as the kick-off
        # user message.  The reply will follow on the first .send().
        seeded_transcript: list[dict[str, str]] = []
        seeded_transcript.append(
            {
                "role": "user",
                "content": (
                    f"You are following up on '{parent.name}'.  "
                    "Below is the full prior conversation between a user "
                    "and that agent.  Read it carefully, then carry out "
                    "the follow-up task at the end.\n\n"
                    "=== Prior conversation ===\n"
                    + _render_transcript(parent)
                    + "\n=== Follow-up task ===\n"
                    + instruction
                ),
            }
        )

        agent = Agent(
            name=(params.get("name") or f"Follow-up of {parent.name}").strip(),
            provider=params.get("provider") or parent.provider,
            model=params.get("model") or parent.model,
            system=parent.system,
            parent_id=parent.id,
            parent_name=parent.name,
            parent_preset=params.get("preset", "custom"),
            transcript=seeded_transcript,
        )
        await self.store.insert_agent(agent)
        return {"agent": agent.model_dump(mode="json")}

    async def agents_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.store.delete_agent(params["id"])
        return {"deleted": bool(ok)}

    async def agents_followup_presets(self, params: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"key": key, "label": label, "instruction": body}
            for key, (label, body) in FOLLOWUP_PRESETS.items()
        ]

    async def hooks_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return hook_status()

    async def hooks_install(self, params: dict[str, Any]) -> dict[str, Any]:
        plan = install_hook(service_url=params["service_url"])
        return {
            "settings_path": str(plan.settings_path),
            "script_path": str(plan.script_path),
        }

    async def hooks_uninstall(self, params: dict[str, Any]) -> dict[str, Any]:
        removed = uninstall_hook()
        return {"removed": removed}

    async def hook_received(self, params: dict[str, Any]) -> dict[str, Any]:
        await self.store.append_event(
            Event(
                source=EventSource.INGEST_CLAUDE_HOOK,
                kind=EventKind.INGEST_RECEIVED,
                payload=params.get("payload") or {},
                text=str(params.get("payload", {}))[:4000],
            )
        )
        return {"ok": True}


# ---------------------------------------------------------------------------
# Wire-up
# ---------------------------------------------------------------------------


def _install_handlers(server: JsonRpcServer, h: Handlers) -> None:
    server.register("workspaces.list", h.workspaces_list)
    server.register("workspaces.register", h.workspaces_register)
    server.register("workspaces.remove", h.workspaces_remove)
    server.register("cards.list", h.cards_list)
    server.register("runs.list", h.runs_list)
    server.register("runs.dispatch", h.runs_dispatch)
    server.register("runs.approve", h.runs_approve)
    server.register("runs.reject", h.runs_reject)
    server.register("runs.cancel", h.runs_cancel)
    server.register("runs.replay", h.runs_replay)
    server.register("runs.consensus", h.runs_consensus)
    server.register("runs.approve_plan", h.runs_approve_plan)
    server.register("runs.artifacts", h.runs_artifacts)
    server.register("search", h.search)
    server.register("lint.instruction", h.lint_instruction)
    server.register("cost.forecast", h.cost_forecast)
    server.register("templates.render", h.render_template)
    server.register("templates.get", h.templates_get)
    server.register("flows.list", h.flows_list)
    server.register("flows.get", h.flows_get)
    server.register("flows.create", h.flows_create)
    server.register("flows.update", h.flows_update)
    server.register("flows.delete", h.flows_delete)
    server.register("flows.dispatch", h.flows_dispatch)
    server.register("flows.cancel", h.flows_cancel)
    server.register("flows.approve_human", h.flows_approve_human)
    server.register("chat.send", h.chat_send)
    server.register("agents.list", h.agents_list)
    server.register("agents.get", h.agents_get)
    server.register("agents.create", h.agents_create)
    server.register("agents.send", h.agents_send)
    server.register("agents.spawn_followup", h.agents_spawn_followup)
    server.register("agents.delete", h.agents_delete)
    server.register("agents.followup_presets", h.agents_followup_presets)
    server.register("providers", h.providers)
    server.register("hook.received", h.hook_received)
    server.register("hooks.status", h.hooks_status)
    server.register("hooks.install", h.hooks_install)
    server.register("hooks.uninstall", h.hooks_uninstall)
    server.register("mcp.list", h.mcp_list)
    server.register("mcp.add", h.mcp_add)
    server.register("mcp.trust", h.mcp_trust)
    server.register("mcp.block", h.mcp_block)
    server.register("mcp.remove", h.mcp_remove)
    server.register("dictation.status", h.dictation_status)
    server.register("dictation.transcribe", h.dictation_transcribe)


async def serve(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    data_dir = Path(args.data_dir) if args.data_dir else _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "agentorchestra.sqlite"

    store = EventStore(db_path)
    await store.open()
    log.info("store open at %s", db_path)

    seeded = await seed_default_cards(store)
    if seeded:
        log.info("seeded %d cards", len(seeded))

    bus = EventBus()
    store.on_append = bus.publish

    manager = WorktreeManager(store)
    dispatcher = RunDispatcher(store, manager, bus)
    handlers = Handlers(store, manager, dispatcher)

    watcher = JSONLWatcher(store)
    await watcher.start()

    sentinel = DriftSentinel(store=store, bus=bus)
    await sentinel.start()

    token = hook_token()
    rpc = JsonRpcServer(token=token, bus=bus)
    _install_handlers(rpc, handlers)
    log.info("rpc token: %s", token[:8] + "…")

    config = uvicorn.Config(
        rpc.app(),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    await store.append_event(
        Event(
            source=EventSource.SYSTEM,
            kind=EventKind.SERVICE_STARTED,
            text=f"service started on 127.0.0.1:{args.port}",
        )
    )

    stop = asyncio.Event()

    def _signal(_sig: int, _frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    stop_task = asyncio.create_task(stop.wait(), name="stop-signal")

    await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError):
        await server_task
    await sentinel.stop()
    await watcher.stop()
    await store.append_event(
        Event(source=EventSource.SYSTEM, kind=EventKind.SERVICE_STOPPED, text="service stopped")
    )
    await store.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="agentorchestra-service")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()
    return asyncio.run(serve(args))


if __name__ == "__main__":
    sys.exit(main())
