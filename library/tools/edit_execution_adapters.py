"""Semantic request adapters for existing Resolve edit workflows.

These functions only bind operation ids to already-owned physical
executors. Qualification remains in ``edit_algebra.OP_STATUS`` and routing
remains in ``execution_router``.
"""

from __future__ import annotations

from collections.abc import Mapping

def _project_folder(context) -> str:
    from library.tools import project_registry

    project_path = project_registry.resolve_project_path(context.request.project)
    if project_path is None:
        project = project_registry.get_project(context.request.project)
        return str(project.project_root)
    return str(project_path.parent)

# These operations can change the positional address of a later item. Until
# this adapter has a fresh live read-back after each command, a subsequent
# positional write must refuse rather than reuse the prepared index.
_POSITION_INVALIDATING_OPERATIONS = frozenset({
    "clip.delete", "clip.remove", "clip.trim", "clip.move", "clip.split",
    "clip.slide", "clip.roll",
    "clip.ripple", "clip.insert", "broll.insert", "track.add", "overlay.add",
    "overlay.remove",
    # A planning-level opening tighten executes its carried trim/remove
    # edits against live state; a later positional write in the same
    # batch must not reuse the addresses it was prepared against.
    "reel.opening",
})


def _target_position_for_context(context, unique_id: str) -> tuple[str, int]:
    operation_index = int(getattr(context, "operation_index", 0))
    compiled = getattr(context, "compiled", None)
    prior = (compiled.operations[:operation_index] if compiled is not None
             else ())
    invalidators = [str(operation["op"]) for operation in prior
                    if str(operation["op"]) in _POSITION_INVALIDATING_OPERATIONS]
    live_identity_operations = {"clip.delete", "clip.remove"}
    if invalidators and str(context.operation["op"]) not in live_identity_operations:
        raise ValueError(
            "cannot safely reuse a prepared track/index after positional "
            f"mutation(s) {invalidators}; refresh target identity before "
            "the next mutation")
    return _target_position(context.snapshot, unique_id)


def _target_identity_args(context, unique_id: str) -> list[str]:
    matches = [clip for track in context.snapshot["tracks"]
               for clip in track["clips"]
               if str(clip["unique_id"]) == unique_id]
    if len(matches) != 1:
        raise ValueError(
            f"target {unique_id!r} resolved to {len(matches)} prepared items")
    clip = matches[0]
    required = ("source_file", "source_in_frame", "source_out_frame")
    missing = [key for key in required if clip.get(key) is None]
    if missing:
        raise ValueError(
            f"target {unique_id!r} has no complete media identity: {missing}")
    return ["--target-unique-id", unique_id,
            "--expected-source-file", str(clip["source_file"]),
            "--expected-source-in-frame", str(clip["source_in_frame"]),
            "--expected-source-out-frame", str(clip["source_out_frame"]),
            "--expected-record-in-frame", str(clip["record_in"]),
            "--expected-record-out-frame", str(clip["record_out"])]


def _target_position(snapshot: Mapping, unique_id: str) -> tuple[str, int]:
    hits = []
    for track in snapshot["tracks"]:
        for index, clip in enumerate(track["clips"]):
            if clip["unique_id"] == unique_id:
                from library.tools.reel_touchup import _row_of

                hits.append((_row_of(track["type"], int(track["index"])), index))
    if len(hits) != 1:
        raise ValueError(
            f"target {unique_id!r} resolved to {len(hits)} timeline items")
    return hits[0]


def _verified_command(context, command: list[str], operation: dict,
                      recovery_action: str) -> dict:
    from library.tools import operation_acceptance, resolve_axi_core

    result = resolve_axi_core.execute(command)
    if not result.ok:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or f"AXI command for {operation['op']} failed")
    after = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    operation_id = str(operation["op"])
    before = context.snapshot
    target_id = str(operation.get("unique_id", ""))
    before_clip = next((clip for track in before["tracks"]
                        for clip in track["clips"]
                        if clip["unique_id"] == target_id), None)
    after_clip = next((clip for track in after["tracks"]
                       for clip in track["clips"]
                       if clip["unique_id"] == target_id), None)
    operation_matches = False
    evidence = {}
    if operation_id == "clip.trim" and before_clip and after_clip:
        expected_in = int(before_clip["source_in_frame"]) + (
            int(operation["in_frame"]) - int(before_clip["record_in"]))
        expected_out = int(before_clip["source_out_frame"]) - (
            int(before_clip["record_out"]) - int(operation["out_frame"]))
        evidence.update({
            "target_identity": True,
            "requested_source_in_out": (
                int(after_clip["source_in_frame"]) == expected_in
                and int(after_clip["source_out_frame"]) == expected_out),
            "requested_record_span": (
                int(after_clip["record_in"]) == int(operation["in_frame"])
                and int(after_clip["record_out"]) == int(operation["out_frame"])),
        })
        operation_matches = all(evidence.values())
    elif operation_id == "clip.delete":
        evidence["target_absent"] = after_clip is None
        operation_matches = after_clip is None
    elif operation_id == "clip.remove" and before_clip is not None:
        evidence.update(_remove_readback_evidence(
            before, after, target_id, before_clip, after_clip))
        operation_matches = all(evidence.values())
    elif operation_id == "caption_fix" and before_clip and after_clip:
        # A timeline inventory does not expose rendered title text. Do not
        # turn command prose into evidence for this operation.
        operation_matches = None
    if operation_id == "clip.remove" and before_clip is not None:
        # A ripple moves every later cut, so the collateral scan excuses
        # the rippled range and judges predecessors strictly; the
        # downstream rule above judges what moved.
        affected: tuple = ((int(before_clip["record_in"]),
                             int(before["end_frame"])),)
    else:
        affected = ()
    unrelated = _unrelated_changes(before, after, target_id,
                                   affected_spans=affected)
    evidence["unrelated_items_unchanged"] = not unrelated
    acceptance = operation_acceptance.evaluate(
        {**operation, "op": operation_id},
        readback_passed=operation_matches, evidence=evidence)
    if not acceptance["passed"]:
        missing = ", ".join(acceptance["missing"]) or "none"
        failed = ", ".join(acceptance["failed"]) or "none"
        raise RuntimeError(
            f"AXI read-back acceptance failed for {operation_id}: "
            f"missing evidence [{missing}], failed checks [{failed}]")
    import re

    journal_match = re.search(r"(?m)^\s*entry:\s*(\S+)\s*$",
                              result.stdout)
    recovery_reference = None
    if journal_match:
        recovery_reference = {
            "kind": "undo_journal_entry",
            "project_folder": _project_folder(context),
            "entry_id": journal_match.group(1),
        }
    elif operation_id in {"clip.trim", "clip.delete", "clip.remove"}:
        raise RuntimeError(
            f"AXI command for {operation_id} verified without a recovery "
            "journal reference")
    target_identity = {
        key: context.operation[key]
        for key in ("unique_id", "client_unique_id")
        if key in context.operation
    }
    unique_id = str(context.operation.get("unique_id") or "")
    if unique_id:
        clips = [clip for track in context.snapshot["tracks"]
                 for clip in track["clips"]
                 if str(clip["unique_id"]) == unique_id]
        if len(clips) == 1:
            target_identity.update({
                key: clips[0][key]
                for key in ("source_file", "source_in_frame",
                            "source_out_frame", "record_in", "record_out")
                if key in clips[0]
            })
    return {
        "status": "committed",
        "backend": "resolve_axi",
        "mutated": True,
        "operation_id": operation_id,
        "request_id": context.request.id,
        "operation_index": int(getattr(context, "operation_index", 0)),
        "target_identity": {
            **target_identity,
        },
        "readback": {"snapshot": after, "command_output": result.stdout},
        "acceptance_contracts": [acceptance],
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": operation_id, "passed": True},),
                "evidence": {"acceptance": acceptance,
                             "snapshot_source": "fresh_live_timeline"},
            },
            "recovery": {
                "status": "available" if recovery_reference else "unavailable",
                "action": recovery_action,
                **({"reference": recovery_reference}
                   if recovery_reference else {}),
            },
        },
    }


