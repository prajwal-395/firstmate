# Overlay media prep: Reel 22 native before/after (2026-10-13)

Step 8 of the native-fidelity push (report §6.8): qtrle overlay media
renders on the native FilmCraft path with no hand transcoding. BEFORE =
step 1 AFTER (report §6.1; `step1/render/reel22-after.mp4`, 27 subtitles
+ 2 MGs as hand-transcoded ProRes 4444); AFTER = the same 1255-frame
timeline executed from the 29 qtrle pipeline originals, auto-transcoded
to ProRes 4444 at render time through a content-keyed cache
(`library/tools/native_overlay_media.py`, wired in
`project_model_cli.execute_filmcraft`).

## Method (step 1 method exactly)

- Same references: shipped deliverable (1183f) and fresh Resolve render
  (1254f). Same whole-reel measures: SSIM-Y/PSNR-y means over 1183f
  (deliverable) and 1254f (fresh), per-200f segments, worst frames.
- Same stills at the same frames: f100, f300, f329.
- Model: copy of `step1/model/reel22-model-after-exec.json` with the 29
  overlay asset URIs rewritten to the qtrle pipeline originals (27 in
  `pipeline_output/steps/4_05_render_subtitles/`, 2 in
  `.../4_06_render_motion_graphics/motion_graphics/`); frame overlay and
  endcard keep their bundle ProRes/H264 files (passthrough, not qtrle).
  No Resolve reads/writes, no captain timelines touched.
- Parity: each cached ProRes decoded to raw RGBA and byte-compared
  against the bundle hand-transcoded ProRes; whole-reel AFTER-vs-AFTER
  (1255f) SSIM/PSNR.

## Whole-reel numbers

| comparison | BEFORE (step 1) SSIM-Y | AFTER SSIM-Y | BEFORE PSNR-y | AFTER PSNR-y |
|---|---|---|---|---|
| qtrle-auto vs hand-transcoded render (1255f) | — | **1.0000** | — | **100.00 (inf)** |
| native vs deliverable (1183f) | 0.6664 | **0.6664** | 20.36 | **20.36** |
| native vs fresh (1254f) | 0.5037 | **0.5037** | 14.23 | **14.23** |

AFTER segments vs deliverable: 0.679/0.670/0.658/0.663/0.660/0.667
(BEFORE: 0.679/0.670/0.658/0.663/0.661/0.667). Worst AFTER frames vs
deliverable: 4/8/5 (0.502 — same frames and values as BEFORE).
AFTER segments vs fresh: 0.487/0.485/0.478/0.484/0.492/0.513 + endcard
0.817 (identical to BEFORE).

Per-overlay parity: **29/29 decoded byte-exact** against the
hand-transcoded ProRes (alpha channel included) — the auto transcode is
the same ffmpeg `prores_ks` profile-4 `yuva444p10le` carriage the hand
pass used, so the cache is deterministic by source content hash.

## Transcode cost per overlay (execution receipt)

29 records in the receipt's `overlay_preparation` (also printed to
stderr): total transcode **10.088s**, total preparation **10.832s**
(hash + lookup + transcode + verify); per overlay 0.17–0.60s, mean
0.35s. Cache: 29 files, 154 MB beside the `--out` path
(`.<stem>.native_overlay_cache`), never inside the source project.
Second run is a cache hit per overlay (0 transcode seconds, covered by
`tests/unit/context/test_native_overlay_media.py`).

Render output: 1255f, 1080x1920, 23.976, H264 75.8 MB, 11.59 Mbps
(BEFORE 75.5 MB, 11.54 — parity kept). Projection losses EMPTY.

## Stills (scratch, same frames as step 1)

- `stills/mine_f{100,300,329}.png` + `step1after_` / `deliverable_`
  mates. f300 viewed: 2.13x punch, MG upper-middle, subtitle
  lower-third — same as step 1 AFTER; residual vs deliverable is header
  card + grade only (unchanged, steps 2–6 out of scope).

## What changed in the product

- `library/tools/native_overlay_media.py` (new): `prepare_projection`
  rewrites qtrle compositing-overlay imports (`subtitle_overlay`,
  `motion_graphic`, `frame_overlay`) to validated content-keyed ProRes
  4444 cache files; non-qtrle and unused assets pass through; source
  renders are never modified.
- `library/tools/project_model_cli.py`: `execute_filmcraft` prepares the
  projection before `filmcraft_verify_media`, records
  `overlay_preparation` on the receipt.
- `native/ren-filmcraft/README.md`: execute-time transcode documented.
- PNG and unmeasured formats still refuse as
  `overlay_codec_not_supported` (unchanged gate, out of scope).

## Left for steps 2–7, 9 (out of scope, unchanged)

- Grade/move composition, masks, dual-mono wiring, stems/ducking, span
  shortfall, speed — as the report left them; this lane moves none of
  their numbers (whole-reel measures reproduce step 1 exactly).
