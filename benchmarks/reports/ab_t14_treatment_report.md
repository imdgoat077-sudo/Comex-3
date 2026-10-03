# T14 DOM Tagging Treatment: Paired A/B Experimental Report

> **Study Design:** Controlled paired A/B experiment across Task T14 (`email-inbox`) with 10 balanced paired replications (20 total task executions, alternating within-pair presentation).
> **Intervention Contrast:** Condition A (Baseline `.email-send` container) vs Condition B (Treatment `.email-forward, .email-reply` child spans).
> **Evaluated Pairs:** 10 matched pairs (20 total task executions).

## 1. Experimental Provenance & Environment

| Parameter | Condition A (Baseline) | Condition B (Treatment) |
|---|---|---|
| **Run Directory** | `benchmarks/runs/ab_t14_baseline` | `benchmarks/runs/ab_t14_treatment` |
| **Condition Label** | `A_BASELINE` | `B_TREATMENT` |
| **treatment_t14 Flag** | `False` | `True` |
| **Git Commit** | `ea825e457d` | `ea825e457d` |
| **Electron / Node** | v43.4.0 / v24.12.0 | v43.4.0 / v24.12.0 |
| **System 2 Model** | `ag/gemini-3.7-flash-medium` | `ag/gemini-3.7-flash-medium` |
| **System 1 Model** | `ag/jev-system1` | `ag/jev-system1` |
| **CLICK Threshold / Margin** | 0.70 / 0.05 (Frozen) | 0.70 / 0.05 (Frozen) |
| **Effective Selector** | `button, input, select, textarea, a, label, h3, [role="button"], [role="checkbox"], .radio, .email-thread, .email-send, [class*="ui-button"], [class*="close"], [class*="titlebar-close"], [class*="search-title"], .star` | `button, input, select, textarea, a, label, h3, [role="button"], [role="checkbox"], .radio, .email-thread, .email-forward, .email-reply, [class*="ui-button"], [class*="close"], [class*="titlebar-close"], [class*="search-title"], .star` |
| **Selector SHA256** | `5fe437dd750a188a...` | `23fea0af71199d7b...` |

## 2. Primary Outcome: Paired Success & Discordance Analysis

| Pair Outcome Category | Count | Percentage | Scientific Meaning |
|---|---|---|---|
| **$A=\text{FAIL}, B=\text{PASS}$** | **1** | **10.0%** | **Treatment Cured Failure** (Key Causal Effect) |
| **$A=\text{PASS}, B=\text{FAIL}$** | **3** | **30.0%** | **Treatment Introduced Failure** (Regression) |
| **$A=\text{PASS}, B=\text{PASS}$** | 0 | 0.0% | Concordant Success |
| **$A=\text{FAIL}, B=\text{FAIL}$** | 6 | 60.0% | Concordant Failure |

### Descriptive Task-Level Proportions
| Condition | Repetitions | Success Count | Success Rate | Wilson 95% CI |
|---|---|---|---|---|
| **Condition A (Baseline)** | 10 | 3 | 30.0% | [10.8%, 60.3%] |
| **Condition B (Treatment)** | 10 | 1 | 10.0% | [1.8%, 40.4%] |

## 3. Mechanistic Causal Chain Diagnostics (Turn 2: Forward Region)

| Diagnostic Metric | Condition A (Baseline) | Condition B (Treatment) | Interpretation |
|---|---|---|---|
| **Turn-2 Actions Evaluated** | 6 | 8 | Forward grounding step |
| **Turn-2 Executed Candidate Classes** | `email-send, star` | `email-forward, email-reply` | Element class received by actuator on Turn 2 |
| **Turn-2 Jev Resolved Target Classes** | `abstained/none` | `email-forward, email-reply` | Candidate class selected by System 1 on Turn 2 |
| **Turn-2 Jev Attempted** | 6 | 8 | System 1 invocation on Turn 2 |
| **Turn-2 Forward-Region Jev Resolved** | 0 (0.0%) | 8 (100.0%) | Child vs container distinction on Turn 2 |
| **Turn-2 Jev Abstained** | 6 | 0 | Uncertainty on composite text on Turn 2 |
| **Turn-2 Mean Confidence** | 0.87 | 0.89 | Grounding certainty |
| **Turn-2 Mean Margin** | 0.84 | 0.84 | Separation from 2nd candidate |
| **Turn-2 Comex Authorization (ALLOW)** | 6 | 8 | Safety & Method E gates |
| **Turn-2 Physical Dispatches Sent** | 6 | 8 | Sent via CDP |
| **Turn-2 Forward Action Observed (`.email-forward`)** | **0 / 6** (0.0%) | **3 / 8** (37.5%) | Direct click on child forward control |
| **Turn-2 Pre-Dispatch `#forward` Present** | 0 / 6 | 0 / 8 | Pre-action state |
| **Turn-2 Post-Dispatch `#forward` Present** | 0 / 6 | 3 / 8 | Post-action state |
| **Turn-2 Causal Transition (`not pre and post`)** | **0 / 6** (0.0%) | **3 / 8** (37.5%) | **True Causal State Transition**: Action caused Forward view |
| **Turn-2 General State Transition (`state_changed: true`)** | 0 / 6 (0.0%) | 8 / 8 (100.0%) | DOM fingerprint mutation |

## 4. Recovery & LoopBreaker Diagnostics (Turn 3)

| Metric | Condition A (Baseline) | Condition B (Treatment) |
|---|---|---|
| **Turn-3 Actions Evaluated** | 5 | 8 |
| **Comex `REPEATED_ACTION_BLOCK` Interventions** | **2** | **0** |
| **Total Interventions** | 2 | 0 |

## 5. Task Duration & Paired Latency Analysis

| Condition | Mean Duration | Median Duration | Canonical p90 |
|---|---|---|---|
| **Condition A (Baseline)** | 22.18s | 16.19s | 38.55s |
| **Condition B (Treatment)** | 26.65s | 29.00s | 34.85s |
| **Paired Delta ($B - A$)** | **+4.47s** | **+12.81s** | — |

### Paired Trial Ledger
| Pair ID | Baseline Duration ($A$) | Treatment Duration ($B$) | Paired $\Delta (B - A)$ | Baseline Outcome | Treatment Outcome |
|---|---|---|---|---|---|
| Pair 1 | 40.84s | 7.39s | -33.45s | FAIL | PASS |
| Pair 2 | 15.12s | 34.69s | +19.57s | FAIL | FAIL |
| Pair 3 | 15.54s | 28.38s | +12.84s | FAIL | FAIL |
| Pair 4 | 38.29s | 26.90s | -11.39s | FAIL | FAIL |
| Pair 5 | 36.70s | 36.34s | -0.36s | FAIL | FAIL |
| Pair 6 | 7.62s | 23.35s | +15.73s | PASS | FAIL |
| Pair 7 | 31.49s | 15.95s | -15.54s | FAIL | FAIL |
| Pair 8 | 13.15s | 30.47s | +17.32s | PASS | FAIL |
| Pair 9 | 6.18s | 33.38s | +27.20s | PASS | FAIL |
| Pair 10 | 16.84s | 29.63s | +12.79s | FAIL | FAIL |


## 6. Empirical Scientific Conclusions

1. **Hypothesis Not Supported**: Observed data does not demonstrate causal superiority of the treatment.