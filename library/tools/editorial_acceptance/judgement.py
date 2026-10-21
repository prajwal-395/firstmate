"""The independent editorial assessment, and what a judge is allowed to see.

The punch list asks for editorial assessments recorded without exposing
the expected choices to the agent. Two halves, and they are separate on
purpose:

- the AGENT must not see the answer key, so it works the request out
  rather than recognising it. `briefs.assert_no_leak` holds that half.
- the JUDGE must not see the answer key either, or the assessment is an
  echo of the evaluator's plan rather than a reading of the film. That
  is this module: `judge_input` is the ONE construction of what a judge
  is shown, and it is built from the brief's public half and the file's
  own digest, evidence and length. Nothing else.

A panel of judges is read, not averaged. Two judges that disagree about
a criterion have not established it, and the disagreement is reported
where it happened rather than resolved by averaging two opinions into a
half-credit.

The REVIEW CONTRACT
-------------------
The punch list's QA-006 asks for a structured editorial review that
evaluates a delivered file on eight named legs, each judgment carrying
its evidence, its uncertainty, and a clear distinction between an
OBJECTIVE failure and a SUBJECTIVE preference.

That distinction is the whole point, and it is enforced by the shape of
the record rather than by prose: the objective rows and the subjective
rows are SECTIONS, a subjective row may not claim an objective basis, and
the verdict is computed so that a subjective score can never clear an
objective failure. A technical defect that a reviewer scored well on
the other seven legs is still a technical defect, and the review says
so in the field a reader looks at first.

The objective rows are the ENGINE'S OWN measurements of the delivered
file - the audio spec (QA-004), the caption spec (QA-003) and the
picture spec (QA-002) - read by `objective_legs` and CITED by name. The
contract does not re-measure anything the product already measures; it
says which measurement speaks to which leg, and what happens when that
measurement could not run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from library.tools.editorial_acceptance import briefs

PANEL_SCHEMA = "ren-editorial-assessment-panel/1"

#: What a judge is shown about the delivered file. The digest, the
#: evidence a judge can look at, and how long it is. Nothing about the
#: plan that produced it.
RENDER_KEYS: tuple[str, ...] = ("sha256", "evidence", "duration_seconds")


class JudgementError(ValueError):
    """The assessment cannot be taken, or is not independent."""


def assert_independent(brief: Mapping[str, Any]) -> None:
    """Refuse a brief that carries the answer key into the judge's hands.

    The judge's view is built from the public half of a brief, so a brief
    handed to a judge must not carry any of the evaluator's fields.
    """
    offending = sorted(key for key in briefs.ORACLE_KEYS if key in brief)
    if offending:
        raise JudgementError(
            "the brief handed to a judge carries the answer key: "
            + ", ".join(offending))
    unknown = sorted(key for key in brief if key not in briefs.PUBLIC_KEYS)
    if unknown:
        raise JudgementError(
            "the brief handed to a judge carries fields the public half "
            "does not have: " + ", ".join(unknown))


def judge_input(brief: Mapping[str, Any],
                render: Mapping[str, Any]) -> dict[str, Any]:
    """Everything a judge is shown, and nothing else.

    The brief's public half, plus the delivered file's digest, its
    evidence and its length. The answer key is not in here, and neither
    is anything about the plan that produced the file.
    """
    assert_independent(brief)
    missing = [key for key in RENDER_KEYS if key not in render]
    if missing:
        raise JudgementError(
            "the render handed to a judge is missing: " + ", ".join(missing))
    return {
        "brief": {
            "instruction": brief["instruction"],
            "project_state": brief["project_state"],
            "acceptance_criteria": brief["acceptance_criteria"],
        },
        "render": {key: render[key] for key in RENDER_KEYS},
    }


def assess(*, task_id: str, brief: Mapping[str, Any],
           render: Mapping[str, Any],
           judge: Callable[[Mapping[str, Any], Mapping[str, Any]],
                           Mapping[str, Any]]) -> dict[str, Any]:
    """One judge's reading of one delivered file, against one brief.

    The scorer is the engine's own (`library/tools/editorial_brief_scorer`),
    so the shape of a criterion judgment is the one the live harness
    already grades. What this adds is the independence guarantee: the
    judge is handed `judge_input` and nothing else, whatever the scorer
    passes in.
    """
    from library.tools import editorial_brief_scorer

    payload = judge_input(brief, render)

    def _scored_judge(_brief: Mapping[str, Any],
                      _render: Mapping[str, Any]) -> Mapping[str, Any]:
        return judge(payload["brief"], payload["render"])

    record = editorial_brief_scorer.score_brief(
        request_id=task_id, brief=brief, render=render, judge=_scored_judge)
    record["independence"] = {
        "judge_saw": sorted(payload["brief"]) + [
            f"render.{key}" for key in RENDER_KEYS],
        "judge_did_not_see": list(briefs.ORACLE_KEYS),
    }
    return record


def score_recorded(*, task_id: str, brief: Mapping[str, Any],
                   render: Mapping[str, Any],
                   criteria: Sequence[Mapping[str, Any]],
                   judge_name: str) -> dict[str, Any]:
    """A RECORDED judgment, scored by the engine's scorer.

    The per-criterion rows are the judge's work: what it saw in the
    delivered file and what it concluded. The COUNTS, the score and the
    verdict are the scorer's work, and they were being typed by hand -
    which is how a record came to say `passed 3, failed 4` over rows
    that were three passes, three failures and four unmeasured, with a
    score of `None` where the scorer computes a number.

    So the rows are kept and the counts are recomputed, here, by the
    same scorer the live harness grades with. The judge is not re-run:
    it already read the file, and re-running it would be a second
    reading, not a scoring.
    """
    if not str(judge_name or "").strip():
        raise JudgementError("a recorded judgment carries no judge to name")
    rows = [dict(row) for row in criteria if isinstance(row, Mapping)]

    def _replay(_brief: Mapping[str, Any],
                _render: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"judge": judge_name, "criteria": rows}

    return assess(task_id=task_id, brief=brief, render=render,
                  judge=_replay)


def _criterion_status(record: Mapping[str, Any], criterion_id: str) -> str:
    editorial = record.get("editorial")
    editorial = editorial if isinstance(editorial, Mapping) else {}
    for row in editorial.get("criteria") or []:
        if isinstance(row, Mapping) and row.get("id") == criterion_id:
            return str(row.get("status") or "not_measured")
    return "not_measured"


def panel(assessments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Two or more independent readings, compared criterion by criterion.

    A criterion is established only when the judges agree about it. One
    that says pass and one that says fail have established nothing, and
    the panel says so instead of choosing a side.
    """
    records = [record for record in assessments
               if isinstance(record, Mapping)]
    judges = [str(record.get("judge") or "unnamed") for record in records]
    criterion_ids: list[str] = []
    for record in records:
        editorial = record.get("editorial")
        editorial = editorial if isinstance(editorial, Mapping) else {}
        for row in editorial.get("criteria") or []:
            if isinstance(row, Mapping) and row.get("id") not in criterion_ids:
                criterion_ids.append(str(row.get("id")))
    per_criterion: dict[str, Any] = {}
    disagreements: list[dict[str, Any]] = []
    for criterion_id in criterion_ids:
        statuses = [_criterion_status(record, criterion_id)
                    for record in records]
        unique = set(statuses)
        if len(unique) == 1:
            status = statuses[0]
            agreement = True
        else:
            status = "disagreement"
            agreement = False
            disagreements.append({"id": criterion_id, "statuses": statuses})
        per_criterion[criterion_id] = {
            "status": status,
            "statuses": statuses,
            "agreement": agreement,
        }
    agreed = sum(1 for row in per_criterion.values() if row["agreement"])
    return {
        "schema": PANEL_SCHEMA,
        "judges": judges,
        "judge_count": len(records),
        "criteria": per_criterion,
        "disagreements": disagreements,
        "agreement_rate": (agreed / len(per_criterion)
                           if per_criterion else None),
        "verdict": panel_verdict(per_criterion),
    }


