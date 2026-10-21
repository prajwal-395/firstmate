"""Turn one OBJECTIVE failure into a narrow EditRequest - and know when to stop.

QA-009 of the captain's punch list (2026-10-20): targeted quality-driven
revisions.  The punch list asks for five things, and this module is the
answer to all five:

* **Identify the precise interval, decision, or property behind a quality
  failure.**  The delivered-file checks already NAME their failures: the
  audio spec (QA-004, PR 3046), the caption spec (QA-003, PR 3082), the
  picture spec (QA-002, PR 3071) and the editorial review (QA-006, PR
  3088) each say which property failed, over which interval, with the
  number they measured.  `objective_checks` reads those verdicts and
  turns each leg into one `QualityCheck` - a measurement, with its
  property, its interval and its evidence.

* **Create a narrow new edit request.**  `plan_revision` turns ONE
  failing check into ONE `EditRequest` (the VC-07 contract,
  `library/tools/edit_request.py`) whose operations touch only the item
  the failure implicates.  A narrow request is narrow in three ways at
  once: the property it changes is the one that failed, the interval it
  touches is the one that failed, and everything else the failure does
  not implicate is named as a PRESERVATION requirement
  (`library/tools/preservation_invariant.py`), so the read-back can
  judge it rather than the requester having to hope for it.

* **Preserve previously accepted portions of the work.**  Every request
  this module writes carries `preservation` requirements: the rows the
  revision does not touch, the clips on the rows it does, and the
  captain's markers.  A requirement the engine cannot judge is refused
  at the request, so a promise nobody can prove never ships.

* **Verify both the corrected issue and previously satisfied constraints.**
  `verify_revision` takes the checks BEFORE and AFTER the revision and
  answers two questions separately: did the targeted check flip to
  passing, and did every check that was passing before stay passing.
  The second half is not implied by the first - a revision that fixes
  the caption and breaks the loudness is a regression, and it is
  reported as one.

* **Stop retrying if the result is not improving or the remaining
  decision is subjective.**  `plan_revision` refuses a second attempt on
  a check whose last outcome was `not_improving` or `regressed`, and a
  SUBJECTIVE check is never revised at all: it is handed to a person.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not measure anything.  Every number here was measured by the
product's own gates (`deliverable_qc`, `delivered_caption_spec`,
`render_qa`); this module reads their verdicts and writes an edit.

It does not choose taste.  A failure whose fix needs material the
timeline does not carry - a dialogue that was lost, a picture that went
black - is reported as `needs_source`, because choosing what to put
there is an editorial decision and this module does not make one.  A
failure with no narrow edit at all is reported as `no_narrow_revision`,
with the reason.  Nothing is invented to fill a gap.

It does not touch Resolve.  `plan_revision` is pure: it reads a
timeline VIEW (the items, their rows, their spans, their boxes) and
writes a request.  The request goes to the same execution path every
other edit goes to, with the same authorization, the same lease and the
same read-back.

THE ONE ENUMERATION
-------------------
`PROPERTIES` names every property a check can fail on, and
`REVISION_KINDS` names every narrow revision this module can write.
A property with no revision kind is not silently dropped: the check is
reported as `no_narrow_revision` and the reader can see that the
failure was understood and had no narrow answer.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from library.tools.edit_request import EditOperation, EditRequest, EditTarget

#: The two kinds of check.  A MEASUREMENT is a number against a stated
#: bound and a gate may act on it.  A SUBJECTIVE check is a judgement
#: about taste: nobody can reproduce it, so no revision may be planned
#: from one - it is handed to a person.  The vocabulary is the one
#: `library/tools/qa_findings.py` already uses.
OBJECTIVE = "objective"
SUBJECTIVE = "subjective"

#: The properties a check can fail on.  Each one names a MEASUREMENT
#: the engine's own gates make on the delivered file.
PROPERTIES: tuple[str, ...] = (
    "integrated_loudness",   # the file's loudness against the spec's target
    "digital_silence",       # picture playing over undeclared digital silence
    "channel_presence",      # a channel that carries no signal
    "caption_collision",     # another graphic drawn over a caption's own box
    "caption_words",         # the caption that reached the file says other words
    "caption_timing",        # the caption is on screen outside its window
    "caption_placement",     # the caption is drawn outside its own box
    "caption_readability",   # the caption's ink is too small to read
    "caption_wrapping",      # the caption drew another number of lines
    "caption_emphasis",      # the emphasis did not reach the export
    "caption_missing",       # the caption never reached the export
    "caption_stale",         # a caption nobody declared is on the export
    "caption_unreadable",    # the delivered frames could not be read at all
    "overlay_geometry",      # an overlay is absent, or drawn at the wrong size
    "undeclared_black",      # the picture goes away without being declared
    "undeclared_freeze",     # the picture stops moving without being declared
    "frame_occupancy",       # the picture does not fill the delivery frame
    "delivery_dimensions",   # the file is not the delivery format's size
    "delivery_duration",     # the file is not the length the plan delivered
)

#: The narrow revisions this module can write.  Each one is a single
#: algebra op with a measured parameter - no constant, no default.
REVISION_KINDS: tuple[str, ...] = (
    "audio_gain",       # the file's loudness is off its target
    "overlay_position",  # a graphic is drawn over a caption's own box
    "overlay_resize",   # an overlay was drawn at the wrong size
    "caption_text",     # the caption that reached the file says other words
    "hold_repair",      # a freeze-hold tail stopped advancing
)

#: The revision statuses.  `planned` is the only one that carries a
#: request; every other one is a refusal with a reason, and the reason
#: is what a reader acts on.
PLANNED = "planned"
NO_NARROW_REVISION = "no_narrow_revision"
NEEDS_SOURCE = "needs_source"
AMBIGUOUS = "ambiguous"
SUBJECTIVE_STOP = "subjective"
ALREADY_ATTEMPTED = "already_attempted"

#: The outcomes of a verification.
FIXED = "fixed"
NOT_IMPROVING = "not_improving"
REGRESSED = "regressed"

#: What a reader does next, per outcome.
ACCEPT = "accept"
STOP = "stop"
ASK_CREATOR = "ask_creator"

#: How much two boxes may overlap before they are "the same place".
#: The caption spec's own rule (`delivered_caption_spec.COLLISION_OVERLAP`),
#: read from there so the two numbers cannot drift apart.
def _collision_overlap() -> float:
    try:
        from library.tools.delivered_caption_spec import COLLISION_OVERLAP

        return float(COLLISION_OVERLAP)
    except Exception:  # noqa: BLE001 - the stated constant is the fallback
        return 0.02


#: The smallest clearance a moved graphic is left with, in
#: DELIVERED-FRAME pixels.
#:
#: The boxes a collision is measured with are read off the delivered
#: file's pixels, so each edge carries up to one pixel of error.  A
#: move that made the two boxes exactly touch would leave them
#: overlapping by up to a pixel after the file was measured again, and
#: the check would call that a collision.  Two pixels - one for each
#: edge - is the smallest gap that cannot overlap when the file is read
#: again.  It is a measurement's resolution, not a taste value: it
#: changes no one's mind about where a graphic goes.
CLEARANCE_PX = 2.0


class TargetedRevisionError(ValueError):
    """A revision cannot be planned, and the reason is not a defect."""


# ── The check: one measurement, named ─────────────────────────────────


@dataclass(frozen=True)
class QualityCheck:
    """One measurement of the delivered file, and whether it passed.

    `metric` is the check's own name, `property` is the precise
    property it measured (one of `PROPERTIES`), and `start`/`end` are
    the seconds on the delivered file the failure covers.  A check with
    no interval is a whole-file measurement, and the planner says so
    rather than inventing a window for it.

    `measured` carries the numbers the gate measured, so a revision's
    parameter is READ OFF the measurement and never guessed.
    """

    metric: str
    property: str
    passed: bool
    detail: str = ""
    source: str = ""
    label: str = ""
    start: float | None = None
    end: float | None = None
    measured: Mapping[str, Any] = field(default_factory=dict)
    kind: str = OBJECTIVE

    @property
    def key(self) -> tuple[str, str, str]:
        """The check's identity: metric, property and the thing it is about."""
        return (self.metric, self.property, self.label)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QualityCheck:
        """A check read back from a record."""
        if not isinstance(data, Mapping):
            raise TargetedRevisionError(
                "a quality check must be a mapping, got "
                f"{type(data).__name__}")
        return cls(
            metric=str(data.get("metric") or ""),
            property=str(data.get("property") or ""),
            passed=bool(data.get("passed")),
            detail=str(data.get("detail") or ""),
            source=str(data.get("source") or ""),
            label=str(data.get("label") or ""),
            start=(None if data.get("start") is None else float(data["start"])),
            end=(None if data.get("end") is None else float(data["end"])),
            measured=dict(data.get("measured") or {}),
            kind=str(data.get("kind") or OBJECTIVE),
        )

    @property
    def whole_file(self) -> bool:
        """Whether the check measured the whole file rather than a window."""
        return self.start is None and self.end is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "property": self.property,
            "passed": bool(self.passed),
            "detail": self.detail,
            "source": self.source,
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "measured": dict(self.measured),
            "kind": self.kind,
        }


