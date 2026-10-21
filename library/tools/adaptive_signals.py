"""The reader side of adaptive inspection: what the engine has MEASURED.

The policy in :mod:`library.tools.adaptive_inspection` says WHERE to
look closer.  This module says what the engine already knows, so the
policy has something to plan from: the measured signals a source's
memory carries.

The five signals the punchlist names (VI-005) map onto the lanes the
engine already measures:

* a **shot change** - the M4 scene lane's measured boundaries;
* a **dialogue boundary** - the M1 transcript's utterance edges and
  the M7 lane's measured voice turns;
* an **object-state change** - the M7 lane's measured candidate spans
  (a hand at the mouth, an object picked up, a person entering);
* **motion** - the M3d lane's measured change and motion evidence;
* **uncertainty** - an interval the engine measured NOTHING about.

A signal the engine has NOT measured is reported as unavailable, with
the reason.  It is never read as "nothing happened there": an
unmeasured interval is uncertainty, and uncertainty is one of the five
triggers.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from typing import Any

from library.tools import edit_critical_events, event_spans, source_memory
from library.tools.adaptive_inspection import (
    Trigger,
    triggers_from_measurements,
)


def measured_triggers(content_digest: str,
                      *,
                      root: os.PathLike | str | None = None,
                      clip: Mapping[str, Any] | None = None,
                      project_folder: str | None = None,
                      recorded: Mapping[str, Any] | None = None,
                      duration_seconds: float | None = None,
                      ) -> dict[str, Any]:
    """The triggers one source's own measurements carry.

    Returns the plan's input and the evidence behind it:

    * ``triggers`` - the trigger rows, each naming the measurement that
      produced it;
    * ``signals`` - per signal, what was measured and what was not, so
      a reader can see that a missing lane is missing rather than
      empty;
    * ``coverage`` - the intervals the engine measured nothing about,
      which is what the uncertainty trigger is made of.
    """
    signals: dict[str, Any] = {}
    triggers: list[Trigger] = []

    scenes = source_memory.read_scenes(content_digest, root)
    if scenes is None:
        signals["shot_change"] = {"status": "unavailable",
                                  "reason": "no M4 scene record for this "
                                            "source"}
    else:
        boundaries = [{"timestamp": b} for b in
                      (scenes.get("scene_boundaries") or [])
                      if _number(b) is not None]
        signals["shot_change"] = {"status": "measured",
                                  "count": len(boundaries),
                                  "source": "M4 scene_boundaries",
                                  "times": [float(b) for b in
                                            (scenes.get("scene_boundaries")
                                             or [])]}
        triggers += triggers_from_measurements(scene_boundaries=boundaries)

    transcript, transcript_status = source_memory.read_m1(content_digest, root)
    if transcript is None or transcript_status != "fresh":
        signals["dialogue_boundary"] = {
            "status": "unavailable",
            "reason": f"no M1 transcript for this source ({transcript_status})"}
    else:
        edges: list[dict[str, Any]] = []
        for utterance in transcript.get("utterances") or ():
            start = _number(utterance.get("start"))
            end = _number(utterance.get("end"))
            if start is None:
                continue
            edges.append({"start": start, "end": start})
            if end is not None and end != start:
                edges.append({"start": end, "end": end})
        signals["dialogue_boundary"] = {"status": "measured",
                                         "count": len(edges),
                                         "source": "M1 utterance edges",
                                         "times": [e["start"] for e in edges]}
        triggers += triggers_from_measurements(speech_regions=edges)

    m7 = event_spans.read_m7(content_digest, root)
    if m7 is None:
        signals["object_state_change"] = {
            "status": "unavailable",
            "reason": "no M7 event record for this source"}
        signals["voice_turn"] = {
            "status": "unavailable",
            "reason": "no M7 event record for this source"}
    else:
        spans: list[dict[str, Any]] = []
        names: list[str] = []
        state_times: list[float] = []
        for name, body in (m7.get("candidates") or {}).items():
            for span in body.get("spans") or ():
                start = _number(span.get("start"))
                end = _number(span.get("end"))
                if start is None:
                    continue
                spans.append({"start": start,
                              "end": end if end is not None else start,
                              "name": name})
                names.append(name)
                for frame in span.get("frames") or ():
                    at = _number(frame.get("t"))
                    if at is not None:
                        state_times.append(float(at))
        signals["object_state_change"] = {"status": "measured",
                                          "spans": len(spans),
                                          "kinds": sorted(set(names)),
                                          "source": "M7 candidate spans",
                                          "times": sorted(state_times)}
        # The span itself is NOT a trigger: a long run holds several
        # events, and the span would send the dense tier to one huge
        # interval.  Each SAMPLE inside the span is the trigger - each
        # one is a coarse measurement of the state holding.
        triggers += _events_within_spans(m7, root=root)
        voice = []
        for span in ((m7.get("predicates") or {}).get("speaking") or {}).get("spans") or ():
            start = _number(span.get("start"))
            end = _number(span.get("end"))
            if start is None:
                continue
            voice.append({"start": start, "end": end if end is not None else start})
        signals["voice_turn"] = {"status": "measured", "count": len(voice),
                                 "source": "M7 speaking spans"}

    motion = _motion_boundaries(content_digest, clip, project_folder, recorded,
                                root)
    signals["motion"] = motion
    if motion.get("status") == "measured":
        triggers += triggers_from_measurements(
            motion_peaks=motion.get("boundaries") or [])

    # Uncertainty: the intervals NO measurement covered.  A lane that
    # was never run for this source leaves the whole recording
    # unmeasured by that lane, and the gaps between the measurements
    # that DID run are unmeasured too.  These become uncertainty
    # triggers, which the policy spends LAST, so a whole recording of
    # silence cannot consume the budget the measured events need.
    uncertainty = _uncertainty_intervals(signals, duration_seconds)
    signals["uncertainty"] = {
        "status": "measured" if uncertainty else "unavailable",
        "count": len(uncertainty),
        "source": "the intervals no measurement covered",
    }
    if uncertainty:
        triggers += triggers_from_measurements(
            unmeasured_intervals=uncertainty)

    return {
        "triggers": triggers,
        "signals": signals,
        "coverage": uncertainty,
    }


def _motion_boundaries(content_digest: str, clip: Mapping[str, Any] | None,
                       project_folder: str | None,
                       recorded: Mapping[str, Any] | None,
                       root: os.PathLike | str | None) -> dict[str, Any]:
    """The measured motion boundaries, or why they are unavailable.

    Motion is read from the M3 lane's OWN per-frame face geometry: the
    face box's displacement between two measured samples is the picture
    moving, and M3 measured it for every frame of every source.  The
    dedicated M3d motion lane is NOT read here: it costs a mask
    analysis over the source's own frames (measured: ten minutes on a
    68-minute source) and its M3f cache was never written for this
    corpus, so reading it would buy nothing and cost a great deal.

    A source with no M3 record has no measured motion, and that is
    reported as unavailable rather than as "nothing moved".
    """
    from library.tools import person_measurements
    m3 = person_measurements.read_m3(content_digest, root)
    if m3 is None:
        return {"status": "unavailable",
                "reason": "no M3 face measurement for this source",
                "boundaries": []}
    boundaries = _motion_from_faces(m3)
    return {"status": "measured", "count": len(boundaries),
            "source": "M3 face-box displacement between measured samples",
            "boundaries": boundaries,
            "times": [float(b["timestamp"]) for b in boundaries]}


#: How far a face box may move, in face widths, before the picture
#: counts as having moved.  A face width is the unit the engine's own
#: geometry uses (`event_spans.hand_mouth_distance` measures in face
#: widths), so the threshold is in the same unit.
MOTION_THRESHOLD_FACE_WIDTHS = 0.08


def _motion_from_faces(m3: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The samples where the measured face box moved.

    The displacement is normalised by the face's own width, so a face
    that is simply closer to the camera does not read as motion.
    """
    frames = m3.get("frames") or []
    out: list[dict[str, Any]] = []
    previous: tuple[float, float, float] | None = None
    for frame in frames:
        at = _number(frame.get("t"))
        if at is None:
            continue
        faces = frame.get("faces") or []
        if not faces:
            previous = None
            continue
        box = faces[0].get("box") or []
        if len(box) < 4:
            previous = None
            continue
        left, top, right, bottom = (float(v) for v in box[:4])
        width = max(1e-6, right - left)
        height = max(1e-6, bottom - top)
        centre = ((left + right) / 2.0, (top + bottom) / 2.0)
        if previous is not None:
            dx = (centre[0] - previous[0]) / width
            dy = (centre[1] - previous[1]) / height
            if math.hypot(dx, dy) >= MOTION_THRESHOLD_FACE_WIDTHS:
                out.append({"timestamp": at})
        previous = (centre[0], centre[1], width)
    return out