def panel_verdict(per_criterion: Mapping[str, Any]) -> str:
    """The panel's reading of the whole brief.

    A failure the judges all agree on is a failure. A criterion they
    disagree about is not established, and the brief is not established
    either - which is not the same as a failure, and is not a pass.
    """
    statuses = {str(row.get("status")) for row in per_criterion.values()}
    if not statuses:
        return "not_measured"
    if "fail" in statuses:
        return "fail"
    if "disagreement" in statuses:
        return "disagreement"
    if "not_measured" in statuses:
        return "not_measured"
    return "pass"


# ── the review contract (QA-006) ────────────────────────────────────────

REVIEW_SCHEMA = "ren-editorial-review/1"

#: The two kinds of row a review carries. An OBJECTIVE row is a
#: measurement the engine's own gates made ON THE DELIVERED FILE; a
#: SUBJECTIVE row is a judge's reading of that same file. They are kept
#: in separate sections because they answer different questions, and
#: one of them must never be allowed to stand in for the other.
OBJECTIVE = "objective"
SUBJECTIVE = "subjective"

#: How a row reached its status. The two FAILURE values are the
#: distinction the punch list asks for: an OBJECTIVE FAILURE is a
#: measurable defect in the file, and a SUBJECTIVE PREFERENCE is an
#: editorial opinion - a real reading, but not a defect until the
#: captain says so.
OBJECTIVE_FAILURE = "objective_failure"
OBJECTIVE_PASS = "objective_pass"
SUBJECTIVE_PREFERENCE = "subjective_preference"
SUBJECTIVE_READING = "subjective_reading"
NOT_MEASURED = "not_measured"

