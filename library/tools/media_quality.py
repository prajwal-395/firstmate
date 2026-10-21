"""The technical leg of a candidate: measured media quality, apart from taste.

What this is for
----------------
The punch list's VI-009 asks Ren to attach objective media-quality
measurements to the intervals a candidate covers, to say how much of
each interval was actually measured, and to let the planner compare
two candidates of the same line - **without** letting the technically
sharpest shot become the automatic editorial choice.

The measurements already existed and had names
(`library/tools/source_quality.py` reduces them into one profile over a
span: focus, exposure, motion, framing, noise, intelligibility and
compression). What was missing was a surface the SELECTION could read,
and a vocabulary that keeps the technical reading apart from the
editorial one. This module is that surface.

**A leg is not a score.** There is no aggregate, no total and no ranking
of candidates here. An indicator is a number with a unit, a method and
an interval; comparing two candidates says which one is higher on which
indicator and by how much. Nothing here says which shot to use.

**A number without its coverage is a claim nobody checked.** Every
indicator carries the coverage of the samples that produced it - how
many samples covered the interval, at what rate, out of how many the
interval needed - and a confidence that says whether the reading is an
instrument's or a model's. An indicator nobody measured is ``absent``
with a reason, never a zero.

**The technical reading never overrides the editorial choice.**
:func:`compare_legs` states the technical difference in numbers and
leaves the editorial selection exactly where it was. It has no field
that can carry a recommendation, and :func:`render_comparison` says so
in words, because a reader who has only the numbers will otherwise read
the bigger number as the verdict.

The indicators
--------------
Seven, one enumeration, and each names what it is NOT:

``sharpness``
    The variance of the 4-neighbour Laplacian over the decoded greyscale
    frame. A sharpness PROXY: it cannot separate defocus, motion blur
    and low texture, and a high number is not a good shot.
``blur``
    The motion-associated softness proxy: frame-to-frame difference
    paired with how far the frame sits below its own clip's sharpness
    threshold. It measures how soft a moving frame is, not WHY.
``exposure``
    The mean luma of the decoded frame. A brightness reading, not an
    exposure verdict and not a correction.
``motion``
    How much the picture changed between frames, and - when the
    temporal index measured it - how much of that change the camera
    model explains. A magnitude, never a cause.
``clipping``
    The share of decoded pixels at the black and white rails. Shadow
    loss and highlight loss, not a recommendation to re-grade.
``noise``
    The residual left after global luminance change is compensated.
    A texture proxy in compressed footage, not a sensor-noise reading.
``usable_speech``
    The share of the 300-3400 Hz band carrying energy over the
    interval, and the source's SNR. A frequency-coverage proxy: never
    intelligibility, never word recognition.

Reading it
----------
    from library.tools import media_quality
    leg = media_quality.technical_leg_for_clip(project_folder, clip)
    print(media_quality.render_leg(leg))

    # A comparison of two candidates, with the numbers and the
    # separation:

        python3 -m library.tools.media_quality compare \\
            --project <project-folder> --clip-a <clip_id> --clip-b <clip_id>
            [--start S --end E] [--editorial-preference left|right]

    The clips are named by their catalog ids; the spans are in SOURCE
    time. ``--editorial-preference`` records the side an editorial choice
    already made, and the comparison leaves it exactly where it is.
"""

from __future__ import annotations

from typing import Any

from library.tools import source_quality

FORMAT = "media_quality_leg/1"
"""The leg's own format tag, so a reader can refuse an unknown one."""

COMPARISON_FORMAT = "media_quality_comparison/1"

#: The seven indicators, in one enumeration. A dimension that is not in
#: here does not exist as a technical leg; asking for one raises.
INDICATORS = (
    "sharpness",
    "blur",
    "exposure",
    "motion",
    "clipping",
    "noise",
    "usable_speech",
)

#: Which profile measurements each indicator is read from, most
#: specific first. The leg reads the profile's measures; it never
#: re-measures anything and never reads a lane's own record.
INDICATOR_MEASUREMENTS: dict[str, tuple[str, ...]] = {
    "sharpness": ("image.sharpness.laplacian_variance",),
    "blur": ("image.motion.blur_proxy",),
    "exposure": ("image.exposure.luma_mean",),
    "motion": ("image.motion.frame_difference_luma_fraction",
               "image.motion.camera_translation",
               "image.motion.subject_energy"),
    "clipping": ("image.exposure.underexposed_fraction",
                 "image.exposure.overexposed_fraction"),
    "noise": ("image.temporal_noise.residual",),
    "usable_speech": ("audio.intelligibility.speech_band_energy_fraction",
                      "audio.snr_db"),
}

