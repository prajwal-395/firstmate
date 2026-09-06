"""A region of the timeline, and the one owner of the domain conversion.

What this exists to make possible
---------------------------------
The captain, on what the pipeline could not do:

    "Regenerate just this small segment of subtitles.  Inspect the region
     of the timeline I said was wrong, go back to the raw footage and
     assets, re-index that specific region ... and splice the refreshed
     subtitles back in."

Every artifact that segment touches is already a list of interval-stamped
entries - the per-clip index's `speech_regions`, the spine's blocks, the
subtitle plan's entries, the rendered overlay segments.  None of them is
opaque.  What was missing was an ADDRESS: nothing in the pipeline accepted
an interval as an argument, and `--rerun` takes a stage, a step or a
step:clip and no fourth thing.  "45.0-72.0s" could be read by a human and
by nothing else.

`Region` is that address, and `resolve` is what turns it into the blocks,
the source spans and the owning steps behind it.

Why the domain is part of the type
----------------------------------
**This module is a defect guard first and a convenience second.**

Word timings are written five ways across the pipeline and two of them
COLLIDE: `temporal_index...speech_regions[].words[]` and
`subtitle_entries[].words[]` both use the keys `start` and `end`, for
DIFFERENT time domains.  The obvious implementation of the captain's
splice - read a word out of the index, write it into a caption - is
structurally valid, type-checks, round-trips through JSON, and is wrong.

Measured on project 001, the same spoken word "i" in both artifacts:

    1.04 index    start=0.836  end=0.872     SOURCE domain
    4.01 captions start=0.000  end=0.036     TIMELINE domain

0.836s apart - 25 frames at 30fps - on the very first word of the video.

**And there is no single correction, because the offset is per block.**
001's eight speech-bearing blocks have eight distinct offsets spread over
146.5 seconds of a 56.6-second timeline, and the sign is not constant:
seven blocks are cut from a source second LATER than the timeline second
they play at, and the eighth is cut from an earlier one.  So the error is
not a uniform drift a reviewer might spot - it is wrong in both
directions, by up to 129.3 seconds.  Nothing about eyeballing one block
generalises to the next.

Key-name mismatches are the dominant bug class this repository names
(AGENTS.md 10.1).  This is that class with a 129-second blast radius, so
the domain is carried by the type rather than by a convention:

- `Region` is TIMELINE seconds, always.  It has one domain and no flag.
- A word list crossing a domain boundary goes through `to_timeline_words`
  or `to_source_words`, which need a block and cannot be called without
  one.
- `assert_domain` REFUSES a word list whose keys name no domain.

That last one refuses rather than classifies, and that is deliberate:
1.04's and 4.01's word entries are structurally identical, so nothing can
tell them apart by inspection.  A guard that tried to sniff the domain
would pass both or fail both.  The spine already solves this properly by
naming the domain in the key (`source_start`/`source_end`), and it is the
only one of the three this guard can accept.  **Renaming 1.04's and
4.01's word keys the way the spine already does is the real fix**; until
then a caller must say which domain it is holding, and saying nothing is
an error rather than a default.

The conversions themselves live in `library/tools/spine_contract.py`,
beside the contract they read - `source_to_timeline` and its inverse
`timeline_to_source` - because the spine block is the only structure that
carries both domains and is therefore the only place either is
computable.

`tests/test_region.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from library.tools.spine_contract import (
    blocks_overlapping,
    source_to_timeline,
    timeline_to_source,
)

TIMELINE = "timeline"
SOURCE = "source"
DOMAINS = (TIMELINE, SOURCE)
"""The two time domains in this pipeline.  Complete.

