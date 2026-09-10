"""Removing firstmate's proof artefacts from the captain's pool, by name.

The captain, on the bin tree (2026-09-10): *"yeah clean that up"* - about
the leftover `SOP Proof_...` bin and the `SOP Proof_min-canvas-rail`
timeline that created it - and, separately, the crew demo timeline
`Reel 09 - your-website-is-only-20-percent (firstmate-merge-demo)`
with any bin it created: a crew demo artefact, same category, with
the captain's standing instruction that this kind of leftover is
cleaned up and the workspace stays clean without him having to ask.
Those sentences are the whole authority for the timeline deletions
below, and they cover exactly what they name.  Neither is a general
license to delete timelines, and this module cannot be pointed at
anything else - a name that is not authorised here, or that the
captain protected, refuses before anything is read further.

A second brief extends the same authority to the positioning lane's
scratch (2026-09-10): *"and then you can clean up all of the bins and
old assets and timelines after that"*, sequenced after the
positioning work landed - `POSITIONING_SCRATCH_PREFIX` plus the one
expired `(pre-rebuild backup)`.  Same rules, same journal, same
protected verification; `discover_scratch_timelines` enumerates what
is present so a second run finds nothing and refuses nothing.

A third brief retires the two superseded Reel 09 timelines
(2026-09-10): *"can you go through and clean up the timelines and
associated assets and bins that are not the main reel 9 timeline?
(and obviously the geo podcast synced timeline)"* - with the
captain's screenshot showing his three remaining Reel 09 timelines.
`SUPERSEDED_REEL_TIMELINES` names the two that go, `(final)` and the
master are the two that stay, and `plan_superseded_removal` proves
the same per-bin facts as the proof path before anything runs.
These two names stay in `PROTECTED_TIMELINES` so the proof path
still refuses them; the superseded path is the only one that may
take them, by exact name, and it verifies `(final)` plus the master
untouched instead of the whole protected set - the other superseded
timeline is already gone on the second of the two removals.

Why this is not in `resolve_organization`
-----------------------------------------
Because organising must never delete, and
`tests/test_resolve_organization.py::test_no_module_here_can_delete_anything`
asserts that of both organiser files.  The dead-bin sweep
(`plan_dead_render_bins`) only collects bins whose timeline is ALREADY
gone.  The proof timeline is still there, so removing it is a
different act with its own authority, its own exact names, and its own
journal - where a reader can see that a timeline deletion was
intended, not slipped.

What is proven, and in what order
---------------------------------
1. The timeline name is firstmate's (`SOP Proof...`) - never the
   captain's work - and it is not protected and not the master.
   Exact match only (AGENTS.md 5): a near match lands elsewhere.
2. The timeline is in the pool, as a timeline.
3. Every named bin exists, sits under a render top (current or
   legacy), and holds only unplaced pipeline-generated clips - or
   clips placed only on the timeline going down with the bin, which
   the one deletion takes together.  Anything a surviving timeline
   still plays refuses.
4. The four protected timelines are verified present and untouched:
   the plan names them in its `verified_untouched` list, read off the
   same artefacts, so the check cannot pass on a pool that lost one.

This module holds no Resolve calls and does no I/O - the same split
`resolve_organization` uses.  `library/tools/execution/remove_proof.py`
is the half that deletes.

`tests/test_dead_render_bins.py`.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from library.tools import resolve_bin_layout as bins
from library.tools.resolve_organization import (
    LEGACY_CLIP_BINS,
    RENDER_TOP_BINS,
    Artefact,
    is_generated,
)

PROOF_PREFIX = bins.PROOF_PREFIX
"""Firstmate's proof timelines start here.  A prefix, not an exact
name: proof builds version and suffix theirs, and this path removes
firstmate's own artefacts, never the captain's addressed work."""

PROTECTED_TIMELINES = frozenset([
    "Reel 09 - your-website-is-only-20-percent (final)",
    "Reel 09 - your-website-is-only-20-percent (reaction-cutaway)",
    "Reel 09 - your-website-is-only-20-percent",
    "GEO Podcast - Synced",
])
"""Timelines this path must never touch.  Named by the captain's brief
of 2026-09-10; asking for any of them - or the master - refuses."""

CAPTAINS_PROOF_TIMELINE = "SOP Proof_min-canvas-rail"
"""The proof timeline the captain authorised removing, verbatim from
the brief.  The bin's exact name is confirmed off the live pool
(`discover_proof_bins` lists candidates); the timeline's is this."""

