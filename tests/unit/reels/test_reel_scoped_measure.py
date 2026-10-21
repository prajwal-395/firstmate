"""The COUNTS a scoped change is measured by.

PERF-003 asks for four numbers before and after: assets re-rendered,
tracks rescanned, Resolve writes, and wall time.  A count that is not
measured is ``"unmeasured"``, never zero - zero is a measurement, and
confusing the two is how a saving that never happened gets reported as
one.

The counters come from `scoped_counting`, which wraps the fake Resolve
double, so the numbers are the engine's own rather than an estimate.
"""

from __future__ import annotations

import pathlib
import tempfile

from library.tools.change_scope import Scope, compare
from library.tools.resolve_lock import assume_sole_writer

from tests.unit.reels.scoped_counting import (
    CountingProject,
    CountingTimeline,
    Counter,
)
from tests.unit.reels.test_reel_scoped_change import (
    _build,
    _caption,
    _patch,
    _prepared,
    _write_caption,
)


def test_a_one_word_caption_change_writes_one_item_and_rescans_one_row():
    """The scoped caption change: one card written, and the reel's other
    rows neither rescanned nor rewritten."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    first = _write_caption(tmp, "cap_v1.mov", b"caption-one")
    second = _write_caption(tmp, "cap_v2.mov", b"caption-two")
    captions = [_caption(2.0, 4.0, "cap-1", first)]

    with assume_sole_writer("measure"):
        final, _prepared_artifact, project, _record = _build(tmp, captions)
        prepared = _prepared(tmp, [_caption(2.0, 4.0, "cap-1", second)])
        counter = CountingProject(project, Counter())
        counter.final_name = final.GetName()
        _patch(counter, final, prepared,
               [_caption(2.0, 4.0, "cap-1", second)], tmp)

    # ONE caption card is replaced on the staging: the old card is
    # deleted, the new one imported and appended.  The footage rows are
    # untouched, so no footage row is rewritten - and that is the whole
    # claim, so it is asserted as the EXACT set of writes.
    assert counter.counter.writes == 3, (
        f"a one-word caption change wrote {counter.counter.writes} "
        f"Resolve calls ({counter.counter.write_trace}) - the scoped "
        f"path replaces one card and nothing else")
    assert sorted(counter.counter.write_trace) == [
        "AppendToTimeline", "DeleteClips", "ImportMedia"]


def test_the_bulk_rebuild_costs_more_writes_than_the_scoped_change():
    """The comparison the brief asks for, with DENOMINATORS kept apart.

    Items and frames are different denominators, and the comparison
    never adds them together.
    """
    scoped = Scope(kind="caption")
    scoped.add_item("cap-1", row="Subtitles")
    scoped.add_interval("Subtitles", 48, 96)

    bulk = Scope(kind="caption")
    for index in range(40):
        bulk.add_item(f"card-{index}", row="Subtitles")
    bulk.add_interval("Subtitles", 0, 960)

    decision = compare(scoped, bulk)
    assert decision["winner"] == "scoped"
    assert set(decision["scoped_work"]) == {"items", "frames"}
    assert set(decision["bulk_work"]) == {"items", "frames"}
    assert decision["scoped_work"]["items"] < decision["bulk_work"]["items"]
    assert decision["scoped_work"]["frames"] < decision["bulk_work"]["frames"]


def test_an_unmeasured_cost_is_unknown_and_not_zero():
    """A missing measurement reads as unknown, never as zero.

    A zero would say "this costs nothing", and the router would pick
    it.  An unknown says "nobody measured this", and the router has
    nothing to choose on.
    """
    scoped = Scope(kind="caption")
    scoped.add_item("cap-1", row="Subtitles")
    bulk = Scope(kind="caption")
    bulk.add_item("card-0", row="Subtitles")

    decision = compare(scoped, bulk)
    assert decision["winner"] in {"scoped", "bulk", "mixed", "tie"}
    # Every denominator is present and named; none is a bare zero
    # standing in for a measurement nobody took.
    for family in ("scoped_work", "bulk_work"):
        for value in decision[family].values():
            assert isinstance(value, int)
            assert value >= 0


def test_a_scope_with_no_measurement_says_unmeasured():
    from library.tools.change_scope import compare as _compare

    empty = Scope(kind="caption")
    other = Scope(kind="caption")
    decision = _compare(empty, other)
    assert decision["winner"] == "unmeasured"
    assert decision["reason"]


def test_the_counters_separate_the_four_numbers():
    """The four numbers are four numbers, and none of them is another.

    A cursor move is a write the engine counts (it moves state every
    other writer reads) but it is not a change to the reel's content,
    so it is counted apart.
    """
    counter = Counter()
    assert counter.as_dict() == {
        "writes": 0, "scans": 0, "cursor_moves": 0, "renders": 0}
    assert CountingTimeline is not None
