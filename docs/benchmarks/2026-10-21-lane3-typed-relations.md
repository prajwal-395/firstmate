# Lane BL-2: the typed relations a model actually holds (2026-10-21)

## What this measures

The grounding programme's B-10 lane: **typed entity relationships** -
`touching, holding, looking_at, speaking, entering, leaving, interacting`.
The audit (`firstmate home data/scout-g-b-audit/report.md`, B10 and
defect D2) measured the gap: on the one populated Project Model on
this machine, **869 relations, 100% `relation_class=temporal`, 100%
`predicate=co_present`, 100% `candidate`**. Zero physical, zero
semantic, one predicate of seven. The hand landmarks the physical
deriver needs were measured (8,191 frames in `persons.json`) and the
model carried none of them.

This report is the before/after on the same project, the same media
and the same blind-label corpus.

## What changed in the engine

1. **The predicate vocabulary is published** -
   `library/tools/relation_classes.PREDICATES` names every relation
   predicate the engine knows, its class, its evidence bar, the lane
   that derives it and what it means. The seven the programme names
   are all there, plus `co_present`, `pointing` and
   `speaking_person_candidate`. The per-class tuples
   (`TEMPORAL_PREDICATES`, `PHYSICAL_PREDICATES`, `SEMANTIC_PREDICATES`)
   are derived from the table, not written beside it.
2. **The model validator refuses an unpublished predicate**, and a
   predicate filed under the wrong class
   (`library/tools/project_model.py`). A typo is a refusal, not a
   relation.
3. **D2: the measured hands reach the model and have an owner.** The
   migration writes M3's per-frame row (with its hands) and LINKS it
   to the roster person present in that frame
   (`_migrate_m3_frames`, `FACE_FRAME_PRESENCE_METHOD`). The deriver
   reads hands from either row shape (`_hand_bearing_observations`),
   so a model that carries the landmarks on a `face` row derives the
   contact too. A frame with two people in it carries no single owner,
   and the deriver invents none.
4. **A person region is not an object.** M3d files an unowned person
   mask under an entity of kind `object`; a hand inside it is the
   person's own body, never a contact with a thing
   (`_is_region_track`).
5. **M10 reaches the model with published predicates.** M10's
   `holding` (a hand meeting a hand) is the published `touching`;
   possession (`holding`) needs an object, and M10 measures none. A
   two-handed grip on no object is no longer a self-relation
   (`M10_PREDICATES`).
6. **`speaking` is derived** (`derive_speaking_relations`): the subject
   is the person a measured voice turn belongs to, the object is
   another person whose measured presence overlaps the turn - on the
   same camera, or on another camera of the same conversation placed
   on the speaker's clock by the model's own measured time transform.
   The edge is a co-occurrence, never an addressee claim, and it says
   so in its own semantics.
7. **The scorer reports per predicate** (`score_relations`,
   `per_predicate`), and a person-to-person edge is not scored as a
   false positive against a person-to-object label (`entity_kinds`).

## The measurement

