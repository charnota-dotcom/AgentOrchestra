"""Limits tab — show what each connected subscription CLI exposes.

Calls ``limits.check`` on the service, which probes each binary
(`claude`, `gemini`) for its public status output and returns a
structured dict.  We render one card per provider with version,
status output (or error), dashboard links, and a note explaining
what's reachable headlessly vs. only via the interactive flow.

Layout:

* Page header + description.
* Refresh button (re-runs the probes).
* Vertical stack of provider cards.

Honesty about headless limits is built into the page copy — we
don't pretend to know remaining-quota numbers we can't query.
"""

from __future__ import annotations

import asyncio
import webbrowser
from typing import TYPE_CHECKING, Any

from PySide6 import QtCore, QtWidgets

if TYPE_CHECKING:
    from apps.gui.ipc.client import RpcClient


def _humanize_bytes(n: int) -> str:
    """Format a byte count for display ('12.3 MB', '728 KB', ...)."""
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n_f = n / 1024
        if n_f < 1024 or unit == "TB":
            return f"{n_f:.1f} {unit}"
        n = int(n_f)
    return f"{n} B"


class LimitsPage(QtWidgets.QWidget):
    # Soft cooldown so the Refresh button can't be hammered.  The
    # CLI status calls take real subprocess time and the operator
    # asked for "once every 5 minutes or on refresh".  We honour
    # that by gating manual refreshes too — not just any auto-poll
    # (we don't have one) but the manual button as well, since
    # mashing it would queue concurrent subprocess spawns.
    _REFRESH_COOLDOWN_SECONDS = 300

    def __init__(self, client: RpcClient) -> None:
        super().__init__()
        self.client = client
        self._last_refresh_at: float | None = None
        self.setStyleSheet("background:#fafbfc;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Limits & subscriptions")
        title.setStyleSheet("font-size:24px;font-weight:600;color:#0f1115;")
        header.addWidget(title, stretch=1)
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(
            "QPushButton{padding:6px 14px;border:1px solid #d0d3d9;"
            "border-radius:4px;background:#fff;}"
            "QPushButton:hover{background:#eef0f3;}"
        )
        self.refresh_btn.clicked.connect(self._refresh)  # type: ignore[arg-type]
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        subtitle = QtWidgets.QLabel(
            "Live status from each subscription CLI you're signed into.  "
            "Per-message remaining-quota numbers aren't returned headlessly "
            "for either provider — the dashboards below are the source of "
            "truth for plan tier, billing and historical usage."
        )
        subtitle.setStyleSheet("color:#5b6068;font-size:11px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Scrollable card list so adding more providers later doesn't
        # break the layout.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._cards_holder = QtWidgets.QWidget()
        self._cards_layout = QtWidgets.QVBoxLayout(self._cards_holder)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        scroll.setWidget(self._cards_holder)
        layout.addWidget(scroll, stretch=1)

        self.status_line = QtWidgets.QLabel("Checking…")
        self.status_line.setStyleSheet("color:#5b6068;font-size:11px;")
        layout.addWidget(self.status_line)

        QtCore.QTimer.singleShot(0, self._refresh)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        import time

        now = time.monotonic()
        if (
            self._last_refresh_at is not None
            and now - self._last_refresh_at < self._REFRESH_COOLDOWN_SECONDS
        ):
            remaining = int(self._REFRESH_COOLDOWN_SECONDS - (now - self._last_refresh_at))
            self.status_line.setText(
                f"Cooldown — next refresh available in {remaining}s "
                "(no auto-poll; manual or every 5 min, whichever first)."
            )
            return
        self._last_refresh_at = now
        self.refresh_btn.setEnabled(False)
        self.status_line.setText("Probing local CLIs…")
        asyncio.ensure_future(self._refresh_async())

    async def _refresh_async(self) -> None:
        try:
            res = await self.client.call("limits.check", {})
        except Exception as exc:
            self.status_line.setText(f"Probe failed: {exc}")
            self.refresh_btn.setEnabled(True)
            return
        # Local-tally is cheap (one SQL count per window per
        # provider); fetch it alongside the probe so cards render
        # in one shot.
        try:
            usage = await self.client.call("limits.usage", {})
        except Exception:
            usage = {"providers": {}}
        try:
            attachment_usage = await self.client.call("attachments.usage", {})
        except Exception:
            attachment_usage = {"agents": [], "total_files": 0, "total_bytes": 0}
        self._render_providers(
            res.get("providers", []),
            res.get("context_windows", {}),
            res.get("data_as_of", "?"),
            usage.get("providers", {}),
            attachment_usage,
        )
        self.status_line.setText(
            f"Last checked: {QtCore.QDateTime.currentDateTime().toString('HH:mm:ss')}  "
            f"·  Cooldown: {self._REFRESH_COOLDOWN_SECONDS // 60} min"
        )
        self.refresh_btn.setEnabled(True)

    def _render_providers(
        self,
        providers: list[dict[str, Any]],
        context_windows: dict[str, int],
        data_as_of: str,
        local_usage: dict[str, dict[str, int]],
        attachment_usage: dict[str, Any] | None = None,
    ) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        for p in providers:
            self._cards_layout.addWidget(
                self._build_card(p, context_windows, data_as_of, local_usage)
            )
        # Per-model context-window summary card so the operator sees
        # at a glance how big each model's prompt budget is.
        if context_windows:
            self._cards_layout.addWidget(self._build_context_card(context_windows))
        # Attachment-storage card: total disk usage by agents'
        # uploaded images + spreadsheets, so the operator can see where
        # disk is going and clean up loud agents.
        if attachment_usage and attachment_usage.get("total_files", 0) > 0:
            self._cards_layout.addWidget(self._build_attachment_card(attachment_usage))
        self._cards_layout.addStretch(1)

    def _build_attachment_card(self, usage: dict[str, Any]) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setStyleSheet(
            "QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;padding:14px;}"
        )
        v = QtWidgets.QVBoxLayout(card)
        v.setSpacing(6)
        title = QtWidgets.QLabel("Attachment storage")
        title.setStyleSheet("font-size:14px;font-weight:600;color:#0f1115;")
        v.addWidget(title)
        total_files = int(usage.get("total_files", 0))
        total_bytes = int(usage.get("total_bytes", 0))
        v.addWidget(
            QtWidgets.QLabel(
                f"<span style='color:#5b6068;font-size:11px;'>"
                f"{total_files} file(s) · {_humanize_bytes(total_bytes)} total"
                f"</span>"
            )
        )
        for row in usage.get("agents", []):
            line = QtWidgets.QLabel(
                f"<b>{row.get('agent_name', '?')}</b>  ·  "
                f"{row.get('files', 0)} files  ·  "
                f"{_humanize_bytes(int(row.get('bytes', 0)))}"
            )
            line.setStyleSheet("font-size:11px;color:#0f1115;padding-left:6px;")
            v.addWidget(line)
        return card

    def _build_card(
        self,
        provider: dict[str, Any],
        context_windows: dict[str, int],
        data_as_of: str,
        local_usage: dict[str, dict[str, int]],
    ) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setStyleSheet("QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)

        # Header row: provider label + signed-in dot.
        header = QtWidgets.QHBoxLayout()
        version = provider.get("version", {}) or {}
        installed = bool(version.get("ok"))
        dot = QtWidgets.QLabel("●")
        dot.setStyleSheet(
            "color:#1f7a3f;font-size:14px;border:none;"
            if installed
            else "color:#b3261e;font-size:14px;border:none;"
        )
        header.addWidget(dot)
        title = QtWidgets.QLabel(f"<b>{provider.get('label', '?')}</b>")
        title.setStyleSheet("font-size:14px;color:#0f1115;border:none;")
        header.addWidget(title)
        header.addStretch(1)
        if installed:
            ver_text = (version.get("stdout") or "").splitlines()[:1]
            ver_lbl = QtWidgets.QLabel(ver_text[0] if ver_text else "")
            ver_lbl.setStyleSheet("color:#5b6068;font-size:11px;border:none;")
            header.addWidget(ver_lbl)
        else:
            ver_lbl = QtWidgets.QLabel("not installed")
            ver_lbl.setStyleSheet("color:#b3261e;font-size:11px;border:none;")
            header.addWidget(ver_lbl)
        v.addLayout(header)

        # Status output box.
        status = provider.get("status", {}) or {}
        body_text = self._format_status_body(status, installed)
        body = QtWidgets.QPlainTextEdit(body_text)
        body.setReadOnly(True)
        body.setStyleSheet(
            "QPlainTextEdit{background:#f6f8fa;border:1px solid #e6e7eb;"
            "border-radius:4px;padding:8px;"
            "font-family:ui-monospace,Consolas,Menlo,monospace;font-size:11px;"
            "color:#0f1115;}"
        )
        body.setMinimumHeight(80)
        body.setMaximumHeight(220)
        v.addWidget(body)

        # Note line.
        note = provider.get("note", "")
        if note:
            note_lbl = QtWidgets.QLabel(note)
            note_lbl.setWordWrap(True)
            note_lbl.setStyleSheet("color:#5b6068;font-size:11px;border:none;")
            v.addWidget(note_lbl)

        # Dashboard buttons.
        dashboards = provider.get("dashboards") or []
        if dashboards:
            dash_row = QtWidgets.QHBoxLayout()
            for d in dashboards:
                btn = QtWidgets.QPushButton(d.get("label", "Open"))
                btn.setStyleSheet(
                    "QPushButton{padding:4px 10px;border:1px solid #d0d3d9;"
                    "border-radius:4px;background:#fff;font-size:11px;}"
                    "QPushButton:hover{background:#eef0f3;}"
                )
                url = d.get("url", "")
                btn.clicked.connect(  # type: ignore[arg-type]
                    lambda _checked=False, u=url: webbrowser.open(u) if u else None
                )
                dash_row.addWidget(btn)
            dash_row.addStretch(1)
            v.addLayout(dash_row)

        # Plan + caps section.  Operator picks their plan from the
        # dropdown; we render the published per-window message caps
        # for that plan and the local tally for that window so the
        # gap between "what the dashboard said you'd get" and "what
        # we've sent so far this session" is visible.
        plans = provider.get("plans") or []
        if plans:
            plan_row = QtWidgets.QHBoxLayout()
            plan_row.addWidget(self._tag(f"Your plan ({data_as_of}):"))
            plan_combo = QtWidgets.QComboBox()
            for p in plans:
                plan_combo.addItem(str(p.get("label", "?")), p)
            plan_row.addWidget(plan_combo, stretch=1)
            v.addLayout(plan_row)

            caps_box = QtWidgets.QPlainTextEdit()
            caps_box.setReadOnly(True)
            caps_box.setStyleSheet(
                "QPlainTextEdit{background:#f6f8fa;border:1px solid #e6e7eb;"
                "border-radius:4px;padding:8px;font-size:11px;color:#0f1115;"
                "font-family:ui-sans-serif,Inter,system-ui;}"
            )
            caps_box.setMinimumHeight(80)
            caps_box.setMaximumHeight(160)
            v.addWidget(caps_box)

            usage_for_provider = local_usage.get(provider.get("id", ""), {})

            def render_caps(_idx: int = -1) -> None:
                idx = plan_combo.currentIndex()
                plan = plan_combo.itemData(idx) if idx >= 0 else None
                if not isinstance(plan, dict):
                    return
                lines: list[str] = []
                for cap in plan.get("message_caps") or []:
                    win = str(cap.get("window", "?"))
                    model = str(cap.get("model", ""))
                    msgs = cap.get("messages", "?")
                    # Map our window labels to the local-tally
                    # buckets (5h / 24h / 7d).
                    bucket = {"5h": "5h", "daily": "24h", "weekly": "7d"}.get(win)
                    used = usage_for_provider.get(bucket) if bucket else None
                    used_part = f"  (this app: {used} sent)" if used is not None else ""
                    lines.append(f"  • {win}  {model}: {msgs} msgs{used_part}")
                if not lines:
                    lines.append("  (no caps published for this plan)")
                note = str(plan.get("notes", ""))
                if note:
                    lines.append("")
                    lines.append(f"  Note: {note}")
                caps_box.setPlainText("\n".join(lines))

            plan_combo.currentIndexChanged.connect(render_caps)  # type: ignore[arg-type]
            render_caps()

        return card

    def _build_context_card(self, context_windows: dict[str, int]) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setStyleSheet("QFrame{background:#fff;border:1px solid #e6e7eb;border-radius:6px;}")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)

        title = QtWidgets.QLabel("<b>Per-model context windows</b>")
        title.setStyleSheet("font-size:14px;color:#0f1115;border:none;")
        v.addWidget(title)

        sub = QtWidgets.QLabel(
            "Maximum prompt size each model can take in a single call.  "
            "Includes system + transcript + any inlined references."
        )
        sub.setStyleSheet("color:#5b6068;font-size:11px;border:none;")
        sub.setWordWrap(True)
        v.addWidget(sub)

        body = QtWidgets.QPlainTextEdit()
        body.setReadOnly(True)
        body.setStyleSheet(
            "QPlainTextEdit{background:#f6f8fa;border:1px solid #e6e7eb;"
            "border-radius:4px;padding:8px;font-size:11px;color:#0f1115;"
            "font-family:ui-monospace,Consolas,Menlo,monospace;}"
        )
        body.setMinimumHeight(80)
        body.setMaximumHeight(220)
        lines: list[str] = []
        for model, tokens in sorted(context_windows.items()):
            lines.append(f"  {model:<28}  {tokens:>9,} tokens")
        body.setPlainText("\n".join(lines))
        v.addWidget(body)
        return card

    @staticmethod
    def _tag(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color:#5b6068;font-size:11px;border:none;")
        return lbl

    @staticmethod
    def _format_status_body(status: dict[str, Any], installed: bool) -> str:
        if not installed:
            return (
                "CLI is not on PATH.  Use the Operator Panel's "
                'Step 1 ("First-time install") then Step 2 / 3 to '
                "verify auth."
            )
        if status.get("ok"):
            return status.get("stdout") or "(empty status output)"
        # Newer-version status command might not exist on the
        # operator's CLI version; show the stderr but frame it as
        # informational rather than an error.
        stderr = status.get("stderr") or ""
        return (
            "`status` subcommand is not available on this CLI version "
            "(this is normal for older builds).  See the dashboards "
            "below for plan + usage info.\n\n"
            f"raw stderr: {stderr}"
        )
