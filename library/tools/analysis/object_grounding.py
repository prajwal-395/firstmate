"""M0-1 / D-04 / E-07: the object lane answers a REQUEST, not only a plan.

`object_segmentation` (step 1.06) is plan-triggered: a clip is segmented
only when a subject grade or a behind-subject title names it. A query
that asks "where is the handheld device?" therefore finds no objects at
all - not because the objects are absent, but because the lane that
measures them answers PLANS, and no plan was written (M0 baseline, lane
6: "a request-driven grounding question finds no objects").

This module is the request-driven half of the object lane. A query names
an object; this runs the detector that the task requires over the
windows the query asks about; and it answers with the detections AND
the coverage map - which frames were inspected, at what rate, and which
parts of the scope nobody looked at.

**What it does not do.** It does not track: a track is the plan-triggered
1.06 path (SAM 2), and a request buys detections. It does not claim
absence from a sparse pass either: a sampled pass that finds nothing is
INCONCLUSIVE with its coverage map, never "the object is not there"
(C-14 - sparse visual observations cannot support exhaustive claims).

**Producer selection is by task requirement** (C-08). A question that
only needs to know whether the object is in the window is answered by
the cheaper named detector; a question that needs the box is answered by
the measured best one. The producers and their measured costs are in
:data:`PRODUCERS`; the requirement to producer mapping is in
:data:`REQUIREMENTS`.

**The lane.** The request-driven rows are written into their own
provenance lane (``object_detection``), not into 1.06's ``segmentation``
lane: a provenance record names ONE producer, and a row must never claim
a producer that did not write it.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OBJECT_LANE = "object_detection"
"""The provenance lane the request-driven detector writes into.

It is deliberately NOT 1.06's ``segmentation`` lane: that lane's
provenance record names the pipeline step that wrote it, and a request
is written by another producer."""

PRODUCER_VERSION = "object-request-v1"
"""The producer version the request-driven rows carry."""

DEFAULT_SAMPLE_FPS = 1.0
"""Frames inspected per second of source by default.

One frame per second is the cadence the M0 fixture's question is
answered at; it is a MEASURED choice, not a constant of nature: it sets
the resolution of a negative answer (a sampled pass cannot claim
absence) and the cost of a positive one."""

EXTRACTION_SECONDS_PER_SOURCE_SECOND = 0.32
"""What one pass of frame extraction costs per second of source.

MEASURED on the M0 fixture (1920x1080, 30 fps): 15.39 s to extract 48
frames spread over 48 s of source - one decode of the whole file plus
the frame encodes. The cost is in the SOURCE, not in the number of
frames asked for, because the frames are selected by their source
index: the decoder walks the file once."""

EXTRACTION_MEASUREMENT = (
    "measured on the M0 fixture (1920x1080, 30 fps): 15.39 s for 48 "
    "frames over 48 s of source, one decode of the whole file")

TIER_CHEAP = "cheap"
TIER_STANDARD = "standard"


@dataclass(frozen=True)
class Producer:
    """One measured detector, and what it costs to ask it."""

    id: str
    detector: str
    tier: str
    cost_seconds_per_frame: float
    fidelity: float | None
    vocabulary: str
    measured: bool
    note: str


PRODUCERS: dict[str, Producer] = {
    "rf_detr_nano": Producer(
        id="rf_detr_nano",
        detector="rf_detr_nano",
        tier=TIER_CHEAP,
        cost_seconds_per_frame=0.384,
        fidelity=33 / 116,
        vocabulary="fixed",
        measured=True,
        note="measured on this machine (M0 fixture, 1920x1080, 48 "
             "frames): 0.384 s/frame of DETECTION, plus one extraction "
             "pass over the source. Its recall is the D-01 corpus's: "
             "33/116 instances (0.284) at precision 0.327. It answers "
             "only the COCO classes it knows - a request for anything "
             "else is a question it cannot answer",
    ),
    "owlv2_named": Producer(
        id="owlv2_named",
        detector="owlv2",
        tier=TIER_STANDARD,
        cost_seconds_per_frame=7.269,
        fidelity=65 / 116,
        vocabulary="open",
        measured=True,
        note="measured on this machine (M0 fixture, 1920x1080, 48 "
             "frames): 7.269 s/frame of detection, plus one extraction "
             "pass over the source. Its recall is the D-01 corpus's: "
             "65/116 instances (0.560) at precision 0.175. It answers "
             "any phrase",
    ),
    "grounding_dino_named": Producer(
        id="grounding_dino_named",
        detector="grounding_dino",
        tier=TIER_STANDARD,
        cost_seconds_per_frame=13.113,
        fidelity=None,
        vocabulary="open",
        measured=False,
        note="measured on this machine (M0 fixture, 1920x1080, 48 "
             "frames): 13.113 s/frame of detection - SLOWER than OWLv2 "
             "here, which is why it is not selected. Its recall on the "
             "D-01 corpus is unmeasured, so it cannot answer a 'grounded' "
             "request either",
    ),
    "vision_nominations": Producer(
        id="vision_nominations",
        detector="vision_foreground",
        tier=TIER_CHEAP,
        cost_seconds_per_frame=0.163,
        fidelity=21 / 116,
        vocabulary="none",
        measured=True,
        note="measured on the MU-15 40-frame set: 0.163 s/frame. It names "
             "NO phrase, so it can only nominate regions for a named "
             "object - it can never answer a named-object question on "
             "its own",
    ),
}
"""Every producer the request path may run, with its measured cost.

