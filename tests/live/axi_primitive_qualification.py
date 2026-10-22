"""AXI-010..023: one live qualification scenario per AXI primitive.

The captain's punch list asks for every editing primitive to carry an
explicit, generalizable qualification boundary.  This module is the
SCENARIO side of that: the table below names, for each item of the
list, the operation, the backend, the production entry that performs it,
and what the scenario must observe.  The live lane
(``vep-axc-live2``) runs this module against a real Resolve and imports
the evidence it produces through the existing registry tool
(``python3 -m library.tools.axi_qualification --import-result ...``):
this module writes the ``ren-axi-qualification-result/v2`` files that
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
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from library.tools import axi_qualification, edit_algebra
from tests.live import small_edit_qualification as small

ROOT = Path(__file__).resolve().parents[2]
PROJECT = small.PROJECT
TIMELINE = os.environ.get("REN_AXI_QUAL_TIMELINE", small.TIMELINE)
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
#: A THIRD owned video, so a fixture can put a DIFFERENT clip on every
#: row.  Two clips of the same media share a NAME, and a scenario that
#: addresses one of them by name cannot say which ("the fixture does
#: not hold exactly one item named X (2)"), so a realistic fixture needs
#: one distinct item per row.
OWNED_VIDEO_C = Path("/tmp/fm-qual-owned-c.mp4")
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
               "exact_source_range", "existing_items_untouched")),
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
    Primitive("delete_linked_effects", "AXI-011",
              "Delete a linked A/V clip with existing transforms and retiming",
              "clip.delete", "edit_patch",
              "edit_patch.apply_live_patch",
              "commit",
              ("video_target_absent", "linked_audio_preserved",
               "other_items_untouched")),
    Primitive("delete_retimed_only", "AXI-011",
              "Delete a linked A/V clip with retiming and no other effects",
              "clip.delete", "edit_patch",
              "edit_patch.apply_live_patch",
              "commit",
              ("video_target_absent", "linked_audio_preserved",
               "other_items_untouched", "retiming_only_state")),
    Primitive("delete_transform_only", "AXI-011",
              "Delete a clip carrying only a manual transform",
              "clip.delete", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("target_absent", "transform_only_state",
               "other_items_untouched")),
    Primitive("delete_linked_audio_twin", "AXI-011",
              "Delete the audio half of a linked A/V clip, preserving picture",
              "clip.delete", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("audio_target_absent", "linked_video_preserved",
               "other_items_untouched")),

    # ── AXI-011 on a REALISTIC timeline (the captain's AXI-005) ──
    #
    # The scenarios above earned the delete on a fixture with one or two
    # clips on one row: light density, one populated row, no linked
    # audio, no effects.  A real edit is none of those things, and the
    # production router refused every one of them with the same sentence
    # - "runtime state does not satisfy recorded qualification
    # predicates: timeline_density is 'moderate', not 'light'" - which is
    # a statement about the FIXTURE, not about the operation.
    #
    # These scenarios earn the delete and the ripple in the states a
    # real timeline holds: a linked A/V target, a retimed target, a
    # Fusion-bearing target, a still held over a caption card's span,
    # and a multi-track timeline dense enough to be 'moderate'.
    Primitive("delete_linked_av_moderate", "AXI-011",
              "Delete a linked A/V clip on a dense, multi-track timeline",
              "clip.delete", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("video_target_absent", "linked_audio_preserved",
               "gap_left", "adjacent_source_ranges_unchanged",
               "other_rows_untouched")),
    Primitive("delete_retimed_moderate", "AXI-011",
              "Delete a retimed clip on a dense, multi-track timeline",
              "clip.delete", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("target_absent", "gap_left", "other_rows_untouched")),
    Primitive("delete_still_card", "AXI-011",
              "Delete a caption-card still held over its own span",
              "clip.delete", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("target_absent", "other_rows_untouched")),
    Primitive("delete_fusion_bearing", "AXI-011",
              "Delete a clip that carries a Fusion composition",
              "clip.delete", "edit_patch",
              "edit_patch.apply_live_patch", "commit",
              ("target_absent", "fusion_comp_gone_with_target",
               "other_rows_untouched")),
    Primitive("ripple_remove_multi_track", "AXI-017",
              "Ripple one track of a multi-track timeline and measure "
              "exactly which tracks and clips moved",
              "clip.remove", "resolve_axi",
              "resolve-axi edit track-ripple-remove", "commit",
              ("target_absent", "same_track_closed",
               "link_group_shifted", "other_rows_untouched",
               "ripple_scope_measured")),
    Primitive("ripple_remove_light", "AXI-017",
              "Ripple one track of a light timeline",
              "clip.remove", "resolve_axi",
              "resolve-axi edit track-ripple-remove", "commit",
              ("target_absent", "same_track_closed",
               "other_rows_untouched", "ripple_scope_measured")),

    # ── AXI-012: trimming, in/out preservation and source-range validation
    Primitive("trim_narrows_source_range", "AXI-012",
              "Narrow a clip's source range and move its record start",
              "clip.trim", "resolve_axi", "resolve-axi edit trim",
              "commit",
              ("source_range_narrowed", "duration_matches_source_range",
               "record_start_shifted", "other_clip_untouched")),
    Primitive("trim_linked_av_stays_synchronized", "AXI-012",
              "Trim a linked A/V picture clip and keep its audio synchronized",
              "clip.trim", "resolve_axi", "resolve-axi edit trim",
              "commit",
              ("video_source_range_narrowed", "audio_source_range_narrowed",
               "audio_picture_record_spans_match", "other_items_untouched")),
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
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95,
                             media="both"),
                      _place("qual_b.mp4", 1, 96, source_out=71,
                             media="both"),
                      _place("qual_c.mp4", 2, 144, source_out=47)],
            "commands": [_patch(
                [{"op": "clip.insert", "unique_id": "axi-prim-insert-1",
                  "track_type": "video", "track_index": 2,
                  "record_in": 48, "record_out": 96,
                  "media_pool_item": "$pool:" + OWNED_VIDEO_A.name,
                  "source_in": 0, "source_out": 48}],
                ["timeline_structure"], "insert-on-track-2",
                capability="reel.build")],
            "targets": [],
            "qualification_params": {"track_type": "video"},
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
        "delete_linked_effects": {
            "operation": "clip.delete",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95,
                             media="both"),
                      _place("qual_b.mp4", 1, 96, source_out=71,
                             media="both"),
                      _place("qual_c.mp4", 2, 0, source_out=47),
                      _patch(
                          [{"op": "clip.set_property",
                            "unique_id": "$qual_a.mp4@video", "key": "Pan",
                            "value": 24.0}],
                          ["picture_transform"], "delete-target-transform"),
                      _patch(
                          [{"op": "retime.segments",
                            "unique_id": "$qual_a.mp4@video",
                            "segments": [{"record_frame": 0,
                                          "percent": 90.0}]}],
                          ["timeline_structure"], "delete-target-retime")],
            "commands": [_patch(
                [{"op": "clip.delete",
                  "unique_id": "$qual_a.mp4@video"}],
                ["timeline_structure"], "delete-linked-effects")],
            "targets": [{"name": "qual_a.mp4", "kind": "video"}],
        },
        "delete_retimed_only": {
            "operation": "clip.delete",
            "setup": [_place("qual_a.mp4", 1, 0, source_out=95,
                             media="both"),
                      _place("qual_b.mp4", 1, 96, source_out=71,
                             media="both"),
                      _place("qual_c.mp4", 2, 0, source_out=47),
                      _patch(
                          [{"op": "retime.segments",
                            "unique_id": "$qual_a.mp4@video",
                            "segments": [{"record_frame": 0,
                                          "percent": 90.0}]}],
                          ["timeline_structure"],
                          "delete-retimed-only-target")],
            "commands": [_patch(
                [{"op": "clip.delete",
                  "unique_id": "$qual_a.mp4@video"}],
                ["timeline_structure"], "delete-retimed-only")],
            "targets": [{"name": "qual_a.mp4", "kind": "video"}],
        },
        "delete_transform_only": {
            "operation": "clip.delete",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=95),
                      _patch(
                          [{"op": "clip.set_property",
                            "unique_id": "$" + OWNED_VIDEO_A.name,
                            "key": "Pan", "value": 40.0}],
                          ["picture_transform"],
                          "delete-transform-only-target")],
            "commands": [_patch(
                [{"op": "clip.delete",
                  "unique_id": "$" + OWNED_VIDEO_A.name}],
                ["timeline_structure"], "delete-transform-only")],
            "targets": [{"name": OWNED_VIDEO_A.name, "kind": "video"}],
        },
        "delete_linked_audio_twin": {
            "operation": "clip.delete",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=71,
                             media="both"),
                      _place(OWNED_VIDEO_B.name, 1, 96, source_out=47),
                      _place(OWNED_VIDEO_C.name, 2, 0, source_out=47),
                      _place(OWNED_OVERLAY_A.name, 3, 120,
                             source_out=47)],
            "commands": [_patch(
                [{"op": "clip.delete",
                  "unique_id": "$" + OWNED_VIDEO_A.name + "@audio"}],
                ["timeline_structure"], "delete-linked-audio-twin")],
            "targets": [{"name": OWNED_VIDEO_A.name, "kind": "audio"}],
        },
        # ── AXI-011 on a REALISTIC timeline (the captain's AXI-005) ──
        #
        # The fixture is the AXI-11 lane's shape, so the measurement
        # speaks about the same states that lane's own fixture holds:
        # a linked A/V A-roll, a cover on a second row, a caption card
        # on a third, and enough items to be 'moderate' rather than
        # 'light'.  Each row carries its OWN clip, because two clips of
        # the same media share a NAME and a scenario that addresses one
        # of them by name cannot say which.
        "delete_linked_av_moderate": {
            "operation": "clip.delete",
            "setup": [_place(OWNED_VIDEO_B.name, 1, 0, source_out=71,
                             media="both"),
                      _place(OWNED_VIDEO_A.name, 1, 96, source_out=95),
                      _place(OWNED_VIDEO_C.name, 2, 0, source_out=47),
                      _place(OWNED_OVERLAY_A.name, 3, 120,
                             source_out=47)],
            "commands": [_patch(
                [{"op": "clip.delete",
                  "unique_id": "$" + OWNED_VIDEO_B.name}],
                ["timeline_structure"], "delete-linked-av")],
            "targets": [{"name": OWNED_VIDEO_B.name, "kind": "video"}],
        },
        "delete_retimed_moderate": {
            "operation": "clip.delete",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=95),
                      # A second clip, so the timeline is a MULTI-ITEM one
                      # before the retime: `retime.segments` is qualified
                      # in that structure only.
                      _place(OWNED_VIDEO_C.name, 2, 0, source_out=47),
                      # The retime lands while the timeline is LIGHT:
                      # `retime.segments` is qualified in the light state
                      # only, so a retime applied after the fixture grew
                      # would be refused before the delete was measured.
                      _patch(
                          [{"op": "retime.segments",
                            "unique_id": "$" + OWNED_VIDEO_A.name,
                            "segments": [{"record_frame": 0,
                                          "percent": 90.0}]}],
                          ["timeline_structure"], "delete-target-retime"),
                      _place(OWNED_VIDEO_B.name, 1, 96, source_out=71,
                             media="both"),
                      _place(OWNED_OVERLAY_A.name, 3, 120,
                             source_out=47)],
            "commands": [_patch(
                [{"op": "clip.delete",
                  "unique_id": "$" + OWNED_VIDEO_A.name}],
                ["timeline_structure"], "delete-retimed")],
            "targets": [{"name": OWNED_VIDEO_A.name, "kind": "video"}],
        },
        "delete_still_card": {
            "operation": "clip.delete",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=47),
                      # A caption card is a STILL held over its own
                      # span: the media has one frame and the card
                      # covers forty-eight, so the item carries the
                      # offset a still always carries.
                      _place(OWNED_OVERLAY_A.name, 3, 96, source_out=47)],
            "commands": [_patch(
                [{"op": "clip.delete",
                  "unique_id": "$" + OWNED_OVERLAY_A.name}],
                ["timeline_structure"], "delete-still-card")],
            "targets": [{"name": OWNED_OVERLAY_A.name, "kind": "video"}],
        },
        "delete_fusion_bearing": {
            "operation": "clip.delete",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=95),
                      _place(OWNED_VIDEO_C.name, 2, 0, source_out=47),
                      # The retime lands FIRST, while the target still
                      # carries nothing: `retime.segments` is qualified
                      # in the untouched state, and `mask.create` is
                      # qualified in the retimed one - so the Fusion
                      # composition lands on a clip the delete then has
                      # to carry away.
                      _patch(
                          [{"op": "retime.segments",
                            "unique_id": "$" + OWNED_VIDEO_A.name,
                            "segments": [{"record_frame": 0,
                                          "percent": 90.0}]}],
                          ["timeline_structure"], "delete-target-retime"),
                      _patch(
                          [{"op": "mask.create",
                            "unique_id": "$" + OWNED_VIDEO_A.name,
                            "matte": "fm-qual-matte"}],
                          ["fusion"], "delete-target-matte")],
            "commands": [_patch(
                [{"op": "clip.delete",
                  "unique_id": "$" + OWNED_VIDEO_A.name}],
                ["timeline_structure"], "delete-fusion-bearing")],
            "targets": [{"name": OWNED_VIDEO_A.name, "kind": "video"}],
        },
        "ripple_remove_multi_track": {
            "operation": "clip.remove",
            "setup": [_place(OWNED_VIDEO_B.name, 1, 0, source_out=71,
                             media="both"),
                      _place(OWNED_VIDEO_A.name, 1, 96, source_out=95,
                             media="both"),
                      _place(OWNED_VIDEO_C.name, 2, 0, source_out=47),
                      _place(OWNED_OVERLAY_A.name, 3, 120,
                             source_out=47)],
            "commands": [_edit("track-ripple-remove", "--track", "video1",
                               "--index", "0", "--project-folder",
                               "@FM_QUAL_PROJECT_DIR@", "--apply")],
            "targets": [{"name": OWNED_VIDEO_B.name, "kind": "video"}],
        },
        "ripple_remove_light": {
            "operation": "clip.remove",
            "setup": [_place(OWNED_VIDEO_B.name, 1, 0, source_out=71),
                      _place(OWNED_VIDEO_A.name, 1, 96, source_out=47),
                      _place(OWNED_VIDEO_C.name, 2, 0, source_out=47)],
            "commands": [_edit("track-ripple-remove", "--track", "video1",
                               "--index", "0", "--project-folder",
                               "@FM_QUAL_PROJECT_DIR@", "--apply")],
            "targets": [{"name": OWNED_VIDEO_B.name, "kind": "video"}],
        },
        # ── AXI-012 ──
        "trim_narrows_source_range": {
            "operation": "clip.trim",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=95),
                      _place(OWNED_VIDEO_B.name, 2, 144, source_out=47)],
            "commands": [_edit("trim", "--track", "video1", "--index", "0",
                               "--source-in", "10", "--source-out", "59",
                               "--project-folder", "@FM_QUAL_PROJECT_DIR@",
                               "--require-recovery", "--apply")],
            "targets": [OWNED_VIDEO_A.name],
        },
        "trim_linked_av_stays_synchronized": {
            "operation": "clip.trim",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=71,
                             media="both"),
                      _place(OWNED_VIDEO_B.name, 2, 96, source_out=47),
                      _place(OWNED_VIDEO_C.name, 3, 120, source_out=47)],
            "commands": [_edit("trim", "--track", "video1", "--index", "0",
                               "--source-in", "0", "--source-out", "47",
                               "--project-folder", "@FM_QUAL_PROJECT_DIR@",
                               "--require-recovery", "--apply")],
            "targets": [{"name": OWNED_VIDEO_A.name, "kind": "video"}],
        },
        "trim_past_handles_refused": {
            "operation": "clip.trim",
            "setup": [_place(OWNED_VIDEO_A.name, 1, 0, source_out=95),
                      _place(OWNED_VIDEO_B.name, 2, 144, source_out=47)],
            "commands": [_edit("trim", "--track", "video1", "--index", "0",
                               "--source-in", "10", "--source-out", "900",
                               "--project-folder", "@FM_QUAL_PROJECT_DIR@",
                               "--require-recovery", "--apply")],
            "targets": [OWNED_VIDEO_A.name],
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
                             media="both"),
                      _place("qual_b.mp4", 1, 96, source_out=71,
                             media="both"),
                      _place("qual_c.mp4", 2, 144, source_out=47)],
            "commands": [_place(OWNED_VIDEO_A.name, 2, 40, source_out=39)],
            "targets": [{"name": "qual_a.mp4", "kind": "video"}],
            "qualification_params": {"track_type": "video"},
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
OWNED_VIDEO_C = "__AXI_VIDEO_C__"
OWNED_OVERLAY_A = "__AXI_OVERLAY_A__"
OWNED_OVERLAY_B = "__AXI_OVERLAY_B__"

ALLOWED_NAMES = {"qual_a.mp4", "qual_b.mp4", "qual_c.mp4",
                 # The fixture's own overlays: the logo and the caption
                 # cards it places are the fixture's too.
                 "fm-qual-logo-180x64.png",
                 "fm-qual-logo-alt-180x64.png",
                 "fm-qual-tight-caption-480x120.png",
                 "fm-qual-full-frame-caption-1920x1080.png",
                 # A TIMELINE item is named by its FILE name, not by the
                 # path the fixture imported it from: comparing the item's
                 # name against the path made the cleanup refuse to
                 # remove the fixture's own media, and the leftover broke
                 # the next case's placement ("the item count did not
                 # move", measured 2026-10-20).
                 OWNED_VIDEO_A.rsplit("/", 1)[-1],
                 OWNED_VIDEO_B.rsplit("/", 1)[-1],
                 OWNED_VIDEO_C.rsplit("/", 1)[-1],
                 OWNED_OVERLAY_A.rsplit("/", 1)[-1],
                 OWNED_OVERLAY_B.rsplit("/", 1)[-1]}


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    # The run store's directory reaches a command that needs it: a
    # re-place carries the transform forward, and a transform write is
    # anchored to a project folder (the ripple's re-place refused
    # without one, measured 2026-10-21).
    argv = [str(part).replace("@FM_QUAL_PROJECT_DIR@", PROJECT_DIR)
            for part in argv]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = resolve_axi.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return {"argv": argv, "exit_code": int(code or 0),
            "stdout": out.getvalue(), "stderr": err.getvalue()}


#: The last cleanup the fixture ran, so a case that cannot find its
#: target can say what the cleanup did before it.
LAST_CLEANUP = None


def _frame(value):
    """A frame number, or None where Resolve reports none.

    An unreadable frame is not a confirmed zero: the read says it could
    not be measured and the judge that needs it says so.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
                    # A frame Resolve does not report is None, not zero:
                    # an unreadable source range is not a confirmed one.
                    "source_in": _frame(item.GetSourceStartFrame()),
                    "source_out": _frame(item.GetSourceEndFrame()),
                    "record_in": _frame(item.GetStart()),
                    "record_out": _frame(item.GetEnd()),
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


