"""The edit algebra: one NLE-neutral vocabulary of reversible timeline operations.

The gap this closes (gap map C-01): five disjoint op vocabularies, none
complete, and the review's algebra terms join/roll/slip/slide/link-unlink/
compound existed nowhere. This module owns the UNION as a single
enumeration. Every editor instruction maps onto these ids; every
executor (Resolve first) reads them.

What each op carries
--------------------
id, conflict domain, temporal effect (local/ripple/global), merge
semantics, required params, a span function, an apply, a read-back
verify, a write key, and an inverse. The shape of the patch-layer view
is `Operation` (re-exported by `edit_patch`); the algebra entry itself
is `AlgebraOp`, which adds the inverse.

    span    (op, base_snapshot) -> (start, end) | None  the frames it touches
    apply   (op, timeline, item_handle | None, project=None) -> returned
            the native write. `project` is the OPEN Resolve project handle:
            the media pool is reached `project.GetMediaPool()` (a Timeline
            carries no such route - measured, the Reel 13 card build).
    verify  (op, after_snapshot) -> failure text | None   the read-back judge
    key     (op) -> what it writes; keys MEET on a prefix

An inverse is a declaration: either the algebra id that undoes the op
(paired ops name each other; value setters name themselves, the inverse
carrying the previous value) or an explicit irreversible reason. The
fidelity test in `tests/contracts/test_capabilities.py` enumerates the
algebra and fails on an op whose inverse does not restore the timeline.

The five vocabularies are views over this union
------------------------------------------------
`edit_patch.OPERATIONS` is re-expressed as the patch-executor view: every
algebra op with a native apply, projected to an `Operation`. The ledger,
plan, touch-up and captain vocabularies keep their own names; each name
resolves to an algebra id (`LEDGER_OPS`, `PLAN_OPS`, `TOUCHUP_VIEW`,
`CAPTAIN_VIEW`). A name that resolves to nothing is a vocabulary that
drifted from the algebra - the contract test fails on it.

The review's algebra terms (`REVIEW_TERMS`) map onto algebra ids. An
editor instruction using a term with no id cannot become a precise
reversible operation - the contract test fails on the missing ones.

New ops land here first; a capability then declares which algebra ops its
`PatchSemantics` permits. Resolve's API surface is one executor backend
behind it; `resolve-axi` verbs are projections, not a second algebra.

`tests/unit/resolve/test_edit_patch.py` and
`tests/contracts/test_capabilities.py`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from library.tools import edit_backend
from library.tools import timeline_shadow as shadow

#: How close a re-read float must be to what was written.
FLOAT_TOLERANCE = 1e-6
SMART_REFRAME_KEYS = ("Pan", "Tilt", "ZoomX", "ZoomY")


# ── Reading a snapshot ─────────────────────────────────────────────────
#
# The shadow's clip dicts (`reel_read` QUICK mode): unique_id, record_in,
# record_out, duration, transform, enabled, markers, track_type,
# track_index, name. Tracks: snapshot["tracks"] = [{"type", "index",
# "name", "clips"}, ...].


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


def _same_value(got, want):
    if (isinstance(want, (int, float))
            and isinstance(got, (int, float))):
        return abs(float(got) - float(want)) <= FLOAT_TOLERANCE
    return got == want


def _item_span(op, base):
    clip = _clip(base, op["unique_id"])
    return (clip["record_in"], clip["record_out"]) if clip else None


def _track(snapshot: dict, track_type: str, index: int) -> dict | None:
    for track in snapshot.get("tracks", []):
        if (track["type"] == track_type
                and int(track["index"]) == int(index)):
            return track
    return None


def _track_span(op, base):
    """The frames a track's clips occupy, or the whole timeline."""
    track = _track(base, op["track_type"], int(op["track_index"]))
    if track is None:
        return None
    clips = track.get("clips") or []
    if not clips:
        return (int(base["start_frame"]), int(base["end_frame"]))
    start = min(int(c["record_in"]) for c in clips)
    end = max(int(c["record_out"]) for c in clips)
    return (start, end)


def _timeline_span(op, base):
    return (int(base["start_frame"]), int(base["end_frame"]))


def _union_span(spans):
    spans = [s for s in spans if s is not None]
    if not spans:
        return None
    return (min(s[0] for s in spans), max(s[1] for s in spans))


# ── The inverse ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Inverse:
    """What undoes an op: an algebra id, or an explicit irreversible reason.

    `op` names the algebra id that undoes this one (paired ops name each
    other). `self_inverse` marks a value setter: the inverse is the same
    op carrying the previous value. An irreversible op carries a reason.
    """

    op: str | None = None
    self_inverse: bool = False
    reason: str = ""

    @property
    def irreversible(self) -> bool:
        return self.op is None and not self.self_inverse


def _pair(other: str, reason: str = "") -> Inverse:
    """A paired op: each undoes the other."""
    return Inverse(op=other, reason=reason)


def _self(reason: str) -> Inverse:
    """A value setter: the inverse carries the previous value."""
    return Inverse(self_inverse=True, reason=reason)


def _never(reason: str) -> Inverse:
    """Genuinely irreversible, and the reason why."""
    return Inverse(reason=reason)


# ── The op ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Operation:
    """The patch-layer view of an algebra op (re-exported by edit_patch)."""

    domain: str
    required: tuple
    span: Callable
    apply: Callable
    verify: Callable
    key: Callable
    merge: str
    item: bool = False
    temporal: str = "local"


@dataclass(frozen=True)
class AlgebraOp:
    """One operation in the union vocabulary, with its inverse declared."""

    id: str
    domain: str
    inverse: Inverse
    required: tuple = ()
    span: Callable | None = None
    apply: Callable | None = None
    verify: Callable | None = None
    key: Callable | None = None
    merge: str = "replace"
    item: bool = False
    temporal: str = "local"
    note: str = ""

    def patch_operation(self) -> Operation:
        """The patch-layer view: what EditPatch schedules and judges."""
        return Operation(
            domain=self.domain, required=self.required,
            span=self.span or _item_span, apply=self.apply or _unapplied,
            verify=self.verify or _no_readback, key=self.key or _item_key,
            merge=self.merge, item=self.item, temporal=self.temporal)


def _unapplied(op, _timeline, _item, _project=None):
    raise RuntimeError(
        f"algebra op {op.get('op')!r} has no native apply yet - its "
        f"executor lands with the gap that owns it")


def _no_readback(op, after):
    return None


def _item_key(op):
    return ("clip", op["unique_id"])


# ── Verify helpers ─────────────────────────────────────────────────────


def _verify_item_field(field: str, expected):
    def verify(op, after):
        clip = _clip(after, op["unique_id"])
        if clip is None:
            return f"clip {op['unique_id']!r} is not on the timeline"
        got = clip.get(field)
        if not _same_value(got, expected):
            return f"clip {op['unique_id']!r} reads {field}={got!r}, not {expected!r}"
        return None
    return verify


def _verify_presence(present: bool, what: str = "clip"):
    def verify(op, after):
        clip = _clip(after, op["unique_id"])
        if present and clip is None:
            return f"{what} {op['unique_id']!r} is not on the timeline"
        if not present and clip is not None:
            return f"{what} {op['unique_id']!r} still reads back"
        return None
    return verify


def _verify_transform(key: str, expected):
    def verify(op, after):
        clip = _clip(after, op["unique_id"])
        if clip is None:
            return f"clip {op['unique_id']!r} is not on the timeline"
        got = (clip.get("transform") or {}).get(key)
        if not _same_value(got, expected):
            return (f"clip {op['unique_id']!r} reads {key}={got!r}, "
                    f"not {expected!r}")
        return None
    return verify


def _verify_duration(expected):
    def verify(op, after):
        clip = _clip(after, op["unique_id"])
        if clip is None:
            return f"clip {op['unique_id']!r} is not on the timeline"
        if int(clip["duration"]) != int(expected):
            return (f"clip {op['unique_id']!r} reads duration="
                    f"{clip['duration']}, not {expected}")
        return None
    return verify


def _verify_track_name(expected):
    def verify(op, after):
        track = _track(after, op["track_type"], int(op["track_index"]))
        if track is None:
            return (f"{op['track_type']}{op['track_index']} is not on "
                    f"the timeline")
        if track["name"] != expected:
            return (f"{op['track_type']}{op['track_index']} reads "
                    f"{track['name']!r}, not {expected!r}")
        return None
    return verify


def _verify_track_named(expected):
    def verify(op, after):
        for track in after.get("tracks", []):
            if (track["type"] == op["track_type"]
                    and track["name"] == expected):
                return None
        return (f"no {op['track_type']} track named {expected!r} reads "
                f"back")
    return verify


def _verify_track_present(present: bool):
    def verify(op, after):
        track = _track(after, op["track_type"], int(op["track_index"]))
        if present and track is None:
            return f"{op['track_type']}{op['track_index']} is not on the timeline"
        if not present and track is not None:
            return f"{op['track_type']}{op['track_index']} still reads back"
        return None
    return verify


def _verify_track_reorder(op, after):
    """The row that was at `from_index` reads back at `to_index` under
    the same name - and, when the indices differ, no longer at
    `from_index`: a reorder that answered True and moved nothing fails
    here."""
    track_type = op["track_type"]
    from_index = int(op["from_index"])
    to_index = int(op["to_index"])
    name = op["name"]
    landed = _track(after, track_type, to_index)
    if landed is None:
        return f"{track_type}{to_index} is not on the timeline"
    if landed["name"] != name:
        return (f"{track_type}{to_index} reads {landed['name']!r}, not "
                f"{name!r}: the row did not land at the declared index")
    if from_index != to_index:
        left = _track(after, track_type, from_index)
        if left is not None and left["name"] == name:
            return (f"{track_type}{from_index} still reads {name!r}: the "
                    "row did not move")
    return None


def _matte_read(op, after):
    """(state, failure): the op's matte as the after-read reports it."""
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return None, f"clip {op['unique_id']!r} is not on the timeline"
    fusion = clip.get("fusion") or {}
    names = fusion.get("comp_names") or []
    if op["matte"] not in names:
        return None, (f"clip {op['unique_id']!r} carries no matte "
                      f"{op['matte']!r}; reads {names}")
    state = (fusion.get("mattes") or {}).get(op["matte"])
    if state is None:
        return None, (f"matte {op['matte']!r} carries no readable "
                      f"RectangleMask")
    return state, None


def _matte_value_failure(label, got, expected):
    """None when a read matte value matches the declared one."""
    if isinstance(expected, dict):
        for axis, want in expected.items():
            have = got.get(axis) if isinstance(got, dict) else None
            failure = _matte_value_failure(f"{label}.{axis}", have, want)
            if failure:
                return failure
        return None
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        if abs(float(got) - float(expected)) <= FLOAT_TOLERANCE:
            return None
    elif got == expected:
        return None
    return f"{label} reads {got!r}, not {expected!r}"


