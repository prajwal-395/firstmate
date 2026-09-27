"""Rung 6 edit-spec defects: false keyword routes, silent questions,
lost value provenance, partial ledger writes and unmeasured intent misses.
"""

import json
from pathlib import Path

import pytest

from library.tools import edit_ledger, edit_spec
from library.tools.project_layout import ProjectLayout


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _spec():
    return {
        "format": edit_spec.FORMAT,
        "request": "isolate the host mic by 60 percent on Reel 09",
        "clauses": [{
            "id": "op-1",
            "text": "isolate the host mic by 60 percent",
            "op": "voice_isolation",
            "op_source": "model",
            "status": "resolved",
            "anchor": {"kind": "reel"},
            "anchor_source": "requester",
            "reel": "Reel 09 - hook",
            "reel_source": "requester",
            "values": {
                "track": {"value": 1, "unit": "track index",
                          "stated_by": "model"},
                "amount": {"value": 60, "unit": "percent",
                           "stated_by": "requester"},
            },
        }],
    }


def test_operation_type_routes_take_and_track_without_keyword_hits():
    """The word 'take' cannot hijack trim into speech selection, and
    'track' cannot send a picture operation to music selection."""
    assert edit_spec.owner_for_op("span_retime") == "mesh_spine"
    assert edit_spec.owner_for_op("grade") == "render"
    assert edit_spec.owner_for_op("look") == "color_grade"
    assert edit_spec.owner_for_op("angle_plan") == "select_broll"
    assert edit_spec.owner_for_op("motion_graphic") == \
        "render_motion_graphics"
    assert edit_spec.owner_for_op("redraw_closer") == "select_reels"
    assert edit_spec.owner_for_op("transform_override") == "render"


def test_every_edit_operation_owner_exists_in_this_pipeline():
    """A renamed step cannot leave a natural-language op silently
    unroutable after its spec has already been committed."""
    from library.tools import marker_routing

    assert set(edit_spec.OP_OWNERS.values()) <= set(marker_routing.BY_NODE_ID)


def test_every_edit_operation_reaches_a_prompt_or_declared_replayer():
    """Every typed operation has a live prompt owner or ledger replayer.

    Catches: an enum owner accepting a request while neither its prompt nor
    the build's direct/projected ledger readers can apply the operation.
    """
    from library.tools import edit_ledger, marker_routing

    dag = json.loads(Path(
        "library/processes/edit_video/dag.json").read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in dag["nodes"]}
    replayed = set(edit_ledger.REPLAYED_OPS + edit_ledger.PROJECTED_OPS)
    for op, owner in edit_spec.OP_OWNERS.items():
        if marker_routing.BY_NODE_ID[owner].delivery != \
                marker_routing.DELIVERY_PROMPT:
            assert op in replayed
            continue
        manifest_path = Path("library") / nodes[owner]["step_ref"] / \
            "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert any(item["name"] == "timeline_notes"
                   for item in manifest["interface"]["inputs"])


def test_marker_note_uses_the_recorded_operation_type_instead_of_keywords():
    """False matches on 'take', 'track' and 'CTA' cannot override the
    linked grade or retime operation types."""
    from library.tools import marker_routing

    raw = {"text": "track the CTA sign in the final shot"}
    note_id = marker_routing.route_note(raw).edit_link_id
    routed = marker_routing.route_note(raw, edit_rows=[{
        "source_note_id": note_id,
        "op": "grade",
        "anchor": {"kind": "reel"},
        "params": {"cdl": {"sat": 0.8}},
    }])
    assert routed.basis == marker_routing.BASIS_EDIT_SPEC
    assert routed.steps == ["render"]
    assert "music_selection" not in routed.steps
    delivered = marker_routing.prompt_block([routed])["notes"][0]
    assert delivered["typed_operations"][0]["op"] == "grade"

    raw_take = {"text": "take 6 frames off the tail of this clip"}
    take_id = marker_routing.route_note(raw_take).edit_link_id
    take_routed = marker_routing.route_note(raw_take, edit_rows=[{
        "source_note_id": take_id,
        "op": "span_retime",
        "anchor": {"kind": "words", "phrase": "this clip"},
        "params": {"edge": "tail"},
    }])
    assert take_routed.steps == ["mesh_spine"]
    assert "speech_sequence" not in take_routed.steps


def test_each_owner_receives_only_its_clause_from_a_multi_operation_note():
    """A clause sent to another owner cannot leak through a shared note.

    Catches: two operation types in one request reaching both prompts and
    letting each step act on a clause it does not own.
    """
    from library.tools import marker_routing

    raw = {"text": "make the music softer and grade the second shot warmer"}
    note_id = marker_routing.route_note(raw).edit_link_id
    routed = marker_routing.route_note(raw, edit_rows=[
        {
            "source_note_id": note_id,
            "op": "plan_change",
            "anchor": {"kind": "reel"},
            "params": {
                "operation_type": "audio_mix",
                "owner": "audio_mix",
                "values": {"level": {
                    "value": -18, "unit": "dB", "stated_by": "requester"}},
            },
        },
        {
            "source_note_id": note_id,
            "op": "grade",
            "anchor": {"kind": "words", "phrase": "second shot"},
            "params": {"look": "warmer"},
        },
    ])

    audio = marker_routing.prompt_block(
        [routed], node_id="audio_mix")["notes"][0]["typed_operations"]
    grade = marker_routing.prompt_block(
        [routed], node_id="render")["notes"][0]["typed_operations"]
    assert [operation["op"] for operation in audio] == ["audio_mix"]
    assert [operation["op"] for operation in grade] == ["grade"]


