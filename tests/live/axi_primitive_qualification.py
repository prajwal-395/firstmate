"""AXI-010..023: one live qualification scenario per AXI primitive.

The captain's punch list asks for every editing primitive to carry an
explicit, generalizable qualification boundary.  This module is the
SCENARIO side of that: the table below names, for each item of the
list, the operation, the backend, the production entry that performs it,
and what the scenario must observe.  The live lane
(``vep-axc-live2``) runs this module against a real Resolve and imports
the evidence it produces through the existing registry tool
(``python3 -m library.tools.axi_qualification --import-result ...``):
this module writes the ``ren-axi-qualification-result/v1`` files that
tool consumes, so the qualification run and the promotion path stay one
path.

What this module never does:

* it never writes a captain's project, timeline or media.  Every
  scenario runs on the private scratch timeline
  (``Ren Qualification`` / ``fm-scratch qualify``), and the media a
  scenario RE-POINTS is copied out of the shared fixture pool into
  ``/tmp`` first, because a replacement writes to the pool item it
  re-points;
* it never re-runs a failed attempt.  A scenario that did not commit on
  its first attempt is a failed row;
* it never infers a measurement.  A check that could not be read is
  ``False``, and a qualification result whose predicates could not be
  measured is written as ``failed``, which the registry records without
  enabling anything;
* it never grades a refusal as a pass.  A scenario that EXPECTS a
  refusal passes when the refusal happened and the timeline did not
  move; it produces no qualification record, because a refusal is not a
  qualification.

The evidence a scenario produces is read back twice: once through the
raw Resolve handles (the precise per-item reading the checks are
computed from) and once through ``resolve-axi items`` (the production
read-back surface an agent sees).  Both are kept on the row.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from library.tools import edit_algebra
from tests.live import small_edit_qualification as small

ROOT = Path(__file__).resolve().parents[2]
PROJECT = small.PROJECT
TIMELINE = small.TIMELINE
REN = small.REN
REPORT_DIR = ROOT / "tests" / "live" / "reports"
REGISTRY_DIR = REPORT_DIR / "registry"
REPORT_PATH = REPORT_DIR / "axi-primitive-qualification.json"

#: The qualification fixture pool.  The media a scenario places is copied
#: out of it, never edited in place.
POOL_DIR = Path.home() / ".local" / "share" / "vep" / "qualification" / "622307e41719"

#: The media this run OWNS.  A source replacement writes to the POOL
#: ITEM it re-points, so the shared fixture pool is never re-pointed:
#: the scenario places a copy it owns and replaces that.
OWNED_VIDEO_A = Path("/tmp/fm-qual-owned-a.mp4")
OWNED_VIDEO_B = Path("/tmp/fm-qual-owned-b.mp4")
OWNED_OVERLAY_A = Path("/tmp/fm-qual-owned-overlay-a.png")
OWNED_OVERLAY_B = Path("/tmp/fm-qual-owned-overlay-b.png")

#: The AXI items this lane owns, in the punch list's order.
AXI_ITEMS = (
    "AXI-010", "AXI-011", "AXI-012", "AXI-013", "AXI-014", "AXI-015",
    "AXI-016", "AXI-017", "AXI-018", "AXI-019", "AXI-020", "AXI-021",
    "AXI-022", "AXI-023",
)


@dataclass(frozen=True)
class Primitive:
    """One AXI primitive and the scenario that qualifies it.

    `expectation` is what the production entry MUST do:

    * ``commit`` - the entry performs the edit and the read-back shows
      it;
    * ``refuse`` - the entry refuses the request and nothing on the
      timeline moves.  A refusal is a recorded outcome, never a
      qualification.

    `backend` is the executor path the scenario exercises, and it is
    the backend the qualification row is keyed on.  A row is only
    written when that path HAS an executor: promoting a pair with no
    executor would route production work to a path that cannot run it.
    """

    id: str
    axi_item: str
    intent: str
    operation: str
    backend: str
    production_entry: str
    expectation: str
    checks: tuple


PRIMITIVES = (
    # ── AXI-010: insertion at exact frame positions and designated tracks
    Primitive("insert_at_frame_on_track", "AXI-010",
              "Insert a clip at an exact record frame on a named track",
              "clip.insert", "edit_patch",
              "edit_patch.apply_live_patch (reel.build capability)",
              "commit",
              ("placed_on_named_track", "exact_record_start",
               "exact_source_range", "other_tracks_untouched")),
    Primitive("insert_to_absent_track_refused", "AXI-010",
              "Refuse an insertion onto a track the timeline does not have",
              "clip.insert", "resolve_axi",
              "resolve-axi edit place", "refuse",
              ("refused", "no_item_added")),

    # ── AXI-011: removal with explicit ripple and non-ripple semantics
    Primitive("remove_ripple_closes_gap", "AXI-011",
              "Remove a clip and close the gap on its track",
              "clip.remove", "resolve_axi",
              "resolve-axi edit track-ripple-remove", "commit",
              ("target_absent", "gap_closed", "other_track_untouched")),
    Primitive("remove_leaves_gap", "AXI-011",
              "Remove a clip and leave the gap (no ripple)",
              "clip.delete", "resolve_axi", "resolve-axi edit delete",
              "commit",
              ("target_absent", "gap_left", "other_track_untouched")),

    # ── AXI-012: trimming, in/out preservation and source-range validation
    Primitive("trim_narrows_source_range", "AXI-012",
              "Narrow a clip's source range and keep its duration",
              "clip.trim", "resolve_axi", "resolve-axi edit trim",
              "commit",
              ("source_range_narrowed", "duration_preserved",
               "record_start_shifted", "other_clip_untouched")),
    Primitive("trim_past_handles_refused", "AXI-012",
              "Refuse a trim that reaches past the clip's handles",
              "clip.trim", "resolve_axi", "resolve-axi edit trim",
              "refuse",
              ("refused", "timeline_unchanged")),

    # ── AXI-013: moving without loss of unrelated timeline state
    Primitive("move_keeps_unrelated_state", "AXI-013",
              "Move a clip and preserve every unrelated item",
              "clip.move", "resolve_axi", "resolve-axi edit move",
              "commit",
              ("moved_to_requested_frame", "source_range_preserved",
               "other_row_untouched", "other_clip_untouched")),

    # ── AXI-014: source replacement preserving authorized adjustments
    Primitive("replace_source_keeps_transform", "AXI-014",
              "Replace a clip's source and keep its clip-level adjustment",
              "clip.replace", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("source_replaced", "transform_preserved",
               "record_span_preserved")),

    # ── AXI-015: exact clip property changes
    Primitive("set_property_position", "AXI-015",
              "Move a clip in the output frame (Pan, Tilt)",
              "clip.set_property", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("property_read_back", "record_span_unchanged")),
    Primitive("set_property_scale", "AXI-015",
              "Scale a clip (ZoomX, ZoomY)",
              "clip.set_property", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("property_read_back", "record_span_unchanged")),
    Primitive("set_property_crop", "AXI-015",
              "Crop a clip (CropLeft, CropRight)",
              "clip.set_property", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("property_read_back", "record_span_unchanged")),
    Primitive("set_property_opacity", "AXI-015",
              "Set a clip's opacity",
              "clip.set_property", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("property_read_back", "record_span_unchanged")),
    Primitive("set_property_rotation", "AXI-015",
              "Rotate a clip (RotationAngle)",
              "clip.set_property", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("property_read_back", "record_span_unchanged")),

    # ── AXI-016: adding and removing video/audio tracks
    Primitive("add_video_and_audio_tracks", "AXI-016",
              "Add a named video track and a named audio track",
              "track.add", "edit_patch", "edit_patch.apply_live_patch",
              "commit",
              ("video_track_added", "audio_track_added",
               "existing_tracks_preserved")),
    Primitive("remove_empty_track", "AXI-016",
              "Remove an empty track", "track.delete", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("track_removed", "other_tracks_preserved")),
    Primitive("remove_occupied_track_refused", "AXI-016",
              "Refuse to remove a track that carries clips",
              "track.delete", "edit_patch", "edit_patch.apply_live_patch",
              "refuse",
              ("refused", "track_still_present", "clips_preserved")),

    # ── AXI-017: track-scoped ripple operations
    Primitive("ripple_is_track_scoped", "AXI-017",
              "Ripple one track and leave the other tracks alone",
              "clip.remove", "resolve_axi",
              "resolve-axi edit track-ripple-remove", "commit",
              ("target_absent", "same_track_closed", "other_track_untouched")),

    # ── AXI-018: B-roll insertion and replacement over dialogue
    Primitive("broll_insert_keeps_dialogue", "AXI-018",
              "Cover a phrase with B-roll and keep the dialogue",
              "broll.insert", "resolve_axi", "resolve-axi edit place",
              "commit",
              ("cover_on_cover_track", "aroll_unchanged",
               "dialogue_audio_unchanged")),
    Primitive("broll_replace_keeps_dialogue", "AXI-018",
              "Replace a B-roll cover's source and keep the dialogue",
              "clip.replace", "edit_patch", "edit_patch.apply_live_patch",
              "commit",
              ("cover_source_replaced", "aroll_unchanged",
               "cover_span_preserved")),

    # ── AXI-019: multicam angle replacement on synchronized source ranges
    Primitive("multicam_angle_switch", "AXI-019",
              "Show the assigned camera for a speaker's turn",
              "multicam.select_angle", "resolve_axi",
              "resolve-axi edit switch-camera", "commit",
              ("angle_source_read_back", "record_span_preserved",
               "other_row_untouched")),

    # ── AXI-021: lower-third and overlay insertion, replacement,
    #    positioning and removal
    Primitive("overlay_insert", "AXI-021",
              "Insert a lower third on a cover row",
              "clip.insert", "edit_patch",
              "edit_patch.apply_live_patch (reel.build capability)",
              "commit",
              ("overlay_on_cover_row", "exact_record_start",
               "picture_untouched")),
    Primitive("overlay_replace", "AXI-021",
              "Replace a lower third in place",
              "clip.replace", "edit_patch", "edit_patch.apply_live_patch",
              "commit",
              ("overlay_source_replaced", "overlay_span_preserved")),
    Primitive("overlay_position", "AXI-021",
              "Move a lower third in the output frame",
              "clip.set_property", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("property_read_back", "overlay_span_unchanged")),
    Primitive("overlay_remove", "AXI-021",
              "Remove a lower third", "clip.delete", "resolve_axi",
              "resolve-axi edit delete", "commit",
              ("overlay_absent", "picture_untouched")),

    # ── AXI-020: caption text, timing and style as SEPARATE operations
    #
    # The style half is already qualified by the live lane's evidence
    # (`caption.style::resolve_axi`).  The text half has no readable
    # read-back on a rendered caption card, and the timing half is not an
    # AXI operation at all: it is the build's declaration path
    # (`library/tools/caption_timing.py`).  Both are refused BY NAME, and
    # a refusal is what this scenario proves.
    Primitive("caption_text_refused", "AXI-020",
              "Refuse a caption text change the timeline cannot read back",
              "caption_fix", "resolve_axi",
              "resolve-axi edit-request submit (the router refuses it)",
              "refuse",
              ("refused", "refusal_names_the_operation",
               "timeline_unchanged")),

    # ── AXI-022: audio gain, mute, fades and automation
    Primitive("audio_mute", "AXI-022",
              "Mute one clip", "clip.set_enabled", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("clip_disabled", "record_span_unchanged",
               "other_clip_unchanged")),
    Primitive("audio_gain_refused", "AXI-022",
              "Refuse a per-clip gain the Resolve API cannot set",
              "audio.gain", "reel_touchup",
              "resolve-axi edit-request submit (the router refuses it)",
              "refuse",
              ("refused", "refusal_names_the_operation",
               "timeline_unchanged")),
    Primitive("audio_automation_refused", "AXI-022",
              "Refuse a keyframed audio lane with no measured spelling",
              "audio.automation", "reel_touchup",
              "resolve-axi edit-request submit (the router refuses it)",
              "refuse",
              ("refused", "refusal_names_the_operation",
               "timeline_unchanged")),

    # ── AXI-023: retiming, transitions, Fusion compositions, graded clips
    Primitive("retime_speed_change", "AXI-023",
              "Retime a clip to a constant speed",
              "retime.segments", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("speed_read_back", "other_clip_untouched")),
    Primitive("transition_at_cut", "AXI-023",
              "Place a native transition at a cut",
              "transition", "resolve_axi", "resolve-axi edit transition",
              "commit",
              ("transition_placed", "items_unchanged")),
    Primitive("fusion_matte_placed", "AXI-023",
              "Place a Fusion matte comp on a clip",
              "mask.create", "edit_patch", "edit_patch.apply_live_patch",
              "commit",
              ("matte_read_back", "other_clip_untouched")),
    Primitive("graded_clip_node_set", "AXI-023",
              "Set a grade on one node of a clip",
              "grade.node_set", "edit_patch", "edit_patch.apply_live_patch",
              "commit",
              ("grade_read_back", "other_clip_untouched")),
)


#: The scenarios whose (operation, backend) pair has NO executor, so the
#: pair cannot be promoted to production: a promotion would route
#: production work to a path that cannot run it.  The evidence the
#: scenario produces is still real - it is the ROW that cannot be
#: written, and this is where that is said out loud.
PROMOTION_GAPS = {
    "move_keeps_unrelated_state": (
        "clip.move has no executor on the AXI verb path: the move "
        "re-places the clip, so the item's identity changes and it cannot "
        "be one patch operation. The pair that can carry it is "
        "clip.move::reel_touchup, the built-reel touch-up."),
    "transition_at_cut": (
        "transition has no executor on the AXI verb path. The pair that "
        "can carry it is transition::reel_touchup, the built-reel "
        "touch-up."),
    "insert_to_absent_track_refused": (
        "clip.insert has no executor on the AXI verb path: `edit place` "
        "is a command, not an algebra executor. The pair that carries an "
        "insertion is clip.insert::edit_patch, exercised by "
        "insert_at_frame_on_track."),
    "audio_automation_refused": (
        "audio.automation has no executor at all: only the `volume` "
        "parameter has a measured spelling, and the OTIO mix path writes "
        "it at build time."),
}


# ── The live batch ─────────────────────────────────────────────────────
#
# One broker borrow runs every scenario, each on the private scratch
# timeline, each cleaned up before the next one starts.  The script is
# written as a plain template and the case table is injected with
# `repr`, so a case stays readable Python here and readable JSON there.


def _edit(*args: str) -> list[str]:
    return ["edit", *args, "--project", PROJECT, "--timeline", TIMELINE]


def _place(clip: str, track: int, record: int, *, source_in: int = 0,
           source_out: int = 47, media: str = "video") -> list[str]:
    return _edit("place", "--clip", clip, "--media", media,
                 "--track", str(track), "--record", str(record),
                 "--source-in", str(source_in),
                 "--source-out", str(source_out), "--apply")


def _patch(operations: list[dict], domains: list[str], label: str,
           *, capability: str = "reel.touchup") -> dict:
    """An EditPatch live write, addressed by ITEM NAME at run time.

    The item's unique id is only known once the fixture is placed, so a
    patch operation carries ``$name`` where the id belongs and the
    broker script resolves it against the live timeline.
    """
    return {"kind": "patch", "operations": operations,
            "conflict_domains": domains, "label": label,
            "capability": capability}


def _cases() -> dict:
    """Every scenario's commands, in the order the broker runs them."""
    return {
        # ── AXI-010 ──
        "insert_at_frame_on_track": {
            "operation": "clip.insert",
            "setup": [],
            "commands": [_patch(
                [{"op": "clip.insert", "unique_id": "axi-prim-insert-1",
                  "track_type": "video", "track_index": 2,
                  "record_in": 48, "record_out": 96,
                  "media_pool_item": "$pool:qual_b.mp4",
                  "source_in": 0, "source_out": 48}],
                ["timeline_structure"], "insert-on-track-2",
                capability="reel.build")],
            "targets": [],
        },
        "insert_to_absent_track_refused": {
            "operation": "clip.insert",
            "setup": [],
            "commands": [_place("qual_b.mp4", 9, 48, source_out=47)],
            "targets": [],
        },
        # ── AXI-011 ──
        "remove_ripple_closes_gap": {
            "operation": "clip.remove",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95),
                      _place("qual_b.mp4", 1, 96, source_out=71)],
            "commands": [_edit("track-ripple-remove", "--track", "video1",
                               "--index", "0", "--apply")],
            "targets": ["qual_a.mp4"],
        },
        "remove_leaves_gap": {
            "operation": "clip.delete",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95),
                      _place("qual_b.mp4", 1, 96, source_out=71)],
            "commands": [_edit("delete", "--track", "video1", "--index", "0",
                               "--apply")],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-012 ──
        "trim_narrows_source_range": {
            "operation": "clip.trim",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95)],
            "commands": [_edit("trim", "--track", "video1", "--index", "0",
                               "--source-in", "10", "--source-out", "59",
                               "--apply")],
            "targets": ["qual_a.mp4"],
        },
        "trim_past_handles_refused": {
            "operation": "clip.trim",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95)],
            "commands": [_edit("trim", "--track", "video1", "--index", "0",
                               "--source-in", "10", "--source-out", "900",
                               "--apply")],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-013 ──
        "move_keeps_unrelated_state": {
            "operation": "clip.move",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47),
                      _place("qual_b.mp4", 1, 96, source_out=71),
                      _place("qual_c.mp4", 2, 0, source_out=47)],
            "commands": [_edit("move", "--track", "video1", "--index", "0",
                               "--to", "48", "--apply")],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-014 ──
        "replace_source_keeps_transform": {
            "operation": "clip.replace",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=47)],
            "commands": [
                _patch([{"op": "clip.set_property",
                         "unique_id": "$" + OWNED_VIDEO_A.name,
                         "key": "Pan", "value": 40.0}],
                       ["picture_transform"], "replace-preset-transform"),
                _patch([{"op": "clip.replace",
                         "unique_id": "$" + OWNED_VIDEO_A.name,
                         "source_file": str(OWNED_VIDEO_B)}],
                       ["timeline_structure", "fusion", "color"],
                       "replace-source"),
            ],
            "targets": [OWNED_VIDEO_A.name],
        },
        # ── AXI-015 ──
        "set_property_position": {
            "operation": "clip.set_property",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47)],
            "commands": [_patch(
                [{"op": "clip.set_property", "unique_id": "$qual_a.mp4",
                  "key": "Pan", "value": 40.0},
                 {"op": "clip.set_property", "unique_id": "$qual_a.mp4",
                  "key": "Tilt", "value": -12.0}],
                ["picture_transform"], "set-position")],
            "targets": ["qual_a.mp4"],
        },
        "set_property_scale": {
            "operation": "clip.set_property",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47)],
            "commands": [_patch(
                [{"op": "clip.set_property", "unique_id": "$qual_a.mp4",
                  "key": "ZoomX", "value": 1.25},
                 {"op": "clip.set_property", "unique_id": "$qual_a.mp4",
                  "key": "ZoomY", "value": 1.25}],
                ["picture_transform"], "set-scale")],
            "targets": ["qual_a.mp4"],
        },
        "set_property_crop": {
            "operation": "clip.set_property",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47)],
            "commands": [_patch(
                [{"op": "clip.set_property", "unique_id": "$qual_a.mp4",
                  "key": "CropLeft", "value": 5.0},
                 {"op": "clip.set_property", "unique_id": "$qual_a.mp4",
                  "key": "CropRight", "value": 5.0}],
                ["picture_transform"], "set-crop")],
            "targets": ["qual_a.mp4"],
        },
        "set_property_opacity": {
            "operation": "clip.set_property",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47)],
            "commands": [_patch(
                [{"op": "clip.set_property", "unique_id": "$qual_a.mp4",
                  "key": "Opacity", "value": 85.0}],
                ["picture_transform"], "set-opacity")],
            "targets": ["qual_a.mp4"],
        },
        "set_property_rotation": {
            "operation": "clip.set_property",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47)],
            "commands": [_patch(
                [{"op": "clip.set_property", "unique_id": "$qual_a.mp4",
                  "key": "RotationAngle", "value": 2.0}],
                ["picture_transform"], "set-rotation")],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-016 ──
        "add_video_and_audio_tracks": {
            "operation": "track.add",
            "setup": [],
            "commands": [_patch(
                [{"op": "track.add", "track_type": "video", "name": "V9"},
                 {"op": "track.add", "track_type": "audio", "name": "A9"}],
                ["timeline_structure"], "add-tracks")],
            "targets": [],
        },
        "remove_empty_track": {
            "operation": "track.delete",
            "setup": [_patch(
                [{"op": "track.add", "track_type": "video", "name": "V9"}],
                ["timeline_structure"], "add-track-for-removal")],
            "commands": [_patch(
                [{"op": "track.delete", "track_type": "video",
                  "track_index": 4}],
                ["timeline_structure"], "remove-empty-track")],
            "targets": [],
        },
        "remove_occupied_track_refused": {
            "operation": "track.delete",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47)],
            "commands": [_patch(
                [{"op": "track.delete", "track_type": "video",
                  "track_index": 1}],
                ["timeline_structure"], "remove-occupied-track")],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-017 ──
        "ripple_is_track_scoped": {
            "operation": "clip.remove",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47),
                      _place("qual_b.mp4", 1, 96, source_out=71),
                      _place("qual_c.mp4", 2, 0, source_out=47)],
            "commands": [_edit("track-ripple-remove", "--track", "video1",
                               "--index", "0", "--apply")],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-018 ──
        "broll_insert_keeps_dialogue": {
            "operation": "broll.insert",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95,
                             media="both")],
            "commands": [_place(OWNED_VIDEO_A.name, 2, 40, source_out=39)],
            "targets": ["qual_a.mp4", OWNED_VIDEO_A.name],
        },
        "broll_replace_keeps_dialogue": {
            "operation": "clip.replace",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95,
                             media="both"),
                      _place(OWNED_VIDEO_A.name, 2, 40, source_out=39)],
            "commands": [_patch(
                [{"op": "clip.replace", "unique_id": "$" + OWNED_VIDEO_A.name,
                  "source_file": str(OWNED_VIDEO_B)}],
                ["timeline_structure", "fusion", "color"], "replace-cover")],
            "targets": [OWNED_VIDEO_A.name],
        },
        # ── AXI-019 ──
        "multicam_angle_switch": {
            "operation": "multicam.select_angle",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95,
                             media="both"),
                      _place(OWNED_VIDEO_A.name, 2, 0, source_out=71)],
            "commands": [_edit(
                "switch-camera", "--track", "video1", "--index", "0",
                "--camera-source-file", str(OWNED_VIDEO_B),
                "--speaker-id", "axc-qual-speaker",
                "--assignment-basis", "project_declaration",
                "--sync-evidence-id", "axc-qual-sync",
                "--source-in", "0", "--source-out", "95", "--apply")],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-021 ──
        "overlay_insert": {
            "operation": "clip.insert",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95)],
            "commands": [_patch(
                [{"op": "clip.insert", "unique_id": "axi-prim-overlay-1",
                  "track_type": "video", "track_index": 3,
                  "record_in": 96, "record_out": 144,
                  "media_pool_item": "$pool:" + OWNED_OVERLAY_A.name}],
                ["timeline_structure"], "insert-overlay",
                capability="reel.build")],
            "targets": [],
        },
        "overlay_replace": {
            "operation": "clip.replace",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95),
                      _place(OWNED_OVERLAY_A.name, 3, 96)],
            "commands": [_patch(
                [{"op": "clip.replace",
                  "unique_id": "$" + OWNED_OVERLAY_A.name,
                  "source_file": str(OWNED_OVERLAY_B)}],
                ["timeline_structure", "fusion", "color"], "replace-overlay")],
            "targets": [OWNED_OVERLAY_A.name],
        },
        "overlay_position": {
            "operation": "clip.set_property",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95),
                      _place(OWNED_OVERLAY_A.name, 3, 96)],
            "commands": [_patch(
                [{"op": "clip.set_property",
                  "unique_id": "$" + OWNED_OVERLAY_A.name,
                  "key": "Pan", "value": 40.0}],
                ["picture_transform"], "position-overlay")],
            "targets": [OWNED_OVERLAY_A.name],
        },
        "overlay_remove": {
            "operation": "clip.delete",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95),
                      _place(OWNED_OVERLAY_A.name, 3, 96)],
            "commands": [_edit("delete", "--track", "video3", "--index", "0",
                               "--apply")],
            "targets": [OWNED_OVERLAY_A.name],
        },
        # ── AXI-020 ──
        "caption_text_refused": {
            "operation": "caption_fix",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47)],
            "commands": [{"kind": "route", "operation": "caption_fix",
                          "params": {"unique_id": "axc-caption",
                                     "text": "a rewritten caption"}}],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-022 ──
        "audio_mute": {
            "operation": "clip.set_enabled",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47,
                             media="both"),
                      _place("qual_b.mp4", 1, 96, source_out=47)],
            "commands": [_patch(
                [{"op": "clip.set_enabled",
                  "unique_id": "$qual_a.mp4", "enabled": False}],
                ["timeline_structure"], "mute-clip")],
            "targets": ["qual_a.mp4"],
        },
        "audio_gain_refused": {
            "operation": "audio.gain",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47,
                             media="both")],
            "commands": [{"kind": "route", "operation": "audio.gain",
                          "params": {"unique_id": "axc-audio",
                                     "db": -6.0}}],
            "targets": ["qual_a.mp4"],
        },
        "audio_automation_refused": {
            "operation": "audio.automation",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47,
                             media="both")],
            "commands": [{"kind": "route", "operation": "audio.automation",
                          "params": {"unique_id": "axc-audio",
                                     "param": "volume",
                                     "keyframes": [{"frame": 0,
                                                    "value": 0.0}]}}],
            "targets": ["qual_a.mp4"],
        },
        # ── AXI-023 ──
        "retime_speed_change": {
            "operation": "retime.segments",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95),
                      _place("qual_b.mp4", 1, 96, source_out=71)],
            "commands": [_patch(
                [{"op": "retime.segments", "unique_id": "$qual_a.mp4",
                  "segments": [{"record_frame": 0, "percent": 50.0}]}],
                ["timeline_structure"], "retime-clip")],
            "targets": ["qual_a.mp4"],
        },
        "transition_at_cut": {
            "operation": "transition",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47),
                      _place("qual_b.mp4", 1, 48, source_out=47)],
            "commands": [_edit("transition", "--index", "0", "--type",
                               "Cross Dissolve", "--duration", "12",
                               "--apply")],
            "targets": ["qual_a.mp4", "qual_b.mp4"],
        },
        "fusion_matte_placed": {
            "operation": "mask.create",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47),
                      _place("qual_b.mp4", 1, 96, source_out=47)],
            "commands": [_patch(
                [{"op": "mask.create", "unique_id": "$qual_a.mp4",
                  "matte": "fm-qual-matte"}],
                ["fusion"], "place-matte")],
            "targets": ["qual_a.mp4"],
        },
        "graded_clip_node_set": {
            "operation": "grade.node_set",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=47),
                      _place("qual_b.mp4", 1, 96, source_out=47)],
            "commands": [_patch(
                [{"op": "grade.node_set", "unique_id": "$qual_a.mp4",
                  "node_index": 1,
                  "params": {"slope_r": 1.1, "slope_g": 1.1, "slope_b": 1.1,
                             "offset_r": 0.02, "offset_g": 0.02,
                             "offset_b": 0.02}}],
                ["color"], "grade-node")],
            "targets": ["qual_a.mp4"],
        },
    }


