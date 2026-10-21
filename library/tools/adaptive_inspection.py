"""Adaptive temporal inspection: a coarse tier, and a dense tier where an event is.

A recording is inspected at two tiers, and the tiers answer different
questions.

**The coarse tier** is the shared M2 sample: an I-frame sample of the
whole source, measured once per source and reused by every reader.  It
is cheap, it covers the whole recording, and it is the tier that says
"this file was looked at".  Nothing here changes it.

**The dense tier** is a frame-cadence inspection of a SHORT interval,
and it exists only where a measured signal says the coarse tier cannot
answer the question.  The dense tier is what names the exact frame an
event happened on, and it is what :mod:`library.tools.frame_exact`
provides: a decode at the source's own frame cadence, every sample
carrying the source's own timestamp and its source frame index.

The policy this module owns is the SCHEDULE: which intervals get the
dense tier, and which are left coarse.  Four rules hold it:

* **A static interval is never re-inspected.**  The coarse tier already
  measured it; running the expensive tier over it again would spend
  inference on a place where nothing changed.  The plan says which
  intervals it skipped and why, so "we did not look again here" is a
  fact in the report rather than an absence.
* **The dense tier is bounded.**  Its coverage is a declared fraction
  of the recording, so a long file cannot turn a short event into a
  frame-cadence pass over everything.  When the budget is short the
  plan DEFERS triggers, in a declared order, and names what it
  deferred; it never silently drops one.
* **The dense tier looks BACK, and says how far.**  A state change
  the coarse tier detected is evidence that something HAPPENED, and
  the coarse tier samples at a finite cadence, so the moment it began
  lies somewhere in the interval the coarse samples bracket.  The
  plan REPORTS that bracket (``onset_bracket``) and how uncertain the
  onset is, so a reader sees the coarse tier's resolution rather than
  inferring it.
* **The dense window is TIGHT around the sample that saw the
  change.**  This is the measured correction, and the measurement is
  what says it: on the M0 fixture a different, earlier approach
  (31.9-32.0 s) measures 0.176 face-widths from the lips, which is
  CLOSER than the M0 event itself (0.13-0.43 over 33.3-33.7 s), so a
  window stretched back over the coarse gap names that earlier
  approach and misses the event by 8 frames.  The bracket is reported,
  not covered.
* **Every sample keeps its original media timestamp.**  A dense sample
  is the source's own frame, at the source's own ``pts``, with the
  source's own frame index.  Nothing here re-times a frame.

The two surfaces this serves are the retrieval lane (which frames the
index holds, and therefore what a search can find) and the event lane
(which frame an event happened on).  Both read this module's plan; the
plan is the one place that decides where the expensive tier goes.
"""

from __future__ import annotations

import math
import os
import resource
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from library.tools import edit_critical_events, frame_exact

POLICY_VERSION = "adaptive-inspection-v1"

#: The five measured signals that earn a dense inspection.  The names
#: are the punchlist's (VI-005): motion, shot changes, dialogue
#: boundaries, object-state changes, and uncertainty.
TRIGGER_KINDS = (
    "motion",
    "shot_change",
    "dialogue_boundary",
    "object_state_change",
    "uncertainty",
)

#: How the dense tier's budget is spent, worst trigger first.  The
#: budget goes to the most SPECIFIC evidence first: a measured event
#: says something WAS there, and an uncertainty trigger says only that
#: nothing was measured there.
#:
#: UNCERTAINTY is LAST, and that is deliberate.  An uncertainty
#: trigger is the least specific evidence the engine has, and a lane
#: that never ran for a source leaves the WHOLE recording unmeasured
#: by it.  Spending the budget on the specific evidence first, and on
#: the silence last, is what keeps a whole recording of unmeasured
#: lanes from consuming the budget the measured events need.
TRIGGER_PRIORITY = {
    "object_state_change": 0,
    "dialogue_boundary": 1,
    "shot_change": 2,
    "motion": 3,
    "uncertainty": 4,
}

#: The dense tier's coverage budget, as a fraction of the recording.
#: The dense tier is a MINORITY of the inspection by construction: a
#: quarter of the recording at frame cadence is the most the policy
#: will spend, and the caller may ask for less.
DEFAULT_DENSE_COVERAGE_FRACTION = 0.25

