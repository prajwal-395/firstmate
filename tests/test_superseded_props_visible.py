"""A re-plan must never leave a silent duplicate: two props files, one span.

POLICY (leave-and-make-visible): when a re-plan writes a new hashed
filename beside the old one - a plan entry that predates `card_index`
grouping under a None key, a source span shifted past the millisecond
the filename carries - the step LEAVES the old file and REPORTS the
pair, so nobody reading two props files for one span mistakes the
orphan for the live card.

Retire-with-archive was the other honest shape, and it lost on the
captain's standing rule: archive rather than delete, and a step that
cannot archive what it is retiring STOPS instead of proceeding. At
render time the step cannot prove the old file unreferenced -
reachability needs the Resolve database copy plus the current step
records and manifest, mid-write mid-pass, and the old generation may
still be placed on a timeline the captain keeps - so auto-retire
would either duplicate the whole mark/sweep apparatus inside the
render path or turn a harmless leftover (disk and confusion, never a
wrong render) into a refused caption pass. Retirement stays with
`caption_asset_gc` mark + sweep (quarantine, never delete), which
alone can prove the mate unplaced. The codebase already reads this
way: `subtitle_coverage` positions a card by its PLACED record, never
by the props' own stale `_timeline_start`.

The surface under test is `ambiguous_span_pairs` (ledger-grouped,
timeline-scoped) as reported by `_ambiguous_pairs_for_dir` and
carried on the pass payload: the orphan is identifiable as the
non-drawing mate WITHOUT opening either render.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import (
    RENDERED,
    _ambiguous_pairs_for_dir,
    render_one_segment,
)
from library.tools.caption_asset_gc import (
    ambiguous_span_pairs,
    ledger_path_for,
)
from tests.test_subtitle_overlay_modes import (
    _props,
    _StubRenderer,
)


def _old_plan_props():
    """A plan entry predating `card_index`, in the old capitalisation."""
    props = _props()
    assert "_card_index" not in props
    props["subtitles"] = [dict(props["subtitles"][0],
                               text="And So My Very")]
    return props


def _replanned_props():
    """The same card re-planned: carded, lowercased, span shifted.

    The source span moves past the millisecond the filename carries,
    so the provenance stem - and the card - differs: the same-card
    retention rule names nothing superseded, and the old file would
    sit silent beside its replacement. The timeline span is
    untouched: one span, two files.
    """
    props = _props()
    props["_card_index"] = 7
    props["_source_start"] = 20.0
    props["_source_end"] = 22.0
    props["subtitles"] = [dict(props["subtitles"][0],
                               text="and so my very")]
    return props


def _render(props, out_dir):
    return render_one_segment(props, out_dir, "tl",
                              remotion_dir="/none",
                              renderer=_StubRenderer(),
                              overlay_geometry="full")


def test_replan_pair_is_reported_not_moved(tmp_path, capsys):
    """The brief's case: the orphan is named as the mate, on disk."""
    out_dir = str(tmp_path)
    first = _render(_old_plan_props(), out_dir)
    assert first["provenance"] == RENDERED
    second = _render(_replanned_props(), out_dir)
    assert second["provenance"] == RENDERED
    assert second["overlay_path"] != first["overlay_path"]
    # The same-card rule missed it: different card, nothing superseded.
    assert second["superseded"] == []
    # Nothing moved: both generations are still on disk.
    assert os.path.isfile(first["overlay_path"])
    assert os.path.isfile(second["overlay_path"])

    pairs = _ambiguous_pairs_for_dir(out_dir, {second["overlay_path"]})

    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["timeline"] == "tl"
    assert pair["files"] == sorted(
        [first["overlay_path"], second["overlay_path"]])
    # Identifiable WITHOUT opening either render: the ledger alone
    # says which file drew this pass and which did not.
    assert pair["drawn_fresh"] == [second["overlay_path"]]
    # And said loudly, not just carried.
    assert "AMBIGUOUS" in capsys.readouterr().err


def test_explained_pair_is_not_ambiguous(tmp_path):
    """A re-render the ledger already attributes leaves no pair behind."""
    out_dir = str(tmp_path)
    first = _render(_props(), out_dir)
    changed = _props()
    changed["subtitles"] = [dict(changed["subtitles"][0],
                                 text="and so my very CHANGED")]
    second = _render(changed, out_dir)
    assert second["provenance"] == RENDERED
    assert second["superseded"] == [first["overlay_path"]]
    pairs = _ambiguous_pairs_for_dir(out_dir, {second["overlay_path"]})
    assert pairs == []


def test_shared_file_is_not_a_pair():
    """One file serving two placings is the sharing, not a duplicate."""
    entry = {"overlay_path": "/d/sub_a_b_1-2_aaaaaaaa.mov",
             "binding": {"timeline": "tl"},
             "timeline_start": 0.0, "timeline_end": 2.0,
             "superseded": []}
    twin = dict(entry, binding={"timeline": "tl", "block_position": 2})
    assert ambiguous_span_pairs([entry, twin], set()) == []


def test_cross_timeline_same_span_is_not_a_pair():
    """A master card and a reel card share absolute seconds legitimately:
    the props carry no timeline, which is why this groups on the ledger
    bindings instead of on a directory scan."""
    master = {"overlay_path": "/d/sub_a_b_1-2_aaaaaaaa.mov",
              "binding": {"timeline": "master"},
              "timeline_start": 10.0, "timeline_end": 12.0,
              "superseded": []}
    reel = {"overlay_path": "/d/sub_c_d_3-4_bbbbbbbb.mov",
            "binding": {"timeline": "reel 01"},
            "timeline_start": 10.0, "timeline_end": 12.0,
            "superseded": []}
    assert ambiguous_span_pairs([master, reel], set()) == []


def test_stale_duplicate_with_no_fresh_claim_is_still_visible():
    """A duplicate from history no pass in this process drew is still
    reported - with nobody named as drawing - rather than silent."""
    old = {"overlay_path": "/d/sub_a_b_1-2_aaaaaaaa.mov",
           "binding": {"timeline": "tl"},
           "timeline_start": 0.0, "timeline_end": 2.0,
           "superseded": []}
    new = {"overlay_path": "/d/sub_c_d_3-4_bbbbbbbb.mov",
           "binding": {"timeline": "tl"},
           "timeline_start": 0.0, "timeline_end": 2.0,
           "superseded": []}
    pairs = ambiguous_span_pairs([old, new], set())
    assert len(pairs) == 1
    assert pairs[0]["drawn_fresh"] == []
    assert pairs[0]["files"] == sorted(
        [old["overlay_path"], new["overlay_path"]])


def test_ledger_is_the_only_thing_the_report_reads(tmp_path):
    """The pair is established from ledger paths alone: corrupt the
    ledger and the report degrades to a note, never a refusal."""
    out_dir = str(tmp_path)
    _render(_old_plan_props(), out_dir)
    with open(ledger_path_for(out_dir), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert _ambiguous_pairs_for_dir(out_dir, set()) == []