def _frame(value):
    """A frame number, or None where Resolve reports none."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def by_name(state, name):
    """The row with this name, the VIDEO row first.

    A linked A/V pair is TWO rows with the SAME name, so a name alone
    resolves to two and the scenario that addresses the video row cannot
    (measured 2026-10-20: "the fixture does not hold exactly one item
    named qual_a.mp4 (2)"). The video row is what an operation on the
    picture addresses; an audio-only target still resolves by name.
    """
    rows = [row for row in state.get("items", [])
            if row.get("name") == name]
    video = [row for row in rows if row.get("kind") == "video"]
    return video or rows


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
            wanted = uid[1:]
            kind = ""
            if "@" in wanted:
                wanted, kind = wanted.rsplit("@", 1)
            matches = ([row for row in state.get("items", [])
                        if row.get("name") == wanted
                        and row.get("kind") == kind]
                       if kind else by_name(state, wanted))
            if len(matches) != 1:
                raise RuntimeError(
                    "the fixture does not hold exactly one item named "
                    + uid[1:] + " (" + str(len(matches)) + ")"
                    + ("; the setup was refused: "
                       + repr(state.get("setup_refusals"))[:600]
                       if state.get("setup_refusals") else "")
                    + ("; the carried cleanup: "
                       + repr(LAST_CLEANUP)[:400]
                       if LAST_CLEANUP else "")
                    + "; the timeline holds: "
                    + repr([(row.get("kind"), row.get("name"),
                             row.get("record"))
                            for row in state.get("items", [])])[:400])
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


def qualification_state(operation, targets, params=None):
    """The MEASURED runtime state, in the recorded predicate vocabulary."""
    from library.tools import axi_qualification_state

    params = params or {}
    operations = ([{"op": operation, **params, "unique_id": uid}
                   for uid in targets] or
                  [{"op": operation, **params}])
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
    paths = [OWNED_VIDEO_A, OWNED_VIDEO_B, OWNED_VIDEO_C,
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
    if len(names) != len(paths):
        # The import is the fixture's own media, and a case that cannot
        # place it cannot judge anything: the refusal names what landed
        # and what did not.
        raise RuntimeError(
            "the owned media did not import: " + repr(names) + " of "
            + repr([str(path) for path in paths]))
    _pooled = []
    for _folder in _walk_pool(pool.GetRootFolder()):
        for _item in _folder.GetClipList() or []:
            try:
                _pooled.append(str(_item.GetName()))
            except Exception:
                continue
    if not [name for name in names if name in _pooled]:
        raise RuntimeError(
            "the owned media imported but the pool does not hold it: "
            + repr(names) + " of " + repr(sorted(_pooled)[:40]))
    # The AXI's own pool lookup is what a placement asks for, so the
    # fixture checks THAT and not only its own walk: a fixture whose
    # media the placement cannot see is a fixture that cannot judge.
    from library.tools import resolve_axi_core_commands as _axi
    try:
        _axi._pool_find_clip(manager.GetCurrentProject(),
                             str(OWNED_VIDEO_A).rsplit("/", 1)[-1], "")
    except Exception as exc:
        raise RuntimeError(
            "the AXI pool lookup cannot see the imported media: "
            + str(exc))
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
                                               OWNED_VIDEO_C, OWNED_OVERLAY_A,
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
    """Remove exactly what the fixture placed, or name what is left.

    An item the fixture does not own is NEVER removed - but it does not
    stop the fixture's own items from being removed either: refusing
    everything left every case's leftovers in place, and the next case
    then placed onto a timeline that was not empty ("the fixture does not
    hold exactly one item named qual_a.mp4 (2)", measured 2026-10-20).
    """
    global LAST_CLEANUP
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
                        # Not ours: it is left exactly where it is, and
                        # the case that finds it is told.
                        collateral.append("refused fixture cleanup of "
                                          + name)
                        continue
                    doomed.append(item)
        if not doomed:
            break
        # One at a time: deleting a linked pair's picture invalidates
        # its audio entry, and a batch delete then fails on the stale
        # entry and leaves the timeline dirty (measured 2026-10-20).
        for _item in doomed:
            if not timeline.DeleteClips([_item], False):
                collateral.append("the fixture items could not be deleted")
                return {"clean": False, "collateral_changes": collateral}
    remaining = []
    for kind in ("video", "audio", "subtitle"):
        count = int(timeline.GetTrackCount(kind) or 0)
        for index in range(1, count + 1):
            for item in timeline.GetItemListInTrack(kind, index) or ():
                if str(item.GetName()) in ALLOWED_NAMES:
                    remaining.append(kind + str(index))
    if remaining:
        collateral.append("the fixture left items on " + ",".join(remaining))
    LAST_CLEANUP = {"clean": not remaining, "collateral_changes": collateral}
    return LAST_CLEANUP


def select_timeline():
    project = manager.GetCurrentProject()
    matches = [project.GetTimelineByIndex(index)
               for index in range(1, int(project.GetTimelineCount() or 0) + 1)
               if project.GetTimelineByIndex(index)
               and project.GetTimelineByIndex(index).GetName() == TIMELINE]
    if len(matches) != 1:
        raise RuntimeError(
            "the private scratch timeline is not selectable: "
            + repr([str(t.GetName()) for t in matches]) + " of "
            + repr([str(project.GetTimelineByIndex(index).GetName())
                    for index in range(1, int(project.GetTimelineCount() or 0) + 1)]))
    # Resolve answers SetCurrentTimeline with False when the timeline is
    # ALREADY current, so a second selection in the same session would
    # read as a failure (measured 2026-10-20). Only a timeline that is
    # not current is set.
    _current = project.GetCurrentTimeline()
    if not (_current is not None
            and str(_current.GetUniqueId() or "")
            == str(matches[0].GetUniqueId() or "")):
        if not project.SetCurrentTimeline(matches[0]):
            raise RuntimeError(
                "the private scratch timeline is not selectable: Resolve "
                "refused to make it current")
    return project.GetCurrentTimeline().GetUniqueId()


if not Path(PROJECT_DIR).is_dir():
    raise RuntimeError("the batch project dir was not stamped by the runner")
select_timeline()
# The owned media is imported PER CASE: every case's cleanup removes it
# again (so one case's replacement never becomes the next case's
# fixture), and an import that only ran once left the second case
# placing a pool item that was already gone ("no clip named
# 'fm-qual-owned-a.mp4' under the pool root", measured 2026-10-20).
setup = {"imported": []}

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
    # Every case starts from a clean timeline: the fixture owns this
    # timeline, and a case that failed mid-placement left an item that
    # made the NEXT case's placement read as "the item count did not
    # move" (measured 2026-10-20).
    _carried = cleanup()
    setup["imported"] = import_owned_media()
    for entry in case.get("setup", []):
        # A setup entry is a VERB, or a PATCH: a fixture that has to
        # carry a retime or a Fusion composition onto its target needs
        # the write to land BEFORE the state is measured, or the
        # measurement reports a timeline the delete never met.
        if isinstance(entry, dict):
            receipt = apply_patch(entry, live_state())
        else:
            receipt = call(entry)
        setup_receipts.append(receipt)
        if receipt["exit_code"]:
            # The setup is the fixture's own placement: a refusal here
            # is why the case's target is missing, so it is printed
            # rather than only counted.
            print("AXI_SETUP_REFUSED" + repr({
                "case": name, "argv": entry,
                "stdout": (receipt.get("stdout") or "")[-600:],
                "stderr": (receipt.get("stderr") or "")[-600:]}),
                flush=True)
            break
    after_setup = live_state()
    after_setup["carried_cleanup"] = _carried
    after_setup["setup_refusals"] = [
        {"argv": receipt.get("argv"),
         "stdout": (receipt.get("stdout") or "")[-500:],
         "stderr": (receipt.get("stderr") or "")[-500:]}
        for receipt in setup_receipts if receipt.get("exit_code")]
    targets = []
    for target in case.get("targets", []):
        if isinstance(target, dict):
            matches = [row for row in after_setup.get("items", [])
                       if row.get("name") == target["name"]
                       and row.get("kind") == target["kind"]]
        else:
            matches = by_name(after_setup, target)
        if len(matches) == 1:
            targets.append(matches[0]["unique_id"])
    state = qualification_state(
        case.get("operation", "clip.delete"), targets,
        case.get("qualification_params"))
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
    # The placeholders sit INSIDE the assignment's own quotes, so
    # replacing the placeholder alone leaves the value quoted twice:
    # TIMELINE = "'fm-scratch qualify'" - a name no timeline carries, and
    # the scenario's own target never resolved (measured 2026-10-20 on the
    # first live run of these scenarios). The whole assignment is
    # replaced, so the value is the value.
    script = script.replace(
        'PROJECT = "__AXI_PROJECT__"', f"PROJECT = {PROJECT!r}")
    script = script.replace(
        'TIMELINE = "__AXI_TIMELINE__"', f"TIMELINE = {TIMELINE!r}")
    script = script.replace("__AXI_PROJECT_DIR__", "@FM_QUAL_PROJECT_DIR@")
    script = script.replace("__AXI_DISPOSABLE__", "@AXC_DISPOSABLE_PROJECT@")
    script = script.replace("__AXI_CASES__", repr(cases))
    script = script.replace("__AXI_OWNED__", repr(
        {OWNED_VIDEO_A.name, OWNED_VIDEO_B.name, OWNED_VIDEO_C.name,
         OWNED_OVERLAY_A.name, OWNED_OVERLAY_B.name}))
    script = script.replace("__AXI_TRACKS__", repr({"V9", "A9"}))
    script = script.replace("__AXI_VIDEO_A__", str(OWNED_VIDEO_A))
    script = script.replace("__AXI_VIDEO_B__", str(OWNED_VIDEO_B))
    script = script.replace("__AXI_VIDEO_C__", str(OWNED_VIDEO_C))
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


def _frame(value):
    """A frame number, or None where the read reported none."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    source_in = _frame(row["source_in"])
    source_out = _frame(row["source_out"])
    duration = _frame(row["record_out"]) - _frame(row["record_in"])
    if source_in is None or source_out is None or duration is None:
        return ()
    if duration == source_out - source_in + 1:
        return (source_in, source_out)
    if duration == source_out - source_in:
        return (source_in, source_out - 1)
    return (source_in, source_out)


def _record_span(row: dict | None) -> tuple:
    if not row:
        return ()
    return (_frame(row["record_in"]), _frame(row["record_out"]))


def _unchanged(before: dict, after: dict, name: str) -> bool:
    old = _items(before, name)
    new = _items(after, name)
    if not old or len(old) != len(new):
        return False

    def signature(row: dict) -> tuple:
        return (row.get("kind"), row.get("track"), _record_span(row),
                _source_bounds(row), tuple(row.get("linked_unique_ids") or ()))

    return sorted(map(signature, old), key=repr) == sorted(
        map(signature, new), key=repr)


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
    placed = _one(after, OWNED_VIDEO_A.name)
    source = _source_bounds(placed)
    before_items = case["after_setup"].get("items", [])
    after_items = after.get("items", [])
    existing_ids = {str(row.get("unique_id") or "")
                    for row in before_items}
    remaining = [row for row in after_items
                 if str(row.get("unique_id") or "") in existing_ids]
    def signature(row: dict) -> tuple:
        return (row.get("unique_id"), row.get("kind"), row.get("track"),
                _record_span(row), _source_bounds(row))
    return {
        "placed_on_named_track": bool(placed) and placed["track"] == 2
        and placed["kind"] == "video",
        "exact_record_start": bool(placed)
        and int(placed["record_in"]) == 48,
        "exact_source_range": source == (0, 47),
        "existing_items_untouched": len(remaining) == len(before_items)
        and sorted(map(signature, remaining), key=repr)
        == sorted(map(signature, before_items), key=repr),
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


def _judge_delete_linked_effects(case: dict) -> dict:
    before = case["after_setup"].get("items", [])
    after = case["after"].get("items", [])
    video_before = [row for row in before if row.get("name") == "qual_a.mp4"
                    and row.get("kind") == "video"]
    audio_before = [row for row in before if row.get("name") == "qual_a.mp4"
                    and row.get("kind") == "audio"]
    audio_after = [row for row in after if row.get("name") == "qual_a.mp4"
                   and row.get("kind") == "audio"]
    unrelated_before = [row for row in before
                        if row.get("name") != "qual_a.mp4"]
    unrelated_after = [row for row in after
                       if row.get("name") != "qual_a.mp4"]
    def signature(row: dict) -> tuple:
        return (row.get("unique_id"), row.get("kind"), row.get("track"),
                _record_span(row), _source_bounds(row))
    return {
        "video_target_absent": len(video_before) == 1 and not [
            row for row in after if row.get("unique_id")
            == video_before[0].get("unique_id")],
        "linked_audio_preserved": len(audio_before) == len(audio_after) == 1
        and signature(audio_before[0]) == signature(audio_after[0]),
        "other_items_untouched": sorted(map(signature, unrelated_before),
                                         key=repr)
        == sorted(map(signature, unrelated_after), key=repr),
    }


def _judge_delete_retimed_only(case: dict) -> dict:
    checks = _judge_delete_linked_effects(case)
    measured = (case.get("qualification_state") or {}).get("state") or {}
    checks["retiming_only_state"] = (
        measured.get("existing_effects") == "retiming")
    return checks


def _judge_delete_transform_only(case: dict) -> dict:
    """Prove local delete on a transform-only target, with read-back."""
    before = case["after_setup"]
    after = case["after"]
    target = _rows_named(before, OWNED_VIDEO_A.name, "video")
    state = (case.get("qualification_state") or {}).get("state") or {}
    return {
        "target_absent": len(target) == 1
        and not _rows_named(after, OWNED_VIDEO_A.name, "video"),
        "transform_only_state": state.get("existing_effects") == "transforms",
        "other_items_untouched": _other_rows_unchanged(
            before, after, skip=(("video", OWNED_VIDEO_A.name, 1),)),
    }


def _judge_delete_linked_audio_twin(case: dict) -> dict:
    """Prove an explicit audio-target delete leaves its linked picture."""
    before = case["after_setup"]
    after = case["after"]
    target_name = OWNED_VIDEO_A.name
    audio_before = _rows_named(before, target_name, "audio")
    video_before = _rows_named(before, target_name, "video")
    video_after = _rows_named(after, target_name, "video")
    unrelated_before = [row for row in before.get("items", [])
                        if row.get("name") != target_name]
    unrelated_after = [row for row in after.get("items", [])
                       if row.get("name") != target_name]
    return {
        "audio_target_absent": len(audio_before) == 1
        and not _rows_named(after, target_name, "audio"),
        "linked_video_preserved": len(video_before) == len(video_after) == 1
        and _signature(video_before[0]) == _signature(video_after[0]),
        "other_items_untouched": sorted(map(_signature, unrelated_before),
                                         key=repr)
        == sorted(map(_signature, unrelated_after), key=repr),
    }


# ── The delete and the ripple on a REALISTIC timeline ──────────────────
#
# The captain's AXI-005 asks the deletion and the ripple to be
# qualified where a real edit happens: a linked A/V target, a retimed
# target, a target that carries a Fusion composition, a caption card
# held over its own span, and a timeline with several populated rows.
#
# The checks below read the ADJACENT state as carefully as the target:
# an edit that moves the picture but loses the source range, the link,
# the effect or the sync is a different edit, and the evidence has to
# say which of them it saw.


def _rows_named(state: dict, name: str, kind: str = "") -> list[dict]:
    """Every row with this name, optionally of one kind."""
    return [row for row in state.get("items", [])
            if row.get("name") == name
            and (not kind or row.get("kind") == kind)]


def _signature(row: dict) -> tuple:
    """Everything about one clip that a neighbouring edit must not change.

    Resolve's `GetProperty()` returns a DICT on most items and a JSON
    STRING on an item that carries a Fusion composition, so the
    transform is normalised here: a string is read as no transform
    rather than crashing the reading.
    """
    transform = row.get("transform")
    if not isinstance(transform, dict):
        transform = {}
    return (row.get("unique_id"), row.get("kind"), row.get("track"),
            _record_span(row), _source_bounds(row),
            tuple(row.get("fusion_comps") or ()),
            tuple(sorted(transform.items())),
            row.get("speed"))


def _other_rows_unchanged(before: dict, after: dict, *,
                          skip: tuple = ()) -> bool:
    """Whether every clip outside `skip` reads exactly as it did."""
    def index(state: dict) -> dict:
        found = {}
        for row in state.get("items", []):
            key = (row.get("kind"), row.get("name"), row.get("track"))
            found.setdefault(key, []).append(row)
        return found

    before_rows, after_rows = index(before), index(after)
    for key in set(before_rows) | set(after_rows):
        if key in skip:
            continue
        old = before_rows.get(key, [])
        new = after_rows.get(key, [])
        if len(old) != len(new):
            return False
        if sorted(map(_signature, old), key=repr) != sorted(
                map(_signature, new), key=repr):
            return False
    return True


def _judge_delete_linked_av_moderate(case: dict) -> dict:
    """A linked A/V target, deleted, on a dense multi-track timeline."""
    before = case["after_setup"]
    after = case["after"]
    target = _rows_named(before, OWNED_VIDEO_B.name, "video")
    twin = _rows_named(before, OWNED_VIDEO_B.name, "audio")
    successor = _rows_named(before, OWNED_VIDEO_A.name, "video")
    return {
        "video_target_absent": len(target) == 1
        and not _rows_named(after, OWNED_VIDEO_B.name, "video"),
        # The twin is the SYNC: a delete that took the picture and left
        # the audio twin behind would leave the row sounding what the
        # picture no longer shows.
        "linked_audio_preserved": len(twin) == 1
        and len(_rows_named(after, OWNED_VIDEO_B.name, "audio")) == 1
        and _signature(twin[0]) == _signature(
            _rows_named(after, OWNED_VIDEO_B.name, "audio")[0]),
        # A delete is not a ripple: the gap stays where the clip was.
        "gap_left": len(successor) == 1
        and len(_rows_named(after, OWNED_VIDEO_A.name, "video")) == 1
        and _record_span(_rows_named(after, OWNED_VIDEO_A.name, "video")[0])
        == _record_span(successor[0]),
        "adjacent_source_ranges_unchanged": len(successor) == 1
        and _source_bounds(_rows_named(after, OWNED_VIDEO_A.name, "video")[0])
        == _source_bounds(successor[0]),
        "other_rows_unchanged": _other_rows_unchanged(
            before, after,
            skip=(("video", OWNED_VIDEO_B.name, 1),)),
    }


def _judge_delete_target_only(case: dict, name: str,
                              successor_name: str = "") -> dict:
    """A delete of one named clip, with the rest of the timeline read back."""
    before = case["after_setup"]
    after = case["after"]
    target = _rows_named(before, name, "video")
    successor = _rows_named(before, successor_name, "video") if successor_name else []
    return {
        "target_absent": len(target) == 1
        and not _rows_named(after, name, "video"),
        "gap_left": (not successor
                     or len(_rows_named(after, successor_name, "video"))
                     == len(successor)),
        "other_rows_unchanged": _other_rows_unchanged(
            before, after, skip=(("video", name, 1),)),
    }


def _judge_delete_still_card(case: dict) -> dict:
    """A caption card: a still held over a span, deleted."""
    before = case["after_setup"]
    after = case["after"]
    target = _rows_named(before, OWNED_OVERLAY_A.name, "video")
    return {
        "target_absent": len(target) == 1
        and not _rows_named(after, OWNED_OVERLAY_A.name, "video"),
        "other_rows_unchanged": _other_rows_unchanged(
            before, after, skip=(("video", OWNED_OVERLAY_A.name, 3),)),
    }


def _judge_delete_fusion_bearing(case: dict) -> dict:
    """A clip that carries a Fusion composition, deleted."""
    before = case["after_setup"]
    after = case["after"]
    target = _rows_named(before, OWNED_VIDEO_A.name, "video")
    carried = [row for row in target
               if [name for name in (row.get("fusion_comps") or ())
                   if name != "Fusion Composition"]]
    return {
        "target_absent": len(target) == 1
        and not _rows_named(after, OWNED_VIDEO_A.name, "video"),
        # The composition went WITH the clip: nothing that carried the
        # treatment is left behind on the row.
        "fusion_comp_gone_with_target": bool(carried)
        and not [row for row in after.get("items", [])
                 if row.get("name") == OWNED_VIDEO_A.name],
        "other_rows_unchanged": _other_rows_unchanged(
            before, after, skip=(("video", OWNED_VIDEO_A.name, 1),)),
    }


def _ripple_scope(before: dict, after: dict) -> dict:
    """What the ripple moved, row by row and clip by clip.

    The punch list asks for the ripple's scope to be MEASURED, not
    assumed.  The clips are matched by NAME and KIND rather than by
    unique id, because a shifted clip is deleted and re-placed and
    therefore carries a NEW identity: the clip that moved is the same
    picture at a new frame, not the same item.

    The answer names three things: the rows that LOST a clip, the rows
    where a clip MOVED, and the rows that did not move at all.
    """
    def index(state: dict) -> dict:
        found = {}
        for row in state.get("items", []):
            key = (row.get("kind"), row.get("name"))
            found.setdefault(key, []).append(row)
        return found

    def rows_of(state: dict) -> set:
        rows = set()
        for row in state.get("items", []):
            rows.add(f"{row.get('kind')}{row.get('track')}")
        for kind, names in (state.get("track_names") or {}).items():
            for index_ in range(1, len(names) + 1):
                rows.add(f"{kind}{index_}")
        return rows

    before_rows, after_rows = index(before), index(after)
    removed, moved, unchanged = [], [], []
    for key in sorted(set(before_rows) | set(after_rows), key=repr):
        kind, name = key
        old = before_rows.get(key, [])
        new = after_rows.get(key, [])
        if len(old) > len(new):
            for row in old[len(new):]:
                removed.append({"row": f"{kind}{row.get('track')}",
                                "name": name,
                                "record_span": list(_record_span(row)),
                                "source_bounds": list(_source_bounds(row))})
            continue
        for old_row, new_row in zip(old, new):
            if (_record_span(old_row) == _record_span(new_row)
                    and _source_bounds(old_row) == _source_bounds(new_row)):
                unchanged.append({"row": f"{kind}{old_row.get('track')}",
                                  "name": name})
            else:
                moved.append({
                    "row": f"{kind}{new_row.get('track')}",
                    "name": name,
                    "from": list(_record_span(old_row)),
                    "to": list(_record_span(new_row)),
                    "source_unchanged": (_source_bounds(old_row)
                                         == _source_bounds(new_row)),
                })
    affected = sorted({entry["row"] for entry in removed + moved})
    return {"removed": removed, "moved": moved, "unchanged": unchanged,
            "rows_affected": affected,
            "rows_untouched": sorted(rows_of(before) - set(affected)),
            "source_ranges_preserved": all(entry["source_unchanged"]
                                           for entry in moved)}


def _judge_ripple_remove_multi_track(case: dict) -> dict:
    """A ripple on a multi-track timeline, measured row by row."""
    before = case["after_setup"]
    after = case["after"]
    scope = _ripple_scope(before, after)
    target = _rows_named(before, OWNED_VIDEO_B.name, "video")
    successor = _rows_named(before, OWNED_VIDEO_A.name, "video")
    duration = 0
    if target:
        duration = _record_span(target[0])[1] - _record_span(target[0])[0]
    shifted_video = _rows_named(after, OWNED_VIDEO_A.name, "video")
    shifted_audio = _rows_named(after, OWNED_VIDEO_A.name, "audio")
    wanted = (_record_span(successor[0])[0] - duration) if successor else None
    video_uid = shifted_video[0].get("unique_id") if shifted_video else ""
    return {
        "target_absent": bool(target)
        and not _rows_named(after, OWNED_VIDEO_B.name),
        # The gap closed on the ripple's own track: the successor sits
        # exactly where the removed clip's frames freed up.
        "same_track_closed": bool(shifted_video) and wanted is not None
        and _record_span(shifted_video[0])[0] == wanted
        and len(shifted_video) == 1,
        # The link group moved with it: the audio twin is on the same
        # frame as its picture, so the row is still in sync.
        "link_group_shifted": bool(shifted_audio) and wanted is not None
        and _record_span(shifted_audio[0])[0] == wanted
        and video_uid in (shifted_audio[0].get("linked_unique_ids") or ()),
        "other_rows_untouched": scope["rows_affected"] == ["audio1", "video1"]
        and "video2" in scope["rows_untouched"]
        and "video3" in scope["rows_untouched"],
        # The scope is MEASURED, and every moved clip kept its source
        # range: a ripple that re-placed a clip from a different part of
        # its media would be a different edit.
        "ripple_scope_measured": bool(scope["removed"])
        and bool(scope["moved"]) and scope["source_ranges_preserved"],
    }


def _judge_ripple_remove_light(case: dict) -> dict:
    """A ripple on a light timeline: the coverage the dense one lacks."""
    before = case["after_setup"]
    after = case["after"]
    scope = _ripple_scope(before, after)
    target = _rows_named(before, OWNED_VIDEO_B.name, "video")
    successor = _rows_named(before, OWNED_VIDEO_A.name, "video")
    duration = 0
    if target:
        duration = _record_span(target[0])[1] - _record_span(target[0])[0]
    shifted = _rows_named(after, OWNED_VIDEO_A.name, "video")
    wanted = (_record_span(successor[0])[0] - duration) if successor else None
    return {
        "target_absent": bool(target)
        and not _rows_named(after, OWNED_VIDEO_B.name),
        "same_track_closed": bool(shifted) and wanted is not None
        and _record_span(shifted[0])[0] == wanted,
        "other_rows_untouched": scope["rows_affected"] == ["video1"]
        and "video2" in scope["rows_untouched"],
        "ripple_scope_measured": bool(scope["removed"])
        and bool(scope["moved"]) and scope["source_ranges_preserved"],
    }


def _judge_trim(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    new = _one(after, OWNED_VIDEO_A.name)
    new_duration = len(range(*_record_span(new))) if new else 0
    return {
        "source_range_narrowed": _source_bounds(new) == (10, 59),
        "duration_matches_source_range": bool(new) and new_duration == 50,
        "record_start_shifted": bool(new) and int(new["record_in"]) == 10,
        "other_clip_untouched": _unchanged(before, after,
                                            OWNED_VIDEO_B.name),
    }


def _judge_trim_linked_av(case: dict) -> dict:
    """Read both halves after one trim; neither half may drift."""
    before = case["after_setup"]
    after = case["after"]
    video_before = _rows_named(before, OWNED_VIDEO_A.name, "video")
    video_after = _rows_named(after, OWNED_VIDEO_A.name, "video")
    audio_before = _rows_named(before, OWNED_VIDEO_A.name, "audio")
    audio_after = _rows_named(after, OWNED_VIDEO_A.name, "audio")
    others_unchanged = _other_rows_unchanged(
        before, after,
        skip=(("video", OWNED_VIDEO_A.name, 1),
              ("audio", OWNED_VIDEO_A.name, 1)))
    return {
        "video_source_range_narrowed": len(video_after) == 1
        and _source_bounds(video_after[0]) == (0, 47),
        "audio_source_range_narrowed": len(audio_after) == 1
        and _source_bounds(audio_after[0]) == (0, 47),
        "audio_picture_record_spans_match": (
            len(video_before) == len(video_after) == 1
            and len(audio_before) == len(audio_after) == 1
            and _record_span(video_after[0]) == _record_span(audio_after[0])
            and _source_bounds(video_after[0]) == _source_bounds(audio_after[0])
            and _record_span(video_after[0]) != _record_span(video_before[0])
            and _record_span(audio_after[0]) != _record_span(audio_before[0])),
        "other_items_untouched": others_unchanged,
    }


def _judge_trim_refused(case: dict) -> dict:
    before = case["after_setup"]
    after = case["after"]
    refused = any(row["exit_code"] for row in case["commands"])
    return {
        "refused": refused,
        "timeline_unchanged": _other_rows_unchanged(before, after),
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
    aroll = [row for row in _items(after, "qual_a.mp4")
             if row.get("kind") == "video"]
    audio = [row for row in _items(after, "qual_a.mp4")
             if row.get("kind") == "audio"]
    return {
        "cover_on_cover_track": bool(cover) and cover["track"] == 2,
        "aroll_unchanged": _unchanged(before, after, "qual_a.mp4"),
        "dialogue_audio_unchanged": len(aroll) == len(audio) == 1
        and bool(aroll[0].get("linked_unique_ids")),
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
    "delete_linked_effects": _judge_delete_linked_effects,
    "delete_retimed_only": _judge_delete_retimed_only,
    "delete_transform_only": _judge_delete_transform_only,
    "delete_linked_audio_twin": _judge_delete_linked_audio_twin,
    "delete_linked_av_moderate": _judge_delete_linked_av_moderate,
    "delete_retimed_moderate": lambda case: _judge_delete_target_only(
        case, OWNED_VIDEO_A.name, OWNED_VIDEO_B.name),
    "delete_still_card": _judge_delete_still_card,
    "delete_fusion_bearing": _judge_delete_fusion_bearing,
    "ripple_remove_multi_track": _judge_ripple_remove_multi_track,
    "ripple_remove_light": _judge_ripple_remove_light,
    "trim_narrows_source_range": _judge_trim,
    "trim_linked_av_stays_synchronized": _judge_trim_linked_av,
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
    *axi_qualification.PREDICATE_FIELDS,
)


def _predicates(measured_states: list[dict]) -> list[dict]:
    """The conditions every scenario that earned the pair was measured in.

    A field the scenarios agree on is an `equals`; a field they differ on
    is a `one_of` over the values they were measured in - the union of
    the conditions the evidence was ACTUALLY earned under, which is the
    pattern the W1-W4 widening used. One scenario's measurement alone
    would narrow a row to that one composition and refuse the others
    (measured 2026-10-20: the delete of a linked audio twin was
    refused because only the video side had been measured).
    """
    values: dict[str, list[str]] = {}
    for measured in measured_states:
        state = (measured or {}).get("state") or {}
        for field in _PREDICATE_FIELDS:
            value = state.get(field)
            if value in (None, ""):
                continue
            values.setdefault(field, [])
            if str(value) not in values[field]:
                values[field].append(str(value))
    predicates = []
    for field in _PREDICATE_FIELDS:
        seen = values.get(field)
        if not seen:
            continue
        predicate = {
            "field": field,
            "operator": "equals" if len(seen) == 1 else "one_of",
            "value": seen[0] if len(seen) == 1 else seen,
        }
        predicates.append(axi_qualification.canonical_predicate(predicate))
    return predicates


def _shipped_coverage(operation: str, backend: str) -> dict:
    """The coverage the SHIPPED registry row holds for this pair.

    A qualification is the union of the states a pair has been earned
    in, and a run that measures a state the pair ALREADY held must not
    NARROW the row it did not re-measure: the coverage the registry
    already carries is evidence too.  The product-format lane's rows
    are the union of what the pair had and what the run measured, and
    this lane writes the same shape.
    """
    try:
        payload = json.loads(
            axi_qualification.REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    record = (payload.get("records") or {}).get(
        f"{operation}::{backend}") or {}
    coverage = record.get("coverage") or {}
    return {field: [str(value) for value in values]
            for field, values in (coverage.get("dimensions") or {}).items()
            if values}


def _shipped_evidence(operation: str, backend: str) -> str:
    """The evidence the SHIPPED registry row names for this pair."""
    try:
        payload = json.loads(
            axi_qualification.REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    record = (payload.get("records") or {}).get(
        f"{operation}::{backend}") or {}
    return str(record.get("evidence") or "")


def _qualification_result(primitive: Primitive, scenarios: list[dict],
                          measured: list[dict]) -> dict:
    """One current-schema qualification document for this pair."""
    checks: dict = {}
    for scenario in scenarios:
        checks.update(scenario.get("checks") or {})
    states = [(row or {}).get("state") or {} for row in measured]
    state = states[0] if states else {}
    coverage_dimensions = {}
    for field in axi_qualification.PREDICATE_FIELDS:
        values = []
        for measured_state in states:
            value = measured_state.get(field)
            if value not in (None, "") and str(value) not in values:
                values.append(str(value))
        coverage_dimensions[field] = values
    # The union of what this run measured and what the shipped row
    # already held.  A run that measures LESS never narrows a row.
    shipped = _shipped_coverage(primitive.operation, primitive.backend)
    unmeasured = [field for field, values in shipped.items()
                  if not set(values) <= set(coverage_dimensions.get(field)
                                            or [])]
    for field, values in shipped.items():
        merged = list(coverage_dimensions.get(field) or [])
        for value in values:
            if value not in merged:
                merged.append(value)
        coverage_dimensions[field] = merged
    coverage = {
        "dimensions": coverage_dimensions,
        "configurations": ["measured_qualification_timeline"],
        "uncovered": {},
        "source": ("measured" if not unmeasured
                   else "derived_from_v1_evidence"),
    }
    return {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": primitive.operation,
        "backend": primitive.backend,
        "result": "passed",
        "evidence": (
            "tests/live/reports/axi-primitive-qualification.json#"
            + ",".join(scenario["id"] for scenario in scenarios)
            + ("; the coverage this row already held: "
               + _shipped_evidence(primitive.operation, primitive.backend)
               if unmeasured else "")),
        "resolve_version": str(state.get("resolve_version") or ""),
        "environment": {
            "resolve_version": str(state.get("resolve_version") or ""),
            "platform": sys.platform,
        },
        "semantic_boundary": {
            "applies_to": [
                (f"{primitive.operation} on {primitive.backend} in the "
             "measured qualification state")],
            "does_not_apply_to": [
                "states or operation/backend pairs not named by this evidence"],
        },
        "coverage": coverage,
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
        "qualification_predicates": axi_qualification.dimensions.predicates_for(
            coverage_dimensions),
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
        measured.setdefault(key, []).append(
            row.get("case", {}).get("qualification_state") or {})
    rows = []
    for key, scenarios in groups.items():
        primitive = next(item for item in PRIMITIVES
                         if (item.operation, item.backend) == key)
        states = [(item or {}).get("state") or {} for item in measured[key]]
        missing_dimensions = [
            field for field in axi_qualification.PREDICATE_FIELDS
            if not any(state.get(field) not in (None, "")
                       for state in states)]
        if missing_dimensions:
            gaps.append({
                "operation_id": primitive.operation,
                "backend": primitive.backend,
                "scenario": ",".join(scenario["id"]
                                      for scenario in scenarios),
                "reason": ("the run did not measure qualification "
                           "dimensions: " + ", ".join(missing_dimensions)),
            })
            continue
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


def _is_legacy_report(path: Path) -> bool:
    """Whether a committed report predates the explicit boundary format.

    A legacy report carries no environment, no semantic boundary and no
    coverage, so the importer REFUSES it (`library/tools/axi_qualification.py`
    refuses the v1 schema outright) - it can never qualify anything, and
    a registry row promoted from one is a row whose committed evidence
    cannot be read back.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload.get("schema") == axi_qualification.LEGACY_REPORT_SCHEMA


def _write_registry_results(report: dict) -> tuple[list[dict], list[dict]]:
    """Write one registry-shaped result per earned pair.

    An existing row is NEVER overwritten: the new evidence is written
    beside it and the report names both, so a promotion stays a
    deliberate act on a file the operator chose.

    The ONE exception is a committed report in the LEGACY schema. It
    cannot be imported at all - the importer refuses it - so it is not a
    promotion anybody could make, and the fresh evidence REPLACES it at
    the canonical path. The canonical path is the row's committed
    evidence: the contract reads it back and requires it to re-import to
    the row the registry holds, so leaving a stale legacy document there
    would keep a row that claims a qualification nothing can satisfy.
    """
    rows, gaps = _registry_rows(report)
    written = []
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    for (operation, backend), result in rows:
        canonical = REGISTRY_DIR / f"{operation}__{backend}.json"
        beside = REGISTRY_DIR / f"{operation}__{backend}__axi-primitive.json"
        legacy = canonical.exists() and _is_legacy_report(canonical)
        path = canonical if legacy else beside
        document = json.dumps(result, indent=2, sort_keys=True) + "\n"
        path.write_text(document, encoding="utf-8")
        # A legacy document BESIDE the canonical one is stale evidence of
        # the same pair, and the importer refuses it as firmly as it
        # refuses the canonical one - so it is refreshed with the same
        # current evidence rather than left claiming a boundary the
        # current format cannot express.
        if legacy and beside.exists() and _is_legacy_report(beside):
            beside.write_text(document, encoding="utf-8")
        written.append({"path": _reported_path(path),
                        "operation_id": operation, "backend": backend,
                        "existing_row_left_in_place": not legacy,
                        "replaced_legacy_report": legacy})
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
                           (POOL_DIR / "qual_c.mp4", OWNED_VIDEO_C),
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
            import traceback

            results.append({
                **asdict(primitive),
                "status": "fail",
                "first_attempt": True,
                "collateral_changes": [],
                "checks": {},
                "error": f"{type(exc).__name__}: {exc}",
                # The traceback: a failure whose cause is one frame away
                # is not a failure a reader can act on.
                "traceback": traceback.format_exc(),
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
    if os.environ.get("REN_AXI_QUAL_TIMELINE"):
        if (not TIMELINE.startswith("fm-axi-qual-")
                and TIMELINE != "fm-scratch frames smoke"):
            raise ValueError("live qualification must use a named scratch timeline")
        small.TIMELINE = TIMELINE
    selected = os.environ.get("REN_AXI_QUAL_ONLY")
    all_cases = _cases()
    if selected:
        wanted = set(selected.split(","))
        unknown = wanted - set(all_cases)
        if unknown:
            raise ValueError(f"unknown qualification scenario IDs: {sorted(unknown)}")
        cases = {name: case for name, case in all_cases.items()
                 if name in wanted}
    else:
        cases = all_cases
    script = _broker_script(cases)
    receipt = small._qualification_batch(script)
    steps = (receipt.get("result") or {}).get("steps", [])
    if not steps:
        raise RuntimeError(
            f"the qualification batch returned no steps: {receipt}")
    stdout = small.result_stdout(receipt)
    start = stdout.find("{\n")
    if start < 0:
        steps_detail = json.dumps(steps, indent=2, default=str)
        raise RuntimeError(
            "the qualification batch printed no JSON result; stdout="
            + repr(stdout[-4000:]) + "; steps=" + steps_detail[-6000:])
    payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    by_id = {row["id"]: row for row in payload.get("cases", [])}
    actions = {primitive.id: _make_action(primitive, by_id)
               for primitive in PRIMITIVES if primitive.id in cases}
    report = run_batch(actions)
    existing_report = {}
    if selected and REPORT_PATH.is_file():
        existing_report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        merged = {row["id"]: row
                  for row in existing_report.get("results", [])}
        merged.update({row["id"]: row for row in report["results"]})
        report["results"] = [merged[key] for key in merged]
        report["scenarios"] = len(report["results"])
        report["passed"] = sum(row["status"] == "pass"
                               for row in report["results"])
        report["failed"] = sum(row["status"] == "fail"
                               for row in report["results"])
    # Registry evidence is limited to this run's scenarios. The pair a
    # run touched also carries the scenarios the pair ALREADY earned, so
    # the coverage the registry holds is the union of what the pair had
    # and what this run measured - a run that measures LESS never
    # narrows a row it did not re-measure.
    registry_report = report
    if selected:
        pairs = {(primitive.operation, primitive.backend)
                 for primitive in PRIMITIVES if primitive.id in cases}
        pair_ids = {primitive.id for primitive in PRIMITIVES
                    if (primitive.operation, primitive.backend) in pairs}
        registry_report = {
            **report,
            "results": [row for row in report["results"]
                        if row["id"] in pair_ids],
        }
    written, gaps = _write_registry_results(registry_report)
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
