"""The timeline is the oracle: a hand edit reads as intent, never drift.

History: docs/evidence/resolve_test_history.md#test_timeline_oracle.
"""


import pytest

from library.tools import timeline_oracle as oracle


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
        _Item("Akshita reaction.mov", 1200, 1271),
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
