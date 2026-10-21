"""The source-quality profile: one named aggregate over measured evidence.

A profile answers "how good is this footage, and what was measured to say
so" in ONE object, for a clip or a span of one. It exists because the
measurements were already there and had no name:

- VI-03 (``image_defects.json``) measured sharpness, temporal noise,
  compression blockiness and the two clipping fractions;
- the picture-quality lane (``picture_quality.json``) measured the
  per-sample focus, luma, clipping, residual-noise and frame-difference
  readings the picture sampler already takes;
- step 1.04's temporal index measured camera translation, subject energy
  and the face track that carries the subject's position and width;
- M11 (``audio_quality.json``) measured SNR, the speech-band energy
  fraction and the silence intervals.

Every one of those numbers was reachable, but a reader had to know which
lane produced it, at what resolution, over which interval, and what its
absence meant. This module is the aggregation: one enumeration of
dimensions, one shape, one place that knows which lane answers which
dimension.

**A profile is MEASUREMENT, never a verdict.** It says what was measured
and what was not. It does not say whether the footage is good enough to
cut, and it carries no threshold: what passes or fails is a versioned
evaluation policy (``library/tools/evaluation_policy.py``), and a
threshold written here would be taste dressed as a measurement. The
punch list's dimensions are focus, exposure, motion, framing, noise and
intelligibility; ``compression`` is the seventh because VI-03 already
measured blockiness and the search index already filters on it, so
dropping it would lose a measurement that exists.

**Coverage and confidence are stated, not implied.** Every dimension
carries how much of the requested span the samples behind it actually
covered - how many samples, at what rate, out of how many the span
needs - and a confidence that says whether an instrument or a model
produced the number. A dimension measured over half the span is not the
same reading as one measured over all of it, and a reader has to be
able to tell them apart.

**Absence is never a default.** A dimension whose lane never ran, or
whose samples do not cover the span, is ``absent`` with a machine-
readable reason. It is never ``0.0``, never ``0.5``, never omitted
without a reason. An absent focus reading and a measured focus reading
are different facts and a filter must be able to tell them apart.

**Measured and inferred stay apart.** Every measure here names its
registry measurement type, and the units and kinds come from
``library/tools/evidence_contract.py``'s registry, so a profile cannot
drift from the contract that governs evidence elsewhere. A reduction of
an instrument's output is ``measurement`` kind and ``measured``
certainty; a reduction of a MODEL's output - the framing dimension
reads the detected face - is ``inference`` kind and ``inferred``
certainty, because that is what the contract says it is. Nothing here is
a verdict: a defocus LIKELIHOOD or a "this shot is unusable" verdict
has no home here, the first because the contract types it as an
inference with uncertainty, the second because it is a policy result.

**Provenance travels with the number.** A profile carries the source
file, the content digest, the asset and clock it sits on, and every
measure carries the interval it was measured over - which for a
source-level statistic is the whole source and not the requested span.

The profile is DERIVED: it is a reduction over lanes that own their own
records, and rebuilding it changes nothing upstream.
"""

from __future__ import annotations

import math
import os
from typing import Any

from library.tools import camera_motion
from library.tools import evidence_contract
from library.tools import subject_framing

FORMAT = "source_quality/1"
"""The profile's own format tag, so a reader can refuse an unknown one."""

DIMENSIONS = (
    "focus",
    "exposure",
    "motion",
    "framing",
    "noise",
    "intelligibility",
    "compression",
)
"""Every dimension a profile carries, in one place.

The first six are the punch list's (focus, exposure, motion, framing,
noise, intelligibility). ``compression`` is the seventh: VI-03 measured
blockiness and the search index filters on it, so a profile that dropped
it would lose a measurement that already exists. A dimension not in here
does not exist, and asking for one raises.
"""

MEASURED = "measured"
ABSENT = "absent"

#: The lane that wrote each measurement, as the source-memory slot names
#: it. The 1.04 temporal index is a project-side step output, not a
#: source-memory slot, so it is named by its step.
LANE_VI03 = "vi-03"
LANE_PICTURE = "picture_quality"
LANE_TEMPORAL = "1_04_temporal_index"
LANE_M11 = "m11"

#: VI-03's own geometry: 180 px on the short side, 5 Hz. The method
#: string carries the SHORT side, because the same instrument at another
#: geometry is another method (the evidence contract's convention).
VI03_METHOD = "image_defects_v1@180px"

