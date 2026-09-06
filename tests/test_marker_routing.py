"""Routing the captain's typed timeline notes to the steps that own them.

The shapes here are REAL.  `CLIP_NOTE`, `MOMENT_NOTE` and `AMBIGUOUS_NOTE`
are the three notes the captain typed onto 001's built timeline, copied
verbatim out of the pull file `marker_feedback` wrote on 2026-08-28 -
same fields, same frames, same clip lists, same words.  A routing test
written against invented notes proves the router agrees with whoever
wrote the fixture.

The clip lists were re-checked against that pull file on 2026-08-29, when
`timeline_decisions` began matching them back to the placements of 001's
own manifest.  Three transcription errors came out: the moment note's V1
clip read source 2457..2529 where the pull file says 25..97, its caption
read 15..87 where the file says 0..72, and the music bed - a fourth clip
under all three notes - had been dropped, which is why the first test
below said "four clips" over a list of three.

No Resolve, and no fake of one: everything here is the disk half, the
same line `tests/test_marker_feedback_records.py` draws.  Nothing in this
file reaches a real project - the project is built under `tmp_path`.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import marker_routing, run_scope  # noqa: E402
from library.tools.marker_feedback import PULL_FILE_SUFFIX  # noqa: E402
from library.tools.marker_routing import (  # noqa: E402
    BY_NODE_ID,
    DELIVERY_PROMPT,
    DELIVERY_REPORT,
    OUTCOME_AMBIGUOUS,
    OUTCOME_ROUTED,
    OUTCOME_UNKNOWN_STEP,
    OUTCOME_UNROUTED,
    STEP_DECISIONS,
    STEP_INPUT_NAME,
    TARGET_CLIP,
    TARGET_MOMENT,
    UndeliverableNote,
    WITHDRAWN_ROUTERS,
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

def test_a_timeline_marker_is_attached_to_a_moment_not_to_a_clip():
    """Four clips play at frame 26 and the note is attached to none of
    them. Reading `clips` as the attachment would hand a note about the
    moment to whatever happened to be on V1."""
    target = marker_routing.resolve_target(MOMENT_NOTE)
    assert target.kind == TARGET_MOMENT
    assert target.clip is None
    assert [c["name"] for c in target.clips_under][:3] == [
        "IMG_1816.MOV", "sub_block_hook.mov", "IMG_1816.MOV"]
    assert len(target.clips_under) == 4
    assert "context" in target.reason


def test_a_clip_marker_is_attached_to_the_one_clip_it_was_typed_on():
    """Four clips play at frame 744 and only IMG_1811.MOV on V2 both
    plays source frame 89 and lands on 744 from it. That is the cutaway
    the captain was actually looking at."""
    target = marker_routing.resolve_target(CLIP_NOTE)
    assert target.kind == TARGET_CLIP
    assert target.clip["name"] == "IMG_1811.MOV"
    assert target.tracks == ["video2"]
    assert target.basis == "rederived"


def test_the_two_are_never_the_same_field():
    """The distinction has to survive into the record and the prompt,
    not just into the resolver."""
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


def test_a_recorded_placement_is_used_and_agrees_with_the_recovered_one():
    """`marker_feedback` now records the placement a clip note was typed
    on. A pull file written before that has it RECOVERED, and the two
    must say the same thing or one of them is wrong."""
    recovered = marker_routing.resolve_target(CLIP_NOTE)
    with_record = dict(CLIP_NOTE, attached_clip=recovered.clip)
    recorded = marker_routing.resolve_target(with_record)
    assert recorded.basis == "recorded"
    assert recorded.clip == recovered.clip


def test_linked_audio_and_video_of_one_placement_is_one_clip():
    """IMG_1817.MOV appears on V1 and A1 with the same file, timeline
    span and source span. That is Resolve's linked pair of ONE placement,
    not two candidates, so recovering it is not a guess."""
    target = marker_routing.resolve_target(AMBIGUOUS_NOTE)
    assert target.kind == TARGET_CLIP
    assert target.clip["name"] == "IMG_1817.MOV"
    assert target.tracks == ["video1", "audio1"]


def test_two_genuinely_different_clips_are_reported_unresolved():
    """A second, different clip whose arithmetic also lands on the frame
    means the placement is not knowable from a pull file. It says so."""
    decoy = dict(CLIP_NOTE["clips"][1], name="OTHER.MOV",
                 source_file="/p/001/raw/OTHER.MOV", track_index=4)
    note = dict(CLIP_NOTE, clips=CLIP_NOTE["clips"] + [decoy])
    target = marker_routing.resolve_target(note)
    assert target.basis == "unresolved"
    assert target.clip is None
    assert "OTHER.MOV" in target.reason and "IMG_1811.MOV" in target.reason


def test_a_clip_marker_outside_the_played_range_keeps_no_clip():
    note = dict(CLIP_NOTE, frame=None, timecode=None,
                unplaced_reason="outside the range this clip plays")
    target = marker_routing.resolve_target(note)
    assert target.basis == "unresolved"
    assert target.clip is None


# ── The three real notes, routed ────────────────────────────────────

def test_the_broll_note_routes_to_the_step_that_chose_the_broll():
    routed = marker_routing.route_note(CLIP_NOTE)
    assert routed.outcome == OUTCOME_ROUTED
    assert routed.steps == ["select_broll"]
    assert routed.basis == "vocabulary"
    assert "broll" in routed.evidence["select_broll"]


def test_the_subtitle_note_routes_to_the_step_that_grouped_the_cards():
    routed = marker_routing.route_note(MOMENT_NOTE)
    assert routed.outcome == OUTCOME_ROUTED
    assert routed.steps == ["plan_subtitles"]


def test_the_blur_note_is_ambiguous_and_says_so():
    """"the zoom blur" names a transition in `plan_transitions` and an
    effect in `plan_vfx`. Both readings are real, so the honest answer is
    both, and nothing here breaks the tie."""
    routed = marker_routing.route_note(AMBIGUOUS_NOTE)
    assert routed.outcome == OUTCOME_AMBIGUOUS
    assert routed.steps == ["plan_transitions", "plan_vfx"]
    assert "zoom" in routed.evidence["plan_transitions"]
    assert "blur" in routed.evidence["plan_vfx"]
    assert "step: <name>" in routed.reason


def test_an_ambiguous_note_reaches_no_step():
    routed = [marker_routing.route_note(n) for n in REAL_NOTES]
    assert marker_routing.notes_for_step(routed, "plan_vfx") == []
    assert marker_routing.notes_for_step(routed, "plan_transitions") == []
    assert len(marker_routing.notes_for_step(routed, "select_broll")) == 1


def test_a_note_naming_nothing_in_the_table_is_reported_not_routed():
    note = dict(MOMENT_NOTE, name="Marker 1",
                note="hm", text="Marker 1\n\nhm")
    routed = marker_routing.route_note(note)
    assert routed.outcome == OUTCOME_UNROUTED
    assert routed.steps == []
    assert "step: <name>" in routed.reason


# ── A declaration outranks the words, and is refused by name ────────

@pytest.mark.parametrize("declaration,expected", [
    ("step: plan_vfx", "plan_vfx"),
    ("step: 4.03", "plan_vfx"),
    ("STEP = plan_vfx", "plan_vfx"),
    ("step:  `plan_vfx` ", "plan_vfx"),
])
def test_a_declared_step_wins_over_the_words(declaration, expected):
    """The note is full of b-roll vocabulary and declares plan_vfx. The
    declaration is what the captain said, so it is what happens."""
    note = dict(CLIP_NOTE)
    note["note"] = CLIP_NOTE["note"] + "\n" + declaration
    note["text"] = CLIP_NOTE["text"] + "\n" + declaration
    routed = marker_routing.route_note(note)
    assert routed.outcome == OUTCOME_ROUTED
    assert routed.basis == "declared"
    assert routed.steps == [expected]


def test_a_declaration_naming_two_steps_is_ambiguous_not_the_first_one():
    note = dict(CLIP_NOTE)
    note["text"] = CLIP_NOTE["text"] + "\nstep: plan_vfx, plan_sfx"
    routed = marker_routing.route_note(note)
    assert routed.outcome == OUTCOME_AMBIGUOUS
    assert routed.steps == ["plan_vfx", "plan_sfx"]


def test_a_declared_name_the_pipeline_does_not_have_is_refused_by_name():
    """Not routed to the nearest thing, and not silently ignored back
    into vocabulary routing - the captain asked for something specific
    and the answer is that it does not exist."""
    note = dict(CLIP_NOTE)
    note["text"] = CLIP_NOTE["text"] + "\nstep: plan_bloopers"
    routed = marker_routing.route_note(note)
    assert routed.outcome == OUTCOME_UNKNOWN_STEP
    assert routed.unknown_names == ["plan_bloopers"]
    assert routed.steps == []
    assert "plan_bloopers" in routed.reason


def test_a_declaration_in_a_custom_data_route_record_is_read():
    """The channel a Resolve panel would write on, where the marker UI
    cannot show a `step:` line."""
    note = dict(MOMENT_NOTE, custom_data={
        "schema": "vep.marker/1", "id": "mk_1", "records": [
            {"kind": "route", "writer": "panel", "writer_version": "1",
             "id": "r1", "at": "2026-08-28T00:00:00+00:00",
             "step": "plan_vfx"},
        ]})
    routed = marker_routing.route_note(note)
    assert routed.basis == "declared"
    assert routed.steps == ["plan_vfx"]


def test_resolve_step_name_is_exact():
    assert marker_routing.resolve_step_name("select_broll") is not None
    assert marker_routing.resolve_step_name("select_brol") is None
    assert marker_routing.resolve_step_name("broll") is None
    assert marker_routing.resolve_step_name("") is None


def test_a_term_matches_on_a_word_boundary():
    """`zoom` must not fire on `zoomorphic`, and `pan` must not fire on
    `panic` - a substring router routes half the notes it sees."""
    assert "plan_transitions" not in marker_routing.matched_terms("zoomorphic")
    assert "compile_manifest" not in marker_routing.matched_terms("panicking")
    assert "plan_vfx" in marker_routing.matched_terms("the zoom is wrong")


# ── The table ───────────────────────────────────────────────────────

def test_every_routable_step_is_a_real_dag_node():
    nodes = {n["id"] for n in run_scope.load_dag()["nodes"]}
    missing = [d.node_id for d in STEP_DECISIONS if d.node_id not in nodes]
    assert not missing, f"routes to steps that are not in the DAG: {missing}"


def test_every_prompt_step_declares_the_input_it_would_receive():
    """The other end of `assert_deliverable`, read off the real
    manifests: a step the table says takes notes and whose manifest does
    not declare the input would fail every run that has a note for it."""
    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)
    for decision in STEP_DECISIONS:
        if decision.delivery != DELIVERY_PROMPT:
            continue
        declared = {inp.get("name") for inp in
                    manifests[decision.node_id]["interface"]["inputs"]}
        assert STEP_INPUT_NAME in declared, (
            f"{decision.node_id} takes notes and does not declare "
            f"{STEP_INPUT_NAME}")


def test_a_report_only_step_declares_nothing_and_says_why():
    for decision in STEP_DECISIONS:
        if decision.delivery == DELIVERY_REPORT:
            assert len(decision.delivery_note.split()) >= 8, (
                f"{decision.node_id} delivers no prompt and does not say "
                f"why: {decision.delivery_note!r}")


def test_every_step_states_what_it_owns():
    for decision in STEP_DECISIONS:
        assert len(decision.owns.split()) >= 6, decision
        assert decision.delivery in (DELIVERY_PROMPT, DELIVERY_REPORT)


def test_the_table_has_no_duplicate_node_or_number():
    assert len({d.node_id for d in STEP_DECISIONS}) == len(STEP_DECISIONS)
    assert len({d.number for d in STEP_DECISIONS}) == len(STEP_DECISIONS)


def test_every_withdrawn_router_carries_a_reason():
    assert WITHDRAWN_ROUTERS
    for name, reason in WITHDRAWN_ROUTERS.items():
        assert len(reason.split()) >= 15, f"{name}: {reason!r}"


# ── Nothing may silently drop a routed note ─────────────────────────

def _manifest(*names):
    return {"interface": {"inputs": [{"name": n} for n in names]}}


def test_a_step_that_cannot_receive_a_routed_note_fails_the_run():
    routed = [marker_routing.route_note(CLIP_NOTE)]
    with pytest.raises(UndeliverableNote) as exc:
        marker_routing.assert_deliverable(
            "select_broll", _manifest("creative_brief"), routed)
    assert STEP_INPUT_NAME in str(exc.value)
    assert "broll of nothing" in str(exc.value)


def test_a_step_that_declares_the_input_receives_its_notes():
    routed = [marker_routing.route_note(n) for n in REAL_NOTES]
    mine = marker_routing.assert_deliverable(
        "select_broll", _manifest("creative_brief", STEP_INPUT_NAME), routed)
    assert [n.steps for n in mine] == [["select_broll"]]


def test_a_step_with_no_notes_is_not_asked_to_declare_anything():
    routed = [marker_routing.route_note(n) for n in REAL_NOTES]
    assert marker_routing.assert_deliverable(
        "mesh_spine", _manifest(), routed) == []


def test_a_report_only_step_is_not_a_failure_and_is_not_a_delivery():
    routed = [marker_routing.route_note(MOMENT_NOTE)]
    assert marker_routing.assert_deliverable(
        "plan_subtitles", _manifest(), routed) == []
    left = dict((n.note_id, why)
                for n, why, _ in marker_routing.undelivered(routed))
    assert list(left.values()) == [DELIVERY_REPORT]


def test_every_note_that_reaches_no_prompt_is_reported():
    """Three notes, one prompt delivery. The other two are accounted
    for by name, not absent from the accounting."""
    routed = [marker_routing.route_note(n) for n in REAL_NOTES]
    left = marker_routing.undelivered(routed)
    assert len(left) == 2
    assert {(n.note_id, why) for n, why, _ in left} == {
        ("timeline:timeline_marker:26", DELIVERY_REPORT),
        ("timeline:clip_marker:1196", OUTCOME_AMBIGUOUS),
    }
    for _, _, detail in left:
        assert detail.strip()


# ── The whole project, and what the captain reads ───────────────────

def test_routing_a_project_reads_every_pull_file(tmp_path):
    project = _project(tmp_path)
    routed = marker_routing.route_project(project)
    assert len(routed) == 3
    assert [n.outcome for n in routed] == [
        OUTCOME_ROUTED, OUTCOME_ROUTED, OUTCOME_AMBIGUOUS]
    assert all(n.timeline == "Pipeline_Edit" for n in routed)


def test_one_note_collected_twice_is_one_routed_note(tmp_path):
    layout = ProjectLayout(tmp_path)
    for stamp in ("20260828T090000Z", "20260828T100000Z"):
        path = layout.write_path(
            Area.MARKER_FEEDBACK, f"Edit.{stamp}{PULL_FILE_SUFFIX}")
        path.write_text(json.dumps({
            "format": "marker_feedback/1", "timeline": "Edit",
            "notes": [CLIP_NOTE]}), encoding="utf-8")
    assert len(marker_routing.route_project(str(tmp_path))) == 1


def test_a_project_with_no_notes_routes_nothing(tmp_path):
    assert marker_routing.route_project(str(tmp_path)) == []


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


def test_a_routing_record_never_overwrites_an_earlier_one(tmp_path):
    project = _project(tmp_path)
    first = marker_routing.write_record(project)
    second = marker_routing.write_record(project)
    assert first["record"] != second["record"]
    assert os.path.exists(first["record"])


def test_a_delivery_is_appended_and_shows_up_in_the_report(tmp_path):
    project = _project(tmp_path)
    marker_routing.record_delivery(
        project, "select_broll", ["Pipeline_Edit:clip_marker:744"])
    marker_routing.record_delivery(
        project, "select_broll", ["Pipeline_Edit:clip_marker:744"])
    assert len(marker_routing.deliveries(project)) == 2
    report = marker_routing.render_report(project)
    assert report.count("**Delivered**: select_broll at") == 1
    assert "select_broll at" in report


def test_the_report_is_honest_about_an_empty_project(tmp_path):
    report = marker_routing.render_report(str(tmp_path))
    assert "No notes have been collected" in report


# ── The CLI ─────────────────────────────────────────────────────────

def test_the_steps_command_prints_the_whole_table(capsys):
    assert marker_routing.main(["steps"]) == 0
    out = capsys.readouterr().out
    for decision in STEP_DECISIONS:
        assert decision.node_id in out
    assert "step: <name>" in out


def test_the_report_command_writes_nothing(tmp_path):
    project = _project(tmp_path)
    before = sorted(p.name for p in
                    ProjectLayout(project).read_dir(Area.MARKER_FEEDBACK)
                    .iterdir())
    assert marker_routing.main(["report", "--project", project]) == 0
    after = sorted(p.name for p in
                   ProjectLayout(project).read_dir(Area.MARKER_FEEDBACK)
                   .iterdir())
    assert before == after


def test_the_write_command_leaves_both_files(tmp_path):
    project = _project(tmp_path)
    assert marker_routing.main(["write", "--project", project]) == 0
    names = {p.name for p in
             ProjectLayout(project).read_dir(Area.MARKER_FEEDBACK).iterdir()}
    assert marker_routing.REPORT_FILENAME in names
    assert any(n.endswith(marker_routing.ROUTING_FILE_SUFFIX) for n in names)


# ── The runner really carries it, and really refuses ────────────────
#
# An edgeless DAG: these are about the injection mechanism, not about
# `data_mapping` routing, and a real node would demand its whole upstream
# be present in state first.  Same shape as
# `tests/test_creative_brief_reaches_prompt.py`.

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
    with pytest.raises(UndeliverableNote):
        _gather("select_broll", project, _manifest("clip_catalog"))


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


def test_it_is_restored_around_the_projection_by_name():
    """The other half of the same guarantee, read off the survey's own
    list rather than off the runner, so the two cannot disagree."""
    from library.tools import input_contract
    assert STEP_INPUT_NAME in input_contract._RESTORED_AROUND_PROJECTION


# ── A note that reaches nobody is SAID, not silently dropped ──────────

def test_the_run_summary_names_every_note_that_reached_nobody(tmp_path):
    """Two of 001's three notes reached no model and nothing said so.

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


