"""Graph-template CRUD, validation, export, and deployment tests."""

from __future__ import annotations

import pytest

from apps.service.store.events import TemplateVersionConflict
from apps.service.templates.deployment import (
    deploy_template_graph,
    export_mermaid,
    validate_template_graph,
)
from apps.service.types import (
    AgentTemplate,
    TemplateCardMapping,
    TemplateDeploymentSettings,
    TemplateEdge,
    TemplateNode,
)


def _valid_template() -> AgentTemplate:
    return AgentTemplate(
        name="Example",
        description="Reusable agent-team template",
        category="ops",
        tags=["demo", "ops"],
        published=True,
        nodes=[
            TemplateNode(id="start", type="start", title="Start", body="Kick off"),
            TemplateNode(
                id="agent",
                type="agent_action",
                title="Agent task",
                body="Draft the response.",
                agent_role="worker",
                instruction="Draft the response.",
                card_mapping=TemplateCardMapping(
                    name="Worker",
                    description="Worker card",
                    provider="anthropic",
                    model="claude-sonnet-4-5",
                    role="worker",
                ),
            ),
            TemplateNode(id="command", type="command", title="Command", command="echo hello"),
            TemplateNode(id="end", type="end", title="End"),
            TemplateNode(id="note", type="documentation", title="Note", body="Docs only"),
        ],
        edges=[
            TemplateEdge(id="e1", from_node="start", from_port="start", to_node="agent", to_port="in"),
            TemplateEdge(id="e2", from_node="agent", from_port="out", to_node="command", to_port="in"),
            TemplateEdge(id="e3", from_node="command", from_port="out", to_node="end", to_port="in"),
        ],
    )


def _invalid_template() -> AgentTemplate:
    return AgentTemplate(
        name="Invalid",
        nodes=[
            TemplateNode(id="start", type="start", title="Start"),
            TemplateNode(
                id="branch",
                type="decision",
                title="Branch",
                body="Route traffic",
                params={"pattern": ".*"},
            ),
            TemplateNode(
                id="agent",
                type="agent_action",
                title="Agent task",
                body="Do work",
                agent_role="worker",
                instruction="Do work",
                card_mapping=TemplateCardMapping(
                    name="Worker",
                    provider="anthropic",
                    model="claude-sonnet-4-5",
                ),
            ),
            TemplateNode(id="note", type="documentation", title="Note"),
            TemplateNode(id="mystery", type="custom_widget", title="Unknown"),
            TemplateNode(id="end", type="end", title="End"),
        ],
        edges=[
            TemplateEdge(id="e1", from_node="start", from_port="start", to_node="branch", to_port="in"),
            TemplateEdge(id="e2", from_node="branch", from_port="true", to_node="agent", to_port="in", label="yes"),
            TemplateEdge(id="e3", from_node="branch", from_port="", to_node="end", to_port="in"),
        ],
    )


def _integration_template() -> AgentTemplate:
    return AgentTemplate(
        name="Integration",
        description="Template with a machine action",
        category="ops",
        tags=["integration", "demo"],
        published=True,
        nodes=[
            TemplateNode(id="start", type="start", title="Start", body="Kick off"),
            TemplateNode(
                id="machine",
                type="integration_action",
                title="Collect WordFlash article",
                subtitle="mcp tool",
                summary="Collect article inputs from WordFlash.",
                body="Call the WordFlash tool and collect article inputs.",
                params={
                    "integration_kind": "mcp_tool",
                    "target_app": "WordFlash",
                    "action_name": "collect article",
                    "server_id": "wordflash-server",
                    "tool_name": "collect_article",
                },
            ),
            TemplateNode(id="end", type="end", title="End"),
            TemplateNode(id="note", type="documentation", title="Note", body="Docs only"),
        ],
        edges=[
            TemplateEdge(id="e1", from_node="start", from_port="start", to_node="machine", to_port="in"),
            TemplateEdge(id="e2", from_node="machine", from_port="out", to_node="end", to_port="in"),
        ],
    )


@pytest.mark.asyncio
async def test_template_graph_crud_round_trip(store) -> None:
    template = _valid_template()

    inserted = await store.insert_template_graph(template.model_copy(deep=True))
    assert inserted.id == template.id
    assert inserted.version == 1

    fetched = await store.get_template_graph(template.id)
    assert fetched is not None
    assert fetched.name == "Example"
    assert len(fetched.nodes) == 5

    fetched.description = "Updated"
    updated = await store.update_template_graph(fetched, expected_version=1)
    assert updated.version == 2

    listed = await store.list_template_graphs()
    assert any(item.id == template.id for item in listed)

    dup = await store.duplicate_template_graph(template.id, name="Example Copy")
    assert dup is not None
    assert dup.id != template.id
    assert dup.version == 1
    assert dup.name == "Example Copy"

    deleted = await store.delete_template_graph(template.id)
    assert deleted is True
    assert await store.get_template_graph(template.id) is None


