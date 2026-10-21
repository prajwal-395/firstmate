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
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace

from library.tools import edit_backend
from library.tools import timeline_shadow as shadow
from library.tools.axi_qualification import load_qualification_records

#: How close a re-read float must be to what was written.
FLOAT_TOLERANCE = 1e-6
SMART_REFRAME_KEYS = ("Pan", "Tilt", "ZoomX", "ZoomY")
_clip_index: ContextVar[tuple | None] = ContextVar("clip_index", default=None)


@contextlib.contextmanager
def indexed_snapshot(snapshot):
    index = {}
    for clip in shadow.clips(snapshot):
        uid = clip["unique_id"]
        index[uid] = None if uid in index else clip
    token = _clip_index.set((snapshot, index))
    try:
        yield
    finally:
        _clip_index.reset(token)


# ── Reading a snapshot ─────────────────────────────────────────────────
#
# The shadow's clip dicts (`reel_read` QUICK mode): unique_id, record_in,
# record_out, duration, transform, enabled, markers, track_type,
# track_index, name. Tracks: snapshot["tracks"] = [{"type", "index",
# "name", "clips"}, ...].


def _clip(snapshot: dict, unique_id: str) -> dict | None:
    indexed = _clip_index.get()
    if indexed is not None and indexed[0] is snapshot:
        return indexed[1].get(unique_id)
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


def _verify_caption_fix(op, after):
    """One fixed caption card reads back on its own span.

    The TEXT is judged by the acceptance contract's rendered evidence -
    the shadow carries no caption text, and a snapshot judge that
    scored the words would be the pipeline inventing them (AGENTS.md
    10.5). This verifier refuses what the snapshot CAN judge: a card
    the write lost, and a timing the write moved.
    """
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    for key in ("record_in", "record_out"):
        if key in op and int(clip[key]) != int(op[key]):
            return (f"clip {op['unique_id']!r} reads {key}={clip[key]}, "
                    f"not {op[key]}: a caption fix must not move "
                    f"the timing")
    return None


