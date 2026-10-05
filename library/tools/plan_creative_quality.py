"""Plan creative quality: does the plan serve the creative direction?

The gap this closes (E11)
------------------------
`reel_conformance_verifier` proves the timeline matches the plan.  It
says nothing about whether the plan's OWN choices are good - a plan that
is structurally conformant but creatively weak (a bad shot choice,
pacing that ignores the declared curve, a must-appear moment the plan
drops) passes every structural gate.  This module is the creative-quality
layer above conformance: it judges the PLAN against the creative
direction (step 2.01's intent model), separately from structural
conformance.  The conformance verifier stays structural; this is the
intent-quality seam above it.

Two deterministic checks, both free (no Resolve, no model, no render):

* Key moments landed.  The direction's `key_moments` are "the strongest
  moments from the footage that MUST appear", each grounded in a clip
  and a source span.  A declared key moment no planned moment plays is a
  moment the direction says is essential that the plan cannot deliver -
  the flagship bad-shot-choice defect, and the gap's fidelity test.

* Pacing curve.  The direction's `pacing_curve` declares a `target_asl`
  per beat.  A beat the plan serves whose measured average shot length
  misses the target is pacing that ignores the declared curve.

**This reports and does not fail** (`PLAN_CREATIVE_QUALITY_GATES` is
False).  Whether a creatively weak plan blocks delivery is a pending
captain call, the same ruling every report-only check in this pipeline
carries.  The findings land in their own section of the conformance
report, never folded into the structural gate's error/warning counts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

PLAN_CREATIVE_QUALITY_GATES = False
"""Whether a creatively weak plan fails the build.

The check measures and reports; it does not fail.  Promoting it is this
boolean, and it is a captain's call because whether a dropped key
moment or a missed pacing target is a delivery-blocking defect is a
creative judgement, not a technical one - the same ruling that keeps
`broll_correspondence`, `punch_in_face` and `caption_obscuring`
report-only.
"""

KEY_MOMENT_MISSING = "KEY-MOMENT-MISSING"
"""A declared key moment no planned moment plays."""

PACING_MISSED = "PACING-MISSED"
"""A served beat whose measured average shot length misses the target."""

NO_INTENT_MODEL = "NO-INTENT-MODEL"
"""Anti-vacuity: there is no creative direction to check against."""

WARNING = "warning"
INFO = "info"

PACING_TOLERANCE = 2.0
"""How far the plan's measured ASL may sit from the target before it is
reported.  ASL is a soft creative target, not a hard constraint, so only
a gross miss - the plan's average shot length more than double, or less
than half, the declared target - is reported.  A correct plan is never
flagged for landing near the target it was given."""


@dataclass(frozen=True)
class CreativeQualityFinding:
    """One thing the plan's creative quality has to say."""

    code: str
    message: str
    severity: str = WARNING
    detail: Optional[dict] = None

    def as_dict(self) -> dict:
        d = {"class": self.code, "message": self.message,
             "severity": self.severity}
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class PlanCreativeQualityReport:
    """The plan judged against the creative direction."""

    findings: List[CreativeQualityFinding] = field(default_factory=list)
    key_moments_total: int = 0
    key_moments_covered: int = 0
    beats_total: int = 0
    beats_measured: int = 0
    unmeasured: List[str] = field(default_factory=list)
    """Why a part of the check could not run - a check with nothing to
    compare against REFUSES rather than passing quietly (AGENTS.md 10.4).
    """

    @property
    def key_moments_missing(self) -> int:
        return self.key_moments_total - self.key_moments_covered

    @property
    def creative_findings(self) -> List[CreativeQualityFinding]:
        """The findings that name a creative defect (not unmeasured)."""
        return [f for f in self.findings
                if f.severity == WARNING]

    def as_dict(self) -> dict:
        return {
            "gates": PLAN_CREATIVE_QUALITY_GATES,
            "key_moments": {
                "total": self.key_moments_total,
                "covered": self.key_moments_covered,
                "missing": self.key_moments_missing,
            },
            "beats": {
                "total": self.beats_total,
                "measured": self.beats_measured,
            },
            "unmeasured": list(self.unmeasured),
            "findings": [f.as_dict() for f in self.findings],
        }


