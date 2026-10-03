"""EditPatch: the unit of work a Resolve timeline accepts.

The single-Resolve plan (captain, 2026-10-02, item 3): an agent does its
thinking FREE and hands the broker a prepared patch - "everything is
prepared, here are the exact operations I need committed" - so the
Resolve critical section is the operations and one read-back, nothing
else. A patch is plain data: it can be queued, retried, inspected and
tested without Resolve.

The shape
---------
    id                idempotency key, chosen by the author
    project           the Resolve project's EXACT name (the open one)
    timeline          the timeline's EXACT name
    base_generation   the shadow generation the patch was planned against
                      (`timeline_shadow`); 0 is never valid
    capability        a capability id (`capabilities.get` must know it)
    affected_spans    [[start, end), ...] in timeline frames
    conflict_domains  a subset of `CONFLICT_DOMAINS`
    operations        [{"op": <one of OPERATIONS>, ...}, ...]
    preconditions     [{"kind": <one of CONDITIONS>, ...}, ...] on the base
    postconditions    the same vocabulary, judged on the read-back
    base_git_sha      optional: the plan's commit

A declaration must be true (AGENTS.md 3): every operation's conflict
domain must be declared, and every frame an operation touches must sit
inside a declared span. `validate` refuses a patch that says less than
it does, checked against the BASE snapshot before Resolve is touched.

Apply, and what each outcome means
----------------------------------
`apply_patch` is the broker's `timeline.apply_patch` job. In order:

1. FREE: the patch is validated against the stored base generation.
   A base that is not the head raises `StalePatch` - nothing written;
   `rebase` says whether it can move forward.
2. RESOLVE, under the exclusive lease with the cursor fenced on the
   timeline: the live timeline is read and hashed. A hash that is not the
   head's means a change nobody recorded (the captain's hand edit): it is
   recorded as an `observed` generation and the patch raises `StalePatch`
   - the timeline no longer matches the generation it was planned on.
   Then the declared preconditions are judged on that live read
   (`PatchRefused`), the operations run in order, and the timeline is
   read back once.
3. The read-back is recorded as the next generation, source `patch`,
   with the patch and its receipt. Every operation is JUDGED BY THE
   READ-BACK (AGENTS.md 5), never by its return value, and so is every
   postcondition. A failed judgement is `status: verification_failed` -
   the generation still records what Resolve really holds, because the
   shadow describes the timeline, not the plan.

Nothing is rolled back. A half-applied patch is recorded as the state it
left, with every failed judgement named; undoing it is a new patch
against that generation. An operation that raises stops the patch at
that operation, and the receipt says which ran.

Rebase
------
`rebase(patch)` moves a stale patch onto the head when it composes with
every generation in between - commute, merge, serialize or rebase, never
conflict (`library/tools/patch_algebra.py`, which decides by what each
operation writes and what each capability declares) - AND its own
targets and preconditions still hold on the head. An `observed`
generation in between refuses: its change is unattributed, so nothing
can say what it meets.

`tests/unit/resolve/test_edit_patch.py`.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from library.tools import resolve_lock
from library.tools import timeline_shadow as shadow
from library.tools.ren_refusal import RenRefusal

#: What a patch may declare it touches. An operation's domain is fixed by
#: its type (`OPERATIONS`); the vocabulary is the plan's.
CONFLICT_DOMAINS = frozenset({
    "timeline_structure", "captions", "picture_transform", "color",
    "fusion", "audio_mix", "markers",
})

#: Keys `clip.set_property` may write: the transform `GetProperty()`
#: reports, and nothing else - a composite or retime key is another
#: domain's write.
TRANSFORM_KEYS = frozenset({
    "Pan", "Tilt", "ZoomX", "ZoomY", "ZoomGang", "RotationAngle",
    "AnchorPointX", "AnchorPointY", "Pitch", "Yaw", "FlipX", "FlipY",
    "CropLeft", "CropRight", "CropTop", "CropBottom", "CropSoftness",
    "CropRetain", "Opacity",
})

#: How close a re-read float must be to what was written.
FLOAT_TOLERANCE = 1e-6
SMART_REFRAME_KEYS = ("Pan", "Tilt", "ZoomX", "ZoomY")


class PatchRefused(RenRefusal):
    """The patch cannot be applied as stated. Nothing was written."""


class StalePatch(PatchRefused):
    """The patch was planned on a generation that is no longer the head."""

    def __init__(self, patch: EditPatch, head: int, *, observed: bool,
                 rebase_possible: bool, reason: str) -> None:
        self.head = head
        self.observed = observed
        self.rebase_possible = rebase_possible
        super().__init__(
            what=(f"patch {patch.id!r} was planned on generation "
                  f"{patch.base_generation} of {patch.timeline!r}, and the "
                  f"head is {head}"),
            why=reason,
            fix=("`edit_patch.rebase(patch)` moves it onto the head"
                 if rebase_possible else
                 f"re-plan the patch against generation {head} "
                 f"(`python3 -m library.tools.timeline_shadow show "
                 f"--project {patch.project!r} --timeline "
                 f"{patch.timeline!r}`)"))


# ── The patch ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EditPatch:
    id: str
    project: str
    timeline: str
    base_generation: int
    capability: str
    affected_spans: tuple
    conflict_domains: tuple
    operations: tuple
    preconditions: tuple = ()
    postconditions: tuple = ()
    base_git_sha: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> EditPatch:
        if isinstance(data, cls):
            return data
        unknown = set(data) - {f for f in cls.__dataclass_fields__}
        if unknown:
            raise PatchRefused(
                what=f"patch {data.get('id')!r} carries unknown keys "
                     f"{sorted(unknown)}",
                why="a key nothing reads is a declaration nothing honours",
                fix="drop the keys, or extend `edit_patch.EditPatch`")
        return cls(
            id=data["id"], project=data["project"],
            timeline=data["timeline"],
            base_generation=int(data["base_generation"]),
            capability=data["capability"],
            affected_spans=tuple(tuple(int(v) for v in span)
                                 for span in data["affected_spans"]),
            conflict_domains=tuple(data["conflict_domains"]),
            operations=tuple(dict(op) for op in data["operations"]),
            preconditions=tuple(dict(c) for c in
                                data.get("preconditions", ())),
            postconditions=tuple(dict(c) for c in
                                 data.get("postconditions", ())),
            base_git_sha=data.get("base_git_sha", ""))

    def to_dict(self) -> dict:
        out = asdict(self)
        out["affected_spans"] = [list(s) for s in self.affected_spans]
        for key in ("conflict_domains", "operations", "preconditions",
                    "postconditions"):
            out[key] = list(out[key])
        return out


def _refuse(patch: EditPatch, what: str, why: str, fix: str):
    raise PatchRefused(what=f"patch {patch.id!r}: {what}", why=why, fix=fix)


# ── Reading a snapshot ─────────────────────────────────────────────────


def _clip(snapshot: dict, unique_id: str) -> dict | None:
    found = [c for c in shadow.clips(snapshot)
             if c["unique_id"] == unique_id]
    return found[0] if len(found) == 1 else None


def _timeline_marker(snapshot: dict, frame: int) -> dict | None:
    for marker in snapshot.get("markers", {}).get("timeline", []):
        if int(marker["frame"]) == int(frame):
            return marker
    return None


def _clip_marker(snapshot: dict, unique_id: str,
                 frame: int) -> dict | None:
    clip = _clip(snapshot, unique_id)
    if clip is None:
        return None
    for marker in clip["markers"]:
        if int(marker["frame"]) == int(frame):
            return marker
    return None


def _matches(marker: dict, want: dict) -> bool:
    return all(marker.get(k) == want[k] for k in (
        "color", "name", "note", "duration", "custom_data")
               if k in want)


# ── Operations: what each writes, where, and how it is judged ─────────


@dataclass(frozen=True)
class Operation:
    domain: str
    required: tuple
    span: Callable        # (op, base_snapshot) -> (start, end) or None
    apply: Callable       # (op, timeline, item_handle_or_None) -> return
    verify: Callable      # (op, after_snapshot) -> failure text or None
    key: Callable         # (op) -> what it writes; keys MEET on a prefix
    merge: str            # its own `patch_algebra.MERGE_SEMANTICS` entry
    item: bool = False    # True: the op addresses `unique_id`
    temporal: str = "local"   # `patch_algebra.TEMPORAL_EFFECTS`; none ripples


def _item_span(op, base):
    clip = _clip(base, op["unique_id"])
    return (clip["record_in"], clip["record_out"]) if clip else None


def _verify_marker_add(op, after):
    marker = _timeline_marker(after, op["frame"])
    if marker is None or not _matches(marker, op):
        return f"no {op.get('color')} marker {op.get('name')!r} reads back at frame {op['frame']}"
    return None


def _verify_marker_delete(op, after):
    if _timeline_marker(after, op["frame"]) is not None:
        return f"a timeline marker still reads back at frame {op['frame']}"
    return None


def _verify_clip_marker_add(op, after):
    marker = _clip_marker(after, op["unique_id"], op["frame"])
    if marker is None or not _matches(marker, op):
        return (f"no matching clip marker {op.get('name')!r} reads back "
                f"on {op['unique_id']!r} at source frame {op['frame']}")
    return None


def _verify_clip_marker_delete(op, after):
    if _clip_marker(after, op["unique_id"], op["frame"]) is not None:
        return (f"a clip marker still reads back on {op['unique_id']!r} "
                f"at source frame {op['frame']}")
    return None


def _verify_smart_reframe(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} disappeared during Smart Reframe"
    before = op["before"]
    current = clip["transform"]
    if all(_same_value(current.get(key), before.get(key))
           for key in op["keys"]):
        return (f"SmartReframe reports success but Pan/Tilt/Zoom read back "
                f"unchanged ({before})")
    return None


def _apply_smart_reframe(op, _timeline, item):
    returned = item.SmartReframe()
    if not returned:
        raise RuntimeError("SmartReframe answered False")
    return returned


def _apply_set_property(op, _timeline, item):
    from library.tools.transform_write_log import set_property

    return set_property(
        item, op["key"], op["value"],
        item_identity={"resolve_unique_id": str(op["unique_id"])})


def _same_value(got, want):
    if (isinstance(want, (int, float))
            and isinstance(got, (int, float))):
        return abs(float(got) - float(want)) <= FLOAT_TOLERANCE
    return got == want


def _live_handles(timeline):
    from library.tools.reel_read import live_items

    handles = {}
    duplicates = set()
    for row in live_items(timeline):
        for item in row["items"]:
            try:
                unique_id = str(item.GetUniqueId() or "")
            except Exception as unreadable:  # noqa: BLE001
                raise RuntimeError(
                    f"an item on {row['name']!r} would not report its "
                    f"unique id ({unreadable})") from unreadable
            if not unique_id:
                continue
            if unique_id in handles:
                duplicates.add(unique_id)
            handles[unique_id] = item
    for unique_id in duplicates:
        handles.pop(unique_id, None)
    return handles


def _delete_clip_with_measured_retry(op, timeline, item):
    """Retry one local delete only when its unique id still reads back.

    `ren edit delete` measured the first call flopping on some Resolve
    pages. The old path re-read the timeline and retried once only while
    the item remained; preserve that measured behavior inside the patch.
    """
    first = timeline.DeleteClips([item], False)
    handles = _live_handles(timeline)
    unique_id = op["unique_id"]
    if unique_id not in handles:
        return first
    second = timeline.DeleteClips([handles[unique_id]], False)
    return second if second else first


def _verify_enabled(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None or clip["enabled"] is not bool(op["enabled"]):
        return (f"clip {op['unique_id']!r} reads enabled="
                f"{None if clip is None else clip['enabled']}, not "
                f"{bool(op['enabled'])}")
    return None


def _verify_property(op, after):
    clip = _clip(after, op["unique_id"])
    got = None if clip is None else clip["transform"].get(op["key"])
    want = op["value"]
    same = (got == want if not isinstance(want, (int, float))
            or not isinstance(got, (int, float))
            else abs(float(got) - float(want)) <= FLOAT_TOLERANCE)
    if not same:
        return f"clip {op['unique_id']!r} reads {op['key']}={got!r}, not {want!r}"
    return None


def _verify_delete(op, after):
    if _clip(after, op["unique_id"]) is not None:
        return f"clip {op['unique_id']!r} still reads back"
    return None


OPERATIONS = {
    "marker.add": Operation(
        domain="markers", required=("frame", "color", "name"),
        span=lambda op, base: (int(op["frame"]),
                               int(op["frame"]) + int(op.get("duration", 1))),
        apply=lambda op, tl, _item: tl.AddMarker(
            int(op["frame"]), op["color"], op["name"], op.get("note", ""),
            int(op.get("duration", 1)), op.get("custom_data", "")),
        verify=_verify_marker_add,
        # One marker per frame: a second add there replaces the first.
        key=lambda op: ("marker", int(op["frame"])), merge="replace"),
    "marker.delete": Operation(
        domain="markers", required=("frame",),
        span=lambda op, base: (int(op["frame"]), int(op["frame"]) + 1),
        apply=lambda op, tl, _item: tl.DeleteMarkerAtFrame(int(op["frame"])),
        verify=_verify_marker_delete,
        key=lambda op: ("marker", int(op["frame"])), merge="commutative"),
    "clip_marker.add": Operation(
        domain="markers", required=("unique_id", "frame", "color", "name"),
        span=_item_span, item=True,
        apply=lambda op, _tl, item: (
            item.AddMarker(int(op["frame"]), op["color"], op["name"],
                           op.get("note", ""),
                           int(op.get("duration", 1)), op["custom_data"])
            if op.get("custom_data") else
            item.AddMarker(int(op["frame"]), op["color"], op["name"],
                           op.get("note", ""),
                           int(op.get("duration", 1)))),
        verify=_verify_clip_marker_add,
        key=lambda op: ("clip", op["unique_id"], "marker",
                        int(op["frame"])), merge="replace"),
    "clip_marker.delete": Operation(
        domain="markers", required=("unique_id", "frame"),
        span=_item_span, item=True,
        apply=lambda op, _tl, item: item.DeleteMarkerAtFrame(
            int(op["frame"])),
        verify=_verify_clip_marker_delete,
        key=lambda op: ("clip", op["unique_id"], "marker",
                        int(op["frame"])), merge="commutative"),
    "clip.smart_reframe": Operation(
        domain="picture_transform",
        required=("unique_id", "keys", "before"),
        span=_item_span, item=True,
        apply=_apply_smart_reframe,
        verify=_verify_smart_reframe,
        # The native command may change any framing property on the item.
        key=lambda op: ("clip", op["unique_id"], "transform"),
        merge="exclusive"),
    "clip.set_enabled": Operation(
        domain="timeline_structure", required=("unique_id", "enabled"),
        span=_item_span, item=True,
        apply=lambda op, tl, item: item.SetClipEnabled(bool(op["enabled"])),
        verify=_verify_enabled,
        key=lambda op: ("clip", op["unique_id"], "enabled"),
        merge="replace"),
    "clip.set_property": Operation(
        domain="picture_transform", required=("unique_id", "key", "value"),
        span=_item_span, item=True,
        apply=_apply_set_property,
        verify=_verify_property,
        key=lambda op: ("clip", op["unique_id"], op["key"]),
        merge="replace"),
    "clip.delete": Operation(
        domain="timeline_structure", required=("unique_id",),
        span=_item_span, item=True,
        # Never ripple: a ripple moves every later cut, which no span the
        # patch declares can cover.
        apply=_delete_clip_with_measured_retry,
        verify=_verify_delete,
        # The whole clip: it meets every write to it, in any domain.
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive"),
}


# ── Conditions: judged on a snapshot (preconditions on the live base,
#    postconditions on the read-back) ───────────────────────────────────


def _duration(snapshot: dict) -> int:
    return int(snapshot["end_frame"]) - int(snapshot["start_frame"])


def _cond_clip_present(c, snap, base):
    return None if _clip(snap, c["unique_id"]) else \
        f"clip {c['unique_id']!r} is not on the timeline"


def _cond_clip_absent(c, snap, base):
    return None if _clip(snap, c["unique_id"]) is None else \
        f"clip {c['unique_id']!r} is on the timeline"


def _cond_marker_present(c, snap, base):
    marker = _timeline_marker(snap, c["frame"])
    return None if marker and _matches(marker, c) else \
        f"no matching timeline marker at frame {c['frame']}"


def _cond_marker_absent(c, snap, base):
    return None if _timeline_marker(snap, c["frame"]) is None else \
        f"a timeline marker is at frame {c['frame']}"


def _cond_clip_marker_present(c, snap, base):
    marker = _clip_marker(snap, c["unique_id"], c["frame"])
    return None if marker and _matches(marker, c) else (
        f"no matching clip marker at source frame {c['frame']} on "
        f"{c['unique_id']!r}")


def _cond_clip_marker_absent(c, snap, base):
    return None if _clip_marker(snap, c["unique_id"], c["frame"]) is None \
        else (f"a clip marker is at source frame {c['frame']} on "
              f"{c['unique_id']!r}")


def _cond_clip_transform_equals(c, snap, base):
    clip = _clip(snap, c["unique_id"])
    if clip is None:
        return f"clip {c['unique_id']!r} is not on the timeline"
    for key, want in c["properties"].items():
        got = clip["transform"].get(key)
        if not _same_value(got, want):
            return (f"clip {c['unique_id']!r} reads {key}={got!r}, "
                    f"not {want!r}")
    return None


def _cond_duration_unchanged(c, snap, base):
    return None if _duration(snap) == _duration(base) else \
        f"duration is {_duration(snap)} frames, was {_duration(base)}"


def _cond_clip_count(c, snap, base):
    count = sum(1 for clip in shadow.clips(snap)
                if clip["track_type"] == c["track_type"]
                and clip["track_index"] == int(c["track_index"]))
    return None if count == int(c["count"]) else \
        f"{c['track_type']}{c['track_index']} holds {count} clips, not {c['count']}"


CONDITIONS = {
    "clip_present": (("unique_id",), _cond_clip_present),
    "clip_absent": (("unique_id",), _cond_clip_absent),
    "marker_present": (("frame",), _cond_marker_present),
    "marker_absent": (("frame",), _cond_marker_absent),
    "clip_marker_present": (("unique_id", "frame"),
                             _cond_clip_marker_present),
    "clip_marker_absent": (("unique_id", "frame"),
                            _cond_clip_marker_absent),
    "clip_transform_equals": (("unique_id", "properties"),
                               _cond_clip_transform_equals),
    "duration_unchanged": ((), _cond_duration_unchanged),
    "clip_count": (("track_type", "track_index", "count"), _cond_clip_count),
}


def _judge(conditions, snap: dict, base: dict) -> list:
    return [f"{c['kind']}: {failure}" for c in conditions
            for failure in [CONDITIONS[c["kind"]][1](c, snap, base)]
            if failure]


# ── Validation: FREE, against the base snapshot ───────────────────────


def _covered(span, spans) -> bool:
    return any(s[0] <= span[0] and span[1] <= s[1] for s in spans)


def validate(patch: EditPatch, base: dict) -> None:
    """Refuse a patch whose declarations are not true of what it does."""
    from library.tools import capabilities
    from library.tools.operations import UnknownOperation

    if not patch.id or patch.base_generation < 1:
        _refuse(patch, "has no id or no base generation",
                "a patch is applied against one recorded generation, and "
                "its id is how a retry is recognised",
                "observe the timeline (`timeline_shadow.observe`) and plan "
                "against its generation")
    try:
        capability = capabilities.get(patch.capability)
    except UnknownOperation:
        _refuse(patch, f"names capability {patch.capability!r}, which "
                       f"is not registered",
                "a capability's id is its identity (AGENTS.md 3)",
                "name one of `capabilities.ids()`")
    declared = capability.execution.patch
    if declared is None:
        _refuse(patch, f"names capability {patch.capability!r}, which "
                       f"declares no patch semantics",
                "composition trusts the capability's declaration "
                "(`patch_algebra`), and this one makes none",
                "name a capability whose `Operation.execution.patch` "
                "declares the write")
    phase = capability.execution.phase("apply")
    if (phase is None or phase.resolve_mode != "exclusive"
            or phase.locality != "timeline"
            or phase.freshness != "timeline_generation"):
        _refuse(patch, f"capability {patch.capability!r} has no compatible "
                       "patch execution policy",
                "patches need exclusive Resolve access, timeline locality "
                "and a recorded timeline generation",
                "declare those requirements in the capability's execution "
                "phase")
    beyond = set(patch.conflict_domains) - set(declared.conflict_domains)
    if beyond:
        _refuse(patch, f"declares domains {sorted(beyond)}, beyond what "
                       f"{patch.capability!r} declares",
                "a patch says no more than its capability may write",
                f"keep to {sorted(declared.conflict_domains)}")
    if not patch.operations or not patch.affected_spans:
        _refuse(patch, "declares no operations or no affected spans",
                "an empty patch has nothing to commit, and a span-less one "
                "cannot be judged against any other",
                "declare at least one operation and the frames it touches")
    if any(s[0] >= s[1] for s in patch.affected_spans):
        _refuse(patch, f"declares an empty span in {patch.affected_spans}",
                "a span is [start, end) in timeline frames",
                "give every span an end after its start")
    unknown = set(patch.conflict_domains) - CONFLICT_DOMAINS
    if unknown:
        _refuse(patch, f"declares unknown conflict domains {sorted(unknown)}",
                "conflict domains are one vocabulary",
                f"use {sorted(CONFLICT_DOMAINS)}")
    for kind_list in (patch.preconditions, patch.postconditions):
        for cond in kind_list:
            spec = CONDITIONS.get(cond.get("kind"))
            if spec is None or any(k not in cond for k in spec[0]):
                _refuse(patch, f"states condition {cond!r}",
                        "a condition is one of a fixed vocabulary, with its "
                        "keys",
                        f"use one of {sorted(CONDITIONS)} with its keys")
    for index, op in enumerate(patch.operations):
        spec = OPERATIONS.get(op.get("op"))
        if spec is None:
            _refuse(patch, f"operation {index} is {op.get('op')!r}",
                    "only measured, read-back-judged writes are operations",
                    f"use one of {sorted(OPERATIONS)}")
        if op["op"] not in declared.operations:
            _refuse(patch, f"operation {index} ({op['op']}) is not one "
                           f"{patch.capability!r} declares",
                    "composition trusts the capability's declaration",
                    f"use one of {list(declared.operations)}")
        missing = [k for k in spec.required if k not in op]
        if missing:
            _refuse(patch, f"operation {index} ({op['op']}) lacks {missing}",
                    "an operation names everything it writes",
                    f"add {missing}")
        if spec.domain not in patch.conflict_domains:
            _refuse(patch, f"operation {index} ({op['op']}) writes "
                           f"{spec.domain!r}, which the patch does not "
                           f"declare",
                    "a declaration must be true (AGENTS.md 3): a scheduler "
                    "that trusts the declared domains would let a "
                    "conflicting patch through",
                    f"add {spec.domain!r} to conflict_domains")
        if op["op"] == "clip.set_property" and op["key"] not in TRANSFORM_KEYS:
            _refuse(patch, f"operation {index} writes property {op['key']!r}",
                    "clip.set_property is the picture transform only",
                    f"use one of {sorted(TRANSFORM_KEYS)}")
        if op["op"] == "clip.smart_reframe":
            if tuple(op["keys"]) != SMART_REFRAME_KEYS:
                _refuse(patch, f"operation {index} names reframe keys "
                               f"{op['keys']!r}",
                        "SmartReframe is judged on its measured framing "
                        "properties",
                        f"use {list(SMART_REFRAME_KEYS)}")
            if not isinstance(op["before"], dict) or any(
                    key not in op["before"] for key in SMART_REFRAME_KEYS):
                _refuse(patch, f"operation {index} has no complete framing "
                               "read-back",
                        "a native operation needs a baseline to verify its "
                        "effect",
                        f"include all of {list(SMART_REFRAME_KEYS)}")
        span = spec.span(op, base)
        if span is None:
            _refuse(patch, f"operation {index} ({op['op']}) targets clip "
                           f"{op.get('unique_id')!r}, which is not exactly "
                           f"once on the base generation",
                    "an operation addresses an item the patch's base holds",
                    "re-plan against the head generation")
        if not _covered(span, patch.affected_spans):
            _refuse(patch, f"operation {index} ({op['op']}) touches frames "
                           f"{list(span)}, outside the declared spans",
                    "a declaration must be true (AGENTS.md 3)",
                    "widen affected_spans to cover it")


# ── Apply ──────────────────────────────────────────────────────────────


def rebase(patch, store: shadow.ShadowStore | None = None) -> EditPatch:
    """The patch moved onto the head, or `StalePatch` saying why not.

    `patch_algebra.carry` composes it with every generation since its
    base.
    """
    from library.tools import patch_algebra

    patch = EditPatch.from_dict(patch)
    store = store or shadow.ShadowStore()
    timeline_id = store.resolve_timeline(patch.project, patch.timeline)
    moved, reason = patch_algebra.carry(patch, store, patch.project,
                                        timeline_id)
    if moved is None:
        raise StalePatch(patch, store.head(patch.project,
                                           timeline_id).generation,
                         observed=False, rebase_possible=False,
                         reason=reason)
    return moved


def apply_live_patch(*, project, timeline, capability: str,
                     operations, conflict_domains,
                     idempotency_key: str, preconditions=(),
                     postconditions=(),
                     store: shadow.ShadowStore | None = None,
                     project_folder: str | None = None) -> dict:
    """Build and commit a patch while the caller already owns Resolve.

    Entry points which already hold a Resolve lease use this seam to
    give one in-process write the same generation checks and read-back
    receipt as `timeline.apply_patch`. Remote callers submit that broker
    job instead of holding a lease here.
    """
    from library.tools import resolve_lock

    if not idempotency_key:
        raise ValueError("an EditPatch needs a non-empty idempotency key")
    operations = tuple(dict(op) for op in operations)
    conflict_domains = tuple(conflict_domains)
    preconditions = tuple(dict(condition) for condition in preconditions)
    postconditions = tuple(dict(condition) for condition in postconditions)
    if not operations:
        raise ValueError("an EditPatch needs at least one operation")
    store = store or shadow.ShadowStore()
    project_name, timeline_id, timeline_name = shadow.timeline_key(
        project, timeline)
    identity = json.dumps(
        [project_name, timeline_id, idempotency_key],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str)
    patch_id = "ren-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    intent = {
        "project": project_name,
        "timeline": timeline_name,
        "capability": capability,
        "conflict_domains": list(conflict_domains),
        "operations": list(operations),
        "preconditions": list(preconditions),
        "postconditions": list(postconditions),
    }

    # A caller retry after a successful write receives the recorded result
    # even though observing the live timeline would now produce a newer
    # base. The prior intent is checked against the reused key, so this
    # cannot return a receipt for different work.
    for generation in store.history(project_name, timeline_id):
        if (generation.source == shadow.PATCH
                and (generation.patch or {}).get("id") == patch_id):
            prior = generation.patch
            prior_intent = {key: prior[key] for key in intent}
            if json.dumps(prior_intent, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str) != json.dumps(
                              intent, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), default=str):
                _refuse(
                    EditPatch.from_dict(prior),
                    "this idempotency key was already used with different "
                    "contents",
                    "a patch id is a durable identity for one complete edit",
                    "retry the original operation unchanged, or assign a "
                    "new idempotency key")
            return _receipt_of(generation)

    with resolve_lock.cursor_excursion(
            project, timeline, f"prepare EditPatch {patch_id}"):
        base = shadow.observe(project, timeline, store)
        snapshot = store.snapshot(base)
        spans = []
        for operation in operations:
            spec = OPERATIONS.get(operation.get("op"))
            if spec is None:
                _refuse(EditPatch(
                    patch_id, project_name, timeline_name, base.generation,
                    capability, (), conflict_domains, operations),
                    f"names unknown operation {operation.get('op')!r}",
                    "only measured, read-back-judged writes are operations",
                    f"use one of {sorted(OPERATIONS)}")
            span = spec.span(operation, snapshot)
            if span is None:
                _refuse(EditPatch(
                    patch_id, project_name, timeline_name, base.generation,
                    capability, (), conflict_domains, operations),
                    f"cannot find the target span for {operation!r}",
                    "an operation addresses an item held by its base",
                    "re-plan against the recorded timeline generation")
            spans.append(tuple(int(frame) for frame in span))
        patch = EditPatch(
            id=patch_id, project=project_name, timeline=timeline_name,
            base_generation=base.generation, capability=capability,
            affected_spans=tuple(spans),
            conflict_domains=conflict_domains, operations=operations,
            preconditions=preconditions, postconditions=postconditions)
        return apply_patch(patch, resolve=None, project=project,
                           timeline=timeline, store=store,
                           project_folder=project_folder)


def apply_patch(patch, *, resolve, project, timeline,
                store: shadow.ShadowStore | None = None,
                project_folder: str | None = None) -> dict:
    """Commit one patch to the live timeline; the receipt, as plain data.

    The broker's `timeline.apply_patch` job. `resolve` is the app handle
    (unused beyond the contract: the patch never switches projects),
    `project` the OPEN project, `timeline` the live handle the broker has
    made current. Raises `StalePatch` / `PatchRefused` with nothing
    written; returns a receipt whose `status` is `committed` or
    `verification_failed`.
    """
    del resolve
    patch = EditPatch.from_dict(patch)
    store = store or shadow.ShadowStore()
    project_name, timeline_id, timeline_name = shadow.timeline_key(
        project, timeline)
    if (project_name, timeline_name) != (patch.project, patch.timeline):
        _refuse(patch, f"addresses {patch.project!r}/{patch.timeline!r} but "
                       f"was handed {project_name!r}/{timeline_name!r}",
                "a Resolve project and timeline are addressed by EXACT name "
                "(AGENTS.md 5)",
                "hand apply_patch the timeline the patch names")
    head = store.head(project_name, timeline_id)
    if head is None:
        _refuse(patch, "targets a timeline the shadow has never read",
                "a patch is planned against a recorded generation",
                "observe the timeline first (`timeline_shadow.observe`)")
    # The id is the idempotency key: a retry of a patch already recorded
    # answers with the receipt it got, and writes nothing again.
    for gen in store.history(project_name, timeline_id):
        if gen.source == shadow.PATCH and gen.patch["id"] == patch.id:
            if gen.patch != patch.to_dict():
                _refuse(
                    patch,
                    "this id was already committed with different contents",
                    "a patch id is a durable identity for one complete patch",
                    "retry the original patch unchanged, or assign a new id")
            return _receipt_of(gen)
    if head.generation != patch.base_generation:
        from library.tools import patch_algebra
        moved, reason = patch_algebra.carry(patch, store, project_name,
                                            timeline_id)
        raise StalePatch(patch, head.generation, observed=False,
                         rebase_possible=moved is not None,
                         reason=reason or "it composes with every "
                                          "generation since")
    base = store.snapshot(head)
    validate(patch, base)

    started = time.monotonic()
    with resolve_lock.cursor_fence(project, timeline,
                                   f"apply EditPatch {patch.id}"):
        live = shadow.read_live(project, timeline)
        if shadow.snapshot_hash(live) != head.hash:
            seen = store.record(
                project=project_name, timeline_id=timeline_id,
                timeline_name=timeline_name, snapshot=live,
                source=shadow.OBSERVED, expected_head=head.generation)
            raise StalePatch(
                patch, seen.generation, observed=True, rebase_possible=False,
                reason=("the live timeline no longer matches generation "
                        f"{head.generation}: a change nobody recorded, now "
                        f"generation {seen.generation}"))
        failures = _judge(patch.preconditions, live, base)
        if failures:
            _refuse(patch, "preconditions do not hold: " + "; ".join(failures),
                    "a patch states what it assumes of its base",
                    "re-plan against the live timeline")
        handles = (_live_handles(timeline)
                   if any(OPERATIONS[op["op"]].item
                          for op in patch.operations) else {})
        ran = []
        stopped = None
        for index, op in enumerate(patch.operations):
            spec = OPERATIONS[op["op"]]
            try:
                item = handles.get(op["unique_id"]) if spec.item else None
                if spec.item and item is None:
                    raise RuntimeError(
                        f"clip {op['unique_id']!r} is not uniquely present")
                if op["op"] == "clip.set_property":
                    current = _clip(live, op["unique_id"])
                    old_value = ((current.get("transform") or {}).get(
                        op["key"]) if current is not None else None)
                    from library.tools.transform_write_log import write_scope

                    with write_scope(
                            project=project_name,
                            timeline_name=timeline_name,
                            timeline_id=timeline_id,
                            project_folder=project_folder,
                            old_value=old_value):
                        returned = spec.apply(op, timeline, item)
                elif op["op"] == "clip.smart_reframe":
                    from library.tools.transform_write_log import (
                        validate_transform_write_context, write_scope)

                    context = validate_transform_write_context(
                        item_identity={"resolve_unique_id":
                                       str(op["unique_id"])},
                        project=project_name, timeline_name=timeline_name,
                        timeline_id=timeline_id,
                        project_folder=project_folder)
                    with write_scope(**context):
                        returned = spec.apply(op, timeline, item)
                else:
                    returned = spec.apply(op, timeline, item)
            except Exception as raised:  # noqa: BLE001 - recorded, then stop
                stopped = {"index": index, "op": op["op"],
                           "raised": f"{type(raised).__name__}: {raised}"}
                break
            ran.append({"index": index, "op": op["op"],
                        "returned": bool(returned)})
            if spec.domain == "timeline_structure":
                handles = _live_handles(timeline)
        after = shadow.read_live(project, timeline)
    from library.tools import transform_write_log

    # SmartReframe is Resolve's equivalent transform setter. Its result
    # is already in the post-write snapshot, so record the values that
    # actually changed without making another Resolve call.
    for row in ran:
        op = patch.operations[row["index"]]
        if op["op"] != "clip.smart_reframe":
            continue
        before = op["before"]
        current = _clip(after, op["unique_id"])
        if current is None:
            continue
        held = current["transform"]
        for key in SMART_REFRAME_KEYS:
            old_value, new_value = before.get(key), held.get(key)
            if _same_value(old_value, new_value):
                continue
            transform_write_log.record_transform_write(
                key, new_value, old_value=old_value,
                item_identity={"resolve_unique_id": str(op["unique_id"])},
                project=project_name, timeline_name=timeline_name,
                timeline_id=timeline_id, project_folder=project_folder,
                caller_site={"file": "library/tools/edit_patch.py",
                             "function": "_apply_smart_reframe"},
                write_kind="SmartReframe")
    hold_seconds = time.monotonic() - started

    judged = [{"index": r["index"], "op": r["op"], "returned": r["returned"],
               "failure": OPERATIONS[r["op"]].verify(
                   patch.operations[r["index"]], after)}
              for r in ran]
    post_failures = _judge(patch.postconditions, after, base)
    ok = (stopped is None and not post_failures
          and not any(j["failure"] for j in judged))
    receipt = {
        "patch_id": patch.id,
        "status": "committed" if ok else "verification_failed",
        "base_generation": head.generation,
        "operations": judged,
        "stopped": stopped,
        "postcondition_failures": post_failures,
        "exclusive_hold_seconds": round(hold_seconds, 4),
    }
    return _receipt_of(store.record(
        project=project_name, timeline_id=timeline_id,
        timeline_name=timeline_name, snapshot=after, source=shadow.PATCH,
        expected_head=head.generation, patch=patch.to_dict(),
        receipt=receipt))


def _receipt_of(generation: shadow.Generation) -> dict:
    return {**generation.receipt, "generation": generation.generation,
            "fingerprint": generation.hash,
            "readback": generation.summary()}
