"""Core domain types for AgentOrchestra.

These models are the canonical shape of every entity. They are used by the
event store, the worktree manager, the providers, the linter, and the IPC
layer. Pydantic gives us validation at every boundary.

We deliberately omit ``from __future__ import annotations`` here.  Under
PEP 563, Pydantic v2 has to resolve string annotations against module
globals, and discriminated-union / forward-reference cases can produce
PydanticUndefinedAnnotation errors that mention exactly that import line.
Evaluating annotations eagerly avoids the trap.  Other modules in this
service still use the future import where it's safe.
"""

import secrets
import string
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------

# Crockford base32 alphabet (lowercase, no I/L/O/U for human-readable IDs).
_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def short_id(length: int = 8) -> str:
    """Return a short, URL-safe, lowercase ID.

    Used for run, step, branch, and other ephemeral IDs. 8 chars of base32
    gives ~40 bits of entropy — collision-safe at the scale of one user's
    desktop history.
    """
    return "".join(secrets.choice(_CROCKFORD) for _ in range(length))


def long_id(length: int = 26) -> str:
    """Return a longer ID, used for primary keys."""
    return "".join(secrets.choice(_CROCKFORD) for _ in range(length))


def utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# PersonalityCard
# ---------------------------------------------------------------------------


class SandboxTier(StrEnum):
    DEVCONTAINER = "devcontainer"  # default; pure-Python in-process LocalSandbox
    DOCKER = "docker"  # implemented (apps/service/sandbox/docker.py)
    FIRECRACKER = "firecracker"  # not yet implemented (E2B microVM stub only)


class BlastRadiusPolicy(BaseModel):
    """When does an action require human approval?"""

    file_count_threshold: int = 5
    sensitive_path_globs: list[str] = Field(
        default_factory=lambda: [
            ".env",
            ".env.*",
            "**/credentials*",
            "**/secrets/**",
            "**/.git/**",
            "**/id_rsa*",
        ]
    )
    network_egress_requires_approval: bool = True
    deletion_requires_approval: bool = True
    push_requires_approval: bool = True


class CostPolicy(BaseModel):
    soft_cap_usd: float = 1.0
    hard_cap_usd: float = 5.0
    soft_cap_tokens: int = 200_000
    hard_cap_tokens: int = 1_000_000


class CardMode(StrEnum):
    """How the dispatcher should run cards of this type."""

    CHAT = "chat"  # text-only, no worktree (research, QA)
    AGENTIC = "agentic"  # tool-using, worktree-bound (code editing)


class PersonalityCard(BaseModel):
    """A reusable agent role.

    Bundles (system prompt template + provider/model + budget + tool
    allowlist + sandbox tier + blast-radius policy) into a single object
    the user picks at dispatch time.
    """

    id: str = Field(default_factory=long_id)
    name: str
    archetype: str  # e.g. "broad-research", "qa-on-fix"
    description: str
    template_id: str  # FK to InstructionTemplate
    # Provider name registered in the LLMProvider registry.  Bundled
    # values are anthropic / google / openai / ollama; we keep the
    # type as plain ``str`` so tests can register fakes and so the
    # registry can grow without a schema change.
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5"
    mode: CardMode = CardMode.CHAT
    cost: CostPolicy = Field(default_factory=CostPolicy)
    blast_radius: BlastRadiusPolicy = Field(default_factory=BlastRadiusPolicy)
    sandbox_tier: SandboxTier = SandboxTier.DEVCONTAINER
    tool_allowlist: list[str] = Field(default_factory=list)  # empty = all bundled tools
    # Ordered fallback list of {"provider": ..., "model": ...} used on
    # rate-limit / 5xx errors before declaring the run failed.
    fallbacks: list[dict[str, str]] = Field(default_factory=list)
    # When true and this card runs to REVIEWING, automatically dispatch
    # a QA-on-fix run targeting this run's diff.
    auto_qa: bool = False
    # When true (agentic only), the dispatcher generates a written plan
    # first and waits for human approval before letting the agent take
    # any tool action.  Approval is via runs.approve_plan.
    requires_plan: bool = False
    stale_minutes: int = 60
    max_commits_per_run: int = 50
    max_turns: int = 12
    skip_pre_commit_hooks: bool = False
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("archetype")
    @classmethod
    def _archetype_slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c == "-" for c in v):
            raise ValueError("archetype must be a slug (alphanumeric + hyphens)")
        return v.lower()


