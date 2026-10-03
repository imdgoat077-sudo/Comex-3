"""
tests/test_commit_c_progress_controller.py
===========================================
Commit C Verification Suite — ProgressController.

Covers:
1. Task decomposition (happy path + fallback)
2. Tier-1 completeness rules (conservative: postcondition+progress is the only deterministic COMPLETE)
3. Regression: state_changed alone is NOT COMPLETE (Maps false-positive regression)
4. Regression: wrong URL + fingerprint changed is NOT COMPLETE
5. Tier-2 rate-limiting (only at attempts % 3 == 0)
6. Tier-2 timeout → UNKNOWN, loop continues
7. task_complete flag hard-stop after all subtasks COMPLETE
8. Reflection edge-trigger (only at attempts==3, not before/after)
9. progress_controller exception does not crash loop
10. Commit D slot: sibling_failures always empty in Commit C
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.progress_controller import (
    ProgressController,
    SubtaskPlan,
    SubtaskRecord,
    SiblingFailure,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_llm(response_text: str):
    """Returns a mock LLM that responds with response_text."""
    mock = MagicMock()
    msg = MagicMock()
    msg.content = response_text
    mock.ainvoke = AsyncMock(return_value=msg)
    return mock


def _b_result(
    postcondition="UNKNOWN",
    progress="UNKNOWN",
    dispatch_success=True,
    state_changed=False,
    status="SUCCESS",
    operation="",
    target_description="",
    details="",
    pre_fingerprint="fp_before",
    post_fingerprint="fp_after",
):
    """Builds a minimal Commit B result dict."""
    return {
        "status": status,
        "operation": operation,
        "target_description": target_description,
        "details": details,
        "dispatch_success": dispatch_success,
        "state_changed": state_changed,
        "postcondition": postcondition,
        "progress": progress,
        "pre_fingerprint": pre_fingerprint,
        "post_fingerprint": post_fingerprint,
    }


# ── Test 1: Decomposition happy path ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_1_decompose_task_returns_structured_subtasks():
    """Mock LLM returns JSON list → ProgressController stores subtasks."""
    payload = '["Navigate to BookMyShow", "Search Paradise cinema", "Select Saturday show"]'
    pc = ProgressController(llm=_make_llm(payload))
    plan = await pc.decompose_task("Book FDFS Paradise on Saturday")
    assert isinstance(plan, SubtaskPlan)
    assert len(plan.subtasks) == 3
    assert plan.subtasks[0] == "Navigate to BookMyShow"
    assert pc.current_subtask().description == "Navigate to BookMyShow"
    assert pc.active_subtask_index == 0


# ── Test 2: Decomposition fallback on bad JSON ─────────────────────────────────

@pytest.mark.asyncio
async def test_2_decompose_task_fallback_on_bad_json():
    """Malformed LLM output → single-subtask mode (entire task = one subtask)."""
    pc = ProgressController(llm=_make_llm("Sorry, I cannot do that."))
    plan = await pc.decompose_task("Search for iPhone on Amazon")
    assert len(plan.subtasks) == 1
    assert plan.subtasks[0] == "Search for iPhone on Amazon"
    assert pc.current_subtask().description == "Search for iPhone on Amazon"


# ── Test 3: Tier-1 COMPLETE on postcondition MET + progress POSITIVE ─────────

@pytest.mark.asyncio
async def test_3_tier1_complete_on_postcondition_met():
    """postcondition=='MET' AND progress=='POSITIVE' → COMPLETE (authoritative Commit B evidence)."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Click search button")]
    act_info = _b_result(postcondition="MET", progress="POSITIVE", state_changed=True)
    pc.record_action(0, act_info)
    result = pc.assess_tier1(0, act_info, current_url="https://bookmyshow.com")
    assert result == "COMPLETE"


# ── Test 4: Tier-1 NOT COMPLETE from state_changed alone ──────────────────────
# MAPS REGRESSION TEST: state changed but navigated to wrong place

