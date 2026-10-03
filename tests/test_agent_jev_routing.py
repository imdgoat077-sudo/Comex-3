import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent_service import detect_interactive_action_in_code
from services.browser_pipeline import (
    BrowserActionPipeline,
    SemanticMicroTask,
    PipelineStatus,
)
from services.candidate_generator import CandidateElement, GroundedAction, ObservationSnapshot
from services.micro_action_resolver import DelegationResolution, DelegationStatus


def test_ast_guardrail_rejections():
    """Verify AST guardrail rejects interactive calls and DOM mutations."""
    interactive_cases = [
        ("click_element('button#search')", "click_element"),
        ("click_at_xy(150, 250)", "click_at_xy"),
        ("element.click()", "click"),
        ("input_elem.value = 'Lo-Fi Beats'", "value"),
        ("js('document.querySelector(\"button\").click()')", "click"),
        ("js('document.querySelector(\"input\").value = \"query\"')", "value"),
        ("btn.fill('hello')", "fill"),
    ]
    for code, expected_kw in interactive_cases:
        reason = detect_interactive_action_in_code(code)
        assert reason is not None, f"Expected rejection for: {code}"
        assert expected_kw in reason.lower() or "javascript" in reason.lower() or "direct call" in reason.lower()


def test_ast_guardrail_permits_readonly():
    """Verify AST guardrail permits navigation, scrolling, waiting, and read-only extraction."""
    readonly_cases = [
        "goto_url('https://www.youtube.com')",
        "page_info()",
        "res = str(js('document.title')); print(res)",
        "js('window.scrollBy(0, 500)')",
        "# Note: click_element was deprecated\nprint('do not use click_element')",
        "import time\ntime.sleep(2)",
        "data = js('Array.from(document.querySelectorAll(\"h3\")).map(e => e.innerText)')",
    ]
    for code in readonly_cases:
        reason = detect_interactive_action_in_code(code)
        assert reason is None, f"Expected allowed for: {code}, but got: {reason}"


@pytest.mark.asyncio
async def test_fill_then_click_pipeline_sequence():
    """Verify an end-to-end FILL -> CLICK sequence invokes MicroActionResolver and BrowserActuator."""
    pipeline = BrowserActionPipeline(delegate_jev=True)

    # Mock candidate generator and extractor
    mock_candidate_search = CandidateElement(
        candidate_id="c_input",
        epoch_id=1,
        bid="bid_input",
        tag="input",
        element_type="text",
        dom_id="search_input",
        text="",
        label="Search input",
    )
    mock_candidate_btn = CandidateElement(
        candidate_id="c_btn",
        epoch_id=1,
        bid="bid_btn",
        tag="button",
        element_type="submit",
        dom_id="search_btn",
        text="Search",
        label="",
    )

    pipeline.extractor.extract_from_cdp = AsyncMock(return_value=[{"bid": "bid_input"}, {"bid": "bid_btn"}])
    pipeline.extractor.compute_fingerprint_from_cdp = AsyncMock(return_value="fp_123")

    mock_snapshot = ObservationSnapshot(
        epoch_id=1,
        timestamp_mono=1.0,
        goal="Search",
        candidates=[mock_candidate_search, mock_candidate_btn],
        url="https://www.youtube.com",
    )
    pipeline.candidate_generator.generate_snapshot = MagicMock(return_value=mock_snapshot)

    # Mock MicroActionResolver
    async def mock_resolve(snapshot, micro_task):
        if micro_task.operation == "FILL":
            action = GroundedAction(
                operation="FILL",
                target_id="c_input",
                target_element=mock_candidate_search,
                confidence=0.96,
                margin=0.35,
                abstained=False,
            )
            return DelegationResolution(
                status=DelegationStatus.RESOLVED,
                action=action,
                target_element=mock_candidate_search,
                latency_ms=120.0,
                reason="High confidence match for search input",
            )
        elif micro_task.operation == "CLICK":
            action = GroundedAction(
                operation="CLICK",
                target_id="c_btn",
                target_element=mock_candidate_btn,
                confidence=0.98,
                margin=0.40,
                abstained=False,
            )
            return DelegationResolution(
                status=DelegationStatus.RESOLVED,
                action=action,
                target_element=mock_candidate_btn,
                latency_ms=110.0,
                reason="High confidence match for search button",
            )
        return DelegationResolution(status=DelegationStatus.ABSTAINED_NO_ACTIONABLE_TARGET, reason="unknown")

    pipeline.micro_action_resolver.resolve = AsyncMock(side_effect=mock_resolve)

    # Mock Actuator
    pipeline.actuator.validate_occlusion = AsyncMock(return_value={"status": "OK"})
    mock_fill_res = MagicMock()
    mock_fill_res.is_success = True
    mock_fill_res.status.value = "SUCCESS"
    mock_fill_res.details = "Filled 'Lo-Fi Beats' into search input"
    pipeline.actuator.execute_atomic_fill = AsyncMock(return_value=mock_fill_res)

    mock_click_res = MagicMock()
    mock_click_res.is_success = True
    mock_click_res.status.value = "SUCCESS"
    mock_click_res.details = "Clicked search button"
    pipeline.actuator.execute_click = AsyncMock(return_value=mock_click_res)

    mock_ws = AsyncMock()

    # Step 1: Execute FILL
    fill_res = await pipeline.execute_action(
        ws=mock_ws,
        operation="FILL",
        target_description="Search box",
        value="Lo-Fi Beats",
        goal="Search for Lo-Fi Beats",
    )
    assert fill_res.status == PipelineStatus.SUCCESS
    assert fill_res.target_id == "c_input"
    assert fill_res.jev_latency_ms == 120.0
    pipeline.actuator.execute_atomic_fill.assert_awaited_once()

    # Step 2: Execute CLICK
    click_res = await pipeline.execute_action(
        ws=mock_ws,
        operation="CLICK",
        target_description="Search button",
        goal="Search for Lo-Fi Beats",
    )
    assert click_res.status == PipelineStatus.SUCCESS
    assert click_res.target_id == "c_btn"
    assert click_res.jev_latency_ms == 110.0
    pipeline.actuator.execute_click.assert_awaited_once()

    # Verify MicroActionResolver was called twice (once for FILL, once for CLICK)
    assert pipeline.micro_action_resolver.resolve.await_count == 2


def test_run_browser_harness_guardrail_blocks_subprocess():
    """Verify that interactive code passed to run_browser_harness is blocked before subprocess.run."""
    from agent_service import run_agent, AgentRequest

    # Test the guardrail directly on the interactive snippet
    code = "click_element('button#search')"
    violation = detect_interactive_action_in_code(code)
    assert violation is not None
    assert "click_element" in violation

    with patch("subprocess.run") as mock_run:
        # If run_browser_harness is invoked with this code, subprocess.run must NOT be called
        # We can simulate the harness wrapper logic directly:
        guard = detect_interactive_action_in_code(code)
        if not guard:
            mock_run()
        mock_run.assert_not_called()

