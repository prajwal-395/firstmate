# compile_manifest: declare what it consumes - mapping and STOP report

Lane: `fm/vep-declare-what-compile-manifest-consumes`. Declaration only.
No change to `library/steps/step_5_04_compile_manifest/step.py`,
no registration in `library/tools/operations.py`, no change to
`library/tools/requirements.py`.

## 1. Load census (derived independently, step.py `compile_manifest`)

Sixteen distinct modern `load(out_dir, "<name>.json")` calls, each with a
legacy `step_X_YY.json` fallback. Matches the brief's list exactly - an
earlier reading of eleven is not reproduced here; every load below was
taken from the function body at origin/main.

| # | file loaded | legacy fallback | producing node | key(s) the function reads |
|---|---|---|---|---|
| 1 | mesh_spine.json | step_2_05.json | mesh_spine | `audio_spine` |
| 2 | assign_aroll.json | step_3_01.json | assign_aroll | `a_roll_assignments`, `hook_assignment` |
| 3 | select_broll.json | step_3_02.json | select_broll | `b_roll_assignments`, `b_roll_interjections` |
| 4 | speech_sequence.json | step_2_02.json | speech_sequence | NONE - value never used (0 references past the load) |
| 5 | plan_transitions.json | step_4_02.json | plan_transitions | `transition_spec` |
| 6 | plan_sfx.json | step_4_04.json | plan_sfx | `sfx_spec` (+`fairlight_preset`) |
| 7 | music_selection.json | step_2_04.json | music_selection | `music_selection` |
| 8 | plan_subtitles.json | step_4_01.json | plan_subtitles | `subtitle_plan` |
| 9 | plan_vfx.json | step_4_03.json | plan_vfx | `enhancement_spec` |
| 10 | color_grade.json | step_5_01.json | color_grade | `color_grade_spec` |
| 11 | audio_mix.json | step_5_02.json | audio_mix | `audio_mix_spec` |
| 12 | semantic_analysis.json | step_1_03.json | semantic_analysis | `semantic_analysis_documents` (+`semantic_analysis`) |
| 13 | render_subtitles.json | step_4_05.json | render_subtitles | `subtitle_overlay` |
| 14 | render_motion_graphics.json | step_4_06.json | render_motion_graphics | `motion_graphics_overlay`, `timed_text_overlay` |
| 15 | creative_cohesion.json | step_5_03.json | creative_cohesion | `cohesion_review` |
| 16 | catalog.json | step_1_02.json | catalog | `clip_catalog`, `project_fps` |

## 2. Mapping to declared state keys (consumer = compile_manifest)

Derived ground truth: `requirements.derive_state_keys()` filtered to
`consumers == compile_manifest`. Nine exist - exactly the manifest's nine
`required: true` inputs, because `run_scope.prerequisites` derives one
condition per REQUIRED input only.

| file | declared key for compile_manifest | verdict |
|---|---|---|
| mesh_spine.json | `state.compile_manifest.audio_spine` from mesh_spine | DECLARED |
| assign_aroll.json | `state.compile_manifest.a_roll_assignments` from assign_aroll | DECLARED (`hook_assignment` rides along undeclared) |
| select_broll.json | `state.compile_manifest.b_roll_assignments` from select_broll | DECLARED (`b_roll_interjections` is optional, no key) |
| speech_sequence.json | NONE - no edge, no manifest input | UNDECLARED, see 3a |
| plan_transitions.json | NONE - `transition_spec` input is OPTIONAL | UNDECLARED, see 3b |
| plan_sfx.json | NONE - `sfx_spec` input is OPTIONAL | UNDECLARED, see 3b |
| music_selection.json | `state.compile_manifest.music_selection` from music_selection | DECLARED |
| plan_subtitles.json | `state.compile_manifest.subtitle_plan` from plan_subtitles | DECLARED |
| plan_vfx.json | NONE - `enhancement_spec` input is OPTIONAL | UNDECLARED, see 3b |
| color_grade.json | NONE - `color_grade_spec` input is OPTIONAL | UNDECLARED, see 3c |
| audio_mix.json | `state.compile_manifest.audio_mix_spec` from audio_mix | DECLARED |
| semantic_analysis.json | `state.compile_manifest.semantic_analysis_documents` from semantic_analysis | DECLARED |
| render_subtitles.json | `state.compile_manifest.subtitle_overlay` from render_subtitles | DECLARED |
| render_motion_graphics.json | NONE - both inputs OPTIONAL | UNDECLARED, see 3c |
| creative_cohesion.json | NONE - `cohesion_review` input is OPTIONAL | UNDECLARED, see 3c |
| catalog.json | `state.compile_manifest.clip_catalog` from catalog | DECLARED (`project_fps` also declared for other consumers) |

Result: 9 of 16 loads have a declared state key. The declaration cannot
be made - STOP, per the brief.

## 3. The seven undeclared loads: what each producer declares instead

### 3a. speech_sequence.json - dead load, no edge at all

