"""
Unit tests for Phase 1 SemanticMicroTask exact contract and shadow evaluation.
"""

import pytest
from benchmarks.jev_shadow import JevShadowObserver, adjudicate_semantic_correctness
from services.candidate_generator import (
    CandidateGenerator,
    GroundedAction,
    SemanticMicroTask,
)
from services.typesafe_client import (
    AsyncTypeSafeClient,
    ChoiceAnswer,
    ClientMode,
    SystemOneResponse,
)


def test_microtask_question_building_click_fill_select():
    generator = CandidateGenerator()
    raw = [
        {"bid": "1", "tag": "button", "type": "submit", "id": "search-btn", "text": "Search"},
        {"bid": "2", "tag": "input", "type": "text", "id": "city-from", "label": "Departure City"},
        {"bid": "3", "tag": "select", "type": "", "id": "country-sel", "options": ["USA", "Canada", "UK"]},
    ]
    snapshot = generator.generate_snapshot(raw, goal="Book flight from Chennai to Toronto")

    # 1. CLICK micro-task
    click_task = SemanticMicroTask(operation="CLICK", target_description="the Search button")
    click_q = snapshot.build_microtask_questions(click_task)
    assert "click_target" in click_q
    assert "fill_target" not in click_q
    assert "e1" in click_q["click_target"].criteria
    assert "NO_ACTIONABLE_TARGET" in click_q["click_target"].criteria

    # 2. FILL micro-task
    fill_task = SemanticMicroTask(operation="FILL", target_description="Departure City field", fill_value="Chennai")
    fill_q = snapshot.build_microtask_questions(fill_task)
    assert "fill_target" in fill_q
    assert "click_target" not in fill_q
    assert "e2" in fill_q["fill_target"].criteria

    # 3. SELECT micro-task
    select_task = SemanticMicroTask(operation="SELECT", target_description="country dropdown", select_option="Canada")
    select_q = snapshot.build_microtask_questions(select_task)
    assert "select_target" in select_q
    assert "select_option" in select_q
    assert "e3" in select_q["select_target"].criteria
    assert any("Canada" in opt for opt in select_q["select_option"].criteria.values())


def test_resolve_microtask_decision_and_margin():
    generator = CandidateGenerator()
    raw = [
        {"bid": "10", "tag": "button", "type": "button", "id": "btn-ok", "text": "OK"},
        {"bid": "11", "tag": "button", "type": "button", "id": "btn-cancel", "text": "Cancel"},
    ]
    snapshot = generator.generate_snapshot(raw, goal="Click OK")
    micro_task = SemanticMicroTask(operation="CLICK", target_description="OK button")

    # Case A: Clear confident choice with high margin
    resp_confident = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(
                choice="e1",
                probabilities={"e1": 0.94, "e2": 0.06},
                confidence=0.94,
            )
        },
    )
    grounded = snapshot.resolve_microtask_decision(resp_confident, micro_task, confidence_threshold=0.70, margin_threshold=0.10)
    assert grounded.abstained is False
    assert grounded.target_id == "e1"
    assert grounded.target_element is not None
    assert grounded.target_element.bid == "10"
    assert grounded.confidence == 0.94
    assert round(grounded.margin, 2) == 0.88

    # Case B: Low margin (ambiguous choice e1: 0.52 vs e2: 0.48) -> Abstain
    resp_ambiguous = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(
                choice="e1",
                probabilities={"e1": 0.52, "e2": 0.48},
                confidence=0.52,
            )
        },
    )
    grounded_ambig = snapshot.resolve_microtask_decision(resp_ambiguous, micro_task, confidence_threshold=0.70, margin_threshold=0.10)
    assert grounded_ambig.abstained is True
    assert "LOW_CONFIDENCE" in grounded_ambig.abstain_reason or "LOW_MARGIN" in grounded_ambig.abstain_reason

    # Case C: NO_ACTIONABLE_TARGET choice -> Clean abstention
    resp_none = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(
                choice="NO_ACTIONABLE_TARGET",
                probabilities={"NO_ACTIONABLE_TARGET": 0.99},
                confidence=0.99,
            )
        },
    )
    grounded_none = snapshot.resolve_microtask_decision(resp_none, micro_task)
    assert grounded_none.abstained is True
    assert grounded_none.abstain_reason == "NO_ACTIONABLE_TARGET"


