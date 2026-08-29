# Decisions the pipeline asks for and throws away

The pipeline decision map counted eight families of field that a step ASKS a
model (or a measurement pass) to produce and that nothing downstream ever
reads.  This file re-measures every one at HEAD, records what changed since
the map was taken on 2026-08-25, and gives one recommendation per remaining
field.

**The recommendation is an opinion, not an action.**  Whether a capability is
wanted is the captain's call, and nothing here was deleted.  What WAS changed
is the one line that needed no taste at all: the `creative_direction` schema
mismatch, where seven code sites read keys step 2.01 is not asked for.

- Measured at `89c796b` (2026-08-29), on the branch that fixes line 7.
- Every verdict below carries the command that produced it.

## Why this list keeps growing back

`tests/test_manifest_readers.py` holds every top-level key of the assembly
manifest to a named reader, and it works.  But the manifest is the LAST
boundary, and each of these was dropped BEFORE it.  The check added with this
change - `tests/test_asked_fields_have_readers.py` - applies the same
discipline one layer earlier, at the step's own declared output schema.  It
ENFORCES on `creative_direction` and REPORTS on everything else.

Its blind spot is stated in the test and repeated here, because it decides how
much of this list a check can ever police: **it can only see a field a
manifest DECLARES in an `expected_schema`, and those schemas are one level
deep.**  A field asked for inside a list-item shape - 4.02's `duration_feel`,
3.03's `cut_decisions` - is invisible to any static check until the schema
describes it.  The smallest declaration that would close that is a nested
`expected_schema` for list-valued outputs; it needs no new file format and no
change to any frozen `handoff.md`.

---

## 1. `music_selection.splices` (step 2.04)

**PARTLY CLOSED - the capability landed under a different name; the field did
not.**

```
$ grep -rn --include='*.py' "splices" library/ | grep -v __pycache__
library/tools/music_section.py:19,21          (documentation of the defect)
library/steps/step_2_04_music_selection/post_bridge.py:123   (a comment)
library/steps/step_5_04_compile_manifest/step.py:1425        (a comment)
```

No code reads `splices`.  What the map's line was really about - which part of
the track plays - was closed by #309/#312 under the key `section`, which
`compile_manifest` reads at `step.py:1430` through
`music_section.resolve_section`.  `splices` is now a second, unread way to ask
the same question, and 2.04's frozen `handoff.md` still asks for it.

**Recommendation: DELETE.** `section` answers the question and is read; two
spellings of one decision is how the two readers of `target_energy` came to
disagree.  Deleting it means removing the key from `manifest.json`'s
`expected_schema` and `llm_outputs` description, which are not frozen.

## 2. `transition_spec[].duration_feel` (step 4.02)

**ALREADY CLOSED.**

```
$ sed -n '278,325p' library/steps/step_4_02_plan_transitions/post_bridge.py
```

The map recorded it as unreachable because the brand's
`transition_duration_ms` was read first and was always non-zero.  At HEAD the
plan's own word decides: `duration_map` renders `instant|quick|medium|slow`
into frames, a brand `{min, max}` only BOUNDS the result, a brand SCALAR is
consulted only when the plan declared no feel, and a drawn transition with
neither is DROPPED with the reason.  AGENTS.md section 10.5 states the rule.

**No recommendation needed.**

## 3. `music_analysis.structure.sections`, `.energy_dynamics`, `.key`, `.chords` (step 2.06)

**LEFT FOR THE CAPTAIN - still unread.**

```
$ for k in sections energy_dynamics chords key; do \
    grep -rn --include='*.py' "get(\"$k\")\|[\"$k\"]" library/ \
    | grep -v __pycache__ | grep -v analysis/music_pipeline | grep -v step_2_06; done
(no output)
```

Only `tempo.beats` / `tempo.downbeats` are read, through
`library/tools/beat_grid.py`.  The four above are computed on every run and
consumed by nothing.  Note that `energy_curve_1hz` is separately BANNED from
prompts by AGENTS.md section 10.1 (198 raw numbers), so it is not merely
unread - it is a cost with no destination.

**Recommendation: give `energy_dynamics` a reader; delete `key` and `chords`;
keep `sections` only if a reader follows.**  The bed's shape over time is the
one thing here that could plausibly drive a planned `music_behavior` curve,
which is a decision the pipeline already models.  A musical key and a chord
chart have no mechanism in this engine that could act on them.  `sections`
overlaps `music_measurement.track_sections`, which IS read.

## 4. `project_config.style_preset`, `project_config.subtitle_style` (step 1.01)

**LEFT FOR THE CAPTAIN - still unread, and the code says so.**

```
$ sed -n '235,244p' library/tools/brand_registry.py
# Only `target_duration_seconds` has a reader; the other two are what step
# 1.01 has always emitted and are kept so the two producers of
# `project_config` cannot disagree about its shape.
PROJECT_CONFIG_KEYS = ("target_duration_seconds", "style_preset", "subtitle_style")
```

`subtitle_style` in particular is a trap: a project's caption typography is
declared as `pipeline.subtitle_typography` and resolved by
`library/tools/subtitle_style.resolve_subtitle_style`, which never looks at
`project_config.subtitle_style`.  A captain who set the latter would see no
change and no error.

**Recommendation: DELETE both.**  Each names a capability that already has a
different, working declaration - `subtitle_style` duplicates
`pipeline.subtitle_typography`, and `style_preset` duplicates the brand
template reference.  A second spelling that silently does nothing is worse
than no field.

## 5. `cut_decisions` (step 3.03)

**LEFT FOR THE CAPTAIN - still unread, and not even routed.**