MERGE_DEMO_TIMELINE = "Reel 09 - your-website-is-only-20-percent (firstmate-merge-demo)"
"""The crew demo timeline the captain authorised removing, verbatim -
same category as the proof artefact, same rules.  Any bin it created
is confirmed off the live pool the same way."""

POSITIONING_SCRATCH_PREFIX = (
    "Reel 09 - your-website-is-only-20-percent (positioning-")
"""The positioning lane's own scratch timelines, by prefix family.

The captain, verbatim, sequencing this after the positioning work
landed (2026-09-10): *"and then you can clean up all of the bins and
old assets and timelines after that"* - his third clutter report that
day, *"so you are not confusing the user as well when they enter and
try using the project"*.  Those sentences are the authority, and they
cover the lane's eight proof timelines - `(positioning-proof)` through
`(positioning-proof-6)` plus its two sibling probes
(`(positioning-freshprobe)`, `(positioning-instant)`) - enumerated off
the live pool rather than trusted from the lane's report.  Scoped to
this reel's parenthesised variant: a near match lands elsewhere, so
anything outside the family still refuses.
"""

PRE_REBUILD_BACKUP_TIMELINE = (
    "Reel 09 - your-website-is-only-20-percent (final) (pre-rebuild backup)")
"""The rebuild lane's safety net, expired once the rebuild landed and
the captain's hand edits were captured.  Same brief as above, which
authorises `(pre-rebuild backup)` leftovers by class; this pool holds
exactly this one, so it is named exactly.  Removing it touches no
protected timeline - `(final)` keeps its 35 hand-positioned items -
and the plan below still verifies all four protected timelines plus
the master first."""

AUTHORISED_DEMO_TIMELINES = frozenset([
    CAPTAINS_PROOF_TIMELINE,
    MERGE_DEMO_TIMELINE,
])
"""Crew demo timelines the captain authorised removing, by EXACT name.
Exact names only (AGENTS.md 5): a near match lands elsewhere, so a
suffixed or reworded variant is not a member and refuses."""

SUPERSEDED_PLAIN_TIMELINE = \
    "Reel 09 - your-website-is-only-20-percent"
SUPERSEDED_REACTION_TIMELINE = (
    "Reel 09 - your-website-is-only-20-percent (reaction-cutaway)")
SUPERSEDED_REEL_TIMELINES = frozenset([
    SUPERSEDED_PLAIN_TIMELINE,
    SUPERSEDED_REACTION_TIMELINE,
])
"""The two superseded Reel 09 timelines the captain authorised
removing, by EXACT name, under his 2026-09-10 cleanup brief (*"can
you go through and clean up the timelines and associated assets and
bins that are not the main reel 9 timeline? (and obviously the geo
podcast synced timeline)"*, with the screenshot of his three
remaining Reel 09 timelines).

The plain one is superseded by `(final)`; the reaction-cutaway is
the variant he chose FROM, whose chosen content `(final)` already
carries.  Both share media with `(final)`, so the plan proves every
removal candidate unreferenced by anything that survives before it
goes - the same per-bin proof the proof path runs, through the same
shared core.  Exact names only (AGENTS.md 5).

These two stay members of `PROTECTED_TIMELINES` above, so the proof
path still refuses them: only `plan_superseded_removal` may take
them, and it answers to `(final)` plus the master instead.
"""

FINAL_REEL_TIMELINE = \
    "Reel 09 - your-website-is-only-20-percent (final)"
