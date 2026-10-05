"""A small change to the MASTER timeline in place, instead of a rebuild.

`ren edit` re-runs the whole pipeline - minutes, and it may change things
the instruction did not touch.  `resolve-axi` writes ad hoc, with no
ledger row and no undo journal entry.  This module is the smallest-change
path for the master timeline: the same staged-copy / verify / promote
discipline `reel_touchup` already gives a built reel, aimed at the
timeline the project's `resolve.timeline_name` declares.

What the caller states, and what it does not
--------------------------------------------
The caller states the change STRUCTURALLY: which item, what changes -
one of the ten ops `reel_touchup` documents.  The qualification gate is
the SAME gate (`reel_touchup.qualify`): the ops are one structural
vocabulary, and a change that is unclassifiable for a reel is
unclassifiable for the master.  Mapping a captain's natural-language
note onto such a change is not this module's job; it is the mechanism
that task will call.

What is different from the reel path
------------------------------------
- The master is the pipeline's output, not a built reel: no sign-off,
  no rounds, no retirement, no carried signature.  A master touch-up is
  reversed by the undo journal and carried by `editor_edit_carry` like
  any other timeline.
- A played-length change on the master desyncs every reel cut from it,
  and the master's comps are built by the pipeline, not by a recorded
  fusion manifest a touch-up can re-derive from.  So the length-changing
  classes (`composed_with_rederivation`, `composed_still_resize`) refuse
  with "rebuild" - the pipeline re-derives the master's comps and
  re-cuts the reels.  The `composed` class (no played-length change) and
  the in-place ops are served.
- The master carries the captain's notes, so marker carry and the marker
  gate run exactly as they do for a reel.

The way back is the JOURNAL, not a copy.  `undo_journal.open_entry`
reads the master timeline before anything is staged; `close_entry`
reads the promoted one before the replaced generation is deleted;
`ren undo` reverses the touch in place from the two.

`tests/unit/resolve/test_composed_edit.py`.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from library.tools import composed_edit as _ce
from library.tools import reel_touchup as _reel_touchup
from library.tools.ren_refusal import RenRefusal

_logger = logging.getLogger(__name__)

# The qualification gate, its result and its refusals are the reel path's:
# the ops are one structural vocabulary, and the master classifies with
# the same gate before it applies them to a different target.
TouchupRefused = _reel_touchup.TouchupRefused
TouchupError = _reel_touchup.TouchupError
Qualification = _reel_touchup.Qualification
COMPOSED = _reel_touchup.COMPOSED
COMPOSED_WITH_REDERIVATION = _reel_touchup.COMPOSED_WITH_REDERIVATION
COMPOSED_STILL_RESIZE = _reel_touchup.COMPOSED_STILL_RESIZE

#: The master timeline's row in the undo journal: it is not a reel, and
#: the journal's `reel` field is an int.
MASTER_REEL = 0


class MasterTouchupRefused(RenRefusal):
    """A master touchup that refused. Fail closed, always by name."""


class MasterTouchupError(RuntimeError):
    """A master touchup that failed mid-flight. The staging still stands."""


def master_timeline_name(project_folder: str) -> str:
    """The master timeline's name, from the project's own declaration.

    `resolve.timeline_name` in project.yaml, defaulting to the schema's
    own default - the same name the build places, so a touch-up edits
    the timeline the pipeline produced.
    """
    import yaml as _yaml

    with open(os.path.join(project_folder, "project.yaml"),
              encoding="utf-8") as handle:
        resolve_config = ((_yaml.safe_load(handle) or {}).get("resolve")
                          or {})
    name = str(resolve_config.get("timeline_name") or "").strip()
    if not name:
        raise MasterTouchupRefused(
            "the project's project.yaml names no master timeline "
            "(`resolve.timeline_name`)",
            "a master touch-up edits the project's master timeline, and "
            "without its name there is no target to edit",
            "set `resolve.timeline_name` in the project's project.yaml, "
            "then re-run `ren touch-master`")
    return name


def _check_carryable(qualification: Qualification,
                     tracks: Sequence[Mapping]) -> None:
    """Refuse in-place ops on items the carry ledger cannot address.

    A carried edit is stated in SOURCE terms (row, source identity,
    source in/out frames) - never by record frame, because a rebuild
    moves every record frame.  When two items on one row play the same
    source passage, a transform write on one of them matches both under
    the carry's own predicate (`editor_edit_carry._governs`), and the
    next build cannot know which one to apply it to.  The write lands
    (the touch-up serves it) but the carry silently supersedes it, so
    the next rebuild drops it.  Refusing here names that instead.
    """
    for entry in qualification.in_place:
        row = str(entry.get("row")).upper()
        kind = str(entry.get("kind") or "")
        record_frame = int(entry["record_frame"])
        target = None
        for track in tracks:
            if _reel_touchup._row_of(track["type"],
                                      int(track["index"])) != row:
                continue
            for clip in track.get("clips") or ():
                if int(clip["record_in"]) == record_frame:
                    target = clip
                    break
        if target is None:
            continue
        source = (str(target.get("source_file") or ""),
                  int(target.get("source_in_frame") or 0),
                  int(target.get("source_out_frame") or 0))
        twins = []
        for track in tracks:
            if _reel_touchup._row_of(track["type"],
                                      int(track["index"])) != row:
                continue
            for clip in track.get("clips") or ():
                if (str(clip.get("source_file") or ""),
                        int(clip.get("source_in_frame") or 0),
                        int(clip.get("source_out_frame") or 0)) == source:
                    twins.append(clip)
        if len(twins) > 1:
            positions = ", ".join(
                f"{row}@{int(c['record_in'])}" for c in twins)
            raise TouchupRefused(
                f"the {kind} on {row}@{record_frame} names an item the "
                f"carry ledger cannot address",
                f"{len(twins)} items on {row} play the same source "
                f"passage ({source[0]} {source[1]}..{source[2]}): "
                f"{positions}. A carried edit is stated in source terms, "
                f"so a write on one of them matches all {len(twins)} and "
                f"the next build cannot know which one to apply it to",
                "state the change on an item that plays a unique source "
                "passage on its row, or state it as a composition edit "
                "(move/remove_overlay) that addresses the item by "
                "position, then re-run `ren touch-master`")


def _resolve_name(project_folder: str, resolve_project_name: str) -> str:
    """The Resolve project name, from the declaration or the folder."""
    if resolve_project_name:
        return resolve_project_name
    import yaml as _yaml

    with open(os.path.join(project_folder, "project.yaml"),
              encoding="utf-8") as handle:
        resolve_config = ((_yaml.safe_load(handle) or {}).get("resolve")
                          or {})
    return (str(resolve_config.get("project_name") or "").strip()
            or os.path.basename(project_folder))


def apply_master_touchup(project_folder: str, spec: Mapping,
                         *, connect=None,
                         resolve_project_name: str = "",
                         accept_editor_changes=None) -> dict:
    """Apply a structured change to the master timeline's existing timeline.

    Stages a DUPLICATE beside the master, conforms it, routes the
    qualified plan through `composed_edit.apply_composed_edit`,
    verifies by re-reading the track, guards the replacement and
    promotes by rename.  The replaced generation is deleted once the
    undo journal holds both sides.  The master timeline is never edited
    directly.

    `connect` is a seam for tests: `connect(resolve_name) -> project`.
    """
    master = master_timeline_name(project_folder)
    resolve_name = _resolve_name(project_folder, resolve_project_name)
    if connect is None:
        from library.tools.reel_build import _connect_resolve_project as _connect
        connect = _connect
    return _apply_master_under_lease(
        project_folder, spec, master, resolve_name,
        accept_editor_changes, connect)


def _apply_master_under_lease(project_folder: str, spec: Mapping,
                              master: str, resolve_name: str,
                              accept_editor_changes, connect) -> dict:
    from library.tools.resolve_lock import under_lease

    @under_lease(f"touch up {master}")
    def _guarded():
        return _apply_master_connected(
            project_folder, spec, master, resolve_name,
            accept_editor_changes, connect)

    return _guarded()


def _apply_master_connected(project_folder: str, spec: Mapping, master: str,
                            resolve_name: str, accept_editor_changes,
                            connect) -> dict:
    import datetime as _dt

    from library.tools import reel_read as _read
    from library.tools.reel_build import (
        staging_name,
        timelines_to_replace,
    )

    started = time.time()
    receipt: dict = {
        "master": master,
        "started": _dt.datetime.now(
            _dt.UTC).isoformat(timespec="seconds"),
    }

    project = connect(resolve_name)
    pool = project.GetMediaPool()
    found = {t.GetName(): t for t in
             timelines_to_replace(project, {master})}
    if master not in found:
        raise TouchupRefused(
            f"no timeline called {master!r} is in Resolve "
            f"project {resolve_name!r}",
            "a master touch-up edits the project's master timeline, and "
            "there is none to edit",
            "build it first (`ren edit <project>`), then touch it up")
    source = found[master]
    staging = staging_name(master)
    if timelines_to_replace(project, {staging}):
        raise TouchupRefused(
            f"a staging container {staging!r} from an "
            f"interrupted run is still in the project",
            "reusing it would grade one run's content as another's",
            "clear it in Resolve before re-running `ren touch-master`")

    tracks = _read.read_tracks(source)
    qualification = _reel_touchup.qualify(tracks, spec)
    # The master's comps are the pipeline's, not a recorded manifest's:
    # a played-length change has no re-derivation route here, so it
    # rebuilds instead.  The `composed` class and the in-place ops are
    # served; the length-changing classes refuse with the safe route.
    if qualification.gate_class != COMPOSED:
        raise TouchupRefused(
            f"this change is class {qualification.gate_class}, which the "
            f"master touch-up does not serve",
            "a played-length change on the master desyncs every reel cut "
            "from it, and the master's comps are built by the pipeline, "
            "not by a recorded fusion manifest a touch-up can re-derive "
            "from",
            "rebuild (`ren edit <project>`) - the pipeline re-derives the "
            "master's comps and re-cuts the reels")
    _check_carryable(qualification, tracks)
    receipt["gate"] = {
        "class": qualification.gate_class,
        "cost": qualification.cost_statement,
        "notes": list(qualification.notes),
    }
    print(f"touch-master {master}: {qualification.gate_class}",
          flush=True)
    print(f"  {qualification.cost_statement}", flush=True)
    for note in qualification.notes:
        print(f"  - {note}", flush=True)

    # THE UNDO JOURNAL, before anything changes (`undo_journal`): the
    # master timeline read whole, with it CURRENT so no transform reads
    # cursor-scaled, and every item the plan deletes outright captured.
    from library.tools import undo_journal as _journal
    from library.tools.resolve_lock import cursor_fence
    try:
        with cursor_fence(project, source, f"journal {master}"):
            journal = _journal.open_entry(
                project_folder, final=master, reel=MASTER_REEL,
                resolve_project=resolve_name, spec=spec,
                gate_class=qualification.gate_class,
                source_timeline=source,
                removals=qualification.removals,
                batch=str(spec.get("batch") or ""),
                project=project)
    except _journal.UndoRefused as unrecordable:
        raise TouchupRefused(
            unrecordable.what, unrecordable.why,
            unrecordable.fix) from unrecordable
    receipt["journal"] = journal["id"]

    from library.tools import plan_provenance as _provenance
    from library.tools import reel_replace_guard as _guard
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    inventory_before = _guard.timeline_inventory(project)
    inventory_operation = _provenance.begin_timeline_inventory(
        review_dir, "master_touchup", inventory_before,
        ren_created_names={master})
    receipt["inventory_operation"] = inventory_operation
    receipt["timeline_inventory_before"] = inventory_before

    stage_started = time.time()
    staged = source.DuplicateTimeline(staging)
    if staged is None or staged.GetName() != staging:
        _journal.fail_entry(project_folder, journal,
                             "the staging copy did not land")
        _provenance.finish_timeline_inventory(
            review_dir, inventory_operation, _guard.timeline_inventory(project))
        raise MasterTouchupError(
            f"the staging copy did not land as {staging!r} - "
            f"nothing was edited and the master timeline stands.")
    try:
        with cursor_fence(project, staged, f"touch up {master}"):
            _edit_staged_master(
                project_folder, project, pool, source, staged, staging,
                qualification, receipt, accept_editor_changes, master,
                stage_started, journal)
    except Exception as failed:
        # The master timeline still stands under its own name; the
        # staging holds the half-done edit for diagnosis.  Delete
        # nothing: a failed touch-up must never widen into a loss.
        receipt["staging_left_standing"] = staging
        if journal.get("status") == _journal.STATUS_OPEN:
            _journal.fail_entry(project_folder, journal, repr(failed))
        _provenance.finish_timeline_inventory(
            review_dir, inventory_operation, _guard.timeline_inventory(project))
        raise
    receipt["seconds"] = round(time.time() - started, 3)
    receipt["receipt_path"] = _write_receipt(project_folder, master, receipt)
    return receipt


def _edit_staged_master(project_folder: str, project: Any, pool: Any,
                        source: Any, staged: Any, staging: str,
                        qualification: Qualification, receipt: dict,
                        accept_editor_changes, master: str,
                        stage_started: float, journal: dict) -> None:
    """Conform, compose and verify the staging copy.

    The same steps `reel_touchup._edit_staged` runs, reusing its
    helpers - the composition is timeline-agnostic.  Stops before
    promotion: the master's promotion is `_promote_master`, not the
    reel's sign-off-and-retire path.
    """
    from library.tools import reel_read as _read

    source_rows = _reel_touchup._live_rows(source)
    staged_rows = _reel_touchup._live_rows(staged)
    from library.tools.project_layout import Area, ProjectLayout
    comp_dir = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "master_touchup", _reel_touchup._safe_slug(master))
    os.makedirs(comp_dir, exist_ok=True)
    conform_receipt = _ce.conform_comp_windows(
        source_rows, staged_rows, comp_dir=comp_dir,
        preserve_passthrough=True)
    receipt["conform"] = {
        "compared": conform_receipt.get("compared"),
        "repaired": len(conform_receipt.get("repaired") or ()),
        "emptied": conform_receipt.get("emptied"),
    }
    receipt["stage_seconds"] = round(time.time() - stage_started, 3)

    receipt["new_rows"] = _reel_touchup._add_rows(
        staged, qualification.new_rows)
    receipt["in_place"] = _reel_touchup._apply_in_place(
        project, staged, qualification, comp_dir, journal["id"],
        project_folder)
    if receipt["in_place"].get("patch_receipt"):
        receipt.setdefault("edit_patches", []).append(
            receipt["in_place"]["patch_receipt"])

    insertions = _reel_touchup._resolve_insertions(
        pool, staged, qualification.insertions)
    changes = list(qualification.changes)

    receipt["pre_delete"] = _reel_touchup._pre_delete_removed(
        project, staged, qualification.removals, journal["id"],
        project_folder=project_folder)
    if receipt["pre_delete"].get("patch_receipt"):
        receipt.setdefault("edit_patches", []).append(
            receipt["pre_delete"]["patch_receipt"])
    changes = _reel_touchup._rekey_changes(
        _read.read_tracks(staged), qualification)
    grade_sources = _reel_touchup._grade_sources_for(
        source, changes, qualification.moves)
    receipt["grades_carried"] = len(grade_sources)

    edit_started = time.time()
    composed = _ce.apply_composed_edit(
        timeline=staged, media_pool=pool, changes=changes,
        insertions=insertions,
        comp_dir=os.path.join(comp_dir, "comps"),
        withheld_dir=os.path.join(comp_dir, "withheld"),
        rederiver=_reel_touchup._NullRederiver(
            "the master touch-up serves the composed class only - "
            "no played length changes, so there is nothing to re-derive"),
        grade_sources=grade_sources,
        link_rows={},
        picture_row="V1",
        write_context={"project_folder": project_folder})
    receipt["composed_seconds"] = round(time.time() - edit_started, 3)
    receipt["composed"] = {
        "plan": composed.plan,
        "captured": composed.captured,
        "deleted": composed.deleted,
        "placed": composed.placed,
        "verified": composed.verified,
        "rederivation_required": composed.rederivation_required,
        "rederived": composed.rederived,
        "seconds": composed.seconds,
    }

    # Verify by RE-READING the track - never by a return value.
    verify_started = time.time()
    staged_tracks = _read.read_tracks(staged)
    receipt["verification_read"] = _reel_touchup._summarise_rows(
        staged_tracks)
    receipt["verify_seconds"] = round(time.time() - verify_started, 3)

    _promote_master(project_folder, project, pool, master, staging,
                    accept_editor_changes, receipt, journal)


def _promote_master(project_folder: str, project: Any, pool: Any,
                    master: str, staging: str, accept_editor_changes,
                    receipt: dict, journal: dict) -> None:
    """Guard, swap names, carry markers, close the journal, delete the
    replaced generation, file the carry.

    The master's promotion: the replace guard, the editor-change
    protection, the marker carry and the marker gate are the reel path's
    own - the master carries the captain's notes too.  What is absent is
    the reel-only machinery: no sign-off, no rounds, no retirement, no
    carried signature.
    """
    from library.tools import editor_edit_carry as _editor_carry
    from library.tools import marker_carry as _markers
    from library.tools import marker_gate as _gate
    from library.tools import plan_provenance as _provenance
    from library.tools import reel_replace_guard as _guard
    from library.tools.reel_build import (
        backup_name,
        timelines_to_replace,
    )

    staged_found = {t.GetName(): t for t in
                    timelines_to_replace(project, {staging})}
    originals = {t.GetName(): t for t in
                 timelines_to_replace(project, {master})}
    if staging not in staged_found or master not in originals:
        raise MasterTouchupError(
            f"the staging {staging!r} or the master {master!r} "
            f"vanished mid-touch-up - nothing was renamed.")

    live_full = _guard.full_timeline_snapshot(
        originals[master], project, project_folder)
    staged_full = _guard.full_timeline_snapshot(
        staged_found[staging], project, project_folder)
    try:
        touch_edits = _editor_carry.derive_touch_edits(
            master, live_full, staged_full, receipt.get("in_place") or {},
            journal_id=journal["id"],
            plan_version=_editor_carry._plan_version(project_folder))
    except _editor_carry.EditorEditCarryRefused as unstatable:
        raise MasterTouchupError(str(unstatable)) from unstatable
    receipt["touch_carried"] = [
        {"id": edit["id"], "kind": edit["kind"], "field": edit["field"],
         "row": edit["row"], "name": edit["name"],
         "source": edit["source"], "wording": edit["wording"]}
        for edit in touch_edits]
    try:
        _guard.assert_target_inventory_unchanged(
            receipt["timeline_inventory_before"],
            _guard.timeline_inventory(project), [master])
        _provenance.assert_not_editor_timeline(
            os.path.join(project_folder, "pipeline_output", "review"),
            originals[master])
        detection = _guard.detect_editor_changes(
            project_folder, master, live_full, live_full)
        edits, superseded = _editor_carry.edits_in_force(
            project_folder, master, detection["pending"])
        receipt["editor_changes"] = _guard.protect_editor_changes(
            project_folder, master, live_full, live_full, staged_full,
            accept=_guard.accepts_editor_changes(
                master, accept_editor_changes), detection=detection,
            carried_edits={"edits": edits, "superseded": superseded,
                           "written": [], "already_held": []})
    except _guard.EditorChangeRefused as refused:
        raise MasterTouchupError(str(refused)) from refused

    incoming_rows = _guard.snapshot_timeline(staged_found[staging],
                                             staging, side="staged")
    retired_rows = _guard.snapshot_timeline(originals[master], master,
                                            side="retiring")
    receipt["replace_report"] = _guard.check_replacement(
        master, staging, retired_rows, incoming_rows)

    carried_markers = None
    notes = _markers.read_markers(originals[master], master)
    if notes:
        keep, lost = _markers.plan_carry(notes, staged_found[staging],
                                         master)
        _markers.report(master, keep, lost)
        carried_markers = {"carried": keep, "uncarried": lost}
    clip_notes = _markers.read_clip_markers(originals[master], master)
    if clip_notes:
        clip_keep, clip_lost = _markers.plan_clip_carry(
            clip_notes, staged_found[staging], master)
        _markers.report_clip(master, clip_keep, clip_lost)
        entry = carried_markers or {"carried": [], "uncarried": []}
        entry["clip_carried"] = clip_keep
        entry["clip_uncarried"] = clip_lost
        carried_markers = entry
    try:
        gate_capture = _gate.assemble_capture(master, notes, clip_notes)
        gate_path = _gate.write_capture(project_folder, master,
                                        gate_capture)
    except OSError as capture_failed:
        raise MasterTouchupError(
            f"the marker capture for {master!r} could not be filed "
            f"({capture_failed}) - nothing was renamed.") \
            from capture_failed
    fleet_before = _gate.fleet_snapshot(project)

    backup = backup_name(master)
    if not originals[master].SetName(backup):
        raise MasterTouchupError(
            f"Resolve would not rename {master!r} aside to "
            f"{backup!r}. Nothing was deleted and the staging "
            f"{staging!r} is untouched.")
    if not staged_found[staging].SetName(master):
        raise MasterTouchupError(
            f"Resolve would not rename staging {staging!r} to "
            f"{master!r}. The master content is safe under "
            f"{backup!r} - rename it back in Resolve and re-run.")
    receipt["promoted"] = {"staging": staging, "master": master,
                           "backup": backup}
    if carried_markers and carried_markers["carried"]:
        declined = _markers.place(staged_found[staging],
                                  carried_markers["carried"])
        carried_markers["declined"] = declined
    if carried_markers and carried_markers.get("clip_carried"):
        clip_declined = _markers.place_clip_markers(
            staged_found[staging], carried_markers["clip_carried"])
        carried_markers["clip_declined"] = clip_declined
    receipt["markers"] = carried_markers or {"carried": [],
                                             "uncarried": []}

    try:
        _gate.verify_promotion(master, gate_capture,
                               staged_found[staging],
                               carried_markers)
    except _markers.MarkerCarryUnreadable as unreadable:
        raise MasterTouchupError(
            f"{master!r} is promoted, but its live markers could not "
            f"be re-read ({unreadable}) - recover from {backup!r} "
            f"and the capture at {gate_path}.") from unreadable
    except _gate.MarkerGateLost as lost:
        raise MasterTouchupError(
            f"{lost} Recover from {backup!r} and the capture at "
            f"{gate_path}.") from lost
    fleet = _gate.check_fleet(
        fleet_before, _gate.fleet_snapshot(project),
        {master, staging, backup})
    if fleet["decreased"]:
        shrunk = "; ".join(
            f"{entry['reel']} {entry['plane']} "
            f"{entry['before']}->{entry['after']}"
            for entry in fleet["decreased"])
        raise MasterTouchupError(
            f"{master!r} is promoted, but {shrunk} - marker count(s) "
            f"SHRANK on timeline(s) this touch-up did not touch.")

    if receipt.get("edit_patches"):
        from library.tools import timeline_shadow
        try:
            head = timeline_shadow.observe(project, staged_found[staging])
        except Exception as unreadable:
            raise MasterTouchupError(
                f"{master!r} is promoted, but the final EditPatch "
                f"generation could not be recorded ({unreadable}); "
                f"recover from {backup!r} and re-run "
                f"`ren timeline observe`.") from unreadable
        receipt["edit_patch_head"] = head.summary()

    from library.tools import undo_journal as _journal
    _journal.close_entry(project_folder, journal,
                         after_timeline=staged_found[staging],
                         rows=incoming_rows, project=project)
    receipt["version"] = journal["version"]
    if journal.get("preservation_after") is not None:
        prior = receipt["editor_changes"].get("carried_edits") or {}
        combined = {**prior,
                    "edits": list(prior.get("edits") or ()) + touch_edits,
                    "superseded": dict(prior.get("superseded") or {})}
        receipt["carried_edits"] = _editor_carry.record_after_promotion(
            project_folder, master, combined,
            journal["preservation_after"], act=f"touch {journal['id']}")
    ren_owned_ids = set()
    for timeline in (originals[master], staged_found[staging]):
        try:
            unique_id = timeline.GetUniqueId()
        except Exception:  # noqa: BLE001
            unique_id = None
        if unique_id:
            ren_owned_ids.add(str(unique_id))
    _provenance.record_unattributed_timeline_changes(
        os.path.join(project_folder, "pipeline_output", "review"),
        receipt["timeline_inventory_before"],
        _guard.timeline_inventory(project), operation="master_touchup",
        ren_owned_ids=ren_owned_ids)
    for record_id in (receipt.get("editor_changes") or {}).get(
            "carried", ()):
        _provenance.resolve_editor_change(
            os.path.join(project_folder, "pipeline_output", "review"),
            master, record_id, status="carried")
    for record_id in (receipt.get("editor_changes") or {}).get(
            "superseded", ()):
        _provenance.resolve_editor_change(
            os.path.join(project_folder, "pipeline_output", "review"),
            master, record_id, status="superseded",
            superseded_by=f"--accept-editor-changes {master}")

    # The replaced generation is DELETED: the journal restores the
    # pre-touch state in place (`ren undo`), so a live backup copy is
    # clutter, not safety.
    from library.tools.reel_build import timelines_to_replace as _replace
    backup_objects = {t.GetName(): t for t in _replace(project, {backup})}
    if backup in backup_objects:
        project.GetMediaPool().DeleteTimelines([backup_objects[backup]])


def _write_receipt(project_folder: str, master: str,
                   receipt: dict) -> str:
    """The touch-up's own account of itself, readable afterwards."""
    import datetime as _dt
    import json as _json

    review_dir = os.path.join(project_folder, "pipeline_output",
                              "review")
    os.makedirs(review_dir, exist_ok=True)
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(review_dir,
                        f"master_touchup_{_reel_touchup._safe_slug(master)}"
                        f"_{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        _json.dump(receipt, handle, indent=2, default=str)
    return path


__all__ = [
    "COMPOSED",
    "COMPOSED_STILL_RESIZE",
    "COMPOSED_WITH_REDERIVATION",
    "MASTER_REEL",
    "MasterTouchupError",
    "MasterTouchupRefused",
    "Qualification",
    "TouchupError",
    "TouchupRefused",
    "apply_master_touchup",
    "master_timeline_name",
]
