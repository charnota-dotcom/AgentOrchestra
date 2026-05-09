"""Tiny syntax-aware diff viewer.

PySide6 provides QSyntaxHighlighter; we ride it with a unified-diff-
specific subclass that paints + lines green, - lines red, hunk
headers in cyan, and file headers (--- / +++) in muted gray.  Stays
intentionally simple — code-content syntax highlighting (Python,
TypeScript, etc.) inside the diff body is a V5 concern.

The widget exposes ``set_diff(text)`` which the Review page calls
after fetching the DIFF artifact.  When the artifact body is empty
or there's no DIFF artifact, the page falls back to its plain-text
QPlainTextEdit so we don't render a black panel for chat-only runs.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class _DiffHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, document: QtGui.QTextDocument) -> None:
        super().__init__(document)
        self._add = self._fmt("#3fb950", bold=True)
        self._del = self._fmt("#f85149", bold=True)
        self._hunk = self._fmt("#a371f7")
        self._head = self._fmt("#7d8590")
        self._index = self._fmt("#d29922")

    @staticmethod
    def _fmt(color: str, *, bold: bool = False) -> QtGui.QTextCharFormat:
        f = QtGui.QTextCharFormat()
        f.setForeground(QtGui.QColor(color))
        if bold:
            f.setFontWeight(QtGui.QFont.Weight.DemiBold)
        return f

    def highlightBlock(self, text: str) -> None:
        if not text:
            return
        if text.startswith("+++") or text.startswith("---"):
            self.setFormat(0, len(text), self._head)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self._hunk)
        elif text.startswith("index ") or text.startswith("diff --git"):
            self.setFormat(0, len(text), self._index)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self._add)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self._del)


class DiffView(QtWidgets.QPlainTextEdit):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        font = QtGui.QFont("ui-monospace", 12)
        font.setStyleHint(QtGui.QFont.StyleHint.TypeWriter)
        self.setFont(font)
        self.setStyleSheet(
            "QPlainTextEdit{background:#0d1117;color:#dee0e3;"
            "border:1px solid #21262d;border-radius:6px;padding:10px;}"
        )
        self._highlighter = _DiffHighlighter(self.document())

    def set_diff(self, text: str) -> None:
        self.setPlainText(text)
        # Keep cursor at start so the user sees the file header first.
        cursor = self.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor)
        # Ensure horizontal bar appears for very long lines.
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
