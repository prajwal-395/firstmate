"""UNDO: a touch is reversed IN PLACE from its journal; a rebuild is rolled back to its version.

In-place for touches, versions for rebuilds (captain, 2026-09-23, D5).

One verb, two mechanisms
------------------------
`ren undo <project> [reel]` (`undo`) reverses the newest act nothing has
reversed yet, read off the reel version ledger (`versions.reel_versions`;
`undo_stack` and `render_stack` list it) - or the act `--act <id>` names,
addressed by a touch's journal entry id, a rebuild's version number, or
a history entry id (`resolve_act`):

- a TOUCH (`reel_touchup.apply_touchup`, one reel or `--all-reels`) is
  reversed IN PLACE on the same timeline from its journal entry
  (`undo_touch`).  An `--all-reels` touch is one act across reels: its
  entries share a `batch` and are reversed together.
- a REBUILD is rolled back to the version before it (`rollback_rebuild`):
  that version's plan moment is restored into `reel_proposals_v2.json`
  (`restore_plan_moment`), the reel is rebuilt through the reels
  process, the touches that stood on the earlier version are re-applied
  from their journals, and the result is compared with the version it
  was meant to reach - `RollbackDiverged` when it does not.  This is the
  one restore route: the Resolve project is derived (declarations plus a
  rebuild, never a restored binary).

A named act is reversed only when the live timeline still reads as that
act left it (`TimelineMovedSinceTouch`): a later act standing on top of
it is caught by that check, not by the address. The check is the same
for any addressed act - a touch's `undo_in_place` projection comparison,
a rebuild's rows-digest comparison against the addressed version.

REDO
----
`ren redo <project> [reel] --act <id>` re-applies a reversed act: a touch
is re-run from its journal's spec (`redo_touch`), a rolled-back rebuild
is re-run from the plan moment of the act that had been rolled back
(`redo_rebuild`). The re-applied act is live again (`mark_redone` clears
its `undone_by`), and a `redo` version names the act it re-applied.

The journal entry
-----------------
Written BEFORE the touch changes anything (`open_entry`), under
`pipeline_output/review/undo/<entry id>/` - text only (JSON and exported
`.comp` Lua), so it is versioned with the project:

    entry.json
      format        "undo_journal/1"
      id, reel, final, resolve_project, batch, spec, gate_class
      status        open -> applied | failed -> undone | undo_failed
      before        {"tracks": reel_read.read_tracks(approved timeline)}
                    - every item's media, span, row, transform, comp
                    windows, clip colour and markers, read with the
                    timeline CURRENT so the transform is not
                    cursor-scaled
      removed       per item the touch deletes without re-placing it
                    (`remove_overlay`, the old side of `swap_pixels`):
                    its `composed_edit.capture_item`, comps exported to
                    `comps/` with their media windows
      fusion_manifest  a copy of the recorded manifest, when the touch
                    changes a played length
      after         {"tracks": ...} of the promoted timeline, written
                    before the replaced generation is deleted
      version       the reel version the touch produced
      undo          the undo's receipt, once reversed
      receipt       the touch's own account of itself (Lane E2, R6 -
                    folded in from the retired standalone
                    `touchup_*.json` file)

No live copy is kept
--------------------
With `before`, `removed` and `after` on disk, a touch DELETES the
replaced generation (`reel_retirement.delete_backups`) - once `after` is
written, never before.  A reel carrying a sign-off still retires, as
promotion does.  A colour grade on an item the touch deletes outright
cannot be journalled, so `reel_touchup` refuses a graded remove (and a
graded swap) and requires a rebuild.

How the undo runs, in place
---------------------------
1. REFUSE by name (`TimelineMovedSinceTouch`) when the live timeline no
   longer reads as `after` - the projection below, row by row.
   Reversing the touch over a later change would destroy that work.
2. Plan the inverse by DIFF, not per op (`plan_inverse`): items only
   `after` has are the touch's; items only `before` has are what it
   took.  A pair on one row playing the same file from the same source
   offset is one item the touch moved or retimed - re-placed at its old
   span through `composed_edit.apply_composed_edit`, with its grade
   copied from a transient reference duplicate of the live timeline.  An
   unpaired `after` item is deleted; an unpaired `before` item is
   re-placed from its `removed` capture and restored by
   `composed_edit.restore_item`.  A transform or comp the touch changed
   on an item it did not move is written back in place.
3. Carry the captain's clip markers off every re-placed item.
4. VERIFY by re-reading: the live timeline must read as `before`, or
   the undo raises `UndoNotVerified` and the reference duplicate stays
   standing, named, holding the post-touch state.
5. Delete the reference duplicate, mark the journal `undone`, append an
   `undo` version and close the carried signature.

The projection
--------------
What "reads as" compares (`projection`, `projection_diff`), per row in
record order: span, source offset, source file, name, the transform
(every key `GetProperty()` returns except the read-only ones), each
comp's media-window frames, and clip colour.  Not compared: unique ids
(a re-placed item is a new object), markers (carried, and a note typed
after the touch must not block its undo), CDL and flags (a touch never
carries either).

`tests/unit/resolve/test_undo_journal.py`.

What a touch-up and a promotion left behind before this module, and the
ruling that replaced it: docs/evidence/undo_journal.md.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.tools import composed_edit as _ce
from library.tools.ren_refusal import RenRefusal

JOURNAL_FORMAT = "undo_journal/1"
JOURNAL_DIRNAME = "undo"
ENTRY_FILENAME = "entry.json"

STATUS_OPEN = "open"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_UNDONE = "undone"
STATUS_UNDO_FAILED = "undo_failed"

_ACCEPTED_TOUCH_OVERRIDE_OPS = frozenset({
    "move", "swap_pixels", "add_overlay", "remove_overlay", "retime",
    "resize_still", "set_properties", "add_row", "set_enabled",
    "entry_motion",
})


def accepted_edit_record(project_folder: str, entry: Mapping) -> dict:
    """Build the persistent semantic record for one accepted touch."""
    from library.tools import plan_provenance, timeline_diff

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    provenance = plan_provenance.read_provenance(review_dir) or {}
    spec = entry.get("spec") or {}
    operations = []
    for operation in spec.get("edits") or ():
        op = str(operation["op"])
        if op not in _ACCEPTED_TOUCH_OVERRIDE_OPS:
            raise UndoRefused(
                f"accepted touch {entry['id']!r} contains unclassified "
                f"operation {op!r}; its rebuild policy is unknown",
                "the edit cannot be safely represented as a declaration "
                "update or a versioned override",
                "classify this operation before accepting the touch")
        operations.append({"operation": dict(operation),
                           "policy": "versioned_override"})
    return {
        "format": "accepted_reel_edit/1",
        "intent": str(spec.get("note") or "").strip(),
        "operations": operations,
        "recorded_edit_program": dict(spec),
        "source_declaration": {
            "plan_content_hash": provenance.get("plan_content_hash"),
            "plan_path": provenance.get("plan_path"),
        },
        "changes": timeline_diff.diff_timelines(
            (entry.get("before") or {}).get("tracks") or (),
            (entry.get("after") or {}).get("tracks") or ()),
        "verified_from_journal": entry["id"],
    }


class UndoRefused(RenRefusal):
    """An undo that declined before changing anything. Always by name."""

    USER_MESSAGE = ("I couldn't undo this as asked, so nothing changed. "
                    "Check the issue below before asking again.")


class TimelineMovedSinceTouch(UndoRefused):
    """The live timeline no longer reads as the touch left it."""

    USER_MESSAGE = ("The timeline changed since this touch was made, so I "
                    "held off undoing to keep your recent work safe. "
                    "Should I undo against the updated cut?")

    RECOVERY_CLASS = "needs_question"


class UndoNotVerified(RuntimeError):
    """An undo that ran and does not read back as the prior state."""


class RollbackDiverged(RuntimeError):
    """A rolled-back rebuild that does not read as the version it targeted."""


class CommitRecordError(RuntimeError):
    """A creative commit that could not be recorded.

    Non-fatal to the touch that caused it - the touch's own journal is
    the way back - but it is RAISED, not swallowed, so the caller can
    put the reason on the receipt and a missing commit is visible.
    """


class MergeConflict(RuntimeError):
    """Two creative changes cannot be safely composed.

    Conflicts identify the target and affected property where possible.
    Unidentifiable or ambiguous legacy changes are refused instead of
    being resolved by clip name.
    """


class BranchRefused(RuntimeError):
    """A branch that could not be recorded or proven.

    Non-fatal to the touch that caused it, but RAISED, not swallowed,
    so the caller can put the reason on the receipt.
    """


class TagRefused(RuntimeError):
    """A tag that could not be recorded or read.

    Raised, not swallowed, so the caller can say which name and which
    target failed: a dangling or silently-moved tag never lands.
    """


class RevertRefused(RuntimeError):
    """A commit that could not be reverted.

    Raised, not swallowed, naming the commit and what stands in the
    way: a later commit that changed the same clips, a change the
    record cannot invert, or a commit outside its timeline's line.
    """


class CherryPickRefused(RuntimeError):
    """A commit whose changes could not be replayed.

    Raised, not swallowed, naming the commit and what stands in the
    way: a later commit that changed the same clips, a merge (pick the
    brought-in commits instead), or a commit outside its timeline's
    line.
    """


# ── Where entries live ───────────────────────────────────────────


def journal_root(project_folder) -> str:
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        JOURNAL_DIRNAME)


def entry_dir(project_folder, entry_id: str) -> str:
    return os.path.join(journal_root(project_folder), entry_id)


def _entry_path(project_folder, entry_id: str) -> str:
    return os.path.join(entry_dir(project_folder, entry_id), ENTRY_FILENAME)


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40]


def new_entry_id(final: str) -> str:
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_slug(final)}-{uuid.uuid4().hex[:6]}"


def new_batch_id() -> str:
    return "batch-" + uuid.uuid4().hex[:10]


def read_entry(project_folder, entry_id: str) -> dict:
    path = _entry_path(project_folder, entry_id)
    try:
        with open(path, encoding="utf-8") as handle:
            entry = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise UndoRefused(
            f"undo journal entry {entry_id!r} could not be read at {path}",
            f"({unreadable})",
            "recover the journal entry from backup, or list what is "
            "left with `ren undo <project> --list`") from unreadable
    if entry.get("format") != JOURNAL_FORMAT:
        raise UndoRefused(
            f"{path} is {entry.get('format')!r}, not {JOURNAL_FORMAT!r}",
            "an entry in a foreign format cannot be trusted",
            "recover the journal entry from backup, or list what is "
            "left with `ren undo <project> --list`")
    return entry


def write_entry(project_folder, entry: Mapping) -> str:
    from library.tools.versions import store

    return store.write_record(_entry_path(project_folder, entry["id"]),
                              entry, prefix=".undo-")


def list_entries(project_folder) -> list:
    """Every entry, oldest first."""
    root = journal_root(project_folder)
    if not os.path.isdir(root):
        return []
    entries = []
    for name in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, name, ENTRY_FILENAME)):
            entries.append(read_entry(project_folder, name))
    return entries


# ── Creative commits: git semantics over creative state ────────────
#
# A commit records WHAT changed and WHY, with the identity the captain's
# spec names: parent, intent, changes, agent, evaluation, artifacts. The
# id is the SHA-256 of the commit's canonical JSON content - the same
# identity pattern `plan_provenance.record_timeline_snapshot` uses - so
# two commits with the same content are the same commit, and a changed
# field changes the id.
#
# The changes list is a `timeline_diff.diff_timelines` output: typed
# changes expressed as edit_graph mutations where the change is a graph
# mutation. The commit is the unit a branch, a diff, a revert and a
# cherry-pick are built on; this module records and reads it, and the
# touch-up path (`reel_touchup`) is its first caller.

COMMIT_FORMAT = "creative_commit/1"
COMMIT_DIRNAME = "commits"
COMMIT_FILENAME = "commit.json"


def commit_root(project_folder) -> str:
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        COMMIT_DIRNAME)


def _commit_path(project_folder, commit_id: str) -> str:
    return os.path.join(commit_root(project_folder), commit_id,
                        COMMIT_FILENAME)


def _commit_id(content: Mapping) -> str:
    """SHA-256 of the commit's canonical JSON content.

    The id covers exactly what the commit says - parent, intent,
    changes, agent, evaluation, artifacts - and nothing else: not the
    id itself, not a timestamp. Two commits with the same content are
    the same commit; a changed field changes the id. This is the
    `plan_provenance` identity pattern (SHA-256 of canonical JSON).
    """
    import hashlib

    payload = json.dumps(dict(content), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False,
                         default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_commit(project_folder, *, parent: str | None,
                  intent: str, changes: Sequence[Mapping],
                  agent: str, timeline: str = "",
                  evaluation: Mapping | None = None,
                  artifacts: Mapping | None = None,
                  second_parent: str | None = None,
                  revision_id: str | None = None) -> dict:
    """Record a creative commit. Returns the commit with its id.

    The commit is written atomically under
    `pipeline_output/review/commits/<id>/commit.json` - versioned text,
    like the undo journal entries, so it diffs in the project's store.
    A commit whose content hashes to an id already on disk is the SAME
    commit: the existing record is returned, not a second one, so a
    re-recorded touch never forks history.

    `timeline` is the timeline (reel final name) the commit is FOR. It is
    part of the commit's content and therefore of its id: a commit on
    Reel 03 is a different commit from one on Reel 10, and a branch is
    the timeline's own chain, not one global chain.

    `second_parent` is the second parent of a MERGE commit - the branch
    that was merged in. It is present only on merge commits, so a regular
    commit's id is unchanged by it: two commits with the same content are
    the same commit, and a merge commit is a different commit from a
    regular one.
    """
    from library.tools.versions import store

    content = {
        "parent": parent,
        "intent": intent,
        "changes": [dict(change) for change in (changes or ())],
        "agent": agent,
        "timeline": timeline,
        "evaluation": dict(evaluation or {}),
        "artifacts": dict(artifacts or {}),
    }
    materialization = content["artifacts"].get("materialization")
    commit_status = {"semantic_only": "prepared",
                     "verified": "verified"}.get(materialization, "recorded")
    if second_parent is not None:
        content["second_parent"] = second_parent
    commit_id = _commit_id(content)
    path = _commit_path(project_folder, commit_id)
    if os.path.isfile(path):
        # The same content is already recorded: return it as it stands,
        # with the sequence it was given when it first landed. A
        # re-recorded touch must not fork history, and must not renumber
        # the commit it duplicates.
        return read_commit(project_folder, commit_id)
    commit = dict(content)
    commit["status"] = commit_status
    commit["format"] = COMMIT_FORMAT
    commit["id"] = commit_id
    commit["recorded_at"] = _now()
    # The sequence is the commit's order on this branch, and it is NOT
    # part of the id content: two commits with the same content are the
    # same commit, but a branch needs a stable order to answer "what did
    # this branch change". A second-resolution timestamp cannot give it -
    # two commits recorded in the same second tie - so the sequence is
    # counted from the commits already on disk.
    sequence = 1
    for existing in list_commits(project_folder):
        sequence = max(sequence, int(existing.get("sequence") or 0) + 1)
    commit["sequence"] = sequence
    from library.tools import creative_revision
    parent_revision = None
    if parent:
        parent_record = read_commit(project_folder, str(parent))
        parent_revision = parent_record.get("revision_id")
    second_parent_revision = None
    if second_parent:
        second_parent_record = read_commit(project_folder, str(second_parent))
        second_parent_revision = second_parent_record.get("revision_id")
    existing_revision = (creative_revision.get(project_folder, revision_id)
                         if revision_id else None)
    revision = creative_revision.create(
        project_folder, revision_id=revision_id,
        **({} if existing_revision else {
            "parents": [revision for revision in
                        (parent_revision, second_parent_revision)
                        if revision],
            "author": str(agent), "intent": str(intent),
            "operations": [dict(change) for change in changes or ()
                           if isinstance(change, Mapping)],
            "affected_entities": [str(timeline)] if timeline else (),
            "status": commit_status,
        }),
        refs={"creative_commit": {"commit_id": commit_id}})
    commit["revision_id"] = revision["revision_id"]
    if materialization == "verified":
        commit["result_revision"] = revision["revision_id"]
    store.write_record(path, commit, prefix=".commit-")
    return commit


def read_commit(project_folder, commit_id: str) -> dict:
    path = _commit_path(project_folder, commit_id)
    try:
        with open(path, encoding="utf-8") as handle:
            commit = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise UndoRefused(
            f"creative commit {commit_id!r} could not be read at {path}",
            f"({unreadable})",
            "recover the commit from the project's git history, or list "
            "what is recorded with `ren commits <project> --list`"
        ) from unreadable
    if commit.get("format") != COMMIT_FORMAT:
        raise UndoRefused(
            f"{path} is {commit.get('format')!r}, not {COMMIT_FORMAT!r}",
            "a commit in a foreign format cannot be trusted",
            "recover the commit from the project's git history")
    return commit


def list_commits(project_folder) -> list:
    """Every commit, in sequence order - the order they were recorded.

    The sequence, not the timestamp, is the order: two commits recorded
    in the same second tie on `recorded_at`, and a branch's history must
    read in the order it was made.
    """
    root = commit_root(project_folder)
    if not os.path.isdir(root):
        return []
    commits = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, COMMIT_FILENAME)
        if os.path.isfile(path):
            commits.append(read_commit(project_folder, name))
    return sorted(commits, key=lambda entry: int(entry.get("sequence") or 0))


def latest_commit_for_timeline(project_folder, timeline: str) -> dict | None:
    """The most recent commit FOR one timeline, by sequence.

    A branch is the timeline's own chain: a touch on Reel 10 after one
    on Reel 03 stands on Reel 10's last commit, not on Reel 03's. Taking
    the parent from the whole project's latest commit would fork one
    timeline's history into another's.
    """
    commits = [c for c in list_commits(project_folder)
               if c.get("timeline") == timeline
               and c.get("status", "recorded") != "prepared"]
    return commits[-1] if commits else None


def record_touch_commit(project_folder, *, journal: Mapping,
                        spec: Mapping, qualification=None,
                        version: int | None = None) -> dict:
    """Record the creative commit a touch-up is, from its closed journal.

    The touch's journal already holds the before and after track reads;
    this diffs them (`timeline_diff.diff_timelines`) into the commit's
    changes and records the commit with the touch's intent, agent,
    evaluation and artifacts. The parent is the most recent commit FOR
    THIS TIMELINE, so a chain of touches on one reel is a chain of
    commits, and two reels touched in turn are two independent chains.

    Raises `CommitRecordError` on failure. The caller catches it: the
    commit is non-fatal to the touch (the journal is the way back), but
    the failure is visible - printed to stderr and put on the receipt -
    never swallowed in silence.
    """
    from library.tools import timeline_diff

    before_tracks = (journal.get("before") or {}).get("tracks") or []
    after_tracks = (journal.get("after") or {}).get("tracks") or []
    changes = timeline_diff.diff_timelines(before_tracks, after_tracks)
    changes = _attach_stable_entities(changes, before_tracks, after_tracks)
    summary = timeline_diff.summarize(changes)
    edits = list((spec or {}).get("edits") or ())
    intent = (str((spec or {}).get("note") or "").strip()
              or _intent_from_edits(edits, summary))
    timeline = str(journal.get("final") or "")
    parent_commit = latest_commit_for_timeline(project_folder, timeline)
    parent = parent_commit.get("id") if parent_commit else None
    evaluation = {}
    if qualification is not None:
        evaluation = {
            "gate_class": getattr(qualification, "gate_class", ""),
            "cost": getattr(qualification, "cost_statement", ""),
        }
    artifacts = {
        "journal": journal.get("id"),
        "reel": timeline,
    }
    if journal.get("fps"):
        artifacts["fps"] = journal["fps"]
    if version is not None:
        artifacts["version"] = version
    return record_commit(
        project_folder, parent=parent, intent=intent,
        changes=changes, agent="reel_touchup", timeline=timeline,
        evaluation=evaluation, artifacts=artifacts,
        revision_id=journal.get("revision_id"))


def _attach_stable_entities(changes: Sequence[Mapping], *track_sets) -> list[dict]:
    """Carry Resolve item ids into measured commit changes when unique.

    The timeline diff remains the source of changed properties. This
    adapter adds the live item identity from the before/after reads so
    later merges do not have to treat a media name as an entity id.
    """
    by_row_name: dict[tuple[str, str], list[Mapping]] = {}
    for tracks in track_sets:
        for track in tracks or ():
            kind = str(track.get("type") or "")
            index = track.get("index")
            row = ("V" if kind.lower().startswith("v") else "A") + str(index)
            for clip in track.get("clips") or ():
                if clip.get("unique_id"):
                    by_row_name.setdefault(
                        (row, str(clip.get("name") or "")), []).append(clip)

    enriched = []
    for change in changes:
        result = dict(change)
        row, name = str(change.get("row") or ""), str(change.get("item") or "")
        states = [state for state in (change.get("before"), change.get("after"))
                  if isinstance(state, Mapping)]
        candidates = by_row_name.get((row, name), ())
        matched = []
        for clip in candidates:
            for state in states:
                pairs = (("start", "record_in"), ("end", "record_out"),
                         ("source_in", "source_in_frame"),
                         ("source_out", "source_out_frame"))
                if all(state.get(state_key) is None
                       or clip.get(clip_key) == state.get(state_key)
                       for state_key, clip_key in pairs):
                    matched.append(str(clip["unique_id"]))
                    break
        identities = set(matched)
        if len(identities) == 1:
            result["entity_id"] = next(iter(identities))
        enriched.append(result)
    return enriched


def _intent_from_edits(edits: Sequence[Mapping], summary: Mapping) -> str:
    """The commit's intent: the touch's own note, or its edits summarized.

    A touch spec carries no prose intent, so the intent is derived from
    what the touch does - the diff summary ("2 moves, 1 trim") plus the
    edit ops. This is a mechanical summary of measured changes, not a
    creative judgement: the captain's note, when the spec carries one,
    wins.
    """
    parts = [f"{count} {op}" for op, count in sorted(summary.items())]
    if parts:
        return "touch: " + ", ".join(parts)
    ops = sorted({str(edit.get("op") or "") for edit in edits if edit.get("op")})
    if ops:
        return "touch: " + ", ".join(ops)
    return "touch"


def revert_commit(project_folder, commit_id: str, note: str = "",
                  dest_tracks=None) -> dict:
    """Revert a commit: record its inverse as a new commit. Returns it.

    The inverse is `timeline_diff.invert_changes` of the target's
    changes - a move moves back, an add removes, a trim restores. The
    revert stands on the timeline's head, so history only moves
    forward: nothing is rewritten, and the reverted commit stays
    readable beside its revert.

    When `dest_tracks` - the destination timeline's current track
    reads - is supplied, every inverse change must still hold there
    (`timeline_diff.changes_hold`): the inverse restores what the
    target left, so a destination that moved on outside the record
    is refused naming it, and a revert proven against a stale chain
    cannot silently clobber a timeline that is no longer what the
    target left.

    Three refusals, all `RevertRefused` and all by name. A target
    outside its timeline's line (it is not the head or an ancestor of
    it) cannot be reverted - the revert would stand on a history that
    does not contain it. A target whose clips later commits also
    changed is refused naming the clips: restoring the old span would
    silently undo the later touch. A target with a change the record
    cannot invert (a whole-row add/remove names a clip count, not the
    clips) is refused naming the change, so a revert never claims a
    clean undo while dropping one.
    """
    from library.tools import timeline_diff

    target = read_commit(project_folder, commit_id)
    timeline = target.get("timeline") or ""
    if target.get("second_parent"):
        raise RevertRefused(
            f"commit {commit_id[:12]} is a merge - its changes are the "
            f"union of both sides, so inverting them would silently "
            f"revert the main line too; revert the commits it brought "
            f"in instead")
    head = latest_commit_for_timeline(project_folder, timeline)
    if head is None or commit_id not in _ancestor_chain(
            project_folder, head.get("id")):
        raise RevertRefused(
            f"commit {commit_id[:12]} is not on {timeline!r}'s line - "
            f"a revert stands on the timeline's head, and this commit "
            f"is not its head or an ancestor of it")
    later = _changes_since(project_folder, head.get("id"), commit_id)
    try:
        inverse = timeline_diff.invert_changes(target.get("changes") or ())
    except timeline_diff.Uninvertible as uninvertible:
        raise RevertRefused(
            f"commit {commit_id[:12]} cannot be reverted "
            f"({uninvertible})") from uninvertible
    inverse_names = {c.get("item") for c in inverse if c.get("item")}
    later_names = {c.get("item") for c in later if c.get("item")}
    conflicts = sorted(inverse_names & later_names)
    if conflicts:
        raise RevertRefused(
            f"commit {commit_id[:12]} cannot be reverted: later commits "
            f"also changed {', '.join(conflicts)} - restoring the old "
            f"spans would silently undo the later touch")
    if not inverse:
        raise RevertRefused(
            f"commit {commit_id[:12]} changed nothing - an empty revert "
            f"would rewrite no history and only pollute it")
    if dest_tracks is not None:
        try:
            problems = timeline_diff.changes_hold(inverse, dest_tracks)
        except ValueError as unreadable:
            raise RevertRefused(
                f"commit {commit_id[:12]} cannot be proven against "
                f"the destination reads ({unreadable})") from unreadable
        if problems:
            raise RevertRefused(
                f"commit {commit_id[:12]} no longer applies to the "
                f"destination reads: " + "; ".join(problems[:5]))
    intent = note or (f"revert {commit_id[:12]} "
                      f"({target.get('intent') or 'no intent'})")
    return record_commit(
        project_folder, parent=head.get("id"), intent=intent,
        changes=inverse, agent="creative_revert", timeline=timeline,
        artifacts={"reverts": commit_id})


# ── Branches and merges: the commit graph ──────────────────────────
#
# A branch is a named pointer to a commit, materialized as a duplicated
# timeline (Resolve's DuplicateTimeline - the "Resolve double"): the
# branch's timeline starts identical to its base and diverges as it is
# edited. A merge combines two commits on one timeline: each side's
# changes since their common ancestor are diffed (the commits' recorded
# `timeline_diff` changes); conflicting writes to one entity/property
# are refused. Otherwise a semantic proposal is recorded with both
# parents and remains prepared until a candidate is verified.

BRANCH_FORMAT = "creative_branch/1"
BRANCH_DIRNAME = "branches"
BRANCH_FILENAME = "branch.json"


def branch_root(project_folder) -> str:
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        BRANCH_DIRNAME)


def _branch_path(project_folder, name: str) -> str:
    # A branch name is an identity, never a path supplied by a caller.
    # Reject separators and dot components before joining it to the store.
    if (not isinstance(name, str) or not name.strip() or "\\" in name
            or any(part in {".", "..", ""}
                   for part in name.replace("\\", "/").split("/"))):
        raise BranchRefused(f"invalid creative branch name {name!r}")
    return os.path.join(branch_root(project_folder), name, BRANCH_FILENAME)


def create_creative_branch(project_folder, *, name: str,
                           base_revision: str,
                           semantic_changes: Sequence[Mapping] = (),
                           declarations: Mapping | None = None,
                           git_branch: str = "", author: str = "ren",
                           intent: str = "") -> dict:
    """Create a semantic branch without creating a Resolve timeline.

    The branch points at a canonical creative revision and stores only its
    semantic delta and declaration references. Resolve materialization is an
    optional, separately recorded projection; discarding it never removes the
    branch or its history.
    """
    from library.tools import creative_revision

    _branch_path(project_folder, name)  # validate before any store write
    base = creative_revision.get(project_folder, str(base_revision))
    if base is None:
        raise BranchRefused(
            f"base creative revision {base_revision!r} is not recorded")
    semantic_changes = semantic_changes or ()
    if not all(isinstance(change, Mapping) for change in semantic_changes):
        raise BranchRefused("creative branch semantic changes must be objects")
    if declarations is not None and not isinstance(declarations, Mapping):
        raise BranchRefused("creative branch declarations must be a mapping")
    changes = [dict(change) for change in semantic_changes]
    path = _branch_path(project_folder, name)
    if os.path.exists(path):
        existing = read_branch(project_folder, name)
        if any(existing.get(key) != value for key, value in (
                ("base_revision", str(base_revision)),
                ("semantic_changes", changes),
                ("declarations", dict(declarations or {})))):
            raise BranchRefused(
                f"creative branch {name!r} already exists with different content")
        return existing
    branch_revision = creative_revision.create(
        project_folder, parents=[str(base_revision)], author=author,
        intent=intent or f"branch {name}", operations=changes,
        affected_entities=(declarations or {}).get("affected_entities", ()),
        refs={"creative_branch": {"name": name,
                                  "base_revision": str(base_revision),
                                  "git_branch": str(git_branch or "")}},
        status="prepared")
    branch = {
        "format": BRANCH_FORMAT, "name": name,
        "base_revision": str(base_revision),
        "revision_id": branch_revision["revision_id"],
        "semantic_changes": changes,
        "declarations": dict(declarations or {}),
        "git_branch": str(git_branch or ""),
        "git_branches": [str(git_branch)] if git_branch else [],
        "materialization": None,
        "created_at": _now(),
    }
    from library.tools.versions import store
    store.write_record(path, branch, prefix=".branch-")
    return branch


def link_branch_git(project_folder, name: str, git_branch: str) -> dict:
    """Link a Git declaration branch to an existing creative branch."""
    branch = read_branch(project_folder, name)
    git_branches = list(branch.get("git_branches") or ())
    if git_branch not in git_branches:
        git_branches.append(str(git_branch))
    branch["git_branches"] = git_branches
    branch.setdefault("git_branch", str(git_branch))
    from library.tools import creative_revision
    revision_refs = {"git_declaration_branch": {
        "creative_branch": name, "git_branch": str(git_branch)}}
    if branch.get("revision_id"):
        creative_revision.create(
            project_folder, revision_id=str(branch["revision_id"]),
            refs=revision_refs)
    from library.tools.versions import store
    store.write_record(_branch_path(project_folder, name), branch,
                       prefix=".branch-")
    return branch


def record_branch_materialization(project_folder, name: str, *,
                                 timeline: str, timeline_id: str = "",
                                 revision_id: str = "") -> dict:
    """Attach a live Resolve projection to the semantic branch."""
    if not str(timeline or "").strip():
        raise BranchRefused("branch materialization needs a timeline name")
    branch = read_branch(project_folder, name)
    branch["materialization"] = {
        "status": "materialized", "timeline": str(timeline),
        "timeline_id": str(timeline_id or ""),
        "revision_id": str(revision_id or ""), "recorded_at": _now(),
    }
    from library.tools import creative_revision
    recorded_revision = str(revision_id or branch["revision_id"])
    creative_revision.create(
        project_folder, revision_id=recorded_revision,
        refs={"branch_materialization": {"branch": name,
                                         "materialization": dict(branch["materialization"])}},
        status="verified")
    from library.tools.versions import store
    store.write_record(_branch_path(project_folder, name), branch,
                       prefix=".branch-")
    return branch


def discard_branch_materialization(project_folder, name: str) -> dict:
    """Forget the live projection while retaining the semantic branch."""
    branch = read_branch(project_folder, name)
    materialization = dict(branch.get("materialization") or {})
    if materialization:
        materialization["status"] = "discarded"
        materialization["discarded_at"] = _now()
        branch["materialization"] = materialization
        from library.tools import creative_revision
        creative_revision.create(
            project_folder,
            revision_id=str(materialization.get("revision_id") or branch["revision_id"]),
            refs={"branch_materialization": {"branch": name,
                                             "materialization": dict(materialization)}})
    from library.tools.versions import store
    store.write_record(_branch_path(project_folder, name), branch,
                       prefix=".branch-")
    return branch


def record_branch(project_folder, *, name: str, base: str,
                  timeline: str, note: str = "",
                  source_tracks=None, branch_tracks=None,
                  actor=None) -> dict:
    """Record a branch: a named pointer to a commit. Returns the branch.

    DuplicateTimeline-backed: the branch is materialized as a duplicated
    timeline (the Resolve double), identical to its base until edited.
    The branch record names the base commit and the timeline it lives
    on, so a branch is the timeline's own line of development.

    When `source_tracks` and `branch_tracks` are both supplied, the
    branch is PROVEN against the double: `timeline_diff` between them
    must be empty. A double that already differs from its source is
    refused - a branch starts from a clean duplicate, not from a
    diverged copy. The proof is the branch's contract with the timeline
    it is materialized on.

    `actor` names the federated participant the branch runs as. A named
    actor must hold Branch on this project's roster
    (`fabric_permissions`): branching is how a participant submits
    work, so an observer may never open one. `None` (the default) is
    the local operator's own act and is never checked.
    """
    if actor is not None:
        from library.tools import fabric_permissions as _permissions
        _permissions.require(project_folder, actor, _permissions.BRANCH,
                             task=f"branch {name}")
    from library.tools.versions import store

    if source_tracks is not None and branch_tracks is not None:
        from library.tools import timeline_diff
        changes = timeline_diff.diff_timelines(source_tracks, branch_tracks)
        if changes:
            summary = ", ".join(
                f"{change.get('op')} {change.get('item')!r}"
                for change in changes[:5])
            raise BranchRefused(
                f"the double for branch {name!r} already differs from its "
                f"source ({summary}) - a branch starts from a clean "
                f"duplicate, not a diverged copy")
    branch = {
        "format": BRANCH_FORMAT,
        "name": name,
        "base": base,
        "timeline": timeline,
        "note": note,
        "created_at": _now(),
    }
    store.write_record(_branch_path(project_folder, name), branch,
                       prefix=".branch-")
    return branch


def read_branch(project_folder, name: str) -> dict:
    path = _branch_path(project_folder, name)
    try:
        with open(path, encoding="utf-8") as handle:
            branch = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise BranchRefused(
            f"branch {name!r} could not be read at {path} ({unreadable})"
        ) from unreadable
    if branch.get("format") != BRANCH_FORMAT:
        raise BranchRefused(
            f"{path} is {branch.get('format')!r}, not {BRANCH_FORMAT!r}")
    return branch


def list_branches(project_folder) -> list:
    """Every recorded branch, by name."""
    root = branch_root(project_folder)
    if not os.path.isdir(root):
        return []
    branches = []
    for path in sorted(Path(root).rglob(BRANCH_FILENAME)):
        name = path.parent.relative_to(root).as_posix()
        branches.append(read_branch(project_folder, name))
    return branches


def _ancestor_chain(project_folder, commit_id: str) -> list:
    """The commit's ancestor ids, nearest first, walking parents.

    The chain includes `commit_id` itself and ends at the root (the
    commit with no parent). A merge commit's second parent is not
    followed: the first-parent chain is the line of development a
    branch walks.
    """
    chain = []
    seen = set()
    current = commit_id
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        current = read_commit(project_folder, current).get("parent")
    return chain


def _changes_since(project_folder, commit_id: str, ancestor_id: str) -> list:
    """The commit's changes since an ancestor, accumulated along the chain.

    Each commit records its changes relative to its parent
    (`timeline_diff` output); the changes since an ancestor are those
    accumulated from the commit back to (not including) the ancestor.
    """
    chain = _ancestor_chain(project_folder, commit_id)
    if ancestor_id not in chain:
        raise MergeConflict(
            f"{ancestor_id[:12]} is not an ancestor of {commit_id[:12]} - "
            f"two commits with no common ancestor cannot be merged")
    changes: list = []
    for cid in chain:
        if cid == ancestor_id:
            break
        changes.extend(read_commit(project_folder, cid).get("changes") or ())
    return changes


def _change_target(change: Mapping) -> tuple | None:
    """Stable target key for a recorded change, or None when ambiguous."""
    operation = change.get("operation")
    operation_id = change.get("algebra_operation")
    if operation_id and isinstance(operation, Mapping):
        from library.tools import edit_algebra
        spec = edit_algebra.ALGEBRA.get(str(operation_id))
        if spec is not None:
            key = spec.key(operation)
            if key and len(key) > 1:
                target = key[:-1] if operation_id == "clip.set_property" else key
                return tuple(str(part) for part in target)
    entity = change.get("entity_id", change.get("unique_id"))
    if entity:
        return ("entity", str(entity))
    # timeline_diff/1 predates native entity ids. Row-qualified names are
    # kept for compatibility; repeated names are rejected as ambiguous.
    row, item = change.get("row"), change.get("item")
    if row and item:
        return ("legacy", str(row), str(item))
    return None


def _change_properties(change: Mapping) -> frozenset[str]:
    explicit = change.get("properties")
    if explicit:
        return frozenset(str(value) for value in explicit)
    operation_id = change.get("algebra_operation")
    operation = change.get("operation")
    if operation_id and isinstance(operation, Mapping):
        from library.tools import edit_algebra
        spec = edit_algebra.ALGEBRA.get(str(operation_id))
        if spec is not None:
            key = spec.key(operation)
            if operation_id == "clip.set_property" and key and len(key) > 1:
                return frozenset({str(key[-1])})
            return frozenset({"$write"})
    return {
        "move": frozenset({"record_start", "record_end"}),
        "trim": frozenset({"source_in", "source_out"}),
        "j_cut": frozenset({"left_offset", "right_offset"}),
        "remove": frozenset({"$entity"}),
        "add": frozenset({"$entity"}),
        "replace": frozenset({"$entity"}),
    }.get(str(change.get("op") or ""), frozenset({"$unknown"}))


def _change_value(change: Mapping, property_name: str):
    operation_id = change.get("algebra_operation")
    operation = change.get("operation")
    if operation_id and isinstance(operation, Mapping):
        if operation_id == "clip.set_property":
            return operation.get("value")
        return {key: value for key, value in operation.items()
                if key not in {"unique_id"}}
    if property_name == "$entity":
        return change.get("after")
    after = change.get("after") or {}
    key = {"record_start": "start", "record_end": "end"}.get(
        property_name, property_name)
    return after.get(key)


def _temporal_effect(change: Mapping) -> str:
    declared = change.get("temporal_effect")
    operation_id = change.get("algebra_operation")
    if operation_id:
        try:
            from library.tools import edit_algebra
            actual = edit_algebra.temporal_effect(str(operation_id))
        except KeyError:
            return "unknown"
        if declared:
            order = {"local": 0, "ripple": 1, "global": 2}
            declared = str(declared)
            if declared not in order or order[declared] < order[actual]:
                return "unknown"
            return declared
        return actual
    if declared:
        return str(declared) if declared in {"local", "ripple", "global"} else "unknown"
    return "local"


def _is_frame_addressed(change: Mapping) -> bool:
    if change.get("frame_addressed") is True:
        return True
    operation = change.get("operation")
    if isinstance(operation, Mapping):
        return "frame" in operation or "record_frame" in operation
    return False


def _change_frame_span(change: Mapping) -> tuple[int, int] | None:
    spans = change.get("affected_spans")
    if spans:
        try:
            return min(int(span[0]) for span in spans), max(
                int(span[1]) for span in spans)
        except (TypeError, ValueError, IndexError):
            return None
    frame = change.get("frame")
    if frame is None and isinstance(change.get("operation"), Mapping):
        operation = change["operation"]
        frame = operation.get("frame", operation.get("record_frame"))
    if frame is not None:
        try:
            frame = int(frame)
        except (TypeError, ValueError):
            return None
        return frame, frame + 1
    return None


def _merge_change_conflicts(left_changes: Sequence[Mapping],
                            right_changes: Sequence[Mapping]) -> list[dict]:
    """Compare writes by entity and property, not by clip-name overlap."""
    conflicts = []
    left_targets, right_targets = {}, {}
    for label, changes, index in (("left", left_changes, left_targets),
                                  ("right", right_changes, right_targets)):
        for change in changes:
            target = _change_target(change)
            if target is None:
                conflicts.append({"side": label, "entity": None,
                                  "property": "$identity",
                                  "reason": "change has no target identity"})
                continue
            index.setdefault(target, []).append(change)
    for target in sorted(set(left_targets) & set(right_targets), key=str):
        left_rows, right_rows = left_targets[target], right_targets[target]
        if target[0] == "legacy" and (len(left_rows) > 1 or len(right_rows) > 1):
            conflicts.append({"entity": target, "property": "$identity",
                              "reason": "repeated legacy clip name is ambiguous"})
            continue
        for left in left_rows:
            for right in right_rows:
                left_props = _change_properties(left)
                right_props = _change_properties(right)
                if "$unknown" in left_props or "$unknown" in right_props:
                    conflicts.append({
                        "entity": target, "property": "$identity",
                        "reason": "change property semantics are undeclared"})
                    continue
                operation_id = left.get("algebra_operation")
                right_operation_id = right.get("algebra_operation")
                if operation_id or right_operation_id:
                    from library.tools import edit_algebra
                    left_spec = edit_algebra.ALGEBRA.get(str(operation_id))
                    right_spec = edit_algebra.ALGEBRA.get(str(right_operation_id))
                    if ((left_spec and left_spec.merge == "exclusive")
                            or (right_spec and right_spec.merge == "exclusive")):
                        if (operation_id == right_operation_id
                                and left.get("operation") == right.get("operation")):
                            continue
                        conflicts.append({
                            "entity": target, "property": "$write",
                            "reason": "an exclusive algebra operation meets another write"})
                        continue
                if "$entity" in left_props or "$entity" in right_props:
                    if ("$entity" in left_props and "$entity" in right_props
                            and left.get("op") == right.get("op")
                            and left.get("after") == right.get("after")):
                        continue
                    conflicts.append({
                        "entity": target, "property": "$entity",
                        "reason": "delete/add/replace meets a modification"})
                    continue
                for prop in sorted(left_props & right_props):
                    if _change_value(left, prop) != _change_value(right, prop):
                        conflicts.append({
                            "entity": target, "property": prop,
                            "reason": "both branches write different values"})
    for ripple_side, ripple_changes, frame_changes in (
            ("left", left_changes, right_changes),
            ("right", right_changes, left_changes)):
        for ripple in ripple_changes:
            effect = _temporal_effect(ripple)
            if effect in {"unknown", "global"} and frame_changes:
                conflicts.append({
                    "entity": _change_target(ripple),
                    "property": "temporal_effect",
                    "reason": ("operation has no temporal-effect declaration"
                               if effect == "unknown" else
                               "global temporal effect meets another change")})
                continue
            if effect != "ripple":
                continue
            boundary = ripple.get("ripple_from_frame")
            if boundary is None:
                boundary = _change_frame_span(ripple)
                boundary = boundary[0] if boundary else None
            if boundary is None and frame_changes:
                conflicts.append({
                    "entity": _change_target(ripple),
                    "property": "temporal_effect",
                    "reason": "ripple boundary is unavailable for revalidation"})
                continue
            for framed in frame_changes:
                span = _change_frame_span(framed)
                past_boundary = (span is not None and span[1] > int(boundary))
                if _is_frame_addressed(framed) and (
                        span is None or past_boundary):
                    conflicts.append({
                        "side": ripple_side,
                        "entity": _change_target(framed),
                        "property": "timeline_frame",
                        "reason": "frame-addressed edit needs re-anchoring after ripple"})
    return conflicts


def _compose_recorded_changes(left_changes: Sequence[Mapping],
                              right_changes: Sequence[Mapping]) -> list[dict]:
    """Union independently written properties, dropping identical writes."""
    merged = [dict(change) for change in left_changes]
    for change in right_changes:
        duplicate = any(
            _change_target(existing) == _change_target(change)
            and _change_properties(existing) == _change_properties(change)
            and all(_change_value(existing, prop) == _change_value(change, prop)
                    for prop in _change_properties(change))
            for existing in merged)
        if not duplicate:
            merged.append(dict(change))
    return merged


def _incoming_changes(left_changes: Sequence[Mapping],
                      right_changes: Sequence[Mapping]) -> list[dict]:
    """Changes to apply to a candidate copied from the left parent."""
    incoming = []
    for change in right_changes:
        already_held = any(
            _change_target(existing) == _change_target(change)
            and _change_properties(existing) == _change_properties(change)
            and all(_change_value(existing, prop) == _change_value(change, prop)
                    for prop in _change_properties(change))
            for existing in left_changes)
        if not already_held:
            incoming.append(dict(change))
    return incoming


def merge_commits(project_folder, *, timeline: str, left: str, right: str,
                  note: str = "", actor=None) -> dict:
    """Merge two commits on one timeline. Returns the merge commit.

    Each side's changes since their common ancestor are compared by
    target and property. The returned merge commit is a semantic proposal
    only; `materialize_merge` is required before any result is counted as
    applied.

    A fast-forward (one commit is the ancestor of the other) merges to
    the descendant: the merged changes are the descendant's changes
    since the ancestor, and no conflict is possible.

    `actor` names the federated participant the merge runs as. A named
    actor must hold Merge on this project's roster
    (`fabric_permissions`): an editor may execute but never merge into
    the shared chain, and the refusal is an event in the log. `None`
    (the default) is the local operator's own act and is never
    checked.
    """
    if actor is not None:
        from library.tools import fabric_permissions as _permissions
        _permissions.require(project_folder, actor, _permissions.MERGE,
                             task=f"merge {timeline}")
    if not left or not right:
        raise MergeConflict("both sides of a merge must be commits")
    if left == right:
        raise MergeConflict("a commit cannot be merged with itself")
    left_chain = _ancestor_chain(project_folder, left)
    right_chain = _ancestor_chain(project_folder, right)
    common = next((cid for cid in left_chain if cid in right_chain), None)
    if common is None:
        raise MergeConflict(
            f"{left[:12]} and {right[:12]} have no common ancestor - "
            f"they cannot be merged")
    if common == left or common == right:
        # Fast-forward: one side is the ancestor of the other. The merge
        # is the descendant; its changes since the ancestor are what the
        # merge brings in, and no conflict is possible.
        descendant = right if common == left else left
        merged = _changes_since(project_folder, descendant, common)
        incoming = merged
    else:
        left_changes = _changes_since(project_folder, left, common)
        right_changes = _changes_since(project_folder, right, common)
        conflicts = _merge_change_conflicts(left_changes, right_changes)
        if conflicts:
            detail = "; ".join(
                f"{entry.get('entity')!r}.{entry.get('property')} "
                f"({entry.get('reason')})" for entry in conflicts)
            raise MergeConflict(f"conflicting merge writes: {detail}", conflicts)
        merged = _compose_recorded_changes(left_changes, right_changes)
        incoming = _incoming_changes(left_changes, right_changes)
    intent = note or f"merge {right[:12]} into {left[:12]}"
    return record_commit(
        project_folder, parent=left, second_parent=right,
        intent=intent, changes=merged, agent="creative_merge",
        timeline=timeline,
        artifacts={"common_ancestor": common,
                   "incoming_changes": incoming,
                   "materialization": "semantic_only"})


def _write_merge_candidate(project_folder, proposal_id: str,
                           lifecycle_record: Mapping,
                           validation: Mapping | None = None) -> str:
    import hashlib

    from library.tools.versions import store

    transaction_key = hashlib.sha256(
        str(lifecycle_record["transaction_id"]).encode("utf-8")).hexdigest()
    path = os.path.join(str(project_folder), "pipeline_output", "review",
                        "merge_materializations",
                        f"{transaction_key}.json")
    store.write_record(path, {
        "format": "creative_merge_materialization/1",
        "proposal_commit": proposal_id,
        "candidate": dict(lifecycle_record),
        "validation": dict(validation or {}),
        "updated_at": _now(),
    }, prefix=".merge-materialization-")
    return path


def materialize_merge(project_folder, proposal: Mapping, *, source,
                      candidate, revalidate, apply, verify, promote,
                      approve=None) -> dict:
    """Apply a semantic merge to a protected candidate and record proof.

    `revalidate(source, candidate, changes)` confirms stable targets,
    operation preconditions and the base commit against the merged state.
    `apply(candidate, changes)` performs the native edit. `verify(source,
    candidate, changes)` must independently read Resolve back and return
    modified/preserved component evidence plus a native read-back token.
    `approve(evidence)` is required when semantic or visual uncertainty is
    reported. `promote(candidate)` must use the existing guarded promotion path and
    report the promoted final name. A failed step leaves the proposal
    semantic-only and never produces an applied merge record.
    """
    if proposal.get("agent") != "creative_merge" or not proposal.get("second_parent"):
        raise MergeConflict("materialization requires a two-parent merge proposal")
    if (proposal.get("artifacts") or {}).get("materialization") != "semantic_only":
        raise MergeConflict("merge proposal is already materialized or has unknown status")
    from library.tools.composed_edit import CandidateLifecycle, promote_candidates

    head = latest_commit_for_timeline(
        project_folder, str(proposal.get("timeline") or ""))
    if head is None or head.get("id") != proposal.get("parent"):
        raise MergeConflict(
            "merge proposal base is no longer the current recorded timeline head")
    merge_result = list(proposal.get("changes") or ())
    changes = list((proposal.get("artifacts") or {}).get(
        "incoming_changes") or merge_result)
    lifecycle = CandidateLifecycle.create(
        source, candidate, str(proposal.get("timeline") or ""), changes)
    if not lifecycle.source_id or lifecycle.source_id == lifecycle.candidate_id:
        raise MergeConflict(
            "merge materialization requires a distinct, identified protected candidate")
    lifecycle_path = _write_merge_candidate(
        project_folder, proposal["id"], lifecycle.as_dict())
    try:
        validation = revalidate(source, candidate, changes)
    except Exception as refused:
        lifecycle.state = "stale"
        _write_merge_candidate(
            project_folder, proposal["id"], lifecycle.as_dict(),
            {"error": str(refused)})
        raise MergeConflict(
            f"merge target or precondition revalidation failed ({refused})") \
            from refused
    if (not isinstance(validation, Mapping)
            or validation.get("targets") is not True
            or validation.get("preconditions") is not True
            or validation.get("base_commit") != proposal.get("parent")):
        lifecycle.state = "stale"
        _write_merge_candidate(project_folder, proposal["id"],
                               lifecycle.as_dict(),
                               validation if isinstance(validation, Mapping)
                               else {})
        raise MergeConflict(
            "merge targets or preconditions no longer hold on the candidate base")
    _write_merge_candidate(project_folder, proposal["id"],
                           lifecycle.as_dict(), validation)
    lifecycle.mark_applying()
    _write_merge_candidate(project_folder, proposal["id"],
                           lifecycle.as_dict(), validation)
    try:
        apply(candidate, changes)
    except Exception:
        lifecycle.state = "application_failed"
        _write_merge_candidate(project_folder, proposal["id"],
                               lifecycle.as_dict(), validation)
        raise
    lifecycle.mark_applied()
    _write_merge_candidate(project_folder, proposal["id"],
                           lifecycle.as_dict(), validation)
    try:
        evidence = verify(source, candidate, changes)
    except Exception:
        lifecycle.state = "verification_failed"
        _write_merge_candidate(project_folder, proposal["id"],
                               lifecycle.as_dict(), validation)
        raise
    if not isinstance(evidence, Mapping):
        lifecycle.state = "verification_failed"
        _write_merge_candidate(project_folder, proposal["id"],
                               lifecycle.as_dict(), validation)
        raise MergeConflict("merge verification returned no native read-back evidence")
    if (evidence.get("modified") is not True
            or evidence.get("preserved") is not True
            or not evidence.get("native_readback")
            or evidence.get("base_commit") != proposal.get("parent")
            or not evidence.get("modified_components")
            or not evidence.get("preserved_components")):
        lifecycle.state = "verification_failed"
        _write_merge_candidate(project_folder, proposal["id"],
                               lifecycle.as_dict(), validation)
        raise MergeConflict(
            "merge candidate did not prove its base plus modified and preserved native components")
    if evidence.get("semantics_uncertain") or evidence.get("visuals_uncertain"):
        approval = approve(evidence) if callable(approve) else None
        if not isinstance(approval, Mapping) or approval.get("approved") is not True:
            lifecycle.state = "approval_required"
            _write_merge_candidate(project_folder, proposal["id"],
                                   lifecycle.as_dict(), validation)
            raise MergeConflict(
                "merge semantics or visuals are uncertain and were not approved")
        evidence = {**dict(evidence), "approval": dict(approval)}
    lifecycle.mark_verified(modified=True, preserved=True,
                            evidence=dict(evidence))
    _write_merge_candidate(project_folder, proposal["id"],
                           lifecycle.as_dict(), validation)
    promotion_result, lifecycle_records = promote_candidates(
        [lifecycle], lambda: promote(candidate),
        on_update=lambda records: _write_merge_candidate(
            project_folder, proposal["id"], records[0], validation))
    if not lifecycle_records or lifecycle_records[0].get("state") != "promoted":
        raise MergeConflict("verified merge candidate was not confirmed promoted")
    promoted = dict(proposal)
    promoted = record_commit(
        project_folder, parent=proposal["parent"],
        second_parent=proposal["second_parent"],
        intent=str(proposal.get("intent") or "verified creative merge"),
        changes=changes, agent="creative_merge", timeline=proposal["timeline"],
        artifacts={
            "materialization": "verified",
            "proposal_commit": proposal["id"],
            "merge_result_changes": merge_result,
            "candidate_record": lifecycle_path,
            "candidate": lifecycle_records[0],
            "verification": dict(evidence),
            "promotion": promotion_result,
        })
    promoted["result_revision"] = promoted["revision_id"]
    for entry in list_queue_entries(
            project_folder, status=ENTRY_MATERIALIZATION_REQUIRED):
        if entry.get("merge_proposal") == proposal.get("id"):
            complete_merge_queue_entry(project_folder, entry["id"],
                                       promoted["id"])
    return promoted


# ── Cherry-picks: replay a commit's changes as a new commit ──────
#
# A cherry-pick replays what a commit DID onto the timeline's head -
# re-applying a change a later revert undid, or one a bad merge lost -
# as a new commit, so the replay carries its provenance
# (`artifacts.cherry_picks` names the source) instead of arriving as a
# hand-copied change nobody can trace. History only moves forward:
# the picked commit stays readable beside its replay.
#
# Only the commit's OWN line: the pick stands on the head of the
# timeline the commit is for, and the commit must be that head or an
# ancestor of it. A pick from another line is refused naming both -
# the record holds diffs, not destination state, so nothing in the
# store can prove the picked spans still hold there. To move a change
# across lines, apply it as a touch-up against the live timeline.

def cherry_pick(project_folder, commit_id: str, note: str = "",
                dest_tracks=None) -> dict:
    """Replay a commit's changes as a new commit. Returns it.

    Four refusals, all `CherryPickRefused` and all by name. A merge
    is refused: its changes are the union of both sides, so replaying
    them would replay the main line too - pick the brought-in commits
    instead. A commit outside its timeline's line is refused: the
    replay would stand on a history that does not contain it. A commit
    whose clips later commits also changed is refused naming the
    clips: replaying the old changes would silently clobber the later
    touch. An empty replay - a commit that changed nothing - is
    refused: it would rewrite no history and only pollute it.

    When `dest_tracks` - the destination timeline's current track
    reads - is supplied, every replayed change must still hold there
    (`timeline_diff.changes_hold`): a change the destination no
    longer reads as left is refused naming it, so a replay proven
    against a stale chain cannot silently clobber a timeline that
    moved on outside the record.
    """
    from library.tools import timeline_diff

    target = read_commit(project_folder, commit_id)
    timeline = target.get("timeline") or ""
    if target.get("second_parent"):
        raise CherryPickRefused(
            f"commit {commit_id[:12]} is a merge - its changes are the "
            f"union of both sides, so replaying them would replay the "
            f"main line too; pick the commits it brought in instead")
    head = latest_commit_for_timeline(project_folder, timeline)
    if head is None or commit_id not in _ancestor_chain(
            project_folder, head.get("id")):
        raise CherryPickRefused(
            f"commit {commit_id[:12]} is not on {timeline!r}'s line - "
            f"a replay stands on the timeline's head, and this commit "
            f"is not its head or an ancestor of it")
    changes = [dict(change) for change in (target.get("changes") or ())]
    if not changes:
        raise CherryPickRefused(
            f"commit {commit_id[:12]} changed nothing - an empty replay "
            f"would rewrite no history and only pollute it")
    later = _changes_since(project_folder, head.get("id"), commit_id)
    picked_names = {c.get("item") for c in changes if c.get("item")}
    later_names = {c.get("item") for c in later if c.get("item")}
    conflicts = sorted(picked_names & later_names)
    if conflicts:
        raise CherryPickRefused(
            f"commit {commit_id[:12]} cannot be replayed: later commits "
            f"also changed {', '.join(conflicts)} - replaying the old "
            f"changes would silently clobber the later touch")
    if dest_tracks is not None:
        try:
            problems = timeline_diff.changes_hold(changes, dest_tracks)
        except ValueError as unreadable:
            raise CherryPickRefused(
                f"commit {commit_id[:12]} cannot be proven against "
                f"the destination reads ({unreadable})") from unreadable
        if problems:
            raise CherryPickRefused(
                f"commit {commit_id[:12]} no longer applies to the "
                f"destination reads: " + "; ".join(problems[:5]))
    intent = note or (f"cherry-pick {commit_id[:12]} "
                      f"({target.get('intent') or 'no intent'})")
    return record_commit(
        project_folder, parent=head.get("id"), intent=intent,
        changes=changes, agent="creative_cherry_pick", timeline=timeline,
        artifacts={"cherry_picks": commit_id})


# ── Tags: named pointers to commits ────────────────────────────
#
# A tag names a commit worth keeping - "v1", "before-grade". The tag is
# a pointer, not a copy: the commit it names is immutable, so moving a
# tag never rewrites history. The target is read before anything is
# written, so a tag for a commit no record answers is refused and a
# dangling pointer never lands; and a name that already points
# somewhere is refused unless the caller says `move`, so what "v1"
# means is never rewritten in silence.

TAG_FORMAT = "creative_tag/1"
TAG_DIRNAME = "tags"
TAG_FILENAME = "tag.json"


def tag_root(project_folder) -> str:
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        TAG_DIRNAME)


def _tag_path(project_folder, name: str) -> str:
    return os.path.join(tag_root(project_folder), name, TAG_FILENAME)


def record_tag(project_folder, *, name: str, commit: str, note: str = "",
               move: bool = False) -> dict:
    """Record a tag: a name pointing at a commit. Returns the tag.

    The target is READ before anything is written: a tag for a commit
    id no commit answers is refused with `TagRefused`, so a dangling
    pointer never lands. A name that already points at a commit is
    refused unless `move` is passed, so a name is never rewritten in
    silence. A moved tag is re-pointed now - the commits it pointed at
    before are untouched, because a tag is a pointer, not history.
    """
    from library.tools.versions import store

    if not str(name or "").strip():
        raise TagRefused("a tag needs a name - "
                         "name the version this commit is")
    try:
        read_commit(project_folder, commit)
    except UndoRefused as missing:
        raise TagRefused(
            f"tag {name!r} cannot point at {commit!r}: no commit with "
            f"that id is recorded ({missing.args[0] if missing.args else missing})",
            "list what is recorded with `ren commits <project> --list`"
        ) from missing
    path = _tag_path(project_folder, name)
    if os.path.isfile(path) and not move:
        current = read_tag(project_folder, name)
        raise TagRefused(
            f"tag {name!r} already points at "
            f"{str(current.get('commit') or '')[:12]} - pass move=True "
            f"to move it, so a name is never rewritten in silence")
    tag = {
        "format": TAG_FORMAT,
        "name": name,
        "commit": commit,
        "note": note,
        "created_at": _now(),
    }
    store.write_record(path, tag, prefix=".tag-")
    return tag


def read_tag(project_folder, name: str) -> dict:
    path = _tag_path(project_folder, name)
    try:
        with open(path, encoding="utf-8") as handle:
            tag = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise TagRefused(
            f"tag {name!r} could not be read at {path} ({unreadable})"
        ) from unreadable
    if tag.get("format") != TAG_FORMAT:
        raise TagRefused(
            f"{path} is {tag.get('format')!r}, not {TAG_FORMAT!r}")
    return tag


def list_tags(project_folder) -> list:
    """Every recorded tag, by name."""
    root = tag_root(project_folder)
    if not os.path.isdir(root):
        return []
    tags = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, TAG_FILENAME)
        if os.path.isfile(path):
            tags.append(read_tag(project_folder, name))
    return tags


def merge_preview(project_folder, *, left: str, right: str) -> dict:
    """What `merge_commits` would do, without recording anything.

    The merge queue's evaluation pass reads this: each candidate is
    compared against the master in one pass, and only the verdict is
    recorded - the merge itself waits for `merge_passing`, which
    re-verifies freshness first. Returns `mergeable` with the merged
    changes (or conflicts by entity and property), the common ancestor, and
    whether the merge is a fast-forward. Unreadable commits propagate
    `UndoRefused` from the read; unmergeable pairs report their reason
    instead of raising, because an evaluation that threw on the first
    bad candidate could never report the rest of the batch.
    """
    left_chain = _ancestor_chain(project_folder, left)
    right_chain = _ancestor_chain(project_folder, right)
    if left == right:
        return {"mergeable": False,
                "reason": "a commit cannot be merged with itself",
                "common": left, "fast_forward": False,
                "conflicts": [], "merged_changes": []}
    common = next((cid for cid in left_chain if cid in right_chain), None)
    if common is None:
        return {"mergeable": False,
                "reason": f"{left[:12]} and {right[:12]} have no common "
                          f"ancestor - they cannot be merged",
                "common": None, "fast_forward": False,
                "conflicts": [], "merged_changes": []}
    if common == left or common == right:
        descendant = right if common == left else left
        changes = _changes_since(project_folder, descendant, common)
        return {"mergeable": True, "reason": "",
                "common": common, "fast_forward": True,
                "conflicts": [],
                "merged_changes": changes, "incoming_changes": changes}
    left_changes = _changes_since(project_folder, left, common)
    right_changes = _changes_since(project_folder, right, common)
    conflicts = _merge_change_conflicts(left_changes, right_changes)
    if conflicts:
        detail = "; ".join(
            f"{entry.get('entity')!r}.{entry.get('property')} "
            f"({entry.get('reason')})" for entry in conflicts)
        return {"mergeable": False,
                "reason": f"conflicting merge writes: {detail}",
                "common": common, "fast_forward": False,
                "conflicts": conflicts, "merged_changes": []}
    return {"mergeable": True, "reason": "",
            "common": common, "fast_forward": False,
            "conflicts": [],
            "merged_changes": _compose_recorded_changes(left_changes,
                                                        right_changes),
            "incoming_changes": _incoming_changes(left_changes,
                                                    right_changes)}


# ── The merge queue ───────────────────────────────────────────────
#
# The captain's merge-queue direction (2026-10-06): candidate branches
# - variants, or proposals from other participants' Rens - are
# evaluated together against the current master version in one pass
# with measured signals; the passing ones merge into the next version,
# a failing one is identified (per-candidate verdicts ARE the split:
# each candidate merges against master independently, so a failure
# already names its candidate) and sent back with the reason; nothing
# merges if its branch changed after it was evaluated. Variant
# exploration owns this queue and the evaluation; who may submit,
# approve and merge is the permissions/federation lane's, so entries
# carry `submitted_by` but this module checks nothing about it. The
# queue is coordinated through the event log - one queue for both
# items - so every transition emits its event.

QUEUE_FORMAT = "merge_queue_entry/1"
QUEUE_DIRNAME = "merge_queue"
QUEUE_FILENAME = "entry.json"

ENTRY_QUEUED = "queued"
ENTRY_EVALUATED_PASS = "evaluated_pass"
ENTRY_EVALUATED_FAIL = "evaluated_fail"
ENTRY_MATERIALIZATION_REQUIRED = "materialization_required"
ENTRY_MERGED = "merged"
ENTRY_REJECTED = "rejected"
ENTRY_STALE = "stale"


class QueueRefused(RenRefusal, RuntimeError):
    """A queue transition that declined before changing anything."""


def queue_root(project_folder) -> str:
    """Where merge-queue entries live, beside commits and branches."""
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        QUEUE_DIRNAME)


def _queue_path(project_folder, entry_id: str) -> str:
    return os.path.join(queue_root(project_folder), entry_id,
                        QUEUE_FILENAME)


def _branch_digest(branch: Mapping) -> str:
    """The branch record's identity, for the freshness check.

    The commit identity pattern over the whole record: any re-record
    of the branch changes the digest, so a merge gated on it refuses
    where the branch moved after evaluation.
    """
    return _commit_id(dict(branch))


def _tip_sequence(project_folder, timeline: str) -> int:
    """The newest commit sequence on one timeline, or 0.

    The branch's line of development may gain commits after an entry
    is evaluated; the sequence is the cheap clock that says whether
    it did. Zero where nothing was ever committed on the timeline -
    absence is absence, never sequence 1.
    """
    sequence = 0
    for commit in list_commits(project_folder):
        if commit.get("timeline") == timeline:
            sequence = max(sequence, int(commit.get("sequence") or 0))
    return sequence


def read_queue_entry(project_folder, entry_id: str) -> dict:
    """One queue entry back, format-checked."""
    path = _queue_path(project_folder, entry_id)
    try:
        with open(path, encoding="utf-8") as handle:
            entry = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise QueueRefused(
            f"merge-queue entry {entry_id!r} could not be read",
            f"({unreadable})",
            "list what is queued with "
            "`manage_project.py merge-queue <project> list") from unreadable
    if entry.get("format") != QUEUE_FORMAT:
        raise QueueRefused(
            f"{path} is {entry.get('format')!r}, not {QUEUE_FORMAT!r}",
            "a queue entry in a foreign format cannot be trusted",
            "recover the entry from the project's git history")
    return entry


def list_queue_entries(project_folder, status: str = "") -> list:
    """Every queue entry, oldest first, optionally one status."""
    root = queue_root(project_folder)
    if not os.path.isdir(root):
        return []
    entries = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, QUEUE_FILENAME)
        if os.path.isfile(path):
            entry = read_queue_entry(project_folder, name)
            if not status or entry.get("status") == status:
                entries.append(entry)
    return sorted(entries, key=lambda entry: entry.get("queued_at") or "")


def _write_queue_entry(project_folder, entry: dict) -> dict:
    from library.tools.versions import store

    store.write_record(
        _queue_path(project_folder, entry["id"]), entry,
        prefix=".queue-")
    return entry


def queue_merge(project_folder, *, branch: str, candidate: str,
                master: str, submitted_by: str, note: str = "") -> dict:
    """Submit one candidate branch to the merge queue.

    `branch` names the recorded branch, `candidate` the commit the
    branch proposes, `master` the commit it is evaluated against.
    `submitted_by` names who proposed it - recorded, never checked
    here: who may submit is the permissions lane's. Returns the entry;
    a re-submitted triple returns the queued entry instead of forking
    a second one. Refusals, all before anything is written: unknown
    branch or commits, a candidate that IS the master (nothing to
    merge), a candidate with no common ancestor with the master (it
    could never merge, so queueing it would only waste an evaluation
    pass).
    """
    from library.tools import event_log

    branch_record = read_branch(project_folder, branch)
    candidate_commit = read_commit(project_folder, candidate)
    master_commit = read_commit(project_folder, master)
    if not str(submitted_by or "").strip():
        raise QueueRefused(
            "no submitter named",
            "a queued proposal with no author cannot be sent back - "
            "a rejection with nobody to reach is not a decision",
            "pass who proposed this candidate and re-run")
    if candidate == master:
        raise QueueRefused(
            f"candidate {candidate[:12]} is already the master",
            "merging a commit with itself changes nothing, and a queue "
            "entry that can only no-op is not a proposal",
            "queue the branch's own tip commit instead")
    preview = merge_preview(
        project_folder, left=master, right=candidate)
    if preview["common"] is None:
        raise QueueRefused(
            f"candidate {candidate[:12]} shares no ancestor with master "
            f"{master[:12]}",
            "two commits with no common ancestor cannot be merged, so "
            "an evaluation pass could only reject it",
            "rebase the branch onto the master line first, then queue it")

    content = {"branch": branch, "candidate": candidate, "master": master}
    entry_id = _commit_id(content)
    if os.path.isfile(_queue_path(project_folder, entry_id)):
        return read_queue_entry(project_folder, entry_id)

    entry = {
        "format": QUEUE_FORMAT,
        "id": entry_id,
        "branch": branch,
        "branch_timeline": branch_record.get("timeline") or "",
        "candidate": candidate,
        "candidate_timeline": candidate_commit.get("timeline") or "",
        "master": master,
        "master_timeline": master_commit.get("timeline") or "",
        "submitted_by": str(submitted_by).strip(),
        "note": str(note or ""),
        "status": ENTRY_QUEUED,
        "queued_at": _now(),
        "evaluation": None,
    }
    _write_queue_entry(project_folder, entry)
    event_log.emit(
        str(project_folder), "merge_queued", task=str(submitted_by).strip(),
        entry=entry_id, branch=branch, candidate=candidate, master=master)
    return entry


def _evaluation_facts(project_folder, *, master: str, candidate: str,
                      preview: Mapping) -> dict:
    """The measured comparison one evaluation records.

    Counts and clip names read off the commits' recorded `timeline_diff`
    changes - the same signals the merge itself refuses on. No score:
    a verdict is pass/fail with the reason, and the choice of what the
    verdict MEANS stays the model's or the user's (AGENTS.md 10.4 -
    passage engagement is an ordering with no score, and so is this).
    """
    common = preview.get("common")
    if not common:
        return {"master_changes": 0, "candidate_changes": 0,
                "master_clips": [], "candidate_clips": []}
    master_changes = _changes_since(project_folder, master, common)
    candidate_changes = _changes_since(project_folder, candidate, common)
    return {
        "master_changes": len(master_changes),
        "candidate_changes": len(candidate_changes),
        "master_clips": sorted(
            {c.get("item") for c in master_changes if c.get("item")}),
        "candidate_clips": sorted(
            {c.get("item") for c in candidate_changes if c.get("item")}),
    }


def evaluate_batch(project_folder, entry_ids=None) -> list:
    """Evaluate the queued entries together against their masters.

    One pass, per-candidate verdicts - the verdicts ARE the batch
    split: each candidate merges against its master independently, so
    a failure already names its candidate and no bisect loop is
    needed. A verdict records the preview's reason plus the measured
    facts (change counts and clip names per side), and snapshots the
    branch digest and tip sequence the merge pass re-verifies -
    nothing merges if the branch moved after this moment. Records
    verdicts only; no commit is created here. Entries that are not
    queued refuse by name: re-evaluating a decided entry would
    overwrite its verdict in silence.
    """
    from library.tools import event_log

    if entry_ids is None:
        entries = list_queue_entries(project_folder, status=ENTRY_QUEUED)
    else:
        entries = [read_queue_entry(project_folder, entry_id)
                   for entry_id in entry_ids]
        for entry in entries:
            if entry.get("status") not in (ENTRY_QUEUED, ENTRY_STALE):
                raise QueueRefused(
                    f"entry {entry['id'][:12]} is "
                    f"{entry.get('status')}, not queued",
                    "evaluating a decided entry would overwrite its "
                    "verdict in silence - a stale entry may re-evaluate, "
                    "because its snapshots are what went old",
                    "evaluate what is queued or stale - "
                    "`merge-queue <project> list --status queued`")
    decided = []
    for entry in entries:
        master = latest_commit_for_timeline(
            project_folder, entry.get("master_timeline") or "")
        if master is None:
            raise QueueRefused(
                f"master timeline {entry.get('master_timeline')!r} has no head",
                "a merge evaluation must be based on the current recorded master",
                "record or recover the master timeline head")
        entry["master"] = master["id"]
        preview = merge_preview(
            project_folder, left=master["id"],
            right=entry["candidate"])
        branch_record = read_branch(project_folder, entry["branch"])
        evaluation = {
            "verdict": "pass" if preview["mergeable"] else "fail",
            "reason": preview.get("reason") or "",
            "evaluated_master": master["id"],
            "common": preview.get("common"),
            "fast_forward": bool(preview.get("fast_forward")),
            "branch_digest": _branch_digest(branch_record),
            "tip_sequence": _tip_sequence(
                project_folder, entry.get("branch_timeline") or ""),
            "evaluated_at": _now(),
        }
        evaluation.update(_evaluation_facts(
            project_folder, master=entry["master"],
            candidate=entry["candidate"], preview=preview))
        entry["evaluation"] = evaluation
        entry["status"] = (ENTRY_EVALUATED_PASS if preview["mergeable"]
                           else ENTRY_EVALUATED_FAIL)
        _write_queue_entry(project_folder, entry)
        event_log.emit(
            str(project_folder), "merge_evaluated",
            task=entry.get("submitted_by") or "",
            entry=entry["id"], branch=entry["branch"],
            verdict=evaluation["verdict"], reason=evaluation["reason"])
        decided.append(entry)
    return decided


def merge_passing(project_folder, entry_ids=None, actor=None) -> list:
    """Prepare one passing entry for verified materialization.

    Each entry is checked against the CURRENT head of its master timeline.
    A pending semantic proposal blocks the next entry until it is
    materialized; later entries are re-evaluated against the resulting
    master. Two gates per entry, in this order:
    freshness (the branch digest and tip sequence must read as they
    did at evaluation - a branch that changed after evaluation goes
    stale and waits for a re-evaluation, it never merges); then the
    merge itself, where a conflict with the current head rejects THIS
    entry with the entity and property named. A prepared proposal is not
    a merge commit applied to the master.
    Entries that never passed evaluation refuse by name.

    `actor` is the permissions lane's hook: who may merge into the
    shared queue. A named actor must hold Merge on the project's
    roster (`fabric_permissions`) before any entry merges; `None` is
    the local operator and is never checked.
    """
    from library.tools import event_log

    pending = list_queue_entries(
        project_folder, status=ENTRY_MATERIALIZATION_REQUIRED)
    if pending:
        raise QueueRefused(
            f"merge proposal {pending[0].get('merge_proposal', '')[:12]} "
            "needs verified materialization before another queue merge",
            "a semantic merge record is not an applied timeline",
            "materialize and verify the candidate, then continue the queue")
    if actor is not None:
        from library.tools import fabric_permissions as _permissions
        _permissions.require(project_folder, actor, _permissions.MERGE,
                             task="merge queue batch")

    if entry_ids is None:
        entries = list_queue_entries(
            project_folder, status=ENTRY_EVALUATED_PASS)
    else:
        entries = [read_queue_entry(project_folder, entry_id)
                   for entry_id in entry_ids]
        for entry in entries:
            if entry.get("status") != ENTRY_EVALUATED_PASS:
                raise QueueRefused(
                    f"entry {entry['id'][:12]} is "
                    f"{entry.get('status')}, not evaluated_pass",
                    "merging without a passing evaluation skips the "
                    "freshness the queue exists to enforce",
                    "evaluate it first - "
                    "`merge-queue <project> evaluate`")
    merged = []
    for entry in entries:
        evaluation = entry.get("evaluation") or {}
        branch_record = read_branch(project_folder, entry["branch"])
        if (_branch_digest(branch_record)
                != evaluation.get("branch_digest")
                or _tip_sequence(project_folder,
                                 entry.get("branch_timeline") or "")
                != evaluation.get("tip_sequence")):
            entry["status"] = ENTRY_STALE
            _write_queue_entry(project_folder, entry)
            event_log.emit(
                str(project_folder), "merge_stale",
                task=entry.get("submitted_by") or "",
                entry=entry["id"], branch=entry["branch"],
                reason="the branch changed after it was evaluated - "
                       "re-evaluate before merging")
            merged.append(entry)
            continue
        head = latest_commit_for_timeline(
            project_folder, entry.get("master_timeline") or "")
        if head is None:
            raise QueueRefused(
                f"master timeline {entry.get('master_timeline')!r} has "
                f"no head commit",
                "the next version stands on the master's head, and it "
                "is gone",
                "recover the master's commits from the project's git "
                "history")
        if evaluation.get("evaluated_master") != head["id"]:
            entry["status"] = ENTRY_STALE
            entry["evaluation"]["reason"] = (
                "the master changed after evaluation - re-evaluate before "
                "materializing this candidate")
            _write_queue_entry(project_folder, entry)
            event_log.emit(
                str(project_folder), "merge_stale",
                task=entry.get("submitted_by") or "",
                entry=entry["id"], branch=entry["branch"],
                reason=entry["evaluation"]["reason"])
            merged.append(entry)
            continue
        try:
            merge_commit = merge_commits(
                project_folder, timeline=entry.get("master_timeline") or "",
                left=head["id"], right=entry["candidate"],
                note=f"merge {entry['branch']} ({entry['id'][:12]})",
                actor=actor)
        except MergeConflict as conflict:
            # args[0] is the message; args[1] is the clip list where
            # the raise site names one. The reason a submitter is sent
            # back is prose, not a tuple repr.
            reason = str(conflict.args[0]) if conflict.args else str(conflict)
            entry["status"] = ENTRY_REJECTED
            entry["merge_error"] = reason
            _write_queue_entry(project_folder, entry)
            event_log.emit(
                str(project_folder), "merge_rejected",
                task=entry.get("submitted_by") or "",
                entry=entry["id"], branch=entry["branch"],
                reason=reason)
            merged.append(entry)
            continue
        entry["status"] = ENTRY_MATERIALIZATION_REQUIRED
        entry["merge_proposal"] = merge_commit["id"]
        _write_queue_entry(project_folder, entry)
        event_log.emit(
            str(project_folder), "merge_prepared",
            task=entry.get("submitted_by") or "",
            entry=entry["id"], branch=entry["branch"],
            merge_proposal=merge_commit["id"])
        merged.append(entry)
        # The current approved master has not moved. One proposal can be
        # prepared at a time; the next candidate must be re-evaluated after
        # this one is actually materialized and verified.
        break
    return merged


def complete_merge_queue_entry(project_folder, entry_id: str,
                               materialized_commit_id: str) -> dict:
    """Mark a queued merge applied only after verified candidate promotion."""
    from library.tools import event_log

    entry = read_queue_entry(project_folder, entry_id)
    if entry.get("status") != ENTRY_MATERIALIZATION_REQUIRED:
        raise QueueRefused(
            f"merge queue entry {entry_id[:12]} is {entry.get('status')!r}, "
            "not awaiting materialization")
    commit = read_commit(project_folder, materialized_commit_id)
    artifacts = commit.get("artifacts") or {}
    proposal = read_commit(project_folder, str(entry.get("merge_proposal") or ""))
    if (commit.get("agent") != "creative_merge"
            or commit.get("status") != "verified"
            or artifacts.get("materialization") != "verified"
            or artifacts.get("proposal_commit") != entry.get("merge_proposal")
            or commit.get("parent") != proposal.get("parent")
            or commit.get("second_parent") != entry.get("candidate")
            or proposal.get("second_parent") != entry.get("candidate")
            or not commit.get("result_revision")):
        raise QueueRefused(
            "the supplied commit is not the verified result of this merge proposal")
    entry["status"] = ENTRY_MERGED
    entry["merge_commit"] = commit["id"]
    entry["result_revision"] = commit.get("result_revision") or commit.get("revision_id")
    _write_queue_entry(project_folder, entry)
    event_log.emit(
        str(project_folder), "merge_merged",
        task=entry.get("submitted_by") or "", entry=entry["id"],
        branch=entry["branch"], merge_commit=commit["id"],
        result_revision=entry["result_revision"])
    return entry


# ── Capture: the touch's side ────────────────────────────────────


def _identity_change(detail: Mapping) -> _ce.ItemChange:
    """An `ItemChange` that moves nothing: the capture's own span."""
    return _ce.ItemChange(
        track_type=detail["track_type"], track_index=int(detail["track_index"]),
        item_index=0, record_frame=int(detail["record_in"]),
        duration=int(detail["duration"]),
        left_offset=_source_trim(detail, "undo capture"),
        previous_record=int(detail["record_in"]),
        previous_duration=int(detail["duration"]), how="journal",
        comp_count=int((detail.get("fusion") or {}).get("comp_count") or 0),
        name=str(detail.get("name") or ""))


