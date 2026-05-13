# ShadowLoop Alignment Matrix

Current terminology alignment:

- Legacy "Drone" references map to FPV Drone.
- Legacy "Agent" references map to Reaper Drone.
- Staging Area is a separate flow node, not a Reaper Drone mode.

This file tracks implementation parity with `ShadowLoop Plan.txt` without modifying that source plan.

## Phase 1 - Shadow-Plan Guard

- `apps/service/dispatch/dispatcher.py`
- Autonomous modifying tools are gated by recent `PLAN.md` intent.
- Violations return `403 Shadow-Plan Violation`.
- Autonomous tool loops are capped at 15 turns.

## Phase 2 - Mapper Archetypes

- `packs/archetypes/ui_architect.md`
- `packs/archetypes/logic_liaison.md`
- `apps/service/cards/seed.py`
- `apps/service/linter/preflight.py`

## Phase 3 - Agentic Evaluation Dashboard

- `apps/gui/windows/analytics.py`
- `apps/service/main.py` RPCs: `analytics.summary`, `analytics.leaderboard`
- `apps/service/store/events.py` aggregation logic
- `apps/service/store/schema.sql` + migrations include analytics metadata fields (`is_hallucination`, `plan_latency`).

## Phase 4 - Parallel Consensus Orchestration

- `apps/service/flows/executor.py` (`consensus` node)
- `apps/service/dispatch/consensus.py` (`runs.consensus` fan-out/fan-in)
- `apps/gui/windows/review.py` candidate split view + winner selection
- `apps/service/main.py` RPC: `runs.select_consensus_winner`