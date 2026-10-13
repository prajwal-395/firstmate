# Static transforms: Reel 22 native before/after (2026-10-13)

Step 1 of the native-fidelity push (report §6.1): static punch-in/Pan/Tilt
and overlay positions minted as model effects and executed on the native
FilmCraft path. BEFORE = report's native render (`reel22-native.mp4`,
no static effects, 35 `overlay_unpositioned`); AFTER = same 1255-frame
timeline with 34 static effects (2 A-roll scale+position, 27 subtitle
positions, 2 MG positions, 1 frame position).

## Method (report method exactly)

- Same references: shipped deliverable (1183f) and fresh Resolve render
  (1254f). Same whole-reel measures (report §3): SSIM-Y/PSNR-y means over
  1183f (deliverable) and 1254f (fresh), per-200f segments, worst frames.
- Same stills at the same frames: f100 (Craig punch), f300 (Akshita punch
  + subtitle/MG collision), f329.
- Model rebuilt from measurements (no Resolve reads/writes, no captain
  timelines touched): A-roll source spans from audio correlation against
  the camera MXFs (piece 1 LCATL0013 @3629.876s, piece 2 LC4932
  @3644.307s, A-roll cut at reel 255/256, endcard cut at 1183/1184 — all
  confirmed by frame-difference on the BEFORE render); geometry fitted to
  the deliverable by NCC template matching (A-roll peaks 0.966–0.988 with
  visual montage verification; native conform confirmed as 1:1
  center-crop, NCC 0.9985).
- Fitted values: Craig scale 0.60746 pos [515.6,1178.4] (2.16x),
  Akshita scale 0.59880 pos [514.5,1176.8] (2.13x) — both consistent with
  the report's 2.1386x punch; all 27 subtitles scale 1.0 center
  [540,1393] (the lower-third slot); MGs scale 1.0 at [304,925] and
  [338,925] (shared left edge x=70); frame shifted to [525.5,1177].
- 9 subtitle spans + placements are ledger ground truth
  (`render_ledger.json` Reel 22 bindings with `tight_box.origin`
  [180,1153]); the other cards' spans come from boundary detection +
  1:1 diagonal sync refinement, validated against the 9 (edge errors from
  fades). The universal pattern (9/9 ledger cards): visible span =
  file[12, dur-12] (12f fades skipped each end), position [540,1393].
- 6 overlay files in scratch match nothing inside Reel 22's windows
  (2ef22db9, 855879f5, 8af079a3, 8d6b36c4, a7734c64, de6f1e12) and are not
  placed; the 27 placed cards tile the reel with no room left over.
  de6f1e12's ledger span [1150,1183] is stale (its text never appears;
  35b21436 plays there).
- Grade/move/masks/dual-mono/stems unchanged (steps 2–6, out of scope);
  audio rides the A-roll MXFs exactly as in BEFORE (stereo file, right
  channel silent at -72dB RMS, left dialogue at -28.9dB RMS — gap 2 open).

## Whole-reel numbers

| comparison | BEFORE SSIM-Y | AFTER SSIM-Y | BEFORE PSNR-y | AFTER PSNR-y |
|---|---|---|---|---|
| native vs deliverable (1183f) | 0.5265 | **0.6664** | 12.88 | **20.36** |
| native vs fresh (1254f) | 0.6186 | **0.5037** | 15.29 | **14.23** |

AFTER segments vs deliverable: 0.679/0.670/0.658/0.663/0.661/0.667
(BEFORE: 0.51–0.56 everywhere). Worst AFTER frames vs deliverable: 4–10
(0.502, MG-head intro + first-subtitle fade region — verified placed,
residual is header + grade).
AFTER segments vs fresh: 0.487/0.485/0.478/0.484/0.492/0.513 +
endcard 0.817 (BEFORE endcard 0.9981). Worst: 1182–1183 (A-roll/endcard
seam) + MG-shift regions.

The fresh number drops because the AFTER render targets the deliverable
geometry per the brief: the deliverable is a re-layout (post header +
~228px downward shift + tighter punch, report gap 9), while fresh keeps
snapshot geometry (unpunched A-roll, default mid-frame overlays). Against
the deliverable — the brief's stated target — SSIM lifts +0.140 and PSNR
+7.5 dB reel-wide. The fresh endcard dip (0.998 → 0.817) is source
quality: the endcard here is an H264 rip of the BEFORE render (55→71f,
re-extracted at the true 1184 cut) because no logo ProRes was found on
disk; sourcing the original is follow-up work.

## Stills (scratch, same frames as the report)

- `fit/tri_f100.png`, `fit/tri_f300.png`, `fit/tri_f329.png`: before |
  after | deliverable triplets. f300: BEFORE center-crop punch with
  mid-frame subtitle/MG pileup → AFTER 2.13x punch, MG upper-middle,
  subtitle lower-third → deliverable (same + header card + grade).
- `fit/f008_side.png`: worst-frame check (after | deliverable) — same
  subtitle, residual is header + grade only.

## What changed in the product (PR #2414)

- `transform.scale` / `transform.position` minted from reel records
  (`reel_project_model.static_transforms_from_placements`,
  `overlay_uses_from_records`), executed as FilmCraft `motion` params
  (`ren-filmcraft` static branch + `filmcraft_plan_picture_effects`).
- AFTER model: 33 clip uses, 34 effects, projection losses EMPTY
  (BEFORE: 35 `overlay_unpositioned` + grade/mask/effect losses).
- Plus a pre-existing ren-cli rustfmt drift fix (blocked the fmt gate).

## Heavy-run resources (inbox 004)

- `ren_native` rebuild (maturin, private scratch cache): queued behind
  full_suite_gate + fi01-sharpness via heavy-work lock; completed.
- AFTER render (plan + import 33 + placements + 34 static params + H264
  export 1255f): wall 129s, CPU 653s, peak RSS 3.97 GB (lock-held,
  exclusive). Output 75.5 MB, 11.54 Mbps (BEFORE 11.38 — parity kept).
- Comparisons (2× SSIM+PSNR over ~1200f 1080x1920): ~1 min via lock.
- Fitting (NCC/SIFT/audio corr, ~34 overlay matches + boundaries): light,
  no lock, ~3 h wall with verification.
- Machine also ran other lanes' full_suite_gate + fi01 jobs (separate
  lock owners, serialized with mine).

## Left for steps 2–9 (out of scope, observed while measuring)

- Grade/move composition, masks, dual-mono wiring, stems/ducking, span
  shortfall, overlay transcode, speed — all as the report left them.
- `ren_native` build script does not write its fingerprint record (had to
  `write_recorded` by hand after a verified-fingerprint build).
- `execute` refuses the already-executed `reel.assemble` edit (stripped
  the edit from the execute copy only; plan still declares it
  non-executable — same verdict the report recorded).
- End-card logo ProRes not found on disk (H264 rip used; see above).
- A-roll source spans snapped to the 23.976 frame grid (≤21 ms, inside
  the audio-correlation tolerance) for the adapter's exact-tick rule.
