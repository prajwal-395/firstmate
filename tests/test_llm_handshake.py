"""The handshake refuses malformed responses with the fix; the chat
interview asks in chat instead of the summary; the marker hook is
disk-only.

Each test names the defect it pins: a malformed handshake response
silently accepted (or failing with no path to repair), an interview
that never reaches the user, a hook that probes Resolve at session
start.
"""

import json
import threading
import time

import pytest

from library.tools import briefing_chat, llm_handshake


def test_valid_object_is_accepted():
    assert llm_handshake.validate_response(
        "creative_direction", '{"a": 1}', "/tmp/proj") == {"a": 1}


def test_empty_response_refuses_with_path_and_resume():
    with pytest.raises(llm_handshake.HandshakeRefusal) as exc:
        llm_handshake.validate_response("creative_direction", "  ",
                                        "/tmp/proj")
    message = str(exc.value)
    assert "creative_direction" in message
    assert "llm_responses/creative_direction.json" in message
    assert "ren edit" in message


def test_non_json_refuses_with_the_reason():
    with pytest.raises(llm_handshake.HandshakeRefusal) as exc:
        llm_handshake.validate_response("plan_vfx", "{oops", "/tmp/proj")
    assert "not JSON" in str(exc.value)


def test_non_object_refuses():
    """A list is valid JSON and still no step's answer."""
    with pytest.raises(llm_handshake.HandshakeRefusal) as exc:
        llm_handshake.validate_response("plan_vfx", "[1, 2]", "/tmp/proj")
    assert "not an object" in str(exc.value)


def test_interview_only_without_brief_and_with_host():
    assert briefing_chat.should_interview(False, "agent") is True
    assert briefing_chat.should_interview(True, "agent") is False
    assert briefing_chat.should_interview(False, "api") is False
    assert briefing_chat.should_interview(False, "mock") is False


def test_first_run_falls_back_to_starters():
    questions = briefing_chat.collect_questions(None)
    assert len(questions) >= 1
    assert all(q["question"] for q in questions)


def test_prior_banked_questions_are_asked_first():
    prior = [{"step_id": "music_selection",
              "entries": [{"question": "What tempo?"}]}]
    assert briefing_chat.collect_questions(prior) == [
        {"step_id": "music_selection", "question": "What tempo?",
         "why_it_matters": "", "what_assumed_instead": ""}]


def test_interview_answer_without_key_refuses():
    with pytest.raises(llm_handshake.HandshakeRefusal):
        briefing_chat.parse_answer({"something_else": []})


def test_interview_round_trip_through_the_handshake(tmp_path):
    """The host asks in chat and answers back through the files."""
    project = tmp_path / "proj"
    project.mkdir()
    state = {"project_folder": str(project)}

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
                assert request["questions"]
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


def test_unanswered_interview_proceeds_briefless(tmp_path):
    """No host, no hang: the run continues exactly as a brief-less one."""
    project = tmp_path / "proj"
    project.mkdir()
    out = briefing_chat.conduct_if_needed(
        str(project), {}, "agent", llm_timeout=0, save=lambda p, s: None)
    assert out["brief_answers"] == []


def test_answered_once_is_never_reasked(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    state = {"brief_answers": []}
    out = briefing_chat.conduct_if_needed(
        str(project), state, "agent", llm_timeout=0,
        save=lambda p, s: None)
    assert out is state
    assert not (project / "pipeline_output" / "llm_requests" /
                "briefing_interview.json").exists()


def test_hook_check_is_disk_only(tmp_path, monkeypatch):
    """No Resolve import, no probe: an empty root reports nothing."""
    import ren.hooks.marker_hook as hook
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    assert hook.cmd_check(type("A", (), {"projects_root": ""})()) == 0


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