def open_entry(project_folder, *, final: str, reel: int,
               resolve_project: str, spec: Mapping, gate_class: str,
               source_timeline: Any, removals: Sequence[Mapping],
               fusion_manifest: Mapping | None = None,
               batch: str = "", project=None) -> dict:
    """Write the journal entry BEFORE the touch changes anything.

    Reads the approved timeline whole and captures every item the plan
    deletes without re-placing. Raises `UndoRefused` - and the touch
    must then refuse - when a removed item carries a colour grade, the
    one state no script can record (`reel_touchup` refuses it first,
    by the same rule as a graded swap).
    """
    from library.tools import reel_read

    entry_id = new_entry_id(final)
    root = entry_dir(project_folder, entry_id)
    comps_dir = os.path.join(root, "comps")
    before = reel_read.read_tracks(source_timeline)
    preservation_before = None
    if project is not None:
        from library.tools.reel_replace_guard import full_timeline_snapshot
        preservation_before = full_timeline_snapshot(
            source_timeline, project, project_folder)
    live_rows = _ce._rows_of(source_timeline)
    removed = []
    for target in removals or ():
        row = str(target["row"]).upper()
        frame = int(target["record_frame"])
        hits = [item for item in (live_rows.get(row) or [])
                if _ce._read(item, "GetStart", None) == frame]
        if len(hits) != 1:
            raise UndoRefused(
                f"the plan removes {row}@{frame} and the "
                f"approved timeline holds {len(hits)} item(s) there",
                "so the journal cannot record what the touch would take",
                "restate the touch against the live timeline, then "
                "re-run `ren touch`")
        item = hits[0]
        detail = _detail_at(before, row, frame)
        capture = _ce.capture_item(item, _identity_change(detail),
                                   comps_dir,
                                   os.path.join(root, "withheld"))
        if capture.node_count is not None and int(capture.node_count) > 1:
            raise UndoRefused(
                f"{row}@{frame} carries a colour grade "
                f"({capture.node_count} nodes)",
                "no script can record a grade, so removing it could not "
                "be undone",
                "rebuild the reel (`ren build <project>`) - a graded "
                "removal needs a rebuild, not a touch-up")
        removed.append({
            "row": row, "record_frame": frame,
            "duration": int(detail["duration"]),
            "left_offset": _source_trim(detail, "undo capture"),
            "source_file": detail.get("source_file") or "",
            "name": detail.get("name") or "",
            "clip_color": detail.get("clip_color") or "",
            "properties": dict(capture.properties),
            "node_count": capture.node_count,
            "comps": [{"index": comp.index,
                       "path": os.path.relpath(comp.path, root),
                       "window": comp.window}
                      for comp in capture.restorable_comps],
        })
    entry = {
        "format": JOURNAL_FORMAT,
        "id": entry_id,
        "reel": int(reel),
        "final": final,
        "resolve_project": resolve_project,
        "batch": batch or "",
        "spec": json.loads(json.dumps(dict(spec or {}), default=str)),
        # A replayed accepted edit is materialized by the full-rebuild
        # lifecycle. Keep its source journal explicit so it is not
        # mistaken for a new independent intent on the next rebuild.
        "accepted_replay_of": (str(spec["accepted_replay_of"])
                               if spec.get("accepted_replay_of") else None),
        "gate_class": gate_class,
        "opened_at": _now(),
        "status": STATUS_OPEN,
        "before": {"tracks": before},
        "removed": removed,
    }
    # Persist intent before the first timeline mutation. A candidate can
    # therefore remain inspectable as prepared even if it is never approved.
    from library.tools import creative_revision
    from library.tools.versions import reel_versions

    prior = reel_versions.latest_act(project_folder, final)
    parent_revision = (prior or {}).get("revision_id")
    revision = creative_revision.create(
        project_folder, parents=[parent_revision] if parent_revision else (),
        author=str(spec.get("author") or "reel_touchup"),
        intent=str(spec.get("note") or "touch-up"),
        operations=[dict(operation) for operation in spec.get("edits", ())
                    if isinstance(operation, Mapping)],
        affected_entities=[str(final)],
        refs={"undo_journal": {"entry_id": entry_id, "status": STATUS_OPEN}},
        status="prepared")
    entry["revision_id"] = revision["revision_id"]
    try:
        fps = float(source_timeline.GetSetting("timelineFrameRate"))
    except (AttributeError, TypeError, ValueError):
        fps = 0.0
    if fps > 0:
        entry["fps"] = fps
    if preservation_before is not None:
        entry["preservation_before"] = preservation_before
    if fusion_manifest is not None:
        entry["fusion_manifest"] = "fusion_manifest.json"
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "fusion_manifest.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(fusion_manifest, handle, indent=2, sort_keys=True)
    write_entry(project_folder, entry)
    return entry


