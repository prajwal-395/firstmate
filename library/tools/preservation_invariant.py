"""Preservation as an execution invariant.

An edit is a promise: the named thing changes and nothing else does. This
module owns that promise as ONE deterministic capability, so every write
path asks the same question of the same evidence and gets the same answer:

    scope    - what the edit is authorized to touch
    before   - the state it was authorized against
    expected - the semantic delta that authorization permits
    after    - the state the write left behind
    actual   - the semantic delta that actually happened
    verdict  - preserved, violated, or unprovable

The vocabulary is the engine's own, not a second one. The authorized
domains are `edit_patch.CONFLICT_DOMAINS` - an operation's domain is
fixed by its type in `edit_algebra.ALGEBRA`, and a patch that does not
declare the domain of an operation it carries is already refused before
Resolve is touched. The observed changes are classified into those same
domains, so a grade that moved, a mix that moved and a caption that moved
read as what they are.

The unit of authorization is the (target, domain) PAIR, not the field.
The engine already decides concurrency the same way: two patches conflict
when they share a conflict domain, and an operation's blast radius is
declared in spans. A field-level expectation would need a second,
guessing table of what each of the eighty-four operations writes into a
snapshot whose shape differs per operation - and a wrong guess there
reads as "unrelated change" on correct work. The pair is what the patch
already promised, so the pair is what is checked.

What this module does NOT own:

- the read-back judgement that proves the REQUESTED edit happened
  (`edit_algebra.verifies`, one pass, one snapshot);
- the unrelated-clip scan that predates it
  (`transaction_orchestration.unrelated_clip_changes`) - this module reads
  the same two snapshots and classifies what it finds by domain, and
  covers the rows that scan cannot see: a renamed row, a timeline marker,
  a timeline's own duration;
- the replacement and rebuild guards (`reel_replace_guard`,
  `editor_edit_carry`), which own preservation across a REBUILD rather
  than across one write.

A verdict is a statement about unrelated state only. Whether the edit
itself landed is the read-back's judgement, and it is judged once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from library.tools import edit_algebra
from library.tools.ren_refusal import RenRefusal

#: The domains a mutation can fall in. `edit_patch` owns the vocabulary;
#: this module only classifies observed changes into it.
CONFLICT_DOMAINS = frozenset({
    "timeline_structure", "captions", "picture_transform", "color",
    "fusion", "audio_mix", "markers",
})

#: The class a change of media identity falls in. It is NOT a conflict
#: domain: no operation declares it, because no operation is allowed to
#: re-point a clip at different media except by replacing it.
SOURCE_IDENTITY = "source_identity"

#: The keys a clip's transform carries that belong to its audio, not to
#: its picture. On an audio row every transform key is audio.
AUDIO_KEYS = frozenset({
    "Volume", "Mute", "AudioSyncOffset", "AudioFade", "Gain", "Level",
    "AudioLevel",
})

#: The snapshot keys that are read-time context, not timeline state. A
#: difference here is not a change to the timeline.
_NON_STATE_KEYS = frozenset({
    "mode", "project", "reported_fps", "timestamp", "verified_at",
    "speaker", "source_frames", "media_type", "track_name",
})

#: The operations that are about a ROW rather than about the clip that
#: happens to sit on it. Only these own the row they name.
_ROW_OPS = frozenset({
    "track.add", "track.delete", "track.rename", "track.reorder",
})

#: The operations that re-place media on their target. Only they may
#: change a target's media identity; nothing else may.
_MEDIA_REPLACING_OPS = frozenset({
    "clip.replace", "clip.insert", "broll.insert",
})

#: The field each operation declares it writes, where it declares one.
#: An operation that declares no field authorizes its DOMAIN, not a
#: guessed field list.
_DECLARED_FIELDS = {
    "clip.set_property": ("transform.{key}",),
    "clip.trim": ("record_in", "record_out", "source_in_frame",
                  "source_out_frame", "duration"),
    "clip.roll": ("record_in", "record_out", "source_in_frame",
                  "source_out_frame", "duration"),
    "clip.slip": ("source_in_frame", "source_out_frame"),
    "clip.slide": ("source_in_frame", "source_out_frame"),
    "clip.move": ("record_in", "track_type", "track_index"),
    "clip.set_enabled": ("enabled",),
    "clip.link": ("linked_unique_ids",),
    "clip.unlink": ("linked_unique_ids",),
    "clip.delete": ("removed",),
    "clip.remove": ("removed",),
    "clip.insert": ("inserted",),
    "broll.insert": ("inserted",),
    "clip.replace": ("replaced",),
    "retime": ("speed",),
    "picture.keyframe": ("keyframes",),
    "marker.add": ("markers.timeline",),
    "marker.delete": ("markers.timeline",),
    "clip_marker.add": ("markers",),
    "clip_marker.delete": ("markers",),
    "audio.gain": ("transform.Volume",),
    "audio.pan": ("transform.Pan",),
    "audio.fade": ("transform.AudioFade",),
    "grade": ("color.cdl",),
    "grade.node_set": ("color.cdl",),
    "clip_lut": ("color.luts",),
}


class PreservationUnprovable(RenRefusal):
    """The requested preservation guarantee cannot be established.

    Nothing ran, nothing is half-written. The fix names what would make
    the guarantee establishable.
    """

    USER_MESSAGE = ("I can't apply this edit safely, because I cannot prove "
                    "the rest of the timeline stays exactly as approved.")
    RECOVERY_CLASS = "blocked"


@dataclass(frozen=True)
class MutationScope:
    """What one edit is authorized to touch.

    ``targets`` are the things the operations name; ``domains`` are the
    conflict domains the patch declared; ``pairs`` are the (target,
    domain) pairs the operations authorize, which is what the observed
    delta is checked against.
    """

    domains: tuple[str, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    pairs: tuple[tuple[str, str], ...] = ()
    targets: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    unbounded_ops: tuple[str, ...] = ()
    rows: tuple[tuple[str, str], ...] = ()
    """The rows the edit owns: ``(spec, kind)`` where kind is ``exact``
    for a row the operation names by index and ``type`` for a row whose
    index only Resolve can choose."""

    created_rows: tuple[tuple[str, str], ...] = ()
    """The rows the edit may bring into existence, by placing a clip on
    a row that is not there yet."""

    def allows(self, target: str, domain: str) -> bool:
        """Whether this scope authorizes a change on one target's domain."""
        return (target, domain) in self.pairs

    def allows_row(self, target: str) -> bool:
        """Whether this scope owns a row well enough to change it."""
        return _row_allowed(target, self.rows)

    def allows_created_row(self, target: str) -> bool:
        """Whether this scope may bring a row into existence."""
        return _row_allowed(target, self.created_rows)

    def as_dict(self) -> dict:
        return {
            "domains": list(self.domains),
            "spans": [list(span) for span in self.spans],
            "pairs": [list(pair) for pair in self.pairs],
            "targets": list(self.targets),
            "operations": list(self.operations),
            "unbounded_ops": list(self.unbounded_ops),
            "rows": [list(row) for row in self.rows],
            "created_rows": [list(row) for row in self.created_rows],
        }


