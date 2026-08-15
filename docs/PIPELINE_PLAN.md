# Pipeline Plan: from "it runs" to "a person would publish this"

Audit date: 2026-08-15. Head at audit: `155bf31`.

The goal this document is measured against is the captain's: **make high
quality edits programmatically**. Footage in one end, an edit out the other
that someone would be happy to publish. Not a green DAG. Not a full
`step_outputs` map.

## How to read this

Every claim below about whether something reaches the screen cites the
**reader**: the code that consumes the value. A step that writes
`music_automation` into the manifest is not evidence that the music ducks.
The evidence is `resolve_build_timeline.py:1251-1257`, and what that code
does is add a coloured marker.

Three words are used precisely:

- **Rendered** - a reader turns the value into picture or sound in the export.
- **Partial** - a reader exists but delivers less than the producer designed.
- **Inert** - the manifest records it and no reader consumes it. The run
  reports success and the viewer sees nothing.

There are two renderers, and they are the only two things that matter:

| Renderer | Reads |
| --- | --- |
| `library/steps/step_6_01_render/resolve_build_timeline.py` | clip placement, conform zoom, CDL, PowerGrade, Fairlight, markers, neural directives |
| `library/tools/execution/apply_fusion_comps.py` -> `library/tools/fusion/comp_builder.py:build_effect_comp` | every per-clip VFX, every drawn transition, the film look |

Anything a planner emits that neither of those reads is inert, whatever the
manifest says.

---

## 1. What actually reaches the screen today

### Rendered

| Stage | Reader | Notes |
| --- | --- | --- |
| 1.02 catalog (fps, resolution, rotation) | `compile_manifest/step.py:654-711`, `resolve_build_timeline.py:482-512` | Drives timebase and conform. Real. |
| 2.02 speech sequence, 2.05 spine | `compile_manifest/step.py:860-920` -> V1 clips | The cut list. This is the load-bearing half of the pipeline and it works. |
| 3.01 A-roll assignment | `resolve_build_timeline.py:651-728` | Placed on V1 with linked A1 audio. |
| 3.02 B-roll | `resolve_build_timeline.py:747-785` | Placed on V2, video only. |
| 4.01 + 4.05 subtitles | `resolve_build_timeline.py:790-875` | Remotion ProRes 4444 segments on V3. Per-word timing and emphasis both reach the picture (`AnimatedWord.tsx:39-49`). |
| 4.02 transitions (4 types only) | `apply_fusion_comps.py:54-82` -> `comp_builder.py:111-117` | `fade_to_black`, `zoom_blur`, `defocus`, `flash`. `transition_vocabulary.py` is honest about the rest. |
| 4.03 VFX (zoom family, shake) | `comp_builder.py:49-94` | Parameter names now match the dispatch. |
| 4.04 SFX + SFX ducking | `resolve_build_timeline.py:1045-1101` | Placed on A3+, and `volume_db` is applied per clip at 1087-1096. |
| 4.06 motion graphics | `resolve_build_timeline.py:880-914` | Remotion segments on V4. See section 2 on what they draw. |
| 5.01 colour, node_2 only | `resolve_build_timeline.py:1296-1318` | CDL slope/offset/power/saturation via `SetCDL`. |
| 5.01 colour, node_4 film look | `compile_manifest/step.py:1125-1130` -> `comp_builder.py:66-104` | Glow, grain, vignette merged onto every V1/V2 clip. |
| 5.01 PowerGrade | `resolve_build_timeline.py:1320-1333` | Real, and now reachable for exactly one template (see fix commit). |
| 6.01 export | `resolve_render.py` | Audio explicitly enabled and probed. |
| 6.02 validation | `render_qa.py`, `step_6_02/step.py:80-208` | Resolution, fps, duration, LUFS, black frames, audio streams. |

### Partial

