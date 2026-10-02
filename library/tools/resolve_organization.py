"""Where a Resolve project's timelines and clips BELONG, and why.

`pool.CreateEmptyTimeline` and `pool.ImportMedia` put what they make into
whatever bin is CURRENT, which is wherever the operator last clicked.
This module decides where each artefact belongs instead.  Bin paths are
owned by `resolve_bin_layout`; the `BIN_*` names here are aliases of it.

The rule has one dimension, and it is a MEASUREMENT
---------------------------------------------------
Every artefact is filed by facts read off the project, never by parsing
its name (`plan_organization`):

1. A **timeline** goes to `05 - Reels/<state>` (below) - but only out of
   root or out of one of the pipeline's own bins.  A timeline sitting in
   any other bin is where a human put it and stays there (`TIMELINE_BINS`).
   A proof timeline (`SOP Proof_...`) goes to `05 - Reels/Proof`
   (`BIN_PROOF`), never among the captain's reels.  The project's own
   declared master timeline is NEVER moved - AGENTS.md 5.
2. A clip whose file lives **under the project's own directory** is
   something this pipeline generated (`is_generated`).  It files under
   the timeline that PLACES it, nested under the render bin its KIND
   belongs to (`06 - Subtitle renders` for subtitle segments, `07 -
   Motion graphics` for motion-graphics, timed-text and carrier renders
   - a path fact read off `project_layout.Area`, never a name parse);
   under that bin's SHARED leaf (`BIN_SHARED`) when several timelines
   place it; and under `Not placed on any timeline` (`BIN_UNPLACED`)
   when none does.  Nothing sits at a render bin's root.
   A generated clip no render area owns - a frame overlay, a freeze hold
   (`resolve_bin_layout.is_render_file` is false) - files under the
   captain's `03 - Assets` (`BIN_ASSETS`) however many timelines place it.
3. Anything else is material that came from outside, and files under
   `Source footage` (`BIN_SOURCE`).

"Which timeline places it" is read from the timelines themselves, not
from the filename: a name is a claim and the timeline is the fact.

Per-reel bins are FLAT under the render bin rather than nested under the
reel's state: state belongs to the timeline, and a captions tree that
mirrored it would leave an empty bin behind on every plan change, which
nothing here may delete.

The bin tree derives from reality, not from build history
---------------------------------------------------------
A per-reel leaf under a render bin whose name is NO live timeline's name,
exactly, is dead whether or not it still holds files, and
`plan_dead_render_bins` plans its retirement with its contents - only
when every clip in it is proven unplaced and pipeline-generated, no
timeline sits in it, and no sub-bin would be taken with it.  It returns
`(retirements, declined)`, so a bin it kept is reported with why.  A leaf
named for a timeline that still exists stays, whatever that timeline's
state.  The `Not placed on any timeline` and shared leaves are canonical
destinations and are never retired here.  `plan_retirements` plans the
legacy shells that are provably empty, deepest first.

CURRENT is the plan, not the last build
---------------------------------------
A reel's state (`reel_state`) comes from the live PROPOSALS file
(`reel_proposals_v2.json`, read through
`plan_provenance.current_plan_names`), not from which reels a build
happened to place:

- `CURRENT` - the live plan's approved moments name it.
- `EARLIER` - an ARCHIVED plan names it, and the live plan does not.
- `UNRECORDED` - no plan on disk names it.

`plan_provenance.json` is not the source of state: after a single-reel
build it names only that reel.  When the live plan cannot be read at all,
filing falls back to the provenance record's built reels rather than
mass-demoting - an unreadable plan is "nothing here can say".

Exact names only, as for addressing a Resolve project or timeline
(AGENTS.md 5).  A plan name plus a hand-typed suffix (`" (fragment
fix)"`) is UNRECORDED, not EARLIER.  `UNRECORDED` is a real answer and
not a failure: it is where timelines no plan names are kept rather than
deleted or misfiled, and emptying it is the captain's call (a plan
record naming them), never the sweeper's.

Nothing here deletes
--------------------
There is no delete call in this module or in the executor that drives
it, and `tests/test_resolve_organization.py` asserts that of both files.
A reel the live plan no longer names is MOVED and RELABELLED, never
removed (captain, 2026-09-06: *"a refusal is cheap and a deleted
timeline is not"*).

Every move is journalled with the bin it came FROM, so the whole thing
reverses.  A revert cannot undo a bin that was CREATED - undoing a create
means deleting - so it reports those by name and leaves them, the same
bargain `project_migration.revert_from_manifest` strikes with copies.

`findings` reports what is not filed the way its evidence says, and
`assert_organized` raises `OrganizationError` on any.  This module holds
no Resolve calls and does no I/O, so every rule in it is testable without
the application running; `library/tools/execution/organise_media_pool.py`
is the half that talks to Resolve.

`tests/test_resolve_organization.py`.

The measurements and rulings behind these rules (the field-test
project's census, the accumulated bin tree, the single-reel build that
demoted eight reels): docs/evidence/resolve_organization.md.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from library.tools import resolve_bin_layout as bins
from library.tools import staging_holds as holds

BIN_REELS = bins.REELS_BIN
"""Alias, not a declaration: `resolve_bin_layout` owns every bin path
the way `timeline_layout` owns every track name, and this module asks
it. The numbered scheme survives because it is bound to
`project_layout.Area`; the per-timeline filing this module computed
survives as sub-bins under it."""

BIN_SUBTITLES = bins.SUBTITLES_BIN
"""Alias - see above."""

BIN_SOURCE = bins.SOURCE_BIN
"""Alias - see above. The captain's own name, kept under it."""

BIN_UNPLACED = bins.UNPLACED_BIN
"""Alias - see above. A clip's placement fact, never a timeline's
state: UNRECORDED and "not placed" are different axes."""

BIN_MOTION_GRAPHICS = bins.MOTION_GRAPHICS_BIN
"""Alias - see above. The second render top whose depth-2 children are
per-timeline bins, and the second place a dead reel's leaf can stand."""

BIN_ASSETS = bins.ASSETS_BIN
"""Alias - see above. The captain's own artwork bin, and the home of a
generated clip no render area owns (a frame overlay, a freeze hold):
shared or not, such a clip is a production asset, not a per-reel
render, so placement never earns it a per-reel folder under a render
bin."""

BIN_SHARED = bins.SHARED_BIN
"""Alias - see above. The leaf a shared render files under, beside the
unplaced leaf: both name a placement state, never a reel."""

BIN_PROOF = bins.REELS_PROOF_BIN
"""Leaf name of the bin firstmate's proof timelines file under. An
alias like the rest."""

BIN_SCRATCH = bins.SCRATCH_BIN
"""The dedicated scratch bin staging timelines are created in and
filed back to. An alias like the rest: `resolve_bin_layout` owns it."""

