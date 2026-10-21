"""Do two changes disagree about the same editorial decision?

The gap this closes (the captain's punch list, VC-005): Ren could say
whether two patches compose (`patch_algebra`, by what each operation
WRITES - a key prefix) and could carry a captain's hand edit through a
rebuild (`editor_edit_carry`, by what the editor's change SAYS in
source terms). Neither could say whether a Ren edit and a hand edit on
the same clip AGREE, because the two vocabularies never met.

A key-prefix algebra answers "do these two writes meet?". It cannot
answer "do these two changes disagree about the same decision?",
because a disagreement is a claim about the SAME thing:

    two edits that trim the same clip to different windows
    two edits that move the same clip to different places
    two edits that set the same property on the same clip to
        different values
    a hand edit that moves a clip and a Ren edit that writes to a
        clip after it, whose frames the move invalidated

What this module owns
---------------------
One CLASSIFICATION of a pair of changes, on four axes, and the DECISION
that follows it:

    source interval   the passage of the source media the change claims
    timeline region   the frames on the timeline the change claims
    target            the item the change governs
    property key      the property the change writes

Three verdicts, and the middle one is the point:

    INDEPENDENT   the two changes claim different things. They can both
                  land, and merging them preserves both.
    CONFLICT      the two changes claim the SAME thing differently.
                  Resolution is required, and the report names both
                  sides - the version that carried each.
    UNPROVEN      the two changes cannot be shown to be independent.
                  An absent measurement is not evidence of agreement,
                  so this is NOT a merge: it requires resolution too.

Where the evidence comes from
-----------------------------
A change is read off a RECORDED state, never guessed:

    edits_between      two snapshots of one timeline: what changed
                       between them, and nothing else
    edits_from_operations
                       a prepared EditPatch's operations, read against
                       the base snapshot it was planned on
    edits_from_carried_edits
                       the carried-edit ledger, whose entries already
                       speak in source terms

A change nobody recorded - the captain's hand edit - is read the same
way: the difference between the snapshot before it and the snapshot
after it. That difference is a STATE, not an intent, so the module
never says what the captain MEANT; it says what the timeline now holds
and whether a Ren change agrees with it.

Who yields
----------
A captain's edit and a Ren edit that conflict do not resolve by
strength: the captain's hand is the state the captain accepted. The
report's `resolution` names the captain's edit as the one that stands
and Ren's edit as refused, and says so for every pair it reports.

Auto-merge
----------
`merge()` returns both sides ONLY when every pair is INDEPENDENT and no
pair is UNPROVEN. Anything else raises `SemanticConflictRefused` with
both versions named. A merge that drops one side's effect is not a
merge, so the merged set is checked: every edit of both sides is still
in it, with its own version, after the identical writes are folded.

`tests/unit/reels/test_semantic_conflict.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

# ── Who made a change ───────────────────────────────────────────────────

ORIGIN_REN = "ren"
ORIGIN_CAPTAIN = "captain"

ORIGINS = (ORIGIN_REN, ORIGIN_CAPTAIN)

#: The origins that are NOT Ren: a hand edit, whoever's hand it was.
HUMAN_ORIGINS = (ORIGIN_CAPTAIN,)

# ── The verdicts ────────────────────────────────────────────────────────

INDEPENDENT = "independent"
CONFLICT = "conflict"
UNPROVEN = "unproven"

VERDICTS = (INDEPENDENT, CONFLICT, UNPROVEN)

# ── The axes ────────────────────────────────────────────────────────────

AXIS_SOURCE = "source_interval"
AXIS_TIMELINE = "timeline_region"
AXIS_TARGET = "target"
AXIS_PROPERTY = "property_key"
AXIS_COORDINATES = "timeline_coordinates"

AXES = (AXIS_SOURCE, AXIS_TIMELINE, AXIS_TARGET, AXIS_PROPERTY,
        AXIS_COORDINATES)

# ── The kinds of change ─────────────────────────────────────────────────

KIND_INSERT = "insert"
KIND_DELETE = "delete"
KIND_SOURCE_WINDOW = "source_window"
KIND_PLACEMENT = "placement"
KIND_PROPERTY = "property"
KIND_ENABLED = "enabled"
KIND_MARKER = "marker"

KINDS = (KIND_INSERT, KIND_DELETE, KIND_SOURCE_WINDOW, KIND_PLACEMENT,
         KIND_PROPERTY, KIND_ENABLED, KIND_MARKER)

#: Changes that move the timeline's own coordinates. One of these on a
#: row invalidates the frames of everything after it on that row, which
#: is why a change on the same row past that point is UNPROVEN rather
#: than independent.
STRUCTURAL_KINDS = (KIND_INSERT, KIND_DELETE, KIND_SOURCE_WINDOW,
                     KIND_PLACEMENT)

#: Changes that PLACE content on a row. A removal does not place
#: anything: the frames a deleted item occupied are where it WAS, and
#: comparing them with the frames another change claims would read every
#: swap - remove one item, place another where it was - as a clash.
PLACING_KINDS = (KIND_INSERT, KIND_SOURCE_WINDOW, KIND_PLACEMENT)

#: The clip fields that are read as a property of the item. A change in
#: any of them is a claim about that item.
_CLIP_PROPERTY_FIELDS = ("transform", "color", "fusion", "composite",
                         "clip_color", "fades", "speed", "keyframes",
                         "flags", "linked_clips")


class SemanticConflictRefused(RuntimeError):
    """Two changes disagree, or cannot be shown to agree.

    The refusal names BOTH sides: the version that carried each change
    and what each claims. A conflict with one name is a conflict nobody
    can resolve.
    """

    def __init__(self, report: ConflictReport):
        self.report = report
        super().__init__(report.reason())


# ── One change ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Edit:
    """One change, on the four axes two changes are compared on.

    `target` is the item the change governs - a Resolve unique id for a
    clip, `timeline` for a timeline-level marker. `row` is the row the
    item lives on. `source` is the passage of the source media the
    change claims, `timeline_region` the frames on the timeline it
    claims, and `properties` the property keys it writes with their
    values.

    An ABSENT measurement is absent, never a default: `source` is None
    when the change says nothing about a source passage, and a
    comparison that would need it says so instead of assuming it.
    """

    edit_id: str
    origin: str
    version: str
    kind: str
    target: str
    row: str = ""
    source_identity: str = ""
    source: tuple | None = None
    timeline_region: tuple | None = None
    properties: tuple = ()

    # ── what this change writes ─────────────────────────────────────

    def property_map(self) -> dict:
        """The property keys it writes, with the values it writes."""
        return {str(key): value for key, value in self.properties}

    def property_keys(self) -> tuple:
        return tuple(str(key) for key, _ in self.properties)

    def axes(self) -> frozenset:
        """The axes this change writes.

        A property write and an enabled write are different axes of the
        same item, so they do not meet. A source window and a placement
        are different axes too - but a source window ALSO moves the
        item's frames, which is the coordinate effect, not the axis.
        """
        if self.kind == KIND_PROPERTY:
            return frozenset(f"property:{key}" for key in self.property_keys())
        if self.kind == KIND_ENABLED:
            return frozenset({"enabled"})
        if self.kind == KIND_MARKER:
            return frozenset(f"marker:{key}" for key in self.property_keys())
        if self.kind == KIND_SOURCE_WINDOW:
            return frozenset({"source_window"})
        if self.kind == KIND_PLACEMENT:
            return frozenset({"placement"})
        if self.kind in (KIND_INSERT, KIND_DELETE):
            return frozenset({"existence", "placement"})
        return frozenset({self.kind})

    def structural(self) -> bool:
        """Whether this change moves the timeline's coordinates."""
        return self.kind in STRUCTURAL_KINDS

    def describe(self) -> str:
        """One line naming what this change claims."""
        parts = [f"{self.kind}"]
        if self.row:
            parts.append(f"on {self.row}")
        if self.target:
            parts.append(f"of {self.target}")
        if self.source is not None:
            parts.append(f"source {_span(self.source)}")
        if self.timeline_region is not None:
            parts.append(f"frames {_span(self.timeline_region)}")
        if self.properties:
            parts.append("writing " + ", ".join(
                f"{key}={_short(value)}" for key, value in self.properties))
        return " ".join(parts)


