import pytest
import json
from pathlib import Path
from typing import Any
from apps.service.types import Run, ToolError
from apps.service.dispatch.tools import WorktreeToolset
from apps.service.dispatch.dispatcher import AUTONOMOUS_TURN_LIMIT, _is_plan_file_target


def test_is_plan_file_target_normalizes_relative_and_windows_paths() -> None:
    assert _is_plan_file_target("PLAN.md")
    assert _is_plan_file_target("./PLAN.md")
    assert _is_plan_file_target(".\\PLAN.md")
    assert not _is_plan_file_target("docs/PLAN.md")
    assert not _is_plan_file_target("")


def test_autonomous_turn_limit_is_hard_capped_to_15() -> None:
    assert AUTONOMOUS_TURN_LIMIT == 15

@pytest.mark.asyncio
async def test_shadow_plan_guard_logic(tmp_path: Path):
    """
    Verification Task 2 & 3:
    - Verify that calling execute with a 'modifying tool' fails with ToolError 
      if last_plan_turn is None or too old.
    - Verify that writing to 'PLAN.md' updates the internal planning state 
      and allows subsequent modification tool calls.
    """
    # Setup
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    
    # Mock a Run
    run = Run(
        id="test-run", 
        card_id="test-card", 
        instruction_id="test-instr",
        workspace_id="test-ws"
    )
    run.last_plan_turn = None
    
    turn_count = 1
    planned_this_turn = False
    
    def on_plan_updated():
        nonlocal planned_this_turn
        planned_this_turn = True
        
    MOD_TOOLS = {"replace", "write_file", "delete_file", "run_shell_command"}
    
    def guard(tool_name: str, params: dict[str, Any]) -> None:
        if tool_name in MOD_TOOLS:
            # Writing or replacing PLAN.md is always allowed (bootstrap/update).
            target = params.get("path") or params.get("file_path")
            if tool_name in {"write_file", "replace"} and target == "PLAN.md":
                return

            if turn_count - (run.last_plan_turn or -1) > 1:
                raise ToolError(
                    "403 Shadow-Plan Violation: You must document your intent "
                    "in PLAN.md before modifying code."
                )

    toolset = WorktreeToolset(
        worktree=worktree,
        on_plan_updated=on_plan_updated,
        guard=guard,
    )

    # 1. Verify failure with modifying tool when last_plan_turn is None
    with pytest.raises(ToolError) as excinfo:
        await toolset.execute(
            "call-1", 
            "write_file", 
            {"path": "foo.py", "content": "print(1)"}
        )
    assert "Shadow-Plan Violation" in str(excinfo.value)

    # 1.1 Verify run_shell_command is also guarded
    with pytest.raises(ToolError) as excinfo:
        await toolset.execute(
            "call-1-shell", 
            "run_shell_command", 
            {"command": "rm -rf /"}
        )
    assert "Shadow-Plan Violation" in str(excinfo.value)

    # 2. Verify non-modifying tools are NOT guarded (e.g. read_file)
    # First create the file so read_file doesn't fail on missing file
    (worktree / "foo.py").write_text("print(0)")
    await toolset.execute("call-2", "read_file", {"path": "foo.py"})
    # Should not raise ToolError

    # 3. Verify that writing to PLAN.md updates planning state
    await toolset.execute(
        "call-3", 
        "write_file", 
        {"path": "PLAN.md", "content": "I will fix the bug."}
    )
    assert planned_this_turn is True
    
    # Simulate dispatcher updating last_plan_turn at turn_end
    if planned_this_turn:
        run.last_plan_turn = turn_count
        planned_this_turn = False
    
    # 4. Verify that subsequent modification tool calls are allowed in the next turn
    turn_count = 2
    await toolset.execute(
        "call-4", 
        "write_file", 
        {"path": "foo.py", "content": "print(1)"}
    )
    # Should not raise ToolError
    
    # 5. Verify it fails again if we advance turn_count without updating PLAN.md
    turn_count = 3 # turn_count(3) - last_plan_turn(1) = 2 > 1
    with pytest.raises(ToolError) as excinfo:
        await toolset.execute(
            "call-5", 
            "write_file", 
            {"path": "bar.py", "content": "print(2)"}
        )
    assert "Shadow-Plan Violation" in str(excinfo.value)
    
    # 6. Verify that updating PLAN.md again allows modifications
    await toolset.execute(
        "call-6", 
        "write_file", 
        {"path": "PLAN.md", "content": "I am updating the plan."}
    )
    assert planned_this_turn is True
    run.last_plan_turn = turn_count
    planned_this_turn = False
    
    await toolset.execute(
        "call-7", 
        "write_file", 
        {"path": "bar.py", "content": "print(2)"}
    )
    # Should not raise ToolError