**Conform is a dead-centre crop, or a letterbox.**
`resolve_build_timeline.py:152-171` sets `ZoomX`/`ZoomY` and nothing else.
There is no `Pan`. Worse, `compile_manifest/step.py:702-704` returns
`needs_conform: False` whenever the vision pass says the primary subject is
visible in the clip's source range, on the reasoning that a centre crop
might cut them off. The consequence for 16:9 source in a 9:16 timeline is
that the subject-bearing shots, which is most of the A-roll, render as a
horizontal strip with roughly two thirds of a vertical frame black. Nothing
fails: `_assert_timeline_fully_covered` only asks that a clip exists, and
`render_qa.detect_black_frames` only fires on a fully black frame.

**Music plays at unity and never ducks.** `compile_manifest/step.py:974-983`
builds the A2 clip with no `volume_db` key at all. The reader,
`resolve_build_timeline.py:1022`, is `vol_db = clip.get('volume_db')`, so it
is `None` and the volume set is skipped. The music sits at source level
under the speech for the whole video.

**Neural directives are best-effort and honest about it.**
`resolve_build_timeline.py:1164-1206` calls `apply_stabilization` and
`apply_super_scale` and records a warning when Resolve declines. Correct
behaviour, unverifiable here.

### Inert: recorded, never read

These are the current instances of the failure mode this codebase is prone
to. Each one is a thing the manifest says happened.

**1. The music mix.** `step_5_02_audio_mix` designs `track_levels`,
per-block `music_automation` and a `master_limiter`. The only reader is
`resolve_build_timeline.py:1243-1257`, and it calls `timeline.AddMarker` for
each one. A purple marker reading "Master Limiter: -1.0dBTP" and cyan
markers reading "Duck or boost the music track to this target level". Those
are notes to a human editor. `track_levels` has no reader at all: A1's
compressor, the -12dB SFX bed, the A4 transition-audio bus are three designs
with nowhere to go. AGENTS.md section 10 says `manifest_validator` asserts
"monotonic ducking curves"; it does not. There is no ducking check in
`library/tools/manifest_validator.py`.

**2. The block-type look library.** `library/tools/fusion/presets.py:16-59`
defines seven curated per-block looks (HOOK punch and glow, EMOTIONAL_PEAK
with grain, OUTRO with a 15-frame fade). The reader is
`apply_fusion_comps.py:170` and `:248`, `label in SEGMENT_PRESETS`. Labels
are built at `compile_manifest/step.py:880,897,914` as
`f"{block['block_type']}_{block['position']}"`, and `block_type` is
lowercase (`spine_contract.py:55`). `"hook_1" in {"HOOK", ...}` is never
true. `SEGMENT_RECIPES` and `CompEngine.from_preset` are reachable only from
`library/tools/fusion/tests/test_effects.py`.

**3. The preset index.** `library/tools/preset_indexer.py` is imported by
`tests/test_preset_indexer.py` and `tests/test_full_pipeline_integration.py`
and by nothing in `library/steps/`. Its mood and energy matching, and with
it every `.meta.json` descriptor, reaches no render. The only presets that
reach a timeline do so by direct path construction: `.drx` at
`step_5_01_color_grade/step.py:220-235` and `.setting` at
`builtin_effect_loader.py`. Consequently `library/presets/luts/` and
`library/presets/dctls/` have no reader at all. `halation.dctl` is a
disabled node with a shipped asset.

**4. The title macros.** `library/presets/fusion-macros/intro_lower_third.setting`
and `outro_subscribe.setting` are real files with real metadata, landed one
commit before this audit. `library/tools/fusion_macro_loader.py` has zero
consumers outside `tests/`. `tests/test_title_macros.py` asserts the
descriptor names the file, the file is non-empty, and `load_macro` returns
non-empty. That is exactly the test shape that let the preset library
advertise thirteen looks for months: it counts descriptors. No intro or
outro title is ever placed on a timeline.

**5. Three of five Remotion compositions.** `Root.tsx` registers
`SubtitleOverlay`, `MotionGraphics`, `LucieEndCard`, `LucieLogoAnimation`
and `FourthWallOverlay`. The pipeline renders two:
`step_4_05_render_subtitles/step.py` and
`step_4_06_render_motion_graphics/step.py:138`. `library/tools/execution/import_endcard.py`
has zero references anywhere including tests, so the end card has no route
in either.

