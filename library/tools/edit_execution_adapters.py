"""Semantic request adapters for existing Resolve edit workflows.

These functions only bind operation ids to already-owned physical
executors. Qualification remains in ``edit_algebra.OP_STATUS`` and routing
remains in ``execution_router``.
"""

from __future__ import annotations

from collections.abc import Mapping


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


def _verified_command(context, command: list[str], operation_id: str,
                      recovery_action: str) -> dict:
    from library.tools.resolve_axi_core import execute

    result = execute(command)
    if not result.ok:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or f"AXI command for {operation_id} failed")
    if "verified: yes" not in result.stdout:
        raise RuntimeError(
            f"AXI command for {operation_id} returned no verified read-back")
    return {
        "status": "committed",
        "backend": "resolve_axi",
        "mutated": True,
        "operation_id": operation_id,
        "request_id": context.request.id,
        "readback": {"stdout": result.stdout},
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": operation_id, "passed": True},),
                "evidence": {"axi_output": result.stdout},
            },
            "recovery": {"status": "recorded", "action": recovery_action},
        },
    }


def _trim(context) -> dict:
    operation = context.operation
    unique_id = str(operation["unique_id"])
    row, index = _target_position(context.snapshot, unique_id)
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
        "--apply", "--project", context.request.project,
        "--timeline", context.request.timeline,
    ], "clip.trim", "AXI undo journal; failed replacement restores the timeline snapshot")


def _delete(context) -> dict:
    row, index = _target_position(
        context.snapshot, str(context.operation["unique_id"]))
    return _verified_command(context, [
        "edit", "delete", "--track", row, "--index", str(index),
        "--apply", "--project", context.request.project,
        "--timeline", context.request.timeline,
    ], "clip.delete",
       "AXI undo journal; verify exact item absence before recovery")


def _caption_fix(context) -> dict:
    operation = context.operation
    row, index = _target_position(
        context.snapshot, str(operation["unique_id"]))
    return _verified_command(context, [
        "edit", "title", "--track", row, "--index", str(index),
        "--text", str(operation["text"]), "--apply", "--project",
        context.request.project, "--timeline", context.request.timeline,
    ], "caption_fix", "no compensating action; operation remains unqualified")


def _audio_gain(context) -> dict:
    import math

    from library.tools import otio_mix, project_registry
    from library.tools.execution.deliver_audio_mix import deliver_semantic_gain

    operation = context.operation
    if len(context.request.operations) != 1:
        raise ValueError(
            "audio.gain currently requires a single-operation request")
    row, _index = _target_position(
        context.snapshot, str(operation["unique_id"]))
    if not row.startswith("A"):
        raise ValueError("audio.gain target must be an audio-track item")
    clip = next(
        clip for track in context.snapshot["tracks"]
        for clip in track["clips"]
        if clip["unique_id"] == operation["unique_id"])
    gain_db = float(operation["db"])
    if not math.isfinite(gain_db):
        raise ValueError("audio.gain dB value must be finite")
    if not otio_mix.MIN_VOLUME_DB <= gain_db <= otio_mix.MAX_VOLUME_DB:
        raise ValueError(
            f"audio.gain dB value must be in [{otio_mix.MIN_VOLUME_DB}, "
            f"{otio_mix.MAX_VOLUME_DB}] dB")
    project_path = project_registry.resolve_project_path(
        context.request.project)
    project = project_registry.get_project(
        context.request.project if project_path is None else str(project_path))
    project_folder = (str(project.project_root) if project_path is None
                      else str(project_path.parent))
    target = otio_mix.audio_op_target(
        "audio.gain", {"db": gain_db}, clip,
        fps=float(context.snapshot["reported_fps"]))
    report = deliver_semantic_gain(
        project_folder, project.resolve.project_name,
        context.request.timeline, target,
        fps=float(context.snapshot["reported_fps"]))
    return {
        "status": "committed", "backend": "reel_touchup", "mutated": True,
        "operation_id": "audio.gain", "request_id": context.request.id,
        "readback": report,
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": "audio.gain", "passed": True},),
                "evidence": {"strict_otio_candidate": report},
            },
            "recovery": {
                "status": "recorded",
                "action": "source timeline retained until candidate preservation and OTIO read-back pass",
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
    else:
        row, index = _target_position(
            context.snapshot, str(operation["unique_id"]))
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
            clip = next(
                clip for track in context.snapshot["tracks"]
                for clip in track["clips"]
                if clip["unique_id"] == operation["unique_id"])
            duration = (int(clip["duration"])
                        + int(operation["delta_frames"]))
            edit = {"op": "retime", "row": row, "item": index,
                    "duration": duration}
        elif operation_id == "clip.slip":
            edit = {"op": "slip", "row": row, "item": index,
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
        project_folder, {"reel": int(matches[0].number), "edits": [edit]})
    transaction = receipt.get("transaction") or {}
    verification = transaction.get("verification") or {}
    if verification.get("status") != "verified":
        raise RuntimeError(
            f"touch-up {operation_id} returned no verified promotion receipt")
    return {
        "status": "committed", "backend": "reel_touchup", "mutated": True,
        "operation_id": operation_id, "request_id": request.id,
        "readback": receipt,
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": operation_id, "passed": True},),
                "evidence": {"touchup_receipt": receipt},
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
        ("audio.fade", "reel_touchup"): _touchup,
        ("audio.gain", "reel_touchup"): _audio_gain,
        ("transition", "reel_touchup"): _touchup,
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