def _verify_magic_mask_provider(op, after):
    """The verify half of a Magic Mask op, judged without the shadow.

    The ported core (`native/ren-core`) models the Fusion engine only:
    it knows matte comps, not node graphs, so a Magic Mask op routed
    to it would be judged by the wrong engine. The api provider's
    apply already claimed True-plus-node-gain at the live handles
    (nothing to re-judge); a ui op reaching verify means its apply
    refusal was bypassed, which fails closed. Returns the failure or
    None, like every verify.
    """
    provider = op.get("provider", "fusion_matte")
    if provider == "resolve_magic_mask_api":
        return None
    return (f"{op.get('op')}: provider {provider!r} executes outside "
            f"the patch - verify reached an op its apply must refuse")


def _verify_mask_create(op, after):
    """The matte comp reads back on the clip with a RectangleMask."""
    if op.get("provider", "fusion_matte") != "fusion_matte":
        return _verify_magic_mask_provider(op, after)
    _state, failure = _matte_read(op, after)
    return failure


def _verify_mask_delete(op, after):
    """The matte comp no longer reads back on the clip."""
    if op.get("provider", "fusion_matte") != "fusion_matte":
        return _verify_magic_mask_provider(op, after)
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    names = (clip.get("fusion") or {}).get("comp_names") or []
    if op["matte"] in names:
        return f"clip {op['unique_id']!r} still carries matte {op['matte']!r}"
    return None


def _verify_mask_edit(op, after):
    """The matte reads back with the declared static geometry."""
    if op.get("provider", "fusion_matte") != "fusion_matte":
        return _verify_magic_mask_provider(op, after)
    state, failure = _matte_read(op, after)
    if failure:
        return failure
    for key, expected in op["geometry"].items():
        failure = _matte_value_failure(
            f"matte {op['matte']!r} {key}", state["geometry"].get(key),
            expected)
        if failure:
            return failure
    return None


def _verify_mask_track(op, after):
    """The matte's keyframes read back exactly at the declared frames.

    Every declared frame carries every declared value, and no frame is
    keyed that was not declared - an empty track reads back unkeyed,
    which is what the inverse of a first track relies on.
    """
    if op.get("provider", "fusion_matte") != "fusion_matte":
        return _verify_magic_mask_provider(op, after)
    from library.tools.reel_read import MATTE_PARAMS

    state, failure = _matte_read(op, after)
    if failure:
        return failure
    got = {k["frame"]: k for k in state["keyframes"]}
    declared = op["track"]["keyframes"]
    extra = sorted(set(got) - {k["frame"] for k in declared})
    if extra:
        return f"matte {op['matte']!r} is keyed at undeclared frames {extra}"
    for keyframe in declared:
        frame = keyframe["frame"]
        if frame not in got:
            return (f"matte {op['matte']!r} has no keyframe at frame "
                    f"{frame}; keyed at {sorted(got)}")
        for param in MATTE_PARAMS:
            if param not in keyframe:
                continue
            failure = _matte_value_failure(
                f"matte {op['matte']!r} frame {frame} {param}",
                got[frame].get(param), keyframe[param])
            if failure:
                return failure
    keyed = {param for k in declared for param in k if param != "frame"}
    for key, expected in op.get("geometry", {}).items():
        if key in keyed:
            continue
        failure = _matte_value_failure(
            f"matte {op['matte']!r} {key}", state["geometry"].get(key),
            expected)
        if failure:
            return failure
    return None


# ── Apply helpers (the native writes) ──────────────────────────────────


def _apply_grade_node_set(op, _timeline, item, _project=None):
    """`SetCDL` the four terms onto ONE named node, judged by re-read.

    The node index is validated against `GetNumNodes` before the write:
    the Graph API has no AddNode, so only an existing node can hold a
    grade. A `True` from `SetCDL` is not evidence the grade landed
    (`color_page_grade.py` measured a CDL that returned True and moved
    zero pixels), so the write is judged by `GetCDL` on the same node
    and the receipt carries what Resolve answered, never what was
    intended.
    """
    from library.tools.cdl_readback import compare_cdl

    node_index = int(op["node_index"])
    params = op["params"]
    try:
        graph = item.GetNodeGraph()
        count = graph.GetNumNodes()
    except Exception as exc:  # noqa: BLE001 - raised, never half-written
        raise RuntimeError(
            f"grade.node_set cannot open the node graph ({exc}) - "
            f"nothing was written.") from exc
    if node_index < 1 or (isinstance(count, int) and node_index > count):
        raise RuntimeError(
            f"grade.node_set names node {node_index}, outside 1..{count} - "
            f"the Graph API has no AddNode, so only an existing node can "
            f"hold a grade. Nothing was written.")
    payload = {
        "NodeIndex": str(node_index),
        "Slope": (f"{float(params.get('slope_r', 1.0)):.4f} "
                  f"{float(params.get('slope_g', 1.0)):.4f} "
                  f"{float(params.get('slope_b', 1.0)):.4f}"),
        "Offset": (f"{float(params.get('offset_r', 0.0)):.4f} "
                   f"{float(params.get('offset_g', 0.0)):.4f} "
                   f"{float(params.get('offset_b', 0.0)):.4f}"),
        "Power": (f"{float(params.get('power_r', 1.0)):.4f} "
                  f"{float(params.get('power_g', 1.0)):.4f} "
                  f"{float(params.get('power_b', 1.0)):.4f}"),
        "Saturation": f"{float(params.get('saturation', 1.0)):.4f}",
    }
    try:
        wrote = bool(item.SetCDL(payload))
    except Exception as exc:  # noqa: BLE001 - raised, never half-written
        raise RuntimeError(
            f"grade.node_set SetCDL raised ({exc}) - verify by hand.") \
            from exc
    if not wrote:
        raise RuntimeError(
            f"grade.node_set SetCDL(node {node_index}) answered False - "
            f"nothing was claimed.")
    try:
        back = dict(item.GetCDL() or {})
    except Exception as exc:  # noqa: BLE001 - raised, never half-written
        raise RuntimeError(
            f"grade.node_set GetCDL re-read raised ({exc}) - verify by "
            f"hand.") from exc
    problems = compare_cdl(back, params)
    if problems:
        raise RuntimeError(
            f"grade.node_set wrote node {node_index} and the re-read "
            f"disagrees: {'; '.join(problems)}")
    return True


def _verify_grade_node_set(op, after):
    """The node-grade read-back: the snapshot's `GetCDL` vs the plan.

    `after` is the post-write shadow snapshot, whose `color.cdl` is
    what `GetCDL` answered after the write. Compared with the four
    terms the op specified, through `cdl_readback.compare_cdl` - the
    same comparison the render QA uses, so the op and the export
    cannot disagree about what a CDL is.
    """
    from library.tools.cdl_readback import compare_cdl

    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    actual = (clip.get("color") or {}).get("cdl") or {}
    problems = compare_cdl(actual, op["params"])
    if problems:
        return (f"clip {op['unique_id']!r} node {op['node_index']} reads "
                f"back: " + "; ".join(problems))
    return None


def _default_ui_driver():
    """The native resolve-control driver - the GUI-only applies' hands.

    Built lazily so importing the algebra never spawns a process, and
    a missing binary refuses here (naming the build) rather than
    mid-apply. Tests point `ui_driver_factory` at a fake instead.
    """
    from library.tools.computer_use.client import DriverClient

    return DriverClient()


#: The UI driver source for the GUI-only applies (`grade.node_add`
#: first). Production builds the native driver, which refuses when it
#: is not built; the tests monkeypatch this to inject the fake - the
#: fake lives in the tests and is never the default. Mirrors
#: `resolved.ui_transaction.driver_factory`, the broker's half of the
#: same seam.
ui_driver_factory = _default_ui_driver


def _read_node_count(item, label="grade.node_add") -> int:
    """The node graph's count through the API, or a refusal.

    The judgement half of every GUI-only grade write: the write is
    GUI-only, the verification is API-readable. A graph that will not
    open or count refuses BEFORE anything runs, so a topology op
    never claims a node it cannot see.
    """
    try:
        graph = item.GetNodeGraph()
        count = graph.GetNumNodes()
    except Exception as exc:  # noqa: BLE001 - raised, never half-written
        raise RuntimeError(
            f"{label} cannot open the node graph ({exc}) - "
            f"nothing was written.") from exc
    if isinstance(count, bool) or not isinstance(count, int):
        raise RuntimeError(
            f"{label} GetNumNodes answered {count!r}, not a count - "
            f"nothing was written.")
    return count


def _apply_grade_node_add(op, timeline, item, _project=None):
    """Add ONE serial node through the Color-page menu recipe, judged.

    The Graph API exposes no AddNode (measured), so the write is
    GUI-only: two menu commands executed server-side
    (`computer_use.scripts.grade_node_add_script` - no viewer
    coordinate and no keystroke anywhere in it; keys land in
    whatever app is frontmost, measured live), then `GetNumNodes`
    is re-read through the API. The count must grow by EXACTLY one:
    no change means the command landed on the wrong clip, and more
    than one means it double-fired - either way the op refuses and
    nothing is claimed.

    The menu acts on the current clip, so the playhead is parked
    mid-clip first and the park is read back: a playhead that will
    not park refuses rather than landing the node on whatever clip
    happens to be current.
    """
    from library.tools.computer_use.scripts import (
        grade_node_add_script, run_script,
    )

    _park_playhead_mid_clip(op, timeline, item)
    before = _read_node_count(item, "grade.node_add")
    client = ui_driver_factory()
    try:
        result = run_script(client, grade_node_add_script())
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()
    failed = [step for step in result.steps
              if step.get("outcome") != "ok"]
    if result.outcome != "done" or failed:
        raise RuntimeError(
            f"grade.node_add UI recipe did not land ({result.outcome}; "
            f"{len(failed)} failed step(s)) - nothing was claimed.")
    after = _read_node_count(item, "grade.node_add")
    if after != before + 1:
        raise RuntimeError(
            f"grade.node_add nodes {before} -> {after}: expected exactly "
            f"one serial node - the command may have landed on the "
            f"wrong clip or double-fired. Nothing was claimed.")
    return True


