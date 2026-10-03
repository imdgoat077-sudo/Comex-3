# Step 5 Implementation Diff Audit: T14 DOM Tagging Treatment & Paired A/B Harness

> **Audit Date:** 2026-09-20T12:52:30+05:30  
> **Status:** Phase A Hardening Complete — Final Gate Check  
> **Targeted Task:** T14 (`email-inbox`)  
> **Study Design:** 10 balanced paired replications (20 total task executions, alternating within-pair presentation).  
> **Telemetry Schema Classification:** `ADDITIVE_ONLY` (backward-compatible candidate identity & forward state fields).  
> **Telemetry Semantics:** `UNTOUCHED` (ActionRecord contract, fingerprint hash algorithms, validation invariants preserved).  
> **Frozen Boundaries:** T15 strictly untouched; Jev thresholds untouched (0.70/0.05); Gemini prompts untouched; Comex safety logic untouched; Validation logic not weakened.

---

## 1. Exact Files Changed

| File | Change Type | Purpose |
|---|---|---|
| `benchmarks/run_browsergym_electron.py` | MODIFIED | Added baseline & treatment selector constants, hashes, scoped selector resolution (`get_effective_selector`), candidate identity extraction (`className`, `selector`), pre/post live DOM forward state queries (`forward_state_pre`, `forward_state_post`), causal transition calculation (`forward_transition_occurred`), and mutual exclusion validation. |
| `benchmarks/action_ledger.py` | MODIFIED | Added candidate identity fields (`target_selector`, `target_class`, `target_text`) to `ActionRecord` and `JevDecisionMetadata`, and added true pre/post forward state fields (`forward_action_observed`, `forward_state_pre`, `forward_state_post`, `forward_state_postcondition`) to `StateDeltaMetadata`. |
| `benchmarks/run_replicated_benchmark.py` | MODIFIED | Added `--condition` and `--treatment-t14` CLI arguments, manifest additions (provenance, hashes, selector), and mutual exclusion validation. Deferred directory creation to execution start. |
| `benchmarks/run_paired_ab_t14.py` | NEW | 10 balanced paired replications orchestrator (20 total task executions, alternating within-pair presentation: Pair 1: A→B, Pair 2: B→A, ...). |
| `benchmarks/compare_ab_t14.py` | NEW | Automated mechanical comparator extracting Paired Discordance Outcome Table as primary success comparison, candidate element identity, pre/post Forward state diagnostics, Turn-3 loop blocks, and canonical percentile durations. |
| `tests/test_ab_t14_treatment.py` | NEW | 17 regression, invariant, and hardening unit tests validating all requirements of Section A20 and Phase A telemetry hardening. |

No other benchmark files or task definitions were altered. `benchmarks/validate_benchmark_run.py` and `benchmarks/compare_validation_runs.py` are completely unchanged.

---

## 2. Exact Selector Difference

### Baseline Selector (`BASELINE_INTERACTIVE_SELECTOR`)
```css
button, input, select, textarea, a, label, h3, [role="button"], [role="checkbox"], .radio, .email-thread, .email-send, [class*="ui-button"], [class*="close"], [class*="titlebar-close"], [class*="search-title"], .star
```
- **SHA256 Hash**: `5fe437dd750a188a9b64264e377166df27f701f4a92abfa51cf2b932d476745e`
- **Candidate count in T14 Reply/Forward region**: 1 coarse container (`.email-send`).

### Treatment Selector (`TREATMENT_T14_INTERACTIVE_SELECTOR`)
```css
button, input, select, textarea, a, label, h3, [role="button"], [role="checkbox"], .radio, .email-thread, .email-forward, .email-reply, [class*="ui-button"], [class*="close"], [class*="titlebar-close"], [class*="search-title"], .star
```
- **SHA256 Hash**: `23fea0af71199d7b4536f986317d2b7361ab0088343a1238915187af9fed16bd`
- **Candidate count in T14 Reply/Forward region**: 2 fine child spans (`.email-forward`, `.email-reply`).