TIMELINE is a second of the delivered edit, measured from its first frame.
SOURCE is a second of a raw clip, measured from that file's own start.
A time is in one or the other and there is no third."""

DOMAIN_WORD_KEYS = {
    SOURCE: ("source_start", "source_end"),
    TIMELINE: ("timeline_start", "timeline_end"),
}
"""How a word entry NAMES its domain.  The spine already writes the source
pair; nothing yet writes the timeline pair, which is why 4.01's captions
are the half of the collision that still has to be declared by its
caller."""

_BARE_KEYS = ("start", "end")


class DomainError(ValueError):
    """A time was used in a domain it does not belong to.

    Raised rather than converted, because a wrong guess here is silent:
    the number is plausible in both domains and the picture only shows it
    at render time, one caption block out of thirty.
    """


# ── The address ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceSpan:
    """The footage behind one block's share of a region.

    `source_start`/`source_end` are seconds inside `clip_id`'s own file,
    already clipped to the region - so a region covering half a block
    names half that block's footage, not all of it.
    """

    clip_id: str
    source_start: float
    source_end: float
    block_position: object


@dataclass(frozen=True)
class RegionAddress:
    """What a region resolves to against one project's spine.

    `blocks` carries the spine blocks THEMSELVES, not just their
    positions, because there is no project-wide offset: a caller holding
    a time and no block cannot convert it (see `timeline_to_source`).
    Handing back positions alone would force every caller to look the
    block up again, and the lookup is exactly where the domain error gets
    made.
    """

    region: Region
    blocks: tuple[dict, ...]
    source_spans: tuple[SourceSpan, ...]
    owners: tuple[tuple[str, str], ...]
    """`(track, node_id)` for every track that carries something here,
    from `timeline_decisions.TRACK_DECISIONS` - the enumeration that
    already answers "which step decided what plays on this track"."""

    @property
    def positions(self) -> list[object]:
        return [b["position"] for b in self.blocks]

    @property
    def clip_ids(self) -> list[str]:
        seen = []
        for span in self.source_spans:
            if span.clip_id not in seen:
                seen.append(span.clip_id)
        return seen


MASTER = None
"""The project's own master timeline, which most projects never name.

`None` rather than a string like `"master"`, because a name is a thing a
project DECLARES and inventing one here would make an unnamed timeline
indistinguishable from a project that really has a timeline called
master.  `subtitle_segment_id.timeline_scope` already reports an unnamed
timeline as `""`; `normalise_timeline` folds that to this."""


def normalise_timeline(timeline):
    """`""` and `None` both mean the unnamed master timeline."""
    if timeline is None:
        return MASTER
    text = str(timeline).strip()
    return text or MASTER


class TimelineMismatch(ValueError):
    """Two regions from different timelines were used together."""


def assert_same_timeline(a: Region, b, where: str) -> None:
    """Refuse a master-timeline region where a reel one is expected, or back.

    `b` is a `Region` or a bare timeline identifier.
    """
    other = b.timeline if isinstance(b, Region) else normalise_timeline(b)
    if a.timeline != other:
        raise TimelineMismatch(
            f"{where}: this region is on timeline {a.timeline!r} and the "
            f"other side is on {other!r}. A reel has its own time base, "
            f"mapped from the master by reel_build.reel_time, so the same "
            f"number means two different moments - the offsets on project "
            f"001 spread 146.5s across one 56.6s timeline. Convert, or "
            f"address the timeline you meant."
        )


@dataclass(frozen=True)
class Region:
    """An interval of ONE NAMED TIMELINE, in seconds.

    Always TIMELINE seconds - the type has one time DOMAIN and carries no
    flag saying which, because a flag is a thing a caller can set wrongly.
    And always ONE timeline, for the same reason one level up.

    Why the timeline is in the type
    -------------------------------
    This module exists because an interval is encoded four ways in this
    repository and word timings five, two of which collide - measured per
    block on project 001 at eight distinct offsets, seven negative and
    one positive, spread 146.5s on a 56.6s timeline.  The fix was that
    the domain belongs to the TYPE rather than to a convention.

    A master-timeline region and a reel-timeline region are that same
    collision one level up.  A reel is its keep ranges laid end to end
    (`reel_build.reel_time`), so reel second 12.0 and master second 12.0
    are different moments, and nothing about the numbers says so.  Were
    the timeline carried by `Scope` instead, a bare `Region` passed
    between functions - which is most of the region surface - would have
    lost it, and a reel region could be handed where a master one is
    expected with nothing to catch it.  Here, the mistake is
    unrepresentable rather than discouraged.

    `timeline` is `MASTER` (None) for the project's own timeline, or the
    timeline's declared name.  It is REQUIRED and FIRST: a caller has to
    have decided which timeline they mean before they can build one.

    Half-open, `[start, end)` - the same reading `blocks_overlapping`
    uses, so a region that ends exactly where a block does does not pull
    the next block in.
    """

    timeline: object
    start: float
    end: float

    def __post_init__(self):
        object.__setattr__(self, "timeline",
                           normalise_timeline(self.timeline))
        if self.start < 0:
            raise ValueError(
                f"region start {self.start} is negative. A region is "
                f"timeline seconds measured from the first frame."
            )
        if self.end < self.start:
            raise ValueError(
                f"region end {self.end} precedes start {self.start}. "
                f"An interval is (start, end), both in timeline seconds."
            )

    @property
    def duration(self) -> float:
        return self.end - self.start

    def blocks(self, structure: list) -> list[dict]:
        """The spine blocks this region touches, in spine order."""
        return blocks_overlapping(structure, self.start, self.end)

    def contains(self, timeline_time: float) -> bool:
        if self.end == self.start:
            return timeline_time == self.start
        return self.start <= timeline_time < self.end

    def clipped_to(self, block: dict) -> Region:
        """This region narrowed to one block's own timeline span."""
        return Region(self.timeline,
                      max(self.start, block["timeline_start"]),
                      min(self.end, block["timeline_end"]))

    def on_same_timeline_as(self, other) -> bool:
        other_tl = (other.timeline if isinstance(other, Region)
                    else normalise_timeline(other))
        return self.timeline == other_tl

    def as_address(self) -> str:
        """This region written the way `parse` reads it back.

        `__str__` is for a HUMAN - it ends in `s` so a reader knows the
        numbers are seconds - and `parse` refuses that trailing letter,
        because `float("48s")` is not a number and guessing would be the
        silent mis-read this module exists to stop.

        So the two forms are different on purpose, and this is the one
        that goes into a command a reader is meant to copy. A refusal
        that prints a command which then refuses is a worse control
        surface than no command at all.
        `tests/test_region.py::test_an_address_parses_back_to_the_region`
        pins the round trip.
        """
        span = f"{self.start:g}-{self.end:g}"
        return span if self.timeline is MASTER else f"{self.timeline}@{span}"

    def __str__(self) -> str:
        span = f"{self.start:g}-{self.end:g}s"
        return span if self.timeline is MASTER else f"{self.timeline}@{span}"