@dataclass(frozen=True)
class SemanticDelta:
    """One semantic difference between two timeline states.

    ``changes`` are the observed differences; ``expected`` are the
    differences the authorized scope permits, computed BEFORE the write.
    """

    changes: tuple[Mapping, ...] = ()
    expected: tuple[Mapping, ...] = ()
    expected_not_observed: tuple[Mapping, ...] = ()
    unobservable_domains: tuple[str, ...] = ()
    unprovable_reason: str = ""

    @property
    def status(self) -> str:
        """``preserved``, ``violated`` or ``unprovable``."""
        if self.unprovable_reason:
            return "unprovable"
        if self.unrelated:
            return "violated"
        return "preserved"

    @property
    def unrelated(self) -> tuple[Mapping, ...]:
        """The observed changes the scope did not authorize."""
        return tuple(row for row in self.changes if row.get("authorized") is False)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "changes": [dict(row) for row in self.changes],
            "expected": [dict(row) for row in self.expected],
            "expected_not_observed": [dict(row)
                                      for row in self.expected_not_observed],
            "unrelated_changes": [dict(row) for row in self.unrelated],
            "unobservable_domains": list(self.unobservable_domains),
            "unprovable_reason": self.unprovable_reason,
        }


def _row_family(spec: str) -> str:
    """The type-level name of a row: ``track:video``."""
    parts = spec.split(":")
    return ":".join(parts[:2]) if len(parts) > 2 else spec


def _row_spec(target: str) -> tuple[str, str]:
    """A row target's spec and kind."""
    parts = target.split(":")
    if len(parts) >= 3:
        return target, "exact"
    return target, "type"


def _row_allowed(target: str, rows: Sequence[tuple[str, str]]) -> bool:
    """Whether a row target matches one of a scope's rows."""
    spec, kind = _row_spec(target)
    for allowed, allowed_kind in rows:
        if allowed == spec:
            return True
        if allowed_kind == "type" and allowed == _row_family(spec):
            return True
        if kind == "type" and allowed == _row_family(spec):
            return True
    return False


