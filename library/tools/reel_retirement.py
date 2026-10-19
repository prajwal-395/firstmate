"""A promotion DELETES the timeline it replaces, unless asked to keep it.

The captain, 2026-09-18, withdrawing his own 2026-09-12 ruling
(`data/vep-can-it-hold-up-in-a-real-editing-workflow` §7 - *"promotion
must retire rather than delete, so a round can be compared against the
last"*): *"i just want the final timelines to exist in the project
unless i explicitly ask for otherwise."* He said it after noticing
"(archived round 001)" on reel titles - and the two instructions fight,
because he has separately asked four times for no leftover timelines.
No leftovers wins; comparison becomes an explicit per-request thing.

So the DEFAULT path (`promote_staged_reels` with no `retain`
declaration) deletes the backup it just made (`delete_backups`), and
`05 - Reels/Archive` stays empty unless something explicitly asks for
retention. Only the default changed: the retirement path -
`retire_timelines`, the naming, the sibling-collision handling, the
archive bin - stays reachable for that explicit ask.

The tension this module is built around
---------------------------------------
"Retire, do not delete" must not mean "leave old timelines lying around
where they are reviewed". The captain has asked four separate times for
leftover timelines to be cleared, and on 2026-09-11 three scratch
timelines sitting in `05 - Reels` beside the captain's own Reel 13 are
how they came to place feedback on a throwaway
(`resolve_bin_layout.SCRATCH_BIN` records that incident). An archive that
grows by eight timelines a round recreates exactly that problem, one
round later.

So retirement has a HOME and a LIFECYCLE THAT ENDS, and the two are
separate answers.

The home: `05 - Reels/Archive`
------------------------------
Already declared - `resolve_bin_layout.BINS` carries it with the purpose
*"Superseded reel versions, moved here when a rebuild lands rather than
renamed with a suffix"* - and, until now, nothing put anything in it. No
new bin is invented here (`resolve_bin_layout` owns every bin path);
the one that was written for this is used for it.

Two things keep it out of the review path where the scratch incident's
did not. It is a NAMED SUB-BIN, never loose beside a deliverable - the
scratch sat one level SHALLOWER than the reel it was confused with. And
the retired timeline is RENAMED as it goes: `Reel 09 - ... (archived
round 003)` can not be mistaken for `Reel 09 - ...` in any list, in any
bin, by anyone.

The lifecycle: bounded by REELS, never by rounds
------------------------------------------------
`RETAINED_GENERATIONS = 1`. When a promotion retires a reel, any
EARLIER archived generation of that same reel is collected. So the
archive holds at most one timeline per reel - the immediately previous
version, which is the one a round diff compares against - and its size
is bounded by how many reels the project has, not by how long the
project runs. That is the property the scratch bin lacked: it cannot
grow with time, so it cannot become the complaint again.

Two exceptions, both deliberate:

- a generation carrying a SIGN-OFF is never collected (`reel_signoff`),
  and a signed-off reel's backup is never deleted either: promotion
  forces those onto the retire path even with no `retain` declaration.
  The captain approved that cut; deleting the only copy of the thing
  they approved is exactly what "unless i explicitly ask for otherwise"
  does not cover. Sign-offs are rare, explicit and
  withdrawable, so this cannot grow quietly - and every retained
  sign-off is NAMED in the report on every build.
- collection is `assert_deletion_scope`-guarded against the archived
  generations of the reels being promoted, and nothing else. The set it
  is allowed to touch is computed from the archive naming, so a wrong
  list refuses rather than widening.

And the record outlives the timeline. `versions.rounds` stores each
round's ROWS on disk, so "what did round 3 look like" is answerable
after round 3's timeline is deleted. Deleting a timeline loses the
ability to re-open it in Resolve; it never loses the ability to say
what it contained. That asymmetry is what makes a default that deletes
acceptable at all - it is now the only thing left.

`tests/unit/reels/test_reel_retirement.py`.
"""

from __future__ import annotations

from library.tools import resolve_bin_layout as bins