def close_entry(project_folder, entry: dict, *, after_timeline: Any,
                rows: Mapping, project=None,
                preservation_snapshot: Mapping | None = None) -> dict:
    """Record what the touch left, and the version it produced.

    Called on the promoted timeline BEFORE the replaced generation is
    deleted: until this returns, the retired copy is the only way back.
    """
    from library.tools import reel_read
    from library.tools.versions import reel_versions

    entry["after"] = {"tracks": reel_read.read_tracks(after_timeline)}
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    # Persist intent, its recorded edit program, policy per operation and
    # the declaration revision against which the touch was accepted.
    entry["accepted_edit"] = accepted_edit_record(project_folder, entry)
    if preservation_snapshot is not None:
        after_snapshot = dict(preservation_snapshot)
    elif project is not None:
        from library.tools.reel_replace_guard import full_timeline_snapshot
        after_snapshot = full_timeline_snapshot(
            after_timeline, project, project_folder)
    else:
        after_snapshot = None
    if after_snapshot is not None:
        entry["preservation_after"] = after_snapshot
        from library.tools.plan_provenance import record_timeline_snapshot
        record_timeline_snapshot(
            review_dir,
            entry["final"], after_snapshot, action="touchup",
            action_journal=entry["id"])
    # VC-14: the reel's component manifest, built from existing
    # records. Never-fail instrumentation like the history below: a
    # manifest that cannot be built is recorded absent, never
    # invented, and never fails the touch it instruments.
    try:
        from library.tools import reel_component_manifest as _manifests

        component_manifest = _manifests.build_manifest(
            project_folder, entry["final"], rows)
    except Exception:  # noqa: BLE001 - absent, not invented
        component_manifest = None
    version = reel_versions.record(
        project_folder, entry["final"], kind=reel_versions.KIND_TOUCH,
        rows=rows, journal=entry["id"], batch=entry.get("batch") or None,
        component_manifest=component_manifest,
        revision_id=entry.get("revision_id"),
        intent=(entry.get("accepted_edit") or {}).get("intent") or "touch-up",
        revision_status="verified")
    entry["version"] = version["version"]
    entry["status"] = STATUS_APPLIED
    entry["applied_at"] = _now()
    write_entry(project_folder, entry)
    # The touch half of the per-reel edit history
    # (`library/tools/reel_edit_history.py`): a touch-up promoted in
    # place, with the journal that can reverse it. Never-fail
    # instrumentation - a history write cannot fail the touch it
    # instruments, and the touch's own journal stays the way back.
    try:
        from library.tools import reel_edit_history as _history

        _history.try_record_ren_act(
            os.path.join(project_folder, "pipeline_output", "review"),
            entry["final"], act=_history.ACT_TOUCH,
            summary=(f"Ren touch-up on {entry['final']} "
                     f"(version {version['version']}, "
                     f"journal {entry['id']})"),
            refs={"version": version["version"],
                  "revision_id": version["revision_id"],
                  "journal": entry["id"],
                  "rows_digest_after": version.get("rows_digest"),
                  **({"batch": entry["batch"]} if entry.get("batch")
                     else {})})
    except Exception:  # noqa: BLE001, S110 - history never fails a touch
        pass
    return entry