# ---------------------------------------------------------------------------
# InstructionTemplate
# ---------------------------------------------------------------------------


class TemplateVariable(BaseModel):
    name: str
    label: str
    kind: Literal["string", "text", "number", "bool", "files", "urls", "checkboxes"]
    required: bool = True
    default: Any = None
    help: str | None = None
    options: list[str] | None = None  # for checkboxes/select


class InstructionTemplate(BaseModel):
    """A versioned Banks-style prompt template.

    The body is Jinja2 with optional front-matter metadata. The
    composer wizard renders forms from the variable list.
    """

    id: str = Field(default_factory=long_id)
    name: str
    archetype: str
    body: str  # Jinja2
    variables: list[TemplateVariable] = Field(default_factory=list)
    version: int = 1
    content_hash: str  # sha256 of body, set by store on save
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Instruction (a concrete rendering)
# ---------------------------------------------------------------------------


class Instruction(BaseModel):
    id: str = Field(default_factory=long_id)
    template_id: str
    template_version: int
    card_id: str
    rendered_text: str
    variables: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class RunState(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    MERGED = "merged"
    REJECTED = "rejected"
    ABORTED = "aborted"


# Canonical state-transition map. Used by both Run and Branch state machines.
RUN_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.QUEUED: {RunState.PLANNING, RunState.ABORTED},
    RunState.PLANNING: {RunState.AWAITING_APPROVAL, RunState.EXECUTING, RunState.ABORTED},
    RunState.AWAITING_APPROVAL: {RunState.EXECUTING, RunState.ABORTED},
    RunState.EXECUTING: {RunState.REVIEWING, RunState.ABORTED, RunState.AWAITING_APPROVAL},
    RunState.REVIEWING: {RunState.MERGED, RunState.REJECTED, RunState.ABORTED},
    RunState.MERGED: set(),
    RunState.REJECTED: set(),
    RunState.ABORTED: set(),
}


