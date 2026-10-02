"""Conflict algebra: whether two prepared EditPatches can both land.

The single-Resolve plan (captain, 2026-10-02): many agents plan edits at
once against one timeline, so "both touch the timeline" is too coarse a
reason to refuse. Each capability declares how its patches compose
(`capabilities.PATCH_SEMANTICS`), each operation knows what it writes
(`edit_patch.OPERATIONS`), and `compose(earlier, later, base)` decides,
for a `later` patch planned on the same base as an `earlier` one that
lands first:

    commute    no write of one meets a write of the other: either order
               gives the same timeline
    merge      every write that meets is the SAME write: the later one is
               already true, and judging it by read-back still passes
    serialize  writes meet under `ordered` semantics: the later one is
               meant to follow, so it applies after, never before
    rebase     the earlier one ripples past where the later one acts, on
               clips it addresses by id: its spans are re-measured on the
               head before it applies
    conflict   anything else - a value both write differently (`replace`),
               an `exclusive` write met at all, a `global` effect on a
               shared domain, or a frame-addressed write past a ripple

Two writes MEET when one's key is a prefix of the other's: a marker is
keyed by frame, a property by clip and name, a deletion by the whole
clip - so deleting a clip meets every write to it, in any domain. The
semantics two meeting writes compose under is the strictest of the two
operations' own and the two capabilities' declared ones.

The vocabularies are ordered, least to most constraining:

    TEMPORAL_EFFECTS  local < ripple < global
    MERGE_SEMANTICS   commutative < ordered < replace < exclusive

A declaration must be true (AGENTS.md 3): a capability may declare
stricter semantics than its operations have - that only costs
concurrency - never looser, because a scheduler that trusts the looser
one lets a lost update through. `problems()` is that check, run by
`library/tools/contract_audit.py`.

Today no operation ripples and none is `ordered`, so `serialize` and
`rebase` are reached only through a capability that declares them; the
verdicts exist so the operation that needs one does not have to invent
its own.

`tests/unit/resolve/test_edit_patch.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from library.tools import edit_patch
from library.tools import timeline_shadow as shadow

TEMPORAL_EFFECTS = ("local", "ripple", "global")
MERGE_SEMANTICS = ("commutative", "ordered", "replace", "exclusive")

COMMUTE, MERGE, SERIALIZE, REBASE, CONFLICT = (
    "commute", "merge", "serialize", "rebase", "conflict")
#: Least to most constraining; a pair's verdict is its worst.
VERDICTS = (COMMUTE, MERGE, SERIALIZE, REBASE, CONFLICT)


@dataclass(frozen=True)
class Composition:
    verdict: str
    reason: str


def semantics_of(patch: edit_patch.EditPatch):
    from library.tools import capabilities
    return capabilities.PATCH_SEMANTICS.get(patch.capability)


def _meet(a: tuple, b: tuple) -> bool:
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _strictest(*semantics: str) -> str:
    return max(semantics, key=MERGE_SEMANTICS.index)


def compose(earlier, later, base: dict) -> Composition:
    """How `later` lands once `earlier` has; both planned on `base`."""
    earlier = edit_patch.EditPatch.from_dict(earlier)
    later = edit_patch.EditPatch.from_dict(later)
    if (earlier.project, earlier.timeline) != (later.project, later.timeline):
        return Composition(COMMUTE, "they address different timelines")
    a, b = semantics_of(earlier), semantics_of(later)
    found = [Composition(COMMUTE, "no write of one meets a write of the "
                                  "other")]

    shared = set(earlier.conflict_domains) & set(later.conflict_domains)
    if shared and "global" in (a.temporal_effect, b.temporal_effect):
        return Composition(CONFLICT, (
            f"a global effect on {sorted(shared)} meets the other patch "
            f"wherever it acts"))

    if a.temporal_effect == "ripple":
        point = min(span[0] for span in earlier.affected_spans)
        for op in later.operations:
            spec = edit_patch.OPERATIONS[op["op"]]
            span = spec.span(op, base)
            if span is None:
                return Composition(CONFLICT, (
                    f"{op['op']} targets clip {op.get('unique_id')!r}, "
                    f"which is not exactly once on the generation before "
                    f"{earlier.id!r}"))
            if span[1] <= point:
                continue
            if not spec.item:
                return Composition(CONFLICT, (
                    f"{op['op']} addresses frame {op.get('frame')}, past "
                    f"where patch {earlier.id!r} ripples from ({point}), "
                    f"and a frame does not move with the cut"))
            found.append(Composition(REBASE, (
                f"patch {earlier.id!r} ripples from frame {point}, past "
                f"clip {op['unique_id']!r}: its span is re-measured")))

    for mine in earlier.operations:
        mine_spec = edit_patch.OPERATIONS[mine["op"]]
        for theirs in later.operations:
            theirs_spec = edit_patch.OPERATIONS[theirs["op"]]
            key = theirs_spec.key(theirs)
            if not _meet(mine_spec.key(mine), key):
                continue
            semantics = _strictest(a.merge_semantics, b.merge_semantics,
                                   mine_spec.merge, theirs_spec.merge)
            what = (f"{mine['op']} and {theirs['op']} both write "
                    f"{list(key)} under {semantics!r}")
            if semantics == "exclusive":
                return Composition(CONFLICT, what)
            if mine == theirs:
                found.append(Composition(MERGE, what + ", identically"))
            elif semantics == "ordered":
                found.append(Composition(SERIALIZE, what))
            elif semantics == "replace":
                return Composition(CONFLICT, what + ", with different "
                                                    "values: a lost update")
    return max(found, key=lambda c: VERDICTS.index(c.verdict))


def carry(patch: edit_patch.EditPatch, store: shadow.ShadowStore,
          project: str, timeline_id: str) -> tuple:
    """`(patch moved onto the head, None)` or `(None, why not)`.

    Composes `patch` with every generation recorded since its base, in
    order. An `observed` generation refuses: its change is unattributed,
    so nothing can say what it meets. The moved patch must then validate
    on the head and its preconditions hold there.
    """
    moved = patch
    base_snap = store.snapshot(store.get(project, timeline_id,
                                         patch.base_generation))
    head = None
    for gen in store.history(project, timeline_id,
                             after=patch.base_generation):
        head = gen
        if gen.source == shadow.OBSERVED:
            return None, (f"generation {gen.generation} is a change nobody "
                          f"recorded (a hand edit), so nothing can say "
                          f"what it meets")
        found = compose(gen.patch, moved, base_snap)
        if found.verdict == CONFLICT:
            return None, (f"generation {gen.generation} (patch "
                          f"{gen.patch['id']!r}): {found.reason}")
        head_snap = store.snapshot(gen)
        if found.verdict == REBASE:
            moved = _respan(moved, gen.patch, base_snap, head_snap)
        moved = replace(moved, base_generation=gen.generation)
        base_snap = head_snap
    if head is None:
        return moved, None
    try:
        edit_patch.validate(moved, base_snap)
    except edit_patch.PatchRefused as refused:
        return None, refused.what
    failures = edit_patch._judge(moved.preconditions, base_snap, base_snap)
    if failures:
        return None, "; ".join(failures)
    return moved, None


def _respan(patch, earlier: dict, base: dict, head: dict):
    """`patch` with every span past `earlier`'s ripple re-measured."""
    point = min(span[0] for span in earlier["affected_spans"])
    kept = [s for s in patch.affected_spans if s[1] <= point]
    for op in patch.operations:
        spec = edit_patch.OPERATIONS[op["op"]]
        if spec.span(op, base)[1] > point:
            span = spec.span(op, head)
            if span is not None:
                kept.append(tuple(span))
    return replace(patch, affected_spans=tuple(kept))