# ── The timeline view: what the planner is allowed to name ───────────


@dataclass(frozen=True)
class TimelineItem:
    """One item on the timeline, as the planner sees it.

    `kind` says what the item IS, because the revision a failure needs
    depends on it: an `audio` item can be gained, a `graphic` can be
    moved, a `caption` can have its text rewritten, a `hold` can have
    its window repaired.  `box` is the item's ink box in DELIVERED-frame
    pixels, when it was measured; without it the planner cannot say
    where a graphic is, and says so.
    """

    unique_id: str
    track_type: str
    track_index: int
    record_in: int
    record_out: int
    name: str = ""
    kind: str = ""
    box: tuple[float, float, float, float] | None = None
    text: str = ""

    def covers(self, first: int, last: int) -> bool:
        """Whether the item plays across the frames in question."""
        return self.record_in < last and first < self.record_out

    def as_dict(self) -> dict[str, Any]:
        return {
            "unique_id": self.unique_id,
            "track_type": self.track_type,
            "track_index": self.track_index,
            "record_in": self.record_in,
            "record_out": self.record_out,
            "name": self.name,
            "kind": self.kind,
            "box": (list(self.box) if self.box is not None else None),
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TimelineItem:
        """A timeline item read back from a record."""
        if not isinstance(data, Mapping):
            raise TargetedRevisionError(
                "a timeline item must be a mapping, got "
                f"{type(data).__name__}")
        box = data.get("box")
        return cls(
            unique_id=str(data.get("unique_id") or ""),
            track_type=str(data.get("track_type") or ""),
            track_index=int(data.get("track_index") or 0),
            record_in=int(data.get("record_in") or 0),
            record_out=int(data.get("record_out") or 0),
            name=str(data.get("name") or ""),
            kind=str(data.get("kind") or ""),
            box=(tuple(float(value) for value in box)
                 if isinstance(box, (list, tuple)) and len(box) == 4
                 else None),
            text=str(data.get("text") or ""),
        )


@dataclass(frozen=True)
class RevisionContext:
    """Everything the planner is allowed to read.

    The planner NEVER reads Resolve and never guesses an identity: the
    items are the caller's view of the timeline, and an item the
    planner cannot see is an item the revision cannot touch.
    """

    project: str
    timeline: str
    items: tuple[TimelineItem, ...] = ()
    fps: float = 0.0
    first_frame: int = 0
    markers: tuple[int, ...] = ()

    def rows(self) -> list[tuple[str, int]]:
        """The rows the view carries, in a stable order."""
        seen: list[tuple[str, int]] = []
        for item in self.items:
            row = (str(item.track_type), int(item.track_index))
            if row not in seen:
                seen.append(row)
        return seen

    def items_on(self, track_type: str, track_index: int) -> list[TimelineItem]:
        return [item for item in self.items
                if str(item.track_type) == str(track_type)
                and int(item.track_index) == int(track_index)]

    def frame_of(self, seconds: float) -> int:
        """The frame a second of the delivered file lands on."""
        return round(self.first_frame + float(seconds) * float(self.fps))

    def span_of(self, check: QualityCheck) -> tuple[int, int]:
        """The frames a check covers, in the timeline's own numbering."""
        if check.whole_file:
            last = max((item.record_out for item in self.items), default=0)
            return int(self.first_frame), int(last)
        start = self.frame_of(check.start or 0.0)
        end = self.frame_of(check.end or 0.0)
        return start, max(start + 1, end)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "timeline": self.timeline,
            "items": [item.as_dict() for item in self.items],
            "fps": self.fps,
            "first_frame": self.first_frame,
            "markers": list(self.markers),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RevisionContext:
        """A timeline view read back from a record."""
        if not isinstance(data, Mapping):
            raise TargetedRevisionError(
                "a timeline view must be a mapping, got "
                f"{type(data).__name__}")
        return cls(
            project=str(data.get("project") or ""),
            timeline=str(data.get("timeline") or ""),
            items=tuple(TimelineItem.from_dict(item)
                        for item in (data.get("items") or ())),
            fps=float(data.get("fps") or 0.0),
            first_frame=int(data.get("first_frame") or 0),
            markers=tuple(int(frame) for frame in (data.get("markers") or ())),
        )


def items_from_snapshot(snapshot: Mapping[str, Any], *,
                        boxes: Mapping[str, Sequence[float]] | None = None,
                        kinds: Mapping[str, str] | None = None
                        ) -> tuple[TimelineItem, ...]:
    """The timeline view, read off a timeline SNAPSHOT.

    The snapshot is the shape the preservation invariant already reads
    (`{"tracks": [{"type", "index", "clips": [...]}]}`), so the planner
    can be handed the state the engine already keeps instead of a
    second read.  `boxes` and `kinds` are the two things a snapshot
    does not carry: where an item's ink landed, and what the item IS.
    """
    boxes = boxes or {}
    kinds = kinds or {}
    items: list[TimelineItem] = []
    for track in snapshot.get("tracks") or ():
        track_type = str(track.get("type") or track.get("track_type") or "")
        try:
            track_index = int(track.get("index") or track.get("track_index") or 0)
        except (TypeError, ValueError):
            continue
        for clip in track.get("clips") or ():
            unique_id = str(clip.get("unique_id") or "")
            if not unique_id:
                continue
            box = boxes.get(unique_id)
            items.append(TimelineItem(
                unique_id=unique_id,
                track_type=track_type,
                track_index=track_index,
                record_in=int(clip.get("record_in") or 0),
                record_out=int(clip.get("record_out") or 0),
                name=str(clip.get("name") or ""),
                kind=str(kinds.get(unique_id) or ""),
                box=(tuple(float(v) for v in box) if box else None),
                text=str(clip.get("text") or ""),
            ))
    return tuple(items)


# ── The revision: one request, or one refusal with a reason ──────────


@dataclass(frozen=True)
class TargetedRevision:
    """What a planner decided about one failing check.

    `status` is `planned` when a request exists and one of the refusal
    statuses otherwise.  `request` is the narrow `EditRequest`;
    `preservation` is what it declares it will not change; `reason` says
    why, in words a reader can act on.
    """

    check: QualityCheck
    status: str
    reason: str
    request: EditRequest | None = None
    revision_kind: str = ""
    scope: tuple[int, int] = ()
    preservation: tuple[Mapping[str, Any], ...] = ()
    next_action: str = ""

    @property
    def planned(self) -> bool:
        return self.status == PLANNED and self.request is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.check.as_dict(),
            "status": self.status,
            "reason": self.reason,
            "revision_kind": self.revision_kind,
            "scope": list(self.scope),
            "preservation": [dict(row) for row in self.preservation],
            "next_action": self.next_action,
            "request": (self.request.to_dict() if self.request is not None
                        else None),
        }


