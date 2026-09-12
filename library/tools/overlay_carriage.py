"""What an overlay artefact IS on disk, and how Resolve must be told to read it.

One enumeration.  Every overlay this engine writes - a caption
(`step_4_05_render_subtitles`), a motion graphic
(`step_4_06_render_motion_graphics`) - is an 8-bit RGBA picture with
PREMULTIPLIED alpha, carried as QuickTime Animation (``qtrle``).  Two
facts about that carriage live here because neither survives being
spelled at a call site: the encoder arguments, and the two clip
attributes Resolve needs before it composites one correctly.

WHY ``qtrle`` AND NOT ProRes 4444
─────────────────────────────────
The source is a Chromium screenshot: `render-batch.mjs` renders with
``imageFormat: "png"``, so every overlay is born 8-bit RGBA.  ProRes
4444 is a 10-bit lossy DCT codec, so encoding into it pays for
precision the picture never had and loses some of what it did have.
``qtrle`` is run-length coding over RGBA - lossless, and nearly free on
the long runs of identical transparent pixels an overlay is mostly made
of.  Measured on the captain's own overlays, 2026-09-12, 80 files
sampled at random from the 672 live ones (60 captions, 20 motion
graphics):

    bit-exact against the ProRes original         80 of 80
    captions          0.394 GB -> 0.138 GB        35.0%  (2.86x)
    motion graphics   0.447 GB -> 0.103 GB        23.0%  (4.35x)
    sample total      0.842 GB -> 0.241 GB        28.6%  (3.50x)
    projected, 672 files, 5.64 GB -> 1.86 GB      33.1%  (saves 3.78 GB)

and encoding the SAME rasterised frames, system ffmpeg, five runs each:

    PNG frames -> ProRes 4444    1.190 s   6,462,824 bytes
    PNG frames -> qtrle          0.141 s   2,125,161 bytes   8.4x faster

It is also the more FAITHFUL of the two, which is the part worth not
losing: composited in Resolve over a known plate and measured against
the Remotion source at every fully-opaque ink pixel, across 49 frames,
the ``qtrle`` composite was EXACT (mean 0.0000, worst 0.000 of 255)
while the ProRes 4444 composite deviated by a mean of 0.77 and as much
as 38 of 255.  This is a fidelity change first and a size change
second.

THE CLIP ATTRIBUTE THAT IS NOT OPTIONAL
───────────────────────────────────────
Resolve auto-detects the DATA LEVEL of a QuickTime Animation clip as
VIDEO range, and an 8-bit RGBA overlay is FULL range.  The consequence
is not a subtle shift on the graphic - it is a defect over the WHOLE
FRAME, including every pixel where the overlay is fully transparent,
because the premultiplied composite adds the clip's black and a
video-range reading puts that black at ``-16/219`` instead of ``0``.

Measured on DaVinci Resolve Studio 21.1, 2026-09-12, one real caption
overlay on V2 over a flat 8-bit ``0x808080`` plate on V1, one frame
exported through Deliver as a 16-bit PNG, read at a corner the caption
never touches:

    overlay codec   Data Level   transparent-region value (of 255)
    none (plate)    -            127.957      the plate itself
    ProRes 4444     Auto         127.957      correct
    ProRes 4444     Full         143.895      WRONG, +16 - black lifted
    qtrle           Auto         109.331      WRONG, -18.6 - black crushed
    qtrle           Full         127.957      correct

So ``Auto`` is right for ProRes and wrong for ``qtrle``, which is why
`data_level_for` is keyed to the CODEC and not set to one value for
everything: forcing ``Full`` on a ProRes overlay breaks it exactly as
leaving ``Auto`` on a ``qtrle`` one does.  A tag on the file does not
help - ``-color_range pc`` and a written ``colr`` atom were both tried
and Resolve still read the clip as video range - so the attribute has
to be SET, on the pool item, by whoever imported it.

With the level set right, the same comparison over 61 consecutive
frames: every transparent pixel bit-identical to the ProRes composite
(worst deviation 0.00000000 of 255), the partial-alpha pixels agreeing
to 0.84 of 255, and the fully-opaque ink differing only where ProRes's
own DCT ringing lives.

WHAT ENFORCES IT
────────────────
Two things, because they catch different failures.

`apply_clip_attributes` sets both attributes and READS THEM BACK off
the pool item, refusing when Resolve did not take them - AGENTS.md 5,
judge a Resolve call by what it RETURNS.  It runs on every import AND
on every lookup hit, because a pool item that was imported while the
file was still ProRes keeps its old attributes after the file is
transcoded underneath it.

`assert_transparent_region_unchanged` is the other one, and it is the
real check: it measures a COMPOSITED FRAME against the plate it was
composited over, wherever the overlay's own alpha is zero, and raises
when they disagree.  That is the defect itself rather than a proxy for
it - a build that sets the attribute and still darkens the picture
fails this, and one that reaches the same picture by another route
passes.  `tests/test_overlay_carriage.py` drives it with the real
exported frames from the measurement above.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Optional, Sequence

# ─── What the encoder is told ────────────────────────────────────────

OVERLAY_VIDEO_CODEC = "qtrle"
"""The ffmpeg encoder name for an overlay artefact's picture."""

