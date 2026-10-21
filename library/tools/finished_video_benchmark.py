"""QA-010: the finished-video acceptance benchmark.

WHAT WAS MISSING
----------------
The editorial acceptance harness (`library/tools/editorial_acceptance/`)
scores a run of briefs against what the request asked for, and it reports
five stages on five denominators.  Four of them had a denominator.  The
fifth - `final_quality`, "does the delivered FILE satisfy the brief, and
pass its technical checks?" - did not: every task record in the committed
report carries `not_measured` for it, because a record without a
delivered file and an assessment has nothing to measure.

So the headline the punch list names as missing was true of the harness
as it stood: **final quality had no measured denominator**.  A benchmark
that stops at the plan measures the engine's reasoning about a video it
never watched.

WHAT THIS IS
------------
A benchmark over FINISHED VIDEOS.  Its unit is a delivered file that
somebody asked for, and it measures two halves of that file and keeps
them apart:

- the MECHANICAL half - the engine's own gates on the delivered file
  (QA-002 picture, QA-003 captions, QA-004 audio), read through
  `editorial_acceptance.judgement.objective_legs`, so the benchmark
  cites a measurement rather than inventing one;
- the CREATIVE half - the brief's own acceptance criteria, judged by a
  reviewer who did not see the plan (`editorial_brief_scorer`), so a
  video is judged against what it was FOR and not against what it was
  built from.

The two halves are NEVER COMBINED.  They are not the same unit - one is
a measurement and one is a reading - and an average over them is exactly
the defect this exists to prevent: a file that failed its audio spec
reads as a seven-out-of-eight edit.  So the report carries two
denominators, side by side, and no third number.

**An unmeasured half is never a zero.**  A leg that could not run is
`not_measured`, with the reason, and it is counted as neither a pass
nor a failure.  A benchmark that reports a zero for a measurement nobody
made is lying about its own denominator.

THE FOUR TRACKED NUMBERS
-------------------------
The punch list asks for four numbers the harness has no place for:

- **first-pass acceptance** - how many finished videos were accepted
  without a revision;
- **revision count** - how many revisions the accepted ones took;
- **human correction time** - how long a person spent correcting them;
- **preserved work** - how much of what was already approved survived.

Each is measured over its OWN denominator - the videos that recorded it -
and a video that recorded nothing is `not_measured` for that number,
never a zero and never a failure.

THE RELEASE GATE
-----------------
The punch list's quality release gate: *"At least one representative set
of end-to-end editorial tasks has completed, delivered, independently
reviewed videos.  No major technical failure can be hidden by an
otherwise acceptable subjective score."*

So the gate is two things, and both are computed rather than asserted:
the corpus has to hold a representative set of FINISHED videos, and no
video may carry a technical failure behind an acceptable creative score.
The second is not a rule about the numbers - it is a rule about the
READING of the two halves, and it is checked per video, because a
corpus-level average is exactly the hiding place the punch list names.

WHAT THIS IS NOT
----------------
It does not drive the work, it does not touch Resolve, and it does not
judge: the reviewer is supplied - either recorded in the corpus, or a
callable the caller names - and the benchmark only records what it said.
`library/tools/editorial_acceptance/record.py` is the record shape a
finished video's run leaves behind; this module reads the same shape
and adds the four tracked numbers and the gate.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools import acceptance_state, report_provenance
from library.tools.editorial_acceptance import briefs, delivery, judgement

CORPUS_SCHEMA = "ren-finished-video-corpus/1"
REPORT_SCHEMA = "ren-finished-video-benchmark/1"

#: The legs a FINISHED VIDEO can answer.  The acceptance gate
#: (`library/tools/acceptance_state.py`) knows eight legs; four of them
#: are about the WRITES and the TIMELINE a video was built from, and a
#: benchmark over delivered files cannot speak for those.  The four
#: below are the ones a delivered file answers for itself.
FINISHED_VIDEO_LEGS: tuple[str, ...] = (
    "delivered_audio",
    "delivered_picture",
    "delivered_captions",
    "editorial_review",
)

#: Which of the engine's own objective legs answers which acceptance
#: leg.  The review contract measures three legs on the delivered file
#: (QA-006); the acceptance gate names the same three measurements
#: under its own names (QA-002, QA-003, QA-004).
OBJECTIVE_LEG_TO_ACCEPTANCE_LEG: dict[str, str] = {
    "audio_continuity": "delivered_audio",
    "shot_quality": "delivered_picture",
    "caption_and_graphic_clarity": "delivered_captions",
}

#: The number of finished videos a representative set holds.  The
#: punch list asks for "at least one representative set of end-to-end
#: editorial tasks"; one video is a task, not a set, and a set of one
#: has no spread to measure.  Three is the smallest set that can carry
#: a denominator worth reading, and it is named here rather than
#: buried in a comparison.
MIN_REPRESENTATIVE_VIDEOS = 3

#: The states a finished video's delivery can be in.
DELIVERED = "delivered"
MISSING = "missing"
UNREADABLE = "unreadable"


class CorpusError(ValueError):
    """The corpus is not a corpus this benchmark can read."""


# ── the corpus ────────────────────────────────────────────────────────────

def validate_corpus(corpus: Any) -> list[str]:
    """Every way the corpus breaks its contract, named.

    An empty list means it holds.  The rules are about what a corpus
    has to SAY, not about what it may leave out: a video with no
    recorded history is a video whose history is `not_measured`, and
    that is a measurement, not a defect.
    """
    problems: list[str] = []
    if not isinstance(corpus, Mapping):
        return ["the corpus is not an object"]
    if corpus.get("schema") != CORPUS_SCHEMA:
        problems.append(f"schema must be {CORPUS_SCHEMA!r}, not "
                        f"{corpus.get('schema')!r}")
    videos = corpus.get("videos")
    if not isinstance(videos, list) or not videos:
        problems.append("the corpus holds no videos")
        videos = []
    seen: set[str] = set()
    for index, entry in enumerate(videos):
        where = f"video {index}"
        if not isinstance(entry, Mapping):
            problems.append(f"{where} is not an object")
            continue
        video_id = str(entry.get("video_id") or "").strip()
        if not video_id:
            problems.append(f"{where} names no video_id")
        elif video_id in seen:
            problems.append(f"two videos share the id {video_id!r}")
        else:
            seen.add(video_id)
        if not str(entry.get("path") or "").strip():
            problems.append(f"{where} names no path")
        brief = entry.get("brief")
        brief_id = str(entry.get("brief_id") or "").strip()
        if brief is None and not brief_id:
            problems.append(f"{where} carries neither a brief nor a "
                            "brief_id")
        if brief is not None:
            if not isinstance(brief, Mapping):
                problems.append(f"{where}'s brief is not an object")
            else:
                problems.extend(f"{where}: {problem}"
                                for problem in _validate_brief(brief))
        problems.extend(f"{where}: {problem}"
                        for problem in _validate_history(
                            entry.get("history")))
    return problems


def _validate_brief(brief: Mapping[str, Any]) -> list[str]:
    """The brief's PUBLIC half, checked.

    The brief a reviewer is shown is the public half and nothing else -
    `editorial_acceptance.judgement.assert_independent` refuses a brief
    that carries the answer key, and the same rule holds here.
    """
    problems: list[str] = []
    if not str(brief.get("instruction") or "").strip():
        problems.append("the brief names no instruction")
    criteria = brief.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        problems.append("the brief declares no acceptance criteria")
    else:
        ids = []
        for criterion in criteria:
            if not isinstance(criterion, Mapping):
                problems.append("an acceptance criterion is not an object")
                continue
            criterion_id = str(criterion.get("id") or "").strip()
            if not criterion_id:
                problems.append("an acceptance criterion names no id")
            elif criterion_id in ids:
                problems.append(f"two acceptance criteria share the id "
                                f"{criterion_id!r}")
            else:
                ids.append(criterion_id)
            if not str(criterion.get("statement") or "").strip():
                problems.append(f"the {criterion_id!r} criterion states "
                                "nothing")
    offending = sorted(key for key in briefs.ORACLE_KEYS if key in brief)
    if "oracle" in brief:
        offending.append("oracle")
    if offending:
        problems.append("the brief carries the answer key: "
                        + ", ".join(offending))
    return problems


def _validate_history(history: Any) -> list[str]:
    """The recorded history, checked.  Absent history is not a defect."""
    if history is None:
        return []
    if not isinstance(history, Mapping):
        return ["the history is not an object"]
    problems: list[str] = []
    attempts = history.get("attempts")
    if attempts is not None and (
            not isinstance(attempts, int) or isinstance(attempts, bool)
            or attempts < 1):
        problems.append("the history's attempts is not a count of at "
                        "least one")
    first = history.get("first_attempt_accepted")
    if first is not None and not isinstance(first, bool):
        problems.append("the history's first_attempt_accepted is not a "
                        "yes or a no")
    seconds = history.get("human_correction_seconds")
    if seconds is not None and (
            not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
            or seconds < 0):
        problems.append("the history's human_correction_seconds is not a "
                        "number of seconds")
    preserved = history.get("preserved_work")
    if preserved is not None:
        if not isinstance(preserved, Mapping):
            problems.append("the history's preserved_work is not an object")
        else:
            checks = preserved.get("checks")
            if not isinstance(checks, Mapping) or not checks:
                problems.append("the history's preserved_work names no check")
            else:
                for name, value in checks.items():
                    if not isinstance(value, bool):
                        problems.append(f"the preserved-work check {name!r} "
                                        "is not a yes or a no")
    return problems


def load_corpus(path: str | Path) -> dict[str, Any]:
    """Read a corpus from disk, and refuse one that breaks its contract."""
    target = Path(path)
    raw = json.loads(target.read_text(encoding="utf-8"))
    problems = validate_corpus(raw)
    if problems:
        raise CorpusError(
            f"{target} is not a finished-video corpus this benchmark can "
            "read:\n  " + "\n  ".join(problems))
    return dict(raw)


# ── scoring one finished video ────────────────────────────────────────────

def _caption_intents(entry: Mapping[str, Any]) -> list[Any] | None:
    """The caption intents a corpus entry declares, or None."""
    declared = entry.get("caption_intents")
    if not declared:
        return None
    from library.tools.delivered_caption_spec import CaptionIntent

    intents = []
    for row in declared:
        intents.append(CaptionIntent(
            label=str(row.get("label") or ""),
            text=str(row.get("text") or ""),
            start=float(row.get("start") or 0.0),
            end=float(row.get("end") or 0.0),
            box=tuple(row["box"]) if row.get("box") else None,
            lines=row.get("lines"),
            ink_height=row.get("ink_height"),
            emphasis=tuple(tuple(item)
                           for item in row.get("emphasis") or ()),
        ))
    return intents


def _spans(entry: Mapping[str, Any], key: str) -> list[tuple[float, float]]:
    """The declared spans a corpus entry carries, read and checked."""
    spans: list[tuple[float, float]] = []
    for item in entry.get(key) or []:
        try:
            start, end = (float(part) for part in str(item).split(":"))
        except ValueError as exc:
            raise CorpusError(
                f"the declared {key} span {item!r} is not start:end "
                "seconds") from exc
        if end <= start:
            raise CorpusError(
                f"the declared {key} span {item!r} does not run forwards")
        spans.append((start, end))
    return spans


def _recorded_judge(entry: Mapping[str, Any]) -> Callable | None:
    """The reviewer the corpus carries, as a judge the scorer can call.

    The recorded rows are the reviewer's WORK: what it saw in the
    delivered file and what it concluded.  The counts, the score and the
    verdict are the scorer's work and are computed from those rows, so
    a corpus cannot hand the benchmark a score it made up.
    """
    rows = entry.get("judgments")
    if not isinstance(rows, list) or not rows:
        return None
    reviewer = str(entry.get("reviewer") or "").strip()
    if not reviewer:
        raise CorpusError(
            f"the video {entry.get('video_id')!r} carries judgments and "
            "names no reviewer")
    recorded = [dict(row) for row in rows if isinstance(row, Mapping)]

    def _judge(_brief: Mapping[str, Any],
               _render: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"judge": reviewer, "criteria": recorded}

    return _judge


def _independence(entry: Mapping[str, Any]) -> dict[str, Any]:
    """What the corpus says about the reviewer's independence.

    The punch list asks for an INDEPENDENT assessment of the creative
    quality, so the corpus has to say who judged and on what they were
    shown.  A reviewer who read the plan is not independent, and the
    benchmark says so rather than counting the assessment.
    """
    reviewer = str(entry.get("reviewer") or "").strip()
    saw_the_plan = entry.get("saw_the_plan")
    return {
        "reviewer": reviewer or None,
        "saw_the_plan": saw_the_plan,
        "independent": bool(reviewer) and saw_the_plan is False,
        "reason": (
            "the reviewer is named and saw only the delivered file"
            if reviewer and saw_the_plan is False else
            "the corpus does not say who judged, or says the judge saw "
            "the plan: the assessment is not independent"
            if not reviewer or saw_the_plan is None else
            "the reviewer saw the plan that produced the file, so the "
            "reading is not independent"),
    }


def resolve_brief(entry: Mapping[str, Any],
                  brief_set: Mapping[str, Any] | None = None
                  ) -> dict[str, Any]:
    """The brief a finished video is judged against.

    The brief is the PUBLIC half - what the reviewer was shown - and it
    comes either from the corpus itself or from the acceptance brief
    set by its id.  The answer key never comes with it: a reviewer who
    can see the expected operations is not reading the video, they are
    recognising the answer.
    """
    inline = entry.get("brief")
    if isinstance(inline, Mapping):
        return briefs.public_view(inline) if "oracle" in inline else dict(
            inline)
    brief_id = str(entry.get("brief_id") or "").strip()
    if not brief_id:
        raise CorpusError(
            f"the video {entry.get('video_id')!r} names no brief")
    if brief_set is None:
        brief_set = briefs.load_brief_set(None)
    brief = briefs.brief(brief_set, brief_id)
    return briefs.public_view(brief)


def score_video(entry: Mapping[str, Any], *,
                judge: Callable | None = None,
                brief_set: Mapping[str, Any] | None = None,
                align_transcript: bool = True,
                workdir: str | Path | None = None) -> dict[str, Any]:
    """One finished video, measured on both halves.

    The two halves are returned SEPARATELY and are never averaged.  The
    per-video OUTCOME is the acceptance gate's own reading (QA-008):
    a technical failure is a failure whatever the creative score says,
    and a leg nobody measured leaves the video unmeasured rather than
    accepted.
    """
    from library.tools import delivery_spec

    video_id = str(entry.get("video_id") or "")
    path = str(entry.get("path") or "")
    brief = resolve_brief(entry, brief_set)
    platform = str(entry.get("platform") or "")
    spec = delivery_spec.resolve_delivery_spec(platform)
    independence = _independence(entry)

    row: dict[str, Any] = {
        "video_id": video_id,
        "path": path,
        "platform": platform,
        "brief_id": str(brief.get("id") or video_id),
        "independence": independence,
        "delivery_status": MISSING,
        "technical": {"status": "not_measured", "legs": [],
                      "reason": "the file was not measured"},
        "creative": {"status": "not_measured", "criteria": [],
                     "reason": "the file was not measured"},
        "brief_criteria": [str(criterion.get("id"))
                            for criterion in (brief.get("acceptance_criteria")
                                              or [])
                            if isinstance(criterion, Mapping)],
        "outcome": acceptance_state.OUTCOME_UNMEASURED,
        "outcome_reason": "",
        "technical_failure_hidden": False,
        "history": dict(entry.get("history") or {}),
        "sources": dict(entry.get("sources") or {}),
    }

    target = Path(path)
    if not target.is_file():
        row["technical"]["reason"] = f"there is no delivered file at {path}"
        row["creative"]["reason"] = f"there is no delivered file at {path}"
        row["outcome_reason"] = (f"there is no delivered file at {path}, so "
                                  "nothing about this video was measured")
        return row

    caption_intents = _caption_intents(entry)
    declared_silence = _spans(entry, "declared_silence")
    declared_black = _spans(entry, "declared_black")
    expected_duration = entry.get("expected_duration_seconds")

    try:
        render = delivery.build_render_evidence(
            target, workdir=workdir, align_transcript=align_transcript,
            expected_resolution=(spec.width, spec.height),
            expected_fps=spec.fps,
            expected_duration=expected_duration,
            declared_black_spans=list(declared_black),
            declared_silence_spans=list(declared_silence),
            target_lufs=spec.loudness_target_lufs,
            lufs_tolerance=spec.loudness_tolerance_lufs,
            true_peak_ceiling=spec.true_peak_ceiling_dbtp)
    except delivery.DeliveryError as exc:
        row["delivery_status"] = UNREADABLE
        row["technical"]["reason"] = str(exc)
        row["creative"]["reason"] = str(exc)
        row["outcome_reason"] = (f"the delivered file could not be read: "
                                  f"{exc}")
        return row

    row["delivery_status"] = DELIVERED
    row["sha256"] = render.get("sha256")
    row["duration_seconds"] = render.get("duration_seconds")

    objective = judgement.objective_legs(
        path, spec=spec,
        expected_duration=expected_duration
        or render.get("duration_seconds"),
        caption_intents=caption_intents,
        declared_silence_spans=list(declared_silence),
        declared_black_spans=list(declared_black))
    row["technical"] = {
        "status": "measured",
        "legs": objective,
        "reason": None,
        "measurements": _measurement_rows(objective),
    }

    creative = _creative_half(entry, brief, render, judge, independence)
    row["creative"] = creative

    row["outcome"], row["outcome_reason"] = _outcome(
        entry, objective, creative)
    row["technical_failure_hidden"] = bool(
        creative.get("status") == "pass"
        and any(leg.get("status") == "fail" for leg in objective))
    return row


def _measurement_rows(objective: Sequence[Mapping[str, Any]]
                      ) -> list[dict[str, Any]]:
    """Every measurement the engine's own gates made, named."""
    rows: list[dict[str, Any]] = []
    for leg in objective:
        for measurement in leg.get("measurements") or ():
            rows.append({
                "leg": leg.get("leg"),
                "name": measurement.get("name"),
                "citation": measurement.get("citation"),
                "status": measurement.get("status"),
                "detail": measurement.get("detail"),
            })
    return rows