@dataclass(frozen=True)
class EditSet:
    """Every change one version carries, and the version that carries it."""

    version: str
    origin: str
    edits: tuple = ()

    @classmethod
    def of(cls, version: str, origin: str, edits: Iterable) -> EditSet:
        return cls(version=str(version), origin=str(origin),
                   edits=tuple(edits))

    def __len__(self) -> int:
        return len(self.edits)

    def __iter__(self):
        return iter(self.edits)


# ── What the comparison found ──────────────────────────────────────────


@dataclass(frozen=True)
class PairVerdict:
    """One pair of changes, and what they claim about each other."""

    verdict: str
    axis: str
    reason: str
    left: Edit
    right: Edit

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "axis": self.axis,
            "reason": self.reason,
            "left": _edit_dict(self.left),
            "right": _edit_dict(self.right),
        }


@dataclass
class ConflictReport:
    """What two versions' changes do to each other.

    `conflicts` and `unproven` name BOTH sides of every pair: the
    version that carried each change and what that change claims.
    `independent` records the pairs that can both land.
    """

    left: EditSet
    right: EditSet
    independent: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    unproven: list = field(default_factory=list)

    @property
    def mergeable(self) -> bool:
        """Whether the two sides can both land, unchanged."""
        return not self.conflicts and not self.unproven

    def resolution(self) -> str:
        """Who yields, when the two sides cannot both land."""
        if self.mergeable:
            return "both preserved"
        human, machine = self._human_and_machine()
        if human is not None and machine is not None:
            return (f"the captain's edit ({human.version}) stands; "
                    f"Ren's edit ({machine.version}) is refused")
        return "resolution required"

    def _human_and_machine(self) -> tuple:
        """The human side and the Ren side, when there is one of each."""
        for pair in list(self.conflicts) + list(self.unproven):
            for human, other in ((pair.left, pair.right),
                                 (pair.right, pair.left)):
                if human.origin in HUMAN_ORIGINS:
                    return human, other
        return None, None

    def reason(self) -> str:
        """The refusal text: what disagrees, and who holds each side."""
        lines = [
            (f"REFUSING to merge {self.left.version!r} with "
             f"{self.right.version!r}: the two changes do not agree.")]
        for pair in list(self.conflicts) + list(self.unproven):
            lines.append(f"  [{pair.axis}] {pair.left.version}: "
                         f"{pair.left.describe()}")
            lines.append(f"  [{pair.axis}] {pair.right.version}: "
                         f"{pair.right.describe()}")
            lines.append(f"    {pair.reason}")
        lines.append(self.resolution())
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "left": {"version": self.left.version, "origin": self.left.origin,
                     "edits": [_edit_dict(edit) for edit in self.left.edits]},
            "right": {"version": self.right.version,
                      "origin": self.right.origin,
                      "edits": [_edit_dict(edit) for edit in self.right.edits]},
            "mergeable": self.mergeable,
            "resolution": self.resolution(),
            "independent": [pair.as_dict() for pair in self.independent],
            "conflicts": [pair.as_dict() for pair in self.conflicts],
            "unproven": [pair.as_dict() for pair in self.unproven],
        }


