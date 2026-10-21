"""The canonical `EditorialIntent` - what was asked, apart from what will be done.

The gap this closes
-------------------
The commission reaches Ren as prose.  A brief is a markdown file; the
planner reads it and writes a direction; the direction's structured
half (`intent_model.editorial_spec`) keeps requirements and preferences
apart, and `scoped_intent` resolves declarations by precedence.  What
does NOT exist anywhere is ONE record of the request itself that says:

    - what the user REQUIRED, what they PREFERRED, and what Ren INFERRED
    - what the commission WANTS (its goals), and which of those goals
      Ren read out of the footage rather than being told
    - which of those are objective (checkable) and which are subjective
    - WHICH EDITORIAL REQUEST each one is (improve pacing, strengthen an
      opening, remove repetition, improve continuity, choose a better
      angle, tighten dialogue, replace B-roll, correct captions, revise
      graphics, or combine edit domains)
    - what evidence must exist before Ren may act on that request
    - whether Ren may act alone, must ask, or must wait for approval
    - what evidence is MISSING and how uncertain the answer is
    - what each request ACTS ON, what it must produce, and what must
      not change while it happens

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
        goals              what the commission wants to be true
        inferred_goals     a goal Ren read out of the commission
        requirements       explicit, mandatory, objective
        preferences        explicit, creative, may yield to a requirement
        inferred_preferences   Ren's own reading, never a user instruction
        requests           the editorial requests, each with its own bar
        references         what the request pointed at
        constraints        the commission's objective limits
        unresolved         what the request leaves open
        evidence_links     the observations the intent rests on

What one request carries
------------------------
A requested change is not a sentence.  The record carries, for each
one:

    kind               which editorial request it is, or NOTHING when
                       the commission's own words name none - an
                       unsupported request stays representable
    candidates         the kinds the wording fits, when it fits more
                       than one - an ambiguity is a record, not a guess
    statement          the requester's words, unedited
    target_roles       what the change acts on
    explicit_constraints   what the requester bound it by
    preservation       what must not change while it happens - a
                       constraint whose subject is untouched, and a
                       first-class field rather than prose
    outputs            what the change must produce
    accepted_autonomy  the requester's own words granting latitude

Every one of those is the REQUESTER'S wording.  Ren classifies nothing
here: a target role is not sorted into a taxonomy of Ren's own, because
sorting it would be a judgement the commission never made.

Every field above is a RECORD, not a computation: the module states
what may be said about an intent and refuses what may not.

Rules for this module
---------------------
- **An inferred preference is never an explicit instruction.**  The two
  live in different lists, and each carries its own `explicit` flag, so
  a reader cannot mistake one for the other.
- **A goal is a goal, not a requirement.**  What the commission wants
  to be true is not mandatory until a requirement says so, and an
  inferred goal is never the requester's instruction.
- **A request names its kind, or says that it does not.**  The kinds are
  one enumeration (`REQUEST_KINDS`); a statement matching two of them
  is recorded as an ambiguity with its candidates, never resolved by
  keyword order, and a statement matching none of them is recorded as
  an unsupported request - neither is forced into a kind it does not
  name.
- **A requirement is objective.**  A statement that cannot be checked
  against a measurement is a goal wearing a requirement's name, and it
  is refused by name.
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

UNDETERMINED_KIND = ""
"""The kind of a request whose own words name none of the declared kinds.

