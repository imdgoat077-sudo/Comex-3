"""
tests/test_ab_t14_treatment.py
==============================
Unit and Regression Tests for Step 5: T14 DOM Tagging Treatment & Paired A/B Harness.

Validates all 14 requirements from Section A20:
1. Baseline T14 selector includes .email-send.
2. Treatment T14 selector includes .email-forward.
3. Treatment T14 selector includes .email-reply.
4. Treatment T14 selector excludes .email-send.
5. T15 selector behavior is identical in both conditions.
6. T01–T13 selectors are identical in both conditions.
7. Default runner behavior is baseline.
8. Manifest condition and hashes are correct.
9. Invalid condition combinations are rejected.
10. Treatment does not bypass Comex.
11. Treatment does not bypass ActionLedger.
12. Pair ordering is deterministic and balanced.
13. Exactly 10 A trials and 10 B trials are scheduled.
14. Each pair contains exactly one A and one B.
"""

from __future__ import annotations

import hashlib
import json
import pytest
from pathlib import Path

from benchmarks.run_browsergym_electron import (
    BASELINE_INTERACTIVE_SELECTOR,
    TREATMENT_T14_INTERACTIVE_SELECTOR,
    BASELINE_SELECTOR_HASH,
    TREATMENT_T14_SELECTOR_HASH,
    ElectronBenchmarkRunner,
)
from benchmarks.run_replicated_benchmark import ReplicatedBenchmarkRunner
from benchmarks.run_paired_ab_t14 import PAIRED_SCHEDULE, PairedABExperimentRunner


class TestT14SelectorsContract:
    def test_01_baseline_t14_includes_email_send(self):
        runner = ElectronBenchmarkRunner(treatment_t14=False)
        selector = runner.get_effective_selector("T14")
        assert ".email-send" in selector

    def test_02_treatment_t14_includes_email_forward(self):
        runner = ElectronBenchmarkRunner(treatment_t14=True)
        selector = runner.get_effective_selector("T14")
        assert ".email-forward" in selector

    def test_03_treatment_t14_includes_email_reply(self):
        runner = ElectronBenchmarkRunner(treatment_t14=True)
        selector = runner.get_effective_selector("T14")
        assert ".email-reply" in selector

    def test_04_treatment_t14_excludes_email_send(self):
        runner = ElectronBenchmarkRunner(treatment_t14=True)
        selector = runner.get_effective_selector("T14")
        assert ".email-send" not in selector

    def test_05_t15_selector_identical_in_both_conditions(self):
        runner_base = ElectronBenchmarkRunner(treatment_t14=False)
        runner_treat = ElectronBenchmarkRunner(treatment_t14=True)
        sel_base = runner_base.get_effective_selector("T15")
        sel_treat = runner_treat.get_effective_selector("T15")
        assert sel_base == sel_treat
        assert sel_base == BASELINE_INTERACTIVE_SELECTOR
        assert ".email-send" in sel_treat
        assert ".email-forward" not in sel_treat

    def test_06_t01_to_t13_selectors_identical_in_both_conditions(self):
        runner_base = ElectronBenchmarkRunner(treatment_t14=False)
        runner_treat = ElectronBenchmarkRunner(treatment_t14=True)
        for i in range(1, 14):
            tid = f"T{i:02d}"
            sel_base = runner_base.get_effective_selector(tid)
            sel_treat = runner_treat.get_effective_selector(tid)
            assert sel_base == sel_treat, f"Selector mismatch for {tid}"
            assert sel_base == BASELINE_INTERACTIVE_SELECTOR