#: What each indicator is NOT, so a number is not read as a verdict.
INDICATOR_NOTES: dict[str, str] = {
    "sharpness": "a sharpness proxy over decoded pixels: it cannot "
                 "separate defocus, motion blur and low texture, and the "
                 "higher number is not the better shot",
    "blur": "how soft a MOVING frame is, measured as frame difference "
            "paired with relative softness; it does not identify a cause",
    "exposure": "the mean luma of the decoded frame: a brightness "
                "reading, not an exposure verdict and not a correction",
    "motion": "how much the picture changed, and how much of it the "
              "camera model explains: a magnitude, never a cause",
    "clipping": "the share of decoded pixels at the rails: shadow loss "
                "and highlight loss, not a grade recommendation",
    "noise": "a residual in compressed footage, not a sensor-noise "
             "reading",
    "usable_speech": "the share of the speech band carrying energy: a "
                      "frequency-coverage proxy, never intelligibility "
                      "and never word recognition",
}

MEASURED = "measured"
ABSENT = "absent"

#: The confidence vocabulary belongs to the profile that computes it
#: (`library/tools/source_quality.py`), so the leg reads the same words
#: and cannot invent a second one.
CONFIDENCE_FULL = source_quality.CONFIDENCE_FULL
CONFIDENCE_PARTIAL = source_quality.CONFIDENCE_PARTIAL
CONFIDENCE_SINGLE = source_quality.CONFIDENCE_SINGLE
CONFIDENCE_NONE = source_quality.CONFIDENCE_NONE
BASIS_INSTRUMENT = source_quality.BASIS_INSTRUMENT
BASIS_MODEL = source_quality.BASIS_MODEL
BASIS_ABSENT = source_quality.BASIS_ABSENT

#: The sentence that keeps the technical reading apart from the
#: editorial choice. It is stated on every comparison, because a reader
#: holding only the numbers will otherwise read the bigger number as
#: the verdict.
EDITORIAL_SEPARATION = (
    "Technical quality is one leg of this choice. It is not a verdict on "
    "which shot to use: the editorial selection stands on its own "
    "grounds, and these numbers neither make nor unmake it."
)


class MediaQualityError(ValueError):
    """A technical-leg request that cannot be answered as asked."""


# ── Building the leg ────────────────────────────────────────────────


