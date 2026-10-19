from library.tools import accepted_edit_replay, undo_journal


def test_accepted_touch_record_links_intent_program_and_declaration(tmp_path):
    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "plan_provenance.json").write_text(
        '{"plan_content_hash":"abc123","plan_path":"plan.json"}',
        encoding="utf-8")
    entry = {
        "id": "touch-1",
        "spec": {"reel": 1, "note": "Use the new lower third",
                 "edits": [{"op": "swap_pixels", "row": "V4",
                            "item": 0, "media": "lower-third.mov"}]},
        "before": {"tracks": []},
        "after": {"tracks": []},
    }

    record = undo_journal.accepted_edit_record(str(tmp_path), entry)

    assert record["format"] == "accepted_reel_edit/1"
    assert record["intent"] == "Use the new lower third"
    assert record["operations"] == [{
        "operation": entry["spec"]["edits"][0],
        "policy": "versioned_override",
    }]
    assert record["source_declaration"] == {
        "plan_content_hash": "abc123", "plan_path": "plan.json"}
    assert record["verified_from_journal"] == "touch-1"


def test_accepted_touch_selection_omits_undone_and_replay_materializations(
        tmp_path, monkeypatch):
    entries = [
        {"id": "active", "final": "Reel 01", "status": "applied",
         "version": 1, "spec": {"edits": [{"op": "swap_pixels"}]}},
        {"id": "undone", "final": "Reel 01", "status": "applied",
         "version": 2, "spec": {"edits": [{"op": "swap_pixels"}]}},
        {"id": "replay", "final": "Reel 01", "status": "applied",
         "version": 3, "accepted_replay_of": "active",
         "spec": {"edits": [{"op": "swap_pixels"}]}},
    ]
    monkeypatch.setattr(undo_journal, "list_entries", lambda _folder: entries)
    monkeypatch.setattr(
        "library.tools.versions.reel_versions.versions_of",
        lambda _folder, _timeline: [
            {"version": 1}, {"version": 2, "undone_by": 4},
            {"version": 3},
        ])

    selected = accepted_edit_replay.accepted_touch_entries(
        str(tmp_path), "Reel 01")

    assert [entry["id"] for entry in selected] == ["active"]


def test_replay_routes_through_touchup_and_identifies_conflicts(
        tmp_path, monkeypatch):
    entry = {"id": "accepted-7", "spec": {"reel": 7, "edits": []}}
    monkeypatch.setattr(
        accepted_edit_replay, "accepted_touch_entries",
        lambda _folder, _timeline: [entry])
    seen = []

    def apply_touchup(_folder, spec):
        seen.append(spec)
        return {"status": "committed", "version": 8,
                "verification_read": {"V4": []}}

    monkeypatch.setattr("library.tools.reel_touchup.apply_touchup",
                        apply_touchup)
    report = accepted_edit_replay.replay_accepted_touches(
        str(tmp_path), ["Reel 07"])

    assert seen[0]["accepted_replay_of"] == "accepted-7"
    assert report["Reel 07"] == [{
        "journal": "accepted-7", "status": "committed", "version": 8,
        "verified": True,
    }]


def test_replay_does_not_hide_a_touch_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(
        accepted_edit_replay, "accepted_touch_entries",
        lambda _folder, _timeline: [{
            "id": "accepted-9", "spec": {"reel": 9, "edits": []}}])

    def refuse(_folder, _spec):
        raise ValueError("target graphic no longer exists")

    monkeypatch.setattr("library.tools.reel_touchup.apply_touchup", refuse)

    try:
        accepted_edit_replay.replay_accepted_touches(
            str(tmp_path), ["Reel 09"])
    except RuntimeError as conflict:
        assert "accepted-9" in str(conflict)
        assert "target graphic no longer exists" in str(conflict)
    else:
        raise AssertionError("an accepted touch conflict was silently ignored")