def parse(text: str, timeline=MASTER) -> Region:
    """A region written the way an operator types it: ``45.0-72.0``.

    Accepts `<start>-<end>` in seconds, optionally prefixed with the
    timeline it is on: ``reel_03@45.0-72.0``.  A prefix and an explicit
    `timeline` that DISAGREE are refused rather than one winning - the
    silent winner is how a reel span gets read against the master.

    Raises on anything else rather than guessing, because the two
    plausible mis-readings - a single number meaning "from here" and a
    negative start - are both silent.
    """
    raw = (text or "").strip()
    if "@" in raw:
        prefix, _, raw = raw.partition("@")
        prefix = normalise_timeline(prefix)
        given = normalise_timeline(timeline)
        if given is not MASTER and given != prefix:
            raise TimelineMismatch(
                f"region {text!r} names timeline {prefix!r} but was asked "
                f"for on {given!r}. Pass one or the other, not two.")
        timeline = prefix
    if not raw:
        raise ValueError(
            "a region needs <start>-<end> in timeline seconds, e.g. 45.0-72.0")
    head, sep, tail = raw.partition("-")
    if not sep or not head.strip() or not tail.strip():
        raise ValueError(
            f"region {text!r}: expected <start>-<end> in timeline seconds, "
            f"e.g. 45.0-72.0")
    try:
        start, end = float(head), float(tail)
    except ValueError:
        raise ValueError(
            f"region {text!r}: both ends must be seconds, e.g. 45.0-72.0"
        ) from None
    return Region(timeline, start, end)


def resolve(region: Region, structure: list, timeline=MASTER) -> RegionAddress:
    """Turn a region into the blocks, footage and owning steps behind it.

    `timeline` is the timeline `structure` describes, and a region from a
    different one is REFUSED rather than resolved against the wrong
    spine.  It is a parameter rather than something read off the blocks
    because a spine block carries no timeline identifier - the spine is
    whichever timeline its producer built.

    A non-speech block inside the region - a transition slot, an outro, a
    bookend card - contributes no source span, because it has no
    `clip_id` to name one.  It is still reported in `blocks`: it occupies
    the region and a caller deciding what to re-run needs to know it is
    there.  Dropping it would make a region that is half transition look
    like a region that is all speech.
    """
    from library.tools.timeline_decisions import TRACK_DECISIONS

    assert_same_timeline(region, timeline, "resolve")
    touched = tuple(region.blocks(structure))
    spans = []
    for block in touched:
        if block["clip_id"] is None:
            continue
        window = region.clipped_to(block)
        spans.append(SourceSpan(
            clip_id=block["clip_id"],
            source_start=round(timeline_to_source(window.start, block), 3),
            source_end=round(timeline_to_source(window.end, block), 3),
            block_position=block["position"],
        ))
    owners = tuple((d.track, d.decided_by) for d in TRACK_DECISIONS)
    return RegionAddress(region=region, blocks=touched,
                         source_spans=tuple(spans), owners=owners)