# ── The clip join ─────────────────────────────────────────────────────


def _clip_source_map(catalog: Sequence[dict]) -> Dict[str, str]:
    """`{clip_id: source_file}` from the catalog.

    The intent model grounds its key moments and beats in a `clip_id`;
    the plan's moments carry `source_file`.  The catalog is the one place
    both names meet, so the join goes through it - the same join
    `footage_segments.load_vision_profiles` makes between the catalog
    and the per-clip documents.
    """
    out: Dict[str, str] = {}
    for entry in catalog or []:
        if not isinstance(entry, dict):
            continue
        clip_id = entry.get("clip_id")
        source = entry.get("source_file") or entry.get("path") or ""
        if clip_id and source:
            out[str(clip_id)] = str(source)
    return out


def _same_source(clip_source: str, span_source: str) -> bool:
    """Whether a plan source_span's file is the catalog clip's file.

    Compared on the absolute path first and the basename as a fallback,
    because the catalog records the absolute path while a transcript
    segment's `source_file` has been through the footage-identity
    normalization - the two spellings of one file are the same footage.
    """
    if not clip_source or not span_source:
        return False
    if os.path.abspath(clip_source) == os.path.abspath(span_source):
        return True
    return (os.path.basename(clip_source)
            == os.path.basename(span_source))


def _overlaps(a_start: float, a_end: float,
              b_start: float, b_end: float) -> bool:
    """Whether two half-open source ranges share any second."""
    return a_start < b_end and b_start < a_end


def _moment_source_spans(moment) -> List[dict]:
    """A plan moment's source spans, as dicts, whatever its shape."""
    spans = getattr(moment, "source_spans", None)
    if spans is None and isinstance(moment, dict):
        spans = moment.get("source_spans")
    out: List[dict] = []
    for span in spans or ():
        if isinstance(span, dict):
            out.append(span)
    return out


# ── Check 1: key moments landed ───────────────────────────────────────


def _check_key_moments(moments: Sequence, intent_model: Optional[dict],
                       clip_map: Dict[str, str],
                       unmeasured: List[str]) -> Tuple[List[CreativeQualityFinding], int, int]:
    """Every declared key moment the plan actually plays.

    A key moment is grounded in a clip and a source span.  It is covered
    when at least one planned moment plays footage that overlaps that
    span on that clip - the same overlap test `intent_model.moments_in_beat`
    uses to link a moment to the beat it belongs to.  A declared key
    moment nothing plays is a moment the direction says MUST appear that
    the plan cannot deliver: the flagship bad-shot-choice defect.
    """
    from library.tools import intent_model as im

    findings: List[CreativeQualityFinding] = []
    total = 0
    covered = 0
    for moment in im.key_moments(intent_model):
        total += 1
        moment_id = moment.get("moment_id") or "<no moment_id>"
        description = moment.get("description") or ""
        span = moment.get("span") or {}
        clip_id = span.get("clip_id")
        start, end = span.get("source_start"), span.get("source_end")
        if not clip_id or start is None or end is None:
            unmeasured.append(
                f"key moment {moment_id!r} names no grounded source span, "
                f"so whether the plan plays it cannot be checked")
            continue
        clip_source = clip_map.get(str(clip_id))
        if clip_source is None:
            unmeasured.append(
                f"key moment {moment_id!r} is grounded in clip "
                f"{clip_id!r}, which the catalog does not carry")
            continue
        plays = any(
            _same_source(clip_source, str(s.get("source_file") or ""))
            and _overlaps(float(start), float(end),
                          float(s.get("source_start") or 0.0),
                          float(s.get("source_end") or 0.0))
            for m in moments
            for s in _moment_source_spans(m)
        )
        if plays:
            covered += 1
            continue
        findings.append(CreativeQualityFinding(
            code=KEY_MOMENT_MISSING,
            message=(
                f"key moment {moment_id!r} ({description!r}) MUST appear, "
                f"but no planned moment plays its footage "
                f"({clip_id}, {float(start):.1f}-{float(end):.1f}s) - the "
                f"plan cannot deliver a moment the direction declares "
                f"essential"),
            detail={
                "moment_id": moment_id,
                "description": description,
                "clip_id": clip_id,
                "source_start": round(float(start), 2),
                "source_end": round(float(end), 2),
            },
        ))
    return findings, total, covered