CURRENT = "current"
EARLIER = "earlier"
UNRECORDED = "unrecorded"

STATES = (CURRENT, EARLIER, UNRECORDED)
"""Every state a reel timeline can be in. Complete."""

STATE_BINS = {
    CURRENT: bins.REEL_STATE_BINS[CURRENT],
    EARLIER: bins.REEL_STATE_BINS[EARLIER],
    UNRECORDED: bins.REEL_STATE_BINS[UNRECORDED],
}
"""Leaf names only - the parent (`05 - Reels`) lives in the layout
module, and destinations are built as `(BIN_REELS, STATE_BINS[state])`."""

LEGACY_REELS_BIN = "Reels"
"""The retired top-level reels bin. Timelines sitting directly in it,
or in its per-state sub-bins, are the pipeline's own lag and re-file
under `05 - Reels` - MOVED, never stranded beside the new empty bins.
Timelines in any OTHER sub-bin of it are where a human put them and
stay there, the same bargain `TIMELINE_BINS` always struck: the
captain's organisation wins wherever the two conflict."""

LEGACY_CLIP_BINS = frozenset(
    top for (top,) in bins.LEGACY_SUCCESSORS if top != LEGACY_REELS_BIN)
"""Retired top-level clip bins (`Reel subtitles`, `Subtitles`, `V1`,
...). A timeline sitting LOOSE in one is pipeline lag - timelines land
wherever the current folder happened to be, and no human organisation
is expressed by a timeline at the root of a clip bin - so the plan
files it out. Anything nested deeper stays: conservatism costs a move,
overreach costs the captain's sorting."""


def timeline_folder_managed(folder: Sequence[str]) -> bool:
    """May the plan file a timeline OUT of this bin? Root, the
    canonical state/proof/reels bins, the retired scheme's equivalents,
    and the roots of retired or canonical clip bins - a timeline loose
    there is where the current-folder lottery put it, not where a human
    filed it. Everything else (a review bin, an archive, the captain's
    own tiers) is theirs and stays."""
    folder = tuple(folder)
    if not folder:
        return True
    if folder in TIMELINE_BINS:
        return True
    return len(folder) == 1 and (
        folder[0] in LEGACY_CLIP_BINS
        or folder[0] in (BIN_SUBTITLES, bins.MOTION_GRAPHICS_BIN))

TIMELINE_BINS = frozenset(
    [(BIN_REELS, STATE_BINS[state]) for state in STATES]
    + [(BIN_REELS, BIN_PROOF), (BIN_REELS,)]
    + [(BIN_SCRATCH,)]
    + [(LEGACY_REELS_BIN, bins.REEL_STATE_BINS[state])
       for state in STATES]
    + [(LEGACY_REELS_BIN,)])
"""The only bins a reel timeline is ever filed OUT of: root (unfiled),
the canonical state and proof bins, the canonical reels root, the
scratch bin, and the retired scheme's equivalents of the state bins
and root.

A timeline sitting anywhere else - `Reels/Fully approved`, a review
bin, an archive - is where a HUMAN put it, and the plan emits no
verdict and no stamp for it: no move, no metadata write, no colour
change.  The pipeline's canonical layout is the pipeline's opinion;
the captain's organisation wins wherever the two conflict, because
taste and organisation are theirs.  Measured 2026-09-08: a build with
the default organise filed all 47 tiered reels back into these three
bins and left the captain's folders empty - the same items,
byte-identical, re-filed by a layout that had no word for them."""

STATE_CLIP_COLOURS = {
    CURRENT: "Green",
    EARLIER: "Brown",
    UNRECORDED: "Blue",
}
"""Which of Resolve's own clip colours each state paints.

Three of the sixteen names Resolve accepts.  This is a LEGEND, not a
judgement: it says which colour means which recorded state, and the
state is computed from the provenance record.  Nothing here reads a
colour back as evidence - `verify` compares against the record, so a
colour a human changed by hand is reported rather than believed.
"""

TAG_PREFIX = "vep:"
"""Every keyword this pipeline writes starts here, so a keyword the
captain typed themselves is never mistaken for one of ours - and so
`verify` can tell the two apart when it reconciles."""


class OrganizationError(RuntimeError):
    """The pool could not be organised, and nothing was half-done."""


@dataclass(frozen=True)
class Artefact:
    """One media-pool item, as READ off the project.

    `placed_by` is the set of timeline names that place this item,
    measured off the timelines rather than inferred from the name.
    `folder_path` is where it sits now, as a tuple of bin names starting
    at the root bin.
    """
    item_id: str
    name: str
    kind: str                      # "timeline" | "clip"
    file_path: str
    placed_by: tuple[str, ...]
    folder_path: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    """Where one artefact belongs, and the evidence that put it there."""
    item_id: str
    name: str
    kind: str
    destination: tuple[str, ...]
    why: str
    state: str | None = None

    @property
    def destination_path(self) -> str:
        return "/".join(self.destination)


@dataclass
class Plan:
    """What organising this project would do. Produced before anything runs."""
    root_bin: str
    verdicts: list[Verdict] = field(default_factory=list)
    moves: list[Verdict] = field(default_factory=list)
    folders: list[tuple[str, ...]] = field(default_factory=list)
    stamps: list[dict] = field(default_factory=list)
    left_alone: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "root_bin": self.root_bin,
            "folders": ["/".join(f) for f in self.folders],
            "moves": [
                {"item_id": v.item_id, "name": v.name, "kind": v.kind,
                 "to": v.destination_path, "why": v.why, "state": v.state}
                for v in self.moves],
            "stamps": list(self.stamps),
            "left_alone": [{"name": n, "why": w} for n, w in self.left_alone],
        }


def reel_state(timeline_name: str,
               current_reels: Iterable[str],
               archived_plan_names: Iterable[str]) -> tuple[str, str]:
    """One timeline's state, and the sentence that justifies it.

    `current_reels` is the CURRENT set - every reel the live plan names,
    resolved by the caller (`plan_provenance.current_plan_names`), NOT
    the reels the last build happened to place. EXACT membership, never
    a prefix.  See the module docstring for why the eight suffixed
    one-offs on the field test are UNRECORDED rather than EARLIER.
    """
    if timeline_name in set(current_reels):
        return CURRENT, "the live plan names it"
    if timeline_name in set(archived_plan_names):
        return EARLIER, ("an archived plan names it and the live plan "
                         "does not")
    return UNRECORDED, "no plan on disk names it"