# ── The comparison ─────────────────────────────────────────────────────


def compare(left: Edit, right: Edit) -> PairVerdict:
    """What two changes claim about each other.

    The rules, in the order they are asked:

    1. The same TARGET, writing the same PROPERTY KEY with different
       values: the two changes disagree about the same editorial
       decision. That is a CONFLICT.
    2. The same TARGET, one of them removing the item the other edits:
       a CONFLICT - the edit has nothing left to write on.
    3. The same TARGET, both claiming a different source window or a
       different place: a CONFLICT.
    4. The same TARGET, writing different axes: INDEPENDENT. A trim and
       a framing change are two decisions about one item, and both can
       hold.
    5. Different targets on the same ROW, claiming overlapping frames:
       a CONFLICT. One row holds one item at a frame.
    6. Otherwise: INDEPENDENT.
    """
    if left.target and left.target == right.target:
        return _compare_same_target(left, right)
    if (left.row and left.row == right.row
            and _places(left) and _places(right)
            and _overlaps(left.timeline_region, right.timeline_region)):
        return PairVerdict(
            CONFLICT, AXIS_TIMELINE,
            f"both changes claim frames {_span(left.timeline_region)} on "
            f"{left.row}, and one row holds one item at a frame",
            left, right)
    return PairVerdict(
        INDEPENDENT, AXIS_TARGET,
        "the two changes claim different items", left, right)


