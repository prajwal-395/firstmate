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

`OPERATIONS` is a VIEW over the edit algebra (`library/tools/
edit_algebra.py`): every algebra op with a native apply, projected to an
`Operation`. The algebra owns the union vocabulary, the inverse/span/
verify declarations and the five existing vocabularies as views; this
module owns the patch transaction - validation against the base, the
exclusive hold, the read-back judgement and the generation receipt.

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

import json
import os
import time
from dataclasses import asdict, dataclass

from library.tools import (
    edit_algebra,
    edit_backend,
    protocol_conformance,
    resolve_lock,
    transaction_receipt,
)
from library.tools import timeline_shadow as shadow
from library.tools.edit_algebra import (
    FLOAT_TOLERANCE,
    SMART_REFRAME_KEYS,
    _clip,
    _clip_marker,
    _live_handles,
    _matches,
    _timeline_marker,
)
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
    "Pan", "Tilt", "Scaling", "ZoomX", "ZoomY", "ZoomGang", "RotationAngle",
    "AnchorPointX", "AnchorPointY", "Pitch", "Yaw", "FlipX", "FlipY",
    "CropLeft", "CropRight", "CropTop", "CropBottom", "CropSoftness",
    "CropRetain", "Opacity",
})

#: Mode keys `clip.set_property` may write: Resolve modes that are not
#: magnitudes. `Scaling` is the scaling MODE (1 = Crop - native pixels,
#: centred), not a magnification. The tight-box Pan/Tilt are computed
#: assuming Crop, so a caption clip without Scaling=1 takes the
#: project's default scaling and lands at the wrong size and place.
MODE_KEYS = frozenset({
    "Scaling",
})


class PatchRefused(RenRefusal):
    """The patch cannot be applied as stated. Nothing was written."""

    USER_MESSAGE = ("I couldn't apply this edit as prepared, so nothing "
                    "on the timeline changed. Resolve the issue below, "
                    "then ask me to try again.")


class StalePatch(PatchRefused):
    """The patch was planned on a generation that is no longer the head."""

    RECOVERY_CLASS = "needs_question"

    def __init__(self, patch: EditPatch, head: int, *, observed: bool,
                 rebase_possible: bool, reason: str,
                 manual_edit_recorded: bool = False) -> None:
        self.head = head
        self.observed = observed
        self.rebase_possible = rebase_possible
        self.manual_edit_recorded = manual_edit_recorded
        self.recovery = transaction_receipt.recovery(
            "stale_generation", reason=reason)
        user_message = (
            "The timeline changed since I prepared this edit. Your recent "
            "work is preserved, and I refreshed my read. Should I apply "
            "this change to the updated cut?")
        if manual_edit_recorded:
            user_message = (
                "I found an unrecorded Resolve edit and kept it intact. "
                "Its intent is unknown, so tell me what you meant before "
                "I rebase this change.")
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
                 f"{patch.timeline!r}`)"),
            user_message=user_message,
            recovery_class="needs_question")


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
    # Project Model head used while constructing this edit. When a project
    # model exists, live writes require this pin and refuse if it has moved.
    expected_model_revision: int | None = None

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
            base_git_sha=data.get("base_git_sha", ""),
            expected_model_revision=data.get("expected_model_revision"))

    def to_dict(self) -> dict:
        out = asdict(self)
        out["affected_spans"] = [list(s) for s in self.affected_spans]
        for key in ("conflict_domains", "operations", "preconditions",
                    "postconditions"):
            out[key] = list(out[key])
        # The serialization boundary: the patch is about to be written or
        # sent. Log in production (refusing would abort a live render
        # mid-apply), refuse in tests/dev.
        protocol_conformance.validate(out, "edit-patch")
        return out


def _intent_form(operations) -> list:
    """The identity form of operations for idempotency comparison.

    Owned by ``transaction_orchestration`` (VC-12); kept here as a thin
    alias so the retry comparison stays one implementation.
    """
    from library.tools import transaction_orchestration as _orch

    return _orch.intent_form(operations)


def _refuse(patch: EditPatch, what: str, why: str, fix: str):
    raise PatchRefused(what=f"patch {patch.id!r}: {what}", why=why, fix=fix)


# ── Operations: the patch-executor view over the edit algebra ──────────
#
# Every algebra op with a native apply, projected to an `Operation`. The
# algebra owns the declarations (inverse/span/verify); this view is what
# EditPatch schedules, composes and judges.