RETAINED_GENERATIONS = 1
"""How many archived generations of one reel survive a promotion.

One: the version the current one replaced, which is exactly what a
round diff needs and no more. This is a RETENTION bound, not a timeout -
nothing expires with the clock, and a reel that is never rebuilt keeps
its archived generation for ever.
"""

ARCHIVE_BIN = (bins.REELS_BIN, bins.REELS_ARCHIVE_BIN)
"""Where a retired timeline lives. Declared in `resolve_bin_layout`
for this purpose before anything used it; this module is the user."""

ARCHIVED_PATTERN = bins.ARCHIVED_TIMELINE_PATTERN
"""An alias, not a declaration: `resolve_bin_layout` owns every name
that decides a bin, so the retirement and the organiser cannot drift
apart about what an archived timeline is called."""


class RetirementRefused(RuntimeError):
    """Retirement declined to move or collect something, and says which."""


def archived_name(final: str, round_number, taken=()) -> str:
    """`Reel 09 - ... (archived round 003)`, never a name already taken.

    Zero-padded so Resolve's alphabetical listing walks the rounds in
    order, and readable enough that the round it belonged to is the
    first thing a reader sees.

    Two rebuilds of one reel inside a single round would otherwise
    produce one name twice - Resolve allows duplicate timeline names,
    so the second would be indistinguishable from the first and the
    retention bound could not tell them apart. `taken` is the names
    already in the project; a collision gains a `.2`, `.3` sibling,
    which sorts after the bare form and parses back to the same round.
    """
    try:
        number = int(round_number)
    except (TypeError, ValueError):
        number = 0
    base = f"{final} (archived round {number:03d}"
    taken = set(taken or ())
    candidate = f"{base})"
    sibling = 2
    while candidate in taken:
        candidate = f"{base}.{sibling})"
        sibling += 1
    return candidate


def retiring_round(rounds, final: str, current: int) -> int:
    """Which round the version being retired was current for.

    The highest recorded round that names this reel - its last
    promotion. A reel with no recorded round at all (built before the
    version object existed) is labelled with the round that is
    replacing it, which is true as far as anything knows: it was
    current right up to now.
    """
    numbers = [entry.get("round") for entry in rounds or ()
               if final in (entry.get("reels") or {})]
    return max(numbers) if numbers else current


def is_archived_timeline(name: str) -> bool:
    """Is this an archived generation rather than a live reel?"""
    return bins.is_archived_timeline(name)


def parse_archived(name: str):
    """`(reel, round)` for an archived name, or `(None, None)`.

    A `.2` sibling parses to the same round as the bare form: it is a
    second retirement inside that round, not a round of its own.
    """
    match = ARCHIVED_PATTERN.match(name or "")
    if not match:
        return None, None
    return match.group("reel"), int(match.group("round"))


def generations_of(names, final: str) -> list:
    """Every archived generation of one reel, newest round first."""
    out = []
    for name in names or ():
        reel, number = parse_archived(name)
        if reel == final:
            out.append((number, name))
    return [name for _number, name in sorted(out, reverse=True)]


def parse_retain(raw) -> set:
    """Reel names whose superseded generation this promotion may RETIRE.

    The explicit opt-in the 2026-09-18 default answers to: absent means
    the backup is deleted, and a future "keep the old one so I can
    compare" names the reel here. Accepts a reel name in any container
    spelling (`reel_signoff.base_name`), so the exact string a report
    prints pastes back. Anything that is not a non-empty string raises
    rather than reading as an empty declaration - the same rule
    `reel_signoff.parse_supersede` holds to, and for the same reason:
    a silently empty declaration is retention that passed without
    being asked.
    """
    from library.tools import reel_signoff as _signoff

    if raw is None:
        return set()
    if isinstance(raw, str):
        raw = [raw]
    try:
        specs = list(raw)
    except TypeError:
        raise ValueError(
            f"retain must be a list of reel names, got {raw!r}.")
    out = set()
    for spec in specs:
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError(
                f"retain specs must be non-empty reel names, got "
                f"{spec!r}.")
        out.add(_signoff.base_name(spec.strip()))
    return out


