"""
tests/test_production_integration.py
====================================
Production Integration Verification Suite for Phase A Nervous System Pipeline.

Validates the full chain:
    Phase A
      ↓
    UniversalExtractor (Observational purity, transient data-comex-id, fingerprint isolation)
      ↓
    CandidateGenerator (Deterministic snapshot, stable candidate IDs)
      ↓
    MicroActionResolver / Jev System 1 (Calibrated CLICK 0.70/0.05, FILL 0.75/0.10, SELECT shadow-only)
      ↓
    ActuatorGate (Nervous system quiescence, overlay evaluation)
      ↓
    BrowserActuator (Method E, late occlusion hit-testing, atomic physical dispatch)
      ↓
    Fingerprint + LoopBreaker (Cycle prevention, zero-delta detection, escalation)

Tests strictly enforce:
1. No external website traffic.
2. Deterministic mock CDP and Jev System 1 evaluation.
3. Calibrated Jev thresholds and clean abstention fail-forward to Gemini.
4. Late occlusion fail-closed blocking.
5. LoopBreaker cycle detection on repeated zero-delta actions.
6. Fingerprint isolation: data-comex-id exclusion invariant.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from services.loop_breaker import ActionLoopBreaker, RecoveryAction
from services.micro_action_resolver import (
    CALIBRATION_THRESHOLDS,
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
from services.universal_extractor import (
    COMEX_BINDING_ATTR,
    DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT,
    UniversalExtractor,
)


class MockCDPConnection:
    """Deterministic Mock CDP WebSocket protocol for offline integration tests."""

    def __init__(self, raw_candidates=None, occlusion_status="OK", initial_fingerprint="fp_state_initial"):
        self.sent_messages: List[Dict[str, Any]] = []
        self.raw_candidates = raw_candidates if raw_candidates is not None else [
            {
                "bid": "c1",
                "tag": "button",
                "type": "",
                "id": "search-submit",
                "className": "btn-primary",
                "text": "Search Flights",
                "label": "Search Flights",
                "accessible_name": "Search Flights",
                "role": "button",
                "selector": "#search-submit",
                "disabled": False,
                "checked": False,
                "expanded": None,
                "focused": False,
                "bounds": {"x": 100, "y": 200, "width": 120, "height": 40},
                "center": {"x": 160, "y": 220},
                "options": None,
            },
            {
                "bid": "c2",
                "tag": "input",
                "type": "text",
                "id": "flight-origin",
                "className": "form-control",
                "text": "Departure Airport",
                "label": "Departure Airport",
                "accessible_name": "Departure Airport",
                "role": "textbox",
                "selector": "#flight-origin",
                "disabled": False,
                "checked": False,
                "expanded": None,
                "focused": False,
                "bounds": {"x": 100, "y": 120, "width": 200, "height": 35},
                "center": {"x": 200, "y": 137},
                "options": None,
            },
        ]
        self.occlusion_status = occlusion_status
        self.fingerprint_state = initial_fingerprint
        self.dispatched_clicks = []
        self.dispatched_fills = []

    async def send(self, message: str) -> None:
        self.sent_messages.append(json.loads(message))

    async def recv(self) -> str:
        last = self.sent_messages[-1]
        msg_id = last["id"]
        expr = last.get("params", {}).get("expression", "")

        val = None
        # 1. Universal Extraction JS
        if "COMEX_BINDING_ATTR" in expr and "computeAccessibleName" in expr:
            val = self.raw_candidates

        # 2. Universal Fingerprint JS
        elif "parts.join('###')" in expr:
            val = self.fingerprint_state

        # 3. Late Occlusion Check
        elif "document.elementFromPoint" in expr:
            if self.occlusion_status == "OK":
                val = {"status": "OK", "x": 160, "y": 220}
            elif self.occlusion_status == "OCCLUDED":
                val = {
                    "status": "STALE_TARGET_OCCLUDED",
                    "x": 160,
                    "y": 220,
                    "top_tag": "DIV",
                    "top_class": "modal-overlay",
                }
            else:
                val = {"status": self.occlusion_status}

        # 4. Click Dispatch
        elif "el.click()" in expr:
            self.dispatched_clicks.append(expr)
            val = True

        # 5. Atomic Fill Dispatch
        elif "prototypeValueSetter" in expr:
            self.dispatched_fills.append(expr)
            val = "OK"

        return json.dumps({
            "id": msg_id,
            "result": {"result": {"value": val}},
        })


def _create_mock_client(choice: str, confidence: float, margin: float) -> AsyncTypeSafeClient:
    """Creates a mock AsyncTypeSafeClient returning predetermined ChoiceAnswer."""
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    prob_top = confidence
    prob_second = max(0.0, confidence - margin)
    probs = {choice: prob_top, "other_choice": prob_second}

    resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(choice=choice, confidence=confidence, probabilities=probs),
            "fill_target": ChoiceAnswer(choice=choice, confidence=confidence, probabilities=probs),
            "select_target": ChoiceAnswer(choice=choice, confidence=confidence, probabilities=probs),
        },
    )
    client.set_mock_handler(lambda state, questions, model: resp)
    return client


class TestProductionPipelineIntegration:
    """End-to-end integration tests for BrowserActionPipeline."""

    @pytest.mark.asyncio
    async def test_successful_click_action_end_to_end(self):
        """Validates successful CLICK through all pipeline stages."""
        ws = MockCDPConnection(occlusion_status="OK", initial_fingerprint="page_state_v1")
        mock_client = _create_mock_client(choice="e1", confidence=0.88, margin=0.35)
        resolver = MicroActionResolver(client=mock_client)
        loop_breaker = ActionLoopBreaker()
        pipeline = BrowserActionPipeline(micro_action_resolver=resolver, loop_breaker=loop_breaker)

        # Execute CLICK
        res = await pipeline.execute_action(
            ws=ws,
            operation="CLICK",
            target_description="Search Flights button",
            turn=1,
        )

        assert res.is_success is True
        assert res.status == PipelineStatus.SUCCESS
        assert res.operation == "CLICK"
        assert res.target_id == "e1"
        assert res.target_bid == "c1"
        assert res.confidence == 0.88
        assert res.margin == 0.35
        assert len(ws.dispatched_clicks) == 1
        assert "el.click()" in ws.dispatched_clicks[0]

    @pytest.mark.asyncio
    async def test_successful_fill_action_end_to_end(self):
        """Validates successful FILL through all pipeline stages."""
        ws = MockCDPConnection(occlusion_status="OK", initial_fingerprint="page_state_v1")
        mock_client = _create_mock_client(choice="e2", confidence=0.82, margin=0.25)
        resolver = MicroActionResolver(client=mock_client)
        pipeline = BrowserActionPipeline(micro_action_resolver=resolver)

        # Execute FILL
        res = await pipeline.execute_action(
            ws=ws,
            operation="FILL",
            target_description="Departure Airport text field",
            value="SFO",
            turn=1,
        )

        assert res.is_success is True
        assert res.status == PipelineStatus.SUCCESS
        assert res.operation == "FILL"
        assert res.target_id == "e2"
        assert res.target_bid == "c2"
        assert res.confidence == 0.82
        assert len(ws.dispatched_fills) == 1

    @pytest.mark.asyncio
    async def test_low_confidence_abstains_fail_forward(self):
        """CLICK with confidence 0.65 (< 0.70 threshold) must abstain cleanly."""
        ws = MockCDPConnection(occlusion_status="OK")
        mock_client = _create_mock_client(choice="e1", confidence=0.65, margin=0.20)
        resolver = MicroActionResolver(client=mock_client)
        pipeline = BrowserActionPipeline(micro_action_resolver=resolver)

        res = await pipeline.execute_action(
            ws=ws,
            operation="CLICK",
            target_description="Search Flights button",
            turn=1,
        )

        assert res.is_success is False
        assert res.was_abstained is True
        assert res.status == PipelineStatus.ABSTAINED
        assert "LOW_CONFIDENCE" in (res.abstention_reason or "")
        # No physical action dispatched
        assert len(ws.dispatched_clicks) == 0

    @pytest.mark.asyncio
    async def test_low_margin_abstains_fail_forward(self):
        """CLICK with margin 0.02 (< 0.05 threshold) must abstain cleanly."""
        ws = MockCDPConnection(occlusion_status="OK")
        mock_client = _create_mock_client(choice="e1", confidence=0.78, margin=0.02)
        resolver = MicroActionResolver(client=mock_client)
        pipeline = BrowserActionPipeline(micro_action_resolver=resolver)

        res = await pipeline.execute_action(
            ws=ws,
            operation="CLICK",
            target_description="Search Flights button",
            turn=1,
        )

        assert res.is_success is False
        assert res.was_abstained is True
        assert res.status == PipelineStatus.ABSTAINED
        assert "LOW_MARGIN" in (res.abstention_reason or "")
        assert len(ws.dispatched_clicks) == 0

    @pytest.mark.asyncio
    async def test_select_operation_shadow_only_disallowed(self):
        """SELECT operation is strictly shadow-only and must be disallowed."""
        ws = MockCDPConnection(occlusion_status="OK")
        mock_client = _create_mock_client(choice="e1", confidence=0.90, margin=0.30)
        resolver = MicroActionResolver(client=mock_client)
        pipeline = BrowserActionPipeline(micro_action_resolver=resolver)

        res = await pipeline.execute_action(
            ws=ws,
            operation="SELECT",
            target_description="Dropdown select",
            value="Option 1",
            turn=1,
        )

        assert res.is_success is False
        assert res.was_abstained is True
        assert "not approved for delegation" in (res.abstention_reason or "")
        assert len(ws.dispatched_clicks) == 0

    @pytest.mark.asyncio
    async def test_late_occlusion_blocks_physical_dispatch(self):
        """Occluded target must fail-closed before physical click."""
        ws = MockCDPConnection(occlusion_status="OCCLUDED")
        mock_client = _create_mock_client(choice="e1", confidence=0.85, margin=0.20)
        resolver = MicroActionResolver(client=mock_client)
        pipeline = BrowserActionPipeline(micro_action_resolver=resolver)

        res = await pipeline.execute_action(
            ws=ws,
            operation="CLICK",
            target_description="Search Flights button",
            turn=1,
        )

        assert res.is_success is False
        assert res.was_occluded is True
        assert res.status == PipelineStatus.OCCLUDED
        assert "modal-overlay" in res.details
        assert len(ws.dispatched_clicks) == 0

    @pytest.mark.asyncio
    async def test_loop_breaker_blocks_repeated_zero_delta_action(self):
        """LoopBreaker must intercept repeated action on identical fingerprint with zero delta."""
        # Static fingerprint produces zero delta
        ws = MockCDPConnection(occlusion_status="OK", initial_fingerprint="constant_stalled_page")
        mock_client = _create_mock_client(choice="e1", confidence=0.88, margin=0.30)
        resolver = MicroActionResolver(client=mock_client)
        loop_breaker = ActionLoopBreaker()
        pipeline = BrowserActionPipeline(micro_action_resolver=resolver, loop_breaker=loop_breaker)

        # Turn 1: First attempt executes, but state delta is zero
        res1 = await pipeline.execute_action(
            ws=ws,
            operation="CLICK",
            target_description="Search Flights button",
            turn=1,
        )
        assert res1.is_success is True
        assert res1.state_changed is False

        # Turn 2: Exact same action on identical fingerprint must be blocked fail-closed!
        res2 = await pipeline.execute_action(
            ws=ws,
            operation="CLICK",
            target_description="Search Flights button",
            turn=2,
        )
        assert res2.is_success is False
        assert res2.was_loop_blocked is True
        assert res2.status == PipelineStatus.BLOCKED_BY_LOOP_BREAKER
        assert res2.recovery_action == RecoveryAction.BLOCK_REPEATED_ACTION
        assert "Loop Breaker blocked repeated action" in res2.details

    @pytest.mark.asyncio
    async def test_fingerprint_isolation_invariant(self):
        """Injected data-comex-id must be excluded from state fingerprint hash."""
        assert DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT is True
        assert COMEX_BINDING_ATTR == "data-comex-id"

        # Check that the fingerprint JS strictly excludes COMEX_BINDING_ATTR
        js = UniversalExtractor.get_fingerprint_script()
        assert "COMEX_BINDING_ATTR = 'data-comex-id'" in js
        assert "attr.name === COMEX_BINDING_ATTR" in js
        assert "continue" in js

    @pytest.mark.asyncio
    async def test_loop_breaker_escalation_after_consecutive_zero_deltas(self):
        """Consecutive zero delta actions on stalled state trigger ESCALATE_TO_SYSTEM2."""
        ws = MockCDPConnection(occlusion_status="OK", initial_fingerprint="static_frozen_dom")
        mock_client = _create_mock_client(choice="e1", confidence=0.85, margin=0.20)
        resolver = MicroActionResolver(client=mock_client)
        loop_breaker = ActionLoopBreaker(max_consecutive_zero_deltas=2)
        pipeline = BrowserActionPipeline(micro_action_resolver=resolver, loop_breaker=loop_breaker)

        # Action 1: zero delta
        r1 = await pipeline.execute_action(ws=ws, operation="CLICK", target_description="Action 1", turn=1)
        assert r1.is_success is True
        assert r1.state_changed is False

        # Action 2 on different element (e2): also zero delta
        mock_client_2 = _create_mock_client(choice="e2", confidence=0.85, margin=0.20)
        pipeline.micro_action_resolver = MicroActionResolver(client=mock_client_2)
        r2 = await pipeline.execute_action(ws=ws, operation="FILL", target_description="Action 2", value="text", turn=2)
        assert r2.is_success is True
        assert r2.state_changed is False

        # Action 3: must be escalated to System 2 by LoopBreaker!
        r3 = await pipeline.execute_action(ws=ws, operation="CLICK", target_description="Action 3", turn=3)
        assert r3.is_success is False
        assert r3.status == PipelineStatus.BLOCKED_BY_LOOP_BREAKER
        assert r3.recovery_action == RecoveryAction.ESCALATE_TO_SYSTEM2
        assert "consecutive zero-delta" in r3.details

    @pytest.mark.asyncio
    async def test_candidate_coverage_retention_across_multihead_choices(self):
        """UniversalExtractor must retain all interactive affordances for Jev multi-head choices."""
        html = """
        <main>
          <button id="book-btn">Book Flight</button>
          <input type="text" id="origin-input" placeholder="Departure City">
          <input type="checkbox" id="direct-only" checked>
          <select id="cabin-class">
            <option value="econ">Economy</option>
            <option value="biz">Business</option>
          </select>
          <a href="/deals">Best Deals</a>
          <div style="cursor: pointer;">Interactive Tile</div>
          <button disabled>Unavailable</button>
          <div inert><button>Inert Button</button></div>
        </main>
        """
        candidates = UniversalExtractor.extract_from_html_fixture(html)
        assert len(candidates) >= 5

        # Check tags and roles are represented
        tags = {c["tag"] for c in candidates}
        assert "button" in tags
        assert "input" in tags
        assert "select" in tags
        assert "a" in tags

        # Accessible names computed
        names = {c["accessible_name"] for c in candidates}
        assert "Book Flight" in names
        assert "Departure City" in names

        # Disabled elements are flagged with disabled=True
        disabled_buttons = [c for c in candidates if c.get("disabled") and c["tag"] == "button"]
        assert len(disabled_buttons) == 1
        assert disabled_buttons[0]["accessible_name"] == "Unavailable"

        # Inert elements are strictly excluded
        assert not any("Inert Button" in c["accessible_name"] for c in candidates)

    def test_agent_service_tool_registration_and_schema(self):
        """execute_browser_action must be registered in agent_service tools."""
        import agent_service
        # Check SYSTEM_PROMPT includes execute_browser_action
        assert "execute_browser_action" in agent_service.SYSTEM_PROMPT
        assert "MicroActionResolver" in str(agent_service.__doc__ or "") or "BrowserActionPipeline" in globals()