@pytest.mark.asyncio
async def test_shadow_observer_exact_microtask_telemetry():
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    expected_resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(
                choice="e1",
                probabilities={"e1": 0.95, "e2": 0.05},
                confidence=0.95,
            )
        },
        latency_ms=120.0,
    )
    client.register_mock_response("default", expected_resp)

    observer = JevShadowObserver(client=client)
    generator = CandidateGenerator()
    raw = [
        {"bid": "21", "tag": "button", "type": "submit", "id": "search-now", "text": "Search Now"},
        {"bid": "22", "tag": "button", "type": "reset", "id": "clear-all", "text": "Clear"},
    ]
    snapshot = generator.generate_snapshot(raw, goal="Click Search Now")

    # Pass explicit SemanticMicroTask
    micro_task = SemanticMicroTask(operation="CLICK", target_description="Search Now button")
    gemini_action = {"action": "click", "bid": "21"}

    rec = await observer.evaluate_observation_shadow(
        snapshot=snapshot,
        task_id="T02",
        task_name="Click Search",
        turn_index=1,
        gemini_action=gemini_action,
        gemini_latency_ms=3500.0,
        micro_task=micro_task,
    )

    assert rec.reference_agreement is True
    assert rec.semantic_correctness is True
    assert rec.abstained is False
    assert rec.wrong_target is False
    assert rec.jev_target_bid == "21"
    assert rec.reference_target_bid == "21"
    assert rec.operation == "CLICK"

    cal = observer.generate_calibration_curve()
    assert cal["reference_agreement_pct"] == 100.0
    assert cal["semantic_correctness_pct"] == 100.0
    assert cal["abstention_rate_pct"] == 0.0
    assert cal["wrong_target_rate_pct"] == 0.0
    assert cal["latency_saved_per_turn_ms"] == 3380.0
    assert cal["by_operation"]["CLICK"]["reference_agreement_pct"] == 100.0


def test_semantic_correctness_adjudication_distinction():
    generator = CandidateGenerator()
    raw = [
        {"bid": "1", "tag": "button", "id": "submit-btn", "text": "Submit Form"},
        {"bid": "2", "tag": "button", "id": "reset-btn", "text": "Reset"},
    ]
    snapshot = generator.generate_snapshot(raw, goal="Please click the Submit Form button")
    target_1 = snapshot.by_bid["1"]
    target_2 = snapshot.by_bid["2"]

    # When Jev matches Gemini on a successful task
    corr_match = adjudicate_semantic_correctness(
        goal=snapshot.goal,
        operation="CLICK",
        target_description="Submit Form",
        target_element=target_1,
        reference_bid="1",
        task_success=True,
    )
    assert corr_match is True

    # When Jev selects a target that directly matches the goal, even if reference was different
    corr_independent = adjudicate_semantic_correctness(
        goal=snapshot.goal,
        operation="CLICK",
        target_description="Submit Form button",
        target_element=target_1,
        reference_bid="2",
        task_success=False,
    )
    assert corr_independent is True

    # When Jev selects the wrong target
    corr_wrong = adjudicate_semantic_correctness(
        goal=snapshot.goal,
        operation="CLICK",
        target_description="Submit Form button",
        target_element=target_2,
        reference_bid="1",
        task_success=False,
    )
    assert corr_wrong is False


