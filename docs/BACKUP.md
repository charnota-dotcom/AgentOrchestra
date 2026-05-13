# Backup & restore

AgentOrchestra keeps every Run, Branch, Step, Reaper Drone, Attachment, Flow,
Workspace, Card, and Event in a single SQLite file in WAL mode. This
guide covers safe ways to back it up and restore it.

## Where the data lives

| Path | What it is |
|---|---|
| `<data_dir>/agentorchestra.sqlite` | the main database |
| `<data_dir>/agentorchestra.sqlite-wal` | uncommitted write-ahead log frames |
| `<data_dir>/agentorchestra.sqlite-shm` | shared-memory index for the WAL |
| `<data_dir>/attachments/<reaper_id>/â€¦` | uploaded images + spreadsheets |
| `<data_dir>/clones/<repo>/â€¦` | git repos cloned via `workspaces.clone` |

`<data_dir>` defaults to `%LOCALAPPDATA%\agentorchestra\` on Windows
and `~/.local/share/agentorchestra/` on Linux/macOS.

## âš  Don't naively copy the .sqlite file

Copying `agentorchestra.sqlite` while the service is running misses
uncommitted WAL frames.  At best the backup is missing the most
recent few minutes of activity; at worst the copied file is
inconsistent and won't open.

## Recommended: built-in `export_backup`

`apps/service/store/backup.py` exposes `export_backup` /
`restore_backup` which use SQLite's `.backup` API to produce a
consistent snapshot **with the service running**.  The Settings tab
exposes both as buttons.

The archive (`<name>.aobackup`) is a tarball containing a single
`store.sqlite` plus a `manifest.json` recording the schema version
and a creation timestamp.  Restore refuses to import a backup whose
`schema_version` is **higher** than the current runtime (downgrade
across schema bumps isn't supported).

## Manual backup (service stopped)

If you must copy the files by hand, **stop the service first**:

```cmd
scripts\stop.cmd
```

Then copy the whole `<data_dir>` directory.  All three SQLite files
(`*.sqlite`, `*.sqlite-wal`, `*.sqlite-shm`) plus `attachments/` and
`clones/` are needed for a faithful restore.

## Restore

Use `restore_backup` (or the Settings-tab Restore button).  The
implementation:

- Validates the manifest's `schema_version`.
- Extracts the archive with `tar.extractall(filter='data')` so a
  malicious archive can't escape the temporary directory.
- Renames the existing `store.sqlite` to `store.sqlite.pre-restore`
  as a safety net.
- Atomically swaps in the restored file.
- **Deletes any stale `-wal` / `-shm` sidecars from the previous
  database** so SQLite can't replay pre-restore WAL frames into the
  freshly-restored file.

If the GUI is open during restore, close it first â€” the running
service holds the connection and the swap will fail otherwise.

## Pruning attachments

The Limits tab's **Attachment storage** card shows per-Reaper Drone disk
usage. To reclaim space:

- Delete the Reaper Drone (cascades to its attachments).
- Or open the canvas chat dialog, click each attachment chip's `Ã—`
  button before sending â€” the server-side row + file get removed.