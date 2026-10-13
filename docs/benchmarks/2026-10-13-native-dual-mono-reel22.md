# Dual-mono routing: Reel 22 native before/after (2026-10-13)

Step 5 of the native-fidelity push: execute the proven discrete-mono
mapping planner when FilmCraft imports A-roll media, and measure stream
metadata from the source where the reel model lacks audio streams. The
change routes each discrete mono dialogue stream to both output channels.
It does not add gain.

## Method

- The fixture and comparison path follow
  `2026-10-13-native-static-transforms-reel22.md`: same shipped deliverable
  (1183 frames), fresh Resolve reference (1254 frames), whole-reel
  SSIM-Y/PSNR-y, per-200-frame segments, and frames f100, f300, f329.
- BEFORE is the step 1 native render; AFTER is the FilmCraft render of the
  same 52.344 s Reel 22 model with the dual-mono mapping applied. Comparison
  uses the fixture's frozen model and overlays in worktree scratch. The
  fixture bundle and captain project remain read-only.
- Audio RMS uses the same 12–17 s dialogue span, decoded to 48 kHz stereo
  float PCM. Comparison covers 240,000 samples per channel.

## Picture comparison

| comparison | BEFORE SSIM-Y | AFTER SSIM-Y | BEFORE PSNR-y | AFTER PSNR-y |
|---|---:|---:|---:|---:|
| native vs deliverable (1183f) | 0.6664 | 0.6664 | 20.36 | 20.36 |
| native vs fresh Resolve (1254f) | 0.5037 | 0.5037 | 14.23 | 14.23 |

AFTER segments vs deliverable (SSIM-Y): 0.6792/0.6697/0.6582/0.6634/0.6605/0.6673.
AFTER segments vs fresh (SSIM-Y): 0.4865/0.4847/0.4781/0.4843/0.4915/0.5129/endcard 0.8166.
Picture remains unchanged by the audio-only mapping.

## Audio comparison

RMS in dBFS, left/right, over seconds 12–17:

| render | left | right |
|---|---:|---:|
| step 1 native BEFORE | -30.45 | -74.17 |
| dual-mono native AFTER | -30.47 | -30.47 |
| shipped deliverable | -15.83 | -15.83 |

The output's channels are level-matched (0.000 dB difference), with dialogue
on both. Relative to the shipped deliverable, each channel is 14.64 dB lower
in this span; the routing change intentionally makes no gain adjustment.
The after-vs-deliverable RMS difference is -16.65 dBFS per channel.

## Render checks and scope

`verify_render` passed black-frame, freeze-frame, resolution (1080x1920),
frame-rate (23.98), duration (52.344 s), and audio-stream checks. It reported
integrated loudness -24.16 LUFS (10.16 dB below its -14 LUFS target) and
20.656 s of digital silence under picture. These checks describe the native
fixture render; the dual-mono routing itself fixes the silent right channel
for dialogue-bearing A-roll, while the deliverable-level gain gap and
unrelated silent spans remain outside this step's routing scope.

## Follow-up measurements

The step 1 AFTER fixture render was run through the same `verify_render`
check. It reported 20.591 s of digital silence under picture in these spans:

`0.000-3.500`, `6.736-8.505`, `10.702-14.804`, `21.159-22.644`,
`30.672-32.820`, `43.472-46.751`, `47.975-49.338`, and `49.400-52.344`.
The fresh dual-mono render reports 20.656 s in the corresponding spans:

`0.000-3.500`, `6.718-8.505`, `10.703-14.804`, `21.150-22.644`,
`30.660-32.820`, `43.465-46.751`, `47.969-49.338`, and `49.385-52.344`.

The first seven intervals overlap the two A-roll placements (0-10.677 s from
`LCATL0013.MXF`, then 10.677-49.383 s from `LC4932.MXF`); the final interval
is the endcard. The model has no separate audio-role clip uses, no mute
declaration, and no gap in the A-roll video spans. Its original A-roll stream
metadata was missing, so execution now measures it before finalizing the
projection; the resulting placements name `A1`. A fresh render after that
change still reports the same silence intervals and the same -24.16 LUFS
integrated level. This follow-up therefore does not claim those silences are
fixed or attribute them to source silence; they remain a separate audio
placement investigation.

The shipped deliverable's `.deliver.json` records mastering input at -23.61
LUFS and output at -14.21 LUFS against a -14 LUFS target, with static gain and
a true-peak limiter. This explains its higher integrated level. The local
12-17 s RMS gap is 14.64 dB; the sidecar does not retain pre-master samples,
so it cannot establish how much of that span-specific difference comes from
mastering versus the segment mix.
