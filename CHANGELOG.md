# Changelog

## Unreleased — Phase 1 MVP scaffold

This commit lays down the working scaffold for the multi-vendor desktop
agent orchestrator.  Execution-ready end-to-end run dispatch is the
next milestone (Phase 1 weeks 4–6); this commit covers everything
upstream of that.

### Added

- Repo structure (`apps/`, `packs/`, `tests/`, `docs/`).
- `pyproject.toml` with pinned core deps; optional `gui`, `google`, `dev`
  extras; ruff + mypy + pytest configured.
- Domain types: PersonalityCard, InstructionTemplate, Instruction, Run,
  Step, Branch, Workspace, Artifact, Approval, Outcome, Event with
  state machines and assertion helpers.
- SQLite event store with FTS5 full-text search and JSON-encoded
  payload columns.
- Async git CLI wrapper with timeouts, branch-name regex, ref
  manipulation, worktree add/remove/prune, atomic info/exclude append.
- WorktreeManager: workspace registration, Run-scoped worktree
  creation (with optional uncommitted-state import), commit on
  logical-step boundaries, three merge modes, stale sweep, panic reset.
- LLMProvider protocol + Anthropic adapter (`AnthropicChatSession`)
  using the official SDK; provider registry.
- Banks-style template engine (front-matter + Jinja2 body) and three
  seed archetype templates: Broad Research, Narrow Research, QA on Fix.
- Card seeder that loads templates and binds them to provider, model,
  cost cap, blast-radius policy, and sandbox tier defaults.
- Pre-flight instruction linter (length, vagueness, secrets, conflicts,
  archetype-specific requirements).
- OS-keyring wrapper with in-memory fallback for tests.
- Cost meter with pinned mid-2026 price table and prompt-size-aware
  forecast.
- Claude session JSONL watcher (ambient capture).
- JSON-RPC IPC server (Starlette) bound to `127.0.0.1` with bearer-token
  auth; methods for workspaces, cards, runs, search, lint, cost
  forecast, template render, providers, hook ingest.
- Orchestrator service entrypoint with signal-handled shutdown.
- PySide6 GUI shell: main window with rail navigation; Home, Composer
  (archetype picker + form-driven wizard + rendered preview + lint
  display + cost forecast), History (FTS search), Settings (provider
  keys + workspaces).
- Unit tests: state machine, naming, linter, template engine, cost
  meter, event store.
- Integration test: WorktreeManager create / commit / merge / cleanup
  / panic-reset against a real git repo.
- GitHub Actions CI: ruff lint + format check, mypy advisory, pytest
  unit + integration on Ubuntu and macOS, Python 3.11 and 3.12.
- Architecture and worktree design docs.

### Deferred (Phase 1 weeks 4–6 and beyond)

- Real Run dispatch (the agent loop in a worktree)
- Mergiraf binary integration and the assisted-merge UX
- Gemini and Ollama adapter implementations
- Hook pack installer
- Briefcase signed installers
- Visual branch/worktree map and live agent pane
- Replay & fork