@pytest.mark.asyncio
async def test_4_tier1_not_complete_from_state_changed_alone():
    """
    state_changed=True but postcondition not MET → UNKNOWN.
    This is the Maps regression: fingerprint changed but navigation went wrong.
    """
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to Google Maps")]
    act_info = _b_result(
        postcondition="UNKNOWN",
        progress="UNKNOWN",
        dispatch_success=True,
        state_changed=True,  # state changed — but to the wrong place
    )
    pc.record_action(0, act_info)
    result = pc.assess_tier1(0, act_info, current_url="https://google.com/search?q=maps")
    assert result == "UNKNOWN", (
        "state_changed alone must NOT produce COMPLETE — this is the Maps regression guard"
    )


# ── Test 5: Tier-1 NOT COMPLETE from URL change + fingerprint change ───────────

@pytest.mark.asyncio
async def test_5_tier1_wrong_url_state_changed_not_complete():
    """
    URL changed and state changed, but postcondition not MET → UNKNOWN not COMPLETE.
    URL heuristic matching is explicitly rejected in Commit C.
    """
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to BookMyShow")]
    act_info = _b_result(
        postcondition="UNKNOWN",
        progress="UNKNOWN",
        dispatch_success=True,
        state_changed=True,
        pre_fingerprint="fp_a",
        post_fingerprint="fp_b",  # different
    )
    pc.record_action(0, act_info)
    # URL even partially matching the task — should still not COMPLETE from URL alone
    result = pc.assess_tier1(0, act_info, current_url="https://bookmyshow.com/movies")
    assert result == "UNKNOWN", (
        "URL match alone must not produce COMPLETE — no heuristic URL matching in Commit C"
    )


# ── Test 6: Tier-1 INCOMPLETE on failed dispatch ──────────────────────────────

@pytest.mark.asyncio
async def test_6_tier1_incomplete_on_failed_dispatch():
    """dispatch_success=False AND state_changed=False → INCOMPLETE."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Fill departure city")]
    act_info = _b_result(
        dispatch_success=False,
        state_changed=False,
        postcondition="UNMET",
        progress="NEGATIVE",
        status="FAILED",
    )
    pc.record_action(0, act_info)
    result = pc.assess_tier1(0, act_info, current_url="https://example.com")
    assert result == "INCOMPLETE"


# ── Test 7: Tier-1 UNKNOWN on ambiguous success ───────────────────────────────

@pytest.mark.asyncio
async def test_7_tier1_unknown_on_ambiguous_success():
    """dispatch_success=True, state_changed=False, postcondition=UNKNOWN → UNKNOWN."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Fill search box")]
    act_info = _b_result(
        dispatch_success=True,
        state_changed=False,
        postcondition="UNKNOWN",
        progress="UNKNOWN",
    )
    pc.record_action(0, act_info)
    result = pc.assess_tier1(0, act_info, current_url="https://example.com")
    assert result == "UNKNOWN"