def test_every_note_reaching_a_prompt_still_says_so():
    routed = [marker_routing.route_note(dict(
        MOMENT_NOTE,
        note="step: select_broll\nthis cutaway is broll of nothing",
        text="step: select_broll\nthis cutaway is broll of nothing"))]
    assert not marker_routing.undelivered(routed)
    lines = "\n".join(marker_routing.undelivered_summary_lines(routed))
    assert "none was dropped" in lines


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


def test_recording_nothing_writes_nothing(tmp_path):
    project = tmp_path / "proj"
    ProjectLayout(str(project)).ensure()
    marker_routing.record_non_delivery(str(project), [])
    assert marker_routing.deliveries(str(project)) == []


def test_the_runner_reports_it_after_the_status_is_decided():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "library" / "processes"
              / "edit_video" / "run_pipeline.py").read_text()
    assert "undelivered_summary_lines" in source
    assert "record_non_delivery" in source
    assert '"notes_reaching_nobody": notes_reaching_nobody' in source
    assert source.index("status = ") < source.index(
        "undelivered_summary_lines")


# ── A note is an INTERVAL on a NAMED TIMELINE ───────────────────────
#
# `data/decisions/region-timeline-identity.md`: a note is on
# `(timeline, span)`. The timeline half has always been on RoutedNote;
# the span half was measured at collection and thrown away here.

