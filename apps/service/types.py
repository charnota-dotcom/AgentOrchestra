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
    DEVCONTAINER = "devcontainer"  # V1 default
    DOCKER = "docker"  # V2
    FIRECRACKER = "firecracker"  # V3


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
    id: str = Field(default_factory=long_id)
    name: str
    repo_path: str  # absolute
    default_base_branch: str = "main"
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
    # Flow Canvas
    FLOW_NODE_QUEUED = "flow.node.queued"
    FLOW_NODE_STARTED = "flow.node.started"
    FLOW_NODE_TOKEN_DELTA = "flow.node.token_delta"
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_branch_transition(frm: BranchState, to: BranchState) -> None:
    if to not in BRANCH_TRANSITIONS[frm]:
        raise IllegalTransitionError(frm.value, to.value)


def assert_run_transition(frm: RunState, to: RunState) -> None:
    if to not in RUN_TRANSITIONS[frm]:
        raise IllegalTransitionError(frm.value, to.value)


class FlowState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    ABORTED = "aborted"


class Flow(BaseModel):
    """A saved orchestration graph.

    The `payload` is a Pydantic-validated nested structure (nodes +
    edges) but stored as JSON in SQLite for simplicity — flows are
    small (~50 KB even for hundred-node graphs) and edits are atomic.
    """

    id: str = Field(default_factory=long_id)
    name: str
    description: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FlowRun(BaseModel):
    """A single execution of a Flow."""

    id: str = Field(default_factory=short_id)
    flow_id: str
    state: FlowState = FlowState.PENDING
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    node_outputs: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


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
