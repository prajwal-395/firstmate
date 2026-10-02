"""The exit code is the gate: unit tests for the pure half.

`compare()` never touches Resolve; the readers are exercised live by
the lane that runs them (capture then verify on an untouched reel must
exit 0; verify against another reel's capture must exit 1 naming the
missing). Synthetic here, no projects, no timelines.
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_comparer():
    """`scripts/vep_marker_check.py` without touching `sys.path`.

    The old import-time insert was doubly fragile: process-global
    (collection order decides later bindings -
    `tests/test_static_check.py`) and relative (it resolves
    against whatever the working directory happens to be).
    """
    spec = importlib.util.spec_from_file_location(
        "_vep_marker_check", REPO_ROOT / "scripts" / "vep_marker_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compare


compare = _load_comparer()


def _timeline(color, name, note, frame, source="timeline_marker"):
    return {"color": color, "name": name, "note": note, "frame": frame,
            "source": source, "timecode": "00:00:00:00", "duration_frames": 1,
            "custom_data_raw": ""}


def _clip(track, item, start, offset, color, name, note):
    return {"track": track, "item": item, "source_file": "/x.mov",
            "start": start, "end": start + 72,
            "markers": [{"offset": offset, "color": color, "duration": 1,
                         "name": name, "note": note, "custom_data": ""}]}


def test_identical_reads_are_clean():
    captured = {
        "timeline": "Reel 09",
        "timeline_markers": [_timeline("Blue", "feedback", "use this", 1525,
                                       source="clip_marker")],
        "clip_items": [_clip("video5", "mg_x.mov", 1499, 26, "Blue",
                             "feedback", "use this")],
    }
    # A clip note also appears in the snapshot half; the timeline half
    # skips non-timeline sources so it is not double-counted.
    live = {"timeline": "Reel 09",
            "timeline_markers": [],
            "clip_items": [_clip("video5", "mg_x.mov", 1499, 26, "Blue",
                                 "feedback", "use this")]}
    assert compare(captured, live) == []


def test_a_missing_blue_is_named():
    captured = {"timeline": "Reel 09",
                "timeline_markers": [_timeline("Blue", "feedback",
                                              "use this", 1525)],
                "clip_items": []}
    live = {"timeline": "Reel 09", "timeline_markers": [],
            "clip_items": []}
    failures = compare(captured, live)
    assert len(failures) == 1
    assert "use this" in failures[0]
    assert "Blue" in failures[0]


def test_a_recoloured_note_fails():
    captured = {"timeline": "Reel 09",
                "timeline_markers": [_timeline("Blue", "feedback",
                                              "use this", 1525)],
                "clip_items": []}
    live = {"timeline": "Reel 09",
            "timeline_markers": [_timeline("Green", "feedback", "use this",
                                           1525)],
            "clip_items": []}
    assert len(compare(captured, live)) == 1


def test_a_moved_timeline_note_fails():
    captured = {"timeline": "Reel 09",
                "timeline_markers": [_timeline("Blue", "feedback",
                                              "use this", 1525)],
                "clip_items": []}
    live = {"timeline": "Reel 09",
            "timeline_markers": [_timeline("Blue", "feedback", "use this",
                                           1526)],
            "clip_items": []}
    failures = compare(captured, live)
    assert len(failures) == 1
    assert "1525" in failures[0]


def test_a_clip_note_on_a_replaced_item_fails():
    """The Reel 29 class: the item is swapped, the note dies silently,
    and only the anchor half of this check can see it."""
    captured = {"timeline": "Reel 28",
                "timeline_markers": [],
                "clip_items": [_clip("video5", "mg_old.mov", 889, 30,
                                     "Blue", "feedback", "scale this")]}
    live = {"timeline": "Reel 28",
            "timeline_markers": [],
            "clip_items": [_clip("video5", "mg_new.mov", 889, 30,
                                 "Blue", "feedback", "scale this")]}
    failures = compare(captured, live)
    assert len(failures) == 1
    assert "mg_old.mov" in failures[0]


def test_an_added_green_reply_is_not_a_failure():
    """The check guards what was captured; a reply landing beside a
    blue note is the lane's own work, not a loss."""
    captured = {"timeline": "Reel 09",
                "timeline_markers": [_timeline("Blue", "feedback",
                                              "use this", 1525)],
                "clip_items": []}
    live = {"timeline": "Reel 09",
            "timeline_markers": [
                _timeline("Blue", "feedback", "use this", 1525),
                _timeline("Green", "reply", "done", 1526)],
            "clip_items": []}
    assert compare(captured, live) == []
