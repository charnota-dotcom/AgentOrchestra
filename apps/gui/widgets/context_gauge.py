"""Context-window gauge — small footer widget for chat surfaces.

Renders the operator-facing readout for "how close is this drone to
the model's context limit?".  Hidden entirely when the
``(provider, model)`` pair is unknown (the service returns
``context_window: null`` in that case — see
``apps.service.tokens.limits``).

Two layouts in one widget, picked via the ``compact`` constructor
flag:

* ``compact=False`` (default, used in the Drones tab footer):

      ~12.4K / 200K tokens (est)   ████░░░░░░░░░░░░  6.2%

* ``compact=True`` (used in the canvas drone chat dialog, where
  horizontal space is tighter):

      ~12.4K / 200K  [6.2%]

Colour bands by percentage of the context window:

* <60%       green
* 60-80%      amber (consider forking the drone soon)
* 80-95%      orange (next turn at risk of truncation)
* >=95%       red (truncation imminent; fork now)

Public API is one method: ``update(transcript_tokens, context_window)``.
Pass the values straight from the latest ``drones.send`` response; the
widget hides / re-shows / repaints accordingly.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


def _humanise_count(n: int) -> str:
    """Compact int → string like '12.4K' / '12K' / '1.2M'."""
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        s = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}K"
    s = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
    return f"{s}M"


def _band_colour(pct: float) -> str:
    """Hex colour for the progress bar fill at the given fraction (0..1)."""
    if pct < 0.60:
        return "#1f7a3f"  # green
    if pct < 0.80:
        return "#cc8400"  # amber
    if pct < 0.95:
        return "#cc5500"  # orange
    return "#b3261e"  # red


class ContextGauge(QtWidgets.QWidget):
    """Progress-bar + label readout of `transcript_tokens / context_window`."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._compact = compact

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._label = QtWidgets.QLabel("")
        self._label.setStyleSheet(
            "color:#5b6068;font-size:11px;font-family:ui-sans-serif,Inter,system-ui;"
        )
        layout.addWidget(self._label)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 1000)  # tenths of a percent for smoother fill
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        if compact:
            self._bar.setMinimumWidth(60)
        else:
            self._bar.setMinimumWidth(160)
        layout.addWidget(self._bar, stretch=1)

        self._pct = QtWidgets.QLabel("")
        self._pct.setStyleSheet(
            "color:#0f1115;font-size:11px;font-weight:600;"
            "font-family:ui-sans-serif,Inter,system-ui;"
        )
        self._pct.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self._pct.setMinimumWidth(50)
        layout.addWidget(self._pct)

        # Start hidden — no data yet.
        self.setVisible(False)

    def set_token_counts(
        self,
        transcript_tokens: int | None,
        context_window: int | None,
    ) -> None:
        """Refresh the gauge with the latest values from `drones.send`.

        Pass ``None`` for either to hide the widget (unknown model
        pair, or before the first message has been sent).
        """
        if transcript_tokens is None or context_window is None or context_window <= 0:
            self.setVisible(False)
            return

        fraction = max(0.0, min(1.0, transcript_tokens / context_window))
        if self._compact:
            self._label.setText(
                f"~{_humanise_count(transcript_tokens)} / {_humanise_count(context_window)}"
            )
        else:
            self._label.setText(
                f"~{_humanise_count(transcript_tokens)} / "
                f"{_humanise_count(context_window)} tokens (est)"
            )
        self._pct.setText(f"{fraction * 100:.1f}%")
        self._bar.setValue(int(fraction * 1000))

        colour = _band_colour(fraction)
        self._bar.setStyleSheet(
            "QProgressBar{background:#e6e7eb;border:none;"
            "border-radius:4px;}"
            f"QProgressBar::chunk{{background:{colour};border-radius:4px;}}"
        )
        # Tooltip explains the colour band so the operator knows
        # what to do at 80%/95%.
        if fraction < 0.60:
            tooltip = "Plenty of context headroom."
        elif fraction < 0.80:
            tooltip = (
                "Approaching the context limit.  Consider forking the "
                "drone (Handoff → Continuation) into a fresh chat soon."
            )
        elif fraction < 0.95:
            tooltip = "Next turn at risk of truncation.  Forking the drone is strongly recommended."
        else:
            tooltip = "Truncation imminent.  Fork the drone now to keep the conversation usable."
        self.setToolTip(tooltip)
        self.setVisible(True)