The costs are measured on this machine on the M0 fixture (48 frames of
1920x1080 source); the recalls are the D-01 corpus's (24 frames, 116
labelled instances), so a reader can see what a request buys before it
buys it."""

REQUIREMENTS = ("presence", "grounded")
"""The task requirements a request may carry (C-08).

``presence`` asks whether the named object is in the window at all;
``grounded`` asks where it is. The requirement picks the producer: the
cheapest one that can answer the question, or the measured best one."""

REQUIREMENT_NOTES = {
    "presence": "the cheapest producer that can answer this question",
    "grounded": "the producer with the best measured recall that can "
                "answer this question",
}


class ObjectGroundingError(RuntimeError):
    """The request could not be measured, and says why."""


def can_answer(producer: Producer, phrase: str) -> bool:
    """Whether a producer can answer a question about this object.

    A fixed-vocabulary detector answers only the classes it knows: a
    request for "handheld device" is a question the COCO detector
    cannot answer, and asking it anyway would return an empty answer
    that reads as "the object is not there".
    """
    if producer.vocabulary == "open":
        return True
    if producer.vocabulary == "none":
        return False
    from library.tools.analysis import open_vocabulary_detect as ovd

    try:
        labels = ovd.fixed_vocabulary_labels(producer.detector)
    except ovd.DetectorNotAvailable:
        return False
    return any(ovd.matches_query(label, phrase) for label in labels)


def select_producer(requirement: str, phrases: Any = None) -> Producer:
    """The producer a task requirement selects, and why.

    An unknown requirement is refused rather than widened into the
    strongest producer: a request that does not say what it needs gets
    no silent upgrade (and no silent downgrade). A requirement with no
    producer that can answer the question is refused too - the answer
    "no producer can name this object" is the true one.
    """
    if requirement not in REQUIREMENTS:
        raise ObjectGroundingError(
            f"unknown task requirement {requirement!r}; one of "
            f"{sorted(REQUIREMENTS)}")
    names = phrases_of(phrases) if phrases else []
    candidates = [producer for producer in PRODUCERS.values()
                  if producer.vocabulary != "none"
                  and all(can_answer(producer, name) for name in names)]
    if not candidates:
        raise ObjectGroundingError(
            "no producer can answer a question about "
            f"{names or 'that object'}: the fixed-vocabulary detectors "
            "answer only the classes they know, and the open-vocabulary "
            "ones are the only ones that can name the rest")
    if requirement == "grounded":
        measured = [producer for producer in candidates
                    if producer.fidelity is not None]
        if not measured:
            raise ObjectGroundingError(
                "no producer that can answer this question has a measured "
                "recall, and an unmeasured one cannot answer a 'grounded' "
                "request")
        return max(measured, key=lambda producer: (
            producer.fidelity, -producer.cost_seconds_per_frame))
    return min(candidates, key=lambda producer: (
        producer.cost_seconds_per_frame, producer.id))


def phrases_of(query_phrases: Any) -> list[str]:
    """The object names a request asks about, cleaned.

    An empty list is refused: a detector asked about nothing answers
    nothing, and that empty answer would read as a recall of zero.
    """
    if isinstance(query_phrases, str):
        query_phrases = [query_phrases]
    cleaned = [str(phrase).strip() for phrase in (query_phrases or [])]
    cleaned = [phrase for phrase in cleaned if phrase]
    if not cleaned:
        raise ObjectGroundingError(
            "a request for an object names at least one object")
    return cleaned


# ── the source ──────────────────────────────────────────────────────────

def probe_source(source_file: str | Path) -> dict[str, Any]:
    """The source's video geometry: frame rate, frame count, size.

    Measured with ffprobe. A source that cannot be probed refuses: a
    coverage map over frames nobody counted is a map of nothing.
    """
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,nb_frames,width,height",
        "-of", "json", str(source_file),
    ]
    result = subprocess.run(command, capture_output=True, encoding="utf-8",
                            check=False)
    if result.returncode != 0:
        raise ObjectGroundingError(
            f"ffprobe could not read {source_file}: "
            f"{result.stderr.strip() or 'unknown error'}")
    try:
        streams = (json.loads(result.stdout) or {}).get("streams") or []
        stream = streams[0]
    except (ValueError, IndexError, TypeError) as exc:
        raise ObjectGroundingError(
            f"ffprobe answered no video stream for {source_file}") from exc
    rate = str(stream.get("r_frame_rate") or "0/1")
    try:
        numerator, _, denominator = rate.partition("/")
        fps = float(numerator) / float(denominator or 1)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ObjectGroundingError(
            f"{source_file} has an unreadable frame rate {rate!r}") from exc
    if fps <= 0:
        raise ObjectGroundingError(f"{source_file} has no usable frame rate")
    frames = stream.get("nb_frames")
    try:
        frame_count = int(frames)
    except (TypeError, ValueError):
        frame_count = 0
    return {
        "fps": fps,
        "frame_count": frame_count,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration_seconds": (frame_count / fps if frame_count > 0 else 0.0),
    }


def sample_frame_indices(*, fps: float, frame_count: int, start_s: float,
                         end_s: float, sample_fps: float) -> list[int]:
    """The source frame indices a window's sample inspects.

    The indices are SOURCE frame numbers, so a detection carries the
    frame it was measured on - the M2 sample's `index: null` defect
    (M0 baseline, stage 6) is not repeated here.
    """
    if fps <= 0:
        raise ObjectGroundingError("a source needs a positive frame rate")
    if sample_fps <= 0:
        raise ObjectGroundingError("the sample rate must be positive")
    if end_s <= start_s:
        raise ObjectGroundingError(
            "a window must end after it starts")
    step = max(1, round(fps / sample_fps))
    first = max(0, int(start_s * fps))
    # The window is half-open: the frame at exactly end_s is outside it.
    last = math.ceil(end_s * fps) - 1
    if frame_count > 0:
        last = min(last, frame_count - 1)
    indices = list(range(first, last + 1, step))
    if not indices:
        indices = [min(first, max(0, (frame_count or 1) - 1))]
    return indices


def extract_frames(source_file: str | Path, indices: list[int],
                   out_dir: str | Path) -> list[Path]:
    """Extract the named source frames in ONE decode pass.

    The frames are selected by their source index, so the file that
    comes out is the frame the index names - not the frame at the same
    ordinal in a re-encoded copy.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not indices:
        return []
    expression = "+".join(f"eq(n\\,{index})" for index in indices)
    pattern = str(out / "%06d.jpg")
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source_file),
        "-vf", f"select='{expression}'", "-fps_mode", "passthrough",
        "-q:v", "2", pattern,
    ]
    result = subprocess.run(command, capture_output=True, encoding="utf-8",
                            check=False)
    if result.returncode != 0:
        raise ObjectGroundingError(
            f"ffmpeg could not extract frames from {source_file}: "
            f"{result.stderr.strip() or 'unknown error'}")
    extracted = sorted(out.glob("*.jpg"))
    if len(extracted) != len(indices):
        raise ObjectGroundingError(
            f"ffmpeg returned {len(extracted)} frames for "
            f"{len(indices)} requested indices from {source_file}")
    return extracted


