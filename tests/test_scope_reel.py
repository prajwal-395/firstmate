"""A reel is a SHAPE of scope, not a kind of region.

Firstmate-decided 2026-09-05. `Region.timeline` answers WHICH TIMELINE;
`Scope.kind` answers WHAT SHAPE - PROJECT, CLIP, REGION, REEL. The two
axes are independent, and a reel cannot be a REGION for two measured
reasons:

  cardinality - a reel is a LIST of keep ranges with its bad takes cut
                out (`reel_build.keep_ranges`); a REGION is one span;
  time base   - the ranges are on the reel's own timeline, which is its
                kept ranges laid end to end, so the same number means a
                different moment than on the master.

Bolting either onto REGION would make one of the two silent.
"""

import pytest

from library.tools import scope as scope_mod
from library.tools.region import MASTER, Region


def test_reel_is_its_own_kind_and_carries_ranges():
    reel = scope_mod.reel([(0.0, 5.0), (10.0, 12.0)], timeline="reel_03")
    assert reel.kind == scope_mod.REEL
    assert len(reel.reel_ranges) == 2
    assert reel.timeline == "reel_03"
    assert all(isinstance(r, Region) for r in reel.reel_ranges)


def test_a_reel_range_is_not_a_bare_pair_of_floats():
    """Pairs are accepted at the door and become Regions immediately."""
    reel = scope_mod.reel([(0.0, 5.0)], timeline="reel_03")
    assert reel.reel_ranges[0] == Region("reel_03", 0.0, 5.0)
    with pytest.raises(scope_mod.ScopeError):
        scope_mod.reel([3.0], timeline="reel_03")


def test_a_reel_with_no_ranges_is_refused():
    """Empty would read as the whole project and redo everything."""
    with pytest.raises(scope_mod.ScopeError) as exc:
        scope_mod.reel([], timeline="reel_03")
    assert "whole project" in str(exc.value)


def test_a_reel_may_not_mix_timelines():
    with pytest.raises(scope_mod.ScopeError) as exc:
        scope_mod.reel([Region("reel_03", 0.0, 5.0),
                        Region("reel_09", 6.0, 7.0)])
    assert "ONE timeline" in str(exc.value)


def test_a_reels_ranges_may_not_overlap():
    """reel_time walks in list order and returns the first containing
    range, so an overlap gives one second two answers."""
    with pytest.raises(scope_mod.ScopeError) as exc:
        scope_mod.reel([(0.0, 5.0), (3.0, 8.0)], timeline="reel_03")
    assert "overlap" in str(exc.value)


def test_play_order_is_preserved_and_time_order_is_not_required():
    """A closing CTA legitimately sits EARLIER on the master than the body
    (`reel_build.reel_ranges` appends it LAST), so ordering the ranges by
    time would move the closer into the middle of the reel."""
    reel = scope_mod.reel([(30.0, 40.0), (5.0, 8.0)], timeline="reel_03")
    assert [r.start for r in reel.reel_ranges] == [30.0, 5.0]


@pytest.mark.parametrize("kind,payload", [
    (scope_mod.PROJECT, {"reel_ranges": (Region(MASTER, 0.0, 1.0),)}),
    (scope_mod.CLIP, {"clip_id": "clip_001",
                      "reel_ranges": (Region(MASTER, 0.0, 1.0),)}),
])
def test_reel_ranges_on_a_non_reel_scope_are_refused(kind, payload):
    """__post_init__ was EXTENDED to REEL, not loosened for it."""
    with pytest.raises(scope_mod.ScopeError) as exc:
        scope_mod.Scope(kind, **payload)
    assert "reel" in str(exc.value).lower()


def test_a_reel_scope_carries_no_region_or_clip():
    with pytest.raises(scope_mod.ScopeError):
        scope_mod.Scope(scope_mod.REEL,
                        reel_ranges=(Region("r", 0.0, 1.0),),
                        region_span=Region("r", 0.0, 1.0))
    with pytest.raises(scope_mod.ScopeError):
        scope_mod.Scope(scope_mod.REEL,
                        reel_ranges=(Region("r", 0.0, 1.0),),
                        clip_id="clip_001")


def test_reel_is_in_kinds_and_a_region_scope_still_works():
    assert scope_mod.REEL in scope_mod.KINDS
    where = scope_mod.region("45.0-72.0")
    assert where.kind == scope_mod.REGION
    assert where.reel_ranges is None
    assert where.timeline is MASTER