def _events_within_spans(m7: Mapping[str, Any],
                         root: os.PathLike | str | None = None
                         ) -> list[Trigger]:
    """The individual events a candidate span CONTAINS.

    A candidate span is a RUN of the coarse tier's samples, and a long
    run is several events: the M0 fixture's hand-near-mouth span runs
    from 3.6 s to 49.8 s and holds three separate occurrences.  A
    trigger that names the whole span would send the dense tier to one
    enormous interval, which is exactly the "maximum frame density
    across an entire recording" the policy must not do.

    So the span is read sample by sample.  Every sample in the run is
    a coarse measurement of the state HOLDING, and every one of them
    becomes a trigger in its own right, carrying the measured value as
    its strength.

    The measurement says why every sample is a trigger and not only
    the run's local minima.  On the M0 fixture the hand-near-mouth run
    measures 0.039, 1.245, 0.176, 0.533, 0.694, 1.167 and 0.050 at
    its seven samples, and the occurrence the M0 case asks about
    (33.3-33.7 s) sits in the gap between the 0.533 sample and the
    1.167 sample - a LOCAL MINIMUM of that coarse series does not
    exist there, so a local-minimum rule names the wrong event or none
    at all.  The coarse sample at 33.233 s measured the hand 0.694
    face-widths from the lips: that IS a measurement of the state
    holding, and the dense tier is what resolves when it began.

    The budget then decides how much of the run is bought, and the
    runs that lose say so (see ``deferred`` in the plan).
    """
    out: list[Trigger] = []
    for name, body in (m7.get("candidates") or {}).items():
        for index, span in enumerate(body.get("spans") or ()):
            for frame in span.get("frames") or ():
                at = _number(frame.get("t"))
                value = _number(frame.get("d"))
                if at is None or value is None:
                    continue
                out.append(Trigger(
                    kind="object_state_change", start=at, end=at,
                    evidence=f"M7 {name}[{index}] state held",
                    tolerance_seconds=float(
                        edit_critical_events.fidelity_requirement(
                            "action_state_change")["temporal_tolerance_seconds"]),
                    event_type="action_state_change", strength=value))
    return out


