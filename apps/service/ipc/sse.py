"""Server-Sent Events endpoint for streaming Run events to the GUI."""

from __future__ import annotations

import asyncio
import json
import logging

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from apps.service.dispatch.bus import EventBus, by_run

log = logging.getLogger(__name__)


def make_run_stream_route(bus: EventBus, *, token: str):  # noqa: ANN201
    """Returns an async function suitable as a Starlette endpoint."""

    async def endpoint(request: Request) -> Response:
        if request.headers.get("authorization", "") != f"Bearer {token}":
            return Response("unauthorized", status_code=401)
        run_id = request.path_params["run_id"]

        async def gen():  # noqa: ANN202
            heartbeat = 15.0
            try:
                async for ev in bus.stream(by_run(run_id), timeout=heartbeat):
                    if await request.is_disconnected():
                        break
                    payload = {
                        "id": ev.id,
                        "seq": ev.seq,
                        "kind": ev.kind.value,
                        "source": ev.source.value,
                        "run_id": ev.run_id,
                        "step_id": ev.step_id,
                        "occurred_at": ev.occurred_at.isoformat(),
                        "text": ev.text,
                        "payload": ev.payload,
                    }
                    yield f"event: {ev.kind.value}\ndata: {json.dumps(payload)}\n\n"
                    if ev.kind.value in ("run.completed",) or (
                        ev.payload.get("state") in ("merged", "rejected", "aborted")
                    ):
                        break
            except asyncio.CancelledError:
                return
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    return endpoint
