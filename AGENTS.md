# Video Editing Pipeline

## How to read this file

You never need one in order to obey a rule.

Before your first run, read §9 (environment), §3 (how a run works) and §5 (DaVinci Resolve).

Section numbers are cited from code comments and other docs, so they are stable.
Change what is in a section, not its number.

## 1. Identity and purpose

The engine stores no project data - assets and pipeline outputs live in isolated directories outside the repository.
Agents and human editors use it to automate the tedious parts of video assembly while retaining creative control.

## 2. Repo layout

- `library/`: core pipeline implementation and shared Python modules.
- `library/processes/`: `edit_video` and `reels` (§3), each with its own `dag.json` and `manifest.json`.
- `library/steps/`: individual pipeline steps, named by phase (e.g. `step_1_01_scan_project`).
- `library/tools/`: shared utilities for Resolve scripting, vision analysis and file management.
- `library/schemas/`: Pydantic schemas for pipeline state and project configuration.
- Brand templates live with the project as `brand.json` (§10.1).
- `library/profiles/`: declared run configurations - which steps a run fires and where it stops (§3).
- `.agents/skills/`: the ONE skill source - Codex and opencode read it, `.claude/skills` links to it; a pipeline skill's entry point is `library/skills/<name>/`.
- `library/presets/`: Fusion macros and DaVinci's own built-in effect settings. Whatever reaches a timeline is found by direct path; there is no preset index.
- `remotion-subtitles/`: Node.js React app that renders subtitle overlays.
- `scripts/`: bash helpers for environment setup and maintenance.
- `tests/`: unit and integration tests for the engine.
- `ren/`: the installed `ren` CLI, the ONE front door - `ren --help` lists its verbs, `ren doctor` checks the machine. It execs `manage_project.py`, which stays underneath. Machine paths: `~/.config/ren/config.env` (`library/tools/paths.py`). `ren undo`: touch in place, rebuild by version (`library/tools/undo_journal.py`).
- `requirements.txt`: Python dependencies.

**One thing in `library/tools/` is a prototype: it is a `ren search` verb and stays out of the PIPELINE.**
`footage_query.py` / `footage_segments.py` are a cross-clip footage
search - "where in all my footage does X happen". Its stdin/stdout bridge
(`footage_query_bridge.py`) was retired in P2 - `ren search` is the caller now.

[`docs/FOOTAGE_INDEX_PROTOTYPE.md`](docs/FOOTAGE_INDEX_PROTOTYPE.md) has what it measured, the
measured score floor that lets it answer "not in this footage", and the 66x reduction of
`temporal_index` that is worth doing without it.

## 3. Pipeline execution

The pipeline is a Directed Acyclic Graph (DAG) in `library/processes/edit_video/dag.json`, ordered by topological sort.

**There is MORE THAN ONE PROCESS, and `library/steps/` belongs to the repository, not to one.** `reels` is the second; node ids are unique across both.
One enumeration, `library/tools/processes.py`. [why](docs/REEL_BUILD_HAS_NO_OWNING_NODE.md)


- Call the analysis stage **preflight**, never "phase 1", even though its step ids read `step_1_0X_*`. `object_segmentation` (1.06) runs after the planning steps and segments ONLY the clips a subject grade or `behind_subject` plan names - the trigger is stated in `step_1_06_object_segmentation/step.py` ([`docs/SUBJECT_MASKING_MEASURED.md`](docs/SUBJECT_MASKING_MEASURED.md)). A step directory with no DAG node fails `tests/test_step_dag_coverage.py` unless `project_layout.STEPS` documents its `unwired_reason`. `prosody_analysis` (1.05) was re-wired on 2026-09-01 - its deterministic measurements are unbiased signal the model lacked - and routes to 2.01 and 2.02 via `view:prosody`. [the measurement that unwired it, now overruled](docs/PROSODY_MEASURED.md)
- **UNWIRED and DESELECTED are different things, and only one is a property of the pipeline.** Unwired means no DAG node exists.  The two lists are `project_layout.STEPS` and `run_scope.DESELECTED_BY_DEFAULT`; a step is in one or the other, never both.
- `objects[].readable_text` in semantic analysis output is the VLM's field - step 1.03 prompts for it directly. The local model (`gemma-4-12b-it-4bit`) reads on-screen text **sparsely, not never**; a recorded claim that it "provably cannot" read text was wrong.
- Step 1.07 `ocr_extraction` is WIRED and DESELECTED BY DEFAULT: `--with ocr_extraction` turns it on.

| Flag | Effect |
|---|---|
| `--from <step_id>` | resume from a step |
| `--step <step_id>` | run one isolated step |
| `--auto` | auto-complete hybrid steps from bridge output instead of pausing for LLM input |
| `--review` | enable review gates; a human answers in `review_gate.py` |
| `--resume` | continue after a review gate is approved or revised |
| `--dry-run` | print the execution plan without running steps |
| `--rerun <target>` | redo finished work; repeatable, and the ONLY supported way to re-run a completed step |
| `--target <name>` | run only what a named destination needs |
| `--only <step_id>` | run this step and whatever it cannot run without; repeatable |
| `--skip <step_id>` | leave a step out; repeatable |
| `--with <step_id>` | turn on a step that is off by default; repeatable |
| `--override <req>` | proceed past a refusal the requirement allows; RECORDED |
| `--profile <name>` | run under a DECLARED run configuration; `none` declines the one the project adopts |
| `--break <step_id>` | stop after this step for review; repeatable, `*` means every step |
| `--no-break <step_id>` | do not stop after this step; repeatable, `*` disarms every breakpoint |

### Scoping a run

One enumeration, `library/tools/run_scope.py`, and both CLIs register its flags from it.

### Configuring a run

**A run shape is DECLARED as data, and it has no power `run_scope` does not already have.**
Detail: `library/tools/run_profile.py`.

**A breakpoint is armed PER STEP, and `--review` is the every-step case.**
Detail: `library/tools/breakpoints.py`.

### State the pipeline did not produce

**A prerequisite may be satisfied from outside the pipeline, and it is CHECKED, never asserted.** A LIVE Resolve timeline is one such producer; a CLOSED project is not.
Detail: `library/tools/external_inputs.py`, `library/tools/timeline_ingest.py`.

### A declaration must be true

**No step may declare an input required that nothing refuses on, or optional that its own code refuses without.**
**A prerequisite is EXECUTABLE, not prose, and is asked of what will RUN.**
Detail: `library/tools/requirements.py`, `library/tools/input_contract.py`.

