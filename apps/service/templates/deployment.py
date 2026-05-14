"""Graph-template validation, export, and deployment helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from apps.service.types import (
    AgentTemplate,
    TemplateCardMapping,
    TemplateDeploymentResult,
    TemplateDeploymentSettings,
    TemplateEdge,
    TemplateNode,
    TemplateValidationIssue,
    TemplateValidationResult,
    TemplateValidationSeverity,
    long_id,
)

_KNOWN_TEMPLATE_TYPES = {
    "start",
    "decision",
    "agent_action",
    "integration_action",
    "command",
    "documentation",
    "end",
    "trigger",
    "branch",
    "merge",
    "human",
    "output",
    "staging_area",
    "ugv",
    "data_prep",
    "integration_action",
}

_DEPLOYABLE_TEMPLATE_TYPES = {
    "start",
    "decision",
    "agent_action",
    "integration_action",
    "command",
    "end",
    "trigger",
    "branch",
    "merge",
    "human",
    "output",
    "staging_area",
    "ugv",
    "data_prep",
    "integration_action",
}

_DOC_ONLY_TYPES = {"documentation"}

_NODE_WIDTH = 200
_NODE_HEIGHT = 110
_GAP_X = 80
_GAP_Y = 30
_GRID_SIZE = 20


def _canonical_type(raw: str | None) -> str:
    return (raw or "").strip().lower().replace("-", "_")


def _issue(
    code: str,
    message: str,
    *,
    severity: TemplateValidationSeverity = TemplateValidationSeverity.WARNING,
    node_id: str | None = None,
    edge_id: str | None = None,
    field: str | None = None,
) -> TemplateValidationIssue:
    return TemplateValidationIssue(
        code=code,
        severity=severity,
        message=message,
        node_id=node_id,
        edge_id=edge_id,
        field=field,
    )


def _template_data(template: AgentTemplate) -> dict[str, Any]:
    return template.model_dump(mode="json")


def _find_cycle_path(nodes: list[TemplateNode], edges: list[TemplateEdge]) -> list[str]:
    node_index = {n.id: n for n in nodes}
    graph: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.from_node in node_index and edge.to_node in node_index and edge.directional:
            graph[edge.from_node].append(edge.to_node)

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {nid: WHITE for nid in node_index}
    stack: list[str] = []

    def visit(nid: str) -> list[str] | None:
        colour[nid] = GREY
        stack.append(nid)
        for nxt in graph.get(nid, []):
            if colour[nxt] == WHITE:
                cycle = visit(nxt)
                if cycle:
                    return cycle
            elif colour[nxt] == GREY:
                if nxt in stack:
                    idx = stack.index(nxt)
                    return stack[idx:] + [nxt]
                return [nxt, nid, nxt]
        stack.pop()
        colour[nid] = BLACK
        return None

    for nid in node_index:
        if colour[nid] == WHITE:
            cycle = visit(nid)
            if cycle:
                return cycle
    return []


def validate_template_graph(template: AgentTemplate) -> TemplateValidationResult:
    """Return structured blocking errors and non-blocking warnings."""

    errors: list[TemplateValidationIssue] = []
    warnings: list[TemplateValidationIssue] = []
    nodes = list(template.nodes)
    edges = list(template.edges)
    node_ids = [n.id for n in nodes]
    node_index = {n.id: n for n in nodes}

    if len(node_index) != len(nodes):
        dupes = {nid for nid in node_ids if node_ids.count(nid) > 1}
        for nid in sorted(dupes):
            errors.append(
                _issue(
                    "duplicate-node-id",
                    f"duplicate node id: {nid}",
                    severity=TemplateValidationSeverity.ERROR,
                    node_id=nid,
                )
            )

    starts = [n for n in nodes if _canonical_type(n.type) == "start"]
    if len(starts) != 1:
        errors.append(
            _issue(
                "start-node-count",
                "template must contain exactly one start node",
                severity=TemplateValidationSeverity.ERROR,
            )
        )

    outgoing: dict[str, list[TemplateEdge]] = defaultdict(list)
    incoming: dict[str, list[TemplateEdge]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.from_node].append(edge)
        incoming[edge.to_node].append(edge)

    for edge in edges:
        if edge.from_node not in node_index or edge.to_node not in node_index:
            errors.append(
                _issue(
                    "dangling-edge",
                    f"edge references unknown node: {edge.from_node}->{edge.to_node}",
                    severity=TemplateValidationSeverity.ERROR,
                    edge_id=edge.id,
                )
            )

    for node in nodes:
        node_type = _canonical_type(node.type)
        if node_type not in _KNOWN_TEMPLATE_TYPES:
            warnings.append(
                _issue(
                    "unsupported-node-type",
                    f"unsupported template node type: {node.type}",
                    node_id=node.id,
                )
            )
            continue

        if node_type in _DOC_ONLY_TYPES:
            warnings.append(
                _issue(
                    "documentation-only",
                    "documentation-only nodes are not deployed to the canvas",
                    node_id=node.id,
                )
            )

        if node_type == "decision":
            labeled = [edge for edge in outgoing.get(node.id, []) if edge.label.strip()]
            if len(labeled) < 2:
                errors.append(
                    _issue(
                        "decision-branches",
                        "decision nodes need at least two labelled outgoing branches",
                        severity=TemplateValidationSeverity.ERROR,
                        node_id=node.id,
                    )
                )

        if node_type == "agent_action":
            if not node.agent_role:
                errors.append(
                    _issue(
                        "agent-role-missing",
                        "agent_action nodes must define an agent_role",
                        severity=TemplateValidationSeverity.ERROR,
                        node_id=node.id,
                        field="agent_role",
                    )
                )
            if not (node.instruction or node.command or node.body.strip()):
                errors.append(
                    _issue(
                        "agent-content-missing",
                        "agent_action nodes must define instruction or command content",
                        severity=TemplateValidationSeverity.ERROR,
                        node_id=node.id,
                    )
                )
            mapping = node.card_mapping
            if mapping is None:
                errors.append(
                    _issue(
                        "card-mapping-missing",
                        "agent_action nodes require card_mapping data to deploy",
                        severity=TemplateValidationSeverity.ERROR,
                        node_id=node.id,
                    )
                )
            else:
                for field in ("name", "provider", "model"):
                    if not getattr(mapping, field):
                        errors.append(
                            _issue(
                                "card-mapping-missing-field",
                                f"card_mapping.{field} is required for deployable agent nodes",
                                severity=TemplateValidationSeverity.ERROR,
                                node_id=node.id,
                                field=field,
                            )
                        )

        if node_type == "integration_action":
            params = node.params or {}
            integration_kind = str(params.get("integration_kind") or "mcp_tool").strip()
            target_app = str(params.get("target_app") or "").strip()
            action_name = str(params.get("action_name") or "").strip()
            if not target_app:
                errors.append(
                    _issue(
                        "integration-target-missing",
                        "integration_action nodes must define a target_app",
                        severity=TemplateValidationSeverity.ERROR,
                        node_id=node.id,
                        field="target_app",
                    )
                )
            if not action_name:
                errors.append(
                    _issue(
                        "integration-action-missing",
                        "integration_action nodes must define an action_name",
                        severity=TemplateValidationSeverity.ERROR,
                        node_id=node.id,
                        field="action_name",
                    )
                )
            if integration_kind not in {"mcp_tool", "passthrough"}:
                errors.append(
                    _issue(
                        "integration-kind-unsupported",
                        f"integration_action nodes must use a supported integration_kind: {integration_kind}",
                        severity=TemplateValidationSeverity.ERROR,
                        node_id=node.id,
                        field="integration_kind",
                    )
                )
            if integration_kind == "mcp_tool":
                if not str(params.get("server_id") or "").strip():
                    errors.append(
                        _issue(
                            "integration-server-missing",
                            "integration_action nodes using mcp_tool must define a server_id",
                            severity=TemplateValidationSeverity.ERROR,
                            node_id=node.id,
                            field="server_id",
                        )
                    )
                if not str(params.get("tool_name") or "").strip():
                    errors.append(
                        _issue(
                            "integration-tool-missing",
                            "integration_action nodes using mcp_tool must define a tool_name",
                            severity=TemplateValidationSeverity.ERROR,
                            node_id=node.id,
                            field="tool_name",
                        )
                    )

        if node_type == "command" and not (node.command or node.body.strip()):
            errors.append(
                _issue(
                    "command-missing",
                    "command nodes must define a command",
                    severity=TemplateValidationSeverity.ERROR,
                    node_id=node.id,
                    field="command",
                )
            )
        if node_type == "command":
            warnings.append(
                _issue(
                    "legacy-command-node",
                    "command nodes are legacy manual gates and do not execute app code; use integration_action for executable app/tool steps",
                    node_id=node.id,
                )
            )

    if not any(_canonical_type(n.type) == "agent_action" for n in nodes):
        warnings.append(
            _issue(
                "no-agent-nodes",
                "template contains no deployable agent_action nodes",
            )
        )

    cycle = _find_cycle_path(nodes, edges)
    if cycle:
        errors.append(
            _issue(
                "cycle-detected",
                f"template graph contains a cycle: {' -> '.join(cycle)}",
                severity=TemplateValidationSeverity.ERROR,
            )
        )

    if len(starts) == 1:
        start_id = starts[0].id
        reachable: set[str] = {start_id}
        queue: deque[str] = deque([start_id])
        graph: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            if edge.from_node in node_index and edge.to_node in node_index and edge.directional:
                graph[edge.from_node].append(edge.to_node)
        while queue:
            nid = queue.popleft()
            for nxt in graph.get(nid, []):
                if nxt not in reachable:
                    reachable.add(nxt)
                    queue.append(nxt)

        for node in nodes:
            node_type = _canonical_type(node.type)
            if node_type in _DOC_ONLY_TYPES or node_type not in _KNOWN_TEMPLATE_TYPES:
                continue
            if node_type == "start":
                continue
            if node.id not in reachable:
                errors.append(
                    _issue(
                        "unreachable-node",
                        f"executable node '{node.title}' is not reachable from Start",
                        severity=TemplateValidationSeverity.ERROR,
                        node_id=node.id,
                    )
                )

    return TemplateValidationResult(
        template_id=template.id,
        template_version=template.version,
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )


def export_mermaid(template: AgentTemplate) -> str:
    """Render the template graph as Mermaid flowchart syntax."""

    def _node_shape(node: TemplateNode) -> str:
        text = _mermaid_text(node.title or node.id)
        node_type = _canonical_type(node.type)
        if node_type in {"start", "end"}:
            return f'(({text}))'
        if node_type == "decision":
            return f'{{{text}}}'
        if node_type == "documentation":
            return f'[{text}]'
        if node_type == "command":
            return f'["{text}"]'
        return f'["{text}"]'

    lines = ["flowchart LR"]
    for node in template.nodes:
        lines.append(f"    {node.id}{_node_shape(node)}")
    for edge in template.edges:
        label = f'|{_mermaid_text(edge.label)}|' if edge.label else ""
        arrow = "-->" if edge.directional else "---"
        lines.append(f"    {edge.from_node} {arrow}{label} {edge.to_node}")
    return "\n".join(lines).rstrip() + "\n"


def deploy_template_graph(
    template: AgentTemplate,
    settings: TemplateDeploymentSettings,
) -> TemplateDeploymentResult:
    """Map a template graph to runtime canvas nodes and edges."""

    validation = validate_template_graph(template)
    if validation.errors:
        return TemplateDeploymentResult(
            template_id=template.id,
            template_version=template.version,
            deployed_group_id=long_id(),
            warnings=[issue.message for issue in validation.warnings],
            errors=[issue.message for issue in validation.errors],
        )

    group_id = long_id()
    node_lookup = {node.id: node for node in template.nodes}
    deployable_nodes = [
        node
        for node in template.nodes
        if _canonical_type(node.type) in _DEPLOYABLE_TEMPLATE_TYPES
    ]
    layout = _layout_nodes(template, deployable_nodes, settings.drop_x, settings.drop_y)
    node_map: dict[str, str] = {}
    outgoing_index: dict[str, int] = defaultdict(int)
    created_nodes: list[dict[str, Any]] = []
    created_edges: list[dict[str, Any]] = []
    warnings: list[str] = [issue.message for issue in validation.warnings]

    for node in deployable_nodes:
        runtime_id = long_id()
        node_map[node.id] = runtime_id
        payload = _node_payload(node, runtime_id, group_id, template, settings)
        if node.id in layout:
            payload["x"], payload["y"] = layout[node.id]
            if settings.snap_to_grid:
                payload["x"] = _snap(payload["x"])
                payload["y"] = _snap(payload["y"])
        created_nodes.append(payload)

    edge_count = 0
    for edge in template.edges:
        src_id = node_map.get(edge.from_node)
        dst_id = node_map.get(edge.to_node)
        if not src_id or not dst_id:
            if _canonical_type(node_lookup.get(edge.from_node, TemplateNode(id="", type="", title="")).type) in {
                "documentation",
            } or _canonical_type(node_lookup.get(edge.to_node, TemplateNode(id="", type="", title="")).type) in {
                "documentation",
            }:
                warnings.append(
                    f"skipped documentation-only edge {edge.from_node}->{edge.to_node}"
                )
            continue
        src_node = node_lookup[edge.from_node]
        dst_node = node_lookup[edge.to_node]
        src_payload = _edge_endpoint_payload(
            src_node,
            edge.from_port,
            outgoing=True,
            label=edge.label,
            sibling_index=outgoing_index[edge.from_node],
        )
        outgoing_index[edge.from_node] += 1
        dst_payload = _edge_endpoint_payload(dst_node, edge.to_port, outgoing=False)
        created_edges.append(
            {
                "id": long_id(),
                "from_node": src_id,
                "from_port": src_payload,
                "to_node": dst_id,
                "to_port": dst_payload,
                "label": edge.label,
                "directional": bool(edge.directional),
                "deployment": {
                    "source_template_id": template.id,
                    "source_template_version": template.version,
                    "source_template_node_id": edge.from_node,
                    "deployed_group_id": group_id,
                },
            }
        )
        edge_count += 1

    return TemplateDeploymentResult(
        template_id=template.id,
        template_version=template.version,
        deployed_group_id=group_id,
        created_node_ids=[n["id"] for n in created_nodes],
        created_edge_ids=[e["id"] for e in created_edges],
        nodes=created_nodes,
        edges=created_edges,
        warnings=warnings,
        errors=[],
    )


def _node_payload(
    node: TemplateNode,
    runtime_id: str,
    group_id: str,
    template: AgentTemplate,
    settings: TemplateDeploymentSettings,
) -> dict[str, Any]:
    node_type = _canonical_type(node.type)
    deployment = {
        "source_template_id": template.id,
        "source_template_version": template.version,
        "source_template_node_id": node.id,
        "deployed_instance_id": runtime_id,
        "deployed_group_id": group_id,
    }
    if node_type == "start" or node_type == "trigger":
        return {
            "id": runtime_id,
            "kind": "control",
            "control_kind": "trigger",
            "label": node.title,
            "title": node.title,
            "subtitle": node.subtitle or "Start of template",
            "body": node.body or "Template start",
            "x": 0.0,
            "y": 0.0,
            "params": {},
            "deployment": deployment,
        }
    if node_type == "decision" or node_type == "branch":
        return {
            "id": runtime_id,
            "kind": "control",
            "control_kind": "branch",
            "label": node.title,
            "title": node.title,
            "subtitle": node.subtitle or "Branch",
            "body": node.body or "Route on condition",
            "x": 0.0,
            "y": 0.0,
            "params": {"pattern": node.params.get("pattern") or node.params.get("regex") or ".*"},
            "deployment": deployment,
        }
    if node_type == "agent_action":
        mapping = node.card_mapping or TemplateCardMapping()
        card = {
            "id": long_id(),
            "name": mapping.name or node.title,
            "description": mapping.description or node.body,
            "provider": mapping.provider,
            "model": mapping.model,
            "role": mapping.role,
            "system_persona": node.instruction or node.body or "",
            "template_node_id": node.id,
            "source_template_id": template.id,
            "source_template_version": template.version,
            "deployed_group_id": group_id,
        }
        return {
            "id": runtime_id,
            "kind": "agent",
            "card": card,
            "label": node.title,
            "x": 0.0,
            "y": 0.0,
            "deployment": deployment,
        }
    if node_type == "integration_action":
        params = dict(node.params)
        payload_params = {
            "integration_kind": str(params.get("integration_kind") or "mcp_tool"),
            "target_app": str(params.get("target_app") or ""),
            "action_name": str(params.get("action_name") or ""),
            "server_id": str(params.get("server_id") or ""),
            "tool_name": str(params.get("tool_name") or ""),
            "arguments": params.get("arguments") or "",
            "summary_hint": str(node.summary or params.get("summary_hint") or ""),
            "body": str(node.body or ""),
            "release_note": str(params.get("release_note") or ""),
        }
        return {
            "id": runtime_id,
            "kind": "control",
            "control_kind": "integration_action",
            "label": node.title,
            "title": node.title,
            "subtitle": node.summary or node.subtitle or "Configured action",
            "body": node.body or payload_params["summary_hint"] or "External app/tool step",
            "x": 0.0,
            "y": 0.0,
            "params": payload_params,
            "deployment": deployment,
        }
    if node_type == "command":
        command = node.command or ""
        body_text = node.body or node.summary or "Legacy manual gate; does not execute app code."
        return {
            "id": runtime_id,
            "kind": "control",
            "control_kind": "staging_area",
            "label": node.title,
            "title": node.title,
            "subtitle": node.summary or node.subtitle or "Manual gate",
            "body": body_text,
            "x": 0.0,
            "y": 0.0,
            "params": {
                "mode": "manual_release",
                "summary_hint": node.summary or node.subtitle or command,
                "release_note": body_text,
                "execution_kind": "manual_gate",
                "command": command,
            },
            "deployment": deployment,
        }
    if node_type == "end" or node_type == "output":
        return {
            "id": runtime_id,
            "kind": "control",
            "control_kind": "output",
            "label": node.title,
            "title": node.title,
            "subtitle": node.subtitle or "End",
            "body": node.body or "Template end",
            "x": 0.0,
            "y": 0.0,
            "params": {},
            "deployment": deployment,
        }
    if node_type == "human":
        return {
            "id": runtime_id,
            "kind": "control",
            "control_kind": "human",
            "label": node.title,
            "title": node.title,
            "subtitle": node.subtitle or "Human",
            "body": node.body or "Await approval",
            "x": 0.0,
            "y": 0.0,
            "params": {},
            "deployment": deployment,
        }
    if node_type == "merge":
        return {
            "id": runtime_id,
            "kind": "control",
            "control_kind": "merge",
            "label": node.title,
            "title": node.title,
            "subtitle": node.subtitle or "Merge",
            "body": node.body or "Join branches",
            "x": 0.0,
            "y": 0.0,
            "params": {},
            "deployment": deployment,
        }
    if node_type == "staging_area":
        return {
            "id": runtime_id,
            "kind": "control",
            "control_kind": "staging_area",
            "label": node.title,
            "title": node.title,
            "subtitle": node.subtitle or "Staging Area",
            "body": node.body or "Wait / gate / release",
            "x": 0.0,
            "y": 0.0,
            "params": dict(node.params),
            "deployment": deployment,
        }
    # Fallback to a generic visible note so unsupported-but-deployable
    # template nodes don't crash the canvas.
    return {
        "id": runtime_id,
        "kind": "control",
        "control_kind": "staging_area",
        "label": node.title,
        "title": node.title,
        "subtitle": node.subtitle or node.type,
        "body": node.body or node.type,
        "x": 0.0,
        "y": 0.0,
        "params": {},
        "deployment": deployment,
    }


def _edge_endpoint_payload(
    node: TemplateNode,
    explicit_port: str,
    *,
    outgoing: bool,
    label: str = "",
    sibling_index: int = 0,
) -> str:
    if explicit_port.strip():
        return explicit_port.strip()
    node_type = _canonical_type(node.type)
    lower_label = label.strip().lower()
    if outgoing:
        if node_type == "start" or node_type == "trigger":
            return "start"
        if node_type == "decision" or node_type == "branch":
            if any(token in lower_label for token in ("false", "no", "reject", "else")):
                return "false"
            if any(token in lower_label for token in ("true", "yes", "approve", "match", "pass")):
                return "true"
            return "true" if sibling_index % 2 == 0 else "false"
        if node_type == "end" or node_type == "output":
            return ""
        if node_type == "agent_action":
            return "out"
        if node_type == "integration_action":
            return "out"
        if node_type == "command":
            return "out"
        if node_type == "merge":
            return "out"
        if node_type == "human":
            return "approved"
        if node_type == "staging_area":
            return "out"
    else:
        if node_type == "decision" or node_type == "branch":
            return "in"
        if node_type == "start" or node_type == "trigger":
            return ""
        if node_type == "agent_action":
            return "in"
        if node_type == "integration_action":
            return "in"
        if node_type == "command":
            return "in"
        if node_type == "merge":
            return "a"
        if node_type == "human":
            return "in"
        if node_type == "output" or node_type == "end":
            return "in"
        if node_type == "staging_area":
            return "in"
    return "in" if not outgoing else "out"


def _snap(value: float) -> float:
    return round(value / _GRID_SIZE) * _GRID_SIZE


def _layout_nodes(
    template: AgentTemplate,
    nodes: list[TemplateNode],
    drop_x: float,
    drop_y: float,
) -> dict[str, tuple[float, float]]:
    if not nodes:
        return {}
    node_ids = {node.id for node in nodes}
    edges = [edge for edge in template.edges if edge.from_node in node_ids and edge.to_node in node_ids]
    indegree: dict[str, int] = {node.id: 0 for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.from_node].append(edge.to_node)
        indegree[edge.to_node] += 1

    ready = deque([nid for nid, deg in indegree.items() if deg == 0])
    order: list[str] = []
    indegree_local = dict(indegree)
    while ready:
        nid = ready.popleft()
        order.append(nid)
        for nxt in outgoing.get(nid, []):
            indegree_local[nxt] -= 1
            if indegree_local[nxt] == 0:
                ready.append(nxt)

    if len(order) != len(nodes):
        # Fall back to the author-supplied coordinates if the graph has a cycle.
        return {node.id: (node.x, node.y) for node in nodes}

    rank: dict[str, int] = {nid: 0 for nid in node_ids}
    for nid in order:
        for nxt in outgoing.get(nid, []):
            rank[nxt] = max(rank[nxt], rank[nid] + 1)

    layers: dict[int, list[str]] = defaultdict(list)
    for nid in order:
        layers[rank[nid]].append(nid)

    max_per_layer = max((len(layer) for layer in layers.values()), default=1)
    layer_height = max_per_layer * (_NODE_HEIGHT + _GAP_Y)
    coords: dict[str, tuple[float, float]] = {}
    for r, layer in layers.items():
        x = r * (_NODE_WIDTH + _GAP_X)
        layer_total = len(layer) * (_NODE_HEIGHT + _GAP_Y) - _GAP_Y
        y_offset = (layer_height - layer_total) / 2
        for i, nid in enumerate(layer):
            y = y_offset + i * (_NODE_HEIGHT + _GAP_Y)
            coords[nid] = (x, y)

    if not coords:
        return {node.id: (node.x, node.y) for node in nodes}

    xs = [xy[0] for xy in coords.values()]
    ys = [xy[1] for xy in coords.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    center_x = (min_x + max_x + _NODE_WIDTH) / 2
    center_y = (min_y + max_y + _NODE_HEIGHT) / 2
    offset_x = drop_x - center_x
    offset_y = drop_y - center_y
    return {nid: (x + offset_x, y + offset_y) for nid, (x, y) in coords.items()}


def _mermaid_text(text: str) -> str:
    return text.replace("\"", "'").replace("\n", "<br/>").strip()
