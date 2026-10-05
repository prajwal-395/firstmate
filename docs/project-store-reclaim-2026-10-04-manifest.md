# Project-store reclaim manifest - 2026-10-04 (caption renders, second pass)

Store: /Users/prajwal/Documents/content_stuff/video_projects/lucie/geo-podcast
Scope: `pipeline_output/steps/4_05_render_subtitles` only - the 1.5G the 2026-09-18 sweep
deliberately excluded. Measured 2026-10-04 23:55 UTC through 2026-10-05 00:15 UTC.

## Outcome in one paragraph

The 4_05 directory held **160 superseded caption files (0.073 GiB, 14 distinct cards)** that
nothing referenced. Every one was cross-checked against planned reel builds and proven dead.
Before this pass could reclaim them, the Reel 23 rebuild's own GC sweep (2026-10-05T00:14:25Z)
collected them into quarantine along with 33 more (193 assets, 0.11 GiB total). The directory
is now clean: **4704 live assets, 2.10 GiB, 0 orphans**. No reclamation was performed by this
pass; the normal build process did it. The GC orphan-check defect that broke the Reel 28 build
(below) is **still present** and is being fixed on `fm/vep-caption-gc-orphan-fix`.

## Inventory (read-only mark, 2026-10-05T00:08:55Z)

Method: `ren purge <project> --db <Project.db>` (plan mode, nothing removed) and
`python3 -m library.tools.caption_asset_gc mark` (read-only reachability). The Resolve database
was read through a copy (`/tmp/resolve_check.db`), never the live file.

| measure | value |
|---|---|
| 4_05 files at session start | 4743 (4705 assets + render_ledger.json + step records), 2.1 GiB |
| LIVE (referenced) | 4627 assets, 2.075 GiB |
| ORPHAN candidates | **160 files, 0.073 GiB** (40 mov + 120 siblings), 14 distinct cards |
| store total (context) | 29 GiB |

Roots that saved the live assets: `resolve:Podcast (field test)` 3353 assets (1131 placed paths,
34 timelines), `pipeline:render_subtitles` 1274 assets (771 referenced paths). The
`pipeline:assembly-manifest` root is `no-record` (this project never wrote one).

## The 14 orphan cards - all superseded generations of Reel 21 / Reel 23

Every orphan card's **newer generation is placed on a live timeline**; the orphan is an older
generation of the same card. Live Resolve places e.g.
`sub_akshita_02b8b2bb-74c0-4d19-9cab_3568664-3581364_5c21426d.mov` while the orphan is
`..._00d584a0.mov`. Zero digest overlap between orphan files and live-placed files.

| card (clip + source span) | reel | orphan files | orphan movs |
|---|---|---|---|
| sub_akshita_02b8b2bb..._3568664-3581364 | Reel 21 | 24 | 6 |
| sub_akshita_02b8b2bb..._3581644-3588454 | Reel 21 | 12 | 3 |
| sub_akshita_02b8b2bb..._3595214-3606974 | Reel 21 | 20 | 5 |
| sub_akshita_e04dc896..._3618311-3622451 | Reel 21 | 4 | 1 |
| sub_akshita_e04dc896..._3622451-3632371 | Reel 21 | 12 | 3 |
| sub_craig_55943966..._3558309-3563029 | Reel 21 | 4 | 1 |
| sub_akshita_a4fae558..._3875008-3880308 | Reel 23 | 8 | 2 |
| sub_akshita_a4fae558..._3880528-3887968 | Reel 23 | 16 | 4 |
| sub_akshita_a4fae558..._3888398-3895698 | Reel 23 | 8 | 2 |
| sub_akshita_a4fae558..._3896418-3900468 | Reel 23 | 4 | 1 |
| sub_akshita_a4fae558..._3909678-3927268 | Reel 23 | 32 | 8 |
| sub_akshita_a4fae558..._3929848-3931738 | Reel 23 | 4 | 1 |
| sub_akshita_a4fae558..._3931738-3937058 | Reel 23 | 8 | 2 |
| sub_akshita_a4fae558..._3939198-3941007 | Reel 23 | 4 | 1 |

Every orphan mov is named as `superseded` by a newer ledger entry, or is an intermediate
generation bound to a `rebuild staging` timeline that no longer exists in live Resolve.

## Cross-check against planned reel builds (firstmate caution, 2026-10-04)

Firstmate cautioned that the orphan check is wrong somewhere and every candidate must be
cross-checked against **planned reel builds, not just placed timelines**. Result:

- **No current proposal references any orphan card.** The Reel 21 proposal (23:35Z) and Reel 23
  proposal (00:03Z) name no orphan digest.
- **No in-flight build references any orphan card.** Live Resolve at 00:14:55 has 34 timelines;
  the only `rebuild staging` timelines are Reel 22 and (transiently) Reel 23. Neither places an
  orphan digest. The Reel 23 rebuild staging (verified 00:12:32Z, promoted 00:14:32Z) places the
  newer generations, not the orphans.
- **No reuse-key reference.** The orphan cards' reuse keys appear in no proposal, staging
  record, or phase-log entry.
- The only records naming the orphan cards are **historical Reel 21 / Reel 23 records**
  (September touchups, placements, retirements, undo entries) - records of past builds, not
  current plans.

## The defect this pass did not fix (Reel 28 incident, 2026-10-04 23:30Z)

Timeline of the false orphan:

| time (UTC) | event |
|---|---|
| 23:29:55 | `sub_craig_02d573c7-84c9-4804-9a7a_247477-257517_*` retired from the Resolve pool (`resolve_retirements_20261004T232955Z.json`) |
| 23:30:22 | GC sweep quarantined it - marked orphan: on no live timeline, named by no pipeline record |
| 23:30:34 | Reel 28 rebuild staging build **failed**: "Resolve would not import rendered caption ... refusing to omit a planned caption segment" |

Root cause: the GC's roots are placed timelines + pipeline records. A caption a build in flight
has **selected but not yet placed** reads as an orphan. The Reel 28 build had selected the
asset; the sweep took it; the build broke. The defect is being fixed on
`fm/vep-caption-gc-orphan-fix` (no commits at time of writing). This pass changed no GC code.

## What reclaimed the candidates

The Reel 23 rebuild's own GC sweep (`sweep_subtitle_segments_20261005T001425Z_manifest.md`)
moved **193 assets (0.11 GiB)** to quarantine at 00:14:25Z - the 160 candidates above plus 33
of the Reel 23 rebuild's own superseded generations. The Reel 23 promotion succeeded at 00:14:32Z
(after the sweep), so that sweep was clean. No build failed after it.

## Conclusion

- The 4_05 directory is **drained**: 4704 live assets, 2.10 GiB, 0 orphans (mark at 00:14:55Z).
- The 0.073 GiB of superseded generations this pass identified is in quarantine, reclaimable
  through `ren purge --apply` once the GC defect is fixed and the captain approves.
- The GC orphan-check defect is **open** and is the only thing standing between this store and
  a full reclaim. Fix it before the next sweep, or the next in-flight build loses a caption.