def plan_collection(existing_names, finals, signed_off_reels=(),
                     retained: int = RETAINED_GENERATIONS) -> dict:
    """Which archived generations go, and which are kept and why.

    Pure: it is handed the names that exist and answers off them, so the
    rule is testable without Resolve and the Resolve half has no policy
    in it. `existing_names` is every timeline name in the project AFTER
    the new generation has been renamed into the archive.

    Returns `{"collect": [names], "kept": [{"name", "why"}]}`. Only the
    reels in `finals` are considered at all: a promotion tidies what it
    touched and nothing else.

    `retained` is how many archived generations of one reel survive.
    The default is the retention bound above - the explicit-retain path.
    The default-delete path passes 0, so every non-signed-off archived
    generation of a promoted reel goes and the archive ends empty for
    it. A sign-off survives either way.
    """
    signed = {str(reel) for reel in (signed_off_reels or ())}
    collect, kept = [], []
    for final in finals or ():
        generations = generations_of(existing_names, final)
        for position, name in enumerate(generations):
            if position < retained:
                kept.append({
                    "name": name,
                    "why": f"the most recent archived generation of "
                           f"{final!r} - what the round diff compares "
                           f"the current cut against"})
                continue
            if final in signed:
                kept.append({
                    "name": name,
                    "why": f"{final!r} carries a captain sign-off, and "
                           f"the cut they approved is never collected"})
                continue
            collect.append(name)
    return {"collect": collect, "kept": kept}


# ── The Resolve half ─────────────────────────────────────────────

def _archive_folder(pool):
    """The archive bin, looked up first and created only if absent.

    `AddSubFolder` forks a same-named duplicate rather than refusing
    (`execution/organise_media_pool.ensure_folder`), so the lookup-first
    helper is the only way to reach a bin; this never invents its own.
    """
    from library.tools.execution.organise_media_pool import ensure_folder

    root = pool.GetRootFolder()
    if not root:
        raise RetirementRefused(
            "the media pool has no root folder, so the archive bin "
            "cannot be reached; nothing was retired.")
    folder = root
    for depth, part in enumerate(ARCHIVE_BIN):
        folder = ensure_folder(pool, folder, part, None,
                               ARCHIVE_BIN[:depth])
    return folder


def retire_timelines(project, pool, backups: dict,
                     rounds_by_final: dict) -> dict:
    """Rename each backup into the archive and file it there.

    `backups` is `{final timeline name: backup timeline object}` - the
    containers `promote_staged_reels` moved the approved timelines into
    before the staging took their names. Each is renamed to
    `archived_name(final, rounds_by_final[final])` and its pool item
    moved to `05 - Reels/Archive`.

    Judged by what Resolve RETURNS, never by `hasattr` (AGENTS.md 5).
    A rename that returns falsy RAISES: the alternative is a timeline
    called `... (pre-rebuild backup)` left in a reels bin, which is
    debris the next build refuses on. A FILING failure does not raise -
    the timeline is safely renamed and merely in the wrong bin, and the
    organiser files a stray archived timeline back on the next build.

    Returns `{"archived": {final: archived name}, "unfiled": [names]}`.
    """
    archived, unfiled = {}, []
    folder = None
    taken = set()
    for index in range(1, project.GetTimelineCount() + 1):
        existing = project.GetTimelineByIndex(index)
        if existing:
            taken.add(existing.GetName())
    for final, timeline in sorted((backups or {}).items()):
        name = archived_name(
            final, (rounds_by_final or {}).get(final, 0), taken)
        taken.add(name)
        if not timeline.SetName(name):
            raise RetirementRefused(
                f"Resolve would not rename {final!r}'s backup to "
                f"{name!r}. The approved content is safe under its "
                f"backup name - rename it in Resolve and re-run; "
                f"nothing was deleted.")
        archived[final] = name
        try:
            if folder is None:
                folder = _archive_folder(pool)
            item = timeline.GetMediaPoolItem()
            if not item or not pool.MoveClips([item], folder):
                unfiled.append(name)
        except Exception:                                   # noqa: BLE001
            unfiled.append(name)
    return {"archived": archived, "unfiled": unfiled}


