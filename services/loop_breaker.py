"""
Production Action-State Loop Breaker and Recovery Controller.

Prevents the agent from spinning in non-mutating action loops (e.g. repeated
clicks on dead/unresponsive buttons like T14 'click Reply' failure) by tracking
state deltas between consecutive turns.

Guarantees (Two-Tier Contract):
1. Tier 1 (Immediate Action Blacklist):
   - When an action produces zero DOM/state delta (fingerprint_before == fingerprint_after),
     that exact action signature becomes immediately blacklisted for that unchanged fingerprint.
   - If the agent immediately attempts the exact same action on that state, it is blocked
     fail-closed as BLOCK_REPEATED_ACTION.
2. Tier 2 (Consecutive Zero-Delta Escalation):
   - When 2 consecutive non-wait actions both produce zero delta (regardless of target),
     the loop breaker triggers ESCALATE_TO_SYSTEM2 to force global strategy reconsideration.
3. Wait Distinction:
   - Distinguishes intentional WAIT operations from stalled UI actions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("comex.services.loop_breaker")


class RecoveryAction(Enum):
    CONTINUE = "CONTINUE"
    BLOCK_REPEATED_ACTION = "BLOCK_REPEATED_ACTION"
    REOBSERVE_ALTERNATE_TARGET = "REOBSERVE_ALTERNATE_TARGET"
    ESCALATE_TO_SYSTEM2 = "ESCALATE_TO_SYSTEM2"


@dataclass
class StepRecord:
    turn: int
    action_type: str
    target_id: str
    text_content: Optional[str]
    fingerprint_before: str
    fingerprint_after: Optional[str] = None
    state_changed: Optional[bool] = None

    @property
    def action_signature(self) -> str:
        sig = f"{self.action_type}:{self.target_id}"
        if self.text_content:
            sig += f":{self.text_content}"
        return sig

    @property
    def is_wait(self) -> bool:
        return self.action_type.lower() in ("wait", "wait_for_state", "wait_quiescence")


class ActionLoopBreaker:
    """Tracks state deltas across turns and detects unhandled loops."""

    def __init__(self, max_consecutive_zero_deltas: int = 2) -> None:
        self.max_consecutive_zero_deltas = max_consecutive_zero_deltas
        self.history: List[StepRecord] = []
        self._blocked_signatures_for_fingerprint: Dict[str, set[str]] = {}

    def record_step_start(
        self,
        turn: int,
        action: Dict[str, Any],
        fingerprint_before: str,
    ) -> StepRecord:
        """Records the beginning of an action execution with pre-action state fingerprint."""
        act_type = str(action.get("action", "")).lower()
        target = str(action.get("bid") or action.get("candidate_id") or action.get("selector") or "")
        text = action.get("text")

        record = StepRecord(
            turn=turn,
            action_type=act_type,
            target_id=target,
            text_content=str(text) if text is not None else None,
            fingerprint_before=fingerprint_before,
        )
        self.history.append(record)
        return record

    def record_step_end(
        self,
        record: StepRecord,
        fingerprint_after: str,
    ) -> bool:
        """
        Records the post-action state fingerprint and checks whether state changed.
        Returns True if state changed, False if zero delta.
        """
        record.fingerprint_after = fingerprint_after
        changed = (fingerprint_after != record.fingerprint_before)
        record.state_changed = changed

        if not changed and not record.is_wait:
            # Action had zero effect on the page. Remember this signature for this state!
            sig_set = self._blocked_signatures_for_fingerprint.setdefault(record.fingerprint_before, set())
            sig_set.add(record.action_signature)
            logger.warning(
                f"Zero delta detected on turn {record.turn} for {record.action_signature}. "
                f"Fingerprint remains {record.fingerprint_before[:12]}..."
            )

        return changed

    def evaluate_proposed_action(
        self,
        proposed_action: Dict[str, Any],
        current_fingerprint: str,
    ) -> Tuple[bool, RecoveryAction, Optional[str]]:
        """
        Evaluates a proposed action against loop history.
        Returns:
            (is_blocked: bool, recovery_action: RecoveryAction, reason: Optional[str])
        """
        act_type = str(proposed_action.get("action", "")).lower()
        target = str(proposed_action.get("bid") or proposed_action.get("candidate_id") or proposed_action.get("selector") or "")
        text = proposed_action.get("text")
        proposed_sig = f"{act_type}:{target}"
        if text is not None:
            proposed_sig += f":{text}"

        # 1. Check if this exact action was already executed on this exact fingerprint with ZERO delta
        blocked_sigs = self._blocked_signatures_for_fingerprint.get(current_fingerprint, set())
        if proposed_sig in blocked_sigs:
            reason = (
                f"Loop Breaker blocked repeated action '{proposed_sig}'. "
                f"This action already produced zero state change on fingerprint {current_fingerprint[:12]}."
            )
            logger.error(reason)
            return True, RecoveryAction.BLOCK_REPEATED_ACTION, reason

        # 2. Check consecutive zero-delta streak across recent non-wait actions
        recent_non_waits = [s for s in self.history if not s.is_wait and s.state_changed is not None]
        if len(recent_non_waits) >= self.max_consecutive_zero_deltas:
            consecutive_zero = all(s.state_changed is False for s in recent_non_waits[-self.max_consecutive_zero_deltas:])
            if consecutive_zero:
                reason = (
                    f"Loop Breaker detected {self.max_consecutive_zero_deltas} consecutive zero-delta actions. "
                    "Page appears stalled or action outcomes are unobserved. Escalating."
                )
                logger.error(reason)
                return True, RecoveryAction.ESCALATE_TO_SYSTEM2, reason

        return False, RecoveryAction.CONTINUE, None

    def reset(self) -> None:
        self.history.clear()
        self._blocked_signatures_for_fingerprint.clear()
