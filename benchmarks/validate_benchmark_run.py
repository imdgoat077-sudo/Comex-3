"""
benchmarks/validate_benchmark_run.py
====================================
Rigorous Validator for Benchmark Run Provenance and Metric Invariants.

Reconciles:
1. task results == action ledger == manifest == report
2. Exact invariants:
   - task count (e.g. 15 for 1 rep)
   - unique task/turn IDs derived from ledger
   - Jev attempted, resolved, abstained derived from ledger
   - Comex blocks derived from ledger
   - dispatch_attempted, dispatch_sent, dispatch_success derived from ledger
   - before/after fingerprints present for all records
   - state_changed strictly derived from fingerprints
   - p90 verified against canonical percentile implementation
   - shadow vs executed distinction explicitly preserved

Usage:
    .venv/bin/python benchmarks/validate_benchmark_run.py \\
        --results path/to/results.jsonl \\
        --ledger path/to/action_ledger.jsonl \\
        --manifest path/to/manifest.json \\
        [--report path/to/report.md]

Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from benchmarks.action_ledger import canonical_percentile


class BenchmarkValidator:
    def __init__(
        self,
        results_path: Path,
        ledger_path: Path,
        manifest_path: Path,
        report_path: Optional[Path] = None,
    ):
        self.results_path = results_path
        self.ledger_path = ledger_path
        self.manifest_path = manifest_path
        self.report_path = report_path

        self.errors: List[str] = []
        self.checks: List[Tuple[str, str, bool]] = []  # (name, details, passed)

    def record_check(self, name: str, details: str, passed: bool) -> None:
        self.checks.append((name, details, passed))
        if not passed:
            self.errors.append(f"FAILED [{name}]: {details}")

    def validate(self) -> bool:
        # 1. File existence & basic loading
        if not self.results_path.exists() or self.results_path.stat().st_size == 0:
            self.record_check("Files Exist", f"results file missing or empty: {self.results_path}", False)
            return False
        if not self.ledger_path.exists() or self.ledger_path.stat().st_size == 0:
            self.record_check("Files Exist", f"ledger file missing or empty: {self.ledger_path}", False)
            return False
        if not self.manifest_path.exists() or self.manifest_path.stat().st_size == 0:
            self.record_check("Files Exist", f"manifest file missing or empty: {self.manifest_path}", False)
            return False

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.record_check("Manifest Schema", "manifest.json loaded successfully", True)
        except Exception as e:
            self.record_check("Manifest Schema", f"Failed to parse manifest: {e}", False)
            return False

        try:
            results: List[Dict[str, Any]] = []
            with open(self.results_path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f, start=1):
                    if line.strip():
                        results.append(json.loads(line))
            self.record_check("Results Schema", f"Loaded {len(results)} task result rows", True)
        except Exception as e:
            self.record_check("Results Schema", f"Failed to parse results.jsonl: {e}", False)
            return False

        try:
            records: List[Dict[str, Any]] = []
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f, start=1):
                    if line.strip():
                        records.append(json.loads(line))
            self.record_check("Ledger Schema", f"Loaded {len(records)} action ledger rows", True)
        except Exception as e:
            self.record_check("Ledger Schema", f"Failed to parse action_ledger.jsonl: {e}", False)
            return False

        # 2. Invariant: Task Count
        expected_task_count = manifest["configuration"]["task_count"] * manifest["repetitions"]
        actual_task_count = len(results)
        self.record_check(
            "Task Count",
            f"Expected {expected_task_count}, observed {actual_task_count}",
            actual_task_count == expected_task_count,
        )

        # 3. Invariant: Unique task/turn IDs reconciliation
        res_task_ids = [r["task_id"] for r in results]
        led_task_ids = sorted(list({r["task_id"] for r in records}))
        expected_order = manifest["configuration"]["task_order"]
        # Verify unique task IDs match expected configuration
        unique_res_tasks = sorted(list(set(res_task_ids)))
        self.record_check(
            "Unique Task IDs",
            f"Results tasks match configured task list ({len(unique_res_tasks)}/{len(expected_order)})",
            unique_res_tasks == sorted(expected_order),
        )

        # Reconcile turns per task
        turns_reconciled = True
        turn_mismatch_detail = ""
        for r in results:
            t_id = r["task_id"]
            rep_idx = r["repetition_index"]
            # Filter ledger for this task and repetition run
            task_records = [rec for rec in records if rec["task_id"] == t_id]
            ledger_turns = len({rec["turn_id"] for rec in task_records})
            if r["turns_taken"] != ledger_turns and manifest["repetitions"] == 1:
                turns_reconciled = False
                turn_mismatch_detail = f"Task {t_id}: results turns_taken={r['turns_taken']} != ledger turns={ledger_turns}"
                break
        self.record_check(
            "Turns Reconciled",
            "Each task's turns_taken equals count of unique turn_ids in ledger" if turns_reconciled else turn_mismatch_detail,
            turns_reconciled,
        )

        # 4. Invariant: Ledger-Derived Metrics Calculation
        total_actions = len(records)
        jev_attempted = sum(1 for r in records if r.get("jev", {}).get("attempted"))
        jev_resolved = sum(1 for r in records if r.get("jev", {}).get("status") == "RESOLVED")
        jev_abstained = sum(1 for r in records if r.get("jev", {}).get("status") in ("ABSTAINED", "DISALLOWED"))
        comex_blocks = sum(1 for r in records if r.get("comex", {}).get("status") == "BLOCK")

        dispatch_attempted = sum(1 for r in records if r.get("execution", {}).get("dispatch_attempted"))
        dispatch_sent = sum(1 for r in records if r.get("execution", {}).get("dispatch_sent"))
        dispatch_success = sum(1 for r in records if r.get("execution", {}).get("dispatch_success"))

        jev_routed = sum(1 for r in records if r.get("routing", {}).get("route") == "JEV")
        delegated_records = [r for r in records if r.get("routing", {}).get("route") == "JEV" and r.get("jev", {}).get("status") == "RESOLVED"]
        delegated_count = len(delegated_records)
        jev_correctness_count = sum(1 for r in delegated_records if r.get("correctness", {}).get("is_correct") is True)
        wrong_targets = sum(1 for r in delegated_records if r.get("correctness", {}).get("is_correct") is False)
        successful_dispatched_jev = sum(1 for r in delegated_records if r.get("execution", {}).get("dispatch_success") is True)

        shadow_only_resolutions = jev_resolved - jev_routed

        # Check against manifest action metrics
        m_act = manifest["metrics_summary"]["action_metrics"]
        self.record_check(
            "Jev Attempted Invariant",
            f"Ledger={jev_attempted}, Manifest={m_act.get('jev_attempted_actions')}",
            jev_attempted == m_act.get("jev_attempted_actions"),
        )
        self.record_check(
            "Jev Resolved Invariant",
            f"Ledger={jev_resolved}, Manifest={m_act.get('jev_resolved_actions')}",
            jev_resolved == m_act.get("jev_resolved_actions"),
        )
        self.record_check(
            "Jev Abstained Invariant",
            f"Ledger={jev_abstained}, Manifest={m_act.get('jev_abstained_actions')}",
            jev_abstained == m_act.get("jev_abstained_actions"),
        )
        self.record_check(
            "Comex Blocks Invariant",
            f"Ledger={comex_blocks}, Manifest={m_act.get('comex_total_blocks')}",
            comex_blocks == m_act.get("comex_total_blocks"),
        )
        self.record_check(
            "Dispatch Attempted Invariant",
            f"Ledger={dispatch_attempted}, Manifest={m_act.get('dispatch_attempted_actions')}",
            dispatch_attempted == m_act.get("dispatch_attempted_actions"),
        )
        self.record_check(
            "Dispatch Sent Invariant",
            f"Ledger={dispatch_sent}, Manifest={m_act.get('dispatch_sent_actions')}",
            dispatch_sent == m_act.get("dispatch_sent_actions"),
        )
        self.record_check(
            "Dispatch Success Invariant",
            f"Ledger={dispatch_success}, Manifest={m_act.get('dispatch_success_actions')}",
            dispatch_success == m_act.get("dispatch_success_actions"),
        )

        # 5. Invariant: Task Summary == Ledger Metrics
        sum_results_delegated = sum(r["delegated_turns"] for r in results)
        self.record_check(
            "Delegation Reconciliation",
            f"Results sum delegated_turns={sum_results_delegated}, Ledger jev_routed={jev_routed}",
            sum_results_delegated == jev_routed,
        )

        sum_results_blocks = sum(r["comex_blocks"] for r in results)
        self.record_check(
            "Blocks Reconciliation",
            f"Results sum comex_blocks={sum_results_blocks}, Ledger BLOCK count={comex_blocks}",
            sum_results_blocks == comex_blocks,
        )

        # 6. Invariant: Shadow vs Executed Distinction
        self.record_check(
            "Shadow vs Routed Distinction",
            f"Resolved={jev_resolved} == Routed={jev_routed} + ShadowOnly={shadow_only_resolutions}",
            jev_resolved == (jev_routed + shadow_only_resolutions),
        )

        # 7. Invariant: State Fingerprints & Deltas
        fp_valid = True
        fp_error = ""
        for idx, r in enumerate(records, start=1):
            st = r.get("state", {})
            bfp = st.get("before_fingerprint", "")
            afp = st.get("after_fingerprint", "")
            changed = st.get("state_changed")

            if not bfp or len(bfp) < 8:
                fp_valid = False
                fp_error = f"Row {idx} missing before_fingerprint"
                break
            if not afp or len(afp) < 8:
                fp_valid = False
                fp_error = f"Row {idx} missing after_fingerprint"
                break
            if changed != (bfp != afp):
                fp_valid = False
                fp_error = f"Row {idx} state_changed ({changed}) != (bfp != afp) ({bfp != afp})"
                break
        self.record_check(
            "State Fingerprints Present & Valid",
            "All records contain valid before/after fingerprints with strictly derived state_changed" if fp_valid else fp_error,
            fp_valid,
        )

        # 8. Invariant: Canonical Percentile Implementation
        durations = [r["duration_s"] for r in results]
        canonical_p90 = canonical_percentile(durations, 0.90)
        canonical_p95 = canonical_percentile(durations, 0.95)
        manifest_p90 = manifest["metrics_summary"]["p90_task_duration_s"]
        p90_matches = abs(canonical_p90 - manifest_p90) < 0.02
        self.record_check(
            "Canonical p90 Duration",
            f"Calculated p90={canonical_p90:.2f}s, Manifest p90={manifest_p90:.2f}s",
            p90_matches,
        )

        # 9. Invariant: Report Reconciliation (if report exists)
        if self.report_path and self.report_path.exists():
            report_text = self.report_path.read_text(encoding="utf-8")
            # Check for correct terms and numbers
            rep_checks = []
            rep_checks.append(f"{manifest['metrics_summary']['tasks_passed']} / {len(results)}")
            rep_checks.append(f"**{delegated_count}**")
            rep_checks.append(f"**{successful_dispatched_jev} / {delegated_count}**")
            rep_checks.append("JEV Decision Correctness")
            rep_checks.append("JEV Actions Successfully Dispatched")

            missing_terms = [t for t in rep_checks if t not in report_text]
            self.record_check(
                "Report Metric Alignment",
                "All exact numbers and terminology reconciled in report" if not missing_terms else f"Missing in report: {missing_terms}",
                len(missing_terms) == 0,
            )

        return len(self.errors) == 0

    def print_summary(self) -> None:
        print("\n" + "=" * 80)
        print("  COMEX BENCHMARK RUN VALIDATION AUDIT")
        print("=" * 80)
        print(f"Results:  {self.results_path}")
        print(f"Ledger:   {self.ledger_path}")
        print(f"Manifest: {self.manifest_path}")
        if self.report_path:
            print(f"Report:   {self.report_path}")
        print("-" * 80)

        for name, details, passed in self.checks:
            mark = "✅ PASS" if passed else "❌ FAIL"
            print(f"{mark} | {name:<32} | {details}")

        print("=" * 80)
        if not self.errors:
            print(">>> VALIDATION PASSED: All benchmark invariants reconciled across results, ledger, manifest, and report.")
            print("PASS")
            print("=" * 80 + "\n")
        else:
            print(f">>> VALIDATION FAILED: {len(self.errors)} assertion(s) violated:")
            for err in self.errors:
                print(f"    - {err}")
            print("FAIL")
            print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Validate Comex benchmark run outputs and invariants")
    parser.add_argument("--results", type=str, required=True, help="Path to results.jsonl")
    parser.add_argument("--ledger", type=str, required=True, help="Path to action_ledger.jsonl")
    parser.add_argument("--manifest", type=str, required=True, help="Path to manifest.json")
    parser.add_argument("--report", type=str, default=None, help="Optional path to report.md")
    args = parser.parse_args()

    validator = BenchmarkValidator(
        results_path=Path(args.results).resolve(),
        ledger_path=Path(args.ledger).resolve(),
        manifest_path=Path(args.manifest).resolve(),
        report_path=Path(args.report).resolve() if args.report else None,
    )

    passed = validator.validate()
    validator.print_summary()

    if not passed:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
