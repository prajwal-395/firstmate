"""The drift detector finally has a caller, and the caller is pinned.

History: docs/evidence/resolve_test_history.md#test_drift_check.
"""
import ast
import inspect
import json
import pytest
from library.tools import drift_check, reel_build
from library.tools.drift_check import (
    compare_documents,
)
from tests.resolve_double import FakeTimeline, make_project
from library.tools.transform_drift import (
    TransformDriftError, built_transforms, describe, drift_rows,
    uniform_factor,
)
from library.tools import timeline_oracle as oracle


def doc(name, *clips):
    """A `timeline_serializer`-shaped document carrying `clips`.

    Each clip is `(track, record_in, clip_name, pan, tilt)`.
    """
    tracks = {}
    for track, start, clip_name, pan, tilt in clips:
        tracks.setdefault(track, []).append(
            {
                "record_in": start,
                "name": clip_name,
                "transform": {"Pan": pan, "Tilt": tilt, "ZoomX": 2.307},
            }
        )
    return {
        "metadata": {"name": name},
        "tracks": [
            {"type": "video", "index": index, "clips": clips_}
            for index, clips_ in sorted(tracks.items())
        ],
    }


#: Reel 26 as the 2026-09-17 measurement found it: the captain's
#: hand-set Pan -77.644 reading -38.822, the caption row at Tilt -864
#: reading -432, the emblem at 1167.568 reading 583.784.
BUILT = doc(
    "Reel 26",
    (1, 100, "speakerone.MXF", -77.644, -0.79),
    (4, 100, "caption.mov", 0.0, -864.0),
    (6, 100, "emblem.mov", 1167.568, 0.0),
)
HALVED = doc(
    "Reel 26",
    (1, 100, "speakerone.MXF", -38.822, -0.395),
    (4, 100, "caption.mov", 0.0, -432.0),
    (6, 100, "emblem.mov", 583.784, 0.0),
)


def test_the_measured_halving_reads_as_one_factor_of_a_half():
    """Seven live reels, 24-55 non-zero axes each, every one x0.5."""
    compared = compare_documents("Reel 26", BUILT, HALVED)
    assert compared["factor"] == pytest.approx(0.5)
    assert compared["moved"] == 3
    assert "every one by x0.5" in compared["line"]
    # Zero times anything is zero: the caption Pan, 0.0 built and live,
    # is undefined (never 1.0) and does not dilute the verdict.
    zero_rows = [row for row in compared["rows"] if row["built"]["Pan"] == 0.0]
    assert zero_rows and all(row["factors"]["Pan"] is None for row in zero_rows)


def test_snapshot_rounding_is_one_factor_not_a_second():
    """`0.7466` stored for a true `0.74655` reads 0.50002, not NO factor.

    The serializer rounds to 4 decimals, so a true uniform half lands
    at 0.49996-0.50004 on small values. Reporting that band as a
    second factor would re-dismiss the defect as a reading artifact -
    the exact sentence this task deletes from the docs.
    """
    built = doc("Reel 01", (1, 590, "LC4930.MXF", 1.4931, 0.25))
    live = doc("Reel 01", (1, 590, "LC4930.MXF", 0.7466, 0.125))
    compared = compare_documents("Reel 01", built, live)
    assert compared["factor"] == pytest.approx(0.5, abs=1e-3)
    assert "every one by x0.5" in compared["line"]
    assert "NO single factor" not in compared["line"]


def test_no_factor_is_invented_where_none_is_measured():
    """Where nothing can move, nothing moved - not x1.0. A replaced clip
    (same track and record frame, different name) is missing: matching
    on position alone would divide a stranger's transform by the
    build's. A non-uniform move is a DIFFERENT fault and must not borrow
    this one's name - the tolerance is for rounding, not for causes."""
    still = doc("Reel X", (1, 0, "still.mov", 0.0, 0.0))
    compared = compare_documents("Reel X", still, still)
    assert compared["factor"] is None
    assert "hold exactly what the build wrote" in compared["line"]

    compared = compare_documents(
        "Reel 01", doc("Reel 01", (1, 590, "LC4930.MXF", 1.493, 0.25)),
        doc("Reel 01", (1, 590, "LC4932.MXF", 5.972, 1.0)))
    assert compared["missing"] == 1
    assert compared["factor"] is None
    assert "no longer has" in compared["line"]

    built = doc(
        "Reel 01", (1, 590, "a.MXF", 10.0, 0.25), (1, 1069, "b.MXF", 10.0, 0.25)
    )
    live = doc("Reel 01", (1, 590, "a.MXF", 5.0, 0.125), (1, 1069, "b.MXF", 20.0, 0.5))
    compared = compare_documents("Reel 01", built, live)
    assert compared["factor"] is None
    assert "by NO single factor" in compared["line"]


