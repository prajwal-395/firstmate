"""The handshake refuses malformed responses with the fix; the chat
interview asks in chat instead of the summary; the marker hook suggests
a verb linked to the marker it names.
"""
from __future__ import annotations
import json
import threading
import time
from pathlib import Path
import pytest
from library.tools import briefing_chat, llm_handshake
import os
from library.tools import review_gate


def test_publishing_retry_request_hides_old_request_before_clearing_response(
        tmp_path, monkeypatch):
    request = tmp_path / "llm_requests" / "mesh_spine.json"
    response = tmp_path / "llm_responses" / "mesh_spine.json"
    request.parent.mkdir()
    response.parent.mkdir()
    request.write_text('{"context": "old"}', encoding="utf-8")
    response.write_text('{"answer": "old"}', encoding="utf-8")

    original_unlink = Path.unlink
    saw_response_clear = []

    def observe_unlink(path, *args, **kwargs):
        if path == response:
            saw_response_clear.append(True)
            assert not request.exists(), (
                "the previous request remained visible after its response "
                "was cleared")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", observe_unlink)
    new_payload = {"context": "retry includes violation"}
    llm_handshake.publish_request(request, response, new_payload)

    assert saw_response_clear == [True]
    assert not response.exists()
    assert json.loads(request.read_text(encoding="utf-8")) == new_payload
    assert list(request.parent.iterdir()) == [request]


def test_validate_response_accepts_an_object_and_refuses_the_rest():
    """Every malformed answer refuses with its reason, the response path
    and how to resume; a JSON object is accepted as-is."""
    assert llm_handshake.validate_response(
        "creative_direction", '{"a": 1}', "/tmp/proj") == {"a": 1}
    for raw, reason in (("  ", None), ("{oops", "not JSON"),
                        ("[1, 2]", "not an object")):
        with pytest.raises(llm_handshake.HandshakeRefusal) as exc:
            llm_handshake.validate_response("creative_direction", raw,
                                            "/tmp/proj")
        message = str(exc.value)
        assert "llm_responses/creative_direction.json" in message, raw
        assert "ren edit" in message, raw
        if reason:
            assert reason in message


def test_interview_only_without_brief_and_with_host():
    assert briefing_chat.should_interview(False, "agent") is True
    assert briefing_chat.should_interview(True, "agent") is False
    assert briefing_chat.should_interview(False, "api") is False
    assert briefing_chat.should_interview(False, "mock") is False


def test_banked_questions_first_starters_on_a_first_run():
    prior = [{"step_id": "music_selection",
              "entries": [{"question": "What tempo?"}]}]
    assert briefing_chat.collect_questions(prior) == [
        {"step_id": "music_selection", "question": "What tempo?",
         "why_it_matters": "", "what_assumed_instead": ""}]
    questions = briefing_chat.collect_questions(None)
    assert len(questions) >= 1
    assert all(q["question"] for q in questions)


def test_briefing_selects_only_unresolved_decision_changing_questions():
    records = [{"step_id": "creative_direction", "entries": [
        {"question": "Who is this video for, and what should they do or feel after watching it?",
         "why_it_matters": "changes audience and outcome",
         "what_you_assumed_instead": "a general audience"},
        {"question": "Who is this video for, and what should they do or feel after watching it?",
         "why_it_matters": "duplicate", "what_you_assumed_instead": ""},
        {"question": "What editing software should we use?",
         "why_it_matters": "method only", "what_you_assumed_instead": ""},
        {"question": "What should viewers remember?",
         "why_it_matters": "changes the creative direction",
         "what_you_assumed_instead": "a useful overview"},
    ]}]
    selected = briefing_chat.unresolved_questions(
        records, {"values": {"audience": "new parents"},
                  "influenced_by": {"audience": {"source": "project"}}})
    assert [q["question"] for q in selected] == [
        "What should viewers remember?"]

    inferred = briefing_chat.unresolved_questions(
        records[:1], {"values": {"audience": "new parents"},
                      "influenced_by": {"audience": {"source": "inference"}}})
    assert inferred


def test_catalog_inventory_is_compact_and_uses_catalog_facts():
    state = {"capability_outputs": {"footage.catalog": {
        "clip_catalog": [
            {"path": "/footage/interview.mov", "duration_seconds": 12.5},
            {"filename": "b-roll.mp4", "duration_seconds": 4},
        ]}}}
    assert briefing_chat.footage_inventory(state) == [
        "2 video clip(s) cataloged.",
        "interview.mov (12.5 seconds)",
        "b-roll.mp4 (4 seconds)",
    ]


def test_interview_answer_without_key_refuses():
    with pytest.raises(llm_handshake.HandshakeRefusal):
        briefing_chat.parse_answer({"something_else": []})


