# Resolve performance baseline - 2026-10-03

Measured on recorded main SHA `34cfc31b85bf0b7fbd172a66aa9a697705602cac` in disposable Resolve projects. The captain's `Podcast (field test)` project was saved, left open only for the snapshot, and never edited. Every benchmark uses a copy-on-write filesystem clone and a separate scratch Resolve project. These runs use real project media and Resolve; they are opt-in and do not belong in the normal gate.

## Measurements

| Scenario | Wall | Resolve wait | Hold p50 / p95 | Resolve utilization | Shadow hit rate | Cursor switches | Resolve render time |
|---|---:|---:|---:|---:|---:|---:|---:|
| One reel build + verification (Reel 01) | 184.07s | 0.016s | 0.914s / 33.467s | 33.37% | N/A - no shadow snapshots queried | 2 | 0s |
| Three simultaneous reel builds (Reels 01-03) | 254.67s to all finish | 157.44s cumulative wait; p50 0.007s / p95 36.361s | 0.120s / 33.575s | 51.46% | N/A - no shadow snapshots queried | 6 | 0s |
| Reel 01 build + render + file QA | 184.07s build + 109.64s render + 5.2s QA (298.91s component sum; phases were not contiguous) | 0.016s build wait; 0s render wait | Build 0.914s / 33.467s; render 109.493s / 109.493s | Build 33.37%; render 98.64% | N/A - no shadow snapshots queried | 2 build switches; render profile unavailable | 109.49s |

The one-reel command returned 0. The builder reported zero errors and two warnings; it left one held staging timeline in the disposable project, which was not promoted. The measured capability time was 149.148s for `reel.build`, 25.455s for `reel.verify`, and 1.853s for `reel.ask`. `ren profile` attributed 56.163s to subtitle rendering, 56.088s to other reel build work, and 36.888s to Resolve hold within `reel.build`; verification added 23.926s of Resolve hold. No Resolve render was queued by this build path.

The bounded broker window for the full command reported 15 jobs, 61.738s total hold, 49.471s exclusive hold, 0s wait p50/p95 at displayed precision, 0.914s / 33.467s hold p50/p95, 33.37% utilization, and no shadow snapshot queries. `ren profile`'s capability rows report 0.001s / 0.002s wait p50/p95 for `reel.build`; its per-run hold p50/p95 were 0.036s / 33.466s. `reel.verify` reported 0.001s / 0.001s wait and 2.308s / 13.092s hold p50/p95. The whole-command values above come from the bounded broker window; the per-capability values are shown to keep those scopes distinct.

The concurrent case returned nonzero in all three clients. Two builds refused because the shared project timeline inventory could not be read (`timeline 37 returned no object`); Reel 03 built but failed conformance. Over the 255s window, the broker recorded 33 jobs, 132.520s hold, 127.485s exclusive hold, 51.46% utilization, wait p50/p95 0.007s / 36.361s, and hold p50/p95 0.120s / 33.575s. Per-run profiles counted 36.795s, 40.222s, and 80.424s waiting across the three task processes, respectively. The three reel-build profiles counted two timeline switches each. The task clients used three separate copy-on-write project trees but one scratch Resolve project; the shared cursor and project timeline inventory exposed a concurrency correctness failure, so these figures are a failure baseline, not completed throughput. No Resolve renders or timeline-shadow snapshot queries occurred.

## Remaining scenarios

The Reel 01 render produced a 77.6 MB, 1080x1920 MP4 in 109.64s of wall, with 109.493s held in Resolve. `verify_render` took 5.2s and failed four checks: audio was -23.71 LUFS (target -14 LUFS), one undeclared black segment, 3.210s of picture over digital silence, and 23.98fps. Freeze-frame, resolution, and audio-stream checks passed. The build, render, and QA phases were measured at separate times, so 298.91s is their component sum, not one contiguous completion time.

The master edit dry run on a copy of `post a day keeps the apple away/001` was blocked by three untranslated timeline notes (zoom blur, B-roll choice, subtitle size). The edit runner refuses a real run until every note has a recorded typed edit spec. No spec was invented or suppressed for a performance run, and no Resolve call was made for this scenario.

## Reproduction

Benchmark commands require a granted live Resolve window, a disposable Resolve project, and copy-on-write project clones. The captured source project is `geo-podcast`; the master-edit source is `post a day keeps the apple away/001`. Reel 01 was individually built; Reels 01-03 were also submitted simultaneously. No build runs on the source project tree.

Use `ren profile <project> --run <run-id> --json` for per-run timing. Use the broker's bounded KPI window over the same run's Unix-epoch start and end: `ren resolved kpi --since <start> --until <end> --json`. The broker wait and hold percentiles are computed over receipts in that window; utilization is held wall time divided by window wall time.
