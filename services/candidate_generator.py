"""
Deterministic Candidate Generator and Observation Epoch Tracker.

Responsibilities:
1. Ingests raw interactive DOM elements discovered by CDP.
2. Deterministically filters to actionable, visible candidates.
3. Assigns stable candidate IDs (e1, e2, ... eN) strictly valid for ONE observation epoch.
4. Generates structured multi-head criteria descriptions for Jev Choice questions
   (operation + click_target + fill_target + select_target + wait_reason).
5. Computes compact SHA-256 state fingerprints for robust change detection.
6. Enforces temporal integrity: decisions with mismatched epoch_ids or stale fingerprints
   are rejected fail-closed as STALE_OBSERVATION_EPOCH.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("comex.services.candidates")


@dataclass(frozen=True)
class CandidateElement:
    candidate_id: str  # e.g., "e1", "e2"
    epoch_id: int      # Observation epoch when this candidate was generated
    bid: str           # Underlying BrowserGym / DOM BID
    tag: str           # e.g., "button", "input", "select", "a"
    element_type: str  # e.g., "text", "checkbox", "radio", "submit"
    dom_id: str        # e.g., "flight-from"
    text: str          # Visible label, value, or innerText
    label: str         # Associated <label> text
    options: Optional[List[str]] = None
    value: str = ""
    checked: bool = False
    selected: bool = False
    expanded: Optional[bool] = None
    disabled: bool = False
    focused: bool = False
    center_x: float = 0.0
    center_y: float = 0.0
    class_name: str = ""
    role: str = ""
    name: str = ""
    placeholder: str = ""
    group_context: str = ""
    accessible_name: str = ""

    @property
    def semantic_role(self) -> str:
        """
        Derives the deterministic W3C HTML-AAM effective semantic role
        without altering the raw DOM role attribute.
        """
        if self.role:
            return self.role

        tag = self.tag.lower()
        el_type = self.element_type.lower()

        if tag == "input":
            if el_type in ("button", "submit", "reset", "image"):
                return "button"
            if el_type == "checkbox":
                return "checkbox"
            if el_type == "radio":
                return "radio"
            if el_type == "search":
                return "searchbox"
            if el_type in ("text", "email", "password", "tel", "url", "number", ""):
                return "textbox"
            return "textbox"

        if tag == "textarea":
            return "textbox"

        if tag == "button":
            return "button"

        if tag == "a":
            return "link"

        if tag == "select":
            return "combobox"

        return ""

    @property
    def descriptive_label(self) -> str:
        parts = [f"<{self.tag}>"]
        sem_role = self.semantic_role
        if sem_role:
            parts.append(f"role='{sem_role}'")
        if self.element_type and self.element_type != self.tag:
            parts.append(f"type='{self.element_type}'")
        if self.group_context:
            parts.append(f"group='{self.group_context}'")
        if self.name:
            parts.append(f"name='{self.name}'")
        if self.placeholder:
            parts.append(f"placeholder='{self.placeholder}'")
        if self.label:
            cleaned_label = " ".join(self.label.split())[:50]
            if cleaned_label != self.placeholder:
                parts.append(f"label='{cleaned_label}'")
        if self.text:
            cleaned_text = " ".join(self.text.split())[:50]
            if cleaned_text != self.placeholder and cleaned_text != self.label:
                parts.append(f"'{cleaned_text}'")
        elif not self.label and self.accessible_name:
            cleaned_an = " ".join(self.accessible_name.split())[:50]
            if cleaned_an != self.placeholder:
                parts.append(f"'{cleaned_an}'")
        if self.dom_id:
            parts.append(f"id='{self.dom_id}'")
        if self.selected:
            parts.append("selected='true'")
        if self.checked:
            parts.append("checked='true'")
        return " ".join(parts)

    @property
    def is_editable(self) -> bool:
        """True if the element is an editable text field or textarea."""
        if self.disabled:
            return False
        if self.tag == "textarea":
            return True
        if self.tag == "input":
            non_editable_types = {"button", "submit", "checkbox", "radio", "hidden", "image", "reset"}
            return self.element_type not in non_editable_types
        if self.role in {"textbox", "searchbox"}:
            return True
        return False

    @property
    def is_select(self) -> bool:
        """True if the element is a dropdown select or combobox."""
        if self.disabled:
            return False
        return self.tag == "select" or self.role in {"listbox", "combobox"}

    @property
    def is_clickable(self) -> bool:
        """True if the element represents an actionable clickable target."""
        if self.disabled:
            return False

        # Exclude non-interactive / internal / decorative tags
        if self.tag in {
            "path", "svg", "g", "circle", "rect", "line", "polygon", "polyline",
            "img", "body", "html", "head", "script", "style", "noscript", "iframe",
        }:
            return False

        # Exclude non-interactive ARIA roles
        if self.role in {"presentation", "none", "text", "img", "group", "region", "banner", "main", "navigation"}:
            return False

        # Native interactive controls
        if self.tag in {"button", "summary", "details"}:
            return True

        if self.tag == "a":
            # Anchor must have an ID, text, or label to be actionable
            return bool(self.text or self.label or self.dom_id)

        if self.tag == "input":
            return self.element_type != "hidden"

        # ARIA interactive roles
        CLICKABLE_ROLES = {
            "button", "link", "tab", "menuitem", "menuitemcheckbox",
            "menuitemradio", "checkbox", "radio", "switch", "option", "combobox",
        }
        if self.role in CLICKABLE_ROLES:
            return True

        # Elements with text/label and explicit interactive class/id
        if self.text or self.label:
            cls = self.class_name.lower()
            if (self.dom_id or "btn" in cls or "button" in cls or "action" in cls or "clickable" in cls) and "-view-model" not in self.tag:
                return True

        return False


@dataclass(frozen=True)
class SemanticMicroTask:
    """High-level semantic browser instruction emitted by Gemini System 2."""
    operation: str                         # "CLICK", "FILL", "SELECT"
    target_description: str                # e.g., "Search button", "departure airport field"
    fill_value: Optional[str] = None       # Required if operation == "FILL"
    select_option: Optional[str] = None    # Required if operation == "SELECT"


@dataclass(frozen=True)
class GroundedAction:
    """Concrete micro-action resolved by Jev System 1 against an ObservationSnapshot."""
    operation: str
    target_id: Optional[str]               # e.g., "e7" or None if abstained
    target_element: Optional[CandidateElement]
    option_id: Optional[str] = None        # Option identifier for SELECT
    resolved_option: Optional[str] = None  # Text value of selected option
    confidence: float = 0.0
    margin: float = 0.0                    # Probability delta between top-1 and top-2
    probabilities: Dict[str, float] = field(default_factory=dict)
    epoch_id: int = 0
    abstained: bool = False
    abstain_reason: Optional[str] = None
    decision_type: str = "TARGET"          # "TARGET", "NO_ACTIONABLE_TARGET", or "ERROR"
    resolution_status: str = "RESOLVED"    # "RESOLVED" vs "ABSTAINED"
    confidence_type: str = "TARGET_CONFIDENCE"  # "TARGET_CONFIDENCE" vs "ABSTENTION_CONFIDENCE"
    prerequisite_control: Optional[CandidateElement] = None  # Structured prerequisite affordance (e.g. search trigger for FILL)


@dataclass(frozen=True)
class MultiHeadDecision:
    """Consolidated decision resolved from a multi-head TypeSafe System One query."""
    operation: str                         # "CLICK", "FILL", "SELECT", "SCROLL", "WAIT", "DONE", "ESCALATE"
    target: Optional[CandidateElement]      # Resolved candidate element, or None if operation is non-targeted (e.g. DONE, WAIT)
    target_id: Optional[str]               # e.g., "e1", "NO_ACTIONABLE_TARGET", or None
    wait_reason: Optional[str]             # e.g. "WAIT_FOR_SUGGESTION" if operation == "WAIT"
    confidence: float                      # Primary confidence score
    probabilities: Dict[str, float]
    raw_operation_choice: str
    raw_target_choice: Optional[str]
    epoch_id: int                          # Snapshot epoch id this decision was resolved against


DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT: bool = True


@dataclass
class ObservationSnapshot:
    epoch_id: int
    timestamp_mono: float
    goal: str
    candidates: List[CandidateElement]
    url: str = ""
    scroll_x: int = 0
    scroll_y: int = 0
    focused_snippet: str = ""
    state_fingerprint: str = ""
    by_candidate_id: Dict[str, CandidateElement] = field(default_factory=dict)
    by_bid: Dict[str, CandidateElement] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_candidate_id = {c.candidate_id: c for c in self.candidates}
        self.by_bid = {c.bid: c for c in self.candidates}
        if not self.state_fingerprint:
            self.state_fingerprint = self._compute_fingerprint()

    def _compute_fingerprint(self) -> str:
        """Deterministic SHA-256 fingerprint capturing URL, scroll, snippet, candidates, and UI interaction states.
        Strictly excludes data-comex-id binding attributes to ensure observational purity."""
        candidate_tuples = []
        for c in self.candidates:
            bid_val = "" if DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT else c.bid
            candidate_tuples.append((
                c.candidate_id,
                bid_val,
                c.tag,
                c.element_type,
                c.text,
                c.label,
                c.dom_id,
                c.value,
                c.checked,
                c.expanded,
                c.disabled,
                c.focused,
                round(c.center_x, 1),
                round(c.center_y, 1),
            ))

        content = {
            "url": self.url,
            "scroll": [self.scroll_x, self.scroll_y],
            "focused_snippet": self.focused_snippet[:2000],
            "candidates": candidate_tuples,
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=True).encode("utf-8", errors="replace")).hexdigest()

    def validate_candidate(self, candidate_id: str, caller_epoch_id: int) -> Optional[CandidateElement]:
        """
        Validates candidate existence and guarantees epoch freshness.
        Returns None if caller_epoch_id does not match this snapshot's epoch_id.
        """
        if caller_epoch_id != self.epoch_id:
            logger.warning(
                f"Stale candidate rejected: decision epoch {caller_epoch_id} != snapshot epoch {self.epoch_id}"
            )
            return None
        return self.by_candidate_id.get(candidate_id)

    def is_fresh(self, current_fingerprint: str, caller_epoch_id: int) -> bool:
        """Strict epoch and state-fingerprint freshness verification."""
        if caller_epoch_id != self.epoch_id:
            return False
        if current_fingerprint and current_fingerprint != self.state_fingerprint:
            return False
        return True

    def build_multihead_questions(self, goal: Optional[str] = None) -> Dict[str, Any]:
        """
        Constructs multi-head question schema for TypeSafe System One.
        Bundles operation + target heads in a single network round trip.
        Guarantees NO empty criteria dictionaries by providing explicit 'NO_ACTIONABLE_TARGET'.
        """
        from services.typesafe_client import ChoiceQuestion

        task_goal = goal or self.goal

        # 1. Operation choices
        operation_criteria = {
            "CLICK": "Click an observed button, link, option, checkbox, or radio.",
            "FILL": "Enter or replace text in an observed editable field.",
            "SELECT": "Choose an option in an observed dropdown select.",
            "SCROLL": "Scroll page content to reveal hidden elements.",
            "WAIT": "Wait for page state update (combobox suggestions, navigation, quiescence).",
            "DONE": "Every requirement is visibly satisfied; task is completed.",
            "ESCALATE": "Requires complex reasoning or is unresolvable by local interaction.",
        }

        # 2. Click targets
        click_candidates = [c for c in self.candidates if c.is_clickable]
        if click_candidates:
            click_criteria = {
                c.candidate_id: f"[{c.candidate_id}] {c.descriptive_label}"
                for c in click_candidates
            }
        else:
            click_criteria = {"NO_ACTIONABLE_TARGET": "No clickable elements observed on the page"}

        # 3. Fill targets
        fill_candidates = [c for c in self.candidates if c.is_editable]
        if fill_candidates:
            fill_criteria = {
                c.candidate_id: f"[{c.candidate_id}] {c.descriptive_label}"
                for c in fill_candidates
            }
        else:
            fill_criteria = {"NO_ACTIONABLE_TARGET": "No editable fields observed on the page"}

        # 4. Select targets
        select_candidates = [c for c in self.candidates if c.is_select]
        if select_candidates:
            select_criteria = {
                c.candidate_id: f"[{c.candidate_id}] {c.descriptive_label}"
                for c in select_candidates
            }
        else:
            select_criteria = {"NO_ACTIONABLE_TARGET": "No select dropdowns observed on the page"}

        # 5. Semantic WAIT contract
        wait_criteria = {
            "WAIT_FOR_SUGGESTION": "Wait for dynamic combobox / autocomplete suggestions to appear.",
            "WAIT_FOR_NAVIGATION": "Wait for page navigation / redirection to settle.",
            "WAIT_FOR_QUIESCENCE": "Wait for animations, network requests, or DOM mutations to quiet down.",
            "WAIT_FOR_DIALOG": "Wait for a modal popup or confirmation dialog to render.",
        }

        questions: Dict[str, ChoiceQuestion] = {
            "operation": ChoiceQuestion(
                instructions=f"Advance the goal using one operation: '{task_goal}'",
                criteria=operation_criteria,
            ),
            "click_target": ChoiceQuestion(
                instructions=f"Select the best target element if the operation is CLICK for goal: '{task_goal}'",
                criteria=click_criteria,
            ),
            "fill_target": ChoiceQuestion(
                instructions=f"Select the best target field if the operation is FILL for goal: '{task_goal}'",
                criteria=fill_criteria,
            ),
            "select_target": ChoiceQuestion(
                instructions=f"Select the dropdown target if the operation is SELECT for goal: '{task_goal}'",
                criteria=select_criteria,
            ),
            "wait_reason": ChoiceQuestion(
                instructions=f"Select the semantic wait condition if the operation is WAIT for goal: '{task_goal}'",
                criteria=wait_criteria,
            ),
        }
        return questions

    def resolve_multihead_decision(self, response: Any) -> MultiHeadDecision:
        """
        Consumes strictly the target head matching the selected operation from SystemOneResponse.
        Unused target heads are discarded and cannot cause actions.
        """
        choices = getattr(response, "choices", {})
        op_ans = choices.get("operation")
        if not op_ans:
            raise ValueError("TypeSafe response missing 'operation' answer")

        operation = op_ans.choice
        confidence = op_ans.confidence
        probabilities = op_ans.probabilities

        target_el: Optional[CandidateElement] = None
        target_id: Optional[str] = None
        wait_reason: Optional[str] = None
        raw_target: Optional[str] = None

        if operation == "CLICK":
            click_ans = choices.get("click_target")
            if click_ans:
                raw_target = click_ans.choice
                if raw_target != "NO_ACTIONABLE_TARGET":
                    target_id = raw_target
                    target_el = self.by_candidate_id.get(raw_target)
                    confidence = min(confidence, click_ans.confidence)
                    probabilities = click_ans.probabilities

        elif operation == "FILL":
            fill_ans = choices.get("fill_target")
            if fill_ans:
                raw_target = fill_ans.choice
                if raw_target != "NO_ACTIONABLE_TARGET":
                    target_id = raw_target
                    target_el = self.by_candidate_id.get(raw_target)
                    confidence = min(confidence, fill_ans.confidence)
                    probabilities = fill_ans.probabilities

        elif operation == "SELECT":
            select_ans = choices.get("select_target")
            if select_ans:
                raw_target = select_ans.choice
                if raw_target != "NO_ACTIONABLE_TARGET":
                    target_id = raw_target
                    target_el = self.by_candidate_id.get(raw_target)
                    confidence = min(confidence, select_ans.confidence)
                    probabilities = select_ans.probabilities

        elif operation == "WAIT":
            wait_ans = choices.get("wait_reason")
            if wait_ans:
                wait_reason = wait_ans.choice

        return MultiHeadDecision(
            operation=operation,
            target=target_el,
            target_id=target_id,
            wait_reason=wait_reason,
            confidence=confidence,
            probabilities=probabilities,
            raw_operation_choice=operation,
            raw_target_choice=raw_target,
            epoch_id=self.epoch_id,
        )

    def build_jev_action_criteria(self) -> Dict[str, str]:
        """Legacy flat action criteria mapping (retained for backwards compatibility)."""
        criteria: Dict[str, str] = {}
        for c in self.candidates:
            if c.is_editable:
                criteria[f"TYPE:{c.candidate_id}"] = f"Fill text into {c.descriptive_label}"
            elif c.is_select:
                criteria[f"SELECT:{c.candidate_id}"] = f"Choose option in {c.descriptive_label}"
            else:
                criteria[f"CLICK:{c.candidate_id}"] = f"Click {c.descriptive_label}"
        
        criteria["COMPLETE:done"] = "Task goal is already accomplished; no more actions needed."
        criteria["WAIT:quiescence"] = "Page is currently loading or animating; wait for state update."
        return criteria

    def build_microtask_questions(self, micro_task: SemanticMicroTask) -> Dict[str, Any]:
        """
        Builds targeted Jev Choice question(s) matching Gemini's fixed operation.
        Gemini specifies operation; Jev strictly resolves target and/or select option.
        """
        from services.typesafe_client import ChoiceQuestion

        op = micro_task.operation.upper()
        questions: Dict[str, Any] = {}

        if op in ("CLICK",):
            click_candidates = [c for c in self.candidates if c.is_clickable]
            criteria = {
                c.candidate_id: f"[{c.candidate_id}] {c.descriptive_label}"
                for c in click_candidates
            } if click_candidates else {}
            criteria["NO_ACTIONABLE_TARGET"] = "No candidate matches the target description"
            questions["click_target"] = ChoiceQuestion(
                instructions=f"Select the candidate element matching: '{micro_task.target_description}' for goal: '{self.goal}'",
                criteria=criteria,
            )

        elif op in ("FILL", "TYPE"):
            fill_candidates = [c for c in self.candidates if c.is_editable]
            criteria = {
                c.candidate_id: f"[{c.candidate_id}] {c.descriptive_label}"
                for c in fill_candidates
            } if fill_candidates else {}
            criteria["NO_ACTIONABLE_TARGET"] = "No editable field matches the target description"
            fill_desc = f" to fill with '{micro_task.fill_value}'" if micro_task.fill_value else ""
            questions["fill_target"] = ChoiceQuestion(
                instructions=f"Select the editable input field matching: '{micro_task.target_description}'{fill_desc} for goal: '{self.goal}'",
                criteria=criteria,
            )

        elif op in ("SELECT", "SELECT_OPTION"):
            select_candidates = [c for c in self.candidates if c.is_select]
            criteria = {
                c.candidate_id: f"[{c.candidate_id}] {c.descriptive_label}"
                for c in select_candidates
            } if select_candidates else {}
            criteria["NO_ACTIONABLE_TARGET"] = "No dropdown select matches the target description"
            questions["select_target"] = ChoiceQuestion(
                instructions=f"Select the dropdown element matching: '{micro_task.target_description}' for goal: '{self.goal}'",
                criteria=criteria,
            )

            # Option resolution
            all_options: List[str] = []
            for c in select_candidates:
                if c.options:
                    for opt in c.options:
                        if opt and opt not in all_options:
                            all_options.append(opt)

            if all_options:
                opt_criteria = {
                    f"opt_{i+1}": opt_text for i, opt_text in enumerate(all_options)
                }
                opt_criteria["NO_ACTIONABLE_OPTION"] = "No option matches the requested value"
                req_opt = micro_task.select_option or "the requested value"
                questions["select_option"] = ChoiceQuestion(
                    instructions=f"Select the option matching '{req_opt}'",
                    criteria=opt_criteria,
                )

        return questions

    def resolve_microtask_decision(
        self,
        response: Any,
        micro_task: SemanticMicroTask,
        confidence_threshold: float = 0.70,
        margin_threshold: float = 0.05,
    ) -> GroundedAction:
        """
        Resolves Jev's answer against the specified micro_task and computes margin and abstention.
        """
        op = micro_task.operation.upper()
        choices = getattr(response, "choices", {})

        target_head = "click_target" if op == "CLICK" else ("fill_target" if op in ("FILL", "TYPE") else "select_target")
        ans = choices.get(target_head)

        if not ans:
            return GroundedAction(
                operation=op,
                target_id=None,
                target_element=None,
                confidence=0.0,
                margin=0.0,
                epoch_id=self.epoch_id,
                abstained=True,
                abstain_reason=f"Missing answer for {target_head}",
            )

        raw_choice = ans.choice
        confidence = ans.confidence
        probabilities = ans.probabilities or {}

        # Compute margin between top 1 and top 2
        probs_sorted = sorted(probabilities.values(), reverse=True)
        margin = (probs_sorted[0] - probs_sorted[1]) if len(probs_sorted) > 1 else (probs_sorted[0] if probs_sorted else confidence)

        abstained = False
        abstain_reason = None
        target_el = None
        target_id = None
        decision_type = "TARGET"
        resolution_status = "RESOLVED"
        confidence_type = "TARGET_CONFIDENCE"

        prereq_control = None
        if raw_choice == "NO_ACTIONABLE_TARGET":
            abstained = True
            abstain_reason = "NO_ACTIONABLE_TARGET"
            decision_type = "NO_ACTIONABLE_TARGET"
            resolution_status = "ABSTAINED"
            confidence_type = "ABSTENTION_CONFIDENCE"
            # Generic prerequisite detection: if FILL was requested on a search/input target
            # but no editable fields exist, check if a clickable trigger exists (e.g. search proxy)
            if op in ("FILL", "TYPE") and any(w in micro_task.target_description.lower() for w in ("search", "find", "query", "filter")):
                clickable_triggers = [
                    c for c in self.candidates
                    if c.is_clickable and any(w in c.descriptive_label.lower() for w in ("search", "find", "query"))
                ]
                if clickable_triggers:
                    prereq_control = clickable_triggers[0]
                    abstain_reason = (
                        f"NO_ACTIONABLE_TARGET (requested FILL unavailable; "
                        f"prerequisite clickable trigger available: {prereq_control.candidate_id} '{prereq_control.descriptive_label}')"
                    )
        elif confidence < confidence_threshold:
            abstained = True
            abstain_reason = f"LOW_CONFIDENCE ({confidence:.2f} < {confidence_threshold:.2f})"
            resolution_status = "ABSTAINED"
        elif margin < margin_threshold:
            abstained = True
            abstain_reason = f"LOW_MARGIN ({margin:.2f} < {margin_threshold:.2f})"
            resolution_status = "ABSTAINED"
        else:
            target_id = raw_choice
            target_el = self.by_candidate_id.get(raw_choice)
            if not target_el:
                abstained = True
                abstain_reason = f"UNKNOWN_CANDIDATE_ID ({raw_choice})"
                resolution_status = "ABSTAINED"

        # Handle select_option if present
        option_id: Optional[str] = None
        resolved_option: Optional[str] = None
        if op in ("SELECT", "SELECT_OPTION") and "select_option" in choices:
            opt_ans = choices["select_option"]
            if opt_ans.choice != "NO_ACTIONABLE_OPTION":
                option_id = opt_ans.choice
                resolved_option = opt_ans.choice

        return GroundedAction(
            operation=op,
            target_id=target_id,
            target_element=target_el,
            option_id=option_id,
            resolved_option=resolved_option,
            confidence=confidence,
            margin=margin,
            probabilities=probabilities,
            epoch_id=self.epoch_id,
            abstained=abstained,
            abstain_reason=abstain_reason,
            decision_type=decision_type,
            resolution_status=resolution_status,
            confidence_type=confidence_type,
            prerequisite_control=prereq_control,
        )


class CandidateGenerator:
    """Manages observation epoch increments and generates stable candidate snapshots."""

    def __init__(self) -> None:
        self._epoch_counter: int = 0

    def generate_snapshot(
        self,
        raw_elements: List[Dict[str, Any]],
        goal: str,
        focused_snippet: str = "",
        url: str = "",
        scroll_x: int = 0,
        scroll_y: int = 0,
    ) -> ObservationSnapshot:
        self._epoch_counter += 1
        current_epoch = self._epoch_counter
        t_mono = time.monotonic()

        candidates: List[CandidateElement] = []
        for idx, el in enumerate(raw_elements, start=1):
            cid = f"e{idx}"
            center = el.get("center", {})
            cand = CandidateElement(
                candidate_id=cid,
                epoch_id=current_epoch,
                bid=str(el.get("bid", "")),
                tag=str(el.get("tag", "")).lower(),
                element_type=str(el.get("type", "")).lower(),
                dom_id=str(el.get("id", "")),
                text=str(el.get("text", "")).strip(),
                label=str(el.get("label", "")).strip(),
                options=el.get("options"),
                value=str(el.get("value", "")),
                checked=bool(el.get("checked", False)),
                selected=bool(el.get("selected", False)),
                expanded=el.get("expanded"),
                disabled=bool(el.get("disabled", False)),
                focused=bool(el.get("focused", False)),
                center_x=float(center.get("x", 0.0)),
                center_y=float(center.get("y", 0.0)),
                class_name=str(el.get("className", "")).strip(),
                role=str(el.get("role", "")).lower(),
                name=str(el.get("name", "")).strip(),
                placeholder=str(el.get("placeholder", "")).strip(),
                group_context=str(el.get("group_context", "")).strip(),
                accessible_name=str(el.get("accessible_name", "")).strip(),
            )
            candidates.append(cand)

        return ObservationSnapshot(
            epoch_id=current_epoch,
            timestamp_mono=t_mono,
            goal=goal,
            candidates=candidates,
            url=url,
            scroll_x=scroll_x,
            scroll_y=scroll_y,
            focused_snippet=focused_snippet,
        )