#: The face cascade reads frames at the CLIP'S OWN aspect ratio with a
#: 480 px short side (`capabilities/temporal/index.FACE_SAMPLE_SHORT_SIDE`),
#: so the method names that geometry.
FRAMING_METHOD = "haar-face-presence-v1@480px"

#: The picture-quality lane's own geometry: 180 px on the short side,
#: 5 Hz - the same frames VI-03 samples, so the two lanes' numbers are
#: comparable and only one of them needs to answer a dimension.
PICTURE_METHOD = "picture_quality_pixels_v1@180px"

#: The VI-03 signal blocks this profile reads, in the order they are
#: reduced. Every one of them is a registry measurement type.
_VI03_MEASUREMENTS = (
    "image.sharpness.laplacian_variance",
    "image.exposure.underexposed_fraction",
    "image.exposure.overexposed_fraction",
    "image.temporal_noise.residual",
    "image.compression.blockiness_proxy",
)


class SourceQualityError(ValueError):
    """A profile request that cannot be answered as asked."""


# ── The registry is the only source of units and kinds ─────────────


def _entry(measurement_type: str) -> dict[str, Any]:
    """The contract's registry row, so a profile cannot drift from it."""
    return evidence_contract.registry_entry(measurement_type)


def _measure(measurement_type: str, value: float, method: str, lane: str,
             *, start_seconds: float, end_seconds: float,
             samples: int | None = None, note: str | None = None,
             sample_rate_hz: float | None = None
             ) -> dict[str, Any]:
    """One measured value, typed by the evidence contract's registry."""
    entry = _entry(measurement_type)
    if entry["kind"] not in (evidence_contract.EvidenceType.MEASUREMENT,
                             evidence_contract.EvidenceType.INFERENCE):
        raise SourceQualityError(
            f"{measurement_type} is {entry['kind'].value}; a quality profile "
            "carries measurements and the inferences a model earned, never "
            "a verdict or taste")
    # A profile carries its own declared dimensions and nothing else: a
    # type that belongs to no dimension has no home here, whatever kind
    # the registry gives it.
    _dimension_of(measurement_type)
    if not math.isfinite(float(value)):
        raise SourceQualityError(
            f"{measurement_type} carries a non-finite value")
    measure = {
        "measurement_type": measurement_type,
        "value": round(float(value), 6),
        "unit": entry["unit"],
        "kind": entry["kind"].value,
        "certainty": (evidence_contract.CertaintyState.MEASURED.value
                      if entry["kind"] is evidence_contract.EvidenceType.MEASUREMENT
                      else evidence_contract.CertaintyState.INFERRED.value),
        "method": method,
        "lane": lane,
        "start_seconds": round(float(start_seconds), 6),
        "end_seconds": round(float(end_seconds), 6),
        "samples": samples,
    }
    if sample_rate_hz is not None:
        # The rate the samples were taken at. Coverage is computed from
        # it, so a reader can tell a full reading from a partial one.
        measure["sample_rate_hz"] = float(sample_rate_hz)
    if note:
        measure["note"] = note
    return measure


# ── The reductions ──────────────────────────────────────────────────


def _sample_rate(doc: dict) -> float | None:
    sampling = doc.get("sampling") or {}
    rate = sampling.get("rate_hz")
    if isinstance(rate, (int, float)) and rate > 0:
        return float(rate)
    return None


def _window(values: list, lo: int, hi: int) -> list[float]:
    out = []
    for value in values[lo:hi]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
    return out


def _vi03_method(doc: dict) -> str:
    """VI-03's method with the geometry it measured on.

    The lane records its own method string; the short side comes from
    the sampled frame size, because the same instrument at another
    geometry is another method (the evidence contract's convention).
    """
    method = doc.get("method") or "image_defects_v1"
    size = (doc.get("sampling") or {}).get("sample_size")
    if isinstance(size, list) and len(size) == 2 and all(
            isinstance(value, int) and value > 0 for value in size):
        return f"{method}@{min(size)}px"
    return method


def _vi03_measures(image_defects: dict | None, start: float, end: float
                    ) -> list[dict[str, Any]]:
    """VI-03's image signals reduced over the span.

    A signal the lane has not measured is skipped, never read as zero:
    records written before a signal existed simply lack that key.
    """
    if not isinstance(image_defects, dict):
        return []
    rate = _sample_rate(image_defects)
    if rate is None:
        return []
    method = _vi03_method(image_defects)
    signals = image_defects.get("signals") or {}
    lo = max(0, math.ceil(start * rate - 1e-9))
    hi = max(lo, math.ceil(end * rate - 1e-9))
    measures = []
    for measurement_type in _VI03_MEASUREMENTS:
        block = signals.get(_VI03_SIGNAL[measurement_type])
        values = block.get("values") if isinstance(block, dict) else block
        if not isinstance(values, list):
            continue
        measured = _window(values, lo, hi)
        if not measured:
            continue
        measures.append(_measure(
            measurement_type, sum(measured) / len(measured), method,
            LANE_VI03, start_seconds=start, end_seconds=end,
            samples=len(measured), sample_rate_hz=rate,
            note=_VI03_NOTE.get(measurement_type)))
    return measures


