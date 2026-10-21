# VI-009: the technical leg reaches candidate selection

Lane: `p-vi009-media-quality`. Date: 2026-10-22. Base: `55527d3c4`.

## What the punch list asked

> VI-009 - Integrate objective media-quality measurements into selection.
> - Attach sharpness, blur, exposure, motion, clipping, noise, and usable
>   speech indicators to relevant intervals.
> - Make measurement coverage and confidence explicit.
> - Let the planner compare visually or acoustically usable alternatives.
> - Avoid interpreting technically sharpest as automatically best editorial
>   shot.
>
> Acceptance: Ren can explain why one candidate is technically preferable
> without confusing technical quality with creative intent.

## What already existed, and what was missing

The measurements existed and had names:

- `library/tools/analysis/picture_quality.py` measures per sample the
  focus (Laplacian variance), the luma mean, the two clipping fractions,
  the residual-noise proxy, the frame difference and the motion-blur
  proxy. Step 1.03's picture pass already decodes every clip for its soft
  ranges and threw the rest of the track away.
- `library/tools/analysis/image_defects.py` (VI-03) files a sampled
  picture track per source.
- `library/tools/audio_quality.py` (M11) files per-second loudness,
  spectral, silence and clipping readings per channel.
- `library/tools/source_quality.py` reduces those lanes into ONE profile
  over a span: focus, exposure, motion, framing, noise, intelligibility
  and compression, each measure typed by the evidence contract's
  registry.

What was missing was a surface the SELECTION could read, a coverage and
a confidence a reader could check, and a vocabulary that keeps the
technical reading apart from the editorial one.

## What landed

1. **`library/tools/media_quality.py` (new).** The technical leg: seven
   indicators (`sharpness`, `blur`, `exposure`, `motion`, `clipping`,
   `noise`, `usable_speech`), each with its value, unit, method, the
   COVERAGE of the samples behind it and a CONFIDENCE that says whether an
   instrument or a model produced the number. There is no total, no score
   and no threshold: a leg is not a ranking.
   `compare_legs()` states the technical difference between two
   candidates in numbers and carries no field that can recommend one; the
   editorial preference, when it is given, is recorded and left where it
   was.
2. **`library/tools/source_quality.py`.** The profile now states
   coverage and confidence per dimension, and reads a fourth lane - the
   picture sampler's per-sample track - so the focus, luma, clipping,
   residual-noise, frame-difference and blur readings reach the profile
   from the pass the pipeline already makes.
3. **`library/tools/analysis/picture_quality.py` +
   `library/tools/source_memory.py`.** The picture track is filed in the
   source memory (`picture_quality.json`) by the picture pass, so the leg
   reads numbers instead of decoding the footage again. The frames come
   from the shared decode, so this adds measurements, not a second decode.
4. **Step 3.02 (`library/steps/step_3_02_select_broll/`).** The candidate
   table the planner reads carries a `tech_leg` cell per candidate, and
   the bridge emits `broll_technical_leg`, which says what the column
   measures, how to read its coverage and confidence, and that the
   technically sharpest candidate is not automatically the right one. The
   post-bridge files the leg of the window that actually plays beside the
   assignment - a receipt beside the choice, never a second choice.

## Measured, on a Ren-owned scratch copy

`docs/benchmarks/vi009_media_quality_proof.py` measures two cameras of
the podcast episode (the captain's media, read-only), files the
measurements in the SCRATCH project's private source memory, and reports
the leg of ONE line as each camera recorded it. The line is aligned by
the sources' own clock group (M6), not by a guess.

- Sources: `LC4930.MXF` (the group's reference) and `LCATL0011.MXF`
  (the other angle), 3840x2160, 225.2 s and 214.5 s.
- Line: *"and just make sure you see the levels, audio levels from
  craig."* - 55.26-58.29 s in `LC4930.MXF`, 32.63-35.66 s in
  `LCATL0011.MXF` (clock offset +11.31 s).
- Measurements: 1,126 and 1,073 picture samples at 5 Hz on the 180 px
  short side; 4 audio channels at 1 Hz intervals each.

| indicator | LC4930.MXF | LCATL0011.MXF | unit |
|---|---|---|---|
| sharpness | 445.0 | 341.6 | laplacian variance |
| blur (motion-blur proxy) | 0.0 | 0.0 | fraction 0..1 |
| exposure (mean luma) | 49.86 | 41.13 | 0..255 |
| motion (frame difference) | 0.002817 | 0.002697 | fraction 0..1 |
| clipping (under / over) | 0 / 1.7e-05 | 1e-06 / 8.25e-04 | fraction 0..1 |
| noise (residual) | 0.0 | 0.0 | normalised luma |
| usable speech (band energy) | 0.5485 | 0.4358 | fraction 0..1 |
| usable speech (SNR, channel 1) | 21.2 | 28.95 | dB |

Coverage on both sides: 15 picture samples at 5 Hz covering 0.9901 of the
span (`full`), 4 audio intervals at 1 Hz covering 1.0 (`full`).

The candidate table the planner reads carries the same numbers:

```
LCATL0011.MXF  ... sharpness=341.6 lapvar[15 full] blur=0[15 full] exposure=41.13 luma[15 full] motion=0.002697[15 full] clipping=1e-06|0.000825[15 full] noise=0[15 full] usable_speech=0.4358|28.95 dB[4 full]
LC4930.MXF     ... sharpness=445 lapvar[15 full] blur=0[15 full] exposure=49.86 luma[15 full] motion=0.002817[15 full] clipping=0|1.7e-05[15 full] noise=0[15 full] usable_speech=0.5485|21.2 dB[4 full]
```

And the comparison, with the editorial preference on the side the
NUMBERS do not prefer:

```
sharpness: LC4930.MXF 445 variance vs LCATL0011.MXF 341.6 variance
exposure: LC4930.MXF 49.86 0_255 vs LCATL0011.MXF 41.13 0_255
usable_speech: LC4930.MXF 0.5485 fraction_0_1 vs LCATL0011.MXF 0.4358 fraction_0_1
Technical quality is one leg of this choice. It is not a verdict on which shot to use: the
editorial selection stands on its own grounds, and these numbers neither make nor unmake it.
```

`editorial_selection_unchanged: true`.

## What this lane did NOT do

- It did not rank candidates. The leg has no total, and the comparison
  has no recommendation field.
- It did not measure M11 in the pipeline. The audio measurements the
  leg reads are still produced out of band
  (`library/tools/audio_quality.py`), so in a run that never measured
  them the `usable_speech` indicator is absent WITH ITS REASON rather
  than empty. Wiring M11 into a step is the next lane's work.
- It did not touch the captain's project, timelines or media. The proof
  ran on `fm-vi009-scratch`, a Ren-owned scratch copy, and the
  measurements landed in that project's private source memory.