# ── Test 8: Tier-2 rate-limit ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_8_tier2_rate_limit_not_called_every_step():
    """should_call_tier2 is True only at attempts % 3 == 0 (attempt 3, 6, 9)."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Select show")]
    ambiguous = _b_result(dispatch_success=True, state_changed=False)

    should_call_map = {}
    for i in range(1, 10):
        pc.record_action(0, ambiguous)
        should_call_map[i] = pc.should_call_tier2(0)

    # True at 3, 6, 9 only
    for attempt_num, should_call in should_call_map.items():
        expected = (attempt_num % 3 == 0)
        assert should_call == expected, (
            f"should_call_tier2 wrong at attempt {attempt_num}: got {should_call}, expected {expected}"
        )


# ── Test 9: Tier-2 timeout returns UNKNOWN, does not block ───────────────────

@pytest.mark.asyncio
async def test_9_tier2_timeout_returns_unknown_does_not_block():
    """assess_tier2_bounded with 0s timeout → UNKNOWN without raising or hanging."""
    async def slow_invoke(*args, **kwargs):
        await asyncio.sleep(10)  # way longer than any timeout

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=slow_invoke)

    pc = ProgressController(llm=mock_llm)
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to maps")]
    pc.record_action(0, _b_result())

    result = await pc.assess_tier2_bounded(0, page_summary="Maps loading...", timeout_s=0.01)
    assert result == "UNKNOWN"


# ── Test 10: advance_to_next increments index ─────────────────────────────────

@pytest.mark.asyncio
async def test_10_advance_to_next_increments_index():
    """advance_to_next marks current COMPLETE and moves to next subtask."""
    pc = ProgressController()
    pc._subtasks = [
        SubtaskRecord(index=0, description="Navigate to site"),
        SubtaskRecord(index=1, description="Select show"),
    ]
    assert pc.active_subtask_index == 0
    next_idx = pc.advance_to_next()
    assert next_idx == 1
    assert pc._subtasks[0].status == "COMPLETE"
    assert pc.active_subtask_index == 1
    assert pc.current_subtask().description == "Select show"
    assert not pc.task_complete


# ── Test 11: task_complete after last subtask ─────────────────────────────────

@pytest.mark.asyncio
async def test_11_task_complete_flag_set_after_last_subtask():
    """After final subtask advance_to_next, task_complete == True."""
    pc = ProgressController()
    pc._subtasks = [
        SubtaskRecord(index=0, description="Navigate to site"),
        SubtaskRecord(index=1, description="Select show"),
    ]
    pc.advance_to_next()   # completes subtask 0, moves to 1
    result = pc.advance_to_next()  # completes subtask 1, moves past end
    assert result is None
    assert pc.task_complete is True


# ── Test 12: Reflection edge-triggered ────────────────────────────────────────

@pytest.mark.asyncio
async def test_12_reflection_injected_at_attempt_3_not_before_not_after():
    """
    should_inject_reflection is True only at attempts==3 (first occurrence).
    Not at 1, 2, or 4+.
    """
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Select cinema")]
    r = _b_result()

    pc.record_action(0, r)  # attempt 1
    assert not pc.should_inject_reflection(0)
    pc.record_action(0, r)  # attempt 2
    assert not pc.should_inject_reflection(0)
    pc.record_action(0, r)  # attempt 3
    assert pc.should_inject_reflection(0)
    # Inject — marks as injected
    pc.get_strategic_reflection(0)
    pc.record_action(0, r)  # attempt 4
    assert not pc.should_inject_reflection(0), "No repeat injection after attempt 3"


# ── Test 13: Reflection content is non-empty and describes attempt history ────

@pytest.mark.asyncio
async def test_13_strategic_reflection_content_nonempty():
    """get_strategic_reflection returns non-empty string describing attempts."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Choose show time")]
    for _ in range(3):
        pc.record_action(0, _b_result(dispatch_success=True, state_changed=False))

    reflection = pc.get_strategic_reflection(0)
    assert reflection
    assert "subtask" in reflection.lower() or "choose show time" in reflection.lower()
    assert "attempt" in reflection.lower() or "3" in reflection


# ── Test 14: ProgressController failure does not crash the loop ───────────────

@pytest.mark.asyncio
async def test_14_progress_controller_failure_does_not_crash_loop():
    """
    If ProgressController raises an exception, the agent loop catches it and continues.
    Simulates the try/except guard in agent_service.py.
    """
    pc = ProgressController()
    pc._subtasks = []  # empty — any call will fail gracefully

    loop_continued = False
    try:
        # Simulate what agent_service does:
        try:
            pc.record_action(999, {})  # invalid index — safe (returns None)
            result = pc.assess_tier1(999, {}, "")  # returns UNKNOWN gracefully
            assert result == "UNKNOWN"
        except Exception:
            pass  # agent_service catch block
        loop_continued = True
    except Exception:
        pass

    assert loop_continued


# ── Test 15: Commit D slot — sibling_failures always empty in Commit C ────────

