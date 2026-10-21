"""frame_rate.py - the frame rate a step uses, and where it came from.

A frame rate is a MEASUREMENT or an ASSUMPTION, and the difference is
one frame of picture.  The container states its rate as a rational
(`24000/1001`), a float cannot hold it, and a rounded float
(`23.976`) cannot be turned back into one without a tolerance guess.
Seven places in the engine used to turn a missing rate into a silent
`30.0`: a PAL source cut on that assumption is 20 % out, and nothing
said so.

This module is the ONE place that resolves a rate:

    MEASURED      the container's own rational, kept whole.
    ASSUMED       nothing measurable reached the site, so the engine's
                  standing assumption (30 fps) is used AND NAMED.  The
                  name says which site assumed it, what it assumed and
                  why nothing else answered.

The assumption is a MECHANICAL default (AGENTS.md 10.5): a frame rate
is a technical value, not taste, so it stays - but a mechanical default
that is silent reads as a measurement, and a reader of the run cannot
tell a 25 fps source from a 30 fps one.

`resolve` never guesses a rate that was measured somewhere else and
simply not routed: it takes the candidates it is given, and the first
usable one wins.  The catalog keeps the rational (see
`library/capabilities/footage/catalog.py`), so a caller that has the
catalog entry can pass the rational and get the exact rate back.

[why](docs/RULE_EVIDENCE.md#the-clock-that-lost-its-rational)
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

# The rate the engine assumed before anybody named it.  It is a
# MECHANICAL default: safe, ordinary, and wrong often enough that a
# silent use of it is a defect.  Kept as a named constant so the
# assumption can be REPORTED, and so a second one cannot appear.
ASSUMED_FPS = 30.0

# The key the assumption record is written under, in the run record.
STATE_KEY = "frame_rate_assumptions"

# What an assumption says it is.  One spelling, here.
ASSUMPTION = "frame_rate"


class FrameRateRefused(ValueError):
    """A rate was needed and neither a measurement nor an assumption was allowed."""


def parse_rational(value: Any) -> tuple[int, int] | None:
    """`"24000/1001"`, `"30"` or `24000/1001` into `(24000, 1001)`.

    None when the value is no rate at all: absent, empty, zero over
    zero (what variable-rate phone footage reports), or unreadable.
    A rate is never rounded here - the rational is the measurement.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        # A float is a lossy carrier of a rational, so it is read back
        # at the denominator that reproduces it EXACTLY.
        fraction = Fraction(value).limit_denominator(1_000_000)
        return fraction.numerator, fraction.denominator
    if isinstance(value, dict):
        try:
            numerator = int(value["numerator"])
            denominator = int(value["denominator"])
        except (KeyError, TypeError, ValueError):
            return None
        if numerator <= 0 or denominator <= 0:
            return None
        return numerator, denominator
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            numerator, denominator = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
        if numerator <= 0 or denominator <= 0:
            return None
        return numerator, denominator
    text = str(value).strip()
    if not text:
        return None
    if "/" in text:
        numerator_text, _, denominator_text = text.partition("/")
        try:
            numerator = int(numerator_text)
            denominator = int(denominator_text)
        except ValueError:
            return None
        if numerator <= 0 or denominator <= 0:
            return None
        return numerator, denominator
    try:
        numerator = int(text)
    except ValueError:
        return None
    if numerator <= 0:
        return None
    return numerator, 1


def rational_dict(numerator: int, denominator: int) -> dict:
    """The Project Model's `Rational` shape."""
    return {"numerator": int(numerator), "denominator": int(denominator)}


def to_float(rational: Any) -> float | None:
    """The float a rational reads as, or None when there is no rational."""
    parsed = parse_rational(rational)
    if parsed is None:
        return None
    return parsed[0] / parsed[1]


@dataclass(frozen=True)
class FrameRate:
    """One resolved rate, and what resolved it.

    `measured` is True when the rate came from a measurement.  When it
    is False the rate is the engine's standing assumption and
    `assumption` says so, in words a reader of the run record can use.
    """

    fps: float
    numerator: int | None = None
    denominator: int | None = None
    measured: bool = True
    assumption: dict | None = None
    source: str = ""

    @property
    def rational(self) -> dict | None:
        """The rate as the Project Model's `Rational`, or None."""
        if self.numerator is None or self.denominator is None:
            return None
        return rational_dict(self.numerator, self.denominator)

    def as_dict(self) -> dict:
        """The record a run keeps: the rate and where it came from."""
        record = {
            "frame_rate": self.fps,
            "measured": self.measured,
            "source": self.source,
        }
        rational = self.rational
        if rational is not None:
            record["frame_rate_rational"] = rational
        if self.assumption is not None:
            record["assumption"] = dict(self.assumption)
        return record


