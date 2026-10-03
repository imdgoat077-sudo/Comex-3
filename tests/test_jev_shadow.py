"""
Tests for JevShadowObserver and Calibration Curve.
"""

import pytest
from benchmarks.jev_shadow import JevShadowObserver
from services.candidate_generator import CandidateGenerator
from services.typesafe_client import (
    AsyncTypeSafeClient,
    ChoiceAnswer,
    ClientMode,
    NoulAnswer,
    SystemOneResponse,
)


@pytest.mark.asyncio
async def test_shadow_observer_mock_agreement_and_calibration():
    # 1. Setup mock client
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    expected_resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "operation": ChoiceAnswer(
                choice="CLICK",
                probabilities={"CLICK": 0.95, "FILL": 0.05},
                confidence=0.95,
            ),
            "click_target": ChoiceAnswer(
                choice="e1",
                probabilities={"e1": 0.92, "e2": 0.08},
                confidence=0.92,
            ),
            "fill_target": ChoiceAnswer(choice="NO_ACTIONABLE_TARGET", probabilities={}, confidence=0.1),
            "select_target": ChoiceAnswer(choice="NO_ACTIONABLE_TARGET", probabilities={}, confidence=0.1),
            "wait_reason": ChoiceAnswer(choice="WAIT_FOR_QUIESCENCE", probabilities={}, confidence=0.5),
            "is_destructive": NoulAnswer(noul=0.01),
        },
        usage={"input_tokens": 100, "output_tokens": 0},
        latency_ms=85.0,
    )
    client.register_mock_response("default", expected_resp)

    observer = JevShadowObserver(client=client)

    # 2. Generate snapshot
    generator = CandidateGenerator()
    raw = [
        {"bid": "101", "tag": "button", "type": "submit", "id": "sub", "text": "Submit"},
        {"bid": "102", "tag": "button", "type": "button", "id": "can", "text": "Cancel"},
    ]
    snapshot = generator.generate_snapshot(raw, goal="Click submit")

    # 3. Simulate turn evaluation where Gemini chose bid=101 click
    gemini_action = {"action": "click", "bid": "101"}
    rec = await observer.evaluate_observation_shadow(
        snapshot=snapshot,
        task_id="T01",
        task_name="Click Button",
        turn_index=1,
        gemini_action=gemini_action,
        gemini_latency_ms=2100.0,
    )

    assert rec.agreement is True
    assert rec.jev_target_bid == "101"
    assert rec.jev_confidence == 0.92
    assert rec.jev_latency_ms == 85.0

    # 4. Calibration curve
    rec.task_success = True
    curve = observer.generate_calibration_curve()
    assert curve["total_turns"] == 1
    assert curve["overall_agreement_pct"] == 100.0
    bin_90_95 = curve["confidence_calibration_curve"]["0.90-0.95"]
    assert bin_90_95["total_decisions"] == 1
    assert bin_90_95["agreement_rate_pct"] == 100.0
    assert bin_90_95["successful_task_rate_pct"] == 100.0
