"""What a rendered subtitle segment is CALLED, and what that name binds.

The defect this closes
----------------------
Step 4.05 named every rendered overlay `sub_block_<block_position>.mov`
and wrote them all into one directory,
`Area.SUBTITLE_SEGMENTS`, which is per PROJECT and not per timeline.  The
captain reported it in their own words (2026-09-04):

    "i think there is a little bug with the way remotion subtitles are
     named where they tend to write over one another because it'll order
     numerically for the single timeline but have no other way of syncing
     the subtitles with its respective audio segment"

Both halves are real and they are different failures:

- **Collision.**  `block_position` is an ordinal within ONE spine.  A
  master timeline and a reel both have a `body_1`, so the reel's render
  silently overwrites the master's.  With one timeline this is latent;
  with a master plus N reels it destroys real output on every run.
- **No binding.**  The name says which slot of which spine it filled and
  nothing about WHOSE speech it captions or WHICH span of source audio it
  was transcribed from.  A file that cannot say what it belongs to cannot
  be checked against anything, and a wrong-but-plausible pairing is
  invisible.

The fix is not a bigger number
------------------------------
An ordinal with more digits, a timestamp or a counter would fix the
collision and leave the binding missing - and a name that is unique but
means nothing is exactly how the wrong caption ends up over the right
audio with nothing able to detect it.  So the identifier BINDS, and the
binding is the identity:

    speaker  +  timeline  +  the source audio span it was read from

`source audio span` is the ground truth - the clip and the seconds inside
it, as `library/tools/timeline_ingest.py` reads them off the timeline and
as `spine_contract` already requires every block to carry.  Two segments
that agree on all three ARE the same segment; anything that differs in
any of them gets a different name.

Readable first, then proof
--------------------------
The name is human-readable so a directory listing is legible, and ends
with a short digest of the WHOLE binding so that two segments which
happen to slug identically still cannot collide.  The digest is over the
exact binding tuple, so it is stable across runs and machines - a rebuild
of the same segment overwrites itself, which is correct, and never
overwrites a different one, which is the bug.

`SEGMENT_BINDING_KEYS` is the enumeration.  A caller that cannot supply
one of them passes `None` and the name says so (`nospeaker`, `noclip`)
rather than omitting the field, because a name that silently drops a
component collides with one that never had it.

`tests/test_subtitle_segment_id.py`.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

SEGMENT_BINDING_KEYS = (
    "timeline",
    "speaker",
    "block_position",
    "source_clip_id",
    "source_start",
    "source_end",
)
"""Everything a rendered segment's identity is made of. Complete."""

_DIGEST_CHARS = 8
_SLUG_MAX = 32


def slug(value: Optional[object], absent: str) -> str:
    """A filename-safe slug, or `absent` when there is nothing to slug.

    `absent` is REQUIRED rather than defaulted so that every caller has
    to decide what the absence of that component is called.  Two missing
    components must not both become the empty string, or a name loses the
    very distinction it exists to carry.
    """
    if value is None:
        return absent
    text = str(value).strip().lower()
    if not text:
        return absent
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        return absent
    return text[:_SLUG_MAX].rstrip("-")


def _span_token(source_start: Optional[float],
                source_end: Optional[float]) -> str:
    """The source audio span, in milliseconds, as a name component.

    Milliseconds rather than seconds because a float in a filename is
    ambiguous across locales and rounds differently on different
    platforms; an integer count of milliseconds does not.
    """
    if source_start is None or source_end is None:
        return "nospan"
    return f"{int(round(float(source_start) * 1000))}-" \
           f"{int(round(float(source_end) * 1000))}"


def segment_binding(timeline: Optional[str],
                    speaker: Optional[str],
                    block_position: Optional[object],
                    source_clip_id: Optional[str],
                    source_start: Optional[float],
                    source_end: Optional[float]) -> dict:
    """The binding, as plain data, recorded alongside the rendered file.

    The name is derived from this and is lossy - it slugs and truncates.
    This is the unabridged version, so a reader can check a segment
    against its audio without parsing a filename.
    """
    return {
        "timeline": timeline,
        "speaker": speaker,
        "block_position": block_position,
        "source_clip_id": source_clip_id,
        "source_start": source_start,
        "source_end": source_end,
    }


def binding_digest(binding: dict) -> str:
    """A stable digest of the whole binding.

    Over a canonical string of the binding in `SEGMENT_BINDING_KEYS`
    order, so it does not move when a dict happens to iterate
    differently.  Floats are formatted to microseconds: enough to
    separate any two real spans, coarse enough that a float that
    round-trips through JSON does not change the digest.
    """
    parts = []
    for key in SEGMENT_BINDING_KEYS:
        value = binding.get(key)
        if isinstance(value, float):
            parts.append(f"{key}={value:.6f}")
        else:
            parts.append(f"{key}={value!r}")
    canonical = "|".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


class SegmentNameCollision(ValueError):
    """Two segments would be written to one filename."""


class UnnamedReelTimeline(ValueError):
    """A reel segment was named without saying which reel it belongs to."""


