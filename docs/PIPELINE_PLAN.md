# Pipeline Plan: from "it runs" to "a person would publish this"

Audit date: 2026-08-15. Head at audit: `155bf31`.

**Reconciled 2026-08-16 at `d8bb263`.** Items that landed between the audit
head and this reconciliation are struck where they appear, not rewritten.
Phase 0 is complete: the residue named at the end of section 3 was gated on
Q7, and Q7 is answered (2026-08-16/17) and delivered.

The goal this document is measured against is the captain's: **make high
quality edits programmatically**. Footage in one end, an edit out the other
that someone would be happy to publish. Not a green DAG. Not a full
`step_outputs` map.

## "Phase" in this document

Phase 0/1/2/3/4 below are stages of the QUALITY-WORK PROGRAMME - honesty,
framing, audio, colour, rhythm. They are not the pipeline's own step
phases, and the two used to collide on "Phase 1".

They no longer do. The pipeline's analysis stage - steps 1.01 to 1.07, the
work that turns footage into transcripts, vision profiles and prosody - is
now the **preflight** stage, named as such in every step manifest
(`classification.stage`), in the runner, in the dashboard and in the
`--rerun` flag. See `library/tools/step_ledger.py`. Where this document
says "phase 1" it means framing, and nothing else.

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
| `library/steps/step_6_01_render/resolve_build_timeline.py` | clip placement, conform zoom, CDL, Fairlight, markers, neural directives |
| `library/tools/execution/apply_fusion_comps.py` -> `library/tools/fusion/comp_builder.py:build_effect_comp` | every per-clip VFX, every drawn transition, the film look |

Anything a planner emits that neither of those reads is inert, whatever the
manifest says.

---

## 1. What actually reaches the screen today

### Rendered

| Stage | Reader | Notes |
| --- | --- | --- |
| delivery format (brand template / project override) | `compile_manifest/step.py` -> `manifest.project.resolution` -> `resolve_build_timeline.py:399` | THE RENDER TARGET. `library/tools/delivery_format.py`, default vertical 1080x1920. Captain's ruling 2026-08-19. Also drives the overlay render size (4.05/4.06), the planners' target frame (3.01/3.02) and the 6.02 resolution gate. |
| 1.02 catalog (fps, `source_resolution`, rotation) | `compile_manifest/step.py:654-711`, `resolve_build_timeline.py:482-512` | Drives timebase and conform. Real. `source_resolution` DESCRIBES the footage and is never a render target - as `project_resolution` it was, and shipped a landscape master. |
| 2.02 speech sequence, 2.05 spine | `compile_manifest/step.py:860-920` -> V1 clips | The cut list. This is the load-bearing half of the pipeline and it works. |
| 3.01 A-roll assignment | `resolve_build_timeline.py:651-728` | Placed on V1 with linked A1 audio. |
| 3.02 B-roll | `resolve_build_timeline.py:747-785` | Placed on V2, video only. |
| 4.01 + 4.05 subtitles | `resolve_build_timeline.py:790-875` | Remotion ProRes 4444 segments on V3. Per-word timing and emphasis both reach the picture (`AnimatedWord.tsx:39-49`). |
| 4.02 transitions (4 types only) | `apply_fusion_comps.py:54-82` -> `comp_builder.py:111-117` | `fade_to_black`, `zoom_blur`, `defocus`, `flash`. `transition_vocabulary.py` is honest about the rest. |
| 4.03 VFX (zoom family, shake) | `comp_builder.py:49-94` | Parameter names now match the dispatch. |
| 4.04 SFX placement | `resolve_build_timeline.py:1045-1101` | SFX clips are placed on A3+. That half is real. ~~and `volume_db` is applied per clip~~ - see Partial below: the level is not. |
| 4.06 motion graphics | `resolve_build_timeline.py:880-914` | Remotion segments on V4. See section 2 on what they draw. |
| 5.01 declared look, CDL half (node_2) | `resolve_build_timeline.py:1276-1327` | Slope/offset/power/saturation from the look a brand template DECLARED, via `SetCDL`. No template declares one, so nothing is applied today. |
| 5.01 declared look, Fusion half (nodes 3-4) | `compile_manifest/step.py:1125-1130` -> `comp_builder.py:59-104` | Contrast, glow, grain and the (optionally coloured) vignette, merged onto every V1/V2 clip - only where a template declared them. |
| 6.01 export | `resolve_render.py` | Audio explicitly enabled and probed. |
| 6.02 validation | `render_qa.py`, `step_6_02/step.py:80-208` | Resolution, fps, duration, LUFS, black frames, audio streams. |

### Partial

**Conform is a per-clip creative parameter, not a binary decision, and it
now tracks the subject.** `compile_manifest/step.py:_conform_fields` accepts
`framing_intent` (0.0 = letterbox, 1.0 = fill), `framing_pan_x` (-1.0 to 1.0
normalised) and `subject_center_x` (0.0-1.0 across the source width).
`resolve_build_timeline.py:_apply_conform` sets `ZoomX`/`ZoomY` and Resolve's
`Pan`/`Tilt` - ~~`PanX`/`PanY`~~, which Resolve does not have and which
therefore moved nothing for the life of P1.1. ~~When `framing_intent` is
unset (None), the legacy subject-visibility heuristic runs so existing
projects render identically.~~ That heuristic is DELETED (P1.4);
`_conform_fields` has one branch, and where the number comes from is one
enumeration, `library/tools/framing_intent.py`: spine block > project.yaml
`pipeline.framing_intent` > brand template `style.framing_intent` > fill.
An explicit per-clip pan still outranks the measurement. Tests:
`tests/test_framing_intent.py`, `tests/test_framing_parameter.py`,
`tests/test_subject_framing.py`.

**Per-clip SFX levels do not reach the mix.** This table listed 4.04 under
Rendered on the strength of `resolve_build_timeline.py:1087-1096` calling
`placed.SetProperty("Volume", linear_vol)`. Measured on Resolve 21.0.0b.28,
an audio `TimelineItem` reports an EMPTY property dict and
`SetProperty("Volume", 0.5)` returns **False** with a readback of `None`.
The call site discards the return value, so every SFX plays at source
level and the run says nothing. The SFX are placed and audible; only the
level is lost. The same measurement applies to the `Volume` call on the
music clip at :1054.

Not fixed, deliberately: audio is out of scope by the captain's ruling of
2026-08-15, and this entry exists because a plan that overstates what
reaches the screen is the exact problem Phase 0 was for. Anyone reviving
the audio thread should start by finding what Resolve *does* accept for a
clip level - the property dict being empty suggests it is not a
TimelineItem property at all on this build.

~~**Music plays at unity and never ducks.** `compile_manifest/step.py:974-983`
builds the A2 clip with no `volume_db` key at all. The reader,
`resolve_build_timeline.py:1022`, is `vol_db = clip.get('volume_db')`, so it
is `None` and the volume set is skipped. The music sits at source level
under the speech for the whole video.~~ **DONE.** The bed now carries the
per-block curve `audio_mix` plans, delivered through an OTIO round trip; a
`volume_db` on the A2 clip was never the mechanism, because
`SetProperty("Volume", ...)` returns False on every audio item. Measured on
001: silence at the file's floor where the spine planned `silent`, -18 dB
under background speech, -6 dB where it planned `prominent`, exact to
0.1 dB. See AGENTS.md section 5, "The mix goes through OTIO".

**Neural directives are best-effort and honest about it.**
`resolve_build_timeline.py:1164-1206` calls `apply_stabilization` and
`apply_super_scale` and records a warning when Resolve declines. Correct
behaviour, unverifiable here.

### Inert: recorded, never read

These are the current instances of the failure mode this codebase is prone
to. Each one is a thing the manifest says happened.

**1. The music mix.** ~~`step_5_02_audio_mix` designs `track_levels`,
per-block `music_automation` and a `master_limiter`. The only reader is
`resolve_build_timeline.py:1243-1257`, and it calls `timeline.AddMarker` for
each one. A purple marker reading "Master Limiter: -1.0dBTP" and cyan
markers reading "Duck or boost the music track to this target level". Those
are notes to a human editor.~~ **DONE.** `music_automation` and every clip's
`volume_db` reach Fairlight through `library/tools/otio_mix.py`; the markers
are the fallback and now say UNAPPLIED. `master_limiter` stays a marker on
purpose - it is a master BUS setting and no clip-level route reaches it.
`track_levels` is read only for its `fade_duration_seconds`, which sets the
ramp between two planned levels; A1's compressor, the -12dB SFX bed and the
A4 transition-audio bus are still three designs with nowhere to go. ~~AGENTS.md section 10 says `manifest_validator` asserts
"monotonic ducking curves"; it does not.~~ The doc claim was corrected under
P0.4; there is still no ducking check in `library/tools/manifest_validator.py`
and, audio being out of scope, there is not going to be one.

