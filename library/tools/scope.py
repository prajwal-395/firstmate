"""What an operation runs AGAINST: the whole project, one clip, or a region.

Why this exists
---------------
The audit's finding about "welded" was not that step bodies are hard to
call - they import, and 64 test files already call them.  It was that
the runner wraps EIGHTEEN services around every step body, all keyed by
the DAG node, and anything invoked outside the DAG gets none of them: no
provenance, no ledger entry, no gate, no hollow-output check, no
handbrake, no run status.

So a capability layer that merely exposes functions produces a
second-class, unrecorded execution path running beside a heavily
instrumented one.  The design's answer is that an operation does not get
its own path - **it is the step, run at a narrower scope**:

    PROJECT   what every step does today, and the default
    CLIP      one clip of one step, which `--rerun step:clip_007`
              already addresses
    REGION    an interval of the timeline, which nothing addressed

`PROJECT` is the default everywhere, so every existing run is unchanged
by the existence of this type.

The reel convergence
--------------------
The captain ruled that the reel work adopts this rather than building
its own mechanism, and the two really are one problem: **a reel is a set
of ranges, and a region is a range.**  A step that can run against a
region can run against a reel timeline, which is what
"a step needs to be able to run against a reel timeline the same way it
runs against a master" asks for.  Do not add a reel-specific scope kind;
add ranges.

What this module deliberately does NOT do
-----------------------------------------
It does not decide what a step does differently at a narrower scope.
That belongs to the step, and to the operation that names it.  This type
carries the address and refuses an incoherent one - nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from library.tools.region import Region, parse as parse_region

PROJECT = "project"
CLIP = "clip"
REGION = "region"
REEL = "reel"

KINDS = (PROJECT, CLIP, REGION, REEL)

# What each kind means, for the CLI's help and the generated skill.  A
# reader who has only this table must be able to choose correctly.
KIND_LEGEND = {
    PROJECT: ("the whole project, which is what every step does today "
              "and what a run with no scope means"),
    CLIP: ("one clip of one step, addressed by its catalog id "
           "(clip_007) - the granularity `--rerun step:clip` already has"),
    REGION: ("an interval of ONE timeline, in seconds (45.0-72.0, or "
             "reel_03@45.0-72.0) - the axis nothing had, and the one the "
             "captain's regenerate-this-segment example needs"),
    REEL: ("one reel: its keep ranges in play order, all on the reel's "
           "own timeline. NOT a region - a reel is a LIST of spans and "
           "its time base is its kept ranges laid end to end"),
}


class ScopeError(ValueError):
    """A scope that does not describe anything runnable."""


@dataclass(frozen=True)
class Scope:
    """The address an operation runs against.

    Construct through `project()`, `clip()` or `region()` rather than
    directly: each refuses the combinations that do not mean anything,
    and a scope that carries the wrong payload for its kind is the
    defect this type exists to make impossible.
    """

    kind: str
    region_span: Optional[Region] = None
    clip_id: Optional[str] = None
    reel_ranges: Optional[tuple] = None
    """A REEL's keep ranges, in PLAY ORDER, all on the reel's own
    timeline.

    A reel is NOT a REGION, for two independent reasons.  Cardinality: a
    reel is a LIST of keep ranges with its bad takes cut out
    (`reel_build.keep_ranges`), where a REGION is one span.  And time
    base: the ranges are addressed on the reel's timeline, which is its
    kept ranges laid end to end, so the same number means a different
    moment than it does on the master.  Bolting either onto REGION would
    make one of the two silent."""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ScopeError(
                f"unknown scope kind {self.kind!r}; known: {', '.join(KINDS)}")

        # Each kind carries exactly its own payload.  A REGION scope with
        # no region is the one that would read as "the whole project" and
        # silently redo everything, so it is refused by name.
        if self.kind == REGION and self.region_span is None:
            raise ScopeError(
                "a REGION scope must carry a region; without one it would "
                "read as the whole project and redo everything")
        if self.kind == CLIP and not self.clip_id:
            raise ScopeError("a CLIP scope must name a clip_id")
        if self.kind == REEL:
            self._check_reel()
        elif self.reel_ranges is not None:
            raise ScopeError(
                f"a {self.kind.upper()} scope carries no reel ranges, but "
                f"{len(self.reel_ranges)} were given; did you mean REEL?")
        if self.kind != REGION and self.region_span is not None:
            raise ScopeError(
                f"a {self.kind.upper()} scope carries no region, but one "
                f"was given ({self.region_span}); did you mean REGION?")
        if self.kind != CLIP and self.clip_id is not None:
            raise ScopeError(
                f"a {self.kind.upper()} scope names no clip, but "
                f"{self.clip_id!r} was given; did you mean CLIP?")

    def _check_reel(self) -> None:
        """Everything a REEL payload has to be, checked rather than assumed."""
        ranges = self.reel_ranges
        if not ranges:
            raise ScopeError(
                "a REEL scope must carry its keep ranges; without them it "
                "would read as the whole project and redo everything")
        if any(not isinstance(r, Region) for r in ranges):
            raise ScopeError(
                "a REEL scope's ranges must be Regions - the timeline they "
                "are on is part of the type, and a bare pair of floats has "
                "lost it")
        timelines = {r.timeline for r in ranges}
        if len(timelines) > 1:
            raise ScopeError(
                f"a REEL's ranges must all be on ONE timeline, got "
                f"{sorted(map(repr, timelines))}. A reel is one timeline "
                f"built from one master; ranges from two of them laid end "
                f"to end are not a reel.")
        # Disjointness, because `reel_build.reel_time` walks the ranges in
        # list order and returns the FIRST containing one.  Overlapping
        # ranges give one second two answers, and the closing CTA - which
        # may sit earlier on the master than the body - is exactly where
        # that would bite.  Order is NOT checked: play order is the point,
        # and a CTA range legitimately precedes the body in time.
        ordered = sorted(ranges, key=lambda r: (r.start, r.end))
        for earlier, later in zip(ordered, ordered[1:]):
            if later.start < earlier.end:
                raise ScopeError(
                    f"a REEL's ranges must not overlap: {earlier} and "
                    f"{later} share time, so reel_build.reel_time would "
                    f"give one master second two answers")

    @property
    def timeline(self):
        """Which timeline this scope addresses, or None where it says nothing."""
        if self.kind == REGION:
            return self.region_span.timeline
        if self.kind == REEL:
            return self.reel_ranges[0].timeline
        return None

    @property
    def is_project(self) -> bool:
        return self.kind == PROJECT

    @property
    def is_region(self) -> bool:
        return self.kind == REGION

    @property
    def is_clip(self) -> bool:
        return self.kind == CLIP

    @property
    def is_reel(self) -> bool:
        return self.kind == REEL

    def __str__(self) -> str:
        if self.kind == REGION:
            return f"region {self.region_span}"
        if self.kind == CLIP:
            return f"clip {self.clip_id}"
        if self.kind == REEL:
            return (f"reel {self.reel_ranges[0].timeline or '<unnamed>'} "
                    f"({len(self.reel_ranges)} ranges)")
        return "project"


def project() -> Scope:
    """The whole project - what every step does today."""
    return Scope(PROJECT)


def clip(clip_id: str) -> Scope:
    """One clip, by its catalog id."""
    return Scope(CLIP, clip_id=clip_id)


def reel(ranges, timeline=None) -> Scope:
    """One reel: its keep ranges, in play order, on the reel's timeline.

    `ranges` are `Region`s, or `(start, end)` pairs plus a `timeline` to
    put them on.  The pairs form exists because `reel_build.keep_ranges`
    returns exactly that today; giving it a timeline here is what stops a
    reel's spans travelling on as bare numbers.
    """
    built = []
    for item in ranges or ():
        if isinstance(item, Region):
            built.append(item)
            continue
        try:
            start, end = item
        except (TypeError, ValueError):
            raise ScopeError(
                f"a reel range is a Region or a (start, end) pair, got "
                f"{item!r}") from None
        built.append(Region(timeline, float(start), float(end)))
    return Scope(REEL, reel_ranges=tuple(built))


def region(span) -> Scope:
    """An interval of the timeline.

    `span` is a `Region`, or the text form `region.parse` reads
    ("45.0-72.0").  Parsing is delegated so this module never becomes a
    second reader of an interval - `library/tools/region.py` is the one
    owner, and the domain is part of that type for a measured reason.
    """
    if isinstance(span, str):
        span = parse_region(span)
    if not isinstance(span, Region):
        raise ScopeError(
            f"a region scope needs a Region or its text form, got "
            f"{type(span).__name__}")
    return Scope(REGION, region_span=span)


def from_cli(project_flag: bool = False, region_text: str = "",
             clip_id: str = "") -> Scope:
    """Build the scope a command line asked for, refusing an ambiguous one.

    Naming two scopes is refused rather than resolved by precedence: a
    caller who wrote both did not mean either, and picking one for them
    is how a region-scoped rerun quietly becomes a project-wide one.
    """
    named = [n for n, v in (("--region", region_text), ("--clip", clip_id))
             if v]
    if len(named) > 1:
        raise ScopeError(
            f"{' and '.join(named)} both name a scope; give one")
    if region_text:
        return region(region_text)
    if clip_id:
        return clip(clip_id)
    return project()
