"""Comparison timelines retire like reels do, and end the same way.

The gap this closes
-------------------
A suffix verification build (`rebuild_reels_in_project(name_suffix=...)`,
e.g. `Reel 13 - ... (baseline scratch)`) promotes into its suffixed
final, takes a hold on it (`staging_holds` - awaiting an explicit human
promotion decision), and then nothing ever retires or collects it. The
next comparison suffix lands beside it, and the one after that beside
both. Measured 2026-09-13 on `lucie/geo-podcast`: 24 timeline records
for about 9 reels - the same accumulation the reel archive was built to
stop, arriving by the door the archive does not watch. The captain has
asked four separate times for leftover timelines to be cleared, and
`resolve_bin_layout.SCRATCH_BIN` records the incident a throwaway left
where it could be reviewed caused.

So this module extends `reel_retirement`'s treatment to them, reusing
its shape rather than inventing a second one: the same home-plus-
lifecycle split, the same scope guard, the same never-collect-a-
sign-off rule.

The home: `05 - Reels/Archive`, the SAME bin
--------------------------------------------
Checked before adding: `resolve_bin_layout` declares the archive bin
for "superseded reel versions, moved here when a rebuild lands rather
than renamed with a suffix" - a superseded comparison IS one - and no
bin is declared for comparison generations, so no new bin is invented
(`resolve_bin_layout` owns every bin path). What comes free with the
same home: `is_canonical` already accepts it, the organiser already
files a stray `(archived round NNN)` name back into it
(`resolve_organization`, whatever the reel part names - including a
comparison identity), and the conformance sweep already declines to
grade an archived name (`grades_as_a_reel`). A retired comparison is
renamed on the way in (`... (baseline scratch) (archived round 007)`),
so it cannot be mistaken for the live comparison in any list - the
same rename `reel_retirement.retire_timelines` performs, called
verbatim rather than restated.

The lifecycle: bounded by REELS, never by rounds
------------------------------------------------
`RETAINED_COMPARISONS = 1` per base reel. On the next promotion that
touches the base reel, live comparisons beyond the newest retire to
the archive, and archived comparisons beyond the newest one are
collected. The project holds, per reel, the current comparison and
the previous one, and nothing else - bounded by how many reels the
project has, not by how long it runs or how often the captain
compares.

Why one, not two: `manage_project.py round-diff` is the consumer the
retention number was checked against, and it reads stored rows, never
live timelines - `versions.rounds.diff_rounds` runs over the
`versions.rounds` rounds document off disk, with no Resolve, and every
promotion (suffixed or not) stamps the rows the replace guard already
read. Zero live copies are needed for a comparison; the one retained
copy exists so the latest comparison stays openable in Resolve. That
is measured behaviour, not preference, and it is why the record
outliving the timeline is what makes a lifecycle that ends acceptable
at all - the same asymmetry `reel_retirement` and `versions.variants`
state for their own generations.

What moves, and what never does
-------------------------------
- LIVE comparisons are only ever RETIRED (renamed into the archive),
  never deleted by this rule. Deletion is irreversible on the
  captain's machine; a rename is reported and reversible.
- Only ARCHIVED comparison generations are collected, and only when
  the version record proves a newer generation of the same base reel
  exists. Collection is `assert_deletion_scope`-guarded to exactly the
  planned archived names; a guard refusal stops and is reported, never
  worked around.
- A generation carrying a captain SIGN-OFF is never collected - and,
  further, a signed-off LIVE comparison is never retired either. The
  sign-off is read through `reel_signoff.signoff_for` on the
  comparison's own identity, the same way `versions.variants` treats a
  signed-off loser. A sign-off on the BASE reel does not protect its
  comparisons: the approved cut lives on the base timeline itself,
  which this rule never names, and the rows live in the round record.
  That is what keeps the bound a bound for signed-off reels - each
  sign-off explicit, each withdrawable, each named on every build.
- A HELD comparison is never retired: the hold names a promotion
  decision that is still open (`staging_holds`, issue #971), and
  moving the timeline out from under it would strand the hold
  pointing at a dead name. Held generations are kept and NAMED on
  every round.
- An UNRECORDED live comparison - no landed round in the version
  record - is never retired either. It is either hand-made (the
  captain's own work, which automation never auto-touches) or a build
  whose stamp failed (already reported as "round NOT stamped" where
  it happened). Kept, and named every round so a human can act. The
  bound therefore holds for everything the engine records, which is
  every engine build on the normal path.
- Only reels the promotion TOUCHED are considered at all - the same
  "a promotion tidies what it touched and nothing else" rule
  `plan_collection` keeps. A comparison whose base reel did not
  promote is untouched, however stale.

What this deliberately does NOT own
-----------------------------------
- Declared VARIANTS (`Reel 09 - ... (reaction-cutaway)`) are excluded
  by exact name through the spec record. A variant is work the
  captain deliberately asked to keep alive until CHOSEN, and
  `versions.variants` already bounds it (`RETAINED_UNCHOSEN = 1` per
  reel on every choice, with the rows stored so the comparison
  survives collection). Two bounds for one family would be two
  chances to disagree about whose the timeline is.
- STAGING containers (`... (rebuild staging)`) and pre-rebuild
  BACKUPS are excluded: they are owned by the build/promote/discard
  path plus `staging_holds`. A pending promotion is not disposable
  scratch, even when it wears a scratch-shaped name.
- Plan finals, the master timeline and firstmate's proof timelines
  are never members. And the reel archive's own generations
  (`<base> (archived round NNN)`) parse back to the base reel, never
  to a comparison identity, so the two retentions cannot collect each
  other's charges.

Wiring: `promote_staged_reels` runs this in phase 4 beside the reel
retirement, never fatally and always reported. A refusal leaves the
promoted reels promoted and the comparisons standing.

`tests/test_comparison_retirement.py`.
"""