def _compare_same_target(left: Edit, right: Edit) -> PairVerdict:
    """Two changes on the SAME item."""
    left_properties = left.property_map()
    right_properties = right.property_map()
    for key in sorted(set(left_properties) & set(right_properties)):
        if _same_value(left_properties[key], right_properties[key]):
            continue
        return PairVerdict(
            CONFLICT, AXIS_PROPERTY,
            f"both write {key} on the same item, to different values "
            f"({_short(left_properties[key])} and "
            f"{_short(right_properties[key])})",
            left, right)
    if left.kind == KIND_DELETE != right.kind:
        return PairVerdict(
            CONFLICT, AXIS_TARGET,
            "one change removes the item the other edits", left, right)
    if left.kind == KIND_INSERT and right.kind == KIND_DELETE:
        return PairVerdict(
            CONFLICT, AXIS_TARGET,
            "one change places the item the other removes", left, right)
    if (left.kind == KIND_SOURCE_WINDOW and right.kind == KIND_SOURCE_WINDOW
            and not _same_span(left.source, right.source)):
        return PairVerdict(
            CONFLICT, AXIS_SOURCE,
            f"both claim a different source window for the same item "
            f"({_span(left.source)} and {_span(right.source)})",
            left, right)
    if (left.kind == KIND_PLACEMENT and right.kind == KIND_PLACEMENT
            and not _same_span(left.timeline_region, right.timeline_region)):
        return PairVerdict(
            CONFLICT, AXIS_TIMELINE,
            f"both claim a different place for the same item "
            f"({_span(left.timeline_region)} and "
            f"{_span(right.timeline_region)})",
            left, right)
    if (left.kind == KIND_SOURCE_WINDOW and right.kind == KIND_PLACEMENT
            or left.kind == KIND_PLACEMENT
            and right.kind == KIND_SOURCE_WINDOW):
        return PairVerdict(
            CONFLICT, AXIS_TIMELINE,
            "one change claims a different source window for the item "
            "the other moves", left, right)
    return PairVerdict(
        INDEPENDENT, AXIS_TARGET,
        "the two changes write different axes of the same item",
        left, right)


def detect(left: EditSet, right: EditSet) -> ConflictReport:
    """What two versions' changes do to each other.

    Every pair of changes is compared, and the pairs are then judged
    once more against the COORDINATE frame: a change that moves a row's
    frames invalidates the frames of everything after it on that row, so
    a change on that row past that point is UNPROVEN rather than
    independent - it may be independent, and nothing here can prove it.
    """
    report = ConflictReport(left=left, right=right)
    for left_edit in left.edits:
        for right_edit in right.edits:
            found = compare(left_edit, right_edit)
            if found.verdict == CONFLICT:
                report.conflicts.append(found)
            elif found.verdict == INDEPENDENT:
                report.independent.append(found)
    for left_edit, right_edit, axis, reason in _coordinate_problems(left, right):
        report.unproven.append(PairVerdict(UNPROVEN, axis, reason,
                                           left_edit, right_edit))
    return report


def merge(left: EditSet, right: EditSet) -> EditSet:
    """Both sides, merged - or a refusal naming both.

    The merged set keeps every change of both sides, with the version
    that carried it, except where the two sides write the SAME thing:
    those are one change, and the version that carries it is the first
    one, with the second named as agreeing with it.
    """
    report = detect(left, right)
    if not report.mergeable:
        raise SemanticConflictRefused(report)
    merged = list(left.edits)
    for edit in right.edits:
        twin = next((other for other in left.edits
                     if _same_write(other, edit)), None)
        if twin is not None:
            continue
        merged.append(edit)
    version = f"{left.version} + {right.version}"
    origin = left.origin if left.origin == right.origin else "mixed"
    return EditSet.of(version, origin, merged)


# ── The coordinate frame ───────────────────────────────────────────────


def _coordinate_problems(left: EditSet, right: EditSet):
    """The pairs whose frames a structural change may have invalidated.

    A change that moves a row's frames (an insert, a delete, a trim, a
    move) invalidates the frames of everything after it on that row. A
    change on that row past that point is UNPROVEN: it may be
    independent, and nothing here can prove it.
    """
    problems = []
    for structural_set, other_set in ((left, right), (right, left)):
        shifts, everywhere = _row_shifts(structural_set)
        if not shifts and everywhere is None:
            continue
        for edit in other_set.edits:
            point = shifts.get(edit.row)
            if everywhere is not None:
                point = (everywhere if point is None
                         else min(point, everywhere))
            if point is None or edit.timeline_region is None:
                continue
            if edit.timeline_region[0] < point:
                continue
            if _same_target_exists(structural_set, edit):
                # The same item: the comparison already judged it.
                continue
            problems.append((
                edit, other_set,
                AXIS_COORDINATES,
                (f"{structural_set.version} moved the frames of {edit.row} "
                 f"from frame {point}, and this change claims frames "
                 f"{_span(edit.timeline_region)} on that row")))
    return problems


def _row_shifts(edits: EditSet) -> tuple:
    """The earliest frame each row's coordinates change at, and the
    earliest frame the TIMELINE's own frame changes at.

    A change with no row moved the timeline's own frame, so it moves
    every row.
    """
    shifts = {}
    everywhere = None
    for edit in edits.edits:
        if not edit.structural():
            continue
        region = edit.timeline_region
        if region is None:
            continue
        point = int(region[0])
        if not edit.row:
            if everywhere is None or point < everywhere:
                everywhere = point
            continue
        if edit.row not in shifts or point < shifts[edit.row]:
            shifts[edit.row] = point
    return shifts, everywhere


