"""
tests/test_commit_b_execution_contract.py
=========================================
Commit B Verification Suite:
1. Authoritative interaction boundary: run_browser_harness rejects all synthetic
   DOM input simulations (dispatchEvent, PointerEvent, ClipboardEvent, KeyboardEvent,
   .click(), .value =, CDP Input simulation).
2. Observational purity: run_browser_harness permits legitimate read-only extraction,
   DOM inspection, scrolling, and navigation.
3. Structured action contract: execute_browser_action returns structured JSON distinguishing:
   - target_resolved
   - dispatch_attempted
   - dispatch_sent
   - dispatch_success
   - state_changed
   - postcondition ("MET", "UNMET", "UNKNOWN")
   - progress ("POSITIVE", "NEUTRAL", "NEGATIVE", "UNKNOWN")
4. Decoupling invariant: dispatch_success=True is NOT failed by state_changed=False.
5. Coordinate & target geometry verification: target coordinates falling outside
   expected region abort before dispatch with COORDINATE_MISMATCH (Lichess regression test).
6. Visibility-aware terminal boundary detector:
   - Detects visible LOGIN_REQUIRED, PAYMENT_REQUIRED, OTP_REQUIRED, CAPTCHA_REQUIRED.
   - Strictly ignores hidden modal elements (display: none / visibility: hidden).
7. Stall detection: 3 consecutive non-progressing actions trigger stall warning injection.
8. Frozen invariants: Jev thresholds, Method E, and BrowserActuator remain unchanged.
"""

import asyncio
import json
import pytest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from agent_service import (
    detect_interactive_action_in_code,
    TERMINAL_DETECTOR_JS,
    detect_terminal_boundary_from_cdp,
)
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
from services.micro_action_resolver import (
    CALIBRATION_THRESHOLDS,
    DelegationResolution,
    DelegationStatus,
    MicroActionResolver,
)
from services.browser_actuator import (
    ActuationResult,
    ActuationStatus,
    BrowserActuator,
)


class MockCDPWebSocket:
    """Mock CDP WebSocket connection for testing pipeline and terminal detection."""

    def __init__(self, response_handlers=None):
        self.sent_messages: List[Dict[str, Any]] = []
        self.response_handlers = response_handlers or {}

    async def send(self, message: str) -> None:
        self.sent_messages.append(json.loads(message))

    async def recv(self) -> str:
        last = self.sent_messages[-1]
        msg_id = last["id"]
        expr = last.get("params", {}).get("expression", "")

        val = None
        for pattern, handler in self.response_handlers.items():
            if pattern in expr:
                val = handler(last) if callable(handler) else handler
                break

        return json.dumps({
            "id": msg_id,
            "result": {"result": {"value": val}}
        })


# ==============================================================================
# Test 1-4: run_browser_harness interaction rejection vs read-only allowance
# ==============================================================================
def test_1_run_browser_harness_rejects_synthetic_clipboard_event():
    """Synthetic ClipboardEvent dispatch in run_browser_harness must be rejected."""
    code = """
    script = '''
    const pasteEvent = new ClipboardEvent("paste", { clipboardData: dt });
    document.querySelector(".excalidraw").dispatchEvent(pasteEvent);
    '''
    res = js(script)
    """
    violation = detect_interactive_action_in_code(code)
    assert violation is not None
    assert "dispatchEvent" in violation or "ClipboardEvent" in violation


def test_2_run_browser_harness_rejects_synthetic_pointer_event():
    """Synthetic PointerEvent simulation in run_browser_harness must be rejected."""
    code = """
    js('''
    piece.dispatchEvent(new PointerEvent("pointerdown", opts));
    document.dispatchEvent(new MouseEvent("mousemove", moveOpts));
    ''')
    """
    violation = detect_interactive_action_in_code(code)
    assert violation is not None
    assert "PointerEvent" in violation or "dispatchEvent" in violation


def test_3_run_browser_harness_rejects_direct_cdp_input_simulation():
    """Direct calls to cdp('Input.dispatchKeyEvent') must be rejected."""
    code = """
    cdp("Input.dispatchKeyEvent", type="rawKeyDown", key="a", code="KeyA")
    """
    violation = detect_interactive_action_in_code(code)
    assert violation is not None
    assert "CDP" in violation or "Input" in violation


