"""Semantic request adapters for existing Resolve edit workflows.

These functions only bind operation ids to already-owned physical
executors. Qualification remains in ``edit_algebra.OP_STATUS`` and routing
remains in ``execution_router``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping


def _project_folder(context) -> str:
    """The Ren project FOLDER the request's project names.

    An edit request names the RESOLVE project - that is what the
    broker's open-project check and the shadow store key on - and the
    folder the executor needs (the undo journal's own store, the reel
    proposals) is found through the registry's reverse lookup, the same
    way the touch-up executor finds it. Reading the request's project as
    a SLUG instead raised `No project with slug 'Ren Scratch - ...'`
    for every request that named its Resolve project, so the executors
    that carry a `--project-folder` could not run at all.
    """
    from library.tools import project_registry

    project_path = project_registry.resolve_project_path(context.request.project)
    if project_path is not None:
        return str(project_path.parent)
    folder = project_registry.project_folder_for_resolve_name(
        context.request.project)
    if folder:
        return str(folder)
    project = project_registry.get_project(context.request.project)
    return str(project.project_root)

# These operations can change the positional address of a later item. Until
# this adapter has a fresh live read-back after each command, a subsequent
# positional write must refuse rather than reuse the prepared index.
_POSITION_INVALIDATING_OPERATIONS = frozenset({
    "clip.delete", "clip.remove", "clip.trim", "clip.move", "clip.split",
    "clip.slide", "clip.roll",
    "clip.ripple", "clip.insert", "broll.insert", "track.add", "overlay.add",
    "overlay.remove", "multicam.select_angle",
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


def _axi_target_position_for_context(context, unique_id: str) -> tuple[str, int]:
    """The target's position in the AXI's own track spelling.

    The same staleness guard as the touch-up path, and the AXI's
    spelling rather than the SOP's: an AXI write verb handed `V1`
    refuses with "bad track 'V1' - tracks read video1, audio2, ...".
    """
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
    return _axi_track(context.snapshot, unique_id)


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


#: The AXI writes address a track as `video1` / `audio1` / `subtitle1`.
#: The touch-up path addresses the same row as `V1` / `A1` (the SOP's
#: label, `library/tools/composed_edit.row_label`), so the two spellings
#: meet here - and an AXI verb handed the SOP label refuses the write
#: with "bad track 'V1' - tracks read video1, audio2, ...".
_AXI_TRACK_KINDS = {"video": "video", "audio": "audio",
                    "subtitle": "subtitle"}


def _axi_track(snapshot: Mapping, unique_id: str) -> tuple[str, int]:
    """The target's track in the AXI's spelling, and its item index."""
    hits = []
    for track in snapshot["tracks"]:
        for index, clip in enumerate(track["clips"]):
            if clip["unique_id"] == unique_id:
                hits.append((str(track.get("type") or ""),
                             int(track.get("index") or 0), index))
    if len(hits) != 1:
        raise ValueError(
            f"target {unique_id!r} resolved to {len(hits)} timeline items")
    track_type, track_index, item_index = hits[0]
    kind = _AXI_TRACK_KINDS.get(track_type.strip().lower())
    if kind is None:
        raise ValueError(
            f"target {unique_id!r} sits on a {track_type!r} row, which the "
            f"AXI cannot address")
    return f"{kind}{track_index}", item_index


def _preservation_verdict(context, operations, before: Mapping,
                          after: Mapping, *, conflict_domains=(),
                          affected_spans=()) -> dict:
    """Judge the preservation invariant over the adapter's two snapshots.

    The adapter read the timeline before its write and again after it.
    Those two reads are the evidence the preservation guarantee is made
    of: the authorized scope says what the edit was told to touch, and
    the difference between the reads says what it actually touched. A
    change the scope did not authorize is collateral - a grade that
    moved, a row that was renamed, a timeline marker that changed - and
    the adapter refuses it rather than reporting it as a success.
    """
    from library.tools import preservation_invariant

    compiled = getattr(context, "compiled", None)
    if not conflict_domains:
        conflict_domains = tuple(
            getattr(compiled, "conflict_domains", ()) or ())
    spans = [tuple(span) for span in affected_spans]
    if not spans:
        spans = [tuple(span) for span in
                 (getattr(compiled, "affected_spans", ()) or ())]
    from library.tools import edit_algebra

    op_spans = []
    try:
        with edit_algebra.indexed_snapshot(before):
            op_spans = [span for span
                        in edit_algebra.spans(list(operations), before)
                        if span is not None]
    except Exception:  # noqa: BLE001 - a span is a refinement, not a gate
        op_spans = []
    for span in op_spans:
        spans.append((int(span[0]), int(span[1])))
    scope = preservation_invariant.scope_for(
        operations, conflict_domains=conflict_domains,
        affected_spans=spans)
    verdict = preservation_invariant.invariant(
        scope=scope, before=before, operations=operations, after=after)
    return verdict.as_dict()


def _refuse_collateral(operation_id: str, preservation: Mapping) -> None:
    """Refuse a write that changed something it was not told to change."""
    if preservation.get("status") != "violated":
        return
    unrelated = preservation.get("unrelated_changes") or []
    named = ", ".join(
        f"{row.get('target')}.{row.get('field')} ({row.get('domain')})"
        for row in unrelated[:5])
    raise RuntimeError(
        f"AXI read-back for {operation_id} changed state the edit was not "
        f"authorized to change: {named}. The edit is refused rather than "
        "committed over approved content.")


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
    id_map = None
    if operation_id == "clip.trim" and before_clip:
        expected_in = int(before_clip["source_in_frame"]) + (
            int(operation["in_frame"]) - int(before_clip["record_in"]))
        expected_out = int(before_clip["source_out_frame"]) - (
            int(before_clip["record_out"]) - int(operation["out_frame"]))
        # The AXI trims by RE-PLACING the item: the trimmed clip is a
        # new item with a new unique id and the original is gone. The
        # read-back therefore finds the target by its IDENTITY - the same
        # media at the trimmed source range - and says which id the
        # target became, so a re-placed item is not read as an unrelated
        # clip that appeared (measured 2026-10-21, the live
        # reel.opening qualification: the trim landed and the read-back
        # could not see it).
        after_clip = next((
            clip for track in after["tracks"] for clip in track["clips"]
            if str(clip.get("source_file") or "")
            == str(before_clip.get("source_file") or "")
            and int(clip["source_in_frame"]) == expected_in
            and int(clip["source_out_frame"]) == expected_out), None)
        if after_clip is not None:
            id_map = {target_id: str(after_clip["unique_id"])}
        evidence.update({
            "target_identity": after_clip is not None,
            "requested_source_in_out": (
                after_clip is not None
                and int(after_clip["source_in_frame"]) == expected_in
                and int(after_clip["source_out_frame"]) == expected_out),
            "requested_record_span": (
                after_clip is not None
                and int(after_clip["record_in"]) == int(operation["in_frame"])
                and int(after_clip["record_out"]) == int(operation["out_frame"])),
        })
        if after_clip is not None:
            evidence["linked_audio_preserved"] = (
                _audio_rows_signature(before, exclude_id=target_id)
                == _audio_rows_signature(after, exclude_id=target_id))
            evidence["approved_edits_preserved"] = (
                _approved_state_signature(before_clip)
                == _approved_state_signature(after_clip))
        operation_matches = all(evidence.values())
    elif operation_id == "clip.delete":
        evidence["target_absent"] = after_clip is None
        operation_matches = after_clip is None
    elif operation_id == "clip.remove" and before_clip is not None:
        evidence.update(_remove_readback_evidence(
            before, after, target_id, before_clip, after_clip))
        operation_matches = all(evidence.values())
    if operation_id == "clip.remove" and before_clip is not None:
        # The removal verifier accounts for every item one-to-one, allowing
        # only the target's explicit link group to disappear and the target
        # track's later items (plus their explicit links) to move by the
        # exact removed duration.
        evidence["unrelated_items_unchanged"] = (
            evidence.get("downstream_continuity_preserved") is True)
    else:
        evidence["unrelated_items_unchanged"] = not _unrelated_changes(
            before, after, target_id, id_map=id_map)
    preservation_operations = [operation]
    for new_id in (id_map or {}).values():
        # The target was RE-PLACED: the trimmed item is a new item and
        # the old one is gone. The scope has to know that the new id is
        # the same target, or the re-place reads as a clip that appeared
        # where none was authorized (measured 2026-10-21, the live
        # reel.opening qualification).
        preservation_operations.append({**operation, "unique_id": new_id})
    preservation = _preservation_verdict(
        context, preservation_operations, before, after)
    _refuse_collateral(operation_id, preservation)
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
        "preservation": preservation,
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": operation_id, "passed": True},),
                "evidence": {"acceptance": acceptance,
                             "preservation": preservation,
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
                       *, affected_spans=(), id_map=None,
                       allowed_target_ids=()) -> list:
    from library.tools.transaction_orchestration import unrelated_clip_changes

    target_ids = list(dict.fromkeys(
        ([target_id] if target_id else []) + list(allowed_target_ids)))
    return unrelated_clip_changes(
        operations=[{"unique_id": unique_id} for unique_id in target_ids],
        affected_spans=affected_spans, id_map=id_map, before=before,
        after=after)


def _audio_rows_signature(snapshot: Mapping, *, exclude_id: str = "") -> tuple:
    """Identity and timing of every non-target audio item in a read-back."""
    rows = []
    for track in snapshot.get("tracks", []):
        if str(track.get("type", "")) != "audio":
            continue
        for clip in track.get("clips", []):
            if str(clip["unique_id"]) == exclude_id:
                continue
            rows.append((str(clip["unique_id"]), int(clip["record_in"]),
                         int(clip["record_out"]),
                         int(clip["source_in_frame"]),
                         int(clip["source_out_frame"]),
                         str(clip.get("source_file", ""))))
    return tuple(sorted(rows))


def _approved_state_signature(clip: Mapping) -> tuple:
    """Stored transform, enabled, color, and flags of one timeline item."""
    transform = clip["transform"]
    if not isinstance(transform, Mapping):
        raise ValueError("clip transform is unreadable after replacement")
    return (tuple(sorted(transform.items())), clip["enabled"],
            clip["clip_color"], tuple(sorted(clip["flags"])))


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
    target_row = (str(before_clip["track_type"]),
                  int(before_clip["track_index"]))
    old_clips = [clip for track in before["tracks"]
                 for clip in track["clips"]]
    new_clips = [clip for track in after["tracks"]
                 for clip in track["clips"]]
    old_by_id = {str(clip["unique_id"]): clip for clip in old_clips}
    new_by_id = {str(clip["unique_id"]): clip for clip in new_clips}

    def link_closure(seed_ids):
        closure = set(seed_ids)
        changed = True
        while changed:
            changed = False
            for clip in old_clips:
                uid = str(clip["unique_id"])
                if uid in closure:
                    added = {str(linked) for linked in clip["linked_unique_ids"]} - closure
                    if added:
                        closure.update(added)
                        changed = True
        return closure

    removed_ids = link_closure({target_id})
    moved_roots = {
        str(clip["unique_id"]) for clip in old_clips
        if (str(clip["track_type"]), int(clip["track_index"])) == target_row
        and int(clip["record_in"]) >= target_out
    }
    moved_ids = link_closure(moved_roots) - removed_ids
    evidence = {"target_absent": after_clip is None
                and removed_ids.isdisjoint(new_by_id)}
    mapping = {}
    matched_after = set()
    downstream_ok = True
    for clip in old_clips:
        uid = str(clip["unique_id"])
        if uid in removed_ids:
            if uid in new_by_id:
                downstream_ok = False
            continue
        moving = uid in moved_ids
        shift = -duration if moving else 0
        expected_in = int(clip["record_in"]) + shift
        expected_out = int(clip["record_out"]) + shift
        candidates = [candidate for candidate in new_clips
                      if (str(candidate["track_type"]),
                          int(candidate["track_index"]))
                      == (str(clip["track_type"]), int(clip["track_index"]))
                      and candidate["source_file"] == clip["source_file"]
                      and int(candidate["source_in_frame"])
                      == int(clip["source_in_frame"])
                    and int(candidate["source_in_frame"])
                    + int(candidate["duration"])
                    == int(clip["source_in_frame"])
                    + int(clip["duration"])
                      and int(candidate["duration"]) == int(clip["duration"])
                      and int(candidate["record_in"]) == expected_in
                      and int(candidate["record_out"]) == expected_out
                      and str(candidate["unique_id"]) not in matched_after]
        if len(candidates) != 1:
            downstream_ok = False
            continue
        candidate = candidates[0]
        candidate_id = str(candidate["unique_id"])
        matched_after.add(candidate_id)
        mapping[uid] = candidate_id
        if not moving and candidate_id != uid:
            downstream_ok = False
    expected_after_count = len(old_clips) - len(removed_ids)
    if len(new_clips) != expected_after_count or len(matched_after) != expected_after_count:
        downstream_ok = False
    for uid in moved_ids:
        mapped = mapping.get(uid)
        if mapped is None:
            downstream_ok = False
            continue
        old_links = {mapping[str(link)] for link in old_by_id[uid]["linked_unique_ids"]
                     if str(link) in mapping}
        new_links = {str(link) for link in new_by_id[mapped]["linked_unique_ids"]}
        if old_links != new_links:
            downstream_ok = False

    target_track = [clip for clip in new_clips
                    if (str(clip["track_type"]), int(clip["track_index"]))
                    == target_row]
    next_old = min((int(clip["record_in"]) for clip in old_clips
                    if (str(clip["track_type"]), int(clip["track_index"]))
                    == target_row and int(clip["record_in"]) >= target_out),
                   default=None)
    evidence["gap_closed"] = (
        next_old is None or any(
            int(clip["record_in"]) == next_old - duration
            for clip in target_track))
    evidence["downstream_continuity_preserved"] = downstream_ok
    return evidence


def _trim(context) -> dict:
    operation = context.operation
    unique_id = str(operation["unique_id"])
    row, index = _axi_target_position_for_context(context, unique_id)
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
    row, index = _axi_target_position_for_context(
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
    """Remove a clip with gap closure scoped to the requested track.

    `clip.remove` means close the gap on the target track. Resolve's
    native ripple delete is timeline-wide, so the guarded AXI command
    performs a local remove plus verified replacements on the target
    track and explicit link groups. Qualification remains disabled until
    live evidence proves continuity and recovery.
    """
    if context.operation.get("close_gap") is False:
        raise ValueError(
            "clip.remove cannot disable gap closure; use clip.delete for "
            "a local deletion")
    row, index = _axi_target_position_for_context(
        context, str(context.operation["unique_id"]))
    return _verified_command(context, [
        "edit", "track-ripple-remove", "--track", row, "--index",
        str(index), *_target_identity_args(
            context, str(context.operation["unique_id"])),
        "--project-folder", _project_folder(context), "--require-recovery",
        "--apply", "--project", context.request.project,
        "--timeline", context.request.timeline,
    ], {**context.operation, "op": "clip.remove"},
       "AXI undo journal entry for the complete track-scoped ripple")


def _caption_cue_timing(props: Mapping) -> tuple:
    """The card's own cue frames - when the card says its words.

    The timing of a caption fix is the thing that must NOT move: the
    card's words start and end on the frames they were measured over.
    """
    cues = props.get("subtitles") or []
    if len(cues) != 1:
        return ()
    cue = cues[0]
    try:
        return (int(cue["startFrame"]), int(cue["endFrame"]))
    except (KeyError, TypeError, ValueError):
        return ()


def _caption_fix(context) -> dict:
    """Rewrite one rendered caption card's text and swap it in place.

    A caption fix is a TEXT change: the card says the wrong words and
    the captain wants the right ones, with the timing, the placement
    and every other caption untouched. A rendered card carries no text
    property - the words are PIXELS - so the text cannot be edited in
    place. The physical work is the caption render path, not a new
    writer: the card's own measured word windows are re-fitted to the
    new words (`library/tools/caption_rewrite.py`), the card is drawn
    again through `subtitles.rerender_swap`, and the new file is
    swapped onto the placement with the swap's per-placement read-back
    and post-check.

    The timing never moves: the cue's own frames ride into the new
    props and are compared before anything touches Resolve, and the
    rendered card's own props - read back off the disk after the
    render - are compared again afterwards, so the pixels on the
    timeline are proved to carry the new text over the old timing.

    Fail-closed notes: a target that is not a rendered caption card on
    the Subtitles row, a card whose render props cannot be read, a new
    text that does not fit the card's measured words, a card that
    already reads the new text, and a stale live identity all refuse
    before mutation.

    The swap replaces the card's MEDIA POOL ITEM, which is
    project-wide: every placement of that card - on every timeline that
    shares it - reads the new text. The receipt records each placement
    the swap touched, so what changed is said out loud.
    """
    import os

    from library.tools import operation_acceptance, resolve_axi_core

    operation = dict(context.operation)
    operation_id = "caption_fix"
    unique_id = str(operation.get("unique_id") or "")
    text = operation.get("text")
    if not unique_id:
        raise ValueError(
            "caption_fix names no unique_id - the card to fix is "
            "required, never inferred")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            f"caption_fix needs the card's new text, got {text!r}")
    new_text = text.strip()

    from library.tools.caption_rewrite import (
        CaptionRewriteError,
        rewrite_props,
    )

    _track, clip = _caption_style_prepared_target(
        context, unique_id, operation_id)
    _caption_style_revalidate_live(context, dict(clip), operation_id)
    project_folder = _project_folder(context)
    old_file = str(clip["source_file"])
    old_props = _caption_style_props(old_file, operation_id)
    try:
        new_props = rewrite_props(old_props, new_text)
    except CaptionRewriteError as exc:
        raise RuntimeError(
            f"caption_fix refused: {exc}") from None
    # A rendered sidecar records the TIGHT canvas (the carrying), while
    # the render entry re-validates props against the project's delivery
    # frame: re-rendering tight dims is refused as a wrong-frame props.
    # Restore the full frame from the project - the renderer re-derives
    # tight itself - using the same reading the validator uses, so the
    # two agree by construction.
    from library.tools.delivery_format import resolve_delivery_format

    full_width, full_height = resolve_delivery_format(project_folder)
    new_props["width"], new_props["height"] = full_width, full_height
    # The fix changes what the card says, never when it says it: the
    # cue's own frames must ride through unchanged.
    old_timing = _caption_cue_timing(old_props)
    new_timing = _caption_cue_timing(new_props)
    if not old_timing or old_timing != new_timing:
        raise RuntimeError(
            f"caption_fix of card {unique_id!r} would move the caption's "
            f"timing from {old_timing} to {new_timing} - refusing a fix "
            "that moves the words off the speech they cover")

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
            f"caption_fix render-and-swap refused: "
            f"{report.get('error') or report}")
    new_file = dict(report.get("map") or {}).get(old_file)
    if not new_file:
        raise RuntimeError(
            f"caption_fix has nothing to do: the re-render produced no "
            f"new file for card {unique_id!r} (already-current: "
            f"{report.get('already_current') or []}) - refusing rather "
            "than claiming a fix")

    after = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    live_hits = [live for track in after["tracks"]
                 for live in track["clips"]
                 if str(live.get("unique_id")) == unique_id]
    # The pixels on disk are the evidence for the TEXT: the renderer
    # writes the props it drew from beside the file it drew. Reading
    # them back proves the new text was drawn over the old timing.
    rendered_props: dict = {}
    try:
        rendered_props = _caption_style_props(str(new_file), operation_id)
    except (RuntimeError, TypeError):
        rendered_props = {}
    rendered_text = ""
    rendered_timing: tuple = ()
    cues = rendered_props.get("subtitles") or []
    if len(cues) == 1:
        rendered_text = str(cues[0].get("text") or "")
        rendered_timing = _caption_cue_timing(rendered_props)

    evidence = {
        "target_identity": len(live_hits) == 1,
        "requested_record_span": (
            len(live_hits) == 1
            and int(live_hits[0]["record_in"]) == int(clip["record_in"])
            and int(live_hits[0]["record_out"]) == int(clip["record_out"])),
        "caption_text_applied": (
            bool(new_file) and os.path.isfile(new_file)
            and len(live_hits) == 1
            and str(live_hits[0].get("source_file")) == str(new_file)
            and rendered_text == new_text),
        "caption_timing_preserved": (
            len(live_hits) == 1
            and int(live_hits[0]["record_in"]) == int(clip["record_in"])
            and int(live_hits[0]["record_out"]) == int(clip["record_out"])
            and int(live_hits[0]["source_in_frame"])
            == int(clip["source_in_frame"])
            and int(live_hits[0]["source_out_frame"])
            == int(clip["source_out_frame"])
            and rendered_timing == old_timing),
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
                      "previous_file": old_file, "new_file": new_file,
                      "previous_text": str(
                          (old_props.get("subtitles") or [{}])[0].get("text")
                          or ""),
                      "applied_text": rendered_text},
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
                          "disk as superseded generations; caption_fix "
                          "carries the previous text)",
                "reference": {
                    "kind": "caption_swap_back",
                    "project_folder": project_folder,
                    "unique_id": unique_id,
                    "previous_file": old_file,
                    "current_file": new_file,
                    "previous_props": old_props,
                },
            },
        },
    }


def _speech_row_signatures(snapshot: Mapping,
                           cover_track_index: int | None = None) -> list:
    """Every non-cover video row and every audio row, by clip identity."""
    rows = []
    for track in snapshot.get("tracks", []):
        track_type = str(track.get("type", ""))
        try:
            track_index = int(track.get("index", 0))
        except (TypeError, ValueError):
            continue
        if not (track_type == "audio"
                or (track_type == "video"
                    and track_index != cover_track_index)):
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
    the top video layer with continuous picture beneath it. Every other
    video and audio row is re-read and preserved, regardless of track
    numbering, so the selected row cannot silently be assumed to be V2
    or the underlying picture assumed to be V1. The
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
    prepared_video_tracks = [track for track in before.get("tracks", [])
                             if str(track.get("type")) == "video"]
    prepared_indexes = [int(track["index"])
                        for track in prepared_video_tracks]
    if not prepared_indexes or track_index != max(prepared_indexes):
        raise ValueError(
            "broll.insert cover must use the topmost video track so the "
            "placed picture cannot be hidden by a higher video layer")
    prepared_cover = [track for track in prepared_video_tracks
                      if int(track["index"]) == track_index]
    if len(prepared_cover) != 1:
        raise ValueError(
            f"broll.insert cover track V{track_index} is absent from "
            "the prepared timeline - create the row before covering it")
    if any(int(clip["record_in"]) < record_out
           and record_in < int(clip["record_out"])
           for clip in prepared_cover[0].get("clips") or []):
        raise ValueError(
            f"broll.insert cover span {record_in}..{record_out} is "
            f"occupied on V{track_index} - refusing rather than "
            "stacking a second cover")
    live_before = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    live_tracks = live_before.get("tracks", [])
    video_tracks = [track for track in live_tracks
                    if str(track.get("type")) == "video"]
    video_indexes = [int(track["index"]) for track in video_tracks]
    if not video_indexes or track_index != max(video_indexes):
        raise ValueError(
            "broll.insert cover must use the topmost video track so the "
            "placed picture cannot be hidden by a higher video layer")
    cover_tracks = [track for track in video_tracks
                    if int(track["index"]) == track_index]
    if len(cover_tracks) != 1:
        raise ValueError(
            f"broll.insert cover track V{track_index} is absent from "
            "the live timeline - create the row before covering it")
    underlay_spans = sorted(
        (int(clip["record_in"]), int(clip["record_out"]))
        for track in video_tracks if int(track["index"]) < track_index
        for clip in (track.get("clips") or [])
        if int(clip["record_in"]) < record_out
        and record_in < int(clip["record_out"]))
    covered_until = record_in
    for start, end in underlay_spans:
        if start > covered_until:
            break
        covered_until = max(covered_until, end)
        if covered_until >= record_out:
            break
    if covered_until < record_out:
        raise ValueError(
            "broll.insert requires continuous picture beneath the cover "
            "span; the lower video tracks do not cover the full range")
    for clip in cover_tracks[0].get("clips") or []:
        if int(clip["record_in"]) < record_out \
                and record_in < int(clip["record_out"]):
            raise ValueError(
                f"broll.insert cover span {record_in}..{record_out} is "
                f"occupied on V{track_index} by "
                f"{clip.get('unique_id')!r} - refusing rather than "
                "stacking a second cover")
    if (_speech_row_signatures(live_before, track_index)
            != _speech_row_signatures(before, track_index)):
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
    speech_preserved = (
        _speech_row_signatures(after, track_index)
        == _speech_row_signatures(before, track_index))
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
    preservation = _preservation_verdict(
        context, [operation], before, after,
        affected_spans=[(record_in, record_out)])
    _refuse_collateral("broll.insert", preservation)
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
        "preservation": preservation,
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


def _place(context) -> dict:
    """Place a pool clip at a stated span through the journaled AXI verb.

    The physical write is the AXI `edit place` verb - the guarded,
    journaled AppendToTimeline placement - and the read-back is a fresh
    live snapshot: exactly one new item, on the requested row, at the
    requested record span, carrying the requested source range, with
    every other item on the timeline unchanged. The op names the pool
    item by its unique id, which is the identity the algebra carries;
    the verb resolves that id to the same item its name would have
    found, so the two can never disagree about what was placed.

    Qualification stays closed until live evidence lands: this is an
    IMPLEMENTED path, not a qualified one.
    """
    import re

    from library.tools import operation_acceptance, resolve_axi_core

    operation = dict(context.operation)
    track_type = str(operation.get("track_type") or "video")
    if track_type != "video":
        raise ValueError(
            f"clip.insert refuses {track_type!r}: the AXI place verb "
            "places video or audio media, and this request names neither")
    try:
        track_index = int(operation["track_index"])
        record_in = int(operation["record_in"])
        record_out = int(operation["record_out"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"clip.insert needs integer track/record spans ({exc})")
    if record_out <= record_in:
        raise ValueError("clip.insert needs a positive record span")
    media_pool_item = str(operation.get("media_pool_item") or "")
    if not media_pool_item:
        raise ValueError("clip.insert names no pool item to place")

    before = context.snapshot
    command = ["edit", "place", "--clip-id", media_pool_item,
               "--media", "video",
               "--track", str(track_index), "--record", str(record_in)]
    if operation.get("source_in") is not None:
        command += ["--source-in", str(int(operation["source_in"]))]
    if operation.get("source_out") is not None:
        command += ["--source-out", str(int(operation["source_out"]))]
    command += ["--project-folder", _project_folder(context),
                "--require-recovery", "--apply",
                "--project", context.request.project,
                "--timeline", context.request.timeline]
    result = resolve_axi_core.execute(command)
    if not result.ok:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or "AXI edit place for clip.insert failed")
    after = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    before_uids = {str(clip.get("unique_id"))
                   for track in before.get("tracks", [])
                   for clip in track.get("clips") or []}
    placed = [clip for track in after.get("tracks", [])
              for clip in track.get("clips") or []
              if str(clip.get("unique_id")) not in before_uids]
    if len(placed) != 1:
        raise RuntimeError(
            f"clip.insert placed {len(placed)} items, not exactly one - "
            "verify by hand before retrying")
    item = placed[0]
    placed_uid = str(item.get("unique_id"))
    requested_source_in = operation.get("source_in")
    evidence = {
        "target_identity": True,
        "source_identity": (
            bool(str(item.get("source_file") or ""))
            and (requested_source_in is None
                 or int(item.get("source_in_frame") or -1)
                 == int(requested_source_in))),
        "requested_record_span": (
            int(item.get("record_in")) == record_in
            and int(item.get("record_out")) == record_out),
        "requested_track": (
            str(item.get("track_type") or "video") == "video"
            and int(item.get("track_index") or 0) == track_index),
    }
    collateral = _unrelated_changes(
        before, after, placed_uid,
        affected_spans=[(record_in, record_out)])
    evidence["unrelated_items_unchanged"] = not collateral
    preservation = _preservation_verdict(
        context, [operation], before, after,
        affected_spans=[(record_in, record_out)])
    _refuse_collateral("clip.insert", preservation)
    acceptance = operation_acceptance.evaluate(
        {**operation, "op": "clip.insert"},
        readback_passed=all(evidence.values()),
        evidence=evidence)
    if not acceptance["passed"]:
        missing = ", ".join(acceptance["missing"]) or "none"
        failed = ", ".join(acceptance["failed"]) or "none"
        raise RuntimeError(
            f"AXI read-back acceptance failed for clip.insert: "
            f"missing evidence [{missing}], failed checks [{failed}]")
    journal_match = re.search(r"(?m)^\s*entry:\s*(\S+)\s*$",
                              result.stdout)
    if not journal_match:
        raise RuntimeError(
            "clip.insert verified without a recovery journal reference")
    recovery_reference = {
        "kind": "undo_journal_entry",
        "project_folder": _project_folder(context),
        "entry_id": journal_match.group(1),
    }
    return {
        "status": "committed",
        "backend": "resolve_axi",
        "mutated": True,
        "operation_id": "clip.insert",
        "request_id": context.request.id,
        "operation_index": int(getattr(context, "operation_index", 0)),
        "target_identity": {
            "unique_id": placed_uid,
            "client_unique_id": str(operation.get("unique_id") or ""),
            "media_pool_item": media_pool_item,
            "source_file": str(item.get("source_file", "")),
            "source_in_frame": int(item.get("source_in_frame") or 0),
            "source_out_frame": int(item.get("source_out_frame") or 0),
            "record_in": int(item.get("record_in") or 0),
            "record_out": int(item.get("record_out") or 0),
        },
        "readback": {"snapshot": after, "command_output": result.stdout},
        "acceptance_contracts": [acceptance],
        "preservation": preservation,
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": "clip.insert", "passed": True},),
                "evidence": {"acceptance": acceptance,
                             "snapshot_source": "fresh_live_timeline"},
            },
            "recovery": {
                "status": "available",
                "action": ("AXI undo journal; delete the placed item to "
                           "restore the timeline"),
                "reference": recovery_reference,
            },
        },
    }


def _caption_style_props(source_file: str, op: str = "caption.style") -> dict:
    """The tight render props written beside a placed caption card.

    The same layout `caption_width._props` reads: `<stem>_props.json`
    beside the card carries the bounds, words and timings the card was
    drawn from. Without them a re-render would have to guess what the
    card says or when it plays, so an unreadable sidecar refuses.

    `op` names the operation asking, so the refusal says which write
    was refused rather than naming a different one.
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
            f"{op} cannot re-render "
            f"{os.path.basename(str(source_file))!r}: no readable render "
            f"props beside it ({path}: {exc}) - refusing to guess bounds, "
            f"text or timing") from None
    if not isinstance(props, dict):
        raise TypeError(
            f"{op} cannot re-render "
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


def _caption_style_prepared_target(context, unique_id: str,
                                   op: str = "caption.style") -> tuple:
    """The prepared caption clip and its track, resolved exactly once.

    A rendered caption card is the medium every Ren reel uses: a card
    on the Subtitles row, playing a file the caption renderer drew.
    `op` names the operation asking, so a refusal says which write was
    refused.
    """
    from library.tools.caption_swap import SUBTITLES_TRACK_NAME

    hits = [(track, clip)
            for track in context.snapshot["tracks"]
            for clip in track["clips"]
            if str(clip.get("unique_id")) == unique_id]
    if len(hits) != 1:
        raise ValueError(
            f"{op} target {unique_id!r} resolved to "
            f"{len(hits)} prepared items")
    track, clip = hits[0]
    if track.get("name") != SUBTITLES_TRACK_NAME:
        raise ValueError(
            f"{op} target {unique_id!r} sits on "
            f"{track.get('name')!r}, not the {SUBTITLES_TRACK_NAME!r} "
            f"row - a caption op on a non-caption is a mislabelled write")
    if not clip.get("source_file"):
        raise ValueError(
            f"caption.style target {unique_id!r} has no source file - "
            f"there is no placed card to re-render")
    return track, clip


def _caption_style_revalidate_live(context, clip: dict,
                                  op: str = "caption.style") -> dict:
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
            f"{op} target {unique_id!r} is stale: it resolves "
            f"to {len(matches)} live items, not one - nothing was rendered")
    live = matches[0]
    for key in ("source_file", "source_in_frame", "source_out_frame",
                "record_in", "record_out"):
        if str(live.get(key)) != str(clip.get(key)):
            raise RuntimeError(
                f"{op} target {unique_id!r} is stale: live "
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
    compiled = getattr(context, "compiled", None)
    prior_operations = (compiled.operations[:int(
        getattr(context, "operation_index", 0))]
                        if compiled is not None else ())
    prior_caption_ids = [
        str(prior["unique_id"])
        for prior in prior_operations
        if prior["op"] == operation_id]
    prior_caption_files = {
        str(candidate["source_file"])
        for track in context.snapshot["tracks"]
        for candidate in track["clips"]
        if str(candidate["unique_id"]) in prior_caption_ids}
    if old_file in prior_caption_files:
        raise RuntimeError(
            f"caption.style batch addresses multiple placements of the "
            f"same rendered caption file {os.path.basename(old_file)!r}; "
            "one pool-item swap already covers them, so duplicate writes "
            "are refused")
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
    # A rendered sidecar records the TIGHT canvas (the carrying), while
    # the render entry re-validates props against the project's delivery
    # frame: re-rendering tight dims is refused as a wrong-frame props.
    # Restore the full frame from the project - the renderer re-derives
    # tight itself - using the same reading the validator uses, so the
    # two agree by construction. Full-canvas cards already carry these
    # dims and are unchanged by the restore.
    from library.tools.delivery_format import resolve_delivery_format

    full_width, full_height = resolve_delivery_format(project_folder)
    new_props["width"], new_props["height"] = full_width, full_height
    restyle_kept = ("style", "width", "height")
    if ({key: new_props[key] for key in new_props
            if key not in restyle_kept} !=
            {key: old_props[key] for key in old_props
                if key not in restyle_kept}):
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
    unrelated = _unrelated_changes(
        context.snapshot, after, unique_id,
        allowed_target_ids=prior_caption_ids)
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
                    "previous_props": old_props,
                },
            },
        },
    }


