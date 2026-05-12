# Flow Canvas — Project & Implementation Plan

> **Status (Phase 5):** core canvas (Phases 1-4 of this plan) is **shipped**
> and lives at `apps/gui/canvas/` + `apps/service/flows/`.  The plan's
> proposed `flows/types.py` / `flows/nodes.py` typed-node split was
> intentionally not pursued — nodes/edges stayed as
> `list[dict[str, Any]]` so the canvas can round-trip arbitrary GUI
> metadata the executor doesn't need to read.  Treat this document as
> the design rationale; the README's Canvas section is the current
> operator-facing description.

A visual, zoomable, drag-and-drop orchestration canvas for AgentOrchestra.

## 1. Goals

The current Compose tab is a single-shot dispatch form. The Flow Canvas
turns AgentOrchestra into a visual workflow tool where you can:

1. **Drag agents onto an infinite 2D plane.**
2. **Link them together** to form sequential, parallel, fan-out, and
   merge patterns.
3. **Edit and run** the whole flow with one click.
4. **Watch it execute live** — nodes light up as they run, edges show
   data flowing along them, results stream into each node.
5. **Zoom from "the whole flow at a glance" down to "this one node's
   inspector"** without losing visual hierarchy at any level.
6. **Save and re-use flows** — a flow is a first-class object, like a
   card.

Non-goals for V1:
- Code editor inside nodes (link out to existing GUI for that).
- Cloud-collaborative editing.
- Versioning beyond "save as new flow".

## 2. Why a node graph fits AgentOrchestra

Multi-agent work is naturally graph-shaped:

- **Sequential** — Broad Research → Narrow Research per finding.
- **Parallel fan-out** — same prompt to Claude-CLI, Gemini-CLI, then
  judge.
- **Conditional branching** — if findings > 5 then deep-dive, else
  publish.
- **Human-in-the-loop** — pause before merge, wait for approval.
- **Loops** — research → critique → revise until score > threshold.

Today every one of these requires manual orchestration in the Compose
tab. The Canvas makes them first-class.

## 3. Architecture overview

```
┌─────────────────────────────────────────────────────────┐
│ apps/gui/canvas/                  (PySide6 frontend)    │
│   canvas.py        OrchestratorCanvas (QGraphicsView)   │
│   scene.py         CanvasScene (grid, snap, viewport)   │
│   nodes/           BaseNode + AgentNode + control nodes │
│   ports.py         Input/output docking points          │
│   edges.py         Bezier edges, dragging, animation    │
│   palette.py       Drag source — list of available nodes│
│   inspector.py     Right-side panel for selection       │
│   minimap.py       Bottom-right overview widget         │
│   layout.py        Auto-layout (sugiyama on networkx)   │
└────────────────────────────┬────────────────────────────┘
                             │ JSON-RPC + SSE
┌────────────────────────────▼────────────────────────────┐
│ apps/service/flows/               (executor + storage)  │
│   executor.py      Topological run loop, parallel       │
│                    fan-out via asyncio.gather           │
│   nodes.py         Per-node-type execution adapters     │
│   types.py         Flow / FlowNode / FlowEdge / FlowRun │
└────────────────────────────┬────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────┐
│ apps/service/store/                                     │
│   schema.sql       New flows / flow_runs tables         │
│   events.py        CRUD + flow_run_*                    │
└─────────────────────────────────────────────────────────┘
```

The Canvas is a new tab alongside Home / Compose / History / Settings.
The existing single-dispatch Compose flow stays — Flow Canvas is
additive, not a replacement.

## 4. Technical decisions

### 4.1 Frontend: QGraphicsView, not WebView, not QML

`QGraphicsView` + `QGraphicsScene` is Qt's native 2D scene graph.

Why:
- Mature and proven for node editors (Krita, Maya plugins, Spyder).
- Built-in viewport culling, item-level paint, scene transforms
  (`scale`, `translate`), mouse wheel zoom, rubber-band selection.
