"""Per-shot colour measurement: what each graded shot IS in RGB.

Step 5.01 used to see mean luma only, and luma cannot see a cast. On the
geo-podcast dark neutral set camera A reads about 15% warmer than camera
B on the SAME wall - whole-frame mean RGB (56.0, 50.5, 50.1) against
(41.9, 41.9, 43.2), R/B 1.118 against 0.970 - and the colourist had no
number for it, so nothing grounded the CDL that could express the
correction (fidelity rung 4c, P4 colour-matched multicam).

What this measures, per source file:

* `mean_rgb` - per-channel means over the sampled frames, 0-255. The
  whole-frame balance (`rb_all`, `gb_all`) conflates the cast with the
  content: a warm face on a neutral set reads warm here even when the
  camera is balanced.
* the NEUTRAL reading - mean RGB of the near-neutral pixels, plus `rb`
  (R/B) and `gb` (G/B) on them. A grey wall, a black drape, a white
  shirt: surfaces with no hue of their own, where R/B != 1 IS the cast.
  On geo-podcast the neutral band is the set wall itself (about half
  the sampled pixels), and it reads camA R/B 1.003 against camB 0.953 -
  a smaller gap than the whole frame's, which is the point: the neutral
  separates the camera from the content.
* luma is NOT measured here. `grade.measure_luma` owns it (ffprobe
  signalstats YAVG); this module reads pixels for chroma.

The neutral rule is stated, not tuned: a pixel counts when its channel
spread is within `max(NEUTRAL_ABS_FLOOR, NEUTRAL_REL_TOL * brightness)`
and its brightness sits inside [`NEUTRAL_LO`, `NEUTRAL_HI`] - out go the
crushed blacks (noise votes warm) and the clipped whites (nothing to
read). Fewer than `NEUTRAL_MIN_FRACTION` of the sampled pixels and the
neutral is ABSENT with the reason, never a whole-frame mean wearing its
name: an absent neutral and a measured-neutral-at-1.0 are different
facts, and reading one as the other is how a cast survives a match.

Sampling is one ffmpeg call: `CHROMA_FRAMES` frames evenly across the
first `CHROMA_SAMPLE_SECONDS`, downscaled to `CHROMA_WIDTH` wide. Both
are MECHANICAL - how much of a file to read, not how the picture should
look - and both are stated on every row beside the number, the same way
`LUMA_SCOPE` travels beside the luma.

`extract_still` draws the one representative still per shot the
colourist is shown (5.01 bridge): a single JPEG at the middle of the
sampled span, so the still IS of the seconds the numbers describe.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import numpy as np
from PIL import Image

#: How the chroma is sampled. Recorded on every row beside the number,
#: so a reader can tell a measurement from an absence (AGENTS.md 10.3).
COLOUR_METHOD = "ffmpeg_frames_pil_rgb"

#: What a shot carries when nothing measured it. NOT zeros: an
#: unmeasured balance and a perfectly neutral one are different facts.
COLOUR_UNMEASURED = "unmeasured"

#: What the measurement covers, in the words the prompt uses. Stated in
#: one place because the colourist has to know what the numbers are not.
CHROMA_FRAMES = 8
CHROMA_SAMPLE_SECONDS = 10.0
CHROMA_WIDTH = 320
COLOUR_SCOPE = (
    f"per-channel mean RGB (0-255) over {CHROMA_FRAMES} frames evenly "
    f"spanning the first {CHROMA_SAMPLE_SECONDS:g}s of the source file, "
    f"downscaled to {CHROMA_WIDTH}px wide"
)

#: The neutral rule. A pixel whose channels spread wider than this is
#: content with a hue of its own, not a reference. The relative term
#: carries the midtones; the absolute floor keeps dark neutrals (the
#: geo-podcast wall lives near 45) from failing on sensor noise.
NEUTRAL_REL_TOL = 0.06
NEUTRAL_ABS_FLOOR = 6.0
NEUTRAL_LO = 25.0
NEUTRAL_HI = 225.0

#: Below this fraction of sampled pixels the neutral is absent. A wall
#: that fills half the frame clears it fifty times over; a shot with no
#: grey in it says so instead of borrowing the whole-frame mean.
NEUTRAL_MIN_FRACTION = 0.01

#: ffmpeg gets a hard ceiling so one unreadable file cannot wedge a run.
FFMPEG_TIMEOUT_SECONDS = 60

#: The still the colourist is shown. Wide enough to judge a cast on,
#: small enough to keep a nine-clip cut under a megabyte of stills.
STILL_WIDTH = 640


def _unmeasured(reason: str) -> dict:
    return {
        "mean_rgb": None,
        "rb_all": None,
        "gb_all": None,
        "neutral_rgb": None,
        "neutral_rb": None,
        "neutral_gb": None,
        "neutral_fraction": 0.0,
        "colour_method": COLOUR_UNMEASURED,
        "colour_samples": 0,
        "colour_unmeasured_because": reason,
    }


def _frame_paths(source_file: str, out_dir: str) -> list:
    """Extract the sampled frames. Returns the paths that exist."""
    pattern = os.path.join(out_dir, "chroma_%03d.jpg")
    fps = CHROMA_FRAMES / CHROMA_SAMPLE_SECONDS
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-i", source_file,
             "-vf", (f"fps={fps},scale={CHROMA_WIDTH}:-1"),
             "-vframes", str(CHROMA_FRAMES),
             "-q:v", "3",
             "-t", str(CHROMA_SAMPLE_SECONDS),
             pattern],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=FFMPEG_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        return []
    except (FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    paths = []
    for index in range(1, CHROMA_FRAMES + 1):
        path = pattern % index
        if os.path.exists(path) and os.path.getsize(path) > 0:
            paths.append(path)
    return paths


def _read_pixels(paths: list) -> np.ndarray | None:
    """All sampled pixels as one (N, 3) float array, or None."""
    frames = []
    for path in paths:
        try:
            with Image.open(path) as image:
                frames.append(
                    np.asarray(image.convert("RGB"), dtype=np.float64))
        except (OSError, ValueError):
            continue
    if not frames:
        return None
    return np.concatenate([f.reshape(-1, 3) for f in frames], axis=0)


def neutral_of(pixels: np.ndarray) -> dict:
    """The neutral reading of one pixel table.

    Pure function over pixels so tests can hold exact swatches to it
    without ffmpeg. Returns `neutral_rgb`, `neutral_rb`, `neutral_gb`,
    `neutral_fraction`, or `absent` + `reason` where no neutral clears
    the floor.
    """
    pixels = np.asarray(pixels, dtype=np.float64).reshape(-1, 3)
    total = pixels.shape[0]
    brightest = pixels.max(axis=1)
    darkest = pixels.min(axis=1)
    brightness = pixels.mean(axis=1)
    spread = brightest - darkest
    allowed = np.maximum(NEUTRAL_ABS_FLOOR, NEUTRAL_REL_TOL * brightness)
    mask = ((spread <= allowed)
            & (brightness >= NEUTRAL_LO)
            & (brightness <= NEUTRAL_HI))
    selected = pixels[mask]
    fraction = float(len(selected)) / float(total) if total else 0.0
    if len(selected) == 0 or fraction < NEUTRAL_MIN_FRACTION:
        return {
            "neutral_rgb": None,
            "neutral_rb": None,
            "neutral_gb": None,
            "neutral_fraction": round(fraction, 4),
            "absent": True,
            "reason": (
                f"only {fraction:.4f} of sampled pixels read near-neutral "
                f"(needs {NEUTRAL_MIN_FRACTION}); this shot carries no "
                f"grey to balance off"),
        }
    mean = selected.mean(axis=0)
    blue = float(mean[2])
    return {
        "neutral_rgb": [round(float(mean[0]), 2),
                        round(float(mean[1]), 2),
                        round(float(mean[2]), 2)],
        "neutral_rb": round(float(mean[0] / blue), 4) if blue else None,
        "neutral_gb": round(float(mean[1] / blue), 4) if blue else None,
        "neutral_fraction": round(fraction, 4),
        "absent": False,
        "reason": "",
    }


def measure_shot_colour(source_file: str) -> dict:
    """Measure one source file's colour, or say plainly nothing did.

    Returns a record carrying `mean_rgb` ([r, g, b] or None),
    `rb_all`/`gb_all` (whole-frame balance), the neutral reading
    (`neutral_rgb`, `neutral_rb`, `neutral_gb`, `neutral_fraction`),
    `colour_method`, `colour_samples` (frames read) and, where the
    measurement did not happen, `colour_unmeasured_because`.

    A failure is REPORTED, never returned as zeros: a zeros row reads
    as measured black, which balances to nothing.
    """
    if not source_file:
        return _unmeasured("no source file on the entry")
    if not os.path.exists(source_file):
        return _unmeasured(f"source file not on disk: {source_file}")

    with tempfile.TemporaryDirectory(prefix="shot_colour_") as work:
        paths = _frame_paths(source_file, work)
        if not paths:
            return _unmeasured(
                "ffmpeg returned no decodable frames in the sampled span")
        pixels = _read_pixels(paths)
        if pixels is None or pixels.shape[0] == 0:
            return _unmeasured(
                "the sampled frames held no readable pixels")
        samples = len(paths)

    mean = pixels.mean(axis=0)
    blue = float(mean[2])
    record = {
        "mean_rgb": [round(float(mean[0]), 2),
                     round(float(mean[1]), 2),
                     round(float(mean[2]), 2)],
        "rb_all": round(float(mean[0] / blue), 4) if blue else None,
        "gb_all": round(float(mean[1] / blue), 4) if blue else None,
        "colour_method": COLOUR_METHOD,
        "colour_samples": samples,
    }
    neutral = neutral_of(pixels)
    record["neutral_rgb"] = neutral["neutral_rgb"]
    record["neutral_rb"] = neutral["neutral_rb"]
    record["neutral_gb"] = neutral["neutral_gb"]
    record["neutral_fraction"] = neutral["neutral_fraction"]
    if neutral["absent"]:
        record["colour_unmeasured_because"] = neutral["reason"]
    return record


def extract_still(source_file: str, out_path: str,
                  at_seconds: float | None = None) -> bool:
    """Draw the representative still of one shot. Returns what happened.

    The still is taken at the middle of the sampled span
    (`CHROMA_SAMPLE_SECONDS / 2`) unless `at_seconds` names a moment, so
    the picture the colourist sees IS of the seconds the numbers
    describe. A still that was not drawn is False, never a zero-byte
    file: ffmpeg can exit 0 leaving nothing behind, and a reader must
    never open that as a picture.
    """
    if not source_file or not os.path.exists(source_file):
        return False
    moment = (CHROMA_SAMPLE_SECONDS / 2.0
              if at_seconds is None else float(at_seconds))
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-ss", f"{max(0.0, moment):.2f}",
             "-i", source_file,
             "-vframes", "1",
             "-vf", f"scale={STILL_WIDTH}:-1",
             "-q:v", "3", out_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=FFMPEG_TIMEOUT_SECONDS, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        return False
    if not os.path.exists(out_path) or os.path.getsize(out_path) <= 0:
        if os.path.exists(out_path):
            os.remove(out_path)
        return False
    return True