# ── Reading the delivered-file checks into checks ────────────────────


def objective_checks(verdict: Any, *, caption_spans: Mapping[str, Any] | None = None
                     ) -> list[QualityCheck]:
    """The delivered file's own checks, as `QualityCheck` rows.

    `verdict` is a `deliverable_qc.DeliverableQCVerdict` - the one
    verdict the delivery path and the QC gate share, so the two cannot
    grade the same file two ways.  Every leg is read: the ones that
    passed are read too, because a revision is only allowed to claim
    that the previously satisfied constraints still hold if they were
    MEASURED before.

    `caption_spans` maps a caption's label to the seconds it was
    planned for, for the caption legs whose interval the caption's own
    finding does not carry.
    """
    checks: list[QualityCheck] = []
    duration = getattr(verdict, "duration", None)
    if duration is not None:
        checks.append(QualityCheck(
            metric="duration", property="delivery_duration",
            passed=bool(duration.passed), detail=duration.detail,
            source="library.tools.deliverable_qc:run_deliverable_qc"))
    audio = getattr(verdict, "audio", None)
    if audio is not None:
        checks.extend(_audio_checks(audio))
    picture = getattr(verdict, "picture", None)
    if picture is not None:
        checks.extend(_picture_checks(picture))
    caption = getattr(verdict, "caption_spec", None)
    if caption is not None:
        checks.extend(_caption_checks(caption, caption_spans or {}))
    return checks


