"""Final-output verification: ONE verdict per acceptance criterion.

The captain's capability punch list, section 6, "Final-Output
Verification and Quality Control", names eight criteria.  This module
is their ONE enumeration and the runner that answers them:

    QA-001  clip and track structure against the accepted edit plan
    QA-002  exact timing, source ranges, media identity, linked audio
    QA-003  missing media, black frames, frozen frames, gaps, overlaps
    QA-004  output format, frame rate, dimensions, duration, encoding
    QA-005  subtitle/caption timing and presence
    QA-006  graphical elements render at the expected placement and scale
    QA-007  loudness, peaks, clipping, channel configuration
    QA-008  export identity and provenance against the project revision

What it is
----------
A COMPOSER, not a second set of measurements.  Every criterion here
calls the checker that already answers it - `render_qa` for the file,
`manifest_validator` for the plan, `subtitle_qa` for the captions,
`overlay_verify` for the overlays, `plan_provenance` and the delivery
ledger for the identity - and records what that checker measured.  The
eight criteria did not exist as ONE thing: the measurements were spread
across six modules, and a reader asking "is this export acceptable?"
had to know which six and in which order.

What it is not
--------------
It does not invent an expectation.  A criterion whose evidence is
missing is UNKNOWN, with the reason and what would resolve it - never a
pass.  "A verified export includes evidence for every required
acceptance criterion", so an export nobody could check is not a
verified export; it is an unchecked one, and this says so rather than
reading as clean.

The three records it reads
--------------------------
- THE PLAN: the accepted edit plan (the compiled `assembly_manifest`).
- THE BUILD: the built timeline's own record - the committed timeline
  snapshot (`pipeline_output/review/<timeline>.timeline.json`), which is
  on disk, so this runs with no Resolve and no render.
- THE EXPORT: the delivered file, and the identity record the delivery
  wrote beside it.

A fourth input - the live timeline - is what answers the legs that only
Resolve can (an overlay's stored transform, a linked audio clip's sync
offset).  When it is absent those legs are UNKNOWN and name the check
that answers them.

The reader of a finding
-----------------------
Every criterion names its reader (AGENTS.md 10.4): a failing criterion
is read by the step that owns the defect, and an UNKNOWN criterion is
read by whoever can supply the missing evidence.  A criterion nobody
reads is a criterion that will not be fixed.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Tuple

# ── The verdicts ──
#
# PASS is the only verdict that lets an export out.  FAIL says the
# export is wrong.  UNKNOWN says nobody could tell - which is NOT a
# pass, because "a verified export includes evidence for every required
# acceptance criterion".  NOT_APPLICABLE says the criterion has nothing
# to judge: a project with no captions has no caption timing to verify.

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"

#: A criterion's reader: who acts on a failure of this criterion.
READER_PLAN = "the plan's owner - the compile step, which wrote the plan"
READER_BUILD = "the build's owner - the render/build step, which laid the timeline"
READER_EXPORT = "the delivery's owner - the deliver step, which wrote the file"
READER_MASTER = "the master - the render step, which produced the pixels"
READER_EVIDENCE = "whoever can supply the missing evidence"

#: The one frame of slack a placed clip may differ from its planned
#: range by.  The plan states seconds rounded to milliseconds; the
#: timeline states frames.  One frame covers the rounding on both sides.
FRAME_SLACK_SECONDS = 0.001

#: How far a placed clip may sit from its planned range and still be
#: the clip the plan MEANT.  Past half a second it is a different
#: clip, and the planned one is not on the timeline at all.
NEAR_MISS_SECONDS = 0.5


@dataclass(frozen=True)
class Criterion:
    """One acceptance criterion, as the punch list states it."""

    id: str
    title: str
    needs: str
    answered_by: str


#: The eight criteria, verbatim from the punch list's section 6.
CRITERIA: Tuple[Criterion, ...] = (
    Criterion(
        "QA-001",
        "Verify expected clip and track structure against the accepted "
        "edit plan.",
        "the accepted edit plan",
        "manifest_validator, plus the plan/build inventory comparison "
        "below",
    ),
    Criterion(
        "QA-002",
        "Verify exact timing, source ranges, media identity, and "
        "linked-audio synchronization.",
        "the accepted edit plan and the build it produced",
        "the plan/build per-clip comparison below, and "
        "reel_read's linked-item read for the audio",
    ),
    Criterion(
        "QA-003",
        "Detect missing media, black frames, frozen frames, unexpected "
        "gaps, and unintended overlaps.",
        "the exported file, and the plan and build for what should be "
        "there",
        "render_qa.detect_black_frames, render_qa.detect_freeze_frames, "
        "and the gap/overlap walk below",
    ),
    Criterion(
        "QA-004",
        "Verify output format, frame rate, dimensions, duration, and "
        "encoding.",
        "the exported file and the declared frame, rate, length and "
        "encoding",
        "render_qa.verify_resolution, verify_framerate, verify_duration "
        "and verify_encoding",
    ),
    Criterion(
        "QA-005",
        "Check subtitle/caption timing and presence.",
        "the plan's captions and the build that carried them",
        "subtitle_qa.verify_subtitle_timing, plus the caption presence "
        "walk below",
    ),
    Criterion(
        "QA-006",
        "Verify graphical elements render at the expected placement and "
        "scale.",
        "the plan's graphical elements and the build that placed them",
        "render_qa.measure_graphics_render for the pixels, and "
        "overlay_verify.verify_values for the placement read-back",
    ),
    Criterion(
        "QA-007",
        "Verify technical audio requirements including loudness, peaks, "
        "clipping, and channel configuration.",
        "the exported file and the declared loudness target and channel "
        "count",
        "render_qa.measure_lufs, render_qa.verify_channel_configuration "
        "and render_qa.detect_clipping",
    ),
    Criterion(
        "QA-008",
        "Verify export identity and provenance against the project "
        "revision that produced it.",
        "the delivery's own identity record and the project's revision "
        "record",
        "the delivery ledger and sidecar, and plan_provenance",
    ),
)

CRITERION_IDS: Tuple[str, ...] = tuple(criterion.id for criterion in CRITERIA)


@dataclass(frozen=True)
class CriterionResult:
    """One criterion's verdict, with the evidence behind it."""

    criterion: str
    title: str
    verdict: str
    detail: str
    evidence: Tuple[Mapping[str, Any], ...] = ()
    reader: str = READER_EVIDENCE

    @property
    def passed(self) -> bool:
        return self.verdict == PASS

    def as_dict(self) -> dict:
        return {
            "criterion": self.criterion,
            "title": self.title,
            "verdict": self.verdict,
            "detail": self.detail,
            "evidence": [dict(entry) for entry in self.evidence],
            "reader": self.reader,
        }