**6. Beat alignment uses a synthetic grid.** `step_2_06_music_analysis` runs
librosa and produces a real beat grid. `dag.json` wires
`music_analysis -> plan_transitions`. `step_4_02_plan_transitions/bridge.py:24-33`
and `post_bridge.py:184-190` ignore it and synthesise
`[i * 60/bpm for i in ...]` starting at t=0. Since no track's first beat
lands at 0.000s, every "beat-snapped" cut is snapped to a grid offset from
the music by the track's lead-in. `plan_sfx` is the only step that reads the
real `music_analysis.beat_grid.bars` (`post_bridge.py:344-345`). AGENTS.md's
claim that `mesh_spine` consumes it for beat-aligned spine construction is
not true either: `step_2_05/post_bridge.py:133-141` reads `music_selection`
for a track id only.

**7. Subtitle safe-zone QA always passes.**
`library/tools/subtitle_qa.py:125-147` looks for `x`, `y`, `x_pct`, `y_pct`
on each subtitle entry. `step_4_01_plan_subtitles/step.py:319-335` emits
`id`, `timeline_start`, `timeline_end`, `text`, `emphasis_words`,
`spine_block_position`, `word_count`, `words`. No coordinates. `y is None`
for every entry, so `top_violations`, `bottom_violations` and
`margin_violations` are empty on every run and all three metrics report
pass. The real caption position is hardcoded at
`SubtitleOverlay/index.tsx:87-89` and the gate cannot see it.

**8. Brand template style never reaches the caption.**
`generate_remotion_props.py:145-153` reads `subtitle_data.get("style", ...)`
where `subtitle_data` is the `subtitle_plan` from 4.01, which never writes a
`style` key. So every project renders Montserrat 800 at 58px, white with a
`#FBF0B8` accent, bottom, 4px black outline, regardless of template. Every
template's `style.color_palette` and `effect.subtitle_style`
("bold_large", "minimal", "clean_standard") reach nothing.

**9. Two dead branches in the renderers.**
`resolve_build_timeline.py:330` binds `vfx_entries` and never uses it again.
`apply_fusion_comps.py:329` gates on `vfx.get('type') == 'zoom_pulse'`; VFX
entries carry `effect_type`, not `type`, and `zoom_pulse` is not in the
effect toolkit, so the branch cannot fire.

**10. Unused execution tools.** `library/tools/execution/sfx_placer.py`
(1445 lines) is imported only by `tests/test_sfx_placer.py`.
`build_powergrade.py` (434 lines) and `import_endcard.py` have no reference
anywhere in the repo, including tests. `library/tools/agent_poll.py` has
zero references of any kind. `apply_native_transitions.py` is documented as
withdrawn and kept deliberately.

---

## 2. The gap to "high quality"

What a viewer would actually notice, ordered by how loud it is.

**Landscape A-roll renders letterboxed.** Section 1 under Partial. In a
vertical feed this is the single most obvious amateur tell there is: a
postage-stamp strip of video with black above and below. It is not a bug in
the sense of an accident; `compile_manifest/step.py:702-704` chooses it
deliberately as the safer of two bad options, because the alternative on
offer is a dead-centre crop that can behead the speaker. The real answer is
a subject-aware crop offset, and the vision pass already measures where the
subject is.

**The music is as loud as the voice.** No ducking, no automation, no
limiter. Everything the mix step designed comes out as timeline markers. On
a phone speaker this reads as unlistenable, not as "slightly hot".

**The music starts and stops abruptly.** `compile_manifest/step.py:976-983`
sets `source_in: 0.0` and `timeline_out: total_duration`, then clamps to the
last V1 clip end (1205-1211). No fade in, no fade out. The track starts
mid-nothing at frame 0 and is guillotined at the last frame.