def _row_refs(operation: Mapping) -> list[tuple[str, str]]:
    """The rows an operation names, and how precisely."""
    track_type = operation.get("track_type")
    if track_type is None:
        return []
    rows = []
    for key in ("track_index", "from_index", "to_index"):
        index = operation.get(key)
        if index is not None:
            rows.append((f"track:{track_type}:{int(index)}", "exact"))
    if not rows:
        rows.append((f"track:{track_type}", "type"))
    return rows


def _target_for(operation: Mapping) -> str:
    """The thing an operation names, in the delta's target vocabulary."""
    unique_id = operation.get("unique_id") or operation.get("client_unique_id")
    if unique_id:
        return f"clip:{unique_id}"
    track_type = operation.get("track_type")
    track_index = operation.get("track_index")
    if track_type is not None and track_index is not None:
        return f"track:{track_type}:{int(track_index)}"
    return "timeline"


def _domain_for(operation: Mapping) -> str:
    """The conflict domain an operation writes, from the algebra."""
    spec = edit_algebra.ALGEBRA.get(str(operation.get("op") or ""))
    if spec is None:
        return ""
    return spec.domain


def _declared_fields(operation: Mapping) -> tuple[str, ...]:
    """The fields an operation declares it writes, where it declares one."""
    op_id = str(operation.get("op") or "")
    fields = _DECLARED_FIELDS.get(op_id)
    if not fields:
        return ()
    return tuple(name.format(key=operation.get("key", ""))
                 for name in fields)


def scope_for(operations: Sequence[Mapping], *,
              conflict_domains: Sequence[str] = (),
              affected_spans: Sequence[Sequence[int]] = ()) -> MutationScope:
    """The authorized mutation scope of one edit.

    Built from what the patch already declares: the conflict domains it
    named, the spans it claimed, and the operations it carries. An
    operation whose type is not in the algebra has no domain, so nothing
    can be authorized for it - it is named in ``unbounded_ops`` rather
    than silently allowed to touch the timeline.
    """
    pairs: list[tuple[str, str]] = []
    targets: list[str] = []
    op_ids: list[str] = []
    unbounded: list[str] = []
    rows: list[tuple[str, str]] = []
    created_rows: list[tuple[str, str]] = []
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        op_id = str(operation.get("op") or "")
        op_ids.append(op_id)
        domain = _domain_for(operation)
        refs = _row_refs(operation)
        if op_id in _ROW_OPS:
            for ref in refs:
                if ref not in rows:
                    rows.append(ref)
        else:
            for ref in refs:
                if ref not in created_rows:
                    created_rows.append(ref)
        if not domain:
            unbounded.append(op_id or "<unnamed>")
            continue
        target = _target_for(operation)
        targets.append(target)
        pair = (target, domain)
        if pair not in pairs:
            pairs.append(pair)
        if op_id in _MEDIA_REPLACING_OPS:
            media_pair = (target, SOURCE_IDENTITY)
            if media_pair not in pairs:
                pairs.append(media_pair)
    return MutationScope(
        domains=tuple(dict.fromkeys(
            [str(domain) for domain in conflict_domains]
            + [domain for domain in (
                _domain_for(operation)
                for operation in (operations or ())
                if isinstance(operation, Mapping)) if domain])),
        spans=tuple((int(span[0]), int(span[1])) for span in affected_spans
                    or ()),
        pairs=tuple(pairs),
        targets=tuple(dict.fromkeys(targets)),
        operations=tuple(op_ids),
        unbounded_ops=tuple(dict.fromkeys(unbounded)),
        rows=tuple(rows),
        created_rows=tuple(created_rows),
    )


def _clips(snapshot: Mapping) -> dict:
    """Every clip in a snapshot, keyed by its unique id."""
    clips = {}
    for track in snapshot.get("tracks") or ():
        track_type = track.get("type")
        for clip in track.get("clips") or ():
            unique_id = clip.get("unique_id")
            if unique_id is None:
                continue
            clips[str(unique_id)] = (clip, track_type)
    return clips


def _tracks(snapshot: Mapping) -> dict:
    """Every row in a snapshot, keyed by (type, index)."""
    tracks = {}
    for track in snapshot.get("tracks") or ():
        key = (str(track.get("type")), int(track.get("index") or 0))
        tracks[key] = track
    return tracks


def _domain_of(field_path: str, track_type: str | None) -> str:
    """The domain a changed field belongs to."""
    head, _, rest = field_path.partition(".")
    if head == "transform":
        if track_type == "audio":
            return "audio_mix"
        return "audio_mix" if rest in AUDIO_KEYS else "picture_transform"
    if head == "color":
        return "color"
    if head == "fusion":
        return "fusion"
    if head == "markers":
        return "markers"
    if head == "keyframes":
        return "picture_transform"
    if head in ("media_pool_item_id", "source_file"):
        return SOURCE_IDENTITY
    return "timeline_structure"


