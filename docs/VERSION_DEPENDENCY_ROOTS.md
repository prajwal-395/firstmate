# Version dependency roots: an archived generation is a liveness root

**The rule.** A reel version that carries a verified recovery archive is a
generation Ren can put BACK (`ren undo <project> <reel> --restore-version N`).
The `.drt` archive and every media file that generation plays are therefore
DEPENDENCIES, not debris: no cleanup path may name them, and a git-tracked
file is untouchable whatever its class.

One reading, `library/tools/version_dependencies.py`, is used by every cleanup
path that can touch project media. It reads the project's own review area and
nothing else:

| the record | what it names |
|---|---|
| `reel_versions.json` and its pending rollback/redo companions | each version's `recovery_archive` receipt, and the receipt's ASSET MANIFEST (`assets[].path`) |
| the undo journal, the timeline snapshots | each clip's media, under `source_file` |
| every `.drt` recovery archive | the media it references, read out of the archive itself (`resolve_archive.archive_media_paths`) |

The reading FAILS CLOSED: a retained record or archive that exists and cannot
be read is not an empty one, so the cleanup refuses rather than proving
nothing. A receipt written before the current format carries no asset manifest
at all, so the ARCHIVE is read too - the archive is the payload.

## The audit

Every cleanup path that can touch project media, measured on a scratch project
carrying one archived generation (one media file named by the receipt, one
named only by the `.drt`, one disposable scratch file, one git-tracked file).
Denominator in brackets; the script is `/tmp/vc008_measure.py` in the lane's
scratch, and the same shape is the test
`tests/unit/reels/test_version_dependency_cleanup_roots.py`.

| path | before | after |
|---|---|---|
| the pool prune (`resolve-prune`) | dependencies heard [2/2] | dependencies heard [2/2] (unchanged; VC-007) |
| the asset collection (`asset_gc`, delegates to the pool prune) | dependencies heard [2/2] | dependencies heard [2/2] (unchanged) |
| the build sweep (`caption_asset_gc` / `build_sweep`) | **dependencies ORPHANED [2/2]** | dependencies LIVE [2/2], orphan [0/2] |
| the reviewed purge (`ren purge`) | **dependencies removable [2/3]** | dependencies kept [3/3], removable [0/3] |
| the machine storage plan (`ren storage plan`) | **dependencies named as removable [2/2]** | dependencies named as removable [0/2], refused as version dependencies [2/2] |
| the disposable scratch file | removable [1/1] | removable [1/1] (no regression) |
| the git-tracked file | removable [0/1] | removable [0/1] (no regression) |

### The gaps that were found

1. **The build sweep and the reviewed purge did not read the version ledger.**
   Their roots were the live Resolve timelines, the current pipeline records
   and the approved decisions. A caption render an archived generation still
   plays was on no live timeline and in no current record, so it was an ORPHAN:
   the sweep moved it to quarantine and the purge deleted it. The restore of
   Reel 26 (PR 3079) worked only because that archive and its media had
   survived.
2. **The machine storage plan read only the artifact ledger.** A file was
   disposable because nothing in the ledger said otherwise, and the ledger
   says nothing about a version's media. A quarantined caption render that an
   archived generation plays was a purge candidate.
3. **The `.drt` was protected only by its SUFFIX.** `NEVER_PURGE_SUFFIXES` and
   `PROTECTED_SUFFIXES` keep `.drt`/`.drp`/`.otio` out of every class, which
   is right and was never the gap - but the MEDIA the archive names had no
   protection at all.

### The trade-off this makes, stated plainly

The ledger keeps EVERY promoted generation of every reel, and each one that
carries a recovery archive keeps its media alive. That is the punchlist's
order ("never delete footage still referenced by an accepted generation"), and
it is why the captain could put Reel 26 back to version 25 - an OLDER
generation. The cost is disk: the media of superseded generations stay until
the generation itself is retired on purpose. Retiring a generation is a
deliberate act on the ledger; it is not something a sweep may decide.

### Paths audited and found sound
- **The workspace collector (`ren workspace-gc`)** walks Ren's own data home,
  never a project folder, protects every media suffix outright, and refuses
  any path git tracks.
- **The scratch Resolve projects (`resolve_scratch_projects`)** delete Resolve
  PROJECTS and keep the five newest `.drp` exports of each. They never touch
  a project's media, and a project's own archive lives in the project folder.
- **Git-tracked files** are refused by `git_guard` in every one of these paths,
  at plan time and again at delete time.

## What changed

- `library/tools/version_dependencies.py` - the one reading.
- `library/tools/resolve_archive.py` - `archive_media_paths`, a reachability
  read of an archive's media paths that is not gated on the export's
  `DbPrjVer` (the strict inventory still is).
- `library/tools/caption_asset_gc.py` - a new root, `project:archived-generations`.
- `library/tools/retention.py` - the reviewed purge reports the dependencies it
  kept, and says WHICH root kept each one.
- `library/tools/storage_lifecycle.py` - the storage plan refuses to NAME a
  version dependency, and the collect and the purge re-prove it at the moment
  of removal.
- `library/tools/execution/prune_orphans.py` - delegates to the one reading
  (no behaviour change).
