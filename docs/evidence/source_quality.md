# The source-quality profile: one named aggregate over measured evidence

Code: `library/tools/source_quality.py`.
Tests: `tests/unit/picture/test_source_quality.py`.

The measurements were already there and had no name. VI-03
(`image_defects.json`) measured sharpness, temporal noise, compression
blockiness and the two clipping fractions. Step 1.04's temporal index
measured camera translation, subject energy and the face track that
carries the subject's position and width. M11 (`audio_quality.json`)
measured SNR and the speech-band energy fraction.

A reader who wanted "how good is this footage" had to know which lane
produced which number, at what resolution, over which interval, and what
its absence meant. This is the aggregation: one enumeration of
dimensions, one shape, one place that knows which lane answers which
dimension.

## The dimensions

`source_quality.DIMENSIONS` is the whole list, and it is the punch
list's six - focus, exposure, motion, framing, noise, intelligibility -
plus `compression`. The seventh is there because VI-03 already measured
blockiness and the search index already filters on it, so a profile that
dropped it would lose a measurement that exists.

| dimension | measures | lane | the reading |
|---|---|---|---|
| focus | `image.sharpness.laplacian_variance` | vi-03 | a sharpness PROXY: it cannot separate defocus, motion blur and low texture, and it is never a focus verdict |
| exposure | `image.exposure.underexposed_fraction`, `image.exposure.overexposed_fraction` | vi-03 | shadow/highlight loss at the rails, not an exposure recommendation |
| motion | `image.motion.camera_translation`, `image.motion.subject_energy` | 1.04 | a separation hypothesis, not a labelled cause |
| framing | `image.framing.subject_center_x`, `image.framing.subject_width_fraction` | 1.04 | the subject's position and size, read by `subject_framing` |
| noise | `image.temporal_noise.residual` | vi-03 | a global-luma-compensated temporal residual, not a sensor-noise diagnosis |
| intelligibility | `audio.snr_db`, `audio.intelligibility.speech_band_energy_fraction` | m11 | a frequency coverage proxy only, never word recognition |
| compression | `image.compression.blockiness_proxy` | vi-03 | 8-pixel boundary contrast excess, not a codec identification |

## Three rules the shape enforces

**Absence is never a default.** A dimension whose lane never ran, or
whose samples do not cover the span, is `absent` with a
machine-readable reason (`no_image_defects_record`, `too_few_detections`,
`no_audio_quality`, ...). It is never `0.0`, never `0.5`, and never
quietly omitted. An absent focus reading and a measured focus reading
are different facts, and a filter has to be able to tell them apart.

**A profile is measurement, never a verdict.** It says what was measured
and what was not. It carries no threshold and no "usable / unusable"
answer: what passes or fails is a versioned evaluation policy
(`library/tools/evaluation_policy.py`), and a threshold written here
would be taste dressed as a measurement. Every measure is `measurement`
kind and `measured` certainty, and its unit and kind come from
`library/tools/evidence_contract.py`'s registry, so a profile cannot
drift from the contract that governs evidence elsewhere. A defocus
LIKELIHOOD has no home here: the contract types it as an inference
with uncertainty, and a profile that could hold one would be a place
where a guess could pass as evidence.

**One reading.** The flat `facets` the footage index filters on are
PUBLISHED FROM the profile, so a filter bound and the profile a reader
inspects cannot disagree. The two readings that existed before this
module are now the owners' readings:

- the camera/subject numbers come from `library/tools/camera_motion.py`,
  which selects samples by their own timestamp. The index-side
  reduction cut the sample array by INDEX, so a span ending at 0.4 s
  took the sample at 0.4 s - a moment outside the span;
- the framing numbers come from `library/tools/subject_framing.py`,
  which decides which detections count and refuses a track with too few
  of them. The index-side reduction averaged whatever the curve held.

## Where the profile lives

The index payload stores it per segment, so a hit can be explained. It
is opt-in at the CLI, because a profile is EVIDENCE and a hit is an
answer:

    ren search "she opens the box" --quality
    ren search-index filter <project> --min-snr-db 12 --quality
    ren search-index detail <project> <segment_id>

`ren search --json` carries it per hit under `quality_profile`.

## What is still missing

- The measurements live in the source memory and the step output, not
  in the Project Model's `observations`. A profile is therefore a
  derived view over the lanes that own their records, not a Project
  Model row, and a query that asks the MODEL for a sharpness
  observation still finds nothing.
- The intelligibility dimension is a frequency coverage proxy. Nothing
  measures whether the words are actually understood, and no lane
  claims to.
