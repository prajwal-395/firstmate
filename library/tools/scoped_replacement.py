"""AXI-007: a source replacement that changes EXACTLY ONE timeline item.

THE DEFECT
----------
``clip.replace`` re-points the MEDIA POOL ITEM the target draws from.
A media pool item is PROJECT-WIDE, and Resolve draws every timeline item
that references it from that one item, so the replacement changed every
other occurrence of the same source - on this timeline and on every
other - without saying so.  The engine documented the hazard and handed
the decision to the caller: the ``clip.replace`` preservation envelope
in ``library/tools/edit_algebra.py`` says a replacement onto a shared
pool item is "the caller's decision, not the engine's".

THE RULE
--------
A replacement changes exactly the item it names.

Two routes, and the engine picks by MEASUREMENT, never by request:

* the pool item is used by the target ALONE - the pool item is
  re-pointed.  Nothing else can see it, and the item keeps its Fusion
  compositions and its grade because Resolve keeps those on the ITEM.
* the pool item is used by others as well - the item is RE-PLACED: the
  new media is placed at the same record span on the same row, the old
  item is removed, and the transform, the timing, the audio link, the
  Fusion compositions and the grade are carried across.  The other
  occurrences never see the write.

Resolve exposes no per-item media setter: ``TimelineItem`` has no way
to re-point one occurrence, and ``MediaPoolItem.ReplaceClip`` is the
POOL's.  So the scoped route is a re-place, and a re-place gives the
item a NEW native id.  The receipt therefore carries the mapping from
the old id to the new one, and the mapping is VERIFIED against the
editorial identity (source, source window, row) rather than assumed.

WHAT IS REFUSED, AND WHY IT IS REFUSED BEFORE ANY WRITE
------------------------------------------------------
* a target that draws from no pool item (a generator, a title) - there
  is no media to re-point and nothing to carry;
* a replacement media that is not in the project's media pool and
  cannot be imported into it;
* an item whose grade cannot be carried to a re-placed item: the Graph
  API has no ``AddNode``, so a grade of more than one node cannot be
  rebuilt on the new item, and a re-placed item comes back with one
  node;
* an item whose Fusion composition cannot be exported and re-imported.

Each refusal happens BEFORE the original is removed, so a refused
replacement leaves the timeline exactly as it was.  A failure AFTER the
removal restores the original: the capture holds everything the
original carried, and the original's own pool item is untouched, so the
original media is placed back at its own span.

`library/tools/editorial_identity.py` owns what a clip IS;
`library/tools/timeline_addressing.py` owns how a live item is found
again; `library/tools/edit_state_preservation.py` owns the clip
metadata a re-placed item must carry.  This module owns the RULE.

The incident behind the rule is in
`docs/RULE_EVIDENCE.md#the-replacement-that-changed-every-occurrence`,
and `tests/unit/resolve/test_scoped_replacement.py` names the defects
this closes.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class ScopedReplacementError(RuntimeError):
    """The replacement could not be scoped, and nothing was written.

    ``reason`` is the machine-readable cause, so a caller can tell a
    refusal it may retry from one it may not.
    """

    def __init__(self, message: str, *, reason: str = "unscoped"):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Use:
    """One timeline item that draws from a media pool item."""

    timeline: str
    timeline_id: str
    uid: str
    track_type: str
    track_index: int
    record_in: int
    record_out: int
    source_file: str

    def as_dict(self) -> dict:
        return {
            "timeline": self.timeline,
            "timeline_id": self.timeline_id,
            "unique_id": self.uid,
            "track_type": self.track_type,
            "track_index": self.track_index,
            "record_in": self.record_in,
            "record_out": self.record_out,
            "source_file": self.source_file,
        }


# ── Reading ───────────────────────────────────────────────────────────


def _safe(call, default=None):
    try:
        value = call()
    except Exception:  # noqa: BLE001 - a Resolve proxy raises many things
        return default
    return default if value is None else value


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pool_item_uid(pool_item) -> str:
    """The media pool item's own id, or "" where it has none."""
    if pool_item is None:
        return ""
    return str(_safe(pool_item.GetUniqueId, "") or "")


