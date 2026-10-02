"""Routing the captain's typed timeline notes to the steps that own them.

History: docs/evidence/resolve_test_history.md#test_marker_routing.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import marker_routing  # noqa: E402
from library.tools.marker_feedback import PULL_FILE_SUFFIX  # noqa: E402
from library.tools.marker_routing import (  # noqa: E402
    DELIVERY_REPORT,
    OUTCOME_AMBIGUOUS,
    OUTCOME_ROUTED,
    OUTCOME_UNKNOWN_STEP,
    OUTCOME_UNROUTED,
    STEP_INPUT_NAME,
    TARGET_CLIP,
    TARGET_MOMENT,
    UndeliverableNote,
)
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402


# ── The captain's three real notes, off 001's timeline ──────────────

MOMENT_NOTE = {
    "source": "timeline_marker",
    "name": "Marker 1",
    "note": "why are the subtitles so big?",
    "text": "Marker 1\n\nwhy are the subtitles so big?",
    "frame": 26,
    "timecode": "00:00:00:26",
    "frame_in_timeline_space": 26,
    "unplaced_reason": "",
    "color": "Blue",
    "duration_frames": 1,
    "custom_data": {},
    "custom_data_raw": "",
    "clips": [
        {"name": "IMG_1816.MOV", "track_type": "video", "track_index": 1,
         "timeline_start": 0, "timeline_end": 72,
         "source_start": 25, "source_end": 97,
         "source_file": "/p/001/raw/IMG_1816.MOV", "clip_color": "",
         "flags": []},
        {"name": "sub_block_hook.mov", "track_type": "video",
         "track_index": 3, "timeline_start": 0, "timeline_end": 72,
         "source_start": 0, "source_end": 72,
         "source_file": "/p/001/pipeline_output/steps/"
                        "4_05_render_subtitles/sub_block_hook.mov",
         "clip_color": "", "flags": []},
        {"name": "IMG_1816.MOV", "track_type": "audio", "track_index": 1,
         "timeline_start": 0, "timeline_end": 72,
         "source_start": 25, "source_end": 97,
         "source_file": "/p/001/raw/IMG_1816.MOV", "clip_color": "",
         "flags": []},
        {"name": "_background music_ rise - uplifting piano _inspiring "
                 "_ beautiful_ _ _ motivation.wav", "track_type": "audio",
         "track_index": 2, "timeline_start": 0, "timeline_end": 1782,
         "source_start": 0, "source_end": 1782,
         "source_file": "/p/001/music/_background music_ rise - uplifting "
                        "piano _inspiring _ beautiful_ _ _ motivation.wav",
         "clip_color": "", "flags": []},
    ],
    "read_at": "2026-08-28T22:12:38.729517+00:00",
}

CLIP_NOTE = {
    "source": "clip_marker",
    "name": "Marker 1",
    "note": "this clip is honestly like broll of nothing, im confused why "
            "it was chosen and added here",
    "text": "Marker 1\n\nthis clip is honestly like broll of nothing, im "
            "confused why it was chosen and added here",
    "frame": 744,
    "timecode": "00:00:24:24",
    "frame_in_timeline_space": 89,
    "unplaced_reason": "",
    "color": "Blue",
    "duration_frames": 1,
    "custom_data": {},
    "custom_data_raw": "",
    "clips": [
        {"name": "IMG_1816.MOV", "track_type": "video", "track_index": 1,
         "timeline_start": 559, "timeline_end": 1038,
         "source_start": 3016, "source_end": 3495,
         "source_file": "/p/001/raw/IMG_1816.MOV", "clip_color": "",
         "flags": []},
        {"name": "IMG_1811.MOV", "track_type": "video", "track_index": 2,
         "timeline_start": 705, "timeline_end": 780,
         "source_start": 50, "source_end": 125,
         "source_file": "/p/001/raw/IMG_1811.MOV", "clip_color": "",
         "flags": []},
        {"name": "sub_block_7.mov", "track_type": "video", "track_index": 3,
         "timeline_start": 559, "timeline_end": 1039,
         "source_start": 15, "source_end": 495,
         "source_file": "/p/001/pipeline_output/steps/"
                        "4_05_render_subtitles/sub_block_7.mov",
         "clip_color": "", "flags": []},
        {"name": "IMG_1816.MOV", "track_type": "audio", "track_index": 1,
         "timeline_start": 559, "timeline_end": 1038,
         "source_start": 3016, "source_end": 3495,
         "source_file": "/p/001/raw/IMG_1816.MOV", "clip_color": "",
         "flags": []},
        {"name": "_background music_ rise - uplifting piano _inspiring "
                 "_ beautiful_ _ _ motivation.wav", "track_type": "audio",
         "track_index": 2, "timeline_start": 0, "timeline_end": 1782,
         "source_start": 0, "source_end": 1782,
         "source_file": "/p/001/music/_background music_ rise - uplifting "
                        "piano _inspiring _ beautiful_ _ _ motivation.wav",
         "clip_color": "", "flags": []},
    ],
    "read_at": "2026-08-28T22:12:38.729517+00:00",
}

AMBIGUOUS_NOTE = {
    "source": "clip_marker",
    "name": "Marker 1",
    "note": "why is this fully blurry, is it the zoom blur applied wrong? "
            "i think we already fixed this right?",
    "text": "Marker 1\n\nwhy is this fully blurry, is it the zoom blur "
            "applied wrong? i think we already fixed this right?",
    "frame": 1196,
    "timecode": "00:00:39:26",
    "frame_in_timeline_space": 686,
    "unplaced_reason": "",
    "color": "Blue",
    "duration_frames": 1,
    "custom_data": {},
    "custom_data_raw": "",
    "clips": [
        {"name": "IMG_1817.MOV", "track_type": "video", "track_index": 1,
         "timeline_start": 1164, "timeline_end": 1235,
         "source_start": 654, "source_end": 725,
         "source_file": "/p/001/raw/IMG_1817.MOV", "clip_color": "",
         "flags": []},
        {"name": "sub_block_9.mov", "track_type": "video", "track_index": 3,
         "timeline_start": 1164, "timeline_end": 1235,
         "source_start": 15, "source_end": 86,
         "source_file": "/p/001/pipeline_output/steps/"
                        "4_05_render_subtitles/sub_block_9.mov",
         "clip_color": "", "flags": []},
        # Resolve's linked audio item of the SAME placement: same file,
        # same timeline span, same source span. One clip, seen twice.
        {"name": "IMG_1817.MOV", "track_type": "audio", "track_index": 1,
         "timeline_start": 1164, "timeline_end": 1235,
         "source_start": 654, "source_end": 725,
         "source_file": "/p/001/raw/IMG_1817.MOV", "clip_color": "",
         "flags": []},
        {"name": "_background music_ rise - uplifting piano _inspiring "
                 "_ beautiful_ _ _ motivation.wav", "track_type": "audio",
         "track_index": 2, "timeline_start": 0, "timeline_end": 1782,
         "source_start": 0, "source_end": 1782,
         "source_file": "/p/001/music/_background music_ rise - uplifting "
                        "piano _inspiring _ beautiful_ _ _ motivation.wav",
         "clip_color": "", "flags": []},
    ],
    "read_at": "2026-08-28T22:12:38.729517+00:00",
}

REAL_NOTES = [MOMENT_NOTE, CLIP_NOTE, AMBIGUOUS_NOTE]


def _project(tmp_path, notes=REAL_NOTES, timeline="Pipeline_Edit"):
    layout = ProjectLayout(tmp_path)
    path = layout.write_path(
        Area.MARKER_FEEDBACK, f"{timeline}.20260828T221238Z{PULL_FILE_SUFFIX}")
    path.write_text(json.dumps({
        "format": "marker_feedback/1",
        "timeline": timeline,
        "note_count": len(notes),
        "notes": notes,
    }), encoding="utf-8")
    return str(tmp_path)


def _by_first_words(routed, fragment):
    hits = [n for n in routed if fragment in n.text]
    assert len(hits) == 1, f"{fragment!r} matched {len(hits)} notes"
    return hits[0]


# ── A clip note and a moment note are different things ──────────────

def test_a_moment_note_and_a_clip_note_are_never_the_same_field():
    """Four clips play at frame 26 and the timeline marker is attached
    to none of them: reading `clips` as the attachment would hand a note
    about the moment to whatever happened to be on V1. Four clips play
    at frame 744 and only IMG_1811.MOV on V2 both plays source frame 89
    and lands on 744 from it - the cutaway the captain was looking at.
    The distinction survives into the record and the prompt."""
    target = marker_routing.resolve_target(MOMENT_NOTE)
    assert target.kind == TARGET_MOMENT
    assert target.clip is None
    assert [c["name"] for c in target.clips_under][:3] == [
        "IMG_1816.MOV", "sub_block_hook.mov", "IMG_1816.MOV"]
    assert len(target.clips_under) == 4
    assert "context" in target.reason

    target = marker_routing.resolve_target(CLIP_NOTE)
    assert target.kind == TARGET_CLIP
    assert target.clip["name"] == "IMG_1811.MOV"
    assert target.tracks == ["video2"]
    assert target.basis == "rederived"

    moment = marker_routing.route_note(MOMENT_NOTE)
    clip = marker_routing.route_note(CLIP_NOTE)
    assert moment.target["kind"] == TARGET_MOMENT
    assert moment.target["clip"] is None and moment.target["clips_under"]
    assert clip.target["kind"] == TARGET_CLIP
    assert clip.target["clip"] and not clip.target["clips_under"]

    block = marker_routing.prompt_block([moment, clip])
    kinds = {n["typed"][:20]: n["attached_to"] for n in block["notes"]}
    assert set(kinds.values()) == {"moment", "clip"}
    assert "clips_under" in block["notes"][0]
    assert "clip" in block["notes"][1] and "clips_under" not in block["notes"][1]


# ── The three real notes, routed ────────────────────────────────────

def test_the_three_real_notes_route_by_their_words():
    """b-roll words reach the step that chose the b-roll; the subtitle
    question reaches the step that grouped the cards. "the zoom blur"
    names a transition in `plan_transitions` and an effect in
    `plan_vfx`: both readings are real, so the honest answer is both,
    and nothing breaks the tie. A note naming nothing is reported."""
    routed = marker_routing.route_note(CLIP_NOTE)
    assert routed.outcome == OUTCOME_ROUTED
    assert routed.steps == ["select_broll"]
    assert routed.basis == "vocabulary"
    assert "broll" in routed.evidence["select_broll"]

    routed = marker_routing.route_note(MOMENT_NOTE)
    assert routed.outcome == OUTCOME_ROUTED
    assert routed.steps == ["plan_subtitles"]

    routed = marker_routing.route_note(AMBIGUOUS_NOTE)
    assert routed.outcome == OUTCOME_AMBIGUOUS
    assert routed.steps == ["plan_transitions", "plan_vfx"]
    assert "zoom" in routed.evidence["plan_transitions"]
    assert "blur" in routed.evidence["plan_vfx"]
    assert "step: <name>" in routed.reason

    routed = marker_routing.route_note(dict(
        MOMENT_NOTE, name="Marker 1", note="hm", text="Marker 1\n\nhm"))
    assert routed.outcome == OUTCOME_UNROUTED
    assert routed.steps == []
    assert "step: <name>" in routed.reason


# ── A declaration outranks the words, and is refused by name ────────

def _declaring(note, declaration):
    return dict(note, note=note["note"] + "\n" + declaration,
                text=note["text"] + "\n" + declaration)


def test_a_declaration_outranks_the_words_and_is_refused_by_name():
    """The note is full of b-roll vocabulary and declares plan_vfx: the
    declaration is what the captain said, so it is what happens. Two
    names are ambiguous, not the first one. A name the pipeline does not
    have is refused by name - not routed to the nearest thing, and not
    silently dropped back into vocabulary routing. A Resolve panel's
    custom-data route record (where the marker UI cannot show a `step:`
    line) is read the same way."""
    routed = marker_routing.route_note(_declaring(CLIP_NOTE, "step: plan_vfx"))
    assert routed.outcome == OUTCOME_ROUTED
    assert routed.basis == "declared"
    assert routed.steps == ["plan_vfx"]

    routed = marker_routing.route_note(
        _declaring(CLIP_NOTE, "step: plan_vfx, plan_sfx"))
    assert routed.outcome == OUTCOME_AMBIGUOUS
    assert routed.steps == ["plan_vfx", "plan_sfx"]

    routed = marker_routing.route_note(
        _declaring(CLIP_NOTE, "step: plan_bloopers"))
    assert routed.outcome == OUTCOME_UNKNOWN_STEP
    assert routed.unknown_names == ["plan_bloopers"]
    assert routed.steps == []
    assert "plan_bloopers" in routed.reason

    routed = marker_routing.route_note(dict(MOMENT_NOTE, custom_data={
        "schema": "vep.marker/1", "id": "mk_1", "records": [
            {"kind": "route", "writer": "panel", "writer_version": "1",
             "id": "r1", "at": "2026-08-28T00:00:00+00:00",
             "step": "plan_vfx"},
        ]}))
    assert routed.basis == "declared"
    assert routed.steps == ["plan_vfx"]


def test_resolve_step_name_is_exact():
    assert marker_routing.resolve_step_name("select_broll") is not None
    assert marker_routing.resolve_step_name("select_brol") is None
    assert marker_routing.resolve_step_name("broll") is None
    assert marker_routing.resolve_step_name("") is None


# The captain's own phrasings, verbatim in shape, each naming exactly
# the steps listed. 2026-09-12: "a little label graphic" (the speaker
# lower third). 2026-09-19: 37 markers across 26 reels, of which 15
# named no term while plainly about a known step, and a second pass of
# ten UNROUTED. A term added to one step must steal no other step's
# notes, and the two leftovers stay unrouted for a reason - one points
# at another note (coreference), one is a bare quoted word-correction.
VOCABULARY_ROWS = (
    ("the lower third is too big", {"render_motion_graphics"}),
    ("that label graphic should hold longer", {"render_motion_graphics"}),
    ("the name tag under him is wrong", {"render_motion_graphics"}),
    ("the grade is too warm", {"color_grade"}),
    ("that passage is repetitive", {"speech_sequence"}),
    ("the captions drift", {"plan_subtitles"}),
    ("CEO and CMO should be capitalized", {"plan_subtitles"}),
    ("LA Fitness is a proper noun, please fix this", {"plan_subtitles"}),
    ("the qu is pronounced in the audio but for the transcript it "
     "should be filtered out", {"plan_subtitles"}),
    ("two takes occur here saying the same thing", {"speech_sequence"}),
    ("this area is a bad take, she says it more concisely in the "
     "next clip", {"speech_sequence"}),
    ("can we use this animation in all of the CTAs",
     {"render_motion_graphics"}),
    ("the bullets are mis-sized, please fix", {"render_motion_graphics"}),
    ("only use the main audio channel, the other 3 audio channels "
     "are bleeding", {"audio_mix"}),
    # Two steps' decisions: ambiguous is correct here, not a tie-break.
    ("take the music down a touch", {"speech_sequence", "music_selection"}),
    ("there is some fluf to remove from here where akshita messes "
     "up and recovers", {"speech_sequence"}),
    ("the end of akshita's audio is cut off here, can you fix this",
     {"speech_sequence"}),
    ("there was an answer from akshita here that was cut out and it "
     "makes the jump to craig feel wrong", {"speech_sequence"}),
    ("akshita seems to kinda say the same things over in this span, "
     "so it needs to be consolidated down properly", {"speech_sequence"}),
    ("there is not value add in the reel, this needs to be fixed",
     {"speech_sequence"}),
    ("there is like no value prop given in this reel", {"speech_sequence"}),
    ("this whole segment seems to be mistakenly placed here, please "
     "investigate and fix", {"mesh_spine"}),
    ("i feel like this CTA technically doesn't make sense here",
     {"render_motion_graphics"}),
    ('same "aics" problem here', set()),
    ('- it\'s "Gemini" not "jim and i"', set()),
)


def test_the_captains_words_route_to_exactly_their_steps():
    wrong = {note: sorted(marker_routing.matched_terms(note))
             for note, expected in VOCABULARY_ROWS
             if set(marker_routing.matched_terms(note)) != expected}
    assert not wrong, wrong
    # A term matches on a word boundary: `zoom` must not fire on
    # `zoomorphic` nor `pan` on `panic` - a substring router routes
    # half the notes it sees.
    assert "plan_transitions" not in marker_routing.matched_terms("zoomorphic")
    assert "compile_manifest" not in marker_routing.matched_terms("panicking")
    assert "plan_vfx" in marker_routing.matched_terms("the zoom is wrong")


# ── Nothing may silently drop a routed note ─────────────────────────

def _manifest(*names):
    return {"interface": {"inputs": [{"name": n} for n in names]}}


# ── The whole project, and what the captain reads ───────────────────

def test_routing_a_project_reads_every_pull_file(tmp_path):
    project = _project(tmp_path)
    routed = marker_routing.route_project(project)
    assert len(routed) == 3
    assert [n.outcome for n in routed] == [
        OUTCOME_ROUTED, OUTCOME_ROUTED, OUTCOME_AMBIGUOUS]
    assert all(n.timeline == "Pipeline_Edit" for n in routed)
    # A project with no pull files routes nothing.
    assert marker_routing.route_project(str(tmp_path / "empty")) == []


def test_one_note_collected_twice_is_one_routed_note(tmp_path):
    layout = ProjectLayout(tmp_path)
    for stamp in ("20260828T090000Z", "20260828T100000Z"):
        path = layout.write_path(
            Area.MARKER_FEEDBACK, f"Edit.{stamp}{PULL_FILE_SUFFIX}")
        path.write_text(json.dumps({
            "format": "marker_feedback/1", "timeline": "Edit",
            "notes": [CLIP_NOTE]}), encoding="utf-8")
    assert len(marker_routing.route_project(str(tmp_path))) == 1


def test_unplaced_notes_on_one_timeline_get_stable_distinct_ids():
    """Two editor notes without frames must not share one ledger link.

    Catches: same-timeline unplaced requests overwriting one another's
    edit specs because both used the old `timeline:source:unplaced` id.
    """
    first = {"source": "timeline_marker", "name": "question A",
             "note": "use the logo"}
    second = {"source": "timeline_marker", "name": "question B",
              "note": "remove the pause"}

    first_id = marker_routing._note_id(first, "Edit", "pull-a.json")
    assert first_id == marker_routing._note_id(
        first, "Edit", "pull-from-later.json")
    assert first_id != marker_routing._note_id(
        second, "Edit", "pull-a.json")


def test_the_report_names_every_note_and_where_it_went(tmp_path):
    project = _project(tmp_path)
    result = marker_routing.write_record(project)
    report = open(result["report"], encoding="utf-8").read()

    assert "why are the subtitles so big?" in report
    assert "broll of nothing" in report
    assert "3.02 select_broll" in report
    assert "4.01 plan_subtitles" in report
    assert "AMBIGUOUS" in report
    assert "4.02 plan_transitions, 4.03 plan_vfx" in report
    # The distinction the captain has to be able to see.
    assert "on clip **IMG_1811.MOV** (video2)" in report
    assert "at a moment on the timeline" in report

    record = json.loads(open(result["record"], encoding="utf-8").read())
    assert record["counts"] == {"routed": 2, "ambiguous": 1,
                                "unrouted": 0, "unknown_step": 0}
    assert record["by_step"] == {
        "plan_subtitles": ["Pipeline_Edit:timeline_marker:26"],
        "select_broll": ["Pipeline_Edit:clip_marker:744"]}


# ── The CLI ─────────────────────────────────────────────────────────


# ── The runner really carries it, and really refuses ────────────────
#
# An edgeless DAG: these are about the injection mechanism, not about
# `data_mapping` routing, and a real node would demand its whole upstream
# be present in state first.  Same shape as
# `tests/contracts/test_context_contracts.py`.

EDGELESS_DAG = {"nodes": [], "edges": []}


def _gather(node_id, project, manifest):
    from library.processes.edit_video.run_pipeline import gather_step_inputs
    return gather_step_inputs(
        node_id, EDGELESS_DAG, {"project_folder": project}, manifest=manifest)


def test_a_routed_note_reaches_the_step_the_runner_assembles(tmp_path):
    """The whole point, end to end: the captain's words come out of the
    context the runner built for the step that chose the b-roll."""
    project = _project(tmp_path)
    inputs = _gather("select_broll", project,
                     _manifest("clip_catalog", STEP_INPUT_NAME))
    block = inputs[STEP_INPUT_NAME]
    assert block["note_count"] == 1
    assert "broll of nothing" in block["notes"][0]["typed"]
    assert block["notes"][0]["clip"] == "IMG_1811.MOV"
    assert block["notes"][0]["attached_to"] == "clip"
    assert "verbatim" in block["legend"]


def test_a_step_with_no_note_for_it_gets_no_key_at_all(tmp_path):
    project = _project(tmp_path)
    inputs = _gather("mesh_spine", project,
                     _manifest("timed_spine", STEP_INPUT_NAME))
    assert STEP_INPUT_NAME not in inputs


def test_neither_candidate_of_an_ambiguous_note_receives_it(tmp_path):
    project = _project(tmp_path)
    for node_id in ("plan_vfx", "plan_transitions"):
        inputs = _gather(node_id, project, _manifest(STEP_INPUT_NAME))
        assert STEP_INPUT_NAME not in inputs, (
            f"{node_id} received a note nothing decided was its")


def test_the_runner_refuses_rather_than_assembling_a_context_without_it(
        tmp_path):
    """The silent-drop guard, on the real assembly path. A run that
    dropped the note here would look exactly like a run with no notes."""
    project = _project(tmp_path)
    with pytest.raises(UndeliverableNote) as exc:
        _gather("select_broll", project, _manifest("clip_catalog"))
    assert STEP_INPUT_NAME in str(exc.value)
    assert "broll of nothing" in str(exc.value)


def test_the_projection_cannot_drop_it(tmp_path):
    """An allow-list that does not name the notes still keeps them, the
    same way it keeps the creative brief - restored BY NAME, so a step's
    `context_fields` neither has to list them nor can drop them."""
    from library.processes.edit_video.run_pipeline import project_step_context
    project = _project(tmp_path)
    inputs = _gather("select_broll", project,
                     _manifest("clip_catalog", STEP_INPUT_NAME))
    inputs["clip_catalog"] = [{"clip_id": "clip_001"}]

    narrowed = project_step_context(
        dict(inputs), {"context_fields": ["clip_catalog"]})

    assert STEP_INPUT_NAME in narrowed
    assert narrowed[STEP_INPUT_NAME] == inputs[STEP_INPUT_NAME]


# ── A note that reaches nobody is SAID, not silently dropped ──────────

def test_the_run_summary_names_every_note_that_reached_nobody(tmp_path):
    """Two of 001's three notes reached no model and nothing said so.

    Of the three real notes, one prompt delivery; the other two are
    accounted for by name, not absent from the accounting.

    *"why are the subtitles so big?"* routes to 4.01 `plan_subtitles`,
    which is deterministic with no `handoff.md`, so it is delivered as a
    `report` and reaches no model at all. *"why is this fully blurry, is
    it the zoom blur applied wrong?"* is AMBIGUOUS between
    `plan_transitions` and `plan_vfx` and therefore reaches neither.
    Both were in `ROUTED-NOTES.md`; neither was in a run.
    """
    n1 = marker_routing.route_note(dict(
        MOMENT_NOTE, note="why are the subtitles so big?",
        text="why are the subtitles so big?"))
    n2 = marker_routing.route_note(dict(
        MOMENT_NOTE, frame=400, timecode="00:00:13:10",
        note="why is this fully blurry, is it the zoom blur applied wrong?",
        text="why is this fully blurry, is it the zoom blur applied wrong?"))
    real = [marker_routing.route_note(n) for n in REAL_NOTES]
    assert {(n.note_id, why) for n, why, _ in
            marker_routing.undelivered(real)} == {
        ("timeline:timeline_marker:26", DELIVERY_REPORT),
        ("timeline:clip_marker:1196", OUTCOME_AMBIGUOUS),
    }

    notes = [n1, n2]
    left = marker_routing.undelivered(notes)
    assert len(left) == 2
    outcomes = {why for _n, why, _d in left}
    assert marker_routing.OUTCOME_AMBIGUOUS in outcomes

    lines = "\n".join(marker_routing.undelivered_summary_lines(notes))
    assert "reached NOBODY" in lines
    assert n1.note_id in lines and n2.note_id in lines
    # The reason travels with the note, not just the count.
    for _note, _why, detail in left:
        assert detail and detail in lines


def test_a_run_that_delivered_nothing_records_that_on_the_note(tmp_path):
    """The note's OWN record, in the log the deliveries go to."""
    project = tmp_path / "proj"
    ProjectLayout(str(project)).ensure()
    note = marker_routing.route_note(dict(
        MOMENT_NOTE,
        note="why is this fully blurry, is it the zoom blur applied wrong?",
        text="why is this fully blurry, is it the zoom blur applied wrong?"))
    notes = [note]
    left = marker_routing.undelivered(notes)
    marker_routing.record_non_delivery(str(project), left)

    entries = marker_routing.deliveries(str(project))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["step"] == marker_routing.NON_DELIVERY_STEP
    assert entry["reached_nobody"][0]["note_id"] == note.note_id
    assert entry["reached_nobody"][0]["outcome"] == \
        marker_routing.OUTCOME_AMBIGUOUS

    # And it must not read as a delivery to a step called "(nobody)".
    report = marker_routing.render_report(str(project), notes)
    assert "Reached nobody" in report
    assert "**Delivered**" not in report