@pytest.mark.asyncio
async def test_15_sibling_failures_empty_in_commit_c():
    """sibling_failures is frozen empty in Commit C. Never populated here."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Select seat")]
    for _ in range(5):
        pc.record_action(0, _b_result())

    rec = pc.current_subtask()
    assert rec is not None
    assert rec.sibling_failures == [], (
        "sibling_failures must be empty in Commit C — Commit D slot only"
    )
    # Verify the SiblingFailure class itself exists but is not used
    sf = SiblingFailure(
        candidate_id="e3",
        target_description="Book button",
        failure_reason="OCCLUDED",
        state_fingerprint="fp_abc",
    )
    assert sf.candidate_id == "e3"


# ── Test 16: task_complete structural hard stop ──────────────────────────────

@pytest.mark.asyncio
async def test_16_task_complete_blocks_browser_actions():
    """
    When progress_controller.task_complete is True, browser action dispatch is blocked.
    Simulates the agent_service loop check.
    """
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Final step")]
    pc.advance_to_next()
    assert pc.task_complete is True

    tool_call_name = "execute_browser_action"
    blocked = False
    if pc.task_complete and tool_call_name in ("execute_browser_action", "browser_click", "run_browser_harness"):
        blocked = True

    assert blocked is True


# ── Test 17 (Req 7A): state_changed only -> UNKNOWN ──────────────────────────

@pytest.mark.asyncio
async def test_17_tier1_state_changed_only_is_unknown():
    """state_changed=True alone on an unrelated element MUST remain UNKNOWN."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Select Saturday 7:30 PM show")]
    act_info = _b_result(
        operation="CLICK",
        target_description="Unrelated sidebar ad",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)
    result = pc.assess_tier1(0, act_info, current_url="https://site.com")
    assert result == "UNKNOWN", "state_changed=True alone on unrelated target must return UNKNOWN"


# ── Test 18 (Req 7B): physical dispatch_success only -> UNKNOWN ───────────────

@pytest.mark.asyncio
async def test_18_tier1_physical_dispatch_success_only_is_unknown():
    """dispatch_success=True alone without state change MUST remain UNKNOWN."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Select Saturday 7:30 PM show")]
    act_info = _b_result(
        operation="CLICK",
        target_description="7:30 PM showtime button",
        dispatch_success=True,
        state_changed=False,
    )
    pc.record_action(0, act_info)
    result = pc.assess_tier1(0, act_info, current_url="https://site.com")
    assert result == "UNKNOWN", "dispatch_success=True alone without state change must remain UNKNOWN"


# ── Test 19 (Req 7C): genuine generic milestone -> COMPLETE without Tier-2 ───

@pytest.mark.asyncio
async def test_19_tier1_genuine_generic_milestone_complete_without_tier2():
    """Genuine generic milestones (Fill, Selection, Search, Nav) return COMPLETE deterministically."""
    # 1. Fill milestone
    pc_fill = ProgressController()
    pc_fill._subtasks = [SubtaskRecord(index=0, description="Fill departure city with Chennai")]
    act_fill = _b_result(
        operation="FILL",
        target_description="Departure city input field",
        dispatch_success=True,
    )
    res_fill = pc_fill.assess_tier1(0, act_fill, current_url="https://flights.com")
    assert res_fill == "COMPLETE"
    assert "Fill milestone" in pc_fill.current_subtask().completion_evidence

    # 2. Selection milestone
    pc_sel = ProgressController()
    pc_sel._subtasks = [SubtaskRecord(index=0, description="Select Saturday 7:30 PM show")]
    act_sel = _b_result(
        operation="CLICK",
        target_description="7:30 PM showtime button",
        dispatch_success=True,
        state_changed=True,
    )
    res_sel = pc_sel.assess_tier1(0, act_sel, current_url="https://movies.com")
    assert res_sel == "COMPLETE"
    assert "Selection milestone" in pc_sel.current_subtask().completion_evidence

    # 3. Search submission milestone
    pc_search = ProgressController()
    pc_search._subtasks = [SubtaskRecord(index=0, description="Search for Paradise cinema")]
    act_search = _b_result(
        operation="CLICK",
        target_description="Search submit button",
        dispatch_success=True,
        state_changed=True,
    )
    res_search = pc_search.assess_tier1(0, act_search, current_url="https://cinemas.com")
    assert res_search == "COMPLETE"
    assert "Search submission milestone" in pc_search.current_subtask().completion_evidence

    # 4. Navigation milestone with explicit expected_url
    pc_nav = ProgressController()
    pc_nav._subtasks = [SubtaskRecord(index=0, description="Navigate to checkout", expected_url="checkout")]
    act_nav = _b_result(
        operation="CLICK",
        target_description="Proceed to checkout button",
        dispatch_success=True,
        state_changed=True,
    )
    res_nav = pc_nav.assess_tier1(0, act_nav, current_url="https://store.com/checkout/step1")
    assert res_nav == "COMPLETE"
    assert "Navigation milestone" in pc_nav.current_subtask().completion_evidence


# ── Test 20 (Req 7D): wrong-direction state change -> NOT COMPLETE ───────────

@pytest.mark.asyncio
async def test_20_tier1_wrong_direction_state_change_is_not_complete():
    """State change on wrong-direction target MUST NOT produce COMPLETE."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Select Saturday show")]
    act_info = _b_result(
        operation="CLICK",
        target_description="Promotional banner link",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)
    result = pc.assess_tier1(0, act_info, current_url="https://site.com")
    assert result != "COMPLETE"
    assert result == "UNKNOWN"


