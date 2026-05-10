"""WorktreeManager — owns every git worktree the orchestrator creates.

See docs/dev/worktree-design.md for the full design.  This file
implements: workspace registration, Run-scoped worktree creation with
optional uncommitted-state import, commit-on-step-boundary, the three
merge modes (clean / assisted / manual scaffolded), stale detection,
panic reset.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Literal

from apps.service.store.events import EventStore
from apps.service.types import (
    Branch,
    BranchState,
    Event,
    EventKind,
    EventSource,
    PersonalityCard,
    Workspace,
    WorktreeError,
    assert_branch_transition,
    is_path_inside,
    short_id,
    utc_now,
)
from apps.service.worktrees import git_cli as g
from apps.service.worktrees import merger as _merger

log = logging.getLogger(__name__)

WORKTREE_DIR = ".agent-worktrees"
EXCLUDE_PATTERNS = [WORKTREE_DIR + "/", ".agentorchestra-lock"]


class _WorkspaceLock:
    """Cross-platform advisory lock per workspace."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: int | None = None

    async def acquire(self, timeout: float = 10.0) -> None:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        # Spin-acquire; on POSIX use fcntl, on Windows use msvcrt.
        while True:
            try:
                self._fh = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
                _platform_lock(self._fh)
                return
            except BlockingIOError:
                if self._fh is not None:
                    os.close(self._fh)
                    self._fh = None
                if loop.time() > deadline:
                    raise WorktreeError(f"timed out acquiring workspace lock {self.path}") from None
                await asyncio.sleep(0.05)

    async def release(self) -> None:
        if self._fh is not None:
            _platform_unlock(self._fh)
            os.close(self._fh)
            self._fh = None


def _platform_lock(fh: int) -> None:
    try:
        import fcntl

        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    except ImportError:
        pass
    try:
        import msvcrt

        msvcrt.locking(fh, msvcrt.LK_NBLCK, 1)
    except ImportError:
        # Last resort: no locking.  Tests still pass; production
        # builds run on POSIX or Windows.
        pass