The loaded value has zero references past the assignment
(`speech_data` appears once, at the load). There is no
`speech_sequence -> compile_manifest` DAG edge and no `speech_sequence`
manifest input. The producing node DOES declare state keys - for other
consumers: `state.mesh_spine.speech_sequence`,
`state.review_rough_cut.speech_sequence`. Declaring a requirement here
would assert consumption that does not happen; the truthful fix is to
delete the load, which is a body change and not authorised in this lane.

### 3b. plan_transitions / plan_sfx / plan_vfx - same key, different consumer

Each producer declares the very key compile_manifest reads, but bound to
a REQUIRED consumer elsewhere, so `produced_by` names this node while no
requirement names compile_manifest as consumer:

- plan_transitions declares `transition_spec` for creative_cohesion and
  plan_sfx (`state.creative_cohesion.transition_spec`,
  `state.plan_sfx.transition_spec`).
- plan_sfx declares `sfx_spec` for creative_cohesion
  (`state.creative_cohesion.sfx_spec`).
- plan_vfx declares `enhancement_spec` for render_motion_graphics
  (`state.render_motion_graphics.enhancement_spec`).

Compile_manifest's own inputs for all three are OPTIONAL, which
`prerequisites` deliberately excludes. Flipping them to required would
bend the contract: decoration absence is legitimate
(`tests/test_compile_manifest_without_the_decoration.py`). Extending the
layer to model optional edges is outside a declaration. STOP.

### 3c. color_grade / render_motion_graphics / creative_cohesion - zero derived effects

No derived requirement names any of these three nodes as producer, for
ANY consumer (measured over `derive_state_keys()`). The registry already
documents two of the three in `EMPTY_EFFECT_REASONS`:

- `color_grade.resolve`: ANALYSIS - real state with a real reader
  (`output_contract`: `compile_manifest.color_grade_spec`), but the
  manifest declares it OPTIONAL, so no requirement names `color_grade`
  as a producer.
- `motion_graphics.render`: ARTIFACT - the overlay reaches
  compile_manifest only through OPTIONAL inputs, so no requirement names
  `render_motion_graphics` as a producer.

Each node's own manifest DOES declare the output
(`color_grade_spec`, `motion_graphics_overlay` + `timed_text_overlay`,
`cohesion_review`) - the state is real, the requirement layer cannot see
it. For creative_cohesion this CONFIRMS the migration map's finding
("a real reader the requirement layer cannot see",
`data/vep-ren-migration-map/report.md` as cited in the brief): the
`cohesion_data.get("cohesion_review", {})` read at step.py:1817 is that
reader, exhibited. Inventing a key for any of the three is forbidden;
STOP.

## 4. Corrections to the brief's expectations (mine wins where it differs)

- The sixteen stand as listed - no more, no fewer.
- `render_subtitles.json` IS declared (`subtitle_overlay`, required
  edge). render_subtitles is NOT among the nine empty-effect operations;
  its derived effect is non-empty. Only render_motion_graphics of the
  pair is empty (ARTIFACT, documented).
- `semantic_analysis.json` IS declared (`semantic_analysis_documents`,
  required edge, one of eight derived requirements naming
  semantic_analysis as producer). It is not in the optional/absent-edge
  group.
- The stop still fires, on seven loads rather than "at least four":
  speech_sequence, plan_transitions, plan_sfx, plan_vfx, color_grade,
  render_motion_graphics, creative_cohesion.

## 5. The two non-`load` inputs: adjudication

- `_load_state_outputs(out_dir)`: NOT a contract input. It reads
  `pipeline_data.json`'s `step_outputs` - the state transport itself.
  Every declared state key arrives through it; declaring it separately
  would be declaring "state", which the layer already models
  (IN_STATE / RECORDED / SUPPLIED).
- `resolve_delivery_format(_project_root)`: NOT a state-key input. It
  reads project configuration (brand template + project.yaml override),
  i.e. static config, not pipeline step output - the same category as
  the framing-intent, tv-frame and bookend reads in this same function,
  which the code comments state explicitly are read directly rather than
  threaded as DAG edges. A future EXTERNAL_STATE requirement could model
  project-config presence (as `resolve.timeline_binding` does), but that
  is outside this mapping.

## 6. Composer count (pure planner, no run, no Resolve)

`reachable goals: 42  COMPLETED: 23  REFUSED: 19` - identical to the
pre-work baseline in the brief. Nothing moved, which is the correct
outcome: this lane registers nothing and changes no derived surface.

## 7. Scope notes

- `library/tools/operations.py` untouched (registry tuple, docstring
  counts, skill doc all unchanged; no `--emit-skill` regen needed -
  emitted doc would be byte-identical).
- `library/tools/requirements.py` untouched (no overlap with the live
  `fm/vep-capability-may-be-a-prompt` work in that file).
- Step body, legacy fallbacks and gathering order untouched.
- No test run per the brief (overheating machine); the composer count
  above is the only execution.