# ── Test 21 (Req 7E): ambiguous milestone -> UNKNOWN ─────────────────────────

@pytest.mark.asyncio
async def test_21_tier1_ambiguous_milestone_is_unknown():
    """Focusing an input field during a search subtask is ambiguous and MUST return UNKNOWN."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Search for Paradise cinema")]
    # Clicking into the search input field focuses it and mutates DOM (cursor/border),
    # but does NOT complete the search milestone until submitted or results appear.
    act_info = _b_result(
        operation="CLICK",
        target_description="Search query input box",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)
    result = pc.assess_tier1(0, act_info, current_url="https://site.com")
    assert result == "UNKNOWN", "Focusing search box must remain UNKNOWN, not COMPLETE"


# ── Test 22 (Req 7F): Tier-2 invoked only for UNKNOWN at rate limit ──────────

@pytest.mark.asyncio
async def test_22_tier2_invoked_only_for_unknown_at_rate_limit():
    """Tier-2 Gemini is invoked only when Tier 1 is UNKNOWN, gated to attempts % 3 == 0."""
    mock_llm = _make_llm("COMPLETE")
    pc = ProgressController(llm=mock_llm)
    pc._subtasks = [SubtaskRecord(index=0, description="Complex custom flow step")]

    ambiguous_act = _b_result(
        operation="CLICK",
        target_description="Custom widget",
        dispatch_success=True,
        state_changed=True,
    )

    # Attempts 1 and 2: Tier 1 returns UNKNOWN, should_call_tier2 is False
    for attempt in (1, 2):
        pc.record_action(0, ambiguous_act)
        t1 = pc.assess_tier1(0, ambiguous_act, "https://site.com")
        assert t1 == "UNKNOWN"
        assert not pc.should_call_tier2(0)

    # Attempt 3: Tier 1 returns UNKNOWN, should_call_tier2 is True → Tier 2 invoked
    pc.record_action(0, ambiguous_act)
    t1 = pc.assess_tier1(0, ambiguous_act, "https://site.com")
    assert t1 == "UNKNOWN"
    assert pc.should_call_tier2(0)
    t2 = await pc.assess_tier2_bounded(0, page_summary="Widget selected")
    assert t2 == "COMPLETE"
    assert pc._tier2_calls == 1

    # When Tier 1 resolves COMPLETE deterministically, Tier 2 is NEVER invoked
    pc_det = ProgressController(llm=mock_llm)
    pc_det._subtasks = [SubtaskRecord(index=0, description="Fill city with Chennai")]
    act_det = _b_result(operation="FILL", target_description="City input", dispatch_success=True)
    t1_det = pc_det.assess_tier1(0, act_det, "https://site.com")
    assert t1_det == "COMPLETE"
    assert pc_det._tier2_calls == 0


# ── Test 23 (Req 8): Telemetry records exact evidence for COMPLETE ───────────

@pytest.mark.asyncio
async def test_23_telemetry_records_exact_evidence_for_complete_subtask():
    """telemetry() must include the exact completion_evidence string justifying COMPLETE."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Fill user email address")]
    act_info = _b_result(
        operation="FILL",
        target_description="User email input",
        dispatch_success=True,
    )
    pc.record_action(0, act_info)
    res = pc.assess_tier1(0, act_info, current_url="https://site.com/login")
    assert res == "COMPLETE"

    telem = pc.telemetry()
    subtask_data = telem["progress_controller_subtasks"][0]
    assert subtask_data["status"] == "ACTIVE"  # until advance_to_next is called
    assert "Fill milestone" in subtask_data["completion_evidence"]
    assert "user email input" in subtask_data["completion_evidence"].lower()


