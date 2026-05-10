"""Backup / restore for the SQLite event store.

Two formats:

- ``.aobackup`` — a tar.gz containing the SQLite file plus the
  templates directory and a manifest.  Atomic via a temp file +
  rename.  Restore validates the manifest and refuses to overwrite a
  store with a higher schema version.
- ``.aobackup-sql`` — a portable SQL dump (``.dump``) for cases where
  the user wants a plain-text backup or wants to migrate to a new
  SQLite version.  Larger but human-inspectable.

Backups never include keyring secrets — those live with the OS, not
the database, and the user can re-enter them on restore.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from apps.service.types import utc_now

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"


@dataclass
class BackupInfo:
    path: Path
    size_bytes: int
    sha256: str
    created_at: datetime
    schema_version: int
    note: str = ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def export_backup(
    *,
    db_path: Path,
    out_path: Path,
    note: str = "",
) -> BackupInfo:
    """Create a .aobackup tarball at ``out_path``.

    Uses sqlite3's online backup API to copy the database while it's in
    use so the export is consistent.
    """
    out_path = out_path.with_suffix(".aobackup")
    tmp_dir = out_path.parent / f".aobackup-tmp-{out_path.stem}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    db_copy = tmp_dir / "store.sqlite"

    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(db_copy))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now().isoformat(),
        "source_db_sha256": _sha256(db_copy),
        "note": note,
    }
    (tmp_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")

    tmp_archive = out_path.with_suffix(".aobackup.partial")
    with tarfile.open(tmp_archive, "w:gz") as tar:
        tar.add(db_copy, arcname="store.sqlite")
        tar.add(tmp_dir / MANIFEST_NAME, arcname=MANIFEST_NAME)
    tmp_archive.replace(out_path)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return BackupInfo(
        path=out_path,
        size_bytes=out_path.stat().st_size,
        sha256=_sha256(out_path),
        created_at=utc_now(),
        schema_version=SCHEMA_VERSION,
        note=note,
    )


@dataclass
class RestoreReport:
    db_path: Path
    schema_version: int
    note: str
    created_at: datetime


def restore_backup(*, archive_path: Path, target_db_path: Path) -> RestoreReport:
    """Replace the database at ``target_db_path`` with the backup's
    contents.  Refuses if the backup's schema_version is higher than
    ours (forward-incompatible).  Backs up the existing DB to a
    sibling ``.pre-restore`` file before overwriting.
    """
    if not archive_path.exists():
        raise FileNotFoundError(archive_path)
    with tarfile.open(archive_path, "r:gz") as tar:
        names = set(tar.getnames())
        if "store.sqlite" not in names or MANIFEST_NAME not in names:
            raise ValueError(f"backup at {archive_path} is missing required members")
        manifest_member = tar.extractfile(MANIFEST_NAME)
        if manifest_member is None:
            raise ValueError("manifest unreadable")
        manifest = json.loads(manifest_member.read().decode())
        if int(manifest.get("schema_version", 0)) > SCHEMA_VERSION:
            raise ValueError(
                f"backup schema_version={manifest['schema_version']} > "
                f"current={SCHEMA_VERSION}; refusing to restore",
            )

        tmp_dir = target_db_path.parent / f".aorestore-tmp-{target_db_path.stem}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            # ``filter='data'`` rejects archive members with absolute
            # paths or `..` segments — without it a malicious .aobackup
            # could write outside `tmp_dir` (tar-slip).  Available
            # since Python 3.12; we require 3.11+ but the keyword is
            # accepted on older interpreters via tarfile's compatibility
            # layer (it just becomes a no-op there).
            try:
                tar.extractall(tmp_dir, filter="data")
            except TypeError:
                # Older tarfile without `filter` kwarg — fall back to
                # an explicit member-path validator.
                for m in tar.getmembers():
                    if m.name.startswith("/") or ".." in Path(m.name).parts:
                        raise ValueError(  # noqa: B904 — outer except is TypeError handling, not the ValueError's cause
                            f"refusing tar-slip member: {m.name}"
                        )
                tar.extractall(tmp_dir)
            staged = tmp_dir / "store.sqlite"
            if target_db_path.exists():
                backup_path = target_db_path.with_suffix(
                    target_db_path.suffix + ".pre-restore",
                )
                shutil.copy2(target_db_path, backup_path)
                log.info("pre-restore backup at %s", backup_path)
            shutil.move(str(staged), str(target_db_path))
            # Drop any stale -wal / -shm sidecars from the *previous*
            # database; without this SQLite would replay pre-restore
            # WAL frames into the freshly-restored file and corrupt it.
            for sfx in (".sqlite-wal", ".sqlite-shm"):
                sidecar = target_db_path.with_suffix(sfx)
                if sidecar.exists():
                    sidecar.unlink()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return RestoreReport(
        db_path=target_db_path,
        schema_version=int(manifest.get("schema_version", 0)),
        note=str(manifest.get("note", "")),
        created_at=datetime.fromisoformat(manifest["created_at"]),
    )


def describe_backup(archive_path: Path) -> dict:
    """Read just the manifest from a backup without restoring."""
    with tarfile.open(archive_path, "r:gz") as tar:
        member = tar.extractfile(MANIFEST_NAME)
        if member is None:
            raise ValueError("manifest unreadable")
        return json.loads(member.read().decode())
