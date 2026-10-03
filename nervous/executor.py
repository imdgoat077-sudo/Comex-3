"""
Comex Browser Nervous System — Stage C: Selector-to-Physical-Click Executor.

Translates an authorized TargetBinding into physical CDP mouse events
via the bound backend_node_id, with multi-stage pre/post-scroll re-validation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Protocol

from .target_binding import BindingFailureReason, TargetBinding, validate_binding_integrity
from .world_state import BrowserState

logger = logging.getLogger("comex.nervous.executor")


class CDPConnection(Protocol):
    """Protocol for sending asynchronous CDP commands."""
    async def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...


class SelectorClickExecutor:
    """
    Authoritative click executor.
    Acts exclusively on the bound backend_node_id from TargetBinding.
    Never re-queries the selector.
    """

    def __init__(
        self,
        cdp: CDPConnection,
        state: BrowserState,
        tab_id: str,
        target_id: str,
        visual_cursor_hook: Optional[Callable[[float, float, str, str], None]] = None,
    ) -> None:
        self._cdp = cdp
        self._state = state
        self._tab_id = tab_id
        self._target_id = target_id
        self._visual_cursor_hook = visual_cursor_hook

    def set_visual_cursor_hook(self, hook: Optional[Callable[[float, float, str, str], None]]) -> None:
        self._visual_cursor_hook = hook

    async def execute(self, action: str, binding: TargetBinding, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute logical click on the bound target.
        Emits mousePressed and mouseReleased as a single logical action.
        """
        if action != "click":
            raise ValueError(f"SelectorClickExecutor only handles 'click', received '{action}'")

        # ── Step 1: Pre-scroll verification ────────────────────────────
        fail_reason = validate_binding_integrity(
            binding, self._state, current_tab_id=self._tab_id, current_target_id=self._target_id
        )
        if fail_reason:
            logger.warning("Pre-scroll validation failed: %s", fail_reason.value)
            raise RuntimeError(f"Pre-scroll validation failed: {fail_reason.value}")

        # ── Step 2: Actuator scroll ────────────────────────────────────
        try:
            await self._cdp.send(
                "DOM.scrollIntoViewIfNeeded",
                {"backendNodeId": binding.backend_node_id},
            )
        except Exception as e:
            logger.warning("DOM.scrollIntoViewIfNeeded failed: %s", e)
            raise RuntimeError(f"Actuator scroll failed: {e}") from e

        # ── Step 3: Post-scroll re-validation ──────────────────────────
        fail_reason = validate_binding_integrity(
            binding, self._state, current_tab_id=self._tab_id, current_target_id=self._target_id
        )
        if fail_reason:
            logger.warning("Post-scroll validation failed: %s", fail_reason.value)
            raise RuntimeError(f"Post-scroll validation failed: {fail_reason.value}")

        # ── Step 4: Box model resolution via bound backendNodeId ───────
        try:
            box_res = await self._cdp.send(
                "DOM.getBoxModel",
                {"backendNodeId": binding.backend_node_id},
            )
        except Exception as e:
            logger.warning("DOM.getBoxModel failed: %s", e)
            raise RuntimeError(f"Box model resolution failed: {e}") from e

        model = box_res.get("model", {})
        border_quad = model.get("border") or model.get("content")
        if not border_quad or len(border_quad) < 8:
            raise RuntimeError(f"Invalid box model returned for backendNodeId {binding.backend_node_id}")

        # Quad points: [x1, y1, x2, y2, x3, y3, x4, y4]
        xs = [border_quad[0], border_quad[2], border_quad[4], border_quad[6]]
        ys = [border_quad[1], border_quad[3], border_quad[5], border_quad[7]]
        target_x = sum(xs) / 4.0
        target_y = sum(ys) / 4.0

        # ── Step 5: Immediate pre-input re-validation ──────────────────
        fail_reason = validate_binding_integrity(
            binding, self._state, current_tab_id=self._tab_id, current_target_id=self._target_id
        )
        if fail_reason:
            logger.warning("Pre-input validation failed: %s", fail_reason.value)
            raise RuntimeError(f"Pre-input validation failed: {fail_reason.value}")

        # Calculate Segment B: authoritative validation complete → physical dispatch
        t_dispatch_mono = time.monotonic()
        val_to_dispatch_ns = int(max(0.0, t_dispatch_mono - binding.resolved_at_mono) * 1e9)

        # ── Step 6: Physical mouse dispatch (mousePressed + mouseReleased) ──
        button = kwargs.get("button", "left")
        clicks = kwargs.get("clicks", 1)
        buttons_mask = 1 if button == "left" else (2 if button == "right" else 4)

        if self._visual_cursor_hook:
            try:
                self._visual_cursor_hook(target_x, target_y, button, self._tab_id)
            except Exception:
                pass

        await self._cdp.send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": target_x,
            "y": target_y,
            "button": button,
            "buttons": buttons_mask,
            "clickCount": clicks,
        })
        await self._cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": target_x,
            "y": target_y,
            "button": button,
            "buttons": 0,
            "clickCount": clicks,
        })

        return {
            "clicked": True,
            "x": target_x,
            "y": target_y,
            "backend_node_id": binding.backend_node_id,
            "validation_to_dispatch_ns": val_to_dispatch_ns,
        }