from __future__ import annotations

import os

from library.tools import reel_retirement as retire
from library.tools import resolve_bin_layout as bins

RETAINED_COMPARISONS = 1
"""How many archived comparison generations of one reel survive a round.

One: the previous comparison, which is the version somebody asks to
re-open. This is a RETENTION bound, not a timeout - nothing expires
with the clock - and it is per BASE reel, deliberately, not per
comparison identity: `reel_retirement` groups archived generations by
the reel name they parse back to, and every new suffix is a new
identity, so a per-identity bound would keep one archived timeline
per suffix the project ever tried and grow with the number of
comparisons. Grouping a reel's comparisons together is what makes the
bound a bound on the reel (`versions.variants` takes the same reading
for its own runner-ups, for the same reason).
"""

ARCHIVE_BIN = retire.ARCHIVE_BIN
"""Where a retired comparison lives. An alias, not a declaration: the
same home the reel archive uses, declared in `resolve_bin_layout`
long before either user. This module and `reel_retirement` file into
the same bin; the names they file cannot be mistaken for each other,
because a comparison archive name still carries its comparison
suffix inside the archived one."""


class ComparisonRefused(RuntimeError):
    """Comparison retirement declined to move or collect something."""


def base_final_for(name, plan_finals):
    """The plan final this timeline is a comparison OF, or None.

    `<plan final> + " (" + suffix + ")"`, longest plan-final prefix
    wins: where one reel's final extends another's
    (`Reel 9 - x` beside `Reel 9 - x (final)`), the comparison was
    built by appending to the moment's own timeline name
    (`built_name`), so the longest match is the true base. Exact
    through the plan, never a parse: no syntactic rule tells a
    comparison suffix from a reel's own parenthesised name, so the
    plan's own names are the only authority for what is a base. A
    plan final itself is never a comparison of anything - even where
    one final extends another - because the family check
    (`is_comparison_timeline`) refuses plan finals before asking.
    """
    if name in set(plan_finals or ()):
        return None
    best = None
    for final in plan_finals or ():
        if (final and name != final
                and (name or "").startswith(final + " (")
                and (name or "").endswith(")")
                and (best is None or len(final) > len(best))):
            best = final
    return best


def is_comparison_timeline(name, plan_finals, variant_names=(),
                           master_name=""):
    """Is this timeline a build-produced comparison of a plan reel?

    Narrow on purpose: a plan final, the master, a declared variant,
    a retired generation, a staging or scratch container, a
    pre-rebuild backup and firstmate's proof timelines are all
    refused before the base match is even asked. What remains is a
    name the plan's own final extends with a parenthesised suffix -
    the shape every suffix verification build promotes into.
    """
    if not name or name == master_name:
        return False
    if name in set(plan_finals or ()):
        return False
    if name in set(variant_names or ()):
        return False
    if bins.is_archived_timeline(name):
        return False
    if bins.is_scratch_timeline(name):
        return False
    if bins.is_proof_timeline(name):
        return False
    from library.tools.reel_build import BACKUP_SUFFIX

    if (name or "").endswith(BACKUP_SUFFIX):
        return False
    return base_final_for(name, plan_finals) is not None