# ── the detection pass ──────────────────────────────────────────────────

def detect_frames(image_paths: list[Path], phrases: list[str], *,
                  producer: Producer,
                  score_threshold: float | None = None
                  ) -> tuple[list[dict[str, Any]], list[str]]:
    """Ask one producer about the named objects on each frame.

    Returns the detections and the reasons the producer gave for
    anything it could not answer. A detector that cannot run on this
    machine refuses with the reason (never an empty detection list):
    "cannot run" and "ran, found nothing" are different answers.
    """
    from library.tools.analysis import open_vocabulary_detect as ovd

    detections: list[dict[str, Any]] = []
    notes: list[str] = []
    for image_path in image_paths:
        try:
            found = ovd.detect_image(
                producer.detector, str(image_path), phrases,
                **({"score_threshold": score_threshold}
                   if score_threshold is not None else {}))
        except ovd.DetectorNotAvailable as exc:
            raise ObjectGroundingError(
                f"the {producer.detector} producer cannot run here: {exc}"
            ) from exc
        for detection in found:
            detections.append({
                "phrase": detection.phrase,
                "label": detection.phrase,
                "box": [round(float(value), 6) for value in detection.box],
                "score": round(float(detection.score), 6),
                "detector": detection.detector,
                "image": image_path.name,
            })
    return detections, notes


