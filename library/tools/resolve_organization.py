"""Where a Resolve project's timelines and clips BELONG, and why.

The captain, on the field-test project (2026-09-06): *"one thing i
mentioned was having folder setups and organization inside the davinci
project to be able to view all of the reels timelines and have all other
assets organized as well"*.

What the project actually looked like
-------------------------------------
Measured on `Podcast (field test)` before any of this ran, twice - once
from a COPY of `Project.db` and once from the live scripting API, which
agreed on every count:

- 49 timelines: one master, 20 reels the live plan built, 20 from plans
  since replaced, and 8 one-off experiments.
- 2,583 media-pool items, of which **10 are real footage** - seven camera
  files, two vfx assets and a still.  The other 2,573 are rendered
  caption overlays this pipeline wrote.
- **1,216 of those overlays are placed on no timeline at all**: renders
  whose reel was rebuilt under a new name, left behind in the pool.
- 21 of the 49 timelines sat in the root bin among 449 overlays; the
  other 28 sat in the captain's own `Reels` bin among 760 more.

Nothing decided any of that.  `pool.CreateEmptyTimeline` and
`pool.ImportMedia` put what they make into whatever bin happens to be
CURRENT, which is wherever the operator last clicked - so a batch built
on Friday and a batch built on Saturday land in different places for no
reason either of them recorded.

The rule has one dimension, and it is a MEASUREMENT
---------------------------------------------------
Every artefact is filed by facts read off the project, never by parsing
its name:

1. A **timeline** goes to `05 - Reels/<state>` (below) - but only out of
   root or out of one of the pipeline's own bins.  A timeline sitting in
   any other bin is where a human put it and stays there (`TIMELINE_BINS`).
   A proof timeline (`SOP Proof_...`, firstmate's own) goes to
   `05 - Reels/Proof`, never among the captain's reels.  The project's
   own declared master timeline is NEVER moved - AGENTS.md 5.
2. A clip whose file lives **under the project's own directory** is
   something this pipeline generated.  It files under the timeline that
   PLACES it, nested under the render bin its KIND belongs to
   (`06 - Subtitle renders` for subtitle segments, `07 - Motion
   graphics` for motion-graphics, timed-text and carrier renders - a
   path fact read off `project_layout.Area`, never a name parse), or
   under that bin's `Not placed on any timeline` when nothing does.
3. Anything else is material that came from outside, and files under
   `Source footage`.

"Which timeline places it" is read from the timelines themselves, not
from the filename.  The rendered overlays do encode their timeline
(`library/tools/subtitle_segment_id.py`), but a name is a claim and the
timeline is the fact - and 1,216 of them are placed nowhere, which no
filename can say.

Per-reel bins are FLAT under the render bin rather than nested under
the reel's state, and that is a decision the no-deleting rule forces:
a reel moves from `Current plan` to `Earlier plans` on the next build,
and a captions tree that mirrored the state would leave the bin it
moved out of behind and EMPTY - for every reel, on every plan change,
for ever, with nothing here allowed to clean them up.  State belongs
to the timeline; the captions bin answers a different question -
"which clips does THIS reel place" - and that answer does not move
when the plan does.

CURRENT is a record, not a guess
--------------------------------
A reel's state comes from `plan_provenance.json`, which the BUILDER
writes and which names the plan it built from by content hash:

- `CURRENT` - the live provenance record's `built_reels` names it.
- `EARLIER` - an ARCHIVED plan names it, and the live record does not.
- `UNRECORDED` - no plan on disk names it.

Exact names only.  A near match lands elsewhere is already the rule for
addressing a Resolve project and a Resolve timeline (AGENTS.md 5), and
it is the same rule here: eight of the field test's timelines are a plan
name plus a hand-typed suffix (`" (fragment fix)"`), and calling those
EARLIER would mean deciding by prefix that a build nobody recorded came
from a plan nobody wrote down.  They are UNRECORDED, which is what the
evidence says.

`UNRECORDED` is therefore a real answer and not a failure.  The captain
built those deliberately.

Nothing here deletes
--------------------
There is no delete call in this module or in the executor that drives it,
and `tests/test_resolve_organization.py` asserts that of both files.  A
reel the live plan no longer names is MOVED and RELABELLED, never
removed: the captain's ruling of 2026-09-06 is *"a refusal is cheap and a
deleted timeline is not"*, and the same reasoning makes an accumulated
timeline cheaper than a lost one.

Every move is journalled with the bin it came FROM, so the whole thing
reverses.  What a revert cannot undo is a bin that was CREATED - undoing
a create means deleting - so a revert reports those by name and leaves
them, the same bargain `project_migration.revert_from_manifest` strikes
with copies.

This module holds no Resolve calls and does no I/O, so every rule in it
is testable without the application running - the same split
`library/tools/panel/` uses (AGENTS.md 15).
`library/tools/execution/organise_media_pool.py` is the half that talks
to Resolve.

`tests/test_resolve_organization.py`.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from library.tools import resolve_bin_layout as bins

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

BIN_PROOF = bins.REELS_PROOF_BIN
"""Leaf name of the bin firstmate's proof timelines file under. An
alias like the rest."""

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
    + [(LEGACY_REELS_BIN, bins.REEL_STATE_BINS[state])
       for state in STATES]
    + [(LEGACY_REELS_BIN,)])
"""The only bins a reel timeline is ever filed OUT of: root (unfiled),
the canonical state and proof bins, the canonical reels root, and the
retired scheme's equivalents of the state bins and root.

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
               built_reels: Iterable[str],
               archived_plan_names: Iterable[str]) -> tuple[str, str]:
    """One timeline's state, and the sentence that justifies it.

    EXACT membership, never a prefix.  See the module docstring for why
    the eight suffixed one-offs on the field test are UNRECORDED rather
    than EARLIER.
    """
    if timeline_name in set(built_reels):
        return CURRENT, "the live plan's provenance record names it"
    if timeline_name in set(archived_plan_names):
        return EARLIER, ("an archived plan names it and the live "
                         "provenance record does not")
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
                      built_reels: Iterable[str],
                      archived_plan_names: Iterable[str],
                      root_bin: str = "Master",
                      plan_hash: str = "",
                      built_at: str = "") -> Plan:
    """The whole filing decision, computed before anything is touched.

    Takes measurements and records; returns a `Plan`.  Calls nothing,
    writes nothing, and cannot leave the project half-organised because
    it never touches it.
    """
    if not master_timeline_name:
        raise OrganizationError(
            "no master timeline name was given. The master is the one "
            "timeline that must never be moved (AGENTS.md 5), and an "
            "organiser that cannot name it would file it like any other "
            "reel. `resolve.timeline_name` in project.yaml is where it "
            "comes from.")

    built = set(built_reels)
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
                dest = want((BIN_SOURCE,))
                plan.verdicts.append(Verdict(
                    a.item_id, a.name, a.kind, dest,
                    f"{len(a.placed_by)} timelines place it, so it "
                    f"belongs to no single one"))
            else:
                dest = want((render_bin, BIN_UNPLACED))
                plan.verdicts.append(Verdict(
                    a.item_id, a.name, a.kind, dest,
                    "no timeline places it"))
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
             recorded_states: dict[str, str] | None = None) -> list[dict]:
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

    for v in plan.verdicts:
        a = by_id.get(v.item_id)
        if a is None:
            out.append({"kind": "missing_item", "name": v.name,
                        "detail": f"{v.name!r} is in the plan and not in "
                                  f"the media pool"})
            continue
        if a.folder_path != v.destination:
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
        f"{bins.render_bin_for_file(a.file_path, project_root)}"
        f"/{BIN_UNPLACED}"
        for a in unplaced})
    return {
        "count": len(unplaced),
        "paths": tuple(paths),
        "shared_with_placed": tuple(p for p in paths if p in placed_paths),
        "bins": tuple(holding),
    }


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


