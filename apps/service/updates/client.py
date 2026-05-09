"""Update channel client.

Polls the publisher's signed manifest URL on launch and on a slow
schedule (every 24h) to discover newer releases.  Verifies the
signature before surfacing the offer to the user.  Downloads the
platform-appropriate asset, hashes it, and writes it to the user's
"updates ready" inbox — install is always user-initiated.

We never silently install updates.  The whole point of code-touching
software is the user has to consent.
"""

from __future__ import annotations

import json
import logging
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from apps.service.updates import manifest as manifest_mod

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateOffer:
    version: str
    notes: str
    asset_url: str
    asset_sha256: str
    archive_path: Path


def current_platform_key() -> str:
    sys = platform.system().lower()
    arch = platform.machine().lower()
    if sys == "darwin":
        return "macos-arm64" if arch in ("arm64", "aarch64") else "macos-x86_64"
    if sys == "windows":
        return "windows-x86_64"
    if sys == "linux":
        return "linux-arm64" if arch in ("arm64", "aarch64") else "linux-x86_64"
    return f"{sys}-{arch}"


async def fetch_manifest(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def discover_offer(
    *,
    manifest_url: str,
    public_key_pem: str,
    current_version: str,
    download_dir: Path,
    channel: str = "stable",
) -> UpdateOffer | None:
    """Returns an UpdateOffer if a newer release exists for our platform.

    Raises InvalidSignatureError if the manifest's signature is bad.
    """
    payload = await fetch_manifest(manifest_url)
    parsed = manifest_mod.verify(payload, public_key_pem=public_key_pem)
    if _version_tuple(parsed.version) <= _version_tuple(current_version):
        return None
    plat_key = current_platform_key()
    asset = parsed.channels.get(channel, {}).get(plat_key)
    if asset is None:
        log.info("no %s asset for %s in manifest", channel, plat_key)
        return None
    download_dir.mkdir(parents=True, exist_ok=True)
    archive_path = download_dir / f"agentorchestra-{parsed.version}-{plat_key}.bin"
    if not (archive_path.exists() and _sha_match(archive_path, asset.sha256)):
        await _download(asset.url, archive_path)
        if not _sha_match(archive_path, asset.sha256):
            archive_path.unlink(missing_ok=True)
            raise ValueError(
                f"downloaded archive sha256 mismatch for {asset.url}",
            )
    return UpdateOffer(
        version=parsed.version,
        notes=parsed.notes,
        asset_url=asset.url,
        asset_sha256=asset.sha256,
        archive_path=archive_path,
    )


async def _download(url: str, dest: Path) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 16):
                    fh.write(chunk)


def _sha_match(path: Path, expected: str) -> bool:
    return manifest_mod.sha256_file(str(path)) == expected.lower()


def _version_tuple(v: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in v.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def write_offer_record(
    *,
    offer: UpdateOffer,
    record_dir: Path,
) -> Path:
    """Persist a small JSON record so the GUI can show the pending
    update across service restarts."""
    record_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "version": offer.version,
        "notes": offer.notes,
        "asset_url": offer.asset_url,
        "asset_sha256": offer.asset_sha256,
        "archive_path": str(offer.archive_path),
    }
    out = record_dir / "pending_update.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    return out
