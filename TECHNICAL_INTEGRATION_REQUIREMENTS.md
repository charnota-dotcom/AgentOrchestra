# Technical Integration Requirements â€” pyside6_annotator v1.4.23

**Audience**: AI agent implementing a new PySide6 app that embeds this library.
**Requirements**: Python 3.10+, PySide6 >= 6.5

---

## 1. Import Statements

```python
from pyside6_annotator import AnnotationManager, FloatingAnnotationBar
from pyside6_annotator import (
    append_attempt,      # log a fix attempt to the action log
    load_actions,        # read the action log dict {index: [attempts]}
    load_global_instructions,  # read the top-level AI instructions string
    regenerate_markdown, # rewrite the .md companion from JSON
    Annotation,          # dataclass for a single annotation
    get_annotation_manager,    # return the live AnnotationManager or None
    MODE_SINGLE,         # annotation mode constants
    MODE_MULTI_CLICK,
    MODE_MULTI_DRAG,
    MODE_AREA,
)
from pathlib import Path
```

All symbols above are exported from the package's `__init__.py`. Do not import directly from `_overlay`, `_floating_bar`, or `_action_log`.

---

## 2. Constructor Signatures

### 2.1 AnnotationManager

```python
AnnotationManager(
    window,               # positional, required â€” QWidget (typically QMainWindow)
    data_dir=None,        # positional-or-keyword, Optional[Path]
    *,                    # keyword-only after here
    app_name="App",       # str â€” display name shown in the Review dialog and action log
    app_version="0.0.0",  # str â€” shown in AI action log entries; use SemVer e.g. "1.0.0"
    action_log_path=None, # Optional[Path] â€” explicit path to the actions JSON log file
    navigate_to=None,     # Optional[Callable[[str], bool]] â€” callback; see section 4
    tab_hint_map=None,    # Optional[dict] â€” maps widget class names to QTabWidget indices
    _is_primary=True,     # bool â€” internal; do NOT set to False in a new integration
    _allow_annotator_ui=False,  # bool â€” internal; do NOT set in a new integration
)
```

**Parameter details:**

| Parameter | Type | Required | Purpose |
|-----------|------|----------|---------|
| `window` | `QWidget` | Yes | The main window the overlay attaches to. All badge and highlight widgets are children of this window. Must be the window that is already constructed before calling this constructor. |
| `data_dir` | `Path \| None` | Strongly recommended | Directory where `annotations.json` is written. If `None`, annotations are not persisted to disk. Must exist before instantiation (see section 7). |
| `app_name` | `str` | Yes (has default) | Appears in the Review dialog title, action log headers, and the clipboard JSON payload as `app_name`. The string is also lowercased and slug-ified (non-word chars â†’ `_`) to derive the action log filename auto-path on Windows. |
| `app_version` | `str` | Yes (has default) | Used as the `version` field in every action log entry. Should match the app's own version string. |
| `action_log_path` | `Path \| None` | Optional | Explicit path to `_claude_actions.json`. If omitted, the manager auto-creates a file at `~/OneDrive/Desktop/Annotation logs/<app_slug>.json` (Windows only; silently disabled on other platforms if the path does not exist). Supply an explicit path to ensure cross-platform action logging. |
| `navigate_to` | `Callable[[str], bool] \| None` | Recommended | Callback invoked when the user clicks "Jump" in the Review dialog. See section 4. |
| `tab_hint_map` | `dict \| None` | Optional | Maps widget class names (strings) to `QTabWidget` tab indices. Used to infer which tab to switch to before retrying a jump. Override built-in defaults. |

**Do not pass** `_is_primary=False` or `_allow_annotator_ui=True` â€” those are internal flags used by `FloatingAnnotationBar` when it creates a secondary self-annotation manager.

---

### 2.2 FloatingAnnotationBar

```python
FloatingAnnotationBar(
    manager,              # positional, required â€” AnnotationManager instance
    *,                    # keyword-only after here
    data_dir=None,        # Optional[Path] â€” enables self-annotation of the bar itself
    action_log_path=None, # Optional[Path] â€” passed through to the self-manager
    host=None,            # Optional[QWidget] â€” used only for initial Y positioning
    parent=None,          # Optional[QWidget] â€” MUST be None in most integrations
    settings_key=None,    # Optional[str] â€” per-app QSettings sub-key for Y position
)
```

**Parameter details:**