It is a value, not a hole: a request the commission states in words
that fit no kind stays in the record, carrying its candidates, and
cannot act alone.
"""


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

GOAL = "goal"
"""What the commission wants to be true. A goal, not a mandate."""

INFERRED_GOAL = "inferred_goal"
"""A goal Ren read out of the commission. Evidence, never an instruction."""

DECLARATION_KINDS = (REQUIREMENT, PREFERENCE, INFERRED_PREFERENCE,
                     GOAL, INFERRED_GOAL)

#: The kinds that are inferences: an inferred goal is a goal Ren read
#: out of the commission, so it lives beside the other inferred kind and
#: never in the explicit one.
INFERRED_KINDS = (INFERRED_PREFERENCE, INFERRED_GOAL)


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

    `kind` is the editorial request this is, and it may be
    `UNDETERMINED_KIND`: a request the commission words in a way that
    names no kind stays in the record with its `candidates`, rather
    than being forced into a kind it does not name or refused out of
    it.  The fields after it are the REQUESTER'S words - what the
    change acts on, what bounds it, what must not change, what it must
    produce, and the latitude the requester granted.
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
    candidates: tuple[str, ...] = ()
    target_roles: tuple[str, ...] = ()
    explicit_constraints: tuple[str, ...] = ()
    preservation: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    accepted_autonomy: str = ""

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
            "candidates": list(self.candidates),
            "target_roles": list(self.target_roles),
            "explicit_constraints": list(self.explicit_constraints),
            "preservation": list(self.preservation),
            "outputs": list(self.outputs),
            "accepted_autonomy": self.accepted_autonomy,
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
            candidates=tuple(str(kind) for kind in (data.get("candidates") or ())),
            target_roles=tuple(str(role) for role in
                               (data.get("target_roles") or ())),
            explicit_constraints=tuple(str(text) for text in
                                       (data.get("explicit_constraints") or ())),
            preservation=tuple(str(text) for text in
                               (data.get("preservation") or ())),
            outputs=tuple(str(text) for text in (data.get("outputs") or ())),
            accepted_autonomy=str(data.get("accepted_autonomy") or ""),
        )


@dataclass(frozen=True)
class EditorialIntent:
    """The canonical record of what was asked, and on what grounds."""

    intent_id: str
    desired_outcome: str
    brief_ref: str = ""
    goals: tuple[Declaration, ...] = ()
    inferred_goals: tuple[Declaration, ...] = ()
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
        """The declaration with this id, from any of the five lists."""
        for entry in (self.goals + self.inferred_goals + self.requirements
                      + self.preferences + self.inferred_preferences):
            if entry.declaration_id == declaration_id:
                return entry
        return None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "intent_id": self.intent_id,
            "desired_outcome": self.desired_outcome,
            "brief_ref": self.brief_ref,
            "goals": [row.to_dict() for row in self.goals],
            "inferred_goals": [row.to_dict() for row in self.inferred_goals],
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
            goals=tuple(Declaration.from_dict(row)
                        for row in (data.get("goals") or ())),
            inferred_goals=tuple(Declaration.from_dict(row)
                                 for row in (data.get("inferred_goals") or ())),
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


def _goal(entry: Any, index: int) -> Declaration:
    """One goal the commission states, or one Ren read out of it.

    A goal is what the commission wants to be true.  It is not a
    mandate: nothing is mandatory until a requirement says so, and the
    goal keeps the requester's wording so a reader can find it again.

    A goal marked `explicit: false` is Ren's own reading - what the
    footage needs - and it is recorded as the inference it is, with the
    provenance that supports it.  It never becomes the requester's
    instruction by being listed beside one.
    """
    if isinstance(entry, str):
        statement = entry.strip()
        identifier = ""
        source = ""
        provenance = ""
        objective = False
        explicit = True
    else:
        statement = str(entry.get("statement") or "").strip()
        identifier = str(entry.get("id") or "")
        source = str(entry.get("source") or "")
        provenance = str(entry.get("provenance") or "")
        objective = bool(entry.get("objective", False))
        explicit = bool(entry.get("explicit", True))
    if not statement:
        raise IntentError(f"the commission's goal[{index}] states nothing")
    kind = GOAL if explicit else INFERRED_GOAL
    return Declaration(
        declaration_id=identifier or f"{kind}:{index}",
        statement=statement,
        kind=kind,
        explicit=explicit,
        objective=objective,
        source=source if explicit else (source or "inference"),
        provenance=provenance,
    )


def _inferred(entry: Mapping[str, Any], index: int,
              kind: str = INFERRED_PREFERENCE) -> Declaration:
    statement = str(entry.get("statement") or "").strip()
    if not statement:
        raise IntentError(f"the inferred {kind}[{index}] states nothing")
    confidence = entry.get("confidence")
    return Declaration(
        declaration_id=str(entry.get("id") or f"{kind}:{index}"),
        statement=statement,
        kind=kind,
        explicit=False,
        objective=bool(entry.get("objective", False)),
        source=str(entry.get("source") or "inference"),
        provenance=str(entry.get("provenance") or ""),
        confidence=(None if confidence is None else float(confidence)),
    )


def _clarification_question(candidates: Sequence[str]) -> str:
    """The question an undetermined request asks.

    A request whose own words name no kind cannot act alone, and the
    record says so by asking - not by guessing which kind it meant.
    """
    if candidates:
        return ("which change does this request mean: "
                + ", ".join(candidates) + "?")
    return "what change does this request ask for?"


def _request(entry: Mapping[str, Any], index: int) -> IntentRequest:
    statement = str(entry.get("statement") or "").strip()
    if not statement:
        raise IntentError(f"the request[{index}] states nothing")
    kind = str(entry.get("kind") or "").strip()
    candidates = tuple(str(name) for name in (entry.get("candidates") or ()))
    unknown = [name for name in candidates if name not in REQUEST_PROFILES]
    if unknown:
        raise IntentError(
            f"the request[{index}] names candidate kinds {unknown}; one of "
            f"{list(REQUEST_KINDS)}")
    if kind and kind not in REQUEST_PROFILES:
        raise IntentError(
            f"the request[{index}] names kind {kind!r}; one of "
            f"{list(REQUEST_KINDS)}")
    if not kind:
        matched = match_request_kinds(statement)
        if len(matched) == 1:
            kind = matched[0]
        else:
            # More than one kind fits is an AMBIGUITY and none fits is
            # an UNSUPPORTED request.  Both are recorded: the wording
            # and the candidates it could have meant.  Neither is
            # resolved here - the choice is the commissioner's.
            candidates = candidates or matched
            kind = UNDETERMINED_KIND
    profile = REQUEST_PROFILES.get(kind)
    if profile is not None:
        bar = tuple(str(lane) for lane in
                    (entry.get("evidence_bar") or profile.evidence_bar))
        autonomy = str(entry.get("autonomy") or profile.autonomy)
        basis = str(entry.get("autonomy_basis")
                    or f"the declared default for {kind}")
        default_objective = profile.objective
    else:
        # No kind means no declared bar to inherit, and no default
        # autonomy to inherit: the request cannot act alone until
        # somebody says which change it is.
        bar = tuple(str(lane) for lane in (entry.get("evidence_bar") or ()))
        autonomy = str(entry.get("autonomy") or NEEDS_CLARIFICATION)
        basis = str(entry.get("autonomy_basis")
                    or ("the request names none of the declared request "
                        "kinds, so it cannot act alone"))
        default_objective = False
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
    question = str(entry.get("question") or "")
    if not question and profile is None and autonomy == NEEDS_CLARIFICATION:
        # Only the record's OWN undetermined kind asks here: a caller
        # that declares `needs_clarification` and asks nothing has
        # hidden a question, and the validator says so.
        question = _clarification_question(candidates)
    return IntentRequest(
        request_id=str(entry.get("id") or f"request:{index}"),
        kind=kind,
        statement=statement,
        explicit=bool(entry.get("explicit", True)),
        objective=bool(entry.get("objective", default_objective)),
        source=str(entry.get("source") or ""),
        autonomy=autonomy,
        autonomy_basis=basis,
        evidence_bar=bar,
        evidence_lanes=lanes,
        missing_evidence=tuple(declared),
        uncertainty=(None if uncertainty is None else float(uncertainty)),
        question=question,
        candidates=candidates,
        target_roles=tuple(str(role) for role in
                           (entry.get("target_roles") or ())),
        explicit_constraints=tuple(str(text) for text in
                                   (entry.get("explicit_constraints") or ())),
        preservation=tuple(str(text) for text in
                           (entry.get("preservation") or ())),
        outputs=tuple(str(text) for text in (entry.get("outputs") or ())),
        accepted_autonomy=str(entry.get("accepted_autonomy") or ""),
    )


def _goal_statements(spec: Mapping[str, Any]) -> list[str]:
    """The commission's goals, read from either shape they may take.

    A goal is a plain string in the spec's own reading, or an object
    carrying the requester's source wording beside it.  A goal Ren
    inferred is not the commission's outcome and does not join it.
    """
    statements = []
    for item in (spec.get("narrative_goals") or ()):
        if isinstance(item, str):
            text = item.strip()
        else:
            if not bool(item.get("explicit", True)):
                continue
            text = str(item.get("statement") or "").strip()
        if text:
            statements.append(text)
    return statements


def _desired_outcome(spec: Mapping[str, Any], override: str,
                     requests: Sequence[IntentRequest] = ()) -> str:
    """What the commission wants the viewer to have got.

    The narrative goals are the outcome; the desired CONTENT is what
    must appear to achieve it, which is a different thing and stays a
    separate list.  With no goal stated, the content is the best
    available reading of the outcome, and with no content either, the
    changes the commission asked for are what it wants to be different:
    a commission that names the changes it wants has said what it
    wants, and a record that refuses it says the opposite.
    """
    if override:
        return str(override).strip()
    goals = _goal_statements(spec)
    if goals:
        return "; ".join(goals)
    desired = [str(item).strip() for item in (spec.get("desired_content") or ())
               if str(item).strip()]
    if desired:
        return "; ".join(desired)
    asked = [request.statement for request in requests if request.statement]
    return "; ".join(asked)


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

    The spec's `narrative_goals` are the commission's GOALS, and they
    arrive as goals rather than as the summary line they used to be
    flattened into.  The spec's `requests` are the requested changes;
    a caller that passes `requests` itself is answering for them, and
    what it passes wins.
    """
    spec = dict(editorial_spec or {})
    spec_goals = tuple(
        _goal(entry, index)
        for index, entry in enumerate(spec.get("narrative_goals") or ()))
    explicit_goals = tuple(row for row in spec_goals if row.explicit)
    inferred_goals = [row for row in spec_goals if not row.explicit]
    requirements = tuple(
        _declaration(entry, index, REQUIREMENT,
                     explicit=True, objective=True)
        for index, entry in enumerate(spec.get("requirements") or ()))
    preferences = tuple(
        _declaration(entry, index, PREFERENCE,
                     explicit=True, objective=False)
        for index, entry in enumerate(spec.get("preferences") or ()))
    inferred_preferences = []
    for index, entry in enumerate(inferred or ()):
        kind = str(entry.get("kind") or INFERRED_PREFERENCE).strip()
        if kind == GOAL:
            inferred_goals.append(_inferred(entry, index, INFERRED_GOAL))
        elif kind == INFERRED_PREFERENCE:
            inferred_preferences.append(_inferred(entry, index))
        else:
            raise IntentError(
                f"the inferred[{index}] names kind {kind!r}; one of "
                f"{[INFERRED_PREFERENCE, GOAL]}")
    request_entries = requests if requests else (spec.get("requests") or ())
    built_requests = tuple(
        _request(entry, index) for index, entry in enumerate(request_entries))
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
        desired_outcome=_desired_outcome(spec, desired_outcome, built_requests),
        brief_ref=str(brief_ref or ""),
        goals=explicit_goals,
        inferred_goals=tuple(inferred_goals),
        requirements=requirements,
        preferences=preferences,
        inferred_preferences=tuple(inferred_preferences),
        requests=built_requests,
        references=tuple(dict(row) for row in (spec.get("references") or ())),
        constraints=tuple(constraints),
        unresolved=tuple(dict(row)
                         for row in (spec.get("unresolved_decisions") or ())),
        evidence_links=tuple(str(link) for link in (evidence_links or ())),
        version=int(intent_version),
    )