def pool_item_path(pool_item) -> str:
    """The media pool item's file path, or "" where it has none."""
    if pool_item is None:
        return ""
    return str(_safe(lambda: pool_item.GetClipProperty("File Path"), "") or "")


def item_pool_item(item):
    """The media pool item a timeline item draws from, or None."""
    if item is None:
        return None
    return _safe(item.GetMediaPoolItem, None)


def item_pool_uid(item) -> str:
    """The id of the pool item a timeline item draws from."""
    return pool_item_uid(item_pool_item(item))


def pool_item_uses(project, pool_item, *,
                   exclude_uid: str = "") -> tuple[Use, ...]:
    """Every timeline item that draws from this pool item.

    EVERY timeline of the project is walked, because a media pool item
    is project-wide: an occurrence on another timeline is exactly the
    unrelated use the rule is about.

    An item that cannot be read is skipped rather than counted as a
    use: an unread handle is not evidence of a second occurrence, and
    the route this measurement selects is chosen by what was MEASURED.
    """
    uid = pool_item_uid(pool_item)
    if not uid:
        return ()
    uses: list[Use] = []
    timelines = _safe(project.GetTimelineCount, 0) or 0
    for index in range(1, int(timelines) + 1):
        timeline = _safe(lambda index=index: project.GetTimelineByIndex(index),
                         None)
        if timeline is None:
            continue
        timeline_name = str(_safe(timeline.GetName, "") or "")
        timeline_id = str(_safe(timeline.GetUniqueId, timeline_name) or "")
        for kind in ("video", "audio"):
            rows = _safe(
                lambda kind=kind, timeline=timeline:
                timeline.GetTrackCount(kind), 0) or 0
            for track_index in range(1, int(rows) + 1):
                items = _safe(
                    lambda kind=kind, track_index=track_index,
                    timeline=timeline:
                    timeline.GetItemListInTrack(kind, track_index), ()) or ()
                for item in items:
                    if item_pool_uid(item) != uid:
                        continue
                    item_uid = str(_safe(item.GetUniqueId, "") or "")
                    if exclude_uid and item_uid == str(exclude_uid):
                        continue
                    uses.append(Use(
                        timeline=timeline_name,
                        timeline_id=timeline_id,
                        uid=item_uid,
                        track_type=kind,
                        track_index=track_index,
                        record_in=_int(_safe(item.GetStart, 0)),
                        record_out=_int(_safe(item.GetEnd, 0)),
                        source_file=pool_item_path(item_pool_item(item)),
                    ))
    return tuple(uses)


def find_pool_item(project, source_file: str):
    """The project's pool item for this file, or None.

    The pool is searched bin by bin, because Resolve's API offers no
    "the pool item for this path" lookup.
    """
    wanted = str(source_file or "")
    if not wanted:
        return None
    media_pool = _safe(project.GetMediaPool, None)
    if media_pool is None:
        return None
    root = _safe(media_pool.GetRootFolder, None)
    if root is None:
        return None
    stack = [(root, "")]
    while stack:
        folder, bin_path = stack.pop()
        for clip in _safe(folder.GetClipList, ()) or ():
            if pool_item_path(clip) == wanted:
                return clip
        for sub in _safe(folder.GetSubFolderList, ()) or ():
            name = str(_safe(sub.GetName, "") or "")
            stack.append((sub, f"{bin_path}/{name}" if bin_path else name))
    return None


def import_pool_item(media_pool, source_file: str):
    """Import a file into the pool and return its pool item, or None."""
    imported = _safe(lambda: media_pool.ImportMedia([str(source_file)]),
                     ()) or ()
    for item in imported:
        return item
    return None


# ── The state a re-place must carry ───────────────────────────────────


