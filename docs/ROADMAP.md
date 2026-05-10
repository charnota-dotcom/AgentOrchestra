# AgentOrchestra — Retrospective Development Roadmap

A factual record of how AgentOrchestra got built, what's actually merged on
`main`, and where it can sensibly go from here.

---

## 1. Origin + intent

AgentOrchestra exists because non-technical Windows operators who already pay
for **Claude Max** and a **Gemini CLI** subscription have no way to *combine*
those two assistants into a single, persistent, repo-aware workspace. The web
UIs are siloed and forget context between tabs; the CLIs are powerful but
require typing; cloud-orchestration tools require API keys (which means an
extra paid bill on top of the subscriptions the operator already owns) and
cloud round-tripping of source code.

The product target is concrete: a Windows desktop app that piggybacks on
whichever local CLIs the operator is already signed into, exposes a small
fixed set of model presets, persists every conversation as a first-class
"Agent", lets the operator **drag those agents onto a canvas** to see who can
see whom, and **binds them to a local git checkout** so the model's built-in
file tools operate on real code without anything leaving the machine.

The constraint that shapes every architectural decision is therefore *no API
keys in the chat path* and *Windows operator first*. Developer ergonomics
(CLI, tests, MCP) are real but secondary — they exist to keep the desktop
product honest, not as the primary surface.

---

## 2. Phases shipped — actually-merged work

Twelve PRs merged to `main`. The work falls naturally into five phases.

### Phase 1 — MVP scaffold (PR #1)

**Theme:** lay down the bones.

PR #1 is the single biggest commit in the history. It ships the repo layout
(`apps/`, `packs/`, `tests/`), the domain types (`PersonalityCard`,
`InstructionTemplate`, `Run`, `Branch`, `Step`, `Approval`, `Outcome`,
`Event`), the SQLite event store with FTS5 + WAL, the async git CLI wrapper,
the `WorktreeManager` with branch-per-run isolation, the `LLMProvider`
protocol with an Anthropic API adapter, the Banks-style template engine, the
pre-flight linter, the cost meter, the JSON-RPC service bound to
`127.0.0.1:8765`, and a four-page PySide6 GUI shell (Home / Compose /
History / Settings). Phase 1 is closed out in the same PR with end-to-end
agentic dispatch, the EventBus + SSE, the Live + Review panes, the diff
artifact, and a Code-Edit card.

Honest assessment: PR #1 was *not* clean — it is a multi-sprint super-PR that
covers what should have been four or five commits, but it gives every later
PR a coherent foundation to extend.

### Phase 2 — multi-vendor + GUI shell (PRs #2 – #5)

**Theme:** the subscription-only thesis becomes real.

- **PR #2** — `claude-cli` and `gemini-cli` provider adapters. The pivotal
  decision: add subprocess-based providers that pipe through the operator's
  *already-signed-in* local CLIs, instead of asking for API keys. This is
  the moment AgentOrchestra stops being "yet another LangChain wrapper" and
  starts being a Max-plan / Gemini-subscription product.
- **PR #3** — wires `pyside6_annotator` overlay onto the main window;
  isolates the `pyside6_annotator` dep into its own optional extra so the
  base install stays small.
- **PR #4** — Composer hardening: the form is built from the template's
  *actual* declared variables (so renaming a variable doesn't silently strip
  the field); empty required vars now error early; live-truncation cap is
  raised; `claude-cli` falls back to plain text when stdout isn't JSON.
- **PR #5** — GUI polish: per-row actions on the History page (Replay /
  remove-workspace), Gemini-CLI variants of the Broad / Narrow research
  archetypes, swap between `claude-cli` and `gemini-cli` in the Settings UI.

Phase 2 also lands the multi-vendor groundwork in `CHANGELOG.md` (Gemini and
Ollama agentic adapters, per-card `fallbacks`, runs.replay, Claude hook
bridge, auto-QA, cost caps, Red-Team / Tracker / cross-vendor consensus
archetypes, plan-act split with HITL gate, workspace map widget).

### Phase 3 — agentic parity + sandbox (PRs #6 – #7)

**Theme:** the operator is human, give them buttons.

