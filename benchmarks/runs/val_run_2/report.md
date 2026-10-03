# Phase 2 Controlled Benchmark Report (1× Replications)

> **Experimental Arm:** Arm C (Jev CLICK/FILL Fast-Path Delegation)
> **Scope:** 1 independent repetitions across 15 benchmark tasks (15 total task executions).
> **Invariants Enforced:** Pure measurement only; zero threshold adjustments; zero DOM tagging changes; SELECT kept shadow-only.

## 1. Executive Statistical Summary

| Metric | Value | Statistical Grounding / 95% Confidence Interval |
|---|---|---|
| **Total Task Executions** | 15 | 1 repetitions across 15 tasks |
| **Overall Task Success** | 13 / 15 (86.7%) | Across all task categories |
| **Total Delegated Decisions ($N$)** | **9** | All evaluated JEV actions |
| **Wrong-Target Executions** | **0** | 0 misclicks / wrong bindings observed |
| **Pure Deterministic Oracle Precision** | **6 / 6** (100.0%) | Wilson 95% CI: **[61.0%, 100.0%]** |
| **Gemini Baseline Agreement** | **3 / 3** (100.0%) | Comparison metric only |
| **JEV Decision Correctness** | **9 / 9** (100.0%) | Wilson 95% CI: **[70.1%, 100.0%]** |
| **JEV Actions Successfully Dispatched** | **7 / 9** (77.8%) | Physical actions sent and successfully executed via CDP; 2 / 9 (22.2%) blocked prior to dispatch by Comex safety/loop-breaker checks |
| **Estimated Model Latency Avoided** | **19.31s** | $\sum \max(0, \text{Gemini} - \text{Jev})$ counterfactual |
| **Mean Task Duration** | **11.53s** | Median: **7.72s**, p90: **25.88s**, p95: **27.64s** |

## 2. Four Distinct Coverage Metrics (Replicated Ledger)

```text
1. Turn Delegation Rate        = 9 / 23 = 39.13%
2. Turn Disposition Coverage   = 21 / 23 = 91.3%
3. Action Execution Coverage   = 9 / 23 = 39.13%
4. Action-Level Jev Coverage   = 11 / 41 = 26.83%
```

> **Note on Jev Resolved (11) vs. Jev Routed (9)**: Across the 41 actions, Jev resolved 11 candidate micro-tasks. Exactly 9 were routed for execution under the delegation pilot; 2 were recorded in shadow mode (1 × disallowed operation policy (SELECT_OPTION is shadow-only); 1 × pre-execution loop prevention (blocked REPEATED_ACTION_BLOCK)), maintaining the strict execution boundary.

## 3. Paired Task Performance Across Repetitions

| Task ID | Task Name | Repetitions | Success Count | Success Rate | Mean Duration | Median Duration | p90 Duration | Delegated Actions |
|---|---|---|---|---|---|---|---|---|
| **T01** | `click-test` | 1 | 1 | 100.0% | 7.63s | 7.63s | 7.63s | 1 |
| **T02** | `click-button` | 1 | 1 | 100.0% | 7.72s | 7.72s | 7.72s | 1 |
| **T03** | `click-button-sequence` | 1 | 1 | 100.0% | 11.01s | 11.01s | 11.01s | 1 |
| **T04** | `enter-text` | 1 | 1 | 100.0% | 8.62s | 8.62s | 8.62s | 0 |
| **T05** | `enter-text-dynamic` | 1 | 1 | 100.0% | 6.59s | 6.59s | 6.59s | 0 |
| **T06** | `focus-text` | 1 | 1 | 100.0% | 6.18s | 6.18s | 6.18s | 0 |
| **T07** | `click-checkboxes` | 1 | 1 | 100.0% | 7.02s | 7.02s | 7.02s | 0 |
| **T08** | `choose-list` | 1 | 1 | 100.0% | 8.07s | 8.07s | 8.07s | 0 |
| **T09** | `click-dialog` | 1 | 1 | 100.0% | 6.20s | 6.20s | 6.20s | 1 |
| **T10** | `login-user` | 1 | 1 | 100.0% | 7.23s | 7.23s | 7.23s | 1 |
| **T11** | `search-engine` | 1 | 1 | 100.0% | 26.64s | 26.64s | 26.64s | 0 |
| **T12** | `click-collapsible` | 1 | 1 | 100.0% | 7.84s | 7.84s | 7.84s | 1 |
| **T13** | `click-option` | 1 | 1 | 100.0% | 7.55s | 7.55s | 7.55s | 0 |
| **T14** | `email-inbox` | 1 | 0 | 0.0% | 29.97s | 29.97s | 29.97s | 1 |
| **T15** | `book-flight` | 1 | 0 | 0.0% | 24.73s | 24.73s | 24.73s | 2 |


## 4. Comex Block Taxonomy Breakdown

- **Total Interventions**: 7
  - **Physical Safety Blocks**: 3
  - **Recovery & Loop-Prevention Blocks**: 4
  - **Category Breakdown**:
    - `REPEATED_ACTION_BLOCK`: 4
    - `TARGET_DISABLED`: 3


## 5. Empirical Scientific Conclusions

1. **Controlled Pilot Evidence**: Zero wrong-target executions observed across delegated decisions.
2. **Statistical Grounding**: Invariants strictly verified between action ledger, task results, manifest, and report.
3. **Disentangled Latency Profile**: Canonical median reflects rapid happy-path execution, disentangled from multi-turn recovery loops.
4. **Failure Distribution Stability**: Failures isolated to DOM granularity (T14) and widget semantics (T15), with zero Jev grounding errors.