def _same_target_exists(edits: EditSet, edit: Edit) -> bool:
    return any(other.target == edit.target for other in edits.edits)


# ── Reading a change off a recorded state ──────────────────────────────


def edits_between(before: dict, after: dict, *, origin: str,
                  version: str) -> list:
    """The changes between two snapshots of one timeline.

    The snapshots are the shape `reel_read` reads: `{"tracks": [...]}`,
    each clip carrying its `unique_id`, its row, its source range, its
    record span, its transform and its markers. A clip is matched by
    its `unique_id` - a Resolve identity - so a clip that moved is the
    SAME clip and not a new one.

    What is compared is what a read can see: the source window, the
    record span, the transform and the other item properties, the
    enabled state and the markers. A difference in any of them is a
    change; nothing else is read as one.
    """
    before_clips = _clips_by_id(before)
    after_clips = _clips_by_id(after)
    edits = []
    for unique_id in sorted(set(before_clips) | set(after_clips)):
        old = before_clips.get(unique_id)
        new = after_clips.get(unique_id)
        if old is None:
            edits.append(_insert_edit(new, origin=origin, version=version))
            continue
        if new is None:
            edits.append(_delete_edit(old, origin=origin, version=version))
            continue
        edits.extend(_changed_edits(old, new, origin=origin, version=version))
    edits.extend(_marker_edits(before, after, origin=origin,
                                version=version))
    edits.extend(_timeline_setting_edits(before, after, origin=origin,
                                         version=version))
    return edits


def _timeline_setting_edits(before: Mapping, after: Mapping, *,
                             origin: str, version: str) -> list:
    """The timeline's own frame, when it moved.

    The timeline's start frame is the coordinate frame every row is
    placed in, so a change to it moves every row. It is recorded
    without a row, and the coordinate rule reads it as a shift on every
    row.
    """
    old = dict(before or {})
    new = dict(after or {})
    edits = []
    # The END frame is not a coordinate: a timeline that grew at its end
    # did not move what is already on it, and the clip differences
    # already say what was added.
    for key in ("start_frame",):
        if _same_value(old.get(key), new.get(key)):
            continue
        edits.append(Edit(
            edit_id=f"timeline.{key}", origin=origin, version=version,
            kind=KIND_PLACEMENT, target="timeline", row="",
            timeline_region=(0, 0)))
    return edits


def edits_from_operations(operations: Sequence, base: dict, *, origin: str,
                          version: str) -> list:
    """The changes a prepared patch's operations claim.

    The operations are the edit algebra's ids (`edit_algebra`), read
    against the base snapshot they were planned on, so a change that
    names an item the base does not hold is refused rather than
    described.
    """
    clips = _clips_by_id(base)
    edits = []
    for operation in operations or ():
        edit = _edit_for_operation(operation, clips, origin=origin,
                                    version=version)
        if edit is not None:
            edits.append(edit)
    return edits


def unreadable_operations(operations: Sequence, base: Mapping | None = None) -> list:
    """The operations this module cannot describe as a change.

    An operation it cannot read is a change it cannot compare, and an
    unread change is never read as agreement: a caller that cannot
    describe every operation must treat the comparison as unproven.

    `base` is the state the operations were planned on. An operation
    that names an item the base does not hold is unreadable too: there
    is no item to say what it claims.
    """
    unreadable = []
    for operation in operations or ():
        if _edit_for_operation(operation, {}, origin="", version="") is None:
            unreadable.append(str(operation.get("op") or ""))
            continue
        unique_id = str(operation.get("unique_id") or "")
        if (base is not None and unique_id
                and unique_id not in _clips_by_id(base)):
            unreadable.append(str(operation.get("op") or ""))
    return unreadable