def assumption(site: str, *, reason: str = "", value: float = ASSUMED_FPS,
               source: str = "") -> dict:
    """The NAMED assumption record.

    A site that had no measurable rate says so here: which site
    assumed, what it assumed, and why nothing else answered.  An
    assumption with no reason is a guess wearing a label.
    """
    return {
        "assumption": ASSUMPTION,
        "site": site,
        "assumed_frame_rate": float(value),
        "reason": reason or (
            f"no measurable frame rate reached {site}; the rate used "
            f"there is the engine's standing assumption, not a "
            f"measurement"),
        "source": source,
    }


def resolve(candidates: dict[str, Any] | None = None, *, site: str,
             reason: str = "") -> FrameRate:
    """The rate for a site, from the measurements it was given.

    `candidates` maps a source name to a measured rate: a rational
    dict, a `"num/den"` string, a float or an int.  The first usable
    candidate wins, and it is MEASURED - the name it came from is
    recorded beside it.

    With no usable candidate the engine's standing assumption is
    returned, and it carries a NAMED assumption record naming the site.
    The caller may refuse instead of using it; what it may not do is
    use it silently.
    """
    for name, value in (candidates or {}).items():
        parsed = parse_rational(value)
        if parsed is None:
            continue
        numerator, denominator = parsed
        return FrameRate(fps=numerator / denominator,
                         numerator=numerator, denominator=denominator,
                         measured=True, source=name)
    return FrameRate(fps=ASSUMED_FPS, numerator=None, denominator=None,
                     measured=False, source="",
                     assumption=assumption(site, reason=reason))


def measured_from_catalog(catalog: list | None) -> FrameRate | None:
    """The rate the CATALOG measured, or None when it measured none.

    The catalog keeps the container's rational, so the rate this
    returns is the container's own - not a float read back through a
    tolerance guess.
    """
    for entry in catalog or []:
        if not isinstance(entry, dict):
            continue
        rational = entry.get("frame_rate_rational")
        parsed = parse_rational(rational) if rational else None
        if parsed is None:
            continue
        numerator, denominator = parsed
        return FrameRate(fps=numerator / denominator,
                         numerator=numerator, denominator=denominator,
                         measured=True, source="catalog")
    return None


def project_assumption(catalog: list | None) -> dict | None:
    """The run's assumption, when the catalog measured no rate at all.

    A run whose footage carries no measurable frame rate still has to
    cut something, so the engine's standing assumption stands - but the
    run record NAMES it, so a reader knows the picture was cut on an
    assumed grid rather than a measured one.
    """
    if measured_from_catalog(catalog) is not None:
        return None
    return assumption(
        "run",
        reason=("no clip in the catalog carries a measurable frame rate; "
                "every site that needed one used the engine's standing "
                "assumption of 30 fps, which is an assumption and not a "
                "measurement"))


def merge(previous: list | None, current: list | None) -> list:
    """Fold this run's assumption records onto a previous run's.

    MERGED, never replaced, like the sibling ledgers: a narrow run must
    not erase the record of the run that did the work.  A record from
    an earlier run is marked, so a carried row is never read as fresh.
    """
    rows: list[dict] = []
    seen: set[tuple] = set()
    for row in current or []:
        if not isinstance(row, dict):
            continue
        rows.append(row)
        seen.add((row.get("site"), row.get("assumption")))
    for row in previous or []:
        if not isinstance(row, dict):
            continue
        key = (row.get("site"), row.get("assumption"))
        if key in seen:
            continue
        carried = dict(row)
        carried["from_a_previous_run"] = True
        rows.append(carried)
    return rows


def summary_lines(rows: list | None) -> list[str]:
    """What the run summary prints about the rate it used."""
    lines = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        site = row.get("site") or "run"
        value = row.get("assumed_frame_rate")
        reason = row.get("reason") or ""
        carried = " (from a previous run)" if row.get(
            "from_a_previous_run") else ""
        lines.append(f"  {site}: assumed {value} fps{carried}"
                     + (f" - {reason}" if reason else ""))
    return lines