def delete_backups(project, pool, backup_objects: dict | list | tuple, *,
                   project_folder: str, run_id: str, job_id: str) -> dict:
    """Delete the backup timelines outright - the DEFAULT promotion path.

    `backup_objects` is the backup timeline objects for the generations
    the promotion just replaced. A mapping remains accepted for existing
    callers; a sequence lets a verified retry name duplicate backup
    generations by object identity. No rename, no filing: the archive
    stays empty.

    The DELETE is the risky half, so this is bounded TIGHTER than the
    collection it replaces. Every name must end in the promotion's own
    backup suffix (`reel_build.BACKUP_SUFFIX`) - anything else refuses
    before anything is read further. Then `assert_deletion_scope` is
    asked of the list about to be deleted against exactly those names,
    so a wrong selection refuses rather than widening (the captain's
    2026-09-06 ruling). Names are read BEFORE the delete: a deleted
    timeline object answers `GetName()` with None. A falsy delete
    RAISES rather than reporting the names deleted - the refused note
    is a handled, rendered outcome and the generation stays present,
    so the next build plans it again.

    Returns `{"deleted": [names]}`.
    """
    from library.tools.reel_build import (
        BACKUP_SUFFIX,
        assert_deletion_scope,
    )
    from library.tools import artifact_lifecycle as lifecycle
    from library.tools import event_log

    targets = (list(backup_objects.values()) if isinstance(backup_objects, dict)
               else list(backup_objects or ()))
    names = [timeline.GetName() for timeline in targets]
    foreign = [name for name in names
               if not name.endswith(BACKUP_SUFFIX)]
    if foreign:
        raise RetirementRefused(
            f"REFUSING to delete: {foreign} are not pre-rebuild backups. "
            f"This path deletes the generation a promotion just replaced "
            f"and nothing else - nothing was deleted.")
    target_ids = [str(timeline.GetUniqueId() or "") for timeline in targets]
    if not targets or any(not identity for identity in target_ids) \
            or len(target_ids) != len(set(target_ids)):
        raise RetirementRefused(
            "REFUSING to delete: backup timeline identities are missing "
            "or duplicated; nothing was deleted.")
    live_by_id = {}
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline:
            identity = str(timeline.GetUniqueId() or "")
            if identity:
                live_by_id[identity] = timeline
    if any(live_by_id.get(identity) is None
           or live_by_id[identity].GetName() != name
           for identity, name in zip(target_ids, names)):
        raise RetirementRefused(
            "REFUSING to delete: a backup identity or name changed before "
            "deletion; nothing was deleted.")
    assert_deletion_scope(targets, set(names))
    record: dict = {"deleted": []}
    if targets:
        project_id = str(project.GetUniqueId() or "")
        if not project_id or not all((project_folder, run_id, job_id)):
            raise RetirementRefused(
                "backup retirement needs the Resolve project identity and "
                "owning Ren job; nothing was deleted")
        log_path = event_log.log_path(project_folder)
        for timeline, name, timeline_id in zip(targets, names, target_ids):
            identity = {"object_type": "timeline",
                        "project_unique_id": project_id,
                        "object_unique_id": timeline_id,
                        "project_name": project.GetName(),
                        "object_name": name}
            artifact_id = lifecycle.stable_artifact_id(
                project_id, run_id, job_id, "resolve_reel_backup", timeline_id)
            lifecycle.register_resolve_artifact(
                log_path, artifact_id=artifact_id,
                kind="resolve_reel_backup", project_id=project_id,
                run_id=run_id, job_id=job_id, identity=identity,
                state=lifecycle.COMPLETED)
            latest = next(row for row in lifecycle.resolve_artifacts(log_path)
                          if row["artifact_id"] == artifact_id)
            if latest["state"] == lifecycle.COMPLETED:
                lifecycle.transition_resolve_artifact(
                    log_path, artifact_id, lifecycle.DISPOSABLE,
                    reason="promotion record committed; backup has no remaining owner")
            elif latest["state"] != lifecycle.DISPOSABLE:
                raise RetirementRefused(
                    f"backup lifecycle state is protected: {latest['state']}")

            def delete_exact(recorded_identity, *, expected_name=name):
                if str(project.GetUniqueId() or "") != recorded_identity[
                        "project_unique_id"]:
                    raise RetirementRefused("Resolve project identity changed before backup delete")
                current = [project.GetTimelineByIndex(index)
                           for index in range(1, project.GetTimelineCount() + 1)
                           if project.GetTimelineByIndex(index)]
                matches = [item for item in current
                           if str(item.GetUniqueId() or "")
                           == recorded_identity["object_unique_id"]]
                if not matches:
                    return {"verified": True, "already_absent": True,
                            "deleted_timeline_id": recorded_identity["object_unique_id"]}
                if (len(matches) != 1
                        or matches[0].GetName() != expected_name
                        or expected_name != recorded_identity["object_name"]):
                    raise RetirementRefused(
                        "backup timeline identity or name changed before deletion")
                assert_deletion_scope(matches, {expected_name})
                if not pool.DeleteTimelines(matches):
                    return {"verified": False, "delete_returned": False}
                remaining_ids = {
                    str(item.GetUniqueId() or "")
                    for index in range(1, project.GetTimelineCount() + 1)
                    if (item := project.GetTimelineByIndex(index)) is not None
                }
                return {"verified": recorded_identity["object_unique_id"]
                            not in remaining_ids,
                        "deleted_timeline_id": recorded_identity["object_unique_id"]}

            try:
                lifecycle.retire_resolve_artifact(
                    log_path, artifact_id, identity, delete_exact)
            except lifecycle.LifecycleRefused as exc:
                raise RetirementRefused(
                    f"Resolve backup {name!r} was not retired: {exc}") from exc
            record["deleted"].append(name)
    return record


