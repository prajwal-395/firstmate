# The data map: what every piece of information is, and who uses it

The captain, 2026-09-07: *"continue to flush out the data management until
you have mapped out exactly what each piece of information is and where/how
it is being used (so we know that our pipeline is perfectly lean)"*.

**The map is derived, not written.** `library/tools/data_map.py` builds it
from the code and from the shape of what real runs wrote, and
`library/tools/field_flow.py` is the analyzer underneath it. This file is the
half that needs prose: what the map found, what it costs, what is not mapped,
and the answer to the leanness question.

```
python3 -m library.tools.data_map                       # the summary
python3 -m library.tools.data_map --document reel_proposals_v2
python3 -m library.tools.data_map --field 'OUT@catalog#clip_catalog[].width'
python3 -m library.tools.data_map --unread              # ranked by cost
python3 -m library.tools.data_map --phantom             # reads of absent keys
python3 -m library.tools.data_map --blind               # what it cannot see
python3 -m library.tools.data_map --bad                 # the gate
python3 -m library.tools.data_map --observe <project>   # re-measure the shape
```

`--field` answers all four questions at once:

```
$ python3 -m library.tools.data_map --field 'OUT@catalog#clip_catalog[].width'
OUT@catalog#clip_catalog[].width
  is        : number  (48 occurrence(s), 192 bytes on the run of record)
  written by: step node `catalog`
  read by   : code/prompt
      get          library/dashboard/server.py:153  (_format_resolution)
      get_default  library/steps/step_3_01_assign_aroll/step.py:34  (needs_conform)  default 0
      get          library/steps/step_3_01_assign_aroll/step.py:103  (assign_a_roll)
      get_default  library/steps/step_3_02_select_broll/post_bridge.py:48  (check_needs_conform)  default clip.get('resolution_width', 0)
      get          library/steps/step_5_04_compile_manifest/step.py:1022  (_conform_fields)
      get          library/tools/reel_conformance_verifier.py:2363  (_catalog_source_sizes)
      prompt       select_broll
  breaks    : None flows onward
```

## Why this is a third contract

`input_contract.py` asks who REFUSES when a declared input is absent.
`output_contract.py` asks who READS what a step produces. Both work on
DECLARED STEP OUTPUTS - 70 of them - and both say so. Two classes of data are
outside them, and both are where the last week's defects lived.

- **Data that is not a declared step output.** `transcript.json`,
  `reel_proposals_v2.json`, `plan_provenance.json`, the Remotion caption
  props, the run archives, `project.yaml`. **39 documents are enumerated** in
  `data_map.DOCUMENTS`, never globbed - a glob would let a document count as
  mapped because some unrelated file matched, and the enumeration IS the
  reviewable list.
- **The FIELD inside a document.** `output_contract` counts a document with
  thirty keys as one output. `frame_rate` versus `project_fps`, a caption hash
  fed the wrong object, a transcriber confidence dropped at write time - every
  one is a field.

And a third route neither models: a field can be read by no line of code and
still be load-bearing, because it reaches a **model**. 382 fields are in that
state, and the whole of step 3.04's 48 KB candidate table is one of them.

## How "who reads it" is derived, and why grep could not do it

A name match answers a different question, and this repository has paid for
finding that out twice - #601 credited six outputs to lines WRITING their own
key, and `output_contract.KNOWN_NAME_COLLISIONS` records what the read-position
fix still could not see. At output level that residue is small. At field level
it is the whole problem: `start`, `end`, `text`, `clip_id`, `duration` and
`speaker` are read in hundreds of places off dozens of unrelated dicts, so a
field-level survey built on names would report everything as read and could
not fail.

So `field_flow` does not match names. It follows the value: an intraprocedural
dataflow with interprocedural argument and return propagation, run to a
fixpoint over 260 modules in about thirteen seconds. A local variable carries a
tag naming the document and the path the value came from, and a subscript, a
`.get`, an `in` or an attribute on a tagged value is a read of that exact
field, with the file, the line, and the SHAPE.

Nine things had to work before the map could see this tree, and **every one was
found by a field KNOWN to be read coming back unread**:

| what | the field that found it |
|---|---|
| a lookup built by comprehension | `clip_catalog[].width` - `{c["clip_id"]: c for c in clip_catalog}` is the commonest idiom here |
| a key held in a variable | `music_analysis.tempo.beats` - `beat_grid._times(analysis, key)`, and nothing else reads the beat grid |
| a module constant in scope | `music_selection.section` - `music_section.SECTION_KEY`, which decides WHICH SECTION of the track plays (AGENTS.md 10.5) |
| `library/` as a second import root | most of `compile_manifest`, which spells `from tools.x` and `from library.tools.x` on adjacent lines |
| a `global` filled inside a function | `compile_manifest._STATE_OUTPUTS`, which every `load()` in that step reads through |
| a collapsed lookup key | `assembly_manifest.tracks{}.clips` - the observer folds `tracks.V1` into `tracks{}` and the renderer reads it by name |
| a document found by a GLOB | `clip_*.json` and `clip_profile_*.json` - the two largest analysis documents in the tree, which read as files nothing opens |
| a filename built in an f-string | `f"{JOURNAL_PREFIX}*.json"` - the media-pool journal |
| `Cls(**d)` | ten of `ocr_result`'s sixteen fields, reached only by a dict splat into a dataclass |

## The SHAPE is the answer to "what breaks"

Not guessed. The access shape is the only mechanical answer this repository
has to the captain's fourth question, and the shapes differ in exactly the way
that matters:

| shape | what breaks if the field is absent |
|---|---|
| `d["k"]` | KeyError - the reader raises |
| `require_keys(d, [...])` | refused by name before anything is computed |
| `obj.attr` | AttributeError, or the object cannot be constructed |
| `d.get("k")` | None flows onward |
| `"k" in d` | a branch is not taken |
| `d.get("k", 30.0)` | **a default is substituted silently** |

The last row is the shape AGENTS.md 10.5 forbids for a creative value, and the
map records the substituted expression, so `--field` prints `default 30.0`.
This is the first instrument in the tree that can enumerate them.

## What the map measures

Measured 2026-09-07 over two real projects - `001` (the `edit_video` process,
end to end) and `geo-podcast` (the reel path, end to end):

- **2,424 field paths** across **55 origins**, 25.1 MB of artefact.
- **983** have a route: 352 read by code alone, 174 by code and a prompt, 382
  by a prompt alone, 85 by a renderer outside Python, 1 by both.
- **1,441 have no route at all** - **8.3 MB, a third of every byte the
  pipeline writes.**

`library/tools/data_map_observed.json` is the committed shape snapshot: a
field path, the types seen at it, how many times it occurred and how many
bytes it is. **No values**, so it carries no project content -
`tests/test_data_map.py` asserts every path segment is a field name, an
element marker or a collapsed lookup, never a clip id, a filename or a
speaker's name.

## IS THE PIPELINE LEAN? No - a third of what it writes has no reader

**Nothing below is deleted. The captain asked to know first.** Ranked by what
removing each would simplify, which is the bytes it occupies on a real run.

| rank | what | unread / total | bytes | verdict |
|---|---|---|---|---|
| 1 | `OUT@temporal_index` | 55/93 | 2,345,004 | **a second copy, in state, of measurements read off the file** |
| 2 | `DOC@conformance_report` | 203/203 | 2,325,282 | a terminal record - nothing reads a report |
| 3 | `DOC@temporal_index_clip` | 46/65 | 1,165,393 | the file copy; 19 fields ARE read |
| 4 | `OUT@ocr_extraction` | 17/17 | 581,570 | already known: DESELECTED BY DEFAULT |
| 5 | `OUT@judge_reels` | 35/42 | 448,994 | the reel verdicts, read from the FILE not the output |
| 6 | `DOC@migration` | 49/49 | 317,241 | what `organize` moved, once |
| 7 | `OUT@select_reels` | 66/114 | 228,196 | candidate measurement - all of it prompt-only |
| 8 | `DOC@resolve_placements` | 12/16 | 188,437 | the media-pool journal |
| 9 | `OUT@compile_manifest` | 171/208 | 110,738 | the plan's unread half |
| 10 | `DOC@reel_proposals_v2` | 58/81 | 86,816 | the reel review surface |
| 11 | `OUT@mesh_spine` | 16/156 | 61,314 | the spine is 90% read |
| 12 | `DOC@provenance` | 27/27 | 57,311 | two append-only ledgers |
| 13 | `OUT@semantic_analysis` | 19/74 | 46,636 | the vision view |
| 14 | `DOC@vision_index` | 64/64 | 41,088 | an index over the vision profiles |

### 1 and 3 - the temporal index is stored twice, and the STATE copy is the redundant one

Step 1.04 writes a per-clip index file AND the same measurements travel in
state as `full_indices`. The map says which copy the readers actually take,
and it is the FILE:

```
$ python3 -m library.tools.data_map --field \
    'DOC@temporal_index_clip#camera_motion_decomposition.values[].residual'
  read by   : code
      get          library/tools/camera_stability.py:148

$ python3 -m library.tools.data_map --field \
    'OUT@temporal_index#full_indices[].camera_motion_decomposition.values[].residual'
  read by   : NOBODY
```