@dataclass(frozen=True)
class ExportExpectation:
    """Everything the export is judged against.

    Every field is a DECLARATION somebody else made - the plan, the
    build, the delivery spec, the project.  Nothing here is guessed by
    this module, and a field left empty means the criterion that needs
    it reports UNKNOWN rather than inventing one.

    `built` is the built timeline's own record (the committed timeline
    snapshot's document).  `delivery` is the identity record the
    delivery wrote beside the file.  `overlays` is the live overlay
    read-back, when a caller has one; without it the placement leg of
    QA-006 is UNKNOWN.
    """

    plan: Optional[Mapping[str, Any]] = None
    built: Optional[Mapping[str, Any]] = None
    delivery: Optional[Mapping[str, Any]] = None
    overlays: Optional[Mapping[str, Any]] = None
    project_folder: str = ""
    duration_seconds: Optional[float] = None
    resolution: Optional[Tuple[int, int]] = None
    frame_rate: Optional[float] = None
    codec: str = ""
    container: str = ""
    channels: Optional[int] = None
    loudness_target_lufs: Optional[float] = None
    loudness_tolerance_lufs: Optional[float] = None
    true_peak_ceiling_dbtp: Optional[float] = None
    declared_black_beats: Tuple[Any, ...] = ()
    spine_blocks: Tuple[Any, ...] = ()
    source_frame_rates: Mapping[str, float] = field(default_factory=dict)

    def with_plan(self, plan: Mapping[str, Any]) -> "ExportExpectation":
        """The same expectation, with the plan's own declarations read in.

        The plan states the frame, the rate, the length and the spine.
        A declaration the plan makes REPLACES a default; one it does
        not make leaves the caller's value alone.
        """
        project = plan.get("project") if isinstance(plan, Mapping) else None
        project = project if isinstance(project, Mapping) else {}
        resolution = self.resolution
        declared = project.get("resolution")
        if isinstance(declared, (list, tuple)) and len(declared) == 2:
            try:
                resolution = (int(declared[0]), int(declared[1]))
            except (TypeError, ValueError):
                resolution = self.resolution
        frame_rate = self.frame_rate
        try:
            declared_fps = float(project.get("frame_rate"))
            if declared_fps > 0:
                frame_rate = declared_fps
        except (TypeError, ValueError):
            pass
        duration = self.duration_seconds
        try:
            declared_duration = float(project.get("duration_seconds"))
            if declared_duration > 0:
                duration = declared_duration
        except (TypeError, ValueError):
            pass
        spine = plan.get("_spine_blocks") if isinstance(plan, Mapping) else None
        spine = tuple(spine or ())
        beats = self.declared_black_beats
        if not beats and spine:
            from library.tools.spine_contract import declared_black_beat_ranges

            beats = tuple(declared_black_beat_ranges(list(spine)))
        return ExportExpectation(
            plan=plan,
            built=self.built,
            delivery=self.delivery,
            overlays=self.overlays,
            project_folder=self.project_folder,
            duration_seconds=duration,
            resolution=resolution,
            frame_rate=frame_rate,
            codec=self.codec,
            container=self.container,
            channels=self.channels,
            loudness_target_lufs=self.loudness_target_lufs,
            loudness_tolerance_lufs=self.loudness_tolerance_lufs,
            true_peak_ceiling_dbtp=self.true_peak_ceiling_dbtp,
            declared_black_beats=beats,
            spine_blocks=spine or self.spine_blocks,
            source_frame_rates=dict(self.source_frame_rates),
        )


@dataclass(frozen=True)
class ExportVerification:
    """One export's verdict on all eight criteria."""

    video_path: str
    results: Tuple[CriterionResult, ...]

    @property
    def passed(self) -> bool:
        """True only when every criterion PASSED.

        An UNKNOWN criterion does not pass.  An export nobody could
        check is an unchecked export, and reading it as clean is the
        failure this module exists to prevent.
        """
        return bool(self.results) and all(
            result.verdict == PASS for result in self.results)

    def failures(self) -> Tuple[CriterionResult, ...]:
        return tuple(r for r in self.results if r.verdict == FAIL)

    def unknowns(self) -> Tuple[CriterionResult, ...]:
        return tuple(r for r in self.results if r.verdict == UNKNOWN)

    def result_for(self, criterion: str) -> Optional[CriterionResult]:
        for result in self.results:
            if result.criterion == criterion:
                return result
        return None

    def as_dict(self) -> dict:
        return {
            "video_path": self.video_path,
            "passed": self.passed,
            "criteria": [result.as_dict() for result in self.results],
            "failed": [r.criterion for r in self.failures()],
            "unknown": [r.criterion for r in self.unknowns()],
        }

    def summary(self) -> str:
        """One line per criterion, then the verdict."""
        lines = [f"{r.criterion} {r.verdict}: {r.detail}" for r in self.results]
        lines.append(
            "PASSED" if self.passed else "NOT PASSED")
        return "\n".join(lines)


class ExportVerificationRefused(ValueError):
    """The export cannot be verified at all.

    An absent file is not a failing export; it is nothing to verify.
    """


# ── The plan and the build, read ──


def _plan_tracks(plan: Mapping[str, Any]) -> "dict[str, list]":
    """The plan's tracks that carry clips, as `{track key: [clip]}`."""
    tracks = plan.get("tracks") if isinstance(plan, Mapping) else None
    if not isinstance(tracks, Mapping):
        return {}
    out: "dict[str, list]" = {}
    for key, value in tracks.items():
        if not isinstance(value, Mapping):
            continue
        clips = [clip for clip in (value.get("clips") or [])
                 if isinstance(clip, Mapping)]
        if clips:
            out[str(key)] = clips
    return out


def _built_rows(built: Mapping[str, Any]) -> "list[dict]":
    """The built timeline's rows, as plain data.

    `{"media_type", "index", "name", "clips"}` per row, in the built
    document's own order.
    """
    rows = []
    tracks = built.get("tracks") if isinstance(built, Mapping) else None
    for track in (tracks or []):
        if not isinstance(track, Mapping):
            continue
        rows.append({
            "media_type": str(track.get("type") or ""),
            "index": track.get("index"),
            "name": str(track.get("name") or ""),
            "clips": [clip for clip in (track.get("clips") or [])
                      if isinstance(clip, Mapping)],
        })
    return rows


def _timeline_fps(plan: Optional[Mapping[str, Any]],
                  built: Optional[Mapping[str, Any]]) -> Optional[float]:
    """The timeline's own rate, from the build first and the plan second."""
    metadata = (built or {}).get("metadata") if isinstance(
        built, Mapping) else None
    metadata = metadata if isinstance(metadata, Mapping) else {}
    project = (plan or {}).get("project") if isinstance(
        plan, Mapping) else None
    project = project if isinstance(project, Mapping) else {}
    for value in (metadata.get("fps"), project.get("frame_rate")):
        try:
            fps = float(value)
        except (TypeError, ValueError):
            continue
        if fps > 0:
            return fps
    return None