def _row(target: str, field_path: str, domain: str, before, after) -> dict:
    return {"target": target, "field": field_path, "domain": domain,
            "before": before, "after": after, "authorized": None}


def _clip_rows(before: Mapping, after: Mapping, target: str,
               track_type: str | None) -> list[dict]:
    """Every field of one clip that changed."""
    rows = []
    for key in sorted(set(before) | set(after)):
        if key in _NON_STATE_KEYS:
            continue
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            for sub in sorted(set(old) | set(new)):
                if old.get(sub) == new.get(sub):
                    continue
                name = f"{key}.{sub}"
                rows.append(_row(target, name, _domain_of(name, track_type),
                                 old.get(sub), new.get(sub)))
            continue
        domain = _domain_of(key, track_type)
        if domain == SOURCE_IDENTITY and key in ("media_pool_item_id",
                                                 "source_file"):
            rows.append(_row(target, "media_identity", SOURCE_IDENTITY,
                             old, new))
            continue
        rows.append(_row(target, key, domain, old, new))
    return rows


def _timeline_rows(before: Mapping, after: Mapping) -> list[dict]:
    """The timeline's own state: its duration, its markers."""
    rows = []
    for key in ("start_frame", "end_frame"):
        old, new = before.get(key), after.get(key)
        if old != new:
            rows.append(_row("timeline", key, "timeline_structure", old, new))
    old_markers = (before.get("markers") or {}).get("timeline") or []
    new_markers = (after.get("markers") or {}).get("timeline") or []
    if old_markers != new_markers:
        rows.append(_row("timeline", "markers.timeline", "markers",
                         old_markers, new_markers))
    return rows


def snapshot_delta(before: Mapping, after: Mapping) -> tuple[Mapping, ...]:
    """Every semantic difference between two timeline snapshots.

    The rows are ordered by target then field, so two runs over the same
    pair of snapshots produce the same list. A clip that appeared or
    disappeared is one row, not a row per field: the edit that placed
    it owns all of it.
    """
    rows: list[dict] = []
    before_clips = _clips(before)
    after_clips = _clips(after)
    for unique_id in sorted(set(before_clips) | set(after_clips)):
        target = f"clip:{unique_id}"
        old = before_clips.get(unique_id)
        new = after_clips.get(unique_id)
        if old is None:
            rows.append(_row(target, "inserted", "timeline_structure",
                             None, new[0]))
            continue
        if new is None:
            rows.append(_row(target, "removed", "timeline_structure",
                             old[0], None))
            continue
        rows.extend(_clip_rows(old[0], new[0], target, new[1] or old[1]))
    before_tracks = _tracks(before)
    after_tracks = _tracks(after)
    for key in sorted(set(before_tracks) | set(after_tracks)):
        target = f"track:{key[0]}:{key[1]}"
        old = before_tracks.get(key)
        new = after_tracks.get(key)
        if old is None:
            rows.append(_row(target, "inserted", "timeline_structure",
                             None, new))
            continue
        if new is None:
            rows.append(_row(target, "removed", "timeline_structure",
                             old, None))
            continue
        for name in ("name", "enabled", "locked"):
            if old.get(name) != new.get(name):
                rows.append(_row(target, name, "timeline_structure",
                                 old.get(name), new.get(name)))
    rows.extend(_timeline_rows(before, after))
    return tuple(rows)


def _clip_span(snapshot: Mapping, target: str) -> tuple[int, int] | None:
    """The frames one clip occupies in a snapshot."""
    if not target.startswith("clip:"):
        return None
    unique_id = target.split(":", 1)[1]
    found = _clips(snapshot).get(unique_id)
    if found is None:
        return None
    clip = found[0]
    try:
        return (int(clip["record_in"]), int(clip["record_out"]))
    except (KeyError, TypeError, ValueError):
        return None


def _overlaps(span: tuple[int, int],
              spans: Sequence[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1]
               for other in spans)


def _observable_domains(snapshot: Mapping) -> set[str]:
    """The domains this snapshot can actually see."""
    clips = [clip for clip, _track in _clips(snapshot).values()]
    domains = set()
    if snapshot.get("tracks"):
        domains.add("timeline_structure")
    if "markers" in snapshot:
        domains.add("markers")
    if any("transform" in clip for clip in clips):
        domains.add("picture_transform")
        domains.add("audio_mix")
    if any("color" in clip for clip in clips):
        domains.add("color")
    if any("fusion" in clip for clip in clips):
        domains.add("fusion")
    return domains


