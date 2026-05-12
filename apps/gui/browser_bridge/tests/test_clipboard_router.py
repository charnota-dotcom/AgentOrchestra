"""Tests for ClipboardRouter — the multi-drone source-URL matcher."""

from __future__ import annotations

from typing import Any

from apps.gui.browser_bridge.clipboard_router import RouteDecision, route


def _drone(
    drone_id: str,
    *,
    chat_url: str | None = None,
    bound: str | None = None,
) -> dict[str, Any]:
    return {
        "id": drone_id,
        "bound_chat_url": bound,
        "blueprint_snapshot": {"chat_url": chat_url},
    }


# --- exact bound_chat_url ---------------------------------------------------


def test_exact_bound_match_wins() -> None:
    drones = [
        _drone("d1", chat_url="https://claude.ai/new"),
        _drone("d2", bound="https://claude.ai/chat/abc"),
        _drone("d3", bound="https://claude.ai/chat/xyz"),
    ]
    decision = route("https://claude.ai/chat/abc", drones)
    assert decision == RouteDecision(kind="bound", drone_id="d2")


# --- prefix fallback --------------------------------------------------------


def test_prefix_unique_match_returns_drone() -> None:
    drones = [
        _drone("d1", chat_url="https://claude.ai/new"),
        _drone("d2", chat_url="https://chatgpt.com/"),
    ]
    decision = route("https://claude.ai/chat/abc", drones)
    assert decision == RouteDecision(kind="prefix_one", drone_id="d1")


def test_prefix_multiple_returns_candidate_list() -> None:
    drones = [
        _drone("d1", chat_url="https://claude.ai/new"),
        _drone("d2", chat_url="https://claude.ai/new"),
        _drone("d3", chat_url="https://chatgpt.com/"),
    ]
    decision = route("https://claude.ai/chat/zzz", drones)
    assert decision.kind == "prefix_many"
    assert decision.candidates is not None
    assert set(decision.candidates) == {"d1", "d2"}


def test_prefix_skips_already_bound_drones() -> None:
    drones = [
        _drone("d1", chat_url="https://claude.ai/new", bound="https://claude.ai/chat/old"),
        _drone("d2", chat_url="https://claude.ai/new"),
    ]
    decision = route("https://claude.ai/chat/new-conversation", drones)
    # d1 is bound to a different conversation — only d2 wins.
    assert decision == RouteDecision(kind="prefix_one", drone_id="d2")


# --- ignore -----------------------------------------------------------------


def test_no_source_url_ignored() -> None:
    drones = [_drone("d1", chat_url="https://claude.ai/new")]
    decision = route(None, drones)
    assert decision == RouteDecision(kind="ignore")
    decision = route("", drones)
    assert decision == RouteDecision(kind="ignore")


def test_no_match_ignored() -> None:
    drones = [_drone("d1", chat_url="https://claude.ai/new")]
    decision = route("https://example.com/something-else", drones)
    assert decision == RouteDecision(kind="ignore")


def test_empty_drone_list_ignored() -> None:
    decision = route("https://claude.ai/chat/abc", [])
    assert decision == RouteDecision(kind="ignore")


# --- defensive --------------------------------------------------------------


def test_drone_with_no_chat_url_ignored() -> None:
    drones = [_drone("d1", chat_url=None)]
    decision = route("https://claude.ai/chat/abc", drones)
    assert decision == RouteDecision(kind="ignore")