def test_unresolved_linked_note_is_held_out_of_planning():
    """A note with an open edit-spec question does not fall back to a
    keyword route while the editor still needs to answer."""
    from library.tools import marker_routing

    raw = {"text": "track the sign in the final shot"}
    note_id = marker_routing.route_note(raw).edit_link_id
    routed = marker_routing.route_note(raw, pending_edit_spec={
        "reason": "edit spec has unresolved referents",
        "questions": [{"question": "Which sign?"}],
    })
    assert routed.edit_link_id == note_id
    assert routed.outcome == marker_routing.OUTCOME_UNROUTED
    assert routed.basis == marker_routing.BASIS_EDIT_SPEC_PENDING
    assert routed.steps == []


def test_pending_note_handshake_holds_pipeline_until_ledger_recorded(tmp_path):
    """A linked note cannot fall back to its keyword route while waiting
    for a host answer, editor clarification, or atomic ledger recording."""
    from library.tools import llm_handshake

    project = _project(tmp_path)
    note_id = "marker-note-123"
    request_id, _path, _note_id = edit_spec.prepare_request(
        str(project), "isolate the host mic", note_id=note_id)
    pending = edit_spec.pending_note_states(str(project))
    assert note_id in pending
    assert "waiting for the host" in pending[note_id]["reason"]

    response_path = Path(llm_handshake.response_path(
        str(project), request_id))
    unresolved = {
        "format": edit_spec.FORMAT,
        "request": "isolate the host mic",
        "clauses": [{
            "id": "op-1", "text": "isolate the host mic",
            "op": "voice_isolation", "op_source": "model",
            "status": "needs_clarification",
            "missing_referent": "which microphone",
            "question": "Which track holds the host mic?",
        }],
    }
    response_path.write_text(json.dumps(unresolved), encoding="utf-8")
    pending = edit_spec.pending_note_states(str(project))
    assert pending[note_id]["questions"][0]["question"] == \
        "Which track holds the host mic?"

    response_path.write_text(json.dumps(_spec()), encoding="utf-8")
    resolved = edit_spec.load_response_spec(str(project), request_id)
    edit_spec.record_spec(str(project), resolved)
    assert edit_spec.pending_note_states(str(project)) == {}
    assert edit_ledger.load_rows(str(project))[0]["source_note_id"] == note_id


def test_malformed_edit_spec_request_holds_pipeline_instead_of_falling_back(
        tmp_path):
    """A damaged translation request cannot silently re-enable keyword routing.

    Catches: a corrupt edit-spec request being ignored so its natural-language
    note reaches planning without typed operations or editor clarification.
    """
    from library.processes.edit_video.run_pipeline import run_pipeline
    from library.tools.llm_handshake import REQUESTS_SUBDIR

    project = _project(tmp_path)
    request_dir = project / REQUESTS_SUBDIR
    request_dir.mkdir(parents=True)
    (request_dir / "edit_spec_broken.json").write_text(
        "{not json", encoding="utf-8")

    pending = edit_spec.pending_note_states(str(project))
    assert pending["unlinked:edit_spec_broken"]["reason"].startswith(
        "edit spec request needs repair:")
    result = run_pipeline(str(project))
    assert result["status"] == "REFUSED"
    assert "needs repair" in result["reason"]


def test_untranslated_marker_note_refuses_keyword_planning(tmp_path):
    """A raw natural-language marker cannot keep using the old word router.

    Catches: `take`/`track`/`CTA` keyword matches reaching planning steps
    when the editor never ran the required edit-spec translation.
    """
    from library.processes.edit_video.run_pipeline import run_pipeline
    from library.tools import marker_feedback, marker_routing
    from library.tools.project_layout import Area

    project = _project(tmp_path)
    note = {"source": "timeline_marker", "name": "Editor note",
            "note": "track the CTA sign in the final shot",
            "text": "track the CTA sign in the final shot", "frame": None,
            "frame_in_timeline_space": None, "custom_data": {}}
    pull = ProjectLayout(str(project)).write_path(
        Area.MARKER_FEEDBACK,
        f"Editor.20260925{marker_feedback.PULL_FILE_SUFFIX}")
    pull.write_text(json.dumps({
        "format": marker_feedback.PULL_FORMAT,
        "timeline": "Editor",
        "notes": [note],
    }), encoding="utf-8")

    note_id = marker_routing.edit_link_id(note, "Editor", str(pull))
    pending = edit_spec.pending_note_states(str(project))
    assert "keyword routing is not used" in pending[note_id]["reason"]
    assert "ren spec prepare" in pending[note_id]["prepare_command"]
    result = run_pipeline(str(project))
    assert result["status"] == "REFUSED"
    assert "translate it with: ren spec prepare" in result["reason"]


def test_changed_spec_value_does_not_reuse_a_same_count_ledger_row(tmp_path):
    """A changed host answer cannot pass the pipeline on row count alone.

    Catches: a stale amount remaining active after the request response was
    revised, while the pending guard sees the same number of ledger rows.
    """
    from library.tools import llm_handshake

    project = _project(tmp_path)
    note_id = "marker-note-amount"
    request_id, _path, _note_id = edit_spec.prepare_request(
        str(project), "isolate the host mic by 60 percent", note_id=note_id)
    response_path = Path(llm_handshake.response_path(
        str(project), request_id))
    spec = _spec()
    spec["source_note_id"] = note_id
    response_path.write_text(json.dumps(spec), encoding="utf-8")
    edit_spec.record_spec(str(project),
                          edit_spec.load_response_spec(
                              str(project), request_id))
    assert edit_spec.pending_note_states(str(project)) == {}

    spec["clauses"][0]["values"]["amount"]["value"] = 70
    response_path.write_text(json.dumps(spec), encoding="utf-8")
    pending = edit_spec.pending_note_states(str(project))
    assert "exactly in the ledger" in pending[note_id]["reason"]
    with pytest.raises(edit_spec.EditSpecError,
                       match="current resolved edit spec"):
        edit_spec.prepare_intent(str(project), request_id,
                                 str(tmp_path / "readback"),
                                 str(tmp_path / "export"))