# ── A note is an INTERVAL on a NAMED TIMELINE ───────────────────────
#
# `data/decisions/region-timeline-identity.md`: a note is on
# `(timeline, span)`. The timeline half has always been on RoutedNote;
# the span half was measured at collection and thrown away here.

def test_the_span_subtracts_the_timelines_own_start_frame():
    """THE conversion, and the one that is easy to get wrong.

    Resolve starts a timeline at 01:00:00:00 - frame 108000 at 30fps -
    so `frame / fps` is an HOUR out and looks entirely plausible. This is
    the same domain collision `region.py` exists to prevent, one level up.
    """
    raw = dict(CLIP_NOTE, frame=108000 + 26, duration_frames=30)
    routed = marker_routing.route_note(
        raw, "Pipeline_Edit_01", "p.markers.json",
        timeline_fps=30.0, timeline_start_frame=108000)

    span = marker_routing.note_span_seconds(routed)
    assert span == (pytest.approx(0.867, abs=0.002),
                    pytest.approx(1.867, abs=0.002)), (
        f"got {span}; frame/fps without the start-frame offset would be "
        f"{(108000 + 26) / 30:.1f}s - an hour into a 56-second cut"
    )
    block = marker_routing.prompt_block([routed])["notes"][0]
    assert block["at_region"] == "0.867..1.867"
    assert block["on_timeline"] == "Pipeline_Edit_01"