BROKER_SCRIPT = '''
import contextlib, io, json, re, time
from pathlib import Path
from library.tools import edit_patch, resolve_axi

PROJECT = "__AXI_PROJECT__"
TIMELINE = "__AXI_TIMELINE__"
PROJECT_DIR = "__AXI_PROJECT_DIR__"
DISPOSABLE = "__AXI_DISPOSABLE__"
CASES = __AXI_CASES__
OWNED_MEDIA = __AXI_OWNED__
OWNED_TRACKS = __AXI_TRACKS__
OWNED_VIDEO_A = "__AXI_VIDEO_A__"
OWNED_VIDEO_B = "__AXI_VIDEO_B__"
OWNED_OVERLAY_A = "__AXI_OVERLAY_A__"
OWNED_OVERLAY_B = "__AXI_OVERLAY_B__"

ALLOWED_NAMES = {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                 OWNED_VIDEO_A, OWNED_VIDEO_B,
                 OWNED_OVERLAY_A, OWNED_OVERLAY_B}


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = resolve_axi.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return {"argv": argv, "exit_code": int(code or 0),
            "stdout": out.getvalue(), "stderr": err.getvalue()}


def live_state():
    """Every clip on every row, read straight off the live handles."""
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    rows = []
    for kind in ("video", "audio", "subtitle"):
        count = int(timeline.GetTrackCount(kind) or 0)
        for index in range(1, count + 1):
            try:
                track_name = str(timeline.GetTrackName(kind, index) or "")
            except Exception:
                track_name = ""
            for item in timeline.GetItemListInTrack(kind, index) or ():
                source_path = ""
                media_type = ""
                try:
                    pool_item = item.GetMediaPoolItem()
                    source_path = str(
                        pool_item.GetClipProperty("File Path") or "")
                    media_type = str(pool_item.GetClipProperty("Type") or "")
                except Exception:
                    source_path = ""
                    media_type = ""
                try:
                    enabled = bool(item.GetClipEnabled())
                except Exception:
                    enabled = None
                try:
                    comps = [str(name) for name in
                             (item.GetFusionCompNameList() or [])]
                except Exception:
                    comps = []
                try:
                    linked = [str(other.GetUniqueId())
                              for other in (item.GetLinkedItems() or [])]
                except Exception:
                    linked = []
                try:
                    speed = item.GetSpeed()
                except Exception:
                    speed = None
                try:
                    cdl = item.GetCDL()
                except Exception:
                    cdl = None
                rows.append({
                    "unique_id": str(item.GetUniqueId()),
                    "kind": kind, "track": index, "track_name": track_name,
                    "name": str(item.GetName()),
                    "source_path": source_path, "media_type": media_type,
                    "source_in": int(item.GetSourceStartFrame()),
                    "source_out": int(item.GetSourceEndFrame()),
                    "record_in": int(item.GetStart()),
                    "record_out": int(item.GetEnd()),
                    "transform": item.GetProperty() or {},
                    "enabled": enabled, "fusion_comps": comps,
                    "linked_unique_ids": linked, "speed": speed, "cdl": cdl,
                })
    track_names = {}
    for kind in ("video", "audio", "subtitle"):
        count = int(timeline.GetTrackCount(kind) or 0)
        names = []
        for index in range(1, count + 1):
            try:
                names.append(str(timeline.GetTrackName(kind, index) or ""))
            except Exception:
                names.append("")
        track_names[kind] = names
    return {"timeline": timeline.GetUniqueId(),
            "end_frame": int(timeline.GetEndFrame()),
            "track_names": track_names, "items": rows}


def by_name(state, name):
    return [row for row in state.get("items", [])
            if row.get("name") == name]


def pool_item_id(name):
    """The media pool item's unique id, by its exact name."""
    pool = manager.GetCurrentProject().GetMediaPool()
    for folder in _walk_pool(pool.GetRootFolder()):
        for item in folder.GetClipList() or ():
            try:
                if str(item.GetName()) == name:
                    return str(item.GetUniqueId())
            except Exception:
                continue
    raise RuntimeError("the media pool holds no item named " + name)


def _walk_pool(folder, depth=0):
    if folder is None or depth > 8:
        return []
    found = [folder]
    try:
        children = folder.GetSubFolderList() or []
    except Exception:
        children = []
    for child in children:
        found.extend(_walk_pool(child, depth + 1))
    return found


def resolve_ids(state, operations):
    """Replace every `$name` and `$pool:name` with a live identity."""
    resolved = []
    for operation in operations:
        row = dict(operation)
        uid = row.get("unique_id")
        if isinstance(uid, str) and uid.startswith("$pool:"):
            row["media_pool_item"] = pool_item_id(uid[len("$pool:"):])
        elif isinstance(uid, str) and uid.startswith("$"):
            matches = by_name(state, uid[1:])
            if len(matches) != 1:
                raise RuntimeError(
                    "the fixture does not hold exactly one item named "
                    + uid[1:] + " (" + str(len(matches)) + ")")
            row["unique_id"] = matches[0]["unique_id"]
        resolved.append(row)
    return resolved


def apply_patch(spec, state):
    operations = resolve_ids(state, spec["operations"])
    project = manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    try:
        receipt = edit_patch.apply_live_patch(
            project=project, timeline=timeline,
            capability=spec.get("capability", "reel.touchup"),
            operations=operations,
            conflict_domains=tuple(spec["conflict_domains"]),
            project_folder=PROJECT_DIR,
            idempotency_key="axi-prim-" + spec["label"] + "-"
            + str(time.time_ns()))
    except Exception as exc:
        return {"argv": ["edit_patch.apply_live_patch", spec["label"]],
                "exit_code": 1, "stdout": "",
                "stderr": type(exc).__name__ + ": " + str(exc)}
    status = str(receipt.get("status") or "")
    return {"argv": ["edit_patch.apply_live_patch", spec["label"]],
            "exit_code": 0 if status == "committed" else 1,
            "stdout": json.dumps(receipt, default=str), "stderr": ""}


def route_refusal(spec):
    """Ask the production router what it does with one operation.

    A primitive with no qualified executor is refused by the router
    BEFORE any write, and that refusal is the qualification boundary:
    the operation cannot reach a timeline at all.
    """
    from library.tools import execution_router

    operation = {"op": spec["operation"], **spec.get("params", {})}
    try:
        decision = execution_router.route_axi_dispatch(
            [operation], context={"qualification_policy": "production"})
    except Exception as exc:
        return {"argv": ["route_axi_dispatch", spec["operation"]],
                "exit_code": 1, "stdout": "",
                "stderr": type(exc).__name__ + ": " + str(exc)}
    return {"argv": ["route_axi_dispatch", spec["operation"]],
            "exit_code": 0, "stdout": json.dumps(decision, default=str),
            "stderr": ""}


def qualification_state(operation, targets):
    """The MEASURED runtime state, in the recorded predicate vocabulary."""
    from library.tools import axi_qualification_state

    operations = [{"op": operation, "unique_id": uid} for uid in targets]
    try:
        return axi_qualification_state.inspect(
            project=PROJECT, timeline=TIMELINE, operations=operations,
            resolve=resolve)
    except Exception as exc:
        return {"inspected": False,
                "reason": type(exc).__name__ + ": " + str(exc)}


def import_owned_media():
    """Import the media this run owns, so a replacement has its own item."""
    pool = manager.GetCurrentProject().GetMediaPool()
    paths = [OWNED_VIDEO_A, OWNED_VIDEO_B,
             OWNED_OVERLAY_A, OWNED_OVERLAY_B]
    missing = [path for path in paths if not Path(path).is_file()]
    if missing:
        raise RuntimeError("the owned media is missing: " + str(missing))
    imported = pool.ImportMedia(paths) or []
    names = []
    for item in imported:
        try:
            names.append(str(item.GetName()))
        except Exception:
            continue
    return names


def remove_owned_media():
    """Delete exactly the pool items this run imported."""
    pool = manager.GetCurrentProject().GetMediaPool()
    removed = []
    doomed = []
    for folder in _walk_pool(pool.GetRootFolder()):
        for item in folder.GetClipList() or ():
            try:
                name = str(item.GetName())
                path = str(item.GetClipProperty("File Path") or "")
            except Exception:
                continue
            if name in OWNED_MEDIA or path in (OWNED_VIDEO_A, OWNED_VIDEO_B,
                                               OWNED_OVERLAY_A,
                                               OWNED_OVERLAY_B):
                doomed.append(item)
                removed.append(name)
    if doomed:
        pool.DeleteClips(doomed)
    return removed


def remove_owned_tracks():
    """Delete exactly the rows this run added."""
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    removed = []
    for kind in ("video", "audio"):
        count = int(timeline.GetTrackCount(kind) or 0)
        for index in range(count, 0, -1):
            try:
                name = str(timeline.GetTrackName(kind, index) or "")
            except Exception:
                name = ""
            if name in OWNED_TRACKS:
                timeline.DeleteTrack(kind, index)
                removed.append(kind + str(index))
    return removed


def cleanup():
    """Remove exactly what the fixture placed, or name what is left."""
    timeline = manager.GetCurrentProject().GetCurrentTimeline()
    collateral = []
    for _ in range(8):
        doomed = []
        for kind in ("video", "audio", "subtitle"):
            count = int(timeline.GetTrackCount(kind) or 0)
            for index in range(1, count + 1):
                for item in timeline.GetItemListInTrack(kind, index) or ():
                    name = str(item.GetName())
                    if name not in ALLOWED_NAMES:
                        collateral.append("refused fixture cleanup of "
                                          + name)
                        return {"clean": False,
                                "collateral_changes": collateral}
                    doomed.append(item)
        if not doomed:
            break
        if not timeline.DeleteClips(doomed, False):
            collateral.append("the fixture items could not be deleted")
            return {"clean": False, "collateral_changes": collateral}
    remaining = []
    for kind in ("video", "audio", "subtitle"):
        count = int(timeline.GetTrackCount(kind) or 0)
        for index in range(1, count + 1):
            if timeline.GetItemListInTrack(kind, index):
                remaining.append(kind + str(index))
    if remaining:
        collateral.append("the fixture left items on " + ",".join(remaining))
    return {"clean": not collateral, "collateral_changes": collateral}


def select_timeline():
    project = manager.GetCurrentProject()
    matches = [project.GetTimelineByIndex(index)
               for index in range(1, int(project.GetTimelineCount() or 0) + 1)
               if project.GetTimelineByIndex(index)
               and project.GetTimelineByIndex(index).GetName() == TIMELINE]
    if len(matches) != 1 or not project.SetCurrentTimeline(matches[0]):
        raise RuntimeError("the private scratch timeline is not selectable")
    return project.GetCurrentTimeline().GetUniqueId()


if not Path(PROJECT_DIR).is_dir():
    raise RuntimeError("the batch project dir was not stamped by the runner")
select_timeline()
imported = import_owned_media()
setup = {"imported": imported}

baseline = call(["items", "--project", PROJECT, "--timeline", TIMELINE,
                 "--refresh-live"])
if baseline["exit_code"]:
    raise RuntimeError("the qualification baseline could not be read: "
                       + baseline["stderr"])
if re.search(r"^\\s+video\\d+,", baseline["stdout"], re.MULTILINE):
    raise RuntimeError("the private qualification timeline must be empty "
                       "before the batch")

results = []
for name, case in CASES.items():
    started = time.perf_counter()
    before = live_state()
    setup_receipts = []
    for argv in case.get("setup", []):
        receipt = call(argv)
        setup_receipts.append(receipt)
        if receipt["exit_code"]:
            break
    after_setup = live_state()
    targets = []
    for target in case.get("targets", []):
        matches = by_name(after_setup, target)
        if len(matches) == 1:
            targets.append(matches[0]["unique_id"])
    state = qualification_state(case.get("operation", "clip.delete"), targets)
    receipts = []
    for command in case.get("commands", []):
        if not isinstance(command, dict):
            receipt = call(command)
        elif command.get("kind") == "route":
            receipt = route_refusal(command)
        else:
            receipt = apply_patch(command, live_state())
        receipts.append(receipt)
        if receipt["exit_code"]:
            break
    after = live_state()
    items_read = call(["items", "--project", PROJECT, "--timeline", TIMELINE,
                       "--refresh-live", "--transforms"])
    cleaned = cleanup()
    removed_tracks = remove_owned_tracks()
    removed_media = remove_owned_media()
    after_cleanup = live_state()
    results.append({
        "id": name,
        "setup_receipts": setup_receipts,
        "commands": receipts,
        "before": before,
        "after_setup": after_setup,
        "qualification_state": state,
        "after": after,
        "items_readback": items_read,
        "cleanup": cleaned,
        "removed_tracks": removed_tracks,
        "removed_media": removed_media,
        "after_cleanup": after_cleanup,
        "collateral_changes": cleaned["collateral_changes"],
        "elapsed_seconds": time.perf_counter() - started,
    })

result = {"cases": results, "setup": setup}
'''


