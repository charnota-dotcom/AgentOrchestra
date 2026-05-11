# Cross-vendor chat orchestration — feature plan

Status: design — implementation in flight across three sister PRs.

---

## Context

The drone abstraction today binds each conversation to one of three
providers — `claude-cli`, `gemini-cli`, `anthropic` (API). Each has a
downside for the operator:

* **CLI** is agentic; the model plans tool use instead of just chatting.
* **API** is clean chat but bills per-token outside the Max subscription.
* **No way** to take a conversation from one service to another, or to
  run several services side-by-side from one workspace.

The operator wants a third path — let the browser tab do the inference;
the app does everything around it — plus better awareness of conversation
length and a way to move conversations across services. This plan ships
those capabilities as three PRs.

---

## The three PRs at a glance

| # | Branch | Scope | LOC | Independent? |
|---|--------|-------|-----|--------------|
| 1 | `claude/token-tracking` | New `apps/service/tokens/` sub-package; token-estimate + context-window lookup; `prompt_tokens` / `transcript_tokens` / `context_window` on every `drones.send` response; `ContextGauge` widget rendered in Drones tab + canvas chat dialog. Lights up for every drone, every provider, no other UX change. | ~150 | yes |
| 2 | `claude/browser-provider` | New `apps/gui/browser_bridge/` sub-package; `Blueprint.chat_url` + `DroneAction.bound_chat_url`; `BrowserBridgeDialog` with copy + paste + clipboard listener; `CF_HTML` SourceURL extraction; multi-drone parallel routing; link-back to browser; handoff menu (continuation / fork / plain). | ~400 | uses PR 1's gauge in the dialog header; otherwise independent |
| 3 | `claude/claude-cli-stream-json` | Promote `apps/service/providers/claude_cli.py` to a sub-package; parse `--output-format stream-json` event stream; extend transcript-entry schema for `tool_call` / `tool_result` / `subagent` kinds; GUI renderers. | ~300 | yes |

Recommended order: 1 → 2 → 3.

---

## PR 1 — Token & context-window tracking

### Sub-package layout

```
apps/service/tokens/
├── __init__.py        # estimate_tokens(text, *, provider, model) -> int
│                      # context_window(provider, model) -> int | None
│                      # estimate_action_total(action, system_prompt) -> int
├── estimate.py        # v1: char/4 heuristic with future-pluggable hook
├── limits.py          # CONTEXT_WINDOWS table (provider, model) -> int
└── tests/
    └── test_estimate.py
```

Pure functions, no I/O, no Qt, no `apps.gui` imports. Same hard-import
rule as the canvas / browser_bridge packages: this package is reusable
in isolation.

### Response shape additions

`drones.send` (and the new `drones.append_assistant_turn` introduced in
PR 2) return three extra fields:

```json
{
  "prompt_tokens":     3200,
  "transcript_tokens": 12400,
  "context_window":    200000
}
```

`context_window` is `null` for unknown `(provider, model)` pairs.
Existing callers ignore unknown keys — pure additive change.

### GUI widget

`apps/gui/widgets/context_gauge.py` — small `QWidget` with three layers:
a label `~12.4K / 200K tokens (est)`, a horizontal progress bar coloured
by percentage, and a trailing percent string. Hidden when
`context_window is None`.

Colour bands:

* <60% green
* 60–80% amber (consider forking the drone soon)
* 80–95% orange (next turn at risk of truncation)
* ≥95% red (truncation imminent — fork now)

Rendered in:

1. Drones tab footer (per-drone running total)
2. Canvas chat dialog footer (same data, smaller layout)
3. `BrowserBridgeDialog` header (PR 2; per-turn prompt size + projected
   post-paste total)

### Estimation accuracy

v1 uses `max(1, len(text) // 4)` — accurate ±30% on English. Labelled
`~` and `(est)` so the operator never confuses it for an exact count.
Amber starts at 60%, leaving headroom. v2 (separate future PR) plugs in
real tokenizers (`tiktoken`, `anthropic.count_tokens`, SentencePiece)
behind the same `estimate_tokens()` signature.

### Out of scope (PR 1)

* Exact per-provider tokenizers
* Auto-forking a drone when total > 80%
* Cost-in-dollars overlay
* Transcript summarisation when nearing limit

---

## PR 2 — Browser provider, URL routing, link-back, handoff

### Sub-package layout

```
apps/gui/browser_bridge/
├── __init__.py             # public: BrowserBridgeDialog, ClipboardRouter
├── README.md
├── dialog.py               # BrowserBridgeDialog: prompt textbox +
│                           # listener status + paste-back textbox
├── clipboard_listener.py   # OS-level watcher; parses CF_HTML/text-html
│                           # for SourceURL
├── clipboard_router.py     # multi-drone routing based on source URL
├── url_launcher.py         # webbrowser.open + cross-platform clipboard set
├── handoff.py              # format renderers: continuation / fork / plain
└── tests/
    ├── conftest.py
    ├── test_url_launcher.py
    ├── test_clipboard_router.py
    └── test_handoff.py
```

