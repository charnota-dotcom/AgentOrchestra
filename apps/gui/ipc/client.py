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
        # The service returns ``{"error": "...", "type": "..."}`` with a
        # 500 body when an RPC handler raises.  Read the body BEFORE
        # raising for status so the actual message reaches the user
        # — httpx's HTTPStatusError otherwise just says "Server error
        # '500'", which is useless.
        body: dict[str, Any] | None
        try:
            body = r.json()
        except Exception:
            body = None
        if r.status_code >= 400:
            if isinstance(body, dict) and "error" in body:
                typ = body.get("type") or ""
                suffix = f" [{typ}]" if typ else ""
                raise RuntimeError(f"{body['error']}{suffix}")
            r.raise_for_status()
        if body is None:
            raise RuntimeError(f"empty response (HTTP {r.status_code})")
        if "error" in body:
            raise RuntimeError(body["error"])
        return body["result"]

    async def healthz(self) -> dict[str, Any]:
        r = await self._client.get("/healthz")
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._client.aclose()