@pytest.mark.asyncio
async def test_template_graph_update_conflict(store) -> None:
    template = _valid_template()
    await store.insert_template_graph(template)
    fetched = await store.get_template_graph(template.id)
    assert fetched is not None
    fetched.description = "first write"
    await store.update_template_graph(fetched, expected_version=1)

    stale = template.model_copy(deep=True)
    stale.description = "stale write"
    with pytest.raises(TemplateVersionConflict):
        await store.update_template_graph(stale, expected_version=1)


def test_template_graph_validation_export_and_deploy() -> None:
    valid = _valid_template()
    valid_result = validate_template_graph(valid)
    assert valid_result.valid is True
    assert not valid_result.errors
    assert any(issue.code == "documentation-only" for issue in valid_result.warnings)
    assert any(issue.code == "legacy-command-node" for issue in valid_result.warnings)

    invalid = _invalid_template()
    invalid_result = validate_template_graph(invalid)
    assert invalid_result.valid is False
    assert any(issue.code == "decision-branches" for issue in invalid_result.errors)
    assert any(issue.code == "unsupported-node-type" for issue in invalid_result.warnings)

    mermaid = export_mermaid(valid)
    assert mermaid.startswith("flowchart LR")
    assert "start" in mermaid
    assert "agent" in mermaid
    assert "command" in mermaid

    before = valid.model_dump(mode="json")
    deployment = deploy_template_graph(
        valid,
        TemplateDeploymentSettings(
            template_id=valid.id,
            template_version=valid.version,
            drop_x=13.0,
            drop_y=27.0,
        ),
    )
    after = valid.model_dump(mode="json")

    assert before == after
    assert deployment.errors == []
    assert len(deployment.nodes) == 4
    assert len(deployment.edges) == 3
    assert any("documentation-only" in warning for warning in deployment.warnings)
    for node in deployment.nodes:
        assert round(float(node["x"])) % 20 == 0
        assert round(float(node["y"])) % 20 == 0
        assert node["deployment"]["source_template_id"] == valid.id
        assert node["deployment"]["deployed_group_id"] == deployment.deployed_group_id


def test_template_graph_integration_action_validation_and_deploy() -> None:
    template = _integration_template()
    result = validate_template_graph(template)
    assert result.valid is True
    assert not result.errors

    deployment = deploy_template_graph(
        template,
        TemplateDeploymentSettings(
            template_id=template.id,
            template_version=template.version,
            drop_x=0.0,
            drop_y=0.0,
        ),
    )
    assert deployment.errors == []
    assert any(node["kind"] == "control" and node["control_kind"] == "integration_action" for node in deployment.nodes)
    machine = next(node for node in deployment.nodes if node.get("control_kind") == "integration_action")
    assert machine["params"]["integration_kind"] == "mcp_tool"
    assert machine["params"]["target_app"] == "WordFlash"
    assert machine["params"]["action_name"] == "collect article"
    assert machine["params"]["server_id"] == "wordflash-server"
    assert machine["params"]["tool_name"] == "collect_article"
    assert machine["params"]["summary_hint"] == "Collect article inputs from WordFlash."
    assert machine["subtitle"] == "Collect article inputs from WordFlash."
    assert machine["body"] == "Call the WordFlash tool and collect article inputs."
    assert machine["params"]["body"] == "Call the WordFlash tool and collect article inputs."


def test_template_graph_passthrough_warns_that_it_does_not_execute() -> None:
    template = _integration_template().model_copy(deep=True)
    machine = next(node for node in template.nodes if node.id == "machine")
    machine.params["integration_kind"] = "passthrough"
    machine.params.pop("server_id", None)
    machine.params.pop("tool_name", None)

    result = validate_template_graph(template)
    assert result.valid is True
    assert any(issue.code == "integration-kind-passthrough" for issue in result.warnings)


def test_template_graph_rejects_unreachable_executable_nodes() -> None:
    template = AgentTemplate(
        name="Unreachable",
        nodes=[
            TemplateNode(id="start", type="start", title="Start"),
            TemplateNode(
                id="machine",
                type="integration_action",
                title="Machine action",
                params={
                    "integration_kind": "passthrough",
                    "target_app": "WordFlash",
                    "action_name": "collect article",
                },
            ),
            TemplateNode(id="end", type="end", title="End"),
        ],
        edges=[
            TemplateEdge(id="e1", from_node="start", from_port="start", to_node="end", to_port="in"),
        ],
    )

    result = validate_template_graph(template)
    assert result.valid is False
    assert any(issue.code == "unreachable-node" for issue in result.errors)