**2. The block-type look library.** ~~**DELETED** under P3.4; see section 3
for why the casing fix in the text below would not have worked.~~
`library/tools/fusion/presets.py:16-59`
defines seven curated per-block looks (HOOK punch and glow, EMOTIONAL_PEAK
with grain, OUTRO with a 15-frame fade). The reader is
`apply_fusion_comps.py:170` and `:248`, `label in SEGMENT_PRESETS`. Labels
are built at `compile_manifest/step.py:880,897,914` as
`f"{block['block_type']}_{block['position']}"`, and `block_type` is
lowercase (`spine_contract.py:55`). `"hook_1" in {"HOOK", ...}` is never
true. ~~`SEGMENT_RECIPES` and `CompEngine.from_preset` are reachable only from
`library/tools/fusion/tests/test_effects.py`.~~ Those two were deleted in
#103; `SEGMENT_PRESETS` itself remains, still unselectable, and is P3.4.

**3. The preset index.** ~~`library/tools/preset_indexer.py`~~ **REMOVED**
along with `library/presets/luts/` and `library/presets/dctls/`, which had no
reader at all, and the descriptors for them. It was imported only by tests and
by nothing in `library/steps/`. Presets that reach a timeline still do so by
direct path: `.setting` at `builtin_effect_loader.py`. The two title macros
`default_brand.yaml` used to name are deleted - see item 4 - so
`library/presets/fusion-macros/` now ships no assets at all.

**4. The title macros.** ~~`library/presets/fusion-macros/intro_lower_third.setting`
and `outro_subscribe.setting` are real files with real metadata...
`tests/test_title_macros.py` asserts the descriptor names the file, the file
is non-empty, and `load_macro` returns non-empty. That is exactly the test
shape that let the preset library advertise thirteen looks for months.~~
**CLOSED** by deletion under Q7: both `.setting` files, both descriptors,
`tests/test_title_macros.py` and `content.intro_template`/
`content.outro_template` are gone. One was a full-frame pure red slate in
Helvetica; the other read "Subscribe!", which
`overall_branding_creative_direction.md:100` forbids by name. Neither could
load - both opened `Tools = ordered() {`. `fusion_macro_loader.py` survives
with no assets left to load; it is a generic reader, and deleting it is a
separate call.

**5. Three of five Remotion compositions.** ~~`Root.tsx` registers
`SubtitleOverlay`, `MotionGraphics`, `LucieEndCard`, `LucieLogoAnimation`
and `FourthWallOverlay`, and the pipeline renders two.~~ **PARTLY CLOSED**
by Q7, and this entry overstated it. The two `Lucie*` registrations
pointed at stub components that rendered a transparent frame and were
deleted; the real implementations live with the client's project and
reach a timeline through `content.bookends` - see
`library/tools/bookends.py`. That half is closed: the end card has a
route, and it is the manifest.

The `FourthWallOverlay` half was **not**, and is **now CLOSED for real**.
#119 generalised the component into `TimedTextOverlay` and added an
`effect.timed_text_overlay` template slot, and this entry recorded it as
a "template-declared overlay" - but nothing in `library/steps/` ever
imported `generate_timed_text_overlay_props`, so the three moments
`fourth_wall.yaml` declared reached no frame of any render and no run
said so. Registered-but-unrendered simply moved from the component to
the slot. The captain removed the declaration on quality grounds on
2026-08-20, `fourth_wall.yaml` itself was deleted (the series template
arrives later, whole, with authorisation), and the reader was authorised
as the first item in the ratified build order.

The route now exists, end to end: `plan_timed_text_segments`
(`library/tools/timed_text_overlay.py`) groups the declared moments into
non-overlapping segments and rebases their frames; `timed_text_render.py`
renders one ProRes 4444 alpha file per segment inside step 4.06;
`compile_manifest` carries them as the top-level `timed_text_overlay`
manifest key; `resolve_build_timeline` places them on **V6** and records
a QA failure for any declared segment that did not land. A moment is
timed from the spine, which is what `docs/ASSET_LIBRARY_PLAN.md`
(ratified 2026-08-20) section 5 requires.

`NO_READER` is deleted, and the guard that held the slot empty is
replaced by `tests/test_timed_text_overlay.py::test_a_pipeline_step_reads_the_slot`
plus `tests/test_timed_text_delivery.py`, which renders a fixture segment
through the real path and asserts the declared colours are in the
declared rows at the declared frames and absent outside them. That last
test is the difference between this entry and the one it replaces: the
previous "CLOSED" rested on the slot existing.

The slot now has a declared asset (2026-08-20, the second item in the
build order): Through the 4th Wall's Night card, in
`tests/fixtures/night_card_project/project.yaml`, anchored to the END of
the hook block so it lands after the cold open in an edit of any length.
It is declared by the PROJECT rather than by a brand template -
`timed_text_overlay.resolve_declaration`, because the copy is artwork and
artwork belongs with the project - and `tests/test_night_card_delivery.py`
renders it at the real 1080x1920 delivery format and asserts its ink
falls inside the picture band, rows 656..1263, that the pipeline actually
produces.

**6. Beat alignment uses a synthetic grid.** ~~**CLOSED** by P4.1 - and
`plan_sfx` was not reading the real grid either; the key it asked for has
never existed. Original text:~~ `step_2_06_music_analysis` runs
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

**8. Brand template style never reaches the caption.** ~~`generate_remotion_props.py:145-153`
reads `subtitle_data.get("style", ...)` where `subtitle_data` is the
`subtitle_plan` from 4.01, which never writes a `style` key. So every project
renders Montserrat 800 at 58px, white with a `#FBF0B8` accent, bottom, 4px
black outline, regardless of template.~~ **CLOSED** by P3.2. All four
templates now resolve to visibly distinct, legible captions, and the
hardcoded default is gone rather than moved elsewhere: an absent style
raises.

**9. Two dead branches in the renderers.** ~~`resolve_build_timeline.py:330`
binds `vfx_entries` and never uses it again. `apply_fusion_comps.py:329` gates
on `vfx.get('type') == 'zoom_pulse'`.~~ **CLOSED** in #103, both deleted.

**10. Unused execution tools.** ~~`sfx_placer.py` (1445 lines)~~ **DELETED**,
~~`build_powergrade.py`~~ **DELETED** with the PowerGrade route,
~~`agent_poll.py`~~ **DELETED**. ~~`import_endcard.py` still has no
reference anywhere in the repo including tests, and waits on Q7.~~
**DELETED**, superseded rather than dropped - see section 5.
`apply_native_transitions.py` is documented as withdrawn and kept
deliberately.

**11. Smart Reframe.** ~~The manifest carried a `smart_reframe` key with
`"unverified_by_design": true`, and `resolve_build_timeline.py` called
`apply_smart_reframe(timeline, ...)` and printed a tick whatever came back.~~
**CLOSED** by withdrawal; see the row in section 5. This is worth keeping on
the record because it is the failure mode in its purest form: not a value
nothing read, but a value something read, acted on, and reported success for,
while the picture never changed.

---

## 2. The gap to "high quality"

What a viewer would actually notice, ordered by how loud it is.

~~**Landscape A-roll renders letterboxed.**~~ **FIXED, P1.4.** In a
vertical feed this was the single most obvious amateur tell there is: a
postage-stamp strip of video with black above and below. It was not an
accident; `compile_manifest/step.py` chose it deliberately as the safer of
two bad options, because the alternative on offer was a dead-centre crop
that can behead the speaker. That alternative is no longer the one on
offer - the crop window follows the subject (P1.2), so filling the frame
and keeping the speaker in it are not in tension any more. The default is
fill; see P1.4.

**The music is as loud as the voice.** No ducking, no automation, no
limiter. Everything the mix step designed comes out as timeline markers. On
a phone speaker this reads as unlistenable, not as "slightly hot".

**The music starts and stops abruptly.** `compile_manifest/step.py:976-983`
sets `source_in: 0.0` and `timeline_out: total_duration`, then clamps to the
last V1 clip end (1205-1211). No fade in, no fade out. The track starts
mid-nothing at frame 0 and is guillotined at the last frame.

