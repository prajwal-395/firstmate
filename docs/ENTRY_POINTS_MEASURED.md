# The three ways in, measured

The captain, 2026-09-06:

> continue fleshing out the pipeline so that you can account for more of the
> various ways that the pipeline would have to account for (things like the
> rough cut already being an input, the song/music spine being put in already,
> a user asking for certain specific sections of the video to be re-edited
> and such)

This is what the pipeline did for each of the three BEFORE anything was
changed, how that was measured, and what is still owed.

Nothing here is a MODE. Each entry point is a set of state keys or an
address, and the run shape falls out of machinery that already existed -
`external_inputs.CHECKS`, `run_scope.resolve` and `operations.Operation`.
A "rough cut mode" would have been a second selection mechanism beside the
one that already derives this, which is the parallel implementation Ruling 1
forbids.

## Reproducing the measurements

Every number below comes from driving the real modules; none is read off a
log. The commands are in the sections that follow, and the two that use the
captain's own footage COPY `001/pipeline_data.json` into a scratch project -
nothing writes into a real project and Resolve is never opened.

---

## 1. The rough cut is already an input

### What the pipeline did

`external_inputs` already accepted three of the six keys a rough cut is
made of, and `run_scope`'s own docstring already claimed the right rule:

> **A recorded output does not remove a step from the run; a SUPPLIED one
> does.** History is not a request. The captain putting a value under
> `external/` is saying "do not make this", so the closure stops at that
> producer.

**It did not hold.** Driving `run_scope.resolve` with `audio_spine`,
`speech_sequence` and `a_roll_assignments` supplied:

| selection | steps | `from_external` |
|---|---|---|
| a plain full run | **26** | **{}** |
| `--target rough_cut_subtitles` | 1 | 2 keys |
| `--only plan_subtitles` | 11 | 2 keys |

On the run shape most runs use, the supply changed nothing. `mesh_spine`
and `assign_aroll` both ran, and because `gather_step_inputs` reads
`step_outputs` before it reads `external`, their output SHADOWED what the
captain handed in. A hand-made cut was silently rebuilt.

The cause is one line of `resolve`: the "leave the producer behind" branch
lives inside the closure walk, and a full run does not walk a closure -
`goals = set(universe)`, so every node is a goal already.

### What it needed, and what it now has

`run_scope.supplied_producers` derives which steps a supply stands in for,
off the same `data_mapping` edges `prerequisites` reads. ALL of a
producer's routed keys or none: `mesh_spine` hands out `audio_spine` AND
`timed_spine`, and leaving it out with only the first supplied would drop
the second in silence.

Three keys were not checkable, so the rough-cut boundary could never be
covered. They are now:

| key | what the check asserts |
|---|---|
| `timed_spine` | the spine contract, same as `audio_spine` - they are the same object today (`step_2_05/post_bridge.py:313`) and supplying one still does not supply the other |
| `b_roll_assignments` | files on disk, non-empty ranges, and NO TWO PLACEMENTS OVERLAP - V2 shows one clip at a time |
| `b_roll_interjections` | the same, and `[]` is accepted (`EMPTY_IS_A_STATEMENT`) because leaving the file out and supplying nothing are different requests |

Measured after, with all six supplied: a plain full run drops from 26 to 21
steps and reports

    NOT RUN, output supplied: music_selection, speech_sequence, mesh_spine,
                              assign_aroll, select_broll

### What is still owed

**A supplied rough cut still drags in the whole preflight.** `creative_direction`
is a hard input of `review_rough_cut` and of all four planners, and taste is
not suppliable (`external_inputs.WITHDRAWN`) - so `scan`, `catalog`,
`semantic_analysis` and `temporal_index` still run to feed it. On the
measurement above, seven steps remained under `--only plan_subtitles` and
every one of them was there for `creative_direction`.

That is a real limit and it is not a defect of this layer. Making it
optional is a change to eleven manifests and a captain's call about what a
step may run without - the same call `library/profiles/podcast.yaml` records
itself declining to make.

**`rough_cut_review` is not suppliable and must not become so.** It is a
VERDICT, and `requirements._rough_cut_approved` already distinguishes a
MISSING measurement from a FAILED one. A hand cut that no `review_rough_cut`
ever judged refuses with the missing-measurement message, and the designed
route past it is `--override rough_cut.approved`, which is RECORDED. Adding
a check that accepted `{"passed": true}` from a file would be asserting an
approval, which is the one thing `external_inputs` exists not to do.

---

## 2. The music spine is already placed

### What the pipeline did

Two routes, and both were closed.

Supplying it:

    'music_selection' cannot be supplied from outside the pipeline: nothing
    here can check it. A check that does not exist is not a check that passes.
      Checkable: a_roll_assignments, assembly_manifest, audio_spine,
                 render_output, speech_sequence

Skipping it:

    This selection cannot run. Refusing before the run starts.

      music_analysis needs music_selection from music_selection, which is
      excluded by this run and has no recorded output in this project.
      mesh_spine needs ...      audio_mix needs ...
      plan_transitions needs ... plan_sfx needs ...
      compile_manifest needs ...

Six stranded consumers. So a captain who had already chosen and placed the
bed had no way to tell the pipeline, and step 2.04 would choose again.

### What it needed, and what it now has

One check. `music_selection` is checkable by exactly the standard
`a_roll_assignments` meets - every claim is about a file on disk and a range
inside it:

* the track resolves through `requirements.resolve_track_path`, the same
  reading step 2.06 does, so the two cannot disagree about which file is the bed;
