# AgentOrchestra — one-click scripts (Windows)

This folder is the operator's panel: every common terminal command,
packaged as a double-clickable `.cmd` so you don't have to memorise
or type any of it.

| Script | What it does | When to run |
|--------|--------------|-------------|
| **`ops.cmd`** | Opens the Operator Panel — a tiny GUI with one button per command in this folder, plus a live output pane. Reads `manifest.json`, so any command added there shows up automatically. | Every day. The single "I want to do an operation" entry point. |
| **`setup.cmd`** | First-time install: creates `.venv`, installs the project + `[gui]` extras, optionally installs `pyside6_annotator` if it lives at `..\Annotator\pyside6_annotator_pkg`. | Once, after cloning. Re-run any time `.venv` goes missing. |
| **`launch.cmd`** | Opens the main AgentOrchestra GUI. The service is auto-spawned in the background; no separate window. | Every session — also reachable from the Ops Panel. |
| **`stop.cmd`** | Closes the GUI window and any background service it supervised. Matches by window title — leaves unrelated Python processes alone. | When you close the laptop or want to free port 8765. |
| **`update.cmd`** | `git pull --ff-only origin main` + `pip install -e .[gui] --upgrade`. | After GitHub Desktop's "Pull origin", or before reporting a bug. |
| **`doctor.cmd`** | One-page health report: Python version, `.venv` status, `claude` / `gemini` on PATH, port 8765, local data dir, annotator import, AgentOrchestra version. | When something's wrong. Copy/paste the output into a bug report. |
| **`test-claude.cmd`** | Smoke-test the local `claude` CLI: PATH check + `claude -p "..."` headless call. Surfaces "Not logged in" if your Max-plan auth lapsed. | First-time setup, or when Claude cards stop replying. |
| **`test-gemini.cmd`** | Smoke-test the local `gemini` CLI: PATH check + `gemini -p "..."` headless call. | First-time setup, or when Gemini cards stop replying. |
| **`reset.cmd`** | Wipes local state (SQLite store, first-run sentinel, annotation logs). Does **not** touch your repo, git history, or CLI auth. Confirms before deleting. | When the local DB is wedged and you want a clean slate. |

The Operator Panel (`ops.cmd`) is the simplest entry point: every
other script becomes a button there with its own summary, "when to
run" hint, and live output. The plain `.cmd` files stay double-
clickable too — you choose the workflow.

## Make them all desktop shortcuts (one-time)

For each `.cmd` you want on your desktop:

1. Open File Explorer to this folder.
2. Right-click the script → **Send to** → **Desktop (create shortcut)**.
3. (Optional) Right-click the new shortcut → **Rename** → e.g. `AgentOrchestra` for `launch.cmd`, `AgentOrchestra (stop)` for `stop.cmd`.
4. (Optional) Right-click → **Properties** → **Change Icon…** if you want a nicer icon.

Most operators end up with **two** shortcuts: `launch` and `stop`. The
others you only need occasionally so they live here, in the repo.

## Typical first-time flow

```
  setup.cmd       — once
  launch.cmd      — start the app
  (use it)
  stop.cmd        — when done
```

## Typical "I pulled new code" flow

```
  update.cmd      — pull + refresh deps
  stop.cmd        — close any old running instance
  launch.cmd      — start fresh
```

## Typical "something's broken" flow

```
  doctor.cmd      — read the report
  (paste it into a bug report or use it to debug)
  reset.cmd       — only if local DB is the suspected culprit
  launch.cmd      — start fresh
```

## Behind the scenes

Every script:

* Resolves its own location via `%~dp0` so it works whether
  double-clicked from File Explorer, run from a desktop shortcut, or
  invoked from any working directory.
* Re-uses the project's `.venv` by `call .venv\Scripts\activate.bat`
  rather than installing globally.
* **Always waits for a keypress before closing**, on both success
  and failure. The window stays on screen so any error message,
  traceback, or status line is readable. Press any key to close
  when you're done reading.
* `launch.cmd` and `ops.cmd` use `cmd /k` (not `/c`) so the host
  cmd window stays open after the GUI / panel exits — useful when
  the GUI crashes and the traceback is in the cmd window rather
  than in the GUI itself. Type `exit` in that window when done.

If you'd rather configure them programmatically, the same set is
indexed in `manifest.json` (id, label, file, summary, when_to_run,
writes, needs_internet) — useful for any wrapper UI or CI tool that
wants to introspect what's available.

## Reference for help / advice

`scripts/manifest.json` is the canonical list of operator commands.
When the assistant gives you instructions, it should look here first
and prefer "double-click `setup.cmd`" over a typed command sequence.
If a workflow isn't covered by an existing entry, it adds a new one
to the manifest before suggesting commands you'd otherwise have to
remember.

## Why `.cmd` and not `.bat`

`.cmd` and `.bat` are functionally identical on modern Windows; we
use `.cmd` for the slightly more sensible default error-handling
behaviour (`%ERRORLEVEL%` is propagated more predictably under nested
`call`).