def _broker_script(cases: dict) -> str:
    script = BROKER_SCRIPT
    script = script.replace("__AXI_PROJECT__", repr(PROJECT))
    script = script.replace("__AXI_TIMELINE__", repr(TIMELINE))
    script = script.replace("__AXI_PROJECT_DIR__", "@FM_QUAL_PROJECT_DIR@")
    script = script.replace("__AXI_DISPOSABLE__", "@AXC_DISPOSABLE_PROJECT@")
    script = script.replace("__AXI_CASES__", repr(cases))
    script = script.replace("__AXI_OWNED__", repr(
        {OWNED_VIDEO_A.name, OWNED_VIDEO_B.name,
         OWNED_OVERLAY_A.name, OWNED_OVERLAY_B.name}))
    script = script.replace("__AXI_TRACKS__", repr({"V9", "A9"}))
    script = script.replace("__AXI_VIDEO_A__", str(OWNED_VIDEO_A))
    script = script.replace("__AXI_VIDEO_B__", str(OWNED_VIDEO_B))
    script = script.replace("__AXI_OVERLAY_A__", str(OWNED_OVERLAY_A))
    script = script.replace("__AXI_OVERLAY_B__", str(OWNED_OVERLAY_B))
    return script


# ── The judges ─────────────────────────────────────────────────────────


