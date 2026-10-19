#!/usr/bin/env python3
"""VC-02 live lifecycle profile: ten workflows on one disposable reel.

Each workflow is ONE locked borrowed session in Ren Qualification,
borrowed from Podcast (field test) and restored afterwards. Only
timelines named ``VC-02 disposable*`` are ever written; any other
project or timeline refuses before the first mutation. The qualification
project is never reset.

Usage:
  bin/vep scripts/vc02_live_profile.py --ledger <ledger.jsonl> [--only ID ...]

The perf ledger is the source of truth. Aggregate each workflow with
``scripts/profile_reel_lifecycle.py --ledger <ledger> --run <run-id>
--workflow <id>`` and record the table in
``docs/benchmarks/2026-10-19-vc-02-reel-lifecycle.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from library.tools import perf_ledger, resolve_transform
from library.tools import qualification_project as qp
from library.tools.marker_feedback import connect_resolve
from library.tools.resolve_deadline import call_with_deadline
from library.tools.resolve_lock import (
    assert_current_timeline,
    cursor_fence,
    resolve_lease,
)

QUALIFICATION_PROJECT = "Ren Qualification"
DISPOSABLE = "VC-02 disposable"
DISPOSABLE_B = "VC-02 disposable B"
WRITABLE = (DISPOSABLE, DISPOSABLE_B)
SOURCE_CLIPS = ("qual_a", "qual_b", "qual_c")

WORKFLOWS = (
    "no_op",
    "single_property_40px",
    "lower_third_replacement",
    "caption_text_timing_fix",
    "clip_insert_replace",
    "ripple_edit",
    "full_rebuild",
    "undo_redo_recovery",
    "rebuild_with_human_edits",
    "multi_reel_one_changed",
)

TITLE_KEYS = ("Styled Text", "StyledText", "Text", "Rich Text")


def call(label, func, *args, **kwargs):
    """One counted Resolve call: timeout, span and API count included."""
    return call_with_deadline(label, func, *args, timeout_s=30.0, **kwargs)


def guard(project, timeline) -> None:
    """Refuse any write target outside the disposable reel."""
    project_name = call("read project name", project.GetName)
    timeline_name = call("read timeline name", timeline.GetName)
    if project_name != QUALIFICATION_PROJECT:
        raise RuntimeError(
            f"refusing: project is {project_name!r}, not {QUALIFICATION_PROJECT!r}"
        )
    if timeline_name not in WRITABLE:
        raise RuntimeError(
            f"refusing: timeline {timeline_name!r} is not a VC-02 disposable"
        )


def find_timeline(project, name: str):
    count = call("timeline count", project.GetTimelineCount) or 0
    for index in range(1, int(count) + 1):
        candidate = call("timeline by index", project.GetTimelineByIndex, index)
        if candidate is not None and call("timeline name", candidate.GetName) == name:
            return candidate
    return None


def timeline_names(project) -> list:
    count = call("timeline count", project.GetTimelineCount) or 0
    names = []
    for index in range(1, int(count) + 1):
        candidate = call("timeline by index", project.GetTimelineByIndex, index)
        if candidate is not None:
            names.append(call("timeline name", candidate.GetName))
    return names


def _pool_identity(item) -> tuple | None:
    """(name, file path) for a file-backed pool clip, else None."""
    try:
        name = call("pool item name", item.GetName)
        file_path = call("pool file path", item.GetClipProperty, "File Path")
    except Exception:  # noqa: BLE001 - timelines also list in the pool
        return None
    if not file_path:
        return None
    return (name, file_path)


def pool_clip_by_name(pool, name: str):
    """A file-backed pool clip by exact name, searched recursively."""
    stack = [call("pool root", pool.GetRootFolder)]
    while stack:
        folder = stack.pop()
        for item in call("pool clips", folder.GetClipList) or ():
            identity = _pool_identity(item)
            if identity is not None and identity[0] == name:
                return item
        stack.extend(call("pool subfolders", folder.GetSubFolderList) or ())
    return None


def media_frames(pool_item) -> int:
    """The source media's frame count, read off the pool clip."""
    raw = call("pool frames", pool_item.GetClipProperty, "Frames")
    return int(float(str(raw)))


def insert_op(uid: str, row: dict, source_uid: str, media_len: int) -> dict:
    """A clip.insert op whose source span never exceeds the media."""
    want = row["record_end"] - row["record_in"]
    placed = min(want, media_len)
    if placed <= 0:
        raise RuntimeError(f"nothing placeable for {row}")
    return {
        "op": "clip.insert",
        "unique_id": uid,
        "track_type": "video",
        "track_index": 1,
        "record_in": row["record_in"],
        "record_out": row["record_in"] + placed,
        "source_in": 0,
        "source_out": placed,
        "media_pool_item": source_uid,
    }