def fail_entry(project_folder, entry: dict, why: str) -> None:
    """A touch that did not land: nothing to undo, and the entry says so."""
    entry["status"] = STATUS_FAILED
    entry["failed"] = why
    write_entry(project_folder, entry)


def attach_receipt(project_folder, entry_id: str, receipt: Mapping) -> str:
    """File a touch's account of itself onto its journal entry (Lane E2).

    The standalone `touchup_*.json` / `master_touchup_*.json` receipt
    files are RETIRED (R6): the request already rides the entry's
    `spec`, and the account now rides its `receipt` - one record per
    act, read back through `read_entry` like everything else the
    journal holds. Old receipt files on disk stay where they are,
    ignored and never deleted. Returns the entry path, so callers
    keep reporting a `receipt_path` that names a file which exists.
    """
    entry = read_entry(project_folder, entry_id)
    entry["receipt"] = json.loads(json.dumps(dict(receipt or {}),
                                             default=str))
    entry["receipt_attached_at"] = _now()
    return write_entry(project_folder, entry)


# ── The projection, and what differs ─────────────────────────────


def _row(detail: Mapping) -> str:
    return _ce.row_label(detail["track_type"], int(detail["track_index"]))


def _source_trim(detail: Mapping, context: str) -> int:
    value = detail.get("left_offset")
    if value is None:
        raise UndoRefused(
            f"{context} cannot preserve {_row(detail)} item's source trim "
            "because `left_offset` was unreadable",
            "undo would re-place it at frame 0 and expose any "
            "transparent preroll",
            "re-read the reel so the item's source trim is known, then "
            "re-run `ren undo`")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise UndoRefused(
            f"{context} cannot preserve {_row(detail)} item's invalid "
            f"source trim {value!r}",
            "undo needs an integer source frame",
            "re-read the reel so the item's source trim is known, then "
            "re-run `ren undo`") from exc