def assert_named_timeline(binding: dict, where: str = "") -> None:
    """Refuse a binding whose timeline is empty, where one is REQUIRED.

    The master timeline may legitimately be unnamed - a project with one
    timeline needs no discriminator, and `slug` renders it `notimeline`.
    A REEL may not.  The captain's format closes every reel on a spoken
    call to action taken from anywhere in the episode, so several reels
    legitimately carry the SAME `source_clip_id` and the same source
    span for their closer; measured on the field test, seven of nineteen
    close on one identical sentence and five more on another.  With no
    timeline in the name those twelve segments are one filename.

    The failure is silent rather than loud.  Reel 09 re-renders over reel
    03's file, reel 03's manifest still points at that path, and
    `compile_manifest._assert_subtitle_overlay_matches_plan` passes -
    it compares block position and time span and reads no content.  The
    captain would see the wrong words on screen and every check green.

    An empty discriminator silently disabling uniqueness is the same
    shape as an empty expected-side making a comparison vacuous, so it
    is refused rather than measured.
    """
    if not slug(binding.get("timeline"), "").strip():
        raise UnnamedReelTimeline(
            f"{where or 'segment'}: a reel segment must name its timeline. "
            f"Its binding is "
            f"{ {k: binding.get(k) for k in SEGMENT_BINDING_KEYS} }, and "
            f"with an empty timeline every reel sharing this source span "
            f"writes to one filename - which on the captain's format is "
            f"most of them, because every reel closes on a call to action "
            f"taken from anywhere in the episode. Pass the reel's name "
            f"through `timeline_scope`."
        )


def assert_unique_segment_names(bindings) -> None:
    """Refuse a SET of bindings in which two produce one filename.

    The general guard, and the one that does not depend on knowing which
    segments are reels.  A collision is a collision whatever caused it -
    a shared closer, two speakers resolving to the same slug, a block
    position repeated across timelines - and this refuses all of them
    before a single file is written.

    Names the two bindings and what differs, because "duplicate segment
    name" sends a reader to the wrong place: the name is a symptom and
    the binding is the cause.
    """
    seen = {}
    for binding in bindings:
        name = segment_identifier(binding)
        if name in seen:
            first = seen[name]
            differing = sorted(
                k for k in SEGMENT_BINDING_KEYS
                if binding.get(k) != first.get(k))
            raise SegmentNameCollision(
                f"two subtitle segments would be written to "
                f"{name!r}.\n"
                f"  first:  { {k: first.get(k) for k in SEGMENT_BINDING_KEYS} }\n"
                f"  second: { {k: binding.get(k) for k in SEGMENT_BINDING_KEYS} }\n"
                f"  binding keys that differ: {differing or 'NONE - the two '
                'bindings are identical, so one of them is addressed wrong'}\n"
                f"The second render would overwrite the first, and nothing "
                f"downstream compares content: "
                f"_assert_subtitle_overlay_matches_plan checks block "
                f"position and time span only."
            )
        seen[name] = binding


def segment_identifier(binding: dict) -> str:
    """The name a rendered subtitle segment is written under.

    Shape: `sub_<timeline>_<speaker>_<block>_<span_ms>_<digest>`.
    Readable left to right, unique on the right.

    Unique WITHIN ONE TIMELINE.  Across timelines the discriminator is
    the timeline component, and an empty one collapses them - see
    `assert_named_timeline` and `assert_unique_segment_names`, which are
    the two guards that make that unrepresentable rather than merely
    documented.
    """
    missing = [k for k in SEGMENT_BINDING_KEYS if k not in binding]
    if missing:
        raise ValueError(
            f"segment binding is missing {missing}. Every component is "
            f"required - pass None for one the edit does not provide, so "
            f"the name records the absence instead of dropping it. "
            f"SEGMENT_BINDING_KEYS is the whole list.")
    return "_".join((
        "sub",
        slug(binding["timeline"], "notimeline"),
        slug(binding["speaker"], "nospeaker"),
        slug(binding["block_position"], "noblock"),
        _span_token(binding["source_start"], binding["source_end"]),
        binding_digest(binding),
    ))


def timeline_scope(audio_spine: Optional[dict],
                   project_config=None,
                   fallback: str = "") -> str:
    """Which timeline a render belongs to, and where that was learned.

    Preference order, most specific first:

    1. `audio_spine["derived_from"]["timeline"]` - present when the spine
       was measured off a real timeline by `timeline_ingest`, and the
       only source that is a MEASUREMENT rather than a declaration.
    2. the project's declared `resolve.timeline_name`.
    3. `fallback`.

    Returns the empty string when none of them says anything, which
    `segment_identifier` renders as `notimeline`.  It does not invent a
    name: an unnamed timeline that collides is a visible bug, and a
    made-up name that does not is a silent one.
    """
    if isinstance(audio_spine, dict):
        derived = audio_spine.get("derived_from")
        if isinstance(derived, dict):
            name = derived.get("timeline")
            if name:
                return str(name)
    declared = getattr(getattr(project_config, "resolve", None),
                       "timeline_name", "")
    if declared:
        return str(declared)
    return fallback