def comparison_identity(name):
    """The comparison a live or archived name belongs to.

    A live comparison is its own identity; an archived
    `C (archived round NNN)` parses back to C. None for names that
    are not archived at all (the caller holds the live name itself).
    """
    identity, _number = retire.parse_archived(name or "")
    return identity


def landed_rounds(rounds, names):
    """`{name: highest recorded round}` for names the record names.

    Read off `versions.rounds.discover` - the same reader `round-diff`
    answers from, so "newest" here and "later" there cannot disagree.
    A name the record never names is absent, never zero-filled: zero
    would read as "landed before everything", which is exactly the
    claim an unrecorded comparison must not carry (it stays kept -
    see `plan_collection`).
    """
    wanted = set(names or ())
    out = {}
    for entry in rounds or ():
        try:
            number = int(entry.get("round"))
        except (TypeError, ValueError):
            continue
        for reel in (entry.get("reels") or {}):
            if reel in wanted and number > out.get(reel, -1):
                out[reel] = number
    return out


def generations_of(existing_names, base_final, *, plan_finals,
                   variant_names=(), master_name="", rounds_by_name=None,
                   just_landed=()):
    """Live comparison generations of one base reel, newest first.

    Just-landed names sort first - they landed NOW, which no record
    can predate - then recorded generations by landed round,
    then unrecorded ones last. Ties break by name so the order is
    total and deterministic. Unrecorded sorting last is NOT a claim
    they are oldest; the planner below never retires them at all,
    whatever position they hold.
    """
    rounds_by_name = rounds_by_name or {}
    landed = set(just_landed or ())

    def key(name):
        if name in landed:
            return (0, 0, name)
        round_number = (rounds_by_name or {}).get(name)
        if round_number is None:
            return (2, 0, name)
        return (1, -int(round_number), name)

    found = [name for name in (existing_names or ())
             if is_comparison_timeline(name, plan_finals, variant_names,
                                       master_name)
             and base_final_for(name, plan_finals) == base_final]
    return sorted(found, key=key)


def archived_generations(existing_names, base_final, *, plan_finals,
                         variant_names=(), master_name=""):
    """Archived comparison generations of one base reel, newest first.

    An archived name `C (archived round NNN)` belongs to the base
    reel its identity C is a comparison of - grouped by BASE,
    deliberately, not by identity (see `RETAINED_COMPARISONS`). The
    suffix carries the round, so the order is total: round
    descending, name ascending for the `.2` siblings a round can
    hold. Generations the reel archive owns (identities that are
    plan finals, variants, staging or backups) never match, because
    the identity itself must pass `is_comparison_timeline`.
    """
    out = []
    for name in existing_names or ():
        identity = comparison_identity(name)
        if identity is None:
            continue
        if not is_comparison_timeline(identity, plan_finals,
                                      variant_names, master_name):
            continue
        if base_final_for(identity, plan_finals) != base_final:
            continue
        _identity, number = retire.parse_archived(name)
        out.append((number, name))
    return [name for _number, name in
            sorted(out, key=lambda pair: (-pair[0], pair[1]))]


