"""Multi-drone routing for clipboard events.

Five browser drones in five browser tabs, one app organising it all.
This router decides which drone a given clipboard event "belongs to"
based on the SourceURL the listener pulled out of the clipboard:

1. Find the drone whose ``bound_chat_url`` exactly matches the event's
   ``source_url`` → route there with confidence.
2. If none bound, find drones whose ``chat_url`` is a prefix of
   ``source_url`` (e.g. blueprint URL ``https://claude.ai/new`` matches
   a captured ``https://claude.ai/chat/abc``).  One match → return it
   (with ``confidence="prefix"``).  Multiple → return a list for the
   caller to disambiguate via picker.
3. No match → return ``RouteDecision(kind="ignore")`` — operator
   copied from somewhere unrelated.

Pure logic; no Qt, no I/O.  Tests live in ``tests/test_clipboard_router.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class RouteDecision:
    """The router's verdict for one clipboard event."""

    kind: Literal["bound", "prefix_one", "prefix_many", "ignore"]
    drone_id: str | None = None
    candidates: list[str] | None = None


def route(
    source_url: str | None,
    drones: list[dict[str, Any]],
) -> RouteDecision:
    """Classify a clipboard event against the set of waiting drones.

    ``drones`` is a list of action dicts in the shape returned by
    ``drones.list``: each must expose ``id``, ``bound_chat_url``, and
    ``blueprint_snapshot.chat_url``.

    Returns the routing decision; the caller is responsible for
    actually delivering the event (or surfacing a picker).
    """
    if not source_url:
        # Non-browser copy.  Router has nothing useful to say.
        return RouteDecision(kind="ignore")

    # Pass 1 — exact bound_chat_url match.
    for d in drones:
        bound = d.get("bound_chat_url")
        if bound and bound == source_url:
            return RouteDecision(kind="bound", drone_id=d.get("id"))

    # Pass 2 — chat_url prefix match (drone not yet bound).
    prefix_hits: list[str] = []
    for d in drones:
        if d.get("bound_chat_url"):
            # Already bound to a specific chat — only the exact match
            # in pass 1 should claim it.
            continue
        snap = d.get("blueprint_snapshot") or {}
        chat_url = snap.get("chat_url")
        if not chat_url:
            continue
        if _is_prefix(chat_url, source_url):
            drone_id = d.get("id")
            if drone_id:
                prefix_hits.append(drone_id)
    if len(prefix_hits) == 1:
        return RouteDecision(kind="prefix_one", drone_id=prefix_hits[0])
    if len(prefix_hits) > 1:
        return RouteDecision(kind="prefix_many", candidates=prefix_hits)

    return RouteDecision(kind="ignore")


def _is_prefix(chat_url: str, source_url: str) -> bool:
    """True if ``source_url`` looks like it came from the same chat
    product as ``chat_url``.

    Treats ``https://claude.ai/new`` as a generic "any claude.ai
    chat" pattern — anything under claude.ai matches.  More
    sophisticated than a plain ``startswith`` because chat products
    use UUID paths post-first-message (``/new`` -> ``/chat/<uuid>``)
    while the blueprint stores only the entry URL.

    Comparison: take the host + tail-stripped path, see if
    ``source_url``'s host matches and the path is "compatible".
    """
    if not chat_url or not source_url:
        return False
    try:
        from urllib.parse import urlparse

        a, b = urlparse(chat_url), urlparse(source_url)
    except Exception:
        return False
    # Same host is sufficient for prefix matching — the blueprint's
    # entry path (e.g. /new) and the actual chat path (e.g.
    # /chat/<uuid>) differ but both belong to the same drone setup.
    return a.netloc.lower() == b.netloc.lower()