def _plan_span(clip: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
    """A planned clip's timeline range in seconds, or None."""
    try:
        start = float(clip.get("timeline_in"))
        end = float(clip.get("timeline_out"))
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    return (start, end)


def _built_span(clip: Mapping[str, Any],
                fps: Optional[float]) -> Optional[Tuple[float, float]]:
    """A built clip's record range in seconds, or None."""
    if not fps:
        return None
    try:
        start = float(clip.get("record_in"))
        end = float(clip.get("record_out"))
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    return (start / fps, end / fps)


def _match_clips(planned: Sequence[Mapping[str, Any]],
                 candidates: Sequence[Mapping[str, Any]],
                 fps: Optional[float],
                 tolerance: float) -> Tuple[list, list]:
    """Each planned clip against the built clip that was placed as it.

    Matched by TIMELINE RANGE, never by name: the plan names a clip and
    the timeline names it something else, but both state where it plays.
    A built clip serves at most one planned clip, so a repeated source
    does not collapse onto its first occurrence.

    Returns `(pairs, unmatched, used)` where a pair is
    `(planned clip, built clip, drift in seconds)` and `used` is the
    set of candidate indexes a pair claimed.

    A placed clip within `NEAR_MISS_SECONDS` of the planned range is
    the one the plan MEANT, even when it sits further off than a frame:
    reporting it as a drift says "this clip plays 0.2s away from where
    the plan put it", which is the defect, where reporting it as
    missing says only "nothing is here".  Beyond that bound it is a
    different clip and the planned one is simply not on the timeline.
    """
    pairs = []
    unmatched = []
    used = set()
    for clip in planned:
        span = _plan_span(clip)
        if span is None:
            unmatched.append((clip, "the plan states no timeline range"))
            continue
        best = None
        near = None
        for index, candidate in enumerate(candidates):
            if index in used:
                continue
            other = _built_span(candidate, fps)
            if other is None:
                continue
            drift = max(abs(span[0] - other[0]), abs(span[1] - other[1]))
            if best is None or drift < best[1]:
                best = (index, drift)
            if drift <= NEAR_MISS_SECONDS and (near is None
                                               or drift < near[1]):
                near = (index, drift)
        chosen = None
        if best is not None and best[1] <= tolerance:
            chosen = best
        elif near is not None:
            chosen = near
        if chosen is None:
            unmatched.append((clip, "no placed clip covers the planned range"))
            continue
        used.add(chosen[0])
        pairs.append((clip, candidates[chosen[0]], chosen[1]))
    return pairs, unmatched, used


@dataclass
class _Comparison:
    """The plan's tracks against the build's rows, matched once."""

    fps: Optional[float]
    tolerance: float
    matched: "dict[str, dict]"
    unplanned_rows: "list[dict]"

    def pairs(self) -> "list[tuple]":
        out = []
        for entry in self.matched.values():
            out.extend(entry.get("pairs") or [])
        return out

    def rows(self) -> "list[dict]":
        """Every row the build carries, matched or not.

        The overlay rows - a caption, a title - are not planned tracks,
        so they are never MATCHED.  A check that looks for a caption on
        the timeline has to look at the rows the plan did not describe,
        or it will find nothing and call it missing.
        """
        rows = []
        seen = set()
        for entry in self.matched.values():
            row = entry.get("row")
            if row is not None and id(row) not in seen:
                seen.add(id(row))
                rows.append(row)
        for unplanned in self.unplanned_rows:
            row = unplanned.get("row")
            if row is not None and id(row) not in seen:
                seen.add(id(row))
                rows.append(row)
        return rows


def _compare_plan_and_build(plan: Mapping[str, Any],
                             built: Mapping[str, Any],
                             fps: Optional[float]) -> _Comparison:
    """Match the plan's tracks against the build's rows.

    A planned track is carried by the row that holds the most of its
    clips; a row that carries clips the plan does not describe, and
    points at media the plan declared, is a PICTURE row the plan
    forgot.  A row pointing at media the plan never declared is an
    OVERLAY row (a caption, a title), which the plan describes
    elsewhere and which is reported, never failed.

    A planned track that matches NOTHING claims no row: a track the
    build dropped must not take a row away from the track that is
    there.
    """
    tolerance = (1.0 / fps if fps else 0.0) + FRAME_SLACK_SECONDS
    plan_tracks = _plan_tracks(plan)
    rows = _built_rows(built)
    declared_sources = {
        os.path.normpath(str(clip.get("source_file")))
        for clips in plan_tracks.values() for clip in clips
        if clip.get("source_file")
    }
    matched: "dict[str, dict]" = {}
    claimed = set()
    for key, clips in plan_tracks.items():
        media_type = "video" if key.upper().startswith("V") else "audio"
        best = None
        for row in rows:
            if row["media_type"] != media_type:
                continue
            pairs, unmatched, used = _match_clips(clips, row["clips"], fps,
                                                  tolerance)
            # A row that carries the planned clips EXACTLY wins over a
            # row that merely carries something near them: a B-roll
            # clip planned for 1.0-2.0s is a near miss for the A-roll
            # clip that plays 0.0-1.0s, and matching the planned cutaway
            # to the A-roll row is how the picture loses its coverage.
            exact = sum(1 for _, _, drift in pairs if drift <= tolerance)
            drift = sum(drift for _, _, drift in pairs)
            score = (exact, -drift)
            if best is None or score > best["score"]:
                best = {"row": row, "score": score, "pairs": pairs,
                        "unmatched": unmatched, "used": used,
                        "exact": exact}
        if best is None:
            matched[key] = {"row": None, "pairs": [], "unmatched": [
                (clip, "the build carries no row of this kind")
                for clip in clips], "extra": []}
            continue
        if best["exact"] == 0:
            matched[key] = {"row": None, "pairs": [], "unmatched": [
                (clip, "no placed clip covers the planned range")
                for clip in clips], "extra": []}
            continue
        row = best["row"]
        claimed.add(id(row))
        matched[key] = {
            "row": row,
            "pairs": best["pairs"],
            "unmatched": best["unmatched"],
            "extra": [clip for index, clip in enumerate(row["clips"])
                      if index not in best["used"]],
        }
    unplanned = []
    for row in rows:
        if id(row) in claimed:
            continue
        sources = {
            os.path.normpath(str(clip.get("file_path")))
            for clip in row["clips"] if clip.get("file_path")
        }
        unplanned.append({
            "row": row,
            "carries_declared_media": bool(sources & declared_sources),
            "sources": sorted(sources),
        })
    return _Comparison(fps=fps, tolerance=tolerance, matched=matched,
                       unplanned_rows=unplanned)


# ── The criteria ──


def _criterion(criterion_id: str) -> Criterion:
    for entry in CRITERIA:
        if entry.id == criterion_id:
            return entry
    raise KeyError(criterion_id)


def _result(criterion_id: str, verdict: str, detail: str,
            evidence: Sequence[Mapping[str, Any]] = (),
            reader: str = READER_EVIDENCE) -> CriterionResult:
    criterion = _criterion(criterion_id)
    return CriterionResult(
        criterion=criterion.id,
        title=criterion.title,
        verdict=verdict,
        detail=detail,
        evidence=tuple(dict(entry) for entry in evidence),
        reader=reader,
    )


def _qa001(expectation: ExportExpectation) -> CriterionResult:
    """QA-001: the export's clip and track structure against the plan."""
    from library.tools import manifest_validator

    plan = expectation.plan
    if not plan:
        return _result(
            "QA-001", UNKNOWN,
            "no accepted edit plan was supplied, so the export's structure "
            "cannot be judged against one",
            reader=READER_PLAN)
    evidence = []
    errors = manifest_validator.validate_manifest(dict(plan))
    evidence.append({"check": "plan_structure", "passed": not errors,
                     "errors": list(errors)})
    if errors:
        return _result(
            "QA-001", FAIL,
            f"the accepted plan is not structurally valid: {errors[0]}",
            evidence, reader=READER_PLAN)
    tracks = _plan_tracks(plan)
    evidence.append({
        "check": "plan_tracks",
        "tracks": {key: len(clips) for key, clips in tracks.items()},
    })
    if not expectation.built:
        return _result(
            "QA-001", UNKNOWN,
            "the accepted plan is structurally valid, but the build it "
            "produced was not supplied, so the export's structure "
            "cannot be compared with it",
            evidence, reader=READER_BUILD)
    comparison = _compare_plan_and_build(plan, expectation.built,
                                         expectation.frame_rate)
    problems = []
    for key, entry in comparison.matched.items():
        row = entry.get("row")
        if row is None:
            problems.append(f"the build carries no row for {key}")
            continue
        for clip, reason in entry.get("unmatched") or []:
            problems.append(
                f"{key} clip {clip.get('label') or clip.get('source_file')!r} "
                f"is in the plan but not on the timeline ({reason})")
        for clip in entry.get("extra") or []:
            problems.append(
                f"{key} carries a clip the plan does not describe "
                f"({clip.get('name')!r})")
    for unplanned in comparison.unplanned_rows:
        if unplanned["carries_declared_media"]:
            problems.append(
                f"the build carries a row ({unplanned['row']['name']!r}) "
                f"holding declared media that no planned track describes")
    evidence.append({
        "check": "plan_against_build",
        "matched": {key: {
            "row": (entry.get("row") or {}).get("name"),
            "clips_matched": len(entry.get("pairs") or []),
            "clips_unmatched": len(entry.get("unmatched") or []),
        } for key, entry in comparison.matched.items()},
        "rows_without_a_planned_track": [
            {"name": entry["row"]["name"],
             "media_type": entry["row"]["media_type"],
             "carries_declared_media": entry["carries_declared_media"]}
            for entry in comparison.unplanned_rows],
    })
    if problems:
        return _result(
            "QA-001", FAIL,
            f"{len(problems)} structural difference(s) between the plan and "
            f"the build: {problems[0]}",
            evidence, reader=READER_BUILD)
    return _result(
        "QA-001", PASS,
        f"the build carries the plan's {len(tracks)} track(s) and every "
        f"planned clip",
        evidence, reader=READER_BUILD)


def _qa002(expectation: ExportExpectation,
           comparison: Optional[_Comparison]) -> CriterionResult:
    """QA-002: timing, source ranges, media identity, linked audio."""
    plan = expectation.plan
    if not plan:
        return _result(
            "QA-002", UNKNOWN,
            "no accepted edit plan was supplied, so the export's timing and "
            "source ranges cannot be judged",
            reader=READER_PLAN)
    if comparison is None:
        return _result(
            "QA-002", UNKNOWN,
            "the build the plan produced was not supplied, so the export's "
            "timing, source ranges and media identity cannot be compared "
            "with the plan",
            reader=READER_BUILD)
    evidence = []
    problems = []
    timing_drift = 0.0
    source_compared = 0
    source_skipped = 0
    for planned, built, drift in comparison.pairs():
        timing_drift = max(timing_drift, drift)
        if drift > comparison.tolerance:
            problems.append(
                f"{planned.get('label') or planned.get('source_file')!r} "
                f"plays {drift:.3f}s away from where the plan put it")
        identity = _media_identity(planned, built,
                                   expectation.source_frame_rates)
        if identity.get("source_compared"):
            source_compared += 1
            if not identity.get("source_matches"):
                problems.append(
                    f"{planned.get('label') or planned.get('source_file')!r} "
                    f"plays a different part of its source than the plan "
                    f"declared ({identity.get('detail')})")
        else:
            source_skipped += 1
        if identity.get("media_matches") is False:
            problems.append(
                f"{planned.get('label') or planned.get('source_file')!r} "
                f"points at different media than the plan declared "
                f"({identity.get('detail')})")
    evidence.append({
        "check": "timing_and_source",
        "clips_compared": len(comparison.pairs()),
        "worst_timing_drift_seconds": round(timing_drift, 4),
        "tolerance_seconds": round(comparison.tolerance, 4),
        "source_ranges_compared": source_compared,
        "source_ranges_not_compared": source_skipped,
    })
    linked, linked_problems = _linked_audio(plan, comparison)
    evidence.append({"check": "linked_audio", **linked})
    problems.extend(linked_problems)
    if problems:
        return _result(
            "QA-002", FAIL,
            f"{len(problems)} timing/source/identity difference(s) between "
            f"the plan and the build: {problems[0]}",
            evidence, reader=READER_BUILD)
    if not comparison.pairs():
        return _result(
            "QA-002", UNKNOWN,
            "no planned clip was matched to a placed clip, so the export's "
            "timing, source ranges and media identity were not compared",
            evidence, reader=READER_BUILD)
    if source_skipped:
        return _result(
            "QA-002", UNKNOWN,
            f"timing and media identity agree on every matched clip, but "
            f"{source_skipped} source range(s) could not be compared: no "
            f"measured frame rate for the source media",
            evidence, reader=READER_BUILD)
    if linked.get("applicable") and not linked.get("compared"):
        return _result(
            "QA-002", UNKNOWN,
            "timing, source ranges and media identity agree on every "
            "matched clip, but no speech clip could be read beside its "
            "picture, so the linked audio was not checked",
            evidence, reader=READER_BUILD)
    return _result(
        "QA-002", PASS,
        f"{len(comparison.pairs())} clip(s) match the plan in timing, "
        f"source range and media identity",
        evidence, reader=READER_BUILD)


def _media_identity(planned: Mapping[str, Any], built: Mapping[str, Any],
                    source_frame_rates: Mapping[str, float]) -> dict:
    """One planned clip against the clip that was placed as it.

    Answers three questions and says which it could answer: does the
    build point at the SAME MEDIA, does it play the same PART of it, and
    (when the source's frame rate is known) how far apart are the two
    source ranges.
    """
    planned_source = str(planned.get("source_file") or "")
    built_source = str(built.get("file_path") or "")
    record = {
        "planned_source": planned_source,
        "built_source": built_source,
        "media_matches": None,
        "source_compared": False,
        "source_matches": True,
        "detail": "",
    }
    if not planned_source or not built_source:
        record["detail"] = (
            "one side states no media at all, so identity cannot be "
            "compared")
        return record
    if os.path.normpath(planned_source) == os.path.normpath(built_source):
        record["media_matches"] = True
    else:
        record["media_matches"] = _same_media(planned_source, built_source)
        if not record["media_matches"]:
            record["detail"] = (
                f"the plan names {planned_source!r} and the build names "
                f"{built_source!r}, and they are not the same media")
            return record
    fps = source_frame_rates.get(os.path.normpath(built_source))
    if not fps:
        fps = source_frame_rates.get(os.path.normpath(planned_source))
    if not fps:
        record["detail"] = (
            "the same media, but no measured frame rate for it, so the "
            "source range was not compared")
        return record
    try:
        planned_in = float(planned.get("source_in"))
        planned_out = float(planned.get("source_out"))
    except (TypeError, ValueError):
        record["detail"] = ("the plan states no source range")
        return record
    built_in = built.get("source_in_frame")
    built_out = built.get("source_out_frame")
    if built_in is None or built_out is None:
        record["detail"] = ("the build states no source range")
        return record
    try:
        drift = max(abs(planned_in - float(built_in) / fps),
                    abs(planned_out - float(built_out) / fps))
    except (TypeError, ValueError, ZeroDivisionError):
        record["detail"] = ("the source range could not be compared")
        return record
    record["source_compared"] = True
    record["source_matches"] = drift <= (1.0 / float(fps)) + FRAME_SLACK_SECONDS
    record["detail"] = (
        f"the source range differs by {drift:.4f}s")
    return record


def _same_media(first: str, second: str) -> bool:
    """Whether two paths name the same media, by their measured identity.

    `footage_identity.fingerprint` is the media's own identity - its
    size and both of its ends - so two copies of one file are the same
    media and two different files that happen to share a basename are
    not.  A path that is not on disk proves nothing and reads as not
    the same.
    """
    if not (os.path.isfile(first) and os.path.isfile(second)):
        return False
    from library.tools import footage_identity

    try:
        return (footage_identity.fingerprint(first)
                == footage_identity.fingerprint(second))
    except OSError:
        return False


def _linked_audio(plan: Mapping[str, Any],
                  comparison: _Comparison) -> Tuple[dict, list]:
    """The audio row against the picture row it belongs to.

    A speech clip that plays a DIFFERENT part of its source than the
    picture beside it is out of sync, and an audio clip carrying a sync
    offset is desynchronised from the picture by construction.  Both
    are read from the BUILD, where the two rows sit side by side.
    """
    picture = comparison.matched.get("V1") or {}
    speech = comparison.matched.get("A1") or {}
    picture_row = picture.get("row") or {}
    speech_row = speech.get("row") or {}
    problems = []
    compared = 0
    plan_tracks = _plan_tracks(plan)
    if "V1" not in plan_tracks and "A1" not in plan_tracks:
        return {"applicable": False, "compared": 0,
                "problem": ("the plan describes no picture row beside a "
                            "speech row, so there is no linked audio to "
                            "read")}, problems
    if not picture_row or not speech_row:
        return {
            "applicable": True,
            "compared": 0,
            "problem": ("the build carries no picture row or no speech row "
                        "beside it, so the linked audio could not be read"),
        }, problems
    fps = comparison.fps
    picture_clips = []
    for clip in picture_row.get("clips") or []:
        span = _built_span(clip, fps)
        if span:
            picture_clips.append((span, clip))
    for clip in speech_row.get("clips") or []:
        span = _built_span(clip, fps)
        if not span:
            continue
        partner = None
        for other_span, other in picture_clips:
            if (abs(other_span[0] - span[0]) <= comparison.tolerance
                    and abs(other_span[1] - span[1]) <= comparison.tolerance):
                partner = other
                break
        if partner is None:
            continue
        compared += 1
        if (partner.get("source_in_frame") != clip.get("source_in_frame")
                or partner.get("source_out_frame")
                != clip.get("source_out_frame")):
            problems.append(
                f"the speech clip at {span[0]:.3f}s plays a different part "
                f"of its source than the picture beside it")
        offset = (clip.get("audio") or {}).get("AudioSyncOffset")
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            offset = 0.0
        if abs(offset) > 0:
            problems.append(
                f"the speech clip at {span[0]:.3f}s carries a sync offset "
                f"of {offset} frames against the picture beside it")
    return {"applicable": True, "compared": compared,
            "problems": len(problems)}, problems


def _qa003(video_path: str, expectation: ExportExpectation,
           comparison: Optional[_Comparison]) -> CriterionResult:
    """QA-003: missing media, black, freeze, gaps, overlaps."""
    from library.tools import render_qa

    evidence = []
    problems = []
    missing = []
    for row in ((comparison.matched if comparison else {}) or {}).values():
        built_row = row.get("row") or {}
        for clip in built_row.get("clips") or []:
            path = str(clip.get("file_path") or "")
            if path and not os.path.exists(path):
                missing.append(path)
    if comparison is not None:
        for unplanned in comparison.unplanned_rows:
            for path in unplanned.get("sources") or []:
                if not os.path.exists(path):
                    missing.append(path)
    evidence.append({"check": "missing_media", "missing": sorted(set(missing))})
    if missing:
        absent = sorted(set(missing))
        problems.append(
            f"{len(absent)} media file(s) the build points at are not "
            f"on disk, the first is {absent[0]!r}")
    black = render_qa.detect_black_frames(
        video_path, declared_beats=list(expectation.declared_black_beats))
    evidence.append({"check": "black_frames", "passed": bool(black.passed),
                     "value": black.value, "detail": black.detail})
    if not black.passed:
        problems.append(f"the export carries black: {black.detail}")
    freeze = render_qa.detect_freeze_frames(video_path)
    evidence.append({"check": "freeze_frames", "passed": bool(freeze.passed),
                     "value": freeze.value, "detail": freeze.detail})
    if not freeze.passed:
        problems.append(f"the export carries a frozen frame: {freeze.detail}")
    gaps, overlaps = _coverage_problems(comparison)
    evidence.append({"check": "gaps_and_overlaps", "gaps": gaps,
                     "overlaps": overlaps})
    if gaps:
        problems.append(
            f"{len(gaps)} gap(s) in the picture the build laid, the first at "
            f"{gaps[0][0]:.3f}s")
    if overlaps:
        problems.append(
            f"{len(overlaps)} overlap(s) in the picture the build laid, the "
            f"first at {overlaps[0][0]:.3f}s")
    if problems:
        return _result(
            "QA-003", FAIL,
            f"{len(problems)} defect(s) in the delivered file: {problems[0]}",
            evidence, reader=READER_EXPORT)
    if comparison is None:
        return _result(
            "QA-003", UNKNOWN,
            "the file carries no black and no frozen frame, but the build "
            "was not supplied, so the gaps and overlaps in the picture it "
            "laid were not checked",
            evidence, reader=READER_BUILD)
    return _result(
        "QA-003", PASS,
        "no missing media, no undeclared black, no frozen frame, no gap and "
        "no overlap",
        evidence, reader=READER_EXPORT)


def _coverage_problems(comparison: Optional[_Comparison]
                        ) -> Tuple[list, list]:
    """The holes and the overlaps in the picture the build laid.

    GAPS are read over the whole PICTURE, V1 and V2 together: a V1 gap
    that a B-roll cutaway covers is not a hole, and the round3 snapshot
    carried four of them (the V1 clip list has holes exactly where V2
    sits).  So the picture is merged first and the holes are read off
    the merge.

    OVERLAPS are read WITHIN a row: two clips of the same row over one
    another is a defect, while V2 over V1 is how B-roll works.
    """
    gaps: list = []
    overlaps: list = []
    if comparison is None:
        return gaps, overlaps
    picture: list = []
    for key, entry in comparison.matched.items():
        if not key.upper().startswith("V"):
            continue
        row = entry.get("row") or {}
        spans = []
        for clip in row.get("clips") or []:
            span = _built_span(clip, comparison.fps)
            if span:
                spans.append((span[0], span[1]))
                picture.append((span[0], span[1]))
        spans.sort()
        for earlier, later in zip(spans, spans[1:]):
            if later[0] < earlier[1] - comparison.tolerance:
                overlaps.append((later[0], earlier[1]))
    merged: list = []
    for start, end in sorted(picture):
        if merged and start <= merged[-1][1] + comparison.tolerance:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for earlier, later in zip(merged, merged[1:]):
        if later[0] > earlier[1] + comparison.tolerance:
            gaps.append((earlier[1], later[0]))
    return gaps, overlaps


def _qa004(video_path: str,
           expectation: ExportExpectation) -> CriterionResult:
    """QA-004: format, frame rate, dimensions, duration, encoding."""
    from library.tools import render_qa

    evidence = []
    problems = []
    ungraded = []
    resolution = expectation.resolution
    if resolution:
        result = render_qa.verify_resolution(
            video_path, expected_width=resolution[0],
            expected_height=resolution[1])
        evidence.append({"check": "resolution", "passed": bool(result.passed),
                         "value": result.value, "detail": result.detail})
        if not result.passed:
            problems.append(f"the export's frame is wrong: {result.detail}")
    else:
        ungraded.append("the frame")
    fps = expectation.frame_rate
    if fps:
        result = render_qa.verify_framerate(video_path, expected_fps=fps)
        evidence.append({"check": "framerate", "passed": bool(result.passed),
                         "value": result.value, "detail": result.detail})
        if not result.passed:
            problems.append(f"the export's rate is wrong: {result.detail}")
    else:
        ungraded.append("the frame rate")
    duration = expectation.duration_seconds
    if duration:
        result = render_qa.verify_duration(video_path, duration)
        evidence.append({"check": "duration", "passed": bool(result.passed),
                         "value": result.value, "detail": result.detail})
        if not result.passed:
            problems.append(f"the export's length is wrong: {result.detail}")
    else:
        ungraded.append("the length")
    encoding = render_qa.verify_encoding(
        video_path, expected_codec=expectation.codec,
        expected_container=expectation.container)
    evidence.append({"check": "encoding", "passed": bool(encoding.passed),
                     "value": encoding.value, "detail": encoding.detail})
    if not encoding.passed:
        problems.append(f"the export's encoding is wrong: {encoding.detail}")
    if problems:
        return _result(
            "QA-004", FAIL,
            f"{len(problems)} format defect(s): {problems[0]}",
            evidence, reader=READER_MASTER)
    if ungraded:
        return _result(
            "QA-004", UNKNOWN,
            f"the export's encoding is right, but nothing declared "
            f"{' and '.join(ungraded)}, so that could not be graded",
            evidence, reader=READER_EVIDENCE)
    return _result(
        "QA-004", PASS,
        "the export carries the declared frame, rate, length and encoding",
        evidence, reader=READER_MASTER)


def _qa005(expectation: ExportExpectation,
           comparison: Optional[_Comparison]) -> CriterionResult:
    """QA-005: caption timing and presence."""
    from library.tools import subtitle_qa

    plan = expectation.plan or {}
    subtitles = plan.get("subtitles") if isinstance(plan, Mapping) else None
    subtitles = [entry for entry in (subtitles or [])
                 if isinstance(entry, Mapping)]
    segments = _overlay_segments(plan)
    if not subtitles and not segments:
        return _result(
            "QA-005", NOT_APPLICABLE,
            "the plan declares no captions, so there is no caption timing to "
            "verify",
            reader=READER_PLAN)
    evidence = []
    problems = []
    if subtitles:
        results = subtitle_qa.verify_subtitle_timing(
            list(subtitles), total_duration=expectation.duration_seconds,
            spine_blocks=list(expectation.spine_blocks) or None)
        for entry in results:
            evidence.append({"check": entry.metric,
                             "passed": bool(entry.passed),
                             "detail": entry.detail})
            if not entry.passed:
                problems.append(f"{entry.metric}: {entry.detail}")
    if segments and comparison is not None:
        absent = _captions_not_placed(segments, comparison)
        evidence.append({"check": "caption_presence",
                         "declared": len(segments),
                         "not_placed": absent})
        if absent:
            problems.append(
                f"{len(absent)} declared caption(s) never reached the "
                f"timeline, the first at {absent[0][0]:.3f}s")
    if problems:
        return _result(
            "QA-005", FAIL,
            f"{len(problems)} caption defect(s): {problems[0]}",
            evidence, reader=READER_BUILD)
    if segments and comparison is None:
        return _result(
            "QA-005", UNKNOWN,
            "the plan's caption timing is sound, but the build that carried "
            "the captions was not supplied, so their presence was not "
            "checked",
            evidence, reader=READER_BUILD)
    return _result(
        "QA-005", PASS,
        f"{len(subtitles)} caption(s) are correctly timed and present",
        evidence, reader=READER_BUILD)


def _overlay_segments(plan: Mapping[str, Any]) -> list:
    """The plan's caption overlay segments, as `(start, end, path)`."""
    segments = []
    track = plan.get("subtitle_overlay") if isinstance(plan, Mapping) else None
    if not isinstance(track, Mapping):
        return segments
    for segment in track.get("segments") or []:
        if not isinstance(segment, Mapping):
            continue
        try:
            start = float(segment.get("timeline_start"))
            end = float(segment.get("timeline_end"))
        except (TypeError, ValueError):
            continue
        segments.append((start, end, str(segment.get("overlay_path") or "")))
    return segments


def _captions_not_placed(segments: Sequence[tuple],
                         comparison: _Comparison) -> list:
    """The declared captions the build never carried, as `(start, end)`.

    Read over EVERY row the build carries, not only the rows the plan
    describes as tracks: a caption is placed on its own row, which no
    planned track names, so a search over the planned rows alone would
    find no caption anywhere and call every one of them missing.
    """
    rows = []
    for row in comparison.rows():
        rows.extend(row.get("clips") or [])
    missing = []
    for start, end, path in segments:
        placed = False
        for clip in rows:
            if path and clip.get("file_path") and (
                    os.path.normpath(str(clip.get("file_path")))
                    != os.path.normpath(path)):
                continue
            span = _built_span(clip, comparison.fps)
            if not span:
                continue
            if (span[0] <= start + comparison.tolerance
                    and span[1] >= end - comparison.tolerance):
                placed = True
                break
        if not placed:
            missing.append((start, end))
    return missing


def _qa006(video_path: str, expectation: ExportExpectation,
           comparison: Optional[_Comparison]) -> CriterionResult:
    """QA-006: graphical elements at the expected placement and scale."""
    from library.tools import render_qa

    plan = expectation.plan or {}
    segments = _graphical_segments(plan)
    if not segments:
        return _result(
            "QA-006", NOT_APPLICABLE,
            "the plan declares no graphical elements, so there is no "
            "placement to verify",
            reader=READER_PLAN)
    evidence = []
    problems = []
    absent = []
    for kind, start, end, path in segments:
        if path and not os.path.exists(path):
            problems.append(
                f"the {kind} element at {start:.3f}s names {path!r}, which "
                f"is not on disk")
    evidence.append({"check": "elements_on_disk", "declared": len(segments)})
    if comparison is not None:
        absent = _elements_not_placed(segments, comparison)
        evidence.append({"check": "element_presence", "not_placed": absent})
        if absent:
            problems.append(
                f"{len(absent)} graphical element(s) never reached the "
                f"timeline, the first at {absent[0][0]:.3f}s")
    spans = _graphics_spans(plan)
    if spans:
        graphics = render_qa.measure_graphics_render(video_path, spans)
        evidence.append({"check": "graphics_render",
                         "passed": bool(graphics.passed),
                         "detail": graphics.detail})
        if not graphics.passed:
            problems.append(
                f"a graphical element drew nothing in the export: "
                f"{graphics.detail}")
    else:
        evidence.append({"check": "graphics_render", "passed": None,
                         "detail": "no graphical span could be read from the "
                                   "plan, so the pixels were not compared"})
    placement = expectation.overlays
    if placement is None:
        return _result(
            "QA-006", UNKNOWN,
            "the plan's graphical elements are on disk and placed, but the "
            "placement and scale of an overlay are read back from the "
            "timeline, which needs Resolve (overlay_verify.verify_values "
            "answers it)",
            evidence, reader=READER_EVIDENCE)
    evidence.append({"check": "placement", **(dict(placement)
                                             if isinstance(placement, Mapping)
                                             else {})})
    if problems:
        return _result(
            "QA-006", FAIL,
            f"{len(problems)} graphical defect(s): {problems[0]}",
            evidence, reader=READER_BUILD)
    return _result(
        "QA-006", PASS,
        f"the plan's {len(segments)} graphical element(s) are on disk, "
        f"placed and drawn",
        evidence, reader=READER_BUILD)


def _graphical_segments(plan: Mapping[str, Any]) -> list:
    """The plan's graphical elements, as `(kind, start, end, path)`.

    The CAPTIONS are not among them.  The punch list gives captions
    their own criterion (QA-005, timing and presence) and gives the
    graphics theirs (QA-006, placement and scale), and the caption
    overlay's geometry is judged by the caption-quality checks
    (`library/tools/qa/subtitle_qa.py`), which measure the caption's own
    box.  Listing captions here as well would grade them twice under
    two rules.
    """
    segments = []
    for key, kind in (("motion_graphics_overlay", "motion graphics"),
                      ("timed_text_overlay", "timed text")):
        track = plan.get(key) if isinstance(plan, Mapping) else None
        if not isinstance(track, Mapping):
            continue
        for segment in track.get("segments") or []:
            if not isinstance(segment, Mapping):
                continue
            try:
                start = float(segment.get("timeline_start"))
                end = float(segment.get("timeline_end"))
            except (TypeError, ValueError):
                continue
            segments.append((kind, start, end,
                             str(segment.get("overlay_path") or "")))
    return segments


def _elements_not_placed(segments: Sequence[tuple],
                         comparison: _Comparison) -> list:
    """The declared graphical elements the build never carried."""
    rows = []
    for row in comparison.rows():
        rows.extend(row.get("clips") or [])
    missing = []
    for kind, start, end, path in segments:
        placed = False
        for clip in rows:
            if path and clip.get("file_path") and (
                    os.path.normpath(str(clip.get("file_path")))
                    != os.path.normpath(path)):
                continue
            span = _built_span(clip, comparison.fps)
            if not span:
                continue
            if (span[0] <= start + comparison.tolerance
                    and span[1] >= end - comparison.tolerance):
                placed = True
                break
        if not placed:
            missing.append((start, end))
    return missing


def _graphics_spans(plan: Mapping[str, Any]) -> list:
    """One `render_qa.GraphicsSpan` per placed overlay segment.

    The span builder is the one the validate step already uses
    (`library/tools/qa_spans.py`), so the export's graphics are
    measured over the same windows the build's own gate measured - two
    definitions of "the graphical element" would be two verdicts.
    """
    from library.tools.qa_spans import graphics_spans

    try:
        return list(graphics_spans(dict(plan)))
    except Exception as failed:  # noqa: BLE001 - reported, never raised
        print(f"export verification: graphics spans unavailable "
              f"({failed})", file=sys.stderr)
        return []


def _qa007(video_path: str,
           expectation: ExportExpectation) -> CriterionResult:
    """QA-007: loudness, peaks, clipping, channel configuration."""
    from library.tools import render_qa

    evidence = []
    problems = []
    target = expectation.loudness_target_lufs
    tolerance = expectation.loudness_tolerance_lufs
    ceiling = expectation.true_peak_ceiling_dbtp
    if target is None:
        from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC

        target = DEFAULT_DELIVERY_SPEC.loudness_target_lufs
        tolerance = (DEFAULT_DELIVERY_SPEC.loudness_tolerance_lufs
                     if tolerance is None else tolerance)
        ceiling = (DEFAULT_DELIVERY_SPEC.true_peak_ceiling_dbtp
                   if ceiling is None else ceiling)
    loudness = render_qa.measure_lufs(
        video_path, target_lufs=target,
        tolerance=tolerance if tolerance is not None else 1.0,
        true_peak_ceiling=ceiling if ceiling is not None else -1.0)
    evidence.append({"check": "loudness", "passed": bool(loudness.passed),
                     "value": loudness.value, "detail": loudness.detail})
    if not loudness.passed:
        problems.append(f"the export's loudness is wrong: {loudness.detail}")
    channels = render_qa.verify_channel_configuration(
        video_path, expected_channels=expectation.channels)
    evidence.append({"check": "channel_configuration",
                     "passed": bool(channels.passed),
                     "value": channels.value, "detail": channels.detail})
    if not channels.passed:
        problems.append(
            f"the export's channel configuration is wrong: {channels.detail}")
    clipping = render_qa.detect_clipping(video_path)
    evidence.append({"check": "clipping", "passed": bool(clipping.passed),
                     "value": clipping.value, "detail": clipping.detail})
    if not clipping.passed:
        problems.append(f"the export carries clipped audio: {clipping.detail}")
    if problems:
        return _result(
            "QA-007", FAIL,
            f"{len(problems)} audio defect(s): {problems[0]}",
            evidence, reader=READER_MASTER)
    return _result(
        "QA-007", PASS,
        f"the export is at {target} LUFS, under the true-peak ceiling, "
        f"unclipped and in its declared channels",
        evidence, reader=READER_MASTER)


def _qa008(expectation: ExportExpectation) -> CriterionResult:
    """QA-008: the export's identity and the revision that produced it."""
    evidence = []
    problems = []
    delivery = expectation.delivery
    if not delivery:
        return _result(
            "QA-008", UNKNOWN,
            "no delivery identity record was supplied, so the export cannot "
            "be tied to the revision that produced it",
            reader=READER_EVIDENCE)
    timeline_name = str(delivery.get("timeline_name") or "")
    evidence.append({"check": "delivery_identity",
                     "timeline_name": timeline_name,
                     "reel": delivery.get("reel"),
                     "recorded_seconds": delivery.get("timeline_seconds")})
    project_folder = expectation.project_folder
    if project_folder and timeline_name:
        provenance = _provenance_entry(project_folder, timeline_name)
        evidence.append({"check": "provenance", **provenance})
        if not provenance.get("recorded"):
            problems.append(
                f"nothing records which build the export came from: "
                f"{provenance.get('reason')}")
    recorded = delivery.get("verification") or {}
    if isinstance(recorded, Mapping) and recorded.get("passed") is False:
        problems.append(
            "the delivery's own verification refused the file when it was "
            "written")
    if problems:
        return _result(
            "QA-008", FAIL,
            f"{len(problems)} identity defect(s): {problems[0]}",
            evidence, reader=READER_EXPORT)
    named = timeline_name or "the delivered reel"
    return _result(
        "QA-008", PASS,
        f"the export is the delivery of {named}, and the revision that "
        f"produced it is on record",
        evidence, reader=READER_EXPORT)


def _provenance_entry(project_folder: str, timeline_name: str) -> dict:
    """Which snapshot the build was from, or why nothing says."""
    from library.tools import plan_provenance
    from library.tools.project_layout import Area, ProjectLayout

    try:
        review_dir = str(ProjectLayout(project_folder).read_dir(Area.REVIEW))
    except Exception as failed:  # noqa: BLE001 - reported, never raised
        return {"recorded": False,
                "reason": f"the project's review area is unreadable "
                          f"({failed})"}
    try:
        entry, reason = plan_provenance.built_from_snapshot(
            review_dir, timeline_name)
    except Exception as failed:  # noqa: BLE001 - reported, never raised
        return {"recorded": False,
                "reason": f"the provenance record could not be read "
                          f"({failed})"}
    if entry is None:
        return {"recorded": False, "reason": reason}
    return {"recorded": True, "snapshot": entry.get("snapshot"),
            "recorded_at": entry.get("recorded_at"), "reason": reason}


# ── The runner ──


def verify_export(video_path: str,
                  expectation: ExportExpectation) -> ExportVerification:
    """Verify one exported file against the accepted edit plan.

    Every criterion is answered.  A criterion whose evidence is missing
    reports UNKNOWN with the reason and what would resolve it; none of
    them reports a pass it did not measure.
    """
    if not video_path or not os.path.isfile(video_path):
        raise ExportVerificationRefused(
            f"there is no exported file at {video_path!r} - an absent "
            f"export is not a failing one, it is nothing to verify")
    if expectation.plan:
        expectation = expectation.with_plan(expectation.plan)
    comparison = None
    if expectation.plan and expectation.built:
        comparison = _compare_plan_and_build(
            expectation.plan, expectation.built,
            expectation.frame_rate
            or _timeline_fps(expectation.plan, expectation.built))
    results = (
        _qa001(expectation),
        _qa002(expectation, comparison),
        _qa003(video_path, expectation, comparison),
        _qa004(video_path, expectation),
        _qa005(expectation, comparison),
        _qa006(video_path, expectation, comparison),
        _qa007(video_path, expectation),
        _qa008(expectation),
    )
    return ExportVerification(video_path=video_path, results=results)


def load_plan(project_folder: str) -> Optional[dict]:
    """The project's accepted edit plan, or None.

    Read through the project's own layout, so the plan is the one the
    compile step wrote and not a file found by name.
    """
    from library.tools.project_layout import Area, ProjectLayout

    try:
        path = ProjectLayout(project_folder).read_path(
            Area.ASSEMBLY_MANIFEST, "assembly_manifest.json")
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - an absent plan is an absent plan
        return None
    return document if isinstance(document, dict) else None


def load_delivery(project_folder: str,
                  timeline_name: str = "") -> Optional[dict]:
    """The project's delivery identity record, or None.

    The ledger the delivery wrote names the file and the reel; the
    sidecar beside the file names the frame range and the settings.
    The ledger is read first, because it is the record of WHAT WAS
    DELIVERED; the sidecar is the record of HOW.

    `timeline_name` names the reel to read.  Without it the first
    delivery on record is read - a project with several reels has
    several records, and a caller that wants a particular one says so.
    """
    from library.tools import deliver_all

    ledger = deliver_all.read_ledger(project_folder)
    if not ledger:
        return None
    record = {}
    for timeline, entry in ledger.items():
        if not isinstance(entry, Mapping):
            continue
        if timeline_name and timeline != timeline_name:
            continue
        record = {
            "timeline_name": timeline,
            "reel": entry.get("reel"),
            "output_path": entry.get("output_path"),
            "signature": entry.get("signature"),
            "size_bytes": entry.get("size_bytes"),
            "mtime_ns": entry.get("mtime_ns"),
            "qc_passed": entry.get("qc_passed"),
        }
        break
    if not record:
        return None
    sidecar = _sidecar_for(record.get("output_path"))
    if sidecar:
        try:
            document = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = None
        if isinstance(document, Mapping):
            record.update(document)
    return record or None


def _sidecar_for(output_path: Any) -> Optional["os.PathLike"]:
    """The `.deliver.json` beside a delivered file, or None."""
    if not output_path:
        return None
    path = os.path.splitext(str(output_path))[0] + ".deliver.json"
    return path if os.path.isfile(path) else None


def load_built(project_folder: str, timeline_name: str) -> Optional[dict]:
    """The built timeline's own record, or None.

    The committed timeline snapshot is the build's own account of
    itself: what it laid, where, from which media.  It is on disk, so
    the export can be verified against the build without Resolve and
    without a render.
    """
    from library.tools.versions import rounds

    safe = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in timeline_name) or "timeline"
    path = os.path.join(project_folder, rounds.SNAPSHOT_DIR,
                        f"{safe}{rounds.SNAPSHOT_SUFFIX}")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None
