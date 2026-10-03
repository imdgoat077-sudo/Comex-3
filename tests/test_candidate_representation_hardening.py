"""
tests/test_candidate_representation_hardening.py
==================================================
Generic verification tests for Jev Browser Grounding Hardening:
1. Composite search trigger: div[role=button] is not classified as editable input.
2. Real input after search opens: placeholder/accessible name preserved, distinguishable.
3. Repeated identical text disambiguated by generic group_context.
4. Date carousel / interactive <li>: actionable child elements not pruned.
5. Hidden input + visible label: semantic relationship preserved.
6. Repeated controls with different roles: candidate descriptions remain distinguishable.
7. Ambiguous case without context: Jev correctly abstains, no threshold weakening.
8. Native button/link/input controls remain unchanged and functional.
9. No site-specific selectors, classes, or IDs are used anywhere.
"""

import pytest
from services.universal_extractor import UniversalExtractor
from services.candidate_generator import CandidateGenerator, CandidateElement, SemanticMicroTask
from services.micro_action_resolver import MicroActionResolver, DelegationStatus
from unittest.mock import AsyncMock, MagicMock


def test_1_composite_search_trigger_not_editable():
    """
    Generic test 1: div[role=button] acting as search trigger is clickable,
    NOT classified as editable input.
    """
    html = """
    <div class="search-widget">
        <div role="button" tabindex="0" aria-label="Search items" data-y="20" data-height="40">
            <span>Search items</span>
        </div>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    assert len(elements) >= 1
    cg = CandidateGenerator()
    snap = cg.generate_snapshot(elements, goal="Search for items")
    c = snap.candidates[0]
    assert c.role == "button"
    assert c.is_clickable is True
    assert c.is_editable is False
    assert "role='button'" in c.descriptive_label


def test_2_real_input_after_search_opens():
    """
    Generic test 2: real input with placeholder has correct accessible_name and is_editable=True.
    """
    html = """
    <div class="dialog">
        <input type="text" placeholder="Type here to search" id="search-box" data-y="50" data-height="30">
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    assert len(elements) == 1
    cg = CandidateGenerator()
    snap = cg.generate_snapshot(elements, goal="Search")
    c = snap.candidates[0]
    assert c.is_editable is True
    assert c.placeholder == "Type here to search"
    assert c.dom_id == "search-box"
    assert "placeholder='Type here to search'" in c.descriptive_label


def test_3_repeated_identical_text_disambiguated_by_group_context():
    """
    Generic test 3: Two identical '2D' buttons under different section headings
    get distinct group_context, preventing identical candidate representation.
    """
    html = """
    <section>
        <h3>English</h3>
        <button data-y="20" data-height="30">2D</button>
    </section>
    <section>
        <h3>Spanish</h3>
        <button data-y="60" data-height="30">2D</button>
    </section>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    assert len(elements) == 2
    cg = CandidateGenerator()
    snap = cg.generate_snapshot(elements, goal="Select English 2D")
    c1, c2 = snap.candidates[0], snap.candidates[1]
    assert c1.text == "2D"
    assert c2.text == "2D"
    assert c1.group_context == "English"
    assert c2.group_context == "Spanish"
    assert c1.descriptive_label != c2.descriptive_label
    assert "group='English'" in c1.descriptive_label
    assert "group='Spanish'" in c2.descriptive_label


def test_4_date_carousel_inside_li_not_pruned():
    """
    Generic test 4: Legitimate clickable date items inside <li> in a carousel
    are NOT pruned by composite-card logic.
    """
    html = """
    <ul class="date-carousel">
        <li data-y="10" data-height="40">
            <button data-y="10" data-height="40">Mon 1</button>
        </li>
        <li data-y="10" data-height="40">
            <button data-y="10" data-height="40">Tue 2</button>
        </li>
    </ul>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    assert len(elements) == 2
    cg = CandidateGenerator()
    snap = cg.generate_snapshot(elements, goal="Select Mon 1")
    assert len(snap.candidates) == 2
    labels = [c.descriptive_label for c in snap.candidates]
    assert any("Mon 1" in l for l in labels)
    assert any("Tue 2" in l for l in labels)


