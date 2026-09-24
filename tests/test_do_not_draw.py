"""A deleted graphic stays deleted through a rebuild.

2026-09-13, wipe and rebuild: the captain had deleted the planned
graphic `mg_geo-podcast_a072b160.mov` from Reel 01's timeline by hand
and the fresh build placed it again - nothing recorded the act at
all. `external/do_not_draw.json` (`library/tools/do_not_draw.py`) is
the store, enforced at placement: the plan keeps the record of what
was intended, the timeline does not play it, and a rebuild that
re-renders under a new content hash holds the same deletion without
being told again.

Fail-before: `library.tools.do_not_draw` has no `load_rules` and
`place_overlay_segments` draws every segment it is given - every test
here errors on the attribute, not on an assertion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools import do_not_draw


REEL = "Reel 01 - the-cta"
LABEL = "vox_reel_01_the_cta_00"
SEGMENT_ID = "mg_geo-podcast_a072b160"
ELEMENTS = ["stat_callout"]
REASON = "captain 2026-09-13: deleted by hand in Resolve"


def _rule(**over):
    rule = {"reel": REEL, "placement_label": LABEL,
            "segment_id": SEGMENT_ID, "elements": list(ELEMENTS),
            "reason": REASON}
    rule.update(over)
    return rule


def _segment(label=LABEL, segment_id=SEGMENT_ID, elements=ELEMENTS,
             path=None):
    return {
        "placement_label": label,
        "segment_id": segment_id,
        "elements": list(elements),
        "overlay_path": path or f"/renders/{segment_id}.mov",
        "timeline_start": 9.092,
        "timeline_end": 11.595,
        "total_frames": 60,
        "lane": 0,
    }


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "external").mkdir(parents=True)
    return project


def _write_rules(project, body):
    (project / "external" / "do_not_draw.json").write_text(
        json.dumps(body), encoding="utf-8")


# ── 1. The declaration validates, loudly ─────────────────────────────


def test_a_malformed_file_refuses_rather_than_building_past(tmp_path):
    project = _project(tmp_path)
    _write_rules(project, {"version": 1,
                           "suppressions": [{"reel": REEL}]})
    with pytest.raises(do_not_draw.DoNotDrawError):
        do_not_draw.load_rules(str(project))


# ── 2. Matching: the label survives the re-render ────────────────────

def test_a_label_hit_suppresses():
    held, why = do_not_draw.should_suppress([_rule()], REEL, _segment())
    assert held and "stays undrawn" in why


def test_a_segment_id_hit_suppresses_without_a_label():
    held, _ = do_not_draw.should_suppress(
        [_rule(placement_label=None)], REEL,
        _segment(label=None))
    assert held


def test_a_shifted_plan_refuses_the_suppression_and_says_so():
    """The input that would delete the WRONG graphic: the plan
    re-ordered under the label, so it now wears another element.
    Suppression refuses - the graphic plays until re-transcribed -
    and the reason names both sides."""
    held, why = do_not_draw.should_suppress(
        [_rule()], REEL, _segment(elements=["quote_card"]))
    assert not held
    assert "NOT suppressed" in why
    assert "stat_callout" in why and "quote_card" in why


# ── 3. Unmatched: a rule the plan left behind says so ────────────────


# ── 4. Survival: the deletion holds across a re-render ───────────────

class _PlacedItem:
    def __init__(self):
        self.props = {}

    def GetStart(self):
        return 218

    def SetProperty(self, prop, value):
        self.props[prop] = value
        return True

    def GetProperty(self, prop):
        return self.props.get(prop)


class _Timeline:
    def __init__(self):
        self.items = [_PlacedItem()]

    def GetUniqueId(self):
        return "timeline-1"

    def GetName(self):
        return "fake reel"

    def GetItemListInTrack(self, kind, index):
        return list(self.items)


class _Folder:
    def __init__(self, name="Master"):
        self._name = name
        self.subs = []

    def GetName(self):
        return self._name

    def GetClipList(self):
        return []

    def GetSubFolderList(self):
        return list(self.subs)


class _Pool:
    """Records what the placer asked Resolve to do."""

    def __init__(self):
        self.appended = []
        self._root = _Folder()
        self._current = self._root

    def GetRootFolder(self):
        return self._root

    def GetCurrentFolder(self):
        return self._current

    def SetCurrentFolder(self, folder):
        self._current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = _Folder(name)
        parent.subs.append(folder)
        self._current = folder
        return folder

    def ImportMedia(self, paths):
        return [object()]

    def AppendToTimeline(self, clips):
        self.appended.extend(clips)
        return [{"placed": True}]


class _Project:
    def __init__(self, timeline):
        self._timeline = timeline

    def SetCurrentTimeline(self, timeline):
        pass

    def GetCurrentTimeline(self):
        return self._timeline

    def GetMediaPool(self):
        return _Pool()


def _build_once(rules, segments):
    """One placement pass through the real funnel - what a rebuild
    does with the segments it rendered, minus Resolve itself."""
    from library.tools.reel_build import place_overlay_segments

    pool = _Pool()
    timeline = _Timeline()
    held = place_overlay_segments(
        pool, _Project(timeline), timeline, REEL, 24000 / 1001,
        segments, 6, kind="explainer", check="F21",
        project_folder="", do_not_draw=rules)
    return pool, held


def test_a_deleted_graphic_stays_deleted_through_a_rerender():
    """The rebuild equivalent: build 1 places the plan minus the
    deletion; build 2 re-renders (new content hash, same label) and
    the deletion holds again - by value on what Resolve was asked
    to append, not asserted."""
    rules = do_not_draw.validate_rules([_rule()])
    control = _segment(label="vox_reel_01_the_cta_01",
                       segment_id="mg_geo-podcast_11111111",
                       elements=["quote_card"],
                       path="/renders/mg_geo-podcast_11111111.mov")
    pool1, held1 = _build_once(rules, [_segment(), control])
    assert len(pool1.appended) == 1
    assert held1 == [SEGMENT_ID]

    rerendered = _segment(segment_id="mg_geo-podcast_bbbb2222",
                          path="/renders/mg_geo-podcast_bbbb2222.mov")
    pool2, held2 = _build_once(rules, [rerendered, control])
    assert len(pool2.appended) == 1
    assert held2 == ["mg_geo-podcast_bbbb2222"]


def test_without_the_declaration_the_graphic_comes_back():
    """The failing input the survival test above guards: the same two
    builds with no declaration place the graphic both times - which
    is exactly what the 2026-09-13 rebuild did."""
    control = _segment(label="vox_reel_01_the_cta_01",
                       segment_id="mg_geo-podcast_11111111",
                       elements=["quote_card"],
                       path="/renders/mg_geo-podcast_11111111.mov")
    pool1, held1 = _build_once(None, [_segment(), control])
    pool2, held2 = _build_once(
        [], [_segment(segment_id="mg_geo-podcast_bbbb2222",
                       path="/renders/mg_geo-podcast_bbbb2222.mov"),
             control])
    assert len(pool1.appended) == 2 and held1 == []
    assert len(pool2.appended) == 2 and held2 == []


def test_a_full_round_trip_through_the_file(tmp_path):
    """Write the file the captain writes, read it the way the build
    reads it, suppress through the funnel."""
    project = _project(tmp_path)
    _write_rules(project, {"version": 1, "suppressions": [_rule()]})
    rules = do_not_draw.load_rules(str(project))
    pool, held = _build_once(rules, [_segment()])
    assert pool.appended == [] and held == [SEGMENT_ID]