`vision_pipeline_v3.load_temporal_index` globs
`pipeline_output/steps/1_04_temporal_index/index/clip_*.json`, so
`camera_motion_decomposition`, `motion_energy`, `speech_regions`,
`face_presence`, `scene_boundaries` and `duration_s` are all read off the file.
The state copy of the same measurements is 2.35 MB nothing takes:

```
686,470B  full_indices[].camera_motion_decomposition
334,120B  full_indices[].energy_curve.values      (only .peak_times is read)
147,260B  full_indices[].speech_activity
```

**One exception, and it is the reason the state copy exists at all.**
`compile_manifest` calls `subject_framing.subject_centers_by_clip(temporal_data)`
on the state copy, and that is where `face_presence.face_center_x` and
`face_width` are read - the only subject-position measurement this pipeline
takes (AGENTS.md 10.3). **The map cannot see that read** and it is recorded in
`data_map.KNOWN_MISSED_READS` rather than counted as waste; see *Being
sceptical* below.

**What removing it would simplify:** `full_indices` is the largest thing in
`pipeline_data.json`, and every step downstream loads the whole file. Dropping
the three fields above from the STATE copy - leaving the file untouched -
takes 1.17 MB out of every state read in the run and changes no reader.

### 2 and 8 - two terminal records, 2.5 MB

`conformance_report.json` (2.33 MB, 203 fields, **zero readers**) is
`reel_build`'s account of what it measured against the plan.
`resolve_placements*.json` (12 of 16 fields unread) is the media-pool journal;
`list_journals` reads four of its fields to offer an undo, and the moves and
stamps themselves are never read back. Both are legitimate in the same way
`validate_sfx_library.sfx_library_status` is
(`output_contract.REPORTED_NOT_CONSUMED`): **the gate is the exit code and the
document is the account of what it looked at.** They are here because a
reader-less document nobody wrote down is indistinguishable from an accident.
**Recommendation: keep, and say so** - which `data_map.DOCUMENTS` now does.

### 4 - OCR, already decided

`ocr_extraction` is WIRED and DESELECTED BY DEFAULT, and
`output_contract.UNREAD_FINDINGS` already records that nothing reads it and
that it costs 445s per run when asked for. The field map adds two things: the
size (582 KB in the step output), and that the per-clip `ocr_result.json`
beside it IS read - 12 of its 16 fields, through `TrackedText(**d)` in
`analysis/ocr_extractor.py`. So the capability works; only its output has no
consumer.

### 5 - `judge_reels` produces 449 KB nothing reads, because the reader takes the file

`reel_quality_bar` reads the judgement from `reel_judgement.json`
(`DOC@reel_judgement`, 15 of 34 fields read); the step output carrying the same
`reel_judgement` document is read by nothing. The same two-copies shape as
rank 1, on the reel path.

### 7 - `select_reels` is prompt-only by design, and that is worth knowing

66 of `select_reels`' 114 fields have no CODE reader, and 25 reach a prompt.
`reel_candidates` - 48,892 bytes of measurement - travels only through the
bridge/`context_fields` route: nothing in Python reads a byte of it, and it is
in front of the model that chooses which passages become reels. **Two things
follow.** Deleting any of it changes what the model sees, so none of it is
free. And **step 3.04's manifest does not declare `reel_candidates` as an
output**, so `output_contract` cannot see the key at all; the field map picks
it up by reading the bridge. That is worth a manifest entry.

### 10 - the reel proposal writes four fields it never reads back

`reel_proposal.ReelMoment.as_dict` writes `timeline_name`,
`duration_seconds`, `total_duration_seconds` and `has_duplicate_take`;
`from_dict` reads none of them, and neither does anything else. They are
derived - `timeline_name` from `number` and `slug`, the durations from
`timeline_start`/`timeline_end` - so a reader that used them instead of the
properties would be reading a number that could be stale.

The larger part of the 86.8 KB is `source_spans[]` (74 KB): the raw footage
each moment plays. Its own docstring says why - *"the ground truth, carried so
a reel can be traced back without re-reading the timeline"* - so it is a
REPORTED record like ranks 2 and 8. `duplicate_takes[].first_text` /
`second_text` (6.3 KB) are the same: measured for the captain to read on the
review surface and never acted on by code, which is what AGENTS.md 10.4 says
they are for.

**Recommendation: the four derived fields go; the reported ones stay.** They
are the only entries on this whole list where removal costs nothing at all -
every reader already has the property.

### 11 - what a LEAN document looks like