class TestRunnerConfigurationAndManifest:
    def test_07_default_runner_behavior_is_baseline(self):
        runner = ElectronBenchmarkRunner()
        assert runner.treatment_t14 is False
        assert runner.condition == "A_BASELINE"
        assert runner.get_effective_selector("T14") == BASELINE_INTERACTIVE_SELECTOR

        repl_runner = ReplicatedBenchmarkRunner()
        assert repl_runner.treatment_t14 is False
        assert repl_runner.condition == "A_BASELINE"

    def test_08_manifest_condition_and_hashes_correct(self):
        repl_base = ReplicatedBenchmarkRunner(treatment_t14=False, condition="A_BASELINE", tasks=["T14"])
        man_base = repl_base.generate_manifest(
            start_iso="2026-09-20T00:00:00Z",
            end_iso="2026-09-20T00:00:10Z",
            elapsed_s=10.0,
            target_tasks=[{"id": "T14"}],
        )
        cfg_base = man_base["configuration"]
        assert cfg_base["condition"] == "A_BASELINE"
        assert cfg_base["treatment_t14"] is False
        assert cfg_base["effective_interactive_selector"] == BASELINE_INTERACTIVE_SELECTOR
        assert cfg_base["baseline_selector_hash"] == BASELINE_SELECTOR_HASH

        repl_treat = ReplicatedBenchmarkRunner(treatment_t14=True, condition="B_TREATMENT", tasks=["T14"])
        man_treat = repl_treat.generate_manifest(
            start_iso="2026-09-20T00:00:00Z",
            end_iso="2026-09-20T00:00:10Z",
            elapsed_s=10.0,
            target_tasks=[{"id": "T14"}],
        )
        cfg_treat = man_treat["configuration"]
        assert cfg_treat["condition"] == "B_TREATMENT"
        assert cfg_treat["treatment_t14"] is True
        assert cfg_treat["effective_interactive_selector"] == TREATMENT_T14_INTERACTIVE_SELECTOR
        assert cfg_treat["treatment_selector_hash"] == TREATMENT_T14_SELECTOR_HASH

    def test_09_invalid_condition_combinations_rejected(self):
        with pytest.raises(ValueError, match="cannot be paired"):
            ElectronBenchmarkRunner(treatment_t14=True, condition="A_BASELINE")

        with pytest.raises(ValueError, match="requires treatment_t14=True"):
            ElectronBenchmarkRunner(treatment_t14=False, condition="B_TREATMENT")

        with pytest.raises(ValueError, match="cannot be paired"):
            ReplicatedBenchmarkRunner(treatment_t14=True, condition="A_BASELINE")

        with pytest.raises(ValueError, match="requires treatment_t14=True"):
            ReplicatedBenchmarkRunner(treatment_t14=False, condition="B_TREATMENT")


class TestSafetyAndLedgerInvariants:
    def test_10_treatment_does_not_bypass_comex(self):
        runner = ElectronBenchmarkRunner(treatment_t14=True)
        # Verify Comex actuator, safety gates, and loop breaker are active
        assert runner.actuator is not None
        assert runner.loop_breaker is not None
        assert hasattr(runner.actuator, "execute_click")
        assert hasattr(runner.actuator, "execute_atomic_fill")

    def test_11_treatment_does_not_bypass_action_ledger(self):
        runner = ElectronBenchmarkRunner(treatment_t14=True)
        assert runner.action_ledger is not None
        assert len(runner.action_ledger.records) == 0


class TestPairedScheduleInvariants:
    def test_12_pair_ordering_is_deterministic_and_balanced(self):
        runner = PairedABExperimentRunner(pairs=10)
        sched = runner.get_schedule()
        assert len(sched) == 10

        # Verify balanced alternating order
        for idx, pair in enumerate(sched):
            p_id = pair["pair_id"]
            assert p_id == idx + 1
            order = pair["order"]
            if idx % 2 == 0:
                # Odd pairs (1, 3, 5, 7, 9) are A -> B
                assert order[0][0] == "A_BASELINE"
                assert order[0][1] is False
                assert order[1][0] == "B_TREATMENT"
                assert order[1][1] is True
            else:
                # Even pairs (2, 4, 6, 8, 10) are B -> A
                assert order[0][0] == "B_TREATMENT"
                assert order[0][1] is True
                assert order[1][0] == "A_BASELINE"
                assert order[1][1] is False

    def test_13_exactly_10_a_and_10_b_trials_scheduled(self):
        runner = PairedABExperimentRunner(pairs=10)
        sched = runner.get_schedule()
        all_trials = [cond for p in sched for cond, _ in p["order"]]
        assert len(all_trials) == 20
        assert all_trials.count("A_BASELINE") == 10
        assert all_trials.count("B_TREATMENT") == 10

    def test_14_each_pair_contains_exactly_one_a_and_one_b(self):
        runner = PairedABExperimentRunner(pairs=10)
        sched = runner.get_schedule()
        for p in sched:
            conditions = [cond for cond, _ in p["order"]]
            assert len(conditions) == 2
            assert "A_BASELINE" in conditions
            assert "B_TREATMENT" in conditions


