# Composite QA cache investigation

Date: 2026-10-02

Base checked: `origin/main` at `91da2cdbefa7ca483c70d8f63ef336a5efbd436f`

## Decision

Do not add a persistent composite QA cache yet. On this base, the production
path does not repeat composite QA against an unchanged timeline generation, so
a cache keyed by generation has no normal hit case. The pass already shares
each rendered frame among checks in the same build.

## Trigger and reuse today

`library/steps/step_6_01_render/resolve_build_timeline.py` is the only
production caller of `execute_qa_plan`. It plans post-build checks, then clears
the frame and segment requests unless `PIPELINE_PERCEPTUAL_QA=1`. With that
setting enabled, `execute_qa_plan` sends all composite frame requests,
transition/VFX segment ranges, and sampled perceptual frames through one
`segment_renderer.render_batch` call. `render_batch` merges nearby ranges and
`execute_qa_plan` deduplicates requested frame numbers. The perceptual observer
receives `execution.composite_frames`, so it does not render those frames a
second time.

The builder names each timeline with a timestamp and creates a new empty
timeline for the build. There is no production command or other production
caller that reruns this composite QA pass against the same timeline. Repeated
builds therefore do not mean repeated QA for one generation. A persistent cache
would add state and invalidation requirements without avoiding today's work.

## Cost evidence

The current 24-hour `ren resolved kpi --hours 24 --json` report had 217 completed
jobs and `render_s: 0`. The job table contained only `lease` and `resolve_axi`
jobs in that window, with no `qa_render` jobs. This is not evidence that visual
QA costs zero: the QA path calls `segment_renderer.render_batch` directly
under `resolve_lock.under_lease`, outside the broker job table that feeds this
KPI.

The current renderer's measured 720p figures are recorded beside
`segment_renderer.merge_ranges`: 0.81-0.87 seconds to start one one-frame job,
about 7.5 ms per frame for a 150-frame render, and 1.95 seconds total for that
150-frame render. Those are the available render-cost measurements; this
checkout has no broker KPI measurement for composite QA itself.

## Inputs a future cache would need

The timeline shadow generation is structural. Its snapshot includes timeline
rate and dimensions, clip paths and source ranges, transforms, CDL/color group,
and limited Fusion composition metadata. It does not hash source media bytes,
all Fusion node parameters, or Resolve's full render settings. The segment
renderer reads the current render format/codec, sets the QA width and height,
and leaves other project-global render settings unspecified.

If a repeat-verification path is added, a safe cache key must therefore change
with at least the timeline's project and unique id plus generation/hash, the
exact merged frame span, output dimensions and frame rate, current format and
codec, every explicit render setting, and fingerprints of the picture inputs
and pixel-affecting Fusion/grade dependencies. A generation number and
resolution alone would permit stale frames after media or effect changes.