def test_5_hidden_input_plus_visible_label():
    """
    Generic test 5: Semantic label associated with an input preserves label text.
    """
    html = """
    <form>
        <label for="newsletter-email">Email Address</label>
        <input type="email" id="newsletter-email" placeholder="you@domain.com" data-y="10" data-height="30">
    </form>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    assert len(elements) == 1
    cg = CandidateGenerator()
    snap = cg.generate_snapshot(elements, goal="Subscribe")
    c = snap.candidates[0]
    assert c.is_editable is True
    assert c.label == "Email Address"
    assert "label='Email Address'" in c.descriptive_label


def test_6_repeated_controls_different_roles_remain_distinguishable():
    """
    Generic test 6: Elements with the same text but different roles are distinguishable.
    """
    html = """
    <div>
        <a href="/settings" data-y="10" data-height="30">Settings</a>
        <button data-y="50" data-height="30">Settings</button>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    assert len(elements) == 2
    cg = CandidateGenerator()
    snap = cg.generate_snapshot(elements, goal="Go to settings")
    c1, c2 = snap.candidates[0], snap.candidates[1]
    assert c1.descriptive_label != c2.descriptive_label
    assert "<a>" in c1.descriptive_label
    assert "<button>" in c2.descriptive_label


@pytest.mark.asyncio
async def test_7_ambiguous_case_without_context_still_abstains():
    """
    Generic test 7: When two candidates are genuinely identical and lack context,
    Jev splits probabilities and abstains (LOW_CONFIDENCE / LOW_MARGIN).
    Confidence thresholds must not be weakened.
    """
    cg = CandidateGenerator()
    c1 = CandidateElement(
        candidate_id="e1", epoch_id=1, bid="c1", tag="button", element_type="button",
        dom_id="", text="Select", label="", group_context=""
    )
    c2 = CandidateElement(
        candidate_id="e2", epoch_id=1, bid="c2", tag="button", element_type="button",
        dom_id="", text="Select", label="", group_context=""
    )
    snap = cg.generate_snapshot([], goal="Select option")
    snap.candidates = [c1, c2]
    snap.__post_init__()

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    # Ambiguous split: confidence 0.50 (below 0.70 threshold)
    mock_choice.choice = "e1"
    mock_choice.confidence = 0.50
    mock_choice.probabilities = {"e1": 0.50, "e2": 0.50}
    mock_resp.choices = {"click_target": mock_choice}
    mock_client.system_one = AsyncMock(return_value=mock_resp)

    resolver = MicroActionResolver(client=mock_client)
    res = await resolver.resolve(snap, SemanticMicroTask(operation="CLICK", target_description="Select"))
    assert res.status == DelegationStatus.ABSTAINED_LOW_CONFIDENCE
    assert res.can_delegate is False


def test_8_native_controls_remain_functional():
    """
    Generic test 8: Standard native button, input, select, link remain fully functional.
    """
    html = """
    <div>
        <button data-y="10" data-height="30">Submit Form</button>
        <input type="text" id="username" placeholder="Username" data-y="50" data-height="30">
        <select id="country" data-y="90" data-height="30">
            <option>USA</option>
            <option>Canada</option>
        </select>
        <a href="/help" data-y="130" data-height="30">Help Link</a>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    assert len(elements) == 4
    cg = CandidateGenerator()
    snap = cg.generate_snapshot(elements, goal="Fill form")
    btn = [c for c in snap.candidates if c.tag == "button"][0]
    inp = [c for c in snap.candidates if c.tag == "input"][0]
    sel = [c for c in snap.candidates if c.tag == "select"][0]
    lnk = [c for c in snap.candidates if c.tag == "a"][0]

    assert btn.is_clickable is True
    assert inp.is_editable is True
    assert sel.is_select is True
    assert lnk.is_clickable is True


def test_9_aria_labelledby_multi_id_support():
    """
    Generic test 9: aria-labelledby with multiple space-separated IDs resolves correctly.
    """
    html = """
    <div>
        <span id="prefix">Flight to</span>
        <span id="dest">Paris</span>
        <button aria-labelledby="prefix dest" data-y="10" data-height="30">Details</button>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    assert len(elements) >= 1
    btn = [e for e in elements if e.get("tag") == "button"][0]
    assert "Flight to Paris" in btn.get("accessible_name", "")