def is_retired_scheme_bin(path: Sequence[str],
                           timeline_names: frozenset = frozenset()) -> bool:
    """May this bin be retired once empty? Scheme membership only -
    emptiness is `plan_retirements`' half, proven off the artefacts."""
    path = tuple(path)
    if not path or path[0] not in RETIRABLE_TOP_BINS:
        return False
    if path[0] in STATE_LEAVES and len(path) > 1:
        return False
    return all(_is_pipeline_component(name, timeline_names)
               for name in path[1:])


def _successor_of(top: str) -> str:
    """The canonical bin that superseded this legacy top, for the record."""
    successor = bins.LEGACY_SUCCESSORS.get((top,))
    if successor is not None:
        return "/".join(successor)
    if top in STATE_LEAVES:
        return BIN_REELS
    return BIN_REELS


def plan_retirements(
        artefacts: Sequence[Artefact],
        bin_paths: Sequence[Sequence[str]],
        timeline_names: Iterable[str] | None = None) -> list[dict]:
    """The legacy shells that are provably empty, deepest first.

    Pure: takes the artefacts off `read_pool` and the full bin tree, and
    returns `[{"path": (...), "why": ...}]` ordered so a child is always
    retired before its parent. Calls nothing, writes nothing.

    Empty means NO ITEM in the whole subtree - a shell with an empty
    sub-bin under it is still empty, and the sub-bin retires with it.
    A bin stays when anything at all is inside it, when it is not part
    of either scheme (the captain's, even when empty), or when a kept
    sub-bin stands under it - retiring the parent would take the
    captain's bin with it, so the parent stays too.
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
        if not is_retired_scheme_bin(path, timeline_names):
            continue
        if occupancy.get(path, 0):
            continue
        descendants = [b for b in known
                       if len(b) > len(path) and b[:len(path)] == path]
        if any(d not in retired_paths for d in descendants):
            continue
        if len(path) == 1:
            why = (f"legacy bin superseded by "
                   f"'{_successor_of(path[0])}'; empty including sub-bins")
        else:
            why = (f"emptied legacy leaf under "
                   f"'{'/'.join(path[:-1])}', superseded by "
                   f"'{_successor_of(path[0])}'; empty including sub-bins")
        retired.append({"path": path, "why": why})
        retired_paths.add(path)
    return retired


def render_bin_census(artefacts: Sequence[Artefact],
                      bin_paths: Sequence[Sequence[str]],
                      retirements: Sequence[dict],
                      timeline_names: Iterable[str] | None = None) -> str:
    """Every bin with its recursive item count and the retire/keep
    decision for each - the read-only plan, reported before acting and
    reconciled after."""
    if timeline_names is None:
        timeline_names = frozenset(
            a.name for a in artefacts if a.kind == "timeline")
    else:
        timeline_names = frozenset(timeline_names)
    retiring = {tuple(r["path"]): r["why"] for r in retirements}
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
            lines.append(f"  {name} ({count} item(s)) - "
                         f"RETIRE: {retiring[path]}")
        elif is_retired_scheme_bin(path, timeline_names):
            lines.append(f"  {name} ({count} item(s)) - "
                         f"keep: legacy but not empty, or a kept bin "
                         f"stands under it")
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
