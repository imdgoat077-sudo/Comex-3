"""
tests/test_action_ledger.py
===========================
Unit tests for Phase 2A Action-Level Telemetry Ledger,
Wilson score confidence intervals, Comex block taxonomy, and ConsistencyGuard.
"""

import pytest
from benchmarks.action_ledger import (
    ActionLedger,
    ActionRecord,
    ComexBlockCategory,
    ComexDecisionMetadata,
    CorrectnessMetadata,
    CorrectnessSource,
    ExecutionMetadata,
    JevDecisionMetadata,
    LatencyBreakdown,
    RoutingMetadata,
    SemanticMicroTaskRecord,
    calculate_wilson_score_interval,
)


def test_wilson_score_interval():
    # Small sample (11/11): Wilson score lower bound is ~0.7412
    lower_11, upper_11 = calculate_wilson_score_interval(11, 11)
    assert round(lower_11, 3) == 0.741
    assert upper_11 == 1.0

    # Large sample (110/110): Wilson score lower bound is ~0.9663
    lower_110, upper_110 = calculate_wilson_score_interval(110, 110)
    assert round(lower_110, 3) == 0.966
    assert upper_110 == 1.0

    # 0 trials
    lower_0, upper_0 = calculate_wilson_score_interval(0, 0)
    assert lower_0 == 0.0
    assert upper_0 == 0.0


def test_multi_action_turn_separation():
    ledger = ActionLedger(run_id="run-test", arm="C")

    # Turn 1 has 3 distinct browser actions (e.g. T10 Login User: fill user, fill pass, click submit)
    act1 = ActionRecord(
        run_id="run-test",
        arm="C",
        task_id="T10",
        task_name="login-user",
        turn_id=1,
        action_id=1,
        operation="FILL",
        semantic_microtask=SemanticMicroTaskRecord(operation="FILL", target_description="username", fill_value="user1"),
        routing=RoutingMetadata(route="JEV"),
        jev=JevDecisionMetadata(attempted=True, decision_type="TARGET", target_id="e2", confidence=0.82, margin=0.76, status="RESOLVED"),
        correctness=CorrectnessMetadata(evaluation_source=CorrectnessSource.MANUAL_ADJUDICATION, is_correct=True),
        execution=ExecutionMetadata(physical_dispatch=True, dispatch_success=True),
        latency=LatencyBreakdown(gemini_request_ms=4000.0, jev_request_ms=1000.0, model_latency_avoided_ms=3000.0),
    )

    act2 = ActionRecord(
        run_id="run-test",
        arm="C",
        task_id="T10",
        task_name="login-user",
        turn_id=1,
        action_id=2,
        operation="FILL",
        semantic_microtask=SemanticMicroTaskRecord(operation="FILL", target_description="password", fill_value="pass1"),
        routing=RoutingMetadata(route="GEMINI"),
        jev=JevDecisionMetadata(attempted=True, decision_type="TARGET", target_id="e4", confidence=0.88, margin=0.84, status="RESOLVED"),
        correctness=CorrectnessMetadata(evaluation_source=CorrectnessSource.MANUAL_ADJUDICATION, is_correct=True),
        execution=ExecutionMetadata(physical_dispatch=True, dispatch_success=True),
        latency=LatencyBreakdown(gemini_request_ms=4000.0, jev_request_ms=1000.0, model_latency_avoided_ms=0.0),
    )

    act3 = ActionRecord(
        run_id="run-test",
        arm="C",
        task_id="T10",
        task_name="login-user",
        turn_id=1,
        action_id=3,
        operation="CLICK",
        semantic_microtask=SemanticMicroTaskRecord(operation="CLICK", target_description="login button"),
        routing=RoutingMetadata(route="GEMINI"),
        jev=JevDecisionMetadata(attempted=False),
        execution=ExecutionMetadata(physical_dispatch=True, dispatch_success=True),
        latency=LatencyBreakdown(gemini_request_ms=4000.0, model_latency_avoided_ms=0.0),
    )

    ledger.record_action(act1)
    ledger.record_action(act2)
    ledger.record_action(act3)

    turn_metrics = ledger.compute_turn_metrics()
    assert turn_metrics["total_gemini_turns"] == 1
    assert turn_metrics["jev_delegated_turns"] == 1

    action_metrics = ledger.compute_action_metrics()
    assert action_metrics["total_browser_actions"] == 3
    assert action_metrics["jev_attempted_actions"] == 2
    assert action_metrics["jev_resolved_actions"] == 2
    assert action_metrics["jev_executed_actions"] == 1
    assert action_metrics["gemini_executed_actions"] == 2
    assert action_metrics["delegated_decisions_count"] == 1
    assert action_metrics["correct_delegated_count"] == 1
    assert action_metrics["wrong_target_count"] == 0
    assert action_metrics["observed_precision_pct"] == 100.0