def edits_from_carried_edits(records: Sequence, *, origin: str,
                             version: str) -> list:
    """The changes the carried-edit ledger already states.

    The ledger (`editor_edit_carry`) speaks in source terms already: the
    row, the source identity, the source range, the field it writes.
    This reads those entries into the same shape, so a carried edit and
    a prepared patch can be compared on one vocabulary.
    """
    edits = []
    for record in records or ():
        source = (record.get("source_in_frame"), record.get("source_out_frame"))
        source = (int(source[0]), int(source[1])) if _both_int(source) else None
        region = (record.get("record_in_when_detected"),
                  record.get("record_out_when_detected"))
        region = (int(region[0]), int(region[1])) if _both_int(region) else None
        kind = _carried_kind(str(record.get("kind") or ""))
        field = str(record.get("field") or "")
        properties = ()
        if field and kind == KIND_PROPERTY:
            properties = ((field, (record.get("after") or {}).get(field)),)
        edits.append(Edit(
            edit_id=str(record.get("id") or ""),
            origin=origin,
            version=version,
            kind=kind,
            target=str(record.get("row") or "") + "|" + str(
                record.get("source_identity") or ""),
            row=str(record.get("row") or ""),
            source_identity=str(record.get("source_identity") or ""),
            source=source,
            timeline_region=region,
            properties=properties))
    return edits


def _carried_kind(kind: str) -> str:
    if kind == "cut":
        return KIND_DELETE
    if kind in ("trim", "extend"):
        return KIND_SOURCE_WINDOW
    if kind == "move":
        return KIND_PLACEMENT
    if kind == "enabled":
        return KIND_ENABLED
    if kind == "transform":
        return KIND_PROPERTY
    if kind == "split":
        return KIND_SOURCE_WINDOW
    return KIND_PROPERTY


# ── The snapshot readers ───────────────────────────────────────────────


def _clips_by_id(snapshot: Mapping) -> dict:
    """Every clip of a snapshot, keyed by its Resolve unique id."""
    clips = {}
    for track in (snapshot or {}).get("tracks") or ():
        for clip in track.get("clips") or ():
            unique_id = str(clip.get("unique_id") or "")
            if not unique_id:
                continue
            clips[unique_id] = clip
    return clips


def _row_of(clip: Mapping) -> str:
    track_type = str(clip.get("track_type") or "")
    track_name = str(clip.get("track_name") or "")
    if not track_type and not track_name:
        return ""
    return f"{track_type}:{track_name}"


def _source_of(clip: Mapping) -> tuple | None:
    start = clip.get("source_in_frame")
    end = clip.get("source_out_frame")
    if not _both_int((start, end)):
        return None
    return (int(start), int(end))


def _region_of(clip: Mapping) -> tuple | None:
    start = clip.get("record_in")
    end = clip.get("record_out")
    if not _both_int((start, end)):
        return None
    return (int(start), int(end))


def _insert_edit(clip: Mapping, *, origin: str, version: str) -> Edit:
    return Edit(
        edit_id=f"insert:{clip.get('unique_id')}",
        origin=origin, version=version, kind=KIND_INSERT,
        target=str(clip.get("unique_id") or ""), row=_row_of(clip),
        source_identity=str(clip.get("source_file") or ""),
        source=_source_of(clip), timeline_region=_region_of(clip))


def _delete_edit(clip: Mapping, *, origin: str, version: str) -> Edit:
    return Edit(
        edit_id=f"delete:{clip.get('unique_id')}",
        origin=origin, version=version, kind=KIND_DELETE,
        target=str(clip.get("unique_id") or ""), row=_row_of(clip),
        source_identity=str(clip.get("source_file") or ""),
        source=_source_of(clip), timeline_region=_region_of(clip))


def _changed_edits(old: Mapping, new: Mapping, *, origin: str,
                   version: str) -> list:
    """The changes between one clip's two reads."""
    edits = []
    unique_id = str(new.get("unique_id") or old.get("unique_id") or "")
    row = _row_of(new) or _row_of(old)
    source_identity = str(new.get("source_file") or old.get("source_file") or "")
    old_source = _source_of(old)
    new_source = _source_of(new)
    old_region = _region_of(old)
    new_region = _region_of(new)
    if old_source != new_source and (old_source or new_source):
        edits.append(Edit(
            edit_id=f"source:{unique_id}", origin=origin, version=version,
            kind=KIND_SOURCE_WINDOW, target=unique_id, row=row,
            source_identity=source_identity, source=new_source,
            timeline_region=new_region))
    elif old_region != new_region and (old_region or new_region):
        edits.append(Edit(
            edit_id=f"placement:{unique_id}", origin=origin, version=version,
            kind=KIND_PLACEMENT, target=unique_id, row=row,
            source_identity=source_identity, source=new_source,
            timeline_region=new_region))
    if old.get("enabled") != new.get("enabled"):
        edits.append(Edit(
            edit_id=f"enabled:{unique_id}", origin=origin, version=version,
            kind=KIND_ENABLED, target=unique_id, row=row,
            source_identity=source_identity, source=new_source,
            timeline_region=new_region,
            properties=(("enabled", new.get("enabled")),)))
    for name in _CLIP_PROPERTY_FIELDS:
        old_value = old.get(name)
        new_value = new.get(name)
        if _same_value(old_value, new_value):
            continue
        if isinstance(old_value, Mapping) and isinstance(new_value, Mapping):
            for key in sorted(set(old_value) | set(new_value)):
                if _same_value(old_value.get(key), new_value.get(key)):
                    continue
                edits.append(Edit(
                    edit_id=f"{name}.{key}:{unique_id}", origin=origin,
                    version=version, kind=KIND_PROPERTY, target=unique_id,
                    row=row, source_identity=source_identity,
                    source=new_source, timeline_region=new_region,
                    properties=((f"{name}.{key}", new_value.get(key)),)))
            continue
        edits.append(Edit(
            edit_id=f"{name}:{unique_id}", origin=origin, version=version,
            kind=KIND_PROPERTY, target=unique_id, row=row,
            source_identity=source_identity, source=new_source,
            timeline_region=new_region,
            properties=((name, new_value),)))
    return edits