def _transform(detail: Mapping) -> dict:
    return {key: value for key, value in
            sorted((detail.get("transform") or {}).items())
            if key not in _ce.READ_ONLY_PROPERTIES}


def _windows(detail: Mapping) -> list:
    return [_ce._frames_of(entry.get("window")) for entry in
            ((detail.get("fusion") or {}).get("media_windows") or ())]


def _key(detail: Mapping) -> tuple:
    """Which item this is: row, span, source offset, file, name."""
    return (_row(detail), int(detail["record_in"]), int(detail["duration"]),
            _source_trim(detail, "undo comparison"),
            str(detail.get("source_file") or ""),
            str(detail.get("name") or ""))


def item_projection(detail: Mapping) -> dict:
    row, start, duration, left, source, name = _key(detail)
    return {"row": row, "start": start, "duration": duration,
            "left_offset": left, "source_file": source, "name": name,
            "transform": _transform(detail),
            "comps": int((detail.get("fusion") or {}).get("comp_count")
                         or 0),
            "windows": _windows(detail),
            "clip_color": str(detail.get("clip_color") or ""),
            "enabled": bool(detail.get("enabled", True))}


def _details(tracks: Sequence[Mapping]) -> list:
    return [clip for track in tracks or () for clip in
            (track.get("clips") or ())]


def projection(tracks: Sequence[Mapping]) -> dict:
    """`{row: [item projection, ...]}` in record order."""
    rows: dict = {}
    for detail in _details(tracks):
        rows.setdefault(_row(detail), []).append(item_projection(detail))
    for items in rows.values():
        items.sort(key=lambda item: (item["start"], item["source_file"]))
    return rows


def projection_diff(expected: Mapping, found: Mapping) -> list:
    """Every row where two projections disagree, said per item."""
    out = []
    for row in sorted(set(expected) | set(found)):
        want = list(expected.get(row) or ())
        got = list(found.get(row) or ())
        if want == got:
            continue
        missing = [item for item in want if item not in got]
        extra = [item for item in got if item not in want]
        out.append({"row": row,
                    "expected": [_say(item) for item in missing],
                    "found": [_say(item) for item in extra]})
    return out


def _say(item: Mapping) -> str:
    return (f"{item['name'] or '(unnamed)'} @{item['start']}"
            f"+{item['duration']} from {item['left_offset']}")


def _detail_at(tracks: Sequence[Mapping], row: str, frame: int) -> dict:
    hits = [detail for detail in _details(tracks)
            if _row(detail) == row and int(detail["record_in"]) == frame]
    if len(hits) != 1:
        raise UndoRefused(
            f"{row}@{frame} names {len(hits)} item(s) in the "
            f"journal's read",
            "so it cannot say what that item was",
            "re-read the reel (`ren drift <project>`) and restate the "
            "touch, then re-run `ren touch`")
    return hits[0]


# ── The inverse, as a pure plan ──────────────────────────────────


@dataclass
class InversePlan:
    #: `(after detail, before detail)`: one item the touch moved or
    #: retimed, re-placed at its old span.
    restores: list = field(default_factory=list)
    #: after details: items the touch placed, deleted by the undo.
    deletions: list = field(default_factory=list)
    #: before details: items the touch took, re-placed from `removed`.
    reinserts: list = field(default_factory=list)
    #: `(after detail, before detail)`: same item, transform or comps
    #: changed in place.
    in_place: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.restores or self.deletions or self.reinserts
                    or self.in_place)


def plan_inverse(before: Sequence[Mapping], after: Sequence[Mapping]) -> InversePlan:
    """What the undo must do to turn `after` back into `before`.

    Planned from the two reads alone, never from the touch's op list:
    a plan per op would have to know every op, and an op added later
    would be undone by nothing. A before/after item that cannot be
    paired uniquely REFUSES rather than guessing which went where.
    """
    plan = InversePlan()
    before_details = list(_details(before))
    after_details = list(_details(after))
    after_by_key: dict = {}
    for detail in after_details:
        after_by_key.setdefault(_key(detail), []).append(detail)
    before_only = []
    for detail in before_details:
        twins = after_by_key.get(_key(detail)) or []
        if twins:
            twin = twins.pop(0)
            if item_projection(twin) != item_projection(detail):
                plan.in_place.append((twin, detail))
        else:
            before_only.append(detail)
    after_only = [detail for twins in after_by_key.values()
                  for detail in twins]

    def pairing(detail):
        return (_row(detail), str(detail.get("source_file") or ""),
                _source_trim(detail, "undo comparison"),
                str(detail.get("name") or ""))

    unpaired_after = list(after_only)
    for detail in before_only:
        candidates = [other for other in unpaired_after
                      if pairing(other) == pairing(detail)]
        if len(candidates) > 1:
            raise UndoRefused(
                f"{len(candidates)} items the touch placed on "
                f"{_row(detail)} play {detail.get('name')!r} from source "
                f"frame {detail.get('left_offset')}",
                f"so which one was "
                f"{_row(detail)}@{detail['record_in']} cannot be said. "
                f"Nothing was changed",
                "re-run `ren undo` - if it refuses again the journal "
                "cannot reverse this touch; rebuild the reel instead")
        if candidates:
            unpaired_after.remove(candidates[0])
            plan.restores.append((candidates[0], detail))
        else:
            plan.reinserts.append(detail)
    plan.deletions = unpaired_after
    return plan


# ── The undo, on live handles ────────────────────────────────────


def _rows_of(timeline) -> dict:
    return _ce._rows_of(timeline)


def _at(rows: Mapping, row: str, frame: int):
    hits = [item for item in (rows.get(row) or [])
            if _ce._read(item, "GetStart", None) == int(frame)]
    return hits[0] if len(hits) == 1 else None


def _change_for(after: Mapping, before: Mapping, index: int, *,
                still_resize: bool = False) -> _ce.ItemChange:
    return _ce.ItemChange(
        track_type=after["track_type"], track_index=int(after["track_index"]),
        item_index=int(index), record_frame=int(before["record_in"]),
        duration=int(before["duration"]),
        left_offset=_source_trim(before, "undo placement"),
        previous_record=int(after["record_in"]),
        previous_duration=int(after["duration"]),
        how=_ce.RESIZE_STILL if still_resize else "undo",
        comp_count=(0 if still_resize else
                    int((after.get("fusion") or {}).get("comp_count") or 0)),
        name=str(after.get("name") or ""))


def _is_still_resize_inverse(plan: InversePlan, gate_class: str) -> bool:
    """Recognize only the journaled one-item native-still duration edit."""
    if gate_class != "composed_still_resize":
        return False
    if (len(plan.restores) != 1 or plan.deletions or plan.reinserts
            or plan.in_place):
        return False
    after, before = plan.restores[0]
    row = _row(before)
    source = str(before.get("source_file") or "")
    if (not row.startswith("V") or int(row[1:]) < 3
            or before.get("media_type") != "Still"
            or after.get("media_type") != "Still"
            or not source.lower().endswith(".png")
            or str(after.get("source_file") or "") != source
            or int(before["record_in"]) != int(after["record_in"])
            or int(before["duration"]) == int(after["duration"])
            or _ce.treatment_comps(before) or _ce.treatment_comps(after)):
        return False
    before_view = item_projection(before)
    after_view = item_projection(after)
    before_view.pop("duration")
    after_view.pop("duration")
    return before_view == after_view


def _index_of(rows: Mapping, detail: Mapping) -> int:
    items = rows.get(_row(detail)) or []
    for index, item in enumerate(items):
        if _ce._read(item, "GetStart", None) == int(detail["record_in"]):
            return index
    raise UndoRefused(
        f"{_row(detail)}@{detail['record_in']} is not on the "
        f"live timeline",
        "so the undo cannot address it",
        "the timeline moved since the touch - re-run `ren undo`; if it "
        "still refuses, the touch cannot be undone in place")


def undo_in_place(*, timeline, media_pool, entry: Mapping, entry_root: str,
                  reference, rederiver: _ce.CompRederiver,
                  resolve_media: Callable[[str], Any],
                  work_dir: str, project_folder: str = "",
                  still_resize: bool = False, still_placer=None) -> dict:
    """Reverse one journaled touch on `timeline` itself. Returns a receipt.

    `reference` is a duplicate of `timeline` taken before this call -
    the post-touch state - read only for colour grades. `resolve_media`
    maps a source file path to a live media pool item.
    """
    from library.tools import marker_carry, reel_read

    receipt: dict = {"entry": entry["id"]}
    before = entry["before"]["tracks"]
    after = entry["after"]["tracks"]

    # 1. The timeline must read as the touch left it.
    live = reel_read.read_tracks(timeline)
    moved = projection_diff(projection(after), projection(live))
    if moved:
        raise TimelineMovedSinceTouch(
            f"{entry['final']!r} has changed since touch {entry['id']} "
            f"({', '.join(d['row'] for d in moved)}): {moved}",
            "undoing the touch over it would destroy that work - "
            "nothing was changed",
            "undo the later work first (`ren undo <project> --list` "
            "shows the acts, newest first), then undo this touch")

    # 2. The plan, and every refusal it can raise, before any write.
    plan = plan_inverse(before, after)
    receipt["plan"] = {
        "restores": [f"{_row(a)}@{a['record_in']}->{b['record_in']}"
                     for a, b in plan.restores],
        "deletions": [f"{_row(a)}@{a['record_in']}" for a in plan.deletions],
        "reinserts": [f"{_row(b)}@{b['record_in']}" for b in plan.reinserts],
        "in_place": [f"{_row(a)}@{a['record_in']}" for a, _b in plan.in_place],
    }
    for a, b in plan.in_place:
        had = int((b.get("fusion") or {}).get("comp_count") or 0)
        has = int((a.get("fusion") or {}).get("comp_count") or 0)
        if has < had:
            raise UndoRefused(
                f"{_row(a)}@{a['record_in']} carries {has} "
                f"comp(s) where it carried {had}",
                "a touch never removes a comp in place, so the journal "
                "cannot reverse it. Nothing was changed",
                "rebuild the reel (`ren build <project>`) instead of "
                "undoing in place")
    if plan.empty:
        raise UndoRefused(
            f"touch {entry['id']} left {entry['final']!r} "
            f"reading exactly as before it",
            "there is nothing to undo",
            "pick another entry (`ren undo <project> --list`), or leave "
            "the reel as it is")
    removed = {(str(r["row"]), int(r["record_frame"])): r
               for r in entry.get("removed") or ()}
    insertions, reinsert_captures = [], []
    for detail in plan.reinserts:
        capture = removed.get((_row(detail), int(detail["record_in"])))
        if capture is None:
            raise UndoRefused(
                f"the touch took {_row(detail)}@"
                f"{detail['record_in']} and the journal holds no capture "
                f"of it",
                "so it cannot be put back. Nothing was changed",
                "rebuild the reel (`ren build <project>`) - this touch "
                "cannot be undone in place")
        mpi = resolve_media(capture["source_file"])
        if mpi is None:
            raise UndoRefused(
                f"{capture['source_file']!r} is not in the media pool",
                f"so {_row(detail)}@{detail['record_in']} cannot be "
                f"re-placed. Nothing was changed",
                "re-import the source file into the media pool, then "
                "re-run `ren undo`")
        insertions.append(_ce.Insertion(
            track_type=detail["track_type"],
            track_index=int(detail["track_index"]), media_pool_item=mpi,
            left_offset=int(capture["left_offset"]),
            duration=int(capture["duration"]),
            record_frame=int(capture["record_frame"]),
            name=capture.get("name") or "",
            properties=dict(capture.get("properties") or {})))
        reinsert_captures.append((detail, capture, mpi))
    rows = _rows_of(timeline)
    provisional = [_change_for(a, b, _index_of(rows, a),
                               still_resize=still_resize)
                   for a, b in plan.restores]
    _ce.assert_rederivation_reachable(provisional, rederiver, insertions)
    reference_rows = _rows_of(reference)
    grade_by_frame = {}
    for a, _b in plan.restores:
        source = _at(reference_rows, _row(a), a["record_in"])
        if source is None:
            raise UndoRefused(
                f"the reference copy holds no single item at "
                f"{_row(a)}@{a['record_in']} to carry its grade from",
                "without the reference there is no grade to restore. "
                "Nothing was changed",
                "rebuild the reel (`ren build <project>`) - this touch "
                "cannot be undone in place")
        grade_by_frame[(_row(a), int(a["record_in"]))] = source

    # 3. The captain's clip markers on everything about to be re-placed.
    replaced = {(a["track_type"], int(a["track_index"]), int(a["record_in"]))
                for a, _b in plan.restores} | {
        (a["track_type"], int(a["track_index"]), int(a["record_in"]))
        for a in plan.deletions}
    notes = [marker for marker in marker_carry.read_clip_markers(timeline)
             if marker.get("anchor") and (
                 marker["anchor"]["track_type"],
                 int(marker["anchor"]["track_index"]),
                 int(marker["anchor"]["timeline_start"])) in replaced]

    # Everything from here writes. A refusal past this line is no
    # longer "nothing was changed", so it is raised as a failed undo.
    try:
        _write_inverse(timeline, media_pool, entry_root, plan,
                       rows, insertions, reinsert_captures,
                       grade_by_frame, rederiver, work_dir, receipt,
                       still_resize=still_resize,
                       still_placer=still_placer,
                       write_context={
                           "project_folder": project_folder or None,
                           "project": entry.get("resolve_project"),
                           "timeline_name": entry["final"],
                       })
    except UndoRefused as refused:
        raise UndoNotVerified(str(refused)) from refused

    # Rows the touch ADDED (`add_row`) go with it, once emptied - the
    # item projection has no row for an empty track, so it cannot see one.
    before_rows = sum(1 for t in before
                      if str(t.get("type", "")).lower().startswith("v"))
    dropped_rows = []
    while int(timeline.GetTrackCount("video") or 0) > before_rows:
        index = int(timeline.GetTrackCount("video"))
        if timeline.GetItemListInTrack("video", index):
            break
        name = timeline.GetTrackName("video", index)
        if not timeline.DeleteTrack("video", index):
            break
        dropped_rows.append(f"V{index} {name}")
    receipt["rows_removed"] = dropped_rows

    # 7. The notes, carried by source frame onto what now plays them.
    carried, uncarried = marker_carry.plan_clip_carry(notes, timeline,
                                                      entry["final"])
    declined = (marker_carry.place_clip_markers(timeline, carried)
                if carried else [])
    if uncarried or declined:
        marker_carry.report_clip(entry["final"], carried,
                                 list(uncarried) + list(declined))
    receipt["markers"] = {"carried": len(carried) - len(declined),
                          "uncarried": [m.get("note", "") for m in
                                        list(uncarried) + list(declined)]}

    # 8. The verdict is the read, never a return value.
    restored = reel_read.read_tracks(timeline)
    wrong = projection_diff(projection(before), projection(restored))
    if wrong:
        raise UndoNotVerified(
            f"{entry['final']!r} does not read as it did before touch "
            f"{entry['id']}: {wrong}")
    receipt["verified"] = True
    return receipt


def _write_inverse(timeline, media_pool, entry_root, plan, rows,
                   insertions, reinsert_captures, grade_by_frame,
                   rederiver, work_dir, receipt,
                   write_context=None, *, still_resize=False,
                   still_placer=None) -> None:
    """Steps 4-6 of the undo: every write, in the one order that works."""
    # 4. In place: transforms and comps the touch changed on items it
    # did not move.
    receipt["in_place"] = []
    for a, b in plan.in_place:
        item = _at(rows, _row(a), a["record_in"])
        receipt["in_place"].append(
            _revert_in_place(item, a, b, write_context=write_context))

    # 5. Delete what the touch placed, then re-place the rest.
    if plan.deletions:
        victims = [_at(rows, _row(a), a["record_in"]) for a in plan.deletions]
        timeline.DeleteClips(victims, False)
        receipt["deleted"] = len(victims)
    rows = _rows_of(timeline)
    changes = [_change_for(a, b, _index_of(rows, a),
                           still_resize=still_resize)
               for a, b in plan.restores]
    grade_sources = {(c.row, c.item_index):
                     grade_by_frame[(c.row, c.previous_record)]
                     for c in changes}
    if changes or insertions:
        composed = _ce.apply_composed_edit(
            timeline=timeline, media_pool=media_pool, changes=changes,
            insertions=insertions,
            comp_dir=os.path.join(work_dir, "comps"),
            withheld_dir=os.path.join(work_dir, "withheld"),
            rederiver=rederiver, grade_sources=grade_sources,
            link_rows={}, picture_row="V1",
            still_placer=still_placer,
            write_context=write_context)
        receipt["composed"] = {"plan": composed.plan,
                               "verified": composed.verified,
                               "rederived": composed.rederived}

    # 6. What the composition cannot know: each item's own prior state.
    rows = _rows_of(timeline)
    for detail, capture, mpi in reinsert_captures:
        item = _at(rows, _row(detail), detail["record_in"])
        comps = tuple(_ce.CapturedComp(
            index=int(comp["index"]),
            path=os.path.join(entry_root, comp["path"]),
            window=comp.get("window")) for comp in capture.get("comps") or ())
        _ce.restore_item(item, _ce.ItemCapture(
            change=_identity_change(detail),
            properties=dict(capture.get("properties") or {}),
            geometry={}, media_pool_item=mpi, restorable_comps=comps),
            write_context=write_context)
    for _a, b in plan.restores:
        item = _at(rows, _row(b), b["record_in"])
        identity = {
            key: b[key]
            for key in ("name", "source_file", "source_in_frame",
                        "source_out_frame")
            if key in b
        }
        diff = _ce.set_properties(
            item, _transform(b), write_context=write_context,
            item_identity={**identity, "row": _row(b),
                           "record_frame": b["record_in"]},
            old_properties=_transform(_a))
        if diff:
            raise UndoNotVerified(
                f"{_row(b)}@{b['record_in']} did not take its prior "
                f"transform back: {diff}.")
    for detail in [b for _a, b in plan.restores] + list(plan.reinserts):
        colour = detail.get("clip_color") or ""
        item = _at(rows, _row(detail), detail["record_in"])
        if colour and _ce._read(item, "GetClipColor", "") != colour:
            item.SetClipColor(colour)


def _revert_in_place(item, after: Mapping, before: Mapping, *,
                     write_context=None) -> dict:
    if item is None:
        raise UndoRefused(
            f"{_row(after)}@{after['record_in']} is not on the "
            f"live timeline to revert in place",
            "the timeline moved under the undo",
            "re-run `ren undo` against the live timeline; if it still "
            "refuses, the touch cannot be undone in place")
    out = {"row": _row(after), "record_frame": int(after["record_in"])}
    if _transform(after) != _transform(before):
        identity = {
            key: after[key]
            for key in ("name", "source_file", "source_in_frame",
                        "source_out_frame")
            if key in after
        }
        diff = _ce.set_properties(
            item, _transform(before), write_context=write_context,
            item_identity={**identity, **out},
            old_properties=_transform(after))
        if diff:
            raise UndoNotVerified(
                f"{out['row']}@{out['record_frame']} did not take its "
                f"prior transform back: {diff}.")
        out["transform"] = True
    if bool(after.get("enabled", True)) != bool(before.get("enabled", True)):
        item.SetClipEnabled(bool(before.get("enabled", True)))
        if bool(item.GetClipEnabled()) != bool(before.get("enabled", True)):
            raise UndoNotVerified(
                f"{out['row']}@{out['record_frame']} did not switch back "
                f"{'on' if before.get('enabled', True) else 'off'}.")
        out["enabled"] = bool(before.get("enabled", True))
    had = int((before.get("fusion") or {}).get("comp_count") or 0)
    has = int(_ce._read(item, "GetFusionCompCount", 0) or 0)
    if has > had:
        # Comps a touch ADDED (`entry_motion`) sit after the ones the
        # item carried; they go, newest first, by the name Resolve reads.
        names = list(item.GetFusionCompNameList() or [])
        for name in reversed(names[had:]):
            item.DeleteFusionCompByName(name)
        out["comps_removed"] = has - had
    return out


# ── The orchestration: journal + Resolve ─────────────────────────


def _connect(resolve_name: str):
    from library.tools.reel_build import _connect_resolve_project
    return _connect_resolve_project(resolve_name)


def _resolve_name(project_folder: str) -> str:
    from library.tools.project_registry import get_project
    return get_project(project_folder).resolve.project_name


def undo_touch(project_folder: str, entry_id: str, *, connect=None,
               rederiver_override=None, accept_editor_changes=None) -> dict:
    """Reverse one touch in place, under the lease, with a verified read."""
    from library.tools.resolve_lock import under_lease

    entry = read_entry(project_folder, entry_id)
    if entry.get("status") != STATUS_APPLIED:
        raise UndoRefused(
            f"touch {entry_id} is {entry.get('status')!r}, not applied",
            "there is nothing of it on the timeline to undo",
            "pick an applied entry (`ren undo <project> --list` shows "
            "them, newest first)")

    @under_lease(f"undo touch on {entry['final']}")
    def _guarded():
        return _undo_touch_connected(project_folder, entry,
                                     connect or _connect,
                                     rederiver_override,
                                     accept_editor_changes)

    return _guarded()


