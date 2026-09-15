"""Two versions of a reel alive at once, compared, and one CHOSEN.

The captain, 2026-09-12, answering
`data/vep-can-it-hold-up-in-a-real-editing-workflow` §7: two versions of
a reel alive at once should be a ROUTINE workflow - expose
`build_reel_variants` and `timeline_variants`, widen the spec past
seam-only, retire rather than delete on promotion. Retirement landed in
part one (`reel_retirement`); this is the half that makes the other two
mean something.

What was missing was not the building. `build_reel_variants` has put N
treatments of one reel side by side, atomically, with namespaced
captions and a `watch` note per variant, since it landed - and had no
caller. What was missing was everything AFTER the build:

- the two versions could only be compared BY EYE, because nothing kept
  what either one contained;
- CHOOSING one was not an act. There was no command, no consequence and
  no record: the loser sat in the bin under its comparison name until
  somebody deleted it by hand, and the round said nothing about a
  decision having been made at all.

So this module holds three things, and they are in the order a captain
uses them.

1. What is ALIVE
----------------
`record_build` stores, per built variant, its timeline name, its suffix,
what it declares, its `watch` note and the ROW SNAPSHOT
(`reel_read.rows_of`) of what was placed. Written by
`reel_build.build_reel_variants` at the moment each variant passes
conformance, into `pipeline_output/review/reel_variant_builds.json` -
beside the rounds, the sign-offs and the specs, on the version-control
allow-list.

The rows are the same payload `round_version` stores per round, for the
same reason: a few kilobytes of JSON outlives the timeline it describes,
so a comparison stays answerable after the loser has been archived and
collected. That asymmetry is what lets the lifecycle END.

2. COMPARED, not just watched
-----------------------------
`compare` runs two stored snapshots through `round_diff.diff_reel` -
which is `reel_replace_guard.diff_rows` with the added rows put back -
and answers "what actually differs between the J-cut and the reaction
cutaway" off disk, with Resolve closed, in milliseconds. The captain
still watches both; this says where to look.

3. CHOOSING, as an act with a consequence
-----------------------------------------
`choose` is the whole point:

- the CHOSEN variant is renamed to the reel's own name. It becomes the
  reel - not a copy of it, the same timeline;
- the version that HELD that name is retired to `05 - Reels/Archive`
  exactly as a promotion retires it (`reel_retirement.retire_timelines`),
  because from the reel's point of view this IS a promotion;
- every UNCHOSEN variant is renamed into the same archive with the same
  `(archived round NNN)` suffix, so no comparison timeline is ever left
  in a bin the captain reviews under a name that reads like a
  deliverable;
- the ROUND records which suffix was chosen, what it was chosen over,
  and WHY. `--why` is required: a choice with no reason is not a
  decision anybody can read six weeks later.

The clutter bound, stated
-------------------------
The captain has asked four separate times for leftover timelines to be
cleared, and a variant workflow is exactly how clutter arrives - two
timelines per reel per comparison, for ever. So the bound is stated
here and it is the same one `reel_retirement` takes, on the same axis:

    at most `RETAINED_UNCHOSEN` (1) unchosen variant survives PER REEL.

Per REEL, deliberately, not per variant identity. `reel_retirement`
groups archived generations by the reel name they parse back to, and
every new suffix is a new identity - so a per-identity bound would keep
one archived timeline per suffix the project ever tried, which grows
with the number of comparisons and recreates the complaint. Grouping the
reel's variants together means the archive holds, per reel, the previous
cut and the most recent runner-up, and nothing else. Bounded by how many
reels the project has; not by how long it runs, nor by how often the
captain compares.

The reel's OWN retired generations are bounded by the same act, through
`reel_retirement.plan_collection` rather than a second rule here: a
choice retires the version that held the reel's name exactly as a
promotion does, so without asking that bound the archive would grow one
generation per choice - this bound arriving by the other door. Measured
2026-09-12 against a real Resolve, which is how it was found.

Two exceptions, both inherited from retirement and for its reasons: a
generation carrying a captain SIGN-OFF is never collected, and every
retained one is NAMED in the report on every choice. The sign-off case
is the one thing here that is not bounded by a constant - it is bounded
by how many sign-offs the captain grants, each explicit, each
withdrawable, and each named on every choice.

Variants alive at once are fine. Variants accumulating are not. What
bounds them is this constant and the fact that `choose` runs the
collection every time.

A variant and the sign-off
--------------------------
A variant IS built, and the captain's ruling was that a sign-off
attaches to a BUILT reel - so a variant can be signed off, and
`reel_signoff` needs no change to allow it: `feedback_ledger
.base_reel_name` strips the build's own container suffixes and leaves
everything else alone, so `Reel 09 - ... (reaction-cutaway)` is its own
sign-off identity already.

What it MEANS is decided here:

- choosing a variant over a SIGNED-OFF incumbent refuses unless the
  choice declares it, in `reel_signoff`'s own declare-then-proceed shape
  and with its own message - the chosen variant is taking that reel's
  name, which is precisely the replacement a sign-off exists to catch;
- choosing a variant that itself carries a sign-off CARRIES the sign-off
  onto the reel's name. The cut the captain approved did not change; its
  container did, and a sign-off that lapsed because a timeline was
  renamed would be an approval the machine withdrew;
- a signed-off variant that LOSES keeps its sign-off and is never
  collected. It is archived, which is where the version that was not
  chosen belongs, and the approval stays on it because the captain gave
  it.

`tests/test_variant_choice.py`.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from library.tools import reel_retirement as retire

BUILDS_FILENAME = "reel_variant_builds.json"
BUILDS_FORMAT = "reel_variant_builds/1"

RETAINED_UNCHOSEN = 1
"""How many unchosen variant generations of one REEL survive a choice.

One: the runner-up of the most recent comparison, which is the version
somebody asks to see again. This is a RETENTION bound, not a timeout -
nothing expires with the clock - and it is the reason a routine variant
workflow cannot become the clutter the captain has complained about.
"""


class ChoiceRefused(RuntimeError):
    """The choice declined to proceed, and says exactly why."""


# ── What is alive ────────────────────────────────────────────────

def builds_path_for(project_folder) -> str:
    """`pipeline_output/review/reel_variant_builds.json`."""
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        BUILDS_FILENAME)


def read_builds(project_folder) -> dict:
    """The built-variant record, or an empty one.

    Unreadable is EMPTY here rather than a raise, because this record
    is written inside a build: a malformed one must not take a
    conformance-clean variant down with it. Every reader below states
    what it found, so an empty answer cannot be mistaken for "no
    variants were built".
    """
    path = builds_path_for(project_folder)
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return {"format": BUILDS_FORMAT, "builds": {}}
    if not isinstance(document, dict):
        return {"format": BUILDS_FORMAT, "builds": {}}
    document.setdefault("format", BUILDS_FORMAT)
    document.setdefault("builds", {})
    return document


def _write(project_folder, document: Mapping) -> str:
    path = builds_path_for(project_folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = json.dumps(dict(document), indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    # The same atomic write `reel_signoff._write` makes, beside it in
    # the same directory: a half-written record of what is alive would
    # be read as a variant that is not there.
    handle, staged = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=".variant-builds-",
        suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(payload)
        os.replace(staged, path)
    except BaseException:
        if os.path.exists(staged):
            os.unlink(staged)
        raise
    return path


def record_build(project_folder, reel_number: int, base_final: str,
                 suffix: str, rows: Mapping, watch: str = "",
                 declares: Sequence[str] = (), built_at: str = "") -> dict:
    """Record one built variant: what it is and what it contains.

    Keyed by the variant's TIMELINE NAME, which is the only identity
    Resolve and the captain share. Re-recording the same name replaces
    the entry: a rebuilt variant is the same variant with a new
    picture, and two rows snapshots for one live timeline is exactly the
    disagreement AGENTS.md 10.1 keeps catching.
    """
    from library.tools.timeline_variants import variant_timeline_name

    final = variant_timeline_name(str(base_final), str(suffix))
    document = read_builds(project_folder)
    document["builds"][final] = {
        "reel": int(reel_number),
        "base_final": str(base_final),
        "suffix": str(suffix),
        "timeline": final,
        "watch": str(watch or ""),
        "declares": sorted(str(name) for name in (declares or ())),
        "built_at": str(built_at
                        or datetime.now(timezone.utc).isoformat()),
        "rows": dict(rows or {}),
    }
    document["format"] = BUILDS_FORMAT
    _write(project_folder, document)
    return document["builds"][final]


def forget_builds(project_folder, timelines: Sequence[str]) -> list:
    """Drop build records for timelines that are no longer alive.

    Called by `choose` once the renames have landed: a record that
    still names `Reel 09 - ... (reaction-cutaway)` after that timeline
    became the reel (or went to the archive) claims something alive
    that is not.
    """
    document = read_builds(project_folder)
    gone = [name for name in (timelines or ())
            if document["builds"].pop(name, None) is not None]
    if gone:
        _write(project_folder, document)
    return gone


def builds_for_reel(project_folder, reel_number: int) -> list:
    """Every recorded variant build of one reel, suffix-ordered."""
    document = read_builds(project_folder)
    entries = [entry for entry in (document.get("builds") or {}).values()
               if isinstance(entry, dict)
               and entry.get("reel") == int(reel_number)]
    return sorted(entries, key=lambda entry: str(entry.get("suffix", "")))


def build_for(project_folder, reel_number: int, suffix: str):
    """One reel's recorded build carrying this suffix, or None."""
    for entry in builds_for_reel(project_folder, reel_number):
        if entry.get("suffix") == suffix:
            return entry
    return None


# ── Compared, off disk ───────────────────────────────────────────

def compare(project_folder, reel_number: int, earlier_suffix: str,
            later_suffix: str) -> dict:
    """What differs between two built variants of one reel.

    Both must have been built and recorded; a missing side RAISES
    rather than diffing against nothing, for `round_diff.diff_rounds`'s
    reason - an empty side reads exactly like a version that contained
    nothing, and the two must never be confused.
    """
    from library.tools import round_diff

    entries = {}
    for suffix in (earlier_suffix, later_suffix):
        entry = build_for(project_folder, reel_number, suffix)
        if entry is None:
            known = [str(e.get("suffix", ""))
                     for e in builds_for_reel(project_folder, reel_number)]
            raise ChoiceRefused(
                f"reel {reel_number} has no recorded build of variant "
                f"{suffix!r}. Built and recorded: {known or 'none'}. "
                f"Build it before comparing it - a comparison against "
                f"nothing reads like a comparison against an empty cut.")
        entries[suffix] = entry
    diff = round_diff.diff_reel(entries[earlier_suffix].get("rows") or {},
                                entries[later_suffix].get("rows") or {})
    diff["reel"] = int(reel_number)
    diff["earlier"] = entries[earlier_suffix]["timeline"]
    diff["later"] = entries[later_suffix]["timeline"]
    diff["earlier_watch"] = entries[earlier_suffix].get("watch", "")
    diff["later_watch"] = entries[later_suffix].get("watch", "")
    return diff


def render_comparison(diff: Mapping, show_items: int = 3) -> str:
    """The comparison, in the sentences a captain reads before watching.

    Per-row, through `round_diff`'s own `_row_line` - the same spelling
    a round diff uses, so two versions of a reel and two rounds of a
    reel read the same way rather than two vocabularies for one
    measurement.
    """
    from library.tools import round_diff

    lines = [f"── Reel {diff.get('reel')}: two versions ──",
             f"  A: {diff.get('earlier')}"
             + (f"  - watch: {diff['earlier_watch']}"
                if diff.get("earlier_watch") else ""),
             f"  B: {diff.get('later')}"
             + (f"  - watch: {diff['later_watch']}"
                if diff.get("later_watch") else "")]
    rerendered = diff.get("rerendered") or []
    changed = diff.get("changed") or []
    if not changed:
        lines.append("  Nothing differs between them"
                     + (f" ({len(rerendered)} row(s) re-rendered at "
                        f"identical spans)" if rerendered else "")
                     + " - two builds of one treatment, not a "
                       "comparison.")
        return "\n".join(lines)
    lines.append(f"  {len(changed)} row(s) differ:")
    for row in rerendered:
        lines.append(f"    {row['key']}: {row['later_count']} item(s) "
                     f"re-rendered at identical spans - nothing moved")
    for row in changed:
        lines.append("  " + round_diff.row_line(row).strip())
        for item in (row.get("gone") or [])[:show_items]:
            lines.append(f"      only in A: {item['name']!r} "
                         f"@{item['start']}..{item['end']} "
                         f"({item['duration']}f)")
        if len(row.get("gone") or []) > show_items:
            lines.append(f"      ... and {len(row['gone']) - show_items} "
                         f"more only in A")
        for item in (row.get("gained") or [])[:show_items]:
            lines.append(f"      only in B: {item['name']!r} "
                         f"@{item['start']}..{item['end']} "
                         f"({item['duration']}f)")
        if len(row.get("gained") or []) > show_items:
            lines.append(f"      ... and "
                         f"{len(row['gained']) - show_items} more only "
                         f"in B")
    return "\n".join(lines)


# ── The choice, planned purely ───────────────────────────────────

def variant_generations(existing_names, variant_names) -> list:
    """Archived generations belonging to a reel's VARIANTS, newest first.

    Grouped across every variant identity of the reel rather than per
    suffix, which is what makes the retention bound a bound on the reel
    (see the module docstring). `variant_names` is the reel's declared
    variant timeline names - exact through the spec record, because no
    syntactic rule tells `(final)` from `(reaction-cutaway)`.
    """
    wanted = set(variant_names or ())
    found = []
    for name in existing_names or ():
        reel, number = retire.parse_archived(name)
        if reel in wanted:
            found.append((number, name))
    return [name for _number, name in sorted(found, reverse=True)]


def plan_choice(existing_names, base_final: str, variant_names,
                chosen: str, losers, round_number: int,
                signed_off_reels=()) -> dict:
    """Every rename and every deletion this choice will perform.

    Pure: handed the names that exist, it answers off them, so the whole
    policy is testable with no Resolve and the Resolve half below holds
    none of it. `chosen` and `losers` are variant TIMELINE names.

    Returns `{"promote", "retire", "archive", "collect", "kept"}`:
    `promote` is `(chosen, base_final)`; `retire` is the incumbent
    holding the reel's name, if one is there; `archive` maps each loser
    to its archived name; `collect` is what the retention bound
    releases; `kept` says why each survivor survived.
    """
    existing = list(existing_names or ())
    live = set(existing)
    signed = {str(reel) for reel in (signed_off_reels or ())}
    if chosen not in live:
        raise ChoiceRefused(
            f"{chosen!r} is not a timeline in this project, so it "
            f"cannot become {base_final!r}. Build the variant first; a "
            f"choice never creates the thing it chooses.")
    taken = set(existing)
    plan = {"promote": (chosen, base_final), "retire": None,
            "archive": {}, "collect": [], "kept": []}
    if base_final in live:
        name = retire.archived_name(base_final, round_number, taken)
        taken.add(name)
        plan["retire"] = (base_final, name)
    for loser in sorted(set(losers or ())):
        if loser not in live or loser == chosen:
            continue
        name = retire.archived_name(loser, round_number, taken)
        taken.add(name)
        plan["archive"][loser] = name

    # The retention bound, asked of the names that will exist AFTER the
    # renames: the runner-up just archived is a generation like any
    # other, and a bound that exempted it would keep one more every
    # time.
    after = (live - {chosen, base_final} - set(plan["archive"]))
    after |= set(plan["archive"].values())
    after.add(base_final)
    if plan["retire"]:
        after.add(plan["retire"][1])
    for position, name in enumerate(
            variant_generations(after, variant_names)):
        if position < RETAINED_UNCHOSEN:
            plan["kept"].append({
                "name": name,
                "why": f"the most recent unchosen variant of "
                       f"{base_final!r} - the runner-up of this "
                       f"comparison"})
            continue
        parsed, _number = retire.parse_archived(name)
        if parsed in signed:
            plan["kept"].append({
                "name": name,
                "why": f"{parsed!r} carries a captain sign-off, and "
                       f"the cut they approved is never collected"})
            continue
        plan["collect"].append(name)

    # And the REEL's own retired generations, through the bound that
    # already owns them (`reel_retirement.plan_collection`) rather than
    # a second rule here. A choice retires the version that held the
    # name exactly as a promotion does, so without this a project that
    # chooses repeatedly grows one archived generation of the reel per
    # choice - the accumulation this whole module is bounded against,
    # arriving by the other door. Measured 2026-09-12 against a real
    # Resolve: two choices left `(archived round 001)` AND
    # `(archived round 001.2)` standing.
    incumbent = retire.plan_collection(sorted(after), [base_final], signed)
    plan["collect"].extend(incumbent["collect"])
    plan["kept"].extend(incumbent["kept"])
    return plan


# ── The Resolve half ─────────────────────────────────────────────

def _timelines_by_name(project) -> dict:
    found = {}
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline:
            found[timeline.GetName()] = timeline
    return found


def choose(project, pool, project_folder, reel_number: int,
           base_final: str, chosen_suffix: str, why: str,
           variant_names=(), supersede_declared=(),
           promoted_at: str | None = None) -> dict:
    """Make one variant the reel. The other goes to the archive.

    The order is the promotion's order and for its reasons: the
    incumbent is retired FIRST (so the reel's name is free), the chosen
    variant takes the name SECOND, and the sign-off bookkeeping happens
    LAST - a choice that refuses partway must never have already retired
    an approval it did not replace.

    `why` is required. `variant_names` is the reel's declared variant
    timeline names (`timeline_variants.declared_variant_names`).
    """
    from library.tools import reel_signoff as signoff
    from library.tools import round_version as rounds
    from library.tools.timeline_variants import variant_timeline_name

    if not str(why or "").strip():
        raise ChoiceRefused(
            "a choice with no reason is not a decision anybody can read "
            "later - say why this treatment won.")
    chosen = variant_timeline_name(str(base_final), str(chosen_suffix))
    live = _timelines_by_name(project)

    # The sign-off on the INCUMBENT: the chosen variant is taking that
    # reel's name, which is exactly the replacement a sign-off exists
    # to catch. Declare-then-proceed, in `reel_signoff`'s own words.
    incumbent_signoff = None
    if base_final in live:
        incumbent_signoff = signoff.assert_declared(
            project_folder, base_final, supersede_declared,
            command=f"variant <project> choose {int(reel_number)} "
                    f"'{chosen_suffix}' --why '...'")

    recorded = rounds.discover(project_folder)
    current_round = recorded[-1]["round"] if recorded else 1
    losers = [entry["timeline"]
              for entry in builds_for_reel(project_folder, reel_number)
              if entry.get("timeline") != chosen]
    plan = plan_choice(sorted(live), base_final, variant_names, chosen,
                       losers, retire.retiring_round(
                           recorded, base_final, current_round),
                       set(signoff.signed_off(project_folder)))

    report = {"reel": int(reel_number), "chosen": chosen,
              "base_final": base_final, "why": str(why),
              "retired": None, "archived": {}, "unfiled": [],
              "collected": [], "kept": plan["kept"],
              "round": current_round}

    if plan["retire"]:
        outcome = retire.retire_timelines(
            project, pool, {base_final: live[base_final]},
            {base_final: retire.parse_archived(plan["retire"][1])[1]})
        report["retired"] = outcome["archived"].get(base_final)
        report["unfiled"].extend(outcome["unfiled"])

    if not live[chosen].SetName(base_final):
        raise ChoiceRefused(
            f"Resolve would not rename {chosen!r} to {base_final!r}. "
            f"Nothing was deleted: the version that held the reel's "
            f"name is safe under {report['retired']!r} and the chosen "
            f"variant is still under its own. Rename it in Resolve and "
            f"re-run.")

    losing = {name: live[name] for name in plan["archive"] if name in live}
    if losing:
        outcome = retire.retire_timelines(
            project, pool, losing,
            {name: retire.parse_archived(plan["archive"][name])[1]
             for name in losing})
        report["archived"] = outcome["archived"]
        report["unfiled"].extend(outcome["unfiled"])

    after = set(_timelines_by_name(project))
    releasable = [name for name in plan["collect"] if name in after]
    if releasable:
        from library.tools.reel_build import assert_deletion_scope, timelines_to_replace
        targets = timelines_to_replace(project, set(releasable))
        assert_deletion_scope(targets, set(releasable))
        if targets:
            # Named BEFORE the delete: a deleted timeline object answers
            # `GetName()` with None (`reel_retirement.collect_superseded`
            # carries the same note and the same fix).
            collected = sorted(name for name in
                               (t.GetName() for t in targets) if name)
            # Judged by what Resolve RETURNS, never by `hasattr`
            # (AGENTS.md 5): a falsy delete RAISES rather than reporting
            # the names collected - the same defect PR 1149 fixed in
            # `reel_retirement.collect_superseded`, measured 2026-09-14
            # against a real Resolve
            # (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`). Refusal
            # for that PR's reason, established of THIS caller rather
            # than assumed from it: the sole production caller
            # (`manage_project.py variant choose`) already wraps the
            # choice in a handled refusal path (`REFUSED: ...`, exit 1).
            # The choice's renames already landed; only the retention
            # cleanup did not, and the next choice plans the
            # still-present generation again.
            if not pool.DeleteTimelines(targets):
                raise ChoiceRefused(
                    f"Resolve declined to delete {len(collected)} "
                    f"superseded generation(s) ({', '.join(collected)}). "
                    f"They are still in the project under their archived "
                    f"names - nothing was reported collected. The "
                    f"choice's renames already landed; only the "
                    f"retention cleanup did not, and the next choice "
                    f"will plan it again.")
            report["collected"] = collected

    # The record follows the picture. A sign-off the CHOSEN variant
    # carried moves onto the reel's name - the cut did not change, its
    # container did - and the incumbent's declared one is superseded,
    # never deleted.
    carried = signoff.signoff_for(project_folder, chosen)
    if incumbent_signoff is not None:
        signoff.supersede(project_folder, base_final,
                          round_number=current_round)
        report["superseded_signoff"] = incumbent_signoff
    if carried is not None:
        signoff.withdraw(project_folder, chosen,
                         why=f"chosen as {base_final!r}; the sign-off "
                             f"follows the cut onto the reel's name")
        signoff.sign_off(project_folder, base_final,
                         note=carried.get("note", ""),
                         by=carried.get("by", "captain"),
                         round_number=current_round)
        report["carried_signoff"] = carried

    # The rows of the cut that won, read off the build record BEFORE it
    # is forgotten - they were measured when the variant was built and
    # are the same snapshot a promotion stores, so the round diff can
    # compare the next round against this one with no Resolve.
    chosen_entry = build_for(project_folder, reel_number, chosen_suffix)
    chosen_rows = dict((chosen_entry or {}).get("rows") or {})
    forget_builds(project_folder, [chosen] + list(plan["archive"]))

    # The round records the CHOICE, not only the promotion: which
    # treatment won, what it won over, and why. Never fatal, for
    # `stamp_promotion`'s reason - a version record that fails a choice
    # already landed in Resolve is worse than no version record.
    try:
        from library.tools.plan_provenance import read_provenance
        try:
            provenance = read_provenance(
                os.path.join(str(project_folder), "pipeline_output",
                             "review"))
        except Exception:                                   # noqa: BLE001
            provenance = None
        stamped = rounds.stamp_promotion(
            project_folder, {base_final: chosen_rows}, provenance,
            promoted_at=promoted_at,
            choices={base_final: {
                "chosen": str(chosen_suffix),
                "chosen_timeline": chosen,
                "over": sorted(plan["archive"]),
                "why": str(why),
            }})
        report["round"] = stamped["round"]
    except Exception as stamp_failed:                       # noqa: BLE001
        report["round_not_stamped"] = f"{stamp_failed}"
    return report


