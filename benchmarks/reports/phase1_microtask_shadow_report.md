# Phase 1: Jev Shadow Mode Exact Micro-Task Evaluation Report

> **Protocol:** Gemini selects the operation (CLICK/FILL/SELECT); Jev grounds the target. Zero actuation driven by Jev.

## 1. Executive Answers to the 5 Core Questions

1. **Can Jev reliably ground CLICK/FILL/SELECT?**
   - **Precision@Execute:** **100.0%** (12 / 12 policy-permitted turns were semantically correct)
   - **Coverage:** **50.0%** (12 / 24 micro-task opportunities resolved by policy)
   - **Reference Agreement (conditional on resolve):** **100.0%** (High-confidence $\ge 0.70$: **100.0%**)
   - **Semantic Correctness (conditional on resolve):** **100.0%**
2. **How often does Jev abstain?**
   - **Abstention Rate:** **50.0%** (12 / 24 turns)
   - **Abstention Function:** Acts as an empirical safety boundary against ungrounded or low-information prompts (e.g. `field id 'tt'`, `on`, `button id ''`).
3. **When Jev is confident, how often is it wrong?**
   - **Wrong-Target Rate:** **0.0%** on confident non-abstained turns (0 errors across all resolved turns)
4. **How much latency does Jev add/save?**
   - **Gemini Turn Latency:** **3677.3ms** vs **Jev Latency:** **1058.9ms** (**3.5x faster**)
   - **Latency Saved Per Turn:** **2618.4ms** (Total across run: **31.42s**)
5. **How many Gemini reasoning turns could realistically be removed?**
   - **Avoidable Full Frontier Turns:** **12** / 24 turns (**50.0%**)

## 2. Breakdown by Operation & Delegation Readiness

| Operation | Total Turns | Non-Abstained | Abstention Rate | Reference Agreement | Semantic Correctness | Wrong Target | Delegation Readiness |
|---|---|---|---|---|---|---|---|
| **CLICK** | 15 | 7 | 53.3% | 100.0% | 100.0% | 0.0% | ✅ **APPROVED (PILOT)** |
| **FILL** | 8 | 5 | 37.5% | 100.0% | 100.0% | 0.0% | ✅ **APPROVED (PILOT)** |
| **SELECT** | 1 | 0 | 100.0% | 0.0% | 0.0% | 0.0% | ⏳ **SHADOW ONLY (INSUFFICIENT DATA)** |

## 3. Target Selection Calibration Curve

> Evaluates candidate-selection decisions where Jev resolved an actual DOM candidate ($P(e_i \text{ is intended target})$). Excludes pure no-target abstentions.

| Confidence Bin | Decisions | Resolved | Abstained | Ref Agreement | Sem Correctness | Wrong Target | Task Success |
|---|---|---|---|---|---|---|---|
| `0.50-0.60` | 3 | 0 | 3 | 0.0% | 0.0% | 0.0% | 0.0% |
| `0.60-0.70` | 0 | 0 | 0 | 0.0% | 0.0% | 0.0% | 0.0% |
| `0.70-0.80` | 5 | 5 | 0 | 100.0% | 100.0% | 0.0% | 80.0% |
| `0.80-0.90` | 1 | 1 | 0 | 100.0% | 100.0% | 0.0% | 100.0% |
| `0.90-0.95` | 4 | 4 | 0 | 100.0% | 100.0% | 0.0% | 25.0% |
| `0.95-1.00` | 2 | 2 | 0 | 100.0% | 100.0% | 0.0% | 50.0% |

## 4. Abstention Population Analysis

> Evaluates non-acting turns where Jev yielded control back to Gemini.

| Abstention Reason | Count | Avg Confidence | Min Conf | Max Conf | Behavioral Interpretation |
|---|---|---|---|---|---|
| `LOW_CONFIDENCE` | 8 | 0.4 | 0.18 | 0.59 | Low semantic info in description (e.g. `field id 'tt'`, `on`, single digits); Jev refused to guess. |
| `NO_ACTIONABLE_TARGET` | 4 | 0.64 | 0.15 | 0.97 | Empty button ID (`button id ''`) or missing label; Jev confidently declared no candidate matched. |

## 5. Multi-Turn Decision Details

