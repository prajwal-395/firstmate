"""Pool metadata versus the file on disk: the ONE comparison for both.

The defect this closes
----------------------
Overlay artefacts are rewritten IN PLACE at a stable path: a re-render
under an unchanged drawing digest lands on the same filename
(`subtitle_segment_id.segment_identifier` digests drawing inputs, and a
renderer change that moves pixels without changing them - the constant
904 canvas of #1071 - keeps the name), and `transcode_in_place`
(`overlay_carriage`) rewrites ProRes bytes as qtrle at the same path by
design, because the pixels are bit-identical. `import_pool_item`
(`reel_build`) reuses the pooled item BY PATH and `_carry` refreshes
clip attributes only; Resolve caches stream dimensions and codec at
import in a place the scripting API cannot set. So a pooled item keeps
its predecessor's metadata forever, and a ProRes decoder at the wrong
dimensions on qtrle bytes fails the render - measured 2026-09-13 as 13
of 228 overlay items stale, 19 placements across Reels 26, 28 and 09.

Two callers, one comparison. `deliver-reel`'s preflight
(`reel_deliver.overlay_staleness`, landed in #1085) refuses before
queueing a job; the reel build (`reel_build.import_pool_item`)
refreshes at its staging step, where rebinding is safe. Both read
through here, so the check cannot drift into two definitions of stale.

What is compared
----------------
Resolution always: the pool's `Resolution` property against ffprobe's
`width`/`height`. Codec when the pool exposes one: the disk side is
always known (ffprobe `codec_name`), the pool side is read off the
item's full property dict by NAME SUBSTRING (`codec`, case-insensitive)
rather than one assumed key, because no caller in this tree has ever
read a codec property and the exact key is unverified against live
Resolve. A pool that exposes no codec key is compared on resolution
alone, and that is said in the record (`codec_compared: False`) rather
than passed off as a full check. Every measured stale item disagreed on
resolution; the codec leg exists for the transcode-only rewrite, where
dimensions survive and only the codec turns over.

A side that cannot be read is SKIPPED, never flagged: a missing file, a
probe ffmpeg cannot read, or a pool item with no parseable Resolution
is `comparable: False`, and disagreement needs both sides known. An
unreadable file is not a stale item, and saying which is not this
module's job.

`tests/test_pool_stream_meta_refresh.py`.
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional

#: Resolve's codec name for a stream, against ffprobe's name for the
#: same bytes. The two vocabularies do not overlap on a single value -
#: measured 2026-09-13 across all 242 pooled items of the field-test
#: project, every pair below observed live - so comparing the raw
#: strings would read EVERY item as stale. A pair not in this table is
#: NOT COMPARED (`codec_compared: False`): an unknown vocabulary is
#: something this table has not learned, never evidence of staleness,
#: and flagging it would be a gate that fails correct output
#: (AGENTS.md 10.4).
CODEC_VOCABULARY = {
    "animation": "qtrle",
    "apple prores 4444": "prores",
    "apple prores 4444 xq": "prores",
    "apple prores 422": "prores",
    "apple prores 422 hq": "prores",
    "apple prores 422 lt": "prores",
    "apple prores 422 proxy": "prores",
    "png": "png",
    "h.264": "h264",
    "h.265": "hevc",
}


def disk_stream(path: str, timeout: int = 15) -> dict:
    """`{width, height, codec}` the file on disk actually carries.

    Each value is None when there is no video stream to compare
    (audio-only, missing or unreadable) - the caller proves staleness
    and nothing more, so what cannot be compared is skipped rather
    than flagged. One ffprobe call for all three, because the build
    pays this per lookup-hit artefact and two probes would double it.

    READ BY NAME, never by position. `-show_entries` selects WHICH
    fields are printed and does not order them: ffprobe emits its own
    stream field order, so `stream=width,height,codec_name` prints
    `qtrle,904,480` and a positional parse takes the codec for the
    width. Measured 2026-09-13: that parse raised `ValueError` on
    every artefact in the project, so this returned all-None, every
    comparison read `comparable: False`, and the staleness check
    landed in #1089 could not fire at all - while 13 genuinely stale
    items sat in the pool.
    """
    record: dict = {"width": None, "height": None, "codec": None}
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,codec_name",
             "-of", "json", path],
            capture_output=True, check=False, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return record
    try:
        streams = json.loads(probe.stdout or "{}").get("streams") or []
    except ValueError:
        return record
    if not streams:
        return record
    stream = streams[0]
    width, height = stream.get("width"), stream.get("height")
    if isinstance(width, int) and isinstance(height, int):
        record["width"] = width
        record["height"] = height
    codec = str(stream.get("codec_name") or "").strip().lower()
    record["codec"] = codec or None
    return record


def _pool_codec(item) -> Optional[str]:
    """The VIDEO codec Resolve's pool metadata claims, or None.

    Read off the item's FULL property dict (the no-arg
    `GetClipProperty()` form). The key must carry `video` as well as
    `codec`: the dict measured live on Resolve 21.1 holds THREE keys
    matching `codec` - `Audio Codec`, `Codec Bitrate` and
    `Video Codec` - and `Audio Codec` comes first, so a plain
    substring match returned `'Linear PCM'` for every overlay
    artefact in the project on 2026-09-13 and compared an audio codec
    against a video one. `Codec Bitrate` is not a codec at all.

    No dict, no video-codec key, or an empty value all read as None -
    compared on resolution alone, said plainly.
    """
    try:
        properties = item.GetClipProperty()
    except Exception:  # noqa: BLE001 - live API, judged by what returns
        return None
    if not isinstance(properties, dict):
        return None
    for key, value in properties.items():
        name = str(key).lower()
        if "codec" in name and "video" in name:
            text = str(value or "").strip().lower()
            return text or None
    return None


def codecs_agree(pool_codec: str, disk_codec: str) -> Optional[bool]:
    """Whether the two names describe the same codec, or None if unknown.

    Resolve and ffprobe name the same bytes differently and share no
    value: `Animation`/`qtrle`, `Apple ProRes 4444`/`prores`,
    `XAVC High L5.1`/`h264`. Comparing the strings would call all 242
    pooled items stale. So the pool side is translated through
    `CODEC_VOCABULARY` and only then compared.

    None means THIS TABLE DOES NOT KNOW the pool's name - the caller
    drops the codec leg and says `codec_compared: False`. A camera
    format the table has not learned must never read as staleness,
    because a gate that fails correct output is no more coverage than
    one that cannot fail (AGENTS.md 10.4).
    """
    translated = CODEC_VOCABULARY.get(pool_codec.strip().lower())
    if translated is None:
        return None
    return translated == disk_codec.strip().lower()


def pool_stream(item) -> dict:
    """`{width, height, codec}` Resolve's pool metadata claims for this item.

    Resolution is parsed off the `Resolution` property (`"904x480"`);
    anything else - missing, unparseable, unreadable - is None. Codec
    comes from `_pool_codec` above.
    """
    record: dict = {"width": None, "height": None, "codec": None}
    try:
        raw = item.GetClipProperty("Resolution")
    except Exception:  # noqa: BLE001 - live API, judged by what returns
        return record
    if isinstance(raw, str) and "x" in raw:
        try:
            width, height = raw.split("x", 1)
            record["width"] = int(width)
            record["height"] = int(height)
        except ValueError:
            pass
    record["codec"] = _pool_codec(item)
    return record


def stream_disagreement(pool_sig: dict, disk_sig: dict) -> Optional[dict]:
    """How the pool's claim disagrees with the file, or None.

    None means agree OR incomparable - both are "no refusal", and the
    record says which (`comparable`, `codec_compared`), because a
    comparison that silently loses a leg reads as coverage it is not
    (AGENTS.md 10.4). A disagreement names both sides on every leg
    that fired.

    THE INPUT THAT BREAKS THIS: a pool item caching `866x480`/ProRes
    for a file that is `904x480`/qtrle on disk - Reel 26, 2026-09-13.
    Either leg alone fires it; the codec-only shape (same dimensions,
    transcoded underneath) is the transcode migration with no
    re-render, which a resolution-only check would wave through.
    """
    comparable = (
        pool_sig.get("width") is not None
        and disk_sig.get("width") is not None
    )
    if not comparable:
        return None
    mismatches = []
    if (pool_sig.get("width"), pool_sig.get("height")) != (
            disk_sig.get("width"), disk_sig.get("height")):
        mismatches.append("resolution")
    agree = None
    if pool_sig.get("codec") and disk_sig.get("codec"):
        agree = codecs_agree(pool_sig["codec"], disk_sig["codec"])
    codec_compared = agree is not None
    if agree is False:
        mismatches.append("codec")
    if not mismatches:
        return None
    return {
        "mismatches": mismatches,
        "comparable": True,
        "codec_compared": codec_compared,
        "pool_resolution": f"{pool_sig['width']}x{pool_sig['height']}",
        "disk_resolution": f"{disk_sig['width']}x{disk_sig['height']}",
        "pool_codec": pool_sig.get("codec"),
        "disk_codec": disk_sig.get("codec"),
    }