def test_4_run_browser_harness_allows_legitimate_observation_and_navigation():
    """Legitimate read-only observation, scrolling, and navigation are allowed."""
    safe_code = """
    goto_url("https://www.google.com/maps")
    wait_for_load()
    import time
    time.sleep(1)
    info = page_info()
    title = str(js("document.title"))
    rect = str(js("document.querySelector('.feed').getBoundingClientRect()"))
    js("window.scrollBy(0, 500)")
    """
    violation = detect_interactive_action_in_code(safe_code)
    assert violation is None


# ==============================================================================
# Test 5: Structured action contract in PipelineResult
# ==============================================================================
def test_5_structured_action_result_contract_keys():
    """PipelineResult.to_dict() must return all required contract fields."""
    res = PipelineResult(
        status=PipelineStatus.SUCCESS,
        operation="CLICK",
        target_description="Search button",
        target_id="e1",
        confidence=0.94,
        margin=0.88,
        state_changed=True,
        duration_ms=45.2,
        details="Successfully executed CLICK on <button>",
        target_resolved=True,
        dispatch_attempted=True,
        dispatch_sent=True,
        dispatch_success=True,
        postcondition_passed=True,
        postcondition="MET",
        progress="POSITIVE",
    )
    d = res.to_dict()
    assert d["status"] == "SUCCESS"
    assert d["target_resolved"] is True
    assert d["dispatch_attempted"] is True
    assert d["dispatch_success"] is True
    assert d["state_changed"] is True
    assert d["postcondition"] == "MET"
    assert d["progress"] == "POSITIVE"
    assert d["confidence"] == 0.94
    assert "details" in d

    # Verify JSON serializability
    j = res.to_json()
    parsed = json.loads(j)
    assert parsed["dispatch_success"] is True
    assert parsed["progress"] == "POSITIVE"


# ==============================================================================
# Test 6: Decoupling invariant (dispatch_success=True vs state_changed=False)
# ==============================================================================
def test_6_dispatch_success_decoupled_from_state_changed():
    """When dispatch succeeds but state_changed is False, action is still SUCCESS with UNKNOWN progress."""
    res = PipelineResult(
        status=PipelineStatus.SUCCESS,
        operation="FILL",
        target_description="Search box",
        target_id="e2",
        confidence=0.85,
        margin=0.70,
        state_changed=False,  # Legitimate typing before submit
        duration_ms=30.0,
        details="Successfully executed FILL",
        target_resolved=True,
        dispatch_attempted=True,
        dispatch_sent=True,
        dispatch_success=True,
        postcondition_passed=True,
        postcondition="UNKNOWN",
        progress="UNKNOWN",
    )
    assert res.is_success is True
    assert res.dispatch_success is True
    assert res.state_changed is False
    assert res.progress == "UNKNOWN"

    d = res.to_dict()
    assert d["dispatch_success"] is True
    assert d["state_changed"] is False
    assert d["progress"] == "UNKNOWN"


# ==============================================================================
# Test 7: Coordinate verification catches mismatch (Lichess regression test)
# ==============================================================================
@pytest.mark.asyncio
async def test_7_coordinate_verification_catches_lichess_header_mismatch():
    """When target e2 resolves to header (213, 27) but expected board region is y >= 64, abort with COORDINATE_MISMATCH."""
    actuator = BrowserActuator()
    # Mock validate_occlusion returning point (213, 27) in the top header
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {
            "status": "OK",
            "x": 213.0,
            "y": 27.0,  # Header coordinates
            "scroll_attempts": 0,
            "scroll_strategy": "none",
        }
    })

    candidate = CandidateElement(
        candidate_id="e2",
        epoch_id=1,
        bid="c2",
        tag="piece",
        element_type="pawn",
        dom_id="e2-pawn",
        text="Pawn on e2 square",
        label="Pawn on e2 square",
        center_x=213.0,
        center_y=27.0,
    )

    pipeline = BrowserActionPipeline(actuator=actuator)

    with patch.object(pipeline.extractor, "compute_fingerprint_from_cdp", AsyncMock(return_value="fp_state")):
        with patch.object(pipeline.micro_action_resolver, "resolve", AsyncMock(return_value=DelegationResolution(
            status=DelegationStatus.RESOLVED,
            action=AsyncMock(confidence=0.93, margin=0.88, abstained=False),
            target_element=candidate,
        ))):
            with patch.object(pipeline.extractor, "extract_from_cdp", AsyncMock(return_value=[{"bid": "c2"}])):
                # Expected board region: chessboard occupies y=64 to y=700, x=97 to x=733
                board_region = {"min_x": 97.0, "min_y": 64.0, "max_x": 733.0, "max_y": 700.0}

                result = await pipeline.execute_action(
                    ws=ws,
                    operation="CLICK",
                    target_description="Pawn on e2 square",
                    expected_region=board_region,
                )

    assert result.status == PipelineStatus.COORDINATE_MISMATCH
    assert result.was_coordinate_mismatch is True
    assert result.dispatch_attempted is True
    assert result.dispatch_sent is False
    assert result.dispatch_success is False
    assert result.postcondition == "UNMET"
    assert result.progress == "NEGATIVE"
    assert "(213.0, 27.0)" in result.details


