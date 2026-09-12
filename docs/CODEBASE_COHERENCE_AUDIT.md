# Codebase coherence, measured 2026-09-12

An inventory of what has no caller, what is implemented more than once, and what is
declared but never enforced - taken across all 359 modules of `library/` (179,844
lines) at `main` 244158bc, and derived from the code rather than from impressions.

Three scanners produced it, and they are cheap enough to re-run:

1. **No caller.** Every module basename, searched as an identifier across every
   `.py`, `.md`, `.json`, `.js`, `.sh`, `.yml` and `.toml` file in the tree, with
   production references counted separately from test references and from docs.
2. **Unreferenced declaration.** A single token index over the same corpus, then
   every `def`/`class` and every `UPPERCASE` module constant whose name occurs
   nowhere outside its own definition line.
3. **Same question, two answers.** Every public module-level function name defined
   in more than one module.

Scanner 3's raw output is mostly a CONVENTION, not duplication: 85 names are
defined in more than one module, and the large families (`describe` ×13,
`summary_lines` ×8, `prompt_block` ×7, `resolve_declaration` ×5) are one module
each answering for itself. Reading the count alone would have produced thirty
speculative consolidations. Two families were real, and both are below.

## What had no caller

| Class | Count at 244158bc | Disposition |
|---|---|---|
| Modules with zero production references | 13 | see below |
| `def`/`class` names referenced nowhere at all | 37 | 9 removed with their modules; 28 remain |
| `def`/`class` names referenced only by a test | 60 | all remain - see "reads as coverage" |
| `UPPERCASE` constants read nowhere at all | 36 | 5 removed; 31 remain |

Of the 13 zero-production-reference modules, only **one** was dead:

- `library/tools/validate_assembly_manifest.py` (73 lines) - **REMOVED.** Zero
  occurrences of its name in any file type in the repository, including docs. Its
  own docstring said "For programmatic use, prefer
  `library/tools/manifest_validator.py`", and `manifest_validator._validate_structure`
  runs the identical `Draft7Validator` pass over the identical
  `library/schema/assembly_manifest.schema.json`, plus the semantic half, and IS
  wired (AGENTS.md 10.2).

The other twelve were each something else, and establishing which mattered more than
the count:

- **Entry points invoked as a string, not imported.** `gemma_shim` (`scripts/gemma`,
  launchd), `workflow_bridge` (`resolve_workflow_integration/.../main.js`),
  `footage_query_bridge` (documented prototype, and
  `tests/test_footage_query_prototype.py` FAILS if a step starts calling it).
- **Test files.** `library/tools/fusion/tests/test_{effects,nodes}.py` are a declared
  `testpaths` root; the scanner sees no importer because pytest collects them.
- **Test-only reachable, intent not established.** `overlay_verify`,
  `person_object_profiles`, `visual_component_plan`, `panel/region_mark`.
- **Deliberately kept, recorded in three places.**
  `library/tools/execution/apply_native_transitions.py` - AGENTS.md 5 closes the DRP
  project-file-surgery route, `docs/RULE_EVIDENCE.md#fcpxml-and-drp-are-closed` says
  the module "and its test remain in the tree unused", and
  `docs/PIPELINE_PLAN.md:1185` names it as deliberately NOT on the deletion list.
  Left alone. Deleting it would overturn a recorded decision.
- **Hand-run operator CLI, zero references of any kind.** `library/tools/resolve_sync.py`
  (334 lines) - filed below rather than removed.

## What was implemented more than once

### `journal_path_for` - six implementations, four of them lossy (FIXED)

Every irreversible act under `library/tools/execution/` writes a journal so it can be
undone, and each of the six modules that does so had written its own answer to "what
is that file called". All six docstrings said a fixed filename was wrong because a
later run overwrites the record an undo needs. **Two had actually done something about
it.** Measured by calling each function twice with one UTC stamp:

```
build_sweep.journal_path_for                suffixed -> _2   OK
execution/remove_proof.journal_path_for     suffixed -> _2   OK
execution/organise_media_pool               SAME PATH BOTH TIMES
execution/mark_master                       SAME PATH BOTH TIMES
execution/retire_empty_bins                 SAME PATH BOTH TIMES
execution/prune_orphans                     SAME PATH BOTH TIMES
execution/prune_orphans.manifest_path_for   SAME PATH BOTH TIMES
```

The stamp is second-granularity, so a second run inside one second - a verify-twice
pass, a `journals()`-driven retry - destroyed the first journal. The two that had the
loop had each paid for it in real data (`remove_proof`: nine removals in five files;
`build_sweep`: an empty second pass over a 102-file record) and each wrote the fix
locally, which is why the other four still had the defect two days later.