class Run(BaseModel):
    id: str = Field(default_factory=lambda: short_id(8))
    workspace_id: str | None = None
    card_id: str
    instruction_id: str
    branch_id: str | None = None
    state: RunState = RunState.QUEUED
    state_changed_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    cost_usd: float = 0.0
    cost_tokens: int = 0
    last_plan_turn: int | None = None
    error: str | None = None

    @field_validator("workspace_id", "branch_id", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        # SQLite + foreign-key enforcement rejects '' as if it were a real
        # row id; normalise blanks to NULL at the model boundary so any
        # call site that passes "" stops triggering FK violations.
        if v == "":
            return None
        return v


# ---------------------------------------------------------------------------
# Step (single LLM call or tool call inside a Run)
# ---------------------------------------------------------------------------


class StepKind(StrEnum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    PLAN = "plan"
    SUMMARY = "summary"


class Step(BaseModel):
    id: str = Field(default_factory=lambda: short_id(10))
    run_id: str
    seq: int
    kind: StepKind
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Branch (the worktree state)
# ---------------------------------------------------------------------------


class BranchState(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    AWAITING_REVIEW = "awaiting_review"
    MERGING = "merging"
    CONFLICTED = "conflicted"
    MERGED = "merged"
    REJECTED = "rejected"
    ABANDONED = "abandoned"
    STALE = "stale"
    CLEANED = "cleaned"


# Per the WorktreeManager design doc.
BRANCH_TRANSITIONS: dict[BranchState, set[BranchState]] = {
    BranchState.CREATED: {BranchState.ACTIVE, BranchState.ABANDONED},
    BranchState.ACTIVE: {
        BranchState.PAUSED,
        BranchState.AWAITING_REVIEW,
        BranchState.STALE,
        BranchState.ABANDONED,
    },
    BranchState.PAUSED: {BranchState.ACTIVE, BranchState.ABANDONED},
    BranchState.AWAITING_REVIEW: {
        BranchState.MERGING,
        BranchState.REJECTED,
        BranchState.ACTIVE,
        BranchState.ABANDONED,
    },
    BranchState.MERGING: {BranchState.MERGED, BranchState.CONFLICTED},
    BranchState.CONFLICTED: {BranchState.MERGED, BranchState.ABANDONED},
    BranchState.MERGED: {BranchState.CLEANED},
    BranchState.REJECTED: {BranchState.CLEANED},
    BranchState.STALE: {BranchState.ABANDONED, BranchState.ACTIVE},
    BranchState.ABANDONED: {BranchState.CLEANED},
    BranchState.CLEANED: set(),
}


class Branch(BaseModel):
    id: str = Field(default_factory=lambda: short_id(8))
    run_id: str
    workspace_id: str
    base_ref: str  # SHA at fork
    base_branch_name: str
    agent_branch_name: str
    worktree_path: str  # absolute
    state: BranchState = BranchState.CREATED
    state_changed_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
    last_commit_sha: str | None = None
    last_commit_at: datetime | None = None
    process_pid: int | None = None
    include_uncommitted: bool = False
    notes: str = ""

    @field_validator("agent_branch_name")
    @classmethod
    def _branch_name(cls, v: str) -> str:
        if not v.startswith("agent/"):
            raise ValueError("agent_branch_name must start with 'agent/'")
        # Reject any character outside the allowed set.
        allowed = string.ascii_lowercase + string.digits + "/-"
        if not all(c in allowed for c in v):
            raise ValueError("agent_branch_name contains illegal characters")
        return v


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class Workspace(BaseModel):
    """A registered local git working tree.

    ``WorktreeManager.register_workspace`` validates the path is a
    working tree (not bare) and the row is the cwd that agents bound
    to this workspace will run in.  Cloned-from-URL workspaces land
    here too via ``workspaces.clone``.
    """

    id: str = Field(default_factory=long_id)
    name: str
    repo_path: str  # absolute path on disk; cwd for agent CLI subprocesses
    default_base_branch: str = "main"  # detected from origin/HEAD on clone, else "main"
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Artifact (diff, file, summary, handoff card, etc.)
# ---------------------------------------------------------------------------


class ArtifactKind(StrEnum):
    DIFF = "diff"
    FILE = "file"
    SUMMARY = "summary"
    HANDOFF_CARD = "handoff_card"
    TRANSCRIPT = "transcript"
    PLAN = "plan"
    QA_VERDICT = "qa_verdict"


class Artifact(BaseModel):
    id: str = Field(default_factory=long_id)
    run_id: str
    step_id: str | None = None
    kind: ArtifactKind
    title: str
    body: str
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"
    EXPIRED = "expired"


class Approval(BaseModel):
    id: str = Field(default_factory=long_id)
    run_id: str
    reason: str
    risk_signals: dict[str, Any] = Field(default_factory=dict)
    decision: ApprovalDecision = ApprovalDecision.PENDING
    requested_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None
    decided_by: str = "user"
    note: str | None = None


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


class OutcomeKind(StrEnum):
    MERGED = "merged"
    REJECTED = "rejected"
    ABANDONED = "abandoned"


class Outcome(BaseModel):
    id: str = Field(default_factory=long_id)
    run_id: str
    kind: OutcomeKind
    rationale: str = ""
    final_cost_usd: float = 0.0
    final_cost_tokens: int = 0
    duration_seconds: int = 0
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Event (the unified normalized event the store records)
# ---------------------------------------------------------------------------


class EventSource(StrEnum):
    DISPATCH_CHAT = "dispatch:chat"
    DISPATCH_RUN = "dispatch:run"
    INGEST_CLAUDE_JSONL = "ingest:claude_jsonl"
    INGEST_CLAUDE_HOOK = "ingest:claude_hook"
    INGEST_GEMINI_OTEL = "ingest:gemini_otel"
    INGEST_SUBPROCESS = "ingest:subprocess"
    INGEST_SDK = "ingest:sdk"
    SYSTEM = "system"


class EventKind(StrEnum):
    # Run lifecycle
    RUN_STARTED = "run.started"
    RUN_STATE_CHANGED = "run.state_changed"
    RUN_COMPLETED = "run.completed"
    # Step
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    LLM_CALL_COMPLETED = "llm.call_completed"
    TOOL_CALLED = "tool.called"
    # Branch / worktree
    WORKTREE_CREATED = "worktree.created"
    WORKTREE_STATE_CHANGED = "worktree.state_changed"
    WORKTREE_CLEANED = "worktree.cleaned"
    COMMIT_CREATED = "commit.created"
    PANIC_RESET = "panic.reset"
    EXTERNAL_CHANGE = "external.change"
    HOOK_MUTATED_FILES = "hook.mutated_files"
    # Approvals
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"
    # Ingestion
    INGEST_RECEIVED = "ingest.received"
    # Health
    SERVICE_STARTED = "service.started"
    SERVICE_STOPPED = "service.stopped"
    CLEANUP_FAILED = "cleanup.failed"
    # Drones / Agents chat
    DRONE_TOKEN_DELTA = "drone.token_delta"
    # Flow Canvas
    FLOW_NODE_QUEUED = "flow.node.queued"
    FLOW_NODE_STARTED = "flow.node.started"
    FLOW_NODE_TOKEN_DELTA = "flow.node.token_delta"
    FLOW_NODE_WAITING = "flow.node.waiting"
    FLOW_NODE_RELEASED = "flow.node.released"
    FLOW_NODE_TIMED_OUT = "flow.node.timed_out"
    FLOW_NODE_REJECTED = "flow.node.rejected"
    FLOW_NODE_BLOCKED = "flow.node.blocked"
    FLOW_NODE_COMPLETED = "flow.node.completed"
    FLOW_NODE_FAILED = "flow.node.failed"
    FLOW_NODE_SKIPPED = "flow.node.skipped"
    FLOW_NODE_HUMAN_PENDING = "flow.node.human_pending"
    FLOW_COMPLETED = "flow.completed"


class Event(BaseModel):
    id: str = Field(default_factory=long_id)
    seq: int = 0  # assigned by store on insert
    occurred_at: datetime = Field(default_factory=utc_now)
    source: EventSource
    kind: EventKind
    run_id: str | None = None
    step_id: str | None = None
    branch_id: str | None = None
    workspace_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    text: str = ""  # FTS-indexable text representation


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IllegalTransitionError(Exception):
    def __init__(self, frm: str, to: str) -> None:
        super().__init__(f"Illegal state transition: {frm!r} -> {to!r}")
        self.frm = frm
        self.to = to


class WorktreeError(Exception):
    pass


class ProviderError(Exception):
    pass


class ToolError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_branch_transition(frm: BranchState, to: BranchState) -> None:
    if to not in BRANCH_TRANSITIONS[frm]:
        raise IllegalTransitionError(frm.value, to.value)


def assert_run_transition(frm: RunState, to: RunState) -> None:
    if to not in RUN_TRANSITIONS[frm]:
        raise IllegalTransitionError(frm.value, to.value)


# ---------------------------------------------------------------------------
# Drones — see docs/DRONE_MODEL.md for the full design.
#
# A *drone* is a messenger our app dispatches.  It carries a frozen
# blueprint config + an action's live state to whichever external AI
# endpoint it's pointed at — Claude, Gemini, Ollama, future MCP
# surfaces.  We don't classify the endpoint as agent-or-not; it's just
# a target the drone messages.
#
# Blueprint  = template (operator-only writes; repo-portable).
# Action     = deployed instance (carries transcript, attachments,
#              optional workspace, one-off skill / reference layers).
# ---------------------------------------------------------------------------


class DroneRole(StrEnum):
    """Authority role on a blueprint, frozen with the action snapshot
    at deploy time.  The orchestrator's ``_check_authority`` helper
    gates cross-action mutation RPCs against this matrix:

    | Role       | Self | Append refs to peer | Append atts to peer | Append skills to peer | Read peer |
    |------------|------|---------------------|---------------------|------------------------|-----------|
    | worker     | ✅   | ❌                  | ❌                  | ❌                     | ✅        |
    | supervisor | ✅   | ✅                  | ✅                  | ✅                     | ✅        |
    | courier    | ✅   | ✅                  | ❌                  | ❌                     | ✅        |
    | auditor    | ❌   | ❌                  | ❌                  | ❌                     | ✅        |

    Auditor cannot mutate even its OWN action — it's read-only by
    construction.  Suitable for "watch-only" drones that summarise
    what's happening across other actions without participating.

    Operators don't define new roles in v1; the four above ship and
    we extend later if a real need arises.
    """

    WORKER = "worker"
    SUPERVISOR = "supervisor"
    COURIER = "courier"
    AUDITOR = "auditor"


class DroneBlueprint(BaseModel):
    """Frozen template for deploying drones.

    The operator (human) is the sole creator and editor; drones
    themselves never modify their own blueprint.  Blueprints are
    repo-portable — workspace binding is action-only, picked at
    deploy time, so one blueprint deploys against many repos.
    """

    id: str = Field(default_factory=long_id)
    name: str
    description: str = ""
    role: DroneRole = DroneRole.WORKER
    provider: str  # 'claude-cli' / 'gemini-cli' / 'codex-cli' / 'anthropic' / 'google' / 'ollama'
    model: str
    # Operator-typed persona / tone / role description.  Goes into
    # the system prompt verbatim.  The mode prompts (Coding / General
    # Chat / File / Image) from apps/gui/presets stack on top of this.
    system_persona: str = ""
    # Default skill tokens (e.g. "/research-deep").  Action can layer
    # additional one-off tokens; cannot remove these defaults.
    skills: list[str] = Field(default_factory=list)
    # Default reference blueprint ids — every action deployed from
    # this blueprint inherits a reference to whatever the latest
    # action of each listed blueprint was.  Empty for most blueprints.
    reference_blueprint_ids: list[str] = Field(default_factory=list)
    # For ``provider == "browser"`` drones: the URL the GUI opens when
    # the operator clicks Send.  Pre-populated by the editor to
    # ``https://claude.ai/new`` but the operator can change it to
    # ChatGPT / Gemini / anything URL-addressable.  Ignored when
    # ``provider`` is anything else.  See docs/BROWSER_PROVIDER_PLAN.md.
    chat_url: str | None = None
    # Optimistic-concurrency token; bumped on every update.  The
    # blueprints.update RPC can take an ``expected_version`` to detect
    # racing edits the same way ``flows.update`` does.
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DroneAction(BaseModel):
    """A deployed drone — instance of a blueprint, ready to chat.

    Holds runtime state: transcript, attachments, workspace binding,
    one-off skill / reference layers.  Inherits everything else from
    the blueprint snapshot stored at deploy time.
    """

    id: str = Field(default_factory=long_id)
    blueprint_id: str
    # Frozen copy of the blueprint at deploy time.  Edits to the
    # blueprint AFTER this action was deployed never reach this
    # action — operator gets predictable behaviour and any in-flight
    # conversations stay coherent with what they were started with.
    blueprint_snapshot: dict[str, Any] = Field(default_factory=dict)
    # Optional workspace binding — operator picks at deploy.  Same
    # blueprint deploys against many repos.  None = chat-only.
    workspace_id: str | None = None
    # Layered on top of the blueprint snapshot's skills.  Action can
    # ADD a one-off /token for this conversation only; cannot remove
    # blueprint defaults.
    additional_skills: list[str] = Field(default_factory=list)
    # One-off cross-action references.  Populated by the operator (or
    # by a Supervisor/Courier drone via append_reference).
    additional_reference_action_ids: list[str] = Field(default_factory=list)
    transcript: list[dict[str, str]] = Field(default_factory=list)
    # For ``provider == "browser"`` drones: the specific conversation
    # URL captured from the first paste-back's clipboard source.
    # Subsequent pastes route to this drone only if their source URL
    # matches.  The GUI also renders a link-back icon that
    # ``webbrowser.open()``s this URL.  None until the first paste; can
    # be cleared and re-bound via ``drones.bind_chat_url``.  See
    # docs/BROWSER_PROVIDER_PLAN.md.
    bound_chat_url: str | None = None

    # Instance-specific name override (e.g. for canvas topology).
    # If None, the UI falls back to the blueprint snapshot's name.
    name: str | None = None

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def effective_role(self) -> DroneRole:
        """Role inherited from the snapshotted blueprint.

        Falls back to ``WORKER`` when the snapshot lacks a role (e.g.
        an action seeded from a future-format blueprint we don't fully
        understand).  Better to default to the most-restricted role
        than to escalate accidentally.
        """
        raw = (self.blueprint_snapshot or {}).get("role")
        try:
            return DroneRole(raw) if raw else DroneRole.WORKER
        except ValueError:
            return DroneRole.WORKER

    @property
    def effective_skills(self) -> list[str]:
        """Concatenation of blueprint skills + action's one-off layer."""
        bp_skills = list((self.blueprint_snapshot or {}).get("skills") or [])
        return bp_skills + list(self.additional_skills)


class BlueprintVersionConflict(Exception):
    """Raised when an optimistic update_blueprint lost the race.

    The caller should re-fetch the blueprint and reapply edits — same
    pattern as ``FlowVersionConflict`` for flows.
    """


class FlowState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    ABORTED = "aborted"


class Flow(BaseModel):
    """A saved orchestration graph.

    The ``nodes`` and ``edges`` lists are the canonical payload — both
    are stored as one JSON column in SQLite for simplicity (flows are
    small, ~50 KB even for hundred-node graphs, and edits are atomic).
    Each node / edge is an opaque ``dict[str, Any]`` rather than a
    typed Pydantic class because the canvas needs to round-trip
    arbitrary GUI metadata (positions, palette ids) the executor
    doesn't read; promoting them to typed models is on the roadmap.
    """

    id: str = Field(default_factory=long_id)
    name: str
    description: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    # Draft flows are scratchpad canvases — operator can edit, drop
    # nodes, simulate visually, but ``flows.dispatch`` refuses to
    # run them.  Promoting to Live = setting this to False.
    is_draft: bool = False
    
    # Flights are pre-set templates of grouped agents. They act as reusable
    # architectural maps that can be deployed as cohesive units.
    is_flight: bool = False
    
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FlowRun(BaseModel):
    """A single execution of a ``Flow``.

    ``state`` walks PENDING → RUNNING → (FINISHED | FAILED | ABORTED).
    ``node_outputs`` is keyed by node id and accumulates each node's
    last assistant text as the run progresses.  ``error`` is only
    populated when ``state is FAILED``.
    """

    id: str = Field(default_factory=short_id)
    flow_id: str
    state: FlowState = FlowState.PENDING
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    # node_id -> last assistant text the node produced (empty string for
    # control nodes that don't emit text).  Persisted as JSON.
    node_outputs: dict[str, str] = Field(default_factory=dict)
    # Set only when state transitions to FAILED; surfaces to the GUI
    # via flow.completed.
    error: str | None = None


class Skill(BaseModel):
    """A reusable agent skill template.

    Skills are 'superpowers' that can be picked and added to blueprints
    or individual drone actions.  They provide high-level instructions
    and capability hints to the external AI endpoint.
    """

    id: str = Field(default_factory=long_id)
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


# --- Prepopulated Agent Skills --------------------------------------------

AGENT_SKILLS: tuple[tuple[str, str], ...] = (
    ("research-deep", "In-depth web and file-based research with citations."),
    ("code-review", "Detailed code analysis for bugs, logic, and style."),
    ("bug-hunt", "Systematic search for logical flaws and edge cases."),
    ("doc-gen", "Comprehensive technical documentation generation."),
    ("unit-test", "Automated test case creation and verification."),
    ("perf-audit", "Performance bottleneck and resource leak analysis."),
    ("security-scan", "Vulnerability assessment and security hardening."),
    ("refactor-dry", "Logic consolidation and DRY principle enforcement."),
    ("api-design", "RESTful and idiomatic API specification design."),
    ("data-extract", "Structured data parsing from unstructured raw text."),
    ("summarize", "Concise extraction of key points from large contexts."),
    ("translate", "Idiomatic translation between programming languages."),
    ("ux-audit", "Accessibility and usability review for UI components."),
    ("db-optim", "SQL query and database schema performance tuning."),
    ("ci-pipeline", "DevOps and CI/CD configuration (GitHub Actions, etc)."),
    ("git-fix", "Resolving complex merge conflicts and history cleanup."),
    ("copy-edit", "Grammar, tone, and clarity refinement for prose."),
    ("market-analysis", "Competitor research and industry trend identification."),
    ("tech-stack", "Architectural recommendations for new project builds."),
    ("root-cause", "Post-mortem analysis of complex system failures."),
)


def is_path_inside(child: Path, parent: Path) -> bool:
    """Return True if `child` is inside `parent` after resolving symlinks.

    Used by the WorktreeManager and tool-call validators to block path
    traversal escapes.
    """
    try:
        child_resolved = child.resolve(strict=False)
        parent_resolved = parent.resolve(strict=False)
        child_resolved.relative_to(parent_resolved)
        return True
    except (ValueError, OSError):
        return False