def _unrelated_changes(before: Mapping, after: Mapping, target_id: str,
                       *, affected_spans=()) -> list:
    from library.tools.transaction_orchestration import unrelated_clip_changes

    return unrelated_clip_changes(
        operations=([{"unique_id": target_id}] if target_id else ()),
        affected_spans=affected_spans, id_map=None, before=before,
        after=after)


def _remove_readback_evidence(before: Mapping, after: Mapping,
                              target_id: str, before_clip: Mapping,
                              after_clip: Mapping | None) -> dict:
    """Independent read-back postconditions for a ripple remove.

    Measured off the before/after pair from the fresh live read: the
    target is gone, its track shows no gap where it was, and every
    surviving clip kept its content (source span and duration) while
    sitting either where it was or exactly one removed-duration
    earlier. Coextensive partners (the linked-audio signature: the same
    record span on another track) must have gone with the picture;
    anything else that vanished is reported, not excused.
    """
    target_in = int(before_clip["record_in"])
    target_out = int(before_clip["record_out"])
    duration = target_out - target_in
    evidence = {"target_absent": after_clip is None}
    after_clips = [clip for track in after["tracks"]
                   for clip in track["clips"]]
    after_by_id = {str(clip["unique_id"]): clip for clip in after_clips}
    track_clips = [clip for track in after["tracks"]
                   for clip in track["clips"]
                   if (str(track["type"])
                       == str(before_clip.get("track_type") or "video")
                       and int(track["index"])
                       == int(before_clip.get("track_index") or 1))]
    successors = [clip for clip in track_clips
                  if int(clip["record_in"]) >= target_in]
    if successors:
        evidence["gap_closed"] = (
            min(int(clip["record_in"]) for clip in successors) == target_in)
    else:
        evidence["gap_closed"] = all(
            int(clip["record_out"]) <= target_in for clip in track_clips)
    downstream_ok = True
    for track in before["tracks"]:
        for clip in track["clips"]:
            unique_id = str(clip["unique_id"])
            if unique_id == target_id:
                continue
            after_match = after_by_id.get(unique_id)
            if after_match is None:
                if (int(clip["record_in"]) == target_in
                        and int(clip["record_out"]) == target_out):
                    continue
                downstream_ok = False
                continue
            if (int(clip["record_in"]) >= target_in
                    and int(clip["record_out"]) <= target_out):
                # A survivor that lived entirely inside the removed span -
                # an orphaned linked partner or a stacked item the ripple
                # should have taken with the picture. (A successor the
                # ripple moved INTO that span is judged by its shift
                # below, not here.)
                downstream_ok = False
                continue
            if (int(after_match["duration"]) != int(clip["duration"])
                    or int(after_match["source_in_frame"])
                    != int(clip["source_in_frame"])
                    or int(after_match["source_out_frame"])
                    != int(clip["source_out_frame"])):
                downstream_ok = False
                continue
            shifted = (int(after_match["record_in"])
                       - int(clip["record_in"]))
            if shifted not in (0, -duration) or (
                    int(clip["record_out"]) <= target_in
                    and shifted != 0):
                downstream_ok = False
    new_ids = (set(after_by_id)
               - {str(clip["unique_id"]) for track in before["tracks"]
                  for clip in track["clips"]})
    if new_ids:
        downstream_ok = False
    evidence["downstream_continuity_preserved"] = downstream_ok
    return evidence


def _trim(context) -> dict:
    operation = context.operation
    unique_id = str(operation["unique_id"])
    row, index = _target_position_for_context(context, unique_id)
    clips = context.snapshot["tracks"]
    clip = next(
        clip for track in clips for clip in track["clips"]
        if clip["unique_id"] == unique_id)
    source_in = int(clip["source_in_frame"]) + (
        int(operation["in_frame"]) - int(clip["record_in"]))
    source_out = int(clip["source_out_frame"]) - (
        int(clip["record_out"]) - int(operation["out_frame"]))
    if source_out < source_in:
        raise ValueError("clip.trim would leave no source frames")
    return _verified_command(context, [
        "edit", "trim", "--track", row, "--index", str(index),
        "--source-in", str(source_in), "--source-out", str(source_out),
        *_target_identity_args(context, unique_id),
        "--project-folder", _project_folder(context), "--require-recovery",
        "--apply", "--project", context.request.project,
        "--timeline", context.request.timeline,
    ], {**operation, "op": "clip.trim"},
       "AXI undo journal; failed replacement restores the timeline snapshot")


def _delete(context) -> dict:
    row, index = _target_position_for_context(
        context, str(context.operation["unique_id"]))
    return _verified_command(context, [
        "edit", "delete", "--track", row, "--index", str(index),
        *_target_identity_args(context, str(context.operation["unique_id"])),
        "--project-folder", _project_folder(context), "--require-recovery",
        "--apply", "--project", context.request.project,
        "--timeline", context.request.timeline,
    ], {**context.operation, "op": "clip.delete"},
       "AXI undo journal; verify exact item absence before recovery")


def _remove(context) -> dict:
    """Ripple a target off the reel through the measured delete command.

    The physical write is `edit delete --ripple` with the same live
    target identity the local delete carries; the read-back half is the
    `clip.remove` acceptance contract (absence, gap close, downstream
    continuity, collateral scan), not the local-delete absence alone.
    """
    row, index = _target_position_for_context(
        context, str(context.operation["unique_id"]))
    return _verified_command(context, [
        "edit", "delete", "--track", row, "--index", str(index),
        "--ripple",
        *_target_identity_args(context, str(context.operation["unique_id"])),
        "--project-folder", _project_folder(context), "--require-recovery",
        "--apply", "--project", context.request.project,
        "--timeline", context.request.timeline,
    ], {**context.operation, "op": "clip.remove"},
       "AXI undo journal; failed ripple restores the timeline snapshot")