def plan_sources(pool, rows: list) -> dict:
    """Pool identity plus media length for every row, by row uid."""
    sources = {}
    for row in rows:
        clip = pool_clip_by_name(pool, row["name"])
        if clip is None:
            raise RuntimeError(f"pool clip {row['name']} is missing")
        sources[row["uid"]] = (
            str(call("pool uid", clip.GetUniqueId)),
            media_frames(clip),
        )
    return sources


def inventory(timeline) -> dict:
    """The disposable's machine state: V1 items, markers, extents."""
    items = call("v1 items", timeline.GetItemListInTrack, "video", 1) or ()
    rows = []
    for item in items:
        rows.append(
            {
                "uid": str(call("item uid", item.GetUniqueId)),
                "name": str(call("item name", item.GetName)),
                "record_in": int(call("item start", item.GetStart)),
                "record_end": int(call("item end", item.GetEnd)),
                "duration": int(call("item duration", item.GetDuration)),
            }
        )
    markers = call("markers", timeline.GetMarkers) or {}
    return {
        "v1": rows,
        "markers": {
            str(frame): {"color": data.get("color"), "name": data.get("name")}
            for frame, data in markers.items()
        },
        "start": int(call("timeline start", timeline.GetStartFrame)),
        "end": int(call("timeline end", timeline.GetEndFrame)),
    }


def apply_patch(
    *,
    project,
    timeline,
    store,
    journal_dir,
    operations,
    conflict_domains,
    preconditions=(),
    postconditions=(),
    capability="reel.touchup",
    label="vc02",
) -> dict:
    """One verified EditPatch; a non-committed receipt raises."""
    from library.tools import edit_patch

    call("set current timeline", project.SetCurrentTimeline, timeline)
    with cursor_fence(project, timeline, f"vc02 {label}"):
        receipt = edit_patch.apply_live_patch(
            project=project,
            timeline=timeline,
            capability=capability,
            operations=operations,
            conflict_domains=conflict_domains,
            idempotency_key=f"vc02-{uuid.uuid4().hex}",
            preconditions=preconditions,
            postconditions=postconditions,
            store=store,
            project_folder=str(journal_dir),
        )
    if receipt.get("status") != "committed":
        raise RuntimeError(
            f"{label}: patch did not commit: {json.dumps(receipt, default=str)[:800]}"
        )
    return receipt


def move_pan(item, pool_item, timeline, project, dx_px: float) -> tuple:
    """Compile a horizontal output-pixel move, by AXI's own math."""
    before = call("item properties", item.GetProperty) or {}
    pan = float(before.get("Pan", 0.0))
    tilt = float(before.get("Tilt", 0.0))
    scaling = before.get("Scaling", 0)
    raw_resolution = str(
        call("pool resolution", pool_item.GetClipProperty, "Resolution") or ""
    )
    if "x" not in raw_resolution.lower():
        raise RuntimeError(f"pool clip has no measured Resolution: {raw_resolution!r}")
    clip_w = int(raw_resolution.lower().split("x", 1)[0].strip())
    clip_h = int(raw_resolution.lower().split("x", 1)[1].strip())
    frame_w = int(
        call("timeline width", timeline.GetSetting, "timelineResolutionWidth")
    )
    frame_h = int(
        call("timeline height", timeline.GetSetting, "timelineResolutionHeight")
    )
    if scaling in (1, "1", True):
        base_scale = resolve_transform.NATIVE_BASE_SCALE
    else:
        behavior = call(
            "mismatch behavior", project.GetSetting, "timelineInputResMismatchBehavior"
        )
        if behavior != "scaleToFit":
            raise RuntimeError(f"unqualified project mismatch behavior {behavior!r}")
        base_scale = resolve_transform.fit_base_scale(clip_w, clip_h, frame_w, frame_h)
    rejection = resolve_transform.native_conversion_refusal(before, "scaleToFit")
    if rejection:
        raise RuntimeError(f"unqualified transform: {rejection}")
    gain = resolve_transform.FALLBACK_DRAW_GAIN
    new_pan = pan + (dx_px * frame_w / (clip_w * base_scale * gain))
    return before, new_pan, tilt


# ── sessions ──────────────────────────────────────────────────────


