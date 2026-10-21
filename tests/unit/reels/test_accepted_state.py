"""VC-003: the accepted grades, Fusion comps, transforms and sound.

THE DEFECTS THESE NAME
----------------------
Four places where the engine could not see work the captain had
already approved, and each of them is a place a comparison SHOULD
have been able to see it:

1. The VERSION RECORD - the committed `.timeline.json`
   (`library/tools/timeline_serializer.py`) - reduced a clip's Fusion
   comps to a COUNT and a list of NAMES, kept only the CDL of the
   grade, and carried no keyframes at all.  A version whose comp had
   been re-authored on another canvas, or whose grade's nodes had been
   replaced, or whose keyframes had gone, diffed as the same reel.

2. The RECORDED-STATE COMPARISON (`timeline_shadow.diff`) compared two
   recorded generations by their ROWS and their markers, so a
   generation that arrived with a different grade on a shot read as
   the same timeline with a different cut.

3. The UNDO's own verification (`undo_journal.projection`) carried the
   transform and the CDL, and carried no keyframes, no comp canvas and
   no sound - so a captain who hand-moved a keyframe or a level after a
   touch was read as "the timeline has not moved", and the undo
   reversed straight over the work.

4. Nothing compared the CANVAS between two states.  Reel 26's closing
   shot carried the same comp names as its neighbours and was drawn on
   the source canvas, so its vignette and glow landed on a different
   picture (QA-005); and a reel whose DELIVERY canvas changed re-frames
   every comp in it without one comp changing.

The invariant these build: an accepted grade, Fusion comp, transform,
keyframe and level may only change when the request authorizes it, and
every comparison of two states says what moved - with the
DENOMINATOR, so "nothing changed" and "nothing of the forty accepted
items changed" are different claims.
"""

from __future__ import annotations

import pytest

from library.tools import accepted_state as acc
from library.tools import look_preservation as look
from library.tools import preservation_invariant
from library.tools import undo_journal

# ── the recorded state, in the shape `reel_read` writes it ──────────────

def _clip(uid="uid-1", *, name="shot", row=1, record_in=0, record_out=48,
          cdl=None, node_count=1, luts=None, nodes=None,
          canvas=(1080, 1920), tools=("Background", "EllipseMask", "Merge",
                                      "MediaIn1", "MediaOut1"),
          keyframes=None, volume=None, fades=None, tilt=0.25,
          linked=None):
    """One recorded clip, in the shape `reel_read.clip_detail` writes."""
    return {
        "unique_id": uid,
        "name": name,
        "item_type": "video",
        "track_type": "video",
        "track_index": row,
        "track_name": "V1",
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "source_in_frame": 0,
        "source_out_frame": 48,
        "left_offset": 0,
        "right_offset": 48,
        "source_file": f"/footage/{name}.mov",
        "media_type": "video",
        "media_pool_item_id": f"media-{uid}",
        "enabled": True,
        "clip_color": "",
        "flags": [],
        "speed": 100.0,
        "volume": volume,
        "fades": fades if fades is not None else {"FadeIn": 0.0,
                                                  "FadeOut": 0.0},
        "linked_clips": linked if linked is not None else [
            {"enabled": True, "fades": {"FadeIn": 0.0, "FadeOut": 0.0},
             "name": name, "record_in": record_in, "record_out": record_out,
             "source_in_frame": 0, "source_out_frame": 48,
             "track_index": 1, "track_type": "audio",
             "unique_id": f"audio-{uid}"}],
        "transform": {"Pan": 0.0, "Tilt": tilt, "ZoomX": 1.0, "ZoomY": 1.0,
                      "ZoomGang": 1.0, "RotationAngle": 0.0,
                      "AnchorPointX": 0.5, "AnchorPointY": 0.5},
        "keyframes": keyframes if keyframes is not None else {},
        "fusion": {
            "comp_count": 1,
            "comp_names": ["MediaIn1"],
            "comps": [{
                "comp_index": 1,
                "name": "MediaIn1",
                "tools": list(tools),
                "canvas": ({"width": canvas[0], "height": canvas[1]}
                           if canvas else None),
            }],
            "media_windows": [],
            "mattes": [],
        },
        "color": {
            "cdl": cdl if cdl is not None else {
                "NodeIndex": 1, "Slope": [1.0, 1.0, 1.0],
                "Offset": [0.0, 0.0, 0.0], "Power": [1.0, 1.0, 1.0],
                "Saturation": 1.0},
            "color_group": "",
            "node_count": node_count,
            "luts": luts if luts is not None else [],
            "nodes": nodes if nodes is not None else [
                {"node": 1, "label": "Node 1", "group": None}],
        },
        "markers": [],
    }


