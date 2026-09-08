"""Stamping each clip with the decision that produced it.

The producer side of the captain's note loop.  `marker_feedback` reads
their typed notes, `marker_routing` sends each to the step that owns it,
and this is the half that lets the marker itself carry the answer instead
of the routing having to infer one.

THE SHAPES HERE ARE REAL.  `MANIFEST` is 001's own assembly manifest,
trimmed to the placements the captain's three notes actually sit on, with
its real labels, frames and source ranges; the three notes are imported
verbatim from `test_marker_routing`, where they were copied out of the
pull file `marker_feedback` wrote on 2026-08-28.  A stamping test written
against invented clips proves the stamp agrees with whoever wrote the
fixture.

No Resolve.  The one call this module makes into Resolve -
`UpdateMarkerCustomData` - is driven against a fake that records what it
was asked to do and REFUSES to add a marker, because never adding one is
the property that keeps the captain's timeline looking the way they left
it.  Nothing here reaches a real project: everything is under `tmp_path`.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import marker_payload, marker_routing  # noqa: E402
from library.tools import timeline_decisions as td  # noqa: E402
from library.tools.marker_feedback import PULL_FILE_SUFFIX  # noqa: E402
from library.tools.project_layout import (  # noqa: E402
    STEP_BY_ID,
    Area,
    ProjectLayout,
)

from tests.test_marker_routing import (  # noqa: E402
    AMBIGUOUS_NOTE,
    CLIP_NOTE,
    MOMENT_NOTE,
)

FPS = 30.0


def _seconds(frame):
    return frame / FPS


# ── 001's manifest, at the placements the notes sit on ──────────────

MANIFEST = {
    "project": {"name": "Pipeline_Edit", "frame_rate": FPS,
                "resolution": {"width": 1080, "height": 1920}},
    "tracks": {
        "V1": {"label": "A-Roll", "clips": [
            {"label": "hook_hook", "source_file": "/p/001/raw/IMG_1816.MOV",
             "source_in": _seconds(25), "source_out": _seconds(97),
             "timeline_in": 0.0, "timeline_out": _seconds(72),
             "timeline_in_frame": 0, "timeline_out_frame": 72},
            {"label": "speech_7_seg0", "source_file": "/p/001/raw/IMG_1816.MOV",
             "source_in": _seconds(3016), "source_out": _seconds(3495),
             "timeline_in": _seconds(559), "timeline_out": _seconds(1038),
             "timeline_in_frame": 559, "timeline_out_frame": 1038},
            {"label": "speech_9_seg0", "source_file": "/p/001/raw/IMG_1817.MOV",
             "source_in": _seconds(654), "source_out": _seconds(725),
             "timeline_in": _seconds(1164), "timeline_out": _seconds(1235),
             "timeline_in_frame": 1164, "timeline_out_frame": 1235},
        ]},
        "V2": {"label": "B-Roll", "clips": [
            {"label": "interjection_7", "source_file": "/p/001/raw/IMG_1811.MOV",
             "source_in": _seconds(50), "source_out": _seconds(125),
             "timeline_in": _seconds(705), "timeline_out": _seconds(780),
             "timeline_in_frame": 705, "timeline_out_frame": 780,
             "video_only": True},
        ]},
        "A2": {"label": "Music", "clips": [
            {"label": "background_music",
             "source_file": "/p/001/music/_background music_ rise - "
                            "uplifting piano _inspiring _ beautiful_ _ _ "
                            "motivation.wav",
             "source_in": 0.0, "source_out": 59.437,
             "timeline_in": 0.0, "timeline_out": 59.437},
        ]},
        "A3": {"label": "SFX", "clips": [
            {"label": "sfx_001", "sfx_id": "whoosh_impact",
             "source_file": "/p/sfx/whoosh_impact.mp3",
             "source_in": 0.714,
             "timeline_in": 34.615, "timeline_out": 34.865,
             "timeline_in_frame": 1038, "timeline_out_frame": 1046},
        ]},
    },
    "subtitle_overlay": {"segments": [
        {"overlay_path": "/p/001/pipeline_output/steps/4_05_render_subtitles/"
                         "sub_block_hook.mov",
         "timeline_start": 0.0, "timeline_end": _seconds(72),
         "source_in_frame": 0, "source_out_frame": 72},
        {"overlay_path": "/p/001/pipeline_output/steps/4_05_render_subtitles/"
                         "sub_block_7.mov",
         "timeline_start": _seconds(559), "timeline_end": _seconds(1038),
         "source_in_frame": 15, "source_out_frame": 495},
        {"overlay_path": "/p/001/pipeline_output/steps/4_05_render_subtitles/"
                         "sub_block_9.mov",
         "timeline_start": _seconds(1164), "timeline_end": _seconds(1235),
         "source_in_frame": 15, "source_out_frame": 86},
    ]},
}


def _placement(found, label):
    hits = [p for p in found if p.label == label]
    assert len(hits) == 1, f"{label!r} matched {len(hits)} placements"
    return hits[0]


# ── The enumeration ─────────────────────────────────────────────────

def test_every_track_names_a_step_the_dag_really_has():
    for row in td.TRACK_DECISIONS:
        assert row.decided_by in STEP_BY_ID, (
            f"{row.track} claims {row.decided_by!r}, which is not a step in "
            f"project_layout.STEPS")


def test_every_track_names_a_step_a_note_can_be_routed_to():
    # The two enumerations have to agree or a stamp routes to a step
    # `marker_routing` cannot deliver to.
    for row in td.TRACK_DECISIONS:
        assert row.decided_by in marker_routing.BY_NODE_ID, (
            f"{row.track} is decided by {row.decided_by!r}, which has no row "
            f"in marker_routing.STEP_DECISIONS - a note stamped with it "
            f"could be routed nowhere")


def test_every_track_states_what_it_is_and_where_the_decision_is_written():
    for row in td.TRACK_DECISIONS:
        assert row.what.strip()
        assert row.locator.strip()
        assert row.basis in td.DECISION_BASES


def test_the_table_has_no_duplicate_track():
    tracks = [row.track for row in td.TRACK_DECISIONS]
    assert len(tracks) == len(set(tracks))


def test_every_unstamped_reason_says_why():
    for key, reason in td.UNSTAMPED_PLACEMENTS.items():
        assert len(reason) > 60, f"{key} does not explain itself"


# ── Reading the manifest ────────────────────────────────────────────

def test_every_placement_of_the_manifest_is_surveyed():
    found = td.placements(MANIFEST)
    assert len(found) == 3 + 1 + 1 + 1 + 3


def test_the_a_roll_is_the_step_that_chose_the_passage_not_the_transform():
    # 3.01 assign_aroll copies block["clip_id"] and block["source_start"]
    # straight off the spine; 2.02 is what chose them.
    clip = _placement(td.placements(MANIFEST), "speech_9_seg0")
    assert clip.step == "speech_sequence"
    assert clip.basis == td.BASIS_CHOSEN
    assert "assign_aroll" in clip.why_this_step


@pytest.mark.parametrize("label,step", [
    ("interjection_7", "select_broll"),
    ("background_music", "music_selection"),
    ("sfx_001", "plan_sfx"),
    ("sub_block_9", "plan_subtitles"),
])
def test_each_track_reaches_the_step_that_decided_it(label, step):
    assert _placement(td.placements(MANIFEST), label).step == step


def test_a_bookend_card_is_not_stamped_and_says_why():
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["tracks"]["V1"]["clips"].append({
        "label": "bookend_outro", "bookend": "outro",
        "source_file": "/p/001/assets/outro.mov",
        "source_in": 0.0, "source_out": 2.0,
        "timeline_in": 59.4, "timeline_out": 61.4,
        "timeline_in_frame": 1783, "timeline_out_frame": 1843,
    })
    card = _placement(td.placements(manifest), "bookend_outro")
    assert card.stamped is False
    assert card.step == ""
    assert card.unstamped_reason == td.UNSTAMPED_PLACEMENTS["bookend_card"]
    assert card.decision_id == ""


def test_an_unknown_track_is_reported_not_guessed():
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["tracks"]["V9"] = {"label": "?", "clips": [
        {"label": "mystery", "source_file": "/p/x.mov",
         "timeline_in_frame": 0, "timeline_out_frame": 10}]}
    mystery = _placement(td.placements(manifest), "mystery")
    assert mystery.stamped is False
    assert mystery.step == ""
    assert mystery.unstamped_reason == td.UNSTAMPED_PLACEMENTS["unknown_track"]


def test_the_linked_speech_track_is_not_a_second_placement():
    # A1 is built from the same V1 clip dicts. Counting it would stamp
    # two records where the timeline has one clip.
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["tracks"]["A1"] = {
        "label": "Speech", "clips": manifest["tracks"]["V1"]["clips"]}
    assert len(td.placements(manifest)) == len(td.placements(MANIFEST))
    assert td.LINKED_AUDIO_OF["A1"] == "V1"


def test_a_repeated_label_still_gets_a_unique_decision_id():
    manifest = json.loads(json.dumps(MANIFEST))
    second = json.loads(json.dumps(manifest["tracks"]["V2"]["clips"][0]))
    second.update({"source_file": "/p/001/raw/IMG_1809.MOV",
                   "timeline_in_frame": 900, "timeline_out_frame": 975,
                   "source_in": _seconds(682), "source_out": _seconds(758)})
    manifest["tracks"]["V2"]["clips"].append(second)
    ids = [p.decision_id for p in td.placements(manifest) if p.stamped]
    assert len(ids) == len(set(ids))
    assert "select_broll/interjection_7#2" in ids


def test_the_route_to_the_reasoning_names_the_producing_step(tmp_path):
    clip = _placement(td.placements(MANIFEST, tmp_path), "interjection_7")
    assert clip.routes["output"] == (
        "pipeline_output/steps/3_02_select_broll/output.json")


def test_a_prompt_and_an_answer_are_named_only_when_they_are_on_disk(tmp_path):
    assert "answer" not in _placement(
        td.placements(MANIFEST, tmp_path), "interjection_7").routes
    answer = tmp_path / "pipeline_output" / "llm_responses" / "select_broll.json"
    answer.parent.mkdir(parents=True)
    answer.write_text("{}", encoding="utf-8")
    assert _placement(td.placements(MANIFEST, tmp_path),
                      "interjection_7").routes["answer"] == (
        "pipeline_output/llm_responses/select_broll.json")


# ── The ledger ──────────────────────────────────────────────────────

def test_the_ledger_is_written_into_the_render_step_s_own_directory(tmp_path):
    path = td.write_ledger(tmp_path, MANIFEST)
    assert path.parent.name == STEP_BY_ID["render"].dirname
    assert path.name == td.LEDGER_FILENAME
    ledger = td.read_ledger(tmp_path)
    assert ledger["format"] == td.LEDGER_FORMAT
    assert ledger["timeline"] == "Pipeline_Edit"
    assert len(ledger["placements"]) == 9


def test_a_project_with_no_ledger_reads_as_empty_not_as_an_error(tmp_path):
    assert td.read_ledger(tmp_path) == {}


# ── Matching a marker's clip back to a placement ────────────────────

def test_every_clip_under_the_captain_s_notes_resolves_to_a_placement(tmp_path):
    td.write_ledger(tmp_path, MANIFEST)
    ledger = td.read_ledger(tmp_path)
    for note in (MOMENT_NOTE, CLIP_NOTE, AMBIGUOUS_NOTE):
        for clip in note["clips"]:
            assert td.placement_for_clip(ledger, clip) is not None, (
                f"{clip['name']} at {clip['timeline_start']} matched nothing")


def test_a_linked_audio_item_resolves_to_the_same_placement_as_its_video(tmp_path):
    td.write_ledger(tmp_path, MANIFEST)
    ledger = td.read_ledger(tmp_path)
    video, audio = [c for c in AMBIGUOUS_NOTE["clips"]
                    if c["name"] == "IMG_1817.MOV"]
    assert td.placement_for_clip(ledger, video)["decision_id"] == \
        td.placement_for_clip(ledger, audio)["decision_id"]


def test_a_clip_from_another_build_matches_nothing(tmp_path):
    td.write_ledger(tmp_path, MANIFEST)
    ledger = td.read_ledger(tmp_path)
    assert td.placement_for_clip(ledger, {
        "source_file": "/p/001/raw/IMG_9999.MOV",
        "source_start": 10, "source_end": 20, "timeline_start": 0}) is None


# ── The stamp, against a fake Resolve ───────────────────────────────

class FakeTimeline:
    """Only what stamping touches, and it refuses what stamping must not do."""

    def __init__(self, name, markers):
        self._name = name
        self.markers = {k: dict(v) for k, v in markers.items()}
        self.refuse_frames = set()

    def GetName(self):
        return self._name

    def GetMarkers(self):
        return {k: dict(v) for k, v in self.markers.items()}

    def UpdateMarkerCustomData(self, frame, data):
        if frame in self.refuse_frames or frame not in self.markers:
            return False
        self.markers[frame]["customData"] = data
        return True

    def AddMarker(self, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError(
            "stamping must never create a marker: a marker is drawn on the "
            "timeline ruler, and the captain did not ask for one")


CAPTAIN_MARKER = {
    "color": "Blue",
    "duration": 1,
    "note": "this clip is honestly like broll of nothing, im confused why "
            "it was chosen and added here",
    "name": "Marker 1",
    "customData": "",
}


def _stamped(tmp_path, markers, frames_refused=()):
    td.write_ledger(tmp_path, MANIFEST)
    timeline = FakeTimeline("Pipeline_Edit", markers)
    timeline.refuse_frames = set(frames_refused)
    report = td.stamp_timeline(timeline, td.read_ledger(tmp_path))
    return timeline, report


def test_a_hand_written_note_survives_stamping_byte_for_byte(tmp_path):
    before = dict(CAPTAIN_MARKER)
    timeline, report = _stamped(tmp_path, {744: dict(CAPTAIN_MARKER)})
    after = timeline.markers[744]
    for field in ("name", "note", "color", "duration"):
        assert after[field] == before[field], f"{field} was changed"
    assert report.markers_stamped == 1
    assert after["customData"] != ""


def test_the_stamp_is_the_only_thing_that_changed(tmp_path):
    timeline, _ = _stamped(tmp_path, {744: dict(CAPTAIN_MARKER)})
    changed = {k for k, v in timeline.markers[744].items()
               if CAPTAIN_MARKER.get(k) != v}
    assert changed == {"customData"}


def test_a_marker_at_a_frame_gets_one_record_per_placement_under_it(tmp_path):
    timeline, report = _stamped(tmp_path, {744: dict(CAPTAIN_MARKER)})
    envelope = marker_payload.parse(timeline.markers[744]["customData"])
    records = marker_payload.records_of(envelope, td.KIND_DECISION)
    # V1 speech_7_seg0, V2 interjection_7, V3 sub_block_7, A2 music.
    assert {r["step"] for r in records} == {
        "speech_sequence", "select_broll", "plan_subtitles", "music_selection"}
    assert report.records_written == len(records) == 4


def test_stamping_twice_replaces_rather_than_accumulates(tmp_path):
    timeline, _ = _stamped(tmp_path, {744: dict(CAPTAIN_MARKER)})
    first = marker_payload.parse(timeline.markers[744]["customData"])
    td.stamp_timeline(timeline, td.read_ledger(tmp_path))
    second = marker_payload.parse(timeline.markers[744]["customData"])
    assert len(second["records"]) == len(first["records"])
    assert ([r["id"] for r in second["records"]]
            == [r["id"] for r in first["records"]])


def test_another_writer_s_record_is_left_alone(tmp_path):
    envelope = marker_payload.new_envelope()
    still = {"kind": marker_payload.KIND_STILL, "writer": "capture_frame",
             "writer_version": 1, "id": "still_1", "at": "2026-08-28T00:00:00Z",
             "path": "marker_feedback/stills/x.png"}
    marker_payload.merge_record(envelope, still)
    marker = dict(CAPTAIN_MARKER, customData=marker_payload.dumps(envelope))
    timeline, _ = _stamped(tmp_path, {744: marker})
    after = marker_payload.parse(timeline.markers[744]["customData"])
    assert marker_payload.records_of(after, marker_payload.KIND_STILL) == [still]


def test_customdata_this_module_does_not_understand_is_kept(tmp_path):
    marker = dict(CAPTAIN_MARKER, customData='{"someone else": "mine"}')
    timeline, _ = _stamped(tmp_path, {744: marker})
    after = marker_payload.parse(timeline.markers[744]["customData"])
    assert after["foreign"] == {"someone else": "mine"}
    assert marker_payload.records_of(after, td.KIND_DECISION)


def test_a_marker_over_nothing_is_left_exactly_as_it_was(tmp_path):
    timeline, report = _stamped(tmp_path, {9000: dict(CAPTAIN_MARKER)})
    assert timeline.markers[9000] == CAPTAIN_MARKER
    assert report.markers_stamped == 0
    assert report.skipped[0]["frame"] == 9000
    assert "no placement" in report.skipped[0]["reason"]


def test_resolve_refusing_the_write_is_reported_not_swallowed(tmp_path):
    timeline, report = _stamped(
        tmp_path, {744: dict(CAPTAIN_MARKER)}, frames_refused=[744])
    assert report.markers_stamped == 0
    assert report.refused[0]["frame"] == 744
    assert timeline.markers[744]["customData"] == ""


def test_the_record_carries_the_identity_and_the_route_to_the_reasoning(tmp_path):
    td.write_ledger(tmp_path, MANIFEST)
    ledger = td.read_ledger(tmp_path)
    clip = [c for c in CLIP_NOTE["clips"] if c["name"] == "IMG_1811.MOV"][0]
    record = td.decision_record(td.placement_for_clip(ledger, clip))
    assert record["kind"] == td.KIND_DECISION
    assert record["step"] == "select_broll"
    assert record["decision_id"] == "select_broll/interjection_7"
    assert record["basis"] == td.BASIS_CHOSEN
    assert record["locator"].startswith("broll_selections.")
    assert record["path"] == (
        "pipeline_output/steps/3_02_select_broll/output.json")
    marker_payload.merge_record(marker_payload.new_envelope(), record)


# ── The routing consumes it ─────────────────────────────────────────

def _project(tmp_path, notes, with_ledger=True):
    layout = ProjectLayout(tmp_path)
    layout.write_path(
        Area.MARKER_FEEDBACK,
        f"Pipeline_Edit.20260828T221238Z{PULL_FILE_SUFFIX}",
    ).write_text(json.dumps({
        "format": "marker_feedback/1", "timeline": "Pipeline_Edit",
        "note_count": len(notes), "notes": notes,
    }), encoding="utf-8")
    if with_ledger:
        td.write_ledger(tmp_path, MANIFEST)
    return str(tmp_path)


UNROUTABLE_WORDS = "this bit just doesn't work for me, can we try something else"


def _wordless(note):
    out = json.loads(json.dumps(note))
    out["name"] = "Marker 1"
    out["note"] = UNROUTABLE_WORDS
    out["text"] = f"Marker 1\n\n{UNROUTABLE_WORDS}"
    return out


def test_a_note_naming_no_step_reaches_the_step_that_produced_its_clip(tmp_path):
    routed = marker_routing.route_project(
        _project(tmp_path, [_wordless(CLIP_NOTE)]))[0]
    assert routed.outcome == marker_routing.OUTCOME_ROUTED
    assert routed.basis == marker_routing.BASIS_STAMPED
    assert routed.steps == ["select_broll"]
    assert routed.decision["decision_id"] == "select_broll/interjection_7"
    assert routed.decision["source"] == "ledger"


def test_without_a_ledger_the_same_note_is_unrouted_and_says_so(tmp_path):
    routed = marker_routing.route_project(
        _project(tmp_path, [_wordless(CLIP_NOTE)], with_ledger=False))[0]
    assert routed.outcome == marker_routing.OUTCOME_UNROUTED
    assert "decision ledger" in routed.reason
    assert routed.steps == []


def test_the_captain_s_own_three_notes_route_exactly_as_they_did(tmp_path):
    routed = marker_routing.route_project(
        _project(tmp_path, [MOMENT_NOTE, CLIP_NOTE, AMBIGUOUS_NOTE]))
    def _one(fragment):
        hits = [n for n in routed if fragment in n.note]
        assert len(hits) == 1, f"{fragment!r} matched {len(hits)} notes"
        return hits[0]

    subtitles = _one("why are the subtitles")
    broll = _one("broll of nothing")
    blurry = _one("fully blurry")
    assert subtitles.steps == ["plan_subtitles"]
    assert subtitles.basis == marker_routing.BASIS_VOCABULARY
    assert broll.steps == ["select_broll"]
    assert broll.basis == marker_routing.BASIS_VOCABULARY
    assert blurry.outcome == marker_routing.OUTCOME_AMBIGUOUS
    assert blurry.steps == ["plan_transitions", "plan_vfx"]


def test_the_stamp_does_not_overrule_the_captain_s_own_words(tmp_path):
    # The measured case: the blurry note sits on a V1 A-roll clip whose
    # stamp is speech_sequence, and a blur is decided in plan_vfx or
    # plan_transitions. A stamp that ranked above the words would have
    # sent it to the step that chose the passage.
    blurry = [n for n in marker_routing.route_project(
        _project(tmp_path, [AMBIGUOUS_NOTE]))][0]
    assert blurry.decision["step"] == "speech_sequence"
    assert "speech_sequence" not in blurry.steps
    assert blurry.outcome == marker_routing.OUTCOME_AMBIGUOUS


def test_a_declared_step_still_outranks_the_stamp(tmp_path):
    note = _wordless(CLIP_NOTE)
    note["note"] += "\nstep: plan_vfx"
    note["text"] += "\nstep: plan_vfx"
    routed = marker_routing.route_project(_project(tmp_path, [note]))[0]
    assert routed.basis == marker_routing.BASIS_DECLARED
    assert routed.steps == ["plan_vfx"]


def test_a_moment_note_is_never_routed_by_the_stamp(tmp_path):
    routed = marker_routing.route_project(
        _project(tmp_path, [_wordless(MOMENT_NOTE)]))[0]
    assert routed.outcome == marker_routing.OUTCOME_UNROUTED
    assert "MOMENT" in routed.decision["reason"]
    # ...and what was playing is still recorded, as context.
    assert {e["step"] for e in routed.decision_context} == {
        "speech_sequence", "plan_subtitles", "music_selection"}


def test_the_marker_s_own_stamp_is_preferred_to_the_ledger(tmp_path):
    note = _wordless(CLIP_NOTE)
    envelope = marker_payload.new_envelope()
    td.stamp_envelope(envelope, [{
        "track": "V2", "label": "interjection_7", "step": "plan_vfx",
        "decision_id": "plan_vfx/written_onto_the_marker",
        "basis": td.BASIS_CHOSEN, "locator": "enhancement_spec",
        "timeline_in_frame": 705, "timeline_out_frame": 780,
        "source_basename": "IMG_1811.MOV", "routes": {},
    }])
    note["custom_data"] = envelope
    routed = marker_routing.route_project(_project(tmp_path, [note]))[0]
    assert routed.decision["source"] == "custom_data"
    assert routed.steps == ["plan_vfx"]


def test_the_report_names_the_decision_behind_the_clip(tmp_path):
    report = marker_routing.render_report(
        _project(tmp_path, [_wordless(CLIP_NOTE)]))
    assert "That clip was produced by" in report
    assert "select_broll/interjection_7" in report
    assert "pipeline_output/steps/3_02_select_broll/output.json" in report


def test_a_step_reading_the_note_is_told_which_decision_it_is_about(tmp_path):
    routed = marker_routing.route_project(
        _project(tmp_path, [_wordless(CLIP_NOTE)]))
    block = marker_routing.prompt_block(routed)
    assert block["notes"][0]["clip_decided_by"] == "select_broll"
    assert block["notes"][0]["clip_decision_id"] == (
        "select_broll/interjection_7")
    # The legend has to say what the new columns are: the handoffs are
    # frozen, so the data is the only place it can be said.
    assert "clip_decided_by" in marker_routing.PROMPT_LEGEND
    assert "clip_decision_written_in" in marker_routing.PROMPT_LEGEND


def test_the_reason_the_stamp_ranks_below_the_words_is_written_down():
    assert "plan_vfx" in td.STAMP_RANKS_BELOW_THE_WORDS
    assert "speech_sequence" in td.STAMP_RANKS_BELOW_THE_WORDS