#: Which picture-quality sample each measurement type is read from.
#: The picture sampler already measures every one of them per sample,
#: so the lane adds no decode: it records what the sampler read.
_PICTURE_SIGNAL = {
    "image.sharpness.laplacian_variance": "focus_laplacian_variance",
    "image.exposure.luma_mean": "luma_mean",
    "image.exposure.underexposed_fraction": "underexposed_pixel_fraction",
    "image.exposure.overexposed_fraction": "overexposed_pixel_fraction",
    "image.temporal_noise.residual": "low_gradient_residual_sigma_luma",
    "image.motion.frame_difference_luma_fraction":
        "frame_difference_luma_fraction",
    "image.motion.blur_proxy": "motion_blur_proxy",
}

#: Every picture measurement type the picture-quality lane answers.
_PICTURE_MEASUREMENTS = tuple(_PICTURE_SIGNAL)

_PICTURE_NOTE = {
    "image.sharpness.laplacian_variance":
        "a sharpness proxy: it cannot separate defocus, motion blur and "
        "low texture, and it is never a focus verdict",
    "image.exposure.luma_mean":
        "the mean luma of the decoded greyscale frame: a brightness "
        "reading, not an exposure verdict",
    "image.exposure.underexposed_fraction":
        "a shadow-loss proxy over decoded pixels, not an exposure "
        "recommendation",
    "image.exposure.overexposed_fraction":
        "a highlight-loss proxy over decoded pixels, not an exposure "
        "recommendation",
    "image.temporal_noise.residual":
        "a low-gradient high-pass residual in decoded pixels, not a "
        "sensor-noise diagnosis",
    "image.motion.frame_difference_luma_fraction":
        "the mean absolute luma difference from the preceding sampled "
        "frame: how much the picture changed, never why",
    "image.motion.blur_proxy":
        "frame difference paired with how far the frame sits below its "
        "own clip's sharpness threshold: how soft a moving frame is, "
        "not the optical cause",
}


def _picture_measures(picture_quality: dict | None, start: float,
                      end: float) -> list[dict[str, Any]]:
    """The picture-quality lane's per-sample readings reduced over a span.

    The lane's record is one row per sample, each with the sample's own
    timestamp, so the reduction selects the rows the span covers and
    averages them. A row whose reading is absent (the first frame has
    no frame difference to compare with) is skipped, never read as zero.
    """
    if not isinstance(picture_quality, dict):
        return []
    rows = picture_quality.get("measurements")
    if not isinstance(rows, list):
        return []
    rate = picture_quality.get("sample_rate_hz")
    rate = float(rate) if isinstance(rate, (int, float)) and rate > 0 else None
    method = picture_quality.get("method") or "picture_quality_pixels_v1"
    size = picture_quality.get("sample_short_side_px")
    if isinstance(size, int) and size > 0:
        method = f"{method}@{size}px"
    selected = [row for row in rows
                if isinstance(row, dict)
                and isinstance(row.get("time_s"), (int, float))
                and start <= float(row["time_s"]) < end]
    if not selected:
        return []
    measures = []
    for measurement_type, key in _PICTURE_SIGNAL.items():
        values = [float(row[key]) for row in selected
                  if isinstance(row.get(key), (int, float))
                  and not isinstance(row.get(key), bool)]
        if not values:
            continue
        measures.append(_measure(
            measurement_type, sum(values) / len(values), method,
            LANE_PICTURE, start_seconds=start, end_seconds=end,
            samples=len(values), sample_rate_hz=rate,
            note=_PICTURE_NOTE.get(measurement_type)))
    return measures


#: Which VI-03 signal block each registry measurement type is read from.
_VI03_SIGNAL = {
    "image.sharpness.laplacian_variance":
        "sharpness_proxy_laplacian_variance",
    "image.exposure.underexposed_fraction":
        "clipping_underexposed_fraction",
    "image.exposure.overexposed_fraction":
        "clipping_overexposed_fraction",
    "image.temporal_noise.residual": "temporal_noise",
    "image.compression.blockiness_proxy": "compression_blockiness",
}