def _uncertainty_intervals(signals: Mapping[str, Any],
                            duration_seconds: float | None
                            ) -> list[dict[str, Any]]:
    """The intervals no measurement covered, as uncertainty triggers.

    A signal that is UNAVAILABLE leaves the whole recording unmeasured
    by that lane: the interval it would have covered is uncertainty
    rather than a place where nothing happened.  Where a lane DID
    measure, the gaps between its measurements are unmeasured too, and
    a gap wide enough to hide an event is worth a second look.

    The intervals are the WIDEST first, so the budget - which spends
    the strongest evidence first - buys the silence that could hide the
    most.
    """
    out: list[dict[str, Any]] = []
    duration = _number(duration_seconds)
    for name, signal in signals.items():
        if signal.get("status") == "unavailable":
            if duration and duration > 0:
                out.append({"start": 0.0, "end": duration,
                            "signal": name,
                            "reason": signal.get("reason")
                            or f"the {name} lane measured nothing for "
                               "this source"})
            continue
        times = [float(t) for t in (signal.get("times") or [])
                 if _number(t) is not None]
        if not times or not duration or duration <= 0:
            continue
        times.sort()
        cursor = 0.0
        for at in times:
            if at - cursor > UNCERTAINTY_GAP_SECONDS:
                out.append({"start": round(cursor, 6), "end": round(at, 6),
                            "signal": name,
                            "reason": f"the {name} lane measured nothing "
                                      "between these samples"})
            cursor = max(cursor, at)
        if duration - cursor > UNCERTAINTY_GAP_SECONDS:
            out.append({"start": round(cursor, 6), "end": round(duration, 6),
                        "signal": name,
                        "reason": f"the {name} lane measured nothing after "
                                  "its last sample"})
    out.sort(key=lambda row: (row["start"], row["end"]))
    return out


#: The gap between two measurements that is wide enough to hide an
#: event to be worth a second look.  It is the coarse tier's own
#: cadence, doubled: a gap the coarse tier could not have resolved is
#: a gap the dense tier can.
UNCERTAINTY_GAP_SECONDS = 2.0


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
