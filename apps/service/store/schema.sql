-- AgentOrchestra SQLite schema.
-- Single-file store. Append-only event log + materialized tables for the GUI.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- ---------------------------------------------------------------------------
-- Workspaces
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspaces (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    repo_path            TEXT NOT NULL UNIQUE,
    default_base_branch  TEXT NOT NULL DEFAULT 'main',
    created_at           TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Cards & Templates
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS templates (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    archetype     TEXT NOT NULL,
    body          TEXT NOT NULL,
    variables     TEXT NOT NULL,       -- JSON
    version       INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE(archetype, version)
);

CREATE TABLE IF NOT EXISTS cards (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    archetype                TEXT NOT NULL,
    description              TEXT NOT NULL,
    template_id              TEXT NOT NULL REFERENCES templates(id),
    provider                 TEXT NOT NULL,
    model                    TEXT NOT NULL,
    cost                     TEXT NOT NULL,        -- JSON
    blast_radius             TEXT NOT NULL,        -- JSON
    sandbox_tier             TEXT NOT NULL,
    tool_allowlist           TEXT NOT NULL,        -- JSON array
    stale_minutes            INTEGER NOT NULL,
    max_commits_per_run      INTEGER NOT NULL,
    skip_pre_commit_hooks    INTEGER NOT NULL,
    version                  INTEGER NOT NULL,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Instructions (a concrete rendering)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS instructions (
    id                  TEXT PRIMARY KEY,
    template_id         TEXT NOT NULL REFERENCES templates(id),
    template_version    INTEGER NOT NULL,
    card_id             TEXT NOT NULL REFERENCES cards(id),
    rendered_text       TEXT NOT NULL,
    variables           TEXT NOT NULL,             -- JSON
    created_at          TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Runs, Steps, Branches, Approvals, Outcomes, Artifacts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL REFERENCES workspaces(id),
    card_id             TEXT NOT NULL REFERENCES cards(id),
    instruction_id      TEXT NOT NULL REFERENCES instructions(id),
    branch_id           TEXT,
    state               TEXT NOT NULL,
    state_changed_at    TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    completed_at        TEXT,
    cost_usd            REAL NOT NULL DEFAULT 0,
    cost_tokens         INTEGER NOT NULL DEFAULT 0,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_workspace ON runs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);

CREATE TABLE IF NOT EXISTS branches (
    id                       TEXT PRIMARY KEY,
    run_id                   TEXT NOT NULL UNIQUE REFERENCES runs(id),
    workspace_id             TEXT NOT NULL REFERENCES workspaces(id),
    base_ref                 TEXT NOT NULL,
    base_branch_name         TEXT NOT NULL,
    agent_branch_name        TEXT NOT NULL,
    worktree_path            TEXT NOT NULL,
    state                    TEXT NOT NULL,
    state_changed_at         TEXT NOT NULL,
    created_at               TEXT NOT NULL,
    last_commit_sha          TEXT,
    last_commit_at           TEXT,
    process_pid              INTEGER,
    include_uncommitted      INTEGER NOT NULL DEFAULT 0,
    notes                    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_branches_workspace_state
    ON branches(workspace_id, state);

CREATE TABLE IF NOT EXISTS steps (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    seq             INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    payload         TEXT NOT NULL                  -- JSON
);

CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, seq);

CREATE TABLE IF NOT EXISTS artifacts (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    step_id         TEXT,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);

CREATE TABLE IF NOT EXISTS approvals (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    reason          TEXT NOT NULL,
    risk_signals    TEXT NOT NULL,         -- JSON
    decision        TEXT NOT NULL,
    requested_at    TEXT NOT NULL,
    decided_at      TEXT,
    decided_by      TEXT NOT NULL DEFAULT 'user',
    note            TEXT
);

CREATE TABLE IF NOT EXISTS outcomes (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL UNIQUE REFERENCES runs(id),
    kind                TEXT NOT NULL,
    rationale           TEXT NOT NULL DEFAULT '',
    final_cost_usd      REAL NOT NULL DEFAULT 0,
    final_cost_tokens   INTEGER NOT NULL DEFAULT 0,
    duration_seconds    INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Event log (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    seq             INTEGER NOT NULL,
    occurred_at     TEXT NOT NULL,
    source          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    run_id          TEXT,
    step_id         TEXT,
    branch_id       TEXT,
    workspace_id    TEXT,
    payload         TEXT NOT NULL,        -- JSON
    text            TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_seq ON events(seq);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at);

-- A single-row table holding the next event sequence number.
-- Wrapped in transactions for atomic increments without locks.
CREATE TABLE IF NOT EXISTS event_seq (
    id      INTEGER PRIMARY KEY CHECK(id = 1),
    next    INTEGER NOT NULL
);
INSERT OR IGNORE INTO event_seq(id, next) VALUES (1, 1);

-- ---------------------------------------------------------------------------
-- FTS5 virtual table for full-text search across instructions, artifacts,
-- and event text. Rebuilt by the application on insert.
-- ---------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
    doc_id UNINDEXED,
    doc_kind UNINDEXED,
    title,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);
