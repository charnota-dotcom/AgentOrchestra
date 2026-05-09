"""Mergiraf detection + thin wrapper.

Mergiraf is a Rust binary that performs syntax-aware structural merges.
It is not bundled with V1 of the orchestrator; if it's on PATH we use
it for the "Combine with help" mode, otherwise we fall back to git's
default merge.

This module is deliberately tiny — its only job is to localize the
binary detection so the WorktreeManager can treat assisted-merge as a
single call regardless of whether Mergiraf is present.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

MERGIRAF_BINARY = "mergiraf"
_MIN_VERSION = (0, 5, 0)


_cached_available: bool | None = None
_cached_version: tuple[int, int, int] | None = None


async def is_available() -> bool:
    """Return True if Mergiraf is on PATH and at the minimum version."""
    global _cached_available, _cached_version
    if _cached_available is not None:
        return _cached_available
    bin_path = shutil.which(MERGIRAF_BINARY)
    if not bin_path:
        _cached_available = False
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (TimeoutError, OSError):
        _cached_available = False
        return False
    text = out.decode("utf-8", errors="replace").strip()
    version = _parse_version(text)
    _cached_version = version
    if version is None or version < _MIN_VERSION:
        log.warning("mergiraf %s present but below minimum %s", text, _MIN_VERSION)
        _cached_available = False
        return False
    _cached_available = True
    return True


def reset_cache() -> None:
    """Force the next call to ``is_available`` to re-probe."""
    global _cached_available, _cached_version
    _cached_available = None
    _cached_version = None


def cached_version() -> tuple[int, int, int] | None:
    return _cached_version


def _parse_version(text: str) -> tuple[int, int, int] | None:
    # Expected format: "mergiraf 0.5.1" — be lenient about prefix.
    parts = text.split()
    for tok in parts:
        if all(c.isdigit() or c == "." for c in tok):
            bits = tok.split(".")
            try:
                return (
                    int(bits[0]),
                    int(bits[1]) if len(bits) > 1 else 0,
                    int(bits[2]) if len(bits) > 2 else 0,
                )
            except ValueError:
                continue
    return None


async def merge_files(base: Path, left: Path, right: Path, *, output: Path) -> bool:
    """Run Mergiraf on a single file.  Returns True on clean merge.

    Caller must ensure ``base``, ``left``, ``right`` are paths to files
    representing the three sides of the merge.  ``output`` is written
    with the merged content.
    """
    if not await is_available():
        raise RuntimeError("mergiraf not available")
    proc = await asyncio.create_subprocess_exec(
        MERGIRAF_BINARY,
        "merge",
        "--base",
        str(base),
        "--left",
        str(left),
        "--right",
        str(right),
        "--output",
        str(output),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    return proc.returncode == 0