- Per-item LOD via `option->levelOfDetailFromTransform()`.
- Pure Qt — no extra runtime, no Chromium, no IPC.
- 100% Pythonic via PySide6.

Why not QML: declarative UI is great for static layouts but heavy for
custom paint/interaction; bridging Python state into QML's property
system adds friction.

Why not embedded React Flow / Cytoscape: shipping Chromium for one
panel is a 100 MB+ dist regression and an IPC nightmare for live
events.

### 4.2 Levels of detail (LOD)

Three tiers, switched on `levelOfDetailFromTransform`:

| Zoom level | What a node draws |
|------------|-------------------|
| > 0.6      | Full: header, body text, ports, status badges, cost, last reply preview |
| 0.25–0.6   | Compact: header + provider icon + status colour |
| < 0.25     | Dot: a single coloured circle, no text |

Edges fade their labels and arrow-heads at < 0.4. Below 0.2 we draw
straight lines instead of beziers — cheaper paint, same topology.

### 4.3 Persistence: flow JSON in SQLite

A flow is a small JSON document (typically < 50 KB even for
hundred-node graphs). Store it as a single column rather than
normalising every node and edge:

```sql
CREATE TABLE flows (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL,        -- JSON: {nodes:[...], edges:[...]}
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE flow_runs (
    id           TEXT PRIMARY KEY,
    flow_id      TEXT NOT NULL REFERENCES flows(id),
    state        TEXT NOT NULL,        -- pending / running / finished / aborted
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    payload      TEXT NOT NULL         -- per-node run state, outputs
);
```

Per-node Step rows still go to the existing `steps` table so the
existing History / search machinery keeps working. The flow-run wraps
them with a `flow_run_id` foreign key.

### 4.4 Executor model & Isolation

A flow run is N coordinated single-agent runs. The executor:

1. **Topologically sorts** the graph. Cycles raise `FlowValidationError`
   unless the cycle is explicitly a Loop control node.
2. **Walks the sort**, dispatching nodes whose dependencies are all
   complete. Independent nodes at the same depth are dispatched
   concurrently via `asyncio.gather`.
3. **Resolves inputs**: each node receives a dict of `{from_port:
   upstream_output}` from its incoming edges. The node's adapter
   formats this into the prompt for its underlying card.
4. **Isolated Parallel Execution**: For parallel agents, `_run_agent` is called
   concurrently. Under the hood, providers like `claude_cli` and `gemini_cli`
   spawn **completely separate OS-level subprocesses** (`asyncio.create_subprocess_exec`).
   This natively guarantees that each agent instance has an entirely isolated
   context window and environment, preventing any cross-contamination.
5. **Emits events**: `flow.node.queued / started / completed / failed`
   plus the existing per-card `run.*` events, all on the same SSE
   bus. The canvas subscribes per-flow-run and updates node visuals
   in real time.
6. **Persists** every node's output as a normal `Run` (so
   History / search / replay all work) plus a `flow_node_run` row
   linking it back to the flow.

Reuses the existing `dispatcher.py` for the actual provider calls.
The flow executor is a thin coordinator on top.

### 4.5 Flights (Multi-Agent Templates)

A "Flight" is a pre-set template of grouped agents (a saved Flow) designed to be deployed as a cohesive unit. While individual agents are generated from "Blueprints", a "Flight" is a top-level architectural map that stamps out multiple instances and their routing at once.
- Saved flows can be marked as `is_flight = True`.
- Flights act as reusable, multi-agent deployment patterns.

### 4.5 Live updates without polling

Same SSE channel the existing Live page uses. The canvas opens one
`stream/flow_runs/<id>` subscription per active flow. Each event:

- `flow.node.started` → pulse the node's border, change colour.
- `flow.node.token_delta` → stream tokens into the node body's
  preview area.
