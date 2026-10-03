# Phase 2 Failure Forensics: Repeated Empirical Investigation of T14 & T15

> **Protocol:** 10 independent repeated runs per task in live Electron browser.

## 1. Executive Failure Distribution Table

### T14 (`email-inbox` — Complex List Selection)

| Outcome / Primary Failure Mode | Count | Frequency |
|---|---|---|
| **Successful Completion** | 1 | 10.0% |
| **Loop-Break after Gemini Fallback Click** | 8 | 80.0% |
| **Late Occlusion Abort** | 0 | 0.0% |
| **Other Failures** | 1 | 10.0% |

### T15 (`book-flight` — Complex Multi-Field Form)

| Outcome / Primary Failure Mode | Count | Frequency |
|---|---|---|
| **Successful Completion** | 0 | 0.0% |
| **Loop-Break on Datepicker Input** | 10 | 100.0% |
| **Late Occlusion Abort on Search Button** | 0 | 0.0% |
| **Other Failures** | 0 | 0.0% |

## 2. Forensic Analysis: T14 (`email-inbox`)

### [OBSERVED FACT]
- **Turn 1 (Email Selection)**: In 100% of runs, Jev resolved the target email by sender name (e.g. `Edita`) with high confidence (conf ≥ 0.90, margin ≥ 0.85). Comex validated coordinates, and the email opened successfully.
- **Turn 2 (Forward Button Grounding)**: In 100% of runs, Jev abstained (confidence ~0.61–0.64 < 0.70 threshold) because the DOM element tagged with BID 2 contained both `Reply` and `Forward` within an encompassing container `.email-send`.
- **Fallback Execution**: Gemini took over via clean fallback, emitting `click: 2`. Clicking BID 2 clicked the center of the `.email-send` container, which produced zero DOM state change.
- **Loop Breaker Intervention**: When Gemini repeated `click: 2` on Turn 3, Comex's `LoopBreaker` blocked the repeated zero-delta action.

### [SYSTEM INTERPRETATION]
- Jev's abstention on Turn 2 was **strictly correct behavior under policy**: the prompt requested 'forward', but the candidate BID 2 had text 'Reply\nForward'. Resolving BID 2 without high confidence would have violated the margin threshold.
- The failure occurred because the DOM tagging query (`.email-send`) tagged the parent container instead of individual `.email-reply` and `.email-forward` child spans.

### [UNVERIFIED HYPOTHESIS]
- If `.email-reply` and `.email-forward` spans are tagged individually with unique BIDs, Jev would ground 'forward' directly to the child span, avoiding both the abstention and the Gemini click error.

## 3. Forensic Analysis: T15 (`book-flight`)

### [OBSERVED FACT]
- **Turn 1 (Field Grounding)**: Jev accurately resolved origin input BID 1 with conf ≥ 0.78. Comex dispatched the text entry.
- **Turn 2 (Datepicker & Late Occlusion)**: Jev accurately resolved the datepicker input BID 3. However, clicking/filling the datepicker opened the jQuery UI calendar popup.
- **Late Occlusion Abort**: In runs where a subsequent action attempted to click the Search button (BID 4), Comex's `validate_occlusion()` (`elementFromPoint`) detected that the calendar popup physically occluded BID 4, aborting the click.
- **Loop Breaker State**: The input validation script in `book-flight.html` requires `DOMESTIC_FLIGHTS.indexOf(inputOrigin) !== -1`. Setting the input value without selecting the jQuery autocomplete dropdown item kept the input invalid, producing zero state change on subsequent turns until `LoopBreaker` intervened.

### [SYSTEM INTERPRETATION]
- Jev grounded every candidate correctly: origin (BID 1), destination (BID 2), date (BID 3).
- Comex's Late Occlusion check performed exactly as designed, preventing a physical click on an occluded element.
- The failure is an environmental interaction semantics issue with MiniWoB's jQuery UI datepicker and autocomplete widget, not a Jev grounding hallucination.

### [UNVERIFIED HYPOTHESIS]
- Closing the calendar widget before clicking Search, or pressing Enter/Tab to dismiss the autocomplete popup, would allow the form to submit cleanly without triggering late occlusion.