def _audio_checks(audio: Any) -> list[QualityCheck]:
    """The audio spec's three legs, read as checks."""
    source = "library.tools.deliverable_qc:verify_audio_spec (QA-004)"
    loudness = audio.loudness
    measured = dict(loudness.value) if isinstance(loudness.value, Mapping) else {}
    threshold = dict(loudness.threshold) if isinstance(
        loudness.threshold, Mapping) else {}
    measured.update({
        "target_lufs": threshold.get("target_lufs"),
        "target_tolerance": threshold.get("tolerance"),
        "target_true_peak_ceiling": threshold.get("true_peak_ceiling"),
    })
    return [
        QualityCheck(
            metric="loudness", property="integrated_loudness",
            passed=bool(loudness.passed), detail=loudness.detail,
            source=source, measured=measured),
        QualityCheck(
            metric="silence", property="digital_silence",
            passed=bool(audio.silence.passed), detail=audio.silence.detail,
            source=source,
            measured=(dict(audio.silence.value)
                      if isinstance(audio.silence.value, Mapping) else {})),
        QualityCheck(
            metric="channels", property="channel_presence",
            passed=bool(audio.channels.passed), detail=audio.channels.detail,
            source=source,
            measured=(dict(audio.channels.value)
                      if isinstance(audio.channels.value, Mapping) else {})),
    ]


def _picture_checks(picture: Any) -> list[QualityCheck]:
    """The picture spec's five legs, read as checks."""
    source = "library.tools.deliverable_qc:verify_picture_spec (QA-002)"
    rows = [
        ("resolution", "delivery_dimensions", picture.dimensions),
        ("framing", "frame_occupancy", picture.framing),
        ("overlays", "overlay_geometry", picture.overlays),
        ("black_frames", "undeclared_black", picture.black_frames),
        ("freeze_frames", "undeclared_freeze", picture.freeze_frames),
    ]
    checks = []
    for metric, prop, result in rows:
        measured = dict(result.value) if isinstance(result.value, Mapping) else {}
        start, end = _interval_of(measured)
        checks.append(QualityCheck(
            metric=metric, property=prop, passed=bool(result.passed),
            detail=result.detail, source=source, start=start, end=end,
            measured=measured))
    return checks


def _caption_checks(caption: Any, spans: Mapping[str, Any]) -> list[QualityCheck]:
    """The caption spec's findings, read as checks.

    A caption finding names ONE leg of ONE caption, so the check's
    label is the caption's own label and the check's property is the
    leg.  A finding that could not be measured is `not_measured` and
    is not a pass; it is carried as a check that did not pass, because
    an unmeasured caption is never a silently passing one.
    """
    source = ("library.tools.delivered_caption_spec:"
              "verify_delivered_captions (QA-003)")
    rate = float(getattr(caption, "fps", 0.0) or 0.0)
    checks: list[QualityCheck] = []
    for finding in getattr(caption, "findings", ()) or ():
        if getattr(finding, "verdict", "") == "not_applicable":
            continue
        measured = dict(getattr(finding, "measured", {}) or {})
        start, end = _caption_interval(finding, measured, spans, rate)
        checks.append(QualityCheck(
            metric="caption", property=_caption_property(finding),
            passed=bool(finding.passed), detail=finding.detail,
            source=source, label=str(finding.label),
            start=start, end=end, measured=measured))
    return checks


#: The caption spec's own leg names, and the property each one is.
#: One leg, one property: two legs that shared a property would share a
#: check's identity, and a check nobody can name is a check nobody can
#: revise.
_CAPTION_PROPERTIES = {
    "collision": "caption_collision",
    "text": "caption_words",
    "timing": "caption_timing",
    "placement": "caption_placement",
    "readability": "caption_readability",
    "wrapping": "caption_wrapping",
    "emphasis": "caption_emphasis",
    "missing": "caption_missing",
    "stale": "caption_stale",
    "read": "caption_unreadable",
}


def _caption_property(finding: Any) -> str:
    """The property a caption finding failed on."""
    return _CAPTION_PROPERTIES.get(str(finding.check), "caption_unreadable")


def _caption_interval(finding: Any, measured: Mapping[str, Any],
                      spans: Mapping[str, Any], rate: float
                      ) -> tuple[float | None, float | None]:
    """The seconds a caption finding covers, when they are known."""
    label = str(getattr(finding, "label", "") or "")
    first = measured.get("intended_first_frame")
    last = measured.get("intended_last_frame")
    if first is not None and last is not None and rate:
        return (float(first) / float(rate),
                (float(last) + 1.0) / float(rate))
    declared = spans.get(label)
    if isinstance(declared, (list, tuple)) and len(declared) == 2:
        return (float(declared[0]), float(declared[1]))
    return (None, None)


def _interval_of(measured: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """The seconds a picture measurement covers, when it names them."""
    where = measured.get("where")
    if isinstance(where, (list, tuple)) and where:
        first = where[0]
        if isinstance(first, Mapping):
            try:
                return (float(first["start"]), float(first["end"]))
            except (KeyError, TypeError, ValueError):
                return (None, None)
    start = measured.get("start")
    end = measured.get("end")
    if start is None or end is None:
        return (None, None)
    try:
        return (float(start), float(end))
    except (TypeError, ValueError):
        return (None, None)


# ── Planning: one check in, one narrow request out ──────────────────


def plan_revision(check: QualityCheck, context: RevisionContext, *,
                  history: Sequence[RevisionOutcome] = (),
                  ) -> TargetedRevision:
    """The narrow revision for ONE failing check.

    The request names only what the failure implicates, and declares
    everything else as a preservation requirement.  A check that
    already passed needs no revision; a SUBJECTIVE check is never
    revised by the engine; a check whose last attempt did not improve
    it is not attempted again.
    """
    if check.passed:
        raise TargetedRevisionError(
            f"check {check.key!r} passed, so it needs no revision")
    if check.kind == SUBJECTIVE:
        return TargetedRevision(
            check=check, status=SUBJECTIVE_STOP,
            reason=("the failure is a judgement about taste, not a "
                    "measurement, so no narrow edit can be planned from "
                    "it - a person decides this one"),
            next_action=ASK_CREATOR)
    if _already_attempted(check, history):
        return TargetedRevision(
            check=check, status=ALREADY_ATTEMPTED,
            reason=("the last revision of this check did not improve it "
                    "(and the file did not get worse), so retrying it "
                    "would be the same edit again - the remaining "
                    "decision is somebody's to make"),
            next_action=ASK_CREATOR)

    builder = _BUILDERS.get(check.property)
    if builder is None:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=(f"the {check.property!r} property has no narrow "
                    "revision this engine can state, so the failure is "
                    "reported rather than guessed at"),
            next_action=STOP)
    return builder(check, context)


