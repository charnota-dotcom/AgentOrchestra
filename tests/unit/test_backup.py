"""Backup / restore round-trip."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from apps.service.store import backup as backup_mod


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE foo (k TEXT, v TEXT)")
    conn.execute("INSERT INTO foo VALUES ('a', '1')")
    conn.execute("INSERT INTO foo VALUES ('b', '2')")
    conn.commit()
    conn.close()


def test_export_then_restore_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "store.sqlite"
    _seed_db(db)

    info = backup_mod.export_backup(
        db_path=db,
        out_path=tmp_path / "snap",
        note="test",
    )
    assert info.path.exists()
    assert info.path.suffix == ".aobackup"
    assert info.size_bytes > 0
    assert len(info.sha256) == 64

    # Mutate original after backup.
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO foo VALUES ('c', '3')")
    conn.commit()
    conn.close()

    # Restore over the original.
    report = backup_mod.restore_backup(
        archive_path=info.path,
        target_db_path=db,
    )
    assert report.note == "test"

    # The mutation should be gone, and the pre-restore backup should
    # exist alongside.
    conn = sqlite3.connect(str(db))
    rows = list(conn.execute("SELECT k FROM foo ORDER BY k"))
    conn.close()
    assert [r[0] for r in rows] == ["a", "b"]
    assert (db.with_suffix(".sqlite.pre-restore")).exists()


def test_describe_reads_manifest_without_restoring(tmp_path: Path) -> None:
    db = tmp_path / "store.sqlite"
    _seed_db(db)
    info = backup_mod.export_backup(
        db_path=db,
        out_path=tmp_path / "snap",
        note="hello",
    )
    manifest = backup_mod.describe_backup(info.path)
    assert manifest["schema_version"] == backup_mod.SCHEMA_VERSION
    assert manifest["note"] == "hello"


def test_restore_refuses_newer_schema(tmp_path: Path) -> None:
    import json
    import tarfile

    archive = tmp_path / "fake.aobackup"
    inner_db = tmp_path / "store.sqlite"
    _seed_db(inner_db)
    manifest_text = json.dumps(
        {
            "schema_version": 9999,
            "created_at": "2099-01-01T00:00:00+00:00",
            "source_db_sha256": "00",
            "note": "future",
        }
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest_text)

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(inner_db, arcname="store.sqlite")
        tar.add(manifest_path, arcname=backup_mod.MANIFEST_NAME)

    target = tmp_path / "target.sqlite"
    _seed_db(target)
    with pytest.raises(ValueError, match="schema_version"):
        backup_mod.restore_backup(archive_path=archive, target_db_path=target)
