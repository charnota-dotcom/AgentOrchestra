"""Speculative parallelism — race N candidates, keep the first acceptable.

Pattern: for a single user-visible Run, fan out to N (provider, model)
candidates in parallel.  Each candidate runs as its own ChatSession
against the EventBus.  As soon as one finishes successfully (or an
optional acceptor predicate returns True on the streamed text), the
others are cancelled and their costs are still recorded.

Useful for:
- racing a cheap-fast model against a strong-slow model so the cheap
  one wins on easy questions and the strong one fills in for hard ones,
- testing an instruction across vendors before promoting one to a card.

This is a building block; the runs.speculative RPC + a card archetype
that consumes it are the user-visible surface.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from apps.service.cost.meter import cost_for_call
from apps.service.providers.registry import get_provider
from apps.service.types import (
    Artifact,
    ArtifactKind,
    CardMode,
    CostPolicy,
    PersonalityCard,
    long_id,
)

log = logging.getLogger(__name__)


Acceptor = Callable[[str], bool]


@dataclass
class SpeculativeAttempt:
    provider: str
    model: str
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    duration_s: float = 0.0
    cancelled: bool = False
    accepted: bool = False


@dataclass
class SpeculativeResult:
    winner: SpeculativeAttempt | None
    attempts: list[SpeculativeAttempt] = field(default_factory=list)
    total_cost_usd: float = 0.0


def _default_acceptor(text: str) -> bool:
    # Default: accept any non-empty response.  Cards with structured
    # output expectations override this with something stricter.
    return bool(text.strip())


async def race(
    *,
    user_message: str,
    candidates: list[tuple[str, str]],
    system: str | None = None,
    acceptor: Acceptor | None = None,
    max_total_seconds: float = 120.0,
) -> SpeculativeResult:
    """Run all candidates in parallel; first accepted output wins.

    The losing tasks are cancelled cleanly; their accumulated tokens and
    duration up to cancellation are still recorded so the cost meter
    reflects real spend.
    """
    if not candidates:
        return SpeculativeResult(winner=None)

    accept = acceptor or _default_acceptor
    attempts = [SpeculativeAttempt(provider=p, model=m) for p, m in candidates]
    won: asyncio.Event = asyncio.Event()

    async def _run(idx: int, provider: str, model: str) -> None:
        a = attempts[idx]
        t0 = time.monotonic()
        try:
            adapter = get_provider(provider)
            card = PersonalityCard(
                name=f"{provider}/{model}",
                archetype="speculative-candidate",
                description="",
                template_id="",
                provider=provider,  # type: ignore[arg-type]
                model=model,
                mode=CardMode.CHAT,
                cost=CostPolicy(),
            )
            session = await adapter.open_chat(card, system=system)
            try:
                async for ev in session.send(user_message):
                    if won.is_set():
                        a.cancelled = True
                        return
                    if ev.kind == "text_delta":
                        a.text += ev.text
                    elif ev.kind == "usage":
                        a.tokens_in = int(ev.payload.get("input_tokens") or 0)
                        a.tokens_out = int(ev.payload.get("output_tokens") or 0)
                    elif ev.kind == "error":
                        raise RuntimeError(ev.text)
                    elif ev.kind == "finish":
                        break
            finally:
                await session.close()
            if accept(a.text):
                a.accepted = True
                won.set()
        except asyncio.CancelledError:
            a.cancelled = True
            raise
        except Exception as exc:
            a.error = str(exc)
        finally:
            a.duration_s = time.monotonic() - t0
            a.cost_usd = cost_for_call(provider, model, a.tokens_in, a.tokens_out)

    async def _wait_or_timeout() -> None:
        try:
            await asyncio.wait_for(won.wait(), timeout=max_total_seconds)
        except TimeoutError:
            return

    tasks = [
        asyncio.create_task(_run(i, p, m), name=f"spec-{p}-{m}")
        for i, (p, m) in enumerate(candidates)
    ]
    waiter = asyncio.create_task(_wait_or_timeout(), name="spec-waiter")

    try:
        # Wait until either a candidate accepts or all finish (no acceptable).
        while not won.is_set():
            await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
                timeout=1.0,
            )
            if all(t.done() for t in tasks):
                break
            # If the waiter expired, give up.
            if waiter.done():
                break
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if not waiter.done():
            waiter.cancel()

    winner = next((a for a in attempts if a.accepted), None)
    if winner is None:
        # No acceptor hit, but maybe a candidate finished cleanly with text.
        winner = next(
            (a for a in attempts if not a.error and a.text and not a.cancelled),
            None,
        )
    total = sum(a.cost_usd for a in attempts)
    return SpeculativeResult(winner=winner, attempts=attempts, total_cost_usd=total)


async def persist_result(store, result: SpeculativeResult, run_id: str) -> None:
    """Save attempts + winner as artifacts on a parent Run."""
    for i, a in enumerate(result.attempts):
        body = a.text or f"(error: {a.error})" if a.error else a.text
        body = body or "(cancelled)" if a.cancelled else body
        await store.insert_artifact(
            Artifact(
                id=long_id(),
                run_id=run_id,
                kind=ArtifactKind.TRANSCRIPT,
                title=(
                    f"Speculative #{i + 1} — {a.provider}/{a.model}"
                    + (" (winner)" if a.accepted else "")
                    + (" (cancelled)" if a.cancelled else "")
                ),
                body=body or "(no output)",
            )
        )
    if result.winner is not None:
        await store.insert_artifact(
            Artifact(
                id=long_id(),
                run_id=run_id,
                kind=ArtifactKind.SUMMARY,
                title=f"Speculative winner: {result.winner.provider}/{result.winner.model}",
                body=result.winner.text or "(empty)",
            )
        )