def _caption_fix(context) -> dict:
    raise RuntimeError(
        "caption_fix is refused before mutation: fresh timeline inventory "
        "does not expose title text, so independent read-back evidence is "
        "missing")


def _speech_row_signatures(snapshot: Mapping) -> list:
    """The V1 plus every audio row as comparable identity tuples.

    The speech broll.insert promises to preserve: the A-roll video row
    and every audio row, with each clip's identity and spans. The cover
    row itself is excluded - the placement lands there.
    """
    rows = []
    for track in snapshot.get("tracks", []):
        track_type = str(track.get("type", ""))
        try:
            track_index = int(track.get("index", 0))
        except (TypeError, ValueError):
            continue
        if not (track_type == "audio"
                or (track_type == "video" and track_index == 1)):
            continue
        for clip in track.get("clips") or []:
            rows.append((track_type, track_index, str(clip.get("unique_id")),
                         int(clip["record_in"]), int(clip["record_out"]),
                         int(clip["source_in_frame"]),
                         int(clip["source_out_frame"]),
                         str(clip.get("source_file", ""))))
    return sorted(rows)


def _broll_insert(context) -> dict:
    """Place supplied B-roll over a marked phrase without touching speech.

    The physical write is the journaled AXI `edit place` verb (the same
    AppendToTimeline placement clip.insert is built on), constrained to
    a cover video track: V1 and every audio row are refused before any
    write, so the A-roll and its audio cannot be overwritten. The
    prepared speech rows are re-read live before the write and must
    match preparation; afterwards the placed cover item and the
    unchanged speech rows are judged from a fresh live snapshot, and
    the placement is claimed only with a verified undo journal
    reference.
    """
    from library.tools import edit_algebra, operation_acceptance, resolve_axi_core

    operation = dict(context.operation)
    track_type = str(operation.get("track_type", "video"))
    try:
        track_index = int(operation["track_index"])
        record_in = int(operation["record_in"])
        record_out = int(operation["record_out"])
        source_in = int(operation["source_in"])
        source_out = int(operation["source_out"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"broll.insert needs integer track/record/source spans ({exc})")
    clip_name = str(operation.get("clip") or "")
    if track_type != "video":
        raise ValueError(
            f"broll.insert refuses {track_type!r}: a cover is video "
            "over the A-roll, never an audio write")
    if track_index < 2:
        raise ValueError(
            f"broll.insert refuses video track {track_index}: the cover "
            "lands on a cover track (V2+), never on the V1 A-roll")
    if not clip_name:
        raise ValueError(
            "broll.insert needs the pool clip name ('clip') of the "
            "supplied matching B-roll")
    if record_out <= record_in or source_out <= source_in:
        raise ValueError(
            "broll.insert needs positive record and source spans")
    if record_out - record_in != source_out - source_in + 1:
        raise ValueError(
            f"broll.insert record span {record_in}..{record_out} does not "
            f"match the marked source range {source_in}..{source_out} "
            "(inclusive): the cover plays the whole marked range exactly")
    before = context.snapshot
    live_before = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    cover_tracks = [track for track in live_before.get("tracks", [])
                    if str(track.get("type")) == "video"
                    and int(track.get("index", -1)) == track_index]
    if len(cover_tracks) != 1:
        raise ValueError(
            f"broll.insert cover track V{track_index} is absent from "
            "the live timeline - create the row before covering it")
    for clip in cover_tracks[0].get("clips") or []:
        if int(clip["record_in"]) < record_out \
                and record_in < int(clip["record_out"]):
            raise ValueError(
                f"broll.insert cover span {record_in}..{record_out} is "
                f"occupied on V{track_index} by "
                f"{clip.get('unique_id')!r} - refusing rather than "
                "stacking a second cover")
    if (_speech_row_signatures(live_before)
            != _speech_row_signatures(before)):
        raise ValueError(
            "broll.insert speech rows changed since preparation: the "
            "A-roll or audio underneath no longer reads as prepared - "
            "re-prepare before covering it")
    command = [
        "edit", "place",
        "--clip", clip_name,
    ]
    if str(operation.get("bin") or ""):
        command += ["--bin", str(operation["bin"])]
    command += [
        "--source-in", str(source_in), "--source-out", str(source_out),
        "--media", "video",
        "--track", str(track_index), "--record", str(record_in),
        "--project-folder", _project_folder(context), "--require-recovery",
        "--apply", "--project", context.request.project,
        "--timeline", context.request.timeline,
    ]
    result = resolve_axi_core.execute(command)
    if not result.ok:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or "AXI edit place for broll.insert failed")
    after = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    before_uids = {str(clip.get("unique_id")) for track in before.get("tracks", [])
                   for clip in track.get("clips") or []}
    spec = edit_algebra.get("broll.insert")
    placed = [str(clip["unique_id"])
              for track in after.get("tracks", [])
              for clip in track.get("clips") or []
              if str(clip.get("unique_id")) not in before_uids
              and spec.verify({**operation,
                               "unique_id": str(clip.get("unique_id"))},
                              after) is None]
    if len(placed) != 1:
        raise RuntimeError(
            f"broll.insert placed {len(placed)} verifiable cover items, "
            "not exactly one - verify by hand before retrying")
    placed_uid = placed[0]
    placed_clip = next(clip for track in after.get("tracks", [])
                       for clip in track.get("clips") or []
                       if str(clip.get("unique_id")) == placed_uid)
    speech_preserved = (_speech_row_signatures(after)
                        == _speech_row_signatures(before))
    evidence = {
        "target_identity": True,
        "source_identity": True,
        "requested_record_span": True,
        "requested_track": True,
        "underlying_speech_preserved": speech_preserved,
    }
    from library.tools.transaction_orchestration import unrelated_clip_changes

    collateral = unrelated_clip_changes(
        operations=[{"unique_id": placed_uid,
                     "client_unique_id": str(operation.get("unique_id") or "")}],
        affected_spans=[(record_in, record_out)], id_map=None,
        before=before, after=after)
    evidence["unrelated_items_unchanged"] = not collateral
    acceptance = operation_acceptance.evaluate(
        {**operation, "op": "broll.insert"},
        readback_passed=all((evidence["target_identity"],
                             evidence["source_identity"],
                             evidence["requested_record_span"],
                             evidence["requested_track"],
                             evidence["underlying_speech_preserved"])),
        evidence=evidence)
    if not acceptance["passed"]:
        missing = ", ".join(acceptance["missing"]) or "none"
        failed = ", ".join(acceptance["failed"]) or "none"
        raise RuntimeError(
            "AXI read-back acceptance failed for broll.insert: "
            f"missing evidence [{missing}], failed checks [{failed}]")
    import re

    journal_match = re.search(r"(?m)^\s*entry:\s*(\S+)\s*$",
                              result.stdout)
    if not journal_match:
        raise RuntimeError(
            "broll.insert verified without a recovery journal reference")
    recovery_reference = {
        "kind": "undo_journal_entry",
        "project_folder": _project_folder(context),
        "entry_id": journal_match.group(1),
    }
    return {
        "status": "committed",
        "backend": "resolve_axi",
        "mutated": True,
        "operation_id": "broll.insert",
        "request_id": context.request.id,
        "operation_index": int(getattr(context, "operation_index", 0)),
        "target_identity": {
            "unique_id": placed_uid,
            "client_unique_id": str(operation.get("unique_id") or ""),
            "source_file": str(placed_clip.get("source_file", "")),
            "source_in_frame": int(placed_clip["source_in_frame"]),
            "source_out_frame": int(placed_clip["source_out_frame"]),
            "record_in": int(placed_clip["record_in"]),
            "record_out": int(placed_clip["record_out"]),
        },
        "readback": {"snapshot": after, "command_output": result.stdout},
        "acceptance_contracts": [acceptance],
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": "broll.insert", "passed": True},),
                "evidence": {"acceptance": acceptance,
                             "snapshot_source": "fresh_live_timeline"},
            },
            "recovery": {
                "status": "available",
                "action": ("AXI undo journal; delete the placed cover item "
                           "to restore the timeline"),
                "reference": recovery_reference,
            },
        },
    }