class TestCandidateIdentityAndPostconditionContract:
    def test_15_candidate_identity_metadata_fields(self):
        from benchmarks.action_ledger import ActionRecord, JevDecisionMetadata
        jev_meta = JevDecisionMetadata(
            attempted=True,
            status="RESOLVED",
            target_id="2",
            target_selector=".email-forward",
            target_class="email-forward",
            target_text="Forward",
        )
        rec = ActionRecord(
            run_id="test-run",
            arm="C",
            task_id="T14",
            task_name="email-inbox",
            turn_id=2,
            action_id=1,
            operation="CLICK",
            target_selector=".email-forward",
            target_class="email-forward",
            target_text="Forward",
            jev=jev_meta,
        )
        assert rec.target_class == "email-forward"
        assert rec.target_selector == ".email-forward"
        assert rec.target_text == "Forward"
        assert rec.jev.target_class == "email-forward"

        # Roundtrip JSON serialization check
        d = rec.to_dict()
        assert d["target_class"] == "email-forward"
        assert d["jev"]["target_class"] == "email-forward"
        rec2 = ActionRecord.from_dict(d)
        assert rec2.target_class == "email-forward"
        assert rec2.jev.target_class == "email-forward"

    def test_16_t14_forward_postcondition_fields(self):
        from benchmarks.action_ledger import StateDeltaMetadata
        # Causal transition: not pre and post -> True
        state_causal = StateDeltaMetadata(
            before_fingerprint="fp1",
            after_fingerprint="fp2",
            state_changed=True,
            forward_action_observed=True,
            forward_state_pre=False,
            forward_state_post=True,
            forward_state_postcondition=True,
        )
        assert state_causal.state_changed is True
        assert state_causal.forward_action_observed is True
        assert state_causal.forward_state_pre is False
        assert state_causal.forward_state_post is True
        assert state_causal.forward_state_postcondition is True

        # Pre-existing state: pre is True -> postcondition transition is False
        state_preexisting = StateDeltaMetadata(
            before_fingerprint="fp1",
            after_fingerprint="fp2",
            state_changed=True,
            forward_action_observed=False,
            forward_state_pre=True,
            forward_state_post=True,
            forward_state_postcondition=False,
        )
        assert state_preexisting.forward_state_pre is True
        assert state_preexisting.forward_state_postcondition is False

    def test_17_paired_discordant_concordant_analysis_contract(self):
        from benchmarks.compare_ab_t14 import generate_ab_report
        # Synthetic paired data with 2 pairs: Pair 1 cured (A=FAIL, B=PASS), Pair 2 concordant fail
        data_a = {
            "dir": "mock_a",
            "manifest": {
                "configuration": {
                    "condition": "A_BASELINE",
                    "treatment_t14": False,
                    "effective_interactive_selector": "sel_a",
                    "baseline_selector_hash": "hash_a",
                    "model_system1": "jev",
                    "model_system2": "gemini",
                },
                "environment": {"git_commit": "abc1234567", "electron_version": "v1", "node_version": "v2"},
            },
            "results": [
                {"pair_id": 1, "success": False, "duration_s": 25.0},
                {"pair_id": 2, "success": False, "duration_s": 30.0},
            ],
            "records": [],
        }
        data_b = {
            "dir": "mock_b",
            "manifest": {
                "configuration": {
                    "condition": "B_TREATMENT",
                    "treatment_t14": True,
                    "effective_interactive_selector": "sel_b",
                    "treatment_selector_hash": "hash_b",
                    "model_system1": "jev",
                    "model_system2": "gemini",
                },
                "environment": {"git_commit": "abc1234567", "electron_version": "v1", "node_version": "v2"},
            },
            "results": [
                {"pair_id": 1, "success": True, "duration_s": 8.0},
                {"pair_id": 2, "success": False, "duration_s": 28.0},
            ],
            "records": [],
        }
        report = generate_ab_report(data_a, data_b)
        assert "$A=\\text{FAIL}, B=\\text{PASS}$" in report
        assert "**1**" in report  # 1 cured failure
        assert "Treatment Cured Failure" in report
        assert "-17.00s" in report  # 8.0 - 25.0 = -17.0s delta