def is_generated(file_path: str, project_root: str) -> bool:
    """Did this pipeline write the file behind this clip?

    A path fact, not a judgement: the project directory is where every
    step writes (`library/tools/project_layout.py`), so a clip whose file
    is inside it came from a run and a clip whose file is outside it came
    from somewhere else.
    """
    if not file_path or not project_root:
        return False
    root = os.path.abspath(project_root).rstrip(os.sep)
    return os.path.abspath(file_path).startswith(root + os.sep)


def _sole_placer(artefact: Artefact) -> str | None:
    return artefact.placed_by[0] if len(artefact.placed_by) == 1 else None


def plan_organization(artefacts: Sequence[Artefact],
                      project_root: str,
                      master_timeline_name: str,
                      current_reels: Iterable[str],
                      archived_plan_names: Iterable[str],
                      root_bin: str = "Master",
                      plan_hash: str = "",
                      built_at: str = "") -> Plan:
    """The whole filing decision, computed before anything is touched.

    `current_reels` is the CURRENT set - every reel the live plan names
    (`plan_provenance.current_plan_names`), not the reels the last build
    placed. Takes measurements and records; returns a `Plan`.  Calls
    nothing, writes nothing, and cannot leave the project half-organised
    because it never touches it.
    """
    if not master_timeline_name:
        raise OrganizationError(
            "no master timeline name was given. The master is the one "
            "timeline that must never be moved (AGENTS.md 5), and an "
            "organiser that cannot name it would file it like any other "
            "reel. `resolve.timeline_name` in project.yaml is where it "
            "comes from.")

    built = set(current_reels)
    archived = set(archived_plan_names)
    states: dict[str, tuple[str, str]] = {}
    for a in artefacts:
        if a.kind == "timeline" and a.name != master_timeline_name:
            states[a.name] = reel_state(a.name, built, archived)

    plan = Plan(root_bin=root_bin)
    wanted_folders: list[tuple[str, ...]] = []

    def want(path: tuple[str, ...]) -> tuple[str, ...]:
        for depth in range(1, len(path) + 1):
            prefix = path[:depth]
            if prefix not in wanted_folders:
                wanted_folders.append(prefix)
        return path

    for a in artefacts:
        if a.kind == "timeline":
            if a.name == master_timeline_name:
                plan.left_alone.append(
                    (a.name,
                     "the master timeline is never moved (AGENTS.md 5)"))
                continue
            if bins.is_proof_timeline(a.name):
                dest = (BIN_REELS, BIN_PROOF)
                why = ("firstmate's proof timeline, filed apart from the "
                       "captain's reels")
                state = None
            elif bins.is_archived_timeline(a.name):
                dest = (BIN_REELS, bins.REELS_ARCHIVE_BIN)
                why = ("a retired generation of a reel, filed in the "
                       "archive bin - kept for the round diff, renamed "
                       "so it cannot be mistaken for the live cut "
                       "(`reel_retirement`)")
                state = None
            elif bins.is_scratch_timeline(a.name):
                dest = (BIN_SCRATCH,)
                why = ("a staging or scratch timeline, filed in the "
                       "dedicated scratch bin outside the reels bins - "
                       "never the newest thing next to a deliverable")
                state = None
            else:
                state, why = states[a.name]
                dest = (BIN_REELS, STATE_BINS[state])
            if not timeline_folder_managed(a.folder_path):
                where = "/".join(a.folder_path) or "(root)"
                why_left = (
                    f"in {where} - a bin this pipeline does not manage, "
                    "so a human put it there and it stays (`TIMELINE_BINS`)")
                plan.left_alone.append((a.name, why_left))
                continue
            dest = want(dest)
            plan.verdicts.append(Verdict(
                a.item_id, a.name, a.kind, dest, why, state))
            if state is not None:
                plan.stamps.append(_stamp(a, state, plan_hash, built_at))
            continue

        if not is_generated(a.file_path, project_root):
            dest = want((BIN_SOURCE,))
            plan.verdicts.append(Verdict(
                a.item_id, a.name, a.kind, dest,
                "its file is outside the project directory, so this "
                "pipeline did not write it"))
            continue

        placer = _sole_placer(a)
        render_bin = bins.render_bin_for_file(a.file_path, project_root)
        if placer is None:
            if a.placed_by:
                # Shared, but still generated: a rebuild that reuses one
                # file on two timelines (a backup beside its final, two
                # reels over the same closing words) must not read as
                # outside material. A shared RENDER files under its
                # render bin's shared leaf - still ours, never as
                # outside material (rule 3 below is for files the
                # pipeline did not write) - while a shared PRODUCTION
                # asset (no render area owns its file) files under the
                # captain's assets bin rather than defaulting to
                # whichever category bin it was first imported into.
                # Either way the next organise after the extra placer
                # goes moves it under the one that stays: self-healing,
                # because the next organise re-reads the timelines.
                dest = want(bins.shared_bin_for_file(
                    a.file_path, project_root))
                if bins.is_render_file(a.file_path, project_root):
                    why = (f"{len(a.placed_by)} timelines place it, so it "
                           f"belongs to no single reel - it files under "
                           f"the shared leaf instead of sitting at the "
                           f"root beside the unfiled")
                else:
                    why = (f"{len(a.placed_by)} timelines place it, so it "
                           f"belongs to no single reel - and it is a "
                           f"production asset no render step wrote, so it "
                           f"files under the shared assets bin instead of "
                           f"the render bin it was imported into")
                plan.verdicts.append(Verdict(
                    a.item_id, a.name, a.kind, dest, why))
            else:
                dest = want(bins.unplaced_bin_for_file(
                    a.file_path, project_root))
                plan.verdicts.append(Verdict(
                    a.item_id, a.name, a.kind, dest,
                    "no timeline places it"))
            continue

        if not bins.is_render_file(a.file_path, project_root):
            # Placed, but not a render: a frame overlay, a freeze hold.
            # Placement never earns a production asset a per-reel folder
            # under a render bin - the category is a path fact, and the
            # assets bin has no per-reel leaves to stand under.
            dest = want((BIN_ASSETS,))
            plan.verdicts.append(Verdict(
                a.item_id, a.name, a.kind, dest,
                f"{placer!r} is the only timeline that places it, but it "
                f"is a production asset no render step wrote, so it "
                f"files under the shared assets bin"))
            continue

        dest = want((render_bin, placer))
        plan.verdicts.append(Verdict(
            a.item_id, a.name, a.kind, dest,
            f"{placer!r} is the only timeline that places it"))

    plan.folders = wanted_folders
    plan.moves = [v for v in plan.verdicts
                  if _current_of(artefacts, v.item_id) != v.destination]
    return plan


def _current_of(artefacts: Sequence[Artefact],
                item_id: str) -> tuple[str, ...]:
    for a in artefacts:
        if a.item_id == item_id:
            return a.folder_path
    return ()