**Cyan corner brackets on every frame.**
`generate_motion_props.py:102` sets `show_accents = True` unconditionally for
every block, and `MotionGraphics/index.tsx:105-156` draws four glowing
L-brackets in `accentColor` in the corners plus a 12px progress bar along
the bottom. The default accent is `#00D4FF`
(`generate_motion_props.py:35-38`), which is not any template's palette.
That is a 2016 free-template look burned into every second of the video, and
it is not switchable from any config the pipeline reads.

**`cut_out` renders the picture floating in black.**
`step_4_03/post_bridge.py:65-69` maps `cut_out` to `zoom_start/mid/end` of
0.95, 0.9 or 0.85. `effects.py:147-151` sets `Transform.Size` to that
constant. A Transform below 1.0 shrinks the image inside its own frame with
transparency around it, and there is nothing under it on V1, so it composites
to black borders. You cannot pull out past the edge of the source. This one
needs a design decision, not a parameter change (see section 4).

**Typography is a webfont loaded over HTTP at render time.**
`remotion-subtitles/src/index.css:3` is
`@import url('https://fonts.googleapis.com/css2?family=Montserrat...')`.
There is no `delayRender` and no `@remotion/google-fonts` handle, so
whether the correct font is in place when Remotion starts rasterising is a
race with the network. On a miss the captions silently render in Chromium's
fallback sans at a different width, which changes line breaking as well as
the letterforms. Nothing downstream can tell.

**Captions are lowercased.** `step_4_01/step.py:318` calls
`.lower().strip()` on every caption. That is a defensible style, but it is a
style decision hardcoded in a planner rather than expressed in the brand
template. Question for the captain in section 4.

**Colour is one CDL and, for one template, a PowerGrade.** node_1 and node_5
of the designed grade are project-level colour management and are recorded
as undelivered with reasons, which is the right call. But the practical
result is that the shipped look is an exposure-matched slope plus a fixed
+0.02 lift and 1.12 saturation, with a Fusion glow/grain/vignette on top. It
is uniform, it is not wrong, and it is not a look.

**Every drawn transition is a per-clip effect.** No dissolve, no wipe, no
whip pan, and this is a hard architectural ceiling on the current route
(`transition_vocabulary.py:46-84`). The four available types are all "punch"
transitions. An edit that needs a soft transition cannot have one.

**Pacing has no reader for its own target.** Brand templates carry
`pacing.cuts_per_minute_min/max`. `step_5_03_creative_cohesion` reads
`creative_direction.pacing` and scores it, but the score is advisory: nothing
re-cuts. The cut rhythm is whatever `speech_sequence` and `mesh_spine`
produced.

---

## 3. What to build, in what order

Ordering is by what unblocks what, then by change-to-the-finished-video per
unit of work.

### Phase 0: honesty. Cheap, and it unblocks judgement.

You cannot prioritise against a manifest that lies. Everything in Phase 0 is
under a day and makes the next four phases measurable.

- **P0.1 Delete or wire the inert list.** Section 5 names what should go.
  Cheap.
- **P0.2 Make an inert key a test failure, not a discovery.** There is one
  precedent that works: `tests/test_transition_vocabulary.py` checks the
  handoff, the templates, the fallback and the dispatch against one
  enumeration. Generalise it. A manifest-key manifest: for each top-level
  manifest key, the module and function that reads it, asserted by a test
  that greps the two renderer modules. Any new key without a reader fails
  CI. Cheap, and it is the single highest-leverage item in this document
  because it retires the failure mode rather than one instance of it.
- **P0.3 Fix the vacuous gates.** Subtitle safe-zone QA must read the real
  rendered geometry, or be deleted. A check that structurally cannot fail is
  worse than no check because it reads as coverage. Cheap.
- **P0.4 Correct the docs that describe readers that do not exist.**
  AGENTS.md claimed `manifest_validator` asserts monotonic ducking curves
  (it has no ducking check) and `step_2_06`'s docstring claimed `mesh_spine`
  and `audio_mix` consume its beat grid (neither does). Cheap, and **done on
  this branch** in commit 3 below.

### Phase 1: framing. The biggest single change to the finished video.

