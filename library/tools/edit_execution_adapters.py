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
    "clip.delete", "clip.trim", "clip.move", "clip.split", "clip.slide", "clip.roll",
    "clip.ripple", "clip.insert", "track.add", "overlay.add",
    "overlay.remove",
})


def _target_position_for_context(context, unique_id: str) -> tuple[str, int]:
    operation_index = int(getattr(context, "operation_index", 0))
    compiled = getattr(context, "compiled", None)
    prior = (compiled.operations[:operation_index] if compiled is not None
             else ())
    invalidators = [str(operation["op"]) for operation in prior
                    if str(operation["op"]) in _POSITION_INVALIDATING_OPERATIONS]
    live_identity_operations = {"clip.delete"}
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
    elif operation_id == "caption_fix" and before_clip and after_clip:
        # A timeline inventory does not expose rendered title text. Do not
        # turn command prose into evidence for this operation.
        operation_matches = None
    unrelated = _unrelated_changes(before, after, target_id)
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
    elif operation_id in {"clip.trim", "clip.delete"}:
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


def _unrelated_changes(before: Mapping, after: Mapping, target_id: str) -> list:
    from library.tools.transaction_orchestration import unrelated_clip_changes

    return unrelated_clip_changes(
        operations=([{"unique_id": target_id}] if target_id else []),
        affected_spans=(), id_map=None, before=before, after=after)


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


def _caption_fix(context) -> dict:
    raise RuntimeError(
        "caption_fix is refused before mutation: fresh timeline inventory "
        "does not expose title text, so independent read-back evidence is "
        "missing")


def _audio_gain(context) -> dict:
    raise RuntimeError(
        "audio.gain is refused before mutation: the candidate receipt lacks "
        "independent audio-render evidence for levels, clipping, and silence")


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
        ("caption_fix", "resolve_axi"): _caption_fix,
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
        ("overlay.add", "reel_touchup"): _touchup,
        ("overlay.remove", "reel_touchup"): _touchup,
        ("overlay.swap_pixels", "reel_touchup"): _touchup,
        ("clip.set_property", "reel_touchup"): _touchup,
        ("clip.set_enabled", "reel_touchup"): _touchup,
        ("track.add", "reel_touchup"): _touchup,
    }
    registered = set(resolve_axi_core.registered_edit_executors())
    for (operation_id, backend_id), executor in desired.items():
        if (operation_id, backend_id) not in registered:
            resolve_axi_core.register_edit_executor(
                operation_id, backend_id, executor)
