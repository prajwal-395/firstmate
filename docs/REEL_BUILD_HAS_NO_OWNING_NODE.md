# Reel BUILDING has no owning DAG node, and forcing one would produce a false contract

Measured on `834a29b` (origin/main), 2026-09-06.

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

### A new node - REFUSED

Inventing one to make the registration typecheck is the move this document
exists instead of. A node that no run schedules, with no producer and no
consumer, would be a fourth thing in `project_layout.STEPS` /
`run_scope.DESELECTED_BY_DEFAULT` whose only purpose is to give an operation a
name to point at.

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

Until that exists, the build stays where it is: `manage_project.py build-reels`
-> `reel_build.rebuild_reels_in_project`. Nothing about that changed here, and no
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
consumers are read off the manifests. Measured over the whole tree, exactly two
required inputs have no producing edge, and only one of them can be absent:

| node | input | can it be absent? |
|---|---|---|
| `scan` | `project_folder` | no - a whitelisted global |
| `select_reels` | `timeline_transcript` | **yes, and it is by default** |

`tests/test_operations.py::test_the_transcript_is_the_only_required_input_no_edge_carries`
re-measures that, so a third cannot appear unnoticed.

**2. An operation whose step body takes the whole input as `data` cannot
`execute`, and this is pre-existing.**

`Operation._arguments` binds only parameters the function names, and a
bridge/post_bridge body takes one parameter called `data` holding the merged
dict. It is not a key in the gathered inputs, so `{}` is bound and the call
raises `TypeError`. Five operations were already in that shape before this change
- `duration_zone.build`, `motion_graphics.render`, `color_grade.resolve`,
`validation.resolve` - and `reel.candidates` / `reel.select` join them.

It is **not** fixed here, deliberately. The runner builds a post-bridge's `data`
as *step inputs + pre-bridge output + the model's answer*
(`run_pipeline.py`, `merge_data`); `Operation.gather` returns the step inputs
alone. Binding `data=gathered` would hand a post-bridge a dict missing the
model's answer and let it produce a confidently wrong result instead of raising.
A `TypeError` is honest; a silently incomplete `data` is not. Both reel
operations are reachable through `.run(...)` today, which is how
`reel_build.reel_subtitle_segments` already drives `subtitles.plan` and
`subtitles.render_segment`.

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
