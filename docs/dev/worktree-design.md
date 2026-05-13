# WorktreeManager â€” Design

(Canonical reference for the implementation in
`apps/service/worktrees/manager.py`.)

## Goals
- Give every Run an isolated, throwaway working directory on its own
  branch with zero impact on the user's main branch or working tree.
- Atomic, crash-safe creation, lifecycle, and cleanup.
- Plain-English UX surface â€” no git terminology in default flows.
- Auto-detect stale, dead, or hung worktrees.
- Clean integration with the merger, sandbox tiers, cost meter, and
  event store.

## Glossary
- **Workspace** â€” one git repo registered with the app.
- **Worktree** â€” a `git worktree` instance under `.agent-worktrees/`.
- **Run** â€” a dispatched agent execution; 1 Run â†” 1 worktree â†” 1 branch.
- **Base ref** â€” the immutable commit a Run forks from.
- **Save point** â€” user-facing name for a commit on the agent branch.
- **Combine** â€” user-facing name for a merge from agent branch into base.

## State machine

```
(none) -> CREATED -> ACTIVE <-> PAUSED
                       â”‚
                       â””â”€> AWAITING_REVIEW
                              â”‚
                              â”œâ”€> MERGING -> MERGED -> CLEANED
                              â”‚      â”‚
                              â”‚      â””â”€> CONFLICTED -> MERGED|ABANDONED
                              â”œâ”€> REJECTED -> CLEANED
                              â””â”€> ACTIVE
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

- **Tier 1 â€” devcontainer-style** (V1 default): worktree is the
  filesystem write boundary; tool allowlist enforced; symlinks not
  followed across the boundary.
- **Tier 2 â€” Docker** (V2): cap-drop ALL, no network, mount worktree
  read-write, repo `.git` read-only.
- **Tier 3 â€” E2B / Firecracker / Daytona** (V3): microVM isolation.

## Three merge modes

- **Combine cleanly** (default when no conflict predicted)
- **Combine with help** â€” Mergiraf as the merge driver (V2; CLI shim
  in V1)
- **Let me decide** â€” surfaces conflict markers in the diff viewer

## Failure modes â€” guarantees

- Every failure leaves git history intact and the user in a
  recoverable place.
- Worktrees are disposable; commits and refs are sacred.
- The `Reset workspaces` panic action removes everything in
  `.agent-worktrees/` and every `refs/agentorchestra/*` ref, but
  never touches the user's branches.

For the full design rationale (edge cases, library choices, security,
test strategy) see `worktree-design-full.md` (TBD).
