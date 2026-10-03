# The master build's Resolve hold, measured

Punch-list item 8 (2026-10-02) proposed decomposing the `edit_video` master builder:
move planning, calculation and preparation outside Resolve, and shrink `build_timeline()`
into short Resolve transactions. This page records what was measured before acting on that.
**The decomposition does not pay.** The build holds Resolve for about 35 seconds, and almost
none of that hold is FREE work. The large hold in a master build is the render, and that
hold is spent waiting for a machine grant, not on Resolve.

## How it was measured

On 2026-10-03, in a granted Resolve window. Everything ran in a disposable Resolve project
(1080x1920 @ 30, shaped before anything was imported), which was deleted afterwards.

- **Source:** a copy-on-write clone of `post a day keeps the apple away/001` (a 56.6 s
  monologue: 8 A-roll passages, 5 cutaways, 8 caption segments, music, SFX).
- **Manifest:** the clone's existing compiled assembly manifest (2026-08-29).
- **Inputs:** today's `compile_manifest` refuses that state: it needs a music audit sidecar
  that only re-running `music_selection` (an LLM step) would write. So `build_timeline` was
  driven directly from the existing manifest. The clone also declares
  `source.program_stream: 1`, because the iPhone sources carry two audio streams (AAC stereo,
  then spatial APAC) and the 2026-08-29 state records neither.

Each phase of `build_timeline` writes an `edit_placement.<phase>` row to the performance
ledger as soon as it ends (`_PhaseClock` in
`library/steps/step_6_01_render/resolve_build_timeline.py`). The phase rows sum to the
lease's own `held_s` to within 70 ms.

## Placement: `render.build:placement`

| phase | build 1 (s) | build 2 (s) |
|---|---:|---:|
| `neural_directives`: 13 × `TimelineItem.Stabilize()` | 19.47 | 26.14 |
| `place_a_roll` | 5.37 | 3.05 |
| `place_captions` | 2.28 | 3.08 |
| `deliver_audio_mix` (the OTIO round trip) | 1.63 | 1.06 |
| `fusion_comps` (the comp subprocess) | 1.02 | 1.49 |
| `place_b_roll` | 1.08 | 0.99 |
| `link_pass` | 0.69 | 1.05 |
| `timeline_create` | 0.50 | 1.92 |
| the other 24 phases | 1.88 | 1.62 |
| **held** | **33.9** | **40.5** |

The FREE work inside the hold is `plan_overlays`, `fps_and_track_plan`, `record_decisions`
and the pure halves of the other phases. Together it is **under 0.1 s**. Moving it out of
the lease would shorten the hold by less than the noise between two identical builds
(6.6 s). Stabilization is 57-65 % of the hold, and it is Resolve's own analysis: it cannot
leave Resolve, and every rebuild pays it again on the same source ranges.

## Render: `render.build:render`

| | seconds |
|---|---:|
| Resolve lease held (`render out`) | 274.5 |
| Resolve rendering (`resolve_render` span, 1,698 frames) | 24.3 |
| waiting for the heavy-work grant, **inside the lease** | 249 |

`render_timeline` (`library/tools/execution/resolve_render.py`) takes `@under_lease` first
and `@heavy_work_locked("…", "render.build:render")` second. That order is the documented
rule in `library/tools/heavy_work_lock.py`. The render profile demands
`cpu 4, gpu 1, ram_gb 4, disk 2`. Concurrent `full_suite_gate` runs (`cpu 8`) left no room,
so the scheduler queued the render for 249 s. Its own history records `waited max 249s`
for `render.build:render`. For all of that time the Resolve instance was held and idle.
The calls that come before rendering (`OpenPage("deliver")`, format and codec, render
settings, `AddRenderJob`), measured one at a time on a fresh timeline, take 0.73 s together.

The broker's KPIs for the window: hold p50 1.74 s, **p95 274.56 s**.

## A master edit, end to end

001 (56.6 s of video) has never run start to finish in one go. Its recorded stages:

| stage | wall | source |
|---|---:|---|
| preflight: `semantic_analysis` (vision) | 55 min | run 2026-08-29T06:31 |
| preflight: `temporal_index` | 9 min | run 2026-08-29T11:50 |
| planning: the LLM steps through `compile_manifest`, preflight reused | 42 min | run 2026-08-26T17:14 |
| render stage: build + render + QA | 6 min | run 2026-08-26T18:10; today: build 0.6 min held, render-out 4.6 min held |
| **total** | **about 1 h 52 min** | |

## What would pay

- **The render's grant wait (249 s of a 315 s Resolve total).** The scheduler already
  carries `resolve_cursor`. Two fixes would stop the lease idling behind a test gate:
  acquire the machine grant before the Resolve lease for this profile, or stop declaring CPU
  and GPU for a render whose Python side measured 0.01 peak cores (Resolve's own process does
  the work). Either one changes the rule in `heavy_work_lock.py`.
- **Stabilization (20-26 s per build).** Stabilizing the same source ranges again on every
  rebuild is the largest Resolve cost left in placement.
- Not the decomposition of `build_timeline`.