#: An absolute cap on the dense tier's coverage, in seconds.  The
#: fraction is recording-relative, so a long file could still buy a
#: great many dense frames; this cap is the absolute bound, and a
#: caller that cannot afford the frames sets it.
DEFAULT_MAX_DENSE_SECONDS = 60.0

#: How far the dense window reaches either side of its trigger, in
#: seconds, before the event's own tolerance is applied.  The window
#: is the event's span plus this margin, so the dense tier sees the
#: event AND its approach.
DEFAULT_TRIGGER_MARGIN_SECONDS = 0.5

#: The widest a dense window may be, whatever the event asks for.  A
#: trigger that spans a whole recording (a dialogue boundary measured
#: over a long utterance) must not become a frame-cadence pass over
#: the recording.
MAX_DENSE_WINDOW_SECONDS = 4.0

#: The edit-critical event type each trigger kind is measured against,
#: so the window is sized by the event's DECLARED tolerance rather
#: than by a taste.
TRIGGER_EVENT_TYPE = {
    "shot_change": "scene_transition",
    "dialogue_boundary": "speech_start_end",
    "object_state_change": "action_state_change",
}


class AdaptiveInspectionError(ValueError):
    """A refusal with a named reason.  Never a silent empty plan."""


@dataclass(frozen=True)
class Trigger:
    """One measured reason to look closer at an interval.

    ``kind`` is one of :data:`TRIGGER_KINDS`.  ``start``/``end`` are
    source seconds.  ``evidence`` names the measurement that produced
    the trigger, so a reader can tell what was measured from what was
    assumed.  ``tolerance_seconds`` is the event type's own declared
    tolerance where the trigger has a type in
    :mod:`library.tools.edit_critical_events`; ``None`` means the
    trigger has no declared tolerance and the default margin applies.

    ``strength`` is the MEASURED value the trigger carries, smaller
    being the stronger evidence (a hand-to-lips distance of 0.02 face
    widths is stronger evidence of an event than 0.30).  It orders the
    budget within one trigger kind, so the budget is spent on the
    clearest events first.  ``None`` means the measurement carried no
    comparable value.
    """

    kind: str
    start: float
    end: float
    evidence: str
    tolerance_seconds: float | None = None
    event_type: str | None = None
    strength: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in TRIGGER_KINDS:
            raise AdaptiveInspectionError(
                f"unknown trigger kind {self.kind!r}; expected one of "
                f"{', '.join(TRIGGER_KINDS)}")
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise AdaptiveInspectionError(
                f"trigger {self.kind!r} has a non-finite span")
        if self.end < self.start:
            raise AdaptiveInspectionError(
                f"trigger {self.kind!r} ends ({self.end}) before it starts "
                f"({self.start})")
        if self.event_type:
            try:
                edit_critical_events.fidelity_requirement(self.event_type)
            except KeyError:
                raise AdaptiveInspectionError(
                    f"trigger names event type {self.event_type!r}, which is "
                    f"not in the edit-critical vocabulary") from None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "start": round(self.start, 6),
                "end": round(self.end, 6),
                "evidence": self.evidence,
                "tolerance_seconds": self.tolerance_seconds,
                "event_type": self.event_type,
                "strength": self.strength}

    def sort_key(self) -> tuple[int, float, float, str]:
        """The order the budget spends in: worst trigger first.

        A trigger's priority comes first, then the stronger evidence
        (the SMALLER measured value), then the tighter tolerance, then
        the earlier trigger, so the order is total and stable.
        """
        tolerance = (self.tolerance_seconds
                     if self.tolerance_seconds is not None else 1.0)
        strength = self.strength if self.strength is not None else 0.0
        return (TRIGGER_PRIORITY.get(self.kind, len(TRIGGER_PRIORITY)),
                strength, tolerance, f"{self.start:.6f}")


# ── Triggers from what the engine measured ────────────────────────────


