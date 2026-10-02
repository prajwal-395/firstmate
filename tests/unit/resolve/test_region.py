"""A region of the timeline is an address, and the domain is part of it.

`library/tools/region.py` is a defect guard before it is a convenience,
so most of what is asserted here is a REFUSAL: a word list that names no
domain, a conversion asked for without a block, an interval with its ends
the wrong way round.

The numbers in `test_the_collision_this_module_exists_for` and
`test_offsets_are_per_block_and_the_sign_is_not_constant` are project
001's real measurements, kept as literals so the test fails if the
premise ever stops being true.
"""
import pytest
from library.tools.region import (
    DomainError,
    MASTER,
    Region,
    TimelineMismatch,
    SOURCE,
    TIMELINE,
    assert_domain,
    domain_of,
    parse,
    read_words,
    resolve,
    to_timeline_words,
)
from library.tools.spine_contract import (
    block_at,
    blocks_overlapping,
)
import os
import sys


def _block(position, timeline_start, timeline_end, clip_id="clip_001",
           source_start=0.0, words=None):
    """One spine block, contract-shaped."""
    duration = timeline_end - timeline_start
    return {
        "position": position,
        "block_type": "speech" if clip_id else "transition_slot",
        "clip_id": clip_id,
        "source_start": source_start if clip_id else None,
        "source_end": (source_start + duration) if clip_id else None,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "word_timestamps": words or [],
        "alignment_method": "whisperx" if clip_id else None,
    }


# Project 001's real spine, reduced to what an address needs.  Eight
# speech-bearing blocks, five non-speech, 56.605s.
_001 = [
    _block("hook", 0.000, 2.398, "clip_011", 0.836),
    _block(1, 2.398, 5.398, None),
    _block(2, 5.398, 8.380, "clip_011", 9.699),
    _block(3, 8.380, 18.427, "clip_017", 30.073),
    _block(4, 18.427, 21.927, None),
    _block(5, 21.927, 32.076, "clip_017", 45.441),
    _block(6, 32.076, 35.076, None),
    _block(7, 35.076, 38.616, "clip_011", 63.135),
    _block(8, 38.616, 41.322, "clip_011", 119.234),
    _block(9, 41.322, 44.322, None),
    _block(10, 44.322, 51.142, "clip_011", 173.639),
    _block(11, 51.142, 52.605, "clip_012", 33.941),
    _block(12, 52.605, 56.605, None),
]


# ── The conversions ──────────────────────────────────────────────────

def test_offsets_are_per_block_and_the_sign_is_not_constant():
    """The reason a conversion needs a block and not a project constant."""
    offsets = {b["position"]: round(b["source_start"] - b["timeline_start"], 3)
               for b in _001 if b["clip_id"] is not None}
    assert len(set(offsets.values())) == 8, offsets
    assert offsets["hook"] == 0.836
    assert offsets[10] == 129.317
    # Block 11 plays later on the timeline than blocks cut from a LATER
    # source second, so its offset runs the other way.  Any implementation
    # that assumes source >= timeline is wrong here.
    assert offsets[11] == -17.201
    assert min(offsets.values()) < 0 < max(offsets.values())


# ── blocks_overlapping ───────────────────────────────────────────────

def test_blocks_overlapping_is_half_open_and_never_clamps():
    """Abutting blocks partition the timeline; a boundary belongs to one.
    A zero-length interval returns its containing block, and past the
    end of the timeline is nothing rather than the last block."""
    touched = blocks_overlapping(_001, 2.398, 5.398)
    assert [b["position"] for b in touched] == [1]
    assert block_at(_001, 12.0)["position"] == 3
    assert [b["position"] for b in blocks_overlapping(_001, 12.0, 12.0)] == [3]
    assert blocks_overlapping(_001, 56.605, 60.0) == []
    assert block_at(_001, 100.0) is None


# ── Region ───────────────────────────────────────────────────────────

def test_region_refuses_a_reversed_or_negative_interval():
    with pytest.raises(ValueError) as exc:
        Region(MASTER, 10.0, 5.0)
    assert "precedes start" in str(exc.value)
    with pytest.raises(ValueError) as exc:
        Region(MASTER, -1.0, 5.0)
    assert "negative" in str(exc.value)


def test_region_clipped_to_a_block_never_exceeds_it():
    window = Region(MASTER, 45.0, 72.0).clipped_to(_001[10])
    assert (window.start, window.end) == (45.0, 51.142)