def test_route_project_hands_the_timelines_measurements_through(tmp_path):
    """The pull file records `timeline_fps` and `timeline_start_frame`
    and nothing read either until now."""
    project = tmp_path / "proj"
    directory = ProjectLayout(project).write_dir(Area.MARKER_FEEDBACK)
    (directory / f"tl.20260905T000000Z{PULL_FILE_SUFFIX}").write_text(
        json.dumps({
            "format": "marker_feedback/1", "timeline": "Pipeline_Edit_01",
            "timeline_fps": 30.0, "timeline_start_frame": 108000,
            "notes": [dict(CLIP_NOTE, frame=108000 + 26, duration_frames=30)],
        }), encoding="utf-8")

    routed = marker_routing.route_project(str(project))
    assert len(routed) == 1
    assert routed[0].timeline_fps == 30.0
    assert routed[0].timeline_start_frame == 108000
    assert marker_routing.note_span_seconds(routed[0]) == (
        pytest.approx(0.867, abs=0.002), pytest.approx(1.867, abs=0.002))


# ── Scoping a routing to the timelines that still exist ──────────────

def _timeline_project(tmp_path, timelines):
    layout = ProjectLayout(tmp_path)
    for name, text in timelines:
        path = layout.write_path(
            Area.MARKER_FEEDBACK,
            f"{name}.20260919T000000Z{PULL_FILE_SUFFIX}")
        path.write_text(json.dumps({
            "format": "marker_feedback/1", "timeline": name,
            "notes": [dict(MOMENT_NOTE, name="feedback", note=text,
                           text=text)],
        }), encoding="utf-8")
    return str(tmp_path)


