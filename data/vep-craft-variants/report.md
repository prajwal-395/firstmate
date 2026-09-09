# Craft variant set, stage 3 - rendered variants for the captain to choose from

Task `vep-craft-variant-generation`. Branch `fm/vep-craft-variant-generation`.
No pipeline code was changed. The captain's Resolve project was not touched.
No recommendation is made here: the pick is the captain's, and we work
backward from it to values afterward.

## 0. The limitation, up front

This set explores only what the pipeline can ALREADY produce. Every variant is
a point on an axis the engine has a delivery route for. If the right answer
needs something the pipeline cannot do - a capability nobody built - no variant
below contains it, and the exercise will converge on the best available rather
than the best. Catching that is stage 4's hand-cut reference, which is exactly
why it comes last. Do not read the winner of this set as "the best"; read it
as "the best of what exists".

## 1. The set

Seven clips, each 10.01 s, 1080x1920, 23.976 fps, h264 + stereo AAC, all cut
from the same 10 s source (`clip_10-20.mp4`: outdoor talking head, speech
present, subject centred). Total viewing time is about 70 seconds. Each variant
moves exactly ONE measurable axis off the shared baseline, so a pick maps back
to exactly one value.

| File | Axis (pipeline route) | Value | Baseline value |
|---|---|---|---|
| `v00_baseline.mp4` | - | - | no captions, dialogue gain 0 dB, CDL slope 1.0 |
| `v01_cap_085.mp4` | caption `fontSize` px (`pipeline.subtitle_typography.size` via `library/tools/subtitle_style.py`) | 85 px | no caption |
| `v02_cap_160.mp4` | caption `fontSize` px (same route) | 160 px | no caption |
| `v03_snd_m3.mp4` | dialogue clip gain dB (the unit `library/tools/otio_mix.py` carries to Fairlight) | -3 dB | 0 dB |
| `v04_snd_m6.mp4` | dialogue clip gain dB (same route) | -6 dB | 0 dB |
| `v05_look_090.mp4` | CDL `slope` all-channels, offset 0 / power 1 / saturation 1 (`NEUTRAL_CDL` in `library/tools/house_look.py`) | 0.9 | 1.0 |
| `v06_look_110.mp4` | CDL `slope` all-channels (same route) | 1.1 | 1.0 |

Why these three axes: they are the captain's own three named shortfalls on 001
(CAPTIONS, SOUND, THE LOOK), and each has a proven delivery route to a frame or
a speaker - `resolve_subtitle_style` has a reader in `SubtitleOverlay`,
`audio_mix` levels reach Fairlight through the OTIO round trip
(`docs/UNREAD_DECISIONS_INVENTORY.md` item 6), and CDL reaches Resolve through
`SetCDL` (`library/tools/house_look.py`). Pacing was not named, so no variant
spends on cut rhythm.

Why seven and no more: one shared baseline plus one A/B pair per axis. Each
choice is a single pairwise comparison, and the pair differs in one number, so
there is nothing to untangle after the pick. A third position per axis would
double viewing time without adding a decision the pair cannot already surface.

## 2. What was held constant

- Geometry: centre FILL crop (`crop=202:360:219:0`) with lanczos upscale to
  1080x1920, identical on all seven. Framing is deliberately NOT an axis here;
  varying it would confound the caption px readings.
- Caption copy (`every variant`), typeface (the repo's bundled Montserrat,
  `remotion-subtitles/public/fonts/Montserrat-Variable.ttf`, SIL OFL 1.1),
  weight (variable-font default instance), outline (4 px black), position
  (bottom edge at row 1620, centred). Only `fontSize` moves.
- Codec and container settings (libx264 `veryfast` crf 23, aac 128k) on all
  seven, so byte differences come from the axis, not the encoder.
- The 85 px value is the captain's declared project value and 160 px is what
  001 shipped (`docs/RULE_EVIDENCE.md`). The gain steps are negative only
  because the source already peaks at 0.0 dBFS - a positive gain would clip,
  which would make the comparison about clipping rather than level.

A defect the stills caught before the numbers ran: the first caption line was
too long and overflowed the frame edges at both sizes. It was shortened to
`every variant` (986 px wide at 160 px size, 525 px at 85 px), both variants
re-rendered, and both stills re-checked by eye. A measurement that produces
only a number can be wrong silently; these produce files you can look at.

## 3. Evidence each axis actually reaches the output

The control comes first. Re-rendering the baseline with the identical command
is byte-identical:

```
cb7e3153b8105811777a1f28573d4b77  v00_baseline.mp4
cb7e3153b8105811777a1f28573d4b77  v00_rerun.mp4
```

The renderer is deterministic, so every difference below is signal, not noise.
(This discipline is load-bearing: `docs/RENDER_CAPABILITY_CEILING.md` section 7
records two render checks that passed against builds with their defect
deliberately reintroduced, both times because the instrument was never shown a
control.)

Per-stream hashes and measured audio level for the committed files:

```
v00_baseline | video MD5=ca408ce6b88ce2e5ccd022c0bed5cb45 | audio MD5=0dafe4d914d75e81c11a48d18558085f | mean -13.8 dB
v01_cap_085  | video MD5=1feecf65adf3bae3590fa35420e86181 | audio MD5=0dafe4d914d75e81c11a48d18558085f | mean -13.8 dB
v02_cap_160  | video MD5=6351c6cc06427205e75e0125fa49a2c5 | audio MD5=0dafe4d914d75e81c11a48d18558085f | mean -13.8 dB
v03_snd_m3   | video MD5=ca408ce6b88ce2e5ccd022c0bed5cb45 | audio MD5=ac2b5af34dac0ecdd083b64c6aefd18f | mean -16.7 dB
v04_snd_m6   | video MD5=ca408ce6b88ce2e5ccd022c0bed5cb45 | audio MD5=00d8db393d52af7eb2b083aa4146e9f1 | mean -19.7 dB
v05_look_090 | video MD5=30b886a4cdcf4a89c441c37b60b027fc | audio MD5=0dafe4d914d75e81c11a48d18558085f | mean -13.8 dB
v06_look_110 | video MD5=640140ac8064a25846b5ab6d92f20237 | audio MD5=0dafe4d914d75e81c11a48d18558085f | mean -13.8 dB
```

