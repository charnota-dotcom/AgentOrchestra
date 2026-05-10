"""Auto-spawn the AgentOrchestra service when the GUI starts.

A long-standing UX papercut: operators had to manually start
``python -m apps.service.main`` in one terminal and ``python -m
apps.gui.main`` in another every session.  This module probes the
configured ``--service-url`` once and, if nothing answers, spawns the
service as a child process bound to the GUI's lifetime.

Design notes:

* Probe with a tiny synchronous TCP connect (not an HTTP request) so
  it returns in microseconds when the port is free and in a few ms
  when it isn't.
* On Windows we use ``CREATE_NO_WINDOW`` so we don't open a console
  window for the child.  On POSIX we redirect stdin/stdout/stderr to
  ``/dev/null`` for the same reason.
* The child is registered with ``atexit`` so a hard GUI crash still
  takes the service down — orphaned services from previous sessions
  are the second-most-common support question after "where do I find
  the API key".
* If the user *did* start the service themselves (port is busy), we
  don't touch it.
"""

from __future__ import annotations

import atexit
import logging
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_SUPERVISED_CHILD: subprocess.Popen | None = None


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_service_running(service_url: str, *, wait_seconds: float = 8.0) -> bool:
    """Probe ``service_url``; spawn the service if nothing answers.

    Returns True if a service is reachable by the time we return,
    False if we tried to spawn and gave up waiting.  Either way the
    caller can carry on — the GUI's RPC client retries on transient
    failures.
    """
    parsed = urlparse(service_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765

    if _port_open(host, port):
        log.info("service already running on %s:%d, attaching", host, port)
        return True

    log.info("no service on %s:%d, spawning child process", host, port)
    _spawn_service()

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if _port_open(host, port):
            log.info("service is up after %.1fs", wait_seconds - (deadline - time.monotonic()))
            return True
        time.sleep(0.2)
    log.warning(
        "service did not bind to %s:%d within %ss; GUI will retry", host, port, wait_seconds
    )
    return False


def _spawn_service() -> None:
    """Launch ``python -m apps.service.main`` as a child process.

    Inherits the current Python interpreter so we always pick up the
    same venv as the GUI — the most common cause of "service started
    but the GUI can't see the new RPC method" is two pythons.
    """
    global _SUPERVISED_CHILD
    if _SUPERVISED_CHILD is not None and _SUPERVISED_CHILD.poll() is None:
        return  # already supervising one

    args = [sys.executable, "-m", "apps.service.main"]
    creationflags = 0
    stdin = subprocess.DEVNULL
    stdout: int | None = subprocess.DEVNULL
    stderr: int | None = subprocess.DEVNULL
    if os.name == "nt":
        # 0x08000000 = CREATE_NO_WINDOW — keep the console hidden so
        # the user only sees the GUI window, not a phantom cmd box.
        creationflags = 0x08000000
    try:
        _SUPERVISED_CHILD = subprocess.Popen(
            args,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    except Exception:
        log.exception("failed to spawn service child process")
        return
    atexit.register(_terminate_child)


def _terminate_child() -> None:
    global _SUPERVISED_CHILD
    proc = _SUPERVISED_CHILD
    if proc is None:
        return
    if proc.poll() is not None:
        return
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
