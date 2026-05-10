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
    Attachment,
    AttachmentKind,
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


def _render_references(
    refs: list[Agent], ref_attachments: dict[str, list[Attachment]] | None = None
) -> str:
    """Format referenced agents' transcripts as a context preamble.

    Each reference is wrapped in clearly-delimited markers so the
    target model can tell where its context ends and its own
    conversation begins.  Cross-provider safe: works the same whether
    Claude is reading a Gemini transcript or vice versa, since both
    just see plain text.

    ``ref_attachments`` is an optional ``{agent_id: [Attachment]}``
    map; spreadsheet attachments on a referenced agent get folded in
    as inlined markdown tables so the receiving agent can reason
    about the file contents.  Image attachments are noted by name
    only — the receiving model can't actually see them via the text
    preamble (they need to be re-attached if the operator wants the
    new agent to view them).
    """
    if not refs:
        return ""
    blocks: list[str] = []
    for ref in refs:
        body = "\n".join(
            f"{('User' if m.get('role') == 'user' else 'Assistant')}: {m.get('content', '')}"
            for m in ref.transcript
        )
        atts = (ref_attachments or {}).get(ref.id, [])
        att_block = ""
        if atts:
            sections: list[str] = []
            for a in atts:
                if a.kind == AttachmentKind.SPREADSHEET and a.rendered_text:
                    sections.append(
                        f"[attachment: {a.original_name}]\n{a.rendered_text}"
                    )
                else:
                    sections.append(
                        f"[attachment: {a.original_name} ({a.kind.value}, {a.bytes} bytes) — "
                        f"not inlined; re-attach if needed]"
                    )
            att_block = "\n\nAttached files:\n" + "\n\n".join(sections)
        blocks.append(
            f"--- Reference: {ref.name}  "
            f"({ref.provider} {ref.model}, {len(ref.transcript)} turns) ---\n"
            f"{body}{att_block}\n"
            f"--- End reference: {ref.name} ---"
        )
    return (
        "=== Context: prior conversations the user wants you to read first ===\n\n"
        + "\n\n".join(blocks)
        + "\n\n=== End context ===\n\n"
        "(The references above are read-only context.  Continue the "
        "conversation below using them as background.)"
    )