**Cyan corner brackets on every frame.** ~~`generate_motion_props.py:102`
sets `show_accents = True` unconditionally for every block~~ **CLOSED** by
P3.1 and Q3: accents are declared per template, never defaulted, and the
cyan is withdrawn. Original text: `generate_motion_props.py:102` set
`show_accents = True` unconditionally for every block, and `MotionGraphics/index.tsx:105-156` draws four glowing
L-brackets in `accentColor` in the corners plus a 12px progress bar along
the bottom. The default accent is `#00D4FF`
(`generate_motion_props.py:35-38`), which is not any template's palette.
That is a 2016 free-template look burned into every second of the video, and
it is not switchable from any config the pipeline reads.

~~**`cut_out` renders the picture floating in black.**
`step_4_03/post_bridge.py:65-69` maps `cut_out` to `zoom_start/mid/end` of
0.95, 0.9 or 0.85. `effects.py:147-151` sets `Transform.Size` to that
constant. A Transform below 1.0 shrinks the image inside its own frame with
transparency around it, and there is nothing under it on V1, so it composites
to black borders. You cannot pull out past the edge of the source. This one
needs a design decision, not a parameter change (see section 4).~~ **CLOSED**
by captain's ruling 2026-08-17: `cut_out` deleted from the toolkit,
superseded by the framing parameter (PR 109). A pull-back is now a lower
framing value.

**Typography is a webfont loaded over HTTP at render time.** ~~`remotion-subtitles/src/index.css:3`
is `@import url('https://fonts.googleapis.com/css2?family=Montserrat...')`.
There is no `delayRender`, so whether the correct font is in place when
Remotion starts rasterising is a race with the network.~~ **CLOSED** by
P3.3. The font is bundled, the load blocks the render, and a failure to
load raises instead of silently substituting.

**Captions are lowercased.** ~~`step_4_01/step.py:318` calls
`.lower().strip()` on every caption... a style decision hardcoded in a
planner rather than expressed in the brand template.~~ **CLOSED** in #102,
before this document was written: it is `effect.caption_case`, and all four
templates declare it. See Q5.

**Colour is CDL plus Fusion, and the values are DECLARED, not authored here.**
~~The shipped look is an exposure-matched slope plus a fixed +0.02 lift and
1.12 saturation - uniform, not wrong, and not a look.~~ ~~**CLOSED.** Four
looks are authored in `library/tools/series_look.py` from the captain's
planning docs, one named by each shipped template~~ **REOPENED AND CLOSED
AGAIN, 2026-08-28**: the captain ruled there is no house look and no settled
grain, so the four looks are REMOVED and `library/tools/series_look.py` holds
no values - it reads a declaration a brand template writes. No shipped
template declares one, so no project gets a grade today. The CDL half carries
hue and level and the Fusion half carries contrast, glow, grain and a shaped
vignette, exactly as before, wherever something declares them. node_1 and node_5 of the
designed grade remain project-level colour management, recorded as undelivered
with reasons, which is still the right call.

**Every drawn transition is a per-clip effect.** No dissolve, no wipe, no
whip pan, and this is a hard architectural ceiling on the current route
(`transition_vocabulary.py:46-84`). The four available types are all "punch"
transitions. An edit that needs a soft transition cannot have one.

**Pacing has no reader for its own target.** ~~Brand templates carry
`pacing.cuts_per_minute_min/max`. `step_5_03_creative_cohesion` reads
`creative_direction.pacing` and scores it, but the score is advisory:
nothing re-cuts.~~ **CLOSED by removal** (P4.2). It was worse than
advisory - the scorer read a key no producer emits, so it never ran at
all. The cut rhythm is still whatever `speech_sequence` and `mesh_spine`
produced, and the pipeline no longer implies otherwise.

---

## 3. What to build, in what order

Ordering is by what unblocks what, then by change-to-the-finished-video per
unit of work.

### Phase 0: honesty. Cheap, and it unblocks judgement.

You cannot prioritise against a manifest that lies. Everything in Phase 0 is
under a day and makes the next four phases measurable.

- **P0.1 Delete or wire the inert list.** ~~Section 5 names what should go.~~
  **DONE.** The Q7 residue closed too, with Q7. `sfx_placer.py`, `agent_poll.py`,
  `SEGMENT_RECIPES`, `CompEngine.from_preset`, the `vfx_entries` binding, the
  `zoom_pulse` branch, `content.watermark` and the J/L cut block went in #103;
  `build_powergrade.py`, `preset_indexer.py`, `luts/` and `dctls/` went with
  the PowerGrade route in #106. `smart_reframe` is now withdrawn: see the note
  at the top of `library/tools/neural_engine.py`. The intro/outro/end-card
  residue - `import_endcard.py`, the two title macros with their descriptors
  and test, `content.intro_template`/`content.outro_template`, and the two
  stub `Lucie*` compositions - was deleted when the bookend mechanism landed;
  the section 5 rows record what replaced each.