# ── A factor across a project-resolution change is the UNIT ─────────


def _at(document, transform_unit_resolution):
    return {
        **document,
        "metadata": {
            **document["metadata"],
            "transform_unit_resolution": transform_unit_resolution,
        },
    }


#: geo-podcast Reel 01, 2026-10-02: the as-built snapshot (written under
#: the old project resolution) holds x4 what the correctly framed live
#: timeline stores. A repair that matched live to it broke 25 reels.
AS_BUILT_OLD_EPOCH = doc(
    "Reel 01",
    (1, 591, "LC4930.MXF", -20.54, -696.041),
    (3, 0, "tv_frame.mov", 0.0, -220.0),
)
LIVE_FRAMED = doc(
    "Reel 01",
    (1, 591, "LC4930.MXF", -5.135, -174.01025),
    (3, 0, "tv_frame.mov", 0.0, -55.0),
)


def test_a_factor_across_a_resolution_change_is_the_unit():
    """Across two project resolutions the factor is the unit, not a
    drift; a snapshot with no recorded epoch (every one before
    2026-10-02 - the records the broken repair trusted) cannot prove a
    drift; and a move within one epoch is still a drift."""
    compared = compare_documents(
        "Reel 01", _at(AS_BUILT_OLD_EPOCH, [1920, 1080]), _at(LIVE_FRAMED, [3840, 2160])
    )
    assert compared["factor"] == pytest.approx(0.25)
    assert compared["drifted"] is False
    assert "1920x1080" in compared["unit_epoch"]
    assert "never write a transform from this factor" in compared["line"]

    compared = compare_documents(
        "Reel 01", AS_BUILT_OLD_EPOCH, _at(LIVE_FRAMED, [3840, 2160])
    )
    assert compared["drifted"] is False
    assert "did not record the project resolution" in compared["unit_epoch"]
    assert "NOT a drift" in compared["line"]

    compared = compare_documents(
        "Reel 26", _at(BUILT, [3840, 2160]), _at(HALVED, [3840, 2160])
    )
    assert compared["unit_epoch"] == ""
    assert compared["drifted"] is True
    assert "NOT a drift" not in compared["line"]


def test_the_serializer_records_the_transform_unit_resolution():
    from library.tools.timeline_serializer import _transform_unit_resolution

    class _Project:
        def GetSetting(self, key):
            return {
                "timelineResolutionWidth": "3840",
                "timelineResolutionHeight": "2160",
            }[key]

    class _Resolve:
        def GetProjectManager(self):
            return self

        def GetCurrentProject(self):
            return _Project()

    assert _transform_unit_resolution(_Resolve()) == [3840, 2160]
    assert _transform_unit_resolution(None) is None


# ── The self-read: current for its own read, cursor back ─────────────


def _project_folder(tmp_path, resolve_name):
    root = tmp_path / "project"
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (root / "project.yaml").write_text(
        f"resolve:\n  project_name: {resolve_name}\n", encoding="utf-8"
    )
    (review / "Reel_26.timeline.json").write_text(json.dumps(BUILT), encoding="utf-8")
    return str(root)


def test_the_read_happens_with_the_reel_current_and_restores(
    tmp_path, monkeypatch, capsys
):
    """Load-bearing half: the live values are read while Reel 26 is
    current, and afterwards the cursor sits where the sweep found it."""
    reel = FakeTimeline("Reel 26", uid="uid-reel")
    other = FakeTimeline("GEO Podcast - Synced", uid="uid-master")
    project = make_project("field test", timelines=[other, reel], current=other)
    seen_current = []

    def fake_serialize(*args, **kwargs):
        seen_current.append(project.GetCurrentTimeline().GetName())
        return json.loads(json.dumps(HALVED))

    monkeypatch.setattr(
        "library.tools.timeline_serializer.serialize_timeline_state", fake_serialize
    )
    folder = _project_folder(tmp_path, "field test")
    report = drift_check.check_project(folder, project=project)

    assert seen_current == ["Reel 26"]
    assert project.GetCurrentTimeline().GetName() == "GEO Podcast - Synced"
    assert "entered on 'GEO Podcast - Synced', read back on " in report["cursor"]
    assert report["reels"]["Reel 26"]["factor"] == pytest.approx(0.5)
    out = capsys.readouterr().out
    assert "drift: Reel 26: 3 of 3 placement(s) moved since the build" in out


