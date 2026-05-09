"""A2A — Agent-to-Agent protocol stub.

Cross-machine agent comms.  V4 ships the wire format + a thin HTTP
transport so two AgentOrchestra instances on the same network can:

- discover each other's capabilities (``GET /a2a/capabilities``)
- delegate a Run (``POST /a2a/runs``) and stream its events back
- post a HandoffCard authored elsewhere (``POST /a2a/handoff``)

The cross-machine runtime path (peer discovery, mutual auth, retry
semantics, NATS-backed fan-out) is V5; this commit only fixes the
schema so we can layer on later without breaking older peers.

Auth: simple bearer token per peer, stored in the keyring under
``a2a:<peer-id>``.  No mutual TLS in V4 — acceptable for LAN use,
not the open internet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from apps.service.types import long_id, utc_now

A2A_VERSION = "0.1.0"


class PeerCapabilities(BaseModel):
    """Reply to GET /a2a/capabilities."""

    a2a_version: str = A2A_VERSION
    peer_id: str
    name: str
    providers: list[str] = Field(default_factory=list)
    archetypes: list[str] = Field(default_factory=list)
    accepts_handoffs: bool = True
    advertised_at: datetime = Field(default_factory=utc_now)


class RunDelegation(BaseModel):
    """POST /a2a/runs — request to run an instruction on a peer."""

    request_id: str = Field(default_factory=long_id)
    archetype: str
    rendered_text: str
    workspace_id: str | None = None
    return_callback_url: str | None = None
    requested_at: datetime = Field(default_factory=utc_now)


class RunDelegationAck(BaseModel):
    request_id: str
    accepted: bool
    remote_run_id: str | None = None
    reason: str | None = None


class HandoffCardEnvelope(BaseModel):
    """POST /a2a/handoff — a HandoffCard authored on a peer.

    Mirrors the local HandoffCard artifact so a remote agent can
    pick up where another left off.
    """

    handoff_id: str = Field(default_factory=long_id)
    origin_peer_id: str
    origin_run_id: str
    target_archetype: str | None = None
    goal: str
    current_state: str
    blockers: list[str] = Field(default_factory=list)
    next_best_action: str
    artifacts: list[dict] = Field(default_factory=list)
    posted_at: datetime = Field(default_factory=utc_now)


class A2AEvent(BaseModel):
    """Event mirrored from a peer's bus.  Subset of the local Event
    schema; the wire format is intentionally narrower so peers running
    different orchestrator versions stay interoperable.
    """

    event_id: str
    occurred_at: datetime
    kind: Literal[
        "run.started",
        "run.state_changed",
        "run.completed",
        "step.completed",
        "tool.called",
        "approval.requested",
        "approval.granted",
        "approval.denied",
    ]
    run_id: str
    text: str = ""
    payload: dict = Field(default_factory=dict)
