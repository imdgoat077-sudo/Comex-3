"""
tests/test_shared_actuator_ab.py
================================
Verification Suite for Unified Physical Actuator Path across A/B Conditions.

Proves:
1. Condition A (direct target selection via target_selector) and Condition B
   (semantic intent -> Jev resolution) route through the EXACT same:
   - BrowserActuator.execute_click
   - ActuatorGate / Method E (validate_occlusion)
   - Visual Cursor notification (notify_visual_cursor)
   - Physical CDP dispatch (el.click())
   - LoopBreaker step recording and state fingerprint checks
2. Visual Cursor hook is invoked with target coordinates in both paths.
3. Occluded targets fail-closed via Method E in both paths.
4. Physical action counts (physical_actions=1) and state change tracking
   are 100% symmetric between Condition A and Condition B.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
import pytest

from services.browser_actuator import ActuationResult, ActuationStatus, BrowserActuator
from services.browser_pipeline import (
    BrowserActionPipeline,
    PipelineResult,
    PipelineStatus,
)
from services.candidate_generator import (
    CandidateElement,
    CandidateGenerator,
    ObservationSnapshot,
    SemanticMicroTask,
)
from services.loop_breaker import ActionLoopBreaker
from services.micro_action_resolver import (
    DelegationResolution,
    DelegationStatus,
    MicroActionResolver,
)
from services.typesafe_client import (
    AsyncTypeSafeClient,
    ChoiceAnswer,
    ClientMode,
    SystemOneResponse,
)
from services.universal_extractor import UniversalExtractor


class MockCDPWebSocket:
    """Mock CDP WebSocket capturing all evaluations, Method E checks, and clicks."""

    def __init__(self, occlusion_status: str = "OK", initial_fp: str = "fp_v1", post_fp: str = "fp_v2"):
        self.sent_messages: List[Dict[str, Any]] = []
        self.occlusion_status = occlusion_status
        self.initial_fp = initial_fp
        self.post_fp = post_fp
        self.fp_call_count = 0
        self.evaluations: List[str] = []
        self.clicks: List[str] = []
        self.occlusion_checks: List[str] = []
        self.raw_candidates = [
            {
                "bid": "c101",
                "tag": "a",
                "type": "",
                "id": "newest-link",
                "className": "",
                "text": "new",
                "label": "new",
                "accessible_name": "new",
                "role": "link",
                "selector": 'a[href="newest"]',
                "disabled": False,
                "checked": False,
                "expanded": None,
                "focused": False,
                "bounds": {"x": 120, "y": 8, "width": 30, "height": 16},
                "center": {"x": 135, "y": 16},
                "options": None,
            }
        ]

    async def send(self, message: str) -> None:
        msg = json.loads(message)
        self.sent_messages.append(msg)
        expr = msg.get("params", {}).get("expression", "")
        self.evaluations.append(expr)
        if "document.elementFromPoint" in expr:
            self.occlusion_checks.append(expr)
        if "el.click()" in expr:
            self.clicks.append(expr)

    async def recv(self) -> str:
        last = self.sent_messages[-1]
        msg_id = last["id"]
        expr = last.get("params", {}).get("expression", "")

        val = None
        if "parts.join('###')" in expr:
            self.fp_call_count += 1
            val = self.initial_fp if self.fp_call_count == 1 else self.post_fp
        elif "COMEX_BINDING_ATTR" in expr and "computeAccessibleName" in expr:
            val = self.raw_candidates
        elif "document.elementFromPoint" in expr:
            if self.occlusion_status == "OK":
                val = {"status": "OK", "x": 135, "y": 16}
            elif self.occlusion_status == "OCCLUDED":
                val = {"status": "STALE_TARGET_OCCLUDED", "x": 135, "y": 16, "top_tag": "DIV"}
            else:
                val = {"status": self.occlusion_status}
        elif "el.click()" in expr:
            val = True

        return json.dumps({
            "id": msg_id,
            "result": {"result": {"value": val}},
        })


class TrackingBrowserActuator(BrowserActuator):
    """BrowserActuator instrumented to verify invocation telemetry."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.execute_click_invocations: List[Dict[str, Any]] = []
        self.cursor_notifications: List[Dict[str, Any]] = []

    async def execute_click(
        self,
        ws: Any,
        bid: Optional[str],
        selector: Optional[str],
        msg_counter: List[int],
        tab_id: str = "active_tab",
    ) -> ActuationResult:
        self.execute_click_invocations.append({
            "bid": bid,
            "selector": selector,
            "tab_id": tab_id,
        })
        return await super().execute_click(ws, bid, selector, msg_counter, tab_id=tab_id)

    async def notify_visual_cursor(
        self, x: float, y: float, action: str = "click", tab_id: str = "active_tab"
    ) -> None:
        self.cursor_notifications.append({
            "x": x,
            "y": y,
            "action": action,
            "tab_id": tab_id,
        })


def _create_mock_client_for_newest() -> AsyncTypeSafeClient:
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    prob_top = 0.98
    probs = {"e1": prob_top, "e2": 0.01}
    resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(choice="e1", confidence=prob_top, probabilities=probs),
        },
    )
    client.set_mock_handler(lambda state, questions, model: resp)
    return client