# ── Check 2: pacing curve ────────────────────────────────────────────


def _beat_asl(moments: Sequence, clip_source: str,
              span: dict) -> Optional[float]:
    """The plan's measured average shot length for one beat, or None.

    A beat is served by the planned moments whose source spans overlap
    the beat's span on the beat's clip.  The plan's ASL for the beat is
    the average of those moments' source seconds on that clip - the
    footage the plan actually plays from the beat, which is the shot
    length the reel will land at.  None when the plan serves the beat
    with no measurable source seconds.
    """
    start = span.get("source_start")
    end = span.get("source_end")
    if start is None or end is None:
        return None
    start, end = float(start), float(end)
    durations: List[float] = []
    for m in moments:
        played = 0.0
        for s in _moment_source_spans(m):
            if not _same_source(clip_source,
                                str(s.get("source_file") or "")):
                continue
            s_start = s.get("source_start")
            s_end = s.get("source_end")
            if s_start is None or s_end is None:
                continue
            lo = max(start, float(s_start))
            hi = min(end, float(s_end))
            if hi > lo:
                played += hi - lo
        if played > 0.0:
            durations.append(played)
    if not durations:
        return None
    return sum(durations) / len(durations)


def _check_pacing(moments: Sequence, intent_model: Optional[dict],
                  clip_map: Dict[str, str],
                  unmeasured: List[str]) -> Tuple[List[CreativeQualityFinding], int, int]:
    """Every beat in the pacing curve the plan serves on target.

    A pacing entry declares the `target_asl` its beat should land at.
    The plan's measured ASL for a served beat that misses the target by
    more than `PACING_TOLERANCE` is pacing that ignores the declared
    curve.  A beat the plan does not serve at all is skipped, not
    flagged: a reel is an excerpt, and a beat no planned moment draws on
    is a beat this reel does not cover rather than a beat it paced
    wrongly.
    """
    from library.tools import intent_model as im

    findings: List[CreativeQualityFinding] = []
    total = 0
    measured = 0
    for entry in im.pacing_curve(intent_model):
        beat_id = entry.get("beat_id") or "<no beat_id>"
        target = entry.get("target_asl")
        if target is None:
            continue
        total += 1
        beat = im.beat(intent_model, beat_id)
        span = beat.get("span") or {}
        clip_id = span.get("clip_id")
        if not clip_id:
            unmeasured.append(
                f"pacing entry {beat_id!r} names a beat with no span, so "
                f"the plan's ASL for it cannot be measured")
            continue
        clip_source = clip_map.get(str(clip_id))
        if clip_source is None:
            unmeasured.append(
                f"pacing entry {beat_id!r} is grounded in clip "
                f"{clip_id!r}, which the catalog does not carry")
            continue
        plan_asl = _beat_asl(moments, clip_source, span)
        if plan_asl is None:
            continue
        measured += 1
        target = float(target)
        if (plan_asl > PACING_TOLERANCE * target
                or plan_asl < target / PACING_TOLERANCE):
            findings.append(CreativeQualityFinding(
                code=PACING_MISSED,
                message=(
                    f"beat {beat_id!r} declares target_asl {target:.1f}s "
                    f"but the plan's moments average {plan_asl:.1f}s - "
                    f"pacing that ignores the declared curve"),
                detail={
                    "beat_id": beat_id,
                    "target_asl": round(target, 2),
                    "plan_asl": round(plan_asl, 2),
                    "tolerance": PACING_TOLERANCE,
                },
            ))
    return findings, total, measured


