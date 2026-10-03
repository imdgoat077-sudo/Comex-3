"""
Tests for TypeSafe AI Jev Client.
"""

import pytest
from services.typesafe_client import (
    AsyncTypeSafeClient,
    ChoiceAnswer,
    ChoiceQuestion,
    ClientMode,
    JevServiceUnavailableError,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneResponse,
)


@pytest.mark.asyncio
async def test_offline_mode_raises_unavailable_error():
    """Offline mode must cleanly raise JevServiceUnavailableError and never guess."""
    client = AsyncTypeSafeClient(mode=ClientMode.OFFLINE)
    with pytest.raises(JevServiceUnavailableError):
        await client.system_one(
            state="Click the submit button",
            questions={
                "is_urgent": NoulQuestion(instructions="Is this urgent?"),
            },
        )


@pytest.mark.asyncio
async def test_mock_mode_registered_response():
    """Mock mode correctly returns registered typed responses for testing."""
    client = AsyncTypeSafeClient(mode=ClientMode.MOCK)
    expected = SystemOneResponse(
        model="jev-latest",
        answers={
            "action": ChoiceAnswer(
                choice="CLICK:e17",
                probabilities={"CLICK:e17": 0.94, "CLICK:e18": 0.06},
                confidence=0.94,
            ),
            "succeeded": NoulAnswer(noul=0.98),
            "frustration": ScoreAnswer(
                score=1.2,
                probabilities={"low": 0.8, "med": 0.2, "high": 0.0},
                confidence=0.85,
            ),
        },
        usage={"input_tokens": 120, "output_tokens": 0},
    )
    client.register_mock_response("default", expected)

    res = await client.system_one(
        state="Current page state",
        questions={
            "action": ChoiceQuestion(
                instructions="Which element to click?",
                criteria={"CLICK:e17": "Submit button", "CLICK:e18": "Cancel button"},
            ),
            "succeeded": NoulQuestion(instructions="Did it succeed?"),
            "frustration": ScoreQuestion(
                instructions="Frustration level?",
                criteria=["low", "med", "high"],
            ),
        },
    )

    assert res.choices["action"].choice == "CLICK:e17"
    assert res.choices["action"].confidence == 0.94
    assert res.nouls["succeeded"].is_yes is True
    assert res.scores["frustration"].score == 1.2


@pytest.mark.asyncio
async def test_live_system_one_evaluation():
    """Validates real TypeSafe Jev API connection and evaluation with active API key."""
    import os
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key or not api_key.startswith("apikey_"):
        pytest.skip("TYPESAFE_API_KEY not set or invalid")

    client = AsyncTypeSafeClient(api_key=api_key, mode=ClientMode.LIVE)
    q = ChoiceQuestion(
        instructions="Which button submits the form?",
        criteria={"submit": "Primary submit button", "cancel": "Cancel button"}
    )
    res = await client.system_one(
        state="A login form with Submit and Cancel buttons.",
        questions={"q1": q}
    )

    assert res.model.startswith("jev-")
    assert res.choices["q1"].choice == "submit"
    assert res.choices["q1"].confidence > 0.8
    assert res.choices["q1"].probabilities["submit"] > 0.8

