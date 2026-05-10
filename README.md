# AgentOrchestra

**Desktop orchestrator for multi-vendor AI sub-agents on Windows.** Drive Claude Code (Max plan), Gemini CLI (Free / AI Pro / AI Ultra), and local Ollama models from a single PySide6 GUI: chat with one model, spawn follow-up agents that build on the prior transcript, drag persistent conversations onto a canvas to see who can see what, attach images and spreadsheets, bind agents to a specific git repo so they can read / edit code with their built-in file tools, and design dispatchable Flow graphs that hand a task off through Trigger → Agent → Branch → Merge → Human → Output nodes.

Subscription-only by default — no API keys are required for the day-to-day flow. Auth piggybacks on whatever your local `claude` and `gemini` CLIs are already signed in to.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Install & first run](#install--first-run)
3. [Operator panel (Windows .cmd scripts)](#operator-panel-windows-cmd-scripts)
4. [The GUI tabs in detail](#the-gui-tabs-in-detail)
   - [Home](#home) · [Chat](#chat) · [Agents](#agents) · [Compose](#compose) · [Canvas](#canvas) · [History](#history) · [Limits](#limits) · [Settings](#settings)
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

1. **Drives sub-agents** through one of four providers — `claude-cli` (Claude Code), `gemini-cli`, `anthropic` (API), `google` (API), `ollama` — and normalises their stream events into one shape (`text_delta`, `assistant_message`, `tool_call`, `tool_result`, `usage`, `finish`, `error`).
2. **Persists everything** to a single SQLite database (WAL mode, FTS5 search) — every Run, Branch, Step, Approval, Outcome, Event, Agent, Attachment, Flow, FlowRun, Workspace, Card, Template, Instruction.
3. **Owns git worktrees** when an agentic Run is dispatched — branch per agent, optional Mergiraf merge, panic reset, drift sentinel.
4. **Watches the host** — Claude session JSONL files and Claude Code hooks — so what you do interactively shows up in the orchestrator's history too.
5. **Exposes ~50 RPC methods** the GUI calls into. The full list is below.

The GUI presents this as **eight rail tabs** (Home, Chat, Agents, Compose, Canvas, History, Limits, Settings) plus two stack pages (Live, Review) reached when you dispatch a Run from Compose.

---

## Install & first run

**Requirements:** Windows, Python 3.11+, GitHub Desktop (or `git` on PATH), and at least one of:

- **Claude Code CLI** signed in via Max subscription — install from <https://docs.claude.com/en/docs/claude-code> then run `claude` once and `/login`.
- **Gemini CLI** signed in via Google AI Studio / Workspace SSO / `GEMINI_API_KEY` — install from <https://github.com/google-gemini/gemini-cli> then run `gemini` once.

Either is enough; both is better. **No Anthropic / Google API key is required** for the chat path.

Optional installs unlock specific features:

| Extra | Unlocks |
|---|---|
| `pip install -e ".[gui]"` | the PySide6 GUI (mandatory for the desktop app) |
| `pip install Pillow` | image resizing (else stored at original size) |
| `pip install openpyxl` | `.xlsx` rendering to markdown tables |
| `pip install xlrd` | legacy `.xls` rendering |
| `pip install faster-whisper` | local voice dictation in Compose |
| `pip install -e ".[google]"` | the Google API provider (only needed if you want to bypass the Gemini CLI) |

The recommended first-run flow uses the **Operator Panel** below — it walks you through it.

---

## Operator panel (Windows .cmd scripts)

`scripts/` is a self-contained set of one-click `.cmd` files designed for non-technical operators. Double-click `ops.cmd` from File Explorer (or pin it to the taskbar) and you get a numbered menu sourced from `scripts/manifest.json`.

| File | Step | What it does |
|---|---|---|
| `ops.cmd` | ★ | The Operator Panel itself — reads `manifest.json` so any new command shows up automatically. |
| `start.cmd` | ★ | **Pre-flight verifier.** Probes `claude -p "…"` and `gemini -p "…"` headlessly. If at least one CLI replies, launches the GUI. If both fail, aborts with instructions to fix the auth. Use first-of-day. |
| `restart.cmd` | ★ | **Everyday button.** Three-pass kill (window-title + port-listening on 8765) then launches a fresh GUI. Use after pulling a new commit, after `update.cmd`, or any time the running service is acting up. |
| `limits.cmd` | ★ | Print whatever subscription / usage info the local CLIs expose. The GUI's **Limits** tab is the in-app version. |
| `setup.cmd` | 1 | Create `.venv` and install AgentOrchestra + `[gui]` extras. Idempotent — re-run after a Python upgrade or if `.venv` goes missing. |
| `test-claude.cmd` | 2 | Smoke-test `claude` on PATH + a headless reply. If "Not logged in", run `claude` interactively and `/login`. |
| `test-gemini.cmd` | 3 | Smoke-test `gemini` on PATH + a headless reply. Skip if you only use Claude. |
| `launch.cmd` | 4 | Plain launch (no pre-flight). Faster than `start.cmd` when you trust the CLIs are signed in. |
| `stop.cmd` | 5 | Stop the GUI + service. Combines window-title kill with netstat-based port-listening kill so it catches both supervisor-spawned and manually-spawned services. |
| `update.cmd` | 6 | `git pull` + `pip install -e .` to refresh the venv. Always follow with `restart.cmd`. |
| `doctor.cmd` | 7 | Diagnose: prints Python version, venv health, port 8765 status, last service log lines. |
| `reset.cmd` | 8 | **Destructive.** Wipe the SQLite store. Use only when nothing else helps. |

The Operator Panel (`scripts/ops.py`) reads `manifest.json` so adding a new `.cmd` file plus a manifest entry surfaces it as a new button automatically — no GUI code change needed.

---

## The GUI tabs in detail

### Home

Landing page. Shows a **Workspaces map** (registered repos with their last activity), an **Active runs** table (in-flight Runs across all workspaces), and a **Recent runs** table with one-row-per-Run history. A Refresh button re-pulls all three. The first time you open the app, a **first-run wizard** (`first_run.py`) walks you through the three CLI smoke tests so you know your subscriptions work before sending real prompts.

### Chat

A lay-person chat box — pick a model preset, type a message, get a reply. Designed to feel exactly like the standard Claude.ai or Gemini web app, but routed through your local CLIs so you keep your subscription auth.

**Controls:**

- **Model picker** — entries from the shared `MODEL_PRESETS` registry in `apps/gui/presets.py` (12 rows: 3 Claude + 2 Gemini for Coding, plus 2 Claude + 1 Gemini for General Chat, plus 1 Claude + 1 Gemini each for File / artifact and Image prompt modes). Each preset is a `ModelPreset(label, provider, model, mode, system)` frozen dataclass. Switching presets mid-thread starts a new chat (a fresh Agent is minted on the next send) because the backend doesn't currently model-swap an existing Agent. The same registry powers the Canvas "+ New conversation" dialog and the Agents-tab "+ New agent" dialog so the three surfaces stay in sync.
- **Thinking** — four steps from `THINKING_PRESETS`: Off / Normal / Hard / Very hard. Adds a directive to the system prompt. Default is Normal. Mid-thread changes apply to the next new chat (the system prompt is locked at agent creation).
- **Skills** — free-form `/foo /bar baz` field. Inlined into the system prompt as instructions; not a real Skills invocation (that's interactive-only on the CLI side).
- **Repo:** picker — bind this conversation to a git repo workspace. When set, the underlying CLI runs with `cwd=<repo_path>` so the model's built-in `Read` / `Bash` / `Edit` / `Grep` tools operate against the project. **Add repo…** picks an existing local directory; **Clone from git…** opens a URL/branch/depth dialog and runs `git clone` into `<data_dir>/clones/`. Switching the repo restarts the chat (same constraint as the model picker).
- **Paperclip 📎** (or `Ctrl+Shift+A`) — attach an image (`.png/.jpg/.jpeg/.gif/.webp`) or spreadsheet (`.xlsx/.xls/.csv`) to the next message. You can also **drag a file from File Explorer onto the chat area**. Local files are cached on disk and uploaded only at send time (so they never orphan if you cancel). Attached chips wrap in a horizontal scroll so 50 files can't push the dialog off-screen.
- **Send** (or `Ctrl+Return`) — fires `agents.send`. The first message of a session auto-creates a persistent `Agent` so the conversation shows up in the Agents tab and the Canvas Conversations palette. Subsequent sends append to the same Agent's transcript. Empty messages with attachments are accepted (we synthesise *"Please review the attached file: x.png"*).
- **Save last reply…** — saves the most recent assistant turn to a file. Honours the leading `# filename.ext` header of File / artifact preset replies.
- **New chat (clear history)** — clears the in-memory mirror so the next send mints a fresh Agent. Pending uploaded attachments are deleted on the previous agent so they don't orphan.

### Agents

Browse, manage, and follow up on every persistent conversation. List view is sorted by recency; selecting an agent shows its transcript, parent (if any), workspace binding, references, and a **Spawn follow-up** panel.

**Spawn follow-up presets** (`FOLLOWUP_PRESETS` in `apps/service/agents/__init__.py`):

| Preset | Instruction the new agent receives |
|---|---|
| `summarise` | Produce a tight summary of the prior conversation. |
| `annotate` | Walk through the prior conversation and add inline notes. |
| `deep_dive` | Pick the most interesting thread and go deeper. |
| `critique` | Find weaknesses in the prior conversation's reasoning. |
| `verify` | Cross-check the prior conversation's facts. |
| `custom` | Operator-supplied instruction. |

The new Agent inherits the parent's provider + model by default but you can override either. The follow-up shows up on the Canvas as a directed lineage edge labelled with the preset name.

### Compose

The **operator-grade** instruction builder — for when you want a card-driven Run with a state machine, cost caps, and an approval gate, rather than a free-form chat.

- Pick a **PersonalityCard** (Broad Research, Narrow Research, QA-on-fix, Code-Edit, …). Cards are pydantic models with `provider`, `model`, `mode`, `cost: CostPolicy`, `blast_radius: BlastRadiusPolicy`, `sandbox_tier`, `tool_allowlist`, `fallbacks`, `auto_qa`, `requires_plan`, `max_turns`, …
- Pick an **InstructionTemplate** (Banks-style with Jinja2 + front-matter). Variables you fill in get rendered into the prompt; the rendered text is persisted as an `Instruction`.
- **Pre-flight linter** runs on the rendered text, surfacing risks (unbounded scope, ambiguous file paths, etc.) before dispatch.
- **Cost forecast** uses the card's `cost.input_per_1k_tokens` / `output_per_1k_tokens` plus the linter's token estimate; per-run hard cap aborts the run, soft cap warns once.
- **Voice dictation** button — opens the OS file picker, runs the audio through a local `faster-whisper` transcribe (fully on-device), drops the text into the instruction box. Audio extensions allow-listed; path resolved + checked at the RPC.
- **Dispatch** — kicks off a `runs.dispatch` which builds a worktree + branch and runs the agent loop with the configured tools.

### Canvas

Drag-and-drop graph editor. Two distinct things live on the canvas:

1. **AgentNodes** — wrap a `PersonalityCard` template. Used by the Flow executor to dispatch a fresh single-shot run when a Flow runs.
2. **ConversationNodes** — wrap a persistent `Agent`. Drop one onto the canvas and double-click to open its chat dialog. A 📂 marker shows in the subtitle when the agent is bound to a repo workspace; the tooltip spells out provider, model, turn count, repo + branch, parent (if a follow-up), and the visibility model.

**Lineage** — when a parent ConversationNode plus one or more of its descendants are on the canvas, AgentOrchestra auto-draws a translucent **LineageBox** around the cluster (so you can see who-belongs-with-whom at a glance) and labelled directional **edges** between them carrying the follow-up preset name. Cosmetic only; recomputes on every drag.

**Visibility toggle** — when one ConversationNode is selected, every other node dims so the cluster reads at a glance. The reverse highlight click clears it.

**Palette** — left panel lets you drop new AgentNodes (from card archetypes), Trigger / Branch / Merge / Human / Output flow nodes, and a **+ New Conversation** button that opens a dialog with name + model preset + workspace picker (with **Add…** and **Clone…** buttons reusing the same RPCs as the Chat tab). New conversations land on the canvas immediately.

**Draft mode** — the Flow's `is_draft` toggle. When draft, the **Run** button is greyed out so you can wire up a graph in a separate canvas without accidentally executing it.

**Edges** — drag from a node's output port to another's input. Edges have an arrowhead at the target (tangent-aware so it doesn't glitch on tight curves) and an optional rounded white pill label.

**Save / Run** — `Save` calls `flows.update`. `Run` calls `flows.dispatch` which kicks the FlowExecutor; events stream over SSE for the run-id.

**Per-Agent chat dialog** (double-click a ConversationNode):

- Header shows name, model, provider, parent (if any).
- **References** label + Edit references button — pick other Agents whose transcripts are inlined as a context preamble on every send (cross-provider safe). Capped at 100 KB total to avoid blowing the context window.
- **Workspace banner** (green) — `📂 Working in: <name> (<path>)`. Always-visible when bound. **Change repo** button opens a list of registered workspaces with **Add new repo…**.
- **Live git status banner** — when the workspace is a real git repo: `git: <branch> ↑2 ↓1 · 3 modified · last: abcd1234 …`. Refreshes on open and after every send.
- **Switch branch** button — opens a small input. `feature/x` switches; prefix with `+` (`+feature/new`) to `git switch -c`.
- Transcript above, paperclip + input + Send below. Same drag-drop and shortcut behaviour as the Chat tab.

### History

Read-only browser over every Run, Branch, Step, Approval, and Artifact. **FTS5 search** across instructions, artifacts, and salient event text — type anything in the search bar and you get ranked hits with `<b>highlighted</b>` snippets.

### Limits

**In-app subscription dashboard.** Lives at `apps/gui/windows/limits.py`. Refresh runs `limits.check` (which probes `claude --version` / `claude status` / `gemini --version` / `gemini status` headlessly) and `limits.usage` (which counts your own sends from the `provider_messages` table for daily / weekly / monthly windows).

**Cards rendered:**

- **One per provider** (Claude Code / Gemini CLI). Each has:
  - A plan picker (Pro / Max-5x / Max-20x / Team for Claude; Free / AI Pro / AI Ultra for Gemini). Plan registry lives at `apps/service/limits/__init__.py` with a `DATA_AS_OF` date so you know how stale the published numbers are.
  - Per-model message caps for the selected plan.
  - **Local tally** — `X / cap` rendered against your own send count for the relevant window. The tally is canonical for your own usage; the published cap is canonical for what your subscription buys.
  - Links to the official dashboards for the operator-of-truth.
- **Context-window summary card** — every model the orchestrator knows the token-budget for, in one sortable list. From `context_windows()`.
- **Attachment storage card** — total file count + bytes uploaded across all agents, plus a per-agent breakdown sorted by bytes. From `attachments.usage`. Useful when you want to know which agent is eating disk.

**Cooldown.** The Refresh button is gated to once per 5 minutes (`_REFRESH_COOLDOWN_SECONDS = 300`) so the CLI status calls — which take real subprocess time — can't be hammered.

### Settings

- **Service URL** (default `http://127.0.0.1:8765`).
- **Token** — sourced from the OS keyring. The service mints one at startup if missing; the GUI looks it up via `hook_token()`.
- **MCP server registry** — list / add / trust / block / remove MCP servers. Trusted servers are exposed to cards whose `tool_allowlist` includes them.
- **Hook installer** — install / uninstall the Claude Code hook scripts (`packs/hooks/`) so JSONL session files land in our ingestion path.

---

## Subsystems

### Repo-aware coding sessions

When an Agent has `workspace_id` set, the CLI runs with `cwd=<repo_path>` so its built-in tools operate against the project. Two ways to get there:

1. **Clone from a git URL** — Chat tab → Clone from git…, or Canvas palette → Clone…. Runs `git clone --quiet [-b <branch>] [--depth N] -- <url> <dest>` into `<data_dir>/clones/<sanitized-name>`. URLs starting with `-` and containing control chars are rejected; pre-existing dest paths are refused; half-finished clones are cleaned up on failure; 5-minute timeout.
2. **Register an existing local repo** — Add repo… picks a directory and runs `WorktreeManager.register_workspace`, which validates the path is a working tree (not a bare repo) and that no `agent/*` branches exist yet (so the worktree namespace is clean).

Once bound, every send to that Agent:

- Spawns the CLI subprocess with `cwd = ws.repo_path`.
- Builds a richer **system prompt** that:
  - Names the workspace and the **current branch**.
  - Tells the model not to push / force / `rm -rf` without explicit go-ahead, to prefer small reviewable diffs, and to run `git status` / `git diff` before non-trivial changes.
  - Inlines the **first project-convention file** found at the repo root: `CLAUDE.md` → `AGENTS.md` → `GEMINI.md` → `.cursorrules` → `.cursor/rules.md`. Capped at 8 KB with a truncation marker. Symlinks pointing outside the repo are refused.
- The canvas chat dialog refreshes the **live git status banner** after the send, so you see what changed.

The **Switch branch** button calls `workspaces.switch_branch` (`git switch [-c] -- <branch>`). Branch names starting with `-` or containing whitespace are rejected; the `--` separator is belt-and-braces.

### Attachments (images + spreadsheets)

Operators drag-drop or paperclip files into the Chat tab or canvas chat dialog. Supported:

- **Images:** `.png` `.jpg` `.jpeg` `.gif` `.webp` — passed through to the CLI as `@<path>` references the model can `Read`. With Pillow installed, oversized images are downscaled to 1600 px on the long edge (JPEG re-encoded at quality 85). `MAX_IMAGE_PIXELS = 50,000,000` guards against decompression bombs. GIFs are passed through unchanged to preserve animation.
- **Spreadsheets:** `.xlsx` `.xls` `.csv` — rendered to one fenced markdown table per sheet, **once at upload time**, capped at 200 rows × 30 cols per sheet with truncation markers. Subsequent sends reuse the cached `rendered_text` so we don't re-parse. `openpyxl` for `.xlsx`, `xlrd` for `.xls` (with `release_resources()` so the file isn't held mmap-open on Windows). Missing optional dep falls back to embedding raw bytes with a "could not render" warning.

**Hard 25 MB upload cap** — pre-checked at the GUI before reading + base64-encoding (which run in `asyncio.to_thread` so a big file doesn't freeze the event loop), and re-checked at the RPC. **Sanitized filename** rejects whitespace / `@` / `\n\r\t` so the `@<path>` token can't break the CLI's prompt tokenizer or smuggle in extra files. The data dir's path is also checked for whitespace at upload time for the same reason.

**Storage layout:** `<data_dir>/attachments/<agent_id>/<id>__<sanitized_name>`. Schema in `apps/service/store/schema.sql` under `CREATE TABLE attachments` with `ON DELETE CASCADE` on `agent_id`. **Cross-agent auth** — every `attachments.delete`, `.list`, and the internal `update_attachment_turn` require the `agent_id` and refuse to act on rows that don't belong to it.

When an Agent references another Agent (see below), the referencing Agent's prompt also gets the referenced Agent's spreadsheet `rendered_text` inlined (capped 100 KB total across all references). Image attachments don't transfer through references — re-attach them if the new Agent needs to see them.

The **Limits → Attachment storage** card surfaces total disk usage broken down by agent.

### Cross-chat references

Every Agent has `reference_agent_ids: list[str]`. Each referenced Agent's full transcript is wrapped in `=== Reference: <name> ===` markers and prepended to every prompt as read-only context. Cross-provider safe: a Gemini-CLI Agent reading a Claude-CLI Agent's transcript just sees plain text.

Set up via the **Edit references** button on the canvas chat dialog. Self-references are rejected.

### Flow Canvas (executable graphs)

A `Flow` is `{nodes: [...], edges: [...]}` plus name, description, version, `is_draft`. Node types:

- **Trigger** — entry point. No inputs.
- **Agent** — references a `card_id`; its `params.goal` overrides the upstream input as the prompt.
- **Branch** — boolean condition; routes to the `true` or `false` port. Downstream nodes whose only inputs come through the not-taken port are marked `skipped`.
- **Merge** — concatenates inputs.
- **Human** — pauses the run; emits `flow.node.human_pending` and waits for `flows.approve_human` from the GUI.
- **Output** — terminal sink.

The **FlowExecutor** validates the graph (no cycles, no dangling edges), topologically sorts, and dispatches in **waves** — every node whose dependencies are all complete runs concurrently via `asyncio.gather`. Cancellation cascades to in-flight node tasks (so child CLI subprocesses get reaped). Per-card cache is pre-populated once per run to avoid stampeding `store.list_cards()`.

Events: `flow.node.queued / started / token_delta / completed / failed / skipped / human_pending` flow through the EventBus and SSE channel keyed by the `flow_run_id`.

`flows.update` supports **optimistic concurrency** via an `expected_version` param (the GUI passes the version it last fetched; mismatch raises `FlowVersionConflict` so the operator sees "reload before saving" rather than a silent overwrite).

`flows.delete` cancels any in-flight executor tasks for that flow before deleting the row.

### Workspaces & worktrees

A `Workspace` is a registered local git working tree. The **WorktreeManager** owns four things:

- `register_workspace(path)` — validate, mark `.agent-worktrees/` as excluded, persist the row.
- `clone_workspace(url, dest_dir, ...)` — clone first, then register.
- `create(run_id, workspace, card)` — for an agentic Run: cut a `agent/<run-id>` branch + worktree under `<repo>/.agent-worktrees/<run-id>`, isolated from the user's main checkout.
- `commit / merge / approve / reject / abandon` — life-cycle for the branch the agent works on.

A **per-workspace file lock** prevents two runs from clobbering the same `.agent-worktrees/` directory. Stale runs are swept on a timer; the **Drift sentinel** notices when an agent commit has wandered too far off the base ref.

### MCP server registry

`apps/service/mcp/` keeps a typed list of MCP servers (stdio transport) the operator has explicitly trusted. Cards reference servers by name in their `tool_allowlist`. Trust states: `UNKNOWN` / `BLOCKED` / `TRUSTED`. Untrusted / blocked / unknown / non-stdio entries are skipped at run dispatch with a logged warning so a Run never exposes a tool the user hasn't explicitly trusted.

### Voice dictation

`apps/service/dictation/whisper.py` wraps `faster-whisper` with a lazy import so the orchestrator runs without it. Audio path is **resolved + checked**: must be a regular file, must have an allow-listed audio extension (`.wav .mp3 .m4a .ogg .flac .webm`). Transcription runs in a thread (Qt thread pool side); model is cached per size. Compose tab ships the entry point.

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│  GUI process                                               │
│  PySide6 + qasync — single window, 8 rail tabs             │
└────────────────────────┬───────────────────────────────────┘
                         │ JSON-RPC over 127.0.0.1:8765 (token-auth)
                         │ SSE for live event streams (run-id channels)
┌────────────────────────┴───────────────────────────────────┐
│  Service process (Python 3.11+, asyncio, uvicorn)          │
│  ┌────────────────┬─────────────────┬───────────────────┐  │
│  │ DISPATCH       │  INGESTION      │ STATE / STORE     │  │
│  │ chat.send      │ JSONL watcher   │ SQLite + FTS5     │  │
│  │ agents.send    │ Hook receiver   │ Event log         │  │
│  │ runs.dispatch  │                 │ WorktreeMgr       │  │
│  │ FlowExecutor   │ Stream parsers  │ Cards + Templates │  │
│  ├────────────────┼─────────────────┼───────────────────┤  │
│  │ PROVIDERS      │  TOOLS          │ POLICY            │  │
│  │ claude-cli     │ MCP registry    │ Cost meter        │  │
│  │ gemini-cli     │ Worktree tools  │ Pre-flight linter │  │
│  │ anthropic API  │ Whisper         │ Approvals (HITL)  │  │
│  │ google API     │ Attachments     │ Drift sentinel    │  │
│  │ ollama HTTP    │ render pipeline │ Keyring secrets   │  │
│  └────────────────┴─────────────────┴───────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

The service is started by `agentorchestra-service`. The GUI (`agentorchestra-gui`) auto-spawns the service if it isn't running (`apps/gui/service_supervisor.py`, with `CREATE_NO_WINDOW` on Windows so you don't see a console pop). On Quit, the GUI runs `RpcClient.aclose()` to completion via `loop.run_until_complete` so the httpx pool / TLS sockets close cleanly.

---

## Where data lives

**Default data directory:** `~/.local/share/agentorchestra/` (Linux/macOS) or `%LOCALAPPDATA%\agentorchestra\` (Windows).

```
<data_dir>/
├── agentorchestra.sqlite      single SQLite DB (WAL + FTS5)
├── agentorchestra.sqlite-wal
├── agentorchestra.sqlite-shm
├── attachments/<agent_id>/    uploaded images + spreadsheets
├── clones/<repo-name>/        managed git clones (workspaces.clone)
└── logs/                      service rotating log
```

Schema is `apps/service/store/schema.sql`, applied via `executescript()` at startup. Column-additive migrations live in `EventStore._migrate` (e.g. `agents.workspace_id`, `agents.parent_preset`, `agents.reference_agent_ids`) with a `_has_column` guard so they're idempotent.

The token used for RPC auth is stored in the **OS keyring** (`keyring` package) under service `agentorchestra` / username `rpc-token`. Service writes it on first start; GUI reads it.

---

## Safety model

- **Subscription-only by default.** No API key path is opened in the standard chat / agent flow. The `anthropic` and `google` providers exist for the API-key route to the worktree-bound dispatcher, but the chat surface stays on `claude-cli` / `gemini-cli`.
- **Localhost-only RPC.** The service binds `127.0.0.1:8765`. Token auth on top so a malicious local browser tab can't enumerate.
- **Sandbox tiers** — `LocalSandbox` (default), `DockerSandbox` (cap-drop ALL, no-new-privileges, read-only root + tmpfs `/tmp`, no network unless card opts in, bind-mount of the worktree at `/workspace`). The DockerSandbox passes file paths via positional `sh` args (`sh -c '... "$1"'` style) so a malicious filename can't break out of the shell quoting.
- **Path-injection guards** on every operator-supplied path: attachments filename rejects whitespace / `@` / `\n\r\t`; dictation audio paths must be regular files with allow-listed extensions; workspaces.clone refuses URLs starting with `-` or containing control chars (allows `@` so SSH URLs `git@github.com:…` work); switch_branch refuses names starting with `-` or containing whitespace.
- **Cross-agent attachment auth** on every attachment RPC.
- **CSRF / replay** — RPC token is a random 256-bit secret minted at first start.
- **HITL approval gates** — the `Approval` table records who approved what when, with `risk_signals` JSON and a free-form note.
- **Cost caps** per-card with a hard cap (aborts) and soft cap (warns once).
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

### Agents (named persistent conversations)

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
| `attachments.list` | Per-agent — requires `agent_id`. |
| `attachments.delete` | Per-agent — requires both `id` and `agent_id`. |
| `attachments.usage` | Per-agent + grand totals. Renders the Limits-tab storage card. |

### Cards / Templates / Runs / Branches

`cards.list`, `templates.render`, `templates.get`, `runs.list`, `runs.dispatch`, `runs.approve`, `runs.reject`, `runs.cancel`, `runs.replay`, `runs.consensus`, `runs.approve_plan`, `runs.artifacts`.

### Flows

`flows.list`, `flows.get`, `flows.create`, `flows.update` (with `expected_version`), `flows.delete` (cancels active runs first), `flows.dispatch`, `flows.cancel`, `flows.approve_human`.

### Chat (one-shot)

`chat.send` — single-turn chat used by some non-Agent surfaces.

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
├── gui/                         PySide6 GUI process
│   ├── canvas/                  Flow + ConversationNode canvas
│   │   ├── page.py              CanvasPage orchestrator
│   │   ├── chat_dialog.py       Per-Agent chat dialog
│   │   ├── lineage_box.py       Translucent cluster wrap
│   │   ├── nodes/               BaseNode, ConversationNode, AgentNode, …
│   │   ├── edges.py             Directional + labelled edges
│   │   ├── palette.py           Left palette + + New conversation
│   │   └── commands.py          Undo-stack QUndoCommand subclasses
│   ├── windows/                 Tabs (chat, agents, composer, history, limits, settings, …)
│   ├── ipc/                     RpcClient (httpx) + SSE consumer
│   ├── annotator.py             Optional pyside6_annotator integration
│   └── service_supervisor.py    Auto-spawn the service
└── service/                     Orchestrator service process
    ├── main.py                  ASGI entrypoint, RPC registration
    ├── types.py                 Domain types: Agent, Workspace, Flow, FlowRun,
    │                            PersonalityCard, Run, Branch, Step, Approval,
    │                            Outcome, Event, Attachment, …
    ├── attachments/             render pipeline (csv / xlsx / xls / images)
    ├── agents/                  follow-up presets + instruction renderer
    ├── cards/                   Seed cards (Broad-Research, QA-on-fix, …); CRUD lives in store/events.py
    ├── cost/                    Forecasts + price tables
    ├── dispatch/                ChatSession + Run lifecycle + EventBus + drift sentinel
    ├── dictation/               faster-whisper wrapper
    ├── flows/                   FlowExecutor (waves, cancellation, validation)
    ├── hitl/                    Approval gates
    ├── ingestion/               JSONL watcher, Claude hook receiver, OTel
    ├── ipc/                     JSON-RPC Starlette server + SSE
    ├── limits/                  Hardcoded plan registry + context_windows()
    ├── linter/                  Pre-flight instruction linter
    ├── mcp/                     MCP server registry + client
    ├── providers/               LLMProvider adapters: anthropic, claude_cli,
    │                            google, gemini_cli, ollama
    ├── sandbox/                 LocalSandbox + DockerSandbox
    ├── secrets/                 OS-keyring wrapper
    ├── store/                   schema.sql + EventStore (aiosqlite + FTS5)
    ├── templates/               Banks / Jinja2 template engine
    ├── updates/                 Signed-update verifier
    └── worktrees/               WorktreeManager + git_cli wrapper + merger
packs/
├── archetypes/                  Bundled cards (Broad-Research, QA-on-fix, …)
├── hooks/                       Claude hook scripts
└── (otel-presets/ — planned, not shipped)
scripts/
├── manifest.json                Operator-panel command manifest
├── ops.cmd / ops.py             Panel host
├── start.cmd / restart.cmd / stop.cmd / launch.cmd / setup.cmd
├── test-claude.cmd / test-gemini.cmd
├── update.cmd / doctor.cmd / reset.cmd
└── limits.cmd
tests/
├── unit/                        Per-module tests (no network)
├── integration/                 Filesystem + git
└── e2e/                         End-to-end flows
docs/
├── user/
└── dev/                         Architecture, design notes, runbooks
```

---

## License

Proprietary. See LICENSE (TBD).
