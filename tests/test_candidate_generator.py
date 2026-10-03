"""
Tests for CandidateGenerator and ObservationSnapshot.
"""

from services.candidate_generator import CandidateGenerator


def test_epoch_increment_and_stale_rejection():
    generator = CandidateGenerator()

    raw_elements_1 = [
        {"bid": "1", "tag": "button", "type": "submit", "id": "btn-search", "text": "Search"},
        {"bid": "2", "tag": "input", "type": "text", "id": "city", "text": "", "label": "From"},
    ]

    snapshot_1 = generator.generate_snapshot(raw_elements_1, goal="Search flights")
    assert snapshot_1.epoch_id == 1
    assert len(snapshot_1.candidates) == 2
    assert snapshot_1.candidates[0].candidate_id == "e1"
    assert snapshot_1.candidates[0].bid == "1"

    # Valid candidate check with matching epoch
    c1 = snapshot_1.validate_candidate("e1", caller_epoch_id=1)
    assert c1 is not None
    assert c1.dom_id == "btn-search"

    # Stale epoch check
    stale = snapshot_1.validate_candidate("e1", caller_epoch_id=0)
    assert stale is None

    # New snapshot generation increments epoch
    raw_elements_2 = [
        {"bid": "10", "tag": "button", "type": "button", "id": "btn-confirm", "text": "Confirm"},
    ]
    snapshot_2 = generator.generate_snapshot(raw_elements_2, goal="Confirm flight")
    assert snapshot_2.epoch_id == 2
    assert snapshot_2.candidates[0].candidate_id == "e1"
    assert snapshot_2.candidates[0].bid == "10"

    # A decision from epoch 1 evaluated against snapshot 2 must be rejected
    rejected = snapshot_2.validate_candidate("e1", caller_epoch_id=1)
    assert rejected is None


def test_build_jev_action_criteria():
    generator = CandidateGenerator()
    raw = [
        {"bid": "1", "tag": "button", "type": "button", "id": "rep", "text": "Reply"},
        {"bid": "2", "tag": "textarea", "type": "", "id": "msg", "text": "", "label": "Body"},
        {"bid": "3", "tag": "select", "type": "", "id": "cat", "options": ["A", "B"]},
    ]
    snapshot = generator.generate_snapshot(raw, goal="Reply to email")
    criteria = snapshot.build_jev_action_criteria()

    assert "CLICK:e1" in criteria
    assert "TYPE:e2" in criteria
    assert "SELECT:e3" in criteria
    assert "COMPLETE:done" in criteria
    assert "WAIT:quiescence" in criteria


def test_state_fingerprint_sensitive_to_ui_state_changes():
    generator = CandidateGenerator()
    base_raw = [
        {"bid": "1", "tag": "input", "type": "text", "id": "name", "value": "", "checked": False, "expanded": None, "disabled": False, "focused": False},
        {"bid": "2", "tag": "button", "type": "button", "id": "btn", "value": "", "checked": False, "expanded": False, "disabled": False, "focused": False},
    ]
    base_snapshot = generator.generate_snapshot(base_raw, goal="Fill form")
    base_fp = base_snapshot.state_fingerprint

    # 1. Text field value changes -> fingerprint must change
    raw_val_change = [
        {"bid": "1", "tag": "input", "type": "text", "id": "name", "value": "Alice", "checked": False, "expanded": None, "disabled": False, "focused": False},
        {"bid": "2", "tag": "button", "type": "button", "id": "btn", "value": "", "checked": False, "expanded": False, "disabled": False, "focused": False},
    ]
    snap_val = generator.generate_snapshot(raw_val_change, goal="Fill form")
    assert snap_val.state_fingerprint != base_fp

    # 2. Checkbox checked changes -> fingerprint must change
    raw_chk_change = [
        {"bid": "1", "tag": "input", "type": "text", "id": "name", "value": "", "checked": True, "expanded": None, "disabled": False, "focused": False},
        {"bid": "2", "tag": "button", "type": "button", "id": "btn", "value": "", "checked": False, "expanded": False, "disabled": False, "focused": False},
    ]
    snap_chk = generator.generate_snapshot(raw_chk_change, goal="Fill form")
    assert snap_chk.state_fingerprint != base_fp

    # 3. ARIA expanded changes -> fingerprint must change
    raw_exp_change = [
        {"bid": "1", "tag": "input", "type": "text", "id": "name", "value": "", "checked": False, "expanded": None, "disabled": False, "focused": False},
        {"bid": "2", "tag": "button", "type": "button", "id": "btn", "value": "", "checked": False, "expanded": True, "disabled": False, "focused": False},
    ]
    snap_exp = generator.generate_snapshot(raw_exp_change, goal="Fill form")
    assert snap_exp.state_fingerprint != base_fp

    # 4. Button disabled changes -> fingerprint must change
    raw_dis_change = [
        {"bid": "1", "tag": "input", "type": "text", "id": "name", "value": "", "checked": False, "expanded": None, "disabled": False, "focused": False},
        {"bid": "2", "tag": "button", "type": "button", "id": "btn", "value": "", "checked": False, "expanded": False, "disabled": True, "focused": False},
    ]
    snap_dis = generator.generate_snapshot(raw_dis_change, goal="Fill form")
    assert snap_dis.state_fingerprint != base_fp

    # 5. Element focus changes -> fingerprint must change
    raw_foc_change = [
        {"bid": "1", "tag": "input", "type": "text", "id": "name", "value": "", "checked": False, "expanded": None, "disabled": False, "focused": True},
        {"bid": "2", "tag": "button", "type": "button", "id": "btn", "value": "", "checked": False, "expanded": False, "disabled": False, "focused": False},
    ]
    snap_foc = generator.generate_snapshot(raw_foc_change, goal="Fill form")
    assert snap_foc.state_fingerprint != base_fp