"""The main Reel 09 timeline: the one the captain hand-repositioned
and is happy with, holding his 27 hand-positioned overlays.  The
superseded path verifies this present and untouched around every
removal; it is the same string `PROTECTED_TIMELINES` already holds,
named here so the superseded path does not depend on that set's
membership."""


def is_authorised_demo(name: str) -> bool:
    """Is this timeline name one the captain authorised removing?

    Firstmate's proof timelines by prefix (`SOP Proof...` - proof
    builds version and suffix theirs), crew demo timelines by exact
    membership above, and the positioning lane's scratch by its
    parenthesised family plus the one expired pre-rebuild backup.
    Classification of our own artefacts is not addressing the
    captain's work; anything else refuses.
    """
    return (name or "").startswith(PROOF_PREFIX) \
        or (name or "").startswith(POSITIONING_SCRATCH_PREFIX) \
        or name in AUTHORISED_DEMO_TIMELINES \
        or name == PRE_REBUILD_BACKUP_TIMELINE


def is_authorised_superseded(name: str) -> bool:
    """Is this timeline name one of the two superseded Reel 09
    timelines the captain authorised removing?

    Exact membership in `SUPERSEDED_REEL_TIMELINES` only (AGENTS.md
    5): `(final)`, the master and every near variant refuse.
    """
    return name in SUPERSEDED_REEL_TIMELINES


class ProofRemovalRefused(Exception):
    """Something about the proof removal is not safe, so NOTHING is removed."""


def discover_proof_bins(
        artefacts: Sequence[Artefact],
        bin_paths: Sequence[Sequence[str]]) -> list[tuple[str, ...]]:
    """Candidate demo caption bins, for the operator to confirm.

    Read-only: every depth-2 bin under a render top (current or
    legacy) whose leaf starts with the proof prefix OR names an
    authorised demo timeline exactly.  The operator passes chosen
    ones back as exact `--proof-bin` names; nothing here deletes.
    """
    known: set[tuple[str, ...]] = {tuple(p) for p in bin_paths}
    for artefact in artefacts:
        folder = tuple(artefact.folder_path)
        for depth in range(1, len(folder) + 1):
            known.add(folder[:depth])
    allowed_tops = set(RENDER_TOP_BINS) | set(LEGACY_CLIP_BINS)
    return sorted(
        p for p in known
        if len(p) == 2 and p[0] in allowed_tops
        and (p[1].startswith(PROOF_PREFIX)
             or p[1] in AUTHORISED_DEMO_TIMELINES))


def discover_scratch_timelines(
        artefacts: Sequence[Artefact]) -> list[str]:
    """Authorised scratch timelines PRESENT in this pool, in name order.

    The runner removes what this returns and skips what it does not:
    a second consecutive run returns nothing, which is the idempotence
    proof - no moves, no removals, and no refusal for work already done.
    Protected timelines and the master never match the scratch family,
    and a pool that lost one still refuses inside `plan_proof_removal`.
    """
    return sorted(
        a.name for a in artefacts
        if a.kind == "timeline" and is_authorised_demo(a.name)
        and a.name not in PROTECTED_TIMELINES)


def discover_superseded_timelines(
        artefacts: Sequence[Artefact]) -> list[str]:
    """Authorised superseded timelines PRESENT in this pool, in name order.

    The runner removes what this returns and skips what it does not:
    a second consecutive run returns nothing, which is the idempotence
    proof - no moves, no removals, and no refusal for work already done.
    `(final)` and the master are never members, so they never match.
    """
    return sorted(
        a.name for a in artefacts
        if a.kind == "timeline" and is_authorised_superseded(a.name))


def discover_superseded_bins(
        artefacts: Sequence[Artefact],
        bin_paths: Sequence[Sequence[str]],
        timeline_name: str) -> list[tuple[str, ...]]:
    """The doomed timeline's own per-reel bins, for the operator to confirm.

    Read-only: every depth-2 bin under a render top (current or
    legacy) whose leaf names the doomed timeline exactly.  The
    operator passes chosen ones back as exact `--proof-bin` names;
    nothing here deletes.
    """
    known: set[tuple[str, ...]] = {tuple(p) for p in bin_paths}
    for artefact in artefacts:
        folder = tuple(artefact.folder_path)
        for depth in range(1, len(folder) + 1):
            known.add(folder[:depth])
    allowed_tops = set(RENDER_TOP_BINS) | set(LEGACY_CLIP_BINS)
    return sorted(
        p for p in known
        if len(p) == 2 and p[0] in allowed_tops and p[1] == timeline_name)


