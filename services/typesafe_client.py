"""
TypeSafe AI Jev (System One) Client for Comex Browser.

Provides high-performance, asynchronous and synchronous access to TypeSafe AI's
System One API (`POST /v1/systemone`) and the Jev model family.

Strict Operating Modes:
  - LIVE: Uses real HTTP API via TYPESAFE_API_KEY (or TYPESAFE_ENDPOINT).
  - MOCK: Exclusively for automated unit tests with registered deterministic responses.
  - OFFLINE: Fails explicitly with JevServiceUnavailableError so the caller can cleanly
            escalate to System 2 (Gemini / Claude). Zero silent heuristic downgrades.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Union

import httpx

logger = logging.getLogger("comex.services.typesafe")

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


class ClientMode(Enum):
    LIVE = "LIVE"
    MOCK = "MOCK"
    OFFLINE = "OFFLINE"


class JevError(Exception):
    """Base exception for TypeSafe Jev client errors."""
    pass


class JevServiceUnavailableError(JevError):
    """Raised when Jev is in OFFLINE mode or the service cannot be reached."""
    pass


class JevAuthenticationError(JevError):
    """Raised when the API key is missing or rejected."""
    pass


# ──────────────────────────────────────────────────────────────────────────────
#  Question Primitives
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class NoulQuestion:
    """Noul (binary probability) question."""
    instructions: str
    criteria: Optional[Dict[str, str]] = None
    type: str = "noul"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": "noul",
            "instructions": self.instructions,
        }
        if self.criteria:
            d["criteria"] = self.criteria
        return d


@dataclass
class ChoiceQuestion:
    """Choice question selecting one option out of a bounded set."""
    instructions: str
    criteria: Dict[str, Optional[str]]
    type: str = "choice"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": self.criteria,
        }


@dataclass
class ScoreQuestion:
    """Score question rating input along an ordered rubric."""
    instructions: str
    criteria: List[str]
    type: str = "score"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": self.criteria,
        }


Question = Union[NoulQuestion, ChoiceQuestion, ScoreQuestion]


# ──────────────────────────────────────────────────────────────────────────────
#  Answer Primitives
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NoulAnswer:
    noul: float
    type: str = "noul"

    @property
    def is_yes(self) -> bool:
        return self.noul >= 0.5


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: Dict[str, float]
    confidence: float
    type: str = "choice"


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    probabilities: Dict[str, float]
    confidence: float
    legend: Optional[List[str]] = None
    type: str = "score"


Answer = Union[NoulAnswer, ChoiceAnswer, ScoreAnswer]


@dataclass(frozen=True)
class SystemOneResponse:
    model: str
    answers: Dict[str, Answer]
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def nouls(self) -> Dict[str, NoulAnswer]:
        return {k: v for k, v in self.answers.items() if isinstance(v, NoulAnswer)}

    @property
    def choices(self) -> Dict[str, ChoiceAnswer]:
        return {k: v for k, v in self.answers.items() if isinstance(v, ChoiceAnswer)}

    @property
    def scores(self) -> Dict[str, ScoreAnswer]:
        return {k: v for k, v in self.answers.items() if isinstance(v, ScoreAnswer)}


# ──────────────────────────────────────────────────────────────────────────────
#  Client Implementation
# ──────────────────────────────────────────────────────────────────────────────

_SHARED_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


class AsyncTypeSafeClient:
    """Asynchronous client for TypeSafe AI's System One API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 10.0,
        mode: Optional[ClientMode] = None,
    ):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self.endpoint = endpoint or os.environ.get("TYPESAFE_ENDPOINT", DEFAULT_ENDPOINT)
        self.default_model = model or os.environ.get("TYPESAFE_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

        if mode is not None:
            self.mode = mode
        elif self.api_key:
            self.mode = ClientMode.LIVE
        else:
            self.mode = ClientMode.OFFLINE

        self._mock_responses: Dict[str, SystemOneResponse] = {}
        self._mock_handler: Optional[Any] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        global _SHARED_HTTP_CLIENT
        if self._http_client is not None and not self._http_client.is_closed:
            return self._http_client
        if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
            limits = httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=120.0)
            _SHARED_HTTP_CLIENT = httpx.AsyncClient(timeout=self.timeout, limits=limits)
        return _SHARED_HTTP_CLIENT

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def warmup(self) -> None:
        """Pre-establishes TLS connection pool so the first micro-action query has zero handshake penalty."""
        if self.mode != ClientMode.LIVE or not self.api_key:
            return
        try:
            client = await self._get_client()
            dummy_q = {"ping": {"type": "choice", "instructions": "ping", "criteria": {"a": "a"}}}
            payload = {"model": self.default_model, "state": "warmup", "questions": dummy_q}
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ComexBrowser/1.0",
            }
            await client.post(self.endpoint, json=payload, headers=headers)
            logger.info("TypeSafe client connection pool pre-warmed successfully.")
        except Exception as e:
            logger.debug(f"TypeSafeClient warmup: {e}")

    def register_mock_response(self, key: str, response: SystemOneResponse) -> None:
        """Register a deterministic mock response for unit tests."""
        self._mock_responses[key] = response

    def set_mock_handler(self, handler: Any) -> None:
        """Set a dynamic mock handler function: (state, questions, model) -> SystemOneResponse."""
        self._mock_handler = handler

    async def system_one(
        self,
        state: Union[str, Dict[str, Any], List[Any]],
        questions: Dict[str, Question],
        model: Optional[str] = None,
    ) -> SystemOneResponse:
        """
        Evaluate a state against typed questions in parallel.
        Returns a SystemOneResponse containing typed answers and confidence scores.
        """
        target_model = model or self.default_model
        t_start = time.perf_counter()

        if self.mode == ClientMode.MOCK:
            if self._mock_handler:
                resp = self._mock_handler(state, questions, target_model)
                dur = (time.perf_counter() - t_start) * 1000.0
                return SystemOneResponse(
                    model=resp.model,
                    answers=resp.answers,
                    usage=resp.usage,
                    latency_ms=dur,
                )
            for k, resp in self._mock_responses.items():
                return resp
            # Default deterministic mock evaluation for test harness runs
            mock_answers: Dict[str, Answer] = {}
            for qid, q in questions.items():
                q_type = getattr(q, "type", "choice")
                if q_type == "choice":
                    criteria = getattr(q, "criteria", {})
                    keys = list(criteria.keys())
                    first_key = keys[0] if keys else "none"
                    probs = {k: (0.91 if k == first_key else round(0.09 / max(1, len(keys) - 1), 3)) for k in keys}
                    mock_answers[qid] = ChoiceAnswer(choice=first_key, probabilities=probs, confidence=0.91)
                elif q_type == "noul":
                    prob = 0.01 if "destructive" in qid else 0.94
                    mock_answers[qid] = NoulAnswer(noul=prob)
                elif q_type == "score":
                    mock_answers[qid] = ScoreAnswer(score=2.0, probabilities={"0": 0.1, "1": 0.9}, confidence=0.88)
            dur = (time.perf_counter() - t_start) * 1000.0
            return SystemOneResponse(
                model=target_model,
                answers=mock_answers,
                usage={"input_tokens": 100, "output_tokens": 0},
                latency_ms=dur or 80.0,
            )

        if self.mode == ClientMode.OFFLINE:
            raise JevServiceUnavailableError(
                "TypeSafe Jev is in OFFLINE mode (no TYPESAFE_API_KEY provided). "
                "Escalating to System 2 / deterministic fallback."
            )

        # LIVE mode
        if not self.api_key:
            raise JevAuthenticationError("TYPESAFE_API_KEY is required for LIVE mode.")

        # Serialize questions
        questions_payload: Dict[str, Any] = {}
        for qid, q in questions.items():
            if hasattr(q, "to_dict"):
                questions_payload[qid] = q.to_dict()
            elif isinstance(q, dict):
                questions_payload[qid] = q
            else:
                raise ValueError(f"Question '{qid}' is of invalid type: {type(q)}")

        payload = {
            "model": target_model,
            "state": state,
            "questions": questions_payload,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "ComexBrowser/1.0",
        }

        try:
            client = await self._get_client()
            res = await client.post(self.endpoint, json=payload, headers=headers)
            latency_ms = (time.perf_counter() - t_start) * 1000.0

            if res.status_code == 401:
                raise JevAuthenticationError(f"Authentication failed: {res.text}")
            if res.status_code >= 500:
                raise JevServiceUnavailableError(f"TypeSafe API server error ({res.status_code}): {res.text}")
            if res.status_code != 200:
                raise JevError(f"TypeSafe API error ({res.status_code}): {res.text}")

            data = res.json()
            parsed_answers: Dict[str, Answer] = {}

            for qid, raw_ans in data.get("answers", {}).items():
                ans_type = raw_ans.get("type")
                if ans_type == "noul":
                    parsed_answers[qid] = NoulAnswer(
                        noul=float(raw_ans.get("noul", 0.0)),
                    )
                elif ans_type == "choice":
                    parsed_answers[qid] = ChoiceAnswer(
                        choice=str(raw_ans.get("choice", "")),
                        probabilities=raw_ans.get("probabilities", {}),
                        confidence=float(raw_ans.get("confidence", 0.0)),
                    )
                elif ans_type == "score":
                    parsed_answers[qid] = ScoreAnswer(
                        score=float(raw_ans.get("score", 0.0)),
                        legend=raw_ans.get("legend"),
                        probabilities=raw_ans.get("probabilities", {}),
                        confidence=float(raw_ans.get("confidence", 0.0)),
                    )
                else:
                    logger.warning(f"Unknown answer type for question '{qid}': {ans_type}")

            return SystemOneResponse(
                model=data.get("model", target_model),
                answers=parsed_answers,
                usage=data.get("usage", {}),
                latency_ms=latency_ms,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if self._http_client and not self._http_client.is_closed:
                try:
                    await self._http_client.aclose()
                except Exception:
                    pass
            self._http_client = None
            raise JevServiceUnavailableError(f"Failed to connect to TypeSafe API: {exc}") from exc


class TypeSafeClient:
    """Synchronous wrapper for AsyncTypeSafeClient."""

    def __init__(self, *args: Any, **kwargs: Any):
        self._async_client = AsyncTypeSafeClient(*args, **kwargs)

    def system_one(
        self,
        state: Union[str, Dict[str, Any], List[Any]],
        questions: Dict[str, Question],
        model: Optional[str] = None,
    ) -> SystemOneResponse:
        import asyncio
        return asyncio.run(self._async_client.system_one(state, questions, model))