`OUT@mesh_spine` is 156 fields and **16 unread**: the spine contract
(AGENTS.md 6) is the one document in this pipeline where nearly every field
has a named reader. It is the shape the rest should be measured against, and
it is why a third is a finding rather than a fact of life.

## The reel path, end to end

The brief asked for this one complete, and it is:

| origin | fields | code | prompt | none | unread bytes |
|---|---|---|---|---|---|
| `DOC@timeline_transcript` | 27 | 19 | 0 | 8 | 8,034 |
| `OUT@select_reels` | 114 | 23 | 25 | 66 | 228,196 |
| `OUT@judge_reels` | 42 | 0 | 7 | 35 | 448,994 |
| `DOC@reel_proposals_v2` | 81 | 23 | 0 | 58 | 86,816 |
| `DOC@reel_judgement` | 34 | 15 | 0 | 19 | 17,427 |
| `OUT@build_reels` | 10 | 5 | 0 | 5 | 2,613 |
| `OUT@verify_reels` | **never observed** | - | - | - | - |
| `DOC@conformance_report` | 203 | 0 | 0 | 203 | 2,325,282 |
| `DOC@plan_provenance` | 7 | 5 | 0 | 2 | 185 |
| `DOC@resolve_placements` | 16 | 4 | 0 | 12 | 188,437 |

534 fields across the whole path. **126 have a reader or a prompt; 408 have
neither**, and 2.33 MB of those 408 are the conformance report alone. One gap
is stated rather than papered over: **neither snapshot project has run step
7.02**, so `verify_reels`' output is unmapped. Re-observing a project that has
is the fix, not a change to the map.

## Reads of a key that cannot exist - 127 of them

The other direction, and a defect class rather than a cost: `--phantom` lists
every read that lands on a key **no artefact carries**, where the parent path
WAS observed. Three groups, all verified by hand:

**A documented absence** - the map found it and the code already explains it:

```
OUT@mesh_spine#audio_spine.structure[].speaker   step_4_01_plan_subtitles/step.py:701
```
> *"Absent on a spine the pipeline built itself; present on one measured off a
> real timeline, where each speaker has their own track."*

**A dead alternative in a fallback chain** - the code tries three names and
only one exists:

```
OUT@catalog#clip_catalog[].file_path        step_3_01_assign_aroll/step.py:97
```
The catalog carries `path` and `source_file`. `file_path` is the first name
tried and it has never existed. `clip_catalog[].fps`, `.filepath`,
`.duration_s`, `.resolution`, `.resolution_width` and `.resolution_height` are
the same shape, in the dashboard and in 3.02's post-bridge.

**A reader of a key the producer is not asked for** - the class
`tests/test_asked_fields_have_readers.py` enforces on `creative_direction`,
now visible everywhere else. Seven are in the dashboard:

```
OUT@semantic_analysis#semantic_analysis_documents[].mood             dashboard/server.py:912
OUT@semantic_analysis#semantic_analysis_documents[].energy           dashboard/server.py:915
OUT@semantic_analysis#semantic_analysis_documents[].overall_mood     dashboard/server.py:912
OUT@semantic_analysis#semantic_analysis_documents[].overall_energy   dashboard/server.py:915
OUT@semantic_analysis#semantic_analysis_documents[].summary          dashboard/server.py:170
OUT@semantic_analysis#semantic_analysis_documents[].interest_score   dashboard/server.py:181
OUT@semantic_analysis#semantic_analysis_documents[].detected_objects dashboard/server.py:920
```

AGENTS.md 7 says in as many words that semantic analysis *"measures no mood or
energy"*. Every one returns its default, and the dashboard renders the default
as though it were a measurement. **Not fixed here** - the dashboard is another
lane's, and this is a map rather than a fix list.

## Being sceptical of these numbers

The last two days produced 701 caption errors that were a pairing bug, a
fingerprint that hashed nothing, and a database sweep that reported zero
because it was erroring on all 1,948 columns. So the first version of this
document was checked by hand against the code, and **it was wrong about two of
its top three findings**: it said the per-clip temporal index and the vision
profiles were read by nothing, and both are read - through a `glob`, which the
analyzer could not follow. Fixing that moved 483 fields from unread to read
and took the headline from 46% of bytes to 33%. The hand check is the reason
the number is worth anything.

**The analyzer errs in ONE direction.** Every tag it fails to carry makes a
field look LESS read, never more. A field this map says is read really is read
- the evidence is a file and a line. A field it says is unread is a
**question**.