def test_revising_one_note_replaces_its_old_operation_type(tmp_path):
    """One note's corrected translation removes its obsolete ledger row.

    Catches: changing an operation after intent review leaving the old
    operation active beside the revised one or holding the pipeline forever.
    """
    project = _project(tmp_path)
    spec = _spec()
    spec["source_note_id"] = "marker-note-23"
    edit_spec.record_spec(str(project), spec)

    revised = json.loads(json.dumps(spec))
    clause = revised["clauses"][0]
    clause["op"] = "caption_fix"
    clause["text"] = "change the caption to 'We shipped it'"
    clause["anchor"] = {"kind": "words", "phrase": "we ship it"}
    clause["values"] = {"replacement": {
        "value": "We shipped it", "unit": "caption text",
        "stated_by": "requester"}}
    edit_spec.record_spec(str(project), revised)

    rows = edit_ledger.load_rows(str(project))
    assert len(rows) == 1
    assert rows[0]["op"] == "caption_fix"
    assert rows[0]["source_note_id"] == "marker-note-23"


def test_a_newer_note_supersedes_the_old_note_without_keyword_rerouting(
        tmp_path):
    """A newer same-operation amount replaces the prior note cleanly.

    Catches: an old, superseded marker either blocking the pipeline or
    falling back to a second keyword route beside its newer ledger row.
    """
    from library.tools import marker_feedback, marker_routing
    from library.tools.project_layout import Area

    project = _project(tmp_path)
    old_note = {
        "source": "timeline_marker", "name": "isolate at 60",
        "note": "isolate the host mic at 60 percent",
        "text": "isolate the host mic at 60 percent", "frame": 10,
        "frame_in_timeline_space": 10, "custom_data": {},
    }
    new_note = {
        "source": "timeline_marker", "name": "isolate at 80",
        "note": "isolate the host mic at 80 percent",
        "text": "isolate the host mic at 80 percent", "frame": 11,
        "frame_in_timeline_space": 11, "custom_data": {},
    }
    pull = ProjectLayout(str(project)).write_path(
        Area.MARKER_FEEDBACK,
        f"Editor.20260925{marker_feedback.PULL_FILE_SUFFIX}")
    pull.write_text(json.dumps({
        "format": marker_feedback.PULL_FORMAT,
        "timeline": "Editor",
        "notes": [old_note, new_note],
    }), encoding="utf-8")

    for note, amount in ((old_note, 60), (new_note, 80)):
        note_id = marker_routing.edit_link_id(note, "Editor", str(pull))
        request_id, _request_path, _linked_id = edit_spec.prepare_request(
            str(project), note["text"], note_id=note_id)
        response = _spec()
        response["request"] = note["text"]
        response["clauses"][0]["values"]["amount"]["value"] = amount
        from library.tools import llm_handshake
        Path(llm_handshake.response_path(
            str(project), request_id)).write_text(
                json.dumps(response), encoding="utf-8")
        edit_spec.record_spec(
            str(project), edit_spec.load_response_spec(
                str(project), request_id))

    states = edit_spec.pending_note_states(str(project))
    old_id = marker_routing.edit_link_id(old_note, "Editor", str(pull))
    new_id = marker_routing.edit_link_id(new_note, "Editor", str(pull))
    assert states[old_id]["state"] == "superseded"
    assert new_id not in states
    routed = {note.edit_link_id: note
              for note in marker_routing.route_project(str(project))}
    assert routed[old_id].basis == \
        marker_routing.BASIS_EDIT_SPEC_SUPERSEDED
    assert routed[old_id].steps == []
    assert routed[new_id].basis == marker_routing.BASIS_EDIT_SPEC
    assert routed[new_id].steps == ["render"]


def test_two_notes_on_one_frame_are_translated_separately(tmp_path):
    """A second note typed on the same frame is its own edit.

    Catches: "music too loud" and a later "cut the pause" at frame 120
    sharing one link id, so once the first was recorded the second was
    neither held for translation nor translated - the planner acted on
    the first note's typed operation under the second note's words.
    """
    from library.tools import marker_routing

    project = _project(tmp_path)
    first = {"source": "timeline_marker", "frame": 120,
             "text": "music too loud"}
    second = {"source": "timeline_marker", "frame": 120,
              "text": "cut the pause"}
    pull = tmp_path / "second.pull.json"
    assert marker_routing._note_id(first, "Reel 01", str(pull)) == \
        marker_routing._note_id(second, "Reel 01", str(pull))
    first_id = marker_routing.edit_link_id(first, "Reel 01", str(pull))
    second_id = marker_routing.edit_link_id(second, "Reel 01", str(pull))
    assert first_id != second_id

    routed = marker_routing.route_note(
        second, "Reel 01", str(pull), edit_rows=[{
            "source_note_id": first_id, "op": "plan_change",
            "anchor": {"kind": "reel"},
            "params": {"operation_type": "audio_mix", "owner": "audio_mix",
                       "values": {"level": {"value": -6, "unit": "dB",
                                            "stated_by": "requester"}}},
        }])
    assert routed.basis != marker_routing.BASIS_EDIT_SPEC


