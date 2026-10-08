"""Timeline diff: two exported timelines, read as typed creative changes.

Each test names a defect the diff closes:

- a moved item read as a removal plus an addition, losing the fact that
  it MOVED (the replace guard's row diff pairs by (name, start, end), so
  a moved clip is two verdicts, not one move);
- a trimmed item read as a move, when its timeline span never changed
  and only its source in/out moved;
- a J-cut read as a trim, when the source span is identical and only
  the audio/video offset moved;
- a commit summary that counts nothing, so "what did this commit do"
  has no answer.

The diff runs on `reel_read.read_tracks` shape - the JSON a timeline
export reads back as - so every fixture is plain data, no Resolve.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import timeline_diff  # noqa: E402


def _clip(name, start, end, source_in=0, source_out=None, left=0, right=0):
    """One timeline item in `reel_read.read_tracks` shape."""
    if source_out is None:
        source_out = source_in + (end - start)
    return {
        "name": name,
        "record_in": start,
        "record_out": end,
        "duration": end - start,
        "source_in_frame": source_in,
        "source_out_frame": source_out,
        "left_offset": left,
        "right_offset": right,
    }


def _track(track_type, index, name, clips):
    return {"type": track_type, "index": index, "name": name,
            "clips": clips}


def _timeline(*tracks):
    return list(tracks)


def test_moved_item_is_one_move_not_a_removal_and_an_addition():
    """A clip that kept its name and source but changed span is ONE move.

    The defect: `reel_replace_guard.diff_rows` pairs by (name, start,
    end), so a moved clip is a `missing` verdict plus a `gained` item -
    two changes that say nothing about the clip having MOVED. A commit
    built on that would read "1 removal, 1 addition" where the truth is
    "1 move", and a revert would delete the clip and re-add it instead
    of moving it back.
    """
    earlier = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100),
        _clip("clip_002", 100, 200),
    ]))
    later = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 50, 150),
        _clip("clip_002", 100, 200),
    ]))
    changes = timeline_diff.diff_timelines(earlier, later)
    moves = [c for c in changes if c["op"] == timeline_diff.OP_MOVE]
    removes = [c for c in changes if c["op"] == timeline_diff.OP_REMOVE]
    adds = [c for c in changes if c["op"] == timeline_diff.OP_ADD]
    assert len(moves) == 1
    assert moves[0]["item"] == "clip_001"
    assert moves[0]["before"]["start"] == 0
    assert moves[0]["after"]["start"] == 50
    assert removes == []
    assert adds == []


def test_creator_summary_names_region_and_avoids_unproven_category_claims():
    changes = timeline_diff.diff_timelines(
        _timeline(_track("video", 1, "V1", [_clip("clip_001", 0, 100)])),
        _timeline(_track("video", 1, "V1", [_clip("clip_001", 30, 130)])))
    summary = timeline_diff.creative_summary(changes, fps=30)
    assert summary["changes"][0]["timecode"] == "00:00:00:00"
    assert summary["changes"][0]["end_timecode"] == "00:00:04:10"
    assert summary["category_status"]["sequence"] == "changed"
    assert summary["category_status"]["captions"] == "unknown"
    assert summary["untouched_categories"] == []


def test_trimmed_item_is_a_trim_not_a_move():
    """A clip whose source in/out moved but whose span did not is a trim.

    The defect: a diff that only compares spans cannot see a trim at
    all (the span is identical, so the item reads as unchanged), and a
    diff that compares source frames but not spans would call it a
    move. A trim is a source-level edit - the timeline placement is
    untouched - and the commit must say "trim", not "move".
    """
    earlier = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100, source_in=0, source_out=100),
    ]))
    later = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100, source_in=10, source_out=110),
    ]))
    changes = timeline_diff.diff_timelines(earlier, later)
    trims = [c for c in changes if c["op"] == timeline_diff.OP_TRIM]
    moves = [c for c in changes if c["op"] == timeline_diff.OP_MOVE]
    assert len(trims) == 1
    assert trims[0]["before"]["source_in"] == 0
    assert trims[0]["after"]["source_in"] == 10
    assert moves == []


def test_j_cut_is_a_j_cut_not_a_trim():
    """A clip whose audio/video offset moved is a J-cut, not a trim.

    The defect: a J-cut changes only the left/right offset - the source
    span and the timeline span are both identical. A diff that reads
    only spans reports nothing (the item is unchanged); a diff that
    reads source frames reports a trim. The commit must say "j_cut".
    """
    earlier = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100, left=0, right=0),
    ]))
    later = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100, left=12, right=0),
    ]))
    changes = timeline_diff.diff_timelines(earlier, later)
    j_cuts = [c for c in changes if c["op"] == timeline_diff.OP_J_CUT]
    trims = [c for c in changes if c["op"] == timeline_diff.OP_TRIM]
    assert len(j_cuts) == 1
    assert j_cuts[0]["before"]["left_offset"] == 0
    assert j_cuts[0]["after"]["left_offset"] == 12
    assert trims == []


def test_unchanged_timeline_produces_no_changes():
    """Two identical reads diff to nothing - the empty case a commit
    summary would otherwise drown in."""
    earlier = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100),
    ]))
    later = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100),
    ]))
    assert timeline_diff.diff_timelines(earlier, later) == []


def test_new_row_is_an_addition_and_lost_row_is_a_removal():
    """A row only the later timeline has is an addition; a row only the
    earlier has is a removal - the two ends of a track-level change."""
    earlier = _timeline(
        _track("video", 1, "V1", [_clip("clip_001", 0, 100)]),
        _track("video", 2, "V2", [_clip("clip_002", 0, 100)]),
    )
    later = _timeline(
        _track("video", 1, "V1", [_clip("clip_001", 0, 100)]),
        _track("audio", 1, "A1", [_clip("clip_003", 0, 100)]),
    )
    changes = timeline_diff.diff_timelines(earlier, later)
    adds = [c for c in changes if c["op"] == timeline_diff.OP_ADD]
    removes = [c for c in changes if c["op"] == timeline_diff.OP_REMOVE]
    assert [c["row"] for c in adds] == ["A1"]
    assert [c["row"] for c in removes] == ["V2"]


def test_summary_counts_each_op():
    """The commit summary counts changes by op - the captain's
    "11 trims, 4 replacements" is a count over this vocabulary."""
    earlier = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100),
        _clip("clip_002", 100, 200),
    ]))
    later = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 50, 150),
        _clip("clip_003", 100, 200),
    ]))
    changes = timeline_diff.diff_timelines(earlier, later)
    summary = timeline_diff.summarize(changes)
    assert summary == {"add": 1, "move": 1, "remove": 1}


def test_move_carries_the_retime_block_mutation():
    """A move is expressed as the edit_graph mutation it corresponds to:
    a retime_block with the new timeline start. This is the half that
    makes the diff usable against a graph, not just a report."""
    earlier = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100),
    ]))
    later = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 50, 150),
    ]))
    changes = timeline_diff.diff_timelines(earlier, later)
    assert changes[0]["mutation"] == {
        "op": "retime_block", "timeline_start": 50}


def test_trim_has_no_graph_mutation():
    """A trim is a source-level edit the edit_graph has no node for, so
    its mutation is None - the diff says what it cannot express rather
    than inventing a graph node that does not exist."""
    earlier = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100, source_in=0, source_out=100),
    ]))
    later = _timeline(_track("video", 1, "V1", [
        _clip("clip_001", 0, 100, source_in=10, source_out=110),
    ]))
    changes = timeline_diff.diff_timelines(earlier, later)
    assert changes[0]["mutation"] is None