def problems(declarations: dict | None = None,
             capability_ids=None) -> list:
    """Every capability declaration its own operations break."""
    from library.tools import capabilities

    if declarations is None:
        declarations = capabilities.PATCH_SEMANTICS
    if capability_ids is None:
        capability_ids = capabilities.ids()
    out = []
    for cap_id, sem in declarations.items():
        name = f"PATCH_SEMANTICS[{cap_id!r}]"
        if cap_id not in capability_ids:
            out.append(f"{name} names no registered capability")
        if sem.temporal_effect not in TEMPORAL_EFFECTS:
            out.append(f"{name}: temporal_effect {sem.temporal_effect!r} "
                       f"is not one of {TEMPORAL_EFFECTS}")
            continue
        if sem.merge_semantics not in MERGE_SEMANTICS:
            out.append(f"{name}: merge_semantics {sem.merge_semantics!r} "
                       f"is not one of {MERGE_SEMANTICS}")
            continue
        unknown = set(sem.conflict_domains) - edit_patch.CONFLICT_DOMAINS
        if unknown:
            out.append(f"{name}: conflict domains {sorted(unknown)} are "
                       f"not in edit_patch.CONFLICT_DOMAINS")
        if not sem.operations:
            out.append(f"{name} declares no operations: a capability "
                       f"that authors no patch has no declaration")
        for op_name in sem.operations:
            spec = edit_patch.OPERATIONS.get(op_name)
            if spec is None:
                out.append(f"{name}: operation {op_name!r} is not in "
                           f"edit_patch.OPERATIONS")
                continue
            if spec.domain not in sem.conflict_domains:
                out.append(f"{name}: {op_name} writes {spec.domain!r}, "
                           f"which it does not declare")
            if (TEMPORAL_EFFECTS.index(spec.temporal)
                    > TEMPORAL_EFFECTS.index(sem.temporal_effect)):
                out.append(f"{name}: declares {sem.temporal_effect!r}, and "
                           f"{op_name} is {spec.temporal!r}")
            if (MERGE_SEMANTICS.index(spec.merge)
                    > MERGE_SEMANTICS.index(sem.merge_semantics)):
                out.append(f"{name}: declares {sem.merge_semantics!r}, and "
                           f"{op_name} is {spec.merge!r} - a scheduler "
                           f"trusting the declaration would let a lost "
                           f"update through")
    return out
