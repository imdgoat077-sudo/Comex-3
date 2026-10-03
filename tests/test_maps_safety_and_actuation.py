"""
tests/test_maps_safety_and_actuation.py
=======================================
Verification suite for the four final design constraints:
1. Adaptive scroll-before-occlusion (center first, bounded reposition, fail-closed, max 2 attempts, logging fields)
2. Composite route cards (div[data-trip-index] semantic candidate, child spans don't compete, Details independently selectable)
3. Adaptive off-screen candidate policy (min(detected, 12) with nearest-to-viewport priority)
4. Explicit protection against repeated selected-card clicks (candidate.selected=True -> TARGET_ALREADY_SELECTED, no dispatch)

Covers all 12 required tests before live Maps runs:
1. Visible target -> no unnecessary scroll
2. Below-fold target -> center scroll -> Method E passes
3. Center still occluded -> bounded second reposition -> Method E re-check
4. Still occluded -> no dispatch
5. Route card resolves as one semantic candidate
6. Child spans don't compete with route card
7. Details remains independently selectable
8. Off-screen candidate count obeys adaptive cap
9. Selected route card -> TARGET_ALREADY_SELECTED
10. Details on selected card -> still executable
11. Fresh coordinates after scrolling are used by cursor + actuator
12. Existing Jev thresholds and timeout unchanged
"""

import asyncio
import json
import pytest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from services.browser_actuator import (
    ActuationResult,
    ActuationStatus,
    BrowserActuator,
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
from services.universal_extractor import (
    UniversalExtractor,
    COMEX_BINDING_ATTR,
)


class MockCDPWebSocket:
    """Mock CDP WebSocket simulating precise browser responses."""

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
# Test 1: Visible target -> no unnecessary scroll
# ==============================================================================
@pytest.mark.asyncio
async def test_1_visible_target_no_unnecessary_scroll():
    """When target is in viewport and clear, 0 scroll attempts made, strategy is 'none'."""
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {
            "status": "OK",
            "x": 200,
            "y": 300,
            "scroll_attempts": 0,
            "scroll_strategy": "none",
            "pre_scroll_rect": {"top": 280, "bottom": 320, "x": 100, "y": 280, "width": 200, "height": 40},
            "post_scroll_rect": {"top": 280, "bottom": 320, "x": 100, "y": 280, "width": 200, "height": 40},
            "occluder_before": None,
            "occluder_after": None,
        }
    })
    msg_counter = [1]
    res = await actuator.validate_occlusion(ws, bid="c1", selector=None, msg_counter=msg_counter)

    assert res["status"] == "OK"
    assert res["scroll_attempts"] == 0
    assert res["scroll_strategy"] == "none"
    assert res["occluder_before"] is None
    assert res["occluder_after"] is None
    assert res["x"] == 200
    assert res["y"] == 300


# ==============================================================================
# Test 2: Below-fold target -> center scroll -> Method E passes
# ==============================================================================
@pytest.mark.asyncio
async def test_2_below_fold_target_center_scroll_method_e_passes():
    """Below-fold target triggers attempt 1 (center scroll); Method E hit-test passes."""
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {
            "status": "OK",
            "x": 276,
            "y": 415,
            "scroll_attempts": 1,
            "scroll_strategy": "center",
            "pre_scroll_rect": {"top": 950, "bottom": 1034, "x": 72, "y": 950, "width": 408, "height": 84},
            "post_scroll_rect": {"top": 373, "bottom": 457, "x": 72, "y": 373, "width": 408, "height": 84},
            "occluder_before": {"reason": "OUT_OF_VIEWPORT"},
            "occluder_after": None,
        }
    })
    msg_counter = [1]
    res = await actuator.validate_occlusion(ws, bid="c5", selector=None, msg_counter=msg_counter)

    assert res["status"] == "OK"
    assert res["scroll_attempts"] == 1
    assert res["scroll_strategy"] == "center"
    assert res["pre_scroll_rect"]["top"] > 800  # Was below fold
    assert res["post_scroll_rect"]["top"] < 800  # Scrolled into center view
    assert res["x"] == 276
    assert res["y"] == 415