def test_edit_pipeline_refuses_to_plan_around_an_open_note_question(tmp_path):
    """An unanswered K6 referent holds the run before any planning step
    can silently produce a cut without the requested object or shot."""
    from library.processes.edit_video.run_pipeline import run_pipeline

    project = _project(tmp_path)
    edit_spec.prepare_request(
        str(project), "use the logo on the end card", note_id="marker-17")
    result = run_pipeline(str(project))
    assert result["status"] == "REFUSED"
    assert "natural-language note" in result["reason"]
    assert "ren spec resolve" in result["reason"]


def test_missing_shot_becomes_a_question_and_records_no_partial_clause():
    """K6's absent or ambiguous shot is sent back to the editor; no
    could_not_determine row or resolved sibling is committed meanwhile."""
    spec = _spec()
    spec["clauses"].append({
        "id": "op-2", "text": "use the shot behind the host",
        "op": "angle_plan", "op_source": "model",
        "status": "needs_clarification",
        "missing_referent": "which shot",
        "question": "Which shot should replace the host's close-up?",
    })
    questions = edit_spec.questions_for_spec(spec)
    assert questions[0]["clause_id"] == "op-2"
    with pytest.raises(edit_spec.NeedsClarification,
                       match="ask the editor"):
        edit_spec.record_spec("/unused", spec)


def test_could_not_determine_cannot_be_recorded_as_a_resolved_operation():
    """A legacy non-answer cannot sneak into a resolved ledger row.

    Catches: the model returning could_not_determine beside a resolved
    clause, which would otherwise let planning continue without asking.
    """
    spec = _spec()
    spec["clauses"][0]["could_not_determine"] = "missing logo"

    with pytest.raises(edit_spec.EditSpecError,
                       match="uses could_not_determine"):
        edit_spec.validate_spec(spec)


def _angle_values():
    return {
        "camera": {"value": "wide", "unit": "camera name",
                   "stated_by": "requester"},
        "min_shot_seconds": {"value": 3, "unit": "seconds",
                             "stated_by": "requester"},
        "lead_frames": {"value": 0, "unit": "frames",
                        "stated_by": "model"},
    }


def _angle_spec(reels, reel=None):
    clause = {
        "id": "angle-1",
        "text": "keep each camera angle on screen at least 3 seconds",
        "op": "angle_plan", "op_source": "model", "status": "resolved",
        "anchor": {"kind": "reel"}, "anchor_source": "requester",
        "values": _angle_values(),
    }
    if reel:
        clause.update(reel=reel, reel_source="requester")
    return {"format": edit_spec.FORMAT,
            "request": "keep each camera angle on screen at least 3 seconds",
            "available_reels": reels, "clauses": [clause]}


def test_angle_plan_requires_an_exact_reel_before_ledger_recording(tmp_path):
    """A camera plan without reel scope cannot become a project-wide pin.

    Catches: E5's per-reel angle plan being recorded without naming which
    reel should hold the camera, minimum shot length and lead.
    """
    project = _project(tmp_path)
    spec = _angle_spec(["Reel 09 - hook", "Reel 10 - close"])
    questions = edit_spec.questions_for_spec(spec)
    assert questions[0]["missing_referent"] == \
        "which reel this angle plan belongs to"
    assert "Reel 09 - hook, Reel 10 - close" in questions[0]["question"]
    with pytest.raises(edit_spec.NeedsClarification,
                       match="Which exact reel timeline"):
        edit_spec.record_spec(str(project), spec)
    assert edit_ledger.load_rows(str(project)) == []

    spec = _angle_spec(["Reel 09 - hook"], reel="Reel 09 - hook")
    del spec["clauses"][0]["values"]["lead_frames"]
    with pytest.raises(edit_ledger.EditLedgerError, match="lead_frames"):
        edit_spec.record_spec(str(project), spec)
    assert edit_ledger.load_rows(str(project)) == []


def test_angle_plan_in_a_project_without_reels_says_why_it_asks():
    """With no reels to choose from, the question says so.

    Catches: PA2.3 in a single-edit project asking "which reel?" with an
    empty list, a question the editor cannot answer from what it says.
    """
    [question] = edit_spec.questions_for_spec(_angle_spec([]))
    assert "no reels yet" in question["question"]


def test_angle_plan_asks_when_its_reel_name_matches_no_known_timeline():
    """An invented per-reel scope cannot become a plan nobody will replay.

    Catches: a typo or nonexistent reel name being accepted as an E5
    per-reel plan while the ledger matcher would silently miss the reel.
    """
    spec = _angle_spec(["Reel 01 - opening"], reel="Reel 99")
    questions = edit_spec.questions_for_spec(spec)
    assert "Reel 01 - opening" in questions[0]["question"]
    with pytest.raises(edit_spec.NeedsClarification):
        edit_spec.ledger_rows(spec)


def test_spec_context_uses_the_planned_and_built_reel_timeline_names(
        tmp_path):
    """A reel alias is checked against names the project actually declares.

    Catches: the translation prompt claiming reel names were supplied while
    omitting planned or already-built timeline names from its context.
    """
    project = _project(tmp_path)
    state_path = Path(ProjectLayout(str(project)).pipeline_data_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"step_outputs": {
        "select_reels": {"reel_selection": {"moments": [{
            "number": 9, "slug": "the-close"}]}},
        "build_reels": {"reel_build": {
            "timelines_built": ["Reel 10 - product-demo"]}},
    }}), encoding="utf-8")

    context = edit_spec._project_context(str(project), "set the angle", "")
    assert context["available_reels"] == [
        "Reel 09 - the-close", "Reel 10 - product-demo"]


