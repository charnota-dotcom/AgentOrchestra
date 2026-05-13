# Changelog

## Unreleased - Vocabulary alignment (2026-05)

- The current product vocabulary now uses **FPV Drone**, **Reaper Drone**, and **Staging Area**.
- Legacy labels remain documented only where they matter for compatibility or historical accuracy.
- Added a dedicated **Templates** tab for graph templates, with JSON-backed CRUD, validation, Mermaid export, and canvas deployment.

## Phase 7 - FPV Drones, Reaper Drones & Stability (2026-05)

Significant UX and reliability overhaul, refining the FPV Drone model and
introducing first-class autonomous Reaper Drone workflows.

- **FPV Drones vs. Reaper Drones Split** â€” Separate UI sections for manual browser-based units ("FPV Drones") and autonomous CLI-based units ("Reaper Drones").
- **Standalone Skills Management** - Dedicated "Skills" tab for full CRUD management of Reaper Drone superpower templates.  Auto-seeded with 20 popular templates (research, security, devops).
- **Blueprint Refinement** â€” Context-aware creation workflow with "+ FPV Drone" and "+ Reaper Drone" buttons.  Reaper Drones now use a mandatory popup skill selector instead of manual entry.
- **Legacy conversion compatibility** â€” "Convert to Reaper Drone" workflow on the canvas and in the blueprint editor, allowing manual conversations to be upgraded to autonomous workflows while preserving history.
- **Real-Time Streaming** â€” SSE-backed token deltas for both stand-alone chat and the Flow Canvas, mirroring the CLI's responsiveness.
- **Peer-to-Peer "Talk"** â€” Cross-context communication enabled via User-set references. Reaper Drones can now "see" and build upon the conversation history of linked peers across different models.
- **Core Stability Fixes** â€” Resolved parallel execution data races, hardened human approval gates, implemented optimistic concurrency locks for flow runs, and added a parent-PID watchdog to prevent orphaned service processes.
- **Annotator Reliability** â€” Fixed a bug where startup loads would "mass-mint" duplicate FPV Drones for every existing annotation. Corrected annotation indexing and implemented self-healing action log deduplication to prevent thread overflow.
- **Blueprints Fix** â€” Resolved a `TypeError` in the "+ FPV Drone" and "+ Reaper Drone" creation dialogs.
- **UX Polish** â€” Relaxed horizontal window width constraints, fixed node header rendering, and added a 10-year-old level User Manual linked in the sidebar.

## Phase 6 - FPV Drone / Reaper Drone model (2026-05)

The old Drone / Agent split has been replaced by the **FPV Drone** / **Reaper Drone** / **Staging Area** model.
See `docs/DRONE_MODEL.md` for the full design.

- **FPV Drone Blueprints** (operator-set frozen templates) and **FPV Drone
  Actions** (deployed instances with their own transcripts).  Each
  action snapshots its blueprint at deploy time so later blueprint
  edits never reach in-flight conversations.
- **Authority matrix** â€” every action carries a snapshotted role
  (`worker` / `supervisor` / `courier` / `auditor`) that gates
  cross-action mutations (`drones.append_reference`,
  `drones.append_skill`).  Auditors are read-only by construction.
- **New tabs** â€” Blueprints (template editor) + Drones (deploy +
  chat surface).
- **Canvas palette** â€” "Conversations" section renamed to "Drones".
  `+ New conversation` â†’ `Deploy` button picks a blueprint + workspace
  + first message in one shot.  Drag the resulting drone onto the
  canvas; double-click to open a small chat dialog.
- **Legacy agent removal** â€” `Agent` class, `agents.*` RPCs, `chat.send`,
  `attachments.*`, `Attachment` class, `FOLLOWUP_PRESETS`, the Agents
  tab, the Chat tab, the canvas Conversations palette, lineage boxes,
  and lineage edges are gone.  The `agents` and `attachments` tables
  are dropped on next service startup (operator-approved).
- **Deferred** â€” drones.send currently doesn't support attachments
  or cross-action references inlined into the prompt; both will
  return in a follow-up PR.  Lineage visualisation on the canvas
  needs re-design for the drone reference model (lists, not
  parent_ids) and is also deferred.

## Phase 5 â€” PR #12 (merged 2026-05-10)

Operator-facing additions in the big phase-5 super-PR:

- **Image + Excel attachments end-to-end.**  `.png/.jpg/.gif/.webp`
  pass through to the CLI as `@<path>` references; `.xlsx/.xls/.csv`
  render to one fenced markdown table per sheet (200 rows Ã— 30 cols
  cap) and inline into the prompt.  25 MB upload cap, sanitized
  filenames, cross-agent auth on every RPC.
