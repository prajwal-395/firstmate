# What creating a version costs, and the reconstruction it must not pay twice

VC-009, from the captain's punchlist of 2026-10-20 (baseline 97b57197):

> Measure branching and promotion overhead.
> - Determine which state must be copied, archived, or reconciled for each
>   edit type.
> - Avoid repeated full reconstruction of unchanged generations.
> - Compare incremental version creation against full version materialization.
> - Require equivalent restoration and preservation before optimizing away a
>   safety step.

This document is the measurement and the change. The numbers are measured on
Ren-owned scratch copies, never on a captain project, and every one of them
carries its denominator.

The incident behind it - the generation that was not the Resolve id, and the
archive of a state that was destroyed by the export that followed it - is in
`docs/evidence/version_generation_identity.md`.

## The three costs, one per word of the punchlist

Every path that creates a version pays for the same three things, and the
punchlist names them:

| bucket | what it is | who pays it |
|---|---|---|
| **copy** | the new generation is MATERIALIZED - the staging duplicate a build or a touch edits | promotion, touch, variant |
| **archive** | the generation being replaced gets a recovery archive (a `.drt` export of the whole timeline) | every path that replaces a generation, and the adoption |
| **reconcile** | the rows snapshot, the digest, and the version/revision ledger write | every path that creates a version |

The edit types are the paths that create a version: `promotion` (a rebuild
promoted), `touch` (a touch-up promoted), `adopt` (the captain's hand edit,
adopted as a version - there is no copy, the captain edited the live
timeline), and `variant` (a variant build promoted).

`library/tools/versions/overhead.py` is the harness: it measures the three
buckets with the real functions the paths use, and records a row per
measurement in the project's ledger
(`pipeline_output/review/version_overhead.json`) with the denominators the
numbers are read against - items, media files, media bytes, versions before
and after. A number without its denominator is a story, and the harness does
not record one.

    python3 -m library.tools.versions.overhead report --project <folder>

## What the measurement found: one generation, exported again and again

The live evidence is on the machine, on the Ren-owned scratch copy
`vc004-hand-edit` (the `Ren Scratch - vc004` project). ONE reel, ONE Resolve
timeline object, FOUR recorded versions - and EIGHT archive files:

    Ren_Scratch_-_vc004_-_Empty--ee92623a0b.drt                                 22521
    Ren_Scratch_-_vc004_-_Empty--ee92623a0b--adopted-a53dc15b.drt               22525
    Ren_Scratch_-_vc004_-_Empty--ee92623a0b--retired-<uuid>-<timeline id>.drt   22553
    Ren_Scratch_-_vc004_-_Empty--ee92623a0b--custom-<run>.drt  x 5              112158
                                                                 TOTAL          175757

The ledger of that reel, with the receipts as they stand on disk:

| version | kind | rows digest | archive file | the file on disk today |
|---|---|---|---|---|
| 1 | adopt | `6b58dbb7…` | `…--ee92623a0b.drt` | **does not verify** - the file was overwritten |
| 2 | adopt | `12301caa…` | `…--ee92623a0b.drt` | verifies |
| 3 | adopt | `12301caa…` | `…--adopted-a53dc15b.drt` | verifies - a SECOND export of the generation version 2 already had |
| 4 | build | `6b58dbb7…` | `…--retired-<uuid>-<id>.drt` | verifies |

Two defects, both measured on that copy:

1. **A generation was exported five times.** Version 2 and version 3 carry the
   SAME rows digest (`12301caa39f97816`) - the same generation - and the second
   one exported a fresh full copy of it (22,525 bytes) instead of keeping the
   archive that was already there.
2. **One export destroyed the archive of a version that is still in the
   ledger.** Version 1's receipt names `…--ee92623a0b.drt` with the hash
   `4f227da7…`; the file on disk today hashes `9c2dcac1…`. Version 1 cannot be
   restored, and nothing said so - the ledger still carries a receipt that no
   longer holds.

The cause is one wrong premise, stated in the module that owns the archives:
*"A Resolve id is unique per timeline object, and a timeline object is one
generation."* It is not. One timeline OBJECT holds several GENERATIONS over
time - the captain's hand edit, a build, a touch - and every one of those has
to be recoverable on its own.

