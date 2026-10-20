"""The canonical `EditorialIntent` - what was asked, apart from what will be done.

The gap this closes
-------------------
The commission reaches Ren as prose.  A brief is a markdown file; the
planner reads it and writes a direction; the direction's structured
half (`intent_model.editorial_spec`) keeps requirements and preferences
apart, and `scoped_intent` resolves declarations by precedence.  What
does NOT exist anywhere is ONE record of the request itself that says:

    - what the user REQUIRED, what they PREFERRED, and what Ren INFERRED
    - which of those are objective (checkable) and which are subjective
    - WHICH EDITORIAL REQUEST each one is (improve pacing, strengthen an
      opening, remove repetition, improve continuity, choose a better
      angle, tighten dialogue, replace B-roll, correct captions, revise
      graphics, or combine edit domains)
    - what evidence must exist before Ren may act on that request
    - whether Ren may act alone, must ask, or must wait for approval
    - what evidence is MISSING and how uncertain the answer is

Without that record every one of those answers is re-derived per step,
per prompt and per run, and a preference can drift into a requirement
without anything noticing.  This module is that record, and it is
SEPARATE from physical edit operations: an intent says what the viewer
should get; an operation says which clip moves.  The two are linked, and
the link is the whole point.

The model
---------
    EditorialIntent
        intent_id          stable identity
        brief_ref          where the request's words live
        desired_outcome    what should be different when it is done
        requirements       explicit, mandatory, objective
        preferences        explicit, creative, may yield to a requirement
        inferred_preferences   Ren's own reading, never a user instruction
        requests           the editorial requests, each with its own bar
        references         what the request pointed at
        constraints        the commission's objective limits
        unresolved         what the request leaves open
        evidence_links     the observations the intent rests on

Every one of those is a RECORD, not a computation: the module states
what may be said about an intent and refuses what may not.

Rules for this module
---------------------
- **An inferred preference is never an explicit instruction.**  The two
  live in different lists, and each carries its own `explicit` flag, so
  a reader cannot mistake one for the other.
- **A requirement is objective.**  A statement that cannot be checked
  against a measurement is a goal wearing a requirement's name, and it
  is refused by name.
- **A request names its kind.**  The kinds are one enumeration
  (`REQUEST_KINDS`); a statement matching two of them is refused, never
  resolved by keyword order.
- **The evidence bar is per request and it is executable.**  A request
  whose bar is unmet must DECLARE what is missing; silence is not a
  pass, and the compiler refuses to act on it.
- **Uncertainty is a number and missing evidence is a list.**  Both are
  recorded, so "I am not sure" has a place to live that is not prose.
- **Autonomy is recorded, per request.**  `autonomous`, `needs_clarification`
  or `needs_approval`, with the basis that decided it.

`to_project_model_row` is the projection into the Project Model's
`EditorialIntent` collection - the same shape the Rust protocol declares
(`id`, `brief_ref`, `constraints`, `evidence_links`), so the durable
record and this one are the same record read two ways.  The Project
Model's `intents` collection was a declared slot with no producer until
this module; `commit_intent` is that producer.

This module imports nothing from steps or processes: it reads the state
a run carries and the commissions it holds, the same readers every
consumer has.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Contract version. Bumped only with a migration that reads older ones.
INTENT_VERSION = 1


# ── The editorial request kinds (EDIT-005) ────────────────────────────
#
# One enumeration.  A request the commission names is one of these kinds,
# and the kind decides the request's evidence bar and its default
# autonomy.  The kind is a DECLARED policy, not a reading of prose: the
# cue phrases below are how a statement is matched to a kind, and a
# statement matching none of them is not forced into one.

IMPROVE_PACING = "improve_pacing"
STRENGTHEN_OPENING = "strengthen_opening"
REMOVE_REPETITION = "remove_repetition"
IMPROVE_CONTINUITY = "improve_continuity"
CHOOSE_BETTER_ANGLE = "choose_better_angle"
TIGHTEN_DIALOGUE = "tighten_dialogue"
REPLACE_BROLL = "replace_broll"
CORRECT_CAPTIONS = "correct_captions"
REVISE_GRAPHICS = "revise_graphics"
MIXED_EDIT = "mixed_edit"

REQUEST_KINDS = (
    IMPROVE_PACING,
    STRENGTHEN_OPENING,
    REMOVE_REPETITION,
    IMPROVE_CONTINUITY,
    CHOOSE_BETTER_ANGLE,
    TIGHTEN_DIALOGUE,
    REPLACE_BROLL,
    CORRECT_CAPTIONS,
    REVISE_GRAPHICS,
    MIXED_EDIT,
)


# ── Autonomy (EDIT-007) ──────────────────────────────────────────────
#
# What Ren may do with a request on its own.  The record says which, and
# says what decided it; nothing infers it later.

AUTONOMOUS = "autonomous"
"""Ren may act on this request without asking."""

NEEDS_CLARIFICATION = "needs_clarification"
"""Ren must put one question to the requester before it may act."""

NEEDS_APPROVAL = "needs_approval"
"""Ren may decide, but the decision must be approved before it lands."""

AUTONOMY = (AUTONOMOUS, NEEDS_CLARIFICATION, NEEDS_APPROVAL)


# ── The evidence lanes the bars name (EDIT-006) ──────────────────────
#
# The source-memory lanes `evidence_query` already routes evidence by.
# A bar names the lanes whose evidence must be present before Ren may
# act on the request - not "some evidence", but WHICH evidence.

SOURCE_METADATA_LANE = "m0"
SPEECH_LANE = "m1"
PICTURE_LANE = "m3"
FACE_LANE = "m3b"
SCENE_LANE = "m4"
SOUND_LANE = "m5"
MULTICAM_LANE = "m6"
EVENT_LANE = "m7"
RELATIONSHIP_LANE = "m10"
TEXT_LANE = "ocr_extraction"

EVIDENCE_LANES = (
    SOURCE_METADATA_LANE,
    SPEECH_LANE,
    PICTURE_LANE,
    FACE_LANE,
    SCENE_LANE,
    SOUND_LANE,
    MULTICAM_LANE,
    EVENT_LANE,
    RELATIONSHIP_LANE,
    TEXT_LANE,
)

#: The states a missing lane can be in.  They are the evidence query's
#: own answers, so "never looked" and "looked and confirmed absent" are
#: never the same word.

PRESENT = "present"
CONFIDENTLY_ABSENT = "confidently_absent"
NOT_YET_ANALYSED = "not_yet_analysed"
INCONCLUSIVE = "inconclusive"

MISSING_EVIDENCE_STATUSES = (PRESENT, CONFIDENTLY_ABSENT, NOT_YET_ANALYSED,
                              INCONCLUSIVE)


@dataclass(frozen=True)
class RequestProfile:
    """What one request kind declares about itself.

    `objective` is the kind's own reading of whether the request can be
    checked against a measurement; `autonomy` is the kind's default
    autonomy; `evidence_bar` is the lanes whose evidence must exist
    before Ren may act.  A request may NARROW its bar and may state its
    own autonomy - it may not widen the bar or act alone where the kind
    says otherwise without saying so.
    """

    kind: str
    objective: bool
    autonomy: str
    evidence_bar: tuple[str, ...]
    cues: tuple[str, ...]
    summary: str


REQUEST_PROFILES: dict[str, RequestProfile] = {
    IMPROVE_PACING: RequestProfile(
        kind=IMPROVE_PACING,
        objective=False,
        autonomy=NEEDS_APPROVAL,
        evidence_bar=(SPEECH_LANE, SCENE_LANE),
        cues=("pacing", "pace", "rhythm", "rushed", "tempo"),
        summary="the rhythm of the piece reads wrong",
    ),
    STRENGTHEN_OPENING: RequestProfile(
        kind=STRENGTHEN_OPENING,
        objective=False,
        autonomy=NEEDS_APPROVAL,
        evidence_bar=(SPEECH_LANE, PICTURE_LANE),
        cues=("opening", "intro", "introduction", "hook", "beginning"),
        summary="the first seconds do not earn the rest",
    ),
    REMOVE_REPETITION: RequestProfile(
        kind=REMOVE_REPETITION,
        objective=True,
        autonomy=AUTONOMOUS,
        evidence_bar=(SPEECH_LANE,),
        cues=("repetition", "repeated", "duplicate", "duplicated",
              "redundant", "said twice", "says twice"),
        summary="the same point is made more than once",
    ),
    IMPROVE_CONTINUITY: RequestProfile(
        kind=IMPROVE_CONTINUITY,
        objective=False,
        autonomy=NEEDS_APPROVAL,
        evidence_bar=(PICTURE_LANE, EVENT_LANE),
        cues=("continuity", "jump cut", "discontinuity", "disjointed"),
        summary="the cuts do not join up",
    ),
    CHOOSE_BETTER_ANGLE: RequestProfile(
        kind=CHOOSE_BETTER_ANGLE,
        objective=False,
        autonomy=NEEDS_APPROVAL,
        evidence_bar=(MULTICAM_LANE, FACE_LANE),
        cues=("angle", "camera angle", "better shot", "wrong shot",
              "flipped", "other camera"),
        summary="the shot in use is not the shot that should be",
    ),
    TIGHTEN_DIALOGUE: RequestProfile(
        kind=TIGHTEN_DIALOGUE,
        objective=True,
        autonomy=AUTONOMOUS,
        evidence_bar=(SPEECH_LANE,),
        cues=("tighten dialogue", "tighten the dialogue", "dialogue cleanup",
              "clean up dialogue", "retake", "filler", "disfluency",
              "false start", "long pause", "silence"),
        summary="the dialogue carries dead air and filler",
    ),
    REPLACE_BROLL: RequestProfile(
        kind=REPLACE_BROLL,
        objective=False,
        autonomy=NEEDS_APPROVAL,
        evidence_bar=(PICTURE_LANE, SCENE_LANE),
        cues=("b-roll", "b roll", "broll", "cutaway", "replace the shot"),
        summary="the supporting shot should better fit the spoken moment",
    ),
    CORRECT_CAPTIONS: RequestProfile(
        kind=CORRECT_CAPTIONS,
        objective=True,
        autonomy=NEEDS_APPROVAL,
        evidence_bar=(SPEECH_LANE, TEXT_LANE),
        cues=("correct captions", "caption correction", "fix captions",
              "caption typo", "caption timing", "caption style",
              "subtitle correction"),
        summary="the captions do not match the intended text and timing",
    ),
    REVISE_GRAPHICS: RequestProfile(
        kind=REVISE_GRAPHICS,
        objective=True,
        autonomy=NEEDS_APPROVAL,
        evidence_bar=(PICTURE_LANE,),
        cues=("lower third", "lower-third", "logo", "overlay", "graphic"),
        summary="a named graphic needs a precise revision",
    ),
    MIXED_EDIT: RequestProfile(
        kind=MIXED_EDIT,
        objective=False,
        autonomy=NEEDS_APPROVAL,
        evidence_bar=(SPEECH_LANE, PICTURE_LANE, TEXT_LANE, SOUND_LANE,
                      SCENE_LANE),
        cues=("mixed edit", "combined edit", "combine these edits"),
        summary="one commission spans structural, visual, caption and audio work",
    ),
}


# ── The declaration kinds (EDIT-002, EDIT-003, EDIT-004) ──────────────

REQUIREMENT = "requirement"
"""Explicit, mandatory, checkable."""

PREFERENCE = "preference"
"""Explicit, creative, and allowed to yield to a requirement."""

INFERRED_PREFERENCE = "inferred_preference"
"""Ren's own reading of the footage. Evidence, never an instruction."""

