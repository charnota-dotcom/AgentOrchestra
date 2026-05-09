"""Async wrapper around the `git` subprocess.

Why subprocess instead of pygit2: `git worktree` subcommands have
historically had patchy libgit2 coverage; subprocess is the canonical,
well-tested path.  We can swap inspection-only operations to pygit2
later for performance without changing the public API.

Every argument is passed positionally; we never use shell=True.  Every
call has a timeout.  Errors raise GitCLIError with stderr captured.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from apps.service.types import WorktreeError


class GitCLIError(WorktreeError):
    def __init__(self, args: list[str], code: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args)} failed [{code}]: {stderr.strip()}")
        self.args = args
        self.code = code
        self.stderr = stderr


@dataclass(frozen=True)
class GitResult:
    code: int
    stdout: str
    stderr: str


_BRANCH_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9/_-]*$")


def validate_branch_name(name: str) -> None:
    if not _BRANCH_NAME_RE.match(name):
        raise GitCLIError(["validate-branch"], 1, f"illegal branch name: {name!r}")


async def git(
    *args: str,
    cwd: Path | str | None = None,
    timeout: float = 60.0,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> GitResult:
    """Run `git <args>` and return the captured result.

    Raises GitCLIError when check=True and the exit code is non-zero.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise GitCLIError(list(args), -1, "timeout") from None
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    code = proc.returncode if proc.returncode is not None else -1
    if check and code != 0:
        raise GitCLIError(list(args), code, stderr)
    return GitResult(code=code, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Higher-level helpers
# ---------------------------------------------------------------------------


async def is_git_repo(path: Path) -> bool:
    try:
        r = await git("rev-parse", "--is-inside-work-tree", cwd=path, check=False)
        return r.code == 0 and r.stdout.strip() == "true"
    except FileNotFoundError:
        return False


async def is_bare_repo(path: Path) -> bool:
    r = await git("rev-parse", "--is-bare-repository", cwd=path, check=False)
    return r.code == 0 and r.stdout.strip() == "true"


async def resolve_ref(path: Path, ref: str) -> str:
    r = await git("rev-parse", "--verify", ref, cwd=path)
    sha = r.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GitCLIError(["rev-parse", ref], 0, f"unexpected sha: {sha}")
    return sha


async def current_branch(path: Path) -> str:
    r = await git("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    return r.stdout.strip()


async def list_branches(path: Path, prefix: str = "") -> list[str]:
    r = await git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=path)
    names = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    return names


async def add_worktree(repo: Path, target: Path, branch: str, base_ref: str) -> None:
    validate_branch_name(branch)
    target.parent.mkdir(parents=True, exist_ok=True)
    await git(
        "worktree",
        "add",
        "-b",
        branch,
        str(target),
        base_ref,
        cwd=repo,
        timeout=120.0,
    )


async def remove_worktree(repo: Path, target: Path, *, force: bool = True) -> None:
    args = ["worktree", "remove", str(target)]
    if force:
        args.append("--force")
    # `git worktree remove` returns non-zero if the worktree is missing;
    # treat that as success after a prune.
    r = await git(*args, cwd=repo, check=False)
    if r.code != 0:
        await git("worktree", "prune", cwd=repo, check=False)
    await git("worktree", "prune", cwd=repo, check=False)


async def delete_branch(repo: Path, branch: str) -> None:
    validate_branch_name(branch)
    await git("branch", "-D", branch, cwd=repo, check=False)


async def update_ref(repo: Path, ref: str, sha: str) -> None:
    await git("update-ref", ref, sha, cwd=repo)


async def delete_ref_namespace(repo: Path, prefix: str) -> int:
    """Delete every ref whose name starts with `prefix`.  Returns count."""
    r = await git("for-each-ref", "--format=%(refname)", prefix, cwd=repo)
    refs = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    for ref in refs:
        await git("update-ref", "-d", ref, cwd=repo, check=False)
    return len(refs)


async def add_to_info_exclude(repo: Path, patterns: list[str]) -> None:
    """Append patterns to `.git/info/exclude` if missing.  Idempotent."""
    git_dir_res = await git("rev-parse", "--git-common-dir", cwd=repo)
    git_dir = Path(git_dir_res.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (repo / git_dir).resolve()
    info = git_dir / "info"
    info.mkdir(parents=True, exist_ok=True)
    excl = info / "exclude"
    existing = excl.read_text() if excl.exists() else ""
    appended = []
    for p in patterns:
        if p not in existing.splitlines():
            appended.append(p)
    if appended:
        with excl.open("a") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("# AgentOrchestra:\n")
            for p in appended:
                fh.write(p + "\n")


async def commit_files(
    repo: Path,
    files: list[str],
    message: str,
    *,
    no_verify: bool = False,
) -> str:
    """Stage `files` and create a commit.  Returns the new SHA."""
    if not files:
        raise GitCLIError(["commit"], 1, "no files to commit")
    await git("add", "--", *files, cwd=repo)
    args = ["commit", "-m", message]
    if no_verify:
        args.append("--no-verify")
    r = await git(*args, cwd=repo, check=False)
    if r.code != 0:
        # Nothing to commit (empty diff) is not exceptional for our flow.
        if "nothing to commit" in (r.stdout + r.stderr).lower():
            raise GitCLIError(args, r.code, "nothing to commit")
        raise GitCLIError(args, r.code, r.stderr)
    head = await git("rev-parse", "HEAD", cwd=repo)
    return head.stdout.strip()


async def merge_into(
    repo: Path, base_branch: str, source_branch: str, *, message: str | None = None
) -> str:
    """Merge `source_branch` into `base_branch` and return the resulting SHA.

    Caller must have already checked out `base_branch` in this worktree.
    """
    args = ["merge", "--no-ff"]
    if message:
        args += ["-m", message]
    args.append(source_branch)
    await git(*args, cwd=repo, timeout=180.0)
    head = await git("rev-parse", "HEAD", cwd=repo)
    return head.stdout.strip()


async def changed_files(repo: Path, base_ref: str, head_ref: str = "HEAD") -> list[str]:
    r = await git("diff", "--name-only", f"{base_ref}..{head_ref}", cwd=repo)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


async def diff(repo: Path, base_ref: str, head_ref: str = "HEAD") -> str:
    r = await git("diff", f"{base_ref}..{head_ref}", cwd=repo, timeout=120.0)
    return r.stdout