- **P1.1 Subject-aware conform.** Replace the letterbox fallback with a crop
  whose horizontal and vertical offset comes from the vision pass's subject
  position, applied as `Pan`/`Tilt` alongside the existing `ZoomX`/`ZoomY`
  in `_apply_conform`. The measurement already exists; nothing consumes it
  for framing. **Medium.** The renderer change is small; deciding the
  offset policy and validating it on real footage is the work.
- **P1.2 Fail compilation on a letterboxed clip.** Once P1.1 lands, a clip
  that would still letterbox is a planning failure, not an output. Add it to
  `manifest_validator` semantics. Cheap once P1.1 exists.

### Phase 2: audio. Second biggest, and mostly mechanical.

- **P2.1 Real music level.** At minimum, carry a `volume_db` on the A2 clip
  so the existing reader at `resolve_build_timeline.py:1022` applies it.
  Cheap, and it converts "unlistenable" to "acceptable" on its own.
- **P2.2 Real ducking.** Per-block music automation needs keyframed track
  volume, which the Resolve scripting API does not expose on a timeline
  item. Two routes: split the A2 clip at each `music_automation` boundary
  and set a per-segment level (works today with the existing reader), or
  render the ducked music offline with ffmpeg `sidechaincompress` and place
  one pre-mixed file. **Medium.** Route choice is a captain question.
- **P2.3 Music fades.** A 1s fade in and a 1.5s fade out, wherever P2.2
  lands. Cheap.
- **P2.4 Master limiter.** Currently a marker. `resolve_render.py` already
  probes the output; a post-render ffmpeg `loudnorm` pass to the -14 LUFS
  that `render_qa.measure_lufs` already checks against is the direct path.
  Cheap, and it closes the loop with an existing gate.

### Phase 3: the look. Where "publishable" is actually decided.

- **P3.1 Motion graphics under template control.** The corner brackets and
  progress bar must be opt-in per template, and `accentColor` must come from
  the template palette. Cheap.
- **P3.2 Subtitle style from the brand template.** Wire
  `style.typography` and `effect.subtitle_style` through 4.01 into the
  Remotion props, and delete the hardcoded default at
  `generate_remotion_props.py:145-153`. Cheap, high visibility.
- **P3.3 Self-host the font.** Bundle the .ttf and load it with
  `@remotion/google-fonts` or `staticFile` plus `delayRender`, so
  typography is deterministic. Cheap.
- **P3.4 The block-type look library, or delete it.** Either fix the label
  casing so `SEGMENT_PRESETS` can be selected, or remove it and
  `SEGMENT_RECIPES` with it. Cheap either way; the decision is a captain
  question because it changes what the edit looks like.
- **P3.5 More than one PowerGrade.** Two of four templates now name no grade
  at all because their assets do not exist. Authoring `.drx` files needs
  Resolve and taste. **Large**, and mostly not engineering.

### Phase 4: rhythm.

- **P4.1 Use the real beat grid.** `plan_transitions` should read
  `music_analysis.beat_grid` instead of synthesising one. The data is
  already wired through the DAG. Cheap, and it makes every beat-snapped cut
  actually land on a beat.
- **P4.2 Close the pacing loop.** `creative_cohesion` scores cuts per minute
  against the template and nothing acts. Either feed the score back into a
  re-cut, or stop pretending pacing is controlled. **Medium.**

### Phase 5: the ceiling.

- **P5.1 Transitions that mix two clips.** Cross dissolve, wipe and whip pan
  are impossible on the per-clip Fusion route, and the two alternative routes
  are closed by ruling. The only remaining path is pre-rendering a two-clip
  transition segment with ffmpeg and placing it as a clip. **Large**, and it
  should not start before Phases 1 to 3 are done, because a soft transition
  in a letterboxed video with music over the vocal changes nothing a viewer
  would notice.

---

## 4. Questions only the captain can answer

Each of these changes what gets built, and none can be settled from the code.