class Session:
    """One borrowed Resolve session with ledger phases available."""

    def __init__(
        self, ledger: Path, run_id: str, capability: str, journal_dir: Path, store
    ) -> None:
        self.ledger = ledger
        self.run_id = run_id
        self.capability = capability
        self.journal_dir = journal_dir
        self.store = store
        self.resolve = None
        self.manager = None
        self.project = None
        self.borrowed_from: dict = {}

    def __enter__(self):
        self.cap = perf_ledger.OpenCapability(
            "/tmp", self.capability, self.run_id, ledger_path_override=self.ledger
        )
        try:
            self.lease = resolve_lease(f"vc02 {self.run_id}", exclusive=True)
            self.lease.__enter__()
        except Exception:
            self.cap.end()
            raise
        try:
            self.resolve = connect_resolve()
            borrower = qp.borrowed(self.resolve)
            self.borrowed_from = borrower.__enter__()
            self._borrower = borrower
        except Exception:
            self.lease.__exit__(*sys.exc_info())
            self.cap.end()
            raise
        self.manager = call("project manager", self.resolve.GetProjectManager)
        if self.manager is None:
            raise RuntimeError("GetProjectManager() returned None")
        qp.switch_project(self.manager, QUALIFICATION_PROJECT)
        return self

    def __exit__(self, *exc):
        try:
            if getattr(self, "_borrower", None) is not None:
                self._borrower.__exit__(*exc)
        finally:
            try:
                self.lease.__exit__(*exc)
            finally:
                self.cap.end()
        return False

    def timeline(self, name: str):
        timeline = find_timeline(self.project_current(), name)
        if timeline is None:
            raise RuntimeError(f"no timeline {name!r} in Qualification")
        return timeline

    def project_current(self):
        return call("current project", self.manager.GetCurrentProject)


def base_inventory(session: Session, name: str) -> dict:
    timeline = session.timeline(name)
    guard(session.project_current(), timeline)
    return inventory(timeline)


# ── workflows ─────────────────────────────────────────────────────


def wf_setup(session: Session) -> dict:
    """Create the disposable from the qualification recipe."""
    with perf_ledger.lifecycle_phase("planning"):
        spine = session.timeline("Q Spine")
        inventory(spine)
        pool = call("media pool", session.project_current().GetMediaPool)
        sources = {}
        for name in SOURCE_CLIPS:
            item = pool_clip_by_name(pool, f"{name}.mp4")
            if item is None:
                raise RuntimeError(f"pool clip {name}.mp4 is missing")
            sources[name] = item
    with perf_ledger.lifecycle_phase("staging"):
        project = session.project_current()
        if find_timeline(project, DISPOSABLE) is not None:
            raise RuntimeError(
                f"{DISPOSABLE!r} already exists; refusing "
                f"to double it (delete by hand first)"
            )
        created = call(
            "create timeline", project.GetMediaPool().CreateEmptyTimeline, DISPOSABLE
        )
        if created is None:
            raise RuntimeError("CreateEmptyTimeline returned nothing")
        guard(project, created)
        call("set current timeline", project.SetCurrentTimeline, created)
        with cursor_fence(project, created, "vc02 setup place"):
            placed = call(
                "append recipe",
                project.GetMediaPool().AppendToTimeline,
                [{"mediaPoolItem": sources[name]} for name in SOURCE_CLIPS],
            )
        if not placed:
            raise RuntimeError("AppendToTimeline placed nothing")
        assert_current_timeline(project, created)
    with perf_ledger.lifecycle_phase("verification"):
        got = inventory(created)
        want = [name for name in ("qual_a.mp4", "qual_b.mp4", "qual_c.mp4")]
        names = [row["name"] for row in got["v1"]]
        if names != want or len(got["v1"]) != 3:
            raise RuntimeError(f"disposable holds {names}, want {want}")
        if got["end"] - got["start"] != 216:
            raise RuntimeError(
                f"disposable is {got['end'] - got['start']} frames, want 216"
            )
    return {"outcome": "created", "inventory": got}


