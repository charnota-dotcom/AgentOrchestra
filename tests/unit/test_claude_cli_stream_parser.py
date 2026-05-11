"""parse_stream_json — defensive unit tests for the claude-cli
stream-json parser introduced in PR ``claude/claude-cli-stream-json``.

These tests are pure parser tests — they construct synthetic
stream-json strings and assert the ``ParsedEvent`` shape that comes
out.  No subprocess, no RPC.
"""

from __future__ import annotations

import json

from apps.service.providers.claude_cli.stream_parser import (
    ParsedEvent,
    parse_stream_json,
)


def _line(obj: dict) -> str:
    return json.dumps(obj)


def test_empty_input_yields_no_events() -> None:
    assert parse_stream_json("") == []
    assert parse_stream_json("\n\n") == []


def test_pure_text_assistant_message() -> None:
    raw = _line(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]},
        }
    )
    events = parse_stream_json(raw)
    assert len(events) == 1
    assert events[0].kind == "text"
    assert events[0].text == "hi there"


def test_tool_use_and_tool_result_pair_share_step() -> None:
    raw = "\n".join(
        [
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "Read",
                                "input": {"file_path": "/etc/hosts"},
                            }
                        ],
                    },
                }
            ),
            _line(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": "127.0.0.1 localhost",
                                "is_error": False,
                            }
                        ],
                    },
                }
            ),
        ]
    )
    events = parse_stream_json(raw)
    kinds = [e.kind for e in events]
    assert kinds == ["tool_call", "tool_result"]
    call, result = events
    assert call.tool_name == "Read"
    assert call.tool_input == {"file_path": "/etc/hosts"}
    assert call.tool_id == "toolu_1"
    assert call.is_subagent is False
    assert call.step == 1
    assert result.tool_id == "toolu_1"
    assert result.tool_output == "127.0.0.1 localhost"
    assert result.is_error is False
    # tool_result borrows the step of its matching call so the GUI can
    # pair them visually.
    assert result.step == 1


def test_task_tool_use_flagged_as_subagent() -> None:
    raw = _line(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_2",
                        "name": "Task",
                        "input": {"description": "spawn sub-agent"},
                    }
                ],
            },
        }
    )
    events = parse_stream_json(raw)
    assert len(events) == 1
    assert events[0].kind == "tool_call"
    assert events[0].is_subagent is True


def test_tool_result_content_can_be_list_of_blocks() -> None:
    raw = _line(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_3",
                        "content": [
                            {"type": "text", "text": "part1 "},
                            {"type": "text", "text": "part2"},
                        ],
                    }
                ],
            },
        }
    )
    events = parse_stream_json(raw)
    assert events[0].kind == "tool_result"
    assert events[0].tool_output == "part1 part2"


def test_is_error_flag_passes_through() -> None:
    raw = _line(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_4",
                        "content": "bash: command not found",
                        "is_error": True,
                    }
                ],
            },
        }
    )
    events = parse_stream_json(raw)
    assert events[0].is_error is True


def test_result_envelope_emits_usage_and_finish() -> None:
    raw = "\n".join(
        [
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                    },
                }
            ),
            _line(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "done",
                    "usage": {"input_tokens": 123, "output_tokens": 7},
                    "total_cost_usd": 0.0042,
                }
            ),
        ]
    )
    events = parse_stream_json(raw)
    kinds = [e.kind for e in events]
    assert kinds == ["text", "usage", "finish"]
    usage_ev = events[1]
    assert usage_ev.usage == {"input_tokens": 123, "output_tokens": 7}
    assert usage_ev.cost_usd == 0.0042


def test_result_backfills_text_when_stream_was_silent() -> None:
    # Some CLI versions only emit the final answer in the result
    # envelope.  Make sure we still surface a text event so the
    # session.send() consumer gets the assistant text.
    raw = _line(
        {
            "type": "result",
            "subtype": "success",
            "result": "only-in-result",
            "usage": {},
            "total_cost_usd": 0.0,
        }
    )
    events = parse_stream_json(raw)
    kinds = [e.kind for e in events]
    assert kinds == ["text", "usage", "finish"]
    assert events[0].text == "only-in-result"


def test_result_text_as_list_of_content_blocks() -> None:
    raw = _line(
        {
            "type": "result",
            "subtype": "success",
            "result": [
                {"type": "text", "text": "alpha "},
                {"type": "text", "text": "beta"},
            ],
            "usage": {},
            "total_cost_usd": 0.0,
        }
    )
    events = parse_stream_json(raw)
    assert events[0].kind == "text"
    assert events[0].text == "alpha beta"


def test_malformed_json_line_is_skipped_not_fatal() -> None:
    raw = "\n".join(
        [
            "this is not json",
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "still here"}],
                    },
                }
            ),
        ]
    )
    events = parse_stream_json(raw)
    assert [e.kind for e in events] == ["text"]
    assert events[0].text == "still here"


def test_unknown_event_types_pass_through_silently() -> None:
    raw = "\n".join(
        [
            _line({"type": "future_event_type", "subtype": "ignore_me"}),
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                    },
                }
            ),
        ]
    )
    events = parse_stream_json(raw)
    assert [e.kind for e in events] == ["text"]


def test_steps_increment_in_order_across_calls() -> None:
    raw = "\n".join(
        [
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "a", "name": "Bash", "input": {}}],
                    },
                }
            ),
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "b", "name": "Read", "input": {}}],
                    },
                }
            ),
        ]
    )
    events = parse_stream_json(raw)
    assert [e.step for e in events] == [1, 2]


def test_dataclass_default_values() -> None:
    # Sanity: ParsedEvent must be constructible with just a kind so
    # callers can build sentinels.
    e = ParsedEvent(kind="text")
    assert e.text == ""
    assert e.tool_input == {}
    assert e.is_subagent is False
    assert e.is_error is False