def coverage_intervals(indices: list[int], *, fps: float,
                       sample_fps: float) -> list[dict[str, float]]:
    """The source seconds each sampled frame stands for.

    A frame sampled at 1 Hz represents the second it was taken in, so
    the coverage of a pass is the union of those seconds - and the
    seconds between two samples are NOT covered by anything.
    """
    period = 1.0 / float(sample_fps)
    intervals: list[dict[str, float]] = []
    for index in indices:
        start = index / fps
        intervals.append({"start_s": round(start, 6),
                          "end_s": round(start + period, 6)})
    return intervals


def merge_intervals(intervals: list[dict[str, float]]
                    ) -> list[dict[str, float]]:
    """Touching or overlapping coverage spans, merged."""
    merged: list[list[float]] = []
    for row in sorted(intervals, key=lambda item: item["start_s"]):
        start, end = float(row["start_s"]), float(row["end_s"])
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{"start_s": round(a, 6), "end_s": round(b, 6)}
            for a, b in merged]


def unexamined(scope: dict[str, float],
               inspected: list[dict[str, float]]) -> list[dict[str, float]]:
    """The parts of the scope nobody looked at.

    An empty list means the scope was fully inspected; a non-empty one
    is the honest statement of what the answer does not cover.
    """
    start = float(scope.get("start_s", 0.0))
    end = float(scope.get("end_s", 0.0))
    gaps: list[dict[str, float]] = []
    cursor = start
    for row in merge_intervals(inspected):
        lo, hi = max(row["start_s"], start), min(row["end_s"], end)
        if hi <= lo:
            continue
        if lo > cursor:
            gaps.append({"start_s": round(cursor, 6),
                         "end_s": round(lo, 6)})
        cursor = max(cursor, hi)
    if cursor < end:
        gaps.append({"start_s": round(cursor, 6), "end_s": round(end, 6)})
    return gaps


def detect_window(source_file: str | Path, *, phrases: Any,
                  start_s: float, end_s: float,
                  producer: Producer,
                  sample_fps: float = DEFAULT_SAMPLE_FPS,
                  resolve_identity: bool = True,
                  workdir: str | Path | None = None) -> dict[str, Any]:
    """Measure one window for the named objects, and say what it covered.

    The answer carries the detections, the coverage map (inspected and
    unexamined seconds), the producer that answered and what it cost.

    With `resolve_identity` (the default) each detection also carries
    the IDENTITY of the object it saw: one id per object across the
    window, and the visibility state of that object in each inspected
    frame. A request that names an object then resolves to an object
    rather than to a pile of boxes - and the identity travels into the
    Project Model's rows, so the next query for the same object reads
    the identity instead of paying for the pass again.
    """
    names = phrases_of(phrases)
    geometry = probe_source(source_file)
    indices = sample_frame_indices(
        fps=geometry["fps"], frame_count=geometry["frame_count"],
        start_s=float(start_s), end_s=float(end_s),
        sample_fps=float(sample_fps))
    temp_dir = Path(workdir) if workdir else Path(tempfile.mkdtemp(
        prefix="ren-object-request-"))
    temp_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        images = extract_frames(source_file, indices, temp_dir)
        detections, notes = detect_frames(images, names, producer=producer)
    finally:
        if workdir is None:
            shutil.rmtree(temp_dir, ignore_errors=True)
    elapsed = time.perf_counter() - started
    for detection in detections:
        index = indices[int(detection["image"].split(".")[0]) - 1]
        detection["frame_index"] = index
        detection["time_seconds"] = round(index / geometry["fps"], 6)
        detection["frame_width"] = geometry["width"]
        detection["frame_height"] = geometry["height"]
    identities: list[dict[str, Any]] = []
    if resolve_identity:
        identities = resolve_window_identities(
            detections, images=images, indices=indices, geometry=geometry)
        by_detection = {
            (round(float(row["time_s"]), 3),
             tuple(round(float(value), 6) for value in row["box"])):
            (row["entity_id"], row.get("state"))
            for row in identities if row.get("box")}
        for detection in detections:
            key = (round(float(detection["time_seconds"]), 3),
                   tuple(round(float(value), 6)
                         for value in detection["box"]))
            found = by_detection.get(key)
            if found is not None:
                detection["entity_id"], detection["visibility"] = found
    inspected = coverage_intervals(indices, fps=geometry["fps"],
                                   sample_fps=float(sample_fps))
    scope = {"start_s": float(start_s), "end_s": float(end_s)}
    return {
        "detections": detections,
        "identities": identities,
        "coverage": {
            "scope": scope,
            "sample_rate_hz": float(sample_fps),
            "frames_inspected": len(indices),
            "frame_indices": indices,
            "inspected": merge_intervals(inspected),
            "unexamined": unexamined(scope, inspected),
            "source_fps": geometry["fps"],
            "source_frame_count": geometry["frame_count"],
        },
        "producer": {
            "id": producer.id,
            "detector": producer.detector,
            "tier": producer.tier,
            "measured_fidelity": producer.fidelity,
            "note": producer.note,
        },
        "producer_version": PRODUCER_VERSION,
        "cost_seconds": round(elapsed, 3),
        "notes": notes,
    }


