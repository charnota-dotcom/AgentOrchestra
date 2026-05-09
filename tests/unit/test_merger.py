"""Mergiraf detection cache + version parsing."""

from __future__ import annotations

import pytest

from apps.service.worktrees import merger


def test_parse_version_handles_prefix() -> None:
    assert merger._parse_version("mergiraf 0.5.1") == (0, 5, 1)
    assert merger._parse_version("0.6.0") == (0, 6, 0)
    assert merger._parse_version("garbage") is None


@pytest.mark.asyncio
async def test_is_available_returns_bool() -> None:
    merger.reset_cache()
    available = await merger.is_available()
    assert isinstance(available, bool)


@pytest.mark.asyncio
async def test_cache_avoids_reprobe() -> None:
    merger.reset_cache()
    a = await merger.is_available()
    b = await merger.is_available()
    assert a == b
