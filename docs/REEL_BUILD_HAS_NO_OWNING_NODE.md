# Reel BUILDING has no owning DAG node, and forcing one would produce a false contract

Measured on `834a29b` (origin/main), 2026-09-06.

> **ACTED ON, 2026-09-06.** The captain authorised the second process this
> document asked for. `library/processes/reels/` now exists with `build_reels`
> and `verify_reels` nodes, `reel.build` and `reel.verify` are in the operation
> registry with DERIVED contracts, and `manage_project.py build-reels` runs that
> process rather than reaching past it into `reel_build`.
>
> Everything below is the measurement that led there and is kept unchanged,
> because the three refusals are still the reasons those nodes are not in
> `edit_video`'s graph. Two paragraphs at the end - "Until that exists" and the
> `data`-binding deferral - have been superseded and say so where they stand.

Proposing reels is now addressable - `reel.candidates` and `reel.select` are in
`library/tools/operations.py`, owned by `select_reels`, and their contract
refuses. **Building** an approved reel onto a Resolve timeline is not, and this
is why, so the next person does not spend the same afternoon on it.

## What was checked

`Operation.owning_node` is not a label. `Operation.requires` is DERIVED from it -
every requirement in `library/tools/requirements.py` whose `consumers` include
that node - so the owner decides the contract, and a wrong owner produces a
contract about the wrong thing. Three candidates were checked against what
`library/tools/reel_build.rebuild_reels_in_project` actually reads.

What the build consumes, read off the function:

| what | where it comes from |
|---|---|
| `reel_proposals_v2.json`, with at least one moment `approved` | `reel_proposal.read_proposal`, the captain's review file |
| the timeline transcript | `pipeline_output/scratch/timeline_transcript/transcript.json` |
| `resolve.project_name` / `resolve.timeline_name` | the project's own `project.yaml` |
| a LIVE Resolve project and its master timeline | `resolve_project_exactly`, `snapshot_timeline` |
| rendered caption overlays | `reel_subtitle_segments`, which drives 4.01 and 4.05 |

It reads no `assembly_manifest`, no `audio_spine`, and no step output at all.

### `render` (step 6.01) - REFUSED

The obvious-looking home: it is the node named "Render & Assemble in Resolve"
and its body is the one that puts clips on a timeline. Its derived contract is
one requirement:

    state.render.assembly_manifest   produced by compile_manifest

Registering a reel build there gives a contract that is wrong in **both**
directions, and the second is the fatal one:

- it would **refuse** a project that has an approved reel plan and a live master
  timeline, because `compile_manifest` never ran - a refusal for a reason that is
  not true;
- it would **pass** a project that has an assembly manifest and not one approved
  reel in it, and then die inside `read_proposal`. The contract would not bite on
  the only thing that matters here, which is the captain's ask inverted.

`render`'s output is `render_output`: one rendered file from one manifest. A reel
build produces nineteen timelines and renders nothing.

### `select_reels` (step 3.04) - REFUSED

It owns the choice, and the build consumes the captain's **approval of that
choice**. A node cannot require its own output, so the approval gate cannot be
derived here either. The step's decision is which conversations are worth a
short; the build's decision is none - it is execution.

### A new node IN EDIT_VIDEO'S GRAPH - REFUSED

Inventing one to make the registration typecheck is the move this document
exists instead of. A node that no run schedules, with no producer and no
consumer, would be a fourth thing in `project_layout.STEPS` /
`run_scope.DESELECTED_BY_DEFAULT` whose only purpose is to give an operation a
name to point at.

This is NOT the refusal that `build_reels` and `verify_reels` later overturned.
They are nodes of a DIFFERENT graph, they have a producer and a consumer between
them, and something schedules them: `manage_project.py build-reels` walks
`library/processes/reels/dag.json`. What stays refused is a reel node inside a
pipeline that produces one video from footage.

## Why no node fits, stated plainly

The DAG describes the production of **one video from footage**. Reels are a
SECOND product, derived from a master timeline that already exists, and the whole
chain that makes them - transcript, selection, approval, build, conformance -
starts from an external input rather than from the top of the pipeline. Two of
its five stages are already outside the DAG by construction:
`timeline_transcript` needs Resolve open and WhisperX loaded, and the approval is
the captain's.

**The honest structure is a second process** beside `library/processes/edit_video`
- its own `dag.json`, its own manifests, its own nodes for build and verify -
reusing the same steps 4.01/4.05 the caption path already reaches through the
registry. That is a design decision with a cost, and it belongs to the captain,
not to a registration that has to typecheck today.

~~Until that exists, the build stays where it is: `manage_project.py build-reels`
-> `reel_build.rebuild_reels_in_project`.~~ **SUPERSEDED.** It exists.
The reels process's own runner (`library/processes/reels/run_reels.py`, which
`build-reels` calls) takes its node order off
`library/processes/reels/dag.json` and runs each node through the operation
registry, so the requirements below are checked BEFORE anything connects to
Resolve. `reel_build` is still the only implementation of the build - the
operation resolves to `step_7_01_build_reels/step.py`, which calls it - and no
copy of it was made.

## Two things measured on the way that are worth keeping

**1. `select_reels` had ZERO requirements, and that is now fixed.**