def _caption_style_props(source_file: str) -> dict:
    """The tight render props written beside a placed caption card.

    The same layout `caption_width._props` reads: `<stem>_props.json`
    beside the card carries the bounds, words and timings the card was
    drawn from. Without them a restyle would have to guess what the
    card says or when it plays, so an unreadable sidecar refuses.
    """
    import json
    import os

    stem = str(source_file).removesuffix(".mov")
    path = stem + "_props.json"
    try:
        with open(path, encoding="utf-8") as handle:
            props = json.load(handle)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"caption.style cannot re-render "
            f"{os.path.basename(str(source_file))!r}: no readable render "
            f"props beside it ({path}: {exc}) - refusing to guess bounds, "
            f"text or timing") from None
    if not isinstance(props, dict):
        raise TypeError(
            f"caption.style cannot re-render "
            f"{os.path.basename(str(source_file))!r}: {path} holds no "
            f"props mapping")
    return props


def _resolve_named_caption_style(project_folder: str,
                                 style_name: str) -> dict:
    """The named style resolved for this project, forced by name.

    The name is the request's own word: video-preference layers never
    override it here, the way an explicit instruction outranks a saved
    default. The project's `pipeline.subtitle_typography` still applies
    key by key (the project typesetting its own captions), and the safe
    area plus the declared caption row still place it - the same
    precedence `subtitle_style.resolve_subtitle_style` keeps. No
    brand-template color palette is re-resolved in this path: the named
    style's own colours apply, never a guessed palette.
    """
    from library.tools import subtitle_style as subtitle_style_mod
    from library.tools.delivery_format import (
        delivery_format_name,
        resolve_format_name,
    )
    from library.tools.safe_area import resolve_safe_area

    try:
        style = subtitle_style_mod.get_subtitle_style(style_name)
    except Exception as exc:
        raise RuntimeError(
            f"caption.style refuses unknown style {style_name!r} "
            f"({exc})") from exc
    typography = dict(
        subtitle_style_mod.project_subtitle_typography(project_folder)
        or {})
    _frame_w, frame_h = resolve_format_name(
        delivery_format_name(project_folder))
    declared_row = subtitle_style_mod.project_caption_row(project_folder)
    return style.resolve(
        typography=typography or None,
        color_palette=None,
        safe_area=resolve_safe_area(project_folder),
        caption_row_px=(None if declared_row is None else
                        subtitle_style_mod.caption_row_px(
                            declared_row, frame_h)),
        frame_h=frame_h)


def _caption_style_prepared_target(context, unique_id: str) -> tuple:
    """The prepared caption clip and its track, resolved exactly once."""
    from library.tools.caption_swap import SUBTITLES_TRACK_NAME

    hits = [(track, clip)
            for track in context.snapshot["tracks"]
            for clip in track["clips"]
            if str(clip.get("unique_id")) == unique_id]
    if len(hits) != 1:
        raise ValueError(
            f"caption.style target {unique_id!r} resolved to "
            f"{len(hits)} prepared items")
    track, clip = hits[0]
    if track.get("name") != SUBTITLES_TRACK_NAME:
        raise ValueError(
            f"caption.style target {unique_id!r} sits on "
            f"{track.get('name')!r}, not the {SUBTITLES_TRACK_NAME!r} "
            f"row - a style op on a non-caption is a mislabelled write")
    if not clip.get("source_file"):
        raise ValueError(
            f"caption.style target {unique_id!r} has no source file - "
            f"there is no placed card to re-render")
    return track, clip


def _caption_style_revalidate_live(context, clip: dict) -> dict:
    """Fresh live read-back of the target before anything is rendered.

    The prepared identity (media plus record span) must still hold on
    the live timeline: a card Resolve moved, replaced or removed since
    preparation is stale, and rendering against the stale read would
    style the wrong pixels.
    """
    from library.tools import resolve_axi_core

    unique_id = str(clip["unique_id"])
    after = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    matches = [live for track in after["tracks"]
               for live in track["clips"]
               if str(live.get("unique_id")) == unique_id]
    if len(matches) != 1:
        raise RuntimeError(
            f"caption.style target {unique_id!r} is stale: it resolves "
            f"to {len(matches)} live items, not one - nothing was rendered")
    live = matches[0]
    for key in ("source_file", "source_in_frame", "source_out_frame",
                "record_in", "record_out"):
        if str(live.get(key)) != str(clip.get(key)):
            raise RuntimeError(
                f"caption.style target {unique_id!r} is stale: live "
                f"{key} reads {live.get(key)!r}, not the prepared "
                f"{clip.get(key)!r} - nothing was rendered")
    return after