# ── The build runs it twice, and neither run can fail the build ──────

_SOURCE = inspect.getsource(reel_build)
_BODY = _SOURCE[_SOURCE.index("def rebuild_reels_in_project") :]


def _function(name):
    for node in ast.walk(ast.parse(_SOURCE)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not in reel_build")


def test_the_build_checks_drift_at_start_and_end():
    """One call brackets nothing: the trigger is caught only by a
    before AND an after. Two sites, named for which end they are."""
    assert _BODY.count('when="build start"') == 1
    assert _BODY.count('when="build end"') == 1


# --------------------------------------------------------------------------
# From test_transform_drift.py
#
# A built timeline that moved since its build is REPORTED, by factor.
#
# The measurement these pin is in `library/tools/transform_drift.py`: the
# project's "4x" is not a build-time defect - every affected reel's own
# build snapshot holds the engine's computed value and the live timeline
# holds a power of two times it. This module is the instrument that says
# so, so what it must not do is agree with a timeline that moved.

def snapshot(*clips):
    """A `timeline_serializer`-shaped document carrying `clips`."""
    tracks = {}
    for index, start, pan, tilt, name in clips:
        tracks.setdefault(index, []).append(
            {"record_in": start, "name": name,
             "transform": {"Pan": pan, "Tilt": tilt, "ZoomX": 2.307}})
    return {"metadata": {"name": "Reel 01 - a"},
            "tracks": [{"type": "video", "index": index, "clips": clips_}
                       for index, clips_ in sorted(tracks.items())]}


BUILT_2 = snapshot(
    (1, 590, 1.493, 0.25, "LC4930.MXF"),
    (1, 1069, 24.914, 0.25, "LC4932.MXF"),
    (2, 0, -29.651, 0.25, "LCATL0011.MXF"),
)


def held(*rows):
    return {(track, start): {"Pan": pan, "Tilt": tilt}
            for track, start, pan, tilt in rows}


def test_a_uniform_move_reads_as_one_factor_and_noise_as_none():
    """The x4 found on Reels 01, 23, 28, 30 and 31 reads as one factor."""
    rows = drift_rows(built_transforms(BUILT_2),
                      held((1, 590, 5.972, 1.0),
                           (1, 1069, 99.656, 1.0),
                           (2, 0, -118.604, 1.0)))
    assert [row["moved"] for row in rows] == [True, True, True]
    assert uniform_factor(rows) == pytest.approx(4.0)
    assert "every one by x4" in describe("Reel 01", rows)

    # Reel 26: built last, never moved. The instrument must agree.
    rows = drift_rows(built_transforms(BUILT_2),
                      held((1, 590, 1.493, 0.25),
                           (1, 1069, 24.914, 0.25),
                           (2, 0, -29.651, 0.25)))
    assert not any(row["moved"] for row in rows)
    assert uniform_factor(rows) is None
    assert "hold exactly what the build wrote" in describe("Reel 26", rows)

    # A built -35.0 reads back -35.000000000000036. That is not a move.
    doc = snapshot((1, 129, -35.0, 0.25, "LC4932.MXF"))
    rows = drift_rows(built_transforms(doc),
                      held((1, 129, -35.000000000000036, 0.25)))
    assert not rows[0]["moved"]


def test_a_non_uniform_drift_or_a_lost_placement_is_named_as_such():
    """A non-uniform drift is a DIFFERENT fault and must not borrow this
    one's name - the measured drift is uniform per timeline. A clip
    that went away is a bigger finding than one that moved, so it is a
    row, never a silent drop."""
    rows = drift_rows(built_transforms(BUILT_2),
                      held((1, 590, 5.972, 1.0),      # x4
                           (1, 1069, 49.828, 0.5),    # x2
                           (2, 0, -29.651, 0.25)))
    assert uniform_factor(rows) is None
    assert "by NO single factor" in describe("Reel 01", rows)

    rows = drift_rows(built_transforms(BUILT_2),
                      held((1, 590, 1.493, 0.25), (2, 0, -29.651, 0.25)))
    gone = [row for row in rows if row["missing"]]
    assert [(row["track"], row["start"]) for row in gone] == [(1, 1069)]
    assert "1 the timeline no longer has" in describe("Reel 01", rows)


def test_a_document_that_is_not_a_timeline_refuses():
    """An empty reading of an unreadable file is the silent pass."""
    with pytest.raises(TransformDriftError):
        built_transforms({"metadata": {"name": "Reel 01 - a"}})


def test_a_clip_carrying_no_transform_is_not_a_placement_to_judge():
    doc = {"metadata": {"name": "x"}, "tracks": [
        {"type": "video", "index": 4, "clips": [
            {"record_in": 0, "name": "sub_a.mov", "transform": {}},
            {"record_in": 9, "name": "sub_b.mov",
             "transform": {"Pan": 0.0, "Tilt": -864.0}}]}]}
    assert sorted(built_transforms(doc)) == [(4, 9)]


# --------------------------------------------------------------------------
# From test_timeline_oracle.py
#
# The timeline is the oracle: a hand edit reads as intent, never drift.
#
# History: docs/evidence/resolve_test_history.md#test_timeline_oracle.

class _Item:
    def __init__(self, name, start, end, unique_id=None):
        self._name = name
        self._start = start
        self._end = end
        self._uid = unique_id if unique_id is not None else f"uid-{name}-{start}"

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetUniqueId(self):
        return self._uid

    def GetMediaPoolItem(self):
        return None

    def GetProperty(self):
        return {}

    def GetMarkers(self):
        return {}


class _Timeline:
    def __init__(self, items_by_track, name="Reel 09 - moment"):
        self._tracks = items_by_track
        self._name = name

    def GetName(self):
        return self._name

    def GetTrackCount(self, track_type):
        return len(self._tracks.get(track_type, {}))

    def GetTrackName(self, track_type, index):
        return list(self._tracks.get(track_type, {}))[index - 1]

    def GetItemListInTrack(self, track_type, index):
        names = list(self._tracks.get(track_type, {}))
        return list(self._tracks[track_type][names[index - 1]])

    def SetCurrentTimeline(self, _timeline):  # pragma: no cover - the trap
        raise AssertionError("a read-only lane moved the cursor")


def _rows(timeline):
    return oracle.snapshot_live_rows(timeline)


def _v1(*items):
    return {"video": {"V1": list(items)}}


def test_an_untouched_timeline_reports_nothing_moved():
    expected = _rows(_Timeline(_v1(_Item("LC4930.MXF", 0, 684))))
    live = _rows(_Timeline(_v1(_Item("LC4930.MXF", 0, 684))))
    diff = oracle.describe_hand_edits(expected, live)
    assert diff["unchanged"] is True
    assert diff["intents"] == []
    evaluation = oracle.evaluate_precondition_against_live(
        "rough_cut_exists", expected, live)
    assert evaluation["satisfied_by_live_timeline"] is True
    assert evaluation["records_agree_with_live"] is True
    assert "Nothing moved" in oracle.render_report(evaluation)
    # 0 of 26 identities survive a rebuild: churned ids are not a rewrite.
    expected = _rows(_Timeline(_v1(
        _Item("LC4930.MXF", 0, 684, unique_id="uid-before-1"),
        _Item("LC4931.MXF", 684, 1200, unique_id="uid-before-2"),
    )))
    live = _rows(_Timeline(_v1(
        _Item("LC4930.MXF", 0, 684, unique_id="uid-after-1"),
        _Item("LC4931.MXF", 684, 1200, unique_id="uid-after-2"),
    )))
    diff = oracle.describe_hand_edits(expected, live)
    assert diff["unchanged"] is True
    assert diff["intents"] == []


def test_a_hand_trim_and_a_hand_placed_cutaway_read_as_intent():
    """The demonstration: what he did by hand, in his terms."""
    expected = _rows(_Timeline(_v1(
        _Item("LC4930.MXF", 0, 684),
        _Item("LC4931.MXF", 684, 1200),
    )))
    # He trimmed the head clip by hand and cut in a reaction.
    live = _rows(_Timeline(_v1(
        _Item("LC4930.MXF", 24, 684),
        _Item("LC4931.MXF", 684, 1200),
        _Item("SpeakerOne reaction.mov", 1200, 1271),
    )))
    diff = oracle.describe_hand_edits(expected, live)
    assert diff["unchanged"] is False
    sentences = " ".join(intent["sentence"] for intent in diff["intents"])
    assert "you " in sentences
    assert "carried as your intent" in sentences
    for banned in ("drift", "correct", "fix", "reconcile"):
        assert banned not in sentences.lower()
    report = oracle.render_report(
        oracle.evaluate_precondition_against_live(
            "rough_cut_exists", expected, live))
    assert "you " in report
    assert "drift" not in report.lower()
    assert "correct" not in report.lower()

    # A removed row is an intent, not a loss.
    expected = _rows(_Timeline({
        "video": {
            "V1": [_Item("LC4930.MXF", 0, 684)],
            "Semantic": [_Item("caption_001.mov", 0, 100)],
        },
    }))
    live = _rows(_Timeline(_v1(_Item("LC4930.MXF", 0, 684))))
    diff = oracle.describe_hand_edits(expected, live)
    assert "row_removed" in [intent["kind"] for intent in diff["intents"]]
    sentence = " ".join(intent["sentence"] for intent in diff["intents"])
    assert "you removed the video:Semantic row" in sentence


def test_the_live_timeline_overrules_records_claiming_no_cut():
    """Records say nothing; the screen shows a cut. The screen wins."""
    expected = _rows(_Timeline({"video": {"V1": []}}))
    live = _rows(_Timeline(_v1(_Item("LC4930.MXF", 0, 684))))
    evaluation = oracle.evaluate_precondition_against_live(
        "rough_cut_exists", expected, live)
    assert evaluation["expected_picture"] is False
    assert evaluation["live_picture"] is True
    assert evaluation["satisfied_by_live_timeline"] is True
    assert evaluation["records_agree_with_live"] is False
    assert "the timeline wins" in oracle.render_report(evaluation)

    # `state.verify_reels.reel_build` is the declared reading of picture:
    # it answers from the screen, including that an empty one is not.
    evaluation = oracle.evaluate_precondition_against_live(
        "state.verify_reels.reel_build", expected, live)
    assert evaluation["declared"] is True
    assert evaluation["basis"] == "live_timeline"
    assert evaluation["satisfied_by_live_timeline"] is True
    assert evaluation["records_agree_with_live"] is False
    assert "the timeline wins" in oracle.render_report(evaluation)
    evaluation = oracle.evaluate_precondition_against_live(
        "state.verify_reels.reel_build", {}, expected)
    assert evaluation["satisfied_by_live_timeline"] is False
    assert "not satisfied" in oracle.render_report(evaluation)


def test_an_unknown_precondition_raises_rather_than_answering():
    live = _rows(_Timeline(_v1(_Item("LC4930.MXF", 0, 684))))
    with pytest.raises(oracle.TimelineOracleError):
        oracle.evaluate_precondition_against_live("does_it_slap", {}, live)
    # A delegated refusal (no folder, no transcript) is UNSATISFIED and
    # reported - never a pass.
    evaluation = oracle.evaluate_precondition_against_live(
        "timeline_transcript.on_file", {}, {})
    assert evaluation["satisfied"] is False
    assert evaluation["missing"] == "timeline_transcript"
    report = oracle.render_report(evaluation)
    assert "timeline_transcript.on_file" in report
    assert "does not hold" in report
    assert "Missing: timeline_transcript" in report


def test_exact_names_win_and_prefixes_refuse():
    class _Project:
        def __init__(self, timelines):
            self._timelines = timelines

        def GetTimelineCount(self):
            return len(self._timelines)

        def GetTimelineByIndex(self, index):
            return self._timelines[index - 1]

    reel = _Timeline(_v1(), name="Reel 09 - moment")
    sibling = _Timeline(_v1(), name="Reel 09 - moment (staging)")
    project = _Project([reel, sibling])
    assert oracle.find_timeline_exact(project, "Reel 09 - moment") is reel
    with pytest.raises(oracle.TimelineNotFound):
        oracle.find_timeline_exact(project, "Reel 09")


def test_no_writer_or_cursor_move_lives_in_this_module():
    from pathlib import Path

    text = Path(oracle.__file__).read_text(encoding="utf-8")
    # Call forms, not prose: the docstring names the ban
    # (`SetCurrentTimeline` without parens); the code must never CALL it.
    for banned_call in (".SetCurrentTimeline(", ".GetUniqueId(",
                        ".DeleteClips(", ".AddMarker(",
                        "ImportFusionComp("):
        assert banned_call not in text
    # The one hard form of the ban: the code never keys an item on its
    # Resolve identity - identity is name plus span, in the diff.
    assert '["unique_id"]' not in text
    assert "['unique_id']" not in text
