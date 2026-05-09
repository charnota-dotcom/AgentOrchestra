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

from apps.service.cards.seed import seed_default_cards
from apps.service.cost.meter import forecast as cost_forecast
from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.dispatcher import RunDispatcher
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
from apps.service.providers.registry import known_providers
from apps.service.secrets.keyring_store import hook_token
from apps.service.store.events import EventStore
from apps.service.templates.engine import render
from apps.service.types import (
    Event,
    EventKind,
    EventSource,
    Instruction,
    long_id,
)
from apps.service.worktrees.manager import WorktreeManager

log = logging.getLogger(__name__)


DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "agentorchestra"


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
    ) -> None:
        self.store = store
        self.manager = manager
        self.dispatcher = dispatcher

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
    server.register("cards.list", h.cards_list)
    server.register("runs.list", h.runs_list)
    server.register("runs.dispatch", h.runs_dispatch)
    server.register("runs.approve", h.runs_approve)
    server.register("runs.reject", h.runs_reject)
    server.register("runs.cancel", h.runs_cancel)
    server.register("runs.replay", h.runs_replay)
    server.register("runs.consensus", h.runs_consensus)
    server.register("runs.artifacts", h.runs_artifacts)
    server.register("search", h.search)
    server.register("lint.instruction", h.lint_instruction)
    server.register("cost.forecast", h.cost_forecast)
    server.register("templates.render", h.render_template)
    server.register("providers", h.providers)
    server.register("hook.received", h.hook_received)
    server.register("hooks.status", h.hooks_status)
    server.register("hooks.install", h.hooks_install)
    server.register("hooks.uninstall", h.hooks_uninstall)


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