def _undo_touch_connected(project_folder, entry, connect,
                          rederiver_override,
                          accept_editor_changes=None) -> dict:
    from library.tools import (
        plan_provenance,
        reel_replace_guard,
        reel_retirement,
        reel_touchup,
    )
    from library.tools.reel_build import backup_name, timelines_to_replace
    from library.tools.reel_replace_guard import snapshot_timeline
    from library.tools.resolve_lock import cursor_fence
    from library.tools.versions import reel_versions

    final = entry["final"]
    project = connect(entry["resolve_project"])
    pool = project.GetMediaPool()
    found = {t.GetName(): t for t in timelines_to_replace(project, {final})}
    if final not in found:
        raise UndoRefused(
            f"no timeline called {final!r} is in Resolve project "
            f"{entry['resolve_project']!r}",
            "the touch cannot be undone on a timeline that is not there",
            "restore the timeline in Resolve (or rebuild the reel), then "
            "re-run `ren undo`")
    live = found[final]
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    inventory_before = reel_replace_guard.timeline_inventory(project)
    reference_name = backup_name(final)
    if timelines_to_replace(project, {reference_name}):
        raise UndoRefused(
            f"{reference_name!r} is already in the project",
            "left by an interrupted run",
            "clear it in Resolve before re-running `ren undo`")

    plan_provenance.assert_not_editor_timeline(review_dir, live)
    preservation_before = reel_replace_guard.full_timeline_snapshot(
        live, project, project_folder)
    expected_after = entry.get("preservation_after") or preservation_before
    expected_before = entry.get("preservation_before") or preservation_before
    editor_report = reel_replace_guard.protect_editor_changes(
        project_folder, final, preservation_before, expected_after,
        expected_before,
        accept=reel_replace_guard.accepts_editor_changes(
            final, accept_editor_changes))
    inventory_operation = plan_provenance.begin_timeline_inventory(
        review_dir, "undo touch", inventory_before,
        ren_created_names={final})
    plan_provenance.record_timeline_snapshot(
        review_dir, final, preservation_before,
        action="before_undo_touch", action_journal=entry["id"],
        last_known=False)

    before = entry["before"]["tracks"]
    plan = plan_inverse(before, entry["after"]["tracks"])
    length_changes = any(int(a["duration"]) != int(b["duration"])
                         for a, b in plan.restores)
    still_resize = _is_still_resize_inverse(
        plan, str(entry.get("gate_class") or ""))
    root = entry_dir(project_folder, entry["id"])
    if rederiver_override is not None:
        rederiver = rederiver_override
    elif still_resize:
        from library.tools.reel_touchup import _StillResizeRederiver
        rederiver = _StillResizeRederiver()
    elif not length_changes:
        rederiver = reel_touchup._NullRederiver(
            "the undo changes no played length")
    else:
        manifest_file = entry.get("fusion_manifest")
        if not manifest_file:
            raise UndoRefused(
                f"undoing touch {entry['id']} changes a played length "
                f"and its journal holds no fusion manifest",
                "there is no route to the comp pass. Nothing was changed",
                "rebuild the reel (`ren build <project>`) - this touch "
                "cannot be undone in place")
        with open(os.path.join(root, manifest_file), encoding="utf-8") as fh:
            manifest = json.load(fh)
        reel_touchup.check_manifest_matches(manifest, before)
        rederiver = _ce.ReelLookRederiver(manifest, project_folder,
                                          entry["resolve_project"], final)
        # The pass maps specs to items by position along each row, so
        # it is told how many items the undo leaves there: `before`'s.
        rederiver.expected_row_counts = {
            row: len(items) for row, items in projection(before).items()}

    reference = live.DuplicateTimeline(reference_name)
    if reference is None or reference.GetName() != reference_name:
        plan_provenance.finish_timeline_inventory(
            review_dir, inventory_operation,
            reel_replace_guard.timeline_inventory(project))
        raise UndoRefused(
            f"Resolve would not duplicate {final!r} as the undo's reference",
            "without the reference copy there is nothing to undo "
            "against - nothing was changed",
            "clear bin space in Resolve and re-run `ren undo`")
    from library.tools.project_layout import Area, ProjectLayout
    work_dir = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "undo", entry["id"])
    still_placer = None
    if still_resize:
        from library.tools.reel_build import _connect_resolve
        from library.tools.still_placement import place_still_exact
        resolve_app = _connect_resolve()
        try:
            fps = float(live.GetSetting("timelineFrameRate"))
        except (TypeError, ValueError, AttributeError) as unreadable:
            raise UndoRefused(
                f"the timeline frame rate is unreadable ({unreadable})",
                "so the still's exact undo duration cannot be placed. "
                "Nothing was changed",
                "re-read the reel settings and retry `ren undo`") from unreadable

        def still_placer(capture):
            change = capture.change
            return place_still_exact(
                resolve_app, project, pool, live, capture.media_pool_item,
                int(change.duration), int(change.record_frame),
                int(change.track_index), fps, timeline_name=final)
    try:
        with cursor_fence(project, live, f"undo touch on {final}"):
            receipt = undo_in_place(
                timeline=live, media_pool=pool, entry=entry,
                entry_root=root, reference=reference, rederiver=rederiver,
                resolve_media=lambda path: reel_touchup.pool_item_for_path(
                    pool, path),
                work_dir=work_dir, project_folder=project_folder,
                still_resize=still_resize, still_placer=still_placer)
            rows = snapshot_timeline(live, final, side="staged")
    except UndoRefused:
        # Raised before the first write: the timeline is as the touch
        # left it, so the reference is only debris.
        reel_retirement.delete_backups(project, pool,
                                       {reference_name: reference})
        plan_provenance.finish_timeline_inventory(
            review_dir, inventory_operation,
            reel_replace_guard.timeline_inventory(project))
        raise
    except Exception as failed:
        entry["status"] = STATUS_UNDO_FAILED
        entry["undo_failed"] = {"at": _now(), "why": repr(failed),
                                "reference": reference_name}
        write_entry(project_folder, entry)
        try:
            reference_id = reference.GetUniqueId()
        except Exception:  # noqa: BLE001
            reference_id = None
        plan_provenance.record_unattributed_timeline_changes(
            review_dir, inventory_before,
            reel_replace_guard.timeline_inventory(project),
            operation="failed undo touch",
            ren_owned_ids={reference_id} if reference_id else (),
            operation_id=inventory_operation)
        plan_provenance.finish_timeline_inventory(
            review_dir, inventory_operation,
            reel_replace_guard.timeline_inventory(project))
        raise UndoNotVerified(
            f"the undo of {entry['id']} did not finish ({failed}). The "
            f"post-touch state is safe on {reference_name!r} and the "
            f"pre-touch state is in {_entry_path(project_folder, entry['id'])}.") \
            from failed
    try:
        preservation_after = reel_replace_guard.full_timeline_snapshot(
            live, project, project_folder)
        plan_provenance.record_timeline_snapshot(
            review_dir, final, preservation_after, action="undo_touch",
            action_journal=entry["id"])
        for record_id in editor_report.get("carried", ()):
            plan_provenance.resolve_editor_change(
                review_dir, final, record_id, status="carried")
        for record_id in editor_report.get("superseded", ()):
            plan_provenance.resolve_editor_change(
                review_dir, final, record_id, status="superseded",
                superseded_by=f"--accept-editor-changes {final}")
        reference_id = reference.GetUniqueId()
        plan_provenance.record_unattributed_timeline_changes(
            review_dir, inventory_before,
            reel_replace_guard.timeline_inventory(project),
            operation="undo touch",
            ren_owned_ids={str(reference_id)} if reference_id else (),
            operation_id=inventory_operation)
    except Exception as snapshot_failed:
        try:
            reference_id = reference.GetUniqueId()
        except Exception:  # noqa: BLE001
            reference_id = None
        plan_provenance.record_unattributed_timeline_changes(
            review_dir, inventory_before,
            reel_replace_guard.timeline_inventory(project),
            operation="undo touch snapshot failure",
            ren_owned_ids={str(reference_id)} if reference_id else (),
            operation_id=inventory_operation)
        plan_provenance.finish_timeline_inventory(
            review_dir, inventory_operation,
            reel_replace_guard.timeline_inventory(project))
        raise UndoNotVerified(
            f"the undo of {entry['id']} was verified, but its full "
            f"timeline snapshot could not be persisted ({snapshot_failed}). "
            f"The post-touch state is recoverable on {reference_name!r}.") \
            from snapshot_failed
    try:
        receipt["reference_deleted"] = reel_retirement.delete_backups(
            project, pool, {reference_name: reference})["deleted"]
    finally:
        plan_provenance.finish_timeline_inventory(
            review_dir, inventory_operation,
            reel_replace_guard.timeline_inventory(project))
    version = reel_versions.record(project_folder, final,
                                   kind=reel_versions.KIND_UNDO, rows=rows,
                                   undoes=entry.get("version"),
                                   journal=entry["id"])
    if entry.get("version"):
        reel_versions.mark_undone(project_folder, final,
                                  int(entry["version"]),
                                  int(version["version"]))
    receipt["version"] = version["version"]
    receipt["signature_closed"] = reel_touchup.close_signature(
        project_folder, project, final)
    # The undo half of the per-reel edit history
    # (`library/tools/reel_edit_history.py`): the touch reversed in
    # place, naming the version it reversed. Never-fail
    # instrumentation, like every other history hook.
    try:
        from library.tools import reel_edit_history as _history

        _history.try_record_ren_act(
            os.path.join(project_folder, "pipeline_output", "review"),
            final, act=_history.ACT_UNDO,
            summary=(f"Ren undo on {final} "
                     f"(version {version['version']} reverses "
                     f"version {entry.get('version')}, "
                     f"journal {entry['id']})"),
            refs={"version": version["version"],
                  "undoes": entry.get("version"),
                  "journal": entry["id"],
                  "rows_digest_after": version.get("rows_digest")})
    except Exception:  # noqa: BLE001, S110 - history never fails an undo
        pass
    entry["status"] = STATUS_UNDONE
    entry["undo"] = {**receipt, "at": _now()}
    write_entry(project_folder, entry)
    return receipt


# ── Rolling a rebuild back ───────────────────────────────────────


def restore_plan_moment(project_folder: str, moment: Mapping) -> str:
    """Put one reel's recorded plan moment back into the live plan.

    Every other reel's moment is left byte-for-byte as it is; the plan
    as it stood is archived first (`plan_provenance.archive_plan`).
    """
    from library.tools.plan_provenance import archive_plan
    from library.tools.reel_proposal import proposal_path
    from library.tools.stable_json import dumps_stable

    path = str(proposal_path(project_folder))
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    moments = list(document.get("moments") or ())
    hits = [i for i, m in enumerate(moments)
            if int(m.get("number", -1)) == int(moment["number"])]
    if len(hits) != 1:
        raise UndoRefused(
            f"the plan holds {len(hits)} moment(s) numbered "
            f"{moment['number']}",
            "so the rollback cannot say which to restore. Nothing was "
            "changed",
            "fix the duplicated reel number in the proposal, then "
            "re-run `ren undo`")
    archive_plan(path)
    moments[hits[0]] = dict(moment)
    document["moments"] = moments
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(dumps_stable(document))
    return path


def _build_one_reel(project_folder: str, reel: int, supersede=(),
                    accept_editor_changes=None) -> int:
    """`manage_project.py build-reels --only-reel N`, in its OWN process.

    A build creates timelines and the touches re-applied after it
    import Fusion comps, and those two never share a process (AGENTS.md
    5). The build needs the ML interpreter (AGENTS.md 9), so it runs
    under the one `shared_environment` finds, or refuses naming why.
    """
    import subprocess
    from pathlib import Path

    from library.tools.shared_environment import python_interpreter

    repo = Path(__file__).resolve().parents[2]
    python, why_not = python_interpreter(str(repo))
    if not python:
        raise UndoRefused(
            "the rollback's rebuild needs the ML interpreter and none "
            "was found",
            f"{why_not}",
            "set up the ML environment (docs/ML_ENVIRONMENT.md), then "
            "re-run `ren undo`")
    argv = [python, str(repo / "manage_project.py"), "build-reels",
            str(project_folder), "--only-reel", str(int(reel))]
    for name in supersede or ():
        argv += ["--supersede", name]
    accepted = ([accept_editor_changes]
                if isinstance(accept_editor_changes, (str, int)) else
                accept_editor_changes or ())
    for name in accepted:
        argv += ["--accept-editor-changes", str(name)]
    return subprocess.run(argv, cwd=str(repo), check=False).returncode


def rollback_rebuild(project_folder: str, final: str, act: Mapping, *,
                     read_live_rows: Callable[[str], Mapping],
                     build: Callable[[str, int], int] | None = None,
                     touch: Callable[[str, Mapping], Any] | None = None,
                     supersede=(), accept_editor_changes=None,
                     restore_archive: Callable | None = None) -> dict:
    """Roll a rebuild back to the version before it, and say if it got there.

    `read_live_rows(final)` reads the live rows; `build` and `touch` are
    the reels process and `reel_touchup.apply_touchup`, seams for tests.
    """
    from library.tools.versions import reel_versions, rounds

    live = dict(read_live_rows(final) or {})
    from library.tools.reel_replace_guard import accepts_editor_changes
    accepts_manual_edits = accepts_editor_changes(
        final, accept_editor_changes)
    if (rounds.digest_rows(live) != act.get("rows_digest")
            and not accepts_manual_edits):
        raise TimelineMovedSinceTouch(
            f"{final!r} no longer reads as version {act['version']} "
            f"({act['kind']}) - it was changed outside Ren since",
            "rolling back would destroy that work; nothing was changed",
            "re-apply the outside change after the rollback, or leave "
            "the timeline as it is - `ren undo` will not destroy it")
    target = reel_versions.state_before(project_folder, final,
                                        int(act["version"]))
    base = reel_versions.build_before(project_folder, final,
                                      int(act["version"]))
    if target is None or base is None or not base.get("plan_moment"):
        raise UndoRefused(
            f"version {act['version']} of {final!r} is the first recorded "
            f"build with a plan behind it",
            "so there is no earlier version to roll back to",
            "there is nothing to roll back - leave the reel as it is")
    if base["plan_moment"] == act.get("plan_moment"):
        return _rollback_from_verified_archive(
            project_folder, final, act, target,
            read_live_rows=read_live_rows, restore_archive=restore_archive,
            supersede=supersede)
    replays = [entry for entry in reel_versions.live_acts(project_folder,
                                                          final)
               if entry["kind"] == reel_versions.KIND_TOUCH
               and int(base["version"]) < int(entry["version"])
               < int(act["version"])]
    reel = int(base["plan_moment"]["number"])
    restore_plan_moment(project_folder, base["plan_moment"])
    reel_versions.set_pending_rollback(project_folder, final,
                                       int(act["version"]))
    try:
        if build is None:
            code = _build_one_reel(project_folder, reel, supersede,
                                   accept_editor_changes)
        else:
            code = build(project_folder, reel)
    except Exception:  # noqa: BLE001 - any rebuild failure triggers archive recovery
        reel_versions.take_pending_rollback(project_folder, final)
        return _rollback_from_verified_archive(
            project_folder, final, act, target,
            read_live_rows=read_live_rows, restore_archive=restore_archive,
            supersede=supersede)
    unplaced = reel_versions.take_pending_rollback(project_folder, final)
    if code or unplaced is not None:
        # The promotion consumes the marker; one still standing means
        # nothing was promoted, and it must not label a later build.
        return _rollback_from_verified_archive(
            project_folder, final, act, target,
            read_live_rows=read_live_rows, restore_archive=restore_archive,
            supersede=supersede)
    replayed = []
    try:
        for entry in replays:
            journal = read_entry(project_folder, entry["journal"])
            spec = dict(journal["spec"])
            (touch or _apply_touch)(project_folder, spec)
            replayed.append(entry["journal"])
    except Exception:  # noqa: BLE001 - any replay failure triggers archive recovery
        return _rollback_from_verified_archive(
            project_folder, final, act, target,
            read_live_rows=read_live_rows, restore_archive=restore_archive,
            supersede=supersede)
    after = dict(read_live_rows(final) or {})
    if rounds.digest_rows(after) != target["rows_digest"]:
        return _rollback_from_verified_archive(
            project_folder, final, act, target,
            read_live_rows=read_live_rows, restore_archive=restore_archive,
            supersede=supersede)
    return {"final": final, "rolled_back": act["version"],
            "to": target["version"], "replayed": replayed}


def _rollback_from_verified_archive(project_folder: str, final: str,
                                    act: Mapping, target: Mapping, *,
                                    read_live_rows: Callable,
                                    restore_archive: Callable | None,
                                    supersede=()) -> dict:
    """Restore the exact retained version when semantic replay diverges."""
    from library.tools.versions import reel_versions, rounds

    receipt = target.get("recovery_archive")
    if not isinstance(receipt, Mapping):
        raise UndoRefused(
            f"version {target.get('version')} of {final!r} has no verified "
            "recovery archive", "plan and touch replay cannot restore the "
            "exact state", "retain or create a verified timeline archive "
            "before retrying the rollback")
    if rounds.digest_rows(target["rows"]) != target["rows_digest"]:
        raise UndoRefused(
            f"version {target.get('version')} of {final!r} has an inconsistent "
            "row snapshot", "its archive cannot be attributed to that state",
            "repair the version record from its source of truth")
    from library.tools import reel_signoff
    declared = reel_signoff.parse_supersede(supersede)
    signoff = reel_signoff.assert_declared(
        project_folder, final, declared, command="ren undo")
    (restore_archive(project_folder, final, target, receipt)
     if restore_archive else _restore_archive_by_resolve_id(
         project_folder, final, target, receipt,
         expected_current_id=(act.get("recovery_archive") or {}).get(
             "timeline_id")))
    after = dict(read_live_rows(final) or {})
    if rounds.digest_rows(after) != target["rows_digest"]:
        raise UndoNotVerified(
            f"archive restoration for {final!r} did not read back as "
            f"version {target['version']}; the timeline is preserved for "
            "recovery and the rollback was not recorded")
    entry = reel_versions.record(
        project_folder, final, kind=reel_versions.KIND_ROLLBACK,
        rows=target["rows"], undoes=int(act["version"]),
        recovery_archive=dict(receipt),
        restored_from_timeline_id=receipt["timeline_id"])
    reel_versions.mark_undone(
        project_folder, final, int(act["version"]), entry["version"])
    if signoff:
        reel_signoff.supersede(project_folder, final)
    return {"final": final, "rolled_back": act["version"],
            "to": target["version"], "restored_from_archive": True,
            "timeline_id": receipt["timeline_id"]}