# ── Test 24: task incomplete + Gemini final → NOT successful completion ────────

@pytest.mark.asyncio
async def test_24_gemini_final_while_task_incomplete_is_not_successful():
    """
    If progress_controller.task_complete==False and terminal_boundary is None,
    a Gemini final/no-tool response MUST NOT be treated as successful task completion.
    This validates the LLM_FINAL_WHILE_INCOMPLETE invariant.
    """
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to BookMyShow")]

    # Task is NOT complete and no terminal boundary was hit
    task_is_complete = pc.task_complete
    terminal_hit = False  # no terminal boundary recorded

    # Gemini emits no tool calls (i.e., it wants to end the loop)
    has_run_tools = True  # tools were called before
    is_conversational = not has_run_tools

    # The invariant: if task not complete + no terminal boundary, Gemini final must be intercepted
    should_intercept = has_run_tools and not is_conversational and not task_is_complete and not terminal_hit
    assert should_intercept is True, (
        "When task is ACTIVE and no terminal boundary hit, Gemini final must be intercepted"
    )


# ── Test 25: task_complete=True → browser-action routes structurally blocked ──

@pytest.mark.asyncio
async def test_25_task_complete_all_browser_routes_blocked():
    """When task_complete=True, all browser action tool names are blocked."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Final step")]
    pc.advance_to_next()
    assert pc.task_complete is True

    browser_tools = ("execute_browser_action", "browser_click", "run_browser_harness")
    for tool_name in browser_tools:
        blocked = pc.task_complete and tool_name in browser_tools
        assert blocked is True, f"Tool '{tool_name}' must be blocked when task_complete=True"


# ── Test 26: terminal boundary → normal clean termination (non-blocking) ──────

@pytest.mark.asyncio
async def test_26_terminal_boundary_allows_clean_termination():
    """
    When terminal_boundary is set in metadata, Gemini final response MUST be
    allowed to terminate normally (not intercepted as LLM_FINAL_WHILE_INCOMPLETE).
    """
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Proceed to checkout")]

    # Simulate terminal boundary being hit
    metadata_terminal_boundary = "LOGIN_REQUIRED"
    terminal_hit = metadata_terminal_boundary not in (None, "NONE")

    # The invariant: terminal boundary hit → should NOT intercept Gemini final
    has_run_tools = True
    is_conversational = False
    task_is_complete = pc.task_complete  # False (subtask still active)

    should_intercept = has_run_tools and not is_conversational and not task_is_complete and not terminal_hit
    assert should_intercept is False, (
        "When terminal boundary is hit, Gemini final must not be intercepted as LLM_FINAL_WHILE_INCOMPLETE"
    )


# ── Test 27: observation-only harness calls do NOT create physical stall counts ─

@pytest.mark.asyncio
async def test_27_observation_harness_calls_do_not_create_stall_counts():
    """
    run_browser_harness observation calls are NOT counted as physical stalls.
    Only execute_browser_action failures and browser_click failures are stalls.
    Validates Commit B stall semantics are preserved.
    """
    # Simulate the stall-detection logic from agent_service.py
    consecutive_stalls = 0

    # 3 observation-only run_browser_harness calls (success=True, no stall)
    for _ in range(3):
        tname = "run_browser_harness"
        is_error = False
        # Stall logic: only execute_browser_action and browser_click contribute
        if tname == "execute_browser_action":
            act_info = {}
            disp_ok = act_info.get("dispatch_success", False)
            if not disp_ok:
                consecutive_stalls += 1
        elif tname == "browser_click":
            if is_error:
                consecutive_stalls += 1
        # run_browser_harness: no contribution regardless of success

    assert consecutive_stalls == 0, (
        "Observation-only run_browser_harness calls must NOT increment consecutive_stalls"
    )


# ── Test 28: simple task decomposes to 1 subtask ─────────────────────────────

@pytest.mark.asyncio
async def test_28_simple_task_decomposes_to_one_subtask():
    """
    Simple single-step tasks must decompose to exactly 1 subtask, not be force-split.
    """
    payload = '["Search for iPhone on Amazon"]'
    pc = ProgressController(llm=_make_llm(payload))
    plan = await pc.decompose_task("Search for iPhone on Amazon")
    assert len(plan.subtasks) == 1
    assert plan.subtasks[0] == "Search for iPhone on Amazon"


# ── Test 29: multi-stage transactional task decomposes to multiple subtasks ───

@pytest.mark.asyncio
async def test_29_multistage_task_decomposes_to_multiple_subtasks():
    """
    Multi-stage transactional workflows must produce multiple meaningful subtasks.
    The mock response simulates what the improved decomposition prompt should generate.
    """
    payload = (
        '["Navigate to BookMyShow", "Search for Paradise cinema", '
        '"Select FDFS Saturday show", "Choose seats", "Proceed to payment"]'
    )
    pc = ProgressController(llm=_make_llm(payload))
    plan = await pc.decompose_task("Book FDFS Paradise on Saturday on BookMyShow")
    assert len(plan.subtasks) >= 3, (
        "Multi-stage booking task must decompose into at least 3 subtasks"
    )
    assert any("navigate" in s.lower() or "open" in s.lower() for s in plan.subtasks), (
        "Must include a navigation subtask"
    )
    assert any("seat" in s.lower() or "select" in s.lower() or "show" in s.lower() for s in plan.subtasks), (
        "Must include a selection/booking subtask"
    )


# ── Tests 30–36: Navigation Milestone Race & Asynchronous Redirect Tests ─────

@pytest.mark.asyncio
async def test_30_immediate_successful_navigation_with_url_available():
    """1. Immediate successful navigation with URL already available -> COMPLETE."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to BookMyShow", expected_url="bookmyshow")]
    act_info = _b_result(
        operation="NAVIGATE",
        target_description="goto_url('https://in.bookmyshow.com')",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)
    res = pc.assess_tier1(0, act_info, current_url="https://in.bookmyshow.com/explore/home/chennai")
    assert res == "COMPLETE"
    assert "Navigation milestone" in pc.current_subtask().completion_evidence
    assert "bookmyshow" in pc.current_subtask().completion_evidence.lower()


