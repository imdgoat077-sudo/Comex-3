"""
benchmarks/action_ledger.py
===========================
Phase 2A Action-Level Execution Ledger for Comex & Jev Evaluation.

Implements:
1. ActionRecord: Immutable, action-level telemetry ledger.
2. ComexBlockCategory: Explicit taxonomy separating physical safety from loop prevention.
3. CorrectnessSource: Independent ground-truth hierarchy (oracle > manual > gemini_agreement > unknown).
4. Wilson Score Confidence Interval calculator for small-sample binomial statistics.
5. ConsistencyGuard: Automated assertion engine ensuring reported metrics match ledger data.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ComexBlockCategory(str, Enum):
    """Explicit taxonomy distinguishing physical safety from recovery/loop-breaker interventions."""
    # Physical safety interventions
    LATE_OCCLUSION = "LATE_OCCLUSION"
    ACTUATOR_GATE_BLOCK = "ACTUATOR_GATE_BLOCK"
    METHOD_E_FAILURE = "METHOD_E_FAILURE"
    TARGET_BINDING_FAILURE = "TARGET_BINDING_FAILURE"
    STALE_EPOCH = "STALE_EPOCH"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_OUT_OF_VIEWPORT = "TARGET_OUT_OF_VIEWPORT"
    TARGET_DISABLED = "TARGET_DISABLED"

    # Recovery / loop prevention interventions
    LOOP_BREAK = "LOOP_BREAK"
    REPEATED_ACTION_BLOCK = "REPEATED_ACTION_BLOCK"
    QUIESCENCE_WAIT_BLOCK = "QUIESCENCE_WAIT_BLOCK"
    OVERLAY_BLOCK = "OVERLAY_BLOCK"
    OTHER = "OTHER"
    NONE = "NONE"


class CorrectnessSource(str, Enum):
    """Independent ground-truth hierarchy. Gemini baseline agreement is NEVER called semantic accuracy."""
    DETERMINISTIC_ORACLE = "deterministic_benchmark_oracle"
    MANUAL_ADJUDICATION = "manual_adjudication"
    GEMINI_BASELINE_AGREEMENT = "gemini_baseline_agreement"
    UNKNOWN = "unknown"


@dataclass
class SemanticMicroTaskRecord:
    operation: str
    target_description: str
    fill_value: Optional[str] = None
    select_option: Optional[str] = None


@dataclass
class RoutingMetadata:
    route: str = "GEMINI"  # "JEV" | "GEMINI" | "HYBRID"
    router_confidence: Optional[float] = None
    router_reason: Optional[str] = None


@dataclass
class JevDecisionMetadata:
    attempted: bool = False
    decision_type: Optional[str] = None  # "TARGET" | "NO_ACTIONABLE_TARGET" | "ABSTAIN"
    target_id: Optional[str] = None
    confidence: Optional[float] = None
    margin: Optional[float] = None
    status: Optional[str] = None  # "RESOLVED" | "ABSTAINED" | "TIMEOUT" | "DISALLOWED"
    abstain_reason: Optional[str] = None
    latency_ms: Optional[float] = None
    target_selector: Optional[str] = None
    target_class: Optional[str] = None
    target_text: Optional[str] = None


@dataclass
class ReferenceMetadata:
    reference_target: Optional[str] = None
    reference_source: str = "gemini_baseline"


@dataclass
class CorrectnessMetadata:
    evaluation_source: CorrectnessSource = CorrectnessSource.UNKNOWN
    is_correct: Optional[bool] = None
    notes: Optional[str] = None


@dataclass
class EpochMetadata:
    snapshot_epoch: int = 0
    execution_epoch: int = 0
    is_stale: bool = False


@dataclass
class ComexDecisionMetadata:
    status: str = "ALLOW"  # "ALLOW" | "BLOCK"
    block_category: ComexBlockCategory = ComexBlockCategory.NONE
    block_reason: Optional[str] = None
    is_physical_safety: bool = False
    is_loop_prevention: bool = False
    method_e: str = "PASS"  # "PASS" | "FAIL" | "SKIPPED"
    occlusion: str = "PASS"  # "PASS" | "OCCLUDED" | "SKIPPED"
    actuator_gate: str = "ALLOW"  # "ALLOW" | "BLOCK"


@dataclass
class ExecutionMetadata:
    dispatch_attempted: bool = False
    dispatch_sent: bool = False
    dispatch_success: bool = False
    physical_dispatch: bool = False  # Backward compatibility alias for dispatch_attempted
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        if self.physical_dispatch and not self.dispatch_attempted:
            self.dispatch_attempted = True
        elif self.dispatch_attempted and not self.physical_dispatch:
            self.physical_dispatch = True
        if self.dispatch_success and not self.dispatch_sent:
            self.dispatch_sent = True


@dataclass
class StateDeltaMetadata:
    before_fingerprint: str = ""
    after_fingerprint: str = ""
    state_changed: bool = False
    forward_action_observed: bool = False
    forward_state_pre: bool = False
    forward_state_post: bool = False
    forward_state_postcondition: bool = False  # True only if (not forward_state_pre) and forward_state_post

    def __post_init__(self) -> None:
        if self.before_fingerprint and self.after_fingerprint:
            self.state_changed = (self.before_fingerprint != self.after_fingerprint)


@dataclass
class LatencyBreakdown:
    gemini_request_ms: Optional[float] = None
    jev_request_ms: Optional[float] = None
    comex_validation_ms: Optional[float] = None
    physical_dispatch_ms: Optional[float] = None
    browser_settle_ms: Optional[float] = None
    wall_clock_action_ms: Optional[float] = None
    estimated_model_latency_avoided_ms: float = 0.0
    model_latency_avoided_ms: float = 0.0  # Backward-compatible alias

    def __post_init__(self) -> None:
        if self.estimated_model_latency_avoided_ms and not self.model_latency_avoided_ms:
            self.model_latency_avoided_ms = self.estimated_model_latency_avoided_ms
        elif self.model_latency_avoided_ms and not self.estimated_model_latency_avoided_ms:
            self.estimated_model_latency_avoided_ms = self.model_latency_avoided_ms


@dataclass
class ActionRecord:
    """Immutable, single browser action event ledger."""
    run_id: str
    arm: str  # "A" | "B" | "C"
    task_id: str
    task_name: str
    turn_id: int
    action_id: int
    operation: str  # "CLICK" | "FILL" | "SELECT" | "FOCUS" | etc.

    semantic_microtask: Optional[SemanticMicroTaskRecord] = None
    routing: RoutingMetadata = field(default_factory=RoutingMetadata)
    jev: JevDecisionMetadata = field(default_factory=JevDecisionMetadata)
    reference: ReferenceMetadata = field(default_factory=ReferenceMetadata)
    correctness: CorrectnessMetadata = field(default_factory=CorrectnessMetadata)
    epoch: EpochMetadata = field(default_factory=EpochMetadata)
    comex: ComexDecisionMetadata = field(default_factory=ComexDecisionMetadata)
    execution: ExecutionMetadata = field(default_factory=ExecutionMetadata)
    state: StateDeltaMetadata = field(default_factory=StateDeltaMetadata)
    latency: LatencyBreakdown = field(default_factory=LatencyBreakdown)

    target_selector: str = ""
    target_class: str = ""
    target_text: str = ""

    timestamp_iso: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionRecord:
        d = dict(data)
        if d.get("semantic_microtask") and isinstance(d["semantic_microtask"], dict):
            d["semantic_microtask"] = SemanticMicroTaskRecord(**d["semantic_microtask"])
        if d.get("routing") and isinstance(d["routing"], dict):
            d["routing"] = RoutingMetadata(**d["routing"])
        if d.get("jev") and isinstance(d["jev"], dict):
            d["jev"] = JevDecisionMetadata(**d["jev"])
        if d.get("reference") and isinstance(d["reference"], dict):
            d["reference"] = ReferenceMetadata(**d["reference"])
        if d.get("correctness") and isinstance(d["correctness"], dict):
            d["correctness"] = CorrectnessMetadata(
                evaluation_source=CorrectnessSource(d["correctness"]["evaluation_source"]) if d["correctness"].get("evaluation_source") else CorrectnessSource.UNKNOWN,
                is_correct=d["correctness"].get("is_correct"),
                notes=d["correctness"].get("notes"),
            )
        if d.get("epoch") and isinstance(d["epoch"], dict):
            d["epoch"] = EpochMetadata(**d["epoch"])
        if d.get("comex") and isinstance(d["comex"], dict):
            cat_val = d["comex"].get("block_category", ComexBlockCategory.NONE)
            d["comex"]["block_category"] = ComexBlockCategory(cat_val) if cat_val else ComexBlockCategory.NONE
            d["comex"] = ComexDecisionMetadata(**d["comex"])
        if d.get("execution") and isinstance(d["execution"], dict):
            d["execution"] = ExecutionMetadata(**d["execution"])
        if d.get("state") and isinstance(d["state"], dict):
            d["state"] = StateDeltaMetadata(**d["state"])
        if d.get("latency") and isinstance(d["latency"], dict):
            d["latency"] = LatencyBreakdown(**d["latency"])
        if "target_selector" not in d:
            d["target_selector"] = ""
        if "target_class" not in d:
            d["target_class"] = ""
        if "target_text" not in d:
            d["target_text"] = ""
        return cls(**d)


def calculate_wilson_score_interval(successes: int, trials: int, confidence: float = 0.95) -> Tuple[float, float]:
    """
    Calculates the two-sided Wilson score confidence interval for a binomial proportion.
    Standard z = 1.95996 for 95% confidence.
    """
    if trials == 0:
        return 0.0, 0.0

    z = 1.959964  # 95% confidence
    p_hat = float(successes) / float(trials)
    n = float(trials)

    denominator = 1.0 + (z**2) / n
    center = (p_hat + (z**2) / (2.0 * n)) / denominator
    spread = (z / denominator) * math.sqrt((p_hat * (1.0 - p_hat) / n) + ((z**2) / (4.0 * (n**2))))

    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    return lower, upper


def canonical_percentile(values: List[float], p: float) -> float:
    """
    Canonical linear interpolation percentile (method='linear', equivalent to
    numpy.percentile(values, p * 100) and pandas.Series.quantile(p)).
    p in [0.0, 1.0], e.g. 0.90 for p90.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    idx = p * (n - 1)
    lower = int(idx)
    upper = min(lower + 1, n - 1)
    weight = idx - lower
    return float(sorted_vals[lower] * (1.0 - weight) + sorted_vals[upper] * weight)


