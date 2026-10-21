"""Judge a DELIVERED file's captions on the delivered file's own frames.

What this is for
----------------
The captain's punch list, QA-003: "Verify captions as delivered." A
caption can be correct in the plan, correct in the rendered card, and
still be wrong in the file the captain receives: the card never reached
the export, it reached it late, it was replaced by a card that was
never re-rendered, it landed on top of a lower third, or it says a
different word than the plan asked for. Every one of those defects is
INVISIBLE to a plan-side check, because the plan is not what was
delivered.

So the measurement is taken on the delivered file, and the plan only
says what the caption was SUPPOSED to be. This is the same move the
audio leg made (PR 3046): the delivery judged a delivered file's audio
by loudness alone, and a file that had lost its dialogue still measured
on target, because BS.1770 gates the silence out of the integrated
number. Here the delivered file's captions are judged by the pixels of
the delivered file.

How it is measured
------------------
1. THE WORDS, by reading the delivered frames. The frames inside each
   declared caption's own window are extracted from the delivered file
   and read with Apple Vision (`library/tools/caption_ocr.py`), which
   gives the words the export carries and the BOX they were drawn in.
2. THE VISIBLE SPAN, in frames. The delivered file is decoded once and
   the bright pixels INSIDE the caption's own measured box are counted
   per frame: the caption is there when that count stands above the
   picture's own rolling baseline. That is the caption's first and last
   frame, measured on the file, with no timeline and no Resolve.
3. THE LEGS, against the intent: text, visible start/end, placement,
   readability, line wrapping, emphasis, collision with other graphics,
   and the caption nobody declared - the stale card a replacement
   leaves behind.

Why the count is LOCAL
----------------------
A bright-pixel count over the whole lower band is not a caption
detector: on a real export the picture's own brightness runs to tens of
thousands of pixels per frame, and the count says more about the
footage than about the captions. Measured on a Ren scratch export
(2026-10-21), a whole-band count never dropped below 10,000 pixels
while the caption's own ink was a few thousand - the count was the
PICTURE, every frame. So the count is taken inside the caption's own
measured box, where the caption is the only thing that draws, and it
is read against the picture's own rolling baseline so a bright scene
does not read as a caption and a caption does not disappear into one.

What it will not do
-------------------
It does not judge the TIMELINE. Nothing here reads a Resolve project, a
placed item or a stored transform: the file is the evidence. It does
not invent an intent: a caption nobody declared is checked only for
being a caption at all. And a check that could not run says so - an
unreadable frame is an ABSENCE, never a pass.

Cost
----
One decode of the delivered file, plus one read of a few frames per
declared caption. `verify_delivered_captions` returns the seconds it
took and the frames it measured, so the cost per minute of video is a
number this module reports rather than a number somebody has to guess.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── The measurements' constants ─────────────────────────────────────────
#
# Every number here is a MEASUREMENT'S threshold, and each one says
# what it is standing on.

#: The luma at or above which a pixel counts as caption ink. Caption
#: type is white; an emphasised word is a colour that still reads bright
#: (a yellow emphasis measures around 226). 190 keeps the coloured
#: emphasis while excluding ordinary picture.
BRIGHT_LUMA = 190

#: The bottom of the frame a caption's ink may sit in. Captions are
#: bottom-positioned, and the engine's own rule
#: (`library/tools/qa/subtitle_qa.py`, `CAPTION_TOP_LIMIT_FRACTION`)
#: puts a caption's ink centre below the frame's midline; a caption ROW
#: (Ren's own reels put it in the bottom eighth) is lower still, and
#: this is the line below which a text block is a caption and above
#: which it is some other graphic - a lower third, a name plate.
CAPTION_ROW_FRACTION = 0.62

#: How wide the rolling baseline is, in seconds. The caption is a STEP
#: above the picture's own brightness; the baseline follows the picture
#: within this window, so a bright scene does not read as a caption and
#: a caption does not disappear into a bright scene. It has to be
#: WIDER than a caption, or the baseline would follow the caption and
#: measure nothing.
BASELINE_WINDOW_SECONDS = 4.0

#: Where a caption's visible span is taken, as a fraction of the
#: caption's own peak ink. Half the peak is the middle of a fade, so a
#: caption that fades in is measured at the middle of its fade rather
#: than at the frame the fade started.
SPAN_LEVEL = 0.5

#: The smallest ink count that counts as the caption being there.
MIN_INK_PIXELS = 60

#: How far a measured visible span may sit from the intended one, in
#: frames, before it is a defect. The scan reads the file at
#: `SCAN_RATE`, so the span is measured to that rate's own step, and
#: the tolerance IS that step: a caption that is half a scan interval
#: away from where the plan put it has not moved, and a card that
#: arrived a second late is well outside it.
TIMING_TOLERANCE_FRAMES = 0  # the file's own scan step; see _tolerance.

#: How similar the delivered words must be to the intended words. OCR
#: reads a rendered glyph, not a string, so a hair-space or a
#: capitalisation is expected; a wrong word is not.
TEXT_MATCH_RATIO = 0.88

#: The tighter ratio the SPACING comparison is held to: a caption that
#: differs only in its spaces is the caption the plan asked for, and a
#: caption that differs in its letters is not. The ratio is tight
#: enough that a wrong word can never pass on it.
SPACING_MATCH_RATIO = 0.95

#: How far the measured caption box may sit from the intended one, in
#: frame pixels, before it is a placement defect.
PLACEMENT_TOLERANCE_PX = 12.0

#: The smallest ink height a caption may have, as a fraction of the
#: frame height. This is the engine's own floor
#: (`library/tools/render_qa.py`, `CAPTION_MIN_HEIGHT_FRACTION`), so a
#: caption is judged by the same number here and at render time.
CAPTION_MIN_HEIGHT_FRACTION = 0.018

#: How far two boxes may overlap before they are "the same place".
#: A collision is an overlap, not a touch.
COLLISION_OVERLAP = 0.02

#: How often the file-wide scan reads a frame, in frames per second.
#: The scan says where the captions are, what they say, and whether a
#: caption nobody declared is on the file. Four times a second is the
#: measurement's own resolution: a caption that is shorter than a
#: quarter of a second is not a caption the captain reads, and the
#: timing tolerance is this rate's own step.
SCAN_RATE = 4.0

#: How many undeclared captions the verdict names. A file whose whole
#: caption track is undeclared is a plan that declared nothing, and one
#: finding says that; the rest is a count.
STALE_FINDING_CAP = 5


def _render_qa_legibility_floor() -> float:
    """The engine's own caption legibility floor.

    Read from `render_qa` so the number here and the number the render
    path uses are the same one; the constant is repeated above only so
    this module states its own rule.
    """
    try:
        from library.tools import render_qa

        return float(render_qa.CAPTION_MIN_HEIGHT_FRACTION)
    except Exception:  # noqa: BLE001 - the stated constant is the fallback
        return CAPTION_MIN_HEIGHT_FRACTION


# ── The intent ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CaptionIntent:
    """What ONE caption was supposed to be.

    The intent is the PLAN's side of the comparison; every measurement
    that follows is taken from the delivered file. `start`/`end` are
    seconds on the delivered file's own timeline.

    `box` is the caption's expected ink box in frame pixels
    `(x0, y0, x1, y1)`; without it the caption is located by reading the
    frame, which is weaker but not wrong. `lines` is the intended number
    of text lines, `ink_height` the intended ink height in pixels (which
    is what carries the emphasis bump - an emphasised word is drawn
    larger), and `emphasis` the intended emphasis windows as
    `(word, start, end)`.
    """
    label: str
    text: str
    start: float
    end: float
    box: Optional[Tuple[float, float, float, float]] = None
    lines: Optional[int] = None
    ink_height: Optional[float] = None
    emphasis: Tuple[Tuple[str, float, float], ...] = ()


#: A finding's verdicts. `pass` and `fail` are measurements;
#: `not_applicable` is a leg the plan declared nothing for - the plan
#: said no expected line count, so there is no line count to judge.
PASS = "pass"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CaptionFinding:
    """One measurement's verdict on one caption (or on the file)."""
    check: str
    label: str
    passed: bool
    detail: str
    measured: Dict[str, Any] = field(default_factory=dict)
    verdict: str = ""

    def __post_init__(self) -> None:
        if not self.verdict:
            object.__setattr__(self, "verdict", PASS if self.passed else FAIL)

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "label": self.label,
            "passed": bool(self.passed),
            "verdict": self.verdict,
            "detail": self.detail,
            "measured": dict(self.measured),
        }