@dataclass
class CapturedState:
    """Everything a re-placed item must carry, read BEFORE the write.

    The comps are exported to disk by the capture, because a Fusion
    composition that is not on disk cannot be imported onto the new
    item, and a re-placed item comes back with none.
    """

    uid: str
    name: str
    track_type: str
    track_index: int
    record_in: int
    record_out: int
    source_in: int
    source_out: int
    properties: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    comps: tuple = ()
    grade: dict = field(default_factory=dict)
    linked_uids: tuple = ()

    @property
    def duration(self) -> int:
        return int(self.record_out) - int(self.record_in)

    def payload(self, pool_item, *, record_frame: int | None = None) -> dict:
        """The ``AppendToTimeline`` payload that places this item's media.

        Resolve's ``endFrame`` is the EXCLUSIVE end of the source window,
        and ``GetSourceEndFrame`` reads it back the same way - so the
        payload carries the source window the item was measured with, and
        the duration is the difference between the two ends.  (Measured
        live, 2026-10-21: an end one frame past the measured window
        placed an item one frame longer than the item it replaced.)
        """
        from library.tools import resolve_temporal

        duration = int(self.source_out) - int(self.source_in)
        payload = resolve_temporal.append_duration_fields(
            int(self.source_in), duration,
            int(self.record_in if record_frame is None else record_frame),
            int(self.track_index),
            1 if self.track_type == "video" else 2)
        payload["mediaPoolItem"] = pool_item
        return payload


def capture_state(item, *, capture_dir: str) -> CapturedState:
    """Read the state of the item that is about to be replaced.

    Raises `ScopedReplacementError` when the state cannot be read or
    cannot be carried - before anything is written.
    """
    from library.tools import comp_media_window, edit_state_preservation

    pool_item = item_pool_item(item)
    if pool_item is None:
        raise ScopedReplacementError(
            "the item draws from no media pool item, so there is no "
            "source to replace and no state to carry",
            reason="no_pool_item")
    uid = str(_safe(item.GetUniqueId, "") or "")
    properties = _safe(item.GetProperty, None)
    if not isinstance(properties, dict):
        raise ScopedReplacementError(
            "the item's picture properties could not be read",
            reason="unreadable_properties")
    try:
        metadata = edit_state_preservation.capture_clip_metadata(item)
    except Exception as exc:  # noqa: BLE001 - Resolve proxy raises many
        raise ScopedReplacementError(
            f"the item's clip metadata could not be read ({exc})",
            reason="unreadable_metadata") from exc
    track_type, track_index = _safe(item.GetTrackTypeAndIndex,
                                    ("video", 1))
    comps = []
    count = _int(_safe(item.GetFusionCompCount, 0))
    os.makedirs(capture_dir, exist_ok=True)
    for index in range(1, count + 1):
        path = os.path.join(capture_dir, f"{uid or 'item'}_c{index}.comp")
        try:
            item.ExportFusionComp(path, index)
        except Exception as exc:  # noqa: BLE001 - Resolve proxy raises many
            raise ScopedReplacementError(
                f"the item's Fusion composition {index} could not be "
                f"exported ({exc}), so it could not be carried to a "
                "re-placed item", reason="unexportable_comp") from exc
        if not os.path.exists(path):
            raise ScopedReplacementError(
                f"the item's Fusion composition {index} did not export to "
                f"{path!r}, so it could not be carried to a re-placed item",
                reason="unexportable_comp")
        comps.append({"index": index, "path": path,
                      "draws": _comp_draws(item, index),
                      "window": _safe(
                          lambda index=index:
                          comp_media_window.read_window(item, index), None)})
    grade = _capture_grade(item)
    if _int(grade.get("nodes")) > 1:
        raise ScopedReplacementError(
            f"the item carries a {grade['nodes']}-node colour grade, and "
            "Resolve's Graph API has no AddNode: a re-placed item comes "
            "back with one node, so this grade cannot be carried across",
            reason="uncarrable_grade")
    return CapturedState(
        uid=uid,
        name=str(_safe(item.GetName, "") or ""),
        track_type=str(track_type or "video"),
        track_index=_int(track_index, 1),
        record_in=_int(_safe(item.GetStart, 0)),
        record_out=_int(_safe(item.GetEnd, 0)),
        source_in=_int(_safe(item.GetSourceStartFrame, 0)),
        source_out=_int(_safe(item.GetSourceEndFrame, 0)),
        properties={key: properties[key] for key in sorted(properties)},
        metadata=metadata,
        comps=tuple(comps),
        grade=grade,
        linked_uids=tuple(metadata.get("linked_item_ids") or ()),
    )