def test_comex_block_taxonomy_separation():
    ledger = ActionLedger(run_id="run-test", arm="C")

    # Safety block: Late occlusion
    act_safety = ActionRecord(
        run_id="run-test",
        arm="C",
        task_id="T15",
        task_name="book-flight",
        turn_id=2,
        action_id=1,
        operation="CLICK",
        comex=ComexDecisionMetadata(
            status="BLOCK",
            block_category=ComexBlockCategory.LATE_OCCLUSION,
            block_reason="Element occluded by datepicker overlay",
            is_physical_safety=True,
            is_loop_prevention=False,
            occlusion="OCCLUDED",
        ),
    )

    # Loop prevention block: Loop breaker repeated action
    act_loop = ActionRecord(
        run_id="run-test",
        arm="C",
        task_id="T15",
        task_name="book-flight",
        turn_id=3,
        action_id=1,
        operation="CLICK",
        comex=ComexDecisionMetadata(
            status="BLOCK",
            block_category=ComexBlockCategory.LOOP_BREAK,
            block_reason="Action produced zero state delta previously",
            is_physical_safety=False,
            is_loop_prevention=True,
        ),
    )

    ledger.record_action(act_safety)
    ledger.record_action(act_loop)

    action_metrics = ledger.compute_action_metrics()
    assert action_metrics["comex_total_blocks"] == 2
    assert action_metrics["comex_physical_safety_blocks"] == 1
    assert action_metrics["comex_loop_prevention_blocks"] == 1
    assert action_metrics["comex_block_breakdown"]["LATE_OCCLUSION"] == 1
    assert action_metrics["comex_block_breakdown"]["LOOP_BREAK"] == 1


def test_consistency_guard_validation():
    ledger = ActionLedger(run_id="run-test", arm="C")

    # Add a delegated record
    ledger.record_action(
        ActionRecord(
            run_id="run-test",
            arm="C",
            task_id="T01",
            task_name="click-test",
            turn_id=1,
            action_id=1,
            operation="CLICK",
            routing=RoutingMetadata(route="JEV"),
            jev=JevDecisionMetadata(attempted=True, status="RESOLVED"),
            correctness=CorrectnessMetadata(is_correct=True),
            latency=LatencyBreakdown(model_latency_avoided_ms=2500.0),
        )
    )

    # Correct summary validation succeeds
    ledger.validate_consistency(
        reported_delegated_count=1,
        reported_precision_denom=1,
        reported_action_coverage_num=1,
        reported_total_avoided_s=2.50,
    )

    # Mismatch in delegated count raises AssertionError
    with pytest.raises(AssertionError, match="CONSISTENCY VIOLATION: reported_delegated_count"):
        ledger.validate_consistency(
            reported_delegated_count=2,  # Intentional discrepancy
            reported_precision_denom=1,
            reported_action_coverage_num=1,
            reported_total_avoided_s=2.50,
        )

    # Mismatch in precision denominator raises AssertionError
    with pytest.raises(AssertionError, match="CONSISTENCY VIOLATION: reported_precision_denom"):
        ledger.validate_consistency(
            reported_delegated_count=1,
            reported_precision_denom=12,  # 12/12 discrepancy guard
            reported_action_coverage_num=1,
            reported_total_avoided_s=2.50,
        )