#: What each VI-03 number is NOT, so a profile is not read as a
#: diagnosis. Sharpness is a proxy: it cannot identify focus error,
#: motion blur or low texture, and nothing downstream may pretend it can.
_VI03_NOTE = {
    "image.sharpness.laplacian_variance":
        "a sharpness proxy: it cannot separate defocus, motion blur and "
        "low texture, and it is never a focus verdict",
    "image.exposure.underexposed_fraction":
        "a shadow-loss proxy over decoded pixels, not an exposure "
        "recommendation",
    "image.exposure.overexposed_fraction":
        "a highlight-loss proxy over decoded pixels, not an exposure "
        "recommendation",
    "image.temporal_noise.residual":
        "a global-luma-compensated temporal residual, not a sensor-noise "
        "diagnosis",
    "image.compression.blockiness_proxy":
        "an 8-pixel boundary contrast excess, not a codec identification",
}


def _flow_method(temporal_index: dict) -> str:
    """The flow instrument's own method string.

    The temporal index records which flow produced the decomposition
    ("farneback", "block_match", or a combination). A document written
    before the method was recorded reads "unrecorded", which is a
    different claim from "unmeasured".
    """
    flow = temporal_index.get("optical_flow_direction") or {}
    method = flow.get("method")
    return method if isinstance(method, str) and method else "unrecorded"


def _motion_measures(temporal_index: dict | None, start: float, end: float
                     ) -> list[dict[str, Any]]:
    """The camera/subject decomposition reduced over the span.

    The reduction is `library/tools/camera_motion.py`'s, which owns this
    measurement: it selects samples by their own timestamp, so a span
    no longer takes the sample that falls past its end, and it reads the
    subject energy as a magnitude.

    The decomposition is a separation hypothesis, not a labelled cause:
    the translation is what the camera model explains, and the subject
    energy is what it does not.
    """
    if not isinstance(temporal_index, dict):
        return []
    if not isinstance(temporal_index.get("camera_motion_decomposition"), dict):
        return []
    summary = camera_motion.summarize_camera_motion(temporal_index, start, end)
    motion = summary.get("camera_motion") or {}
    method = _flow_method(temporal_index)
    samples = summary.get("sample_count")
    measures = []
    translation = motion.get("translation")
    if isinstance(translation, (int, float)):
        measures.append(_measure(
            "image.motion.camera_translation", float(translation), method,
            LANE_TEMPORAL, start_seconds=start, end_seconds=end,
            samples=samples,
            note="mean global shift read as camera motion, in normalised "
                 "proxy-frame displacements; a separation hypothesis, not a "
                 "labelled cause"))
    energy = motion.get("subject_energy")
    if isinstance(energy, (int, float)):
        measures.append(_measure(
            "image.motion.subject_energy", float(energy), method,
            LANE_TEMPORAL, start_seconds=start, end_seconds=end,
            samples=samples,
            note="mean residual flow the camera model does not explain, "
                 "read as a magnitude"))
    return measures


def _framing_measures(temporal_index: dict | None, start: float, end: float
                       ) -> tuple[list[dict[str, Any]], str | None]:
    """The subject's position and width over the span.

    ``subject_framing`` owns the reading: it decides which samples count,
    how few are too few, and when the answer is unmeasurable. This
    module only names the two numbers it produced and says why they are
    missing when they are.
    """
    if not isinstance(temporal_index, dict):
        return [], "no_temporal_index"
    face_presence = temporal_index.get("face_presence")
    if not isinstance(face_presence, dict) or not face_presence:
        return [], "no_face_track"
    box = subject_framing.subject_box(face_presence, start, end)
    if box is None:
        reading = subject_framing.subject_center_reading(
            face_presence, start, end)
        reason = reading.reason if reading.status == "unmeasurable" \
            else "no_subject_width"
        return [], reason
    measures = [
        _measure("image.framing.subject_center_x", box.center_x,
                 FRAMING_METHOD, LANE_TEMPORAL, start_seconds=start,
                 end_seconds=end,
                 note="median horizontal centre of the detected face, as a "
                      "fraction of source width"),
        _measure("image.framing.subject_width_fraction", box.width,
                 FRAMING_METHOD, LANE_TEMPORAL, start_seconds=start,
                 end_seconds=end,
                 note="the subject's own face width as a fraction of source "
                      "width"),
    ]
    return measures, None