#: The CDL terms `SetCDL` writes, and the spelling it reads them back in.
_CDL_TERMS: tuple[tuple[str, str], ...] = (
    ("Slope", "slope"), ("Offset", "offset"), ("Power", "power"),
    ("Saturation", "saturation"),
)

#: The tools a PASS-THROUGH composition carries.  Resolve gives every
#: timeline item one, and it draws nothing: the item's media is on the
#: row.  A composition that carries anything else is a TREATMENT, and
#: only a treatment is carried to the re-placed item - the pass-through
#: is Resolve's own, and the re-placed item comes back with one.
PASS_THROUGH_TOOLS: frozenset[str] = frozenset({
    "MediaIn", "MediaOut", "AudioDisplay",
})


def _comp_draws(item, index: int) -> bool:
    """Whether this composition draws, or is Resolve's pass-through.

    An unreadable tool list is treated as DRAWING: an effect that
    cannot be classified is carried, never dropped.
    """
    comp = _safe(lambda: item.GetFusionCompByIndex(index), None)
    if comp is None or isinstance(comp, str):
        return True
    tools = _safe(lambda: comp.GetToolList(False), None)
    if not isinstance(tools, dict):
        return True
    names = set()
    for key, tool in tools.items():
        name = str(_safe(lambda tool=tool: tool.GetAttrs("TOOLS_RegID"), "")
                  or key)
        names.add(name.split("1")[0] if name.endswith("1") else name)
    if not names:
        return True
    return bool(names - PASS_THROUGH_TOOLS)



def _capture_grade(item) -> dict:
    """The item's colour grade, in the terms a re-placed item can carry.

    The Graph API cannot create a node, so only a ONE-node grade can be
    rebuilt on the new item - and only the node's CDL travels, because
    that is what `SetCDL` writes.
    """
    graph = _safe(item.GetNodeGraph, None)
    nodes = _int(_safe(graph.GetNumNodes, 0)) if graph is not None else 0
    cdl = _safe(item.GetCDL, None)
    luts = []
    if graph is not None:
        for node in range(1, nodes + 1):
            path = _safe(lambda node=node: graph.GetLUT(node), None)
            if path:
                luts.append({"node": node, "path": str(path)})
    return {"nodes": nodes, "cdl": dict(cdl) if isinstance(cdl, dict) else {},
            "luts": luts}


def _write_context(write_context: Mapping | None, project_name: str = "",
                   timeline=None) -> dict:
    """The transform write log's context, with somewhere to write to.

    The transform write log REFUSES a write it cannot attribute to a
    project, so a scoped replacement that restores a transform needs a
    project folder, the name of the Resolve project it writes for, and
    the timeline it writes on.  A caller that has them (the EditPatch
    route does) passes them; a caller that does not gets a scratch
    folder of its own, outside the checkout, so the restore is never
    refused for want of a place to log.
    """
    context = dict(write_context or {})
    if not context.get("project") and project_name:
        context["project"] = project_name
    if timeline is not None:
        if not context.get("timeline_name"):
            context["timeline_name"] = str(_safe(timeline.GetName, "") or "")
        if not context.get("timeline_id"):
            context["timeline_id"] = str(
                _safe(timeline.GetUniqueId, "") or "")
    if not context.get("project_folder"):
        context["project_folder"] = tempfile.mkdtemp(
            prefix="ren-scoped-write-")
    return context