Frame-level differences at t=5 s (decoded RGB, mean absolute difference in
grey levels) and caption-ink pixel counts in rows 1380..1660:

```
mean_abs_diff(baseline, v01_cap_085) = 2.723 grey levels
mean_abs_diff(baseline, v02_cap_160) = 3.457 grey levels
mean_abs_diff(baseline, v03_snd_m3)  = 0.000 grey levels
mean_abs_diff(baseline, v04_snd_m6)  = 0.000 grey levels
mean_abs_diff(baseline, v05_look_090) = 15.572 grey levels
mean_abs_diff(baseline, v06_look_110) = 11.346 grey levels

near-white px in caption band [v01_cap_085] = 1414
near-white px in caption band [v02_cap_160] = 6133
near-white px in caption band [v00_baseline] = 0
```

Reading it:

- CAPTIONS: ink pixels 1414 vs 6133 (4.3x for a 1.88x linear size step - the
  stroke scales too), baseline band empty. The axis draws, and the two
  positions are far apart.
- SOUND: video streams byte-identical to baseline AND 0.000 frame difference -
  the axis touches nothing visual - while mean level moves -13.8 to -16.7
  (-2.9 measured for -3 nominal) and -19.7 (-5.9 measured for -6 nominal; the
  tenth is AAC re-encode rounding). The axis reaches the speaker and only the
  speaker.
- LOOK: 11-15 grey levels of mean frame difference with audio byte-identical
  to baseline. The axis reaches every pixel and nothing else.
- Cross-isolation holds both ways: every non-sound variant carries the
  baseline audio hash exactly, and both sound variants carry the baseline
  video hash exactly. Each variant moves one thing.

## 4. Render cost

Measured before the set was built (the go/no-go the brief asks for):

```
baseline render wall: 0.8s
```

Full-set build cost after the caption re-render: about 8 s wall on this
machine for all seven (ffmpeg path ~0.8 s per variant; PIL caption path
~2.0 s per variant). Per-file sizes are 2.1-2.5 MB; the directory is ~16 MB.
Rebuilding the set at full source resolution or with the Resolve renderer
instead of ffmpeg is unmeasured and is not claimed here.

## 5. What could not be varied, and why

- `creative_direction` fields (`target_mood`, `target_energy`, the other six):
  no mechanical reader changes a frame from any of them
  (`docs/UNREAD_DECISIONS_INVENTORY.md` item 7). Varying them produces
  byte-identical videos and wastes the exercise, so none was built.
- Pacing and cut rhythm, including transition choice: the captain did not name
  it, and a different cut is different content, not a position on an axis. Out
  of scope per the brief.
- Music track and which section plays: selection is the model's per-track
  decision (`library/tools/music_selection_contract.py`); a different track is
  different content. No music library is configured in this environment either.
- Speech-above-bed separation: needs separate dialogue and bed stems, and the
  source is one mixed stereo pair. `measure_speech_above_bed` in
  `library/tools/render_qa.py` has nothing to separate here. Dialogue gain is
  the closest axis with a delivery route, and it is what was built.
- SFX choice: the mapping names files, and choosing between them is taste
  (`library/tools/sfx_library.py`). Varying it swaps sounds rather than moving
  along an axis.
- Grain, glow, vignette and contrast strengths: the engine ships none - there
  is no house look (`library/tools/house_look.py`, AGENTS.md section 12). Any
  value would be invented taste, which AGENTS.md 10.5 forbids the engine.
- Framing and crop: held constant on purpose (section 2). A real fourth axis,
  cut to keep the set a 70-second watch.
- Per-speaker caption styling, caption position, outline width: reachable,
  second-order axes cut for set size. Available on request once the size
  question is answered.
- Tracked labels, object moves, MCP-fetched looks, model-authored components:
  no route to a frame (`docs/RENDER_CAPABILITY_CEILING.md` sections 4 and 6).

## 6. Done-check (real output, pasted not summarised)

```
$ ffprobe v00_baseline: 1080x1920, 23.976 fps, h264 + stereo aac, 10.01 s, 2295380 bytes
$ re-render control:  cb7e3153b8105811777a1f28573d4b77 == cb7e3153b8105811777a1f28573d4b77 (identical)
$ sound deltas:       -13.8 dB -> -16.7 dB (-2.9) and -19.7 dB (-5.9)
$ caption ink:        0 -> 1414 px (85) -> 6133 px (160)
$ grade frame diff:   15.572 (slope 0.9) and 11.346 (slope 1.1) grey levels
$ cross-isolation:    sound variants share baseline video MD5; all others share baseline audio MD5
$ single-variant render cost: 0.8 s wall (baseline, ffmpeg path)
```

No test suite was run: no pipeline code changed, so there is no test that
covers this change. The verification is the measurement above, run against the
committed files.


## Note

The seven craft variant .mp4 files (v00_baseline, v01_cap_085, v02_cap_160, v03_snd_m3, v04_snd_m6, v05_look_090, v06_look_110) originally generated with this report have been archived to `/Users/prajwal/Documents/work_stuff/firstmate/data/vep-craft-variants/`.
