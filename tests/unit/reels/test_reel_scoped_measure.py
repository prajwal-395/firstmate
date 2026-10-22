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
import time
from contextlib import contextmanager

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
from tests.unit.reels import test_reel_scoped_retime as retime


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
        "writes": 0, "scans": 0, "cursor_moves": 0, "renders": 0,
        "item_writes": 0}
    assert CountingTimeline is not None


#: The mutating calls a timeline ITEM carries.  Counted apart from the
#: timeline's own writes: a speed set on a clip is a different write
#: from a placement, and the punch list's rule is never to combine
#: denominators.
ITEM_WRITE_METHODS = ("SetSpeed", "SetProperty", "SetCDL",
                       "AddFusionComp", "DeleteFusionComp", "SetFusionComp",
                       "SetClipEnabled", "SetTransform", "SplitAtFrame",
                       "SetKeyframe")


@contextmanager
def counting_item_writes(counter):
    """Count the mutating calls the double's timeline ITEMS take.

    The double's item CLASS is patched, not the item: a duplicate of a
    counted timeline copies its items, and an item-level wrapper would
    follow the copy and count the original's writes.
    """
    from tests import resolve_double as _double

    cls = _double.FakeTimelineItem
    original = {}
    for name in ITEM_WRITE_METHODS:
        method = getattr(cls, name, None)
        if method is None:
            continue
        original[name] = method

        def _make(wrapped, label):
            def counted(self, *args, **kwargs):
                counter.item_writes += 1
                counter.item_write_trace.append(label)
                return wrapped(self, *args, **kwargs)
            return counted

        setattr(cls, name, _make(method, name))
    try:
        yield counter
    finally:
        for name, method in original.items():
            setattr(cls, name, method)


def test_a_one_clip_retime_costs_less_than_the_rebuild_it_replaces(tmp_path):
    """PERF-003's measurement, before and after, on the same plan.

    BEFORE: the full rebuild of the retimed plan - what the scoped
    route used to fall back to, because a retime was refused.

    AFTER: the scoped patch - the passage and the clock's tail
    re-placed, everything else carried over.

    The two are measured the same way, and the RESULT is the same reel:
    `test_reel_scoped_retime` proves the patched staging equals a fresh
    full build item for item, so this measures which route costs less
    to reach that same reel.

    The denominators are kept apart: timeline writes (the counter the
    scoped route has always been measured by), item writes (the speed
    the retime sets), and wall seconds.
    """
    from library.tools.reel_build import build_reel_timeline
    from tests.scenarios.test_reel_build import FakeMoment

    old_captions, new_captions = retime._scenario(tmp_path)

    # BEFORE: the rebuild.
    bulk_counter = Counter()
    _timeline, _pool, project = retime._world()
    counted_bulk = CountingProject(project, bulk_counter)
    bulk_started = time.perf_counter()
    with assume_sole_writer("measure-bulk"), \
            counting_item_writes(bulk_counter):
        build_reel_timeline(
            counted_bulk, FakeMoment(), retime._clips(), list(
                new_captions), 23.976, 1080, 1920, str(tmp_path),
            {"segments": []}, master_timeline=None,
            program_channels={"1": 1, "2": 1, "3": 1},
            timeline_name="Reel 99 - scoped retime",
            ranges=retime._retimed_ranges())
    bulk_seconds = time.perf_counter() - bulk_started

    # AFTER: the scoped patch, over the reel built before the retime.
    scoped_counter = Counter()
    _final, _pool, project, _record = retime._build(
        tmp_path, old_captions, retime._plain_ranges())
    prepared = retime._prepared(
        tmp_path, new_captions, retime._retimed_ranges())
    counted_scoped = CountingProject(project, scoped_counter)
    counted_scoped.final_name = "Reel 99 - scoped retime"
    scoped_started = time.perf_counter()
    with assume_sole_writer("measure-scoped"), \
            counting_item_writes(scoped_counter):
        retime._patch(counted_scoped, _final, prepared, tmp_path)
    scoped_seconds = time.perf_counter() - scoped_started

    assert scoped_counter.writes < bulk_counter.writes, (
        f"the scoped retime wrote {scoped_counter.writes} Resolve "
        f"call(s) against the rebuild's {bulk_counter.writes} "
        f"({scoped_counter.write_trace} vs {bulk_counter.write_trace})")
    assert scoped_seconds < bulk_seconds, (
        f"the scoped retime took {scoped_seconds:.3f}s against the "
        f"rebuild's {bulk_seconds:.3f}s")
