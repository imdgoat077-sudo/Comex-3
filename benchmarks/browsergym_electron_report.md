# ServiceNow BrowserGym Benchmark Report — Comex Electron Browser

- **Model**: `ag/gemini-3.7-flash-medium`
- **Runtime Environment**: Live Comex Electron Shell (`WebContentsView` via CDP Port 9222)
- **Overall Accuracy**: **12/15 (80.0%)**
- **Average Duration**: **10.98s**
- **Total Gemini Calls**: **24**
- **Total Jev Delegations**: **11**
- **Total Jev Abstentions**: **12**
- **Total Fallbacks to Gemini**: **12**
- **Wrong Target Executions**: **0**
- **Comex Blocks**: **8**
- **Total Latency Saved**: **29.99s**
- **Loop-Break Events**: **4**
- **Blocked Repeated Actions**: **4**
- **Re-observations**: **0**
- **Successful Recoveries**: **0**
- **Evaluation Mode**: Production Control Loop (BrowserActuator + LoopBreaker + Occlusion Check)

## Category Performance

| Category | Tasks | Passed | Accuracy |
|---|---|---|---|
| Button Click | 2 | 2 | 100.0% |
| Checkbox Selection | 1 | 1 | 100.0% |
| Complex List Selection | 1 | 0 | 0.0% |
| Complex Multi-Field Form | 1 | 0 | 0.0% |
| Dropdown Select | 1 | 0 | 0.0% |
| Form Submission | 1 | 1 | 100.0% |
| Hierarchy & Toggle | 1 | 1 | 100.0% |
| Input Focus | 1 | 1 | 100.0% |
| Modal Dialog | 1 | 1 | 100.0% |
| Multi-Step Click | 1 | 1 | 100.0% |
| Radio / Option | 1 | 1 | 100.0% |
| Search & Navigate | 1 | 1 | 100.0% |
| Text Input | 2 | 2 | 100.0% |

## Task Execution Details

| ID | Task Name | Category | Result | Reward | Latency | Turns | Gemini | Delegated | Abstained | Fallback | Latency Saved |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T01 | `click-test` | Button Click | **PASS** | 1.00 | 6.67s | 1 | 1 | 1 | 0 | 0 | 1.34s |
| T02 | `click-button` | Button Click | **PASS** | 1.00 | 6.35s | 1 | 1 | 1 | 0 | 0 | 1.8s |
| T03 | `click-button-sequence` | Multi-Step Click | **PASS** | 1.00 | 8.1s | 1 | 1 | 1 | 0 | 0 | 2.76s |
| T04 | `enter-text` | Text Input | **PASS** | 1.00 | 6.92s | 1 | 1 | 0 | 1 | 1 | 0.0s |
| T05 | `enter-text-dynamic` | Text Input | **PASS** | 1.00 | 6.41s | 1 | 1 | 0 | 1 | 1 | 0.0s |
| T06 | `focus-text` | Input Focus | **PASS** | 1.00 | 5.85s | 1 | 1 | 0 | 1 | 1 | 0.0s |
| T07 | `click-checkboxes` | Checkbox Selection | **PASS** | 1.00 | 6.61s | 1 | 1 | 0 | 1 | 1 | 0.0s |
| T08 | `choose-list` | Dropdown Select | **FAIL** | -1.00 | 6.89s | 1 | 1 | 0 | 1 | 1 | 0.0s |
| T09 | `click-dialog` | Modal Dialog | **PASS** | 1.00 | 5.87s | 1 | 1 | 1 | 0 | 0 | 1.72s |
| T10 | `login-user` | Form Submission | **PASS** | 1.00 | 6.96s | 1 | 1 | 1 | 0 | 0 | 2.2s |
| T11 | `search-engine` | Search & Navigate | **PASS** | 1.00 | 25.63s | 4 | 4 | 1 | 3 | 3 | 2.73s |
| T12 | `click-collapsible` | Hierarchy & Toggle | **PASS** | 1.00 | 6.08s | 1 | 1 | 1 | 0 | 0 | 2.14s |
| T13 | `click-option` | Radio / Option | **PASS** | 1.00 | 6.68s | 1 | 1 | 0 | 1 | 1 | 0.0s |
| T14 | `email-inbox` | Complex List Selection | **FAIL** | 0.00 | 28.79s | 4 | 4 | 1 | 3 | 3 | 5.55s |
| T15 | `book-flight` | Complex Multi-Field Form | **FAIL** | 0.00 | 30.89s | 4 | 4 | 3 | 0 | 0 | 9.75s |