def collect_superseded(project, pool, existing_names, finals,
                       signed_off_reels=(),
                       retained: int = RETAINED_GENERATIONS,
                       protected_names=()) -> dict:
    """Delete the archived generations the retention bound releases.

    One of the TWO deletions this module performs. On the explicit-
    retain path it deletes the generation BEFORE the one just retired -
    so a project holds the previous cut, and a signed-off cut for
    ever. On the default-delete path (`retained=0`) it deletes every
    non-signed-off archived generation of the promoted reels, so the
    archive ends empty for them.

    `assert_deletion_scope` is asked of the list about to be deleted,
    against the archived names the plan chose, so a wrong selection
    refuses rather than widening (`reel_build.assert_deletion_scope`,
    the captain's 2026-09-06 ruling).
    """
    from library.tools.reel_build import assert_deletion_scope, timelines_to_replace

    protected = set(protected_names or ()) & set(existing_names or ())
    eligible = set(existing_names or ()) - protected
    plan = plan_collection(eligible, finals, signed_off_reels,
                           retained=retained)
    record = {"collected": [], "kept": plan["kept"],
              "planned": list(plan["collect"])}
    record["kept"].extend(
        {"name": name,
         "why": "created or renamed by the editor; left untouched"}
        for name in sorted(protected))
    if not plan["collect"]:
        return record
    targets = timelines_to_replace(project, set(plan["collect"]))
    assert_deletion_scope(targets, set(plan["collect"]))
    if targets:
        # Named BEFORE the delete. A deleted timeline object answers
        # `GetName()` with None, so reading the report off it afterwards
        # crashes the collection it was reporting on - measured
        # 2026-09-12 against a real Resolve, and invisible until then
        # because `RETAINED_GENERATIONS = 1` means nothing is collected
        # until a reel is retired a SECOND time.
        collected = sorted(name for name in
                           (t.GetName() for t in targets) if name)
        # Judged by what Resolve RETURNS, never by `hasattr` (AGENTS.md
        # 5): a falsy delete RAISES rather than reporting the names
        # collected. Measured 2026-09-14 against a real Resolve
        # (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`) - the report
        # listed a timeline under `collected` while the census still
        # showed it present. The refused note is already a handled,
        # rendered outcome (`reel_build` catches it and the run
        # continues - the reels are promoted), and the generation stays
        # archived, so the next build plans it again.
        if not pool.DeleteTimelines(targets):
            raise RetirementRefused(
                f"Resolve declined to delete {len(collected)} archived "
                f"generation(s) ({', '.join(collected)}). They are still "
                f"in the project under their archived names - nothing "
                f"was reported collected.")
        record["collected"] = collected
    return record


