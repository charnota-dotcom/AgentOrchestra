# Desktop launcher scripts (Windows)

Two double-clickable scripts so you don't have to type commands.

## `launch.cmd`

Opens two terminal windows:

1. **AgentOrchestra Service** — the background process that holds the
   SQLite store, dispatches runs, and exposes the JSON-RPC + SSE API
   on `127.0.0.1:8765`.
2. **AgentOrchestra GUI** — the PySide6 desktop app you click around in.

Both auto-activate the project's `.venv` and start in the right
directory regardless of where you launch the script from. The GUI
window opens ~5 seconds after the service so the RPC call lands
cleanly.

## `stop.cmd`

Closes both windows opened above. Matches by window title so unrelated
Python processes are unaffected.

## One-time setup: put it on your desktop

1. Open File Explorer to this folder.
2. Right-click **`launch.cmd`** → **Send to** → **Desktop (create
   shortcut)**.
3. (Optional) Right-click the shortcut on your desktop → **Rename**
   → call it `AgentOrchestra`.
4. (Optional) Right-click the shortcut → **Properties** →
   **Change Icon…** if you want something prettier than the default.

Same for `stop.cmd` if you want a one-click shutdown.

## If `launch.cmd` complains about a missing virtual environment

The script will print clear instructions and pause so you can read
them. The fix is the one-time install:

```
cd "C:\Users\<you>\OneDrive\Documents\GitHub\AgentOrchestra"
python -m venv .venv
.venv\Scripts\activate.bat
pip install -e ".[gui]"
```

After that, double-clicking `launch.cmd` should always work.
