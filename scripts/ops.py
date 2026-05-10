"""AgentOrchestra Operator Panel.

A tiny PySide6 window that bundles every ``scripts/*.cmd`` as a
clickable button with its summary, "when to run" hint, and a live
output pane.  Reads ``scripts/manifest.json`` so adding a new command
is a one-line manifest edit — no GUI code change needed.

Run via ``ops.cmd`` from this folder, or:

    .venv\\Scripts\\activate.bat
    python scripts\\ops.py

Design notes:

* Uses ``QProcess`` so the GUI stays responsive while a script runs
  (stdout streams in live).  Closing the write-channel after start
  defeats any ``pause`` waiting on stdin.
* No service / RPC / network — this is a pure local launcher, runs
  even if the orchestrator service is offline.
* Single file; no dependencies beyond PySide6 (already installed via
  the project's ``[gui]`` extra).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

SCRIPTS_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPTS_DIR / "manifest.json"


class CommandCard(QtWidgets.QFrame):
    """One row per manifest entry: label + summary + Run button."""

    run_clicked = QtCore.Signal(str)  # script filename

    def __init__(self, cmd: dict[str, object]) -> None:
        super().__init__()
        self.setStyleSheet("QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}")
        self.setObjectName("card")
        self._cmd = cmd

        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(12)

        # Step badge on the left — the manifest's ``step`` field gives
        # the typical first-time-to-running sequence (1=install,
        # 2=verify Claude, 3=verify Gemini, 4=launch, …).  Step 0
        # entries are pinned utilities (Restart, the panel itself);
        # they get a green star instead of a number so they read as
        # "always available" rather than "step in a sequence".
        step = cmd.get("step")
        if isinstance(step, int) and step > 0:
            badge = QtWidgets.QLabel(str(step))
            badge.setFixedSize(28, 28)
            badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                "QLabel{background:#1f6feb;color:#fff;border-radius:14px;"
                "font-weight:700;font-size:13px;border:none;}"
            )
            h.addWidget(badge, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        elif step == 0:
            badge = QtWidgets.QLabel("★")
            badge.setFixedSize(28, 28)
            badge.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                "QLabel{background:#1f7a3f;color:#fff;border-radius:14px;"
                "font-weight:700;font-size:14px;border:none;}"
            )
            h.addWidget(badge, alignment=QtCore.Qt.AlignmentFlag.AlignTop)

        text = QtWidgets.QVBoxLayout()
        text.setSpacing(2)

        title = QtWidgets.QLabel(str(cmd.get("label", "?")))
        title.setStyleSheet("font-size:13px;font-weight:600;color:#0f1115;border:none;")
        text.addWidget(title)

        summary = QtWidgets.QLabel(str(cmd.get("summary", "")))
        summary.setWordWrap(True)
        summary.setStyleSheet("color:#5b6068;font-size:11px;border:none;")
        text.addWidget(summary)

        when = QtWidgets.QLabel("When: " + str(cmd.get("when_to_run", "")))
        when.setWordWrap(True)
        when.setStyleSheet("color:#7a7d85;font-size:10px;font-style:italic;border:none;")
        text.addWidget(when)

        h.addLayout(text, stretch=1)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.setFixedWidth(90)
        self.run_btn.setStyleSheet(
            "QPushButton{padding:8px 12px;background:#1f6feb;color:#fff;"
            "border-radius:4px;font-weight:600;border:none;}"
            "QPushButton:hover{background:#1860d6;}"
            "QPushButton:disabled{background:#aab1bb;}"
        )
        file_name = str(cmd.get("file", ""))
        self.run_btn.clicked.connect(lambda _checked=False, f=file_name: self.run_clicked.emit(f))  # type: ignore[arg-type]
        h.addWidget(self.run_btn, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)


class OpsWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AgentOrchestra — Operator Panel")
        self.resize(720, 720)

        # Single QProcess instance reused across runs.  We don't
        # support concurrent commands — Run buttons get disabled
        # while a command is in flight.
        self.process: QtCore.QProcess | None = None
        self.cards: list[CommandCard] = []

        central = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        title = QtWidgets.QLabel("Operator Panel")
        title.setStyleSheet("font-size:22px;font-weight:600;color:#0f1115;")
        v.addWidget(title)

        subtitle = QtWidgets.QLabel(
            "One click per command. Live output appears below. "
            "All scripts live in ``scripts/`` and are also double-clickable directly."
        )
        subtitle.setStyleSheet("color:#5b6068;font-size:11px;")
        subtitle.setWordWrap(True)
        v.addWidget(subtitle)

        # Card list inside a scroll area so the panel still fits when
        # someone adds a tenth command to the manifest.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        cards_holder = QtWidgets.QWidget()
        cards_v = QtWidgets.QVBoxLayout(cards_holder)
        cards_v.setContentsMargins(0, 0, 0, 0)
        cards_v.setSpacing(8)

        commands = self._load_manifest()
        for cmd in commands:
            card = CommandCard(cmd)
            card.run_clicked.connect(self._run_script)  # type: ignore[arg-type]
            self.cards.append(card)
            cards_v.addWidget(card)
        cards_v.addStretch(1)
        scroll.setWidget(cards_holder)
        v.addWidget(scroll, stretch=1)

        # Output pane.
        v.addWidget(self._small("Output"))
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet(
            "QPlainTextEdit{background:#0f1115;color:#e8e8ea;border:1px solid #1f2024;"
            "border-radius:6px;padding:10px;"
            "font-family:ui-monospace,Consolas,Menlo,monospace;font-size:11px;}"
        )
        self.output.setMinimumHeight(180)
        v.addWidget(self.output)

        bottom = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("Ready")
        self.status.setStyleSheet("color:#5b6068;font-size:11px;")
        bottom.addWidget(self.status, stretch=1)
        clear_btn = QtWidgets.QPushButton("Clear output")
        clear_btn.setStyleSheet(
            "QPushButton{padding:4px 10px;font-size:11px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        clear_btn.clicked.connect(self.output.clear)  # type: ignore[arg-type]
        bottom.addWidget(clear_btn)
        v.addLayout(bottom)

        self.setCentralWidget(central)

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def _load_manifest(self) -> list[dict[str, object]]:
        try:
            data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            return list(data.get("commands", []))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Manifest error", f"Couldn't read {MANIFEST_PATH}:\n{exc}"
            )
            return []

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _run_script(self, script_filename: str) -> None:
        if (
            self.process is not None
            and self.process.state() != QtCore.QProcess.ProcessState.NotRunning
        ):
            QtWidgets.QMessageBox.information(
                self, "Already running", "Wait for the current command to finish."
            )
            return

        path = SCRIPTS_DIR / script_filename
        if not path.is_file():
            self._append(f"[ops] Script not found: {path}\n")
            return

        self._set_buttons_enabled(False)
        self.status.setText(f"Running {script_filename} …")
        self._append(f"\n$ {script_filename}\n")

        proc = QtCore.QProcess(self)
        proc.setProgram("cmd.exe")
        # /c runs and exits — important so any pause / read at the end
        # of the script gets EOF when we close the write channel.
        proc.setArguments(["/c", str(path)])
        proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._drain_stdout)  # type: ignore[arg-type]
        proc.finished.connect(self._on_finished)  # type: ignore[arg-type]
        proc.errorOccurred.connect(self._on_error)  # type: ignore[arg-type]

        self.process = proc
        proc.start()
        # Closing the write channel turns subsequent ``pause`` reads
        # into immediate EOF — otherwise scripts that end with
        # ``pause`` would hang the GUI forever.
        proc.closeWriteChannel()

    def _drain_stdout(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self._append(data)

    def _on_finished(
        self,
        exit_code: int,
        _exit_status: QtCore.QProcess.ExitStatus,
    ) -> None:
        if self.process is not None:
            # Drain anything still buffered.
            self._drain_stdout()
        verdict = "ok" if exit_code == 0 else f"exit {exit_code}"
        self._append(f"[ops] Finished — {verdict}\n")
        self.status.setText(f"Finished ({verdict})")
        self.process = None
        self._set_buttons_enabled(True)

    def _on_error(self, error: QtCore.QProcess.ProcessError) -> None:
        self._append(f"[ops] Process error: {error.name}\n")
        self.status.setText(f"Error: {error.name}")
        self.process = None
        self._set_buttons_enabled(True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _append(self, text: str) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for c in self.cards:
            c.run_btn.setEnabled(enabled)

    @staticmethod
    def _small(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(
            "color:#5b6068;font-size:11px;font-weight:600;"
            "text-transform:uppercase;letter-spacing:0.05em;"
        )
        return lbl


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("AgentOrchestra Ops Panel")
    app.setOrganizationName("AgentOrchestra")
    # Use Qt's default style — the panel is intentionally austere.
    icon_path = SCRIPTS_DIR.parent / "apps" / "gui" / "resources" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    win = OpsWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
