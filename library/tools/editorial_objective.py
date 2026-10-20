"""EDIT-024: what an objective requires of a cut, checked against the cut.

`library/tools/editorial_policy.py` owns the vocabulary: an objective is a
genre, and a genre carries constraints.  This module owns the CHECKING of
those constraints against a cut, and the reading of which passages a
reduction is not allowed to drop.

The split exists because a constraint and a check are different things.  A
constraint is what the genre requires; a check is what can be measured
about the cut that was built.  A constraint whose facts were never measured
reads `undetermined`, not `satisfied` - the discipline the repository
already enforces in `library/tools/undetermined.py` and
`library/tools/evidence_graph.py`, whose five-way absence vocabulary
(`present`, `absent`, `not_observed`, `not_analyzed`, `inconclusive`) this
module borrows the shape of.

**No constraint here is a count.**  There is no "at least N" in any of
them, so none of them is a creative floor
(`library/tools/creative_floors.py`), and none of them pads an edit to
satisfy a number.

**Who supplies the facts.**  The context below is built by the caller from
state the run already holds: the speech sequence for what the material
offers, the timed spine for what the cut plays, the B-roll assignments
for what covers it, and the intent model for what was claimed.  Nothing
here reads state itself, so a test can hand it a cut that never existed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from library.tools.editorial_policy import OBJECTIVES, EditorialPolicy

SATISFIED = "satisfied"
VIOLATED = "violated"
UNDETERMINED = "undetermined"

STATUSES = (SATISFIED, VIOLATED, UNDETERMINED)


class ObjectiveContextError(ValueError):
    """The context a constraint is checked against is malformed."""


@dataclass(frozen=True)
class Passage:
    """One passage of speech, as the material offers it or as the cut plays it.

    `passage_id` is the identity both sides share: a cut passage names the
    source passage it came from.  `step_index` is the tutorial's step
    number, when the caller knows it, and is None otherwise - a fact that
    was not measured, not a step zero.
    """

    passage_id: str
    speaker: str = ""
    text: str = ""
    source_clip_id: str = ""
    source_start: float = 0.0
    source_end: float = 0.0
    block_type: str = "speech"
    step_index: int | None = None
    serves_beats: tuple[str, ...] = ()


@dataclass(frozen=True)
class Cutaway:
    """A picture that covers a stretch of the cut."""

    cutaway_id: str
    source_clip_id: str = ""
    source_start: float = 0.0
    source_end: float = 0.0
    covers_passage_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Claim:
    """A statement the edit is built to carry."""

    claim_id: str
    statement: str = ""
    passage_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Beat:
    """A narrative unit, and whether the direction required it."""

    beat_id: str
    purpose: str = ""
    required: bool = False
    passage_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObjectiveContext:
    """Everything a constraint may be checked against."""

    source_passages: tuple[Passage, ...] = ()
    cut_passages: tuple[Passage, ...] = ()
    cutaways: tuple[Cutaway, ...] = ()
    claims: tuple[Claim, ...] = ()
    beats: tuple[Beat, ...] = ()
    hook_passage_ids: tuple[str, ...] = ()
    demonstration_passage_ids: tuple[str, ...] = ()

    def passage(self, passage_id: str) -> Passage | None:
        for row in self.source_passages:
            if row.passage_id == passage_id:
                return row
        for row in self.cut_passages:
            if row.passage_id == passage_id:
                return row
        return None


@dataclass(frozen=True)
class Constraint:
    """One thing the genre requires of the cut."""

    name: str
    what: str
    needs: tuple[str, ...]


CONSTRAINTS: dict[str, Constraint] = {
    constraint.name: constraint for constraint in (
        Constraint(
            name="speaker_coverage",
            what="every speaker who said something the cut could have used "
                 "is still heard in the cut.",
            needs=("source_passages", "cut_passages"),
        ),
        Constraint(
            name="question_answer_kept_together",
            what="a question and the answer that follows it stay together: "
                 "the passage after a question is not another speaker "
                 "answering something else.",
            needs=("cut_passages",),
        ),
        Constraint(
            name="hook_present",
            what="the cut opens on a hook.",
            needs=("cut_passages", "hook_passage_ids"),
        ),
        Constraint(
            name="demonstration_visible",
            what="the passage that demonstrates stays on screen.",
            needs=("cut_passages", "demonstration_passage_ids"),
        ),
        Constraint(
            name="cutaway_over_demonstration",
            what="no cutaway covers the demonstration.",
            needs=("cutaways", "demonstration_passage_ids"),
        ),
        Constraint(
            name="step_order",
            what="the steps the cut plays stay in the order they were "
                 "spoken, and no step between two played steps is missing.",
            needs=("cut_passages",),
        ),
        Constraint(
            name="chronology_preserved",
            what="the passages the cut plays keep the relative order they "
                 "had in the material.",
            needs=("source_passages", "cut_passages"),
        ),
        Constraint(
            name="claim_present",
            what="every declared claim is still carried by the cut.",
            needs=("claims", "cut_passages"),
        ),
    )
}


@dataclass(frozen=True)
class Finding:
    """What one constraint says about one cut."""

    constraint: str
    status: str
    detail: str
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint": self.constraint,
            "status": self.status,
            "detail": self.detail,
            "evidence_ids": list(self.evidence_ids),
        }


def _speaks(row: Passage) -> bool:
    """Does this passage carry something said?

    A passage with no text was never measured for what it says, so it
    cannot count as a speaker's presence - and it cannot count as their
    absence either.  The callers below treat it as neither.
    """
    return bool(str(row.text or "").strip())


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_end > b_start and a_start < b_end


def _check_speaker_coverage(context: ObjectiveContext) -> list[Finding]:
    spoken = {row.speaker for row in context.source_passages
              if _speaks(row) and row.speaker}
    if not spoken:
        return [Finding(
            "speaker_coverage", UNDETERMINED,
            "no source passage carries a named speaker and text, so nobody "
            "can be said to be missing")]
    heard = {row.speaker for row in context.cut_passages
             if _speaks(row) and row.speaker}
    missing = sorted(spoken - heard)
    if missing:
        return [Finding(
            "speaker_coverage", VIOLATED,
            f"the cut does not carry {', '.join(missing)}",
            tuple(sorted(
                row.passage_id for row in context.source_passages
                if row.speaker in missing and _speaks(row))))]
    return [Finding(
        "speaker_coverage", SATISFIED,
        f"every speaker the material offers ({', '.join(sorted(spoken))}) "
        f"is heard in the cut")]


def _check_question_answer_kept_together(context: ObjectiveContext) -> list[Finding]:
    rows = list(context.cut_passages)
    if not rows:
        return [Finding(
            "question_answer_kept_together", UNDETERMINED,
            "the cut plays no passage, so no question can be separated from "
            "an answer")]
    broken = []
    for index, row in enumerate(rows[:-1]):
        if not str(row.text or "").rstrip().endswith("?"):
            continue
        following = rows[index + 1]
        if row.speaker and following.speaker and row.speaker != following.speaker:
            broken.append((row, following))
    if broken:
        detail = "; ".join(
            f"'{row.text.strip()[:60]}' is followed by {following.speaker or 'another speaker'}"
            for row, following in broken)
        return [Finding(
            "question_answer_kept_together", VIOLATED,
            f"a question is separated from its answer: {detail}",
            tuple(row.passage_id for row, _ in broken))]
    return [Finding(
        "question_answer_kept_together", SATISFIED,
        "no question in the cut is followed by another speaker's answer")]


def _check_hook_present(context: ObjectiveContext) -> list[Finding]:
    if not context.cut_passages:
        return [Finding(
            "hook_present", UNDETERMINED,
            "the cut plays no passage, so there is no opening to read")]
    first = context.cut_passages[0]
    if first.block_type == "hook" or first.passage_id in context.hook_passage_ids:
        return [Finding(
            "hook_present", SATISFIED,
            f"the cut opens on {first.passage_id}",
            (first.passage_id,))]
    return [Finding(
        "hook_present", VIOLATED,
        f"the cut opens on {first.passage_id}, which is not a hook",
        (first.passage_id,))]


def _check_demonstration_visible(context: ObjectiveContext) -> list[Finding]:
    if not context.demonstration_passage_ids:
        return [Finding(
            "demonstration_visible", UNDETERMINED,
            "no passage was identified as the demonstration, so none can be "
            "called visible or hidden")]
    played = {row.passage_id for row in context.cut_passages}
    missing = [pid for pid in context.demonstration_passage_ids if pid not in played]
    if missing:
        return [Finding(
            "demonstration_visible", VIOLATED,
            f"the demonstration ({', '.join(missing)}) is not in the cut",
            tuple(missing))]
    return [Finding(
        "demonstration_visible", SATISFIED,
        f"the demonstration ({', '.join(context.demonstration_passage_ids)}) "
        f"is in the cut",
        tuple(context.demonstration_passage_ids))]


def _check_cutaway_over_demonstration(context: ObjectiveContext) -> list[Finding]:
    if not context.demonstration_passage_ids:
        return [Finding(
            "cutaway_over_demonstration", UNDETERMINED,
            "no passage was identified as the demonstration, so no cutaway "
            "can be said to cover one")]
    covering = []
    for cutaway in context.cutaways:
        for pid in context.demonstration_passage_ids:
            passage = context.passage(pid)
            if passage is None:
                continue
            if _overlaps(cutaway.source_start, cutaway.source_end,
                          passage.source_start, passage.source_end):
                covering.append((cutaway, passage))
    if covering:
        return [Finding(
            "cutaway_over_demonstration", VIOLATED,
            "; ".join(f"{cutaway.cutaway_id} covers {passage.passage_id}"
                      for cutaway, passage in covering),
            tuple(cutaway.cutaway_id for cutaway, _ in covering))]
    return [Finding(
        "cutaway_over_demonstration", SATISFIED,
        "no cutaway covers the demonstration")]


def _check_step_order(context: ObjectiveContext) -> list[Finding]:
    stepped = [row for row in context.cut_passages if row.step_index is not None]
    if not stepped:
        return [Finding(
            "step_order", UNDETERMINED,
            "no cut passage carries a step number, so no order can be "
            "checked")]
    indexes = [row.step_index for row in stepped]
    if any(later <= earlier for earlier, later in zip(indexes, indexes[1:])):
        return [Finding(
            "step_order", VIOLATED,
            "the steps the cut plays are not in the order they were spoken",
            tuple(row.passage_id for row in stepped))]
    return [Finding(
        "step_order", SATISFIED,
        f"the {len(stepped)} stepped passage(s) keep their spoken order")]


def _check_chronology_preserved(context: ObjectiveContext) -> list[Finding]:
    order = {row.passage_id: index
             for index, row in enumerate(context.source_passages)}
    played = [row for row in context.cut_passages if row.passage_id in order]
    if len(played) < 2:
        return [Finding(
            "chronology_preserved", UNDETERMINED,
            "fewer than two played passages are also source passages, so "
            "no order can be compared")]
    positions = [order[row.passage_id] for row in played]
    if any(later <= earlier for earlier, later in zip(positions, positions[1:])):
        return [Finding(
            "chronology_preserved", VIOLATED,
            "the cut plays its passages in an order the material did not "
            "have",
            tuple(row.passage_id for row in played))]
    return [Finding(
        "chronology_preserved", SATISFIED,
        f"the {len(played)} played passage(s) keep their source order")]


def _check_claim_present(context: ObjectiveContext) -> list[Finding]:
    if not context.claims:
        return [Finding(
            "claim_present", UNDETERMINED,
            "no claim was declared, so there is none to carry")]
    played = {row.passage_id for row in context.cut_passages}
    dropped = [claim for claim in context.claims
               if claim.passage_ids and not (set(claim.passage_ids) & played)]
    if dropped:
        return [Finding(
            "claim_present", VIOLATED,
            "; ".join(f"{claim.claim_id} is not carried by the cut"
                      for claim in dropped),
            tuple(claim.claim_id for claim in dropped))]
    return [Finding(
        "claim_present", SATISFIED,
        f"every declared claim ({len(context.claims)}) is carried by the cut")]


_CHECKS = {
    "speaker_coverage": _check_speaker_coverage,
    "question_answer_kept_together": _check_question_answer_kept_together,
    "hook_present": _check_hook_present,
    "demonstration_visible": _check_demonstration_visible,
    "cutaway_over_demonstration": _check_cutaway_over_demonstration,
    "step_order": _check_step_order,
    "chronology_preserved": _check_chronology_preserved,
    "claim_present": _check_claim_present,
}


def check_constraints(policy: EditorialPolicy | None,
                      context: ObjectiveContext) -> list[Finding]:
    """Every constraint this objective carries, checked against one cut.

    A policy that declares no objective checks nothing: the objective is
    what says which constraints apply, and a project that named none has
    said nothing about the shape of its cut.
    """
    if policy is None:
        return []
    objective = OBJECTIVES.get(policy.objective)
    if objective is None:
        return []
    findings = []
    for name in objective.constraints:
        check = _CHECKS.get(name)
        if check is None:
            findings.append(Finding(
                name, UNDETERMINED,
                f"no check implements the constraint {name!r}; the "
                f"objective declares it and nothing reads it"))
            continue
        findings.extend(check(context))
    return findings


def protected_passage_ids(policy: EditorialPolicy | None,
                          context: ObjectiveContext) -> tuple[str, ...]:
    """The passages a reduction may not drop, under this objective.

    This is the objective's reach into the reduction behaviours: a
    document whose declared claim no longer survives is a document that
    stopped being a document, and a reel whose hook was cut is a reel
    with no opening.  The ids come from the constraints the objective
    carries and the facts the context holds; a constraint whose facts are
    absent protects nothing and says so in the finding above.
    """
    if policy is None:
        return ()
    played = {row.passage_id for row in context.cut_passages}
    protected: set[str] = set()

    for claim in context.claims:
        if claim.passage_ids and not (set(claim.passage_ids) & played):
            protected.update(claim.passage_ids)

    for beat in context.beats:
        if not beat.required or not beat.passage_ids:
            continue
        if not (set(beat.passage_ids) & played):
            protected.update(beat.passage_ids)

    spoken = {row.speaker for row in context.source_passages
              if _speaks(row) and row.speaker}
    heard = {row.speaker for row in context.cut_passages
             if _speaks(row) and row.speaker}
    for row in context.source_passages:
        if row.speaker and row.speaker in spoken - heard:
            protected.add(row.passage_id)

    return tuple(sorted(protected))


def context_from_state(state: Mapping[str, Any]) -> ObjectiveContext:
    """Build the check context from a run's state.

    The source passages come from the speech sequence, the cut passages
    from the timed spine's speech and hook blocks, the cutaways from the
    B-roll assignments and interjections, and the claims from the intent
    model.  A piece of state that is absent yields an absent fact, never
    an invented one.
    """
    speech = state.get("speech_sequence") or {}
    speakers = _transcript_speakers(state)
    source_rows = []
    for index, row in enumerate(speech.get("body_sequence") or []):
        if not isinstance(row, Mapping):
            continue
        clip_id = str(row.get("clip_id") or "")
        speaker = str(row.get("speaker") or "")
        if not speaker:
            speaker = speakers.get(
                (clip_id, _number(row.get("source_start"))), "")
        source_rows.append(Passage(
            passage_id=str(row.get("passage_id") or row.get("id")
                           or f"passage:{index + 1}"),
            speaker=speaker,
            text=str(row.get("text") or ""),
            source_clip_id=clip_id,
            source_start=_number(row.get("source_start")),
            source_end=_number(row.get("source_end")),
            block_type=str(row.get("block_type") or "speech"),
            serves_beats=tuple(row.get("serves_beats") or ()),
        ))
    for index, row in enumerate(speech.get("excluded_passages") or []):
        if not isinstance(row, Mapping):
            continue
        source_rows.append(Passage(
            passage_id=str(row.get("passage_id") or row.get("id")
                           or f"excluded:{index + 1}"),
            speaker=str(row.get("speaker") or ""),
            text=str(row.get("text") or ""),
            source_clip_id=str(row.get("clip_id") or ""),
            source_start=_number(row.get("source_start")),
            source_end=_number(row.get("source_end")),
        ))

    spine = state.get("timed_spine") or {}
    body = [row for row in (speech.get("body_sequence") or [])
            if isinstance(row, Mapping)]
    cut_rows = []
    for block in spine.get("structure") or []:
        if not isinstance(block, Mapping):
            continue
        if block.get("block_type") not in ("speech", "hook"):
            continue
        content = block.get("content") or {}
        ref = content.get("passage_ref")
        # `passage_ref` is the 1-based POSITION of the passage in the
        # speech sequence's body - the only name a spine block may
        # reference (library/capabilities/spine/mesh.py).  The passage id
        # IS that position, so the two agree by construction.
        origin = None
        if isinstance(ref, int) and not isinstance(ref, bool):
            if 1 <= ref <= len(body):
                origin = body[ref - 1]
        passage_id = (f"passage:{ref}" if isinstance(ref, int) and
                      not isinstance(ref, bool)
                      else str(ref or block.get("passage_id")
                               or block.get("position")))
        cut_rows.append(Passage(
            passage_id=passage_id,
            speaker=(origin.get("speaker") if origin
                     else str(content.get("speaker") or "")),
            text=(origin.get("text") if origin
                  else str(content.get("text") or "")),
            source_clip_id=str(block.get("clip_id") or ""),
            source_start=_number(block.get("source_start")),
            source_end=_number(block.get("source_end")),
            block_type=str(block.get("block_type") or "speech"),
            step_index=(origin.get("step_index") if origin else None),
            serves_beats=(tuple(origin.get("serves_beats") or ())
                          if origin else ()),
        ))

    cutaways = []
    for key in ("b_roll_assignments", "b_roll_interjections"):
        for index, row in enumerate(state.get(key) or []):
            if not isinstance(row, Mapping):
                continue
            cutaways.append(Cutaway(
                cutaway_id=str(row.get("cutaway_id") or row.get("clip_id")
                               or f"{key}:{index + 1}"),
                source_clip_id=str(row.get("clip_id") or ""),
                source_start=_number(row.get("source_start")),
                source_end=_number(row.get("source_end")),
                covers_passage_ids=tuple(row.get("covers_passage_ids") or ()),
            ))

    intent = state.get("intent_model") or {}
    claims = []
    for index, row in enumerate(intent.get("claims") or []):
        if not isinstance(row, Mapping):
            continue
        claims.append(Claim(
            claim_id=str(row.get("claim_id") or f"claim:{index + 1}"),
            statement=str(row.get("statement") or ""),
            passage_ids=tuple(row.get("passage_ids") or ()),
        ))

    beats = []
    for row in intent.get("beats") or []:
        if not isinstance(row, Mapping):
            continue
        beats.append(Beat(
            beat_id=str(row.get("beat_id") or ""),
            purpose=str(row.get("purpose") or ""),
            required=bool(row.get("required")),
            passage_ids=tuple(row.get("passage_ids") or ()),
        ))

    moments = [row for row in (intent.get("key_moments") or [])
               if isinstance(row, Mapping)]
    hook_ids = _passages_overlapping_moments(cut_rows, moments)
    demonstration_ids = _passages_overlapping_moments(
        cut_rows, [row for row in moments
                   if _names_a_demonstration(row)])

    return ObjectiveContext(
        source_passages=tuple(source_rows),
        cut_passages=tuple(cut_rows),
        cutaways=tuple(cutaways),
        claims=tuple(claims),
        beats=tuple(beats),
        hook_passage_ids=hook_ids,
        demonstration_passage_ids=demonstration_ids,
    )


#: What marks a key moment as THE demonstration.  The intent model's key
#: moments are free text, so the marker is read from the moment's id or
#: its description, and a moment that carries neither marker is not a
#: demonstration - nothing is inferred from the fact that a moment
#: exists.
DEMONSTRATION_MARKER = "demonstrat"


def _names_a_demonstration(moment: Mapping[str, Any]) -> bool:
    marker = DEMONSTRATION_MARKER
    moment_id = str(moment.get("moment_id") or "").lower()
    description = str(moment.get("description") or "").lower()
    return marker in moment_id or marker in description


def _passages_overlapping_moments(cut_rows: list[Passage],
                                  moments: Sequence[Mapping[str, Any]]
                                  ) -> tuple[str, ...]:
    """The cut passages a key moment's span covers.

    A key moment is grounded in a clip and a source range, and a cut
    passage is grounded in the same two, so the two are matched on the
    overlap - the same rule `library/tools/intent_model.py` uses to put a
    moment inside a beat.
    """
    found = []
    for moment in moments:
        span = moment.get("span") or {}
        clip_id = str(span.get("clip_id") or "")
        try:
            start = float(span["source_start"])
            end = float(span["source_end"])
        except (KeyError, TypeError, ValueError):
            continue
        for row in cut_rows:
            if clip_id and row.source_clip_id and row.source_clip_id != clip_id:
                continue
            if row.source_end <= start or row.source_start >= end:
                continue
            if row.passage_id not in found:
                found.append(row.passage_id)
    return tuple(found)


def _transcript_speakers(state: Mapping[str, Any]) -> dict[tuple[str, float], str]:
    """Who spoke, per clip and start second, from the timeline transcript.

    The speech sequence names no speaker - the model writes the passage
    and the clip, and the attribution belongs to the TRANSCRIBER.  The
    transcript's rows carry it, with the source file and the source
    range, so the join is on the clip and the range.

    A passage the transcript says nothing about has no speaker, and a
    missing speaker is an ABSENCE: the constraint that reads it says
    `undetermined` rather than concluding that nobody spoke.
    """
    transcript = state.get("timeline_transcript")
    if not isinstance(transcript, Mapping):
        return {}
    catalog = {}
    for row in state.get("clip_catalog") or ():
        if isinstance(row, Mapping):
            clip_id = str(row.get("clip_id") or "")
            filename = str(row.get("filename") or row.get("path") or "")
            if clip_id and filename:
                catalog[filename] = clip_id
    found: dict[tuple[str, float], str] = {}
    for row in transcript.get("segments") or ():
        if not isinstance(row, Mapping):
            continue
        speaker = str(row.get("speaker") or "")
        if not speaker:
            continue
        source_file = str(row.get("source_file") or "")
        clip_id = str(row.get("clip_id") or "") or catalog.get(source_file, "")
        if not clip_id:
            continue
        start = _number(row.get("source_start"))
        found.setdefault((clip_id, start), speaker)
    return found


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def describe_findings(findings: Sequence[Finding]) -> list[str]:
    """One line per finding, for a prompt or a report."""
    lines = []
    for finding in findings:
        lines.append(f"[{finding.status}] {finding.constraint}: {finding.detail}")
    return lines
