"""Staging Area node.

A staging area is a first-class flow node that can wait, aggregate, and
gate release decisions independently of a Reaper. The executor uses the
node's params to decide when downstream execution may continue.
"""

from __future__ import annotations

from typing import Any

from PySide6 import QtGui

from apps.gui.canvas.nodes.base import BaseNode
from apps.gui.canvas.ports import Port, PortDirection


_MODES = (
    "wait_for_all",
    "wait_for_any",
    "threshold",
    "manual_release",
    "agent_decision",
    "budget_gate",
    "quality_gate",
)


class StagingAreaNode(BaseNode):
    """A flow node that stages and releases upstream context."""

    HEADER_COLOUR = QtGui.QColor("#8a5b00")

    def __init__(self, node_id: str, *, params: dict[str, Any] | None = None) -> None:
        params = params or {}
        self.mode = str(params.get("mode") or "wait_for_all")
        if self.mode not in _MODES:
            self.mode = "wait_for_all"
        self.threshold = int(params.get("threshold") or 1)
        self.timeout_seconds = (
            int(params["timeout_seconds"]) if params.get("timeout_seconds") is not None else None
        )
        self.decision_card_id = params.get("decision_card_id") or None
        self.budget_limit_usd = (
            float(params["budget_limit_usd"]) if params.get("budget_limit_usd") is not None else None
        )
        self.estimated_cost_usd = (
            float(params["estimated_cost_usd"])
            if params.get("estimated_cost_usd") is not None
            else None
        )
        self.quality_threshold = (
            float(params["quality_threshold"])
            if params.get("quality_threshold") is not None
            else None
        )
        self.observed_quality = (
            float(params["observed_quality"])
            if params.get("observed_quality") is not None
            else None
        )
        self.summary_hint = str(params.get("summary_hint") or "")
        self.release_note = str(params.get("release_note") or "")

        super().__init__(
            node_id=node_id,
            title="Staging Area",
            subtitle=self._build_subtitle(),
            body=self.summary_hint or "Waiting for release conditions.",
        )

        self.add_input_port(Port(self, PortDirection.INPUT, "in"))
        self.add_output_port(Port(self, PortDirection.OUTPUT, "out"))

    def _build_subtitle(self) -> str:
        extras: list[str] = [self.mode.replace("_", " ")]
        if self.mode == "threshold":
            extras.append(f"threshold={self.threshold}")
        if self.timeout_seconds is not None:
            extras.append(f"timeout={self.timeout_seconds}s")
        if self.decision_card_id:
            extras.append("reaper-linked")
        return " | ".join(extras)

    def sync_view(self) -> None:
        self._subtitle = self._build_subtitle()
        self.set_body(self.summary_hint or self.release_note or "Waiting for release conditions.")
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in _MODES else "wait_for_all"
        self.sync_view()

    def set_threshold(self, threshold: int) -> None:
        self.threshold = max(1, int(threshold))
        self.sync_view()

    def set_timeout_seconds(self, timeout_seconds: int | None) -> None:
        self.timeout_seconds = int(timeout_seconds) if timeout_seconds is not None else None
        self.sync_view()

    def to_payload(self) -> dict[str, Any]:
        pos = self.pos()
        params: dict[str, Any] = {
            "mode": self.mode,
            "threshold": self.threshold,
            "summary_hint": self.summary_hint,
            "release_note": self.release_note,
        }
        if self.timeout_seconds is not None:
            params["timeout_seconds"] = self.timeout_seconds
        if self.decision_card_id:
            params["decision_card_id"] = self.decision_card_id
        if self.budget_limit_usd is not None:
            params["budget_limit_usd"] = self.budget_limit_usd
        if self.estimated_cost_usd is not None:
            params["estimated_cost_usd"] = self.estimated_cost_usd
        if self.quality_threshold is not None:
            params["quality_threshold"] = self.quality_threshold
        if self.observed_quality is not None:
            params["observed_quality"] = self.observed_quality
        return {
            "id": self.node_id,
            "type": "staging_area",
            "x": pos.x(),
            "y": pos.y(),
            "params": params,
        }