@pytest.mark.asyncio
async def test_calibration_populations_clean_separation():
    """
    Verifies that high-confidence NO_ACTIONABLE_TARGET decisions do NOT
    contaminate the target candidate selection calibration curve.
    """
    from benchmarks.jev_shadow import ShadowTurnRecord

    observer = JevShadowObserver()

    # Decision 1: Target candidate e1 selected with 0.99 conf -> matches reference, correct
    observer.records.append(
        ShadowTurnRecord(
            task_id="T01",
            task_name="t1",
            turn_index=1,
            epoch_id=1,
            goal="Click target",
            num_candidates=3,
            operation="CLICK",
            target_description="Crystal",
            reference_target_bid="3",
            jev_action="CLICK:e3",
            jev_target_bid="3",
            jev_confidence=0.99,
            jev_margin=0.98,
            reference_agreement=True,
            semantic_correctness=True,
            abstained=False,
            decision_type="TARGET",
            resolution_status="RESOLVED",
            confidence_type="TARGET_CONFIDENCE",
            gemini_latency_ms=4000.0,
            jev_latency_ms=900.0,
        )
    )

    # Decision 2: High confidence (0.97) NO_ACTIONABLE_TARGET -> Abstained
    observer.records.append(
        ShadowTurnRecord(
            task_id="T02",
            task_name="t2",
            turn_index=1,
            epoch_id=1,
            goal="Click button",
            num_candidates=3,
            operation="CLICK",
            target_description="button id ''",
            reference_target_bid="1",
            jev_action="CLICK:none",
            jev_target_bid=None,
            jev_confidence=0.97,
            jev_margin=0.97,
            reference_agreement=False,
            semantic_correctness=False,
            abstained=True,
            abstain_reason="NO_ACTIONABLE_TARGET",
            decision_type="NO_ACTIONABLE_TARGET",
            resolution_status="ABSTAINED",
            confidence_type="ABSTENTION_CONFIDENCE",
            gemini_latency_ms=4000.0,
            jev_latency_ms=900.0,
        )
    )

    # Decision 3: Low confidence (0.40) target choice -> Abstained
    observer.records.append(
        ShadowTurnRecord(
            task_id="T03",
            task_name="t3",
            turn_index=1,
            epoch_id=1,
            goal="Fill name",
            num_candidates=2,
            operation="FILL",
            target_description="field id 'tt'",
            reference_target_bid="1",
            jev_action="FILL:none",
            jev_target_bid=None,
            jev_confidence=0.40,
            jev_margin=0.30,
            reference_agreement=False,
            semantic_correctness=False,
            abstained=True,
            abstain_reason="LOW_CONFIDENCE (0.40 < 0.75)",
            decision_type="TARGET",
            resolution_status="ABSTAINED",
            confidence_type="TARGET_CONFIDENCE",
            gemini_latency_ms=4000.0,
            jev_latency_ms=900.0,
        )
    )

    cal = observer.generate_calibration_curve()

    # In target selection calibration:
    # 0.95-1.00 bin must have ONLY 1 decision (the candidate e3 choice), NOT contaminated by Decision 2!
    bin_95_100 = cal["target_selection_calibration"]["0.95-1.00"]
    assert bin_95_100["total_decisions"] == 1
    assert bin_95_100["resolved_decisions"] == 1
    assert bin_95_100["reference_agreement_pct"] == 100.0
    assert bin_95_100["semantic_correctness_pct"] == 100.0

    # In abstention analysis: Decision 2 and 3 must appear cleanly
    abst_analysis = cal["abstention_analysis"]
    assert "NO_ACTIONABLE_TARGET" in abst_analysis
    assert abst_analysis["NO_ACTIONABLE_TARGET"]["count"] == 1
    assert abst_analysis["NO_ACTIONABLE_TARGET"]["avg_confidence"] == 0.97
    assert "LOW_CONFIDENCE" in abst_analysis
    assert abst_analysis["LOW_CONFIDENCE"]["count"] == 1

    # Precision@Execute: 1 resolved out of 1 allowed -> 100.0%
    assert cal["precision_at_execute_pct"] == 100.0
    # Coverage: 1 allowed out of 3 total -> 33.3%
    assert cal["coverage_pct"] == 33.3