def _creative_half(entry: Mapping[str, Any], brief: Mapping[str, Any],
                   render: Mapping[str, Any], judge: Callable | None,
                   independence: Mapping[str, Any]) -> dict[str, Any]:
    """The brief's own criteria, judged by somebody who did not see the plan.

    Without an independent reviewer the creative half is `not_measured`,
    and it says why: the punch list asks for an independent assessment
    of the creative quality, so an assessment nobody can call independent
    does not count.
    """
    from library.tools import editorial_brief_scorer

    chosen = judge or _recorded_judge(entry)
    if chosen is None:
        return {
            "status": "not_measured",
            "criteria": [],
            "score": None,
            "reason": "the corpus carries no recorded judgment for this "
                      "video, so nothing about its creative quality was "
                      "measured",
        }
    if not independence.get("independent"):
        return {
            "status": "not_measured",
            "criteria": [],
            "score": None,
            "reason": independence.get("reason"),
        }
    scored = editorial_brief_scorer.score_brief(
        request_id=str(entry.get("video_id") or ""),
        brief=brief, render=render, judge=chosen)
    editorial = scored.get("editorial") or {}
    return {
        "status": str(editorial.get("status") or "not_measured"),
        "score": editorial.get("score"),
        "passed": editorial.get("passed"),
        "failed": editorial.get("failed"),
        "measured": editorial.get("measured"),
        "not_measured": editorial.get("not_measured"),
        "criteria": editorial.get("criteria") or [],
        "judge": scored.get("judge"),
        "reason": None,
    }