def _already_attempted(check: QualityCheck,
                       history: Sequence[RevisionOutcome]) -> bool:
    """Whether this check was already revised without improving."""
    for outcome in history or ():
        if outcome.target_key == check.key and outcome.outcome in (
                NOT_IMPROVING, REGRESSED):
            return True
    return False


def _request(context: RevisionContext, operations: Sequence[EditOperation], *,
             preservation: Sequence[Mapping[str, Any]],
             reason: str) -> EditRequest:
    """The request a planner writes."""
    return EditRequest(
        id=f"qa009_{uuid.uuid4().hex[:12]}",
        project=context.project,
        timeline=context.timeline,
        operations=tuple(operations),
        originator={"kind": "pipeline",
                    "note": "a targeted quality-driven revision (QA-009)"},
        preservation=tuple(dict(row) for row in preservation),
    )


def _preservation(context: RevisionContext,
                  touched: Sequence[str]) -> tuple[Mapping[str, Any], ...]:
    """What the revision declares it will NOT change.

    The rows the revision does not touch, whole; the clips on the rows
    it does, one by one; and the captain's markers, which are the
    notes he wrote on the timeline and are never a revision's business.
    """
    touched_ids = {str(uid) for uid in touched}
    requirements: list[Mapping[str, Any]] = []
    for track_type, track_index in context.rows():
        on_row = context.items_on(track_type, track_index)
        if not any(item.unique_id in touched_ids for item in on_row):
            requirements.append({"kind": "row_unchanged",
                                 "track_type": track_type,
                                 "track_index": track_index})
            continue
        for item in on_row:
            if item.unique_id in touched_ids:
                continue
            requirements.append({"kind": "clip_unchanged",
                                 "unique_id": item.unique_id})
    for frame in context.markers or ():
        requirements.append({"kind": "marker_preserved", "frame": int(frame)})
    return tuple(requirements)


def _target(item: TimelineItem) -> EditTarget:
    """The target an operation names."""
    return EditTarget(
        unique_id=item.unique_id,
        track_type=item.track_type,
        track_index=item.track_index,
        name=item.name,
    )


# ── The builders, one per narrow revision ────────────────────────────


def _plan_audio_gain(check: QualityCheck, context: RevisionContext
                     ) -> TargetedRevision:
    """The file's loudness is off its target: gain the audio it carries.

    The parameter is READ OFF the measurement: the spec's own target
    and the file's own integrated loudness.  The revision aims at the
    TARGET and not at the edge of the tolerance band, because a master
    that lands exactly on the edge passes by zero margin and a later
    render tips it over.

    A file that is too QUIET cannot always be gained to its target: the
    true peak would pass the spec's ceiling.  That is refused by name,
    because a limiter is a mixing decision and this module does not
    make one.
    """
    measured = check.measured or {}
    target = measured.get("target_lufs")
    current = measured.get("input_i")
    if target is None or current is None:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the loudness measurement carries no integrated "
                    "loudness and no target, so there is no gain to "
                    "apply"),
            next_action=STOP)
    try:
        db = round(float(target) - float(current), 2)
    except (TypeError, ValueError):
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the loudness measurement is not readable, so there "
                    "is no gain to apply"),
            next_action=STOP)
    if db == 0:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the file's integrated loudness already reads the "
                    "spec's target, so the failure is not the loudness"),
            next_action=STOP)
    peak = measured.get("input_tp")
    ceiling = measured.get("target_true_peak_ceiling")
    if peak is not None and ceiling is not None and db > 0:
        try:
            if float(peak) + db > float(ceiling):
                return TargetedRevision(
                    check=check, status=NO_NARROW_REVISION,
                    reason=(f"gaining the file by {db:+.2f} dB would put "
                            f"its true peak at {float(peak) + db:+.2f} "
                            f"dBTP, over the spec's {float(ceiling):.1f} "
                            "dBTP ceiling - a limiter is a mixing "
                            "decision, not a narrow revision"),
                    next_action=STOP)
        except (TypeError, ValueError):
            pass
    first, last = context.span_of(check)
    audio_items = [item for item in context.items
                   if str(item.track_type) == "audio"
                   and item.covers(first, last)]
    if not audio_items:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the timeline carries no audio item over the "
                    "measured window, so there is nothing to gain"),
            next_action=STOP)
    operations = [
        EditOperation(
            op="audio.gain", target=_target(item),
            params={"db": db}, kind="direct",
            expected_effect=(f"item {item.unique_id!r} plays {db:+.2f} dB "
                             f"louder, so the file's integrated loudness "
                             f"reads the spec's {float(target):.1f} LUFS "
                             "target"))
        for item in audio_items
    ]
    preservation = _preservation(
        context, [item.unique_id for item in audio_items])
    request = _request(context, operations, preservation=preservation,
                       reason="the file's loudness is off its target")
    return TargetedRevision(
        check=check, status=PLANNED, reason=(
            f"the file measures {float(current):.2f} LUFS against a "
            f"{float(target):.1f} LUFS target, so {len(audio_items)} "
            "audio item(s) are gained by "
            f"{db:+.2f} dB and nothing else is touched"),
        request=request, revision_kind="audio_gain",
        scope=(first, last), preservation=preservation,
        next_action="submit_through_axi_after_authorization")


