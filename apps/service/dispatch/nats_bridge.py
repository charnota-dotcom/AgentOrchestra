"""NATS bridge for the EventBus.

When a NATS server is configured, the bridge subscribes to local
EventBus events and republishes them on a NATS subject namespace
(``agentorchestra.<workspace>.<run>``).  Conversely, it subscribes to
the same namespace to receive events from peer orchestrators and
publishes them back to the local EventBus tagged with a
``peer_origin`` payload.

This unlocks two distributed patterns:
- Multi-machine fan-out: a long-running tracker on machine A watches
  runs spawned on machines B and C without any of them sharing the
  same SQLite store.
- Mobile push bridging: a tiny relay translates a subset of A2A
  events into APNs / FCM pushes; the relay subscribes via NATS, no
  changes needed in the orchestrator itself.

Lazy SDK import; if ``nats-py`` isn't installed we log + no-op so
the orchestrator still runs unchanged for local users.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from apps.service.dispatch.bus import EventBus, all_events
from apps.service.types import Event, EventKind, EventSource

log = logging.getLogger(__name__)


SUBJECT_PREFIX = "agentorchestra"


def _import_sdk() -> Any:
    try:
        import nats  # type: ignore[import-not-found]

        return nats
    except ImportError as exc:
        log.info("nats-py not installed; NATS bridge disabled (%s)", exc)
        return None


@dataclass
class NatsBridge:
    bus: EventBus
    nats_url: str = "nats://localhost:4222"
    peer_id: str = "local"
    _nc: Any = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _publish_task: asyncio.Task | None = None
    _subscribe_task: asyncio.Task | None = None

    async def start(self) -> bool:
        nats_mod = _import_sdk()
        if nats_mod is None:
            return False
        try:
            self._nc = await nats_mod.connect(self.nats_url, name=self.peer_id)
        except Exception as exc:
            log.warning("nats connect %s failed: %s", self.nats_url, exc)
            return False
        self._stop.clear()
        self._publish_task = asyncio.create_task(
            self._publish_loop(),
            name="nats-bridge-pub",
        )
        self._subscribe_task = asyncio.create_task(
            self._subscribe_loop(),
            name="nats-bridge-sub",
        )
        log.info("nats bridge connected to %s as peer %s", self.nats_url, self.peer_id)
        return True

    async def stop(self) -> None:
        self._stop.set()
        for t in (self._publish_task, self._subscribe_task):
            if t and not t.done():
                t.cancel()
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:
                pass
            self._nc = None

    async def _publish_loop(self) -> None:
        async for ev in self.bus.stream(all_events()):
            if self._stop.is_set():
                return
            if ev.payload.get("peer_origin") and ev.payload["peer_origin"] != self.peer_id:
                # Avoid reflecting peer events back onto the network.
                continue
            subject = self._subject_for(ev)
            try:
                await self._nc.publish(subject, _serialize(ev))
            except Exception:
                log.warning("nats publish %s failed", subject, exc_info=True)

    async def _subscribe_loop(self) -> None:
        if self._nc is None:
            return
        sub = await self._nc.subscribe(f"{SUBJECT_PREFIX}.>")
        try:
            async for msg in sub.messages:
                if self._stop.is_set():
                    return
                try:
                    payload = json.loads(msg.data.decode())
                except Exception:
                    continue
                origin = payload.get("peer_origin")
                if origin == self.peer_id:
                    continue
                # Republish on the local bus tagged with peer_origin.
                local_event = _deserialize(payload, peer_origin=origin or "?")
                if local_event is not None:
                    self.bus.publish(local_event)
        finally:
            try:
                await sub.unsubscribe()
            except Exception:
                pass

    def _subject_for(self, ev: Event) -> str:
        ws = ev.workspace_id or "global"
        run = ev.run_id or "unscoped"
        return f"{SUBJECT_PREFIX}.{ws}.{run}.{ev.kind.value}"


def _serialize(ev: Event) -> bytes:
    payload = {
        "id": ev.id,
        "seq": ev.seq,
        "occurred_at": ev.occurred_at.isoformat(),
        "source": ev.source.value,
        "kind": ev.kind.value,
        "run_id": ev.run_id,
        "step_id": ev.step_id,
        "branch_id": ev.branch_id,
        "workspace_id": ev.workspace_id,
        "text": ev.text,
        "payload": ev.payload,
    }
    return json.dumps(payload).encode()


def _deserialize(d: dict, *, peer_origin: str) -> Event | None:
    try:
        kind = EventKind(d["kind"])
    except Exception:
        return None
    payload = dict(d.get("payload") or {})
    payload["peer_origin"] = peer_origin
    return Event(
        id=d.get("id", ""),
        seq=int(d.get("seq", 0)),
        source=EventSource.SYSTEM,
        kind=kind,
        run_id=d.get("run_id"),
        step_id=d.get("step_id"),
        branch_id=d.get("branch_id"),
        workspace_id=d.get("workspace_id"),
        text=d.get("text", ""),
        payload=payload,
    )