def _caption_style(context) -> dict:
    """Re-render one caption card in the requested style and swap it in.

    The physical work is the caption render path, not a new writer:
    the card's own props (bounds, words, timings) are re-rendered with
    only the style replaced, through `subtitles.rerender_swap`, and the
    new file is ReplaceClip'ed onto the card with the swap's own
    per-placement read-back and post-check. Text and timing are never
    rewritten here - the new props are the old props with a new style,
    compared key by key before anything touches Resolve.

    Fail-closed notes: an unknown style name, a target that is not a
    Subtitles-row card, a stale live identity, an unreadable props
    sidecar, and a card that already renders the requested style all
    refuse before mutation. Live Resolve qualification is deferred, so
    ordinary production routing refuses this op; the adapter, its
    read-back and its recovery exist so that qualification has
    something to judge.
    """
    import os

    from library.tools import operation_acceptance, resolve_axi_core

    operation = dict(context.operation)
    operation_id = "caption.style"
    unique_id = str(operation.get("unique_id") or "")
    style_name = operation.get("style")
    if not unique_id:
        raise ValueError(
            "caption.style names no unique_id - the card to restyle is "
            "required, never inferred")
    if not isinstance(style_name, str) or not style_name:
        raise ValueError(
            f"caption.style needs a style naming SUBTITLE_STYLES, got "
            f"{style_name!r}")
    try:
        from library.tools import subtitle_style as subtitle_style_names

        subtitle_style_names.get_subtitle_style(style_name)
    except Exception as exc:
        raise RuntimeError(
            f"caption.style refuses unknown style {style_name!r} "
            f"({exc})") from exc
    _track, clip = _caption_style_prepared_target(context, unique_id)
    _caption_style_revalidate_live(context, dict(clip))
    project_folder = _project_folder(context)
    old_file = str(clip["source_file"])
    old_props = _caption_style_props(old_file)
    old_style = (old_props.get("style")
                 if isinstance(old_props.get("style"), dict) else {})
    new_style = _resolve_named_caption_style(project_folder, style_name)
    if "captionMaxWidth" in old_style:
        new_style = {**new_style,
                     "captionMaxWidth": old_style["captionMaxWidth"]}
    if old_style == new_style:
        raise RuntimeError(
            f"caption.style has nothing to do: card {unique_id!r} already "
            f"renders style {style_name!r} - refusing rather than "
            f"claiming a restyle")
    new_props = dict(old_props)
    new_props["style"] = new_style
    if ({key: new_props[key] for key in new_props if key != "style"} !=
            {key: old_props[key] for key in old_props if key != "style"}):
        raise RuntimeError(
            f"caption.style restyle of card {unique_id!r} would rewrite "
            f"more than the style - refusing a restyle that moves text "
            f"or timing")

    from library.tools import operations as pipeline_operations

    rerender_and_swap = pipeline_operations.get(
        "subtitles.rerender_swap").run
    report = rerender_and_swap(
        project_folder,
        [{"old_mov": old_file,
          "timeline_label": context.request.timeline,
          "props": new_props}],
        swap=True)
    if not report.get("ok"):
        raise RuntimeError(
            f"caption.style render-and-swap refused: "
            f"{report.get('error') or report}")
    new_file = dict(report.get("map") or {}).get(old_file)
    if not new_file:
        raise RuntimeError(
            f"caption.style has nothing to do: card {unique_id!r} already "
            f"renders style {style_name!r} "
            f"(already-current: {report.get('already_current') or []}) - "
            f"refusing rather than claiming a restyle")

    after = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    live_hits = [live for track in after["tracks"]
                 for live in track["clips"]
                 if str(live.get("unique_id")) == unique_id]

    evidence = {
        "target_identity": len(live_hits) == 1,
        "requested_record_span": (
            len(live_hits) == 1
            and int(live_hits[0]["record_in"]) == int(clip["record_in"])
            and int(live_hits[0]["record_out"]) == int(clip["record_out"])),
        "caption_text_preserved": (
            {key: new_props[key] for key in new_props if key != "style"}
            == {key: old_props[key] for key in old_props if key != "style"}),
        "caption_style_applied": (
            bool(new_file) and os.path.isfile(new_file)
            and len(live_hits) == 1
            and str(live_hits[0].get("source_file")) == str(new_file)),
    }
    operation_matches = all(evidence.values())
    unrelated = _unrelated_changes(context.snapshot, after, unique_id)
    evidence["unrelated_items_unchanged"] = not unrelated
    acceptance = operation_acceptance.evaluate(
        {**operation, "op": operation_id},
        readback_passed=operation_matches and not unrelated,
        evidence=evidence)
    if not acceptance["passed"]:
        missing = ", ".join(acceptance["missing"]) or "none"
        failed = ", ".join(acceptance["failed"]) or "none"
        raise RuntimeError(
            f"AXI read-back acceptance failed for {operation_id}: "
            f"missing evidence [{missing}], failed checks [{failed}]")
    target_identity = {
        key: operation[key]
        for key in ("unique_id", "client_unique_id")
        if key in operation
    }
    target_identity.update({
        key: clip[key]
        for key in ("source_file", "source_in_frame",
                    "source_out_frame", "record_in", "record_out")
        if key in clip
    })
    return {
        "status": "committed",
        "backend": "resolve_axi",
        "mutated": True,
        "operation_id": operation_id,
        "request_id": context.request.id,
        "operation_index": int(getattr(context, "operation_index", 0)),
        "target_identity": {
            **target_identity,
        },
        "readback": {"snapshot": after,
                      "render_report": {
                          "rendered": report.get("rendered"),
                          "reused": report.get("reused"),
                          "swapped": report.get("swapped"),
                      },
                      "previous_file": old_file, "new_file": new_file},
        "acceptance_contracts": [acceptance],
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": operation_id, "passed": True},),
                "evidence": {"acceptance": acceptance,
                             "snapshot_source": "fresh_live_timeline",
                             "previous_file": old_file,
                             "new_file": new_file},
            },
            "recovery": {
                "status": "recorded",
                "action": "swap the retained previous-generation file "
                          "back onto the card (old caption files stay on "
                          "disk as superseded generations; caption.style "
                          "is self-inverse)",
                "reference": {
                    "kind": "caption_swap_back",
                    "project_folder": project_folder,
                    "unique_id": unique_id,
                    "previous_file": old_file,
                    "current_file": new_file,
                },
            },
        },
    }


def _audio_gain(context) -> dict:
    raise RuntimeError(
        "audio.gain is refused before mutation: the candidate receipt lacks "
        "independent audio-render evidence for levels, clipping, and silence")


