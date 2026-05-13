# AgentOrchestra â€” UI Gap Analysis

Read-only audit performed 2026-05-10 against `main` (post PRs #12â€“#15).
Compares the operator-facing requirement set (README, ROADMAP, FLOW_CANVAS_PLAN,
CHANGELOG, plus the implicit session-derived requirements) to the shipped GUI
under `apps/gui/`.

---

## 1. Executive summary

Posture: **close to "all requirements met" on the shipped GUI surfaces.** The
old Settings/API-key and Reaper Drones second-class issues are resolved, and the
shared `presets.py` registry has eliminated the worst model-picker drift. The
remaining gaps are now mostly convenience and surface-completeness items rather
than core workflow blockers.

Update 2026-05: the separate graph-template builder now exists in the
`Templates` tab. When this document says `templates` in the old service
context, it still means instruction templates unless the graph-template
builder is explicitly named.

The three highest-impact gaps:

1. **Settings still lacks a GUI editor for service-level controls.** The
   README now correctly says `Service URL` / `Token` are CLI-level options and
   the MCP registry is service-side, but that still leaves no in-app editor for
   either surface.
2. **The Drones tab and canvas mini-dialog are not feature-parity peers.**
   Workspace binding and references live in the Drones tab, while the canvas
   chat dialog stays message-only and does not surface branch switching, live
   git status, attachments, or per-turn references.
3. **Composer dictation is file-based, not live recording.** It transcribes an
   existing audio file through `faster-whisper`; there is still no in-app
   recorder.

---

## 2. Per-tab review

### Home â€” `apps/gui/windows/home.py`
- **Today:** Workspace map (right) + Active runs + Recent runs tables; single
  Refresh button; first-run wizard auto-fires once.
- **Gaps:**
  - LOW â€” the Active table's "Reaper Drone" column shows the run id, not an
    operator-friendly card / Reaper Drone name (`home.py:96`); operators read the wrong
    column to find a chat in flight.
  - LOW â€” no auto-refresh; the operator must click Refresh after dispatching
    from Compose to see the row appear in Active.

### Legacy chat surface
- **Today:** The old `apps/gui/windows/chat.py` surface is no longer present in
  the current GUI tree. Its former attachment and picker gaps are now folded
  into the Drones and Canvas notes above.
- **Gaps:** None distinct from the Drones / Canvas rows above.

### Reaper Drones â€” `apps/gui/windows/drones.py`
- **Today:** Sidebar list, transcript view, Send box, workspace banner,
  references editor, and a `+ New` / edit dialog for deployed drone actions.
- **Gaps:**
  - **HIGH** â€” no attachment upload flow.
  - **HIGH** â€” no live git-status banner or `Switch branch` control.
  - MEDIUM â€” the `+ New` dialog is still coding-oriented rather than a full
    general-mode conversation builder.

### Compose â€” `apps/gui/windows/composer.py`
- **Today:** Cards list, workspace picker, form-driven variables, Preview
  (renders + lints + cost forecast), Dispatch, Dictate.
- **Gaps:**
  - **MEDIUM** â€” voice-dictate dialog asks for a pre-recorded audio file
    (`composer.py:213-221`); no in-app recording. The README's Compose section
    promises "voice dictation button" without that caveat.
  - LOW â€” no attachment upload control on Compose.
  - LOW â€” no draft-save (you have to hit Preview before Dispatch becomes
    active, but losing focus to another tab discards typed variables).

### Canvas â€” `apps/gui/canvas/page.py` + `palette.py` + `drone_chat_dialog.py`
- **Today:** Drag from palette (control / Reaper Drone cards / conversations);
  new conversation dialog mirrors the shared model/workspace picker incl.
  workspace + Addâ€¦ / Cloneâ€¦; lineage edges + LineageBox cluster; Visibility
  toggle; Draft-mode banner; auto-layout; minimap; undo; per-Reaper Drone chat
  dialog with transcript + send box + context gauge.
- **Gaps:**
  - **MEDIUM** â€” `_open_flow` uses an `QInputDialog.comboBoxItems()`
    round-trip (`page.py:800-812`) which silently falls back to index 0 on any
    unexpected dialog text â€” operators selecting flow #2 in a long list
    sometimes load flow #1.
  - LOW â€” Visibility highlight only fires for `ConversationNode` selection
    (`page.py:386-394`); the toolbar button doesn't tell the operator that
    selecting an Reaper Drone node / control node does nothing.
  - LOW â€” `_load_flow` requires the palette's cards / agents lists to have
    populated before it can resolve `card_id` / `agent_id` (`page.py:828-867`).
    Opening the canvas, clicking Open immediately, can yield "Missing card" /
    "Missing agent" stub nodes.
  - LOW â€” no minimap toggle / hide; can't be turned off when the operator
    wants the centre area for a wide flow.
  - LOW â€” the mini-dialog is intentionally message-only, so attachments,
    references, and branch controls are absent there by design.

### History â€” `apps/gui/windows/history.py`
- **Today:** Search tab (FTS5) + Recent runs tab with per-row Approve / Reject
  / Cancel and Replayâ€¦. Replay dialog is subscription-only by default.
- **Gaps:**
  - LOW â€” no filtering by state / workspace / card on the Recent runs tab â€”
    just a flat list. Painful past ~50 runs.
  - LOW â€” search results have no click-through to open the originating Run / Reaper Drone
    Agent (`history.py:113-116`). Read-only listing only.

### Limits â€” `apps/gui/windows/limits.py`
- **Today:** Per-provider cards w/ plan picker, message caps + local tally,
  context-windows summary, attachment-storage card. 5-min cooldown gate.
- **Gaps:**
  - LOW â€” no per-Reaper Drone context-window-used ribbon promised in `ROADMAP.md`
    (Now, line 222â€“226). That's a future roadmap item, so flag for context.
  - LOW â€” Refresh cooldown is silent until clicked (`limits.py:108-121`);
    a small countdown on the disabled button would be friendlier.

### Settings â€” `apps/gui/windows/settings.py`
- **Today:** Hooks installer, workspaces list, and a collapsed API-fallback
  disclosure for Anthropic / Google / OpenAI keys.
- **Gaps:**
  - **MEDIUM** â€” no GUI editor for the service URL / RPC token override.
  - **MEDIUM** â€” no GUI editor for the MCP server registry.

### Live â€” `apps/gui/windows/live.py` (reachable from Compose dispatch)
- **Today:** SSE-driven transcript + event log + Cancel + Open Review.
- **Gaps:**
  - LOW â€” event-log items are flattened to `[{kind}] {payload}` (`live.py:115`)
    so a `tool_call` payload renders as a Python dict repr. Hard to scan.
  - LOW â€” no per-event timestamp / elapsed delta.

### Review â€” `apps/gui/windows/review.py` (reachable from Live)
- **Today:** Plain text or DiffView (auto-routed by artifact kind), Approve /
  Reject + note.
- **Gaps:**
  - LOW â€” README Â§Safety mentions "5-second hold before activating" the
    Approve button on high-blast-radius cards; not implemented (`review.py:65-78`
    just shows a normal button).
  - LOW â€” no link back to the spawning Live page once the Run is approved.

---

## 3. Cross-cutting requirements

- **Subscription-only flow.** â˜… Mostly intact: Drones / canvas route through
  `claude-cli` / `gemini-cli`; the Replay dropdown deliberately omits API
  providers (`history.py:213`); Settings keeps API keys collapsed by default.
  No documentation mismatch remains here.
- **Attachment upload flow.** â˜… Backend storage and Limits usage are present.
  **Gap:** the current GUI does not expose a dedicated upload surface on the
  Drones tab, canvas mini-dialog, or Composer. **MEDIUM.**
- **Drag from Conversations palette to canvas.** â˜… Wired
  (`palette.py:112-114` + `_DragList.startDrag` + `page.py:288-307`); duplicate-
  drop guard recentres the existing node. No gap.
- **Visibility toggle dimming non-cluster nodes.** â˜… Wired
  (`page.py:370-455`) â€” both ancestors + descendants on selection. **Gap:** no
  visual hint that Reaper Drone node / control node selection does nothing. **LOW.**
- **Draft canvas mirroring the shared picker.** â˜… Mirrored: same 12-row
  picker, same thinking-depth, same skills, same workspace combo with Addâ€¦ /
  Cloneâ€¦ (`palette.py:127-322`); amber Draft banner on toolbar
  (`page.py:127-139`); `compose_system` shared. No gap.
- **Repo-aware agents end-to-end.** â˜… Workspace binding, green workspace
  banner, and `CLAUDE.md` / `AGENTS.md` inlining are wired. **Gap:** branch
  switching, live git-status surfacing, and attachment upload are still absent
  from the GUI surfaces. **MEDIUM.**
- **Operator Panel one-click flow on Windows.** â˜… `scripts/ops.py` reads
  `manifest.json`; star-badged â˜… pinned utilities (start / restart / ops /
  limits) plus 1-8 numbered steps; QProcess-based with merged channels. No gap.

---

## 4. Forward priorities

### Now (block operator workflow / break trust)
- **Settings: decide whether service-level controls stay CLI-only.**
  The app still has no GUI for `Service URL`, RPC token override, or MCP
  registry management. Severity MEDIUM.
- **Drones tab / canvas mini-dialog: decide on parity or keep the split.**
  The Drones tab owns workspace and references; the canvas dialog is still
  intentionally minimal. Severity MEDIUM.
- **Composer: in-app recording.** The current file-picker flow is usable but
  less ergonomic than the README's "voice dictation" wording suggests.

### Next (annoying or partial implementations)
- **Canvas Open dialog: replace `QInputDialog.comboBoxItems()` index lookup**
  with a real `QListWidget` so flow #N actually loads. `page.py:800-812`.
- **Settings: surface Service URL / Token and MCP registry if GUI parity is
  desired.** RPCs already exist, but the current page keeps them out of the UI.
- **Compose: in-app voice recording** instead of file picker. `composer.py:213-221`.
- **Drones tab + New dialog: optional thinking + skills + mode** so a General-
  mode Reaper Drone can be created without going to the canvas.
- **Live page: structured event-log rendering** â€” kind + delta + ts + cost.
  `apps/gui/windows/live.py:115`.

### Later (polish / nice-to-have)
- **Per-agent context-window ribbon** on chat dialog headers (already on
  ROADMAP "Now" but not yet shipped).
- **Visibility toggle: Reaper Drone hint** â€” disable the button or show a "select
  a conversation" status when no ConversationNode is selected. `page.py:386`.
- **Home tab auto-refresh** after dispatch + agent column showing card name
  rather than run id. `home.py:96`.
- **History filtering / state-faceted search** + click-through to Run /
  Reaper Drone. `history.py:113-242`.
- **Review high-blast-radius hold-to-approve** the README Â§Safety advertises.
  `review.py:65-78`.
- **Limits cooldown countdown label** on the disabled Refresh button.
  `limits.py:108-121`.
- **Canvas minimap show/hide toggle** for wide flows. `canvas/page.py:146-148`.