- **PR #6** — first wave of operator scripts: `launch.cmd`, `stop.cmd`,
  Flow Canvas + GUI auto-spawning the service, the Chat tab as a
  lay-person agent UI, and the deliberate dropping of API-keyed providers
  from the *default* registry. The Flow Canvas (per
  `docs/FLOW_CANVAS_PLAN.md`) lands as a `QGraphicsView`-based scene with
  named agents, multi-turn chat, minimap, auto-layout, and undo.
- **PR #7** — ships `setup.cmd / launch.cmd / stop.cmd / update.cmd /
  doctor.cmd / reset.cmd`, the clickable PySide6 **Operator Panel**
  (`ops.cmd`), `test-claude.cmd` / `test-gemini.cmd` smoke-tests, the
  Chat-tab model picker with general / file / image modes baked in, and a
  numbered step-by-step flow on the panel. The Phase-3 changelog entry
  describes the parallel backend work: `Sandbox` protocol with
  `LocalSandbox` + `DockerSandbox` (cap-drop ALL, no-network, --read-only,
  worktree bind-mount), Mergiraf merge-driver wire-up, MCP server registry
  with trust-on-first-use, the first-run wizard, voice dictation via
  `faster-whisper`, the drift sentinel, and a Briefcase config + signed
  update-manifest verifier.

### Phase 4 — chat + canvas + agents + limits (PRs #8 – #11)

**Theme:** the canvas becomes a workbench, not a demo.

- **PR #8** — `RpcClient` surfaces the service's actual error body on 5xx
  (no more silent `Internal Error` toasts).
- **PR #9** — slug-safe ephemeral card archetype on the Chat / Agents path
  (a real bug: the chat tab was minting cards with archetype names
  containing whitespace, which then failed the cards.list filter).
- **PR #10** — `stop.cmd` now reaps the supervisor-spawned service too
  (netstat-based port-listening kill on 8765, on top of the window-title
  kill).
- **PR #11** — the Phase-4 marquee: `canvas.create new conversations` +
  visibility toggle, drag conversations onto the canvas, double-click to
  chat, **auto-drawn labelled directional lineage edges**, in-app **Limits
  tab** with a 5-minute cooldown, the `restart.cmd` everyday button,
  `start.cmd` pre-flight verifier pinned to the top of the operator
  panel, fix for `gemini-cli` workspace-trust env, **chat sessions
  auto-persist as Agents**, **subscription-limits panel** showing plan
  caps + per-model context windows + local message tally, **cross-chat
  references** (one agent's transcript folded into another's prompt),
  **canvas lineage cluster boxes + draft mode**.

### Phase 5 — attachments + repo-aware coding + audit hardening + UX mirror (PR #12)

**Theme:** make the desktop app honest under pressure.

PR #12 is the second multi-sprint super-PR. It bundles five distinct lines of
work that probably should have been separate PRs but landed together:

1. **Image + Excel attachments end-to-end** — paperclip + drag-drop, 25 MB
   cap, Pillow-based image resizing, `.xlsx` / `.xls` / `.csv` rendered to
   markdown tables once at upload time, per-agent storage + cross-agent
   auth checks on every attachment RPC.
2. **Repo-aware coding sessions** — bind an Agent to a Workspace, the CLI
   then runs with `cwd=<repo_path>` so its built-in `Read` / `Bash` /
   `Edit` tools operate on the project; richer system prompt naming the
   repo + branch and inlining the first project-convention file
   (`CLAUDE.md` / `AGENTS.md` / `GEMINI.md` / `.cursorrules` /
   `.cursor/rules.md`, capped 8 KB); live git-status banner; switch-branch
   button; `Clone from git…` dialog.
3. **Async-correctness audit** — lost-update + connection-sharing fixes
   across the store layer.
4. **Three audit batches** — A (HIGH security + crash + lifecycle, including
   a critical sandbox-injection fix and five HIGH leaks), B (MEDIUM UX +
   perf + budget), C (LOW drag-drop + shortcut + flow-cancel polish).
5. **Comprehensive README rewrite** — the 520-line operator-facing doc.

---

## 3. Architectural through-lines

Six decisions show up in every phase and have not been reversed:

- **Subscription-only by default.** The chat path uses `claude-cli` /
  `gemini-cli` only; API providers exist but are off the default registry
  (PR #6's "drop API-keyed providers from the default registry + UI"). No
  `ANTHROPIC_API_KEY` is required to use the product.
- **Single SQLite store with FTS5 + WAL** — every Run, Branch, Step,
  Approval, Outcome, Event, Agent, Attachment, Flow, FlowRun, Workspace,
  Card, Template, Instruction lives in one file. Column-additive migrations
  in `EventStore._migrate` with a `_has_column` guard so they're idempotent.
- **Per-process boundary: GUI ↔ service over local JSON-RPC + SSE.** The
  service binds `127.0.0.1:8765` with bearer-token auth from the OS keyring;
  the GUI auto-spawns it via `service_supervisor.py` (with `CREATE_NO_WINDOW`
  on Windows). Streaming is SSE on `/events/<channel>`. This makes the
  product trivially scriptable — every UI action is also an RPC.
- **Branch-per-agent via `WorktreeManager`.** Every agentic Run gets its own
  `agent/<run-id>` branch in `<repo>/.agent-worktrees/<run-id>`, with a
  per-workspace file lock, stale-sweep timer, and panic-reset.
- **Provider abstraction stable across CLI + API.** The same `LLMProvider`
  protocol covers `anthropic`, `claude-cli`, `google`, `gemini-cli`, and
  `ollama`. Stream events normalise to `text_delta` / `assistant_message`
  / `tool_call` / `tool_result` / `usage` / `finish` / `error` regardless of
  vendor.
- **Frozen presets registry (`apps/gui/presets.py`).** Single source of
  truth for the model + thinking-depth + skills picker, shared by the Chat
  tab, the Canvas "+ New conversation" dialog, and the Agents-tab "+ New
  agent" dialog. Both registries are exported as tuples so a buggy consumer
  can't mutate them.

---

## 4. Where we are now

PR #13 is the in-flight follow-up to #12. The unmerged commits on the
working branch:

- **`apps/gui/presets`** — shared registry promotion. The Chat tab loses its
  local `_MODEL_PRESETS` / `_THINKING_PRESETS` / `_label_for` /
  `_skills_to_system` definitions and consumes the new module instead. The
  Canvas "+ New conversation" dialog is redesigned to match (provider
  filter + full 12-row picker + thinking-depth dropdown + skills field).
  The Agents-tab "+ New agent" dialog slices `MODEL_PRESETS` for
  Coding-mode rows and guards `currentIndex() == -1`.
- **Draft-canvas amber banner** explaining that Run is disabled but model /
  thinking / skills / repo binding behave the same as the Chat tab.
- **Mid-thread thinking / skills changes** show an amber hint that the
  change applies to the next New chat (the system prompt is locked at agent
  creation).
- **Repo-aware coding sessions polish** — clone-from-git, live git status,
  project conventions inlining (#12 carryover that gets its final UX in
  #13).
- **Comprehensive QA hardening** — async-correctness, attachments lifecycle,
  drag-drop, shortcut, flow-cancel.

The shipped product is functional end-to-end on Windows for one operator on
one machine, with everything under `~/.local/share/agentorchestra` (or
`%LOCALAPPDATA%\agentorchestra`).

---

## 5. Forward-looking roadmap

### Now (1 – 2 PRs)

- **Worktree-bound dispatch via Claude Code CLI.** The `apps/service/dispatch/a2a.py`
  V5 stub already specifies the wire format. Wire it to a real run path so
  agentic Runs can dispatch through the same Max-plan auth the chat path
  uses, instead of the API-keyed `anthropic` provider — closes the last
  "API key required" gap in the product.
- **Per-agent context-window estimate ribbon.** The Limits tab already knows
  the per-model token budget via `context_windows()` and the local send
  count. Surface the fraction-used directly on each Agent's chat dialog
  header so an operator sees "≈ 38 % of 200 K used" before the next send.
  Pure-frontend; no schema change.
- **Automated annotator-response writing.** The annotator overlay (PR #3)
  is read-only today. Hook it to the existing `agents.spawn_followup` with
  the `annotate` preset so that highlighting a span on screen mints a
  child agent whose first message is the operator's note plus the
  selection.

### Next (3 – 5 PRs)

- **Image generation + audio in/out.** The provider abstraction handles
  multi-modal in for images already (Phase 5). Add an image-generation
  capability flag and route to whichever provider exposes one (e.g.
  Gemini `imagen`); add audio-in beyond the existing dictation by streaming
  CLI audio output to the OS player, and audio-out by accepting voice prompts
  on the chat tab.
- **MCP server marketplace.** The MCP registry (Phase 3 / PR #7) already
  has trust-on-first-use + SHA-256 fingerprints. Add a discovery surface
  in Settings: a curated list of community servers, one-click trust, and a
  test-call button.
- **Multi-repo agents.** Today an Agent binds to one workspace. Lift the
  constraint so an Agent can hold a list of `workspace_id`s, with the
  `cwd` selected per-send via a small picker. Implies a schema change
  (`agents.workspace_ids JSON` migration, kept backward-compatible by
  reading the legacy single-id column when the JSON is null).
- **PR comment ingestion.** Use the `gh` CLI to ingest PR review comments
  as `HandoffCard` events — closing the loop for the existing Tracker
  archetype.
- **Signed updater + Briefcase installer.** The verifier (`apps/service/updates/`)
  is done; Briefcase config exists; what's missing is the signing certs
  + distribution channel + a CI release workflow.
- **Headless cron-driven flows.** `flows.dispatch` is already a pure RPC.
  Add an `agentorchestra-cron` entrypoint that takes a flow id + cron
  expression and dispatches without the GUI, suitable for Windows Task
  Scheduler.

### Later (vision)

- **Team-shared workspace.** Replace the `127.0.0.1:8765` binding with an
  optional Tailscale-backed multi-operator mode, gated by per-user keyring
  tokens.
- **Run replay debugger.** `runs.replay` exists. Build a per-step time-slider
  UI on the History page that stops on each EventBus event and lets the
  operator see (and override) the next call.
- **Cost analytics + budgeting.** The cost meter records per-Run; aggregate
  to per-day / per-workspace / per-model dashboards on the Limits tab. Hard
  monthly budget caps with Slack-out warnings.
- **Plugin SDK for new providers / sandbox tiers.** The `LLMProvider` and
  `Sandbox` protocols are stable; promote them to a documented public API
  with an examples repo (Bedrock provider, Firecracker sandbox).
- **Encrypted attachments at rest.** The 25 MB-per-file attachments live as
  plain bytes on disk today; gate them behind an OS-keyring-derived AES key
  for operators on shared machines.

---

## 6. Key risks + open questions

- **Subscription rate limits, not API limits.** The Limits tab tells the
  operator the published cap, but the local CLIs do not expose a real-time
  remaining-budget number — an operator can hit a cap mid-flow with no
  recovery path beyond "wait until the window resets". A graceful degrade
  to a fallback provider on `RateLimitError` is in `PersonalityCard.fallbacks`
  but is not wired into the chat path.
- **Canvas scaling beyond ~50 agents.** `QGraphicsView` is fast, but the
  lineage-box recompute runs on every drag and the auto-layout uses
  Sugiyama on `networkx`. Both are O(n²)-ish. We have no real-world data
  past ~20 nodes.
- **Migration story when SQLite schema changes.** `EventStore._migrate` is
  column-additive only. The day a column needs to be *renamed* or
  *dropped*, or the FTS5 corpus needs a re-index, we have no
  pre-restore-tagged backup mechanism beyond the manual `.aobackup`
  format from Phase 4.
- **Distribution: the Briefcase signed installer never finished.** The
  config + manifest verifier are present (`briefcase.toml` +
  `apps/service/updates/manifest.py`), but no signed `.msi` / `.pkg` is
  shipped. Operators today install from source, which contradicts the
  non-technical-Windows-operator audience.
- **License is "Proprietary. See LICENSE (TBD)".** As long as that line
  stays in the README, no open-source contributor flow is possible — every
  external PR is legally ambiguous. A clear license decision is the
  unblocker for community contribution.