def _intelligibility_measures(audio_quality: dict | None, start: float,
                               end: float) -> tuple[list[dict[str, Any]],
                                                    str | None]:
    """What M11 measured about whether the speech can be heard.

    The per-interval speech-band energy fraction reduces over the span.
    The SNR is a whole-source statistic - its method says so - so it
    travels with the SOURCE's interval, not the requested span, and a
    reader is never told it describes the span.
    """
    if not isinstance(audio_quality, dict):
        return [], "no_audio_quality"
    channels = audio_quality.get("channels")
    if not isinstance(channels, list) or not channels:
        return [], "no_audio_quality"
    source_duration = audio_quality.get("duration_seconds")
    if not isinstance(source_duration, (int, float)):
        for channel in channels:
            if isinstance(channel, dict) and isinstance(
                    channel.get("duration_seconds"), (int, float)):
                source_duration = channel["duration_seconds"]
                break
    method = audio_quality.get("method")
    if not isinstance(method, str) or not method:
        method = "unrecorded"
    measures = []
    fractions: list[float] = []
    channel_samples: list[int] = []
    non_silent: list[float] = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        rows = channel.get("intervals")
        if isinstance(rows, list):
            per_channel: list[float] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_start = row.get("start_seconds")
                row_end = row.get("end_seconds")
                fraction = row.get("speech_band_energy_fraction")
                if not isinstance(row_start, (int, float)) or \
                        not isinstance(row_end, (int, float)) or \
                        not isinstance(fraction, (int, float)):
                    continue
                if row_end <= start or row_start >= end:
                    continue
                per_channel.append(float(fraction))
            if per_channel:
                # One mean per channel, then the mean of the channels:
                # the channels are alternatives, not more samples of one
                # timeline, so adding them up would overstate the
                # coverage of the span.
                fractions.append(sum(per_channel) / len(per_channel))
                channel_samples.append(len(per_channel))
        share = _non_silent_share(channel, start, end)
        if share is not None:
            non_silent.append(share)
        snr = channel.get("snr_db")
        if isinstance(snr, (int, float)) and not isinstance(snr, bool):
            span_end = float(source_duration) if isinstance(
                source_duration, (int, float)) else end
            measures.append(_measure(
                "audio.snr_db", float(snr),
                method, LANE_M11,
                start_seconds=0.0, end_seconds=round(span_end, 6),
                note="a whole-source temporal contrast estimate, not a "
                     "measure of this span"))
    if fractions:
        measures.append(_measure(
            "audio.intelligibility.speech_band_energy_fraction",
            sum(fractions) / len(fractions), method, LANE_M11,
            start_seconds=start, end_seconds=end,
            samples=max(channel_samples) if channel_samples else None,
            sample_rate_hz=1.0,
            note="the share of the 300-3400 Hz band that carries energy: a "
                 "frequency coverage proxy only, not intelligibility and "
                 "not word recognition"))
    if non_silent:
        measures.append(_measure(
            "audio.speech.non_silent_fraction",
            sum(non_silent) / len(non_silent), method, LANE_M11,
            start_seconds=start, end_seconds=end,
            note="the share of the span that is not silent: whether the "
                 "sound is there at all, never what was said"))
    if not measures:
        return [], "no_audio_quality"
    return measures, None


def _non_silent_share(channel: dict, start: float, end: float) -> float | None:
    """The share of the span the lane did not measure as silent.

    The lane's silence intervals are resolved at 100 ms and the silence
    threshold is its own; the share says whether there is sound in the
    span at all, and says nothing about what was said.
    """
    intervals = channel.get("silence_intervals")
    if not isinstance(intervals, list):
        return None
    span = max(0.0, float(end) - float(start))
    if span <= 0:
        return None
    silent = 0.0
    for interval in intervals:
        if not isinstance(interval, dict):
            continue
        interval_start = interval.get("start_seconds")
        interval_end = interval.get("end_seconds")
        if not isinstance(interval_start, (int, float)) or \
                not isinstance(interval_end, (int, float)):
            continue
        overlap = min(float(end), float(interval_end)) - max(
            float(start), float(interval_start))
        if overlap > 0:
            silent += overlap
    return round(max(0.0, min(1.0, 1.0 - silent / span)), 6)


# ── The profile ─────────────────────────────────────────────────────

#: The confidence levels a dimension can state. ``full`` means every
#: sample the span needs was measured; ``partial`` means some of them
#: were; ``single_sample`` means one sample stands for the span;
#: ``none`` means nothing was measured. The level describes COVERAGE;
#: the basis says whether an instrument or a model produced the number.
CONFIDENCE_FULL = "full"
CONFIDENCE_PARTIAL = "partial"
CONFIDENCE_SINGLE = "single_sample"
CONFIDENCE_NONE = "none"

