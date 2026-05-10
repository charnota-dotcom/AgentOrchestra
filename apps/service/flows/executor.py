"""Flow executor — coordinator that walks a flow graph and dispatches
each node through the existing single-agent dispatcher.

Design:

* Topological sort, then dispatch in waves: each wave is the set of
  nodes whose dependencies are all complete.  Independent nodes in
  the same wave fire concurrently via ``asyncio.gather``.
* A node receives, as its goal, the concatenated output of its
  upstream nodes (or its own ``params.goal`` override for AgentNodes).
* Control nodes (Trigger / Branch / Merge / Human / Output) are
  implemented inline rather than dispatched through a provider.
* Cancellation: a flow run holds a single ``asyncio.Task`` per
  in-flight node; ``cancel`` cancels the lot and marks the run as
  ``aborted``.
* Events: ``flow.node.queued / started / token_delta / completed /
  failed / human_pending`` flow through the existing ``EventBus`` and
  per-run SSE channel keyed by the flow_run_id.
* Loops are out of scope for V1 — cycle detection raises
  ``FlowValidationError`` before execution starts.

This module never opens a provider session itself; it asks the
existing ``Dispatcher`` to do that work, with one quirk: chat-style
flow nodes go through a thin direct path because the existing
``runs.dispatch`` machinery is geared toward standalone runs with
their own state machine.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from apps.service.providers.registry import get_provider
from apps.service.types import (
    Event,
    EventKind,
    EventSource,
    Flow,
    FlowRun,
    FlowState,
    PersonalityCard,
    long_id,
    utc_now,
)

if TYPE_CHECKING:
    from apps.service.store.events import EventStore

log = logging.getLogger(__name__)


class FlowValidationError(Exception):
    """Raised before execution if the graph is malformed (cycles,
    missing nodes, dangling edges, etc.)."""


class FlowExecutor:
    def __init__(self, store: EventStore) -> None:
        self.store = store
        # Track active runs so we can cancel them.  A flow run owns
        # exactly one supervisor task; cancelling that task aborts
        # any nested asyncio.gather of node tasks underneath.
        self._active: dict[str, asyncio.Task[Any]] = {}
        self._human_waiters: dict[tuple[str, str], asyncio.Future[bool]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def dispatch(self, flow: Flow) -> FlowRun:
        run = FlowRun(flow_id=flow.id, state=FlowState.PENDING)
        await self.store.insert_flow_run(run)
        task = asyncio.ensure_future(self._supervise(flow, run))
        self._active[run.id] = task
        return run

    async def cancel(self, run_id: str) -> bool:
        task = self._active.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def approve_human(self, run_id: str, node_id: str, approved: bool) -> bool:
        future = self._human_waiters.get((run_id, node_id))
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _supervise(self, flow: Flow, run: FlowRun) -> None:
        try:
            self._validate(flow)
            run.state = FlowState.RUNNING
            await self.store.update_flow_run(run)
            await self._run_graph(flow, run)
            if run.state == FlowState.RUNNING:  # not flipped to FAILED
                run.state = FlowState.FINISHED
        except asyncio.CancelledError:
            run.state = FlowState.ABORTED
        except FlowValidationError as exc:
            run.state = FlowState.FAILED
            run.error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("flow run %s crashed", run.id)
            run.state = FlowState.FAILED
            run.error = repr(exc)
        finally:
            run.ended_at = utc_now()
            await self.store.update_flow_run(run)
            await self._emit(run.id, "flow.completed", payload={"state": run.state.value})
            self._active.pop(run.id, None)

    # ------------------------------------------------------------------
    # Validation + topo sort
    # ------------------------------------------------------------------

    def _validate(self, flow: Flow) -> None:
        node_ids = {n["id"] for n in flow.nodes}
        for e in flow.edges:
            if e.get("from_node") not in node_ids or e.get("to_node") not in node_ids:
                raise FlowValidationError(
                    f"edge references unknown node: {e.get('from_node')}->{e.get('to_node')}"
                )
        # Cycle detection via DFS.
        graph: dict[str, list[str]] = defaultdict(list)
        for e in flow.edges:
            graph[e["from_node"]].append(e["to_node"])
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {nid: WHITE for nid in node_ids}

        def visit(n: str) -> None:
            if colour[n] == GREY:
                raise FlowValidationError(f"cycle detected through node {n}")
            if colour[n] == BLACK:
                return
            colour[n] = GREY
            for m in graph.get(n, []):
                visit(m)
            colour[n] = BLACK

        for nid in node_ids:
            if colour[nid] == WHITE:
                visit(nid)

    # ------------------------------------------------------------------
    # Graph walking
    # ------------------------------------------------------------------

    async def _run_graph(self, flow: Flow, run: FlowRun) -> None:
        nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in flow.nodes}
        # Adjacency and in-degree for topo waves.
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        indegree: dict[str, int] = {nid: 0 for nid in nodes}
        for e in flow.edges:
            outgoing[e["from_node"]].append(e)
            indegree[e["to_node"]] += 1
        outputs: dict[str, str] = {}
        # Edges that were "blocked" by a Branch routing decision —
        # we treat downstream nodes whose only inputs are blocked as
        # skipped, not failed.
        blocked_edges: set[tuple[str, str, str]] = set()  # (from_node, from_port, to_node)
        skipped: set[str] = set()
        # Each node's incoming edges, used to compute "is ready".
        incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in flow.edges:
            incoming[e["to_node"]].append(e)

        ready: list[str] = [nid for nid, deg in indegree.items() if deg == 0]
        completed: set[str] = set()

        # Cards loaded once — keep a tiny cache so we don't roundtrip
        # the store for every agent node.
        card_cache: dict[str, PersonalityCard] = {}

        async def execute(node_id: str) -> None:
            node = nodes[node_id]
            # Determine effective inputs.
            inputs: list[str] = []
            for e in incoming[node_id]:
                if (e["from_node"], e.get("from_port", ""), node_id) in blocked_edges:
                    continue
                if e["from_node"] in skipped:
                    continue
                if e["from_node"] in outputs:
                    inputs.append(outputs[e["from_node"]])
            # If every incoming edge is blocked or skipped, the node
            # itself is skipped.
            if incoming[node_id] and not inputs and node["type"] != "trigger":
                skipped.add(node_id)
                await self._emit(
                    run.id,
                    "flow.node.skipped",
                    payload={"node_id": node_id},
                )
                return

            await self._emit(run.id, "flow.node.queued", payload={"node_id": node_id})
            await self._emit(run.id, "flow.node.started", payload={"node_id": node_id})

            try:
                if node["type"] == "trigger":
                    output = "(start)"
                elif node["type"] == "agent":
                    output = await self._run_agent(run.id, node, inputs, card_cache)
                elif node["type"] == "branch":
                    output, take_true = self._run_branch(node, inputs)
                    # Block the edge from the *not-taken* port so its
                    # downstream subgraph gets skipped.
                    for e in outgoing[node_id]:
                        port = e.get("from_port", "")
                        if (take_true and port == "false") or (not take_true and port == "true"):
                            blocked_edges.add((node_id, port, e["to_node"]))
                elif node["type"] == "merge":
                    output = self._run_merge(inputs)
                elif node["type"] == "human":
                    approved = await self._wait_human(run.id, node_id, inputs)
                    if not approved:
                        skipped.add(node_id)
                        await self._emit(
                            run.id,
                            "flow.node.skipped",
                            payload={"node_id": node_id, "reason": "rejected"},
                        )
                        return
                    output = inputs[0] if inputs else ""
                elif node["type"] == "output":
                    output = inputs[0] if inputs else ""
                else:
                    raise FlowValidationError(f"unknown node type: {node['type']}")
            except Exception as exc:
                log.exception("flow node %s failed", node_id)
                await self._emit(
                    run.id,
                    "flow.node.failed",
                    payload={"node_id": node_id, "error": repr(exc)},
                )
                raise

            outputs[node_id] = output
            run.node_outputs[node_id] = output
            await self.store.update_flow_run(run)
            await self._emit(
                run.id,
                "flow.node.completed",
                payload={"node_id": node_id, "output": output[:2000]},
            )

        # Walk in waves.  This is intentionally simple: we don't
        # build a full DAG scheduler here — the nodes-per-wave model
        # is correct for the kinds of graphs operators will draw
        # (tens of nodes, mostly shallow).
        while ready:
            wave = ready
            ready = []
            results = await asyncio.gather(
                *(execute(nid) for nid in wave),
                return_exceptions=True,
            )
            for nid, res in zip(wave, results, strict=False):
                if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                    run.state = FlowState.FAILED
                    run.error = repr(res)
                if nid not in skipped:
                    completed.add(nid)
            if run.state == FlowState.FAILED:
                return
            # Anything whose inputs are now satisfied (all upstream
            # either completed or skipped) joins the next wave.
            for nid in nodes:
                if nid in completed or nid in skipped or nid in ready:
                    continue
                upstream_ids = [e["from_node"] for e in incoming[nid]]
                if upstream_ids and all(u in completed or u in skipped for u in upstream_ids):
                    ready.append(nid)

    # ------------------------------------------------------------------
    # Per-node-type adapters
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        run_id: str,
        node: dict[str, Any],
        inputs: list[str],
        card_cache: dict[str, PersonalityCard],
    ) -> str:
        card_id = node.get("card_id")
        if not card_id:
            raise FlowValidationError(f"agent node {node['id']} has no card_id")
        if card_id not in card_cache:
            cards = await self.store.list_cards()
            for c in cards:
                if c.id == card_id:
                    card_cache[card_id] = c
                    break
            else:
                raise FlowValidationError(f"card not found: {card_id}")
        card = card_cache[card_id]

        params = node.get("params") or {}
        goal_override = (params.get("goal") or "").strip()
        if goal_override:
            prompt = goal_override
        elif inputs:
            prompt = "\n\n".join(inputs)
        else:
            raise FlowValidationError(
                f"agent node {node['id']} has neither a goal override nor an upstream input"
            )

        provider = get_provider(card.provider)
        session = await provider.open_chat(card)
        accumulated: list[str] = []
        try:
            async for ev in session.send(prompt):
                if ev.kind == "text_delta":
                    accumulated.append(ev.text)
                    await self._emit(
                        run_id,
                        "flow.node.token_delta",
                        payload={"node_id": node["id"], "delta": ev.text[:1000]},
                    )
                elif ev.kind == "error":
                    raise RuntimeError(ev.text or "provider error")
                elif ev.kind == "finish":
                    break
        finally:
            await session.close()
        return "".join(accumulated)

    @staticmethod
    def _run_branch(
        node: dict[str, Any],
        inputs: list[str],
    ) -> tuple[str, bool]:
        text = inputs[0] if inputs else ""
        params = node.get("params") or {}
        pattern = params.get("pattern", ".*")
        matched = bool(re.search(pattern, text))
        return text, matched

    @staticmethod
    def _run_merge(inputs: list[str]) -> str:
        return "\n\n---\n\n".join(inputs)

    async def _wait_human(self, run_id: str, node_id: str, inputs: list[str]) -> bool:
        future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._human_waiters[(run_id, node_id)] = future
        await self._emit(
            run_id,
            "flow.node.human_pending",
            payload={
                "node_id": node_id,
                "preview": (inputs[0] if inputs else "")[:1000],
            },
        )
        try:
            return await future
        finally:
            self._human_waiters.pop((run_id, node_id), None)

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    _KIND_MAP = {
        "flow.node.queued": EventKind.FLOW_NODE_QUEUED,
        "flow.node.started": EventKind.FLOW_NODE_STARTED,
        "flow.node.token_delta": EventKind.FLOW_NODE_TOKEN_DELTA,
        "flow.node.completed": EventKind.FLOW_NODE_COMPLETED,
        "flow.node.failed": EventKind.FLOW_NODE_FAILED,
        "flow.node.skipped": EventKind.FLOW_NODE_SKIPPED,
        "flow.node.human_pending": EventKind.FLOW_NODE_HUMAN_PENDING,
        "flow.completed": EventKind.FLOW_COMPLETED,
    }

    async def _emit(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        ev_kind = self._KIND_MAP.get(kind, EventKind.LLM_CALL_COMPLETED)
        await self.store.append_event(
            Event(
                id=long_id(),
                source=EventSource.DISPATCH_RUN,
                kind=ev_kind,
                run_id=run_id,
                payload=payload,
                text="",
            )
        )
