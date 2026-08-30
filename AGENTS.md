# Video Editing Pipeline

You are an autonomous agent operating the video editing pipeline.
This manual explains how to navigate the repository, run the pipeline, and make editorial decisions.

## How to read this file

Everything under a numbered section is a **rule**: something to do, or not do, when you touch this pipeline.
Rules are stated as rules, in the imperative, and they do not carry their own history.

The history is kept, and it matters, but it is not on the reading path.
The incident, measurement, date, crash log or diagnostic path behind a rule lives in [`docs/RULE_EVIDENCE.md`](docs/RULE_EVIDENCE.md), reachable from the rule as a `[why]` link.
Read a `[why]` when you want to overturn a rule, or when you need the numbers.
You never need one in order to obey a rule.

Before your first run, read §9 (environment), §3 (how a run works) and §5 (DaVinci Resolve).

Section numbers are cited from code comments and other docs, so they are stable.
Change what is in a section, not its number.

## 1. Identity and purpose

This repository is the engine for an automated video editing pipeline.
It takes raw footage, analyzes it, and generates a fully assembled DaVinci Resolve timeline: shortform vertical video with subtitles, B-roll, SFX and music.
The engine stores no project data - assets and pipeline outputs live in isolated directories outside the repository.
Agents and human editors use it to automate the tedious parts of video assembly while retaining creative control.

## 2. Repo layout

- `library/`: core pipeline implementation and shared Python modules.
- `library/processes/`: complete pipelines such as `edit_video`, with its `dag.json` and `manifest.json`.
- `library/steps/`: individual pipeline steps, named by phase (e.g. `step_1_01_scan_project`).
- `library/tools/`: shared utilities for Resolve scripting, vision analysis and file management.
- `library/schemas/`: Pydantic schemas for pipeline state and project configuration.
- `library/dashboard/`: FastAPI server for the human-in-the-loop review dashboard.
- `library/tools/panel/`: the Resolve panel's logic, with no Qt and no Resolve in it (§15).
- `library/templates/`: brand templates defining styles, effects and content rules.
- `library/profiles/`: declared run configurations - which steps a run fires and where it stops (§3).
- `library/presets/`: Fusion macros and DaVinci's own built-in effect settings. Whatever reaches a timeline is found by direct path; there is no preset index.
- `remotion-subtitles/`: Node.js React app that renders subtitle overlays.
- `scripts/`: bash helpers for environment setup and maintenance.
- `resolve_scripts/`: the entry points DaVinci Resolve's Workspace > Scripts menu calls - the capture button (§15) and the pipeline panel. Installed by `scripts/install_resolve_scripts.sh`.
- `tests/`: unit and integration tests for the engine.
- `manage_project.py`: top-level CLI for creating, listing and running projects.
- `requirements.txt`: Python dependencies.

**One thing in `library/tools/` is a prototype: it is in the DASHBOARD and stays out of the PIPELINE.**
`footage_query.py` / `footage_segments.py` / `footage_query_bridge.py` are a cross-clip footage
search - "where in all my footage does X happen".
A PERSON may call it: `library/dashboard/footage_search.py` serves the dashboard's Footage Search
view, and that is the only authorised caller.
A STEP may not: do not import either module from a step, the DAG or a manifest.
`tests/test_footage_query_prototype.py` fails if you do, and widening it further is the captain's
call - stated the way the dashboard carve-out was, by naming which half is being authorised.
[`docs/FOOTAGE_INDEX_PROTOTYPE.md`](docs/FOOTAGE_INDEX_PROTOTYPE.md) has what it measured, the
measured score floor that lets it answer "not in this footage", and the 66x reduction of
`temporal_index` that is worth doing without it.

## 3. Pipeline execution

The pipeline is a Directed Acyclic Graph (DAG) in `library/processes/edit_video/dag.json`, ordered by topological sort.
It groups 26 atomic steps into phases: 0 setup, PREFLIGHT analysis, 2 planning, 3 assembly, 4 post-production, 5 finishing/QA, 6 rendering. A default run does 25 of them - see "Scoping a run" below.