def _outcome(entry: Mapping[str, Any],
             objective: Sequence[Mapping[str, Any]],
             creative: Mapping[str, Any]) -> tuple[str, str]:
    """The acceptance gate's own reading of one finished video.

    The gate is the engine's own (`library/tools/acceptance_state.py`),
    run over the legs a delivered file answers for itself.  A technical
    failure is a failure whatever the creative score says, and a leg
    nobody measured leaves the video UNMEASURED rather than accepted.
    """
    facts: dict[str, Any] = {
        "has_delivered_file": True,
        "has_captions": entry.get("has_captions"),
        "requires_approval": entry.get("requires_approval"),
    }
    results: dict[str, dict[str, Any]] = {}
    for leg in objective:
        acceptance_leg = OBJECTIVE_LEG_TO_ACCEPTANCE_LEG.get(
            str(leg.get("leg") or ""))
        if not acceptance_leg:
            continue
        status = str(leg.get("status") or "")
        results[acceptance_leg] = {
            "status": (acceptance_state.PASSED if status == "pass"
                       else acceptance_state.FAILED if status == "fail"
                       else acceptance_state.UNMEASURED),
            "observation": _observation(leg),
            "artifact": str(entry.get("path") or ""),
        }
    creative_status = str(creative.get("status") or "not_measured")
    results["editorial_review"] = {
        "status": (acceptance_state.PASSED if creative_status == "pass"
                   else acceptance_state.FAILED if creative_status == "fail"
                   else acceptance_state.UNMEASURED),
        "observation": (
            f"the brief's own criteria, judged by "
            f"{creative.get('judge') or 'no reviewer'}: "
            f"{creative.get('passed')} passed, {creative.get('failed')} "
            f"failed, {creative.get('not_measured')} not measured"),
        "artifact": str(entry.get("path") or ""),
    }
    evaluated = acceptance_state.evaluate(facts, results,
                                          legs=FINISHED_VIDEO_LEGS)
    return str(evaluated["outcome"]), str(evaluated["reason"])