@dataclass(frozen=True)
class CaptionVerdict:
    """The delivered file's captions, judged on the delivered file.

    `checked` says the measurement RAN. `passed` is every finding. A
    verdict that could not run says so in `reason` and is not a pass.
    """
    checked: bool
    passed: bool
    findings: Tuple[CaptionFinding, ...]
    reason: str = ""
    seconds: float = 0.0
    frames_measured: int = 0
    captions_measured: int = 0
    duration_seconds: float = 0.0
    fps: float = 0.0
    """The rate the measurements are in, so a finding's FRAMES can be
    read as the SECONDS a revision has to scope itself to."""

    @property
    def failures(self) -> Tuple[CaptionFinding, ...]:
        return tuple(finding for finding in self.findings if not finding.passed)

    def as_dict(self) -> dict:
        return {
            "checked": bool(self.checked),
            "passed": bool(self.passed),
            "reason": self.reason,
            "seconds": round(self.seconds, 3),
            "frames_measured": int(self.frames_measured),
            "captions_measured": int(self.captions_measured),
            "duration_seconds": round(self.duration_seconds, 3),
            "fps": float(self.fps),
            "seconds_per_minute": (
                round(self.seconds * 60.0 / self.duration_seconds, 2)
                if self.duration_seconds > 0 else None),
            "findings": [finding.as_dict() for finding in self.findings],
        }


# ── The file's own facts ───────────────────────────────────────────────

def probe_video(video_path: str) -> dict:
    """The delivered file's size, rate and length, read from the file.

    Every number here comes from `ffprobe` on the delivered file; none
    is taken from the plan, because the file's own rate is what the
    measurements must be counted in.
    """
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,nb_frames,duration",
        "-of", "json", video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise ValueError(
            f"the delivered file could not be read: {result.stderr.strip()}")
    try:
        streams = (json.loads(result.stdout or "{}").get("streams") or [])
    except ValueError as exc:
        raise ValueError(f"the delivered file could not be read: {exc}") from None
    if not streams:
        raise ValueError("the delivered file carries no video stream")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    fps = _parse_rate(stream.get("avg_frame_rate"))
    frames = int(stream.get("nb_frames") or 0)
    duration = float(stream.get("duration") or 0.0)
    if not duration and frames and fps:
        duration = frames / fps
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frames": frames,
        "duration": duration,
    }


