"""Targeted smoke tests for the Templates page polish."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtWidgets

from apps.gui.canvas.nodes.template_graph import TemplateGraphNode
from apps.gui.canvas.palette import PalettePanel
from apps.gui.windows.templates import TemplateBuilderPage, _preview_text
from apps.service.types import AgentTemplate, TemplateValidationIssue, TemplateValidationResult


class _FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params or {}))
        return self.responses.get(method, [])


def _app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_preview_text_truncates_long_copy() -> None:
    assert _preview_text("short") == "short"
    long_text = " ".join(f"word{i}" for i in range(40))
    preview = _preview_text(long_text, limit=60)
    assert len(preview) <= 60
    assert preview.endswith("…")


@pytest.mark.asyncio
async def test_template_builder_validation_panel_updates_state() -> None:
    _app()
    client = _FakeClient({"template_graphs.list": []})
    page = TemplateBuilderPage(client)  # type: ignore[arg-type]
    await asyncio.sleep(0)
    page._current = AgentTemplate(name="Draft")

    result = TemplateValidationResult(
        valid=False,
        errors=[
            TemplateValidationIssue(
                code="missing-agent-role",
                message="agent role is missing",
                node_id="n1",
                field="agent_role",
            )
        ],
        warnings=[
            TemplateValidationIssue(
                code="docs",
                message="documentation nodes will not deploy",
                node_id="n2",
            )
        ],
    )

    page._update_validation_panel(result)

    assert "Needs fixes" in page.validation_summary.text()
    assert "blocked" in page.validation_ready.text()
    assert page.validation_errors.count() == 1
    assert page.validation_warnings.count() == 1
    assert page.publish_btn.isEnabled() is False

    valid = TemplateValidationResult(valid=True, warnings=[], errors=[])
    page._update_validation_panel(valid)

    assert "Ready to publish" in page.validation_summary.text()
    assert page.publish_btn.isEnabled() is True


@pytest.mark.asyncio
async def test_palette_panel_lists_only_published_templates() -> None:
    _app()
    client = _FakeClient(
        {
            "cards.list": [],
            "drones.list": [],
            "template_graphs.list": [
                {
                    "id": "pub",
                    "name": "Published Template",
                    "category": "ops",
                    "nodes": [],
                    "edges": [],
                    "tags": [],
                    "published": True,
                    "version": 2,
                },
                {
                    "id": "draft",
                    "name": "Draft Template",
                    "category": "ops",
                    "nodes": [],
                    "edges": [],
                    "tags": [],
                    "published": False,
                    "version": 1,
                },
            ],
        }
    )
    panel = PalettePanel(client)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    await panel.reload_templates()

    assert panel.templates_list.count() == 1
    assert "Published" in panel.templates_list.item(0).text()


@pytest.mark.asyncio
async def test_template_graph_node_uses_concise_preview() -> None:
    _app()
    node = TemplateGraphNode(
        "n1",
        {
            "type": "agent_action",
            "title": "Agent task",
            "instruction": " ".join(f"token{i}" for i in range(40)),
            "body": " ".join(f"body{i}" for i in range(40)),
        },
    )

    payload = node.to_template_payload()
    assert len(payload["body"]) < len(payload["instruction"])
    assert payload["body"].endswith("…")
