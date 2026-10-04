# Master 001 current-head benchmark - 2026-10-03

## Status

The code measured was commit `d254af447c8c81e7afb0a84bb08bc7bc3cb0cb94`
(the task worktree's launch head). The fresh attempt failed at placement
setup because the clone had no declared audio program stream. A clone-only
declaration enabled a corrected warm analysis/planning pass, which placed and
rendered successfully. Final validation ran and returned `FAILED`: the file
and timeline gates passed, while the visual review found broad black matte
areas above and below the picture throughout the portrait frame and the
deterministic subtitle report flagged seven fast segments. No product fix was
made and there is no successful end-to-end outcome.

The first `verify_timeline` call refused because the shell lacked the Resolve
scripting module. After Firstmate supplied the required environment, the skill
passed all six checks. This was an environment setup gap, not a Resolve
failure.

`origin/main` has since advanced to `617586e35307dcda724c2f6011dd37572f355e0b`
through PRs 1635-1638 and 1641. Those changes include vision scheduling,
iPhone audio stream selection, and pipeline scheduling; they were not present
in the measured checkout. Treat this report as a measurement of `d254af4`, not
as a measurement of today's `origin/main`. No performance change is attributed
to those later PRs.

## Comparison

The previous complete master 001 run was approximately 1h52m: 55m semantic
analysis, 9m temporal indexing, 42m planning, and about 6m placement/render/QA.
Its render phase included a 249s grant wait. The previous figures are rounded
and did not include broker hold/wait percentiles for the whole run.

| Phase | Previous complete run | Fresh on `d254af4` | Corrected warm pass |
|---|---:|---:|---:|
| Semantic analysis | ~55m | 7,748.333s (2h09m08s) | 0.064s; 17 clips, no model spans |
| Temporal index | ~9m | 469.338s (7m49s), 17 fresh | 15.986s; 14 reused, 3 reindexed |
| Planning | ~42m | 2,834.782s (47m15s), through manifest | 1,984.064s (33m04s), through manifest |
| Pre-Resolve elapsed span | ~106m across the first three phases | 11,060.032s (3h04m20s) | 2,002.508s (33m23s) |
| Placement | Included in previous ~6m phase | Failed after 1.24s in the `render` capability; no speech placement completed | Succeeded inside the 114.579s render capability; maximum Resolve hold 13.934s |
| Render/export | Included in previous ~6m phase | Not reached | 114.579s capability wall; H.264 MP4, 59.967s, 1080x1920 at 30fps |
| Final QA | Included in previous ~6m phase | Not run | 255.782s validate step, including 235.147s host-model wait; file and timeline gates passed, overall validation failed |
| Outcome | Complete; ~1h52m | Failed at placement setup; no complete total | Render completed; final validation failed. Active phase sum 2,372.869s (39m33s), across non-contiguous runs |

The fresh pre-Resolve span is about 78m longer than the previous run's rounded
106m for those phases. The corrected warm timings show cache reuse, but are not
a strict same-input comparison: after the fresh refusal, only the clone's
`project.yaml` was changed to declare `source.program_stream: 1`, and the
catalog was rerun. The source project was not changed. Three of the 17 temporal
entries were consequently reindexed. The first warm attempt stopped at a 300s
host-response timeout; corrected warm run `20261003T193708-77783` completed
analysis/planning. Placement/render ran as `20261003T204454-99183`, and
validation as `20261003T205935-78948`. The active warm phase sum is not
contiguous and includes the 235.147s validate response wait.

There is no support for assigning these differences to an individual PR. The
fresh semantic phase made 127 model calls and included two structured-response
parse failures after retry. The corrected warm planning span included two
300s host-response timeouts that later succeeded on retry. The warm validate
step spent 235.147s waiting for its host-model response. These are observed
run conditions, not a diagnosis of a code regression.

## Resolve windows and failure

Firstmate granted two Resolve windows for the fresh attempt. Readiness to the
first grant was 473.316s (manifest ready at 22:31:25.684Z; window began at
22:39:19Z); this is a readiness-to-grant proxy because the exact request-write
time was not recorded. The first broker KPI window, 22:39:19Z-22:51:42Z,
contained 17 jobs (16 done, 1 failed), 3.276s total hold, 3.016s exclusive
hold, hold p50/p95 0.026/2.404s, wait p50/p95 0/0.001s, and 0s render time.
It covered scratch setup and project-list failures, not a successful render.

For the retry, the recorded acceptance at 22:58:03Z preceded the grant at
23:13:42Z by 939s; the exact initial request time was not persisted. The
23:13:42Z-23:19:50Z broker window had 3 completed jobs, 2.301s total hold,
2.301s exclusive hold, hold p50/p95 0.856/1.437s, wait p50/p95 0/0s, and 0s
render time. Run `20261003T191629-59858` imported 38 media items and created
`Pipeline_Edit_2_20261003_191630_58s`, then refused speech placement for
`IMG_1816.MOV`: it has stereo AAC stream 1 and four-channel `apple_apac` stream
2, but the clone catalog and project had no selected program stream. The
fail-closed refusal is at `resolve_build_timeline.py:1009-1014`. The copied
source project had no `source.program_stream`; a fresh clone catalog exposed
all 17 multi-stream MOVs.

After that failure, `source.program_stream: 1` was added to the clone only, and
the actual catalog step selected stereo AAC stream 1 for all 17 files with
zero refusals. This is benchmark input setup, not a product-code fix. The
source remained unchanged. PR 1637 later landed auto-selection for iPhone
spatial audio on `origin/main`, after the measured commit; it was not part of
these runs.

Firstmate's handled recovery message 008 records that the captain's exact
project/timeline was restored and the disposable scratch project was deleted
and absent from the project list. The captain timeline was
`Podcast (field test)` / `Reel 15 - the-3d-nail-art-salon-beats-the-chains`,
UID `11e9a51e-b19b-4adf-b9ce-99bcf0fe08bb`, with 33 timelines.

For the warm window, the queued notice was at 00:12:14Z and Firstmate granted
the window at 00:28:43Z: 989s from notice to grant, with the exact request-write
time unavailable. A custom identity script first called unsupported
`Project.GetTimelineList()`; after replacing it with `GetTimelineCount()`, the
worktree broker ran the setup. Render run `20261003T204454-99183` placed the
timeline and completed the H.264 export. Its Resolve profile reports 3 leases,
24.901s total hold, wait p50/p95 0.002/0.007s, hold p50/p95 10.916/13.934s,
and 9.202s in the broker's render span. The bounded KPI window
00:39:25Z-00:52:01Z had 10 completed jobs, 26.787s total hold (26.718s
exclusive), wait p50/p95 0/0.001s, hold p50/p95 0.018/13.935s, and a 0s
broker `render_s` counter. The pipeline render capability itself was 114.579s;
these measurements have different scopes.

The first `verify_timeline` attempt refused with `no DaVinciResolveScript
module`. With `RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`, and
`PYTHONPATH=$RESOLVE_SCRIPT_API/Modules:$PYTHONPATH` set as Firstmate specified,
`verify_timeline` passed all six checks at 00:56:37Z. `verify_render` passed
all eight checks at 00:58:21Z, including duration, frame rate, resolution,
loudness, black frames, freeze frames, silence-under-picture and audio stream.
Validate run `20261003T205935-78948` ended `FAILED` after 255.782s. I inspected
all eight watch strips: every span shows broad black matte above and below the
central picture band; text is inside the frame, faces remain in frame, and no
within-shot jumps were visible. The deterministic report also flags seven
subtitle segments above 25 characters/second. Speech intelligibility and
subjective speech/music balance were not heard. No visual intent brief was
available to determine whether the matte is deliberate. The run recap printed
`not_watched` for the earlier request; the current validate request carried
eight strips and they were inspected.

Afterward, the captain was restored to `Podcast (field test)` / `Reel 15 -
the-3d-nail-art-salon-beats-the-chains`, UID
`11e9a51e-b19b-4adf-b9ce-99bcf0fe08bb`, with 33 timelines. The scratch project
was deleted and confirmed absent from the project list. Broker PID 35560 was
stopped by exact PID and status read back as not serving.

## Render and QA diagnostics

The corrected warm manifest is 59.9s, with 5 V1 items, 1 B-roll item, 1 music
item and 32 subtitle overlays. Subtitle asset rendering produced 32/32 files
with zero render failures; a sampled overlay-still visual check passed.
Manifest diagnostics reported integrated loudness -21.72 LUFS against a -14
LUFS target, true peak -2.33 dBTP, a dark-frame warning (mean 13.2 at 28.34s),
and framing-spread issues. The final render measured -14.39 LUFS and -1.31
dBTP. `verify_render` passed its eight checks. Pipeline validation returned
failed because of the visible matte and seven fast subtitle segments; the
file-only and timeline conformance gates passed.

## Clone and run conditions

- Source: `/Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001`.
- Copy-on-write clone: `/private/tmp/vep-benchmark-current-head.q3obr_9g/001`;
  source folder name retained and `pipeline_data.json.project_folder` points
to the clone.
- `PIPELINE_SOURCE_MEMORY_ROOT` used a clone-private source-memory directory.
  Catalog media paths point into the clone's `raw/` tree.
- Three copied untranslated marker notes (zoom blur, B-roll choice and
  subtitle size) were removed from the clone's marker-feedback input after the
  runner refused before execution. No marker-resolution records were created.
  Source project and markers were untouched.
- The validate step required `RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`, and
  `PYTHONPATH` with the scripting `Modules` directory prepended. Firstmate is
  filing this environment gap as a Ren fix; it was not changed here.
- The benchmark used no product code changes and ran no tests.

## Raw records

- [Raw timing, refusal and broker-window JSONL](2026-10-03-current-head.jsonl)
- [Commands, run details and step logs](2026-10-03-current-head-runs.txt)