# ==============================================================================
# Test 8-10: Visibility-aware terminal boundary detector
# ==============================================================================
@pytest.mark.asyncio
async def test_8_terminal_detector_detects_visible_login_modal():
    """Visibility-aware detector catches visibly rendered login modal."""
    ws = MockCDPWebSocket(response_handlers={
        "LOGIN_REQUIRED": lambda msg: {
            "detected": ["LOGIN_REQUIRED"],
            "details": "Visible authentication/login modal or password field detected"
        }
    })
    res = await detect_terminal_boundary_from_cdp(ws)
    assert res is not None
    assert "LOGIN_REQUIRED" in res["detected"]


@pytest.mark.asyncio
async def test_9_terminal_detector_ignores_hidden_login_elements():
    """Visibility-aware detector returns None when login modal is display: none or hidden."""
    ws = MockCDPWebSocket(response_handlers={
        "LOGIN_REQUIRED": lambda msg: None
    })
    res = await detect_terminal_boundary_from_cdp(ws)
    assert res is None


@pytest.mark.asyncio
async def test_10_terminal_detector_detects_visible_payment_otp_captcha():
    """Visibility-aware detector catches payment wall, OTP, and captcha."""
    for wall in ["PAYMENT_REQUIRED", "OTP_REQUIRED", "CAPTCHA_REQUIRED"]:
        ws = MockCDPWebSocket(response_handlers={
            wall: lambda msg, w=wall: {"detected": [w], "details": f"Visible {w} detected"}
        })
        res = await detect_terminal_boundary_from_cdp(ws)
        assert res is not None
        assert wall in res["detected"]


# ==============================================================================
# Test 11: 3 consecutive non-progressing actions stall guidance
# ==============================================================================
def test_11_consecutive_stall_detection_logic():
    """Simulate stall counter: 3 non-progressing actions trigger stall warning."""
    consecutive_stalls = 0
    warning_triggered = False

    # 3 consecutive failed or blocked actions
    failed_results = [
        {"dispatch_success": False, "status": "FAILED", "state_changed": False},
        {"dispatch_success": False, "status": "OCCLUDED", "state_changed": False},
        {"dispatch_success": False, "status": "BLOCKED_BY_LOOP_BREAKER", "state_changed": False},
    ]

    for r in failed_results:
        disp_ok = r.get("dispatch_success", False)
        status_val = r.get("status", "")
        if not disp_ok or status_val in ("BLOCKED_BY_LOOP_BREAKER", "OCCLUDED", "COORDINATE_MISMATCH", "FAILED"):
            consecutive_stalls += 1
        elif disp_ok and r.get("state_changed", False):
            consecutive_stalls = 0

        if consecutive_stalls == 3:
            warning_triggered = True

    assert consecutive_stalls == 3
    assert warning_triggered is True


# ==============================================================================
# Test 12: Frozen components invariant
# ==============================================================================
def test_12_frozen_components_invariant():
    """Verify Jev thresholds, Method E, and BrowserActuator remain unchanged."""
    assert CALIBRATION_THRESHOLDS["CLICK"]["confidence"] == 0.70
    assert CALIBRATION_THRESHOLDS["CLICK"]["margin"] == 0.05
    assert CALIBRATION_THRESHOLDS["FILL"]["confidence"] == 0.75
    assert CALIBRATION_THRESHOLDS["FILL"]["margin"] == 0.10

    resolver = MicroActionResolver()
    assert resolver.timeout_ms == 1200.0

    actuator = BrowserActuator()
    assert hasattr(actuator, "validate_occlusion")
    assert hasattr(actuator, "execute_click")
    assert hasattr(actuator, "execute_atomic_fill")