## The change: a generation is (Resolve id, rows digest, look digest)

`library/tools/versions/replaced_generation.py`:

- **The archive file is named by the generation**, not by the object:
  `gen-<resolve id>-<rows digest>`. Two generations of one object can no
  longer land on the same file, so exporting a changed generation cannot
  destroy the archive of the generation before it (defect 2 above).
- **An archive that already holds this generation is REUSED**, not exported
  again. The reuse is allowed only when the archive's receipt names the same
  Resolve id, the same rows digest, the same LOOK and the same media, when
  the file still hashes true, and when the read-back against the LIVE
  generation passes - the same threefold proof (identity, structure, media)
  the export path gets. Everything else is an export (defect 1 above).

The LOOK is part of the identity because the rows do not carry it: a captain's
hand edit that changes only the grade keeps every row, so an archive of the
generation BEFORE the grade reads as this one - and a restore from it would
bring back the wrong picture. VC-004 (PR 3096, landed while this branch was
open) records the look on every adopted version; this branch digests it and
makes it part of the generation's name, so a reuse can never keep an archive
of an earlier look.

The reuse is the "incremental version creation" the punchlist asks to be
compared against "full version materialization": the incremental path writes
NO new archive and NO new bytes, and it proves the archive it keeps is this
generation's.

The paths that share the routine:

- the promotion (`library/tools/reel_build.py`),
- the touch (`library/tools/reel_touchup.py`),
- the variant choice (`library/tools/versions/variants.py`),
- the adoption (`library/tools/reel_adoption.py`).

## Before and after, with denominators

Denominator: ONE reel, ONE Resolve timeline object, FOUR recorded versions,
TWO distinct generations (by rows digest).

| | before | after |
|---|---|---|
| archive exports of one generation | 5 | 1 |
| archive bytes written for one reel | 175,757 | 22,553 |
| versions whose receipt no longer verifies | 1 of 4 | 0 of 4 |
| an adoption of an unchanged generation | 1 export, 22,525 bytes | 0 exports, 0 bytes |
| a promotion of a generation that is already archived | 1 export (when the archive was attached to an earlier version row) | 0 exports |
| an archive of the same rows but another grade | exported again (a distinct generation) | exported again - the look is part of the key |

The live SECONDS for each bucket are measured by the harness on the
machine's own Resolve, through the serving broker, on this lane's own
scratch copy (`Ren Scratch - vc009`). On the day this was written the
machine's broker queue was saturated by other lanes (19 jobs queued) and
the single scratch-origin slot was held by another writer, so those
numbers are NOT in this document yet. What IS measured here is measured
on a real Ren-owned scratch copy: the export COUNT and the BYTES of the
archives each edit type wrote, before and after.

## What was NOT changed

- The archive is still exported whenever the generation is new or changed.
- The safety step is still there: the archive is still READ BACK and compared
  with the live generation before it is used, and the media is still hashed
  into the receipt. The reuse replaces the EXPORT, never the proof.
- A restore is still a restoration: the reused archive is a `.drt` of the
  exact generation, and the restore still imports it and compares the rows it
  reads back with the version's recorded rows.

## The tests

`tests/unit/reels/test_vc009_branch_overhead.py` - one defect per test:

1. a generation that is already archived is not exported again;
2. a reused receipt carries the evidence an exported one carries (the same
   file, the same content identity, the same rows digest, the same media
   manifest);
3. an archive of ANOTHER generation is not reused (the same Resolve id with
   different rows is a different generation);
4. an archive of ANOTHER LOOK is not reused (the same rows with another grade
   is a different generation);
5. an archive whose bytes changed is not reused;
6. an adopted generation gets a file of its own, and the archive of the
   generation before it still verifies;
7. an adoption of an unchanged generation does not export again;
8. the measurement records a denominator.

`tests/unit/reels/test_vc002_recovery_archives.py` keeps its contract: the
build's export still names the archive by the generation - now by the
generation's id AND the digest of the rows it holds.
