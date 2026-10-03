"""
Micro-Action Resolver for Comex Browser (Phase 2 Controlled Delegation Pilot).

Core Invariants:
1. Frozen Nervous System: Comex remains the sole authorization and actuation authority.
   Jev produces candidate target grounding recommendations ONLY. Jev NEVER executes CDP commands.
2. Bounded Operations: Phase 2 delegation is strictly limited to CLICK and FILL.
   SELECT, SCROLL, WAIT, and DONE are strictly non-delegated (shadow-only / Gemini-owned).
3. Fail-Closed for Jev / Fail-Forward for Agent:
   Any Jev low confidence (< tau_op), low margin (< mu_op), NO_ACTIONABLE_TARGET choice,
   timeout (> 1200ms), or network error immediately yields cleanly to Gemini System 2.
4. Independent Comex Actuation Gates:
   Before any physical click or fill, Comex validates:
   - Snapshot epoch freshness (epoch_id == current_epoch)
   - TargetBinding integrity (bid exists in live DOM)
   - Method E coordinate resolution
   - Late Occlusion Check via elementFromPoint(cx, cy)
   - ActuatorGate quiescence & safe overlay policy
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from services.candidate_generator import (
    CandidateElement,
    GroundedAction,
    ObservationSnapshot,
    SemanticMicroTask,
)
from services.typesafe_client import (
    AsyncTypeSafeClient,
    ClientMode,
    JevServiceUnavailableError,
)

logger = logging.getLogger("comex.services.micro_resolver")

# Calibrated Phase 2 thresholds
CALIBRATION_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "CLICK": {"confidence": 0.70, "margin": 0.05},
    "FILL": {"confidence": 0.75, "margin": 0.10},
    "SELECT": {"confidence": 0.70, "margin": 0.05},  # Kept for shadow mode evaluation
}

ALLOWED_DELEGATION_OPERATIONS: Set[str] = {"CLICK", "FILL"}


class DelegationStatus(Enum):
    RESOLVED = "RESOLVED"
    ABSTAINED_OPERATION_DISALLOWED = "ABSTAINED_OPERATION_DISALLOWED"
    ABSTAINED_NO_CANDIDATES = "ABSTAINED_NO_CANDIDATES"
    ABSTAINED_NO_ACTIONABLE_TARGET = "ABSTAINED_NO_ACTIONABLE_TARGET"
    ABSTAINED_LOW_CONFIDENCE = "ABSTAINED_LOW_CONFIDENCE"
    ABSTAINED_LOW_MARGIN = "ABSTAINED_LOW_MARGIN"
    ABSTAINED_TIMEOUT = "ABSTAINED_TIMEOUT"
    ABSTAINED_SERVICE_ERROR = "ABSTAINED_SERVICE_ERROR"
    ABSTAINED_UNKNOWN_CANDIDATE = "ABSTAINED_UNKNOWN_CANDIDATE"


@dataclass(frozen=True)
class DelegationResolution:
    """Outcome of attempting to resolve a SemanticMicroTask through Jev System 1."""
    status: DelegationStatus
    action: Optional[GroundedAction] = None
    target_element: Optional[CandidateElement] = None
    latency_ms: float = 0.0
    reason: str = ""

    @property
    def can_delegate(self) -> bool:
        return self.status == DelegationStatus.RESOLVED and self.action is not None and not self.action.abstained


class MicroActionResolver:
    """
    Evaluates Gemini's SemanticMicroTask against an ObservationSnapshot using Jev System 1.
    Strictly enforces safety gating: only allows delegation for CLICK and FILL meeting
    the calibrated confidence and margin criteria.
    """

    def __init__(
        self,
        client: Optional[AsyncTypeSafeClient] = None,
        timeout_ms: Optional[float] = None,
        allowed_operations: Optional[Set[str]] = None,
        thresholds: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> None:
        self.client = client or AsyncTypeSafeClient()
        self.timeout_ms = timeout_ms if timeout_ms is not None else float(os.getenv("TYPESAFE_TIMEOUT_MS", "1200.0"))
        self.allowed_operations = allowed_operations or set(ALLOWED_DELEGATION_OPERATIONS)
        self.thresholds = thresholds or dict(CALIBRATION_THRESHOLDS)

    async def resolve(
        self,
        snapshot: ObservationSnapshot,
        micro_task: SemanticMicroTask,
    ) -> DelegationResolution:
        """
        Attempts to resolve the target of a SemanticMicroTask.
        Returns DelegationResolution with status and GroundedAction.
        """
        t0 = time.perf_counter()
        op = micro_task.operation.upper()

        # Gate 1: Operation Authority Check
        # Gemini owns the operation; only CLICK and FILL are allowed for Jev delegation in Phase 2
        if op not in self.allowed_operations:
            lat = (time.perf_counter() - t0) * 1000.0
            reason = f"Operation '{op}' is not approved for delegation (SELECT stays shadow-only)"
            logger.info(f"MicroActionResolver: {reason}")
            action = GroundedAction(
                operation=op,
                target_id=None,
                target_element=None,
                confidence=0.0,
                margin=0.0,
                epoch_id=snapshot.epoch_id,
                abstained=True,
                abstain_reason=reason,
                decision_type="NONE",
                resolution_status="ABSTAINED",
                confidence_type="TARGET_CONFIDENCE",
            )
            return DelegationResolution(
                status=DelegationStatus.ABSTAINED_OPERATION_DISALLOWED,
                action=action,
                latency_ms=lat,
                reason=reason,
            )

        # Gate 2: Candidate Snapshot Non-Empty
        if not snapshot.candidates:
            lat = (time.perf_counter() - t0) * 1000.0
            reason = "No candidate elements available in snapshot"
            action = GroundedAction(
                operation=op,
                target_id=None,
                target_element=None,
                confidence=0.0,
                margin=0.0,
                epoch_id=snapshot.epoch_id,
                abstained=True,
                abstain_reason=reason,
                decision_type="NONE",
                resolution_status="ABSTAINED",
                confidence_type="TARGET_CONFIDENCE",
            )
            return DelegationResolution(
                status=DelegationStatus.ABSTAINED_NO_CANDIDATES,
                action=action,
                latency_ms=lat,
                reason=reason,
            )

        # Gate 3: Query Jev System 1 with Strict Timeout
        questions = snapshot.build_microtask_questions(micro_task)
        state_desc = {
            "goal": snapshot.goal,
            "url": snapshot.url,
            "operation": op,
            "target": micro_task.target_description,
        }
        if micro_task.fill_value:
            state_desc["fill_value"] = micro_task.fill_value
        if micro_task.select_option:
            state_desc["select_option"] = micro_task.select_option
        if snapshot.focused_snippet:
            state_desc["focused_snippet"] = snapshot.focused_snippet[:1000]

        try:
            resp = await asyncio.wait_for(
                self.client.system_one(state=state_desc, questions=questions),
                timeout=self.timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            lat = (time.perf_counter() - t0) * 1000.0
            reason = f"Jev System 1 query timed out after {self.timeout_ms:.0f}ms"
            logger.warning(f"MicroActionResolver: {reason}")
            action = GroundedAction(
                operation=op,
                target_id=None,
                target_element=None,
                confidence=0.0,
                margin=0.0,
                epoch_id=snapshot.epoch_id,
                abstained=True,
                abstain_reason="TIMEOUT",
                decision_type="ERROR",
                resolution_status="ABSTAINED",
                confidence_type="TARGET_CONFIDENCE",
            )
            return DelegationResolution(
                status=DelegationStatus.ABSTAINED_TIMEOUT,
                action=action,
                latency_ms=lat,
                reason=reason,
            )
        except (JevServiceUnavailableError, Exception) as e:
            lat = (time.perf_counter() - t0) * 1000.0
            reason = f"Jev System 1 unavailable: {e}"
            logger.warning(f"MicroActionResolver: {reason}")
            action = GroundedAction(
                operation=op,
                target_id=None,
                target_element=None,
                confidence=0.0,
                margin=0.0,
                epoch_id=snapshot.epoch_id,
                abstained=True,
                abstain_reason=str(e),
                decision_type="ERROR",
                resolution_status="ABSTAINED",
                confidence_type="TARGET_CONFIDENCE",
            )
            return DelegationResolution(
                status=DelegationStatus.ABSTAINED_SERVICE_ERROR,
                action=action,
                latency_ms=lat,
                reason=reason,
            )

        # Gate 4: Resolve GroundedAction via Calibrated Policy Thresholds
        thresh = self.thresholds.get(op, {"confidence": 0.70, "margin": 0.05})
        grounded = snapshot.resolve_microtask_decision(
            resp,
            micro_task,
            confidence_threshold=thresh["confidence"],
            margin_threshold=thresh["margin"],
        )
        lat = (time.perf_counter() - t0) * 1000.0

        # Gate 5: Check Abstention Reason
        if grounded.abstained:
            abst_status = DelegationStatus.ABSTAINED_LOW_CONFIDENCE
            if grounded.decision_type == "NO_ACTIONABLE_TARGET":
                abst_status = DelegationStatus.ABSTAINED_NO_ACTIONABLE_TARGET
            elif "LOW_MARGIN" in (grounded.abstain_reason or ""):
                abst_status = DelegationStatus.ABSTAINED_LOW_MARGIN
            elif "UNKNOWN_CANDIDATE_ID" in (grounded.abstain_reason or ""):
                abst_status = DelegationStatus.ABSTAINED_UNKNOWN_CANDIDATE

            logger.info(f"MicroActionResolver: Abstained on {op} '{micro_task.target_description}': {grounded.abstain_reason}")
            return DelegationResolution(
                status=abst_status,
                action=grounded,
                latency_ms=lat,
                reason=grounded.abstain_reason or "Abstained by policy gate",
            )

        # Gate 6: Target Element Binding Integrity
        if not grounded.target_element or not grounded.target_element.bid:
            reason = "Target candidate missing or invalid BID"
            logger.warning(f"MicroActionResolver: {reason}")
            return DelegationResolution(
                status=DelegationStatus.ABSTAINED_UNKNOWN_CANDIDATE,
                action=grounded,
                latency_ms=lat,
                reason=reason,
            )

        # All Gates Passed: Ready for Comex validation and actuation
        logger.info(
            f"MicroActionResolver: Successfully resolved {op} on target '{grounded.target_id}' "
            f"(BID: {grounded.target_element.bid}, conf: {grounded.confidence:.2f}, margin: {grounded.margin:.2f}) "
            f"in {lat:.1f}ms"
        )
        return DelegationResolution(
            status=DelegationStatus.RESOLVED,
            action=grounded,
            target_element=grounded.target_element,
            latency_ms=lat,
            reason=f"Resolved with confidence {grounded.confidence:.2f} >= {thresh['confidence']:.2f}",
        )