def triggers_from_measurements(
        *,
        scene_boundaries: Sequence[Mapping[str, Any]] = (),
        speech_regions: Sequence[Mapping[str, Any]] = (),
        state_changes: Sequence[Mapping[str, Any]] = (),
        motion_peaks: Sequence[Mapping[str, Any]] = (),
        unmeasured_intervals: Sequence[Mapping[str, Any]] = (),
) -> list[Trigger]:
    """The triggers the engine's own measurements carry.

    Each argument is a sequence of rows carrying a time (``timestamp``
    or ``t``) or a span (``start``/``end``).  A row that carries
    neither is REFUSED by name rather than dropped silently: a
    measurement nobody can place is not a measurement.

    The five kinds map onto the five measured signals:

    * ``scene_boundaries`` -> a shot change;
    * ``speech_regions`` -> a dialogue boundary (both edges);
    * ``state_changes`` -> an object-state change;
    * ``motion_peaks`` -> motion;
    * ``unmeasured_intervals`` -> uncertainty: an interval the engine
      measured NOTHING about, which is exactly where a second look
      earns its cost.
    """
    triggers: list[Trigger] = []
    triggers += _triggers_from_rows(
        scene_boundaries, "shot_change", "scene_boundary")
    triggers += _triggers_from_rows(
        speech_regions, "dialogue_boundary", "speech_region",
        both_edges=True)
    triggers += _triggers_from_rows(
        state_changes, "object_state_change", "state_change")
    triggers += _triggers_from_rows(
        motion_peaks, "motion", "motion_peak")
    triggers += _triggers_from_rows(
        unmeasured_intervals, "uncertainty", "unmeasured_interval")
    return triggers


def _triggers_from_rows(rows: Sequence[Mapping[str, Any]], kind: str,
                         evidence: str, both_edges: bool = False
                         ) -> list[Trigger]:
    out: list[Trigger] = []
    for index, row in enumerate(rows or ()):
        if not isinstance(row, Mapping):
            raise AdaptiveInspectionError(
                f"{evidence} row {index} is not a mapping")
        at = _number(row.get("timestamp", row.get("t")))
        start = _number(row.get("start"))
        end = _number(row.get("end"))
        event_type = TRIGGER_EVENT_TYPE.get(kind)
        tolerance = None
        if event_type:
            tolerance = float(edit_critical_events.fidelity_requirement(
                event_type)["temporal_tolerance_seconds"])
        if start is not None and end is not None and end >= start:
            out.append(Trigger(kind=kind, start=start, end=end,
                               evidence=f"{evidence}[{index}]",
                               tolerance_seconds=tolerance,
                               event_type=event_type))
            continue
        if at is None:
            raise AdaptiveInspectionError(
                f"{evidence} row {index} carries no timestamp and no span")
        out.append(Trigger(kind=kind, start=at, end=at,
                           evidence=f"{evidence}[{index}]",
                           tolerance_seconds=tolerance,
                           event_type=event_type))
        if both_edges:
            out.append(Trigger(kind=kind, start=at, end=at,
                               evidence=f"{evidence}[{index}].end",
                               tolerance_seconds=tolerance,
                               event_type=event_type))
    return out


# ── The plan ──────────────────────────────────────────────────────────


@dataclass
class DenseWindow:
    """One interval the dense tier will inspect, and why."""

    start: float
    end: float
    triggers: list[Trigger] = field(default_factory=list)
    onset_bracket: dict[str, Any] | None = None
    """The coarse bracket a state change's ONSET may lie in, and
    whether the window covered all of it."""

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        row = {"start": round(self.start, 6), "end": round(self.end, 6),
               "duration_seconds": round(self.duration_seconds, 6),
               "triggers": [t.to_dict() for t in self.triggers],
               "evidence": sorted({t.evidence for t in self.triggers})}
        if self.onset_bracket is not None:
            row["onset_bracket"] = self.onset_bracket
        return row


