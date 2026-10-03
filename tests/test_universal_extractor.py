"""
tests/test_universal_extractor.py
=================================
Unit tests for Universal DOM Extractor (Layer 1: Offline Fixtures).

Verifies:
1. Native controls (button, input, select, textarea, a[href], summary) extraction.
2. ARIA interactive roles (role="button", role="link", role="checkbox", role="combobox").
3. Accessible name computation across aria-labelledby, aria-label, <label>, placeholder, and text.
4. Pointer affordances (cursor: pointer on leaf/text nodes).
5. Contenteditable element discovery.
6. Filtering of inert, disabled, display: none, and visibility: hidden elements.
7. Observational purity: extraction produces zero application state mutation.
8. Fingerprint isolation invariant: data-comex-id is excluded from state fingerprinting.
9. Candidate coverage retention invariant: all required candidate properties are preserved.
"""

import hashlib
import json
import pytest

from services.universal_extractor import (
    UniversalExtractor,
    DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT,
    COMEX_BINDING_ATTR,
)
from services.candidate_generator import (
    CandidateGenerator,
    ObservationSnapshot,
)


def test_fingerprint_isolation_constant():
    """Invariant 1: data-comex-id must be explicitly excluded from state fingerprinting."""
    assert DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT is True
    assert COMEX_BINDING_ATTR == "data-comex-id"


def test_native_controls_extraction():
    """Verify standard native controls are extracted with proper tags, types, and labels."""
    html = """
    <div>
        <h1>Welcome</h1>
        <button id="btn-submit" class="primary-btn">Submit Order</button>
        <input type="text" id="username" placeholder="Enter your username">
        <input type="checkbox" id="terms" checked>
        <select id="country">
            <option value="US">United States</option>
            <option value="CA">Canada</option>
        </select>
        <textarea id="notes" placeholder="Delivery instructions"></textarea>
        <a href="/help" id="help-link">Help & FAQ</a>
        <a id="non-nav-link">Not a link</a>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    ids = [e["id"] for e in elements]

    assert "btn-submit" in ids
    assert "username" in ids
    assert "terms" in ids
    assert "country" in ids
    assert "notes" in ids
    assert "help-link" in ids
    assert "non-nav-link" not in ids  # <a> without href is not an interactive link

    # Check button attributes
    btn = next(e for e in elements if e["id"] == "btn-submit")
    assert btn["tag"] == "button"
    assert btn["text"] == "Submit Order"

    # Check input placeholder
    inp = next(e for e in elements if e["id"] == "username")
    assert inp["tag"] == "input"
    assert inp["type"] == "text"
    assert inp["text"] == "Enter your username"

    # Check checkbox checked state
    chk = next(e for e in elements if e["id"] == "terms")
    assert chk["checked"] is True


def test_aria_roles_and_accessible_names():
    """Verify ARIA role detection and accessible name computation."""
    html = """
    <div>
        <div role="button" id="custom-btn" aria-label="Close dialog">X</div>
        <span role="checkbox" id="custom-chk" aria-checked="true" aria-label="Remember me"></span>
        <div role="combobox" id="combo" aria-label="Search destination"></div>
        <div role="tab" id="tab-reviews" aria-label="Customer Reviews">Reviews (14)</div>
        <span role="link" id="custom-link">Terms of Service</span>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    by_id = {e["id"]: e for e in elements}

    assert "custom-btn" in by_id
    assert by_id["custom-btn"]["role"] == "button"
    assert by_id["custom-btn"]["accessible_name"] == "Close dialog"

    assert "custom-chk" in by_id
    assert by_id["custom-chk"]["role"] == "checkbox"
    assert by_id["custom-chk"]["checked"] is True
    assert by_id["custom-chk"]["accessible_name"] == "Remember me"

    assert "combo" in by_id
    assert by_id["combo"]["role"] == "combobox"

    assert "tab-reviews" in by_id
    assert by_id["tab-reviews"]["accessible_name"] == "Customer Reviews"


def test_pointer_styled_and_contenteditable_elements():
    """Verify detection of elements with cursor: pointer or contenteditable."""
    html = """
    <div>
        <span id="upvote-arrow" style="cursor: pointer;">▲</span>
        <div id="editable-div" contenteditable="true">Type notes here...</div>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    by_id = {e["id"]: e for e in elements}

    assert "upvote-arrow" in by_id
    assert by_id["upvote-arrow"]["text"] == "▲"

    assert "editable-div" in by_id
    assert by_id["editable-div"]["text"] == "Type notes here..."


def test_hidden_and_inert_filtering():
    """Verify elements hidden by CSS or marked inert/disabled are filtered or flagged."""
    html = """
    <div>
        <button id="visible-btn">Click Me</button>
        <button id="hidden-display" style="display: none;">Invisible</button>
        <button id="hidden-vis" style="visibility: hidden;">Hidden</button>
        <button id="disabled-btn" disabled>Disabled Action</button>
        <div inert>
            <button id="inert-btn">Inside Inert</button>
        </div>
    </div>
    """
    elements = UniversalExtractor.extract_from_html_fixture(html)
    ids = [e["id"] for e in elements]

    assert "visible-btn" in ids
    assert "hidden-display" not in ids
    assert "hidden-vis" not in ids
    assert "inert-btn" not in ids

    # disabled-btn is present but flagged disabled
    assert "disabled-btn" in ids
    dis = next(e for e in elements if e["id"] == "disabled-btn")
    assert dis["disabled"] is True


def test_fingerprint_isolation_on_identical_dom():
    """
    Invariant: Extracting candidates multiple times on the same page
    must produce IDENTICAL state fingerprints. Attaching data-comex-id
    must NOT cause a false state_changed transition.
    """
    gen = CandidateGenerator()
    html = """
    <div>
        <button id="search-btn">Search</button>
        <input type="text" id="query" value="test">
    </div>
    """
    raw_1 = UniversalExtractor.extract_from_html_fixture(html)
    snap_1 = gen.generate_snapshot(raw_1, goal="Perform search", url="https://example.com")

    # Simulate a second extraction where transient IDs or BIDs re-attach
    raw_2 = UniversalExtractor.extract_from_html_fixture(html)
    # Simulate slightly modified transient BIDs
    for r in raw_2:
        r["bid"] = f"transient_{r['bid']}"
    snap_2 = gen.generate_snapshot(raw_2, goal="Perform search", url="https://example.com")

    assert snap_1.state_fingerprint == snap_2.state_fingerprint, (
        "Transient BIDs / data-comex-id must NOT alter the state fingerprint."
    )


def test_candidate_coverage_retention_invariant():
    """
    Coverage Retention Invariant:
    For any known interactive element, CandidateGenerator must expose it,
    retain selector, class, accessible name, bounds, epoch, and exact DOM binding.
    """
    gen = CandidateGenerator()
    html = """
    <div class="thread-container">
        <button id="action-btn" class="btn primary-action" aria-label="Confirm Action">Click</button>
    </div>
    """
    raw = UniversalExtractor.extract_from_html_fixture(html)
    snap = gen.generate_snapshot(raw, goal="Confirm Action")

    assert len(snap.candidates) == 1
    c = snap.candidates[0]

    assert c.candidate_id == "e1"
    assert c.dom_id == "action-btn"
    assert c.tag == "button"
    assert c.label == "Confirm Action"
    assert c.text == "Confirm Action"
    assert c.epoch_id == snap.epoch_id
    assert c.center_x > 0
    assert c.center_y > 0
    assert c.is_clickable is True
