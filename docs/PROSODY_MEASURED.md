# Prosody - what step 1.05 would have measured on 001, and what already answers it

**Status: MEASURED, and the step is now UNWIRED.**

This closes the question the captain asked on 2026-08-29 - is prosody genuinely useful
to this pipeline - and records the numbers, so re-adding it later means overturning a
measurement rather than re-deriving one.

It is an unwiring, not a deletion. **The capability stays on disk in full:**

    library/steps/step_1_05_prosody_analysis/          the step, unchanged
    library/tools/analysis/speech_advanced_pipeline.py the parselmouth pass, unchanged
    library/tools/prosody_profile.py                   profile_defect, unchanged
    library/tools/context_views.py                     view:prosody, unchanged
    tests/test_prosody_view.py, tests/test_prosody_failure_is_loud.py

What changed is that no DAG node runs it and no step declares its output, and its
recorded `unwired_reason` in `library/tools/project_layout.STEPS` now states what was
measured rather than what was assumed. The precedent is `object_segmentation` (1.06) and
[`docs/SUBJECT_MASKING_MEASURED.md`](SUBJECT_MASKING_MEASURED.md); the distinction
between UNWIRED and DESELECTED is AGENTS.md section 3, and prosody is unwired because
after 2.01's declaration went there is no consumer left at all.

The full scout report this is drawn from is
`data/vep-prosody-usefulness-test/report.md` in the firstmate home, sections 3-5.

## 1. It had never measured anything, anywhere

    grep -rl '"method": "parselmouth' /Users/prajwal/Documents/content_stuff/
    → only speech_advanced_pipeline.py and tests/test_prosody_failure_is_loud.py