- **Repo-aware coding sessions.**  Bind an Agent to a Workspace; the
  CLI subprocess runs with `cwd=<repo_path>` so its built-in Read /
  Bash / Edit / Grep tools operate against the project.
  ``workspaces.clone`` lets the operator clone a remote URL into
  ``<data_dir>/clones/`` in one click.  Live ``workspaces.git_status``
  banner + Switch-branch button on the canvas chat dialog.  The
  system prompt auto-inlines the first project-convention file found
  at the repo root (CLAUDE.md / AGENTS.md / GEMINI.md / .cursorrules)
  capped at 8 KB.
- **Conversations on the canvas.**  ConversationNodes wrap an Agent;
  drag from the new Conversations palette section, double-click to
  open a chat dialog scoped to that one agent.  Directional, labelled
  lineage edges between parent and follow-up.  LineageBox cluster
  wraps drawn around related conversations.  Visibility toggle dims
  unrelated nodes when a conversation is selected.
- **Cross-chat references.**  Each Agent has
  ``reference_agent_ids``; referenced transcripts (and their
  spreadsheet attachments) are inlined as a context preamble on every
  send.  Cross-provider safe.  100 KB total cap.
- **Limits tab.**  Cards per provider (Claude Code / Gemini CLI) with
  plan picker + per-model caps + local message tally + attachment-
  storage breakdown.  Manual refresh, 5-minute cooldown.
- **Operator panel.**  â˜… pinned utilities (start / restart / ops /
  limits) plus 1-8 numbered setup flow.  ``start.cmd`` pre-flight
  verifies both CLIs before launching; ``restart.cmd`` does a three-
  pass kill (window-title + port-listening) so supervisor-spawned
  services are reaped.
- **Audit hardening.**  Three batches landed on top of the feature
  work: HIGH security (DockerSandbox shell-injection, attachment-
  upload size cap, CLI path-injection guards, Pillow decompression
  bomb, xlsx zip-bomb walk fix, cross-agent auth, agent_dir boundary
  on unlink, dictation path traversal, workspaces.clone URL guard,
  switch_branch dash guard, EventStore connection-sharing, agents.send
  lost-update, FlowExecutor cancel leak, RpcClient.aclose at quit);
  MEDIUM (asyncio.to_thread on uploads, _render_references cap,
  attachment-only sends, chip scroll, drag-drop, Ctrl+Shift+A,
  flows.delete cancels active runs, optimistic flows.update); LOW
  polish.
- **Comprehensive README rewrite** + retrospective ROADMAP.md.

## Unreleased â€” Phase 5 (PR #13)

Continued phase-5 work on top of PR #12:

- ``apps/gui/presets`` â€” new shared registry for model + thinking-depth
  presets and the canonical ``compose_system(...)`` assembler.  Single
  source of truth across the Chat tab, the Canvas "+ New conversation"
  dialog, and the Agents-tab "+ New agent" dialog.  Public API:
  ``MODEL_PRESETS`` (12 rows Ã— 4 modes), ``THINKING_PRESETS`` (Off /
  Normal / Hard / Very hard), ``compose_system``, ``model_label_for``.
  Both registries are exported as tuples so a buggy consumer can't
  corrupt them.
- Chat tab refactored to consume the shared module â€” drops its local
  ``_MODEL_PRESETS`` / ``_THINKING_PRESETS`` / ``_label_for`` / ``_skills_to_system``
  definitions.  Behaviour-preserving.
- Canvas "+ New conversation" dialog redesigned: provider filter,
  full 12-row model + mode picker, thinking-depth dropdown, skills
  field â€” same picker as the Chat tab.  ``compose_system`` produces
  identical system prompts for identical inputs across screens.
- Draft-canvas amber banner: "ðŸ“ Draft canvas â€” planning surface.  Run
  is disabled. Model / thinking / skills / repo binding all behave
  the same as the Chat tab; flip Draft off to dispatch."
- Agents-tab "+ New agent" dialog: now slices ``MODEL_PRESETS`` for
  Coding-mode rows.  Asserts non-empty at import and guards
  ``currentIndex() == -1`` to refuse rather than IndexError.
- AgentChatDialog header + ConversationNode subtitle/tooltip: use
  ``model_label_for`` so the canvas shows the friendly label
  ("Claude Sonnet 4.6") instead of the raw provider id.
- Mid-thread thinking / skills changes now show a small amber hint â€”
  "â†³ Thinking / skills changes apply to the next New chat" â€” because
  the system prompt is locked at agent creation.