# ── The verifier ─────────────────────────────────────────────────────


def verify_plan_creative_quality(
        moments: Sequence,
        intent_model: Optional[dict],
        catalog: Sequence[dict] = ()) -> PlanCreativeQualityReport:
    """Judge the plan against the creative direction.

    `moments` is the plan - every `reel_proposal.ReelMoment` the proposal
    carries, across all reels.  `intent_model` is step 2.01's structured
    intent model (its `key_moments` and `pacing_curve` are what these
    checks read).  `catalog` is the clip catalog, the join between the
    model's `clip_id` grounding and the plan's `source_file` spans.

    **This reports and does not fail** (`PLAN_CREATIVE_QUALITY_GATES` is
    False).  A plan with no intent model to check against is reported as
    unmeasured, never as a creative pass: a check with nothing to compare
    against REFUSES rather than passing quietly (AGENTS.md 10.4).
    """
    unmeasured: List[str] = []
    report = PlanCreativeQualityReport()

    if not intent_model:
        report.findings.append(CreativeQualityFinding(
            code=NO_INTENT_MODEL,
            message=(
                "no intent model is on file, so the plan's creative "
                "quality cannot be judged against the creative direction "
                "- run step 2.01 (creative_direction)"),
            severity=INFO,
        ))
        report.unmeasured.append(
            "the whole check: no intent model to read a direction from")
        return report

    clip_map = _clip_source_map(catalog)

    key_findings, key_total, key_covered = _check_key_moments(
        moments, intent_model, clip_map, unmeasured)
    report.findings.extend(key_findings)
    report.key_moments_total = key_total
    report.key_moments_covered = key_covered

    pace_findings, beats_total, beats_measured = _check_pacing(
        moments, intent_model, clip_map, unmeasured)
    report.findings.extend(pace_findings)
    report.beats_total = beats_total
    report.beats_measured = beats_measured

    report.unmeasured.extend(unmeasured)
    return report


# ── Loading the inputs off a project ─────────────────────────────────


def load_intent_model(project_folder: str) -> Optional[dict]:
    """Step 2.01's intent model, or None when the project has none.

    Read through `capability_outputs` off `pipeline_data.json` - the one
    place a step's output is guaranteed to have landed (AGENTS.md 10.1) -
    the same reader `_catalog_source_sizes` uses for the catalog.
    """
    if not project_folder:
        return None
    path = os.path.join(project_folder, "pipeline_data.json")
    try:
        import json
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None
    from library.tools import capability_outputs
    model = capability_outputs.value(
        state, "step_2_01_creative_direction", "intent_model")
    return model if isinstance(model, dict) else None


def load_catalog(project_folder: str) -> List[dict]:
    """The clip catalog, or empty when the project has none."""
    if not project_folder:
        return []
    path = os.path.join(project_folder, "pipeline_data.json")
    try:
        import json
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return []
    from library.tools import capability_outputs
    catalog = capability_outputs.value(
        state, "footage.catalog", "clip_catalog") or []
    return [c for c in catalog if isinstance(c, dict)]


def verify_project_plan(project_folder: str,
                        moments: Sequence) -> PlanCreativeQualityReport:
    """Judge a project's plan against its creative direction.

    Reads the intent model and the catalog off the project, then runs the
    check.  Separated from `verify_plan_creative_quality` so the check
    itself is testable against plain data with no project on disk.
    """
    return verify_plan_creative_quality(
        moments,
        load_intent_model(project_folder),
        load_catalog(project_folder))