def _operations() -> dict:
    return {op_id: op.patch_operation()
            for op_id, op in edit_algebra.ALGEBRA.items()
            if op.apply is not None}


OPERATIONS: dict = _operations()


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


def _same_value(got, want):
    if (isinstance(want, (int, float))
            and isinstance(got, (int, float))):
        return abs(float(got) - float(want)) <= FLOAT_TOLERANCE
    return got == want


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
    if not conditions:
        return []
    rust = edit_backend.rust()
    if rust is not None:
        return rust.judge_conditions(conditions, snap, base)
    return [f"{c['kind']}: {failure}" for c in conditions
            for failure in [CONDITIONS[c["kind"]][1](c, snap, base)]
            if failure]


# ── Validation: FREE, against the base snapshot ───────────────────────


def _covered(span, spans) -> bool:
    return any(s[0] <= span[0] and span[1] <= s[1] for s in spans)


def validate(patch: EditPatch, base: dict) -> None:
    """Refuse a patch whose declarations are not true of what it does.

    The capability-registry checks stay Python (they read the registry);
    the semantic checks - domains, declared operations, transform keys,
    smart-reframe keys, span coverage - go through the backend switch.
    """
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
    rust = edit_backend.rust()
    if rust is not None:
        semantics = {
            "operations": list(declared.operations),
            "conflict_domains": list(declared.conflict_domains),
        }
        from library.tools.perf_ledger import count_call, span
        count_call("edit_core_calls")
        with span("edit_core_rust", backend="rust", calls=1):
            reason = rust.validate_patch(patch.to_dict(), base, semantics)
        if reason is not None:
            _refuse(patch, reason,
                    "a declaration must be true (AGENTS.md 3)",
                    "widen affected_spans to cover the frames the "
                    "operations touch")
        return
    _validate_python(patch, base, declared)


def prepare_patch(patch, base: dict) -> EditPatch:
    """Compile and validate a patch against its immutable base snapshot.

    This is the offline preparation boundary. It performs no Resolve calls
    and is safe to inspect or retry before physical execution.
    """
    patch = EditPatch.from_dict(patch)
    validate(patch, base)
    return patch


def _validate_python(patch: EditPatch, base: dict, declared) -> None:
    """The semantic checks, in Python (the selectable fallback)."""
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
    insert_spans: dict = {}
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
        if (op["op"] == "clip.set_property"
                and op["key"] not in TRANSFORM_KEYS
                and op["key"] not in MODE_KEYS):
            _refuse(patch, f"operation {index} writes property {op['key']!r}",
                    "clip.set_property is the picture transform and its modes only",
                    f"use one of {sorted(TRANSFORM_KEYS | MODE_KEYS)}")
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
        if op["op"] == "clip.insert":
            span = spec.span(op, base)
            insert_spans[op["unique_id"]] = span
        elif op.get("unique_id") in insert_spans:
            span = insert_spans[op["unique_id"]]
        else:
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


def inverse_patch(patch, *, before: dict, after: dict,
                  base_generation: int, patch_id: str) -> EditPatch:
    """Build a conservative, snapshot-backed inverse EditPatch.

    Reuse the feedback path's measured inverse rules, which cover only
    marker and local clip-property writes. Other operations need a semantic
    replay or native archive and are refused here rather than guessed.
    """
    from library.tools import feedback_patch_apply

    patch = EditPatch.from_dict(patch)
    try:
        operations = feedback_patch_apply.inverse_operations(
            patch.operations, before)
    except feedback_patch_apply.UnrestorableOp as refused:
        raise PatchRefused(
            what=f"patch {patch.id!r} has no safe inverse EditPatch",
            why=str(refused),
            fix="use semantic replay or a verified native recovery archive") \
            from refused
    spans = []
    for operation in operations:
        spec = OPERATIONS.get(operation.get("op"))
        span = spec.span(operation, after) if spec else None
        if span is None:
            raise PatchRefused(
                what=f"inverse operation {operation!r} has no measured span",
                why="an inverse must declare its full affected range",
                fix="use semantic replay or a verified native recovery archive")
        spans.append(tuple(int(frame) for frame in span))
    domains = feedback_patch_apply._conflict_domains(operations)
    preconditions = []
    for operation in patch.operations:
        op_id = operation["op"]
        if op_id == "clip.set_property":
            preconditions.append({
                "kind": "clip_transform_equals",
                "unique_id": str(operation["unique_id"]),
                "properties": {str(operation["key"]): operation["value"]}})
        elif op_id in {"marker.add", "marker.delete"}:
            preconditions.append({
                "kind": "marker_present" if op_id == "marker.add"
                else "marker_absent",
                **{key: operation[key] for key in (
                    "frame", "color", "name", "note", "duration",
                    "custom_data") if key in operation}})
        elif op_id in {"clip_marker.add", "clip_marker.delete"}:
            preconditions.append({
                "kind": ("clip_marker_present" if op_id == "clip_marker.add"
                         else "clip_marker_absent"),
                **{key: operation[key] for key in (
                    "unique_id", "frame", "color", "name", "note",
                    "duration", "custom_data") if key in operation}})
    return EditPatch(
        id=patch_id, project=patch.project, timeline=patch.timeline,
        base_generation=int(base_generation), capability=patch.capability,
        affected_spans=tuple(spans), conflict_domains=domains,
        operations=tuple(operations), preconditions=tuple(preconditions),
        expected_model_revision=patch.expected_model_revision)