def restore_state(item, state: CapturedState, *, timeline=None,
                  write_context: Mapping | None = None,
                  project_name: str = "") -> dict:
    """Carry the captured state onto a re-placed item, and read it back.

    Returns the mismatches: an empty mapping means every carried field
    read back as it was captured.
    """
    from library.tools import comp_media_window, edit_state_preservation

    mismatches: dict[str, Any] = {}
    try:
        properties = edit_state_preservation.write_properties(
            item, state.properties,
            write_context=_write_context(write_context, project_name,
                                         timeline),
            item_identity={"uid": str(_safe(item.GetUniqueId, "") or ""),
                           "name": state.name},
            require_reported=True)
    except Exception as exc:  # noqa: BLE001 - Resolve proxy raises many
        properties = {"error": str(exc)}
    if properties:
        mismatches["picture_properties"] = properties
    try:
        metadata = edit_state_preservation.restore_clip_metadata(
            item, state.metadata, source_in=state.source_in,
            source_out=state.source_out, timeline=timeline)
    except Exception as exc:  # noqa: BLE001 - Resolve proxy raises many
        metadata = {"error": str(exc)}
    if metadata:
        mismatches["clip_metadata"] = metadata
    comps = []
    for comp in state.comps:
        if not comp.get("draws", True):
            # Resolve's own pass-through: the re-placed item comes back
            # with one, and importing a second would draw nothing twice.
            continue
        before = _int(_safe(item.GetFusionCompCount, 0))
        try:
            item.ImportFusionComp(comp["path"])
        except Exception as exc:  # noqa: BLE001 - Resolve proxy raises many
            comps.append({"index": comp["index"], "error": str(exc)})
            continue
        after = _int(_safe(item.GetFusionCompCount, 0))
        index = after if after > before else int(comp["index"])
        entry = {"index": index, "carried_from": comp["index"]}
        window = comp.get("window")
        if window is not None:
            try:
                readback, _wrote = comp_media_window.apply_window(
                    item, index, window)
                entry["window"] = readback
            except Exception as exc:  # noqa: BLE001
                entry["window_error"] = str(exc)
        comps.append(entry)
    if comps:
        mismatches["fusion_comps"] = comps
    grade = _restore_grade(item, state.grade)
    if grade:
        mismatches["grade"] = grade
    return mismatches


def _restore_grade(item, grade: Mapping) -> dict:
    """Put the captured grade back, judged by a re-read."""
    if not grade:
        return {}
    graph = _safe(item.GetNodeGraph, None)
    nodes = _int(_safe(graph.GetNumNodes, 0)) if graph is not None else 0
    wanted_nodes = _int(grade.get("nodes"))
    if wanted_nodes and nodes != wanted_nodes:
        return {"nodes": [wanted_nodes, nodes],
                "reason": "the re-placed item does not carry the same "
                          "number of colour nodes"}
    problems: dict[str, Any] = {}
    cdl = grade.get("cdl") or {}
    if cdl:
        payload: dict[str, str] = {"NodeIndex": "1"}
        for key, _term in _CDL_TERMS:
            value = cdl.get(key)
            if value in (None, ""):
                continue
            payload[key] = str(value)
        if len(payload) > 1:
            wrote = _safe(lambda: item.SetCDL(payload), None)
            if wrote is False:
                problems["cdl"] = "SetCDL answered false"
            else:
                back = _safe(item.GetCDL, None)
                if not isinstance(back, dict) or not back:
                    problems["cdl"] = "the CDL did not read back"
    for entry in grade.get("luts") or ():
        node = _int(entry.get("node"), 1)
        if graph is None:
            continue
        path = entry["path"]
        wrote = _safe(lambda node=node, path=path: graph.SetLUT(node, path),
                      None)
        if wrote is False:
            problems[f"lut_{node}"] = "SetLUT answered false"
    return problems


# ── The replacement ───────────────────────────────────────────────────


def replace_source(*, timeline, project, item, source_file: str,
                   capture_dir: str, media_pool=None,
                   write_context: Mapping | None = None,
                   expected_grade: Mapping | None = None) -> dict:
    """Replace ONE item's source, and nothing else's.

    The receipt says which route ran, which other uses the pool item
    had, and the mapping from the old native id to the new one when the
    scoped route re-placed the item.

    `expected_grade` is the grade the caller asserts the item carries
    after the replacement; the scoped route judges it by a re-read, so a
    replacement that lost the grade is refused rather than committed.
    """
    pool_item = item_pool_item(item)
    if pool_item is None:
        raise ScopedReplacementError(
            "the item draws from no media pool item, so there is no "
            "source to replace", reason="no_pool_item")
    old_uid = str(_safe(item.GetUniqueId, "") or "")
    project_name = str(_safe(project.GetName, "") or "")
    uses = pool_item_uses(project, pool_item, exclude_uid=old_uid)
    if not uses:
        return _replace_pool_item(item=item, pool_item=pool_item,
                                  source_file=source_file)
    return _replace_scoped(timeline=timeline, project=project, item=item,
                           source_file=source_file, capture_dir=capture_dir,
                           media_pool=media_pool, unrelated_uses=uses,
                           old_uid=old_uid, write_context=write_context,
                           expected_grade=expected_grade,
                           project_name=project_name)


