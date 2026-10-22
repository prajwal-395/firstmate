# The generation that was not the Resolve id

The evidence behind VC-009: what a version creation costs, and the
reconstruction of an unchanged generation that it must not pay twice.

This is the write-up for
`library/tools/versions/replaced_generation.py` (the generation's
identity and the reuse), `library/tools/versions/overhead.py` (what a
version creation costs, per edit type) and
`docs/VERSION_OVERHEAD.md` (the measurement and the before/after). Where
this file and the code disagree, the code and its docstring win.

## The incident

PR 3109 made every replacing path archive the generation it replaces, and
VC-002's rule was: *"A generation is archived ONCE, at a path of its own."*
The path of its own was named by the generation's **Resolve id**, and the
module said why: *"A Resolve id is unique per timeline object, and a
timeline object is one generation."*

That premise is false. One timeline OBJECT holds several GENERATIONS over
time - the captain's hand edit, a build, a touch - and every one of them
has to be recoverable on its own.

**Measured, on the Ren-owned scratch copy `vc004-hand-edit`** (one reel,
one Resolve timeline object `e4e3cf87-…-d20061a60e06`, four recorded
versions):

- the ledger held **EIGHT archive files** for that one object - 175,757
  bytes - and versions 2 and 3 carried the SAME rows digest
  (`12301caa39f97816`), the same generation, each with a full export of
  it;
- version 1's receipt named `…--ee92623a0b.drt` with the hash
  `4f227da7…`, and the file on disk hashed `9c2dcac1…`: **the export of
  the next generation had overwritten it**, so version 1 could not be
  restored and nothing said so - the ledger still carried a receipt that
  no longer held.

So one generation was reconstructed five times, and one of those
reconstructions destroyed the archive of a state that was still in the
ledger. The cost was paid in the only currency that matters for a
recovery archive: the archive of the state before the captain's hand edit
was gone.

## The rule

A generation is **(Resolve id, rows digest, look digest)**:

- the id names the timeline OBJECT;
- the rows digest says what that object PLAYS (names, spans, enabled
  state);
- the look digest says what it LOOKS LIKE (the grade, the Fusion comps,
  the composite settings).

The archive file is named by the generation, so two generations of one
object cannot land on one file. And an archive that already holds this
generation is **REUSED** rather than exported again - but only after the
SAME read-back the export path gets (identity, structure, media), so the
archive that is kept is proven to hold this generation and to carry its
media. The reuse replaces the EXPORT, never the proof: a restore from a
reused archive is still a restoration, and a version whose archive cannot
be found is still unrestorable, which is why the reuse is refused rather
than assumed.

The LOOK is in the key because the rows do not carry it. A captain's hand
edit that changes only the grade keeps every row, so an archive of the
generation BEFORE the grade reads as this one - and a restore from it
brings back the wrong picture. VC-004 (PR 3096) landed the look on every
adopted version while this work was open; this branch digests it and makes
it part of the generation's name, and keeps VC-004's own per-adoption
discriminator for the export, so a changed generation never lands on an
earlier one's file.

The paths that share the routine: the promotion
(`library/tools/reel_build.py`), the touch (`library/tools/reel_touchup.py`),
the variant choice (`library/tools/versions/variants.py`) and the adoption
(`library/tools/reel_adoption.py`).

## What it costs, and what the cut saves

The punchlist asks for three costs per edit type - the state that is
COPIED (the staging duplicate a build or a touch edits), ARCHIVED (the
`.drt` export of the whole timeline) and RECONCILED (the rows snapshot,
the digest, the ledger write) - and asks that the numbers carry their
denominators.

`library/tools/versions/overhead.py` measures them with the real
functions the paths use, on the scratch copy, and records a row per
measurement in the project's ledger
(`pipeline_output/review/version_overhead.json`) with the denominators the
numbers are read against: items, media files, media bytes, versions
before and after.

**Before and after, on the copy above** (one reel, one Resolve timeline
object, four recorded versions, two distinct generations):

| | before | after |
|---|---|---|
| archive exports of one generation | 5 | 1 |
| archive bytes written for one reel | 175,757 | 22,553 |
| versions whose receipt no longer verifies | 1 of 4 | 0 of 4 |
| an adoption of an unchanged generation | 1 export, 22,525 bytes | 0 exports, 0 bytes |
| a promotion of a generation already archived | 1 export | 0 exports |
| an archive of the same rows, another grade | exported again | exported again - the look is part of the key |

The live seconds per bucket were not taken on the day this was written:
the machine's broker queue was saturated by other lanes, the single
scratch-origin slot was held by another writer, and the serving broker
then stopped - and a lane may not start one from its worktree. The
measurement harness is the route to take them; the counts and the bytes
above are measured on a real scratch copy.

## The tests

`tests/unit/reels/test_vc009_branch_overhead.py` - one defect per test:

1. a generation that is already archived is not exported again;
2. a reused receipt carries the evidence an exported one carries (the same
   file, the same content identity, the same rows digest, the same media
   manifest);
3. an archive of ANOTHER generation is not reused (the same Resolve id with
   different rows is a different generation);
4. an archive of ANOTHER LOOK is not reused (the same rows with another
   grade is a different generation);
5. an archive whose bytes changed is not reused;
6. an adopted generation gets a file of its own, and the archive of the
   generation before it still verifies;
7. an adoption of an unchanged generation does not export again;
8. the measurement records a denominator.
