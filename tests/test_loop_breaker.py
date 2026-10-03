"""
Unit tests for ActionLoopBreaker service.
"""

import pytest
from services.loop_breaker import ActionLoopBreaker, RecoveryAction


def test_loop_breaker_allows_normal_mutating_actions():
    breaker = ActionLoopBreaker()
    fp1 = "fp_initial_state"
    fp2 = "fp_after_click_next"

    # Turn 1: Click button 1
    action1 = {"action": "click", "bid": "1"}
    blocked, rec, reason = breaker.evaluate_proposed_action(action1, current_fingerprint=fp1)
    assert not blocked
    assert rec == RecoveryAction.CONTINUE

    rec_step = breaker.record_step_start(turn=1, action=action1, fingerprint_before=fp1)
    changed = breaker.record_step_end(rec_step, fingerprint_after=fp2)
    assert changed is True


def test_loop_breaker_blocks_repeated_zero_delta_action():
    """Simulates T14 email failure: clicking Reply repeatedly with no page change."""
    breaker = ActionLoopBreaker()
    fp_stalled = "fp_email_open"

    # Turn 1: Click Reply (bid 5)
    reply_action = {"action": "click", "bid": "5"}
    blocked, rec, reason = breaker.evaluate_proposed_action(reply_action, current_fingerprint=fp_stalled)
    assert not blocked

    step1 = breaker.record_step_start(turn=1, action=reply_action, fingerprint_before=fp_stalled)
    # Page did not change!
    breaker.record_step_end(step1, fingerprint_after=fp_stalled)

    # Turn 2: Agent tries to click Reply again!
    blocked2, rec2, reason2 = breaker.evaluate_proposed_action(reply_action, current_fingerprint=fp_stalled)
    assert blocked2 is True
    assert rec2 == RecoveryAction.BLOCK_REPEATED_ACTION
    assert "Loop Breaker blocked repeated action" in reason2


def test_loop_breaker_escalates_on_consecutive_zero_deltas():
    breaker = ActionLoopBreaker(max_consecutive_zero_deltas=2)
    fp = "fp_frozen_screen"

    # Action 1 fails
    act1 = {"action": "click", "bid": "1"}
    s1 = breaker.record_step_start(1, act1, fp)
    breaker.record_step_end(s1, fp)

    # Action 2 on different element also produces zero delta
    act2 = {"action": "click", "bid": "2"}
    s2 = breaker.record_step_start(2, act2, fp)
    breaker.record_step_end(s2, fp)

    # Action 3 triggers escalation
    act3 = {"action": "click", "bid": "3"}
    blocked, rec, reason = breaker.evaluate_proposed_action(act3, current_fingerprint=fp)
    assert blocked is True
    assert rec == RecoveryAction.ESCALATE_TO_SYSTEM2


def test_loop_breaker_distinguishes_wait():
    breaker = ActionLoopBreaker(max_consecutive_zero_deltas=2)
    fp = "fp_loading_screen"

    # Wait action
    act_wait = {"action": "wait", "bid": ""}
    s1 = breaker.record_step_start(1, act_wait, fp)
    breaker.record_step_end(s1, fp)

    # A subsequent action should NOT be blocked as a zero-delta streak just because wait was called
    act2 = {"action": "click", "bid": "1"}
    blocked, rec, _ = breaker.evaluate_proposed_action(act2, current_fingerprint=fp)
    assert not blocked