def _stamp(a: Artefact, state: str, plan_hash: str, built_at: str) -> dict:
    """The identity written onto a reel timeline's own pool item.

    Resolve accepts metadata only under keys it already knows - an
    invented one returns False from `SetMetadata` and stores nothing,
    measured on 21.0.0b.28 - so the identity rides on `Keywords` and
    `Comments`, which it does accept.

    `Keywords` is what a machine reads back; `Comments` is the same fact
    in a sentence, because the captain reads the pool, not this file.
    """
    tags = [f"{TAG_PREFIX}reel", f"{TAG_PREFIX}state={state}"]
    if plan_hash and state == CURRENT:
        tags.append(f"{TAG_PREFIX}plan={plan_hash[:12]}")
    if built_at and state == CURRENT:
        tags.append(f"{TAG_PREFIX}built={built_at}")
    sentence = {
        CURRENT: "Built by the live plan"
                 + (f" {plan_hash[:12]}" if plan_hash else "")
                 + (f" at {built_at}" if built_at else "") + ".",
        EARLIER: "Built from a plan that has since been replaced. Kept, "
                 "not deleted.",
        UNRECORDED: "No plan on disk names this timeline. Kept as it is.",
    }[state]
    return {
        "item_id": a.item_id,
        "name": a.name,
        "state": state,
        "fields": {"Keywords": " ".join(tags), "Comments": sentence},
        "clip_color": STATE_CLIP_COLOURS[state],
    }


def findings(artefacts: Sequence[Artefact],
             plan: Plan,
             duplicate_bins: Sequence[str] = (),
             recorded_states: dict[str, str] | None = None,
             dead_paths: Sequence[Sequence[str]] = ()) -> list[dict]:
    """What is WRONG with the project as it stands. Empty means organised.

    Four things can be wrong, and each is read from a different place so
    that one instrument being broken cannot make the whole check vacuous:

    - an artefact is not in the bin its evidence puts it in,
    - two bins share a name (`AddSubFolder` happily makes a second one -
      measured, and it is why `ensure_folder` looks up before creating),
    - a timeline's recorded `Keywords` state disagrees with the state its
      provenance gives it,
    - an artefact the plan names is not in the pool at all.

    A finding is a dict rather than a raise so a caller can print all of
    them; `assert_organized` is the half that refuses.
    """
    out: list[dict] = []
    by_id = {a.item_id: a for a in artefacts}
    dead = {tuple(p) for p in dead_paths}

    for v in plan.verdicts:
        a = by_id.get(v.item_id)
        if a is None:
            out.append({"kind": "missing_item", "name": v.name,
                        "detail": f"{v.name!r} is in the plan and not in "
                                  f"the media pool"})
            continue
        if a.folder_path != v.destination:
            folder = tuple(a.folder_path)
            if any(folder[:len(d)] == d for d in dead):
                # Not misfiled: it sits in a dead bin and leaves the
                # pool with it.  The dead_render_bin finding carries
                # it; a misfiled line beside it would read as an
                # instruction to hand-move it to Unplaced first.
                continue
            out.append({
                "kind": "misfiled", "name": v.name,
                "detail": f"{v.name!r} is in "
                          f"{'/'.join(a.folder_path) or '(root)'} and its "
                          f"evidence puts it in {v.destination_path} - "
                          f"{v.why}"})

    for name in sorted(set(duplicate_bins)):
        out.append({"kind": "duplicate_bin", "name": name,
                    "detail": f"more than one bin is called {name!r}. "
                              f"Resolve allows it and AddSubFolder makes "
                              f"one on every call, so half the reels can "
                              f"end up in each."})

    for stamp in plan.stamps:
        recorded = (recorded_states or {}).get(stamp["item_id"])
        if recorded is None:
            continue
        if recorded != stamp["state"]:
            out.append({
                "kind": "stale_state", "name": stamp["name"],
                "detail": f"{stamp['name']!r} carries "
                          f"{TAG_PREFIX}state={recorded} and its plan "
                          f"record says {stamp['state']}"})
    return out


def assert_organized(found: Sequence[dict]) -> None:
    """Refuse when the project is not filed the way its evidence says."""
    if found:
        lines = "\n".join(f"  - [{f['kind']}] {f['detail']}" for f in found)
        raise OrganizationError(
            f"the Resolve project is not organised - "
            f"{len(found)} finding(s):\n{lines}")


def unplaced_report(artefacts: Sequence[Artefact],
                    project_root: str) -> dict:
    """What this pipeline has generated that no timeline plays.

    Filing them under `Not placed on any timeline` is housekeeping, not
    an answer: on the field test that bin holds 1,216 caption renders,
    and a bin nobody counts grows on every rebuild without ever saying
    so.  This is the count, said out loud, every time the pool is
    organised - the same bargain `render_qa` strikes with chroma and the
    mix (AGENTS.md 10.4): REPORT A NUMBER and pass.

    It reports and it does not judge.  There is no threshold here and
    there must not be one: how many superseded renders are too many is
    the captain's call, and deleting them is irreversible.

    `shared_with_placed` is the one figure that changes what a reader may
    safely DO, so it is separate and named.  Three of the field test's
    1,216 unplaced items point at a file a PLACED item also uses -
    `pool.ImportMedia` made a second item for a path already in the pool
    - so removing the ITEM is safe there and deleting the FILE would take
    media off a live timeline.  A count that blurred the two would be a
    number that reads as permission.

    Derived from the same `artefacts` the plan is, so the report and the
    filing cannot disagree about which items are unplaced.
    """
    # An item with no file path cannot be shown to have come from a run,
    # so it is not counted here - `Akshita` on the field test is exactly
    # that, and it files as source material.  `count` and `paths` are
    # therefore the same population, which is why there is no third
    # number for items that have neither.
    generated = [a for a in artefacts
                 if a.kind == "clip" and is_generated(a.file_path, project_root)]
    unplaced = [a for a in generated if not a.placed_by]
    placed_paths = {a.file_path for a in generated if a.placed_by}
    paths = sorted({a.file_path for a in unplaced if a.file_path})
    holding = sorted({
        "/".join(bins.unplaced_bin_for_file(a.file_path, project_root))
        for a in unplaced})
    return {
        "count": len(unplaced),
        "paths": tuple(paths),
        "shared_with_placed": tuple(p for p in paths if p in placed_paths),
        "bins": tuple(holding),
    }


