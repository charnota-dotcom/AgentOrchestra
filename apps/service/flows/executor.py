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
import json
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from apps.service.flows.node_types import canonical_node_type
from apps.service.providers import registry as provider_registry
from apps.service.tokens import estimate_action_total
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

_KNOWN_NODE_TYPES = {
    "trigger",
    "branch",
    "merge",
    "human",
    "output",
    "reaper",
    "fpv_drone",
    "staging_area",
    "consensus",
}


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
        self._waiting_reported: set[tuple[str, str]] = set()

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
        for n in flow.nodes:
            node_type = canonical_node_type(str(n.get("type") or ""))
            if node_type not in _KNOWN_NODE_TYPES:
                raise FlowValidationError(f"unknown node type: {n.get('type')}")
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

    @staticmethod
    def _canonical_type(node: dict[str, Any]) -> str:
        return canonical_node_type(str(node.get("type") or ""))

    @staticmethod
    def _directional_inputs(node_id: str, incoming: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return [e for e in incoming.get(node_id, []) if e.get("directional", True)]

    @staticmethod
    def _count_ready_inputs(
        node_id: str,
        incoming: dict[str, list[dict[str, Any]]],
        completed: set[str],
        skipped: set[str],
    ) -> tuple[int, int]:
        directional = FlowExecutor._directional_inputs(node_id, incoming)
        total = len(directional)
        ready = 0
        for e in directional:
            upstream = e["from_node"]
            if upstream in completed or upstream in skipped:
                ready += 1
        return ready, total

    @staticmethod
    def _node_ready(
        node: dict[str, Any],
        incoming: dict[str, list[dict[str, Any]]],
        completed: set[str],
        skipped: set[str],
    ) -> bool:
        node_type = canonical_node_type(str(node.get("type") or ""))
        ready, total = FlowExecutor._count_ready_inputs(node["id"], incoming, completed, skipped)
        if total == 0:
            return True
        if node_type == "staging_area":
            mode = str((node.get("params") or {}).get("mode") or "wait_for_all")
            if mode == "wait_for_any":
                return ready >= 1
            if mode == "threshold":
                threshold = int((node.get("params") or {}).get("threshold") or 1)
                return ready >= max(1, threshold)
        return ready >= total

    @staticmethod
    def _staging_wait_reason(
        node: dict[str, Any],
        incoming: dict[str, list[dict[str, Any]]],
        completed: set[str],
        skipped: set[str],
    ) -> str | None:
        if canonical_node_type(str(node.get("type") or "")) != "staging_area":
            return None
        ready, total = FlowExecutor._count_ready_inputs(node["id"], incoming, completed, skipped)
        params = node.get("params") or {}
        mode = str(params.get("mode") or "wait_for_all")
        if total == 0:
            return None
        if mode == "wait_for_any":
            if ready >= 1:
                return None
            return "waiting for the first upstream result"
        if mode == "threshold":
            threshold = max(1, int(params.get("threshold") or 1))
            if ready >= threshold:
                return None
            return f"waiting for threshold {ready}/{threshold}"
        if ready < total:
            return f"waiting for upstream inputs {ready}/{total}"
        return None

    @staticmethod
    def _inputs_to_text(inputs: dict[str, list[str]]) -> str:
        if not inputs:
            return ""
        chunks: list[str] = []
        for port, values in inputs.items():
            if not values:
                continue
            label = port or "in"
            chunks.append(f"[{label}]\n" + "\n\n".join(values))
        return "\n\n".join(chunks)

    @staticmethod
    def _summarize_staging(
        node: dict[str, Any],
        inputs: dict[str, list[str]],
        *,
        prefix: str = "",
    ) -> str:
        summary = FlowExecutor._inputs_to_text(inputs).strip()
        params = node.get("params") or {}
        note = str(params.get("summary_hint") or params.get("release_note") or "").strip()
        parts = [part for part in (prefix.strip(), note, summary) if part]
        return "\n\n".join(parts) if parts else "(no input)"

    async def _staging_agent_summary(
        self,
        run_id: str,
        node: dict[str, Any],
        inputs: dict[str, list[str]],
        card_cache: dict[str, PersonalityCard],
    ) -> str:
        params = node.get("params") or {}
        card_id = params.get("decision_card_id")
        if not card_id:
            return self._summarize_staging(node, inputs, prefix="Release summary:")
        card = card_cache.get(card_id)
        if card is None:
            raise FlowValidationError(f"decision card not found: {card_id}")
        provider = provider_registry.get_provider(card.provider)
        session = await provider.open_chat(card)
        prompt = (
            "You are a staging-area reaper. Summarize the input and recommend whether to release it.\n"
            "Return a concise decision note followed by the release summary.\n\n"
            + self._summarize_staging(node, inputs, prefix="Input:")
        )
        text = ""
        try:
            async for ev in session.send(prompt):
                if ev.kind == "text_delta":
                    text += ev.text
                    await self._emit(
                        run_id,
                        "flow.node.token_delta",
                        payload={"node_id": node["id"], "delta": ev.text[:1000]},
                    )
                elif ev.kind == "error":
                    raise RuntimeError(ev.text or "decision provider error")
                elif ev.kind == "finish":
                    break
        finally:
            await session.close()
        return text.strip() or self._summarize_staging(node, inputs, prefix="Release summary:")

    # ------------------------------------------------------------------
    # Graph walking
    # ------------------------------------------------------------------

    async def _run_graph(self, flow: Flow, run: FlowRun) -> None:
        nodes: dict[str, dict[str, Any]] = {
            n["id"]: {**n, "type": canonical_node_type(str(n.get("type") or ""))}
            for n in flow.nodes
        }
        # Adjacency and in-degree for topo waves.
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # Sequential in-degree (directional edges only)
        seq_indegree: dict[str, int] = {nid: 0 for nid in nodes}
        for e in flow.edges:
            outgoing[e["from_node"]].append(e)
            if e.get("directional", True):
                seq_indegree[e["to_node"]] += 1
        
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

        # A node is 'ready' if all its *directional* (sequential) 
        # dependencies are satisfied. Non-directional edges act as 
        # data references but don't block the wave.
        ready: list[str] = [nid for nid, deg in seq_indegree.items() if deg == 0]
        completed: set[str] = set()

        # Cards loaded once — pre-populate so concurrent agent nodes
        # in the same wave don't all stampede self.store.list_cards().
        all_cards = await self.store.list_cards()
        card_cache: dict[str, PersonalityCard] = {c.id: c for c in all_cards}
        # Serialise concurrent node-completion writes: outputs / run.node_outputs /
        # update_flow_run() all share state across asyncio.gather'd tasks
        # in the same wave.
        outputs_lock = asyncio.Lock()
        waiting_reported: set[str] = set()

        async def execute(node_id: str) -> None:
            node = nodes[node_id]
            node_type = canonical_node_type(str(node.get("type") or ""))

            inputs: dict[str, list[str]] = defaultdict(list)
            for e in incoming[node_id]:

                from_nid = e["from_node"]
                from_port = e.get("from_port", "")
                to_port = e.get("to_port", "")

                if (from_nid, from_port, node_id) in blocked_edges:
                    continue
                if from_nid in skipped:
                    continue
                
                # We pull data from any upstream node that has finished,
                # whether the edge was directional or not.
                if from_nid in outputs:
                    inputs[to_port].append(outputs[from_nid])

            # If every incoming edge is blocked or skipped, the node
            # itself is skipped.
            # (Trigger nodes have no incoming edges in V1)
            has_directional_inputs = any(e.get("directional", True) for e in incoming[node_id])
            if has_directional_inputs and not inputs and node_type != "trigger":
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
                if node_type == "trigger":
                    output = "(start)"
                elif node_type == "reaper":
                    output = await self._run_agent(run.id, node, inputs, card_cache, incoming, nodes)
                elif node_type == "fpv_drone":
                    output = await self._run_fpv_drone(run.id, node, inputs)
                elif node_type == "staging_area":
                    outcome = await self._run_staging_area(run.id, node, inputs, card_cache)
                    status = outcome["status"]
                    if status != "released":
                        skipped.add(node_id)
                        await self._emit(
                            run.id,
                            f"flow.node.{status}",
                            payload={
                                "node_id": node_id,
                                "reason": outcome.get("reason", ""),
                                "preview": outcome.get("output", "")[:2000],
                            },
                        )
                        return
                    await self._emit(
                        run.id,
                        "flow.node.released",
                        payload={
                            "node_id": node_id,
                            "reason": outcome.get("reason", ""),
                            "preview": str(outcome.get("output") or "")[:2000],
                        },
                    )
                    output = str(outcome.get("output") or "")
                elif node_type == "consensus":
                    output = await self._run_consensus_node(run.id, node, inputs, card_cache)
                elif node_type == "branch":
                    output, take_true = self._run_branch(node, inputs)
                    # Block the edge from the *not-taken* port so its
                    # downstream subgraph gets skipped.
                    for e in outgoing[node_id]:
                        port = e.get("from_port", "")
                        if (take_true and port == "false") or (not take_true and port == "true"):
                            blocked_edges.add((node_id, port, e["to_node"]))
                elif node_type == "merge":
                    output = self._run_merge(inputs)
                elif node_type == "human":
                    approved = await self._wait_human(run.id, node_id, inputs)
                    if not approved:
                        skipped.add(node_id)
                        await self._emit(
                            run.id,
                            "flow.node.skipped",
                            payload={"node_id": node_id, "reason": "rejected"},
                        )
                        return
                    output = self._flatten_inputs(inputs)
                elif node_type == "output":
                    output = self._flatten_inputs(inputs)
                else:
                    raise FlowValidationError(f"unknown node type: {node.get('type')}")
            except Exception as exc:
                log.exception("flow node %s failed", node_id)
                await self._emit(
                    run.id,
                    "flow.node.failed",
                    payload={"node_id": node_id, "error": repr(exc)},
                )
                raise

            async with outputs_lock:
                outputs[node_id] = output
                run.node_outputs[node_id] = output
                await self.store.update_flow_run(run)
            await self._emit(
                run.id,
                "flow.node.completed",
                payload={"node_id": node_id, "output": output[:2000]},
            )

        # Walk in waves.
        while ready:
            wave = ready
            ready = []
            tasks = {nid: asyncio.create_task(execute(nid)) for nid in wave}
            try:
                results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            except asyncio.CancelledError:
                for t in tasks.values():
                    t.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)
                raise
            for nid, res in zip(wave, results, strict=False):
                if isinstance(res, BaseException) and not isinstance(res, asyncio.CancelledError):
                    run.state = FlowState.FAILED
                    run.error = repr(res)
                if nid not in skipped:
                    completed.add(nid)

            if run.state == FlowState.FAILED:
                return
            # Anything whose inputs are now satisfied joins the next wave.
            for nid, candidate in nodes.items():
                if nid in completed or nid in skipped or nid in ready:
                    continue
                if not self._node_ready(candidate, incoming, completed, skipped):
                    reason = self._staging_wait_reason(candidate, incoming, completed, skipped)
                    if reason and nid not in waiting_reported:
                        waiting_reported.add(nid)
                        await self._emit(
                            run.id,
                            "flow.node.waiting",
                            payload={"node_id": nid, "reason": reason},
                        )
                    continue
                ready.append(nid)

    # ------------------------------------------------------------------
    # Per-node-type adapters
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        run_id: str,
        node: dict[str, Any],
        inputs: dict[str, list[str]],
        card_cache: dict[str, PersonalityCard],
        incoming: dict[str, list[dict[str, Any]]],
        nodes: dict[str, dict[str, Any]],
    ) -> str:
        card_id = node.get("card_id")
        if not card_id:
            raise FlowValidationError(f"agent node {node['id']} has no card_id")
        card = card_cache.get(card_id)
        if card is None:
            raise FlowValidationError(f"card not found: {card_id}")

        params = node.get("params") or {}
        goal_override = (params.get("goal") or "").strip()

        # Bug Gap 1: Handle port-specific inputs for Agents.
        # If 'instructions' port is connected, use it as the primary prompt.
        # Otherwise use goal override.
        # If 'context' port is connected, append it as context.
        instructions = goal_override
        if "instructions" in inputs:
            instructions = "\n\n".join(inputs["instructions"])

        context = ""
        if "context" in inputs:
            context = "\n\n".join(inputs["context"])

        # FALLBACK for generic inputs (unnamed ports or backward compat)
        generic = "\n\n".join(inputs.get("", []))

        # Peer context: Fetch transcripts of other live drones linked
        # on the canvas (enabled by the user via non-directional links).
        peer_lines = []
        for e in incoming[node["id"]]:
            # We already handled directional (synchronous data) edges
            # in Wave calculation. Non-directional edges here serve
            # as context/reference providers.
            if e.get("directional"):
                continue
            
            from_nid = e["from_node"]
            from_node = nodes.get(from_nid)
            if not from_node or canonical_node_type(str(from_node.get("type") or "")) != "fpv_drone":
                continue
                
            aid = from_node.get("action_id")
            if not aid:
                continue
                
            ref_action = await self.store.get_drone_action(aid)
            if not ref_action:
                continue
            
            ref_snap = ref_action.blueprint_snapshot or {}
            ref_name = ref_action.name or ref_snap.get("name") or "Peer"
            
            ref_turns = []
            for m in (ref_action.transcript or []):
                if m.get("role") in ("user", "assistant"):
                    speaker = "User" if m.get("role") == "user" else f"Agent ({ref_name})"
                    ref_turns.append(f"{speaker}: {m.get('content', '')}")
            
            if ref_turns:
                peer_lines.append(
                    f"### PEER CONTEXT: Shared history with '{ref_name}'\n"
                    + "\n".join(ref_turns)
                )

        prompt_parts = []
        if instructions:
            prompt_parts.append(instructions)
        if context:
            prompt_parts.append("### CONTEXT\n" + context)
        if generic:
            prompt_parts.append(generic)
        if peer_lines:
            prompt_parts.append("\n\n".join(peer_lines))

        prompt = "\n\n".join(prompt_parts)
        if not prompt:
            prompt = self._flatten_inputs(inputs)
        if not prompt:
            raise FlowValidationError(
                f"agent node {node['id']} has no goal override or connected inputs"
            )

        provider = provider_registry.get_provider(card.provider)
        session = await provider.open_chat(card)
        accumulated: list[str] = []
        try:
            async for ev in session.send(prompt):
                if ev.kind == "text_delta":
                    accumulated.append(ev.text)
                    # Support both Flow Canvas (node body) and live chat tabs.
                    await self._emit(
                        run_id,
                        "flow.node.token_delta",
                        payload={"node_id": node["id"], "delta": ev.text[:1000]},
                    )
                    await self.store.append_event(
                        Event(
                            source=EventSource.DISPATCH_RUN,
                            kind=EventKind.DRONE_TOKEN_DELTA,
                            run_id=node.get("action_id") or node["id"],
                            payload={"delta": ev.text[:1000]},
                            text="",
                        )
                    )
                elif ev.kind == "error":
                    raise RuntimeError(ev.text or "provider error")
                elif ev.kind == "finish":
                    break
        finally:
            await session.close()
        return "".join(accumulated)

    async def _run_fpv_drone(
        self,
        run_id: str,
        node: dict[str, Any],
        inputs: dict[str, list[str]],
    ) -> str:
        action_id = node.get("action_id")
        if not isinstance(action_id, str) or not action_id:
            raise FlowValidationError(f"fpv drone node {node['id']} has no action_id")
        action = await self.store.get_drone_action(action_id)
        if action is None:
            raise FlowValidationError(f"drone action not found: {action_id}")

        snap = action.blueprint_snapshot or {}
        name = action.name or snap.get("name") or "FPV Drone"
        provider = str(snap.get("provider") or "")
        model = str(snap.get("model") or "")
        tokens = estimate_action_total(action, provider=provider, model=model)
        transcript = action.transcript or []
        last_turn = next(
            (m.get("content", "") for m in reversed(transcript) if m.get("role") == "assistant"),
            "",
        )
        bundle_lines = [
            f"Source bundle: {name}",
            f"Provider: {provider} / {model}",
            f"Turns: {len(transcript)}",
            f"Token estimate: ~{tokens}",
        ]
        if action.workspace_id:
            bundle_lines.append("Workspace-bound: yes")
        if inputs:
            bundle_lines.append("Upstream context:")
            bundle_lines.append(self._inputs_to_text(inputs))
        if last_turn:
            bundle_lines.append("Latest assistant turn:")
            bundle_lines.append(last_turn)
        return "\n".join(bundle_lines).strip()

    async def _run_staging_area(
        self,
        run_id: str,
        node: dict[str, Any],
        inputs: dict[str, list[str]],
        card_cache: dict[str, PersonalityCard],
    ) -> dict[str, str]:
        params = node.get("params") or {}
        mode = str(params.get("mode") or "wait_for_all")
        summary = self._summarize_staging(node, inputs)
        if mode in {"wait_for_all", "wait_for_any", "threshold"}:
            output = summary or "(empty upstream output)"
            return {"status": "released", "output": output, "reason": f"mode={mode}"}

        if mode == "manual_release":
            timeout_seconds = params.get("timeout_seconds")
            future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
            self._human_waiters[(run_id, node["id"])] = future
            await self._emit(
                run_id,
                "flow.node.waiting",
                payload={
                    "node_id": node["id"],
                    "preview": summary[:1000],
                    "reason": "manual release requested",
                },
            )
            try:
                approved = await asyncio.wait_for(
                    future,
                    timeout=float(timeout_seconds) if timeout_seconds is not None else None,
                )
            except asyncio.TimeoutError:
                return {
                    "status": "timed_out",
                    "output": summary,
                    "reason": "manual release timed out",
                }
            finally:
                self._human_waiters.pop((run_id, node["id"]), None)
            if not approved:
                return {
                    "status": "rejected",
                    "output": summary,
                    "reason": "manual release rejected",
                }
            return {"status": "released", "output": summary, "reason": "manual release approved"}

        if mode == "agent_decision":
            decision_text = await self._staging_agent_summary(run_id, node, inputs, card_cache)
            return {
                "status": "released",
                "output": decision_text or summary,
                "reason": "agent decision summary",
            }

        if mode == "budget_gate":
            budget = params.get("budget_limit_usd")
            estimate = params.get("estimated_cost_usd")
            if budget is not None and estimate is not None and float(estimate) > float(budget):
                return {
                    "status": "blocked",
                    "output": summary,
                    "reason": f"budget gate blocked: estimate {estimate} > budget {budget}",
                }
            return {"status": "released", "output": summary, "reason": "budget gate released"}

        if mode == "quality_gate":
            threshold = params.get("quality_threshold")
            observed = params.get("observed_quality")
            if threshold is not None and observed is not None and float(observed) < float(threshold):
                return {
                    "status": "blocked",
                    "output": summary,
                    "reason": f"quality gate blocked: score {observed} < threshold {threshold}",
                }
            return {"status": "released", "output": summary, "reason": "quality gate released"}

        return {"status": "released", "output": summary, "reason": f"mode={mode}"}

    async def _run_consensus_node(
        self,
        run_id: str,
        node: dict[str, Any],
        inputs: dict[str, list[str]],
        card_cache: dict[str, PersonalityCard],
    ) -> str:
        params = node.get("params") or {}
        candidate_ids = params.get("candidate_card_ids") or []
        if not isinstance(candidate_ids, list) or len(candidate_ids) < 2:
            raise FlowValidationError("consensus node requires at least two candidate_card_ids")
        judge_card_id = params.get("judge_card_id") or node.get("card_id")
        if not isinstance(judge_card_id, str) or not judge_card_id:
            raise FlowValidationError("consensus node requires judge_card_id or node.card_id")
        judge_card = card_cache.get(judge_card_id)
        if judge_card is None:
            raise FlowValidationError(f"judge card not found: {judge_card_id}")

        question = self._flatten_inputs(inputs).strip() or str(params.get("goal") or "").strip()
        if not question:
            raise FlowValidationError(f"consensus node {node['id']} has no question input")

        async def _candidate_run(card_id: str) -> dict[str, Any]:
            card = card_cache.get(card_id)
            if card is None:
                return {"card_id": card_id, "error": "card not found", "text": ""}
            text = ""
            error: str | None = None
            provider = provider_registry.get_provider(card.provider)
            session = await provider.open_chat(card)
            try:
                async for ev in session.send(question):
                    if ev.kind == "text_delta":
                        text += ev.text
                        await self._emit(
                            run_id,
                            "flow.node.token_delta",
                            payload={"node_id": node["id"], "delta": ev.text[:1000]},
                        )
                    elif ev.kind == "error":
                        error = ev.text or "provider error"
                        break
                    elif ev.kind == "finish":
                        break
            finally:
                await session.close()
            return {"card_id": card_id, "provider": card.provider, "model": card.model, "text": text, "error": error}

        candidate_results = await asyncio.gather(*[_candidate_run(cid) for cid in candidate_ids])
        block_lines = []
        for idx, res in enumerate(candidate_results, start=1):
            title = f"Candidate #{idx} ({res.get('provider','?')}/{res.get('model','?')})"
            body = res.get("text") or f"(error: {res.get('error')})"
            block_lines.append(f"## {title}\n{body}")
        judge_prompt = (
            "You are a judge. Compare candidate answers and choose the best one.\n"
            "Return: winner index, short rationale, and final merged answer.\n\n"
            f"Question:\n{question}\n\nCandidates:\n\n" + "\n\n".join(block_lines)
        )
        judge_provider = provider_registry.get_provider(judge_card.provider)
        judge_session = await judge_provider.open_chat(judge_card)
        judged = ""
        try:
            async for ev in judge_session.send(judge_prompt):
                if ev.kind == "text_delta":
                    judged += ev.text
                    await self._emit(
                        run_id,
                        "flow.node.token_delta",
                        payload={"node_id": node["id"], "delta": ev.text[:1000]},
                    )
                elif ev.kind == "error":
                    raise RuntimeError(ev.text or "judge provider error")
                elif ev.kind == "finish":
                    break
        finally:
            await judge_session.close()

        return json.dumps(
            {
                "question": question,
                "candidates": candidate_results,
                "judge": {
                    "card_id": judge_card.id,
                    "provider": judge_card.provider,
                    "model": judge_card.model,
                    "output": judged,
                },
            },
            ensure_ascii=True,
        )

    @staticmethod
    def _run_branch(
        node: dict[str, Any],
        inputs: Any,
    ) -> tuple[str, bool]:
        text = FlowExecutor._flatten_inputs(inputs)
        params = node.get("params") or {}
        pattern = params.get("pattern", ".*")
        matched = bool(re.search(pattern, text))
        return text, matched

    @staticmethod
    def _run_merge(inputs: Any) -> str:
        return FlowExecutor._flatten_inputs(inputs, separator="\n\n---\n\n")

    async def _wait_human(self, run_id: str, node_id: str, inputs: dict[str, list[str]]) -> bool:
        future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._human_waiters[(run_id, node_id)] = future
        await self._emit(
            run_id,
            "flow.node.human_pending",
            payload={
                "node_id": node_id,
                "preview": FlowExecutor._flatten_inputs(inputs)[:1000],
            },
        )
        try:
            return await future
        finally:
            self._human_waiters.pop((run_id, node_id), None)

    @staticmethod
    def _flatten_inputs(inputs: Any, separator: str = "\n\n") -> str:
        """Utility to join all inputs into a single string."""
        if isinstance(inputs, list):
            return separator.join(str(v) for v in inputs)
        if not isinstance(inputs, dict):
            return str(inputs or "")
        all_vals = []
        for vals in inputs.values():
            all_vals.extend(vals)
        return separator.join(all_vals)

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    _KIND_MAP = {
        "flow.node.queued": EventKind.FLOW_NODE_QUEUED,
        "flow.node.started": EventKind.FLOW_NODE_STARTED,
        "flow.node.token_delta": EventKind.FLOW_NODE_TOKEN_DELTA,
        "flow.node.waiting": EventKind.FLOW_NODE_WAITING,
        "flow.node.released": EventKind.FLOW_NODE_RELEASED,
        "flow.node.timed_out": EventKind.FLOW_NODE_TIMED_OUT,
        "flow.node.rejected": EventKind.FLOW_NODE_REJECTED,
        "flow.node.blocked": EventKind.FLOW_NODE_BLOCKED,
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
