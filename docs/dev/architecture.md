# Architecture

The orchestrator runs as **two processes**: a PySide6 GUI and a Python
service.  They communicate over JSON-RPC on `127.0.0.1`.  Authentication
is a per-launch bearer token shared via the OS keyring (the GUI reads
the same token the service generated on startup).

## Why two processes

- A long-running agent run, a stuck subprocess, or a runaway OTel
  collector cannot freeze the UI.
- The service exposes the same RPC surface to a future CLI / headless
  mode without GUI dependencies.
- The GUI process can be reloaded during development while the
  service stays warm.

## Subsystems inside the service

```
┌───────────────────────────────────────────────────────────┐
│ DISPATCH                                                  │
│   ChatSession  — in-process SDK calls (no worktree)        │
│   Run          — worktree-bound, full lifecycle            │
├───────────────────────────────────────────────────────────┤
│ INGESTION                                                 │
│   JSONLWatcher — Claude session files                      │
│   HookHTTP     — Claude hook receiver                      │
│   OTelCol      — Gemini telemetry                           │
│   StreamParser — orchestrator-spawned CLI subprocesses      │
│   SDKAdapter   — orchestrator-driven SDK iterators          │
├───────────────────────────────────────────────────────────┤
│ SUPERVISION & REGISTRY                                     │
│   EventStore (SQLite + FTS5)                               │
│   WorktreeManager                                          │
│   Cards / Templates                                        │
│   Cost meter                                               │
│   Keyring                                                  │
│   MCP server registry                                      │
└───────────────────────────────────────────────────────────┘
```

Every dispatch and every ingestion path lands in one normalized
event schema in `EventStore`.  The GUI reads through the RPC surface
and never touches the database directly.

See `docs/dev/worktree-design.md` for the worktree subsystem in
detail.

## Data flow on a typical Run

1. User picks an archetype card in the Composer wizard.
2. Composer renders the template via `templates.render`, lints it via
   `lint.instruction`, forecasts cost via `cost.forecast`.
3. User clicks Dispatch → service creates a `Run`, calls
   `WorktreeManager.create()` for an isolated branch.
4. Provider adapter starts streaming events; each event lands in the
   event store.
5. Plan is surfaced, HITL gate fires if blast radius exceeds the
   card's threshold.
6. On approval, agent executes; commits flow through
   `WorktreeManager.commit()` at logical-step boundaries.
7. On finish, `WorktreeManager.request_review()` builds a ReviewBundle
   for the Diff/Review screen.
8. User picks a merge mode; `WorktreeManager.approve_and_merge()`
   merges into the base branch and cleans the worktree.
9. Outcome row + final cost recorded.

## Out of scope (still)

This list has shrunk substantially since V1.  As of Phase 5, **only
the items below remain unshipped**:

- Briefcase signed installer (certs not issued yet).
- Firecracker / E2B microVM sandbox tier (E2B stub exists at
  `apps/service/sandbox/e2b.py` but isn't wired into the dispatcher).

Items previously listed here that have **shipped**:

- Mergiraf integration (`apps/service/worktrees/merger.py`).
- Gemini CLI + API providers (`providers/gemini_cli.py`, `providers/google.py`).
- Ollama provider (`providers/ollama.py`).
- Docker sandbox tier (`apps/service/sandbox/docker.py`).
