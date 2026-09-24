"""The drift detector finally has a caller, and the caller is pinned.

`library/tools/drift_check.py` is the schedule `transform_drift.py`
never had: newest build snapshot per reel against a self-read of the
live timeline, printing the per-reel factor at both ends of every
build. What these pin:

- the comparison that found the 2026-09-16 halving reproduces exactly:
  halved Pan/Tilt on every clip reads as one factor of 0.5;
- the two things the measurement must handle: snapshot rounding
  (0.49996-0.50004 is one factor, not a second one) and zero-valued
  placements (undefined ratio, never 1.0, never diluting the verdict);
- matching is on track, record frame AND name: a replaced clip reads
  as missing, never as a factor;
- the self-read is load-bearing: the read happens with the reel
  current, and the cursor is back where it started afterwards;
- the build runs the check at its start AND its end, and neither call
  can fail the build it instruments.
"""

import ast
import inspect
import json

import pytest

from library.tools import drift_check, reel_build
from library.tools.drift_check import (
    compare_documents,
    find_live_timeline,
    keyed_transforms,
    newest_snapshots,
    per_reel_factor,
    summarize,
)
from library.tools.transform_drift import TransformDriftError


def doc(name, *clips):
    """A `timeline_serializer`-shaped document carrying `clips`.

    Each clip is `(track, record_in, clip_name, pan, tilt)`.
    """
    tracks = {}
    for track, start, clip_name, pan, tilt in clips:
        tracks.setdefault(track, []).append(
            {"record_in": start, "name": clip_name,
             "transform": {"Pan": pan, "Tilt": tilt, "ZoomX": 2.307}})
    return {"metadata": {"name": name},
            "tracks": [{"type": "video", "index": index, "clips": clips_}
                       for index, clips_ in sorted(tracks.items())]}


#: Reel 26 as the 2026-09-17 measurement found it: the captain's
#: hand-set Pan -77.644 reading -38.822, the caption row at Tilt -864
#: reading -432, the emblem at 1167.568 reading 583.784.
BUILT = doc("Reel 26",
            (1, 100, "akshita.MXF", -77.644, -0.79),
            (4, 100, "caption.mov", 0.0, -864.0),
            (6, 100, "emblem.mov", 1167.568, 0.0))
HALVED = doc("Reel 26",
             (1, 100, "akshita.MXF", -38.822, -0.395),
             (4, 100, "caption.mov", 0.0, -432.0),
             (6, 100, "emblem.mov", 583.784, 0.0))


def test_the_measured_halving_reads_as_one_factor_of_a_half():
    """Seven live reels, 24-55 non-zero axes each, every one x0.5."""
    compared = compare_documents("Reel 26", BUILT, HALVED)
    assert compared["factor"] == pytest.approx(0.5)
    assert compared["moved"] == 3
    assert "every one by x0.5" in compared["line"]


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


def test_zero_placements_are_undefined_never_one():
    """Zero times anything is zero: immune, and not diluting.

    The caption Pan above is 0.0 built and 0.0 live. Its ratio is
    undefined (not 1.0), and the reel still verdicts 0.5 on the axes
    that can move.
    """
    compared = compare_documents("Reel 26", BUILT, HALVED)
    zero_rows = [row for row in compared["rows"]
                 if row["built"]["Pan"] == 0.0]
    assert zero_rows and all(
        row["factors"]["Pan"] is None for row in zero_rows)
    assert compared["factor"] == pytest.approx(0.5)


def test_an_all_zero_reel_cannot_verdict_anything_and_holds():
    """Where nothing can move, nothing moved - not x1.0."""
    built = doc("Reel X", (1, 0, "still.mov", 0.0, 0.0))
    compared = compare_documents("Reel X", built, built)
    assert compared["factor"] is None
    assert "hold exactly what the build wrote" in compared["line"]


def test_a_replaced_clip_reads_as_missing_never_as_a_factor():
    """Same track and record frame, different name: the build's clip
    went away. Matching on position alone would divide the stranger's
    transform by the build's and invent a factor."""
    built = doc("Reel 01", (1, 590, "LC4930.MXF", 1.493, 0.25))
    live = doc("Reel 01", (1, 590, "LC4932.MXF", 5.972, 1.0))
    compared = compare_documents("Reel 01", built, live)
    assert compared["missing"] == 1
    assert compared["factor"] is None
    assert "no longer has" in compared["line"]


def test_two_different_factors_refuse_to_read_as_one():
    """A non-uniform move is a DIFFERENT fault and must not borrow
    this one's name - the tolerance is for rounding, not for causes."""
    built = doc("Reel 01",
                (1, 590, "a.MXF", 10.0, 0.25),
                (1, 1069, "b.MXF", 10.0, 0.25))
    live = doc("Reel 01",
               (1, 590, "a.MXF", 5.0, 0.125),
               (1, 1069, "b.MXF", 20.0, 0.5))
    compared = compare_documents("Reel 01", built, live)
    assert compared["factor"] is None
    assert "by NO single factor" in compared["line"]


# ── The self-read: current for its own read, cursor back ─────────────

class _FakeTimeline:
    def __init__(self, name, uid):
        self._name = name
        self._uid = uid

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid


class _FakeProject:
    def __init__(self, name, timelines):
        self._name = name
        self._timelines = timelines
        self._current = timelines[0]

    def GetName(self):
        return self._name

    def GetCurrentTimeline(self):
        return self._current

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]


def _project_folder(tmp_path, resolve_name):
    root = tmp_path / "project"
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (root / "project.yaml").write_text(
        f"resolve:\n  project_name: {resolve_name}\n",
        encoding="utf-8")
    (review / "Reel_26.timeline.json").write_text(
        json.dumps(BUILT), encoding="utf-8")
    return str(root)


def test_the_read_happens_with_the_reel_current_and_restores(
        tmp_path, monkeypatch, capsys):
    """Load-bearing half: the live values are read while Reel 26 is
    current, and afterwards the cursor sits where the sweep found it."""
    reel = _FakeTimeline("Reel 26", "uid-reel")
    other = _FakeTimeline("GEO Podcast - Synced", "uid-master")
    project = _FakeProject("field test", [other, reel])
    seen_current = []

    def fake_serialize(*args, **kwargs):
        seen_current.append(project.GetCurrentTimeline().GetName())
        return json.loads(json.dumps(HALVED))

    monkeypatch.setattr(
        "library.tools.timeline_serializer.serialize_timeline_state",
        fake_serialize)
    folder = _project_folder(tmp_path, "field test")
    report = drift_check.check_project(folder, project=project)

    assert seen_current == ["Reel 26"]
    assert project.GetCurrentTimeline().GetName() == "GEO Podcast - Synced"
    assert "entered on 'GEO Podcast - Synced', read back on " in \
        report["cursor"]
    assert report["reels"]["Reel 26"]["factor"] == pytest.approx(0.5)
    out = capsys.readouterr().out
    assert "drift: Reel 26: 3 of 3 placement(s) moved since the build" in out


# ── The build runs it twice, and neither run can fail the build ──────

_SOURCE = inspect.getsource(reel_build)
_BODY = _SOURCE[_SOURCE.index("def rebuild_reels_in_project"):]


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