def _marker_edits(before: Mapping, after: Mapping, *, origin: str,
                  version: str) -> list:
    """The timeline-level markers that appeared or disappeared."""
    old_markers = _timeline_markers(before)
    new_markers = _timeline_markers(after)
    edits = []
    for key in sorted(set(old_markers) | set(new_markers)):
        if key in old_markers and key in new_markers:
            continue
        edits.append(Edit(
            edit_id=f"marker:{key}", origin=origin, version=version,
            kind=KIND_MARKER, target="timeline", row="",
            properties=((f"marker@{key}", key in new_markers),)))
    return edits


def _timeline_markers(snapshot: Mapping) -> dict:
    markers = (snapshot or {}).get("markers") or {}
    records = markers.get("timeline")
    if records is None:
        records = markers.get("notes") or ()
    out = {}
    for marker in records:
        out[str(marker.get("frame"))] = marker
    return out


# ── The operation reader ───────────────────────────────────────────────


def _edit_for_operation(operation: Mapping, clips: Mapping, *, origin: str,
                        version: str) -> Edit | None:
    """One operation, as a change."""
    op = str(operation.get("op") or "")
    unique_id = str(operation.get("unique_id") or "")
    clip = clips.get(unique_id) or {}
    row = _row_of(clip)
    source = _source_of(clip)
    region = _region_of(clip)
    if op in ("clip.delete", "clip.remove"):
        return Edit(edit_id=f"{op}:{unique_id}", origin=origin,
                    version=version, kind=KIND_DELETE, target=unique_id,
                    row=row, source_identity=str(clip.get("source_file") or ""),
                    source=source, timeline_region=region)
    if op in ("clip.insert", "broll.insert"):
        placed = (operation.get("record_in"), operation.get("record_out"))
        placed = (int(placed[0]), int(placed[1])) if _both_int(placed) else region
        return Edit(edit_id=f"{op}:{unique_id}", origin=origin,
                    version=version, kind=KIND_INSERT, target=unique_id,
                    row=row, source_identity=str(clip.get("source_file") or ""),
                    source=source, timeline_region=placed)
    if op == "clip.trim":
        window = (operation.get("in_frame"), operation.get("out_frame"))
        window = (int(window[0]), int(window[1])) if _both_int(window) else source
        return Edit(edit_id=f"{op}:{unique_id}", origin=origin,
                    version=version, kind=KIND_SOURCE_WINDOW,
                    target=unique_id, row=row,
                    source_identity=str(clip.get("source_file") or ""),
                    source=window, timeline_region=region)
    if op in ("clip.move", "clip.slide"):
        to_frame = operation.get("to_frame")
        if isinstance(to_frame, int) and region is not None:
            moved = (int(to_frame), int(to_frame) + (region[1] - region[0]))
        else:
            moved = region
        return Edit(edit_id=f"{op}:{unique_id}", origin=origin,
                    version=version, kind=KIND_PLACEMENT, target=unique_id,
                    row=row, source_identity=str(clip.get("source_file") or ""),
                    source=source, timeline_region=moved)
    if op == "clip.set_enabled":
        return Edit(edit_id=f"{op}:{unique_id}", origin=origin,
                    version=version, kind=KIND_ENABLED, target=unique_id,
                    row=row, source_identity=str(clip.get("source_file") or ""),
                    source=source, timeline_region=region,
                    properties=(("enabled", operation.get("enabled")),))
    if op == "clip.set_property":
        return Edit(edit_id=f"{op}:{unique_id}", origin=origin,
                    version=version, kind=KIND_PROPERTY, target=unique_id,
                    row=row, source_identity=str(clip.get("source_file") or ""),
                    source=source, timeline_region=region,
                    properties=(("transform." + str(operation.get("key") or ""),
                                 operation.get("value")),))
    if op == "clip.smart_reframe":
        keys = operation.get("keys") or ()
        return Edit(edit_id=f"{op}:{unique_id}", origin=origin,
                    version=version, kind=KIND_PROPERTY, target=unique_id,
                    row=row, source_identity=str(clip.get("source_file") or ""),
                    source=source, timeline_region=region,
                    properties=tuple(("transform." + str(key), None)
                                     for key in keys))
    if op == "clip.replace":
        return Edit(edit_id=f"{op}:{unique_id}", origin=origin,
                    version=version, kind=KIND_SOURCE_WINDOW,
                    target=unique_id, row=row,
                    source_identity=str(operation.get("source_file") or ""),
                    source=source, timeline_region=region)
    if op in ("marker.add", "marker.delete"):
        frame = operation.get("frame")
        return Edit(edit_id=f"{op}:{frame}", origin=origin, version=version,
                    kind=KIND_MARKER, target="timeline", row="",
                    properties=((f"marker@{frame}",
                                 op == "marker.add"),))
    if op in ("clip_marker.add", "clip_marker.delete"):
        frame = operation.get("frame")
        return Edit(edit_id=f"{op}:{unique_id}:{frame}", origin=origin,
                    version=version, kind=KIND_MARKER, target=unique_id,
                    row=row, timeline_region=region,
                    properties=((f"marker@{frame}",
                                 op == "clip_marker.add"),))
    return None


