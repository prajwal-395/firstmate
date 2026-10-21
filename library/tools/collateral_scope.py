"""PLAN-006: the collateral a request may cause, modelled before the write.

An edit is a promise: the named thing changes and nothing else does
(``preservation_invariant`` judges that promise on the READ-BACK, against
the state the write left behind).  This module answers the question that
comes BEFORE the write: what ELSE could this request move, and is that
movement the request's own doing?

The punch list asks (PLAN-006) for preservation-aware edit planning:

- prefer changes that affect only the authorized media or interval;
- explicitly model which adjacent items, timing, linked audio, captions
  and effects may change;
- treat preservation of approved work as an invariant;
- distinguish necessary collateral shifts, such as a requested ripple,
  from unauthorized modifications;
- make conflicts visible to the agent before execution.

So this module computes, for one request against one pre-edit state:

    authorized - the rows and the intervals the request may touch;
    collateral - every item the request may move, each with the ASPECT it
                 moves in and the KIND of movement it is;
    conflicts  - the movements that are unauthorized, and the necessary
                 movements the request does not carry.

The vocabulary is the engine's own.  The ASPECTS are the five the punch
list names; the DOMAINS are ``edit_patch.CONFLICT_DOMAINS``; the temporal
effect of an operation is ``edit_algebra.temporal_effect`` - the engine's
own declaration of whether an operation shifts timing.  Nothing here
invents a second vocabulary for what an operation does.

Three kinds, and the difference between them is the whole point:

    REQUESTED     the request asked for this change: the operation names
                  the item, or the operation IS a ripple and the item is
                  on the row the ripple moves;
    NECESSARY     a declared rule implies the change follows from a
                  requested one: a linked partner moves with its partner,
                  a caption card follows the picture it covers, an effect
                  rides along with the item it is on;
    UNAUTHORIZED  nothing in the request implies it: the change would
                  touch work the request does not name.

A NECESSARY change the request does not CARRY is a conflict too.  The
caption card left covering a picture that is gone is not an unauthorized
modification - it is an unfinished one, and it is visible here rather
than after the write.

WHAT THIS MODULE DOES NOT OWN:

- the read-back judgement that proves the REQUESTED edit happened
  (``preservation_invariant``, ``edit_algebra.verifies``);
- the authorization scope itself, which is the patch's own declaration
  (``preservation_invariant.scope_for``).  This module reads that scope
  and asks what it does not cover;
- the write order and the compound transaction's other pre-write rules
  (``compound_transaction``), which call this module and refuse on what
  it reports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from library.tools import edit_algebra

#: The aspects a collateral change falls in - the five the punch list
#: names (PLAN-006).  An item that moves because the picture under it
#: moved is an ADJACENT ITEM that changed its TIMING; the two are read
#: together, and the aspect says which reading applies.
ASPECTS = ("adjacent_items", "timing", "linked_audio", "captions", "effects")

#: The three kinds of movement, and the difference between them.
REQUESTED = "requested"
NECESSARY = "necessary"
UNAUTHORIZED = "unauthorized"
KINDS = (REQUESTED, NECESSARY, UNAUTHORIZED)

#: The operations that change an item's own record span, and so move
#: whatever is linked to it.
_TIMING_OPS = frozenset({
    "clip.trim", "clip.slide", "clip.ripple", "clip.remove", "clip.delete",
    "clip.insert", "clip.split", "clip.join", "retime.segments",
    "span_retime", "reel.opening",
})

#: The operations that change the LINK GROUP's state.  A group is a set
#: of items that share one audio, so the state is the group's, not the
#: item's: linking two items writes both, and unlinking one breaks the
#: group on both sides.
_LINK_STATE_OPS = frozenset({"clip.link", "clip.unlink"})

#: The operations that RE-PLACE an item, and so must carry the state it
#: held (the grade, the comp, the transform) onto the new item.
_REPLACING_OPS = frozenset({"clip.replace", "caption.style"})

#: The operations that change what a LINKED PARTNER records about its
#: partner.  The partner's record carries the group's members' own state
#: (their place, their source window, their enablement, their fades), so
#: a change to any of those is visible on the other side of the group.
_GROUP_RECORD_OPS = _TIMING_OPS | frozenset({
    "clip.set_enabled", "clip.roll", "clip.slip", "clip.replace",
})

#: How far a shift travels, and from which point.
#:
#: The engine declares that an operation is a ripple
#: (``edit_algebra.temporal_effect``), but that declaration does NOT say
#: how far the shift travels, so the reach is declared per operation
#: here rather than guessed from the name.
#:
#: ``clip.remove`` is Resolve's NATIVE ripple: it moves every row's
#: tail.  The engine refuses the unscoped use of that primitive
#: (``edit_algebra._remove_clip_with_measured_retry``: "Resolve's native
#: ripple is timeline-wide"), and this module says WHICH rows it would
#: move, item by item.
#:
#: ``clip.ripple`` is the engine's own scoped shift: the row it names,
#: from the anchor's start.  ``clip.slide`` moves its anchor and takes
#: the tail with it.
#:
#: A ripple op that is not in this table shifts NOTHING beyond its own
#: target: an operation whose temporal effect the engine declares but
#: whose reach nobody measured is not allowed to claim collateral.
_REACH = {
    "clip.remove": ("timeline", "end"),
    "clip.ripple": ("row", "start"),
    "clip.slide": ("row", "end"),
}

#: The fields whose change is a change of TIMING rather than of content.
_TIMING_FIELDS = frozenset({
    "record_in", "record_out", "duration", "source_in_frame",
    "source_out_frame", "left_offset", "right_offset",
})


@dataclass(frozen=True)
class Effect:
    """What one operation does to the timeline beyond its own target.

    Read from the engine's own declarations: the DOMAIN is the
    operation's conflict domain, the TEMPORAL effect is whether it
    shifts timing, and the REACH is how far the shift travels.
    """

    op: str
    domain: str
    temporal: str
    reach: str
    point: str = "end"
    timing: bool = False
    link_state: bool = False
    re_places: bool = False
    group_record: bool = False
    targets: tuple[str, ...] = ()
    """The items the operation names, by every key it names them with."""

    @property
    def ripples(self) -> bool:
        return self.temporal == "ripple"


def effect_of(operation: Mapping[str, Any]) -> Effect:
    """The declared effect of one operation.

    An operation the algebra does not know has NO domain, so nothing can
    be authorized for it - it is reported as an unbounded operation
    rather than silently allowed to touch the timeline.
    """
    op_id = str(operation.get("op") or "")
    spec = edit_algebra.ALGEBRA.get(op_id)
    domain = spec.domain if spec is not None else ""
    try:
        temporal = edit_algebra.temporal_effect(op_id)
    except KeyError:
        temporal = "local"
    reach, point = _REACH.get(op_id, ("target", "end"))
    return Effect(op=op_id, domain=domain, temporal=temporal, reach=reach,
                  point=point, timing=op_id in _TIMING_OPS,
                  link_state=op_id in _LINK_STATE_OPS,
                  re_places=op_id in _REPLACING_OPS,
                  group_record=op_id in _GROUP_RECORD_OPS,
                  targets=named_targets(operation))


def named_targets(operation: Mapping[str, Any]) -> tuple[str, ...]:
    """Every item an operation names, by its own keys.

    The authorization scope reads ONE key (``unique_id``), and an
    operation that names more than one item - ``clip.link`` names the
    partner it links to, ``clip.join`` names the item it joins - wrote a
    request that says "these two" while the scope read "this one".  The
    partner was then unauthorized by a request that named it, and the
    read-back called the change collateral.

    Every key an operation uses to name an item is read here, so the
    scope and the request agree on what the request named.
    """
    named: list[str] = []
    for key in ("unique_id", "client_unique_id", "with_unique_ids",
                "other_unique_id"):
        value = operation.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for entry in value:
                text = str(entry or "")
                if text and text not in named:
                    named.append(text)
            continue
        text = str(value or "")
        if text and text not in named:
            named.append(text)
    return tuple(named)


def _target(target: str) -> str:
    """A target in the delta's vocabulary."""
    return f"clip:{target}"