def scratch_report(artefacts: Sequence[Artefact],
                   project_folder: str) -> dict:
    """Staging and scratch timelines still in the pool, and which of
    them have outlived their promotion.

    Measured 2026-09-11: three `(scratch fm-restore...) (rebuild
    staging)` timelines sat in `05 - Reels` until the captain found
    them - no run had ever said they were still there. So every run
    says: `present` names them all, `held` are pending a promotion the
    holds file records (with what each awaits and how long it has
    waited), and `outlived` are under no hold - their promotion, if it
    ever happens, already did, and they are clutter the next run will
    name again rather than discover by accident.

    `misplaced` are scratch timelines sitting anywhere but the scratch
    bin - the placement the build guarantees and the plan repairs, said
    here by name and bin so a hand-duplicated scratch is noticed on the
    run that finds it, not the review that trips over it.

    Nothing here deletes: a scratch beside a pending promotion is
    exactly what the holds guard (`library/tools/staging_holds.py`)
    keeps, and removing any other timeline by name needs the captain's
    authority for that name (`library/tools/proof_cleanup.py`), not a
    second sweep. The safe automatic removal the sweep already owns is
    the holds-aware proof path; this report drives the operator to it
    rather than duplicating it.

    An unreadable holds file does not read as empty: `held` and
    `outlived` stay unknown and `holds_unreadable` carries the refusal
    loudly, while `present` and `misplaced` - which need no holds - are
    still reported. The plan and the filing never depend on this; the
    report is advisory, so a corrupt holds file degrades the report
    rather than refusing the organise.
    """
    present = sorted(
        a.name for a in artefacts
        if a.kind == "timeline" and bins.is_scratch_timeline(a.name))
    misplaced = sorted(
        {(a.name, "/".join(a.folder_path) or "(root)")
         for a in artefacts
         if a.kind == "timeline" and bins.is_scratch_timeline(a.name)
         and tuple(a.folder_path) != (BIN_SCRATCH,)})
    try:
        held_entries = holds.read_holds(project_folder)
    except holds.HoldsUnreadable as unreadable:
        return {"present": present, "held": [], "outlived": [],
                "misplaced": misplaced,
                "holds_unreadable": str(unreadable)}
    present_set = set(present)
    held = sorted(
        ({"name": name,
          "awaiting": (held_entries[name] or {}).get("awaiting"),
          "age": holds.hold_age(held_entries[name])}
         for name in present_set & set(held_entries)),
        key=lambda entry: entry["name"])
    outlived = sorted(present_set - set(held_entries))
    return {"present": present, "held": held, "outlived": outlived,
            "misplaced": misplaced, "holds_unreadable": ""}


def render_scratch_report(report: dict) -> str:
    """The scratch timelines, in the sentences an operator has to read.

    Said on every organise and every build, even when there is nothing
    to say: a run that names its scratches is one the captain never
    has to audit by hand.
    """
    if report.get("holds_unreadable"):
        lines = [
            f"  Staging holds unreadable: {report['holds_unreadable']}",
        ]
        if report.get("present"):
            lines.append(
                "  Scratch timelines still in the pool (hold status "
                "unknown until the holds file is inspected): "
                + ", ".join(repr(n) for n in report["present"]) + ".")
        return "\n".join(lines)
    if not report.get("present"):
        return ("  No staging or scratch timelines in the pool - nothing "
                "is waiting on a promotion and nothing outlived one.")
    lines = []
    for name, folder in report.get("misplaced", []):
        lines.append(
            f"  Scratch timeline {name!r} sits in "
            f"{folder!r}, not the scratch bin "
            f"{BIN_SCRATCH!r} - the organiser files it back; "
            f"a scratch beside a deliverable is where misplaced "
            f"feedback comes from.")
    for entry in report.get("held", []):
        awaiting = entry.get("awaiting")
        waits = (f"awaiting promotion to {awaiting!r}"
                 if awaiting else "awaiting an explicit promotion decision")
        lines.append(
            f"  Scratch timeline {entry['name']!r} is HELD - {waits} "
            f"({entry.get('age', '')} ago). The sweep keeps it until "
            f"promotion releases the hold.")
    for name in report.get("outlived", []):
        lines.append(
            f"  Scratch timeline {name!r} is still here under NO "
            f"pending-promotion hold - it outlived its purpose. Promote "
            f"it, or remove it by hand; the sweep never takes an "
            f"unlisted name silently.")
    return "\n".join(lines)


def pool_tree_report(artefacts: Sequence[Artefact]) -> str:
    """The pool as it stands, read before anything is written.

    One line per bin with the asset count of its whole subtree, so the
    operator sees what is filed where BEFORE the plan moves anything -
    the read half of the migration bar. Timelines and clips count the
    same: this is occupancy, not a verdict.
    """
    subtree: dict[tuple[str, ...], int] = {}
    at_root = 0
    for a in artefacts:
        folder = tuple(a.folder_path)
        if not folder:
            at_root += 1
        for depth in range(1, len(folder) + 1):
            prefix = folder[:depth]
            subtree[prefix] = subtree.get(prefix, 0) + 1

    def line(path: tuple[str, ...]) -> str:
        name = "/".join(path) if path else "(root)"
        count = at_root if not path else subtree[path]
        return f"{'  ' * len(path)}{name} ({count} item(s))"

    ordered = sorted(subtree, key=lambda p: (len(p), list(p)))
    lines = [line(p) for p in ordered]
    if at_root:
        lines.append(line(()))
    return "\n".join(lines)


def state_from_keywords(keywords: str) -> str | None:
    """The state a pool item CLAIMS, read back off its own keywords.

    Returns None when nothing this pipeline wrote is there, which is not
    a defect: an item organised before stamping existed, or one the
    captain added by hand, simply has nothing to reconcile.
    """
    for token in (keywords or "").split():
        if token.startswith(f"{TAG_PREFIX}state="):
            value = token.split("=", 1)[1]
            return value if value in STATES else None
    return None


RETIRABLE_TOP_BINS = frozenset(
    [top for (top,) in bins.LEGACY_SUCCESSORS]
    + list(STATE_BINS.values()))
"""Top-level bins the pipeline's own superseded scheme stood up, plus the
old state leaves standing alone at the top level (`Unrecorded`, ...).
A top-level bin with any other name is the captain's - they may be about
to put something in it - and stays even when empty."""

_REEL_LEAF_RE = re.compile(r"^Reel \d+\s+-")
"""The leaf vocabulary a per-reel bin is provably pipeline-made in:
`Reel NN - ...` - staging, final, versioned (`v003`) or backup
(`(superseded)`) alike. Firstmate proofs (`SOP Proof...`,
`bins.is_proof_timeline`) join it in `is_retired_canonical_bin`.
Anything else under a render top is read as the captain's."""

STATE_LEAVES = frozenset(STATE_BINS.values())
"""Leaf names of the old scheme's state bins. Kept so the per-reel state
survives the move in `resolve_bin_layout.REEL_STATE_BINS`."""


def _is_pipeline_component(name: str, timeline_names: frozenset) -> bool:
    """Is this one path component old-scheme vocabulary?

    A legacy top, a state leaf, the unplaced leaf, a nested legacy top
    (the duplicate `AddSubFolder` forks), or a per-reel leaf named for a
    timeline the pool actually holds - the old scheme filed per-timeline
    bins under its clip tops, and the migration emptied them. Anything
    else (`Fully approved`, `my picks`) is organisation the pipeline did
    not make, and from the pool alone it is indistinguishable from the
    captain's - so it is read as the captain's and stays.

    Residual unknown, said plainly: a captain's bin named EXACTLY like a
    timeline under a legacy top reads as pipeline-made. Every retirement
    is journalled by name, so a wrong one reverts by re-creating it.
    """
    return (
        name in LEGACY_CLIP_BINS
        or name == LEGACY_REELS_BIN
        or name in STATE_LEAVES
        or name == BIN_UNPLACED
        or name in timeline_names
    )


RENDER_TOP_BINS = frozenset([BIN_SUBTITLES, bins.MOTION_GRAPHICS_BIN])
"""The CURRENT scheme's render tops. Their children are per-timeline
bins the plan names for the timeline that places the clips, so a reel
rebuilt under a new name leaves its old child standing and empty."""


def is_spent_render_bin(path: Sequence[str]) -> bool:
    """Is this an emptied PER-REEL bin of the CURRENT scheme?

    The defect this answers, measured on geo-podcast 2026-09-10: the
    retirement rule only ever reached the LEGACY tops, and per-reel
    bins live under the canonical ones (`06 - Subtitle renders`,
    `07 - Motion graphics`). So `Reel 09 ... (j-cut)` sat empty under
    BOTH of them with no rule that could ever collect it, and every
    rebuild under a new reel name added one more - which is exactly the
    "empty bins from several iterations" the captain reported.

    Depth two only, and never `Not placed on any timeline` or `Placed
    on several timelines`: the tops themselves are scaffolding
    `resolve_bin_layout.bins_to_create` stands up on every build, and
    the unplaced and shared leaves are canonical DESTINATIONS - retiring
    either would be a bin the next build immediately re-creates, which
    is churn, not cleanup.

    Emptiness is NOT decided here: this is scheme membership, and
    `plan_retirements` proves the bin holds nothing off the artefacts.
    """
    path = tuple(path)
    return (len(path) == 2
            and path[0] in RENDER_TOP_BINS
            and path[1] not in (BIN_UNPLACED, BIN_SHARED))


def is_retired_scheme_bin(path: Sequence[str],
                           timeline_names: frozenset = frozenset()) -> bool:
    """May this bin be retired once empty? Scheme membership only -
    emptiness is `plan_retirements`' half, proven off the artefacts."""
    path = tuple(path)
    if len(path) == 2 and path[0] in RENDER_TOP_BINS:
        # Current-scheme per-reel leaves are the canonical rule's
        # half (`is_retired_canonical_bin`): only reel-vocabulary
        # leaves of gone timelines retire there, and a captain's bin
        # under a render top stays. Claiming them here too would
        # retire any empty leaf whatever it is called.
        return False
    if is_spent_render_bin(path):
        return True
    if not path or path[0] not in RETIRABLE_TOP_BINS:
        return False
    if path[0] in STATE_LEAVES and len(path) > 1:
        return False
    return all(_is_pipeline_component(name, timeline_names)
               for name in path[1:])


def is_retired_canonical_bin(path: Sequence[str],
                             timeline_names: frozenset = frozenset()
                             ) -> bool:
    """May this CANONICAL bin be retired once empty? The other half of
    the sweep `plan_retirements` runs: the legacy scheme above covers
    the retired bins, this covers the CURRENT scheme's per-reel leaves
    - `06 - Subtitle renders/<reel>`, `07 - Motion graphics/<reel>` -
    whose timeline is gone.

    A rebuild files under the staging name and promotion moves the
    items onto the final name; a deleted timeline leaves its bin
    behind. Either way the bin that is left is EMPTY and names nothing
    live, and keeping it is what strands a stale sub-bin beside every
    rebuild for ever. So: depth exactly two under a render top (the
    layout allows nothing deeper there), never the `Not placed` or
    `Placed on several` standing destinations, and never a leaf a live timeline still
    answers to - a live reel with nothing currently filed may gain
    some on the next build, and retiring its bin would be churn, not
    cleaning.

    A captain's bin under a render top (`my picks`) stays even when
    empty: from the pool alone it is indistinguishable from the
    captain's, so only a leaf in REEL-LEAF vocabulary retires - a
    `Reel NN - ...` name (staging, final, versioned or backup) or a
    firstmate proof. A deleted timeline by any other name leaves a bin
    this sweep cannot prove pipeline-made, and the captain's
    organisation wins where the two conflict.

    Residual unknown, said plainly: a captain's bin named EXACTLY like
    a reel under a render top reads as pipeline-made. Every retirement
    is journalled and re-creating an empty bin restores exactly what
    was removed.
    """
    path = tuple(path)
    if len(path) != 2:
        return False
    top, leaf = path
    if top not in (bins.SUBTITLES_BIN, bins.MOTION_GRAPHICS_BIN):
        return False
    if leaf in (BIN_UNPLACED, BIN_SHARED):
        return False
    if leaf in timeline_names:
        return False
    return bool(_REEL_LEAF_RE.match(leaf)) or bins.is_proof_timeline(leaf)


def _successor_of(top: str) -> str:
    """The canonical bin that superseded this legacy top, for the record."""
    if top in RENDER_TOP_BINS:
        return top
    successor = bins.LEGACY_SUCCESSORS.get((top,))
    if successor is not None:
        return "/".join(successor)
    if top in STATE_LEAVES:
        return BIN_REELS
    return BIN_REELS


RENDER_TOP_BINS = frozenset([BIN_SUBTITLES, BIN_MOTION_GRAPHICS])
"""The canonical render tops whose depth-2 children are per-timeline
bins.  The plan files a generated clip under `(render_bin, placer)`,
so every child but the unplaced leaf is named for the timeline that
places what is inside it - which is what makes a child naming NO live
timeline a measurable fact rather than a guess."""