def _measure_index(profile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Every measure in the profile, keyed by its measurement type."""
    index: dict[str, list[dict[str, Any]]] = {}
    dimensions = (profile or {}).get("dimensions") or {}
    for dimension in dimensions.values():
        if not isinstance(dimension, dict):
            continue
        for measure in dimension.get("measures") or []:
            if not isinstance(measure, dict):
                continue
            measurement_type = measure.get("measurement_type")
            if isinstance(measurement_type, str):
                index.setdefault(measurement_type, []).append(measure)
    return index


def _one_per_type(measures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One measure per measurement type.

    A multi-channel source answers the same question once per channel;
    the leg carries the first of each type and says how many others the
    profile holds, so the cell does not repeat the same number four
    times.
    """
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for measure in measures:
        measurement_type = measure.get("measurement_type")
        if measurement_type in seen:
            continue
        seen.add(measurement_type)
        kept.append(measure)
    return kept


def _indicator_leg(name: str, measures: list[dict[str, Any]],
                   profile: dict[str, Any],
                   absent_reason: str = "not_measured") -> dict[str, Any]:
    """One indicator: its measures, its coverage and its confidence."""
    if not measures:
        return {
            "status": ABSENT,
            "measures": [],
            "absent_reason": absent_reason,
            "coverage": {"kind": "unavailable", "samples": 0,
                         "sample_rate_hz": None, "covered_seconds": 0.0,
                         "covered_fraction": None,
                         "interval_s": [profile.get("start_seconds"),
                                        profile.get("end_seconds")]},
            "confidence": {"level": CONFIDENCE_NONE, "basis": BASIS_ABSENT,
                           "because": "no lane measured this indicator over "
                                      "the interval"},
            "note": INDICATOR_NOTES.get(name, ""),
        }
    coverage = _coverage_of(measures, profile)
    confidence = _confidence_of(measures, coverage)
    kept = _one_per_type(measures)
    return {
        "status": MEASURED,
        "measures": kept,
        "extra_measures": len(measures) - len(kept),
        "absent_reason": None,
        "coverage": coverage,
        "confidence": confidence,
        "note": INDICATOR_NOTES.get(name, ""),
    }


def _coverage_of(measures: list[dict[str, Any]],
                 profile: dict[str, Any]) -> dict[str, Any]:
    """How much of the interval the samples behind these measures covered.

    The rule is `library/tools/source_quality.py`'s - the profile
    computes coverage for its own dimensions and the leg computes it for
    an indicator the same way, so the two cannot drift.
    """
    from library.tools import source_quality

    span = (profile.get("start_seconds") or 0.0,
            profile.get("end_seconds") or 0.0)
    return source_quality.coverage_of(measures, span)


def _confidence_of(measures: list[dict[str, Any]],
                   coverage: dict[str, Any]) -> dict[str, Any]:
    """The confidence the measures earn, and what produced them."""
    from library.tools import source_quality

    return source_quality.confidence_of(measures, coverage)


def _absent_reasons(profile: dict[str, Any]) -> dict[str, str]:
    """Why each dimension of the profile could not be measured.

    The profile already knows which lane never ran for each dimension;
    an indicator borrows the reason of the dimension that owns its
    measurements, so an unmeasured indicator says WHICH lane is
    missing rather than only that it is missing.
    """
    reasons: dict[str, str] = {}
    dimensions = (profile or {}).get("dimensions") or {}
    for name, dimension in dimensions.items():
        if not isinstance(dimension, dict):
            continue
        if dimension.get("status") == MEASURED:
            continue
        reason = dimension.get("absent_reason")
        if isinstance(reason, str) and reason:
            reasons[name] = reason
    return reasons


def _dimension_of_measurement(profile: dict[str, Any],
                              measurement_type: str) -> str | None:
    """The dimension that owns a measurement type.

    Read off the profile's own enumeration, so the leg and the profile
    cannot disagree about which dimension a measurement answers.
    """
    from library.tools import source_quality

    try:
        return source_quality.dimension_of(measurement_type)
    except source_quality.SourceQualityError:
        return None


def technical_leg(profile: dict[str, Any]) -> dict[str, Any]:
    """The technical leg of one interval, read off a quality profile.

    The profile is `library/tools/source_quality.py`'s; this reads it and
    changes nothing. An indicator with no measure is ``absent`` with a
    reason, never a zero, and the leg carries no total, no score and no
    threshold.
    """
    if not isinstance(profile, dict):
        raise MediaQualityError("a technical leg needs a quality profile")
    index = _measure_index(profile)
    reasons = _absent_reasons(profile)
    indicators = {}
    for name in INDICATORS:
        measures = []
        owners = []
        for measurement_type in INDICATOR_MEASUREMENTS[name]:
            measures.extend(index.get(measurement_type, []))
            owner = _dimension_of_measurement(profile, measurement_type)
            if owner:
                owners.append(owner)
        reason = "not_measured"
        for owner in owners:
            if owner in reasons:
                reason = reasons[owner]
                break
        indicators[name] = _indicator_leg(name, measures, profile, reason)
    measured = [name for name in INDICATORS
                if indicators[name]["status"] == MEASURED]
    absent = [name for name in INDICATORS
              if indicators[name]["status"] == ABSENT]
    return {
        "format": FORMAT,
        "interval_s": [profile.get("start_seconds"),
                       profile.get("end_seconds")],
        "asset_id": profile.get("asset_id"),
        "content_digest": profile.get("content_digest"),
        "source_file": profile.get("source_file"),
        "indicators": indicators,
        "measured_indicators": measured,
        "absent_indicators": absent,
        "editorial_separation": EDITORIAL_SEPARATION,
    }


def compact_leg(leg: dict[str, Any]) -> dict[str, Any]:
    """The leg without its prose: what a record carries beside a decision.

    The notes explain what each indicator is not; a state record has
    room for the numbers, the coverage and the confidence, and the
    vocabulary lives in the module and in the prompt block.
    """
    if not isinstance(leg, dict):
        return {}
    indicators = {}
    for name in INDICATORS:
        indicator = (leg.get("indicators") or {}).get(name) or {}
        indicators[name] = {
            "status": indicator.get("status", ABSENT),
            "values": [
                {"value": measure.get("value"), "unit": measure.get("unit"),
                 "measurement_type": measure.get("measurement_type"),
                 "method": measure.get("method"), "lane": measure.get("lane")}
                for measure in indicator.get("measures") or []
                if isinstance(measure, dict)
            ],
            "absent_reason": indicator.get("absent_reason"),
            "coverage": indicator.get("coverage"),
            "confidence": indicator.get("confidence"),
        }
    return {"format": leg.get("format"), "interval_s": leg.get("interval_s"),
            "indicators": indicators,
            "measured_indicators": leg.get("measured_indicators", []),
            "absent_indicators": leg.get("absent_indicators", []),
            "editorial_separation": EDITORIAL_SEPARATION}


def technical_leg_for_clip(project_folder, clip: dict, *,
                            start_seconds: float | None = None,
                            end_seconds: float | None = None,
                            temporal_index: dict | None = None,
                            root=None) -> dict[str, Any]:
    """The technical leg of one clip, or of a span of it."""
    from library.tools import source_quality

    profile = source_quality.profile_for_clip(
        project_folder, clip, start_seconds=start_seconds,
        end_seconds=end_seconds, temporal_index=temporal_index, root=root)
    return technical_leg(profile)


# ── Rendering it ────────────────────────────────────────────────────


def _format_number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value) == int(value) and abs(float(value)) < 1e6:
        return str(int(value))
    return f"{float(value):.4g}"


