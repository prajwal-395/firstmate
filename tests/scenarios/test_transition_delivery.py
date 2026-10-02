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

#: The source frame every comp below is built at. The builder takes no
#: default frame, so each call states it - the same numbers the removed
#: default carried (see tests/unit/picture/test_vfx.py).
SOURCE_RES = (1080, 1920)


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
                        "duration_feel": "quick", "rationale": "beat hit"}],
        timed_spine=_spine((1, "clip_a"), (2, "clip_b")),
        music_selection={},
    )
    assert len(spec) == 1
    assert spec[0]["transition_type"] == "flash"
    assert spec[0]["duration_frames"] == 6, "the plan said 'quick'"
    assert spec[0]["requested_type"] == "flash"
    assert spec[0]["downgrade_reason"] == ""


def test_the_plans_own_pace_is_what_is_held(capsys):
    """How long a drawn transition holds is the PLAN's to say.

    This read the brand template's `transition_duration_ms` first and
    consulted `duration_feel` only if that came out under a frame, so on
    project 001 a "quick" defocus and a "medium" defocus were both held
    for 500 ms - the top of a range in a brand file 001 never selected.
    """
    plan = [{"cut_point_position": 2, "type": "defocus",
             "duration_feel": "quick"},
            {"cut_point_position": 3, "type": "defocus",
             "duration_feel": "medium"}]
    spine = _spine((1, "a"), (2, "b"), (3, "c"))
    spec = resolve_transitions(creative_plan=plan, timed_spine=spine,
                               music_selection={})
    assert [e["duration_frames"] for e in spec] == [6, 10]

    # And a brand that permits a RANGE bounds that choice without
    # replacing it: 200-500 ms is 6-15 frames, so both still stand.
    spec = resolve_transitions(
        creative_plan=plan, timed_spine=spine, music_selection={},
        brand_effect={"transition_duration_ms": {"min": 200, "max": 500}})
    assert [e["duration_frames"] for e in spec] == [6, 10]


def test_a_drawn_transition_with_no_declared_length_is_dropped(capsys):
    """Neither invented nor emitted at zero frames (AGENTS.md 10.5)."""
    spec = resolve_transitions(
        creative_plan=[{"cut_point_position": 2, "type": "defocus",
                        "rationale": "no feel declared"}],
        timed_spine=_spine((1, "clip_a"), (2, "clip_b")),
        music_selection={},
    )
    assert spec == []
    assert "duration_feel" in capsys.readouterr().err


def test_a_withdrawn_type_is_recorded_not_swapped():
    """It must not come back as some other creative transition, and the
    entry must say what happened - the rationale used to be carried
    through unchanged onto a transition it no longer described."""
    spec = resolve_transitions(
        creative_plan=[{"cut_point_position": 2, "type": "wipe",
                        "rationale": "graphic reveal"}],
        timed_spine=_spine((1, "clip_a"), (2, "clip_b")),
        music_selection={},
    )
    assert spec[0]["transition_type"] == "hard_cut"
    assert spec[0]["duration_frames"] == 0
    assert spec[0]["requested_type"] == "wipe"
    assert spec[0]["downgrade_reason"]


def test_a_granted_native_type_resolves_to_the_native_route():
    """`cross_dissolve` is drawn by Resolve itself (fidelity rung 3b),
    not downgraded to a hard cut."""
    spec = resolve_transitions(
        creative_plan=[{"cut_point_position": 2, "type": "cross_dissolve",
                        "duration_feel": "medium",
                        "rationale": "time passing"}],
        timed_spine=_spine((1, "clip_a"), (2, "clip_b")),
        music_selection={},
    )
    assert spec[0]["transition_type"] == "cross_dissolve"
    assert spec[0]["duration_frames"] == 10
    assert spec[0]["requested_type"] == "cross_dissolve"
    assert spec[0]["downgrade_reason"] == ""


def test_a_measured_refusal_reaches_the_post_bridge_as_a_refusal():
    """A whip refuses by name rather than shipping a hard cut."""
    from library.tools.native_ops import NativeTransitionRefused
    with pytest.raises(NativeTransitionRefused, match="whip_pan"):
        resolve_transitions(
            creative_plan=[{"cut_point_position": 2, "type": "whip_pan",
                            "rationale": "energy"}],
            timed_spine=_spine((1, "clip_a"), (2, "clip_b")),
            music_selection={},
        )


# ── 5.04 → renderer: the correctly-indexed list is the one that is read ──

def test_a_transition_becomes_real_nodes_in_the_comp():
    for ttype in FUSION_TYPES:
        for half in ("tail", "head"):
            comp = build_effect_comp(
                {f"{half}_transition": ttype, f"{half}_transition_frames": 10,
                 "vignette": False}, 90, source_res=SOURCE_RES)
            assert "Tools = {" in comp
            # More than just the MediaIn/MediaOut pair.
            assert comp.count("= ") > 4, (ttype, half, comp)


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
    # No transitions planned is a pass.
    assert verify_transitions(_FakeTimeline([]), None, []).passed
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

