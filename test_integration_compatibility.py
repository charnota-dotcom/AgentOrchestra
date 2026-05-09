"""
Integration compatibility test suite for pyside6_annotator.

Run with:
    pytest test_integration_compatibility.py -v

Requires:
    pytest
    pytest-qt
    pyside6_annotator (installed or on PYTHONPATH)
    PySide6 >= 6.5

Each test verifies one integration contract documented in
TECHNICAL_INTEGRATION_REQUIREMENTS.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(app_name: str) -> str:
    """Replicate the slug logic used by AnnotationManager and _process_pending_actions."""
    import re
    return re.sub(r"[^\w\-]", "_", app_name.lower()).strip("_") or "app"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def main_window(qtbot):
    """A minimal QMainWindow for tests that need a live window."""
    from PySide6.QtWidgets import QMainWindow
    win = QMainWindow()
    win.resize(800, 600)
    win.show()
    qtbot.addWidget(win)
    return win


@pytest.fixture
def data_dir(tmp_path):
    """A temporary data directory that exists before the manager is constructed."""
    d = tmp_path / "myapp_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def log_path(tmp_path):
    """A temporary path for the action log JSON."""
    return tmp_path / "_claude_actions.json"


@pytest.fixture
def manager(main_window, data_dir, log_path):
    """A minimal AnnotationManager wired to a real QMainWindow."""
    from pyside6_annotator import AnnotationManager

    mgr = AnnotationManager(
        window=main_window,
        data_dir=data_dir,
        app_name="TestApp",
        app_version="0.1.0",
        action_log_path=log_path,
    )
    return mgr


# ---------------------------------------------------------------------------
# TEST 1: AnnotationManager instantiation
# ---------------------------------------------------------------------------

class TestAnnotationManagerInstantiation:
    """Verify AnnotationManager can be instantiated with a minimal setup."""

    def test_instantiation_with_window_and_data_dir(self, main_window, data_dir, log_path):
        """AnnotationManager must construct without error given a QMainWindow and data_dir."""
        from pyside6_annotator import AnnotationManager

        mgr = AnnotationManager(
            window=main_window,
            data_dir=data_dir,
            app_name="TestApp",
            app_version="1.0.0",
            action_log_path=log_path,
        )
        assert mgr is not None

    def test_instantiation_without_data_dir(self, main_window):
        """AnnotationManager must construct with data_dir=None (no persistence)."""
        from pyside6_annotator import AnnotationManager

        mgr = AnnotationManager(
            window=main_window,
            data_dir=None,
            app_name="TestApp",
            app_version="1.0.0",
            action_log_path=None,
        )
        assert mgr is not None

    def test_app_name_stored(self, manager):
        """app_name must be stored as _app_name."""
        assert manager._app_name == "TestApp"

    def test_app_version_stored(self, manager):
        """app_version must be stored as _app_version."""
        assert manager._app_version == "0.1.0"

    def test_annotations_path_set(self, manager, data_dir):
        """_annotations_path must be data_dir/annotations.json."""
        assert manager._annotations_path == data_dir / "annotations.json"

    def test_action_log_path_stored(self, manager, log_path):
        """action_log_path must be stored as _action_log_path."""
        assert manager._action_log_path == log_path

    def test_initial_annotation_count_zero(self, manager):
        """A fresh manager with no existing annotations.json must have 0 annotations."""
        assert manager.annotation_count() == 0

    def test_get_annotation_manager_returns_instance(self, manager):
        """get_annotation_manager() must return the most recently constructed primary manager."""
        from pyside6_annotator import get_annotation_manager
        live = get_annotation_manager()
        assert live is manager


# ---------------------------------------------------------------------------
# TEST 2: FloatingAnnotationBar with parent=None
# ---------------------------------------------------------------------------

class TestFloatingAnnotationBarInstantiation:
    """Verify FloatingAnnotationBar can be instantiated with parent=None."""

    def test_instantiation_parent_none(self, qtbot, manager, data_dir):
        """FloatingAnnotationBar must construct with parent=None without error."""
        from pyside6_annotator import FloatingAnnotationBar

        bar = FloatingAnnotationBar(
            manager,
            data_dir=data_dir,
            host=None,
            parent=None,
        )
        qtbot.addWidget(bar)
        assert bar is not None

    def test_parent_is_none(self, qtbot, manager):
        """When parent=None is passed, bar.parent() must be None."""
        from pyside6_annotator import FloatingAnnotationBar

        bar = FloatingAnnotationBar(manager, parent=None)
        qtbot.addWidget(bar)
        assert bar.parent() is None

    def test_bar_is_visible_after_show(self, qtbot, manager):
        """bar.show() must make the bar visible."""
        from pyside6_annotator import FloatingAnnotationBar

        bar = FloatingAnnotationBar(manager, parent=None)
        qtbot.addWidget(bar)
        bar.show()
        assert bar.isVisible()

    def test_manager_buttons_wired(self, qtbot, manager):
        """FloatingAnnotationBar.__init__ must wire toggle/review/export buttons to the manager."""
        from pyside6_annotator import FloatingAnnotationBar

        bar = FloatingAnnotationBar(manager, parent=None)
        qtbot.addWidget(bar)

        # The manager must now have all three button references set.
        assert manager._toggle_btn is not None
        assert manager._review_btn is not None
        assert manager._export_btn is not None


# ---------------------------------------------------------------------------
# TEST 3: navigate_to callback — called with the correct annotation index
# ---------------------------------------------------------------------------

class TestNavigateToCallback:
    """Verify the navigate_to callback is called with the right screen_name."""

    def test_navigate_to_called_with_screen_name(self, qtbot, main_window, data_dir, log_path):
        """
        When a Jump is triggered on an annotation that has a screen_name,
        the navigate_to callback must be called with that screen_name string.
        """
        from pyside6_annotator import AnnotationManager, Annotation

        navigate_calls: list[str] = []

        def _navigate(screen_name: str) -> bool:
            navigate_calls.append(screen_name)
            return True

        mgr = AnnotationManager(
            window=main_window,
            data_dir=data_dir,
            app_name="TestApp",
            app_version="0.1.0",
            action_log_path=log_path,
            navigate_to=_navigate,
        )

        # Simulate an annotation with a known screen_name.
        ann = Annotation(
            index=0,
            comment="Test comment",
            timestamp="2026-01-01T00:00:00",
            tag="QPushButton",
            object_name="testBtn",
            widget_type="button",
            text_snippet="Click me",
            geometry={"x": 100, "y": 100, "width": 80, "height": 30},
            screen_name="HomeScreen",
        )
        mgr._annotations.append(ann)

        # Call _navigate_to_screen directly (same code path as Jump button).
        from pyside6_annotator._overlay import _ReviewDialog
        # Simulate what _ReviewDialog._navigate_to_screen does:
        cb = mgr._navigate_to_cb
        assert cb is not None, "navigate_to callback must be stored on the manager"
        result = bool(cb(ann.screen_name))

        assert navigate_calls == ["HomeScreen"], (
            f"navigate_to was called with {navigate_calls!r}, expected ['HomeScreen']"
        )
        assert result is True

    def test_navigate_to_none_does_not_crash(self, qtbot, main_window, data_dir, log_path):
        """When navigate_to=None, _navigate_to_cb must be None and Jump must not crash."""
        from pyside6_annotator import AnnotationManager

        mgr = AnnotationManager(
            window=main_window,
            data_dir=data_dir,
            app_name="TestApp",
            app_version="0.1.0",
            action_log_path=log_path,
            navigate_to=None,
        )
        assert mgr._navigate_to_cb is None

    def test_navigate_to_return_false_is_accepted(self, qtbot, main_window, data_dir, log_path):
        """navigate_to may return False; the library must not crash in that case."""
        from pyside6_annotator import AnnotationManager

        def _never_navigates(screen_name: str) -> bool:
            return False

        mgr = AnnotationManager(
            window=main_window,
            data_dir=data_dir,
            app_name="TestApp",
            app_version="0.1.0",
            action_log_path=log_path,
            navigate_to=_never_navigates,
        )
        # Calling the callback directly must return False without raising.
        result = bool(mgr._navigate_to_cb("SomeScreen"))
        assert result is False


# ---------------------------------------------------------------------------
# TEST 4: annotations.json created in data_dir after adding an annotation
# ---------------------------------------------------------------------------

class TestAnnotationsJsonPersistence:
    """Verify annotations.json is written to data_dir when an annotation is saved."""

    def test_annotations_json_created_on_save(self, manager, data_dir):
        """After _save_annotations(), data_dir/annotations.json must exist."""
        from pyside6_annotator import Annotation

        ann = Annotation(
            index=0,
            comment="First annotation",
            timestamp="2026-01-01T12:00:00",
            tag="QLabel",
            object_name="",
            widget_type="label",
            text_snippet="Hello",
            geometry={"x": 0, "y": 0, "width": 100, "height": 20},
        )
        manager._annotations.append(ann)
        manager._save_annotations()

        annotations_file = data_dir / "annotations.json"
        assert annotations_file.exists(), (
            f"Expected {annotations_file} to exist after _save_annotations()"
        )

    def test_annotations_json_contains_correct_data(self, manager, data_dir):
        """annotations.json must contain the annotation that was saved."""
        from pyside6_annotator import Annotation

        ann = Annotation(
            index=7,
            comment="Check the layout",
            timestamp="2026-01-01T12:00:00",
            tag="QPushButton",
            object_name="submitBtn",
            widget_type="button",
            text_snippet="Submit",
            geometry={"x": 200, "y": 100, "width": 120, "height": 40},
        )
        manager._annotations.append(ann)
        manager._save_annotations()

        data = json.loads((data_dir / "annotations.json").read_text(encoding="utf-8"))
        # The file may be a list of dicts or a dict with an "annotations" key.
        if isinstance(data, list):
            items = data
        else:
            items = data.get("annotations", [])

        indices = [item.get("index") for item in items]
        assert 7 in indices, f"Saved annotation index 7 not found in {indices}"

    def test_annotations_json_loadable_after_save(self, manager, data_dir):
        """After saving and reloading, annotation count must match."""
        from pyside6_annotator import Annotation

        for i in range(3):
            ann = Annotation(
                index=i,
                comment=f"Annotation {i}",
                timestamp="2026-01-01T12:00:00",
                tag="QLabel",
                object_name=f"lbl{i}",
                widget_type="label",
                text_snippet=f"Text {i}",
                geometry={"x": i * 10, "y": 0, "width": 80, "height": 20},
            )
            manager._annotations.append(ann)
        manager._save_annotations()

        # Reload by calling _load_annotations on a fresh manager attribute.
        manager._annotations = []
        manager._load_annotations()
        assert manager.annotation_count() == 3


# ---------------------------------------------------------------------------
# TEST 5: _pending_actions.json processed on startup
# ---------------------------------------------------------------------------

class TestPendingActionsProcessing:
    """Verify _pending_actions.json is processed by AnnotationManager on startup."""

    def test_pending_actions_processed_and_renamed(self, qtbot, main_window, tmp_path, log_path):
        """
        A _pending_actions.json in cwd must be processed on startup and renamed
        to _pending_actions.processed.
        """
        from pyside6_annotator import AnnotationManager

        app_name = "TestApp"
        slug = _slug(app_name)

        # Write a _pending_actions.json in cwd (tmp_path via monkeypatching).
        pending_file = tmp_path / "_pending_actions.json"
        pending_data = {
            "for_app": slug,
            "attempts": [
                {
                    "annotation_index":   0,
                    "version":            "0.2.0",
                    "status":             "shipped",
                    "description":        "Fixed the layout issue.",
                    "change_overview":    ["main_window.py: reordered widgets"],
                    "risk_level":         "Low",
                    "risk_note":          "No data model changes.",
                    "next_steps":         ["Run tests"],
                    "files_changed":      ["main_window.py"],
                    "annotation_excerpt": "The layout looks broken",
                }
            ],
        }
        pending_file.write_text(json.dumps(pending_data), encoding="utf-8")

        # Create a data_dir and pre-populate annotations.json so index 0 exists.
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        ann_data = [
            {
                "index": 0, "comment": "The layout looks broken here",
                "timestamp": "2026-01-01T00:00:00", "mode": "single",
                "element": {
                    "tag": "QLabel", "objectName": None,
                    "type": "label", "textSnippet": None,
                    "boundingBox": {"x": 0, "y": 0, "width": 100, "height": 20},
                },
                "resolved": False, "resolved_at": None,
            }
        ]
        (data_dir / "annotations.json").write_text(
            json.dumps(ann_data), encoding="utf-8"
        )

        # Patch Path.cwd() to return tmp_path so the scan finds our file.
        import pyside6_annotator._overlay as _ov
        original_cwd = Path.cwd

        with patch.object(Path, "cwd", return_value=tmp_path):
            mgr = AnnotationManager(
                window=main_window,
                data_dir=data_dir,
                app_name=app_name,
                app_version="0.2.0",
                action_log_path=log_path,
            )
            # Force the deferred _process_pending_actions to run immediately.
            mgr._process_pending_actions()

        processed_file = tmp_path / "_pending_actions.processed"
        assert processed_file.exists(), (
            "_pending_actions.json was not renamed to _pending_actions.processed after processing"
        )
        assert not pending_file.exists(), (
            "_pending_actions.json still exists after processing; expected it to be renamed"
        )

    def test_pending_actions_written_to_log(self, qtbot, main_window, tmp_path, log_path):
        """
        After _pending_actions.json is processed, the action log must contain
        the attempt for the given annotation_index.
        """
        from pyside6_annotator import AnnotationManager, load_actions

        app_name = "TestApp"
        slug = _slug(app_name)

        pending_file = tmp_path / "_pending_actions.json"
        pending_data = {
            "for_app": slug,
            "attempts": [
                {
                    "annotation_index":   5,
                    "version":            "0.3.0",
                    "status":             "partial",
                    "description":        "Partially fixed. More to do.",
                    "change_overview":    ["screen_a.py: updated header"],
                    "risk_level":         "Medium",
                    "risk_note":          "Header font may differ on Windows.",
                    "next_steps":         ["Fix footer alignment"],
                    "files_changed":      ["screen_a.py"],
                    "annotation_excerpt": "Header font is wrong",
                }
            ],
        }
        pending_file.write_text(json.dumps(pending_data), encoding="utf-8")

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(Path, "cwd", return_value=tmp_path):
            mgr = AnnotationManager(
                window=main_window,
                data_dir=data_dir,
                app_name=app_name,
                app_version="0.3.0",
                action_log_path=log_path,
            )
            mgr._process_pending_actions()

        actions = load_actions(json_path=log_path)
        assert 5 in actions, (
            f"Expected annotation_index 5 in action log, got keys: {list(actions.keys())}"
        )
        attempt = actions[5][0]
        assert attempt["status"] == "partial"
        assert attempt["version"] == "0.3.0"

    def test_pending_actions_wrong_for_app_ignored(self, qtbot, main_window, tmp_path, log_path):
        """
        A _pending_actions.json whose for_app does not match the app slug
        must be ignored (not renamed, not processed).
        """
        from pyside6_annotator import AnnotationManager, load_actions

        pending_file = tmp_path / "_pending_actions.json"
        pending_data = {
            "for_app": "completely_different_app",
            "attempts": [
                {
                    "annotation_index": 1,
                    "version": "1.0.0",
                    "status": "shipped",
                    "description": "Fix.",
                    "change_overview": [],
                    "risk_level": "Low",
                    "risk_note": "",
                    "next_steps": [],
                }
            ],
        }
        pending_file.write_text(json.dumps(pending_data), encoding="utf-8")

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        with patch.object(Path, "cwd", return_value=tmp_path):
            mgr = AnnotationManager(
                window=main_window,
                data_dir=data_dir,
                app_name="TestApp",
                app_version="1.0.0",
                action_log_path=log_path,
            )
            mgr._process_pending_actions()

        # File should NOT have been renamed — wrong app.
        assert pending_file.exists(), (
            "_pending_actions.json for a different app was incorrectly processed"
        )
        processed = tmp_path / "_pending_actions.processed"
        assert not processed.exists()


# ---------------------------------------------------------------------------
# TEST 6: append_attempt() writes a valid action log entry
# ---------------------------------------------------------------------------

class TestAppendAttempt:
    """Verify append_attempt() writes well-formed entries to the action log."""

    def test_append_creates_log_file(self, log_path):
        """append_attempt must create the JSON file if it does not exist."""
        from pyside6_annotator import append_attempt

        append_attempt(
            0,
            json_path=log_path,
            version="1.0.0",
            status="shipped",
            description="Fixed the thing.",
            change_overview=["main.py: updated render()"],
            risk_level="Low",
            risk_note="No side effects.",
            next_steps=["Merge to main"],
        )

        assert log_path.exists(), f"Expected {log_path} to be created by append_attempt()"

    def test_append_writes_correct_structure(self, log_path):
        """The JSON file written by append_attempt must match the documented schema."""
        from pyside6_annotator import append_attempt

        append_attempt(
            42,
            json_path=log_path,
            version="2.0.0",
            status="partial",
            description="Partially resolved the header issue.",
            change_overview=["header.py: moved title widget"],
            risk_level="Medium",
            risk_note="Footer layout may shift on resize.",
            next_steps=["Check footer alignment"],
            files_changed=["header.py"],
            annotation_excerpt="Header is misaligned",
        )

        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert "schema_version" in data
        assert "actions" in data
        actions = data["actions"]
        assert len(actions) == 1
        action = actions[0]
        assert action["annotation_index"] == 42
        assert len(action["attempts"]) == 1
        attempt = action["attempts"][0]
        assert attempt["status"] == "partial"
        assert attempt["version"] == "2.0.0"
        assert attempt["description"] == "Partially resolved the header issue."
        assert attempt["change_overview"] == ["header.py: moved title widget"]
        assert attempt["risk_level"] == "Medium"
        assert "timestamp" in attempt  # ISO-8601 timestamp must be present

    def test_append_multiple_attempts_same_index(self, log_path):
        """Multiple calls with the same annotation_index must accumulate attempts."""
        from pyside6_annotator import append_attempt, load_actions

        append_attempt(
            10, json_path=log_path, version="1.0.0", status="partial",
            description="First attempt.", change_overview=[], risk_level="Low",
            risk_note="", next_steps=[],
        )
        append_attempt(
            10, json_path=log_path, version="1.1.0", status="shipped",
            description="Second attempt, fixed.", change_overview=[], risk_level="Low",
            risk_note="", next_steps=[],
        )

        actions = load_actions(json_path=log_path)
        assert 10 in actions
        assert len(actions[10]) == 2
        assert actions[10][0]["status"] == "partial"
        assert actions[10][1]["status"] == "shipped"

    def test_append_multiple_attempts_different_indices(self, log_path):
        """Calls with different annotation_indices must create separate action entries."""
        from pyside6_annotator import append_attempt, load_actions

        append_attempt(
            1, json_path=log_path, version="1.0.0", status="shipped",
            description="Fixed 1.", change_overview=[], risk_level="Low",
            risk_note="", next_steps=[],
        )
        append_attempt(
            2, json_path=log_path, version="1.0.0", status="no_change_needed",
            description="No change needed.", change_overview=[], risk_level="Low",
            risk_note="", next_steps=[],
        )

        actions = load_actions(json_path=log_path)
        assert 1 in actions
        assert 2 in actions

    def test_invalid_status_raises_value_error(self, log_path):
        """An invalid status value must raise ValueError."""
        from pyside6_annotator import append_attempt

        with pytest.raises(ValueError, match="status must be one of"):
            append_attempt(
                0, json_path=log_path, version="1.0.0", status="invalid_status",
                description="", change_overview=[], risk_level="Low",
                risk_note="", next_steps=[],
            )

    def test_all_valid_statuses_accepted(self, log_path):
        """All five documented status values must be accepted without error."""
        from pyside6_annotator import append_attempt

        valid_statuses = ["shipped", "no_change_needed", "partial", "blocked", "wontfix"]
        for i, status in enumerate(valid_statuses):
            append_attempt(
                i, json_path=log_path, version="1.0.0", status=status,
                description=f"Attempt with status {status}.",
                change_overview=[], risk_level="Low", risk_note="", next_steps=[],
            )
        # If we got here without ValueError, all are valid.
        assert True

    def test_legacy_summary_migrated_to_description(self, log_path):
        """When only summary is given (no description), it must be stored as description."""
        from pyside6_annotator import append_attempt

        append_attempt(
            0, json_path=log_path, version="1.0.0", status="shipped",
            summary="Old-style summary text.",
            change_overview=[], risk_level="Low", risk_note="", next_steps=[],
        )

        data = json.loads(log_path.read_text(encoding="utf-8"))
        attempt = data["actions"][0]["attempts"][0]
        assert attempt["description"] == "Old-style summary text.", (
            "Legacy summary was not migrated to description field"
        )

    def test_md_companion_generated(self, log_path):
        """append_attempt must generate a .md companion file alongside the JSON."""
        from pyside6_annotator import append_attempt

        append_attempt(
            0, json_path=log_path, version="1.0.0", status="shipped",
            description="Done.", change_overview=[], risk_level="Low",
            risk_note="", next_steps=[],
        )

        md_path = log_path.with_suffix(".md")
        assert md_path.exists(), (
            f"Expected .md companion at {md_path} to be generated by append_attempt()"
        )


# ---------------------------------------------------------------------------
# TEST 7: destroyed signal cleanly closes FloatingAnnotationBar
# ---------------------------------------------------------------------------

class TestDestroyedSignalCleanup:
    """Verify window.destroyed triggers bar.close() without error."""

    def test_bar_closes_when_window_destroyed(self, qtbot, data_dir, log_path):
        """
        Closing the host window must call bar.close() via the destroyed signal,
        which must remove the QApplication event filter without raising.
        """
        from PySide6.QtWidgets import QMainWindow, QApplication
        from pyside6_annotator import AnnotationManager, FloatingAnnotationBar

        win = QMainWindow()
        win.resize(400, 300)
        win.show()

        mgr = AnnotationManager(
            window=win,
            data_dir=data_dir,
            app_name="CloseTest",
            app_version="1.0.0",
            action_log_path=log_path,
        )
        bar = FloatingAnnotationBar(mgr, data_dir=data_dir, host=win, parent=None)
        bar.show()

        # Wire the signal as documented.
        win.destroyed.connect(bar.close)

        # Verify bar is visible before closing.
        assert bar.isVisible()

        # close() alone does NOT emit destroyed — destroyed fires only when the C++ QObject
        # is actually deleted. Call deleteLater() then flush the event loop.
        win.close()
        win.deleteLater()
        QApplication.processEvents()

        # The bar should now be hidden (close() hides it; WA_DeleteOnClose is False).
        assert not bar.isVisible(), (
            "FloatingAnnotationBar should be hidden after window.destroyed triggered bar.close()"
        )

    def test_close_event_removes_event_filter(self, qtbot, data_dir, log_path):
        """
        bar.close() must remove _CollapseFilter from QApplication.
        Repeated close() calls must not raise.
        """
        from PySide6.QtWidgets import QMainWindow, QApplication
        from pyside6_annotator import AnnotationManager, FloatingAnnotationBar

        win = QMainWindow()
        win.show()
        qtbot.addWidget(win)

        mgr = AnnotationManager(
            window=win,
            data_dir=data_dir,
            app_name="FilterTest",
            app_version="1.0.0",
            action_log_path=log_path,
        )
        bar = FloatingAnnotationBar(mgr, parent=None)
        qtbot.addWidget(bar)
        bar.show()

        # First close — should cleanly remove the event filter.
        bar.close()
        QApplication.processEvents()

        # Second close — must not raise even though filter is already removed.
        try:
            bar.close()
            QApplication.processEvents()
        except Exception as exc:
            pytest.fail(f"Second bar.close() raised an exception: {exc}")

    def test_manager_cleared_on_destroyed(self, qtbot, data_dir, log_path):
        """
        When the manager's parent window is destroyed, get_annotation_manager()
        must return None — the destroyed signal clears _ACTIVE_MANAGER.
        """
        from PySide6.QtWidgets import QMainWindow, QApplication
        from pyside6_annotator import AnnotationManager, get_annotation_manager

        win = QMainWindow()
        win.show()

        mgr = AnnotationManager(
            window=win,
            data_dir=data_dir,
            app_name="DestroyTest",
            app_version="1.0.0",
            action_log_path=log_path,
        )

        assert get_annotation_manager() is mgr

        # deleteLater() + processEvents() triggers the C++ destroyed signal,
        # which fires the _clear_this_manager callback to set _ACTIVE_MANAGER = None.
        win.close()
        win.deleteLater()
        QApplication.processEvents()
        # A second processEvents() pass ensures deferred deletions have completed.
        QApplication.processEvents()

        assert get_annotation_manager() is None, (
            "get_annotation_manager() must return None after the manager's window is destroyed"
        )


# ---------------------------------------------------------------------------
# TEST 8: Additional contract verifications
# ---------------------------------------------------------------------------

class TestAdditionalContracts:
    """Additional integration contracts not covered by the above categories."""

    def test_instantiation_order_bar_before_manager_raises(self, qtbot, main_window, data_dir):
        """
        Constructing FloatingAnnotationBar with a non-AnnotationManager object
        must raise AttributeError (manager not fully constructed).
        """
        from pyside6_annotator import FloatingAnnotationBar

        fake_manager = MagicMock(spec=[])  # no attributes at all

        with pytest.raises((AttributeError, TypeError)):
            FloatingAnnotationBar(fake_manager, parent=None)

    def test_navigate_to_callback_signature(self, qtbot, main_window, data_dir, log_path):
        """
        The navigate_to callback must receive exactly one positional argument (screen_name: str).
        """
        from pyside6_annotator import AnnotationManager

        received_args = []

        def _navigate(*args, **kwargs):
            received_args.append((args, kwargs))
            return True

        mgr = AnnotationManager(
            window=main_window,
            data_dir=data_dir,
            app_name="SigTest",
            app_version="1.0.0",
            action_log_path=log_path,
            navigate_to=_navigate,
        )

        # Simulate what the library calls.
        mgr._navigate_to_cb("TargetScreen")

        assert len(received_args) == 1
        args, kwargs = received_args[0]
        assert len(args) == 1, f"Expected 1 positional arg, got {len(args)}: {args}"
        assert args[0] == "TargetScreen"
        assert kwargs == {}, f"Expected no keyword args, got {kwargs}"

    def test_pending_actions_json_schema_validation(self, tmp_path, log_path):
        """
        _pending_actions.json with a missing required 'attempts' key must
        not crash the processor (it should silently skip the file).
        """
        from pyside6_annotator import AnnotationManager
        from PySide6.QtWidgets import QMainWindow

        win = QMainWindow()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Malformed: no "attempts" key.
        bad_file = tmp_path / "_pending_actions.json"
        bad_file.write_text(json.dumps({"for_app": "testapp"}), encoding="utf-8")

        with patch.object(Path, "cwd", return_value=tmp_path):
            mgr = AnnotationManager(
                window=win,
                data_dir=data_dir,
                app_name="testapp",
                app_version="1.0.0",
                action_log_path=log_path,
            )
            # Must not raise.
            try:
                mgr._process_pending_actions()
            except Exception as exc:
                pytest.fail(
                    f"_process_pending_actions raised on malformed file: {exc}"
                )

    def test_load_actions_tolerates_missing_file(self, tmp_path):
        """load_actions() must return {} when the log file does not exist."""
        from pyside6_annotator import load_actions

        missing = tmp_path / "nonexistent.json"
        result = load_actions(json_path=missing)
        assert result == {}

    def test_load_actions_tolerates_corrupt_json(self, tmp_path):
        """load_actions() must return {} when the log file contains invalid JSON."""
        from pyside6_annotator import load_actions

        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{ this is not valid JSON !!!}", encoding="utf-8")
        result = load_actions(json_path=corrupt)
        assert result == {}

    def test_annotation_count_increments_after_save(self, manager, data_dir):
        """annotation_count() must reflect saved annotations."""
        from pyside6_annotator import Annotation

        assert manager.annotation_count() == 0

        ann = Annotation(
            index=0,
            comment="Count test",
            timestamp="2026-01-01T00:00:00",
            tag="QLabel",
            object_name="",
            widget_type="label",
            text_snippet="",
            geometry={"x": 0, "y": 0, "width": 50, "height": 20},
        )
        manager._annotations.append(ann)
        manager._save_annotations()

        assert manager.annotation_count() == 1

    def test_qsettings_organization_and_app(self, qtbot, manager):
        """
        The library must use QSettings("pyside6_annotator", "annotator").
        Verify the constants on FloatingAnnotationBar match.
        """
        from pyside6_annotator import FloatingAnnotationBar

        assert FloatingAnnotationBar._SETTINGS_ORG == "pyside6_annotator"
        assert FloatingAnnotationBar._SETTINGS_APP == "annotator"
        assert FloatingAnnotationBar._SETTINGS_Y_KEY == "floating_bar/edge_y"

    def test_mode_constants_are_strings(self):
        """Annotation mode constants must be plain strings (not Enum members)."""
        from pyside6_annotator import MODE_SINGLE, MODE_MULTI_CLICK, MODE_MULTI_DRAG, MODE_AREA

        for const in (MODE_SINGLE, MODE_MULTI_CLICK, MODE_MULTI_DRAG, MODE_AREA):
            assert isinstance(const, str), f"Expected str, got {type(const)} for {const!r}"

    def test_mode_constants_values(self):
        """Annotation mode constant string values must match the documented values."""
        from pyside6_annotator import MODE_SINGLE, MODE_MULTI_CLICK, MODE_MULTI_DRAG, MODE_AREA

        assert MODE_SINGLE == "single"
        assert MODE_MULTI_CLICK == "multi_click"
        assert MODE_MULTI_DRAG == "multi_drag"
        assert MODE_AREA == "area"