def _plan_overlay_position(check: QualityCheck, context: RevisionContext
                            ) -> TargetedRevision:
    """A graphic is drawn over a caption's own box: move the graphic.

    The graphic moves; the caption does not.  The caption's words and
    timing are the accepted part of the work, and a revision that
    re-times a caption to fix a collision would trade an accepted
    thing for a broken one.

    The displacement is the smallest one that clears the caption's box
    AND collides with nothing else on the timeline, and it must stay
    inside the delivery frame.  When two directions are equally good
    the revision is AMBIGUOUS and says so: a tie is a decision, and
    picking one by author order is how a plan acquires an opinion.
    """
    measured = check.measured or {}
    caption_box = _box(measured.get("caption_box"))
    if caption_box is None:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the collision names no box for the caption, so the "
                    "graphic that covers it cannot be placed"),
            next_action=STOP)
    frame = _box(measured.get("frame"))
    first, last = context.span_of(check)
    candidates = [item for item in context.items
                  if item.box is not None
                  and item.covers(first, last)
                  and _overlap(item.box, caption_box) >= _collision_overlap()]
    if not candidates:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("no item on the timeline draws over the caption's box "
                    "- the graphic that collides with it is not on the "
                    "timeline, so there is no item to move"),
            next_action=STOP)
    if len(candidates) > 1:
        return TargetedRevision(
            check=check, status=AMBIGUOUS,
            reason=(f"{len(candidates)} items draw over the caption's box "
                    f"({', '.join(item.unique_id for item in candidates)}), "
                    "so the one to move is not decided by the measurement"),
            next_action=ASK_CREATOR)
    graphic = candidates[0]
    width = height = None
    if frame is not None:
        width, height = frame[2] - frame[0], frame[3] - frame[1]
    moves = _clearing_moves(graphic.box, caption_box, width, height)
    if not moves:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the graphic cannot be moved clear of the caption "
                    "inside the delivery frame, so the collision needs a "
                    "layout decision rather than a displacement"),
            next_action=ASK_CREATOR)
    for move in moves:
        moved = _moved_box(graphic.box, move[0], move[1])
        blockers = [item for item in context.items
                    if item.unique_id != graphic.unique_id
                    and item.box is not None
                    and item.covers(first, last)
                    and _overlap(moved, item.box) >= _collision_overlap()]
        if blockers:
            continue
        break
    else:
        return TargetedRevision(
            check=check, status=AMBIGUOUS,
            reason=("every displacement that clears the caption puts the "
                    "graphic over something else on the timeline, so the "
                    "place it should go is not decided by the measurement"),
            next_action=ASK_CREATOR)
    dx, dy = move
    operations = [EditOperation(
        op="overlay.position", target=_target(graphic),
        params={"dx": float(dx), "dy": float(dy)}, kind="direct",
        expected_effect=(f"item {graphic.unique_id!r} moves {float(dx):+.0f} "
                         f"px across and {float(dy):+.0f} px down, clear of "
                         "the caption's own box; its size, its timing and "
                         "its words are unchanged"))]
    preservation = _preservation(context, [graphic.unique_id])
    request = _request(context, operations, preservation=preservation,
                       reason="a graphic is drawn over a caption's box")
    return TargetedRevision(
        check=check, status=PLANNED, reason=(
            f"one graphic draws over the caption's own box, so it moves "
            f"{float(dx):+.0f}/{float(dy):+.0f} px and the caption is left "
            "exactly as it was accepted"),
        request=request, revision_kind="overlay_position",
        scope=(first, last), preservation=preservation,
        next_action="submit_through_axi_after_authorization")


def _plan_overlay_resize(check: QualityCheck, context: RevisionContext
                         ) -> TargetedRevision:
    """An overlay was drawn at the wrong size: draw it at the asked size.

    The asked-for size is the plan's own box, and the drawn size is
    the delivered file's own measurement, so the parameter is the
    ratio between two measured numbers.
    """
    measured = check.measured or {}
    overlays = measured.get("overlays")
    if not isinstance(overlays, (list, tuple)) or not overlays:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the overlay measurement names no overlay, so there "
                    "is no size to change"),
            next_action=STOP)
    defects = [row for row in overlays
               if isinstance(row, Mapping) and row.get("verdict") == "defect"]
    if not defects:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("no overlay was measured as wrong, so the failure is "
                    "not an overlay's size"),
            next_action=STOP)
    if len(defects) > 1:
        return TargetedRevision(
            check=check, status=AMBIGUOUS,
            reason=(f"{len(defects)} overlays were measured as wrong, so "
                    "the one to resize is not decided by the measurement"),
            next_action=ASK_CREATOR)
    row = defects[0]
    label = str(row.get("label") or "")
    scale = row.get("measured_scale")
    expected = row.get("expected_ink_box")
    if scale is None or not expected:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the overlay measurement carries no drawn size and no "
                    "asked-for size, so there is no size to apply"),
            next_action=STOP)
    first, last = context.span_of(check)
    candidates = [item for item in context.items
                  if item.kind == "graphic" and item.covers(first, last)
                  and (not label or label in item.name or label in item.text)]
    if not candidates:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=(f"no graphic on the timeline is the {label!r} overlay, "
                    "so there is no item to resize"),
            next_action=STOP)
    if len(candidates) > 1:
        return TargetedRevision(
            check=check, status=AMBIGUOUS,
            reason=(f"{len(candidates)} graphics could be the {label!r} "
                    "overlay, so the one to resize is not decided by the "
                    "measurement"),
            next_action=ASK_CREATOR)
    graphic = candidates[0]
    box = _box(expected)
    if box is None:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the overlay's asked-for box is not readable, so "
                    "there is no size to apply"),
            next_action=STOP)
    width = round(float(box[2]) - float(box[0]), 2)
    height = round(float(box[3]) - float(box[1]), 2)
    operations = [EditOperation(
        op="overlay.resize", target=_target(graphic),
        params={"width": width, "height": height}, kind="direct",
        expected_effect=(f"item {graphic.unique_id!r} is drawn at the "
                         f"plan's {width:.0f}x{height:.0f} box instead of "
                         f"the size it landed at; its place and its "
                         "timing are unchanged"))]
    preservation = _preservation(context, [graphic.unique_id])
    request = _request(context, operations, preservation=preservation,
                       reason="an overlay was drawn at the wrong size")
    return TargetedRevision(
        check=check, status=PLANNED, reason=(
            f"one overlay was drawn at {scale} of the size the plan "
            f"asked for, so it is resized to {width:.0f}x{height:.0f} and "
            "nothing else is touched"),
        request=request, revision_kind="overlay_resize",
        scope=(first, last), preservation=preservation,
        next_action="submit_through_axi_after_authorization")