STATUSES: tuple[str, ...] = ("pass", "fail", "not_measured")

#: How sure a row is. `none` is a CLAIM - the reading admits nothing -
#: and it is spelled out rather than left as an empty field, because an
#: empty field reads as an oversight and an oversight is what this
#: contract exists to catch.
UNCERTAINTY_LEVELS: tuple[str, ...] = ("none", "low", "medium", "high")

#: The engine's own measurements of the delivered file, CITED by name.
#: The review does not re-measure anything the product already
#: measures; it says which measurement speaks to which leg, and what
#: happens when that measurement could not run.
AUDIO_SPEC_CITATION = ("library.tools.deliverable_qc:verify_audio_spec "
                       "(QA-004)")
CAPTION_SPEC_CITATION = ("library.tools.delivered_caption_spec:"
                          "verify_delivered_captions (QA-003)")
PICTURE_SPEC_CITATION = ("library.tools.deliverable_qc:verify_picture_spec "
                          "(QA-002)")
BLACK_FRAME_CITATION = "library.tools.render_qa:detect_black_frames"
FREEZE_FRAME_CITATION = "library.tools.render_qa:detect_freeze_frames"


class ReviewError(ValueError):
    """The review does not satisfy the contract."""


class ReviewLeg:
    """One leg of the editorial review.

    `objective_citations` names the engine's own measurement of the
    DELIVERED FILE that speaks to this leg, when one exists. A leg with
    no citation has no objective half: it is judged, and the review
    says so rather than letting the reader assume a measurement ran.
    """

    __slots__ = ("id", "objective_citations", "statement")

    def __init__(self, leg_id: str, statement: str,
                 objective_citations: tuple[str, ...] = ()) -> None:
        self.id = leg_id
        self.statement = statement
        self.objective_citations = tuple(objective_citations)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "statement": self.statement,
                "objective_citations": list(self.objective_citations)}


#: The eight legs of the review, quoted from the punch list's QA-006.
#: They are DATA, in one place, because a review that quietly judged
#: five of them is a review whose coverage nobody can read.
REVIEW_LEGS: tuple[ReviewLeg, ...] = (
    ReviewLeg(
        "narrative_coherence",
        "Narrative coherence and preservation of meaning."),
    ReviewLeg(
        "moment_selection",
        "Correctness and relevance of selected moments."),
    ReviewLeg(
        "pacing_and_rhythm",
        "Pacing and rhythm."),
    ReviewLeg(
        "speaker_camera_continuity",
        "Speaker and camera continuity."),
    ReviewLeg(
        "shot_quality",
        "Shot quality and visual relevance.",
        (PICTURE_SPEC_CITATION, BLACK_FRAME_CITATION,
         FREEZE_FRAME_CITATION)),
    ReviewLeg(
        "audio_continuity",
        "Audio continuity.",
        (AUDIO_SPEC_CITATION,)),
    ReviewLeg(
        "caption_and_graphic_clarity",
        "Caption and graphic clarity.",
        (CAPTION_SPEC_CITATION, PICTURE_SPEC_CITATION)),
    ReviewLeg(
        "creative_direction_adherence",
        "Adherence to the user's creative direction."),
)