@dataclass(frozen=True)
class CollateralItem:
    """One movement the request may cause.

    ``kind`` says WHO asked for it; ``carried`` says whether the request
    carries it; ``own`` says whether the movement is the operation's own
    named target or a CONSEQUENCE of it.  A movement that is necessary and
    not carried is a conflict: the request implies the change and does
    not make it.
    """

    target: str
    aspect: str
    domain: str
    kind: str
    operation: str
    reason: str
    carried: bool = True
    own: bool = True

    @property
    def conflict(self) -> bool:
        """Whether this movement is a conflict the agent must see."""
        return self.kind == UNAUTHORIZED or (
            self.kind == NECESSARY and not self.carried)

    def as_dict(self) -> dict[str, Any]:
        return {"target": self.target, "aspect": self.aspect,
                "domain": self.domain, "kind": self.kind,
                "operation": self.operation, "reason": self.reason,
                "carried": self.carried, "own": self.own,
                "conflict": self.conflict}


@dataclass(frozen=True)
class CollateralScope:
    """What one request may move, and what of that is a conflict."""

    items: tuple[CollateralItem, ...] = ()
    rows: tuple[tuple[str, int], ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    requirements: tuple[Mapping[str, Any], ...] = ()
    unbounded_ops: tuple[str, ...] = ()
    status: str = "modelled"

    @property
    def conflicts(self) -> tuple[CollateralItem, ...]:
        """The movements that are unauthorized, or necessary and uncarried."""
        return tuple(item for item in self.items if item.conflict)

    @property
    def collateral(self) -> tuple[CollateralItem, ...]:
        """Every movement that is not the request's own target."""
        return tuple(item for item in self.items if not item.own)

    @property
    def unauthorized(self) -> tuple[CollateralItem, ...]:
        return tuple(item for item in self.items
                     if item.kind == UNAUTHORIZED)

    def by_aspect(self, aspect: str) -> tuple[CollateralItem, ...]:
        return tuple(item for item in self.items if item.aspect == aspect)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rows": [list(row) for row in self.rows],
            "spans": [list(span) for span in self.spans],
            "unbounded_ops": list(self.unbounded_ops),
            "items": [item.as_dict() for item in self.items],
            "conflicts": [item.as_dict() for item in self.conflicts],
            "unauthorized": [item.as_dict() for item in self.unauthorized],
        }