def _restore_archive_by_resolve_id(project_folder: str, final: str,
                                   target: Mapping, receipt: Mapping, *,
                                   expected_current_id: str | None) -> dict:
    """Import, verify, and swap a retained DRT under an exclusive lease."""
    import os
    import uuid
    from pathlib import Path

    from library.tools import reel_read
    from library.tools.resolve_archive import read_archive
    from library.tools.resolve_lock import resolve_lease
    from library.tools.versions import rounds, store

    if not expected_current_id:
        raise UndoRefused(
            f"the current version of {final!r} has no Resolve identity in "
            "its recovery receipt", "the live generation cannot be matched "
            "to the version being rolled back", "record a verified recovery "
            "archive for the current version before retrying")

    archive_path = Path(str(receipt["archive_path"]))
    if (not archive_path.is_file()
            or store._sha256(archive_path) != receipt.get("archive_sha256")):
        raise UndoRefused(
            f"the recovery archive for {final!r} is missing or changed",
            "its recorded hash does not verify", "recover the immutable DRT "
            "from project version control before retrying")
    facts = read_archive(archive_path)
    archived_items = [item for timeline in facts["timelines"]
                      for track in timeline["tracks"]
                      for item in track["items"]]
    manifest = list(receipt.get("assets") or [])
    for asset in manifest:
        path = Path(str(asset["path"]))
        if (not path.is_file() or path.stat().st_size != int(asset["bytes"])
                or store._sha256(path) != asset["sha256"]):
            raise UndoRefused(
                f"an asset for version {target['version']} of {final!r} "
                "is missing or changed", "the DRT alone cannot prove the "
                "restoration inventory", "restore the hashed asset before "
                "retrying")
    archive_paths = {os.path.normcase(os.path.realpath(str(item["media_path"])))
                     for item in archived_items if item.get("media_path")}
    manifest_paths = {os.path.normcase(os.path.realpath(str(item["path"])))
                      for item in manifest}
    if (archive_paths != manifest_paths
            or len(archived_items) != int(receipt.get("inventory_items", -1))
            or rounds.digest_rows(target["rows"]) != receipt.get("rows_digest")):
        raise UndoRefused(
            f"the recovery archive for version {target['version']} of "
            f"{final!r} failed inventory verification", "its contents do "
            "not match the version receipt", "recover the correct immutable "
            "archive and retry")

    resolve_name = _resolve_name(project_folder)
    with resolve_lease(f"restore reel version {target['version']}"):
        project = _connect(resolve_name)
        pool = project.GetMediaPool()
        timelines = [project.GetTimelineByIndex(index)
                     for index in range(1, project.GetTimelineCount() + 1)]
        before_ids = [str(t.GetUniqueId() or "") for t in timelines if t]
        if (any(not uid for uid in before_ids)
                or len(before_ids) != len(set(before_ids))):
            raise UndoRefused(
                f"Resolve timeline identities are missing or reused while "
                f"restoring {final!r}", "archive import cannot be safely "
                "scoped", "resolve the duplicate or missing IDs before "
                "undo")
        finals = [timeline for timeline in timelines
                  if timeline and timeline.GetName() == final]
        current_id = (str(finals[0].GetUniqueId() or "")
                      if len(finals) == 1 else "")
        if (len(finals) != 1 or not current_id
                or sum(str(t.GetUniqueId() or "") == current_id
                       for t in timelines if t) != 1):
            raise UndoRefused(
                f"cannot resolve one live timeline {final!r} by its Resolve "
                "identity", "archive restoration would be ambiguous",
                "resolve the duplicate or missing timeline before undo")
        current = finals[0]
        if current_id != str(expected_current_id):
            raise UndoRefused(
                f"the live {final!r} is Resolve ID {current_id!r}, not the "
                f"recorded current version {expected_current_id!r}",
                "a different generation occupies the final name",
                "restore the recorded generation or adopt the current one "
                "before undo")
        import_name = archive_path.stem
        if any(t.GetName() == import_name for t in timelines if t):
            raise UndoRefused(
                f"Resolve already contains the recovery import name "
                f"{import_name!r}", "the DRT donor would be ambiguous",
                "resolve the existing recovery timeline before retrying")
        cursor = project.GetCurrentTimeline()
        try:
            donor = pool.ImportTimelineFromFile(
                str(archive_path), {"timelineName": import_name,
                                    "importSourceClips": False})
        except Exception as exc:
            partial = [t for index in range(
                1, project.GetTimelineCount() + 1)
                       if (t := project.GetTimelineByIndex(index))
                       and t.GetName() == import_name]
            if len(partial) == 1:
                pool.DeleteTimelines(partial)
            raise UndoNotVerified(
                f"Resolve failed importing the verified archive for "
                f"{final!r}: {exc!r}; the current generation remains") from exc
        if cursor:
            from library.tools.resolve_lock import assert_current_timeline
            try:
                assert_current_timeline(project, cursor)
            except Exception as exc:
                partial = [t for index in range(
                    1, project.GetTimelineCount() + 1)
                           if (t := project.GetTimelineByIndex(index))
                           and t.GetName() == import_name]
                if len(partial) == 1:
                    pool.DeleteTimelines(partial)
                raise UndoNotVerified(
                    f"Resolve moved its cursor while importing the archive "
                    f"for {final!r} and it could not be restored: {exc!r}") \
                    from exc
        inventory = [project.GetTimelineByIndex(index)
                     for index in range(1, project.GetTimelineCount() + 1)]
        imported_matches = [t for t in inventory if t
                            and t.GetName() == import_name]
        if len(imported_matches) != 1:
            if imported_matches:
                pool.DeleteTimelines(imported_matches)
            raise UndoNotVerified(
                f"Resolve did not create exactly one named archive "
                f"timeline for {final!r}; the current generation remains")
        imported = imported_matches[0]
        donor_id = str(imported.GetUniqueId() or "")
        if (not donor_id or sum(str(t.GetUniqueId() or "") == donor_id
                                for t in inventory if t) != 1
                or donor_id in before_ids):
            pool.DeleteTimelines([imported])
            raise UndoNotVerified(
                f"Resolve reused or omitted the imported timeline ID for "
                f"{final!r}; nothing was replaced")
        if not donor:
            pool.DeleteTimelines([imported])
            raise UndoNotVerified(
                f"Resolve returned no handle for the imported archive of "
                f"{final!r}; the current generation remains in place")
        if str(donor.GetUniqueId() or "") != donor_id:
            pool.DeleteTimelines([imported])
            raise UndoNotVerified(
                f"Resolve returned a different timeline from the newly "
                f"imported archive for {final!r}")
        donor = imported
        donor_rows = reel_read.rows_of({"tracks": reel_read.read_tracks(donor)})
        if rounds.digest_rows(donor_rows) != target["rows_digest"]:
            pool.DeleteTimelines([donor])
            raise UndoNotVerified(
                f"imported archive {receipt['timeline_id']!r} does not read "
                f"as version {target['version']}; the current generation "
                "remains in place")
        donor_name = donor.GetName()
        backup = f"{final} (undo recovery {uuid.uuid4().hex[:8]})"
        if not current.SetName(backup):
            pool.DeleteTimelines([donor])
            raise UndoNotVerified(
                f"Resolve would not preserve the current {final!r} before "
                "archive restoration")
        if not donor.SetName(final):
            current.SetName(final)
            pool.DeleteTimelines([donor])
            raise UndoNotVerified(
                f"Resolve would not install the verified archive as "
                f"{final!r}; the prior generation was restored")
        readback = reel_read.rows_of({"tracks": reel_read.read_tracks(donor)})
        if rounds.digest_rows(readback) != target["rows_digest"]:
            donor.SetName(donor_name)
            current.SetName(final)
            pool.DeleteTimelines([donor])
            raise UndoNotVerified(
                f"archive timeline {receipt['timeline_id']!r} changed during "
                f"restore of {final!r}; the prior generation was restored")
        if not pool.DeleteTimelines([current]):
            raise UndoNotVerified(
                f"{final!r} reads as the archived version, but Resolve could "
                f"not remove the preserved backup {backup!r}; both remain")
    return {"restored": True, "timeline_id": receipt["timeline_id"]}


def _apply_touch(project_folder: str, spec: Mapping):
    from library.tools import reel_touchup
    return reel_touchup.apply_touchup(project_folder, spec)


# ── Redo: re-applying a reversed act ─────────────────────────────


def redo(project_folder: str, *, final: str = "", act: str = "",
         connect=None, read_live_rows=None, build=None, touch=None,
         supersede=(), accept_editor_changes=None,
         direct_transaction_adapter=None) -> list:
    """Re-apply a reversed act: a touch from its journal, a rebuild from its plan.

    `act` addresses the reversed act by id - a touch's journal entry id,
    a rolled-back rebuild's version number, a rollback's version number,
    or a history entry id. The act must be reversed; a live act has
    nothing to redo. Returns one receipt per reel re-applied.
    """
    kind, target, entry, name = resolve_act(project_folder, final, act)
    if kind == "direct":
        return [_direct_transaction_action(
            project_folder, target["transaction_id"], "redo",
            adapter=direct_transaction_adapter)]
    if kind == "patch":
        return [redo_patch_transaction(
            project_folder, target["patch_id"], connect=connect)]
    if kind == "touch":
        return [redo_touch(project_folder, entry["id"],
                           connect=connect, read_live_rows=read_live_rows,
                           touch=touch,
                           accept_editor_changes=accept_editor_changes)]
    return [redo_rebuild(project_folder, name, target,
                         read_live_rows=read_live_rows, build=build,
                         accept_editor_changes=accept_editor_changes)]


def redo_touch(project_folder: str, entry_id: str, *, connect=None,
               read_live_rows=None, touch=None,
               accept_editor_changes=None) -> dict:
    """Re-apply a touch that was undone, from its journal's spec.

    The journal holds the touch's spec; re-applying it runs the touch
    again through the `touch` seam (`reel_touchup.apply_touchup` in
    production, a test double otherwise) and records a `redo` version
    naming the undo it reversed. The touch is live again as a new
    version; the undo stays recorded as the history it was.
    """
    from library.tools.versions import reel_versions

    entry = read_entry(project_folder, entry_id)
    if entry.get("status") != STATUS_UNDONE:
        raise UndoRefused(
            f"touch {entry_id} is {entry.get('status')!r}, not undone",
            "there is no undo of it to redo",
            "pick an undone entry (`ren undo <project> --list` shows "
            "the acts)")
    history = reel_versions.versions_of(project_folder, entry["final"])
    touch_version = next(
        (v for v in history if int(v["version"]) == int(entry["version"])),
        None)
    undo_version = (touch_version or {}).get("undone_by")
    if undo_version is None:
        raise UndoRefused(
            f"touch {entry_id} carries no undo to redo",
            "its version was never reversed",
            "pick an undone entry (`ren undo <project> --list` shows "
            "the acts)")
    spec = dict(entry["spec"])
    apply_touch = touch or _apply_touch
    apply_touch(project_folder, spec)
    rows = (read_live_rows or _live_rows_reader(
        project_folder, connect))(entry["final"])
    version = reel_versions.record(
        project_folder, entry["final"], kind=reel_versions.KIND_REDO,
        rows=rows, redoes=undo_version, journal=entry["id"])
    try:
        from library.tools import reel_edit_history as _history
        _history.try_record_ren_act(
            os.path.join(project_folder, "pipeline_output", "review"),
            entry["final"], act=_history.ACT_REDO,
            summary=(f"Ren redo on {entry['final']} (version "
                     f"{version['version']} re-applies touch journal "
                     f"{entry['id']}, reversing undo version "
                     f"{undo_version})"),
            refs={"version": version["version"], "redoes": undo_version,
                  "journal": entry["id"],
                  "rows_digest_after": version.get("rows_digest")})
    except Exception:  # noqa: BLE001, S110 - history never fails a redo
        pass
    return {"entry": entry["id"], "version": version["version"],
            "redo": True, "reapplied": undo_version}


def redo_rebuild(project_folder: str, final: str, act: Mapping, *,
                 read_live_rows: Callable[[str], Mapping] | None = None,
                 build: Callable[[str, int], int] | None = None,
                 accept_editor_changes=None) -> dict:
    """Re-apply a rebuild that was rolled back, from its plan moment.

    `act` is the rollback version or the rolled-back rebuild. The
    rebuild's plan moment is restored and the reel rebuilt through the
    `build` seam; a `redo` version names the act it re-applied and the
    rolled-back rebuild is live again.
    """
    from library.tools.versions import reel_versions, rounds

    history = reel_versions.versions_of(project_folder, final)
    if act.get("kind") == reel_versions.KIND_ROLLBACK:
        rebuild = next(
            (v for v in history
             if int(v["version"]) == int(act.get("redoes") or 0)), None)
        if rebuild is None:
            raise UndoRefused(
                f"rollback {act['version']} of {final!r} names no rebuild "
                f"to re-apply",
                "so there is nothing to redo",
                "list the acts with `ren undo <project> --list`")
        act = rebuild
    if act.get("kind") != reel_versions.KIND_BUILD:
        raise UndoRefused(
            f"version {act['version']} of {final!r} is "
            f"{act.get('kind')!r}, not a rebuild",
            "so there is no rebuild to redo",
            "list the acts with `ren undo <project> --list`")
    if not act.get("undone_by"):
        raise UndoRefused(
            f"version {act['version']} of {final!r} is not rolled back",
            "there is no rollback of it to redo",
            "pick a rolled-back rebuild (`ren undo <project> --list` "
            "shows the acts)")
    plan_moment = act.get("plan_moment")
    if not plan_moment:
        raise UndoRefused(
            f"version {act['version']} of {final!r} carries no plan moment",
            "so the redo cannot say which plan to rebuild from",
            "list the acts with `ren undo <project> --list`")
    target = reel_versions.state_before(project_folder, final,
                                        int(act["version"]))
    if target is None:
        raise UndoRefused(
            f"version {act['version']} of {final!r} is the first recorded "
            f"version",
            "so there is no earlier state to have rolled back to",
            "there is nothing to redo - leave the reel as it is")
    live = dict(read_live_rows(final) or {})
    from library.tools.reel_replace_guard import accepts_editor_changes
    if (rounds.digest_rows(live) != target["rows_digest"]
            and not accepts_editor_changes(final, accept_editor_changes)):
        raise TimelineMovedSinceTouch(
            f"{final!r} no longer reads as version {target['version']} - "
            f"it was changed outside Ren since the rollback",
            "re-applying the rebuild over it would destroy that work; "
            "nothing was changed",
            "re-apply the outside change after the redo, or leave the "
            "timeline as it is - `ren redo` will not destroy it")
    reel = int(plan_moment["number"])
    restore_plan_moment(project_folder, plan_moment)
    reel_versions.set_pending_redo(project_folder, final, int(act["version"]))
    if build is None:
        code = _build_one_reel(project_folder, reel, supersede=(),
                               accept_editor_changes=accept_editor_changes)
    else:
        code = build(project_folder, reel)
    unplaced = reel_versions.take_pending_redo(project_folder, final)
    if code or unplaced is not None:
        raise UndoNotVerified(
            f"the rebuild for the redo of {final!r} "
            f"{'refused (exit ' + str(code) + ')' if code else 'promoted nothing'}"
            f"; the plan now holds version {act['version']}'s moment "
            f"and the timeline was not replaced.")
    after = dict(read_live_rows(final) or {})
    if rounds.digest_rows(after) != act["rows_digest"]:
        diff = rounds.diff_reel(act["rows"], after)
        raise RollbackDiverged(
            f"{final!r} was rebuilt from version {act['version']}'s plan "
            f"and it does not read as that version: {diff}. The redo is "
            f"on the timeline and recorded, and this is what it could "
            f"not reproduce.")
    redo_version = next(
        (v for v in reversed(reel_versions.versions_of(project_folder, final))
         if v.get("kind") == reel_versions.KIND_REDO), None)
    return {"final": final, "redone": act["version"],
            "version": (int(redo_version["version"])
                        if redo_version else None)}


# ── `ren undo` ───────────────────────────────────────────────────


def undo_stack(project_folder, final: str = "") -> list:
    """The acts `ren undo` would reverse, newest first."""
    from library.tools.versions import reel_versions

    finals = ([final] if final
              else list(reel_versions.read(project_folder)["reels"]))
    acts = [(name, act) for name in finals
            for act in reel_versions.live_acts(project_folder, name)]
    project_name = _resolve_name(project_folder)
    from library.tools.timeline_shadow import ShadowStore
    shadow_store = ShadowStore()
    generations = shadow_store.generations_of(project_name)
    for generation in generations:
        patch = generation.patch or {}
        patch_id = str(patch.get("id") or "")
        timeline = generation.timeline_name
        if (generation.source != "patch" or not patch_id or final and timeline != final or patch_id.startswith(("creative-undo-", "creative-redo-")) or (generation.receipt or {}).get("status") != "committed"):
            continue
        latest_action = _latest_patch_action(
            generations, generation.timeline_id, patch_id)
        if latest_action and latest_action[0] == "undo":
            continue
        acts.append((timeline, {
            "version": int(generation.generation), "kind": "patch",
            "at": _generation_time(generation), "patch_id": patch_id,
            "timeline_id": generation.timeline_id,
        }))
    from library.tools import event_log
    direct_heads = {}
    for event in event_log.read_events(project_folder):
        fields = event.get("fields", {})
        if fields.get("direct_transaction"):
            direct_heads[str(fields["transaction_id"])] = {
                **fields, "_at": event.get("at")}
    for txid, head in direct_heads.items():
        timeline = str(head.get("timeline") or "")
        if (head.get("stage") != "committed"
                or head.get("action") == "undo"
                or (final and timeline != final)):
            continue
        acts.append((timeline or "direct transaction", {
            "version": int(head.get("base_generation") or 0),
            "kind": "direct", "at": str(head.get("_at") or ""),
            "transaction_id": txid, "timeline_id": timeline,
        }))
    return sorted(acts, key=lambda pair: (str(pair[1]["at"]),
                                          int(pair[1]["version"])),
                  reverse=True)


def _generation_time(generation) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(float(generation.recorded_at), UTC).isoformat(
        timespec="seconds")


def _patch_action_prefix(patch_id: str, action: str) -> str:
    import hashlib
    digest = hashlib.sha256(patch_id.encode("utf-8")).hexdigest()[:16]
    return f"creative-{action}-{digest}-"


def _latest_patch_action(generations, timeline_id: str,
                         patch_id: str) -> tuple[str, Any, int] | None:
    actions = []
    for generation in generations:
        if generation.timeline_id != timeline_id:
            continue
        candidate_id = str((generation.patch or {}).get("id") or "")
        for action in ("undo", "redo"):
            prefix = _patch_action_prefix(patch_id, action)
            if candidate_id.startswith(prefix):
                try:
                    sequence = int(candidate_id[len(prefix):])
                except ValueError:
                    continue
                actions.append((action, generation, sequence))
    # Undo and redo counters are independent. Generation orders the actions.
    return max(actions, key=lambda row: int(row[1].generation)) if actions else None


def _patch_generation(project_folder: str, patch_id: str, final: str = ""):
    from library.tools.timeline_shadow import ShadowStore

    project_name = _resolve_name(project_folder)
    shadow_store = ShadowStore()
    hits = [generation for generation in shadow_store.generations_of(project_name)
            if generation.source == "patch"
            and (generation.patch or {}).get("id") == patch_id
            and (not final or generation.timeline_name == final)]
    if len(hits) != 1:
        raise UndoRefused(
            f"EditPatch act {patch_id!r} matched {len(hits)} recorded "
            "generations", "an undo must identify one committed patch on "
            "one timeline", "use `ren undo <project> --list` and address "
            "the exact patch as `--act patch:<id>`")
    generation = hits[0]
    if (generation.receipt or {}).get("status") != "committed":
        raise UndoRefused(
            f"EditPatch act {patch_id!r} is not a committed transaction",
            "a partial or unverified write cannot be presented as a clean "
            "revision", "inspect its transaction receipt and recover the "
            "recorded live state first")
    return shadow_store, generation


def _direct_transaction_action(project_folder: str, transaction_id: str,
                               action: str, adapter=None) -> dict:
    from library.tools import direct_transaction
    from library.tools.resolve_lock import resolve_lease

    try:
        plan = direct_transaction.explain(
            project_folder, transaction_id, action=action)
    except direct_transaction.DirectTransactionRefused as refused:
        raise UndoRefused(
            f"direct transaction {transaction_id!r} cannot be {action}ed",
            str(refused),
            "keep the transaction refused until its native inverse and adapter "
            "are qualified") from refused
    if not plan["verified"]:
        raise UndoRefused(
            f"direct transaction {transaction_id!r} has no qualified native "
            "recovery route", "; ".join(plan["blockers"]),
            "qualify its direct inverse before asking Ren to undo it")
    if not isinstance(adapter, Mapping):
        raise UndoRefused(
            f"direct transaction {transaction_id!r} has no live Resolve adapter",
            "native compensation requires callbacks bound to the transaction's "
            "Resolve operation", "route this transaction through its qualified "
            "Resolve adapter before retrying")
    required = {"observe", "apply", "same_state"}
    if action == "redo":
        required.add("verify")
    missing = required - set(adapter)
    if missing:
        raise UndoRefused(
            f"direct transaction {transaction_id!r} adapter is incomplete",
            "missing callbacks: " + ", ".join(sorted(missing)),
            "supply the complete qualified transaction adapter")
    head = direct_transaction.read_transaction(project_folder, transaction_id)
    timeline = str(head.get("timeline") or "direct transaction")
    with resolve_lease(f"{action} direct transaction {transaction_id}"):
        try:
            if action == "undo":
                receipt = direct_transaction.undo_committed(
                    project_folder=project_folder, transaction_id=transaction_id,
                    observe=adapter["observe"], apply=adapter["apply"],
                    same_state=adapter["same_state"])
            else:
                receipt = direct_transaction.redo_committed(
                    project_folder=project_folder, transaction_id=transaction_id,
                    observe=adapter["observe"], apply=adapter["apply"],
                    verify=adapter["verify"], same_state=adapter["same_state"])
        except direct_transaction.DirectTransactionRefused as refused:
            raise UndoRefused(
                f"direct transaction {transaction_id!r} cannot be {action}ed",
                str(refused),
                "resolve the named live-state or qualification issue before retrying") \
                from refused
        except direct_transaction.DirectTransactionFailed as failed:
            raise UndoNotVerified(
                f"direct transaction {transaction_id!r} {action} failed: {failed}") \
                from failed
    return {"source": "direct_transaction", "transaction_id": transaction_id,
            "timeline": timeline, "verified": receipt["status"] == "verified",
            **receipt}


def _apply_patch_revision(project_folder: str, timeline_name: str, patch,
                          *, timeline_id: str, connect=None) -> dict:
    from library.tools import edit_patch, timeline_shadow
    from library.tools.resolve_lock import cursor_excursion, resolve_lease

    project_name = _resolve_name(project_folder)
    with resolve_lease(f"{patch.id} on {timeline_name}"):
        project = (connect or _connect)(project_name)
        timelines = [project.GetTimelineByIndex(index)
                     for index in range(1, project.GetTimelineCount() + 1)]
        matches = [timeline for timeline in timelines
                   if timeline and timeline.GetName() == timeline_name]
        if len(matches) != 1:
            raise UndoRefused(
                f"Resolve has {len(matches)} timelines named {timeline_name!r}",
                "the patch revision cannot be safely addressed by name",
                "resolve duplicate timeline names before retrying")
        timeline = matches[0]
        if str(timeline.GetUniqueId() or "") != timeline_id:
            raise UndoRefused(
                f"timeline {timeline_name!r} no longer has recorded Resolve "
                f"identity {timeline_id!r}",
                "the inverse belongs to a different native timeline",
                "adopt or restore the recorded timeline generation before retrying")
        try:
            with cursor_excursion(project, timeline, f"apply {patch.id}"):
                receipt = edit_patch.apply_patch(
                    patch, resolve=None, project=project, timeline=timeline,
                    store=timeline_shadow.ShadowStore(),
                    project_folder=project_folder)
        except edit_patch.PatchRefused as refused:
            raise UndoRefused(
                f"EditPatch revision {patch.id!r} was refused",
                f"{refused}",
                "resolve the named precondition or generation conflict, then retry") \
                from refused
    if receipt.get("status") != "committed":
        raise UndoNotVerified(
            f"EditPatch revision {patch.id!r} did not verify; its read-back "
            "generation remains recorded")
    return receipt


def _carried_patch(project_folder: str, generation, *, patch,
                   model_revision=None):
    from library.tools import edit_patch, patch_algebra
    from library.tools.timeline_shadow import ShadowStore

    store = ShadowStore()
    patch_data = patch.to_dict()
    patch_data["expected_model_revision"] = (
        generation.model_revision if model_revision is None
        else model_revision)
    patch = edit_patch.EditPatch.from_dict(patch_data)
    moved, reason = patch_algebra.carry(
        patch, store, generation.project, generation.timeline_id)
    if moved is None:
        raise TimelineMovedSinceTouch(
            f"EditPatch act {generation.patch['id']!r} cannot be reversed "
            "over the current timeline head",
            reason or "a later or unattributed generation conflicts",
            "undo the conflicting revision first, then retry this act")
    return moved


