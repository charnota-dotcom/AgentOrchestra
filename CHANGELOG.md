# Changelog

## Unreleased — Phase 5

Next batch.  Tracks PR #13 — work that will land on top of merged
PR #12.

- ``apps/gui/presets`` — new shared registry for model + thinking-depth
  presets and the canonical ``compose_system(...)`` assembler.  Single
  source of truth across the Chat tab, the Canvas "+ New conversation"
  dialog, and the Agents-tab "+ New agent" dialog.  Public API:
  ``MODEL_PRESETS`` (12 rows × 4 modes), ``THINKING_PRESETS`` (Off /
  Normal / Hard / Very hard), ``compose_system``, ``model_label_for``.
  Both registries are exported as tuples so a buggy consumer can't
  corrupt them.
- Chat tab refactored to consume the shared module — drops its local
  ``_MODEL_PRESETS`` / ``_THINKING_PRESETS`` / ``_label_for`` / ``_skills_to_system``
  definitions.  Behaviour-preserving.
- Canvas "+ New conversation" dialog redesigned: provider filter,
  full 12-row model + mode picker, thinking-depth dropdown, skills
  field — same picker as the Chat tab.  ``compose_system`` produces
  identical system prompts for identical inputs across screens.
- Draft-canvas amber banner: "📐 Draft canvas — planning surface.  Run
  is disabled. Model / thinking / skills / repo binding all behave
  the same as the Chat tab; flip Draft off to dispatch."
- Agents-tab "+ New agent" dialog: now slices ``MODEL_PRESETS`` for
  Coding-mode rows.  Asserts non-empty at import and guards
  ``currentIndex() == -1`` to refuse rather than IndexError.
- AgentChatDialog header + ConversationNode subtitle/tooltip: use
  ``model_label_for`` so the canvas shows the friendly label
  ("Claude Sonnet 4.6") instead of the raw provider id.
- Mid-thread thinking / skills changes now show a small amber hint —
  "↳ Thinking / skills changes apply to the next New chat" — because
  the system prompt is locked at agent creation.

## Unreleased — Phase 4

Sprint 1 — multi-vendor agentic parity + sandbox tier.
- Gemini ``run_with_tools`` via google-genai's function-calling
  (generate_content + Tool[function_declarations]); per-turn
  function_call execution + function_response feed-back.  JSON-Schema
  fields Gemini can't accept (default, additionalProperties) are
  recursively stripped from the input schema.
- Ollama ``run_with_tools`` via the OpenAI-compatible
  /v1/chat/completions tool path.  Tolerant of malformed args (local
  models often produce noisy JSON) — coerces to {} rather than
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

Sprint 2 — speculative parallelism + hot model swap.
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

Sprint 3 — backup/restore + distributed bus + A2A schema + update client.
- ``apps/service/store/backup.py``: ``.aobackup`` tar.gz format with
  JSON manifest + sqlite3 online backup API; refuses to restore
  forward-incompatible schemas; pre-restore copy of the current DB
  lands at ``target.sqlite.pre-restore`` so a botched restore is
  recoverable.
- ``apps/service/dispatch/a2a.py``: Pydantic models for the A2A
  protocol — PeerCapabilities, RunDelegation + Ack,
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

Sprint 4 — UI polish.
- ``apps/gui/widgets/diff_view.py``: QSyntaxHighlighter-based diff
  viewer paints + green, - red, hunk headers purple, file headers
  muted gray.  Review page uses a QStackedWidget to show DiffView
  for runs with a DIFF artifact and the plain-text body for
  chat-only runs.

CI: provider field on PersonalityCard relaxed from
Literal[\"anthropic\",...] to plain str so test fakes (failing,
secondary, echo, vendorA, ...) no longer fail Pydantic validation.

## Unreleased — Phase 3

Sprint 1 — multi-vendor parity + sandbox + Mergiraf wire-up.
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

Sprint 2 — innovation + onboarding.
- MCP server registry with trust-on-first-use, SHA-256 fingerprint of
  command + args + url, and trust transitions UNTRUSTED -> TRUSTED ->
  BLOCKED.  RPCs: mcp.list / add / trust / block / remove.
- First-run wizard (4 pages): welcome, provider keys, default
  workspace, defaults (sandbox tier + daily budget + Claude hook
  install).  Sentinel file at ~/.local/share/agentorchestra/
  first_run.done so it shows once.
- Local-only voice dictation via lazy faster-whisper wrapper.
  Composer's "🎙 Dictate" button picks an audio file, the service
  transcribes via dictation.transcribe, the result lands in the first
  text input.
- Drift Sentinel: a single asyncio task subscribed to the EventBus
  flagging runs with N tool calls and zero commits, or N consecutive
  tool errors.  Started with the service.

Sprint 3 — distribution.
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
