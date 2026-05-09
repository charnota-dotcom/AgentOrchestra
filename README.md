# AgentOrchestra

Desktop orchestrator for multi-vendor AI sub-agents — Claude, Gemini, OpenAI, and local models — with branch-per-agent isolation, full instruction tracking, and indexed search over every output.

## Status

**Phase 1 (MVP) — in progress.** This branch (`claude/ai-agent-orchestration-research-BmALo`) is the working scaffold.

What is implemented in this commit:

- Repository scaffold with module boundaries from the project plan
- Domain types: PersonalityCard, InstructionTemplate, Run, Step, Branch, Approval, Outcome, Event
- SQLite event store with FTS5 search
- WorktreeManager (state machine, creation, commit, cleanup, stale sweep, panic reset)
- LLMProvider protocol + Anthropic ChatSession adapter (Claude Agent SDK + API fallback)
- Banks-style template engine with Jinja2 + front-matter
- Three seed archetype cards: Broad Research, Narrow Research, QA-on-fix
- Pre-flight instruction linter
- Cost forecast skeleton with pinned price tables
- OS-keyring secret storage
- Claude session JSONL watcher (ambient capture)
- JSON-RPC IPC layer over local HTTP (Starlette / uvicorn)
- Orchestrator service entrypoint and supervisor
- PySide6 GUI shell: Home, Composer, Live, Diff, History, Settings windows
- Unit tests for state machine, store, linter, manager, naming
- CI workflow (lint, type-check, test) on GitHub Actions

What is **not** in this commit (scoped for later weeks of Phase 1 or Phase 2+):

- Real Mergiraf integration (vendor binary; CLI shimmed)
- Briefcase installer configuration (signing certs need to be issued first)
- Gemini and Ollama adapters
- Docker / Firecracker sandbox tiers
- Voice dictation, red-team, consensus voting
- Production-grade GUI polish

## Architecture

Two processes on the user's machine:

```
┌─────────────────────────────────────────────────────┐
│  GUI (PySide6 + qasync)                              │
└──────────────────────────┬──────────────────────────┘
                           │ JSON-RPC over localhost
┌──────────────────────────┴──────────────────────────┐
│  Orchestrator service (Python, asyncio)              │
│  ┌──────────┬───────────────┬────────────────────┐   │
│  │ DISPATCH │  INGESTION    │ SUPERVISION/REG.   │   │
│  │ Chat     │ JSONL watcher │ SQLite + FTS5      │   │
│  │ Run      │ Hook receiver │ WorktreeMgr        │   │
│  │          │ OTel collector│ Cards / Templates  │   │
│  │          │ Stream parser │ Cost meter         │   │
│  │          │ SDK adapter   │ Keyring / MCP reg. │   │
│  └──────────┴───────────────┴────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

See `docs/dev/architecture.md` for the full picture.

## Development

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,gui,google]"

# Run unit tests
pytest tests/unit

# Run integration tests (touch the filesystem, spawn git)
pytest tests/integration

# Lint + type-check
ruff check .
ruff format --check .
mypy apps packs

# Start the orchestrator service (default port 8765)
agentorchestra-service

# Start the GUI (connects to a running service)
agentorchestra-gui
```

## Project layout

```
apps/
├── gui/        PySide6 GUI process
└── service/    Orchestrator service process
    ├── dispatch/    ChatSession + Run lifecycle
    ├── ingestion/   JSONL watcher, hooks, OTel, subprocess parsers
    ├── providers/   LLMProvider adapters (Anthropic, Google, etc.)
    ├── worktrees/   WorktreeManager + merger + GC
    ├── store/       SQLite event store + FTS search
    ├── cards/       PersonalityCard CRUD
    ├── templates/   Banks-based template engine
    ├── linter/      Pre-flight instruction checks
    ├── cost/        Forecasts + price tables
    ├── secrets/     Keyring wrapper
    ├── hitl/        Approval gates
    └── ipc/         JSON-RPC server
packs/
├── archetypes/      Bundled cards (Broad-Research, QA-on-fix, etc.)
├── hooks/           Claude hook scripts
└── otel-presets/    Gemini OTel collector configs
tests/
├── unit/
├── integration/
└── e2e/
docs/
├── user/
└── dev/
```

## License

Proprietary. See LICENSE (TBD).