Project: `ren-scratch-breaktest` (a Ren-owned scratch copy; the
captain's projects, timelines and media were not touched). The source
memory was copied to `/tmp/vep-bl2/memory` and the lanes were rebuilt
THERE, so the shared cache and the captain's project state are
unchanged.

    python3 docs/benchmarks/vep-bl2-typed-relations-bench.py \
        --project ~/Documents/content_stuff/video_projects/ren-scratch-breaktest \
        --memory-root /tmp/vep-bl2/memory \
        --labels <firstmate home>/data/vep-labels-r1-a/round2/vep-mim-l3-identity

### The lanes, and why they were rebuilt

| lane | stored state | why | rebuilt |
|---|---|---|---|
| M3 (persons) | `vision-helper-v1 over M2` - a superseded instrument, so `slot_is_fresh` refuses it and the migration writes no frame row | the refusal is correct: a v1 record is not a v2 measurement | yes, in the scratch copy, for 5 of 7 sources (3,707 frames) |
| M3d (regions) | present for 2 of 7 sources, and older than the rebuilt M3 | M3d reads M3, so a rebuilt M3 makes M3d stale | yes, for the smallest source (429 frames, 29 s) |
| M10 (relationships) | absent everywhere | M10 reads M3, M3b and M3d | yes, for the sources that have M3d |
| 1.06 (object segmentation) | **absent on every project on this machine** | there is no object box anywhere to measure a contact against | no |

### Before and after

| measure | before (stored lanes, old code) | after (rebuilt lanes, new code) |
|---|---:|---:|
| relations in the model | 869 | **1,267** |
| `co_present` (temporal) | 869 | 889 |
| `speaking` (temporal) | 0 | **378** |
| physical relations | 0 | 0 (see below) |
| semantic relations | 0 | 0 (by design: a model interpretation owns them) |
| `verified` / `candidate` | 0 / 869 | 0 / 1,267 |
| measured hand landmarks in the model | **0** | **3,440** (on 3,707 frame rows) |
| hand rows linked to a person | 0 | 175 |
| M10 relationship candidates in the model | 0 | 9 |

Isolating the two changes (same code, different lanes; same lanes,
different code):

| lanes | code | relations | by predicate |
|---|---|---:|---|
| stored | old | 869 | `co_present` 869 |
| stored | new | 1,185 | `co_present` 869, `speaking` **316** |
| rebuilt | old | 889 | `co_present` 889 |
| rebuilt | new | 1,267 | `co_present` 889, `speaking` **378** |

The code alone adds 316 typed edges on the footage the machine already
had; the rebuilt lanes add 62 more (the two M3d region tracks of the
rebuilt source join the co-presence set) and the 3,440 hand landmarks
that defect D2 had dropped.

### Per predicate

| predicate | class | derived by | measured on this footage |
|---|---|---|---:|
| `co_present` | temporal | `derive_temporal_relations` | 889 |
| `speaking` | temporal | `derive_speaking_relations` | 378 |
| `touching` | physical | `derive_physical_relations` (person-object), M10 (person-person) | 0 |
| `holding` | physical | `derive_physical_relations` (a contact sustained 1 s or more) | 0 |
| `looking_at` | physical | M10 | 0 |
| `pointing` | physical | M10 | 0 |
| `entering` | physical | published; no lane derives it (the model carries no scene entity) | 0 |
| `leaving` | physical | published; no lane derives it (the model carries no scene entity) | 0 |
| `interacting` | semantic | published; a model interpretation owns it, and it is never verified | 0 |

### Why the physical predicates are zero here - measured, not inferred

- **`touching` and `holding` (person and object).** The deriver needs
  an object box. **No project on this machine carries a 1.06
  segmentation** (`ls */pipeline_output/steps/1_06_object_segmentation`
  is empty in every project, and the machine's source memory holds no
  segmentation artifact), so there is no object to touch. The hands
  are in the model (3,440 rows) and the deriver now reads them; there
  is nothing for them to touch. A person-region mask is not an object
  (change 4), so the 881 unowned person regions in this model are no
  longer offered to the deriver as things.
- **`touching`, `looking_at`, `pointing` (person and person).** M10
  measured **0 spans** on this footage: it is a two-camera podcast -
  each source carries one person, so no two hands meet, no head-pose
  ray meets another person, and no hand points at another face. The 9
  M10 candidate observations are in the model; they carry no span.
- **`entering`, `leaving`.** Published, and no lane derives them: the
  model carries no scene entity for a person to enter.
- **`interacting`.** Semantic by design. The validator refuses a
  semantic relation that is `verified` and requires its certainty to
  be `interpreted`; a model interpretation owns the edge.

### The blind-label corpus: what it could and could not score

The existing corpus is `vep-labels-r1-a` and `vep-labels-r1-b`,
`round2/vep-mim-l3-identity`: 7 windows each over `IMG_1807`,
`IMG_1808`, `IMG_1816` (calibration) and `IMG_1812`, `IMG_1820`,
`IMG_1821`, `IMG_1822` (held out). Agent A labels 4 contacts and 0
pickups; agent B labels 6 contacts and 0 pickups.

Those windows name **Apple footage this model does not hold** (the
model's 7 assets are the podcast sources). The scorer is therefore
run with **no comparable label**: precision and recall are
**undefined**, and the benchmark prints `uncovered` rather than a zero
that would read as a score. `score_pickups` is the same: the corpus
has 0 pickup labels, and the model has 0 `pick_up` events, so the
denominator does not exist.

What the corpus says about the state of the world is unchanged and is
not improved by this lane: the calibration report
(`docs/benchmarks/2026-10-16-lane3-relations-calibration.md`) measured
**0 physical relations, 0/2 contact recall, and no pickup denominator**
against it, with the same reason this lane measures here - there are no
object tracks to score.

**The corpus was not extended.** Scoring the Apple windows needs a
model over that footage with 1.06 object tracks, and the object
tracker is the blocker, not this lane. Building it was out of this
lane's budget and is named as the next step below.

## The defect

**D2 - the physical deriver can never fire on a migrated model.**
Before: the deriver looked for `modality == "face_frame"` rows
carrying hands, the migration wrote no such row (M3's stored
instrument was superseded, so the frame rows were refused), and the
model's 8,191 measured hand frames were dropped. After: the frame
rows are written, linked to the person in the frame, and read by the
deriver from either row shape. The hands are in the model; the
relation still needs an object box, and the measured reason for its
absence is above.

## What was not done

- **No 1.06 object tracks were produced.** They are the one thing
  between this lane and a measured `touching`/`holding` number, and
  the machine has no 1.06 output on any project.
- **The Apple windows were not scored.** The corpus needs a model over
  that footage with object tracks; the corpus is unchanged.
- **`entering` and `leaving` are published, not derived.** The model
  carries no scene entity, and this lane did not mint one.
- **No semantic relation is derived.** `interacting` is published for a
  model interpretation to own.

## Reproducing

    # a scratch copy of the source memory, so the shared cache is untouched
    cp -a ~/.local/share/vep/source_memory /tmp/vep-bl2/memory
    # rebuild the lanes the deriver needs, in the scratch copy
    python3 -m library.tools.person_measurements build <project> \
        --memory-root /tmp/vep-bl2/memory --clip clip_001 --clip clip_002 \
        --clip clip_004 --clip clip_005 --clip clip_007
    python3 -m library.tools.person_regions build <project> \
        --memory-root /tmp/vep-bl2/memory --clip clip_004
    python3 -m library.tools.relationships build <project> \
        --memory-root /tmp/vep-bl2/memory
    # the measurement
    python3 docs/benchmarks/vep-bl2-typed-relations-bench.py \
        --project <project> --memory-root /tmp/vep-bl2/memory \
        --labels <corpus dir> --out /tmp/vep-bl2/after.json
