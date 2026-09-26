"""Sub-block anchors: P5 lands within one frame, and unresolvable anchors refuse.

Fidelity probe P5 ("punch 15% on 'quit', whoosh exactly on it"): "quit"
is at timeline 11.44 s in the spine, the whoosh was placed at the block
start, 3.06 s early, and the punch spanned the whole 10 s block - because
no plan field anchored to a word, a beat or a frame.

These tests drive the real post-bridges on a fixture spine with "quit"
at timeline 11.44 s and assert both placements land within one frame of
the word's start (stating the frame error), plus the refusal half: an
anchor that names nothing placeable raises `AnchorRefused` - the
`RenRefusal` shape the retry path hands back to the model - instead of
falling back to the block start silently.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_02_plan_transitions.post_bridge import (
    resolve_transitions,
)
from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
from library.steps.step_4_04_plan_sfx.post_bridge import resolve_sfx
from library.tools.context_views import build_view
from library.tools.frame_utils import seconds_to_frame
from library.tools.sub_block_anchor import AnchorRefused, resolve_anchor

FPS = 30.0
# The probe's own numbers: "quit" at timeline 11.44 s, in a 10 s block.
QUIT_START = 11.44
QUIT_END = 11.84
QUIT_FRAME = seconds_to_frame(QUIT_START, FPS)


def _spine() -> dict:
    return {"structure": [
        {"position": 1, "block_type": "speech", "clip_id": "clip_001",
         "source_start": 100.0, "source_end": 110.0,
         "timeline_start": 8.0, "timeline_end": 18.0,
         "word_timestamps": [
             {"word": "tell", "source_start": 100.0, "source_end": 100.4},
             {"word": "me", "source_start": 100.5, "source_end": 100.7},
             {"word": "when", "source_start": 101.0, "source_end": 101.4},
             {"word": "you", "source_start": 102.0, "source_end": 102.4},
             {"word": "quit", "source_start": 103.44,
              "source_end": 103.84},
             {"word": "now", "source_start": 104.0, "source_end": 104.4},
         ],
         "alignment_method": "mfa"},
        {"position": 2, "block_type": "speech", "clip_id": "clip_002",
         "source_start": 200.0, "source_end": 205.0,
         "timeline_start": 18.0, "timeline_end": 23.0,
         "word_timestamps": [
             {"word": "hello", "source_start": 200.0,
              "source_end": 200.4},
         ],
         "alignment_method": "mfa"},
    ]}


def _catalog() -> list:
    return [{
        "sfx_id": "test_whoosh.wav",
        "path": "/nonexistent/test_whoosh.wav",
        "category": "Accents",
        "duration_seconds": 1.2,
        "envelope": "punchy",
        "transient_offset_sec": 0.05,
    }]


def _grid(source="detected"):
    # The grid opens with the block (single-section mapping: file time
    # minus source_in), so bar 2 beat 3 lands inside block 1.
    beats = [round(8.37 + i * 0.5, 3) for i in range(32)]
    downbeats = [round(8.37 + i * 2.0, 3) for i in range(8)]
    return {"tempo": {"bpm": 120.0, "beats": beats,
                      "downbeats": downbeats,
                      "downbeat_source": source, "method": "beat_this"}}


# ── P5 end to end on fixtures ─────────────────────────────────────────

def test_p5_punch_spans_the_word_not_the_block():
    """The punch covers 11.44-11.84 s, not the whole 8.0-18.0 s block."""
    plan = [{
        "target_block_position": 1,
        "effect_type": "zoom_emphasis",
        "params": {"zoom_start": 1.0, "zoom_mid": 1.15,
                   "zoom_end": 1.0},
        "rationale": "punch 15% on 'quit'",
        "anchor": {"word": "quit"},
        "anchor_end": {"word": "quit", "edge": "end"},
    }]
    resolved = resolve_vfx(plan, _spine(), FPS)
    assert len(resolved) == 1
    vfx = resolved[0]
    assert vfx["timeline_start"] == pytest.approx(QUIT_START, abs=1e-9)
    assert vfx["timeline_end"] == pytest.approx(QUIT_END, abs=1e-9)
    frame_error = abs(seconds_to_frame(vfx["timeline_start"], FPS)
                      - QUIT_FRAME)
    assert frame_error <= 1, f"P5 punch frame error: {frame_error} frames"
    assert "anchor_method" in vfx


def test_p5_whoosh_lands_on_the_word_not_the_block_start():
    """The whoosh plays at 11.44 s, not at the block start 8.0 s."""
    plan = [{
        "sfx_id": "test_whoosh.wav",
        "spine_block_position": 1,
        "volume_db": -8.0,
        "rationale": "whoosh exactly on 'quit'",
        "anchor": {"word": "quit"},
    }]
    result = resolve_sfx(plan, _spine(), [], {}, {}, FPS, {}, {},
                         catalog=_catalog())
    assert len(result["sfx_list"]) == 1
    placed = result["sfx_list"][0]
    assert placed["timeline_in"] != pytest.approx(8.0, abs=1e-9)
    frame_error = abs(placed["timeline_in_frame"] - QUIT_FRAME)
    assert frame_error <= 1, f"P5 whoosh frame error: {frame_error} frames"
    assert "anchor" in placed["placement_method"]


def test_p5_cut_lands_on_the_word_end():
    """A transition anchored to the word cuts at 11.84 s."""
    plan = [{
        "cut_point_position": 2,
        "type": "hard_cut",
        "rationale": "cut right after 'quit'",
        "anchor": {"word": "quit", "edge": "end"},
    }]
    resolved = resolve_transitions(plan, _spine(), {}, frame_rate=FPS)
    assert len(resolved) == 1
    assert resolved[0]["cut_point_timeline"] == pytest.approx(
        QUIT_END, abs=1e-9)


# ── Beat and frame anchors resolve exactly ────────────────────────────

def test_bar_beat_anchor_resolves_on_the_detected_grid():
    block = _spine()["structure"][0]
    hit = resolve_anchor({"bar": 2, "beat": 3}, block=block,
                         music_analysis=_grid(), music_selection=None,
                         frame_rate=FPS, step="plan_sfx",
                         plan="sfx_creative", index=0)
    # Bar 2 opens at 10.37 s; its beats are 10.37/10.87/11.37/11.87.
    assert hit["timeline_seconds"] == pytest.approx(11.37, abs=1e-9)


def test_downbeat_anchor_resolves_to_the_bar_start():
    block = _spine()["structure"][0]
    hit = resolve_anchor({"downbeat": 4}, block=block,
                         music_analysis=_grid(), music_selection=None,
                         frame_rate=FPS, step="plan_sfx",
                         plan="sfx_creative", index=0)
    assert hit["timeline_seconds"] == pytest.approx(14.37, abs=1e-9)


def test_frame_anchor_resolves_inside_the_block():
    block = _spine()["structure"][0]
    hit = resolve_anchor({"frame": QUIT_FRAME}, block=block,
                         frame_rate=FPS, step="plan_vfx",
                         plan="vfx_creative", index=0)
    assert hit["frame"] == QUIT_FRAME
    # timeline_seconds is rounded to ms; the frame is the exact record.
    assert abs(hit["timeline_seconds"] - QUIT_FRAME / FPS) < 1e-3


def test_frame_anchor_uses_shared_frame_boundary_not_rounded_seconds():
    """Finding 13: rounded edge seconds must not refuse their shared frame."""
    block = dict(_spine()["structure"][0])
    block.update({
        # 767 / 30 is 25.5666..., whose millisecond display value rounds
        # upward. The shared frame span still starts exactly at frame 767.
        "timeline_start": 25.567,
        "timeline_end": 30.0,
        "timeline_start_frame": 767,
        "timeline_end_frame": 900,
    })
    hit = resolve_anchor({"frame": 767}, block=block,
                         frame_rate=FPS, step="plan_transitions",
                         plan="transition_creative", index=0)
    assert hit["frame"] == 767


# ── Unresolvable anchors refuse, never fall back ──────────────────────
#
# One parametrized test, one case per refusal branch: a word the block
# does not say, an occurrence past its matches, a word anchor on a
# wordless block, a frame outside the block, a detected grid demanded
# of an estimated one, a beat anchor with no grid routed, a bar past
# the grid, two addresses in one anchor, an unread anchor sub-key, a
# non-dict anchor, and an offset pushing the moment outside the block.

def _block():
    return _spine()["structure"][0]


UNRESOLVABLE = [    pytest.param({"word": "never"}, {},
                 "word 'never' is not spoken",
                 id="word-absent"),
    pytest.param({"word": "quit", "occurrence": 2}, {},
                 "occurrence 2", id="occurrence-overflow"),
    pytest.param({"word": "quit"}, {"wordless": True},
                 "is not spoken", id="wordless-block"),
    pytest.param({"frame": 900}, {}, "outside block",
                 id="frame-outside"),
    pytest.param({"downbeat": 1, "grid": "detected"},
                 {"source": "estimated"}, "estimated",
                 id="estimated-grid"),
    pytest.param({"beat": 3}, {"no_grid": True}, "no usable beat grid",
                 id="beat-without-grid"),
    pytest.param({"bar": 99, "beat": 1}, {}, "has 8 bars",
                 id="bar-past-grid"),
    pytest.param({"word": "quit", "beat": 3}, {}, "2 addresses",
                 id="two-addresses"),
    pytest.param({"word": "quit", "ofset_seconds": 1.0}, {},
                 "ofset_seconds", id="unknown-subkey"),
    pytest.param("quit", {}, "is not an object", id="non-dict"),
    pytest.param({"word": "quit", "offset_seconds": 99.0}, {},
                 "outside block", id="offset-outside"),
]


@pytest.mark.parametrize(("anchor", "tweak", "match"), UNRESOLVABLE)
def test_unresolvable_anchors_refuse(anchor, tweak, match):
    block = _block()
    if tweak.get("wordless"):
        block = dict(block, word_timestamps=[])
    music = {} if tweak.get("no_grid") else _grid(
        tweak.get("source", "detected"))
    with pytest.raises(AnchorRefused) as excinfo:
        resolve_anchor(anchor, block=block, music_analysis=music,
                       music_selection=None, frame_rate=FPS,
                       step="plan_sfx", plan="sfx_creative", index=0)
    assert match in str(excinfo.value)


def test_misplaced_extent_anchors_refuse():
    """Each consumer's extent policy refuses what it cannot place.

    A sound's extent is its duration_seconds, a cut is a point, and a
    VFX span must run forward - so `anchor_end` on SFX and transitions,
    and a backwards span on VFX, refuse rather than landing nowhere.
    """
    sfx_plan = [{
        "sfx_id": "test_whoosh.wav",
        "spine_block_position": 1,
        "volume_db": -8.0,
        "rationale": "extent is duration, not anchors",
        "anchor": {"word": "quit"},
        "anchor_end": {"word": "now"},
    }]
    with pytest.raises(AnchorRefused):
        resolve_sfx(sfx_plan, _spine(), [], {}, {}, FPS, {}, {},
                    catalog=_catalog())

    cut_plan = [{
        "cut_point_position": 2,
        "type": "hard_cut",
        "rationale": "a cut is a point",
        "anchor": {"word": "quit", "edge": "end"},
        "anchor_end": {"word": "quit", "edge": "end"},
    }]
    with pytest.raises(AnchorRefused):
        resolve_transitions(cut_plan, _spine(), {}, frame_rate=FPS)

    vfx_plan = [{
        "target_block_position": 1,
        "effect_type": "zoom_emphasis",
        "params": {"zoom_start": 1.0, "zoom_mid": 1.15,
                   "zoom_end": 1.0},
        "rationale": "backwards span",
        "anchor": {"word": "now"},
        "anchor_end": {"word": "quit"},
    }]
    with pytest.raises(AnchorRefused):
        resolve_vfx(vfx_plan, _spine(), FPS)


def test_refusal_is_the_ren_shape_with_a_fix():
    with pytest.raises(AnchorRefused) as excinfo:
        resolve_anchor({"word": "never"}, block=_block(),
                       step="plan_sfx", plan="sfx_creative", index=3)
    rendered = excinfo.value.render()
    assert rendered.startswith("ren: refused - ")
    assert "why:" in rendered
    assert "fix:" in rendered
    assert "sfx_creative" in rendered


# ── Motion anchors: cut and place on action ───────────────────────────

def _motion():
    # The action IS the word: the onset lands on "quit"'s start and
    # the apex on its end, so a motion-anchored plan and a
    # word-anchored one agree to the frame - the two vocabularies
    # addressing one moment.
    return [{"clip_id": "clip_001", "motion_method": "farneback",
             "motion_peaks": [
                 {"time": 103.44, "kind": "onset",
                  "magnitude": 0.5},
                 {"time": 103.84, "kind": "apex",
                  "magnitude": 0.7},
                 {"time": 107.0, "kind": "apex",
                  "magnitude": 0.4}]}]


def test_effect_spans_from_onset_to_apex():
    """A shake from where the action starts to where it peaks."""
    plan = [{
        "target_block_position": 1,
        "effect_type": "screen_shake",
        "params": {"shake_x": 0.02, "shake_y": 0.02,
                   "shake_decay_frames": 12},
        "rationale": "shake on the action, not the block",
        "anchor": {"action_onset": 1},
        "anchor_end": {"motion_peak": 1},
    }]
    resolved = resolve_vfx(plan, _spine(), FPS,
                           temporal_indices=_motion())
    assert len(resolved) == 1
    vfx = resolved[0]
    assert vfx["timeline_start"] == pytest.approx(QUIT_START, abs=1e-9)
    assert vfx["timeline_end"] == pytest.approx(QUIT_END, abs=1e-9)
    frame_error = abs(seconds_to_frame(vfx["timeline_start"], FPS)
                      - QUIT_FRAME)
    assert frame_error <= 1, f"motion punch frame error: {frame_error}"


def test_cut_lands_on_the_action_onset():
    """A transition anchored to the onset cuts at 11.44 s."""
    plan = [{
        "cut_point_position": 2,
        "type": "hard_cut",
        "rationale": "cut on the action",
        "anchor": {"action_onset": 1},
    }]
    resolved = resolve_transitions(plan, _spine(), {}, _motion(),
                                   frame_rate=FPS)
    assert len(resolved) == 1
    cut = resolved[0]["cut_point_timeline"]
    assert cut == pytest.approx(QUIT_START, abs=1e-9)
    assert abs(seconds_to_frame(cut, FPS) - QUIT_FRAME) <= 1


MOTION_UNRESOLVABLE = [
    pytest.param({"motion_peak": 9}, "occurrence 9",
                 id="occurrence-overflow"),
    pytest.param({"action_onset": 1, "edge": "end"}, "a peak is a point",
                 id="edge-on-a-point"),
    pytest.param({"motion_peak": 1, "grid": "detected"},
                 "not motion ones", id="grid-on-motion"),
]


@pytest.mark.parametrize(("anchor", "match"), MOTION_UNRESOLVABLE)
def test_unresolvable_motion_anchors_refuse(anchor, match):
    with pytest.raises(AnchorRefused) as excinfo:
        resolve_anchor(anchor, block=_block(),
                       temporal_indices=_motion(),
                       frame_rate=FPS, step="plan_vfx",
                       plan="vfx_creative", index=0)
    assert match in str(excinfo.value)


def test_motion_anchor_without_measurement_refuses():
    """No routed summaries, or an unmeasured clip: refuse, never guess."""
    with pytest.raises(AnchorRefused) as excinfo:
        resolve_anchor({"motion_peak": 1}, block=_block(),
                       temporal_indices=[],
                       frame_rate=FPS, step="plan_vfx",
                       plan="vfx_creative", index=0)
    assert "no motion measurement is routed" in str(excinfo.value)
    rated = [{"clip_id": "clip_001", "motion_method": "unmeasured",
              "motion_peaks": []}]
    with pytest.raises(AnchorRefused) as excinfo:
        resolve_anchor({"motion_peak": 1}, block=_block(),
                       temporal_indices=rated,
                       frame_rate=FPS, step="plan_vfx",
                       plan="vfx_creative", index=0)
    assert "unmeasured" in str(excinfo.value)


def test_motion_anchor_outside_the_block_range_refuses():
    """Peaks the block's range does not contain are not addressable."""
    far = [{"clip_id": "clip_001", "motion_method": "farneback",
            "motion_peaks": [
                {"time": 150.0, "kind": "apex", "magnitude": 0.9}]}]
    with pytest.raises(AnchorRefused) as excinfo:
        resolve_anchor({"motion_peak": 1}, block=_block(),
                       temporal_indices=far,
                       frame_rate=FPS, step="plan_vfx",
                       plan="vfx_creative", index=0)
    assert "no measured apex inside block" in str(excinfo.value)


# ── The beat-grid view behind the anchors ─────────────────────────────

def test_beatgrid_view_lists_bars_with_provenance():
    view = build_view("beatgrid", {"music_analysis": _grid(),
                                   "music_selection": None})
    grid = view["beatgrid"]
    assert grid["bar_count"] == 8
    assert grid["downbeat_source"] == "detected"
    assert grid["bars"][0] == {"bar": 1, "downbeat_seconds": 8.37,
                               "beats_in_bar": 4}
    assert "legend" in grid
    # And it is absent - not empty, not guessed - with no grid routed.
    assert build_view("beatgrid", {}) == {}
    assert build_view("beatgrid", {"music_analysis": {}}) == {}