### Two ledgers, two lifetimes

One enumeration: `library/tools/step_ledger.py`.

### Run status

The run summary reports `SUCCESS` only when the whole DAG is complete and `failed_steps` is empty in the project ledger - not just the steps this invocation touched.
Detail: `library/processes/edit_video/run_pipeline.py`.

### A run that was RESTARTED says so, in its own outputs

One enumeration, `library/tools/run_restart.py`.

### A step says what it could not determine

One enumeration, `library/tools/undetermined.py`.

### A step says where its measurements contradict the direction, and complies anyway

One enumeration, `library/tools/direction_contradiction.py`.

### A step that makes a craft judgement is told what craft it is

One enumeration, `library/tools/craft_role.py`. [why](docs/RULE_EVIDENCE.md#twelve-handoffs-no-role)

### A step with no creative brief ASKS, rather than planning in silence

One enumeration, `library/tools/brief_attachment.py` for the choice and `library/tools/briefing_interview.py` for what happens when it goes the other way.

### A contract rejection reaches the model that caused it

One enumeration, `library/tools/post_bridge_retry.py`.

### A reel nothing changed about is not placed again

One enumeration, `library/tools/reel_rebuild_need.py`, and the decision is FAIL-CLOSED.

## 4. Dashboard

Retired from the product (P2, D3): no server, no browser surface, no `ren review` verb. What stays is the
shell side - `ren search` / `ren search-index` for footage search (Q11), `python3 -m library.tools.review_gate answer`
for review gates, `python3 -m library.dashboard.review_channel poll|reply|list` for the hook layer's note feed,
and the file protocol in `library/tools/run_control.py` (`pipeline.hold`, `pipeline.pid`, `pipeline_run.json`).

- Review gates pause execution for inspection.  A rejected gate halts the pipeline entirely; a revised gate applies the reviewer's modifications directly to the step output in `pipeline_data.json`.
- Retired with the browser: **The picker lists the project the server is SERVING, first**;
  **Extend this dashboard. Never author a fresh per-run review page.**;
  **Footage Search is a span search; Footage Library is a clip browser. Keep them apart.**

### Footage search

`ren search-index` builds the index ONLY when asked, into
`<project>/pipeline_output/scratch/footage_index/`. No build at server start or on first query:
both wrote into the captain's project unasked.

- **A ranking cannot say "not here", so there is a floor.** Retention is judged on the RAW
  dense cosine (`DENSE_SCORE_FLOOR`), never on the blended score. Below it and above
  `DENSE_WEAK_FLOOR` is a WEAK band.
- Staleness compares an `ingest_fingerprint` recorded at build time. `pipeline_data.json` is
  fingerprinted by its `catalog` subtree only - the state writer rewrites the whole file after
  every step, so a downstream step landing is not a change to the footage.
- Retired with the browser: `library/dashboard/footage_search.py` (warm index,
  `cold`/`loading`/`ready`/`unavailable` loader).

### The review return channel

**One send carries the whole queue and wakes an agent, which replies onto the same surface.**
`library/dashboard/review_channel.py` is the store and the agent side; the browser half
(`/api/review/*` in `server.py`, polled as `GET /api/review/poll`) is retired. The agent polls with
`python3 -m library.dashboard.review_channel poll` (`wait_for_batch`) and replies onto the notes.

Notes live per project in `pipeline_output/review/channel.json`, each recording the view it was written on.

A note whose `anchor.selector` is empty is REJECTED.

- Retired with the browser: **A note is anchored to a specific element, and the anchor is computed in the browser.**
  `computeAnchor` in `library/dashboard/static/components/review-channel.js` measured it and `resolveAnchor`
  walked it back; the store keeps the measured anchor.

### Run control

**The handbrake is a file, not a signal.** `library/tools/run_control.py` owns the whole vocabulary -
`pipeline.hold`, `pipeline.pid`, `pipeline_run.json` at project root - and both processes speak only through it.
`pipeline_run.json` is the runner's own account of itself (mode, current step, how it ended).

- Retired with the browser: the Start, Handbrake, Resume and Step buttons, which launched
  `run_pipeline.py` as a child process (Start used `--full-auto agent` and did NOT pass `--review`).

## 5. DaVinci Resolve integration - CRITICAL RULES

### Connection

**A Resolve project is addressed by its EXACT listed name, never a prefix.**: `library/tools/timeline_ingest.py`.

**Timeline speech is REBUILT from source, not rendered; WHICH transcriber hears it is a seam that FALLS BACK**: `library/tools/timeline_transcript.py`, `library/tools/hybrid_transcription.py`.

### Process isolation

Never create a timeline and use `ImportFusionComp` in the same Python process: `library/tools/execution/apply_fusion_comps.py`.

### One Resolve, many writers

**The unit of exclusion is the instance CURSOR; a write REFUSES outside the lease, and a foreign move is DETECTED.**: `library/tools/resolve_lock.py`.

**What may run in parallel is a TABLE**: `library/tools/concurrency_routing.py`; declarations contend per KEY: `library/tools/declaration_keys.py`.

### Judge every Resolve call by what it returns

**Judge a Resolve call by what it RETURNS, never by `hasattr`**: `library/steps/step_6_01_render/probe_resolve_capabilities.py`.

**A comp's MediaIn must COVER every frame its item PLAYS, or Resolve FAILS the render there.**: `library/tools/comp_media_window.py`.

**Pan/Tilt is ONE model, and a unit is not a pixel.**: `library/tools/resolve_transform.py`.

### Transitions go through Fusion or Resolve itself.

**Do not wire FCPXML or DRP project-file surgery back in.**: `library/tools/transition_vocabulary.py`.

**A DRAWN transition can only sit where a V1 clip ends.**: `library/tools/transition_carriers.py`.

### Stabilization is the memory ceiling, and it runs last

Detail: `library/steps/step_6_01_render/resolve_build_timeline.py`.

### Fusion .comp files - NEVER

**Six things that must NEVER appear in a Fusion .comp.**: `library/tools/fusion/comp_builder.py`.

### Fusion .comp files - ALWAYS

**What a Fusion .comp must ALWAYS carry.**: `library/tools/fusion/comp_builder.py`.

### Frame mapping

One enumeration, `library/tools/fusion/played_window.py`; a comp is KEYED to it: `library/tools/composed_edit.py`. [why](docs/RULE_EVIDENCE.md#the-comp-that-had-to-be-re-derived)

### Default transition values

**The default transition values** - Brightness Flash, Crash Zoom and Glow: `library/tools/fusion/effects.py`.

### Tracks

Rows are the SOP's: `docs/TIMELINE_SOP.md`; `library/tools/timeline_layout.py` owns index and name, `library/tools/execution/fusion_tracks.py` the comp rows.

### Media pool and audio

One enumeration: `library/steps/step_6_01_render/resolve_build_timeline.py`.

### The mix goes through OTIO, and it goes in at placement time

**in dB**: `library/tools/otio_mix.py`. [why](docs/RULE_EVIDENCE.md#the-mix-goes-through-otio)

### Visual verification

**Both pages show the SAME PIXELS through DIFFERENT VIEWERS; measure a grade on an EXPORT, never on a viewer.**
`library/tools/resolve_surfaces.py`. [why](docs/RULE_EVIDENCE.md#the-two-pages-that-showed-one-frame)

**A capture that did not happen RAISES.**
`library/tools/marker_capture.py`. [why](docs/RULE_EVIDENCE.md#the-still-that-was-never-taken)

### Reading a killed build off disk

**Copy the project database before opening it; never open it in place.**

### Organising the pool

**FILED from measurements; nothing deleted. Smart bins are NOT scriptable.**: `library/tools/resolve_organization.py`.

**REMOVING is a different act: `DeleteClips` on a TIMELINE's pool item DELETES THE TIMELINE.**: `library/tools/orphan_removal.py`.

**Every build SWEEPS: empty bins and dead pool items go, superseded files to quarantine; nothing is unlinked.**: `library/tools/build_sweep.py`.

**A staged timeline awaiting promotion is HELD.**: `library/tools/staging_holds.py`.

### Markers and timeline items

`timeline.AddMarker()` and `timeline.GetItemListInTrack()`; patterns in `timeline_item_markers`.

**Markers are the ONLY write the master takes, placed by footage overlap.**: `library/tools/master_markers.py`.

## 6. The spine contract

- Every block carries `clip_id`, `source_start`, `source_end`, `word_timestamps` and `alignment_method`. `None`/empty only for non-speech blocks.
- **Read those keys directly** (`block["clip_id"]`). 
- Word timings use `source_start`/`source_end`, not `start`/`end`.
- `speech_sequence` (2.02) treats the LLM's `source_start`/`source_end` as a LOOKUP HINT only.  This is what stops invented round-number ranges reaching the timeline.
- **Two body passages cut from one clip may not claim overlapping source ranges.** 2.02 re-anchors such a passage past the previous one or fails it; `manifest_validator` asserts the same on consecutive V1 clips. 
- **A passage is anchored by SEARCH, never by the occurrence nearest the hint.** 2.02's `_align_words_to_text` tries every occurrence of the passage's first word, both from the occurrence and from `ANCHOR_BACKUP_SECONDS` before it, and ranks: most passage words aligned, then SHORTEST SPAN, then smallest leading gap, then hint proximity. 
- **There is no gap threshold and no voiced-fraction band.** A silence survives exactly when no equally complete anchor removes it. Both outcomes are SAID: `alignment_report` records the leading gap, voiced fraction and anchors considered, and the `reanchored`/`held` cases print to stderr. 

## 7. Data flow

Each step reads required upstream outputs based on the DAG's `data_mapping` edges and writes its own output back under `step_outputs.<step_id>`.

- `catalog`: video metadata, durations, file paths.
- `semantic_analysis`: the v3 vision observations - scene, camera, actions, objects, assessment - plus the view derived from them. It measures no mood or energy (see §10.1).
- `speech_sequence`: speech segments ordered into a coherent narrative.
- `aroll_assignments`: narrative blocks mapped to source clips and timeline ranges.
- `broll_selections`: secondary footage (`b_roll_assignments`) and standalone cutaways (`b_roll_interjections`), assigned to cover A-roll segments or to insert visual breaks.
- `compile_manifest`: all decisions consolidated into an `assembly_manifest.json` that drives the final Resolve render.

## 8. Project management

- Create with `python3 manage_project.py new <slug> --name "Project Name"`.
- Project configuration is `project.yaml` inside each project directory; the `ProjectConfig` schema defines source settings, pipeline options and Resolve bindings.
- The project registry scans the root directory to list and manage available projects.
- A project outside `PIPELINE_PROJECTS_ROOT` is addressed by passing its absolute path in place of the slug to `run`, `status` and `info`. 

### Where a project's files go

**One module owns the project-side layout: `library/tools/project_layout.py`.**
**`pipeline_output/steps/` IS the pipeline.**
**Backups of `pipeline_data.json` are automatic and bounded.**
Detail: `library/tools/project_layout.py`.

### Reading a run back

**The layout answers "which step wrote this" by where the file is. Provenance adds WHICH RUN and FROM WHAT.**
**Never attribute a file to the nearest plausible step.**
Detail: `library/tools/provenance.py`.

**Two generated documents, regenerated on every run and by `manage_project.py trace <slug>`.**
Detail: `library/tools/run_traceback.py`.

**A project that predates the layout is brought onto it with `manage_project.py organize <slug>`.**
Detail: `library/tools/project_migration.py`.

### Replaying a step without running the pipeline

**A step's exact prompt and context can be rebuilt off frozen state, at a named revision, with no pipeline run, no Resolve and no project write.**
Detail: `library/tools/replay_bench/bench.py`.

### No test reaches a real project

**A test builds its project under `tmp_path`, or it skips. It never falls back to a real one.**
Detail: `tests/test_tests_never_reach_real_projects.py`.

## 9. Environment and dependencies

- **`run` is NOT the only command needing the dedicated `.venv`: `build-reels` does too, and `ML_DEPENDENT_COMMANDS` does not list it.** The preflight is NEVER at import time and checks that list, so `reel_look` refuses the punch-in inside the build, when cv2 has no Haar cascade (>=4.8,<5). A build also needs those variables set and the manager PARKED in the project's `resolve.folder` - it reads the CURRENT folder (`library/tools/timeline_ingest.py`). `tests/test_cli_ml_preflight.py`. [why](docs/RULE_EVIDENCE.md#the-dashboard-could-not-be-opened)
- Set `HF_TOKEN` for HuggingFace models and `PIPELINE_SFX_LIBRARY`/`PIPELINE_MUSIC_LIBRARY`/`PIPELINE_PROJECTS_ROOT` to absolute paths. Never hand-export the Resolve preamble or re-type the venv path: `.opencode/plugins/vep-env.js` injects `RESOLVE_SCRIPT_API`/`RESOLVE_SCRIPT_LIB`/`PIPELINE_PYTHON` into every shell, and `bin/vep <py-args>` runs the ladder-resolved interpreter. `tests/test_vep_env.py`.
- Python dependencies are in `requirements.txt`. `librosa` is required by `music_analysis`; without it the step reports `available: false` and the run fails rather than continuing silently.
- External tools: `ffmpeg`/`ffprobe`, Node.js for Remotion, and GPU for Gemma 4, SAM 2, WhisperX and EasyOCR.
- **A shared dependency lives ONCE PER MACHINE, outside every checkout; a checkout FINDS it, and absence REFUSES rather than skipping or falling back.** `library/tools/shared_environment.py` locates the Node store and the ML interpreter: `docs/SHARED_ENVIRONMENT.md`. The venv build is `docs/ML_ENVIRONMENT.md`.
- **Every `subprocess.run` capturing text must pass `encoding="utf-8"`.** `text=True` decodes with the locale codec, and this pipeline writes UTF-8 status glyphs. [why](docs/RULE_EVIDENCE.md#text-true-decodes-with-the-locale-codec)
- **Reach Resolve through `library/tools/resolve_locale.scriptapp_preserving_locale`, never `dvr.scriptapp` directly.** The call resets `LC_CTYPE` to `C` down in Blackmagic's library, breaking every later encoding-defaulted read. Only `LC_CTYPE` is restored, never `LC_NUMERIC` - ledger and reason in `library/tools/resolve_locale.py`.

### What CI actually checks

**The build has one gate that CAN fail and one report that cannot, and the two run under DIFFERENT ruff configs.**
`ruff-ci-gate.toml` at the repository root is the enforcing half - what is enforced, what is deferred and the count of each are written down in it.
`.github/workflows/ci.yml` runs it with NO `|| true`; the unfiltered run beside it keeps `|| true` and annotates everything.
[why](docs/RULE_EVIDENCE.md#the-build-that-declined-to-look)

- **A deferral is per FILE with its count, and that is weaker than it reads**: a listed file is wholly exempt, so a NEW violation in one still passes. Fixing a file means DELETING its line - a line no longer needed is a lie about what is still owed.
- **The runner installs ffmpeg**, because 27 library files shell out to it and every audio/video measurement path skipped without it. The suite skipped HONESTLY, which is what made it invisible.
- **pytest runs with `-rs`.** `131 skipped` names nothing; a build that declines to measure something must say what.
- `tests/test_ci_can_fail.py` reads the workflow and fails the moment either hole reopens.

**CI is THREE LAYERS, and only the last is on GitHub.**
Detail: `docs/CI_LAYERS.md`. No push/dispatch/PR fires anything: one
clean-room gate per BATCH on the `run-tests` label proving install from declared
manifests. The full-suite gate is LOCAL, free - firstmate runs
`scripts/full_suite_gate.sh` (4m54s) per batch, parallel by default; control: `--no-parallel`.

- **Run the tests of everything that depends on your change, in both directions** - the modules it uses AND every module that uses it - and expect no CI verdict on your PR.
  Captain's rule, 2026-09-03: *"is there a reason why we run all these test locally
  and fry the CPU?"* - so compute the selection with `scripts/select_dependent_tests.py <changed-file>` instead of judging it.
- **Wide fan-out is the one exception: NAME it.** `compile_manifest` is a fair claim; a renderer, a docs move or an
AGENTS.md restructure is not.

### This file is an INDEX, and two gates keep it one

**A rule lives with the code it governs; AGENTS.md keeps the headline and points at it.**
Measured 2026-09-03: the file regrew 34,848 characters in the two working days after the
2026-09-01 condensation - twelve commits, mean +2,913 each, `###` subsections 39 -> 47 with
none removed - because the working convention was "land a module, add a section describing
it". A condensation buys two days; only moving the detail out changes the slope.

- **`scripts/check_agents_md_size.py` gates the size, globally and PER SECTION**, and runs in
  CI. Both ratchets may ONLY EVER MOVE DOWN. `SECTION_BUDGETS` must SUM to no more than
  `CEILING`, so a budget cannot be raised without lowering another; a `##` section with no
  budget row FAILS rather than defaulting, because an unbudgeted section is the trap.
- **`scripts/check_agents_md_preservation.py` gates a MOVE**, and is run by hand with a
  `--before`. `--after` takes the rule corpus - AGENTS.md first, then every destination,
  **ENUMERATED, never a glob**: a glob would let a rule survive because its identifier happens
  to occur in an unrelated file, and the enumeration IS the reviewable list of claimed
  destinations. It prints WHERE each rule landed: a PASS reads as a mapping, not a count.
- **A section may shrink to an index row; it may not shrink into silence.** Past the shrink
  floor, what remains must NAME a destination that is in the corpus AND has gained content.
  Naming a destination that received nothing is refused.
- **Headings never move**: section numbers are cross-referenced from
  code (`AGENTS.md 10.1`, `§10.5`).
- `tests/test_agents_md_gates.py` pins that both gates can FAIL - AGENTS.md 10.4's rule
  applied to the gates on this file.

## 10. Cross-cutting rules

These hold across steps and cost a full audit cycle each. Do not undo them.

### 10.1 Contracts between steps

**Key-name mismatches are the dominant bug class.**
Index required keys directly so a rename fails loudly; never `.get()` a default for a key a contract promises. [why - and the known disagreements](docs/RULE_EVIDENCE.md#key-name-mismatches)
Join semantic documents to the catalog with `library/tools/semantic_index.py`.

**An edit lands at the layer that OWNS it; an ending only TRUNCATES; a promotion NAMES a marker it cannot carry.** `library/tools/edit_depth.py` - twelve classes, twelve owners.

**A declared output has a READER.** `library/tools/output_contract.py`. [why](docs/RULE_EVIDENCE.md#the-outputs-nobody-read)

**A step reads the vision document through a SUMMARY its own handoff names, not through the raw document.** `library/tools/vision_schema_adapter.py`. [why](docs/RULE_EVIDENCE.md#the-transition-planner-read-the-raw-document)

**`compile_manifest` reads `pipeline_data.json`, not just files.** `library/steps/step_5_04_compile_manifest/step.py`. [why](docs/RULE_EVIDENCE.md#compile-manifest-read-an-empty-catalog)

**Declare `interface.llm_outputs` on any hybrid step whose bridge emits a key the step also declares as an output** `library/processes/edit_video/run_pipeline.py`. [why](docs/RULE_EVIDENCE.md#empty-llm-schema)

**One vision schema, two views.** `library/tools/vision_schema_adapter.py`. [why](docs/RULE_EVIDENCE.md#vision-schema-two-views)

**A project's brand template reaches the run through `state["brand_template"]`.** `library/tools/brand_registry.py`. [why](docs/RULE_EVIDENCE.md#brand-template-never-reached-the-run)

**A project that names no brand template gets NOTHING, and every slot's reading of that absence is written down.**
**A project's own declarations reach every step through `state["project_config"]`.** `library/tools/brand_registry.py`. [why](docs/RULE_EVIDENCE.md#a-template-nobody-chose)

**A brand's CONSTRAINTS reach three planning steps, and a step has two names.** `library/tools/brand_registry.py`. [why](docs/RULE_EVIDENCE.md#the-brand-reached-no-planning-step)

**The captain's creative brief is one per-project declaration that reaches NINE steps, BY REFERENCE, WHEN THE PROJECT ATTACHES IT.** **A run that attaches none INTERVIEWS rather than going quiet.** `library/tools/brief_reference.py`. [why](docs/RULE_EVIDENCE.md#the-brief-is-paid-seven-times)

**A reference is an ABSOLUTE PATH plus a MAP.** `library/tools/brief_reference.py`. [why](docs/RULE_EVIDENCE.md#the-brief-was-copied-seven-times)

**A step may carry ONE reading of a measurement, or two on different axes - never the reading and the structure it was read from.** `library/tools/footage_reference.py`. [why](docs/RULE_EVIDENCE.md#three-views-of-one-analysis)

**Every LLM step declares `context_fields`, at the manifest's TOP LEVEL, and one declared where nothing reads it is REFUSED.** `library/tools/context_projector.py`, `library/tools/context_views.py`. [why](docs/RULE_EVIDENCE.md#the-declaration-nothing-read)

**A step's decision must be SOURCED from its own context.** `library/tools/replay_bench/bench.py`. [why](docs/RULE_EVIDENCE.md#the-decision-that-was-remembered-not-sourced)

**A table the prompt names, arriving with zero rows, is reported on the run that sends it.** `library/tools/context_views.py`. [why](docs/RULE_EVIDENCE.md#a-prompt-that-described-an-empty-table)

**Word timings do not reach a prompt, and what a step cannot select by NAME it selects with a named VIEW.** `library/tools/context_views.py`. [why](docs/RULE_EVIDENCE.md#the-transcript-arrived-with-every-word)

**Never send a summary and the structure it was rendered from.** `library/tools/vision_schema_adapter.py`. [why](docs/RULE_EVIDENCE.md#the-summary-and-its-own-source)

**No raw value list reaches a prompt.** `library/tools/beat_grid.py`. [why](docs/RULE_EVIDENCE.md#the-beat-grid-in-the-prompt)

**A step that chooses a picture is SHOWN one, and so is one that judges a RENDER.** **What a render SAYS is HEARD against the plan.** `library/tools/window_frames.py`, `library/tools/render_watch.py`, `library/tools/reel_hearing.py` - the last two REPORT, never gate. [why](docs/RULE_EVIDENCE.md#no-step-that-chose-a-picture-had-seen-one) [why](docs/RULE_EVIDENCE.md#caption-shipped-late)

**A TOON table cell is quoted with a BACKTICK, and a multi-line value under a KEY is a `|` block.** `library/tools/toon_serializer.py`. [why](docs/RULE_EVIDENCE.md#the-apostrophe-was-doubled-in-every-prompt)

**A step reads only the keys the PRODUCING step is asked for, and `creative_direction` is the enumeration that proves it.** `library/tools/creative_direction.py`. [why](docs/RULE_EVIDENCE.md#seven-reads-of-a-key-that-cannot-exist)

**A call with nothing to ask is not made.** `library/processes/edit_video/run_pipeline.py`. [why](docs/RULE_EVIDENCE.md#thirty-three-thousand-tokens-for-three-bytes)

**The delivery format is a property of the PRODUCT, not of the footage.**
Detail: `library/tools/delivery_format.py`. [why](docs/RULE_EVIDENCE.md#delivery-format-is-not-the-source-resolution)

**The beat grid is `tempo.beats` / `tempo.downbeats`, and it does not start at zero.**
Detail: `library/tools/beat_grid.py`. [why](docs/RULE_EVIDENCE.md#the-beat-grid-does-not-start-at-zero)

**`target_energy` has ONE reading.** `library/tools/energy_reading.py`. [why](docs/RULE_EVIDENCE.md#building-is-not-high)

**`music_behavior` has ONE vocabulary.** **The timeline's length comes from the spine, never from a passage's `end_time`.**
Detail: `library/tools/music_behavior.py`. [why](docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary)

### 10.2 Reaching the picture and the sound

**A capability is only real where the renderer reads it.**
Detail: `library/tools/execution/apply_fusion_comps.py`. [why](docs/RULE_EVIDENCE.md#unread-parameter-names)

**An empty VFX plan says WHY it is empty, and step 4.03 CAN produce a non-empty one.** `library/tools/vfx_plan_basis.py`.

**An overlay that draws nothing is not rendered.** `library/tools/motion_graphics_plan.py`. [why](docs/RULE_EVIDENCE.md#overlays-that-draw-nothing)

**An overlay segment the manifest names and disk does not have REFUSES the compile.**
Detail: `library/steps/step_5_04_compile_manifest/step.py`. [why](docs/RULE_EVIDENCE.md#the-motion-graphics-that-were-planned-and-absent)

**A MODEL plans the motion-graphics layer, on its own timebase, in rows - and a brand template REFINES it rather than gating it.**
Detail: `library/tools/motion_graphics_plan.py`.

**An overlay artefact is a TIGHT canvas at the 480 floor, and the placer SETS its transform then READS IT BACK - inside the 3840 rail or reported by name.**
Detail: `library/tools/tight_box.py`, `library/tools/overlay_placement.py`. [why](docs/RULE_EVIDENCE.md#the-captions-at-the-clamp)

**A project may caption each speaker differently, and the engine declares no per-speaker values.** `library/tools/subtitle_style.py`.

**A rendered subtitle segment is named for its speaker, timeline and source audio span.** `library/tools/subtitle_segment_id.py`.

**Manifest validation has a semantic half.** `library/tools/manifest_validator.py`. [why](docs/RULE_EVIDENCE.md#manifest-validator-semantic-half)

**Every frame of the timeline must show a clip.** `library/steps/step_5_04_compile_manifest/step.py`. [why](docs/RULE_EVIDENCE.md#undeclared-black)

**Overlay geometry comes from the safe area, and captions are grouped by measured pixels.**
Detail: `library/tools/safe_area.py`. [why](docs/RULE_EVIDENCE.md#safe-area-and-the-caption-grouper)

**The caption row is DECLARED, or `CAPTION_LIFT_PX` above the safe-area inset.**
Detail: `library/tools/subtitle_style.py`. [why](docs/RULE_EVIDENCE.md#the-caption-lift-is-eleven-pixels)

**Reconstruct a grouping with `fits_in_box`, never `fits`.** `library/tools/safe_area.py`. [why](docs/RULE_EVIDENCE.md#the-caption-grouping-reconstruction-used-the-wrong-predicate)

**A project may typeset its own captions, and that is not a change to anyone else's.**
Detail: `library/tools/subtitle_style.py`. [why](docs/RULE_EVIDENCE.md#the-caption-size-that-governs-one-video)

### 10.3 Measuring the footage

**Subject position comes from `face_center_x`, not from the vision pass.**
Detail: `library/tools/subject_framing.py`. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)

**A crop must be wide enough for the subject, and aiming it is not enough.**
Detail: `library/tools/subject_framing.py`. [why](docs/RULE_EVIDENCE.md#the-crop-was-narrower-than-the-face)

**Face frames are sampled at the CLIP'S OWN aspect, never a fixed shape.**
Detail: `library/steps/step_1_04_temporal_index/step.py`. [why](docs/RULE_EVIDENCE.md#the-squashed-face-frame)

**The frame FILLS by default, and there is no heuristic.** **A framing DECLARATION is not a framing DELIVERED, and the manifest records both.**
Detail: `library/tools/framing_intent.py`. [why](docs/RULE_EVIDENCE.md#the-letterbox-default)

**A project may DECLARE where its footage lives, and a bad declaration is refused.** `library/tools/footage_identity.py`.

**A file on disk is not a measurement.** `library/tools/analysis/speech_advanced_pipeline.py`. [why](docs/RULE_EVIDENCE.md#hollow-prosody-files-cached)

**Never invoke `step_1_03_semantic_analysis/step.py` against a real project to test it.**
Detail: `library/steps/step_1_03_semantic_analysis/step.py`. [why](docs/RULE_EVIDENCE.md#semantic-analysis-triggers-a-vision-run)

**`usable_ranges` is a measurement, and an absent one is EMPTY - never the whole clip.**
Detail: `library/tools/analysis/picture_quality.py`. [why](docs/RULE_EVIDENCE.md#usable-ranges-were-the-whole-clip)

**Camera steadiness has ONE reading, and it says which signal answered.**
Detail: `library/tools/camera_stability.py`. [why](docs/RULE_EVIDENCE.md#the-residual-nobody-read)

**No assessment field reports a default as though it were measured. That is the whole rule, and it holds for every field.**
Detail: `tests/test_assessment_reports_no_default_as_measured.py`. [why](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)

### 10.4 Gates, and what counts as evidence

**A reel is BUILT only once the captain approves it, and PROPOSED fails that gate as REJECTED does.** `library/tools/reel_proposal.py`; its four qualities: `library/tools/reel_quality_bar.py`.

**Reaching the ENGINE is not reaching the REELS until MEASURED.** `library/tools/reel_divergence.py`. [why](docs/RULE_EVIDENCE.md#logo-on-one-reel)

**A replace is a diff: promotion refuses an undeclared row loss.** `library/tools/reel_replace_guard.py`. [why](docs/RULE_EVIDENCE.md#the-promote-that-never-looked-back)

**A version is a ROUND; a BUILT reel carries a durable sign-off; promotion DELETES unless retained, and `round-diff` compares two rounds.** One version model, `library/tools/versions/`; `reel_signoff.py`, `reel_retirement.py`.

**TWO versions of a reel may be alive at once, and CHOOSING one is an ACT: `manage_project.py variant new|build|list|diff|choose|merge`.** A variant differs in a SEAM or a per-project DECLARATION and in nothing else; the archive holds ONE unchosen variant per REEL. `library/tools/versions/variants.py`.

**Passage engagement is a JUDGEMENT the model writes, it is an ORDERING, and there is NO SCORE.** `library/tools/passage_engagement.py`, `tests/test_passage_engagement.py`. [why](docs/RULE_EVIDENCE.md#every-line-scored-the-same)

**A recommendation is APPLICABLE where it is made, or it is an OBSERVATION that names who owns it.** `library/tools/cohesion_scope.py`. [why](docs/RULE_EVIDENCE.md#the-review-recommended-what-it-could-not-do)

**The default answer to "should this have a test" is NO: a test earns its place by naming a defect; expensive tests justify their cost.** A gate that cannot fail is worse than no gate, because it reads as coverage; a SKIPPED test names a running environment, and a test body must be able to fail. Detail: `docs/TEST_AUTHORING.md`. [why](docs/RULE_EVIDENCE.md#gates-that-cannot-fail)

**A gate that FAILS correct output is no more coverage than one that cannot fail.** `library/tools/pipeline_skills.py`. [why](docs/RULE_EVIDENCE.md#gates-that-fail-correct-output)

**Seven baseline-craft properties are checked on every build, and two of them deliberately do not fail.**
`render_qa.py` measures the RENDER: [why](docs/RULE_EVIDENCE.md#a-dim-shot-is-not-a-letterbox-bar)
`manifest_validator.py` checks the PLAN. [why](docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line)
Chroma and the mix REPORT A NUMBER and pass. [why](docs/RULE_EVIDENCE.md#baseline-craft-properties)

**The bed is fitted at the SECTION that plays, and the offset is a REQUIRED argument.** `library/tools/render_qa.py`. [why](docs/RULE_EVIDENCE.md#the-bed-was-fitted-from-the-wrong-second)

**The bed is bounded by the PICTURE, not by V1.** `library/steps/step_5_04_compile_manifest/step.py`. [why](docs/RULE_EVIDENCE.md#the-bed-was-trimmed-to-the-last-v1-clip)

**The bed's own measurements reach the mix.** `library/tools/music_measurement.py`.

**Every QA finding has a reader, and one that has none is reported.** `library/tools/qa_findings.py`. [why](docs/RULE_EVIDENCE.md#the-qa-report-had-no-reader)

**The rough-cut review's own answer has a reader, and it has two halves.** `library/tools/cut_verdicts.py`. [why](docs/RULE_EVIDENCE.md#the-review-answered-and-nobody-read-it)

### 10.5 Creative latitude

**The pipeline never invents a creative judgement on the model's behalf.**
A CREATIVE fallback substitutes taste (a mood, a theme, a transition, an effect, a sound, an energy word) and it goes. A MECHANICAL default is a safe technical value (a frame rate, a timeout, a codec) and it stays. Where a creative value is genuinely absent, FAIL or REPORT PLAINLY. [why](docs/RULE_EVIDENCE.md#the-pipeline-invented-taste-where-no-step-ran)

**A creative value is DECIDED, and the decision SAYS what decided it.**
A stated preference, then the declared direction, then the model reasoning over MEASURED signal, then a fallback naming WHOSE preference it is.
Detail: `library/tools/decided_value.py`. [why](docs/CREATIVE_VALUE_DECISION.md)

- Two things are NOT taste: "nothing is drawn" (`transition_vocabulary.CUT_TYPES`, `series_look.NEUTRAL_CDL`), and a rule acting on a value the direction really DECLARED (`creative_cohesion`). Detail: `library/tools/creative_floors.py`.
- A plan entry that names no effect, no sound or no level is DROPPED with the reason. Never completed from a constant, in a bridge or in `compile_manifest`.
- **How strong an effect is is the PLAN's number, not a scale the engine offers.** Step 4.03's `INTENSITY_MAP` is REMOVED (captain, 2026-09-02) and `plan_vfx.TOOLKIT_PARAMETERS` replaces it, carrying NO value, default or bound. An entry whose `params` name none of them is dropped as `no_readable_parameters`. Detail: `library/steps/step_4_03_plan_vfx/post_bridge.py`.
- An alias may RENAME a capability and may not CHOOSE one (`library/steps/step_4_03_plan_vfx/post_bridge.py`).
- Dead code that states taste is removed, not left.

**There are NO creative floors, and there must not be again.**
Detail: `tests/test_no_creative_floors.py`. [why](docs/RULE_EVIDENCE.md#no-creative-floors)

**Sound-effect selection is one enumeration, `library/tools/sfx_library.py`, and the model names a FILE.**
[why](docs/RULE_EVIDENCE.md#the-sfx-chooser-was-a-word-list)

**Music selection is one enumeration, `library/tools/music_selection_contract.py`.**

**Nothing refuses a track on rights, and no rights model may be built.**
Detail: `library/tools/music_search.py`.

**Search is one enumeration, `library/tools/music_search.py`, and it runs by default.**
[why](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)

**Two candidates that are the same recording are established from the MEASUREMENTS, never the filename.**
Detail: `library/tools/music_duplicates.py`. [why](docs/RULE_EVIDENCE.md#a-third-of-the-choice-set-was-a-copy)

**Which SECTION of the track plays is the model's decision, and there is no best-section rule.**
Detail: `library/tools/music_section.py`. [why](docs/RULE_EVIDENCE.md#the-splices-that-reached-nothing)

**The bed is a SEQUENCE, not one continuous minute of one track.**
Detail: `library/tools/music_bed.py`.

**The envelope is measured for the sections the model SHORTLISTS, in a second pass.**
Detail: `library/tools/second_pass.py`.

**Which SECONDS of a chosen cutaway play is decided from the PICTURE, never from its audio.**
Detail: `library/tools/cutaway_window.py`. [why](docs/RULE_EVIDENCE.md#the-cutaway-window-came-from-a-muted-waveform)

**Every candidate is MEASURED, and nothing about it is classified.**
Detail: `library/tools/music_measurement.py`. [why](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)

## 11. Third-Party Asset Licenses

**Anything added to `library/presets/` from an outside source needs its licence recorded here before it lands.**

**A PowerGrade lands only with its provenance recorded here.**
Captain's ruling 2026-09-10: `The Grade Free_1.13.1.drx` (captain-supplied, Zay's free grade) is authorised; no file ships in this repo. `library/tools/color_page_grade.py`; `tests/test_color_page_grade.py`. [why](docs/RULE_EVIDENCE.md#the-unlicensed-powergrade)

**Two third-party assets ship, with their licences.**
Montserrat, as `remotion-subtitles/public/fonts/Montserrat-Variable.ttf` (variable 100-900), under **SIL Open Font License 1.1** (`public/fonts/OFL-Montserrat.txt`). GSAP, as `hyperframes/vendor/gsap.min.js` (3.14.2, pinned), under the **GSAP Standard License** (free; header in the file).

- **Bundle fonts; never import one over HTTP.** [why](docs/RULE_EVIDENCE.md#the-webfont-race)
- `tests/test_bundled_fonts.py` fails if the font or licence goes missing, a font arrives over HTTP, or a template names one neither bundled nor accepted as system.

**A declared typeface must be one that really draws the glyphs.**
One enumeration, `library/tools/render_fonts.py` - bundled, accepted as a system font, or carried by the project as a `font_file`.

## 12. The look

**There is no house look**, and the slot is `style.series_look`.
Detail: `library/tools/series_look.py`. [why](docs/RULE_EVIDENCE.md#there-is-no-house-look)

**A project that names no template still gets a GRADE, because a colourist decides one.**
Detail: `library/tools/color_correction.py`. [why](docs/RULE_EVIDENCE.md#the-step-that-measured-nine-clips-and-graded-none)

**A grade DELIVERS or it does not, and only EXPORTED PIXELS say which.**
Detail: `library/tools/color_page_grade.py`. [why](docs/RULE_EVIDENCE.md#the-powergrade-with-no-grade-in-it)

## 13. Intros, outros and end cards

One enumeration, `library/tools/bookends.py`.

- A brand template declares `content.bookends`; a template that declares nothing gets nothing.
- A declaration names either an `asset` that already exists or a `composition` to render, plus a `duration_seconds`. **A malformed declaration raises rather than being dropped.** [why](docs/RULE_EVIDENCE.md#bookends-only-on-some-videos)
- **A card takes the same path as every other clip.** `mesh_spine` turns each declaration into an `intro_card`/`outro_card`/`end_card` spine block (NOT `intro`/`outro`, which already mean a non-speech pacing beat).
- **A card the PLAN wrote REFUSES the step, by name.** `bookends.assert_no_invented_bookends` raises `InventedBookendBlock`. `intro` and `outro` are NOT cards and are never dropped. [why](docs/RULE_EVIDENCE.md#the-card-that-vanished-into-a-log-line) Same shape as `UnplayableSfxPlan` in 4.04 (§10.5).
- Never append a card out of band after compilation.
- Project-owned compositions are staged **verbatim** by `bookend_render.py` into gitignored build output. The engine renders a client's asset; it never edits one.

## 14. General assets vs project assets

`docs/ASSET_LIBRARY_PLAN.md` is the standing test.
Three questions, and an asset must pass all three to live in the engine - failing any one is sufficient to stop it:

1. **Substitution**: does it survive being handed another series' content, or does it encode one series' copy, palette or typeface?
2. **Timing and geometry**: is it anchored to a spine block and to the picture the delivery format actually produces, or to absolute frames and a full-bleed frame nothing renders?
3. **Reader**: does a step read it, and does a test assert the picture changes?

Question one decides where it lives; two and three decide whether it is finished. [why](docs/RULE_EVIDENCE.md#the-4th-wall-end-card)

- **A brand template may set per-series PARAMETERS and may not contain ARTWORK**: no on-screen copy, no coordinates or frames describing one finished episode.
- Artwork is a project asset, declared by reference through `content.bookends` and staged verbatim (§13).
- **Per-series typefaces live per project, not in the engine.** Font files and their licences belong with the project that owns the series; the engine stays series-neutral.
- **Series-shared masters live in their own store.** `library/tools/brand_library.py`; `docs/BRAND_LIBRARY_STORE.md`. [why](docs/RULE_EVIDENCE.md#unversioned-brand-masters)

**Timed text is artwork, and the project wins.**
A timed text moment is copy the viewer reads, so it is ARTWORK and belongs with the project - which is what makes a timed-text declaration legal at all under question three.
One enumeration, `library/tools/timed_text_overlay.py`.

- A moment is timed from the SPINE (`block` + `anchor` + `offset_seconds` + `duration_seconds`). Absolute `start_frame` stays available and is bounded by the spine's real length, and a malformed declaration raises.
- Moments whose spans touch are grouped into one rendered segment.
- `timed_text_render.py` renders each inside step 4.06; `resolve_build_timeline` places them on **V6**.
- A project's `project.yaml` may carry its own `effect.timed_text_overlay`, replacing the brand template's whole slot. `timed_text_overlay.resolve_declaration` is that half.
- **The card states its own `y`** (normalised against the delivery frame). Keep `y` in 0.35..0.65. [why](docs/RULE_EVIDENCE.md#the-night-card-y-band)

## 15. Notes the captain types onto the timeline

**inside DaVinci Resolve**
Detail: `library/tools/marker_feedback.py`.

**To read a reel, call `library/tools/reel_read.py` - never a new probe.** [why](docs/RULE_EVIDENCE.md#the-probe-that-missed-the-clip-markers)

### Where a note goes

**A collected note is routed to the step that owns its decision; an
ambiguous one is reported, not sent somewhere.**
Detail: `library/tools/marker_routing.py`.

### What decided the clip the note is on

**Every clip on the built timeline carries the decision that produced it, and the routing
reads that instead of inferring - but only where inference has nothing.**
Detail: `library/tools/timeline_decisions.py`.

### The button that captures the frame

**Capture the frame the captain is looking at, onto the marker there.**
Detail: `library/tools/marker_capture.py`.

**A note's identity is its REEL and its WORDS, outliving the rebuild.**
`library/tools/feedback_ledger.py`.

### What goes in `customData`

One enumeration, `library/tools/marker_payload.py`.

## 16. Motion graphics

**The motion-graphics elements this pipeline may plan are one enumeration, `library/tools/motion_graphics_vocabulary.py`, and it defines AXES rather than values.** [why](docs/RULE_EVIDENCE.md#the-roster-nobody-wrote-down)

- **An entry names a dimension; the magnitude belongs to whoever declares it.** `AXES` is the vocabulary of dimensions and no axis has a default or a bound. `colour_role` is a role of the declaring palette, never a colour; `type_role` is a weight, never a size.
- **Reachability is REPORTED per entry, never a filter on membership.** Read the counts off the roster; one written around today's renderer would keep its defect after the repair.
- **`never` is not optional.** An entry that only says what a thing is teaches a model to reach for it everywhere, so every entry records refusals and `assert_roster_is_well_formed` raises without them.
- **The boundary is the whole point.** `OUT_OF_VOCABULARY` names the module that owns each near miss. An element is in this roster when it is an ADDITIVE OVERLAY carrying meaning the picture and the captions do not already carry; one that REPLACES it is `library/tools/full_frame_element.py` ([why](docs/FULL_FRAME_ELEMENTS.md)).
- **Nothing here is keyed to one identity**, because the engine serves a daily channel and client work (§14). `channel_bug` draws a project-supplied asset; the engine ships no artwork and states none.
- **Two neighbouring decisions are the captain's and this file must not take either**: what produces the COPY a graphic shows, and whether the model authors a component or fills a props schema. An entry declares only WHETHER it needs a text payload. `COPY_SOURCE_IS_UNSET` records both; a change that would force one is a stop, not an implication.
- `roster_rows()` and `ROSTER_LEGEND` are the prompt-side route - a definition beside the table, not in 4.06's prose. The whole roster ships - nothing is shortlisted, because whatever selects a shortlist becomes the chooser (§10.5). **Step 4.06's bridge is the consumer**, and `motion_graphics_plan.DRAWABLE` is DERIVED from the `reachable` column rather than listed twice (§10.2).
- **An ANIMATED EXPLAINER is a staged plan over this roster, timed to the WORDS that say each stage.** `library/tools/explainer_plan.py`. [why](docs/ANIMATED_EXPLAINER.md)
- `tests/test_motion_graphics_vocabulary.py`.
## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

When a rule comes from an incident, state the rule here and put the incident in `docs/RULE_EVIDENCE.md` with a `[why]` link.
A reader should be able to obey every rule in this file without opening that one.