def _replace_pool_item(*, item, pool_item, source_file: str) -> dict:
    """The pool item is the target's alone: re-point it in place.

    Resolve keeps the item's Fusion compositions and its grade when the
    pool item is re-pointed, so nothing has to be carried.
    """
    try:
        replaced = pool_item.ReplaceClip(str(source_file))
    except Exception as exc:  # noqa: BLE001 - Resolve proxy raises many
        raise ScopedReplacementError(
            f"the media pool item would not re-point ({exc})",
            reason="replace_failed") from exc
    if not replaced:
        raise ScopedReplacementError(
            "the media pool item answered false to the re-point",
            reason="replace_failed")
    back = pool_item_path(pool_item)
    if back != str(source_file):
        raise ScopedReplacementError(
            f"the media pool item reads {back!r} after the re-point, not "
            f"{source_file!r}", reason="replace_unverified")
    return {
        "route": "pool_item_replace",
        "source_file": str(source_file),
        "unique_id": str(_safe(item.GetUniqueId, "") or ""),
        "id_map": {},
        "unrelated_uses": [],
        "preserved": ["fusion_comps", "grade", "picture_properties",
                      "record_span", "source_span"],
    }


def _replace_scoped(*, timeline, project, item, source_file: str,
                    capture_dir: str, media_pool, unrelated_uses,
                    old_uid: str, write_context: Mapping | None = None,
                    expected_grade: Mapping | None = None,
                    project_name: str = "") -> dict:
    """The pool item is shared: re-place the item, and carry its state.

    The old item is removed and the new media is placed at the same
    record span on the same row, so the other occurrences of the shared
    pool item are never touched.  A failure after the removal puts the
    original back: its pool item was never re-pointed, and the capture
    holds the state it carried.
    """
    media_pool = media_pool or _safe(project.GetMediaPool, None)
    if media_pool is None:
        raise ScopedReplacementError(
            "the project's media pool could not be opened",
            reason="no_media_pool")
    new_pool_item = find_pool_item(project, source_file)
    imported = False
    if new_pool_item is None:
        if not os.path.exists(str(source_file)):
            raise ScopedReplacementError(
                f"the replacement media {source_file!r} is not on disk",
                reason="missing_media")
        new_pool_item = import_pool_item(media_pool, source_file)
        imported = new_pool_item is not None
        if new_pool_item is None:
            raise ScopedReplacementError(
                f"the replacement media {source_file!r} could not be "
                "imported into the project's media pool",
                reason="unimportable_media")
    state = capture_state(item, capture_dir=capture_dir)
    original_pool_item = item_pool_item(item)
    _safe(lambda: timeline.DeleteClips([item], False))
    new_item = _place(timeline, media_pool,
                      state.payload(new_pool_item))
    if new_item is None:
        _restore_original(timeline, media_pool, state, original_pool_item,
                          write_context=write_context,
                          project_name=project_name)
        raise ScopedReplacementError(
            "the replacement media could not be placed at the item's own "
            "record span", reason="placement_failed")
    landed = _verify_placement(new_item, state, source_file)
    if landed:
        _safe(lambda: timeline.DeleteClips([new_item], False))
        _restore_original(timeline, media_pool, state, original_pool_item,
                          write_context=write_context,
                          project_name=project_name)
        raise ScopedReplacementError(
            f"the re-placed item did not read back as the item it "
            f"replaced ({landed})", reason="placement_unverified")
    mismatches = restore_state(new_item, state, timeline=timeline,
                               write_context=write_context,
                               project_name=project_name)
    if expected_grade:
        graded = _check_expected_grade(new_item, expected_grade)
        if graded:
            mismatches = {**mismatches, "expected_grade": graded}
    if mismatches:
        _safe(lambda: timeline.DeleteClips([new_item], False))
        _restore_original(timeline, media_pool, state, original_pool_item,
                          write_context=write_context,
                          project_name=project_name)
        raise ScopedReplacementError(
            f"the re-placed item did not carry the state it had "
            f"({mismatches})", reason="state_not_carried")
    new_uid = str(_safe(new_item.GetUniqueId, "") or "")
    return {
        "route": "scoped_re_place",
        "source_file": str(source_file),
        "unique_id": new_uid,
        "id_map": {old_uid: new_uid} if old_uid and new_uid else {},
        "previous_unique_id": old_uid,
        "imported_media": imported,
        "unrelated_uses": [use.as_dict() for use in unrelated_uses],
        "preserved": ["picture_properties", "record_span", "source_span",
                      "fusion_comps", "grade", "clip_metadata", "audio_link"],
        "comp_dir": capture_dir,
    }