def owner_of_track(track: str) -> str | None:
    """The DAG node whose decision fills a track, or None if unlisted."""
    from library.tools.timeline_decisions import BY_TRACK

    decision = BY_TRACK.get(track)
    return decision.decided_by if decision else None


# ── The domain guard ─────────────────────────────────────────────────

def domain_of(words: list) -> str | None:
    """Which domain a word list NAMES, or None when it names none.

    None is the answer for 1.04's and 4.01's entries alike - they use
    bare `start`/`end` - and it is the honest one.  Callers must not read
    None as "probably source": that reading is the bug this module
    exists for.
    """
    if not words:
        return None
    keys = set(words[0])
    for domain, (lo, hi) in DOMAIN_WORD_KEYS.items():
        if lo in keys and hi in keys:
            return domain
    return None


def assert_domain(words: list, expected: str, where: str) -> None:
    """Refuse a word list that is not demonstrably in `expected`.

    An empty list passes: it carries no times, so it cannot carry them in
    the wrong domain.
    """
    if expected not in DOMAINS:
        raise DomainError(f"unknown domain {expected!r}. Known: {list(DOMAINS)}.")
    if not words:
        return
    found = domain_of(words)
    if found is None:
        keys = sorted(set(words[0]))
        bare = all(k in words[0] for k in _BARE_KEYS)
        raise DomainError(
            f"{where}: word entries name no time domain (keys: {keys}). "
            + ("They use bare 'start'/'end', which the temporal index uses "
               "for SOURCE seconds and the subtitle plan uses for TIMELINE "
               "seconds - on project 001's first word those differ by "
               "0.836s. Declare the domain with region.read_words(words, "
               "region.SOURCE|region.TIMELINE) instead of passing them on."
               if bare else
               f"Expected {list(DOMAIN_WORD_KEYS[expected])}.")
        )
    if found != expected:
        raise DomainError(
            f"{where}: expected {expected}-domain words but the entries name "
            f"{found}. Convert with region.to_{expected}_words(words, block) "
            f"rather than renaming the keys - the two differ by the block's "
            f"own offset, which is per block and not a constant."
        )


def read_words(words: list, domain: str) -> list[dict]:
    """Take a bare `start`/`end` word list and NAME its domain.

    The single boundary where an undeclared word list becomes a declared
    one.  `domain` is the caller's statement about what it is holding -
    `SOURCE` for the temporal index, `TIMELINE` for the subtitle plan -
    and there is no default, because the whole failure mode is a caller
    that never thought about it.

    Already-named entries are returned unchanged when they agree and
    refused when they do not.
    """
    if domain not in DOMAINS:
        raise DomainError(f"unknown domain {domain!r}. Known: {list(DOMAINS)}.")
    if not words:
        return []
    named = domain_of(words)
    if named is not None:
        assert_domain(words, domain, "read_words")
        return [dict(w) for w in words]
    lo, hi = DOMAIN_WORD_KEYS[domain]
    out = []
    for word in words:
        if not all(k in word for k in _BARE_KEYS):
            raise DomainError(
                f"read_words: entry {word!r} has neither 'start'/'end' nor "
                f"{list(DOMAIN_WORD_KEYS[domain])}."
            )
        entry = {k: v for k, v in word.items() if k not in _BARE_KEYS}
        entry[lo] = word["start"]
        entry[hi] = word["end"]
        out.append(entry)
    return out


def to_timeline_words(words: list, block: dict) -> list[dict]:
    """Convert SOURCE-domain word entries into TIMELINE-domain ones.

    Needs the block and cannot be called without one, which is the point:
    there is no project-wide offset to apply.
    """
    assert_domain(words, SOURCE, "to_timeline_words")
    lo, hi = DOMAIN_WORD_KEYS[TIMELINE]
    out = []
    for word in words:
        entry = {k: v for k, v in word.items()
                 if k not in DOMAIN_WORD_KEYS[SOURCE]}
        entry[lo] = source_to_timeline(word["source_start"], block)
        entry[hi] = source_to_timeline(word["source_end"], block)
        out.append(entry)
    return out


def to_source_words(words: list, block: dict) -> list[dict]:
    """Convert TIMELINE-domain word entries into SOURCE-domain ones."""
    assert_domain(words, TIMELINE, "to_source_words")
    lo, hi = DOMAIN_WORD_KEYS[SOURCE]
    out = []
    for word in words:
        entry = {k: v for k, v in word.items()
                 if k not in DOMAIN_WORD_KEYS[TIMELINE]}
        entry[lo] = timeline_to_source(word["timeline_start"], block)
        entry[hi] = timeline_to_source(word["timeline_end"], block)
        out.append(entry)
    return out