def _state(*clips, width=1080, height=1920, timeline="Reel 13"):
    """A recorded state, in the shape `reel_read.read_reel` writes."""
    return {
        "timeline": timeline,
        "project": "Ren Scratch - breaktest",
        "width": width,
        "height": height,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": list(clips)}],
        "markers": {"timeline": [], "notes": []},
    }


def _scope(*operations):
    return preservation_invariant.scope_for(list(operations))


# ── 1. the version record ────────────────────────────────────────────────

class _FakeTimeline:
    """A timeline that answers the serializer's questions."""

    def __init__(self, clips):
        self._clips = clips

    def GetName(self):
        return "Reel 13 - breaktest-selected"

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return 1920

    def GetStartTimecode(self):
        return "01:00:00:00"

    def GetSetting(self, name):
        return {"timelineFrameRate": "23.976",
                "timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920"}.get(name, "")

    def GetMarkers(self):
        return {}

    def GetTrackCount(self, kind):
        return 1 if kind == "video" else 0

    def GetTrackName(self, kind, index):
        return "Akshita"

    def GetItemListInTrack(self, kind, index):
        return list(self._clips) if kind == "video" else []


class _FakeProject:
    def GetSetting(self, name):
        return {"timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920"}.get(name, "")


class _FakeManager:
    def GetCurrentProject(self):
        return _FakeProject()


class _FakeResolve:
    def GetProjectManager(self):
        return _FakeManager()


def test_the_version_record_carries_the_grade_the_comps_and_the_keyframes(
        monkeypatch):
    """The version record keeps what a comparison needs to see.

    The defect: the committed `.timeline.json` reduced a comp to a
    COUNT and a list of NAMES, kept only the CDL of the grade, and
    carried no keyframes - so two versions that differed in the comp's
    canvas, in the grade's nodes or in the shot's motion diffed as the
    same reel.
    """
    from library.tools import reel_read, timeline_serializer

    detail = _clip(keyframes={"Tilt": [{"frame": 0, "value": 0.25,
                                         "interpolation": "linear"}]},
                    node_count=8, luts=["TheGradeFree"],
                    nodes=[{"node": 1, "label": "BAL/EXP", "group": None}])
    monkeypatch.setattr(reel_read, "clip_detail",
                        lambda item, *args, **kwargs: detail)
    record = timeline_serializer.serialize_timeline_state.__wrapped__(
        resolve_mock=_FakeResolve(), timeline=_FakeTimeline([object()]))

    clip = record["tracks"][0]["clips"][0]
    assert clip["color"]["node_count"] == 8
    assert clip["color"]["luts"] == ["TheGradeFree"]
    assert clip["color"]["nodes"][0]["label"] == "BAL/EXP"
    assert clip["fusion"]["comps"][0]["canvas"] == {"width": 1080,
                                                    "height": 1920}
    assert clip["fusion"]["comps"][0]["tools"]
    assert clip["keyframes"]["Tilt"][0]["value"] == 0.25
    assert clip["fades"] == {"FadeIn": 0.0, "FadeOut": 0.0}


# ── 2. the comparison of two states ──────────────────────────────────────

def test_a_grade_that_moved_is_an_accepted_item_that_moved():
    """The accepted state says the grade moved, and how many items it read."""
    before = _state(_clip(cdl={"NodeIndex": 1, "Slope": [1.0, 1.0, 1.0],
                               "Offset": [0.0, 0.0, 0.0],
                               "Power": [1.0, 1.0, 1.0],
                               "Saturation": 1.0}))
    after = _state(_clip(cdl={"NodeIndex": 1, "Slope": [1.2, 1.0, 1.0],
                              "Offset": [0.0, 0.0, 0.0],
                              "Power": [1.0, 1.0, 1.0],
                              "Saturation": 1.0}))

    report = acc.verify(before, after)

    assert report["status"] == acc.UNPROVABLE
    assert report["counts"]["changed"] == 1
    assert report["counts"]["accepted_items"] >= 1
    assert any(row["part"] == "grade" for row in report["findings"])


