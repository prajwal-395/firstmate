"""Source-local clocks at frame and sample precision (DATA-005).

The canonical Project Model migrates every source onto millisecond ticks,
which is the right interchange unit but not the precision editors and
measurements need: a frame boundary or an audio sample rarely lands on an
exact millisecond. This module keeps the ms-tick core untouched and adds
the exact mapping beside it.

A :class:`SourceClock` records one source's native rates (video frame
rate as an exact rational, audio sample rate in Hz) against the ms-tick
clock the model already uses. Conversions use :class:`Fraction`
arithmetic throughout, so a frame span maps to the exact half-open tick
range that covers it, and a tick maps back to the single frame or sample
that contains it. Nothing is rounded through floats.

One honest limit is stated, not hidden: millisecond ticks cannot resolve
individual 48 kHz audio samples, so neighbouring samples share a tick
and the mapping below one millisecond is many-to-one. The spans are
exact; the grid is coarse. Sample numbers travel beside ticks in
provenance wherever sample precision matters.

No single global clock is assumed: each source digest carries its own
clock, and cross-source joins keep going through the measured
``time_transforms`` the Project Model already validates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

STANDARD_FRAME_RATES = (Fraction(24000, 1001), Fraction(30000, 1001),
                        Fraction(60000, 1001))
"""The three NTSC-family rates, spelled as the exact rationals they are.

No float spells ``24000/1001`` exactly, and a rate that arrives as a
float has already lost the fraction that makes it exact. These three
are the rates most footage carries, so they are recognised by name
before any float is turned into a fraction.
"""


def rational_frame_rate(value: Any) -> Fraction | None:
    """A measured frame rate as the exact rational it is.

    A measured rate arrives as a float (``23.976``), and a float is
    not a rate: it is the nearest double to one. The three NTSC-family
    rates are recognised first (no float spells them, and every camera
    that ships one means exactly that fraction), and any other measured
    rate becomes the shortest fraction that names it. ``None`` when the
    value is not a rate at all.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    for candidate in STANDARD_FRAME_RATES:
        if abs(number - float(candidate)) <= 0.001:
            return candidate
    try:
        return Fraction(str(number)).limit_denominator(100_000)
    except (ValueError, ZeroDivisionError):
        return None


def frame_grid_ticks(ticks_per_second: int, rate: Fraction) -> int | None:
    """The exact ticks one frame covers on a clock, or ``None``.

    A clock whose tick grid cannot hold a whole frame has no frame
    grid: the answer is ``None``, never a rounded tick count that
    pretends one frame is exactly that many ticks.
    """
    if ticks_per_second <= 0 or rate is None or rate <= 0:
        return None
    numerator = ticks_per_second * rate.denominator
    if numerator % rate.numerator:
        return None
    return numerator // rate.numerator


FRAME_EPSILON = Fraction(1, 10 ** 9)
"""How close to a frame edge a float second may sit and still be it.

A float carries the frame boundary a hair either side of it, and the
hair is the difference between a frame and the frame after it. One
billionth of a frame is far below any measurement and far above that
hair.
"""


def frame_index_ceil(seconds: float, rate: Fraction) -> int:
    """The first frame ON OR AFTER a time.

    The build's own reading of a source edge: the in-point must not
    fall before the edge the caption plan set, because a word that
    starts earlier than the plan says plays speech the plan excluded.
    """
    exact = Fraction(seconds) * rate
    whole = math.floor(exact)
    if exact - whole <= FRAME_EPSILON:
        return whole
    return whole + 1


def frame_count(seconds: float, rate: Fraction) -> int:
    """How many frames a span covers, as the build counts them.

    The planned duration in whole frames, rounded - the same count
    ``reel_build._resolve_source_frame_span`` gives Resolve.
    """
    exact = Fraction(seconds) * rate
    return round(exact)