def plan_dead_render_bins(
        artefacts: Sequence[Artefact],
        bin_paths: Sequence[Sequence[str]],
        project_root: str,
        timeline_names: Iterable[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Per-reel leaves of the CURRENT scheme whose timeline is gone.

    Returns `(retirements, declined)`.  A retirement is
    `{"path": (...), "why": ..., "contents": [{item_id, name,
    file_path, folder}]}` - the pool items that go with the bin, so
    the executor removes exactly what was proven and journals what it
    held.  A declined entry is `{"path": (...), "why": ...}`: a bin
    that looked dead and was kept, with the sentence saying why -
    an over-cautious sweep reports what it declined rather than
    guessing.

    Dead means all of these, proven off the artefacts:

    - depth exactly 2 under a render top, and not the unplaced or
      shared leaf (canonical DESTINATIONS the next build re-creates;
      retiring either is churn, not cleanup),
    - the leaf names NO live timeline, exactly - the same exact-match
      rule that files timelines, so a variant suffix is a different
      name and a different bin,
    - nothing in the subtree is a timeline (timelines file under
      `05 - Reels`; one sitting here is a human's filing or a drag,
      and retiring its bin would strand it),
    - every clip in the subtree is placed on NOTHING - a clip some
      live timeline still plays belongs to that timeline's bin, and
      the organiser moves it there; the sweep does not take it,
    - every clip in the subtree is pipeline-generated (its file under
      the project directory) - anything else is the captain's
      material and stays,
    - no sub-bin stands under it that this plan does not retire -
      retiring the parent would take that bin with it.

    A leaf named for a timeline that still exists is never examined
    here, whatever that timeline's state: `Earlier plans` reels keep
    their bins.  Contents may be empty - an emptied dead leaf retires
    as a shell, the same outcome the legacy rule gives its own - but
    ONLY for a leaf in reel-leaf vocabulary (`Reel NN - ...`, a
    staging/final/versioned/backup name, or a firstmate proof): the
    executor retires an emptied leaf through the same membership rule
    the canonical shell sweep uses, so a leaf the rule cannot prove
    the pipeline made is DECLINED here, never emitted.  An empty
    subtree proves nothing off the artefacts - no timeline inside,
    nothing placed, nothing foreign are all vacuously true - so for a
    leaf outside that vocabulary "names no live timeline" is the whole
    proof, and it holds for every captain's bin by construction.
    Emitting it would hand the executor a plan it must refuse (or
    worse, a non-empty one it would delete), so the planner says so
    instead.
    """
    if timeline_names is None:
        timeline_names = frozenset(
            a.name for a in artefacts if a.kind == "timeline")
    else:
        timeline_names = frozenset(timeline_names)

    known: set[tuple[str, ...]] = {tuple(p) for p in bin_paths}
    for artefact in artefacts:
        folder = tuple(artefact.folder_path)
        for depth in range(1, len(folder) + 1):
            known.add(folder[:depth])
    known.discard(())

    retirements: list[dict] = []
    declined: list[dict] = []
    for path in sorted(known):
        if not (len(path) == 2 and path[0] in RENDER_TOP_BINS
                and path[1] not in (BIN_UNPLACED, BIN_SHARED)):
            continue
        leaf = path[1]
        if leaf in timeline_names:
            continue
        if not (_REEL_LEAF_RE.match(leaf)
                or bins.is_proof_timeline(leaf)):
            declined.append({
                "path": path,
                "why": (f"names no live timeline, but {leaf!r} is not "
                        f"in reel-leaf vocabulary (`Reel NN - ...`) and "
                        f"is no firstmate proof - kept; from the pool "
                        f"alone it is indistinguishable from the "
                        f"captain's, so the sweep cannot prove the "
                        f"pipeline made it and the captain's "
                        f"organisation wins where the two conflict")})
            continue
        subtree = [a for a in artefacts
                   if tuple(a.folder_path)[:2] == path]
        holders = [a for a in subtree if a.kind == "timeline"]
        if holders:
            declined.append({
                "path": path,
                "why": (f"names no live timeline, but timeline "
                        f"{holders[0].name!r} sits inside it - kept, "
                        f"because retiring the bin would strand it")})
            continue
        placed = [a for a in subtree if a.placed_by]
        if placed:
            declined.append({
                "path": path,
                "why": (                f"names no live timeline, but "
                        f"{placed[0].name!r} inside it is still placed "
                        f"on {min(placed[0].placed_by)!r} - kept; "
                        f"the organiser files placed clips by their "
                        f"live placer")})
            continue
        foreign = [a for a in subtree
                   if not is_generated(a.file_path, project_root)]
        if foreign:
            declined.append({
                "path": path,
                "why": (f"names no live timeline, but "
                        f"{foreign[0].name!r} inside it is not "
                        f"pipeline-generated - kept; the sweep never "
                        f"takes the captain's material")})
            continue
        descendants = [b for b in known
                       if len(b) > len(path) and b[:len(path)] == path]
        if descendants:
            declined.append({
                "path": path,
                "why": (f"names no live timeline, but sub-bin(s) "
                        f"{', '.join('/'.join(d) for d in sorted(descendants))} "
                        f"stand under it that this plan does not retire "
                        f"- kept, because retiring the parent would take "
                        f"them with it")})
            continue
        contents = [{"item_id": a.item_id, "name": a.name,
                     "file_path": a.file_path,
                     "folder": "/".join(a.folder_path)}
                    for a in sorted(subtree, key=lambda a: a.name)]
        retirements.append({
            "path": path,
            "kind": "dead_render_bin",
            "why": (f"per-reel bin under '{path[0]}' naming "
                    f"{leaf!r}, and no timeline of that name is in "
                    f"the pool - dead whether or not it holds files; "
                    f"{len(contents)} item(s) retire with it"),
            "contents": contents,
        })
    retirements.sort(key=lambda e: ([-len(e["path"])] + list(e["path"])))
    return retirements, declined


def plan_retirements(
        artefacts: Sequence[Artefact],
        bin_paths: Sequence[Sequence[str]],
        timeline_names: Iterable[str] | None = None,
        project_root: str | None = None) -> list[dict]:
    """The legacy shells that are provably empty, deepest first.

    Pure: takes the artefacts off `read_pool` and the full bin tree, and
    returns `[{"path": (...), "why": ...}]` ordered so a child is always
    retired before its parent. Calls nothing, writes nothing.

    TWO populations, one sweep rather than two sweepers: the retired
    SCHEME's shells (`is_retired_scheme_bin` - the migration's legacy
    bins) and the current scheme's orphaned per-reel leaves
    (`is_retired_canonical_bin` - a staging name emptied by promotion,
    a deleted timeline's bin). Both retire only once empty, and the
    emptiness proof below is shared.

    Empty means NO ITEM in the whole subtree - a shell with an empty
    sub-bin under it is still empty, and the sub-bin retires with it.
    A bin stays when anything at all is inside it, when it is not part
    of either population (the captain's, even when empty), or when a kept
    sub-bin stands under it - retiring the parent would take the
    captain's bin with it, so the parent stays too.

    Dead per-reel leaves of the CURRENT scheme ride along when
    `project_root` is given: `plan_dead_render_bins` retires a leaf
    under a render bin that names no live timeline, WITH its contents
    (proven unplaced and pipeline-generated).  Without a root the dead
    rule cannot prove contents are the pipeline's, so it stays off and
    this plans legacy shells exactly as before.
    """
    if timeline_names is None:
        timeline_names = frozenset(
            a.name for a in artefacts if a.kind == "timeline")
    else:
        timeline_names = frozenset(timeline_names)

    occupancy: dict[tuple[str, ...], int] = {}
    for artefact in artefacts:
        folder = tuple(artefact.folder_path)
        for depth in range(1, len(folder) + 1):
            prefix = folder[:depth]
            occupancy[prefix] = occupancy.get(prefix, 0) + 1

    known: set[tuple[str, ...]] = {tuple(p) for p in bin_paths}
    for artefact in artefacts:
        folder = tuple(artefact.folder_path)
        for depth in range(1, len(folder) + 1):
            known.add(folder[:depth])
    known.discard(())

    retired: list[dict] = []
    retired_paths: set[tuple[str, ...]] = set()
    for path in sorted(known, key=lambda p: (-len(p), list(p))):
        if is_retired_scheme_bin(path, timeline_names):
            if len(path) == 1:
                why = (f"legacy bin superseded by "
                       f"'{_successor_of(path[0])}'; empty including sub-bins")
            else:
                why = (f"emptied legacy leaf under "
                       f"'{'/'.join(path[:-1])}', superseded by "
                       f"'{_successor_of(path[0])}'; empty including sub-bins")
        elif is_retired_canonical_bin(path, timeline_names):
            why = (f"emptied per-reel bin under "
                   f"'{path[0]}' for {path[1]!r}, which no live timeline "
                   f"answers to; empty including sub-bins")
        else:
            continue
        if occupancy.get(path, 0):
            continue
        descendants = [b for b in known
                       if len(b) > len(path) and b[:len(path)] == path]
        if any(d not in retired_paths for d in descendants):
            continue
        kind = ("legacy_shell"
                if is_retired_scheme_bin(path, timeline_names)
                else "canonical_empty")
        retired.append({"path": path, "kind": kind, "why": why})
        retired_paths.add(path)
    if project_root is not None:
        dead, _declined = plan_dead_render_bins(
            artefacts, bin_paths, project_root, timeline_names)
        for entry in dead:
            key = tuple(entry["path"])
            if key not in retired_paths:
                retired.append(entry)
                retired_paths.add(key)
            else:
                # The dead proof is stronger than the empty proof: it
                # re-proves the contents unplaced and pipeline-made,
                # so a dead leaf first swept as `canonical_empty`
                # upgrades to `dead_render_bin`. Legacy shells are a
                # different population and keep their kind.
                for i, old in enumerate(retired):
                    if tuple(old["path"]) == key and old.get(
                            "kind") == "canonical_empty":
                        retired[i] = entry
        retired.sort(key=lambda e: (-len(e["path"]), list(e["path"])))
    return retired


def render_bin_census(artefacts: Sequence[Artefact],
                       bin_paths: Sequence[Sequence[str]],
                       retirements: Sequence[dict],
                       timeline_names: Iterable[str] | None = None,
                       declined: Sequence[dict] = ()) -> str:
    """Every bin with its recursive item count and the retire/keep
    decision for each - the read-only plan, reported before acting and
    reconciled after."""
    if timeline_names is None:
        timeline_names = frozenset(
            a.name for a in artefacts if a.kind == "timeline")
    else:
        timeline_names = frozenset(timeline_names)
    retiring = {tuple(r["path"]): r for r in retirements}
    declining = {tuple(d["path"]): d["why"] for d in declined}
    occupancy: dict[tuple[str, ...], int] = {}
    for artefact in artefacts:
        folder = tuple(artefact.folder_path)
        for depth in range(1, len(folder) + 1):
            prefix = folder[:depth]
            occupancy[prefix] = occupancy.get(prefix, 0) + 1

    known = sorted({tuple(p) for p in bin_paths if tuple(p)},
                   key=lambda p: (len(p), list(p)))
    lines = ["Media-pool bins, with recursive item counts:"]
    for path in known:
        name = "/".join(path)
        count = occupancy.get(path, 0)
        if path in retiring:
            entry = retiring[path]
            held = entry.get("contents") or []
            if held:
                lines.append(
                    f"  {name} ({count} item(s)) - "
                    f"RETIRE with {len(held)} item(s): {entry['why']}")
            else:
                lines.append(f"  {name} ({count} item(s)) - "
                             f"RETIRE: {entry['why']}")
        elif path in declining:
            lines.append(f"  {name} ({count} item(s)) - "
                         f"keep: declined - {declining[path]}")
        elif is_retired_scheme_bin(path, timeline_names):
            lines.append(f"  {name} ({count} item(s)) - "
                         f"keep: legacy but not empty, or a kept bin "
                         f"stands under it")
        elif is_retired_canonical_bin(path, timeline_names):
            lines.append(f"  {name} ({count} item(s)) - "
                         f"keep: orphaned per-reel bin, but not empty "
                         f"or a kept bin stands under it")
        elif (len(path) >= 1 and path[0] in
              (bins.SUBTITLES_BIN, bins.MOTION_GRAPHICS_BIN)):
            lines.append(f"  {name} ({count} item(s)) - "
                         f"keep: current-scheme bin - a render top, the "
                         f"standing unplaced or shared destination, or a "
                         f"live reel's bin")
        else:
            lines.append(f"  {name} ({count} item(s)) - "
                         f"keep: not part of either scheme - the "
                         f"captain's, and it stays even when empty")
    if not known:
        lines.append("  (no sub-bins)")
    return "\n".join(lines)


def render_plan(plan: Plan) -> str:
    """The plan, as the operator reads it before deciding to apply it."""
    lines = [f"Media pool: {plan.root_bin}", ""]
    counts: dict[str, int] = {}
    for v in plan.verdicts:
        counts[v.destination_path] = counts.get(v.destination_path, 0) + 1
    lines.append(f"{len(plan.folders)} bin(s) in the layout:")
    for f in plan.folders:
        path = "/".join(f)
        lines.append(f"  {path}    ({counts.get(path, 0)} item(s))")
    moving = sum(1 for _ in plan.moves)
    lines.append("")
    lines.append(f"{moving} of {len(plan.verdicts)} item(s) would move.")
    for name, why in plan.left_alone:
        lines.append(f"  left alone: {name} - {why}")
    by_state: dict[str, int] = {}
    for s in plan.stamps:
        by_state[s["state"]] = by_state.get(s["state"], 0) + 1
    if by_state:
        lines.append("")
        lines.append("Reel timelines by state: " + ", ".join(
            f"{k}={by_state[k]}" for k in STATES if k in by_state))
    return "\n".join(lines)