BASIS_INSTRUMENT = "instrument_reading"
BASIS_MODEL = "model_reading"
BASIS_ABSENT = "absent"


def _absent_dimension(reason: str) -> dict[str, Any]:
    return {"status": ABSENT, "measures": [], "absent_reason": reason,
            "coverage": {"kind": "unavailable", "samples": 0,
                         "expected_samples": None, "covered_fraction": None,
                         "sample_rate_hz": None, "interval_s": None},
            "confidence": {"level": CONFIDENCE_NONE, "basis": BASIS_ABSENT,
                           "because": "no lane measured this dimension over "
                                      "the requested span"}}


def _coverage_of(measures: list[dict[str, Any]], span: tuple[float, float]
                 ) -> dict[str, Any]:
    """How much of the span the samples behind these measures covered.

    The coverage is DURATION: the samples stand for
    ``samples / rate`` seconds of the span, and the fraction is that
    share of the span. A span covered to within half a sampling period
    is complete - the half period is the gap the sample grid cannot
    see, not a gap in the measurement.
    """
    best: tuple[int, float] | None = None
    for measure in measures:
        samples = measure.get("samples")
        rate = measure.get("sample_rate_hz")
        if not isinstance(samples, int) or samples <= 0:
            continue
        if not isinstance(rate, (int, float)) or rate <= 0:
            continue
        if best is None or samples > best[0]:
            best = (samples, float(rate))
    if best is None:
        return {"kind": "unavailable", "samples": 0, "sample_rate_hz": None,
                "covered_seconds": 0.0, "covered_fraction": None,
                "interval_s": [span[0], span[1]]}
    samples, rate = best
    width = max(0.0, span[1] - span[0])
    covered_seconds = samples / rate
    fraction = None
    if width > 0:
        fraction = round(min(1.0, covered_seconds / width), 4)
    tolerance = 0.5 / rate
    kind = ("complete" if width <= 0 or (width - covered_seconds) <= tolerance
            else "sampled")
    return {"kind": kind, "samples": samples, "sample_rate_hz": rate,
            "covered_seconds": round(covered_seconds, 3),
            "covered_fraction": fraction, "interval_s": [span[0], span[1]]}


def _confidence_of(measures: list[dict[str, Any]],
                   coverage: dict[str, Any]) -> dict[str, Any]:
    """The confidence the measures earn, and what produced them."""
    kinds = {measure.get("certainty") for measure in measures}
    basis = BASIS_MODEL if "inferred" in kinds else BASIS_INSTRUMENT
    samples = coverage.get("samples") or 0
    fraction = coverage.get("covered_fraction")
    if samples <= 0:
        level = CONFIDENCE_NONE
        because = "no sample of the requested span was measured"
    elif samples == 1:
        level = CONFIDENCE_SINGLE
        because = "one sample stands for the requested span"
    elif coverage.get("kind") == "complete":
        level = CONFIDENCE_FULL
        because = (f"the samples cover the span ({samples} at "
                   f"{coverage.get('sample_rate_hz')} Hz)")
    else:
        level = CONFIDENCE_PARTIAL
        because = (f"the samples cover {coverage.get('covered_fraction')} of "
                   f"the span ({samples} at "
                   f"{coverage.get('sample_rate_hz')} Hz)")
    return {"level": level, "basis": basis, "because": because}


def _dimension(measures: list[dict[str, Any]],
               absent_reason: str | None,
               span: tuple[float, float] = (0.0, 0.0)) -> dict[str, Any]:
    if measures:
        coverage = _coverage_of(measures, span)
        return {"status": MEASURED, "measures": measures,
                "absent_reason": None, "coverage": coverage,
                "confidence": _confidence_of(measures, coverage)}
    return _absent_dimension(absent_reason or "not_measured")