def _plan_removal(
        artefacts: Sequence[Artefact],
        bin_paths: Sequence[Sequence[str]],
        *,
        timeline_name: str,
        bin_names: Iterable[str],
        project_root: str,
        master_name: str,
        authorised: Callable[[str], bool],
        foreign_hint: str,
        bin_why: str,
        untouched_names: frozenset[str]) -> dict:
    """The shared core behind both removal plans.

    Every fact is proven before anything runs: the name is
    authorised and not one of the kept timelines, the timeline is in
    the pool as a timeline, every named bin exists under a render top
    and holds only unplaced pipeline-generated clips - or clips
    placed only on the timeline going down with the bin - and every
    kept timeline is verified present and untouched.  Raises
    `ProofRemovalRefused` on anything unproven.
    """
    if timeline_name in untouched_names or timeline_name == master_name:
        raise ProofRemovalRefused(
            f"{timeline_name!r} is protected - this path never removes "
            f"it. Nothing was removed.")
    if not authorised(timeline_name):
        raise ProofRemovalRefused(
            f"{timeline_name!r} is not a captain-authorised {foreign_hint}. "
            f"This path removes those only. Nothing was removed.")

    timelines = [a for a in artefacts
                 if a.kind == "timeline" and a.name == timeline_name]
    if not timelines:
        raise ProofRemovalRefused(
            f"no timeline called {timeline_name!r} is in the pool - "
            f"there is no exact match to prove. Nothing was removed.")
    target = timelines[0]

    known: set[tuple[str, ...]] = {tuple(p) for p in bin_paths}
    for artefact in artefacts:
        folder = tuple(artefact.folder_path)
        for depth in range(1, len(folder) + 1):
            known.add(folder[:depth])
    known.discard(())
    allowed_tops = set(RENDER_TOP_BINS) | set(LEGACY_CLIP_BINS)

    planned_bins = []
    for bin_name in bin_names:
        path = tuple(b for b in bin_name.split("/") if b)
        if len(path) != 2 or path[0] not in allowed_tops:
            raise ProofRemovalRefused(
                f"{bin_name!r} is not a per-reel bin under a render "
                f"bin. Nothing was removed.")
        if path not in known:
            raise ProofRemovalRefused(
                f"{bin_name!r} is not in the pool - there is no exact "
                f"match to prove. Nothing was removed.")
        subtree = [a for a in artefacts
                   if tuple(a.folder_path)[:len(path)] == path]
        holders = [a for a in subtree if a.kind == "timeline"]
        if holders:
            raise ProofRemovalRefused(
                f"{bin_name!r} holds timeline {holders[0].name!r} - "
                f"retiring it would strand that timeline. Nothing was "
                f"removed.")
        # A clip placed ONLY on the timeline going down with the bin
        # goes with it: the one DeleteClips call takes the timeline
        # and these together, so nothing that survives still plays
        # them.  Anything a surviving timeline still plays refuses.
        placed = [a for a in subtree
                  if a.placed_by and set(a.placed_by) != {timeline_name}]
        if placed:
            raise ProofRemovalRefused(
                f"{placed[0].name!r} in {bin_name!r} is still placed on "
                f"{min(set(placed[0].placed_by) - {timeline_name})!r}. "
                f"Nothing was removed.")
        foreign = [a for a in subtree
                   if not is_generated(a.file_path, project_root)]
        if foreign:
            raise ProofRemovalRefused(
                f"{foreign[0].name!r} in {bin_name!r} is not "
                f"pipeline-generated. Nothing was removed.")
        deeper = [b for b in known
                  if len(b) > len(path) and b[:len(path)] == path]
        if deeper:
            raise ProofRemovalRefused(
                f"sub-bin(s) {', '.join('/'.join(d) for d in sorted(deeper))} "
                f"stand under {bin_name!r}. Nothing was removed.")
        planned_bins.append({
            "path": path,
            "why": (bin_why.format(timeline_name=timeline_name,
                                   count=len(subtree))),
            "contents": [{"item_id": a.item_id, "name": a.name,
                          "file_path": a.file_path,
                          "folder": "/".join(a.folder_path)}
                         for a in sorted(subtree, key=lambda a: a.name)],
        })

    untouched = []
    for name in sorted(untouched_names | {master_name}):
        present = [a for a in artefacts
                   if a.kind == "timeline" and a.name == name]
        if not present:
            raise ProofRemovalRefused(
                f"protected timeline {name!r} is not in the pool - "
                f"the pool is not what this removal was proven "
                f"against. Nothing was removed.")
        untouched.append({"name": name,
                          "folder": "/".join(present[0].folder_path)})

    planned_bins.sort(key=lambda e: ([-len(e["path"])] + list(e["path"])))
    return {
        "timeline": {"item_id": target.item_id, "name": target.name,
                     "folder": "/".join(target.folder_path)},
        "bins": planned_bins,
        "verified_untouched": untouched,
    }


