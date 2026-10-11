# FilmCraft upstream requests (tracked notes, not patches)

Engine-side asks too big, too behavioral, or too upstream-owned for a fork
patch. Each entry records the measured pin behavior, the ask, and who should
carry it. Intake replays only `*.patch` in this directory, so this file is
inert to patch replay. No upstream PRs are part of this workflow (see
`docs/UPSTREAM_ENGINE_INTAKE.md`); the pin-move lane re-probes open asks at
each new HEAD.

## 1. Variable speed as a first-class retime (open)

Raised by audit G6 (native headless gap audit, 2026-10-10). Measured at the
pinned revision `7bd7621` (CLI 0.5.0) on 2026-10-10.

### What the pin does (measured, not assumed)

- `clip.speedDuration` params: `{"clips":[id]?,"speed":percent=100,
  "reverse":bool,"ripple":bool,"interpolation":"frameSampling|frameBlending|
  opticalFlow"?}` (from `commands --json`).
- There is NO per-piece / multi-speed parameter: one call sets one speed.
  A stepped ramp still needs razor points plus one constant retime per
  piece - Ren's `expand_retime_ramp` (`native/ren-filmcraft/src/adapter.rs`).
- Supporting link mechanics measured while proving the workaround:
  `timeline.razor` severs the A/V link on later pieces (distinct video/audio
  link ids; first half keeps the shared id); a retime over severed ids
  ripples followers once PER ID instead of once; `clip.link` is a toggle
  (mixed links clear to none with `{"linked":false}`, unlinked clips gain a
  shared id with `{"linked":true}`, an intact pair unlinks); `ripple:false`
  sets speed flags without changing any span, so it cannot extend audio.
  The workaround re-links each severed piece (clear, then link) ahead of its
  retime.
- Reverse is WIRED, not asked: Ren plans the engine's `reverse` flag for
  whole clips and ramp pieces, with a frame-exact equivalence proof plus a
  render-level direction proof (reversed-frame PSNR beats frame-for-frame by
  10+ dB). Whole-ramp reversal (reversed piece order) keeps refusing with
  `reverse_not_supported` - the expansion plays pieces in timeline order.

### The ask

A first-class stepped / curved speed parameter on `clip.speedDuration`
(per-segment speeds plus per-segment interpolation in one call), or
documented confirmation that razor-plus-per-piece is the intended permanent
route. Until answered, Ren keeps the expansion workaround and its
equivalence proofs (`ren_retime_ramp_expands_to_razors_plus_constant_retimes`,
`ren_retime_ramp_with_reversed_piece_matches_model_frame_exact`,
`ren_reverse_plays_backwards_frame_exact`).

### Handoff

- Pin-move lane: re-probe at the new HEAD whether any multi-speed parameter
  appeared; update or close this entry with the outcome.
- No code in Ren depends on this file; it is a tracking note only.

## 2. MXF ingest cost: fixed open/scan plus slow per-frame decode (open)

Raised by the native speed profile (`data/vep-fc-native-speed-profile/report.md`
sections 2-3, 6, 2026-10-10). Measured at the pinned revision `98ba9acbcedc`
(CLI 0.5.0) on Mac 10 CPUs, 24 GiB RAM, macOS 26.3, ffmpeg/ffprobe 9.0.2.

### What the pin does (measured, not assumed)

- Reel 22 A-roll (1183 frames, two 4K MXF sources) exports in 389 s with no
  overlays at all; the full 1255-frame build with 35 overlays takes 493 s.
  Overlays render at 130 fps over black, so ingest is ~75-100% of every
  export.
- Every export pays a fixed ~150-200 s open/scan/seek cost per 50-60 GB
  MXF file before frames flow: 48 frames took 195 s, 120 frames over the
  same span took 180 s; a single still takes 40-58 s. Seeking 30 s deep
  into the 60 GB file costs ~400 s (121 frames in 428 s, 0.28 fps).
- Per-frame rate after the fixed cost is ~0.1-0.3 s per 4K frame
  (0.67 fps at 0-5 s). ffmpeg decodes and scales the same bytes in
  software at 29 fps: same bytes, same CPU, ~43x slower through the
  engine's media stack.
- Pre-conforming the used ranges to 1080x1920 ProRes once (9 s per
  120 frames via ffmpeg) drops the export to 5.7 s (21 fps): ~15 s vs
  180 s end to end, a 12-31x win. Ren now pre-conforms on the native
  path (`library/tools/filmcraft_preconform.py`) and papers over this
  cost rather than fixing it.
- Correction to the report's section 3 guess (measured 2026-10-11 by
  the pre-conform lane): there is NO 4K-to-vertical resample inside the
  ingest number. The engine places 4K pixels 1:1, center-cropping a
  1080x1920 window with no scaling (SSIM 0.94 for the exact center
  window vs 0.41-0.46 for every scale-based conform). The ingest cost
  is pure demux plus decode, which is also why a center-cropped ProRes
  pre-conform matches a direct export at SSIM 0.978 / PSNR 42.6 dB.

### The ask

Faster MXF open/index/seek (suspect whole-file scan per open plus
single-threaded decode), or guidance on what ingest path avoids it.
Repro: any 1080x1920 sequence placing LCATL0013.MXF (50,216,119,344 B) /
LC4932.MXF (59,972,918,320 B), `file.exportMedia` h264/auto over 0-5 s.

### Handoff

- Pin-move lane: re-probe the fixed cost and per-frame rate at the new
  HEAD; update or close this entry with the outcome.
- No code in Ren depends on this file; it is a tracking note only.

## 3. GPU export rendering is inert headless, and `auto` diverges by path (open)

Raised by the native speed profile (report sections 5-6, 2026-10-10) and
extended by the pre-conform lane (2026-10-11). Measured at the pinned
revision `98ba9acbcedc` (CLI 0.5.0).

### What the pin does (measured, not assumed)

- `--gpu-rendering auto` through the `export` subcommand changes zero
  pixels (framehash-identical to CPU) and buys zero fps (181.9 s vs
  179.8 s per 120 frames): the compositor either falls back to the CPU
  reference path headless or is bit-exact here, and no renderer choice
  surfaces anywhere in the receipt.
- `gpuRendering:auto` through `exec file.exportMedia` (the path Ren's
  adapter drives) does not fall back: it panics the CLI
  (`wgpu: No wgpu backend feature that is implemented for the target
  platform was enabled`). Same flag, two paths: silent fallback vs a
  process-killing panic.
- `file.exportMedia` VALIDATES a top-level `gpuRendering` as `off|auto`
  (a bogus value refuses), but `describe file.exportMedia` does not list
  the parameter at all: the surface exists, is enforced, and is
  undocumented.
- Ren's adapter sends `gpuRendering` explicitly on every export
  (`native/ren-filmcraft/src/lib.rs`), records it with `hardwareEncoding`
  on the receipt, defaults to `off`, and refuses `auto` before any engine
  call until the exec path stops panicking.

### The ask

1. Surface `gpu_render_stats()` (frames/fallbacks,
   `crates/export/src/lib.rs:851-857`) in the `file.exportMedia`
   response so adapters can tell fallback from parity.
2. Make the exec path match the subcommand (fall back, never panic), or
   document that `gpuRendering:auto` is unsupported headless.
3. List `gpuRendering` in the `file.exportMedia` parameter description.

### Handoff

- Pin-move lane: re-probe `gpuRendering:auto` on both paths at the new
  HEAD (fallback? parity? still panics?); update or close this entry
  with the outcome.
- No code in Ren depends on this file; it is a tracking note only.