Hard rule: no `apps.service.*` or `apps.gui.ipc.*` imports inside this
sub-package. The caller (`apps/gui/windows/drones.py`,
`apps/gui/canvas/drone_chat_dialog.py`) wires it to `RpcClient`.

### Data model

```python
# apps/service/types.py
class Blueprint:
    ...
    chat_url: str | None = None          # for provider="browser"; default
                                         # "https://claude.ai/new" suggested
                                         # in the GUI when picked

class DroneAction:
    ...
    bound_chat_url: str | None = None    # pinned after first paste-back
                                         # from a specific conversation URL
                                         # e.g. claude.ai/chat/<uuid>
```

SQLite migration (additive only):

```sql
ALTER TABLE drone_blueprints ADD COLUMN chat_url TEXT;
ALTER TABLE drone_actions    ADD COLUMN bound_chat_url TEXT;
```

### Service-side surface

Branch in `drones_send`: when `blueprint_snapshot.provider == "browser"`:

1. Append the user turn to the transcript (persisted regardless).
2. Render the prompt using PR #39's v3 history-in-system logic.
3. Return without calling any LLM:

```json
{
  "needs_paste": true,
  "rendered_prompt": "...",
  "chat_url": "https://claude.ai/new",
  "bound_chat_url": null | "https://claude.ai/chat/<uuid>",
  "prompt_tokens": 3200,
  "transcript_tokens": 8200,
  "context_window": 200000,
  "action": <updated drone action>
}
```

New RPCs:

* `drones.append_assistant_turn(action_id, content)` — appends the
  assistant turn from the operator's paste. Returns updated action +
  fresh token totals.
* `drones.bind_chat_url(action_id, url)` — pins `bound_chat_url`. Called
  automatically after the first paste-back lands; also exposed as a
  manual "Re-link…" action in the GUI.
* `drones.export(action_id, format)` — returns formatted handoff text;
  `format ∈ {"continuation", "fork", "plain"}`.

### Clipboard listener — what it captures

```python
@dataclass
class ClipboardEvent:
    text: str
    source_url: str | None      # parsed from CF_HTML (Win) / text/html
                                # (Mac/Linux) SourceURL header
    source_title: str | None    # macOS extra via WebURLsWithTitlesPboardType
    captured_at: datetime
```

OS support:

| OS | Mechanism | Notes |
|----|-----------|-------|
| Windows | `AddClipboardFormatListener` via `pywin32` | Push-based; instant; already a dependency |
| macOS | `NSPasteboard.changeCount` poll, 200 ms | No public push API |
| Linux | `pyperclip` poll, 200 ms | Works under X11 + Wayland |

### Multi-drone parallel routing (M9)

`ClipboardRouter` wraps the listener:

1. Clipboard fires with `source_url`.
2. Router finds the drone whose `bound_chat_url == source_url` — routes
   there.
3. If none bound, finds drones whose `chat_url` is a prefix match. One
   match → ask "save for drone X?". Multiple → small picker.
4. No match → silent skip; debug-mode logs the rejected event.

Five browser drones, five tabs, app auto-routes.

### Link back to the browser

Three surfaces, all wired to `webbrowser.open(action.bound_chat_url)`:

1. Drones tab header: `🔗 claude.ai/chat/4f8…` icon next to drone title.
2. Canvas `DroneActionNode` tooltip on hover.
3. Canvas chat dialog header: same icon.

`bound_chat_url is None` → icon hidden. Operator can hit "Re-link…" to
clear and rebind on next paste.

### Handoff menu — three formats (v1)

Popover menu in the Drones tab and canvas chat dialog:

| Format | Contents | Best for |
|--------|----------|----------|
| Continuation prompt | Persona + skills + every prior turn, framed as "pick up from the last user turn" | Continuing in a fresh tab of the same or a different service |
| Fork — same role, fresh start | Persona + skills only, no prior turns | Spawning a sibling conversation with the same character |
| Plain transcript | User / assistant turns only, no system framing | Sharing, doc embedding, code review |

Each option copies to clipboard + shows a toast with the estimated token
count + an "Open <service> in browser" shortcut.

### Out of scope (PR 2)

* System tray + global hotkey (sketched in plan as Rung 3, future PR)
* Browser extension (Rung 4, separate project)
* Auto-typing into the browser (PyAutoGUI-style — explicitly avoided)
* Service-specific handoff formats (v2; v1's universal Continuation
  format works across claude.ai / ChatGPT / Gemini)

---

## PR 3 — claude-cli stream-json sub-agent capture

### Sub-package layout