# ── Helpers ────────────────────────────────────────────────────────────


def _same_write(left: Edit, right: Edit) -> bool:
    """Whether two changes write exactly the same thing."""
    return (left.kind == right.kind
            and left.target == right.target
            and _same_value(left.source, right.source)
            and _same_value(left.timeline_region, right.timeline_region)
            and left.property_map() == right.property_map())


def _places(edit: Edit) -> bool:
    """Whether this change puts content at a place on a row."""
    return edit.kind in PLACING_KINDS


def _overlaps(left, right) -> bool:
    """Whether two frame ranges overlap. An absent range overlaps
    nothing: a change that says nothing about where it lands cannot be
    shown to land on top of anything."""
    if not left or not right:
        return False
    return int(left[0]) < int(right[1]) and int(right[0]) < int(left[1])


def _same_span(left, right) -> bool:
    if left is None or right is None:
        return left is right
    return (int(left[0]), int(left[1])) == (int(right[0]), int(right[1]))


def _same_value(left, right) -> bool:
    """Two measured values, compared the way a measurement is."""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            return False
        return all(_same_value(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False
        return all(_same_value(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-6
    return left == right


def _both_int(pair) -> bool:
    return (isinstance(pair, (tuple, list)) and len(pair) == 2
            and all(isinstance(value, int) and not isinstance(value, bool)
                    for value in pair))


def _span(span) -> str:
    if not span:
        return "?"
    return f"{int(span[0])}..{int(span[1])}"


def _short(value) -> str:
    text = json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= 60 else text[:57] + "..."


def _edit_dict(edit: Edit) -> dict:
    return {
        "edit_id": edit.edit_id,
        "origin": edit.origin,
        "version": edit.version,
        "kind": edit.kind,
        "target": edit.target,
        "row": edit.row,
        "source_identity": edit.source_identity,
        "source": list(edit.source) if edit.source else None,
        "timeline_region": (list(edit.timeline_region)
                            if edit.timeline_region else None),
        "properties": {str(key): value for key, value in edit.properties},
    }


__all__ = [
    "AXES",
    "AXIS_COORDINATES",
    "AXIS_PROPERTY",
    "AXIS_SOURCE",
    "AXIS_TARGET",
    "AXIS_TIMELINE",
    "CONFLICT",
    "HUMAN_ORIGINS",
    "INDEPENDENT",
    "KINDS",
    "ORIGINS",
    "ORIGIN_CAPTAIN",
    "ORIGIN_REN",
    "UNPROVEN",
    "VERDICTS",
    "ConflictReport",
    "Edit",
    "EditSet",
    "PairVerdict",
    "SemanticConflictRefused",
    "compare",
    "detect",
    "edits_between",
    "edits_from_carried_edits",
    "edits_from_operations",
    "merge",
    "unreadable_operations",
]
