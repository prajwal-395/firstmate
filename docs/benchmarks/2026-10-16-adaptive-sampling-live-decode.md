# Adaptive sampling in the live decode path (2026-10-16)

Follow-up to PR 2626 (opt-in policy) and
[`2026-10-15-adaptive-sampling-policy.md`](2026-10-15-adaptive-sampling-policy.md):
the policy is now executable through the real frame decoder
(`extract_frames` with `sample_times`), and measured here against the
incumbent dense grid on real footage.

## What changed, and what did not change

- `extract_frames` decodes an explicit policy-selected timestamp list under
  a policy-keyed cache namespace, with exact `-ss` seeks and refusal to run
  policy times without a policy version. Separate cache: adaptive stills
  never masquerade as dense-grid stills.
- `plan_coarse_frame_sampling` builds the adaptive schedule (80% of the
  dense frame budget) from the temporal index through the existing
  activity-map adapter (now reads the published `motion_energy` /
  `optical_flow_direction` fields).
- The pipeline default does NOT move: `run_pipeline`,
  `_prepare_host_semantic_contexts` and the `vision_pipeline_v3` CLI
  (`--frame-sampling`) all default to `dense` - the incumbent
  fixed-interval grid, byte-identical layer inputs so existing measurement
  caches are untouched. `adaptive` is explicit opt-in, and unknown modes
  are refused. This slice makes no model adoption or default change, per
  ARCH-004/ARCH-005: the numbers below do not justify one.
- `semantic_analysis` declares the new transitive dependency
  (`library/tools/adaptive_frame_sampling.py`) in `code_identity.py`.

## Method

Splits follow
[`2026-10-15-editorial-eval-corpus.md`](2026-10-15-editorial-eval-corpus.md).
Settings were fixed on calibration and the held-out split ran exactly once.

| | Calibration | Held out |
|---|---|---|
| Clips | IMG_1807/1808/1814, LCATL0012, LC4931 | IMG_1812, LC4930 |
| Settings | scene 0.3, motion 2 Hz, silence −35 dB/0.4 s, cover ±1.0 s, dense grid 5 s, budget 0.8 | identical, frozen |

Signals are model-free: ffmpeg scene cuts, frame-difference motion
energy, and an energy voice-activity proxy (silencedetect complement -
NOT word boundaries, NOT a transcript). Events = scene cuts +
high-motion times + voiced boundaries; an event is covered when a decoded
frame lands within ±1.0 s. "Mean motion at frames" is a scheduling
proxy (prioritization per frame), not visual understanding. No agent
labels were used, so object/action quality is unmeasured - see limits.

## Calibration (settings chosen here)

| Clip | Dense frames / cold / recall / motion | Adaptive frames / cold / recall / motion | Events |
|---|---|---|---|
| IMG_1807 17 s | 5 / 3.9 s / 0.607 / 0.561 | 4 / 3.8 s / 0.357 / 0.663 | 28 |
| IMG_1808 28 s | 7 / 5.2 s / 0.500 / 0.359 | 6 / 5.3 s / 0.500 / 0.520 | 18 |
| IMG_1814 46 s | 11 / 8.0 s / 0.636 / 0.147 | 9 / 9.8 s / 0.364 / 0.201 | 11 |
| LCATL0012 289 s | 59 / 255.9 s / 0.433 / n/a | 48 / 217.7 s / 0.373 / n/a | 134 |
| LC4931 288 s | 59 / 153.7 s / 0.337 / n/a | 48 / 115.9 s / 0.462 / n/a | 104 |

Warm decode (cache hit) is milliseconds in both arms. Driver peak RSS
89.5 MB; wall 1340 s, dominated by MXF signal timeouts (below).

## Held out (frozen settings, single run)

| Clip | Dense frames / cold / recall / motion | Adaptive frames / cold / recall / motion | Events |
|---|---|---|---|
| IMG_1812 139 s | 29 / 28.7 s / 0.596 / 0.384 | 24 / 17.9 s / 0.723 / 0.512 | 47 |
| LC4930 225 s | 47 / 252.0 s / 0.427 / n/a | 38 / 206.1 s / 0.396 / n/a | 96 |

Driver peak RSS 101.9 MB; wall 875 s.

## Scorecard vs the incumbent

- Frames examined: adaptive decodes exactly the 0.8 budget on every clip
  (-20%). Undecoded intervals stay explicit (`unexamined_intervals`,
  `coverage_status: sampled`); no absence claim is made anywhere.
- Decode cost (cold): roughly proportional to frames on expensive-seek
  media (podcast MXF −15 to −25%, IMG_1812 −37%); on cheap short clips the
  per-run variance dominates (IMG_1814 adaptive cost MORE with fewer
  frames: 9.8 s vs 8.0 s - arbitrary seeks vs sequential grid).
- Event recall (proxy): mixed. Calibration 1 up / 1 tied / 3 down;
  held out 1 up (IMG_1812: 0.72 vs 0.60) and 1 narrowly down (0.40 vs
  0.43). No measured recall win: the default stays dense.
- Information per frame (motion proxy, Apple clips only): adaptive higher
  on all 4 Apple clips measured (calibration 3/3, held out 1/1).
- Latency cold/warm, peak memory, end-to-end cost: tabled above. No model
  is loaded by either arm, so inference precision is not applicable;
  nothing was downloaded.

## Findings that constrain the next slice

1. MXF signal timeouts: on both podcast files, `detect_scenes` and
   `compute_motion_energy` hit their 120 s caps, so the policy ran on the
   discovery floor plus voiced boundaries only. Graceful degradation, but
   the schedule is near-degenerate there - faster low-res signal paths or
   longer caps are needed before adaptive can earn the podcast default.
2. Small-budget clustering: on the 17 s IMG_1807 (budget 4), the discovery
   floor collapses to one sample and the whole schedule clusters around
   the single scene cut (recall 0.36 vs 0.61). Short clips need a floor
   that survives tiny budgets.
3. Query conditioning without retrieval candidates records intent (query
   preserved in the plan, OCR/detail flag set) but correctly moves no
   selection - density follows candidates, which arrive with the
   retrieval lane (vep-mu-retrieval owns RET-*). Candidate densification
   itself is unit-tested.

## Limits

Scheduling proxies only. Real object/action quality per decoded frame
needs the standing blind-agent-label protocol (2 labellers + tie-breaker
via firstmate) - not requested in this slice. VideoITG/Tempo remain
approach studies over existing measurements (PR 2626 note); no external
weights or code. No model downloaded, none adopted, no default changed.