@pytest.mark.parametrize("bad", [""])
def test_parse_refuses_anything_it_would_have_to_guess_at(bad):
    with pytest.raises(ValueError):
        parse(bad)


# ── resolve ──────────────────────────────────────────────────────────

def test_resolve_turns_a_timestamp_into_footage():
    """The whole point: '45.0-72.0s' becomes clips and source seconds."""
    address = resolve(Region(MASTER, 45.0, 72.0), _001)
    assert address.positions == [10, 11, 12]
    assert address.clip_ids == ["clip_011", "clip_012"]
    spans = {s.block_position: (s.source_start, s.source_end)
             for s in address.source_spans}
    assert spans[10] == (174.317, 180.459)
    assert spans[11] == (33.941, 35.404)
    # Block 12 is 001's outro: inside the region, no clip behind it.
    assert 12 not in spans


# ── The domain guard ─────────────────────────────────────────────────

_INDEX_WORDS = [{"word": "i", "start": 0.836, "end": 0.872}]
_CAPTION_WORDS = [{"word": "i", "start": 0.000, "end": 0.036}]
_SPINE_WORDS = [{"word": "i", "source_start": 0.836, "source_end": 0.872}]


def test_the_collision_this_module_exists_for():
    """One spoken word, two artifacts, same keys, 0.836s apart on 001."""
    assert _INDEX_WORDS[0].keys() == _CAPTION_WORDS[0].keys()
    drift = _INDEX_WORDS[0]["start"] - _CAPTION_WORDS[0]["start"]
    assert drift == pytest.approx(0.836)
    assert drift == pytest.approx(
        _001[0]["source_start"] - _001[0]["timeline_start"])
    # Copying the key across puts 001's first caption 25 frames late.
    naive = _INDEX_WORDS[0]["start"]
    converted = to_timeline_words(
        read_words(_INDEX_WORDS, SOURCE), _001[0])[0]["timeline_start"]
    assert converted == pytest.approx(0.0)
    assert naive - converted == pytest.approx(0.836)
    assert round((naive - converted) * 30) == 25


def test_the_domain_guard_refuses_what_it_cannot_name():
    """The guard REFUSES; it cannot classify - bare start/end are
    identical in both domains. A list in the wrong named domain is
    refused, an already-named list is never relabelled, and there is no
    project-wide offset, so a conversion needs its block."""
    assert domain_of(_INDEX_WORDS) is None
    assert domain_of(_CAPTION_WORDS) is None
    for words in (_INDEX_WORDS, _CAPTION_WORDS):
        with pytest.raises(DomainError) as exc:
            assert_domain(words, SOURCE, "test")
        assert "name no time domain" in str(exc.value)
        assert "0.836" in str(exc.value)
    timeline_words = to_timeline_words(_SPINE_WORDS, _001[0])
    with pytest.raises(DomainError) as exc:
        assert_domain(timeline_words, SOURCE, "test")
    assert "expected source" in str(exc.value)
    with pytest.raises(DomainError):
        read_words(_SPINE_WORDS, TIMELINE)
    with pytest.raises(TypeError):
        to_timeline_words(_SPINE_WORDS)


# ── The timeline is part of the type ────────────────────────────
#
# Firstmate-decided 2026-09-05: `Region` carries WHICH TIMELINE, `Scope`
# carries WHAT SHAPE. A master-timeline region and a reel-timeline region
# are the domain collision above one level up - a reel is its keep ranges
# laid end to end, so reel second 12.0 and master second 12.0 are
# different moments and nothing about the numbers says so.


def test_a_region_knows_which_timeline_it_is_on():
    assert Region(MASTER, 1.0, 2.0).timeline is MASTER
    assert Region("reel_03", 1.0, 2.0).timeline == "reel_03"
    # "" is how `subtitle_segment_id.timeline_scope` reports an unnamed
    # timeline, and it must not become a timeline literally called "".
    assert Region("", 1.0, 2.0).timeline is MASTER
    assert Region("  ", 1.0, 2.0).timeline is MASTER
    # Identical numbers on two timelines are different moments.
    assert Region(MASTER, 12.0, 15.0) != Region("reel_03", 12.0, 15.0)


def test_mixing_timelines_is_refused_rather_than_converted():
    master = Region(MASTER, 45.0, 72.0)
    with pytest.raises(TimelineMismatch) as exc:
        resolve(master, _001, timeline="reel_03")
    assert "reel_03" in str(exc.value)
    # A text form that disagrees with its argument: one silent winner is
    # how a reel span gets read against the master.
    with pytest.raises(TimelineMismatch):
        parse("reel_03@45.0-72.0", timeline="reel_09")