# ── the evidence rows ───────────────────────────────────────────────────

PRODUCER = "library.tools.analysis.object_grounding"
"""The producer the request-driven rows name."""


# ── the identity of what was detected ─────────────────────────────────────

def resolve_window_identities(detections: list[dict[str, Any]], *,
                              images: list[Path], indices: list[int],
                              geometry: dict[str, Any]) -> list[dict[str, Any]]:
    """Give one window's detections the identity of the object they saw.

    The identity half of the object lane
    (:mod:`library.tools.analysis.object_identity`) does the work; this
    is the request path's call into it, and the shape it hands back:
    one row per detection, carrying the identity and the visibility
    state of that object in that frame.

    The appearance of an object is NOT measured here: a window is one
    source, so there is no second clip to match the appearance against,
    and the frames are about to be deleted.
    """
    from library.tools.analysis import object_identity

    by_image: dict[str, list[dict[str, Any]]] = {}
    for row in detections:
        by_image.setdefault(str(row.get("image") or ""), []).append(row)
    frames: list[object_identity.Frame] = []
    observed: list[object_identity.ObservedDetection] = []
    for index, image in zip(indices, images):
        frame_id = f"frame-{index:06d}"
        time_s = index / float(geometry["fps"])
        frames.append(object_identity.Frame(
            frame_id=frame_id, clip_id="request-window", time_s=time_s,
            image=str(image), width=int(geometry["width"]),
            height=int(geometry["height"]), scope="request-window"))
        for row in by_image.get(image.name, []):
            box = tuple(float(value) for value in (row.get("box") or []))
            if len(box) != 4:
                continue
            observed.append(object_identity.ObservedDetection(
                clip_id="request-window", frame_id=frame_id, time_s=time_s,
                phrase=str(row.get("phrase") or row.get("label") or ""),
                box=box, score=float(row.get("score") or 0.0),
                image=str(image)))
    resolution = object_identity.resolve(frames, observed,
                                          measure_appearance=False)
    return resolution.observations


def detection_value(detection: dict[str, Any], *, query: str) -> dict[str, Any]:
    """The evidence value one detection carries.

    The queried name travels with the answer (`query`) beside the
    detector's own answer (`phrase`), so a reader can see which
    question a row answers and what the detector said about it.
    """
    return {
        "kind": "detection",
        "query": query,
        "phrase": detection.get("phrase"),
        "label": detection.get("label"),
        "detector": detection.get("detector"),
        "score": detection.get("score"),
        "box": list(detection.get("box") or []),
        "frame_index": detection.get("frame_index"),
        "time_seconds": detection.get("time_seconds"),
        "frame_width": detection.get("frame_width"),
        "frame_height": detection.get("frame_height"),
        "entity_id": detection.get("entity_id"),
        "visibility": detection.get("visibility"),
    }


def coverage_value(phrase: str, result: dict[str, Any]) -> dict[str, Any]:
    """The evidence value one window's coverage carries.

    The window is filed as its own row so the coverage map survives
    into the model: a later query can say what was inspected even when
    the pass found nothing.
    """
    coverage = result.get("coverage") or {}
    return {
        "kind": "coverage",
        "query": phrase,
        "phrase": None,
        "label": None,
        "detector": (result.get("producer") or {}).get("detector"),
        "score": None,
        "box": [],
        "frame_index": None,
        "time_seconds": None,
        "frame_width": None,
        "frame_height": None,
        "producer": dict(result.get("producer") or {}),
        "looked": {
            "frames": int(coverage.get("frames_inspected") or 0),
            "sample_rate_hz": float(coverage.get("sample_rate_hz") or 0.0),
            "inspected": list(coverage.get("inspected") or []),
            "unexamined": list(coverage.get("unexamined") or []),
            "scope": dict(coverage.get("scope") or {}),
        },
    }