OVERLAY_PIXEL_FORMAT = "argb"
"""8-bit RGBA, the only alpha-carrying pixel format `qtrle` accepts.

`ffmpeg -h encoder=qtrle` lists `rgb24 rgb555be argb gray`; `argb` is
the sole one with an alpha plane, and it is the QuickTime Animation
32-bit layout.
"""

OVERLAY_FORMAT_NAME = "QuickTime Animation (qtrle) RGBA"
"""How an overlay artefact is NAMED in a step's own record.

The steps report a `format` beside `has_alpha` in their payload, and a
record that still said ProRes 4444 would be a step describing its own
output wrongly - the exact key-name-mismatch shape AGENTS.md 10.1 is
about, one level up.
"""

OVERLAY_ENCODE_ARGS: tuple[str, ...] = (
    "-c:v", OVERLAY_VIDEO_CODEC, "-pix_fmt", OVERLAY_PIXEL_FORMAT,
)
"""The video half of every overlay encode, spelled once.

Used by the caption crop (`library/tools/tight_box.crop_probe_to_tight`)
and the motion-graphics post-render transcode
(`library/steps/step_4_06_render_motion_graphics/post_bridge.py`).
Remotion cannot emit this itself: its `renderMedia` takes no `qtrle`
codec, and its BUNDLED ffmpeg is built with `--disable-encoders` and an
explicit enable list that does not include `qtrle`, so an
`ffmpegOverride` that asks for it fails with `Unknown encoder 'qtrle'`
(measured 2026-09-12).  The transcode therefore uses the SYSTEM ffmpeg,
after the render.
"""

#: Codec names, as ffprobe spells them, whose clips Resolve reads as
#: VIDEO range by default when the picture is really FULL range.  Read
#: the table in this module's docstring before adding to it: a codec
#: belongs here only where an exported still proves it.
FULL_RANGE_CODECS = frozenset({"qtrle"})

DATA_LEVEL_FULL = "Full"
"""The `Data Level` clip property value an 8-bit RGBA overlay needs."""

ALPHA_MODE_PREMULTIPLIED = "Premultiplied"
"""The `Alpha mode` every overlay this engine writes needs.

Chromium composites the drop shadow and the 8-direction outline into
premultiplied RGBA: measured over a real caption frame, `max(RGB - A)`
is 0, i.e. no channel ever exceeds its own alpha.
"""


class OverlayCarriageRefused(RuntimeError):
    """Resolve would not hold an overlay's clip attributes.

    Raised rather than warned: a clip whose Data Level did not take
    composites a darkened frame, and a build that shipped one would
    look like a grade problem rather than a codec problem.
    """


class TransparentRegionDarkened(RuntimeError):
    """A composited frame disagrees with its plate where nothing was drawn."""


# ─── Reading a file ──────────────────────────────────────────────────

_PROBE_CACHE: dict = {}
"""Probes already taken this process, keyed by path AND its stat.

`apply_clip_attributes` is called on every media-pool import and on
every lookup hit, a-roll included, so an uncached ffprobe would be paid
hundreds of times a build. Keyed on `(path, mtime_ns, size)` rather
than path alone, because a file transcoded underneath a running process
is exactly the case this module exists for: a stale entry would tell
Resolve to read the new codec under the old codec's rule.
"""