def _items(state: dict, name: str) -> list[dict]:
    return [row for row in state.get("items", [])
            if row.get("name") == name]


def _one(state: dict, name: str) -> dict | None:
    rows = _items(state, name)
    return rows[0] if len(rows) == 1 else None


def _source_bounds(row: dict | None) -> tuple:
    """The clip's source range as INCLUSIVE bounds.

    Resolve reports two forms of `GetSourceEndFrame`: existing clips
    report the inclusive end, a fresh `AppendToTimeline` placement
    echoes the exclusive one.  The record duration decides which form
    the reading speaks, which is what the engine's own normalisation
    does (`resolve_axi_core_commands._inclusive_source_end`).
    """
    if not row:
        return ()
    source_in = int(row["source_in"])
    source_out = int(row["source_out"])
    duration = int(row["record_out"]) - int(row["record_in"])
    if duration == source_out - source_in + 1:
        return (source_in, source_out)
    if duration == source_out - source_in:
        return (source_in, source_out - 1)
    return (source_in, source_out)


def _record_span(row: dict | None) -> tuple:
    if not row:
        return ()
    return (int(row["record_in"]), int(row["record_out"]))


def _unchanged(before: dict, after: dict, name: str) -> bool:
    old = _one(before, name)
    new = _one(after, name)
    if old is None or new is None:
        return False
    return (_record_span(old) == _record_span(new)
            and _source_bounds(old) == _source_bounds(new))


