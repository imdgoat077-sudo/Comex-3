"""
Tests for DecisionPolicyManager.
"""

from services.decision_policy import DecisionPolicyManager, RiskTier


def test_decision_policy_risk_classification():
    mgr = DecisionPolicyManager()

    assert mgr.classify_risk("click", "Close tab") == RiskTier.ROUTINE
    assert mgr.classify_risk("type", "Search city") == RiskTier.INTERACTIVE_INPUT
    assert mgr.classify_risk("click", "Submit search") == RiskTier.FORM_SUBMISSION
    assert mgr.classify_risk("click", "Delete account permanently") == RiskTier.DESTRUCTIVE
    assert mgr.classify_risk("complete", "Done") == RiskTier.COMPLETION


def test_decision_policy_evaluation_thresholds():
    mgr = DecisionPolicyManager()

    # Routine requires 0.65
    auth, tier, reason = mgr.evaluate("scroll", confidence=0.70)
    assert auth is True
    assert tier == RiskTier.ROUTINE

    # Interactive input requires 0.75
    auth, tier, reason = mgr.evaluate("type", confidence=0.70)
    assert auth is False
    assert tier == RiskTier.INTERACTIVE_INPUT

    auth, tier, reason = mgr.evaluate("type", confidence=0.80)
    assert auth is True

    # Form submission requires 0.85
    auth, tier, reason = mgr.evaluate("click", confidence=0.80, target_desc="Submit form")
    assert auth is False
    assert tier == RiskTier.FORM_SUBMISSION

    # Destructive requires confirmation
    auth, tier, reason = mgr.evaluate("click", confidence=0.99, target_desc="Delete database")
    assert auth is False
    assert tier == RiskTier.DESTRUCTIVE
    assert "requires explicit user confirmation" in reason
