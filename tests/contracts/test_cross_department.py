"""Cross-department constraints, checked against the edit graph.

Each test names a defect this module closes: a conflict between two
departments' decisions that no module could reason about - the same
transition twice in a row, a drawn transition whose cut a B-roll
interjection covers, a motion graphic colliding with a caption row, a
mix window spanning a cut, an effect or sound landing on a block whose
picture B-roll replaced. The graph is derived, so every test builds a
small edit's `pipeline_data.json` state in the shape step 3.02/4.02/
4.05/4.06/5.02 actually emit it, builds the graph, and asserts the
conflict is reported with the node that yields - never a registry,
enumeration or schema entry, which would pass no matter what the
checker actually finds.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import cross_department, edit_graph

REPO = Path(__file__).resolve().parents[2]


def _block(position, block_type="speech", clip_id="clip_001",
           source=(0.0, 5.0), timeline=(0.0, 5.0), behavior="background"):
    return {
        "position": position,
        "block_type": block_type,
        "clip_id": clip_id,
        "source_start": source[0],
        "source_end": source[1],
        "timeline_start": timeline[0],
        "timeline_end": timeline[1],
        "word_timestamps": [{"word": "a", "source_start": source[0],
                              "source_end": source[1]}],
        "alignment_method": "search",
        "music_behavior": behavior,
    }


def _state(**overrides) -> dict:
    """A small edit's recorded state, in the shapes the steps emit.

    Three speech blocks cut from clip_001. Each override replaces one
    capability's record, so a test states only the decision it is about.
    """
    records = {
        "spine.mesh": {"audio_spine": {"structure": [
            _block(1, source=(5.0, 10.0), timeline=(0.0, 5.0)),
            _block(2, source=(15.0, 20.0), timeline=(5.0, 10.0)),
            _block(3, source=(25.0, 30.0), timeline=(10.0, 15.0)),
        ]}},
    }
    records.update(overrides)
    return {"capability_outputs": records}


def _transitions(*entries) -> dict:
    """A transition_spec in the shape step 4.02 emits it: a bare list."""
    return {"transitions.resolve": {"transition_spec": list(entries)}}


def _interjection(over_position, start, end, clip_id="clip_002"):
    """A B-roll interjection in the shape step 3.02 emits it.

    The block an interjection covers is keyed `over_spine_block_position`
    - the same key compile_manifest reads - so the graph and the
    compiler agree on which block it is.
    """
    return {
        "over_spine_block_position": over_position,
        "assigned_clip": {"clip_id": clip_id,
                          "video_in": 0.0, "video_out": 2.0},
        "timeline_start": start,
        "timeline_end": end,
    }


def _drawn(cut_position, cut_time, transition_type="crash_zoom"):
    return {
        "cut_point_position": cut_position,
        "transition_type": transition_type,
        "outgoing_position": cut_position - 1,
        "incoming_position": cut_position,
        "cut_time": cut_time,
        "duration_seconds": 0.5,
    }


def _conflicts(state: dict) -> list:
    return cross_department.check_cross_department(
        edit_graph.build_graph(state))


def _finding(conflicts, constraint, yields):
    """The one conflict of a constraint that names `yields`, or fail."""
    matches = [c for c in conflicts
               if c.constraint == constraint and c.yields == yields]
    assert len(matches) == 1, (
        f"expected one {constraint} conflict yielding {yields}, "
        f"found {[(c.constraint, c.yields) for c in conflicts]}")
    return matches[0]


# ── The fidelity test: a transition a B-roll interjection covers ─────


def test_a_drawn_transition_whose_cut_an_interjection_covers_yields_to_it():
    """The defect: a transition that needs a V1 boundary, sitting where
    a B-roll interjection covers the cut, is placed on V1 and draws
    through a boundary the viewer never sees - the interjection is on
    top. Nothing reasons about it; the compile downgrades pairwise, if
    at all, with a reason that names no cause. The fix checks the
    constraint against the graph and reports the conflict with the
    node that yields: the transition yields to the interjection.
    """
    state = _state(
        **_transitions(_drawn(2, 5.0)),
        **{"broll.resolve": {
            "b_roll_assignments": [],
            "b_roll_interjections": [_interjection(2, 4.0, 11.0)]}},
    )
    conflicts = _conflicts(state)
    finding = _finding(
        conflicts, cross_department.TRANSITION_BOUNDARY, "transition:2")
    assert finding.names == ("transition:2", "broll_interjection:0")
    assert "broll_interjection:0" in finding.reason
    assert "5.0" in finding.reason


def test_a_transition_past_an_interjections_reach_is_not_a_conflict():
    """The defect: a checker that fires on any transition near any
    interjection reports conflicts the picture does not create - a cut
    the interjection does not cover keeps its V1 boundary. The fix
    reports the conflict only when the interjection's span contains the
    cut.
    """
    state = _state(
        **_transitions(_drawn(3, 10.0)),
        **{"broll.resolve": {
            "b_roll_assignments": [],
            "b_roll_interjections": [_interjection(1, 0.0, 4.0)]}},
    )
    assert _conflicts(state) == []


# ── The transition department's global grammar ──────────────────────


def test_the_same_drawn_type_twice_in_a_row_reports_the_later_yielding():
    """The defect: the same transition type can appear twice in a row
    with no rule against it - each cut is decided independently from the
    fixed vocabulary. The fix checks the grammar against the graph's
    cut order and reports the later transition as the one that yields.
    """
    state = _state(**_transitions(
        _drawn(2, 5.0, "crash_zoom"),
        _drawn(3, 10.0, "crash_zoom"),
    ))
    finding = _finding(
        _conflicts(state), cross_department.TRANSITION_GRAMMAR,
        "transition:3")
    assert finding.names == ("transition:2", "transition:3")
    assert "crash_zoom" in finding.reason


def test_two_different_drawn_types_in_a_row_are_not_a_conflict():
    """The defect: a grammar check that fires on any two drawn
    transitions in a row forbids every varied cut sequence. The fix
    reports only the same type twice.
    """
    state = _state(**_transitions(
        _drawn(2, 5.0, "crash_zoom"),
        _drawn(3, 10.0, "glow"),
    ))
    assert _conflicts(state) == []


# ── Graphics against graphics ───────────────────────────────────────


def test_a_motion_graphic_colliding_with_a_caption_row_yields():
    """The defect: a graphic can collide with a caption row - both are
    planned per-overlay with their own values, and the compile checks
    overlay existence, not semantic collision. The fix checks the two
    against each other on the block they share and reports the motion
    graphic as the one that yields: the caption carries the words.
    """
    state = _state(
        **{"subtitles.render": {"subtitle_overlay": {"segments": [{
            "spine_block_position": 1,
            "timeline_start": 0.5, "timeline_end": 4.5,
            "text": "hello",
        }]}}},
        **{"motion_graphics.render": {"motion_graphics_overlay": {
            "segments": [{
                "spine_block_position": 1,
                "timeline_start": 1.0, "timeline_end": 4.0,
                "text": "BIG",
            }]}}},
    )
    finding = _finding(
        _conflicts(state), cross_department.GRAPHIC_CAPTION,
        "graphic:1:motion_graphics")
    assert finding.names == (
        "graphic:1:motion_graphics", "graphic:1:subtitle")
    assert "block 1" in finding.reason


def test_a_motion_graphic_on_its_own_block_is_not_a_conflict():
    """The defect: a collision check that fires on any caption and any
    graphic in the same edit forbids every overlay. The fix reports
    only the two that share a block.
    """
    state = _state(
        **{"subtitles.render": {"subtitle_overlay": {"segments": [{
            "spine_block_position": 1,
            "timeline_start": 0.5, "timeline_end": 4.5,
            "text": "hello",
        }]}}},
        **{"motion_graphics.render": {"motion_graphics_overlay": {
            "segments": [{
                "spine_block_position": 2,
                "timeline_start": 5.5, "timeline_end": 9.0,
                "text": "BIG",
            }]}}},
    )
    assert _conflicts(state) == []


# ── Mix against transitions ─────────────────────────────────────────


def test_a_mix_window_spanning_a_drawn_cut_reports_the_window_yielding():
    """The defect: the mix interacts with speech level-only, never
    structurally - a bed window can span a cut a drawn transition
    holds, so the mix behavior changes under the viewer's feet at the
    one moment the edit marks. The fix checks the window's span against
    the cut and reports the window as the one that yields.
    """
    state = _state(
        **_transitions(_drawn(2, 5.0)),
        **{"audio_mix.resolve": {"audio_mix_spec": {"mix_windows": [{
            "timeline_start": 0.0, "timeline_end": 12.0,
            "behavior": "bed",
        }]}}},
    )
    finding = _finding(
        _conflicts(state), cross_department.MIX_WINDOW_TRANSITION,
        "mix_window:0")
    assert finding.names == ("mix_window:0", "transition:2")
    assert "5.0" in finding.reason


def test_a_mix_window_ending_at_a_cut_is_not_a_conflict():
    """The defect: a span check that fires on any window touching any
    cut forbids every bed that ends where a cut does. The fix reports
    only the window whose span strictly contains the cut.
    """
    state = _state(
        **_transitions(_drawn(2, 5.0)),
        **{"audio_mix.resolve": {"audio_mix_spec": {"mix_windows": [{
            "timeline_start": 0.0, "timeline_end": 5.0,
            "behavior": "bed",
        }]}}},
    )
    assert _conflicts(state) == []


# ── Sound and effects against picture ───────────────────────────────


def test_an_sfx_on_a_broll_covered_block_yields():
    """The defect: an SFX is placed per-block with no view of whether
    the block's picture survived - a sound planned for the A-roll's
    content lands on a block whose picture a B-roll cover replaced and
    illustrates nothing. The fix reports the SFX as the one that
    yields to the cover.
    """
    state = _state(
        **{"sfx.resolve": {"sfx_spec": [{
            "spine_block_position": 2,
            "sfx_id": "whoosh.wav",
            "volume_db": -12.0,
            "duration_seconds": 0.3,
        }]}},
        **{"broll.resolve": {"b_roll_assignments": [{
            "spine_block_position": 2,
            "clip_id": "clip_002",
            "video_in": 0.0, "video_out": 2.0,
            "timeline_start": 5.0, "timeline_end": 10.0,
        }], "b_roll_interjections": []}},
    )
    finding = _finding(
        _conflicts(state), cross_department.SFX_ON_COVERED_BLOCK, "sfx:0")
    assert finding.names == ("sfx:0", "broll:2")
    assert "block 2" in finding.reason


def test_a_vfx_on_a_broll_covered_block_yields():
    """The defect: a VFX comp is planned per-clip with no view of
    whether the block's picture survived - the effect draws on a clip
    the viewer does not see. The fix reports the VFX as the one that
    yields to the cover.
    """
    state = _state(
        **{"vfx.resolve": {"enhancement_spec": [{
            "position": 2, "effect": "drift", "clip_id": "clip_001",
        }]}},
        **{"broll.resolve": {"b_roll_assignments": [{
            "spine_block_position": 2,
            "clip_id": "clip_002",
            "video_in": 0.0, "video_out": 2.0,
            "timeline_start": 5.0, "timeline_end": 10.0,
        }], "b_roll_interjections": []}},
    )
    finding = _finding(
        _conflicts(state), cross_department.VFX_ON_COVERED_BLOCK, "vfx:2")
    assert finding.names == ("vfx:2", "broll:2")


# ── The checker does not invent conflicts ───────────────────────────


def test_a_clean_edit_reports_no_conflicts():
    """The defect: a constraint checker that always finds something
    cries wolf until its reader stops reading. The fix reports exactly
    the conflicts the graph state creates - a clean edit reports none.
    """
    state = _state(
        **_transitions(_drawn(2, 5.0, "crash_zoom")),
        **{"subtitles.render": {"subtitle_overlay": {"segments": [{
            "spine_block_position": 1,
            "timeline_start": 0.5, "timeline_end": 4.5,
            "text": "hello",
        }]}}},
        **{"audio_mix.resolve": {"audio_mix_spec": {"mix_windows": [{
            "timeline_start": 0.0, "timeline_end": 5.0,
            "behavior": "bed",
        }]}}},
    )
    assert _conflicts(state) == []


# ── The graph reads the shape step 3.02 actually emits ──────────────


def test_broll_nodes_are_emitted_from_the_bare_list_step_3_02_emits():
    """The defect: the graph read B-roll decisions only in the
    dict-wrapped spelling, so on a real run - where step 3.02 emits the
    two lists bare - no B-roll node existed and every cross-department
    constraint over B-roll was invisible. The fix reads both spellings,
    so the interjection and the assignment are graphed.
    """
    state = _state(**{"broll.resolve": {
        "b_roll_assignments": [{
            "spine_block_position": 3,
            "clip_id": "clip_002",
            "video_in": 0.0, "video_out": 2.0,
            "timeline_start": 10.0, "timeline_end": 15.0,
        }],
        "b_roll_interjections": [_interjection(2, 4.0, 11.0, "clip_004")]}})
    graph = edit_graph.build_graph(state)
    node_ids = {candidate["node_id"] for candidate in graph["nodes"]}
    assert "broll:3" in node_ids
    assert "broll_interjection:0" in node_ids


# ── The wire: compile_manifest acts on the conflict ────────────────


def test_compile_manifest_downgrades_a_transition_an_interjection_covers():
    """The defect: compile_manifest places a drawn transition on V1
    without any view of whether a B-roll interjection covers the cut, so
    the effect draws through a boundary the viewer never sees - and the
    downgrade it falls back to names no cause. The fix builds the edit
    graph from the run's recorded state, checks the cross-department
    constraints, and ships the transition as a hard cut with a reason
    that names the interjection it yields to.
    """
    import os
    import tempfile
    from unittest.mock import patch
    from library.steps.step_5_04_compile_manifest.step import (
        compile_manifest,
    )

    with tempfile.TemporaryDirectory() as project_root:
        pipeline_data = _state(
            **_transitions(_drawn(2, 5.0)),
            **{"broll.resolve": {
                "b_roll_assignments": [],
                "b_roll_interjections": [_interjection(2, 4.0, 11.0)]}},
        )
        with open(os.path.join(project_root, "pipeline_data.json"),
                  "w", encoding="utf-8") as f:
            json.dump(pipeline_data, f)

        aroll = os.path.join(project_root, "aroll.mov")
        broll = os.path.join(project_root, "broll.mov")
        for path in (aroll, broll):
            with open(path, "w") as f:
                f.write("dummy")

        def _inputs(out_dir, filename):
            return {
                "a_roll_assignments": [
                    {"clip_id": "clip_1", "source_clip_id": "clip_1",
                     "source_file": aroll, "video_in": 5.113,
                     "video_out": 10.427, "timeline_start": 0.0,
                     "timeline_end": 5.0},
                    {"clip_id": "clip_1", "source_clip_id": "clip_1",
                     "source_file": aroll, "video_in": 15.234,
                     "video_out": 20.567, "timeline_start": 5.0,
                     "timeline_end": 10.0},
                ],
                "b_roll_assignments": [],
                "b_roll_interjections": [_interjection(2, 4.0, 11.0)],
                "subtitle_plan": {"subtitles": []},
                "transition_spec": [_drawn(2, 5.0)],
                "enhancement_spec": [], "color_grade_spec": {},
                "audio_mix_spec": {}, "music_selection": {},
                "audio_spine": {"frame_rate": 30.0, "structure": [
                    _block(1, source=(5.113, 10.427), timeline=(0.0, 5.0)),
                    _block(2, source=(15.234, 20.567), timeline=(5.0, 10.0)),
                    _block(3, source=(25.345, 30.678), timeline=(10.0, 15.0)),
                ]},
                "clip_catalog": [
                    {"clip_id": "clip_1", "path": aroll,
                     "width": 1080, "height": 1920},
                    {"clip_id": "clip_002", "path": broll,
                     "width": 1080, "height": 1920},
                ],
                "semantic_analysis": [],
                "semantic_analysis_documents": [],
            }

        out_dir = os.path.join(project_root, "pipeline_output")
        os.makedirs(out_dir, exist_ok=True)
        with patch("library.steps.step_5_04_compile_manifest.step.load",
                   side_effect=_inputs):
            manifest = compile_manifest(out_dir)

    downgraded = manifest["transitions_downgraded"]
    assert len(downgraded) == 1
    assert downgraded[0]["requested_type"] == "crash_zoom"
    assert downgraded[0]["shipped_type"] == "hard_cut"
    assert "broll_interjection:0" in downgraded[0]["reason"]
    # The conflict is on the manifest, with the node that yields.
    conflicts = manifest["cross_department_conflicts"]
    boundary = [c for c in conflicts
                if c["constraint"] == cross_department.TRANSITION_BOUNDARY]
    assert len(boundary) == 1
    assert boundary[0]["yields"] == "transition:2"
