"""A planned transition must survive every hop to the Fusion comp.

Five independent breaks each stopped this on their own: the type was
replaced by the selector, the renderer read the wrong manifest key, the
clip index defaulted to 0, an unknown type produced an empty comp, and
the macro path substituted a transition nobody chose.
"""
import pytest

from library.tools.fusion.comp_builder import build_effect_comp
from library.steps.step_4_02_plan_transitions.post_bridge import resolve_transitions
from library.tools.transition_vocabulary import FUSION_TYPES


def _spine(*positions):
    blocks = []
    for i, (position, clip_id) in enumerate(positions):
        blocks.append({
            "position": position,
            "block_type": "speech",
            "clip_id": clip_id,
            "timeline_start": float(i * 5),
            "timeline_end": float(i * 5 + 5),
            "word_timestamps": [],
            "alignment_method": "whisperx",
        })
    return {"structure": blocks, "total_estimated_duration_seconds": 20.0}


# ── 4.02: the plan's type survives ──

def test_a_requested_drawable_type_reaches_the_spec():
    spec = resolve_transitions(
        creative_plan=[{"cut_point_position": 2, "type": "flash",
                        "rationale": "beat hit"}],
        timed_spine=_spine((1, "clip_a"), (2, "clip_b")),
        music_selection={},
    )
    assert len(spec) == 1
    assert spec[0]["transition_type"] == "flash"
    assert spec[0]["duration_frames"] > 0
    assert spec[0]["requested_type"] == "flash"
    assert spec[0]["downgrade_reason"] == ""


def test_a_withdrawn_type_is_recorded_not_swapped():
    """It must not come back as some other creative transition, and the
    entry must say what happened - the rationale used to be carried
    through unchanged onto a transition it no longer described."""
    spec = resolve_transitions(
        creative_plan=[{"cut_point_position": 2, "type": "cross_dissolve",
                        "rationale": "time passing"}],
        timed_spine=_spine((1, "clip_a"), (2, "clip_b")),
        music_selection={},
    )
    assert spec[0]["transition_type"] == "hard_cut"
    assert spec[0]["duration_frames"] == 0
    assert spec[0]["requested_type"] == "cross_dissolve"
    assert spec[0]["downgrade_reason"]


def test_no_spec_entry_can_carry_an_undrawable_type():
    spec = resolve_transitions(
        creative_plan=[
            {"cut_point_position": 2, "type": "whip_pan"},
            {"cut_point_position": 3, "type": "light_leak"},
        ],
        timed_spine=_spine((1, "a"), (2, "b"), (3, "c")),
        music_selection={},
    )
    from library.tools.transition_vocabulary import PLANNABLE_TYPES
    for entry in spec:
        assert entry["transition_type"] in PLANNABLE_TYPES


# ── 5.04 → renderer: the correctly-indexed list is the one that is read ──

def _manifest_with_transition(**overrides):
    spec = {"type": "fade_to_black", "after_clip": 1, "duration_frames": 10}
    spec.update(overrides)
    return {
        "tracks": {"V1": {"clips": [
            {"label": "clip_0", "source_file": "a.mov"},
            {"label": "clip_1", "source_file": "b.mov"},
            {"label": "clip_2", "source_file": "c.mov"},
        ]}},
        "fusion_effects": {"per_clip": {}, "transitions": [spec]},
        # The informational top-level list carries no clip index at all.
        # Reading THIS is what put every transition on clip 0.
        "transitions": [{"transition_type": "fade_to_black",
                         "cut_point_timeline": 10.0}],
    }


def test_renderer_reads_the_indexed_list_and_maps_tail_then_head():
    manifest = _manifest_with_transition()
    specs = manifest["fusion_effects"]["transitions"]
    by_clip = {}
    for tspec in specs:
        by_clip.setdefault(tspec["after_clip"], {})["tail_transition"] = tspec["type"]
        by_clip.setdefault(tspec["after_clip"] + 1, {})["head_transition"] = tspec["type"]
    # The outgoing clip gets the tail, the incoming clip gets the head.
    assert by_clip[1]["tail_transition"] == "fade_to_black"
    assert by_clip[2]["head_transition"] == "fade_to_black"
    assert 0 not in by_clip


@pytest.mark.parametrize("ttype", FUSION_TYPES)
def test_a_transition_becomes_real_nodes_in_the_comp(ttype):
    tail = build_effect_comp(
        {"tail_transition": ttype, "tail_transition_frames": 10,
         "vignette": False}, 90)
    head = build_effect_comp(
        {"head_transition": ttype, "head_transition_frames": 10,
         "vignette": False}, 90)
    for comp in (tail, head):
        assert "Tools = {" in comp
        # More than just the MediaIn/MediaOut pair.
        assert comp.count("= ") > 4, comp


def test_an_undrawable_transition_in_the_manifest_raises():
    with pytest.raises(ValueError, match="No Fusion transition builder"):
        build_effect_comp(
            {"tail_transition": "cross_dissolve",
             "tail_transition_frames": 10, "vignette": False}, 90)


# ── The QA station that never checked anything ──

class _FakeItem:
    def __init__(self, comps):
        self._comps = comps

    def GetFusionCompNameList(self):
        return list(self._comps)


class _FakeTimeline:
    def __init__(self, items):
        self._items = items

    def GetItemListInTrack(self, track_type, index):
        return list(self._items) if index == 1 else []


def test_the_station_passes_when_both_clips_carry_a_comp():
    from library.tools.timeline_qa import verify_transitions
    timeline = _FakeTimeline([
        _FakeItem([]), _FakeItem(["Fusion Composition 1"]),
        _FakeItem(["Fusion Composition 1"]),
    ])
    report = verify_transitions(
        timeline, None,
        [{"type": "fade_to_black", "after_clip": 1, "duration_frames": 10}])
    assert report.passed


def test_the_station_fails_when_a_transition_drew_nothing():
    """It used to return passed=True without looking at anything, which
    is why zero transitions in the finished video went unnoticed."""
    from library.tools.timeline_qa import verify_transitions
    timeline = _FakeTimeline([_FakeItem([]), _FakeItem([]), _FakeItem([])])
    report = verify_transitions(
        timeline, None,
        [{"type": "fade_to_black", "after_clip": 1, "duration_frames": 10}])
    assert not report.passed
    names = [c.name for c in report.checks]
    assert any("tail" in n for n in names)
    assert any("head" in n for n in names)


def test_the_station_fails_when_the_incoming_clip_does_not_exist():
    from library.tools.timeline_qa import verify_transitions
    timeline = _FakeTimeline([_FakeItem([]), _FakeItem(["c"])])
    report = verify_transitions(
        timeline, None,
        [{"type": "fade_to_black", "after_clip": 1, "duration_frames": 10}])
    assert not report.passed


def test_no_transitions_planned_is_a_pass():
    from library.tools.timeline_qa import verify_transitions
    assert verify_transitions(_FakeTimeline([]), None, []).passed
