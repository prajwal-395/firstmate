"""The declared PowerGrade is THE grade on a reel, and the CDL rides inside it.

`SetCDL` returns True and can change nothing; a DRX applied and read back
by node is the route that delivers. The pixel measurements behind this:
docs/evidence/reel_look.md.
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import reel_look
from library.tools.color_page_grade import ColorPageGradeError

TEST_CDL = {
    "slope_r": 1.03, "slope_g": 1.0, "slope_b": 0.96,
    "offset_r": -0.01, "offset_g": 0.005, "offset_b": 0.02,
    "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
    "saturation": 1.12,
}

PROVENANCE = {"source": "built in the Resolve GUI for this test",
              "authorised_by": "test", "licence": "test fixture"}

# The eight nodes the reference `.drx` really builds, read back off the
# live graph. Named here so a fake graph is the shape Resolve returned,
# not a shape invented to make a test pass.
REAL_LABELS = ("Input", "BAL/EXP", "CONTRAST", "SAT",
               "W&B", "Output", "FLC", "Corrections")


# ── fakes ────────────────────────────────────────────────────────────────

class _FakeGraph:
    def __init__(self, item):
        self._item = item

    def GetNumNodes(self):
        return len(self._item.labels)

    def GetNodeLabel(self, index):
        return self._item.labels[index - 1]

    def ApplyGradeFromDRX(self, path, mode):
        self._item.drx_calls.append((path, mode))
        if self._item.drx_result:
            self._item.labels = list(REAL_LABELS)
        return self._item.drx_result


class _FakeItem:
    def __init__(self, path, name=None, drx_result=True, cdl_result=True,
                 labels=("",)):
        self._path = path
        self._name = name or os.path.basename(path)
        self.labels = list(labels)
        self.drx_calls = []
        self.cdl_calls = []
        self.drx_result = drx_result
        self._cdl_result = cdl_result

    def GetMediaPoolItem(self):
        item = self

        class _MPI:
            def GetClipProperty(self, key):
                return item._path if key == "File Path" else item._name
        return _MPI()

    def GetName(self):
        return self._name

    def GetNodeGraph(self):
        return _FakeGraph(self)

    def SetCDL(self, values):
        self.cdl_calls.append(dict(values))
        return self._cdl_result


class _FakeTimeline:
    def __init__(self, by_row):
        self._by_row = by_row

    def GetItemListInTrack(self, kind, index):
        assert kind == "video"
        return list(self._by_row.get(index, []))


def _plan():
    return {"video_tracks": [
        {"role": "a_roll", "occupant": "1", "index": 1},
        {"role": "a_roll", "occupant": "2", "index": 2},
        {"role": "frame", "occupant": "", "index": 3},
    ]}


def _declare(tmp_path, **extra):
    drx = tmp_path / "look.drx"
    drx.write_text("<Gallery::GyStill/>")
    block = {"path": "look.drx", "provenance": dict(PROVENANCE)}
    block.update(extra)
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"name": "t", "color": {"power_grade_drx": block}}))
    return drx


# ── 1. the declaration ───────────────────────────────────────────────────


def test_a_cdl_node_that_is_not_a_label_is_refused(tmp_path):
    for bad in (2, ["BAL/EXP"]):
        _declare(tmp_path, cdl_node=bad)
        with pytest.raises(ColorPageGradeError, match="cdl_node"):
            reel_look.resolve_power_grade(str(tmp_path))


# ── 2. the routing: a DRX REPLACES the CDL route, never joins it ─────────

def test_a_declared_drx_is_the_route_and_the_bare_cdl_is_not_applied():
    """`ApplyGradeFromDRX` replaces the whole node graph including the
    node `SetCDL` writes, so running both would leave whichever ran
    second and call it the grade."""
    item = _FakeItem("/footage/a.mxf")
    timeline = _FakeTimeline({1: [item]})
    record = reel_look.apply_grade(
        timeline, _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": None},
        footage_sources={"/footage/a.mxf"})
    assert record["route"] == "power_grade_drx"
    assert item.drx_calls == [("/look.drx", 0)]
    assert item.cdl_calls == []
    assert record["applied"] == ["a.mxf"]
    # verified by a node readback: a re-fetched graph carries the nodes
    # the file builds, which is the whole reason this is the route
    assert record["nodes"]["a.mxf"] == 8
    assert record["verified"] is True
    assert record["warnings"] == []


# ── 3. the CDL lands INSIDE the applied grade, on the named node ─────────

def test_the_cdl_lands_on_the_named_node_of_the_applied_graph():
    """`BAL/EXP` is index 2 of the eight nodes the reference grade
    builds, and that is the node its own README says a per-clip
    exposure and balance correction is dialled into."""
    item = _FakeItem("/footage/a.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": "BAL/EXP"},
        footage_sources={"/footage/a.mxf"})
    assert record["warnings"] == []
    (call,) = item.cdl_calls
    assert call["NodeIndex"] == "2"          # BAL/EXP, found by LABEL
    assert call["Slope"] == "1.0300 1.0000 0.9600"
    assert call["Offset"] == "-0.0100 0.0050 0.0200"
    assert call["Saturation"] == "1.1200"
    assert record["cdl_landed_on"]["a.mxf"]["landed"] is True
    # A grade whose nodes sit in a different order still gets the CDL
    # on the node that MEANS exposure, because the label is what is
    # matched.
    item = _FakeItem("/footage/a.mxf")
    reordered = ("Input", "FLC", "CONTRAST", "BAL/EXP", "Output")

    class _Reordered(_FakeGraph):
        def ApplyGradeFromDRX(self, path, mode):
            self._item.drx_calls.append((path, mode))
            self._item.labels = list(reordered)
            return True

    item.GetNodeGraph = lambda: _Reordered(item)
    reel_look.apply_grade(
        _FakeTimeline({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": "BAL/EXP"},
        footage_sources={"/footage/a.mxf"})
    (call,) = item.cdl_calls
    assert call["NodeIndex"] == "4"


def test_a_missing_label_refuses_the_cdl_rather_than_guessing_node_1():
    """Node 1 of the reference grade is its INPUT colour-space
    transform. Writing an exposure correction into it because a label
    was misspelt would replace the conversion the whole grade is built
    on, so the CDL is dropped BY NAME instead."""
    item = _FakeItem("/footage/a.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": "EXPOSURE"},
        footage_sources={"/footage/a.mxf"})
    assert item.drx_calls                      # the look still landed
    assert item.cdl_calls == []                # the correction did not
    assert record["cdl_landed_on"] == {}
    assert any("cdl_node_missing" in w for w in record["warnings"])


# ── 5. a refusal is recorded, never raised, and never silent ─────────────

def test_a_clip_resolve_refuses_is_recorded_and_the_reel_continues():
    bad = _FakeItem("/footage/a.mxf", drx_result=False)
    good = _FakeItem("/footage/b.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline({1: [bad], 2: [good]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": None},
        footage_sources={"/footage/a.mxf", "/footage/b.mxf"})
    assert record["applied"] == ["b.mxf"]
    assert any("a.mxf" in w and "ApplyGradeFromDRX" in w
               for w in record["warnings"])
    # a SetCDL refusal inside the graph is recorded by name
    item = _FakeItem("/footage/a.mxf", cdl_result=False)
    record = reel_look.apply_grade(
        _FakeTimeline({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": "BAL/EXP"},
        footage_sources={"/footage/a.mxf"})
    assert record["applied"] == ["a.mxf"]       # the look landed
    assert record["cdl_landed_on"]["a.mxf"]["landed"] is False
    assert any("BAL/EXP" in w for w in record["warnings"])


# ── 6. SetCDL RETURNS TRUE AND CHANGES NOTHING ───────────────────────────
#
# The regression this module exists for (docs/evidence/reel_look.md):
# do not report a grade this route claims to have applied.

def test_setcdl_true_is_not_evidence_the_grade_landed():
    """A clip whose `SetCDL` returns True but whose picture never
    changes is EXACTLY the measured failure, and the record must not
    call that a grade. `verified` is False and the reason carries the
    numbers, so a reader of a build record can tell what happened
    without re-running Resolve."""
    item = _FakeItem("/footage/a.mxf", cdl_result=True)
    record = reel_look.apply_grade(
        _FakeTimeline({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade=None, footage_sources={"/footage/a.mxf"},
        allow_unverified_cdl=True)
    assert item.cdl_calls, "the call is still made"
    assert record["applied"] == ["a.mxf"], "SetCDL returned True"
    assert record["verified"] is False, (
        "a True return from SetCDL must never be recorded as a verified "
        "grade - it returned True on six clips that stayed ungraded")
    assert "2,073,600" in record["unverified_because"]
    assert "601,760" in record["unverified_because"]


def test_a_look_with_no_drx_is_refused_rather_than_reported():
    """The pipeline REFUSES rather than writing `applied` against clips
    nothing can be shown to have reached."""
    item = _FakeItem("/footage/a.mxf")
    with pytest.raises(reel_look.ReelLookRefused) as excinfo:
        reel_look.apply_grade(
            _FakeTimeline({1: [item]}), _plan(), dict(TEST_CDL),
            power_grade=None, footage_sources={"/footage/a.mxf"})
    message = str(excinfo.value)
    assert "power_grade_drx" in message, "the refusal names the fix"
    assert "2,073,600" in message, "the refusal carries the measurement"
    assert item.cdl_calls == [], "nothing was applied on the way out"


def test_declaring_no_look_at_all_is_not_a_refusal():
    """A project that declares nothing gets nothing, quietly - that is
    an absence, not an unverifiable grade (AGENTS.md 10.1)."""
    item = _FakeItem("/footage/a.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline({1: [item]}), _plan(), {}, power_grade=None,
        footage_sources={"/footage/a.mxf"})
    assert record["route"] == "none"
    assert record["verified"] is False


def test_a_drx_that_says_yes_and_lands_no_nodes_is_not_verified():
    """The same lie in the other mechanism: `ApplyGradeFromDRX` returns
    True and the graph still reads the bare node. Applied, NOT verified,
    and the warning says to treat the reel as ungraded."""
    class _Liar(_FakeItem):
        def GetNodeGraph(self):
            item = self

            class _G(_FakeGraph):
                def ApplyGradeFromDRX(self, path, mode):
                    item.drx_calls.append((path, mode))
                    return True          # says yes, adds no nodes
            return _G(item)

    item = _Liar("/footage/a.mxf")
    record = reel_look.apply_grade(
        _FakeTimeline({1: [item]}), _plan(), dict(TEST_CDL),
        power_grade={"path": "/look.drx", "provenance": PROVENANCE,
                     "cdl_node": None},
        footage_sources={"/footage/a.mxf"})
    assert record["applied"] == ["a.mxf"]
    assert record["nodes"] == {"a.mxf": 1}
    assert record["verified"] is False
    assert any("not VERIFIED" in w for w in record["warnings"])