- **QA round 3** (this commit batch): 7-agent audit pass plus follow-
  up fixes covering UX consistency (button styles, error toasts,
  loading states), security (token timing, attachment filename
  prompt-injection, tar-slip on backup restore, WAL/SHM cleanup,
  workspaces.clone path traversal, derived default branch, agent
  reference_agent_ids scrub on delete), GUI lifetime (deleted-parent
  guards on long-running clone callbacks, hidden-label layout space,
  visibility highlight cleared on node delete, signal connect
  simplification), docstring drift (dispatcher, types.Workspace /
  Flow / FlowRun / SandboxTier), README factual fixes (Home tab
  description, OTel removal, 8-not-9 tabs, cards/ description,
  manifest.json claim), CHANGELOG completeness, plus new
  `docs/BACKUP.md`, `docs/ROADMAP.md`, and a stub `LICENSE` file.
- New regression tests for attachments.upload, cross-agent attachment
  auth, FlowVersionConflict on flows.update, flows.delete cancelling
  in-flight runs.

## Unreleased â€” Phase 4

Sprint 1 â€” multi-vendor agentic parity + sandbox tier.
- Gemini ``run_with_tools`` via google-genai's function-calling
  (generate_content + Tool[function_declarations]); per-turn
  function_call execution + function_response feed-back.  JSON-Schema
  fields Gemini can't accept (default, additionalProperties) are
  recursively stripped from the input schema.
- Ollama ``run_with_tools`` via the OpenAI-compatible
  /v1/chat/completions tool path.  Tolerant of malformed args (local
  models often produce noisy JSON) â€” coerces to {} rather than
  aborting.
- Minimal MCP stdio client (``apps/service/mcp/client.py``):
  initialize handshake, tools/list, tools/call.  Tolerant of
  malformed lines so a misbehaving server doesn't kill an agent run.
- ``WorktreeToolset`` gains ``mcp_tools`` + ``MCPRunTimeTool``
  adapter.  Built-ins take priority; MCP tools land with public name
  ``mcp:<server>:<tool>`` so they cannot shadow built-ins.
- RunDispatcher.``_open_mcp_tools`` resolves card.tool_allowlist
  against the registry; only TRUSTED stdio servers are opened, the
  rest are skipped with a logged warning.  Clients are torn down in
  the same finally block as the sandbox.
- ``apps/service/sandbox/e2b.py``: E2B Firecracker microVM tier with
  read/write/list parity to LocalSandbox.  Lazy SDK import; raises
  SandboxError when the package or E2B_API_KEY is missing so the
  dispatcher falls back to LocalSandbox with a warning event.

Sprint 2 â€” speculative parallelism + hot model swap.
- ``apps/service/dispatch/speculative.py``: ``race(user_message,
  candidates)`` runs N (provider, model) chats in parallel; first
  acceptable response cancels the others.  Cancelled tasks still
  record duration + cost.  Building block for a runs.speculative
  RPC + a Speculative card archetype.
- ``apps/service/dispatch/hot_swap.py``: pure decision module that
  decides when to swap to a larger-context fallback model mid-run.
  ``plan_swap(card, tokens_used)`` returns a HotSwapPlan with the
  routing change and a human-readable reason.  Pinned context caps
  per (provider, model) tunable in one place.

Sprint 3 â€” backup/restore + distributed bus + A2A schema + update client.
- ``apps/service/store/backup.py``: ``.aobackup`` tar.gz format with
  JSON manifest + sqlite3 online backup API; refuses to restore
  forward-incompatible schemas; pre-restore copy of the current DB
  lands at ``target.sqlite.pre-restore`` so a botched restore is
  recoverable.
- ``apps/service/dispatch/a2a.py``: Pydantic models for the A2A
  protocol â€” PeerCapabilities, RunDelegation + Ack,
  HandoffCardEnvelope, A2AEvent.  Wire format only; runtime path is
  V5.
- ``apps/service/dispatch/nats_bridge.py``: optional bridge that
  republishes EventBus events on a NATS subject namespace
  (``agentorchestra.<workspace>.<run>.<kind>``) and relays peer
  events back tagged with ``peer_origin``.  Lazy nats-py; no-op when
  the package isn't installed.
- ``apps/service/updates/client.py``: signed-manifest discovery +
  download.  Verifies the signature, picks the platform-appropriate
  asset, hashes the download, raises if the sha256 doesn't match.
  Install is always user-initiated.

Sprint 4 â€” UI polish.
- ``apps/gui/widgets/diff_view.py``: QSyntaxHighlighter-based diff
  viewer paints + green, - red, hunk headers purple, file headers
  muted gray.  Review page uses a QStackedWidget to show DiffView
  for runs with a DIFF artifact and the plain-text body for
  chat-only runs.

