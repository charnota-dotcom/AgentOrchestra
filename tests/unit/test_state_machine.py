"""Branch and Run state-machine transitions."""

from __future__ import annotations

import pytest

from apps.service.types import (
    BRANCH_TRANSITIONS,
    BranchState,
    IllegalTransitionError,
    RUN_TRANSITIONS,
    RunState,
    assert_branch_transition,
    assert_run_transition,
)


def test_every_branch_state_has_a_row() -> None:
    for state in BranchState:
        assert state in BRANCH_TRANSITIONS


def test_every_run_state_has_a_row() -> None:
    for state in RunState:
        assert state in RUN_TRANSITIONS


def test_terminal_branch_states_have_no_outgoing() -> None:
    assert BRANCH_TRANSITIONS[BranchState.CLEANED] == set()


def test_legal_transition_passes() -> None:
    assert_branch_transition(BranchState.CREATED, BranchState.ACTIVE)
    assert_branch_transition(BranchState.ACTIVE, BranchState.AWAITING_REVIEW)
    assert_branch_transition(BranchState.AWAITING_REVIEW, BranchState.MERGING)
    assert_branch_transition(BranchState.MERGING, BranchState.MERGED)
    assert_branch_transition(BranchState.MERGED, BranchState.CLEANED)


def test_illegal_transition_raises() -> None:
    with pytest.raises(IllegalTransitionError):
        assert_branch_transition(BranchState.CREATED, BranchState.MERGED)
    with pytest.raises(IllegalTransitionError):
        assert_branch_transition(BranchState.CLEANED, BranchState.ACTIVE)


def test_run_legal_transition() -> None:
    assert_run_transition(RunState.QUEUED, RunState.PLANNING)
    assert_run_transition(RunState.PLANNING, RunState.EXECUTING)
    assert_run_transition(RunState.EXECUTING, RunState.REVIEWING)
    assert_run_transition(RunState.REVIEWING, RunState.MERGED)


def test_run_illegal_transition() -> None:
    with pytest.raises(IllegalTransitionError):
        assert_run_transition(RunState.QUEUED, RunState.MERGED)
