# T14 A/B Experiment Run Report: Condition B_TREATMENT

> **Condition:** `B_TREATMENT` (treatment_t14=True)
> **Repetitions:** 10 episodes across 10 balanced pairs.
> **Selector:** `button, input, select, textarea, a, label, h3, [role="button"], [role="checkbox"], .radio, .email-thread, .email-forward, .email-reply, [class*="ui-button"], [class*="close"], [class*="titlebar-close"], [class*="search-title"], .star`

## 1. Executive Statistical Summary

| Metric | Value | Statistical Grounding / 95% Confidence Interval |
|---|---|---|
| **Total Task Executions** | 10 | 10 independent repetitions |
| **Overall Task Success** | 1 / 10 (10.0%) | Evaluated via MiniWoB Referee |
| **Total Delegated Decisions ($N$)** | **21** | All evaluated JEV actions |
| **Wrong-Target Executions** | **0** | 0 misclicks observed |
| **JEV Decision Correctness** | **21 / 21** (100.0%) | Wilson 95% CI: **[84.5%, 100.0%]** |
| **JEV Actions Successfully Dispatched** | **21 / 21** (100.0%) | Physical actions sent and successfully executed via CDP; 0 / 21 (0.0%) blocked prior to dispatch by Comex safety/loop-breaker checks |
| **Estimated Model Latency Avoided** | **81.75s** | $\sum \max(0, \text{Gemini} - \text{Jev})$ counterfactual |
| **Mean Task Duration** | **26.65s** | Median: **29.00s**, p90: **34.85s**, p95: **35.60s** |

## 2. Four Distinct Coverage Metrics (Replicated Ledger)

```text
1. Turn Delegation Rate        = 3 / 4 = 75.0%
2. Turn Disposition Coverage   = 4 / 4 = 100.0%
3. Action Execution Coverage   = 21 / 30 = 70.0%
4. Action-Level Jev Coverage   = 21 / 30 = 70.0%
```

## 3. Individual Episode Performance

| Rep # | Pair ID | Within-Pair Order | Success | Duration | Turns | Jev Delegated | Comex Blocks |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 2 | PASS | 7.39s | 1 | 0 | 0 |
| 2 | 2 | 1 | FAIL | 34.69s | 3 | 2 | 0 |
| 3 | 3 | 2 | FAIL | 28.38s | 3 | 2 | 0 |
| 4 | 4 | 1 | FAIL | 26.90s | 3 | 2 | 0 |
| 5 | 5 | 2 | FAIL | 36.34s | 4 | 3 | 0 |
| 6 | 6 | 1 | FAIL | 23.35s | 3 | 2 | 0 |
| 7 | 7 | 2 | FAIL | 15.95s | 1 | 1 | 0 |
| 8 | 8 | 1 | FAIL | 30.47s | 4 | 3 | 0 |
| 9 | 9 | 2 | FAIL | 33.38s | 4 | 3 | 0 |
| 10 | 10 | 1 | FAIL | 29.63s | 4 | 3 | 0 |


## 4. Comex Block Taxonomy Breakdown

- **Total Interventions**: 0
  - **Physical Safety Blocks**: 0
  - **Recovery & Loop-Prevention Blocks**: 0
  - **Category Breakdown**:

