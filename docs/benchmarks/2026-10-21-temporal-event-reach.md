# Temporal event reach — how far the typed index reaches on real footage

**Lane:** BL-7 of the workstream B audit (G5: *"The typed temporal surface is empty on real
footage"*), the prerequisite lane for B-11 (event state transitions).

**Date:** 2026-10-21 · **Base:** `4abb1694f` (the tip of `main` when the lane started) ·
**Branch:** `fm/g-bl7-temporal-events`

**Measure:** `python3 docs/benchmarks/temporal_event_reach_bench.py` — the same index
(`library/tools/temporal_events.build_temporal_events`) the production query reads
(`ren search --temporal`), over the eight Ren scratch projects the audit measured. Every
project is READ; the index is derived, so the measurement writes nothing.

---

## 1. The result

| project | events (before → after) | events with a state change | events with a participant | unmapped kinds (before → after) |
|---|---|---|---|---|
| `ren-scratch-breaktest` | 0 → **9 659** | 763 (7.9 %) | 7 932 (82.1 %) | 4 kinds → **0** |
| `ren-scratch-bench-demo` | 0 → **530** | 512 (96.6 %) | 512 (96.6 %) | — → **0** |
| `ren-scratch-bench-demo2` | 0 → **530** | 512 (96.6 %) | 512 (96.6 %) | — → **0** |
| `ren-scratch-retrieval-bench` | 0 → **530** | 512 (96.6 %) | 512 (96.6 %) | — → **0** |
| `vep-live-workflows-e2e` | 0 → **0** | 0 (0 %) | 0 (0 %) | — → 0 |
| `eval04-scratch-renders` | 0 → **0** | 0 (0 %) | 0 (0 %) | — → 0 |
| `axc-reel-opening-qual` | 0 → **0** | 0 (0 %) | 0 (0 %) | — → 0 |
| `axc-reel-opening-qual-hd` | 0 → **0** | 0 (0 %) | 0 (0 %) | — → 0 |

**Before:** 0 events on 8 of 8 projects; 0 % of events carried a state change; 0 % carried a
participant; `ren-scratch-breaktest` reported its four Project Model event kinds
(`cinematography_scene`, `cinematography_sequence`, `cinematography_shot`,
`cinematography_subshot`) as **unmapped**.

**After:** 11 249 typed events across 4 of 8 projects; no Project Model event kind is unmapped.

### The three projects that still yield nothing, and why

A project that yields 0 events is a failing project — so each of these is named, not excused.

| project | why it is empty | what would fill it |
|---|---|---|
| `vep-live-workflows-e2e` | It has the footage and the catalog, and a **private** source-memory scope — but that scope holds only the M2 frame index, because the preflight never ran on it. A live run says so by REFUSING: `ren edit <project> --step temporal_index --dry-run` reports *"temporal_index needs semantic_analysis_documents from semantic_analysis, which is not in this run and has no recorded output in this project"*. The vision pass is hours of GPU work, so the lane did not run it. The same media's measurements do exist in the machine-wide store — the other scratch copies of this footage read them from there — but `vep-live-workflows-e2e` has a scope of its own, and a project with its own scope does not read the shared one. | Run the preflight on it (the vision pass first, then the temporal lane), so its own scope holds the measurements. |
| `eval04-scratch-renders`, `axc-reel-opening-qual`, `axc-reel-opening-qual-hd` | Project shells: no `pipeline_data.json` catalog, no footage, no Project Model, no step output. There is nothing to index. | A run. There is no footage to run on. |

### Where the events come from

| project | source | what it reads |
|---|---|---|
| `ren-scratch-breaktest` | Project Model **and** source memory | The model's 182 typed events, its 116 059 observations (the sound events step 1.04 measured, the words the transcriber timed, the voice turns the diarizer measured, the region tracks the detector measured) and the source-memory slots for the same media. |
| `ren-scratch-bench-demo`, `ren-scratch-bench-demo2`, `ren-scratch-retrieval-bench` | source memory | The M5 sound slot (step 1.04's own output), the M4 scene slot and the M7 voice-turn slot, read by the content digest the project's own catalog declares. These projects have no Project Model of their own. |

The source-memory reads go through `source_memory.project_read_root`, the sanctioned read path:
the project's own scope when it has one, the machine-wide store when it does not. The
machine-wide store is where the measurements of that media were taken before the projects
scoped their own; it is content-addressed, so the slot is a measurement of the media and not
of the project that measured it. No captain project, timeline or media was written, no broker
was started, and the only live command that touched a project was a **dry run** on
`vep-live-workflows-e2e`, which refused before doing anything.

---

## 2. What the lane changed

Three defects, one file (`library/tools/temporal_events.py`):

1. **The model's own event kinds could not be typed.** `MODEL_EVENT_KINDS` named twelve kinds
   and not one of the four kinds a real Project Model actually holds, so every one of the 182
   events on `ren-scratch-breaktest` was reported as unmapped. The VI-09 hierarchy is now read
   as the boundary each level opens (`CINEMATOGRAPHY_READING`): a sequence, a scene and a shot
   open the **same cut**, so one cut is one event however many levels name it; a sub-shot is
   a segment of a shot, and the boundary between two sub-shots is where the picture's measured
   attributes changed.

2. **The model's MEASURED observations were ignored.** The index read the `events` collection
   and nothing else, so a model holding a whole measurement read as an empty surface. The
   observations are now read through the vocabulary that names what each one is
   (`MODEL_OBSERVATION_EVENTS`): a sound event is an `audio_event`, a scene is the cut that
   opens it, a voice turn is the change of speaker, a pause between words is a phrase
   boundary, an utterance is a span of speech.

3. **The temporal lane's own output never reached the index.** Step 1.04's measured sound
   events live in the M5 slot of the source memory; the index read neither the slot nor the
   Project Model's copy of it. The source memory is now a source of the index, read by the
   content digest the project's own catalog declares.

### The state changes and the participants

B-11 wants events that carry `state_changes` and `participant_ids`. BL-7 does not manufacture
them: it gives them somewhere to live, and it states what each one is.

| what | entity | property | before → after | evidence |
|---|---|---|---|---|
| a person's **tracked** presence | the person | `tracked` | `False → True` / `True → False` | the region-track observations that measured it |
| a voice turn | the speaker | `speaking` | `False → True` | the voice-turn observation |
| a sub-shot boundary | the asset (the picture) | `framing`, `camera_movement`, `lighting_level`, `color_character`, `motion_magnitude` | the previous level's value → this one | both readings' measurement ids |

Two readings of these worth stating plainly:

* A **track** measures that a person *was tracked* over a span. It does not measure that the
  person entered or left, so the property is `tracked` and the basis says so: *a gap in a track
  is an absence of measurement, not a measurement of absence*. This is the product ruling the
  audit's G6 asks for, applied where it can be applied without ruling on occlusion.
* A **picture attribute** is reported against the asset, because the picture is not an entity
  in the model. A state change the Project Model would accept verbatim — an entity, a
  property, evidence — is the tracked-presence and the speaking one.

### What was NOT done

* The index was not taught to *classify* anything new. Every event is a measurement the model
  or the source memory already holds, read through the existing vocabulary.
* No occlusion state, no `person_enters` / `person_leaves` action class was invented from a
  track.
* The three empty shell projects were not given a synthetic project to make the table look
  better.

---

## 3. Reproducing

```bash
python3 docs/benchmarks/temporal_event_reach_bench.py            # the table
python3 docs/benchmarks/temporal_event_reach_bench.py --json     # the rows
python3 -m library.tools.temporal_events <project>              # the index itself
```

The defect-naming tests are `tests/unit/picture/test_temporal_event_reach.py`; the vocabulary
contract is `tests/contracts/test_temporal_event_vocabulary.py`.
