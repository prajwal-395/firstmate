"""Feedback outcomes use the project's captured pull and resolution shapes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import feedback_outcomes as outcomes  # noqa: E402
from library.tools import feedback_ledger, marker_feedback  # noqa: E402
from library.tools import marker_resolution  # noqa: E402
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402


def _note(**changes):
    note = {
        "note_id": "Reel_07:timeline_marker:1256",
        "stable_note_id": "",
        "source": "timeline_marker",
        "name": "feedback",
        "note": "Keep the lower third clear of the speaker's hand.",
        "text": "feedback\n\nKeep the lower third clear of the speaker's hand.",
        "frame": 1256,
        "frame_in_timeline_space": 1256,
        "timecode": "00:00:41:20",
        "timeline": "Reel 07",
        "pull_file": "Reel_07.2026-10-01T12-00-00Z.markers.json",
        "collected_at": "2026-10-01T12:00:00+00:00",
    }
    note.update(changes)
    return note


def _write_pull(project, filename, timeline, pulled_at, notes):
    layout = ProjectLayout(project)
    path = layout.write_path(Area.MARKER_FEEDBACK, filename)
    path.write_text(json.dumps({
        "format": marker_feedback.PULL_FORMAT,
        "pulled_at": pulled_at,
        "resolve_project": "Ren Qualification",
        "timeline": timeline,
        "timeline_start_frame": 0,
        "timeline_fps": 30.0,
        "note_count": len(notes),
        "notes": notes,
    }, indent=2) + "\n", encoding="utf-8")
    return path


def test_resolution_writes_the_verbatim_note_anchor_and_decline_reason(tmp_path):
    note = _note()
    record = marker_resolution.record_resolution(
        str(tmp_path), note, marker_resolution.STATUS_DECLINED,
        action="decline: keep the original framing",
        rationale="The hand crosses the lower third only in the chosen take.",
    )

    events = outcomes.read_events(str(tmp_path))
    assert len(events) == 1
    event = events[0]
    assert event["note"]["text"] == note["text"]
    assert event["anchor"] == {
        "source": note["source"], "timeline": note["timeline"],
        "frame": note["frame"],
        "frame_in_timeline_space": note["frame_in_timeline_space"],
        "timecode": note["timecode"], "pull_file": note["pull_file"],
        "collected_at": note["collected_at"],
    }
    assert event["outcome_id"] == record["outcome_id"]
    assert event["action"]["kind"] == "declined"
    assert event["verification"]["state"] == outcomes.VERIFICATION_NOT_DONE
    assert event["not_done"]["reason"] == record["rationale"]


def test_rebuild_plan_and_blocked_outcome_keep_distinct_states(tmp_path):
    note = _note()
    plan = outcomes.record_plan(
        str(tmp_path), note, action_kind="rebuild",
        summary="Move the lower third above the speaker's hand.",
        outcome_id="planned-1")
    blocked = outcomes.record_blocked(
        str(tmp_path), note, reason="The note could not be matched to a clip.",
        action_kind="rebuild", outcome_id="blocked-1")

    assert plan["verification"]["state"] == outcomes.VERIFICATION_PLANNED
    assert plan["not_done"] is None
    assert blocked["verification"]["state"] == outcomes.VERIFICATION_NOT_DONE
    assert blocked["not_done"]["reason"] == (
        "The note could not be matched to a clip.")
    assert {event["outcome_id"] for event in outcomes.read_events(str(tmp_path))} == {
        "planned-1", "blocked-1"}
    assert outcomes._infer_action_kind(
        "addressed_unverified", "", "addressed") == "rebuild"
    assert outcomes._infer_action_kind("blocked", "", "") == "blocked"


def test_legacy_verified_label_without_a_named_check_stays_planned(tmp_path):
    event = outcomes.resolution_event(
        str(tmp_path), {
            "note_id": "legacy-note", "stable_note_id": "",
            "timeline": "Reel 07", "text": "feedback\n\nFix the cut.",
            "status": "resolved_verified", "check": "",
            "evidence": {"verified": "The reel was rebuilt."},
            "resolved_at": "2026-10-01T12:00:00+00:00",
        }, outcome_id="legacy-1", details={"source": "derived"})
    assert event["verification"]["state"] == outcomes.VERIFICATION_PLANNED


def test_backfill_preserves_pull_anchor_and_does_not_infer_timeline_readback(
        tmp_path):
    timeline = "Reel 07"
    note_text = "feedback\n\nKeep the lower third clear of the speaker's hand."
    note = {
        "source": "timeline_marker", "name": "feedback",
        "note": "Keep the lower third clear of the speaker's hand.",
        "text": note_text, "frame": 1256,
        "frame_in_timeline_space": 1256,
        "timecode": "00:00:41:20", "custom_data": {},
        "custom_data_raw": "", "note_id": "",
    }
    _write_pull(tmp_path, "Reel_07.2026-10-01T12-00-00Z.markers.json",
                timeline, "2026-10-01T12:00:00+00:00", [note])
    ask_id = feedback_ledger.durable_identity(timeline, note_text)
    reply_custom = marker_feedback.reply_custom_data(
        answers=ask_id, answers_text=note_text,
        summary="Moved the lower third above the hand.")
    reply = {
        "source": "timeline_marker", "name": "reply: lower third moved",
        "note": "DONE: Moved the lower third above the hand.",
        "text": "reply: lower third moved\n\nDONE: Moved the lower third above the hand.",
        "frame": 1257, "frame_in_timeline_space": 1257,
        "timecode": "00:00:41:21",
        "custom_data": json.loads(reply_custom),
        "custom_data_raw": reply_custom, "note_id": "",
    }
    _write_pull(tmp_path, "Reel_07.2026-10-02T12-00-00Z.markers.json",
                timeline, "2026-10-02T12:00:00+00:00", [reply])

    result = outcomes.backfill(str(tmp_path), from_reel=7, to_reel=7)
    assert result["created"] == 1
    event = result["events"][0]
    assert event["source"] == "derived"
    assert event["note_identity"] == ask_id
    assert event["note"]["text"] == note_text
    assert event["anchor"]["frame_in_timeline_space"] == 1256
    assert event["anchor"]["timecode"] == "00:00:41:20"
    assert event["action"]["summary"] == (
        "Moved the lower third above the hand.")
    assert event["verification"]["state"] == outcomes.VERIFICATION_PLANNED
    assert event["not_done"] is None
    again = outcomes.backfill(str(tmp_path), from_reel=7, to_reel=7)
    assert again["created"] == 1
    assert len(outcomes.read_events(str(tmp_path))) == 1

    document = outcomes.build(str(tmp_path), from_reel=7, to_reel=7)
    assert len(document["notes"]) == 1
    assert document["notes"][0]["anchor"]["frame_in_timeline_space"] == 1256
    assert "timeline_readback" not in outcomes.render_text(document)
    assert "Feedback outcomes" in outcomes.render_html(document)


def test_ren_command_filters_reels_and_emits_html(tmp_path, capsys):
    note = {
        "source": "timeline_marker", "name": "feedback",
        "note": "Keep the lower third clear.",
        "text": "feedback\n\nKeep the lower third clear.",
        "frame": 1256, "frame_in_timeline_space": 1256,
        "timecode": "00:00:41:20", "custom_data": {},
        "custom_data_raw": "", "note_id": "",
    }
    _write_pull(tmp_path, "Reel_07.2026-10-01T12-00-00Z.markers.json",
                "Reel 07", "2026-10-01T12:00:00+00:00", [note])

    assert outcomes.main([str(tmp_path), "--from-reel", "7", "--to-reel",
                          "7", "--html"]) == 0
    report = capsys.readouterr().out
    assert "Feedback outcomes" in report
    assert "Keep the lower third clear." in report
    assert "Frame 1256" in report


def test_report_renders_snapshot_shas_and_missing_after_version():
    document = {
        "project": "/project",
        "outcome_count": 1,
        "notes": [{
            "reel": "Reel 01",
            "identity": "note-1",
            "text": "Fix the caption.",
            "first_asked": "2026-10-01T12:00:00+00:00",
            "anchor": {"frame_in_timeline_space": 42},
            "outcomes": [{
                "source": "derived",
                "status": "addressed_unverified",
                "action": {"kind": "rebuild", "summary": "Caption updated."},
                "verification": {"state": "not_done"},
                "store_commits": [{
                    "sha": "65348c7fdb833db9", "subject": "Reel 01 rebuild",
                    "relation": "committed snapshot of this reel",
                }],
                "reel_versions": {
                    "before": {"sha": "1517388827887deb", "committed_at": "2026-10-01"},
                    "after": {"sha": "65348c7fdb833db9", "committed_at": "2026-10-02"},
                },
            }],
        }],
    }

    text = outcomes.render_text(document)
    html = outcomes.render_html(document)
    assert "151738882788 (2026-10-01) -> 65348c7fdb83 (2026-10-02)" in text
    assert "committed snapshot of this reel" in text
    assert "151738882788" in html
    assert "65348c7fdb83" in html
    assert "committed snapshot of this reel" in html

    document["notes"][0]["outcomes"][0]["reel_versions"]["after"] = None
    text_without_after = outcomes.render_text(document)
    assert "151738882788 (2026-10-01) -> not recorded" in text_without_after