def wf_no_op(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        first = base_inventory(session, DISPOSABLE)
    with perf_ledger.lifecycle_phase("verification"):
        second = base_inventory(session, DISPOSABLE)
    if first != second:
        raise RuntimeError("disposable moved between two reads")
    return {"outcome": "already satisfied, no mutation", "inventory": second}


def wf_single_property(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        before = inventory(timeline)
        item = _item_by_index(timeline, 0)
        pool_item = call("item pool", item.GetMediaPoolItem)
        props, new_pan, _tilt = move_pan(item, pool_item, timeline, project, 40.0)
    with perf_ledger.lifecycle_phase("resolve_ops"):
        receipt = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="single-property",
            operations=[
                {
                    "op": "clip.set_property",
                    "unique_id": before["v1"][0]["uid"],
                    "key": "Pan",
                    "value": new_pan,
                }
            ],
            conflict_domains=("picture_transform",),
            preconditions=[
                {
                    "kind": "clip_transform_equals",
                    "unique_id": before["v1"][0]["uid"],
                    "properties": {
                        key: props[key] for key in ("Pan", "Tilt") if key in props
                    },
                }
            ],
        )
    with perf_ledger.lifecycle_phase("verification"):
        after = inventory(timeline)
        back = call("pan read-back", item.GetProperty)
        if abs(float(back.get("Pan", 0.0)) - new_pan) > 1e-6:
            raise RuntimeError("Pan did not read back")
        if [row["record_in"] for row in after["v1"]] != [
            row["record_in"] for row in before["v1"]
        ]:
            raise RuntimeError("a property edit moved a clip")
    return {
        "outcome": "moved 40px, verified",
        "pan": new_pan,
        "patch": receipt.get("patch_id"),
        "inventory": after,
    }


def wf_lower_third(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        base = inventory(timeline)
        from library.tools import edit_patch as _ep

        qualified = [name for name in _ep.OPERATIONS if "title" in name]
        pool = call("media pool", project.GetMediaPool)
        title_sources = []
        stack = [call("pool root", pool.GetRootFolder)]
        while stack:
            folder = stack.pop()
            for item in call("pool clips", folder.GetClipList) or ():
                identity = _pool_identity(item)
                if identity is None:
                    continue
                lowered = identity[0].lower()
                if "title" in lowered or "lower" in lowered:
                    title_sources.append(identity[0])
            stack.extend(call("pool subfolders", folder.GetSubFolderList) or ())
    if qualified or title_sources:
        return {
            "outcome": "unexpected: a title route exists",
            "qualified_ops": qualified,
            "title_sources": title_sources,
            "v1_items": len(base["v1"]),
        }
    return {
        "outcome": "refused: no title op, verb, source, or item",
        "qualified_title_ops": qualified,
        "title_sources_in_pool": title_sources,
        "title_items_on_reel": [],
        "v1_items": len(base["v1"]),
        "unblock": "a fixture reel carrying a title item plus a "
        "qualified title-text op",
    }


def wf_caption_fix(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        base = inventory(timeline)
        try:
            subtitle_tracks = int(
                call("subtitle track count", timeline.GetTrackCount, "subtitle")
            )
        except Exception as exc:  # noqa: BLE001 - no subtitle addressing
            subtitle_tracks = f"unsupported: {exc}"
        from library.tools import edit_patch as _ep

        qualified = [
            name for name in _ep.OPERATIONS if "caption" in name or "subtitle" in name
        ]
    if isinstance(subtitle_tracks, int) and subtitle_tracks > 0:
        return {
            "outcome": "unexpected: subtitle tracks present",
            "subtitle_tracks": subtitle_tracks,
            "v1_items": len(base["v1"]),
        }
    return {
        "outcome": "refused: no subtitle track and no caption op",
        "subtitle_tracks": subtitle_tracks,
        "qualified_caption_ops": qualified,
        "v1_items": len(base["v1"]),
        "unblock": "a fixture reel carrying captions plus a qualified caption-text op",
    }


def wf_insert_replace(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        before = inventory(timeline)
        pool = call("media pool", project.GetMediaPool)
        source = pool_clip_by_name(pool, "qual_c.mp4")
        if source is None:
            raise RuntimeError("pool clip qual_c.mp4 is missing")
        source_uid = str(call("pool uid", source.GetUniqueId))
        source_len = media_frames(source)
        target = before["v1"][1]
    with perf_ledger.lifecycle_phase("resolve_ops"):
        receipt_insert = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="clip-insert",
            capability="reel.build",
            operations=[
                insert_op(
                    f"vc02-insert-{uuid.uuid4().hex[:8]}",
                    {
                        "record_in": before["end"],
                        "record_end": before["end"] + source_len,
                    },
                    source_uid,
                    source_len,
                )
            ],
            conflict_domains=("timeline_structure",),
        )
        mid = inventory(timeline)
        if len(mid["v1"]) != len(before["v1"]) + 1:
            raise RuntimeError(f"insert did not land: {len(mid['v1'])} items")
        receipt_delete = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="clip-replace-delete",
            operations=[{"op": "clip.delete", "unique_id": target["uid"]}],
            conflict_domains=("timeline_structure",),
            preconditions=[{"kind": "clip_present", "unique_id": target["uid"]}],
        )
        receipt_replace = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="clip-replace-insert",
            capability="reel.build",
            operations=[
                insert_op(
                    f"vc02-replace-{uuid.uuid4().hex[:8]}",
                    target,
                    source_uid,
                    source_len,
                ),
            ],
            conflict_domains=("timeline_structure",),
        )
    with perf_ledger.lifecycle_phase("verification"):
        after = inventory(timeline)
        # The 72-frame passage is replaced by the 48-frame source at the
        # same record head: clamping keeps the request satisfiable. The
        # end-append from the insert half survives the replace half.
        want = [("qual_a.mp4", 0, 96), ("qual_c.mp4", 96, 96 + source_len)]
        want += [
            ("qual_c.mp4", row["record_in"], row["record_end"])
            for row in before["v1"][2:]
        ]
        want.append(("qual_c.mp4", before["end"], before["end"] + source_len))
        got = [
            (row["name"], row["record_in"], row["record_end"]) for row in after["v1"]
        ]
        if got != want:
            raise RuntimeError(f"unexpected V1 after insert+replace: {got}")
        if after["end"] - after["start"] != before["end"] + source_len:
            raise RuntimeError("duration is not base + insert after the insert")
    return {
        "outcome": "inserted + replaced, verified",
        "insert_patch": receipt_insert.get("patch_id"),
        "replace_delete_patch": receipt_delete.get("patch_id"),
        "replace_insert_patch": receipt_replace.get("patch_id"),
        "inventory": after,
    }


def wf_ripple(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        before = inventory(timeline)
        pool = call("media pool", project.GetMediaPool)
        sources = plan_sources(pool, before["v1"])
        delta = 48
        shifted = []
        for row in before["v1"][1:]:
            pool_uid, media_len = sources[row["uid"]]
            want = row["record_end"] - row["record_in"]
            placed = min(want, media_len)
            shifted.append(
                (
                    row,
                    pool_uid,
                    row["record_in"] + delta,
                    row["record_in"] + delta + placed,
                )
            )
    with perf_ledger.lifecycle_phase("resolve_ops"):
        receipt_delete = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="ripple-delete",
            operations=[
                {"op": "clip.delete", "unique_id": row["uid"]}
                for row in before["v1"][1:]
            ],
            conflict_domains=("timeline_structure",),
            preconditions=[
                {"kind": "clip_present", "unique_id": row["uid"]}
                for row in before["v1"][1:]
            ],
        )
        receipt_insert = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="ripple-insert",
            capability="reel.build",
            operations=[
                insert_op(
                    f"vc02-ripple-{row['uid'][:8]}",
                    {"record_in": new_in, "record_end": new_end},
                    pool_uid,
                    new_end - new_in,
                )
                for row, pool_uid, new_in, new_end in shifted
            ],
            conflict_domains=("timeline_structure",),
        )
    with perf_ledger.lifecycle_phase("verification"):
        after = inventory(timeline)
        want = [
            (
                before["v1"][0]["name"],
                before["v1"][0]["record_in"],
                before["v1"][0]["record_end"],
            )
        ] + [
            (row["name"], new_in, new_end)
            for row, _pool_uid, new_in, new_end in shifted
        ]
        got = [
            (row["name"], row["record_in"], row["record_end"]) for row in after["v1"]
        ]
        if got != want:
            raise RuntimeError(f"ripple landed {got}, want {want}")
    return {
        "outcome": f"rippled +{delta}f downstream, verified",
        "delete_patch": receipt_delete.get("patch_id"),
        "insert_patch": receipt_insert.get("patch_id"),
        "inventory": after,
    }


