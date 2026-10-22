# `library.tools.generation_identity` - the evidence behind the durable identity

The captain's development punch list, 2026-10-20, item VC-001: *"Establish
durable version identities."*

The module's docstring is the contract. This file is the evidence: the
measurement that found the gap, what the gap was, and what changed.
Where the two disagree, the code and its docstring win.

## The measurement that found it

On 2026-10-21 the punch list's sibling work (PR 3131) found a scratch
project holding three Resolve timelines under one reel's name. The
project was `Ren Scratch - vep-bench-demo2`, and the name was
`Reel 13 - the-accounting-firm-ai-called-healthcare`:

| Resolve timeline id | what it was |
|---|---|
| `4baf457e-9790-48a8-b6ca-5ff5972f4742` | the reel's first generation |
| `7c842807-5b06-4dc9-ae4a-b3d9d51a2de6` | the generation a touch-up promoted |
| `46183cae-0072-40b2-bcca-b400ebda2328` | the generation the last touch-up promoted |

Three GENERATIONS of one reel, one name, and every write addressed to
that name refused before its first write:

```text
recorded base for 'Ren Scratch - vep-bench-demo2'/'Reel 13 - the-accounting-firm-ai-called-healthcare'
resolved to 3 timelines
```

## What the gap was

The identity of a generation lived nowhere:

- the **timeline shadow** keyed a generation by
  `(project, timeline_id, generation number)` - a durable key, but a
  machine-local pair of integers with no name any other record could
  hold;
- the **undo journal** recorded WHAT REN DID (a promotion names the
  candidate that became the reel's timeline), not WHICH generation it
  made current;
- the **reel version ledger** - the one record a person reads - filed
  every version under the reel's NAME, with no Resolve identity at all.

So the name was still the sole identity, and the AXI's recorded base was
resolved by it:

```python
candidates = [(generation, snapshot)
              for generation, snapshot in store.current_snapshots(project_key)
              if generation.timeline_name == request.timeline]
if len(candidates) != 1:
    raise ValueError("... resolved to %d timelines" % len(candidates))
```

`EditRequest` already carried `timeline_id` - the Resolve timeline's
`GetUniqueId` - and NOTHING filled it in and NOTHING read it.

PR 3131 had taught ONE path (the revise path) to ask the journal, which
is why that path survived a collision and the AXI gate did not. Two
gates, one rule, and the rule was only half-kept.

## The change

One enumeration, `library/tools/generation_identity.py`:

- the identity is `sha256(resolve_project, resolve_timeline_id,
  content_digest)` - the Resolve timeline identity and the generation's
  immutable version metadata, and nothing else. The NAME is not in it,
  so a rename keeps the identity; the timeline id is in it, so two
  timelines that share a name do not collide;
- the identity is DERIVED, never stored: it cannot drift from the state
  it names, and the shadow's shared SQLite schema does not grow a column
  every machine would have to migrate. The project's own mirror
  (`pipeline_output/review/timeline_shadow.json`) carries it, so the
  identity travels with the project;
- the version entry carries it too (`generation` on the entry), with
  the facts VC-001 says it preserves: the PARENT generation (the
  previous state of the SAME timeline - a same-named neighbour is a
  collision, not a parent), the AUTHOR, the ACCEPTED state (read from
  the project's own acceptance record, which already names the Resolve
  identity and the rows digest of what was delivered) and the ASSET
  DEPENDENCIES (the rows' media, completed by the recovery archive's
  hashed manifest);
- a target that is AMBIGUOUS (a name whose history spans more than one
  Resolve timeline) or STALE (a generation a later one replaced) is
  REFUSED, and every candidate is named by its durable id.

Every path that resolves a reel now asks the ONE resolver,
`generation_identity.resolve_timeline_id`: the identity the version
ledger recorded, then the journal (PR 3131), then the shadow - which
still refuses a name two live timelines share, because that is two LIVE
timelines and the engine will not pick one (AGENTS.md 5).

## Measured, before and after, with the denominators

`scripts/vc001_generation_identity_measure.py`, over the Ren-owned
scratch projects, 2026-10-22.

**Generations with a durable identity.**

| population | denominator | before | after |
|---|---|---|---|
| recorded generations (the shadow's) | 4962 | 0 | 4962 |
| version-ledger generations | 21 | 0 | 0 (they predate the change) |
| version-ledger generations the new code records | - | - | 1 of 1 |

The 21 ledger generations were recorded before the change and hold no
Resolve identity to backfill from: their identity is absent evidence,
never an invented one. Every generation the new code records carries
one - measured on a scratch COPY, through the real ledger API
(`--record-sample`).

**Name collisions that block a write.** A collision is a reel name two
or more recorded timelines share. 65 of them, across the scratch
projects.

| gate | before | after |
|---|---|---|
| the AXI's recorded base (main's rule: by name) | 65 blocked | 5 blocked |
| naming the reel (main's rule after PR 3131: journal, then shadow) | 60 blocked | 60 blocked |

The 60 that remain are blocked because NO record names the current
generation: 2 to 21 live timelines share the name and nothing says which
one is meant. That is an AMBIGUOUS target, and VC-001 says to reject it.
The 5 that were blocked at the AXI gate and are not any more are the
ones whose current generation the project's own records name; the
identity resolves them to exactly the timeline the journal named
(`46183cae-...` for `vep-bench-demo2`).

Denominators are never combined here: the two gates are two gates, the
two populations are two populations, and unmeasured is never zero.

## What was NOT done

- The 21 generations recorded before this change carry no identity and
  are not backfilled: the evidence to mint one (the Resolve identity)
  is not in those entries. A backfill would have to invent it.
- A generation the engine only OBSERVED (the captain's hand edit, read
  through the shadow) gets its identity in the mirror, but the mirror is
  not the version ledger: such a generation has no parent, author,
  accepted state or asset dependencies, because no version was recorded
  for it.
- The 60 remaining collisions are refused on purpose, and no evidence
  in those projects says which timeline is current. Making them
  addressable would need a record of the CURRENT generation per name -
  a state, not an identity.