# ==============================================================================
# Test 3: Center still occluded -> bounded second reposition -> Method E re-check
# ==============================================================================
@pytest.mark.asyncio
async def test_3_center_still_occluded_bounded_second_reposition():
    """Center scroll still leaves target occluded by floating banner; second reposition succeeds."""
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {
            "status": "OK",
            "x": 276,
            "y": 350,
            "scroll_attempts": 2,
            "scroll_strategy": "adaptive_reposition",
            "pre_scroll_rect": {"top": 894, "bottom": 978, "x": 72, "y": 894, "width": 408, "height": 84},
            "post_scroll_rect": {"top": 308, "bottom": 392, "x": 72, "y": 308, "width": 408, "height": 84},
            "occluder_before": {"tag": "DIV", "class": "Tn4yhe", "id": "", "text": "New! Continue your trip"},
            "occluder_after": None,
        }
    })
    msg_counter = [1]
    res = await actuator.validate_occlusion(ws, bid="c5", selector=None, msg_counter=msg_counter)

    assert res["status"] == "OK"
    assert res["scroll_attempts"] == 2
    assert res["scroll_strategy"] == "adaptive_reposition"
    assert res["occluder_before"]["class"] == "Tn4yhe"
    assert res["occluder_after"] is None


# ==============================================================================
# Test 4: Still occluded -> no dispatch
# ==============================================================================
@pytest.mark.asyncio
async def test_4_still_occluded_fails_closed_no_dispatch():
    """When target remains occluded after 2 attempts, fail closed and dispatch NO physical click."""
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {
            "status": "STALE_TARGET_OCCLUDED",
            "x": 276,
            "y": 745,
            "scroll_attempts": 2,
            "scroll_strategy": "adaptive_reposition",
            "pre_scroll_rect": {"top": 703, "bottom": 787, "x": 72, "y": 703, "width": 408, "height": 84},
            "post_scroll_rect": {"top": 703, "bottom": 787, "x": 72, "y": 703, "width": 408, "height": 84},
            "occluder_before": {"tag": "DIV", "class": "Tn4yhe"},
            "occluder_after": {"tag": "DIV", "class": "Tn4yhe"},
            "top_tag": "DIV",
            "top_class": "Tn4yhe",
        },
        "el.click()": lambda msg: True
    })
    msg_counter = [1]
    res = await actuator.execute_click(ws, bid="c3", selector=None, msg_counter=msg_counter)

    assert res.is_success is False
    assert res.was_occluded is True
    assert res.status == ActuationStatus.STALE_TARGET_OCCLUDED
    assert res.scroll_attempts == 2
    assert res.scroll_strategy == "adaptive_reposition"
    # Invariant: Physical click was NOT dispatched!
    assert not any("el.click()" in m.get("params", {}).get("expression", "") for m in ws.sent_messages)


# ==============================================================================
# Test 5: Route card resolves as one semantic candidate
# ==============================================================================
def test_5_route_card_resolves_as_one_semantic_candidate():
    """Transit route card div[data-trip-index] is extracted as a single composite candidate."""
    html = """
    <div tabindex="0" class="UgZKXd ZxL25b yYG3jf" data-trip-index="1" role="button">
        <span class="Os0QJc google-symbols" aria-label="Transit" role="img">transit_icon</span>
        <div class="header">
            <span>1 hr 46 min</span>
            <span>4:01 AM - 5:47 AM</span>
        </div>
        <div class="line-info">
            <span class="badge">55H</span>
            <span>Trichy to Chennai</span>
        </div>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)

    route_cards = [e for e in elements if e.get("type") == "transit_route_card"]
    assert len(route_cards) == 1

    card = route_cards[0]
    assert card["tag"] == "div"
    assert card["role"] == "button"
    assert "1 hr 46 min" in card["text"]
    assert "Trichy to Chennai" in card["text"]


# ==============================================================================
# Test 6: Child spans don't compete with route card
# ==============================================================================
def test_6_child_spans_do_not_compete_with_route_card():
    """Child spans within div[data-trip-index] are suppressed and do not compete as candidates."""
    html = """
    <div tabindex="0" class="UgZKXd ZxL25b yYG3jf" data-trip-index="2" role="button">
        <span class="icon">transit</span>
        <span class="duration">1 hr 48 min</span>
        <span class="time">4:01 AM - 5:49 AM</span>
        <span class="line">Route 119</span>
        <span class="destination">Trichy Central</span>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)

    # Exactly 1 candidate should exist for this card (the parent card itself)
    assert len(elements) == 1
    assert elements[0]["type"] == "transit_route_card"
    assert elements[0]["tag"] == "div"


