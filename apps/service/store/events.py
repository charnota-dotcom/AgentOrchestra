"""Async SQLite store for AgentOrchestra.

Wraps `aiosqlite` with a single connection per service process. Owns:

- Schema migration (idempotent on startup)
- Event append + sequence assignment
- CRUD for workspaces, cards, templates, instructions, runs, branches,
  steps, artifacts, approvals, outcomes
- FTS5 full-text indexing of instructions, artifacts, and salient event text

This is the single source of truth for everything the GUI reads.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from apps.service.types import (
    Approval,
    Artifact,
    BlueprintVersionConflict,
    Branch,
    BranchState,
    DroneAction,
    DroneBlueprint,
    DroneRole,
    Event,
    Flow,
    FlowRun,
    FlowState,
    Instruction,
    InstructionTemplate,
    Outcome,
    PersonalityCard,
    Run,
    RunState,
    Skill,
    Step,
    Workspace,
    utc_now,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
log = logging.getLogger(__name__)


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class FlowVersionConflict(Exception):
    """Raised when an optimistic update_flow lost the race; the caller
    needs to re-fetch the flow and reapply their edits."""


class EventStore:
    """Owns the SQLite database for one running service.

    All writes go through `append_event` so the event log is the
    canonical timeline.  The materialized tables (runs, branches,
    etc.) are updated atomically with the corresponding event in a
    single transaction.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._db: aiosqlite.Connection | None = None
        # Optional callback fired after each successful append.  The
        # service entrypoint hooks the EventBus into this so live UIs
        # see events the moment they land.
        self.on_append: Callable[[Event], None] | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._migrate()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("EventStore not opened")
        return self._db

    async def _migrate(self) -> None:
        sql = SCHEMA_PATH.read_text()
        # executescript wraps in its own transaction
        await self.db.executescript(sql)
        await self.db.commit()
        # One-shot drop of the legacy Agent path.  The operator chose
        # "drop the tables on next startup" during the rip-out planning
        # — see docs/DRONE_MODEL.md.  Idempotent: DROP IF EXISTS is a
        # no-op once the rows are gone.
        for legacy in ("attachments", "agents"):
            await self.db.execute(f"DROP TABLE IF EXISTS {legacy}")
        # Additive column migrations.  CREATE TABLE IF NOT EXISTS leaves
        # existing tables untouched; for already-deployed DBs we need
        # explicit ALTERs so the new fields appear on the existing
        # tables too.  Each ALTER is idempotent via a column-presence
        # probe — re-running the migration on an already-patched DB is
        # a no-op.  See docs/BROWSER_PROVIDER_PLAN.md (PR 2).
        await self._add_column_if_missing("drone_blueprints", "chat_url", "TEXT")
        await self._add_column_if_missing("drone_actions", "bound_chat_url", "TEXT")
        await self._add_column_if_missing("drone_actions", "name", "TEXT")
        await self._add_column_if_missing("flows", "is_flight", "INTEGER NOT NULL DEFAULT 0")
        await self.db.commit()

        # Seed initial skills if the table is empty.
        await self.seed_initial_skills()

    async def seed_initial_skills(self) -> None:
        """Populate the 20 popular agent skill templates if none exist."""
        from apps.service.types import AGENT_SKILLS

        async with self._lock:
            cur = await self.db.execute("SELECT COUNT(*) FROM skills")
            row = await cur.fetchone()
            if row and row[0] > 0:
                return

            for name, description in AGENT_SKILLS:
                skill = Skill(name=name, description=description)
                await self.db.execute(
                    """
                    INSERT INTO skills (id, name, description, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        skill.id,
                        skill.name,
                        skill.description,
                        skill.created_at.isoformat(),
                        skill.updated_at.isoformat(),
                    ),
                )
            await self.db.commit()

    async def _add_column_if_missing(self, table: str, column: str, decl: str) -> None:
        """Idempotent ``ALTER TABLE table ADD COLUMN column decl`` —
        no-op when the column already exists.  Decl is the type plus
        any constraints (e.g. ``"TEXT NOT NULL DEFAULT ''"``); SQLite
        requires a default when adding a NOT NULL column to a non-empty
        table, so callers must include one for non-nullable adds.
        """
        cur = await self.db.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        existing = {r["name"] for r in rows}
        if column in existing:
            return
        await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ------------------------------------------------------------------
    # Event append
    # ------------------------------------------------------------------

    async def append_event(self, event: Event) -> Event:
        async with self._lock:
            cur = await self.db.execute("SELECT next FROM event_seq WHERE id = 1")
            row = await cur.fetchone()
            assert row is not None
            seq = int(row["next"])
            await self.db.execute(
                "UPDATE event_seq SET next = next + 1 WHERE id = 1",
            )
            event.seq = seq
            await self.db.execute(
                """
                INSERT INTO events (id, seq, occurred_at, source, kind,
                    run_id, step_id, branch_id, workspace_id, payload, text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.seq,
                    event.occurred_at.isoformat(),
                    event.source.value,
                    event.kind.value,
                    event.run_id,
                    event.step_id,
                    event.branch_id,
                    event.workspace_id,
                    json.dumps(event.payload),
                    event.text,
                ),
            )
            if event.text:
                await self._fts_insert("event", event.id, event.kind.value, event.text)
            await self.db.commit()
        if self.on_append is not None:
            try:
                self.on_append(event)
            except Exception:
                log.exception("on_append callback failed")
        return event

    # ------------------------------------------------------------------
    # FTS5
    # ------------------------------------------------------------------

    async def _fts_insert(self, doc_kind: str, doc_id: str, title: str, body: str) -> None:
        await self.db.execute(
            "INSERT INTO search (doc_id, doc_kind, title, body) VALUES (?, ?, ?, ?)",
            (doc_id, doc_kind, title, body),
        )

    async def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return FTS hits ordered by rank (higher = better match)."""
        if not query.strip():
            return []
        cur = await self.db.execute(
            """
            SELECT doc_id, doc_kind, title,
                   snippet(search, 3, '<b>', '</b>', '…', 12) AS snippet,
                   rank
            FROM search
            WHERE search MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    async def insert_workspace(self, ws: Workspace) -> Workspace:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?)",
                (ws.id, ws.name, ws.repo_path, ws.default_base_branch, ws.created_at.isoformat()),
            )
            await self.db.commit()
        return ws

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        cur = await self.db.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
        row = await cur.fetchone()
        return Workspace.model_validate(_row_to_dict(row)) if row else None

    async def list_workspaces(self) -> list[Workspace]:
        cur = await self.db.execute("SELECT * FROM workspaces ORDER BY created_at")
        rows = await cur.fetchall()
        return [Workspace.model_validate(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Flows (visual orchestration)
    # ------------------------------------------------------------------

    async def insert_flow(self, flow: Flow) -> Flow:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO flows VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    flow.id,
                    flow.name,
                    flow.description,
                    json.dumps(
                        {"nodes": flow.nodes, "edges": flow.edges, "is_draft": flow.is_draft}
                    ),
                    flow.version,
                    flow.created_at.isoformat(),
                    flow.updated_at.isoformat(),
                ),
            )
            await self.db.commit()
        return flow

    async def update_flow(self, flow: Flow, *, expected_version: int | None = None) -> Flow:
        # Optimistic concurrency: if the caller passes the version they
        # read, we only commit when the row's current version still
        # matches.  Two canvases saving the same flow concurrently used
        # to silently overwrite each other.
        async with self._lock:
            if expected_version is not None:
                cur = await self.db.execute(
                    "UPDATE flows SET name = ?, description = ?, payload = ?, "
                    "version = version + 1, updated_at = ? "
                    "WHERE id = ? AND version = ?",
                    (
                        flow.name,
                        flow.description,
                        json.dumps(
                            {"nodes": flow.nodes, "edges": flow.edges, "is_draft": flow.is_draft}
                        ),
                        flow.updated_at.isoformat(),
                        flow.id,
                        expected_version,
                    ),
                )
                if (cur.rowcount or 0) == 0:
                    raise FlowVersionConflict(
                        f"flow {flow.id} version {expected_version} no longer current"
                    )
            else:
                await self.db.execute(
                    """
                    UPDATE flows
                       SET name = ?, description = ?, payload = ?, version = version + 1, updated_at = ?
                     WHERE id = ?
                    """,
                    (
                        flow.name,
                        flow.description,
                        json.dumps(
                            {"nodes": flow.nodes, "edges": flow.edges, "is_draft": flow.is_draft}
                        ),
                        flow.updated_at.isoformat(),
                        flow.id,
                    ),
                )
            await self.db.commit()
        return flow

    async def get_flow(self, flow_id: str) -> Flow | None:
        cur = await self.db.execute("SELECT * FROM flows WHERE id = ?", (flow_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        body = json.loads(d.pop("payload"))
        d["nodes"] = body.get("nodes", [])
        d["edges"] = body.get("edges", [])
        d["is_draft"] = bool(body.get("is_draft", False))
        return Flow.model_validate(d)

    async def list_flows(self) -> list[Flow]:
        cur = await self.db.execute("SELECT * FROM flows ORDER BY updated_at DESC")
        rows = await cur.fetchall()
        out: list[Flow] = []
        for r in rows:
            d = dict(r)
            body = json.loads(d.pop("payload"))
            d["nodes"] = body.get("nodes", [])
            d["edges"] = body.get("edges", [])
            d["is_draft"] = bool(body.get("is_draft", False))
            out.append(Flow.model_validate(d))
        return out

    # ------------------------------------------------------------------
    # Provider-side message tally — local count of successful sends per
    # provider, used by the Limits tab to show "X / cap" against the
    # published plan limits without hitting a CLI status command.
    # ------------------------------------------------------------------

    async def record_provider_message(self, provider: str, model: str = "") -> None:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO provider_messages (provider, model, sent_at) VALUES (?, ?, ?)",
                (provider, model, utc_now().isoformat()),
            )
            await self.db.commit()

    async def count_provider_messages(self, provider: str, since_iso: str) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM provider_messages WHERE provider = ? AND sent_at >= ?",
            (provider, since_iso),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def delete_flow(self, flow_id: str) -> bool:
        # Cascade to runs first so the foreign key check passes.  Hold
        # the write lock for both DELETEs so a concurrent insert_flow_run
        # can't sneak in between them.
        async with self._lock:
            await self.db.execute("DELETE FROM flow_runs WHERE flow_id = ?", (flow_id,))
            cur = await self.db.execute("DELETE FROM flows WHERE id = ?", (flow_id,))
            await self.db.commit()
        return (cur.rowcount or 0) > 0

    async def insert_flow_run(self, run: FlowRun) -> FlowRun:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.flow_id,
                    run.state.value,
                    run.started_at.isoformat(),
                    run.ended_at.isoformat() if run.ended_at else None,
                    json.dumps({"node_outputs": run.node_outputs, "error": run.error}),
                ),
            )
            await self.db.commit()
        return run

    async def update_flow_run(self, run: FlowRun) -> FlowRun:
        async with self._lock:
            await self.db.execute(
                """
                UPDATE flow_runs
                   SET state = ?, ended_at = ?, payload = ?
                 WHERE id = ?
                """,
                (
                    run.state.value,
                    run.ended_at.isoformat() if run.ended_at else None,
                    json.dumps({"node_outputs": run.node_outputs, "error": run.error}),
                    run.id,
                ),
            )
            await self.db.commit()
        return run

    async def get_flow_run(self, run_id: str) -> FlowRun | None:
        cur = await self.db.execute("SELECT * FROM flow_runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        body = json.loads(d.pop("payload"))
        d["state"] = FlowState(d["state"])
        d["node_outputs"] = body.get("node_outputs") or {}
        d["error"] = body.get("error")
        return FlowRun.model_validate(d)

    async def delete_workspace(self, workspace_id: str) -> bool:
        """Remove a workspace.  Runs are kept (workspace_id stays set)
        so historical context isn't lost; only the workspace row goes
        away.  Returns True if a row was removed.
        """
        async with self._lock:
            cur = await self.db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            await self.db.commit()
        return (cur.rowcount or 0) > 0

    # ------------------------------------------------------------------
    # Templates & Cards
    # ------------------------------------------------------------------

    async def insert_template(self, t: InstructionTemplate) -> InstructionTemplate:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO templates (id, name, archetype, body, variables,
                    version, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    t.id,
                    t.name,
                    t.archetype,
                    t.body,
                    json.dumps([v.model_dump() for v in t.variables]),
                    t.version,
                    t.content_hash,
                    t.created_at.isoformat(),
                ),
            )
            await self.db.commit()
        return t

    async def get_template(self, template_id: str) -> InstructionTemplate | None:
        cur = await self.db.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["variables"] = json.loads(d["variables"])
        return InstructionTemplate.model_validate(d)

    async def insert_card(self, c: PersonalityCard) -> PersonalityCard:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO cards (id, name, archetype, description, template_id,
                    provider, model, mode, cost, blast_radius, sandbox_tier,
                    tool_allowlist, fallbacks, auto_qa, requires_plan,
                    stale_minutes, max_commits_per_run, max_turns,
                    skip_pre_commit_hooks, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    c.id,
                    c.name,
                    c.archetype,
                    c.description,
                    c.template_id,
                    c.provider,
                    c.model,
                    c.mode.value,
                    c.cost.model_dump_json(),
                    c.blast_radius.model_dump_json(),
                    c.sandbox_tier.value,
                    json.dumps(c.tool_allowlist),
                    json.dumps(c.fallbacks),
                    int(c.auto_qa),
                    int(c.requires_plan),
                    c.stale_minutes,
                    c.max_commits_per_run,
                    c.max_turns,
                    int(c.skip_pre_commit_hooks),
                    c.version,
                    c.created_at.isoformat(),
                    c.updated_at.isoformat(),
                ),
            )
            await self.db.commit()
        return c

    @staticmethod
    def _hydrate_card(row: aiosqlite.Row) -> PersonalityCard:
        d = dict(row)
        d["cost"] = json.loads(d["cost"])
        d["blast_radius"] = json.loads(d["blast_radius"])
        d["tool_allowlist"] = json.loads(d["tool_allowlist"])
        d["skip_pre_commit_hooks"] = bool(d["skip_pre_commit_hooks"])
        # Backwards-compat for DBs that predate later-added columns.
        d.setdefault("mode", "chat")
        d.setdefault("max_turns", 12)
        if d.get("fallbacks"):
            d["fallbacks"] = json.loads(d["fallbacks"])
        else:
            d["fallbacks"] = []
        d["auto_qa"] = bool(d.get("auto_qa", 0))
        d["requires_plan"] = bool(d.get("requires_plan", 0))
        return PersonalityCard.model_validate(d)

    async def list_cards(self) -> list[PersonalityCard]:
        cur = await self.db.execute("SELECT * FROM cards ORDER BY archetype, name")
        rows = await cur.fetchall()
        return [self._hydrate_card(r) for r in rows]

    async def get_card(self, card_id: str) -> PersonalityCard | None:
        cur = await self.db.execute("SELECT * FROM cards WHERE id = ?", (card_id,))
        row = await cur.fetchone()
        return self._hydrate_card(row) if row else None

    # ------------------------------------------------------------------
    # Instructions
    # ------------------------------------------------------------------

    async def insert_instruction(self, ins: Instruction) -> Instruction:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO instructions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ins.id,
                    ins.template_id,
                    ins.template_version,
                    ins.card_id,
                    ins.rendered_text,
                    json.dumps(ins.variables),
                    ins.created_at.isoformat(),
                ),
            )
            await self._fts_insert("instruction", ins.id, "instruction", ins.rendered_text)
            await self.db.commit()
        return ins

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def insert_run(self, run: Run) -> Run:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO runs (id, workspace_id, card_id, instruction_id,
                    branch_id, state, state_changed_at, created_at,
                    completed_at, cost_usd, cost_tokens, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    run.card_id,
                    run.instruction_id,
                    run.branch_id,
                    run.state.value,
                    run.state_changed_at.isoformat(),
                    run.created_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.cost_usd,
                    run.cost_tokens,
                    run.error,
                ),
            )
            await self.db.commit()
        return run

    async def update_run_state(self, run_id: str, state: RunState) -> None:
        async with self._lock:
            await self.db.execute(
                "UPDATE runs SET state = ?, state_changed_at = ? WHERE id = ?",
                (state.value, utc_now().isoformat(), run_id),
            )
            await self.db.commit()

    async def get_run(self, run_id: str) -> Run | None:
        cur = await self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        return Run.model_validate(_row_to_dict(row)) if row else None

    async def list_runs(self, *, workspace_id: str | None = None, limit: int = 100) -> list[Run]:
        if workspace_id:
            cur = await self.db.execute(
                "SELECT * FROM runs WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?",
                (workspace_id, limit),
            )
        else:
            cur = await self.db.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cur.fetchall()
        return [Run.model_validate(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------

    async def insert_branch(self, b: Branch) -> Branch:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO branches (id, run_id, workspace_id, base_ref,
                    base_branch_name, agent_branch_name, worktree_path,
                    state, state_changed_at, created_at,
                    last_commit_sha, last_commit_at, process_pid,
                    include_uncommitted, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    b.id,
                    b.run_id,
                    b.workspace_id,
                    b.base_ref,
                    b.base_branch_name,
                    b.agent_branch_name,
                    b.worktree_path,
                    b.state.value,
                    b.state_changed_at.isoformat(),
                    b.created_at.isoformat(),
                    b.last_commit_sha,
                    b.last_commit_at.isoformat() if b.last_commit_at else None,
                    b.process_pid,
                    int(b.include_uncommitted),
                    b.notes,
                ),
            )
            await self.db.commit()
        return b

    async def update_branch_state(
        self, branch_id: str, state: BranchState, *, last_commit_sha: str | None = None
    ) -> None:
        async with self._lock:
            if last_commit_sha is not None:
                await self.db.execute(
                    """UPDATE branches SET state = ?, state_changed_at = ?,
                       last_commit_sha = ?, last_commit_at = ? WHERE id = ?""",
                    (
                        state.value,
                        utc_now().isoformat(),
                        last_commit_sha,
                        utc_now().isoformat(),
                        branch_id,
                    ),
                )
            else:
                await self.db.execute(
                    "UPDATE branches SET state = ?, state_changed_at = ? WHERE id = ?",
                    (state.value, utc_now().isoformat(), branch_id),
                )
            await self.db.commit()

    async def get_branch(self, branch_id: str) -> Branch | None:
        cur = await self.db.execute("SELECT * FROM branches WHERE id = ?", (branch_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["include_uncommitted"] = bool(d["include_uncommitted"])
        return Branch.model_validate(d)

    async def list_branches_by_state(
        self, *, workspace_id: str | None = None, states: Iterable[BranchState] | None = None
    ) -> list[Branch]:
        clauses = []
        params: list[Any] = []
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if states:
            qmarks = ",".join("?" for _ in states)
            clauses.append(f"state IN ({qmarks})")
            params.extend(s.value for s in states)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = await self.db.execute(
            f"SELECT * FROM branches {where} ORDER BY created_at",
            params,
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["include_uncommitted"] = bool(d["include_uncommitted"])
            out.append(Branch.model_validate(d))
        return out

    # ------------------------------------------------------------------
    # Steps & Artifacts
    # ------------------------------------------------------------------

    async def insert_step(self, s: Step) -> Step:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO steps (id, run_id, seq, kind, started_at,
                    completed_at, tokens_in, tokens_out, cost_usd, latency_ms, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    s.id,
                    s.run_id,
                    s.seq,
                    s.kind.value,
                    s.started_at.isoformat(),
                    s.completed_at.isoformat() if s.completed_at else None,
                    s.tokens_in,
                    s.tokens_out,
                    s.cost_usd,
                    s.latency_ms,
                    json.dumps(s.payload),
                ),
            )
            await self.db.commit()
        return s

    async def insert_artifact(self, a: Artifact) -> Artifact:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    a.id,
                    a.run_id,
                    a.step_id,
                    a.kind.value,
                    a.title,
                    a.body,
                    a.created_at.isoformat(),
                ),
            )
            await self._fts_insert("artifact", a.id, a.title, a.body)
            await self.db.commit()
        return a

    # ------------------------------------------------------------------
    # Approvals & Outcomes
    # ------------------------------------------------------------------

    async def insert_approval(self, ap: Approval) -> Approval:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ap.id,
                    ap.run_id,
                    ap.reason,
                    json.dumps(ap.risk_signals),
                    ap.decision.value,
                    ap.requested_at.isoformat(),
                    ap.decided_at.isoformat() if ap.decided_at else None,
                    ap.decided_by,
                    ap.note,
                ),
            )
            await self.db.commit()
        return ap

    async def insert_outcome(self, o: Outcome) -> Outcome:
        async with self._lock:
            await self.db.execute(
                "INSERT INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    o.id,
                    o.run_id,
                    o.kind.value,
                    o.rationale,
                    o.final_cost_usd,
                    o.final_cost_tokens,
                    o.duration_seconds,
                    o.created_at.isoformat(),
                ),
            )
            await self.db.commit()
        return o

    # ------------------------------------------------------------------
    # Drones — see docs/DRONE_MODEL.md.
    #
    # Blueprint = operator-set frozen template.
    # Action    = deployed instance carrying live state.
    # ------------------------------------------------------------------

    async def insert_drone_blueprint(self, bp: DroneBlueprint) -> DroneBlueprint:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO drone_blueprints (
                    id, name, description, role, provider, model,
                    system_persona, skills, reference_blueprint_ids,
                    chat_url, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bp.id,
                    bp.name,
                    bp.description,
                    bp.role.value,
                    bp.provider,
                    bp.model,
                    bp.system_persona,
                    json.dumps(bp.skills),
                    json.dumps(bp.reference_blueprint_ids),
                    bp.chat_url,
                    bp.version,
                    bp.created_at.isoformat(),
                    bp.updated_at.isoformat(),
                ),
            )
            await self.db.commit()
        return bp

    async def update_drone_blueprint(
        self, bp: DroneBlueprint, *, expected_version: int | None = None
    ) -> DroneBlueprint:
        """Optimistic-concurrency update.  Pass the version you read to
        guard against racing edits.  Same pattern as ``update_flow``.
        """
        bp.updated_at = utc_now()
        async with self._lock:
            if expected_version is not None:
                cur = await self.db.execute(
                    """
                    UPDATE drone_blueprints
                       SET name = ?, description = ?, role = ?, provider = ?,
                           model = ?, system_persona = ?, skills = ?,
                           reference_blueprint_ids = ?, chat_url = ?,
                           version = version + 1, updated_at = ?
                     WHERE id = ? AND version = ?
                    """,
                    (
                        bp.name,
                        bp.description,
                        bp.role.value,
                        bp.provider,
                        bp.model,
                        bp.system_persona,
                        json.dumps(bp.skills),
                        json.dumps(bp.reference_blueprint_ids),
                        bp.chat_url,
                        bp.updated_at.isoformat(),
                        bp.id,
                        expected_version,
                    ),
                )
                if (cur.rowcount or 0) == 0:
                    raise BlueprintVersionConflict(
                        f"blueprint {bp.id} version {expected_version} no longer current"
                    )
                bp.version = expected_version + 1
            else:
                await self.db.execute(
                    """
                    UPDATE drone_blueprints
                       SET name = ?, description = ?, role = ?, provider = ?,
                           model = ?, system_persona = ?, skills = ?,
                           reference_blueprint_ids = ?, chat_url = ?,
                           version = version + 1, updated_at = ?
                     WHERE id = ?
                    """,
                    (
                        bp.name,
                        bp.description,
                        bp.role.value,
                        bp.provider,
                        bp.model,
                        bp.system_persona,
                        json.dumps(bp.skills),
                        json.dumps(bp.reference_blueprint_ids),
                        bp.chat_url,
                        bp.updated_at.isoformat(),
                        bp.id,
                    ),
                )
                bp.version += 1
            await self.db.commit()
        return bp

    @staticmethod
    def _hydrate_drone_blueprint(row: aiosqlite.Row) -> DroneBlueprint:
        d = dict(row)
        d["role"] = DroneRole(d["role"])
        d["skills"] = json.loads(d.get("skills") or "[]")
        d["reference_blueprint_ids"] = json.loads(d.get("reference_blueprint_ids") or "[]")
        return DroneBlueprint.model_validate(d)

    async def get_drone_blueprint(self, blueprint_id: str) -> DroneBlueprint | None:
        cur = await self.db.execute("SELECT * FROM drone_blueprints WHERE id = ?", (blueprint_id,))
        row = await cur.fetchone()
        return self._hydrate_drone_blueprint(row) if row else None

    async def list_drone_blueprints(self) -> list[DroneBlueprint]:
        cur = await self.db.execute("SELECT * FROM drone_blueprints ORDER BY updated_at DESC")
        rows = await cur.fetchall()
        return [self._hydrate_drone_blueprint(r) for r in rows]

    async def delete_drone_blueprint(self, blueprint_id: str) -> bool:
        """Refuses if any actions reference this blueprint.  Caller
        should check ``count_actions_for_blueprint`` first and surface
        a confirmation if non-zero.
        """
        async with self._lock:
            cur = await self.db.execute(
                "SELECT COUNT(*) AS n FROM drone_actions WHERE blueprint_id = ?",
                (blueprint_id,),
            )
            row = await cur.fetchone()
            if row and int(row["n"]) > 0:
                # Don't silently cascade — operator should know.
                return False
            cur2 = await self.db.execute(
                "DELETE FROM drone_blueprints WHERE id = ?", (blueprint_id,)
            )
            await self.db.commit()
        return (cur2.rowcount or 0) > 0

    async def count_actions_for_blueprint(self, blueprint_id: str) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM drone_actions WHERE blueprint_id = ?",
            (blueprint_id,),
        )
        row = await cur.fetchone()
        return int(row["n"]) if row else 0

    # --- Drone actions ------------------------------------------------

    async def insert_drone_action(self, action: DroneAction) -> DroneAction:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO drone_actions (
                    id, blueprint_id, blueprint_snapshot, workspace_id,
                    additional_skills, additional_reference_action_ids,
                    transcript, bound_chat_url, name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action.id,
                    action.blueprint_id,
                    json.dumps(action.blueprint_snapshot),
                    action.workspace_id,
                    json.dumps(action.additional_skills),
                    json.dumps(action.additional_reference_action_ids),
                    json.dumps(action.transcript),
                    action.bound_chat_url,
                    action.name,
                    action.created_at.isoformat(),
                    action.updated_at.isoformat(),
                ),
            )
            await self.db.commit()
        return action

    async def update_drone_action(self, action: DroneAction) -> DroneAction:
        action.updated_at = utc_now()
        async with self._lock:
            await self.db.execute(
                """
                UPDATE drone_actions
                   SET blueprint_snapshot = ?,
                       workspace_id = ?,
                       additional_skills = ?,
                       additional_reference_action_ids = ?,
                       transcript = ?,
                       bound_chat_url = ?,
                       name = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    json.dumps(action.blueprint_snapshot),
                    action.workspace_id,
                    json.dumps(action.additional_skills),
                    json.dumps(action.additional_reference_action_ids),
                    json.dumps(action.transcript),
                    action.bound_chat_url,
                    action.name,
                    action.updated_at.isoformat(),
                    action.id,
                ),
            )
            await self.db.commit()
        return action

    @staticmethod
    def _hydrate_drone_action(row: aiosqlite.Row) -> DroneAction:
        d = dict(row)
        d["blueprint_snapshot"] = json.loads(d.get("blueprint_snapshot") or "{}")
        d["additional_skills"] = json.loads(d.get("additional_skills") or "[]")
        d["additional_reference_action_ids"] = json.loads(
            d.get("additional_reference_action_ids") or "[]"
        )
        d["transcript"] = json.loads(d.get("transcript") or "[]")
        return DroneAction.model_validate(d)

    async def get_drone_action(self, action_id: str) -> DroneAction | None:
        cur = await self.db.execute("SELECT * FROM drone_actions WHERE id = ?", (action_id,))
        row = await cur.fetchone()
        return self._hydrate_drone_action(row) if row else None

    async def list_drone_actions(self, *, blueprint_id: str | None = None) -> list[DroneAction]:
        if blueprint_id:
            cur = await self.db.execute(
                "SELECT * FROM drone_actions WHERE blueprint_id = ? ORDER BY updated_at DESC",
                (blueprint_id,),
            )
        else:
            cur = await self.db.execute("SELECT * FROM drone_actions ORDER BY updated_at DESC")
        rows = await cur.fetchall()
        return [self._hydrate_drone_action(r) for r in rows]

    async def delete_drone_action(self, action_id: str) -> bool:
        async with self._lock:
            cur = await self.db.execute("DELETE FROM drone_actions WHERE id = ?", (action_id,))
            await self.db.commit()
        return (cur.rowcount or 0) > 0

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def insert_skill(self, s: Skill) -> Skill:
        async with self._lock:
            await self.db.execute(
                """
                INSERT INTO skills (id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    s.id,
                    s.name,
                    s.description,
                    s.created_at.isoformat(),
                    s.updated_at.isoformat(),
                ),
            )
            await self.db.commit()
        return s

    async def update_skill(self, s: Skill) -> Skill:
        s.updated_at = utc_now()
        async with self._lock:
            await self.db.execute(
                """
                UPDATE skills
                   SET name = ?, description = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    s.name,
                    s.description,
                    s.updated_at.isoformat(),
                    s.id,
                ),
            )
            await self.db.commit()
        return s

    async def get_skill(self, skill_id: str) -> Skill | None:
        cur = await self.db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
        row = await cur.fetchone()
        return Skill.model_validate(dict(row)) if row else None

    async def list_skills(self) -> list[Skill]:
        cur = await self.db.execute("SELECT * FROM skills ORDER BY updated_at DESC")
        rows = await cur.fetchall()
        return [Skill.model_validate(dict(r)) for r in rows]

    async def delete_skill(self, skill_id: str) -> bool:
        async with self._lock:
            cur = await self.db.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
            await self.db.commit()
        return (cur.rowcount or 0) > 0

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # Hold the write lock for the whole transaction so concurrent
        # writers can't smuggle their statements into our BEGIN..COMMIT
        # window on the shared aiosqlite connection.
        async with self._lock:
            await self.db.execute("BEGIN")
            try:
                yield
            except Exception:
                await self.db.execute("ROLLBACK")
                raise
            else:
                await self.db.commit()


def _ensure_resources_available() -> Path:
    """Check the schema file is locatable.  Raises if missing."""
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"schema.sql not found at {SCHEMA_PATH}")
    return SCHEMA_PATH


_ensure_resources_available()