@dataclass(frozen=True)
class SourceClock:
    """Native rates for one source, keyed by content digest.

    ``fps_num``/``fps_den`` is the video frame rate (24000/1001 for
    NTSC-style footage, 25/1 for PAL). ``sample_rate`` is audio samples
    per second, or None for a video-only source. ``ticks_per_second``
    is the Project Model clock rate, 1000 by migration convention.
    """

    digest: str
    fps_num: int
    fps_den: int
    sample_rate: int | None = None
    ticks_per_second: int = 1000

    def __post_init__(self) -> None:
        if not self.digest or not isinstance(self.digest, str):
            raise ValueError("source clock needs a content digest")
        if self.fps_num <= 0 or self.fps_den <= 0:
            raise ValueError("frame rate must be a positive rational")
        if self.sample_rate is not None and self.sample_rate <= 0:
            raise ValueError("sample rate must be positive when present")
        if self.ticks_per_second <= 0:
            raise ValueError("ticks per second must be positive")

    @property
    def fps(self) -> Fraction:
        """Exact frames per second."""
        return Fraction(self.fps_num, self.fps_den)

    def to_dict(self) -> dict:
        """JSON-safe record for sidecars and provenance stamping."""
        return {
            "digest": self.digest,
            "fps_num": self.fps_num,
            "fps_den": self.fps_den,
            "sample_rate": self.sample_rate,
            "ticks_per_second": self.ticks_per_second,
        }

    @staticmethod
    def from_dict(record: dict) -> "SourceClock":
        """Rebuild a clock from :meth:`to_dict`, refusing bad records."""
        try:
            return SourceClock(
                digest=record["digest"],
                fps_num=int(record["fps_num"]),
                fps_den=int(record["fps_den"]),
                sample_rate=(None if record.get("sample_rate") is None
                             else int(record["sample_rate"])),
                ticks_per_second=int(record.get("ticks_per_second", 1000)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid source clock record: {exc}") from exc


def _check_frame(frame: int) -> int:
    if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0:
        raise ValueError("frame numbers are non-negative integers")
    return frame


def _check_sample(sample: int) -> int:
    if not isinstance(sample, int) or isinstance(sample, bool) or sample < 0:
        raise ValueError("sample numbers are non-negative integers")
    return sample


def _check_tick(tick: int) -> int:
    if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
        raise ValueError("ticks are non-negative integers")
    return tick


def frame_span_ticks(clock: SourceClock, frame: int) -> tuple[int, int]:
    """Half-open ``[start, end)`` tick range exactly covering one frame.

    The span is the floor of the frame's start edge through the ceiling
    of its end edge, so every frame maps to at least one tick and
    consecutive frames tile without gaps or overlaps.
    """
    frame = _check_frame(frame)
    tps = Fraction(clock.ticks_per_second)
    start = (frame * tps) / clock.fps
    end = ((frame + 1) * tps) / clock.fps
    lo = int(start) if start.denominator == 1 else start.numerator // start.denominator
    hi = -(-end.numerator // end.denominator)
    if hi <= lo:
        hi = lo + 1
    return lo, hi


def tick_to_frame(clock: SourceClock, tick: int) -> int:
    """The single frame containing a tick (floor, never rounded)."""
    tick = _check_tick(tick)
    value = (Fraction(tick) * clock.fps) / clock.ticks_per_second
    return value.numerator // value.denominator


def sample_span_ticks(clock: SourceClock, sample: int) -> tuple[int, int]:
    """Half-open tick range exactly covering one audio sample."""
    if clock.sample_rate is None:
        raise ValueError("source clock carries no audio sample rate")
    sample = _check_sample(sample)
    tps = Fraction(clock.ticks_per_second)
    rate = Fraction(clock.sample_rate)
    start = (sample * tps) / rate
    end = ((sample + 1) * tps) / rate
    lo = start.numerator // start.denominator
    hi = -(-end.numerator // end.denominator)
    if hi <= lo:
        hi = lo + 1
    return lo, hi


def tick_to_sample(clock: SourceClock, tick: int) -> int:
    """The single audio sample containing a tick."""
    if clock.sample_rate is None:
        raise ValueError("source clock carries no audio sample rate")
    tick = _check_tick(tick)
    value = (Fraction(tick * clock.sample_rate)) / clock.ticks_per_second
    return value.numerator // value.denominator


def ticks_between_frames(clock: SourceClock, first: int, last: int) -> tuple[int, int]:
    """Half-open tick range covering frames ``first`` through ``last``.

    Both bounds are inclusive frame numbers; the tick range tiles exactly
    with neighbouring frame ranges.
    """
    first = _check_frame(first)
    last = _check_frame(last)
    if last < first:
        raise ValueError("last frame must not precede first frame")
    start, _ = frame_span_ticks(clock, first)
    _, end = frame_span_ticks(clock, last)
    return start, end


def from_model_clock(digest: str, model_clock: dict,
                     *, sample_rate: int | None = None) -> SourceClock:
    """Build a source clock from a Project Model clock row plus rates.

    A migrated clock row carries ``ticks_per_second`` and an optional
    ``frame_rate`` of ``{"numerator", "denominator"}``; the migration
    writes ``frame_rate`` as None today, in which case the caller must
    supply the measured ``fps`` or the build refuses rather than guess.
    """
    frame_rate = (model_clock or {}).get("frame_rate")
    fps = None
    if isinstance(frame_rate, dict):
        try:
            fps = (int(frame_rate["numerator"]), int(frame_rate["denominator"]))
        except (KeyError, TypeError, ValueError):
            fps = None
    if fps is None:
        raise ValueError(
            f"no measured frame rate for {digest}: supply fps explicitly")
    return SourceClock(digest=digest, fps_num=fps[0], fps_den=fps[1],
                       sample_rate=sample_rate,
                       ticks_per_second=int((model_clock or {}).get(
                           "ticks_per_second", 1000)))