def _audio_gain(context) -> dict:
    raise RuntimeError(
        "audio.gain is refused before mutation: the candidate receipt lacks "
        "independent audio-render evidence for levels, clipping, and silence")


def _multicam_select_angle(context) -> dict:
    from library.tools import operation_acceptance, resolve_axi_core

    operation = context.operation
    unique_id = str(operation.get("unique_id") or "")
    if not unique_id:
        raise ValueError("multicam.select_angle needs a target unique_id")
    required = ("speaker_id", "assignment_basis", "source_file",
                "source_in_frame", "source_out_frame", "sync_evidence_id")
    missing = [key for key in required if operation.get(key) is None]
    if missing:
        raise ValueError(
            "multicam.select_angle needs a resolved speaker assignment and "
            f"synced camera source range; missing {missing} - nothing was "
            "written")
    empty_identity = [key for key in ("speaker_id", "sync_evidence_id")
                      if not str(operation[key]).strip()]
    if empty_identity:
        raise ValueError(
            "multicam.select_angle needs non-empty speaker and sync "
            f"evidence identities, got empty {empty_identity} - nothing "
            "was written")
    if operation["assignment_basis"] not in {
            "project_declaration", "diarized_speaker_identity"}:
        raise ValueError(
            "multicam.select_angle assignment_basis must name a project "
            "declaration or diarized speaker identity - nothing was written")
    try:
        source_in = int(operation["source_in_frame"])
        source_out = int(operation["source_out_frame"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "multicam.select_angle synced source range must use integer "
            f"frames ({exc}) - nothing was written") from exc
    source_file = str(operation["source_file"] or "")
    if not source_file or source_in < 0 or source_out < source_in:
        raise ValueError(
            "multicam.select_angle needs a valid assigned camera source "
            "range - nothing was written")

    # Revalidate the exact target before dispatch. The command performs a
    # journaled, place-then-delete replacement and restores the prior
    # timeline snapshot if the new placement cannot be verified.
    track, index = _axi_target_position_for_context(context, unique_id)
    identity_args = _target_identity_args(context, unique_id)
    command = [
        "edit", "switch-camera", "--project", context.request.project,
        "--timeline", context.request.timeline,
        "--track", track, "--index", str(index), *identity_args,
        "--camera-source-file", source_file,
        "--speaker-id", str(operation["speaker_id"]),
        "--assignment-basis", str(operation["assignment_basis"]),
        "--sync-evidence-id", str(operation["sync_evidence_id"]),
        "--source-in", str(source_in), "--source-out", str(source_out),
        "--project-folder", _project_folder(context), "--require-recovery",
        "--apply",
    ]
    result = resolve_axi_core.execute(command)
    if not result.ok:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or "multicam.select_angle camera switch failed")
    after = resolve_axi_core.read_live_timeline(
        context.request.project, context.request.timeline)
    before_matches = [(row, clip) for row in context.snapshot["tracks"]
                      for clip in row["clips"]
                      if str(clip["unique_id"]) == unique_id]
    if len(before_matches) != 1:
        raise ValueError(
            f"multicam.select_angle target {unique_id!r} no longer resolves "
            "uniquely in the prepared snapshot")
    before_track, before_clip = before_matches[0]
    after_hits = [clip for row in after["tracks"]
                  if row["type"] == before_track["type"]
                  and int(row["index"]) == int(before_track["index"])
                  for clip in row["clips"]
                  if (str(clip["source_file"]) == source_file
                      and int(clip["source_in_frame"]) == source_in
                      and int(clip["source_out_frame"]) == source_out
                      and int(clip["record_in"]) == int(before_clip["record_in"])
                      and int(clip["record_out"]) == int(before_clip["record_out"]))]
    after_clip = after_hits[0] if len(after_hits) == 1 else None
    evidence = {
        "target_identity": (after_clip is not None
                            and str(after_clip["unique_id"]) != unique_id),
        "requested_camera_source_selected": (
            after_clip is not None
            and str(after_clip["source_file"]) == source_file
            and int(after_clip["source_in_frame"]) == source_in
            and int(after_clip["source_out_frame"]) == source_out),
        "record_span_preserved": (
            after_clip is not None
            and int(after_clip["record_in"]) == int(before_clip["record_in"])
            and int(after_clip["record_out"]) == int(before_clip["record_out"])),
        "unrelated_items_unchanged": not _unrelated_changes(
            context.snapshot, after, unique_id,
            id_map=({unique_id: str(after_clip["unique_id"])}
                    if after_clip is not None else None)),
    }
    acceptance = operation_acceptance.evaluate(
        operation, readback_passed=all(evidence.values()), evidence=evidence)
    if not acceptance["passed"]:
        missing_checks = ", ".join(acceptance["missing"]) or "none"
        failed = ", ".join(acceptance["failed"]) or "none"
        raise RuntimeError(
            "AXI read-back acceptance failed for multicam.select_angle: "
            f"missing evidence [{missing_checks}], failed checks [{failed}]")
    journal_id = next((line.split(":", 1)[1].strip()
                       for line in result.stdout.splitlines()
                       if line.strip().startswith("entry:")), "")
    if not journal_id:
        raise RuntimeError(
            "multicam.select_angle verified without an undo journal "
            "reference")
    receipt = {
        "status": "committed", "backend": "resolve_axi", "mutated": True,
        "operation_id": "multicam.select_angle",
        "request_id": context.request.id,
        "operation_index": int(getattr(context, "operation_index", 0)),
        "target_identity": {"unique_id": unique_id,
                            "replacement_unique_id": (
                                str(after_clip["unique_id"])
                                if after_clip is not None else ""),
                            "speaker_id": str(operation["speaker_id"]),
                            "source_file": source_file},
        "readback": {"snapshot": after},
        "acceptance_contracts": [acceptance],
        "recovery": {"action": "ren undo the journaled camera switch",
                     "reference": {"kind": "undo_journal_entry",
                                   "project_folder": _project_folder(context),
                                   "entry_id": journal_id}},
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": "multicam.select_angle",
                            "passed": True},),
                "evidence": {"acceptance": acceptance,
                             "snapshot_source": "fresh_live_timeline"}},
            "recovery": {"status": "available",
                         "action": "ren undo the journaled camera switch",
                         "reference": {"kind": "undo_journal_entry",
                                       "project_folder": _project_folder(context),
                                       "entry_id": journal_id}},
        },
    }
    return receipt