def probe_overlay(path: str, timeout: int = 30) -> dict:
    """`{codec_name, pix_fmt, profile}` for *path*'s video stream, or `{}`.

    `{}` means the probe could not be run or read - never "no alpha".
    Callers that must decide something distinguish the two themselves,
    because an unreadable file is a different fact from a file read and
    found wanting.
    """
    try:
        status = os.stat(path)
        key = (path, status.st_mtime_ns, status.st_size)
    except OSError:
        key = None
    if key is not None and key in _PROBE_CACHE:
        return dict(_PROBE_CACHE[key])
    fields = _probe_overlay_uncached(path, timeout)
    if key is not None:
        _PROBE_CACHE[key] = fields
    return dict(fields)


def _probe_overlay_uncached(path: str, timeout: int = 30) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,pix_fmt,profile",
             "-of", "json", path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except ValueError:
        return {}
    if not streams:
        return {}
    stream = streams[0]
    return {
        "codec_name": str(stream.get("codec_name") or ""),
        "pix_fmt": str(stream.get("pix_fmt") or ""),
        "profile": str(stream.get("profile") or ""),
    }


def carries_alpha(codec_name: str = "", pix_fmt: str = "",
                  profile: str = "") -> bool:
    """Does a stream with these ffprobe fields have an alpha plane?

    THE ONE ALPHA SNIFF.  `library/tools/qa/asset_qa.py` used to test
    `"yuva" in pix_fmt` or ProRes-with-a-4444-profile, which is a
    complete description of the codecs this engine wrote in 2026-09-11
    and none of the one it writes now: a `qtrle`/`argb` overlay read as
    having NO alpha, so QA rejected a valid file.  The list is by
    PIXEL FORMAT first, because that is where the alpha plane actually
    is, with the ProRes profile kept as the second route because
    `yuva444p10le` and `yuva444p12le` both already match the first.
    """
    fmt = (pix_fmt or "").lower()
    codec = (codec_name or "").lower()
    if any(marker in fmt for marker in ("yuva", "argb", "rgba",
                                        "abgr", "bgra", "ya8", "ya16")):
        return True
    return codec == "prores" and "4444" in (profile or "")


def data_level_for(codec_name: str) -> Optional[str]:
    """`"Full"` where Resolve's auto-detection is wrong, else `None`.

    `None` means LEAVE IT ALONE - not "set it to Auto".  Auto is right
    for every codec but the ones in `FULL_RANGE_CODECS`, and forcing a
    level onto a clip Resolve already reads correctly is the same
    defect in the other direction (see the table in this module's
    docstring: ProRes 4444 at `Full` renders +16 of 255 too bright).
    """
    return DATA_LEVEL_FULL if (codec_name or "").lower() in FULL_RANGE_CODECS \
        else None


# ─── Telling Resolve ─────────────────────────────────────────────────

def apply_clip_attributes(pool_item, path: str = "",
                          probe: Optional[dict] = None) -> dict:
    """Set the alpha mode and data level an overlay needs, and READ BACK.

    Decided by the FILE, never by the caller: the caller knows it is
    placing an overlay, but only the file knows which codec it is, and
    the right data level differs between them.  A clip with no alpha
    plane is left entirely alone - this is called from generic import
    paths that carry a-roll too.

    Returns what was applied and what Resolve reports holding, so the
    caller can record it.  Raises `OverlayCarriageRefused` when a
    property was asked for and the read-back disagrees, because a
    silently-refused Data Level is exactly the failure this exists to
    stop.
    """
    if pool_item is None:
        raise OverlayCarriageRefused(
            "no pool item to carry an overlay's clip attributes")
    fields = probe if probe is not None else probe_overlay(path or "")
    if not fields:
        # Unreadable, so nothing is CLAIMED about it. A file the probe
        # cannot read is not asserted to be alpha-free.
        return {"applied": {}, "read_back": {}, "alpha": None}
    if not carries_alpha(**fields):
        return {"applied": {}, "read_back": {}, "alpha": False}

    wanted = {"Alpha mode": ALPHA_MODE_PREMULTIPLIED}
    level = data_level_for(fields.get("codec_name", ""))
    if level is not None:
        wanted["Data Level"] = level

    read_back = {}
    for name, value in wanted.items():
        pool_item.SetClipProperty(name, value)
        read_back[name] = pool_item.GetClipProperty(name)
    disagreed = {n: read_back[n] for n, v in wanted.items()
                 if read_back[n] != v}
    if disagreed:
        raise OverlayCarriageRefused(
            f"Resolve would not hold the overlay clip attributes for "
            f"{path or pool_item}: asked for {wanted}, it reports "
            f"{disagreed}. A {fields.get('codec_name')!r} overlay whose "
            f"Data Level is not {DATA_LEVEL_FULL!r} composites a frame "
            f"darkened by 16/255 EVERYWHERE, including where the overlay "
            f"is fully transparent - see library/tools/overlay_carriage.py.")
    return {"applied": wanted, "read_back": read_back, "alpha": True}


