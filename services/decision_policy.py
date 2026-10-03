"""
Risk-Aware Decision Policy for Comex Browser.

Maps action types and contextual risk tiers to calibrated confidence thresholds.
Enforces:
  - Routine actions (scroll, navigation, focus) have lower barriers.
  - Form submissions and state mutations require higher confidence.
  - Destructive / high-stakes actions require extreme confidence or human confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, Optional, Tuple


class RiskTier(Enum):
    ROUTINE = "ROUTINE"                     # Read-only, tab focus, scroll
    INTERACTIVE_INPUT = "INTERACTIVE_INPUT" # Filling search boxes, clicking non-submitting links
    FORM_SUBMISSION = "FORM_SUBMISSION"     # Submitting searches, applying filters, booking
    DESTRUCTIVE = "DESTRUCTIVE"             # Account deletion, purchases, sending real emails
    COMPLETION = "COMPLETION"               # Declaring goal complete


@dataclass(frozen=True)
class PolicyRule:
    risk_tier: RiskTier
    min_confidence: float
    require_confirmation: bool = False


DEFAULT_RISK_POLICIES: Dict[RiskTier, PolicyRule] = {
    RiskTier.ROUTINE: PolicyRule(risk_tier=RiskTier.ROUTINE, min_confidence=0.65),
    RiskTier.INTERACTIVE_INPUT: PolicyRule(risk_tier=RiskTier.INTERACTIVE_INPUT, min_confidence=0.75),
    RiskTier.FORM_SUBMISSION: PolicyRule(risk_tier=RiskTier.FORM_SUBMISSION, min_confidence=0.85),
    RiskTier.COMPLETION: PolicyRule(risk_tier=RiskTier.COMPLETION, min_confidence=0.90),
    RiskTier.DESTRUCTIVE: PolicyRule(risk_tier=RiskTier.DESTRUCTIVE, min_confidence=0.95, require_confirmation=True),
}


class DecisionPolicyManager:
    """Evaluates whether a candidate action and confidence score meet policy authorization."""

    def __init__(self, overrides: Optional[Dict[RiskTier, PolicyRule]] = None) -> None:
        self.policies = dict(DEFAULT_RISK_POLICIES)
        if overrides:
            self.policies.update(overrides)

    def classify_risk(self, action_type: str, target_desc: str = "") -> RiskTier:
        act = action_type.lower()
        desc = target_desc.lower()

        # Destructive patterns
        if any(w in desc for w in ("delete", "remove", "pay", "buy", "purchase", "send message", "send email")):
            return RiskTier.DESTRUCTIVE

        # Completion
        if act in ("complete", "done"):
            return RiskTier.COMPLETION

        # Form submissions
        if act in ("submit",) or (act == "click" and any(w in desc for w in ("submit", "search", "book", "apply", "login"))):
            return RiskTier.FORM_SUBMISSION

        # Inputs
        if act in ("type", "fill", "select"):
            return RiskTier.INTERACTIVE_INPUT

        # Routine
        if act in ("scroll", "focus", "wait") or (act == "click" and any(w in desc for w in ("tab", "menu", "close", "expand"))):
            return RiskTier.ROUTINE

        # Default fallback
        return RiskTier.INTERACTIVE_INPUT

    def evaluate(
        self,
        action_type: str,
        confidence: float,
        target_desc: str = "",
        force_destructive: bool = False,
    ) -> Tuple[bool, RiskTier, str]:
        """
        Returns (is_authorized, risk_tier, reason).
        """
        tier = RiskTier.DESTRUCTIVE if force_destructive else self.classify_risk(action_type, target_desc)
        rule = self.policies.get(tier, DEFAULT_RISK_POLICIES[RiskTier.INTERACTIVE_INPUT])

        if rule.require_confirmation:
            return False, tier, f"Action in {tier.value} tier requires explicit user confirmation."

        if confidence < rule.min_confidence:
            return (
                False,
                tier,
                f"Confidence {confidence:.2f} is below policy threshold {rule.min_confidence:.2f} for {tier.value}.",
            )

        return True, tier, f"Authorized under {tier.value} policy (conf={confidence:.2f} >= {rule.min_confidence:.2f})."