def wf_full_rebuild(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        before = inventory(timeline)
        pool = call("media pool", project.GetMediaPool)
        sources = plan_sources(pool, before["v1"])
        want = [
            (
                row["name"],
                row["record_in"],
                row["record_in"]
                + min(row["record_end"] - row["record_in"], sources[row["uid"]][1]),
            )
            for row in before["v1"]
        ]
    with perf_ledger.lifecycle_phase("resolve_ops"):
        receipt_delete = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="full-rebuild-delete",
            operations=[
                {"op": "clip.delete", "unique_id": row["uid"]} for row in before["v1"]
            ],
            conflict_domains=("timeline_structure",),
            preconditions=[
                {"kind": "clip_present", "unique_id": row["uid"]}
                for row in before["v1"]
            ],
        )
        receipt_insert = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="full-rebuild-insert",
            capability="reel.build",
            operations=[
                insert_op(
                    f"vc02-rebuild-{uuid.uuid4().hex[:8]}",
                    row,
                    sources[row["uid"]][0],
                    sources[row["uid"]][1],
                )
                for row in before["v1"]
            ],
            conflict_domains=("timeline_structure",),
        )
    with perf_ledger.lifecycle_phase("verification"):
        after = inventory(timeline)
        if [
            (row["name"], row["record_in"], row["record_end"]) for row in after["v1"]
        ] != want:
            raise RuntimeError("rebuild does not match its base")
    return {
        "outcome": "rebuilt, matches base",
        "delete_patch": receipt_delete.get("patch_id"),
        "insert_patch": receipt_insert.get("patch_id"),
        "inventory": after,
    }


