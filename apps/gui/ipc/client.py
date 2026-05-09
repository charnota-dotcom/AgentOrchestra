"""Async JSON-RPC client used by every GUI window."""

from __future__ import annotations

from typing import Any

import httpx


class RpcClient:
    def __init__(self, *, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, connect=5.0),
        )

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        r = await self._client.post("/rpc", json={"method": method, "params": params or {}})
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(body["error"])
        return body["result"]

    async def healthz(self) -> dict[str, Any]:
        r = await self._client.get("/healthz")
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._client.aclose()