def test_the_counts_carry_the_denominator():
    """The report says how many accepted items it compared."""
    clips = [_clip(uid=f"uid-{index}", name=f"shot-{index}")
             for index in range(3)]
    before = _state(*clips)
    after = _state(*clips)

    report = acc.verify(before, after)

    assert report["counts"]["shots"] == 3
    assert report["counts"]["accepted_items"] >= 3
    assert report["counts"]["changed"] == 0
    assert report["status"] == acc.PRESERVED


def test_a_comp_re_authored_on_another_canvas_is_named():
    """Reel 26's signature: the same comp names, another canvas."""
    before = _state(_clip(canvas=(1080, 1920)))
    after = _state(_clip(canvas=(3840, 2160)))

    report = acc.verify(before, after)

    kinds = {row["kind"] for row in report["canvas"]}
    assert "comp_canvas_changed" in kinds
    assert report["status"] != acc.PRESERVED


def test_a_delivery_canvas_change_is_named():
    """A reel that delivers another canvas re-frames every comp in it."""
    before = _state(_clip(), width=1080, height=1920)
    after = _state(_clip(), width=1080, height=1080)

    report = acc.verify(before, after)

    kinds = {row["kind"] for row in report["canvas"]}
    assert "delivery_canvas_changed" in kinds


def test_a_keyframe_that_moved_is_an_accepted_item_that_moved():
    """The motion of a shot is part of what was accepted."""
    before = _state(_clip(keyframes={"Tilt": [{"frame": 0, "value": 0.25}]}))
    after = _state(_clip(keyframes={"Tilt": [{"frame": 0, "value": -0.5}]}))

    report = acc.verify(before, after)

    assert any(row["part"] == "keyframes" for row in report["findings"])


def test_the_sound_that_moved_is_an_accepted_item_that_moved():
    """The level of a shot is part of what was accepted."""
    before = _state(_clip(volume=0.0))
    after = _state(_clip(volume=-12.0))

    report = acc.verify(before, after)

    assert any(row["part"] == "sound" for row in report["findings"])


def test_a_transform_that_moved_is_an_accepted_item_that_moved():
    """The framing of a shot is part of what was accepted."""
    before = _state(_clip(tilt=0.25))
    after = _state(_clip(tilt=-0.5))

    report = acc.verify(before, after)

    assert any(row["part"] == "transform" for row in report["findings"])


# ── 3. the invariant ─────────────────────────────────────────────────────

def test_the_invariant_refuses_a_change_nothing_authorized():
    """An accepted item may only change when the request says so.

    The scope here authorizes a TRIM of the shot - a change of its
    timing - and says nothing about its framing, so a framing that
    moved is unauthorized and the invariant refuses.
    """
    before = _state(_clip(uid="uid-1", tilt=0.25))
    after = _state(_clip(uid="uid-1", tilt=-0.5))
    scope = _scope({"op": "clip.trim", "unique_id": "uid-1"})

    with pytest.raises(acc.AcceptedStateMoved):
        acc.require_preserved(before, after, scope=scope)


def test_the_invariant_fails_closed_on_a_difference_nobody_can_judge():
    """A difference nobody can judge is not a pass."""
    before = _state(_clip(tilt=0.25))
    after = _state(_clip(tilt=-0.5))

    with pytest.raises(acc.AcceptedStateUnprovable):
        acc.require_preserved(before, after)


def test_the_invariant_accepts_a_change_the_request_authorized():
    """A request that says what it changes may change it."""
    before = _state(_clip(uid="uid-1", tilt=0.25))
    after = _state(_clip(uid="uid-1", tilt=-0.5))
    scope = _scope({"op": "clip.set_property", "unique_id": "uid-1",
                    "property": "Tilt", "value": -0.5})

    report = acc.require_preserved(before, after, scope=scope)

    assert report["status"] == acc.PRESERVED


