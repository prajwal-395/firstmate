# Stems and ducking: Reel 22 native fidelity (2026-10-13)

Step 6 of the native-fidelity push. The native model writer now preserves
music/SFX roles from the reel audio track plan and maps the declared
word-gap ducking plan onto placed music uses. Reel 22's available source
records contain no such plan or uses, so this run cannot exercise the
feature without inventing an edit.

## Source audit

- The read-only Reel 22 `pipeline_data.json` has no `audio_mix` capability
  output or music/SFX planning output.
- Its recorded timeline snapshot has the two dialogue audio rows only.
- The step 1 native model has 33 clip uses and no audio clip uses; its
  builder's track plan contains only video tracks.
- Therefore the step 1 AFTER model carries **0 music uses, 0 SFX uses,
  and 0 ducking events**. The ducking depth is not measurable because no
  ducking event is declared. No candidate library asset was assigned to
  this reel.

## Audio measurement and render comparison

The step 1 AFTER audio measurement records stereo output with the right
channel silent at approximately -72 dB RMS and left dialogue at -28.9 dB
RMS. The step 1 AFTER model has no audio ClipUses or audio effects, so
this lane supplies no audio change to render; its measurable before/after
is consequently 0 dB change on both recorded channels, with no measured
ducking depth. This is the same audio state documented for the native
BEFORE render. A native rerender would repeat that output without testing
stems or ducking, so it would not establish the requested source-to-render
comparison.

The unchanged step 1 visual comparison is the only available render
comparison for this lane:

| comparison | step 1 AFTER SSIM-Y | step 1 AFTER PSNR-y |
|---|---:|---:|
| native vs deliverable (1183f) | 0.6664 | 20.36 dB |
| native vs fresh Resolve (1254f) | 0.5037 | 14.23 dB |

These are the step 1 measurements, not a new stems-on render. The source
records provide no audio plan from which to build one. As step 1's method
notes, the deliverable's right channel is silent at approximately -72 dB
RMS and left dialogue is approximately -28.9 dB RMS; the native render
matches that audio layout, while the separate fresh Resolve reference is
not used to infer a stem assignment.

## Result

The code path is wired for declared reel music/SFX tracks and
`audio_mix_spec` ducking automation. Reel 22 cannot demonstrate a gap
closure because the read-only source has no music/SFX selections or
`audio_mix_spec`. Supplying a measured reel audio plan is required to
produce stem and ducking events; choosing library assets or inventing
ducking intervals here would be unsupported.

## Follow-up audit of all project reels

Inbox 004 requested a second reel with recorded music, SFX and mix
automation. The read-only project folder has no eligible candidate:

- `pipeline_data.json` enumerates 13 saved `reel.build.track_plans`; the
  only audio roles are `speech`. No capability output contains
  `audio_mix_spec`, `music_ducking_plan` or `music_automation`.
- The review folder contains 53 timeline snapshots spanning all 31
  numbered reels. Their 108 audio-track rows are named `Akshita CH1`,
  `Craig CH1`, or the empty `Audio 3`; the snapshots contain zero
  non-dialogue audio clip paths.
- The project's `project_model/` contains only `.lock`, with no recorded
  model carrying additional reel plans.

Thus no reel meets the requested source criteria, so a native stems render
and comparison against a shipped export cannot be made without supplying
new project data. This audit is read-only; no export, timeline or project
file was changed.