def _inline_spreadsheet_attachments(prompt: str, attachments: list[Attachment]) -> str:
    """Append rendered-text views of spreadsheet attachments to the
    prompt.  Images are deliberately *not* inlined here — their bytes
    are passed through ``ChatSession.send(attachments=...)`` so the
    CLI can hand the actual file to the model.
    """
    sheets = [a for a in attachments if a.kind == AttachmentKind.SPREADSHEET and a.rendered_text]
    if not sheets:
        return prompt
    blocks = [
        f"[attachment: {a.original_name}]\n{a.rendered_text}" for a in sheets
    ]
    return (
        prompt
        + "\n\n=== Attached spreadsheets ===\n\n"
        + "\n\n".join(blocks)
        + "\n\n=== End attachments ==="
    )


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
        data_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.manager = manager
        self.dispatcher = dispatcher
        self.flow_executor = flow_executor or FlowExecutor(store)
        self.data_dir = data_dir or _data_dir()
        self.attachments_dir = self.data_dir / "attachments"
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        # Per-agent serialisation: two concurrent agents.send for the
        # same agent_id used to fetch v1, both append, and the later
        # writer would overwrite the earlier turn (lost-update).  Each
        # send now takes the agent's own lock for the full
        # fetch->mutate->LLM->store cycle.
        self._agent_send_locks: dict[str, asyncio.Lock] = {}
        self._agent_send_locks_guard = asyncio.Lock()

    async def _lock_for_agent(self, agent_id: str) -> asyncio.Lock:
        async with self._agent_send_locks_guard:
            lock = self._agent_send_locks.get(agent_id)
            if lock is None:
                lock = asyncio.Lock()
                self._agent_send_locks[agent_id] = lock
            return lock

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

    async def workspaces_tree(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return a flat, gitignore-respecting file listing of a
        workspace's repo path so the GUI can show operators what
        an agent bound to this workspace can actually see.

        ``params``:
          workspace_id: id of the workspace.
          limit:        max files to return (default 500).
          query:        optional substring filter.

        Listing is hard-capped to ``limit`` files to keep large repos
        responsive.  Honours ``.gitignore`` by shelling out to
        ``git ls-files`` when the path is a git repo; falls back to a
        bounded recursive walk otherwise.
        """
        ws = await self.store.get_workspace(params["workspace_id"])
        if not ws:
            raise ValueError(f"unknown workspace: {params['workspace_id']}")
        limit = max(1, min(int(params.get("limit", 500) or 500), 5000))
        query = (params.get("query") or "").strip().lower()
        repo_path = Path(ws.repo_path)
        if not repo_path.is_dir():
            raise ValueError(f"workspace path does not exist: {ws.repo_path}")
        is_git = (repo_path / ".git").exists()
        files: list[str] = []
        truncated = False
        if is_git:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "ls-files",
                "-co",
                "--exclude-standard",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, _stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=10.0
                )
            except TimeoutError:
                proc.kill()
                with contextlib.suppress(TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(proc.communicate(), timeout=2.0)
                raise ValueError("git ls-files timed out") from None
            for line in stdout_b.decode("utf-8", errors="replace").splitlines():
                if not line:
                    continue
                if query and query not in line.lower():
                    continue
                files.append(line)
                if len(files) >= limit:
                    truncated = True
                    break
        else:
            # Plain directory: bounded walk, skip dotdirs, hard cap.
            seen = 0
            for sub in repo_path.rglob("*"):
                if not sub.is_file():
                    continue
                # Skip anything under a dotdir (e.g. .git, .venv) so we
                # don't enumerate gigabytes of vendored deps.
                if any(p.startswith(".") for p in sub.relative_to(repo_path).parts[:-1]):
                    continue
                rel = str(sub.relative_to(repo_path))
                if query and query not in rel.lower():
                    seen += 1
                    if seen > limit * 10:
                        truncated = True
                        break
                    continue
                files.append(rel)
                if len(files) >= limit:
                    truncated = True
                    break
        return {
            "workspace_id": ws.id,
            "repo_path": ws.repo_path,
            "is_git": is_git,
            "files": files,
            "truncated": truncated,
            "count": len(files),
        }

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

        # The RPC server is loopback-only, but a malicious local process
        # could still POST to it.  Resolve the path and confirm it points
        # at a regular file with an audio-shaped extension before handing
        # it to the transcriber.
        allowed_exts = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}
        raw = params.get("audio_path")
        if not isinstance(raw, str) or not raw:
            raise ValueError("audio_path required")
        path = Path(raw).resolve(strict=False)
        if not path.is_file():
            raise ValueError(f"audio_path is not a regular file: {path}")
        if path.suffix.lower() not in allowed_exts:
            raise ValueError(
                f"audio_path extension {path.suffix!r} not in allowed set {sorted(allowed_exts)}"
            )
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
            is_draft=bool(params.get("is_draft", False)),
        )
        await self.store.insert_flow(flow)
        return {"id": flow.id}

    async def flows_update(self, params: dict[str, Any]) -> dict[str, Any]:
        from apps.service.store.events import FlowVersionConflict

        flow = await self.store.get_flow(params["id"])
        if not flow:
            raise ValueError(f"unknown flow: {params['id']}")
        # Optional optimistic-concurrency token.  GUI passes the version
        # it last fetched; if another writer has bumped the row in the
        # meantime, the update is rejected and the GUI should re-fetch.
        expected_version = params.get("expected_version")
        flow.name = params.get("name", flow.name)
        flow.description = params.get("description", flow.description)
        flow.nodes = params.get("nodes", flow.nodes)
        flow.edges = params.get("edges", flow.edges)
        if "is_draft" in params:
            flow.is_draft = bool(params["is_draft"])
        flow.updated_at = utc_now()
        try:
            await self.store.update_flow(flow, expected_version=expected_version)
        except FlowVersionConflict as exc:
            raise ValueError(
                f"flow {flow.id} has been modified by another writer; reload before saving"
            ) from exc
        return {"id": flow.id, "version": flow.version + 1}

    async def flows_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.store.delete_flow(params["id"])
        return {"deleted": bool(ok)}

    async def flows_dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        flow = await self.store.get_flow(params["flow_id"])
        if not flow:
            raise ValueError(f"unknown flow: {params['flow_id']}")
        if flow.is_draft:
            raise ValueError(
                "flow is in draft mode — flip the Draft toggle off "
                "in the Canvas toolbar to promote it to Live first."
            )
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

    async def _enrich_agent(self, agent: Agent) -> dict[str, Any]:
        """Convert an Agent to a JSON dict and decorate it with the
        bound workspace's name + path so the GUI can render the repo
        chip without a second round-trip.
        """
        d = agent.model_dump(mode="json")
        if agent.workspace_id:
            ws = await self.store.get_workspace(agent.workspace_id)
            if ws is not None:
                d["workspace_name"] = ws.name
                d["workspace_path"] = ws.repo_path
        return d

    async def _enrich_agents(self, agents: list[Agent]) -> list[dict[str, Any]]:
        # Pull all workspaces once instead of N round-trips for N agents.
        ws_rows = await self.store.list_workspaces()
        by_id = {w.id: w for w in ws_rows}
        out: list[dict[str, Any]] = []
        for a in agents:
            d = a.model_dump(mode="json")
            if a.workspace_id and a.workspace_id in by_id:
                d["workspace_name"] = by_id[a.workspace_id].name
                d["workspace_path"] = by_id[a.workspace_id].repo_path
            out.append(d)
        return out

    async def agents_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._enrich_agents(await self.store.list_agents())

    async def agents_get(self, params: dict[str, Any]) -> dict[str, Any]:
        agent = await self.store.get_agent(params["id"])
        if not agent:
            raise ValueError(f"unknown agent: {params['id']}")
        return await self._enrich_agent(agent)

    async def agents_create(self, params: dict[str, Any]) -> dict[str, Any]:
        refs = params.get("reference_agent_ids") or []
        if not isinstance(refs, list):
            refs = []
        workspace_id = params.get("workspace_id")
        # Validate the workspace up-front so the GUI gets a clear error
        # instead of a per-send failure later.
        if workspace_id:
            ws = await self.store.get_workspace(workspace_id)
            if not ws:
                raise ValueError(f"unknown workspace: {workspace_id}")
        agent = Agent(
            name=(params.get("name") or "Unnamed agent").strip(),
            provider=params["provider"],
            model=params["model"],
            system=params.get("system", ""),
            reference_agent_ids=[str(r) for r in refs if r],
            workspace_id=workspace_id,
        )
        await self.store.insert_agent(agent)
        return await self._enrich_agent(agent)

    async def agents_set_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        """Bind / unbind an agent to a Workspace (project repo).

        ``params``:
          agent_id:     id of the agent to update.
          workspace_id: id of the workspace, or null to detach.
        """
        agent = await self.store.get_agent(params["agent_id"])
        if not agent:
            raise ValueError(f"unknown agent: {params['agent_id']}")
        ws_id = params.get("workspace_id")
        if ws_id:
            ws = await self.store.get_workspace(ws_id)
            if not ws:
                raise ValueError(f"unknown workspace: {ws_id}")
        agent.workspace_id = ws_id or None
        await self.store.update_agent(agent)
        return await self._enrich_agent(agent)

    async def agents_set_references(self, params: dict[str, Any]) -> dict[str, Any]:
        """Replace an existing agent's reference_agent_ids list.

        ``params``:
          agent_id: id of the agent to update.
          reference_agent_ids: list of agent ids to inline as context
            on every subsequent send.  Empty list = no context.
        """
        agent = await self.store.get_agent(params["agent_id"])
        if not agent:
            raise ValueError(f"unknown agent: {params['agent_id']}")
        refs = params.get("reference_agent_ids") or []
        if not isinstance(refs, list):
            refs = []
        agent.reference_agent_ids = [str(r) for r in refs if r and r != agent.id]
        await self.store.update_agent(agent)
        return await self._enrich_agent(agent)

    async def agents_send(self, params: dict[str, Any]) -> dict[str, Any]:
        """Append the user's message to the agent's transcript and
        get a reply.  Multi-turn — every turn after the first builds
        on the prior transcript.

        Optional ``attachment_ids`` selects previously-uploaded files
        to attach to this turn; spreadsheets get inlined as markdown
        tables and images get passed through to the CLI as path refs
        (handled inside each provider).
        """
        from apps.service.providers.registry import get_provider

        agent_id = params["agent_id"]
        message = params["message"]
        attachment_ids = list(params.get("attachment_ids") or [])
        # Serialise sends per agent — without this lock two concurrent
        # turns lose one user/assistant pair (last writer wins).
        async with await self._lock_for_agent(agent_id):
            agent = await self.store.get_agent(agent_id)
            if not agent:
                raise ValueError(f"unknown agent: {agent_id}")
            new_turn_index = len(agent.transcript)
            agent.transcript.append({"role": "user", "content": message})

            # Resolve any attachments the operator picked for this turn.
            attachments: list[Attachment] = []
            if attachment_ids:
                attachments = await self.store.get_attachments_by_ids(attachment_ids)
                # Reject foreign attachments — every id must belong to
                # this agent so we can't be tricked into reading another
                # agent's files.
                for a in attachments:
                    if a.agent_id != agent_id:
                        raise ValueError(
                            f"attachment {a.id} belongs to a different agent"
                        )

            # Fold transcript + system into a single prompt for the CLI
            # adapter (which doesn't accept structured messages in
            # headless mode).  Fresh providers keep their own session
            # state per call; persisting it in our store is the
            # source-of-truth.
            # Pull every referenced agent the operator wired up and
            # prepend their transcripts as a context preamble.  Cross-
            # provider safe: Gemini reading a Claude transcript or vice
            # versa just sees plain text, so no special-casing.
            refs: list[Agent] = []
            ref_attachments: dict[str, list[Attachment]] = {}
            for ref_id in agent.reference_agent_ids or []:
                ref = await self.store.get_agent(ref_id)
                if ref is not None:
                    refs.append(ref)
                    ref_attachments[ref.id] = await self.store.list_attachments(ref.id)
            reference_block = _render_references(refs, ref_attachments)
            prompt = _render_transcript(agent)
            if reference_block:
                prompt = reference_block + "\n\n" + prompt

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

            # Repo-aware path: if the agent is bound to a Workspace,
            # spawn the CLI subprocess with cwd=<repo_path> so the
            # model's built-in Read / Bash / Edit tools operate against
            # the project, and prepend a system-prompt header so the
            # model knows what it's looking at.
            cwd: str | None = None
            system_prompt = agent.system or None
            if agent.workspace_id:
                ws = await self.store.get_workspace(agent.workspace_id)
                if ws is not None:
                    cwd = ws.repo_path
                    repo_header = (
                        f"You are operating inside the project at {ws.repo_path} "
                        f"(workspace name: {ws.name}).  Use your built-in file "
                        f"tools (Read / Bash / Edit / Grep) to browse and modify "
                        f"the repository as needed."
                    )
                    system_prompt = (
                        repo_header
                        if not system_prompt
                        else f"{repo_header}\n\n{system_prompt}"
                    )

            session = await provider.open_chat(card, system=system_prompt, cwd=cwd)
            chunks: list[str] = []
            # Fold spreadsheet attachments into the prompt body (the CLI
            # adapter has no path concept for tabular data); images
            # remain as Attachment objects passed via send(...).
            prompt_with_sheets = _inline_spreadsheet_attachments(prompt, attachments)
            image_attachments = [
                a for a in attachments if a.kind == AttachmentKind.IMAGE
            ]
            try:
                async for ev in session.send(
                    prompt_with_sheets, attachments=image_attachments
                ):
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
            # Bind each attachment to the user-turn we just appended so
            # the GUI can show "this turn had X attached".
            for a in attachments:
                await self.store.update_attachment_turn(a.id, new_turn_index)
            # Record the send for the in-app message-tally counter so the
            # Limits tab can show "X / cap" against the published plan
            # caps without having to ask the CLI.
            await self.store.record_provider_message(agent.provider, agent.model)
            return {"reply": reply, "agent": await self._enrich_agent(agent)}

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
        return {"agent": await self._enrich_agent(agent)}

    async def agents_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params["id"]
        ok = await self.store.delete_agent(agent_id)
        # Drop the per-agent send lock so the dict doesn't grow forever.
        async with self._agent_send_locks_guard:
            self._agent_send_locks.pop(agent_id, None)
        return {"deleted": bool(ok)}

    async def agents_followup_presets(self, params: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"key": key, "label": label, "instruction": body}
            for key, (label, body) in FOLLOWUP_PRESETS.items()
        ]

    # ------------------------------------------------------------------
    # Attachments — file uploads bound to an Agent
    # ------------------------------------------------------------------

    async def attachments_upload(self, params: dict[str, Any]) -> dict[str, Any]:
        """Persist an uploaded file, render it (markdown table for
        spreadsheets, optional resize for images) and index a row.

        Params:
            agent_id:      target agent (must already exist)
            original_name: filename the user chose
            content_b64:   base64-encoded bytes
        """
        import base64
        import tempfile

        from apps.service.attachments import (
            AttachmentRenderError,
            classify_kind,
            render_attachment,
            sanitize_filename,
        )

        agent_id = params["agent_id"]
        agent = await self.store.get_agent(agent_id)
        if not agent:
            raise ValueError(f"unknown agent: {agent_id}")

        original_name = str(params.get("original_name") or "upload")
        try:
            raw = base64.b64decode(params["content_b64"], validate=True)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"content_b64 missing or not valid base64: {exc}") from exc
        if not raw:
            raise ValueError("attachment is empty")

        # Decide kind from extension; reject unknown types up front so
        # the operator gets a useful error.
        kind = classify_kind(Path(original_name))
        if not kind:
            raise ValueError(
                f"unsupported file type {Path(original_name).suffix!r}; "
                "supported: images (.png/.jpg/.gif/.webp) and spreadsheets (.xlsx/.xls/.csv)"
            )

        attachment_id = long_id()
        agent_dir = self.attachments_dir / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        safe_name = sanitize_filename(original_name)
        stored_path = agent_dir / f"{attachment_id}__{safe_name}"

        # Render goes via a temp file so we get a uniform read-from-Path
        # API even though the caller hands us bytes.  Cleanup is by
        # context.
        with tempfile.NamedTemporaryFile(
            "wb", delete=False, suffix=Path(safe_name).suffix
        ) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        render_warning: str | None = None
        try:
            try:
                result = render_attachment(tmp_path, dest=stored_path, kind=kind)
            except AttachmentRenderError as exc:
                # Render failed but we still want the bytes available so
                # the operator can retry with another tool.  Persist raw
                # and surface a warning in the response.
                stored_path.write_bytes(raw)
                render_warning = str(exc)
                from apps.service.attachments.render import RenderResult

                result = RenderResult(
                    rendered_text=None,
                    mime_type="application/octet-stream",
                    bytes_written=len(raw),
                )
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        attachment = Attachment(
            id=attachment_id,
            agent_id=agent_id,
            kind=AttachmentKind(kind),
            original_name=original_name,
            stored_path=str(stored_path),
            mime_type=result.mime_type,
            bytes=result.bytes_written,
            rendered_text=result.rendered_text,
        )
        await self.store.insert_attachment(attachment)
        out = attachment.model_dump(mode="json")
        if render_warning:
            out["warning"] = render_warning
        return out

    async def attachments_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        agent_id = params["agent_id"]
        rows = await self.store.list_attachments(agent_id)
        return [a.model_dump(mode="json") for a in rows]

    async def attachments_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        attachment_id = params["id"]
        attachment = await self.store.delete_attachment(attachment_id)
        if attachment is None:
            return {"deleted": False}
        # Best-effort filesystem cleanup; missing file is fine (operator
        # may have wiped the data dir).
        try:
            Path(attachment.stored_path).unlink()
        except OSError:
            log.debug("attachment file already gone: %s", attachment.stored_path)
        return {"deleted": True}

    async def limits_check(self, params: dict[str, Any]) -> dict[str, Any]:
        """Probe every locally-installed CLI for whatever subscription
        / usage info it exposes.  Returns a structured dict the GUI's
        Limits tab renders as one card per provider.

        Per-message remaining-quota numbers are not reliably available
        headlessly for either Claude Code or Gemini CLI — both gate
        that behind their interactive `/status` flow.  We surface
        whatever each binary returns from its public status commands
        plus links to the official dashboards so the operator can
        always drill in.
        """
        import shutil

        async def _run(args: list[str], timeout: float = 15.0) -> dict[str, Any]:
            binary = shutil.which(args[0])
            if not binary:
                return {"ok": False, "stdout": "", "stderr": "not found on PATH", "exit": -1}
            try:
                proc = await asyncio.create_subprocess_exec(
                    binary,
                    *args[1:],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                except TimeoutError:
                    proc.kill()
                    # Reap the child so we don't leave a zombie behind.
                    # communicate() also closes the pipes for us.
                    try:
                        await asyncio.wait_for(proc.communicate(), timeout=2.0)
                    except (TimeoutError, ProcessLookupError):
                        pass
                    return {
                        "ok": False,
                        "stdout": "",
                        "stderr": f"timed out after {timeout}s",
                        "exit": -2,
                    }
            except Exception as exc:
                return {"ok": False, "stdout": "", "stderr": str(exc), "exit": -3}
            return {
                "ok": (proc.returncode or 0) == 0,
                "stdout": stdout_b.decode("utf-8", errors="replace").strip(),
                "stderr": stderr_b.decode("utf-8", errors="replace").strip(),
                "exit": proc.returncode or 0,
            }

        # Claude Code: try `--version` (always works) then optionally
        # `status` (newer versions).  We don't try the interactive
        # `/status` slash command headlessly because it would never
        # return.
        claude_version = await _run(["claude", "--version"])
        claude_status = (
            await _run(["claude", "status"], timeout=8.0)
            if claude_version["ok"]
            else {
                "ok": False,
                "stdout": "",
                "stderr": "claude not on PATH",
                "exit": -1,
            }
        )
        # Gemini CLI: same pattern.
        gemini_version = await _run(["gemini", "--version"])
        gemini_status = (
            await _run(["gemini", "status"], timeout=8.0)
            if gemini_version["ok"]
            else {
                "ok": False,
                "stdout": "",
                "stderr": "gemini not on PATH",
                "exit": -1,
            }
        )

        from apps.service.limits import (
            DATA_AS_OF,
            claude_plans,
            context_windows,
            gemini_plans,
        )

        return {
            "data_as_of": DATA_AS_OF,
            "context_windows": context_windows(),
            "providers": [
                {
                    "id": "claude-cli",
                    "label": "Claude Code (Pro / Max plan)",
                    "version": claude_version,
                    "status": claude_status,
                    "plans": claude_plans(),
                    "dashboards": [
                        {
                            "label": "Pro / Max usage dashboard",
                            "url": "https://claude.ai/settings/usage",
                        },
                        {"label": "Subscription page", "url": "https://claude.ai/settings"},
                    ],
                    "note": (
                        "Per-message remaining-count isn't returned "
                        "headlessly.  Caps below are the published plan "
                        "limits; the dashboards above show live usage."
                    ),
                },
                {
                    "id": "gemini-cli",
                    "label": "Gemini CLI",
                    "version": gemini_version,
                    "status": gemini_status,
                    "plans": gemini_plans(),
                    "dashboards": [
                        {"label": "Gemini app + plan", "url": "https://gemini.google.com/"},
                        {
                            "label": "AI Studio (API keys)",
                            "url": "https://aistudio.google.com/app/apikey",
                        },
                    ],
                    "note": (
                        "Headless quota readout isn't documented.  Caps "
                        "below are the published plan limits; the "
                        "dashboards above show live usage."
                    ),
                },
            ],
        }

    async def limits_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        """Local message-send tally per provider.

        Returns rolling counts for the three windows operators care
        about: 5 hours (Claude Pro/Max session window), 24 hours
        (Gemini daily) and 7 days (Claude weekly).  Counts come from
        the ``provider_messages`` table populated by every successful
        agents.send.  Independent of any CLI status command — this
        is what *we* observed.
        """
        from datetime import timedelta

        from apps.service.types import utc_now

        now = utc_now()
        windows = {
            "5h": (now - timedelta(hours=5)).isoformat(),
            "24h": (now - timedelta(hours=24)).isoformat(),
            "7d": (now - timedelta(days=7)).isoformat(),
        }
        out: dict[str, dict[str, int]] = {}
        for provider in ("claude-cli", "gemini-cli"):
            counts: dict[str, int] = {}
            for label, since_iso in windows.items():
                counts[label] = await self.store.count_provider_messages(provider, since_iso)
            out[provider] = counts
        return {"providers": out, "checked_at": now.isoformat()}

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
    server.register("workspaces.tree", h.workspaces_tree)
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
    server.register("agents.set_references", h.agents_set_references)
    server.register("agents.set_workspace", h.agents_set_workspace)
    server.register("agents.delete", h.agents_delete)
    server.register("agents.followup_presets", h.agents_followup_presets)
    server.register("attachments.upload", h.attachments_upload)
    server.register("attachments.list", h.attachments_list)
    server.register("attachments.delete", h.attachments_delete)
    server.register("providers", h.providers)
    server.register("hook.received", h.hook_received)
    server.register("limits.check", h.limits_check)
    server.register("limits.usage", h.limits_usage)
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
    handlers = Handlers(store, manager, dispatcher, data_dir=data_dir)

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