```
$ grep -rn --include='*.py' "cut_decisions" library/ | grep -v __pycache__
(no output)

$ python3 -c "import json;d=json.load(open('library/processes/edit_video/dag.json'));\
print([(e['to'],e['data_mapping']) for e in d['edges'] if e['from']=='review_rough_cut'])"
[('plan_subtitles',   {'rough_cut_review': 'rough_cut_review'}),
 ('plan_transitions', {'rough_cut_review': 'rough_cut_review'}),
 ('plan_vfx',         {'rough_cut_review': 'rough_cut_review'}),
 ('plan_sfx',         {'rough_cut_review': 'rough_cut_review'})]
```

3.03 declares two outputs and only `rough_cut_review` is carried on any edge,
so `cut_decisions` reaches no code and no prompt.

**Recommendation: give it a reader, or route it to a prompt.**  This is the
one line on the list where the asked-for thing is plainly the point of the
step: a rough-cut review that names cuts to make, whose cut list nothing sees.
Routing it into `plan_subtitles` costs one DAG edge and one manifest input,
and needs no frozen file touched.

## 6. `audio_mix` levels and the automation curve (step 5.02)

**ALREADY CLOSED.**

```
$ sed -n '1266,1330p' library/steps/step_6_01_render/resolve_build_timeline.py
audio_mix = manifest.get('audio_mix', {})
music_automation = audio_mix.get('music_automation', [])
master_limiter  = audio_mix.get('master_limiter', {})
$ ls library/tools/otio_mix.py library/tools/execution/deliver_audio_mix.py
```

Every planned dB now reaches Fairlight through an OpenTimelineIO round trip
(AGENTS.md section 5, "The mix goes through OTIO").  The cyan `UNAPPLIED
target` marker is the documented FALLBACK when Resolve declines the import,
not the delivery route.

**One part is still markers by design and correctly so:** `master_limiter` is
a master BUS setting and no clip-level route reaches a bus.  That is recorded
in AGENTS.md, not a gap.

## 7. `creative_direction` - eight of ten mechanical read sites (step 2.01)

**FIXED HERE.**  See `library/tools/creative_direction.py` and the PR body.

Re-measured at `89c796b`: nine of the ten sites the map counted are still
present, and SEVEN of them read a key step 2.01's schema does not define.  The
ninth - `creative_cohesion.map_energy` collapsing "building" into "high" - was
already closed by `library/tools/energy_reading.py`.

```
$ grep -rn --include='*.py' 'creative_direction\(\s*or {}\)\?\.get' library/ | grep -v __pycache__
generate_motion_props.py:129  visual_style      generate_motion_props.py:150  accent_color
generate_motion_props.py:168  title             generate_motion_props.py:169  subtitle
generate_motion_props.py:172  series_name       generate_motion_props.py:173  episode_label
step_4_04_plan_sfx/post_bridge.py:553  content_type
step_5_03_creative_cohesion/step.py:329  target_energy      <- the one real key
```

A further finding the map did not have: `transition_selector._is_high_energy`
now has **no production caller** - the scene-change defaults it gated were
withdrawn - so at HEAD `target_energy` reaches exactly one place, 5.03's
`measurements.declared_target_energy`, which reports it and judges nothing.
**The creative direction has no mechanical reader that changes a frame.**
That is a captain's question, not a defect, and it is out of this change's
scope: giving one of the eight fields a reader would be inventing a rule
about how a mood becomes a cut.

**Recommendation for the eight fields themselves: KEEP ALL EIGHT, none as
mechanical.** Seven are prompt-only by design - `mesh_spine` names six of them
in `context_fields` and nine handoffs read the direction whole - and a compass
that steers by being read is doing its job.  What should NOT come back is a
code site reading a ninth key; the new check makes that fail.

## 8. `object_segmentation` (1.06) and `ocr_extraction` (1.07)

**PARTLY CLOSED.**

```
$ python3 -c "import json;d=json.load(open('library/processes/edit_video/dag.json'));\
ids=[n['id'] for n in d['nodes']];print('ocr:',   'ocr_extraction' in ids);\
print('seg:', 'object_segmentation' in ids)"
ocr: True
seg: False
```

1.07 is now WIRED and deselected by default (#245, `run_scope.DESELECTED_BY_DEFAULT`);
`--with ocr_extraction` runs it.  1.06 is still unwired, with a documented
`unwired_reason` and a measured report in `docs/SUBJECT_MASKING_MEASURED.md`.

**Recommendation: leave both as they are.**  Each is now an explicit,
documented decision rather than a silent gap, and #162 tracks the design
question of what would consume masks and OCR output.  Wiring a producer before
a consumer exists is what put the other seven lines on this list.

---

## Summary

| # | Field | Verdict | Recommendation |
|---|---|---|---|
| 1 | 2.04 `splices` | PARTLY CLOSED | delete - `section` replaced it and is read |
| 2 | 4.02 `duration_feel` | ALREADY CLOSED | - |
| 3 | 2.06 `sections`/`energy_dynamics`/`key`/`chords` | LEFT FOR THE CAPTAIN | reader for `energy_dynamics`; delete `key`, `chords` |
| 4 | 1.01 `style_preset`/`subtitle_style` | LEFT FOR THE CAPTAIN | delete both - each duplicates a working declaration |
| 5 | 3.03 `cut_decisions` | LEFT FOR THE CAPTAIN | give it a reader, or route it to a prompt |
| 6 | 5.02 mix levels and curve | ALREADY CLOSED | - |
| 7 | 2.01 `creative_direction` read sites | **FIXED HERE** | keep all eight fields; seven are prompt-only |
| 8 | 1.06 / 1.07 | PARTLY CLOSED | leave both |