def plan_inspection(
        duration_seconds: float,
        fps: float,
        coarse_times: Sequence[float],
        triggers: Sequence[Trigger],
        *,
        dense_coverage_fraction: float = DEFAULT_DENSE_COVERAGE_FRACTION,
        trigger_margin_seconds: float = DEFAULT_TRIGGER_MARGIN_SECONDS,
        max_window_seconds: float = MAX_DENSE_WINDOW_SECONDS,
        max_dense_seconds: float = DEFAULT_MAX_DENSE_SECONDS,
) -> dict[str, Any]:
    """Where the dense tier goes, and where it does not.

    ``coarse_times`` are the coarse tier's own sample times: the plan
    uses them to find the bracket each state change sits in, because
    the coarse tier's cadence is what makes the ONSET uncertain.  The
    coarse tier is RETAINED unchanged; the plan only says what is
    added.

    The budget is the dense tier's COVERAGE, and it is
    ``dense_coverage_fraction`` of the recording.  A recording whose
    coarse tier is one sample a second gets a dense tier that covers
    at most a quarter of the recording; one whose coarse tier is a
    sample a minute gets the same quarter.  Either way the dense tier
    is a minority of the inspection, so a short event cannot turn into
    a frame-cadence pass over a long recording.

    The plan reports, in order:

    * ``coarse`` - the retained tier, its sample count and its
      cadence;
    * ``dense`` - the windows, each with the triggers that earned it,
      the budget, and the coverage it spends;
    * ``deferred`` - the triggers the budget did not reach, and why;
    * ``skipped_static`` - the intervals the plan deliberately left
      coarse, with the measurement that says they are static.
    """
    duration = _number(duration_seconds)
    rate = _number(fps)
    if duration is None or duration <= 0:
        raise AdaptiveInspectionError(
            "duration must be a positive number of seconds")
    if rate is None or rate <= 0:
        raise AdaptiveInspectionError(
            "fps must be a positive number of frames a second")
    if not 0 < dense_coverage_fraction <= 1:
        raise AdaptiveInspectionError(
            "dense_coverage_fraction must be in (0, 1]")
    if trigger_margin_seconds < 0 or not math.isfinite(trigger_margin_seconds):
        raise AdaptiveInspectionError(
            "trigger_margin_seconds must be a finite non-negative number")
    if max_window_seconds <= 0 or not math.isfinite(max_window_seconds):
        raise AdaptiveInspectionError(
            "max_window_seconds must be a finite positive number")
    if max_dense_seconds <= 0 or not math.isfinite(max_dense_seconds):
        raise AdaptiveInspectionError(
            "max_dense_seconds must be a finite positive number")

    coarse = sorted(float(t) for t in (coarse_times or ())
                    if _number(t) is not None)
    # The budget is the SMALLER of the two bounds: a share of the
    # recording, and an absolute cap.  A recording-relative budget alone
    # lets a long file buy a great many dense frames; the cap is what
    # keeps the tier affordable.
    coverage_budget = min(duration * dense_coverage_fraction,
                          max_dense_seconds)

    ordered = sorted(triggers or (), key=Trigger.sort_key)
    windows: list[DenseWindow] = []
    deferred: list[dict[str, Any]] = []
    spent = 0.0
    for trigger in ordered:
        margin = trigger_margin_seconds
        if trigger.tolerance_seconds is not None:
            margin = max(margin, trigger.tolerance_seconds)
        start = max(0.0, trigger.start - margin)
        end = min(duration, trigger.end + margin)
        if end < start:
            end = start
        bracket = None
        if trigger.kind == "object_state_change":
            # The coarse tier saw the CHANGE, not its beginning, so the
            # ONSET lies somewhere in the bracket the coarse samples
            # leave around it.  The bracket is REPORTED, and the dense
            # window is NOT stretched across it.
            #
            # The measurement says why: on the M0 fixture the coarse
            # sample that saw the hand reach the mouth (t = 33.233 s)
            # had a measured hand-to-lips distance of 0.694, while the
            # event's own band (33.3-33.7 s) measures 0.13-0.43 and a
            # DIFFERENT, earlier approach (31.9-32.0 s) measures
            # 0.176.  A window stretched back over the coarse bracket
            # therefore names THAT event, not the one the trigger
            # fired on.  The window stays tight, so the dense tier
            # names the event the measurement pointed at.
            #
            # See docs/benchmarks/2026-10-21-adaptive-inspection.md.
            bracket = {"bracket_start": round(_bracket_start(coarse,
                                                             trigger.start), 6),
                       "bracket_end": round(trigger.start, 6),
                       "onset_uncertainty_seconds": round(
                           trigger.start - _bracket_start(coarse,
                                                          trigger.start), 6)}
        if (end - start) > max_window_seconds:
            # A trigger that spans more than the widest allowed window
            # is INSPECTED AT ITS EDGES, not across its whole span:
            # the edges are where the change is.
            start = max(0.0, end - max_window_seconds)
        merged = _merge_window(windows, start, end)
        if merged is not None:
            # A merged window is ONE dense pass, and it costs what the
            # MERGED window costs, not what each trigger cost on its
            # own: the budget is charged for the coverage actually
            # bought.
            cost = sum(w.duration_seconds for w in merged) - spent
            if spent + cost > coverage_budget + 1e-9:
                deferred.append({
                    "trigger": trigger.to_dict(),
                    "reason": "dense coverage budget reached",
                    "coverage_seconds": round(cost, 6),
                    "budget_seconds": round(coverage_budget, 6),
                })
                continue
            spent += cost
            windows = merged
            continue
        cost = end - start
        if spent + cost > coverage_budget + 1e-9:
            deferred.append({
                "trigger": trigger.to_dict(),
                "reason": "dense coverage budget reached",
                "coverage_seconds": round(cost, 6),
                "budget_seconds": round(coverage_budget, 6),
            })
            continue
        spent += cost
        window = DenseWindow(start=start, end=end, triggers=[trigger])
        if bracket is not None:
            window.onset_bracket = bracket
        windows.append(window)

    windows.sort(key=lambda w: (w.start, w.end))
    covered = sum(w.duration_seconds for w in windows)

    return {
        "policy_version": POLICY_VERSION,
        "coarse": {
            "sampled_frames": len(coarse),
            "fps": rate,
            "duration_seconds": round(duration, 6),
            "cadence_seconds": _cadence(coarse),
            "retained": True,
            "basis": "the shared coarse sample; unchanged by this plan",
        },
        "dense": {
            "coverage_budget_seconds": round(coverage_budget, 6),
            "coverage_fraction": dense_coverage_fraction,
            "max_dense_seconds": max_dense_seconds,
            "covered_seconds": round(covered, 6),
            "coverage_fraction_spent": round(covered / duration, 6)
            if duration else 0.0,
            "frames_at_source_cadence": int(round(covered * rate)),
            "window_count": len(windows),
            "windows": [w.to_dict() for w in windows],
        },
        "deferred": deferred,
        "skipped_static": _static_intervals(windows, duration),
        "triggers": [t.to_dict() for t in ordered],
    }