def _park_playhead_mid_clip(op, timeline, item) -> None:
    """Park the playhead mid-clip and read the park back, or refuse.

    One retry: the first set after a timeline switch can miss
    (measured by the batch-5 lane). A playhead that will not park
    refuses - the menu acts on the current clip, so an unparked run
    would land the node wherever the playhead happens to be.
    """
    from library.tools.marker_feedback import frames_to_timecode

    try:
        start, end = int(item.GetStart()), int(item.GetEnd())
        fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    except Exception as exc:  # noqa: BLE001 - raised, never half-written
        raise RuntimeError(
            f"grade.node_add cannot read clip {op.get('unique_id')!r}'s "
            f"span ({exc}) - nothing was written.") from exc
    wanted = frames_to_timecode((start + end) // 2, fps)
    try:
        timeline.SetCurrentTimecode(wanted)
        if timeline.GetCurrentTimecode() != wanted:
            timeline.SetCurrentTimecode(wanted)
            if timeline.GetCurrentTimecode() != wanted:
                raise RuntimeError(
                    f"playhead reads "
                    f"{timeline.GetCurrentTimecode()!r}, not {wanted!r}")
    except Exception as exc:  # noqa: BLE001 - raised, never half-written
        raise RuntimeError(
            f"grade.node_add could not park the playhead on clip "
            f"{op.get('unique_id')!r} ({exc}) - nothing was written."
            ) from exc


def _apply_set_property(op, _timeline, item, _project=None):
    from library.tools.transform_write_log import set_property

    return set_property(
        item, op["key"], op["value"],
        item_identity={"resolve_unique_id": str(op["unique_id"])})


def _apply_smart_reframe(op, _timeline, item, _project=None):
    returned = item.SmartReframe()
    if not returned:
        raise RuntimeError("SmartReframe answered False")
    return returned


def _apply_retime_segments(op, _timeline, item, _project=None):
    """Split the clip at each segment boundary, then SetSpeed each piece.

    Resolve carries no speed-curve API (measured 2026-09-24, PR 1376):
    a ramp reaches the timeline as stepped constant-speed segments. This
    apply produces exactly that - one split per boundary, one constant
    `SetSpeed` per piece.
    """
    segments = sorted(op["segments"], key=lambda s: int(s["record_frame"]))
    pieces = [item]
    for segment in reversed(segments[1:]):
        frame = int(segment["record_frame"])
        for piece in list(pieces):
            start = int(piece.GetStart())
            end = int(piece.GetEnd())
            if start <= frame < end:
                second = piece.SplitAtFrame(frame)
                if second is not None:
                    pieces.append(second)
                break
    pieces.sort(key=lambda p: int(p.GetStart()))
    for index, segment in enumerate(segments):
        if index < len(pieces):
            pieces[index].SetSpeed(
                {"Percentage": float(segment["percent"]),
                 "RippleTimeline": False})
    return True


def _apply_picture_keyframe(op, _timeline, item, _project=None):
    """Write keyframed transform parameters through the write log."""
    from library.tools.transform_write_log import set_keyframe

    for keyframe in op["keyframes"]:
        set_keyframe(
            item, op["param"], keyframe["frame"], keyframe["value"],
            keyframe.get("interpolation", "linear"),
            item_identity={"resolve_unique_id": str(op["unique_id"])})
    return True


def _call(item, method, default):
    try:
        return getattr(item, method)()
    except Exception:
        return default


def _build_matte_comp(item, matte_name, geometry, keyframes=()):
    """Build a real Fusion matte comp and return its path.

    The matte is a RectangleMask comp: MediaIn -> Merge -> MediaOut. The
    Merge takes the picture as Foreground and the RectangleMask as
    EffectMask, so the output is the picture masked by the rectangle. A
    RectangleMask carries no image Input (probed: 57 inputs, none of
    them Input) - it is a mask generator, and the Merge is the node that
    applies it. The comp is built by the same CompEngine that builds
    every other comp in this pipeline, so it serializes to the Lua-table
    format Resolve reads.

    `keyframes` are BAKED into the comp: Width/Height keys become a
    BezierSpline, Center keys an XYPath over an X and a Y spline (the
    engine's own shake idiom - an XYPath, never the `Path {}` the Merge
    rule forbids). Keying through the live API does not take: measured
    on Resolve 21.1, `SetInput(name, value, time)` on an input that is
    not already animated sets the STATIC value. An input with no keys
    takes its `geometry` value.

    The comp is written to a temp directory: it is a vehicle for the
    import, not a persistent artifact - the matte lives in the comp's
    inputs, which the shadow reads back.
    """
    import os
    import tempfile

    from library.tools.fusion.engine import CompEngine
    from library.tools.fusion.nodes import BezierSpline, FusionNode

    pool_item = _call(item, "GetMediaPoolItem", None)

    def pool_int(key, default):
        try:
            return int(pool_item.GetClipProperty(key)) or default
        except Exception:
            return default

    engine = CompEngine(clip_dur=pool_int("Frames", 100),
                        width=pool_int("Width", 1920),
                        height=pool_int("Height", 1080))

    def spline(name, pairs):
        curve = BezierSpline(name)
        for frame, value in pairs:
            curve.add_key(int(frame), float(value), flags={"Linear": True})
        engine.comp.add_node(curve)
        return curve

    mask = FusionNode("RectangleMask1", "RectangleMask")
    for param in ("Width", "Height"):
        keyed = [(k["frame"], k[param]) for k in keyframes if param in k]
        static = geometry.get(param)
        mask.set_input(param, spline(f"RectangleMask1{param}", keyed)
                       if keyed else 1.0 if static is None else static)
    keyed = [(k["frame"], k["Center"]) for k in keyframes if "Center" in k]
    if keyed:
        path = FusionNode("RectangleMask1Path", "XYPath")
        for axis in ("X", "Y"):
            path.set_input(axis, spline(
                f"RectangleMask1Center{axis}",
                [(frame, point[axis]) for frame, point in keyed]))
        engine.comp.add_node(path)
        mask.set_input("Center", "RectangleMask1Path", source="Value")
    else:
        center = geometry.get("Center") or {"X": 0.5, "Y": 0.5}
        mask.set_input("Center", (center["X"], center["Y"]))
    mask.set_input("Invert", geometry.get("Invert") or 0)
    mask.set_input("PixelAspect", (1, 1))
    mask.pos = (110, 0)
    engine.comp.add_node(mask)

    merge = FusionNode("Merge1", "Merge")
    merge.set_input("Foreground", "MediaIn1")
    merge.set_input("EffectMask", "RectangleMask1", source="Mask")
    merge.pos = (220, 0)
    engine.comp.add_node(merge)

    engine._last_output = "Merge1"
    content = engine.serialize()

    comp_dir = tempfile.mkdtemp(prefix="matte_")
    comp_path = os.path.join(comp_dir, f"{matte_name}.comp")
    with open(comp_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return comp_path


def _import_matte(item, matte_name, geometry, keyframes, label):
    """Put a matte comp on the item as a NEW comp; return its name.

    Measured on Resolve 21.1 (the C-11 live smoke): `ImportFusionComp`
    ADDS a comp only to an item that has none. On an item that already
    carries comps it OVERWRITES the current one in place and returns a
    comp all the same - so a bare import onto the captain's clip would
    replace whichever comp was current. `AddFusionComp` adds an empty
    comp and makes it current, so the import lands in that. The new
    comp is the one name the after-list has and the before-list did not;
    anything but exactly one refuses, and a new comp that does not read
    back as a matte is removed before refusing.
    """
    from library.tools.reel_read import matte_state

    comp_path = _build_matte_comp(item, matte_name, geometry, keyframes)
    before = list(item.GetFusionCompNameList() or [])
    added = item.AddFusionComp()
    after = list(item.GetFusionCompNameList() or [])
    new = [name for name in after if name not in before]
    if not added or len(new) != 1:
        raise RuntimeError(
            f"{label}: AddFusionComp returned {added!r}; expected exactly 1 "
            f"new comp, found {new} (before={before}, after={after})")
    new_name = new[0]
    imported = item.ImportFusionComp(comp_path)
    if not imported or matte_state(item, after.index(new_name) + 1) is None:
        # Resolve keeps a clip's last comp, so on a clip that had none
        # the empty comp stays; say which.
        removed = item.DeleteFusionCompByName(new_name)
        raise RuntimeError(
            f"{label}: ImportFusionComp returned {imported!r} and comp "
            f"{new_name!r} does not read back as a matte; "
            f"{'it was removed' if removed else 'it could not be removed'}")
    return new_name


def _rename_matte(item, old_name, matte_name, label):
    renamed = item.RenameFusionCompByName(old_name, matte_name)
    names = list(item.GetFusionCompNameList() or [])
    if matte_name not in names:
        raise RuntimeError(
            f"{label}: RenameFusionCompByName({old_name!r}, "
            f"{matte_name!r}) returned {renamed!r}; comps read {names}")


def _refuse_unmarked_matte(op, label):
    from library.tools import reel_read

    if not reel_read.is_matte_comp(op["matte"]):
        raise RuntimeError(
            f"{label}: matte name {op['matte']!r} does not carry "
            f"{reel_read.MATTE_MARK!r}; the read only opens comps whose "
            f"name marks them as mattes, so it could never verify this one")


def _current_matte(item, matte_name, label):
    """The named matte's read-back state; refuses when there is none."""
    from library.tools.reel_read import matte_state

    names = list(item.GetFusionCompNameList() or [])
    if matte_name not in names:
        raise RuntimeError(
            f"{label}: clip carries no matte {matte_name!r}; reads {names}")
    state = matte_state(item, names.index(matte_name) + 1)
    if state is None:
        raise RuntimeError(
            f"{label}: matte {matte_name!r} carries no readable "
            f"RectangleMask")
    return state


def _replace_matte(item, matte_name, geometry, keyframes, label):
    """Replace a matte comp: import the new one, THEN delete the old.

    The order is the point - a failed import must never lose the
    captain's matte, so the old comp is only deleted once its
    replacement is on the clip.
    """
    new_name = _import_matte(item, matte_name, geometry, keyframes, label)
    if not item.DeleteFusionCompByName(matte_name):
        item.DeleteFusionCompByName(new_name)
        raise RuntimeError(
            f"{label}: DeleteFusionCompByName({matte_name!r}) failed; "
            f"the replacement was removed and the matte left as it was")
    _rename_matte(item, new_name, matte_name, label)


#: The mask providers (the Resolve Hands plan, section 8): no parallel
#: magic_mask.* vocabulary is invented - the algebra already has
#: mask.create / mask.edit / mask.track, and a provider says which
#: engine executes the op. `fusion_matte` is the implemented one (a
#: Fusion RectangleMask comp). The two Magic Mask providers are named
#: here so an op that claims one fails loudly instead of silently
#: getting a Fusion matte - the wrong capability executed and verified.
MASK_PROVIDERS = ("fusion_matte", "resolve_magic_mask_api",
                  "resolve_magic_mask_ui")


def _mask_provider(op, label):
    """The provider executing one mask op, refused by name when unknown."""
    provider = op.get("provider", "fusion_matte")
    if provider not in MASK_PROVIDERS:
        raise RuntimeError(
            f"{label}: provider {provider!r} is unknown; one of "
            f"{list(MASK_PROVIDERS)}")
    return provider


def _refuse_unimplemented_mask_provider(op, label):
    """Refuse a named-but-unimplemented mask provider, by name."""
    raise RuntimeError(
        f"{label}: provider {op.get('provider')!r} is not implemented; "
        f"only 'fusion_matte' executes today")


#: The modes `TimelineItem.CreateMagicMask` takes - the same gate
#: `resolve-axi sense mask` enforces, so neither the algebra nor the
#: CLI ever asks the API for a mode it refuses.
MAGIC_MASK_MODES = ("F", "B", "BI")

#: The recipe verb that executes the UI provider's ops. EditPatch
#: carries no driver - a UI seed click or tracker press cannot run
#: inside a patch - so these ops name their executor instead of a
#: Fusion matte. The recipe builders (`magic_mask.seed_script` /
#: `track_script`) ARE the provider's implementation: seed_script is
#: what a mask.create/ui op runs, track_script what a mask.track/ui
#: op runs.
MAGIC_MASK_RECIPE_VERB = "ren computer-use script"


def _magic_mask_mode(op, label):
    """The CreateMagicMask mode the op names, refused when unknown."""
    mode = op.get("mode", "BI")
    if mode not in MAGIC_MASK_MODES:
        raise RuntimeError(
            f"{label}: bad Magic Mask mode {mode!r}; one of "
            f"{list(MAGIC_MASK_MODES)}")
    return mode


def _require_magic_seed(op, label):
    """The MaskSeed receipt an api create runs on, refused when absent.

    Measured (`resolve_axi sense_mask`, `neural_engine`): a bare
    `CreateMagicMask` call answers False - the API starts tracking
    from a seeded click, and without the seed there is nothing to
    start from. The seed's viewer point is the click the UI phase
    made; the object id binds the mask that follows to the subject
    Ren determined.
    """
    seed = op.get("seed")
    if not isinstance(seed, dict):
        raise RuntimeError(
            f"{label}: provider 'resolve_magic_mask_api' needs 'seed', "
            f"the MaskSeed receipt whose viewer point the UI phase "
            f"clicked - an unseeded call answers False")
    viewer = seed.get("viewer") or {}
    if not isinstance(viewer.get("x"), (int, float)) or not isinstance(
            viewer.get("y"), (int, float)):
        raise RuntimeError(
            f"{label}: the seed carries no viewer point "
            f"({seed.get('viewer')!r}) - nothing says where the click "
            f"landed")
    if not seed.get("object_id"):
        raise RuntimeError(
            f"{label}: the seed names no object "
            f"({seed.get('object_id')!r}) - the mask would bind to "
            f"no tracked subject")
    return seed


def _node_count(item):
    """The clip's Color-page node count, or None where unreadable."""
    try:
        graph = item.GetNodeGraph()
    except Exception:                                   # noqa: BLE001
        return None
    if graph is None:
        return None
    try:
        return int(graph.GetNumNodes())
    except Exception:                                   # noqa: BLE001
        return None


def _call_create_magic_mask(op, item, label, *, gain_required):
    """Run one CreateMagicMask call and judge it by the node graph.

    The claim rule is `sense_mask`'s: True plus a gained node claims
    the mask, anything else is a refusal with the measured reason.
    `gain_required` is the creation half (a started mask adds its
    node); a re-track claims the accepted call - tracking moves an
    existing mask, which gains nothing - and the mask read-back that
    follows judges it.
    """
    mode = _magic_mask_mode(op, label)
    before = _node_count(item)
    if gain_required and before is None:
        raise RuntimeError(
            f"{label}: the clip's node graph does not read, so a "
            f"started mask could never be verified - refusing before "
            f"anything runs")
    try:
        started = bool(item.CreateMagicMask(mode))
    except Exception as exc:                             # noqa: BLE001
        raise RuntimeError(
            f"{label}: CreateMagicMask({mode}) raised ({exc}) - "
            f"verify by hand")
    if not started:
        raise RuntimeError(
            f"{label}: CreateMagicMask({mode}) answered False "
            f"(nodes {before}) - no mask started, nothing claimed")
    if gain_required:
        after = _node_count(item)
        if after == before:
            raise RuntimeError(
                f"{label}: CreateMagicMask reports True but the node "
                f"count reads back unchanged ({before}) - unverified, "
                f"refusing to claim it")
    return True


#: The Power Window shapes the Color page's Window palette offers. The
#: recipe draws the named shape; the create key below refuses anything
#: else, so a planner cannot invent one and hand the recipe garbage.
WINDOW_SHAPES = ("circle", "linear", "polygon", "powercurve", "gradient")


def _window_create_key(op):
    """What a window create writes, refusing an invented shape."""
    shape = op.get("shape")
    if shape not in WINDOW_SHAPES:
        raise ValueError(
            f"color.window.create: bad window shape {shape!r}; one of "
            f"{list(WINDOW_SHAPES)}")
    return ("clip", op["unique_id"], "window", int(op["node_index"]))


#: The tracker directions the Color page offers beside a window: Track
#: Forward, Track Reverse (measured: Color > Tracker > Forward/Reverse).
#: A bidirectional track is two ops, not a third vocabulary item.
WINDOW_TRACK_DIRECTIONS = ("forward", "reverse")


def _window_track_key(op):
    """What a window track writes, refusing an invented direction."""
    direction = op.get("direction")
    if direction not in WINDOW_TRACK_DIRECTIONS:
        raise ValueError(
            f"color.window.track: bad track direction {direction!r}; one "
            f"of {list(WINDOW_TRACK_DIRECTIONS)}")
    return ("clip", op["unique_id"], "window", int(op["node_index"]))


def _qualifier_sample_key(op):
    """What a qualifier sample writes, refusing an unclickable point.

    The point is a normalized frame point - two finite numbers in
    0..1, the contract the viewer-coordinate mapping (captain plan s7)
    clicks through. Anything else names no frame pixel, so the recipe
    would sample blind.
    """
    point = op.get("point")
    if (not isinstance(point, (list, tuple)) or len(point) != 2
            or not all(isinstance(v, (int, float))
                       and not isinstance(v, bool) and 0.0 <= v <= 1.0
                       for v in point)):
        raise ValueError(
            f"color.qualifier.sample: bad sample point {point!r}; two "
            f"finite numbers in 0..1")
    return ("clip", op["unique_id"], "qualifier", int(op["node_index"]))


def _refuse_ui_provider(op, label, recipe):
    """Refuse a UI-provider op with the route that executes it."""
    raise RuntimeError(
        f"{label}: provider 'resolve_magic_mask_ui' executes through "
        f"the recipe verb ({MAGIC_MASK_RECIPE_VERB} --recipe {recipe}), "
        f"not in a patch - EditPatch carries no driver for the UI "
        f"click this op needs")


def _apply_mask_create(op, _timeline, item, project=None):
    """Start the mask the provider names and claim it by read-back.

    fusion_matte imports a static, full-frame matte comp and gives it
    the matte's name. resolve_magic_mask_api starts the native mask
    from the op's seed click (True plus a gained node claims it).
    resolve_magic_mask_ui never runs in a patch - it names the recipe
    verb that clicks the seed. An op claiming a Magic Mask provider
    used to silently get a Fusion matte here - the wrong capability
    executed and verified.
    """
    _refuse_unmarked_matte(op, "mask.create")
    provider = _mask_provider(op, "mask.create")
    if provider == "resolve_magic_mask_ui":
        _refuse_ui_provider(op, "mask.create", "magic-mask-seed")
    if provider == "resolve_magic_mask_api":
        _require_magic_seed(op, "mask.create")
        return _call_create_magic_mask(
            op, item, "mask.create", gain_required=True)
    if op["matte"] in (item.GetFusionCompNameList() or []):
        raise RuntimeError(
            f"mask.create: clip already carries matte {op['matte']!r}")
    new_name = _import_matte(item, op["matte"], {}, (), "mask.create")
    _rename_matte(item, new_name, op["matte"], "mask.create")
    return True


def _passthrough_comp_path(item):
    """A comp that draws nothing: MediaIn -> MediaOut."""
    import os
    import tempfile

    from library.tools.fusion.engine import CompEngine

    pool_item = _call(item, "GetMediaPoolItem", None)

    def pool_int(key, default):
        try:
            return int(pool_item.GetClipProperty(key)) or default
        except Exception:
            return default

    content = CompEngine(clip_dur=pool_int("Frames", 100),
                         width=pool_int("Width", 1920),
                         height=pool_int("Height", 1080)).serialize()
    path = os.path.join(tempfile.mkdtemp(prefix="matte_"), "passthrough.comp")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def _apply_mask_delete(op, _timeline, item, project=None):
    """Remove the named matte comp - the inverse of mask.create.

    Measured on Resolve 21.1: `DeleteFusionCompByName` refuses a clip's
    LAST comp (returns False, the count stays 1). A matte that is the
    only comp is therefore reset instead: made current, overwritten with
    a passthrough comp (MediaIn -> MediaOut, which draws nothing) and
    renamed to the name Resolve gives a clip's first comp. The matte is
    gone either way, which is what the verify reads.

    Deleting is a Fusion-comp act whatever the provider: a Magic Mask
    provider names how the mask was made, and unmaking one is not a
    comp delete. Non-fusion providers are refused by name.
    """
    if _mask_provider(op, "mask.delete") != "fusion_matte":
        _refuse_unimplemented_mask_provider(op, "mask.delete")
    names = list(item.GetFusionCompNameList() or [])
    if op["matte"] not in names:
        raise RuntimeError(
            f"mask.delete: clip carries no matte {op['matte']!r}; "
            f"reads {names}")
    if len(names) > 1:
        return item.DeleteFusionCompByName(op["matte"])
    item.LoadFusionCompByName(op["matte"])
    if not item.ImportFusionComp(_passthrough_comp_path(item)):
        raise RuntimeError(
            f"mask.delete: {op['matte']!r} is the clip's only comp, which "
            f"Resolve will not delete, and resetting it to a passthrough "
            f"failed")
    _rename_matte(item, op["matte"], "Composition 1", "mask.delete")
    return True


def _apply_mask_edit(op, _timeline, item, project=None):
    """Set the matte's static geometry, keeping every key it carries.

    A declared input that is keyed loses its keys (it is now static); an
    input the edit does not name keeps its value and its keys. Editing
    is a Fusion-geometry act: a Magic Mask carries no static geometry
    to set, so non-fusion providers are refused by name.
    """
    if _mask_provider(op, "mask.edit") != "fusion_matte":
        _refuse_unimplemented_mask_provider(op, "mask.edit")
    _refuse_unmarked_matte(op, "mask.edit")
    state = _current_matte(item, op["matte"], "mask.edit")
    geometry = {**state["geometry"], **op["geometry"]}
    keyframes = []
    for keyframe in state["keyframes"]:
        kept = {k: v for k, v in keyframe.items() if k not in op["geometry"]}
        if set(kept) - {"frame"}:
            keyframes.append(kept)
    _replace_matte(item, op["matte"], geometry, keyframes, "mask.edit")
    return True


def _apply_mask_track(op, _timeline, item, project=None):
    """Track the mask through the clip.

    fusion_matte replaces the matte's keyframes with the declared
    track (keys are clip-relative comp frames). resolve_magic_mask_api
    re-tracks through the mask engine itself: the op's keyframes are
    the fusion provider's vocabulary and must be empty here - a keyed
    api track names two engines at once. resolve_magic_mask_ui never
    runs in a patch - it names the recipe verb that presses the
    tracker. Baking keys loses an input's static value (Fusion keeps
    none beside a spline), so the inverse of a first fusion track is
    an EMPTY track carrying the previous geometry.
    """
    provider = _mask_provider(op, "mask.track")
    if provider == "resolve_magic_mask_ui":
        _refuse_ui_provider(op, "mask.track", "magic-mask-track")
    if provider == "resolve_magic_mask_api":
        keyframes = (op.get("track") or {}).get("keyframes") or []
        if keyframes:
            raise RuntimeError(
                f"mask.track: provider 'resolve_magic_mask_api' carries "
                f"{len(keyframes)} keyframe(s) - keyframes are the "
                f"fusion_matte provider's vocabulary; the api provider "
                f"tracks through the mask engine, so its track is empty")
        return _call_create_magic_mask(
            op, item, "mask.track", gain_required=False)
    _refuse_unmarked_matte(op, "mask.track")
    state = _current_matte(item, op["matte"], "mask.track")
    _replace_matte(item, op["matte"],
                   {**state["geometry"], **op.get("geometry", {})},
                   op["track"]["keyframes"], "mask.track")
    return True


def _delete_clip_with_measured_retry(op, timeline, item, _project=None):
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


def _find_pool_item_by_id(pool, unique_id):
    """The pool item holding *unique_id*, searched through every folder.

    A placement addresses its source by the pool item's unique id - the
    same identity `pool_item_for` matches on, so a reel's overlay never
    pairs with another reel's file of the same name.
    """
    stack = [pool.GetRootFolder()]
    while stack:
        folder = stack.pop()
        for item in folder.GetClipList() or ():
            if str(item.GetUniqueId()) == str(unique_id):
                return item
        stack.extend(folder.GetSubFolderList() or ())
    return None


def _apply_clip_insert(op, timeline, _item, project=None):
    """Place a pool clip at a stated span; the read-back names the item.

    The op carries the pool item's unique id (`media_pool_item`) and the
    source span (`source_in`/`source_out`). The apply places the clip,
    reads back the new item's Resolve-assigned unique id, and writes it
    onto the op so the verify (`_verify_presence`) and any later op can
    address the placed item.

    The media pool is reached `project.GetMediaPool()` - the route every
    other placement in the engine takes. A Timeline carries no such
    method (measured, the Reel 13 card build: `timeline.GetMediaPool`
    answered None on live Resolve and the call raised TypeError).
    """
    if project is None:
        raise RuntimeError(
            "clip.insert: no project handle - the media pool is reached "
            "project.GetMediaPool(), and a Timeline carries no such route")
    pool = project.GetMediaPool()
    pool_item = _find_pool_item_by_id(pool, op.get("media_pool_item"))
    if pool_item is None:
        raise RuntimeError(
            f"clip.insert: pool item {op.get('media_pool_item')!r} "
            "is not in the media pool")
    source_in = int(op.get("source_in", 0))
    source_out = int(op.get("source_out", 0))
    if source_out <= source_in:
        frames = pool_item.GetClipProperty("Frames")
        source_out = source_in + (int(frames) if frames else 1)
    placed = pool.AppendToTimeline([{
        "mediaPoolItem": pool_item,
        "startFrame": source_in,
        "endFrame": source_out,
        "mediaType": 1 if op["track_type"] == "video" else 2,
        "trackIndex": int(op["track_index"]),
        "recordFrame": int(op["record_in"]),
    }])
    if not placed:
        raise RuntimeError("clip.insert: AppendToTimeline placed nothing")
    new_item = placed[0]
    op["client_unique_id"] = op["unique_id"]
    op["unique_id"] = str(new_item.GetUniqueId())
    return new_item


def _live_handles(timeline):
    from library.tools.reel_read import live_items

    handles = {}
    duplicates = set()
    for row in live_items(timeline):
        for item in row["items"]:
            try:
                unique_id = str(item.GetUniqueId() or "")
            except Exception as unreadable:
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


def _apply_track_reorder(op, tl, _item, _project=None):
    """Move a track row to a new index; the row's name follows.

    Resolve has no move-track call. The measured route is delete and
    re-insert: `AddTrack`'s options dict carries `index`, which places
    the row at a declared position (skipped or out of bounds appends).
    DeleteTrack destroys the row's clips, so a row carrying any is
    refused - the engine does not destroy clips to move a row.
    """
    track_type = op["track_type"]
    from_index = int(op["from_index"])
    to_index = int(op["to_index"])
    if from_index == to_index:
        return True
    items = list(tl.GetItemListInTrack(track_type, from_index) or [])
    if items:
        raise RuntimeError(
            f"{track_type}{from_index} carries {len(items)} clip(s); "
            "Resolve has no move-track call and DeleteTrack destroys a "
            "row's clips - refusing rather than reordering a row that "
            "carries clips")
    name = tl.GetTrackName(track_type, from_index)
    if not tl.DeleteTrack(track_type, from_index):
        return False
    if not tl.AddTrack(track_type, {"index": to_index}):
        return False
    return bool(tl.SetTrackName(track_type, to_index, name))


# ── The union ──────────────────────────────────────────────────────────
#
# Every op in all five vocabularies, plus the review's missing terms.
# Grouped by family; each carries its inverse, span and read-back verify.

ALGEBRA: dict[str, AlgebraOp] = {

    # ── markers (domain: markers) ──────────────────────────────────
    "marker.add": AlgebraOp(
        id="marker.add", domain="markers",
        required=("frame", "color", "name"),
        span=lambda op, base: (int(op["frame"]),
                               int(op["frame"]) + int(op.get("duration", 1))),
        apply=lambda op, tl, _item, _project=None: tl.AddMarker(
            int(op["frame"]), op["color"], op["name"], op.get("note", ""),
            int(op.get("duration", 1)), op.get("custom_data", "")),
        verify=lambda op, after: _verify_marker_add(op, after),
        key=lambda op: ("marker", int(op["frame"])), merge="replace",
        inverse=_pair("marker.delete"),
        note="One marker per frame: a second add there replaces the first."),
    "marker.delete": AlgebraOp(
        id="marker.delete", domain="markers",
        required=("frame",),
        span=lambda op, base: (int(op["frame"]), int(op["frame"]) + 1),
        apply=lambda op, tl, _item, _project=None: tl.DeleteMarkerAtFrame(
            int(op["frame"])),
        verify=lambda op, after: _verify_marker_delete(op, after),
        key=lambda op: ("marker", int(op["frame"])), merge="commutative",
        inverse=_pair("marker.add", "re-adds the deleted marker's fields"),
        note="The inverse carries the deleted marker's color/name/note."),
    "clip_marker.add": AlgebraOp(
        id="clip_marker.add", domain="markers",
        required=("unique_id", "frame", "color", "name"),
        span=_item_span, item=True,
        apply=lambda op, _tl, item, _project=None: (
            item.AddMarker(int(op["frame"]), op["color"], op["name"],
                           op.get("note", ""),
                           int(op.get("duration", 1)), op["custom_data"])
            if op.get("custom_data") else
            item.AddMarker(int(op["frame"]), op["color"], op["name"],
                           op.get("note", ""),
                           int(op.get("duration", 1)))),
        verify=lambda op, after: _verify_clip_marker_add(op, after),
        key=lambda op: ("clip", op["unique_id"], "marker",
                        int(op["frame"])), merge="replace",
        inverse=_pair("clip_marker.delete")),
    "clip_marker.delete": AlgebraOp(
        id="clip_marker.delete", domain="markers",
        required=("unique_id", "frame"),
        span=_item_span, item=True,
        apply=lambda op, _tl, item, _project=None: item.DeleteMarkerAtFrame(
            int(op["frame"])),
        verify=lambda op, after: _verify_clip_marker_delete(op, after),
        key=lambda op: ("clip", op["unique_id"], "marker",
                        int(op["frame"])), merge="commutative",
        inverse=_pair("clip_marker.add",
                       "re-adds the deleted marker's fields")),

    # ── clip structure (domain: timeline_structure) ─────────────────
    "clip.delete": AlgebraOp(
        id="clip.delete", domain="timeline_structure",
        required=("unique_id",),
        span=_item_span, item=True,
        # Never ripple: a ripple moves every later cut, which no span the
        # patch declares can cover.
        apply=_delete_clip_with_measured_retry,
        verify=lambda op, after: _verify_delete(op, after),
        # The whole clip: it meets every write to it, in any domain.
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_pair("clip.insert",
                       "re-inserts the deleted source range"),
        note="A local delete leaves a gap; `clip.ripple` closes it."),
    "clip.insert": AlgebraOp(
        id="clip.insert", domain="timeline_structure",
        required=("unique_id", "track_type", "track_index",
                  "record_in", "record_out", "media_pool_item"),
        span=lambda op, base: (int(op["record_in"]), int(op["record_out"])),
        apply=_apply_clip_insert,
        verify=_verify_presence(True),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_pair("clip.delete"),
        note="Places a pool clip at a stated span; the inverse deletes it."),
    "clip.split": AlgebraOp(
        id="clip.split", domain="timeline_structure",
        required=("unique_id", "at_frame"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_split(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_pair("clip.join"),
        note="One clip into two at a source frame; join restores it."),
    "clip.join": AlgebraOp(
        id="clip.join", domain="timeline_structure",
        required=("unique_id", "other_unique_id"),
        span=lambda op, base: _union_span([
            _item_span({**op}, base),
            _clip(base, op["other_unique_id"]) and (
                _clip(base, op["other_unique_id"])["record_in"],
                _clip(base, op["other_unique_id"])["record_out"])]),
        verify=lambda op, after: _verify_join(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_pair("clip.split"),
        note="Two adjacent clips into one; split restores them."),
    "clip.trim": AlgebraOp(
        id="clip.trim", domain="timeline_structure",
        required=("unique_id", "in_frame", "out_frame"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_trim(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_self("the inverse carries the previous in/out points"),
        note="New in/out points on one clip; the gap is the caller's."),
    "clip.ripple": AlgebraOp(
        id="clip.ripple", domain="timeline_structure",
        required=("unique_id", "delta_frames"),
        span=lambda op, base: _ripple_span(op, base),
        verify=lambda op, after: _verify_ripple(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        temporal="ripple",
        inverse=_self("the inverse carries the opposite delta"),
        note="Shift every later clip on the track by delta frames."),
    "clip.roll": AlgebraOp(
        id="clip.roll", domain="timeline_structure",
        required=("unique_id", "delta_frames"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_source_frames(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_self("the inverse carries the negated delta"),
        note="Roll the source window; duration and position unchanged."),
    "clip.slip": AlgebraOp(
        id="clip.slip", domain="timeline_structure",
        required=("unique_id", "delta_frames"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_source_frames(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_self("the inverse carries the negated delta"),
        note="Slip the source under a fixed timeline window."),
    "clip.slide": AlgebraOp(
        id="clip.slide", domain="timeline_structure",
        required=("unique_id", "to_frame"),
        span=lambda op, base: _slide_span(op, base),
        verify=lambda op, after: _verify_slide(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        temporal="ripple",
        inverse=_self("the inverse moves the clip back"),
        note="Move the clip, rolling its neighbours to close the gaps."),
    "clip.move": AlgebraOp(
        id="clip.move", domain="timeline_structure",
        required=("unique_id", "to_frame"),
        span=lambda op, base: _slide_span(op, base),
        verify=lambda op, after: _verify_slide(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_self("the inverse moves the clip back"),
        note="An item to a different record position on the same row."),
    "clip.replace": AlgebraOp(
        id="clip.replace", domain="timeline_structure",
        required=("unique_id", "source_file"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_replace(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_self("the inverse carries the previous source"),
        note="A clip's source for another pool clip at the same span."),
    "clip.set_enabled": AlgebraOp(
        id="clip.set_enabled", domain="timeline_structure",
        required=("unique_id", "enabled"),
        span=_item_span, item=True,
        apply=lambda op, tl, item, _project=None: item.SetClipEnabled(
            bool(op["enabled"])),
        verify=lambda op, after: _verify_enabled(op, after),
        key=lambda op: ("clip", op["unique_id"], "enabled"),
        merge="replace",
        inverse=_self("the inverse carries the previous state")),
    "clip.link": AlgebraOp(
        id="clip.link", domain="timeline_structure",
        required=("unique_id", "with_unique_ids"),
        span=lambda op, base: _union_span(
            [_item_span(op, base)] +
            [_item_span({"unique_id": uid}, base)
             for uid in op["with_unique_ids"]]),
        apply=lambda op, tl, item, _project=None: tl.SetClipsLinked(
            [item] + [_live_item(tl, uid) for uid in op["with_unique_ids"]
                      if _live_item(tl, uid) is not None], True),
        verify=lambda op, after: None,
        key=lambda op: ("link", op["unique_id"]), merge="exclusive",
        item=True,
        inverse=_pair("clip.unlink"),
        note="Link clips so later edits move them together. The shadow "
             "read-back does not capture link state; the executor "
             "verifies by re-read."),
    "clip.unlink": AlgebraOp(
        id="clip.unlink", domain="timeline_structure",
        required=("unique_id",),
        span=_item_span, item=True,
        apply=lambda op, tl, item, _project=None: tl.SetClipsLinked(
            [item], False),
        verify=lambda op, after: None,
        key=lambda op: ("link", op["unique_id"]), merge="exclusive",
        inverse=_pair("clip.link")),

    # ── clip properties (domain: picture_transform) ────────────────
    "clip.smart_reframe": AlgebraOp(
        id="clip.smart_reframe", domain="picture_transform",
        required=("unique_id", "keys", "before"),
        span=_item_span, item=True,
        apply=_apply_smart_reframe,
        verify=lambda op, after: _verify_smart_reframe(op, after),
        # The native command may change any framing property on the item.
        key=lambda op: ("clip", op["unique_id"], "transform"),
        merge="exclusive",
        inverse=_pair("clip.set_property",
                       "restores the measured framing properties"),
        note="Resolve's SmartReframe; judged on its framing properties."),
    "clip.set_property": AlgebraOp(
        id="clip.set_property", domain="picture_transform",
        required=("unique_id", "key", "value"),
        span=_item_span, item=True,
        apply=_apply_set_property,
        verify=lambda op, after: _verify_property(op, after),
        key=lambda op: ("clip", op["unique_id"], op["key"]),
        merge="replace",
        inverse=_self("the inverse carries the previous value"),
        note="One transform property; the inverse restores the old value."),

    # ── retime (domain: timeline_structure) ─────────────────────────
    "retime": AlgebraOp(
        id="retime", domain="timeline_structure",
        required=("unique_id", "percent"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_retime(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        temporal="ripple",
        inverse=_self("the inverse carries the previous speed"),
        note="Constant-percent speed; a ramp reaches the timeline as "
             "stepped segments (`retime.segments`), never as a curve."),
    "retime.segments": AlgebraOp(
        id="retime.segments", domain="timeline_structure",
        required=("unique_id", "segments"),
        span=_item_span, item=True,
        apply=_apply_retime_segments,
        verify=lambda op, after: _verify_retime_segments(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        temporal="ripple",
        inverse=_self("the inverse carries the previous speed"),
        note="Stepped constant-speed segments; the measured form of a "
              "speed ramp."),

    # ── picture keyframes (domain: picture_transform) ───────────────
    "picture.keyframe": AlgebraOp(
        id="picture.keyframe", domain="picture_transform",
        required=("unique_id", "param", "keyframes"),
        span=_item_span, item=True,
        apply=_apply_picture_keyframe,
        verify=lambda op, after: _verify_picture_keyframe(op, after),
        key=lambda op: ("clip", op["unique_id"], "keyframe", op["param"]),
        merge="replace",
        inverse=_self("the inverse carries the previous keyframes"),
        note="A keyframed picture parameter, written through the "
              "transform write log."),

    # ── audio (domain: audio_mix) ───────────────────────────────────
    "voice_isolation": AlgebraOp(
        id="voice_isolation", domain="audio_mix",
        required=("track_index",),
        span=lambda op, base: _track_span(
            {**op, "track_type": "audio"}, base),
        verify=lambda op, after: None,
        key=lambda op: ("audio", "isolation", int(op["track_index"])),
        merge="exclusive",
        inverse=_self("the inverse restores the pre-isolation state"),
        note="Per-track voice isolation, replayed by the build."),
    "audio.gain": AlgebraOp(
        id="audio.gain", domain="audio_mix",
        required=("unique_id", "db"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "audio", "gain"),
        merge="replace",
        inverse=_self("the inverse carries the previous dB"),
        note="Per-clip gain in dB. No live-Resolve apply (the scripting "
             "API carries no per-clip audio surface); `otio_mix."
             "apply_audio_ops` writes it through the OTIO mix path."),
    "audio.pan": AlgebraOp(
        id="audio.pan", domain="audio_mix",
        required=("unique_id", "pan"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "audio", "pan"),
        merge="replace",
        inverse=_self("the inverse carries the previous pan"),
        note="Refuses by name: Resolve's OTIO carries no measured audio-pan "
             "parameter - the audio surface is volume only."),
    "audio.fade": AlgebraOp(
        id="audio.fade", domain="audio_mix",
        required=("unique_id", "fade_in", "fade_out"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "audio", "fade"),
        merge="replace",
        inverse=_self("the inverse carries the previous envelope"),
        note="Fade in/out with curve. No live-Resolve apply; "
             "`otio_mix.apply_audio_ops` writes it through the OTIO mix "
             "path and reads it back."),
    "audio.automation": AlgebraOp(
        id="audio.automation", domain="audio_mix",
        required=("unique_id", "param", "keyframes"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "audio", "automation",
                        op["param"]),
        merge="replace",
        inverse=_self("the inverse carries the previous keyframes"),
        note="A keyframed audio lane. No live-Resolve apply; "
             "`otio_mix.apply_audio_ops` writes it through the OTIO mix "
             "path. Only the `volume` parameter has a measured spelling."),

    # ── grade (domain: color) ────────────────────────────────────
    "clip_lut": AlgebraOp(
        id="clip_lut", domain="color",
        required=("unique_id", "lut"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "color", "lut"),
        merge="replace",
        inverse=_self("the inverse carries the previous LUT"),
        note="A node LUT on anchored clips, replayed by the build."),
    "grade": AlgebraOp(
        id="grade", domain="color",
        required=("unique_id", "grade"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "color", "grade"),
        merge="replace",
        inverse=_self("the inverse carries the previous grade"),
        note="A declared LUT/PowerGrade, replayed by the build."),
    "grade.node_add": AlgebraOp(
        id="grade.node_add", domain="color",
        required=("unique_id", "node_index"),
        span=_item_span, item=True,
        apply=_apply_grade_node_add,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["node_index"])),
        merge="exclusive",
        inverse=_pair("grade.node_remove"),
        note="GUI-only write, API-read judgement: the Color-page menu "
             "recipe presses Add Serial Node server-side, and "
             "`GetNumNodes` must grow by exactly one. The Resolve 21.1 "
             "Graph API has no AddNode (measured; `edit_ledger.py` "
             "refuses a LUT on a node outside 1..count for the same "
             "reason)."),
    "grade.node_remove": AlgebraOp(
        id="grade.node_remove", domain="color",
        required=("unique_id", "node_index"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["node_index"])),
        merge="exclusive",
        inverse=_pair("grade.node_add"),
        note="Declared, NOT executable: the Graph API has no RemoveNode. "
             "SetNodeEnabled(False) bypasses a node but does not remove "
             "it from the graph."),
    "grade.node_reorder": AlgebraOp(
        id="grade.node_reorder", domain="color",
        required=("unique_id", "from_index", "to_index"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["from_index"])),
        merge="exclusive",
        inverse=_self("the inverse swaps the nodes back"),
        note="Declared, NOT executable, BY DECISION (2026-10-07): no "
             "menu or API route moves a node - the Color menu's 160 "
             "commands (live `list_commands`) hold no move, swap, "
             "reorder, copy or paste, and the Resolve 21 manual "
             "(Ch.143) documents drag-only routes (Command-drag swaps "
             "contents; move is double-click plus E-extract plus drag "
             "to a connection line), all viewer-coordinate and "
             "foreground. A drag-based slice stays unbuilt until one "
             "is authorized; no producer emits this op today."),
    "grade.node_set": AlgebraOp(
        id="grade.node_set", domain="color",
        required=("unique_id", "node_index", "params"),
        span=_item_span, item=True,
        apply=_apply_grade_node_set,
        verify=_verify_grade_node_set,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["node_index"])),
        merge="replace",
        inverse=_self("the inverse carries the previous node grade"),
        note="SetCDL the four terms onto one named node; judged by GetCDL "
             "re-read. The only node-grade op the Resolve 21.1 API can "
             "execute - SetCDL with NodeIndex is measured, AddNode is "
             "not in the API."),

    # ── power windows (domain: color) ──────────────────────────────
    "color.window.create": AlgebraOp(
        id="color.window.create", domain="color",
        required=("unique_id", "node_index", "shape", "geometry"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=_window_create_key,
        merge="exclusive",
        inverse=_pair("color.window.delete",
                      "delete removes the window create placed"),
        note="Declared, NOT executable: the Resolve 21.1 Graph API "
             "exposes no window calls - GetNodeGraph reads topology, "
             "never a window. The window is drawn in the GUI by the "
             "recipe verb and judged on sampled frames, which land "
             "with the tracking slice."),
    "color.window.delete": AlgebraOp(
        id="color.window.delete", domain="color",
        required=("unique_id", "node_index"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "window",
                        int(op["node_index"])),
        merge="exclusive",
        inverse=_pair("color.window.create",
                      "create draws the window delete removed, with "
                      "its shape and geometry"),
        note="Declared, NOT executable: removing a window is the same "
             "GUI route as creating one. The pair exists so the undo "
             "chain terminates in the algebra."),
    "color.window.modify": AlgebraOp(
        id="color.window.modify", domain="color",
        required=("unique_id", "node_index", "geometry"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "window",
                        int(op["node_index"])),
        merge="replace",
        inverse=_self("the inverse carries the previous geometry"),
        note="Declared, NOT executable: reshaping a window is the same "
             "GUI route as creating one. The geometry is the recipe's "
             "input, opaque here; the read-back lands with the tracking "
             "slice."),
    "color.window.track": AlgebraOp(
        id="color.window.track", domain="color",
        required=("unique_id", "node_index", "direction"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=_window_track_key,
        merge="replace",
        inverse=_self("the inverse carries the previous window track"),
        note="Declared, NOT executable: the tracker buttons (Track "
             "Forward / Track Reverse) are GUI-only, and the tracked "
             "result reads back only on sampled render frames, which "
             "land with the tracking slice."),
    "color.qualifier.sample": AlgebraOp(
        id="color.qualifier.sample", domain="color",
        required=("unique_id", "node_index", "point"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=_qualifier_sample_key,
        merge="replace",
        inverse=_self("the inverse carries the previous sampled range"),
        note="Declared, NOT executable: the eyedropper pick is GUI-only "
             "and its selected range reads back nowhere in the 21.1 API. "
             "The point is the normalized frame point the recipe clicks; "
             "the sampled range judgement lands with the qualifier "
             "slice."),
    "color.qualifier.adjust": AlgebraOp(
        id="color.qualifier.adjust", domain="color",
        required=("unique_id", "node_index", "params"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "qualifier",
                        int(op["node_index"])),
        merge="replace",
        inverse=_self("the inverse carries the previous qualifier params"),
        note="Declared, NOT executable: qualifier widths and softness are "
             "Inspector controls the API does not reach. The params are "
             "the recipe's input, opaque here."),

    # ── mask (domain: fusion) ──────────────────────────────────────
    "mask.create": AlgebraOp(
        id="mask.create", domain="fusion",
        required=("unique_id", "matte"),
        span=_item_span, item=True,
        apply=_apply_mask_create,
        verify=_verify_mask_create,
        key=lambda op: ("clip", op["unique_id"], "mask", op["matte"]),
        merge="exclusive",
        inverse=_pair("mask.delete"),
        note="Place a full-frame matte comp; the matte read verifies. "
             "The `provider` names the engine: fusion_matte places the "
             "comp; resolve_magic_mask_api starts the native mask from "
             "the op's seed (True plus a gained node claims it); "
             "resolve_magic_mask_ui names the recipe verb that clicks "
             "the seed and never runs in a patch."),
    "mask.delete": AlgebraOp(
        id="mask.delete", domain="fusion",
        required=("unique_id", "matte"),
        span=_item_span, item=True,
        apply=_apply_mask_delete,
        verify=_verify_mask_delete,
        key=lambda op: ("clip", op["unique_id"], "mask", op["matte"]),
        merge="exclusive",
        inverse=_pair("mask.create",
                      "then mask.edit and mask.track restore its geometry "
                      "and keys")),
    "mask.edit": AlgebraOp(
        id="mask.edit", domain="fusion",
        required=("unique_id", "matte", "geometry"),
        span=_item_span, item=True,
        apply=_apply_mask_edit,
        verify=_verify_mask_edit,
        key=lambda op: ("clip", op["unique_id"], "mask", op["matte"]),
        merge="replace",
        inverse=_self("the inverse carries the previous geometry")),
    "mask.track": AlgebraOp(
        id="mask.track", domain="fusion",
        required=("unique_id", "matte", "track"),
        span=_item_span, item=True,
        apply=_apply_mask_track,
        verify=_verify_mask_track,
        key=lambda op: ("clip", op["unique_id"], "mask", op["matte"]),
        merge="replace",
        inverse=_self("the inverse carries the previous keyframes "
                      "(empty means none) and the previous geometry"),
        note="Key the matte's track; the keyframes read back verifies. "
             "The `provider` names the engine: fusion_matte keys the "
             "comp; resolve_magic_mask_api re-tracks through the mask "
             "engine (empty track - keyframes are the fusion "
             "vocabulary); resolve_magic_mask_ui names the recipe verb "
             "that presses the tracker and never runs in a patch."),

    # ── multicam (domain: picture_transform) ────────────────────────
    "multicam.angle": AlgebraOp(
        id="multicam.angle", domain="picture_transform",
        required=("unique_id", "angle_index"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "angle"),
        merge="replace",
        inverse=_self("the inverse carries the previous angle"),
        note="A multicam angle change; the angle list re-read verifies."),

    # ── compound (domain: timeline_structure) ────────────────────────
    "compound.create": AlgebraOp(
        id="compound.create", domain="timeline_structure",
        required=("unique_id", "child_unique_ids"),
        span=lambda op, base: _union_span(
            [_clip(base, uid) and (
                _clip(base, uid)["record_in"], _clip(base, uid)["record_out"])
             for uid in op["child_unique_ids"]]),
        verify=_verify_presence(True, "compound"),
        key=lambda op: ("compound", op["unique_id"]), merge="exclusive",
        inverse=_pair("compound.flatten"),
        note="Nest clips into one compound item; flatten restores them."),
    "compound.flatten": AlgebraOp(
        id="compound.flatten", domain="timeline_structure",
        required=("unique_id",),
        span=_item_span, item=True,
        verify=_verify_presence(False, "compound"),
        key=lambda op: ("compound", op["unique_id"]), merge="exclusive",
        inverse=_pair("compound.create")),

    # ── tracks (domain: timeline_structure) ──────────────────────────
    "track.add": AlgebraOp(
        id="track.add", domain="timeline_structure",
        required=("track_type", "name"),
        span=_timeline_span,
        apply=lambda op, tl, _item, _project=None: tl.AddTrack(
            op["track_type"], op["name"]),
        verify=lambda op, after: _verify_track_named(op["name"])(op, after),
        key=lambda op: ("track", op["track_type"], -1), merge="exclusive",
        inverse=_pair("track.delete"),
        note="A new named row; the touch-up's `add_row` views here."),
    "track.delete": AlgebraOp(
        id="track.delete", domain="timeline_structure",
        required=("track_type", "track_index"),
        span=_track_span,
        apply=lambda op, tl, _item, _project=None: tl.DeleteTrack(
            op["track_type"], int(op["track_index"])),
        verify=_verify_track_present(False),
        key=lambda op: ("track", op["track_type"],
                        int(op["track_index"])), merge="exclusive",
        inverse=_pair("track.add", "re-adds the row with its name")),
    "track.rename": AlgebraOp(
        id="track.rename", domain="timeline_structure",
        required=("track_type", "track_index", "name"),
        span=_track_span,
        apply=lambda op, tl, _item, _project=None: tl.SetTrackName(
            op["track_type"], int(op["track_index"]), op["name"]),
        verify=lambda op, after: _verify_track_name(op["name"])(op, after),
        key=lambda op: ("track", op["track_type"],
                        int(op["track_index"])), merge="replace",
        inverse=_self("the inverse carries the previous name")),
    "track.reorder": AlgebraOp(
        id="track.reorder", domain="timeline_structure",
        required=("track_type", "from_index", "to_index", "name"),
        span=_timeline_span,
        apply=_apply_track_reorder,
        verify=_verify_track_reorder,
        key=lambda op: ("track", op["track_type"],
                        int(op["from_index"])), merge="exclusive",
        inverse=_self("the inverse carries the previous index"),
        note="Move a row to a new index; the track listing verifies. A "
             "row carrying clips is refused: Resolve has no move-track "
             "call, and DeleteTrack destroys them."),

    # ── overlays / graphics (domain: fusion) ────────────────────────
    "overlay.add": AlgebraOp(
        id="overlay.add", domain="fusion",
        required=("row", "record_frame"),
        span=lambda op, base: (int(op["record_frame"]),
                               int(op["record_frame"])
                               + int(op.get("duration", 1))),
        verify=lambda op, after: None,
        key=lambda op: ("overlay", op["row"],
                        int(op["record_frame"])), merge="exclusive",
        inverse=_pair("overlay.remove"),
        note="A new overlay item on an overlay row."),
    "overlay.remove": AlgebraOp(
        id="overlay.remove", domain="fusion",
        required=("unique_id",),
        span=_item_span, item=True,
        verify=_verify_presence(False, "overlay"),
        key=lambda op: ("overlay", op["unique_id"]), merge="exclusive",
        inverse=_pair("overlay.add", "re-places the removed item")),
    "overlay.swap_pixels": AlgebraOp(
        id="overlay.swap_pixels", domain="fusion",
        required=("unique_id", "file"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("overlay", op["unique_id"]), merge="replace",
        inverse=_self("the inverse carries the previous file"),
        note="An overlay's pixels for a re-rendered file."),
    "overlay.resize": AlgebraOp(
        id="overlay.resize", domain="fusion",
        required=("unique_id", "width", "height"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("overlay", op["unique_id"]), merge="replace",
        inverse=_self("the inverse carries the previous size")),
    "overlay.entry_motion": AlgebraOp(
        id="overlay.entry_motion", domain="fusion",
        required=("unique_id", "frames"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("overlay", op["unique_id"]), merge="replace",
        inverse=_self("the inverse clears the animation")),

    # ── projected captain edits (domain: picture_transform) ─────────
    "transform_override": AlgebraOp(
        id="transform_override", domain="picture_transform",
        required=("unique_id", "key", "value"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_property(op, after),
        key=lambda op: ("clip", op["unique_id"], op["key"]),
        merge="replace",
        inverse=_self("the inverse carries the previous value"),
        note="Hold one Edit-page transform property."),
    "span_retime": AlgebraOp(
        id="span_retime", domain="timeline_structure",
        required=("unique_id", "percent"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_retime(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        temporal="ripple",
        inverse=_self("the inverse carries the previous ranges")),
    "drop_fragment": AlgebraOp(
        id="drop_fragment", domain="timeline_structure",
        required=("unique_id",),
        span=_item_span, item=True,
        verify=_verify_presence(False),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_pair("clip.insert",
                       "re-inserts the dropped passage"),
        note="Remove the speech (and so the picture) that says it."),
    "caption_fix": AlgebraOp(
        id="caption_fix", domain="captions",
        required=("unique_id", "text"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "caption"),
        merge="replace",
        inverse=_self("the inverse carries the previous text"),
        note="Rewrite caption text."),
    "redraw_closer": AlgebraOp(
        id="redraw_closer", domain="timeline_structure",
        required=("unique_id", "start"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_self("the inverse carries the previous start"),
        note="Move a shared closer's start to the anchor's words."),

    # ── plan-level ops (delivered to planning nodes) ────────────────
    #
    # These are not timeline writes; a `plan_change` row is delivered to
    # the owning planner. Their inverse is the previous plan - semantic
    # undo (C-04) - so they are declared irreversible here with the reason.
    "angle_plan": AlgebraOp(
        id="angle_plan", domain="picture_transform",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "angle_plan"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse; its "
                       "inverse is the previous plan (C-04)"),
        note="Shapes the picture rows; delivered to the planner."),
    "plan_change": AlgebraOp(
        id="plan_change", domain="timeline_structure",
        required=("node", "note"),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "plan_change"), merge="ordered",
        inverse=_never("a carrier delivered to a planning node; its "
                       "inverse is the previous plan (C-04)"),
        note="A plan-level change delivered to its owning step."),
    "transition": AlgebraOp(
        id="transition", domain="fusion",
        required=("carrier", "transition"),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "transition"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "shot_selection": AlgebraOp(
        id="shot_selection", domain="timeline_structure",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "shot_selection"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "speech_selection": AlgebraOp(
        id="speech_selection", domain="timeline_structure",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "speech_selection"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "story_pacing": AlgebraOp(
        id="story_pacing", domain="timeline_structure",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "story_pacing"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "music_selection": AlgebraOp(
        id="music_selection", domain="audio_mix",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "music_selection"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "audio_mix": AlgebraOp(
        id="audio_mix", domain="audio_mix",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "audio_mix"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "look": AlgebraOp(
        id="look", domain="color",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "look"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "visual_effect": AlgebraOp(
        id="visual_effect", domain="fusion",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "visual_effect"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "sound_effect": AlgebraOp(
        id="sound_effect", domain="audio_mix",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "sound_effect"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "subject_effect": AlgebraOp(
        id="subject_effect", domain="fusion",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "subject_effect"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "motion_graphic": AlgebraOp(
        id="motion_graphic", domain="fusion",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "motion_graphic"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "brand_asset": AlgebraOp(
        id="brand_asset", domain="fusion",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "brand_asset"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
    "end_card": AlgebraOp(
        id="end_card", domain="fusion",
        required=("plan",),
        span=lambda op, base: None,
        verify=lambda op, after: None,
        key=lambda op: ("plan", "end_card"), merge="ordered",
        inverse=_never("a planner re-run has no timeline inverse (C-04)")),
}


# ── Verify functions for the structural ops ───────────────────────────
#
# Each judges an after-snapshot: did the op's effect land? They are pure
# reads of the shadow snapshot, so they are testable without Resolve.


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


def _verify_split(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    at = int(op["at_frame"])
    if int(clip["record_out"]) != at:
        return (f"clip {op['unique_id']!r} reads record_out="
                f"{clip['record_out']}, not {at}: the split did not trim "
                f"it to the first half")
    return None


def _verify_join(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    other = _clip(after, op["other_unique_id"])
    if other is not None:
        return (f"clip {op['other_unique_id']!r} still reads back: "
                f"the join did not absorb it")
    return None


def _verify_trim(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    if (int(clip["record_in"]) != int(op["in_frame"])
            or int(clip["record_out"]) != int(op["out_frame"])):
        return (f"clip {op['unique_id']!r} reads "
                f"{clip['record_in']}..{clip['record_out']}, not "
                f"{op['in_frame']}..{op['out_frame']}")
    return None


def _ripple_span(op, base):
    clip = _clip(base, op["unique_id"])
    if clip is None:
        return None
    return (int(clip["record_in"]), int(base["end_frame"]))


def _verify_ripple(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    return None


def _verify_roll(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    if int(clip["duration"]) <= 0:
        return f"clip {op['unique_id']!r} reads a non-positive duration"
    return None


def _verify_slip(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    return None


def _verify_source_frames(op, after):
    """Roll/slip: the source window reads back at the declared frames.

    The op carries the expected `source_in`/`source_out`; without them the
    read-back cannot judge (the shadow holds no source-window baseline).
    """
    if "source_in" not in op or "source_out" not in op:
        return None
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    if (int(clip["source_in_frame"]) != int(op["source_in"])
            or int(clip["source_out_frame"]) != int(op["source_out"])):
        return (f"clip {op['unique_id']!r} reads source "
                f"{clip['source_in_frame']}..{clip['source_out_frame']}, "
                f"not {op['source_in']}..{op['source_out']}")
    return None


def _slide_span(op, base):
    clip = _clip(base, op["unique_id"])
    if clip is None:
        return None
    start = min(int(clip["record_in"]), int(op["to_frame"]))
    end = max(int(clip["record_out"]),
              int(op["to_frame"]) + int(clip["duration"]))
    return (start, end)


def _verify_slide(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    if int(clip["record_in"]) != int(op["to_frame"]):
        return (f"clip {op['unique_id']!r} reads record_in="
                f"{clip['record_in']}, not {op['to_frame']}")
    return None


def _verify_replace(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    if clip.get("source_file") != op["source_file"]:
        return (f"clip {op['unique_id']!r} reads source "
                f"{clip.get('source_file')!r}, not {op['source_file']!r}")
    return None


def _verify_retime(op, after):
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    if "duration" in op and int(clip["duration"]) != int(op["duration"]):
        return (f"clip {op['unique_id']!r} reads duration="
                f"{clip['duration']}, not {op['duration']}")
    return None


def _verify_retime_segments(op, after):
    """Each piece of the split clip reads back at its declared speed."""
    segments = sorted(op["segments"], key=lambda s: int(s["record_frame"]))
    prefix = op["unique_id"] + "-split-"
    pieces = [c for c in shadow.clips(after)
              if c["unique_id"] == op["unique_id"]
              or c["unique_id"].startswith(prefix)]
    pieces.sort(key=lambda c: int(c["record_in"]))
    if len(pieces) != len(segments):
        return (f"clip {op['unique_id']!r} reads {len(pieces)} pieces, "
                f"not {len(segments)}")
    for index, (piece, segment) in enumerate(zip(pieces, segments)):
        speed = piece.get("speed")
        if speed is None:
            return (f"piece {index} of clip {op['unique_id']!r} reads "
                    f"no speed")
        if abs(float(speed) - float(segment["percent"])) > FLOAT_TOLERANCE:
            return (f"piece {index} of clip {op['unique_id']!r} reads "
                    f"speed={speed}, not {segment['percent']}")
    return None


def _verify_picture_keyframe(op, after):
    """Keyframes read back at the declared frames with the declared values."""
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    keyframes = (clip.get("keyframes") or {}).get(op["param"], [])
    read_back = {int(kf["frame"]): kf for kf in keyframes}
    for keyframe in op["keyframes"]:
        frame = int(keyframe["frame"])
        if frame not in read_back:
            return (f"no keyframe reads back on {op['unique_id']!r} for "
                    f"{op['param']!r} at frame {frame}")
        got = read_back[frame].get("value")
        if not _same_value(got, keyframe["value"]):
            return (f"keyframe on {op['unique_id']!r} for {op['param']!r} "
                    f"at frame {frame} reads value={got!r}, "
                    f"not {keyframe['value']!r}")
    return None


def _live_item(timeline, unique_id):
    for item in _live_handles(timeline).values():
        if str(item.GetUniqueId()) == str(unique_id):
            return item
    return None


# ── The five vocabularies as views over the algebra ────────────────────
#
# Each existing vocabulary keeps its own names; every name resolves to an
# algebra id. A name that resolves to nothing is a vocabulary that drifted
# from the union - the contract test fails on it.

#: `edit_ledger.OPS` - the ledger's complete vocabulary.
LEDGER_OPS = (
    "voice_isolation", "clip_lut", "grade",
    "angle_plan", "retime",
    "transform_override", "span_retime", "drop_fragment", "caption_fix",
    "redraw_closer",
    "plan_change",
)

#: `edit_operations.PLAN_OPERATION_OWNERS` - the plan-level ops.
PLAN_OPS = (
    "transition", "shot_selection", "speech_selection", "story_pacing",
    "music_selection", "audio_mix", "look", "visual_effect", "sound_effect",
    "subject_effect", "motion_graphic", "brand_asset", "end_card",
)

#: The touch-up's ten ops, each resolving to an algebra id. `retime`,
#: `set_properties`, `set_enabled` and `add_row` alias ops the other
#: vocabularies already name; the rest are the touch-up's own.
TOUCHUP_VIEW = {
    "move": "clip.move",
    "swap_pixels": "overlay.swap_pixels",
    "add_overlay": "overlay.add",
    "remove_overlay": "overlay.remove",
    "retime": "retime",
    "resize_still": "overlay.resize",
    "set_properties": "clip.set_property",
    "add_row": "track.add",
    "set_enabled": "clip.set_enabled",
    "entry_motion": "overlay.entry_motion",
}

#: `captain_edits.KINDS` - every kind is a ledger op.
CAPTAIN_VIEW = {
    "caption_fix": "caption_fix",
    "drop_fragment": "drop_fragment",
    "redraw_closer": "redraw_closer",
    "transform_override": "transform_override",
    "span_retime": "span_retime",
}

#: The review's algebra terms, each mapping onto an algebra id. An editor
#: instruction using a term with no id cannot become a precise reversible
#: operation - the contract test fails on the missing ones.
REVIEW_TERMS = {
    "insert": "clip.insert",
    "delete": "clip.delete",
    "split": "clip.split",
    "join": "clip.join",
    "trim": "clip.trim",
    "ripple": "clip.ripple",
    "roll": "clip.roll",
    "slip": "clip.slip",
    "slide": "clip.slide",
    "move": "clip.move",
    "replace": "clip.replace",
    "enable": "clip.set_enabled",
    "disable": "clip.set_enabled",
    "link": "clip.link",
    "unlink": "clip.unlink",
    "track_add": "track.add",
    "track_delete": "track.delete",
    "track_rename": "track.rename",
    "track_reorder": "track.reorder",
    "clip_properties": "clip.set_property",
    "retime": "retime",
    "retime_curves": "retime.segments",
    "keyframes": "picture.keyframe",
    "transitions": "transition",
    "compositing": "compound.create",
    "crop": "clip.set_property",
    "transform": "clip.set_property",
    "reframe": "clip.smart_reframe",
    "mask": "mask.create",
    "mask_track": "mask.track",
    "audio_gain": "audio.gain",
    "audio_pan": "audio.pan",
    "audio_fades": "audio.fade",
    "audio_automation": "audio.automation",
    "grade_nodes": "grade.node_set",
    "captions": "caption_fix",
    "graphics": "overlay.add",
    "markers": "marker.add",
    "multicam_angle": "multicam.angle",
    "compound": "compound.create",
    "nested": "compound.create",
}


def ops() -> list[AlgebraOp]:
    """Every op in the union, in declaration order."""
    return list(ALGEBRA.values())


def get(op_id: str) -> AlgebraOp | None:
    """The algebra op for an id, or None."""
    return ALGEBRA.get(op_id)


def patch_op_ids() -> tuple[str, ...]:
    """The ids EditPatch may schedule: every op with a native apply."""
    return tuple(op_id for op_id, op in ALGEBRA.items()
                 if op.apply is not None)


# ── The backend switch ──────────────────────────────────────────────
#
# The computational core (span, key, verify) is ported to Rust
# (`native/ren-core`, the `ren_native` extension). One switch
# (`library/tools/edit_backend.py`) routes these through it; Python is
# the default and the fallback. The declarations above (`ALGEBRA`) stay
# the Python source of truth - the parity harness compares the two.


def span(op, base):
    """The frames an op touches over a base snapshot, on the active backend."""
    rust = edit_backend.rust()
    if rust is not None:
        return rust.span(op, base)
    spec = ALGEBRA.get(op.get("op"))
    if spec is None:
        return None
    return spec.span(op, base)


def key(op):
    """What an op writes, as a key, on the active backend."""
    rust = edit_backend.rust()
    if rust is not None:
        return rust.key(op)
    spec = ALGEBRA.get(op.get("op"))
    if spec is None:
        return ()
    return spec.key(op)


def _beyond_ported_core(op) -> bool:
    """Whether an op's judgement lives outside the ported core.

    `native/ren-core` models the Fusion engine: matte comps, keys,
    geometry. A mask op naming a Magic Mask provider is judged by a
    different engine (the Color-page node graph, read at the live
    handles by the apply) - routing it to the port would judge it by
    matte comps it never places. Such ops stay on the Python verify
    whatever the backend switch says; removing this carve-out is part
    of porting the providers to Rust, not a cleanup.
    """
    return ((op.get("op") or "") in ("mask.create", "mask.delete",
                                     "mask.edit", "mask.track")
            and op.get("provider", "fusion_matte") != "fusion_matte")


def verify(op, after):
    """The read-back judgement of one op, on the active backend."""
    if _beyond_ported_core(op):
        return _verify_magic_mask_provider(op, after)
    rust = edit_backend.rust()
    if rust is not None:
        return rust.verify(op, after)
    spec = ALGEBRA.get(op.get("op"))
    if spec is None:
        return None
    return spec.verify(op, after)
