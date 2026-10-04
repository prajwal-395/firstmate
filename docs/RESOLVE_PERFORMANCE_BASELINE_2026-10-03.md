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

## Concurrent re-run after the inventory lease

Re-run on main `a8c07ff7`, after `timeline_inventory` began holding a shared lease (https://github.com/prajwal-395/video_editing_pilot/pull/1616). Reels 01, 02 and 03 were submitted at once, one per copy-on-write project tree, into one scratch Resolve project imported from an export of the captain's project. Each build ran `build-reels --only-reel N --rebuild-all`.

| | Baseline | Re-run |
|---|---:|---:|
| Inventory refusals (`timeline N returned no object`) | 2 of 3 | 0 of 3 |
| Builds reaching conformance | 1 | 3 |
| Conformance verdict | Reel 03 failed | Reels 01 and 02 passed; Reel 03 failed |
| Wall to all finish | 254.67s | 467s |
| Broker jobs | 33 | 54 |
| Broker hold / exclusive hold | 132.520s / 127.485s | 266.593s / 246.730s |
| Broker utilization | 51.46% | 54.99% |
| Broker wait p50 / p95 | 0.007s / 36.361s | 0.001s / 43.300s |
| Broker hold p50 / p95 | 0.120s / 33.575s | 0.702s / 37.839s |
| Per-run Resolve wait | 36.795s, 40.222s, 80.424s | 102.046s, 91.830s, 109.666s |

Wall, jobs and per-run wait rose because all three builds now place, verify and attempt promotion, where two of the baseline's stopped at the inventory read. All three still returned nonzero, and neither cause is the race:

- Reels 01 and 02 passed conformance, then promotion refused. The copied timelines carry a disabled motion-graphics item named `mg_geo-podcast_<hash>.mov`, which the editor-change carry could not match to the staged `mg_c1_<hash>.mov`. Motion-graphics files are named after the project folder, so a clone whose folder is not named `geo-podcast` cannot promote over the captain's timelines. This is an artefact of the benchmark setup.
- Reel 03 failed F25 `played_not_captioned`: 12 spoken words at 20.48-22.77s ("what's the best CRM if I run a 10 person law firm?") have no caption over them. All 25 planned captions were placed (`captions 25/25`), so the gap is in the caption plan, not in placement. It reproduces the baseline's Reel 03 failure and does not come from concurrency.

## Remaining scenarios

The Reel 01 render produced a 77.6 MB, 1080x1920 MP4 in 109.64s of wall, with 109.493s held in Resolve. `verify_render` took 5.2s and failed four checks: audio was -23.71 LUFS (target -14 LUFS), one undeclared black segment, 3.210s of picture over digital silence, and 23.98fps. Freeze-frame, resolution, and audio-stream checks passed. The build, render, and QA phases were measured at separate times, so 298.91s is their component sum, not one contiguous completion time.

The master edit dry run on a copy of `post a day keeps the apple away/001` was blocked by three untranslated timeline notes (zoom blur, B-roll choice, subtitle size). The edit runner refuses a real run until every note has a recorded typed edit spec. No spec was invented or suppressed for a performance run, and no Resolve call was made for this scenario.

## After the 2026-10-03 fixes

Re-run on current `main`, SHA `d5cc5732` (PR 1623), using copy-on-write clones and disposable Resolve projects imported from the captain's export. Detailed timestamps, commands, broker windows, and failure messages are in [the raw measurement log](benchmarks/2026-10-03-after.md), with command records in [JSONL](benchmarks/2026-10-03-after.jsonl). A second grant was used for master 001. Its remapped retry created a timeline but failed timeline-sync QA on stale subtitle overlays before export or render. Fresh clones for the concurrent batch were prepared, but the batch was not started before the 25-minute stop point.

| Scenario | Before (wall; Resolve hold; wait p50/p95; failures / outcome) | After (wall; Resolve hold; wait p50/p95; failures / outcome) |
|---|---|---|
| One Reel 01 build | 184.07s; 61.738s total hold; 0.001s / 0.002s per-run wait; returned 0 with 2 warnings. | 174.95s; 51.661s total hold (47.654s exclusive); 0s / 0s; returned 1. Promotion refused because the existing Reel 01 had a disabled Semantic graphic that did not match the enabled staged graphic. The shorter wall time is not a successful build. |
| Reels 01-03 concurrently | Closest pre-fix run after PR 1616: 467s to all finish; 266.593s hold; 0.001s / 43.300s wait; all 3 returned nonzero (two promotion refusals, Reel 03 conformance failure). The earlier inventory-race baseline was 254.67s, 132.520s hold, 0.007s / 36.361s wait, with the same 3 nonzero results. | First-window attempt: 213.331s to all finish; 115.460s hold (105.784s exclusive); 0s / 3.896s wait; all 3 returned 1. It was contaminated by staging debris, a disabled Semantic graphic and a caption conformance failure. Fresh clones were prepared in the second window, but no clean placement-plus-verification run started before the stop point; clean after wall, hold, waits and outcome are unavailable. |
| Reel 01 render + `verify_render` | 109.64s render + 5.2s QA; 109.493s render hold; 0s / 0s wait; QA failed loudness (-23.71 LUFS), black, picture over silence, and frame rate. | 127s render + about 6s QA; 119.846s hold; 0s / 0s wait; hold p50/p95 0.616s / 119.230s. QA failed on one undeclared black segment and 3.188s of picture over digital silence. Loudness was -14.22 LUFS / -1.45 dBTP; freeze, 1080x1920 resolution, 23.98fps frame rate, and audio stream passed. Render wall was 17.36s longer than before. |
| Master 001 build + render + QA | About 6 minutes for this phase; the prior measured render held Resolve 274.5s and waited 249s for the heavy-work grant inside that hold. Wait p50/p95 for the full scenario were not reported; the source figure does not separately record a failure outcome. | Remapped retry: 16.651s to timeline-sync QA failure; Resolve hold 16.647s across 2 jobs; wait p50/p95 0s / 0s. Eight V3 subtitle overlays were missing because their cloned artifacts had no `tight_box` placement. The attempt failed before export/render, so render QA and a complete master phase remain unmeasured. |

The Reel 01 output shows a concrete QA change consistent with PR [1622](https://github.com/prajwal-395/video_editing_pilot/pull/1622): the actual 23.98fps render now passes the frame-rate check, while the prior record marked that rate as a failure. This is a QA correctness result, not a speed gain. The render itself took 17.36s longer. PR [1623](https://github.com/prajwal-395/video_editing_pilot/pull/1623) cannot be credited with removing a wait from this Reel 01 workload: the before and after Reel 01 render windows both reported zero wait, and the master retry failed before export/render, so its previous 249s wait was not remeasured. PR [1621](https://github.com/prajwal-395/video_editing_pilot/pull/1621) cannot be evaluated from the contaminated concurrent attempt; the clean batch was not run. No measured runtime change is attributed to PRs [1615](https://github.com/prajwal-395/video_editing_pilot/pull/1615), [1619](https://github.com/prajwal-395/video_editing_pilot/pull/1619), or [1620](https://github.com/prajwal-395/video_editing_pilot/pull/1620) from these runs.

The single Reel 01 promotion refusal was reproduced on a scratch clone. A read-only inspection of the captain's live `Podcast (field test)` project also found the same disabled asset, `mg_geo-podcast_e1b5df7a.mov`, on video track 6 at frames 577-587 with `enabled=false`. This confirms the condition exists on the captain's actual Reel 01; promotion was not attempted there, so the refusal itself was only observed on the clone.

For the earlier `d5cc5732` after-fixes run, the 55-minute vision analysis, 9-minute temporal index, and 42-minute LLM planning figures were carried forward unchanged and not rerun. That gives 106 minutes before its render phase. Since that build/render/QA phase did not complete, its end-to-end total is **106 minutes plus an unmeasured render phase**. The current-head `d254af4` run below separately reached final QA.

## Reproduction

Benchmark commands require a granted live Resolve window, a disposable Resolve project, and copy-on-write project clones. The captured source project is `geo-podcast`; the master-edit source is `post a day keeps the apple away/001`. Reel 01 was individually built; Reels 01-03 were also submitted simultaneously. No build runs on the source project tree.

Use `ren profile <project> --run <run-id> --json` for per-run timing. Use the broker's bounded KPI window over the same run's Unix-epoch start and end: `ren resolved kpi --since <start> --until <end> --json`. The broker wait and hold percentiles are computed over receipts in that window; utilization is held wall time divided by window wall time.

A project tree's `pipeline_data.json` records its own absolute `project_folder`, and `build-reels` reads `project.yaml` from that recorded folder rather than from the path it was given. A clone must rewrite that path to point at itself, or the build reads, and could write, the source project. Name each clone's folder `geo-podcast`, in separate parent directories, so its motion-graphics filenames match the copied timelines.

### Master 001 subtitle follow-up

The eight missing V3 captions were not a clone-only gap. Read-only inspection of the source project's `pipeline_data.json` and a fresh copy-on-write clone found the same eight `render_subtitles` and `compile_manifest` rows, with neither `geometry` nor `tight_box`; the source state dates to 2026-08-30. All eight MOVs are 1080x1920, matching the manifest's 1080x1920 frame. The builder treated these legacy, full-frame overlays as unknown tight canvases and skipped them. It now accepts an unannotated legacy caption only when Resolve reports media dimensions exactly equal to the requested frame, so it places these overlays without a transform while still refusing unknown or mismatched dimensions. An offline Resolve double reproduced all eight V3 timeline-sync misses from the clone before the fix; the regression test exercises those names through placement and timeline-sync QA.

## Master 001 current-head benchmark snapshot

The master 001 benchmark at measured commit `d254af4` reached final QA. Fresh
analysis/planning took 3h04m through manifest compilation (7,748.333s semantic,
469.338s temporal, 2,834.782s planning), versus the prior complete run's
rounded 106m for those phases. Fresh placement refused an iPhone clip with no
declared program audio stream. The corrected warm clone changed its stream
declaration, so it is not a strict same-input comparison; its analysis/planning
took 33m23s, placement/render 114.579s, and validation 255.782s. Render
completed, but final validation failed on persistent black matte around the
picture and seven subtitles above 25 characters/second. `verify_render` and
`verify_timeline` passed. The validate shell needed Resolve scripting variables
set by hand; no product fix was made. `origin/main` later advanced through PRs
1635-1638 and 1641, so these are not measurements of those commits. Full
results, grant windows, caveats and raw records are in
[the benchmark report](benchmarks/2026-10-03-current-head.md).