Now one owner, `library/tools/journal_naming.py`, and one mechanism:
`tests/test_journal_paths_never_overwrite.py` parameterises over the REAL functions,
so a seventh journal writer that hand-rolls a path fails it. `build_sweep`'s
per-area `mark_*.json` name went through the same helper - it was an eighth copy of
the same loop.

The module is `journal_naming`, not `journal_path`: `journal_path` is a parameter name
in four of the six callers and a module by that name would shadow it.

### The two dead Fusion generator wrappers (REMOVED)

`library/steps/step_6_01_render/fusion_comp_generator.py` (113 lines) and
`fusion_transition_generator.py` (94 lines) were thin wrappers over
`library/tools/fusion/`, duplicating `comp_builder.build_effect_comp`'s dispatch.
`docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` had already measured that no step, bridge,
manifest or DAG node reached either; their only importer was
`test_integration_demo.py` (478 lines), a manual Resolve demo that pytest does not
collect. All three removed, and with them:

- the sole entry in `DELIBERATE_EXCLUSIONS` in `tests/test_no_orphan_test_files.py`;
- an exemption from `tests/test_reel_read.py`'s "no module outside the readers touches
  Resolve directly" allowlist, which the dead demo was holding;
- two documents that pointed future readers at the dead wrapper -
  `.agents/skills/davinci_resolve_pipeline/SKILL.md` and
  `docs/architecture/pipeline_master_reference.md`.

That skill doc is the more important half. It described `generate_comp(...)` with
concrete `zoom_mid=1.04`, `grade_gain=1.05`, `glow_gain=0.08`, `vignette=True,
vignette_blend=0.25` in a worked example, and advertised a `SEGMENT_PRESETS` table
deleted under P3.4 by the captain's ruling of 2026-08-16. `vignette` at blend 0.25 is
the exact "vignette nobody asked for at a strength nobody chose" AGENTS.md 12 records
as removed. `tests/test_no_creative_floors.py` reads step `handoff.md`/`manifest.json`
and the role block; it does not read `.agents/skills/`, so those numbers sat in an
agent-facing document with no guard on them. The section now names
`plan_vfx.TOOLKIT_PARAMETERS` and states that it carries no value, default or bound.

## What was declared and never enforced

- **`library/schemas/resolve_manifest.py`** (108 lines, 9 Pydantic classes) -
  **REMOVED.** Nothing imported it. Its only "reference" was the prose string
  `"expected_schema": "resolve_manifest.py"` in three step-manifest output slots, and
  nothing resolves that string to a file (`test_asked_fields_have_readers` reads a
  string `expected_schema` as prose). It also described the assembly manifest, not the
  three outputs that named it - `audio_spine`, `b_roll_assignments`,
  `b_roll_interjections`. Those three now name the contracts that really govern them
  (AGENTS.md 6 and `spine_contract.py`; AGENTS.md 7, `manifest_validator.py`,
  `cutaway_window.py`). The live schema, `library/schema/assembly_manifest.schema.json`,
  is untouched and still enforced by `manifest_validator`.
- **`TRANSITION_PRESETS`** - **REMOVED** from `library/tools/fusion/presets.py`, where
  five entries each declared a 7-frame duration and nothing read them; a second,
  equally unread copy went with `fusion_transition_generator.py`. A duration is how
  strong an effect is, which is the PLAN's number and not a scale the engine offers
  (AGENTS.md 10.5), so an unread table of sevens was a creative floor waiting for a
  reader. `presets.py` keeps its docstring: `docs/PIPELINE_PLAN.md` cites it as the
  record of what P3.4 deleted and why.
- **`TARGET_WIDTH` / `TARGET_HEIGHT` / `TARGET_FRAME_RATE`** in
  `library/steps/step_3_01_assign_aroll/step.py` - **REMOVED.** Commented "used only
  by callers that import the helpers directly", with no such caller anywhere. The
  comment was the only thing keeping a hardcoded 1080x1920@30 in a step that resolves
  its delivery format per project (AGENTS.md 10.1).
- **`resolve_placement_lock`** in `library/tools/resolve_lock.py` - **REMOVED.** A
  `@contextmanager` taking an exclusive `flock` on `/tmp/resolve_placement.lock` that
  nothing ever entered, in a module whose docstring is about serialising placement. It
  read as though placement were serialised across processes when the only live guard is
  `assert_current_timeline`, a per-call check. `PLACEMENT_REQUIRES_CURRENT` stays: it is
  the recorded reason for the guard that IS live.
- **The phantom prompt override** in `library/tools/video_segment_analyzer.py` -
  **REMOVED.** `try: from visual_qa_prompts import PROMPTS / except ImportError:` around
  a hardcoded dict. `visual_qa_prompts` has never defined `PROMPTS`, so the except
  branch ran every time and a reader looking for the source of those prompts was sent
  to a module that does not have them. Same pass moved the file off bare sibling
  imports (`from vision_model import`, `import render_qa`) - it was the only module in
  `library/tools/` still using them, and it could not be imported by its own package
  path from a clean interpreter, which is exactly how its one production caller
  (`visual_qa_router:398`) imports it.

