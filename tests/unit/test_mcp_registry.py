"""MCP server registry."""

from __future__ import annotations

from pathlib import Path

from apps.service.mcp import registry


def test_add_writes_file_with_default_untrusted(tmp_path: Path) -> None:
    p = tmp_path / "mcp.json"
    s = registry.add_server(
        name="filesystem",
        transport="stdio",
        command="/usr/bin/mcp-fs",
        args=["--root", "/tmp"],
        path=p,
    )
    assert s.trust is registry.MCPTrust.UNTRUSTED
    assert s.fingerprint
    rows = registry.list_servers(p)
    assert len(rows) == 1
    assert rows[0].name == "filesystem"


def test_trust_then_block_then_remove(tmp_path: Path) -> None:
    p = tmp_path / "mcp.json"
    s = registry.add_server(name="github", transport="http", url="http://x", path=p)
    trusted = registry.trust_server(s.id, path=p)
    assert trusted is not None
    assert trusted.trust is registry.MCPTrust.TRUSTED

    blocked = registry.block_server(s.id, path=p)
    assert blocked is not None
    assert blocked.trust is registry.MCPTrust.BLOCKED

    assert registry.remove_server(s.id, path=p) is True
    assert registry.list_servers(p) == []


def test_fingerprint_changes_with_command(tmp_path: Path) -> None:
    p = tmp_path / "mcp.json"
    s1 = registry.add_server(
        name="a",
        transport="stdio",
        command="/bin/a",
        args=["1"],
        path=p,
    )
    p2 = tmp_path / "mcp2.json"
    s2 = registry.add_server(
        name="a",
        transport="stdio",
        command="/bin/a",
        args=["2"],
        path=p2,
    )
    assert s1.fingerprint != s2.fingerprint