def retire_owned_qualification_timeline(resolve, manager, project_folder,
                                       artifact_id: str) -> dict:
    """Retire one completed qualification timeline by recorded Resolve ID.

    This is the AXI guarded retirement operation. The caller holds the
    exclusive Resolve lease; the ownership record, exact project/timeline
    IDs, content fingerprint, durable plan, and post-delete census all have
    to agree before the mutation is allowed.
    """
    import json
    from datetime import UTC, datetime

    from library.tools import artifact_lifecycle as lifecycle
    from library.tools.qualification_project import (
        QUALIFICATION_PROJECT, borrowed, switch_project,
    )
    from library.tools.resolve_lock import exclusive_held
    from library.tools.shared_environment import vep_home

    if not exclusive_held():
        raise RetirementRefused("Resolve timeline retirement requires its exclusive lease")
    log_path = lifecycle.event_log.log_path(project_folder)
    rows = [row for row in lifecycle.resolve_artifacts(log_path)
            if row["artifact_id"] == artifact_id
            and row["kind"] == "resolve_qualification_timeline"]
    if len(rows) != 1:
        raise RetirementRefused("timeline has no unique recorded qualification ownership")
    row = rows[0]
    if row.get("state") not in {lifecycle.DISPOSABLE, lifecycle.PURGING}:
        raise RetirementRefused(f"timeline is not disposable: {row.get('state')}")
    identity = row.get("identity") or {}
    if (identity.get("object_type") != "timeline"
            or identity.get("project_name") != QUALIFICATION_PROJECT
            or not identity.get("project_unique_id")
            or not identity.get("object_unique_id")
            or not identity.get("content_sha256")):
        raise RetirementRefused("timeline ownership identity or change fingerprint is incomplete")

    plan_root = vep_home() / "resolve-timeline-retirement"
    plan_root.mkdir(parents=True, exist_ok=True)
    plan_path = plan_root / f"{artifact_id}.json"
    plan = {"format": "ren-resolve-timeline-retirement/1",
            "artifact_id": artifact_id, "identity": identity,
            "created_at": datetime.now(UTC).isoformat(),
            "selected": True, "job_id": row["job_id"],
            "run_id": row["run_id"]}
    plan_path.write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n",
                         encoding="utf-8")
    try:
        readback_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RetirementRefused(f"retirement plan did not read back: {exc}") from exc
    if (readback_plan.get("artifact_id") != artifact_id
            or readback_plan.get("identity") != identity
            or readback_plan.get("selected") is not True):
        raise RetirementRefused("retirement plan identity changed before apply")

    def delete_exact(recorded_identity):
        with borrowed(resolve):
            project = switch_project(manager, QUALIFICATION_PROJECT)
            project_id = str(project.GetUniqueId() or "") if project else ""
            if project_id != recorded_identity["project_unique_id"]:
                raise RetirementRefused("qualification project identity changed")
            matches = []
            for index in range(1, int(project.GetTimelineCount() or 0) + 1):
                timeline = project.GetTimelineByIndex(index)
                if timeline and str(timeline.GetUniqueId() or "") == recorded_identity[
                        "object_unique_id"]:
                    matches.append(timeline)
            if not matches:
                return {"verified": True, "project_unique_id": project_id,
                        "deleted_timeline_id": recorded_identity["object_unique_id"],
                        "already_absent": True}
            if len(matches) != 1:
                raise RetirementRefused("recorded timeline id is ambiguous")
            target = matches[0]
            if target.GetName() != recorded_identity["object_name"]:
                raise RetirementRefused("recorded timeline name changed")
            if not project.SetCurrentTimeline(target):
                raise RetirementRefused("Resolve would not select the recorded timeline")
            current = project.GetCurrentTimeline()
            if (not current or str(current.GetUniqueId() or "")
                    != recorded_identity["object_unique_id"]):
                raise RetirementRefused("recorded timeline selection did not read back")
            from library.tools.qualification_workflow import _timeline_fingerprint
            fingerprint = _timeline_fingerprint(project, current)
            if fingerprint != recorded_identity["content_sha256"]:
                raise RetirementRefused(
                    "qualification timeline changed after its owning job; human edits are held")
            if not manager.SaveProject():
                raise RetirementRefused("Resolve refused to save before timeline retirement")
            alternates = [project.GetTimelineByIndex(index)
                          for index in range(1, int(project.GetTimelineCount() or 0) + 1)
                          if project.GetTimelineByIndex(index)]
            alternate = next((candidate for candidate in alternates
                              if str(candidate.GetUniqueId() or "")
                              != recorded_identity["object_unique_id"]), None)
            if alternate is None:
                raise RetirementRefused(
                    "no other timeline can preserve the project while retiring this one")
            if not project.SetCurrentTimeline(alternate):
                raise RetirementRefused("Resolve would not move off the target timeline")
            current = project.GetCurrentTimeline()
            if (not current or str(current.GetUniqueId() or "")
                    == recorded_identity["object_unique_id"]):
                raise RetirementRefused("Resolve cursor still points at the target timeline")
            pool = project.GetMediaPool()
            if not pool.DeleteTimelines([target]):
                raise RetirementRefused("Resolve refused to delete the recorded timeline")
            remaining = [project.GetTimelineByIndex(index)
                         for index in range(1, int(project.GetTimelineCount() or 0) + 1)]
            if any(item and str(item.GetUniqueId() or "")
                   == recorded_identity["object_unique_id"] for item in remaining):
                raise RetirementRefused("timeline id remains in Resolve after deletion")
            if not manager.SaveProject():
                raise RetirementRefused("Resolve refused to save the verified timeline deletion")
            return {"verified": True, "project_unique_id": project_id,
                    "deleted_timeline_id": recorded_identity["object_unique_id"],
                    "remaining_timeline_ids": [str(item.GetUniqueId() or "")
                                                for item in remaining if item]}

    result = lifecycle.retire_resolve_artifact(
        log_path, artifact_id, identity, delete_exact)
    return {"plan": str(plan_path), **result}


