"""
Unit tests for MicroActionResolver (Phase 2 Controlled Delegation Pilot).
"""

import asyncio
import pytest
from services.candidate_generator import (
    CandidateGenerator,
    SemanticMicroTask,
)
from services.micro_action_resolver import (
    DelegationResolution,
    DelegationStatus,
    MicroActionResolver,
)
from services.typesafe_client import (
    AsyncTypeSafeClient,
    ChoiceAnswer,
    ClientMode,
    JevServiceUnavailableError,
    SystemOneResponse,
)


@pytest.fixture
def test_snapshot():
    generator = CandidateGenerator()
    raw = [
        {"bid": "1", "tag": "button", "type": "submit", "id": "search-btn", "text": "Search"},
        {"bid": "2", "tag": "input", "type": "text", "id": "city-from", "label": "Departure City"},
        {"bid": "3", "tag": "select", "type": "", "id": "country-sel", "options": ["USA", "Canada", "UK"]},
    ]
    return generator.generate_snapshot(raw, goal="Book flight from Chennai to Toronto")


@pytest.mark.asyncio
async def test_resolver_click_delegation_success(test_snapshot):
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    mock_resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(
                choice="e1",
                probabilities={"e1": 0.94, "e2": 0.06},
                confidence=0.94,
            )
        },
        latency_ms=95.0,
    )
    client.register_mock_response("default", mock_resp)

    resolver = MicroActionResolver(client=client)
    micro_task = SemanticMicroTask(operation="CLICK", target_description="Search button")

    res = await resolver.resolve(test_snapshot, micro_task)
    assert res.status == DelegationStatus.RESOLVED
    assert res.can_delegate is True
    assert res.target_element is not None
    assert res.target_element.bid == "1"
    assert res.action is not None
    assert res.action.confidence == 0.94
    assert res.action.operation == "CLICK"


@pytest.mark.asyncio
async def test_resolver_fill_delegation_success(test_snapshot):
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    mock_resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "fill_target": ChoiceAnswer(
                choice="e2",
                probabilities={"e2": 0.88, "e1": 0.12},
                confidence=0.88,
            )
        },
        latency_ms=110.0,
    )
    client.register_mock_response("default", mock_resp)

    resolver = MicroActionResolver(client=client)
    micro_task = SemanticMicroTask(
        operation="FILL",
        target_description="Departure City field",
        fill_value="Chennai",
    )

    res = await resolver.resolve(test_snapshot, micro_task)
    assert res.status == DelegationStatus.RESOLVED
    assert res.can_delegate is True
    assert res.target_element is not None
    assert res.target_element.bid == "2"
    assert res.action.operation == "FILL"


@pytest.mark.asyncio
async def test_resolver_select_strictly_disallowed(test_snapshot):
    """
    Verifies that SELECT is strictly excluded from Phase 2 delegation
    and immediately yields to Gemini System 2 without calling Jev actuation.
    """
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    resolver = MicroActionResolver(client=client)
    micro_task = SemanticMicroTask(
        operation="SELECT",
        target_description="Country dropdown",
        select_option="Canada",
    )

    res = await resolver.resolve(test_snapshot, micro_task)
    assert res.status == DelegationStatus.ABSTAINED_OPERATION_DISALLOWED
    assert res.can_delegate is False
    assert "not approved for delegation" in res.reason
    assert res.action.abstained is True


@pytest.mark.asyncio
async def test_resolver_abstain_on_no_actionable_target(test_snapshot):
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    mock_resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(
                choice="NO_ACTIONABLE_TARGET",
                probabilities={"NO_ACTIONABLE_TARGET": 0.96},
                confidence=0.96,
            )
        },
    )
    client.register_mock_response("default", mock_resp)

    resolver = MicroActionResolver(client=client)
    micro_task = SemanticMicroTask(operation="CLICK", target_description="button id ''")

    res = await resolver.resolve(test_snapshot, micro_task)
    assert res.status == DelegationStatus.ABSTAINED_NO_ACTIONABLE_TARGET
    assert res.can_delegate is False
    assert res.action.abstained is True
    assert res.action.decision_type == "NO_ACTIONABLE_TARGET"


@pytest.mark.asyncio
async def test_resolver_abstain_on_low_confidence(test_snapshot):
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    mock_resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "click_target": ChoiceAnswer(
                choice="e1",
                probabilities={"e1": 0.55, "e2": 0.45},
                confidence=0.55,  # Below 0.70 threshold
            )
        },
    )
    client.register_mock_response("default", mock_resp)

    resolver = MicroActionResolver(client=client)
    micro_task = SemanticMicroTask(operation="CLICK", target_description="2")

    res = await resolver.resolve(test_snapshot, micro_task)
    assert res.status == DelegationStatus.ABSTAINED_LOW_CONFIDENCE
    assert res.can_delegate is False
    assert res.action.abstained is True


@pytest.mark.asyncio
async def test_resolver_abstain_on_low_margin(test_snapshot):
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    mock_resp = SystemOneResponse(
        model="jev-latest",
        answers={
            "fill_target": ChoiceAnswer(
                choice="e2",
                probabilities={"e2": 0.76, "e1": 0.74},  # Margin 0.02 < 0.10
                confidence=0.76,
            )
        },
    )
    client.register_mock_response("default", mock_resp)

    resolver = MicroActionResolver(client=client)
    micro_task = SemanticMicroTask(operation="FILL", target_description="input field", fill_value="test")

    res = await resolver.resolve(test_snapshot, micro_task)
    assert res.status == DelegationStatus.ABSTAINED_LOW_MARGIN
    assert res.can_delegate is False
    assert res.action.abstained is True


@pytest.mark.asyncio
async def test_resolver_timeout_fails_forward(test_snapshot):
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)

    # Simulate slow response exceeding timeout
    async def slow_query(*args, **kwargs):
        await asyncio.sleep(0.5)
        return SystemOneResponse(model="jev-latest", answers={})

    client.system_one = slow_query

    resolver = MicroActionResolver(client=client, timeout_ms=50.0)  # 50ms timeout
    micro_task = SemanticMicroTask(operation="CLICK", target_description="Search")

    res = await resolver.resolve(test_snapshot, micro_task)
    assert res.status == DelegationStatus.ABSTAINED_TIMEOUT
    assert res.can_delegate is False
    assert res.action.abstained is True
    assert "TIMEOUT" in (res.action.abstain_reason or "")