**Ten anchors are checked on every run.** `data_map.ANCHORS` names fields that
are KNOWN to be read and says how each is reached; the gate fails if the map
stops finding one. They were chosen to be the hard cases: a read through a
lookup comprehension, a parameter, a module constant, a dataclass attribute, a
named view and a prompt declaration. Two more are in `NOT_READ_ANCHORS` and
fail if they ever gain a reader.

**A second, independently written instrument corroborates.**
`output_contract._read_literals` knows nothing about documents or dataflow and
answers the same question from the only other angle: does this name occur in a
READ POSITION anywhere at all. **464 of the 1,441 unread fields (32%) have a
name that appears in no read position in the entire tree** - two instruments
agreeing, with no line anywhere that could be the missed reader. The other 977
have a name that occurs somewhere, on some other dict, and this map says
which. The two disagree in exactly one direction and on exactly six fields,
all keyed by a variable rather than a literal; they are listed with reasons in
`tests/test_data_map.py`.

**A hand check that finds a reader is recorded, not argued away.**
`data_map.KNOWN_MISSED_READS` holds the fields where this happened -
`face_presence.face_center_x` and `.face_width`, read through a nested
function's closed-over accumulator - with the reader named and the line. They
are excluded from the ranking, because a hole in the instrument is not data
the pipeline wastes. Closure support was implemented and **reverted**: sharing
free names took the analysis from 7s to 86s and multiplied the recorded
accesses 44-fold, which is imprecision rather than reach.

**The cross-check runs as a test.** Every output `output_contract` reports as
carried by a DAG edge must have at least one field with a route in this map.

## What is NOT mapped, and how much

| blind spot | size | why |
|---|---|---|
| dynamic-key reads | 5,127 | `row[key]` where `key` is not a literal and does not resolve reads the CONTAINER, not a field |
| reads on a line with more than one origin | 1,419 | `compile_manifest.load(out_dir, "<step>.json")` resolves to every step output at once; one is right and the rest are its shadow |
| tags dropped as too nested | 2,651 | no observed path has two consecutive `[]`, so `step_outputs[][][][]` is the analyzer losing a dict it built in a loop |
| reads whose parent is a scalar | 95 | one local name holding two different things in one function, which a flow-insensitive reading unions |
| reads on an origin no run produced | 22,768 | mostly the shadow above; says nothing about the pipeline |
| documents declared and never observed | 13 | each with its reason in `data_map.NOT_OBSERVED` |
| values that leave Python | 85 fields | Remotion props and the batch spec are read in TypeScript (`data_map.EXTERNAL_READERS`) |
| whether the MODEL used it | all 382 prompt-only fields | `context_fields` says a field REACHED a prompt. No static reading can say the model acted on it, and AGENTS.md 10.4 is why nothing here pretends otherwise |
| a closure reading an enclosing local | 2 known, not counted | `KNOWN_MISSED_READS`; measured and reverted rather than guessed at |
| `output.json` / `snapshot.json` | 2 filenames, 2 writers each | a read seeded on the literal cannot say which. `output.json` costs nothing (its shape IS the step's output); `snapshot.json` is one id with two shapes |

Two more, stated plainly:

- **`OUT@verify_reels` has never been observed.** Neither snapshot project has
  run step 7.02.
- **`step_3_04_select_reels` emits `reel_candidates` and its manifest does not
  declare it as an output.** 48,892 bytes reach the model through the
  bridge/`context_fields` route and `output_contract` cannot see the key.

## The gate

`data_map.disagreements()`, run by `tests/test_data_map.py`. Four checks, and
each is proved to fail in BOTH directions - AGENTS.md 10.4 calls a gate that
cannot fail and a gate that fails correct output the same defect from either
side.

- **ANCHORS** - a field the map must find a reader for, and two it must not.
  Fails when the analyzer goes blind; does not fail when new unread data
  appears.
- **UNREAD_BUDGET** - per origin, how many observed fields nothing reads. MAY
  ONLY MOVE DOWN, the same ratchet `scripts/check_agents_md_size.py` uses on
  AGENTS.md. Fails when data is added that nothing reads, and fails when a
  budget is left higher than what is owed - a line no longer needed is a lie
  about what is still owed.
- **NOT_OBSERVED** - a document declared and never produced. Fails when one is
  added with no run behind it, and fails when a recorded absence becomes
  stale.
- **KNOWN_MISSED_READS** - a hole a hand check found in the instrument. Fails
  when one closes and the entry is left standing.

`tests/test_data_map.py` has 21 tests, eight of which do nothing but break the
gate on purpose and assert it says so; `tests/test_field_flow.py` has 11 more
that hold the analyzer to the write/read distinction it exists to make, each
against a module built under `tmp_path` rather than against the repository's
own moving contents.