def _multicam_select_angle(context) -> dict:
    operation = context.operation
    unique_id = str(operation.get("unique_id") or "")
    if not unique_id:
        raise ValueError("multicam.select_angle needs a target unique_id")
    try:
        angle_index = int(operation["angle_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "multicam.select_angle needs an integer angle_index "
            f"({exc}) - nothing was written.") from exc
    # Target identity revalidation before anything else: the prepared
    # position must still resolve exactly once, and the prepared media
    # identity must be complete. A stale or ambiguous target refuses
    # here, before any Resolve contact.
    _target_position_for_context(context, unique_id)
    _target_identity_args(context, unique_id)
    raise RuntimeError(
        "multicam.select_angle is refused before mutation: the timeline "
        f"inventory exposes no per-item angle, so angle {angle_index} on "
        f"target {unique_id!r} could never be verified; the AXI dispatch "
        "exposes no guarded set-angle verb (only the sense-switch "
        "auto-cut and the pool multicam build/sync verbs); and the staged "
        "replacement needs the assigned camera's synced source range, "
            "which preparation does not supply. Nothing was written.")


def _reel_opening_decided(operation: Mapping) -> tuple[str, list[dict]]:
    """The validated brief and structural edits a reel.opening carries.

    The model decides the edits; this adapter never invents trim points.
    A ripple edit is refused before the first write: no ripple executor
    qualifies today, so executing around it would leave a half-tightened
    opening behind.
    """
    from library.tools import edit_algebra

    brief = operation.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError(
            "reel.opening carries no supplied brief - tightening an "
            "opening without one would invent the target")
    edits = operation.get("edits")
    if not isinstance(edits, (list, tuple)) or not edits:
        raise ValueError(
            "reel.opening carries no structural edits - nothing decided "
            "is nothing to execute")
    allowed = edit_algebra.REEL_OPENING_STRUCTURAL_OPS
    decided = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise TypeError(
                f"reel.opening edit {index} must be a mapping")
        edit = dict(edit)
        edit_id = str(edit.get("op") or "")
        if edit_id not in allowed:
            raise ValueError(
                f"reel.opening edit {index} names {edit_id!r}; one of "
                f"{list(allowed)}")
        decided.append(edit)
    if any(str(edit.get("op")) == "clip.ripple" for edit in decided):
        raise ValueError(
            "reel.opening refuses a ripple edit before mutation: no "
            "qualified ripple executor exists (the staged adapter raises "
            "for clip.ripple) - re-plan the close as trim/remove, or "
            "qualify ripple first")
    return brief, decided


def _compensate_opening_edits(context, sub_receipts: list) -> dict:
    """Undo committed opening edits in reverse order, verifying each.

    The outer batch compensator only sees this adapter's aggregate
    receipt, so a mid-sequence failure restores here before the raise -
    nothing half-tightened escapes the adapter.
    """
    from library.tools import undo_journal

    actions = []
    for receipt in reversed(sub_receipts):
        reference = ((receipt.get("execution_contract") or {})
                     .get("recovery", {}).get("reference", {}))
        try:
            result = undo_journal.undo(
                str(reference["project_folder"]),
                final=context.request.timeline,
                act=str(reference["entry_id"]))
            ok = (isinstance(result, list) and len(result) == 1
                  and (not isinstance(result[0], Mapping)
                       or result[0].get("verified") is not False))
        except Exception as exc:  # noqa: BLE001 - preserve partial state
            return {
                "status": "recovery_required", "actions": actions,
                "failed_entry": reference.get("entry_id"),
                "error": f"{type(exc).__name__}: {exc}",
            }
        if not ok:
            return {
                "status": "recovery_required", "actions": actions,
                "failed_entry": reference.get("entry_id"),
                "error": "undo journal did not return one verified restoration",
            }
        actions.append({"entry_id": reference.get("entry_id"),
                        "verified": True})
    return {"status": "restored_original_verified", "actions": actions}


def _opening_preserved(before: Mapping, live: Mapping,
                       ordered: list) -> bool:
    """Whether every retained clip kept what the edits did not decide.

    Removed clips must be exactly the decided delete list and nothing
    may have appeared. Retained non-targets must read identical on
    their stable fields; retained targets must keep source file,
    transform, switch and speed - their spans are judged by the
    opening-span read-back instead. A linked companion the executor
    moved without a decided edit fails here: that is collateral, not
    sync.
    """
    def rows(snapshot: Mapping) -> dict:
        return {str(clip["unique_id"]): clip
                for track in (snapshot.get("tracks") or [])
                for clip in (track.get("clips") or [])}

    stable = ("source_file", "source_in_frame", "source_out_frame",
              "record_in", "record_out", "transform", "enabled",
              "speed", "name", "track_type", "track_index")
    target_stable = ("source_file", "transform", "enabled", "speed",
                     "name")
    old, new = rows(before), rows(live)
    removed = {str(edit["unique_id"]) for edit in ordered
               if str(edit.get("op")) == "clip.delete"}
    targets = {str(edit.get("unique_id")) for edit in ordered
               if edit.get("unique_id")}
    if {uid for uid in old if uid not in new} != removed:
        return False
    if any(uid not in old for uid in new):
        return False
    for uid, clip in old.items():
        if uid in removed:
            continue
        kept = new[uid]
        keys = target_stable if uid in targets else stable
        if any(clip.get(key) != kept.get(key) for key in keys):
            return False
    return True


def _reel_opening(context) -> dict:
    """Tighten a reel opening through its decided structural edits.

    A planning-level operation: the carried trim/remove edits are
    compiled through EditRequest (dependency order, affected spans,
    conflict domains - never a full rebuild) and each runs through its
    own existing executor against a fresh live snapshot, so no
    positional address goes stale between writes. Targets are
    revalidated before the first write; every edit needs its own undo
    journal entry and read-back; the aggregate carries the opening's
    own acceptance contract with the collateral check.
    """
    import dataclasses

    from library.tools import edit_algebra, operation_acceptance, resolve_axi_core
    from library.tools.edit_request import compile_request, from_algebra_ops
    from library.tools.transaction_orchestration import (
        unrelated_clip_changes,
    )

    operation = dict(context.operation)
    anchor_id = str(operation.get("unique_id") or "")
    if not anchor_id:
        raise ValueError(
            "reel.opening names no anchor clip unique_id")
    brief, decided = _reel_opening_decided(operation)

    stated = [
        {**edit, "expected_effect": str(
            edit.get("expected_effect")
            or f"tighten opening: apply {edit.get('op')} "
               f"to {edit.get('unique_id')}")}
        for edit in decided
    ]
    generation = getattr(context, "generation", None)
    base_generation = (None if generation is None else getattr(
        generation, "generation", None))
    request = from_algebra_ops(
        stated,
        project=context.request.project, timeline=context.request.timeline,
        expected_base_generation=(
            None if base_generation is None
            else int(base_generation)),
        originator={"kind": "agent", "name": "reel.opening"},
        preconditions=[{
            "preserves": "speaker meaning and approved edits outside "
                         "the decided edits"}],
    )
    compiled = compile_request(request, context.snapshot)
    ordered = [dict(op) for op in compiled.operations]

    resolve_axi_core._revalidate_registered_targets(
        request=context.request, operations=ordered,
        snapshot=context.snapshot)

    executors = {"clip.trim": _trim, "clip.delete": _delete}
    before = context.snapshot
    live = before
    sub_receipts: list = []
    journal_refs: list = []
    try:
        for edit in ordered:
            sub_context = dataclasses.replace(
                context, operation=edit, snapshot=live,
                operation_index=0, compiled=None)
            receipt = executors[str(edit["op"])](sub_context)
            if (receipt.get("status") != "committed"
                    or (receipt.get("execution_contract") or {})
                    .get("verification", {}).get("status") != "verified"):
                raise RuntimeError(
                    f"reel.opening edit {edit.get('op')} returned no "
                    f"verified commit")
            reference = ((receipt.get("execution_contract") or {})
                         .get("recovery", {}).get("reference", {}))
            if reference.get("kind") != "undo_journal_entry":
                raise RuntimeError(
                    f"reel.opening edit {edit.get('op')} verified without "
                    f"an undo journal reference")
            sub_receipts.append(receipt)
            journal_refs.append(reference)
            live = resolve_axi_core.read_live_timeline(
                context.request.project, context.request.timeline)

        opening_ok = edit_algebra.get("reel.opening").verify(
            {**operation, "edits": ordered}, live) is None
        anchor_before = [clip for track in before["tracks"]
                         for clip in track["clips"]
                         if str(clip["unique_id"]) == anchor_id]
        anchor_live = [clip for track in live["tracks"]
                       for clip in track["clips"]
                       if str(clip["unique_id"]) == anchor_id]
        evidence = {
            "target_identity": (
                len(anchor_before) == 1 and len(anchor_live) == 1
                and anchor_before[0].get("source_file")
                == anchor_live[0].get("source_file")),
            "requested_opening_span": opening_ok,
            "approved_edits_preserved": _opening_preserved(
                before, live, ordered),
            "unrelated_items_unchanged": not unrelated_clip_changes(
                operations=([{**operation, "unique_id": anchor_id}]
                            + [{"unique_id": edit["unique_id"]}
                               for edit in ordered
                               if edit.get("unique_id")]),
                affected_spans=[tuple(span)
                                for span in compiled.affected_spans],
                id_map=None, before=before, after=live),
        }
        acceptance = operation_acceptance.evaluate(
            {**operation, "op": "reel.opening"},
            readback_passed=opening_ok, evidence=evidence)
        if not acceptance["passed"]:
            missing = ", ".join(acceptance["missing"]) or "none"
            failed = ", ".join(acceptance["failed"]) or "none"
            raise RuntimeError(
                "AXI read-back acceptance failed for reel.opening: "
                f"missing evidence [{missing}], failed checks [{failed}]")
    except Exception as exc:
        compensation = (
            {"status": "not_required", "actions": []}
            if not sub_receipts
            else _compensate_opening_edits(context, sub_receipts))
        raise RuntimeError(
            f"reel.opening failed after {len(sub_receipts)} committed "
            f"edit(s) ({type(exc).__name__}: {exc}); "
            f"compensation: {compensation['status']}") from exc

    target_identity = {
        key: operation[key]
        for key in ("unique_id", "client_unique_id")
        if key in operation
    }
    target_identity["brief"] = brief
    return {
        "status": "committed",
        "backend": "resolve_axi",
        "mutated": True,
        "operation_id": "reel.opening",
        "request_id": context.request.id,
        "operation_index": int(getattr(context, "operation_index", 0)),
        "target_identity": target_identity,
        "readback": {"snapshot": live,
                     "operation_receipts": sub_receipts},
        "acceptance_contracts": [acceptance],
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": (
                    *({"operation": str(edit.get("op")),
                       "passed": True} for edit in ordered),
                    {"operation": "reel.opening", "passed": True},
                ),
                "evidence": {
                    "acceptance": acceptance,
                    "snapshot_source": "fresh_live_timeline",
                    "compiled_spans": [list(span)
                                       for span in compiled.affected_spans],
                    "conflict_domains": list(compiled.conflict_domains),
                },
            },
            "recovery": {
                "status": "available",
                "action": "undo each listed journal entry in reverse "
                          "execution order and verify restoration",
                "references": journal_refs,
            },
        },
    }


