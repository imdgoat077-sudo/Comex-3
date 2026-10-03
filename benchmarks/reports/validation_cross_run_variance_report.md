# Validation Runs Cross-Run Variance & Invariant Reconciliation Report

> **Objective:** Verify experimental control across identical configurations before initiating T14/T15 DOM/widget modifications.

> **Rule Enforced:** All metrics computed via canonical functions (`canonical_percentile`, `statistics.mean`, `statistics.median`, `ActionLedger`). Zero hand-calculated numbers.

## 1. Experimental Configuration Equality Check

| Configuration Property | Run 1 (`val_run_1`) | Run 2 (`val_run_2`) | Controlled Match? |
|---|---|---|---|
| **Git Commit** | `ea825e45` | `ea825e45` | ✅ Identical |
| **Experimental Arm** | Arm C | Arm C | ✅ Identical |
| **Model System 2** | `ag/gemini-3.7-flash-medium` | `ag/gemini-3.7-flash-medium` | ✅ Identical |
| **Model System 1** | `ag/jev-system1` | `ag/jev-system1` | ✅ Identical |
| **Calibration Thresholds** | CLICK: 0.70/0.05, FILL: 0.75/0.10 | CLICK: 0.70/0.05, FILL: 0.75/0.10 | ✅ Identical (Frozen) |
| **Task Order** | 15 tasks (T01 → T15) | 15 tasks (T01 → T15) | ✅ Identical |
| **Browser / Shell** | Electron v43.4.0 | Electron v43.4.0 | ✅ Identical |

## 2. Executive Statistical Variance (Canonical Metrics)

| Metric | Run 1 (`val_run_1`) | Run 2 (`val_run_2`) | Delta ($\Delta$) | Variance Interpretation |
|---|---|---|---|---|
| **Task Success Count** | 13 / 15 (86.7%) | 13 / 15 (86.7%) | $0$ (0.0%) | **Deterministic agreement (T01–T13 pass, T14–T15 fail)** |
| **Delegated JEV Decisions** | 9 | 9 | $0$ | **Identical delegation opportunity count** |
| **JEV Decision Correctness** | 9 / 9 (100.0%) | 9 / 9 (100.0%) | $0$ (0.0%) | **Zero misclicks / wrong bindings observed** |
| **JEV Actions Dispatched** | 7 / 9 (77.8%) | 7 / 9 (77.8%) | $0$ (0.0%) | **7 dispatched via CDP; remaining 2 blocked by Comex on T15** |
| **Comex Total Blocks** | 7 | 7 | $0$ | **Identical safety & loop-prevention counts (3 safety, 4 loop)** |
| **Mean Task Duration** | 18.37s | 11.53s | -6.84s | **Inference latency variance on initial multi-action tasks** |
| **Median Task Duration** | 8.74s | 7.72s | -1.02s | **Stable happy-path execution speed** |
| **p90 Task Duration** | 39.29s | 25.88s | -13.41s | **Canonical percentile $(N-1) 	imes 0.90$ interpolation** |

## 3. Paired Task-by-Task Comparison

| Task ID | Task Name | Run 1 Result | Run 1 Dur | Run 2 Result | Run 2 Dur | Duration Delta | Run 1 Delegated | Run 2 Delegated |
|---|---|---|---|---|---|---|---|---|
| **T01** | `click-test` | PASS | 8.23s | PASS | 7.63s | -0.60s | 1 | 1 |
| **T02** | `click-button` | PASS | 20.67s | PASS | 7.72s | -12.95s | 1 | 1 |
| **T03** | `click-button-sequence` | PASS | 45.54s | PASS | 11.01s | -34.53s | 1 | 1 |
| **T04** | `enter-text` | PASS | 52.49s | PASS | 8.62s | -43.87s | 0 | 0 |
| **T05** | `enter-text-dynamic` | PASS | 7.37s | PASS | 6.59s | -0.78s | 0 | 0 |
| **T06** | `focus-text` | PASS | 6.98s | PASS | 6.18s | -0.80s | 0 | 0 |
| **T07** | `click-checkboxes` | PASS | 8.51s | PASS | 7.02s | -1.49s | 0 | 0 |
| **T08** | `choose-list` | PASS | 7.11s | PASS | 8.07s | +0.96s | 0 | 0 |
| **T09** | `click-dialog` | PASS | 11.87s | PASS | 6.20s | -5.67s | 1 | 1 |
| **T10** | `login-user` | PASS | 7.66s | PASS | 7.23s | -0.43s | 0 | 1 |
| **T11** | `search-engine` | PASS | 27.70s | PASS | 26.64s | -1.06s | 0 | 0 |
| **T12** | `click-collapsible` | PASS | 8.74s | PASS | 7.84s | -0.90s | 1 | 1 |
| **T13** | `click-option` | PASS | 7.15s | PASS | 7.55s | +0.40s | 0 | 0 |
| **T14** | `email-inbox` | FAIL | 25.68s | FAIL | 29.97s | +4.29s | 1 | 1 |
| **T15** | `book-flight` | FAIL | 29.91s | FAIL | 24.73s | -5.18s | 3 | 2 |

## 4. Shadow vs. Routed Accounting Verification

- **Run 1**: Jev Resolved (11) = Routed (9) + Shadow-Only (2)
  - Reason 1: Disallowed operation policy (1 × `SELECT_OPTION` on T08 is shadow-only)
  - Reason 2: Pre-execution loop prevention (1 × repeated `FILL` on T15 Turn 4 blocked by Comex before delegation)
- **Run 2**: Jev Resolved (11) = Routed (9) + Shadow-Only (2)
  - Reason 1: Disallowed operation policy (1 × `SELECT_OPTION` on T08 is shadow-only)
  - Reason 2: Pre-execution loop prevention (1 × repeated `FILL` on T15 Turn 4 blocked by Comex before delegation)