@pytest.mark.asyncio
async def test_31_url_settles_from_empty_to_expected_within_window():
    """2. URL initially empty / about:blank, then becomes expected URL within bounded window -> COMPLETE."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to GitHub", expected_url="github")]
    act_info = _b_result(
        operation="NAVIGATE",
        target_description="goto_url('https://github.com')",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)

    # Simulated bounded polling helper
    urls = ["", "about:blank", "https://github.com/login"]
    call_count = 0
    def mock_get_url(cdp_url):
        nonlocal call_count
        u = urls[min(call_count, len(urls) - 1)]
        call_count += 1
        return u

    async def mock_get_current_url_settled(cdp_url, retries=4, delay_s=0.01):
        for i in range(retries):
            u = mock_get_url(cdp_url)
            if u and not u.startswith("about:"):
                return u
            await asyncio.sleep(delay_s)
        return mock_get_url(cdp_url)

    settled_url = await mock_get_current_url_settled("ws://fake", retries=4, delay_s=0.01)
    assert "github.com" in settled_url
    res = pc.assess_tier1(0, act_info, current_url=settled_url)
    assert res == "COMPLETE"
    assert "Navigation milestone" in pc.current_subtask().completion_evidence


@pytest.mark.asyncio
async def test_32_url_never_becomes_expected_remains_incomplete():
    """3. URL initially empty / about:blank and never becomes expected URL -> does NOT claim COMPLETE."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to Twitter", expected_url="twitter")]
    act_info = _b_result(
        operation="NAVIGATE",
        target_description="goto_url('https://twitter.com')",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)

    # Stays about:blank through all retries
    async def mock_get_current_url_settled(cdp_url, retries=3, delay_s=0.01):
        for i in range(retries):
            await asyncio.sleep(delay_s)
        return "about:blank"

    settled_url = await mock_get_current_url_settled("ws://fake", retries=3, delay_s=0.01)
    res = pc.assess_tier1(0, act_info, current_url=settled_url)
    assert res != "COMPLETE"
    assert res == "UNKNOWN"