# ==============================================================================
# Test 7: Details remains independently selectable
# ==============================================================================
def test_7_details_remains_independently_selectable():
    """Child Details button inside route card is preserved as its own actionable candidate."""
    html = """
    <div tabindex="0" class="UgZKXd ZxL25b vKKO3d yYG3jf" data-trip-index="0" role="link">
        <div class="route-summary">1 hr 33 min 4:01 AM - 5:34 AM Route 119</div>
        <button class="TIQqpf fontTitleSmall Hk4XGb" id="details-btn-0">Details</button>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)

    # Both parent route card and child Details button must exist
    assert len(elements) == 2

    route_card = next(e for e in elements if e["type"] == "transit_route_card")
    details_btn = next(e for e in elements if e["tag"] == "button")

    assert route_card["tag"] == "div"
    assert "Route 119" in route_card["text"]
    assert route_card["selected"] is True

    assert details_btn["id"] == "details-btn-0"
    assert details_btn["text"] == "Details"
    assert details_btn["selected"] is False


# ==============================================================================
# Test 8: Off-screen candidate count obeys adaptive cap
# ==============================================================================
def test_8_offscreen_candidate_count_obeys_adaptive_cap():
    """
    Adaptive off-screen candidate cap: min(detected_items_in_active_scroll_container, 12).
    - Maps 6 cards (5 in viewport, 1 offscreen): all 6 allowed.
    - Large list (5 in viewport, 20 offscreen): capped at 12 offscreen (total 17).
    """
    # Case A: 6 route cards (5 in viewport, 1 offscreen)
    cards_html = ""
    for i in range(6):
        y = i * 150
        cards_html += f"""
        <div tabindex="0" class="UgZKXd" data-trip-index="{i}" role="button" data-y="{y}" data-height="100" data-container="transit_pane">
            Route {i} 1 hr 30 min
        </div>
        """
    res_6 = UniversalExtractor.extract_from_html_fixture(cards_html, viewport_height=800)
    # y=0..4 are in viewport (0, 150, 300, 450, 600 < 800); y=750 is top=750 bottom=850 (partially offscreen/offscreen)
    # Total cards detected in container = 6 -> max_offscreen = min(6, 12) = 6 -> all 6 included!
    assert len(res_6) == 6

    # Case B: Large scroll container with 25 actionable items
    large_html = ""
    for i in range(25):
        y = i * 100
        large_html += f"""
        <button id="item-{i}" data-y="{y}" data-height="40" data-container="large_list">
            Item {i}
        </button>
        """
    res_25 = UniversalExtractor.extract_from_html_fixture(large_html, viewport_height=500)
    # In viewport (y < 500): items 0..4 (5 items)
    # Off-screen (y >= 500): items 5..24 (20 items)
    # Cap = min(25, 12) = 12 offscreen items
    # Total extracted = 5 in viewport + 12 offscreen = 17 items!
    assert len(res_25) == 17


# ==============================================================================
# Test 9: Selected route card -> TARGET_ALREADY_SELECTED
# ==============================================================================
@pytest.mark.asyncio
async def test_9_selected_route_card_blocks_with_target_already_selected():
    """Clicking an already-selected route card triggers TARGET_ALREADY_SELECTED with no physical dispatch."""
    selected_card = CandidateElement(
        candidate_id="e1",
        epoch_id=1,
        bid="c1",
        tag="div",
        element_type="transit_route_card",
        dom_id="",
        text="Route 119 - 1 hr 33 min",
        label="Route 119",
        selected=True,
        center_x=276.0,
        center_y=350.0,
        role="link",
    )

    mock_actuator = BrowserActuator()
    mock_actuator.validate_occlusion = AsyncMock()
    mock_actuator.execute_click = AsyncMock()

    pipeline = BrowserActionPipeline(actuator=mock_actuator)

    ws = MockCDPWebSocket()
    with patch.object(pipeline.extractor, "compute_fingerprint_from_cdp", AsyncMock(return_value="fp_state")):
        with patch.object(pipeline.micro_action_resolver, "resolve", AsyncMock(return_value=DelegationResolution(
            status=DelegationStatus.RESOLVED,
            action=AsyncMock(confidence=0.95, margin=0.5, abstained=False),
            target_element=selected_card,
        ))):
            with patch.object(pipeline.extractor, "extract_from_cdp", AsyncMock(return_value=[{"bid": "c1"}])):
                result = await pipeline.execute_action(
                    ws=ws,
                    operation="CLICK",
                    target_description="Route 119 transit route",
                    goal="Select route 119",
                )

    assert result.status == PipelineStatus.TARGET_ALREADY_SELECTED
    assert result.was_already_selected is True
    # Invariant: NO physical click dispatch!
    mock_actuator.execute_click.assert_not_called()


# ==============================================================================
# Test 10: Details on selected card -> still executable
# ==============================================================================
@pytest.mark.asyncio
async def test_10_details_on_selected_card_still_executable():
    """Clicking the Details button on a selected route card is NOT blocked and executes normally."""
    details_btn = CandidateElement(
        candidate_id="e2",
        epoch_id=1,
        bid="c2",
        tag="button",
        element_type="button",
        dom_id="details-btn-0",
        text="Details",
        label="Details",
        selected=False,  # Details button is not a selected route card
        center_x=320.0,
        center_y=350.0,
        role="button",
    )

    mock_actuator = BrowserActuator()
    mock_actuator.validate_occlusion = AsyncMock(return_value={"status": "OK", "x": 320.0, "y": 350.0})
    mock_actuator.execute_click = AsyncMock(return_value=ActuationResult(
        status=ActuationStatus.SUCCESS,
        action="click",
        target_id="details-btn-0",
        x=320.0,
        y=350.0,
    ))

    pipeline = BrowserActionPipeline(actuator=mock_actuator)

    ws = MockCDPWebSocket()
    with patch.object(pipeline.extractor, "compute_fingerprint_from_cdp", AsyncMock(return_value="fp_state")):
        with patch.object(pipeline.micro_action_resolver, "resolve", AsyncMock(return_value=DelegationResolution(
            status=DelegationStatus.RESOLVED,
            action=AsyncMock(confidence=0.92, margin=0.4, abstained=False),
            target_element=details_btn,
        ))):
            with patch.object(pipeline.extractor, "extract_from_cdp", AsyncMock(return_value=[{"bid": "c2"}])):
                result = await pipeline.execute_action(
                    ws=ws,
                    operation="CLICK",
                    target_description="Details button",
                    goal="View turn-by-turn route details",
                )

    assert result.status == PipelineStatus.SUCCESS
    assert result.was_already_selected is False
    mock_actuator.execute_click.assert_called_once()


# ==============================================================================
# Test 11: Fresh coordinates after scrolling are used by cursor + actuator
# ==============================================================================
@pytest.mark.asyncio
async def test_11_fresh_coordinates_after_scrolling_used_by_cursor_and_actuator():
    """Target coordinates after adaptive scrolling are passed to cursor and returned."""
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {
            "status": "OK",
            "x": 276.0,
            "y": 415.0,  # Fresh coordinate after center scroll
            "scroll_attempts": 1,
            "scroll_strategy": "center",
            "pre_scroll_rect": {"top": 894, "bottom": 978, "x": 72, "y": 894, "width": 408, "height": 84},
            "post_scroll_rect": {"top": 373, "bottom": 457, "x": 72, "y": 373, "width": 408, "height": 84},
        },
        "el.click()": lambda msg: True,
    })

    cursor_notified_coords = []
    async def mock_cursor_notify(x, y, action="click", tab_id="active_tab"):
        cursor_notified_coords.append((x, y))

    actuator.notify_visual_cursor = mock_cursor_notify

    msg_counter = [1]
    res = await actuator.execute_click(ws, bid="c5", selector=None, msg_counter=msg_counter)

    assert res.is_success is True
    assert res.x == 276.0
    assert res.y == 415.0
    assert len(cursor_notified_coords) == 1
    assert cursor_notified_coords[0] == (276.0, 415.0)
    assert res.scroll_attempts == 1
    assert res.scroll_strategy == "center"


# ==============================================================================
# Test 12: Existing Jev thresholds and timeout unchanged
# ==============================================================================
def test_12_existing_jev_thresholds_and_timeout_unchanged():
    """Verify calibrated Jev confidence and margin thresholds and timeout remain unchanged."""
    # CLICK thresholds
    assert CALIBRATION_THRESHOLDS["CLICK"]["confidence"] == 0.70
    assert CALIBRATION_THRESHOLDS["CLICK"]["margin"] == 0.05

    # FILL thresholds
    assert CALIBRATION_THRESHOLDS["FILL"]["confidence"] == 0.75
    assert CALIBRATION_THRESHOLDS["FILL"]["margin"] == 0.10

    # SELECT thresholds
    assert CALIBRATION_THRESHOLDS["SELECT"]["confidence"] == 0.70
    assert CALIBRATION_THRESHOLDS["SELECT"]["margin"] == 0.05

    # Timeout
    resolver = MicroActionResolver()
    assert resolver.timeout_ms == 1200.0