def render_indicator(indicator: dict[str, Any]) -> str:
    """One indicator as a short cell: the numbers, then the coverage."""
    if indicator.get("status") != MEASURED:
        return f"unmeasured({indicator.get('absent_reason') or 'not_measured'})"
    parts = []
    for measure in indicator.get("measures") or []:
        parts.append(f"{_format_number(measure.get('value'))}"
                     f" {measure.get('unit') or ''}".strip())
    coverage = indicator.get("coverage") or {}
    confidence = indicator.get("confidence") or {}
    coverage_text = (f"{coverage.get('samples')} samples @"
                     f"{coverage.get('sample_rate_hz')}Hz "
                     f"{coverage.get('covered_fraction')}")
    return (f"{'|'.join(parts)} [{coverage_text} "
            f"{confidence.get('level')}/{confidence.get('basis')}]")


#: The compact per-candidate cell: the numbers, then how sure they are.
#: A prompt cell is a single line, so the values carry their unit and
#: their coverage, and an unmeasured indicator says which lane is
#: missing.
#: Short unit labels for the prompt cell. The full unit name is in the
#: profile and in the comparison prose; a cell that must fit on one
#: line says the same thing in fewer characters.
UNIT_LABELS = {
    "variance": "lapvar",
    "0_255": "luma",
    "fraction_0_1": "",
    "db": "dB",
    "normalized_luma": "",
}


def _short(value: Any, unit: Any) -> str:
    text = _format_number(value)
    label = UNIT_LABELS.get(unit, unit or "")
    return f"{text} {label}".strip()


def render_leg_cell(leg: dict[str, Any]) -> str:
    """One candidate's technical leg as a single tab-free cell."""
    if not isinstance(leg, dict):
        return ""
    indicators = leg.get("indicators") or {}
    cells = []
    for name in INDICATORS:
        indicator = indicators.get(name) or {}
        if indicator.get("status") != MEASURED:
            cells.append(f"{name}=-")
            continue
        values = [_short(measure.get("value"), measure.get("unit"))
                  for measure in indicator.get("measures") or []]
        coverage = indicator.get("coverage") or {}
        confidence = indicator.get("confidence") or {}
        cells.append(f"{name}={'|'.join(values)}"
                     f"[{coverage.get('samples')}"
                     f" {confidence.get('level')}]")
    return " ".join(cells)


def render_leg(leg: dict[str, Any]) -> str:
    """The whole leg as one tab-free line, for a prompt cell."""
    if not isinstance(leg, dict):
        return ""
    indicators = leg.get("indicators") or {}
    cells = []
    for name in INDICATORS:
        indicator = indicators.get(name) or {}
        value = render_indicator(indicator)
        cells.append(f"{name}={value}")
    return "; ".join(cells)


def render_comparison(comparison: dict[str, Any]) -> str:
    """A comparison as prose, with the numbers and the separation."""
    if not isinstance(comparison, dict):
        return ""
    lines = []
    for difference in comparison.get("differences") or []:
        lines.append(
            f"{difference['indicator']}: {difference.get('label_left')} "
            f"{_format_number(difference.get('left'))}"
            f" {difference.get('unit') or ''}".strip()
            + f" vs {difference.get('label_right')} "
            f"{_format_number(difference.get('right'))}"
            f" {difference.get('unit') or ''}".strip())
    if not lines:
        lines.append("no indicator was measured on both sides")
    lines.append(comparison.get("editorial_separation") or EDITORIAL_SEPARATION)
    return "\n".join(lines)


# ── Comparing two candidates ────────────────────────────────────────


