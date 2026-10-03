# Jev Shadow Mode Benchmark & Calibration Report

- **Total Multi-Turn Steps Observed**: 23
- **Evaluations with Jev Active**: 23
- **Overall Gemini/Jev Agreement**: **47.8%**
- **Average Gemini Turn Latency**: **4324.3ms**
- **Average Jev Turn Latency**: **1156.4ms**

## Empirical Reliability / Calibration Curve

| Confidence Bin | Decisions | Agreement Rate | Task Success Rate |
|---|---|---|---|
| `0.50-0.60` | 0 | 0.0% | 0.0% |
| `0.60-0.70` | 1 | 0.0% | 100.0% |
| `0.70-0.80` | 2 | 100.0% | 100.0% |
| `0.80-0.90` | 4 | 100.0% | 100.0% |
| `0.90-0.95` | 5 | 40.0% | 100.0% |
| `0.95-1.00` | 0 | 0.0% | 0.0% |

## Multi-Turn Decision & Verifier Details

| Task | Turn | Epoch | Gemini Action | Jev Action | Jev Conf | Agree? | Verifier Transition |
|---|---|---|---|---|---|---|---|
| T01 | 1 | 1 | `click(bid=1)` | `CLICK:e1 (bid=1)` | 0.85 | ✅ YES | `no_change` |
| T02 | 1 | 3 | `click(bid=2)` | `CLICK:e2 (bid=2)` | 0.70 | ✅ YES | `no_change` |
| T03 | 1 | 5 | `click(bid=1)` | `CLICK:e1 (bid=1)` | 0.91 | ✅ YES | `no_change` |
| T04 | 1 | 7 | `fill(bid=1)` | `FILL:e1 (bid=1)` | 0.90 | ✅ YES | `no_change` |
| T05 | 1 | 9 | `fill(bid=1)` | `FILL:e1 (bid=1)` | 0.86 | ✅ YES | `no_change` |
| T06 | 1 | 11 | `click(bid=1)` | `CLICK:e1 (bid=1)` | 0.73 | ✅ YES | `no_change` |
| T07 | 1 | 13 | `click(bid=2)` | `SELECT:none (bid=None)` | 0.37 | ❌ NO | `no_change` |
| T08 | 1 | 15 | `select_option(bid=1)` | `SELECT:e1 (bid=1)` | 0.43 | ✅ YES | `no_change` |
| T09 | 1 | 17 | `click(bid=1)` | `CLICK:e1 (bid=1)` | 0.23 | ✅ YES | `expected_progress` |
| T10 | 1 | 19 | `fill(bid=2)` | `FILL:e2 (bid=2)` | 0.86 | ✅ YES | `expected_progress` |
| T11 | 1 | 21 | `fill(bid=1)` | `FILL:e1 (bid=1)` | 0.88 | ✅ YES | `expected_progress` |
| T11 | 2 | 23 | `click(bid=7)` | `FILL:e1 (bid=1)` | 0.90 | ❌ NO | `expected_progress` |
| T11 | 3 | 25 | `click(bid=8)` | `FILL:e1 (bid=1)` | 0.92 | ❌ NO | `no_change` |
| T11 | 4 | 27 | `click(bid=4)` | `FILL:e1 (bid=1)` | 0.94 | ❌ NO | `no_change` |
| T12 | 1 | 29 | `click(bid=1)` | `CLICK:e1 (bid=1)` | 0.18 | ✅ YES | `no_change` |
| T13 | 1 | 31 | `click(bid=8)` | `SELECT:none (bid=None)` | 0.67 | ❌ NO | `no_change` |
| T14 | 1 | 33 | `click(bid=11)` | `SCROLL:none (bid=None)` | 0.47 | ❌ NO | `expected_progress` |
| T14 | 2 | 35 | `click(bid=2)` | `SCROLL:none (bid=None)` | 0.32 | ❌ NO | `no_change` |
| T14 | 3 | 37 | `click(bid=2)` | `SCROLL:none (bid=None)` | 0.32 | ❌ NO | `N/A` |
| T15 | 1 | 38 | `fill(bid=1)` | `ESCALATE:none (bid=None)` | 0.34 | ❌ NO | `no_change` |
| T15 | 2 | 40 | `fill(bid=3)` | `ESCALATE:none (bid=None)` | 0.35 | ❌ NO | `no_change` |
| T15 | 3 | 42 | `fill(bid=3)` | `ESCALATE:none (bid=None)` | 0.28 | ❌ NO | `no_change` |
| T15 | 4 | 44 | `fill(bid=3)` | `ESCALATE:none (bid=None)` | 0.32 | ❌ NO | `N/A` |