def expected_delta(scope: MutationScope, before: Mapping,
                   operations: Sequence[Mapping]) -> tuple[Mapping, ...]:
    """The semantic delta the edit is authorized to produce.

    Computed BEFORE the write, from the operations themselves: each
    operation authorizes its own domain on the target it names. The rows
    name the operation that authorized them and the fields that
    operation declared, so a reader can see what was promised beside
    what was observed.
    """
    rows = []
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        op_id = str(operation.get("op") or "")
        domain = _domain_for(operation)
        if not domain:
            continue
        target = _target_for(operation)
        rows.append({
            "target": target,
            "domain": domain,
            "operation": op_id,
            "fields": list(_declared_fields(operation)),
        })
        if op_id in _MEDIA_REPLACING_OPS:
            rows.append({
                "target": target,
                "domain": SOURCE_IDENTITY,
                "operation": op_id,
                "fields": ["media_identity"],
            })
    return tuple(rows)


def _authorize(row: Mapping, scope: MutationScope,
               before: Mapping, after: Mapping) -> bool:
    """Whether the scope authorized one observed change."""
    target = str(row.get("target"))
    domain = str(row.get("domain"))
    if scope.allows(target, domain):
        return True
    if target.startswith("track:"):
        if str(row.get("field")) == "inserted" \
                and scope.allows_created_row(target):
            return True
        return domain in scope.domains and scope.allows_row(target)
    if domain not in scope.domains:
        return False
    if target == "timeline":
        return True
    span = _clip_span(after, target) or _clip_span(before, target)
    if span is None:
        return False
    return _overlaps(span, scope.spans)


def compare(scope: MutationScope, expected: Sequence[Mapping],
             changes: Sequence[Mapping], *,
             before: Mapping, after: Mapping) -> SemanticDelta:
    """Judge the observed delta against the expected one.

    The expected delta says what the edit was authorized to change; the
    observed delta is what the write actually changed. A change neither
    authorized nor covered by the patch's declared spans is collateral,
    and it is reported with the target, the field and the domain so the
    reader knows what moved.
    """
    judged = []
    for row in changes:
        judged.append({**row,
                       "authorized": _authorize(row, scope, before, after)})
    observed_pairs = {(str(row["target"]), str(row["domain"]))
                      for row in judged}
    expected_rows = []
    not_observed = []
    for row in expected:
        pair = (str(row["target"]), str(row["domain"]))
        if pair in observed_pairs:
            expected_rows.append(row)
            continue
        expected_rows.append(row)
        not_observed.append(row)
    unobservable = tuple(sorted(
        domain for domain in scope.domains
        if domain not in _observable_domains(before)))
    return SemanticDelta(
        changes=tuple(judged),
        expected=tuple(expected_rows),
        expected_not_observed=tuple(not_observed),
        unobservable_domains=unobservable,
    )


def invariant(*, scope: MutationScope, before: Mapping,
              operations: Sequence[Mapping], after: Mapping) -> SemanticDelta:
    """The whole preservation invariant for one write.

    ``before`` is the pre-edit snapshot, ``after`` the state the write
    left behind. The expected delta is computed from the operations
    before the comparison, so the verdict answers "did this write change
    anything it was not authorized to change?" and nothing else.
    """
    reason = ""
    if scope.unbounded_ops:
        reason = (
            "the edit carries operations whose authorized scope is not "
            f"declared: {', '.join(scope.unbounded_ops)}")
    elif not isinstance(before, Mapping) or not isinstance(after, Mapping):
        reason = "the pre-edit and post-edit states are not comparable"
    changes = snapshot_delta(before, after) if not reason else ()
    expected = expected_delta(scope, before, operations) if not reason else ()
    return compare(scope, expected, changes, before=before, after=after) \
        if not reason else SemanticDelta(unprovable_reason=reason)


def pre_edit_snapshot(*, store, project: str, timeline_id: str) -> dict:
    """The recorded state an edit is authorized against.

    The shadow store is the engine's record of every timeline it has
    read; its head for this timeline is the pre-edit snapshot. A timeline
    with no recorded generation cannot establish a guarantee, and says so
    rather than proceeding against an empty state.
    """
    head = store.head(project, timeline_id)
    if head is None:
        raise PreservationUnprovable(
            what=f"no recorded state for timeline {timeline_id!r}",
            why="the timeline has never been read into the shadow store, so "
                "there is no pre-edit state to preserve against",
            fix="read the timeline once (ren timeline refresh) and retry")
    return store.snapshot(head)
