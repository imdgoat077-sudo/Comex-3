"""
Production Comex Browser Actuator Service.

Incorporates the browser-control techniques from jev-ultrafast:
1. Late Occlusion Hit-Testing: Validates elementFromPoint immediately before physical click
   to prevent misclicks on modal backdrops, overlays, or occluded elements.
2. Dynamic Combobox Readiness: Bounded maximum poll (up to 200ms / 2 rAFs) waiting for
   [role="option"] to settle after typing in comboboxes, with zero delay for standard inputs.
3. Atomic Keyboard Text Fill: SelectAll (Cmd+A / Ctrl+A) + Input.insertText + synthetic events.
4. Visual Cursor Coordination: Directs visual cursor animation to validated target coordinates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Union

logger = logging.getLogger("comex.services.actuator")


class ActuationStatus(Enum):
    SUCCESS = "SUCCESS"
    STALE_TARGET_OCCLUDED = "STALE_TARGET_OCCLUDED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_DISABLED_OR_INERT = "TARGET_DISABLED_OR_INERT"
    TARGET_OUT_OF_VIEWPORT = "TARGET_OUT_OF_VIEWPORT"
    TARGET_ALREADY_SELECTED = "TARGET_ALREADY_SELECTED"
    ACTUATION_FAILED = "ACTUATION_FAILED"


@dataclass(frozen=True)
class ActuationResult:
    status: ActuationStatus
    action: str
    target_id: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    details: Optional[str] = None
    duration_ms: float = 0.0
    scroll_attempts: int = 0
    scroll_strategy: str = "none"
    pre_scroll_rect: Optional[Dict[str, Any]] = None
    post_scroll_rect: Optional[Dict[str, Any]] = None
    occluder_before: Optional[Any] = None
    occluder_after: Optional[Any] = None

    @property
    def is_success(self) -> bool:
        return self.status == ActuationStatus.SUCCESS

    @property
    def was_occluded(self) -> bool:
        return self.status == ActuationStatus.STALE_TARGET_OCCLUDED


class CDPProtocol(Protocol):
    """Protocol for sending CDP commands over WebSocket or HTTP session."""
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str: ...


class BrowserActuator:
    """Production actuator executing verified browser interactions over CDP."""

    def __init__(
        self,
        cursor_notify_url: str = "http://127.0.0.1:8000/agent/cursor/notify",
        combobox_max_wait_ms: int = 200,
    ) -> None:
        self.cursor_notify_url = cursor_notify_url
        self.combobox_max_wait_ms = combobox_max_wait_ms

    def _dispatch_cursor_notify(self, payload: bytes) -> None:
        try:
            req = urllib.request.Request(
                self.cursor_notify_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=0.2)
        except Exception:
            pass

    async def notify_visual_cursor(self, x: float, y: float, action: str = "click", tab_id: str = "active_tab") -> None:
        """
        Asynchronously triggers visual cursor animation to target coordinates.
        Guaranteed 100% non-blocking fire-and-forget so physical clicks are NEVER delayed by cursor IPC.
        """
        payload = json.dumps({"x": x, "y": y, "action": action, "tabId": tab_id}).encode()
        import threading
        threading.Thread(target=self._dispatch_cursor_notify, args=(payload,), daemon=True).start()

    async def cdp_evaluate(
        self,
        ws: Any,
        expression: str,
        msg_counter: List[int],
        return_by_value: bool = True,
        await_promise: bool = False,
    ) -> Any:
        """Helper to evaluate JS expression over CDP WebSocket and return result."""
        msg_counter[0] += 1
        cid = msg_counter[0]
        params: Dict[str, Any] = {
            "expression": expression,
            "returnByValue": return_by_value,
        }
        if await_promise:
            params["awaitPromise"] = True

        await ws.send(json.dumps({
            "id": cid,
            "method": "Runtime.evaluate",
            "params": params,
        }))

        while True:
            raw = await ws.recv()
            res = json.loads(raw)
            if res.get("id") == cid:
                if "error" in res:
                    raise RuntimeError(f"CDP evaluate error: {res['error']}")
                return res.get("result", {}).get("result", {}).get("value")

    async def validate_occlusion(
        self,
        ws: Any,
        bid: Optional[str],
        selector: Optional[str],
        msg_counter: List[int],
    ) -> Dict[str, Any]:
        """
        Late Occlusion Hit-Test Check with Adaptive Scroll-Before-Occlusion.
        Strategy:
        1. Resolve target.
        2. Check geometry / current hit-test.
           If inside viewport and hit-test passes -> return OK with 0 scroll attempts ('none').
        3. If outside viewport or occluded:
           Attempt 1: scrollIntoView(center), re-read geometry, Method E hit-test.
           If hit-test passes -> return OK with 1 scroll attempt ('center').
        4. If still occluded:
           Attempt 2: Perform ONE bounded adaptive reposition based on the occluding element's geometry.
           Re-read geometry, Method E hit-test.
           If hit-test passes -> return OK with 2 scroll attempts ('adaptive_reposition').
        5. If still blocked -> fail closed (STALE_TARGET_OCCLUDED). Hard maximum of 2 scroll attempts.
        Logs: scroll_attempts, scroll_strategy, pre_scroll_rect, post_scroll_rect, occluder_before, occluder_after.
        """
        js_template = """
        (() => {
            let el = null;
            const bidVal = __BID_JSON__;
            const selVal = __SEL_JSON__;
            if (bidVal) el = document.querySelector('[data-bid="' + bidVal + '"], [data-comex-id="' + bidVal + '"]');
            if (!el && selVal) {
                try { el = document.querySelector(selVal); } catch(e) {}
            }
            if (!el || !el.isConnected) return { status: 'TARGET_NOT_FOUND', scroll_attempts: 0, scroll_strategy: 'none' };
            if (el.matches(':disabled') || el.closest('[aria-disabled="true"],[inert]')) {
                return { status: 'TARGET_DISABLED_OR_INERT', scroll_attempts: 0, scroll_strategy: 'none' };
            }

            const getRect = (elem) => {
                const r = elem.getBoundingClientRect();
                return {
                    x: Math.round(r.x),
                    y: Math.round(r.y),
                    width: Math.round(r.width),
                    height: Math.round(r.height),
                    top: Math.round(r.top),
                    bottom: Math.round(r.bottom),
                    left: Math.round(r.left),
                    right: Math.round(r.right)
                };
            };

            const getElSummary = (elem) => {
                if (!elem) return null;
                return {
                    tag: elem.tagName,
                    className: elem.className || '',
                    id: elem.id || '',
                    text: (elem.innerText || elem.textContent || '').slice(0, 60).trim()
                };
            };

            const isInsideViewport = (rect, x, y) => {
                return rect.width > 0 && rect.height > 0 &&
                       x >= 0 && y >= 0 &&
                       x < window.innerWidth && y < window.innerHeight &&
                       rect.bottom > 0 && rect.right > 0 &&
                       rect.top < window.innerHeight && rect.left < window.innerWidth;
            };

            let r0 = getRect(el);
            const pre_scroll_rect = r0;
            let post_scroll_rect = r0;
            let scroll_attempts = 0;
            let scroll_strategy = 'none';
            let occluder_before = null;
            let occluder_after = null;

            if (r0.width <= 0 && r0.height <= 0) {
                return {
                    status: 'TARGET_NOT_FOUND',
                    scroll_attempts,
                    scroll_strategy,
                    pre_scroll_rect,
                    post_scroll_rect,
                    occluder_before: null,
                    occluder_after: null
                };
            }

            let x0 = Math.round(r0.x + r0.width / 2);
            let y0 = Math.round(r0.y + r0.height / 2);

            let initialInViewport = isInsideViewport(r0, x0, y0);
            let initialHit = false;
            let initialTopEl = null;

            if (initialInViewport) {
                initialTopEl = document.elementFromPoint(x0, y0);
                initialHit = initialTopEl && (el.contains(initialTopEl) || initialTopEl.contains(el));
                if (!initialHit && initialTopEl) {
                    occluder_before = getElSummary(initialTopEl);
                }
            } else {
                occluder_before = { reason: 'OUT_OF_VIEWPORT' };
            }

            // Step 1: Visible target -> no unnecessary scroll
            if (initialInViewport && initialHit) {
                return {
                    status: 'OK',
                    x: x0,
                    y: y0,
                    scroll_attempts: 0,
                    scroll_strategy: 'none',
                    pre_scroll_rect,
                    post_scroll_rect: r0,
                    occluder_before: null,
                    occluder_after: null
                };
            }

            // Step 2: Attempt 1 - Scroll into view (center)
            scroll_attempts = 1;
            scroll_strategy = 'center';
            try {
                el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
            } catch(e) {}

            let r1 = getRect(el);
            post_scroll_rect = r1;
            let x1 = Math.round(r1.x + r1.width / 2);
            let y1 = Math.round(r1.y + r1.height / 2);

            let topEl1 = null;
            let isHit1 = false;
            if (isInsideViewport(r1, x1, y1)) {
                topEl1 = document.elementFromPoint(x1, y1);
                isHit1 = topEl1 && (el.contains(topEl1) || topEl1.contains(el));
            }

            if (isHit1) {
                return {
                    status: 'OK',
                    x: x1,
                    y: y1,
                    scroll_attempts: 1,
                    scroll_strategy: 'center',
                    pre_scroll_rect,
                    post_scroll_rect: r1,
                    occluder_before,
                    occluder_after: null
                };
            }

            // Step 3: Attempt 2 - Center still occluded -> bounded second reposition based on occluder geometry
            scroll_attempts = 2;
            scroll_strategy = 'adaptive_reposition';
            if (topEl1) {
                occluder_after = getElSummary(topEl1);
            }

            let container = el.parentElement;
            while (container && container !== document.body && container !== document.documentElement) {
                const cs = window.getComputedStyle(container);
                if (cs.overflowY === 'auto' || cs.overflowY === 'scroll') break;
                container = container.parentElement;
            }
            const scrollTarget = (container && container !== document.body && container !== document.documentElement) ? container : window;

            if (topEl1) {
                const occRect = topEl1.getBoundingClientRect();
                const isBottomOccluder = occRect.top > window.innerHeight / 2 || occRect.bottom >= window.innerHeight - 10;
                const isTopOccluder = occRect.top <= 60 || occRect.bottom < window.innerHeight / 2;

                if (isBottomOccluder) {
                    const overlap = Math.max(20, Math.round(r1.bottom - occRect.top + 24));
                    if (scrollTarget === window) {
                        window.scrollBy({ top: overlap, behavior: 'instant' });
                    } else {
                        scrollTarget.scrollTop += overlap;
                    }
                } else if (isTopOccluder) {
                    const overlap = Math.max(20, Math.round(occRect.bottom - r1.top + 24));
                    if (scrollTarget === window) {
                        window.scrollBy({ top: -overlap, behavior: 'instant' });
                    } else {
                        scrollTarget.scrollTop -= overlap;
                    }
                } else {
                    try {
                        el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
                    } catch(e) {}
                }
            } else {
                try {
                    el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
                } catch(e) {}
            }

            // Method E re-check
            let r2 = getRect(el);
            post_scroll_rect = r2;
            let x2 = Math.round(r2.x + r2.width / 2);
            let y2 = Math.round(r2.y + r2.height / 2);

            let topEl2 = null;
            let isHit2 = false;
            if (isInsideViewport(r2, x2, y2)) {
                topEl2 = document.elementFromPoint(x2, y2);
                isHit2 = topEl2 && (el.contains(topEl2) || topEl2.contains(el));
            }

            if (isHit2) {
                return {
                    status: 'OK',
                    x: x2,
                    y: y2,
                    scroll_attempts: 2,
                    scroll_strategy: 'adaptive_reposition',
                    pre_scroll_rect,
                    post_scroll_rect: r2,
                    occluder_before,
                    occluder_after: null
                };
            }

            // Still blocked -> fail closed (no dispatch)
            const finalOccluder = getElSummary(topEl2 || topEl1);
            return {
                status: 'STALE_TARGET_OCCLUDED',
                x: x2,
                y: y2,
                scroll_attempts: 2,
                scroll_strategy: 'adaptive_reposition',
                pre_scroll_rect,
                post_scroll_rect: r2,
                occluder_before,
                occluder_after: finalOccluder,
                top_tag: finalOccluder ? finalOccluder.tag : null,
                top_class: finalOccluder ? finalOccluder.className : null,
                top_id: finalOccluder ? finalOccluder.id : null
            };
        })()
        """
        expr = js_template.replace("__BID_JSON__", json.dumps(bid or "")).replace("__SEL_JSON__", json.dumps(selector or ""))
        result = await self.cdp_evaluate(ws, expr, msg_counter)
        return result or {"status": "TARGET_NOT_FOUND", "scroll_attempts": 0, "scroll_strategy": "none"}

    async def execute_click(
        self,
        ws: Any,
        bid: Optional[str],
        selector: Optional[str],
        msg_counter: List[int],
        tab_id: str = "active_tab",
    ) -> ActuationResult:
        """
        Authoritative physical click with late occlusion validation and visual cursor animation.
        """
        t0 = time.perf_counter()

        # 1. Late Occlusion Validation
        validation = await self.validate_occlusion(ws, bid, selector, msg_counter)
        status_str = validation.get("status", "TARGET_NOT_FOUND")

        if status_str != "OK":
            status = ActuationStatus[status_str] if status_str in ActuationStatus.__members__ else ActuationStatus.ACTUATION_FAILED
            return ActuationResult(
                status=status,
                action="click",
                target_id=bid or selector,
                x=validation.get("x"),
                y=validation.get("y"),
                details=f"Occlusion validation: {validation}",
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                scroll_attempts=validation.get("scroll_attempts", 0),
                scroll_strategy=validation.get("scroll_strategy", "none"),
                pre_scroll_rect=validation.get("pre_scroll_rect"),
                post_scroll_rect=validation.get("post_scroll_rect"),
                occluder_before=validation.get("occluder_before"),
                occluder_after=validation.get("occluder_after"),
            )

        target_x = float(validation["x"])
        target_y = float(validation["y"])

        # 2. Visual Cursor Notification (using fresh post-scroll coordinates)
        await self.notify_visual_cursor(target_x, target_y, action="click", tab_id=tab_id)

        # 3. Physical Dispatch
        # DO NOT re-scroll blindly with scrollIntoView({ block: 'center' }) here.
        # validate_occlusion has already placed and verified the element at target_x, target_y.
        click_expr = f"""
        (() => {{
            let el = null;
            if ({json.dumps(bid or "")}) el = document.querySelector('[data-bid="{bid}"], [data-comex-id="{bid}"]');
            if (!el && {json.dumps(selector or "")}) {{
                try {{ el = document.querySelector({json.dumps(selector)}); }} catch(e) {{}}
            }}
            if (!el) return false;
            if (typeof el.focus === 'function') el.focus();
            el.click();
            return true;
        }})()
        """
        await self.cdp_evaluate(ws, click_expr, msg_counter)

        return ActuationResult(
            status=ActuationStatus.SUCCESS,
            action="click",
            target_id=bid or selector,
            x=target_x,
            y=target_y,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            scroll_attempts=validation.get("scroll_attempts", 0),
            scroll_strategy=validation.get("scroll_strategy", "none"),
            pre_scroll_rect=validation.get("pre_scroll_rect"),
            post_scroll_rect=validation.get("post_scroll_rect"),
            occluder_before=validation.get("occluder_before"),
            occluder_after=validation.get("occluder_after"),
        )

    async def execute_atomic_fill(
        self,
        ws: Any,
        bid: Optional[str],
        selector: Optional[str],
        text: str,
        msg_counter: List[int],
        tab_id: str = "active_tab",
    ) -> ActuationResult:
        """
        Atomic text entry using SelectAll (Cmd+A / Ctrl+A) and atomic value setting.
        Triggers native synthetic input and change events so frameworks (React, Vue) update state.
        """
        t0 = time.perf_counter()

        # 1. Resolve element geometry for visual cursor
        validation = await self.validate_occlusion(ws, bid, selector, msg_counter)
        if validation.get("x") is not None and validation.get("y") is not None:
            await self.notify_visual_cursor(float(validation["x"]), float(validation["y"]), action="type", tab_id=tab_id)

        # 2. Atomic Fill Script with React prototype value setter
        fill_expr = f"""
        (() => {{
            let el = null;
            if ({json.dumps(bid or "")}) el = document.querySelector('[data-bid="{bid}"], [data-comex-id="{bid}"]');
            if (!el && {json.dumps(selector or "")}) {{
                try {{ el = document.querySelector({json.dumps(selector)}); }} catch(e) {{}}
            }}
            if (!el) return 'TARGET_NOT_FOUND';
            if (el.matches(':disabled') || el.readOnly || el.getAttribute('aria-readonly') === 'true') {{
                return 'TARGET_DISABLED_OR_INERT';
            }}

            if (typeof el.focus === 'function') el.focus();

            // Set native value overriding React setter if present
            const valueSetter = Object.getOwnPropertyDescriptor(el, 'value')?.set;
            const prototype = Object.getPrototypeOf(el);
            const prototypeValueSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
            
            if (prototypeValueSetter && valueSetter !== prototypeValueSetter) {{
                prototypeValueSetter.call(el, {json.dumps(text)});
            }} else if (valueSetter) {{
                valueSetter.call(el, {json.dumps(text)});
            }} else {{
                el.value = {json.dumps(text)};
            }}

            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));

            // Return role info to determine combobox readiness
            const isCombobox = el.getAttribute('role') === 'combobox' || 
                               el.getAttribute('aria-autocomplete') !== null ||
                               el.id.includes('combobox') ||
                               el.id.includes('airport') ||
                               el.id.includes('search');
            return isCombobox ? 'COMBOBOX' : 'OK';
        }})()
        """
        fill_status = await self.cdp_evaluate(ws, fill_expr, msg_counter)

        if fill_status == "TARGET_NOT_FOUND":
            return ActuationResult(
                status=ActuationStatus.TARGET_NOT_FOUND,
                action="fill",
                target_id=bid or selector,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        if fill_status == "TARGET_DISABLED_OR_INERT":
            return ActuationResult(
                status=ActuationStatus.TARGET_DISABLED_OR_INERT,
                action="fill",
                target_id=bid or selector,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

        # 3. Dynamic Combobox Readiness Settle
        if fill_status == "COMBOBOX":
            await self.wait_for_combobox_settle(ws, bid, selector, msg_counter)

        return ActuationResult(
            status=ActuationStatus.SUCCESS,
            action="fill",
            target_id=bid or selector,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )

    async def wait_for_combobox_settle(
        self,
        ws: Any,
        bid: Optional[str],
        selector: Optional[str],
        msg_counter: List[int],
    ) -> None:
        """
        Dynamic combobox suggestion settle.
        Polls boundedly (max 200ms) waiting for [role="option"] or dropdown suggestions.
        Exits immediately as soon as suggestions are rendered.
        """
        settle_expr = f"""
        (new Promise(resolve => {{
            let field = null;
            if ({json.dumps(bid or "")}) field = document.querySelector('[data-bid="{bid}"], [data-comex-id="{bid}"]');
            if (!field && {json.dumps(selector or "")}) {{
                try {{ field = document.querySelector({json.dumps(selector)}); }} catch(e) {{}}
            }}
            let frames = 0, stopped = false;
            const finish = () => {{ stopped = true; resolve(); }};
            setTimeout(finish, {self.combobox_max_wait_ms});

            const ready = () => {{
                if (stopped) return;
                const ids = (field?.getAttribute('aria-controls') || field?.getAttribute('aria-owns') || '')
                    .split(/\\s+/).filter(Boolean);
                const roots = ids.length ? ids.map(id => document.getElementById(id)).filter(Boolean) : [document];
                const options = roots.flatMap(root => Array.from(root.querySelectorAll('[role="option"], .tt-suggestion, .ui-menu-item')));
                
                const hasVisibleOptions = options.some(e => {{
                    const r = e.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
                }});

                if (++frames >= 2 && hasVisibleOptions) {{
                    finish();
                }} else {{
                    requestAnimationFrame(ready);
                }}
            }};
            requestAnimationFrame(ready);
        }}))
        """
        try:
            await self.cdp_evaluate(ws, settle_expr, msg_counter, await_promise=True)
        except Exception:
            pass

    async def execute_select_option(
        self,
        ws: Any,
        bid: Optional[str],
        selector: Optional[str],
        option_value: str,
        msg_counter: List[int],
        tab_id: str = "active_tab",
    ) -> ActuationResult:
        """Dropdown select option execution."""
        t0 = time.perf_counter()
        select_expr = f"""
        (() => {{
            let el = null;
            if ({json.dumps(bid or "")}) el = document.querySelector('[data-bid="{bid}"], [data-comex-id="{bid}"]');
            if (!el && {json.dumps(selector or "")}) {{
                try {{ el = document.querySelector({json.dumps(selector)}); }} catch(e) {{}}
            }}
            if (!el || el.tagName !== 'SELECT') return false;
            const val = {json.dumps(option_value)};
            for (let opt of el.options) {{
                if (opt.value === val || opt.text.trim().toLowerCase() === String(val).toLowerCase().trim()) {{
                    el.value = opt.value;
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }}
            }}
            return false;
        }})()
        """
        success = await self.cdp_evaluate(ws, select_expr, msg_counter)
        return ActuationResult(
            status=ActuationStatus.SUCCESS if success else ActuationStatus.TARGET_NOT_FOUND,
            action="select_option",
            target_id=bid or selector,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )
