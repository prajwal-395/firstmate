"""A correct dissolve is not a clip collision.

Finding 18, execution-frontier report 2026-09-24: build QA reported
every correct dissolve as a clip-overlap failure - a 6-frame centred
Cross Dissolve read as "overlap_before_clip_N expected >=0, got -3".
A centred native dissolve is drawn OVER the cut, so the neighbours
overlap by half its duration. That explained overlap passes; anything
bigger, or anywhere else, still fails.

No Resolve: a fake timeline with scripted item spans.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.timeline_qa import run_full_timeline_qa  # noqa: E402


class _Item:
    def __init__(self, start, end):
        self._start = start
        self._end = end

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetMediaPoolItem(self):
        return None


class _Timeline:
    def __init__(self, v1_spans):
        self._items = [_Item(s, e) for s, e in v1_spans]

    def GetItemListInTrack(self, track_type, index):
        if track_type == "video" and index == 1:
            return list(self._items)
        return []


def _failures(report):
    return [c for c in report.checks if not c.passed]


# ── The finding: a 6 f dissolve overlapping by 3 ────────────────────

def test_a_declared_dissolve_explains_its_own_overlap():
    """Two V1 items overlapping by 3 frames at a boundary carrying a
    declared 6-frame centred dissolve: no failure."""
    timeline = _Timeline([(0, 161), (158, 300)])
    manifest = {"native_transitions": [
        {"transition_id": "trans_001", "after_clip": 0,
         "duration_frames": 6}],
        "project": {"frame_rate": 30, "duration_seconds": 10.0},
    }
    report = run_full_timeline_qa(timeline, None, manifest)
    assert report.passed


def test_an_overlap_bigger_than_the_dissolve_still_fails():
    """The same boundary overlapping by 4 frames with only a 6-frame
    dissolve declared (allowance 3): still a collision."""
    timeline = _Timeline([(0, 162), (158, 300)])
    manifest = {"native_transitions": [
        {"transition_id": "trans_001", "after_clip": 0,
         "duration_frames": 6}],
        "project": {"frame_rate": 30, "duration_seconds": 10.0},
    }
    report = run_full_timeline_qa(timeline, None, manifest)
    assert not report.passed
    assert any(c.name == "overlap_before_clip_1"
               for c in _failures(report))


def test_a_dissolve_at_the_wrong_boundary_explains_nothing():
    """A declared dissolve after clip 1 does not cover an overlap at
    the boundary after clip 0."""
    timeline = _Timeline([(0, 161), (158, 300), (300, 400)])
    manifest = {"native_transitions": [
        {"transition_id": "trans_001", "after_clip": 1,
         "duration_frames": 6}],
        "project": {"frame_rate": 30, "duration_seconds": 13.0},
    }
    report = run_full_timeline_qa(timeline, None, manifest)
    assert not report.passed
    assert any(c.name == "overlap_before_clip_1"
               for c in _failures(report))
