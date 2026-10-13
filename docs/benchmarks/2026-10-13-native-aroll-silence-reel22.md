# A-roll audio silence: Reel 22 native before/after (2026-10-13)

The native render contained 20.656 seconds of silence under seven A-roll
spans, plus the silent endcard. The source footage and timeline timing were
correct. Silent audio streams from subtitle and motion-graphics overlay
files were also being placed onto Audio 1, replacing the camera audio during
the overlay intervals.

## Diagnosis

- Audio 1 was enabled, unmuted and unsoloed. Its clips were enabled at 0 dB.
- Before the fix, the A-roll audio appeared only over dialogue intervals;
  silent subtitle and motion-graphics files occupied Audio 1 over the seven
  reported intervals. The A-roll pictures themselves remained continuous.
- Camera MXFs have four mono audio streams at 48 kHz. Stream index 1
  (`0:a:0`, the first audio stream) carries dialogue. Stream index 2 is near
  silent (about -70 dBFS); streams 3 and 4 are lower-level ambience.
- Source and render align at 0 ms for six spans and 0.3 ms for one. The
  model's source spans were not offset. The endcard has no camera audio use
  and is expected to be silent.

## Per-span comparison

RMS and Pearson correlation are measured after decoding to 48 kHz PCM. The
camera column uses MXF stream index 1 (`0:a:0`); native and deliverable
columns show each output channel. Correlation uses the best absolute
normalized cross-correlation within ±250 ms; values shown are the signed
coefficient at that lag. `ch1`/`ch2` are the left/right output channels.

| Span | Source clip / source time | Timeline | Camera RMS | Native L RMS / corr / lag | Native R RMS / corr / lag | Deliverable RMS / corr (both channels) |
|---|---|---:|---:|---:|---:|---:|
| 1 | LCATL0013.MXF 3629.876–3633.376 | 0.000–3.500 | -26.88 dBFS | -26.95 / +0.9971 / 0 ms | -70.67 / +0.0118 / -112.8 ms | -20.30 / +0.9998 |
| 2 | LCATL0013.MXF 3636.594–3638.381 | 6.718–8.505 | -26.25 dBFS | -26.25 / +0.9965 / 0 ms | -69.85 / -0.0189 / -156.0 ms | -19.64 / +1.0000 |
| 3 | LC4932.MXF 3644.333–3648.434 | 10.703–14.804 | -23.42 dBFS | -23.43 / +0.9982 / 0.3 ms | -70.70 / +0.0134 / +243.9 ms | -14.43 / +0.9915 |
| 4 | LC4932.MXF 3654.780–3656.274 | 21.150–22.644 | -24.26 dBFS | -24.28 / +0.9990 / 0 ms | -70.38 / +0.0231 / +89.3 ms | -14.66 / +1.0000 |
| 5 | LC4932.MXF 3664.290–3666.450 | 30.660–32.820 | -27.00 dBFS | -27.02 / +0.9988 / 0 ms | -70.41 / -0.0191 / +40.0 ms | -17.40 / +0.9999 |
| 6 | LC4932.MXF 3677.095–3680.381 | 43.465–46.751 | -27.65 dBFS | -27.66 / +0.9991 / 0 ms | -70.38 / +0.0127 / -222.1 ms | -18.05 / +1.0000 |
| 7 | LC4932.MXF 3681.599–3682.968 | 47.969–49.338 | -24.18 dBFS | -24.19 / +0.9986 / 0 ms | -70.39 / -0.0227 / -221.2 ms | -14.96 / +0.9954 |

The pre-fix native output was at AAC's digital-noise floor in these seven
spans (about -114 to -130 dBFS, or digital zero); its correlation to camera
dialogue was consequently undefined or noise-like. After the fix, the
left-channel native RMS is within 0.07 dB of camera source RMS, and
correlation is 0.9965–0.9991. The shipped deliverable has dialogue on both
channels and higher RMS from downstream gain. The right-channel difference
is the separate dual-mono change already accepted for the next pool; this
branch starts at `origin/main` and addresses the overlay replacement cause.

The endcard interval 49.385–52.344 s has no camera source span. Fixed native
RMS is -124.67/-107.76 dBFS (left/right); the deliverable is digital silence
on both channels. Endcard silence is expected. -14 LUFS mastering remains a
separate gap and is out of scope.

## Change and verification

FilmCraft placement operations now explicitly carry `video` and `audio`
booleans. A-roll and B-roll keep audio; stem roles keep audio-only placement;
subtitles, motion graphics and endcards are picture-only. Both fields
deserialize as true for older saved operations, preserving their previous
default. The native render's Audio 1 now contains only the two camera MXF
clips across the full 0–1184f A-roll picture span; no overlay audio clips
replace them.

The post-fix render was verified at 1255 frames / 52.344 s. Only the
expected 2.943 s endcard is digitally silent under picture. The render's
resolution, frame rate, duration, audio stream, black-frame and freeze-frame
checks passed. Its -26.57 LUFS measurement is below the delivery target and
is intentionally left to the separate mastering work.