def _native(record: Any, *, method: str, sample_rate: float,
            samples: int, inspected: list[dict[str, float]],
            clock_id: str, resolution: list[int] | None) -> dict[str, Any]:
    """The evidence contract one row carries, with its coverage stated."""
    from library.tools import evidence_contract

    native = evidence_contract.to_rust_evidence(record)
    native.update({
        "source_stream_id": None,
        "sampling_method": method,
        "sample_resolution": resolution,
        "temporal_coverage": [
            {"clock_id": clock_id,
             "start": evidence_contract.point_to_tick(row["start_s"], 1000),
             "end": evidence_contract.point_to_tick(row["end_s"], 1000)}
            for row in inspected],
        "coverage_status": "sampled",
        "verified_status": "verified",
        "producer": PRODUCER,
        "producer_version": PRODUCER_VERSION,
        "freshness": "fresh",
        "coverage_sample_rate_hz": sample_rate,
        "coverage_samples": samples,
    })
    return native


def _slug(text: str) -> str:
    """A safe token for an id."""
    return "".join(char if char.isalnum() else "_"
                   for char in str(text).strip().lower())[:40] or "object"


def build_observations(*, model: dict[str, Any], digest: str, asset_id: str,
                       clock_id: str, phrases: list[str],
                       result: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """The Project Model rows one request produces.

    One row per detection, plus one row per window carrying the
    coverage that pass established. A pass that found nothing still
    produces rows: the coverage row is a measured absence with the
    coverage that justifies it, so a later query can say "looked here,
    found none" instead of "never looked".

    A detection is filed under the question it answers. The detector's
    own answer travels beside it, so a row that says "handheld device"
    was found where the detector called it "cell phone" is readable.
    """
    from library.tools import evidence_contract

    wanted = {phrase.strip().lower(): phrase for phrase in phrases
              if phrase and phrase.strip()}
    coverage = result.get("coverage") or {}
    intervals = list(coverage.get("inspected") or [])
    scope = dict(coverage.get("scope") or {})
    sample_rate = float(coverage.get("sample_rate_hz") or 0.0)
    frames_inspected = int(coverage.get("frames_inspected") or 0)
    detector = (result.get("producer") or {}).get("detector") or "detector"
    method = f"{detector}-named-object-detection"
    provenance_id = f"prov-{digest}-{OBJECT_LANE}"
    observations: list[dict[str, Any]] = []

    def _row(value: dict[str, Any], start: float, end: float, *,
             score: float | None, samples: int,
             resolution: list[int] | None,
             row_id: str) -> None:
        record = evidence_contract.new_inference(
            asset_id=asset_id, clock_id=clock_id,
            start_tick=evidence_contract.point_to_tick(start, 1000),
            end_tick=evidence_contract.point_to_tick(end, 1000),
            measurement_type="object.detection", value=value,
            uncertainty=evidence_contract.uncertainty_from_score(score),
            method=method, provenance_id=provenance_id,
            coverage=evidence_contract.CoverageKind.SAMPLED,
            coverage_samples=samples,
            coverage_sample_rate_hz=sample_rate)
        native = _native(record, method=method, sample_rate=sample_rate,
                         samples=samples, inspected=intervals,
                         clock_id=clock_id, resolution=resolution)
        payload = {**value, "evidence_contract": native}
        confidence_detail = None
        if score is not None:
            from library.tools.evidence_lifecycle import typed_confidence
            confidence_detail = typed_confidence(
                float(score), "raw_score", PRODUCER)
        observations.append({
            "id": row_id,
            "asset_id": asset_id,
            "interval": {
                "clock_id": clock_id,
                "start": evidence_contract.point_to_tick(start, 1000),
                "end": evidence_contract.point_to_tick(end, 1000)},
            "modality": "object_detection",
            "payload": payload,
            "confidence": score,
            "confidence_detail": confidence_detail,
            "provenance_id": provenance_id,
        })

    per_frame: dict[tuple[str, int], int] = {}
    for detection in result.get("detections") or []:
        label = str(detection.get("phrase") or "").strip().lower()
        owner = wanted.get(label)
        if owner is None and len(wanted) == 1:
            # One question, one answer: the detector's own word for the
            # object travels in the row beside the question it answers.
            owner = next(iter(wanted.values()))
        if owner is None:
            # A detection of something nobody asked about is not an
            # answer to this question; it is counted, not filed.
            continue
        value = detection_value(detection, query=owner)
        start = float(detection.get("time_seconds") or 0.0)
        end = start + (1.0 / sample_rate if sample_rate else 0.0)
        resolution = None
        if detection.get("frame_width") and detection.get("frame_height"):
            resolution = [int(detection["frame_width"]),
                          int(detection["frame_height"])]
        frame_index = int(detection.get("frame_index") or 0)
        key = (owner, frame_index)
        ordinal = per_frame.get(key, 0)
        per_frame[key] = ordinal + 1
        _row(value, start, end, score=detection.get("score"), samples=1,
             resolution=resolution,
             row_id=(f"obs-{digest}-objdet-{_slug(owner)}-"
                     f"{frame_index:06d}-{ordinal:02d}"))
    producer_id = (result.get("producer") or {}).get("id") or "producer"
    for phrase in phrases:
        if intervals or scope:
            start = float(scope.get("start_s", 0.0))
            end = float(scope.get("end_s", scope.get("start_s", 0.0)))
            _row(coverage_value(phrase, result), start, end, score=None,
                 samples=frames_inspected, resolution=None,
                 row_id=(f"obs-{digest}-objdet-{_slug(phrase)}-coverage-"
                         f"{_slug(producer_id)}-"
                         f"{round(start * 1000):010d}-"
                         f"{round(end * 1000):010d}"))
    provenance = [{
        "id": provenance_id,
        "producer": PRODUCER,
        "producer_version": PRODUCER_VERSION,
        "source_digest": digest,
        "recorded_at": None,
    }]
    return observations, provenance


# ── reading the rows back ───────────────────────────────────────────────

def value_of(observation: dict[str, Any]) -> dict[str, Any]:
    """The evidence value one observation carries."""
    native = (observation.get("payload") or {}).get("evidence_contract")
    if isinstance(native, dict) and isinstance(native.get("value"), dict):
        return native["value"]
    return {}


def is_coverage_row(observation: dict[str, Any]) -> bool:
    """Whether a row is a window's coverage record, not a detection."""
    return value_of(observation).get("kind") == "coverage"


def phrase_of(observation: dict[str, Any]) -> str:
    """The object name a row answers about."""
    value = value_of(observation)
    for key in ("query", "phrase", "label"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def detection_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The detections a query answered with.

    Each row carries the detector's own answer (phrase, box, score),
    the frame it was measured on, and the evidence id a reader can
    trace.
    """
    rows: list[dict[str, Any]] = []
    for match in matches:
        observation = match.get("obs") or {}
        value = value_of(observation)
        if not value or value.get("kind") == "coverage":
            continue
        interval = observation.get("interval") or {}
        rows.append({
            "evidence_id": observation.get("id"),
            "provenance_id": observation.get("provenance_id"),
            "query": value.get("query"),
            "phrase": value.get("phrase"),
            "detector": value.get("detector"),
            "score": value.get("score"),
            "box": value.get("box"),
            "frame_index": value.get("frame_index"),
            "time_seconds": value.get("time_seconds"),
            "entity_id": value.get("entity_id"),
            "visibility": value.get("visibility"),
            "interval": {
                "clock_id": interval.get("clock_id"),
                "start": interval.get("start"),
                "end": interval.get("end"),
                "start_s": (interval.get("start") / 1000
                            if isinstance(interval.get("start"), int) else None),
                "end_s": (interval.get("end") / 1000
                          if isinstance(interval.get("end"), int) else None),
            },
            "asset_id": observation.get("asset_id"),
        })
    return rows


def coverage_map_for(model: dict[str, Any], *, phrases: list[str],
                     scope: dict[str, Any] | None = None
                     ) -> dict[str, Any]:
    """The coverage map of a request, read back off the model's rows.

    It names what the pass inspected, at what rate, and what it never
    looked at. The unexamined part is computed against the QUERY's
    scope, so a request that covered less than it asked for says so.
    """
    wanted = {phrase.strip().lower() for phrase in phrases if phrase}
    inspected: list[dict[str, float]] = []
    rates: set[float] = set()
    windows: list[dict[str, Any]] = []
    producers: list[dict[str, Any]] = []
    for observation in model.get("observations") or []:
        if not isinstance(observation, dict) or not is_coverage_row(observation):
            continue
        value = value_of(observation)
        if phrase_of(observation).lower() not in wanted:
            continue
        looked = value.get("looked") or {}
        inspected.extend(looked.get("inspected") or [])
        if looked.get("sample_rate_hz"):
            rates.add(float(looked["sample_rate_hz"]))
        producer = value.get("producer") or {}
        if producer and producer not in producers:
            producers.append(dict(producer))
        windows.append({
            "scope": dict(looked.get("scope") or {}),
            "frames": int(looked.get("frames") or 0),
        })
    query_scope = {
        "start_s": float((scope or {}).get("start_s", 0.0)),
        "end_s": float((scope or {}).get("end_s", 0.0)),
    }
    merged = merge_intervals(inspected)
    gaps = unexamined(query_scope, merged) if query_scope["end_s"] > \
        query_scope["start_s"] else []
    rate = min(rates) if len(rates) == 1 else None
    covered_seconds = sum(row["end_s"] - row["start_s"] for row in merged)
    frames = round(covered_seconds * rate) if rate else 0
    return {
        "scope": query_scope,
        "frames_inspected": frames,
        "sample_rate_hz": rate,
        "inspected": merged,
        "unexamined": gaps,
        "windows": windows,
        "producers": producers,
    }


# ── the coverage map and the coverage table ─────────────────────────────

def category_of(phrase: str) -> str:
    """The D-01 category a queried phrase belongs to.

    The taxonomy is D-01's (`detection_coverage.CLASSES`); a phrase that
    names no category is its own category, reported as such rather than
    folded into a class it was never asked about.
    """
    from library.tools.analysis import detection_coverage

    lowered = str(phrase).strip().lower()
    for name in detection_coverage.CLASSES:
        if name == lowered or name in lowered.split():
            return name
    return str(phrase)


def coverage_table(rows: list[dict[str, Any]], *,
                   frames_inspected: int) -> dict[str, Any]:
    """The D-01 coverage table for one request, with its denominators.

    Every row states how many frames were inspected (the denominator),
    how many of them carried the object, and how many detections the
    detector answered. A category nobody asked about is UNMEASURED, not
    zero - the rule D-01's own table follows.

    The strata are MEASURED from the box geometry, never asserted: an
    instance is small when its box covers under 1% of the frame, and
    partial when it touches a frame edge.
    """
    from library.tools.analysis import detection_coverage

    by_phrase: dict[str, dict[str, Any]] = {}
    strata = {name: {"instances": 0, "frames_with_detection": 0}
              for name in ("small", "partial")}
    frames_seen: dict[str, set[Any]] = {}
    for row in rows:
        phrase = str(row.get("phrase") or row.get("query") or "")
        if not phrase:
            continue
        bucket = by_phrase.setdefault(phrase, {
            "category": category_of(phrase),
            "instances": 0, "detections": 0, "frames_with_detection": 0,
            "frames_inspected": frames_inspected})
        bucket["instances"] += 1
        bucket["detections"] += 1
        frame_index = row.get("frame_index")
        if frame_index is not None:
            # DISTINCT frames: two producers measuring the same frame
            # is one frame with the object in it, not two.
            frames_seen.setdefault(phrase, set()).add(frame_index)
        box = row.get("box") or []
        if len(box) == 4:
            flags = detection_coverage.instance_strata({"box": box})
            for name, hit in flags.items():
                if hit:
                    strata[name]["instances"] += 1
    for phrase, bucket in by_phrase.items():
        bucket["frames_with_detection"] = min(
            frames_inspected, len(frames_seen.get(phrase, ())))
    by_category: dict[str, dict[str, Any]] = {}
    category_frames: dict[str, set[Any]] = {}
    for phrase, bucket in by_phrase.items():
        category = bucket["category"]
        slot = by_category.setdefault(category, {
            "instances": 0, "detections": 0, "frames_with_detection": 0,
            "frames_inspected": frames_inspected, "phrases": []})
        slot["instances"] += bucket["instances"]
        slot["detections"] += bucket["detections"]
        category_frames.setdefault(category, set()).update(
            frames_seen.get(phrase, ()))
        slot["phrases"].append(phrase)
    for category, slot in by_category.items():
        slot["frames_with_detection"] = min(
            frames_inspected, len(category_frames.get(category, ())))
    for slot in by_category.values():
        inspected = slot["frames_inspected"]
        slot["coverage"] = (slot["frames_with_detection"] / inspected
                            if inspected else None)
        slot["measured"] = slot["instances"] > 0
    for name in detection_coverage.CLASSES:
        by_category.setdefault(name, {
            "instances": 0, "detections": 0, "frames_with_detection": 0,
            "frames_inspected": frames_inspected, "phrases": [],
            "coverage": None, "measured": False})
    for name, slot in strata.items():
        slot["frames_inspected"] = frames_inspected
        slot["coverage"] = (slot["frames_with_detection"] / frames_inspected
                            if frames_inspected else None)
        slot["measured"] = slot["instances"] > 0
    return {"by_phrase": by_phrase, "by_category": by_category,
            "strata": strata}
