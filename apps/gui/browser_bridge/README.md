# Browser bridge

This sub-package owns the **browser provider's** GUI surface — the
copy/paste workflow that lets an operator run a drone conversation
inside a browser tab (claude.ai, ChatGPT, Gemini, anything URL-
addressable) while the app keeps the persistence, blueprint
templating, parallel-drone management, and context-window tracking.

See `docs/BROWSER_PROVIDER_PLAN.md` for the full design.

## What's in here

| File | What it does |
|------|--------------|
| `dialog.py` | `BrowserBridgeDialog`: a non-modal QDialog with the rendered prompt on top (read-only + Copy button), a paste-back textbox at the bottom (Save reply / Cancel buttons), and a status line showing the clipboard listener's most recent event. Hosts a `ContextGauge` from PR 1 so the operator sees prompt size + projected post-paste total. |
| `clipboard_listener.py` | OS-level clipboard watcher. Windows: `AddClipboardFormatListener` via pywin32 (push-based). Mac/Linux: 200ms poll. Emits `ClipboardEvent(text, source_url, source_title, captured_at)`. Source URL is parsed from `CF_HTML` / `text/html` mime — Chrome/Edge/Firefox embed the source page's URL there automatically when you copy from a web page. |
| `clipboard_router.py` | Multi-drone routing. Given a `ClipboardEvent`, finds the drone whose `bound_chat_url` matches; falls back to `chat_url` prefix match; otherwise asks. Lets the operator orchestrate N parallel browser tabs from one app. |
| `url_launcher.py` | Cross-platform `webbrowser.open(url)` + clipboard `setText(text)`. Auto-prepends `https://` when the URL has no scheme. |
| `handoff.py` | Three format renderers — *continuation* (full transcript + persona), *fork* (persona only, fresh start), *plain* (transcript only). Mirrors the server-side `drones.export` RPC so the GUI can compose handoff prompts client-side too. |

## Hard import rule

Nothing in this sub-package imports from `apps.service.*` (except
the pure-data `apps.service.tokens` package) or `apps.gui.ipc.*`.
The caller (`apps/gui/windows/drones.py`,
`apps/gui/canvas/drone_chat_dialog.py`) wires this package to the
`RpcClient` via constructor injection. Keeps the package testable
in isolation and reusable.

## Cross-platform clipboard support

| OS | Mechanism | Notes |
|----|-----------|-------|
| Windows | `AddClipboardFormatListener` via `pywin32` | Push-based; instant; already a dependency |
| macOS | `NSPasteboard.changeCount` poll, 200 ms | No public push API |
| Linux | `pyperclip` poll, 200 ms | Works under X11 + Wayland |

The polling fallback uses `pyperclip` if available; otherwise the
listener gracefully degrades to "manual paste-back only" — the
operator can still paste into the dialog's bottom textbox by hand.

## Where this fits in the data flow

```
operator → app (BrowserBridgeDialog) → clipboard / new browser tab
                                     ↓
                              [claude.ai / ChatGPT / etc.]
                                     ↓
                            operator copies reply
                                     ↓
                       clipboard_listener fires
                                     ↓
                       clipboard_router picks drone
                                     ↓
                BrowserBridgeDialog → drones.append_assistant_turn
                                     ↓
                          persisted transcript
```

The operator does the same two keystrokes per turn that they'd do
in claude.ai by itself — paste in the browser, copy the reply —
but every conversation lands in the app's persistent store, with
all the orchestration features around it.