def wf_undo_redo(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        before = inventory(timeline)
        item = _item_by_index(timeline, 0)
        pool_item = call("item pool", item.GetMediaPoolItem)
        props, moved, _tilt = move_pan(item, pool_item, timeline, project, 40.0)
        uid = before["v1"][0]["uid"]
    with perf_ledger.lifecycle_phase("resolve_ops"):
        receipt_do = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="undo-redo-do",
            operations=[
                {
                    "op": "clip.set_property",
                    "unique_id": uid,
                    "key": "Pan",
                    "value": moved,
                }
            ],
            conflict_domains=("picture_transform",),
            preconditions=[
                {
                    "kind": "clip_transform_equals",
                    "unique_id": uid,
                    "properties": {
                        key: props[key] for key in ("Pan", "Tilt") if key in props
                    },
                }
            ],
        )
        mid_props = call("mid properties", item.GetProperty) or {}
        receipt_undo = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="undo-redo-undo",
            operations=[
                {
                    "op": "clip.set_property",
                    "unique_id": uid,
                    "key": "Pan",
                    "value": float(props.get("Pan", 0.0)),
                }
            ],
            conflict_domains=("picture_transform",),
            preconditions=[
                {
                    "kind": "clip_transform_equals",
                    "unique_id": uid,
                    "properties": {
                        key: mid_props[key]
                        for key in ("Pan", "Tilt")
                        if key in mid_props
                    },
                }
            ],
        )
    with perf_ledger.lifecycle_phase("verification"):
        after = inventory(timeline)
        if after != before:
            raise RuntimeError("undo did not restore the base inventory")
        back = call("pan read-back", item.GetProperty)
        if abs(float(back.get("Pan", 0.0)) - float(props.get("Pan", 0.0))) > 1e-6:
            raise RuntimeError("Pan did not undo to its base value")
    return {
        "outcome": "edit + inverse undo, base restored",
        "do_patch": receipt_do.get("patch_id"),
        "undo_patch": receipt_undo.get("patch_id"),
        "inventory": after,
    }


def wf_human_edits(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        before = inventory(timeline)
        pool = call("media pool", project.GetMediaPool)
        sources = plan_sources(pool, before["v1"])
        want = [
            (
                row["name"],
                row["record_in"],
                row["record_in"]
                + min(row["record_end"] - row["record_in"], sources[row["uid"]][1]),
            )
            for row in before["v1"]
        ]
    with perf_ledger.lifecycle_phase("resolve_ops"):
        receipt_note = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="human-note",
            operations=[
                {
                    "op": "marker.add",
                    "frame": 48,
                    "color": "Red",
                    "name": "human-note",
                    "note": "captain's note: keep this",
                }
            ],
            conflict_domains=("markers",),
            preconditions=[{"kind": "marker_absent", "frame": 48}],
        )
        receipt_delete = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="rebuild-keep-human-delete",
            operations=[
                {"op": "clip.delete", "unique_id": row["uid"]} for row in before["v1"]
            ],
            conflict_domains=("timeline_structure",),
            preconditions=[
                {"kind": "clip_present", "unique_id": row["uid"]}
                for row in before["v1"]
            ],
        )
        receipt_insert = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="rebuild-keep-human-insert",
            capability="reel.build",
            operations=[
                insert_op(
                    f"vc02-keep-{uuid.uuid4().hex[:8]}",
                    row,
                    sources[row["uid"]][0],
                    sources[row["uid"]][1],
                )
                for row in before["v1"]
            ],
            conflict_domains=("timeline_structure",),
        )
    with perf_ledger.lifecycle_phase("verification"):
        after = inventory(timeline)
        if [
            (row["name"], row["record_in"], row["record_end"]) for row in after["v1"]
        ] != want:
            raise RuntimeError("rebuild changed the picture")
        note = after["markers"].get("48")
        if note is None or note.get("name") != "human-note":
            raise RuntimeError(f"human note did not survive: {after['markers']}")
    return {
        "outcome": "rebuild kept the human note, verified",
        "note_patch": receipt_note.get("patch_id"),
        "delete_patch": receipt_delete.get("patch_id"),
        "insert_patch": receipt_insert.get("patch_id"),
        "inventory": after,
    }


