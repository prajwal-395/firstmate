"""Durable outcomes for notes the captain leaves on reel timelines.

Marker pulls remain the source for what was asked and where. Resolution
records remain the source for the verification decision. This module
joins them into durable outcome events, keeps the evidence beside each
event, and renders one project-wide view without connecting to Resolve.
Repeated writes with the same outcome id update that event, so a
resolution written before and after marker removal stays one event.

The project store is versioned independently from Ren. A store commit is
included only when one was observed; a Ren PR is included only when the
caller supplied its URL. Historical entries use ``source: derived`` and
retain the record or reply that justified each claim.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools.project_layout import Area, ProjectLayout  # noqa: E402

OUTCOMES_SUBDIR = "outcomes"
OUTCOME_FORMAT = "feedback_outcome/1"
OUTCOMES_FORMAT = "feedback_outcomes/1"
VERIFICATION_TIMELINE_READBACK = "timeline_readback"
VERIFICATION_PLANNED = "planned"
VERIFICATION_NOT_DONE = "not_done"


class OutcomesUnreadable(RuntimeError):
    """An outcome record exists but cannot be read safely."""


def _safe_name(value: str) -> str:
    stem = "".join(c if c.isalnum() or c in "-_." else "_"
                   for c in str(value or "note")) or "note"
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"{stem[:100]}-{digest}.outcomes.json"


def outcome_path(project_folder, identity: str) -> Path:
    return ProjectLayout(project_folder).write_path(
        Area.MARKER_FEEDBACK, OUTCOMES_SUBDIR, _safe_name(identity))


def outcomes_dir(project_folder) -> Path:
    return ProjectLayout(project_folder).read_path(
        Area.MARKER_FEEDBACK, OUTCOMES_SUBDIR)


def _atomic_json(path: Path, document: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, staged = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(dict(document), out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(staged, path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise


def _read_document(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OutcomesUnreadable(
            f"{path} exists but could not be read: {exc}") from exc
    if (not isinstance(document, dict)
            or document.get("format") != OUTCOMES_FORMAT
            or not isinstance(document.get("events"), list)):
        raise OutcomesUnreadable(
            f"{path} is not a {OUTCOMES_FORMAT} document")
    return document


def write_event(project_folder, event: Mapping) -> dict:
    """Append an event or replace the same event id after read-back."""
    event = dict(event)
    event["format"] = OUTCOME_FORMAT
    identity = str(event.get("note_identity") or "")
    event_id = str(event.get("outcome_id") or "")
    if not identity or not event_id:
        raise ValueError("an outcome needs note_identity and outcome_id")
    path = outcome_path(project_folder, identity)
    if path.exists():
        document = _read_document(path)
        if document.get("note_identity") != identity:
            raise OutcomesUnreadable(
                f"{path} belongs to another note identity")
    else:
        document = {"format": OUTCOMES_FORMAT,
                    "note_identity": identity, "events": []}
    events = list(document["events"])
    for index, old in enumerate(events):
        if old.get("outcome_id") == event_id:
            events[index] = event
            break
    else:
        events.append(event)
    events.sort(key=lambda row: (str(row.get("recorded_at") or ""),
                                 str(row.get("outcome_id") or "")))
    document["events"] = events
    document["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(path, document)
    return event


def read_events(project_folder) -> list[dict]:
    directory = outcomes_dir(project_folder)
    if not directory.is_dir():
        return []
    events = []
    for path in sorted(directory.glob("*.outcomes.json")):
        document = _read_document(path)
        for event in document["events"]:
            if not isinstance(event, dict):
                raise OutcomesUnreadable(
                    f"{path} contains an outcome that is not an object")
            events.append(dict(event))
    return sorted(events, key=lambda row: (
        str(row.get("recorded_at") or ""),
        str(row.get("outcome_id") or "")))


def pending_outcome_id(project_folder, note: Mapping) -> str:
    """Return the newest recorded plan for this note, if one is open."""
    timeline = str(note.get("timeline") or "")
    identity = _identity(note, timeline)
    pending = [event for event in read_events(project_folder)
               if event.get("note_identity") == identity
               and event.get("source") == "recorded"
               and event.get("status") == "planned"]
    if not pending:
        return ""
    latest = max(pending, key=lambda row: (
        str(row.get("recorded_at") or ""),
        str(row.get("outcome_id") or "")))
    return str(latest.get("outcome_id") or "")


def _note_text(note: Mapping) -> str:
    explicit = note.get("text")
    if explicit:
        return str(explicit)
    return "\n\n".join(
        str(part) for part in (note.get("name") or "", note.get("note") or "")
        if part)


def _anchor(note: Mapping) -> dict:
    anchor = {
        "source": note.get("source") or "",
        "timeline": note.get("timeline") or "",
        "frame": note.get("frame"),
        "frame_in_timeline_space": note.get("frame_in_timeline_space"),
        "timecode": note.get("timecode"),
        "pull_file": note.get("pull_file") or "",
        "collected_at": note.get("collected_at") or "",
    }
    # A note typed while watching a file (`ren revise --at`, the round's
    # review page) also says WHERE in that file - the moment the editor
    # saw, which is what they will recognise.
    watched = (note.get("custom_data") or {}).get("chat_time_anchor") \
        if isinstance(note.get("custom_data"), Mapping) else None
    if isinstance(watched, Mapping) and watched.get("file_clock"):
        anchor["file_clock"] = str(watched["file_clock"])
        anchor["watched"] = str(watched.get("watched") or "delivered")
    return anchor


def _identity(note: Mapping, timeline: str = "") -> str:
    from library.tools.feedback_ledger import durable_identity, note_text

    stable = str(note.get("stable_note_id") or note.get("identity") or "")
    return stable or durable_identity(
        timeline or str(note.get("timeline") or ""), note_text(note))


def _project_head(project_folder) -> list[dict]:
    root = Path(project_folder)
    if not (root / ".git").exists():
        return []
    from library.tools.versions import store

    result = store.git(project_folder, "show", "-s", "--format=%H%x09%s", "HEAD")
    if result.returncode != 0 or not result.stdout.strip():
        return []
    sha, _, subject = result.stdout.strip().partition("\t")
    return [{"sha": sha, "subject": subject,
             "relation": "project-store HEAD when outcome was recorded"}]


def _version_refs(project_folder, timeline: str) -> dict:
    if not timeline:
        return {"before": None, "after": None,
                "reason": "the note has no timeline name"}
    try:
        from library.tools.feedback_ledger import base_reel_name
        from library.tools.versions import reel_versions

        document = reel_versions.read(project_folder)
        target = base_reel_name(timeline)
        candidates = [(name, history) for name, history
                      in document["reels"].items()
                      if base_reel_name(name) == target]
        exact = [(name, history) for name, history in candidates
                 if name == timeline]
        if exact:
            candidates = exact
        if len(candidates) != 1:
            reason = ("no reel version history was recorded"
                      if not candidates else
                      "multiple reel histories share the note's base name")
            return {"before": None, "after": None, "reason": reason}
        _name, history = candidates[0]
        if not history:
            return {"before": None, "after": None,
                    "reason": "the reel version history is empty"}
        def compact(entry):
            return {key: entry[key] for key in
                    ("version", "kind", "at", "built_with", "rows_digest")
                    if key in entry}
        return {"before": compact(history[-2]) if len(history) > 1 else None,
                "after": compact(history[-1]),
                "reason": ("the latest recorded reel version; no prior "
                           "version exists" if len(history) == 1 else "")}
    except Exception as exc:  # noqa: BLE001 - version context is supplementary
        return {"before": None, "after": None,
                "reason": f"reel version history could not be read: {exc}"}


def _unmodified_versions(project_folder, timeline: str, *, uncertain=False) -> dict:
    current = _version_refs(project_folder, timeline).get("after")
    return {
        "before": current,
        "after": None if uncertain else current,
        "reason": ("the outcome did not verify a post-change reel version"
                   if uncertain else
                   "no reel version change was recorded for this outcome"),
    }


def _infer_action_kind(status: str, check: str, action: str,
                       explicit: str = "") -> str:
    if explicit:
        return explicit
    if status == "declined":
        return "declined"
    if check == "feedback_patch_holds" or action.lower().startswith(
            "patched in place"):
        return "patch"
    if status == "blocked":
        return "blocked"
    if status in ("addressed_unverified", "unverifiable"):
        return "rebuild" if action else "blocked"
    return "rebuild"


def _default_not_done(status: str, action: str, rationale: str,
                      evidence: Mapping, removal: Mapping) -> dict | None:
    if status == "declined":
        reason = rationale or action or "the note was declined"
    elif status in ("addressed_unverified", "unverifiable", "blocked"):
        reason = (removal.get("reason") or evidence.get("reason")
                  or rationale or action
                  or f"the note was not completed ({status})")
    else:
        return None
    return {"reason": str(reason), "status": status}


def resolution_event(project_folder, record: Mapping, *, outcome_id: str,
                     details: Mapping | None = None) -> dict:
    """Build the event paired with a marker_resolution record."""
    details = dict(details or {})
    status = str(record.get("status") or "")
    action = str(record.get("action") or "")
    rationale = str(record.get("rationale") or "")
    check = str(record.get("check") or "")
    evidence = dict(record.get("evidence") or {})
    removal = dict(record.get("removal") or {})
    timeline = str(record.get("timeline") or "")
    note = {
        "note_id": record.get("stable_note_id") or record.get("note_id") or "",
        "name": record.get("name") or "",
        "note": record.get("note") or "",
        "text": record.get("text") or _note_text(record),
        "timeline": timeline,
        "source": record.get("source") or "",
    }
    identity = _identity(record, timeline)
    from library.tools.marker_resolution import CHECKS

    readback_proven = (status == "resolved_verified" and check in CHECKS
                       and (check != "feedback_patch_holds"
                            or (evidence.get("post_hash")
                                and evidence.get("post_hash")
                                == evidence.get("head_hash"))))
    verification_state = (VERIFICATION_TIMELINE_READBACK if readback_proven
                          else VERIFICATION_PLANNED)
    not_done = details.get("not_done")
    if not_done is None:
        not_done = _default_not_done(status, action, rationale, evidence, removal)
    if not_done:
        verification_state = details.get("verification_state") or \
            VERIFICATION_NOT_DONE
    changed = str(details.get("change_summary") or rationale or action)
    if status == "declined":
        changed = "No change was made."
    commits = details.get("store_commits")
    if commits is None:
        commits = _project_head(project_folder)
    versions = details.get("reel_versions")
    if versions is None:
        versions = (_version_refs(project_folder, timeline)
                    if status == "resolved_verified" else
                    _unmodified_versions(
                        project_folder, timeline,
                        uncertain=status != "declined"))
    return {
        "format": OUTCOME_FORMAT,
        "outcome_id": str(outcome_id),
        "note_identity": identity,
        "note": {"text": note["text"], "name": note["name"],
                 "note": note["note"]},
        "anchor": _anchor(record),
        "status": status,
        "action": {"kind": _infer_action_kind(
                       status, check, action,
                       str(details.get("action_kind") or "")),
                   "summary": changed,
                   "rationale": rationale},
        "store_commits": list(commits or []),
        "ren_pr": str(details.get("ren_pr") or os.environ.get(
            "REN_PR_URL", "")),
        "reel_versions": dict(versions or {}),
        "verification": {"state": verification_state,
                         "check": check,
                         "evidence": evidence,
                         "marker_removed": bool(record.get("marker_removed")),
                         "marker_still_present": bool(
                             record.get("marker_still_present", True))},
        "not_done": not_done,
        "source": str(details.get("source") or "recorded"),
        "recorded_at": str(record.get("resolved_at") or
                            datetime.now(timezone.utc).isoformat()),
        "resolution_record": str(record.get("note_id") or ""),
    }


def record_resolution_outcome(project_folder, record: Mapping, *,
                              outcome_id: str,
                              details: Mapping | None = None) -> dict:
    event = resolution_event(project_folder, record, outcome_id=outcome_id,
                             details=details)
    return write_event(project_folder, event)


def record_plan(project_folder, note: Mapping, *, action_kind: str,
                summary: str, outcome_id: str = "") -> dict:
    from library.tools.feedback_ledger import note_text

    timeline = str(note.get("timeline") or "")
    identity = _identity(note, timeline)
    when = datetime.now(timezone.utc).isoformat()
    event = {
        "format": OUTCOME_FORMAT,
        "outcome_id": outcome_id or hashlib.sha256(
            f"plan\0{identity}\0{when}".encode("utf-8")).hexdigest()[:24],
        "note_identity": identity,
        "note": {"text": note_text(note), "name": note.get("name") or "",
                 "note": note.get("note") or ""},
        "anchor": _anchor(note),
        "status": "planned",
        "action": {"kind": action_kind, "summary": summary, "rationale": ""},
        "store_commits": _project_head(project_folder),
        "ren_pr": os.environ.get("REN_PR_URL", ""),
        "reel_versions": _unmodified_versions(
            project_folder, timeline, uncertain=True),
        "verification": {"state": VERIFICATION_PLANNED, "check": "",
                         "evidence": {}, "marker_removed": False,
                         "marker_still_present": True},
        "not_done": None,
        "source": "recorded",
        "recorded_at": when,
        "resolution_record": "",
    }
    return write_event(project_folder, event)


def record_blocked(project_folder, note: Mapping, *, reason: str,
                   action_kind: str = "blocked", outcome_id: str = "",
                   summary: str = "No change was made.",
                   reel_versions: Mapping | None = None) -> dict:
    from library.tools.feedback_ledger import note_text

    timeline = str(note.get("timeline") or "")
    identity = _identity(note, timeline)
    when = datetime.now(timezone.utc).isoformat()
    event = {
        "format": OUTCOME_FORMAT,
        "outcome_id": outcome_id or hashlib.sha256(
            f"blocked\0{identity}\0{when}".encode("utf-8")).hexdigest()[:24],
        "note_identity": identity,
        "note": {"text": note_text(note), "name": note.get("name") or "",
                 "note": note.get("note") or ""},
        "anchor": _anchor(note),
        "status": "blocked",
        "action": {"kind": action_kind, "summary": summary,
                   "rationale": ""},
        "store_commits": _project_head(project_folder),
        "ren_pr": os.environ.get("REN_PR_URL", ""),
        "reel_versions": dict(reel_versions or _unmodified_versions(
            project_folder, timeline, uncertain=True)),
        "verification": {"state": VERIFICATION_NOT_DONE, "check": "",
                         "evidence": {}, "marker_removed": False,
                         "marker_still_present": True},
        "not_done": {"reason": str(reason), "status": "blocked"},
        "source": "recorded",
        "recorded_at": when,
        "resolution_record": "",
    }
    return write_event(project_folder, event)


def _parse_time(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_timeline(timeline: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_"
                   for c in timeline) or "timeline"


def _snapshot_commits(project_folder, timeline: str) -> list[dict]:
    if not timeline or not (Path(project_folder) / ".git").exists():
        return []
    from library.tools.versions import store

    relpath = (f"pipeline_output/review/{_safe_timeline(timeline)}"
               ".timeline.json")
    result = store.git(project_folder, "log", "--format=%H%x09%aI%x09%s",
                       "--", relpath)
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append({"sha": parts[0], "committed_at": parts[1],
                         "subject": parts[2], "path": relpath,
                         "relation": "committed snapshot of this reel"})
    return rows


def _versions_around(project_folder, timeline: str, first_asked: str,
                     resolved_at: str) -> tuple[dict, list[dict]]:
    commits = _snapshot_commits(project_folder, timeline)
    asked, resolved = _parse_time(first_asked), _parse_time(resolved_at)
    eligible = []
    for commit in commits:
        at = _parse_time(commit["committed_at"])
        if at and asked and resolved and asked <= at <= resolved:
            eligible.append(commit)
    before_candidates = [commit for commit in commits
                         if _parse_time(commit["committed_at"])
                         and asked and _parse_time(commit["committed_at"]) <= asked]
    before = before_candidates[0] if before_candidates else None
    after = eligible[0] if eligible else None
    refs = {"before": before, "after": after,
            "reason": ("reconstructed from committed timeline snapshots"
                       if after else
                       "no committed timeline snapshot falls between the "
                       "first pull and this outcome")}
    return refs, eligible


def _derived_from_resolution(project_folder, identity: str,
                             record: Mapping, first_asked: str) -> dict:
    outcome_id = "derived-resolution-" + hashlib.sha256(
        f"{identity}\0{record.get('resolved_at','')}".encode("utf-8")
    ).hexdigest()[:20]
    event = resolution_event(project_folder, record, outcome_id=outcome_id,
                             details={"source": "derived",
                                      "store_commits": []})
    event["source"] = "derived"
    event["note_identity"] = identity
    versions, commits = _versions_around(
        project_folder, str(record.get("timeline") or ""), first_asked,
        str(record.get("resolved_at") or ""))
    event["reel_versions"] = versions
    if commits:
        event["store_commits"] = commits
    elif event["store_commits"]:
        event["store_commits"] = [{**row,
                                   "association": "HEAD only; no matching "
                                   "timeline snapshot was found"}
                                  for row in event["store_commits"]]
    event["recorded_at"] = str(record.get("resolved_at") or "")
    return event


def _reply_summary(text: str) -> str:
    lines = str(text or "").splitlines()
    done = []
    collect = False
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("DONE:"):
            collect = True
            done.append(stripped[5:].strip())
            continue
        if collect and stripped.startswith(("JUDGE THIS:", "This reel ",
                                            "This marker ", "It was ")):
            break
        if collect and stripped:
            done.append(stripped)
    return "\n".join(done).strip() or str(text or "").strip()


def backfill(project_folder, *, from_reel: int | None = None,
             to_reel: int | None = None) -> dict:
    """Write derived events from resolutions, replies and committed snapshots."""
    from library.tools import feedback_ledger, marker_feedback, marker_resolution

    pulls = marker_feedback.pulled_files(project_folder)
    entries = feedback_ledger.link_replies(
        feedback_ledger.collect(project_folder, pulls))
    original_notes = {}
    for pull_path, payload in pulls:
        timeline = str(payload.get("timeline") or "")
        for raw in payload.get("notes") or ():
            identity = feedback_ledger.identity_of(raw, timeline)
            if identity in original_notes:
                continue
            try:
                pull_file = str(pull_path.relative_to(
                    Path(project_folder).expanduser().resolve()))
            except ValueError:
                pull_file = Path(pull_path).name
            original_notes[identity] = {
                "note": raw,
                "anchor": {
                    "source": raw.get("source") or "",
                    "timeline": timeline,
                    "frame": raw.get("frame"),
                    "frame_in_timeline_space": raw.get(
                        "frame_in_timeline_space"),
                    "timecode": raw.get("timecode"),
                    "pull_file": pull_file,
                    "collected_at": payload.get("pulled_at") or "",
                },
            }
    existing = read_events(project_folder)
    events_by_identity: dict[str, list[dict]] = {}
    for event in existing:
        events_by_identity.setdefault(event["note_identity"], []).append(event)
    resolutions: dict[str, list[dict]] = {}
    for record in marker_resolution.all_resolutions(project_folder):
        stable = str(record.get("stable_note_id") or "")
        identity = stable or feedback_ledger.durable_identity(
            str(record.get("timeline") or ""),
            feedback_ledger.note_text(record))
        resolutions.setdefault(identity, []).append(record)
    created = []
    skipped = 0
    for identity, entry in entries.items():
        if entry.kind != feedback_ledger.KIND_ASK:
            continue
        match = re.search(r"\bReel\s+(\d+)\b", entry.reel, re.IGNORECASE)
        number = int(match.group(1)) if match else None
        if from_reel is not None and (number is None or number < from_reel):
            continue
        if to_reel is not None and (number is None or number > to_reel):
            continue
        # Recorded events are authoritative. Derived events are rebuilt
        # from their source records, then idempotently replaced by id.
        current_events = events_by_identity.get(identity, [])
        if any(event.get("source") != "derived"
               for event in current_events):
            skipped += 1
            continue
        records = resolutions.get(identity) or []
        if records:
            record = max(records, key=lambda row: str(row.get("resolved_at") or ""))
            event = _derived_from_resolution(
                project_folder, identity, record, entry.first_asked)
            original = original_notes.get(identity)
            if original:
                raw = original["note"]
                event["note"] = {
                    "text": feedback_ledger.note_text(raw),
                    "name": raw.get("name") or "",
                    "note": raw.get("note") or "",
                }
                event["anchor"] = original["anchor"]
            created.append(write_event(project_folder, event))
            events_by_identity.setdefault(identity, []).append(event)
            continue
        replies = [entries[reply_id] for reply_id in entry.answered_by
                   if reply_id in entries]
        for reply in replies:
            reply_id = "derived-reply-" + hashlib.sha256(
                f"{identity}\0{reply.identity}".encode("utf-8")).hexdigest()[:20]
            summary = _reply_summary(reply.text)
            original = original_notes.get(identity)
            raw = original["note"] if original else {}
            event = {
                "format": OUTCOME_FORMAT,
                "outcome_id": reply_id,
                "note_identity": identity,
                "note": {"text": feedback_ledger.note_text(raw)
                         if raw else entry.text,
                         "name": raw.get("name") or "",
                         "note": raw.get("note") or ""},
                "anchor": (original["anchor"] if original else {
                    "source": "", "timeline": entry.reel,
                    "frame": None, "frame_in_timeline_space": None,
                    "timecode": None, "pull_file": "",
                    "collected_at": entry.first_asked}),
                "status": "answered_by_reply",
                "action": {"kind": ("patch" if "patched in place" in
                                     summary.lower() else "rebuild"),
                           "summary": summary, "rationale": ""},
                "store_commits": [],
                "ren_pr": "",
                "reel_versions": {"before": None, "after": None,
                                  "reason": "no direct version reference in "
                                            "the reply"},
                "verification": {"state": VERIFICATION_PLANNED,
                                 "check": "", "evidence": {
                                     "reply_identity": reply.identity,
                                     "reply_text": reply.text},
                                 "marker_removed": False,
                                 "marker_still_present": True},
                "not_done": (None if "done:" in reply.text.lower() else {
                    "reason": "the reply has no explicit DONE statement",
                    "status": "blocked"}),
                "source": "derived",
                "recorded_at": reply.last_seen or entry.last_seen,
                "resolution_record": "",
            }
            timeline_snapshot_versions, commits = _versions_around(
                project_folder, entry.reel, entry.first_asked,
                reply.last_seen or entry.last_seen)
            event["reel_versions"] = timeline_snapshot_versions
            event["store_commits"] = commits
            created.append(write_event(project_folder, event))
            events_by_identity.setdefault(identity, []).append(event)
    return {"created": len(created), "skipped": skipped,
            "events": created}


def _reel_number(name: str) -> int | None:
    match = re.search(r"\bReel\s+(\d+)\b", str(name or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def build(project_folder, *, from_reel: int | None = None,
          to_reel: int | None = None) -> dict:
    from library.tools import feedback_ledger, marker_feedback

    pulls = marker_feedback.pulled_files(project_folder)
    entries = feedback_ledger.collect(project_folder, pulls)
    pull_anchors = {}
    for pull_path, payload in pulls:
        timeline = str(payload.get("timeline") or "")
        for raw in payload.get("notes") or ():
            identity = feedback_ledger.identity_of(raw, timeline)
            if identity in pull_anchors:
                continue
            try:
                pull_file = str(pull_path.relative_to(
                    Path(project_folder).expanduser().resolve()))
            except ValueError:
                pull_file = Path(pull_path).name
            pull_anchors[identity] = _anchor({
                **raw, "timeline": timeline, "pull_file": pull_file,
                "collected_at": payload.get("pulled_at") or ""})
    event_map: dict[str, list[dict]] = {}
    for event in read_events(project_folder):
        event_map.setdefault(event["note_identity"], []).append(event)
    notes = []
    for identity, entry in entries.items():
        if entry.kind != feedback_ledger.KIND_ASK:
            continue
        number = _reel_number(entry.reel)
        if from_reel is not None and (number is None or number < from_reel):
            continue
        if to_reel is not None and (number is None or number > to_reel):
            continue
        outcomes = sorted(event_map.get(identity, ()), key=lambda row: (
            str(row.get("recorded_at") or ""),
            str(row.get("outcome_id") or "")))
        notes.append({"identity": identity, "reel": entry.reel,
                      "reel_number": number, "text": entry.text,
                      "note_id": entry.note_id,
                      "first_asked": entry.first_asked,
                      "last_seen": entry.last_seen,
                      "frames": list(entry.frames),
                      "anchor": next((event.get("anchor") for event in
                                      sorted(event_map.get(identity, ()),
                                             key=lambda row: str(
                                                 row.get("recorded_at") or ""))
                                      if event.get("anchor")),
                                  pull_anchors.get(identity)),
                      "outcomes": outcomes})
    notes.sort(key=lambda row: (row["reel_number"] is None,
                                row["reel_number"] or 0,
                                row["first_asked"], row["identity"]))
    return {"format": OUTCOMES_FORMAT,
            "project": str(Path(project_folder).expanduser().resolve()),
            "from_reel": from_reel, "to_reel": to_reel,
            "notes": notes,
            "outcome_count": sum(len(row["outcomes"]) for row in notes)}


def _version_label(reference: Mapping | None, *, missing: str) -> str:
    if not reference:
        return missing
    if reference.get("version") is not None:
        label = f"version {reference['version']}"
    elif reference.get("sha"):
        label = str(reference["sha"])[:12]
    elif reference.get("generation") is not None:
        label = f"generation {reference['generation']}"
    elif reference.get("snapshot_hash"):
        label = str(reference["snapshot_hash"])[:12]
    else:
        label = "recorded"
    at = reference.get("committed_at") or reference.get("at")
    return f"{label} ({at})" if at else label


def render_summary(document: Mapping) -> str:
    """The budgeted default: counts plus the 5 newest notes, never the ledger.

    LE-12: `ren outcomes` defaulted to the full per-note ledger
    (25,394 bytes on the measured project). The summary keeps the
    shape an agent scans - per-action counts, per-verification-state
    counts, and the 5 newest notes with one line each - under budget.
    `--full` restores `render_text`.
    """
    notes = list(document.get("notes") or ())
    events = [event for note in notes for event in (note.get("outcomes") or ())]
    by_action: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for event in events:
        action = str((event.get("action") or {}).get("kind") or "unknown")
        by_action[action] = by_action.get(action, 0) + 1
        state = str((event.get("verification") or {}).get("state") or "unknown")
        by_state[state] = by_state.get(state, 0) + 1
    open_notes = sum(1 for note in notes if not note.get("outcomes"))
    lines = [f"Feedback outcomes: {len(notes)} note(s), "
             f"{document.get('outcome_count', len(events))} outcome event(s)"
             f" ({open_notes} open)"]
    if by_action:
        lines.append("actions: " + ", ".join(
            f"{kind}={count}" for kind, count in sorted(by_action.items())))
    else:
        lines.append("actions: none recorded")
    if by_state:
        lines.append("verification: " + ", ".join(
            f"{state}={count}" for state, count in sorted(by_state.items())))
    else:
        lines.append("verification: none recorded")
    newest = sorted(
        notes,
        key=lambda row: (str(row.get("last_seen") or ""),
                         str(row.get("first_asked") or ""),
                         str(row.get("identity") or "")),
        reverse=True)[:5]
    lines.append(f"newest {len(newest)} note(s):")
    for note in newest:
        outcome_kinds = ",".join(
            str((event.get("action") or {}).get("kind") or "?")
            for event in (note.get("outcomes") or ())) or "open"
        text = str(note.get("text") or "(no text)").splitlines()
        first_line = text[0][:100] if text else "(no text)"
        lines.append(f"  {note.get('reel', '?')} - {note.get('identity', '?')}"
                     f" [{outcome_kinds}]: {first_line}")
    lines.append("(`ren outcomes <project> --full` prints the full ledger; "
                 "`--json` prints the document.)")
    return "\n".join(lines)


def render_text(document: Mapping) -> str:
    notes = list(document.get("notes") or ())
    lines = [f"Feedback outcomes: {len(notes)} note(s), "
             f"{document.get('outcome_count', 0)} outcome event(s)",
             f"Project: {document.get('project', '')}"]
    for note in notes:
        lines += ["", f"{note['reel']} - {note['identity']}",
                  f"  asked: {note.get('first_asked') or 'unknown'}",
                  f"  note: {note.get('text') or '(no text)'}"]
        anchor = note.get("anchor") or {}
        if anchor:
            frame = anchor.get("frame_in_timeline_space")
            if frame is None:
                frame = anchor.get("frame")
            if frame is not None or anchor.get("timecode"):
                lines.append(f"  anchor: frame {frame if frame is not None else '?'}"
                             f"{(' / ' + str(anchor['timecode'])) if anchor.get('timecode') else ''}")
        if not note["outcomes"]:
            lines.append("  outcome: open - no outcome record found")
            continue
        for event in note["outcomes"]:
            action = event.get("action") or {}
            verification = event.get("verification") or {}
            lines.append(f"  outcome [{event.get('source','recorded')}/"
                          f"{event.get('status','unknown')}]: "
                          f"{action.get('kind','unknown')} - "
                          f"{action.get('summary') or '(no change summary)'}")
            lines.append(f"    verified: {verification.get('state','unknown')}")
            commits = event.get("store_commits") or []
            if commits:
                lines.append("    project-store commit(s): " + ", ".join(
                    f"{row.get('sha','')} {row.get('subject','')}"
                    f" ({row['relation']})" if row.get("relation") else
                    f"{row.get('sha','')} {row.get('subject','')}"
                    for row in commits))
            else:
                lines.append("    project-store commit(s): none linked")
            if event.get("ren_pr"):
                lines.append(f"    Ren PR: {event['ren_pr']}")
            versions = event.get("reel_versions") or {}
            before, after = versions.get("before"), versions.get("after")
            if before or after:
                lines.append(f"    reel version: {_version_label(before, missing='unknown')}"
                             f" -> {_version_label(after, missing='not recorded')}")
            elif versions.get("reason"):
                lines.append(f"    reel version: unknown ({versions['reason']})")
            if event.get("not_done"):
                lines.append(f"    not done: "
                             f"{event['not_done'].get('reason','unknown')}")
    return "\n".join(lines)


STYLE = (
    ":root{color-scheme:light;--ink:#18211e;--muted:#61716b;--line:#d9e2dc;--paper:#f4f7f2;--card:#fff;--green:#145c45;--amber:#805600;--red:#963d35}"
    "*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}"
    "main{max-width:980px;margin:0 auto;padding:40px 22px 72px}h1{font-size:clamp(2rem,5vw,3rem);line-height:1.05;margin:0 0 10px;letter-spacing:-.035em}"
    ".sub{color:var(--muted);margin:0 0 28px;overflow-wrap:anywhere}.note{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;margin:14px 0;box-shadow:0 3px 14px #18211e0a}"
    ".note header{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}.reel{font-weight:750;color:var(--green)}.location{font-size:.85rem;color:var(--muted)}.note-text{white-space:pre-wrap;overflow-wrap:anywhere;margin:14px 0 18px}"
    ".outcome{border-top:1px solid var(--line);padding-top:15px;margin-top:15px}.tags{display:flex;gap:7px;flex-wrap:wrap}.tags span{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;border-radius:999px;background:#edf3ee;padding:4px 9px;color:var(--green)}"
    ".outcome h3{font-size:1rem;margin:11px 0 3px;text-transform:capitalize}.outcome p{margin:7px 0}.not-done{color:var(--red)}.open{color:var(--amber)}ul{padding-left:20px;color:var(--muted);overflow-wrap:anywhere}code{font-size:.85em}.empty{color:var(--muted)}@media(max-width:600px){main{padding:26px 14px 48px}.note{padding:17px}}")
"""The outcome report's look, shared with the round's review page
(`round_page`), which shows the same note cards beside the previews."""


def render_html(document: Mapping) -> str:
    range_label = "All reels"
    if document.get("from_reel") is not None or document.get("to_reel") is not None:
        low = document.get("from_reel") or 1
        high = document.get("to_reel") or "end"
        range_label = f"Reels {low}–{high}"
    body = (render_cards(document.get("notes") or ())
            or '<p class="empty">No notes found.</p>')
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>Feedback outcomes - {html.escape(range_label)}</title>"
            f"<style>{STYLE}</style>"
            "</head><body><main>"
            f"<h1>Feedback outcomes</h1><p class=\"sub\">{html.escape(range_label)} · "
            f"{len(document.get('notes') or ())} notes · "
            f"{html.escape(str(document.get('project') or ''))}</p>"
            f"{body}</main></body></html>")


def render_cards(notes) -> str:
    """One `<article class="note">` per note: its words, where it sits and
    every outcome recorded for it. "" for no notes."""
    cards = []
    for note in notes or ():
        note_text = html.escape(str(note.get("text") or "(no text)"))
        outcomes = []
        if not note.get("outcomes"):
            outcomes.append('<p class="open">Open - no outcome record found</p>')
        for event in note.get("outcomes") or ():
            action = event.get("action") or {}
            verification = event.get("verification") or {}
            commits = event.get("store_commits") or []
            versions = event.get("reel_versions") or {}
            before, after = versions.get("before"), versions.get("after")
            commit_html = "".join(
                f"<li><code>{html.escape(str(row.get('sha','')))}</code> "
                f"{html.escape(str(row.get('subject','')))}"
                f"{(' (' + html.escape(str(row['relation'])) + ')') if row.get('relation') else ''}</li>"
                for row in commits)
            if commits:
                commit_html = f"<p>Project-store commit(s):</p><ul>{commit_html}</ul>"
            else:
                commit_html = "<p>Project-store commit(s): none linked</p>"
            versions_text = ""
            if before or after:
                versions_text = ("<p>Reel version: "
                    f"{html.escape(_version_label(before, missing='unknown'))}"
                    " → "
                    f"{html.escape(_version_label(after, missing='not recorded'))}"
                    "</p>")
            elif versions.get("reason"):
                versions_text = ("<p>Reel version: unknown ("
                                 f"{html.escape(str(versions['reason']))})</p>")
            not_done = event.get("not_done")
            not_done_html = (f"<p class=\"not-done\">Not done: "
                             f"{html.escape(str(not_done.get('reason','')))}</p>"
                             if not_done else "")
            pr_html = (f"<p>Ren PR: <a href=\"{html.escape(str(event['ren_pr']), quote=True)}\">"
                       f"{html.escape(str(event['ren_pr']))}</a></p>"
                       if event.get("ren_pr") else "")
            outcomes.append(
                "<section class=\"outcome\">"
                f"<div class=\"tags\"><span>{html.escape(str(event.get('source','recorded')))}</span>"
                f"<span>{html.escape(str(event.get('status','unknown')))}</span>"
                f"<span>{html.escape(str(verification.get('state','unknown')))}</span></div>"
                f"<h3>{html.escape(str(action.get('kind','unknown')))}</h3>"
                f"<p>{html.escape(str(action.get('summary') or '(no change summary)'))}</p>"
                f"{versions_text}{pr_html}"
                f"{commit_html}"
                f"{not_done_html}</section>")
        anchor = ((note.get("outcomes") or [{}])[-1].get("anchor")
                  or note.get("anchor") or {})
        frame = anchor.get("frame_in_timeline_space")
        location = (f"At {anchor['file_clock']} in the {anchor.get('watched') or 'delivered'} file"
                    if anchor.get("file_clock") else
                    f"Frame {frame}" if frame is not None else
                    f"First asked {note.get('first_asked') or 'unknown'}")
        cards.append(
            f"<article class=\"note\"><header><span class=\"reel\">"
            f"{html.escape(str(note.get('reel') or 'Unknown reel'))}</span>"
            f"<span class=\"location\">{html.escape(location)}</span></header>"
            f"<p class=\"note-text\">{note_text}</p>"
            f"{''.join(outcomes)}</article>")
    return "".join(cards)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren outcomes",
        description="Print notes and their recorded or derived outcomes.")
    parser.add_argument("project", help="project slug or folder path")
    parser.add_argument("--from-reel", type=int, default=None,
                        help="include this reel number and later reels")
    parser.add_argument("--to-reel", type=int, default=None,
                        help="include this reel number and earlier reels")
    parser.add_argument("--html", action="store_true",
                        help="print a standalone HTML report")
    parser.add_argument("--json", action="store_true",
                        help="print the outcome document as JSON")
    parser.add_argument("--full", action="store_true",
                        help="print the full per-note ledger (today's "
                             "default); without it, print the budgeted "
                             "summary")
    parser.add_argument("--backfill", action="store_true",
                        help="write derived entries from existing records first")
    args = parser.parse_args(argv)
    if args.from_reel is not None and args.from_reel < 1:
        parser.error("--from-reel must be at least 1")
    if args.to_reel is not None and args.to_reel < 1:
        parser.error("--to-reel must be at least 1")
    if (args.from_reel is not None and args.to_reel is not None
            and args.from_reel > args.to_reel):
        parser.error("--from-reel cannot be greater than --to-reel")
    if args.html and args.json:
        parser.error("--html and --json cannot be used together")
    if args.full and args.json:
        parser.error("--full and --json cannot be used together")
    if args.full and args.html:
        parser.error("--full and --html cannot be used together")
    try:
        project = _resolve_project(args.project)
    except (FileNotFoundError, RuntimeError) as refused:
        print(f"feedback_outcomes: {refused}", file=sys.stderr)
        return 2
    if args.backfill:
        report = backfill(project, from_reel=args.from_reel,
                          to_reel=args.to_reel)
        print(f"Backfilled {report['created']} derived event(s); "
              f"skipped {report['skipped']} note(s) with an existing outcome.",
              file=sys.stderr)
    document = build(project, from_reel=args.from_reel,
                     to_reel=args.to_reel)
    if args.html:
        print(render_html(document))
    elif args.json:
        print(json.dumps(document, indent=2, ensure_ascii=False))
    elif args.full:
        print(render_text(document))
    else:
        print(render_summary(document))
    return 0


def _resolve_project(ref: str) -> str:
    """Resolve a project slug or folder without syncing or touching Resolve.

    A project whose config fails validation still resolves - reads
    answer with one `⚠ config:` line rather than refusing.
    """
    candidate = Path(ref).expanduser()
    if candidate.is_absolute() and candidate.is_dir():
        folder = candidate.resolve()
        yaml_path = folder / "project.yaml"
        if yaml_path.is_file():
            try:
                from library.schemas.project_config import load_project_config
                from library.tools.project_registry import warn_config_findings
                warn_config_findings(load_project_config(
                    yaml_path, migrate_format=False, strict=False))
            except (OSError, ValueError):
                pass
        return str(folder)
    from library.tools.project_registry import (
        get_project, resolve_project_path, warn_config_findings)

    found = resolve_project_path(ref)
    if found is not None:
        return str(found.parent)
    try:
        config = get_project(ref, strict=False)
    except FileNotFoundError as missing:
        raise RuntimeError(
            f"no project {ref!r}; pass a project slug or a folder path") \
            from missing
    warn_config_findings(config)
    return str(config._project_root)


if __name__ == "__main__":
    raise SystemExit(main())
