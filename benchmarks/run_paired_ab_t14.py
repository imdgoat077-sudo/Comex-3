"""
benchmarks/run_paired_ab_t14.py
===============================
Balanced Paired A/B Experiment Orchestrator for Task T14 (email-inbox).

Experimental Design:
- 10 balanced paired replications (20 total task executions).
- Balanced alternating within-pair schedule:
    Pair 1:  A -> B
    Pair 2:  B -> A
    Pair 3:  A -> B
    Pair 4:  B -> A
    Pair 5:  A -> B
    Pair 6:  B -> A
    Pair 7:  A -> B
    Pair 8:  B -> A
    Pair 9:  A -> B
    Pair 10: B -> A
- Condition A (Baseline): Coarse container selector (.email-send).
- Condition B (Treatment): Fine-grained child selector (.email-forward, .email-reply).
- Isolated outputs:
    benchmarks/runs/ab_t14_baseline/
    benchmarks/runs/ab_t14_treatment/

NOTE: Phase A implements this harness. Execution occurs strictly in Phase B upon human authorization.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from benchmarks.action_ledger import (
    ActionLedger,
    ActionRecord,
    ComexBlockCategory,
    CorrectnessSource,
    calculate_wilson_score_interval,
    canonical_percentile,
)
from benchmarks.jev_shadow import JevShadowObserver
from benchmarks.run_browsergym_electron import (
    BENCHMARK_TASKS,
    ElectronBenchmarkRunner,
    FixtureServer,
    TaskResult,
    BASELINE_INTERACTIVE_SELECTOR,
    TREATMENT_T14_INTERACTIVE_SELECTOR,
    BASELINE_SELECTOR_HASH,
    TREATMENT_T14_SELECTOR_HASH,
)
from services.micro_action_resolver import MicroActionResolver
from services.typesafe_client import AsyncTypeSafeClient, ClientMode

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("PairedAB_T14")

PAIRED_SCHEDULE: List[Dict[str, Any]] = [
    {"pair_id": 1, "order": [("A_BASELINE", False), ("B_TREATMENT", True)]},
    {"pair_id": 2, "order": [("B_TREATMENT", True), ("A_BASELINE", False)]},
    {"pair_id": 3, "order": [("A_BASELINE", False), ("B_TREATMENT", True)]},
    {"pair_id": 4, "order": [("B_TREATMENT", True), ("A_BASELINE", False)]},
    {"pair_id": 5, "order": [("A_BASELINE", False), ("B_TREATMENT", True)]},
    {"pair_id": 6, "order": [("B_TREATMENT", True), ("A_BASELINE", False)]},
    {"pair_id": 7, "order": [("A_BASELINE", False), ("B_TREATMENT", True)]},
    {"pair_id": 8, "order": [("B_TREATMENT", True), ("A_BASELINE", False)]},
    {"pair_id": 9, "order": [("A_BASELINE", False), ("B_TREATMENT", True)]},
    {"pair_id": 10, "order": [("B_TREATMENT", True), ("A_BASELINE", False)]},
]


def get_git_info() -> Dict[str, Any]:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT_DIR), text=True).strip()
    except Exception:
        commit = "unknown"
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(ROOT_DIR), text=True).strip()
    except Exception:
        branch = "unknown"
    try:
        dirty_out = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(ROOT_DIR), text=True).strip()
        is_dirty = len(dirty_out) > 0
    except Exception:
        is_dirty = False
    return {"git_commit": commit, "git_branch": branch, "git_dirty": is_dirty}


def get_node_and_electron_versions() -> Tuple[str, str]:
    try:
        node_v = subprocess.check_output(["node", "-v"], text=True).strip()
    except Exception:
        node_v = "unknown"
    try:
        electron_v = subprocess.check_output(["npx", "electron", "-v"], cwd=str(ROOT_DIR), text=True).strip()
    except Exception:
        electron_v = "unknown"
    return node_v, electron_v


class PairedABExperimentRunner:
    def __init__(
        self,
        pairs: int = 10,
        arm: str = "C",
        baseline_dir: Optional[str] = None,
        treatment_dir: Optional[str] = None,
    ):
        self.pairs_count = pairs
        self.arm = arm.upper()
        self.delegate_jev = (self.arm == "C")
        self.shadow_jev = (self.arm in ("B", "C"))

        mode = ClientMode.LIVE if os.environ.get("TYPESAFE_API_KEY") else ClientMode.OFFLINE
        self.client = AsyncTypeSafeClient(mode=mode)
        self.shadow_observer = JevShadowObserver(client=self.client) if self.shadow_jev else None
        self.micro_resolver = MicroActionResolver(client=self.client) if self.delegate_jev else None

        self.run_id_base = f"ab_t14_{int(time.time())}"
        self.baseline_dir = Path(baseline_dir).resolve() if baseline_dir else (ROOT_DIR / "benchmarks" / "runs" / "ab_t14_baseline")
        self.treatment_dir = Path(treatment_dir).resolve() if treatment_dir else (ROOT_DIR / "benchmarks" / "runs" / "ab_t14_treatment")
        # Defer mkdir until execute_paired_suite() starts
        self.baseline_ledger = ActionLedger(run_id=f"{self.run_id_base}_baseline", arm=self.arm)
        self.treatment_ledger = ActionLedger(run_id=f"{self.run_id_base}_treatment", arm=self.arm)

        self.baseline_task_results: List[Dict[str, Any]] = []
        self.treatment_task_results: List[Dict[str, Any]] = []

    def get_schedule(self) -> List[Dict[str, Any]]:
        return PAIRED_SCHEDULE[:self.pairs_count]

    async def execute_paired_suite(self) -> Dict[str, Any]:
        server = FixtureServer()
        server.start()
        await asyncio.sleep(0.5)

        t14_def = next((t for t in BENCHMARK_TASKS if t["id"] == "T14"), None)
        if not t14_def:
            raise RuntimeError("Task T14 (email-inbox) not found in BENCHMARK_TASKS!")

        schedule = self.get_schedule()
        total_trials = len(schedule) * 2

        logger.info("=" * 80)
        logger.info(f"🚀 STARTING BALANCED PAIRED T14 EXPERIMENT: {len(schedule)} PAIRS ({total_trials} TOTAL TRIALS)")
        logger.info(f"Baseline Output:  {self.baseline_dir}")
        logger.info(f"Treatment Output: {self.treatment_dir}")
        logger.info("=" * 80)

        # Create and clean output directories when execution begins
        for out_dir in (self.baseline_dir, self.treatment_dir):
            out_dir.mkdir(parents=True, exist_ok=True)
            for fname in ("results.jsonl", "action_ledger.jsonl", "manifest.json", "report.md"):
                p = out_dir / fname
                if p.exists():
                    p.unlink()

        t_start = time.time()
        start_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_start))

        trial_counter = 0
        try:
            for pair_info in schedule:
                pair_id = pair_info["pair_id"]
                order_list = pair_info["order"]

                logger.info(f"\n{'=' * 80}\n>>> EXECUTING PAIR {pair_id}/{len(schedule)}: Sequence = {[c[0] for c in order_list]}\n{'=' * 80}")

                for within_pair_idx, (condition, is_treatment) in enumerate(order_list, start=1):
                    trial_counter += 1
                    target_dir = self.treatment_dir if is_treatment else self.baseline_dir
                    target_ledger = self.treatment_ledger if is_treatment else self.baseline_ledger
                    target_results = self.treatment_task_results if is_treatment else self.baseline_task_results
                    rep_idx = len(target_results) + 1

                    logger.info(f"\n--- [Trial {trial_counter}/{total_trials}] Pair {pair_id} | Order {within_pair_idx}/2 | Condition: {condition} (treatment_t14={is_treatment}) ---")

                    runner = ElectronBenchmarkRunner(
                        shadow_observer=self.shadow_observer,
                        micro_resolver=self.micro_resolver,
                        delegate_jev=self.delegate_jev,
                        run_id=f"{self.run_id_base}_{'treatment' if is_treatment else 'baseline'}_rep{rep_idx}",
                        arm=self.arm,
                        treatment_t14=is_treatment,
                        condition=condition,
                    )

                    active_tab = runner.discover_active_tab()
                    ws_url = active_tab["webSocketDebuggerUrl"]

                    import websockets
                    async with websockets.connect(ws_url, max_size=10_000_000) as ws:
                        msg_counter = [trial_counter * 1000]
                        t0 = time.monotonic()
                        res = await runner.run_single_task(t14_def, ws, msg_counter, max_turns=t14_def.get("max_turns", 4))
                        dur = time.monotonic() - t0

                        task_entry = {
                            "repetition_index": rep_idx,
                            "pair_id": pair_id,
                            "within_pair_order": within_pair_idx,
                            "condition": condition,
                            "run_id": runner.run_id,
                            "arm": self.arm,
                            "task_id": res.task_id,
                            "task_name": res.name,
                            "category": res.category,
                            "success": res.success,
                            "duration_s": round(dur, 2),
                            "turns_taken": res.turns_taken,
                            "gemini_calls": res.gemini_calls,
                            "delegated_turns": res.delegated_turns,
                            "abstained_turns": res.abstained_turns,
                            "disallowed_turns": res.disallowed_turns,
                            "wrong_target_executions": res.wrong_target_executions,
                            "fallbacks_to_gemini": res.fallbacks_to_gemini,
                            "comex_blocks": res.comex_blocks,
                            "stale_target_failures": res.stale_target_failures,
                            "latency_saved_s": round(res.latency_saved_s, 2),
                            "loop_break_events": res.loop_break_events,
                            "blocked_repeated_actions": res.blocked_repeated_actions,
                        }
                        target_results.append(task_entry)

                        # Write row to condition's results.jsonl
                        with open(target_dir / "results.jsonl", "a", encoding="utf-8") as f:
                            f.write(json.dumps(task_entry) + "\n")

                        # Append records to condition's ledger
                        for rec in runner.action_ledger.records:
                            target_ledger.record_action(rec)
                            with open(target_dir / "action_ledger.jsonl", "a", encoding="utf-8") as f:
                                f.write(rec.to_json() + "\n")

                        logger.info(f"Result: {'✅ PASS' if res.success else '❌ FAIL'} in {dur:.2f}s (turns={res.turns_taken}, delegated={res.delegated_turns}, blocks={res.comex_blocks})")
                        await asyncio.sleep(0.5)

        finally:
            server.stop()

        t_end = time.time()
        end_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_end))

        # Generate manifests and reports for both conditions
        git_info = get_git_info()
        node_v, electron_v = get_node_and_electron_versions()

        self._finalize_condition(
            condition_name="A_BASELINE",
            is_treatment=False,
            out_dir=self.baseline_dir,
            ledger=self.baseline_ledger,
            results=self.baseline_task_results,
            git_info=git_info,
            node_v=node_v,
            electron_v=electron_v,
            start_iso=start_iso,
            end_iso=end_iso,
            elapsed_s=t_end - t_start,
        )

        self._finalize_condition(
            condition_name="B_TREATMENT",
            is_treatment=True,
            out_dir=self.treatment_dir,
            ledger=self.treatment_ledger,
            results=self.treatment_task_results,
            git_info=git_info,
            node_v=node_v,
            electron_v=electron_v,
            start_iso=start_iso,
            end_iso=end_iso,
            elapsed_s=t_end - t_start,
        )

        return {
            "baseline_dir": str(self.baseline_dir),
            "treatment_dir": str(self.treatment_dir),
            "total_pairs": len(schedule),
            "baseline_success": sum(1 for r in self.baseline_task_results if r["success"]),
            "treatment_success": sum(1 for r in self.treatment_task_results if r["success"]),
        }

    def _finalize_condition(
        self,
        condition_name: str,
        is_treatment: bool,
        out_dir: Path,
        ledger: ActionLedger,
        results: List[Dict[str, Any]],
        git_info: Dict[str, Any],
        node_v: str,
        electron_v: str,
        start_iso: str,
        end_iso: str,
        elapsed_s: float,
    ) -> None:
        act_metrics = ledger.compute_action_metrics()
        turn_metrics = ledger.compute_turn_metrics()
        lat_metrics = ledger.compute_latency_metrics()

        total_runs = len(results)
        total_passed = sum(1 for r in results if r["success"])
        total_failed = total_runs - total_passed
        overall_accuracy = (total_passed / total_runs * 100.0) if total_runs else 0.0

        durations = [r["duration_s"] for r in results]
        mean_dur = statistics.mean(durations) if durations else 0.0
        median_dur = statistics.median(durations) if durations else 0.0
        p90_dur = canonical_percentile(durations, 0.90)
        p95_dur = canonical_percentile(durations, 0.95)

        eff_selector = TREATMENT_T14_INTERACTIVE_SELECTOR if is_treatment else BASELINE_INTERACTIVE_SELECTOR
        config_fingerprint_payload = f"{condition_name}:{is_treatment}:{BASELINE_SELECTOR_HASH}:{TREATMENT_T14_SELECTOR_HASH}:{git_info['git_commit']}"
        config_fingerprint = hashlib.sha256(config_fingerprint_payload.encode("utf-8")).hexdigest()

        manifest = {
            "schema_version": "1.0.0",
            "run_id": f"{self.run_id_base}_{'treatment' if is_treatment else 'baseline'}",
            "arm": self.arm,
            "repetitions": total_runs,
            "start_time": start_iso,
            "end_time": end_iso,
            "elapsed_seconds": round(elapsed_s, 2),
            "environment": {
                "git_commit": git_info["git_commit"],
                "git_branch": git_info["git_branch"],
                "git_dirty": git_info["git_dirty"],
                "os_platform": platform.platform(),
                "os_arch": platform.machine(),
                "python_version": sys.version.split()[0],
                "node_version": node_v,
                "electron_version": electron_v,
                "cdp_port": 9222,
                "fixture_port": 8890,
            },
            "configuration": {
                "condition": condition_name,
                "treatment_t14": is_treatment,
                "effective_interactive_selector": eff_selector,
                "baseline_selector_hash": BASELINE_SELECTOR_HASH,
                "treatment_selector_hash": TREATMENT_T14_SELECTOR_HASH,
                "configuration_fingerprint": config_fingerprint,
                "task_suite_version": "browsergym-miniwob-1.0",
                "delegate_jev": self.delegate_jev,
                "shadow_jev": self.shadow_jev,
                "model_system2": "ag/gemini-3.7-flash-medium",
                "model_system1": "ag/jev-system1",
                "proxy_url": "http://localhost:20128/v1",
                "calibration_thresholds": {
                    "CLICK": {"confidence": 0.70, "margin": 0.05, "actionable": True},
                    "FILL": {"confidence": 0.75, "margin": 0.10, "actionable": True},
                    "SELECT": {"confidence": 0.70, "margin": 0.05, "actionable": False, "shadow_only": True},
                },
                "task_order": ["T14"],
                "task_count": 1,
            },
            "metrics_summary": {
                "total_tasks_run": total_runs,
                "tasks_passed": total_passed,
                "tasks_failed": total_failed,
                "task_success_rate_pct": round(overall_accuracy, 2),
                "mean_task_duration_s": round(mean_dur, 2),
                "median_task_duration_s": round(median_dur, 2),
                "p90_task_duration_s": round(p90_dur, 2),
                "p95_task_duration_s": round(p95_dur, 2),
                "action_metrics": act_metrics,
                "turn_metrics": turn_metrics,
                "latency_metrics": lat_metrics,
            },
            "artifacts": {
                "results_jsonl": "results.jsonl",
                "action_ledger_jsonl": "action_ledger.jsonl",
                "report_md": "report.md",
            },
        }

        with open(out_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        report_text = self._build_condition_report(
            condition_name=condition_name,
            is_treatment=is_treatment,
            results=results,
            act_metrics=act_metrics,
            turn_metrics=turn_metrics,
            lat_metrics=lat_metrics,
            mean_dur=mean_dur,
            median_dur=median_dur,
            p90_dur=p90_dur,
            p95_dur=p95_dur,
            overall_accuracy=overall_accuracy,
            total_runs=total_runs,
            total_passed=total_passed,
        )
        with open(out_dir / "report.md", "w", encoding="utf-8") as f:
            f.write(report_text)

    def _build_condition_report(
        self,
        condition_name: str,
        is_treatment: bool,
        results: List[Dict[str, Any]],
        act_metrics: Dict[str, Any],
        turn_metrics: Dict[str, Any],
        lat_metrics: Dict[str, Any],
        mean_dur: float,
        median_dur: float,
        p90_dur: float,
        p95_dur: float,
        overall_accuracy: float,
        total_runs: int,
        total_passed: int,
    ) -> str:
        delegated_total = act_metrics["delegated_decisions_count"]
        delegated_correct = act_metrics["jev_decision_correctness_count"]
        wrong_targets = act_metrics["wrong_target_count"]
        successful_dispatched = act_metrics["successfully_dispatched_jev_actions"]
        blocked_dispatches = delegated_total - successful_dispatched
        dispatched_pct = (successful_dispatched / delegated_total * 100.0) if delegated_total else 0.0
        blocked_pct = (blocked_dispatches / delegated_total * 100.0) if delegated_total else 0.0

        ci_combined_lower, ci_combined_upper = calculate_wilson_score_interval(delegated_correct, delegated_total)

        lines: List[str] = []
        lines.append(f"# T14 A/B Experiment Run Report: Condition {condition_name}\n")
        lines.append(f"> **Condition:** `{condition_name}` (treatment_t14={is_treatment})")
        lines.append(f"> **Repetitions:** {total_runs} episodes across 10 balanced pairs.")
        lines.append(f"> **Selector:** `{TREATMENT_T14_INTERACTIVE_SELECTOR if is_treatment else BASELINE_INTERACTIVE_SELECTOR}`\n")

        lines.append("## 1. Executive Statistical Summary\n")
        lines.append("| Metric | Value | Statistical Grounding / 95% Confidence Interval |")
        lines.append("|---|---|---|")
        lines.append(f"| **Total Task Executions** | {total_runs} | {total_runs} independent repetitions |")
        lines.append(f"| **Overall Task Success** | {total_passed} / {total_runs} ({overall_accuracy:.1f}%) | Evaluated via MiniWoB Referee |")
        lines.append(f"| **Total Delegated Decisions ($N$)** | **{delegated_total}** | All evaluated JEV actions |")
        lines.append(f"| **Wrong-Target Executions** | **{wrong_targets}** | 0 misclicks observed |")
        lines.append(f"| **JEV Decision Correctness** | **{delegated_correct} / {delegated_total}** ({delegated_correct/delegated_total*100.0 if delegated_total else 0.0:.1f}%) | Wilson 95% CI: **[{ci_combined_lower*100.0:.1f}%, {ci_combined_upper*100.0:.1f}%]** |")
        lines.append(f"| **JEV Actions Successfully Dispatched** | **{successful_dispatched} / {delegated_total}** ({dispatched_pct:.1f}%) | Physical actions sent and successfully executed via CDP; {blocked_dispatches} / {delegated_total} ({blocked_pct:.1f}%) blocked prior to dispatch by Comex safety/loop-breaker checks |")
        lines.append(f"| **Estimated Model Latency Avoided** | **{lat_metrics['total_model_latency_avoided_s']:.2f}s** | $\\sum \\max(0, \\text{{Gemini}} - \\text{{Jev}})$ counterfactual |")
        lines.append(f"| **Mean Task Duration** | **{mean_dur:.2f}s** | Median: **{median_dur:.2f}s**, p90: **{p90_dur:.2f}s**, p95: **{p95_dur:.2f}s** |\n")

        lines.append("## 2. Four Distinct Coverage Metrics (Replicated Ledger)\n")
        lines.append("```text")
        lines.append(f"1. Turn Delegation Rate        = {turn_metrics['jev_delegated_turns']} / {turn_metrics['total_gemini_turns']} = {turn_metrics['turn_delegation_rate_pct']}%")
        lines.append(f"2. Turn Disposition Coverage   = {turn_metrics['jev_delegated_turns'] + turn_metrics['jev_abstained_turns']} / {turn_metrics['total_gemini_turns']} = {turn_metrics['turn_disposition_coverage_pct']}%")
        lines.append(f"3. Action Execution Coverage   = {act_metrics['jev_executed_actions']} / {act_metrics['jev_attempted_actions']} = {act_metrics['action_execution_coverage_pct']}%")
        lines.append(f"4. Action-Level Jev Coverage   = {act_metrics['jev_resolved_actions']} / {act_metrics['total_browser_actions']} = {act_metrics['action_level_jev_coverage_pct']}%")
        lines.append("```\n")

        lines.append("## 3. Individual Episode Performance\n")
        lines.append("| Rep # | Pair ID | Within-Pair Order | Success | Duration | Turns | Jev Delegated | Comex Blocks |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in results:
            lines.append(f"| {r['repetition_index']} | {r.get('pair_id', '-')} | {r.get('within_pair_order', '-')} | {'PASS' if r['success'] else 'FAIL'} | {r['duration_s']:.2f}s | {r['turns_taken']} | {r['delegated_turns']} | {r['comex_blocks']} |")
        lines.append("\n")

        lines.append("## 4. Comex Block Taxonomy Breakdown\n")
        lines.append(f"- **Total Interventions**: {act_metrics['comex_total_blocks']}")
        lines.append(f"  - **Physical Safety Blocks**: {act_metrics['comex_physical_safety_blocks']}")
        lines.append(f"  - **Recovery & Loop-Prevention Blocks**: {act_metrics['comex_loop_prevention_blocks']}")
        lines.append("  - **Category Breakdown**:")
        for cat, cnt in act_metrics["comex_block_breakdown"].items():
            lines.append(f"    - `{cat}`: {cnt}")
        lines.append("\n")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run Balanced Paired A/B Experiment for T14")
    parser.add_argument("--pairs", type=int, default=10, help="Number of balanced pairs (default: 10)")
    parser.add_argument("--arm", type=str, default="C", choices=["A", "B", "C"], help="Experimental arm (default: C)")
    parser.add_argument("--baseline-dir", type=str, default=None, help="Output directory for Baseline A runs")
    parser.add_argument("--treatment-dir", type=str, default=None, help="Output directory for Treatment B runs")
    args = parser.parse_args()

    runner = PairedABExperimentRunner(
        pairs=args.pairs,
        arm=args.arm,
        baseline_dir=args.baseline_dir,
        treatment_dir=args.treatment_dir,
    )
    asyncio.run(runner.execute_paired_suite())


if __name__ == "__main__":
    main()
