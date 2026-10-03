"""
Unit tests for Multi-Head Jev Questions, State Fingerprint, and Epoch Invariant.
"""

import pytest
from services.candidate_generator import (
    CandidateElement,
    CandidateGenerator,
    MultiHeadDecision,
    ObservationSnapshot,
)
from services.typesafe_client import ChoiceAnswer, SystemOneResponse


def test_multihead_questions_structure():
    gen = CandidateGenerator()
    elements = [
        {"bid": "1", "tag": "button", "text": "Search Flights", "center": {"x": 100, "y": 200}},
        {"bid": "2", "tag": "input", "type": "text", "label": "From City", "center": {"x": 150, "y": 250}},
        {"bid": "3", "tag": "select", "label": "Ticket Class", "options": ["Economy", "Business"]},
    ]
    snap = gen.generate_snapshot(elements, goal="Book flight from Zurich to London")

    assert snap.epoch_id == 1
    assert len(snap.state_fingerprint) == 64  # SHA-256 hex string

    questions = snap.build_multihead_questions()
    assert "operation" in questions
    assert "click_target" in questions
    assert "fill_target" in questions
    assert "select_target" in questions
    assert "wait_reason" in questions

    # Check criteria content
    assert "CLICK" in questions["operation"].criteria
    assert "FILL" in questions["operation"].criteria
    assert "WAIT" in questions["operation"].criteria
    assert "DONE" in questions["operation"].criteria

    # Target heads contain candidate indices
    assert "e1" in questions["click_target"].criteria
    assert "e2" in questions["fill_target"].criteria
    assert "e3" in questions["select_target"].criteria

    # Semantic wait criteria
    assert "WAIT_FOR_SUGGESTION" in questions["wait_reason"].criteria
    assert "WAIT_FOR_NAVIGATION" in questions["wait_reason"].criteria


def test_empty_candidates_guard_no_actionable_target():
    """Verify that when a category has 0 candidates, NO_ACTIONABLE_TARGET is emitted."""
    gen = CandidateGenerator()
    # Elements have only buttons, no inputs or selects
    elements = [
        {"bid": "1", "tag": "button", "text": "OK"},
    ]
    snap = gen.generate_snapshot(elements, goal="Click OK")
    questions = snap.build_multihead_questions()

    assert "e1" in questions["click_target"].criteria
    assert "NO_ACTIONABLE_TARGET" in questions["fill_target"].criteria
    assert "NO_ACTIONABLE_TARGET" in questions["select_target"].criteria
    # Zero empty criteria maps
    for qid, q in questions.items():
        assert len(q.criteria) > 0, f"Question '{qid}' must not have empty criteria"


def test_resolve_multihead_decision_click():
    gen = CandidateGenerator()
    elements = [
        {"bid": "1", "tag": "button", "text": "Submit"},
        {"bid": "2", "tag": "input", "type": "text", "label": "Name"},
    ]
    snap = gen.generate_snapshot(elements, goal="Submit form")

    # Simulate SystemOneResponse
    response = SystemOneResponse(
        model="jev-latest",
        answers={
            "operation": ChoiceAnswer(choice="CLICK", probabilities={"CLICK": 0.95, "FILL": 0.05}, confidence=0.95),
            "click_target": ChoiceAnswer(choice="e1", probabilities={"e1": 0.92, "e2": 0.08}, confidence=0.92),
            "fill_target": ChoiceAnswer(choice="e2", probabilities={"e2": 0.88}, confidence=0.88),
            "wait_reason": ChoiceAnswer(choice="WAIT_FOR_QUIESCENCE", probabilities={}, confidence=0.5),
        },
    )

    decision = snap.resolve_multihead_decision(response)
    assert decision.operation == "CLICK"
    assert decision.target is not None
    assert decision.target.candidate_id == "e1"
    assert decision.target.bid == "1"
    assert decision.epoch_id == snap.epoch_id
    assert decision.confidence == 0.92  # min(0.95, 0.92)


def test_resolve_multihead_decision_wait_semantic():
    gen = CandidateGenerator()
    snap = gen.generate_snapshot([], goal="Wait for combobox")

    response = SystemOneResponse(
        model="jev-latest",
        answers={
            "operation": ChoiceAnswer(choice="WAIT", probabilities={"WAIT": 0.90}, confidence=0.90),
            "click_target": ChoiceAnswer(choice="NO_ACTIONABLE_TARGET", probabilities={}, confidence=0.1),
            "fill_target": ChoiceAnswer(choice="NO_ACTIONABLE_TARGET", probabilities={}, confidence=0.1),
            "select_target": ChoiceAnswer(choice="NO_ACTIONABLE_TARGET", probabilities={}, confidence=0.1),
            "wait_reason": ChoiceAnswer(choice="WAIT_FOR_SUGGESTION", probabilities={"WAIT_FOR_SUGGESTION": 0.95}, confidence=0.95),
        },
    )

    decision = snap.resolve_multihead_decision(response)
    assert decision.operation == "WAIT"
    assert decision.target is None
    assert decision.wait_reason == "WAIT_FOR_SUGGESTION"


def test_epoch_and_fingerprint_freshness():
    gen = CandidateGenerator()
    elements = [{"bid": "1", "tag": "button", "text": "Next"}]
    snap1 = gen.generate_snapshot(elements, goal="Click Next", url="http://test/1")
    snap2 = gen.generate_snapshot(elements, goal="Click Next", url="http://test/2")

    assert snap1.epoch_id == 1
    assert snap2.epoch_id == 2
    assert snap1.state_fingerprint != snap2.state_fingerprint

    # snap1 rejects decisions from epoch 2
    assert snap1.validate_candidate("e1", caller_epoch_id=2) is None
    assert snap1.validate_candidate("e1", caller_epoch_id=1) is not None

    # is_fresh check
    assert snap1.is_fresh(snap1.state_fingerprint, caller_epoch_id=1) is True
    assert snap1.is_fresh(snap1.state_fingerprint, caller_epoch_id=2) is False
    assert snap1.is_fresh("different_fingerprint", caller_epoch_id=1) is False