| Task | Turn | Op | Target Description | Ref BID | Jev BID | Dec Type | Res Status | Conf | Margin | Ref Agree? | Sem Correct? | Abstained? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T01 | 1 | `CLICK` | Click Me! | `1` | `1` | `TARGET` | `RESOLVED` | 0.86 | 0.86 | ✅ YES | ✅ YES | NO |
| T02 | 1 | `CLICK` | Yes | `3` | `3` | `TARGET` | `RESOLVED` | 0.98 | 0.96 | ✅ YES | ✅ YES | NO |
| T03 | 1 | `CLICK` | ONE | `1` | `1` | `TARGET` | `RESOLVED` | 0.92 | 0.90 | ✅ YES | ✅ YES | NO |
| T04 | 1 | `FILL` | field id 'tt' | `1` | `N/A` | `TARGET` | `ABSTAINED` | 0.34 | 0.34 | ❌ NO | ❌ NO | ⚠️ YES (LOW_CONFIDENCE (0.34 < 0.75)) |
| T05 | 1 | `FILL` | field id 'tt' | `1` | `N/A` | `TARGET` | `ABSTAINED` | 0.24 | 0.24 | ❌ NO | ❌ NO | ⚠️ YES (LOW_CONFIDENCE (0.24 < 0.75)) |
| T06 | 1 | `CLICK` | button id 'tt' | `1` | `N/A` | `TARGET` | `ABSTAINED` | 0.18 | 0.18 | ❌ NO | ❌ NO | ⚠️ YES (LOW_CONFIDENCE (0.18 < 0.70)) |
| T07 | 1 | `CLICK` | on | `4` | `N/A` | `NO_ACTIONABLE_TARGET` | `ABSTAINED` | 0.15 | 0.03 | ❌ NO | ❌ NO | ⚠️ YES (NO_ACTIONABLE_TARGET) |
| T08 | 1 | `SELECT` | Briana
Andromache
Jorie
C | `1` | `N/A` | `TARGET` | `ABSTAINED` | 0.42 | 0.42 | ❌ NO | ❌ NO | ⚠️ YES (LOW_CONFIDENCE (0.42 < 0.70)) |
| T09 | 1 | `CLICK` | Close | `1` | `1` | `TARGET` | `RESOLVED` | 0.78 | 0.71 | ✅ YES | ✅ YES | NO |
| T10 | 1 | `FILL` | field id 'username' | `2` | `2` | `TARGET` | `RESOLVED` | 0.76 | 0.68 | ✅ YES | ✅ YES | NO |
| T11 | 1 | `FILL` | field id 'search-text' | `1` | `N/A` | `TARGET` | `ABSTAINED` | 0.52 | 0.52 | ❌ NO | ❌ NO | ⚠️ YES (LOW_CONFIDENCE (0.52 < 0.75)) |
| T11 | 2 | `CLICK` | 2 | `7` | `N/A` | `TARGET` | `ABSTAINED` | 0.59 | 0.45 | ❌ NO | ❌ NO | ⚠️ YES (LOW_CONFIDENCE (0.59 < 0.70)) |
| T11 | 3 | `CLICK` | 2 | `8` | `N/A` | `TARGET` | `ABSTAINED` | 0.54 | 0.40 | ❌ NO | ❌ NO | ⚠️ YES (LOW_CONFIDENCE (0.54 < 0.70)) |
| T11 | 4 | `CLICK` | Nieves | `5` | `5` | `TARGET` | `RESOLVED` | 0.73 | 0.61 | ✅ YES | ✅ YES | NO |
| T12 | 1 | `CLICK` | Section #45 | `1` | `1` | `TARGET` | `RESOLVED` | 0.77 | 0.69 | ✅ YES | ✅ YES | NO |
| T13 | 1 | `CLICK` | on | `2` | `N/A` | `TARGET` | `ABSTAINED` | 0.35 | 0.21 | ❌ NO | ❌ NO | ⚠️ YES (LOW_CONFIDENCE (0.35 < 0.70)) |
| T14 | 1 | `CLICK` | button id '' | `6` | `N/A` | `NO_ACTIONABLE_TARGET` | `ABSTAINED` | 0.73 | 0.52 | ❌ NO | ❌ NO | ⚠️ YES (NO_ACTIONABLE_TARGET) |
| T14 | 2 | `CLICK` | button id '' | `6` | `N/A` | `NO_ACTIONABLE_TARGET` | `ABSTAINED` | 0.70 | 0.46 | ❌ NO | ❌ NO | ⚠️ YES (NO_ACTIONABLE_TARGET) |
| T14 | 3 | `CLICK` | Arda
Non justo.
Faucibus  | `5` | `5` | `TARGET` | `RESOLVED` | 0.99 | 0.98 | ✅ YES | ✅ YES | NO |
| T14 | 4 | `CLICK` | button id '' | `1` | `N/A` | `NO_ACTIONABLE_TARGET` | `ABSTAINED` | 0.97 | 0.97 | ❌ NO | ❌ NO | ⚠️ YES (NO_ACTIONABLE_TARGET) |
| T15 | 1 | `FILL` | From: | `1` | `1` | `TARGET` | `RESOLVED` | 0.75 | 0.62 | ✅ YES | ✅ YES | NO |
| T15 | 2 | `FILL` | field id 'datepicker' | `3` | `3` | `TARGET` | `RESOLVED` | 0.94 | 0.90 | ✅ YES | ✅ YES | NO |
| T15 | 3 | `FILL` | field id 'datepicker' | `3` | `3` | `TARGET` | `RESOLVED` | 0.93 | 0.90 | ✅ YES | ✅ YES | NO |
| T15 | 4 | `FILL` | field id 'datepicker' | `3` | `3` | `TARGET` | `RESOLVED` | 0.91 | 0.86 | ✅ YES | ✅ YES | NO |