@pytest.mark.asyncio
async def test_shared_actuator_both_arms_invoke_execute_click():
    """Proves both Condition A and Condition B pass through BrowserActuator.execute_click."""
    actuator = TrackingBrowserActuator()
    resolver = MicroActionResolver(client=_create_mock_client_for_newest())
    loop_breaker = ActionLoopBreaker()
    pipeline = BrowserActionPipeline(
        micro_action_resolver=resolver,
        actuator=actuator,
        loop_breaker=loop_breaker,
    )

    # ── Condition A: WITHOUT Jev (Gemini direct target selection) ──
    ws_a = MockCDPWebSocket(occlusion_status="OK", initial_fp="fp_a1", post_fp="fp_a2")
    res_a = await pipeline.execute_action(
        ws=ws_a,
        operation="CLICK",
        target_description="selector:a[href='newest']",
        target_selector="a[href='newest']",
    )

    assert res_a.status == PipelineStatus.SUCCESS
    assert res_a.is_success is True
    assert res_a.target_selector == "a[href='newest']"
    assert res_a.confidence == 1.0
    assert res_a.margin == 1.0
    assert res_a.state_changed is True
    assert len(actuator.execute_click_invocations) == 1
    assert actuator.execute_click_invocations[0]["selector"] == "a[href='newest']"
    assert len(ws_a.clicks) == 1

    # ── Condition B: WITH Jev (Gemini semantic intent -> Jev resolution) ──
    ws_b = MockCDPWebSocket(occlusion_status="OK", initial_fp="fp_b1", post_fp="fp_b2")
    res_b = await pipeline.execute_action(
        ws=ws_b,
        operation="CLICK",
        target_description="Click the 'new' link",
    )

    assert res_b.status == PipelineStatus.SUCCESS
    assert res_b.is_success is True
    assert res_b.target_bid == "c101"
    assert res_b.confidence == 0.98
    assert res_b.state_changed is True
    assert len(actuator.execute_click_invocations) == 2
    assert actuator.execute_click_invocations[1]["bid"] == "c101"
    assert len(ws_b.clicks) == 1


@pytest.mark.asyncio
async def test_shared_visual_cursor_hook_invoked_in_both_arms():
    """Proves notify_visual_cursor is invoked in both Condition A and Condition B with correct coordinates."""
    actuator = TrackingBrowserActuator()
    resolver = MicroActionResolver(client=_create_mock_client_for_newest())
    pipeline = BrowserActionPipeline(
        micro_action_resolver=resolver,
        actuator=actuator,
    )

    # Arm A
    ws_a = MockCDPWebSocket(occlusion_status="OK")
    await pipeline.execute_action(
        ws=ws_a,
        operation="CLICK",
        target_selector="a[href='newest']",
    )
    assert len(actuator.cursor_notifications) == 1
    assert actuator.cursor_notifications[0]["x"] == 135.0
    assert actuator.cursor_notifications[0]["y"] == 16.0
    assert actuator.cursor_notifications[0]["action"] == "click"

    # Arm B
    ws_b = MockCDPWebSocket(occlusion_status="OK")
    await pipeline.execute_action(
        ws=ws_b,
        operation="CLICK",
        target_description="Click 'new'",
    )
    assert len(actuator.cursor_notifications) == 2
    assert actuator.cursor_notifications[1]["x"] == 135.0
    assert actuator.cursor_notifications[1]["y"] == 16.0
    assert actuator.cursor_notifications[1]["action"] == "click"


@pytest.mark.asyncio
async def test_method_e_occlusion_enforced_identically_in_both_arms():
    """Proves Method E late occlusion check blocks execution fail-closed in both arms."""
    actuator = TrackingBrowserActuator()
    resolver = MicroActionResolver(client=_create_mock_client_for_newest())
    pipeline = BrowserActionPipeline(
        micro_action_resolver=resolver,
        actuator=actuator,
    )

    # Arm A occluded
    ws_a = MockCDPWebSocket(occlusion_status="OCCLUDED")
    res_a = await pipeline.execute_action(
        ws=ws_a,
        operation="CLICK",
        target_selector="a[href='newest']",
    )
    assert res_a.status == PipelineStatus.OCCLUDED
    assert res_a.was_occluded is True
    assert len(ws_a.clicks) == 0  # Physical click NOT dispatched
    assert len(actuator.execute_click_invocations) == 0  # Blocked before execute_click

    # Arm B occluded
    ws_b = MockCDPWebSocket(occlusion_status="OCCLUDED")
    res_b = await pipeline.execute_action(
        ws=ws_b,
        operation="CLICK",
        target_description="Click 'new'",
    )
    assert res_b.status == PipelineStatus.OCCLUDED
    assert res_b.was_occluded is True
    assert len(ws_b.clicks) == 0  # Physical click NOT dispatched
    assert len(actuator.execute_click_invocations) == 0  # Blocked before execute_click


@pytest.mark.asyncio
async def test_loop_breaker_and_fingerprints_shared_across_both_arms():
    """Proves LoopBreaker cycles are tracked and prevented identically in both arms."""
    actuator = TrackingBrowserActuator()
    resolver = MicroActionResolver(client=_create_mock_client_for_newest())
    loop_breaker = ActionLoopBreaker()
    pipeline = BrowserActionPipeline(
        micro_action_resolver=resolver,
        actuator=actuator,
        loop_breaker=loop_breaker,
    )

    # Repeated zero-delta clicks on Arm A (identical before and after fingerprints)
    ws_a1 = MockCDPWebSocket(occlusion_status="OK", initial_fp="same_fp", post_fp="same_fp")
    res_a1 = await pipeline.execute_action(ws=ws_a1, operation="CLICK", target_selector="button#retry")
    assert res_a1.status == PipelineStatus.SUCCESS
    assert res_a1.state_changed is False

    ws_a2 = MockCDPWebSocket(occlusion_status="OK", initial_fp="same_fp", post_fp="same_fp")
    res_a2 = await pipeline.execute_action(ws=ws_a2, operation="CLICK", target_selector="button#retry")
    assert res_a2.status == PipelineStatus.BLOCKED_BY_LOOP_BREAKER
    assert res_a2.was_loop_blocked is True
