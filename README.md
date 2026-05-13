# AgentOrchestra

**Desktop orchestrator for multi-vendor AI sub-agents on Windows.** Drive Claude Code, Gemini CLI, Codex CLI, and local Ollama models from a single PySide6 GUI: design reusable **Blueprints** (templates with provider, model, system persona, default skills + role), deploy them as **FPV Drones** (manual browser-based source bundles), drag FPV Drones onto a canvas, attach repos so they can read / edit code with their built-in file tools, and design dispatchable Flow graphs that hand a task off through Trigger â†’ Agent â†’ Branch â†’ Merge â†’ Human â†’ Output nodes.

> **Heads up â€” Phase 6 rename.** The legacy "Agent" abstraction now maps to Reaper Drone, the Drone model now maps to FPV Drone, and Staging Area is a separate first-class node.  Existing chats from earlier versions are dropped on first launch after the upgrade.  See `docs/DRONE_MODEL.md` for the design and `CHANGELOG.md` for what changed.

Subscription-only by default â€” no API keys are required for the day-to-day flow. Auth piggybacks on whatever your local `claude` and `gemini` CLIs are already signed in to.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Install & first run](#install--first-run)
3. [Operator panel (Windows .cmd scripts)](#operator-panel-windows-cmd-scripts)
4. [The GUI tabs in detail](#the-gui-tabs-in-detail)
   - [Home](#home) Â· [FPV Drones](#drones) Â· [Reaper Drones](#Reaper Drones) Â· [Blueprints](#blueprints) Â· [Skills](#skills) Â· [Compose](#compose) Â· [Canvas](#canvas) Â· [Analytics](#analytics) Â· [History](#history) Â· [Limits](#limits) Â· [Settings](#settings)
5. [Subsystems](#subsystems)
   - [Repo-aware coding sessions](#repo-aware-coding-sessions)
   - [Attachments (images + spreadsheets)](#attachments-images--spreadsheets)
   - [Cross-chat references](#cross-chat-references)
   - [Flow Canvas (executable graphs)](#flow-canvas-executable-graphs)
   - [Workspaces & worktrees](#workspaces--worktrees)
   - [MCP server registry](#mcp-server-registry)
   - [Voice dictation](#voice-dictation)
6. [Architecture](#architecture)
7. [Where data lives](#where-data-lives)
8. [Safety model](#safety-model)
9. [Full RPC reference](#full-rpc-reference)
10. [Development](#development)
11. [Project layout](#project-layout)
12. [License](#license)

---

## What it does

AgentOrchestra is two cooperating processes: a long-lived **service** and a **GUI** that talks to it over JSON-RPC on `127.0.0.1:8765`.

The service does five things:

1. **Drives sub-agents** through CLI and API providers â€” `claude-cli` (Claude Code), `gemini-cli`, `codex-cli`, `anthropic` (API), `google` (API), `ollama` â€” and normalises their stream events into one shape (`text_delta`, `assistant_message`, `tool_call`, `tool_result`, `usage`, `finish`, `error`).
2. **Persists everything** to a single SQLite database (WAL mode, FTS5 search) â€” every Run, Branch, Step, Approval, Outcome, Event, Agent, Attachment, Flow, FlowRun, Workspace, Card, Template, Instruction.
3. **Owns git worktrees** when an agentic Run is dispatched â€” branch per agent, optional Mergiraf merge, panic reset, drift sentinel.
4. **Watches the host** â€” Claude session JSONL files and Claude Code hooks â€” so what you do interactively shows up in the orchestrator's history too.
5. **Exposes ~50 RPC methods** the GUI calls into. The full list is below.

The GUI presents this as **eleven rail tabs** (Home, FPV Drones, Reaper Drones, Blueprints, Skills, Compose, Canvas, Analytics, History, Limits, Settings) plus two stack pages (Live, Review) reached when you dispatch a Run from Compose.

---

## Install & first run

**Requirements:** Windows, Python 3.11+, GitHub Desktop (or `git` on PATH), and at least one of:

- **Claude Code CLI** signed in via Max subscription â€” install from <https://docs.claude.com/en/docs/claude-code> then run `claude` once and `/login`.
- **Gemini CLI** signed in via Google AI Studio / Workspace SSO / `GEMINI_API_KEY` â€” install from <https://github.com/google-gemini/gemini-cli> then run `gemini` once.

Any one is enough; more than one is better. **No Anthropic / Google API key is required** for the chat path.

Optional installs unlock specific features:

| Extra | Unlocks |
|---|---|
| `pip install -e ".[gui]"` | the PySide6 GUI (mandatory for the desktop app) |
| `pip install Pillow` | image resizing (else stored at original size) |
| `pip install openpyxl` | `.xlsx` rendering to markdown tables |
| `pip install xlrd` | legacy `.xls` rendering |
| `pip install faster-whisper` | local voice dictation in Compose |
| `pip install -e ".[google]"` | the Google API provider (only needed if you want to bypass the Gemini CLI) |

The recommended first-run flow uses the **Operator Panel** below â€” it walks you through it.

---

## Operator panel (Windows .cmd scripts)

`scripts/` is a self-contained set of one-click `.cmd` files designed for non-technical operators. Double-click `ops.cmd` from File Explorer (or pin it to the taskbar) and you get a numbered menu sourced from `scripts/manifest.json`.

| File | Step | What it does |
|---|---|---|
| `ops.cmd` | â˜… | The Operator Panel itself â€” reads `manifest.json` so any new command shows up automatically. |
| `start.cmd` | â˜… | **Pre-flight verifier.** Probes `claude -p "â€¦"` and `gemini -p "â€¦"` headlessly. If at least one CLI replies, launches the GUI. If both fail, aborts with instructions to fix the auth. Use first-of-day. |
| `restart.cmd` | â˜… | **Everyday button.** Three-pass kill (window-title + port-listening on 8765) then launches a fresh GUI. Use after pulling a new commit, after `update.cmd`, or any time the running service is acting up. |
| `limits.cmd` | â˜… | Print whatever subscription / usage info the local CLIs expose. The GUI's **Limits** tab is the in-app version. |
| `setup.cmd` | 1 | Create `.venv` and install AgentOrchestra + `[gui]` extras. Idempotent â€” re-run after a Python upgrade or if `.venv` goes missing. |
| `test-claude.cmd` | 2 | Smoke-test `claude` on PATH + a headless reply. If "Not logged in", run `claude` interactively and `/login`. |
| `test-gemini.cmd` | 3 | Smoke-test `gemini` on PATH + a headless reply. Skip if you only use Claude. |
| `launch.cmd` | 4 | Plain launch (no pre-flight). Faster than `start.cmd` when you trust the CLIs are signed in. |
| `stop.cmd` | 5 | Stop the GUI + service. Combines window-title kill with netstat-based port-listening kill so it catches both supervisor-spawned and manually-spawned services. |
| `update.cmd` | 6 | `git pull` + `pip install -e .` to refresh the venv. Always follow with `restart.cmd`. |
| `doctor.cmd` | 7 | Diagnose: prints Python version, venv health, port 8765 status, last service log lines. |
| `reset.cmd` | 8 | **Destructive.** Wipe the SQLite store. Use only when nothing else helps. |

The Operator Panel (`scripts/ops.py`) reads `manifest.json` so adding a new `.cmd` file plus a manifest entry surfaces it as a new button automatically â€” no GUI code change needed.

---

## The GUI tabs in detail

### Home

Landing page. Shows a **Workspaces map** (registered repos with their last activity), an **Active runs** table (in-flight Runs across all workspaces), and a **Recent runs** table with one-row-per-Run history. A Refresh button re-pulls all three. The first time you open the app, a **first-run wizard** (`first_run.py`) walks you through the three CLI smoke tests so you know your subscriptions work before sending real prompts.

### FPV Drones

A dedicated tab for manual, browser-based robot friends.  These units use the `browser` provider and require the operator to copy/paste messages through their standard web browser (Claude.ai, ChatGPT, etc.).  Ideal for simple tasks or when you want to remain in control of every turn.

### Reaper Drones

A dedicated tab for autonomous robot friends.  These units use CLI-based providers (`claude-cli`, `gemini-cli`, `codex-cli`) to run all by themselves on your computer.  They can read your files, run code, and solve complex problems without any manual copy-pasting.

### Blueprints

The **"Robot Plan"** workshop.  Create frozen templates for your friends:
- **+ FPV Drone** â€” Start a manual browser plan.
- **+ Reaper Drone** â€” Start an autonomous CLI plan with integrated skill selection.
- **Convert to Reaper Drone** â€” Select any Drone blueprint and upgrade it to an Reaper Drone brain at any time.

### Skills

The **"Superpower"** management library.  Create, edit, and delete instruction templates (e.g. `/research-deep`, `/code-review`).  These are database-backed and can be easily picked from a popup window whenever you are making or deploying a Reaper Drone.

### Compose

The **operator-grade** instruction builder â€” for when you want a card-driven Run with a state machine, cost caps, and an approval gate, rather than a free-form chat.

- Pick a **PersonalityCard** (Broad Research, Narrow Research, QA-on-fix, Code Planning Assistant, â€¦). Cards are pydantic models with `provider`, `model`, `mode`, `cost: CostPolicy`, `blast_radius: BlastRadiusPolicy`, `sandbox_tier`, `tool_allowlist`, `fallbacks`, `auto_qa`, `requires_plan`, `max_turns`, â€¦
- Pick an **InstructionTemplate** (Banks-style with Jinja2 + front-matter). Variables you fill in get rendered into the prompt; the rendered text is persisted as an `Instruction`.
- **Pre-flight linter** runs on the rendered text, surfacing risks (unbounded scope, ambiguous file paths, etc.) before dispatch.
- **Cost forecast** uses the card's `cost.input_per_1k_tokens` / `output_per_1k_tokens` plus the linter's token estimate; per-run hard cap aborts the run, soft cap warns once.
- **Voice dictation** button â€” opens the OS file picker, runs the audio through a local `faster-whisper` transcribe (fully on-device), drops the text into the instruction box. Audio extensions allow-listed; path resolved + checked at the RPC.
- **Dispatch** â€” kicks off a `runs.dispatch` which builds a worktree + branch and runs the agent loop with the configured tools.

### Canvas

Drag-and-drop graph editor. Two distinct things live on the canvas:

1. **BlueprintNodes** â€” wrap a `DroneBlueprint` template. Used by the Flow executor to dispatch a fresh single-shot run when a Flow runs.
2. **DroneNodes** â€” wrap a persistent `DroneAction`. Drop one onto the canvas and double-click to reconfigure it. A ðŸ“‚ marker shows in the subtitle when bound to a repo; the tooltip spells out provider, model, turn count, and repo.

**Key Features:**
- **Edit on Double-Click:** Double-clicking any drone node opens the **Edit Drone** dialog (name, workspace, skills) without changing the original blueprint.
- **Convert to Reaper Drone:** Right-click any manual browser drone to "promote" it to an autonomous CLI Reaper Drone, preserving the full transcript.
- **Peer References:** Non-directional edges between drone nodes act as implicit context providers, allowing agents to "talk" across different windows and models.
- **Lineage:** Auto-draws translucent boxes around parent/child FPV Drone clusters.

### Analytics

Operational analytics dashboard backed by `analytics.summary` and `analytics.leaderboard`.
Tracks rolling metrics including hallucination proxy rate (tool-error incidence),
token efficiency, re-plan velocity, and cost-per-success.

### History

Read-only browser over every Run, Branch, Step, Approval, and Artifact. **FTS5 search** across instructions, artifacts, and salient event text â€” type anything in the search bar and you get ranked hits with `<b>highlighted</b>` snippets.

### Limits

**In-app subscription dashboard.** Lives at `apps/gui/windows/limits.py`. Refresh runs `limits.check` (which probes `claude --version` / `claude status` / `gemini --version` / `gemini status` / `codex --version` headlessly) and `limits.usage` (which counts your own sends from the `provider_messages` table for 5h / 24h / 7d windows).

**Cards rendered:**

- **One per provider** (Claude Code / Gemini CLI / Codex CLI). Each has:
  - A plan picker sourced from `apps/service/limits/__init__.py` with a `DATA_AS_OF` date so you know how stale the published numbers are.
  - Per-model message caps for the selected plan.
  - **Local tally** â€” `X / cap` rendered against your own send count for the relevant window. The tally is canonical for your own usage; the published cap is canonical for what your subscription buys.
  - Links to the official dashboards for the operator-of-truth.
- **Context-window summary card** â€” every model the orchestrator knows the token-budget for, in one sortable list. From `context_windows()`.
- **Attachment storage card** â€” total file count + bytes uploaded across all agents, plus a per-agent breakdown sorted by bytes. From `attachments.usage`. Useful when you want to know which agent is eating disk.

**Cooldown.** The Refresh button is gated to once per 5 minutes (`_REFRESH_COOLDOWN_SECONDS = 300`) so the CLI status calls â€” which take real subprocess time â€” can't be hammered.

### Settings

- **Service URL** (default `http://127.0.0.1:8765`).
- **Token** â€” sourced from the OS keyring. The service mints one at startup if missing; the GUI looks it up via `hook_token()`.
- **MCP server registry** â€” list / add / trust / block / remove MCP servers. Trusted servers are exposed to cards whose `tool_allowlist` includes them.
- **Hook installer** â€” install / uninstall the Claude Code hook scripts (`packs/hooks/`) so JSONL session files land in our ingestion path.

---

## Subsystems

### Repo-aware coding sessions

When a Reaper Drone has `workspace_id` set, the CLI runs with `cwd=<repo_path>` so its built-in tools operate against the project. Two ways to get there:

1. **Clone from a git URL** â€” Chat tab â†’ Clone from gitâ€¦, or Canvas palette â†’ Cloneâ€¦. Runs `git clone --quiet [-b <branch>] [--depth N] -- <url> <dest>` into `<data_dir>/clones/<sanitized-name>`. URLs starting with `-` and containing control chars are rejected; pre-existing dest paths are refused; half-finished clones are cleaned up on failure; 5-minute timeout.
2. **Register an existing local repo** â€” Add repoâ€¦ picks a directory and runs `WorktreeManager.register_workspace`, which validates the path is a working tree (not a bare repo) and that no `agent/*` branches exist yet (so the worktree namespace is clean).

Once bound, every send to that Agent:

- Spawns the CLI subprocess with `cwd = ws.repo_path`.
- Builds a richer **system prompt** that:
  - Names the workspace and the **current branch**.
  - Tells the model not to push / force / `rm -rf` without explicit go-ahead, to prefer small reviewable diffs, and to run `git status` / `git diff` before non-trivial changes.
  - Inlines the **first project-convention file** found at the repo root: `CLAUDE.md` â†’ `AGENTS.md` â†’ `GEMINI.md` â†’ `.cursorrules` â†’ `.cursor/rules.md`. Capped at 8 KB with a truncation marker. Symlinks pointing outside the repo are refused.
- The canvas chat dialog refreshes the **live git status banner** after the send, so you see what changed.

The **Switch branch** button calls `workspaces.switch_branch` (`git switch [-c] -- <branch>`). Branch names starting with `-` or containing whitespace are rejected; the `--` separator is belt-and-braces.

### Attachments (images + spreadsheets)

Operators drag-drop or paperclip files into the Chat tab or canvas chat dialog. Supported:

- **Images:** `.png` `.jpg` `.jpeg` `.gif` `.webp` â€” passed through to the CLI as `@<path>` references the model can `Read`. With Pillow installed, oversized images are downscaled to 1600 px on the long edge (JPEG re-encoded at quality 85). `MAX_IMAGE_PIXELS = 50,000,000` guards against decompression bombs. GIFs are passed through unchanged to preserve animation.
- **Spreadsheets:** `.xlsx` `.xls` `.csv` â€” rendered to one fenced markdown table per sheet, **once at upload time**, capped at 200 rows Ã— 30 cols per sheet with truncation markers. Subsequent sends reuse the cached `rendered_text` so we don't re-parse. `openpyxl` for `.xlsx`, `xlrd` for `.xls` (with `release_resources()` so the file isn't held mmap-open on Windows). Missing optional dep falls back to embedding raw bytes with a "could not render" warning.

**Hard 25 MB upload cap** â€” pre-checked at the GUI before reading + base64-encoding (which run in `asyncio.to_thread` so a big file doesn't freeze the event loop), and re-checked at the RPC. **Sanitized filename** rejects whitespace / `@` / `\n\r\t` so the `@<path>` token can't break the CLI's prompt tokenizer or smuggle in extra files. The data dir's path is also checked for whitespace at upload time for the same reason.

**Storage layout:** `<data_dir>/attachments/<agent_id>/<id>__<sanitized_name>`. Schema in `apps/service/store/schema.sql` under `CREATE TABLE attachments` with `ON DELETE CASCADE` on `agent_id`. **Cross-agent auth** â€” every `attachments.delete`, `.list`, and the internal `update_attachment_turn` require the `agent_id` and refuse to act on rows that don't belong to it.

When a Reaper Drone references another Agent (see below), the referencing Agent's prompt also gets the referenced Agent's spreadsheet `rendered_text` inlined (capped 100 KB total across all references). Image attachments don't transfer through references â€” re-attach them if the new Agent needs to see them.

The **Limits â†’ Attachment storage** card surfaces total disk usage broken down by agent.

### Peer Communication (References)

Reaper Drones from independent contexts can "talk" to each other when linked by the operator. Each referenced unit's full conversation history is injected into the agent's system prompt as read-only context. This is cross-provider safe: a Gemini-CLI Agent reading a Claude-CLI Agent's transcript just sees plain text.

Established via:
- **Standalone:** "Edit references" button in the Drones or Agents chat pane.
- **Canvas:** Drawing a non-directional edge between two drone nodes.

Peer history is capped at 20,000 characters to prevent blowing the context window while still providing deep shared memory.

### Flow Canvas (executable graphs)

A `Flow` is `{nodes: [...], edges: [...]}` plus name, description, version, `is_draft`. Node types:

- **Trigger** â€” entry point. No inputs.
- **Reaper Drone** â€” references a `card_id`; its `params.goal` overrides the upstream input as the prompt.
- **Consensus** â€” fans out one question to multiple candidate cards, then fans in through a judge card to produce a ranked/merged outcome.
- **Branch** â€” boolean condition; routes to the `true` or `false` port. Downstream nodes whose only inputs come through the not-taken port are marked `skipped`.
- **Merge** â€” concatenates inputs.
- **Human** â€” pauses the run; emits `flow.node.human_pending` and waits for `flows.approve_human` from the GUI.
- **Output** â€” terminal sink.

The **FlowExecutor** validates the graph (no cycles, no dangling edges), topologically sorts, and dispatches in **waves** â€” every node whose dependencies are all complete runs concurrently via `asyncio.gather`. Cancellation cascades to in-flight node tasks (so child CLI subprocesses get reaped). Per-card cache is pre-populated once per run to avoid stampeding `store.list_cards()`.

Events: `flow.node.queued / started / token_delta / completed / failed / skipped / human_pending` flow through the EventBus and SSE channel keyed by the `flow_run_id`.

`flows.update` supports **optimistic concurrency** via an `expected_version` param (the GUI passes the version it last fetched; mismatch raises `FlowVersionConflict` so the operator sees "reload before saving" rather than a silent overwrite).

`flows.delete` cancels any in-flight executor tasks for that flow before deleting the row.

### Workspaces & worktrees

A `Workspace` is a registered local git working tree. The **WorktreeManager** owns four things:

- `register_workspace(path)` â€” validate, mark `.agent-worktrees/` as excluded, persist the row.
- `clone_workspace(url, dest_dir, ...)` â€” clone first, then register.
- `create(run_id, workspace, card)` â€” for an agentic Run: cut a `agent/<run-id>` branch + worktree under `<repo>/.agent-worktrees/<run-id>`, isolated from the user's main checkout.
- `commit / merge / approve / reject / abandon` â€” life-cycle for the branch the agent works on.

A **per-workspace file lock** prevents two runs from clobbering the same `.agent-worktrees/` directory. Stale runs are swept on a timer; the **Drift sentinel** notices when an agent commit has wandered too far off the base ref.

### MCP server registry

`apps/service/mcp/` keeps a typed list of MCP servers (stdio transport) the operator has explicitly trusted. Cards reference servers by name in their `tool_allowlist`. Trust states: `UNKNOWN` / `BLOCKED` / `TRUSTED`. Untrusted / blocked / unknown / non-stdio entries are skipped at run dispatch with a logged warning so a Run never exposes a tool the user hasn't explicitly trusted.

### Voice dictation

`apps/service/dictation/whisper.py` wraps `faster-whisper` with a lazy import so the orchestrator runs without it. Audio path is **resolved + checked**: must be a regular file, must have an allow-listed audio extension (`.wav .mp3 .m4a .ogg .flac .webm`). Transcription runs in a thread (Qt thread pool side); model is cached per size. Compose tab ships the entry point.

---

## Architecture

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  GUI process                                               â”‚
â”‚  PySide6 + qasync â€” single window, 8 rail tabs             â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                         â”‚ JSON-RPC over 127.0.0.1:8765 (token-auth)
                         â”‚ SSE for live event streams (run-id channels)
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  Service process (Python 3.11+, asyncio, uvicorn)          â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚ DISPATCH       â”‚  INGESTION      â”‚ STATE / STORE     â”‚  â”‚
â”‚  â”‚ chat.send      â”‚ JSONL watcher   â”‚ SQLite + FTS5     â”‚  â”‚
â”‚  â”‚ agents.send    â”‚ Hook receiver   â”‚ Event log         â”‚  â”‚
â”‚  â”‚ runs.dispatch  â”‚                 â”‚ WorktreeMgr       â”‚  â”‚
â”‚  â”‚ FlowExecutor   â”‚ Stream parsers  â”‚ Cards + Templates â”‚  â”‚
â”‚  â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤  â”‚
â”‚  â”‚ PROVIDERS      â”‚  TOOLS          â”‚ POLICY            â”‚  â”‚
â”‚  â”‚ claude-cli     â”‚ MCP registry    â”‚ Cost meter        â”‚  â”‚
â”‚  â”‚ gemini-cli     â”‚ Worktree tools  â”‚ Pre-flight linter â”‚  â”‚
â”‚  â”‚ anthropic API  â”‚ Whisper         â”‚ Approvals (HITL)  â”‚  â”‚
â”‚  â”‚ google API     â”‚ Attachments     â”‚ Drift sentinel    â”‚  â”‚
â”‚  â”‚ ollama HTTP    â”‚ render pipeline â”‚ Keyring secrets   â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

The service is started by `agentorchestra-service`. The GUI (`agentorchestra-gui`) auto-spawns the service if it isn't running (`apps/gui/service_supervisor.py`, with `CREATE_NO_WINDOW` on Windows so you don't see a console pop). On Quit, the GUI runs `RpcClient.aclose()` to completion via `loop.run_until_complete` so the httpx pool / TLS sockets close cleanly.

---

## Where data lives

**Default data directory:** `~/.local/share/agentorchestra/` (Linux/macOS) or `%LOCALAPPDATA%\agentorchestra\` (Windows).

```
<data_dir>/
â”œâ”€â”€ agentorchestra.sqlite      single SQLite DB (WAL + FTS5)
â”œâ”€â”€ agentorchestra.sqlite-wal
â”œâ”€â”€ agentorchestra.sqlite-shm
â”œâ”€â”€ attachments/<agent_id>/    uploaded images + spreadsheets
â”œâ”€â”€ clones/<repo-name>/        managed git clones (workspaces.clone)
â””â”€â”€ logs/                      service rotating log
```

Schema is `apps/service/store/schema.sql`, applied via `executescript()` at startup. Column-additive migrations live in `EventStore._migrate` (e.g. `agents.workspace_id`, `agents.parent_preset`, `agents.reference_agent_ids`) with a `_has_column` guard so they're idempotent.

The token used for RPC auth is stored in the **OS keyring** (`keyring` package) under service `agentorchestra` / username `rpc-token`. Service writes it on first start; GUI reads it.

---

## Safety model

- **Subscription-only by default.** No API key path is opened in the standard chat / agent flow. The `anthropic` and `google` providers exist for the API-key route to the worktree-bound dispatcher, but the chat surface stays on `claude-cli` / `gemini-cli` / `codex-cli`.
- **Localhost-only RPC.** The service binds `127.0.0.1:8765`. Token auth on top so a malicious local browser tab can't enumerate.
- **Sandbox tiers** â€” `LocalSandbox` (default), `DockerSandbox` (cap-drop ALL, no-new-privileges, read-only root + tmpfs `/tmp`, no network unless card opts in, bind-mount of the worktree at `/workspace`). The DockerSandbox passes file paths via positional `sh` args (`sh -c '... "$1"'` style) so a malicious filename can't break out of the shell quoting.
- **Path-injection guards** on every operator-supplied path: attachments filename rejects whitespace / `@` / `\n\r\t`; dictation audio paths must be regular files with allow-listed extensions; workspaces.clone refuses URLs starting with `-` or containing control chars (allows `@` so SSH URLs `git@github.com:â€¦` work); switch_branch refuses names starting with `-` or containing whitespace.
- **Cross-agent attachment auth** on every attachment RPC.
- **CSRF / replay** â€” RPC token is a random 256-bit secret minted at first start.
- **HITL approval gates** â€” the `Approval` table records who approved what when, with `risk_signals` JSON and a free-form note.
- **Cost caps** per-card with a hard cap (aborts) and soft cap (warns once).
- **Shadow-Plan guard** â€” autonomous mutation tools are state-gated; code modifications require recent intent captured in `PLAN.md` or the run receives `403 Shadow-Plan Violation`.
- **Autonomous turn cap** â€” autonomous tool loops are hard-capped at 15 turns.
- **Drift sentinel** flags worktree branches that have wandered too far from the base ref before merge.
- **Coding-session prompt header** explicitly tells the agent not to push / force / `rm -rf` without explicit go-ahead. The CLI's own permission prompts still apply for write tools.

---

## Full RPC reference

All under `127.0.0.1:8765`, JSON-RPC body, `Authorization: Bearer <token>`. Streaming responses use SSE on `/events/<channel>`.

### Workspaces (project repos)

| Method | Purpose |
|---|---|
| `workspaces.list` | All registered workspaces. |
| `workspaces.register` | Register an existing local working tree. |
| `workspaces.remove` | Unregister (rows in `runs.workspace_id` stay set so history isn't lost). |
| `workspaces.tree` | Bounded gitignore-respecting file listing (`git ls-files` if a git repo, else bounded `rglob`). |
| `workspaces.clone` | `git clone <url> -> <dest>` then `register_workspace`. |
| `workspaces.git_status` | Branch, ahead/behind, modified/staged/untracked counts, last commit. |
| `workspaces.switch_branch` | `git switch [-c] -- <branch>`. |

### Reaper Drones (named persistent conversations)

| Method | Purpose |
|---|---|
| `agents.list` | All agents enriched with `workspace_name` + `workspace_path`. |
| `agents.get` | One agent enriched. |
| `agents.create` | Mint a new agent (name, provider, model, system, optional refs + workspace). |
| `agents.send` | Append a user turn, optionally attach files, get a reply. Per-agent send-lock serialises concurrent sends. |
| `agents.spawn_followup` | Mint a child Agent seeded with the parent's transcript + a follow-up preset instruction. |
| `agents.set_references` | Replace `reference_agent_ids`. |
| `agents.set_workspace` | Bind / unbind to a workspace. |
| `agents.delete` | Remove + drop the per-agent send-lock entry. |
| `agents.followup_presets` | The 6 presets above (label + body). |

### Attachments

| Method | Purpose |
|---|---|
| `attachments.upload` | Persist + render (markdown for spreadsheets, optional resize for images). 25 MB cap. |
| `attachments.list` | Per-agent â€” requires `agent_id`. |
| `attachments.delete` | Per-agent â€” requires both `id` and `agent_id`. |
| `attachments.usage` | Per-agent + grand totals. Renders the Limits-tab storage card. |

### Cards / Templates / Runs / Branches

`cards.list`, `templates.render`, `templates.get`, `runs.list`, `runs.dispatch`, `runs.approve`, `runs.reject`, `runs.cancel`, `runs.replay`, `runs.consensus`, `runs.select_consensus_winner`, `runs.approve_plan`, `runs.artifacts`.

### Flows

`flows.list`, `flows.get`, `flows.create`, `flows.update` (with `expected_version`), `flows.delete` (cancels active runs first), `flows.dispatch`, `flows.cancel`, `flows.approve_human`.

### Chat (one-shot)

`chat.send` â€” single-turn chat used by some non-Agent surfaces.

### Limits

`limits.check` (CLI probes), `limits.usage` (local tally per provider per window).

### Hooks (Claude Code)

`hooks.status`, `hooks.install`, `hooks.uninstall`, `hook.received` (callback path the hook scripts POST into).

### MCP servers

`mcp.list`, `mcp.add`, `mcp.trust`, `mcp.block`, `mcp.remove`.

### Dictation / Search / Misc

`dictation.status`, `dictation.transcribe`, `search`, `lint.instruction`, `cost.forecast`, `providers`.

---

## Development

Requires Python 3.11+ and `git` on PATH.

```bash
python -m venv .venv
.\.venv\Scripts\activate          # Windows
# or
source .venv/bin/activate          # Linux/macOS

pip install -e ".[dev,gui,google]"

# Run the unit tests (no network, no real CLIs)
pytest tests/unit -q

# Integration tests (touch the filesystem, spawn git)
pytest tests/integration -q

# Lint + type-check
ruff check .
ruff format --check .
mypy apps packs

# Start the orchestrator service (default port 8765)
agentorchestra-service

# Start the GUI (auto-spawns the service if it isn't running)
agentorchestra-gui
```

CI runs lint + ruff format-check + the full test suite on GitHub Actions on every PR.

---

## Project layout

```
apps/
â”œâ”€â”€ gui/                         PySide6 GUI process
â”‚   â”œâ”€â”€ canvas/                  Flow + ConversationNode canvas
â”‚   â”‚   â”œâ”€â”€ page.py              CanvasPage orchestrator
â”‚   â”‚   â”œâ”€â”€ chat_dialog.py       Per-Agent chat dialog
â”‚   â”‚   â”œâ”€â”€ lineage_box.py       Translucent cluster wrap
â”‚   â”‚   â”œâ”€â”€ nodes/               BaseNode, ConversationNode, AgentNode, â€¦
â”‚   â”‚   â”œâ”€â”€ edges.py             Directional + labelled edges
â”‚   â”‚   â”œâ”€â”€ palette.py           Left palette + + New conversation
â”‚   â”‚   â””â”€â”€ commands.py          Undo-stack QUndoCommand subclasses
â”‚   â”œâ”€â”€ windows/                 Tabs (chat, agents, composer, history, limits, settings, â€¦)
â”‚   â”œâ”€â”€ ipc/                     RpcClient (httpx) + SSE consumer
â”‚   â”œâ”€â”€ annotator.py             Optional pyside6_annotator integration
â”‚   â””â”€â”€ service_supervisor.py    Auto-spawn the service
â””â”€â”€ service/                     Orchestrator service process
    â”œâ”€â”€ main.py                  ASGI entrypoint, RPC registration
    â”œâ”€â”€ types.py                 Domain types: Agent, Workspace, Flow, FlowRun,
    â”‚                            PersonalityCard, Run, Branch, Step, Approval,
    â”‚                            Outcome, Event, Attachment, â€¦
    â”œâ”€â”€ attachments/             render pipeline (csv / xlsx / xls / images)
    â”œâ”€â”€ agents/                  follow-up presets + instruction renderer
    â”œâ”€â”€ cards/                   Seed cards (Broad-Research, QA-on-fix, â€¦); CRUD lives in store/events.py
    â”œâ”€â”€ cost/                    Forecasts + price tables
    â”œâ”€â”€ dispatch/                ChatSession + Run lifecycle + EventBus + drift sentinel
    â”œâ”€â”€ dictation/               faster-whisper wrapper
    â”œâ”€â”€ flows/                   FlowExecutor (waves, cancellation, validation)
    â”œâ”€â”€ hitl/                    Approval gates
    â”œâ”€â”€ ingestion/               JSONL watcher, Claude hook receiver, OTel
    â”œâ”€â”€ ipc/                     JSON-RPC Starlette server + SSE
    â”œâ”€â”€ limits/                  Hardcoded plan registry + context_windows()
    â”œâ”€â”€ linter/                  Pre-flight instruction linter
    â”œâ”€â”€ mcp/                     MCP server registry + client
    â”œâ”€â”€ providers/               LLMProvider adapters: anthropic, claude_cli,
    â”‚                            codex_cli, google, gemini_cli, ollama
    â”œâ”€â”€ sandbox/                 LocalSandbox + DockerSandbox
    â”œâ”€â”€ secrets/                 OS-keyring wrapper
    â”œâ”€â”€ store/                   schema.sql + EventStore (aiosqlite + FTS5)
    â”œâ”€â”€ templates/               Banks / Jinja2 template engine
    â”œâ”€â”€ updates/                 Signed-update verifier
    â””â”€â”€ worktrees/               WorktreeManager + git_cli wrapper + merger
packs/
â”œâ”€â”€ archetypes/                  Bundled cards (Broad-Research, QA-on-fix, â€¦)
â”œâ”€â”€ hooks/                       Claude hook scripts
â””â”€â”€ (otel-presets/ â€” planned, not shipped)
scripts/
â”œâ”€â”€ manifest.json                Operator-panel command manifest
â”œâ”€â”€ ops.cmd / ops.py             Panel host
â”œâ”€â”€ start.cmd / restart.cmd / stop.cmd / launch.cmd / setup.cmd
â”œâ”€â”€ test-claude.cmd / test-gemini.cmd
â”œâ”€â”€ update.cmd / doctor.cmd / reset.cmd
â””â”€â”€ limits.cmd
tests/
â”œâ”€â”€ unit/                        Per-module tests (no network)
â”œâ”€â”€ integration/                 Filesystem + git
â””â”€â”€ e2e/                         End-to-end flows
docs/
â”œâ”€â”€ user/
â””â”€â”€ dev/                         Architecture, design notes, runbooks
```

---

## License

Proprietary. See LICENSE (TBD).