def plan_collection(existing_names, base_finals, *, plan_finals,
                    variant_names=(), master_name="", rounds_by_name=None,
                    just_landed=(), signed_identities=(),
                    held_names=()) -> dict:
    """Which comparisons retire, which archived ones go, which stay.

    Pure: handed the names that exist, it answers off them, so the
    rule is testable without Resolve and the Resolve half holds no
    policy in it. Only the reels in `base_finals` are considered at
    all: a promotion tidies what it touched and nothing else.

    Returns `{"retire": [{"name", "why"}], "collect": [names],
    "kept": [{"name", "why"}]}`. `retire` names LIVE timelines that
    move to the archive (never deleted); `collect` names ARCHIVED
    timelines only - a live name in `collect` is a planner defect,
    and the Resolve half refuses it before anything is read further.
    """
    signed = set(signed_identities or ())
    held = set(held_names or ())
    rounds_by_name = rounds_by_name or {}
    just = set(just_landed or ())
    retire, collect, kept = [], [], []
    for base in sorted(set(base_finals or ())):
        live = generations_of(
            existing_names, base, plan_finals=plan_finals,
            variant_names=variant_names, master_name=master_name,
            rounds_by_name=rounds_by_name, just_landed=just)
        newest = None
        for name in live:
            if name in signed:
                kept.append({
                    "name": name,
                    "why": f"{name!r} carries a captain sign-off, and "
                           f"the cut they approved is never retired, "
                           f"let alone collected"})
                continue
            if newest is None and (name in just
                                   or name in rounds_by_name):
                newest = name
                landed = ("this round" if name in just
                          else f"round {rounds_by_name[name]}")
                why = (f"the most recent comparison of {base!r} "
                       f"({landed}) - the one that stays openable "
                       f"in Resolve")
                if name in held:
                    why += ("; still under its pending-promotion hold, "
                            "which this does not release")
                kept.append({"name": name, "why": why})
                continue
            if name in held:
                kept.append({
                    "name": name,
                    "why": "under a pending-promotion hold - the "
                           "promotion decision it awaits is still open, "
                           "and moving it would strand the hold on a "
                           "dead name"})
                continue
            if name not in rounds_by_name and name not in just:
                kept.append({
                    "name": name,
                    "why": "no landed round in the version record - "
                           "either hand-made (the captain's own, never "
                           "auto-touched) or a build whose stamp failed "
                           "(already reported where it happened). Kept; "
                           "move or remove it by hand"})
                continue
            retire.append({
                "name": name,
                "why": f"superseded by {newest!r} - retires to "
                       f"{'/'.join(ARCHIVE_BIN)}, still openable"})
        for position, name in enumerate(archived_generations(
                existing_names, base, plan_finals=plan_finals,
                variant_names=variant_names, master_name=master_name)):
            identity = comparison_identity(name)
            if identity in signed:
                kept.append({
                    "name": name,
                    "why": f"{identity!r} carries a captain sign-off, "
                           f"and the cut they approved is never "
                           f"collected"})
                continue
            if position < RETAINED_COMPARISONS:
                kept.append({
                    "name": name,
                    "why": f"the most recent archived comparison of "
                           f"{base!r} - what the round diff compares "
                           f"the current cut against, openable"})
                continue
            collect.append(name)
    return {"retire": retire, "collect": collect, "kept": kept}


# ── Readers (each states what it found; empty is an answer) ─────────

def _plan_finals(project_folder) -> tuple:
    """The plan's own final timeline names, or () when unreadable.

    Unreadable is EMPTY here rather than a raise: with no bases known
    the family is empty and the plan below is a no-op, which is the
    safe direction - and every report below states that nothing was
    bounded, so an empty answer cannot be mistaken for "nothing to
    bound".
    """
    try:
        from library.tools.reel_proposal import proposal_path, read_proposal

        moments = read_proposal(str(proposal_path(project_folder)))
        return tuple(sorted(
            {m.timeline_name for m in moments
             if getattr(m, "timeline_name", "")}))
    except Exception:                                   # noqa: BLE001
        return ()


def _live_names(project) -> list:
    """Every timeline name in the project, in index order."""
    names = []
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline:
            names.append(timeline.GetName())
    return names


# ── The Resolve half ─────────────────────────────────────────────

