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
    mode                     TEXT NOT NULL DEFAULT 'chat',
    cost                     TEXT NOT NULL,        -- JSON
    blast_radius             TEXT NOT NULL,        -- JSON
    sandbox_tier             TEXT NOT NULL,
    tool_allowlist           TEXT NOT NULL,        -- JSON array
    fallbacks                TEXT NOT NULL DEFAULT '[]',  -- JSON array of {provider, model}
    auto_qa                  INTEGER NOT NULL DEFAULT 0,
    requires_plan            INTEGER NOT NULL DEFAULT 0,
    stale_minutes            INTEGER NOT NULL,
    max_commits_per_run      INTEGER NOT NULL,
    max_turns                INTEGER NOT NULL DEFAULT 12,
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
    workspace_id        TEXT REFERENCES workspaces(id),
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

-- ---------------------------------------------------------------------------
-- Flow Canvas (visual orchestration).  See docs/FLOW_CANVAS_PLAN.md.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flows (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL,        -- JSON: {nodes:[...], edges:[...]}
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flow_runs (
    id           TEXT PRIMARY KEY,
    flow_id      TEXT NOT NULL REFERENCES flows(id),
    state        TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    payload      TEXT NOT NULL          -- JSON: per-node outputs, errors
);

CREATE INDEX IF NOT EXISTS idx_flow_runs_flow ON flow_runs(flow_id);

-- ---------------------------------------------------------------------------
-- Agents (named, persistent conversations).  Distinct from `cards`
-- (template-bound dispatch units): agents are the lay-person path —
-- a name, a model, a transcript, and an optional parent link for
-- follow-up agents (summarise / annotate / deep dive / etc).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    system        TEXT NOT NULL DEFAULT '',
    parent_id     TEXT REFERENCES agents(id),
    parent_name   TEXT,
    -- preset name used when this agent was spawned as a follow-up
    -- (summarise / annotate / deep_dive / critique / verify / custom).
    -- NULL for top-level agents.  Drives the directional-edge label
    -- on the canvas.  Existing installs get this column via the
    -- code-side migration in EventStore._migrate.
    parent_preset TEXT,
    -- JSON list of agent_ids whose transcripts are inlined as a
    -- context preamble on every send.  Lets a fresh agent (different
    -- provider / model) reference prior conversations without being
    -- a literal child.  Existing installs get this via the code-side
    -- migration too.
    reference_agent_ids TEXT NOT NULL DEFAULT '[]',
    -- Optional Workspace this agent operates inside.  When set, the
    -- CLI subprocess runs with cwd = workspace.repo_path so the model
    -- can use its built-in file tools against the project.  Null =
    -- pure chat agent with no repo access.  Code-side migration
    -- backfills this column on existing installs.
    workspace_id  TEXT REFERENCES workspaces(id),
    transcript    TEXT NOT NULL DEFAULT '[]',  -- JSON: [{role, content}]
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agents_parent ON agents(parent_id);
CREATE INDEX IF NOT EXISTS idx_agents_updated ON agents(updated_at DESC);

-- ---------------------------------------------------------------------------
-- Provider-side message tally — append-only.  Lets the Limits tab show
-- "X messages / cap" against the published plan limits without polling
-- the CLI.  One row per successful agents.send / chat.send.  Pruned by
-- a daily VACUUM-equivalent (left for a future cleanup pass; the cost
-- is roughly 50 bytes per send).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provider_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    sent_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_messages_at ON provider_messages(provider, sent_at DESC);

-- ---------------------------------------------------------------------------
-- Attachments: files (images, spreadsheets) the operator drops into a
-- chat or agent dialog.  Stored on disk under
-- <data_dir>/attachments/<agent_id>/<id>__<sanitized_filename>; this
-- row indexes them and caches the rendered-text representation for
-- spreadsheets so we don't reparse on every send.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attachments (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    turn_index      INTEGER NOT NULL DEFAULT -1,
    kind            TEXT NOT NULL,            -- 'image' | 'spreadsheet'
    original_name   TEXT NOT NULL,
    stored_path     TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    bytes           INTEGER NOT NULL,
    rendered_text   TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attachments_agent ON attachments(agent_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Drones — see docs/DRONE_MODEL.md.
--
-- A *blueprint* is the operator-set template (frozen, repo-portable).
-- A *drone action* is a deployed instance carrying live transcript +
-- workspace + one-off skill / reference layers.  The action holds an
-- immutable JSON snapshot of the blueprint as it was at deploy time
-- so blueprint edits never reach in-flight actions.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS drone_blueprints (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    description              TEXT NOT NULL DEFAULT '',
    role                     TEXT NOT NULL DEFAULT 'worker',
    provider                 TEXT NOT NULL,
    model                    TEXT NOT NULL,
    system_persona           TEXT NOT NULL DEFAULT '',
    skills                   TEXT NOT NULL DEFAULT '[]',
    reference_blueprint_ids  TEXT NOT NULL DEFAULT '[]',
    version                  INTEGER NOT NULL DEFAULT 1,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drone_blueprints_updated
    ON drone_blueprints(updated_at DESC);

CREATE TABLE IF NOT EXISTS drone_actions (
    id                                TEXT PRIMARY KEY,
    blueprint_id                      TEXT NOT NULL REFERENCES drone_blueprints(id),
    blueprint_snapshot                TEXT NOT NULL,                 -- JSON
    workspace_id                      TEXT REFERENCES workspaces(id),
    additional_skills                 TEXT NOT NULL DEFAULT '[]',
    additional_reference_action_ids   TEXT NOT NULL DEFAULT '[]',
    transcript                        TEXT NOT NULL DEFAULT '[]',
    created_at                        TEXT NOT NULL,
    updated_at                        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drone_actions_blueprint
    ON drone_actions(blueprint_id);
CREATE INDEX IF NOT EXISTS idx_drone_actions_updated
    ON drone_actions(updated_at DESC);

-- Drone-action attachments use the same on-disk shape as the existing
-- attachments table; we keep them separate so a drone deletion can
-- cascade cleanly without touching the (legacy) general-chat
-- attachments.
CREATE TABLE IF NOT EXISTS drone_action_attachments (
    id              TEXT PRIMARY KEY,
    action_id       TEXT NOT NULL REFERENCES drone_actions(id) ON DELETE CASCADE,
    turn_index      INTEGER NOT NULL DEFAULT -1,
    kind            TEXT NOT NULL,
    original_name   TEXT NOT NULL,
    stored_path     TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    bytes           INTEGER NOT NULL,
    rendered_text   TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drone_action_attachments_action
    ON drone_action_attachments(action_id, created_at DESC);