**Q1. Landscape source in a vertical timeline: crop or letterbox?**
Today the pipeline letterboxes any clip where the subject is visible, which
is most A-roll, so those shots render as a strip in a mostly black vertical
frame. The alternative is a subject-tracked crop that fills the frame and
throws away roughly half the width. There is a third option: crop, and put a
blurred scaled-up copy of the same frame behind the letterbox instead of
black. Which of the three should the pipeline default to, and should it ever
be per-clip?

**Q2. Should music ducking be real, and if so which route?**
Route A splits the A2 clip at every `music_automation` boundary and sets a
level per segment. Works with the reader that exists, and gives audible
steps at each boundary unless crossfaded. Route B pre-mixes the music
against the speech track with ffmpeg sidechain compression and places one
file, which sounds correct but means the music level is baked before the
timeline exists and cannot be adjusted in Resolve afterwards. Which matters
more: a mix that sounds right on export, or one an editor can still touch?

**Q3. Do the corner brackets and the progress bar stay?**
Every frame of every video currently carries four glowing L-shaped corner
brackets in cyan and a progress bar along the bottom edge. Nothing in any
config turns them off. Keep them as a house style with the colour driven by
the template palette, keep them only on hook blocks, or delete the accents
entirely and keep only the upper third?

**Q4. What should `cut_out` mean?**
It currently sets a Transform below 1.0, which shrinks the picture inside its
own frame and surrounds it with black. You cannot zoom out past the edge of
the source. The options are: (a) delete `cut_out` from the toolkit and record
the withdrawal, (b) make every clip default to a 1.15 baseline zoom so
`cut_out` can return toward 1.0 and read as a pull-back, at the cost of
throwing away 13% of every frame's resolution on every shot, or (c) keep the
shrink and fill the surround with a blurred copy of the frame. Which?

**Q5. Should captions be lowercase?**
`step_4_01` lowercases every caption. It is a deliberate look and it is
currently unconditional and hardcoded. Should it be a brand template setting,
and what should each of the four shipped templates use?

**Q6. What is the actual house look?**
One PowerGrade ships, `cinematic_warm.drx`, and it is a third-party gift with
no written commercial licence (AGENTS.md section 11). Two templates now name
no grade at all. Producing more requires grading a reference frame in Resolve
by hand. Is the captain willing to author two or three `.drx` files, or
should the pipeline drop the PowerGrade node and deliver its look entirely
through CDL plus Fusion, which it can do unaided?

**Q7. Do intros, outros and end cards belong in this pipeline?**
Three Remotion compositions (`LucieEndCard`, `LucieLogoAnimation`,
`FourthWallOverlay`), two Fusion title macros and an `import_endcard.py` tool
all exist and none is reachable. Either they get wired to a spine block type
and a template slot, or they should be deleted. Which, and if wired: does
every video get an outro card, or only some?

**Q8. What is "publishable" measured against?**
Every gate in the pipeline today is technical: resolution, fps, duration,
black frames, LUFS, audio streams. None of them would have caught a
letterboxed edit with music over the vocal. Is there a reference video, or
three, that the captain would call publishable? Without one, "high quality"
is unfalsifiable and every plan after Phase 0 is guesswork calibrated on my
taste rather than theirs. This is the single most valuable thing the captain
could hand back.

**Q9. Which brand template is the real one?**
Four ship. `shortform_energetic` is the one matching what AGENTS.md says this
pipeline makes. If the other three are aspirational, they are three more
surfaces that will drift out of sync with the renderers, and the honest move
is to keep one.

---

## 5. What should be deleted

A pipeline that advertises less and delivers all of it is better than one
that claims more. In rough order of how much noise removal saves:

| Delete | Why |
| --- | --- |
| `library/tools/execution/sfx_placer.py` (1445 lines) | Imported only by its own test. SFX resolution lives in `compile_manifest/step.py:1026-1082` and placement in `resolve_build_timeline.py:1045-1101`. This is a second, parallel, unreferenced implementation. |
| `library/tools/execution/build_powergrade.py` (434 lines) | Zero references in the repo, including tests. |
| `library/tools/execution/import_endcard.py` | Zero references in the repo, including tests. Delete, or wire per Q7. |
| `library/tools/agent_poll.py` | Zero references of any kind. |
| `library/tools/preset_indexer.py` and every `.meta.json` it indexes | No pipeline consumer. Presets that reach a timeline are found by direct path. Keep the asset files, delete the index and its mood matching, or wire it per Q6. |
| `library/presets/luts/`, `library/presets/dctls/` | No reader. `film_emulation.meta.json` has no asset at all; it points at a path inside a Resolve install and `preset_indexer.py:36-42` explicitly exempts it from the existence check. |
| `library/tools/fusion_macro_loader.py` and `tests/test_title_macros.py` | No pipeline consumer. The test asserts the descriptor and the file, which is the exact shape that hid the preset library problem. Delete both, or wire per Q7. |
| `SEGMENT_RECIPES` and `CompEngine.from_preset` | Reachable only from `library/tools/fusion/tests/test_effects.py`. Duplicates `SEGMENT_PRESETS_FLAT`. |
| `resolve_build_timeline.py:330` (`vfx_entries`) | Bound and never used. |
| `apply_fusion_comps.py:328-336` (`zoom_pulse`) | Gates on a key VFX entries do not carry, for a type not in the toolkit. |
| `resolve_build_timeline.py:590-649` (J/L cut offsets) | Documented as unreachable and deliberately kept. Keep only if Q2's answer is that the audio pass will revive it; otherwise it is 60 lines of live-looking code that cannot run. |
| `content.intro_template`, `content.outro_template`, `content.watermark` in `default_brand.yaml` | No reader. `library/assets/watermark.png` does not exist; `library/assets/` does not exist. |
| `LucieEndCard`, `LucieLogoAnimation`, `FourthWallOverlay` | Registered in `Root.tsx`, rendered by nothing. Delete or wire per Q7. |
| `smart_reframe` in the manifest | Emitted with `"unverified_by_design": true` and `"status": "behaviour unknown"` (`compile_manifest/step.py:1276-1280`). A capability nobody has confirmed exists. Verify it or drop it. |

Not on this list, deliberately: `apply_native_transitions.py`, which AGENTS.md
keeps on purpose as a record of a closed route, and every regression fixture
in `tests/fixtures/captured_run/`.

---

## What changed in this branch

Three fixes, each its own commit, separate from this document.

1. `fix(fusion): bind the built-in effect loader before the generator pass`.
   `apply_fusion_comps` imported `import_effect_to_clip` inside the
   `if has_any_effects:` branch and read it unconditionally in the
   generator-overlay pass at the bottom of the function. A plan with
   generator overlays but no per-clip VFX and no drawn transitions killed the
   subprocess with a `NameError` before a single `.setting` file reached a V5
   carrier clip, and `resolve_build_timeline.py:1122-1125` records that only
   as a warning. Not verified in a render: it needs DaVinci Resolve.

2. `fix(templates): drop PowerGrade names with no asset on disk`.
   `shortform_energetic` named "High Contrast Pop" and `cinematic_narrative`
   named "Moody Desaturated"; neither `.drx` exists. `step_5_01` writes the
   unresolved path into the manifest on purpose so the renderer fails loudly,
   and it does, at `resolve_build_timeline.py:1321-1327`. No run under either
   template could produce a video. Adds the check that would have caught it,
   alongside the transition-vocabulary check that already covers the other
   half of these templates.

3. `fix(docs): correct two claims about readers that do not exist`. Two of
   the P0.4 items above, done rather than planned. AGENTS.md claimed
   `manifest_validator` asserts monotonic ducking curves, and
   `step_2_06_music_analysis`'s docstring claimed `mesh_spine` and
   `audio_mix` consume its beat grid. Neither reader exists. Both read as
   coverage for things nothing covers, which is how the failure mode in this
   document propagates into the next agent's assumptions.

Nothing architectural was changed, and no test, validator, assertion or QA
check was weakened, disabled, skipped or deleted.