# ── the state the request is planned against ─────────────────────────


def _items(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    items: dict[str, Mapping[str, Any]] = {}
    for track in snapshot.get("tracks") or ():
        for clip in track.get("clips") or ():
            unique_id = str(clip.get("unique_id") or "")
            if unique_id:
                items[unique_id] = clip
    return items


def _rows(snapshot: Mapping[str, Any]) -> dict[tuple[str, int], Mapping]:
    rows: dict[tuple[str, int], Mapping] = {}
    for track in snapshot.get("tracks") or ():
        rows[(str(track.get("type") or ""),
              int(track.get("index") or 0))] = track
    return rows


def _row_of(rows: Mapping[tuple[str, int], Mapping],
            unique_id: str) -> tuple[str, int] | None:
    for key, track in rows.items():
        for clip in track.get("clips") or ():
            if str(clip.get("unique_id") or "") == unique_id:
                return key
    return None


def _span_of(clip: Mapping[str, Any]) -> tuple[int, int] | None:
    try:
        return (int(clip["record_in"]), int(clip["record_out"]))
    except (KeyError, TypeError, ValueError):
        return None


def _linked_of(clip: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(str(uid) for uid in
                        (clip.get("linked_unique_ids") or ()) if uid))


def _aspect_of(domain: str, field_path: str = "") -> str:
    """The aspect a domain's change falls in."""
    if domain == "captions":
        return "captions"
    if domain in ("fusion", "color"):
        return "effects"
    if domain == "audio_mix":
        return "linked_audio"
    if domain == "markers":
        return "timing"
    head = field_path.split(".", 1)[0]
    if head in ("linked_unique_ids", "linked_clips"):
        return "linked_audio"
    if head in _TIMING_FIELDS:
        return "timing"
    return "adjacent_items"


def caption_rows(snapshot: Mapping[str, Any],
                 declared: Sequence[tuple[str, int]] = ()
                 ) -> tuple[tuple[str, int], ...]:
    """The rows that carry caption cards.

    The declared rows win.  The fallback is the row NAME: the engine's
    caption row is named for what it carries in the layouts that name
    their rows, and a row whose name says nothing is NOT guessed to be
    a caption row - a guessed caption row would put a caption conflict
    on every request that touches a picture.
    """
    if declared:
        return tuple((str(kind), int(index)) for kind, index in declared)
    rows = []
    for track in snapshot.get("tracks") or ():
        name = str(track.get("name") or "").lower()
        if "caption" in name or "subtitle" in name:
            rows.append((str(track.get("type") or ""),
                         int(track.get("index") or 0)))
    return tuple(rows)


# ── the model ─────────────────────────────────────────────────────────


def allowed_collateral(*, operations: Sequence[Mapping[str, Any]],
                       before: Mapping[str, Any],
                       requirements: Sequence[Mapping[str, Any]] = (),
                       affected_spans: Sequence[Sequence[int]] = (),
                       caption_rows_declared: Sequence[tuple[str, int]] = ()
                       ) -> CollateralScope:
    """The collateral one request may cause, before anything is written.

    ``before`` is the pre-edit state the request was planned against;
    ``requirements`` are the requester's own preservation requirements
    (the approved work), and ``affected_spans`` are the intervals the
    request declares it may touch.

    An empty ``before`` means the state could not be read: the scope
    reports ``undetermined`` rather than claiming the request is clean,
    because a model that cannot see the timeline cannot say what the
    request would move.
    """
    if not isinstance(before, Mapping) or not before.get("tracks"):
        return CollateralScope(status="undetermined",
                               requirements=tuple(requirements or ()))

    items = _items(before)
    rows = _rows(before)
    spans = tuple((int(span[0]), int(span[1]))
                  for span in (affected_spans or ()))
    caption_row_set = set(caption_rows(before, caption_rows_declared))

    # Every item the request names, over EVERY operation: a request that
    # names a partner in its second operation has named it for its
    # first operation too.
    named: set[str] = set()
    request_rows: list[tuple[str, int]] = []
    unbounded: list[str] = []
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        effect = effect_of(operation)
        if not effect.domain:
            unbounded.append(effect.op or "<unnamed>")
            continue
        named.update(named_targets(operation))
        for row in _named_rows(operation):
            if row not in request_rows:
                request_rows.append(row)
        for unique_id in named_targets(operation):
            row = _row_of(rows, unique_id)
            if row is not None and row not in request_rows:
                request_rows.append(row)

    movements: list[CollateralItem] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(item: CollateralItem) -> None:
        key = (item.target, item.aspect, item.kind, item.operation)
        if key in seen:
            return
        seen.add(key)
        movements.append(item)

    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        effect = effect_of(operation)
        if not effect.domain:
            continue
        for unique_id in named_targets(operation):
            clip = items.get(unique_id)
            row = _row_of(rows, unique_id)
            if row is not None and row not in request_rows:
                request_rows.append(row)
            if clip is None:
                # The request names an item the state does not hold: the
                # plan is against another generation, and nothing here
                # can say what it would move.
                continue
            add(CollateralItem(
                target=_target(unique_id),
                aspect=_aspect_of(effect.domain),
                domain=effect.domain,
                kind=REQUESTED,
                operation=effect.op,
                reason=(f"the request names this item and writes its "
                        f"{effect.domain}"),
                own=True,
            ))
        movements.extend(_reach_of(effect=effect, items=items, rows=rows,
                                   spans=spans,
                                   request_rows=set(request_rows)))
        movements.extend(_coupling_of(effect=effect, items=items,
                                      named=named))
        movements.extend(_caption_carry(effect=effect, items=items,
                                        named=named,
                                        caption_rows=caption_row_set))
        movements.extend(_effect_carry(effect=effect, items=items))

    return CollateralScope(
        items=tuple(movements),
        rows=tuple(request_rows),
        spans=spans,
        requirements=tuple(requirements or ()),
        unbounded_ops=tuple(dict.fromkeys(unbounded)),
    )


def _named_rows(operation: Mapping[str, Any]) -> list[tuple[str, int]]:
    """The rows an operation names in its own right."""
    track_type = operation.get("track_type")
    track_index = operation.get("track_index")
    if track_type is None or track_index is None:
        return []
    return [(str(track_type), int(track_index))]


def _reach_of(*, effect: Effect, items: Mapping[str, Mapping[str, Any]],
              rows: Mapping[tuple[str, int], Mapping],
              spans: Sequence[tuple[int, int]],
              request_rows: set[tuple[str, int]]) -> list[CollateralItem]:
    """The items a ripple reaches, and what kind of movement that is.

    A ripple on a row the request names moves that row's tail: the
    request asked for it, so those movements are REQUESTED.  Resolve's
    NATIVE ripple moves every row's tail, and the rows the request did
    not name are UNAUTHORIZED - that is the hazard the engine refuses
    the primitive over, named item by item.

    The rows that count as named are the request's OWN rows: the rows of
    the items it names, and the rows it names outright.  A request that
    names a clip on the speech row has asked about that row, so a ripple
    that moves the speech is not unrequested - it is still visible, but
    it is not an unauthorized modification.
    """
    if effect.reach == "target":
        return []
    out: list[CollateralItem] = []
    for unique_id in effect.targets:
        clip = items.get(unique_id)
        if clip is None:
            continue
        span = _span_of(clip)
        if span is None:
            continue
        point = span[0] if effect.point == "start" else span[1]
        anchor_row = _row_of(rows, unique_id)
        for (kind, index), track in rows.items():
            if effect.reach == "row" and (kind, index) != anchor_row:
                continue
            for other in track.get("clips") or ():
                other_id = str(other.get("unique_id") or "")
                if other_id == unique_id:
                    continue
                other_span = _span_of(other)
                if other_span is None or other_span[0] < point:
                    continue
                on_named_row = (kind, index) in request_rows
                covered = _within(other_span, spans)
                if on_named_row and covered:
                    kind_here = REQUESTED
                    reason = ("the ripple the request asks for moves this "
                              "item on the row it names")
                elif on_named_row:
                    kind_here = UNAUTHORIZED
                    reason = ("this item moves, and the interval the request "
                              "declared does not cover it")
                else:
                    kind_here = UNAUTHORIZED
                    reason = ("Resolve's native ripple moves this row too, "
                              "and the request names only another row")
                out.append(CollateralItem(
                    target=_target(other_id),
                    aspect="timing",
                    domain="timeline_structure",
                    kind=kind_here,
                    operation=effect.op,
                    reason=reason,
                    carried=True,
                    own=False,
                ))
    return out


def _within(span: tuple[int, int],
            spans: Sequence[tuple[int, int]]) -> bool:
    """Whether a span lies inside one of the declared intervals.

    A request that declares NO interval cannot be caught out by the
    interval rule: there is nothing to be outside of.  The rule then
    applies to the rows alone.
    """
    if not spans:
        return True
    return any(span[0] >= start and span[1] <= end
               for start, end in spans)


def _coupling_of(*, effect: Effect, items: Mapping[str, Mapping[str, Any]],
                 named: set[str]) -> list[CollateralItem]:
    """The link group's movements: the partner that follows its partner.

    A linked pair is ONE audio, shown twice.  When the request changes
    one of them, the other's state follows - that is what makes the
    change NECESSARY rather than unauthorized.  The group is named in
    the operation's own keys where the operation names it (``clip.link``)
    and is not named where it does not (``clip.unlink``), and the
    difference is the difference between a REQUESTED movement and a
    NECESSARY one.
    """
    out: list[CollateralItem] = []
    for unique_id in effect.targets:
        clip = items.get(unique_id)
        if clip is None:
            continue
        partners = _linked_of(clip)
        if not partners:
            continue
        if effect.link_state:
            reason = ("the request changes this link group, and the "
                      "group's state is the group's")
        elif effect.timing:
            reason = ("this item's timing changes, and a linked partner "
                      "shares its audio, so the partner moves with it")
        elif effect.group_record:
            reason = ("this item's state changes, and its linked partner "
                      "carries a record of it")
        else:
            continue
        for partner in partners:
            requested = partner in named
            out.append(CollateralItem(
                target=_target(partner),
                aspect="linked_audio",
                domain="timeline_structure",
                kind=REQUESTED if requested else NECESSARY,
                operation=effect.op,
                reason=reason,
                carried=True,
                own=False,
            ))
    return out


def _caption_carry(*, effect: Effect, items: Mapping[str, Mapping[str, Any]],
                   named: set[str],
                   caption_rows: set[tuple[str, int]]) -> list[CollateralItem]:
    """The caption cards that follow the picture under them.

    A caption card does not move when the picture under it is removed -
    the card stays where it is and now covers something else.  That is
    not an unauthorized modification: it is a consequence the request
    implies and does not make, so it is NECESSARY and UNCARRIED, and it
    is a conflict the agent sees before the write rather than a caption
    that is wrong afterwards.
    """
    out: list[CollateralItem] = []
    if not effect.timing:
        return out
    touched: list[tuple[int, int]] = []
    for unique_id in effect.targets:
        clip = items.get(unique_id)
        if clip is None:
            continue
        span = _span_of(clip)
        if span is not None:
            touched.append(span)
    if not touched:
        return out
    for unique_id, clip in items.items():
        if not _on_row(clip, caption_rows):
            continue
        span = _span_of(clip)
        if span is None:
            continue
        if not any(span[0] < end and start < span[1]
                   for start, end in touched):
            continue
        out.append(CollateralItem(
            target=_target(unique_id),
            aspect="captions",
            domain="captions",
            kind=NECESSARY,
            operation=effect.op,
            reason=("this caption card covers the interval the request "
                    "changes, and a caption card does not move with the "
                    "picture"),
            carried=unique_id in named,
            own=False,
        ))
    return out


def _on_row(clip: Mapping[str, Any],
            rows: set[tuple[str, int]]) -> bool:
    """Whether an item sits on one of the named rows."""
    track_type = clip.get("track_type")
    track_index = clip.get("track_index")
    if track_type is None or track_index is None:
        return False
    return (str(track_type), int(track_index)) in rows


def _effect_carry(*, effect: Effect,
                   items: Mapping[str, Mapping[str, Any]]
                   ) -> list[CollateralItem]:
    """The effect an item carries when the item is re-placed.

    A RE-PLACEMENT puts a new item where the old one was, and the grade
    and the comp the old one carried have to ride along.  The engine's
    scoped replacement carries them, so the movement is NECESSARY and
    CARRIED - but it is named here, because an effect that was lost
    silently is the one thing nobody looks for.
    """
    if not effect.re_places:
        return []
    out: list[CollateralItem] = []
    for unique_id in effect.targets:
        clip = items.get(unique_id)
        if clip is None:
            continue
        fusion = clip.get("fusion") or {}
        color = clip.get("color") or {}
        comps = int(fusion.get("comp_count") or 0) if isinstance(
            fusion, Mapping) else 0
        cdl = color.get("cdl") if isinstance(color, Mapping) else None
        if not comps and not cdl:
            continue
        out.append(CollateralItem(
            target=_target(unique_id),
            aspect="effects",
            domain="fusion" if comps else "color",
            kind=NECESSARY,
            operation=effect.op,
            reason=("this item carries an effect, and a re-placement has "
                    "to carry it onto the new item"),
            carried=True,
            own=False,
        ))
    return out


# ── the conflict with the approved work ───────────────────────────────


def requirement_conflicts(scope: CollateralScope, *,
                          before: Mapping[str, Any],
                          ) -> list[dict[str, Any]]:
    """Where the request's own consequences contradict the approved work.

    The requester may name work that must survive the edit
    (``preservation_invariant.PRESERVATION_REQUIREMENTS``).  The
    compound feasibility check asks whether the request WRITES to that
    work; this asks the other half - whether the request's CONSEQUENCES
    reach it.  A ripple that would move an approved clip is invisible to
    the first question and fatal to the second.
    """
    items = _items(before or {})
    rows = _rows(before or {})
    out: list[dict[str, Any]] = []
    for requirement in scope.requirements or ():
        name = str(requirement.get("kind") or requirement.get("name") or "")
        if name == "clip_unchanged":
            unique_id = str(requirement.get("unique_id") or "")
            for item in scope.items:
                if item.own or item.target != _target(unique_id):
                    continue
                out.append({"requirement": name, "status": "infeasible",
                            "detail": (f"the request's own consequences "
                                       f"reach clip {unique_id!r}: "
                                       f"{item.reason}")})
        elif name == "row_unchanged":
            track_type = str(requirement.get("track_type") or "").lower()
            track_index = int(requirement.get("track_index") or 0)
            for item in scope.items:
                if item.own:
                    continue
                row = _row_of(rows, item.target.split(":", 1)[-1])
                if row != (track_type, track_index):
                    continue
                out.append({"requirement": name, "status": "infeasible",
                            "detail": (f"the request's own consequences "
                                       f"reach row {track_type}"
                                       f"{track_index}: {item.reason}")})
        elif name == "marker_preserved":
            frame = int(requirement.get("frame") or 0)
            for item in scope.items:
                if item.own:
                    continue
                clip = items.get(item.target.split(":", 1)[-1])
                span = _span_of(clip) if clip is not None else None
                if span is not None and span[0] <= frame < span[1]:
                    out.append({"requirement": name, "status": "infeasible",
                                "detail": (f"the request's own consequences "
                                           f"reach the marker at frame "
                                           f"{frame}: {item.reason}")})
    return out


# ── the preference ────────────────────────────────────────────────────


def prefer_least_collateral(variants: Sequence[Sequence[Mapping[str, Any]]],
                             *, before: Mapping[str, Any],
                             requirements: Sequence[Mapping[str, Any]] = (),
                             affected_spans: Sequence[Sequence[int]] = ()
                             ) -> tuple[int, CollateralScope]:
    """The variant that touches the least, and the scope that says why.

    "Prefer changes that affect only the authorized media or interval"
    (PLAN-006) is a rule about WHICH of several ways to do the same
    thing the engine should pick.  The order is the order the punch list
    gives: an unauthorized movement is worse than an uncarried one, an
    uncarried one is worse than a carried one, and a request that moves
    nothing at all is better than one that moves something.

    The first variant wins a tie, so the requester's order is respected
    when two variants cost the same.
    """
    best_index = -1
    best_scope: CollateralScope | None = None
    best_key: tuple[int, ...] | None = None
    for index, variant in enumerate(variants or ()):
        scope = allowed_collateral(operations=variant, before=before,
                                   requirements=requirements,
                                   affected_spans=affected_spans)
        unauthorized = len(scope.unauthorized)
        uncarried = sum(1 for item in scope.collateral if not item.carried)
        collateral = len(scope.collateral)
        key = (unauthorized, uncarried, collateral)
        if best_key is None or key < best_key:
            best_key = key
            best_index = index
            best_scope = scope
    if best_scope is None:
        return (-1, CollateralScope(status="undetermined"))
    return (best_index, best_scope)