```
apps/service/providers/claude_cli/
├── __init__.py             # public re-export of ClaudeCLIProvider
├── provider.py             # ClaudeCLIProvider class (was claude_cli.py)
├── session.py              # ClaudeCLIChatSession (was inside the same file)
├── stream_parser.py        # parse claude --output-format stream-json events
└── tests/
    ├── test_stream_parser.py
    └── test_session.py
```

### Transcript-entry schema

Today `DroneAction.transcript` is `list[{"role": "user|assistant",
"content": str}]`. Extend additively:

```python
{"role": "user", "content": "..."}                                     # operator
{"role": "assistant", "content": "..."}                                # final reply
{"role": "tool_call", "tool": "Bash",
 "input": {"command": "ls"}, "step": 3}                                # invocation
{"role": "tool_result", "tool": "Bash",
 "output": "...", "step": 3}                                           # result
{"role": "subagent", "agent_id": "abc",
 "prompt": "...", "result": "..."}                                     # delegated
```

Schema stays `list[dict[str, Any]]`. Old transcripts continue to work.

### GUI rendering

Drones tab + chat dialog render each kind distinctly:

* `user` / `assistant` — today's blue/grey bubbles
* `tool_call` — collapsible monospace card "Used Bash: `ls -la`"
* `tool_result` — same, with truncated output
* `subagent` — nested mini-bubble with delegated prompt + result,
  collapsible

Operator collapses agentic detail to see just user/assistant, expands to
see the full thinking trail.

### Out of scope (PR 3)

* Live streaming display (we wait for the full response before
  rendering); follow-up if desired
* Interactive sub-agent control (cancelling a sub-agent mid-run)

---

## Data model deltas — consolidated

```python
# apps/service/types.py
class Blueprint:
    chat_url: str | None = None         # PR 2

class DroneAction:
    bound_chat_url: str | None = None   # PR 2

# transcript entries gain optional new kinds (PR 3)
# no class change — existing list[dict[str, Any]] accepts them
```

SQLite migrations consolidated:

```sql
-- PR 2
ALTER TABLE drone_blueprints ADD COLUMN chat_url TEXT;
ALTER TABLE drone_actions    ADD COLUMN bound_chat_url TEXT;
```

PR 3 needs no schema change (transcript is already JSON-blob).

---

## Risks

| Risk | PR | Mitigation |
|------|----|------------|
| Token estimate is off by 30% — operator hits the wall before the gauge warns | 1 | `~ (est)` labelling; amber starts at 60% leaving headroom; v2 plugs in real tokenizers |
| Unknown model pair → gauge hidden | 1 | Document how to extend `CONTEXT_WINDOWS`; future RPC `tokens.context_window` so GUI can query without hard-coding |
| Operator pastes wrong content into the paste-back | 2 | Dialog shows captured source URL prominently; mismatch with `bound_chat_url` triggers a warning |
| Operator copies from a non-browser source → no source URL | 2 | Listener falls back to single-drone-dialog routing |
| Sub-agent stream-json format changes between claude-code versions | 3 | Parser is version-tolerant; unknown event kinds skipped with a debug-log entry |

---

## Verification

After all three PRs land + `Update + Restart`:

1. **Token tracking** — every drone in the Drones tab shows a gauge in
   its footer. Pick a drone with a few turns; gauge displays a small
   percentage. Send another turn; numbers update.
2. **Browser provider** — create a new blueprint with `provider: browser`
   and a chat URL. Deploy a drone. Send a message: browser opens, prompt
   in clipboard, paste, get reply, copy, dialog toasts, save. Transcript
   shows both turns. Re-link icon points at the bound conversation URL.
3. **Parallel routing** — deploy two browser drones in different tabs.
   Send messages from both. Copies from each tab route to the right
   drone automatically.
4. **Handoff** — pick a drone, hit Handoff → Continuation prompt, paste
   into a different service's tab, confirm the conversation picks up.
5. **Sub-agent capture** — send a complex task to a claude-cli drone
   (e.g. "find every file in this repo containing 'foo'"). Transcript
   shows the tool_call / tool_result entries inline with the final
   assistant turn. Each is collapsible.

---

## Out-of-band future work

These are sketched here so they don't get re-litigated when someone
revisits this area later:

1. **Exact tokenizers** — `tiktoken` for GPT, `anthropic.count_tokens`
   for Claude, SentencePiece for Gemini. Pluggable behind the same
   `estimate_tokens()` signature.
2. **System tray + global hotkey** — Rung 3 of the browser-bridge ladder.
   ~30 LOC on top of PR 2.
3. **Browser extension** — Rung 4. Distinct security and packaging
   work; only worth it if Rung 2 friction proves intolerable.
4. **Auto-fork on context approach** — when `transcript_tokens > 80% *
   context_window`, offer to fork the drone with a summarised history.
5. **Cost-in-dollars overlay** — pair tokens with $/token for paid
   providers; estimate run cost.
6. **Cross-service handoff polish** — per-service tagged formats
   (Claude prefers XML, GPT prefers plain, Gemini is flexible).