# ─── The check that is the defect itself ─────────────────────────────

TRANSPARENT_DEVIATION_TOLERANCE = 1.0
"""How far a composited frame may sit from its plate where nothing drew.

On the 8-bit scale, whatever precision the frames are read at.  The
measured correct value is EXACTLY ZERO - 61 consecutive frames, two
million transparent pixels each, `max|composite - plate| = 0.00000000`
- and the measured defect is 15.94, so this tolerance neither fails
correct output nor passes the thing it is for.  It is not zero because
a composite that has been through a colour-managed render path may
legitimately move by less than one code value; it is nowhere near 16.
"""


def transparent_region_deviation(composited, plate, alpha,
                                 tolerance: float = None):
    """`(worst, count)` where a composite differs from its plate under alpha 0.

    Pure measurement on three arrays, so it is testable without
    Resolve, and it is the SAME arithmetic whether the frames came
    from a Deliver export, a still or a probe render:

    * `composited` - the rendered frame, H x W x (3 or 4).
    * `plate`      - the same frame with the overlay absent.
    * `alpha`      - the overlay's OWN alpha plane, H x W, 0..255.

    Only the RGB channels are compared, and only where `alpha == 0`:
    those pixels must come through untouched, whatever the overlay
    draws elsewhere.  `worst` is on the scale the arrays carry;
    `count` is how many pixels exceed *tolerance*.
    """
    import numpy as np

    tol = TRANSPARENT_DEVIATION_TOLERANCE if tolerance is None else tolerance
    comp = np.asarray(composited, dtype=np.float64)[..., :3]
    ref = np.asarray(plate, dtype=np.float64)[..., :3]
    mask = np.asarray(alpha) == 0
    if comp.shape != ref.shape:
        raise ValueError(
            f"composite {comp.shape} and plate {ref.shape} are not the "
            f"same picture, so nothing can be compared between them")
    if mask.shape != comp.shape[:2]:
        raise ValueError(
            f"alpha plane {mask.shape} does not cover the composite "
            f"{comp.shape[:2]}")
    if not mask.any():
        raise ValueError(
            "the overlay is opaque on every pixel of this frame, so it "
            "cannot say whether the untouched picture came through")
    diff = np.abs(comp - ref)[mask]
    worst = float(diff.max())
    return worst, int((diff.max(axis=-1) > tol).sum())


def assert_transparent_region_unchanged(composited, plate, alpha,
                                        tolerance: float = None,
                                        what: str = "overlay") -> float:
    """Raise `TransparentRegionDarkened` unless the plate came through.

    The gate the Data Level attribute exists to satisfy, pointed at
    the PICTURE rather than at the call that was supposed to produce
    it.  A build that sets the property and still darkens the frame
    fails here; so does one that stops setting it.
    """
    tol = TRANSPARENT_DEVIATION_TOLERANCE if tolerance is None else tolerance
    worst, count = transparent_region_deviation(
        composited, plate, alpha, tolerance=tol)
    if worst > tol:
        raise TransparentRegionDarkened(
            f"{what}: the composited frame differs from the plate by up "
            f"to {worst:.3f} of 255 on {count} pixel(s) the overlay does "
            f"not draw on at all (tolerance {tol}). The overlay is being "
            f"read at the wrong data level - a qtrle clip imported on "
            f"Resolve's default `Auto` composites 16/255 dark over the "
            f"WHOLE frame. See library/tools/overlay_carriage.py.")
    return worst


# ─── Migrating what is already on disk ───────────────────────────────