def apply_live_patch(*, project, timeline, capability: str,
                     operations, conflict_domains,
                     idempotency_key: str, preconditions=(),
                     postconditions=(),
                     acceptance_evidence: dict | None = None,
                     store: shadow.ShadowStore | None = None,
                     project_folder: str | None = None,
                     model_revision: int | None = None) -> dict:
    """Build and commit a patch while the caller already owns Resolve.

    Entry points which already hold a Resolve lease use this seam to
    give one in-process write the same generation checks and read-back
    receipt as `timeline.apply_patch`. Remote callers submit that broker
    job instead of holding a lease here.

    `model_revision` pins the Project Model revision the patch is
    planned against. A caller placing many patches in one pass (a reel
    build) loads it once and passes it to every patch instead of
    re-loading - and re-validating - the whole model per patch
    (measured 2026-10-17: ~10 s per load on the live model, twice per
    patch). Left out, the revision is loaded here as before. A pinned
    revision skips the re-read, so a model that advances mid-pass is
    noticed only by the next fresh load - the pin is for one pass
    under one hold, not across builds.
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
    from library.tools import transaction_orchestration as _orch

    store = store or shadow.ShadowStore()
    project_name, timeline_id, timeline_name = shadow.timeline_key(
        project, timeline)
    patch_id = _orch.derive_patch_id(
        project_name, timeline_id, idempotency_key)
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
    generation = store.patch_generation(project_name, timeline_id, patch_id)
    if generation is not None:
        prior = generation.patch
        prior_intent = {key: prior[key] for key in intent}
        prior_intent["operations"] = _intent_form(
            prior.get("operations") or ())
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

    if model_revision is None and project_folder:
        from pathlib import Path

        from library.tools import project_model_store
        try:
            model_revision = project_model_store.project_model_revision(
                Path(project_folder))
        except FileNotFoundError:
            model_revision = None

    with resolve_lock.cursor_excursion(
            project, timeline, f"prepare EditPatch {patch_id}"):
        base = shadow.observe(project, timeline, store)
        snapshot = store.snapshot(base)
        spans_by_index = {}
        insert_spans: dict = {}
        insert_ops = [op for op in operations if op.get("op") == "clip.insert"]
        planned_inserts = iter(edit_algebra.spans(insert_ops, snapshot)
                               if insert_ops else ())
        pending_span_indices = []
        pending_span_ops = []
        for index, operation in enumerate(operations):
            spec = OPERATIONS.get(operation.get("op"))
            if spec is None:
                continue
            if operation.get("op") == "clip.insert":
                span = next(planned_inserts)
                insert_spans[operation["unique_id"]] = span
            elif (operation.get("unique_id") in insert_spans):
                span = insert_spans[operation["unique_id"]]
            else:
                pending_span_indices.append(index)
                pending_span_ops.append(operation)
                continue
            spans_by_index[index] = span

        if pending_span_ops:
            spans_by_index.update(zip(
                pending_span_indices,
                edit_algebra.spans(pending_span_ops, snapshot)))
        spans = []
        for index, operation in enumerate(operations):
            if OPERATIONS.get(operation.get("op")) is None:
                _refuse(EditPatch(
                    patch_id, project_name, timeline_name, base.generation,
                    capability, (), conflict_domains, operations),
                    f"names unknown operation {operation.get('op')!r}",
                    "only measured, read-back-judged writes are operations",
                    f"use one of {sorted(OPERATIONS)}")
            span = spans_by_index[index]
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
            preconditions=preconditions, postconditions=postconditions,
            expected_model_revision=model_revision)
        return apply_patch(patch, resolve=None, project=project,
                           timeline=timeline, store=store,
                           project_folder=project_folder,
                           model_revision=model_revision,
                           acceptance_evidence=acceptance_evidence)


def apply_patch(patch, *, resolve, project, timeline,
                store: shadow.ShadowStore | None = None,
                project_folder: str | None = None,
                model_revision: int | None = None,
                acceptance_evidence: dict | None = None) -> dict:
    """Commit one patch to the live timeline; the receipt, as plain data.

    The broker's `timeline.apply_patch` job. `resolve` is the app handle
    (unused beyond the contract: the patch never switches projects),
    `project` the OPEN project, `timeline` the live handle the broker has
    made current. Raises `StalePatch` / `PatchRefused` with nothing
    written; returns a receipt whose `status` is `committed` or
    `verification_failed`.

    `model_revision` pins the Project Model head the patch is checked
    against; left out, it is loaded here as before (see
    `apply_live_patch` for why a caller passes it).
    """
    del resolve
    patch = EditPatch.from_dict(patch)
    # The pre-apply operations, snapshotted before the apply mutates
    # them (clip.insert writes the Resolve-assigned unique id back onto
    # the op).  The recorded patch carries THIS form so a retry's intent
    # compares equal to what was recorded - the post-apply unique id is
    # a result, not part of the patch's identity.
    recorded_operations = tuple(dict(op) for op in patch.operations)
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
    gen = store.patch_generation(project_name, timeline_id, patch.id)
    if gen is not None:
        recorded = {**gen.patch,
                     "operations": _intent_form(
                         gen.patch.get("operations") or ())}
        attempted = {**patch.to_dict(),
                     "operations": _intent_form(patch.operations)}
        if recorded != attempted:
            _refuse(
                patch,
                "this id was already committed with different contents",
                "a patch id is a durable identity for one complete patch",
                "retry the original patch unchanged, or assign a new id")
        return _receipt_of(gen)
    if project_folder:
        from pathlib import Path

        from library.tools import project_model_store
        try:
            model_revision = project_model_store.project_model_revision(
                Path(project_folder))
        except FileNotFoundError:
            # Projects predating the canonical model remain on the legacy
            # path. Once a model exists, its revision is mandatory.
            model_revision = None
    if model_revision is not None:
        if (type(patch.expected_model_revision) is not int
                or patch.expected_model_revision < 0):
            _refuse(
                patch, "has an invalid Project Model revision",
                "the expected revision must be a non-negative integer",
                "rebuild the patch against the current Project Model")
        if model_revision != patch.expected_model_revision:
            _refuse(
                patch, f"was planned on Project Model revision "
                f"{patch.expected_model_revision}, but the head is "
                f"{model_revision}",
                "a stale edit cannot overwrite newer editorial state",
                "rebuild the patch against the current Project Model")
    if head.generation != patch.base_generation:
        from library.tools import patch_algebra
        moved, reason = patch_algebra.carry(patch, store, project_name,
                                            timeline_id)
        raise StalePatch(patch, head.generation, observed=False,
                         rebase_possible=moved is not None,
                         reason=reason or "it composes with every "
                                           "generation since")
    base = store.snapshot(head)
    patch = prepare_patch(patch, base)

    started = time.monotonic()
    from library.tools import perf_ledger
    with resolve_lock.cursor_fence(project, timeline,
                                   f"apply EditPatch {patch.id}"), \
            edit_algebra.insertion_sources():
        with perf_ledger.span(
                "resolve_snapshot_read", snapshot_role="pre_write",
                snapshot_count=1, batch=True):
            live = shadow.read_live(project, timeline)
        if shadow.snapshot_hash(live) != head.hash:
            manual_edit_recorded = False
            if project_folder:
                try:
                    from library.tools import reel_edit_history
                    from library.tools.reel_read import rows_of
                    from library.tools.versions.rounds import digest_rows

                    review_dir = os.path.join(
                        project_folder, "pipeline_output", "review")
                    live_digest = shadow.snapshot_hash(live)
                    rows_digest = digest_rows(rows_of(live))
                    manual_edit = reel_edit_history.detect_manual_edit(
                        review_dir, timeline_name, rows_digest,
                        summary=("unattributed Resolve edit observed before "
                                 f"transaction {patch.id}; intent unknown"))
                    if manual_edit is None:
                        manual_edit = reel_edit_history.record_manual_edit(
                            review_dir, timeline_name,
                            summary=("unattributed Resolve state change not "
                                     "represented in clip rows; intent unknown; "
                                     f"snapshot {head.hash} -> {live_digest}"),
                            rows_digest_before=rows_digest,
                            rows_digest_after=rows_digest)
                    manual_edit_recorded = manual_edit is not None
                except Exception as exc:
                    raise PatchRefused(
                        what=(f"patch {patch.id!r} found a live timeline "
                              "change it could not record"),
                        why=(f"the live timeline differs from recorded "
                             f"generation {head.generation}, and manual-edit "
                             f"history could not be updated: {exc}"),
                        fix=("preserve the current Resolve timeline, repair "
                             "the review history, and re-plan against the "
                             "observed state"),
                        user_message=(
                            "I found a Resolve edit but couldn't record it, "
                            "so I left the timeline untouched. Please keep "
                            "the edit and repair its review history before "
                            "trying again."),
                        recovery_class="needs_question") from exc
            if project_folder:
                from library.tools import event_log

                pending = transaction_receipt.interrupted(
                    event_log.read_events(project_folder))
                prior = next((row for row in pending
                              if row["transaction_id"] == patch.id), None)
                if prior:
                    _emit_transaction_stage(
                        project_folder, patch, "recovery_required",
                        prior_transaction=prior,
                        actual_state=live, required=False)
            seen = store.record(
                project=project_name, timeline_id=timeline_id,
                timeline_name=timeline_name, snapshot=live,
                source=shadow.OBSERVED, expected_head=head.generation)
            raise StalePatch(
                patch, seen.generation, observed=True, rebase_possible=False,
                reason=("the live timeline no longer matches generation "
                        f"{head.generation}: a change nobody recorded, now "
                        f"generation {seen.generation}"
                        + ("; its manual-edit history entry has unknown "
                           "intent" if manual_edit_recorded else "")),
                manual_edit_recorded=manual_edit_recorded)
        failures = _judge(patch.preconditions, live, base)
        if failures:
            _refuse(patch, "preconditions do not hold: " + "; ".join(failures),
                    "a patch states what it assumes of its base",
                    "re-plan against the live timeline")
        # This durable intent is the recovery anchor: it must reach the
        # existing project event journal before the first Resolve write.
        journaled = _emit_transaction_stage(
            project_folder, patch, "prepared", base_generation=head.generation,
            expected_state={"operations": list(patch.operations),
                            "postconditions": list(patch.postconditions)},
            required=bool(project_folder))
        with perf_ledger.span(
                "resolve_mutation_batch", batch=True,
                operation_count=len(patch.operations),
                timeline_snapshot_count=1):
            ran, stopped, id_map, after = execute_physical_patch(
                patch, live=live, timeline=timeline, project=project,
                project_name=project_name, timeline_id=timeline_id,
                timeline_name=timeline_name, project_folder=project_folder)
        if stopped and stopped.get("failure_type") == \
                "post_write_readback_failed":
            observed = store.record(
                project=project_name, timeline_id=timeline_id,
                timeline_name=timeline_name, snapshot=after,
                source=shadow.OBSERVED, expected_head=head.generation)
            _emit_transaction_stage(
                project_folder, patch, "recovery_required",
                actual_state=after, applied_operations=ran,
                failure=stopped["raised"], observed_generation=observed.generation,
                required=False)
            raise PatchRefused(
                what=(f"patch {patch.id!r} wrote to {timeline_name!r}, but "
                      "its first post-write read-back failed"),
                why=(f"Resolve was re-read and the observed state was saved "
                     f"as generation {observed.generation}; the patch result "
                     "needs review before another edit"),
                fix=("inspect the observed timeline generation and decide "
                     "whether to keep or reverse the applied edit"),
                user_message=("The edit may have landed. I saved the current "
                              "timeline state for review before continuing."),
                recovery_class="needs_question")
    journal_errors = []
    try:
        observed_journaled = _emit_transaction_stage(
            project_folder, patch, "observed", actual_state=after,
            applied_operations=ran, stopped=stopped, required=False)
        if project_folder and not observed_journaled:
            journal_errors.append("could not persist the observed timeline state")
    except OSError as exc:
        journal_errors.append(str(exc))
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
                caller_site={"file": "library/tools/edit_algebra.py",
                             "function": "_apply_smart_reframe"},
                write_kind="SmartReframe")
    hold_seconds = time.monotonic() - started

    receipt = verify_execution(
        patch, base=base, head_generation=head.generation, ran=ran,
        id_map=id_map, after=after, stopped=stopped,
        hold_seconds=hold_seconds,
        acceptance_evidence=acceptance_evidence,
        journaled=journaled, journal_errors=journal_errors)
    try:
        verified_journaled = _emit_transaction_stage(
            project_folder, patch, "verified", status=receipt["status"],
            stopped=stopped, actual_state=after,
            acceptance_contracts=receipt["acceptance_contracts"],
            required=False)
        if project_folder and not verified_journaled:
            receipt["journal"]["errors"].append("could not persist verification outcome")
    except OSError as exc:
        receipt["journal"]["errors"].append(str(exc))
    return commit_execution(
        patch, receipt=receipt, snapshot=after, store=store,
        project_name=project_name, timeline_id=timeline_id,
        timeline_name=timeline_name, recorded_operations=recorded_operations,
        project_folder=project_folder, model_revision=model_revision)


def execute_physical_patch(patch: EditPatch, *, live: dict, timeline,
                           project, project_name: str, timeline_id: str,
                           timeline_name: str,
                           project_folder: str | None) -> tuple:
    """Apply the prepared operations once and return their read-back.

    The caller holds the Resolve cursor fence and has already checked the
    generation and preconditions. The operation list is one physical batch.
    """
    handles = (_live_handles(timeline)
               if any(OPERATIONS[op["op"]].item
                      for op in patch.operations) else {})
    # Whether any op AFTER position i needs item handles. A
    # `timeline_structure` write re-reads the rows below, and for a
    # pure-placement patch (dozens of `clip.insert`, no addressing
    # op) every one of those re-reads is waste: each walks the whole
    # timeline and the patch pays O(n^2) Resolve calls. Measured on
    # a one-reel build (2026-10-17): per-insert patches re-read per
    # insert, and even one batched patch re-walked per insert.
    later_needs_handles = [False] * (len(patch.operations) + 1)
    for _rev in range(len(patch.operations) - 1, -1, -1):
        later_needs_handles[_rev] = (
            later_needs_handles[_rev + 1]
            or OPERATIONS[patch.operations[_rev]["op"]].item)
    id_map: dict = {}
    handles_dirty = False
    newest_insert = None
    ran = []
    stopped = None
    for index, op in enumerate(patch.operations):
        spec = OPERATIONS[op["op"]]
        try:
            applying_journaled = _emit_transaction_stage(
                project_folder, patch, "applying", index=index,
                op=op["op"], required=False)
        except OSError as raised:
            stopped = {"index": index, "op": op["op"],
                       "raised": f"transaction journal: {raised}",
                       "failure_type": "journal_write_failed"}
            break
        if project_folder and not applying_journaled:
            stopped = {"index": index, "op": op["op"],
                       "raised": "transaction journal did not record applying stage",
                       "failure_type": "journal_write_failed"}
            break
        try:
            if spec.item:
                resolved = id_map.get(op["unique_id"], op["unique_id"])
                if handles_dirty and resolved != newest_insert:
                    # An append may invalidate earlier item handles.
                    # Refresh only when a later op actually needs one.
                    handles = _live_handles(timeline)
                    handles_dirty = False
                item = handles.get(resolved)
            else:
                item = None
            if spec.item and item is None:
                raise RuntimeError(
                    f"clip {op['unique_id']!r} is not uniquely present")
            if op["op"] in ("clip.set_property", "picture.keyframe"):
                current = _clip(live, resolved)
                prop = op["key"] if op["op"] == "clip.set_property" \
                    else op["param"]
                old_value = ((current.get("transform") or {}).get(
                    prop) if current is not None else None)
                from library.tools.transform_write_log import write_scope

                with write_scope(
                        project=project_name,
                        timeline_name=timeline_name,
                        timeline_id=timeline_id,
                        project_folder=project_folder,
                        old_value=old_value):
                    returned = spec.apply(op, timeline, item, project)
            elif op["op"] == "clip.smart_reframe":
                from library.tools.transform_write_log import (
                    validate_transform_write_context,
                    write_scope,
                )

                context = validate_transform_write_context(
                    item_identity={"resolve_unique_id":
                                   str(resolved)},
                    project=project_name, timeline_name=timeline_name,
                    timeline_id=timeline_id,
                    project_folder=project_folder)
                with write_scope(**context):
                    returned = spec.apply(op, timeline, item, project)
            else:
                returned = spec.apply(op, timeline, item, project)
        except Exception as raised:  # noqa: BLE001 - recorded, then stop
            stopped = {"index": index, "op": op["op"],
                       "raised": f"{type(raised).__name__}: {raised}"}
            break
        ran.append({"index": index, "op": op["op"],
                    "returned": bool(returned)})
        try:
            applied_journaled = _emit_transaction_stage(
                project_folder, patch, "operation_applied", index=index,
                op=op["op"], returned=bool(returned), required=False)
        except OSError as raised:
            stopped = {"index": index, "op": op["op"],
                       "raised": f"transaction journal: {raised}",
                       "failure_type": "journal_write_failed"}
            break
        if project_folder and not applied_journaled:
            stopped = {"index": index, "op": op["op"],
                       "raised": "transaction journal did not record applied operation",
                       "failure_type": "journal_write_failed"}
            break
        if op["op"] == "clip.insert":
            id_map[op["client_unique_id"]] = op["unique_id"]
            # Append returns the newly assigned handle. Re-listing
            # every row after every insert made a batch quadratic.
            handles[op["unique_id"]] = returned
            newest_insert = op["unique_id"]
            handles_dirty = True
        elif (spec.domain == "timeline_structure"
              and later_needs_handles[index + 1]):
            handles = _live_handles(timeline)
            handles_dirty = False
    try:
        from library.tools import perf_ledger
        with perf_ledger.span(
                "resolve_snapshot_read", snapshot_role="post_write",
                snapshot_count=1, batch=True):
            after = shadow.read_live(project, timeline)
    except Exception as first_readback_error:  # noqa: BLE001
        try:
            with perf_ledger.span(
                    "resolve_snapshot_read", snapshot_role="post_write_retry",
                    snapshot_count=1, batch=True):
                after = shadow.read_live(project, timeline)
        except Exception as retry_error:
            raise PatchRefused(
                what=(f"patch {patch.id!r} may have written to "
                      f"{timeline_name!r}, but Resolve could not be read "
                      "after the write"),
                why=(f"the first read-back failed with "
                     f"{type(first_readback_error).__name__}: "
                     f"{first_readback_error}; the recovery read also failed "
                     f"with {type(retry_error).__name__}: {retry_error}"),
                fix=("inspect the timeline in Resolve and record its current "
                     "state before retrying the patch"),
                user_message=("The edit may have landed, and I could not "
                              "read the timeline to confirm it. Inspect the "
                              "timeline before continuing."),
                recovery_class="needs_question") from retry_error
        stopped = {
            "index": (ran[-1]["index"] if ran else -1),
            "op": (ran[-1]["op"] if ran else "read_back"),
            "raised": (f"{type(first_readback_error).__name__}: "
                       f"{first_readback_error}"),
            "failure_type": "post_write_readback_failed",
        }
    return ran, stopped, id_map, after


def verify_execution(patch: EditPatch, *, base: dict, head_generation: int,
                     ran: list, id_map: dict, after: dict,
                     stopped: dict | None, hold_seconds: float,
                     acceptance_evidence: dict | None = None,
                     journaled: bool = False,
                     journal_errors: list | None = None) -> dict:
    """Judge physical execution only from the observed read-back.

    VC-12: the judgement is one shared pass in
    ``transaction_orchestration.verify_readback`` (single ``verifies``
    call, single unrelated-change scan, same receipt keys).
    """
    from library.tools import transaction_orchestration as _orch

    judged_receipt = _orch.verify_readback(
        list(patch.operations), ran, after, base=base, id_map=id_map,
        affected_spans=patch.affected_spans,
        postconditions=list(patch.postconditions),
        acceptance_evidence=acceptance_evidence, stopped=stopped,
        hold_seconds=hold_seconds, head_generation=head_generation,
        patch_id=patch.id, project=patch.project,
        timeline=patch.timeline, backend="edit_patch",
        journaled=journaled, journal_errors=journal_errors)
    # The shared pass returns the full receipt body except the requested
    # count and expected state, which are properties of the patch rather
    # than of what ran: restore the patch-level values verbatim.
    judged_receipt["requested_operation_count"] = len(patch.operations)
    judged_receipt["expected_state"] = {
        "operations": list(patch.operations),
        "postconditions": list(patch.postconditions)}
    return judged_receipt


def _emit_transaction_stage(project_folder, patch: EditPatch, stage: str,
                            *, required: bool, **fields) -> bool:
    """Append a lifecycle event to the existing per-project event log."""
    if not project_folder:
        return False
    from library.tools import event_log

    try:
        payload = {
            "transaction_id": patch.id,
            "idempotency_key": patch.id,
            "project": patch.project,
            "timeline": patch.timeline,
            "stage": stage,
            "base_generation": patch.base_generation,
            "operations": list(patch.operations),
            **fields,
        }
        event_log.emit(project_folder, event_log.EDIT_TRANSACTION_STAGE,
                       **payload)
    except OSError:
        if required:
            raise
        return False
    return True


def _unrelated_clip_changes(patch: EditPatch, before: dict, after: dict,
                            id_map: dict) -> list:
    """Report changed clips outside the transaction's declared spans/targets."""
    def rows(snapshot):
        return {str(clip["unique_id"]): clip
                for track in snapshot["tracks"] for clip in track["clips"]}

    old, new = rows(before), rows(after)
    targets = {str(op[key]) for op in patch.operations
               for key in ("unique_id", "client_unique_id") if op.get(key)}
    targets.update(str(actual) for actual in id_map.values())
    affected = patch.affected_spans
    changed = []
    for unique_id in sorted(old.keys() | new.keys()):
        if old.get(unique_id) == new.get(unique_id) or unique_id in targets:
            continue
        entry = old.get(unique_id) or new.get(unique_id)
        clip = entry
        start = int(clip["record_in"])
        end = int(clip["record_out"])
        if any(start < span_end and span_start < end
               for span_start, span_end in affected):
            continue
        changed.append({"unique_id": unique_id, "before": old.get(unique_id),
                        "after": new.get(unique_id)})
    return changed