* ffprobe finds an AUDIO stream in it - a path that exists is not a bed;
* every further track in `tracks[]` is on disk, because the bed is a SEQUENCE;
* `section.source_in` and every `splices[]` range lie INSIDE the measured
  file. This is the silent failure the check exists for: a section past the
  end yields a bed of silence that nothing downstream notices.

WHY the track suits the piece is taste and is carried through unexamined.

With the derivation from entry point 1, supplying it removes step 2.04 from
every run shape, and naming it anyway is refused:

    music_selection was named on the command line, and this project supplies
    its whole output from outside the pipeline (music_selection). Running it
    would overwrite what you supplied: a step's own output is read before
    external state, so the file under external/ would still be on disk and
    nothing would read it again.

`music_analysis` still runs, and that is correct: the beat grid has to be
measured whatever chose the track.

### What is still owed

Nothing for this entry point.

---

## 3. Re-edit a named section

### What the pipeline did

The address machinery was all there - `region.parse`, `scope.Scope`,
`operations.parse_address`, `step_ledger.parse_rerun_target` - and **not one
of the six operations that declare REGION scope honoured its region.**

Measured on a three-block spine with the region covering block 1 alone:

| call | caption cards |
|---|---|
| `subtitles.plan` at PROJECT scope | 4 |
| `subtitles.plan` at REGION 2.0-4.0 | **4** |
| `generate_subtitles(scope=that region)` | **1** |

`Operation.execute` never passed the `Scope` to the step. The step had
honoured the region all along.

The full inventory, by how each failed:

| operation | scopes | what happened |
|---|---|---|
| `subtitles.plan` | project, region | ran at PROJECT scope; a whole-plan overwrite wearing a region's clothes |
| `subtitles.render` | project, region | ran at PROJECT scope; would have re-rendered every segment of the video |
| `subtitles.splice` | region | `TypeError: missing 2 required positional arguments: 'stored_plan' and 'scope'` |
| `subtitles.render_segment` | project, region | `TypeError` - `props`, `out_dir`, `timeline_label` |
| `transcript.reindex` | region | `TypeError` - `clip_id`, `source_file`, `source_start`, `source_end` |
| `transcript.splice` | region | `TypeError` - `index_doc`, `fresh_regions`, `source_start`, `source_end` |

The first two are the worse half: a crash is honest, a confidently
whole-project answer is not.

Separately, `--rerun <step>@<span>` PARSED, cleared the step's whole ledger
entry, and printed

    region 32.0-48.0 of plan_subtitles: ledger cleared; the region scope
    decides what is recomputed

That sentence was not true. Steps run as subprocesses over JSON stdin and
no step's `main()` reads an address, so the step re-ran at PROJECT scope and
redid the whole video. For `--rerun plan_transitions@32.0-48.0` that is a
model re-deciding every transition in the piece.

### What it needed, and what it now has

* `operations.SCOPE_PARAMETER` - the address is bound where the step's own
  signature names it, with the same precedence `MERGED_INPUT_PARAMETERS` has
  (a real gathered key of that name still wins).
* `Operation.unbound_parameters` / `_teach_unbound` - a signature nothing can
  fill REFUSES naming the argument, instead of crashing at the splat.
* `--set NAME=<json>|@file.json` - the way out the refusal names, so the
  suggested command runs verbatim.
* `Region.as_address()` - the spelling `region.parse` reads back. `__str__`
  ends in `s` for a human and `parse` refuses that, so a printed command
  that used it would have refused when copied.
* `--rerun <step>@<span>` REFUSES, naming the region-scoped operations for
  that node, derived from the registry rather than listed.

Proven on the captain's real footage (project 001, 13 spine blocks, 56.6s):

    region 32-48s touches spine blocks [5, 6, 7, 8, 9, 10]
    subtitles.plan  PROJECT -> 41 cards over blocks 2,3,5,7,8,10,11,hook
    subtitles.plan  @32-48  -> 22 cards over blocks 5,7,8,10
      every region block is one the region touches: True
      registry answer == the step's own answer with that scope: True

    subtitles.splice@32-48 -> completed
      stored plan 30 cards -> 36; the 14 cards OUTSIDE the region
      byte-identical: True

### What is still owed

**A section re-edit reaches the CAPTIONS and nothing else.** `plan_transitions`,
`plan_vfx`, `plan_sfx`, `assign_aroll` and `select_broll` have no
region-scoped operation, because none of those step bodies takes a `scope`
and none has a splice half. Two things each of them would need:

1. a `scope` parameter that narrows WHICH blocks it plans, the way
   `generate_subtitles` does - the cheap half;
2. a splice that can show it left the rest alone, the way
   `subtitle_splice.assert_durations_preserved` does. This is the expensive
   half and it is different per step: a transition is a property of a CUT
   between two blocks, so a region ending mid-block owns half a decision.

**The runner still cannot run a step at a region**, and closing that is not
a `Scope`-threading exercise. A step is a subprocess fed JSON on stdin, so
the address would have to be serialisable and every step's `main()` would
have to read it. Until then the operations CLI is the only route that
honours a region, which is what the `--rerun` refusal now says.

**The dashboard still reports `executable: false`.** `Operation.execute`
works now, but the runner's eighteen per-step services do not - no ledger
entry, no provenance, no gate, no handbrake. Flipping the flag without them
would create exactly the second-class unrecorded execution path
`library/tools/scope.py` was written to avoid. That is a separate piece of
work and it is a captain's call, not an implication of this one.
