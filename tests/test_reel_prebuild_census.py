"""Measure before you build: the census runs first, prints, never refuses.

The 2026-09-11 round decided the caption row from a census that
existed only after a four-reel build, a refusal and a discard: four
reels at tilt -425..-436 against one at -870. The pre-build census
reads that same state before anything is placed - same kind of
element, materially different places - prints it, and lets the build
proceed either way.

Proven here three ways: the round's own numbers flag (and agreement
does not), unreadable and fresh reels degrade to notes rather than
refusals, and the rebuild calls the census on a multi-reel build.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from library.tools import reel_prebuild_census as census

REELS = ["Reel 01 - hook (final)", "Reel 23 - pricing (final)",
         "Reel 30 - seam (final)", "Reel 31 - phrase (final)",
         "Reel 28 - closer (final)"]


def _clip(row, tilt, pan=0.0):
    return SimpleNamespace(track_type="video", track_name=row,
                           transform={"Pan": pan, "Tilt": tilt})


def _per_reel(tilts, row="Subtitles"):
    """Five reels' caption rows at the given median tilts."""
    return {final: {row: {"pan": 0.0, "tilt": tilt, "n": 4}}
            for final, tilt in zip(REELS, tilts)}


def test_the_round_s_numbers_disagree(capsys):
    """Four reels at -425..-436 against one at -870: flagged, by row."""
    report = census.compare_reel_rows(
        _per_reel([-425.0, -430.0, -433.0, -436.0, -870.0]))

    assert report["disagreements"] == ["Subtitles"]
    assert report["rows"]["Subtitles"]["disagree"] is True
    text = census.render_census(report, REELS)
    assert "DISAGREES" in text and "Subtitles" in text
    print(text)


def test_characteristics_read_the_median_not_one_card():
    """One odd card must not move a reel: the row's characteristic is
    the median over its readable clips."""
    clips = [_clip("Subtitles", -430.0), _clip("Subtitles", -432.0),
             _clip("Subtitles", -870.0)]
    assert census.row_characteristics(clips)["Subtitles"]["tilt"] == -432.0


def test_picture_and_broll_rows_are_not_compared():
    """A-roll conforms and b-roll cutaways legitimately differ per
    reel - comparing them would flag every build, so the read drops
    them before anything is compared (even an 800-unit spread)."""
    per_reel = {
        REELS[0]: census.row_characteristics(
            [_clip("Craig", -100.0), _clip("Craig", -102.0),
             _clip("B-Roll", 500.0)]),
        REELS[1]: census.row_characteristics(
            [_clip("Craig", -900.0), _clip("Craig", -902.0),
             _clip("B-Roll", -500.0)]),
    }
    report = census.compare_reel_rows(per_reel)

    assert report["disagreements"] == []
    assert report["rows"] == {}


def test_unreadable_and_fresh_reels_become_notes_not_refusals(capsys):
    """A timeline that will not read, and a final with no timeline
    yet, take no part in the comparison - and the build still gets
    its table."""
    good = SimpleNamespace(
        clips=[_clip("Subtitles", -430.0), _clip("Subtitles", -432.0)])

    class Project:
        def GetName(self):
            return "Mock Project"

        def GetTimelineCount(self):
            return 2

        def GetTimelineByIndex(self, index):
            return [SimpleNamespace(GetName=lambda: REELS[0]),
                    SimpleNamespace(GetName=lambda: REELS[1])][index - 1]

    def snapshot_fn(timeline, _project):
        if timeline.GetName() == REELS[0]:
            raise RuntimeError("Resolve is busy")
        return good

    report = census.report_prebuild(Project(), [REELS[0], REELS[1], REELS[2]],
                                    snapshot_fn=snapshot_fn)

    assert report["disagreements"] == []
    assert "could not be read" in report["notes"][REELS[0]]
    assert "no existing timeline" in report["notes"][REELS[2]]
    assert "census" in capsys.readouterr().out.lower()


# ── The wiring: the build path calls it, or it is prose ──────────

@pytest.fixture
def build_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        'resolve: {project_name: "Mock Project", '
        'timeline_name: "Master"}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")
    return root


def _moment(number, name):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


class _FakeTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        if not getattr(self, "_uid", None):
            type(self)._seq = getattr(type(self), "_seq", 0) + 1
            self._uid = f"{type(self).__name__}-{type(self)._seq}"
        return self._uid


class _FakeProject:
    def __init__(self, names):
        self.timelines = [_FakeTimeline(name) for name in names]
        pool = MagicMock()
        pool.CreateEmptyTimeline.side_effect = self._create
        self._pool = pool

    def _create(self, name):
        timeline = _FakeTimeline(name)
        self.timelines.append(timeline)
        return timeline

    def GetMediaPool(self):
        return self._pool

    def GetName(self):
        return "Mock Project"

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    # A Resolve project HAS a cursor, and `resolve_lock`'s guard reads
    # it back by unique id - a fake without one cannot model the guard.
    def GetCurrentTimeline(self):
        # None until something sets it: a project that has not been
        # pointed anywhere has no cursor, and inventing one here would
        # hand the entry-unit guard a timeline nobody opened.
        return getattr(self, "_current", None)

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


def test_a_multi_reel_build_reports_before_placing(build_project):
    """The census fires on the build path whether or not anyone
    remembers: two reels build, the census is called with both finals
    before anything is placed."""
    from library.tools.reel_build import rebuild_reels_in_project

    resolve_project = _FakeProject(["Master"])
    moments = [_moment(1, "Reel 01 - hook"),
               _moment(2, "Reel 02 - promise")]

    def _place(**place_kwargs):
        resolve_project.GetMediaPool().CreateEmptyTimeline(
            place_kwargs.get("timeline_name"))
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=moments), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_prebuild_census.report_prebuild",
                  return_value={"disagreements": []}) as prebuild:
        rebuild_reels_in_project(str(build_project), organise=False,
                                 verify=False)

    assert prebuild.call_count == 1
    _project, finals = prebuild.call_args[0][:2]
    assert finals == ["Reel 01 - hook", "Reel 02 - promise"]