- `flow.node.completed` → freeze final preview, show cost, animate
  little dots flowing along outgoing edges.
- `flow.node.failed` → red border, error tooltip, downstream nodes
  marked unreachable.

## 5. Phased delivery

Each phase is independently shippable and useful on its own.

### Phase 1 — Canvas foundation (2–3 days)

- New tab in `MainWindow`: **Canvas**.
- `OrchestratorCanvas` (QGraphicsView) with grid background.
- Pan: middle-mouse-drag or Space+drag. Zoom: Ctrl+wheel,
  cursor-anchored.
- Rubber-band selection. Multi-select with Shift.
- A generic `BaseNode` (rounded rect with title bar) you can drop
  programmatically and drag around. LOD already in place.
- Bezier edges between two nodes (no port logic yet — endpoints are
  just the node centres).
- Save / load canvas state to a JSON file on disk (no DB yet).

**Demo:** drop boxes on a canvas, drag them around, draw curves
between them, zoom from 10% to 400% smoothly, save the layout.

### Phase 2 — Agent nodes & palette (2 days)

- `AgentNode(BaseNode)` — wraps a `PersonalityCard`. Header shows
  card name + provider icon (Claude / Gemini / OpenAI / Ollama / CLI).
  Body shows current prompt template summary.
- `Palette` panel on the left: lists all cards from `cards.list`,
  drag a card onto the canvas to spawn an `AgentNode`.
- `Inspector` panel on the right: when one node is selected, shows
  editable fields for that node's card overrides (prompt, provider,
  model). Calls existing `cards.list` / `templates.get` RPCs.
- Real input/output ports on each node, snap edges to ports.

**Demo:** drag two cards onto the canvas, connect them, edit prompts
in the inspector, save the flow as JSON.

### Phase 3 — Flow execution (4–5 days)

- New types `Flow`, `FlowNode`, `FlowEdge`, `FlowRun` in
  `apps/service/types.py`.
- Schema migration: `flows`, `flow_runs`, `flow_node_runs` tables.
- `apps/service/flows/executor.py`: topological sort, parallel
  dispatch via `asyncio.gather`, error propagation.
- New RPCs: `flows.list / get / create / update / delete /
  dispatch / cancel`.
- New SSE channel `stream/flow_runs/<id>`.
- Canvas subscribes to the SSE for an active run, updates node
  visuals: queued (grey), running (pulsing blue), completed (green),
  failed (red). Edges animate dots flowing left-to-right.
- Inspector panel shows the streaming transcript of the selected
  running node — same widget as the existing Live page.

**Demo:** build a 3-step flow (Broad Research → Narrow Research →
Synthesis), hit Run, watch each node fire in order, see the final
answer.

### Phase 4 — Control nodes (3–4 days)

- `TriggerNode` — manual (button) for V1; scheduled / webhook later.
- `BranchNode` — outputs route to one of N downstream paths based on
  a predicate. V1 supports two predicates: regex match on the
  upstream text, and an LLM-judge prompt that returns a label.
- `MergeNode` — joins N parallel branches; output is the
  concatenation or an LLM-summarised synthesis.
- `HumanNode` — pauses the run, surfaces an approval prompt in the
  GUI; downstream nodes wait until Approve / Reject is clicked.
- `OutputNode` — terminal sink. Renders the upstream result as a
  Markdown preview, optionally writes to a file path or pushes to a
  Slack webhook.

**Demo:** "broad research → branch on findings count → narrow
research per finding (parallel fan-out) → merge → human approves →
write report" — a non-trivial workflow that today would take a
half-hour of manual dispatch.

### Phase 5 — Polish (2–3 days)

- **Minimap** in the bottom-right corner showing the whole graph
  with a viewport rectangle you can drag.
- **Auto-layout** button: runs a Sugiyama (hierarchical) layout via
  `networkx` topological sort + manual lane assignment, or use
  `pygraphviz` if the user has it installed.