def _check_expected_grade(item, expected: Mapping) -> dict:
    """The grade the caller asserted, against the item as it reads now.

    An empty mapping means the assertion holds.  The CDL is compared
    term by term, because Resolve reports the four terms as one string
    each, and the LUTs are compared as the paths they name.
    """
    problems: dict[str, Any] = {}
    wanted_cdl = expected.get("cdl") or {}
    if wanted_cdl:
        actual = _safe(item.GetCDL, None)
        if not isinstance(actual, dict):
            problems["cdl"] = "the CDL could not be read back"
        else:
            for key, value in wanted_cdl.items():
                got = actual.get(key)
                if str(got) != str(value):
                    problems[f"cdl.{key}"] = [value, got]
    wanted_luts = list(expected.get("luts") or ())
    if wanted_luts:
        graph = _safe(item.GetNodeGraph, None)
        nodes = _int(_safe(graph.GetNumNodes, 0)) if graph is not None else 0
        got_luts = []
        for node in range(1, nodes + 1):
            path = _safe(lambda node=node: graph.GetLUT(node), None)
            if path:
                got_luts.append(str(path))
        if sorted(got_luts) != sorted(str(path) for path in wanted_luts):
            problems["luts"] = [sorted(str(p) for p in wanted_luts),
                                sorted(got_luts)]
    return problems


def _place(timeline, media_pool, payload: Mapping):
    """Place one item, and return the handle that landed."""
    placed = _safe(lambda: media_pool.AppendToTimeline([dict(payload)]),
                   ()) or ()
    for handle in placed:
        return handle
    return None


def _restore_original(timeline, media_pool, state: CapturedState,
                       pool_item, *, write_context: Mapping | None = None,
                       project_name: str = "") -> None:
    """Put the original back after a failed scoped replacement.

    The original's pool item was never re-pointed - that is the whole
    point of the scoped route - so the original media is placed back at
    its own span and its captured state is carried onto it again.
    """
    if pool_item is None:
        return
    handle = _place(timeline, media_pool, state.payload(pool_item))
    if handle is None:
        return
    restore_state(handle, state, timeline=timeline,
                  write_context=write_context,
                  project_name=project_name)


def _verify_placement(new_item, state: CapturedState, source_file: str) -> str:
    """Why the re-placed item is not the item it replaced, or ""."""
    problems = []
    path = pool_item_path(item_pool_item(new_item))
    if path != str(source_file):
        problems.append(f"source reads {path!r}, not {source_file!r}")
    record_in = _int(_safe(new_item.GetStart, 0))
    record_out = _int(_safe(new_item.GetEnd, 0))
    if (record_in, record_out) != (state.record_in, state.record_out):
        problems.append(f"record span reads [{record_in}, {record_out}), "
                        f"not [{state.record_in}, {state.record_out})")
    source_in = _int(_safe(new_item.GetSourceStartFrame, 0))
    source_out = _int(_safe(new_item.GetSourceEndFrame, 0))
    if (source_in, source_out) != (state.source_in, state.source_out):
        problems.append(f"source span reads [{source_in}, {source_out}], "
                        f"not [{state.source_in}, {state.source_out}]")
    return "; ".join(problems)