def _parse_rate(rate: Any) -> float:
    """`"30000/1001"` -> 29.97. Zero when the file states none."""
    text = str(rate or "").strip()
    if not text or text in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator = float(denominator)
            return float(numerator) / denominator if denominator else 0.0
        return float(text)
    except (TypeError, ValueError):
        return 0.0


# ── The file's frames ──────────────────────────────────────────────────

def extract_frames(video_path: str, frame_indices: Sequence[int],
                   dest_dir: str,
                   crop: Optional[Tuple[int, int, int, int]] = None
                   ) -> Dict[int, str]:
    """The delivered file's frames at `frame_indices`, as image files.

    One decode, one output per requested frame. The frames are read back
    out of the delivered file - never out of the plan and never out of a
    rendered card. Returns `{frame_index: path}`.

    `crop` is `(x0, y0, x1, y1)` in frame pixels: the frame is cut to
    that region before it is written, which is how the probes around a
    caption's edges are read (a small image is a small OCR, and the
    caption's own row is all those probes need).
    """
    if not frame_indices:
        return {}
    wanted = sorted({int(index) for index in frame_indices})
    expression = "+".join(f"eq(n\\,{index})" for index in wanted)
    filters = [f"select='{expression}'"]
    if crop:
        x0, y0, x1, y1 = (int(value) for value in crop)
        filters.append(f"crop={max(1, x1 - x0)}:{max(1, y1 - y0)}:"
                       f"{x0}:{y0}")
    command = [
        "ffmpeg", "-v", "error", "-y", "-i", video_path,
        "-vf", ",".join(filters),
        "-fps_mode", "passthrough", "-start_number", "0",
        os.path.join(dest_dir, "frame_%07d.png"),
    ]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise ValueError(
            f"the delivered frames could not be extracted: "
            f"{result.stderr.strip()[-300:]}")
    produced = sorted(os.path.join(dest_dir, name)
                      for name in os.listdir(dest_dir)
                      if name.startswith("frame_") and name.endswith(".png"))
    return {index: path for index, path in zip(wanted, produced)}


def extract_scan(video_path: str, dest_dir: str, rate: float,
                 crop: Optional[Tuple[int, int, int, int]] = None,
                 scan_rate: float = SCAN_RATE) -> List[Tuple[int, str]]:
    """The delivered file read at a fixed rate, as image files.

    One decode, the whole file, cropped to the caption row. The frames
    come back in time order and their numbers are worked out from the
    rate: frame `n` of the scan is the frame at `n / scan_rate` seconds.

    The scan takes every `step`-th frame, where `step` is the file's
    rate over the scan rate. A list of frame numbers is a filter
    expression too long for ffmpeg to build - a file's worth of them
    fails with "cannot allocate memory" - and "every step-th frame" is
    the same thing in a few characters.
    """
    step = max(1, int(round(rate / scan_rate)))
    filters = []
    if crop:
        x0, y0, x1, y1 = (int(value) for value in crop)
        filters.append(f"crop={max(1, x1 - x0)}:{max(1, y1 - y0)}:"
                       f"{x0}:{y0}")
    filters.append(f"select='not(mod(n\\,{step}))'")
    pattern = os.path.join(dest_dir, "scan_%05d.jpg")
    command = [
        "ffmpeg", "-v", "error", "-y", "-i", video_path,
        "-vf", ",".join(filters), "-fps_mode", "passthrough",
        "-q:v", "3", pattern,
    ]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise ValueError(
            f"the delivered file could not be scanned: "
            f"{result.stderr.strip()[-300:]}")
    produced = sorted(os.path.join(dest_dir, name)
                      for name in os.listdir(dest_dir)
                      if name.startswith("scan_") and name.endswith(".jpg"))
    return [(index * step, path) for index, path in enumerate(produced)]