def _reel_opening_decided(operation: Mapping) -> tuple[str, list[dict]]:
    """The validated brief and structural edits a reel.opening carries.

    The model decides the edits; this adapter never invents trim points.
    Gap-closing removals are carried as `clip.remove`, which has its own
    guarded AXI executor and continuity acceptance. Local deletes are not
    accepted because they leave a gap in the opening.
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
               if str(edit.get("op")) == "clip.remove"}
    ripple_rows = {}
    for clip in old.values():
        if any(str(edit.get("op")) == "clip.remove"
               and str(edit.get("unique_id")) == str(clip["unique_id"])
               for edit in ordered):
            row = (int(clip["track_index"]), str(clip["track_type"]))
            ripple_rows[row] = min(
                int(clip["record_in"]), ripple_rows.get(row, int(clip["record_in"])))
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
        row = (int(clip["track_index"]), str(clip["track_type"]))
        if row in ripple_rows and int(clip["record_in"]) > ripple_rows[row]:
            # The clip.remove executor's continuity acceptance proves
            # the expected ripple offset; this aggregate check still
            # requires the retained clip's source and editorial identity.
            keys = tuple(key for key in keys
                         if key not in {"record_in", "record_out"})
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

    executors = {"clip.trim": _trim, "clip.remove": _remove}
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
        preservation = _preservation_verdict(
            context, ordered, before, live,
            affected_spans=[tuple(span) for span in compiled.affected_spans])
        _refuse_collateral("reel.opening", preservation)
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
        # The request names the RESOLVE project (the broker's
        # open-project check and the shadow store key on that), so the
        # Ren project FOLDER is found by its Resolve name. A slug still
        # resolves by slug.
        folder = project_registry.project_folder_for_resolve_name(
            request.project)
        if folder is None:
            project = project_registry.get_project(request.project)
            folder = str(project.project_root)
        project_folder = folder
    else:
        project_folder = str(project_path.parent)
    proposal = reel_proposal.read_proposal(
        str(reel_proposal.proposal_path(project_folder)))
    matches = [moment for moment in proposal
               if moment.timeline_name == request.timeline]
    if not matches:
        # A scratch timeline may be renamed to disambiguate it from another
        # Resolve timeline with the same display name. Its reel number still
        # identifies the one proposal entry that owns the selected reel.
        numbers = {int(number) for number in re.findall(
            r"\breel\s+0*(\d+)\b", str(request.timeline), re.IGNORECASE)}
        if len(numbers) == 1:
            reel_number = next(iter(numbers))
            matches = [moment for moment in proposal
                       if int(moment.number) == reel_number]
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
            "recovery": {
                "status": "recorded",
                "action": "undo the touch-up journal entry and verify its read-back",
                "reference": {
                    "kind": "undo_journal_entry",
                    "project_folder": _project_folder(context),
                    "entry_id": str(receipt["journal"]),
                },
            },
        },
    }


def register_builtin_edit_executors() -> None:
    """Bind supported P0 operations to their existing AXI executors."""
    from library.tools import resolve_axi_core

    desired = {
        ("clip.trim", "resolve_axi"): _trim,
        ("clip.delete", "resolve_axi"): _delete,
        ("clip.remove", "resolve_axi"): _remove,
        ("clip.insert", "resolve_axi"): _place,
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