def wf_multi_reel(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        timeline = session.timeline(DISPOSABLE)
        guard(project, timeline)
        base_a = inventory(timeline)
    with perf_ledger.lifecycle_phase("staging"):
        call("set current timeline", project.SetCurrentTimeline, timeline)
        stale = find_timeline(project, DISPOSABLE_B)
        if stale is not None:
            pool = call("media pool", project.GetMediaPool)
            call("delete stale B", pool.DeleteTimelines, [stale])
            if DISPOSABLE_B in timeline_names(project):
                raise RuntimeError(f"{DISPOSABLE_B} survived deletion")
        created = call("duplicate reel", timeline.DuplicateTimeline, DISPOSABLE_B)
        # Duplicating moves the cursor onto the new timeline; put it
        # back before the fence reads it back.
        call("restore current timeline", project.SetCurrentTimeline, timeline)
        with cursor_fence(project, timeline, "vc02 multi-reel duplicate"):
            pass
        if DISPOSABLE_B not in timeline_names(project):
            raise RuntimeError(
                f"{DISPOSABLE_B} is not listed after duplicate (answered {created!r})"
            )
        reel_b = session.timeline(DISPOSABLE_B)
        base_b = inventory(reel_b)
    with perf_ledger.lifecycle_phase("resolve_ops"):
        item = _item_by_index(timeline, 0)
        pool_item = call("item pool", item.GetMediaPoolItem)
        props, moved, _tilt = move_pan(item, pool_item, timeline, project, 40.0)
        receipt = apply_patch(
            project=project,
            timeline=timeline,
            store=session.store,
            journal_dir=session.journal_dir,
            label="multi-reel-change",
            operations=[
                {
                    "op": "clip.set_property",
                    "unique_id": base_a["v1"][0]["uid"],
                    "key": "Pan",
                    "value": moved,
                }
            ],
            conflict_domains=("picture_transform",),
            preconditions=[
                {
                    "kind": "clip_transform_equals",
                    "unique_id": base_a["v1"][0]["uid"],
                    "properties": {
                        key: props[key] for key in ("Pan", "Tilt") if key in props
                    },
                }
            ],
        )
    with perf_ledger.lifecycle_phase("verification"):
        after_a = inventory(timeline)
        after_b = inventory(reel_b)
        if after_b != base_b:
            raise RuntimeError(f"the unchanged reel moved: {after_b}")
        if _strip_uids(after_a) != _strip_uids(base_a):
            raise RuntimeError(f"the change moved a clip: {after_a}")
        changed_item = _item_by_index(timeline, 0)
        changed_props = call("changed props", changed_item.GetProperty) or {}
        if abs(float(changed_props.get("Pan", 0.0)) - moved) > 1e-6:
            raise RuntimeError("Pan did not read back on the changed reel")
    return {
        "outcome": "one reel changed, other verified untouched",
        "patch": receipt.get("patch_id"),
        "changed": after_a,
        "untouched": after_b,
    }


def wf_cleanup(session: Session) -> dict:
    with perf_ledger.lifecycle_phase("planning"):
        project = session.project_current()
        names = timeline_names(project)
        targets = [name for name in (DISPOSABLE, DISPOSABLE_B) if name in names]
        if not targets:
            raise RuntimeError("no VC-02 disposable to clean up")
    with perf_ledger.lifecycle_phase("archive_export"):
        export_flag = getattr(session.resolve, "EXPORT_DRT", None)
        archives = {}
        if export_flag is None:
            archives["flag"] = "Resolve does not expose EXPORT_DRT"
        else:
            out_dir = session.journal_dir / "drt"
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in targets:
                timeline = session.timeline(name)
                dest = out_dir / f"{name.replace(' ', '_')}.drt"
                call("drt export", timeline.Export, str(dest), export_flag)
                if not dest.is_file() or dest.stat().st_size == 0:
                    raise RuntimeError(f"DRT export of {name} left nothing")
                archives[name] = dest.stat().st_size
    with perf_ledger.lifecycle_phase("cleanup"):
        pool = call("media pool", project.GetMediaPool)
        doomed = [session.timeline(name) for name in targets]
        deleted = call("delete timelines", pool.DeleteTimelines, doomed)
        if deleted is not True:
            raise RuntimeError(f"DeleteTimelines answered {deleted!r}")
        remaining = timeline_names(project)
        if any(name in remaining for name in targets):
            raise RuntimeError("a disposable survived deletion")
        problems = qp.verify(project)
    with perf_ledger.lifecycle_phase("verification"):
        if problems:
            raise RuntimeError(
                "Qualification no longer matches its "
                "declaration: " + "; ".join(problems)
            )
    return {
        "outcome": "cleaned",
        "archives_bytes": archives,
        "removed": targets,
        "qualification_problems": problems,
    }


FUNCS = {
    "setup": wf_setup,
    "no_op": wf_no_op,
    "single_property_40px": wf_single_property,
    "lower_third_replacement": wf_lower_third,
    "caption_text_timing_fix": wf_caption_fix,
    "clip_insert_replace": wf_insert_replace,
    "ripple_edit": wf_ripple,
    "full_rebuild": wf_full_rebuild,
    "undo_redo_recovery": wf_undo_redo,
    "rebuild_with_human_edits": wf_human_edits,
    "multi_reel_one_changed": wf_multi_reel,
    "cleanup": wf_cleanup,
}

ORDER = ("setup",) + WORKFLOWS + ("cleanup",)


def _item_by_index(timeline, index: int):
    items = call("v1 items", timeline.GetItemListInTrack, "video", 1) or ()
    if index >= len(items):
        raise RuntimeError(f"V1 has {len(items)} items, need index {index}")
    return items[index]


def _run_one(name: str, ledger: Path, journal_dir: Path, store, stamp: str) -> dict:
    run_id = f"vc02-{name}-{stamp}"
    session = Session(ledger, run_id, f"vc02:{name}", journal_dir, store)
    t0 = time.perf_counter()
    try:
        with session:
            if name == "setup":
                return {
                    "name": name,
                    "run_id": run_id,
                    "result": wf_setup(session),
                    "wall_s": round(time.perf_counter() - t0, 3),
                }
            expected = EXPECTED_BASES.get(name)
            if expected is not None and name in WORKFLOWS:
                actual = inventory(session.timeline(DISPOSABLE))
                if _strip_uids(actual) != _strip_uids(expected):
                    raise RuntimeError(
                        f"base mismatch before {name}: the disposable "
                        f"is not where the previous workflow left it"
                    )
            return {
                "name": name,
                "run_id": run_id,
                "result": FUNCS[name](session),
                "wall_s": round(time.perf_counter() - t0, 3),
            }
    except Exception as exc:  # noqa: BLE001 - one workflow's failure
        # is a row, never a silent stop; the driver decides whether to
        # continue.
        return {
            "name": name,
            "run_id": run_id,
            "error": f"{type(exc).__name__}: {exc}",
            "wall_s": round(time.perf_counter() - t0, 3),
        }


def _strip_uids(inv: dict) -> dict:
    """Compare picture state without Resolve-assigned unique ids."""
    return {
        "v1": [
            {key: row[key] for key in ("name", "record_in", "record_end", "duration")}
            for row in inv.get("v1", ())
        ],
        "markers": inv.get("markers", {}),
        "start": inv.get("start"),
        "end": inv.get("end"),
    }


EXPECTED_BASES: dict = {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="workflow ids to run (default: all in order)",
    )
    parser.add_argument(
        "--manifest", type=Path, default=None, help="write {workflow: run_id} JSON here"
    )
    args = parser.parse_args()
    selected = tuple(name for name in ORDER if args.only is None or name in args.only)
    unknown = [name for name in (args.only or ()) if name not in ORDER]
    if unknown:
        print(f"unknown workflows: {unknown}", file=sys.stderr)
        return 2
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    journal_dir = args.ledger.parent / "vc02-journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    from library.tools.timeline_shadow import ShadowStore

    store = ShadowStore(path=args.ledger.parent / "vc02_shadow.db")
    stamp = time.strftime("%Y%m%dT%H%M%S")
    manifest = {}
    outcomes = []
    for name in selected:
        row = _run_one(name, args.ledger, journal_dir, store, stamp)
        manifest[name] = row["run_id"]
        outcomes.append(row)
        print(json.dumps(row, default=str))
        if "inventory" in row.get("result", {}):
            EXPECTED_BASES[_next_name(name)] = row["result"]["inventory"]
        if row.get("error") and name in WORKFLOWS and _must_stop(name):
            print(f"stopping after {name}: {row['error']}", file=sys.stderr)
            break
    if args.manifest is not None:
        args.manifest.write_text(
            json.dumps(manifest, indent=1) + "\n", encoding="utf-8"
        )
    failed = [row for row in outcomes if row.get("error")]
    return 1 if failed else 0


def _next_name(name: str):
    """The workflow whose base this workflow's end state predicts."""
    order = list(ORDER)
    nxt = order.index(name) + 1
    return order[nxt] if nxt < len(order) else None


def _must_stop(name: str) -> bool:
    """Expected refusals (lower-third, caption) never stop the campaign;
    any other failure does, rather than measuring on unknown state."""
    return name not in ("lower_third_replacement", "caption_text_timing_fix")


if __name__ == "__main__":
    raise SystemExit(main())
