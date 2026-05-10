# AgentOrchestra — one-click scripts (Windows)

This folder is the operator's panel: every common terminal command,
packaged as a double-clickable `.cmd` so you don't have to memorise
or type any of it.

| Script | What it does | When to run |
|--------|--------------|-------------|
| **`setup.cmd`** | First-time install: creates `.venv`, installs the project + `[gui]` extras, optionally installs `pyside6_annotator` if it lives at `..\Annotator\pyside6_annotator_pkg`. | Once, after cloning. Re-run any time `.venv` goes missing. |
| **`launch.cmd`** | Opens the GUI. The service is auto-spawned in the background by the GUI itself; no separate window. | Every session. Make this your desktop shortcut. |
| **`stop.cmd`** | Closes the GUI window and any background service it supervised. Matches by window title — leaves unrelated Python processes alone. | When you close the laptop or want to free port 8765. |
| **`update.cmd`** | `git pull --ff-only origin main` + `pip install -e .[gui] --upgrade`. | After GitHub Desktop's "Pull origin", or before reporting a bug. |
| **`doctor.cmd`** | One-page health report: Python version, `.venv` status, `claude` / `gemini` on PATH, port 8765, local data dir, annotator import, AgentOrchestra version. | When something's wrong. Copy/paste the output into a bug report. |
| **`reset.cmd`** | Wipes local state (SQLite store, first-run sentinel, annotation logs). Does **not** touch your repo, git history, or CLI auth. Confirms before deleting. | When the local DB is wedged and you want a clean slate. |

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
* Pauses on failure so the error stays on screen instead of the cmd
  window flashing closed.

If you'd rather configure them programmatically, the same set is
indexed in `manifest.json` (id, label, file, summary, when_to_run,
writes, needs_internet) — useful for any wrapper UI or CI tool that
wants to introspect what's available.

## Why `.cmd` and not `.bat`

`.cmd` and `.bat` are functionally identical on modern Windows; we
use `.cmd` for the slightly more sensible default error-handling
behaviour (`%ERRORLEVEL%` is propagated more predictably under nested
`call`).
