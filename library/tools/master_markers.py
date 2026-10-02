"""Where each reel was taken from, marked on the master timeline.

The captain's ruling of 2026-09-07: *"Let the pipeline add markers
only"* - the master is read-only except for markers showing where each
reel came from, so episode coverage is visible at a glance when the
project is opened.  Nothing else about it may be touched: not its bin,
not its colour, not its keywords, not its timing.

`GEO Podcast - Synced` is a 44-minute sync and the reels are cut from
it.  Opening the project answers "which reels exist" (the `Reels/<state>`
bins, §4 of `resolve_organization`) and does NOT answer "which parts of
the episode have I already used" - a question on the TIME axis, which no
bin tree can show and a marker can.  Measured on the field test: 48
reels cover **69.5% of the episode**, so nearly a third of it has never
been cut, which is the thing a marker makes visible and nothing else in
the project says at all.

A marker describes a REGION OF THE MASTER, not a reel
-----------------------------------------------------
The first shape was one marker per reel, spanning its first cut to its
last, and it was wrong twice over.  Reels draw from widely separated
parts of the episode - Reel 01 uses frames 3..1073 and 8002..8181 - so
that marker claimed 8,178 frames for a fifty-second reel and buried the
gap.  And 21 of the 48 reels end on the SAME call-to-action clip, which
maps to master frame 7710 for all of them: Resolve keeps ONE marker per
frame, `AddMarker` returns False on an occupied one, and 48 markers
wanted only 22 distinct frames.

So the spans of every reel are merged into DISJOINT regions of the
master and one marker describes each.  That cannot collide by
construction, it says what is actually true of the shared
call-to-action - 21 reels use this region - and it answers the coverage
question directly rather than per reel.

Located by MEASUREMENT, never by name
-------------------------------------
A built reel's timeline name is the plan's moment name plus whatever
suffix the build was given (`" (harvest)"` on the field test), so the
proposal's `timeline_name` and `plan_provenance.built_reels` do not
match and **no name in either names a span on the master**.  Deciding by
prefix is the near-match failure AGENTS.md 5 refuses for project and
timeline names, and `resolve_organization` already declines it for
exactly this data - eight of the field test's timelines are a plan name
plus a hand-typed suffix and are UNRECORDED rather than guessed at.

So the span is measured off the footage instead.  Both the master and
every reel place the same camera files, and a timeline item carries the
file it plays and the SOURCE frame it starts at:

    reel item   (LC4930.MXF, source 3151..3630)
    master item (LC4930.MXF, source 3151..3637) at master frame 594
    => that reel covers master frames 594..1073

An overlap in SOURCE frames on the SAME file is a fact about the
footage, and it holds however the timelines were named.  A reel whose
footage is not on the master is REPORTED, not guessed at - on a
harvest-built reel that would mean it was cut from something else.

Additive, and reversible
------------------------
Every marker this module writes carries the pipeline's own envelope in
`customData` (`library/tools/marker_payload.py`, AGENTS.md 15) with a
`reel_span` record.  That is what makes removal exact: `clear_markers`
deletes the markers whose `customData` this module wrote and leaves
every marker the captain typed, at whatever frame, untouched.  A marker
that could only be removed by colour or by frame would take the
captain's notes with it, and a marker you cannot remove is not additive.

Measured on 21.0.0b.28, on a THROWAWAY project
-----------------------------------------------
- `AddMarker(frame, colour, name, note, duration, customData)` returns
  True, and `GetMarkers()` reads them back with every field.
- **Markers SURVIVE `SaveProject` + `CloseProject` + `LoadProject`.**
  Written, saved, closed, reopened, read back identical.
- `DeleteMarkerAtFrame(frame)` returns True and `GetMarkers()` is empty
  after it, so they are reversible.
- Adding a marker moves **`BtLockableBlob` only** - the row count went
  1 -> 2 and no other table in the project database moved.  In
  particular `Sm2TiTrack`, `Sm2TiItem` and `Sm2TiItem_Sm2TiTrack` are
  untouched, which is why `prune_orphans.timeline_digests` - a digest of
  tracks and items - does not move when a marker is added.  That is the
  measurement the captain asked for: a marker is annotation stored beside
  the edit, not a change to it.

`tests/unit/resolve/test_master_markers.py`.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from library.tools import marker_payload
from library.tools.resolve_organization import CURRENT, EARLIER, UNRECORDED

WRITER = "master_markers"
WRITER_VERSION = 1
KIND_REEL_SPAN = "reel_span"

STATE_MARKER_COLOURS = {
    CURRENT: "Green",
    EARLIER: "Cocoa",
    UNRECORDED: "Blue",
}
"""Which of Resolve's own MARKER colours each recorded state paints.