def _plan_caption_text(check: QualityCheck, context: RevisionContext
                       ) -> TargetedRevision:
    """The caption that reached the file says other words: rewrite them.

    The words are the PLAN's words, taken from the measurement's own
    intent, so the revision restores what was asked for and does not
    invent a new phrasing.
    """
    measured = check.measured or {}
    intended = measured.get("intended_text")
    if not intended:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("the caption measurement names no intended words, so "
                    "there is no text to write"),
            next_action=STOP)
    first, last = context.span_of(check)
    candidates = [item for item in context.items
                  if item.kind == "caption" and item.covers(first, last)]
    if not candidates:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("no caption item covers the measured window, so the "
                    "card that reached the file cannot be named"),
            next_action=STOP)
    if len(candidates) > 1:
        return TargetedRevision(
            check=check, status=AMBIGUOUS,
            reason=(f"{len(candidates)} caption items cover the measured "
                    "window, so the one to rewrite is not decided by the "
                    "measurement"),
            next_action=ASK_CREATOR)
    caption = candidates[0]
    operations = [EditOperation(
        op="caption_fix", target=_target(caption),
        params={"text": str(intended)}, kind="direct",
        expected_effect=(f"item {caption.unique_id!r} carries the planned "
                         f"words {str(intended)!r}; its timing and its "
                         "place are unchanged"))]
    preservation = _preservation(context, [caption.unique_id])
    request = _request(context, operations, preservation=preservation,
                       reason="the caption that reached the file says "
                              "other words")
    return TargetedRevision(
        check=check, status=PLANNED, reason=(
            "one caption carries other words, so its card is rewritten "
            "with the plan's own words and nothing else is touched"),
        request=request, revision_kind="caption_text",
        scope=(first, last), preservation=preservation,
        next_action="submit_through_axi_after_authorization")


def _plan_hold_repair(check: QualityCheck, context: RevisionContext
                      ) -> TargetedRevision:
    """The picture stopped moving: repair the hold's window.

    A freeze-hold tail whose comp media window no longer covers the
    frames it plays freezes the picture.  The repair is in place, on
    that item alone.
    """
    first, last = context.span_of(check)
    candidates = [item for item in context.items
                  if item.kind == "hold" and item.covers(first, last)]
    if not candidates:
        return TargetedRevision(
            check=check, status=NO_NARROW_REVISION,
            reason=("no freeze-hold item covers the frozen window, so the "
                    "freeze is not a hold whose window can be repaired"),
            next_action=STOP)
    if len(candidates) > 1:
        return TargetedRevision(
            check=check, status=AMBIGUOUS,
            reason=(f"{len(candidates)} freeze-hold items cover the "
                    "window, so the one to repair is not decided by the "
                    "measurement"),
            next_action=ASK_CREATOR)
    hold = candidates[0]
    operations = [EditOperation(
        op="hold.repair_window", target=_target(hold),
        params={}, kind="direct",
        expected_effect=(f"item {hold.unique_id!r} plays its own frames "
                         "again instead of holding one; its span, its "
                         "pixels and its grade are unchanged"))]
    preservation = _preservation(context, [hold.unique_id])
    request = _request(context, operations, preservation=preservation,
                       reason="the picture stopped moving")
    return TargetedRevision(
        check=check, status=PLANNED, reason=(
            "one freeze-hold item covers the frozen window, so its "
            "window is repaired in place and nothing else is touched"),
        request=request, revision_kind="hold_repair",
        scope=(first, last), preservation=preservation,
        next_action="submit_through_axi_after_authorization")


def _plan_needs_source(check: QualityCheck, context: RevisionContext
                      ) -> TargetedRevision:
    """The fix needs material the timeline does not carry.

    A picture that went black and a dialogue that was lost are both
    fixed by putting SOMETHING there, and choosing what is an
    editorial decision.  The engine reports the interval and stops.
    """
    window = (f"{check.start:.3f}s-{check.end:.3f}s"
              if check.start is not None and check.end is not None
              else "the whole file")
    return TargetedRevision(
        check=check, status=NEEDS_SOURCE,
        reason=(f"the failure covers {window} and the fix needs material "
                "the timeline does not carry - choosing what goes there "
                "is an editorial decision, so the engine reports the "
                "interval instead of choosing for it"),
        next_action=ASK_CREATOR)


#: One builder per property.  A property that is not in this table has
#: no narrow revision, and the planner says so by name.
_BUILDERS = {
    "integrated_loudness": _plan_audio_gain,
    "caption_collision": _plan_overlay_position,
    "overlay_geometry": _plan_overlay_resize,
    "caption_words": _plan_caption_text,
    "undeclared_freeze": _plan_hold_repair,
    "digital_silence": _plan_needs_source,
    "undeclared_black": _plan_needs_source,
}


# ── The geometry a displacement is measured with ─────────────────────


