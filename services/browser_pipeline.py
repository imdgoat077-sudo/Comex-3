"""
services/browser_pipeline.py
============================
Production Comex Browser Action Pipeline (Phase A Nervous System).

Orchestrates the verified execution flow:
    Phase A
      ↓
    UniversalExtractor (Observational purity, transient data-comex-id, fingerprint isolation)
      ↓
    CandidateGenerator (Deterministic snapshot, stable candidate IDs)
      ↓
    MicroActionResolver / Jev System 1 (Calibrated CLICK 0.70/0.05, FILL 0.75/0.10, SELECT shadow-only)
      ↓
    ActuatorGate (Nervous system quiescence, overlay evaluation)
      ↓
    BrowserActuator (Method E, late occlusion hit-testing, atomic physical dispatch)
      ↓
    Fingerprint + LoopBreaker (Cycle prevention, zero-delta detection, escalation)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from services.browser_actuator import ActuationResult, ActuationStatus, BrowserActuator
from services.candidate_generator import (
    CandidateElement,
    CandidateGenerator,
    GroundedAction,
    ObservationSnapshot,
    SemanticMicroTask,
)
from services.loop_breaker import ActionLoopBreaker, RecoveryAction, StepRecord
from services.micro_action_resolver import (
    CALIBRATION_THRESHOLDS,
    DelegationResolution,
    DelegationStatus,
    MicroActionResolver,
)
from services.universal_extractor import (
    COMEX_BINDING_ATTR,
    DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT,
    UniversalExtractor,
)

logger = logging.getLogger("comex.services.pipeline")


class PipelineStatus(Enum):
    SUCCESS = "SUCCESS"
    ABSTAINED = "ABSTAINED"
    BLOCKED_BY_LOOP_BREAKER = "BLOCKED_BY_LOOP_BREAKER"
    TARGET_ALREADY_SELECTED = "TARGET_ALREADY_SELECTED"
    COORDINATE_MISMATCH = "COORDINATE_MISMATCH"
    OCCLUDED = "OCCLUDED"
    GATE_BLOCKED = "GATE_BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class PipelineResult:
    """Structured result of executing a SemanticMicroTask through the browser action pipeline."""

    status: PipelineStatus
    operation: str
    target_description: str
    target_id: Optional[str] = None
    target_bid: Optional[str] = None
    target_element: Optional[CandidateElement] = None
    target_selector: Optional[str] = None
    confidence: float = 0.0
    margin: float = 0.0
    state_changed: bool = False
    duration_ms: float = 0.0
    jev_latency_ms: float = 0.0
    pre_fingerprint: str = ""
    post_fingerprint: str = ""
    occlusion_result: Optional[Dict[str, Any]] = None
    execution_result: Optional[ActuationResult] = None
    details: str = ""
    recovery_action: Optional[RecoveryAction] = None
    abstention_reason: Optional[str] = None
    target_resolved: bool = False
    dispatch_attempted: bool = False
    dispatch_sent: bool = False
    dispatch_success: bool = False
    postcondition_passed: bool = False
    postcondition: str = "UNKNOWN"
    progress: str = "UNKNOWN"

    @property
    def is_success(self) -> bool:
        return self.status == PipelineStatus.SUCCESS

    @property
    def was_abstained(self) -> bool:
        return self.status == PipelineStatus.ABSTAINED

    @property
    def was_occluded(self) -> bool:
        return self.status == PipelineStatus.OCCLUDED

    @property
    def was_loop_blocked(self) -> bool:
        return self.status == PipelineStatus.BLOCKED_BY_LOOP_BREAKER

    @property
    def was_already_selected(self) -> bool:
        return self.status == PipelineStatus.TARGET_ALREADY_SELECTED

    @property
    def was_coordinate_mismatch(self) -> bool:
        return self.status == PipelineStatus.COORDINATE_MISMATCH

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "operation": self.operation,
            "target_description": self.target_description,
            "target_resolved": self.target_resolved,
            "dispatch_attempted": self.dispatch_attempted,
            "dispatch_success": self.dispatch_success,
            "state_changed": self.state_changed,
            "postcondition": self.postcondition,
            "progress": self.progress,
            "confidence": round(self.confidence, 3),
            "duration_ms": round(self.duration_ms, 1),
            "details": self.details,
            "pre_fingerprint": self.pre_fingerprint,
            "post_fingerprint": self.post_fingerprint,
        }

    def to_json(self) -> str:
        import json as _json
        return _json.dumps(self.to_dict())


class BrowserActionPipeline:
    """
    Unified production browser nervous system pipeline.
    Binds UniversalExtractor, CandidateGenerator, Jev MicroActionResolver,
    ActuatorGate, BrowserActuator, and LoopBreaker into a single closed loop.
    """

    def __init__(
        self,
        candidate_generator: Optional[CandidateGenerator] = None,
        micro_action_resolver: Optional[MicroActionResolver] = None,
        actuator: Optional[BrowserActuator] = None,
        loop_breaker: Optional[ActionLoopBreaker] = None,
        extractor: Optional[UniversalExtractor] = None,
        delegate_jev: bool = True,
    ) -> None:
        self.candidate_generator = candidate_generator or CandidateGenerator()
        self.micro_action_resolver = micro_action_resolver or MicroActionResolver()
        self.actuator = actuator or BrowserActuator()
        self.loop_breaker = loop_breaker or ActionLoopBreaker()
        self.extractor = extractor or UniversalExtractor()
        self.delegate_jev = delegate_jev

    async def execute_action(
        self,
        ws: Any,
        operation: str,
        target_description: str = "",
        value: Optional[str] = None,
        goal: str = "",
        url: str = "",
        msg_counter: Optional[List[int]] = None,
        tab_session: Optional[Any] = None,
        turn: int = 1,
        target_selector: Optional[str] = None,
        expected_region: Optional[Dict[str, float]] = None,
    ) -> PipelineResult:
        """
        Executes a single browser micro-action through the verified nervous system stages.
        Supports both Condition B (semantic target_description -> Jev resolution) and
        Condition A (direct target_selector -> bypass Jev) through the exact same downstream
        BrowserActuator, ActuatorGate / Method E, Visual Cursor, and CDP dispatch.
        """
        t0 = time.perf_counter()
        if msg_counter is None:
            msg_counter = [0]

        # Normalize operation
        op = operation.strip().upper()
        if op in ("TYPE", "INPUT"):
            op = "FILL"
        elif op in ("SELECT_OPTION",):
            op = "SELECT"

        # Resolve live page URL if not provided
        if not url:
            try:
                url = await self.actuator.cdp_evaluate(ws, "window.location.href", msg_counter) or ""
            except Exception:
                url = ""

        # ── Step 1: Pre-action state fingerprint (excluding data-comex-id) ──
        fp_before = await self.extractor.compute_fingerprint_from_cdp(ws, msg_counter)

        # ── Step 2: LoopBreaker pre-action check ──────────────────────────────
        proposed_action = {
            "action": op,
            "target_description": target_description or target_selector or "",
            "selector": target_selector,
            "text": value,
        }
        is_blocked, recovery, block_reason = self.loop_breaker.evaluate_proposed_action(
            proposed_action, fp_before
        )
        if is_blocked:
            dur = (time.perf_counter() - t0) * 1000.0
            return PipelineResult(
                status=PipelineStatus.BLOCKED_BY_LOOP_BREAKER,
                operation=op,
                target_description=target_description or (target_selector or ""),
                target_selector=target_selector,
                details=block_reason or "Blocked by loop breaker",
                recovery_action=recovery,
                duration_ms=dur,
                pre_fingerprint=fp_before,
                target_resolved=False,
                dispatch_attempted=False,
                dispatch_sent=False,
                dispatch_success=False,
                postcondition_passed=False,
                postcondition="UNMET",
                progress="NO_PROGRESS",
            )

        resolution = None
        if target_selector:
            # ── Condition A: Direct target selection (bypasses Jev candidate resolution) ──
            bid = None
            selector = target_selector
            target_el = None
            target_id = target_selector
            target_bid = None
            confidence = 1.0
            margin = 1.0
        else:
            # ── Condition B: Candidate extraction + Jev System 1 resolution ──
            # ── Step 3: Universal candidate extraction ────────────────────────────
            # Tagged with transient data-comex-id, no application-visible state mutation
            raw_elements = await self.extractor.extract_from_cdp(ws, msg_counter)
            if not raw_elements:
                dur = (time.perf_counter() - t0) * 1000.0
                return PipelineResult(
                    status=PipelineStatus.ABSTAINED,
                    operation=op,
                    target_description=target_description,
                    details="UniversalExtractor found zero visible/interactive elements on page",
                    abstention_reason="NO_CANDIDATES",
                    duration_ms=dur,
                    pre_fingerprint=fp_before,
                    target_resolved=False,
                    dispatch_attempted=False,
                    dispatch_sent=False,
                    dispatch_success=False,
                    postcondition_passed=False,
                    postcondition="UNMET",
                    progress="NO_PROGRESS",
                )

            # ── Step 4: Candidate snapshot generation ─────────────────────────────
            snapshot = self.candidate_generator.generate_snapshot(
                raw_elements=raw_elements,
                goal=goal or target_description,
                url=url,
            )
            # Verify and link state fingerprint
            snapshot = ObservationSnapshot(
                epoch_id=snapshot.epoch_id,
                timestamp_mono=snapshot.timestamp_mono,
                goal=snapshot.goal,
                candidates=snapshot.candidates,
                url=snapshot.url,
                scroll_x=snapshot.scroll_x,
                scroll_y=snapshot.scroll_y,
                focused_snippet=snapshot.focused_snippet,
                state_fingerprint=fp_before,
            )

            # ── Step 5: MicroActionResolver / Jev System 1 ────────────────────────
            if not self.delegate_jev:
                dur = (time.perf_counter() - t0) * 1000.0
                return PipelineResult(
                    status=PipelineStatus.ABSTAINED,
                    operation=op,
                    target_description=target_description,
                    confidence=0.0,
                    margin=0.0,
                    details="Jev delegation is disabled (delegate_jev=False). Resolve target directly with browser_click(selector).",
                    abstention_reason="DELEGATION_DISABLED",
                    duration_ms=dur,
                    jev_latency_ms=0.0,
                    pre_fingerprint=fp_before,
                    target_resolved=False,
                    dispatch_attempted=False,
                    dispatch_sent=False,
                    dispatch_success=False,
                    postcondition_passed=False,
                    postcondition="UNMET",
                    progress="NO_PROGRESS",
                )

            micro_task = SemanticMicroTask(
                operation=op,
                target_description=target_description,
                fill_value=value if op == "FILL" else None,
                select_option=value if op == "SELECT" else None,
            )

            resolution = await self.micro_action_resolver.resolve(snapshot, micro_task)
            if not resolution.can_delegate or resolution.status != DelegationStatus.RESOLVED:
                dur = (time.perf_counter() - t0) * 1000.0
                conf = resolution.action.confidence if resolution.action else 0.0
                marg = resolution.action.margin if resolution.action else 0.0
                return PipelineResult(
                    status=PipelineStatus.ABSTAINED,
                    operation=op,
                    target_description=target_description,
                    confidence=conf,
                    margin=marg,
                    details=f"Jev System 1 abstained ({resolution.status.value}): {resolution.reason}",
                    abstention_reason=resolution.reason,
                    duration_ms=dur,
                    jev_latency_ms=resolution.latency_ms,
                    pre_fingerprint=fp_before,
                    target_resolved=False,
                    dispatch_attempted=False,
                    dispatch_sent=False,
                    dispatch_success=False,
                    postcondition_passed=False,
                    postcondition="UNMET",
                    progress="NO_PROGRESS",
                )

            grounded_action = resolution.action
            target_el = resolution.target_element
            if not target_el:
                dur = (time.perf_counter() - t0) * 1000.0
                return PipelineResult(
                    status=PipelineStatus.ABSTAINED,
                    operation=op,
                    target_description=target_description,
                    details="Target element missing from resolution",
                    abstention_reason="MISSING_TARGET_ELEMENT",
                    duration_ms=dur,
                    pre_fingerprint=fp_before,
                    target_resolved=False,
                    dispatch_attempted=False,
                    dispatch_sent=False,
                    dispatch_success=False,
                    postcondition_passed=False,
                    postcondition="UNMET",
                    progress="NO_PROGRESS",
                )

            bid = target_el.bid
            selector = f"#{target_el.dom_id}" if target_el.dom_id else f'[{COMEX_BINDING_ATTR}="{bid}"]'
            target_id = target_el.candidate_id
            target_bid = bid
            confidence = grounded_action.confidence
            margin = grounded_action.margin

            # ── Step 5b: LoopBreaker check on resolved target BID ─────────────────
            resolved_action = {
                "action": op,
                "bid": bid,
                "candidate_id": target_el.candidate_id,
                "selector": selector,
                "text": value,
            }
            is_blocked, recovery, block_reason = self.loop_breaker.evaluate_proposed_action(
                resolved_action, fp_before
            )
            if is_blocked:
                dur = (time.perf_counter() - t0) * 1000.0
                return PipelineResult(
                    status=PipelineStatus.BLOCKED_BY_LOOP_BREAKER,
                    operation=op,
                    target_description=target_description,
                    target_id=target_el.candidate_id,
                    target_bid=bid,
                    target_element=target_el,
                    target_selector=selector,
                    confidence=grounded_action.confidence,
                    margin=grounded_action.margin,
                    details=block_reason or "Blocked by loop breaker",
                    recovery_action=recovery,
                    duration_ms=dur,
                    pre_fingerprint=fp_before,
                    target_resolved=True,
                    dispatch_attempted=False,
                    dispatch_sent=False,
                    dispatch_success=False,
                    postcondition_passed=False,
                    postcondition="UNMET",
                    progress="NO_PROGRESS",
                )

        # ── Step 5c: Protection against repeated selected-card clicks ──────────
        # If target is a composite card or option and is already selected, block physical dispatch!
        if op == "CLICK":
            is_selected_item = False
            if target_el:
                is_card_or_option = target_el.element_type in ("transit_route_card", "composite_card", "radio", "tab") or (
                    target_el.tag == "div" and getattr(target_el, "selected", False)
                )
                if is_card_or_option and getattr(target_el, "selected", False):
                    is_selected_item = True
            elif selector:
                try:
                    check_expr = f"""
                    (() => {{
                        const el = document.querySelector({json.dumps(selector)});
                        if (!el) return false;
                        return el.getAttribute('aria-selected') === 'true' ||
                               el.getAttribute('aria-checked') === 'true' ||
                               el.getAttribute('aria-current') === 'true' ||
                               el.classList.contains('selected') ||
                               el.classList.contains('active');
                    }})()
                    """
                    is_selected_item = bool(await self.actuator.cdp_evaluate(ws, check_expr, msg_counter))
                except Exception:
                    pass

            if is_selected_item:
                dur = (time.perf_counter() - t0) * 1000.0
                logger.info(
                    f"Blocked click on already-selected target ({target_id or selector}): TARGET_ALREADY_SELECTED"
                )
                return PipelineResult(
                    status=PipelineStatus.TARGET_ALREADY_SELECTED,
                    operation=op,
                    target_description=target_description or (selector or ""),
                    target_id=target_id,
                    target_bid=target_bid,
                    target_element=target_el,
                    target_selector=selector,
                    confidence=confidence,
                    margin=margin,
                    details=f"Target ({target_id or selector}) is already selected. Blocked to prevent unintended navigation or redundant state change.",
                    duration_ms=dur,
                    pre_fingerprint=fp_before,
                    target_resolved=True,
                    dispatch_attempted=True,
                    dispatch_sent=False,
                    dispatch_success=False,
                    postcondition_passed=False,
                    postcondition="MET",
                    progress="NEUTRAL",
                )

        # ── Step 6: ActuatorGate & Quiescence Validation (SHARED PATH) ────────
        if tab_session:
            if hasattr(tab_session, "tracker") and tab_session.tracker and hasattr(tab_session.tracker, "state"):
                if not tab_session.tracker.state.is_target_subtree_quiet:
                    await asyncio.sleep(0.085)
            if hasattr(tab_session, "evaluate_overlay_state"):
                try:
                    await tab_session.evaluate_overlay_state()
                except Exception:
                    pass

        # ── Step 7: BrowserActuator (Late Occlusion & Physical Dispatch - SHARED) ──
        validation = await self.actuator.validate_occlusion(
            ws, bid=bid, selector=selector, msg_counter=msg_counter
        )
        occ_status = validation.get("status", "TARGET_NOT_FOUND")
        if occ_status != "OK":
            dur = (time.perf_counter() - t0) * 1000.0
            return PipelineResult(
                status=PipelineStatus.OCCLUDED,
                operation=op,
                target_description=target_description or (selector or ""),
                target_id=target_id,
                target_bid=target_bid,
                target_element=target_el,
                target_selector=selector,
                confidence=confidence,
                margin=margin,
                details=f"Physical action blocked by late occlusion hit-test ({occ_status}): {validation}",
                duration_ms=dur,
                pre_fingerprint=fp_before,
                occlusion_result=validation,
                target_resolved=True,
                dispatch_attempted=True,
                dispatch_sent=False,
                dispatch_success=False,
                postcondition_passed=False,
                postcondition="UNMET",
                progress="NEGATIVE",
            )

        target_x = float(validation["x"])
        target_y = float(validation["y"])

        # ── Step 7.5: Coordinate & Target Geometry Verification ──────────────
        # Verify that resolved physical coordinates correspond to the target's expected bounds/region
        if expected_region:
            min_x = expected_region.get("min_x", expected_region.get("x", float("-inf")))
            min_y = expected_region.get("min_y", expected_region.get("y", float("-inf")))
            max_x = expected_region.get("max_x", min_x + expected_region.get("width", float("inf")) if "width" in expected_region else float("inf"))
            max_y = expected_region.get("max_y", min_y + expected_region.get("height", float("inf")) if "height" in expected_region else float("inf"))

            if not (min_x <= target_x <= max_x and min_y <= target_y <= max_y):
                dur = (time.perf_counter() - t0) * 1000.0
                logger.warning(
                    f"Coordinate mismatch for {target_id or selector}: physical point ({target_x}, {target_y}) "
                    f"falls outside expected region [{min_x}, {min_y}, {max_x}, {max_y}]"
                )
                return PipelineResult(
                    status=PipelineStatus.COORDINATE_MISMATCH,
                    operation=op,
                    target_description=target_description or (selector or ""),
                    target_id=target_id,
                    target_bid=target_bid,
                    target_element=target_el,
                    target_selector=selector,
                    confidence=confidence,
                    margin=margin,
                    details=f"Target physical coordinates ({target_x}, {target_y}) fall outside expected region [{min_x}, {min_y}, {max_x}, {max_y}]. Aborting physical dispatch to prevent misclick.",
                    duration_ms=dur,
                    pre_fingerprint=fp_before,
                    target_resolved=True,
                    dispatch_attempted=True,
                    dispatch_sent=False,
                    dispatch_success=False,
                    postcondition_passed=False,
                    postcondition="UNMET",
                    progress="NEGATIVE",
                )

        if op == "CLICK":
            act_res = await self.actuator.execute_click(
                ws, bid=bid, selector=selector, msg_counter=msg_counter
            )
        elif op == "FILL":
            act_res = await self.actuator.execute_atomic_fill(
                ws, bid=bid, selector=selector, text=value or "", msg_counter=msg_counter
            )
        elif op == "SELECT":
            act_res = await self.actuator.execute_select_option(
                ws, bid=bid, selector=selector, option_value=value or "", msg_counter=msg_counter
            )
        else:
            dur = (time.perf_counter() - t0) * 1000.0
            return PipelineResult(
                status=PipelineStatus.FAILED,
                operation=op,
                target_description=target_description or (selector or ""),
                details=f"Unsupported operation '{op}'",
                duration_ms=dur,
                pre_fingerprint=fp_before,
                target_resolved=True,
                dispatch_attempted=True,
                dispatch_sent=False,
                dispatch_success=False,
                postcondition_passed=False,
                postcondition="UNMET",
                progress="NEGATIVE",
            )

        if not act_res.is_success:
            dur = (time.perf_counter() - t0) * 1000.0
            return PipelineResult(
                status=PipelineStatus.FAILED,
                operation=op,
                target_description=target_description or (selector or ""),
                target_id=target_id,
                target_bid=target_bid,
                target_element=target_el,
                target_selector=selector,
                details=f"Physical dispatch failed ({act_res.status.value}): {act_res.details}",
                duration_ms=dur,
                pre_fingerprint=fp_before,
                occlusion_result=validation,
                execution_result=act_res,
                target_resolved=True,
                dispatch_attempted=True,
                dispatch_sent=True,
                dispatch_success=False,
                postcondition_passed=False,
                postcondition="UNMET",
                progress="NEGATIVE",
            )

        # ── Step 8: Post-action state fingerprint & LoopBreaker recording (SHARED) ─
        fp_after = await self.extractor.compute_fingerprint_from_cdp(ws, msg_counter)
        step_rec = self.loop_breaker.record_step_start(
            turn=turn,
            action={
                "action": op,
                "bid": bid,
                "candidate_id": target_id,
                "selector": selector,
                "text": value,
            },
            fingerprint_before=fp_before,
        )
        state_changed = self.loop_breaker.record_step_end(step_rec, fp_after)

        # ── Step 9: Return structured result (SHARED) ─────────────────────────
        dur = (time.perf_counter() - t0) * 1000.0
        label = target_el.descriptive_label if target_el else f"selector '{selector}'"
        jev_lat = resolution.latency_ms if resolution else 0.0
        # Physical pipeline verifies dispatch & DOM delta, but task-level progress is UNKNOWN
        # until evaluated by ProgressController against the active subtask.
        postcondition_status = "UNKNOWN"
        progress_status = "UNKNOWN"
        return PipelineResult(
            status=PipelineStatus.SUCCESS,
            operation=op,
            target_description=target_description or (selector or ""),
            target_id=target_id,
            target_bid=target_bid,
            target_element=target_el,
            target_selector=selector,
            confidence=confidence,
            margin=margin,
            state_changed=state_changed,
            duration_ms=dur,
            jev_latency_ms=jev_lat,
            pre_fingerprint=fp_before,
            post_fingerprint=fp_after,
            occlusion_result=validation,
            execution_result=act_res,
            details=(
                f"Successfully executed {op} on {label} "
                f"(selector: {selector}, BID: {bid}, "
                f"conf: {confidence:.2f}, margin: {margin:.2f}, "
                f"state_changed: {state_changed}) in {dur:.1f}ms"
            ),
            target_resolved=True,
            dispatch_attempted=True,
            dispatch_sent=True,
            dispatch_success=True,
            postcondition_passed=True,
            postcondition=postcondition_status,
            progress=progress_status,
        )
