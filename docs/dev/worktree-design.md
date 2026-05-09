# WorktreeManager — Design

(Canonical reference for the implementation in
`apps/service/worktrees/manager.py`.)

## Goals
- Give every Run an isolated, throwaway working directory on its own
  branch with zero impact on the user's main branch or working tree.
- Atomic, crash-safe creation, lifecycle, and cleanup.
- Plain-English UX surface — no git terminology in default flows.
- Auto-detect stale, dead, or hung worktrees.
- Clean integration with the merger, sandbox tiers, cost meter, and
  event store.

## Glossary
- **Workspace** — one git repo registered with the app.
- **Worktree** — a `git worktree` instance under `.agent-worktrees/`.
- **Run** — a dispatched agent execution; 1 Run ↔ 1 worktree ↔ 1 branch.
- **Base ref** — the immutable commit a Run forks from.
- **Save point** — user-facing name for a commit on the agent branch.
- **Combine** — user-facing name for a merge from agent branch into base.

## State machine

```
(none) -> CREATED -> ACTIVE <-> PAUSED
                       │
                       └─> AWAITING_REVIEW
                              │
                              ├─> MERGING -> MERGED -> CLEANED
                              │      │
                              │      └─> CONFLICTED -> MERGED|ABANDONED
                              ├─> REJECTED -> CLEANED
                              └─> ACTIVE
        any non-terminal -> STALE -> ABANDONED -> CLEANED
```

`MERGED`, `REJECTED`, `ABANDONED` and `CLEANED` are terminal.  Cleaned
worktrees are removed from disk; the row stays in SQLite forever for
provenance.

## Naming conventions

- **Branch:** `agent/<archetype-slug>/<short-run-id>` (8-char ULID).
- **Worktree path:** `<workspace>/.agent-worktrees/<archetype-slug>-<short-run-id>/`.
- **Tracking ref:** `refs/agentorchestra/runs/<run-id>` updated after every commit.
- **`.git/info/exclude`** appended (idempotently) with `.agent-worktrees/`.

## Concurrency

- Per-workspace advisory lock (file lock at `.agent-worktrees/.lock`)
  serializes only worktree-creation, removal, and merging.  Reads and
  per-worktree commits are not serialized.
- Per-Branch row lock prevents concurrent state mutations.

## Sandbox tiers

- **Tier 1 — devcontainer-style** (V1 default): worktree is the
  filesystem write boundary; tool allowlist enforced; symlinks not
  followed across the boundary.
- **Tier 2 — Docker** (V2): cap-drop ALL, no network, mount worktree
  read-write, repo `.git` read-only.
- **Tier 3 — E2B / Firecracker / Daytona** (V3): microVM isolation.

## Three merge modes

- **Combine cleanly** (default when no conflict predicted)
- **Combine with help** — Mergiraf as the merge driver (V2; CLI shim
  in V1)
- **Let me decide** — surfaces conflict markers in the diff viewer

## Failure modes — guarantees

- Every failure leaves git history intact and the user in a
  recoverable place.
- Worktrees are disposable; commits and refs are sacred.
- The `Reset workspaces` panic action removes everything in
  `.agent-worktrees/` and every `refs/agentorchestra/*` ref, but
  never touches the user's branches.

For the full design rationale (edge cases, library choices, security,
test strategy) see `worktree-design-full.md` (TBD).