def test_a_clean_revision_passes_the_invariant():
    """A revision that changed nothing about the accepted state passes."""
    state = _state(_clip())

    report = acc.require_preserved(state, state)

    assert report["status"] == acc.PRESERVED


# ── 4. the undo's own verification ───────────────────────────────────────

def test_the_undo_projection_is_blind_to_the_keyframe_that_moved():
    """The defect the undo's check could not see.

    The projection carries the transform and the CDL.  It carries no
    keyframes, no comp canvas and no sound, so a captain who hand-moved
    a keyframe after a touch was read as "the timeline has not moved",
    and the undo reversed straight over the work.
    """
    before = _clip(keyframes={"Tilt": [{"frame": 0, "value": 0.25}]})
    after = _clip(keyframes={"Tilt": [{"frame": 0, "value": -0.5}]})

    moved = undo_journal.projection_diff(
        undo_journal.projection([{"type": "video", "index": 1,
                                  "name": "V1", "clips": [after]}]),
        undo_journal.projection([{"type": "video", "index": 1,
                                  "name": "V1", "clips": [before]}]))

    assert moved == [], ("the projection reports the timeline as unchanged"
                         " although the keyframe moved")
    assert acc.moved_accepted_items({"tracks": [{"type": "video", "index": 1,
                                                  "name": "V1",
                                                  "clips": [before]}]},
                                     {"tracks": [{"type": "video", "index": 1,
                                                  "name": "V1",
                                                  "clips": [after]}]})


# ── 5. the recorded-state comparison ─────────────────────────────────────

def test_the_recorded_state_comparison_carries_the_accepted_state():
    """The defect: a row diff cannot see a grade.

    `timeline_shadow.diff` compared two recorded generations by their
    rows and their markers, so a generation that arrived with a
    different grade on a shot read as the same timeline.
    """
    from library.tools import timeline_shadow

    before = _state(_clip(tilt=0.25))
    after = _state(_clip(tilt=-0.5))

    report = timeline_shadow.diff(before, after)

    assert "accepted" in report
    assert report["accepted"]["counts"]["changed"] >= 1
    assert any(row["part"] == "transform"
               for row in report["accepted"]["findings"])


def test_the_version_comparison_carries_the_accepted_state():
    """A round diff says what moved in the LOOK, not only in the cut."""
    from library.tools.versions import rounds

    before = acc.capture(_state(_clip(tilt=0.25)))
    after = acc.capture(_state(_clip(tilt=-0.5)))

    report = rounds.diff_reel({}, {},
                              earlier_accepted=before, later_accepted=after)

    assert report["accepted"]["counts"]["changed"] >= 1
    assert any(row["part"] == "transform"
               for row in report["accepted"]["findings"])


def test_a_version_comparison_without_an_accepted_state_says_so():
    """An absent measurement is not a pass, and not a failure either."""
    from library.tools.versions import rounds

    report = rounds.diff_reel({}, {})

    assert "accepted" not in report


# ── 7. the restore of a generation ───────────────────────────────────────

def test_the_restore_reports_what_it_changes_in_the_accepted_state():
    """Restoring a generation replaces the approved work with it.

    The defect: the restore compared the ROWS of the live timeline with
    the version it was replacing, and a generation's ROWS carry no
    grade, no comp, no keyframe and no level - so a restore that
    replaced an approved grade with an older one was invisible.
    """
    from library.tools import undo_journal

    current = {"accepted": acc.capture(_state(_clip(tilt=0.25)))}
    target = {"accepted": acc.capture(_state(_clip(tilt=-0.5)))}

    report = undo_journal._accepted_change(current, target)

    assert report["counts"]["changed"] == 1
    assert any(row["part"] == "transform" for row in report["findings"])


def test_a_restore_of_a_version_with_no_accepted_state_says_so():
    """A version recorded before the accepted state was kept carries none."""
    from library.tools import undo_journal

    report = undo_journal._accepted_change({}, {})

    assert report["status"] == "unmeasured"


# ── 6. the look's own vocabulary ─────────────────────────────────────────

def test_the_accepted_parts_are_the_look_parts():
    """One enumeration: the accepted state names QA-005's parts."""
    assert acc.ACCEPTED_PARTS == look.LOOK_PARTS
    assert "sound" in acc.ACCEPTED_PARTS