Not one profile on this machine has ever carried a measurement. On 001 the step ran and
reported `available: false` - *"Prosody measured nothing on any of 17 clip(s)"* - which
`profile_defect` correctly refuses as hollow (AGENTS.md section 10.3, "A file on disk is
not a measurement"). That refusal is what made the declaration below fatal.

## 2. What it emits, and the three structural problems

`library/tools/analysis/speech_advanced_pipeline.py:53-183`, one profile per clip:

    pitch_stats            mean/median/min/max/std F0, range_semitones, voicing_percentage
                           - WHOLE-CLIP aggregates
    pitch_contour_10ms     pitch_values[:3000]      → hard cap: first 30 s of the clip
    voice_quality          jitter_local, shimmer_local, hnr_db, quality_assessment
    speaking_rate          words_per_minute         - ONE number for the whole clip
    intensity_contour_50ms intensity_values[:1200]  → hard cap: first 60 s of the clip

**(a) The contours stop before the footage the edit uses.** Over 001's eleven chosen
passages:

    TOTAL chosen speech                               46.03 s
      covered by the pitch contour     (30 s cap)     10.16 s   22.1%
      covered by the intensity contour (60 s cap)     23.79 s   51.7%

Every passage 2.01 and 2.02 weighed - body3 @ 63.1 s, body4 @ 100.5-116.5 s, body10 @
119.2 s, the closing line - is **outside both caps** on clip_011 (188.578 s). Same
first-N-seconds defect as `scene[]` (#302), on the same clip.

**(b) The aggregates are per-clip, and every question asked of it is per-passage.**
`speaking_rate` is one WPM for the clip; `pitch_stats.mean_f0_hz` is one number for
188.578 s of which the edit used 27. When parselmouth 0.4.7 was installed and run on
clip_011 (`docs/RULE_EVIDENCE.md:1492`), what came back was `mean_f0_hz 119.5,
voicing_percentage 40.8, hnr_db 6.3` - three scalars for a three-minute clip. None of
them answers "which of two restatements does he land better".

**(c) `voice_quality.quality_assessment` is a hardcoded ladder, and on this footage it
describes the microphone.** `speech_advanced_pipeline.py:129-134` hardcodes
`hnr > 20 → "clear"`, `> 10 → "slightly_breathy"`, else `"breathy"`. clip_011's measured
HNR is **6.3 dB**, so it reads `"breathy"` - a fact about parking-lot iPhone audio, not
about vocal register. Three unmeasured constants deciding an emotional-register word is
what AGENTS.md sections 10.5 and 12 forbid.

## 3. What already answers the same questions, at 100% coverage

`temporal_index` (step 1.04) carries, per clip, over the clip's **whole** duration:

| field | shape | what it is |
|---|---|---|
| `energy_curve.values` | 30 Hz, 0-1 | frame-aligned RMS, normalised by the clip's own max |
| `energy_curve.peak_times` | seconds | scipy `find_peaks`, prominence 0.3, >= 0.5 s apart |
| `speech_activity.values` | 30 Hz, 0/1 | voiced / unvoiced |
| `speech_activity.speech_ratio` | scalar | voiced fraction |
| `speech_regions[].words[]` | `{word, start, end}` | WhisperX word-level timings |

Coverage measured over all 17 of 001's index files: **17 of 17 clips, 100% of duration,
at 30 Hz** - against prosody's 0 of 17, and 22.1% / 51.7% if it ran.

### 3.1 It discriminates between passages

For the eleven passages 2.02 chose, computed from `energy_curve` + `speech_activity` +
word timings:

    passage  clip                span    dur    wpm  maxgap  meanRMS peakRMS voicedRMS voiced%
    hook     clip_011   0.84-   3.23   2.40  275.2    0.08    0.190   0.354    0.191     96%
    body1    clip_011  17.67-  20.35   2.68  156.6    1.12    0.203   0.386    0.244     56%
    body2    clip_011  24.17-  26.87   2.70  266.5    0.12    0.222   0.508    0.222     95%
    body3    clip_011  63.13-  66.67   3.54  288.1    0.24    0.364   0.728    0.389     84%
    body4    clip_011 100.52- 116.51  15.99  138.8    6.90    0.346   1.000    0.385     72%
    body5    clip_012  21.80-  24.18   2.38  277.1    0.08    0.339   0.914    0.342     96%
    body6    clip_012  30.14-  31.88   1.74  276.2    0.13    0.257   1.000    0.267     92%
    body7    clip_012  31.90-  33.66   1.76  238.2    0.32    0.226   0.409    0.244     80%
    body8    clip_012  33.94-  35.40   1.46  287.1    0.07    0.171   0.420    0.169     98%
    body9    clip_017  31.45-  40.12   8.67  200.8    1.05    0.252   1.000    0.323     69%
    body10   clip_011 119.23- 121.94   2.71  288.2    0.44    0.353   0.701    0.367     74%

    WPM spread:             min 138.8  max 288.2  ratio 2.08x
    voiced mean-RMS spread: min 0.169  max 0.389  ratio 2.30x

A 2.08x spread in local speaking rate and 2.30x in voiced level across eleven passages is
not noise - compare `passage_engagement`'s three withdrawn arithmetic scorers, where
eight of eleven passages scored an identical composite.

**D12 is answered outright.** 2.02 wrote of passage 4: *"if the delivery in that take is
meandering it will be the weak point."* Passage 4 is **138.8 WPM, the slowest of the
eleven by 12% against a median of ~270**, and holds a 6.90 s inter-word gap, **6x the
next largest**.

**Caveat, stated rather than glossed:** that 6.90 s gap is a WhisperX alignment artifact,
not a silence. Cross-checked against `speech_activity`, `[100.57-107.47]` is **66%
voiced** - he was talking and the aligner dropped words. The next two gaps (0.70 s and
0.52 s) are **5% and 7% voiced** - real silences. **Word gaps alone are not a breath
detector; the pair (word gaps x `speech_activity`) is.** Any implementation must read
both.

### 3.2 The mid-breath check 3.03 recorded as unobtainable

3.03 flagged two cuts and said the verdict was *"an audio property and there is no
prosody in this project"*. `speech_activity` and `energy_curve` at +/-0.5 s:

    block 14 end - clip_017 @ 40.120 s
      speech_activity  [1 x16, 0 x12, 1,1]
      energy_curve     [… 0.170, 0.136, 0.063, 0.010, 0.009, 0.010, 0.011, 0.010, 0.008 …]
      → lands on the LAST voiced frame, into 0.40 s of near-silence. CLEAN.

    block 7 end - clip_011 @ 116.512 s
      speech_activity  [1 x24, 0,0, 1,1,1,1]
      energy_curve     [… 0.288, 0.295, 0.272, 0.312, 0.289, 0.209, 0.176, 0.136]
      → lands INSIDE continuous speech at full level; two unvoiced frames (67 ms).
        NOT into a breath.

Produced from files already sitting in
`pipeline_output/steps/1_04_temporal_index/index/` when 3.03 wrote that sentence.
**Prosody was never what stood between 3.03 and this answer; routing was.**

### 3.3 2.01's three key moments against their own clip's baseline

    key moment                  clip      range        voicedRMS  voiced%  WPM   maxgap  words
    The admission               clip_017   28.4- 55.6      0.285     66%   271.8   1.07     82
    The diagnosis               clip_011   92.1-118.2      0.353     71%   283.2   1.36     87
    The commitment / landing    clip_011   63.1- 71.0      0.360     78%   282.2   0.52     29

    whole-clip baseline
      clip_011 (0-188.6 s):  voicedRMS 0.283   voiced 56%   WPM 253.2
      clip_017 (0- 85.8 s):  voicedRMS 0.241   voiced 58%   WPM 263.2

All three sit **above** their own clip's baseline on level, voicing density and rate -
the shape of the disconfirming evidence D05 wanted. **Caveat:** `energy_curve` is
normalised per clip, so this is a within-clip relative reading, and higher level at a key
moment may be him leaning into the phone rather than animation. The claim is not that the
mood answer flips - it is that the question stops being unanswerable. That judgement is
the model's to make, not the engine's (AGENTS.md section 10.5).

## 4. What is given up, stated plainly

- **Pitch (F0).** Nothing in this repository measures fundamental frequency. Rising
  intonation (a question), falling (trailing off) and pitch range are unavailable from
  any source.
- **Voice quality** - jitter, shimmer, HNR. 2.02's *"a crack in it"* becomes
  unanswerable.
- **Absolute speech level.** `energy_curve` is normalised by each clip's own max, so
  "clip A is louder than clip B" is not answerable from it. This gap is already recorded
  at `step_5_02_audio_mix.SPEECH_LOUDNESS_IS_UNMEASURED`, with what closing it would
  cost; unwiring prosody does not worsen it, but it does close the one route that might
  have supplied it as a side effect.

Of the 86 recorded decisions across 001's traces, **one named prosody (D05)**, and three
of the four questions it was wanted for are answered above. `speech_coverage`,
`camera_stability` and `usable_ranges` do **not** substitute: they are picture and
coverage measurements on different axes.

## 5. What the unwiring cost, and what it unblocked

`prosody_analysis` was declared `required: true` on step 2.01 `creative_direction` and
routed by a DAG edge. With the hollow output correctly refused, the key was absent from
`step_outputs` and `gather_step_inputs` raised:

    RuntimeError Step 'creative_direction': data_mapping expects key 'prosody_analysis'
    from upstream step 'prosody_analysis', but it is missing from that step's outputs.
    Available keys: []

That was one of the two entries holding every 001 run at `FAILED`, and a hard block on
the pipeline's first creative step.

**2.01 loses nothing real.** `view:prosody` rendered, on 001, exactly one line:

    {"prosody": {"not_measured": "17 of 17 clip(s) have no prosody measurement: parselmouth not installed"}}

and `grep -n -i prosody library/steps/step_2_01_creative_direction/handoff.md` returns
exactly one line, **127**: `| Reads | prosody_analysis |`. That is a row in the State
Interaction table - a routing declaration, not an instruction. The 2.01 prompt never
tells the model what to do with prosody.

**That line is the one thing this change did not touch.** `handoff.md` is frozen and the
captain has a separate open decision on it, so the disagreement is recorded in
`library/tools/input_contract.UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT` instead - the
mirror of `UNCONSUMED_DECLARATIONS`, gated from both sides so it cannot go stale
silently.

## 6. What would have to change to bring it back

Re-wiring is not one change:

1. **Install parselmouth** (`praat_parselmouth 0.4.7` has a working wheel for this
   interpreter) - without it the step measures nothing at all.
2. **Lift both contour caps**, or the footage the edit uses stays outside them.
3. **Make the aggregates per-passage**, since every question asked of prosody is.
4. **Remove the hardcoded HNR ladder** in `speech_advanced_pipeline.py:129-134`, which
   states an emotional-register word from three unmeasured constants.
5. **Give it a consumer** - re-declaring the input on 2.01 is not one; the prompt would
   still have to ask the model for something.

Items 2-4 are what this document measures. Overturn a number here before spending time
on item 1.