def test_every_plan_splice_is_a_region_only_operation():
    """A splice redoes one region of a plan; offered a whole project it
    would silently replace every other region's decisions."""
    from library.tools import operations
    from library.tools import scope as scope_mod

    region = scope_mod.region(Region(None, 4.0, 6.0))
    for name, run in (("aroll.splice", "splice_region_aroll"),
                      ("broll.splice", "splice_region_broll"),
                      ("transitions.splice", "splice_region_transitions"),
                      ("vfx.splice", "splice_region_vfx")):
        op = operations.get(name)
        assert op.supports(region), name
        assert not op.supports(scope_mod.project()), name
        assert op.run.__name__ == run


# --------------------------------------------------------------------------
# From test_regional_motion.py
#
# Regional motion runs only in selected intervals and stays advisory.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import regional_motion as rm  # noqa: E402


def _vision(actions):
    return {"actions": [{"window": [0, 10], "actions": actions}]}


def _face_presence(boxes, rate=5):
    return {"sample_rate_hz": rate, "face_boxes": boxes}


def test_only_time_bounded_gemma_action_rows_select_spans():
    spans = rm.action_candidate_spans(_vision([
        {"start": 2, "end": 3, "action": "gestures with a hand",
         "body_language": "leans forward"},
        {"start": 3.02, "end": 4, "action": "points to the screen",
         "body_language": ""},
        {"start": 6, "end": 10, "action": "outside clip",
         "body_language": ""},
        {"action": "untimed is not a span"},
    ]), duration=6)

    assert spans == [{
        "start": 2.0,
        "end": 4.0,
        "selected_by": "gemma_action",
        "action_labels": [
            "gestures with a hand", "leans forward", "points to the screen",
        ],
    }]
    # Generic speech and static posture do not trigger a footage sweep.
    assert rm.action_candidate_spans(_vision([
        {"start": 0, "end": 10, "action": "person speaking to camera",
         "body_language": "seated, hands visible"},
    ]), duration=10) == []


def test_scene_boundaries_split_selected_spans_without_expanding_them():
    split = rm.split_at_scene_boundaries(
        [{"start": 2.0, "end": 5.0, "selected_by": "gemma_action",
          "action_labels": ["waves"]}],
        [{"time": 0.0, "type": "start"},
         {"time": 3.25, "type": "scene_change"}],
    )
    assert [(row["start"], row["end"]) for row in split] == [
        (2.0, 3.25), (3.25, 5.0)]


def test_face_box_uses_the_existing_5hz_track():
    boxes = [[0.2, 0.1, 0.4, 0.4], [0.4, 0.2, 0.6, 0.5], None]
    assert rm.face_box_at(_face_presence(boxes), 0.1) == pytest.approx(
        [0.3, 0.15, 0.5, 0.45])
    assert rm.face_box_at(_face_presence(boxes), 0.4) == boxes[1]
    assert rm.face_box_at(_face_presence(boxes), 0.6) is None


def test_moving_region_is_separate_from_the_face_and_in_crop_evidence():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    rng = np.random.default_rng(12)
    patch = rng.integers(0, 256, size=(56, 72), dtype=np.uint8)
    frames = []
    for index in range(12):
        frame = np.zeros((rm.SAMPLE_HEIGHT, rm.SAMPLE_WIDTH), dtype=np.uint8)
        x = 420 + index * 12
        frame[230:286, x:x + 72] = patch
        frames.append(frame)
    faces = _face_presence([[0.24, 0.16, 0.43, 0.46]] * 6)
    result = rm.analyze_frames(
        frames, 0.0, faces, 16 / 9,
        {"start": 0.0, "end": 1.2, "selected_by": "gemma_action",
         "action_labels": ["moves a hand across the frame"]},
    )

    assert result["face_track"]["observations"]
    assert result["motion_tracks"]
    motion = result["motion_tracks"][0]
    assert motion["kind"] == "hand_body_motion_candidate"
    assert motion["envelope"][0] > 0.6
    assert result["face_track"]["observations"][0]["box"][0] < 0.5
    assert motion["track_id"] in result["crop_suggestion"][
        "preserve_region_ids"]
    assert any(row["region_id"] == motion["track_id"]
               for row in result["keep_clear_suggestions"])
    assert result["crop_suggestion"]["recommendation"] == (
        "no_dynamic_crop_needed")


