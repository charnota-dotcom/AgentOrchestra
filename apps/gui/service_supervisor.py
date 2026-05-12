"""Service supervisor — auto-spawns the orchestrator service from the GUI.

If 127.0.0.1:8765 is free when the GUI starts, we spawn a headless
``agentorchestra-service`` subprocess.  Windows-only: we use
``CREATE_NO_WINDOW`` so the operator doesn't see a terminal flash.
Cleanup on exit reaps the child.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Single global reference to the supervised child process.
_SUPERVISED_CHILD: subprocess.Popen[Any] | None = None


def spawn_if_needed() -> None:
    """Check port 8765.  If free, spawn the service."""
    global _SUPERVISED_CHILD
    if _is_port_in_use(8765):
        log.info("port 8765 in use — assuming an existing service is running")
        return

    # Derive the command.  If we're running from source, we use the
    # same python.exe as the GUI and run the module.
    cmd = [
        sys.executable,
        "-m",
        "apps.service.main",
        "--data-dir",
        str(
            (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "agentorchestra" / "data")
            if sys.platform == "win32"
            else (Path.home() / ".local" / "share" / "agentorchestra")
        ),
        "--parent-pid",
        str(os.getpid()),
    ]

    # Windows-specific: hide the terminal window.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    # Redirect stdout/stderr to a log file in the local data dir.
    log_dir = Path.home() / ".local" / "share" / "agentorchestra" / "logs"
    if sys.platform == "win32":
        log_dir = Path(os.environ["LOCALAPPDATA"]) / "agentorchestra" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "service.log"

    log.info(f"spawning supervised service: {' '.join(cmd)}")
    log.info(f"cwd: {Path.cwd()}")

    # mypy: ``Popen`` expects ``IO[Any] | int | None`` for stdout/stderr.
    # We use a real file handle.
    log_fh: Any = open(log_path, "a", encoding="utf-8")

    try:
        _SUPERVISED_CHILD = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            creationflags=creationflags,
            cwd=str(Path.cwd()),
        )
        log.info(f"supervised service spawned (pid {_SUPERVISED_CHILD.pid}) -> {log_path}")
    except Exception as exc:
        log.error(f"failed to spawn supervised service: {exc}")
        if log_fh:
            log_fh.close()


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def reap() -> None:
    """Terminate the supervised service if we spawned it."""
    global _SUPERVISED_CHILD
    if _SUPERVISED_CHILD is None:
        return

    proc = _SUPERVISED_CHILD
    log.info(f"reaping supervised service (pid {proc.pid})")
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        log.exception("failed to terminate supervised service")
    finally:
        _SUPERVISED_CHILD = None


def is_supervising() -> bool:
    return _SUPERVISED_CHILD is not None and _SUPERVISED_CHILD.poll() is None


def wait_for_service(port: int = 8765, timeout: float = 5.0) -> bool:
    """Block until the port is bound or timeout is reached.

    Returns True if the service is ready, False otherwise.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if _is_port_in_use(port):
            return True
        time.sleep(0.1)
    return False