| Parameter | Type | Required | Purpose |
|-----------|------|----------|---------|
| `manager` | `AnnotationManager` | Yes | Must be the already-constructed primary `AnnotationManager`. The bar wires itself to the manager's buttons in `__init__`. |
| `data_dir` | `Path \| None` | Optional | If supplied, enables the "Annotate bar" / "Review bar" self-annotation section and stores bar annotations in `<data_dir>/.annotator/annotations.json`. If `None`, the self-annotation section is hidden. |
| `action_log_path` | `Path \| None` | Optional | Accepted but **silently ignored** â€” `_build_self_manager()` always uses a hardcoded OneDrive path for the bar's own action log regardless of this value. Ignored entirely if `data_dir` is `None`. |
| `host` | `QWidget \| None` | Optional | The main window. Used only to compute the initial Y center position of the bar. Has no effect after positioning. |
| `parent` | `QWidget \| None` | **Must be None** | See section 5. |
| `settings_key` | `str \| None` | Optional | If provided, the bar's Y position is stored in QSettings under `floating_bar/<settings_key>/edge_y`. Use this when multiple apps share the same QSettings organization/app name to avoid position collisions. |

---

## 3. Instantiation Order

**AnnotationManager must be constructed before FloatingAnnotationBar.** The bar's `__init__` calls `manager.set_toggle_button()`, `manager.set_review_button()`, `manager.set_export_button()`, and `manager.set_anchor()` immediately. If the manager does not exist yet these will raise `AttributeError`.

Correct sequence inside `QMainWindow.__init__()`:

```python
def __init__(self):
    super().__init__()
    # ... build your UI ...

    # Step 1: ensure data_dir exists BEFORE constructing the manager.
    # _save_annotations() silently swallows all I/O errors â€” there is no
    # exception to catch if this directory is missing; annotations are lost silently.
    _data_dir = Path.home() / ".myapp"
    _data_dir.mkdir(parents=True, exist_ok=True)

    # Step 2: construct the manager (attaches to self as QObject parent)
    self._annotation_mgr = AnnotationManager(
        window=self,
        data_dir=_data_dir,
        app_name="My App",
        app_version="1.0.0",
        navigate_to=self._annotation_navigate_to,
    )

    # Step 3: construct the bar (wires itself to the manager).
    # parent=None is required â€” see section 5.
    self._annotation_bar = FloatingAnnotationBar(
        self._annotation_mgr,
        data_dir=_data_dir,
        host=self,
        parent=None,  # MUST be None â€” non-None parent in QStackedWidget layouts destroys the bar
    )
    # Note: FloatingAnnotationBar.__init__ calls _place_collapsed() which calls show()
    # internally, so the bar is already visible here. The explicit show() below is
    # harmless but makes intent clear.
    self._annotation_bar.show()

    # Step 4: wire destroyed signal for cleanup
    self.destroyed.connect(self._annotation_bar.close)
```

---

## 4. The navigate_to Callback

### Signature

```python
def my_navigate_to(screen_name: str) -> bool:
    ...
```

- **Receives**: `screen_name` â€” a `str` containing the `screen_name` field recorded on the `Annotation` at the time it was created. This is whatever value was set on `ann.screen_name`. If the annotation was created before `screen_name` was recorded, this will be an empty string `""`.
- **Must return**: `True` if navigation was attempted (regardless of whether it succeeded), `False` if no navigation is possible (e.g. the screen name is unrecognised or empty). The library calls `bool()` on the return value.
- **What it must do**: Navigate the app's `QStackedWidget` (or equivalent) to show the screen that contains the annotated widget, so the jump-to mechanism can then locate and highlight the widget. The callback is called from the Qt main thread during a click on the "Jump" button in the Review dialog.

### Example implementation

```python
def _annotation_navigate_to(self, screen_name: str) -> bool:
    screen_map = {
        "HomeScreen":     0,
        "SettingsScreen": 1,
        "DetailScreen":   2,
    }
    idx = screen_map.get(screen_name)
    if idx is None:
        return False
    self._stack.setCurrentIndex(idx)
    return True
```

### What happens internally

The callback is stored as `AnnotationManager._navigate_to_cb`. When the user clicks "Jump" in the Review dialog (`_ReviewDialog._navigate_to_screen`):

