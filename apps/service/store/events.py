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
    Branch,
    BranchState,
    Event,
    Instruction,
    InstructionTemplate,
    Outcome,
    PersonalityCard,
    Run,
    RunState,
    Step,
    Workspace,
    utc_now,
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
log = logging.getLogger(__name__)


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


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
    # Templates & Cards
    # ------------------------------------------------------------------

    async def insert_template(self, t: InstructionTemplate) -> InstructionTemplate:
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
        await self.db.execute(
            """
            INSERT INTO cards (id, name, archetype, description, template_id,
                provider, model, mode, cost, blast_radius, sandbox_tier,
                tool_allowlist, fallbacks, auto_qa, stale_minutes,
                max_commits_per_run, max_turns, skip_pre_commit_hooks,
                version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    # Convenience helpers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
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