def test_spec_context_carries_the_edit_in_timeline_order_with_its_words(
        tmp_path):
    """"Clip 3" and "0:07" are resolvable from the translation context.

    Catches: CT3.1 ("take 6 frames off the tail of clip 3") read as catalog
    clip_003 and bounced back as a question, because the context listed only
    the catalog and never the edit the editor was looking at.
    """
    project = _project(tmp_path)
    state_path = Path(ProjectLayout(str(project)).pipeline_data_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    def speech(position, clip, start, end, words):
        return {"position": position, "block_type": "speech",
                "clip_id": clip, "timeline_start": start,
                "timeline_end": end, "content": {"word_timestamps": [
                    {"word": word, "source_start": 0.0, "source_end": 0.1}
                    for word in words.split()]}}

    state_path.write_text(json.dumps({"step_outputs": {
        "mesh_spine": {"audio_spine": {"structure": [
            speech(0, "clip_011", 0.0, 2.4, "today is the day"),
            {"position": 1, "block_type": "transition_slot",
             "clip_id": None, "timeline_start": 2.4, "timeline_end": 5.4,
             "content": None},
            speech(2, "clip_017", 5.4, 9.0, "it lines up so nicely"),
        ]}},
        "select_broll": {"b_roll_assignments": [
            {"spine_block_position": 1, "clip_id": "clip_005"}]},
    }}), encoding="utf-8")

    edit = edit_spec._project_context(
        str(project), "trim clip 3", "")["current_edit"]
    assert [row["order"] for row in edit] == [1, 2, 3]
    assert edit[1]["cutaway_clip_ids"] == ["clip_005"]
    assert edit[2]["clip_id"] == "clip_017"
    assert edit[2]["timeline_start_seconds"] == 5.4
    assert edit[2]["last_words"] == "it lines up so nicely"
    assert "source_start" not in json.dumps(edit)


def test_stated_frames_reach_the_owner_with_its_rung7_fields():
    """A number in frames arrives naming where the owner can carry it.

    Catches: a requester's "12-frame dissolve" delivered to
    plan_transitions as a bare value it then re-reads as a feel word,
    when rung 7 gave the planner `duration_frames` to hold it exactly.
    """
    from library.tools import marker_routing

    raw = {"text": "make the dissolve into the b-roll 12 frames"}
    note_id = marker_routing.route_note(raw).edit_link_id
    routed = marker_routing.route_note(raw, edit_rows=[{
        "source_note_id": note_id,
        "op": "plan_change",
        "anchor": {"kind": "reel"},
        "params": {
            "operation_type": "transition",
            "owner": "plan_transitions",
            "values": {
                "duration": {"value": 12, "unit": "frames",
                             "stated_by": "requester"},
                "type": {"value": "cross_dissolve", "unit": "transition type",
                         "stated_by": "requester"}},
        },
    }])

    [operation] = marker_routing.prompt_block(
        [routed], node_id="plan_transitions")["notes"][0]["typed_operations"]
    carried = operation["carry_stated_numbers_in"]
    assert set(carried) == {"duration"}
    assert "duration_frames" in carried["duration"]
    assert all(field.endswith("_frames") for field in carried["duration"])


def test_resolved_values_keep_units_and_provenance_in_edit_ledger(tmp_path):
    """An exact amount and model-chosen track survive translation into
    the one ledger with their separate sources and units."""
    project = _project(tmp_path)
    result = edit_spec.record_spec(str(project), _spec())
    assert result[0][1] == "recorded"
    row = edit_ledger.load_rows(str(project))[0]
    assert row["params"] == {"track": 1, "amount": 60}
    assert row["value_sources"] == {
        "track": "model", "amount": "requester"}
    assert row["value_units"] == {
        "track": "track index", "amount": "percent"}
    assert row["anchor_source"] == "requester"
    assert row["op_source"] == "model"


def test_missing_value_source_or_unit_is_refused_before_ledger_write(tmp_path):
    """A spec value cannot be flattened into the ledger without saying
    who stated it and which units the number uses."""
    project = _project(tmp_path)
    spec = _spec()
    del spec["clauses"][0]["values"]["amount"]["unit"]
    with pytest.raises(edit_spec.EditSpecError, match=r"\.unit"):
        edit_spec.record_spec(str(project), spec)
    assert edit_ledger.load_rows(str(project)) == []


def test_one_bad_clause_prevents_every_row_from_being_written(tmp_path):
    """Atomic spec recording refuses a batch with duplicate decisions
    instead of replaying only its last clause."""
    project = _project(tmp_path)
    spec = _spec()
    duplicate = json.loads(json.dumps(spec["clauses"][0]))
    duplicate["id"] = "op-2"
    duplicate["text"] = "isolate the same track by 80 percent"
    duplicate["values"]["amount"]["value"] = 80
    spec["clauses"].append(duplicate)
    with pytest.raises(edit_ledger.EditLedgerError,
                       match="same ledger decision twice"):
        edit_spec.record_spec(str(project), spec)
    assert edit_ledger.load_rows(str(project)) == []


def test_intent_miss_returns_the_measured_shortfall_in_a_revised_spec():
    """A build miss returns requested-versus-measured evidence with the
    clause, rather than recording an unexplained generic failure."""
    spec = _spec()
    result = edit_spec.intent_check(spec, {
        "op-1": {
            "timeline": {"followed": True, "broke_nothing": True,
                         "measurement": {"value": 60, "unit": "percent"},
                         "evidence": "Timeline readback shows 60%."},
            "export": {"followed": False, "broke_nothing": True,
                       "measurement": {"value": 42, "unit": "percent"},
                       "shortfall": {"requested": 60, "observed": 42,
                                     "unit": "percent"},
                       "evidence": "Export measurement reads 42%."},
        }
    })
    assert result["status"] == "revise"
    revised = result["revised_spec"]["clauses"][0]
    assert revised["revision"]["measured_shortfalls"][0][
        "measured_shortfall"] == {
            "requested": 60, "observed": 42, "unit": "percent"}
    assert "proxy preview" in result["proxy_preview"]


def test_intent_shortfall_must_match_the_request_unit_and_export_measurement():
    """An unrelated unit or stale export reading cannot be reported as the
    measured miss against the editor's declared amount."""
    spec = _spec()
    evidence = {
        "op-1": {
            "timeline": {"followed": True, "broke_nothing": True,
                         "measurement": {"value": 60, "unit": "percent"},
                         "evidence": "Timeline readback shows 60%."},
            "export": {"followed": False, "broke_nothing": True,
                       "measurement": {"value": 42, "unit": "percent"},
                       "shortfall": {"requested": 60, "observed": 41,
                                     "unit": "percent"},
                       "evidence": "Export measurement reads 42%."},
        },
    }
    with pytest.raises(edit_spec.EditSpecError,
                       match="differs from the measured value"):
        edit_spec.intent_check(spec, evidence)

    evidence["op-1"]["export"]["shortfall"] = {
        "requested": 60, "observed": 42, "unit": "dB"}
    with pytest.raises(edit_spec.EditSpecError, match="differs from measured unit"):
        edit_spec.intent_check(spec, evidence)


def test_intent_handshake_reviews_the_built_artifacts_and_saves_revision(
        tmp_path):
    """The intent judge receives the real readback/export paths, validates
    its measured miss, and returns a revised spec without editing the ledger."""
    from library.tools import llm_handshake

    project = _project(tmp_path)
    request_id, _request_path, _note_id = edit_spec.prepare_request(
        str(project), "isolate the host mic by 60 percent on Reel 09")
    request_spec = _spec()
    response_path = Path(llm_handshake.response_path(
        str(project), request_id))
    response_path.write_text(json.dumps(request_spec), encoding="utf-8")
    edit_spec.record_spec(str(project),
                          edit_spec.load_response_spec(
                              str(project), request_id))
    readback = tmp_path / "built-timeline.txt"
    readback.write_text("Audio Track 1 voice isolation: 60%\n",
                        encoding="utf-8")
    export = tmp_path / "reel.mp4"
    export.write_bytes(b"test export fixture")

    intent_id, intent_path = edit_spec.prepare_intent(
        str(project), request_id, str(readback), str(export))
    intent_request = json.loads(Path(intent_path).read_text(encoding="utf-8"))
    context = json.loads(intent_request["context"])
    assert intent_request["kind"] == "edit_spec_intent"
    assert context["artifacts"]["timeline_readback"]["path"] == \
        str(readback.resolve())
    assert context["artifacts"]["export"]["path"] == str(export.resolve())
    assert "actual built timeline readback" in intent_request["prompt"]
    assert "proxy preview" in intent_request["prompt"]

    observations = {
        "format": edit_spec.INTENT_FORMAT,
        "observations": {
            "op-1": {
                "timeline": {
                    "followed": True, "broke_nothing": True,
                    "measurement": {"value": 60, "unit": "percent"},
                    "evidence": "Timeline inspector readback shows 60%.",
                },
                "export": {
                    "followed": False, "broke_nothing": True,
                    "measurement": {"value": 42, "unit": "percent"},
                    "shortfall": {"requested": 60, "observed": 42,
                                  "unit": "percent"},
                    "evidence": "Export measurement reads 42%.",
                },
            },
        },
    }
    Path(llm_handshake.response_path(
        str(project), intent_id)).write_text(
            json.dumps(observations), encoding="utf-8")
    result = edit_spec.resolve_intent(str(project), intent_id)
    assert result["status"] == "revise"
    revised_path = Path(result["revised_spec_path"])
    revised = json.loads(revised_path.read_text(encoding="utf-8"))
    assert revised["clauses"][0]["revision"]["measured_shortfalls"][0][
        "measured_shortfall"] == {
            "requested": 60, "observed": 42, "unit": "percent"}
    assert len(edit_ledger.load_rows(str(project))) == 1

    export.write_bytes(b"a later export")
    with pytest.raises(edit_spec.EditSpecError,
                       match="artifact changed while the intent review was pending"):
        edit_spec.resolve_intent(str(project), intent_id)


def test_intent_cli_cannot_accept_observations_without_reviewed_artifacts():
    """The product intent command must use the readback/export handshake.

    Catches: a user supplied observations file claiming a pass without Ren
    asking the host to inspect the built timeline and final export.
    """
    with pytest.raises(SystemExit) as exc:
        edit_spec.main([
            "intent", "check", "--spec", "spec.json",
            "--observations", "observations.json"])
    assert exc.value.code == 2


def test_prepare_writes_edit_spec_handshake_for_host_translation(tmp_path):
    """The host receives a typed edit-spec request before Ren records any
    operation, using the same LLM request/response folders as other work."""
    from library.tools.llm_handshake import request_path

    project = _project(tmp_path)
    request_id, path, note_id = edit_spec.prepare_request(
        str(project), "remove the background noise", reel="Reel 09",
        note_id="marker-note-123")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["step_id"] == request_id
    assert payload["kind"] == "edit_spec"
    assert "one operation per clause" in payload["prompt"]
    assert json.loads(payload["context"])["source_note_id"] == \
        "marker-note-123"
    assert note_id == "marker-note-123"
    assert Path(path) == Path(request_path(str(project), request_id))


def test_direct_request_gets_a_durable_note_for_typed_pipeline_delivery(tmp_path):
    """A chat/CLI request without an existing Resolve marker still gets
    a captured note id, so a resolved plan op reaches its owner step."""
    from library.tools import marker_feedback, marker_routing

    project = _project(tmp_path)
    request_id, _path, note_id = edit_spec.prepare_request(
        str(project), "put a brand end card on the final shot")
    pulls = marker_feedback.pulled_files(str(project))
    assert len(pulls) == 1
    routed = marker_routing.route_project(str(project))[0]
    assert routed.edit_link_id == note_id
    assert routed.basis == marker_routing.BASIS_EDIT_SPEC_PENDING
    assert request_id in edit_spec.pending_note_states(str(project))[
        note_id]["request_id"]


def test_brand_asset_is_stored_and_delivered_by_operation_type(tmp_path):
    """A brand CTA reaches motion graphics from its typed operation,
    regardless of words that the legacy router might match elsewhere."""
    from library.tools import marker_routing

    project = _project(tmp_path)
    from library.tools import llm_handshake

    request_id, _path, note_id = edit_spec.prepare_request(
        str(project), "put the supplied brand logo on the end card")
    spec = {
        "format": edit_spec.FORMAT,
        "request": "put the supplied brand logo on the end card",
        "source_note_id": note_id,
        "clauses": [{
            "id": "brand-1",
            "text": "use the supplied brand logo on the end card",
            "op": "brand_asset",
            "op_source": "model",
            "status": "resolved",
            "anchor": {"kind": "reel"},
            "anchor_source": "requester",
            "values": {
                "asset": {"value": "approved-logo.svg",
                          "unit": "asset path",
                          "stated_by": "requester"},
            },
        }],
    }

    Path(llm_handshake.response_path(str(project), request_id)).write_text(
        json.dumps(spec), encoding="utf-8")
    edit_spec.record_spec(str(project),
                          edit_spec.load_response_spec(
                              str(project), request_id))
    rows = edit_ledger.load_rows(str(project))
    assert rows[0]["op"] == "plan_change"
    assert rows[0]["params"]["operation_type"] == "brand_asset"
    assert rows[0]["params"]["owner"] == "render_motion_graphics"
    assert rows[0]["params"]["values"]["asset"]["unit"] == \
        "asset path"

    routed = marker_routing.route_project(str(project))[0]
    assert routed.basis == marker_routing.BASIS_EDIT_SPEC
    assert routed.steps == ["render_motion_graphics"]
    operation = marker_routing.prompt_block([routed])["notes"][0][
        "typed_operations"][0]
    assert operation["op"] == "brand_asset"
    assert operation["params"]["asset"]["value"] == "approved-logo.svg"
    manifest = json.loads(Path(
        "library/steps/step_4_06_render_motion_graphics/manifest.json"
    ).read_text(encoding="utf-8"))
    marker_routing.assert_deliverable(
        "render_motion_graphics", manifest, [routed])


def test_redraw_closer_routes_to_the_select_reels_prompt(tmp_path):
    """A closer change reaches the step that chooses each reel's ending,
    with a declared notes input, instead of unrelated motion graphics."""
    from library.tools import llm_handshake, marker_routing

    project = _project(tmp_path)
    request_id, _path, note_id = edit_spec.prepare_request(
        str(project), "move the closing line to the new opening words")
    spec = {
        "format": edit_spec.FORMAT,
        "request": "move the closing line to the new opening words",
        "source_note_id": note_id,
        "clauses": [{
            "id": "closer-1",
            "text": "move this shared closer",
            "op": "redraw_closer",
            "op_source": "requester",
            "status": "resolved",
            "anchor": {"kind": "words", "phrase": "new opening words"},
            "anchor_source": "requester",
            "values": {
                "from_phrase": {"value": "old closing words",
                                "unit": "spoken phrase",
                                "stated_by": "requester"},
            },
        }],
    }
    Path(llm_handshake.response_path(str(project), request_id)).write_text(
        json.dumps(spec), encoding="utf-8")
    edit_spec.record_spec(str(project),
                          edit_spec.load_response_spec(
                              str(project), request_id))

    routed = marker_routing.route_project(str(project))[0]
    assert routed.steps == ["select_reels"]
    assert routed.basis == marker_routing.BASIS_EDIT_SPEC
    manifest = json.loads(Path(
        "library/steps/step_3_04_select_reels/manifest.json"
    ).read_text(encoding="utf-8"))
    marker_routing.assert_deliverable("select_reels", manifest, [routed])


def test_audio_mix_plan_change_reaches_the_mix_prompt(tmp_path):
    """An audio_mix operation is not recorded as report-only feedback.

    Catches: the plan owner being named in the ledger enum while the mixed
    gain answer never reaches the model that decides the audio plan.
    """
    from library.tools import llm_handshake, marker_routing

    project = _project(tmp_path)
    request_id, _path, _note_id = edit_spec.prepare_request(
        str(project), "lower the bed by 4 dB while the host is speaking")
    spec = {
        "format": edit_spec.FORMAT,
        "request": "lower the bed by 4 dB while the host is speaking",
        "clauses": [{
            "id": "mix-1",
            "text": "lower the bed by 4 dB while the host is speaking",
            "op": "audio_mix",
            "op_source": "model",
            "status": "resolved",
            "anchor": {"kind": "reel"},
            "anchor_source": "requester",
            "values": {
                "separation": {"value": 4, "unit": "dB",
                               "stated_by": "requester"},
            },
        }],
    }
    Path(llm_handshake.response_path(str(project), request_id)).write_text(
        json.dumps(spec), encoding="utf-8")
    edit_spec.record_spec(str(project),
                          edit_spec.load_response_spec(
                              str(project), request_id))

    routed = marker_routing.route_project(str(project))[0]
    assert routed.steps == ["audio_mix"]
    assert routed.basis == marker_routing.BASIS_EDIT_SPEC
    manifest = json.loads(Path(
        "library/steps/step_5_02_audio_mix/manifest.json"
    ).read_text(encoding="utf-8"))
    marker_routing.assert_deliverable("audio_mix", manifest, [routed])


def test_story_pacing_cold_open_translates_and_reaches_mesh_spine(tmp_path):
    """ST2.1's structure is a typed value delivered to its owning step.

    The line stays in a transcript-grounded words anchor; the value names
    only the requested hook/intro structure, leaving the passage position
    to mesh_spine's current speech sequence.
    """
    from library.tools import llm_handshake, marker_routing

    request = (
        "open on the line about quitting every single day, then go back to "
        "the intro")
    phrase = "i've quit every single day"
    project = _project(tmp_path)
    state = {
        "step_outputs": {
            "speech_sequence": {
                "transcripts_toon": (
                    "[1]{clip_id,start,end,text}\n"
                    "clip_017,30,40,"
                    "i almost didn't do this again i've been literally this "
                    "week i've quit every single day and the only reason "
                    "i'm recording today is because i told myself this is "
                    "the last shot i got"),
                "topics_toon": "",
            },
        },
    }
    Path(ProjectLayout(str(project)).pipeline_data_path).write_text(
        json.dumps(state), encoding="utf-8")

    request_id, request_path, note_id = edit_spec.prepare_request(
        str(project), request)
    translator_request = json.loads(
        Path(request_path).read_text(encoding="utf-8"))
    translator_context = json.loads(translator_request["context"])
    assert translator_context["operation_owners"]["story_pacing"] == \
        "mesh_spine"
    assert translator_context["transcript_context"]["transcripts_toon"]
    contract = translator_context["plan_operation_value_contracts"][
        "story_pacing"]["values"]["opening_structure"]
    assert "cold_open_then_intro" in contract["allowed"]
    assert "cold_open_then_intro" in translator_request["prompt"]
    assert "do not encode a line or passage position" in \
        translator_request["prompt"]

    response = {
        "format": edit_spec.FORMAT,
        "request": request,
        "source_note_id": note_id,
        "clauses": [{
            "id": "c1",
            "text": request,
            "op": "story_pacing",
            "op_source": "requester",
            "status": "resolved",
            "anchor": {
                "kind": "words",
                "phrase": phrase,
                "stated": "the line about quitting every single day",
            },
            "anchor_source": "requester",
            "values": {
                "opening_structure": {
                    "value": "cold_open_then_intro",
                    "unit": "spine structure",
                    "stated_by": "requester",
                },
            },
        }],
    }
    Path(llm_handshake.response_path(str(project), request_id)).write_text(
        json.dumps(response), encoding="utf-8")
    translated = edit_spec.load_response_spec(str(project), request_id)
    edit_spec.record_spec(str(project), translated)

    row = edit_ledger.load_rows(str(project))[0]
    assert row["op"] == "plan_change"
    assert row["params"]["operation_type"] == "story_pacing"
    assert row["params"]["owner"] == "mesh_spine"
    assert row["anchor"]["phrase"] == phrase
    assert row["params"]["values"]["opening_structure"]["value"] == \
        "cold_open_then_intro"

    routed = marker_routing.route_project(str(project))
    assert len(routed) == 1
    assert routed[0].steps == ["mesh_spine"]
    assert routed[0].basis == marker_routing.BASIS_EDIT_SPEC
    operation = marker_routing.prompt_block(
        routed, node_id="mesh_spine")["notes"][0]["typed_operations"][0]
    assert operation["op"] == "story_pacing"
    assert operation["owner"] == "mesh_spine"
    assert operation["anchor"]["phrase"] == phrase
    assert operation["params"]["opening_structure"]["value"] == \
        "cold_open_then_intro"
    assert "passage_ref" not in operation["params"]

    manifest = json.loads(Path(
        "library/steps/step_2_05_mesh_spine/manifest.json"
    ).read_text(encoding="utf-8"))
    marker_routing.assert_deliverable("mesh_spine", manifest, routed)
    handoff = Path(
        "library/steps/step_2_05_mesh_spine/handoff.md"
    ).read_text(encoding="utf-8")
    assert "cold_open_then_intro" in handoff
    assert "anchor.phrase" in handoff
    assert "actual body sequence" in " ".join(handoff.split())


def test_empty_story_pacing_values_are_still_refused(tmp_path):
    """A resolved structural note cannot pass translation without values."""
    from library.tools import llm_handshake

    request = (
        "open on the line about quitting every single day, then go back to "
        "the intro")
    project = _project(tmp_path)
    request_id, _request_path, note_id = edit_spec.prepare_request(
        str(project), request)
    response = {
        "format": edit_spec.FORMAT,
        "request": request,
        "source_note_id": note_id,
        "clauses": [{
            "id": "c1",
            "text": request,
            "op": "story_pacing",
            "op_source": "requester",
            "status": "resolved",
            "anchor": {"kind": "words", "phrase": "the requested line"},
            "anchor_source": "requester",
            "values": {},
        }],
    }
    Path(llm_handshake.response_path(str(project), request_id)).write_text(
        json.dumps(response), encoding="utf-8")

    with pytest.raises(
            edit_spec.EditSpecError,
            match="story_pacing needs one or more typed operation values"):
        edit_spec.load_response_spec(str(project), request_id)
    assert edit_ledger.load_rows(str(project)) == []
