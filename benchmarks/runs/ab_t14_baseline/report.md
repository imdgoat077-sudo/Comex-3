# T14 A/B Experiment Run Report: Condition A_BASELINE

> **Condition:** `A_BASELINE` (treatment_t14=False)
> **Repetitions:** 10 episodes across 10 balanced pairs.
> **Selector:** `button, input, select, textarea, a, label, h3, [role="button"], [role="checkbox"], .radio, .email-thread, .email-send, [class*="ui-button"], [class*="close"], [class*="titlebar-close"], [class*="search-title"], .star`

## 1. Executive Statistical Summary

| Metric | Value | Statistical Grounding / 95% Confidence Interval |
|---|---|---|
| **Total Task Executions** | 10 | 10 independent repetitions |
| **Overall Task Success** | 3 / 10 (30.0%) | Evaluated via MiniWoB Referee |
| **Total Delegated Decisions ($N$)** | **7** | All evaluated JEV actions |
| **Wrong-Target Executions** | **0** | 0 misclicks observed |
| **JEV Decision Correctness** | **7 / 7** (100.0%) | Wilson 95% CI: **[64.6%, 100.0%]** |
| **JEV Actions Successfully Dispatched** | **7 / 7** (100.0%) | Physical actions sent and successfully executed via CDP; 0 / 7 (0.0%) blocked prior to dispatch by Comex safety/loop-breaker checks |
| **Estimated Model Latency Avoided** | **18.27s** | $\sum \max(0, \text{Gemini} - \text{Jev})$ counterfactual |
| **Mean Task Duration** | **22.18s** | Median: **16.19s**, p90: **38.55s**, p95: **39.69s** |

## 2. Four Distinct Coverage Metrics (Replicated Ledger)

```text
1. Turn Delegation Rate        = 1 / 4 = 25.0%
2. Turn Disposition Coverage   = 4 / 4 = 100.0%
3. Action Execution Coverage   = 7 / 23 = 30.43%
4. Action-Level Jev Coverage   = 7 / 23 = 30.43%
```

## 3. Individual Episode Performance

| Rep # | Pair ID | Within-Pair Order | Success | Duration | Turns | Jev Delegated | Comex Blocks |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | FAIL | 40.84s | 4 | 1 | 1 |
| 2 | 2 | 2 | FAIL | 15.12s | 1 | 1 | 0 |
| 3 | 3 | 1 | FAIL | 15.54s | 3 | 1 | 1 |
| 4 | 4 | 2 | FAIL | 38.29s | 4 | 1 | 1 |
| 5 | 5 | 1 | FAIL | 36.70s | 3 | 1 | 0 |
| 6 | 6 | 2 | PASS | 7.62s | 1 | 0 | 0 |
| 7 | 7 | 1 | FAIL | 31.49s | 2 | 1 | 0 |
| 8 | 8 | 2 | PASS | 13.15s | 1 | 0 | 0 |
| 9 | 9 | 1 | PASS | 6.18s | 1 | 0 | 0 |
| 10 | 10 | 2 | FAIL | 16.84s | 3 | 1 | 1 |


## 4. Comex Block Taxonomy Breakdown

- **Total Interventions**: 4
  - **Physical Safety Blocks**: 0
  - **Recovery & Loop-Prevention Blocks**: 4
  - **Category Breakdown**:
    - `REPEATED_ACTION_BLOCK`: 4