def test_routing_scopes_to_named_timelines(tmp_path):
    """94 pull files routed to 63 notes on 2026-09-19, of which 26 sat
    on staged, backed-up and archived containers no timeline carries
    any more. The unscoped record works ghosts alongside the captain's
    current words; the scope keeps history on disk while routing the
    live set."""
    project = _timeline_project(tmp_path, [
        ("Reel 01", "the live note"),
        ("Reel 01 (batch-1050)", "the ghost note"),
    ])
    assert len(marker_routing.route_project(project)) == 2
    scoped = marker_routing.route_project(project, timelines=["Reel 01"])
    assert len(scoped) == 1
    assert scoped[0].timeline == "Reel 01"
    assert "the live note" in scoped[0].text


def test_the_write_command_scopes_its_record_and_report(tmp_path):
    project = _timeline_project(tmp_path, [
        ("Reel 01", "the live note"),
        ("Reel 01 (batch-1050)", "the ghost note"),
    ])
    assert marker_routing.main(
        ["write", "--project", project, "--timeline", "Reel 01"]) == 0
    directory = ProjectLayout(project).read_dir(Area.MARKER_FEEDBACK)
    records = [p for p in directory.iterdir()
               if p.name.endswith(marker_routing.ROUTING_FILE_SUFFIX)]
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["timelines"] == ["Reel 01"]
    assert record["note_count"] == 1
    report = (directory / marker_routing.REPORT_FILENAME).read_text(
        encoding="utf-8")
    assert "Scoped to 1 timeline(s)" in report
    assert "the live note" in report
    assert "the ghost note" not in report