def _observation(leg: Mapping[str, Any]) -> str:
    """One objective leg, said in a sentence a reader can check."""
    measurements = leg.get("measurements") or ()
    failed = [measurement for measurement in measurements
              if measurement.get("status") == "fail"]
    if failed:
        return "; ".join(str(measurement.get("detail") or "")
                         for measurement in failed)
    if all(measurement.get("status") == "not_measured"
           for measurement in measurements):
        return "; ".join(str(measurement.get("detail") or "")
                         for measurement in measurements)
    return "; ".join(str(measurement.get("detail") or "")
                     for measurement in measurements)


# ── the corpus-level report ───────────────────────────────────────────────

def _count(denominator: int, measured: int, passed: int,
           failed: int) -> dict[str, Any]:
    """One rate, on its own denominator.

    `not_measured` is counted against the denominator and is never a
    pass and never a failure: a measurement nobody made is not a zero,
    and folding it into the passed count would turn "nobody looked" into
    "it was fine".
    """
    return {
        "denominator": denominator,
        "measured": measured,
        "passed": passed,
        "failed": failed,
        "not_measured": denominator - measured,
        "value": (passed / measured) if measured else None,
        "status": "measured" if measured else "not_measured",
    }


def _technical_view(videos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The mechanical half, over the corpus, on its own denominators."""
    denominator = len(videos)
    legs: dict[str, Any] = {}
    measurements: dict[str, Any] = {}
    for video in videos:
        for leg in video.get("technical", {}).get("legs") or ():
            leg_id = str(leg.get("leg") or "")
            row = legs.setdefault(leg_id, {"measured": 0, "passed": 0,
                                           "failed": 0})
            status = str(leg.get("status") or "not_measured")
            if status == "not_measured":
                continue
            row["measured"] += 1
            row["passed" if status == "pass" else "failed"] += 1
        for measurement in video.get("technical", {}).get(
                "measurements") or ():
            name = str(measurement.get("name") or "")
            row = measurements.setdefault(name, {"measured": 0, "passed": 0,
                                                 "failed": 0,
                                                 "citation":
                                                 measurement.get("citation")})
            status = str(measurement.get("status") or "not_measured")
            if status == "not_measured":
                continue
            row["measured"] += 1
            row["passed" if status == "pass" else "failed"] += 1
    return {
        "note": (
            "The MECHANICAL half: the engine's own gates on the delivered "
            "file. Every leg and every measurement carries its own "
            "denominator, and a measurement that could not run is "
            "not_measured - never a zero."),
        "legs": {leg_id: _count(denominator, row["measured"], row["passed"],
                                row["failed"])
                 for leg_id, row in sorted(legs.items())},
        "measurements": {
            name: _count(denominator, row["measured"], row["passed"],
                         row["failed"])
            for name, row in sorted(measurements.items())},
        "videos_with_a_failure": sorted(
            str(video.get("video_id")) for video in videos
            if any(leg.get("status") == "fail"
                   for leg in video.get("technical", {}).get("legs") or ())),
    }


def _creative_view(videos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The creative half, over the corpus, on its own denominators.

    A criterion's denominator is the number of finished videos whose
    BRIEF asks about it - not the size of the corpus.  A criterion one
    brief asks about and another does not is a criterion with one
    reading, and counting the video that was never asked as an
    unmeasured reading would put a hole in the denominator that no
    reader asked for.
    """
    criteria: dict[str, Any] = {}
    statuses = {"pass": 0, "fail": 0, "not_measured": 0}
    for video in videos:
        creative = video.get("creative") or {}
        status = str(creative.get("status") or "not_measured")
        statuses[status if status in statuses else "not_measured"] += 1
        asked = {str(criterion_id) for criterion_id
                 in (video.get("brief_criteria") or [])}
        for criterion_id in asked:
            row = criteria.setdefault(criterion_id, {"denominator": 0,
                                                     "measured": 0,
                                                     "passed": 0,
                                                     "failed": 0})
            row["denominator"] += 1
        for criterion in creative.get("criteria") or ():
            criterion_id = str(criterion.get("id") or "")
            row = criteria.setdefault(criterion_id, {"denominator": 0,
                                                     "measured": 0,
                                                     "passed": 0,
                                                     "failed": 0})
            criterion_status = str(criterion.get("status")
                                   or "not_measured")
            if criterion_status == "not_measured":
                continue
            row["measured"] += 1
            row["passed" if criterion_status == "pass"
                else "failed"] += 1
    return {
        "note": (
            "The CREATIVE half: the brief's own acceptance criteria, "
            "judged against the delivered file by a reviewer who did not "
            "see the plan. It is a READING, not a measurement, and it is "
            "never averaged with the mechanical half. A criterion's "
            "denominator is the finished videos whose brief asks about "
            "it."),
        "criteria": {
            criterion_id: _count(row["denominator"], row["measured"],
                                 row["passed"], row["failed"])
            for criterion_id, row in sorted(criteria.items())},
        "video_statuses": statuses,
        "videos_with_a_failure": sorted(
            str(video.get("video_id")) for video in videos
            if str((video.get("creative") or {}).get("status")) == "fail"),
    }


def _tracked(videos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The four numbers the punch list asks for, each on its denominator."""
    return {
        "first_pass_acceptance": _first_pass_acceptance(videos),
        "revision_count": _revision_count(videos),
        "human_correction_seconds": _human_correction_seconds(videos),
        "preserved_work": _preserved_work(videos),
    }


def _first_pass_acceptance(videos: Sequence[Mapping[str, Any]]
                            ) -> dict[str, Any]:
    """How many finished videos were accepted without a revision."""
    measured = [video for video in videos
                if isinstance((video.get("history") or {}).get(
                    "first_attempt_accepted"), bool)]
    accepted = [video for video in measured
                if (video.get("history") or {}).get("first_attempt_accepted")
                is True]
    row = _count(len(measured), len(measured), len(accepted), 0)
    row["note"] = (
        "Of the finished videos that recorded whether their FIRST attempt "
        "was accepted, how many were. The denominator is the videos that "
        "recorded it, not the corpus: a video with no recorded first "
        "attempt is not_measured here, not a failure.")
    row["videos_without_a_record"] = sorted(
        str(video.get("video_id")) for video in videos
        if not isinstance((video.get("history") or {}).get(
            "first_attempt_accepted"), bool))
    return row


def _revision_count(videos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How many revisions the finished videos took."""
    measured = [video for video in videos
                if isinstance((video.get("history") or {}).get("attempts"),
                              int)]
    attempts = [int((video.get("history") or {})["attempts"])
                for video in measured]
    revisions = [attempt - 1 for attempt in attempts]
    return {
        "note": (
            "How many REVISIONS the finished videos took: one less than "
            "the number of attempts. The denominator is the videos that "
            "recorded an attempt count."),
        "denominator": len(measured),
        "measured": len(measured),
        "total_revisions": sum(revisions) if revisions else None,
        "mean_revisions": (sum(revisions) / len(revisions)
                           if revisions else None),
        "max_revisions": max(revisions) if revisions else None,
        "value": (sum(revisions) / len(revisions) if revisions else None),
        "status": "measured" if measured else "not_measured",
        "videos_without_a_record": sorted(
            str(video.get("video_id")) for video in videos
            if not isinstance((video.get("history") or {}).get("attempts"),
                              int)),
    }


def _human_correction_seconds(videos: Sequence[Mapping[str, Any]]
                              ) -> dict[str, Any]:
    """How long a person spent correcting the finished videos."""
    measured = [video for video in videos
                if isinstance((video.get("history") or {}).get(
                    "human_correction_seconds"), (int, float))
                and not isinstance((video.get("history") or {}).get(
                    "human_correction_seconds"), bool)]
    seconds = [float((video.get("history") or {})["human_correction_seconds"])
               for video in measured]
    return {
        "note": (
            "How long a PERSON spent correcting the finished videos. The "
            "denominator is the videos that recorded a correction time; a "
            "video nobody corrected, or whose correction time nobody "
            "recorded, is not_measured here and never a zero."),
        "denominator": len(measured),
        "measured": len(measured),
        "total_seconds": sum(seconds) if seconds else None,
        "mean_seconds": (sum(seconds) / len(seconds) if seconds else None),
        "max_seconds": max(seconds) if seconds else None,
        "value": (sum(seconds) / len(seconds) if seconds else None),
        "status": "measured" if measured else "not_measured",
        "videos_without_a_record": sorted(
            str(video.get("video_id")) for video in videos
            if not isinstance((video.get("history") or {}).get(
                "human_correction_seconds"), (int, float))),
    }


def _preserved_work(videos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How much of what was already approved survived."""
    checks: dict[str, dict[str, int]] = {}
    total = 0
    passed = 0
    for video in videos:
        preserved = (video.get("history") or {}).get("preserved_work")
        preserved = preserved if isinstance(preserved, Mapping) else {}
        for name, value in (preserved.get("checks") or {}).items():
            row = checks.setdefault(str(name), {"measured": 0, "passed": 0,
                                                "failed": 0})
            row["measured"] += 1
            row["passed" if value is True else "failed"] += 1
            total += 1
            if value is True:
                passed += 1
    return {
        "note": (
            "How much of the work that was ALREADY approved survived the "
            "edits. Each check is a yes or a no recorded by whoever ran "
            "the build; the denominator is the checks that were recorded."),
        "denominator": total,
        "measured": total,
        "passed": passed,
        "failed": total - passed,
        "value": (passed / total) if total else None,
        "status": "measured" if total else "not_measured",
        "checks": {name: _count(row["measured"], row["measured"],
                                row["passed"], row["failed"])
                   for name, row in sorted(checks.items())},
    }


def release_gate(videos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The punch list's quality release gate, computed.

    Two conditions, and both are counted rather than asserted:

    - the corpus holds a representative set of FINISHED videos - each
      one delivered, each one independently reviewed;
    - no video carries a technical failure behind an acceptable
      creative score.  The hiding is checked PER VIDEO, because a
      corpus-level average is where the hiding would go.
    """
    delivered = [video for video in videos
                 if video.get("delivery_status") == DELIVERED]
    independent = [video for video in videos
                   if (video.get("independence") or {}).get("independent")]
    hidden = [video for video in videos
              if video.get("technical_failure_hidden")]
    conditions = [
        {
            "id": "representative_set",
            "statement": (
                f"the corpus holds at least {MIN_REPRESENTATIVE_VIDEOS} "
                "finished videos"),
            "met": len(videos) >= MIN_REPRESENTATIVE_VIDEOS,
            "count": len(videos),
            "required": MIN_REPRESENTATIVE_VIDEOS,
            "reason": (
                f"the corpus holds {len(videos)} finished video(s)"),
        },
        {
            "id": "every_video_delivered",
            "statement": "every finished video in the corpus was delivered",
            "met": len(delivered) == len(videos) and bool(videos),
            "count": len(delivered),
            "required": len(videos),
            "reason": (
                f"{len(delivered)} of {len(videos)} finished videos were "
                "delivered"),
        },
        {
            "id": "independently_reviewed",
            "statement": (
                "every finished video was reviewed for its creative "
                "quality by a reviewer who did not see the plan"),
            "met": len(independent) == len(videos) and bool(videos),
            "count": len(independent),
            "required": len(videos),
            "reason": (
                f"{len(independent)} of {len(videos)} finished videos "
                "carry an independent review"),
        },
        {
            "id": "no_hidden_technical_failure",
            "statement": (
                "no technical failure is hidden behind an acceptable "
                "subjective score"),
            "met": not hidden,
            "count": len(hidden),
            "required": 0,
            "reason": (
                "no finished video carries a technical failure behind an "
                "acceptable creative score" if not hidden else
                "these finished videos carry a technical failure behind an "
                "acceptable creative score: "
                + ", ".join(str(video.get("video_id")) for video in hidden)),
        },
    ]
    unmet = [condition["id"] for condition in conditions
             if not condition["met"]]
    return {
        "note": (
            "The quality release gate. Every condition carries its own "
            "count and its own reason, and the gate is met only when "
            "every condition is."),
        "met": not unmet,
        "unmet": unmet,
        "conditions": conditions,
    }


def run_benchmark(corpus: Mapping[str, Any], *,
                  judge: Callable | None = None,
                  brief_set: Mapping[str, Any] | None = None,
                  align_transcript: bool = True,
                  workdir: str | Path | None = None) -> dict[str, Any]:
    """Measure every finished video in the corpus, and report.

    The report carries the two halves SEPARATELY, the four tracked
    numbers with their own denominators, and the release gate.  There
    is no combined score anywhere in it, and there is deliberately no
    way to compute one from what is here: the two halves are not the
    same unit.
    """
    problems = validate_corpus(corpus)
    if problems:
        raise CorpusError(
            "the corpus breaks its contract:\n  " + "\n  ".join(problems))
    if brief_set is None:
        brief_set = briefs.load_brief_set(corpus.get("brief_set"))
    videos = [score_video(entry, judge=judge, brief_set=brief_set,
                           align_transcript=align_transcript,
                           workdir=workdir)
              for entry in corpus["videos"]]
    report = {
        "schema": REPORT_SCHEMA,
        "corpus": {
            "corpus_id": str(corpus.get("corpus_id") or ""),
            "description": str(corpus.get("description") or ""),
            "video_count": len(videos),
            "brief_count": len({str(video.get("brief_id"))
                                for video in videos}),
        },
        "video_count": len(videos),
        "technical": _technical_view(videos),
        "creative": _creative_view(videos),
        "tracked": _tracked(videos),
        "release_gate": release_gate(videos),
        "outcomes": {
            outcome: sum(1 for video in videos
                         if video.get("outcome") == outcome)
            for outcome in acceptance_state.OUTCOMES
        },
        "the_halves_are_not_combined": True,
        "note": (
            "The mechanical half and the creative half are measured on "
            "their own denominators and are never combined. A half that "
            "was not measured is not_measured, never a zero."),
        "videos": videos,
    }
    return report_provenance.stamp(
        report,
        evidence_kind="editorial-acceptance",
        fixture=str(corpus.get("corpus_id") or "not_recorded"),
        producer=("library/tools/finished_video_benchmark.py:run_benchmark "
                  "(ren accept --finished-video)"),
        scope={
            "measurement": "finished_video_acceptance",
            "evaluation_level": 4,
            "task_set": str(corpus.get("corpus_id") or "not_recorded"),
            "fixture": str(corpus.get("corpus_id") or "not_recorded"),
        })


# ── the baseline table ────────────────────────────────────────────────────

def baseline_table(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The baseline table: one row per finished video, and the totals.

    The table is DATA, so a reader can diff it against the next run
    without reading a sentence.  The mechanical half and the creative
    half sit in separate COLUMNS, because they are separate
    denominators and a column that held their average would be the
    thing this benchmark exists to stop.
    """
    rows: list[dict[str, Any]] = []
    for video in report.get("videos") or ():
        technical = video.get("technical") or {}
        legs = technical.get("legs") or []
        creative = video.get("creative") or {}
        history = video.get("history") or {}
        rows.append({
            "video_id": video.get("video_id"),
            "delivery": video.get("delivery_status"),
            "technical_legs_measured": sum(
                1 for leg in legs if leg.get("status") != "not_measured"),
            "technical_legs_failed": sum(
                1 for leg in legs if leg.get("status") == "fail"),
            "technical_legs_not_measured": sum(
                1 for leg in legs if leg.get("status") == "not_measured"),
            "creative_criteria_measured": creative.get("measured"),
            "creative_criteria_passed": creative.get("passed"),
            "creative_criteria_failed": creative.get("failed"),
            "creative_score": creative.get("score"),
            "outcome": video.get("outcome"),
            "technical_failure_hidden": video.get(
                "technical_failure_hidden"),
            "first_attempt_accepted": history.get("first_attempt_accepted"),
            "revisions": (history.get("attempts") - 1
                          if isinstance(history.get("attempts"), int)
                          else None),
            "human_correction_seconds": history.get(
                "human_correction_seconds"),
            "preserved_work_checks": len(
                (history.get("preserved_work") or {}).get("checks") or {}),
        })
    return rows


def table_markdown(report: Mapping[str, Any]) -> str:
    """The baseline table, as markdown a reader can paste anywhere."""
    columns = (
        ("video_id", "finished video"),
        ("delivery", "delivered"),
        ("technical_legs_measured", "tech legs measured"),
        ("technical_legs_failed", "tech legs failed"),
        ("technical_legs_not_measured", "tech legs not measured"),
        ("creative_criteria_measured", "creative measured"),
        ("creative_criteria_passed", "creative passed"),
        ("creative_criteria_failed", "creative failed"),
        ("outcome", "outcome"),
        ("technical_failure_hidden", "failure hidden"),
        ("revisions", "revisions"),
        ("human_correction_seconds", "human correction s"),
    )
    rows = baseline_table(report)
    lines = ["| " + " | ".join(label for _, label in columns) + " |",
             "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(
            "" if row.get(key) is None else str(row.get(key))
            for key, _ in columns) + " |")
    return "\n".join(lines)


def write_report(report: Mapping[str, Any],
                 path: str | Path | None = None) -> Path:
    """Write the report where a reader will find it."""
    target = Path(path) if path is not None else default_report_path()
    return report_provenance.write_report(target, report)


def default_report_path() -> Path:
    """Where the finished-video report goes when nobody says."""
    from ren.engine_root import find_engine_root

    root = find_engine_root()
    if root is None:
        root = Path(__file__).resolve().parents[2]
    return Path(root) / "tests/live/reports/finished-video-benchmark.json"