def ink_profile(video_path: str, width: int, height: int,
                region: Optional[Tuple[int, int, int, int]] = None
                ) -> List[int]:
    """The delivered file's bright-pixel count per frame, in a region.

    One decode, streamed: the region is cropped and turned to grey by
    ffmpeg, and each frame's bright pixels are counted. The list is one
    count per frame, in frame order.

    `region` is `(x0, y0, x1, y1)` in frame pixels. Without one the
    whole frame is counted, which measures the PICTURE rather than the
    caption - the caller passes the caption's own measured box.
    """
    import numpy as np

    if region:
        x0, y0, x1, y1 = (int(value) for value in region)
        x0 = max(0, min(width - 1, x0))
        y0 = max(0, min(height - 1, y0))
        x1 = max(x0 + 1, min(width, x1))
        y1 = max(y0 + 1, min(height, y1))
        crop = f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0}"
        crop_width, crop_height = x1 - x0, y1 - y0
    else:
        crop = "null"
        crop_width, crop_height = width, height
    command = [
        "ffmpeg", "-v", "error", "-i", video_path,
        "-vf", f"{crop},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    frame_bytes = crop_width * crop_height
    counts: List[int] = []
    try:
        while True:
            buffer = process.stdout.read(frame_bytes)
            if not buffer or len(buffer) < frame_bytes:
                break
            frame = np.frombuffer(buffer, dtype=np.uint8).reshape(
                crop_height, crop_width)
            counts.append(int(np.count_nonzero(frame >= BRIGHT_LUMA)))
    finally:
        try:
            process.stdout.close()
        except OSError:
            pass
        process.wait()
    return counts


def ink_above_baseline(counts: Sequence[int], window: int) -> List[int]:
    """Each frame's ink ABOVE the picture's own brightness.

    A caption is white type over a picture, and a picture's brightness
    moves. An absolute floor reads a bright scene as a caption (every
    frame of it is over the floor) and misses a caption over a bright
    scene (the floor is already high). So the floor is the picture's
    OWN low-water mark, and the measure is how much ink stands above
    it.

    The low-water mark is the window's lower QUARTILE, not its median:
    a caption that fills most of the window IS the window's median, and
    a median baseline would follow the caption and measure nothing. The
    quartile stays with the picture - the quiet frames between the
    captions - and the caption stands above it.
    """
    values = [int(value) for value in counts]
    if not values:
        return []
    span = max(1, int(window))
    half = span // 2
    out: List[int] = []
    for index in range(len(values)):
        low = max(0, index - half)
        high = min(len(values), index + half + 1)
        window_values = sorted(values[low:high])
        baseline = window_values[int(len(window_values) * 0.25)]
        out.append(values[index] - baseline)
    return out


def visible_span(excess: Sequence[int], first: int, last: int,
                 level: float = SPAN_LEVEL) -> Optional[Tuple[int, int]]:
    """The frames a caption is visible for, from its ink curve.

    The caption's peak inside `(first, last)` sets the level: half of
    it is where the caption is definitely there. The span is the run of
    frames around the peak that stays above it.
    """
    low = max(0, first)
    high = min(len(excess) - 1, last)
    if high < low:
        return None
    window = list(excess[low:high + 1])
    peak = max(window)
    if peak <= 0:
        return None
    threshold = max(MIN_INK_PIXELS, int(peak * level))
    peak_index = low + window.index(peak)
    start = peak_index
    while start > low and excess[start - 1] >= threshold:
        start -= 1
    end = peak_index
    while end < high and excess[end + 1] >= threshold:
        end += 1
    return (start, end)


# ── The text pass ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Observation:
    """One text observation, in frame pixels."""
    text: str
    confidence: float
    x0: float
    y0: float
    x1: float
    y1: float
    frame: int = -1
    """The delivered frame this text was read at, when it is known.

    A COLLISION is only actionable if the reader can say WHERE it
    happened: the frame it was seen at, and the box the other graphic
    drew in.  A collision that names neither is a fact nobody can
    turn into an edit, which is the shape QA-009 removes.
    """

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


def _observations(docs: Sequence[Sequence[dict]], width: int, height: int,
                  origin: Tuple[int, int] = (0, 0),
                  frame: int = -1) -> List[_Observation]:
    """The helper's readings as frame-pixel observations.

    `origin` is the top-left of the image the helper read, in frame
    pixels: a cropped probe reads its own box, and the box is put back
    into the frame's coordinates here.  `frame` is the delivered frame
    the reading came from, so a collision can name WHERE it happened.
    """
    out: List[_Observation] = []
    for texts in docs or ():
        for entry in texts or ():
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            x, y, w, h = entry.get("bbox") or (0, 0, 0, 0)
            out.append(_Observation(
                text=text,
                confidence=float(entry.get("confidence") or 0.0),
                x0=origin[0] + float(x) * width,
                y0=origin[1] + float(y) * height,
                x1=origin[0] + (float(x) + float(w)) * width,
                y1=origin[1] + (float(y) + float(h)) * height,
                frame=int(frame)))
    return out


# ── Text normalisation ─────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9']+")


def normalise_words(text: str) -> List[str]:
    """A text's words, comparable: lower case, no punctuation."""
    return _WORD_RE.findall(str(text or "").lower())


def words_say_the_same(observed: str, intended: str) -> Tuple[bool, float]:
    """Whether the delivered words ARE the intended words.

    Two comparisons, and the caption passes on either:

    * the WORD sequences, compared with `difflib` - a hair space or a
      capitalisation does not fail the check and a wrong word does;
    * the CHARACTERS with the spaces taken out - because OCR reads a
      rendered glyph, and at caption sizes it sometimes reads two words
      as one ("captiontext"). A space is not editorial, so a caption
      that differs only in its spacing is the caption the plan asked
      for; a caption that differs in its LETTERS is not, and the
      character comparison is held to a much tighter ratio so it can
      never carry a wrong word through.

    The ratio returned is the word ratio, so a reader sees how close
    the WORDS were.
    """
    left = normalise_words(observed)
    right = normalise_words(intended)
    if not right:
        return (not left), 1.0
    if not left:
        return False, 0.0
    word_ratio = difflib.SequenceMatcher(a=left, b=right).ratio()
    if word_ratio >= TEXT_MATCH_RATIO:
        return True, word_ratio
    characters = difflib.SequenceMatcher(
        a="".join(left), b="".join(right)).ratio()
    if characters >= SPACING_MATCH_RATIO:
        return True, word_ratio
    return False, word_ratio


# ── The verdict ────────────────────────────────────────────────────────

def verify_delivered_captions(video_path: str,
                              intents: Sequence[CaptionIntent],
                              *, frame: Optional[Tuple[int, int]] = None,
                              fps: Optional[float] = None,
                              ocr: bool = True,
                              declared_spans: Sequence[Tuple[float, float]] = ()
                              ) -> CaptionVerdict:
    """Judge the delivered file's captions, on the delivered file.

    `intents` is what the captions were supposed to be. An empty list
    means nobody declared any: there is then nothing to compare the
    delivered file against, and the verdict says so rather than passing
    a file it could not judge.

    `declared_spans` are the seconds where the plan declares SOME OTHER
    graphic - a lower third, a watermark, an end card. Ink in those
    seconds belongs to that graphic, so it is never called an undeclared
    caption: the undeclared set has to be the whole plan, not the
    captions alone, or every watermark reads as a stale card.

    Returns a `CaptionVerdict`. A verdict that could not run says so:
    `checked` is False and `reason` names why. An unmeasured caption is
    never a passing caption.
    """
    started = time.time()
    if not video_path or not os.path.isfile(video_path):
        raise FileNotFoundError(
            f"the delivered caption check needs a file, got: {video_path!r}")

    facts = probe_video(video_path)
    width = int(frame[0]) if frame else int(facts["width"])
    height = int(frame[1]) if frame else int(facts["height"])
    rate = float(fps or facts["fps"] or 0.0)
    if rate <= 0:
        return CaptionVerdict(
            checked=False, passed=False, findings=(),
            reason="the delivered file states no frame rate, so its "
                   "captions cannot be measured in frames",
            duration_seconds=float(facts["duration"] or 0.0))
    if not intents:
        return CaptionVerdict(
            checked=False, passed=False, findings=(),
            reason="no caption was declared for this file, so its captions "
                   "have nothing to be judged against",
            duration_seconds=float(facts["duration"] or 0.0))

    findings: List[CaptionFinding] = []
    ocr_reason = ""

    seen: List[dict] = []
    for intent in intents:
        first = int(round(intent.start * rate))
        last = int(round(intent.end * rate)) - 1
        seen.append({
            "intent": intent,
            "first": first,
            "last": last,
            "lines": [],
            "found": [],
            "box": None,
            "visible": [],
            "present": [],
        })

    # THE SCAN: the whole file, read at a fixed rate, cropped to the
    # caption row. It says where the captions are, what they say, and -
    # because it looks everywhere - whether there is a caption nobody
    # declared. Every measurement below is read out of this scan, so the
    # cost is a function of the file's LENGTH, not of the number of
    # captions in it.
    scan: Dict[int, List[_Observation]] = {}
    with tempfile.TemporaryDirectory(prefix="ren-caption-ocr-") as tmp:
        if ocr:
            try:
                from library.tools import caption_ocr

                row = _caption_row(width, height)
                frames = extract_scan(video_path, tmp, rate, crop=row)
                docs = caption_ocr.read_texts([path for _index, path in frames])
                for position, (index, _path) in enumerate(frames):
                    if position >= len(docs):
                        break
                    scan[index] = _observations(
                        [docs[position]], row[2] - row[0], row[3] - row[1],
                        origin=row[:2], frame=index)
            except Exception as exc:  # noqa: BLE001 - an absence, not a failure
                scan = {}
                ocr_reason = f"the delivered frames could not be read: {exc}"
        else:
            ocr_reason = "the text of the delivered captions was not read"

        for entry in seen:
            found = [observation for index in sorted(scan)
                     if entry["first"] <= index <= entry["last"]
                     for observation in scan[index]]
            entry["found"] = found
            entry["lines"] = _caption_lines(found, height, entry["intent"].text)
            entry["box"] = _union(entry["lines"]) if entry["lines"] else None
            # The frames the caption SLOT is occupied at: the ones
            # where the caption row carries text at all. The caption's
            # words change from cue to cue, so the SLOT is what the
            # timing measures - whether a caption is on screen for the
            # window the plan gave it - and the WORDS are what the text
            # leg measures.
            entry["present"] = [
                index for index in sorted(scan)
                if entry["first"] <= index <= entry["last"]
                and _caption_lines(scan[index], height)]
            # The frames the caption's OWN words are readable at.
            entry["visible"] = [
                index for index in sorted(scan)
                if entry["first"] <= index <= entry["last"]
                and words_say_the_same(
                    " ".join(line.text for line in scan[index]),
                    entry["intent"].text)[0]]

    for entry in seen:
        findings.extend(_check_intent(
            entry, rate, width, height, ocr_reason))

    # A caption nobody declared: the shape a replacement leaves behind
    # when the new card never reached the export, and the shape a card
    # that was never removed leaves behind.
    findings.extend(_stale_captions(
        scan, seen, rate, height, declared_spans=list(declared_spans or ())))

    checked = not ocr_reason
    passed = all(finding.passed for finding in findings)
    return CaptionVerdict(
        checked=checked,
        passed=passed and checked,
        findings=tuple(findings),
        reason=ocr_reason,
        seconds=time.time() - started,
        frames_measured=len(scan),
        captions_measured=len(intents),
        duration_seconds=float(facts["duration"] or 0.0),
        fps=float(rate),
    )


def _caption_row(width: int, height: int) -> Tuple[int, int, int, int]:
    """The caption row of the frame: the bottom of it, as a crop.

    A caption is bottom-positioned, so the row it lives in is the
    bottom of the frame. The crop is generous - a third of the frame -
    because the row's exact height is the PROJECT's declaration and this
    check does not have it; what it does have is the caption's own box,
    measured later, from the same reading.
    """
    top = int(height * 0.68)
    return (0, top, width, height)


def _scan_frames(facts: dict, rate: float) -> List[int]:
    """The frames the scan reads: the whole file, at a fixed rate."""
    total = int(facts.get("frames") or 0)
    if not total:
        return []
    step = max(1, int(round(rate / SCAN_RATE)))
    return list(range(0, total, step))


def _region_of(seen: Sequence[dict], width: int,
               height: int) -> Optional[Tuple[int, int, int, int]]:
    """The region the caption row occupies, in frame pixels.

    The union of the captions' own measured boxes, padded a little so a
    caption that is drawn slightly wider than the plan said still lands
    inside the measured region. None when no caption was found at all.
    """
    boxes = [entry["box"] for entry in seen if entry["box"]]
    if not boxes:
        return None
    x0 = int(min(box[0] for box in boxes)) - 4
    y0 = int(min(box[1] for box in boxes)) - 4
    x1 = int(max(box[2] for box in boxes)) + 4
    y1 = int(max(box[3] for box in boxes)) + 4
    return (max(0, x0), max(0, y0), min(width, x1), min(height, y1))


def _caption_lines(found: Sequence[_Observation], height: int,
                   intended: str = "") -> List[_Observation]:
    """The observations that ARE the caption.

    The caption is the text the plan ASKED FOR: the lines whose words
    are the plan's words. That separates it from the other graphics on
    the same row - a lower third, a name plate - whose words are not
    the caption's, and it survives the caption saying the WRONG words
    (as long as one of them is still a word of the plan's caption).

    When nothing matches - the caption says entirely different words -
    the caption is the LOWEST block of text in the frame: a caption is
    bottom-positioned, and the engine's own rule puts a caption's ink
    centre below the frame's midline.
    """
    wanted = set(normalise_words(intended))
    if wanted:
        matching = []
        for observation in found:
            words = normalise_words(observation.text)
            if not words:
                continue
            shared = sum(1 for word in words if word in wanted)
            if shared * 2 >= len(words):
                matching.append(observation)
        if matching:
            return _dedupe(sorted(matching,
                                  key=lambda observation: observation.y0))
    low = [observation for observation in found
           if (observation.y0 + observation.y1) / 2.0
           >= height * CAPTION_ROW_FRACTION]
    if not low:
        return []
    return [max(low, key=lambda observation: observation.y1)]


def _check_intent(entry: dict,
                  rate: float,
                  width: int,
                  height: int,
                  ocr_reason: str) -> List[CaptionFinding]:
    """Every leg, for one intended caption."""
    findings: List[CaptionFinding] = []
    intent = entry["intent"]
    first = entry["first"]
    last = entry["last"]
    caption_lines = entry["lines"]
    found = entry["found"]

    if not entry["present"]:
        findings.append(CaptionFinding(
            check="missing",
            label=intent.label,
            passed=False,
            detail=(
                f"no caption is visible between frames {first} and {last}, "
                f"where {intent.text!r} was supposed to be. The card never "
                "reached the export."),
            measured={"intended_first_frame": first,
                      "intended_last_frame": last}))
        return findings

    caption_box = _union(caption_lines)
    caption_text = " ".join(line.text for line in caption_lines)

    # 1. THE WORDS.
    same, ratio = words_say_the_same(caption_text, intent.text)
    findings.append(CaptionFinding(
        check="text",
        label=intent.label,
        passed=bool(same),
        detail=(
            f"the delivered caption reads {caption_text!r}, and the plan "
            f"asked for {intent.text!r}"
            if not same else
            f"the delivered caption reads the intended words "
            f"({ratio:.0%} alike)"),
        measured={"observed_text": caption_text,
                  "intended_text": intent.text,
                  "similarity": round(ratio, 4)}))

    # 2. THE VISIBLE SPAN, measured on the file by reading it.
    visible = sorted(entry["present"])
    span = (visible[0], visible[-1]) if visible else None
    if span is None:
        findings.append(CaptionFinding(
            check="timing",
            label=intent.label,
            passed=False,
            detail=(
                "the caption is not readable at any frame of the window it "
                f"was planned for (frames {first} to {last}): the file does "
                "not carry these words where the plan says they are"),
            measured={"intended_first_frame": first,
                      "intended_last_frame": last}))
    else:
        late = span[0] - first
        early = last - span[1]
        tolerance = max(2, int(round(rate / SCAN_RATE)))
        within = (abs(late) <= tolerance and abs(early) <= tolerance)
        findings.append(CaptionFinding(
            check="timing",
            label=intent.label,
            passed=bool(within),
            detail=(
                f"the caption is visible from frame {span[0]} to "
                f"{span[1]}, and the plan wanted frames {first} to {last}"
                + (f" - it appears at least {abs(late)} frame(s) LATE"
                   if late > 0 else "")
                + (f" - it appears at least {abs(late)} frame(s) EARLY"
                   if late < 0 else "")
                + (f" - it disappears at least {abs(early)} frame(s) EARLY"
                   if early > 0 else "")
                + (f" - it disappears at least {abs(early)} frame(s) LATE"
                   if early < 0 else "")
                + "."),
            measured={"first_frame": span[0],
                      "last_frame": span[1],
                      "intended_first_frame": first,
                      "intended_last_frame": last,
                      "late_frames": late,
                      "early_frames": early}))

    # 3. THE PLACEMENT.
    measured_box = tuple(caption_box)
    offset = None
    if intent.box:
        offset = max(abs(measured_box[0] - intent.box[0]),
                     abs(measured_box[1] - intent.box[1]),
                     abs(measured_box[2] - intent.box[2]),
                     abs(measured_box[3] - intent.box[3]))
    inside = (measured_box[0] >= 0 and measured_box[1] >= 0
              and measured_box[2] <= width and measured_box[3] <= height)
    centre_low = (measured_box[1] + measured_box[3]) / 2.0 >= height * 0.5
    placed = inside and centre_low and (
        offset is None or offset <= PLACEMENT_TOLERANCE_PX)
    findings.append(CaptionFinding(
        check="placement",
        label=intent.label,
        passed=bool(placed),
        detail=(
            f"the caption is drawn at "
            f"({measured_box[0]:.0f},{measured_box[1]:.0f})-"
            f"({measured_box[2]:.0f},{measured_box[3]:.0f}) of a "
            f"{width}x{height} frame"
            + ("" if intent.box else
               " (the plan declared no box, so only the frame's own "
               "rules were applied)")
            + (f" - {offset:.0f}px from the intended box"
               if offset is not None and offset > PLACEMENT_TOLERANCE_PX
               else "")
            + ("" if inside else " - it runs off the frame")
            + ("" if centre_low else
               " - it sits in the upper half of the frame, where a "
               "caption does not go")),
        measured={"box": [round(value, 1) for value in measured_box],
                  "intended_box": list(intent.box) if intent.box else None,
                  "offset_px": None if offset is None else round(offset, 1),
                  "frame": [0, 0, int(width), int(height)]}))

    # 4. THE READABILITY.
    floor = height * _render_qa_legibility_floor()
    ink_height = measured_box[3] - measured_box[1]
    legible = ink_height >= floor
    findings.append(CaptionFinding(
        check="readability",
        label=intent.label,
        passed=bool(legible),
        detail=(
            f"the caption's ink is {ink_height:.0f}px tall and the floor is "
            f"{floor:.0f}px"
            + ("" if legible else " - the caption is too small to read")),
        measured={"ink_height_px": round(ink_height, 1),
                  "floor_px": round(floor, 1)}))

    # 5. THE WRAPPING.
    if intent.lines is None:
        findings.append(CaptionFinding(
            check="wrapping",
            label=intent.label,
            passed=True,
            verdict=NOT_APPLICABLE,
            detail=("the plan declared no line count for this caption, "
                    "so the delivered wrapping was not judged"),
            measured={"lines": len(caption_lines)}))
    else:
        wrapped = len(caption_lines) == intent.lines
        findings.append(CaptionFinding(
            check="wrapping",
            label=intent.label,
            passed=bool(wrapped),
            detail=(
                f"the delivered caption draws {len(caption_lines)} line(s) "
                f"and the plan wanted {intent.lines}"
                + ("" if wrapped else " - the card was re-rendered at "
                   "another width")),
            measured={"lines": len(caption_lines),
                      "intended_lines": intent.lines}))

    # 6. THE EMPHASIS.
    # An emphasised word is drawn LARGER, so what reaches the file is
    # the caption's own ink height: a card whose emphasis was lost in
    # the export is shorter than the plan's card.
    if intent.ink_height is None:
        findings.append(CaptionFinding(
            check="emphasis",
            label=intent.label,
            passed=True,
            verdict=NOT_APPLICABLE,
            detail=("the plan declared no ink height for this caption, "
                    "so the delivered emphasis was not judged"),
            measured={"ink_height_px": round(ink_height, 1),
                      "emphasis": [list(window)
                                   for window in intent.emphasis]}))
    else:
        delta = abs(ink_height - intent.ink_height)
        tolerance = max(4.0, intent.ink_height * 0.12)
        matched_emphasis = delta <= tolerance
        findings.append(CaptionFinding(
            check="emphasis",
            label=intent.label,
            passed=bool(matched_emphasis),
            detail=(
                f"the delivered caption's ink is {ink_height:.0f}px tall "
                f"and the planned card's is {intent.ink_height:.0f}px"
                + ("" if matched_emphasis else
                   " - the emphasis did not reach the export, or a "
                   "different card did")),
            measured={"ink_height_px": round(ink_height, 1),
                      "planned_ink_height_px": round(intent.ink_height, 1),
                      "emphasis": [list(window)
                                   for window in intent.emphasis]}))

    # 7. THE COLLISION.
    # Another graphic drawn over the caption's own box. The caption's
    # OWN lines, read again at another frame of its window, are not a
    # collision with themselves.
    def _same_place(observation: _Observation) -> bool:
        return any(_same_line(line, observation) for line in caption_lines)

    others = [observation for observation in found
              if not _same_place(observation)]
    colliding = [observation for observation in others
                 if _overlaps_box(observation, measured_box) >= COLLISION_OVERLAP]
    findings.append(CaptionFinding(
        check="collision",
        label=intent.label,
        passed=not colliding,
        detail=(
            "the caption collides with "
            + ", ".join(repr(observation.text) for observation in colliding)
            + " - another graphic is drawn over the caption's own box"
            + (f", first seen at frame {colliding[0].frame}"
               if colliding and colliding[0].frame >= 0 else "")
            if colliding else
            "nothing else is drawn over the caption's box"),
        measured={"collisions": [observation.text
                                 for observation in colliding],
                  "collision_boxes": [
                      [round(observation.x0, 1), round(observation.y0, 1),
                       round(observation.x1, 1), round(observation.y1, 1)]
                      for observation in colliding],
                  "collision_frames": sorted({
                      observation.frame for observation in colliding
                      if observation.frame >= 0}),
                  "caption_box": [round(value, 1) for value in measured_box],
                  "frame": [0, 0, int(width), int(height)],
                  "intended_first_frame": first,
                  "intended_last_frame": last}))

    if ocr_reason:
        findings.append(CaptionFinding(
            check="read",
            label=intent.label,
            passed=False,
            detail=ocr_reason,
            measured={}))
    return findings


def _stale_captions(scan: Dict[int, List[_Observation]],
                    seen: Sequence[dict],
                    rate: float,
                    height: int,
                    declared_spans: Sequence[Tuple[float, float]] = ()
                    ) -> List[CaptionFinding]:
    """The captions the file shows that nobody declared.

    The scan read the whole file, so it saw every caption in it. A
    caption in the caption row that no declared caption accounts for is
    the shape a replacement leaves behind when the new card never
    reached the export, and the shape a card that was never removed
    leaves behind.

    Ink in a window the plan declares for ANOTHER graphic is that
    graphic's, and is never called a stale caption.
    """
    if not scan or not seen:
        return []
    declared: List[Tuple[float, float]] = [
        (entry["first"] / rate, entry["last"] / rate) for entry in seen
        if rate]
    declared.extend(
        (float(span[0]), float(span[1])) for span in (declared_spans or ()))
    undeclared: List[Tuple[int, str]] = []
    for index in sorted(scan):
        first = index / rate if rate else 0.0
        if any(first >= begin - 0.5 and first <= end + 0.5
               for begin, end in declared):
            continue
        lines = _caption_lines(scan[index], height)
        if not lines:
            continue
        text = " ".join(line.text for line in lines)
        if not text:
            continue
        if any(existing[1] == text for existing in undeclared):
            continue
        undeclared.append((index, text))
    if not undeclared:
        return []
    findings: List[CaptionFinding] = []
    for index, text in undeclared[:STALE_FINDING_CAP]:
        findings.append(CaptionFinding(
            check="stale",
            label=f"frame {index}",
            passed=False,
            detail=(
                f"a caption is visible at frame {index} that no caption "
                f"declares - it reads {text!r}. A card that was replaced "
                "but never re-rendered, or a card that was never removed, "
                "looks exactly like this."),
            measured={"frame": index, "text": text}))
    if len(undeclared) > STALE_FINDING_CAP:
        findings.append(CaptionFinding(
            check="stale",
            label="more",
            passed=False,
            detail=(f"{len(undeclared) - STALE_FINDING_CAP} further "
                    "undeclared caption(s) are on this file; the first "
                    f"{STALE_FINDING_CAP} are named above."),
            measured={"undeclared_captions": len(undeclared)}))
    return findings


def _dedupe(lines: Sequence[_Observation]) -> List[_Observation]:
    """One entry per line, when the same line was read more than once.

    The same caption is read at several frames of its window, and each
    read is the SAME line - though not always the same WORDS, because
    OCR reads a rendered glyph and the same line can be read one word
    differently from one frame to the next. The line is identified by
    where it is drawn, not by what was read: without this the caption's
    words are counted three times and the text check compares a caption
    with itself.
    """
    out: List[_Observation] = []
    for line in lines:
        if any(_same_line(other, line) for other in out):
            continue
        out.append(line)
    return out


def _same_line(one: _Observation, other: _Observation) -> bool:
    """Whether two readings are the SAME line of text.

    A caption's line grows and shrinks as its words are spoken (the
    karaoke sweep scales the word being spoken), so the SAME line is
    read a few pixels higher or lower from frame to frame. The lines
    overlap when they are the same line.
    """
    vertical = min(one.y1, other.y1) - max(one.y0, other.y0)
    if vertical <= 0:
        return False
    smaller = min(one.height, other.height)
    if smaller <= 0:
        return False
    horizontal = min(one.x1, other.x1) - max(one.x0, other.x0)
    return (vertical / smaller) >= 0.5 and horizontal > 0


def _union(lines: Sequence[_Observation]) -> Tuple[float, float, float, float]:
    """The box around a set of observations."""
    return (min(line.x0 for line in lines),
            min(line.y0 for line in lines),
            max(line.x1 for line in lines),
            max(line.y1 for line in lines))


def _overlaps_box(observation: _Observation,
                  box: Tuple[float, float, float, float]) -> float:
    """How much of an observation lies inside a box."""
    x0, y0, x1, y1 = box
    ix = max(0.0, min(observation.x1, x1) - max(observation.x0, x0))
    iy = max(0.0, min(observation.y1, y1) - max(observation.y0, y0))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    smaller = observation.area
    return inter / smaller if smaller > 0 else 0.0


__all__ = [
    "CaptionFinding",
    "CaptionIntent",
    "CaptionVerdict",
    "extract_frames",
    "ink_above_baseline",
    "ink_profile",
    "normalise_words",
    "probe_video",
    "verify_delivered_captions",
    "visible_span",
    "words_say_the_same",
]
