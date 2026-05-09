"""Signed update manifest verification.

Format:

    {
      "version": "0.2.0",
      "released_at": "2026-06-01T12:00:00+00:00",
      "channels": {
        "stable": {
          "macos-arm64": {"url": "...", "sha256": "..."},
          "macos-x86_64": {"url": "...", "sha256": "..."},
          "windows-x86_64": {"url": "...", "sha256": "..."},
          "linux-x86_64": {"url": "...", "sha256": "..."}
        }
      },
      "notes": "...",
      "signature": "<base64 ed25519 signature over the canonical bytes>"
    }

The publisher signs the canonical JSON encoding of the manifest with
its ed25519 private key; the GUI ships with the matching public key
embedded.  Updates are NEVER applied silently — the GUI surfaces them
and waits for the user to click "Install".

We use ed25519 because cryptography's Ed25519 primitives are fast,
their keys are small, and verification is straightforward.

If `cryptography` is not installed, verify() raises NotInstalledError
so the orchestrator can fail closed and refuse to apply unsigned
manifests.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any


class NotInstalledError(Exception):
    pass


class InvalidSignatureError(Exception):
    pass


@dataclass(frozen=True)
class UpdateAsset:
    url: str
    sha256: str


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    released_at: str
    channels: dict[str, dict[str, UpdateAsset]]
    notes: str = ""


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON used for signing and verification.  Excludes the
    signature field by design.
    """
    body = {k: v for k, v in payload.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def verify(payload: dict[str, Any], *, public_key_pem: str) -> UpdateManifest:
    """Verify the manifest against the publisher's public key.

    Returns a parsed UpdateManifest on success.  Raises
    InvalidSignatureError on bad signatures and NotInstalledError when
    the cryptography package is missing.
    """
    try:
        from cryptography.exceptions import InvalidSignature  # type: ignore[import-not-found]
        from cryptography.hazmat.primitives import serialization  # type: ignore[import-not-found]
    except ImportError as exc:
        raise NotInstalledError(
            "`cryptography` not installed; cannot verify update manifests"
        ) from exc

    sig_b64 = payload.get("signature")
    if not isinstance(sig_b64, str) or not sig_b64:
        raise InvalidSignatureError("manifest missing signature")
    try:
        sig = base64.b64decode(sig_b64.encode())
    except Exception as exc:
        raise InvalidSignatureError(f"signature not base64: {exc}") from exc

    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
    except Exception as exc:
        raise InvalidSignatureError(f"bad public key: {exc}") from exc

    try:
        public_key.verify(sig, canonical_bytes(payload))
    except InvalidSignature as exc:
        raise InvalidSignatureError("signature does not verify") from exc

    channels: dict[str, dict[str, UpdateAsset]] = {}
    for chan, assets in (payload.get("channels") or {}).items():
        channels[chan] = {}
        for plat, ent in (assets or {}).items():
            url = ent.get("url", "")
            sha = ent.get("sha256", "")
            if not url or not sha:
                continue
            channels[chan][plat] = UpdateAsset(url=url, sha256=sha)

    return UpdateManifest(
        version=str(payload.get("version", "")),
        released_at=str(payload.get("released_at", "")),
        channels=channels,
        notes=str(payload.get("notes", "")),
    )


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