def plan_proof_removal(
        artefacts: Sequence[Artefact],
        bin_paths: Sequence[Sequence[str]],
        *,
        timeline_name: str,
        bin_names: Iterable[str],
        project_root: str,
        master_name: str) -> dict:
    """The proof timeline and bins to remove, proven before anything runs.

    Pure: takes the artefacts off `read_pool` and the full bin tree,
    returns `{"timeline": ..., "bins": [...], "verified_untouched":
    [...]}`.  Raises `ProofRemovalRefused` on anything unproven -
    including a protected name, a non-proof name, a timeline or bin
    that is not in the pool, and bin contents that are not all
    unplaced generated clips.
    """
    return _plan_removal(
        artefacts, bin_paths,
        timeline_name=timeline_name, bin_names=bin_names,
        project_root=project_root, master_name=master_name,
        authorised=is_authorised_demo,
        foreign_hint="demo artefact (firstmate proof, or an exact "
                     "authorised demo name)",
        bin_why=("firstmate's proof caption bin for {timeline_name!r}, "
                 "removed under the captain's 2026-09-10 authority; "
                 "{count} item(s) go with it"),
        untouched_names=PROTECTED_TIMELINES)


def plan_superseded_removal(
        artefacts: Sequence[Artefact],
        bin_paths: Sequence[Sequence[str]],
        *,
        timeline_name: str,
        bin_names: Iterable[str],
        project_root: str,
        master_name: str) -> dict:
    """One superseded Reel 09 timeline and its per-reel bins, proven.

    Same shape and same proof as `plan_proof_removal`, under the
    captain's 2026-09-10 cleanup brief (*"can you go through and clean
    up the timelines and associated assets and bins that are not the
    main reel 9 timeline? (and obviously the geo podcast synced
    timeline)"*).  Only the two exact `SUPERSEDED_REEL_TIMELINES`
    names are authorised; `(final)` and the master are verified
    present and untouched around the removal, and every bin content
    still played by a surviving timeline refuses - which is what
    proves media shared with `(final)` stays.
    """
    return _plan_removal(
        artefacts, bin_paths,
        timeline_name=timeline_name, bin_names=bin_names,
        project_root=project_root, master_name=master_name,
        authorised=is_authorised_superseded,
        foreign_hint="superseded reel (one of the two exact "
                     "captain-authorised Reel 09 timeline names)",
        bin_why=("superseded reel's per-reel bin for {timeline_name!r}, "
                 "removed under the captain's 2026-09-10 cleanup "
                 "authority; {count} item(s) go with it"),
        untouched_names=frozenset([FINAL_REEL_TIMELINE]))