def _box(value: Any) -> tuple[float, float, float, float] | None:
    """A box, when it is readable."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError):
        return None


def _overlap(one: tuple[float, float, float, float],
             other: tuple[float, float, float, float]) -> float:
    """How much two boxes overlap, as a fraction of the smaller one."""
    width = min(one[2], other[2]) - max(one[0], other[0])
    height = min(one[3], other[3]) - max(one[1], other[1])
    if width <= 0 or height <= 0:
        return 0.0
    area = width * height
    smaller = ((min(one[2] - one[0], other[2] - other[0]))
               * (min(one[3] - one[1], other[3] - other[1])))
    if smaller <= 0:
        return 0.0
    return area / smaller


def _moved_box(box: tuple[float, float, float, float],
               dx: float, dy: float) -> tuple[float, float, float, float]:
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def _clearing_moves(graphic: tuple[float, float, float, float],
                    caption: tuple[float, float, float, float],
                    width: float | None, height: float | None
                    ) -> list[tuple[float, float]]:
    """The displacements that clear the caption's box, smallest first.

    Four directions are considered, because a graphic can be beside a
    caption as easily as above it.  A displacement that would take the
    graphic outside the delivery frame is not a displacement.

    The clearance is a full CAPTION-INK-HEIGHT, not the two pixels of
    measurement error alone.  A graphic left a couple of pixels from a
    caption is READ as part of it - the caption's own measured box
    grows to cover the neighbour - so the collision would be measured
    again after the move, and the revision would have changed nothing
    the check can see.  One caption-height of clearance is the smallest
    gap the caption's own box cannot grow into.
    """
    clearance = max(CLEARANCE_PX, caption[3] - caption[1])
    moves: list[tuple[float, float]] = []
    up = graphic[3] - caption[1] + clearance
    down = caption[3] - graphic[1] + clearance
    left = graphic[2] - caption[0] + clearance
    right = caption[2] - graphic[0] + clearance
    if up > 0 and (height is None or graphic[1] - up >= 0):
        moves.append((0.0, -up))
    if down > 0 and (height is None or graphic[3] + down <= height):
        moves.append((0.0, down))
    if left > 0 and (width is None or graphic[0] - left >= 0):
        moves.append((-left, 0.0))
    if right > 0 and (width is None or graphic[2] + right <= width):
        moves.append((right, 0.0))
    moves.sort(key=lambda move: (math.hypot(move[0], move[1]), move))
    return moves


# ── Verification: the corrected issue AND the satisfied constraints ──


@dataclass(frozen=True)
class RevisionOutcome:
    """What a revision did, measured against the checks.

    `outcome` is `fixed` when the targeted check now passes and nothing
    that passed before stopped passing.  `not_improving` is when the
    targeted check still fails - and that is the signal to STOP, not
    to try again.  `regressed` is when the revision fixed its target
    and broke something else.
    """

    outcome: str
    target_key: tuple[str, str, str]
    fixed: tuple[tuple[str, str, str], ...] = ()
    still_failing: tuple[tuple[str, str, str], ...] = ()
    regressions: tuple[tuple[str, str, str], ...] = ()
    detail: str = ""

    @property
    def next_action(self) -> str:
        """What a reader does with this outcome."""
        if self.outcome == FIXED:
            return ACCEPT
        if self.outcome == NOT_IMPROVING:
            return STOP
        return STOP

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "target_key": list(self.target_key),
            "fixed": [list(key) for key in self.fixed],
            "still_failing": [list(key) for key in self.still_failing],
            "regressions": [list(key) for key in self.regressions],
            "detail": self.detail,
            "next_action": self.next_action,
        }


def verify_revision(before: Sequence[QualityCheck],
                    after: Sequence[QualityCheck],
                    revision: TargetedRevision) -> RevisionOutcome:
    """Judge a revision against the checks before and after it.

    Two questions, answered separately:

    * did the targeted check flip to passing?
    * did every check that was passing before still pass?

    A revision that answers the first and fails the second is a
    REGRESSION, and it is reported as one: the corrected issue is not
    the whole story, and a delivery that traded an accepted portion of
    the work for a fixed one is not a fix.
    """
    before_map = {check.key: check for check in before or ()}
    after_map = {check.key: check for check in after or ()}
    target = revision.check.key
    still = tuple(key for key, check in after_map.items() if not check.passed)
    fixed = ((target,) if target in after_map and after_map[target].passed
             else ())
    regressions = tuple(
        key for key, check in after_map.items()
        if not check.passed
        and (key not in before_map or before_map[key].passed))
    if target in still:
        outcome = NOT_IMPROVING
        detail = (f"the check {list(target)!r} still fails after the "
                  "revision, so the revision did not improve it")
    elif regressions:
        outcome = REGRESSED
        detail = (f"the revision fixed {list(target)!r} but "
                  f"{len(regressions)} check(s) that passed before now "
                  f"fail: {[list(key) for key in regressions]}")
    else:
        outcome = FIXED
        detail = (f"the check {list(target)!r} now passes and every check "
                  "that passed before still passes")
    return RevisionOutcome(
        outcome=outcome, target_key=target, fixed=fixed,
        still_failing=still, regressions=regressions, detail=detail)


__all__ = [
    "ACCEPT",
    "ALREADY_ATTEMPTED",
    "AMBIGUOUS",
    "ASK_CREATOR",
    "FIXED",
    "NEEDS_SOURCE",
    "NOT_IMPROVING",
    "NO_NARROW_REVISION",
    "OBJECTIVE",
    "PLANNED",
    "PROPERTIES",
    "REGRESSED",
    "REVISION_KINDS",
    "STOP",
    "SUBJECTIVE",
    "SUBJECTIVE_STOP",
    "QualityCheck",
    "RevisionContext",
    "RevisionOutcome",
    "TargetedRevision",
    "TargetedRevisionError",
    "TimelineItem",
    "items_from_snapshot",
    "objective_checks",
    "plan_revision",
    "verify_revision",
]