def _cadence(times: Sequence[float]) -> float | None:
    """The coarse tier's own cadence, in seconds, or ``None``.

    The cadence is what makes an ONSET uncertain: a change the coarse
    tier saw could have begun anywhere inside the interval its
    samples bracket.
    """
    if len(times) < 2:
        return None
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not gaps:
        return None
    gaps.sort()
    return round(gaps[len(gaps) // 2], 6)


def _bracket_start(coarse: Sequence[float], at: float) -> float:
    """Where the coarse samples bracket a time: the sample before it.

    The sample AT the change already sees the event, so it cannot
    bound the onset; the sample BEFORE it is the one that left the
    interval unmeasured, and the onset lies in between.
    """
    before = [t for t in coarse if t < at]
    return before[-1] if before else (coarse[0] if coarse else at)


def _merge_window(windows: list[DenseWindow], start: float,
                   end: float) -> list[DenseWindow] | None:
    """Merge a window into the ones it overlaps, or return ``None``.

    Two triggers that fall inside one interval are ONE dense pass: the
    engine decodes the interval once and reads both events off it.
    """
    overlapping = [w for w in windows
                   if w.start <= end and start <= w.end]
    if not overlapping:
        return None
    merged = DenseWindow(
        start=min([w.start for w in overlapping] + [start]),
        end=max([w.end for w in overlapping] + [end]),
        triggers=[t for w in overlapping for t in w.triggers])
    brackets = [w.onset_bracket for w in overlapping
                if w.onset_bracket is not None]
    if brackets:
        merged.onset_bracket = {
            "bracket_start": round(min(b["bracket_start"]
                                       for b in brackets), 6),
            "bracket_end": round(max(b["bracket_end"]
                                     for b in brackets), 6),
            "onset_uncertainty_seconds": round(
                max(b["onset_uncertainty_seconds"] for b in brackets), 6),
        }
    kept = [w for w in windows if w not in overlapping]
    kept.append(merged)
    kept.sort(key=lambda w: (w.start, w.end))
    return kept


def _static_intervals(windows: Sequence[DenseWindow],
                       duration: float) -> list[dict[str, Any]]:
    """The intervals the plan leaves coarse because nothing changed.

    These are the intervals BETWEEN the dense windows: the coarse tier
    measured them, no trigger fired in them, and the dense tier is not
    run over them again.  Naming them is the difference between "we did
    not look again here" and a silent gap.
    """
    out: list[dict[str, Any]] = []
    cursor = 0.0
    for window in windows:
        if window.start > cursor:
            out.append({"start": round(cursor, 6),
                        "end": round(window.start, 6),
                        "reason": "static: no measured change, coarse tier "
                                  "retained, not re-inspected"})
        cursor = max(cursor, window.end)
    if cursor < duration:
        out.append({"start": round(cursor, 6), "end": round(duration, 6),
                    "reason": "static: no measured change, coarse tier "
                              "retained, not re-inspected"})
    return out


# ── Filling the dense tier ────────────────────────────────────────────


def select_dense_samples(coarse_samples: Sequence[Mapping[str, Any]],
                          plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The coarse samples that fall inside the plan's dense windows.

    The dense tier can be filled from samples that already exist: the
    coarse tier's own frames, at their own timestamps, are the finest
    evidence the engine holds about those intervals, and reusing them
    costs no decode.  Each row says which window it serves and which
    trigger earned that window.

    The rows keep the sample's OWN media timestamp and frame index; a
    sample is never re-timed to land on a window edge.
    """
    windows = (plan.get("dense") or {}).get("windows") or []
    rows: list[dict[str, Any]] = []
    for sample in sorted(coarse_samples or (),
                         key=lambda s: _number(s.get("t")) or 0.0):
        at = _number(sample.get("t"))
        if at is None:
            continue
        for window in windows:
            if float(window["start"]) <= at <= float(window["end"]):
                row = {"t": at, "window_start": window["start"],
                       "window_end": window["end"],
                       "evidence": window.get("evidence") or []}
                if sample.get("pts_seconds") is not None:
                    row["pts_seconds"] = sample["pts_seconds"]
                if sample.get("frame_index") is not None:
                    row["frame_index"] = sample["frame_index"]
                rows.append(row)
                break
    return rows


def _peak_rss_mb() -> float:
    """The process's peak resident set, in megabytes."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2)


def _child_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def name_boundary(samples: Sequence[Mapping[str, Any]],
                  series: Sequence[float | None]) -> dict[str, Any] | None:
    """The frame a measured series is most extreme on.

    The rule is the coarse lane's own: the hand-to-lips distance, and
    the frame where it is SMALLEST - the closest approach.  Nothing
    here invents a judgement; the series is measured, and the frame
    with the extreme value is named.

    A series with no measured value names no frame: an absent
    measurement is not an extreme.
    """
    measured = [(index, value) for index, value in enumerate(series)
                if value is not None]
    if not measured:
        return None
    index, value = min(measured, key=lambda row: row[1])
    if index >= len(samples):
        return None
    sample = samples[index]
    return {
        "frame_index": sample.get("frame_index"),
        "pts_seconds": sample.get("pts_seconds"),
        "on_grid": sample.get("on_grid"),
        "value": round(float(value), 6),
        "rule": "closest approach of the measured hand-to-lips distance",
    }


def run_inspection(
        plan: Mapping[str, Any],
        source_file: str,
        timebase: frame_exact.SourceTimebase,
        *,
        out_dir: str | os.PathLike,
        width: int = frame_exact.SAMPLE_WIDTH,
        measure: bool = True,
) -> dict[str, Any]:
    """Run the plan's dense tier over one source.

    Every window is decoded at the source's own frame cadence by
    :func:`library.tools.frame_exact.sample_interval`, so every sample
    carries the source's own ``pts_seconds`` and its source frame
    index.  The report is the cost of the tier and what it saw:

    * ``samples`` - the samples, each with its original media
      timestamp and source frame index;
    * ``windows`` - per window, the samples it produced and the
      boundary frame the measured series names;
    * ``cost`` - frames decoded, wall seconds, CPU seconds and peak
      memory.

    ``measure=False`` decodes without running the Vision pass, which
    is how the cost of the DECODE alone is separated from the cost of
    the measurement.
    """
    if not isinstance(plan, Mapping):
        raise AdaptiveInspectionError("the plan must be a mapping")
    windows = (plan.get("dense") or {}).get("windows") or []
    started = time.perf_counter()
    cpu_before = _child_cpu_seconds()
    samples: list[dict[str, Any]] = []
    per_window: list[dict[str, Any]] = []
    frames_decoded = 0
    for index, window in enumerate(windows):
        start = float(window["start"])
        end = float(window["end"])
        if end <= start:
            continue
        target = os.path.join(str(out_dir), f"window_{index:04d}")
        sample = frame_exact.sample_interval(source_file, timebase, start, end,
                                            target, width=width)
        frames = sample.get("frames") or []
        frames_decoded += len(frames)
        measured: list[dict[str, Any]] = []
        series: list[float | None] = []
        if measure and frames:
            records = frame_exact.measure_frames([f["file"] for f in frames])
            distances = frame_exact.hand_mouth_distances(records)
            for frame, record, distance in zip(frames, records, distances):
                measured.append({
                    "pts_seconds": frame["pts_seconds"],
                    "frame_index": frame["frame_index"],
                    "on_grid": frame["on_grid"],
                    "faces": len(record.get("faces") or []),
                    "hands": len(record.get("hands") or []),
                    "hand_to_lips": (None if distance is None
                                      else round(float(distance), 6)),
                })
                series.append(distance)
        else:
            for frame in frames:
                measured.append({
                    "pts_seconds": frame["pts_seconds"],
                    "frame_index": frame["frame_index"],
                    "on_grid": frame["on_grid"],
                })
                series.append(None)
        boundary = name_boundary(measured, series) if measure else None
        samples.extend(measured)
        per_window.append({
            "index": index,
            "start": round(start, 6),
            "end": round(end, 6),
            "evidence": window.get("evidence") or [],
            "frames": len(frames),
            "first_frame_index": frames[0]["frame_index"] if frames else None,
            "last_frame_index": frames[-1]["frame_index"] if frames else None,
            "boundary": boundary,
            "samples": measured,
            "decode": sample.get("cost") or {},
            "coverage": sample.get("coverage") or {},
        })
    wall = round(time.perf_counter() - started, 3)
    cpu = round(_child_cpu_seconds() - cpu_before, 3)
    return {
        "policy_version": plan.get("policy_version"),
        "source_file": os.path.abspath(source_file),
        "samples": samples,
        "windows": per_window,
        "boundaries": [w["boundary"] for w in per_window if w.get("boundary")],
        "cost": {
            "frames_decoded": frames_decoded,
            "frames_measured": sum(1 for s in samples if "faces" in s),
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            "peak_rss_mb": _peak_rss_mb(),
            "measured": bool(measure),
        },
        "timestamp_basis": "the source's own pts and source frame index, "
                           "read off the container; no frame is re-timed",
    }


def inspect_source(
        source_file: str,
        duration_seconds: float,
        fps: float,
        coarse_times: Sequence[float],
        triggers: Sequence[Trigger],
        *,
        out_dir: str | os.PathLike,
        timebase: frame_exact.SourceTimebase | None = None,
        dense_coverage_fraction: float = DEFAULT_DENSE_COVERAGE_FRACTION,
        measure: bool = True,
) -> dict[str, Any]:
    """Plan and run the dense tier over one source.

    The convenience form of :func:`plan_inspection` and
    :func:`run_inspection`: the plan, the run, and the two together
    in one report.  ``timebase`` is measured from the source when it
    is not given, so every sample carries the source's own frame grid.
    """
    if timebase is None:
        timebase = frame_exact.probe_timebase(source_file)
    plan = plan_inspection(duration_seconds, fps, coarse_times, triggers,
                            dense_coverage_fraction=dense_coverage_fraction)
    report = run_inspection(plan, source_file, timebase, out_dir=out_dir,
                            measure=measure)
    report["plan"] = plan
    return report


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None