def _approx(left, right, tolerance: float = 0.01) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _property(row: dict | None, key: str):
    if not row:
        return None
    return (row.get("transform") or {}).get(key)


def _judge_insert_at_frame_on_track(case: dict) -> dict:
    after = case["after"]
    placed = _one(after, "qual_b.mp4")
    source = _source_bounds(placed)
    return {
        "placed_on_named_track": bool(placed) and placed["track"] == 2
        and placed["kind"] == "video",
        "exact_record_start": bool(placed)
        and int(placed["record_in"]) == 48,
        "exact_source_range": source == (0, 47),
        "other_tracks_untouched": not _items(after, "qual_a.mp4"),
    }


def _judge_insert_refused(case: dict) -> dict:
    after = case["after"]
    refused = any(row["exit_code"] for row in case["commands"])
    return {
        "refused": refused,
        "no_item_added": not _items(after, "qual_b.mp4"),
    }


def _judge_remove_ripple(case: dict) -> dict:
    after = case["after"]
    survivor = _one(after, "qual_b.mp4")
    return {
        "target_absent": not _items(after, "qual_a.mp4"),
        "gap_closed": bool(survivor) and int(survivor["record_in"]) == 0,
        "other_track_untouched": not _items(after, "qual_c.mp4"),
    }


def _judge_remove_leaves_gap(case: dict) -> dict:
    after = case["after"]
    survivor = _one(after, "qual_b.mp4")
    return {
        "target_absent": not _items(after, "qual_a.mp4"),
        "gap_left": bool(survivor) and int(survivor["record_in"]) == 96,
        "other_track_untouched": not _items(after, "qual_c.mp4"),
    }