# ── The move ──────────────────────────────────────────────────────────


def move_item(*, timeline, item, to_frame: int, capture_dir: str,
              media_pool, write_context: Mapping | None = None,
              project_name: str = "", project=None) -> dict:
    """Move ONE item to another record position on its own row.

    Resolve has no move, so a move is a re-place: the item is captured,
    removed, and placed again at the new record frame with its
    transform, its timing, its audio link, its Fusion compositions and
    its grade carried across.  The receipt carries the id mapping,
    because the moved item is a new object.
    """
    state = capture_state(item, capture_dir=capture_dir)
    pool_item = item_pool_item(item)
    if pool_item is None:
        raise ScopedReplacementError(
            "the item draws from no media pool item, so it cannot be "
            "re-placed", reason="no_pool_item")
    if not project_name and project is not None:
        project_name = str(_safe(project.GetName, "") or "")
    if not project_name:
        owner = _safe(timeline.GetProject, None)
        if owner is not None:
            project_name = str(_safe(owner.GetName, "") or "")
    target = int(to_frame)
    _safe(lambda: timeline.DeleteClips([item], False))
    new_item = _place(timeline, media_pool,
                      state.payload(pool_item, record_frame=target))
    if new_item is None:
        _restore_original(timeline, media_pool, state, pool_item,
                          write_context=write_context)
        raise ScopedReplacementError(
            f"the item could not be placed at record frame {target}",
            reason="placement_failed")
    record_in = _int(_safe(new_item.GetStart, 0))
    if record_in != target:
        _safe(lambda: timeline.DeleteClips([new_item], False))
        _restore_original(timeline, media_pool, state, pool_item,
                          write_context=write_context)
        raise ScopedReplacementError(
            f"the moved item reads record frame {record_in}, not {target}",
            reason="placement_unverified")
    mismatches = restore_state(new_item, state, timeline=timeline,
                               write_context=write_context,
                               project_name=project_name)
    if mismatches:
        _safe(lambda: timeline.DeleteClips([new_item], False))
        _restore_original(timeline, media_pool, state, pool_item,
                          write_context=write_context,
                          project_name=project_name)
        raise ScopedReplacementError(
            f"the moved item did not carry the state it had ({mismatches})",
            reason="state_not_carried")
    new_uid = str(_safe(new_item.GetUniqueId, "") or "")
    return {
        "route": "scoped_re_place",
        "operation": "clip.move",
        "unique_id": new_uid,
        "previous_unique_id": state.uid,
        "id_map": {state.uid: new_uid} if state.uid and new_uid else {},
        "record_in": record_in,
        "record_out": _int(_safe(new_item.GetEnd, 0)),
        "duration": state.duration,
        "preserved": ["picture_properties", "record_span", "source_span",
                      "fusion_comps", "grade", "clip_metadata", "audio_link"],
        "comp_dir": capture_dir,
    }


def editorial_selector(item, *, track_type: str = "video") -> dict:
    """The selector that finds this item again after a re-place.

    The native id does not survive a re-place, so the selector is the
    EDITORIAL identity: the material, the source window and the row.
    """
    pool_item = item_pool_item(item)
    return {
        "source_file": pool_item_path(pool_item),
        "source_in_frame": _int(_safe(item.GetSourceStartFrame, 0)),
        "source_out_frame": _int(_safe(item.GetSourceEndFrame, 0)),
        "track_type": track_type,
        "resolve_item_id": str(_safe(item.GetUniqueId, "") or ""),
    }


__all__ = [
    "ScopedReplacementError",
    "Use",
    "CapturedState",
    "capture_state",
    "restore_state",
    "pool_item_uses",
    "find_pool_item",
    "import_pool_item",
    "replace_source",
    "move_item",
    "editorial_selector",
]
