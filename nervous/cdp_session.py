"""
Comex Browser Nervous System — Stage C: Per-Tab CDP Session & Controlled Mutation Plane.

Encapsulates per-tab WebSocket CDP sessions, ordered event queues with sequence
barriers, explicit DOM object resolution, Method E verification on bound remote
objects, and closed-loop execution through ActuatorGate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

from .actuator_gate import ActuatorGate, GateResult
from .executor import SelectorClickExecutor
from .method_e import METHOD_E_CHECK_FUNC
from .policy_registry import OverlayPolicyMatcher, PolicyRegistry, ReflexActionRouter, SafeOverlayHandler
from .target_binding import BindingFailureReason, TargetBinding
from .world_state import BrowserState, WorldStateTracker

logger = logging.getLogger("comex.nervous.cdp_session")



class TabSessionContext:
    """
    Per-tab session context holding the target's dedicated WorldStateTracker,
    ordered event-processing queue, ActuatorGate, and SelectorClickExecutor.
    """

    def __init__(
        self,
        tab_id: str,
        target_id: str,
        cdp_client: Any,
        main_frame_id: Optional[str] = None,
    ) -> None:
        self.tab_id = tab_id
        self.target_id = target_id
        self.cdp = cdp_client
        self.main_frame_id = main_frame_id

        # Wire notification handler if client supports it
        if hasattr(cdp_client, "set_notification_handler"):
            cdp_client.set_notification_handler(self.on_cdp_notification)

        # Dedicated per-tab nervous system components
        self.tracker = WorldStateTracker(tab_id=tab_id, target_ws_url=getattr(cdp_client, "ws_url", ""))
        self.executor = SelectorClickExecutor(self.cdp, self.tracker.state, tab_id=tab_id, target_id=target_id)
        self.policy_registry = PolicyRegistry()
        self.overlay_handler = SafeOverlayHandler(self.policy_registry, self)
        self.action_router = ReflexActionRouter(overlay_handler=self.overlay_handler)
        self.gate = ActuatorGate(self.tracker.state, self.executor, handler=self.action_router)
        self.matcher = OverlayPolicyMatcher(self.policy_registry, self.cdp)

        # Monotonic event sequence & barrier primitives
        self._rx_seq: int = 0
        self._processed_watermark: int = 0
        self._event_queue: asyncio.Queue[Tuple[int, str, Dict[str, Any]]] = asyncio.Queue()
        self._watermark_cond = asyncio.Condition()
        self._pump_task: Optional[asyncio.Task] = None
        self._running: bool = False

    async def start(self) -> None:
        """Start the sequential event processing loop."""
        if self._running:
            return
        self._running = True
        self._pump_task = asyncio.create_task(self._process_events_loop())

    async def stop(self) -> None:
        """Stop the event processing loop."""
        self._running = False
        if self._pump_task:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass

    def set_visual_cursor_hook(self, hook: Optional[Callable[[float, float, str, str], None]]) -> None:
        """Set or update the non-blocking visual cursor hook on the executor."""
        self.executor.set_visual_cursor_hook(hook)

    def on_cdp_notification(self, method: str, params: Dict[str, Any]) -> int:
        """
        Called synchronously by the WebSocket receive loop upon receiving a CDP event.
        Assigns a monotonically increasing sequence number and enqueues the event.
        Returns the assigned sequence number.
        """
        self._rx_seq += 1
        seq = self._rx_seq
        self._event_queue.put_nowait((seq, method, params))
        return seq

    async def _process_events_loop(self) -> None:
        """Sequential consumer applying CDP events in FIFO order to WorldStateTracker."""
        while self._running:
            try:
                seq, method, params = await self._event_queue.get()
                self._apply_event_to_tracker(method, params)
                async with self._watermark_cond:
                    self._processed_watermark = seq
                    self._watermark_cond.notify_all()
                self._event_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error processing CDP event in TabSessionContext: %s", e)

    def _apply_event_to_tracker(self, method: str, params: Dict[str, Any]) -> None:
        """Route CDP event to WorldStateTracker."""
        if method == "DOM.documentUpdated":
            self.tracker.handle_document_updated()
        elif method == "DOM.childNodeInserted":
            self.tracker.handle_child_node_inserted(
                params.get("parentNodeId", 0),
                params.get("node", {}).get("nodeId", 0),
            )
        elif method == "DOM.childNodeRemoved":
            self.tracker.handle_child_node_removed(
                params.get("parentNodeId", 0),
                params.get("nodeId", 0),
            )
        elif method == "DOM.attributeModified":
            self.tracker.handle_attribute_modified(
                params.get("nodeId", 0),
                params.get("name", ""),
                params.get("value", ""),
            )
        elif method == "DOM.attributeRemoved":
            self.tracker.handle_attribute_removed(
                params.get("nodeId", 0),
                params.get("name", ""),
            )
        elif method == "DOM.characterDataModified":
            self.tracker.handle_character_data_modified(
                params.get("nodeId", 0),
                params.get("characterData", ""),
            )
        elif method == "DOM.setChildNodes":
            self.tracker.handle_set_child_nodes(
                params.get("parentId", 0),
                params.get("nodes", []),
            )
        elif method == "Page.frameNavigated":
            frame = params.get("frame", {})
            is_main = not frame.get("parentId")
            if is_main and frame.get("id"):
                self.main_frame_id = frame.get("id")
            self.tracker.handle_frame_navigated(frame.get("url", ""), is_main_frame=is_main)
        elif method == "Page.loadEventFired":
            self.tracker.handle_load_event_fired()
        elif method == "Page.javascriptDialogOpening":
            self.tracker.handle_javascript_dialog_opening(
                params.get("type", "alert"),
                params.get("message", ""),
                params.get("defaultPrompt", ""),
            )
        elif method == "Page.javascriptDialogClosed":
            self.tracker.handle_javascript_dialog_closed()
        elif method == "Network.requestWillBeSent":
            self.tracker.handle_request_will_be_sent(
                params.get("requestId", ""),
                params.get("type", "Other"),
            )
        elif method == "Network.loadingFinished":
            self.tracker.handle_loading_finished(params.get("requestId", ""))
        elif method == "Network.loadingFailed":
            self.tracker.handle_loading_failed(params.get("requestId", ""))

    async def wait_until_processed(self, sequence_watermark: int, timeout: float = 3.0) -> None:
        """
        Ordered event barrier: Wait until all CDP events received up to sequence_watermark
        have been applied to WorldStateTracker.
        """
        t_deadline = time.monotonic() + timeout
        async with self._watermark_cond:
            while self._processed_watermark < sequence_watermark:
                remaining = t_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for watermark {sequence_watermark} "
                        f"(current: {self._processed_watermark})"
                    )
                try:
                    await asyncio.wait_for(self._watermark_cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise TimeoutError(
                        f"Timed out waiting for watermark {sequence_watermark} "
                        f"(current: {self._processed_watermark})"
                    )

    async def flush_and_sync(self) -> int:
        """
        Send a round-trip probe over CDP and await processing of all preceding events.
        Guarantees that WorldStateTracker reflects all events dispatched prior to the probe.
        """
        # 1. Send round-trip probe
        doc_res = await self.cdp.send("DOM.getDocument", {"depth": 0})
        # 2. Capture the sequence number at this point
        watermark = self._rx_seq
        # 3. Wait until event processing loop drains through watermark
        await self.wait_until_processed(watermark)
        return watermark

    async def evaluate_overlay_state(self) -> None:
        """Evaluate overlay presence and policy match against current DOM state."""
        await self.matcher.evaluate_overlay_state(self.tracker.state)

    async def resolve_and_bind(self, selector: str) -> TargetBinding:
        """
        Resolve the selector via CDP, evaluate Method E against the resolved remote object,
        and construct an immutable TargetBinding.
        """
        # Step 1: Flush event queue so tracker is fully up-to-date
        watermark = await self.flush_and_sync()
        await self.evaluate_overlay_state()

        # Step 2: Resolve selector to DOM nodeId with full descendant tree population
        doc = await self.cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        root = doc.get("root", {})
        root_node_id = root.get("nodeId", 1)
        self.tracker.populate_tree_hierarchy(root)

        q_res = await self.cdp.send("DOM.querySelector", {
            "nodeId": root_node_id,
            "selector": selector,
        })
        node_id = q_res.get("nodeId", 0)
        if not node_id:
            raise RuntimeError(f"Selector '{selector}' not found in document")

        # Step 2b: Ensure target subtree descendants are requested and monitored in CDP DOM agent
        try:
            await self.cdp.send("DOM.requestChildNodes", {"nodeId": node_id, "depth": -1})
        except Exception:
            pass

        # Step 3: Describe node to extract backend_node_id and frame_id
        desc_res = await self.cdp.send("DOM.describeNode", {"nodeId": node_id})
        node_desc = desc_res.get("node", {})
        backend_node_id = node_desc.get("backendNodeId")
        frame_id = node_desc.get("frameId")

        if backend_node_id is None:
            raise RuntimeError(f"Could not extract backendNodeId for selector '{selector}'")

        # Step 4: Main-frame safety assertion (Milestone 1)
        if self.main_frame_id and frame_id and frame_id != self.main_frame_id:
            raise RuntimeError(
                f"Selector '{selector}' resolves inside iframe ({frame_id}). "
                "Cross-frame targets are explicitly out of scope for Milestone 1."
            )

        # Step 5: Resolve remote object to evaluate Method E on the identical node handle
        obj_res = await self.cdp.send("DOM.resolveNode", {"nodeId": node_id})
        remote_obj = obj_res.get("object", {})
        object_id = remote_obj.get("objectId")
        if not object_id:
            raise RuntimeError(f"Could not resolve remote object handle for selector '{selector}'")

        # Step 6: Evaluate frozen Method E on the bound remote object
        call_res = await self.cdp.send("Runtime.callFunctionOn", {
            "objectId": object_id,
            "functionDeclaration": METHOD_E_CHECK_FUNC,
            "returnByValue": True,
        })
        eval_val = call_res.get("result", {}).get("value", {})
        is_actionable = bool(eval_val.get("actionable", False))
        box = eval_val.get("box", {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0})

        # Release the remote object handle
        try:
            await self.cdp.send("Runtime.releaseObject", {"objectId": object_id})
        except Exception:
            pass

        now = time.monotonic()

        # Step 7: Create immutable TargetBinding
        binding = TargetBinding(
            tab_id=self.tab_id,
            target_id=self.target_id,
            session_id=None,
            frame_id=frame_id,
            selector=selector,
            node_id=node_id,
            backend_node_id=backend_node_id,
            document_generation=self.tracker.state.document_generation,
            target_subtree_mutation_generation=self.tracker.state.target_subtree_mutation_generation,
            state_watermark=watermark,
            bounding_box=box,
            resolved_at_mono=now,
        )

        # Step 8: Update WorldStateTracker observed state (is_stable=False requires consecutive observation)
        self.tracker.state.assign_target(
            locator=selector,
            node_id=node_id,
            backend_node_id=backend_node_id,
            is_actionable=is_actionable,
            is_stable=False,
            bounding_box=box,
        )

        # Step 9: Sample consecutive observation to establish initial geometric stability
        await self.update_target_position()

        return binding

    async def update_target_position(self) -> None:
        """
        Sample target position via CDP and update WorldStateTracker to establish
        geometric stability across consecutive observations.
        """
        target = self.tracker.state.current_target
        if not target:
            return
        try:
            if target.backend_node_id is not None:
                box_res = await self.cdp.send(
                    "DOM.getBoxModel",
                    {"backendNodeId": target.backend_node_id},
                )
            elif target.node_id is not None:
                box_res = await self.cdp.send(
                    "DOM.getBoxModel",
                    {"nodeId": target.node_id},
                )
            else:
                return
            model = box_res.get("model", {})
            border_quad = model.get("border") or model.get("content")
            if border_quad and len(border_quad) >= 8:
                xs = [border_quad[0], border_quad[2], border_quad[4], border_quad[6]]
                ys = [border_quad[1], border_quad[3], border_quad[5], border_quad[7]]
                min_x = min(xs)
                min_y = min(ys)
                width = float(model.get("width", max(xs) - min_x))
                height = float(model.get("height", max(ys) - min_y))
                self.tracker.state.update_target_position(min_x, min_y, width, height)
        except Exception as e:
            logger.debug("Failed to sample target position: %s", e)


class WebSocketCDPClient:
    """Async WebSocket CDP client managing request/response routing and notifications."""

    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self._ws: Optional[Any] = None
        self._msg_id: int = 0
        self._pending: Dict[int, asyncio.Future[Dict[str, Any]]] = {}
        self._notification_handler: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._listen_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if self._ws:
            return
        import websockets
        self._ws = await websockets.connect(self.ws_url, max_size=20 * 1024 * 1024)
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def close(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None

    def set_notification_handler(self, handler: Callable[[str, Dict[str, Any]], None]) -> None:
        self._notification_handler = handler

    async def _listen_loop(self) -> None:
        try:
            async for raw in self._ws:
                data = json.loads(raw)
                msg_id = data.get("id")
                if msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        if "error" in data:
                            fut.set_exception(RuntimeError(data["error"].get("message", "CDP Error")))
                        else:
                            fut.set_result(data.get("result", {}))
                elif "method" in data and self._notification_handler:
                    self._notification_handler(data["method"], data.get("params", {}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("CDP listen loop error on %s: %s", self.ws_url, e)

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self._ws:
            await self.connect()
        self._msg_id += 1
        call_id = self._msg_id
        fut: asyncio.Future[Dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[call_id] = fut
        payload = {"id": call_id, "method": method, "params": params or {}}
        await self._ws.send(json.dumps(payload))
        return await fut


_GLOBAL_SESSIONS: Dict[str, TabSessionContext] = {}


async def get_or_create_tab_session(tab_id: str, ws_url: str) -> TabSessionContext:
    """Retrieve or initialize a persistent TabSessionContext for tab_id."""
    session = _GLOBAL_SESSIONS.get(tab_id)
    if session and getattr(session.cdp, "ws_url", "") == ws_url:
        return session

    client = WebSocketCDPClient(ws_url)
    await client.connect()
    # Enable core domains
    await asyncio.gather(
        client.send("Page.enable"),
        client.send("DOM.enable"),
        client.send("Runtime.enable"),
        client.send("Network.enable"),
        client.send("Emulation.setFocusEmulationEnabled", {"enabled": True}),
    )

    new_session = TabSessionContext(tab_id=tab_id, target_id=tab_id, cdp_client=client)
    client.set_notification_handler(new_session.on_cdp_notification)
    await new_session.start()
    _GLOBAL_SESSIONS[tab_id] = new_session
    return new_session

