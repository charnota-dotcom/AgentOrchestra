# AgentOrchestra — UI Gap Analysis

Read-only audit performed 2026-05-10 against `main` (post PRs #12–#15).
Compares the operator-facing requirement set (README, ROADMAP, FLOW_CANVAS_PLAN,
CHANGELOG, plus the implicit session-derived requirements) to the shipped GUI
under `apps/gui/`.

---

## 1. Executive summary

Posture: **close to "all requirements met" — Phase 5 is functionally complete on
the Chat / Canvas / Limits / Operator-Panel surfaces, the implicit "subscription-only,
conversations-as-nodes, repo-aware coding" thesis is intact, and the shared
`presets.py` registry has eliminated the worst Chat ↔ Canvas drift.** What
remains are *consistency* gaps — features that work on one surface but were not
mirrored to the other — plus a small handful of polish / hardening items.

The three highest-impact gaps:

1. **The Agents tab is a second-class citizen.** It cannot bind a workspace,
   edit references, attach files, switch branch, or surface the git-status
   banner — none of which the canvas chat dialog or the Chat tab is missing.
   `apps/gui/windows/agents.py:53-457`.
2. **Settings still presents Anthropic / Google / OpenAI API-key fields** as a
   prominent block above hooks/workspaces, contradicting the
   "subscription-only by default" thesis the README opens with.
   `apps/gui/windows/settings.py:39-86`.
3. **The Chat tab's paperclip path has no client-side 25 MB pre-check.** The
   canvas chat dialog enforces it; the Chat tab uploads first and lets the
   server reject — wasting seconds on a multi-MB read+b64 round-trip.
   `apps/gui/windows/chat.py:456-495` vs `apps/gui/canvas/chat_dialog.py:380-395`.

---

## 2. Per-tab review

### Home — `apps/gui/windows/home.py`
- **Today:** Workspace map (right) + Active runs + Recent runs tables; single
  Refresh button; first-run wizard auto-fires once.
- **Gaps:**
  - LOW — the Active table's "Agent" column shows the run id, not an
    operator-friendly card / agent name (`home.py:96`); operators read the wrong
    column to find a chat in flight.
  - LOW — no auto-refresh; the operator must click Refresh after dispatching
    from Compose to see the row appear in Active.

### Chat — `apps/gui/windows/chat.py`
- **Today:** Model picker (12 rows) + Thinking + Skills + Repo combo + paperclip
  + drag-drop + Save-last-reply + New-chat. Repo binding via `Add repo…` and
  `Clone from git…` is fully wired. Auto-mints an Agent on first send.
- **Gaps:**
  - **MEDIUM** — no client-side 25 MB pre-check before
    `Path.read_bytes()` + `base64.b64encode` (`chat.py:469-478`); the canvas
    dialog has it (`chat_dialog.py:389`). On a 50 MB drag-drop the UI does
    seconds of pointless work before showing the server's "too large" warning.
  - **LOW** — `_render_for_send` (`chat.py:803-817`) is dead code: the actual
    send only passes the latest user message because the backend keeps the
    transcript. The dead path is misleading for future maintainers.
  - **LOW** — `_pending_attachments` carries a `bytes` field set from `stat()`,
    but no per-image dimension preview / total-size bar.

### Agents — `apps/gui/windows/agents.py`
- **Today:** Sidebar list, transcript view, Send box, Spawn-follow-up panel,
  + New agent dialog (Coding mode only, 5 rows).
- **Gaps:**
  - **HIGH** — no workspace binding control. `agents.set_workspace` exists in
    the RPC; the Agents tab never calls it. Operators who land here and want to
    bind their existing chat to a repo have to drop it on the canvas and
    double-click. (`agents.py:295-334`).
  - **HIGH** — no References editor. `agents.set_references` only reachable
    via the canvas chat dialog (`chat_dialog.py:254-329`).
  - **HIGH** — no attachment paperclip / drag-drop. Both Chat and the canvas
    chat dialog support attachments; this tab does not.
  - **HIGH** — no live git-status banner / Switch-branch button on the
    transcript pane. Same omission pattern.
  - MEDIUM — the `+ New` dialog has no thinking / skills / mode picker
    (`agents.py:295-326` deliberately filters to MODE_CODING). The README
    states this is intentional, so call it MEDIUM not HIGH — but it means a
    chat-style General-mode agent literally cannot be created from this tab.

### Compose — `apps/gui/windows/composer.py`
- **Today:** Cards list, workspace picker, form-driven variables, Preview
  (renders + lints + cost forecast), Dispatch, Dictate.
- **Gaps:**
  - **MEDIUM** — voice-dictate dialog asks for a pre-recorded audio file
    (`composer.py:213-221`); no in-app recording. The README's Compose section
    promises "voice dictation button" without that caveat.
  - LOW — no attachment paperclip on Compose; agentic Runs cannot ingest
    images/spreadsheets via this surface (only the Chat / Canvas paths).
  - LOW — no draft-save (you have to hit Preview before Dispatch becomes
    active, but losing focus to another tab discards typed variables).

### Canvas — `apps/gui/canvas/page.py` + `palette.py` + `chat_dialog.py`
- **Today:** Drag from palette (control / agent cards / conversations);
  + New conversation dialog mirrors Chat-tab picker incl. workspace +
  Add… / Clone…; lineage edges + LineageBox cluster; Visibility toggle;
  Draft-mode banner; auto-layout; minimap; undo; per-agent chat dialog with
  workspace banner + git status + Switch branch + References + attachments.
- **Gaps:**
  - **MEDIUM** — `_open_flow` uses an `QInputDialog.comboBoxItems()`
    round-trip (`page.py:800-812`) which silently falls back to index 0 on any
    unexpected dialog text — operators selecting flow #2 in a long list
    sometimes load flow #1.
  - LOW — Visibility highlight only fires for `ConversationNode` selection
    (`page.py:386-394`); the toolbar button doesn't tell the operator that
    selecting an AgentNode / control node does nothing.
  - LOW — `_load_flow` requires the palette's cards / agents lists to have
    populated before it can resolve `card_id` / `agent_id` (`page.py:828-867`).
    Opening the canvas, clicking Open immediately, can yield "Missing card" /
    "Missing agent" stub nodes.
  - LOW — no minimap toggle / hide; can't be turned off when the operator
    wants the centre area for a wide flow.

### History — `apps/gui/windows/history.py`
- **Today:** Search tab (FTS5) + Recent runs tab with per-row Approve / Reject
  / Cancel and Replay…. Replay dialog is subscription-only by default.
- **Gaps:**
  - LOW — no filtering by state / workspace / card on the Recent runs tab —
    just a flat list. Painful past ~50 runs.
  - LOW — search results have no click-through to open the originating Run /
    Agent (`history.py:113-116`). Read-only listing only.

### Limits — `apps/gui/windows/limits.py`
- **Today:** Per-provider cards w/ plan picker, message caps + local tally,
  context-windows summary, attachment-storage card. 5-min cooldown gate.
- **Gaps:**
  - LOW — no per-agent context-window-used ribbon promised in `ROADMAP.md`
    (Now, line 222–226). That's a future roadmap item, so flag for context.
  - LOW — Refresh cooldown is silent until clicked (`limits.py:108-121`);
    a small countdown on the disabled button would be friendlier.

### Settings — `apps/gui/windows/settings.py`
- **Today:** API-key fields (Anthropic / Google / OpenAI), hook installer,
  workspaces list, Add workspace.
- **Gaps:**
  - **HIGH** — Provider keys block is the first card on the page, contradicting
    the "subscription-only by default" thesis the README opens with
    (`settings.py:39-86`). Operators following a fresh install screenshot the
    Settings page and ask "where do I get an Anthropic key?" — exactly the
    confusion the first-run wizard PR #11 went out of its way to remove.
  - **MEDIUM** — no MCP server registry UI despite README §Settings claiming
    one (README:198) and the RPCs (`mcp.list / add / trust / block / remove`)
    being shipped.
  - **MEDIUM** — no "Service URL / Token" field despite README §Settings
    listing them (README:196-197). Currently the only way to change the URL is
    via `--service-url` CLI flag.

### Live — `apps/gui/windows/live.py` (reachable from Compose dispatch)
- **Today:** SSE-driven transcript + event log + Cancel + Open Review.
- **Gaps:**
  - LOW — event-log items are flattened to `[{kind}] {payload}` (`live.py:115`)
    so a `tool_call` payload renders as a Python dict repr. Hard to scan.
  - LOW — no per-event timestamp / elapsed delta.

### Review — `apps/gui/windows/review.py` (reachable from Live)
- **Today:** Plain text or DiffView (auto-routed by artifact kind), Approve /
  Reject + note.
- **Gaps:**
  - LOW — README §Safety mentions "5-second hold before activating" the
    Approve button on high-blast-radius cards; not implemented (`review.py:65-78`
    just shows a normal button).
  - LOW — no link back to the spawning Live page once the Run is approved.

---

## 3. Cross-cutting requirements

- **Subscription-only flow.** ★ Mostly intact: chat / agents / canvas all route
  through `claude-cli` / `gemini-cli`; the Replay dropdown deliberately omits
  API providers (`history.py:213`); first-run wizard removed the API-key page.
  **Gap:** Settings still surfaces three API-key fields up front. **Severity HIGH** for
  consistency with README claim.
- **Drag-and-drop file attach.** ★ Wired on Chat tab and canvas chat dialog
  (`chat.py:387-404`, `chat_dialog.py:353-378`). **Gap:** Agents tab and
  Composer have no drop handler. **MEDIUM.**
- **Drag from Conversations palette to canvas.** ★ Wired
  (`palette.py:112-114` + `_DragList.startDrag` + `page.py:288-307`); duplicate-
  drop guard recentres the existing node. No gap.
- **Visibility toggle dimming non-cluster nodes.** ★ Wired
  (`page.py:370-455`) — both ancestors + descendants on selection. **Gap:** no
  visual hint that AgentNode / control node selection does nothing. **LOW.**
- **Draft canvas mirroring Chat UI.** ★ Mirrored: same 12-row picker, same
  thinking-depth, same skills, same workspace combo with Add… / Clone…
  (`palette.py:127-322`); amber Draft banner on toolbar (`page.py:127-139`);
  `compose_system` shared. No gap.
- **Repo-aware agents end-to-end.** ★ Clone (Chat + canvas), Switch branch
  (canvas chat dialog only), green workspace banner, live git-status banner,
  CLAUDE.md / AGENTS.md inlined system prompt — all wired on Chat + canvas
  surfaces. **Gap:** Agents tab cannot bind / unbind / switch branch / see
  status banner. **HIGH.**
- **Operator Panel one-click flow on Windows.** ★ `scripts/ops.py` reads
  `manifest.json`; star-badged ★ pinned utilities (start / restart / ops /
  limits) plus 1-8 numbered steps; QProcess-based with merged channels. No gap.

---

## 4. Forward priorities

### Now (block operator workflow / break trust)
- **Settings: hide / collapse provider-keys block** under an "Advanced — API
  fallback (optional)" disclosure. Severity HIGH.
  `apps/gui/windows/settings.py:39-86`.
- **Agents tab: add Workspace + References + Attachments + git banner**
  to mirror the canvas chat dialog. Severity HIGH; the simplest fix is to
  extract the canvas chat dialog's right-hand-side controls into a reusable
  widget and embed it. `apps/gui/windows/agents.py:123-171`.
- **Chat tab: 25 MB pre-check before read+b64.** Severity MEDIUM but cheap.
  `apps/gui/windows/chat.py:456-495`.

### Next (annoying or partial implementations)
- **Canvas Open dialog: replace `QInputDialog.comboBoxItems()` index lookup**
  with a real `QListWidget` so flow #N actually loads. `page.py:800-812`.
- **Settings: ship the MCP server registry UI** the README §Settings promises.
  RPCs already exist. `apps/gui/windows/settings.py:1-213`.
- **Settings: surface Service URL + Token override** — currently CLI-only.
- **Compose: in-app voice recording** instead of file picker. `composer.py:213-221`.
- **Agents tab + New dialog: optional thinking + skills + mode** so a General-
  mode agent can be created without going to the canvas.
- **Live page: structured event-log rendering** — kind + delta + ts + cost.
  `apps/gui/windows/live.py:115`.

### Later (polish / nice-to-have)
- **Per-agent context-window ribbon** on chat dialog headers (already on
  ROADMAP "Now" but not yet shipped).
- **Visibility toggle: AgentNode hint** — disable the button or show a "select
  a conversation" status when no ConversationNode is selected. `page.py:386`.
- **Home tab auto-refresh** after dispatch + agent column showing card name
  rather than run id. `home.py:96`.
- **History filtering / state-faceted search** + click-through to Run /
  Agent. `history.py:113-242`.
- **Review high-blast-radius hold-to-approve** the README §Safety advertises.
  `review.py:65-78`.
- **Limits cooldown countdown label** on the disabled Refresh button.
  `limits.py:108-121`.
- **Canvas minimap show/hide toggle** for wide flows. `canvas/page.py:146-148`.