def _platform_unlock(fh: int) -> None:
    try:
        import fcntl

        fcntl.flock(fh, fcntl.LOCK_UN)
        return
    except ImportError:
        pass
    try:
        import msvcrt

        msvcrt.locking(fh, msvcrt.LK_UNLCK, 1)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class WorktreeManager:
    def __init__(self, store: EventStore) -> None:
        self.store = store
        self._workspace_locks: dict[str, _WorkspaceLock] = {}
        self._row_locks: dict[str, asyncio.Lock] = {}

    # ----- Workspaces -------------------------------------------------

    async def register_workspace(
        self, repo_path: Path, name: str | None = None, default_base_branch: str = "main"
    ) -> Workspace:
        repo = repo_path.resolve()
        if not await g.is_git_repo(repo):
            raise WorktreeError(f"{repo} is not a git working tree")
        if await g.is_bare_repo(repo):
            raise WorktreeError(f"{repo} is a bare repo; only working trees are supported")

        existing = [b for b in await g.list_branches(repo, prefix="agent/")]
        if existing:
            raise WorktreeError(
                "workspace already has branches under 'agent/' "
                f"namespace ({len(existing)}); please rename or remove them"
            )

        # Make sure .agent-worktrees/ is excluded so build artifacts inside
        # don't pollute the user's status.
        await g.add_to_info_exclude(repo, EXCLUDE_PATTERNS)

        ws = Workspace(
            name=name or repo.name,
            repo_path=str(repo),
            default_base_branch=default_base_branch,
        )
        await self.store.insert_workspace(ws)
        await self.store.append_event(
            Event(
                source=EventSource.SYSTEM,
                kind=EventKind.SERVICE_STARTED,
                workspace_id=ws.id,
                text=f"workspace registered: {ws.name} at {ws.repo_path}",
            )
        )
        return ws

    async def clone_workspace(
        self,
        url: str,
        *,
        dest_dir: Path,
        name: str | None = None,
        branch: str | None = None,
        depth: int | None = None,
    ) -> Workspace:
        """Clone a remote git URL into ``dest_dir`` and register it as a
        Workspace.

        ``url`` must not start with a hyphen (otherwise git would parse
        it as an option and we'd be a confused-deputy for whatever flag
        a malicious URL injected).  ``dest_dir`` must not already exist
        — we refuse to clone over arbitrary filesystem state.

        Optional ``branch`` checks out a branch on clone (--branch).
        Optional ``depth`` requests a shallow clone for big repos.
        """
        # Operator-supplied input goes straight into argv; reject the
        # obvious option-injection vector and zero-byte / control chars.
        if not url or url.startswith("-"):
            raise WorktreeError(f"invalid git URL: {url!r}")
        if any(c in url for c in ("\n", "\r", "\x00")):
            raise WorktreeError("git URL contains control characters")
        if branch is not None and (
            not branch
            or branch.startswith("-")
            or any(c in branch for c in ("\n", "\r", "\x00", " "))
        ):
            raise WorktreeError(f"invalid branch name: {branch!r}")
        if dest_dir.exists():
            raise WorktreeError(f"clone destination already exists: {dest_dir}")
        dest_dir.parent.mkdir(parents=True, exist_ok=True)

        args = ["git", "clone", "--quiet"]
        if depth is not None and depth > 0:
            args.extend(["--depth", str(int(depth))])
        if branch is not None:
            args.extend(["--branch", branch])
        # `--` so a URL beginning with `-` (already rejected above) or
        # a path that looks like an option isn't reinterpreted by git.
        args.extend(["--", url, str(dest_dir)])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=300.0
            )
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.communicate(), timeout=2.0)
            raise WorktreeError("git clone timed out after 5 minutes") from None
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            # Best-effort cleanup so a half-finished clone doesn't trap
            # the operator from retrying with a different URL.
            with contextlib.suppress(Exception):
                import shutil as _sh

                _sh.rmtree(dest_dir, ignore_errors=True)
            raise WorktreeError(f"git clone failed: {err[:500]}")

        return await self.register_workspace(dest_dir, name=name)

    # ----- Locks ------------------------------------------------------

    def _get_workspace_lock(self, ws: Workspace) -> _WorkspaceLock:
        if ws.id not in self._workspace_locks:
            lock_path = Path(ws.repo_path) / WORKTREE_DIR / ".lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._workspace_locks[ws.id] = _WorkspaceLock(lock_path)
        return self._workspace_locks[ws.id]

    def _get_row_lock(self, branch_id: str) -> asyncio.Lock:
        if branch_id not in self._row_locks:
            self._row_locks[branch_id] = asyncio.Lock()
        return self._row_locks[branch_id]

    # ----- Lifecycle: create -----------------------------------------

    async def create(
        self,
        run_id: str,
        workspace: Workspace,
        card: PersonalityCard,
        *,
        base_branch: str | None = None,
        include_uncommitted: bool = False,
    ) -> Branch:
        repo = Path(workspace.repo_path)
        base = base_branch or workspace.default_base_branch
        ws_lock = self._get_workspace_lock(workspace)
        await ws_lock.acquire()
        try:
            base_sha = await g.resolve_ref(repo, base)
            short_run = run_id  # already 8 chars
            branch_name = f"agent/{card.archetype}/{short_run}"
            wt_dir = repo / WORKTREE_DIR / f"{card.archetype}-{short_run}"
            if wt_dir.exists():
                raise WorktreeError(f"worktree path already exists: {wt_dir}")

            # Optionally import the user's uncommitted state.
            stash_ref: str | None = None
            if include_uncommitted:
                stash_ref = await self._stash_uncommitted(repo)

            try:
                await g.add_worktree(repo, wt_dir, branch_name, base_sha)
            except Exception:
                if stash_ref:
                    # Restore the user's stash if creation failed.
                    with contextlib.suppress(Exception):
                        await g.git("stash", "pop", stash_ref, cwd=repo, check=False)
                raise

            if include_uncommitted and stash_ref:
                await self._apply_stash_into_worktree(wt_dir, repo, stash_ref)

            branch = Branch(
                run_id=run_id,
                workspace_id=workspace.id,
                base_ref=base_sha,
                base_branch_name=base,
                agent_branch_name=branch_name,
                worktree_path=str(wt_dir.resolve()),
                state=BranchState.CREATED,
                include_uncommitted=include_uncommitted,
            )
            await self.store.insert_branch(branch)

            # Transition CREATED -> ACTIVE eagerly; the Run loop will start
            # writing commits immediately.
            await self._transition(branch, BranchState.ACTIVE)

            await self.store.append_event(
                Event(
                    source=EventSource.SYSTEM,
                    kind=EventKind.WORKTREE_CREATED,
                    run_id=run_id,
                    branch_id=branch.id,
                    workspace_id=workspace.id,
                    payload={
                        "agent_branch_name": branch_name,
                        "base_ref": base_sha,
                        "worktree_path": branch.worktree_path,
                    },
                    text=f"worktree created: {branch_name}",
                )
            )
            return branch
        finally:
            await ws_lock.release()

    async def _stash_uncommitted(self, repo: Path) -> str | None:
        # Returns the stash ref or None if there was nothing to stash.
        st = await g.git("status", "--porcelain", cwd=repo, check=False)
        if not st.stdout.strip():
            return None
        await g.git(
            "stash",
            "push",
            "--include-untracked",
            "--keep-index",
            "-m",
            "agentorchestra:import-uncommitted",
            cwd=repo,
        )
        # The most-recent stash is stash@{0}; capture the SHA so we
        # can drop it after applying.
        info = await g.git("rev-parse", "stash@{0}", cwd=repo)
        return info.stdout.strip()

    async def _apply_stash_into_worktree(self, wt: Path, repo: Path, stash_ref: str) -> None:
        # Apply the stash into the worktree, stage everything, and commit.
        # Then drop the stash from the workspace side.
        await g.git("stash", "apply", stash_ref, cwd=wt, check=False)
        await g.git("add", "-A", cwd=wt)
        await g.git(
            "commit",
            "-m",
            "Imported uncommitted state from your workspace",
            cwd=wt,
            check=False,
        )
        await g.git("stash", "drop", stash_ref, cwd=repo, check=False)

    # ----- Lifecycle: commit / pause / review ------------------------

    async def commit(
        self,
        branch_id: str,
        files: list[str],
        message: str,
        *,
        no_verify: bool = False,
    ) -> str:
        branch = await self._must_get_branch(branch_id)
        if branch.state not in {BranchState.ACTIVE, BranchState.PAUSED}:
            raise WorktreeError(f"cannot commit in state {branch.state.value}")
        sha = await g.commit_files(
            Path(branch.worktree_path),
            files=files,
            message=message,
            no_verify=no_verify,
        )
        # Update tracking ref so the Run is reachable even after branch deletion.
        repo = await self._workspace_repo(branch.workspace_id)
        await g.update_ref(repo, f"refs/agentorchestra/runs/{branch.run_id}", sha)
        await self.store.update_branch_state(branch_id, branch.state, last_commit_sha=sha)
        await self.store.append_event(
            Event(
                source=EventSource.DISPATCH_RUN,
                kind=EventKind.COMMIT_CREATED,
                run_id=branch.run_id,
                branch_id=branch.id,
                workspace_id=branch.workspace_id,
                payload={"sha": sha, "files": files, "message": message},
                text=message,
            )
        )
        return sha

    async def pause(self, branch_id: str) -> None:
        branch = await self._must_get_branch(branch_id)
        await self._transition(branch, BranchState.PAUSED)

    async def resume(self, branch_id: str) -> None:
        branch = await self._must_get_branch(branch_id)
        await self._transition(branch, BranchState.ACTIVE)

    async def request_review(self, branch_id: str) -> dict:
        branch = await self._must_get_branch(branch_id)
        await self._transition(branch, BranchState.AWAITING_REVIEW)
        repo = await self._workspace_repo(branch.workspace_id)
        files = await g.changed_files(repo, branch.base_ref, branch.agent_branch_name)
        return {
            "branch_id": branch.id,
            "run_id": branch.run_id,
            "agent_branch": branch.agent_branch_name,
            "base_ref": branch.base_ref,
            "changed_files": files,
            "diff": await g.diff(repo, branch.base_ref, branch.agent_branch_name),
        }

    # ----- Lifecycle: merge / reject / abandon -----------------------

    MergeMode = Literal["clean", "assisted", "manual"]

    async def approve_and_merge(self, branch_id: str, mode: MergeMode = "clean") -> dict:
        branch = await self._must_get_branch(branch_id)
        ws_lock = self._get_workspace_lock(await self._must_get_workspace(branch.workspace_id))
        await ws_lock.acquire()
        try:
            await self._transition(branch, BranchState.MERGING)
            repo = await self._workspace_repo(branch.workspace_id)

            # Move HEAD in the main checkout to the base branch.
            await g.git("checkout", branch.base_branch_name, cwd=repo)

            try:
                if mode == "clean":
                    sha = await g.merge_into(
                        repo,
                        branch.base_branch_name,
                        branch.agent_branch_name,
                        message=(f"Merge {branch.agent_branch_name}\n\nrun: {branch.run_id}"),
                    )
                elif mode == "assisted":
                    # Install Mergiraf as the per-repo merge driver and
                    # rely on git's `merge.<driver>` config to invoke it
                    # for tree-sitter-aware files.  Falls back to a
                    # regular 3-way merge if Mergiraf isn't installed.
                    installed = await _merger.install_as_merge_driver(repo)
                    if not installed:
                        log.info("mergiraf not available; falling back to normal merge")
                    sha = await g.merge_into(
                        repo,
                        branch.base_branch_name,
                        branch.agent_branch_name,
                        message=(
                            f"Merge {branch.agent_branch_name} (assisted)\n\nrun: {branch.run_id}"
                        ),
                    )
                else:  # "manual"
                    sha = await g.merge_into(
                        repo,
                        branch.base_branch_name,
                        branch.agent_branch_name,
                        message=(
                            f"Merge {branch.agent_branch_name} (manual)\n\nrun: {branch.run_id}"
                        ),
                    )
            except g.GitCLIError as exc:
                # Conflict surface: state -> CONFLICTED for manual mode,
                # otherwise propagate.
                if "conflict" in (exc.stderr or "").lower() and mode != "clean":
                    await self._transition(branch, BranchState.CONFLICTED)
                    return {
                        "merged": False,
                        "conflicted": True,
                        "branch_id": branch.id,
                        "stderr": exc.stderr,
                    }
                raise

            await self._transition(branch, BranchState.MERGED)
            await self._cleanup(branch)
            return {"merged": True, "branch_id": branch.id, "merge_sha": sha}
        finally:
            await ws_lock.release()

    async def reject(self, branch_id: str, reason: str) -> None:
        branch = await self._must_get_branch(branch_id)
        await self._transition(branch, BranchState.REJECTED)
        await self.store.append_event(
            Event(
                source=EventSource.DISPATCH_RUN,
                kind=EventKind.WORKTREE_STATE_CHANGED,
                run_id=branch.run_id,
                branch_id=branch.id,
                workspace_id=branch.workspace_id,
                payload={"reason": reason, "state": "rejected"},
                text=f"rejected: {reason}",
            )
        )
        await self._cleanup(branch)

    async def abandon(self, branch_id: str, reason: str) -> None:
        branch = await self._must_get_branch(branch_id)
        await self._transition(branch, BranchState.ABANDONED)
        await self.store.append_event(
            Event(
                source=EventSource.DISPATCH_RUN,
                kind=EventKind.WORKTREE_STATE_CHANGED,
                run_id=branch.run_id,
                branch_id=branch.id,
                workspace_id=branch.workspace_id,
                payload={"reason": reason, "state": "abandoned"},
                text=f"abandoned: {reason}",
            )
        )
        # Abandoned worktrees are NOT auto-cleaned; user can resume later.

    # ----- Stale sweep & GC ------------------------------------------

    async def sweep_stale(
        self, workspace_id: str | None = None, *, default_minutes: int = 60
    ) -> list[str]:
        active = await self.store.list_branches_by_state(
            workspace_id=workspace_id,
            states=[BranchState.ACTIVE, BranchState.PAUSED],
        )
        now = utc_now()
        flagged: list[str] = []
        for b in active:
            cutoff = now - timedelta(minutes=default_minutes)
            last = b.last_commit_at or b.created_at
            if last >= cutoff:
                continue
            if b.process_pid and _is_pid_alive(b.process_pid):
                continue
            await self._transition(b, BranchState.STALE)
            flagged.append(b.id)
        return flagged

    async def panic_reset(self, workspace_id: str) -> dict:
        ws = await self._must_get_workspace(workspace_id)
        ws_lock = self._get_workspace_lock(ws)
        await ws_lock.acquire()
        try:
            non_terminal = await self.store.list_branches_by_state(
                workspace_id=workspace_id,
                states=[
                    BranchState.CREATED,
                    BranchState.ACTIVE,
                    BranchState.PAUSED,
                    BranchState.AWAITING_REVIEW,
                    BranchState.MERGING,
                    BranchState.CONFLICTED,
                    BranchState.STALE,
                ],
            )
            cleaned: list[str] = []
            for b in non_terminal:
                with contextlib.suppress(Exception):
                    await self._transition(b, BranchState.ABANDONED)
                with contextlib.suppress(Exception):
                    await self._cleanup(b)
                cleaned.append(b.id)
            repo = Path(ws.repo_path)
            await g.git("worktree", "prune", cwd=repo, check=False)
            await g.delete_ref_namespace(repo, "refs/agentorchestra/runs")
            await self.store.append_event(
                Event(
                    source=EventSource.SYSTEM,
                    kind=EventKind.PANIC_RESET,
                    workspace_id=workspace_id,
                    payload={"branch_ids": cleaned},
                    text=f"panic reset: {len(cleaned)} branches",
                )
            )
            return {"reset": len(cleaned), "branch_ids": cleaned}
        finally:
            await ws_lock.release()

    # ----- Internals --------------------------------------------------

    async def _cleanup(self, branch: Branch) -> None:
        repo = await self._workspace_repo(branch.workspace_id)
        wt = Path(branch.worktree_path)
        try:
            await g.remove_worktree(repo, wt, force=True)
            await g.delete_branch(repo, branch.agent_branch_name)
            await self._transition(branch, BranchState.CLEANED)
            await self.store.append_event(
                Event(
                    source=EventSource.SYSTEM,
                    kind=EventKind.WORKTREE_CLEANED,
                    run_id=branch.run_id,
                    branch_id=branch.id,
                    workspace_id=branch.workspace_id,
                    text=f"cleaned: {branch.agent_branch_name}",
                )
            )
        except Exception as exc:
            log.exception("cleanup failed for branch %s", branch.id)
            await self.store.append_event(
                Event(
                    source=EventSource.SYSTEM,
                    kind=EventKind.CLEANUP_FAILED,
                    run_id=branch.run_id,
                    branch_id=branch.id,
                    workspace_id=branch.workspace_id,
                    payload={"error": str(exc)},
                    text=f"cleanup failed: {exc}",
                )
            )

    async def _transition(self, branch: Branch, to: BranchState) -> None:
        async with self._get_row_lock(branch.id):
            assert_branch_transition(branch.state, to)
            old = branch.state
            branch.state = to
            await self.store.update_branch_state(branch.id, to)
            await self.store.append_event(
                Event(
                    source=EventSource.SYSTEM,
                    kind=EventKind.WORKTREE_STATE_CHANGED,
                    run_id=branch.run_id,
                    branch_id=branch.id,
                    workspace_id=branch.workspace_id,
                    payload={"from": old.value, "to": to.value},
                    text=f"{old.value} -> {to.value}",
                )
            )

    async def _must_get_branch(self, branch_id: str) -> Branch:
        b = await self.store.get_branch(branch_id)
        if not b:
            raise WorktreeError(f"unknown branch: {branch_id}")
        return b

    async def _must_get_workspace(self, workspace_id: str) -> Workspace:
        ws = await self.store.get_workspace(workspace_id)
        if not ws:
            raise WorktreeError(f"unknown workspace: {workspace_id}")
        return ws

    async def _workspace_repo(self, workspace_id: str) -> Path:
        ws = await self._must_get_workspace(workspace_id)
        return Path(ws.repo_path)

    # ----- Path-traversal validator (used by tool-call gates) --------

    @staticmethod
    def is_write_path_safe(branch: Branch, target: Path) -> bool:
        return is_path_inside(target, Path(branch.worktree_path))


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# Helper used by Run dispatcher when synthesising an ID before the row exists.
def new_run_id() -> str:
    return short_id(8)