### Machine-Readable Contrast
- **ADDED SELECTORS**: `['.email-forward', '.email-reply']`
- **REMOVED SELECTORS**: `['.email-send']`
- **UNTOUCHED SELECTORS**: `button`, `input`, `select`, `textarea`, `a`, `label`, `h3`, `[role="button"]`, `[role="checkbox"]`, `.radio`, `.email-thread`, `[class*="ui-button"]`, `[class*="close"]`, `[class*="titlebar-close"]`, `[class*="search-title"]`, `.star`
- **Other Selector Differences**: **NONE** (0 unintended modifications).

---

## 3. Exact Runner / Configuration Difference & Scoping Rule

### Scoping Rule
The treatment selector is strictly scoped to task `T14`:
```python
def get_effective_selector(self, task_id: Optional[str] = None) -> str:
    tid = task_id or self.current_task_id
    if tid == "T14" and self.treatment_t14:
        return TREATMENT_T14_INTERACTIVE_SELECTOR
    return BASELINE_INTERACTIVE_SELECTOR
```
- Tasks `T01` through `T13` and `T15` receive `BASELINE_INTERACTIVE_SELECTOR` regardless of whether `treatment_t14` is True or False.
- Default configuration: `treatment_t14 = False`, `condition = "A_BASELINE"`.

### Condition Validation & Mutual Exclusion
Invalid configurations are explicitly rejected with `ValueError`:
- `condition="A_BASELINE"` + `treatment_t14=True` $\to$ **REJECTED**
- `condition="B_TREATMENT"` + `treatment_t14=False` $\to$ **REJECTED**

---

## 4. Candidate Identity & True Causal Forward Transition Telemetry

### 4.1 Candidate Element Identity
To avoid equating candidate sets by arbitrary BID integers:
- Each candidate element extracts its DOM `className` and CSS `selector`.
- Each action record in `action_ledger.jsonl` explicitly records:
  - `task_id`
  - `turn_id`
  - `target_id`
  - `target_selector`
  - `target_class`
  - `target_text`
- When Jev evaluates candidates, `JevDecisionMetadata` preserves `target_selector`, `target_class`, and `target_text`.

### 4.2 True Pre/Post Causal State Transition
Rather than asserting post-action existence alone, telemetry records both boundary states:
- `forward_state_pre`: Did `#forward` already exist in the DOM before dispatch?
- `forward_state_post`: Did `#forward` exist in the DOM after dispatch?
- `forward_state_postcondition`: Dispatch-boundary causal transition: `(not forward_state_pre) and forward_state_post`.

This verifies that the `#forward` DOM state transitioned across the action dispatch boundary (establishing a dispatch-boundary causal transition rather than assuming post-action existence proves causality or relying on presence alone):
```text
pre-action DOM state: forward_state_pre = false
                    ↓
candidate extraction exposes .email-forward
                    ↓
Jev resolves target: target_class=.email-forward
                    ↓
Comex validates/ALLOW
                    ↓
dispatch_sent = true & dispatch_success = true
                    ↓
forward_action_observed = true
                    ↓
post-action DOM state: forward_state_post = true
                    ↓
causal forward transition: forward_state_postcondition = true
                    ↓
state_changed = true (DOM fingerprint mutated)
                    ↓
task evaluator = PASS
```

---

## 5. Exact Manifest Additions

The benchmark manifest (`manifest.json`) generated by `ReplicatedBenchmarkRunner` and `PairedABExperimentRunner` includes the following provenance fields under `configuration`:
```json
{
  "condition": "A_BASELINE" | "B_TREATMENT",
  "treatment_t14": false | true,
  "effective_interactive_selector": "...",
  "baseline_selector_hash": "5fe437dd750a188a9b64264e377166df27f701f4a92abfa51cf2b932d476745e",
  "treatment_selector_hash": "23fea0af71199d7b4536f986317d2b7361ab0088343a1238915187af9fed16bd",
  "configuration_fingerprint": "<sha256 of condition:treatment:hashes:commit>",
  "task_suite_version": "browsergym-miniwob-1.0"
}
```

---

## 6. Exact Comparator Logic (`benchmarks/compare_ab_t14.py`)