A LEGEND, not a judgement - the same bargain
`resolve_organization.STATE_CLIP_COLOURS` strikes, and deliberately the
same three readings, so a green marker on the master and a green clip in
`05 - Reels/Current plan` mean the identical thing.  Resolve's marker palette
has no `Brown`, so EARLIER is `Cocoa`; the clip palette has no `Cocoa`.
Nothing reads a colour back as evidence.
"""


class MarkerRefused(Exception):
    """The master cannot be marked from this evidence, so it is not."""


@dataclass(frozen=True)
class SourceWindow:
    """One item, as the span of a FILE it plays and where it sits."""
    file_path: str
    source_in: int
    source_out: int
    timeline_start: int


@dataclass(frozen=True)
class MasterMarker:
    """One marker to write on the master."""
    frame: int
    duration: int
    name: str
    note: str
    colour: str
    custom_data: str

    @property
    def end(self) -> int:
        return self.frame + self.duration


def windows_of(items: Iterable) -> list[SourceWindow]:
    """Read `SourceWindow`s off live timeline items.

    `GetSourceStartFrame()` is the first source frame played and
    `GetEnd() - GetStart()` is how many frames are played, so the source
    span is `[src, src + played)`.  An item with no media pool item -
    a title, a generator - plays no file and is skipped rather than
    given an empty path that would collide with every other one.
    """
    out = []
    for item in items:
        pool_item = item.GetMediaPoolItem()
        if not pool_item:
            continue
        path = pool_item.GetClipProperty("File Path") or ""
        if not path:
            continue
        played = int(item.GetEnd()) - int(item.GetStart())
        source_in = int(item.GetSourceStartFrame())
        out.append(SourceWindow(path, source_in, source_in + played,
                                int(item.GetStart())))
    return out


def locate_on_master(reel: Sequence[SourceWindow],
                     master: Sequence[SourceWindow]) -> list[tuple[int, int]]:
    """Where this reel's footage sits on the master, in master frames.

    An overlap in SOURCE frames on the SAME file, translated into the
    master's own timeline by the offset the master item carries.
    Returns merged, sorted `(start, end)` intervals; empty when none of
    the reel's footage is on the master, which is a real answer and not
    a failure - a reel cut from something else has no span here.
    """
    spans: list[tuple[int, int]] = []
    for cut in reel:
        for window in master:
            if window.file_path != cut.file_path:
                continue
            first = max(cut.source_in, window.source_in)
            last = min(cut.source_out, window.source_out)
            if first >= last:
                continue
            offset = window.timeline_start - window.source_in
            spans.append((first + offset, last + offset))
    return merge(spans)


def merge(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlapping and touching spans, joined."""
    out: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


STATE_PRECEDENCE = (CURRENT, EARLIER, UNRECORDED)
"""Which state a region takes when reels of several states share it.

An ORDERING of recorded facts, not a preference: a region a reel in the
LIVE plan uses is in the live plan, whatever else also uses it, and that
is the strongest true statement about it.  The note names every reel and
its own state, so nothing is hidden by the colour.
"""


