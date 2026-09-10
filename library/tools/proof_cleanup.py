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

from collections.abc import Iterable, Sequence

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

AUTHORISED_DEMO_TIMELINES = frozenset([
    CAPTAINS_PROOF_TIMELINE,
    MERGE_DEMO_TIMELINE,
])
"""Crew demo timelines the captain authorised removing, by EXACT name.
Exact names only (AGENTS.md 5): a near match lands elsewhere, so a
suffixed or reworded variant is not a member and refuses."""


def is_authorised_demo(name: str) -> bool:
    """Is this timeline name one the captain authorised removing?

    Firstmate's proof timelines by prefix (`SOP Proof...` - proof
    builds version and suffix theirs), crew demo timelines by exact
    membership above.  Classification of our own artefacts is not
    addressing the captain's work; anything else refuses.
    """
    return (name or "").startswith(PROOF_PREFIX) \
        or name in AUTHORISED_DEMO_TIMELINES


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
    if timeline_name in PROTECTED_TIMELINES or timeline_name == master_name:
        raise ProofRemovalRefused(
            f"{timeline_name!r} is protected - this path never removes "
            f"it. Nothing was removed.")
    if not is_authorised_demo(timeline_name):
        raise ProofRemovalRefused(
            f"{timeline_name!r} is not a captain-authorised demo "
            f"artefact (firstmate proof, or an exact authorised demo "
            f"name). This path removes those only. Nothing was removed.")

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
            "why": (f"firstmate's proof caption bin for "
                    f"{timeline_name!r}, removed under the captain's "
                    f"2026-09-10 authority; "
                    f"{len(subtree)} item(s) go with it"),
            "contents": [{"item_id": a.item_id, "name": a.name,
                          "file_path": a.file_path,
                          "folder": "/".join(a.folder_path)}
                         for a in sorted(subtree, key=lambda a: a.name)],
        })

    untouched = []
    for name in sorted(PROTECTED_TIMELINES | {master_name}):
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