def _touchup(context) -> dict:
    from library.tools import project_registry, reel_proposal, reel_touchup

    request = context.request
    project_path = project_registry.resolve_project_path(request.project)
    if project_path is None:
        project = project_registry.get_project(request.project)
        project_folder = str(project.project_root)
    else:
        project_folder = str(project_path.parent)
    matches = [moment for moment in reel_proposal.read_proposal(
        str(reel_proposal.proposal_path(project_folder)))
        if moment.timeline_name == request.timeline]
    if len(matches) != 1:
        raise ValueError(
            f"touch-up timeline {request.timeline!r} matched "
            f"{len(matches)} project reels")

    operation = context.operation
    operation_id = str(operation["op"])
    edit: dict
    if operation_id == "track.add":
        index = max((int(track["index"]) for track in context.snapshot["tracks"]
                     if track["type"] == "video"), default=0) + 1
        edit = {"op": "add_row", "row": f"V{index}",
                "name": str(operation["name"])}
    elif operation_id == "transition":
        from collections.abc import Mapping

        from library.tools.reel_touchup import _row_of

        if len(request.operations) != 1:
            raise ValueError(
                "transition currently requires a single-operation request")
        carrier = operation["carrier"]
        transition = operation["transition"]
        if not isinstance(carrier, Mapping) or not isinstance(transition, Mapping):
            raise ValueError("transition needs carrier and transition objects")
        outgoing_uid = str(carrier["outgoing_unique_id"])
        incoming_uid = str(carrier["incoming_unique_id"])
        cut_frame = int(carrier["cut_frame"])
        locations = [
            (track, index, clip)
            for track in context.snapshot["tracks"]
            for index, clip in enumerate(track["clips"])
            if clip["unique_id"] in {outgoing_uid, incoming_uid}
        ]
        outgoing = [row for row in locations
                    if row[2]["unique_id"] == outgoing_uid]
        incoming = [row for row in locations
                    if row[2]["unique_id"] == incoming_uid]
        if len(outgoing) != 1 or len(incoming) != 1:
            raise ValueError(
                "transition carrier ids must each resolve exactly once")
        out_track, out_index, out_clip = outgoing[0]
        in_track, in_index, in_clip = incoming[0]
        if (_row_of(out_track["type"], int(out_track["index"]))
                != _row_of(in_track["type"], int(in_track["index"]))
                or out_track["type"] != "video"
                or in_track["type"] != "video"
                or in_index != out_index + 1
                or int(out_clip["record_out"]) != cut_frame
                or int(in_clip["record_in"]) != cut_frame + 1):
            raise ValueError(
                "transition carrier must identify adjacent video items at "
                "the exact named cut frame")
        edit = {
            "op": "native_transition",
            "row": _row_of(in_track["type"], int(in_track["index"])),
            "item": in_index,
            "outgoing_unique_id": outgoing_uid,
            "incoming_unique_id": incoming_uid,
            "cut_frame": cut_frame,
            "transition_type": str(transition["type"]),
            "category": str(transition["category"]),
            "duration_frames": int(transition["duration_frames"]),
            "alignment": str(transition.get("alignment", "center")),
        }
    elif operation_id == "overlay.add":
        media = operation.get("file", operation.get("media"))
        if not media or not operation.get("duration"):
            raise ValueError(
                "overlay.add touch-up needs a media path and positive duration")
        edit = {
            "op": "add_overlay", "row": str(operation["row"]),
            "media": str(media), "record": int(operation["record_frame"]),
            "duration": int(operation["duration"]),
            "properties": dict(operation.get("properties") or {}),
        }
        for key in ("left_offset", "right_offset"):
            if key in operation:
                edit[key] = operation[key]
    elif operation_id == "overlay.resize":
        row, index = _target_position_for_context(
            context, str(operation["unique_id"]))
        edit = {"op": "resize_overlay", "row": row, "item": index,
                "width": int(operation["width"]),
                "height": int(operation["height"])}
    elif operation_id == "overlay.position":
        row, index = _target_position_for_context(
            context, str(operation["unique_id"]))
        edit = {"op": "position_overlay", "row": row, "item": index,
                "dx": float(operation["dx"]),
                "dy": float(operation["dy"])}
    else:
        row, index = _target_position_for_context(
            context, str(operation["unique_id"]))
        if operation_id == "audio.fade":
            import math

            if len(request.operations) != 1:
                raise ValueError(
                    "audio.fade currently requires a single-operation "
                    "request so composition cannot replace unrelated audio")
            fps = float(context.snapshot["reported_fps"])
            fade_in = float(operation["fade_in"])
            fade_out = float(operation["fade_out"])
            if (not math.isfinite(fade_in) or not math.isfinite(fade_out)
                    or fade_in < 0 or fade_out < 0):
                raise ValueError(
                    "audio.fade durations must be finite and non-negative")
            from library.tools.frame_utils import duration_to_frames

            edit = {
                "op": "audio_fade", "row": row, "item": index,
                "fade_in_frames": duration_to_frames(fade_in, fps),
                "fade_out_frames": duration_to_frames(fade_out, fps),
            }
        elif operation_id == "clip.move":
            edit = {"op": "move", "row": row, "item": index,
                    "to_record": int(operation["to_frame"])}
        elif operation_id == "clip.ripple":
            raise ValueError(
                "clip.ripple cannot use reel_touchup retime: canonical "
                "ripple shifts later clips while preserving the target "
                "duration; no semantically equivalent staged operation "
                "is currently available")
        elif operation_id == "clip.slip":
            edit = {"op": "slip", "row": row, "item": index,
                    "delta_frames": int(operation["delta_frames"])}
        elif operation_id == "clip.split":
            edit = {"op": "split", "row": row, "item": index,
                    "at_frame": int(operation["at_frame"])}
        elif operation_id == "clip.slide":
            edit = {"op": "slide", "row": row, "item": index,
                    "to_record": int(operation["to_frame"])}
        elif operation_id == "clip.roll":
            edit = {"op": "roll", "row": row, "item": index,
                    "delta_frames": int(operation["delta_frames"])}
        elif operation_id == "overlay.remove":
            edit = {"op": "remove_overlay", "row": row, "item": index}
        elif operation_id == "overlay.swap_pixels":
            edit = {"op": "swap_pixels", "row": row, "item": index,
                    "media": str(operation["file"])}
        elif operation_id == "overlay.replace":
            # The named replace reuses the swap_pixels physical path:
            # the approved file lands at the target's own record span
            # with its position, timing and transform carried across.
            edit = {"op": "swap_pixels", "row": row, "item": index,
                    "media": str(operation["file"])}
        elif operation_id == "clip.set_property":
            edit = {"op": "set_properties", "row": row, "item": index,
                    "properties": {str(operation["key"]): operation["value"]}}
        elif operation_id == "clip.set_enabled":
            edit = {"op": "set_enabled", "row": row, "item": index,
                    "enabled": bool(operation["enabled"])}
        else:  # pragma: no cover - registry table below is authoritative
            raise ValueError(f"no touch-up adapter for {operation_id}")

    receipt = reel_touchup.apply_touchup(
        project_folder, {"reel": int(matches[0].number), "edits": [edit],
                         "acceptance_operations": [dict(operation)]})
    transaction = receipt.get("transaction") or {}
    verification = transaction.get("verification") or {}
    contracts = receipt.get("acceptance_contracts") or []
    if (verification.get("status") != "verified"
            or len(contracts) != 1 or contracts[0].get("passed") is not True):
        raise RuntimeError(
            f"touch-up {operation_id} returned no passing operation "
            "acceptance contract")
    return {
        "status": "committed", "backend": "reel_touchup", "mutated": True,
        "operation_id": operation_id, "request_id": request.id,
        "readback": receipt,
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": tuple({"operation": operation_id,
                                 "check": row["check"], "passed": True}
                                for row in contracts[0]["results"]),
                "evidence": {"acceptance_contract": contracts[0],
                             "touchup_receipt": receipt},
            },
            "recovery": {"status": "recorded",
                         "action": "staged touch-up journal and backup"},
        },
    }