def undo_patch_transaction(project_folder: str, patch_id: str, *,
                           connect=None) -> dict:
    """Undo a committed, locally reversible EditPatch as a new generation."""
    from library.tools import edit_patch

    store, generation = _patch_generation(project_folder, patch_id)
    generations = store.generations_of(generation.project)
    latest = _latest_patch_action(generations, generation.timeline_id, patch_id)
    if latest and latest[0] == "undo":
        raise UndoRefused(
            f"EditPatch act {patch_id!r} is already undone",
            "there is no live edit to reverse", "use `ren redo` with "
            f"`--act patch:{patch_id}` to re-apply it")
    try:
        before_generation = store.get(
            generation.project, generation.timeline_id,
            int(generation.generation) - 1)
    except Exception as unreadable:
        raise UndoRefused(
            f"EditPatch act {patch_id!r} has no recorded base snapshot",
            f"its inverse cannot be derived ({unreadable})",
            "restore the base timeline generation before retrying") \
            from unreadable
    before = store.snapshot(before_generation)
    after = store.snapshot(generation)
    action_count = sum(
        1 for item in generations
        if (item.patch or {}).get("id", "").startswith(
            _patch_action_prefix(patch_id, "undo")))
    action_id = (_patch_action_prefix(patch_id, "undo")
                 + str(action_count + 1))
    try:
        inverse = edit_patch.inverse_patch(
            generation.patch, before=before, after=after,
            base_generation=generation.generation, patch_id=action_id)
    except edit_patch.PatchRefused as refused:
        raise UndoRefused(
            f"EditPatch act {patch_id!r} has no safe inverse",
            f"{refused}",
            "use semantic replay or a verified native recovery archive") \
            from refused
    inverse = _carried_patch(
        project_folder, generation, patch=inverse)
    receipt = _apply_patch_revision(
        project_folder, generation.timeline_name, inverse,
        timeline_id=generation.timeline_id, connect=connect)
    return {"source": "edit_patch", "patch_id": patch_id,
            "undo_patch_id": inverse.id, "generation": receipt["generation"],
            "verified": True}


def redo_patch_transaction(project_folder: str, patch_id: str, *,
                           connect=None) -> dict:
    """Re-apply a reversed EditPatch as another verified generation."""
    from library.tools import edit_patch

    store, generation = _patch_generation(project_folder, patch_id)
    generations = store.generations_of(generation.project)
    latest = _latest_patch_action(generations, generation.timeline_id, patch_id)
    if not latest or latest[0] != "undo":
        raise UndoRefused(
            f"EditPatch act {patch_id!r} has no current undo to redo",
            "redo must follow its latest undo in the same revision chain",
            "choose an undone edit with `ren undo <project> --list`")
    _, undo_generation, undo_sequence = latest
    redo_count = sum(
        1 for item in generations
        if (item.patch or {}).get("id", "").startswith(
            _patch_action_prefix(patch_id, "redo")))
    redo_id = (_patch_action_prefix(patch_id, "redo")
               + str(redo_count + 1))
    replay = edit_patch.EditPatch.from_dict({
        **generation.patch, "id": redo_id,
        "base_generation": undo_generation.generation,
        "expected_model_revision": undo_generation.model_revision,
    })
    replay = _carried_patch(
        project_folder, generation, patch=replay,
        model_revision=undo_generation.model_revision)
    receipt = _apply_patch_revision(
        project_folder, generation.timeline_name, replay,
        timeline_id=generation.timeline_id, connect=connect)
    return {"source": "edit_patch", "patch_id": patch_id,
            "redo_patch_id": redo_id, "undo_sequence": undo_sequence,
            "generation": receipt["generation"], "verified": True}


def explain_undo(project_folder: str, *, final: str = "", act: str = "",
                 accept_editor_changes=None) -> list[dict[str, Any]]:
    """Describe the undo route and intervening history without writing.

    This is deliberately ledger-only: the actual operation still checks the
    live timeline under its normal guards immediately before changing it.
    """
    from library.tools.versions import reel_versions

    if act:
        kind, target, journal, name = resolve_act(project_folder, final, act)
        if kind == "direct":
            from library.tools import direct_transaction
            summary = direct_transaction.explain(
                project_folder, target["transaction_id"], action="undo")
            return [{"reel": name, "version": target["version"],
                     "kind": "direct", "journal": target["transaction_id"],
                     "target_version": None,
                     "mechanism": summary["mechanism"],
                     "intervening": [],
                     "outcome": ("verified compensation will run only when "
                                 "native inverse qualification and the live "
                                 "state check pass; "
                                 + ("; ".join(summary["blockers"])
                                    if summary["blockers"] else
                                    "otherwise refuses")),
                     "editor_changes_authorized": False}]
        if kind == "patch":
            store, generation = _patch_generation(
                project_folder, target["patch_id"], name)
            later = [g for g in store.generations_of(generation.project)
                     if g.timeline_id == generation.timeline_id
                     and g.generation > generation.generation]
            return [{
                "reel": name, "version": int(generation.generation),
                "kind": "patch", "journal": target["patch_id"],
                "target_version": int(generation.generation) - 1,
                "mechanism": ("verified inverse EditPatch from the recorded "
                              "base snapshot; compatible later patches are carried"),
                "intervening": [
                    {"version": int(g.generation), "kind": g.source,
                     "status": ("unattributed" if g.source == "observed"
                                else "recorded after target")}
                    for g in later],
                "outcome": ("conflicting or unattributed later generations "
                            "refuse; the inverse commits as a new verified revision"),
                "editor_changes_authorized": False}]
        if target.get("kind") == reel_versions.KIND_ROLLBACK:
            raise UndoRefused(
                f"version {target['version']} of {name!r} is a rollback "
                "revision, not an act to undo",
                "undoing that state by rebuilding would corrupt the act's "
                "ancestry", "use `ren redo` with this rollback version to "
                "re-apply the build it reversed")
        targets = [(name, target, kind, journal)]
    else:
        stack = undo_stack(project_folder, final)
        if not stack:
            raise UndoRefused(
                f"nothing recorded on {final or 'this project'!r} is left to undo",
                "every recorded act was already undone, or none was ever recorded",
                "there is nothing to undo - leave the reels as they are")
        name, target = stack[0]
        targets = [(name, target, target["kind"], None)]
        if target["kind"] == reel_versions.KIND_TOUCH and target.get("batch"):
            targets = [(other, item, item["kind"], None)
                       for other, item in undo_stack(project_folder)
                       if item.get("batch") == target["batch"]]

    plans = []
    for name, target, kind, journal in targets:
        if kind == "direct":
            from library.tools import direct_transaction
            summary = direct_transaction.explain(
                project_folder, target["transaction_id"], action="undo")
            plans.append({
                "reel": name, "version": target["version"],
                "kind": "direct", "journal": target["transaction_id"],
                "target_version": None, "mechanism": summary["mechanism"],
                "intervening": [],
                "outcome": ("native inverse qualification and an exact live "
                            "state match are required; "
                            + ("; ".join(summary["blockers"])
                               if summary["blockers"] else "otherwise refuses")),
                "editor_changes_authorized": False})
            continue
        if kind == "patch":
            patch_store, patch_generation = _patch_generation(
                project_folder, target["patch_id"], name)
            later_generations = [
                g for g in patch_store.generations_of(
                    patch_generation.project)
                if g.timeline_id == patch_generation.timeline_id
                and g.generation > patch_generation.generation]
            plans.append({
                "reel": name, "version": int(patch_generation.generation),
                "kind": "patch", "journal": target["patch_id"],
                "target_version": int(patch_generation.generation) - 1,
                "mechanism": ("verified inverse EditPatch from the recorded "
                              "base snapshot; compatible later patches are carried"),
                "intervening": [
                    {"version": int(g.generation), "kind": g.source,
                     "status": ("unattributed" if g.source == "observed"
                                else "recorded after target")}
                    for g in later_generations],
                "outcome": ("conflicting or unattributed later generations refuse; "
                            "the inverse commits as a new verified revision"),
                "editor_changes_authorized": False})
            continue
        history = reel_versions.versions_of(project_folder, name)
        later = [entry for entry in history
                 if int(entry["version"]) > int(target["version"])]
        intervening = [
            {"version": int(entry["version"]), "kind": entry["kind"],
             "status": ("already reversed" if entry.get("undone_by")
                        else "recorded after target")}
            for entry in later]
        if kind == "touch":
            mechanism = "verified inverse transaction from the touch journal"
            outcome = ("preserves intervening editor changes only when they are "
                       "outside the inverse projection; otherwise refuses")
            entry_id = (journal or {}).get("id") or target.get("journal")
        else:
            prior = reel_versions.state_before(
                project_folder, name, int(target["version"]))
            mechanism = "semantic replay of the prior plan and later live touches"
            if prior and isinstance(prior.get("recovery_archive"), Mapping):
                mechanism += "; verified native archive is the fallback"
            outcome = ("later touches are replayed and each read-back is verified; "
                       "incompatible live changes refuse unless explicitly accepted")
            entry_id = None
        plans.append({
            "reel": name, "version": int(target["version"]),
            "kind": kind, "journal": entry_id,
            "target_version": (int(reel_versions.state_before(
                project_folder, name, int(target["version"]))["version"])
                if kind != "touch" and reel_versions.state_before(
                    project_folder, name, int(target["version"])) else None),
            "mechanism": mechanism, "intervening": intervening,
            "outcome": outcome,
            "editor_changes_authorized": bool(accept_editor_changes),
        })
    return plans


def render_undo_plan(plans: Sequence[Mapping]) -> str:
    """Render the explanation shown before a mutating undo begins."""
    lines = ["Undo plan:"]
    for plan in plans:
        lines.append(f"  {plan['reel']} v{plan['version']} ({plan['kind']})")
        lines.append(f"    mechanism: {plan['mechanism']}")
        if plan.get("target_version") is not None:
            lines.append(f"    restores state from v{plan['target_version']}")
        later = plan.get("intervening") or []
        if later:
            rendered = ", ".join(
                f"v{item['version']} {item['kind']} ({item['status']})"
                for item in later)
            lines.append(f"    intervening revisions: {rendered}")
        else:
            lines.append("    intervening revisions: none recorded")
        lines.append(f"    effect: {plan['outcome']}")
    return "\n".join(lines)


def explain_redo(project_folder: str, *, final: str = "", act: str = "") \
        -> list[dict[str, Any]]:
    """Describe a redo's source, route, and later recorded history."""
    kind, target, journal, name = resolve_act(project_folder, final, act)
    if kind == "direct":
        from library.tools import direct_transaction
        summary = direct_transaction.explain(
            project_folder, target["transaction_id"], action="redo")
        return [{"reel": name, "version": target["version"],
                 "kind": "direct", "source": target["transaction_id"],
                 "mechanism": summary["mechanism"], "intervening": [],
                 "outcome": ("only a currently undone transaction may be "
                             "re-applied; "
                             + ("; ".join(summary["blockers"])
                                if summary["blockers"] else
                                "native read-back must verify"))}]
    if kind == "patch":
        store, generation = _patch_generation(
            project_folder, target["patch_id"], name)
        generations = store.generations_of(generation.project)
        mechanism = "re-apply the recorded EditPatch operations as a verified generation"
        source = target["patch_id"]
        after = [{"version": int(g.generation), "kind": g.source}
                 for g in generations
                 if g.timeline_id == generation.timeline_id
                 and g.generation > generation.generation]
        return [{"reel": name, "version": int(generation.generation),
                 "kind": "patch", "source": source,
                 "mechanism": mechanism, "intervening": after,
                 "outcome": ("redo requires the latest transaction in this "
                             "chain to be an undo; conflicting or unattributed "
                             "changes refuse")}]
    from library.tools.versions import reel_versions

    history = reel_versions.versions_of(project_folder, name)
    if kind == "touch":
        mechanism = "semantic replay of the touch journal's recorded edit program"
        source = journal["id"]
    else:
        source_act = target
        if target.get("kind") == reel_versions.KIND_ROLLBACK:
            source_act = next((entry for entry in history
                               if int(entry["version"]) ==
                               int(target.get("redoes") or 0)), None)
        if source_act is None:
            raise UndoRefused(
                f"rollback {target['version']} of {name!r} names no rebuild "
                "to re-apply", "the redo source cannot be identified",
                "repair the rollback's version reference before retrying")
        mechanism = (f"rebuild from version {source_act['version']}'s "
                     "recorded plan moment")
        source = int(source_act["version"])
    after = [
        {"version": int(entry["version"]), "kind": entry["kind"],
         "status": "recorded after selected act"}
        for entry in history
        if int(entry["version"]) > int(target["version"])]
    return [{"reel": name, "version": int(target["version"]),
             "kind": kind, "source": source, "mechanism": mechanism,
             "intervening": after,
             "outcome": "read-back is verified; incompatible live changes refuse"}]


def render_redo_plan(plans: Sequence[Mapping]) -> str:
    """Render the explanation shown before a mutating redo begins."""
    lines = ["Redo plan:"]
    for plan in plans:
        lines.append(f"  {plan['reel']} v{plan['version']} ({plan['kind']})")
        lines.append(f"    mechanism: {plan['mechanism']}")
        lines.append(f"    source: {plan['source']}")
        later = plan.get("intervening") or []
        rendered = (", ".join(f"v{item['version']} {item['kind']}"
                               for item in later)
                    if later else "none recorded")
        lines.append(f"    intervening revisions: {rendered}")
        lines.append(f"    effect: {plan['outcome']}")
    return "\n".join(lines)


def resolve_act(project_folder, final: str, act_id: str):
    """Resolve an act id to `(kind, act, entry, final_name)`.

    An act id is a touch's journal entry id, a rebuild's version number,
    or a history entry id (resolved through its refs) - the history is
    the address space. `act` is the version ledger entry; `entry` is the
    undo journal entry for a touch, else None; `final_name` is the reel
    the act was found on.
    """
    if str(act_id).startswith("direct:"):
        from library.tools import direct_transaction
        transaction_id = str(act_id).split(":", 1)[1]
        try:
            head = direct_transaction.read_transaction(project_folder,
                                                       transaction_id)
        except direct_transaction.DirectTransactionRefused as refused:
            raise UndoRefused(
                f"direct transaction {transaction_id!r} is not reversible",
                str(refused),
                "inspect its recovery journal and resolve the recorded state") \
                from refused
        timeline = str(head.get("timeline") or "direct transaction")
        if final and timeline != final:
            raise UndoRefused(
                f"direct transaction {transaction_id!r} belongs to "
                f"{timeline!r}, not {final!r}",
                "an act cannot be undone on another timeline",
                "address the transaction on its recorded timeline")
        return ("direct", {"version": int(head.get("base_generation") or 0),
                            "kind": "direct", "transaction_id": transaction_id,
                            "at": str(head.get("_at") or "")},
                head, timeline)

    if str(act_id).startswith("patch:"):
        patch_id = str(act_id).split(":", 1)[1]
        _store, generation = _patch_generation(project_folder, patch_id, final)
        return ("patch", {"version": generation.generation,
                           "kind": "patch", "patch_id": patch_id,
                           "timeline_id": generation.timeline_id,
                           "at": _generation_time(generation)},
                None, generation.timeline_name)

    from library.tools.versions import reel_versions

    review_dir = os.path.join(str(project_folder), "pipeline_output", "review")
    finals = ([final] if final
              else list(reel_versions.read(project_folder)["reels"]))

    for name in finals:
        try:
            entry = read_entry(project_folder, act_id)
        except UndoRefused:
            continue
        if entry.get("final") != name:
            continue
        for act in reel_versions.versions_of(project_folder, name):
            if act.get("journal") == act_id:
                return ("touch", act, entry, name)
        raise UndoRefused(
            f"journal entry {act_id!r} is recorded on {name!r} but no "
            f"version names it",
            "so it cannot be addressed as an act",
            "list the acts with `ren undo <project> --list`")

    try:
        number = int(act_id)
    except (TypeError, ValueError):
        number = None
    if number is not None:
        for name in finals:
            for act in reel_versions.versions_of(project_folder, name):
                if (int(act["version"]) == number
                        and act.get("kind") in (reel_versions.KIND_BUILD,
                                                reel_versions.KIND_ROLLBACK)):
                    return ("rebuild", act, None, name)

    from library.tools import reel_edit_history
    for name in finals:
        for entry in reel_edit_history.recorded_history(review_dir, name):
            if entry.get("id") != act_id:
                continue
            refs = entry.get("refs") or {}
            if refs.get("journal"):
                return resolve_act(project_folder, name, str(refs["journal"]))
            if refs.get("version") is not None:
                return resolve_act(project_folder, name, str(refs["version"]))
            raise UndoRefused(
                f"history entry {act_id!r} names no version or journal",
                "so it cannot be addressed as an act",
                "list the acts with `ren undo <project> --list`")

    raise UndoRefused(
        f"no act {act_id!r} is recorded on {final or 'this project'}",
        "the id names no journal entry, version, or history entry",
        "list the acts with `ren undo <project> --list`")


def undo(project_folder: str, *, final: str = "", act: str = "",
          connect=None, read_live_rows=None, build=None, touch=None,
          supersede=(), accept_editor_changes=None,
          direct_transaction_adapter=None) -> list:
    """Reverse the newest act (on `final`, or anywhere), or the act `act` names.

    `act` addresses any recorded act by id, not just the newest: a touch
    is reversed in place, a rebuild rolled back. A named act is reversed
    only when the live timeline still reads as that act left it - a later
    act standing on top of it is caught by that check (`TimelineMoved
    SinceTouch`), not by the address. Returns one receipt per reel.
    """
    from library.tools.versions import reel_versions

    if act:
        kind, target, entry, name = resolve_act(project_folder, final, act)
        if kind == "direct":
            return [_direct_transaction_action(
                project_folder, target["transaction_id"], "undo",
                adapter=direct_transaction_adapter)]
        if kind == "patch":
            return [undo_patch_transaction(
                project_folder, target["patch_id"], connect=connect)]
        if kind == "touch":
            return [undo_touch(project_folder, entry["id"],
                               connect=connect,
                               accept_editor_changes=accept_editor_changes)]
        if target.get("kind") == reel_versions.KIND_ROLLBACK:
            raise UndoRefused(
                f"version {target['version']} of {name!r} is a rollback "
                "revision, not an act to undo",
                "undoing that state by rebuilding would corrupt the act's "
                "ancestry", "use `ren redo` with this rollback version to "
                "re-apply the build it reversed")
        if target.get("undone_by"):
            raise UndoRefused(
                f"version {target['version']} of {name!r} is "
                f"already undone (by version {target['undone_by']})",
                "there is nothing to undo",
                "pick a live act (`ren undo <project> --list` shows them)")
        return [rollback_rebuild(
            project_folder, name, target,
            read_live_rows=read_live_rows or _live_rows_reader(
                project_folder, connect),
            build=build, touch=touch, supersede=supersede,
            accept_editor_changes=accept_editor_changes)]
    stack = undo_stack(project_folder, final)
    if not stack:
        raise UndoRefused(
            f"nothing recorded on {final or 'this project'!r} is "
            f"left to undo",
            "every recorded act was already undone, or none was "
            "ever recorded",
            "there is nothing to undo - leave the reels as they are")
    name, act = stack[0]
    targets = [(name, act)]
    if act["kind"] == reel_versions.KIND_TOUCH and act.get("batch"):
        targets = [(other, other_act) for other, other_act
                   in undo_stack(project_folder)
                   if other_act.get("batch") == act["batch"]]
        for other, _act in targets:
            if reel_versions.latest_act(project_folder, other) != _act:
                raise UndoRefused(
                    f"the all-reels touch {act['batch']} has a later "
                    f"act on {other!r} standing on it",
                    "an all-reels touch is undone whole, and a later "
                    "act stands in the way",
                    f"undo the later act on {other!r} first, then "
                    f"re-run `ren undo`")
    receipts = []
    for name, act in targets:
        if act["kind"] == "direct":
            receipts.append(_direct_transaction_action(
                project_folder, act["transaction_id"], "undo",
                adapter=direct_transaction_adapter))
        elif act["kind"] == "patch":
            receipts.append(undo_patch_transaction(
                project_folder, act["patch_id"], connect=connect))
        elif act["kind"] == reel_versions.KIND_TOUCH:
            receipts.append(undo_touch(project_folder, act["journal"],
                                       connect=connect,
                                       accept_editor_changes=
                                       accept_editor_changes))
        else:
            receipts.append(rollback_rebuild(
                project_folder, name, act,
                read_live_rows=read_live_rows or _live_rows_reader(
                    project_folder, connect),
                build=build, touch=touch, supersede=supersede,
                accept_editor_changes=accept_editor_changes))
    return receipts


def _live_rows_reader(project_folder, connect=None):
    def read(final):
        from library.tools.reel_build import timelines_to_replace
        from library.tools.reel_replace_guard import snapshot_timeline
        from library.tools.resolve_lock import resolve_lease

        with resolve_lease(f"read {final} for undo", exclusive=False):
            project = (connect or _connect)(_resolve_name(project_folder))
            found = {t.GetName(): t
                     for t in timelines_to_replace(project, {final})}
            if final not in found:
                raise UndoRefused(
                    f"no timeline called {final!r} is in the Resolve "
                    f"project",
                    "the undo cannot read a timeline that is not there",
                    "restore the timeline in Resolve (or rebuild the "
                    "reel), then re-run `ren undo`")
            return snapshot_timeline(found[final], final, side="retiring")
    return read


def render_stack(stack: Sequence[tuple]) -> str:
    if not stack:
        return "Nothing to undo."
    lines = []
    for final, act in stack:
        what = (f"touch {act.get('journal')}" if act["kind"] == "touch"
                else f"EditPatch {act['patch_id']}" if act["kind"] == "patch"
                else f"direct transaction {act['transaction_id']}"
                if act["kind"] == "direct" else "rebuild")
        batch = f" (all-reels {act['batch']})" if act.get("batch") else ""
        lines.append(f"  {final} v{act['version']} {what}{batch} "
                     f"at {act['at']}")
    return "\n".join(lines)


__all__ = [
    "JOURNAL_FORMAT",
    "InversePlan",
    "RollbackDiverged",
    "TimelineMovedSinceTouch",
    "UndoNotVerified",
    "UndoRefused",
    "close_entry",
    "create_creative_branch",
    "discard_branch_materialization",
    "explain_redo",
    "explain_undo",
    "fail_entry",
    "link_branch_git",
    "list_entries",
    "open_entry",
    "plan_inverse",
    "projection",
    "projection_diff",
    "read_entry",
    "record_branch_materialization",
    "redo",
    "redo_rebuild",
    "redo_touch",
    "render_redo_plan",
    "render_undo_plan",
    "resolve_act",
    "restore_plan_moment",
    "rollback_rebuild",
    "undo",
    "undo_in_place",
    "undo_stack",
    "undo_touch",
]