- Call the analysis stage **preflight**, never "phase 1", even though its step ids read `step_1_0X_*`. `docs/PIPELINE_PLAN.md` uses Phase 0/1/2 for the quality-work programme and the two names collided.
- `library/steps/` holds 28 step definitions. **TWO exist and are not wired into the DAG.** Each carries a documented `unwired_reason` in `project_layout.STEPS`, `StepDir.__post_init__` rejects `wired=False` without one, and `tests/test_step_dag_coverage.py` fails if a step directory exists with no DAG node and no unwired declaration. Both have been MEASURED and both stay unwired: `object_segmentation` (1.06) - nothing consumes masks, and [`docs/SUBJECT_MASKING_MEASURED.md`](docs/SUBJECT_MASKING_MEASURED.md) is what SAM 2.1 cost and what its masks are good for on 001; `prosody_analysis` (1.05) - nothing declares its output since #F5, and [`docs/PROSODY_MEASURED.md`](docs/PROSODY_MEASURED.md) is what it would have measured against what `temporal_index` already answers at 100% coverage. **Unwiring says nothing consumes it, not that the capability is gone**: 1.05's step directory, `speech_advanced_pipeline.py`, `prosody_profile.py` and `view:prosody` all stay. [why](docs/RULE_EVIDENCE.md#unwired-steps-need-a-reason)
- **UNWIRED and DESELECTED are different things, and only one is a property of the pipeline.** Unwired means no DAG node exists (1.05, 1.06). Deselected means the node exists and a RUN declined it - `ocr_extraction` (1.07), off by default under #245. The two lists are `project_layout.STEPS` and `run_scope.DESELECTED_BY_DEFAULT`; a step is in one or the other, never both.
- `objects[].readable_text` in semantic analysis output is the VLM's field - step 1.03 prompts for it directly. The local model (`gemma-4-12b-it-4bit`) reads on-screen text **sparsely, not never**; a recorded claim that it "provably cannot" read text was wrong. Step 1.07 (OCR) writes to a SEPARATE output key (`ocr_extraction`), not to `readable_text`. The two are independent: improving the VLM's text reading and selecting the OCR step are separate improvements, not the same fix. #162 tracks the design question of what consumes masks and OCR output. [why - both measured on 001](docs/RULE_EVIDENCE.md#unwired-steps-need-a-reason)
- Step 1.07 `ocr_extraction` is WIRED and DESELECTED BY DEFAULT (#245): `--with ocr_extraction` turns it on. It finds real text the VLM returned null for, at a measured 23% signal-to-noise. [why - the 001 measurement in full](docs/RULE_EVIDENCE.md#unwired-steps-need-a-reason)

Run with `python3 manage_project.py run <slug>`:

| Flag | Effect |
|---|---|
| `--from <step_id>` | resume from a step |
| `--step <step_id>` | run one isolated step |
| `--auto` | auto-complete hybrid steps from bridge output instead of pausing for LLM input |
| `--review` | enable review gates that pause for human inspection on the dashboard |
| `--resume` | continue after a review gate is approved or revised |
| `--dry-run` | print the execution plan without running steps |
| `--rerun <target>` | redo finished work; repeatable, and the ONLY supported way to re-run a completed step |
| `--target <name>` | run only what a named destination needs |
| `--only <step_id>` | run this step and whatever it cannot run without; repeatable |
| `--skip <step_id>` | leave a step out; repeatable |
| `--with <step_id>` | turn on a step that is off by default; repeatable |
| `--profile <name>` | run under a DECLARED run configuration; `none` declines the one the project adopts |
| `--break <step_id>` | stop after this step for review; repeatable, `*` means every step |
| `--no-break <step_id>` | do not stop after this step; repeatable, `*` disarms every breakpoint |

### Scoping a run

One enumeration, `library/tools/run_scope.py`, and both CLIs register its flags from it.

- **A selection is resolved against the DAG before the run starts, or refused.** A selection that strands a consumer names the consumer, the producer and the missing output keys, and exits 2 having written nothing. [why - the run that died forty minutes in](docs/RULE_EVIDENCE.md#a-selection-that-died-forty-minutes-in)
- **A prerequisite is a condition on STATE, not on lineage.** `run_scope.Prerequisite` is one required KEY, and the resolver asks whether that key exists by any of three means: a step in this run makes it, a previous run recorded it, or it was supplied from outside and CHECKED. Which step would normally make it is one of the three answers, not the question. [why](docs/RULE_EVIDENCE.md#a-prerequisite-is-a-statement-about-state)
- **An edge is HARD when it carries a key the consumer does not declare optional** - the same condition `gather_step_inputs` raises on. Soft parents are not pulled in by a target.
- **Excluding a producer REFUSES its consumers; it never drops them silently.** There is no "let downstream cope": a required input has no absent-value code path (section 10.1). Say "I just want the rough cut" by naming a GOAL, not by excluding twelve steps.
- **A recorded output satisfies an excluded dependency** - ledger entry, a `step_outputs` value, AND the KEY inside it. All three, because `gather_step_inputs` raises on the key: a step that finished and recorded something else is not a step that recorded this. That is what makes a scoped re-run fast. A `--rerun` target is about to be discarded, so it satisfies nothing.
- **A recorded output does not remove a step from the run; a SUPPLIED one does.** History is not a request. The captain putting a value under `external/` is saying "do not make this", so the closure stops at that producer.
- **A target names its GOAL steps and nothing else.** The step list is walked off the DAG every run, so inserting a step upstream keeps the target right without anybody editing it. `rough_cut_subtitles` is the one target; add another only on evidence.
- `--step` and `--from` narrow the scope further and behave exactly as they always have. `--step <id>` names a step outright and outranks the default-off list.
- **A step that is off by default is reported on every run**, including a plain full one, and is not counted as never-completed - a step that exists and silently never runs is the trap this file's step-directory check exists to stop.
- `tests/test_run_scope.py`.

### Configuring a run

**A run shape is DECLARED as data, and it has no power `run_scope` does not already have.**
One enumeration, `library/tools/run_profile.py`. [why - a run shape used to be a code change, and what driving the gate loop on 001 found](docs/RULE_EVIDENCE.md#a-run-shape-was-a-code-change)

- A profile carries `goals` (or a built-in `target`), `skip`, `with` and `breakpoints`, and hands the first four to `run_scope.resolve` as a `Selection`. **The dependency refusal, the hard/soft edge derivation and the pre-run failure all apply unchanged**; `tests/test_run_profile.py` asserts a declared profile and the equivalent flags produce the SAME refusal string.
- **Two directories, and a name resolves in the project first**: `<project>/profiles/<name>.yaml` (`Kind.INPUT`, the captain's) shadows `library/profiles/<name>.yaml` (the engine's), and the run header says which file answered. There is no `extends:` - the three layers that compose are engine profile -> a project ADOPTS it (`pipeline.run_profile`) -> one run OVERRIDES it.
- **Naming a step on the command line outranks the profile.** `--skip`/`--with` ADD to what it said; `--target`/`--only` REPLACE its goals; `--only`/`--with` take a step OUT of its skip list. `--skip X --only X` is still the contradiction `run_scope` refuses.
- **A profile is refused by name** for an unknown key, an unknown step, an unknown target, no `description`, a `name` disagreeing with its filename, or declaring both `target` and `goals`.
- YAML, because a profile that leaves a step out wants to say WHY on the line above it.

**A breakpoint is armed PER STEP, and `--review` is the every-step case.**
One enumeration, `library/tools/breakpoints.py`. The gate machinery is unchanged - `review_gate.py` still writes the snapshot and still takes approve/reject/revise; this is the selector it never had.

- **An unreachable breakpoint is NAMED, never refused.** A breakpoint strands no consumer, so refusing would make `--profile podcast --only catalog` impossible for no gain; the header says "armed at X, which this run does not run - it will NOT stop there" before the run starts, and `pipeline_run.json` records it.
- An unknown step id, or a step both `--break` and `--no-break`, IS refused by name.
- **Arming a gate makes it `pending` and throws away the previous run's answer.** A gate that pauses is by definition unanswered.
- **The pause prints the command that answers it and the command that carries on**, and the resume command DROPS `--rerun` (`breakpoints._NOT_CARRIED`): carrying it would clear the ledger entry the pause just wrote and stop in the same place forever.
- Answer one without a browser: `python3 -m library.tools.review_gate list|show|answer`.
- `tests/test_run_profile.py`, `tests/test_breakpoints.py`, `tests/test_run_configuration_end_to_end.py`.

### State the pipeline did not produce

**A prerequisite may be satisfied from outside the pipeline, and it is CHECKED, never asserted.**
One enumeration, `library/tools/external_inputs.py`. [why - what was measured, and what could not honestly be checked](docs/RULE_EVIDENCE.md#a-prerequisite-is-a-statement-about-state)

- The value is SUPPLIED, in `<project>/external/<state_key>.json` carrying `key`, `source` and `value` - not claimed by a flag. The same verified value is what `gather_step_inputs` hands the step, so the resolver can never believe something the run cannot use.
- **The file is named for the STATE key, which is the PRODUCER's name for it.** Step 6.01 records `render_output`; step 6.02 calls the same value `rendered_output`. Offering the consumer's name is refused, naming the producer's.
- **`CHECKS` is the whole of what can be supplied. A key that is not in it is refused by name**, because a check that does not exist is not a check that passes. Today: `a_roll_assignments` (every source file on disk, every range non-empty, and within the catalog's measured duration where the catalog is on file), `audio_spine` (`spine_contract`), `assembly_manifest` (`manifest_validator`, both halves, plus every placed clip resolving to a file) and `render_output` (ffprobe finds a video stream).
- `WITHDRAWN` records what cannot be asserted and why. **A Resolve timeline built by hand is not one of them**: it is not refusable at resolve time, so supply the artifact that describes it instead. Do not add a flag that believes a claim.
- `source` is RECORDED, never trusted. The verdict comes from the check.
- A file that does not check out REFUSES the run, before anything is deleted or written. It is not skipped.
- `tests/test_external_inputs.py`.

### A declaration must be true

**No step may declare an input required that nothing refuses on, or optional that its own code refuses without.**
`library/tools/input_contract.py` surveys all 144 declared inputs of the DAG's 26 steps and says, for each, WHO refuses when it is absent - the runner (edge-routed and required), the step (with a file and a line), or nobody.

    python3 -m library.tools.input_contract          # the survey
    python3 -m library.tools.input_contract --bad    # disagreements only

- **Enforcement is not warrant.** Establishing warrant means RUNNING the step without the input; `tests/test_compile_manifest_without_the_decoration.py` does that for every input of the one step that reads state directly instead of taking `gather_step_inputs`' word for it. [why - the four specs `compile_manifest` enforced perfectly and did not need](docs/RULE_EVIDENCE.md#a-prerequisite-is-a-statement-about-state)
- A required input the step nonetheless runs without is recorded in `REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT` with what would go silently missing - and the test checks the record BOTH ways, so an entry for an input that really refuses is stale and fails.
- The line is AGENTS.md section 10.5's: `[]` for transitions is the absence of decoration and is optional; `{}` for the audio mix is the spine's declared `music_behavior` going missing and is not.
- `UNCONSUMED_DECLARATIONS` records an input read by neither the step's code nor its prompt, still declared because unrouting it would leave a frozen `handoff.md` documenting a read that no longer happens. `UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT` is its MIRROR - the declaration has gone and the frozen line has stayed, because the two are owned by different people - and `creative_direction.prosody_analysis` is its one entry, held open until the captain rules on `step_2_01_creative_direction/handoff.md:127`. It fails from BOTH sides: an entry whose input is declared again is stale, and so is one whose handoff no longer names the key. Widening any of these tables is not a way to make the survey quiet.
- **A step with no `handoff.md` reaches no prompt, and its CODE is the only consumer it can have.** `step_has_a_prompt` asks `run_pipeline.get_step_implementation`, and `trace_step_values` then asks whether the value the key yields REACHES A USE - naming the key is not reading it. [why](docs/RULE_EVIDENCE.md#the-guard-that-could-not-see-a-deterministic-step)
- **That half REPORTS; it does not fail**, because escalating a pre-existing finding is the captain's call. `unread_by_a_prompt_less_step` is the report; `disagreements` is unchanged.
- **The value read is one-sided and says so.** `_UNTRACEABLE` is what it reads as USED rather than guessing about - anything but a plain function the step's own files define, an alias, a second hop. The survey prints that and its remaining blind spot (a step WITH a prompt is still judged on whether its code NAMES the key) on every run.
- `tests/test_input_declarations_are_true.py`.

Steps come in three implementation types:

- **Deterministic**: a `step.py`, run automatically over JSON stdin/stdout.
- **Hybrid**: a `bridge.py` that pre-computes context plus a `handoff.md` prompt for an LLM.
- **LLM-only**: only a `handoff.md`, generating the output from upstream context.

Pipeline state lives in `pipeline_data.json` at the root of each project directory.
It tracks completed steps in two ledgers and stores all JSON outputs under `step_outputs`.
Inspect it to debug data flow or verify upstream results.

### Two ledgers, two lifetimes

One enumeration: `library/tools/step_ledger.py`.

- Every step manifest declares `classification.stage`. An undeclared or unknown stage raises rather than defaulting.
- **preflight** is enrichment of THIS PROJECT'S SOURCE FOOTAGE - scan, catalog, vision, transcription, prosody, segmentation, OCR - recorded in `preflight_completed`.
- **edit** is everything downstream of a creative decision, recorded in `edit_completed`.
- `validate_sfx_library` (0.01) is edit: it validates a SHARED library, not this project's footage.
- `music_analysis` (2.06) is edit: it enriches a CHOSEN asset, and the choice is what an edit reset discards.

The two keys are separate, so `--rerun edit` is structurally incapable of discarding enrichment - it never names the other ledger. [why](docs/RULE_EVIDENCE.md#whisperx-paid-twice)

    manage_project.py run <slug> --rerun edit                     # reset the edit run
    manage_project.py run <slug> --rerun temporal_index           # one step
    manage_project.py run <slug> --rerun temporal_index:clip_007  # one clip of one step

- Per-clip granularity works because each step declares where its per-clip artifacts live, in `classification.per_clip_artifacts`. The runner deletes exactly those files and the step's own "already on disk?" check recomputes exactly that clip.
- A step that declares none is re-run whole. **Add a per-clip artifact and you must declare it**, or nothing can invalidate it.
- Preflight is skipped once done, and that is safe because identity is checked. `library/tools/footage_identity.py` fingerprints each clip by size plus a digest of its first and last mebibyte - not a whole-file hash and NOT mtime - against `source_fingerprints` in the state file. Replaced, removed or renumbered footage invalidates exactly its own cached analysis. [why](docs/RULE_EVIDENCE.md#fingerprint-not-mtime)
- **The identity check watches the FOOTAGE, not the CODE. A fix that adds a field to a preflight output is invisible to it**, so a cached step keeps answering in the old shape and every consumer reads the absence as a measurement rather than as staleness. Ask, of each cached preflight step, whether a change since its date altered what it EMITS - the ledger date against `git log` on the step's own directory - and `--rerun` the ones where it did. [why - the 17 index files with no `face_width`, and what that made inert](docs/RULE_EVIDENCE.md#a-preflight-cache-that-predates-its-own-fix)
- **A project's own declarations do NOT travel in a preflight cache.** `project_config` carries only `brand_registry.PROJECT_CONFIG_KEYS`, and `pipeline.framing_intent`, `pipeline.subtitle_typography` and the rest of the `pipeline:` block are read straight off `project.yaml` at the point of use, every run. Re-running `scan_project` neither refreshes them nor is needed to.
- Do not build a caching framework or a content-addressed artifact store. The mechanism is one declared field, one split ledger, one re-run flag, one identity check.
- The per-clip index lives with the PROJECT: it resolves from `project_folder`, not the runner's CWD, and reuses any per-clip file already there instead of re-transcribing it. [why](docs/RULE_EVIDENCE.md#per-clip-index-in-a-worktree)

### Run status

The run summary reports `SUCCESS` only when the whole DAG is complete and `failed_steps` is empty in the project ledger - not just the steps this invocation touched.

- `FAILED`: a step failed, or emitted an `available: false`/hollow result, in this run or an earlier one. Exit code 1.
- `AWAITING_LLM`, `PARTIAL` (`--step`/`--from`/a review-gate pause left DAG steps unrun), `DRY_RUN`.
- `failed_steps` is current state, not a log: a step that later succeeds is removed from it.
- **A recorded failure of a step this DAG no longer contains is REPORTED and does not decide the status.** An entry is cleared when that step SUCCEEDS, so a step with no node can never clear one - unwiring `prosody_analysis` left exactly that on 001. The runner partitions `failed_steps` against the DAG's nodes and names the stranded half first, saying no run can clear it. Never drop one: going quiet about a recorded failure is what `failed_steps` exists to prevent. `tests/test_a_stranded_failure_does_not_decide_the_status.py`.

## 4. Dashboard

Launch with `python3 manage_project.py dashboard <slug-or-path>`.
It needs none of the ML stack (§9), and a project kept outside `PIPELINE_PROJECTS_ROOT` is opened by passing its path.
**The picker lists the project the server is SERVING, first**, even when that project is outside the root - `/api/projects` otherwise enumerated only the root, named a different project as selected, and left no route back to the one on screen.
It serves a pipeline view, a footage library, a footage SEARCH, a transcript view and a timeline view, and it is the human-in-the-loop review layer.

- Review gates pause execution for inspection. Reviewers approve, reject or revise. A rejected gate halts the pipeline entirely; a revised gate applies the reviewer's modifications directly to the step output in `pipeline_data.json`.
- The dashboard also captures annotations and feedback as structured data for agent communication.
- **Extend this dashboard. Never author a fresh per-run review page.** [why](docs/RULE_EVIDENCE.md#review-surface-is-this-dashboard)

### Footage search

**Footage Search is a span search; Footage Library is a clip browser. Keep them apart.**
`library/dashboard/footage_search.py` is the dashboard's half of the footage-index prototype
(§2) - the ONE place authorised to call it.

- The index is built ONLY when the reviewer presses the button, into
  `<project>/pipeline_output/scratch/footage_index/`, and the view states that path first.
  Never build it at server start or on first query: both write into the captain's project
  without them asking.
- The embedding model is loaded once per server in a background thread; the banner reports
  `cold`/`loading`/`ready`/`unavailable` and names the backend, because a degraded backend
  must say so rather than quietly return worse results.
- **A ranking cannot say "not here", so there is a floor.** Retention is judged on the RAW
  dense cosine (`DENSE_SCORE_FLOOR`), never on the blended score, which is min-max normalised
  and therefore ~1.0 at the top for every query ever asked. Below it and above
  `DENSE_WEAK_FLOOR` is a WEAK band, reported as a near miss rather than as a result.
- Staleness compares an `ingest_fingerprint` recorded at build time. `pipeline_data.json` is
  fingerprinted by its `catalog` subtree only - the state writer rewrites the whole file after
  every step, so a downstream step landing is not a change to the footage.
- `tests/test_dashboard_footage_search.py`.

### The review return channel

**A note is anchored to a specific element, and the anchor is computed in the browser.**
`computeAnchor` in `library/dashboard/static/components/review-channel.js` measures a CSS path plus the element's tag and visible text; `resolveAnchor` walks it back to a live element after a view re-renders, path first and tag+text second.
The server stores what the browser measured and never computes one.
A note whose `anchor.selector` is empty is REJECTED, never degraded to a page comment. [why](docs/RULE_EVIDENCE.md#notes-are-anchored-not-page-comments)

**One send carries the whole queue and wakes an agent, which replies onto the same surface.**
`library/dashboard/review_channel.py` is the store and the agent side; `/api/review/*` in `server.py` is the browser's half.
The agent parks on `wait_for_batch` (or `GET /api/review/poll`) and is released the moment the reviewer sends.
A reply always lands on NOTES - named ones, or every note in the batch - so the answer appears under the note that prompted it.
A batch stays pending until all of its notes are answered.

    python3 -m library.dashboard.review_channel poll  --project <dir>
    python3 -m library.dashboard.review_channel reply --project <dir> \
        --batch <id> [--note <id>] --text "what you did"

Notes live per project in `pipeline_output/review/channel.json`, each recording the view it was written on.

### Run control

Start, Handbrake, Resume and Step launch `run_pipeline.py` as a child process.

- Start uses `--full-auto agy` and does NOT pass `--review`; gates are an opt-in tick box. Step is `--step <id>`, with the id resolved server-side to the first topologically-unrun step. [why](docs/RULE_EVIDENCE.md#runs-are-driven-from-the-page)
- **The handbrake is a file, not a signal.** `library/tools/run_control.py` owns the whole vocabulary - `pipeline.hold`, `pipeline.pid`, `pipeline_run.json` at the project root - and both processes speak only through it.
- The runner reads the hold at the TOP OF EACH STEP, so the step in flight finishes and writes its state first. Never kill mid-step. [why](docs/RULE_EVIDENCE.md#the-handbrake-is-a-file)
- `pipeline_run.json` is the runner's own account of itself (mode, current step, how it ended). Nothing else may write it, and the dashboard reports run state from that file only.
- Launch with `sys.executable`, never a bare `python3`.
- Child output goes to `pipeline_output/logs/run_*.log`, and `/api/pipeline/run` waits briefly and fails the request if the runner already died.
- Tests: `tests/test_dashboard_run_control.py`.

## 5. DaVinci Resolve integration - CRITICAL RULES

These constraints are hard-won and must be followed exactly when scripting DaVinci Resolve.

### Connection

Resolve must be running with a project open before executing Resolve-dependent steps.

```python
import sys, os
sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
import DaVinciResolveScript as dvr
resolve = dvr.scriptapp("Resolve")
```

If `resolve` is `None`, Resolve is not running or not fully loaded - retry with a delay.

### Process isolation

- Never create a timeline and use `ImportFusionComp` in the same Python process. Clip references go stale after timeline creation, so import comps in a separate script or process.
- One `ImportFusionComp` returns a Composition object, not a boolean. Verify with `clip.GetFusionCompNameList()`.
- Tool loading is lazy: `GetToolList()` may show 0 tools immediately after import. For reliable bulk comp building use `comp.AddTool()` on the Fusion page to bypass lazy loading.

### Judge every Resolve call by what it returns

- **`hasattr` is always True on Resolve's scripting proxies, including invented names.** Guard on return values, never on `hasattr`. [why](docs/RULE_EVIDENCE.md#hasattr-is-always-true)
- **A tick that prints what it ASKED FOR is a lie, and so is a failure that prints nothing.** One defect, two faces: `build_timeline` discarded four `SetSetting` returns and printed the MANIFEST's numbers, so the log read `1080x1920` while the timeline was `1920x1080`; and step 6.01 wrote its reason to STDOUT while the runner reports STDERR on a non-zero exit, so a build that refused for a stated cause arrived as an exit code and a truncated log. Print what `GetSetting` RETURNS, and send a failure's reason to stderr. Both ends of the runner's failure branch now report both streams. [why - the landscape master, and three renders spent guessing at an error the pipeline already had](docs/RULE_EVIDENCE.md#the-tick-that-reported-what-it-wanted)
- **The timeline SHAPE goes on the PROJECT, and is confirmed by reading it back.** The OTIO mix round trip re-imports the timeline and the rebuilt one inherits the project's resolution, so per-timeline settings applied before it are discarded. Measured on Resolve 21: these keys commit synchronously (20/20 immediate reads matched), so a read-back is a confirmation and not a wait - but confirm anyway, because `SetSetting` returning True is a claim and `GetSetting` is the evidence. `tests/test_resolve_build_timeline.py` fails a build whose project will not hold it.
- Discarding a return value and printing success is not evidence the call did anything. Say so when Resolve declines; the neural-directive block in `resolve_build_timeline` is the pattern to copy. [why](docs/RULE_EVIDENCE.md#smart-reframe-reported-success-for-months)
- Read the truth off `TimelineItem.GetProperty()` with no argument, which returns the whole dict, before trusting any property name.
- **`Pan` and `Tilt` are the transform properties. There is no `PanX` and no `PanY`.** `ZoomX`/`ZoomY` are real. [why](docs/RULE_EVIDENCE.md#pan-tilt-and-volume)
- **The scripting API cannot set an audio level, and that is a COMPLETE enumeration.** An audio `TimelineItem` has no property dictionary at all, so every spelling of `SetProperty` returns False; the whole documented audio surface is `GetFairlightPresets`, `ApplyFairlightPresetToCurrentTimeline` and `InsertAudioToCurrentTrackAtPlayhead`, and Fusion's `ActionManager` registers no audio action. Do not re-probe it - see "The mix goes through OTIO" below. [why](docs/RULE_EVIDENCE.md#pan-tilt-and-volume)
- `TimelineItem.Stabilize()` works, and is the most expensive call in a build - see below.
- `CreateMagicMask` is withdrawn: it returns False for every mode.
- Super Scale is a **MediaPoolItem** property taking an **int**, with companion keys `SuperScale Sharpness`/`SuperScale Noise Reduction` (no space after Super). [why](docs/RULE_EVIDENCE.md#hasattr-is-always-true)

### Transitions go through Fusion. Both other routes are closed.

The Python timeline builder plus Fusion IS the architecture, not a workaround.
**Do not wire FCPXML or DRP project-file surgery back in.** [why](docs/RULE_EVIDENCE.md#fcpxml-and-drp-are-closed)

- Every transition type the pipeline may plan lives in ONE enumeration, `library/tools/transition_vocabulary.py`, with a recorded reason for each withdrawn type.
- **A cut the plan did not decorate is a hard cut.** `transition_selector` never invents a DRAWN transition; `WITHDRAWN_SCENE_CHANGE_DEFAULTS` records the three it used to. A brand template's allow-list is a permission, not an instruction.
- `tests/test_transition_vocabulary.py` checks the handoff toolkit, the brand templates, the registry fallback and the renderer's dispatch against it. A type advertised anywhere else fails CI.
- Adding a transition means adding a builder to `library/tools/fusion/effects.py` first.
- No per-clip Fusion comp can mix two clips, so there is no cross dissolve or wipe on this route.

**A DRAWN transition can only sit where a V1 clip ends, and the step that plans them is TOLD which cuts those are.**
One enumeration, `library/tools/transition_carriers.py`.

- `block_reaches_v1` is the single statement of V1 membership - a bookend card, a `speech` or `hook` block - and `compile_manifest` builds its V1 track from that same predicate, so the two cannot drift.
- Every B-roll placement goes on V2, so a cut whose OUTGOING block is a `transition_slot` has no V1 clip ending on it and `compile_manifest` refuses the transition by name. A cut whose outgoing clip is the LAST thing on V1 is refused too: the effect is a tail AND a head.
- `cut_carriers` reads that off the spine before the run, and step 4.02's bridge puts it in `cuts_toon` as `can_carry_drawn_transition` / `carry_basis`. `CUTS_LEGEND` defines both columns as DATA, because `handoff.md` is frozen - the same route `music_measurement.MEASUREMENT_LEGEND` takes for step 2.04.
- **The table is never filtered or re-ranked.** Every cut is still offered; the model is told the truth and still chooses (section 10.5). [why - the run it failed, and the measured A/B](docs/RULE_EVIDENCE.md#the-menu-contained-cuts-that-cannot-be-built)
- `tests/test_transition_carriers.py`.

### Stabilization is the memory ceiling, and it runs last

`neural_engine_directives` is applied AFTER every clip, comp, overlay and SFX is placed, so a build that dies inside it loses ALL of them. [why](docs/RULE_EVIDENCE.md#stabilization-oom-87gb)

- Treat it as the memory ceiling of the whole pipeline and do not run other heavy jobs beside it.
- It changes picture steadiness and nothing else - never structure, timing, framing, grade, captions or sound.
- For a timeline meant to be scrubbed rather than shipped, pop `neural_engine_directives` off the **in-memory** manifest before `build_timeline` and leave the file on disk carrying it.
  `PIPELINE_SKIP_STABILIZATION=1` is that pop, in step 6.01, opt-in and off by default: the plan keeps its directives and only that run declines them. A render made with it is UNSTABILIZED - say so when reporting one. [why](docs/RULE_EVIDENCE.md#stabilization-oom-87gb)

### Fusion .comp files - NEVER

- Never use `ApplyMode` in a Merge node: it crashes Resolve with a SIGSEGV.
- Never use `Path {}` when a Merge node exists in the same comp: it causes black output.
- Never use `BlendClone`; it is silently ignored. Use `Blend`.
- Never use `Tools = ordered() {`; it fails. Use `Tools = {`.
- Never omit `GlobalOut` on Background nodes: it stops rendering mid-clip.
- Never set DirectionalBlur `Length` greater than 5: it creates artifacts and edge tiling.
- Never set transition zoom greater than 1.04: it is too aggressive and breaks immersion.

### Fusion .comp files - ALWAYS

- Always set `Inverted = Input { Value = 1, }` on EllipseMask for vignettes.
- Always include `MaskWidth`, `MaskHeight` and `PixelAspect` on EllipseMask.
- Always wire `Transform1.Input <- MediaIn1.Output` explicitly.
- Always use `Blend` instead of `BlendClone` for Merge opacity.
- Always include `GlobalOut` on Background nodes matching the clip duration.
- Use static `Center = Input { Value = { x, y }, },` for animated pan/center.
- **Size every Background node to the SOURCE clip's own resolution, never to the delivery format.** Read it off the MediaPoolItem's `Resolution` and do NOT swap it for rotation - Fusion gets the stored frame. [why](docs/RULE_EVIDENCE.md#background-sized-to-the-delivery-frame)

### Frame mapping

**Comp frame 0 is the clip's FIRST PLAYED frame, and `clip_dur` is the SOURCE's frame count. They are different numbers and both are needed.**
One enumeration, `library/tools/fusion/played_window.py`.

- Use the SOURCE clip frame count for `clip_dur` (the comp's GlobalIn/GlobalOut range), read as `int(mpi.GetClipProperty('Frames'))`, not `clip.GetDuration()`. It is always at least the played length, so a Background sized by it exists for every frame that plays. [why](docs/RULE_EVIDENCE.md#keyframes-outside-the-played-window)
- **Every animated keyframe is placed in the COMP's frames, through `played_range`** - never at a source frame number. `source_in_frame`/`source_out_frame` arrive in the SOURCE's numbering and are translated; a segment cut from source frames 654-725 animates over comp frames 0-71.
- **A spline extrapolates FLAT, so a keyframe outside what plays is not a ramp that does nothing - it is the effect held at full strength for the whole clip.** That is what shipped: 0 of 30 planned transition frames drew and 331 frames, 18.6% of 001's video, carried an unplanned full-strength defocus. Issue #202's `zoom_blur` "held for the whole clip" is the same defect on another type. [why - the four banked comps and the measured master](docs/RULE_EVIDENCE.md#the-transition-ramp-that-never-ran)
- **A ramp longer than the frames its clip plays is REFUSED by name** (`TransitionLongerThanTheClip`), never drawn: it never reaches neutral, so it covers the whole clip.
- **Count DRAWN frames, not planned ones.** `library/tools/fusion/transition_frames.py` reads the comp the renderer writes and evaluates its splines; `tests/test_transition_ramp_draws.py` is the gate. 001's plan was correct on every run it ever made, which is exactly what kept this invisible - a test that asserts a plan exists cannot see it.

### Default transition values

- Brightness Flash: `Brightness = 0.67`, `Saturation = 1.83`, animate `Blend` 0-1 with Sine easing.
- Crash Zoom: Transform `Scale = 0.4`, `Offset = 0.6`, range 0.6-1.0, Quad easing, mirrored.
- Glow: `SoftGlow.Gain = 5.0`, `SoftGlow.XGlowSize = 100`, linear easing.
- Default easing uses `LUTLookup` driven by the system `Transition` variable for Edit page transitions.
- For per-clip Fusion comps, replicate easing with `BezierSpline.sampled()` pre-baked keyframes.

### Tracks

**Per-clip Fusion comps reach V1 AND V2.**
One enumeration, `library/tools/execution/fusion_tracks.py`.

- `compile_manifest` merges a declared look onto both. Any pass that draws it must read both. [why](docs/RULE_EVIDENCE.md#house-look-missed-the-broll)
- Transitions stay on V1: `after_clip` indexes the V1 clip LIST, so replaying it elsewhere draws a transition at an unrelated cut.
- Drop detection in `build_verification` asks whether a label was PLACED, not whether it is on V1.
- `tests/test_house_look_reaches_broll.py` drives the real pass against a fake Resolve.

**Track V2 is for additive overlays only** and its Fusion comps cannot read V1 video content.

- Use V2 for dip-to-black, colour washes, letterbox bars and particle effects.
- Do NOT use V2 for flash, blur or zoom: they require processing the underlying video on V1.
- Adjustment Clips cannot go on V2 - `InsertGeneratorIntoTimeline` always targets V1.
- Import one `transparent_1080x1920_30fps.mov` and reuse it via `AppendToTimeline` with `clipInfo` targeting `trackIndex: 2`.
- `SetProperty('CompositeMode', n)` sets composite modes (0 Normal, 5 Screen). `timeline.SetTrackEnable('video', 2, False/True)` toggles V2.

**A3 is a logical SFX bucket, not one lane.**
Overlapping SFX are fine - the timeline builder allocates A3, A4, ... - but identical SFX positions are not.

### Media pool and audio

- **Prefix overlay filenames with their context**, such as `sub_craig_seg_000.mov`. Generic names like `seg_000.mov` collide in the media pool and basename lookups silently pick the wrong clip. Look up by filepath first with `GetClipProperty("File Path")` before falling back to basename. [why](docs/RULE_EVIDENCE.md#media-pool-name-collisions)
- **Place V1 clips while only track A1 exists**, or the timeline floods with empty tracks: iPhone MOVs contain multiple audio streams. Add A2 and later tracks afterward, and place music or SFX with `mediaType: 2`.
- **Resolve audio pool items report 24fps regardless of the timeline.** `AppendToTimeline`'s `startFrame`/`endFrame` are in the SOURCE timebase, so compute audio in/out with the pool item's own FPS. [why](docs/RULE_EVIDENCE.md#audio-pool-items-report-24fps)
- **Renders are silent unless you say otherwise.** `SetRenderSettings` must set `ExportAudio`/`AudioCodec` explicitly; `resolve_render.py` also probes the output for an audio stream before reporting success. [why](docs/RULE_EVIDENCE.md#renders-are-silent-by-default)

### The mix goes through OTIO, and it goes in at placement time

Every planned dB - the bed's per-block curve and each clip's `volume_db` - reaches Fairlight
by ONE route: `library/tools/otio_mix.py` writes it into an OpenTimelineIO export and
`library/tools/execution/deliver_audio_mix.py` imports the result back.
Resolve's OTIO carries clip volume in plain JSON, **in dB**, with keyframes.
[why - the measured renders, and the routes that were rejected](docs/RULE_EVIDENCE.md#the-mix-goes-through-otio)

- **The import REBUILDS the timeline.** Fusion comps and CDL grades do NOT survive it; clip
  placement, transform (`_apply_conform`), timeline markers and native transitions do. So the
  round trip runs straight after the last clip is placed, before the Fusion pass and the
  grade, and nothing may hold a `TimelineItem` from before it.
  `tests/test_audio_mix_delivery.py` drives a whole build and asserts the comps are still there.
- **The `volume` parameter is ABSENT from an untouched export**: Resolve writes
  `"Parameters": []` when every value is at its default, so it must be INSERTED, not patched.
- **A keyframe's frame number is measured from the CLIP'S START ON THE TIMELINE**, not from the
  start of the timeline and not from the source in-point.
- **`ImportTimelineFromFile` answers None with no diagnostic** when a referenced media file is
  missing, when the path is relative, or when the timeline name is taken. Check the first,
  pass an absolute path, and rename the placement timeline out of the way.
- A cyan `UNAPPLIED target` marker is the FALLBACK, written only when the route declines and
  saying so. It is a note asking a human to set a level, never a level.
- The master limiter stays a marker in every case: it is a master BUS setting, and no
  clip-level route reaches it.
- DRT blob surgery and FCP7 XML are both REJECTED as routes and are not to be reopened. DRT rests on
  an undocumented binary layout with only a static gain demonstrated; FCP7 XML's round trip costs a
  constant 3.1 dB tax on everything. [why](docs/RULE_EVIDENCE.md#the-mix-goes-through-otio)

### Visual verification

Render single frames via the Deliver page to verify effects look correct.
Under 2KB means a broken or black frame; 30-150KB means real video content.

### Reading a killed build off disk

Resolve is the captain's application and a crashed build is not licence to start it.
What reached disk is readable on its own, as plain SQLite.
**Copy the project database before opening it; never open it in place.**
[why - paths, the table join, and what it cannot answer](docs/RULE_EVIDENCE.md#reading-a-killed-build)

### Markers and timeline items

Use `timeline.AddMarker()` and `timeline.GetItemListInTrack()`, following the patterns in `timeline_item_markers` and related tools.

## 6. The spine contract

`mesh_spine` (step 2.05) emits the timeline spine every creative step reads.
Its shape is defined and enforced in `library/tools/spine_contract.py`, which mesh_spine calls before emitting.

- Every block carries `clip_id`, `source_start`, `source_end`, `word_timestamps` and `alignment_method`. `None`/empty only for non-speech blocks.
- **Read those keys directly** (`block["clip_id"]`). Do NOT reintroduce `.get("clip_id", "")` fallback chains: a missing key is a contract violation and must raise. [why](docs/RULE_EVIDENCE.md#get-clip-id-disabled-beat-alignment)
- Word timings use `source_start`/`source_end`, not `start`/`end`.
- `speech_sequence` (2.02) treats the LLM's `source_start`/`source_end` as a LOOKUP HINT only. Real timings come from aligning the passage text against WhisperX words, and a passage that cannot be aligned fails the step. This is what stops invented round-number ranges reaching the timeline.
- **Two body passages cut from one clip may not claim overlapping source ranges.** 2.02 re-anchors such a passage past the previous one or fails it; `manifest_validator` asserts the same on consecutive V1 clips. The drift threshold is a secondary aid only. [why - and what is still unverified in a render](docs/RULE_EVIDENCE.md#overlapping-source-ranges-play-twice)
- **A passage is anchored by SEARCH, never by the occurrence nearest the hint.** 2.02's `_align_words_to_text` tries every occurrence of the passage's first word, both from the occurrence and from `ANCHOR_BACKUP_SECONDS` before it, and ranks the alignments: most passage words aligned, then SHORTEST SPAN, then smallest leading gap, then hint proximity. The hint is the last tie-break because 001's correct anchor was *further* from the hint than the wrong one. [why - the 6.901s of unintended audio it shipped](docs/RULE_EVIDENCE.md#the-passage-opened-on-the-wrong-i)
- **There is no gap threshold and no voiced-fraction band.** A silence survives exactly when no equally complete anchor removes it, so a real dramatic pause is kept and a mis-anchor is not. Both outcomes are SAID: `alignment_report` on the step's own output records the leading gap, the largest gap, the voiced fraction and the anchors considered for every passage, and the `reanchored`/`held` cases also print to stderr. Never correct silently, and never add a fitted band - `tests/test_aligner_leading_gap.py` fails on one.

## 7. Data flow

Data flows through `pipeline_data.json`.
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
- A project outside `PIPELINE_PROJECTS_ROOT` is addressed by passing its absolute path in place of the slug to `run`, `status`, `info` and `dashboard`. It is referenced in place, never copied.
- Multi-project environments group projects by client folders if specified during creation.

### Where a project's files go

**One module owns the project-side layout: `library/tools/project_layout.py`.**
`paths.py` owns the REPO and the MACHINE; this owns one PROJECT, which is a folder passed in rather than a constant.

**`pipeline_output/steps/` IS the pipeline.** One directory per step, `1_04_temporal_index/` and so on, so `ls` walks it in the order it runs and a file's directory names the step that wrote it. That is the point: the captain audits by walking the folder, not by consulting an index. [why](docs/RULE_EVIDENCE.md#by-kind-was-the-wrong-axis)

- `STEPS` is the ordered table of every step, in DAG order, with the directory it owns. `AreaSpec.step` names the owning step for a step-owned area.
- Directory names use the STEP number, the same spelling `library/steps/` and every "step 1.04" citation uses; `README-LAYOUT.md` renders true run order, which diverges from that sort in two places. [why](docs/RULE_EVIDENCE.md#by-kind-was-the-wrong-axis)
- Each step directory holds `output.json` and `summary.md` (what `step_exporter` writes) plus whatever files the step produced.
- **A step writes only inside its own directory.** Pass `step=` to `write_dir`/`write_path` and another step's area raises; `assert_step_owns` is the same guard for a path from outside the layout.
- **Not everything is a step's product.** `logs/`, `gates/`, `review/`, `llm_*/`, `thumbnails/`, `backups/`, `migrations/`, `provenance/`, `scratch/`, `unsorted/` and `exports/` stay at project level.
- `exports/` is the one area TWO steps legitimately write: 6.01 the render and 6.02 the QA report. `produced_by` names both, and provenance leaves `step_id` None rather than picking one.

- Every place inside a project folder is a row in `AREAS`, keyed by `Area`. A place that is not a row does not exist, and asking for one raises.
- **A step never composes a project path.** It names an `Area` and gets a path: `write_dir`/`write_path` to write, `read_dir`/`read_path` to read, `resolve_project_relative` for a path recorded in state. All sixteen steps that used to join `project_folder` with a name of their own choosing now do this. [why](docs/RULE_EVIDENCE.md#nothing-owned-the-project-folder)
- **Inputs are structurally protected.** `raw/`, `music/`, `assets/`, `brand_assets/`, `compositions/`, `external/` and `profiles/` are `Kind.INPUT`: `write_dir`/`write_path` raise for them, `ensure()` does not create them, and `assert_writable` refuses any path underneath. Reads are unaffected.
- `assert_writable(path)` is the guard for a path that arrives from outside the layout - a manifest key, a CLI flag. Outside the project, at the bare project root, or inside an input area all raise.
- **A project explains itself.** `ensure()` renders `README-LAYOUT.md` from the same table the code reads - the steps in run order, what each reads and what each writes - and runs on `manage_project.py new` and at step 1.01 of every run.
- **`classification.per_clip_artifacts` names an AREA, not a directory**: `{area:temporal_index}/{clip_id}.json`. [why - the two steps left pointing at a moved directory](docs/RULE_EVIDENCE.md#a-declaration-that-went-stale)
- The scaffold is not a second list. [why](docs/RULE_EVIDENCE.md#by-kind-was-the-wrong-axis)
- Anything the pipeline FETCHES rather than computes - a downloaded music track - is output, and goes to `Area.ACQUIRED_MEDIA` under the step that fetched it, not into `music/`.

**Backups of `pipeline_data.json` are automatic and bounded.**
`pipeline_output/backups/pipeline_data/`, one per RUN rather than per save, newest `MAX_PIPELINE_DATA_BACKUPS` kept, pruned by the writer.

- The pruner only ever considers files matching its own naming pattern, so a hand-made backup dropped in beside them is never deleted. Pre-policy backups live in `backups/pipeline_data/legacy/`.
- One per run, not one per save: `save_pipeline_state` runs after every step, and the thing worth keeping is the state as it stood BEFORE a run. [why](docs/RULE_EVIDENCE.md#nine-hand-made-backups)

### Reading a run back

**The layout answers "which step wrote this" by where the file is. Provenance adds WHICH RUN and FROM WHAT.**
`library/tools/provenance.py` owns it.

Every record carries the METHOD that produced it, because an audit that cannot tell a measurement from a declaration is not an audit:

- `observed` - the runner listed the output tree before the step and again after, and this file appeared or changed in between. A recorded fact about a specific run.
- `declared` - nothing was watching, but the file sits in a step's directory, or the area declares a writer. True of the pipeline as built, not of this file.
- `unknown` - neither, and it stays unknown. **Never attribute a file to the nearest plausible step.**

- The runner observes each step **after** `_export_step_for_review`, or a step's own `<step_id>.json` export is attributed to nobody.
- Records are append-only: a run is a thing that happened, and a later run overwriting a file does not unmake the record of the earlier one. Newest wins when the question is "what is this file now".
- An area declaring TWO producers (`exports/` is `render` and `validate`) leaves `step_id` None and names both.
- `derived_from` is READ out of the artifact - `SOURCE_KEYS` names the keys - never inferred from a filename. A `clip_id` is not a path.
- `pipeline_output/backups/` is counted, not listed: an archived copy is not the artifact the step wrote.

**Two generated documents, regenerated on every run and by `manage_project.py trace <slug>`.**
`library/tools/run_traceback.py`. `RUN-TRACEBACK.md` is the steps in order - when, how long, what it consumed and from which step, what it produced. `ARTIFACTS.md` is the other direction: every file, with the step that wrote it and how that was established.

- Both are generated from `dag.json`, the two ledgers, `step_errors` and the provenance ledger. Never hand-written, and never edited in place.
- `trace` works on a run that already happened: the timings and verdicts are recorded fact, and file attribution falls back to `declared` - labelled, with the header saying plainly that nothing was observed. [why](docs/RULE_EVIDENCE.md#the-folder-could-not-be-read-back)
- `prosody_analysis`, `object_segmentation` and `ocr_extraction` declare their areas and are not in the DAG, so `ARTIFACTS.md` says nothing runs them. `unwired_step_ids` matches by `step_ref`, because the DAG calls `step_1_01_scan_project` simply `scan`.
- `tests/test_run_traceback.py`.

**A project that predates the layout is brought onto it with `manage_project.py organize <slug>`.**
`library/tools/project_migration.py`. It plans by default and changes nothing until `--apply`.

- **It never deletes.** Every action is a move or a copy, and a file whose purpose cannot be established goes to `pipeline_output/unsorted/<bucket>/` with a stated reason, never a guess. Measure what you can - `media_facts` records a file's duration, format and encoder - so an admitted unknown is an examined one.
- **It never modifies an input directory.** Pipeline output found inside one is COPIED out, so `raw/` is left byte for byte as it was found. That is also why `--revert` undoes moves and not copies: undoing a copy means deleting.
- Every run writes a manifest to `pipeline_output/migrations/` - each action with source, destination, byte count, digest and reason, plus the byte totals before and after. `manage_project.py organize <slug> --revert <manifest> --apply` reads it back.
- `tests/test_project_migration.py`.

### Replaying a step without running the pipeline

**A step's exact prompt and context can be rebuilt off frozen state, at a named revision, with no pipeline run, no Resolve and no project write.**
`library/tools/replay_bench/`, driven by `python3 -m library.tools.replay_bench`. Read [`docs/STEP_REPLAY_BENCH.md`](docs/STEP_REPLAY_BENCH.md) before changing what a step is routed: it answers "did that change what the model sees" in seconds, against a project two other workers may be holding.

- The reconstruction is the runner's OWN assembly - `gather_step_inputs`, the step's `bridge.py`, `project_fields`, `json_to_toon`, the handoff and `get_brand_constraints` - never a model of it. `reconstruct.py` therefore imports nothing from `library` at module scope: it runs as a subprocess with the TARGET tree first on `sys.path`, and a module-scope import would measure the same tree on both sides.
- **A snapshot is captured outside the repository; only its MANIFEST is committed**, to `tests/fixtures/replay_snapshots/`. The payload is one client's transcripts and goes stale the moment a step changes what it emits; the manifest is digests, so two people can establish they hold the same bytes without shipping them.
- **`verify` is a gate, not a report.** It reconstructs every archived context and exits non-zero on any unaccounted difference: if it cannot reproduce the past it cannot be trusted to compare futures. A step that matches only after a named cause is subtracted reads `EXACT (explained)`, never as a clean pass.
- **Never use the pipeline's own token figures.** `present_llm_step` logs `len(s.split()) * 1.3` and it is 0.38x-0.54x the `o200k_base` count on 001's own contexts. The bench measures from the reconstructed string and names the tokenizer; with `tiktoken` absent the count is absent rather than estimated.
- The bench measures the pipeline and stays out of it. No step, process or dashboard module may import it - `tests/test_replay_bench.py` fails if one does.

### No test reaches a real project

**A test builds its project under `tmp_path`, or it skips. It never falls back to a real one.**
The captain's footage and renders cannot be re-shot and this machine has no Time Machine destination, so there is no undo. [why](docs/RULE_EVIDENCE.md#tests-bound-to-the-captains-project)

- `library.tools.paths.PROJECTS_ROOT` is the ONE constant naming where real projects live, and it is the enforcement point - `project_layout.py` cannot be, because a project folder is an argument it has no way to judge. **A test may not read that constant.** Patching it by string is fine.
- `tests/conftest.py` points `PIPELINE_PROJECTS_ROOT` at an empty temporary directory for the whole session, before anything under `library/` is imported. The sandbox stays EMPTY; it is not a fixture.
- `tests/test_tests_never_reach_real_projects.py` asserts the guarantee three ways: the root a test sees is the sandbox, no test source reads the constant or carries a home-absolute path, and collecting the whole suite against a populated DECOY root - sandbox deliberately off - binds nothing under it and changes not one byte of it.

## 9. Environment and dependencies

- **`run` needs the dedicated `.venv`; nothing else in the CLI does.**
  The venv is per checkout and gitignored - `python3 -m venv .venv && pip install -r requirements.txt` makes one.
  `manage_project.py` checks for `mlx_vlm`/`whisperx`/`easyocr`/`torch` only for the commands in `ML_DEPENDENT_COMMANDS`, which is `run` alone: the ML packages are imported by pipeline STEPS, and `run` launches them with `sys.executable`, so the interpreter running the CLI is the one that must carry them.
  `dashboard`, `list`, `status`, `info`, `trace`, `organize`, `archive`, `relink` and `new` reach none of them and are served without them. Never move that check back to import time. [why](docs/RULE_EVIDENCE.md#the-dashboard-could-not-be-opened)
  The refusal names the activate script if this checkout really has one, and says how to make one if it does not - the old message printed a bare `source .venv/bin/activate` into a checkout with no `.venv`.
  `tests/test_cli_ml_preflight.py`.
- Set `RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB` to your DaVinci Resolve installation.
- Set `HF_TOKEN` for HuggingFace models like Audio Flamingo Next.
- Set `PIPELINE_SFX_LIBRARY`, `PIPELINE_MUSIC_LIBRARY` and `PIPELINE_PROJECTS_ROOT` to absolute paths.
- Python dependencies are in `requirements.txt`. `librosa` is required by `music_analysis`; without it the step reports `available: false` and the run fails rather than continuing silently.
- External tools: `ffmpeg` and `ffprobe`. Node.js for Remotion subtitle rendering.
- GPU acceleration is required for Gemma 4, SAM 2, WhisperX and EasyOCR.
- **Every `subprocess.run` capturing text must pass `encoding="utf-8"`.** `text=True` decodes with the locale codec, and this pipeline writes UTF-8 status glyphs. [why](docs/RULE_EVIDENCE.md#text-true-decodes-with-the-locale-codec)
- **Reach Resolve through `library/tools/resolve_locale.scriptapp_preserving_locale`, never `dvr.scriptapp` directly.** The call resets `LC_CTYPE` to `C` down in Blackmagic's library, so `locale.getpreferredencoding()` becomes US-ASCII and every later `open()`, `Path.read_text()` or `text=True` subprocess without an explicit encoding raises `UnicodeDecodeError` on this repository's own UTF-8 sources. The same defect class as the rule above, arriving from the other side: there the caller chose the wrong codec, here the codec changed underneath a caller who chose none. Only `LC_CTYPE` is restored - `LC_NUMERIC` is untouched, because handing fusionscript a decimal comma would corrupt every number crossing the boundary. Measured on 21.0.0b.28: the import is harmless, `scriptapp` is what does it. **Two call sites use the wrapper (`marker_feedback`, step 6.01); eight others still call `scriptapp` directly and are unmigrated** - `resolve_relinker`, `timeline_serializer`, `resolve_health`, `resolve_project_sync`, `qa/timeline_sync_qa`, `execution/resolve_render`, `execution/apply_fusion_comps` and `probe_resolve_capabilities`.

## 10. Cross-cutting rules

These hold across steps and cost a full audit cycle each. Do not undo them.

### 10.1 Contracts between steps

**Key-name mismatches are the dominant bug class.**
Index required keys directly so a rename fails loudly; never `.get()` a default for a key a contract promises. [why - and the known disagreements](docs/RULE_EVIDENCE.md#key-name-mismatches)
The declaration side of the same rule - that no step may declare an input required which nothing refuses on, and none may refuse one it declared optional - is surveyed by `library/tools/input_contract.py` (section 3, "A declaration must be true").
Join semantic documents to the catalog with `library/tools/semantic_index.py`: the documents are keyed by FILE STEM, the catalog by `clip_XXX`. [why - the per-clip table that came out full of `none`](docs/RULE_EVIDENCE.md#the-transition-planner-read-the-raw-document)

**A step reads the vision document through a SUMMARY its own handoff names, not through the raw document.**
Name the columns, or - where a pre-bridge already builds the table the handoff points at, as 4.02 does with `cuts_toon` - route the document to the BRIDGE and keep it out of `context_fields` entirely; declaring it with no sub-paths gets all fifteen columns, `analysis_metadata` included.
Projection narrows the prompt and never the inputs: the bridge, the post-bridge and `step.py` still receive the whole thing. [why - the measured share](docs/RULE_EVIDENCE.md#the-transition-planner-read-the-raw-document)

**`compile_manifest` reads `pipeline_data.json`, not just files.**
The per-step `*.json` files in `pipeline_output/` are a best-effort dashboard export, and a missing file reads as `{}`. [why](docs/RULE_EVIDENCE.md#compile-manifest-read-an-empty-catalog)

**Declare `interface.llm_outputs` on any hybrid step whose bridge emits a key the step also declares as an output**, or whose LLM contribution differs from the step's outputs.
`present_llm_step` builds the injected schema from `interface.outputs` minus what the bridge produced, so without the declaration the model is asked for nothing, or every attempt "fails". [why](docs/RULE_EVIDENCE.md#empty-llm-schema)

**One vision schema, two views.**
`vision_pipeline_v3.py` emits `scene[]`/`camera[]`/`actions[]`/`objects[]`/`assessment{}`; `library/tools/vision_schema_adapter.py` derives the historical `analysis.*`/`blocks` view from it (step 1.03 applies it on write, `semantic_index` on read), so either may be addressed.
Derive only what v3 measured. [why](docs/RULE_EVIDENCE.md#vision-schema-two-views)
A step that wants framing, stability, usable ranges or subject visibility must also list those paths in its manifest's `context_fields`, or they are deleted before the prompt.

**A project's brand template reaches the run through `state["brand_template"]`.**
`library/tools/brand_registry.py` is the whole vocabulary: `project_template_name`, `resolve_template_reference` (a NAME or a PATH to a BrandTemplate, raising on either missing) and `reference_template_name`. [why](docs/RULE_EVIDENCE.md#brand-template-never-reached-the-run)
The key is spelled three ways and they are not interchangeable:

- `state["brand_template"]` is the REFERENCE string.
- `inputs["brand_template"]` is the RESOLVED TEMPLATE DICT, and reaches only a step whose manifest declares it (step 5.01 does `brand_template.get("style")`).
- `inputs["brand_style"|"brand_effect"|"brand_content"]` are the slot dicts.

Nothing is broadcast. A step gets brand data because its manifest asked.
`tests/test_brand_template_load.py`.

**A project that names no brand template gets NOTHING, and every slot's reading of that absence is written down.**
`library/tools/brand_registry.no_brand_template` is what an empty declaration resolves to - every creative slot empty - and `ABSENT_SLOT_READINGS` is the statement, one row per slot, of what each consumer does with it. `describe_brand_absence()` is printed once per run so the absence is stated rather than inferred. [why](docs/RULE_EVIDENCE.md#a-template-nobody-chose)

- **`library/templates/default_brand.yaml` is a template a project must NAME**; an empty declaration does not resolve to it, and naming it is what makes its values a brand decision.
- An absent slot reads as the ABSENCE OF DECORATION, never as a substitute taste: an undeclared look is no grade at all and no exposure normalisation either (§12), an empty transition allow-list permits the whole drawable vocabulary, an absent `transition_duration_ms` bounds nothing, an absent `delivery_format` gets the product enumeration's own default. Add a slot, add its row - `tests/test_brand_template_load.py` fails on a slot with no recorded reading.
- **`effect.caption_case` is the one creative value that survives absence**, recorded as an exception rather than left implicit.
- Two slots have NO READER and no template value should state one: `content.music_genre` and `effect.sfx_density`.

**A project's own declarations reach every step through `state["project_config"]`.**
`brand_registry.project_declared_config` reads them off project.yaml, `load_pipeline_state` puts them in state and the runner's whitelist broadcasts them. Only what the project DECLARES is in there; an undeclared key is absent, never filled in.

- `target_duration_seconds` is the one with readers. [why - the four duration gates that ran against a constant](docs/RULE_EVIDENCE.md#a-template-nobody-chose)
- `library/tools/duration_targets.get_target_duration_zone` returns **None** when neither the project nor a selected template declares a target, and each caller says it did not check. There is no fallback zone.

**A brand's CONSTRAINTS reach three planning steps, and a step has two names.**
`TemplateLoader.get_brand_constraints` gives `creative_direction` a palette and typography, `plan_transitions` the permitted transition vocabulary and `plan_vfx` a VFX intensity - as prompt text, not as an input key.
`library/tools/template_loader.BRAND_CONSTRAINT_STEPS` is that enumeration, named in the DAG's vocabulary and checked against the step table at import.

- **The DAG knows `plan_vfx`; the step's manifest and directory know `step_4_03_plan_vfx`, and no rule connects them** - `scan` is not a prefix of `scan_project`. `library/tools/project_layout.node_id_for` is the ONLY translator, and the node id is what `pipeline_data.json`, both ledgers and the runner key everything by. Code holding one vocabulary while its caller holds the other goes through that function or it silently answers nothing. [why](docs/RULE_EVIDENCE.md#the-brand-reached-no-planning-step)
- The `agy` request file records `constraints` and concatenates it into `prompt`, because in that mode the file IS the prompt.
- `tests/test_brand_constraints_reach_the_prompt.py`.

**The captain's creative brief is one per-project declaration that reaches EIGHT steps, BY REFERENCE.**
`project.yaml`'s `creative_brief` - top level or under `pipeline:` - names a markdown file.
`load_pipeline_state` reads the PATH into state; `gather_step_inputs` reads the FILE and hands the step a REFERENCE to it, and only if the step's own manifest declares the input.
A relative path resolves against the project; an absolute one is taken as given, so the captain's read-only planning tree is cited in place and never copied. A path that cannot be read or is empty RAISES.

- Seven of them - `creative_direction`, `speech_sequence`, `music_selection`, `select_broll`, `plan_transitions`, `plan_vfx` and `plan_sfx` - are the ones whose handoffs tell the model to read one. `tests/test_creative_brief_reaches_prompt.py` fails if a handoff documents a brief its manifest does not declare.
- **The eighth is `mesh_spine`, and it is declared without a handoff line.** It sets every gap length and every `music_behavior` and had no brief at all; its `handoff.md` is under the captain's freeze, so the DECLARATION is what asks for it. A step gets the brief because its manifest asked, not because its prompt mentions one - the documenting-set above is the direction the test enforces, and it is one-way. [why - the two audit rounds that found it missing](docs/RULE_EVIDENCE.md#the-brief-is-paid-seven-times)
- It is not a `context_fields` entry. Like `brand_template` it is restored around the projection BY NAME, so a step's allow-list neither has to list it nor can drop it.
- **The cost is per step, not per run.** Measure before assuming a brief is free. [why - 001's measured before and after](docs/RULE_EVIDENCE.md#the-brief-is-paid-seven-times)

**A reference is an ABSOLUTE PATH plus a MAP, and the rule for what still travels inline is in `library/tools/brief_reference.py`.**
[why - what copying the document cost across the seven prompts, and what the map costs instead](docs/RULE_EVIDENCE.md#the-brief-was-copied-seven-times)

- **The map carries a LINE RANGE per heading**, so following it is one `sed -n 'a,bp'` and not a search.
- **The rule is per SECTION, not per step**, so every step sees the same document: the preamble inline, a section under `INLINE_WHEN_UNDER_BYTES` inline, everything else a heading, a size, a range and a lede. Nothing is filtered or summarised away - the whole document is at the path.
- **Which sections are about THIS video is not the engine's judgement.** A project pins sections inline with `pipeline.creative_brief_inline` in its `project.yaml`, and there is no default list.
- **The mechanism carries THREE documents, and a fourth costs a row.** `brief_reference.REFERENCED_INPUTS` is that enumeration - the brief, step 4.04's SFX catalogue (#299), and step 3.02's per-clip vision analysis (#F14; `library/tools/footage_reference.py`). Do not build a second by-reference mechanism.
- **`HARNESS_READS_FILES` is a complete enumeration and an unknown harness raises.** `agy` and `mock` reach a file; `api` does not, so under `api` the document is carried whole - a route the model cannot follow is a loss, not a saving. `present_llm_step` does that restore.
- `tests/test_brief_reference.py` FOLLOWS the reference rather than asserting its shape: it parses the path and the range out of the string the model reads and requires that what comes back was not in the prompt.

**A step may carry ONE reading of a measurement, or two on different axes - never the reading and the structure it was read from.**
The summary rule below says it for a rendered pair; this is the same rule when the second copy is the whole structure. [why - step 3.02's three readings of one vision analysis, what each uniquely carried, and the measured before and after](docs/RULE_EVIDENCE.md#three-views-of-one-analysis)

- **Establish what each view uniquely carries before deleting one**, and move the STRUCTURE rather than a rendering: the largest view is usually the one holding the per-segment bounds, the object labels and the assessment fields no table has a column for.
- The structure goes BY REFERENCE - `library/tools/footage_reference.footage_document` is its shape - so nothing is filtered, ranked or summarised away and every byte is at the path.
- **Shape a referenced document for the MAP.** Nothing below a `##` may be a heading (`parse_sections` lifts deeper headings into the map), and the section's opening line carries no underscore or backtick (`_lede` strips markdown emphasis).
- `tests/test_broll_context_share.py` guards the RATIO of what the prompt spends on readings to what the structure costs inline, because that number does not depend on a fixture.
- **Measure a value where the model READS it** - the whole assembled prompt, pre-bridge included - not on the one route it used to arrive by. A projection to `{}` serialises as the truthy string `"{}"`, so a gate measuring the old route goes quiet rather than red.
- **Collapsing a structure into a summary makes the summary's blank cells load-bearing.** All three states - measured and usable, measured and unusable, never measured - must be legible in the cell itself.

**Every LLM step declares `context_fields`, and the deterministic half loses nothing by it.**
Projection happens inside `present_llm_step`, so a hybrid's post-bridge and a `deterministic_with_llm` step's `step.py` keep receiving the unprojected inputs - only the prompt narrows. A step declaring none is handed every byte it was routed. [why](docs/RULE_EVIDENCE.md#two-steps-had-no-projection)

- A path prefixed with `-` DROPS what the paths above it selected: `"timed_spine"` then `"-timed_spine.structure.*.word_timestamps"`. Prefer it to enumerating the twenty keys you meant to keep, which stops delivering the twenty-first.
- `render` (6.01) and `validate` (6.02) are the only unprojected LLM steps and are an open captain decision; `tests/test_llm_context_routing.py` holds that exemption list.
- **A pre-bridge's own table is never projected away, and you do not have to list it.** `run_pipeline.project_step_context` - the ONE place the projection happens - restores any `bridge_supplied` key the allow-list dropped entirely. Listing it in `context_fields` is still allowed and is the only way to NARROW it. [why](docs/RULE_EVIDENCE.md#the-bridge-table-that-was-projected-away)

**A step's decision must be SOURCED from its own context.**
Judge routing by the assembled context, never by whether the run came out right: one agent answering the whole run carries facts forward in its own head, so a step reading nothing at all still decides well and the missing edge leaves no trace. Check with `library/tools/replay_bench` - rebuild the step's context off frozen state and look for the sentence the decision rests on. `tests/test_pacing_and_sfx_are_not_remembered.py`. [why - the two edges that were only ever answered from memory](docs/RULE_EVIDENCE.md#the-decision-that-was-remembered-not-sourced)

**A table the prompt names, arriving with zero rows, is reported on the run that sends it.**
`library/tools/empty_table_guard.py`, called from `present_llm_step`, names every TOP-LEVEL key whose value is `[0]{...}` or `[]`, marking the ones the prompt mentions by name.
It never fails a run - an empty table can be the honest answer - and it catches the zero-row half of the family only: rows that are PRESENT but hollow go past it. [why](docs/RULE_EVIDENCE.md#a-prompt-that-described-an-empty-table)

    python3 -m library.tools.empty_table_guard <project_folder>   # a run that already happened

- **Build a pre-bridge table on a key the DAG really routes, and key its rows on the identifier the answer has to name.** A-roll entries are keyed `spine_block_position`, not `segment_id`.

**Word timings do not reach a prompt, and what a step cannot select by NAME it selects with a named VIEW.**
`library/tools/context_views.py` is the enumeration: a manifest may put `view:<name>` in `context_fields` and get a READING of a routed input rather than a path into it. An unknown name raises, and a view's NAME is the key it writes - which is what makes a second projection a no-op, and an `llm_only` step is projected twice on every run. [why](docs/RULE_EVIDENCE.md#the-transcript-arrived-with-every-word)

- `view:transcript` is what step 2.01 reads instead of `temporal_index.*.speech_regions`: what was said, in which clip, between which two seconds. **Step 2.02 does NOT declare it** - its own pre-bridge builds `transcripts_toon` off the same regions and its handoff reads that table by name, so declaring both puts every line in the prompt twice, in two different column orders. [why](docs/RULE_EVIDENCE.md#the-transcript-shipped-twice)
- `view:picture` is what a step reads to see a clip past its opening, and **every step that decides from what a shot looks like declares it** - 2.01, 2.02, 3.02, 4.02, 4.03 and 4.04. `analysis.scene` is `scene[]` as prose and `scene[]` is one segment per clip, so a long clip is described by its opening seconds alone; the view is the vision pass's per-window `blocks`. [why - the coverage measured on 001](docs/RULE_EVIDENCE.md#the-director-saw-the-first-nineteen-seconds)
  - **It is NOT a substitute for `scene[]` and must not be swapped in for it.** `scene[]` says WHERE (location, type, lighting, notable features); the action windows say WHAT HAPPENS. Different axes, so the view goes BESIDE `analysis.scene`. Repairing `scene[]`'s coverage is a separate, unestablished question (#302).
  - **Its rows are keyed by the CATALOG clip id wherever a routed input makes that join possible** - the id `timed_spine`, the assignments and `transcripts_toon` use, while the documents are keyed by file stem. `clip_catalog`, `a_roll_assignments` and `b_roll_assignments` are the lists it joins against; a document none of them names keeps its own id and is reported in `not_in_the_clip_list`. That is why 2.02, 4.03 and 4.04 route `clip_catalog` - optional, projected to `clip_id` and `filename`.
  - **Raw `blocks` in a `context_fields` allow-list is the wrong route**, several times the view's size once `json.dumps`'d into a cell. `tests/test_picture_view.py` fails if one comes back.
- `view:stability` is the two camera-steadiness signals SIDE BY SIDE, with the verdict on whether they agree - declared by `select_broll`, `plan_transitions` and `plan_vfx`. The VLM's per-window `camera[].stability` and the deterministic per-clip `assessment.camera_stability` measure different things and on 001 they disagreed on 9 of 17 clips while three tables in one run carried two different answers about the same clip. **It resolves nothing**: whatever picked a winner would become the measurement (section 10.5).
- `view:alignment` is what step 2.02's `alignment_report` measured about each passage's INSIDES - the leading gap, the largest silence BETWEEN two words, the voiced fraction - declared by `review_rough_cut`. It ORDERS and REPORTS; no threshold fires on any of it, and section 6's "no gap threshold and no voiced-fraction band" is unchanged. The run summary is its second reader (`library/tools/alignment_findings.py`).
- `view:prosody` has NO consumer since #F5 unwired step 1.05, and is kept for whatever declares one next. It is what step 2.01 used to read instead of `prosody_analysis.profiles`. An allow-list selects by NAME and cannot tell a measurement from a record of its absence, so this selects by `library/tools/prosody_profile.profile_defect` - the same predicate step 1.05 refuses to write a hollow profile with. Real profiles pass through; the rest become ONE line saying how many measured nothing and why. **State the absence, never hide it.** [why](docs/RULE_EVIDENCE.md#seventeen-copies-of-an-error-are-not-a-measurement)
- **A view is not routing.** The step still has to declare the input the view reads.
- **The code that cuts on the timings still gets every word**, because none of it reads the prompt: every post-bridge and `step.py` receives the UNPROJECTED inputs.
- **A declaration of NOTHING BUT `-` paths means "everything, minus these".** That is how `render` and `validate` drop `assembly_manifest.subtitles.*.words` while staying deliberately unprojected.
- `tests/test_transcript_view.py`, `tests/test_prosody_view.py`.

**Never send a summary and the structure it was rendered from.**
`analysis.scene` IS `scene[]` rendered by `vision_schema_adapter.scene_prose`, and `analysis.motion` IS `camera[]`. Send the prose (it is lossless here and 30-50% smaller) unless the step's handoff tells the model to read the segment bounds - 3.02's does, so 3.02 keeps the structure and drops the prose. `tests/test_context_ships_it_once.py` fails on a manifest declaring a pair. [why](docs/RULE_EVIDENCE.md#the-summary-and-its-own-source)

**No raw value list reaches a prompt.**
`music_analysis.tempo.beats`, `.tempo.downbeats` and `.energy_dynamics.energy_curve_1hz` are hundreds of numbers each, and a step routed the whole analysis must DROP them with `-` paths. Beat proximity is decided in code, off the unprojected inputs, through `library/tools/beat_grid.py` - the model is separately handed the answer as `cuts_toon`'s `beat_near_cut`. Same class as the word timings above. [why](docs/RULE_EVIDENCE.md#the-beat-grid-in-the-prompt)

**A step that chooses a picture is SHOWN one, and the picture is of the window it will receive.**
One enumeration, `library/tools/window_frames.py`.
Prose says what a clip DOES; it cannot say what it LOOKS like. [why - the captain's marked cutaway, the strip, and the 0 of 12 contexts that had never carried an image](docs/RULE_EVIDENCE.md#no-step-that-chose-a-picture-had-seen-one)

- **The window is enumerable before the answer, which is what makes the frame honest.** `window_anchors` is every `video_in` `cutaway_window.choose_window` can return, computed across the spine's own slot lengths - not assumed to be the span list, because `fit_to_clip` pulls the anchor earlier near a clip's end.
- **A strip shows both ENDS of the window, plus enough of the middle that no more than `SECONDS_UNSEEN_BETWEEN_SAMPLES` passes unseen.** The ends are the first and last thing the viewer sees. The interior bound is a RESOLUTION, the same kind of number as `picture_quality`'s 5 Hz, and it is stated in the prompt. One frame is never enough.
- **A strip runs to the LONGEST slot that anchors there, and a shorter slot plays a prefix of it.** The row carries `video_in`, `strip_end` and `frames` and the header says so.
- **Every candidate window gets one; nothing is ranked, filtered or shortlisted.** Whatever selects a shortlist becomes the chooser (§10.5), which is what `cutaway_window.DECLINED_TO_RANK` already refuses. A window whose strip could not be drawn is NAMED in the map, never quietly absent.
- **A picture has no smaller textual form, so this is NOT a second `brief_reference`.** `HARNESS_SHOWS_FRAMES` is its own complete enumeration and an unknown harness raises. A harness that cannot be shown one is handed a line SAYING the frames were drawn and withheld, and the prose path stands. Never put base64 in the context (`WITHDRAWN_DELIVERIES`).
- A strip already drawn is reused, the way the per-clip index is. [why - the measured ffmpeg time, disk and context cost on 001](docs/RULE_EVIDENCE.md#no-step-that-chose-a-picture-had-seen-one)
- `tests/test_window_frames.py` FOLLOWS the reference: it parses the directory and filenames out of the string the model reads, opens what comes back and probes its dimensions.

**A TOON table cell is quoted with a BACKTICK, and a multi-line value under a KEY is a `|` block.**
`library/tools/toon_serializer.py`. Escaping a newline is right in a CELL, where a row is a line; under a KEY it is not. [why](docs/RULE_EVIDENCE.md#the-apostrophe-was-doubled-in-every-prompt)

- **Columns come out in the order the DATA declares them, never sorted.** A spine block is stored `position, block_type, ..., content, ..., source_start, source_end`, and a projected tree carries the order of the manifest's own `context_fields`; both are deliberate statements. Alphabetising puts `end` before `start`. [why](docs/RULE_EVIDENCE.md#alphabetical-columns-put-end-before-start)
- The quote character must be one neither content class contains - this pipeline sends prose full of apostrophes AND `json.dumps`'d dicts full of double quotes, so `'` and `"` are both out.
- **A lossless round trip is NOT the test.** Nothing downstream calls `toon_to_json` - the model reads the characters. Assert the emitted FORM.
- A nested object in a table cell is `json.dumps`'d, and that is 7-64% of every large context. **Do not "fix" it by demoting the table to indexed blocks**: measured end to end on all eleven prompts it makes them 19% BIGGER. A view that flattens one shape is the route that measures better, and the large savings are in what a step READS, not how it is written. [why - both routes measured](docs/RULE_EVIDENCE.md#embedded-json-is-where-the-content-is)

**A step reads only the keys the PRODUCING step is asked for, and `creative_direction` is the enumeration that proves it.**
`library/tools/creative_direction.py` loads `DIRECTION_KEYS` off step 2.01's own manifest and `direction_value` RAISES on anything else, because a `.get` for a key the schema cannot produce returns a default that reads as a decision. [why - the seven code sites, each returning its default on every run the pipeline has made](docs/RULE_EVIDENCE.md#seven-reads-of-a-key-that-cannot-exist)

- **Which side is wrong is established from the HANDOFF, not from the code.** Repairing the other side - adding a key to the schema - looks identical and can ask a creative director for artwork that section 14 puts with the project.
- `WITHDRAWN_DIRECTION_KEYS` records each withdrawn read and where the value really lives. `MECHANICALLY_READ_KEYS` and `PROMPT_ONLY_KEYS` must together account for every field the schema declares, and a prompt-only claim is CHECKED against the manifests' `context_fields` rather than asserted.
- `tests/test_asked_fields_have_readers.py` ENFORCES on `creative_direction` and REPORTS on every other step's declared output schema without failing the build - reader-or-delete for the rest is the captain's call, inventoried in [`docs/UNREAD_DECISIONS_INVENTORY.md`](docs/UNREAD_DECISIONS_INVENTORY.md).
- **It can only see what a manifest DECLARES**, and `expected_schema` is one level deep, so a field asked for inside a list-item shape (4.02's `duration_feel`, 3.03's `cut_decisions`) is invisible to it. Closing that needs nested `expected_schema`, not a new format.

**A call with nothing to ask is not made.**
When every key a step declares has already been produced - by its own `step.py`, or by its pre-bridge - the schema is empty and `present_llm_step` returns `{}` without calling. Declare `interface.llm_outputs` to ask anyway. [why](docs/RULE_EVIDENCE.md#thirty-three-thousand-tokens-for-three-bytes)

**The delivery format is a property of the PRODUCT, not of the footage.**
One enumeration, `library/tools/delivery_format.py`: a brand template declares `delivery_format`, a project may override with `pipeline.delivery_format`, the default is vertical 1080x1920, and an unknown name raises.
Every consumer calls `resolve_delivery_format(project_folder)`; nothing carries the value as a key.
The catalog's `source_resolution` DESCRIBES the footage and is never a render target. [why](docs/RULE_EVIDENCE.md#delivery-format-is-not-the-source-resolution)

**The beat grid is `tempo.beats` / `tempo.downbeats`, and it does not start at zero.**
Read it through `library/tools/beat_grid.py` and never synthesise `[i * 60/bpm ...]`.
The times are in the MUSIC file's clock and are used as timeline times, which holds only while music is placed at `source_in` 0 - `compile_manifest` asserts that. [why](docs/RULE_EVIDENCE.md#the-beat-grid-does-not-start-at-zero)

**`target_energy` has ONE reading: `library/tools/energy_reading.py`.**
"Building" names a TRAJECTORY, not a level, and is not "high".
`WITHDRAWN_HIGH_WORDS` records why "dynamic" and "fast" are out too; widening the high bucket is a decision, not drift. [why](docs/RULE_EVIDENCE.md#building-is-not-high)

**`music_behavior` has ONE vocabulary: `library/tools/music_behavior.py`.**
Five words - `prominent`, `background`, `fade_in`, `fade_out`, `silent` - and `silent` is one of them, because a planned silence is a decision.
`mesh_spine` declares it, `spine_contract` rejects a word outside it, `audio_mix` turns it into the dB, `compile_manifest` CARRIES it onto `_spine_blocks` rather than recomputing it, and `render_qa` judges the render against it.
Resolve a block that declares none through `resolve_music_behavior`, never with a local default: `WITHDRAWN_BEHAVIORS` records why the two-word `full`/`ducked` form is out. [why](docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary)
`tests/test_music_behavior_vocabulary.py`.

**The timeline's length comes from the spine, never from a passage's `end_time`.**
`library/tools/timeline_duration.measure_timeline_duration`: max `timeline_end` over the spine, falling back to `a_roll_assignments`.
`0.0` means "no evidence" - say so rather than warn about a length nothing measured. [why](docs/RULE_EVIDENCE.md#end-time-is-a-source-timestamp)
### 10.2 Reaching the picture and the sound

**A capability is only real where the renderer reads it.**
The renderer dispatches on parameter NAMES (`library/tools/execution/apply_fusion_comps.build_effect_comp`), so a planner emitting a name nothing reads produces a comp without that effect and no warning. [why](docs/RULE_EVIDENCE.md#unread-parameter-names)

- When you add a knob, add it to `build_effect_comp` in the same commit and assert it draws nodes (`tests/test_vfx_delivery.py`).
- When a design node cannot be delivered, record the reason where the design lives. Withdrawal is a legitimate outcome; a silent unread key is not.
- Every TOP-LEVEL manifest key is held to this by `tests/test_manifest_readers.py`: name a reader that really contains `manifest[key]`, plus one sentence saying what that reader does to the picture or the sound - or put it in `EXEMPTED_KEYS` with a reason.
- `docs/PIPELINE_PLAN.md` is the standing audit of which manifest keys have a reader. Check it before assuming a stage's output reaches the picture, and update it when you wire or withdraw one.

**An empty VFX plan says WHY it is empty, and step 4.03 CAN produce a non-empty one.**
One enumeration, `library/tools/vfx_plan_basis.py`. [why - `{"visual_effects": []}` read the same whether the planner chose stillness or named four effects the post-bridge discarded, plus the four hypotheses and which one it was](docs/RULE_EVIDENCE.md#the-vfx-plan-that-was-always-empty)

- `enhancement_spec.planning_basis` carries `basis`, `proposed`, `resolved` and one `dropped` record per casualty with its reason. **`no_effects_planned` and `every_entry_dropped` are spelled differently on purpose**: the first is a decision, the second is the absence of one.
- `DROP_REASONS` is the whole of what a drop can be for and a reason outside it is refused by name, so a new drop branch has to say what it is before it can go quiet.
- **Recording is not gating.** `may_be_empty: true` on `vfx_creative` still holds and an empty plan is still accepted; `compile_manifest` reads the basis and NAMES the casualties, and fails on none of them. Whether a dropped entry should REFUSE the step the way an unplayable sound refuses 4.04 (§10.5) is recorded in `THE_REFUSAL_QUESTION` and is the captain's call.
- **The step is not broken and the vocabulary is not missing.** `tests/test_vfx_reaches_the_manifest.py` runs a named toolkit effect end to end to a drawn node.
- `tests/test_vfx_plan_basis.py`.

**An overlay that draws nothing is not rendered.**
`generate_motion_props.props_draw_ink` is the predicate; keep it in step with the MotionGraphics composition.
The output carries NO `available` key when nothing draws, because `available: false` anywhere fails the run. [why](docs/RULE_EVIDENCE.md#overlays-that-draw-nothing)

**An overlay segment the manifest names and disk does not have REFUSES the compile.**
`compile_manifest.assert_overlay_segments_on_disk`, over `OVERLAY_TRACKS` - subtitles, motion graphics and timed text, all three at the same severity.
Every overlay is ADDITIVE, so an absent one changes nothing anyone downstream can see: the picture underneath is intact and the export is a valid video of the right length. [why](docs/RULE_EVIDENCE.md#the-motion-graphics-that-were-planned-and-absent)

**Nothing plans a motion graphic; `tests/test_motion_graphics_delivery.py` is what proves one reaches a pixel.**
Step 4.06 declares `enhancement_spec` and `creative_direction` REQUIRED, the DAG routes both, and `generate_motion_props` reads neither.
§16 names the elements that SHOULD exist; what anything can actually ask the renderer for today is a brand template's `effect.motion_accents` and `effect.motion_progress_bar`.
The upper third's COPY has no producer, and that is an open captain decision (`motion_graphics_vocabulary.COPY_SOURCE_IS_UNSET`), not a value for the engine to invent.
`input_contract` REPORTS both (section 3, "A declaration must be true"). [why](docs/RULE_EVIDENCE.md#the-motion-graphics-that-were-planned-and-absent)

**Manifest validation has a semantic half.**
`library/tools/manifest_validator.py` asserts distinct cut points, distributed SFX, distinct VFX ranges, B-roll differing from the A-roll it covers, no overlay overlaps, no repeated source audio across consecutive V1 clips, no zero-duration clips and no fabricated round-number source ranges.
It does NOT check ducking curves.
Regression fixtures live in `tests/fixtures/captured_run/` and come from a real broken run - never replace them with empty-list fixtures. [why](docs/RULE_EVIDENCE.md#manifest-validator-semantic-half)

**Every frame of the timeline must show a clip.**
`compile_manifest._assert_timeline_fully_covered` fails on any stretch of V1+V2 with nothing on it.
The one exception is a hole the plan deliberately declared, via the optional `intentional_black_beat`/`black_beat_reason` spine keys documented in `library/tools/spine_contract.py`; an undeclared hole always fails.
Step 6.02 honours the same declaration by passing `declared_black_beat_ranges` into `render_qa`, and both gates bound a beat by `MAX_DECLARED_BLACK_BEAT_SECONDS` from the spine contract - keep that bound in one place. [why](docs/RULE_EVIDENCE.md#undeclared-black)

**Overlay geometry comes from `library/tools/safe_area.py`, and captions are grouped by measured pixels.**
One enumeration keyed by delivery format, insets stored as FRACTIONS so a 4K vertical or a small test frame needs no second row; an unknown format raises.
Four consumers read it: `subtitle_style.SubtitleStyle.resolve` (the `safeArea`/`captionMaxWidth` props), `generate_motion_props`, `timed_text_overlay` (which refuses a card centred in the platform's UI band) and `plan_subtitles`' grouper.
**Every element `MotionGraphics/index.tsx` draws is positioned from the insets, the progress bar included** - never `bottom: 0`, `width: 100%`, which on a 1080x1920 delivery sits 320px inside the caption and audio-bar band and is covered by the platform's own interface. [why - the profile, and the grouper that never ran](docs/RULE_EVIDENCE.md#safe-area-and-the-caption-grouper)

- The subtitle style is resolved at the top of `generate_subtitles` and there is no blind path: `split_into_groups` raises without a `fits_fn`.
- **A card fits the BOX, not one line.** The overlay wraps (`flexWrap`), so `fits_in_box`/`MAX_CAPTION_LINES` is the test; grouping against one line halves the words on every card and therefore halves how long each is on screen. [why](docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line)
  **Reconstruct a grouping with `fits_in_box`, never `fits`.** A reconstruction using the one-line half reads as a bug in the pipeline. [why - both predicates run against 001's shipped 45 cards](docs/RULE_EVIDENCE.md#the-caption-grouping-reconstruction-used-the-wrong-predicate)
- **The split is BALANCED, not greedy.** A greedy fill leaves the remainder as a runt card, and a card is on screen only until the NEXT card's first word, so nothing downstream can lengthen one. `split_into_groups` solves per block for the partition with the fewest cards under the floor. Model the REAL display duration if you touch it.
- The last card of a block leaves when the block does, so it can be short with no partition able to fix it. `manifest_validator`'s P6 reports exactly that case and fails every other one.
- Set the weight axis when measuring a VARIABLE font.
- A card with one over-wide word carries `fit_scale` and the render draws THAT CARD smaller; the style's font size is untouched.
- The Remotion studio's `defaultProps` get the insets from `src/safeArea.generated.ts`, projected out of the enumeration by `scripts/generate_safe_area_defaults.py`.
- `tests/test_caption_safe_area.py`.

**A project may typeset its own captions, and that is not a change to anyone else's.**
`pipeline.subtitle_typography` in a project.yaml, the same `{font, size, weight}` shape a template's `style.typography` uses, resolved by `subtitle_style.resolve_subtitle_style`. [why - the captain's measurement, and what the size moves on 001](docs/RULE_EVIDENCE.md#the-caption-size-that-governs-one-video)

- It overrides the template's typography **KEY BY KEY**, not slot for slot: asking for a size is not asking to give up the typeface. That is the one place this precedence differs from `delivery_format_name`'s and `timed_text_overlay`'s, deliberately.
- `TYPOGRAPHY_KEYS` is the whole of what may be declared and a fourth key is refused by name, because `SubtitleStyle.resolve` reads exactly three.
- **A number that governs one video does not go in a preset four other videos read.** 001 declares `size: 85`; `bold_large` (192), `clean_standard` (144), `minimal` (120) and `LEGACY_FONT_SIZE` (160) are untouched.
- The size moves caption PACING, because the grouper fits cards to the box at the resolved size. Re-measure rather than assuming a ratio.
- `tests/test_subtitle_style.py`.
### 10.3 Measuring the footage

**Subject position comes from `face_center_x`, not from the vision pass.**
`vision_pipeline_v3` measures shot size, identity and time ranges - never a position - and `object_segmentation`/`ocr_extraction` produce boxes but are not in the DAG.
`step_1_04_temporal_index.compute_face_presence` emits the horizontal centre of the largest detected face at 5Hz; `library/tools/subject_framing.py` reduces it per clip and returns a POSITION; `compile_manifest._conform_fields` owns the one copy of the geometry that turns it into a pan.

- `subject_framing` returns None whenever the footage cannot support an answer. **None means "frame centred" - do not replace it with a fabricated 0.5.**
- The join is `subject_centers_by_clip`. Step 1.04's real output shape is `{"temporal_event_indices": [...], "full_indices": [...]}` - a LIST of per-clip dicts, not a mapping. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)

**A crop must be wide enough for the subject, and aiming it is not enough.**
`compute_face_presence` records `face_width` beside `face_center_x`; `subject_framing.subject_box` reduces both, and `SUBJECT_HEADROOM` is how much clear space the subject needs on each side.
Filling a portrait frame from landscape source keeps exactly `1 / fill_zoom` of the source WIDTH, and a subject wider than that is cut by the frame edge wherever the pan points. [why - 001's measured frame](docs/RULE_EVIDENCE.md#the-crop-was-narrower-than-the-face)

- **No zoom both fills the frame and holds an over-wide subject** - every zoom below fill leaves bars - so `_conform_fields` SYNTHESISES the missing picture: shrink the source until the subject fits and put the same frame again, scaled to cover and blurred, behind it. That is the `framing_backdrop` route, drawn by `fx.subject_backdrop` and dispatched on `backdrop_picture_scale`.
- The Fusion comp composes into the 9:16 column Resolve's own transform then crops at FULL fill zoom, so a backdrop clip carries **no `framing_pan_x`**: a pan slides the composition out of the frame.
- **The verdict is carried by the PLAN check**, `manifest_validator`'s P8. `render_qa.measure_face_intact` is the render-side backstop and is weaker on purpose: a face cropped hard enough stops being detectable at all.
- An explicit `framing_pan_x` still outranks the measurement, and the clip then keeps its crop with `subject_safe_zoom` recorded so P8 can say what that cost.
- `tests/test_subject_survives_the_conform.py`.

**Face frames are sampled at the CLIP'S OWN aspect, never a fixed shape.**
`face_sample_dimensions` reads the DISPLAY shape (rotation side data applied, because autorotate runs before the filter chain), bounds the SHORT side to `FACE_SAMPLE_SHORT_SIDE`, and raises rather than falling back to a shape.
Anything derived from the sample size - `frame_area`, the `face_center_x` divisor - must read that size, not a literal. [why - the numbers, and what the fix does not fix](docs/RULE_EVIDENCE.md#the-squashed-face-frame)

**The frame FILLS by default, and there is no heuristic.**
One enumeration, `library/tools/framing_intent.py`: 0.0 letterboxes, 1.0 fills.
The number comes from the spine block > the project's `pipeline.framing_intent` > the template's `style.framing_intent` > `DEFAULT_FRAMING_INTENT` (1.0).
A malformed declaration raises, and `_conform_fields` has ONE branch. [why](docs/RULE_EVIDENCE.md#the-letterbox-default)
`tests/test_framing_intent.py`.

**A framing DECLARATION is not a framing DELIVERED, and the manifest records both.**
`_conform_fields` writes `framing_intent` (what was asked for) and `framing_delivered` (what the geometry produces) on every picture clip; `render_qa` reads the second. [why - 001's eighteen placements, and the 13.7x failure recording only the first caused](docs/RULE_EVIDENCE.md#a-declaration-a-clip-cannot-honour)

- **A source whose display aspect already covers the delivery frame has no bars to give**, so it fills at every intent, `0.0` included. `framing_intent.source_covers_frame` is that predicate and `delivered_framing_intent` is the reading. Do not put the coverage arithmetic anywhere else.
- **Which clips letterbox is therefore a MEASUREMENT, not a second creative choice.** A step that chose a framing per clip would be inventing taste where the arithmetic already answers (section 10.5). **Whether the picture is inset at all is the captain's PREFERENCE and belongs in the project's own declaration.**
- **The spine-block level of the chain is reachable and unwritten.** `compile_manifest` really reads `block["framing_intent"]` (`tests/test_compile_manifest.py`), but no handoff asks for one and `spine_contract` does not list the key. On the B-roll side the hook is the PLACEMENT's own key, not the block it covers: a cutaway is different footage.

**A file on disk is not a measurement.**
Judge a step by what it MEASURED.
`speech_advanced_pipeline` raises `ProsodyUnavailable` and writes nothing rather than recording an error as a result; step 1.05 rejects a hollow profile and reports `available: false`, which `check_output_is_real` reads as a failed step. [why](docs/RULE_EVIDENCE.md#hollow-prosody-files-cached)

**Never invoke `step_1_03_semantic_analysis/step.py` against a real project to test it.**
Exercise the collection half with an analysis dir of copied profiles and `raw_footage_files: []`. [why](docs/RULE_EVIDENCE.md#semantic-analysis-triggers-a-vision-run)

**`usable_ranges` is a measurement, and an absent one is EMPTY - never the whole clip.**
`[[0, duration]]` beside `usable_ranges_method: "unmeasured"` is one field contradicting the three next to it, and the B-roll selector reads it to decide which 2.5 seconds of a clip to cut. [why](docs/RULE_EVIDENCE.md#usable-ranges-were-the-whole-clip)

- `[]` with method `unmeasured` means nobody looked; `[]` with method `deterministic_v1` means the clip was measured and none of it is usable. `usable_ranges_summary` renders the two differently and neither as a blank cell.
- The signals that measured it are named in `usable_ranges_signals`. `library/tools/analysis/picture_quality.py` is the one that needs only the video file, so it is the one that works on a first run - 1.03 runs BEFORE 1.04, so the temporal-index rules have nothing to read until a re-run.
- It samples at 5 Hz and reports runs of 0.6s or longer. **State that bound when you report a verdict**: shorter soft windows can fall between samples, and it cannot tell motion blur from a missed focus.
- `camera_stability` stays `unknown` when nothing measured it. Do not fill it in to match.

**Camera steadiness has ONE reading, and it says which signal answered.**
`library/tools/camera_stability.py`.
The optical-flow residual is the signal designed for the job; the standard deviation of `motion_energy` is frame differencing and cannot tell a gesturing subject from a shaking camera. [why - the key nothing writes, and 001's 17 clips](docs/RULE_EVIDENCE.md#the-residual-nobody-read)

- **The residual lives at `camera_motion_decomposition.values[].residual`.** `compute_deterministic_assessment` read `temporal_index["camera_motion"]["residual"]`, a key nothing has ever written, so the residual was read on 0 of 001's 17 clips and every label came from frame differencing.
- **The thresholds are read off the INSTRUMENT, not fitted to a project.** Step 1.04 block-matches a 160x90 frame at 5 Hz over a grid stepping 2 px, which arrives as a residual of 0.125, so the tiers are half a grid step and one grid step of MEAN per-sample global displacement. `MEASURED_ON_001` attaches that project's distribution and the VLM cross-check as a CHECK, with the caveat that one project is a thin basis and no render has been made against them.
- **Every label carries `camera_stability_method`** - `optical_flow_residual`, `motion_energy_std` or `unmeasured` - because a method field travels with the number it qualifies. A document written before the field existed reads `unrecorded`, which is a different claim from `unmeasured`.
- The two signals still differ where both are real: `view:stability` is how that reaches a prompt (section 10.1).
- `tests/test_camera_stability.py`.
- **The DISPLAY reads the method, not the ranges.** `usable_ranges_summary` answers "unmeasured" whenever the method says so, whatever the ranges hold, and `adapt_semantic_document` REPLACES a stale `usable_portions` rather than deferring to it. Same read-side shape as `stability_summary` treating the literal `"unknown"` as absent - neither writes to the stored document.

**No assessment field reports a default as though it were measured. That is the whole rule, and it holds for every field.**
`compute_deterministic_assessment` is where the deterministic half is decided and `tests/test_assessment_reports_no_default_as_measured.py` is the sweep, kept executable: the assessment is computed with nothing to measure and every field it produces must be an admitted absence. The family was found one field at a time, so assume another exists until the sweep says otherwise. [why - the four found in #301, and what a re-run of 001 would and would not fix](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)

- **An empty `speech_regions` list is not a measurement of silence.** `detect_speech_regions` returns `[]` both when WhisperX ran and heard nothing and when it raised. **`speech_present` is `True` or `None`, never `False`**, and `speech_coverage_method` says `temporal_index` only once a coverage has been computed.
- **An answer that came back without a key is not an answer of `[]`.** `primary_subject_visible` is `None` when the model omitted it and `[]` only when the model really said the subject is nowhere.
- A rendering of an absent measurement is not a measurement either: `_derived_clip_type` returns `""` for a `content_type` of `"unknown"` rather than classifying the clip `b_roll`.
- **A method field travels with the number it qualifies.** The four manifests routing `assessment.usable_ranges` route `usable_ranges_method` beside it, and 2.02 routes `speech_coverage_method` beside `speech_coverage`; an allow-list that selects the number alone cannot tell a measurement from a default.
### 10.4 Gates, and what counts as evidence

**A gate that cannot fail is worse than no gate, because it reads as coverage.**
If you cannot make it read real state, delete it. [why](docs/RULE_EVIDENCE.md#gates-that-cannot-fail)

**Passage engagement is a JUDGEMENT the model writes, and a reader acts on the ORDERING, not the number.**
One enumeration, `library/tools/passage_engagement.py` - `engagement_of`, `engagement_rank`, `engagement_basis`, `unjudged_summary`. Step 2.02's handoff asks for `engagement` on every passage it selects: `{rank, composite, basis}`, ranked against that sequence and nothing else.

- **Compare ranks, and only near the top.** Report the composite; never fire on it. `MEASURED_SPREAD` records how far ranks and composites move between answers to the identical prompt. [why](docs/RULE_EVIDENCE.md#every-line-scored-the-same)
- **A rank is comparable only inside ONE speech_sequence.** It is the model's ordering over the passages it chose, not a scale.
- **A passage the model declined to judge reads as UNJUDGED, never as a low score**, and its reason is stated rather than blanked - the same rule `view:prosody` follows. `engagement_of` and `engagement_rank` return **None**; never coerce either to 0 or to last. `WITHDRAWN_SCORERS` records why each of the three arithmetic scorers that came before was not a measurement.
- The selection itself moves between answers at one revision, so a between-revision difference in what 2.02 picks means nothing until it exceeds that.

`tests/test_passage_engagement.py`.

**A recommendation is APPLICABLE where it is made, or it is an OBSERVATION that names who owns it.**
One enumeration, `library/tools/cohesion_scope.py`. `creative_cohesion` (5.03) cannot be moved upstream of the decisions it reviews - it would arrive before its own inputs exist - so it is scoped where it runs. [why](docs/RULE_EVIDENCE.md#the-review-recommended-what-it-could-not-do)

- `ACTIONABLE_AT_COHESION` is the (state key, field) pairs `compile_manifest.apply_cohesion_adjustments` really rewrites - today `transition_spec.duration_frames` alone, because a duration moves no cut point, clip boundary or subtitle. Only these reach `adjustments`.
- `OWNED_UPSTREAM` reaches `observations` instead, each naming the owning STEP, why the compiler refuses it, and the re-run that would act on it. It carries **no `suggested_value`** (§10.5).
- A pair in neither list RAISES, so a new finding has to say which side it is on.
- **The rescope is not a way to go quiet.** Every finding stays in `warnings`, every observation reaches `assembly_manifest.cohesion_adjustments` under `observed`, and `tests/test_cohesion_scope.py` drives the real applier against both lists.
- **An empty `adjustments` SAYS which absence it is.** `cohesion_scope.adjustments_basis` spells `no_proposal_was_made`, `every_proposal_was_owned_upstream` and `adjustments_were_made` differently - the shape `vfx_plan_basis` takes, for the same reason - and travels onto the manifest's record as `basis`, carrying the standing statement that `ACTIONABLE_AT_COHESION` has an applier and no producer. On every run this project has made it has been the first.
- **`cohesion_score` is REMOVED, not recomputed.** [why](docs/RULE_EVIDENCE.md#the-review-recommended-what-it-could-not-do)

**A SKIPPED test must name an environment that runs it, and a test body must be able to fail.**
`tests/skip_audit.py` is the enumeration and `tests/test_no_unfailable_tests.py` runs it; the runtime half is the session hook in the repo-root `conftest.py`, which FAILS a run reporting a skip no `EnvironmentCondition` declares.

- `ENVIRONMENT_CONDITIONS` is measuring instruments and external applications only - ffmpeg, cv2, parselmouth, npx, Resolve's own templates. **A condition that reads THIS REPOSITORY'S contents is not an environment.** [why - the five tests that skipped everywhere for months, and the nine behind a fixture that has never been committed](docs/RULE_EVIDENCE.md#five-tests-skipped-in-every-environment)
- The source half also fails a test whose body is `pass`, or whose whole body is a `try` swallowing every exception.
- Run it alone with `python3 -m pytest tests/test_no_unfailable_tests.py -q`.

**A gate that FAILS correct output is no more coverage than one that cannot fail.**
If you add a model-judged gate, give it a deterministic half that can carry the verdict, and record the model's opinion rather than enforcing it. [why](docs/RULE_EVIDENCE.md#gates-that-fail-correct-output)

**Six baseline-craft properties are checked on every build, and two of them deliberately do not fail.**
A seventh, a face cut by the frame edge, is measured on the render and gates through the same `framing` check (§10.3).

`render_qa.py` measures the RENDER:

- the picture fills the delivery frame and keeps ONE GEOMETRY PER DECLARED FRAMING
  (`measure_frame_occupancy`).
  **A letterbox bar is FLAT and CONTIGUOUS FROM AN EDGE, and darkness alone does not make one.**
  A row joins a bar only while it is dark, has near-zero variance along itself and matches the
  row before it; the walk runs inward from the top and bottom boundaries and stops at the first
  row that is picture. [why](docs/RULE_EVIDENCE.md#a-dim-shot-is-not-a-letterbox-bar)
  **Every sample is attributed to the clip playing over it** through `framing_spans`, which step
  6.02 builds off the manifest's `framing_delivered` (§10.3) with V2 winning an overlap. The fill
  floor applies to the FILL stretches and the consistency bound applies WITHIN each declared
  framing - never switch the consistency half off for a video declaring more than one framing.
  [why](docs/RULE_EVIDENCE.md#a-declaration-a-clip-cannot-honour)
  **An OVERLAY is not picture, and a sample carries the frame at its own timestamp.** Caption
  ink is neither dark nor flat, so the bar walk stopped at it: 001's bottom bar read 347 rows
  under a caption and 656 without one, on a picture that never changes size. The bars are
  measured on the columns a CENTRED overlay cannot reach (`safe_area.centered_usable_width`);
  a frame size no delivery format describes is measured full width and SAYS so on the result.
  And `_stream_raw_frames` asks ffmpeg for `fps=N:round=up`, because the default `round=near`
  emits the LAST input frame to claim a slot - up to half a sample period after the label,
  which put a cutaway starting at 32.067s into the sample the A-roll clip before it owns.
  [why - both, measured on 001's correct render](docs/RULE_EVIDENCE.md#the-occupancy-gate-failed-a-correct-render)
- colour exists somewhere in the frame (`measure_chroma_presence`);
- speech sits above the bed (`measure_speech_above_bed`);
- the master is deliverable without clipping (`measure_lufs`, whose true-peak half sets `passed = False`).

**`subtitle_gaps` measures the uncaptioned seconds INSIDE a speech block, and it reads the spine to know which those are.**
A B-roll breath is time the plan chose to have no words in. All four of 001's reported gaps - 3.0, 3.5, 3.0 and 3.0s - were the planned beats at spine blocks 1, 4, 6 and 9, and the finding reaches a creative step (§10.4, the QA findings) carrying a legend. A caller with no spine gets the old whole-timeline measurement and the result says which it made.

`manifest_validator.py` checks the PLAN: no caption card under 0.5s, and no effect family covering 100% of eligible items with two or fewer parameter sets.

- P6 exempts the one card no grouping can lengthen - the last in its block, ending where the block does - and reports it. [why](docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line)
- P7 judges DRAWN effects only. `transition_vocabulary.CUT_TYPES` draw nothing, so an edit of nothing but hard cuts is the absence of decoration, and its denominator is the transitions the plan wrote, not `len(v1_clips) - 1`. [why](docs/RULE_EVIDENCE.md#hard-cuts-are-not-an-effect-on-everything)

Chroma and the mix REPORT A NUMBER and pass.
Promoting either is ONE boolean (`CHROMA_PRESENCE_GATES`, `SPEECH_ABOVE_BED_GATES`); do not turn them into gates by another route. [why - including why frame-mean saturation is not the statistic](docs/RULE_EVIDENCE.md#baseline-craft-properties)

`SPEECH_ABOVE_BED_GATES` stays False on the captain's own condition (#183): `background` means -18 dB of CLIP GAIN while the check reads it as SEPARATION, and 001's music is mastered 8.6 dB hotter than its speech, so the target is unreachable by any mix setting.
Do not flip the boolean without changing one of the two. [why - the per-window numbers](docs/RULE_EVIDENCE.md#the-mix-target-is-not-a-separation)

**A clip gain is not a separation, and both halves now SAY which one they are holding.**
`music_behavior.SEPARATION_TARGETS_DB` is the plan's half and is EMPTY - no behaviour declares a separation, and an engine-supplied one would be a strength nobody chose arriving one level up (§10.5).
`measure_speech_above_bed` reads a declared target when the plan carries one and otherwise falls back to the clip gain, recording `required_margin_basis` per window and `judged_on_clip_gain` on the result, so a reported margin can never be mistaken for one somebody chose.

**The bed is fitted at the SECTION that plays, and the offset is a REQUIRED argument.**
Step 2.04 chooses which part of the track plays (§10.5) and the A2 clip carries it as `source_in`, so timeline second *t* is music file second *t + (source_in - timeline_in)*.
`measure_speech_above_bed` takes `music_offset_seconds` positionally with no default, records it beside the windows, and `run_full_render_qa` declines to run P3 at all when it is None - the same shape `beat_grid` already has, and for the same reason: a default of "no offset" is the value that is silently wrong. [why - 001's bed plays from 60.0 s, and fitting from 0 turned 0 of 8 speech windows meeting their target into 8 of 8](docs/RULE_EVIDENCE.md#the-bed-was-fitted-from-the-wrong-second)

**The bed is bounded by the PICTURE, not by V1.**
`compile_manifest` clamps a music clip that runs past the last picture, and the bound is V1 AND V2. It was V1 alone; 001's outro cutaway sits on V2, so the four seconds the spine declared `fade_out` at -12 dB - with `audio_mix` automation written for them - shipped with no bed at all, 4.0s measured at -91.0 dB, announced by a `WARNING:` on stderr forty minutes into an unattended run. [why](docs/RULE_EVIDENCE.md#the-bed-was-trimmed-to-the-last-v1-clip)

**The bed's own measurements reach the mix, because a step that cannot see the music cannot act on any answer about it.**
Step 2.04 measures every candidate (§10.5); its post-bridge folds the CHOSEN track's SCALARS onto `music_selection.measurements` through `music_measurement.selection_measurements`, and step 5.02 reads them and records `bed` plus a per-window `bed_level_after_gain_lufs` - the bed's integrated loudness plus the clip gain, which is arithmetic and not a decision.

- **Only scalars travel.** `music_selection` is declared whole by `plan_transitions` and `mesh_spine`, so the envelope curve and the section table would land in two prompts (§10.1). `WITHHELD_FROM_THE_SELECTION` records both with the reason, and an unaccounted measurement key raises at import.
- **An unmeasured bed is an admitted absence**: `measured: false` with its reason, no level at all, and `bed_level_after_gain_lufs` None - never 0.
- **The separation a window will DELIVER is predicted, and the separation it OUGHT to deliver is not supplied.** `library/tools/speech_loudness.py` measures the speech with one ffmpeg `loudnorm` pass per block over the ranges `a_roll_assignments` names - 0.23 s a block, measured, so 1.2 s for 001's eight - and 5.02 records `speech_lufs` and `separation_delivered_db` per window. **Measure and expose; never choose.** `SEPARATION_TARGETS_DB` is still empty and the master loudness target is still the captain's, so nothing compares the delivered number with anything. A block whose speech could not be measured records the reason and `None`, never 0.
- Step 5.02 declares `audio_spine` and `music_selection` and nothing else. `creative_direction` reaches it through the spine's own `music_behavior`, which `mesh_spine` already decided from it; `enhancement_spec` described audio-aware mixing nobody built. Re-declaring either needs a reader in the same commit.
- `tests/test_mix_reads_the_bed.py`.

The occupancy gate needs to know what the picture was SUPPOSED to look like, so `compile_manifest._conform_fields` records the resolved `framing_intent` on every clip.
A declared letterbox is exempt from the fill floor and never from the consistency half.
`tests/test_baseline_craft_properties.py`.

**Every QA finding has a reader, and one that has none is reported.**
One enumeration, `library/tools/qa_findings.py`. [why - the four shortfalls step 6.02 measured on 001 and wrote to a report nothing opened, and which of them is genuinely advisory](docs/RULE_EVIDENCE.md#the-qa-report-had-no-reader)

- **Two readers, one module.** The run summary prints them at the end of every run, and step 3.03 `review_rough_cut` is handed them as `render_qa_findings`. Both go through `read_qa_report`, so neither can develop a private opinion about which findings matter.
- **Reading is not gating.** The summary block runs AFTER `status` is decided and assigns nothing; promoting a report-only check is still one boolean in `render_qa`. The test pins that ordering off the runner's own source.
- **`passed` is the verdict; `severity` is how loud it is.** A check that did not pass is FAILING at its declared severity. One that passed while carrying a non-`info` severity is ADVISORY **if and only if** its metric is in `REPORT_ONLY_METRICS`, the two whose gate boolean is False. Advisory is read off that enumeration and never off severity alone, and a check's severity moves with its verdict.
- **A metric with no row in `FINDING_READERS` is named first and loudest** - in the summary and in what 3.03 receives - and fails the test, which harvests the metric names out of both producers and checks BOTH directions.
- **No DAG edge carries the findings to 3.03 and none can**: `validate` is the final node and 3.03 is in phase 3, so an edge would be a back edge. They travel by name in `gather_step_inputs`, only to a step whose manifest DECLARES them, and they describe the LAST render - `load_findings` asks state first and the file second and RECORDS which answered. They carry their own legend, because `handoff.md` is frozen (the `CUTS_LEGEND` route), and the legend says plainly that a finding is not grounds to reject a rough cut.
- `tests/test_qa_findings_reach_a_reader.py`.

**The rough-cut review's own answer has a reader, and it has two halves.**
`review_rough_cut` (3.03) is asked for `cut_decisions` on every run.
One enumeration, `library/tools/cut_verdicts.py`. [why - the 19 rows the model answered with and nobody read, the flag verbatim, and what 3.03 reviews that it did not itself decide](docs/RULE_EVIDENCE.md#the-review-answered-and-nobody-read-it)

- **A row that names a CUT goes to step 4.02**, folded onto `cuts_toon` as `narrative_verdict` and
  `verdict_note` keyed on `cut_point_position` - the identifier both tables already share, so there
  is no join. `CUT_VERDICT_LEGEND` defines the two columns as DATA, because `handoff.md` is frozen
  (the `CUTS_LEGEND` route). **4.01 `plan_subtitles` cannot be the reader**: it is `deterministic`
  and has no prompt at all.
- **A row that names NO cut goes to the run summary**, printed after `status` is decided. A route
  back to the owning step does not exist and is stated rather than quietly closed.
  **Nothing is filtered by `decision`, `scope` or severity**: whatever picks which findings matter
  becomes the reviewer.
- **An unjudged cut reads `unjudged`, never `smooth`.** `verdict_of` returns None, and how many cuts
  went unjudged is SAID (`cuts_unjudged`) rather than inferred from a column. A word outside
  `smooth`/`acceptable`/`jarring`/`broken` is carried VERBATIM and marked `unrecognised` -
  `WITHDRAWN_READINGS` records why dropping it and why mapping it onto the nearest word are both out.
- **The verdict decides nothing.** No rule turns `jarring` into a transition; the column is data and
  the model still chooses (10.5).
- **3.03's one input that is a MEASUREMENT rather than an upstream decision is `temporal_index`**, and
  it reads it as `view:transcript` - its Check 5 requires a script "derived from actual temporal
  index data, not from the speech_sequence's intended text", so the projection must not delete it.
  **The remaining self-review is not in the DAG - it is that one agent answers 2.02, 2.05, 3.02 and
  then 3.03 under `--full-auto agy` (10.1). Closing that needs a different answerer, not an edge.**
- `tests/test_cut_decisions_reach_a_reader.py`.
### 10.5 Creative latitude

**The pipeline never invents a creative judgement on the model's behalf.**
A CREATIVE fallback substitutes taste - a mood, a theme, a transition, an effect, a sound, an energy word, a pace chosen for feel - and it goes. A MECHANICAL default is a safe technical value - a frame rate, a timeout, a codec, a retry count, a path - and it stays. Where a creative value is genuinely absent, FAIL or REPORT PLAINLY; a silently-defaulted mood ships and a stopped run does not. [why - the full audit, and what was left](docs/RULE_EVIDENCE.md#the-pipeline-invented-taste-where-no-step-ran)

- Two things are NOT taste, and are the reason the rule is workable. A value meaning "nothing is drawn" - `transition_vocabulary.CUT_TYPES`, `house_look.NEUTRAL_CDL` - is the absence of decoration, not a choice of it. And a rule acting on a value the creative direction really DECLARED is not a fallback: `creative_cohesion` may judge a transition against a declared "high", but may not invent the word first.
- A plan entry that names no effect, no sound, no intensity or no level is DROPPED with the reason. Never completed from a constant, in a bridge or in `compile_manifest`.
- An alias may RENAME a capability and may not CHOOSE one. `push_in` -> `zoom_emphasis` is a fact; `slow_zoom` -> `slow_zoom_in` answered "which way?" for the planner and is withdrawn.
- Dead code that states taste is removed, not left.

**There are NO creative floors, and there must not be again.**
The creative direction decides how many cutaways and how many sounds a piece gets, and the accepted consequence is that a thin edit is no longer caught mechanically. [why](docs/RULE_EVIDENCE.md#no-creative-floors)

- **A floor in the PROMPT is a floor.** `tests/test_no_creative_floors.py` guards every creative-planning prompt (`CREATIVE_PLANNING_STEPS`). Add a planning step, add it there.
- **A floor in a BRIDGE is a floor.** [why - the VFX post-bridge that padded the plan up to every eligible block and survived the ruling by living in code](docs/RULE_EVIDENCE.md#the-default-that-outvoted-the-plan)
- **A floor that CUTS is still a floor.** `audio_reactive_sfx.scale_sfx_density` deleted half the plan's impacts because a constant said the piece was "moderate". Deleted, not unwired.
- **`tests/test_no_creative_floors.py` reads CODE as well as prompts.** It drives the real bridges of every step in `CREATIVE_PLANNING_STEPS` and asserts on their output. Reading only prompts, or listing only the steps a ruling named, is how three floors survived.
- A COVERAGE requirement is not a floor: "every non-speech block MUST have B-roll" stays, because an uncovered block fails `_assert_timeline_fully_covered`.
- `_assert_sfx_distributed` stays: it catches a collapse (every SFX on one frame), not a sparse plan.
- **A floor in a REVIEW step is still a floor.** `creative_cohesion` (5.03) reports the counts under `cohesion_review.measurements` and judges none of them; a pace check there needs a pace the creative direction DECLARED, which no step emits. **The step is therefore a pure OBSERVER**: every proposal it can still make routes to `OWNED_UPSTREAM`, so `adjustments` is empty for every input at every energy (#272). `ACTIONABLE_AT_COHESION` has an applier and no producer, which `library/tools/cohesion_scope.py` states and `tests/test_cohesion_scope.py::test_the_step_is_a_pure_observer` pins off the step's own source. **Do not read an empty `adjustments` as a clean bill of health.**
- **How long a drawn transition holds comes from the PLAN.** The handoff asks for a `duration_feel` on every one; step 4.02's post-bridge renders that word into frames. A brand template's `transition_duration_ms` `{min, max}` is a RANGE, so it BOUNDS that choice and never replaces it; a scalar is a declared length. A drawn transition that neither declares is DROPPED with the reason, not held for a constant.

**Sound-effect selection is one enumeration, `library/tools/sfx_library.py`, and the model names a FILE.**
`load_sfx_catalog` merges the library's three index files - `sfx_index.json`, `library_semantic.json` and `profiles/*.json` - into one row per playable sound, and step 4.04's bridge writes the WHOLE catalogue to the step's own directory and puts a REFERENCE to it in the prompt as `sfx_catalog_reference`.
The answer names an `sfx_id` out of it. [why - the two sounds 001 shipped, what the library said about them, and the word list that chose them](docs/RULE_EVIDENCE.md#the-sfx-chooser-was-a-word-list)

- **There is no type vocabulary and no keyword matching.** `TYPE_KEYWORDS`, `match_sfx_file` and `available_sfx_types` are deleted, not unwired.
- **An entry that is not on disk is not in the catalogue**, and `resolve_sfx_id` matches EXACTLY. No nearest neighbour: a near match is a chooser.
- **A plan naming a sound the library has not got fails in step 4.04**, whole and by name, not three steps later inside `compile_manifest` and never by dropping the entry - a dropped entry ships an edit missing a sound nobody decided to cut.
- **Step 5.04 PLACES; it does not choose.** The plan carries `sfx_id`, `source_file` and `source_in`, and `compile_manifest` reads them.
- A sound's PLACEMENT is keyed on its measured `envelope_shape`, never a per-type constant.
- **How long a sound plays is the PLAN's decision, BOUNDED by what the file measures.** One enumeration, `library/tools/sfx_duration.py`. `duration_seconds` is optional on every plan entry; declaring none plays the whole sound, which is the ABSENCE of a decision. A request past the file's measured length, less whatever the transient trim skipped, is REFUSED BY NAME in step 4.04 and never clamped. **The only floor is the timebase** - two frames, one at level plus one of de-click ramp - and it is mechanical: how short a sound should be is the plan's call and no minimum may be added. **A sound cut short carries a one-frame de-click ramp**, delivered as OTIO volume keyframes by `otio_mix.declick_curve`. [why - the run of record's own 0.25s, and the measured discontinuity](docs/RULE_EVIDENCE.md#the-plan-could-not-say-how-long-a-sound-plays)
- **The whole library ships and nothing is shortlisted.** Whatever selects a shortlist becomes the chooser. The library's FAISS index and per-entry `embedding` go unused: retrieval is what you need when you cannot show everything, and everything fits.
- **It ships BY REFERENCE, through the mechanism `brief_reference` already built (#295, see 10.1).** `sfx_library.catalog_document` is the shape - one `##` section per sound, titled with the exact `sfx_id` an answer must name and opening with the sound's measured facts, so the map names **every sound by id with category, length, envelope and temperature** and a LINE RANGE for the prose. **A reference that narrowed the menu would be the shortlist problem again.** [why - the measured inline and map sizes](docs/RULE_EVIDENCE.md#the-catalogue-was-copied-into-the-prompt)
- `library/steps/step_4_04_plan_sfx/handoff.md` is frozen and its toolkit table still names `foley`, `ambient` and `reverse_cymbal`, which the library cannot play. They name nothing the model can emit - the schema asks for an `sfx_id` - but the table is the captain's to correct.
- A sound and a transition are named by the SAME identifier, `spine_block_position`, so `transitions_toon` is keyed by the block a cut leads into and pairing them needs no join.
- **The candidate table says what the BED is doing under each block**, as `music_behavior` and `bed_under_it` with `sfx_candidates_legend` beside them (`music_measurement.bed_under_block`). 001's one sound plays at -14 dB at the exact frame the bed goes `prominent` at -6, and nothing in the pipeline predicted whether it would be heard. **It states a LEVEL and never a TARGET**: what separation a sound should have is the same undeclared decision `SEPARATION_TARGETS_DB` is empty for.
- **A non-speech block names no `clip_id` on the spine, and that is not un-measurability.** Both this table and 4.03's read `not measured (no source clip)` on every one of them - 5 of 13 rows on 001 - while `b_roll_assignments` names the covering cutaway in the same prompt. `library/tools/broll_coverage.py` is the join, and the two tables say different things with it: 4.03 gets a real camera description of the cutaway, and this one gets an ADMITTED ABSENCE with a reason, because a cutaway is placed `video_only` and its own audio is never heard.
- `tests/test_sfx_choice_from_the_catalogue.py`, `tests/test_sfx_duration.py`, `tests/test_sfx_catalogue_by_reference.py`, `tests/test_sfx_hears_the_bed.py`, `tests/test_broll_coverage_reaches_the_tables.py`.

**Music selection is one enumeration, `library/tools/music_selection_contract.py`.**
The bridge catalogues `PIPELINE_MUSIC_LIBRARY` **and** the project's `music/` and picks nothing.
The post-bridge judges source, catalogue membership, duration plausibility and a justification naming the registers the creative direction forbids.
Choosing from OUTSIDE the library is legitimate and stays allowed.

**Nothing refuses a track on rights, and no rights model may be built.**
Captain's ruling 2026-08-28: *"just assume for everything that you already have a licence ... so song choices need to be made on creative decisions - not if a license exists or not"*.
Where a track came from is RECORDED as `provenance` - the query, the URL, the channel, and whatever the platform stated - and read by nothing.
`tests/test_music_search.py` fails if any code path branches on a licence.

**Search is one enumeration, `library/tools/music_search.py`, and a project declares it or gets none.**
`pipeline.music_search` in `project.yaml` carries `queries`, `results_per_query` and `fetch_limit`; a project that declares nothing searches nothing and the catalogue says so, and a declaration missing a bound is refused by name.

- **The project writes the queries.** Composing one in code out of the creative direction's `target_mood` would be the engine writing the search terms, which is §10.5's fabrication under another name.
- **A result is judged on duration BEFORE anything is downloaded**, off the metadata the search returns for free.
- **A fetched candidate is MEASURED before the model sees it**, through the same `measure_candidates` pass a local track takes, and it is fetched through `download_track.download_audio` - the one fetch path.
- `yt-dlp` is in `requirements.txt` with a measured floor - 2026.03.17 answers every download `HTTP Error 403`. [why - the measured search, download and bridge cost](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)
- `tests/test_music_search.py`, and [`docs/MUSIC_SOURCING.md`](docs/MUSIC_SOURCING.md) §5 for the table.

**Two candidates that are the same recording are established from the MEASUREMENTS, never the filename.**
`library/tools/music_duplicates.py`. [why - the two of 001's four candidates that were one recording, and how the 1.0 dB tolerance was measured](docs/RULE_EVIDENCE.md#a-third-of-the-choice-set-was-a-copy)

- The tolerance is measured, not picked: 1.0 dB.
- `true_peak_dbtp` is NOT compared, and `DECLINED_SIGNALS` says why: lossy coding moves it most and it says least.
- **A duplicate is MARKED, not dropped.** The defect was that the model could not tell; removing a row would have the pipeline choosing which encode the captain gets.
- `tests/test_music_duplicates.py`.

**Which SECTION of the track plays is the model's decision, and there is no best-section rule.**
`library/tools/music_section.py`. [why - the literal `source_in: 0.0` that made 2.04's frozen handoff ask for nothing](docs/RULE_EVIDENCE.md#the-splices-that-reached-nothing)

- The model is asked for `section: {source_in, why}` through the manifest's `interface.llm_outputs`, which is what builds the injected schema; `handoff.md` is frozen and is not touched.
- It decides from `music_measurement.track_sections` - one row per playable span of the track, with its mean level and spread. A DESCRIPTION at the granularity of what plays, not a menu and not a ranking.
- **A selection that declares none plays from the head of the file, and that is the ABSENCE of a decision** - the same reading `CUT_TYPES` and `NEUTRAL_CDL` get.
- **The beat grid moves with it.** `beat_positions`/`downbeat_positions` take the selection and return TIMELINE time; the argument is required, because a default of "no offset" is the value that is silently wrong. `assert_music_offset_is_the_chosen_section` holds the other end. `plan_sfx` is routed `music_selection` for this.
- `resolve_section` RAISES rather than sliding a section back to fit: moving the start is choosing which part plays.
- `UNSUPPORTED_BY_THE_MEASUREMENTS` records what a section choice cannot yet see - the SHAPE of a non-zero section, the bar lines, whether it has vocals. Say what is missing; do not fill it with a rule.
- `tests/test_music_section.py`.

**Which SECONDS of a chosen cutaway play is decided from the PICTURE, never from its audio.**
One enumeration, `library/tools/cutaway_window.py`. A cutaway is placed `video_only: True`, so the
clip's own audio is never heard. [why - the post-bridge that centred every window on that muted clip's RMS peak, the seven windows it decided, and the motion rule that disconfirmed nothing](docs/RULE_EVIDENCE.md#the-cutaway-window-came-from-a-muted-waveform)

- `AUDIO_SIGNALS` is the enumeration of what may not reach the decision, and
  `tests/test_cutaway_window.py` reads the SOURCE of both the module and step 3.02's post-bridge and
  fails if one of those keys is indexed or `.get()`'d there. A behavioural test alone is not enough.
- **The candidate spans are scene boundaries UNION the vision pass's time-bounded `blocks`.** A
  strategy needing >= 2 scene boundaries gets them on almost no clips.
- **The model's own `preferred_moment` chooses**, matched against what the vision pass observed
  during each span. The engine resolves words to seconds; it does not decide that a busier or
  brighter span is better.
- **`DECLINED_TO_RANK` records every signal that is measured and deliberately not ranked on**, with
  the reason - `motion_energy`, `camera_motion`, brightness, saturation and face presence are all in
  it, because that a moving shot makes better B-roll than a still one is taste.
- **Every basis is recorded on the placement** as `window_basis`. `single_span` and `undiscriminated`
  are the ABSENCE of a decision and are spelled differently from `moment_match` so a reviewer can tell.
- **`usable_ranges` is read through its METHOD** (§10.3): a measured range excludes a window outside
  it, an `unmeasured` one filters nothing.
- The candidate rows plus `CANDIDATE_LEGEND` are the shape a prompt would carry, so letting the model
  name the window itself needs no edit to the step's frozen `handoff.md`.
- `tests/test_cutaway_window.py`.

**Every candidate is MEASURED, and nothing about it is classified.**
`library/tools/music_measurement.py` is that half: integrated loudness, loudness range, RMS spread, the envelope over the played window, true peak and the share of energy in the speech band.
A mood, a genre or an energy word computed here would be the taste fabrication this section forbids.

- **The measurements ship with `MEASUREMENT_LEGEND`**, because step 2.04's `handoff.md` is under a captain freeze and cannot name the columns. It defines what a key IS; it never says what to conclude.
- **A candidate the duration check already rejected is not opened**, and says so rather than leaving a blank column. That is mechanical - it cannot be selected either way.
- `DECLINED_MEASUREMENTS` records what was left out and why, BPM included (2.06 measures tempo properly, after the choice). Widening the table is not a way to improve the prompt - every column is paid on every candidate. [why - what the candidate data costs 2.04's context](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)
- The bed's own level is what decides whether a planned `music_behavior` offset lands - see §10.4.
- The played window's envelope is the section starting at 0; `track_sections` is how every other span compares.
- **Where the candidates come from**: [`docs/MUSIC_SOURCING.md`](docs/MUSIC_SOURCING.md) §5.
- `tests/test_music_measurement.py`.

## 11. Third-Party Asset Licenses

**Anything added to `library/presets/` from an outside source needs its licence recorded here before it lands.**

**No third-party look assets ship.**
A look is declared by a brand template as CDL plus Fusion values, and this repository ships none of its own - see §12.
`tests/test_color_grade_delivery.py` fails if any `.drx` reappears. [why](docs/RULE_EVIDENCE.md#the-unlicensed-powergrade)

**One third-party asset does ship, with its licence.**
Montserrat, as `remotion-subtitles/public/fonts/Montserrat-Variable.ttf` - a variable font covering the 100-900 weight axis, which is every weight `library/tools/subtitle_style.py` can ask for.
It is licensed under the **SIL Open Font License 1.1**, which permits redistribution including commercially and requires the licence to travel with the font; it does, as `public/fonts/OFL-Montserrat.txt`.

- **Bundle fonts; never import one over HTTP.** [why](docs/RULE_EVIDENCE.md#the-webfont-race)
- `tests/test_bundled_fonts.py` fails if the font or its licence goes missing, if a font is imported over HTTP again, or if a template names a font that is neither bundled nor explicitly accepted as a system font.

**A declared typeface must be one that really draws the glyphs.**
One enumeration, `library/tools/render_fonts.py` - bundled, accepted as a system font, or carried by the project as a `font_file` staged out of `<project>/brand_assets/` by `prep_remotion`.
Anything else raises, because Chromium substitutes its fallback sans and the frames are still valid pictures of the right size. [why](docs/RULE_EVIDENCE.md#timed-text-drew-in-the-wrong-face)

## 12. The look

**There is no house look.** The engine ships no slope, no saturation, no contrast, no glow, no grain and no vignette, and a project gets a grade only where a brand template it NAMED declares one.
`library/tools/house_look.py` is the reader for that declaration and holds no values of its own. [why - the four looks that were removed, and what 001 loses](docs/RULE_EVIDENCE.md#there-is-no-house-look)

A look is delivered in two halves, because that is what the mechanisms can express:

- **CDL** carries hue and level - slope (highlights), offset (shadows and the black floor), power (midtones), saturation - applied by `SetCDL` in `resolve_build_timeline`.
- **Fusion** carries what a CDL has no term for - pivot contrast, glow, grain, and a shaped, optionally coloured vignette - and reaches the picture only through the parameter names `fusion/comp_builder.build_effect_comp` dispatches on (§10.2).

- **`LOOK_ELEMENTS` is the whole vocabulary**, and an element outside it is REFUSED by name. Each row says which half delivers it and why that half and not the other.
- **An element is declared WHOLE or refused.** A glow with a gain and no threshold cannot be finished without the engine choosing the missing number, which is the defect this section exists for. Same shape as `bookends` (§13): raise, never drop and never complete.
- **No element has a default and none has a bound.** How strong a glow is, and how far a slope may travel, are the declaring author's decisions; an engine-supplied range is a strength nobody chose arriving one level up.
- **A project declaring no look gets NOTHING** - not a reduced look and not exposure normalisation. `NEUTRAL_CDL` is identity and `fusion_look` is `{}`, so no clip gets a comp for the look's sake at all. This is the shape #297 established for every other brand slot (§10.1).
- **A vignette is drawn only where one was asked for.** `build_effect_comp` used to default `vignette` to True, so any clip carrying a zoom got one at blend 0.25 and soft 0.35 - two strengths arriving through a `.get` default rather than through a plan or a template.
- **Exposure is MEASURED, and normalised only onto a reference the declaration carries.** Step 5.01 records every clip's average luma with its method and sample count; a clip nothing measured carries `null` and a reason, never `0.0`. The engine used to hold the target as the constant `122.0` and the clamp as `0.5` stops - both decisions about how bright the finished video is, taken by nobody. `exposure_reference` is the declared target, and the offset is then `log2(reference / measured)`, which is the definition of a stop rather than a choice. [why - the parser that discarded every sample](docs/RULE_EVIDENCE.md#the-exposure-probe-measured-nothing)
- `tests/test_house_look.py`, `tests/test_color_grade_delivery.py`.

## 13. Intros, outros and end cards

One enumeration, `library/tools/bookends.py`.

- A brand template declares `content.bookends`; a template that declares nothing gets nothing.
- A declaration names either an `asset` that already exists or a `composition` to render, plus a `duration_seconds`. **A malformed declaration raises rather than being dropped.** [why](docs/RULE_EVIDENCE.md#bookends-only-on-some-videos)
- **A card takes the same path as every other clip.** `mesh_spine` turns each declaration into an `intro_card`/`outro_card`/`end_card` spine block (NOT `intro`/`outro`, which already mean a non-speech pacing beat), step 4.06 renders the composition-mode ones, `compile_manifest` emits a V1 clip, and the renderer places it. That is what puts a card inside the coverage assertion, the manifest duration and render QA.
- **A card the PLAN wrote REFUSES the step, by name.** `bookends.assert_no_invented_bookends` raises `InventedBookendBlock` from 2.05's post-bridge naming every offending block; it used to drop them with a line on stderr. The plan around a card is written knowing the card is there, so dropping it ships an edit designed for a moment it no longer has - and a log line forty minutes into an unattended run is read by nobody. Same shape as `UnplayableSfxPlan` in 4.04 (§10.5). `intro` and `outro` are NOT cards and are never dropped. [why - and where the prompt contradiction actually lived](docs/RULE_EVIDENCE.md#the-card-that-vanished-into-a-log-line)
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

**Timed text is artwork, and the project wins.**
A timed text moment is copy the viewer reads, so it is ARTWORK and belongs with the project - which is what makes a timed-text declaration legal at all under question three.
One enumeration, `library/tools/timed_text_overlay.py`.

- A moment is timed from the SPINE (`block` + `anchor` + `offset_seconds` + `duration_seconds`). Absolute `start_frame` stays available and is bounded by the spine's real length, and a malformed declaration raises.
- Moments whose spans touch are grouped into one rendered segment, because two clips cannot share frames of a track.
- `timed_text_render.py` renders each inside step 4.06, `compile_manifest` carries them as the `timed_text_overlay` manifest key, and `resolve_build_timeline` places them on **V6**.
- The bookend route declares its artwork project-side by naming a `source:` under `content.bookends`; a line of copy has no file to point at, so timed text needs its own half. A project's `project.yaml` may carry its own `effect.timed_text_overlay`, and it replaces the brand template's whole slot - the same project-over-template precedence `delivery_format_name` uses. `timed_text_overlay.resolve_declaration` is that half, and step 4.06 calls it.
- **The card states its own `y`**, because geometry is normalised against the whole delivery frame and not the picture area inside letterbox bars. Keep `y` in 0.35..0.65 to land over picture whether the source letterboxes or fills. [why - the measured rows, and the worked example](docs/RULE_EVIDENCE.md#the-night-card-y-band)

## 15. Notes the captain types onto the timeline

The captain reviews a built timeline **inside DaVinci Resolve** and drops markers on it carrying
natural language - what looks wrong, what to change, what to go and find out.
One enumeration, `library/tools/marker_feedback.py`, which reads them and writes them to disk.
It is proved against a real running Resolve by `tests/test_marker_feedback_against_resolve.py`;
its recorded per-call findings are in the module docstring, in the shape `neural_engine.py` uses.

    python3 -m library.tools.marker_feedback pull  --project <dir>   # collect, durably
    python3 -m library.tools.marker_feedback show                    # read, write nothing
    python3 -m library.tools.marker_feedback check --project <dir>   # exit 2 if uncollected

- **A MARKER CARRIES TWO PIECES OF TYPED TEXT AND BOTH ARE READ.** `GetMarkers()` returns `name`
  (the Add Marker dialog's **Name** field, where the cursor lands) and `note` (its **Notes**
  field). Reading only `note` loses everything typed into Name, silently, with the marker still
  on the timeline. Both are kept verbatim; nothing is summarised, truncated or normalised.
  `Timeline.AddMarker` REFUSES a marker whose name is empty, and returns False saying so.
- **A timeline marker's frame is relative to `Timeline.GetStartFrame()`; `TimelineItem.GetStart()`
  is absolute.** A clip marker's frame is a SOURCE frame, the same space as `GetLeftOffset()`, so
  `timeline_frame = item.GetStart() + (key - item.GetLeftOffset())` and only inside the range the
  clip plays. Resolve bounds-checks neither. A key outside that range is kept UNPLACED with the
  reason, never clamped to the clip's head.
- **`TimelineItem.GetProperty("Comments")` is always None.** Clip comments are a MEDIA POOL
  property. A timeline item's property dict holds transform keys only - read it with no argument
  (§5) before trusting a name.
- **The record goes to `<project>/marker_feedback/`, and that is why `Kind.CAPTURED` exists.**
  Everything under `pipeline_output/` is `Kind.OUTPUT` - safe to delete because a re-run
  reproduces it. A typed note is the opposite: irreproducible, and destroyed by precisely a
  re-run. One file per pull, never overwritten, safe to run repeatedly and while Resolve is open.
- **The build path REFUSES to delete a timeline carrying uncollected notes.** Step 6.01 deletes
  any timeline whose name matches before rebuilding, so a re-render used to take the captain's
  markers with it. `guard_timeline_deletion` runs before the first delete and fails the build,
  naming the notes and the one command that clears it. It refuses rather than warning because a
  warning forty minutes into an unattended `--full-auto` build is read by nobody, and clearing
  the refusal costs one read-only command. `PIPELINE_DISCARD_TIMELINE_MARKERS=1` is the explicit
  override.
- **The reader READS, and the two things that write to a marker write only `customData`** -
  the capture button and the decision stamp, both below. Neither creates a
  marker the captain did not make, neither touches `name`, `note`, colour or duration, and no
  marker is ever deleted. No colour vocabulary: the captain chose typed notes over colour
  codes, so colour is recorded and read by nothing. No acknowledgement marker is written back.
  This is not wired into the dashboard; the whole point is that the editor stays in Resolve.

### Where a note goes

**A collected note is routed to the step that owns the decision it is about, and an
ambiguous one is reported as ambiguous rather than sent somewhere.**
One enumeration, `library/tools/marker_routing.py`.

    python3 manage_project.py notes <slug-or-path>            # who got what
    python3 manage_project.py notes <slug-or-path> --write     # + ROUTED-NOTES.md

- **A CLIP note and a MOMENT note are different things and are never flattened together.**
  A `clip_marker`, `media_pool_marker` or `clip_comment` is attached to the clip it was typed
  on and carries `clip`; a `timeline_marker` is about a MOMENT and carries `clips_under`,
  which is CONTEXT. Reading "the clips at this frame" as "the clip this note is about" hands
  a note typed on a V2 cutaway to whatever was on V1 underneath it.
  `marker_feedback.MarkerNote.attached_clip` records the placement; a pull file written
  before that field existed has it RECOVERED from §15's frame arithmetic, and the recovery is
  refused - reported `unresolved` - unless the candidates are one clip. Resolve's linked
  audio and video items of one placement, agreeing on file, timeline range and source range,
  are the one clip they are.
- **`STEP_DECISIONS` is the whole of what a note can be routed to**, each row naming the
  decision that step makes. A step outside it cannot be routed to, and a note naming one is
  refused BY NAME.
- **Two bases, and two non-answers.** `declared` - a `step: select_broll` line typed into the
  marker, or a `route` record in its `customData` - is authoritative. Otherwise the note's own
  words must name EXACTLY ONE step's decision. Two is `ambiguous`, naming both; none is
  `unrouted`. There is no score, no ranking, no tie-break and no default: `WITHDRAWN_ROUTERS`
  records why highest-term-count, nearest-neighbour and ask-a-model are all out, and why the
  clip under the playhead decides nothing. A note routed to the wrong step is worse than one
  reported as ambiguous.
- **Delivery is `prompt` or `report`, declared per step.** A step with a `handoff.md` declares
  the `timeline_notes` input and `gather_step_inputs` hands it `prompt_block()` - the words
  plus a legend, the route `MEASUREMENT_LEGEND` and `CUTS_LEGEND` take, because the handoffs
  are frozen. A deterministic step has no prompt at all; the note is still routed, recorded
  and reported, with that reason stated. `run_pipeline.project_step_context` restores
  `timeline_notes` BY NAME, so a `context_fields` allow-list neither has to list it nor can
  drop it (§10.1).
- **Nothing may silently drop a routed note.** `assert_deliverable` fails the run when a note
  is routed to a prompt step whose manifest does not declare the input, because a context
  assembled without it reads exactly like a run with no notes. `undelivered` accounts for
  every note that reaches no prompt, by name.
- **A note that reached NOBODY is named in the run summary and recorded on the note.** Three
  markers were pulled on 001 and one reached a prompt; the other two - one routed to a
  deterministic step with no prompt, one `ambiguous` between two - were in `ROUTED-NOTES.md`
  and in nothing a run said, so the captain thinks they were heard. The summary prints them
  after `status` is decided and `record_non_delivery` appends them to the same log the
  deliveries go to. **It stops at visibility**: `WITHDRAWN_ROUTERS` records why every tie-break
  was refused, and resolving an ambiguity is the captain's call, not the engine's.
- The delivery log is APPENDED by the runner, in the `Kind.CAPTURED` area beside the pull
  files: a delivery is a thing that happened, and a later run delivering the same note does
  not unmake the record of the first. `ROUTED-NOTES.md` is generated from the pull files and
  never hand-edited.
- `tests/test_marker_routing.py`, whose note fixtures are the three the captain really typed.

### What decided the clip the note is on

**Every clip on the built timeline carries the decision that produced it, and the routing
reads that instead of inferring - but only where inference has nothing.**
One enumeration, `library/tools/timeline_decisions.py`, and it is the producer half of the
loop above.

    python3 -m library.tools.timeline_decisions ledger --project <dir>   # build and write it
    python3 -m library.tools.timeline_decisions show   --project <dir>   # read, write nothing
    python3 -m library.tools.timeline_decisions stamp  --project <dir>   # onto a live timeline

- **`TRACK_DECISIONS` is one row per track of the manifest, and a track with no row is
  REPORTED, never attributed to the nearest step.** Two rows are worth stating outright:
  **V1 A-roll is `speech_sequence` (2.02), not `assign_aroll` (3.01)** - 3.01 is
  deterministic and reads `block["clip_id"]` and `block["source_start"]` straight off the
  spine, so it chooses no clip and no range; and **a bookend card is not stamped at all**,
  because it is a brand template's DECLARATION (§13) that no step weighed against an
  alternative. `UNSTAMPED_PLACEMENTS` records both. A stamp naming a step that decided
  nothing reads as an answer.
- `DECISION_BASES` keeps `chosen` and `declared` apart, the same line §10.5 draws.
- **A1 is not a placement.** `LINKED_AUDIO_OF` says so: `compile_manifest` builds A1 from the
  same V1 clip dicts, and surveying it would stamp two records where the timeline has one clip.
- **The build writes a LEDGER and creates NO marker.** A marker is drawn on the timeline
  ruler, so thirty the captain did not ask for would be a visible change to their timeline in
  exchange for a payload the UI cannot show. Step 6.01 writes
  `pipeline_output/steps/6_01_render/timeline_decisions.json` from the manifest alone, and
  merges the decision into any marker ALREADY on the timeline through
  `UpdateMarkerCustomData`, which leaves `name`, `note`, colour and duration untouched. The
  capture button stamps the marker it makes, beside the still.
- **The stamp ranks BELOW the captain's own words.** A stamp says what PRODUCED the picture;
  their words say what the note is ABOUT. Measured on their own notes: *"why is this fully
  blurry, is it the zoom blur applied wrong?"* sits on a V1 A-roll clip stamped
  `speech_sequence`, while its words are ambiguous between `plan_transitions` and `plan_vfx` -
  where a blur holding for a whole clip is really decided. So the order is declared >
  vocabulary > stamped, and the stamp closes the UNROUTED case without touching the routed
  ones. `STAMP_RANKS_BELOW_THE_WORDS` is the record.
- **A MOMENT note is never routed by the stamp**, only a note attached to ONE clip. That is
  the captain's own selection, not the stack at a frame, so it is not
  `marker_routing.WITHDRAWN_ROUTERS["the_clip_under_the_playhead_decides"]` coming back. What
  was playing under a moment is recorded as `decision_context` and routes nothing.
- A marker's own stamp outranks the ledger, because it was written when the marker was made
  and the ledger describes the LAST build. Neither is guessed at: a placement that matches no
  row comes back as a stated reason.
- `tests/test_timeline_decisions.py`.

### The panel beside the timeline

**The pipeline is readable from inside Resolve, and the panel's whole reason to exist is that it knows where the PLAYHEAD is.**
`resolve_scripts/VEP Pipeline Panel.py` is the entry point Resolve calls; everything it DECIDES is in `library/tools/panel/`, which imports no Qt and no Resolve and is driven by `tests/test_panel_*.py`. Install it with the same `scripts/install_resolve_scripts.sh` the capture button uses. [why - what the panel answered before the join, and the measured before and after](docs/RULE_EVIDENCE.md#the-panel-handed-the-model-a-filename)

- **The split is the rule.** Nothing under `library/tools/panel/` may import `DaVinciResolveScript`, `BlackmagicFusion` or a Qt binding; live Resolve facts arrive as `clip_context.ResolveContext`, a plain dataclass a test can build. The panel IMPORTS the repository - `project_layout.node_id_for` is the one step-id translator and the panel calls it. `tests/test_panel_boundary.py`.
- **The clip under the playhead is joined to what the pipeline measured** - the catalog id, the vision observations, the transcript of the seconds that PLAY, and which step chose the placement (`timeline_decisions`). Every fact is a READING, never a document pasted in, and what could not be joined is SAID.
- **The picture is not the topmost item.** `GetCurrentVideoItem()` answers with the highest video track, and on a finished build that is a subtitle card; `clip_context.picture_at` takes the highest track carrying FOOTAGE and `overlays_at` reports the rest. An overlay is told apart by living under `pipeline_output/`.
- **What the captain is LOOKING AT outranks what they last clicked**, and the prompt says which is which. `prompt_block` is bounded and says what it cut.
- **A large output is drilled down, never dumped.** `panel/trace.py` navigates one LEVEL at a time - 001's 2.89 MB `temporal_index` is seven readable lines - a leaf is bounded and SAYS how many bytes were withheld, and a path that does not exist is refused by name. Both readings of a step's output are offered and labelled, because `pipeline_data.json` and the per-step `output.json` can legitimately differ (§10.1).
- **The run is previewed before it starts**: the profile, the steps it will and will not fire with each reason, and the `run_scope` refusal VERBATIM. `panel/run_view.py` calls the same resolver the runner does and has no opinion of its own.
- **The panel launches the runner with the checkout's `.venv/bin/python3`, never `sys.executable`.** Resolve launches whatever Python it finds, and a run started with that dies inside a step's import; a checkout with no venv is REFUSED by name. This is the one place the panel's rule differs from the dashboard's (§4).
- **The handbrake stays advisory** and the panel never kills the runner.
- **It holds no credential**: the model is reached by shelling out to the already-authenticated `claude` CLI, overridable with `VEP_PANEL_MODEL`. Nothing secret is written into Resolve's application-support folder.
- **The FRAME under the playhead goes with the question**, because a measurement reads the whole clip and the captain is asking about one moment. `library/tools/panel/frame_attach.py` decides where the still goes, where the call runs and what the model is told; `marker_capture.grab_still` is the ONE grabber and no second one may be written. The still goes to the panel's own `~/.vep_panel/frames/` and never under the project - `marker_feedback/stills/` is `Kind.CAPTURED`, a frame the captain kept, and a question's frame is remade by asking again. The grab and the call are ONE worker job; a grab that cannot happen degrades to a text-only question carrying its stated REASON, never an error. `VEP_PANEL_NO_FRAME=1` declines it. [why - the measured cost, and the before/after on 001](docs/RULE_EVIDENCE.md#the-model-was-told-a-filename-and-not-shown-the-frame)
- **The model call runs where it can READ what the prompt points at.** The CLI will not open a file outside its working directory, and the panel's call set none, so it inherited whatever Resolve was launched with. The failure that comes out is the model answering *"I need permission to read the screenshot file"* - exit code 0, a plausible sentence, and nothing that looks like a bug. `frame_attach.call_site` names the directory and `frame_attach.reaches_the_file` is what the test pins, so the guarantee is *the call can read the still* and not *the call sets cwd* - `--add-dir` satisfies it too. Any future feature that hands the model a path depends on this. `tests/test_panel_frame_attach.py`.
- **Every slow thing goes on a worker thread** - the panel owns its loop (`StepLoop(False)`) and the heartbeat under the header is the captain's own proof that it has not stuck. Measured: Resolve answered 303 probes at p50 1.3 ms with 0 errors while the panel read 4.5 MB, rebuilt the join and waited 10 s on a model call.
- Toolkit facts that bite, all measured: `hasattr` is True for widgets that do not exist; `Stack.CurrentIndex` is broken and `Hidden` is the page switch; `Label.Pixmap` draws nothing and `<img>` in a read-only `TextEdit` does; `ui.Timer` never fires; `MinimumSize` is ignored by the layout and a stretch RATIO is not; Qt decides a string is rich text by looking for a tag.
- **A process that connected to Resolve leaves through `os._exit`, never `sys.exit`.** `fusionscript.so` does not join its own `RemoteApp` thread before its static destructor frees the pool that thread is using, so the C runtime's teardown can SEGFAULT after the work is finished - the captain sees "Python quit unexpectedly" and reads it as the panel dying. The panel's `_leave` flushes both streams and hands the status to the kernel. It is a race, so it does not reproduce on demand (0 of 14 attempts with traffic in flight); the crash report is the evidence, not a repro.
- **A column is sized to the longest value it really holds, and a truncation may never read as a word.** `ledger` rendered as `ledge` and `preflight` as `pref` - both complete English, so a cut was indistinguishable from a value. A column that cannot be widened is dropped and its value moves to the detail pane, whole.
- **A screenshot of this panel is opened and read before it is committed.** Nothing else tells a picture of the panel from a picture of what was behind it; `docs/panel/README.md` records the one that got through.
- **Footage Search is deliberately not in the panel.** `library/dashboard/footage_search.py` is the only authorised caller of the footage index (§2) and widening that is the captain's call.

### What Resolve's script host does not give an entry point

**`__file__` IS NOT DEFINED there, and an entry point verified by running it as a FILE has not been verified.**
Both entry points in `resolve_scripts/` are launched by Resolve's own script host, which defines `__name__` as `"__main__"` but does NOT define `__file__` - and `importlib`, `exec_module` and `python3 the_file.py` all define it, so every route a test or a screenshot takes hides this. [why - the two menu entries that did nothing at all](docs/RULE_EVIDENCE.md#the-menu-entries-that-did-nothing)

- **Read `__file__` in ONE place per entry point, inside a helper that answers `""` when it is absent.** Nowhere else, and `tests/test_resolve_scripts_bootstrap.py` fails on a second reader.
- **A fallback chain is evaluated LAZILY, or the last candidate can kill the first.** `_repo_root()` built its candidates as a tuple, so the `__file__` fallback raised before the stamped `REPO_ROOT` beside it - correct, and pointing at a directory that existed - was ever tested.
- **A bootstrap failure must reach the SCREEN.** Resolve puts a traceback in `~/Library/Application Support/.../logs/davinci_resolve.log` and nothing in front of the captain, so a script that dies during bootstrap is a menu item that silently does nothing. `print` is the floor - it reaches Resolve's Console before any import of ours has run - and a window built from the injected `fusion`/`bmd` goes on top of it and MAY NOT RAISE. The panel's module body cannot be reached by its own `if __name__ == "__main__":` guard, so it holds its failure in `BOOTSTRAP_ERROR` instead.
- **Verify from the MENU.** `tests/test_resolve_scripts_bootstrap.py` executes each entry point's bootstrap the way the host does - `exec(compile(...))` into a namespace with no `__file__` - and that is the substitute for a click, not a replacement for one.
- **The two copies of `_repo_root` stay two.** Its whole job is to find the repository a shared copy would have to be imported from. The duplication is held by ONE test over BOTH files rather than by one function.
- `sys.argv` and the working directory are not relied on by either file, and the same test keeps it that way.

### The button that captures the frame

Playhead on the moment, one click in **Workspace > Scripts > Capture Frame for Firstmate**, and the
frame plus whatever the captain typed is captured. `library/tools/marker_capture.py` is the whole
of it; `resolve_scripts/` is the entry point Resolve calls.

    scripts/install_resolve_scripts.sh              # the one command that installs it
    scripts/install_resolve_scripts.sh --uninstall

- **The repository is the source of truth.** The installer COPIES the entry point into the Scripts
  folder with this checkout's path stamped in, so the copy imports `library/` and cannot drift.
  Re-run it only when the checkout moves. **Nothing else may write into the application support
  folder**, and never as a side effect of another command.
- **`GrabStill` returns the GRADED, CONFORMED frame** - measured against a Deliver render of the
  same timecode at a mean absolute difference of 0.36-0.38/255, while the raw source frame is a
  different resolution and 54 points more saturated. The page it is taken on does not matter. The
  numbers, and every other call's measured return, are in `marker_capture`'s module docstring.
- **The still goes to `<project>/marker_feedback/stills/`**, the `Kind.CAPTURED` area, and the
  project is MEASURED by walking up from the timeline's own footage to `project.yaml`. The captain
  is never asked to name a file or a folder. A timeline whose footage sits under no project is
  REFUSED, never guessed at.
- **The gallery is put back.** `GrabStill` leaves the still in the current album and Resolve saves
  it; the button deletes it again and reports the before/after count.
- **A marker that is already there is UPDATED, never replaced** - `AddMarker` refuses an occupied
  frame anyway, and the captain's `name` and `note` are what must survive. The playhead inside a
  marker with a duration resolves to that marker's start frame.

### What goes in `customData`

One enumeration, `library/tools/marker_payload.py`: a versioned ENVELOPE carrying a list of
self-describing records, with the reasoning for that shape in the module docstring.

- `schema` versions the ENVELOPE, `writer_version` versions one writer's own keys, and they move
  independently so a second writer can grow without every reader relearning the envelope.
- **An attachment is a record with a `path`, not a record of a particular kind**, so a writer
  pointing at a file gets "a reader can open this" for free.
- **`customData` this module did not write is kept under `foreign`, never overwritten.** The UI
  does not show the field, so nobody would notice it going missing.
- `pull` and `show` surface attachments, and a note with one prints differently from one without.
  **A path the captain TYPED into a note is surfaced too**, told apart by `origin`, matched
  conservatively (absolute POSIX path or `file://`) and never rewritten out of the text.
- `tests/test_marker_payload.py`, `tests/test_marker_capture_against_resolve.py`.

## 16. Motion graphics

**The motion-graphics elements this pipeline may plan are one enumeration, `library/tools/motion_graphics_vocabulary.py`, and it defines AXES rather than values.**
Fifteen elements across seven functions in the edit - identify, quantify, enumerate, point, quote, punctuate, persist - each with what it is, when it earns its place, what it needs, and what it must never be used for. [why - the implicit roster of three, and the eight empty ProRes segments](docs/RULE_EVIDENCE.md#the-roster-nobody-wrote-down)

    python3 -m library.tools.motion_graphics_vocabulary            # the roster
    python3 -m library.tools.motion_graphics_vocabulary --check    # the rules, as a gate

- **An entry names a dimension; the magnitude belongs to whoever declares it.** `AXES` is the vocabulary of dimensions - timing, anchor, footprint, entrance, exit, emphasis, colour_role, type_role, copy, data, asset - and no axis has a default or a bound. `colour_role` is a role of the declaring palette and never a colour; `type_role` is a weight within the element and never a size. Nothing here fixes a colour, a duration, an easing strength or an intensity, which is the same rule §12 emptied `house_look.py` to establish.
- **Reachability is REPORTED per entry, never a filter on membership.** Four entries are `reachable_now`, ten need renderer work and one needs a measurement nothing takes. The render path is broken; a roster written around it would keep the defect after the repair.
- **`never` is not optional.** An entry that only says what a thing is teaches a model to reach for it everywhere, so every entry records refusals and `assert_roster_is_well_formed` raises without them.
- **The boundary is the whole point.** `OUT_OF_VOCABULARY` names the module that owns each near miss: a treatment of the spoken word is a caption (`subtitle_style`), a treatment of the picture is VFX (`plan_vfx`), a change between two shots is a transition (`transition_vocabulary`), a full frame of artwork is a bookend (`bookends`). An element is in this roster when it is an ADDITIVE OVERLAY carrying meaning the picture and the captions do not already carry.
- **Nothing here is keyed to one identity**, because the engine serves a daily channel and client work (§14). `channel_bug` draws a project-supplied asset; the engine ships no artwork and states none.
- **Two neighbouring decisions are the captain's and this file must not take either**: what produces the COPY a graphic shows, and whether the model authors a component or fills a props schema. An entry declares only WHETHER it needs a text payload. `COPY_SOURCE_IS_UNSET` records both; a change that would force one is a stop, not an implication.
- `roster_rows()` and `ROSTER_LEGEND` are the prompt-side route, the same shape `music_measurement.MEASUREMENT_LEGEND` takes. The whole roster ships - nothing is shortlisted, because whatever selects a shortlist becomes the chooser (§10.5).
- `tests/test_motion_graphics_vocabulary.py`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

When a rule comes from an incident, state the rule here and put the incident in `docs/RULE_EVIDENCE.md` with a `[why]` link.
A reader should be able to obey every rule in this file without opening that one.
