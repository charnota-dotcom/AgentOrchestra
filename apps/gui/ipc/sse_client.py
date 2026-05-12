"""Tiny async SSE client for streaming Run events."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger(__name__)


class SseClient:
    def __init__(self, *, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def stream_run(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        return self._stream(f"/stream/runs/{run_id}")

    async def stream_drone(self, action_id: str) -> AsyncIterator[dict[str, Any]]:
        return self._stream(f"/stream/drones/{action_id}")

    async def _stream(self, path: str) -> AsyncIterator[dict[str, Any]]:
        headers = {
            "authorization": f"Bearer {self.token}",
            "accept": "text/event-stream",
        }
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
            try:
                async with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code != 200:
                        log.warning("SSE failed (%s): %s", url, resp.status_code)
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if not data:
                                continue
                            try:
                                yield json.loads(data)
                            except json.JSONDecodeError:
                                continue
            except (httpx.ReadError, asyncio.CancelledError):
                return
