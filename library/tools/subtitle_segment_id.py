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
audio with nothing able to detect it.

The second fix, 2026-09-10: the name used to carry the TIMELINE as the
discriminator, and three variant timelines captioning the same words
rendered the same pixels three times - 67 caption movs holding 23
unique byte-contents on the captain's project.  The captain's ruling
roots the identity in PROVENANCE, never in placement: a subtitle render
is named for the SOURCE FOOTAGE it came from
(`PROVENANCE_STEM_KEYS`), and the content digest beside it decides
reuse.  Two artefacts that differ in any drawing input digest
differently and cannot collide; what shares a filename IS the same
pixels, which is the sharing.  The timeline survives in the recorded
binding - which placement this file serves - never in the filename.

Readable first, then proof
--------------------------
The name is human-readable so a directory listing is legible, and ends
with a short digest of the DRAWING INPUTS so that two segments which
happen to stem identically still cannot collide unless their pixels
agree.  A rebuild of the same segment overwrites itself, which is
correct, and never overwrites a different one, which was the bug.

`SEGMENT_BINDING_KEYS` is the placement record.  A caller that cannot
supply one of them passes `None` and the record says so (`nospeaker`,
`noclip`) rather than omitting the field, because a record that
silently drops a component collides with one that never had it.

`tests/test_subtitle_segment_id.py`.
"""

from __future__ import annotations

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
"""Everything a rendered segment's placement record is made of. Complete.

The timeline stays IN the record - it says which placing this file
serves - and stays OUT of the filename. See `PROVENANCE_STEM_KEYS`.
"""

PROVENANCE_STEM_KEYS = (
    "speaker",
    "source_clip_id",
    "source_start",
    "source_end",
)
"""What a rendered segment's filename is rooted in: the source footage.

The captain's ruling of 2026-09-09 - he wants to look at a file and
know where it came from, "root it in the source footage it came from
(for subtitles) ... to be able to trace it and not the timeline".
`timeline` and `block_position` are placement - WHEN and WHERE this
file is placed - and placement never names a file. Two placements
captioning the same source span with the same words compute the same
stem; whether they share the file is then decided by the content
digest beside it, which is the sharing rather than the collision.
"""

_SLUG_MAX = 32


def slug(value: Optional[object], absent: str) -> str:
    """A filename-safe slug, or `absent` when there is nothing to slug.

    `absent` is REQUIRED rather than defaulted so that every caller has
    to decide what the absence of that component is called.  Two missing
    components must not both become the empty string, or a name loses the
    very distinction it exists to carry.

    A slug longer than `_SLUG_MAX` is cut at the last word boundary
    inside the limit, never mid-word: reel 05's caption names broke as
    `invisible-o`, `envisio` and `goo` because the cut was a character
    count.  The bound itself is unchanged - only where the cut lands
    moves, and only ever shorter.  A single word longer than the limit
    has no boundary to break on and keeps the hard cut; uniqueness never
    rested on the readable half, which is what the digest is for.
    """
    if value is None:
        return absent
    text = str(value).strip().lower()
    if not text:
        return absent
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        return absent
    if len(text) <= _SLUG_MAX:
        return text
    cut = text[:_SLUG_MAX].rstrip("-")
    if len(cut) == _SLUG_MAX and text[_SLUG_MAX] != "-":
        # Mid-word: the next character continues the word the cut just
        # split, so back up to the previous boundary. A cut landing
        # exactly on a boundary, and a single unbreakable word, are
        # returned as before.
        mark = cut.rfind("-")
        if mark > 0:
            cut = cut[:mark]
    return cut


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


def provenance_stem(binding: dict) -> str:
    """The filename half that says where the speech came from.

    Shape: `sub_<speaker>_<clip>_<span_ms>`. No timeline, no block
    ordinal: both are placement, and placement never names a file.
    Absences are named (`nospeaker`, `noclip`, `nospan`) rather than
    omitted, because a stem that silently drops a component collides
    with one that never had it.
    """
    missing = [k for k in SEGMENT_BINDING_KEYS if k not in binding]
    if missing:
        raise ValueError(
            f"segment binding is missing {missing}. Every component is "
            f"required - pass None for one the edit does not provide, so "
            f"the record names the absence instead of dropping it. "
            f"SEGMENT_BINDING_KEYS is the whole list.")
    return "_".join((
        "sub",
        slug(binding["speaker"], "nospeaker"),
        slug(binding["source_clip_id"], "noclip"),
        _span_token(binding["source_start"], binding["source_end"]),
    ))


class SegmentNameCollision(ValueError):
    """Two segments would be written to one filename."""


def assert_no_content_collision(named) -> None:
    """Refuse a SET of planned renders in which one filename would hold
    two different pixels.

    `named` is `(segment_name, content_key)` pairs - the filename each
    render would write, and the full content key (drawing digest +
    renderer fingerprint + carriage) behind it.  Two pairs sharing a
    name share a file, which is correct when their keys agree (the
    same words in the same style at the same size - the cross-variant
    sharing this identity exists for) and a silent overwrite when they
    do not.  The second case is refused before a single file is
    written: nothing downstream compares content, so a wrong-pixels
    overwrite would reach the timeline with every check green.

    An empty content key ("cannot be established") agrees only with
    itself: two unkeyed renders of one name both proceed to render,
    and the second overwrites the first with pixels nothing vouched
    for - which the reuse path refuses to skip on, so it re-renders
    rather than reuses, loudly.
    """
    seen = {}
    for segment_name, content_key in named:
        if segment_name in seen:
            if seen[segment_name] != content_key:
                raise SegmentNameCollision(
                    f"two subtitle segments would be written to "
                    f"{segment_name!r} with different pixels.\n"
                    f"  first content key:  {seen[segment_name]!r}\n"
                    f"  second content key: {content_key!r}\n"
                    f"The second render would overwrite the first, and "
                    f"nothing downstream compares content. Two segments "
                    f"that draw differently must digest differently - "
                    f"a shared filename with two keys means a drawing "
                    f"input escaped the content key."
                )
        else:
            seen[segment_name] = content_key


def segment_identifier(binding: dict, content_digest: str) -> str:
    """The name a rendered subtitle segment is written under.

    Shape: `sub_<speaker>_<clip>_<span_ms>_<digest>`. Readable left
    to right - the provenance, then the proof.

    `content_digest` is the drawing-inputs digest (see
    `library/tools/render_cache.py`): the hex that decides reuse. Only
    the short prefix rides in the filename; the full three-factor key
    lives in the sidecar and is the authority. A digest that differs
    in any drawing input names a different file, so a reel can never
    overwrite the master's caption the way the ordinal name did - and
    two variants captioning the same words compute the same name,
    which is the sharing.
    """
    if not content_digest:
        raise ValueError(
            "segment_identifier needs the drawing-inputs digest - a "
            "provenance stem alone names WHERE the speech came from "
            "but not WHICH pixels, and two different captions over one "
            "span would share a filename. Pass \"\" nowhere: an "
            "unestablished digest renders, never skips, and must still "
            "name its own file.")
    from library.tools.render_cache import short_digest
    missing = [k for k in SEGMENT_BINDING_KEYS if k not in binding]
    if missing:
        raise ValueError(
            f"segment binding is missing {missing}. Every component is "
            f"required - pass None for one the edit does not provide, so "
            f"the record names the absence instead of dropping it. "
            f"SEGMENT_BINDING_KEYS is the whole list.")
    return f"{provenance_stem(binding)}_{short_digest(content_digest)}"


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