- **P0.2 Make an inert key a test failure, not a discovery.** ~~There is one
  precedent that works...~~ **DONE.** `tests/test_manifest_readers.py` (#105)
  discovers the top-level keys from `compile_manifest`'s manifest literal by
  AST and asserts each one is named in `EXPECTED_READERS` with a reader that
  really contains `manifest[key]`, or is in `EXEMPTED_KEYS` with a reason.
  Verified by injecting an unread key: the test fails with the key's name.
  Each entry now also carries one sentence on what the reader DOES with the
  value, asserted non-empty. That sentence is the part a static check cannot
  do for you - `smart_reframe` satisfied "a reader exists" for its whole life
  while its reader called a method Resolve does not expose and printed a tick
  regardless of the answer. It stays honest in both directions: `audio_mix`'s
  sentence says it draws markers for a human editor and changes no level.
- **P0.3 Fix the vacuous gates.** ~~Subtitle safe-zone QA must read the real
  rendered geometry, or be deleted.~~ **DONE.** The safe-zone gate was deleted
  in #104. Two more were found in the same sweep and both are now real:
  `timeline_qa.verify_fusion_comps` had `pass` as its only loop body and
  returned `passed=True` whatever the timeline held - it is the station that
  should have caught "no Fusion comp of any kind reached the picture", the
  collapse the renderer catches with an ad-hoc count next to the transition
  station. It now maps the manifest's label-keyed `fusion_effects.per_clip`
  onto the labels the run actually placed, per track, and reads
  `GetFusionCompNameList()`. `timeline_qa.verify_color_grades` swallowed every
  readback exception with a bare `except: pass` and reported pass; a failed
  readback is now recorded as a warning-severity check. `verify_audio`'s
  docstring claimed it checked per-track levels, which nothing sets.
- **P0.4 Correct the docs that describe readers that do not exist.**
  ~~AGENTS.md claimed `manifest_validator` asserts monotonic ducking curves
  (it has no ducking check) and `step_2_06`'s docstring claimed `mesh_spine`
  and `audio_mix` consume its beat grid (neither does).~~ **DONE**, verified
  at `d8bb263`: AGENTS.md section 10 now says the validator does NOT check
  ducking, and `step_2_06`'s docstring names `plan_sfx` as its only consumer
  and says outright that the DAG edges into 2.05 and 4.02 are not read.

~~**Phase 0 residue, gated on Q7.** Five section-5 entries all turn on the
same question - whether intros, outros and end cards belong in this
pipeline.~~ **CLOSED.** Q7 was answered "wire them up, but only on some
videos" (2026-08-16) and the scope ruling of 2026-08-17 kept the captain's
premade cards as reusable per-project assets. The mechanism landed as
`content.bookends` plus the `intro_card`/`outro_card`/`end_card` spine block
types (`library/tools/bookends.py`); the two title macros, the two stub
compositions, `content.intro_template`/`content.outro_template` and
`import_endcard.py` were deleted with it. `fusion_macro_loader.py` is the
only piece of the five left standing.

### Phase 1: framing. The biggest single change to the finished video.

- **P1.1 Per-clip creative framing.** ~~DONE.~~ `_conform_fields` accepts
  `framing_intent` (0.0-1.0) and `framing_pan_x` (-1.0-1.0). The renderer
  applies `ZoomX`/`ZoomY` and `PanX`. Brand templates can bias via
  `style.framing_intent`. Spine blocks can override per clip.
  Tests: `tests/test_framing_parameter.py` (21 tests).
- **P1.2 Subject-aware pan offset.** ~~Use the vision pass's subject bounding
  box to compute an intelligent `framing_pan_x` default rather than
  dead-centre. The Pan infrastructure exists; the policy to drive it from
  vision data is the remaining work.~~ **DONE**, but the premise was wrong
  twice over and both corrections are worth keeping.

  **The Pan infrastructure did not exist.** `_apply_conform` wrote `PanX`
  and `PanY`, which Resolve does not have - the Inspector Transform names
  are `Pan` and `Tilt`. `SetProperty("PanX", ...)` returns False and reads
  back None. Three renders of the same clip at pan +682.67, 0 and -682.67
  came out byte-identical. P1.1 delivered the zoom half only.

  **The vision pass does not measure where the subject is.**
  `vision_pipeline_v3` emits shot size (`camera[].framing`), identity
  (`objects[].role`) and time ranges (`assessment.primary_subject_visible`),
  none of which is a position. `object_segmentation` and `ocr_extraction`
  do produce real boxes and are not wired into the DAG. The one place the
  pipeline measures subject geometry is
  `step_1_04_temporal_index.compute_face_presence`, whose Haar cascade
  returned `(x, y, w, h)` per face and kept only `max(w*h)`. It now keeps
  the horizontal centre too, as `face_center_x`.

  The policy lives in `library/tools/subject_framing.py` and returns a
  POSITION, not a pan: `compile_manifest._conform_fields` already holds the
  source size, fit scale, zoom and target width, so the geometry stays at
  one site. Silence is a real answer - no detections, too few, a subject
  already near centre, or an OpenCV without Haar all yield None, which
  means the framing a project gets today. Tests:
  `tests/test_subject_framing.py`, `tests/test_face_presence_position.py`.

  **Q1 (2026-08-16):** the captain ruled letterbox stays the default and
  the tracking gets built regardless, so a clip a template or spine block
  pushes toward fill follows the subject instead of centring blindly.
  Whether the default should ever move is a separate question and is not
  settled here. **It is settled now - see P1.4, which moves it.**

- **P1.3 The framing mechanism was a no-op until 2026-08-19.** Both items
  above were unreachable on the one project that needed them. The render
  target came from the modal SOURCE resolution, so on project 001 -
  landscape footage - target equalled source, `_conform_fields` returned
  "no conform needed" for every clip, and neither letterbox nor fill could
  be seen. The delivery format now comes from the brand template
  (`library/tools/delivery_format.py`), and the first vertical render
  makes Q1 judgeable for the first time: all 13 clips letterbox, and
  gemma-4-12b reports `black_bars: top_and_bottom` on 6 of 8 sampled
  frames. That is the ruled default doing what it was ruled to do -
  whether it is the RIGHT default is the captain's open question, now
  with a render behind it. See `docs/RUN_001_END_TO_END.md` section 14.

- **P1.4 The default is fill, and the heuristic that made it letterbox is
  deleted.** The render P1.3 unlocked answered Q1. Measured on
  `exports/Pipeline_Edit.mp4`: the A-roll picture occupied rows 656..1263,
  608 of 1920 rows (31.7% of the frame height), with **61.6% of all pixels
  below luma 12** averaged over 55 samples - and because the three B-roll
  cutaways are portrait-shot and DID fill, the video flipped between
  full-bleed and a thin strip three times.

  The named cause was not "letterbox is the default". It was that the
  default was a HEURISTIC and the heuristic was inverted: with no
  declaration, `_conform_fields` ran a legacy branch whose rule was *if the
  primary subject is visible during this clip's source range, a centre crop
  might cut them off, so prefer letterbox*. On a selfie monologue the
  subject is visible in every clip, so every clip letterboxed, always - and
  the whole `framing_intent` + `subject_center_x` mechanism, which lives in
  the OTHER branch, never executed on any project. No shipped template
  reached it either.

  What changed:
  * The legacy branch is gone. `_conform_fields` has one branch.
  * One enumeration, `library/tools/framing_intent.py`, owns where a
    clip's number comes from: spine block > project.yaml
    `pipeline.framing_intent` > template `style.framing_intent` >
    `DEFAULT_FRAMING_INTENT` (= 1.0, fill). The project level is new; it
    is the same project-over-template precedence `delivery_format_name`
    uses, and it is what keeps "this project wants bars" expressible
    without forking a template.
  * A malformed declaration raises instead of degrading to "no framing".
  * `subject_framing.subject_centers_by_clip` reads the shape step 1.04
    actually emits (`full_indices`, a LIST). It read only a mapping
    before, so it returned `{}` on every real run: the pan was never
    computed, and P1.2 was dead in delivery as well as unreachable. Its
    tests all fed it invented mappings, so it passed.

  Proven on 001's banked state without re-rendering: all 8 A-roll clips go
  `needs_conform: false` -> `true` at zoom 3.1605, picture rows 656..1263
  -> 0..1919 (31.7% -> 100% of frame height), 5 of 8 carrying a
  subject-derived pan; the 3 without are clips whose face track is either
  inside the centre deadband or too sparse to reduce, which is
  `subject_framing` declining to answer rather than failing. Frames cut
  from the source at the manifest's exact crop rect confirm the speaker is
  framed on all 8. B-roll is unaffected: it is portrait, so its aspect
  already matches and `_conform_fields` returns early.

### Phase 2: audio. (OUT OF SCOPE)

The audio thread was ruled OUT OF SCOPE on 2026-08-15. The half-built ducking machinery (P2.2) and J/L cut offset code was removed under that ruling.
The remaining items (P2.1 Real music level, P2.3 Music fades, P2.4 Master limiter) are not happening as part of the automated pipeline.

### Phase 3: the look. Where "publishable" is actually decided.

- **P3.1 Motion graphics under template control.** ~~The corner brackets and
  progress bar must be opt-in per template, and `accentColor` must come from
  the template palette.~~ **MECHANISM DONE; the default is Q3.** The colour
  now comes from `style.color_palette` via `library/tools/brand_palette.py`,
  the same rule the captions use - two copies of "which entry is the brand
  accent" would let one video carry two different brand colours. The
  brackets and the progress bar are `effect.motion_accents` and
  `effect.motion_progress_bar`, and step 4.06 declares the brand slots so
  the runner injects them.

  **Q3 answered 2026-08-16, by reframing rather than by picking an option:**
  "they are templates, so they dont need to be in every video, they are
  just part of a growing library now". There is therefore NO universal
  default. A template that declares nothing gets nothing; declaring is what
  turns an element on. That is the opposite of the old behaviour, and
  deliberately so - the old behaviour is what put the same cyan corners on
  every video this pipeline has ever made, and preserving it is precisely
  what was rejected.

  A template that enables accents must supply a colour to draw them in,
  from its own palette or from `creative_direction`; there is no constant
  fallback and `#00D4FF` can no longer reach a frame. Asking for accents
  with no usable accent colour raises `MissingAccentColor` rather than
  silently drawing the withdrawn cyan. An unrecognised flag value keeps
  the default rather than reading as false, so a typo cannot quietly
  change the look. Tests: `tests/test_motion_graphics_template.py`.
- **P3.2 Subtitle style from the brand template.** ~~Wire
  `style.typography` and `effect.subtitle_style` through 4.01 into the
  Remotion props, and delete the hardcoded default at
  `generate_remotion_props.py:145-153`.~~ **DONE.** The named looks live in
  one enumeration, `library/tools/subtitle_style.py`, on the same contract
  as `transition_vocabulary` and `series_look`: an unknown name raises, and
  a style no template names fails CI. A named style owns the SHAPE (size,
  weight, outline, position); the template supplies the brand, via
  `style.typography` and `style.color_palette`. Step 4.01 resolves it and
  emits `subtitle_plan.style`; `generate_remotion_props` now RAISES when
  that key is absent rather than substituting a house look, because the
  substitute was the bug. `fontWeight` gained a reader in
  `SubtitleOverlay/index.tsx`, where it had been hardcoded to 800 while
  templates declared a weight. Tests: `tests/test_subtitle_style.py`.

  Two things found by rendering it. An emphasised word was sized with
  `transform: scale()`, which reserves no layout width, so it overflowed
  and collided with its neighbours - "the brand template" rendered as
  "thebrandtemplate", worse the larger the caption. Emphasis is now sized
  with `fontSize`. And `cinematic_narrative`'s palette contains no usable
  accent: its most saturated entry is a dark muted navy that would sit on
  top of its own near-black outline, so the derivation rejects it and
  keeps the style's accent. A dark accent is only kept when it is vivid,
  which is what tells `#223344` from `#ff0055`.
- **P3.3 Self-host the font.** ~~Bundle the .ttf and load it with
  `@remotion/google-fonts` or `staticFile` plus `delayRender`, so
  typography is deterministic.~~ **DONE.** Montserrat ships as a single
  variable file, `remotion-subtitles/public/fonts/Montserrat-Variable.ttf`,
  covering the whole 100-900 axis - every weight `subtitle_style.py` can
  ask for, asserted by reading the font's own `fvar` table. `src/fonts.ts`
  registers it with `staticFile` plus `delayRender`/`continueRender` and
  declares the variable axis, without which Chromium synthesises bold
  instead of using the real 900. A font that fails to load now raises
  rather than falling back to Chromium's default sans, which was invisible
  in the output. Licence: SIL OFL 1.1, shipped beside the font and recorded
  in AGENTS.md section 11. The `Inter` import went too - nothing in `src/`
  ever asked for Inter. Tests: `tests/test_bundled_fonts.py`.

  Verified two ways: removing the bundled file makes the render fail with
  `Failed to load bundled font Montserrat`, proving the render really
  depends on it rather than on a system or network fallback; and the
  rendered still is **byte-identical** (md5 `fb3c4f9c…`) to the same frame
  rendered when the webfont happened to win the race - so the typography
  is now guaranteed rather than lucky, and nothing about the picture
  changed.
- **P3.4 The block-type look library, or delete it.** ~~Either fix the label
  casing so `SEGMENT_PRESETS` can be selected~~, or remove it. **DELETED**,
  by the captain's ruling of 2026-08-16.

  **The casing theory in the struck text is wrong, and this is recorded so
  nobody re-derives it.** Correcting the label casing would NOT have
  enabled those seven looks. They were unreachable twice over, established
  independently:

  1. The reader was `if not effects and label in SEGMENT_PRESETS`
     (`apply_fusion_comps.py:170`). It fired only for a clip carrying NO
     other effects - and since the house look landed in #106,
     `compile_manifest` merges the Fusion half of the grade into
     `per_clip` for EVERY V1 and V2 clip. `effects` is therefore non-empty
     on every clip and the branch could not run, whatever the labels said.
  2. Five of the seven were keyed to block types the spine cannot emit.
     `spine_contract` documents `block_type` as `"hook" | "speech" |
     anything else`, so `CORE_INSIGHT`, `TURNING_POINT`, `EMOTIONAL_PEAK`,
     `RESOLUTION` and `B_ROLL_CINEMATIC` had no block that could ever
     select them.

  Seven curated looks that cannot be selected are exactly the
  reads-as-coverage problem Phase 0 existed to remove. The reason is
  recorded in `library/tools/fusion/presets.py`, where they used to live.

  Per-block-type looks are not off the table, but they are **their own
  design job** if the captain asks for them: new spine block types, plus a
  decision about how a block look composes with a house look that every
  clip already carries. Explicitly not a cleanup item, and not to be
  reopened as one.
- ~~**P3.5 More than one PowerGrade.**~~ **DONE**, by dropping the PowerGrade
  rather than authoring more: see Q6. All four templates name a look and the
  values live in this repo.

**A failed QA station is now loud, and deliberately not fatal.**
Error-severity QA check failures used to be appended to
`results["warnings"]`, alongside "Fairlight preset not found" and friends,
while the build printed "Build succeeded" with an empty error list. They
now go to `results["qa_failures"]`, are announced in their own banner under
the verdict, and are forwarded by `step_6_01_render/step.py` into
`pipeline_data.json` - that payload hand-picks its keys, so a value not
named there never reaches the ledger.

`success` is unchanged on purpose. The captain's ruling of 2026-08-16 is
two steps: make it visible now, make it fatal only with evidence on how
often a station fires on real footage, because making it fatal on no
evidence is the mirror image of the defect it fixes.

**The evidence does not exist yet, and that is the finding.** There is no
complete pipeline run on disk to measure against. Of the four projects
under `PIPELINE_PROJECTS_ROOT`, three have no `pipeline_data.json` at all
and `test-proof` has two completed steps of twenty-six - `scan` and
`catalog` - so it never reached `compile_manifest`, let alone a render.
The one real manifest that survives is the `tests/fixtures/captured_run/`
capture, which carries 14 V1 clips and 13 VFX but zero
`fusion_effects.per_clip` and zero fusion transitions, so
`verify_fusion_comps` returns early on it and would not have fired.

So the measured failure rate today is: **one real manifest available, zero
stations fired, no rendered runs at all**. That is not a number worth
making a fatality decision on. `qa_failures` is now the channel that
accrues it, from the next real render onward.

**`default_brand` was a fallback that rendered, not a look anyone should
ship.** **FIXED 2026-08-16** by giving it a real neutral palette -
`#F5F5F5` / `#9AA0A6` / `#141414`, every entry below the accent saturation
floor so the fallback cannot invent a brand colour. The finding is kept
rather than deleted, because how it was found matters more than the fix.
Rejected, deliberately: refusing to run without a named brand template. A
fallback whose job is to work should work, and that change could block a
render at the worst moment.

Found while assigning motion accents per template (Q3), and worth
stating on its own because it was invisible until it bit.

`default_brand.yaml` is what a project gets when `project.yaml` names no
brand template, and `compile_manifest` falls back to it by name. Its
`style.color_palette` is `["#ff0000", "#00ff00", "#0000ff"]` - three pure
RGB primaries, visibly a placeholder rather than a palette. Everything
that now derives colour from the palette will honour it: the motion
accents would have drawn **red corner brackets**, and the caption accent
would have gone red too if `default_subtitles` were palette-driven.

It is only PARTLY defused, and I checked by rendering rather than by
reasoning - the first version of this entry claimed nothing paints
anything red, and that was wrong.

Defused: `default_subtitles` opts out of palette derivation and keeps the
legacy caption colours, and `default_brand` declares no motion accents, so
no red corner brackets are drawn.

**Not defused: the motion-graphics upper third renders in `#ff0000`
today.** `MotionGraphics/index.tsx` colours the upper-third subtitle with
`accentColor`, which is resolved from the palette whether or not the
accent ELEMENTS are enabled - reasonably, since the two shipped templates
that do declare accents want their subtitle in the brand colour, and
`#ff0055` and `#ddab7e` both look deliberate there. On `default_brand` the
same path yields pure red. Rendered proof: `115,030` bytes, md5
`1eb6f065`, subtitle line in `#ff0000`.

So a project that falls back to this template gets a red subtitle line
now, and would get red corner brackets the moment anyone enabled them.

Not in scope for Phase 3. The fix is either a real palette for
`default_brand` or a refusal to run without an explicitly named template,
and that is a decision, not a cleanup. Related: **Q9**, which asks whether
four templates should ship at all.

### Phase 4: rhythm.

- **P4.1 Use the real beat grid.** ~~`plan_transitions` should read
  `music_analysis.beat_grid` instead of synthesising one.~~ **DONE**, and
  the item understated it: there is no `beat_grid` key to read. The
  producer, `music_pipeline.analyze_music`, returns
  `{"tempo": {"bpm", "beats", "downbeats"}, "key", "structure", ...}` and
  step 2.06 passes it through unchanged. So `plan_sfx`'s
  `music_analysis["beat_grid"]["bars"]` - credited in section 1 as the one
  honest consumer of the real grid - was always `[]`, and **the SFX beat
  snapping never ran either**. Both consumers now go through
  `library/tools/beat_grid.py`, which is the single place that knows the
  producer's shape, and `tests/test_beat_grid.py` asserts the two ends
  agree by reading the producer's own AST rather than a fixture.

  Also fixed: step 2.06's completion log read `analysis['bpm']`, which is
  not where BPM lives, so it printed `BPM=?` on every run.

  The grid is expressed in the MUSIC file's clock and used as timeline
  time. That holds only while music is placed at `source_in` 0 /
  `timeline_in` 0, so `compile_manifest` now asserts it - an offset would
  move every snapped cut and SFX hit silently.

- ~~**P4.2 Close the pacing loop.**~~ **NOT CLOSED - the machinery is
  REMOVED**, under the captain's ruling that "or stop pretending" is a real
  option. Three independent reasons, any one sufficient:

  1. The check read `creative_direction["pacing"]["cuts_per_minute"]`, a
     singular key **no producer has ever emitted**, so the target was
     always `None` and the check never ran. The two tests that covered it
     supplied the key themselves - a fixture proving a fixture.
  2. The brand templates' pacing blocks, where a target would have come
     from, had **no reader anywhere in the repository**, and spelled
     themselves two ways: `cuts_per_minute_min`/`_max` in three templates,
     `min_cuts_per_minute`/`max_cuts_per_minute` in the fourth. They do not
     reach an LLM prompt either - only `plan_subtitles` and
     `render_motion_graphics` receive `brand_style`, and no handoff
     mentions pacing.
  3. Even had it run, it emitted no adjustment, and 5.03 runs **after** the
     spine, the speech sequence and the transitions have fixed the cut.
     Changing pacing means re-cutting, which is a re-plan;
     `apply_cohesion_adjustments` refuses those by design and the DAG has
     no edge back to the planning steps.

  Removed: the check, `extract_cuts_per_minute`, `StyleSlots.pacing`, and
  the pacing block in all four templates. `tests/test_beat_grid.py` fails
  if any of it returns. Pacing control remains possible, but it is a
  re-cut loop and therefore a design job, not a config key.
- **P4.2 Close the pacing loop.** `creative_cohesion` scores cuts per minute
  against the template and nothing acts. Either feed the score back into a
  re-cut, or stop pretending pacing is controlled. **Medium.**

- **P4.3 CLOSED by rescope (#238).** `creative_cohesion`'s only finding on
  001's 2026-08-26 run was `speech_sequence.segment_order`, which
  `compile_manifest` correctly refused; `applied_adjustments` was empty on
  every run. The review cannot move upstream of what it reviews - it reads
  the 4.02-4.04 plans - so it is scoped where it runs instead:
  `library/tools/cohesion_scope.py` splits findings into the ones the
  manifest compiler applies (`adjustments`) and the ones a step upstream
  owns (`observations`, naming the owner and the re-run that would act).
  Two further dead readings went with it: the colour check read
  `color_grade_spec["mood"]`, a key step 5.01 does not emit, and
  `cohesion_score` was 100 minus hand-picked weights that nothing outside
  the step read. Both removed. See
  `docs/RULE_EVIDENCE.md#the-review-recommended-what-it-could-not-do`.

### Phase 4.5: the perceptual quality gate. Ahead of Phase 5, by ruling.

**Q8 answered (2026-08-16), and not with the answer that was asked for.**
The question was "what is publishable measured against", expecting a
reference video. The captain's answer instead: feed the render to the local
Gemma 4 12B model and ask it for a quality analysis. So the gate becomes
**perceptual rather than technical**, and it runs on every render rather
than being calibrated once against a reference.

**What this settles and what it does not.** It genuinely closes the gap:
every gate in this pipeline is technical - resolution, fps, duration,
black frames, LUFS, audio streams - and not one would catch a letterboxed
edit with the subject's head cropped off. A model that looks at the frame
catches exactly that. What it does NOT settle is whose taste the bar is
calibrated to; it moves it from the agent's taste to the model's. That is
an improvement, because a model verdict is reproducible, inspectable and
free to run locally - but it needs the captain to look once and say
whether they agree, and until then none of it is anybody's standard.

**The plumbing already existed and was never called.**
`visual_qa_router` already routed to `library/tools/vision_model.VisionModel`
with `MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"`; `mlx_vlm` and the
cached model are both present on the machine. The reason every render
printed "Completed 0 frame grabs and 0 segment checks" was
`plan_qa_checks` reading `manifest["clips"]` / `manifest["video_clips"]` -
**neither of which has ever existed**. Clips live under `tracks.V1.clips`.
The same key-name mismatch as the beat grid, the caption style and the
conform pan, this time inside the QA layer itself, where it read as a
clean visual pass rather than an absent one.

**What the first real verdicts taught, which is why the dimensions are
narrow.** Asked openly - "report only what is wrong" - the model returned
`{"issues": []}` on a frame whose subject was visibly cut in half. Asked
grounded, closed questions it got all three calibration frames right:

| frame | verdict |
| --- | --- |
| letterboxed | `black_bars: top_and_bottom` - "large black bars at the top and bottom, failing to fill the screen" |
| fill, dead-centre crop | `main_subject_fully_visible: false` - "cut off by the left edge of the frame" |
| fill, subject-tracked | clean |

So the dimension set was derived from what the model actually notices, not
written in advance. `library/tools/perceptual_qa.py` holds them.

**Four constraints, all deliberate.** Structured rather than prose, so
verdicts compare across renders. Ask what is WRONG and WHY, never for a
score - a number invites tuning toward the number, which this project has
been burned by. Every dimension must discriminate: `dimension_variance()`
counts distinct answers, and `fills_frame` was **dropped** for answering
`true` on all three frames including the letterboxed one, contradicting
the model's own `black_bars` answer. And **observation only** - it never
touches `success`, for the same reason the QA stations are not fatal:
there is no evidence yet about the false-positive rate.

**Known limits, recorded rather than discovered later.** The calibration
frames are synthetic test patterns built to make geometry legible, and the
model reads them as charts - on the clean frame it reported cropped ruler
numbers, which is true of the pattern and irrelevant to video quality.
Real footage is needed before any false-positive rate means anything.
`text_legible` has variance 1 over three frames, which is not yet evidence
it cannot discriminate; it stays, marked unverified, pending a real
sample. Cost is roughly 6s per frame on the local model, so the run is
bounded to 6 frames sampled evenly across the timeline, and the number
skipped is reported - a silent cap reads as full coverage.

**Ask it closed questions. This is the finding, not a detail.** Asked
openly - "report only what is WRONG and why, as JSON" - the model
returned `{"issues": []}` on a frame whose subject was visibly cut in
half at the frame edge. Given the grounded, closed questions in
`perceptual_qa.DIMENSIONS`, the same model on the same frames got all
three right, including naming which edge the subject was cut off on. What
it notices depends almost entirely on being asked something specific.
Do not re-derive this by trying the open prompt; it has been tried.

**Not done: the calibration step, and it is the next thing.** Renders
alongside model verdicts, in front of the captain, once. If the model and
the captain disagree on something concrete, that disagreement is the most
valuable thing this phase can produce - more valuable than the gate
working.

It is **blocked on real footage**, not on effort. The three calibration
frames are synthetic patterns built to make geometry legible, and the
model correctly reads them as charts - on the clean one it reported
cropped ruler numbers, which is true of the pattern and irrelevant to
video. No project on disk has footage or a completed run: three of four
have no `pipeline_data.json` at all, and `test-proof` has 2 completed
steps of 26. So a false-positive rate cannot be established yet, which is
also why the gate is observation-only.

What is known about the gate, for whoever picks this up:

- It reliably catches **letterboxing** and **a subject cut off by the
  frame edge**, and says which edge. Those are the two loudest defects in
  section 2 and neither is visible to any technical gate.
- It returns clean on a correctly framed frame, so it discriminates
  rather than complaining about everything.
- It costs roughly **6s per frame** plus a ~4s model load, all local and
  free. The run is bounded to 6 frames sampled evenly, and reports how
  many it skipped.
- `text_legible` has variance 1 over three frames. That is not yet
  evidence it cannot discriminate - it is an unverified dimension awaiting
  a real sample, and it should be dropped if a real set leaves it at 1.

### Phase 5: the ceiling.

- **P5.1 Transitions that mix two clips.** Cross dissolve, wipe and whip pan
  are impossible on the per-clip Fusion route, and the two alternative routes
  are closed by ruling. The only remaining path is pre-rendering a two-clip
  transition segment with ffmpeg and placing it as a clip.

  **SCOPED 2026-08-16, not built.** Measured rather than estimated, on
  1080x1920 at 30fps with a 0.5s dissolve.

  **The route works.** A real cross dissolve was placed on a Resolve
  timeline through the existing renderer and rendered: both clips visible
  mid-blend at frame 90. This is the thing the per-clip Fusion route
  cannot do, and it is not hard.

  **Cost is negligible, and that is the surprise.** Encoding only the
  transition WINDOW - not the whole pair of clips:

  | codec | encode | size | per 10 transitions |
  | --- | --- | --- | --- |
  | ProRes 422 HQ | 0.22s | 1.79 MB | 2.2s, 18 MB |
  | ProRes 422 Proxy | 0.21s | 0.76 MB | 2.1s, 8 MB |
  | H.264 crf18 | 0.17s | 0.05 MB | 1.7s, 0.5 MB |

  ProRes 422 HQ is the right choice: it is an intermediate that a graded
  timeline will re-encode, and 18 MB is nothing. Encoding the whole pair
  instead of the window costs 7x the time and 10x the size for no gain.

  **The design that works is duration-PRESERVING.** The segment spans the
  cut, taking half its length from each neighbour, so A is trimmed by
  0.25s, B starts 0.25s later, and total duration is unchanged. A naive
  overlap dissolve shortens the timeline by the transition duration -
  which would shift every subsequent subtitle, SFX hit, motion-graphics
  segment and the music clamp, because all of those are placed at
  absolute timeline times. That is the difference between a contained
  change and a re-timing of the whole edit.

  **The open risk is audio, and it is the real work.** The segment is
  video-only, so the renderer placed A1 from the source clips either side
  and left a **16-frame hole** across the transition:

      A1: clip_A.mp4  0 -> 82        (audio present)
          [82 -> 98 SILENT]          (the transition window)
          clip_B.mp4  98 -> 180      (audio present)

  Half a second of silence at every dissolve, in the speech track. Fixing
  it means either carrying crossfaded audio in the segment - which is a
  mix decision, and audio is out of scope by ruling - or placing A1 from
  the untrimmed sources across the join while V1 takes the segment. The
  second is probably right and is not free: it breaks the clip linking
  the renderer relies on.

  Also seen: V1 summed to 179 frames against an expected 180, so the
  frame accounting needs proving before this ships.

  **Where it has to run.** Before `compile_manifest`, because it must trim
  both neighbours; and after `plan_transitions`, because it needs the cut
  points. It also needs the real source files, so it cannot be a planner.

  **Sequencing (captain, 2026-08-16):** the perceptual quality gate comes
  first. This is a large change whose value is a soft transition, and
  until something can see the render we cannot tell whether it is worth
  the audio work.

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

**Q4. What should `cut_out` mean?** **ANSWERED (2026-08-17):** option (a) -
delete `cut_out` from the toolkit. Framing already expresses a pull-back
(PR 109 merged the framing parameter), so a separate sub-1.0 zoom effect is
redundant. Removed from INTENSITY_MAP, handoff, and tests.

**Q5. Should captions be lowercase?** **ANSWERED (2026-08-16):** make it a
per-template setting and set all four templates to their CURRENT behaviour,
so a hidden global becomes a declared choice without anyone making a look
decision on the captain's behalf.

**Already implemented**, in #102, before the question was put:
`effect.caption_case` is in the schema with an enum, all four templates
declare `lowercase` explicitly, `step_4_01` honours it, an unknown value
falls back to lowercase, and `tests/test_caption_case.py` covers it. The
only thing added under this ruling is a guard that all four keep declaring
it, so the decision stays durable rather than drifting back to the default.

The struck text below was already stale when written: `step_4_01` has not
lowercased unconditionally since #102.

**Q6. What is the actual house look?** **ANSWERED (2026-08-15).** The captain
ruled: drop the PowerGrade node and deliver the look entirely through CDL plus
Fusion, which the pipeline can do unaided. It needs no hand-grading, it removes
an unlicensed third-party asset, and it fixes the templates that named no grade
- in one move. Four looks then shipped in `library/tools/series_look.py`,
authored from the planning docs at `PLAN/series portfolio '26 planning/`.
Rejected: hand-authoring `.drx` files in Resolve; keeping the gifted grade and
merely flagging the licence.
**Superseded 2026-08-28:** the captain ruled *"there are no house glow looks,
there are no settled house grain or anything"*. The four looks are removed -
their DIRECTIONS came from the planning docs but their STRENGTHS did not, and
could not: a document states a direction, not a magnitude. A look is now
declared by a brand template, in values. See AGENTS.md section 12 and
`docs/RULE_EVIDENCE.md` "there-is-no-house-look".

~~**Q7. Do intros, outros and end cards belong in this pipeline?**~~
**ANSWERED** 2026-08-16: "wire them up, but only on some videos", and the
scope ruling of 2026-08-17 settled the residue - the engine serves client
work as well as the 8 series, so the premade cards are kept as per-project
assets rather than retired. Delivered: a brand template declares
`content.bookends`, `mesh_spine` turns each declaration into an
`intro_card`/`outro_card`/`end_card` block, and `compile_manifest` puts it
on V1. A template that declares nothing gets nothing, which is every
template but `lucie_client.yaml`.

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

## 4.5 The test-suite hang of 2026-08-16 — RESOLVED, recorded in full

Kept because it cost about an hour across three people-hours of wrong
theories, and because a future session meeting a slow suite should know
what was already eliminated.

**Status: fixed, in the same commit as the perceptual gate.** Verified by
three consecutive clean runs — `988 passed, 7 skipped` in 5.08s, 5.01s and
5.09s, with CPU time (3.1s) close to wall time, which is the signature of
a suite doing work rather than sleeping.

### What it looked like

`python3 -m pytest tests/ -q` started, did a few seconds of work, and
stalled indefinitely. Three occurrences. The clearest measurement: **10
minutes 17 seconds of wall clock for 2.75 seconds of CPU**, at 0.0%, with
four sleeping threads, **no network connections at all**, stdin on
`/dev/null`, and three pipe file descriptors pointing at one endpoint.
Verbose output stopped at
`tests/test_resolve_build_timeline.py::test_media_import_logic`.

### The cause

A `faulthandler.dump_traceback_later` stack dump, rather than any theory:

```
build_timeline
  -> execute_frame_grab
    -> render_single_frame
      -> segment_renderer.render_segment:102
```

`render_segment` polls `project.IsRenderingInProgress()` and sleeps 0.5s
between polls. Under `tests/test_resolve_build_timeline.py` the project is
a `MagicMock`, which answers **truthy forever**, so the loop slept out its
entire timeout — **once per frame grab**. Not a deadlock: a long poll.
That is why CPU was near zero and why threads and pipes were present.

**Why it appeared only that day.** It was dormant. `plan_qa_checks` read
`manifest["clips"]` / `manifest["video_clips"]`, neither of which has ever
existed, so it returned zero frame grabs and this path never executed.
Fixing that key-name bug switched it on.

### The fixes

1. The mock now answers `IsRenderingInProgress` honestly.
2. A unit test no longer spawns real subprocesses at the live application.
3. **Both the frame-grab loop and the perceptual pass are opt-in**
   (`PIPELINE_PERCEPTUAL_QA`). Each frame grab is a real Deliver-page
   render, so the key-name fix would otherwise have added minutes to every
   production export unasked — trading a silent no-op for a silent cost.
4. Unrelated but found on the way: the Fusion comp subprocess in
   `resolve_build_timeline` had **no timeout** — the only Resolve-touching
   subprocess in the codebase without one, against seventeen that have
   one. A real render could hang forever, indistinguishable from a slow
   Fusion pass. It is bounded now and a timeout is a failure, not silence.

### Theories that were WRONG — do not re-walk these

- **"`vision_model` loads the 12B model at import time."** False. It is a
  lazy singleton; `load()` is called only inside `_ensure_loaded()` at use
  time, and the module-level `from mlx_vlm import ...` is inside a
  try/except that tolerates absence. That module is correct.
- **"A test opens a Resolve connection or binds a port and waits."** No
  network connections existed on the hung process.
- **"A heredoc left the process waiting on stdin."** Its stdin was
  `/dev/null`. (A *separate* hang earlier that day genuinely was a
  malformed heredoc piping a script into `python3 -` inside a compound
  command; write edit scripts to a file and run them by path instead.)
- **"It hangs during collection."** Not reproduced. `--collect-only`
  completes in 1.5–1.8s, measured repeatedly before and after the fix, and
  it executes no test bodies so it never reaches the polling loop. One
  process observed in that state was most likely a stale orphan; if
  collection ever genuinely hangs, that is a **second** problem and this
  entry does not cover it.

### The debt it left

Every passing count quoted in the Phase 3, Phase 4 and Q5 gates came from
this suite while it was capable of hanging. Those runs did complete and
did report their numbers, but the suite was not trustworthy at the time.
The baseline was re-established from scratch afterwards — **988 passed, 7
skipped** — and that is the number to carry forward, not any earlier one.

**The general lesson**, which is the same one this document keeps
recording: a suite that can hang belongs to the same family as a check
that cannot fail. A green run and a hung run are indistinguishable to
anyone who does not wait, and CI eventually just times out and gets
re-run.

### The bigger lesson, which is not about the hang

**Repairing a reader that never ran is not a bug fix. It is a feature
being switched on for the first time, and it must be treated as one.**

The one-line change from `manifest["clips"]` to `tracks.V1.clips` looked
like a typo correction. It was not: it activated a loop that performs a
real Deliver-page render **per placed clip**, which had never executed in
the pipeline's life. Shipped as a bug fix, it would have added minutes to
every production export — unasked, unmentioned, and not noticed until
somebody wondered why renders had got slow.

Before fixing a key-name mismatch, establish what the repaired reader
will now DO, and how often, and what it costs. If the answer is anything
but free, the switch-on is a separate decision from the correction — here
it became `PIPELINE_PERCEPTUAL_QA`, off by default, reporting how many
checks it skipped. Several other findings in this document are dormant
readers of exactly this kind; treat each one the same way.

## 5. What should be deleted

A pipeline that advertises less and delivers all of it is better than one
that claims more. In rough order of how much noise removal saves:

| Delete | Why |
| --- | --- |
| ~~`library/tools/execution/sfx_placer.py` (1445 lines)~~ | **DELETED** in #103. SFX resolution lives in `compile_manifest/step.py` and placement in `resolve_build_timeline.py`. |
| ~~`library/tools/execution/build_powergrade.py`~~ | **DELETED** with the PowerGrade route. |
| ~~`library/tools/execution/import_endcard.py`~~ | **DELETED**, and this one was superseded rather than merely unused. It appended a rendered end card to V1 out of band, from a hand-run CLI, AFTER the manifest was written - so `manifest_validator`, the coverage assertion and the render-QA duration checks all described a timeline that no longer existed. The same card now enters through `content.bookends` -> a spine block -> a V1 clip in the manifest, which is the route every other clip takes; `library/tools/bookend_render.py` carries the note. Nothing was lost: its default input path (`library/tools/execution/output/lucie_endcard.mov`) never existed on disk, and git history holds the file. |
| ~~`library/tools/agent_poll.py`~~ | **DELETED** in #103. |
| ~~`library/tools/preset_indexer.py`~~ | **DELETED**, with the descriptors for the asset families that went with it. The two fusion-macro descriptors were kept: `tests/test_title_macros.py` reads them to hold the descriptors, the assets and `default_brand.yaml` to the same filenames. |
| ~~`library/presets/luts/`, `library/presets/dctls/`~~ | **DELETED.** No reader, and `film_emulation.meta.json` had no asset at all - it pointed inside a Resolve install and the indexer exempted it from its own existence check. |
| ~~`tests/test_title_macros.py`~~ and the two `.setting` macros | **DELETED** under Q7, with both `.meta.json` descriptors. The test asserted the descriptor, the filename and non-empty file contents - never a pixel - while the assets themselves were a full-frame pure red slate and a "Subscribe!" card the channel spec forbids by name. `library/tools/fusion_macro_loader.py` is still here and now has no asset to load; it is a generic reader rather than a route, so retiring it is its own decision. |
| ~~`SEGMENT_RECIPES` and `CompEngine.from_preset`~~ | **DELETED** in #103. `SEGMENT_PRESETS`, which they duplicated, is still there and still unselectable - that is P3.4, not this table. |
| ~~`resolve_build_timeline.py:330` (`vfx_entries`)~~ | **DELETED** in #103. |
| ~~`apply_fusion_comps.py:328-336` (`zoom_pulse`)~~ | **DELETED** in #103. |
| ~~`resolve_build_timeline.py:590-649` (J/L cut offsets)~~ | **DELETED** in #103, under the ruling that put audio out of scope. Q2 is moot with it. |
| ~~`content.intro_template`, `content.outro_template` in `default_brand.yaml`~~ | **DELETED** under Q7, from the template and from `ContentSlots`. `content.bookends` replaced them: it names a card, a duration and its props, and `compile_manifest` reads it. |
| ~~`LucieEndCard`, `LucieLogoAnimation`, `FourthWallOverlay`~~ | **RESOLVED**, each differently. The two `Lucie*` entries in this repo were transparent stubs that rendered a byte-identical empty frame - deleted, with their `Root.tsx` registrations. The real implementations are the client's, live with the client's project, and are declared by `lucie_client.yaml` through `content.bookends`; `bookend_render.py` stages and renders them **verbatim**, so the engine never edits a client's asset. `FourthWallOverlay` took two more moves: generalised into `TimedTextOverlay` (#119), whose template slot then turned out to have no reader, and the series-specific registration plus the trial-run copy it carried deleted on 2026-08-20 - see section 5 and `docs/ASSET_LIBRARY_PLAN.md`. |
| ~~`smart_reframe` in the manifest~~ | **DELETED**, having been unverifiable rather than unverified. Its wrapper guarded on `hasattr(clip, 'SmartReframe')`, which is True for every name on a Resolve proxy including invented ones; its only caller handed it a Timeline, which exposes no such method, discarded the return value and printed "✓ Applied Smart Reframe" whatever happened; and its test asserted that a mock returning True made the wrapper return True. Framing is delivered per clip by `_apply_conform`, which a working Smart Reframe would have fought. Reason recorded at the top of `library/tools/neural_engine.py`, beside the Magic Mask withdrawal. |

Not on this list, deliberately: `apply_native_transitions.py`, which AGENTS.md
keeps on purpose as a record of a closed route, and every regression fixture
in `tests/fixtures/captured_run/`.

---

## Handover, 2026-08-16

Written at a halt, after Phases 0-4. Everything here that is not obvious
from the code, because context does not survive a session.

### Merged

| PR | What |
| --- | --- |
| #110 | Phase 0 — Smart Reframe withdrawn, two vacuous QA gates made real, plan reconciled |
| #111 | The conform pan never moved the picture (`PanX` → `Pan`); the QA layer never loaded |
| #112 | P1.2 — the crop follows the subject |
| #113 | Phase 3 — subtitle styling, bundled font, template-declared motion accents, P3.4 delete |
| #114 | Phase 4 — the real beat grid; pacing removed rather than pretended |
| #115 | Q5 pinned; P5.1 scoped with measurements |

**Open: #116** — perceptual QA (Q8) as observation-only, plus the suite-hang
fix and the unbounded Fusion subprocess. CI green. It is coherent and
honest about its limits; the calibration step is explicitly NOT done.

### The pattern behind almost every finding

Six separate times, a producer and a consumer disagreed about a key name,
the reader `.get()`ed a default, and the run reported success over empty
data: `framing_pan_x` written to a property Resolve does not have;
`subtitle_plan.style` never written while Remotion consumed it;
`music_analysis.beat_grid` never existing while two steps read it;
`creative_direction.pacing.cuts_per_minute` never emitted while a check
scored it; `manifest["clips"]` never existing while the QA router planned
from it; and the whole QA import block nulled by one absent module.

**The two techniques that caught them, both reusable.** Assert
producer/consumer agreement by parsing the **producer's own AST** — a
fixture agreeing with a reader only proves the fixture. And **render it**:
byte sizes and md5s settle arguments that reasoning does not. The caption
collision, the beheaded subject, the red fallback subtitle and the
letterboxed strip were all found by looking at pixels, not code.

### Still open, needing the captain

- ~~**Q4** — what `cut_out` should mean.~~ **CLOSED** 2026-08-17: deleted,
  superseded by the framing parameter.
- ~~**Q7 residue**~~ **CLOSED** 2026-08-17. The engine serves client work
  as well as the 8 series; the 4th Wall copy ships corrected (#119); and
  `import_endcard.py` is superseded by the bookend wiring rather than
  dropped as dead code - the capability it stood for now runs through the
  manifest, where the gates can see it.
- **Perceptual QA calibration** — renders alongside model verdicts, in
  front of the captain, once. Until then none of it is anybody's standard.
  Blocked on real footage: the calibration frames are synthetic patterns
  the model reads as charts, and **no project on disk has any footage or a
  completed run** (three of four have no `pipeline_data.json`; `test-proof`
  has 2 completed steps of 26).
- **QA-station fatality** — step two of that ruling needs a failure rate
  from real runs. `qa_failures` now accrues it; there is still no data.

### Things known that are not obvious anywhere else

- **Audio is out of scope by ruling**, but two audio facts were measured
  and matter: `SetProperty("Volume", …)` returns **False** on an audio
  `TimelineItem` on Resolve 21 (its property dict is empty), so per-clip
  SFX levels do not reach the mix; and a pre-rendered two-clip transition
  leaves a **16-frame hole in the speech track** unless A1 is placed from
  the untrimmed sources.
- **`_apply_conform` writes `Pan`/`Tilt`.** There is no `PanX`/`PanY`.
  Read `TimelineItem.GetProperty()` with no argument to see the truth.
- **`hasattr` is always True on Resolve proxies.** Judge by return value.
- **The worktree `.venv` has no pytest and no cv2**; the suite runs on
  system `python3` (3.14). OpenCV 5 removed Haar cascades, so
  `opencv-python` is pinned `<5`.
- **Resolve was running throughout**, with many leftover proof timelines
  (`FramingProof_*`, `P51_Scope`, `Gate_*`). Harmless, but they accumulate.

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