def test_only_a_face_trajectory_suggests_a_dynamic_crop():
    """A stable face says so explicitly; a one-sample face-box outlier
    does not trigger a crop; a real trajectory still can. The decision
    view's summary carries no per-sample trajectories."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")

    def analyze(n_frames, boxes, end, label):
        frames = [np.zeros((rm.SAMPLE_HEIGHT, rm.SAMPLE_WIDTH), dtype=np.uint8)
                  for _ in range(n_frames)]
        return rm.analyze_frames(
            frames, 0.0, _face_presence(boxes), 16 / 9,
            {"start": 0.0, "end": end, "selected_by": "gemma_action",
             "action_labels": [label]})

    stable = analyze(10, [[0.32, 0.14, 0.5, 0.42]] * 5, 1.0, "speaking")
    assert stable["crop_suggestion"]["recommendation"] == (
        "no_dynamic_crop_needed")
    assert stable["crop_suggestion"]["dynamic_crop_needed"] is False
    compact = rm.compact_span(stable)
    assert "observations" not in compact["face"]
    assert "observations" not in compact

    boxes = [[0.32, 0.14, 0.5, 0.42] for _ in range(20)]
    boxes[10] = [0.32, 0.38, 0.5, 0.66]
    outlier = analyze(40, boxes, 4.0, "gestures briefly")
    assert outlier["crop_suggestion"]["recommendation"] == (
        "no_dynamic_crop_needed")
    assert outlier["crop_suggestion"]["face_center_drift"] < 0.08

    moving = analyze(12, [[0.2 + index * 0.1, 0.14, 0.36 + index * 0.1, 0.42]
                          for index in range(6)],
                     1.2, "turns toward the speaker")
    assert moving["crop_suggestion"]["recommendation"] == (
        "consider_dynamic_crop")


def test_no_gemma_action_means_no_video_decode(monkeypatch):
    def fail_if_decoded(*args, **kwargs):
        raise AssertionError("unselected footage must not be decoded")

    monkeypatch.setattr(rm, "analyze_candidate_span", fail_if_decoded)
    result = rm.build_analysis(
        "/not/read/without/a/candidate.mxf", duration=30,
        semantic_document={"actions": []},
        face_presence={}, scene_boundaries=[],
    )
    assert result["measurement_status"] == "no_candidates"
    assert result["spans"] == []
    assert "not run over the footage" in result["reason"]


# --------------------------------------------------------------------------
# From test_sub_block_anchors.py
#
# Sub-block anchors: P5 lands within one frame, and unresolvable anchors refuse.
#
# Fidelity probe P5 ("punch 15% on 'quit', whoosh exactly on it"): "quit"
# is at timeline 11.44 s in the spine, the whoosh was placed at the block
# start, 3.06 s early, and the punch spanned the whole 10 s block - because
# no plan field anchored to a word, a beat or a frame.
#
# These tests drive the real post-bridges on a fixture spine with "quit"
# at timeline 11.44 s and assert both placements land within one frame of
# the word's start (stating the frame error), plus the refusal half: an
# anchor that names nothing placeable raises `AnchorRefused` - the
# `RenRefusal` shape the retry path hands back to the model - instead of
# falling back to the block start silently.

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

def test_p5_punch_builds_to_a_peak_then_releases_at_a_second_anchor():
    """Its ramps expand around independently anchored peak/release moments."""
    plan = [{
        "target_block_position": 1,
        "effect_type": "zoom_emphasis",
        "params": {"zoom_start": 1.0, "zoom_mid": 1.15,
                   "zoom_end": 1.0, "zoom_in_seconds": 0.67,
                   "zoom_out_seconds": 0.67},
        "rationale": "punch 15% on 'quit'",
        "anchor": {"word": "quit"},
        "anchor_end": {"word": "now"},
    }]
    resolved = resolve_vfx(plan, _spine(), FPS)
    assert len(resolved) == 1
    vfx = resolved[0]
    in_frames = seconds_to_frame(0.67, FPS)
    peak_frame = seconds_to_frame(QUIT_START, FPS)
    release_frame = seconds_to_frame(12.0, FPS)
    start_frame = peak_frame - in_frames
    end_boundary_frame = release_frame + in_frames + 1
    assert seconds_to_frame(vfx["timeline_start"], FPS) == start_frame
    assert seconds_to_frame(vfx["timeline_end"], FPS) == end_boundary_frame
    assert vfx["params"]["zoom_in_duration_frames"] == in_frames
    assert vfx["params"]["zoom_release_offset_frames"] == (
        release_frame - start_frame)
    assert vfx["params"]["zoom_out_duration_frames"] == in_frames
    frame_error = abs(seconds_to_frame(vfx["timeline_start"], FPS)
                      - start_frame)
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

def test_beat_and_frame_anchors_resolve_exactly():
    block = _spine()["structure"][0]
    music = dict(music_analysis=_grid(), music_selection=None)
    # Bar 2 opens at 10.37 s; its beats are 10.37/10.87/11.37/11.87.
    hit = resolve_anchor({"bar": 2, "beat": 3}, block=block, **music,
                         frame_rate=FPS, step="plan_sfx",
                         plan="sfx_creative", index=0)
    assert hit["timeline_seconds"] == pytest.approx(11.37, abs=1e-9)
    # A downbeat resolves to its bar's start.
    hit = resolve_anchor({"downbeat": 4}, block=block, **music,
                         frame_rate=FPS, step="plan_sfx",
                         plan="sfx_creative", index=0)
    assert hit["timeline_seconds"] == pytest.approx(14.37, abs=1e-9)
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
# One table, one row per refusal branch: a word the block
# does not say, an occurrence past its matches, a word anchor on a
# wordless block, a frame outside the block, a detected grid demanded
# of an estimated one, a beat anchor with no grid routed, a bar past
# the grid, two addresses in one anchor, an unread anchor sub-key, a
# non-dict anchor, and an offset pushing the moment outside the block.

def _block_2():
    return _spine()["structure"][0]


UNRESOLVABLE = [
    pytest.param({"word": "never"}, {},
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


def test_unresolvable_anchors_refuse():
    wrong = []
    for row in UNRESOLVABLE:
        anchor, tweak, match = row.values
        block = _block_2()
        if tweak.get("wordless"):
            block = dict(block, word_timestamps=[])
        music = {} if tweak.get("no_grid") else _grid(
            tweak.get("source", "detected"))
        try:
            resolve_anchor(anchor, block=block, music_analysis=music,
                           music_selection=None, frame_rate=FPS,
                           step="plan_sfx", plan="sfx_creative", index=0)
        except AnchorRefused as exc:
            if match not in str(exc):
                wrong.append((row.id, str(exc)))
        else:
            wrong.append((row.id, "resolved instead of refusing"))
    assert not wrong, wrong


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
                   "zoom_end": 1.0, "zoom_in_seconds": 0.67,
                   "zoom_out_seconds": 0.67},
        "rationale": "backwards span",
        "anchor": {"word": "now"},
        "anchor_end": {"word": "quit"},
    }]
    from library.tools.punch_timing import PunchTimingRefused
    with pytest.raises(PunchTimingRefused):
        resolve_vfx(vfx_plan, _spine(), FPS)


def test_refusal_is_the_ren_shape_with_a_fix():
    with pytest.raises(AnchorRefused) as excinfo:
        resolve_anchor({"word": "never"}, block=_block_2(),
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


def test_unresolvable_motion_anchors_refuse():
    """An occurrence past the peaks, an edge on a point, a grid on a
    motion anchor; no routed summaries or an unmeasured clip (refuse,
    never guess); and peaks the block's range does not contain."""
    far = [{"clip_id": "clip_001", "motion_method": "farneback",
            "motion_peaks": [
                {"time": 150.0, "kind": "apex", "magnitude": 0.9}]}]
    unmeasured = [{"clip_id": "clip_001", "motion_method": "unmeasured",
                   "motion_peaks": []}]
    rows = (
        ({"motion_peak": 9}, _motion(), "occurrence 9"),
        ({"action_onset": 1, "edge": "end"}, _motion(), "a peak is a point"),
        ({"motion_peak": 1, "grid": "detected"}, _motion(),
         "not motion ones"),
        ({"motion_peak": 1}, [], "no motion measurement is routed"),
        ({"motion_peak": 1}, unmeasured, "unmeasured"),
        ({"motion_peak": 1}, far, "no measured apex inside block"),
    )
    for anchor, indices, match in rows:
        with pytest.raises(AnchorRefused) as excinfo:
            resolve_anchor(anchor, block=_block_2(), temporal_indices=indices,
                           frame_rate=FPS, step="plan_vfx",
                           plan="vfx_creative", index=0)
        assert match in str(excinfo.value), anchor


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
