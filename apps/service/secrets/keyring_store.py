"""OS-keyring wrapper.

Provider keys (Anthropic, Google, OpenAI), MCP credentials, and the
hook-receiver authentication token live here.  Falls back to an
in-memory store only for tests; production never touches disk.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

SERVICE_NAME = "agentorchestra"


class _MemoryBackend:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _KeyringBackend:
    def __init__(self, kr: Any) -> None:
        self._kr = kr

    def get(self, key: str) -> str | None:
        return self._kr.get_password(SERVICE_NAME, key)

    def set(self, key: str, value: str) -> None:
        self._kr.set_password(SERVICE_NAME, key, value)

    def delete(self, key: str) -> None:
        try:
            self._kr.delete_password(SERVICE_NAME, key)
        except Exception:  # noqa: BLE001
            pass


def _make_backend() -> _MemoryBackend | _KeyringBackend:
    try:
        import keyring  # type: ignore[import-not-found]
        # Probe the backend; some CI envs have keyring installed but no daemon.
        try:
            keyring.get_password(SERVICE_NAME, "__probe__")
            return _KeyringBackend(keyring)
        except Exception:  # noqa: BLE001
            log.warning("keyring backend unavailable; falling back to in-memory store")
            return _MemoryBackend()
    except ImportError:
        log.warning("keyring not installed; using in-memory store")
        return _MemoryBackend()


_backend: _MemoryBackend | _KeyringBackend = _make_backend()


def get_secret(key: str) -> str | None:
    return _backend.get(key)


def set_secret(key: str, value: str) -> None:
    _backend.set(key, value)


def delete_secret(key: str) -> None:
    _backend.delete(key)


# Convenience names for the common keys.

def anthropic_key() -> str | None:
    return get_secret("anthropic_api_key")


def google_key() -> str | None:
    return get_secret("google_api_key")


def openai_key() -> str | None:
    return get_secret("openai_api_key")


def hook_token() -> str:
    """Per-launch token for the hook receiver.  Lazily generated."""
    import secrets as _secrets
    token = get_secret("hook_token")
    if not token:
        token = _secrets.token_urlsafe(24)
        set_secret("hook_token", token)
    return token