def register_builtin_edit_executors() -> None:
    """Bind supported P0 operations to their existing AXI executors."""
    from library.tools import resolve_axi_core

    desired = {
        ("clip.trim", "resolve_axi"): _trim,
        ("clip.delete", "resolve_axi"): _delete,
        ("clip.remove", "resolve_axi"): _remove,
        ("reel.opening", "resolve_axi"): _reel_opening,
        ("caption_fix", "resolve_axi"): _caption_fix,
        ("broll.insert", "resolve_axi"): _broll_insert,
        ("multicam.select_angle", "resolve_axi"): _multicam_select_angle,
        ("caption.style", "resolve_axi"): _caption_style,
        ("clip.move", "reel_touchup"): _touchup,
        ("clip.ripple", "reel_touchup"): _touchup,
        ("clip.slip", "reel_touchup"): _touchup,
        ("clip.split", "reel_touchup"): _touchup,
        ("clip.slide", "reel_touchup"): _touchup,
        ("clip.roll", "reel_touchup"): _touchup,
        ("audio.fade", "reel_touchup"): _touchup,
        ("audio.gain", "reel_touchup"): _audio_gain,
        ("transition", "reel_touchup"): _touchup,
        ("overlay.resize", "reel_touchup"): _touchup,
        ("overlay.position", "reel_touchup"): _touchup,
        ("overlay.add", "reel_touchup"): _touchup,
        ("overlay.remove", "reel_touchup"): _touchup,
        ("overlay.swap_pixels", "reel_touchup"): _touchup,
        ("overlay.replace", "reel_touchup"): _touchup,
        ("clip.set_property", "reel_touchup"): _touchup,
        ("clip.set_enabled", "reel_touchup"): _touchup,
        ("track.add", "reel_touchup"): _touchup,
    }
    registered = set(resolve_axi_core.registered_edit_executors())
    for (operation_id, backend_id), executor in desired.items():
        if (operation_id, backend_id) not in registered:
            resolve_axi_core.register_edit_executor(
                operation_id, backend_id, executor)