- **Validator** with a problems panel: "node X is unreachable",
  "cycle detected at edge Y→Z", "node W has no card assigned".
- **Undo / redo** (QUndoStack with QUndoCommand subclasses for
  add/remove/move/connect).
- **Templates**: save a flow as a template, instantiate from
  templates, share JSON files with other users.
- **Keyboard shortcuts**: Delete, Ctrl+D (duplicate), Ctrl+G (group
  into sub-flow placeholder), Ctrl+A (select all), F (zoom-to-fit), Z
  (zoom-to-selection), Space (pan).
- **Export**: render the canvas as PNG / SVG for documentation.

### Phase 6 — Stretch goals (open-ended)

- **Per-node A/B**: each node can opt-in to "run on Claude AND Gemini
  in parallel" with a built-in judge — visualised as a node that
  splits and re-merges automatically.
- **Sub-flows**: collapse a selected group of nodes into a single
  callable sub-flow node; flows-as-functions.
- **Versioning** with a diff view between two saved versions of the
  same flow.
- **Scheduled triggers**: cron-like syntax on TriggerNode.
- **HTTP webhook triggers**: each TriggerNode gets a unique URL.
- **Marketplace**: import community-published flow templates from a
  public registry.
- **Live collaboration** (very long term): Yjs / CRDT under
  QGraphicsScene so two operators can edit the same flow.

## 6. Effort estimate

| Phase | Working days | Cumulative |
|-------|--------------|------------|
| 1     | 2–3          | 3          |
| 2     | 2            | 5          |
| 3     | 4–5          | 10         |
| 4     | 3–4          | 14         |
| 5     | 2–3          | 17         |
| 6     | open         | —          |

A useful MVP through **Phase 4 ≈ two weeks of focused work**.

## 7. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Slow paint with many nodes | LOD tiers + viewport culling (built into QGraphicsView). Profile at 200 nodes. |
| Edge routing looks tangled at scale | V1: simple bezier. V2: A* routing around obstacles. |
| Cycle detection performance on huge graphs | `networkx.simple_cycles` is good enough until ~10⁴ edges. |
| Live SSE flood when many nodes run in parallel | Coalesce token-delta events on the service side: emit at most one per node per 50 ms. |
| Existing Compose flow regression | Keep Compose tab. Canvas tab is additive. Same RPCs underneath. |
| Confusing UX on first launch | Ship a built-in "Hello flow" template (Trigger → Broad Research → Output). One click to instantiate. |
| Loops / infinite recursion | Hard cap on per-flow-run total node executions (e.g. 100). Configurable per flow. |
| Cost runaway on parallel fan-out | Reuse existing per-card cost caps; flow-level cap as a sum across all node runs. Hard-stop on breach. |

## 8. How this fits the existing roadmap

- **Phase 1–4 of the original plan**: built. Cards, dispatch,
  worktrees, History, Live page, providers (Claude, Gemini, Anthropic
  API, Google API, Ollama), CLI variants, annotator, basic GUI.
- **This plan = Phase 5**: visual orchestration on top of the
  existing dispatch primitives.
- **Existing primitives are reused unchanged**: `PersonalityCard`,
  `dispatcher.py`, `EventStore`, `EventBus`, `RpcClient`. The
  Canvas is a new view + a new orchestration layer; nothing under it
  needs to change.

## 9. First concrete steps (when greenlit)

1. Create `apps/gui/canvas/` package with the Phase 1 skeleton:
   `canvas.py`, `scene.py`, `nodes/__init__.py`, `nodes/base.py`,
   `edges.py`. Wire a placeholder Canvas tab into `MainWindow`.
2. Verify pan/zoom feel right with a hardcoded 5-node scene.
3. Land Phase 1 as PR. Ship.
4. Iterate: Phase 2 PR, Phase 3 PR, etc.
5. Each phase ships behind a `Canvas` rail-button so users opt in
   gradually.