1. `cb(ann.screen_name)` is called.
2. If it returns truthy, the library waits 350 ms (one Qt paint cycle) then retries `_find_annotated_widget(ann)`.
3. `_find_annotated_widget` walks `QApplication.allWidgets()` matching by `type(w).__name__ == ann.tag`, then by `objectName`, text snippet, or bounding-box proximity.
4. If a match is found, a blue dashed `_HighlightFrame` is flashed for 2.5 s.

**Threading**: The callback is always invoked from the Qt main thread. Do not perform blocking I/O inside it.

---

## 5. FloatingAnnotationBar parent=None Requirement

The `FloatingAnnotationBar` is constructed with Qt window flags:
```
Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
```

It is a **top-level window**, not a child widget.

**Why `parent=None` is required in QStackedWidget layouts:**

When a `QStackedWidget` swaps pages, Qt sends `WM_DESTROY` (Windows) or equivalent destroy events to widgets that have the stack page as their Qt parent. If `FloatingAnnotationBar` is given any widget inside the stack (or the stack itself) as its Qt parent, it will be destroyed and recreated whenever the page changes. Setting `parent=None` makes it a true top-level `Tool` window that is owned by the OS, not by the Qt widget hierarchy, so it survives page switches.

The `host` parameter is separate â€” it is used during `_place_collapsed()` to read `host.screen()` for multi-monitor awareness and to compute the initial Y position as `host.y() + (host.height() - bar_height) // 2`. It **is** stored as `self._host` for the lifetime of the bar object.

**`WA_DeleteOnClose` is explicitly set to `False`** in the bar's `__init__`, so calling `.close()` hides it without destroying the C++ object. This means it is safe to call `self.destroyed.connect(self._annotation_bar.close)` â€” `close()` triggers `closeEvent()` which removes the QApplication event filter and stops the animation, but does not delete the widget.

---

## 6. destroyed Signal Wiring

```python
self.destroyed.connect(self._annotation_bar.close)
```

This line must appear in the main window's `__init__` after both the manager and bar are constructed.

**Why it matters:**

The `FloatingAnnotationBar` installs a `_CollapseFilter` (a `QObject` event filter) on `QApplication.instance()` via `installEventFilter()`. If the bar is not explicitly closed when the main window is destroyed, this filter remains installed on the QApplication. On subsequent accesses (e.g. if the app creates a new window or if teardown order is non-deterministic), the filter's `eventFilter()` method tries to access `self._bar`, which is a dangling C++ pointer, causing a `RuntimeError`. The `closeEvent()` of `FloatingAnnotationBar` calls `QApplication.instance().removeEventFilter(self._collapse_filter)` to prevent this.

The `_CollapseFilter.eventFilter()` method already guards against this with a `try/except RuntimeError` block, but the explicit `close()` connection is the clean solution and should always be present.

---

## 7. data_dir

### What it stores

When `data_dir` is provided to `AnnotationManager`, annotations are persisted to:
```
<data_dir>/annotations.json
```

When `data_dir` is also provided to `FloatingAnnotationBar`, the bar's self-annotations are stored in:
```
<data_dir>/.annotator/annotations.json
```

The `.annotator/` subdirectory is created automatically by `_build_self_manager()` if it does not exist (using `bar_dir.mkdir(parents=True, exist_ok=True)`).

### Must exist before instantiation

`data_dir` itself **must already exist** before `AnnotationManager.__init__` is called. The manager constructs the annotations path as `Path(data_dir) / "annotations.json"` and immediately calls `_load_annotations()` which opens the file for reading. If `data_dir` does not exist, `_load_annotations()` silently no-ops (the path will not be found). **Critically: `_save_annotations()` also silently swallows all I/O errors** â€” it calls `parent.mkdir(parents=True, exist_ok=True)` before writing but wraps everything in a broad `except Exception: pass`. Data loss occurs silently with no exception raised to the caller. This is why the pre-creation pattern is essential; there is no error signal to catch.

### Recommended pattern

```python
data_dir = Path.home() / ".myapp"
data_dir.mkdir(parents=True, exist_ok=True)

self._annotation_mgr = AnnotationManager(
    window=self,
    data_dir=data_dir,
    ...
)
```

Use a per-app subdirectory of the user's home directory (e.g. `~/.myapp`) to avoid collisions between apps. Never share a `data_dir` between two apps running simultaneously.

---

## 8. app_name and app_version

### app_name