def test_10_no_site_specific_assumptions():
    """
    Generic test 10: Verify no BookMyShow, date regex, or hardcoded ID assumptions
    exist in the extraction or candidate generation source code.
    """
    import inspect
    import services.universal_extractor as ue
    import services.candidate_generator as cg

    ue_src = inspect.getsource(ue)
    cg_src = inspect.getsource(cg)

    forbidden = ["bookmyshow", "bmscdn", "202", "the-paradise", "fdfs"]
    for word in forbidden:
        assert word not in ue_src.lower(), f"Forbidden site-specific word '{word}' found in universal_extractor.py"
        assert word not in cg_src.lower(), f"Forbidden site-specific word '{word}' found in candidate_generator.py"


def test_11_effective_semantic_roles_for_native_controls():
    """
    Generic test 11: Verify that implicit/effective semantic roles are accurately
    derived for standard HTML controls without overwriting raw DOM role:
    1. input[type=text] -> textbox
    2. input[type=search] -> searchbox
    3. textarea -> textbox
    4. button -> button
    5. input[type=checkbox] -> checkbox, input[type=radio] -> radio
    6. select -> combobox
    7. native link (a) -> link
    """
    cg = CandidateGenerator()
    c_text = CandidateElement(candidate_id="e1", epoch_id=1, bid="c1", tag="input", element_type="text", dom_id="", text="", label="", role="")
    c_search = CandidateElement(candidate_id="e2", epoch_id=1, bid="c2", tag="input", element_type="search", dom_id="", text="", label="", role="")
    c_textarea = CandidateElement(candidate_id="e3", epoch_id=1, bid="c3", tag="textarea", element_type="", dom_id="", text="", label="", role="")
    c_button = CandidateElement(candidate_id="e4", epoch_id=1, bid="c4", tag="button", element_type="submit", dom_id="", text="Go", label="", role="")
    c_chk = CandidateElement(candidate_id="e5", epoch_id=1, bid="c5", tag="input", element_type="checkbox", dom_id="", text="", label="Agree", role="")
    c_rad = CandidateElement(candidate_id="e6", epoch_id=1, bid="c6", tag="input", element_type="radio", dom_id="", text="", label="Opt1", role="")
    c_sel = CandidateElement(candidate_id="e7", epoch_id=1, bid="c7", tag="select", element_type="", dom_id="", text="", label="Choice", role="")
    c_lnk = CandidateElement(candidate_id="e8", epoch_id=1, bid="c8", tag="a", element_type="", dom_id="", text="Home", label="", role="")

    # Verify raw role is preserved as empty
    assert c_text.role == ""
    assert c_search.role == ""
    assert c_textarea.role == ""
    assert c_button.role == ""
    assert c_chk.role == ""
    assert c_rad.role == ""
    assert c_sel.role == ""
    assert c_lnk.role == ""

    # Verify semantic_role is accurately derived
    assert c_text.semantic_role == "textbox"
    assert c_search.semantic_role == "searchbox"
    assert c_textarea.semantic_role == "textbox"
    assert c_button.semantic_role == "button"
    assert c_chk.semantic_role == "checkbox"
    assert c_rad.semantic_role == "radio"
    assert c_sel.semantic_role == "combobox"
    assert c_lnk.semantic_role == "link"

    # Verify descriptive_label surfaces the semantic role
    assert "role='textbox'" in c_text.descriptive_label
    assert "role='searchbox'" in c_search.descriptive_label
    assert "role='textbox'" in c_textarea.descriptive_label
    assert "role='button'" in c_button.descriptive_label
    assert "role='checkbox'" in c_chk.descriptive_label
    assert "role='radio'" in c_rad.descriptive_label
    assert "role='combobox'" in c_sel.descriptive_label
    assert "role='link'" in c_lnk.descriptive_label