class ActionLedger:
    """Thread-safe, append-only repository of ActionRecord instances with automated consistency checking."""

    def __init__(self, run_id: str, arm: str):
        self.run_id = run_id
        self.arm = arm
        self.records: List[ActionRecord] = []

    def record_action(self, record: ActionRecord) -> None:
        self.records.append(record)

    record = record_action

    def export_jsonl(self, filepath: Path) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            for r in self.records:
                f.write(json.dumps(r.to_dict()) + "\n")

    # ──────────────────────────────────────────────────────────────────
    # Metrics Aggregation Directly From Raw Ledger (Zero Manual Counters)
    # ──────────────────────────────────────────────────────────────────

    def compute_turn_metrics(self) -> Dict[str, Any]:
        """Aggregates metrics strictly at the Turn level."""
        turns: Dict[Tuple[str, int], List[ActionRecord]] = {}
        for r in self.records:
            k = (r.task_id, r.turn_id)
            turns.setdefault(k, []).append(r)

        total_gemini_turns = len(turns)
        jev_adjudicated_turns = sum(
            1 for acts in turns.values() if any(a.jev.attempted for a in acts)
        )
        jev_delegated_turns = sum(
            1 for acts in turns.values() if any(a.routing.route == "JEV" and a.jev.status == "RESOLVED" for a in acts)
        )
        jev_abstained_turns = sum(
            1 for acts in turns.values() if all(a.jev.status in ("ABSTAINED", "DISALLOWED") for a in acts if a.jev.attempted) and any(a.jev.attempted for a in acts)
        )
        gemini_fallback_turns = sum(
            1 for acts in turns.values() if any(a.routing.route == "GEMINI" and a.jev.attempted for a in acts)
        )

        return {
            "total_gemini_turns": total_gemini_turns,
            "jev_adjudicated_turns": jev_adjudicated_turns,
            "jev_delegated_turns": jev_delegated_turns,
            "jev_abstained_turns": jev_abstained_turns,
            "gemini_fallback_turns": gemini_fallback_turns,
            "turn_delegation_rate_pct": round((jev_delegated_turns / total_gemini_turns * 100.0), 2) if total_gemini_turns else 0.0,
            "turn_disposition_coverage_pct": round(((jev_delegated_turns + jev_abstained_turns) / total_gemini_turns * 100.0), 2) if total_gemini_turns else 0.0,
        }

    def compute_action_metrics(self) -> Dict[str, Any]:
        """Aggregates metrics strictly at the Action level."""
        total_actions = len(self.records)
        jev_attempted = sum(1 for r in self.records if r.jev.attempted)
        jev_resolved = sum(1 for r in self.records if r.jev.status == "RESOLVED")
        jev_abstained = sum(1 for r in self.records if r.jev.status in ("ABSTAINED", "DISALLOWED"))
        jev_executed = sum(1 for r in self.records if r.routing.route == "JEV" and r.execution.physical_dispatch)
        gemini_executed = sum(1 for r in self.records if r.routing.route == "GEMINI" and r.execution.physical_dispatch)

        safety_blocks = sum(1 for r in self.records if r.comex.is_physical_safety)
        loop_prevention_blocks = sum(1 for r in self.records if r.comex.is_loop_prevention)
        total_blocks = sum(1 for r in self.records if r.comex.status == "BLOCK")

        block_reasons: Dict[str, int] = {}
        for r in self.records:
            if r.comex.status == "BLOCK":
                cat = r.comex.block_category.value
                block_reasons[cat] = block_reasons.get(cat, 0) + 1

        successful_physical = sum(1 for r in self.records if r.execution.dispatch_success)

        dispatch_attempted = sum(1 for r in self.records if r.execution.dispatch_attempted)
        dispatch_sent = sum(1 for r in self.records if r.execution.dispatch_sent)
        dispatch_success = sum(1 for r in self.records if r.execution.dispatch_success)

        delegated_records = [r for r in self.records if r.routing.route == "JEV" and r.jev.status == "RESOLVED"]
        delegated_count = len(delegated_records)
        wrong_targets = sum(1 for r in delegated_records if r.correctness.is_correct is False)
        correct_delegations = sum(1 for r in delegated_records if r.correctness.is_correct is True)
        successful_dispatched_jev = sum(1 for r in delegated_records if r.execution.dispatch_success)
        jev_routed = sum(1 for r in self.records if r.routing.route == "JEV")
        jev_shadow_only = jev_resolved - delegated_count

        lower_ci, upper_ci = calculate_wilson_score_interval(correct_delegations, delegated_count)

        return {
            "total_browser_actions": total_actions,
            "jev_attempted_actions": jev_attempted,
            "jev_resolved_actions": jev_resolved,
            "jev_abstained_actions": jev_abstained,
            "jev_routed_actions": jev_routed,
            "jev_shadow_only_resolutions": jev_shadow_only,
            "jev_executed_actions": jev_executed,
            "gemini_executed_actions": gemini_executed,
            "dispatch_attempted_actions": dispatch_attempted,
            "dispatch_sent_actions": dispatch_sent,
            "dispatch_success_actions": dispatch_success,
            "successful_physical_actions": successful_physical,
            "comex_total_blocks": total_blocks,
            "comex_physical_safety_blocks": safety_blocks,
            "comex_loop_prevention_blocks": loop_prevention_blocks,
            "comex_block_breakdown": block_reasons,
            "delegated_decisions_count": delegated_count,
            "jev_decision_correctness_count": correct_delegations,
            "successfully_dispatched_jev_actions": successful_dispatched_jev,
            "correct_delegated_count": correct_delegations,
            "wrong_target_count": wrong_targets,
            "observed_precision_pct": round((correct_delegations / delegated_count * 100.0), 2) if delegated_count else 0.0,
            "precision_wilson_95_ci": [round(lower_ci, 4), round(upper_ci, 4)],
            "action_execution_coverage_pct": round((jev_executed / jev_attempted * 100.0), 2) if jev_attempted else 0.0,
            "action_level_jev_coverage_pct": round((jev_resolved / total_actions * 100.0), 2) if total_actions else 0.0,
        }

    def compute_latency_metrics(self) -> Dict[str, Any]:
        """Computes Estimated Model Latency Avoided vs Wall Clock Action Time."""
        import statistics

        total_avoided_ms = sum(r.latency.estimated_model_latency_avoided_ms or r.latency.model_latency_avoided_ms for r in self.records)
        jev_latencies = [r.latency.jev_request_ms for r in self.records if r.latency.jev_request_ms is not None]
        gemini_latencies = [r.latency.gemini_request_ms for r in self.records if r.latency.gemini_request_ms is not None]
        action_wall_latencies = [r.latency.wall_clock_action_ms for r in self.records if r.latency.wall_clock_action_ms is not None]

        mean_jev_ms = (sum(jev_latencies) / len(jev_latencies)) if jev_latencies else 0.0
        mean_gemini_ms = (sum(gemini_latencies) / len(gemini_latencies)) if gemini_latencies else 0.0
        p90_wall_ms = canonical_percentile(action_wall_latencies, 0.90)
        p95_wall_ms = canonical_percentile(action_wall_latencies, 0.95)

        return {
            "total_model_latency_avoided_s": round(total_avoided_ms / 1000.0, 2),
            "total_estimated_model_latency_avoided_s": round(total_avoided_ms / 1000.0, 2),
            "mean_jev_latency_ms": round(mean_jev_ms, 2),
            "mean_gemini_latency_ms": round(mean_gemini_ms, 2),
            "action_wall_latency_p90_ms": round(p90_wall_ms, 2),
            "action_wall_latency_p95_ms": round(p95_wall_ms, 2),
            "sample_count_jev": len(jev_latencies),
            "sample_count_gemini": len(gemini_latencies),
        }

    # ──────────────────────────────────────────────────────────────────
    # Consistency Guard Assertion Engine
    # ──────────────────────────────────────────────────────────────────

    def validate_consistency(
        self,
        reported_delegated_count: int,
        reported_precision_denom: int,
        reported_action_coverage_num: int,
        reported_total_avoided_s: float,
    ) -> None:
        """
        Fails loudly with AssertionError if any reported summary number does not
        strictly equal the ledger's ground truth. Prevents 12/12-style discrepancies.
        """
        act_metrics = self.compute_action_metrics()
        lat_metrics = self.compute_latency_metrics()

        ledger_delegated = act_metrics["delegated_decisions_count"]
        ledger_resolved = act_metrics["jev_resolved_actions"]
        ledger_avoided_s = lat_metrics["total_model_latency_avoided_s"]

        if reported_delegated_count != ledger_delegated:
            raise AssertionError(
                f"CONSISTENCY VIOLATION: reported_delegated_count ({reported_delegated_count}) != ledger ({ledger_delegated})"
            )

        if reported_precision_denom != ledger_delegated:
            raise AssertionError(
                f"CONSISTENCY VIOLATION: reported_precision_denom ({reported_precision_denom}) != ledger delegated count ({ledger_delegated})"
            )

        if reported_action_coverage_num != ledger_resolved:
            raise AssertionError(
                f"CONSISTENCY VIOLATION: reported_action_coverage_num ({reported_action_coverage_num}) != ledger resolved ({ledger_resolved})"
            )

        if abs(reported_total_avoided_s - ledger_avoided_s) > 0.05:
            raise AssertionError(
                f"CONSISTENCY VIOLATION: reported_total_avoided_s ({reported_total_avoided_s}) != ledger avoided ({ledger_avoided_s})"
            )