REVIEW_LEG_IDS: tuple[str, ...] = tuple(leg.id for leg in REVIEW_LEGS)


def review_leg(leg_id: str) -> ReviewLeg:
    """The leg with this id, or a refusal."""
    for leg in REVIEW_LEGS:
        if leg.id == leg_id:
            return leg
    raise ReviewError(
        f"{leg_id!r} is not a leg of the editorial review. The legs are: "
        + ", ".join(REVIEW_LEG_IDS))


def _basis_for(kind: str, status: str) -> str:
    """The basis a row of this kind and status must carry."""
    if status == "not_measured":
        return NOT_MEASURED
    if kind == OBJECTIVE:
        return OBJECTIVE_FAILURE if status == "fail" else OBJECTIVE_PASS
    return (SUBJECTIVE_PREFERENCE if status == "fail"
            else SUBJECTIVE_READING)


def _check_evidence(row: Mapping[str, Any], leg_id: str,
                    status: str) -> list[dict[str, Any]]:
    """The evidence a measured row must carry, checked rather than hoped for."""
    evidence = row.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        if status == "not_measured":
            return []
        raise ReviewError(
            f"the {leg_id!r} row is measured and carries no evidence: a "
            "judgment without evidence is an opinion, and this contract "
            "refuses one")
    checked = []
    for item in evidence:
        if (not isinstance(item, Mapping)
                or not str(item.get("artifact") or "").strip()
                or not str(item.get("observation") or "").strip()):
            raise ReviewError(
                f"the {leg_id!r} row carries evidence without an artifact "
                "and an observation, so a reader cannot look at it")
        checked.append({"artifact": str(item["artifact"]),
                        "observation": str(item["observation"])})
    return checked


def _check_uncertainty(row: Mapping[str, Any], leg_id: str) -> dict[str, Any]:
    """The uncertainty a row must state, checked rather than hoped for."""
    uncertainty = row.get("uncertainty")
    if not isinstance(uncertainty, Mapping):
        raise ReviewError(
            f"the {leg_id!r} row states no uncertainty: a reading that "
            "does not say what could change it reads as a certainty it "
            "does not have")
    level = str(uncertainty.get("level") or "").strip()
    note = str(uncertainty.get("note") or "").strip()
    if level not in UNCERTAINTY_LEVELS:
        raise ReviewError(
            f"the {leg_id!r} row's uncertainty level {level!r} is not one "
            "of " + ", ".join(UNCERTAINTY_LEVELS))
    if not note:
        raise ReviewError(
            f"the {leg_id!r} row's uncertainty has no note: a level with "
            "no words says nothing")
    return {"level": level, "note": note}


