"""The editor's timeline edits, carried through every rebuild.

The captain, 2026-10-01, on Ren holding onto his manual timeline edits:
*"first snapshot + detect my changes + refuse to overwrite them, then
carrying edits through rebuilds"*. Step one (`reel_replace_guard`
`detect_editor_changes` / `protect_editor_changes`) records every
unattributed delta between Ren's last read of a reel and the live
timeline, and REFUSES a replacement that would lose one. This module is
step two: the deltas it can state in SOURCE terms become CARRIED EDITS,
and every promotion - a rebuild, a swap, a variant choice - applies them
to the incoming staging timeline before anything is renamed.

What is carried
---------------
An edit names the item it governs by what it PLAYS - row
(`"<media>:<track name>"`), source identity and source in/out frames -
never by a record frame, because a rebuild moves every record frame
and keeps every source frame.

- `cut`: a source passage the editor removed (Reel 7: Craig
  25,263-25,374 and Akshita 60,745-60,979). Staging must play no frame
  of it on that row. `ripple` records whether the editor closed the gap
  (items after the cut moved earlier by its length), and the staging
  delete does the same - which in Resolve removes that TIME from every
  row (`_ripple_collateral`), so a rippled cut that another row's item
  overlaps refuses rather than trimming that item.
- `enabled`: an item switched off or on.
- `transform`: one Edit-page transform property (Pan, Tilt, Zoom...)
  the editor set on an item.

A trim (an item's source range shortened) and a record move of an item
that kept its source are recognised and NOT YET carried: they stay
step one's refusal, named as what they are. So does anything else
(an added item, a grade, a marker the marker carry cannot place).

Every carried edit records the human wording, where it came from (the
editor-change record id), its before/after values and the plan version
it was detected against (`plan_content_hash`). The ledger is
`plan_provenance.CARRIED_EDITS_KEY`; an edit stays in force across every
later build until a Ren act changes the same thing on purpose
(`record_after_promotion` retires it as superseded, naming the act) or
the editor reverses it (the next detection supersedes it).

Apply, then verify by re-reading
--------------------------------
`carry_editor_edits` plans every edit against a full read of staging
before it writes anything; an edit that cannot be mapped (two staged
items play the same passage, or a staged item plays PART of a cut
passage) refuses by name with the source ranges, nothing written.
`verify_carried_edits` judges the result on a fresh read of staging, in
source ranges, never in row counts: a cut passage still playing, an
item not holding its enabled state or transform, refuses the
promotion. A write's return value is never the verdict.

`tests/test_editor_edit_carry.py`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from hashlib import sha256

from library.tools import reel_replace_guard as _guard

RECORD_FIELDS = ("record_in", "record_out")
#: Transform keys Resolve reports but nobody sets.
_IGNORED_TRANSFORM_KEYS = frozenset({"Resolution", "ResolutionWidth",
                                     "ResolutionHeight"})
TOLERANCE = 1e-6


class EditorEditCarryRefused(_guard.EditorChangeRefused):
    """A carried edit could not be mapped onto, or did not land on, staging."""


def _row(item: dict) -> str:
    return _guard.row_key(str(item.get("track_type")),
                          str(item.get("track_name")))


def _source(item: dict) -> tuple:
    return (item.get("source_in_frame"), item.get("source_out_frame"))


def _frames(value) -> str:
    return f"{int(value):,}" if isinstance(value, int) else str(value)


def _span(start, end) -> str:
    return f"{_frames(start)}..{_frames(end)}"


def _edit_id(final: str, kind: str, item: dict, field: str = "") -> str:
    key = "\0".join(str(part) for part in (
        final, kind, _row(item), item.get("source_identity"),
        item.get("source_in_frame"), item.get("source_out_frame"), field))
    return sha256(key.encode("utf-8")).hexdigest()[:24]


def _governs(edit: dict, item: dict) -> bool:
    """`item` plays exactly the passage `edit` names."""
    return (_row(item) == edit["row"]
            and item.get("source_identity") == edit["source_identity"]
            and _source(item) == (edit["source_in_frame"],
                                  edit["source_out_frame"]))


def _overlaps(edit: dict, item: dict) -> bool:
    """`item` plays at least one frame of the passage `edit` names."""
    if (_row(item) != edit["row"]
            or item.get("source_identity") != edit["source_identity"]):
        return False
    start, end = _source(item)
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    return start < edit["source_out_frame"] and \
        edit["source_in_frame"] < end


def _same(got, wanted) -> bool:
    numbers = (int, float)
    if (isinstance(got, numbers) and isinstance(wanted, numbers)
            and not isinstance(got, bool) and not isinstance(wanted, bool)):
        return abs(float(got) - float(wanted)) <= TOLERANCE
    return got == wanted


def _base(final: str, kind: str, item: dict, *, field: str, record: dict,
          plan_version, wording: str, before, after) -> dict:
    return {
        "id": _edit_id(final, kind, item, field),
        "timeline": final,
        "kind": kind,
        "field": field,
        "row": _row(item),
        "track_type": item.get("track_type"),
        "track_name": item.get("track_name"),
        "name": item.get("name"),
        "source_identity": item.get("source_identity"),
        "source_in_frame": item.get("source_in_frame"),
        "source_out_frame": item.get("source_out_frame"),
        "record_in_when_detected": item.get("record_in"),
        "record_out_when_detected": item.get("record_out"),
        "before": before,
        "after": after,
        "wording": wording,
        "source": f"unattributed_editor_change:{record.get('id')}",
        "author": "editor (unattributed: not a Ren action)",
        "detected_at": record.get("recorded_at"),
        "plan_version": plan_version,
        "status": "active",
    }


def _rippled(removed: dict, changes: list[dict]) -> bool:
    """Did the editor close the gap the removed item left?

    Read off the same record: an item that kept its source and sat at
    or after the cut moved EARLIER. Nothing moved earlier is a lift.
    """
    end = removed.get("record_out")
    if not isinstance(end, int):
        return False
    for change in changes:
        if change["kind"] != "item_changed":
            continue
        moved = change["changed"].get("record_in")
        if not moved:
            continue
        before, after = moved["before"], moved["after"]
        if (isinstance(before, int) and isinstance(after, int)
                and before >= end and after < before):
            return True
    return False


def derive_edits(record: dict, *, plan_version=None) -> tuple[list, list]:
    """One editor-change record as carried edits, plus what is not carried.

    Pure. Returns `(edits, uncarried)`, where each `uncarried` entry is
    `{"change", "why"}` - a trim, a move, an addition or a field this
    module does not carry, said as what it is.
    """
    final = str(record.get("timeline"))
    changes = list(record.get("changes") or ())
    removed = [change for change in changes
               if change["kind"] == "item_removed"]
    added = [change for change in changes if change["kind"] == "item_added"]
    edits, uncarried = [], []

    def trimmed_into(change):
        old = change["before"]
        return [other for other in added
                if _row(other["after"]) == _row(old)
                and other["after"].get("source_identity")
                == old.get("source_identity")
                and _overlaps({"row": _row(old),
                               "source_identity": old.get("source_identity"),
                               "source_in_frame": old.get("source_in_frame"),
                               "source_out_frame": old.get(
                                   "source_out_frame")}, other["after"])]

    trimmed_added = set()
    for change in removed:
        old = change["before"]
        partners = trimmed_into(change)
        if partners:
            for other in partners:
                trimmed_added.add(id(other))
            uncarried.append({
                "change": change,
                "why": (f"a trim of {_row(old)} {old.get('name')!r} "
                        f"(source {_span(*_source(old))} now plays as "
                        + ", ".join(_span(*_source(other["after"]))
                                    for other in partners)
                        + ") - trims are not carried yet")})
            continue
        if not isinstance(old.get("source_in_frame"), int) or \
                not isinstance(old.get("source_out_frame"), int):
            uncarried.append({
                "change": change,
                "why": (f"{_row(old)} {old.get('name')!r} carries no "
                        f"readable source range, so its removal cannot "
                        f"be stated as a passage")})
            continue
        ripple = _rippled(old, changes)
        edits.append(_base(
            final, "cut", old, field="", record=record,
            plan_version=plan_version,
            wording=(f"Cut {old.get('name')!r} source "
                     f"{_span(*_source(old))} from {_row(old)}"
                     f"{' and close the gap' if ripple else ''}"),
            before={"record_in": old.get("record_in"),
                    "record_out": old.get("record_out"), "present": True},
            after={"present": False}) | {"ripple": ripple})
    for change in added:
        if id(change) in trimmed_added:
            continue
        item = change["after"]
        uncarried.append({
            "change": change,
            "why": (f"{_row(item)} {item.get('name')!r} source "
                    f"{_span(*_source(item))} was ADDED by the editor - "
                    f"additions are not carried")})
    for change in changes:
        if change["kind"] != "item_changed":
            # Timeline settings (a rippled cut's shorter end frame) and
            # markers (`marker_carry`) are judged by the guard's re-read.
            continue
        old, new = change["before"], change["after"]
        for field, values in change["changed"].items():
            if field == "enabled":
                if not isinstance(values["after"], bool):
                    uncarried.append({"change": change,
                                      "why": "enabled state unreadable"})
                    continue
                state = "on" if values["after"] else "off"
                edits.append(_base(
                    final, "enabled", new, field="enabled", record=record,
                    plan_version=plan_version,
                    wording=(f"Switch {new.get('name')!r} source "
                             f"{_span(*_source(new))} on {_row(new)} "
                             f"{state}"),
                    before=values["before"], after=values["after"]))
            elif field == "transform":
                old_t = values["before"] or {}
                new_t = values["after"] or {}
                for key in sorted(set(old_t) | set(new_t)):
                    if key in _IGNORED_TRANSFORM_KEYS or \
                            _same(old_t.get(key), new_t.get(key)):
                        continue
                    if key not in new_t:
                        uncarried.append({
                            "change": change,
                            "why": f"transform {key} became unreadable"})
                        continue
                    edits.append(_base(
                        final, "transform", new, field=f"transform.{key}",
                        record=record, plan_version=plan_version,
                        wording=(f"Hold {key} {new_t[key]!r} on "
                                 f"{new.get('name')!r} source "
                                 f"{_span(*_source(new))} on {_row(new)}"),
                        before=old_t.get(key), after=new_t[key]))
            elif field in RECORD_FIELDS or field in ("duration", "composite"):
                # A record move is checked by the guard's own re-read;
                # `composite` mirrors transform keys already carried.
                continue
            else:
                uncarried.append({
                    "change": change,
                    "why": (f"{field} on {_row(old)} {old.get('name')!r} "
                            f"is not carried")})
    return edits, uncarried


def _plan_version(project_folder: str):
    from library.tools import plan_provenance

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    return (plan_provenance.read_provenance(review_dir) or {}).get(
        "plan_content_hash")


def edits_in_force(project_folder: str, final: str,
                   pending: list[dict]) -> tuple[list, dict]:
    """The ledger's active edits, updated by the newly pending records.

    Returns `(edits, superseded)`: a newer edit on the same item and
    property replaces the older one, and an item the editor ADDED back
    over a cut passage supersedes that cut - the editor reversed it.
    """
    from library.tools import plan_provenance

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    by_id = {edit["id"]: edit for edit in
             plan_provenance.carried_editor_edits(review_dir, final)}
    superseded = {}
    version = _plan_version(project_folder)
    for record in pending:
        derived, _uncarried = derive_edits(record, plan_version=version)
        for edit in derived:
            by_id[edit["id"]] = edit
        for change in record.get("changes") or ():
            if change["kind"] != "item_added":
                continue
            for edit in list(by_id.values()):
                if edit["kind"] == "cut" and _overlaps(edit, change["after"]):
                    superseded[edit["id"]] = (
                        f"the editor put {edit['name']!r} source "
                        f"{_span(edit['source_in_frame'], edit['source_out_frame'])}"
                        f" back (record {record.get('id')})")
                    by_id.pop(edit["id"])
    return list(by_id.values()), superseded


def plan_application(edits: list[dict], staged: dict, final: str) -> dict:
    """What each edit does to `staged` (a full snapshot). Pure; may refuse."""
    items = list(staged.get("items") or ())
    plan = {"delete": [], "set_enabled": [], "set_transform": [],
            "already_held": []}
    problems = []
    for edit in edits:
        exact = [item for item in items if _governs(edit, item)]
        if edit["kind"] == "cut":
            partial = [item for item in items
                       if _overlaps(edit, item) and item not in exact]
            if partial:
                problems.append(
                    f"  cut {edit['row']} {edit['name']!r} source "
                    f"{_span(edit['source_in_frame'], edit['source_out_frame'])}: "
                    f"staging plays part of it as "
                    + ", ".join(f"source {_span(*_source(item))} at record "
                                f"{_span(item.get('record_in'), item.get('record_out'))}"
                                for item in partial)
                    + " - cutting a part is a trim, not carried yet")
                continue
            if not exact:
                plan["already_held"].append(edit["id"])
                continue
            plan["delete"].extend({"edit": edit, "item": item}
                                  for item in exact)
            continue
        if len(exact) != 1:
            problems.append(
                f"  {edit['kind']} {edit['row']} {edit['name']!r} source "
                f"{_span(edit['source_in_frame'], edit['source_out_frame'])}: "
                f"staging plays that passage {len(exact)} time(s), so the "
                f"edit has no single item to land on")
            continue
        item = exact[0]
        if edit["kind"] == "enabled":
            if item.get("enabled") is edit["after"]:
                plan["already_held"].append(edit["id"])
            else:
                plan["set_enabled"].append({"edit": edit, "item": item})
        else:
            key = edit["field"].split(".", 1)[1]
            if _same((item.get("transform") or {}).get(key), edit["after"]):
                plan["already_held"].append(edit["id"])
            else:
                plan["set_transform"].append({"edit": edit, "item": item,
                                              "key": key})
    problems.extend(_ripple_collateral(plan["delete"], items))
    if problems:
        raise EditorEditCarryRefused(
            f"REFUSING to replace {final!r}: the editor's carried edits "
            f"cannot be mapped onto the staged timeline. Nothing was "
            f"renamed; the live timeline is still in the project.\n"
            + "\n".join(problems)
            + f"\nTo accept this loss deliberately, pass "
              f"--accept-editor-changes {final!r}.")
    return plan


def _ripple_collateral(deletes: list, items: list) -> list[str]:
    """What a rippled cut would take from items it does not name.

    Measured on Resolve 21.1 (scratch project, 2026-10-01): a ripple
    `DeleteClips` removes the deleted span's TIME from every row - an
    overlay after it moves earlier, and an overlay straddling it loses
    the overlapping frames off its tail. Nothing in the API can split
    or move an item instead, so a rippled cut is carried only where
    every item it overlaps is itself being cut; anything else refuses
    here, before a write, naming the item that would change.
    """
    doomed = {id(step["item"]) for step in deletes}
    problems = []
    for step in deletes:
        if not step["edit"].get("ripple"):
            continue
        start, end = step["item"]["record_in"], step["item"]["record_out"]
        for item in items:
            if id(item) in doomed:
                continue
            if item.get("record_in") < end and start < item.get("record_out"):
                problems.append(
                    f"  cut {step['edit']['row']} {step['edit']['name']!r} "
                    f"source {_span(step['edit']['source_in_frame'], step['edit']['source_out_frame'])}"
                    f" closes its gap across every row, and that would "
                    f"trim or remove {_row(item)} {item.get('name')!r} at "
                    f"record {_span(item.get('record_in'), item.get('record_out'))}"
                    f" - a rippled cut over another row's item is not "
                    f"carried")
                doomed.add(id(item))
    return problems


def _handle(rows: list, item: dict):
    """The live handle `item` (a snapshot entry) was read from."""
    wanted_id = str(item.get("unique_id") or "")
    for row in rows:
        if (row["type"] != item.get("track_type")
                or row["index"] != item.get("track_index")):
            continue
        for handle in row["items"]:
            if wanted_id:
                try:
                    handle_id = str(handle.GetUniqueId() or "")
                except Exception:  # noqa: BLE001 - unreadable is not this one
                    handle_id = ""
                if handle_id == wanted_id:
                    return handle
                continue
            if (handle.GetStart() == item.get("record_in")
                    and handle.GetName() == item.get("name")):
                return handle
    raise EditorEditCarryRefused(
        f"staged item {item.get('name')!r} on {_row(item)} at record "
        f"{item.get('record_in')} could not be found again to write to; "
        f"the staging changed under the carry.")


def apply_plan(staged_timeline, project, plan: dict, final: str) -> list:
    """Write `plan` onto the staging timeline. Returns what was written."""
    from library.tools import composed_edit, reel_read
    from library.tools.resolve_lock import cursor_excursion

    written = []
    with cursor_excursion(project, staged_timeline,
                          f"carry editor edits onto {final}"):
        def rows():
            return reel_read.live_items(staged_timeline,
                                        resolve_project=project)

        current = rows()
        for step in plan["set_enabled"]:
            handle = _handle(current, step["item"])
            handle.SetClipEnabled(bool(step["edit"]["after"]))
            written.append(step["edit"]["id"])
        for step in plan["set_transform"]:
            handle = _handle(current, step["item"])
            composed_edit.set_properties(
                handle, {step["key"]: step["edit"]["after"]})
            written.append(step["edit"]["id"])
        # Lifts first, all at once: they move nothing. Rippled cuts
        # last, latest record span first, one span per call, so no
        # delete shifts an item a later one still has to find.
        lifts = [step for step in plan["delete"]
                 if not step["edit"].get("ripple")]
        if lifts:
            staged_timeline.DeleteClips(
                [_handle(current, step["item"]) for step in lifts], False)
            written.extend(step["edit"]["id"] for step in lifts)
        spans: dict = {}
        for step in plan["delete"]:
            if step["edit"].get("ripple"):
                key = (step["item"]["record_in"], step["item"]["record_out"])
                spans.setdefault(key, []).append(step)
        for key in sorted(spans, reverse=True):
            current = rows()
            staged_timeline.DeleteClips(
                [_handle(current, step["item"]) for step in spans[key]],
                True)
            written.extend(step["edit"]["id"] for step in spans[key])
    return written


def carry_editor_edits(project_folder: str, final: str, project,
                       staged_timeline, read_staged,
                       detection: dict, *, accept: bool = False) -> dict:
    """Apply every edit in force for `final` onto the staging timeline.

    `detection` is `reel_replace_guard.detect_editor_changes` over the
    live and staged reads; `read_staged()` returns a full snapshot of
    staging as it stands now, and is called only when an edit is in
    force. Returns the carry report the promotion keeps
    and hands to `verify_carried_edits` and `record_after_promotion`.
    With `accept`, an edit that cannot be mapped is dropped from this
    carry (and later superseded) rather than refusing.
    """
    edits, superseded = edits_in_force(
        project_folder, final, detection.get("pending") or [])
    if not edits:
        return {"edits": [], "superseded": superseded, "written": [],
                "already_held": []}
    staged_snapshot = read_staged()
    try:
        plan = plan_application(edits, staged_snapshot, final)
    except EditorEditCarryRefused:
        if not accept:
            raise
        mappable, dropped = [], {}
        for edit in edits:
            try:
                plan_application([edit], staged_snapshot, final)
                mappable.append(edit)
            except EditorEditCarryRefused:
                dropped[edit["id"]] = (
                    f"--accept-editor-changes {final}: could not be mapped")
        edits, superseded = mappable, {**superseded, **dropped}
        plan = plan_application(edits, staged_snapshot, final)
    written = apply_plan(staged_timeline, project, plan, final) if (
        plan["delete"] or plan["set_enabled"] or plan["set_transform"]) \
        else []
    return {"edits": edits, "superseded": superseded,
            "written": written, "already_held": plan["already_held"]}


def verify_carried_edits(report: dict, staged_after: dict,
                         final: str) -> None:
    """Refuse unless every carried edit holds on a fresh read of staging."""
    items = list(staged_after.get("items") or ())
    missed = []
    for edit in report.get("edits") or ():
        passage = _span(edit["source_in_frame"], edit["source_out_frame"])
        if edit["kind"] == "cut":
            still = [item for item in items if _overlaps(edit, item)]
            if still:
                missed.append(
                    f"  cut {edit['row']} {edit['name']!r} source {passage} "
                    f"still plays at record " + ", ".join(
                        _span(item.get("record_in"), item.get("record_out"))
                        for item in still))
            continue
        exact = [item for item in items if _governs(edit, item)]
        if len(exact) != 1:
            missed.append(
                f"  {edit['kind']} {edit['row']} {edit['name']!r} source "
                f"{passage}: staging now plays it {len(exact)} time(s)")
            continue
        if edit["kind"] == "enabled":
            got = exact[0].get("enabled")
        else:
            got = (exact[0].get("transform") or {}).get(
                edit["field"].split(".", 1)[1])
        if not _same(got, edit["after"]):
            missed.append(
                f"  {edit['field']} on {edit['row']} {edit['name']!r} "
                f"source {passage}: wanted {edit['after']!r}, staging "
                f"reads {got!r}")
    if missed:
        raise EditorEditCarryRefused(
            f"REFUSING to replace {final!r}: the editor's carried edits "
            f"did not land on the staged timeline. Nothing was renamed; "
            f"the live timeline is still in the project.\n"
            + "\n".join(missed))


def record_after_promotion(project_folder: str, final: str,
                           report: dict | None, promoted: dict, *,
                           act: str) -> dict:
    """File the edits in force, judged on the promoted read.

    Called once the promoted timeline is read back. Every edit this
    promotion carried, and every edit already on the ledger, that the
    promoted timeline HOLDS stays active; one it does not hold was
    changed by this Ren act on purpose (a touch re-enabling a graphic),
    so it is superseded with the act named - the next rebuild must not
    undo the act.
    """
    from library.tools import plan_provenance

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    report = report or {}
    superseded = dict(report.get("superseded") or {})
    edits = {edit["id"]: edit for edit in
             plan_provenance.carried_editor_edits(review_dir, final)
             if edit["id"] not in superseded}
    carried_now = {edit["id"] for edit in report.get("edits") or ()}
    edits.update({edit["id"]: edit for edit in report.get("edits") or ()})
    now = datetime.now(UTC).isoformat(timespec="seconds")
    held = []
    for edit in edits.values():
        try:
            verify_carried_edits({"edits": [edit]}, promoted, final)
        except EditorEditCarryRefused:
            superseded[edit["id"]] = f"changed by {act}"
            if edit["id"] in carried_now:
                held.append({**edit, "status": "superseded",
                             "superseded_at": now,
                             "superseded_by": f"changed by {act}"})
            continue
        held.append({**edit, "last_carried_at": now,
                     "last_carried_by": act})
    plan_provenance.record_carried_edits(review_dir, final, held,
                                         superseded=superseded)
    return {"carried": sorted(edit["id"] for edit in held
                              if edit.get("status") == "active"),
            "superseded": superseded}


def describe(report: dict | None) -> list[str]:
    """One plain line per edit, for a promotion's printed summary."""
    if not report:
        return []
    written = set(report.get("written") or ())
    return [f"{edit['wording']} "
            f"({'applied' if edit['id'] in written else 'already held'})"
            for edit in report.get("edits") or ()]
