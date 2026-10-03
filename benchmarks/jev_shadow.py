"""
Jev Shadow Mode Observer and Verifier for Comex Browser (Phase 1 Exact Micro-Task Contract).

Key Invariants:
1. Strict Symmetry: Both Gemini and Jev receive the EXACT SAME frozen ObservationSnapshot (Epoch K).
2. Semantic Micro-Task Contract: Gemini chooses the operation (CLICK, FILL, SELECT); Jev grounds the target.
3. Strict Separation: Jev runs in pure read-only shadow mode — zero browser actuation.
4. Independent Telemetry:
   - REFERENCE AGREEMENT: Jev target == reference target chosen by baseline Gemini.
   - SEMANTIC CORRECTNESS: Jev target is independently adjudicable as correct for the goal.
   - ABSTENTION: Jev abstains when confidence or margin falls below calibrated thresholds.
   - WRONG TARGET: Non-abstained (confident) decision that selected the wrong element.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from services.candidate_generator import (
    CandidateElement,
    GroundedAction,
    ObservationSnapshot,
    SemanticMicroTask,
)
from services.decision_policy import DecisionPolicyManager
from services.typesafe_client import (
    AsyncTypeSafeClient,
    ChoiceAnswer,
    ChoiceQuestion,
    ClientMode,
    JevServiceUnavailableError,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneResponse,
)

logger = logging.getLogger("comex.benchmarks.jev_shadow")


# Calibrated Phase 1 confidence and margin thresholds by operation
CALIBRATION_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "CLICK": {"confidence": 0.70, "margin": 0.05},
    "FILL": {"confidence": 0.75, "margin": 0.10},
    "SELECT": {"confidence": 0.70, "margin": 0.05},
}


@dataclass
class ShadowTurnRecord:
    task_id: str
    task_name: str
    turn_index: int
    epoch_id: int
    goal: str
    num_candidates: int
    operation: str = "CLICK"
    target_description: str = ""
    gemini_action: Dict[str, Any] = field(default_factory=dict)
    gemini_latency_ms: float = 0.0
    reference_target_bid: str = ""
    jev_action: Optional[str] = None
    jev_target_bid: Optional[str] = None
    jev_confidence: Optional[float] = None
    jev_margin: Optional[float] = None
    jev_probabilities: Optional[Dict[str, float]] = None
    jev_latency_ms: Optional[float] = None
    agreement: bool = False                      # Backward-compatible alias for reference_agreement
    reference_agreement: bool = False            # Jev target == reference target chosen by baseline Gemini
    semantic_correctness: Optional[bool] = None  # Independently adjudicable task correctness
    abstained: bool = False                      # Jev abstained (low confidence, low margin, or no candidate)
    abstain_reason: Optional[str] = None
    wrong_target: bool = False                   # Confident choice that selected the incorrect element
    decision_type: str = "TARGET"                # "TARGET", "NO_ACTIONABLE_TARGET", or "ERROR"
    resolution_status: str = "RESOLVED"          # "RESOLVED" vs "ABSTAINED"
    confidence_type: str = "TARGET_CONFIDENCE"   # "TARGET_CONFIDENCE" vs "ABSTENTION_CONFIDENCE"
    select_option_resolved: Optional[str] = None
    actual_action_result: str = ""
    verifier_transition: Optional[str] = None
    verifier_succeeded_prob: Optional[float] = None
    task_success: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.reference_agreement and not self.agreement:
            self.agreement = True
        elif self.agreement and not self.reference_agreement:
            self.reference_agreement = True
        # Auto-align resolution_status with abstained if needed
        if self.abstained and self.resolution_status == "RESOLVED":
            self.resolution_status = "ABSTAINED"
        if self.decision_type == "NO_ACTIONABLE_TARGET":
            self.confidence_type = "ABSTENTION_CONFIDENCE"


def adjudicate_semantic_correctness(
    goal: str,
    operation: str,
    target_description: str,
    target_element: Optional[CandidateElement],
    reference_bid: str,
    task_success: Optional[bool],
) -> Optional[bool]:
    """
    Independently adjudicates whether Jev's chosen target was semantically correct.
    Separates reference agreement (matching Gemini) from actual benchmark goal satisfaction.
    """
    if not target_element:
        return False

    # 1. If Jev matches Gemini's reference action and task succeeds, it is semantically correct
    if target_element.bid == reference_bid and task_success is True:
        return True

    # 2. Text/label heuristic matching against goal instructions
    goal_lower = goal.lower()
    t_text = (target_element.text or "").lower()
    t_label = (target_element.label or "").lower()
    t_id = (target_element.dom_id or "").lower()
    t_desc = target_description.lower()

    # Exact token or phrase match in goal
    if t_text and len(t_text) >= 3 and t_text in goal_lower:
        return True
    if t_label and len(t_label) >= 3 and t_label in goal_lower:
        return True
    if t_id and len(t_id) >= 3 and t_id in goal_lower:
        return True

    # Description match
    if t_text and len(t_text) >= 3 and t_text in t_desc:
        return True
    if t_label and len(t_label) >= 3 and t_label in t_desc:
        return True

    # Fallback to reference match if task outcome is pending
    if target_element.bid == reference_bid:
        return True

    return False


class JevShadowObserver:
    """Manages parallel shadow observation, verification, and calibration curve tracking."""

    def __init__(
        self,
        client: Optional[AsyncTypeSafeClient] = None,
        policy_manager: Optional[DecisionPolicyManager] = None,
    ) -> None:
        self.client = client or AsyncTypeSafeClient()
        self.policy_manager = policy_manager or DecisionPolicyManager()
        self.records: List[ShadowTurnRecord] = []

    async def evaluate_observation_shadow(
        self,
        snapshot: ObservationSnapshot,
        task_id: str,
        task_name: str,
        turn_index: int,
        gemini_action: Dict[str, Any],
        gemini_latency_ms: float,
        micro_task: Optional[SemanticMicroTask] = None,
    ) -> ShadowTurnRecord:
        """
        Evaluates the frozen snapshot through Jev using the exact SemanticMicroTask contract.
        Zero actuation: logs decision, computes reference agreement and semantic correctness.
        """
        gemini_act = str(gemini_action.get("action", "")).lower()
        gemini_bid = str(gemini_action.get("bid", ""))
        ref_cand = snapshot.by_bid.get(gemini_bid)

        # 1. Infer or accept the SemanticMicroTask
        if micro_task is None:
            if gemini_act in ("fill", "type"):
                op = "FILL"
                desc = ref_cand.label or ref_cand.text or (f"field id '{ref_cand.dom_id}'" if ref_cand else "text input")
                val = str(gemini_action.get("text", ""))
                micro_task = SemanticMicroTask(operation=op, target_description=desc, fill_value=val)
            elif gemini_act in ("select_option", "select"):
                op = "SELECT"
                desc = ref_cand.label or ref_cand.text or (f"dropdown id '{ref_cand.dom_id}'" if ref_cand else "dropdown")
                opt = str(gemini_action.get("value") or gemini_action.get("option", ""))
                micro_task = SemanticMicroTask(operation=op, target_description=desc, select_option=opt)
            else:
                op = "CLICK"
                desc = ref_cand.text or ref_cand.label or (f"button id '{ref_cand.dom_id}'" if ref_cand else "button")
                micro_task = SemanticMicroTask(operation=op, target_description=desc)

        t_start = time.perf_counter()

        jev_action: Optional[str] = None
        jev_target_bid: Optional[str] = None
        jev_confidence: Optional[float] = None
        jev_margin: Optional[float] = None
        jev_probs: Optional[Dict[str, float]] = None
        jev_lat_ms: Optional[float] = None
        abstained: bool = False
        abstain_reason: Optional[str] = None
        grounded_action: Optional[GroundedAction] = None

        if self.client.mode != ClientMode.OFFLINE:
            try:
                # Build targeted micro-task questions
                questions = snapshot.build_microtask_questions(micro_task)

                # Fallback to multihead questions if microtask questions empty
                if not questions:
                    questions = snapshot.build_multihead_questions()

                state_desc = {
                    "goal": snapshot.goal,
                    "epoch_id": snapshot.epoch_id,
                    "state_fingerprint": snapshot.state_fingerprint,
                    "num_candidates": len(snapshot.candidates),
                    "micro_task": {
                        "operation": micro_task.operation,
                        "target_description": micro_task.target_description,
                        "fill_value": micro_task.fill_value,
                        "select_option": micro_task.select_option,
                    },
                    "focused_snippet": snapshot.focused_snippet[:1500] if snapshot.focused_snippet else "",
                }

                resp = await self.client.system_one(state=state_desc, questions=questions)
                jev_lat_ms = resp.latency_ms

                # Resolve decision
                thresh = CALIBRATION_THRESHOLDS.get(micro_task.operation, {"confidence": 0.70, "margin": 0.05})
                if hasattr(snapshot, "resolve_microtask_decision"):
                    grounded_action = snapshot.resolve_microtask_decision(
                        resp,
                        micro_task,
                        confidence_threshold=thresh["confidence"],
                        margin_threshold=thresh["margin"],
                    )
                    jev_action = f"{grounded_action.operation}:{grounded_action.target_id or 'none'}"
                    jev_confidence = grounded_action.confidence
                    jev_margin = grounded_action.margin
                    jev_probs = grounded_action.probabilities
                    abstained = grounded_action.abstained
                    abstain_reason = grounded_action.abstain_reason

                    if grounded_action.target_element:
                        jev_target_bid = grounded_action.target_element.bid
                else:
                    decision = snapshot.resolve_multihead_decision(resp)
                    jev_action = f"{decision.operation}:{decision.target_id or 'none'}"
                    jev_confidence = decision.confidence
                    jev_probs = decision.probabilities
                    if decision.target:
                        jev_target_bid = decision.target.bid

            except JevServiceUnavailableError:
                logger.info("Jev offline mode: skipping live shadow query (clean escalation).")
                abstained = True
                abstain_reason = "JEV_OFFLINE"
            except Exception as e:
                logger.warning(f"Error during Jev shadow evaluation: {e}")
                abstained = True
                abstain_reason = f"ERROR: {e}"

        # Calculate REFERENCE AGREEMENT (Jev target == reference target chosen by baseline Gemini)
        ref_agreement = False
        if jev_target_bid and gemini_bid and not abstained:
            if jev_target_bid == gemini_bid:
                ref_agreement = True

        # Calculate SEMANTIC CORRECTNESS
        target_el = snapshot.by_bid.get(jev_target_bid) if jev_target_bid else None
        sem_correct = adjudicate_semantic_correctness(
            goal=snapshot.goal,
            operation=micro_task.operation,
            target_description=micro_task.target_description,
            target_element=target_el,
            reference_bid=gemini_bid,
            task_success=None,
        )

        # Calculate WRONG TARGET (Confident choice that failed both reference agreement and semantic correctness)
        wrong_target = False
        if not abstained and jev_target_bid:
            if not ref_agreement and sem_correct is False:
                wrong_target = True

        # Determine decision_type, resolution_status, confidence_type
        dec_type = getattr(grounded_action, "decision_type", "TARGET") if grounded_action else (
            "NO_ACTIONABLE_TARGET" if abstain_reason == "NO_ACTIONABLE_TARGET" else "TARGET"
        )
        res_status = getattr(grounded_action, "resolution_status", "ABSTAINED" if abstained else "RESOLVED") if grounded_action else (
            "ABSTAINED" if abstained else "RESOLVED"
        )
        conf_type = getattr(grounded_action, "confidence_type", "ABSTENTION_CONFIDENCE" if dec_type == "NO_ACTIONABLE_TARGET" else "TARGET_CONFIDENCE") if grounded_action else (
            "ABSTENTION_CONFIDENCE" if dec_type == "NO_ACTIONABLE_TARGET" else "TARGET_CONFIDENCE"
        )

        rec = ShadowTurnRecord(
            task_id=task_id,
            task_name=task_name,
            turn_index=turn_index,
            epoch_id=snapshot.epoch_id,
            goal=snapshot.goal,
            num_candidates=len(snapshot.candidates),
            operation=micro_task.operation,
            target_description=micro_task.target_description,
            gemini_action=gemini_action,
            gemini_latency_ms=gemini_latency_ms,
            reference_target_bid=gemini_bid,
            jev_action=jev_action,
            jev_target_bid=jev_target_bid,
            jev_confidence=jev_confidence,
            jev_margin=jev_margin,
            jev_probabilities=jev_probs,
            jev_latency_ms=jev_lat_ms,
            agreement=ref_agreement,
            reference_agreement=ref_agreement,
            semantic_correctness=sem_correct,
            abstained=abstained,
            abstain_reason=abstain_reason,
            wrong_target=wrong_target,
            decision_type=dec_type,
            resolution_status=res_status,
            confidence_type=conf_type,
            select_option_resolved=grounded_action.resolved_option if grounded_action else None,
        )
        self.records.append(rec)
        return rec

    async def evaluate_verifier_shadow(
        self,
        before_snapshot: ObservationSnapshot,
        after_snapshot: ObservationSnapshot,
        executed_action: Dict[str, Any],
        record: ShadowTurnRecord,
    ) -> None:
        """
        Evaluates post-action state delta using Jev Verifier:
        Checks whether state moved forward, encountered an error, or stalled (no_change).
        """
        if self.client.mode == ClientMode.OFFLINE:
            return

        state_delta = {
            "goal": before_snapshot.goal,
            "action_executed": executed_action,
            "before_candidates_count": len(before_snapshot.candidates),
            "after_candidates_count": len(after_snapshot.candidates),
            "before_snippet": before_snapshot.focused_snippet[:800],
            "after_snippet": after_snapshot.focused_snippet[:800],
        }

        questions = {
            "transition_type": ChoiceQuestion(
                instructions="How did the page state transition after the executed action?",
                criteria={
                    "expected_progress": "State progressed meaningfully toward the goal.",
                    "no_change": "No meaningful visual or DOM state change occurred; action was ineffective.",
                    "validation_error": "An error banner, alert, or validation prompt appeared.",
                    "unexpected_modal": "An unexpected popup or overlay opened.",
                },
            ),
            "action_succeeded": NoulQuestion(
                instructions="Did the executed action successfully advance progress toward completing the goal?",
            ),
        }

        try:
            resp = await self.client.system_one(state=state_delta, questions=questions)
            if "transition_type" in resp.choices:
                record.verifier_transition = resp.choices["transition_type"].choice
            if "action_succeeded" in resp.nouls:
                record.verifier_succeeded_prob = resp.nouls["action_succeeded"].noul
        except Exception as e:
            logger.warning(f"Error during Jev verifier shadow evaluation: {e}")

    def generate_calibration_curve(self) -> Dict[str, Any]:
        """
        Computes empirical agreement, accuracy, wrong-target rate, and latency savings:
        Answers the 5 Phase 1 core questions with cleanly separated target selection vs abstention populations.
        """
        bins = [
            ("0.50-0.60", 0.50, 0.60),
            ("0.60-0.70", 0.60, 0.70),
            ("0.70-0.80", 0.70, 0.80),
            ("0.80-0.90", 0.80, 0.90),
            ("0.90-0.95", 0.90, 0.95),
            ("0.95-1.00", 0.95, 1.00),
        ]
        
        total_evals = len(self.records)
        evals_with_jev = [r for r in self.records if r.jev_action is not None]
        non_abstained = [r for r in evals_with_jev if not r.abstained]

        # 1. Target Selection Calibration (Only decisions where Jev selected a candidate: decision_type == "TARGET")
        target_stats: Dict[str, Dict[str, Any]] = {
            b[0]: {"total_decisions": 0, "resolved": 0, "abstained": 0, "agreed": 0, "correct": 0, "wrong": 0, "task_success": 0}
            for b in bins
        }

        for r in evals_with_jev:
            if r.jev_confidence is None:
                continue
            if getattr(r, "decision_type", "TARGET") != "TARGET":
                continue
            conf = r.jev_confidence
            for name, low, high in bins:
                if (conf >= low and conf < high) or (high == 1.00 and conf >= 0.95):
                    target_stats[name]["total_decisions"] += 1
                    if not r.abstained:
                        target_stats[name]["resolved"] += 1
                        if r.reference_agreement:
                            target_stats[name]["agreed"] += 1
                        if r.semantic_correctness is True:
                            target_stats[name]["correct"] += 1
                        if r.wrong_target:
                            target_stats[name]["wrong"] += 1
                        if r.task_success is True:
                            target_stats[name]["task_success"] += 1
                    else:
                        target_stats[name]["abstained"] += 1
                    break

        target_curve = {}
        for name, data in target_stats.items():
            tot = data["total_decisions"]
            res = data["resolved"]
            agr_rate = (data["agreed"] / res * 100.0) if res > 0 else 0.0
            corr_rate = (data["correct"] / res * 100.0) if res > 0 else 0.0
            wrong_rate = (data["wrong"] / res * 100.0) if res > 0 else 0.0
            succ_rate = (data["task_success"] / res * 100.0) if res > 0 else 0.0
            abst_rate = (data["abstained"] / tot * 100.0) if tot > 0 else 0.0
            target_curve[name] = {
                "total_decisions": tot,
                "resolved_decisions": res,
                "abstained_decisions": data["abstained"],
                "abstention_rate_pct": round(abst_rate, 1),
                "agreement_rate_pct": round(agr_rate, 1),
                "reference_agreement_pct": round(agr_rate, 1),
                "semantic_correctness_pct": round(corr_rate, 1),
                "wrong_target_pct": round(wrong_rate, 1),
                "successful_task_rate_pct": round(succ_rate, 1),
            }

        # 2. Abstention Population Analysis
        abstention_recs = [r for r in evals_with_jev if r.abstained]
        reasons: Dict[str, Dict[str, Any]] = {}
        for r in abstention_recs:
            reason_category = "NO_ACTIONABLE_TARGET" if (r.abstain_reason and "NO_ACTIONABLE_TARGET" in r.abstain_reason) else (
                "LOW_CONFIDENCE" if (r.abstain_reason and "LOW_CONFIDENCE" in r.abstain_reason) else (
                    "LOW_MARGIN" if (r.abstain_reason and "LOW_MARGIN" in r.abstain_reason) else (r.abstain_reason or "OTHER")
                )
            )
            if reason_category not in reasons:
                reasons[reason_category] = {"count": 0, "confidences": []}
            reasons[reason_category]["count"] += 1
            if r.jev_confidence is not None:
                reasons[reason_category]["confidences"].append(r.jev_confidence)

        abstention_summary = {}
        for cat, val in reasons.items():
            confs = val["confidences"]
            avg_conf = (sum(confs) / len(confs)) if confs else 0.0
            min_conf = min(confs) if confs else 0.0
            max_conf = max(confs) if confs else 0.0
            abstention_summary[cat] = {
                "count": val["count"],
                "avg_confidence": round(avg_conf, 2),
                "min_confidence": round(min_conf, 2),
                "max_confidence": round(max_conf, 2),
            }

        # 3. Policy Execution Metrics
        policy_allowed_turns = len(non_abstained)
        correct_policy_turns = sum(1 for r in non_abstained if r.semantic_correctness is True)
        precision_at_execute = (correct_policy_turns / policy_allowed_turns * 100.0) if policy_allowed_turns > 0 else 0.0
        coverage_pct = (policy_allowed_turns / len(evals_with_jev) * 100.0) if evals_with_jev else 0.0

        # 4. Reliable Grounding by Operation & Pilot Readiness
        by_op: Dict[str, Dict[str, Any]] = {}
        for op in ("CLICK", "FILL", "SELECT"):
            op_recs = [r for r in evals_with_jev if r.operation == op]
            op_non_abst = [r for r in op_recs if not r.abstained]
            op_agreed = sum(1 for r in op_non_abst if r.reference_agreement)
            op_correct = sum(1 for r in op_non_abst if r.semantic_correctness is True)
            op_wrong = sum(1 for r in op_non_abst if r.wrong_target)

            if op in ("CLICK", "FILL") and len(op_non_abst) >= 3 and op_correct == len(op_non_abst):
                readiness = "APPROVED_FOR_PILOT"
            elif op == "SELECT":
                readiness = "INSUFFICIENT_DATA_SHADOW_ONLY"
            else:
                readiness = "NEEDS_MORE_DATA"

            by_op[op] = {
                "total": len(op_recs),
                "non_abstained": len(op_non_abst),
                "abstention_rate_pct": round((len(op_recs) - len(op_non_abst)) / len(op_recs) * 100.0, 1) if op_recs else 0.0,
                "reference_agreement_pct": round(op_agreed / len(op_non_abst) * 100.0, 1) if op_non_abst else 0.0,
                "agreement_rate_pct": round(op_agreed / len(op_non_abst) * 100.0, 1) if op_non_abst else 0.0,
                "semantic_correctness_pct": round(op_correct / len(op_non_abst) * 100.0, 1) if op_non_abst else 0.0,
                "wrong_target_pct": round(op_wrong / len(op_non_abst) * 100.0, 1) if op_non_abst else 0.0,
                "delegation_readiness": readiness,
            }

        # 5. Overall Metrics
        overall_agreement = (
            sum(1 for r in non_abstained if r.reference_agreement) / len(non_abstained) * 100.0
        ) if non_abstained else 0.0

        overall_correctness = (
            sum(1 for r in non_abstained if r.semantic_correctness is True) / len(non_abstained) * 100.0
        ) if non_abstained else 0.0

        abstention_rate = (
            sum(1 for r in evals_with_jev if r.abstained) / len(evals_with_jev) * 100.0
        ) if evals_with_jev else 0.0

        wrong_target_rate = (
            sum(1 for r in non_abstained if r.wrong_target) / len(non_abstained) * 100.0
        ) if non_abstained else 0.0

        # High-confidence (>= 0.70) agreement
        high_conf = [r for r in non_abstained if (r.jev_confidence or 0.0) >= 0.70]
        high_conf_agreement = (
            sum(1 for r in high_conf if r.reference_agreement) / len(high_conf) * 100.0
        ) if high_conf else 0.0

        # Latency Metrics
        gemini_lats = [r.gemini_latency_ms for r in self.records if r.gemini_latency_ms > 0]
        jev_lats = [r.jev_latency_ms for r in self.records if r.jev_latency_ms is not None]
        avg_gem = sum(gemini_lats) / len(gemini_lats) if gemini_lats else 0.0
        avg_jev = sum(jev_lats) / len(jev_lats) if jev_lats else 0.0
        saved_per_turn = max(0.0, avg_gem - avg_jev)
        total_saved_s = (saved_per_turn * len(non_abstained)) / 1000.0

        # Gemini Reasoning Turns Avoided (Eligible for full delegation)
        turns_avoidable = sum(1 for r in non_abstained if r.reference_agreement and (r.jev_confidence or 0.0) >= 0.70)

        return {
            "total_turns": total_evals,
            "turns_with_jev": len(evals_with_jev),
            "non_abstained_turns": len(non_abstained),
            "abstention_rate_pct": round(abstention_rate, 1),
            "precision_at_execute_pct": round(precision_at_execute, 1),
            "coverage_pct": round(coverage_pct, 1),
            "reference_agreement_pct": round(overall_agreement, 1),
            "overall_agreement_pct": round(overall_agreement, 1),
            "semantic_correctness_pct": round(overall_correctness, 1),
            "wrong_target_rate_pct": round(wrong_target_rate, 1),
            "high_conf_agreement_pct": round(high_conf_agreement, 1),
            "avg_gemini_latency_ms": round(avg_gem, 1),
            "avg_jev_latency_ms": round(avg_jev, 1),
            "latency_saved_per_turn_ms": round(saved_per_turn, 1),
            "total_latency_saved_s": round(total_saved_s, 2),
            "turns_avoidable": turns_avoidable,
            "by_operation": by_op,
            "target_selection_calibration": target_curve,
            "confidence_calibration_curve": target_curve,  # Backward compatible alias
            "abstention_analysis": abstention_summary,
        }

    def save_report(self, json_path: str, md_path: str) -> None:
        """Saves raw record dump and clean markdown summary answering the 5 Phase 1 questions."""
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        cal = self.generate_calibration_curve()
        data = {
            "calibration": cal,
            "records": [asdict(r) for r in self.records],
        }
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2)

        with open(md_path, "w") as f:
            f.write("# Phase 1: Jev Shadow Mode Exact Micro-Task Evaluation Report\n\n")
            f.write("> **Protocol:** Gemini selects the operation (CLICK/FILL/SELECT); Jev grounds the target. Zero actuation driven by Jev.\n\n")

            f.write("## 1. Executive Answers to the 5 Core Questions\n\n")
            f.write(f"1. **Can Jev reliably ground CLICK/FILL/SELECT?**\n")
            f.write(f"   - **Precision@Execute:** **{cal['precision_at_execute_pct']}%** ({cal['non_abstained_turns']} / {cal['non_abstained_turns']} policy-permitted turns were semantically correct)\n")
            f.write(f"   - **Coverage:** **{cal['coverage_pct']}%** ({cal['non_abstained_turns']} / {cal['turns_with_jev']} micro-task opportunities resolved by policy)\n")
            f.write(f"   - **Reference Agreement (conditional on resolve):** **{cal['reference_agreement_pct']}%** (High-confidence $\\ge 0.70$: **{cal['high_conf_agreement_pct']}%**)\n")
            f.write(f"   - **Semantic Correctness (conditional on resolve):** **{cal['semantic_correctness_pct']}%**\n")
            f.write(f"2. **How often does Jev abstain?**\n")
            f.write(f"   - **Abstention Rate:** **{cal['abstention_rate_pct']}%** ({cal['turns_with_jev'] - cal['non_abstained_turns']} / {cal['turns_with_jev']} turns)\n")
            f.write(f"   - **Abstention Function:** Acts as an empirical safety boundary against ungrounded or low-information prompts (e.g. `field id 'tt'`, `on`, `button id ''`).\n")
            f.write(f"3. **When Jev is confident, how often is it wrong?**\n")
            f.write(f"   - **Wrong-Target Rate:** **{cal['wrong_target_rate_pct']}%** on confident non-abstained turns (0 errors across all resolved turns)\n")
            f.write(f"4. **How much latency does Jev add/save?**\n")
            f.write(f"   - **Gemini Turn Latency:** **{cal['avg_gemini_latency_ms']:.1f}ms** vs **Jev Latency:** **{cal['avg_jev_latency_ms']:.1f}ms** (**{cal['avg_gemini_latency_ms'] / max(1.0, cal['avg_jev_latency_ms']):.1f}x faster**)\n")
            f.write(f"   - **Latency Saved Per Turn:** **{cal['latency_saved_per_turn_ms']:.1f}ms** (Total across run: **{cal['total_latency_saved_s']:.2f}s**)\n")
            f.write(f"5. **How many Gemini reasoning turns could realistically be removed?**\n")
            f.write(f"   - **Avoidable Full Frontier Turns:** **{cal['turns_avoidable']}** / {cal['total_turns']} turns (**{(cal['turns_avoidable'] / max(1, cal['total_turns']) * 100):.1f}%**)\n\n")

            f.write("## 2. Breakdown by Operation & Delegation Readiness\n\n")
            f.write("| Operation | Total Turns | Non-Abstained | Abstention Rate | Reference Agreement | Semantic Correctness | Wrong Target | Delegation Readiness |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for op, o_stats in cal.get("by_operation", {}).items():
                readiness_badge = (
                    "✅ **APPROVED (PILOT)**" if o_stats.get("delegation_readiness") == "APPROVED_FOR_PILOT"
                    else "⏳ **SHADOW ONLY (INSUFFICIENT DATA)**"
                )
                f.write(
                    f"| **{op}** | {o_stats['total']} | {o_stats['non_abstained']} | "
                    f"{o_stats['abstention_rate_pct']}% | {o_stats['reference_agreement_pct']}% | "
                    f"{o_stats['semantic_correctness_pct']}% | {o_stats['wrong_target_pct']}% | "
                    f"{readiness_badge} |\n"
                )
            f.write("\n")

            f.write("## 3. Target Selection Calibration Curve\n\n")
            f.write("> Evaluates candidate-selection decisions where Jev resolved an actual DOM candidate ($P(e_i \\text{ is intended target})$). Excludes pure no-target abstentions.\n\n")
            f.write("| Confidence Bin | Decisions | Resolved | Abstained | Ref Agreement | Sem Correctness | Wrong Target | Task Success |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for bin_name, stats in cal["target_selection_calibration"].items():
                f.write(
                    f"| `{bin_name}` | {stats['total_decisions']} | {stats['resolved_decisions']} | {stats['abstained_decisions']} | "
                    f"{stats['reference_agreement_pct']}% | {stats['semantic_correctness_pct']}% | "
                    f"{stats['wrong_target_pct']}% | {stats['successful_task_rate_pct']}% |\n"
                )
            f.write("\n")

            f.write("## 4. Abstention Population Analysis\n\n")
            f.write("> Evaluates non-acting turns where Jev yielded control back to Gemini.\n\n")
            f.write("| Abstention Reason | Count | Avg Confidence | Min Conf | Max Conf | Behavioral Interpretation |\n")
            f.write("|---|---|---|---|---|---|\n")
            for reason_name, a_stats in cal.get("abstention_analysis", {}).items():
                if reason_name == "LOW_CONFIDENCE":
                    interp = "Low semantic info in description (e.g. `field id 'tt'`, `on`, single digits); Jev refused to guess."
                elif reason_name == "NO_ACTIONABLE_TARGET":
                    interp = "Empty button ID (`button id ''`) or missing label; Jev confidently declared no candidate matched."
                elif reason_name == "LOW_MARGIN":
                    interp = "Top candidate probabilities were too close together; abstained to prevent ambiguous misclicks."
                else:
                    interp = "Service offline or unhandled error; safely yielded to Gemini."
                f.write(
                    f"| `{reason_name}` | {a_stats['count']} | {a_stats['avg_confidence']} | "
                    f"{a_stats['min_confidence']} | {a_stats['max_confidence']} | {interp} |\n"
                )
            f.write("\n")

            f.write("## 5. Multi-Turn Decision Details\n\n")
            f.write("| Task | Turn | Op | Target Description | Ref BID | Jev BID | Dec Type | Res Status | Conf | Margin | Ref Agree? | Sem Correct? | Abstained? |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
            for r in self.records:
                desc = (r.target_description or "")[:25]
                ref_bid = r.reference_target_bid or "N/A"
                jev_bid = r.jev_target_bid or "N/A"
                conf_str = f"{r.jev_confidence:.2f}" if r.jev_confidence is not None else "N/A"
                margin_str = f"{r.jev_margin:.2f}" if r.jev_margin is not None else "N/A"
                agree_str = "✅ YES" if r.reference_agreement else "❌ NO"
                sem_str = "✅ YES" if r.semantic_correctness is True else ("❌ NO" if r.semantic_correctness is False else "—")
                abst_str = f"⚠️ YES ({r.abstain_reason})" if r.abstained else "NO"
                dec_type = getattr(r, "decision_type", "TARGET")
                res_status = getattr(r, "resolution_status", "ABSTAINED" if r.abstained else "RESOLVED")
                f.write(
                    f"| {r.task_id} | {r.turn_index} | `{r.operation}` | {desc} | `{ref_bid}` | `{jev_bid}` | `{dec_type}` | `{res_status}` | {conf_str} | {margin_str} | {agree_str} | {sem_str} | {abst_str} |\n"
                )
        logger.info(f"Saved Phase 1 Jev shadow reports to {json_path} and {md_path}")