@dataclass(frozen=True)
class Region:
    """One disjoint stretch of the master, and who uses it."""
    start: int
    end: int
    reels: tuple[tuple[str, str], ...]   # (reel name, its state)

    @property
    def state(self) -> str:
        states = {state for _name, state in self.reels}
        for candidate in STATE_PRECEDENCE:
            if candidate in states:
                return candidate
        raise MarkerRefused(
            f"region {self.start}..{self.end} is used by "
            f"{len(self.reels)} reel(s) with no recorded state: "
            f"{sorted(states)!r}.")


def coverage_regions(
        per_reel: Sequence[tuple[str, str, Sequence[tuple[int, int]]]],
) -> list[Region]:
    """Every reel's spans, merged into disjoint regions of the master.

    `per_reel` is `(reel name, state, spans)`.  Two reels that use
    overlapping footage produce ONE region naming both, which is what
    lets the shared call-to-action be one marker saying "21 reels" and
    not twenty-one markers fighting for one frame.
    """
    spans = [(start, end) for _n, _s, ss in per_reel for start, end in ss]
    out = []
    for start, end in merge(spans):
        users = tuple(sorted(
            (name, state) for name, state, ss in per_reel
            if any(a < end and b > start for a, b in ss)))
        if not users:
            raise MarkerRefused(
                f"region {start}..{end} was merged from the reels' own "
                f"spans and now matches none of them.")
        out.append(Region(start, end, users))
    return out


def marker_for(region: Region, master_end: int) -> MasterMarker:
    """The one marker describing one region of the master."""
    if region.start < 0 or region.end > master_end:
        raise MarkerRefused(
            f"region {region.start}..{region.end} falls outside the "
            f"master, which is 0..{master_end}. Nothing was marked.")
    names = [name for name, _state in region.reels]
    name = (names[0] if len(names) == 1
            else f"{len(names)} reels use this")
    note = "\n".join(
        [f"{region.end - region.start} frames of this episode, used by:"]
        + [f"  {reel} [{state}]" for reel, state in region.reels])
    envelope = marker_payload.merge_record(marker_payload.new_envelope(), {
        "kind": KIND_REEL_SPAN,
        "writer": WRITER,
        "writer_version": WRITER_VERSION,
        "id": marker_payload.new_id("reelspan"),
        "at": marker_payload.utc_now(),
        "start": region.start,
        "end": region.end,
        "reels": [{"reel": r, "state": s} for r, s in region.reels],
    })
    return MasterMarker(
        frame=region.start,
        duration=max(1, region.end - region.start),
        name=name,
        note=note,
        colour=STATE_MARKER_COLOURS[region.state],
        custom_data=marker_payload.dumps(envelope))


def is_ours(custom_data: str) -> bool:
    """Did THIS module write this marker?

    The whole basis of removal being exact.  A marker the captain typed
    has no envelope, or an envelope with no `reel_span` record from this
    writer, and either way it is left alone.
    """
    try:
        envelope = marker_payload.parse(custom_data or "")
    except (ValueError, TypeError):
        return False
    return any(record.get("writer") == WRITER
               for record in marker_payload.records_of(
                   envelope, KIND_REEL_SPAN))


def assert_no_collision(planned: Sequence[MasterMarker],
                        existing: dict) -> None:
    """Refuse to write where the captain already has a marker.

    Resolve keeps ONE marker per frame: `AddMarker` at an occupied frame
    returns False, and there is no version of this that overwrites the
    captain's note safely.  A collision is reported rather than resolved
    by nudging the frame, because a marker moved to a free frame no
    longer says where the reel came from.
    """
    theirs = {frame for frame, marker in existing.items()
              if not is_ours(marker.get("customData", ""))}
    clashes = sorted(m.frame for m in planned if m.frame in theirs)
    if clashes:
        raise MarkerRefused(
            f"{len(clashes)} planned marker(s) land on a frame the captain "
            f"already has a marker on: {clashes[:5]}. Resolve keeps one "
            f"marker per frame and AddMarker returns False there. Nothing "
            f"was marked.")