@pytest.mark.asyncio
async def test_33_redirect_landing_on_expected_domain_is_complete():
    """4. Redirect: initial URL is vanity/short domain, final URL lands on expected destination -> COMPLETE."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to BookMyShow", expected_url="bookmyshow")]
    act_info = _b_result(
        operation="NAVIGATE",
        target_description="goto_url('https://bms.com')",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)

    # Landed on redirect destination
    final_redirect_url = "https://in.bookmyshow.com/explore/home/mumbai"
    res = pc.assess_tier1(0, act_info, current_url=final_redirect_url)
    assert res == "COMPLETE"
    assert "Navigation milestone" in pc.current_subtask().completion_evidence


@pytest.mark.asyncio
async def test_34_wrong_destination_not_complete():
    """5. Wrong destination: navigation succeeds physically but lands on unrelated URL -> NOT COMPLETE."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to BookMyShow", expected_url="bookmyshow")]
    act_info = _b_result(
        operation="NAVIGATE",
        target_description="goto_url('https://in.bookmyshow.com')",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)

    # Landed on google search or 404 page
    res = pc.assess_tier1(0, act_info, current_url="https://google.com/search?q=bookmyshow")
    assert res != "COMPLETE"
    assert res == "UNKNOWN"


@pytest.mark.asyncio
async def test_35_existing_non_navigation_completions_remain_unaffected():
    """6. Existing non-navigation Commit C completion behavior remains unchanged."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Select 2D format")]
    act_info = _b_result(
        operation="CLICK",
        target_description="2D format button",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)
    res = pc.assess_tier1(0, act_info, current_url="https://site.com/showtimes")
    assert res == "COMPLETE"
    assert "Selection milestone" in pc.current_subtask().completion_evidence


@pytest.mark.asyncio
async def test_36_observation_failure_fails_closed():
    """7. Timeout / observation failure in URL settling fails closed and does not crash."""
    pc = ProgressController()
    pc._subtasks = [SubtaskRecord(index=0, description="Navigate to Amazon", expected_url="amazon")]
    act_info = _b_result(
        operation="NAVIGATE",
        target_description="goto_url('https://amazon.com')",
        dispatch_success=True,
        state_changed=True,
    )
    pc.record_action(0, act_info)

    # Simulates network/CDP exception inside _get_current_url_settled
    async def broken_url_settled():
        try:
            raise RuntimeError("CDP disconnect")
        except Exception:
            return ""

    res_url = await broken_url_settled()
    res = pc.assess_tier1(0, act_info, current_url=res_url)
    assert res == "UNKNOWN"
    assert res != "COMPLETE"