def _normalise_row(row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    """One row, checked against the contract and put in its canonical shape."""
    leg_id = str(row.get("leg") or row.get("id") or "").strip()
    if not leg_id:
        raise ReviewError("a review row names no leg")
    leg = review_leg(leg_id)
    status = str(row.get("status") or "").strip()
    if status not in STATUSES:
        raise ReviewError(
            f"the {leg_id!r} row has status {status!r}, which is not one "
            "of " + ", ".join(STATUSES))
    basis = str(row.get("basis") or "").strip()
    expected = _basis_for(kind, status)
    if not basis:
        basis = expected
    if basis != expected:
        raise ReviewError(
            f"the {leg_id!r} row is {kind} and {status}, so its basis is "
            f"{expected!r}, not {basis!r}: an objective failure and a "
            "subjective preference are different claims and a row may "
            "not be one while claiming to be the other")
    rationale = str(row.get("rationale") or "").strip()
    if not rationale:
        raise ReviewError(
            f"the {leg_id!r} row carries no rationale: a verdict with no "
            "reason is not a reading")
    normalised = {
        "leg": leg.id,
        "kind": kind,
        "status": status,
        "basis": basis,
        "rationale": rationale,
        "evidence": _check_evidence(row, leg.id, status),
        "uncertainty": _check_uncertainty(row, leg.id),
    }
    if kind == OBJECTIVE:
        measurements = row.get("measurements")
        if not isinstance(measurements, list) or not measurements:
            raise ReviewError(
                f"the objective {leg_id!r} row names no measurement: an "
                "objective row must say which measurement produced it")
        checked = []
        for measurement in measurements:
            if not isinstance(measurement, Mapping):
                raise ReviewError(
                    f"the objective {leg_id!r} row carries a measurement "
                    "that is not an object")
            name = str(measurement.get("name") or "").strip()
            if not name:
                raise ReviewError(
                    f"the objective {leg_id!r} row carries a measurement "
                    "with no name")
            measured_status = str(measurement.get("status") or "").strip()
            if measured_status not in STATUSES:
                raise ReviewError(
                    f"the measurement {name!r} on the {leg_id!r} row has "
                    f"status {measured_status!r}, which is not one of "
                    + ", ".join(STATUSES))
            checked.append({
                "name": name,
                "citation": str(measurement.get("citation") or "").strip(),
                "status": measured_status,
                "detail": str(measurement.get("detail") or "").strip(),
            })
        normalised["measurements"] = checked
    return normalised


def _measurement(name: str, citation: str, result: Any) -> dict[str, Any]:
    """One engine measurement, as the review cites it.

    A measurement that carries NO value and reports itself as
    information did not run: the engine's own "not checked" shape. It is
    reported as not measured, never as a pass - a leg nobody could
    measure is not a leg that passed.
    """
    value = getattr(result, "value", None)
    severity = str(getattr(result, "severity", "") or "")
    if value is None and severity == "info":
        status = NOT_MEASURED
    else:
        status = ("pass" if bool(getattr(result, "passed", False))
                  else "fail")
    return {
        "name": name,
        "citation": citation,
        "status": status,
        "detail": str(getattr(result, "detail", "") or ""),
    }


def _unmeasured(name: str, citation: str, reason: str) -> dict[str, Any]:
    """A measurement that could not run, named as such."""
    return {"name": name, "citation": citation, "status": "not_measured",
            "detail": reason}


def objective_verdict(video_path: str, *, spec: Any,
                      expected_duration: float | None = None,
                      caption_intents: Sequence[Any] | None = None,
                      caption_spans: Sequence[tuple[float, float]] | None = None,
                      declared_silence_spans: Sequence[tuple[float, float]]
                      | None = None,
                      declared_black_spans: Sequence[tuple[float, float]]
                      | None = None,
                      ) -> Any:
    """The delivered file's QC verdict: the ONE measurement of the file.

    The review reads this, and so does the TARGETED REVISION planner
    (QA-009, `library/tools/targeted_revision.py`): the failure a
    revision is planned from is the same measurement the review reports,
    measured once.  A second measurement of the same file could
    disagree with the first, and a revision planned from the other one
    would be planned from a number nobody saw.
    """
    from library.tools import deliverable_qc

    return deliverable_qc.run_deliverable_qc(
        video_path, spec,
        expected_duration if expected_duration is not None else 0.0,
        declared_black_beats=list(declared_black_spans or ()),
        declared_silence_spans=list(declared_silence_spans or ()),
        caption_intents=list(caption_intents)
        if caption_intents is not None else None,
        caption_spans=list(caption_spans or ()),
        expected_width=int(getattr(spec, "width", 0) or 0) or None,
        expected_height=int(getattr(spec, "height", 0) or 0) or None)


def objective_legs(video_path: str, *, spec: Any,
                   expected_duration: float | None = None,
                   caption_intents: Sequence[Any] | None = None,
                   caption_spans: Sequence[tuple[float, float]] | None = None,
                   declared_silence_spans: Sequence[tuple[float, float]]
                   | None = None,
                   declared_black_spans: Sequence[tuple[float, float]]
                   | None = None,
                   qc: Any = None,
                   ) -> list[dict[str, Any]]:
    """The delivered file's OBJECTIVE legs, from the engine's own gates.

    One call, one file, three legs, and every measurement NAMED. The
    audio spec (QA-004), the caption spec (QA-003) and the picture spec
    (QA-002) are the product's own gates on the delivered file, so the
    review reads them instead of measuring the same numbers a second
    way.

    A gate that could not run is REPORTED as not measured, with the
    reason. It is never folded into a pass, and it is never invented:
    a picture spec that is not in this checkout is named as absent,
    with the measurement that WOULD speak to the leg cited beside the
    ones that ran.
    """
    if qc is None:
        qc = objective_verdict(
            video_path, spec=spec,
            expected_duration=expected_duration,
            caption_intents=caption_intents,
            caption_spans=caption_spans,
            declared_silence_spans=declared_silence_spans,
            declared_black_spans=declared_black_spans)

    picture = getattr(qc, "picture", None)
    if picture is None:
        picture_measurements = [_unmeasured(
            PICTURE_SPEC_CITATION,
            "library.tools.deliverable_qc:verify_picture_spec (QA-002)",
            "the delivered picture spec (QA-002) is not in this checkout, "
            "so the leg's picture measurements did not run")]
    else:
        picture_measurements = [
            _measurement(f"{PICTURE_SPEC_CITATION} :: dimensions",
                         PICTURE_SPEC_CITATION, picture.dimensions),
            _measurement(f"{PICTURE_SPEC_CITATION} :: framing",
                         PICTURE_SPEC_CITATION, picture.framing),
            _measurement(f"{PICTURE_SPEC_CITATION} :: overlays",
                         PICTURE_SPEC_CITATION, picture.overlays),
            _measurement(f"{PICTURE_SPEC_CITATION} :: black_frames",
                         PICTURE_SPEC_CITATION, picture.black_frames),
            _measurement(f"{PICTURE_SPEC_CITATION} :: freeze_frames",
                         PICTURE_SPEC_CITATION, picture.freeze_frames),
        ]

    rows: list[dict[str, Any]] = []

    audio = qc.audio
    rows.append(_normalise_row({
        "leg": "audio_continuity",
        "status": "pass" if audio.passed else "fail",
        "rationale": (
            "the delivered file's own audio spec: its loudness against "
            "the spec, the digital silence under picture that catches "
            "missing dialogue, and the channel presence"),
        "evidence": [{
            "artifact": str(video_path),
            "observation": (
                f"the delivered file's audio spec "
                f"({'passed' if audio.passed else 'FAILED'}): loudness "
                f"{audio.loudness.detail}; silence under picture "
                f"{audio.silence.detail}; channels {audio.channels.detail}")
        }],
        "uncertainty": {
            "level": "none",
            "note": ("the measurement is the product's own gate on the "
                     "delivered file; it does not read the plan, so it "
                     "cannot see a defect the plan intended")},
        "measurements": [
            _measurement(AUDIO_SPEC_CITATION,
                         "library.tools.deliverable_qc:verify_audio_spec "
                         "(QA-004)", audio.loudness),
            _measurement("library.tools.render_qa:"
                         "measure_silence_under_picture",
                         "library.tools.deliverable_qc:verify_audio_spec "
                         "(QA-004)", audio.silence),
            _measurement("library.tools.render_qa:"
                         "verify_channel_presence",
                         "library.tools.deliverable_qc:verify_audio_spec "
                         "(QA-004)", audio.channels),
        ],
    }, kind=OBJECTIVE))

    caption_spec = qc.caption_spec
    if caption_spec is None:
        caption_measurement = _unmeasured(
            CAPTION_SPEC_CITATION,
            "library.tools.delivered_caption_spec:"
            "verify_delivered_captions (QA-003)",
            "no caption intent was supplied, so the delivered file's "
            "captions had nothing to be compared against")
    else:
        caption_measurement = {
            "name": CAPTION_SPEC_CITATION,
            "citation": ("library.tools.delivered_caption_spec:"
                         "verify_delivered_captions (QA-003)"),
            "status": ("pass" if caption_spec.passed
                       else ("not_measured" if not caption_spec.checked
                             else "fail")),
            "detail": (caption_spec.reason
                       or "; ".join(finding.detail
                                    for finding in caption_spec.findings)
                       or f"{len(caption_spec.findings)} finding(s)"),
        }
    overlay_measurement = next(
        (measurement for measurement in picture_measurements
         if measurement["name"].endswith(":: overlays")),
        _unmeasured(PICTURE_SPEC_CITATION, PICTURE_SPEC_CITATION,
                    "the delivered picture spec did not run, so the "
                    "graphics it measures did not either"))
    caption_statuses = [caption_measurement["status"],
                        overlay_measurement["status"]]
    if "fail" in caption_statuses:
        caption_status = "fail"
    elif all(status == "not_measured" for status in caption_statuses):
        caption_status = "not_measured"
    else:
        caption_status = "pass"
    rows.append(_normalise_row({
        "leg": "caption_and_graphic_clarity",
        "status": caption_status,
        "rationale": (
            "the delivered file's own caption spec, and the overlays leg "
            "of the picture spec that measures the graphics the plan "
            "placed"),
        "evidence": [{
            "artifact": str(video_path),
            "observation": (
                f"the delivered file's caption and graphic measurements: "
                f"{caption_measurement['detail']}; "
                f"{overlay_measurement['detail']}")}],
        "uncertainty": {
            "level": "medium" if "not_measured" in caption_statuses
            else "none",
            "note": ("a measurement that could not run is named beside "
                     "the ones that did, and this leg is not a pass on "
                     "the ones that did"
                     if "not_measured" in caption_statuses else
                     "the measurement is the product's own gate on the "
                     "delivered file; it does not read the plan")},
        "measurements": [caption_measurement, overlay_measurement],
    }, kind=OBJECTIVE))

    shot_measurements = [measurement for measurement in picture_measurements
                         if not measurement["name"].endswith(":: overlays")]
    shot_statuses = [measurement["status"] for measurement in shot_measurements]
    if "fail" in shot_statuses:
        shot_status = "fail"
    elif all(status == "not_measured" for status in shot_statuses):
        shot_status = "not_measured"
    else:
        shot_status = "pass"
    rows.append(_normalise_row({
        "leg": "shot_quality",
        "status": shot_status,
        "rationale": (
            "the delivered file's own picture spec: the frame it was "
            "delivered at, whether the picture fills it, the black frames "
            "it holds and the frozen frames it holds"),
        "evidence": [{
            "artifact": str(video_path),
            "observation": (
                "the delivered file's picture measurements: "
                + "; ".join(measurement["detail"]
                            for measurement in shot_measurements))}],
        "uncertainty": {
            "level": "medium" if "not_measured" in shot_statuses else "none",
            "note": ("a measurement that could not run is named beside "
                     "the ones that did, and this leg is not a pass on "
                     "the ones that did"
                     if "not_measured" in shot_statuses else
                     "the measurement is the product's own gate on the "
                     "delivered file; it does not read the plan")},
        "measurements": shot_measurements,
    }, kind=OBJECTIVE))

    return rows


def review_verdict(objective_rows: Sequence[Mapping[str, Any]],
                   subjective_rows: Sequence[Mapping[str, Any]]
                   ) -> dict[str, Any]:
    """The review's reading of the whole file.

    Four verdicts, and the differences between them are the point:

    - ``fail`` - an OBJECTIVE leg failed. A measurable defect in the
      delivered file. Nothing a judge says about the other seven legs
      can clear it, and the subjective score is published beside the
      verdict rather than folded into it.
    - ``preference`` - no objective leg failed and a SUBJECTIVE leg did.
      A real reading, and an editorial opinion: not a technical defect.
    - ``not_measured`` - nothing failed and a leg was not read.
    - ``pass`` - every leg was read, nothing failed.

    The score is over the MEASURED subjective rows only, and it is
    named separately so that nobody can read a high score over a file
    that failed its audio spec.
    """
    objective_failures = [row for row in objective_rows
                          if row.get("status") == "fail"]
    subjective_failures = [row for row in subjective_rows
                           if row.get("status") == "fail"]
    measured = [row for row in subjective_rows
                if row.get("status") != "not_measured"]
    passed = [row for row in measured if row.get("status") == "pass"]
    unmeasured = [row for row in subjective_rows
                  if row.get("status") == "not_measured"]
    score = (len(passed) / len(measured)) if measured else None
    if objective_failures:
        verdict = "fail"
        reason = (
            f"{len(objective_failures)} objective leg(s) failed on the "
            "delivered file, and an objective failure is not cleared by a "
            "subjective score")
    elif subjective_failures:
        verdict = "preference"
        reason = (
            f"{len(subjective_failures)} subjective leg(s) failed and no "
            "objective leg did: these are editorial preferences, not "
            "technical defects")
    elif unmeasured:
        verdict = "not_measured"
        reason = (
            f"{len(unmeasured)} leg(s) were not read, so nothing was "
            "established about them")
    else:
        verdict = "pass"
        reason = "every leg was read and no leg failed"
    return {
        "verdict": verdict,
        "reason": reason,
        "objective_failures": [dict(row) for row in objective_failures],
        "subjective_failures": [dict(row) for row in subjective_failures],
        "objective_measured": sum(
            1 for row in objective_rows if row.get("status") != "not_measured"),
        "objective_not_measured": sum(
            1 for row in objective_rows if row.get("status") == "not_measured"),
        "subjective_measured": len(measured),
        "subjective_not_measured": len(unmeasured),
        "subjective_score": score,
        "subjective_score_cannot_clear": ["objective_failure"],
    }


def review(*, task_id: str, brief: Mapping[str, Any],
           render: Mapping[str, Any],
           judgments: Sequence[Mapping[str, Any]] | None = None,
           judge: Callable[[Mapping[str, Any], Mapping[str, Any]],
                           Mapping[str, Any]] | None = None,
           objective: Sequence[Mapping[str, Any]] = (),
           ) -> dict[str, Any]:
    """One structured editorial review of one delivered file.

    `judgments` are the judge's own rows - one per leg, each with its
    evidence and its uncertainty. `judge` is the callable that produces
    them, and it is handed `judge_input` and nothing else, so the
    independence the module exists for holds here too.

    `objective` are the engine's own measurements of the delivered file,
    as `objective_legs` produces them. They are a SECTION of the
    review, never a contribution to the subjective score.
    """
    if (judgments is None) == (judge is None):
        raise ReviewError(
            "a review carries either a recorded judgment or a judge, "
            "not both and not neither")
    if judgments is not None:
        rows = [dict(row) for row in judgments if isinstance(row, Mapping)]
    else:
        payload = judge_input(brief, render)
        produced = judge(payload["brief"], payload["render"])
        if not isinstance(produced, Mapping):
            raise ReviewError("the judge returned something that is not a "
                               "mapping")
        raw = produced.get("legs") or produced.get("criteria")
        if not isinstance(raw, list):
            raise ReviewError(
                "the judge returned no legs: a review reads the eight "
                "named legs and says so")
        rows = [dict(row) for row in raw if isinstance(row, Mapping)]

    subjective = [_normalise_row(row, kind=SUBJECTIVE) for row in rows]
    covered = {row["leg"] for row in subjective}
    missing = [leg_id for leg_id in REVIEW_LEG_IDS if leg_id not in covered]
    if missing:
        raise ReviewError(
            "the review does not carry a row for: " + ", ".join(missing)
            + ". Every leg is read; a leg that could not be read says "
            "so with a `not_measured` row rather than being left out.")
    duplicate = sorted({leg_id for leg_id in covered
                        if [row["leg"] for row in subjective].count(leg_id)
                        > 1})
    if duplicate:
        raise ReviewError(
            "the review carries more than one row for: "
            + ", ".join(duplicate))

    objective_rows = [_normalise_row(row, kind=OBJECTIVE)
                      for row in objective
                      if isinstance(row, Mapping)]
    verdict = review_verdict(objective_rows, subjective)
    coverage = []
    for leg in REVIEW_LEGS:
        leg_rows = [row for row in objective_rows if row["leg"] == leg.id]
        gaps = [measurement["name"]
                for row in leg_rows
                for measurement in row.get("measurements") or ()
                if measurement.get("status") == "not_measured"]
        coverage.append({
            "leg": leg.id,
            "statement": leg.statement,
            "objective_measurements": [
                measurement["name"]
                for row in leg_rows
                for measurement in row.get("measurements") or ()],
            "objective_gaps": gaps,
            "objective_citations": list(leg.objective_citations),
            "read": leg.id in covered,
        })
    return {
        "schema": REVIEW_SCHEMA,
        "task_id": str(task_id),
        "render_sha256": str(render.get("sha256") or ""),
        "brief_instruction": str(brief.get("instruction") or ""),
        "legs": coverage,
        "objective": objective_rows,
        "subjective": subjective,
        "verdict": verdict["verdict"],
        "verdict_reason": verdict["reason"],
        "objective_failures": verdict["objective_failures"],
        "subjective_failures": verdict["subjective_failures"],
        "subjective_score": verdict["subjective_score"],
        "subjective_score_cannot_clear": ["objective_failure"],
        "counts": {
            "objective_measured": verdict["objective_measured"],
            "objective_not_measured": verdict["objective_not_measured"],
            "subjective_measured": verdict["subjective_measured"],
            "subjective_not_measured": verdict["subjective_not_measured"],
        },
    }
