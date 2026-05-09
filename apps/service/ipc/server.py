"""JSON-RPC IPC server.

Bound to 127.0.0.1 only.  Authentication via a per-launch token in the
`Authorization: Bearer <token>` header — same token the GUI receives
on startup and the Claude hook receiver requires.

Methods are registered by their dotted name (e.g. ``runs.list``) and
take a JSON-decoded params object.  Methods are async; their return
value is JSON-encoded and sent as the response.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from apps.service.dispatch.bus import EventBus
from apps.service.ipc.sse import make_run_stream_route

log = logging.getLogger(__name__)


Method = Callable[[dict[str, Any]], Awaitable[Any]]


class JsonRpcServer:
    def __init__(self, *, token: str, bus: EventBus | None = None) -> None:
        self.token = token
        self.bus = bus
        self._methods: dict[str, Method] = {}

    def register(self, name: str, fn: Method) -> None:
        self._methods[name] = fn

    def app(self) -> Starlette:
        routes = [
            Route("/rpc", self._rpc, methods=["POST"]),
            Route("/healthz", self._healthz, methods=["GET"]),
            Route("/ingest/hook", self._hook, methods=["POST"]),
        ]
        if self.bus is not None:
            routes.append(
                Route(
                    "/stream/runs/{run_id}",
                    make_run_stream_route(self.bus, token=self.token),
                    methods=["GET"],
                )
            )
        return Starlette(routes=routes)

    async def _healthz(self, _request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "methods": sorted(self._methods.keys())})

    def _check_auth(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        return header == f"Bearer {self.token}"

    async def _rpc(self, request: Request) -> Response:
        if not self._check_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        method = payload.get("method")
        params = payload.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return JSONResponse(
                {"error": "method must be string, params must be object"},
                status_code=400,
            )
        fn = self._methods.get(method)
        if not fn:
            return JSONResponse(
                {"error": f"unknown method: {method}"}, status_code=404
            )
        try:
            result = await fn(params)
        except Exception as exc:  # surfaced to caller
            log.exception("rpc method %s failed", method)
            return JSONResponse(
                {"error": str(exc), "type": type(exc).__name__}, status_code=500
            )
        return JSONResponse({"result": result})

    async def _hook(self, request: Request) -> Response:
        if not self._check_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        # The actual ingestion is wired by the supervisor; methods called
        # ``hook.received`` will pick it up.
        fn = self._methods.get("hook.received")
        if fn:
            await fn({"payload": payload})
        return JSONResponse({"ok": True})
