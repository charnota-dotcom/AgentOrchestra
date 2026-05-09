# Changelog

## Unreleased — Phase 2

Sprint 1 — multi-vendor.
- Gemini chat adapter via the official google-genai SDK.
- Ollama chat adapter via the OpenAI-compatible /v1/chat/completions
  endpoint at http://localhost:11434.
- Provider registry now wires anthropic + google + ollama by default;
  agentic Gemini / Ollama runs surface a clear deferred-feature error.
- PersonalityCard gains `fallbacks`, an ordered list of {provider,
  model} dicts.  RunDispatcher tries the primary on open; on failure
  it walks the fallbacks before declaring the run aborted.

Sprint 2 — innovations.
- runs.replay re-runs a past Run with optional provider / model /
  instruction overrides.  Overrides clone the card so the original's
  accounting stays intact.  History page in the GUI grew a Recent
  runs tab with a Replay… dialog.
- Claude hook bridge: bundled `packs/hooks/agentorchestra-hook.sh`
  and an idempotent installer that edits `~/.claude/settings.json`
  to attach our entry to SessionStart / PreToolUse / PostToolUse /
  Stop / SubagentStop with env vars carrying the URL + token.
  Settings page exposes Install / Remove buttons.
- Auto-QA on diff: PersonalityCard.auto_qa triggers a chat-style
  qa-on-fix run targeting the parent's diff once the parent reaches
  REVIEWING.
- Cost caps enforced mid-run: every usage event recomputes cumulative
  cost; soft cap emits a warning event once; hard cap aborts the run
  cleanly.

Sprint 3 — specialised archetypes.
- Red Team adversarial reviewer card targeting another run's diff.
- Tracker watcher card emitting structured HandoffCards.
- Cross-vendor Consensus card + a fan-out + judge orchestrator
  (`apps/service/dispatch/consensus.py`).  RPC: runs.consensus.

Sprint 4 — UI polish + safety.
- Workspace map widget: per-workspace lanes with run state pips +
  costs, refresh button, Home page now splits into Active table +
  workspace map.
- Plan-act split with HITL gate: PersonalityCard.requires_plan makes
  the dispatcher generate a written plan first, persist it as a PLAN
  artifact, transition the Run to AWAITING_APPROVAL, and wait on an
  asyncio.Event until runs.approve_plan releases it.

Tests: test_providers, test_replay, test_hook_installer,
test_consensus, test_plan_act (integration).
CI: ruff check + ruff format --check green tree-wide.

## Phase 1 — MVP scaffold

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