def _verify_caption_style(op, after):
    """One restyled caption reads back on its own span under a new file.

    The style itself is judged by the acceptance contract's rendered
    evidence - the shadow carries no style field, and a snapshot judge
    that scored taste would be the pipeline inventing it (AGENTS.md
    10.5). This verifier refuses what the snapshot CAN judge: a caption
    the write lost, and a timing the write moved.
    """
    clip = _clip(after, op["unique_id"])
    if clip is None:
        return f"clip {op['unique_id']!r} is not on the timeline"
    for key in ("record_in", "record_out"):
        if key in op and int(clip[key]) != int(op[key]):
            return (f"clip {op['unique_id']!r} reads {key}={clip[key]}, "
                    f"not {op[key]}: a style change must not move "
                    f"the timing")
    return None


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
    except Exception as exc:
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
    except Exception as exc:
        raise RuntimeError(
            f"grade.node_set SetCDL raised ({exc}) - verify by hand.") \
            from exc
    if not wrote:
        raise RuntimeError(
            f"grade.node_set SetCDL(node {node_index}) answered False - "
            f"nothing was claimed.")
    try:
        back = dict(item.GetCDL() or {})
    except Exception as exc:
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
    except Exception as exc:
        raise RuntimeError(
            f"{label} cannot open the node graph ({exc}) - "
            f"nothing was written.") from exc
    if isinstance(count, bool) or not isinstance(count, int):
        raise RuntimeError(  # noqa: TRY004 - invalid API result is a refusal
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
        grade_node_add_script,
        run_script,
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


def _park_playhead_mid_clip(op, timeline, item,
                         label="grade.node_add") -> None:
    """Park the playhead mid-clip and read the park back, or refuse.

    The menus act on the current clip, so the playhead is parked on
    the op's clip first. One retry: the first set after a timeline
    switch can miss (measured by the batch-5 lane). A playhead that
    will not park refuses rather than landing the write on whatever
    clip happens to be current.
    """
    from library.tools.marker_feedback import frames_to_timecode

    try:
        start, end = int(item.GetStart()), int(item.GetEnd())
        fps = float(timeline.GetSetting("timelineFrameRate") or 24)
    except Exception as exc:
        raise RuntimeError(
            f"{label} cannot read clip {op.get('unique_id')!r}'s "
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
    except Exception as exc:
        raise RuntimeError(
            f"{label} could not park the playhead on clip "
            f"{op.get('unique_id')!r} ({exc}) - nothing was written."
            ) from exc


#: The node cache modes writable through the API, measured live: 0
#: and 1 write and read back, 2 is refused, -1 is the default. The
#: labels a topology apply writes to tell its nodes apart - and
#: restores afterwards, so the graph the run leaves matches the one
#: it found except for the topology change itself.
CACHE_LABEL_MODES = (0, 1, -1)


def _read_cache_modes(item, count, label) -> list:
    """Every node's cache mode through the API, or a refusal."""
    try:
        graph = item.GetNodeGraph()
        return [graph.GetNodeCacheMode(index)
                for index in range(1, count + 1)]
    except Exception as exc:
        raise RuntimeError(
            f"{label} cannot read the node cache modes ({exc}) - "
            f"nothing was written.") from exc


def _write_cache_modes(item, modes, label) -> None:
    """Write one cache mode per node and read the write back."""
    try:
        graph = item.GetNodeGraph()
        for position, mode in enumerate(modes):
            if not graph.SetNodeCacheMode(position + 1, mode):
                raise RuntimeError(
                    f"SetNodeCacheMode({position + 1}, {mode}) "
                    f"answered False")
        back = [graph.GetNodeCacheMode(position + 1)
                for position in range(len(modes))]
    except Exception as exc:
        raise RuntimeError(
            f"{label} cannot write the node cache modes ({exc}) - "
            f"nothing was claimed.") from exc
    if back != list(modes):
        raise RuntimeError(
            f"{label} cache modes read back {back}, not {list(modes)} - "
            f"nothing was claimed.")


def _apply_grade_node_remove(op, timeline, item, _project=None):
    """Remove ONE node through the Color-page menu recipe, judged.

    The Graph API exposes no RemoveNode (measured), so the write is
    GUI-only. Stepping wraps around the ends with no selection
    readout (both measured live), so the recipe adds a marker serial
    first - a just-added node is selected, the one known start -
    steps to the target, deletes it, steps to the marker, deletes
    that too (`computer_use.scripts` stage builders - server-side
    menu commands with settles, no viewer coordinate, no keystroke).
    Each stage is judged separately through the API: the count must
    move by exactly one AND the removed position's label must be
    gone. The apply labels the graph with cache modes first and
    restores the originals (minus the target) after, so a delete
    that landed on the wrong node refuses instead of claiming the
    target.
    """
    from library.tools.computer_use.scripts import (
        _marker_add_script,
        _remove_marker_script,
        _remove_target_script,
    )

    label = "grade.node_remove"
    try:
        node_index = int(op["node_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"{label} needs an integer node_index ({exc}) - nothing "
            f"was written.") from exc
    before = _read_node_count(item, label)
    if not 1 <= node_index <= before:
        raise RuntimeError(
            f"{label} names node {node_index}, outside 1..{before} - "
            f"nothing was written.")
    _park_playhead_mid_clip(op, timeline, item, label)
    original_modes = _read_cache_modes(item, before, label)
    client = ui_driver_factory()
    try:
        # Stage one: the marker. A just-added node is selected - the
        # one known start stepping has.
        _run_remove_stage(client, _marker_add_script(), "add the marker")
        marked = _read_node_count(item, label)
        if marked != before + 1:
            raise RuntimeError(
                f"the marker add left {marked} nodes, not "
                f"{before + 1} - nothing was deleted.")
        # The marker is unlabeled stock: the originals plus its default.
        with_marker = original_modes + [-1]
        stratified = [CACHE_LABEL_MODES[(position - 1)
                                        % len(CACHE_LABEL_MODES)]
                      for position in range(1, marked + 1)]
        _write_cache_modes(item, stratified, label)
        # Stage two: the target.
        _run_remove_stage(client, _remove_target_script(
            node_index, before), "take the target")
        mid = _read_node_count(item, label)
        try:
            survivors = _read_cache_modes(item, mid, label)
        except RuntimeError:
            survivors = None
        expected = stratified[:node_index - 1] + stratified[node_index:]
        if mid != before or survivors != expected:
            _restore_labels_by_survivors(item, stratified, with_marker,
                                         survivors, label)
            raise RuntimeError(
                f"{label} nodes {before} -> {marked} -> {mid}, "
                f"survivors {survivors} (expected {expected}) - the "
                f"target delete did not land as planned. Re-plan "
                f"against the live graph.")
        # Stage three: the marker. Current sits at the target's old
        # position with the marker last.
        stage_two_labels = expected
        stage_two_originals = with_marker[:node_index - 1] \
            + with_marker[node_index:]
        _run_remove_stage(client, _remove_marker_script(
            node_index, before), "clear the marker")
        after = _read_node_count(item, label)
        try:
            final = _read_cache_modes(item, after, label)
        except RuntimeError:
            final = None
        if after != before - 1 or final != stage_two_labels[:-1]:
            _restore_labels_by_survivors(item, stage_two_labels,
                                         stage_two_originals, final, label)
            raise RuntimeError(
                f"{label} nodes {before} -> {marked} -> {mid} -> "
                f"{after}, survivors {final} (expected "
                f"{stage_two_labels[:-1]}) - the marker delete did "
                f"not land as planned. Re-plan against the live graph.")
        _write_cache_modes(item,
                           [m for position, m in enumerate(original_modes, 1)
                            if position != node_index], label)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()
    return True


def _run_remove_stage(client, script, stage) -> None:
    """Run one remove stage; a step that does not land refuses."""
    from library.tools.computer_use.scripts import run_settled_script

    result = run_settled_script(client, script)
    failed = [step for step in result.steps
              if step.get("outcome") != "ok"]
    if result.outcome != "done" or failed:
        raise RuntimeError(
            f"grade.node_remove UI recipe did not land to {stage} "
            f"({result.outcome}; {len(failed)} failed step(s)) - "
            f"nothing was claimed.")


def _restore_labels_by_survivors(item, labeled, originals, survivors,
                                 label) -> bool:
    """Restore labels by identifying which position died.

    `labeled` is what the graph carried (len L), `originals` what
    those positions held before (len L), `survivors` what the graph
    carries now. Where the survivors equal the labels minus exactly
    one position, that position's originals go back onto the rest.
    Returns whether a position matched - no match leaves the labels
    alone, because a wrong restore would be a second unmeasured
    write.
    """
    if survivors is None or len(survivors) != len(labeled) - 1:
        return False
    for died in range(1, len(labeled) + 1):
        if survivors == labeled[:died - 1] + labeled[died:]:
            with contextlib.suppress(Exception):
                _write_cache_modes(item,
                                   originals[:died - 1]
                                   + originals[died:], label)
            return True
    return False


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
    except Exception:  # noqa: BLE001 - third-party Resolve methods vary by version
        return default


def _fusion_media_dimensions(item):
    """Read the dimensions Fusion receives, refusing to guess a canvas."""
    pool_item = _call(item, "GetMediaPoolItem", None)
    if not pool_item:
        raise RuntimeError("mask operation cannot resolve its media pool item")

    resolution = None
    try:
        resolution = pool_item.GetClipProperty("Resolution")
    except Exception:  # noqa: BLE001 - Resolve facade versions vary
        resolution = None
    if resolution and "x" in str(resolution).lower():
        try:
            width, height = (int(part.strip())
                             for part in str(resolution).lower().split("x", 1))
        except (TypeError, ValueError):
            width = height = 0
    else:
        try:
            width = int(pool_item.GetClipProperty("Width") or 0)
            height = int(pool_item.GetClipProperty("Height") or 0)
        except (AttributeError, TypeError, ValueError):
            width = height = 0
    if width <= 0 or height <= 0:
        raise RuntimeError(
            "mask operation cannot read positive Fusion media dimensions; "
            "refusing to build a comp with guessed dimensions")
    return width, height


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

    def pool_int(key, default):
        pool_item = _call(item, "GetMediaPoolItem", None)
        try:
            return int(pool_item.GetClipProperty(key)) or default
        except Exception:  # noqa: BLE001 - third-party Resolve methods vary by version
            return default

    width, height = _fusion_media_dimensions(item)
    engine = CompEngine(clip_dur=pool_int("Frames", 100),
                        width=width, height=height)

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
        raise RuntimeError(  # noqa: TRY004 - missing domain receipt refusal
            f"{label}: provider 'resolve_magic_mask_api' needs 'seed', "
            f"the MaskSeed receipt whose viewer point the UI phase "
            f"clicked - an unseeded call answers False")
    viewer = seed.get("viewer") or {}
    if not isinstance(viewer.get("x"), (int, float)) or not isinstance(
            viewer.get("y"), (int, float)):
        raise RuntimeError(  # noqa: TRY004 - malformed receipt refusal
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
    from library.tools.magic_mask import source_frame_range_for_item

    seed = op.get("seed") or {}
    requested_range = (op.get("source_frame_range")
                       or seed.get("source_frame_range"))
    if requested_range is None:
        raise RuntimeError(
            f"{label}: provider 'resolve_magic_mask_api' needs an "
            "explicit source_frame_range from the trimmed timeline item")
    source_frame_range_for_item(
        item, requested_range, seed_frame=seed.get("seed_frame"))
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

    def pool_int(key, default):
        pool_item = _call(item, "GetMediaPoolItem", None)
        try:
            return int(pool_item.GetClipProperty(key)) or default
        except Exception:  # noqa: BLE001 - third-party Resolve methods vary by version
            return default

    width, height = _fusion_media_dimensions(item)
    content = CompEngine(clip_dur=pool_int("Frames", 100),
                         width=width, height=height).serialize()
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


def _swapback_fusion_keys(item, moves, label):
    """Move bound Fusion keys to their remapped frames, judged at once.

    ``moves`` are ``{"comp", "tool", "param", "from_frame", "to_frame",
    "value"}`` on the NEW render clock - already translated through
    both frame maps by ``resolve_swapback.plan``. The write primitive
    (live key set + old-key removal) is the one the Unit 5 probe proved
    on Resolve 21.1; every move reads back before this returns, and a
    key that does not read back raises instead of leaving two truths.
    """
    from library.tools import resolve_swapback

    resolve_swapback.move_fusion_keys(item, moves, label)
    return True


def _apply_replace(op, _timeline, item, _project=None):
    """Swap the item's source file, carrying its bound keys and grade.

    ``MediaPoolItem.ReplaceClip`` preserves the grade and Fusion comps
    structurally but leaves their keys where they were - the measured
    Unit 5 failure. So the op carries the remapped keys
    (``fusion_keys``, on the new render clock) and the grade assertion
    (``expected_grade``) with the binding, and all three land or the op
    raises: ReplaceClip, key moves, then one read-back over source,
    keys and grade. The patch verify re-judges the source from the
    snapshot; ``resolve_swapback.accept`` judges the rest live.
    """
    pool_item = _call(item, "GetMediaPoolItem", None)
    if pool_item is None:
        raise RuntimeError(
            f"clip.replace: clip {op.get('unique_id')!r} exposes no media "
            f"pool item; refusing to re-point what cannot be read back")
    try:
        replaced = pool_item.ReplaceClip(op["source_file"])
    except Exception as exc:
        raise RuntimeError(
            f"clip.replace: ReplaceClip({op['source_file']!r}) raised "
            f"({exc})") from exc
    if not replaced:
        raise RuntimeError(
            f"clip.replace: ReplaceClip({op['source_file']!r}) returned "
            f"False; nothing was re-pointed")
    moves = op.get("fusion_keys") or []
    if moves:
        _swapback_fusion_keys(item, moves, "clip.replace")
    try:
        back_item = pool_item.GetClipProperty("File Path")
    except Exception as exc:
        raise RuntimeError(
            f"clip.replace: the pool item would not re-read "
            f"({exc})") from exc
    if back_item != op["source_file"]:
        raise RuntimeError(
            f"clip.replace: pool item reads {back_item!r}, not "
            f"{op['source_file']!r}")
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


def _remove_clip_with_measured_retry(op, timeline, item, _project=None):
    """Refuse until gap closure can be scoped to the target track.

    Resolve's ``DeleteClips(..., True)`` ripples across tracks. That can
    move or remove speech and overlays outside ``clip.remove``'s declared
    target track, so an operation that promises a track-scoped ripple
    cannot use this timeline-wide primitive. Keep the refusal before the
    first write; ``clip.delete`` is the explicit local-delete operation.
    """
    del op, timeline, item, _project
    raise RuntimeError(
        "clip.remove track-scoped gap closure is unavailable: Resolve's "
        "native ripple is timeline-wide; use clip.delete only when a "
        "local deletion is what was requested")


_insert_sources: ContextVar[dict | None] = ContextVar("insert_sources", default=None)


@contextlib.contextmanager
def insertion_sources():
    """One lazy pool-id index for a patch, discarded before imports can stale it."""
    token = _insert_sources.set({})
    try:
        yield
    finally:
        _insert_sources.reset(token)


def _find_pool_item_by_id(pool, unique_id):
    """The pool item holding *unique_id*, searched through every folder.

    A placement addresses its source by the pool item's unique id - the
    same identity `pool_item_for` matches on, so a reel's overlay never
    pairs with another reel's file of the same name.
    """
    cache = _insert_sources.get()
    if cache is not None and "items" in cache:
        return cache["items"].get(str(unique_id))
    indexed = {}
    stack = [pool.GetRootFolder()]
    while stack:
        folder = stack.pop()
        for item in folder.GetClipList() or ():
            identity = str(item.GetUniqueId())
            if cache is not None:
                indexed.setdefault(identity, item)
            elif identity == str(unique_id):
                return item
        stack.extend(folder.GetSubFolderList() or ())
    if cache is not None:
        cache["items"] = indexed
    return indexed.get(str(unique_id))


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


def _apply_track_delete(op, tl, _item, _project=None):
    """Delete a row, but never one that still carries clips.

    Resolve's ``DeleteTrack`` destroys every clip on the row with no
    undo of its own. A row removal that took the clips with it would
    destroy editorial work the request never named, so the row is
    refused while it holds anything. The refusal happens BEFORE the
    write, so a refused removal leaves the timeline exactly as it was.
    """
    track_type = str(op["track_type"])
    track_index = int(op["track_index"])
    try:
        items = tl.GetItemListInTrack(track_type, track_index) or ()
    except Exception as exc:
        raise RuntimeError(
            f"track.delete cannot read {track_type}{track_index} ({exc}) - "
            "nothing was deleted.") from exc
    if items:
        raise RuntimeError(
            f"track.delete refuses to delete {track_type}{track_index}: it "
            f"carries {len(items)} clip(s). Resolve's DeleteTrack destroys "
            "them, and a row removal that took editorial work with it would "
            "destroy work the request never named. Remove or move the "
            "clips first, then remove the row.")
    return tl.DeleteTrack(track_type, track_index)


def _apply_track_add(op, tl, _item, _project=None):
    """Append a row, then name it using Resolve's measured API surface."""
    track_type = op["track_type"]
    before = int(tl.GetTrackCount(track_type) or 0)
    index = before + 1
    if not tl.AddTrack(track_type):
        return False
    if int(tl.GetTrackCount(track_type) or 0) != index:
        return False
    return bool(tl.SetTrackName(track_type, index, op["name"]))


# ── Reel opening (a planning-level structural op) ───────────────────
#
# Tightening an opening to a supplied brief while preserving the
# speaker's meaning and approved edits. The op carries the model's
# DECIDED edits - the adapter never invents trim points - and compiles
# them to existing structural edits through EditRequest. `clip.delete`
# is the remove primitive; a sibling `clip.remove` from the
# ordinary-request vocabulary is deliberately not referenced, so this
# op stays independent of the lane that owns it.


#: Structural edits a `reel.opening` may carry. `clip.remove` is the
#: verified ripple-delete operation; local `clip.delete` would leave a
#: gap and does not satisfy an opening tighten.
REEL_OPENING_STRUCTURAL_OPS = ("clip.trim", "clip.remove")


def _reel_opening_edit_list(op) -> list:
    """The op's carried edits as plain dicts; anything else reads as none.

    Total: span and verify judge read-backs, they never raise, so an
    op carrying no readable edits fails closed as "no structural edits"
    instead of throwing mid-judgement.
    """
    edits = op.get("edits")
    if not isinstance(edits, (list, tuple)):
        return []
    return [dict(edit) for edit in edits if isinstance(edit, dict)]


def _reel_opening_span(op, base):
    """The frames the carried edits touch: the union of their item spans.

    Each edit is judged by the clip it names on the base snapshot, the
    same read the nested structural verifiers judge afterwards. A ripple
    edit spans its item here, not to the timeline end: this span covers
    what the op touches, while the ripple's own propagation is the
    reason the op is temporally a ripple and stays staged.
    """
    return _union_span([
        _item_span({"unique_id": edit.get("unique_id")}, base)
        if edit.get("unique_id") else None
        for edit in _reel_opening_edit_list(op)])


def _verify_reel_opening(op, after):
    """Every carried structural edit reads back on the after-snapshot.

    Each edit is judged by its own structural verifier - a trim by its
    declared in/out, a delete by its absence - so the opening's
    read-back postconditions are exactly the postconditions of the
    edits it compiled to. The first failing edit names itself; wording
    is diagnostic, None-ness is the verdict.
    """
    edits = _reel_opening_edit_list(op)
    if not edits:
        return "reel.opening carries no structural edits to read back"
    for index, edit in enumerate(edits):
        edit_id = edit.get("op")
        if edit_id not in REEL_OPENING_STRUCTURAL_OPS:
            return (f"reel.opening edit {index} names {edit_id!r}, "
                    f"not a structural opening edit")
        spec = ALGEBRA.get(edit_id)
        if spec is None or spec.verify is None:
            return (f"reel.opening edit {index} ({edit_id}) has no "
                    f"read-back verifier")
        try:
            failure = spec.verify(dict(edit), after)
        except Exception as exc:  # noqa: BLE001 - an unreadable edit is a failed read-back, never a thrown judgement
            return (f"reel.opening edit {index} ({edit_id}) does not "
                    f"read back ({type(exc).__name__}: {exc})")
        if failure is not None:
            return f"reel.opening edit {index} ({edit_id}): {failure}"
def _verify_broll_insert(op, after):
    """The cover item reads back on its cover track at the exact spans.

    broll.insert places a NEW item, so the read-back addresses it by the
    op's `unique_id` exactly once: zero matches is a missing placement,
    more than one is an ambiguous identity. The cover track, the record
    span, the marked source range and (when named) the B-roll clip name
    must all read back. Resolve echoes fresh placements with the
    exclusive source endpoint while existing clips report the inclusive
    one, so both endpoint forms read as the requested range (measured,
    `_inclusive_source_end` on the AXI trim path).
    """
    found = []
    for track in after.get("tracks", []):
        if (track.get("type") != op.get("track_type")
                or int(track.get("index", -1)) != int(op["track_index"])):
            continue
        for clip in track.get("clips") or []:
            if clip.get("unique_id") == op["unique_id"]:
                found.append(clip)
    if not found:
        return (f"no broll cover item {op['unique_id']!r} reads back on "
                f"{op.get('track_type')}{op.get('track_index')}: the "
                f"placement did not land")
    if len(found) > 1:
        return (f"broll cover item {op['unique_id']!r} reads back "
                f"{len(found)} times on "
                f"{op.get('track_type')}{op.get('track_index')}: the "
                f"placed identity is ambiguous")
    clip = found[0]
    if (int(clip["record_in"]) != int(op["record_in"])
            or int(clip["record_out"]) != int(op["record_out"])):
        return (f"broll cover item {op['unique_id']!r} reads "
                f"{clip['record_in']}..{clip['record_out']}, not "
                f"{op['record_in']}..{op['record_out']}")
    duration = int(op["record_out"]) - int(op["record_in"])
    source_in = int(clip["source_in_frame"])
    source_out = int(clip["source_out_frame"])
    if source_in != int(op["source_in"]) or (
            duration != source_out - source_in + 1
            and duration != source_out - source_in):
        return (f"broll cover item {op['unique_id']!r} reads source "
                f"{source_in}..{source_out}, not "
                f"{op['source_in']}..{op['source_out']}")
    if op.get("clip") and clip.get("name") != op["clip"]:
        return (f"broll cover item {op['unique_id']!r} reads "
                f"{clip.get('name')!r}, not {op['clip']!r}")
    return None


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
    "clip.remove": AlgebraOp(
        id="clip.remove", domain="timeline_structure",
        required=("unique_id",),
        span=lambda op, base: _ripple_span(op, base),
        item=True,
        # Ripple: the gap closes by moving every later cut, so the span
        # covers the removed clip through the timeline end - the range
        # a patch validation must hold. The write is the measured
        # ripple delete with the local delete's one retry.
        apply=_remove_clip_with_measured_retry,
        verify=lambda op, after: _verify_remove(op, after),
        # The whole clip: it meets every write to it, in any domain.
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        temporal="ripple",
        inverse=_pair("clip.insert",
                       "re-inserts the removed source range"),
        note="A clip deleted with its gap closed: later clips move "
             "earlier by the removed duration. Unlike `clip.delete`, no "
             "gap is left for a separate `clip.ripple`."),
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
    "broll.insert": AlgebraOp(
        id="broll.insert", domain="timeline_structure",
        required=("unique_id", "track_type", "track_index",
                  "record_in", "record_out", "source_in", "source_out",
                  "clip"),
        span=lambda op, base: (int(op["record_in"]), int(op["record_out"])),
        verify=_verify_broll_insert,
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_pair("clip.delete",
                      "deletes the placed B-roll cover; the A-roll and "
                      "its audio are untouched by the removal"),
        note="Covers a marked phrase with the supplied matching B-roll: a "
             "pool clip placed onto a COVER video track at the exact "
             "marked source range and record span, leaving the A-roll "
             "and its audio untouched. `clip` is the pool clip's exact "
             "name (`bin` scopes the search); the inverse deletes the "
             "placed cover item."),
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
        note="Slip the source under a fixed timeline window. The staged "
             "touch-up path reads the source bounds and exact placement "
             "back."),
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
        apply=_apply_replace,
        verify=lambda op, after: _verify_replace(op, after),
        key=lambda op: ("clip", op["unique_id"]), merge="exclusive",
        inverse=_self("the inverse carries the previous source"),
        note="A clip's source for another pool clip at the same span, "
             "carrying the remapped Fusion keys (`fusion_keys`) and the "
             "grade assertion (`expected_grade`) with the binding so a "
             "commit can never land new media under stale keys "
             "(the Unit 5 swap-back)."),
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
        note="Per-clip gain in dB through an isolated OTIO candidate; exact "
             "source and record start, full timeline preservation and OTIO "
             "level read-back are required."),
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
        note="Fade in/out in seconds; the staged native adapter converts "
             "to frame counts and reads all item fades back."),
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
        apply=_apply_grade_node_remove,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["node_index"])),
        merge="exclusive",
        inverse=_pair("grade.node_add"),
        note="GUI-only write, API-read judgement: the Color-page menu "
             "recipe steps to the target node and presses Delete Current "
             "Node; `GetNumNodes` must shrink by exactly one AND the "
             "removed index's cache-mode label must be gone (labels "
             "restored after). The Resolve 21.1 Graph API has no "
             "RemoveNode - SetNodeEnabled(False) bypasses a node but "
             "does not remove it from the graph."),
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
    "multicam.select_angle": AlgebraOp(
        id="multicam.select_angle", domain="picture_transform",
        required=("unique_id", "speaker_id", "assignment_basis",
                  "source_file", "source_in_frame", "source_out_frame",
                  "sync_evidence_id"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_multicam_select_angle(op, after),
        key=lambda op: ("clip", op["unique_id"], "angle"),
        merge="replace",
        inverse=_self("the inverse carries the previous angle"),
        note="Show the camera assigned to the identified speaker for "
             "their speaking turn, replacing its source with the "
             "explicitly synced source range while preserving record "
             "edit points."),

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
        apply=_apply_track_add,
        verify=lambda op, after: _verify_track_named(op["name"])(op, after),
        key=lambda op: ("track", op["track_type"], -1), merge="exclusive",
        inverse=_pair("track.delete"),
        note="A new named row; the touch-up's `add_row` views here."),
    "track.delete": AlgebraOp(
        id="track.delete", domain="timeline_structure",
        required=("track_type", "track_index"),
        span=_track_span,
        apply=_apply_track_delete,
        verify=_verify_track_present(False),
        key=lambda op: ("track", op["track_type"],
                        int(op["track_index"])), merge="exclusive",
        inverse=_pair("track.add", "re-adds the row with its name"),
        note="Remove an EMPTY row. Resolve's DeleteTrack destroys every "
             "clip on the row with no undo of its own, so a row that still "
             "carries clips is REFUSED: a row removal that took editorial "
             "work with it would destroy work the request never named."),
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
    "overlay.replace": AlgebraOp(
        id="overlay.replace", domain="fusion",
        required=("unique_id", "file"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("overlay", op["unique_id"]), merge="replace",
        inverse=_self("the inverse carries the previous file"),
        note="Replace the selected overlay with the supplied approved "
             "file, preserving its position and timing; staged through "
             "the swap_pixels physical path with identity and "
             "preservation read-back."),
    "overlay.resize": AlgebraOp(
        id="overlay.resize", domain="fusion",
        required=("unique_id", "width", "height"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("overlay", op["unique_id"]), merge="replace",
        inverse=_self("the inverse carries the previous size"),
        note="Width and height are output-geometry values. The staged "
             "candidate adapter links the geometry model to rendered-pixel "
             "verification; live qualification remains deferred."),
    "overlay.position": AlgebraOp(
        id="overlay.position", domain="fusion",
        required=("unique_id", "dx", "dy"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("overlay", op["unique_id"]), merge="replace",
        inverse=_self("the inverse carries the previous position"),
        note="dx/dy are signed output-pixel deltas (dx positive right, "
             "dy positive down, so up by 40 output pixels is dy=-40). "
             "Size, timing and every other item are preserved. The staged "
             "candidate adapter lowers the translated box through the "
             "geometry model to rendered-pixel verification; live Resolve "
             "qualification remains deferred."),
    "overlay.entry_motion": AlgebraOp(
        id="overlay.entry_motion", domain="fusion",
        required=("unique_id", "frames"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("overlay", op["unique_id"]), merge="replace",
        inverse=_self("the inverse clears the animation")),
    "hold.repair_window": AlgebraOp(
        id="hold.repair_window", domain="fusion",
        required=("unique_id",),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("hold", op["unique_id"]), merge="replace",
        inverse=_self("the inverse carries the previous window"),
        note="A freeze-hold tail's comp media windows rebound in place; "
        "spans, pixels, transforms and grades unchanged."),

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
        verify=_verify_caption_fix,
        key=lambda op: ("clip", op["unique_id"], "caption"),
        merge="replace",
        inverse=_self("the inverse carries the previous text"),
        note="Rewrite the text of one rendered caption card - the "
             "medium every Ren reel uses - and swap it in place. The "
             "card's own measured word windows are re-fitted to the "
             "new words, the card is drawn again through the caption "
             "render path, and the new file is swapped onto the "
             "placement. The timing never moves."),
    "caption.style": AlgebraOp(
        id="caption.style", domain="captions",
        required=("unique_id", "style"),
        span=_item_span, item=True,
        verify=_verify_caption_style,
        key=lambda op: ("clip", op["unique_id"], "caption", "style"),
        merge="replace",
        inverse=_self("the inverse carries the previous style"),
        note="Re-render one caption card in the named style - same text, "
             "same timing, new pixels - and swap it in place. The caption "
             "render path (`subtitle_style`, the step 4.05 assets) does "
             "the physical work and the swap read-back judges every card. "
             "`style` names `SUBTITLE_STYLES`; anything else is refused, "
             "never guessed."),
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
        inverse=_never("transition deletion has no qualified compensation"),
        note="A semantic transition names outgoing and incoming item ids, "
             "their exact cut frame, and a granted native type. The staged "
             "adapter verifies the returned transition span before promotion."),
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

    # ── reel opening (domain: timeline_structure) ────────────────────
    #
    # A planning-level operation that compiles to existing structural
    # edits (trim and ripple remove) through EditRequest. `unique_id`
    # anchors the opening clip being tightened, `brief` is the supplied
    # brief the opening is tightened to, and `edits` are the model's
    # decided structural edits in execution order. The adapter runs
    # those through their own executors with a fresh live read-back
    # after each write - never a full rebuild. Unqualified until the
    # live-requests lane records qualifying evidence, so every backend
    # fails it closed until then.
    "reel.opening": AlgebraOp(
        id="reel.opening", domain="timeline_structure",
        required=("unique_id", "brief", "edits"),
        span=_reel_opening_span, item=True,
        verify=_verify_reel_opening,
        key=lambda op: ("reel", "opening", op["unique_id"]),
        merge="exclusive",
        temporal="ripple",
        inverse=_self("the inverse carries the pre-tighten opening spans"),
        note="Tighten the opening to the supplied brief while preserving "
             "the speaker's meaning and approved edits; executes as its "
             "carried trim/remove edits, each with its own undo journal "
             "entry and read-back."),
}


def temporal_effect(operation_id: str) -> str:
    """Return the declared timeline effect for an algebra operation.

    Consumers that plan merges must use this declaration rather than
    guessing from an operation name or the size of its frame span.
    Unknown operations are deliberately not treated as local.
    """
    operation = ALGEBRA.get(str(operation_id))
    if operation is None:
        raise KeyError(f"unknown edit algebra operation {operation_id!r}")
    return operation.temporal


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


def _verify_remove(op, after):
    """The removed clip reads back nowhere; the gap close is judged by
    the acceptance contract, not here.

    The algebra verifier sees only the after-snapshot, so absence is
    what it can judge. Whether the gap closed, the downstream
    continuity held and the linked audio went with the picture is
    measured from the before/after pair by `operation_acceptance`
    (the `clip.remove` contract), supplied with evidence by the
    executing adapter.
    """
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


def _verify_multicam_select_angle(op, after):
    """The assigned camera and synchronized range replace the turn."""
    if _clip(after, op["unique_id"]) is not None:
        return (f"camera switch left target {op['unique_id']!r} in place; "
                "the replacement identity did not read back")
    matches = [clip for clip in shadow.clips(after)
               if clip.get("source_file") == op["source_file"]
               and _same_value(clip.get("source_in_frame"),
                               op["source_in_frame"])
               and _same_value(clip.get("source_out_frame"),
                               op["source_out_frame"])]
    if not matches:
        return (f"no replacement reads assigned camera "
                f"{op['source_file']!r} at the synchronized source range")
    if len(matches) != 1:
        return (f"the assigned camera {op['source_file']!r} reads back "
                f"{len(matches)} times at the synchronized source range")
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
_PLANNING_OPERATION_IDS = frozenset((*PLAN_OPS, "angle_plan", "plan_change"))

#: The touch-up's eleven ops, each resolving to an algebra id. `retime`,
#: `set_properties`, `set_enabled` and `add_row` alias ops the other
#: vocabularies already name; the rest are the touch-up's own.
TOUCHUP_VIEW = {
    "move": "clip.move",
    "swap_pixels": "overlay.swap_pixels",
    "add_overlay": "overlay.add",
    "remove_overlay": "overlay.remove",
    "native_transition": "transition",
    "retime": "retime",
    "resize_still": "overlay.resize",
    "resize_overlay": "overlay.resize",
    "position_overlay": "overlay.position",
    "audio_fade": "audio.fade",
    "split": "clip.split",
    "slide": "clip.slide",
    "roll": "clip.roll",
    "slip": "clip.slip",
    "set_properties": "clip.set_property",
    "add_row": "track.add",
    "set_enabled": "clip.set_enabled",
    "entry_motion": "overlay.entry_motion",
    "repair_hold_window": "hold.repair_window",
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


@dataclass(frozen=True)
class BackendStatus:
    """Evidence-backed availability of one algebra op on one executor."""

    backend: str
    compilable: bool
    qualification_status: str
    limitations: tuple[str, ...] = ()
    qualification_evidence: tuple[str, ...] = ()
    resolve_version: str | None = None
    qualification_media_types: tuple[str, ...] = ()
    qualification_timeline_conditions: tuple[str, ...] = ()
    qualification_predicates: tuple[Mapping, ...] = ()
    preservation_limits: tuple[str, ...] = ()
    visual_verification: tuple[str, ...] = ()
    audio_verification: tuple[str, ...] = ()
    recovery_qualification: str = "not live-qualified"
    implementation_status: str = "implemented_unqualified"
    implementation_evidence: tuple[str, ...] = ()

    @property
    def qualified(self) -> bool:
        return self.qualification_status in {"qualified", "production_enabled"}

    @property
    def production_enabled(self) -> bool:
        return self.qualification_status == "production_enabled"


@dataclass(frozen=True)
class OpStatus:
    """Inspectable semantic and execution contract for an algebra op.

    `defined` means the semantic id is in this vocabulary. `compilable`
    means at least one listed executor can produce a write. Qualification
    and production enablement are deliberately separate: a write may be
    implemented without live Resolve evidence.
    """

    id: str
    param_schema: dict
    state_domains: tuple[str, ...]
    required_resolve_capabilities: tuple[str, ...]
    media_types: tuple[str, ...]
    timeline_conditions: tuple[str, ...]
    precondition_validator: str
    backends: tuple[BackendStatus, ...]
    verification_contract: str
    inverse_recovery: dict
    defined: bool
    compilable: bool
    qualification_status: str
    safety_class: str = ""
    minimal_safe_mechanism: str = ""
    unsupported_native_state: tuple[str, ...] = ()
    measured_cost: dict | None = None
    target_strategy: str = "not specified in VC-20"
    implementation_status: str = "no_executor"
    workflow_evidence: tuple[str, ...] = ()

    @property
    def qualified(self) -> bool:
        return self.qualification_status in {"qualified", "production_enabled"}

    @property
    def production_enabled(self) -> bool:
        return self.qualification_status == "production_enabled"

    def for_backend(self, backend: str) -> BackendStatus | None:
        return next((entry for entry in self.backends
                     if entry.backend == backend), None)


_BACKEND_LIMITATIONS = {
    "edit_patch": (
        "Only algebra operations with a native apply are schedulable.",),
    "feedback_patch_apply": (
        "Local restorable feedback operations only; no pool fetch or ripple.",),
    "reel_touchup": (
        "Only the named touch-up vocabulary and its declared constraints.",),
    "composed_edit": (
        "Composition-level staging and validation rules apply.",),
    "resolve_axi": (
        "Only operations reached by the Resolve AXI command dispatch.",),
    "reel_append_placement": (
        "Recorded full-build placement; not an existing-timeline patch.",),
    "reel_otio_placement": (
        "Recorded full-build placement; OTIO representability refusals apply.",),
    "resolve_native_invocation": (
        "Only explicitly bound documented methods; AXI authorization and operation-specific read-back are required.",),
    "resolve.ui": (
        "UI recipes are available only for declared controls; exact UI state and independent verification are required.",),
}

# Qualification rows are deliberately not synthesized from old live test
# paths. Those tests do not record all of Resolve version, media, timeline,
# preservation, visual/audio verification and recovery evidence required by
# the current production contract.
_BACKEND_QUALIFICATIONS = {}

# The importer writes only observed live-run records. Qualification status is
# derived below from the record's completed evidence fields; this file never
# stores an operator-selected ``qualified`` or ``production_enabled`` flag.
_BACKEND_QUALIFICATIONS.update(load_qualification_records())

# Route research is reported to discovery so a missing adapter does not
# silently imply that UI control is the next attempt. Inspection is not
# qualification; each gap remains fail-closed.
_ROUTE_EVALUATION = {
    "overlay.resize": {
        "inspected": ["Resolve 21.1 TimelineItem.SetProperties/GetProperties and documented Pan/Tilt/ZoomX/ZoomY", "Fusion scripting", "layout_model", "overlay_verify", "OTIO", "Blackmagic MCP tools/list", "Fairlight UI/API coverage", "Resolve Workflow Integration"],
        "available": ["protected reel_touchup lowers output-pixel dimensions through layout_model, writes Pan/Tilt/ZoomX/ZoomY on staging, and verifies an enabled/disabled rendered pixel diff with overlay_verify before promotion", "Blackmagic MCP run_script is arbitrary Python, not a semantic operation backend"],
        "gaps": ["limited to a native PNG still or a rendered movie overlay on V3+ without drawing Fusion comps, rotation, crop, or non-native Scaling; a rendered movie overlay must play at normal speed, so its frame that draws can be located; live Resolve qualification remains deferred", "MCP script tools bypass operation-specific authorization, verification and recovery"],
        "next": "Run the deferred geometry qualification on disposable Resolve timelines, including preservation, recovery, clipping and rendered dimensions, before enabling normal routing.",
        "not_yet_evaluated": [],
    },
    "overlay.position": {
        "inspected": ["Resolve 21.1 TimelineItem.SetProperties/GetProperties and documented Pan/Tilt/ZoomX/ZoomY", "Fusion scripting", "layout_model", "overlay_verify", "OTIO", "Blackmagic MCP tools/list", "Fairlight UI/API coverage", "Resolve Workflow Integration"],
        "available": ["protected reel_touchup translates the output-pixel box through layout_model, writes Pan/Tilt/ZoomX/ZoomY on staging, and verifies an enabled/disabled rendered pixel diff with overlay_verify before promotion", "Blackmagic MCP run_script is arbitrary Python, not a semantic operation backend"],
        "gaps": ["limited to a native PNG still or a rendered movie overlay on V3+ without drawing Fusion comps, rotation, crop, or non-native Scaling; a rendered movie overlay must play at normal speed, so its frame that draws can be located; size and timing preserved, translated box must stay inside the delivery frame and safe area; live Resolve qualification remains deferred", "MCP script tools bypass operation-specific authorization, verification and recovery"],
        "next": "Run the deferred geometry qualification on disposable Resolve timelines, including preservation, recovery, safe-area placement and rendered position, before enabling normal routing.",
        "not_yet_evaluated": [],
    },
    "audio.gain": {
        "inspected": ["native scripting", "OTIO mix", "Blackmagic MCP delegated capabilities", "Resolve Workflow Integrations developer package and installed VEP plugin"],
        "available": ["reel_touchup maps the exact audio item to a one-target OTIO round-trip; candidate promotion requires full timeline preservation and fresh OTIO level read-back", "Workflow Integration is a host over the same scripting API; the installed VEP plugin exposes read-only Resolve calls, not a new edit backend"],
        "gaps": ["SetProperty(Volume) is not a working measured writer", "a target must match one audio item by exact source path and record start", "full timeline snapshot must be unchanged, so candidates that lose Fusion/CDL/editor state are discarded", "no rendered audio acceptance evidence for this request path", "plugin-owned writes have no AXI request/receipt path and would bypass the shared broker, lease, qualification, and recovery contract"],
        "not_yet_evaluated": ["Resolve Fairlight UI-independent controls"],
        "next": "Run the deferred live qualification with rendered audio evidence, recovery and preservation checks before enabling normal routing.",
    },
    "audio.fade": {
        "inspected": ["native scripting", "OTIO mix", "Blackmagic MCP delegated capabilities", "Resolve Workflow Integrations developer package and installed VEP plugin"],
        "available": ["staged TimelineItem.SetFades/GetFades adapter uses frame-count durations and checks every audio item fade before and after the write", "Workflow Integration is a host over the same scripting API; the installed VEP plugin exposes read-only Resolve calls, not a new edit backend"],
        "gaps": ["the touch-up preservation snapshot does not include audio fades, so this adapter independently compares all native fade states on source and staged timelines", "audio-only request is required; mixed structural operations refuse", "independent rendered-audio acceptance evidence is not yet supplied, so normal production access remains disabled", "plugin-owned writes have no AXI request/receipt path and would bypass the shared broker, lease, qualification, and recovery contract"],
        "next": "Run the deferred live qualification with rendered audio evidence, recovery and preservation checks before enabling normal routing.",
    },
    "clip.split": {
        "inspected": ["Resolve 21.1 TimelineItem and Timeline method inventory", "composed_edit", "OTIO", "Blackmagic MCP tools/list", "Fairlight UI/API coverage", "Resolve Workflow Integration"],
        "available": ["reel_touchup stages exact two-span reconstruction, carries native transform properties to the new half, and verifies item spans plus collateral timeline before promotion", "TimelineItem has no documented SplitAtFrame method", "MCP run_script only exposes arbitrary code and no AXI transaction contract"],
        "gaps": ["limited to one normal-speed clip with no links, Fusion comps, markers, flags, keyframes or color state; live Resolve qualification remains deferred", "MCP does not improve safety or verification"],
        "next": "Run deferred split qualification on a disposable timeline before enabling normal routing.",
        "not_yet_evaluated": [],
    },
    "clip.slide": {
        "inspected": ["Resolve 21.1 TimelineItem and Timeline method inventory", "composed_edit", "reel_touchup", "OTIO", "Blackmagic MCP tools/list", "Fairlight UI/API coverage", "Resolve Workflow Integration"],
        "available": ["reel_touchup composes the target and both neighbors on a staged candidate; placement read-back and full collateral comparison gate promotion", "no native Slide operation is documented", "MCP run_script supplies no candidate-local preservation or recovery guarantee"],
        "gaps": ["limited to three contiguous normal-speed clips with no links, Fusion comps, markers, flags, keyframes or color state; live qualification remains deferred"],
        "next": "Run deferred slide qualification on a disposable timeline before enabling normal routing.",
        "not_yet_evaluated": [],
    },
    "clip.roll": {
        "inspected": ["Resolve 21.1 TimelineItem and Timeline method inventory", "composed_edit", "reel_touchup", "OTIO", "Blackmagic MCP tools/list", "Fairlight UI/API coverage", "Resolve Workflow Integration"],
        "available": ["reel_touchup composes the two source windows at their shared cut on a staged candidate; placement read-back and full collateral comparison gate promotion", "no native Roll operation is documented", "MCP run_script supplies no candidate-local preservation or recovery guarantee"],
        "gaps": ["limited to two contiguous normal-speed clips with no links, Fusion comps, markers, flags, keyframes or color state; live qualification remains deferred"],
        "next": "Run deferred roll qualification on a disposable timeline before enabling normal routing.",
        "not_yet_evaluated": [],
    },
    "transition": {
        "inspected": ["native scripting", "Fusion build path", "OTIO", "Resolve Workflow Integrations developer package and installed VEP plugin"],
        "available": ["reel_touchup applies a granted AddTransition type to the exact incoming item on a staged timeline and verifies the returned span against the named cut and duration", "Workflow Integration is a host over the same scripting API; the installed VEP plugin exposes read-only Resolve calls, not a new edit backend"],
        "gaps": ["only centered transitions with an explicit frame duration are supported", "the adapter uses returned native transition span evidence; normal routing remains disabled until live qualification verifies audio/video output and recovery", "plugin-owned writes have no AXI request/receipt path and would bypass the shared broker, lease, qualification, and recovery contract"],
        "next": "Run the deferred live qualification with visual/audio evidence and recovery checks before enabling normal routing.",
        "not_yet_evaluated": [],
    },
    "caption_fix": {
        "inspected": ["step 4.05 caption render path and the render props beside a placed card", "caption_swap pool-item replacement", "caption_rewrite word-window fit", "OTIO", "Blackmagic MCP tools/list", "Resolve Workflow Integrations developer package and installed VEP plugin"],
        "available": ["the adapter re-fits the card's own measured word windows to the new words, re-renders the card through rerender_and_swap and swaps the pool item with per-placement read-back and a post-check that no old file remains placed", "the rendered card's own props are read back off the disk after the render, so the pixels are proved to carry the new text over the old timing"],
        "gaps": ["one caption card per operation, on the Subtitles row; a title item is not a caption card", "the new text must fit the card's measured span - a text that cannot be timed inside the card refuses rather than being drawn unswept", "the swap replaces the card's media pool item, which is project-wide: every placement of that card reads the new text", "live Resolve qualification remains deferred, so normal routing stays fail-closed", "plugin-owned writes have no AXI request/receipt path and would bypass the shared broker, lease, qualification, and recovery contract"],
        "next": "Run the deferred live qualification with per-card rendered evidence, text/timing preservation and swap-back recovery before enabling normal routing.",
        "not_yet_evaluated": [],
    },
    "caption.style": {
        "inspected": ["native scripting title/text properties", "step 4.05 caption render path and subtitle_style", "caption_swap pool-item replacement", "OTIO", "Blackmagic MCP tools/list", "Resolve Workflow Integrations developer package and installed VEP plugin"],
        "available": ["the adapter re-renders one caption card in a named subtitle style through rerender_and_swap with text and timing preserved, then ReplaceClips the pool item with per-placement read-back and a post-check that no old file remains placed", "Workflow Integration is a host over the same scripting API; the installed VEP plugin exposes read-only Resolve calls, not a new edit backend"],
        "gaps": ["one caption card per operation; the style must name SUBTITLE_STYLES and is never guessed", "live Resolve qualification remains deferred, so normal routing stays fail-closed", "plugin-owned writes have no AXI request/receipt path and would bypass the shared broker, lease, qualification, and recovery contract"],
        "next": "Run the deferred live qualification with per-card rendered evidence, text/timing preservation and swap-back recovery before enabling normal routing.",
        "not_yet_evaluated": [],
    },
    "broll.insert": {
        "inspected": ["Resolve 21.1 AppendToTimeline with mediaPoolItem/startFrame/endFrame/mediaType/trackIndex/recordFrame", "AXI edit place read-back (item count delta, placed span inventory)", "timeline_shadow live snapshot inventory"],
        "available": ["the registered resolve_axi adapter revalidates the cover track and the underlying speech rows against the prepared snapshot, places through the journaled edit place verb, and verifies the placed cover item plus unchanged A-roll/audio from a fresh live read-back"],
        "gaps": ["live Resolve qualification remains deferred; normal production routing refuses until operation-specific qualification records land", "MCP run_script supplies no AXI transaction contract and is not a backend"],
        "next": "Run the deferred live qualification on a disposable Resolve timeline (exact cover placement, A-roll/audio preservation, journal recovery) before enabling normal routing.",
        "not_yet_evaluated": [],
    },
}
_WORKFLOW_EVIDENCE = {
    "clip.ripple": (
        "library/tools/composed_edit.py:plan_ripple (staged timeline-wide timing composition)",),
}

_IMPLEMENTATION_EVIDENCE = {
    ("audio.gain", "reel_touchup"): (
        "library/tools/edit_execution_adapters.py:_audio_gain",
        "library/tools/execution/deliver_audio_mix.py:deliver_semantic_gain",
        "library/tools/execution/deliver_audio_mix.py:deliver_mix",
        "library/tools/otio_mix.py:audio_op_target",
    ),
    ("transition", "reel_touchup"): (
        "library/tools/edit_execution_adapters.py:_touchup",
        "library/tools/reel_touchup.py:_op_native_transition",
        "library/tools/reel_touchup.py:_apply_in_place",
    ),
    ("audio.fade", "reel_touchup"): (
        "library/tools/edit_execution_adapters.py:_touchup",
        "library/tools/reel_touchup.py:_op_audio_fade",
        "library/tools/reel_touchup.py:_apply_in_place",
        "library/tools/reel_touchup.py:_audio_fades",
    ),
    ("clip.delete", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_delete",
        "library/tools/resolve_axi_core_commands.py:cmd_edit_delete",
    ),
    ("clip.remove", "edit_patch"): (
        "library/tools/edit_algebra.py:_remove_clip_with_measured_retry",
    ),
    ("clip.remove", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_remove",
        "library/tools/resolve_axi_core_commands.py:cmd_edit_track_ripple_remove",
    ),
    ("clip.insert", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_place",
        "library/tools/resolve_axi_core_commands.py:cmd_edit_place",
    ),
    ("clip.trim", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_trim",
        "library/tools/resolve_axi_core_commands.py:cmd_edit_trim",
    ),
    ("clip.replace", "edit_patch"): (
        "library/tools/edit_algebra.py:_apply_replace",
        "library/tools/edit_patch.py:execute_physical_patch",
    ),
    ("broll.insert", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_broll_insert",
        "library/tools/resolve_axi_core_commands.py:cmd_edit_place",
    ),
    ("clip.ripple", "reel_touchup"): (
        "library/tools/edit_execution_adapters.py:_touchup",
        "library/tools/reel_touchup.py:_op_retime",
        "library/tools/composed_edit.py:plan_ripple",
    ),
    ("clip.slip", "reel_touchup"): (
        "library/tools/edit_execution_adapters.py:_touchup",
        "library/tools/reel_touchup.py:_op_slip",
        "library/tools/composed_edit.py:apply_composed_edit",
    ),
    ("caption_fix", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_caption_fix",
        "library/tools/caption_rewrite.py:rewrite_props",
        "library/steps/step_4_05_render_subtitles/step.py:rerender_and_swap",
        "library/tools/caption_swap.py:swap_files",
    ),
    ("multicam.select_angle", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_multicam_select_angle",
        "library/tools/resolve_axi_core_commands.py:cmd_edit_switch_camera",
        "library/tools/resolve_axi_core_commands.py:_replace_item",
    ),
    ("caption.style", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_caption_style",
        "library/steps/step_4_05_render_subtitles/step.py:rerender_and_swap",
        "library/tools/caption_swap.py:swap_files",
        "library/tools/subtitle_style.py:resolve_subtitle_style",
    ),
    ("reel.opening", "resolve_axi"): (
        "library/tools/edit_execution_adapters.py:_reel_opening",
        "library/tools/resolve_axi_core_commands.py:cmd_edit_trim",
        "library/tools/resolve_axi_core_commands.py:cmd_edit_delete",
    ),
    ("overlay.add", "reel_touchup"): (
        "library/tools/reel_touchup.py:_is_native_still",
        "library/tools/reel_touchup.py:_place_still_insertions",
        "library/tools/reel_touchup.py:_assert_still_insertions",
        "PR #2718: native PNG stills use exact-duration placement",
    ),
    ("overlay.resize", "reel_touchup"):
        ("library/tools/reel_touchup.py:_op_resize_overlay",
         "library/tools/reel_touchup.py:_overlay_resize_projection",
         "library/tools/overlay_verify.py:verify_rendered_boxes"),
    ("overlay.position", "reel_touchup"):
        ("library/tools/reel_touchup.py:_op_position_overlay",
         "library/tools/reel_touchup.py:_overlay_position_projection",
         "library/tools/overlay_verify.py:verify_rendered_boxes"),
    ("clip.split", "reel_touchup"): (
        "library/tools/edit_execution_adapters.py:_touchup",
        "library/tools/reel_touchup.py:_op_split",
        "library/tools/composed_edit.py:apply_composed_edit",
        "library/tools/composed_edit.py:verify_placement"),
    ("clip.slide", "reel_touchup"): (
        "library/tools/edit_execution_adapters.py:_touchup",
        "library/tools/reel_touchup.py:_op_slide",
        "library/tools/composed_edit.py:apply_composed_edit",
        "library/tools/composed_edit.py:verify_placement"),
    ("clip.roll", "reel_touchup"): (
        "library/tools/edit_execution_adapters.py:_touchup",
        "library/tools/reel_touchup.py:_op_roll",
        "library/tools/composed_edit.py:apply_composed_edit",
        "library/tools/composed_edit.py:verify_placement"),
}

_BACKEND_OPERATION_LIMITATIONS = {
    ("overlay.resize", "reel_touchup"):
        ("one transparent PNG still or one rendered movie overlay on V3+; a rendered movie overlay carries an alpha plane and plays at normal speed; no drawing Fusion comp, rotation, crop or non-native Scaling; dimensions are delivery pixels; preserve center; an enabled/disabled rendered pixel diff verifies the requested box within 2px; approved timeline is retained until promotion",),
    ("overlay.position", "reel_touchup"):
        ("one transparent PNG still or one rendered movie overlay on V3+; a rendered movie overlay carries an alpha plane and plays at normal speed; no drawing Fusion comp, rotation, crop or non-native Scaling; dx/dy are signed output pixels (dx positive right, dy positive down); size and timing preserved; translated box must stay inside the delivery frame; an enabled/disabled rendered pixel diff verifies the requested box within 2px; approved timeline is retained until promotion",),
    ("clip.split", "reel_touchup"):
        ("one normal-speed video item without links, Fusion comps, markers, flags, keyframes or color state; exact interior record-frame boundary and readable source bounds; carries native transform properties to the second half; candidate verifies both source/record spans and collateral timeline",),
    ("clip.slide", "reel_touchup"):
        ("three contiguous normal-speed video items without links, Fusion comps, markers, flags, keyframes or color state; preserves total record duration and verifies all three source/record spans and collateral timeline on staging",),
    ("clip.roll", "reel_touchup"):
        ("two contiguous normal-speed video items without links, Fusion comps, markers, flags, keyframes or color state; preserves shared record duration and verifies both source/record spans and collateral timeline on staging",),
    ("audio.gain", "reel_touchup"):
        ("one audio-only request; requested dB must be in the OTIO range; exact source path and record start must identify one item; full candidate snapshot must match before and after; no independent rendered-audio evidence is recorded yet",),
    ("transition", "reel_touchup"):
        ("exact outgoing/incoming unique ids and cut frame are required; only granted native transition types, centered alignment and explicit frame durations are accepted; AddTransition must return a span covering the exact named cut; no independent rendered audio/video evidence is recorded yet",),
    ("audio.fade", "reel_touchup"):
        ("one audio-only request; fade durations are frame-rounded at the timeline rate; every audio item's native fades must match on source/staging before write and all unrelated fade values must read unchanged afterward; no independent rendered-audio evidence is recorded yet",),
    ("clip.trim", "resolve_axi"):
        ("source-window narrowing only; requires readable source and record spans, and AXI re-place/delete read-back",),
    ("broll.insert", "resolve_axi"):
        ("topmost video track over continuous lower-layer picture; exact marked source range and record span with matching record duration; every non-cover video row and audio row must read back unchanged; a verified undo journal entry is required before the placement is claimed",),
    ("clip.delete", "resolve_axi"):
        ("local delete leaves a gap; ripple must be a separate explicitly requested staged operation",),
    ("clip.remove", "edit_patch"):
        (("track-scoped ripple is unavailable; the EditPatch executor refuses "
          "before writing because Resolve's native ripple affects every track"),),
    ("clip.remove", "resolve_axi"):
        (("track-scoped executor re-places only the target track's later items "
          "and explicit link groups; production remains disabled until live "
          "qualification proves continuity, unrelated-track preservation, and "
          "journal recovery"),),
    ("clip.insert", "resolve_axi"):
        (("one pool item placed at one record span on one named video row "
          "through the journaled AXI place verb; the placement is read back "
          "from a fresh live snapshot and every unrelated item must read "
          "unchanged; production remains disabled until live qualification "
          "lands"),),
    ("clip.ripple", "reel_touchup"):
        ("one target clip changes played length; every affected later item must be readable and recoverable on the staged copy",),
    ("clip.slip", "reel_touchup"):
        ("one target source window shifts within measured source bounds; its record span stays fixed and the staged replacement must read back exactly",),
    ("caption_fix", "resolve_axi"):
        ("one rendered caption card per operation, re-rendered with the new text over the card's own measured word windows and swapped in place with per-placement read-back; the card's timing never moves and the new text must fit the card's measured span; a title item is not a caption card and is not addressed here; live Resolve qualification remains deferred",),
    ("multicam.select_angle", "resolve_axi"):
        (("requires a resolved speaker id, assignment basis, unique camera media-pool source, "
          "explicit synced source frames, and a source range matching the existing record duration; "
          "the switch is a journaled place-then-delete replacement, read back against the replaced "
          "source and record span; live Resolve qualification remains deferred"),),
    ("caption.style", "resolve_axi"):
        ("one caption card per operation, re-rendered in a named subtitle style with its text and timing preserved; the style must name library/tools/subtitle_style.py SUBTITLE_STYLES and is never guessed; rendered caption images require regeneration with per-card swap read-back; live Resolve qualification remains deferred",),
    ("reel.opening", "resolve_axi"):
        ("supplied brief plus decided source trims and gap-closing clip.remove edits only; every executed edit carries its own undo journal entry and independent read-back; live Resolve qualification remains deferred",),
    ("clip.move", "reel_touchup"):
        ("same-row overlay items only; cross-row moves require remove plus add",),
    ("overlay.add", "reel_touchup"):
        ("native PNG stills use still_placement exact-duration routing; staging must re-read the requested span",),
    ("clip.insert", "edit_patch"):
        ("one pool item placed at one stated record span on one named row; the source range is the pool item's own; the placed item reads back under the Resolve-assigned unique id, which is NOT the id the request named",),
    ("clip.replace", "edit_patch"):
        ("re-points the MEDIA POOL ITEM the clip draws from, so every clip on every timeline that draws from that item changes with it; the clip's own record span, transform and Fusion keys are preserved and read back; a replacement onto a shared pool item is the caller's decision, not the engine's",),
    ("track.delete", "edit_patch"):
        ("an EMPTY row only: Resolve's DeleteTrack destroys every clip on the row with no undo of its own, so a row that still carries clips is refused before the write",),
    ("track.add", "edit_patch"):
        ("appends one row and names it with Resolve's measured API; the existing rows keep their names, order and contents",),
    ("clip.set_property", "edit_patch"):
        ("the picture transform and its modes only: Pan, Tilt, Scaling, ZoomX, ZoomY, ZoomGang, RotationAngle, AnchorPointX, AnchorPointY, Pitch, Yaw, FlipX, FlipY, CropLeft, CropRight, CropTop, CropBottom, CropSoftness, CropRetain, Opacity; a key outside that set is refused, never rounded; the write is judged by GetProperty() read back on the same item",),
    ("clip.set_enabled", "edit_patch"):
        ("one item's enabled state: the clip's contribution is switched off, which is how Resolve mutes an audio item; the record span and the source range are untouched; the state reads back through GetClipEnabled()",),
    ("retime.segments", "edit_patch"):
        ("Resolve carries no speed-curve API, so a ramp reaches the timeline as stepped constant-speed segments: one split per boundary and one constant SetSpeed per piece; the clip's duration changes and every later clip on the row moves with it",),
    ("mask.create", "edit_patch"):
        ("the fusion_matte provider places a full-frame Fusion matte comp and claims it by read-back; the two Magic Mask providers are named so a request that claims one fails loudly instead of silently getting a Fusion matte",),
    ("grade.node_set", "edit_patch"):
        ("one named node of an EXISTING graph - the Graph API has no AddNode, so a node that does not exist is refused; the write is judged by GetCDL read back on the same node, because a SetCDL that answered True is not evidence the grade landed",),
}

# Live evidence is intentionally enumerated by exact op and executor. A
# test that exercises a neighboring operation or a Resolve double is not
# qualification evidence. Update this map only when a live qualification
# test proves the named operation through the named production path.
_LIVE_QUALIFICATION = {
    ("marker.add", "feedback_patch_apply"):
        ("tests/qualification/test_feedback_patch_apply_against_resolve.py",),
    ("marker.delete", "feedback_patch_apply"):
        ("tests/qualification/test_feedback_patch_apply_against_resolve.py",),
    ("marker.add", "edit_patch"):
        ("tests/qualification/test_feedback_patch_flow_against_resolve.py",),
    ("marker.delete", "edit_patch"):
        ("tests/qualification/test_feedback_patch_flow_against_resolve.py",),
    ("clip.set_property", "reel_touchup"):
        ("tests/qualification/test_golden_conversation_live.py",),
    ("clip.set_property", "composed_edit"):
        ("tests/qualification/test_golden_conversation_live.py",),
}


def _executor_coverage() -> dict[str, set[str]]:
    """Operation ids accepted by each existing production executor."""
    # These are operation handlers in reel_touchup, not every name accepted
    # by the caller-facing touch-up vocabulary.
    touchup = {
        "clip.move", "overlay.swap_pixels", "overlay.replace", "overlay.add", "overlay.remove",
        "clip.set_enabled", "clip.ripple", "clip.slip", "retime", "clip.set_property",
        "audio.fade", "audio.gain", "transition", "overlay.resize", "overlay.position", "clip.split", "clip.slide", "clip.roll", "track.add", "overlay.entry_motion", "hold.repair_window",
    }
    # `composed_edit.set_properties` is the measured in-place property
    # writer. Other touch-up operations compile through the workflow but
    # are not directly implemented by this function.
    composed = {"clip.set_property"}
    return {
        "edit_patch": {op_id for op_id, op in ALGEBRA.items()
                       if op.apply is not None},
        "feedback_patch_apply": {
            "marker.add", "marker.delete", "clip_marker.add",
            "clip_marker.delete", "clip.set_property", "clip.set_enabled"},
        "reel_touchup": touchup,
        "composed_edit": composed,
        # AXI command adapters are registered only where an existing command
        # performs a guarded write with operation-specific read-back.
        "resolve_axi": {
            "marker.add", "clip.delete", "clip.remove",
            "clip.smart_reframe",
            # Existing AXI commands now have semantic EditRequest adapters.
            # They remain implemented/unqualified until operation-specific
            # live qualification records are added.
            "clip.trim", "caption_fix", "broll.insert", "multicam.select_angle",
            "caption.style",
            # AXI-11: the journaled `edit place` verb IS the guarded
            # placement path for a plain pool-item insert, so the router
            # can name the path that ran.  It stays UNQUALIFIED - the
            # evidence that would promote it belongs to the live
            # qualification lane, and nothing here grants it.
            "clip.insert",
            # Planning-level: no command of its own, executed through the
            # trim/delete commands its carried edits name. Unqualified
            # until the live-requests lane records qualifying evidence.
            "reel.opening",
        },
        "reel_append_placement": {"clip.insert"},
        "reel_otio_placement": {"clip.insert"},
        # Generic native invocation is registered only for semantic ops that
        # already have a documented, typed method mapping. The adapter is
        # implemented but remains unqualified until live Resolve evidence is
        # recorded for this exact op/backend pair.
        "resolve_native_invocation": {"marker.add"},
        # These controls have explicit UI recipes, but none has completed a
        # live qualification run. Discovery must not imply production access.
        "resolve.ui": {
            "color.window.create", "color.window.modify",
            "color.window.track",
        },
    }


def _resolve_capabilities(op: AlgebraOp) -> tuple[str, ...]:
    if op.id in {"clip.insert", "broll.insert"}:
        return ("media pool", "timeline placement")
    if op.id.startswith(("marker.", "clip_marker.")):
        return ("timeline markers",) if not op.id.startswith("clip_") else ("timeline item markers",)
    if op.id.startswith("clip."):
        return ("timeline item read/write",)
    if op.id.startswith("reel."):
        # A reel op compiles to timeline-item structural edits, so it
        # reads and writes exactly what those edits do - no wider claim.
        return ("timeline item read/write",)
    if op.id.startswith("overlay."):
        return ("media pool", "timeline placement", "Fusion or item properties")
    if op.id.startswith("track."):
        return ("timeline track read/write",)
    if op.id.startswith("mask."):
        return ("Fusion composition read/write",)
    if op.id.startswith("color."):
        return ("Color page windows/qualifier read/write",)
    if op.id.startswith("grade.") or op.id in {"grade", "clip_lut"}:
        return ("color page node graph",)
    if op.id.startswith("audio.") or op.id == "voice_isolation":
        return ("audio track/item read/write",)
    return ("operation-specific Resolve support",)


def _media_types(op: AlgebraOp) -> tuple[str, ...]:
    if op.domain == "audio_mix":
        return ("audio",)
    if op.id.startswith("overlay."):
        return ("still", "video", "Fusion graphic")
    if op.domain in {"color", "picture_transform", "fusion"}:
        return ("video", "still")
    if op.id in {"clip.insert", "broll.insert", "clip.delete", "clip.remove",
                 "clip.move", "clip.trim",
                 "clip.replace", "clip.split", "clip.join", "clip.roll",
                 "clip.slip", "clip.slide", "clip.ripple", "reel.opening"}:
        return ("video", "audio", "still")
    return ("timeline",)


def _make_op_status(op: AlgebraOp, coverage: dict[str, set[str]]) -> OpStatus:
    backends = []
    for backend, operation_ids in coverage.items():
        if op.id not in operation_ids:
            continue
        qualification = _BACKEND_QUALIFICATIONS.get((op.id, backend), {})
        evidence = tuple(qualification.get(
            "evidence", _LIVE_QUALIFICATION.get((op.id, backend), ())))
        qualification_complete = bool(
            evidence and qualification.get("result") == "passed"
            and qualification.get("resolve_version")
            and qualification.get("media_types")
            and qualification.get("timeline_conditions")
            and qualification.get("qualification_predicates")
            and qualification.get("expected_state")
            and qualification.get("actual_state")
            and qualification.get("preservation_limits")
            and qualification.get("preservation_checks")
            and qualification.get("recovery")
            and "visual_verification" in qualification
            and "audio_verification" in qualification)
        qualification_status = (
            "production_enabled" if qualification_complete
            and qualification.get("production_enabled") else
            "qualified" if qualification_complete else
            "evidence_incomplete" if evidence else "not_qualified")
        backends.append(BackendStatus(
            backend=backend,
            compilable=True,
            qualification_status=qualification_status,
            limitations=(_BACKEND_LIMITATIONS[backend]
                         + _BACKEND_OPERATION_LIMITATIONS.get((op.id, backend), ())
                         + ((op.note,) if op.note else ())),
            qualification_evidence=evidence,
            resolve_version=qualification.get("resolve_version"),
            qualification_media_types=qualification.get("media_types", ()),
            qualification_timeline_conditions=qualification.get("timeline_conditions", ()),
            qualification_predicates=qualification.get("qualification_predicates", ()),
            preservation_limits=qualification.get("preservation_limits", ()),
            visual_verification=qualification.get("visual_verification", ()),
            audio_verification=qualification.get("audio_verification", ()),
            recovery_qualification=qualification.get("recovery", "not live-qualified"),
            implementation_status=("implemented_qualified"
                                   if qualification_status in {"qualified", "production_enabled"}
                                   else "implemented_unqualified"),
            implementation_evidence=_IMPLEMENTATION_EVIDENCE.get((op.id, backend), ()),
        ))
    workflow_evidence = _WORKFLOW_EVIDENCE.get(op.id, ())
    if backends:
        implementation_status = ("implemented_qualified" if any(
            row.qualified for row in backends) else "implemented_unqualified")
    elif op.id in _PLANNING_OPERATION_IDS:
        implementation_status = "planning_only"
    elif workflow_evidence:
        implementation_status = "workflow_only"
    else:
        implementation_status = "no_executor"
    required = tuple(op.required)
    conditions = ["the exact Resolve timeline is open"]
    if "unique_id" in required:
        conditions.append("the target item resolves uniquely on that timeline")
    if "track_type" in required:
        conditions.append("the named track exists or can be created by the op")
    operation_qualification_status = (
        "production_enabled" if any(row.production_enabled for row in backends) else
        "qualified" if any(row.qualified for row in backends) else
        "evidence_incomplete" if any(row.qualification_status == "evidence_incomplete"
                                     for row in backends) else "not_qualified")
    return OpStatus(
        id=op.id,
        param_schema={"type": "object",
                      "properties": {key: {} for key in required},
                      "required": required,
                      "additionalProperties": True},
        state_domains=(op.domain,),
        required_resolve_capabilities=_resolve_capabilities(op),
        media_types=_media_types(op),
        timeline_conditions=tuple(conditions),
        precondition_validator=(
            "EditPatch: library.tools.edit_patch.validate; "
            "wrappers: executor-specific guards"),
        backends=tuple(backends),
        verification_contract=(
            "post-write snapshot checked by "
            f"{op.verify.__name__ if op.verify.__name__ != '<lambda>' else 'the operation-specific algebra verifier'}"
            if op.verify else "missing"),
        inverse_recovery={"op": op.inverse.op,
                          "self_inverse": op.inverse.self_inverse,
                          "irreversible_reason": op.inverse.reason},
        defined=True,
        compilable=bool(backends),
        qualification_status=operation_qualification_status,
        implementation_status=implementation_status,
        workflow_evidence=workflow_evidence,
    )


# Built from this vocabulary and the executor dispatch declarations above.
# This is the stable query surface for routers and inspection tools.
_EXECUTOR_COVERAGE = _executor_coverage()
OP_STATUS: dict[str, OpStatus] = {
    op_id: _make_op_status(op, _EXECUTOR_COVERAGE)
    for op_id, op in ALGEBRA.items()
}

# VC-20 uses the live lifecycle measurements, not target-strategy claims.
# Only operations represented by an isolated or clearly named benchmark
# scenario carry numeric costs; combined workflows say so explicitly.
_MEASURED_EDIT_COSTS = {
    "clip.set_property": {
        "scenario": "single_property_40px",
        "wall_s": 12.9, "exclusive_lease_hold_s": 12.89,
        "resolve_ops_s": 0.57,
        "source": "docs/benchmarks/2026-10-19-vc-02-reel-lifecycle.md",
    },
    "clip.insert": {
        "scenario": "clip_insert_replace (combined)",
        "wall_s": 13.2, "exclusive_lease_hold_s": 13.14,
        "resolve_ops_s": 0.92,
        "source": "docs/benchmarks/2026-10-19-vc-02-reel-lifecycle.md",
    },
    "clip.replace": {
        "scenario": "clip_insert_replace (combined)",
        "wall_s": 13.2, "exclusive_lease_hold_s": 13.14,
        "resolve_ops_s": 0.92,
        "source": "docs/benchmarks/2026-10-19-vc-02-reel-lifecycle.md",
    },
    "clip.ripple": {
        "scenario": "ripple_edit",
        "wall_s": 13.4, "exclusive_lease_hold_s": 13.36,
        "resolve_ops_s": 1.13,
        "source": "docs/benchmarks/2026-10-19-vc-02-reel-lifecycle.md",
    },
}


def _vc20_metadata(op_id: str) -> tuple[str, str, tuple[str, ...], dict]:
    """Return conservative route guidance and recorded evidence for VC-20."""
    safety = OP_SAFETY_CLASS.get(op_id, SAFETY_UNREPRESENTABLE)
    status = OP_STATUS[op_id]
    if safety == SAFETY_READ_ONLY:
        mechanism = "Planner delivery only; no timeline write."
    elif safety == SAFETY_REVERSIBLE_MUTATION:
        mechanism = ("Direct transaction only when the selected backend has a "
                     "qualified executor, independent read-back, and proven inverse; "
                     "otherwise protected staging or refusal.")
    elif safety == SAFETY_COMPLEX_LOCALIZED:
        mechanism = "Protected staged edit with modified and preserved state read-back."
    elif safety == SAFETY_STRUCTURAL:
        mechanism = ("Protected staged edit with dependent-range verification; "
                     "full staged rebuild or refusal when dependencies are unproven.")
    else:
        mechanism = "Refuse until a qualified executor and verification path exist."
    domain = status.state_domains[0] if status.state_domains else "unknown"
    native_state = {
        "timeline_structure": ("markers", "linked_groups", "grades", "Fusion", "audio_effects"),
        "picture_transform": ("grades", "Fusion", "linked_groups", "markers"),
        "color": ("Fusion", "audio_effects", "markers", "linked_groups"),
        "fusion": ("grades", "audio_effects", "markers", "linked_groups"),
        "audio_mix": ("grades", "Fusion", "markers", "linked_groups"),
        "markers": ("grades", "Fusion", "audio_effects", "linked_groups"),
        "captions": ("grades", "Fusion", "audio_effects", "markers"),
    }.get(domain, ("grades", "Fusion", "audio_effects", "markers", "linked_groups"))
    return safety, mechanism, native_state, dict(_MEASURED_EDIT_COSTS.get(op_id, {
        "status": "unmeasured",
        "source": "docs/benchmarks/2026-10-19-vc-02-reel-lifecycle.md",
    }))

# AXI's user-facing control names resolve to the same algebra entries used
# by the executors. These are declarations, not claims of live support:
# the evidence-backed status above remains authoritative for qualification.
AXI_OPERATION_MAP: dict[str, tuple[str, ...]] = {
    "clip.place": ("clip.insert",),
    "clip.trim": ("clip.trim",),
    "clip.move": ("clip.move",),
    "clip.delete": ("clip.delete",),
    "clip.remove": ("clip.remove",),
    "marker.add": ("marker.add",),
    "marker.delete": ("marker.delete",),
}

AXI_OPERATION_LIMITATIONS = {
}


def capability(op_id: str) -> dict | None:
    """JSON-ready evidence-backed capability answer for an algebra op."""
    entry = status(op_id)
    if entry is None:
        return None
    return {
        "id": entry.id,
        "available": entry.compilable,
        "documented": entry.defined,
        "implemented": entry.compilable,
        "live_qualified": entry.qualified,
        "production_enabled": entry.production_enabled,
        "qualification_status": entry.qualification_status,
        "implementation_status": entry.implementation_status,
        "workflow_evidence": list(entry.workflow_evidence),
        "route_evaluation": _ROUTE_EVALUATION.get(entry.id),
        "backends": [{
            "id": backend.backend,
            "implemented": backend.compilable,
            "live_qualified": backend.qualified,
            "production_enabled": backend.production_enabled,
            "qualification_evidence": list(backend.qualification_evidence),
            "limitations": list(backend.limitations),
            "qualification": {
                "resolve_version": backend.resolve_version,
                "media_types": list(backend.qualification_media_types),
                "timeline_conditions": list(backend.qualification_timeline_conditions),
                "preservation_limits": list(backend.preservation_limits),
                "visual_verification": list(backend.visual_verification),
                "audio_verification": list(backend.audio_verification),
                "recovery": backend.recovery_qualification,
            },
            "implementation_status": backend.implementation_status,
            "qualification_status": backend.qualification_status,
            "implementation_evidence": list(backend.implementation_evidence),
        } for backend in entry.backends],
        "supported_backend": [backend.backend for backend in entry.backends],
        # Fusion preservation is a measured property, not implied by a
        # generic clip move or by a successful API return.
        "preserves_fusion": {
            "value": None,
            "conditions": [],
            "qualification_evidence": [],
        },
        "required_resolve_capabilities": list(entry.required_resolve_capabilities),
        "media_types": list(entry.media_types),
        "timeline_conditions": list(entry.timeline_conditions),
        "verification": entry.verification_contract,
        "inverse_recovery": entry.inverse_recovery,
        "recovery_capability": {
            "proven_backends": [
                backend.backend for backend in entry.backends
                if proven_inverse(op_id, backend.backend)],
            "proven_evidence": list(proven_inverse(op_id)),
        },
        "safety_class": entry.safety_class,
        "minimal_safe_mechanism": entry.minimal_safe_mechanism,
        "target_strategy": entry.target_strategy,
        "unsupported_native_state": list(entry.unsupported_native_state),
        "measured_cost": entry.measured_cost,
    }


def status(op_id: str, backend: str | None = None):
    """Return status metadata for an operation, optionally on one backend.

    An unknown id returns ``None``. A known id with an unknown or
    unsupported backend returns ``None`` from ``for_backend``.
    """
    entry = OP_STATUS.get(op_id)
    if entry is None or backend is None:
        return entry
    return entry.for_backend(backend)


def is_production_enabled(op_id: str, backend: str) -> bool:
    """Whether this op is live-qualified and enabled on ``backend``."""
    entry = status(op_id, backend)
    return bool(entry and entry.production_enabled)


# ── VC-11 execution safety classes ────────────────────────────────
#
# Every algebra op carries exactly one of six classes. The class states the
# execution policy; the scope of the change decides the work, the risk
# decides the protection. Declared reversibility (``Inverse`` above) never
# authorizes a direct write: only qualification evidence proves a recovery,
# and only an independent read-back judges it. Uncertain work defaults to
# protected staging or refusal. The merged execution router enforces this
# table; no caller flag can widen a class to direct.

SAFETY_READ_ONLY = "read_only"
SAFETY_NO_OP = "no_op"
SAFETY_REVERSIBLE_MUTATION = "reversible_mutation"
SAFETY_COMPLEX_LOCALIZED = "complex_localized"
SAFETY_STRUCTURAL = "structural"
SAFETY_UNREPRESENTABLE = "unrepresentable_or_unqualified"

SAFETY_CLASSES: tuple[str, ...] = (
    SAFETY_READ_ONLY,
    SAFETY_NO_OP,
    SAFETY_REVERSIBLE_MUTATION,
    SAFETY_COMPLEX_LOCALIZED,
    SAFETY_STRUCTURAL,
    SAFETY_UNREPRESENTABLE,
)

#: Most restrictive first. A mixed request is judged by its most
#: restrictive member; ``no_op`` is a router outcome, never a member.
_SAFETY_RESTRICTION: tuple[str, ...] = (
    SAFETY_UNREPRESENTABLE,
    SAFETY_STRUCTURAL,
    SAFETY_COMPLEX_LOCALIZED,
    SAFETY_REVERSIBLE_MUTATION,
    SAFETY_READ_ONLY,
    SAFETY_NO_OP,
)


@dataclass(frozen=True)
class SafetyPolicy:
    """What one safety class permits on the write path."""

    direct_allowed: bool
    staged_allowed: bool
    requires_qualified_executor: bool
    requires_readback: bool
    note: str


SAFETY_CLASS_POLICY: dict[str, SafetyPolicy] = {
    SAFETY_READ_ONLY: SafetyPolicy(
        direct_allowed=False, staged_allowed=False,
        requires_qualified_executor=False, requires_readback=False,
        note="a plan delivery to its owning planner, never a Resolve "
             "write; the write router refuses it and names the planner"),
    SAFETY_NO_OP: SafetyPolicy(
        direct_allowed=False, staged_allowed=False,
        requires_qualified_executor=False, requires_readback=False,
        note="the request is already satisfied: no Resolve write is "
             "issued and no revision is recorded"),
    SAFETY_REVERSIBLE_MUTATION: SafetyPolicy(
        direct_allowed=True, staged_allowed=True,
        requires_qualified_executor=True, requires_readback=True,
        note="direct only with proof on the chosen backend: a "
             "production-enabled executor, an independent read-back, and "
             "a proven executable inverse; anything less stays staged"),
    SAFETY_COMPLEX_LOCALIZED: SafetyPolicy(
        direct_allowed=False, staged_allowed=True,
        requires_qualified_executor=True, requires_readback=True,
        note="protected staged only: a candidate timeline, the requested "
             "ops alone, and scoped verification of changed and unchanged "
             "state; complex native state (Fusion, Color, audio lanes) is "
             "never edited in place without proven preservation"),
    SAFETY_STRUCTURAL: SafetyPolicy(
        direct_allowed=False, staged_allowed=True,
        requires_qualified_executor=True, requires_readback=True,
        note="staged only with dependent-range verification: topology, "
             "timing and identity effects past the edited item are "
             "re-verified; an unqualified staged edit falls back to an "
             "explicit full rebuild, never to direct"),
    SAFETY_UNREPRESENTABLE: SafetyPolicy(
        direct_allowed=False, staged_allowed=False,
        requires_qualified_executor=True, requires_readback=True,
        note="refuse, or an approved alternative: no executor proves "
             "this operation, so no write path may take it until new "
             "qualification evidence lands"),
}

#: One class per algebra op. Smallness never promotes an op: a one-field
#: toggle without qualification evidence or read-back is staged or refused
#: like any other unproven write.
OP_SAFETY_CLASS: dict[str, str] = {
    # Plan deliveries: read by planners, never written to Resolve.
    "angle_plan": SAFETY_READ_ONLY,
    "plan_change": SAFETY_READ_ONLY,
    "transition": SAFETY_COMPLEX_LOCALIZED,
    "shot_selection": SAFETY_READ_ONLY,
    "speech_selection": SAFETY_READ_ONLY,
    "story_pacing": SAFETY_READ_ONLY,
    "music_selection": SAFETY_READ_ONLY,
    "audio_mix": SAFETY_READ_ONLY,
    "look": SAFETY_READ_ONLY,
    "visual_effect": SAFETY_READ_ONLY,
    "sound_effect": SAFETY_READ_ONLY,
    "subject_effect": SAFETY_READ_ONLY,
    "motion_graphic": SAFETY_READ_ONLY,
    "brand_asset": SAFETY_READ_ONLY,
    "end_card": SAFETY_READ_ONLY,
    # Single-scope writes with snapshot read-back; direct only when proven.
    "marker.add": SAFETY_REVERSIBLE_MUTATION,
    "marker.delete": SAFETY_REVERSIBLE_MUTATION,
    "clip_marker.add": SAFETY_REVERSIBLE_MUTATION,
    "clip_marker.delete": SAFETY_REVERSIBLE_MUTATION,
    "clip.set_enabled": SAFETY_REVERSIBLE_MUTATION,
    "clip.set_property": SAFETY_REVERSIBLE_MUTATION,
    "track.rename": SAFETY_REVERSIBLE_MUTATION,
    "grade.node_set": SAFETY_REVERSIBLE_MUTATION,
    # Localized but complex: protected staged, never direct.
    "clip.smart_reframe": SAFETY_COMPLEX_LOCALIZED,
    "picture.keyframe": SAFETY_COMPLEX_LOCALIZED,
    "clip.replace": SAFETY_COMPLEX_LOCALIZED,
    "clip.link": SAFETY_COMPLEX_LOCALIZED,
    "clip.unlink": SAFETY_COMPLEX_LOCALIZED,
    "mask.create": SAFETY_COMPLEX_LOCALIZED,
    "mask.delete": SAFETY_COMPLEX_LOCALIZED,
    "mask.edit": SAFETY_COMPLEX_LOCALIZED,
    "mask.track": SAFETY_COMPLEX_LOCALIZED,
    "grade.node_add": SAFETY_COMPLEX_LOCALIZED,
    "grade.node_remove": SAFETY_COMPLEX_LOCALIZED,
    "overlay.add": SAFETY_COMPLEX_LOCALIZED,
    "overlay.remove": SAFETY_COMPLEX_LOCALIZED,
    "overlay.swap_pixels": SAFETY_COMPLEX_LOCALIZED,
    "overlay.replace": SAFETY_COMPLEX_LOCALIZED,
    "overlay.resize": SAFETY_COMPLEX_LOCALIZED,
    "overlay.position": SAFETY_COMPLEX_LOCALIZED,
    "overlay.entry_motion": SAFETY_COMPLEX_LOCALIZED,
    "audio.gain": SAFETY_COMPLEX_LOCALIZED,
    "audio.fade": SAFETY_COMPLEX_LOCALIZED,
    "audio.automation": SAFETY_COMPLEX_LOCALIZED,
    "voice_isolation": SAFETY_COMPLEX_LOCALIZED,
    "clip_lut": SAFETY_COMPLEX_LOCALIZED,
    "grade": SAFETY_COMPLEX_LOCALIZED,
    "caption_fix": SAFETY_COMPLEX_LOCALIZED,
    "caption.style": SAFETY_COMPLEX_LOCALIZED,
    "transform_override": SAFETY_COMPLEX_LOCALIZED,
    "clip.roll": SAFETY_COMPLEX_LOCALIZED,
    "clip.slip": SAFETY_COMPLEX_LOCALIZED,
    "redraw_closer": SAFETY_COMPLEX_LOCALIZED,
    "hold.repair_window": SAFETY_COMPLEX_LOCALIZED,
    # Topology, timing or identity effects: staged with dependent ranges.
    "clip.delete": SAFETY_STRUCTURAL,
    "clip.remove": SAFETY_STRUCTURAL,
    "clip.insert": SAFETY_STRUCTURAL,
    "broll.insert": SAFETY_STRUCTURAL,
    "clip.split": SAFETY_STRUCTURAL,
    "clip.join": SAFETY_STRUCTURAL,
    "clip.trim": SAFETY_STRUCTURAL,
    "clip.ripple": SAFETY_STRUCTURAL,
    "clip.slide": SAFETY_STRUCTURAL,
    "clip.move": SAFETY_STRUCTURAL,
    "retime": SAFETY_STRUCTURAL,
    "retime.segments": SAFETY_STRUCTURAL,
    "span_retime": SAFETY_STRUCTURAL,
    "compound.create": SAFETY_STRUCTURAL,
    "compound.flatten": SAFETY_STRUCTURAL,
    "track.add": SAFETY_STRUCTURAL,
    "track.delete": SAFETY_STRUCTURAL,
    "track.reorder": SAFETY_STRUCTURAL,
    "drop_fragment": SAFETY_STRUCTURAL,
    "reel.opening": SAFETY_STRUCTURAL,
    # No executor proves these: refuse or an approved alternative.
    "audio.pan": SAFETY_UNREPRESENTABLE,
    "grade.node_reorder": SAFETY_UNREPRESENTABLE,
    "color.window.create": SAFETY_UNREPRESENTABLE,
    "color.window.delete": SAFETY_UNREPRESENTABLE,
    "color.window.modify": SAFETY_UNREPRESENTABLE,
    "color.window.track": SAFETY_UNREPRESENTABLE,
    "color.qualifier.sample": SAFETY_UNREPRESENTABLE,
    "color.qualifier.adjust": SAFETY_UNREPRESENTABLE,
    "multicam.angle": SAFETY_UNREPRESENTABLE,
    # The camera switch HAS an executor: the AXI adapter runs the
    # journaled place-then-delete replacement and reads the replaced
    # source range back. It was classed unrepresentable while it was a
    # canonical NAME only, and that class refused it before any route
    # was chosen. It is a localized source replacement at the same
    # record span, exactly as `clip.replace` is.
    "multicam.select_angle": SAFETY_COMPLEX_LOCALIZED,
}

VC20_TARGET_STRATEGY = {
    "clip.set_property": "property patch",
    "clip.set_enabled": "property patch",
    "marker.add": "direct transaction, after inverse qualification",
    "marker.delete": "direct transaction, after inverse qualification",
    "clip_marker.add": "direct transaction, after inverse qualification",
    "clip_marker.delete": "direct transaction, after inverse qualification",
    "overlay.add": "incremental staged edit",
    "overlay.remove": "incremental staged edit",
    "clip.move": "incremental staged edit",
    "clip.replace": "incremental staged edit with source/effect preservation",
    "clip.trim": "incremental staged edit with timing verification",
    "broll.insert": "staged cover-track placement with speech preservation, once qualified",
    "caption_fix": "targeted caption regeneration",
    "caption.style": "targeted caption regeneration in the requested style",
    "clip.ripple": "dependency-aware structural transaction",
    "reel.opening": "compiled structural edits with dependent-range verification",
    "grade.node_set": "native grade transaction once qualified",
    "grade.node_add": "native grade transaction once qualified",
    "grade.node_remove": "native grade transaction once qualified",
    "grade.node_reorder": "native grade transaction once qualified",
    "mask.create": "native Fusion transaction once qualified",
    "mask.delete": "native Fusion transaction once qualified",
    "mask.edit": "native Fusion transaction once qualified",
    "mask.track": "native Fusion transaction once qualified",
    "compound.create": "full staged rebuild for restructure",
    "compound.flatten": "full staged rebuild for restructure",
    "track.add": "full staged rebuild for restructure",
    "track.delete": "full staged rebuild for restructure",
    "track.reorder": "full staged rebuild for restructure",
    "clip.split": "full staged rebuild for restructure",
    "clip.join": "full staged rebuild for restructure",
}

# Complete the status registry after its safety vocabulary is available.
# Every op receives a conservative route and an explicit measured or
# unmeasured cost record.
OP_STATUS = {
    op_id: replace(
        entry,
        safety_class=(metadata := _vc20_metadata(op_id))[0],
        minimal_safe_mechanism=metadata[1],
        unsupported_native_state=metadata[2],
        measured_cost=metadata[3],
        target_strategy=VC20_TARGET_STRATEGY.get(
            op_id, "not specified in VC-20"),
    )
    for op_id, entry in OP_STATUS.items()
}

#: Ops whose verifier reads the after-snapshot and can fail. A vacuous
#: ``lambda op, after: None`` always passes, so it is not read-back, and
#: neither is a missing verifier. Only these ops satisfy the read-back
#: half of a direct proof.
_INDEPENDENT_READBACK_OPS: frozenset[str] = frozenset({
    "marker.add", "marker.delete", "clip_marker.add", "clip_marker.delete",
    "clip.delete", "clip.remove", "clip.insert", "broll.insert", "clip.split",
    "clip.join", "clip.trim",
    "clip.ripple", "clip.roll", "clip.slip", "clip.slide", "clip.move",
    "clip.replace", "clip.set_enabled",
    "clip.smart_reframe", "clip.set_property",
    "retime", "retime.segments",
    "picture.keyframe",
    "grade.node_set",
    "mask.create", "mask.delete", "mask.edit", "mask.track",
    "compound.create", "compound.flatten",
    "track.add", "track.delete", "track.rename", "track.reorder",
    "overlay.remove",
    "transform_override", "span_retime", "drop_fragment",
    "multicam.select_angle",
    "caption.style",
    "caption_fix",
    "reel.opening",
})

#: Router labels that name the matrix backend they prove against. The
#: property arm reads ``edit_patch`` rows; the label is only a route name.
_ROUTER_BACKEND_PROOF: dict[str, str] = {
    "edit_patch": "edit_patch",
    "edit_patch.property": "edit_patch",
    "resolve_axi": "resolve_axi",
}


def safety_class(op_id: str) -> str | None:
    """The VC-11 safety class for an algebra op, or None when unknown."""
    return OP_SAFETY_CLASS.get(op_id)


def request_safety_class(op_ids: Sequence[str]) -> str | None:
    """The most restrictive known class in a request, or None.

    ``no_op`` is a router outcome, never a member: an empty or fully
    unknown request has no class and keeps its existing refusal.
    """
    known = [OP_SAFETY_CLASS[op_id] for op_id in op_ids
             if op_id in OP_SAFETY_CLASS]
    if not known:
        return None
    return min(known, key=_SAFETY_RESTRICTION.index)


def has_independent_readback(op_id: str) -> bool:
    """Whether the op's verifier reads the after-snapshot and can fail."""
    return op_id in _INDEPENDENT_READBACK_OPS


def proven_inverse(op_id: str, backend: str | None = None) -> tuple[str, ...]:
    """Qualification evidence proving the op's inverse executes.

    The declared inverse (``inverse_recovery`` on the status) is only a
    name; a name nobody qualified is not recovery. A self-inverse op is
    proven by its own evidence; a paired op by its partner's evidence on
    the same backend. An irreversible op proves nothing, ever.
    """
    spec = ALGEBRA.get(op_id)
    if spec is None or spec.inverse.irreversible:
        return ()
    target = op_id if spec.inverse.self_inverse else spec.inverse.op
    entry = OP_STATUS.get(target)
    if entry is None:
        return ()
    if backend is not None:
        proof_backend = _ROUTER_BACKEND_PROOF.get(backend, backend)
        row = entry.for_backend(proof_backend)
        return tuple(row.qualification_evidence) if row is not None else ()
    return tuple(evidence for row in entry.backends
                 for evidence in row.qualification_evidence)


def direct_blockers(op_ids: Sequence[str], backend: str) -> tuple[str, ...]:
    """Why each op must not go direct on ``backend``; empty means proven.

    Direct needs all three at once: the reversible-mutation class, an
    independent read-back, and qualification evidence for both the op and
    its inverse on the chosen backend. Smallness and a named inverse prove
    nothing and block nothing here.
    """
    proof_backend = _ROUTER_BACKEND_PROOF.get(backend, backend)
    blockers: list[str] = []
    for op_id in op_ids:
        cls = OP_SAFETY_CLASS.get(op_id)
        if cls != SAFETY_REVERSIBLE_MUTATION:
            blockers.append(
                f"{op_id} is {cls or 'unclassified'} - direct needs "
                f"{SAFETY_REVERSIBLE_MUTATION}")
            continue
        if not has_independent_readback(op_id):
            blockers.append(f"{op_id} has no independent read-back")
        entry = status(op_id, proof_backend)
        if entry is None or not entry.production_enabled:
            blockers.append(
                f"{op_id} has no production-enabled executor on "
                f"{proof_backend}")
        if not proven_inverse(op_id, proof_backend):
            spec = ALGEBRA.get(op_id)
            declared = (op_id if spec is not None and spec.inverse.self_inverse
                        else (spec.inverse.op if spec is not None else None))
            blockers.append(
                f"{op_id} declares inverse {declared!r} but no "
                f"qualification evidence proves it on {proof_backend}")
    return tuple(blockers)


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
# The computational core (span, verify) is ported to Rust
# (`native/ren-core`, the `ren_native` extension). One switch
# (`library/tools/edit_backend.py`) routes these through it by default;
# Python is the selectable fallback. The typed `key` API remains Python;
# Rust composition uses its own native key parts. Declarations above stay
# the Python source of truth - the parity harness compares the two.


def span(op, base):
    """The frames an op touches over a base snapshot, on the active backend."""
    rust = edit_backend.rust()
    if rust is not None:
        from library.tools.perf_ledger import count_call, span
        count_call("edit_core_calls")
        with span("edit_core_rust", backend="rust", calls=1,
                  **edit_backend.boundary_shape(
                      base, [op],
                      projection=edit_backend.projection_of([op]))):
            return rust.span(op, base)
    spec = ALGEBRA.get(op.get("op"))
    if spec is None:
        return None
    return spec.span(op, base)


def spans(operations, base):
    """The spans for operations sharing one base snapshot."""
    rust = edit_backend.rust()
    if rust is not None:
        base_handle = edit_backend.snapshot_handle(base, operations)
        from library.tools.perf_ledger import count_call
        from library.tools.perf_ledger import span as ledger_span
        count_call("edit_core_calls")
        with ledger_span("edit_core_rust", backend="rust", calls=1,
                         **edit_backend.boundary_shape(
                             base, operations,
                             projection=edit_backend.projection_of(
                                 operations))):
            return rust.span_many(operations, base_handle)
    with indexed_snapshot(base):
        return [span(op, base) for op in operations]


def key(op):
    """What an op writes, preserving the typed declaration key contract."""
    # The Python contract preserves typed key parts (notably integer indices)
    # and validates GUI-only color operations that are not edit-patch ops.
    # Rust's string projection is for the parity boundary; compose uses its
    # own typed native keys internally.
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
        from library.tools.perf_ledger import count_call, span
        count_call("edit_core_calls")
        with span("edit_core_rust", backend="rust", calls=1,
                  **edit_backend.boundary_shape(
                      after, [op],
                      projection=edit_backend.projection_of([op]))):
            return rust.verify(op, after)
    spec = ALGEBRA.get(op.get("op"))
    if spec is None:
        return None
    return spec.verify(op, after)


def verifies(operations, after):
    """Read-back judgements for operations sharing one snapshot."""
    if any(_beyond_ported_core(op) for op in operations):
        return [verify(op, after) for op in operations]
    rust = edit_backend.rust()
    if rust is not None:
        after_handle = edit_backend.snapshot_handle(
            after, operations, include_transform=True)
        from library.tools.perf_ledger import count_call, span
        count_call("edit_core_calls")
        with span("edit_core_rust", backend="rust", calls=1,
                  **edit_backend.boundary_shape(
                      after, operations,
                      projection=edit_backend.projection_of(operations))):
            return rust.verify_many(operations, after_handle)
    with indexed_snapshot(after):
        return [verify(op, after) for op in operations]