def intent_from_request(statement: str, *, intent_id: str,
                        brief_ref: str = "",
                        evidence_lanes: Iterable[str] = (),
                        ) -> EditorialIntent:
    """The canonical intent of ONE request, in the requester's own words.

    A commission arrives as a structured spec, and `build_intent` reads
    that. A one-off request arrives as a sentence, and this is the path
    from the sentence to the canonical record - so the harness can
    measure what the run READ instead of reading a prose restatement it
    wrote about itself.

    It is deliberately narrow. It declares the REQUEST KIND the sentence
    names - and only when the sentence names exactly one. A sentence
    that names none, or that names two, is still a request: it is
    recorded as ONE request carrying the candidates it could have
    meant, with the question it has to be asked, and it cannot act
    alone. It invents NO requirement and NO preference: a direction
    the sentence does not state has nowhere to live here, and inventing
    one is the failure this exists to prevent.
    """
    text = str(statement or "").strip()
    if not text:
        raise IntentError("a request states nothing")
    lanes = tuple(str(lane) for lane in evidence_lanes)
    matched = match_request_kinds(text)
    if len(matched) == 1:
        kind = matched[0]
        profile = REQUEST_PROFILES[kind]
        missing = [{"lane": lane, "status": NOT_YET_ANALYSED}
                   for lane in profile.evidence_bar if lane not in lanes]
        request = IntentRequest(
            request_id="request:0",
            kind=kind,
            statement=text,
            explicit=True,
            objective=profile.objective,
            autonomy=profile.autonomy,
            autonomy_basis=f"the declared default for {kind}",
            evidence_bar=profile.evidence_bar,
            evidence_lanes=lanes,
            missing_evidence=tuple(missing),
        )
    else:
        request = IntentRequest(
            request_id="request:0",
            kind=UNDETERMINED_KIND,
            statement=text,
            explicit=True,
            objective=False,
            autonomy=NEEDS_CLARIFICATION,
            autonomy_basis=("the request names none of the declared request "
                            "kinds, so it cannot act alone"),
            evidence_bar=(),
            evidence_lanes=lanes,
            missing_evidence=(),
            candidates=matched,
            question=_clarification_question(matched),
        )
    return EditorialIntent(
        intent_id=str(intent_id),
        desired_outcome=text,
        brief_ref=str(brief_ref or ""),
        requests=(request,),
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
        if request.kind not in REQUEST_PROFILES and \
                request.kind != UNDETERMINED_KIND:
            problems.append(f"request {label}: names kind {request.kind!r}; "
                            f"one of {list(REQUEST_KINDS)}")
            continue
        for candidate in request.candidates:
            if candidate not in REQUEST_PROFILES:
                problems.append(
                    f"request {label}: names candidate kind {candidate!r}; "
                    f"one of {list(REQUEST_KINDS)}")
        profile = REQUEST_PROFILES.get(request.kind)
        if profile is None:
            if request.autonomy == AUTONOMOUS:
                problems.append(
                    f"request {label}: names no request kind and is "
                    "autonomous - a change nobody has identified cannot "
                    "act alone")
            if not request.candidates and not request.question:
                problems.append(
                    f"request {label}: names no request kind and asks "
                    "nothing - the change it means is undetermined")
        else:
            for lane in request.evidence_bar:
                if lane not in EVIDENCE_LANES:
                    problems.append(
                        f"request {label}: evidence bar names lane "
                        f"{lane!r}, which is not a lane")
            widened = [lane for lane in request.evidence_bar
                       if lane not in profile.evidence_bar]
            if widened:
                problems.append(
                    f"request {label}: evidence bar names {widened}, which "
                    f"{request.kind} does not require - a bar may be "
                    "narrowed, not widened")
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
        for field, values in (("target_roles", request.target_roles),
                              ("explicit_constraints",
                               request.explicit_constraints),
                              ("preservation", request.preservation),
                              ("outputs", request.outputs)):
            for text in values:
                if not str(text).strip():
                    problems.append(
                        f"request {label}: {field} carries an empty entry - "
                        "the requester's own words are not blank")

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
ENTRY_GOAL = GOAL
ENTRY_INFERRED_GOAL = INFERRED_GOAL
ENTRY_REQUEST = "request"
ENTRY_REFERENCE = "reference"
ENTRY_CONSTRAINT = "constraint"
ENTRY_UNRESOLVED = "unresolved"
ENTRY_DESIRED_OUTCOME = "desired_outcome"

PROJECT_MODEL_ENTRY_KINDS = (
    ENTRY_DESIRED_OUTCOME,
    ENTRY_GOAL,
    ENTRY_INFERRED_GOAL,
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
    for declaration in intent.goals:
        entries.append({CONSTRAINT_ENTRY: ENTRY_GOAL, **declaration.to_dict()})
    for declaration in intent.inferred_goals:
        entries.append({CONSTRAINT_ENTRY: ENTRY_INFERRED_GOAL,
                        **declaration.to_dict()})
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
    goals, inferred_goals = [], []
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
        elif kind == ENTRY_GOAL:
            goals.append(Declaration.from_dict(entry))
        elif kind == ENTRY_INFERRED_GOAL:
            inferred_goals.append(Declaration.from_dict(entry))
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
        goals=tuple(goals),
        inferred_goals=tuple(inferred_goals),
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
    for goal in intent.goals:
        print(f"  goal: {goal.statement}")
    for goal in intent.inferred_goals:
        print(f"  inferred goal: {goal.statement}")
    print(f"  requirements: {len(intent.requirements)}")
    print(f"  preferences: {len(intent.preferences)}")
    print(f"  inferred preferences: {len(intent.inferred_preferences)}")
    print(f"  requests: {len(intent.requests)}")
    for request in intent.requests:
        absent = missing_lanes(request)
        print(f"    - {request.request_id} [{request.kind or 'undetermined'}] "
              f"autonomy={request.autonomy}"
              + (f" missing={list(absent)}" if absent else ""))
        for role in request.target_roles:
            print(f"        acts on: {role}")
        for constraint in request.explicit_constraints:
            print(f"        bound by: {constraint}")
        for kept in request.preservation:
            print(f"        must not change: {kept}")
        for output in request.outputs:
            print(f"        must produce: {output}")
        if request.accepted_autonomy:
            print(f"        accepted autonomy: {request.accepted_autonomy}")
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