DECLARATION_KINDS = (REQUIREMENT, PREFERENCE, INFERRED_PREFERENCE)


# ── The records ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Declaration:
    """One stated or inferred thing the commission says.

    `explicit` says whether the requester said it.  `objective` says
    whether it can be checked against a measurement.  A requirement that
    is not objective is refused: a statement nobody can verify cannot
    be mandatory.
    """

    declaration_id: str
    statement: str
    kind: str
    explicit: bool
    objective: bool
    source: str = ""
    provenance: str = ""
    confidence: float | None = None

    def to_dict(self) -> dict:
        return {
            "declaration_id": self.declaration_id,
            "statement": self.statement,
            "kind": self.kind,
            "explicit": self.explicit,
            "objective": self.objective,
            "source": self.source,
            "provenance": self.provenance,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Declaration:
        if not isinstance(data, Mapping):
            raise IntentError("a declaration must be a mapping")
        confidence = data.get("confidence")
        return cls(
            declaration_id=str(data.get("declaration_id") or ""),
            statement=str(data.get("statement") or ""),
            kind=str(data.get("kind") or ""),
            explicit=bool(data.get("explicit", True)),
            objective=bool(data.get("objective", False)),
            source=str(data.get("source") or ""),
            provenance=str(data.get("provenance") or ""),
            confidence=(None if confidence is None else float(confidence)),
        )


@dataclass(frozen=True)
class IntentRequest:
    """One editorial request, with the bar and the autonomy it carries.

    `evidence_bar` is the lanes whose evidence must be present.
    `evidence_lanes` is the lanes the evidence actually present came
    from.  `missing_evidence` says what is absent and why - a request
    whose bar is unmet and which declares nothing missing is a request
    pretending it has grounds it does not have.
    """

    request_id: str
    kind: str
    statement: str
    explicit: bool = True
    objective: bool = False
    source: str = ""
    autonomy: str = AUTONOMOUS
    autonomy_basis: str = ""
    evidence_bar: tuple[str, ...] = ()
    evidence_lanes: tuple[str, ...] = ()
    missing_evidence: tuple[dict, ...] = ()
    uncertainty: float | None = None
    question: str = ""

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "statement": self.statement,
            "explicit": self.explicit,
            "objective": self.objective,
            "source": self.source,
            "autonomy": self.autonomy,
            "autonomy_basis": self.autonomy_basis,
            "evidence_bar": list(self.evidence_bar),
            "evidence_lanes": list(self.evidence_lanes),
            "missing_evidence": [dict(row) for row in self.missing_evidence],
            "uncertainty": self.uncertainty,
            "question": self.question,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IntentRequest:
        if not isinstance(data, Mapping):
            raise IntentError("a request must be a mapping")
        uncertainty = data.get("uncertainty")
        return cls(
            request_id=str(data.get("request_id") or ""),
            kind=str(data.get("kind") or ""),
            statement=str(data.get("statement") or ""),
            explicit=bool(data.get("explicit", True)),
            objective=bool(data.get("objective", False)),
            source=str(data.get("source") or ""),
            autonomy=str(data.get("autonomy") or AUTONOMOUS),
            autonomy_basis=str(data.get("autonomy_basis") or ""),
            evidence_bar=tuple(str(lane) for lane in
                               (data.get("evidence_bar") or ())),
            evidence_lanes=tuple(str(lane) for lane in
                                 (data.get("evidence_lanes") or ())),
            missing_evidence=tuple(dict(row) for row in
                                   (data.get("missing_evidence") or ())),
            uncertainty=(None if uncertainty is None else float(uncertainty)),
            question=str(data.get("question") or ""),
        )


@dataclass(frozen=True)
class EditorialIntent:
    """The canonical record of what was asked, and on what grounds."""

    intent_id: str
    desired_outcome: str
    brief_ref: str = ""
    requirements: tuple[Declaration, ...] = ()
    preferences: tuple[Declaration, ...] = ()
    inferred_preferences: tuple[Declaration, ...] = ()
    requests: tuple[IntentRequest, ...] = ()
    references: tuple[dict, ...] = ()
    constraints: tuple[dict, ...] = ()
    unresolved: tuple[dict, ...] = ()
    evidence_links: tuple[str, ...] = ()
    version: int = INTENT_VERSION

    # ── Readers ──────────────────────────────────────────────────────

    def request(self, request_id: str) -> IntentRequest | None:
        """The request with this id, or None."""
        for entry in self.requests:
            if entry.request_id == request_id:
                return entry
        return None

    def declaration(self, declaration_id: str) -> Declaration | None:
        """The declaration with this id, from any of the three lists."""
        for entry in (self.requirements + self.preferences
                      + self.inferred_preferences):
            if entry.declaration_id == declaration_id:
                return entry
        return None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "intent_id": self.intent_id,
            "desired_outcome": self.desired_outcome,
            "brief_ref": self.brief_ref,
            "requirements": [row.to_dict() for row in self.requirements],
            "preferences": [row.to_dict() for row in self.preferences],
            "inferred_preferences": [row.to_dict()
                                     for row in self.inferred_preferences],
            "requests": [row.to_dict() for row in self.requests],
            "references": [dict(row) for row in self.references],
            "constraints": [dict(row) for row in self.constraints],
            "unresolved": [dict(row) for row in self.unresolved],
            "evidence_links": list(self.evidence_links),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EditorialIntent:
        if not isinstance(data, Mapping):
            raise IntentError("an intent must be a mapping")
        return cls(
            intent_id=str(data.get("intent_id") or ""),
            desired_outcome=str(data.get("desired_outcome") or ""),
            brief_ref=str(data.get("brief_ref") or ""),
            requirements=tuple(Declaration.from_dict(row)
                               for row in (data.get("requirements") or ())),
            preferences=tuple(Declaration.from_dict(row)
                              for row in (data.get("preferences") or ())),
            inferred_preferences=tuple(
                Declaration.from_dict(row)
                for row in (data.get("inferred_preferences") or ())),
            requests=tuple(IntentRequest.from_dict(row)
                           for row in (data.get("requests") or ())),
            references=tuple(dict(row) for row in (data.get("references") or ())),
            constraints=tuple(dict(row) for row in (data.get("constraints") or ())),
            unresolved=tuple(dict(row) for row in (data.get("unresolved") or ())),
            evidence_links=tuple(str(link) for link in
                                 (data.get("evidence_links") or ())),
            version=int(data.get("version") or INTENT_VERSION),
        )


class IntentError(ValueError):
    """An intent that cannot be what it claims to be, refused by name."""


# ── Matching a statement to a request kind ────────────────────────────


def match_request_kinds(statement: str) -> tuple[str, ...]:
    """Every request kind whose cues the statement names, in kind order.

    More than one match is an AMBIGUITY, not a choice: the caller states
    the kind.  Zero matches is not a failure either - a request the
    commission names in its own words may be none of the declared kinds.
    """
    text = str(statement or "").lower()
    matched = []
    for kind in REQUEST_KINDS:
        profile = REQUEST_PROFILES[kind]
        if any(cue in text for cue in profile.cues):
            matched.append(kind)
    return tuple(matched)


def request_kind(statement: str) -> str:
    """The one request kind a statement names, or a refusal naming them.

    A statement matching two kinds is refused rather than resolved by
    keyword order: the choice between them is the commissioner's, and
    the two kinds carry different evidence bars.
    """
    matched = match_request_kinds(statement)
    if not matched:
        raise IntentError(
            f"the statement names none of the request kinds {list(REQUEST_KINDS)}")
    if len(matched) > 1:
        raise IntentError(
            f"the statement names more than one request kind: {list(matched)}")
    return matched[0]


# ── Building the intent from a commission ─────────────────────────────


def _declaration(entry: Mapping[str, Any], index: int, kind: str,
                 *, explicit: bool, objective: bool) -> Declaration:
    statement = str(entry.get("statement") or "").strip()
    if not statement:
        raise IntentError(
            f"the commission's {kind}[{index}] states nothing")
    return Declaration(
        declaration_id=str(entry.get("id") or f"{kind}:{index}"),
        statement=statement,
        kind=kind,
        explicit=explicit,
        # A preference may declare itself objective when it names
        # something measurable ("prefer 16:9"); a requirement that
        # declares itself subjective is refused by the validator, because
        # an uncheckable mandatory statement is a goal in disguise.
        objective=bool(entry.get("objective", objective)),
        source=str(entry.get("source") or ""),
        provenance=str(entry.get("provenance") or ""),
        confidence=(None if entry.get("confidence") is None
                    else float(entry["confidence"])),
    )


def _inferred(entry: Mapping[str, Any], index: int) -> Declaration:
    statement = str(entry.get("statement") or "").strip()
    if not statement:
        raise IntentError(f"the inferred preference[{index}] states nothing")
    confidence = entry.get("confidence")
    return Declaration(
        declaration_id=str(entry.get("id") or f"{INFERRED_PREFERENCE}:{index}"),
        statement=statement,
        kind=INFERRED_PREFERENCE,
        explicit=False,
        objective=bool(entry.get("objective", False)),
        source=str(entry.get("source") or "inference"),
        provenance=str(entry.get("provenance") or ""),
        confidence=(None if confidence is None else float(confidence)),
    )


def _request(entry: Mapping[str, Any], index: int) -> IntentRequest:
    statement = str(entry.get("statement") or "").strip()
    if not statement:
        raise IntentError(f"the request[{index}] states nothing")
    kind = str(entry.get("kind") or "").strip()
    if not kind:
        kind = request_kind(statement)
    if kind not in REQUEST_PROFILES:
        raise IntentError(
            f"the request[{index}] names kind {kind!r}; one of "
            f"{list(REQUEST_KINDS)}")
    profile = REQUEST_PROFILES[kind]
    bar = tuple(str(lane) for lane in
                (entry.get("evidence_bar") or profile.evidence_bar))
    autonomy = str(entry.get("autonomy") or profile.autonomy)
    basis = str(entry.get("autonomy_basis")
                or f"the declared default for {kind}")
    uncertainty = entry.get("uncertainty")
    lanes = tuple(str(lane) for lane in (entry.get("evidence_lanes") or ()))
    declared = [dict(row) for row in (entry.get("missing_evidence") or ())]
    named = {str(row.get("lane")) for row in declared}
    # The gap is made EXPLICIT here, not left to the reader: a lane the
    # bar names and the evidence lacks is recorded as not yet analysed,
    # unless the caller measured its absence and said so.
    for lane in bar:
        if lane in lanes or lane in named:
            continue
        declared.append({"lane": lane, "status": NOT_YET_ANALYSED})
    return IntentRequest(
        request_id=str(entry.get("id") or f"request:{index}"),
        kind=kind,
        statement=statement,
        explicit=bool(entry.get("explicit", True)),
        objective=bool(entry.get("objective", profile.objective)),
        source=str(entry.get("source") or ""),
        autonomy=autonomy,
        autonomy_basis=basis,
        evidence_bar=bar,
        evidence_lanes=lanes,
        missing_evidence=tuple(declared),
        uncertainty=(None if uncertainty is None else float(uncertainty)),
        question=str(entry.get("question") or ""),
    )


def _desired_outcome(spec: Mapping[str, Any], override: str) -> str:
    """What the commission wants the viewer to have got.

    The narrative goals are the outcome; the desired CONTENT is what
    must appear to achieve it, which is a different thing and stays a
    separate list.  With no goal stated, the content is the best
    available reading of the outcome.
    """
    if override:
        return str(override).strip()
    goals = [str(item).strip() for item in (spec.get("narrative_goals") or ())
             if str(item).strip()]
    if goals:
        return "; ".join(goals)
    desired = [str(item).strip() for item in (spec.get("desired_content") or ())
               if str(item).strip()]
    return "; ".join(desired)


def build_intent(*, intent_id: str,
                 editorial_spec: Mapping[str, Any] | None = None,
                 brief_ref: str = "",
                 desired_outcome: str = "",
                 requests: Sequence[Mapping[str, Any]] = (),
                 inferred: Sequence[Mapping[str, Any]] = (),
                 evidence_links: Iterable[str] = (),
                 intent_version: int = INTENT_VERSION) -> EditorialIntent:
    """Build the canonical intent from the commission's own reading.

    `editorial_spec` is the structured commission step 2.01 already
    writes: its `requirements` and `preferences` are the user's own
    words, so they arrive EXPLICIT.  `inferred` carries what Ren read
    out of the footage, and it arrives inferred - never mixed in with
    the user's words.
    """
    spec = dict(editorial_spec or {})
    requirements = tuple(
        _declaration(entry, index, REQUIREMENT,
                     explicit=True, objective=True)
        for index, entry in enumerate(spec.get("requirements") or ()))
    preferences = tuple(
        _declaration(entry, index, PREFERENCE,
                     explicit=True, objective=False)
        for index, entry in enumerate(spec.get("preferences") or ()))
    inferred_preferences = tuple(
        _inferred(entry, index)
        for index, entry in enumerate(inferred or ()))
    built_requests = tuple(
        _request(entry, index) for index, entry in enumerate(requests or ()))
    constraints = []
    duration = spec.get("target_duration")
    if duration:
        constraints.append({"kind": "target_duration", "value": dict(duration)})
    for field in ("platform", "audience"):
        value = spec.get(field)
        if value:
            constraints.append({"kind": field, "value": str(value)})
    pacing = spec.get("pacing") or []
    if pacing:
        constraints.append({"kind": "pacing", "value": [str(p) for p in pacing]})
    subjects = spec.get("subjects") or {}
    if subjects:
        constraints.append({"kind": "subjects", "value": dict(subjects)})
    prohibited = spec.get("prohibited_content") or []
    if prohibited:
        constraints.append({"kind": "prohibited_content",
                            "value": [str(p) for p in prohibited]})
    return EditorialIntent(
        intent_id=str(intent_id),
        desired_outcome=_desired_outcome(spec, desired_outcome),
        brief_ref=str(brief_ref or ""),
        requirements=requirements,
        preferences=preferences,
        inferred_preferences=inferred_preferences,
        requests=built_requests,
        references=tuple(dict(row) for row in (spec.get("references") or ())),
        constraints=tuple(constraints),
        unresolved=tuple(dict(row)
                         for row in (spec.get("unresolved_decisions") or ())),
        evidence_links=tuple(str(link) for link in (evidence_links or ())),
        version=int(intent_version),
    )


def from_state(state: Mapping[str, Any], *, intent_id: str = "intent:1",
               brief_ref: str = "",
               requests: Sequence[Mapping[str, Any]] = (),
               inferred: Sequence[Mapping[str, Any]] = (),
               ) -> EditorialIntent:
    """Read the canonical intent out of a run's state.

    The commission lives in `intent_model.editorial_spec`; the brief's
    own words live beside it.  Nothing is inferred here: an intent built
    from a state carries what the commission stated, and a run that
    stated nothing yields an intent with nothing in it - which the
    validator names rather than filling in.
    """
    intent_model = state.get("intent_model") or {}
    spec = intent_model.get("editorial_spec") or {}
    if not brief_ref:
        brief_ref = str(state.get("creative_brief") or "")
    return build_intent(
        intent_id=intent_id,
        editorial_spec=spec,
        brief_ref=brief_ref,
        requests=requests,
        inferred=inferred,
    )


def inferred_from_view(view: Mapping[str, Any]) -> list[dict]:
    """The inference-sourced declarations of a scoped intent view.

    `scoped_intent.resolve` already separates the sources: a creator,
    brand or project declaration outranks an inference, and an inference
    can never be marked explicit.  This reads the inference rows out of
    that view so the canonical intent can hold them as what they are -
    Ren's own reading, never the requester's instruction.
    """
    rows = []
    for entry in (view or {}).get("declarations") or ():
        if not isinstance(entry, Mapping):
            continue
        if entry.get("source") != "inference":
            continue
        rows.append({
            "id": str(entry.get("key") or ""),
            "statement": entry.get("value"),
            "source": "inference",
            "provenance": str(entry.get("provenance") or ""),
            "confidence": entry.get("confidence"),
        })
    return rows


# ── The contract over an intent ───────────────────────────────────────


def validate_intent(intent: EditorialIntent) -> list[str]:
    """Every defect in an intent, as a list of problems. Empty is valid.

    Each problem NAMES the defect it prevents, so a reader can tell a
    requirement that cannot be checked from a preference that has been
    promoted to one.
    """
    if not isinstance(intent, EditorialIntent):
        raise IntentError("validate_intent needs an EditorialIntent")
    problems: list[str] = []

    if not intent.intent_id:
        problems.append("intent: no intent_id")
    if not intent.desired_outcome:
        problems.append("intent: no desired_outcome - the commission asks "
                        "for a change and does not say what it should be")
    if intent.version != INTENT_VERSION:
        problems.append(f"intent: version {intent.version}, this reader "
                        f"speaks {INTENT_VERSION}")

    seen_ids: set[str] = set()
    for entry, kind in ((intent.requirements, REQUIREMENT),
                        (intent.preferences, PREFERENCE),
                        (intent.inferred_preferences, INFERRED_PREFERENCE)):
        for declaration in entry:
            label = declaration.declaration_id or "<no declaration_id>"
            if not declaration.declaration_id:
                problems.append(f"{kind} {label}: no declaration_id")
            elif declaration.declaration_id in seen_ids:
                problems.append(
                    f"{kind} {label}: duplicate declaration_id")
            else:
                seen_ids.add(declaration.declaration_id)
            if not declaration.statement:
                problems.append(f"{kind} {label}: no statement")
            if declaration.kind != kind:
                problems.append(
                    f"{kind} {label}: declares kind {declaration.kind!r}")
            if kind == INFERRED_PREFERENCE:
                if declaration.explicit:
                    problems.append(
                        f"{kind} {label}: marked explicit - an inference is "
                        "evidence, never the requester's instruction")
            elif not declaration.explicit:
                problems.append(
                    f"{kind} {label}: marked inferred - the requester's own "
                    "words cannot be an inference")
            if kind == REQUIREMENT and not declaration.objective:
                problems.append(
                    f"{kind} {label}: not objective - a mandatory statement "
                    "nobody can check is a goal wearing a requirement's name")

    for request in intent.requests:
        label = request.request_id or "<no request_id>"
        if not request.request_id:
            problems.append(f"request {label}: no request_id")
        if not request.statement:
            problems.append(f"request {label}: no statement")
        if request.kind not in REQUEST_PROFILES:
            problems.append(f"request {label}: names kind {request.kind!r}; "
                            f"one of {list(REQUEST_KINDS)}")
            continue
        profile = REQUEST_PROFILES[request.kind]
        for lane in request.evidence_bar:
            if lane not in EVIDENCE_LANES:
                problems.append(
                    f"request {label}: evidence bar names lane {lane!r}, "
                    f"which is not a lane")
        widened = [lane for lane in request.evidence_bar
                   if lane not in profile.evidence_bar]
        if widened:
            problems.append(
                f"request {label}: evidence bar names {widened}, which "
                f"{request.kind} does not require - a bar may be narrowed, "
                "not widened")
        for lane in request.evidence_lanes:
            if lane not in EVIDENCE_LANES:
                problems.append(
                    f"request {label}: evidence names lane {lane!r}, which "
                    "is not a lane")
        if request.autonomy not in AUTONOMY:
            problems.append(
                f"request {label}: autonomy {request.autonomy!r}; one of "
                f"{list(AUTONOMY)}")
        if request.autonomy == NEEDS_CLARIFICATION and not request.question:
            problems.append(
                f"request {label}: needs clarification and asks nothing")
        if request.uncertainty is not None and not (
                0.0 <= request.uncertainty <= 1.0):
            problems.append(
                f"request {label}: uncertainty {request.uncertainty} "
                "outside 0..1")
        missing = missing_lanes(request)
        declared = {str(row.get("lane")) for row in request.missing_evidence
                    if isinstance(row, Mapping)}
        for lane in missing:
            if lane not in declared:
                problems.append(
                    f"request {label}: evidence bar is unmet ({lane} is "
                    "absent) and the request declares nothing about it - "
                    "silence is not evidence")
        for row in request.missing_evidence:
            if not isinstance(row, Mapping) or not row.get("lane"):
                problems.append(
                    f"request {label}: a missing-evidence row names no lane")
                continue
            status = row.get("status")
            if status is not None and status not in MISSING_EVIDENCE_STATUSES:
                problems.append(
                    f"request {label}: missing evidence for lane "
                    f"{row.get('lane')!r} has status {status!r}; one of "
                    f"{list(MISSING_EVIDENCE_STATUSES)}")
            if row.get("lane") in request.evidence_lanes:
                problems.append(
                    f"request {label}: lane {row.get('lane')!r} is declared "
                    "both present and missing")

    for index, reference in enumerate(intent.references):
        if not reference.get("description") or not reference.get("source"):
            problems.append(
                f"reference[{index}]: needs description and source")
    for index, entry in enumerate(intent.unresolved):
        if not entry.get("question") or not entry.get("why_it_matters"):
            problems.append(
                f"unresolved[{index}]: needs question and why_it_matters")
    return problems


def missing_lanes(request: IntentRequest) -> tuple[str, ...]:
    """The lanes the request's evidence bar names and the evidence lacks."""
    present = set(request.evidence_lanes)
    return tuple(lane for lane in request.evidence_bar if lane not in present)


def evidence_sufficient(request: IntentRequest) -> bool:
    """Whether the request's evidence bar is met."""
    return not missing_lanes(request)


# ── The Project Model projection ──────────────────────────────────────
#
# The Rust protocol declares `EditorialIntent` as
# `id`, `brief_ref`, `constraints` and `evidence_links`.  `constraints`
# is a list of arbitrary values, which is where this record's entries
# live: one value per declaration, request, reference, constraint and
# open question, each naming its kind.  The projection is therefore
# lossless in one direction and read back by `from_project_model_row`.

CONSTRAINT_ENTRY = "entry"
"""The key every projected entry carries, naming what kind of entry it is."""

ENTRY_REQUIREMENT = REQUIREMENT
ENTRY_PREFERENCE = PREFERENCE
ENTRY_INFERRED_PREFERENCE = INFERRED_PREFERENCE
ENTRY_REQUEST = "request"
ENTRY_REFERENCE = "reference"
ENTRY_CONSTRAINT = "constraint"
ENTRY_UNRESOLVED = "unresolved"
ENTRY_DESIRED_OUTCOME = "desired_outcome"

PROJECT_MODEL_ENTRY_KINDS = (
    ENTRY_DESIRED_OUTCOME,
    ENTRY_REQUIREMENT,
    ENTRY_PREFERENCE,
    ENTRY_INFERRED_PREFERENCE,
    ENTRY_REQUEST,
    ENTRY_REFERENCE,
    ENTRY_CONSTRAINT,
    ENTRY_UNRESOLVED,
)


def to_project_model_row(intent: EditorialIntent) -> dict:
    """The Project Model row for this intent.

    The row is the durable record; `from_project_model_row` reads it
    back into the same record, so the two are one representation read
    two ways rather than two representations that can drift.
    """
    entries: list[dict] = []
    entries.append({CONSTRAINT_ENTRY: ENTRY_DESIRED_OUTCOME,
                    "outcome": intent.desired_outcome})
    for declaration in intent.requirements:
        entries.append({CONSTRAINT_ENTRY: ENTRY_REQUIREMENT,
                        **declaration.to_dict()})
    for declaration in intent.preferences:
        entries.append({CONSTRAINT_ENTRY: ENTRY_PREFERENCE,
                        **declaration.to_dict()})
    for declaration in intent.inferred_preferences:
        entries.append({CONSTRAINT_ENTRY: ENTRY_INFERRED_PREFERENCE,
                        **declaration.to_dict()})
    for request in intent.requests:
        entries.append({CONSTRAINT_ENTRY: ENTRY_REQUEST, **request.to_dict()})
    for reference in intent.references:
        entries.append({CONSTRAINT_ENTRY: ENTRY_REFERENCE, **dict(reference)})
    for constraint in intent.constraints:
        entries.append({CONSTRAINT_ENTRY: ENTRY_CONSTRAINT, **dict(constraint)})
    for entry in intent.unresolved:
        entries.append({CONSTRAINT_ENTRY: ENTRY_UNRESOLVED, **dict(entry)})
    return {
        "id": intent.intent_id,
        "brief_ref": intent.brief_ref or None,
        "constraints": entries,
        "evidence_links": list(intent.evidence_links),
    }


def _without_entry(entry: Mapping[str, Any]) -> dict:
    """The entry as it was, without the key that names its kind."""
    return {key: value for key, value in entry.items()
            if key != CONSTRAINT_ENTRY}


def from_project_model_row(row: Mapping[str, Any]) -> EditorialIntent:
    """Read the canonical intent back out of a Project Model row.

    A row whose entries carry no `entry` key is not this record - it is
    some other use of the same slot - and is refused rather than read
    as an empty intent.
    """
    if not isinstance(row, Mapping):
        raise IntentError("a project model intent must be a mapping")
    entries = [entry for entry in (row.get("constraints") or [])
               if isinstance(entry, Mapping)]
    mine = [entry for entry in entries if CONSTRAINT_ENTRY in entry]
    if not mine:
        raise IntentError(
            f"intent {row.get('id')!r} carries no canonical entries - a row "
            "somebody else wrote is not this record")
    if len(mine) != len(entries):
        raise IntentError(
            f"intent {row.get('id')!r} mixes canonical entries with entries "
            "this reader does not own - a half-canonical row is ambiguous")
    unknown = [entry for entry in mine
               if entry.get(CONSTRAINT_ENTRY) not in
               PROJECT_MODEL_ENTRY_KINDS]
    if unknown:
        raise IntentError(
            f"intent {row.get('id')!r} carries {len(unknown)} entries this "
            "reader does not own")
    requirements, preferences, inferred = [], [], []
    requests, references, constraints, unresolved = [], [], [], []
    outcome = ""
    for entry in mine:
        kind = entry.get(CONSTRAINT_ENTRY)
        if kind == ENTRY_DESIRED_OUTCOME:
            outcome = str(entry.get("outcome") or "")
        elif kind == ENTRY_REQUEST:
            requests.append(IntentRequest.from_dict(entry))
        elif kind == ENTRY_REFERENCE:
            references.append(_without_entry(entry))
        elif kind == ENTRY_CONSTRAINT:
            constraints.append(_without_entry(entry))
        elif kind == ENTRY_UNRESOLVED:
            unresolved.append(_without_entry(entry))
        elif kind == ENTRY_REQUIREMENT:
            requirements.append(Declaration.from_dict(entry))
        elif kind == ENTRY_PREFERENCE:
            preferences.append(Declaration.from_dict(entry))
        elif kind == ENTRY_INFERRED_PREFERENCE:
            inferred.append(Declaration.from_dict(entry))
    return EditorialIntent(
        intent_id=str(row.get("id") or ""),
        desired_outcome=outcome,
        brief_ref=str(row.get("brief_ref") or ""),
        requirements=tuple(requirements),
        preferences=tuple(preferences),
        inferred_preferences=tuple(inferred),
        requests=tuple(requests),
        references=tuple(references),
        constraints=tuple(constraints),
        unresolved=tuple(unresolved),
        evidence_links=tuple(str(link) for link in
                             (row.get("evidence_links") or ())),
    )


def commit_intent(project_folder: str, intent: EditorialIntent, *,
                  expected_revision: int | None = None,
                  author: str = "ren",
                  reason: str = "editorial intent") -> int:
    """Write the intent into the project's Project Model.

    The `intents` collection was a declared slot with no producer; this
    is the producer.  The write is a record-level change under the same
    compare-and-swap the rest of the model uses, so a stale revision
    refuses rather than overwriting a newer record.
    """
    from library.tools import project_model_store

    problems = validate_intent(intent)
    if problems:
        raise IntentError(
            "the intent is not valid and is not written: "
            + "; ".join(problems))
    project = Path(project_folder)
    revision = (int(expected_revision) if expected_revision is not None
                else project_model_store.project_model_revision(project))
    return project_model_store.commit_project_model_records(
        project,
        [{"collection": "intents", "id": intent.intent_id,
          "operation": "append" if _intent_absent(project, intent.intent_id)
          else "update",
          "record": to_project_model_row(intent)}],
        expected_revision=revision, author=author, reason=reason)


def _intent_absent(project: Path, intent_id: str) -> bool:
    from library.tools import project_model_store

    try:
        _revision, model = project_model_store.load_project_model(project)
    except (OSError, ValueError, RuntimeError):
        return True
    return intent_id not in {row.get("id")
                             for row in model.get("intents", [])}


# ── The CLI ───────────────────────────────────────────────────────────


def _state_of(project_folder: str) -> dict:
    from library.tools import project_layout

    path = project_layout.ProjectLayout(project_folder).pipeline_data_path
    if not path.is_file():
        raise IntentError(f"the project has no pipeline_data.json: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IntentError(f"cannot read {path}: {exc}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.editorial_intent",
        description="the canonical editorial intent of a project: what was "
                    "asked, what was inferred, and what evidence each "
                    "request still needs")
    parser.add_argument("project", help="the project folder")
    parser.add_argument("--json", action="store_true",
                        help="print the record as JSON")
    parser.add_argument("--commit", action="store_true",
                        help="write the intent into the Project Model")
    args = parser.parse_args(argv)

    state = _state_of(args.project)
    intent = from_state(state, brief_ref=str(state.get("creative_brief") or ""))
    problems = validate_intent(intent)
    if args.commit:
        commit_intent(args.project, intent)
    if args.json:
        print(json.dumps({"intent": intent.to_dict(),
                          "problems": problems}, indent=2,
                         ensure_ascii=False))
        return 1 if problems else 0
    print(f"intent {intent.intent_id}")
    print(f"  desired outcome: {intent.desired_outcome or '(none stated)'}")
    print(f"  requirements: {len(intent.requirements)}")
    print(f"  preferences: {len(intent.preferences)}")
    print(f"  inferred preferences: {len(intent.inferred_preferences)}")
    print(f"  requests: {len(intent.requests)}")
    for request in intent.requests:
        absent = missing_lanes(request)
        print(f"    - {request.request_id} [{request.kind}] "
              f"autonomy={request.autonomy}"
              + (f" missing={list(absent)}" if absent else ""))
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
