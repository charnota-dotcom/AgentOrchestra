"""Shared flow-node vocabulary and compatibility helpers."""

from __future__ import annotations

from typing import Any


NODE_TYPE_ALIASES: dict[str, str] = {
    "agent": "reaper",
    "reaper": "reaper",
    "drone_action": "fpv_drone",
    "fpv_drone": "fpv_drone",
    "staging-area": "staging_area",
    "staging_area": "staging_area",
    "integration-action": "integration_action",
    "integration_action": "integration_action",
}


NODE_TYPE_LABELS: dict[str, str] = {
    "trigger": "Trigger",
    "branch": "Branch",
    "merge": "Merge",
    "human": "Human",
    "output": "Output",
    "reaper": "Reaper",
    "fpv_drone": "FPV Drone",
    "integration_action": "Machine Action",
    "staging_area": "Staging Area",
    "consensus": "Consensus",
}


def canonical_node_type(raw: str | None) -> str:
    if not raw:
        return ""
    return NODE_TYPE_ALIASES.get(raw, raw)


def node_display_label(raw: str | None) -> str:
    canonical = canonical_node_type(raw)
    if canonical in NODE_TYPE_LABELS:
        return NODE_TYPE_LABELS[canonical]
    return (raw or "").replace("_", " ").replace("-", " ").title()


def normalize_flow_node(node: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(node)
    normalized["type"] = canonical_node_type(str(normalized.get("type") or ""))
    if normalized["type"] == "staging-area":
        normalized["type"] = "staging_area"
    return normalized


def normalize_flow_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_flow_node(n) for n in nodes]