def collect_for_bases(project, pool, project_folder, promoted_finals,
                      master_timeline_name: str = "",
                      existing_names=None) -> dict:
    """Retire and collect this promotion's superseded comparisons.

    `promoted_finals` is what just landed; each maps to its base reel
    (itself, when it IS a plan final) and only those bases are
    considered. Returns the report `render` reads. Records a refusal
    instead of raising: the reels are already promoted, and a
    comparison lifecycle that breaks a build is worse than one that
    skips a round loudly.
    """
    from library.tools import reel_signoff as _signoff
    from library.tools.versions import rounds as _rounds
    from library.tools import staging_holds as _holds
    from library.tools.versions.variants import declared_variant_timelines

    report: dict = {"bases": [], "retired": {}, "unfiled": [],
                    "collect": [], "collected": [], "kept": [],
                    "planned_retire": [], "planned_collect": [],
                    "refused": "", "notes": []}
    plan_finals = _plan_finals(project_folder)
    if not plan_finals:
        report["notes"].append(
            "no reel plan names could be read, so no timeline is a "
            "comparison of anything - nothing was bounded.")
        return report
    try:
        held = _holds.read_holds(project_folder)
    except Exception as unreadable:                      # noqa: BLE001
        report["refused"] = (
            f"the staging-holds file cannot be read ({unreadable}); "
            f"refusing to judge any comparison until it is inspected "
            f"or removed. Nothing was moved and nothing was deleted.")
        return report
    try:
        _signoff.read_signoffs(project_folder)
    except Exception as unreadable:                      # noqa: BLE001
        report["refused"] = (
            f"the sign-off record cannot be read ({unreadable}); an "
            f"unreadable approval reads exactly like no approval, so "
            f"nothing is judged until it is inspected. Nothing was "
            f"moved and nothing was deleted.")
        return report

    def base_of(final):
        if final in set(plan_finals):
            return final
        return base_final_for(final, plan_finals)

    bases = sorted({base for final in (promoted_finals or ())
                    for base in [base_of(final)] if base})
    report["bases"] = bases
    if not bases:
        report["notes"].append(
            "nothing promoted maps to a plan reel, so no base was "
            "touched - nothing was bounded.")
        return report
    names = list(existing_names if existing_names is not None
                 else _live_names(project))
    from library.tools import plan_provenance as _provenance
    from library.tools import reel_replace_guard as _preservation
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    try:
        protected_names = _provenance.protected_timeline_names(
            review_dir, _preservation.timeline_inventory(project))
    except Exception as unreadable:                       # noqa: BLE001
        report["refused"] = (
            f"the timeline ownership inventory could not be read "
            f"({unreadable}); no comparison timeline was moved or "
            f"deleted.")
        return report
    report["kept"].extend(
        {"name": name,
         "why": "created or renamed by the editor; left untouched"}
        for name in sorted(protected_names))
    names = [name for name in names if name not in protected_names]
    try:
        rounds = _rounds.discover(project_folder)
    except Exception as unreadable:                      # noqa: BLE001
        rounds = []
        report["notes"].append(
            f"the version record cannot be read ({unreadable}) - every "
            f"comparison reads as unrecorded and stays kept. Nothing "
            f"was moved and nothing was deleted.")
    rounds_by_name = landed_rounds(rounds, names)
    variant_names = declared_variant_timelines(project_folder)
    just = {name for name in (promoted_finals or ())
            if is_comparison_timeline(name, plan_finals, variant_names,
                                      master_timeline_name)}
    signed = set()
    for name in names:
        identity = (comparison_identity(name) or name)
        if (is_comparison_timeline(identity, plan_finals,
                                   variant_names, master_timeline_name)
                and base_final_for(identity, plan_finals) in set(bases)
                and _signoff.signoff_for(project_folder, identity)
                is not None):
            signed.add(identity)
    plan = plan_collection(
        names, bases, plan_finals=plan_finals,
        variant_names=variant_names, master_name=master_timeline_name,
        rounds_by_name=rounds_by_name, just_landed=just,
        signed_identities=signed, held_names=set(held))
    report["kept"] = plan["kept"]
    report["planned_retire"] = [entry["name"]
                                for entry in plan["retire"]]

    # The retire half runs first, exactly as `promote_staged_reels`
    # retires reel backups before collecting: the collection below is
    # planned off a FRESH read, so the generation just retired takes
    # part in the retention order rather than standing outside it.
    if plan["retire"]:
        try:
            current = rounds[-1]["round"] if rounds else 1
        except (TypeError, KeyError, IndexError):
            current = 1
        retiring = {entry["name"]: entry for entry in plan["retire"]}
        try:
            outcome = retire.retire_timelines(
                project, pool, _objects_for(project, list(retiring)),
                {name: retire.retiring_round(rounds, name, current)
                 for name in retiring})
            report["retired"] = outcome["archived"]
            report["unfiled"] = outcome["unfiled"]
        except Exception as failed:                     # noqa: BLE001
            report["refused"] = (
                f"retirement refused ({failed}) - the reels are "
                f"promoted; the superseded comparisons are still in "
                f"the project under their live names and nothing was "
                f"deleted.")
        names = [name for name in _live_names(project)
                 if name not in protected_names]
        rounds_by_name = landed_rounds(rounds, names)
        replan = plan_collection(
            names, bases, plan_finals=plan_finals,
            variant_names=variant_names,
            master_name=master_timeline_name,
            rounds_by_name=rounds_by_name, just_landed=just,
            signed_identities=signed, held_names=set(held))
        # The replan's live entries duplicate the plan's; only its
        # archived verdicts are new (the generation just retired takes
        # part in the retention order now).
        report["kept"].extend(
            entry for entry in replan["kept"]
            if bins.is_archived_timeline(entry["name"]))
        report["planned_collect"] = list(replan["collect"])
    else:
        replan = plan
        report["planned_collect"] = list(plan["collect"])

    if replan["collect"]:
        try:
            record = _collect_archived(project, pool,
                                       replan["collect"])
            report["collect"] = replan["collect"]
            report["collected"] = record["collected"]
            report["kept"].extend(record["kept"])
        except Exception as failed:                     # noqa: BLE001
            refused = (f"collection refused ({failed}) - nothing was "
                       f"deleted.")
            report["refused"] = ((report["refused"] + " ") if
                                 report["refused"] else "") + refused
    return report


