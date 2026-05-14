"""Targeted smoke tests for the Templates page polish."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets
from PySide6.QtTest import QTest

from apps.gui.canvas.nodes.template_graph import TemplateGraphNode
from apps.gui.canvas.palette import PalettePanel
from apps.gui.windows.templates import TemplateBuilderPage, _preview_text
from apps.service.types import AgentTemplate, TemplateNode, TemplateValidationIssue, TemplateValidationResult


class _FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params or {}))
        if method in self.responses:
            return self.responses[method]
        if method == "template_graphs.validate":
            return {"valid": True, "errors": [], "warnings": []}
        return []


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
    assert preview[-1] in {".", "…"}


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


def test_template_graph_node_surfaces_execution_contract() -> None:
    _app()
    node = TemplateGraphNode(
        "n2",
        {
            "type": "integration_action",
            "title": "Collect WordFlash article",
            "params": {
                "integration_kind": "mcp_tool",
                "target_app": "WordFlash",
                "action_name": "collect article",
                "server_id": "wordflash-server",
                "tool_name": "collect_article",
            },
        },
    )

    payload = node.to_template_payload()
    assert "WordFlash" in payload["subtitle"]
    assert "collect article" in payload["subtitle"]
    assert "WordFlash" in payload["footer"]
    assert "Executes via MCP tool" in payload["footer"]
    assert "server: wordflash-server" in payload["footer"]
    assert "tool: collect_article" in payload["footer"]

    passthrough_node = TemplateGraphNode(
        "n4",
        {
            "type": "integration_action",
            "title": "Preview WordFlash article",
            "params": {
                "integration_kind": "passthrough",
                "target_app": "WordFlash",
                "action_name": "collect article",
            },
        },
    )
    passthrough_payload = passthrough_node.to_template_payload()
    assert "preview only" in passthrough_payload["footer"].lower()
    assert "does not launch" in passthrough_payload["footer"].lower()

    command_node = TemplateGraphNode(
        "n3",
        {
            "type": "command",
            "title": "Manual gate",
            "command": "echo hello",
        },
    )
    command_payload = command_node.to_template_payload()
    assert "Manual gate" in command_payload["footer"]
    assert "echo hello" in command_payload["footer"]


def test_staging_area_node_surfaces_gate_footer() -> None:
    _app()
    from apps.gui.canvas.nodes.staging_area import StagingAreaNode

    node = StagingAreaNode(
        "s1",
        params={
            "mode": "manual_release",
            "summary_hint": "Validate the local inputs before release.",
            "release_note": "Release only after validation passes.",
        },
    )

    payload = node.to_payload()
    assert "Gate" in payload["params"]["summary_hint"] or payload["params"]["summary_hint"]
    assert "manual release" in node._footer
    assert "Validate the local inputs" in node._footer or "Release only" in node._footer


@pytest.mark.asyncio
async def test_template_builder_proxy_click_selects_node() -> None:
    app = _app()
    client = _FakeClient({"template_graphs.list": []})
    page = TemplateBuilderPage(client)  # type: ignore[arg-type]
    await asyncio.sleep(0)

    template = AgentTemplate(
        name="Draft",
        nodes=[
            TemplateNode(
                id="n1",
                type="agent_action",
                title="Template node",
                summary="Click me",
                x=220,
                y=180,
            )
        ],
        edges=[],
    )
    page._load_template(template)
    page.resize(1000, 700)
    page.show()
    app.processEvents()

    node = page.scene.nodes()[0]
    proxy = page.view._proxies[node.node_id]  # type: ignore[attr-defined]
    QTest.mouseClick(
        proxy,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        proxy.rect().center(),
    )
    app.processEvents()

    assert node.isSelected() is True
    assert page.scene.selectedItems() == [node]
    page.close()