def profile_over_span(*, image_defects: dict | None = None,
                      picture_quality: dict | None = None,
                      temporal_index: dict | None = None,
                      audio_quality: dict | None = None,
                      start_seconds: float = 0.0,
                      end_seconds: float | None = None,
                      asset_id: str | None = None,
                      content_digest: str | None = None,
                      source_file: str | None = None,
                      clock_id: str | None = None
                      ) -> dict[str, Any]:
    """The quality profile of one span, reduced from measured records.

    The inputs are the records the lanes own: VI-03's image defects, the
    picture-quality lane's per-sample track, step 1.04's temporal index
    and M11's audio quality. Nothing here re-measures anything and
    nothing here decodes a frame.

    A span that no lane covers produces a profile whose every dimension
    is ``absent`` with the reason each one is missing. That is the
    honest answer, and it is a different object from a profile of a span
    that was measured clean.
    """
    start = float(start_seconds)
    if end_seconds is None:
        end = start
    end = float(end_seconds)
    if end <= start:
        raise SourceQualityError(
            "a quality profile needs a non-empty half-open span")
    if start < 0:
        raise SourceQualityError("a quality profile cannot start before zero")
    span = (start, end)

    by_dimension: dict[str, list[dict[str, Any]]] = {
        name: [] for name in DIMENSIONS}
    reasons: dict[str, str] = {}

    # The picture-quality lane answers the picture dimensions the
    # pipeline measures on every run; VI-03 answers the rest and fills
    # a gap the picture lane did not measure. One measurement type is
    # filed once, by the lane that produced it.
    picture_measures = _picture_measures(picture_quality, start, end)
    answered = {measure["measurement_type"] for measure in picture_measures}
    for measure in picture_measures:
        by_dimension[_dimension_of(measure["measurement_type"])].append(measure)
    for measure in _vi03_measures(image_defects, start, end):
        if measure["measurement_type"] in answered:
            continue
        by_dimension[_dimension_of(measure["measurement_type"])].append(measure)
    if not by_dimension["focus"] and not by_dimension["exposure"] \
            and not by_dimension["noise"] and not by_dimension["compression"]:
        reasons.setdefault("focus", "no_picture_measurement_record")
        reasons.setdefault("exposure", "no_picture_measurement_record")
        reasons.setdefault("noise", "no_picture_measurement_record")
        reasons.setdefault("compression", "no_image_defects_record")

    motion = _motion_measures(temporal_index, start, end)
    by_dimension["motion"].extend(motion)
    if not motion:
        reasons.setdefault("motion", "no_camera_motion_decomposition")

    framing, framing_reason = _framing_measures(temporal_index, start, end)
    by_dimension["framing"].extend(framing)
    if framing_reason:
        reasons.setdefault("framing", framing_reason)

    intelligibility, intelligibility_reason = _intelligibility_measures(
        audio_quality, start, end)
    by_dimension["intelligibility"].extend(intelligibility)
    if intelligibility_reason:
        reasons.setdefault("intelligibility", intelligibility_reason)

    dimensions = {
        name: _dimension(by_dimension[name], reasons.get(name), span)
        for name in DIMENSIONS
    }
    measured = [name for name in DIMENSIONS
                if dimensions[name]["status"] == MEASURED]
    absent = [name for name in DIMENSIONS
              if dimensions[name]["status"] == ABSENT]
    return {
        "format": FORMAT,
        "asset_id": asset_id,
        "content_digest": content_digest,
        "source_file": source_file,
        "clock_id": clock_id,
        "start_seconds": round(start, 6),
        "end_seconds": round(end, 6),
        "dimensions": dimensions,
        "measured_dimensions": measured,
        "absent_dimensions": absent,
    }


#: Which dimension each registry measurement type belongs to.
_DIMENSION_OF = {
    "image.sharpness.laplacian_variance": "focus",
    "image.motion.blur_proxy": "focus",
    "image.exposure.luma_mean": "exposure",
    "image.exposure.underexposed_fraction": "exposure",
    "image.exposure.overexposed_fraction": "exposure",
    "image.temporal_noise.residual": "noise",
    "image.compression.blockiness_proxy": "compression",
    "image.motion.frame_difference_luma_fraction": "motion",
    "image.motion.camera_translation": "motion",
    "image.motion.subject_energy": "motion",
    "image.framing.subject_center_x": "framing",
    "image.framing.subject_width_fraction": "framing",
    "audio.snr_db": "intelligibility",
    "audio.intelligibility.speech_band_energy_fraction": "intelligibility",
    "audio.speech.non_silent_fraction": "intelligibility",
}


def coverage_of(measures: list[dict[str, Any]],
                span: tuple[float, float]) -> dict[str, Any]:
    """The public face of the coverage rule, so a reader cannot drift."""
    return _coverage_of(measures, span)


def confidence_of(measures: list[dict[str, Any]],
                  coverage: dict[str, Any]) -> dict[str, Any]:
    """The public face of the confidence rule."""
    return _confidence_of(measures, coverage)


def _dimension_of(measurement_type: str) -> str:
    try:
        return _DIMENSION_OF[measurement_type]
    except KeyError:
        raise SourceQualityError(
            f"{measurement_type} belongs to no quality dimension") from None