Both its DAG edges (`audio_spine` from `mesh_spine`, `creative_direction` from
`creative_direction`) map inputs its manifest declares OPTIONAL, and its one hard
input - `timeline_transcript`, `required: true` - has no producing edge at all,
because its producer is a CLI tool. `run_scope.prerequisites` walks edges, so
`derive_state_keys` derived nothing, so an operation owned by `select_reels`
could not have refused for any reason.

Meanwhile `run_pipeline.gather_step_inputs` **raises** when it is absent - the
mid-run crash `requirements.py` exists to move to before the run starts. And
`run_scope.DESELECTED_BY_DEFAULT` already carries the workaround in prose:
*"Running it by default would crash on the missing transcript."*

`requirements.derive_runner_injected_keys` expresses it, still DERIVED - the
consumers are read off the manifests. Measured over the whole tree, these are the
required inputs with no producing edge, and only one KIND of them can be absent:

| node | input | can it be absent? |
|---|---|---|
| `scan` | `project_folder` | no - a whitelisted global |
| `build_reels` | `project_folder` | no - the same global |
| `verify_reels` | `project_folder` | no - the same global |
| `select_reels` | `timeline_transcript` | **yes, and it is by default** |
| `build_reels` | `timeline_transcript` | **yes** |
| `verify_reels` | `timeline_transcript` | **yes** |

The three transcript rows are ONE requirement with three consumers, and the two
new ones were not added anywhere: `derive_runner_injected_keys` reads which
manifests declare the input required, so the reel process inherited the whole
contract by declaring the input. That is what "derived, never hand-listed" buys.

`tests/test_operations.py::test_the_transcript_is_the_only_required_input_no_edge_carries`
re-measures this ACROSS EVERY PROCESS, so a new one cannot appear unnoticed.

**2. An operation whose step body takes the whole input as `data` cannot
`execute`, and this is pre-existing.**

`Operation._arguments` binds only parameters the function names, and a
bridge/post_bridge body takes one parameter called `data` holding the merged
dict. It is not a key in the gathered inputs, so `{}` is bound and the call
raises `TypeError`. Five operations were already in that shape before this change
- `duration_zone.build`, `motion_graphics.render`, `color_grade.resolve`,
`validation.resolve` - and `reel.candidates` / `reel.select` join them.

~~It is **not** fixed here, deliberately.~~ **FIXED, 2026-09-06**, and the
deferral's reasoning is what shaped the fix rather than being discarded by it.

The reasoning was: the runner builds a post-bridge's `data` as *step inputs +
pre-bridge output + the model's answer* (`run_pipeline.py`, `merge_data`);
`Operation.gather` returns the step inputs alone. Binding `data=gathered` would
hand a post-bridge a dict missing the model's answer and let it produce a
confidently wrong result instead of raising. A `TypeError` is honest; a silently
incomplete `data` is not.

Both halves of that are true, and the resolution is neither binding blindly nor
raising `TypeError`:

- **A PRE-bridge's `data` IS the step's inputs**, so the gathered dict is
  complete and the call is not merely non-crashing, it is the same call the
  runner makes. `duration_zone.build` now executes;
  `tests/test_operations_execute.py::test_an_operation_whose_body_takes_the_merged_dict_executes`
  runs it and reads the project's own declared duration back out of the result.
- **A POST-bridge REFUSES**, naming the keys the model owes it and the two ways
  to supply them. The keys come from `run_pipeline.llm_output_declarations` -
  the same function `present_llm_step` asks the model with - so the guard and
  the schema cannot disagree about what the model owes.

The count was seven, not five: `sfx_library.validate` takes the whole dict under
the name `inputs`, and `select_reels`' post-bridge takes it twice as
`resolve(llm_output, data)` because its own `main()` calls `resolve(data, data)`.
`operations.MERGED_INPUT_PARAMETERS` is those three spellings, asserted complete
against the tree by
`test_no_operation_takes_a_merged_dict_under_a_name_this_module_does_not_know`.

**3. Step 3.04 told every project its transcript was in `lucie/geo-podcast`.**

Found by the grep that pins the transcript path to one spelling.
`step_3_04_select_reels/post_bridge.py:179` carried

    "The FULL absolute transcript is available at: "
    "/Users/prajwal/Documents/content_stuff/video_projects/lucie/geo-podcast/"
    "pipeline_output/scratch/timeline_transcript/transcript.json"

as a literal inside the `approval` string the step writes into its own
output - so a run for a client's project, or for the engine's own test
project, was handed the podcast field test's path. The engine serves a daily
channel and client work and states no series' own paths (AGENTS.md 14). It now
derives the path from `project_folder`, and claims nothing when there is no
project folder to derive it from. Both directions are pinned in
`tests/test_select_reels_post_bridge_subprocess.py`.

## What was NOT touched

`library/tools/reel_build.py`. The two transcript-path spellings inside it keep
composing the path by hand rather than calling
`timeline_transcript.transcript_path`.

The deferral was taken because PR #568 (`fm/vep-caption-provenance`) was in
flight over `rebuild_reels_in_project`. It **landed as `a26050e`** and did not
touch either line, so what remains is an ordinary debt in the build path - the
one file this change deliberately does not enter, for the same reason the build
is not registered above. The exemption is asserted by COUNT rather than
remembered, in
`tests/test_operations.py::test_the_transcript_path_is_spelled_once_outside_reel_build`,
so fixing those two fails the test and forces this paragraph to be deleted.