def render(report: dict) -> str:
    """What retirement did, in the sentences an operator has to read."""
    lines = []
    archived = (report or {}).get("archived") or {}
    if archived:
        lines.append(
            f"  Retired {len(archived)} replaced timeline(s) to "
            f"{'/'.join(ARCHIVE_BIN)} - nothing was deleted here:")
        for final in sorted(archived):
            lines.append(f"    {final} -> {archived[final]}")
    deleted = (report or {}).get("deleted") or []
    if deleted:
        lines.append(
            f"  Deleted {len(deleted)} superseded backup(s) - one "
            f"timeline per reel, nothing archived: "
            f"{', '.join(deleted)}")
    for name in (report or {}).get("unfiled") or ():
        lines.append(
            f"  {name!r} was renamed but not moved into "
            f"{'/'.join(ARCHIVE_BIN)}; the organiser files it on the "
            f"next build.")
    collected = (report or {}).get("collected") or []
    if collected:
        lines.append(
            f"  Collected {len(collected)} superseded generation(s) "
            f"(retention: {RETAINED_GENERATIONS} per reel): "
            f"{', '.join(collected)}")
    for kept in (report or {}).get("kept") or []:
        lines.append(f"  Kept {kept['name']!r} - {kept['why']}")
    if not lines:
        lines.append("  Nothing to retire: no timeline was replaced.")
    return "\n".join(lines)