def test_a_notes_own_span_reaches_the_prompt():
    """A marker DRAGGED to a length is an interval, and the prompt used
    to be told only `at_timecode` - a point."""
    raw = dict(CLIP_NOTE, frame=744, duration_frames=90)
    routed = marker_routing.route_note(
        raw, "Pipeline_Edit_01", "p.markers.json",
        timeline_fps=30.0, timeline_start_frame=108000)

    assert routed.duration_frames == 90
    # (744 - 108000) is negative here only because this fixture's frame
    # predates the offset; the real pairing is asserted below.
    block = marker_routing.prompt_block([routed])
    assert "at_region" in block["notes"][0]


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


def test_a_span_that_cannot_be_computed_is_absent_not_zero():
    """A pull file written before `timeline_fps` was recorded has no fps.

    A note reported at 0.000..0.000 would read as the first frame of the
    cut, which is a claim about where the captain was looking.
    """
    routed = marker_routing.route_note(CLIP_NOTE, "tl", "p.markers.json")
    assert marker_routing.note_span_seconds(routed) is None
    block = marker_routing.prompt_block([routed])["notes"][0]
    assert block["at_region"] == ""
    assert "on_timeline" not in block


def test_an_unplaced_note_has_no_span():
    raw = dict(CLIP_NOTE, frame=None, unplaced_reason="not on this timeline")
    routed = marker_routing.route_note(raw, "tl", "p.markers.json",
                                       timeline_fps=30.0)
    assert marker_routing.note_span_seconds(routed) is None


def test_a_moment_notes_context_carries_each_clips_span(project_with_notes=None):
    """`clips_under` rendered names only, so the one reader who could act
    on "which of these covers my region" was told the least useful half.

    Still CONTEXT, and it still routes nothing."""
    routed = marker_routing.route_note(MOMENT_NOTE, "tl", "p.markers.json",
                                       timeline_fps=30.0)
    block = marker_routing.prompt_block([routed])["notes"][0]
    assert block["attached_to"] == "moment"
    joined = " ".join(block["clips_under"])
    assert "0..72" in joined, joined
    assert "0..1782" in joined, "the music bed's own span is context too"
    # The withdrawn router stays withdrawn.
    assert routed.target["kind"] == marker_routing.TARGET_MOMENT
    assert "the_clip_under_the_playhead_decides" in \
        marker_routing.WITHDRAWN_ROUTERS


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
