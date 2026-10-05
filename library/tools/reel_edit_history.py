"""A reel's edit history: every change to one timeline, in order, never overwritten.

The captain, 2026-10-05, about reels whose rebuilds fail plan-versus-timeline
verification (Reel 28: plan 1978 frames vs timeline 1403; Reel 26: plan 1022
vs timeline 1104): manual edits after the plan leave Ren unable to tell
whether the captain changed a timeline, an earlier Ren build placed it
differently, or the plan itself changed.

What Ren already records, and where the history breaks
-----------------------------------------------------
- `timeline_shadow` (machine-local SQLite, NOT project data): per-timeline
  generations with `observed` (a hand edit, or any first read) vs `patch`
  (a Ren EditPatch) source. It SEES hand edits but answers per machine,
  never per reel, and carries no actor or plan version.
- `plan_provenance.json` (project-side): the plan hash each build used,
  per-reel caption/footage hashes, `ren_timeline_snapshots` (whose per-reel
  `history` array survives), and `unattributed_editor_changes`
  (append-only, idempotent by digest). Rich, but nothing unifies one
  reel's Ren acts and the captain's edits into one ordered account.
- `reel_versions.json`: append-only Ren build/touch/undo/rollback versions
  with rows digests and plan moments - but no manual-edit kind, no actor,
  and no plan content hash.
- `conformance_report.json`: OVERWRITTEN every run (`staging_holds`) - a
  verification that fails today says nothing about what yesterday's said.
- `ren drift` (`drift_check`): compares the build snapshot against live
  and REPORTS, recording nothing - a detection without a trace.

This module is the unified account, built ON those records rather than
beside them: entries live as one more key inside the existing
`plan_provenance.json` sidecar (same file, same project-file lock, same
merge discipline), and every entry REFERENCES the record that proves it
(version number, editor-change id, snapshot digest) instead of copying
snapshots into a parallel store.

An entry carries time (`at`), actor (`ren` or `captain`), what changed
(`act` + `summary`), and - for every Ren build and promotion - the plan
version it used (`plan_version`: the plan content hash plus the caption
and footage-binding hashes). Manual edits are detected by comparing the
live timeline against Ren's last recorded state and are recorded
read-only: detection never writes to Resolve, never reverts or
"corrects" the captain's edit, and never changes a resolution. Reel
rebuilds stay out of scope: recording never alters what a build places.

Plan-versus-timeline verification consults this history
(`attribute_plan_mismatch`, wired into `verify_reel`): a frame mismatch
that a recorded manual edit explains is reported AS that edit - naming
the entry, its time and what it changed - instead of as an unexplained
PLAN-MISMATCH error. What nothing recorded explains still refuses.

The captain's reason for a detected edit
----------------------------------------
A detected edit is a fact without a motive until the captain's own
words are filed against it. `file_manual_edit_reason` files those
words at the next interaction - a NEW entry referencing the edit it
explains (history is append-only, so the detection entry is never
rewritten), linked by the entry's id or its `editor_change_id`, and by
the `note_id` of the answered note that carried them. `reason_for`
reads them back; `attribute_plan_mismatch` cites them when the edit
explains a mismatch. `unexplained_manual_edits` /
`intent_unknown_lines` report the detected edits still waiting for a
reason - the run summary's "intent unknown" prompt. Filing a reason
never blocks or alters the edit itself: the captain's change stands
exactly as he made it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Optional, Sequence

#: The key inside `plan_provenance.json` holding the per-reel histories.
HISTORY_KEY = "reel_edit_history"

ACTOR_REN = "ren"
ACTOR_CAPTAIN = "captain"

ACT_BUILD = "build"
ACT_PROMOTION = "promotion"
ACT_TOUCH = "touch"
ACT_UNDO = "undo"
ACT_ROLLBACK = "rollback"
ACT_MANUAL_EDIT = "manual_edit"
ACT_MANUAL_EDIT_REASON = "manual_edit_reason"

REN_ACTS = (ACT_BUILD, ACT_PROMOTION, ACT_TOUCH, ACT_UNDO, ACT_ROLLBACK)

_FORMAT = "reel_edit_history/1"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str)


def _entry_id(final: str, at: str, actor: str, act: str,
              summary: str, refs: Mapping) -> str:
    digest = hashlib.sha256(
        f"{final}\0{at}\0{actor}\0{act}\0{summary}\0"
        f"{_canonical(dict(refs or {}))}".encode("utf-8")).hexdigest()
    return digest[:32]


def _mutate(review_dir: str, mutation) -> None:
    """Apply `mutation(doc)` to the provenance sidecar under its lock."""
    from library.tools.project_file_lock import lock_project_file

    path = Path(review_dir) / "plan_provenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock_project_file(path):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            doc = {}
        if not isinstance(doc, dict):
            raise ValueError(f"{path} is not a JSON object")
        mutation(doc)
        path.write_text(json.dumps(doc, indent=2, default=str),
                        encoding="utf-8")


def recorded_history(review_dir: str, final: str) -> list[dict]:
    """The entries recorded for `final`, oldest first. Never raises."""
    try:
        from library.tools.plan_provenance import read_provenance

        doc = read_provenance(str(review_dir)) or {}
        table = doc.get(HISTORY_KEY) or {}
        entries = table.get(str(final)) or []
        ordered = sorted((dict(entry) for entry in entries
                          if isinstance(entry, dict)),
                         key=lambda e: (str(e.get("at") or ""),
                                        str(e.get("id") or "")))
        return ordered
    except Exception:  # noqa: BLE001 - an unreadable history reads as empty
        return []


def _derived_manual_entries(review_dir: str, final: str) -> list[dict]:
    """Manual-edit entries derived from recorded editor-change records.

    `reel_replace_guard.detect_editor_changes` already records the
    captain's unattributed live edits into
    `unattributed_editor_changes` at build/promote time. Those ARE the
    recorded manual edits, so the history surfaces them even where no
    explicit history entry was ever written. Derived on READ, never
    persisted: an explicit entry referencing the same editor-change id
    suppresses its derived twin, so the two can never double-count.
    """
    try:
        from library.tools.plan_provenance import read_provenance

        doc = read_provenance(str(review_dir)) or {}
    except Exception:  # noqa: BLE001 - derivation is best-effort
        return []
    table = doc.get(HISTORY_KEY) or {}
    recorded = table.get(str(final)) or []
    referenced = set()
    for entry in recorded:
        if not isinstance(entry, dict):
            continue
        refs = entry.get("refs") or {}
        if refs.get("editor_change_id"):
            referenced.add(str(refs["editor_change_id"]))
    changes = ((doc.get("unattributed_editor_changes") or {}).get(
        str(final)) or [])
    derived = []
    for record in changes:
        if not isinstance(record, dict) or not record.get("id"):
            continue
        if str(record["id"]) in referenced:
            continue
        summaries = []
        for change in record.get("changes") or ():
            if isinstance(change, dict):
                summaries.append(_summarize_change(change))
        derived.append({
            "id": f"editor-change:{record['id']}",
            "at": str(record.get("recorded_at") or ""),
            "actor": ACTOR_CAPTAIN,
            "act": ACT_MANUAL_EDIT,
            "summary": ("captain's manual edit recorded at "
                        f"{record.get('recorded_at')}: "
                        + ("; ".join(summaries[:5])
                           if summaries else "live timeline differs from "
                           "Ren's last recorded snapshot")
                        + (""
                           if str(record.get("status") or "pending")
                           == "pending" else
                           f" (status: {record.get('status')})")),
            "plan_version": None,
            "refs": {"editor_change_id": str(record["id"]),
                     "baseline": record.get("baseline"),
                     "status": record.get("status")},
            "frame_delta": None,
            "backfilled": False,
            "derived": True,
        })
    return derived


def _summarize_change(change: Mapping) -> str:
    kind = str(change.get("kind") or "")
    if kind.startswith("item_"):
        item = change.get("before") or change.get("after") or {}
        row = f"{item.get('track_type')}:{item.get('track_name')}"
        name = item.get("name") or ""
        if kind == "item_removed":
            return f"{row} {name!r} removed"
        if kind == "item_added":
            return f"{row} {name!r} added"
        fields = ",".join(sorted((change.get("changed") or {})))
        return f"{row} {name!r} changed ({fields})"
    if kind.startswith("marker_"):
        marker = change.get("before") or change.get("after") or {}
        return (f"{kind.replace('_', ' ')} at {marker.get('frame')} "
                f"{marker.get('name')!r}")
    return f"timeline {change.get('field')}: changed"


def history_for(review_dir: str, final: str) -> list[dict]:
    """The full ordered history of `final`: recorded plus derived.

    Recorded entries first in file order, then derived manual edits the
    editor-change table proves - all sorted oldest-first by time. A
    derived twin of an explicitly recorded edit never appears twice.
    """
    entries = recorded_history(review_dir, final)
    entries.extend(_derived_manual_entries(review_dir, final))
    entries.sort(key=lambda e: (str(e.get("at") or ""),
                                str(e.get("id") or "")))
    return entries


def make_entry(final: str, *, actor: str, act: str, summary: str,
               plan_version: Optional[Mapping] = None,
               refs: Optional[Mapping] = None,
               frame_delta: Optional[int] = None,
               at: Optional[str] = None,
               backfilled: bool = False) -> dict:
    """One history entry, constructed without writing anything.

    Hook sites holding the provenance lock (the build's own provenance
    write, promotion's rename) build entries with this and file them
    with `append_entries` into the document they already hold - one
    locked write, never two.
    """
    final = str(final)
    stamp = at or _now()
    refs = dict(refs or {})
    return {
        "id": _entry_id(final, stamp, actor, act, summary, refs),
        "at": stamp,
        "actor": actor,
        "act": act,
        "summary": summary,
        "plan_version": (dict(plan_version) if plan_version is not None
                         else None),
        "refs": refs,
        "frame_delta": frame_delta,
        "backfilled": bool(backfilled),
    }


def append_entries(doc: Mapping, final: str, entries: Sequence[Mapping]
                   ) -> None:
    """File entries into a provenance document already under lock.

    Append-only and idempotent: an id already present is left exactly
    as it is, never rewritten. Mutates `doc` in place.
    """
    table = doc.get(HISTORY_KEY)
    if not isinstance(table, dict):
        table = {}
        doc[HISTORY_KEY] = table
    filed = table.get(str(final))
    if not isinstance(filed, list):
        filed = []
        table[str(final)] = filed
    known = {e.get("id") for e in filed if isinstance(e, dict)}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") not in known:
            known.add(entry.get("id"))
            filed.append(dict(entry))
def record_entry(review_dir: str, final: str, *, actor: str, act: str,
                 summary: str, plan_version: Optional[Mapping] = None,
                 refs: Optional[Mapping] = None,
                 frame_delta: Optional[int] = None,
                 at: Optional[str] = None,
                 backfilled: bool = False) -> dict:
    """Append one history entry for `final`. Idempotent by entry id.

    History is append-only: an entry whose id already exists is left
    exactly as it is, never rewritten. Returns the entry written (or
    the one already there).
    """
    entry = make_entry(final, actor=actor, act=act, summary=summary,
                       plan_version=plan_version, refs=refs,
                       frame_delta=frame_delta, at=at,
                       backfilled=backfilled)

    def update(doc):
        append_entries(doc, str(final), [entry])

    _mutate(str(review_dir), update)
    return entry


def try_record_ren_act(review_dir: str, final: str, *, act: str,
                       summary: str, plan_version: Optional[Mapping] = None,
                       refs: Optional[Mapping] = None,
                       frame_delta: Optional[int] = None) -> Optional[dict]:
    """Record one Ren act. Never raises: history must not fail a build.

    Recording is instrumentation, not the build: a history write that
    cannot land is said on stderr and the build continues (AGENTS.md
    10.4) - and the missing entry reads as a gap, never as a clean
    reel.
    """
    try:
        return record_entry(review_dir, final, actor=ACTOR_REN, act=act,
                            summary=summary, plan_version=plan_version,
                            refs=refs, frame_delta=frame_delta)
    except Exception as exc:  # noqa: BLE001 - the contract is never-fail
        print(f"  edit history unrecorded for {final}: {exc!r} - "
              f"the build continues without it", file=sys.stderr)
        return None


def record_manual_edit(review_dir: str, final: str, *, summary: str,
                       rows_digest_before: Optional[str] = None,
                       rows_digest_after: Optional[str] = None,
                       frame_delta: Optional[int] = None,
                       editor_change_id: Optional[str] = None,
                       reason: Optional[str] = None,
                       note_id: Optional[str] = None,
                       at: Optional[str] = None) -> dict:
    """Record the captain's own edit. Detection stays read-only.

    The caller measured the difference through Ren's existing read
    paths; this only files it. The edit itself is never touched.

    `reason` is the captain's own words, known only when the detection
    itself carries them (the note that produced the edit is being filed
    in the same breath). A reason found later is filed against the
    entry with `file_manual_edit_reason` - history is append-only, so
    the detection entry is never rewritten. `note_id` links the note
    that produced this edit, so the history carries the captain's
    intent alongside the mechanical change.
    """
    refs: dict = {}
    if rows_digest_before:
        refs["rows_digest_before"] = str(rows_digest_before)
    if rows_digest_after:
        refs["rows_digest_after"] = str(rows_digest_after)
    if editor_change_id:
        refs["editor_change_id"] = str(editor_change_id)
    if reason:
        refs["reason"] = str(reason)
    if note_id:
        refs["note_id"] = str(note_id)
    return record_entry(review_dir, final, actor=ACTOR_CAPTAIN,
                        act=ACT_MANUAL_EDIT, summary=summary, refs=refs,
                        frame_delta=frame_delta, at=at)


def file_manual_edit_reason(review_dir: str, final: str, *,
                            manual_edit_id: Optional[str] = None,
                            editor_change_id: Optional[str] = None,
                            reason: str,
                            note_id: Optional[str] = None,
                            at: Optional[str] = None) -> Optional[dict]:
    """File the captain's reason for a detected manual edit. Never raises.

    The reason is the captain's own words, filed at the next interaction
    (the review channel's prompt, the run summary's "intent unknown"
    list). History is append-only and idempotent by entry id, so the
    reason is a NEW entry referencing the edit it explains - never a
    rewrite of the detection entry, which stays exactly as measured.

    The link is the manual edit entry's id, or the `editor_change_id`
    the replace guard recorded against it: either resolves the edit the
    reason explains. `note_id` links the answered note that carried
    the reason, so the note feed and the edit history are one account.

    Filing is instrumentation, not the edit: a reason that cannot be
    filed is said on stderr and the edit stands without it, exactly as
    a Ren act that cannot be recorded never fails a build.
    """
    reason = str(reason or "").strip()
    if not reason:
        return None
    if not manual_edit_id and not editor_change_id:
        return None
    refs: dict = {"reason": reason}
    if manual_edit_id:
        refs["manual_edit_id"] = str(manual_edit_id)
    if editor_change_id:
        refs["editor_change_id"] = str(editor_change_id)
    if note_id:
        refs["note_id"] = str(note_id)
    try:
        return record_entry(review_dir, final, actor=ACTOR_CAPTAIN,
                            act=ACT_MANUAL_EDIT_REASON,
                            summary=f"captain's reason for manual edit: "
                                    f"{reason}",
                            refs=refs, at=at)
    except Exception as exc:  # noqa: BLE001 - the contract is never-fail
        print(f"  edit history reason unrecorded for {final}: {exc!r} - "
              f"the edit stands without it", file=sys.stderr)
        return None


def reason_for(entries: Sequence[Mapping],
               manual_edit_id: Optional[str]) -> Optional[str]:
    """The reason filed for one manual edit entry, or None.

    The entry's own `refs.reason` first (known at detection time), then
    the newest `manual_edit_reason` entry filed against it - by the
    entry's id, or by the `editor_change_id` the entry carries. None
    anywhere is honestly None: an unexplained edit stays unexplained.
    """
    target = str(manual_edit_id or "")
    if not target:
        return None
    for entry in entries or ():
        if (entry.get("act") == ACT_MANUAL_EDIT
                and str(entry.get("id") or "") == target):
            reason = (entry.get("refs") or {}).get("reason")
            if reason:
                return str(reason)
    for entry in reversed(list(entries or ())):
        if entry.get("act") != ACT_MANUAL_EDIT_REASON:
            continue
        refs = entry.get("refs") or {}
        if (str(refs.get("manual_edit_id") or "") == target
                or str(refs.get("editor_change_id") or "") == target):
            reason = refs.get("reason")
            if reason:
                return str(reason)
    return None


def unexplained_manual_edits(review_dir: str, final: str) -> list[dict]:
    """Detected manual edits with no reason filed. Never raises.

    The run summary's "intent unknown" list: a detected edit is a fact
    without a motive until the captain's words are filed against it.
    Recorded and derived entries both count; an entry explained by a
    reason filed against its id or its `editor_change_id` is not
    unexplained. An unreadable history reads as empty, never as clean.
    """
    try:
        entries = history_for(review_dir, final)
    except Exception:  # noqa: BLE001 - an unreadable history reads as empty
        return []
    out = []
    for entry in entries:
        if entry.get("act") != ACT_MANUAL_EDIT:
            continue
        if reason_for(entries, entry.get("id")):
            continue
        editor_change_id = (entry.get("refs") or {}).get("editor_change_id")
        if editor_change_id and reason_for(entries, editor_change_id):
            continue
        out.append(entry)
    return out


def intent_unknown_lines(review_dir: str, final: str) -> list[str]:
    """One "intent unknown" line per unexplained detected edit.

    The prompt at the next interaction: the run summary and the review
    channel read this to ask the captain why he made the change, so a
    later run does not re-derive a plan that contradicts his unrecorded
    intent. Each line names the reel, what changed, and the history
    entry the reason will be filed against.
    """
    lines = []
    for entry in unexplained_manual_edits(review_dir, final):
        lines.append(
            f"intent unknown: {final} - "
            f"{entry.get('summary', 'manual edit')} "
            f"(history entry {entry.get('id')}) - why did the captain "
            f"make this change?")
    return lines


def _project_folder_for(review_dir: str) -> str:
    """The project folder owning `review_dir` (or itself in tests)."""
    path = Path(str(review_dir))
    if path.name == "review" and path.parent.name == "pipeline_output":
        return str(path.parent.parent)
    return str(review_dir)


def _last_ren_digest(entries: Sequence[Mapping]) -> Optional[str]:
    """The rows digest Ren last left on this reel, newest first."""
    for entry in reversed(list(entries)):
        refs = entry.get("refs") or {}
        if (entry.get("actor") == ACTOR_REN
                and refs.get("rows_digest_after")):
            return str(refs["rows_digest_after"])
    return None


def ren_recorded_digest(review_dir: str, final: str) -> Optional[str]:
    """What Ren last recorded on `final`, from every record that says.

    The history's own Ren entries first; then the versions ledger's
    newest live act (`reel_versions.latest_act` rows_digest); then the
    head of `ren_timeline_snapshots`. The first answer found wins -
    each names what Ren left, and absence everywhere is honestly
    absence rather than an invented baseline.
    """
    entries = recorded_history(review_dir, final)
    digest = _last_ren_digest(entries)
    if digest:
        return digest
    try:
        from library.tools.versions import reel_versions

        act = reel_versions.latest_act(
            _project_folder_for(review_dir), str(final))
        if act and act.get("rows_digest"):
            return str(act["rows_digest"])
    except Exception:  # noqa: BLE001 - fall through to snapshots
        pass
    try:
        from library.tools.plan_provenance import read_provenance

        doc = read_provenance(str(review_dir)) or {}
        table = doc.get("ren_timeline_snapshots") or {}
        head = table.get(str(final)) or {}
        if head.get("sha256"):
            return str(head["sha256"])
    except Exception:  # noqa: BLE001 - absence is absence
        pass
    return None


def detect_manual_edit(review_dir: str, final: str,
                       live_rows_digest: str, *, summary: str,
                       frame_delta: Optional[int] = None,
                       editor_change_id: Optional[str] = None) -> Optional[dict]:
    """File the captain's edit when live differs from Ren's last record.

    Compares `live_rows_digest` - measured by the caller through an
    existing read path - against what Ren last recorded. Equal digests
    mean nothing to file (returns None). A difference the history's
    head already files (same after-digest) is not filed twice. Anything
    else is appended as a `captain` / `manual_edit` entry and returned.

    Read-only against Resolve: this writes history, never timelines.
    """
    live_rows_digest = str(live_rows_digest or "")
    if not live_rows_digest:
        return None
    expected = ren_recorded_digest(review_dir, final)
    if expected is not None and expected == live_rows_digest:
        return None
    head_entries = recorded_history(review_dir, final)
    for entry in reversed(head_entries):
        refs = entry.get("refs") or {}
        if (entry.get("actor") == ACTOR_CAPTAIN
                and entry.get("act") == ACT_MANUAL_EDIT
                and refs.get("rows_digest_after") == live_rows_digest):
            return entry
    return record_manual_edit(
        review_dir, final, summary=summary, frame_delta=frame_delta,
        rows_digest_before=expected, rows_digest_after=live_rows_digest,
        editor_change_id=editor_change_id)


def last_ren_act(entries: Sequence[Mapping]) -> Optional[dict]:
    """The newest Ren entry. Manual edits never count as Ren acts."""
    for entry in reversed(list(entries)):
        if entry.get("actor") == ACTOR_REN:
            return entry
    return None


def attribute_plan_mismatch(entries: Sequence[Mapping],
                            delta_frames: Optional[int] = None
                            ) -> Optional[dict]:
    """Which recorded manual edit explains a plan/timeline mismatch.

    The newest `captain` / `manual_edit` entry recorded AFTER Ren's last
    act: anything Ren placed afterwards already supersedes it, and an
    edit from before the build cannot explain a mismatch with that
    build. A recorded frame delta that CONTRADICTS the mismatch (both
    measured, unequal) disqualifies - anything else attributes, naming
    the edit. Nothing recorded returns None: unexplained stays
    unexplained and still refuses.

    When the edit carries the captain's reason, the entry is returned
    as a COPY with `reason` attached - the caller cites the captain's
    own words when it reports the edit, instead of a fact with no
    motive. The entry in the history is never rewritten.
    """
    entries = list(entries or ())
    ren = last_ren_act(entries)
    ren_at = str((ren or {}).get("at") or "")
    for entry in reversed(entries):
        if (entry.get("actor") != ACTOR_CAPTAIN
                or entry.get("act") != ACT_MANUAL_EDIT):
            continue
        if ren is not None and str(entry.get("at") or "") <= ren_at:
            continue
        recorded_delta = entry.get("frame_delta")
        if (delta_frames is not None and recorded_delta is not None
                and int(recorded_delta) != int(delta_frames)):
            continue
        reason = reason_for(entries, entry.get("id"))
        if reason:
            return dict(entry, reason=reason)
        return entry
    return None


def record_drift_findings(review_dir: str, drift_report: Mapping) -> int:
    """File one manual-edit entry per drifted reel. Never raises.

    `ren drift` (`drift_check.check_project`) compares each reel's
    build snapshot against its live self-read and REPORTS - which left
    every detection without a trace. This files the trace: one
    `captain` / `manual_edit` entry per reel the comparison proves
    moved, with the drift line as what changed. A reel whose head
    entry already files the identical line is not filed twice.
    Unit-epoch readings (the numbers are a unit conversion, not a
    move) and unreadable reels file nothing: unproven is unfiled.
    Returns the number of entries added.
    """
    added = 0
    try:
        reels = (drift_report or {}).get("reels") or {}
        for name in sorted(reels):
            compared = reels[name]
            if not isinstance(compared, dict):
                continue
            if not compared.get("compared") or not compared.get("drifted"):
                continue
            summary = ("drift check: " + str(compared.get("line") or
                       "the live timeline no longer holds what its "
                       "build snapshot wrote"))
            head = recorded_history(review_dir, str(name))
            if (head and head[-1].get("actor") == ACTOR_CAPTAIN
                    and head[-1].get("act") == ACT_MANUAL_EDIT
                    and head[-1].get("summary") == summary):
                continue
            record_manual_edit(review_dir, str(name), summary=summary)
            added += 1
    except Exception as exc:  # noqa: BLE001 - history never fails drift
        print(f"  edit history unrecorded from drift: {exc!r}",
              file=sys.stderr)
    return added


def backfill(review_dir: str, final: str,
             project_folder: Optional[str] = None) -> int:
    """Derive history entries from the records predating this module.

    Reads `reel_versions` (build/touch/undo/rollback with their times,
    row digests, journals and plan moments), the provenance build stamps
    (`built_at_reels`, `built_with`, caption and footage-binding hashes
    as the plan version), and the editor-change table (manual edits with
    their carried/superseded/restored status). Every derived entry is
    marked `backfilled: true` and idempotent: re-running adds nothing.

    Returns the number of entries added.
    """
    review_dir = str(review_dir)
    folder = (str(project_folder) if project_folder is not None
              else _project_folder_for(review_dir))
    added = 0
    existing_ids = {e.get("id") for e in recorded_history(review_dir, final)}

    def file_entry(**fields):
        nonlocal added
        entry = record_entry(review_dir, final, **fields, backfilled=True)
        if entry["id"] not in existing_ids:
            existing_ids.add(entry["id"])
            added += 1
        return entry

    try:
        from library.tools.versions import reel_versions

        versions = reel_versions.versions_of(folder, str(final))
    except Exception:  # noqa: BLE001 - backfill what can be read
        versions = []
    plan_version = None
    try:
        from library.tools.plan_provenance import read_provenance

        provenance = read_provenance(review_dir) or {}
        built_at = (provenance.get("built_at_reels") or {}).get(str(final))
        built_with = (provenance.get("built_with") or {}).get(str(final))
        caption = (provenance.get("caption_hashes") or {}).get(str(final))
        binding = ((provenance.get("footage_binding_hashes") or {}).get(
            str(final)))
        plan_version = {
            "plan_content_hash": provenance.get("plan_content_hash"),
            "caption_hash": caption,
            "footage_binding_hash": binding,
            "built_with": built_with,
            "built_at": built_at,
        }
    except Exception:  # noqa: BLE001 - versions still backfill
        provenance = {}
    for version in versions:
        kind = str(version.get("version_kind") or version.get("kind") or "")
        act = { "build": ACT_BUILD, "touch": ACT_TOUCH,
                "undo": ACT_UNDO, "rollback": ACT_ROLLBACK }.get(kind)
        if act is None:
            continue
        refs: dict = {"version": version.get("version")}
        if version.get("rows_digest"):
            refs["rows_digest_after"] = str(version["rows_digest"])
        if version.get("journal"):
            refs["journal"] = str(version["journal"])
        if version.get("round") is not None:
            refs["round"] = version["round"]
        summary_bits = [f"Ren {act} (version {version.get('version')})"]
        if version.get("round") is not None:
            summary_bits.append(f"round {version['round']}")
        if version.get("journal"):
            summary_bits.append(f"journal {version['journal']}")
        file_entry(actor=ACTOR_REN, act=act,
                   summary=" ".join(summary_bits),
                   plan_version=plan_version if act in (
                       ACT_BUILD, ACT_ROLLBACK) else None,
                   refs=refs, at=version.get("at"))
    changes = ((provenance.get("unattributed_editor_changes") or {}).get(
        str(final)) or [])
    for record in changes:
        if not isinstance(record, dict) or not record.get("id"):
            continue
        file_entry(
            actor=ACTOR_CAPTAIN, act=ACT_MANUAL_EDIT,
            summary=(f"captain's manual edit recorded at "
                     f"{record.get('recorded_at')} "
                     f"(status: {record.get('status')})"),
            refs={"editor_change_id": str(record["id"])},
            at=record.get("recorded_at"))
    return added