def transcode_in_place(path: str, timeout: int = 600) -> dict:
    """Rewrite an existing overlay as `qtrle`, or say why it was not.

    The migration path for artefacts a previous carriage rendered.  It
    is a TRANSCODE and not a re-render because `qtrle` is lossless over
    an 8-bit RGBA picture, so the result is the same pixels the cached
    reuse key already promises - and that is VERIFIED here, frame by
    frame over the whole file, rather than assumed.  A file whose
    transcode is not bit-exact is left exactly as it was.

    Returns a record; raises nothing, because a migration over hundreds
    of files reports per file rather than stopping on one.
    """
    fields = probe_overlay(path)
    record = {"path": path, "before": 0, "after": 0,
              "codec_before": fields.get("codec_name", ""),
              "changed": False, "bit_exact": None, "skipped": "", "error": ""}
    try:
        record["before"] = os.path.getsize(path)
    except OSError as exc:
        # Same reasoning as the unreadable-probe case below: a file that
        # is not there is not an overlay in the wrong codec, and whether
        # a render produced its output is judged by the render's own
        # return value, not by the codec step downstream of it.
        record["skipped"] = f"not on disk ({exc})"
        return record
    if not fields:
        # SKIPPED, not FAILED, and the distinction is deliberate. A file
        # the probe cannot read is not an overlay artefact in the wrong
        # codec - it is not an overlay artefact at all, and saying which
        # is not this function's job. The render step that calls it has
        # its own gates for a broken output (`qa/asset_qa.py`, the
        # caption ink QA), and each of them says something more useful
        # about a corrupt file than "could not be transcoded" does.
        # Refusing here would be this step reporting a defect it does
        # not own.
        record["skipped"] = "ffprobe could not read it"
        record["after"] = record["before"]
        return record
    if fields.get("codec_name", "").lower() == OVERLAY_VIDEO_CODEC:
        record["skipped"] = "already qtrle"
        record["after"] = record["before"]
        return record
    if not carries_alpha(**fields):
        record["skipped"] = "no alpha plane, so not an overlay artefact"
        record["after"] = record["before"]
        return record

    temporary = f"{path}.qtrle.tmp.mov"
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", path,
             *OVERLAY_ENCODE_ARGS, "-c:a", "copy", temporary],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _unlink(temporary)
        record["error"] = f"transcode failed: {exc}"
        return record
    if result.returncode != 0 or not os.path.isfile(temporary):
        _unlink(temporary)
        record["error"] = (
            f"transcode failed: {(result.stderr or '').strip()[-300:]}")
        return record

    record["bit_exact"] = frames_are_identical(path, temporary)
    if not record["bit_exact"]:
        _unlink(temporary)
        record["error"] = (
            "transcode is not bit-exact, so it is not the picture the "
            "cached reuse key promises; the original is untouched")
        return record
    try:
        os.replace(temporary, path)
    except OSError as exc:
        _unlink(temporary)
        record["error"] = f"cannot replace {path}: {exc}"
        return record
    record["after"] = os.path.getsize(path)
    record["changed"] = True
    return record


def frames_are_identical(left: str, right: str, timeout: int = 600) -> bool:
    """Do two files decode to the same 8-bit RGBA bytes, every frame?

    The whole file, not a sample: what makes a transcode legitimate in
    place of a re-render is that the picture did not change, and a
    sampled check would let a changed frame through as a hit.
    """
    def _raw(path: str) -> Optional[bytes]:
        try:
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", path,
                 "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
                capture_output=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout if result.returncode == 0 else None

    a, b = _raw(left), _raw(right)
    return a is not None and b is not None and a == b


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def restamp_carriage(paths: Sequence[str], old: str, new: str) -> dict:
    """Move a recorded carriage stamp from *old* to *new*, in place.

    The other half of the migration.  A carriage bump makes every
    recorded reuse key and every tight-box sidecar name a carriage that
    no longer exists, and the readers of both refuse rather than
    re-render quietly (`step_4_05_render_subtitles._reuse_key`,
    `library/tools/tight_box`).  Because the transcode above leaves the
    PICTURE unchanged, the placement those records describe is still
    the placement the artefact needs, so the stamp is corrected rather
    than the artefact rebuilt.

    Handles both shapes: a `*_reuse_key.txt` whose text ends `+<carriage>`,
    and a `*_box.json` carrying a `carriage` field.
    """
    changed, skipped, failed = [], [], []
    for path in paths:
        try:
            if path.endswith(".json"):
                with open(path, encoding="utf-8") as handle:
                    record = json.load(handle)
                if record.get("carriage") != old:
                    skipped.append(path)
                    continue
                record["carriage"] = new
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, indent=2)
            else:
                with open(path, encoding="utf-8") as handle:
                    text = handle.read().strip()
                if not text.endswith(f"+{old}"):
                    skipped.append(path)
                    continue
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text[: -len(old)] + new)
            changed.append(path)
        except (OSError, ValueError) as exc:
            failed.append({"path": path, "error": str(exc)})
    return {"changed": changed, "skipped": skipped, "failed": failed}