def test_interview_round_trip_through_the_handshake(tmp_path):
    """The host asks in chat and answers back through the files."""
    project = tmp_path / "proj"
    project.mkdir()
    state = {
        "project_folder": str(project),
        "project_config": {"audience": "new parents"},
        "capability_outputs": {"footage.catalog": {"clip_catalog": [
            {"path": "/footage/first-steps.mp4", "duration_seconds": 18},
        ]}},
    }

    def host():
        deadline = time.time() + 25
        req = project / "pipeline_output" / "llm_requests" / \
            "briefing_interview.json"
        res = project / "pipeline_output" / "llm_responses" / \
            "briefing_interview.json"
        while time.time() < deadline:
            if req.exists():
                request = json.loads(req.read_text(encoding="utf-8"))
                assert request["kind"] == "briefing_interview"
                assert request["footage_inventory"] == [
                    "1 video clip(s) cataloged.", "first-steps.mp4 (18 seconds)"]
                assert all("audience" not in q["question"].lower()
                           for q in request["questions"])
                assert request["assumptions"]
                assert "assumed default" in request["prompt"]
                assert "a general audience" not in request["prompt"]
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps(
                    {"brief_answers": [
                        {"question": "Who is this for?",
                         "answer": "New parents"}]}), encoding="utf-8")
                return
            time.sleep(0.05)

    thread = threading.Thread(target=host, daemon=True)
    thread.start()
    out = briefing_chat.conduct_if_needed(
        str(project), state, "agent", llm_timeout=30, save=lambda p, s: None)
    thread.join(timeout=30)
    assert out["brief_answers"] == [
        {"question": "Who is this for?", "answer": "New parents"}]
    assert out["briefing_assumptions"]
    assert all("assumed_default" in row for row in out["briefing_assumptions"])


def test_the_interview_never_blocks_or_reasks(tmp_path):
    """No host, no hang: the run continues exactly as a brief-less one;
    and a run that already holds answers is never asked again."""
    project = tmp_path / "proj"
    project.mkdir()
    out = briefing_chat.conduct_if_needed(
        str(project), {}, "agent", llm_timeout=0, save=lambda p, s: None)
    assert out["brief_answers"] == []
    assert out["briefing_assumptions"]
    assert all(row["creator_answered"] is False
               for row in out["briefing_assumptions"])

    project = tmp_path / "proj2"
    project.mkdir()
    state = {"brief_answers": []}
    out = briefing_chat.conduct_if_needed(
        str(project), state, "agent", llm_timeout=0,
        save=lambda p, s: None)
    assert out is state
    assert not (project / "pipeline_output" / "llm_requests" /
                "briefing_interview.json").exists()


def test_hook_suggests_a_verb_per_note(tmp_path):
    import ren.hooks.marker_hook as hook
    project = tmp_path / "proj"
    marker = project / "marker_feedback"
    marker.mkdir(parents=True)
    (marker / "pull-1.json").write_text(json.dumps(
        {"notes": [{"text": "lower the caption a touch"},
                   {"name": "Trim", "note": "tighten the head"}]}),
        encoding="utf-8")
    notes = hook.pending_notes(str(project))
    assert [n["text"] for n in notes] == [
        "lower the caption a touch", "Trim tighten the head"]
    assert "touch" in hook.suggest_verb(notes[0]["text"])


def test_hook_suggestion_links_the_marker_it_names(tmp_path):
    """The printed command translates THIS marker, not a copy of it.

    Catches: the hook printing `ren spec prepare --request ...` with no
    `--note-id`, so running it filed a duplicate note while the marker
    itself stayed untranslated and kept holding the run.
    """
    import shlex

    import ren.hooks.marker_hook as hook
    from library.tools import marker_routing

    project = tmp_path / "proj"
    marker = project / "marker_feedback"
    marker.mkdir(parents=True)
    raw = {"source": "timeline_marker", "frame": 120,
           "text": "cut the long pause"}
    pull = marker / "pull-1.json"
    pull.write_text(json.dumps({"timeline": "Reel 01", "notes": [raw]}),
                    encoding="utf-8")
    [note] = hook.pending_notes(str(project))
    expected = marker_routing.edit_link_id(raw, "Reel 01", str(pull))
    assert note["note_id"] == expected
    command = hook.suggest_verb(note["text"], note["note_id"], str(project))
    assert f"--note-id {shlex.quote(expected)}" in command


# --------------------------------------------------------------------------
# From test_review_gate_ids.py
#
# A gate id becomes a directory name, so it may not be a path.
#
# This lands with the change that makes it REACHABLE. Until operation
# breakpoints, every gate id came from the DAG; `--break <address>` makes
# it something an operator types and `/api/gates/{step_id}` makes it
# something an HTTP path carries.

@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(
        json.dumps({"project_folder": str(folder)}), encoding="utf-8")
    return folder


def test_a_step_id_and_an_operation_address_both_work(project):
    for gate_id in ("render", "subtitles.render@45.0-72.0"):
        path = review_gate.save_gate_snapshot(str(project), gate_id, "x", {})
        assert os.path.realpath(path).startswith(os.path.realpath(project))
        assert review_gate.get_gate_status(str(project), gate_id) == "pending"
    assert set(review_gate.list_pending_gates(str(project))) == {
        "render", "subtitles.render@45.0-72.0"}


def test_an_id_that_is_a_path_is_refused_for_writes_and_reads(project):
    """Measured before this check existed: '../../../outside' wrote
    `outside/snapshot.json` OUTSIDE the project directory entirely. A read
    that built the path would leave the project too."""
    with pytest.raises(review_gate.UnsafeGateId):
        review_gate.save_gate_snapshot(str(project), "../../../outside", "x", {})
    for call in (review_gate.get_gate_status,
                 review_gate.load_gate_snapshot,
                 review_gate.load_gate_feedback):
        with pytest.raises(review_gate.UnsafeGateId):
            call(str(project), "../../../outside")