def compare_legs(left: dict[str, Any], right: dict[str, Any], *,
                 left_label: str = "left", right_label: str = "right",
                 editorial_preference: str | None = None) -> dict[str, Any]:
    """The technical difference between two candidates, in numbers.

    The comparison states which side is higher on which indicator and by
    how much, together with each side's coverage and confidence. It
    does NOT rank the candidates, and it does not carry a
    recommendation: there is no field here that can say which shot to
    use.

    ``editorial_preference`` names the side an editorial decision
    already chose (``left`` or ``right``). It is recorded and left
    exactly where it was; the technical reading never moves it, and the
    output says so.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise MediaQualityError("a comparison needs two technical legs")
    if editorial_preference not in (None, "left", "right"):
        raise MediaQualityError(
            "editorial_preference names a side: 'left' or 'right'")
    differences = []
    not_comparable = []
    for name in INDICATORS:
        left_indicator = (left.get("indicators") or {}).get(name) or {}
        right_indicator = (right.get("indicators") or {}).get(name) or {}
        left_measure = _first_measure(left_indicator)
        right_measure = _first_measure(right_indicator)
        if left_measure is None or right_measure is None:
            missing = []
            if left_measure is None:
                missing.append(left_label)
            if right_measure is None:
                missing.append(right_label)
            not_comparable.append({
                "indicator": name,
                "reason": "not_measured_on_" + "_and_".join(missing),
            })
            continue
        left_value = left_measure.get("value")
        right_value = right_measure.get("value")
        delta = None
        if isinstance(left_value, (int, float)) and \
                isinstance(right_value, (int, float)):
            delta = round(float(left_value) - float(right_value), 6)
        differences.append({
            "indicator": name,
            "measurement_type": left_measure.get("measurement_type"),
            "label_left": left_label,
            "label_right": right_label,
            "left": left_value,
            "right": right_value,
            "unit": left_measure.get("unit"),
            "delta": delta,
            "left_coverage": (left_indicator.get("coverage") or {}),
            "right_coverage": (right_indicator.get("coverage") or {}),
            "left_confidence": (left_indicator.get("confidence") or {}),
            "right_confidence": (right_indicator.get("confidence") or {}),
        })
    return {
        "format": COMPARISON_FORMAT,
        "left": {"label": left_label,
                 "interval_s": left.get("interval_s"),
                 "measured_indicators": left.get("measured_indicators", [])},
        "right": {"label": right_label,
                  "interval_s": right.get("interval_s"),
                  "measured_indicators": right.get("measured_indicators", [])},
        "differences": differences,
        "not_comparable": not_comparable,
        "editorial_preference": editorial_preference,
        "editorial_selection_unchanged": True,
        "editorial_separation": EDITORIAL_SEPARATION,
        "does_not_conclude": ("which candidate is the better EDITORIAL "
                              "choice"),
    }


def _first_measure(indicator: dict[str, Any]) -> dict[str, Any] | None:
    for measure in indicator.get("measures") or []:
        if isinstance(measure, dict):
            return measure
    return None


def _main(argv: list[str] | None = None) -> int:
    """The module's own command line: compare two candidates' legs."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["compare"])
    parser.add_argument("--project", required=True,
                        help="the project folder the clips belong to")
    parser.add_argument("--clip-a", dest="clip_a", required=True)
    parser.add_argument("--clip-b", dest="clip_b", required=True)
    parser.add_argument("--start", type=float, default=None,
                        help="source-time start of the span, in seconds")
    parser.add_argument("--end", type=float, default=None,
                        help="source-time end of the span, in seconds")
    parser.add_argument("--editorial-preference", default=None,
                        choices=["left", "right"],
                        help="the side an editorial choice already made")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from library.tools.analysis import footage_segments

    catalog = footage_segments.load_catalog(args.project)
    clips = {clip.get("clip_id"): clip for clip in catalog}
    missing = [clip_id for clip_id in (args.clip_a, args.clip_b)
               if clip_id not in clips]
    if missing:
        print(json.dumps({"error": f"clip(s) not in the catalog: {missing}"}))
        return 1
    temporal = footage_segments.load_temporal_index(args.project)
    legs = {}
    for label, clip_id in (("left", args.clip_a), ("right", args.clip_b)):
        legs[label] = technical_leg_for_clip(
            args.project, clips[clip_id],
            start_seconds=args.start, end_seconds=args.end,
            temporal_index=temporal.get(clip_id))
    comparison = compare_legs(
        legs["left"], legs["right"],
        left_label=args.clip_a, right_label=args.clip_b,
        editorial_preference=args.editorial_preference)
    if args.json:
        print(json.dumps(comparison, indent=2))
    else:
        print(render_comparison(comparison))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