def _objects_for(project, names) -> dict:
    """`{name: timeline object}` for exact names, by exact name."""
    from library.tools.reel_build import timelines_to_replace

    return {timeline.GetName(): timeline
            for timeline in timelines_to_replace(project, set(names))}


def _collect_archived(project, pool, planned) -> dict:
    """Delete the archived generations the retention bound releases.

    The ONLY deletion this module performs. `planned` must name
    ARCHIVED timelines only - a live name here is a planner defect,
    and it refuses before anything is read further. Then
    `assert_deletion_scope` is asked of the list about to be deleted
    against exactly those names, so a wrong selection refuses rather
    than widening (`reel_build.assert_deletion_scope`, the captain's
    2026-09-06 ruling). Names are read BEFORE the delete: a deleted
    timeline object answers `GetName()` with None
    (`reel_retirement.collect_superseded` carries the same note).
    """
    from library.tools.reel_build import assert_deletion_scope, timelines_to_replace

    live = [name for name in (planned or ())
            if not bins.is_archived_timeline(name)]
    if live:
        raise ComparisonRefused(
            f"REFUSING to collect: {live} are not archived timelines. "
            f"This rule deletes retired generations only - a live "
            f"comparison is retired first, never deleted. Nothing was "
            f"deleted.")
    targets = timelines_to_replace(project, set(planned or ()))
    assert_deletion_scope(targets, set(planned or ()))
    record: dict = {"collected": [], "kept": []}
    if targets:
        # Named BEFORE the delete. A deleted timeline object answers
        # `GetName()` with None, so reading the report off it afterwards
        # crashes the collection it was reporting on
        # (`reel_retirement.collect_superseded` carries the same note).
        collected = sorted(name for name in
                           (t.GetName() for t in targets) if name)
        # Judged by what Resolve RETURNS, never by `hasattr` (AGENTS.md
        # 5): a falsy delete RAISES rather than reporting the names
        # collected. Measured 2026-09-14 against a real Resolve
        # (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`) - the report
        # listed a timeline under `collected` while the census still
        # showed it present. The refused note is already a handled,
        # rendered outcome (the driver catches it and the run
        # continues - the reels are promoted), and the generation stays
        # archived, so the next build plans it again. The same shape
        # PR 1149 landed in `reel_retirement.collect_superseded`,
        # followed verbatim rather than re-answered.
        if not pool.DeleteTimelines(targets):
            raise ComparisonRefused(
                f"Resolve declined to delete {len(collected)} archived "
                f"generation(s) ({', '.join(collected)}). They are still "
                f"in the project under their archived names - nothing "
                f"was reported collected.")
        record["collected"] = collected
    return record


def render(report: dict) -> str:
    """What comparison retirement did, in operator sentences."""
    lines = []
    bases = (report or {}).get("bases") or []
    if bases:
        lines.append(
            f"  Comparison lifecycle for {len(bases)} touched reel(s) "
            f"(retention: {RETAINED_COMPARISONS} archived comparison "
            f"per reel):")
    for final in sorted((report or {}).get("retired") or {}):
        lines.append(f"    {final} -> "
                     f"{(report['retired'])[final]}")
    for name in (report or {}).get("unfiled") or ():
        lines.append(
            f"  {name!r} was renamed but not moved into "
            f"{'/'.join(ARCHIVE_BIN)}; the organiser files it on the "
            f"next build.")
    collected = (report or {}).get("collected") or []
    if collected:
        lines.append(
            f"  Collected {len(collected)} superseded comparison(s): "
            f"{', '.join(collected)}")
    for kept in (report or {}).get("kept") or []:
        lines.append(f"  Kept {kept['name']!r} - {kept['why']}")
    for note in (report or {}).get("notes") or ():
        lines.append(f"  {note}")
    refused = (report or {}).get("refused") or ""
    if refused:
        lines.append(f"  REFUSED: {refused}")
    if not lines:
        lines.append("  Nothing to bound: no comparison timelines stood "
                     "beside the promoted reels.")
    return "\n".join(lines)