def commit_execution(patch: EditPatch, *, receipt: dict, snapshot: dict,
                     store: shadow.ShadowStore, project_name: str,
                     timeline_id: str, timeline_name: str,
                     recorded_operations: tuple,
                     project_folder: str | None,
                     model_revision: int | None) -> dict:
    """Persist the verified observation and publish its transaction receipt."""
    generation = store.record(
        project=project_name, timeline_id=timeline_id,
        timeline_name=timeline_name, snapshot=snapshot, source=shadow.PATCH,
        expected_head=receipt["base_generation"],
        patch={**patch.to_dict(), "operations": recorded_operations},
        receipt=receipt)
    model_commit = {"status": "not_required", "revision": None}
    if model_revision is not None:
        from library.tools import project_model_editorial
        try:
            revision = project_model_editorial.commit_edit_readback(
                project_folder, patch=patch,
                readback=store.snapshot(generation), execution=receipt,
                generation=generation.generation,
                expected_revision=model_revision)
            model_commit = {"status": "committed", "revision": revision}
        except Exception as exc:  # noqa: BLE001 - durable Resolve write remains recorded
            model_commit = {"status": "failed",
                            "error": f"{type(exc).__name__}: {exc}"}
    receipt = {**receipt, "resolve_write_status": receipt["status"],
               "project_model_commit": model_commit,
               "status": ("project_model_commit_failed"
                          if model_commit["status"] == "failed"
                          else receipt["status"]),
               "generation": generation.generation,
               "fingerprint": generation.hash,
               "readback": generation.summary()}
    from library.tools import transaction_orchestration as _orch

    receipt = _orch.standardize_receipt(receipt)
    generation = store.update_receipt(generation, receipt)
    _emit_transaction_stage(
        project_folder, patch,
        "committed" if receipt["committed"]["status"] == "committed" else "recorded",
        generation=generation.generation,
        project_model_commit=model_commit, status=receipt["status"],
        required=False)
    _emit_patch_event(project_folder, receipt, generation.generation)
    return receipt


def _emit_patch_event(project_folder: str | None, receipt: dict,
                      generation: int) -> None:
    """Emit one patch event into the project's event log, or skip.

    The patch is the act, the event log is the record of it: a failure
    to emit must never take down a commit, the same rule the lease
    layer (`resource_leases._emit`) holds. A patch applied without a
    project folder leaves no event, which is the honest answer - the
    log is per-project and the caller named none. An idempotent retry
    emits nothing: the retry writes nothing new, so there is no new
    operation to record.
    """
    if not project_folder:
        return
    try:
        from library.tools import event_log
        event_log.emit(project_folder, event_log.TIMELINE_OPERATIONS,
                       patch_id=receipt["patch_id"],
                       status=receipt["status"],
                       operation_count=len(receipt["operations"]),
                       generation=generation)
    except OSError:
        pass


def _receipt_of(generation: shadow.Generation) -> dict:
    from library.tools import transaction_orchestration as _orch

    return _orch.standardize_receipt({
        **generation.receipt, "generation": generation.generation,
        "fingerprint": generation.hash,
        "readback": generation.summary()})
