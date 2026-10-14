"""Route a feedback note to a patch, or leave it for the rebuild.

Slice 3 of the feedback-patch-first work: the wiring. `handle_note`
takes one routed note and the fix its author computed (`operations` +
`scope`, caller-supplied the way `reel.touchup` is caller-supplied):

* verdict rebuild -> `{"action": "rebuild"}` and nothing else is
  touched. The note reaches planning through the existing prompt path
  (`gather_step_inputs` hands `prompt_block` to the owning step) exactly
  as today.
* verdict patch -> the fix applies through the guarded patch path, the
  verified patch is recorded (`marker_resolution.resolve_note` with the
  `feedback_patch_holds` check, WITHOUT live handles so the captain's
  blue marker stays), and the green reply marker is written AFTER that
  verified record. The next routing holds the note back from planning
  (`BASIS_ANSWERED`, re-measured off the shadow store) - so a patched
  note never triggers a rebuild. A refusal or a failed verification
  records nothing and writes no reply: the note stays open.

The classifier (`feedback_patch.decide`) and the applier
(`feedback_patch_apply.guarded_apply`) arrive as separate slices, so
both are injected for tests and lazily imported as defaults - this
module takes plain dicts and never imports either at module scope.

`ren revise` (`library/tools/revise.py`) is the caller: it generates
the operations and the scope from the note's typed timeline program and
the reel's recorded generation. `python3 -m
library.tools.feedback_patch_flow apply --project <folder>
--timeline <exact name> --note <note_id> --operations @ops.json --scope
@scope.json --idempotency-key <key>` is the hand-fed operator entry: it routes
the project's notes, finds the note, and handles it. Live Resolve only;
read-only until the guarded apply runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

ACTION_PATCHED = "patched"
ACTION_REBUILD = "rebuild"
ACTION_REFUSED = "refused"


class FlowNotReady(RuntimeError):
    """A sibling slice this flow calls on has not merged yet."""


def _default_decide(routed_note: dict) -> dict:
    try:
        from library.tools import feedback_patch
    except ImportError as exc:
        raise FlowNotReady(
            "feedback_patch (slice 1) has not merged - inject `decide` "
            "for now") from exc
    return feedback_patch.decide(routed_note)


def _default_apply(*, project, timeline, operations, scope: dict,
                   idempotency_key: str, store=None,
                   project_folder=None) -> dict:
    try:
        from library.tools import feedback_patch_apply
    except ImportError as exc:
        raise FlowNotReady(
            "feedback_patch_apply (slice 2) has not merged - inject "
            "`apply_patch` for now") from exc
    return feedback_patch_apply.guarded_apply(
        project=project, timeline=timeline, operations=operations,
        scope=scope, idempotency_key=idempotency_key, store=store,
        project_folder=project_folder)


def _note_text(note: dict) -> str:
    text = (note or {}).get("text") or ""
    if text:
        return str(text)
    return "\n\n".join(
        part for part in (str((note or {}).get("name") or ""),
                          str((note or {}).get("note") or "")) if part)


def _change_summary(operations: list) -> str:
    """Describe the supported patch operations in words a reviewer can scan."""
    summaries = []
    for operation in operations:
        kind = str(operation.get("op") or "")
        frame = operation.get("frame")
        unique_id = str(operation.get("unique_id") or "")
        if kind == "marker.add":
            summaries.append(f"Added a timeline marker at frame {frame}.")
        elif kind == "marker.delete":
            summaries.append(f"Removed a timeline marker at frame {frame}.")
        elif kind == "clip_marker.add":
            summaries.append(
                f"Added a marker at source frame {frame} on clip {unique_id}.")
        elif kind == "clip_marker.delete":
            summaries.append(
                f"Removed a marker at source frame {frame} on clip {unique_id}.")
        elif kind == "clip.set_property":
            prop = str(operation.get("property") or "property")
            summaries.append(
                f"Set {prop} on clip {unique_id} to "
                f"{operation.get('value')!r}.")
        elif kind == "clip.set_enabled":
            state = "enabled" if operation.get("enabled") else "disabled"
            summaries.append(f"{state.capitalize()} clip {unique_id}.")
        else:
            summaries.append(f"Applied {kind or 'an edit'}.")
    return " ".join(summaries) or "No patch operations were supplied."


def default_reply_writer(*, project, timeline, routed_note: dict,
                         report: str, summary: str,
                         idempotency_key: str) -> dict:
    """The green reply marker, beside the note, read back.

    A moment note is answered on the timeline at its own frame PLUS ONE;
    a clip note on the same clip at its source frame plus one. That is
    the measured convention (blue 1247 -> green 1248; blue 1711 -> green
    1712): the reply sits next to the question, never on it, because a
    frame already carrying a marker refuses the write. An occupied reply
    frame is refused with the reason rather than overwritten - the
    captain's markers are never edited. The marker states it is ours and
    what it answers (`reply_custom_data`); the write is judged by the
    read-back, never by the return.
    """
    from library.tools import feedback_ledger, marker_feedback

    timeline_name = timeline.GetName()
    words = _note_text(routed_note)
    identity = feedback_ledger.durable_identity(timeline_name, words)
    custom_data = marker_feedback.reply_custom_data(
        answers=identity, answers_text=words, summary=summary)
    name = f"reply: {report.splitlines()[0][:80] if report else 'patched'}"
    target = (routed_note or {}).get("target") or {}
    if target.get("kind") == "clip" and (target.get("clip") or {}).get(
            "unique_id"):
        item = _find_item(timeline,
                          str(target["clip"]["unique_id"]))
        if item is None:
            raise FlowNotReady(
                f"the note's clip {target['clip']['unique_id']!r} is not "
                f"on {timeline_name!r} any more - the timeline moved "
                f"under the patch")
        key = int(routed_note["frame_in_timeline_space"]) + 1
        taken = {int(raw) for raw in (item.GetMarkers() or {})}
        if key in taken:
            raise FlowNotReady(
                f"source frame {key} on {target['clip']['unique_id']!r} "
                f"already carries a marker - the green reply refuses to "
                f"overwrite it")
        return marker_feedback.place_reply_clip_marker(
            item, key, "Green", name, report,
            custom_data=custom_data, project=project,
            idempotency_key=idempotency_key)
    key = int(routed_note["frame"]) + 1
    start_frame = int(timeline.GetStartFrame())
    taken = {int(raw) for raw in (timeline.GetMarkers() or {})}
    if key - start_frame in taken:
        raise FlowNotReady(
            f"frame {key} on {timeline_name!r} already carries a marker - "
            f"the green reply refuses to overwrite it")
    return marker_feedback.place_reply_marker(
        timeline, key, "Green", name, report,
        custom_data=custom_data, project=project,
        idempotency_key=idempotency_key)


def _find_item(timeline, unique_id: str):
    """The timeline item carrying `unique_id`, or None. Read, never guess."""
    for track_type in ("video", "audio"):
        try:
            count = int(timeline.GetTrackCount(track_type) or 0)
        except Exception:  # noqa: BLE001 - an unreadable track is skipped
            continue
        for index in range(1, count + 1):
            try:
                items = timeline.GetItemListInTrack(track_type, index) or []
            except Exception:  # noqa: BLE001 - same
                continue
            for item in items:
                try:
                    if str(item.GetUniqueId() or "") == unique_id:
                        return item
                except Exception:  # noqa: BLE001 - same
                    continue
    return None


def handle_note(*, routed_note: dict, project_folder: str,
                operations: list, scope: dict, idempotency_key: str,
                project=None, timeline=None, decide=None,
                apply_patch=None, record=None,
                write_reply=None, record_outcome=None,
                change_summary: str = "") -> dict:
    """Patch this note in place, or hand it back for the rebuild.

    `operations`/`scope` are the fix the author computed; `project` and
    `timeline` are the live Resolve handles the patch writes through
    (required only when the verdict is a patch). `change_summary` is the
    reviewer-facing account of the change when the caller can name more
    than the operations do (the region they land in). Returns
    `{"action": "patched", ...}`, `{"action": "rebuild", ...}` or, for a
    program nothing can execute, `{"action": "refused", ...}`.
    """
    from library.tools import marker_resolution, feedback_outcomes

    note = dict(routed_note or {})
    outcome_id = uuid.uuid4().hex
    outcome_writer = record_outcome or feedback_outcomes
    try:
        verdict = (decide or _default_decide)(note)
    except Exception as exc:
        outcome_writer.record_blocked(
            project_folder, note, reason=f"could not classify the note: {exc}",
            action_kind="blocked", outcome_id=outcome_id)
        raise
    if not isinstance(verdict, dict):
        reason = "the classifier returned no usable decision"
        outcome_writer.record_blocked(
            project_folder, note, reason=reason, action_kind="blocked",
            outcome_id=outcome_id)
        raise FlowNotReady(reason)
    if verdict.get("verdict") == ACTION_REFUSED:
        reason = str(verdict.get("reason", ""))
        outcome = outcome_writer.record_blocked(
            project_folder, note, reason=reason, action_kind="blocked",
            outcome_id=outcome_id)
        return {"action": ACTION_REFUSED, "reason": reason,
                "outcome": outcome}
    if verdict.get("verdict") != "patch":
        reason = str(verdict.get("reason", ""))
        outcome = outcome_writer.record_plan(
            project_folder, note, action_kind="rebuild",
            summary=reason or "The note will be handled in the next rebuild.",
            outcome_id=outcome_id)
        return {"action": ACTION_REBUILD, "reason": reason,
                "outcome": outcome}
    if project is None or timeline is None:
        reason = ("a patch verdict needs the live Resolve handles; the "
                  "patch did not run")
        outcome_writer.record_blocked(
            project_folder, note, reason=reason, action_kind="patch",
            outcome_id=outcome_id)
        raise FlowNotReady(reason)
    if not idempotency_key:
        reason = "a patch needs a non-empty idempotency key"
        outcome_writer.record_blocked(
            project_folder, note, reason=reason, action_kind="patch",
            outcome_id=outcome_id)
        raise FlowNotReady(reason)
    try:
        receipt = (apply_patch or _default_apply)(
            project=project, timeline=timeline, operations=operations,
            scope=scope, idempotency_key=idempotency_key,
            project_folder=project_folder)
    except Exception as exc:
        outcome_writer.record_blocked(
            project_folder, note, reason=f"patch did not complete: {exc}",
            action_kind="patch", outcome_id=outcome_id)
        raise

    from library.tools.timeline_shadow import snapshot_hash
    from library.tools import timeline_shadow as shadow

    patch_generation = receipt["patch"].get("generation")
    version_refs = {
        "before": {"generation": receipt["backup"].get("generation"),
                   "snapshot_hash": receipt["backup"].get("hash")},
        "after": {"generation": patch_generation},
        "reason": ("timeline-shadow generations record this in-place patch; "
                   "no promoted reel build was run"),
    }
    try:
        project_name = project.GetName()
        _, timeline_id, _ = shadow.timeline_key(project, timeline)
        post_hash = snapshot_hash(shadow.read_live(project, timeline))
    except Exception as exc:
        outcome_writer.record_blocked(
            project_folder, note,
            reason=f"the patch applied but timeline read-back failed: {exc}",
            action_kind="patch", outcome_id=outcome_id,
            summary=("The patch was applied, but the timeline read-back "
                     "failed; completion is unverified."),
            reel_versions=version_refs)
        raise
    measured = {"resolve_project": project_name,
                "timeline_id": timeline_id,
                "post_hash": post_hash, "head_hash": post_hash}
    version_refs["after"] = {
        "generation": patch_generation,
        "snapshot_hash": post_hash,
    }
    outcome_details = {
        "action_kind": "patch",
        "change_summary": change_summary or _change_summary(operations),
        "verification_state": "timeline_readback",
        "reel_versions": version_refs,
    }
    record_out = (record or marker_resolution.resolve_note)(
        project_folder, note,
        action=f"patched in place ({receipt['patch'].get('status')})",
        rationale=str(verdict.get("reason", "")),
        check=marker_resolution.CHECK_FEEDBACK_PATCH, measured=measured,
        verifier="feedback_patch_flow.handle_note",
        timeline=timeline, resolve_project=project,
        outcome_details=outcome_details, outcome_id=outcome_id)
    record_doc = record_out.get("record", record_out)
    if record_doc.get("status") != "resolved_verified":
        raise FlowNotReady(
            f"the patch check did not verify - no green reply is written "
            f"for an unverified patch: {record_doc.get('status')}")
    reply = (write_reply or default_reply_writer)(
        project=project, timeline=timeline, routed_note=note,
        report=(f"patched in place: {verdict.get('reason', '')} "
                f"(backup generation {receipt['backup']['generation']})"),
        summary=f"patched in place: {verdict.get('reason', '')}",
        idempotency_key=f"{idempotency_key}:reply")
    return {"action": ACTION_PATCHED, "verdict": verdict, "receipt": receipt,
            "record": record_doc, "reply": reply}


def _load_json_maybe_at(path: str):
    text = path[1:] if path.startswith("@") else path
    candidate = Path(text)
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.feedback_patch_flow",
        description="Patch one routed feedback note in place, or hand it "
                    "back for the rebuild.")
    parser.add_argument("command", choices=("apply",),
                        help="apply: handle one routed note by its note_id")
    parser.add_argument("--project", required=True,
                        help="path to the project folder")
    parser.add_argument("--timeline", required=True,
                        help="the timeline's EXACT name (never a prefix)")
    parser.add_argument("--note", required=True,
                        help="the routed note_id to handle")
    parser.add_argument("--operations", required=True,
                        help="@path or inline JSON: the EditPatch operations")
    parser.add_argument("--scope", required=True,
                        help="@path or inline JSON: the declared scope")
    parser.add_argument("--idempotency-key", required=True)
    args = parser.parse_args(argv)

    from dataclasses import asdict

    from library.tools import marker_feedback, marker_routing

    routed = marker_routing.route_project(args.project)
    match = next((note for note in routed if note.note_id == args.note),
                 None)
    if match is None:
        print(f"no routed note {args.note!r} in {args.project!r}")
        return 2
    resolve = marker_feedback.connect_resolve()
    manager = resolve.GetProjectManager()
    resolve_project = manager.GetCurrentProject()
    timeline = None
    for index in range(1, int(resolve_project.GetTimelineCount() or 0) + 1):
        candidate = resolve_project.GetTimelineByIndex(index)
        if candidate and candidate.GetName() == args.timeline:
            timeline = candidate
            break
    if timeline is None:
        print(f"no timeline {args.timeline!r} open in "
              f"{resolve_project.GetName()!r}")
        return 2
    out = handle_note(
        routed_note=asdict(match), project_folder=args.project,
        operations=_load_json_maybe_at(args.operations),
        scope=_load_json_maybe_at(args.scope),
        idempotency_key=args.idempotency_key, project=resolve_project,
        timeline=timeline)
    print(json.dumps({key: value for key, value in out.items()
                      if key != "receipt"}, indent=2, ensure_ascii=False,
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