## Filed, not done

Each of these is real and each was left alone on purpose.

- **`library/tools/resolve_sync.py`** (334 lines). A hand-run CLI
  (`sync|verify|clean|status`) that symlinks `library/presets/` into Resolve's
  application-support directories. Zero references of any kind - no caller, no test, no
  doc, not named by either `scripts/install_*.sh`. It is the sole reader of seven
  `paths.py` constants, one of which (`PRESETS_FAIRLIGHT`) points at a directory that
  does not exist, so one of its three link sources has never resolved. **Needs the
  captain's intent**: it may be how their Fusion macros got installed. Deleting it is a
  four-line change once someone says so; guessing is how a working hand tool
  disappears.
- **Sixty `def`/`class` names reachable only from a test.** This is the dominant shape
  of "reads as coverage" in this tree, and it is the shape `marker_resolution` had -
  good code, never in the loop, and deleting it would have been wrong. Representative:
  `overlay_verify.verify_values` / `verify_pixels` (7 and 4 test refs),
  `transform_drift.built_transforms` / `drift_rows` (8 and 10),
  `visual_component_plan.resolve_component_plan` / `component_layers`,
  `marker_resolution.read_resolution`, `fusion/transition_frames.transition_splines`.
  Triaging sixty items needs sixty intent decisions; a partial pass would leave nobody
  able to tell which half was judged.
- **`format_toon`, six copies.** `step_2_02_speech_sequence`, `step_3_02_select_broll`,
  `step_4_02_plan_transitions`, `step_4_03_plan_vfx`, `step_4_04_plan_sfx` and
  `step_4_06_render_motion_graphics` each define a five-line TOON table formatter;
  three are byte-identical and the other three add an empty-rows early return. None of the six
  quotes a cell, and `library/tools/toon_serializer.py` is the owner that does -
  AGENTS.md 10.1, "a TOON table cell is quoted with a BACKTICK", written for
  `RULE_EVIDENCE.md#the-apostrophe-was-doubled-in-every-prompt`. **Deferred because
  these six files are prompt-construction and a sibling lane owns the model-call
  path**; consolidating them changes prompt bytes.
- **Seven unreferenced prompt builders** in `library/tools/visual_qa_prompts.py`
  (`color_grade_prompt`, `transition_prompt`, `subtitle_prompt`, `vfx_prompt`,
  `clip_placement_prompt`, `general_qa_prompt`, `pre_render_sweep_prompt`). The module
  is live; these seven have no caller. Same lane boundary as above.
- **Bare sibling imports, about 45 sites.** `apply_fusion_comps`,
  `resolve_build_timeline`, `render_qa`, `master_loudness`, `model_lifecycle` and
  others import siblings by bare name and rely on a `sys.path` entry. A module imported
  once bare and once as `library.tools.X` is loaded TWICE, under two `sys.modules` keys,
  with independent state. Measured on the `apply_fusion_comps` import: 14 library
  modules loaded, 1 of them twice (`execution/__init__`) - so the class is real and
  today's cost is small, because most of those imports are lazy and inside functions.
  Converting the convention is a wide mechanical change across the build path; it buys
  correctness-under-patching, not speed, and a sibling lane owns those files.
- **`library/schema/` and `library/schemas/`**, two sibling directories one character
  apart, holding the JSON schemas and the Pydantic models. Renaming either is churn
  across every importer for taste alone.
- **`proof_cleanup.py` + `execution/remove_proof.py`** (739 lines) implement one
  authorised deletion of two named timelines under the captain's verbatim 2026-09-10
  instruction, and the modules say they cannot be pointed at anything else. That is a
  one-off operation carrying a permanent control layer. Worth a decision about whether
  it retires now the act is done; not worth removing unilaterally, because
  `journal_path_for`'s collision lesson was measured here.

## Not examined

Said plainly, because a report that implies the whole surface was read is the claim
this project has been burned by:

- **`tests/`, 462 files and 139,633 lines, was NOT audited for duplication.** It is
  larger than `library/` and nobody has looked. The one signal collected: scanner 3 was
  run over `library/` only, so the analogous "same question answered twice" count for
  the suite is unknown.
- **The two DAGs' step bodies** (`library/steps/*/step.py`, `bridge.py`,
  `post_bridge.py`) were read only where a scanner pointed at them. No systematic pass.
- **`library/dashboard/`** (FastAPI server and its static JS) was covered by the
  scanners and not read.
- **The model-call path and the build/render path** were inventoried but not edited -
  two sibling lanes own them.