- **Primary Outcome: Paired Discordant / Concordant Table**:
  - $A=\text{FAIL}, B=\text{PASS}$ (**Treatment Cured Failure**)
  - $A=\text{PASS}, B=\text{FAIL}$ (**Treatment Introduced Failure / Regression**)
  - Concordant Both PASS
  - Concordant Both FAIL
- **Descriptive Task Proportions**: Repetition counts, success counts, success rates, and Wilson 95% score confidence intervals directly from `results.jsonl`.
- **Mechanistic Causal Chain (Turn 2)**: Reconciles action records for Turn 2 where the forward click occurs:
  - Executed candidate classes (`target_class`)
  - Jev resolved target classes (`jev.target_class`)
  - Jev attempted, resolved, abstained rates
  - Mean confidence and mean margin
  - Comex authorization (ALLOW vs BLOCK)
  - Dispatches sent and dispatch successes
  - **Forward Action Observed (`forward_action_observed: true`)**
  - **Pre-Dispatch State (`forward_state_pre`)**
  - **Post-Dispatch State (`forward_state_post`)**
  - **Causal Forward Transition (`forward_state_postcondition`: not pre and post)**
  - **General State Transition (`state_changed: true`)**
- **Recovery Analysis (Turn 3)**: Measures `REPEATED_ACTION_BLOCK` triggers from `ActionLoopBreaker`.
- **Durations**: Computes mean, median, canonical 90th percentile (`canonical_percentile(durs, 0.90)`), and per-pair deltas ($\Delta_i = B_i - A_i$).
- **Zero Manual Math**: Derived entirely via code; zero hardcoded expected values.

---

## 7. Exact Tests Added (`tests/test_ab_t14_treatment.py`)

All requirements from Section A20 and Phase A hardening refinements are implemented and validated:
1. `test_01_baseline_t14_includes_email_send`: PASS
2. `test_02_treatment_t14_includes_email_forward`: PASS
3. `test_03_treatment_t14_includes_email_reply`: PASS
4. `test_04_treatment_t14_excludes_email_send`: PASS
5. `test_05_t15_selector_identical_in_both_conditions`: PASS
6. `test_06_t01_to_t13_selectors_identical_in_both_conditions`: PASS
7. `test_07_default_runner_behavior_is_baseline`: PASS
8. `test_08_manifest_condition_and_hashes_correct`: PASS
9. `test_09_invalid_condition_combinations_rejected`: PASS
10. `test_10_treatment_does_not_bypass_comex`: PASS
11. `test_11_treatment_does_not_bypass_action_ledger`: PASS
12. `test_12_pair_ordering_is_deterministic_and_balanced`: PASS
13. `test_13_exactly_10_a_and_10_b_trials_scheduled`: PASS
14. `test_14_each_pair_contains_exactly_one_a_and_one_b`: PASS
15. `test_15_candidate_identity_metadata_fields`: PASS
16. `test_16_t14_forward_postcondition_fields`: PASS
17. `test_17_paired_discordant_concordant_analysis_contract`: PASS

**Total Test Result:** **17/17 PASS** (plus 56 regression tests across `ActionLedger`, `ActuatorGate`, `LoopBreaker`, `BrowserActuator`, and coordinate validation for **73/73 total PASS**).

---

## 8–13. Explicit Non-Negotiable Boundary Confirmations

8. **T15 Was Untouched**: T15 fixture, prompt, and selector are 100% frozen and completely identical in both conditions. T15 receives `BASELINE_INTERACTIVE_SELECTOR`.
9. **Jev Thresholds Were Untouched**: Confidence threshold (0.70) and margin threshold (0.05) are unmodified.
10. **Gemini Prompts Were Untouched**: System prompt, few-shot examples, and task instructions are completely unmodified.
11. **Comex Was Untouched**: Safety checks, Method E, occlusion detection, ActuatorGate, and ReflexEngine remain active on all execution paths.
12. **Telemetry Schema Change**: `ADDITIVE_ONLY`. Added backward-compatible fields: `target_selector`, `target_class`, `target_text`, `forward_action_observed`, `forward_state_pre`, `forward_state_post`, `forward_state_postcondition`.
13. **Telemetry Semantics**: `UNTOUCHED`. ActionRecord schema integrity, state delta computation, fingerprint hashing, and validation invariants remain strictly enforced without weakening.
