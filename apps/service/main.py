"""Orchestrator service entrypoint.

Boots the SQLite store, seeds default cards, starts the JSONL watcher,
mounts the JSON-RPC server, and serves until SIGINT/SIGTERM.

Run with: ``agentorchestra-service`` (installed by pyproject scripts).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path
from typing import Any

import uvicorn

from apps.service.cards.seed import seed_default_cards
from apps.service.cost.meter import forecast as cost_forecast
from apps.service.dispatch.bus import EventBus
from apps.service.dispatch.dispatcher import RunDispatcher
from apps.service.dispatch.drift_sentinel import DriftSentinel
from apps.service.flows import FlowExecutor
from apps.service.ingestion.hook_installer import (
    install as install_hook,
)
from apps.service.ingestion.hook_installer import (
    status as hook_status,
)
from apps.service.ingestion.hook_installer import (
    uninstall as uninstall_hook,
)
from apps.service.ingestion.jsonl_watcher import JSONLWatcher
from apps.service.ipc.server import JsonRpcServer
from apps.service.linter.preflight import lint
from apps.service.mcp import registry as mcp_registry
from apps.service.providers.registry import known_providers
from apps.service.secrets.keyring_store import hook_token
from apps.service.store.events import EventStore
from apps.service.templates.engine import render
from apps.service.types import (
    BlueprintVersionConflict,
    DroneAction,
    DroneBlueprint,
    DroneRole,
    Event,
    EventKind,
    EventSource,
    Flow,
    Instruction,
    long_id,
    utc_now,
)
from apps.service.worktrees.manager import WorktreeManager

log = logging.getLogger(__name__)


DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "agentorchestra"


def _data_dir() -> Path:
    p = DEFAULT_DATA_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _first_descriptive_line(text: str) -> str:
    """Return the first non-empty, non-front-matter, non-marker line of
    a markdown blob, trimmed.  Used by ``skills.list`` to derive a
    one-line description for each Claude Code skill file.

    Skips:
      * YAML front-matter delimiters (``---``).
      * Blank lines.
      * Heading markers (lines starting with ``#``) — those are usually
        just the skill name.

    Returns ``""`` if nothing useful is found in the first 40 lines.
    """
    in_frontmatter = False
    for i, raw in enumerate(text.splitlines()):
        if i > 40:
            break
        line = raw.strip()
        if not line:
            continue
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            # Inside YAML; pick up `description:` if it shows up.
            if line.lower().startswith("description:"):
                return line.split(":", 1)[1].strip().strip("\"'")
            continue
        if line.startswith("#"):
            continue
        return line[:200]
    return ""


# ---------------------------------------------------------------------------
# Drone authority — see docs/DRONE_MODEL.md ("Authority matrix").
#
# Encodes the role -> (op, scope) permission matrix as a single
# function so the RPC layer + the authority unit-tests share the
# exact same source of truth.  Raises ``PermissionError`` on denial;
# the caller wraps that into a JSON-RPC error.
# ---------------------------------------------------------------------------


_DRONE_OPS = frozenset({"append_reference", "append_skill", "append_attachment"})


def _check_drone_authority(
    actor_role: DroneRole,
    op: str,
    *,
    is_self: bool,
) -> None:
    """Gate a cross-action mutation against the actor's snapshotted role.

    ``is_self=True`` means the action is mutating itself (e.g. a
    drone appending a skill to its own action row).  Auditors are
    read-only even on self.
    """
    if op not in _DRONE_OPS:
        raise ValueError(f"unknown drone op: {op}")
    if actor_role is DroneRole.AUDITOR:
        # Auditors observe; they never mutate, including their own
        # action.  Defence in depth — keeps a compromised auditor
        # blueprint from being repurposed as a write surface.
        raise PermissionError(f"auditor drones are read-only ({op} denied)")
    if is_self:
        # Worker / Supervisor / Courier can all mutate themselves.
        return
    # Cross-action mutation — narrower gate.
    if actor_role is DroneRole.WORKER:
        raise PermissionError(f"worker drones cannot {op} on other actions")
    if actor_role is DroneRole.COURIER and op != "append_reference":
        # Couriers carry context references between drones but don't
        # add skills or attachments — keeps the surface tight.
        raise PermissionError(f"courier drones can only append_reference, not {op}")
    # Supervisor: any op on any peer.


# ---------------------------------------------------------------------------
# RPC method handlers
# ---------------------------------------------------------------------------


class Handlers:
    def __init__(
        self,
        store: EventStore,
        manager: WorktreeManager,
        dispatcher: RunDispatcher,
        flow_executor: FlowExecutor | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.manager = manager
        self.dispatcher = dispatcher
        self.flow_executor = flow_executor or FlowExecutor(store)
        self.data_dir = data_dir or _data_dir()
        # Per-action serialisation: two concurrent drones.send for the
        # same action_id used to fetch v1, both append, and the later
        # writer would overwrite the earlier turn (lost-update).  Each
        # send takes the action's own lock for the full
        # fetch->mutate->LLM->store cycle.
        self._action_send_locks: dict[str, asyncio.Lock] = {}
        self._action_send_locks_guard = asyncio.Lock()

    async def _lock_for_action(self, action_id: str) -> asyncio.Lock:
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("action_id required")
        async with self._action_send_locks_guard:
            lock = self._action_send_locks.get(action_id)
            if lock is None:
                exists = await self.store.get_drone_action(action_id)
                if exists is None:
                    raise ValueError(f"unknown action: {action_id}")
                lock = asyncio.Lock()
                self._action_send_locks[action_id] = lock
            return lock

    async def workspaces_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [w.model_dump(mode="json") for w in await self.store.list_workspaces()]

    async def workspaces_register(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(params["path"])
        ws = await self.manager.register_workspace(
            path,
            name=params.get("name"),
            default_base_branch=params.get("default_base_branch", "main"),
        )
        return ws.model_dump(mode="json")

    async def workspaces_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.store.delete_workspace(params["workspace_id"])
        return {"removed": bool(ok)}

    async def workspaces_clone(self, params: dict[str, Any]) -> dict[str, Any]:
        """Clone a remote git URL into a managed directory and register
        it as a Workspace so a coding-session agent can be bound to it
        in one click.

        ``params``:
          url:    git remote URL (https / ssh).  Must not start with -.
          name:   optional display name; defaults to the repo name
                  derived from the URL.
          branch: optional branch to check out on clone.
          depth:  optional shallow-clone depth for big repos.
          dest:   optional explicit clone destination; defaults to
                  ``<data_dir>/clones/<sanitized_repo_name>``.
        """
        import re as _re

        def sanitize_filename(name: str) -> str:
            """Strip path separators + non-safe chars so a clone dir
            name lands somewhere predictable.  Inline replacement for
            the deleted apps.service.attachments.render helper.
            """
            from pathlib import Path as _Path

            base = _Path(name).name
            cleaned = _re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
            return cleaned or "upload"

        url = (params.get("url") or "").strip()
        if not url:
            raise ValueError("url required")
        # Derive a friendly directory name from the URL so the operator
        # gets something readable in the clones/ folder.
        derived = url.rstrip("/").rsplit("/", 1)[-1]
        if derived.endswith(".git"):
            derived = derived[: -len(".git")]
        derived = sanitize_filename(derived) or "clone"
        clones_root = (self.data_dir / "clones").resolve()
        clones_root.mkdir(parents=True, exist_ok=True)
        dest_str = params.get("dest")
        if dest_str:
            # Operator-supplied dest is allowed but locked to the
            # managed clones/ directory.  Refuse any path that resolves
            # outside (e.g. /etc, ~/.ssh) — the loopback RPC is local-
            # auth only but we still don't want to be a confused-deputy.
            dest = Path(dest_str).resolve()
            if not dest.is_relative_to(clones_root):
                raise ValueError(
                    f"clone dest {dest} must be inside the managed clones directory ({clones_root})"
                )
        else:
            dest = clones_root / derived
            # Append a numeric suffix if a clones/<name> already exists.
            n = 2
            while dest.exists():
                dest = clones_root / f"{derived}-{n}"
                n += 1

        ws = await self.manager.clone_workspace(
            url,
            dest_dir=dest,
            name=(params.get("name") or derived),
            branch=params.get("branch") or None,
            depth=params.get("depth"),
        )
        # Detect the actual default branch from origin/HEAD instead of
        # leaving it stuck at "main" (which breaks flows.dispatch on
        # repos whose default is master / trunk / develop).  Best-effort:
        # any failure here just leaves the default we already stored.
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(dest),
                "symbolic-ref",
                "--short",
                "refs/remotes/origin/HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode == 0:
                ref = out.decode("utf-8", errors="replace").strip()
                # Strip leading "origin/" prefix.
                if ref.startswith("origin/"):
                    detected = ref[len("origin/") :]
                    if detected and detected != ws.default_base_branch:
                        ws.default_base_branch = detected
                        # Re-persist with the correct base branch.
                        async with self.store._lock:
                            await self.store.db.execute(
                                "UPDATE workspaces SET default_base_branch = ? WHERE id = ?",
                                (detected, ws.id),
                            )
                            await self.store.db.commit()
        except Exception:
            log.debug(
                "couldn't detect default branch for cloned workspace %s", ws.id, exc_info=True
            )
        return ws.model_dump(mode="json")

    async def workspaces_git_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Live git state of a workspace.  The chat dialog reads this
        on open + after each send so the operator sees what the agent
        is actually working against.

        Returns: branch, ahead, behind, modified (count), staged (count),
        untracked (count), last_commit (sha+subject), is_git.
        """
        ws = await self.store.get_workspace(params["workspace_id"])
        if not ws:
            raise ValueError(f"unknown workspace: {params['workspace_id']}")
        repo = Path(ws.repo_path)
        if not (repo / ".git").exists() and not repo.is_dir():
            return {"is_git": False, "repo_path": str(repo)}

        async def _git(*args: str, timeout: float = 5.0) -> tuple[int, str, str]:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo),
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.communicate(), timeout=2.0)
                return -1, "", "timed out"
            return (
                proc.returncode or 0,
                stdout_b.decode("utf-8", errors="replace"),
                stderr_b.decode("utf-8", errors="replace"),
            )

        # Fast probe: is it actually a working tree?
        rc, _out, _err = await _git("rev-parse", "--is-inside-work-tree")
        if rc != 0:
            return {"is_git": False, "repo_path": str(repo)}

        rc_b, branch_out, _ = await _git("rev-parse", "--abbrev-ref", "HEAD")
        branch = branch_out.strip() if rc_b == 0 else "?"
        # ahead/behind upstream — gracefully degrade if no upstream.
        ahead = behind = 0
        rc_ab, ab_out, _ = await _git("rev-list", "--left-right", "--count", "HEAD...@{u}")
        if rc_ab == 0 and ab_out.strip():
            try:
                a_str, b_str = ab_out.strip().split()
                ahead, behind = int(a_str), int(b_str)
            except ValueError:
                pass
        # Status counts via porcelain v2 to keep the output stable.
        rc_s, status_out, _ = await _git("status", "--porcelain=v1")
        modified = staged = untracked = 0
        if rc_s == 0:
            for line in status_out.splitlines():
                if not line:
                    continue
                code = line[:2]
                if code == "??":
                    untracked += 1
                else:
                    if code[0] != " ":
                        staged += 1
                    if code[1] != " ":
                        modified += 1
        # Last commit (subject only — short for the GUI banner).
        last_sha = ""
        last_subj = ""
        rc_l, log_out, _ = await _git("log", "-1", "--format=%h %s", "--no-color")
        if rc_l == 0 and log_out.strip():
            parts = log_out.strip().split(maxsplit=1)
            last_sha = parts[0]
            last_subj = parts[1] if len(parts) > 1 else ""
        return {
            "is_git": True,
            "repo_path": str(repo),
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "modified": modified,
            "staged": staged,
            "untracked": untracked,
            "last_commit_sha": last_sha,
            "last_commit_subject": last_subj,
        }

    async def workspaces_switch_branch(self, params: dict[str, Any]) -> dict[str, Any]:
        """Switch (or create + switch to) a branch in a workspace.

        ``params``:
          workspace_id: id of the workspace.
          branch:       branch name to switch to.
          create:       if true, pass -c so the branch is created from HEAD.
        """
        ws = await self.store.get_workspace(params["workspace_id"])
        if not ws:
            raise ValueError(f"unknown workspace: {params['workspace_id']}")
        branch = (params.get("branch") or "").strip()
        if not branch:
            raise ValueError("branch required")
        # Reject obvious option-injection so the operator can't be
        # tricked into passing `--upload-pack=…` etc.
        if branch.startswith("-") or any(c in branch for c in ("\n", "\r", "\x00", " ")):
            raise ValueError(f"invalid branch name: {branch!r}")
        create = bool(params.get("create"))
        repo = Path(ws.repo_path)
        # git switch shape:
        #   create:  `git switch -c <branch>`  (start-point defaults to HEAD)
        #   plain:   `git switch -- <branch>`  (`--` end-of-options separator)
        # We can't use `--` together with `-c` because `git switch -c <a> [<b>]`
        # treats `<b>` as the start-point — `-c -- foo` would mean "create
        # branch named `--` from start-point `foo`" which then errors with
        # "invalid reference: foo".  Branch names have already been validated
        # to not start with `-` so the create path is safe without `--`.
        if create:
            args = ["git", "-C", str(repo), "switch", "-c", branch]
        else:
            args = ["git", "-C", str(repo), "switch", "--", branch]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.communicate(), timeout=2.0)
            raise ValueError("git switch timed out") from None
        if proc.returncode != 0:
            err = stderr_b.decode("utf-8", errors="replace").strip()
            raise ValueError(f"git switch failed: {err[:300]}")
        return {"workspace_id": ws.id, "branch": branch, "created": create}

    async def workspaces_tree(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return a flat, gitignore-respecting file listing of a
        workspace's repo path so the GUI can show operators what
        an agent bound to this workspace can actually see.

        ``params``:
          workspace_id: id of the workspace.
          limit:        max files to return (default 500).
          query:        optional substring filter.

        Listing is hard-capped to ``limit`` files to keep large repos
        responsive.  Honours ``.gitignore`` by shelling out to
        ``git ls-files`` when the path is a git repo; falls back to a
        bounded recursive walk otherwise.
        """
        ws = await self.store.get_workspace(params["workspace_id"])
        if not ws:
            raise ValueError(f"unknown workspace: {params['workspace_id']}")
        limit = max(1, min(int(params.get("limit", 500) or 500), 5000))
        query = (params.get("query") or "").strip().lower()
        repo_path = Path(ws.repo_path)
        if not repo_path.is_dir():
            raise ValueError(f"workspace path does not exist: {ws.repo_path}")
        is_git = (repo_path / ".git").exists()
        files: list[str] = []
        truncated = False
        if is_git:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo_path),
                "ls-files",
                "-co",
                "--exclude-standard",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, _stderr_b = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            except TimeoutError:
                proc.kill()
                with contextlib.suppress(TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(proc.communicate(), timeout=2.0)
                raise ValueError("git ls-files timed out") from None
            for line in stdout_b.decode("utf-8", errors="replace").splitlines():
                if not line:
                    continue
                if query and query not in line.lower():
                    continue
                files.append(line)
                if len(files) >= limit:
                    truncated = True
                    break
        else:
            # Plain directory: bounded walk, skip dotdirs, hard cap.
            seen = 0
            for sub in repo_path.rglob("*"):
                if not sub.is_file():
                    continue
                # Skip anything under a dotdir (e.g. .git, .venv) so we
                # don't enumerate gigabytes of vendored deps.
                if any(p.startswith(".") for p in sub.relative_to(repo_path).parts[:-1]):
                    continue
                rel = str(sub.relative_to(repo_path))
                if query and query not in rel.lower():
                    seen += 1
                    if seen > limit * 10:
                        truncated = True
                        break
                    continue
                files.append(rel)
                if len(files) >= limit:
                    truncated = True
                    break
        return {
            "workspace_id": ws.id,
            "repo_path": ws.repo_path,
            "is_git": is_git,
            "files": files,
            "truncated": truncated,
            "count": len(files),
        }

    async def cards_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in await self.store.list_cards()]

    async def runs_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        runs = await self.store.list_runs(workspace_id=params.get("workspace_id"))
        return [r.model_dump(mode="json") for r in runs]

    async def search(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.store.search(params["query"], limit=params.get("limit", 50))

    async def lint_instruction(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        issues = lint(
            params["text"],
            archetype=params.get("archetype"),
            variables=params.get("variables"),
        )
        return [
            {
                "rule": i.rule,
                "severity": i.severity.value,
                "message": i.message,
                "field": i.field,
                "suggestion": i.suggestion,
            }
            for i in issues
        ]

    async def cost_forecast(self, params: dict[str, Any]) -> dict[str, Any]:
        f = cost_forecast(
            params["provider"],
            params["model"],
            rendered_prompt_tokens=params["rendered_prompt_tokens"],
            archetype=params.get("archetype"),
        )
        return {
            "low_usd": f.low_usd,
            "high_usd": f.high_usd,
            "expected_usd": f.expected_usd,
            "rationale": f.rationale,
        }

    async def render_template(self, params: dict[str, Any]) -> dict[str, Any]:
        template = await self.store.get_template(params["template_id"])
        if not template:
            raise ValueError(f"unknown template: {params['template_id']}")
        rendered = render(template, params.get("variables", {}))
        ins = Instruction(
            id=long_id(),
            template_id=template.id,
            template_version=template.version,
            card_id=params["card_id"],
            rendered_text=rendered,
            variables=params.get("variables", {}),
        )
        await self.store.insert_instruction(ins)
        return {"instruction_id": ins.id, "rendered_text": rendered}

    async def templates_get(self, params: dict[str, Any]) -> dict[str, Any]:
        template = await self.store.get_template(params["template_id"])
        if not template:
            raise ValueError(f"unknown template: {params['template_id']}")
        return {
            "id": template.id,
            "name": template.name,
            "archetype": template.archetype,
            "version": template.version,
            "variables": [
                {
                    "name": v.name,
                    "label": v.label,
                    "kind": v.kind,
                    "required": v.required,
                    "default": v.default,
                    "help": v.help,
                    "options": v.options,
                }
                for v in template.variables
            ],
        }

    async def runs_dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        run = await self.dispatcher.dispatch(
            workspace_id=params.get("workspace_id"),
            card_id=params["card_id"],
            instruction_id=params["instruction_id"],
            rendered_text=params["rendered_text"],
        )
        return {"run_id": run.id, "state": run.state.value}

    async def runs_approve(self, params: dict[str, Any]) -> dict[str, Any]:
        await self.dispatcher.approve(params["run_id"], note=params.get("note"))
        return {"ok": True}

    async def runs_reject(self, params: dict[str, Any]) -> dict[str, Any]:
        await self.dispatcher.reject(params["run_id"], params.get("reason", ""))
        return {"ok": True}

    async def runs_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.dispatcher.cancel(
            params["run_id"],
            params.get("reason", "user requested"),
        )
        return {"ok": ok}

    async def runs_approve_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.dispatcher.approve_plan(params["run_id"])
        return {"ok": ok}

    async def runs_consensus(self, params: dict[str, Any]) -> dict[str, Any]:
        from apps.service.dispatch.consensus import run_consensus

        # Locate the bundled consensus card+template so we have valid FKs.
        cur = await self.store.db.execute(
            "SELECT id, template_id FROM cards WHERE archetype = 'consensus' LIMIT 1",
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError("consensus card not seeded; restart the service to seed it")
        candidates = [(c["provider"], c["model"]) for c in (params.get("candidates") or [])]
        if len(candidates) < 2:
            raise ValueError("need at least two candidates")
        result = await run_consensus(
            self.store,
            self.dispatcher.bus,
            question=params["question"],
            judge_provider=params.get("judge_provider", "anthropic"),
            judge_model=params.get("judge_model", "claude-sonnet-4-5"),
            candidates=candidates,
            judge_instructions=params.get("judge_instructions"),
            consensus_card_id=row["id"],
            consensus_template_id=row["template_id"],
        )
        return {
            "run_id": result.run_id,
            "candidates": [
                {
                    "provider": c.provider,
                    "model": c.model,
                    "tokens_in": c.tokens_in,
                    "tokens_out": c.tokens_out,
                    "error": c.error,
                    "duration_s": c.duration_s,
                }
                for c in result.candidates
            ],
            "cost_usd": result.cost_usd,
        }

    async def runs_replay(self, params: dict[str, Any]) -> dict[str, Any]:
        run = await self.dispatcher.replay(
            params["run_id"],
            provider_override=params.get("provider"),
            model_override=params.get("model"),
            instruction_override=params.get("instruction"),
        )
        return {"run_id": run.id, "state": run.state.value}

    async def runs_artifacts(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        cur = await self.store.db.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at",
            (params["run_id"],),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def providers(self, params: dict[str, Any]) -> list[str]:
        return known_providers()

    async def mcp_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [mcp_registry.to_dict(s) for s in mcp_registry.list_servers()]

    async def mcp_add(self, params: dict[str, Any]) -> dict[str, Any]:
        s = mcp_registry.add_server(
            name=params["name"],
            transport=params["transport"],
            command=params.get("command", ""),
            args=params.get("args") or [],
            url=params.get("url", ""),
            env=params.get("env") or {},
        )
        return mcp_registry.to_dict(s)

    async def mcp_trust(self, params: dict[str, Any]) -> dict[str, Any]:
        s = mcp_registry.trust_server(params["id"])
        if not s:
            raise ValueError(f"unknown mcp server: {params['id']}")
        return mcp_registry.to_dict(s)

    async def mcp_block(self, params: dict[str, Any]) -> dict[str, Any]:
        s = mcp_registry.block_server(params["id"])
        if not s:
            raise ValueError(f"unknown mcp server: {params['id']}")
        return mcp_registry.to_dict(s)

    async def mcp_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = mcp_registry.remove_server(params["id"])
        return {"ok": ok}

    async def dictation_status(self, params: dict[str, Any]) -> dict[str, Any]:
        from apps.service.dictation.whisper import is_available

        return {"available": is_available()}

    async def dictation_transcribe(self, params: dict[str, Any]) -> dict[str, Any]:
        from apps.service.dictation.whisper import (
            TranscriptionOptions,
            transcribe_file,
        )

        # The RPC server is loopback-only, but a malicious local process
        # could still POST to it.  Resolve the path and confirm it points
        # at a regular file with an audio-shaped extension before handing
        # it to the transcriber.
        allowed_exts = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}
        raw = params.get("audio_path")
        if not isinstance(raw, str) or not raw:
            raise ValueError("audio_path required")
        path = Path(raw).resolve(strict=False)
        if not path.is_file():
            raise ValueError(f"audio_path is not a regular file: {path}")
        if path.suffix.lower() not in allowed_exts:
            raise ValueError(
                f"audio_path extension {path.suffix!r} not in allowed set {sorted(allowed_exts)}"
            )
        text = await asyncio.to_thread(
            transcribe_file,
            path,
            TranscriptionOptions(
                model_size=params.get("model_size", "base"),
                language=params.get("language"),
            ),
        )
        return {"text": text}

    # ------------------------------------------------------------------
    # Flow Canvas RPCs
    # ------------------------------------------------------------------

    async def flows_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        flows = await self.store.list_flows()
        return [
            {
                "id": f.id,
                "name": f.name,
                "description": f.description,
                "version": f.version,
                "nodes": f.nodes,
                "edges": f.edges,
                "updated_at": f.updated_at.isoformat(),
            }
            for f in flows
        ]

    async def flows_get(self, params: dict[str, Any]) -> dict[str, Any]:
        flow = await self.store.get_flow(params["id"])
        if not flow:
            raise ValueError(f"unknown flow: {params['id']}")
        return {
            "id": flow.id,
            "name": flow.name,
            "description": flow.description,
            "version": flow.version,
            "nodes": flow.nodes,
            "edges": flow.edges,
        }

    async def flows_create(self, params: dict[str, Any]) -> dict[str, Any]:
        flow = Flow(
            name=params.get("name") or "Untitled flow",
            description=params.get("description", ""),
            nodes=params.get("nodes", []),
            edges=params.get("edges", []),
            is_draft=bool(params.get("is_draft", False)),
        )
        await self.store.insert_flow(flow)
        return {"id": flow.id}

    async def flows_update(self, params: dict[str, Any]) -> dict[str, Any]:
        from apps.service.store.events import FlowVersionConflict

        flow = await self.store.get_flow(params["id"])
        if not flow:
            raise ValueError(f"unknown flow: {params['id']}")
        # Optional optimistic-concurrency token.  GUI passes the version
        # it last fetched; if another writer has bumped the row in the
        # meantime, the update is rejected and the GUI should re-fetch.
        expected_version = params.get("expected_version")
        flow.name = params.get("name", flow.name)
        flow.description = params.get("description", flow.description)
        flow.nodes = params.get("nodes", flow.nodes)
        flow.edges = params.get("edges", flow.edges)
        if "is_draft" in params:
            flow.is_draft = bool(params["is_draft"])
        flow.updated_at = utc_now()
        try:
            await self.store.update_flow(flow, expected_version=expected_version)
        except FlowVersionConflict as exc:
            raise ValueError(
                f"flow {flow.id} has been modified by another writer; reload before saving"
            ) from exc
        return {"id": flow.id, "version": flow.version + 1}

    async def flows_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        # Cancel any in-flight runs of this flow before deleting the
        # row, otherwise the supervisor task would carry on writing
        # update_flow_run against zero rows and silently mask its own
        # cancellation path.
        flow_id = params["id"]
        cancelled = 0
        for run_id, task in list(self.flow_executor._active.items()):
            run = await self.store.get_flow_run(run_id)
            if run and run.flow_id == flow_id and not task.done():
                task.cancel()
                cancelled += 1
        ok = await self.store.delete_flow(flow_id)
        return {"deleted": bool(ok), "cancelled_runs": cancelled}

    async def flows_dispatch(self, params: dict[str, Any]) -> dict[str, Any]:
        flow = await self.store.get_flow(params["flow_id"])
        if not flow:
            raise ValueError(f"unknown flow: {params['flow_id']}")
        if flow.is_draft:
            raise ValueError(
                "flow is in draft mode — flip the Draft toggle off "
                "in the Canvas toolbar to promote it to Live first."
            )
        run = await self.flow_executor.dispatch(flow)
        return {"run_id": run.id}

    async def flows_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.flow_executor.cancel(params["run_id"])
        return {"cancelled": bool(ok)}

    async def flows_approve_human(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.flow_executor.approve_human(
            params["run_id"], params["node_id"], bool(params.get("approved", True))
        )
        return {"ok": bool(ok)}

    # ------------------------------------------------------------------
    # Named agents — persistent conversations with follow-up linkage.
    # ------------------------------------------------------------------

    # Project-convention files we look for at the repo root, in order
    # of preference.  The first match wins; runner-up files are noted.
    _CONVENTION_FILES = (
        "CLAUDE.md",
        "AGENTS.md",
        "GEMINI.md",
        ".cursorrules",
        ".cursor/rules.md",
    )

    # Cap inlined convention text so a 500 KB CLAUDE.md doesn't dominate
    # the prompt.  Honest truncation marker so the model knows.
    _CONVENTION_INLINE_CAP = 8000

    async def _build_repo_system_prompt(self, ws: Any, *, base_system: str | None) -> str:
        """Compose the system prompt for a repo-bound coding session.

        Folds together (in order):
          - the operator's own ``base_system`` (if any),
          - a header naming the workspace,
          - the current branch,
          - the contents of the first project-convention file we find
            at the repo root (CLAUDE.md / AGENTS.md / GEMINI.md /
            .cursorrules), capped to 8 KB.

        Best-effort: any failure (missing git, IO error, etc.) is
        swallowed and we fall back to the basic header.
        """
        repo = Path(ws.repo_path)
        # Branch lookup — graceful when not a git repo.
        branch = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _err = await asyncio.wait_for(proc.communicate(), timeout=4.0)
            if proc.returncode == 0:
                branch = out.decode("utf-8", errors="replace").strip()
        except Exception:
            log.debug("branch lookup failed for %s", repo, exc_info=True)

        # Find the first convention file that exists.  Read up to the
        # cap; refuse symlinks pointing outside the repo as a
        # cheap defence.
        convention_text = ""
        convention_name = ""
        for rel in self._CONVENTION_FILES:
            candidate = (repo / rel).resolve()
            try:
                if not candidate.is_relative_to(repo.resolve()):
                    continue
            except (OSError, ValueError):
                continue
            if candidate.is_file():
                try:
                    convention_text = await asyncio.to_thread(
                        candidate.read_text, encoding="utf-8", errors="replace"
                    )
                except OSError:
                    continue
                convention_name = rel
                break

        truncated = False
        if len(convention_text) > self._CONVENTION_INLINE_CAP:
            convention_text = convention_text[: self._CONVENTION_INLINE_CAP]
            truncated = True

        parts: list[str] = []
        header = (
            f"You are operating inside the project at {ws.repo_path} (workspace name: {ws.name})"
        )
        if branch:
            header += f", currently on branch '{branch}'"
        header += "."
        parts.append(header)

        parts.append(
            "This is a real source-controlled repository. Use your "
            "built-in file tools (Read / Bash / Edit / Grep) to browse "
            "and modify it. Before making non-trivial changes:\n"
            "  - Run `git status` and `git diff` to understand current state.\n"
            "  - Look for tests near the file you are editing.\n"
            "  - Do NOT run `git push`, force operations, or destructive "
            "commands (`rm -rf`, `git reset --hard`) without an explicit "
            "go-ahead from the user.\n"
            "  - Prefer small, reviewable diffs."
        )

        if convention_name and convention_text:
            parts.append(
                f"=== Project convention file: {convention_name} "
                f"(repo-root) ===\n{convention_text}\n"
                + (
                    f"\n_(truncated; first {self._CONVENTION_INLINE_CAP} chars only)_"
                    if truncated
                    else ""
                )
                + "\n=== End project convention ==="
            )

        if base_system:
            parts.append(base_system)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Drones — see docs/DRONE_MODEL.md.
    #
    # Blueprint = operator-set frozen template (only the operator
    # creates / edits — there is no auth gate on blueprints.* because
    # the GUI is the only client and the operator IS the GUI user).
    #
    # Action    = deployed instance.  Cross-action mutations
    # (append_reference / append_skill) are gated by the actor's
    # snapshotted role via ``_check_drone_authority``.  drones.send
    # lives in PR #24 alongside the chat dialog.
    # ------------------------------------------------------------------

    async def blueprints_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = await self.store.list_drone_blueprints()
        return [r.model_dump(mode="json") for r in rows]

    async def blueprints_get(self, params: dict[str, Any]) -> dict[str, Any]:
        bp = await self.store.get_drone_blueprint(params["id"])
        if not bp:
            raise ValueError(f"unknown blueprint: {params['id']}")
        return bp.model_dump(mode="json")

    async def blueprints_create(self, params: dict[str, Any]) -> dict[str, Any]:
        role = params.get("role") or DroneRole.WORKER.value
        try:
            role_enum = DroneRole(role)
        except ValueError as e:
            raise ValueError(f"unknown role: {role}") from e
        bp = DroneBlueprint(
            name=(params.get("name") or "Untitled blueprint").strip(),
            description=params.get("description") or "",
            role=role_enum,
            provider=params["provider"],
            model=params["model"],
            system_persona=params.get("system_persona") or "",
            skills=[str(s) for s in (params.get("skills") or []) if s],
            reference_blueprint_ids=[
                str(r) for r in (params.get("reference_blueprint_ids") or []) if r
            ],
        )
        await self.store.insert_drone_blueprint(bp)
        return bp.model_dump(mode="json")

    async def blueprints_update(self, params: dict[str, Any]) -> dict[str, Any]:
        """Edit a blueprint.  Pass ``expected_version`` to detect
        racing edits (mirrors ``flows.update``).  Returns the updated
        blueprint or raises ``BlueprintVersionConflict`` re-formatted
        as a ValueError so the JSON-RPC layer surfaces it cleanly.
        """
        bp = await self.store.get_drone_blueprint(params["id"])
        if not bp:
            raise ValueError(f"unknown blueprint: {params['id']}")
        if "name" in params:
            bp.name = (params["name"] or "Untitled blueprint").strip()
        if "description" in params:
            bp.description = params["description"] or ""
        if "role" in params:
            try:
                bp.role = DroneRole(params["role"])
            except ValueError as e:
                raise ValueError(f"unknown role: {params['role']}") from e
        if "provider" in params:
            bp.provider = params["provider"]
        if "model" in params:
            bp.model = params["model"]
        if "system_persona" in params:
            bp.system_persona = params["system_persona"] or ""
        if "skills" in params:
            bp.skills = [str(s) for s in (params["skills"] or []) if s]
        if "reference_blueprint_ids" in params:
            bp.reference_blueprint_ids = [
                str(r) for r in (params["reference_blueprint_ids"] or []) if r
            ]
        expected_version = params.get("expected_version")
        try:
            await self.store.update_drone_blueprint(
                bp,
                expected_version=int(expected_version) if expected_version is not None else None,
            )
        except BlueprintVersionConflict as e:
            # Surface as a generic error so the GUI can refetch +
            # re-prompt without needing to import the exception type.
            raise ValueError(str(e)) from e
        return bp.model_dump(mode="json")

    async def blueprints_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        """Refuses if any actions still link to this blueprint.  Returns
        ``{deleted: bool, linked_actions: int}`` so the GUI can show a
        precise "N drones deployed from this blueprint, delete those
        first" message.
        """
        blueprint_id = params["id"]
        linked = await self.store.count_actions_for_blueprint(blueprint_id)
        if linked > 0:
            return {"deleted": False, "linked_actions": linked}
        deleted = await self.store.delete_drone_blueprint(blueprint_id)
        return {"deleted": bool(deleted), "linked_actions": 0}

    async def drones_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        blueprint_id = params.get("blueprint_id")
        rows = await self.store.list_drone_actions(blueprint_id=blueprint_id)
        return [r.model_dump(mode="json") for r in rows]

    async def drones_get(self, params: dict[str, Any]) -> dict[str, Any]:
        a = await self.store.get_drone_action(params["id"])
        if not a:
            raise ValueError(f"unknown action: {params['id']}")
        return a.model_dump(mode="json")

    async def drones_deploy(self, params: dict[str, Any]) -> dict[str, Any]:
        """Snapshot a blueprint + spawn a fresh action.

        ``params``:
          blueprint_id:                  required.
          workspace_id:                  optional repo binding.
          additional_skills:             optional one-off /tokens.
          additional_reference_action_ids: optional cross-action refs.
        """
        bp = await self.store.get_drone_blueprint(params["blueprint_id"])
        if not bp:
            raise ValueError(f"unknown blueprint: {params['blueprint_id']}")
        workspace_id = params.get("workspace_id")
        if workspace_id:
            ws = await self.store.get_workspace(workspace_id)
            if not ws:
                raise ValueError(f"unknown workspace: {workspace_id}")
        action = DroneAction(
            blueprint_id=bp.id,
            blueprint_snapshot=bp.model_dump(mode="json"),
            workspace_id=workspace_id or None,
            additional_skills=[str(s) for s in (params.get("additional_skills") or []) if s],
            additional_reference_action_ids=[
                str(r) for r in (params.get("additional_reference_action_ids") or []) if r
            ],
        )
        await self.store.insert_drone_action(action)
        return action.model_dump(mode="json")

    async def drones_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        ok = await self.store.delete_drone_action(params["id"])
        async with self._action_send_locks_guard:
            self._action_send_locks.pop(params["id"], None)
        return {"deleted": bool(ok)}

    async def drones_send(self, params: dict[str, Any]) -> dict[str, Any]:
        """Append the operator's message to a drone action's transcript
        and get a reply.  Multi-turn — every turn after the first builds
        on the prior transcript.

        Reads from the action's frozen ``blueprint_snapshot`` for
        provider / model / system_persona, layering in the action's
        ``additional_skills`` on top of the snapshot's defaults so the
        LLM sees ``effective_skills``.

        First-version scope: chat-only or workspace-bound, no
        attachments, no cross-action references.  Both deferred to a
        follow-up PR.
        """
        from apps.service.providers.registry import get_provider

        action_id = params["action_id"]
        message = params["message"]
        async with await self._lock_for_action(action_id):
            action = await self.store.get_drone_action(action_id)
            if not action:
                raise ValueError(f"unknown action: {action_id}")
            snapshot = action.blueprint_snapshot or {}
            provider_name = snapshot.get("provider")
            model = snapshot.get("model")
            if not provider_name or not model:
                # An action without a provider/model in its snapshot is
                # malformed — refuse rather than guess.  The Blueprints
                # tab requires both, so this only fires on hand-crafted
                # rows or a future-format snapshot we don't fully
                # understand.
                raise ValueError(
                    f"action {action_id} has no provider/model in snapshot — "
                    "redeploy from a complete blueprint"
                )
            action.transcript.append({"role": "user", "content": message})

            # Build a CLEAN system prompt and a CLEAN message body.
            #
            # History of this prompt-assembly logic:
            #   v1 (pre-PR-37) inlined a fake "System: ... User: ...
            #       Assistant:" transcript as a single user message.
            #       Claude-sonnet read it as a metadata dump and replied
            #       "I don't see a question, just system reminders."
            #   v2 (PR #37) moved persona/skills to system_prompt and
            #       framed multi-turn history in natural language inside
            #       the user message ("Prior conversation in this thread
            #       ... New message from the user ...").  Single-turn
            #       worked.  Multi-turn FAILED — claude saw role-labeled
            #       text in the user-turn and hallucinated fake "User:"
            #       / "Assistant:" lines in its reply, continuing the
            #       pattern.  Operator screenshot 2026-05-11 showed a
            #       drone "responding" with three fake assistant lines
            #       and a fake user line.
            #   v3 (this rewrite) moves the history INTO the system
            #       prompt (clearly framed as "for context only, do NOT
            #       echo or paraphrase these turns") and sends ONLY the
            #       new user message as the user-turn.  No role labels
            #       in the body for the model to mimic.
            #
            # persona + skills + history all go via the provider's
            # proper system-prompt mechanism (claude-cli: --append-
            # system-prompt; gemini-cli: its _render_prompt inlines).
            persona = snapshot.get("system_persona") or ""
            effective_skills = list(snapshot.get("skills") or []) + list(
                action.additional_skills or []
            )
            system_lines: list[str] = []
            if persona:
                system_lines.append(persona)
            if effective_skills:
                system_lines.append(
                    "Operator-supplied skills you can invoke: " + " ".join(effective_skills)
                )

            prior_turns = action.transcript[:-1]
            if prior_turns:
                history_lines = []
                for m in prior_turns:
                    speaker = "User" if m.get("role") == "user" else "You (the assistant)"
                    history_lines.append(f"{speaker}: {m.get('content', '')}")
                history_block = "\n\n".join(history_lines)
                system_lines.append(
                    "Prior conversation in this thread, provided here as "
                    "context only.  Do NOT echo, paraphrase, or continue "
                    "these turns in your reply — the new user message "
                    "follows separately and is the one to respond to.\n\n" + history_block
                )

            system_prompt: str | None = "\n\n".join(system_lines).strip() or None
            message_body = message

            provider = get_provider(provider_name)
            from apps.service.types import (
                BlastRadiusPolicy,
                CardMode,
                CostPolicy,
                PersonalityCard,
                SandboxTier,
            )

            card = PersonalityCard(
                name=f"(drone {snapshot.get('name', action_id)})",
                archetype="drone",
                description="ephemeral card for a drone action",
                template_id="drone",
                provider=provider_name,
                model=model,
                mode=CardMode.CHAT,
                cost=CostPolicy(),
                blast_radius=BlastRadiusPolicy(),
                sandbox_tier=SandboxTier.DEVCONTAINER,
            )

            cwd: str | None = None
            if action.workspace_id:
                ws = await self.store.get_workspace(action.workspace_id)
                if ws is not None:
                    cwd = ws.repo_path
                    system_prompt = await self._build_repo_system_prompt(
                        ws, base_system=system_prompt
                    )

            session = await provider.open_chat(card, system=system_prompt, cwd=cwd)
            chunks: list[str] = []
            try:
                async for ev in session.send(message_body, attachments=[]):
                    if ev.kind == "text_delta":
                        chunks.append(ev.text)
                    elif ev.kind == "error":
                        raise RuntimeError(ev.text or "provider error")
                    elif ev.kind == "finish":
                        break
            finally:
                await session.close()
            reply = "".join(chunks)
            action.transcript.append({"role": "assistant", "content": reply})
            await self.store.update_drone_action(action)
            await self.store.record_provider_message(provider_name, model)
            return {"reply": reply, "action": action.model_dump(mode="json")}

    async def _load_actor_for_authority(self, actor_id: str) -> DroneAction:
        actor = await self.store.get_drone_action(actor_id)
        if not actor:
            raise ValueError(f"unknown actor action: {actor_id}")
        return actor

    async def drones_append_reference(self, params: dict[str, Any]) -> dict[str, Any]:
        """Append a cross-action reference onto a target action.

        Gated by the actor's snapshotted role:
        - WORKER:     self only.
        - SUPERVISOR: any peer.
        - COURIER:    any peer (this is the courier's main job).
        - AUDITOR:    denied.
        """
        actor_id = params["actor_id"]
        target_id = params["target_id"]
        ref_id = params["reference_action_id"]
        actor = await self._load_actor_for_authority(actor_id)
        target = await self.store.get_drone_action(target_id)
        if not target:
            raise ValueError(f"unknown target action: {target_id}")
        try:
            _check_drone_authority(
                actor.effective_role,
                "append_reference",
                is_self=(actor_id == target_id),
            )
        except PermissionError as e:
            raise ValueError(str(e)) from e
        if ref_id and ref_id not in target.additional_reference_action_ids:
            target.additional_reference_action_ids.append(ref_id)
            await self.store.update_drone_action(target)
        return target.model_dump(mode="json")

    async def drones_append_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        """Append a one-off /skill onto a target action.

        Authority: SUPERVISOR (any peer) or self (any non-AUDITOR).
        """
        actor_id = params["actor_id"]
        target_id = params["target_id"]
        skill = (params.get("skill") or "").strip()
        if not skill:
            raise ValueError("skill required")
        actor = await self._load_actor_for_authority(actor_id)
        target = await self.store.get_drone_action(target_id)
        if not target:
            raise ValueError(f"unknown target action: {target_id}")
        try:
            _check_drone_authority(
                actor.effective_role,
                "append_skill",
                is_self=(actor_id == target_id),
            )
        except PermissionError as e:
            raise ValueError(str(e)) from e
        if skill not in target.additional_skills:
            target.additional_skills.append(skill)
            await self.store.update_drone_action(target)
        return target.model_dump(mode="json")

    async def skills_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Discover skills available to a given provider.

        Returns ``{provider, skills, source}`` where:
          * ``provider`` is the requested provider name.
          * ``skills`` is a list of ``{name, description, path}`` dicts.
          * ``source`` is either ``"~/.claude/skills"`` (Claude) or
            ``"none"`` (Gemini today — no first-class skills mechanism
            in the Gemini CLI; the operator can still type free-form
            ``/foo /bar`` directives that we inline as system text).

        For Claude (``claude-cli`` or ``anthropic``) we scan
        ``~/.claude/skills/*.md`` for the operator's installed skills.
        Each file's stem becomes the skill name.  The first non-empty
        non-front-matter line of the body becomes the description so
        the picker dialog has something to render under each name.
        Skills directory missing → empty list (not an error).
        """
        provider = (params.get("provider") or "").strip()
        if provider in ("claude-cli", "anthropic"):
            skills_dir = Path.home() / ".claude" / "skills"
            entries: list[dict[str, str]] = []
            if skills_dir.is_dir():
                for path in sorted(skills_dir.glob("*.md")):
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    description = _first_descriptive_line(text)
                    entries.append(
                        {
                            "name": path.stem,
                            "description": description,
                            "path": str(path),
                        }
                    )
            return {
                "provider": provider,
                "skills": entries,
                "source": str(skills_dir) if skills_dir.is_dir() else "none",
            }
        # Gemini / Ollama / API providers don't have a first-class
        # skills mechanism the way Claude Code does.  Return empty
        # so the GUI can render a helpful "no skills detected — type
        # free-form" message.
        return {"provider": provider, "skills": [], "source": "none"}

    async def limits_check(self, params: dict[str, Any]) -> dict[str, Any]:
        """Probe every locally-installed CLI for whatever subscription
        / usage info it exposes.  Returns a structured dict the GUI's
        Limits tab renders as one card per provider.

        Per-message remaining-quota numbers are not reliably available
        headlessly for either Claude Code or Gemini CLI — both gate
        that behind their interactive `/status` flow.  We surface
        whatever each binary returns from its public status commands
        plus links to the official dashboards so the operator can
        always drill in.
        """
        import shutil

        async def _run(args: list[str], timeout: float = 15.0) -> dict[str, Any]:
            binary = shutil.which(args[0])
            if not binary:
                return {"ok": False, "stdout": "", "stderr": "not found on PATH", "exit": -1}
            try:
                proc = await asyncio.create_subprocess_exec(
                    binary,
                    *args[1:],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                except TimeoutError:
                    proc.kill()
                    # Reap the child so we don't leave a zombie behind.
                    # communicate() also closes the pipes for us.
                    try:
                        await asyncio.wait_for(proc.communicate(), timeout=2.0)
                    except (TimeoutError, ProcessLookupError):
                        pass
                    return {
                        "ok": False,
                        "stdout": "",
                        "stderr": f"timed out after {timeout}s",
                        "exit": -2,
                    }
            except Exception as exc:
                return {"ok": False, "stdout": "", "stderr": str(exc), "exit": -3}
            return {
                "ok": (proc.returncode or 0) == 0,
                "stdout": stdout_b.decode("utf-8", errors="replace").strip(),
                "stderr": stderr_b.decode("utf-8", errors="replace").strip(),
                "exit": proc.returncode or 0,
            }

        # Claude Code: try `--version` (always works) then optionally
        # `status` (newer versions).  We don't try the interactive
        # `/status` slash command headlessly because it would never
        # return.
        claude_version = await _run(["claude", "--version"])
        claude_status = (
            await _run(["claude", "status"], timeout=8.0)
            if claude_version["ok"]
            else {
                "ok": False,
                "stdout": "",
                "stderr": "claude not on PATH",
                "exit": -1,
            }
        )
        # Gemini CLI: same pattern.
        gemini_version = await _run(["gemini", "--version"])
        gemini_status = (
            await _run(["gemini", "status"], timeout=8.0)
            if gemini_version["ok"]
            else {
                "ok": False,
                "stdout": "",
                "stderr": "gemini not on PATH",
                "exit": -1,
            }
        )

        from apps.service.limits import (
            DATA_AS_OF,
            claude_plans,
            context_windows,
            gemini_plans,
        )

        return {
            "data_as_of": DATA_AS_OF,
            "context_windows": context_windows(),
            "providers": [
                {
                    "id": "claude-cli",
                    "label": "Claude Code (Pro / Max plan)",
                    "version": claude_version,
                    "status": claude_status,
                    "plans": claude_plans(),
                    "dashboards": [
                        {
                            "label": "Pro / Max usage dashboard",
                            "url": "https://claude.ai/settings/usage",
                        },
                        {"label": "Subscription page", "url": "https://claude.ai/settings"},
                    ],
                    "note": (
                        "Per-message remaining-count isn't returned "
                        "headlessly.  Caps below are the published plan "
                        "limits; the dashboards above show live usage."
                    ),
                },
                {
                    "id": "gemini-cli",
                    "label": "Gemini CLI",
                    "version": gemini_version,
                    "status": gemini_status,
                    "plans": gemini_plans(),
                    "dashboards": [
                        {"label": "Gemini app + plan", "url": "https://gemini.google.com/"},
                        {
                            "label": "AI Studio (API keys)",
                            "url": "https://aistudio.google.com/app/apikey",
                        },
                    ],
                    "note": (
                        "Headless quota readout isn't documented.  Caps "
                        "below are the published plan limits; the "
                        "dashboards above show live usage."
                    ),
                },
            ],
        }

    async def limits_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        """Local message-send tally per provider.

        Returns rolling counts for the three windows operators care
        about: 5 hours (Claude Pro/Max session window), 24 hours
        (Gemini daily) and 7 days (Claude weekly).  Counts come from
        the ``provider_messages`` table populated by every successful
        agents.send.  Independent of any CLI status command — this
        is what *we* observed.
        """
        from datetime import timedelta

        from apps.service.types import utc_now

        now = utc_now()
        windows = {
            "5h": (now - timedelta(hours=5)).isoformat(),
            "24h": (now - timedelta(hours=24)).isoformat(),
            "7d": (now - timedelta(days=7)).isoformat(),
        }
        out: dict[str, dict[str, int]] = {}
        for provider in ("claude-cli", "gemini-cli"):
            counts: dict[str, int] = {}
            for label, since_iso in windows.items():
                counts[label] = await self.store.count_provider_messages(provider, since_iso)
            out[provider] = counts
        return {"providers": out, "checked_at": now.isoformat()}

    async def hooks_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return hook_status()

    async def hooks_install(self, params: dict[str, Any]) -> dict[str, Any]:
        plan = install_hook(service_url=params["service_url"])
        return {
            "settings_path": str(plan.settings_path),
            "script_path": str(plan.script_path),
        }

    async def hooks_uninstall(self, params: dict[str, Any]) -> dict[str, Any]:
        removed = uninstall_hook()
        return {"removed": removed}

    async def hook_received(self, params: dict[str, Any]) -> dict[str, Any]:
        await self.store.append_event(
            Event(
                source=EventSource.INGEST_CLAUDE_HOOK,
                kind=EventKind.INGEST_RECEIVED,
                payload=params.get("payload") or {},
                text=str(params.get("payload", {}))[:4000],
            )
        )
        return {"ok": True}


# ---------------------------------------------------------------------------
# Wire-up
# ---------------------------------------------------------------------------


def _install_handlers(server: JsonRpcServer, h: Handlers) -> None:
    server.register("workspaces.list", h.workspaces_list)
    server.register("workspaces.register", h.workspaces_register)
    server.register("workspaces.remove", h.workspaces_remove)
    server.register("workspaces.tree", h.workspaces_tree)
    server.register("workspaces.clone", h.workspaces_clone)
    server.register("workspaces.git_status", h.workspaces_git_status)
    server.register("workspaces.switch_branch", h.workspaces_switch_branch)
    server.register("cards.list", h.cards_list)
    server.register("runs.list", h.runs_list)
    server.register("runs.dispatch", h.runs_dispatch)
    server.register("runs.approve", h.runs_approve)
    server.register("runs.reject", h.runs_reject)
    server.register("runs.cancel", h.runs_cancel)
    server.register("runs.replay", h.runs_replay)
    server.register("runs.consensus", h.runs_consensus)
    server.register("runs.approve_plan", h.runs_approve_plan)
    server.register("runs.artifacts", h.runs_artifacts)
    server.register("search", h.search)
    server.register("lint.instruction", h.lint_instruction)
    server.register("cost.forecast", h.cost_forecast)
    server.register("templates.render", h.render_template)
    server.register("templates.get", h.templates_get)
    server.register("flows.list", h.flows_list)
    server.register("flows.get", h.flows_get)
    server.register("flows.create", h.flows_create)
    server.register("flows.update", h.flows_update)
    server.register("flows.delete", h.flows_delete)
    server.register("flows.dispatch", h.flows_dispatch)
    server.register("flows.cancel", h.flows_cancel)
    server.register("flows.approve_human", h.flows_approve_human)
    server.register("blueprints.list", h.blueprints_list)
    server.register("blueprints.get", h.blueprints_get)
    server.register("blueprints.create", h.blueprints_create)
    server.register("blueprints.update", h.blueprints_update)
    server.register("blueprints.delete", h.blueprints_delete)
    server.register("drones.list", h.drones_list)
    server.register("drones.get", h.drones_get)
    server.register("drones.deploy", h.drones_deploy)
    server.register("drones.delete", h.drones_delete)
    server.register("drones.send", h.drones_send)
    server.register("drones.append_reference", h.drones_append_reference)
    server.register("drones.append_skill", h.drones_append_skill)
    server.register("skills.list", h.skills_list)
    server.register("providers", h.providers)
    server.register("hook.received", h.hook_received)
    server.register("limits.check", h.limits_check)
    server.register("limits.usage", h.limits_usage)
    server.register("hooks.status", h.hooks_status)
    server.register("hooks.install", h.hooks_install)
    server.register("hooks.uninstall", h.hooks_uninstall)
    server.register("mcp.list", h.mcp_list)
    server.register("mcp.add", h.mcp_add)
    server.register("mcp.trust", h.mcp_trust)
    server.register("mcp.block", h.mcp_block)
    server.register("mcp.remove", h.mcp_remove)
    server.register("dictation.status", h.dictation_status)
    server.register("dictation.transcribe", h.dictation_transcribe)


async def serve(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    data_dir = Path(args.data_dir) if args.data_dir else _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "agentorchestra.sqlite"

    store = EventStore(db_path)
    await store.open()
    log.info("store open at %s", db_path)

    seeded = await seed_default_cards(store)
    if seeded:
        log.info("seeded %d cards", len(seeded))

    bus = EventBus()
    store.on_append = bus.publish

    manager = WorktreeManager(store)
    dispatcher = RunDispatcher(store, manager, bus)
    handlers = Handlers(store, manager, dispatcher, data_dir=data_dir)

    watcher = JSONLWatcher(store)
    await watcher.start()

    sentinel = DriftSentinel(store=store, bus=bus)
    await sentinel.start()

    token = hook_token()
    rpc = JsonRpcServer(token=token, bus=bus)
    _install_handlers(rpc, handlers)
    log.info("rpc token: %s", token[:8] + "…")

    config = uvicorn.Config(
        rpc.app(),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    await store.append_event(
        Event(
            source=EventSource.SYSTEM,
            kind=EventKind.SERVICE_STARTED,
            text=f"service started on 127.0.0.1:{args.port}",
        )
    )

    stop = asyncio.Event()

    def _signal(_sig: int, _frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    stop_task = asyncio.create_task(stop.wait(), name="stop-signal")

    await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError):
        await server_task
    await sentinel.stop()
    await watcher.stop()
    await store.append_event(
        Event(source=EventSource.SYSTEM, kind=EventKind.SERVICE_STOPPED, text="service stopped")
    )
    await store.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="agentorchestra-service")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()
    return asyncio.run(serve(args))


if __name__ == "__main__":
    sys.exit(main())