def render(report: Mapping) -> str:
    """What the choice did, in the sentences an operator has to read."""
    lines = [(f"Reel {report.get('reel')}: chose "
              f"{report.get('chosen')!r} as "
              f"{report.get('base_final')!r}"),
             f"  why: {report.get('why')}"]
    if report.get("retired"):
        lines.append(f"  Retired the version that held the name -> "
                     f"{report['retired']}")
    for loser in sorted((report or {}).get("archived") or {}):
        lines.append(f"  Not chosen: {loser} -> "
                     f"{report['archived'][loser]}")
    for name in (report or {}).get("unfiled") or ():
        lines.append(f"  {name!r} was renamed but not moved into "
                     f"{'/'.join(retire.ARCHIVE_BIN)}; the organiser "
                     f"files it on the next build.")
    if report.get("collected"):
        lines.append(
            f"  Collected {len(report['collected'])} superseded "
            f"generation(s) (retention: {RETAINED_UNCHOSEN} unchosen "
            f"variant per reel): {', '.join(report['collected'])}")
    for kept in (report or {}).get("kept") or ():
        lines.append(f"  Kept {kept['name']!r} - {kept['why']}")
    if report.get("carried_signoff"):
        lines.append("  The sign-off on the chosen variant follows it "
                     "onto the reel's name.")
    if report.get("superseded_signoff"):
        lines.append("  The sign-off on the version it replaced is "
                     "recorded as superseded, not deleted.")
    if report.get("round_not_stamped"):
        lines.append(f"  Round NOT stamped ({report['round_not_stamped']}) "
                     f"- the choice landed and is unaffected, but it is "
                     f"not in the version record.")
    else:
        lines.append(f"  Round {report.get('round')} records the choice.")
    return "\n".join(lines)