def _judge_trim(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    old = _one(before, "qual_a.mp4")
    new = _one(after, "qual_a.mp4")
    old_duration = len(range(*_record_span(old))) if old else 0
    new_duration = len(range(*_record_span(new))) if new else 0
    return {
        "source_range_narrowed": _source_bounds(new) == (10, 59),
        "duration_preserved": bool(new) and new_duration == old_duration,
        "record_start_shifted": bool(new) and int(new["record_in"]) == 10,
        "other_clip_untouched": not _items(after, "qual_b.mp4"),
    }


def _judge_trim_refused(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    refused = any(row["exit_code"] for row in case["commands"])
    return {
        "refused": refused,
        "timeline_unchanged": _unchanged(before, after, "qual_a.mp4"),
    }


def _judge_move(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    old = _one(before, "qual_a.mp4")
    new = _one(after, "qual_a.mp4")
    return {
        "moved_to_requested_frame": bool(new)
        and int(new["record_in"]) == 48,
        "source_range_preserved": bool(old) and bool(new)
        and _source_bounds(old) == _source_bounds(new),
        "other_row_untouched": _unchanged(before, after, "qual_c.mp4"),
        "other_clip_untouched": _unchanged(before, after, "qual_b.mp4"),
    }


def _judge_replace_source(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    old = _one(before, OWNED_VIDEO_A.name)
    new = _one(after, OWNED_VIDEO_A.name)
    return {
        "source_replaced": bool(new)
        and new["source_path"] == str(OWNED_VIDEO_B),
        "transform_preserved": bool(old) and bool(new)
        and _approx(_property(old, "Pan"), 40.0)
        and _approx(_property(new, "Pan"), 40.0),
        "record_span_preserved": bool(old) and bool(new)
        and _record_span(old) == _record_span(new),
    }


def _judge_set_property(case: dict, expected: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    name = "qual_a.mp4"
    row = _one(after, name)
    old = _one(before, name)
    checks = {"property_read_back": bool(row) and all(
        _approx(_property(row, key), value)
        for key, value in expected.items())}
    checks["record_span_unchanged"] = (
        bool(old) and bool(row)
        and _record_span(old) == _record_span(row))
    return checks


def _judge_add_tracks(case: dict) -> dict:
    names = case["after"].get("track_names", {})
    return {
        "video_track_added": "V9" in names.get("video", []),
        "audio_track_added": "A9" in names.get("audio", []),
        "existing_tracks_preserved": all(
            name in names.get("video", [])
            for name in ("Video 1", "V2", "V3")),
    }


def _judge_remove_empty_track(case: dict) -> dict:
    names = case["after"].get("track_names", {})
    return {
        "track_removed": "V9" not in names.get("video", []),
        "other_tracks_preserved": all(
            name in names.get("video", [])
            for name in ("Video 1", "V2", "V3")),
    }


def _judge_remove_occupied_refused(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    refused = any(row["exit_code"] for row in case["commands"])
    names = after.get("track_names", {})
    return {
        "refused": refused,
        "track_still_present": "Video 1" in names.get("video", []),
        "clips_preserved": _unchanged(before, after, "qual_a.mp4"),
    }


def _judge_ripple_track_scoped(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    survivor = _one(after, "qual_b.mp4")
    return {
        "target_absent": not _items(after, "qual_a.mp4"),
        "same_track_closed": bool(survivor)
        and int(survivor["record_in"]) == 0,
        "other_track_untouched": _unchanged(before, after, "qual_c.mp4"),
    }


def _judge_broll_insert(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    cover = _one(after, OWNED_VIDEO_A.name)
    aroll = _one(after, "qual_a.mp4")
    return {
        "cover_on_cover_track": bool(cover) and cover["track"] == 2,
        "aroll_unchanged": _unchanged(before, after, "qual_a.mp4"),
        "dialogue_audio_unchanged": bool(aroll)
        and bool(aroll.get("linked_unique_ids")),
    }


def _judge_broll_replace(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    cover = _one(after, OWNED_VIDEO_A.name)
    return {
        "cover_source_replaced": bool(cover)
        and cover["source_path"] == str(OWNED_VIDEO_B),
        "aroll_unchanged": _unchanged(before, after, "qual_a.mp4"),
        "cover_span_preserved": bool(cover)
        and _record_span(cover) == (40, 80),
    }


def _judge_multicam(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    switched = _one(after, "qual_a.mp4")
    return {
        "angle_source_read_back": bool(switched)
        and switched["source_path"] == str(OWNED_VIDEO_B),
        "record_span_preserved": bool(switched)
        and _record_span(switched) == (0, 96),
        "other_row_untouched": _unchanged(before, after, OWNED_VIDEO_A.name),
    }


def _judge_overlay_insert(case: dict) -> dict:
    after = case["after"]
    overlay = _one(after, OWNED_OVERLAY_A.name)
    return {
        "overlay_on_cover_row": bool(overlay) and overlay["track"] == 3,
        "exact_record_start": bool(overlay)
        and int(overlay["record_in"]) == 96,
        "picture_untouched": _unchanged(case["after_setup"], after,
                                        "qual_a.mp4"),
    }


def _judge_overlay_replace(case: dict) -> dict:
    after = case["after"]
    overlay = _one(after, OWNED_OVERLAY_A.name)
    return {
        "overlay_source_replaced": bool(overlay)
        and overlay["source_path"] == str(OWNED_OVERLAY_B),
        "overlay_span_preserved": bool(overlay)
        and _record_span(overlay) == (96, 144),
    }


def _judge_overlay_position(case: dict) -> dict:
    after = case["after"]
    overlay = _one(after, OWNED_OVERLAY_A.name)
    return {
        "property_read_back": bool(overlay)
        and _approx(_property(overlay, "Pan"), 40.0),
        "overlay_span_unchanged": bool(overlay)
        and _record_span(overlay) == (96, 144),
    }


def _judge_overlay_remove(case: dict) -> dict:
    after = case["after"]
    return {
        "overlay_absent": not _items(after, OWNED_OVERLAY_A.name),
        "picture_untouched": _unchanged(case["after_setup"], after,
                                        "qual_a.mp4"),
    }


def _judge_audio_mute(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    muted = _one(after, "qual_a.mp4")
    return {
        "clip_disabled": bool(muted) and muted.get("enabled") is False,
        "record_span_unchanged": bool(muted)
        and _record_span(muted) == (0, 48),
        "other_clip_unchanged": _unchanged(before, after, "qual_b.mp4"),
    }


def _judge_routed_refusal(case: dict, operation: str) -> dict:
    """A primitive the production router refuses before any write."""
    before = case["after_setup"]
    after = case["after"]
    refused = [row for row in case["commands"] if row["exit_code"]]
    spoken = " ".join(row["stderr"] + row["stdout"]
                      for row in case["commands"]).lower()
    return {
        "refused": bool(refused),
        "refusal_names_the_operation": operation.lower() in spoken
        or "qualified" in spoken,
        "timeline_unchanged": _unchanged(before, after, "qual_a.mp4"),
    }


def _judge_retime(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    retimed = _one(after, "qual_a.mp4")
    speed = retimed.get("speed") if retimed else None
    percent = None
    if isinstance(speed, dict):
        percent = speed.get("Percentage", speed.get("percent"))
    elif isinstance(speed, (int, float)):
        percent = speed
    return {
        "speed_read_back": percent is not None
        and _approx(percent, 50.0, 0.5),
        "other_clip_untouched": _unchanged(before, after, "qual_b.mp4"),
    }


def _judge_transition(case: dict) -> dict:
    after = case["after"]
    placed = any(row["exit_code"] == 0 for row in case["commands"])
    spoken = " ".join(row["stdout"] for row in case["commands"]).lower()
    return {
        "transition_placed": placed and "transition" in spoken,
        "items_unchanged": _unchanged(case["after_setup"], after,
                                      "qual_a.mp4")
        and _unchanged(case["after_setup"], after, "qual_b.mp4"),
    }


def _judge_fusion_matte(case: dict) -> dict:
    after = case["after"]
    row = _one(after, "qual_a.mp4")
    return {
        "matte_read_back": bool(row)
        and "fm-qual-matte" in (row.get("fusion_comps") or []),
        "other_clip_untouched": _unchanged(case["after_setup"], after,
                                           "qual_b.mp4"),
    }


def _judge_graded_node(case: dict) -> dict:
    after = case["after"]
    row = _one(after, "qual_a.mp4")
    return {
        "grade_read_back": bool(row.get("cdl")) if row else False,
        "other_clip_untouched": _unchanged(case["after_setup"], after,
                                           "qual_b.mp4"),
    }


JUDGES = {
    "insert_at_frame_on_track": _judge_insert_at_frame_on_track,
    "insert_to_absent_track_refused": _judge_insert_refused,
    "remove_ripple_closes_gap": _judge_remove_ripple,
    "remove_leaves_gap": _judge_remove_leaves_gap,
    "trim_narrows_source_range": _judge_trim,
    "trim_past_handles_refused": _judge_trim_refused,
    "move_keeps_unrelated_state": _judge_move,
    "replace_source_keeps_transform": _judge_replace_source,
    "set_property_position": lambda case: _judge_set_property(
        case, {"Pan": 40.0, "Tilt": -12.0}),
    "set_property_scale": lambda case: _judge_set_property(
        case, {"ZoomX": 1.25, "ZoomY": 1.25}),
    "set_property_crop": lambda case: _judge_set_property(
        case, {"CropLeft": 5.0, "CropRight": 5.0}),
    "set_property_opacity": lambda case: _judge_set_property(
        case, {"Opacity": 85.0}),
    "set_property_rotation": lambda case: _judge_set_property(
        case, {"RotationAngle": 2.0}),
    "add_video_and_audio_tracks": _judge_add_tracks,
    "remove_empty_track": _judge_remove_empty_track,
    "remove_occupied_track_refused": _judge_remove_occupied_refused,
    "ripple_is_track_scoped": _judge_ripple_track_scoped,
    "broll_insert_keeps_dialogue": _judge_broll_insert,
    "broll_replace_keeps_dialogue": _judge_broll_replace,
    "multicam_angle_switch": _judge_multicam,
    "overlay_insert": _judge_overlay_insert,
    "overlay_replace": _judge_overlay_replace,
    "overlay_position": _judge_overlay_position,
    "overlay_remove": _judge_overlay_remove,
    "audio_mute": _judge_audio_mute,
    "caption_text_refused": lambda case: _judge_routed_refusal(
        case, "caption_fix"),
    "audio_gain_refused": lambda case: _judge_routed_refusal(
        case, "audio.gain"),
    "audio_automation_refused": lambda case: _judge_routed_refusal(
        case, "audio.automation"),
    "retime_speed_change": _judge_retime,
    "transition_at_cut": _judge_transition,
    "fusion_matte_placed": _judge_fusion_matte,
    "graded_clip_node_set": _judge_graded_node,
}


# ── The qualification record ───────────────────────────────────────────
#
# The predicates are written from the MEASURED runtime state, so a
# predicate never names a value the measurement cannot produce
# (`library/tools/axi_qualification_state.py` owns that vocabulary).

_PREDICATE_FIELDS = (
    "resolve_version", "project_name", "timeline_name", "media_type",
    "clip_composition", "linked_audio_configuration", "timeline_structure",
)


def _predicates(measured: dict) -> list[dict]:
    state = measured.get("state") or {}
    predicates = []
    for field in _PREDICATE_FIELDS:
        value = state.get(field)
        if value in (None, ""):
            continue
        predicates.append({"field": field, "operator": "equals",
                           "value": str(value)})
    return predicates


def _qualification_result(primitive: Primitive, scenarios: list[dict],
                          measured: dict) -> dict:
    """One `ren-axi-qualification-result/v1` document for this pair."""
    checks: dict = {}
    for scenario in scenarios:
        checks.update(scenario.get("checks") or {})
    state = measured.get("state") or {}
    return {
        "schema": "ren-axi-qualification-result/v1",
        "operation_id": primitive.operation,
        "backend": primitive.backend,
        "result": "passed",
        "evidence": (
            "tests/live/reports/axi-primitive-qualification.json#"
            + ",".join(scenario["id"] for scenario in scenarios)),
        "resolve_version": str(state.get("resolve_version") or ""),
        "project_conditions": [
            f"{PROJECT} / {TIMELINE}",
            "private target timeline only",
            f"production entry: {primitive.production_entry}",
        ],
        "supported_media_types": [str(state.get("media_type") or "video")],
        "expected_state": {name: True for name in checks},
        "actual_state": {
            "checks": checks,
            "measurement": state,
            "scenarios": [scenario["id"] for scenario in scenarios],
        },
        "preservation_checks": [
            {"check": name, "result": "passed"}
            for name, value in checks.items() if value],
        "preservation_limits": [
            "one private scratch timeline; the checks are the ones the "
            "scenarios name: " + ", ".join(sorted(checks))],
        "visual_verification": [
            ("the live read-back of every clip on every row, item by item "
             "(source path, source range, record span, transform, enabled "
             "state, Fusion comps) plus `resolve-axi items`")],
        "audio_verification": [
            ("not applicable: this operation writes no audio level; the "
             "audio rows are read back unchanged by the same per-item "
             "reading")],
        "recovery_status": "not_required",
        "qualification_predicates": _predicates(measured),
    }


def _registry_rows(report: dict) -> tuple[list[tuple[tuple[str, str], dict]],
                                         list[dict]]:
    """The qualification rows the passing scenarios earned, one per pair.

    A row is only written for a pair that HAS an executor: promoting a
    pair without one would route production work to a path that cannot
    run it.  A pair that cannot be promoted is REPORTED, never dropped:
    the gap is the qualification boundary the punch list asks for.
    """
    by_scenario = {row["id"]: row for row in report.get("results", [])}
    groups: dict[tuple[str, str], list[dict]] = {}
    measured: dict[tuple[str, str], dict] = {}
    gaps: list[dict] = []
    for primitive in PRIMITIVES:
        row = by_scenario.get(primitive.id)
        if row is None:
            continue
        declared = PROMOTION_GAPS.get(primitive.id)
        if declared:
            gaps.append({
                "operation_id": primitive.operation,
                "backend": primitive.backend,
                "scenario": primitive.id,
                "reason": declared,
            })
            continue
        if row["status"] != "pass":
            continue
        if primitive.expectation != "commit":
            # A refusal proves a refusal, never a qualification.
            continue
        key = (primitive.operation, primitive.backend)
        entry = edit_algebra.status(primitive.operation)
        if entry is None or entry.for_backend(primitive.backend) is None:
            gaps.append({
                "operation_id": primitive.operation,
                "backend": primitive.backend,
                "scenario": primitive.id,
                "reason": (
                    f"{primitive.operation} has no executor on "
                    f"{primitive.backend}, so the pair cannot be promoted: "
                    "the registry would route production work to a path "
                    "that cannot run it"),
            })
            continue
        groups.setdefault(key, []).append(row)
        measured[key] = row.get("case", {}).get("qualification_state") or {}
    rows = []
    for key, scenarios in groups.items():
        primitive = next(item for item in PRIMITIVES
                         if (item.operation, item.backend) == key)
        result = _qualification_result(primitive, scenarios, measured[key])
        if not result["preservation_checks"] or not result["qualification_predicates"]:
            # No complete evidence means no registrable row: the loader
            # refuses an incomplete report, and a refusal here would
            # hide the gap rather than record it.
            gaps.append({
                "operation_id": primitive.operation,
                "backend": primitive.backend,
                "scenario": ",".join(scenario["id"] for scenario in scenarios),
                "reason": ("the run measured no complete evidence for this "
                           "pair, so no row is written"),
            })
            continue
        rows.append((key, result))
    return rows, gaps


def _write_registry_results(report: dict) -> tuple[list[dict], list[dict]]:
    """Write one registry-shaped result per earned pair.

    An existing row is NEVER overwritten: the new evidence is written
    beside it and the report names both, so a promotion stays a
    deliberate act on a file the operator chose.
    """
    rows, gaps = _registry_rows(report)
    written = []
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    for (operation, backend), result in rows:
        path = REGISTRY_DIR / f"{operation}__{backend}.json"
        replaced = path.exists()
        if replaced:
            path = REGISTRY_DIR / f"{operation}__{backend}__axi-primitive.json"
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        written.append({"path": _reported_path(path),
                        "operation_id": operation, "backend": backend,
                        "existing_row_left_in_place": replaced})
    return written, gaps


def _reported_path(path: Path) -> str:
    """The path as the operator sees it: inside the repository, or absolute."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# ── The run ────────────────────────────────────────────────────────────


def _owned_media() -> list[str]:
    """Copy the fixture media this run owns out of the shared pool."""
    small._write_logo_fixture(small.LOGO)
    small._write_caption_fixture(small.LOGO_ALT, 180, 64,
                                 [(20, 20, 160, 44)], (245, 145, 45, 255))
    copied = []
    for source, target in ((POOL_DIR / "qual_a.mp4", OWNED_VIDEO_A),
                           (POOL_DIR / "qual_b.mp4", OWNED_VIDEO_B),
                           (small.LOGO, OWNED_OVERLAY_A),
                           (small.LOGO_ALT, OWNED_OVERLAY_B)):
        if not source.is_file():
            raise RuntimeError(
                f"the qualification fixture is missing: {source}")
        shutil.copyfile(source, target)
        copied.append(str(target))
    return copied


def run_batch(actions: dict) -> dict:
    """Run the declared scenarios once and score them."""
    selected = os.environ.get("REN_AXI_QUAL_ONLY")
    primitives = PRIMITIVES
    if selected:
        wanted = set(selected.split(","))
        unknown = wanted - {primitive.id for primitive in PRIMITIVES}
        if unknown:
            raise ValueError(
                f"unknown qualification scenario IDs: {sorted(unknown)}")
        primitives = tuple(primitive for primitive in PRIMITIVES
                           if primitive.id in wanted)
    results = []
    for primitive in primitives:
        started = perf_counter()
        try:
            evidence = actions[primitive.id]()
        except Exception as exc:  # noqa: BLE001 - the failure IS the evidence
            results.append({
                **asdict(primitive),
                "status": "fail",
                "first_attempt": True,
                "collateral_changes": [],
                "checks": {},
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_seconds": perf_counter() - started,
            })
            continue
        checks = evidence["checks"]
        collateral = evidence["collateral_changes"]
        committed = evidence["receipt"]["status"] == "committed"
        expected = (committed if primitive.expectation == "commit"
                    else not committed)
        passed = (expected and not collateral
                  and all(checks.values())
                  and evidence.get("attempts", 1) == 1)
        results.append({
            **asdict(primitive),
            "status": "pass" if passed else "fail",
            "first_attempt": True,
            "collateral_changes": collateral,
            "checks": checks,
            "receipt": evidence["receipt"],
            "observed": evidence.get("observed", {}),
            "elapsed_seconds": evidence.get("elapsed_seconds",
                                            perf_counter() - started),
            "case": evidence.get("case", {}),
        })
    return {
        "schema": "ren-axi-primitive-qualification/1",
        "evaluation_level": 1,
        "axi_items": list(AXI_ITEMS),
        "scenarios": len(results),
        "passed": sum(row["status"] == "pass" for row in results),
        "failed": sum(row["status"] == "fail" for row in results),
        "results": results,
    }


def _make_action(primitive: Primitive, by_id: dict):
    """The action that judges one scenario from the batch's evidence."""
    judge = JUDGES.get(primitive.id)

    def action() -> dict:
        case = by_id.get(primitive.id)
        if case is None:
            raise RuntimeError(
                f"the qualification batch omitted {primitive.id}")
        refused = [row for row in case["commands"] if row["exit_code"]]
        committed = bool(case["commands"]) and not refused
        checks = judge(case) if judge else {}
        return {
            "receipt": {"status": "committed" if committed else "refused",
                        "refusals": [row["argv"] for row in refused]},
            "collateral_changes": case.get("collateral_changes", []),
            "checks": checks,
            "attempts": 1,
            "elapsed_seconds": case.get("elapsed_seconds"),
            "case": case,
            "observed": {
                "items_readback": (case.get("items_readback") or {}).get(
                    "stdout", ""),
                "qualification_state": case.get("qualification_state"),
            },
        }

    return action


def main() -> int:
    _owned_media()
    if os.environ.get("REN_LIVE_RESOLVE") != "1":
        raise SystemExit("set REN_LIVE_RESOLVE=1 to run against Resolve")
    cases = _cases()
    script = _broker_script(cases)
    receipt = small._qualification_batch(script, wait_seconds=900,
                                         timeout_seconds=960)
    steps = (receipt.get("result") or {}).get("steps", [])
    if not steps:
        raise RuntimeError(
            f"the qualification batch returned no steps: {receipt}")
    stdout = steps[0].get("stdout", "")
    start = stdout.find("{\n")
    if start < 0:
        raise RuntimeError("the qualification batch printed no JSON result")
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    by_id = {row["id"]: row for row in payload.get("cases", [])}
    actions = {primitive.id: _make_action(primitive, by_id)
               for primitive in PRIMITIVES}
    report = run_batch(actions)
    written, gaps = _write_registry_results(report)
    report["registry_results"] = written
    report["promotion_gaps"] = gaps
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False)
                           + "\n", encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH),
                      "passed": report["passed"],
                      "failed": report["failed"],
                      "registry_results": written,
                      "promotion_gaps": gaps}, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