- Displayed in the Review dialog window title area (as part of the app payload's `app_name` field when annotations are copied to clipboard).
- Used to derive the slug for the automatic action log path: non-word characters are replaced with `_`, leading/trailing underscores stripped. Example: `"My App"` â†’ `"my_app"` â†’ log file `my_app.json`.
- Used by `_process_pending_actions()` to match `for_app` in `_pending_actions.json`. The slug comparison is case-sensitive and must match exactly. **Use the same `app_name` string every time the app is launched.**
- Expected format: any human-readable string. Keep it concise (it appears in UI). Example: `"Flashcard Invigilator"`, `"Sightread"`.

### app_version

- Stored in each action log attempt as the `version` field.
- Displayed in the Review dialog's AI action cards (e.g. `v1.2.3 Â· shipped Â· 2026-01-15`).
- Expected format: SemVer string such as `"1.0.0"` or `"2.3.14"`. Arbitrary strings are accepted but SemVer is strongly preferred as the UI renders it with a `v` prefix.

---

## 9. _pending_actions.json

### Purpose

`_pending_actions.json` is the AI worker's mechanism for submitting fix attempts into the action log without needing to call Python code directly. The file is dropped into the app's working directory (or a configured scan path), and the annotator processes it automatically on the next app launch.

### Where to place it

Place the file in **the same directory as the app's entry point** (`main.py` or equivalent). The annotator always scans `Path.cwd()` as a fallback, plus any directories listed in `scan_paths.json` (one level deep, including immediate subdirectories). After processing, the file is renamed to `_pending_actions.processed`.

### When it is processed

`_process_pending_actions()` is invoked via `QTimer.singleShot(0, ...)` in `AnnotationManager.__init__`, meaning it runs after the first Qt event loop tick â€” i.e. after `show()` is called and the window appears, but during normal app startup. The delay avoids blocking the UI while scanning OneDrive paths.

### Schema

The file must be valid JSON with this exact structure:

```json
{
    "for_app": "my_app",
    "attempts": [
        {
            "annotation_index":    42,
            "version":             "1.2.0",
            "status":              "shipped",
            "description":         "Under 100 words â€” what was wrong and what you did.",
            "change_overview":     ["One entry per file or function changed"],
            "risk_level":          "Low",
            "risk_note":           "One sentence on side effects or areas to watch.",
            "next_steps":          ["Run unit tests", "Ready to merge"],
            "files_changed":       ["myapp/ui/history_screen.py"],
            "annotation_excerpt":  "First 8 words of the annotation comment",
            "first_seen_version":  "1.0.0",
            "notes":               ""
        }
    ]
}
```

**Field rules:**

| Field | Type | Required | Constraint |
|-------|------|----------|-----------|
| `for_app` | `str` | Yes | Must match `re.sub(r"[^\w\-]", "_", app_name.lower()).strip("_")` exactly. **If absent (empty string or missing key), the file is processed by ANY app** â€” absent `for_app` acts as a wildcard. If present but mismatched, the file is ignored. |
| `attempts` | `list` | Yes | One object per annotation being addressed. |
| `annotation_index` | `int` | Yes | Must match an existing annotation index from the app's `annotations.json`. |
| `version` | `str` | Yes | The new version string being shipped. |
| `status` | `str` | Yes | Must be exactly one of: `"shipped"`, `"no_change_needed"`, `"partial"`, `"blocked"`, `"wontfix"`. Any other value raises `ValueError` inside `append_attempt()` and the attempt is silently skipped. |
| `description` | `str` | Yes | Human-readable summary of what was done. Keep under 100 words. |
| `change_overview` | `list[str]` | Yes | One string per file or function changed. |
| `risk_level` | `str` | Yes | Exactly one of: `"Low"`, `"Medium"`, `"High"`. |
| `risk_note` | `str` | Yes | One sentence on side effects. |
| `next_steps` | `list[str]` | Yes | 1â€“2 strings describing what the developer should do next. |
| `files_changed` | `list[str]` | Optional | List of file paths modified. |
| `annotation_excerpt` | `str` | Optional | First ~8 words of the annotation comment, for display. |
| `first_seen_version` | `str` | Optional | Version when this annotation was first created. |
| `notes` | `str` | Optional | Additional freeform notes. |

**Processing logic:**
1. The file is read. If `for_app` is present and non-empty, it must exactly match the app slug or the file is skipped. If `for_app` is absent or empty, it matches any app (wildcard â€” always compute and include `for_app` to prevent cross-app processing).
2. For each entry in `attempts`, `append_attempt()` is called with all fields.
3. If at least one attempt succeeds, the file is renamed to `_pending_actions.processed`.
4. If an entry has an invalid `status`, it is silently skipped (exception swallowed). Other entries in the same file are still processed.
5. `action_log_path` must be non-None for processing to occur. If the manager has no action log path, the file is found but not processed (returns `False`).

---

## 10. Action Log â€” append_attempt()

### Signature

```python
append_attempt(
    annotation_index,     # int â€” required positional
    json_path=None,       # Optional[Path] â€” path to the JSON log file
    *,
    version,              # str â€” required keyword
    status,               # str â€” required keyword; one of the five valid values
    description="",       # str
    change_overview=None, # Optional[list[str]]
    risk_level="Low",     # str â€” one of "Low", "Medium", "High"
    risk_note="",         # str
    next_steps=None,      # Optional[list[str]]
    summary="",           # str â€” legacy fallback; migrated to description if description is empty
    files_changed=None,   # Optional[list[str]]
    notes="",             # str
    annotation_excerpt="",  # str
    first_seen_version="",  # str
) -> None
```

### Required fields

`annotation_index`, `version`, and `status` are the only fields that will cause a failure if wrong:
- `annotation_index` must be castable to `int`.
- `status` must be in `{"shipped", "no_change_needed", "partial", "blocked", "wontfix"}`; any other value raises `ValueError`.

All other fields have safe defaults and are optional.

### What it writes

Each call appends one entry to the `"attempts"` list under the matching `annotation_index` in the JSON file, then atomically rewrites the file (write to `.json.tmp`, then `replace()`). It also regenerates the `.md` companion file. The JSON structure written is:

```json
{
    "schema_version": 2,
    "last_updated_version": "<version>",
    "actions": [
        {
            "annotation_index":   42,
            "annotation_excerpt": "...",
            "first_seen_version": "...",
            "attempts": [
                {
                    "version":         "1.2.0",
                    "timestamp":       "2026-01-15T10:30:00+00:00",
                    "status":          "shipped",
                    "description":     "...",
                    "change_overview": [...],
                    "risk_level":      "Low",
                    "risk_note":       "...",
                    "next_steps":      [...],
                    "files_changed":   [...],
                    "notes":           ""
                }
            ]
        }
    ]
}
```

### load_actions()

```python
load_actions(json_path=None) -> dict[int, list[dict]]
```

Returns `{annotation_index: [attempt_dict, ...]}`. Returns `{}` if the file is missing or malformed. Tolerates both string and int keys for `annotation_index`. Only returns entries where at least one attempt has a `"status"` field.

---

## 11. QSettings Keys Used Internally

The library uses `QSettings("pyside6_annotator", "annotator")` (organization = `"pyside6_annotator"`, application = `"annotator"`). Do not use the same organization+application combination in a new app.

Keys written:

| Key | Type | Purpose |
|-----|------|---------|
| `floating_bar/edge_y` | `int` | Y position of the floating bar on screen |
| `floating_bar/<settings_key>/edge_y` | `int` | Per-app Y position when `settings_key` is supplied |
| `annotation/utc_offset_hours` | `float` | User-configured UTC offset for annotation timestamps |
| `review/archive_expanded` | `bool` | Whether the Archive section is expanded in the Review dialog |

If the new app also uses QSettings, use a different organization+application name to avoid reading stale values from these keys.

---

## 12. Threading Constraints

**All calls into the pyside6_annotator API must be made from the Qt main thread.** The library:

- Installs and removes event filters on `QApplication.instance()` from `start()` and `stop()`.
- Creates and manipulates Qt widgets (`_HighlightFrame`, `_Badge`, `_Popover`, `_ReviewDialog`) in response to user input events.
- Calls `QTimer.singleShot()` for deferred work.
- Reads/writes files (`annotations.json`, action log JSON) synchronously on the main thread. For large `data_dir` paths (e.g. network drives), this can cause brief UI stalls during `_load_annotations()` at startup and `_save_annotations()` after each annotation. There is no built-in async I/O â€” keep `data_dir` on a local drive.

`_process_pending_actions()` runs via `QTimer.singleShot(0, ...)` so it executes on the main thread after the event loop starts. Do not call it manually from a background thread.

---

## 13. Debug Flag

Set the environment variable `ANNOTATOR_DEBUG=1` before launching the app:

```bash
ANNOTATOR_DEBUG=1 python main.py
```

This enables `_jump_debug()` output, which prints timestamped diagnostic lines to `stderr` during "Jump" operations in the Review dialog. Lines are prefixed `[JUMP HH:MM:SS.mmm]` and include:

- The annotation index, tag, and text snippet being searched for.
- The current screen before navigation.
- Whether the widget was found immediately or required navigation.
- The inferred `tab_hint_map` tab index.
- Whether `navigate_to` returned truthy.
- After the deferred retry, the result of `_find_annotated_widget`.
- All visible widgets of the matching class type if the search fails.

This flag has no effect on any other part of the library.

---

## 14. Known Failure Modes and Gotchas

### 14.1 data_dir does not exist at construction time

`_load_annotations()` silently no-ops if the file does not exist. `_save_annotations()` **does not raise** â€” it wraps all file I/O in `except Exception: pass` and silently discards errors. Data loss occurs without any signal to the caller. Always call `data_dir.mkdir(parents=True, exist_ok=True)` before constructing `AnnotationManager`; there is no exception to catch if you forget.

### 14.2 app_name slug mismatch blocks _pending_actions.json processing

The `for_app` field in `_pending_actions.json` is matched against `re.sub(r"[^\w\-]", "_", app_name.lower()).strip("_")`. If `app_name` changes between app versions or has subtle differences (e.g. extra spaces, different capitalisation), the file will be silently ignored. Always verify the slug by computing it manually before writing `_pending_actions.json`.

### 14.3 action_log_path=None disables _pending_actions.json processing

If `action_log_path` is `None` (which happens on non-Windows platforms when the OneDrive auto-path does not exist), `_try_file()` returns `False` immediately without processing any attempts. To ensure cross-platform action logging, always supply an explicit `action_log_path`.

### 14.4 FloatingAnnotationBar with non-None parent in QStackedWidget apps

Setting `parent` to any widget inside a `QStackedWidget` causes the bar to be destroyed when the stack page changes (Qt parent-child lifecycle). The bar will vanish from the screen. Always use `parent=None`.

### 14.5 Duplicate get_annotation_manager export in __all__

`__init__.py` lists `get_annotation_manager` twice in `__all__` (lines 78â€“79). This is a harmless duplicate but is a known issue in the current source.

### 14.6 Self-annotation log path hardcoded to OneDrive

`_build_self_manager()` (inside `FloatingAnnotationBar`) hardcodes the self-annotation log to `~/OneDrive/Desktop/Annotation logs/annotation_bar.json`. On non-Windows or machines without OneDrive, this path creation fails silently (`OSError` is caught) and `_bar_log` is set to `None`, disabling bar action logging. This does not affect the primary app's action log.

### 14.7 Annotations saved with index starting from current counter

The `Annotation.index` is assigned from `self._counter` which starts at 0 and increments. If `annotations.json` exists and is loaded, `_counter` is set to `max(ann.index for ann in annotations)` (the maximum index seen, not `max + 1`). The `+1` happens lazily at annotation-creation time (`self._counter += 1; idx = self._counter`), so the first new annotation correctly gets `max + 1`. If the file is deleted and recreated, index numbering restarts from 0, causing mismatches with any existing action log entries that reference old indices.

### 14.8 No explicit signal for new annotation created

`AnnotationManager` does not emit a Qt signal when a new annotation is saved. If the host app needs to react to new annotations (e.g. update a status bar badge), it must subclass `AnnotationManager` and override `_save_annotations()`, or poll `annotation_count()` (all annotations including resolved) or `open_count()` (unresolved only). Use `open_count()` for badge/notification purposes.

### 14.9 QApplication must exist before constructing either class

Both `AnnotationManager` and `FloatingAnnotationBar` call `QApplication.instance()` during `__init__`. Constructing either before `QApplication` is created will cause a crash.

### 14.10 Absent for_app in _pending_actions.json is a wildcard

The matching guard is `if for_app and for_app != safe_name: return False`. When the `for_app` field is absent or an empty string, the condition short-circuits and the file is **processed by any app**. Always include an explicit `for_app` slug in every `_pending_actions.json` to prevent accidental cross-app processing.

### 14.11 MODE_MULTI_DRAG annotations pre-v1.0.33

Annotations created in `MODE_MULTI_DRAG` before library version 1.0.33 store `text_snippet` as a count string (e.g. `"3 elements"`) rather than real widget text. Jump-to detects this with a regex and shows "Not available" instead of attempting a search. These annotations cannot be jumped to and should be re-created.

---

## 15. Additional Public API (Programmatic Control)

These methods exist on `AnnotationManager` and are useful when integrating without a `FloatingAnnotationBar`, or when the host app needs to control annotation mode programmatically.

```python
manager.start(mode=MODE_SINGLE)   # enter annotation mode; mode is one of the MODE_* constants
manager.stop()                     # exit annotation mode
manager.toggle(mode=MODE_SINGLE)  # toggle between active and inactive
manager.is_active() -> bool        # True while annotation mode is active
manager.current_mode() -> str      # returns the current MODE_* string
manager.annotation_count() -> int  # total annotation count (including resolved)
manager.open_count() -> int        # unresolved annotation count (use for badges)
manager.export_report()            # open the export dialog
manager.open_review()              # open the Review dialog
```

`get_annotation_manager()` (also exported as `get_active_manager`) returns the most recently constructed primary `AnnotationManager` instance, or `None` if none exists. Useful in nested code that does not have a direct reference to the manager.

---

## 16. Maintaining Long-Term Compatibility

### Run the test suite on every library upgrade

`test_integration_compatibility.py` (in this repo) tests the contracts between the host app and the library â€” not the library's internals. Run it after every `pyside6_annotator` version bump before shipping:

```bash
pip install pyside6_annotator --upgrade
pytest test_integration_compatibility.py -v
```

A failing test identifies the exact broken contract (constructor signature changed, attribute renamed, status value added, `for_app` matching logic changed, etc.) before it reaches production.

### What the tests will catch automatically

- `AnnotationManager` / `FloatingAnnotationBar` constructor signature changes
- Private attribute renames (`_app_name`, `_navigate_to_cb`, `_toggle_btn`, etc.)
- `append_attempt()` valid status values changing
- `load_actions()` return format changing
- `_pending_actions.json` processing behaviour (rename logic, `for_app` matching)
- `FloatingAnnotationBar` QSettings key renames

### 16.2 What requires manual checking

- Visual regressions in the bar or Review dialog UI
- New library features worth adopting (check the changelog when bumping the pin)
- `for_app` slug consistency â€” if the app is ever renamed, the slug changes and all existing `_pending_actions.json` files will be silently ignored unless updated

### 16.3 Recommended CI setup

Pin the library version in `requirements.txt` or `pyproject.toml` and gate on the test suite whenever the pin is bumped:

```toml
# pyproject.toml
[project]
dependencies = [
    "pyside6_annotator==1.4.23",  # bump deliberately; run test suite after each bump
]
```

```yaml
# CI step (GitHub Actions example)
- name: Verify annotator integration
  run: pytest test_integration_compatibility.py -v
```

---

## 17. Common Integration Bugs & Fixes

### 17.1 Mass-Minting on Startup

**The Problem**: `AnnotationManager` triggers `_save_annotation` (or your overridden version) for every record it loads from `annotations.json` during startup. If your override spawns an external process (like a browser helper or autonomous worker), you will mass-mint a new process for every existing annotation every time the app launches.

**The Fix**: Implement a readiness flag. Initialize it to `False` and use a `QTimer` to set it to `True` after the initial load burst is finished.

```python
class MyManager(AnnotationManager):
    def _save_annotation(self, widget, index, comment, **kwargs):
        super()._save_annotation(widget, index, comment, **kwargs)
        if not getattr(self, "_ready", False):
            return  # skip mass-minting during startup load
        # ... spawn your worker here ...

# In setup code:
mgr = MyManager(...)
mgr._ready = False
QtCore.QTimer.singleShot(1000, lambda: setattr(mgr, "_ready", True))
```

### 17.2 Action Log Overflow (Duplicates)

**The Problem**: If your external worker is retried or if the mass-minting bug above occurred, the action log JSON can end up with multiple identical `ai_action` entries for the same annotation index, making the thread unreadable.

**The Fix**: Implement a self-healing deduplication pass at startup. Before constructing the `AnnotationManager`, read the `action_log_path` JSON, strip duplicate attempts (same description/changes) for each index, and write it back.

### 17.3 Wrong Annotation Indexing

**The Problem**: When overriding `_save_annotation`, do not rely on `self._annotations[-1]`. If the library is bulk-saving or if a race condition occurs, the last element in the list may not be the one currently being processed.

**The Fix**: Always use the `index` parameter provided to the method: `ann = self._annotations[index]`.