CI: provider field on PersonalityCard relaxed from
Literal[\"anthropic\",...] to plain str so test fakes (failing,
secondary, echo, vendorA, ...) no longer fail Pydantic validation.

## Unreleased â€” Phase 3

Sprint 1 â€” multi-vendor parity + sandbox + Mergiraf wire-up.
- Gemini agentic via google-genai function-calling; per-turn function
  call execution + function_response feed-back; JSON-schema field
  stripping for Gemini-incompatible keys.
- Ollama agentic via OpenAI-compatible function-calling; defensive
  arg-coercion for noisy local models.
- New apps/service/sandbox/ package: Sandbox protocol, LocalSandbox,
  DockerSandbox (cap-drop ALL, no-network, --read-only, worktree
  bind-mount).  WorktreeToolset routes I/O through the sandbox when
  set; falls back to direct FS otherwise.  RunDispatcher honors
  card.sandbox_tier and emits a fallback event when Docker isn't
  available.
- merger.install_as_merge_driver registers Mergiraf via per-repo
  `git config merge.mergiraf.driver` + a tagged .gitattributes block
  (~20 tree-sitter-supported globs).  Idempotent.  Wired into
  WorktreeManager assisted-merge mode.

Sprint 2 â€” innovation + onboarding.
- MCP server registry with trust-on-first-use, SHA-256 fingerprint of
  command + args + url, and trust transitions UNTRUSTED -> TRUSTED ->
  BLOCKED.  RPCs: mcp.list / add / trust / block / remove.
- First-run wizard (4 pages): welcome, provider keys, default
  workspace, defaults (sandbox tier + daily budget + Claude hook
  install).  Sentinel file at ~/.local/share/agentorchestra/
  first_run.done so it shows once.
- Local-only voice dictation via lazy faster-whisper wrapper.
  Composer's "ðŸŽ™ Dictate" button picks an audio file, the service
  transcribes via dictation.transcribe, the result lands in the first
  text input.
- Drift Sentinel: a single asyncio task subscribed to the EventBus
  flagging runs with N tool calls and zero commits, or N consecutive
  tool errors.  Started with the service.

Sprint 3 â€” distribution.
- Briefcase config (briefcase.toml) for macOS / Windows / Linux
  installers; mac entitlements for Hardened Runtime; stub for
  signing (the certs aren't shipped in this branch).
- Signed update manifest format + verifier
  (apps/service/updates/manifest.py): ed25519 over canonical JSON
  via the `cryptography` package.  Manifests without signatures are
  rejected; tampered payloads fail verification.

Tests: test_sandbox, test_merger_install, test_mcp_registry,
test_drift_sentinel, test_update_manifest.

CI: ruff check + ruff format --check green tree-wide.

## Phase 2

Sprint 1 â€” multi-vendor.
- Gemini chat adapter via the official google-genai SDK.
- Ollama chat adapter via the OpenAI-compatible /v1/chat/completions
  endpoint at http://localhost:11434.
- Provider registry now wires anthropic + google + ollama by default;
  agentic Gemini / Ollama runs surface a clear deferred-feature error.
- PersonalityCard gains `fallbacks`, an ordered list of {provider,
  model} dicts.  RunDispatcher tries the primary on open; on failure
  it walks the fallbacks before declaring the run aborted.

Sprint 2 â€” innovations.
- runs.replay re-runs a past Run with optional provider / model /
  instruction overrides.  Overrides clone the card so the original's
  accounting stays intact.  History page in the GUI grew a Recent
  runs tab with a Replayâ€¦ dialog.
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

Sprint 3 â€” specialised archetypes.
- Red Team adversarial reviewer card targeting another run's diff.
- Tracker watcher card emitting structured HandoffCards.
- Cross-vendor Consensus card + a fan-out + judge orchestrator
  (`apps/service/dispatch/consensus.py`).  RPC: runs.consensus.

Sprint 4 â€” UI polish + safety.
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

## Phase 1 â€” MVP scaffold

This commit lays down the working scaffold for the multi-vendor desktop
agent orchestrator.  Execution-ready end-to-end run dispatch is the
next milestone (Phase 1 weeks 4â€“6); this commit covers everything
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

### Deferred (Phase 1 weeks 4â€“6 and beyond)

- Real Run dispatch (the agent loop in a worktree)
- Mergiraf binary integration and the assisted-merge UX
- Gemini and Ollama adapter implementations
- Hook pack installer
- Briefcase signed installers
- Visual branch/worktree map and live agent pane
- Replay & fork
