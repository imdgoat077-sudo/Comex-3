"""
services/progress_controller.py
================================
Commit C: Deterministic-first subtask completeness judge.

Four-layer architecture:
  Semantic (Jev) → Safety (Comex) → Physical (Actuator) → Task (ProgressController)

This layer answers: "Did this action advance the user's task?"

Design invariants:
  - state_changed != COMPLETE
  - dispatch_success != COMPLETE
  - URL change alone != COMPLETE (no URL-based heuristics in Commit C)
  - FAILED status not used in Commit C (Commit B LoopBreaker handles physical stalls)
  - sibling_failures kept as empty slot — populated in Commit D
  - Tier-2 Gemini call is bounded (asyncio.wait_for 3s), never blocks the action loop
  - Reflection is edge-triggered at attempts==3, not repeated every turn
  - task_complete flag triggers hard stop via agent_service.py loop check
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger("comex.services.progress_controller")

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "to", "for", "in", "on", "at", "by", "from",
    "with", "into", "onto", "of", "off", "out", "then", "next", "after", "is", "it",
}


@dataclass
class SiblingFailure:
    """Commit D slot — always empty in Commit C."""
    candidate_id: str
    target_description: str
    failure_reason: str  # "OCCLUDED" | "COORDINATE_MISMATCH" | "ZERO_DELTA" | "ABSTAINED"
    state_fingerprint: str  # scope key: failure expires when fingerprint changes


@dataclass
class SubtaskRecord:
    index: int
    description: str
    attempts: int = 0
    status: str = "ACTIVE"  # "ACTIVE" | "COMPLETE"  (no FAILED in Commit C)
    action_history: List[Dict[str, Any]] = field(default_factory=list)
    reflection_injected: bool = False  # edge-triggered: only once per subtask
    sibling_failures: List[SiblingFailure] = field(default_factory=list)  # Commit D slot
    expected_url: Optional[str] = None  # explicit expected destination URL or path
    expected_milestone: Optional[str] = None  # explicit milestone label
    completion_evidence: str = ""  # exact evidence string that justified COMPLETE


@dataclass
class SubtaskPlan:
    task: str
    subtasks: List[str]


class ProgressController:
    """
    Deterministic-first subtask completeness judge.

    Public API:
        decompose_task(task) -> SubtaskPlan         # 1 Gemini call at task start
        record_action(subtask_idx, result: dict)    # pure update, no I/O
        assess_tier1(subtask_idx, act_info, current_url) -> "COMPLETE"|"INCOMPLETE"|"UNKNOWN"
        assess_tier2_bounded(subtask_idx, page_summary, timeout_s=3.0)
            -> "COMPLETE"|"INCOMPLETE"|"UNKNOWN"    # bounded fail-safe (awaited, capped)
        get_strategic_reflection(subtask_idx) -> str
        should_call_tier2(subtask_idx) -> bool      # True at attempts % 3 == 0
        should_inject_reflection(subtask_idx) -> bool  # True only at attempts == 3 (edge)
        advance_to_next() -> int | None             # returns new index or None if all done
        current_subtask() -> SubtaskRecord | None
        task_complete: bool
    """

    def __init__(self, llm: Any = None) -> None:
        self._llm = llm
        self._subtasks: List[SubtaskRecord] = []
        self._active_idx: int = 0
        self._decompose_time_s: float = 0.0
        self._tier2_calls: int = 0
        self._tier2_time_s: float = 0.0

    # ── Decomposition ──────────────────────────────────────────────────────────

    async def decompose_task(self, task: str) -> SubtaskPlan:
        """
        One Gemini call at task start. Falls back to single-subtask mode on failure.
        1–7 subtasks (not 3–7 — simple tasks get 1).
        """
        if not self._llm:
            self._subtasks = [SubtaskRecord(index=0, description=task)]
            return SubtaskPlan(task=task, subtasks=[task])

        prompt = (
            f'Given the browser task: "{task}"\n'
            "Break this into sequential subtasks an agent must complete in order.\n"
            "Rules:\n"
            "- If the task is a simple one-step action (single search, single navigation), output 1 subtask.\n"
            "- If the task is a multi-stage transactional workflow (booking, checkout, form submission), "
            "output one subtask per meaningful stage (navigate, search/find, select item, choose options, proceed to payment). "
            "Use 3 to 7 subtasks for multi-stage workflows.\n"
            "- Do NOT split trivial sub-steps into separate subtasks.\n"
            "Output ONLY a JSON array of short action phrase strings, nothing else.\n"
            'Example multi-stage: ["Navigate to BookMyShow", "Search for Paradise cinema", '
            '"Select FDFS Saturday show", "Choose seats", "Proceed to payment"]\n'
            'Example simple: ["Search for iPhone on Amazon"]'
        )

        t0 = time.perf_counter()
        try:
            from langchain_core.messages import HumanMessage
            resp = await asyncio.wait_for(
                self._llm.ainvoke([HumanMessage(content=prompt)]),
                timeout=45.0,
            )
            raw = (resp.content or "").strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            subtask_list = json.loads(raw)
            if not isinstance(subtask_list, list) or not subtask_list:
                raise ValueError("Empty or non-list response")
            subtask_list = [str(s).strip() for s in subtask_list if str(s).strip()][:7]
        except Exception as e:
            logger.warning(f"[ProgressController] decompose_task fallback (single subtask): {e}")
            subtask_list = [task]
        finally:
            self._decompose_time_s = time.perf_counter() - t0

        self._subtasks = []
        for i, desc in enumerate(subtask_list):
            expected_url = None
            m = re.search(r"(?:navigate\s+to|go\s+to|open)\s+([a-zA-Z0-9_\-\.]+)", desc, re.IGNORECASE)
            if m:
                dest = m.group(1).lower().replace("website", "").replace("site", "").replace("app", "").replace("page", "").strip()
                if len(dest) >= 3 and dest not in _STOP_WORDS:
                    expected_url = dest
            self._subtasks.append(SubtaskRecord(index=i, description=desc, expected_url=expected_url))
        self._active_idx = 0
        logger.info(
            f"[ProgressController] Decomposed '{task[:60]}' into {len(self._subtasks)} subtasks: "
            + str([s.description for s in self._subtasks])
        )
        return SubtaskPlan(task=task, subtasks=subtask_list)

    # ── Record ─────────────────────────────────────────────────────────────────

    def record_action(self, subtask_idx: int, result: dict) -> None:
        """Record action result for the given subtask. Pure update, no I/O."""
        rec = self._get(subtask_idx)
        if rec is None or rec.status == "COMPLETE":
            return
        rec.attempts += 1
        rec.action_history.append({
            "attempt": rec.attempts,
            "dispatch_success": result.get("dispatch_success", False),
            "state_changed": result.get("state_changed", False),
            "postcondition": result.get("postcondition", "UNKNOWN"),
            "progress": result.get("progress", "UNKNOWN"),
            "status": result.get("status", ""),
        })

    # ── Tier 1 (deterministic) ─────────────────────────────────────────────────

    def _evaluate_generic_milestone(
        self,
        rec: SubtaskRecord,
        act_info: dict,
        current_url: str,
    ) -> Optional[str]:
        """
        Evaluates whether act_info satisfies a generic task milestone for rec.description.
        Returns the exact justification evidence string if satisfied, or None.

        Invariants enforced:
          - dispatch_success must be True
          - state_changed alone NEVER produces COMPLETE
          - dispatch_success alone NEVER produces COMPLETE
          - Ambiguous actions return None (triggering Tier 1 UNKNOWN)
        """
        dispatch_success = bool(act_info.get("dispatch_success", False))
        if not dispatch_success:
            return None

        subtask_desc = rec.description.lower()
        op = str(act_info.get("operation", "")).upper()
        target_desc = str(act_info.get("target_description", "")).lower()
        details = str(act_info.get("details", "")).lower()
        state_changed = bool(act_info.get("state_changed", False))

        # 1. Explicit milestone expectation configured on subtask record
        if rec.expected_milestone and rec.expected_milestone.lower() in target_desc:
            if state_changed or op == "FILL":
                return f"Explicit milestone '{rec.expected_milestone}' observed on target '{target_desc}'"

        # 2. Navigation milestone with explicit expected_url
        if rec.expected_url:
            parsed = urllib.parse.urlparse(current_url.lower())
            netloc_and_path = (parsed.netloc + parsed.path).lower()
            if rec.expected_url.lower() in netloc_and_path:
                return f"Navigation milestone: reached expected URL '{rec.expected_url}' in '{current_url}'"

        subtask_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-\.]+\b", subtask_desc))
        target_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-\.]+\b", target_desc))
        meaningful_subtask_tokens = {
            t for t in subtask_tokens
            if len(t) >= 3 and t not in _STOP_WORDS
        }

        # 3. Fill / Input Milestone
        fill_verbs = {"fill", "enter", "type", "input", "write", "provide"}
        if any(v in subtask_tokens for v in fill_verbs):
            if op == "FILL" or "fill" in details:
                fill_entities = meaningful_subtask_tokens - fill_verbs - {"field", "input", "box", "text", "form"}
                if fill_entities and (fill_entities & target_tokens or any(fe in target_desc for fe in fill_entities)):
                    return f"Fill milestone: confirmed atomic FILL on target '{target_desc}' matching subtask field"

        # 4. Selection Milestone (state_changed is mandatory)
        select_verbs = {"select", "choose", "pick", "check", "opt"}
        if any(v in subtask_tokens for v in select_verbs):
            if op in ("CLICK", "SELECT") and state_changed:
                select_entities = (
                    meaningful_subtask_tokens
                    - select_verbs
                    - {"option", "item", "button", "tab", "choice", "slot", "link", "radio", "checkbox"}
                )
                if select_entities and (select_entities & target_tokens or any(se in target_desc for se in select_entities)):
                    return f"Selection milestone: confirmed {op} on target '{target_desc}' matching subtask entity with state change"

        # 5. Search / Query Milestone (state_changed is mandatory)
        search_verbs = {"search", "query", "find", "lookup"}
        if any(v in subtask_tokens for v in search_verbs):
            if state_changed:
                search_submit_markers = {"submit", "magnif", "search button", "search icon", "go button", "search btn"}
                is_submit_button = any(sm in target_desc for sm in search_submit_markers) or (
                    "search" in target_desc and any(b in target_desc for b in ("button", "btn", "icon", "submit"))
                )
                # Ensure clicking a pure search input/box does NOT count as submission
                if op == "CLICK" and is_submit_button:
                    return f"Search submission milestone: confirmed search submission on '{target_desc}' with state change"

                # Check if a matching search result / suggestion was selected
                query_entities = meaningful_subtask_tokens - search_verbs - {"for", "result", "results", "item", "items", "cinema", "movie", "page"}
                if op == "CLICK" and query_entities and any(qe in target_desc for qe in query_entities):
                    is_input_box = any(ib in target_desc for ib in ("input", "box", "field", "search bar", "textbox"))
                    if not is_input_box:
                        return f"Search result milestone: selected result '{target_desc}' matching query with state change"

        # 6. Modal / Overlay / Transition Milestone (state_changed is mandatory)
        modal_verbs = {"close", "dismiss", "accept", "proceed", "continue", "agree", "confirm"}
        modal_nouns = {"modal", "dialog", "popup", "cookie", "banner", "terms", "prompt", "alert"}
        if any(v in subtask_tokens for v in modal_verbs) and any(n in subtask_tokens for n in modal_nouns):
            if op == "CLICK" and state_changed:
                if any(mv in target_desc for mv in modal_verbs) or any(mn in target_desc for mn in modal_nouns):
                    return f"Modal transition milestone: confirmed {op} on '{target_desc}' for modal transition with state change"

        return None

    def assess_tier1(
        self,
        subtask_idx: int,
        act_info: dict,
        current_url: str,
    ) -> Literal["COMPLETE", "INCOMPLETE", "UNKNOWN"]:
        """
        Deterministic completeness assessment.
        Evaluates active subtask against Commit B action result, bounded browser evidence,
        and generic task milestones.

        Evidence hierarchy:
          1. Explicit Commit B postcondition MET + progress POSITIVE → COMPLETE
          2. Generic task milestone satisfied (Navigation, Fill, Selection, Search, Modal) → COMPLETE
          3. dispatch_success==False AND state_changed==False → INCOMPLETE
          4. Otherwise (including state_changed alone or ambiguous action) → UNKNOWN

        Invariants:
          - state_changed alone NEVER produces COMPLETE
          - dispatch_success alone NEVER produces COMPLETE
          - Ambiguous actions return UNKNOWN to allow bounded Tier-2 evaluation
        """
        rec = self._get(subtask_idx)
        if rec is None or rec.status == "COMPLETE":
            return "COMPLETE"

        postcondition = act_info.get("postcondition", "UNKNOWN")
        progress = act_info.get("progress", "UNKNOWN")
        dispatch_success = act_info.get("dispatch_success", False)
        state_changed = act_info.get("state_changed", False)

        # 1. Authoritative Commit B explicit positive evidence
        if postcondition == "MET" and progress == "POSITIVE":
            evidence = "Authoritative Commit B: postcondition MET + progress POSITIVE"
            rec.completion_evidence = evidence
            logger.info(f"[ProgressController] Subtask {subtask_idx} COMPLETE ({evidence})")
            return "COMPLETE"

        # 2. Generic task-aware milestone evaluation
        milestone_evidence = self._evaluate_generic_milestone(rec, act_info, current_url)
        if milestone_evidence:
            rec.completion_evidence = milestone_evidence
            logger.info(f"[ProgressController] Subtask {subtask_idx} COMPLETE ({milestone_evidence})")
            return "COMPLETE"

        # 3. Explicit physical failure signal
        if not dispatch_success and not state_changed:
            return "INCOMPLETE"

        # 4. State changed alone, or dispatch succeeded but milestone ambiguous → Tier 2
        return "UNKNOWN"

    # ── Tier 2 (bounded Gemini) ────────────────────────────────────────────────

    async def assess_tier2_bounded(
        self,
        subtask_idx: int,
        page_summary: str,
        timeout_s: float = 3.0,
    ) -> Literal["COMPLETE", "INCOMPLETE", "UNKNOWN"]:
        """
        Bounded fail-safe Gemini completeness check.
        Returns "UNKNOWN" on timeout or any error — never blocks the action loop.
        """
        rec = self._get(subtask_idx)
        if rec is None or not self._llm:
            return "UNKNOWN"

        t0 = time.perf_counter()
        self._tier2_calls += 1
        prompt = (
            f'Subtask: "{rec.description}"\n'
            f"Current page: {page_summary[:400]}\n"
            "Is this subtask COMPLETE or INCOMPLETE? "
            "Reply with exactly one word: COMPLETE, INCOMPLETE, or UNKNOWN."
        )
        try:
            from langchain_core.messages import HumanMessage
            resp = await asyncio.wait_for(
                self._llm.ainvoke([HumanMessage(content=prompt)]),
                timeout=timeout_s,
            )
            raw = (resp.content or "").strip().upper().split()[0]
            result: Literal["COMPLETE", "INCOMPLETE", "UNKNOWN"] = (
                "COMPLETE" if raw == "COMPLETE"
                else "INCOMPLETE" if raw == "INCOMPLETE"
                else "UNKNOWN"
            )
            logger.info(f"[ProgressController] Tier-2 subtask {subtask_idx}: {result}")
            return result
        except Exception as e:
            logger.debug(f"[ProgressController] Tier-2 timeout/error for subtask {subtask_idx}: {e}")
            return "UNKNOWN"
        finally:
            self._tier2_time_s += time.perf_counter() - t0

    # ── Rate-limit / gate helpers ──────────────────────────────────────────────

    def should_call_tier2(self, subtask_idx: int) -> bool:
        """True at attempts % 3 == 0 (i.e. attempt 3, 6, 9). Never called before first attempt."""
        rec = self._get(subtask_idx)
        if rec is None or rec.attempts == 0:
            return False
        return rec.attempts % 3 == 0

    def should_inject_reflection(self, subtask_idx: int) -> bool:
        """
        Edge-triggered: True only when attempts == 3 AND reflection not yet injected
        for this subtask. Prevents repeated context pollution.
        """
        rec = self._get(subtask_idx)
        if rec is None or rec.reflection_injected:
            return False
        return rec.attempts == 3

    def get_strategic_reflection(self, subtask_idx: int) -> str:
        """
        Returns a brief structured reflection string for injection into the Gemini turn.
        Does NOT reference candidate-level failures (that is Commit D).
        Marks reflection as injected so it is not repeated.
        """
        rec = self._get(subtask_idx)
        if rec is None:
            return ""
        rec.reflection_injected = True
        outcomes = [
            f"attempt {h['attempt']}: dispatch={'OK' if h['dispatch_success'] else 'FAIL'} "
            f"state_changed={h['state_changed']} postcondition={h['postcondition']}"
            for h in rec.action_history[-3:]
        ]
        return (
            f"Subtask {subtask_idx + 1} '{rec.description}' has {rec.attempts} attempts. "
            f"Recent outcomes: {'; '.join(outcomes) or 'none'}. "
            "Consider whether the target element is visible, offscreen, or needs a different approach."
        )

    # ── Advancement ───────────────────────────────────────────────────────────

    def advance_to_next(self) -> Optional[int]:
        """
        Mark current subtask COMPLETE and advance index.
        Returns the new active index, or None if all subtasks are done.
        """
        rec = self._get(self._active_idx)
        if rec is not None:
            rec.status = "COMPLETE"
            logger.info(f"[ProgressController] Subtask {self._active_idx} marked COMPLETE")
        self._active_idx += 1
        if self._active_idx >= len(self._subtasks):
            logger.info("[ProgressController] All subtasks COMPLETE — task_complete=True")
            return None
        return self._active_idx

    def current_subtask(self) -> Optional[SubtaskRecord]:
        return self._get(self._active_idx)

    @property
    def active_subtask_index(self) -> int:
        return self._active_idx

    @property
    def task_complete(self) -> bool:
        return self._active_idx >= len(self._subtasks) and len(self._subtasks) > 0

    # ── Telemetry ─────────────────────────────────────────────────────────────

    def telemetry(self) -> Dict[str, Any]:
        return {
            "progress_controller_subtask_count": len(self._subtasks),
            "progress_controller_active_idx": self._active_idx,
            "progress_controller_decompose_time_s": round(self._decompose_time_s, 3),
            "progress_controller_tier2_calls": self._tier2_calls,
            "progress_controller_tier2_time_s": round(self._tier2_time_s, 3),
            "progress_controller_task_complete": self.task_complete,
            "progress_controller_subtasks": [
                {
                    "index": s.index,
                    "description": s.description,
                    "status": s.status,
                    "attempts": s.attempts,
                    "completion_evidence": s.completion_evidence,
                }
                for s in self._subtasks
            ],
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, idx: int) -> Optional[SubtaskRecord]:
        if 0 <= idx < len(self._subtasks):
            return self._subtasks[idx]
        return None
