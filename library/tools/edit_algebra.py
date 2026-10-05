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
    apply   (op, timeline, item_handle | None) -> returned   the native write
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

from collections.abc import Callable
from dataclasses import dataclass

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


def _unapplied(op, _timeline, _item):
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


def _verify_fusion_comps(expected_names):
    def verify(op, after):
        clip = _clip(after, op["unique_id"])
        if clip is None:
            return f"clip {op['unique_id']!r} is not on the timeline"
        names = (clip.get("fusion") or {}).get("comp_names") or []
        missing = [n for n in expected_names if n not in names]
        if missing:
            return (f"clip {op['unique_id']!r} carries no Fusion comp "
                    f"{missing}")
        return None
    return verify


# ── Apply helpers (the native writes) ──────────────────────────────────


def _apply_set_property(op, _timeline, item):
    from library.tools.transform_write_log import set_property

    return set_property(
        item, op["key"], op["value"],
        item_identity={"resolve_unique_id": str(op["unique_id"])})


def _apply_smart_reframe(op, _timeline, item):
    returned = item.SmartReframe()
    if not returned:
        raise RuntimeError("SmartReframe answered False")
    return returned


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
        apply=lambda op, tl, _item: tl.AddMarker(
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
        apply=lambda op, tl, _item: tl.DeleteMarkerAtFrame(int(op["frame"])),
        verify=lambda op, after: _verify_marker_delete(op, after),
        key=lambda op: ("marker", int(op["frame"])), merge="commutative",
        inverse=_pair("marker.add", "re-adds the deleted marker's fields"),
        note="The inverse carries the deleted marker's color/name/note."),
    "clip_marker.add": AlgebraOp(
        id="clip_marker.add", domain="markers",
        required=("unique_id", "frame", "color", "name"),
        span=_item_span, item=True,
        apply=lambda op, _tl, item: (
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
        apply=lambda op, _tl, item: item.DeleteMarkerAtFrame(
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
                  "record_in", "record_out"),
        span=lambda op, base: (int(op["record_in"]), int(op["record_out"])),
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
        apply=lambda op, tl, item: item.SetClipEnabled(bool(op["enabled"])),
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
        apply=lambda op, tl, item: tl.SetClipsLinked(
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
        apply=lambda op, tl, item: tl.SetClipsLinked([item], False),
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
        verify=lambda op, after: _verify_retime(op, after),
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
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "keyframe", op["param"]),
        merge="replace",
        inverse=_self("the inverse carries the previous keyframes"),
        note="A keyframed picture parameter; the shadow read-back carries "
             "no keyframes, so the executor verifies by re-read."),

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
        note="Per-clip gain in dB; the OTIO mix carries it."),
    "audio.pan": AlgebraOp(
        id="audio.pan", domain="audio_mix",
        required=("unique_id", "pan"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "audio", "pan"),
        merge="replace",
        inverse=_self("the inverse carries the previous pan")),
    "audio.fade": AlgebraOp(
        id="audio.fade", domain="audio_mix",
        required=("unique_id", "fade_in", "fade_out"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "audio", "fade"),
        merge="replace",
        inverse=_self("the inverse carries the previous envelope"),
        note="Fade in/out with curve; the OTIO mix carries it."),
    "audio.automation": AlgebraOp(
        id="audio.automation", domain="audio_mix",
        required=("unique_id", "param", "keyframes"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "audio", "automation",
                        op["param"]),
        merge="replace",
        inverse=_self("the inverse carries the previous keyframes"),
        note="A keyframed audio lane; the OTIO mix carries it."),

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
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["node_index"])),
        merge="exclusive",
        inverse=_pair("grade.node_remove"),
        note="Add a colour node; the node graph re-read verifies."),
    "grade.node_remove": AlgebraOp(
        id="grade.node_remove", domain="color",
        required=("unique_id", "node_index"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["node_index"])),
        merge="exclusive",
        inverse=_pair("grade.node_add")),
    "grade.node_reorder": AlgebraOp(
        id="grade.node_reorder", domain="color",
        required=("unique_id", "from_index", "to_index"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["from_index"])),
        merge="exclusive",
        inverse=_self("the inverse swaps the nodes back")),
    "grade.node_set": AlgebraOp(
        id="grade.node_set", domain="color",
        required=("unique_id", "node_index", "params"),
        span=_item_span, item=True,
        verify=lambda op, after: None,
        key=lambda op: ("clip", op["unique_id"], "grade",
                        int(op["node_index"])),
        merge="replace",
        inverse=_self("the inverse carries the previous node grade")),

    # ── mask (domain: fusion) ──────────────────────────────────────
    "mask.create": AlgebraOp(
        id="mask.create", domain="fusion",
        required=("unique_id", "matte"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_fusion_comps([op["matte"]]),
        key=lambda op: ("clip", op["unique_id"], "mask"),
        merge="exclusive",
        inverse=_pair("mask.edit", "the inverse removes the matte"),
        note="Place a matte; the Fusion comp list verifies."),
    "mask.edit": AlgebraOp(
        id="mask.edit", domain="fusion",
        required=("unique_id", "matte", "geometry"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_fusion_comps([op["matte"]]),
        key=lambda op: ("clip", op["unique_id"], "mask"),
        merge="replace",
        inverse=_self("the inverse carries the previous geometry")),
    "mask.track": AlgebraOp(
        id="mask.track", domain="fusion",
        required=("unique_id", "transform"),
        span=_item_span, item=True,
        verify=lambda op, after: _verify_fusion_comps([op["matte"]]),
        key=lambda op: ("clip", op["unique_id"], "mask"),
        merge="replace",
        inverse=_self("the inverse carries the previous transform")),

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
        apply=lambda op, tl, _item: tl.AddTrack(op["track_type"],
                                                 op["name"]),
        verify=lambda op, after: _verify_track_named(op["name"])(op, after),
        key=lambda op: ("track", op["track_type"], -1), merge="exclusive",
        inverse=_pair("track.delete"),
        note="A new named row; the touch-up's `add_row` views here."),
    "track.delete": AlgebraOp(
        id="track.delete", domain="timeline_structure",
        required=("track_type", "track_index"),
        span=_track_span,
        apply=lambda op, tl, _item: tl.DeleteTrack(op["track_type"],
                                                    int(op["track_index"])),
        verify=_verify_track_present(False),
        key=lambda op: ("track", op["track_type"],
                        int(op["track_index"])), merge="exclusive",
        inverse=_pair("track.add", "re-adds the row with its name")),
    "track.rename": AlgebraOp(
        id="track.rename", domain="timeline_structure",
        required=("track_type", "track_index", "name"),
        span=_track_span,
        apply=lambda op, tl, _item: tl.SetTrackName(
            op["track_type"], int(op["track_index"]), op["name"]),
        verify=lambda op, after: _verify_track_name(op["name"])(op, after),
        key=lambda op: ("track", op["track_type"],
                        int(op["track_index"])), merge="replace",
        inverse=_self("the inverse carries the previous name")),
    "track.reorder": AlgebraOp(
        id="track.reorder", domain="timeline_structure",
        required=("track_type", "from_index", "to_index"),
        span=_timeline_span,
        verify=lambda op, after: None,
        key=lambda op: ("track", op["track_type"],
                        int(op["from_index"])), merge="exclusive",
        inverse=_self("the inverse swaps the rows back"),
        note="Move a row to a new index; the track listing verifies."),

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