def dimension_of(measurement_type: str) -> str:
    """The dimension a measurement type belongs to.

    The public face of the enumeration, so a reader that wants to know
    which dimension answers a measurement does not reach into the
    private map.
    """
    return _dimension_of(measurement_type)


def facets_of(profile: dict[str, Any]) -> dict[str, float]:
    """The profile's measures as the flat facet map the search index reads.

    One number per facet, and a facet the profile could not measure is
    simply absent: the index's filters fail a bound closed when a
    measurement is missing, so an absent facet must not read as a
    default.
    """
    if not isinstance(profile, dict):
        return {}
    dimensions = profile.get("dimensions") or {}
    facets: dict[str, float] = {}
    for measurement_type, facet in _FACET_KEYS.items():
        measure = _find_measure(dimensions, measurement_type)
        if measure is not None:
            facets[facet] = measure["value"]
    return facets


#: Each registry measurement type and the facet key it publishes under.
#: The keys are the ones `FootageIndex.filter` already filters on.
_FACET_KEYS = {
    "image.sharpness.laplacian_variance": "sharpness_laplacian_variance",
    "image.exposure.underexposed_fraction": "underexposed_fraction",
    "image.exposure.overexposed_fraction": "overexposed_fraction",
    "image.temporal_noise.residual": "temporal_noise_residual",
    "image.compression.blockiness_proxy": "compression_blockiness_proxy",
    "image.motion.camera_translation": "camera_translation_magnitude",
    "image.motion.subject_energy": "subject_energy",
    "image.framing.subject_center_x": "subject_center_x",
    "image.framing.subject_width_fraction": "subject_width_fraction",
    "audio.snr_db": "snr_db",
    "audio.intelligibility.speech_band_energy_fraction":
        "speech_band_energy_fraction",
}


def _find_measure(dimensions: dict[str, Any],
                   measurement_type: str) -> dict[str, Any] | None:
    for dimension in (dimensions or {}).values():
        if not isinstance(dimension, dict):
            continue
        for measure in dimension.get("measures") or []:
            if isinstance(measure, dict) and \
                    measure.get("measurement_type") == measurement_type:
                return measure
    return None


# ── Reading it off a project ────────────────────────────────────────


def _load_json(path):
    import json
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def profile_for_clip(project_folder, clip: dict, *,
                     start_seconds: float | None = None,
                     end_seconds: float | None = None,
                     temporal_index: dict | None = None,
                     root=None) -> dict[str, Any]:
    """The quality profile of one clip, or of a span of it.

    Reads the lanes' own records: VI-03's image defects, the
    picture-quality lane's per-sample track and M11's audio quality out
    of the per-source memory, and step 1.04's temporal index out of the
    project's step output. A lane that never ran contributes an absent
    dimension with a reason, never a default.
    """
    from library.tools import source_memory
    from library.tools.analysis import footage_segments

    recorded = source_memory.load_recorded_fingerprints(project_folder)
    digest, _basis = source_memory.digest_for_clip(
        project_folder, clip, recorded)
    image_defects = (source_memory.read_image_defects(digest, root)
                     if digest else None)
    picture_quality = (source_memory.read_picture_quality(digest, root)
                       if digest else None)
    audio_quality = (source_memory.read_audio_quality(digest, root)
                     if digest else None)
    if temporal_index is None:
        temporal_index = footage_segments.load_temporal_index(
            project_folder).get(clip.get("clip_id")) or {}
    start = float(start_seconds if start_seconds is not None else 0.0)
    if end_seconds is None:
        end = float(clip.get("duration_seconds") or 0.0)
        if end <= start:
            end = start + 1.0
    else:
        # An explicit span is the caller's: it names the interval the
        # measurement is about (a cutaway window, a line), and it is
        # never widened to the clip's own length.
        end = float(end_seconds)
    source_file = clip.get("source_file") or clip.get("path") or None
    asset_id = clock_id = None
    if digest:
        from library.tools import footage_model_link as model_link
        asset_id = model_link.asset_id_for_digest(digest)
        clock_id = model_link.clock_id_for_digest(digest)
    return profile_over_span(
        image_defects=image_defects,
        picture_quality=picture_quality,
        temporal_index=temporal_index,
        audio_quality=audio_quality,
        start_seconds=start, end_seconds=end,
        asset_id=asset_id,
        content_digest=digest,
        source_file=os.path.abspath(source_file) if source_file else None,
        clock_id=clock_id)
