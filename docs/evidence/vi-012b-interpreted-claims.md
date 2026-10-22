# Interpreted claims, measured (VI-012b)

Punchlist item VI-012: *separate measurements from interpretations*.
PR 3137 (`library/tools/claim_provenance.py`) gave every world-model
claim an epistemic class - `measured`, `inferred`, `interpreted`,
`unknown` - and its own measurement is in
`docs/CLAIM_PROVENANCE_MEASURED.md`. That PR ended with one line
nobody could dispute and nobody could fix:

> No claim in these sources is `interpreted`: no human judgement has
> entered the world model yet.

This is the follow-up: **a captain's correction and a reviewer's
verdict enter the world model as claims of class `interpreted`, with
provenance**, and the class is measured on the corrections the captain
actually recorded.

## What changed

`library/tools/human_judgement.py` is the ONE way a human judgement
enters. It is a READER of records that already exist - it writes no
store of its own:

| the record | what it is | how it is read |
|---|---|---|
| `marker_feedback/*.markers.json` | the captain's notes, typed on a Resolve timeline | the note's own clip placement gives the SOURCE span under the note |
| `learned_context/learnings.json` | the captain's recorded corrections | read, and reported as UNATTACHED (see below) |

Four rules ride on it:

- **A judgement never edits the claim it disagrees with.** The captain
  saying "this cut here is jarring" does not delete the model's account
  of that second; it stands beside it, in a different class, with its
  own provenance. A disagreement between a person and a model is a fact
  about the footage's READING.
- **A judgement that cannot be placed is NOT placed.** The learned
  context's corrections name TIMELINE seconds, and the project records
  no timeline-to-source map, so they stay unattached with the reason
  attached. Attaching them to the nearest source would be the guess the
  punchlist forbids ("preserve unknown values rather than replacing
  them with plausible guesses").
- **Unknown stays unknown.** A note with no recorded time keeps
  `recorded_at: null`; the time the file was read is not the time the
  captain said it.
- **One note is one judgement.** The engine pulls the captain's notes
  every round; the FIRST pull is when he said it, and a later pull
  records when the engine looked again.

`world_model.py` files the judgements a source carries under
`judgements`, each with `provenance.class = "interpreted"` (the class
follows from the registry type `creative.correction`, whose kind is
`creative_judgment`), and `world_model query --t <second>` returns the
judgements about that second beside the model's claims about it.

## The measurement

Seven real podcast sources from the machine's source memory
(`~/.local/share/vep/source_memory`), copied to a scratch root OUTSIDE
the store (`/tmp/vi012b/lanes`, the JSON lanes only - no decode, no
model, no Resolve, no captain media touched). "Before" is the world
model built as `main` builds it; "after" is the same sources rebuilt
with the project's judgements. The census is
`python3 -m library.tools.claim_provenance <world_model.json>`.

| | before (main) | after (this branch) |
|---|---:|---:|
| claims | 52,744 | 52,815 |
| claims carrying a class | 52,744 (100.0%) | 52,815 (100.0%) |
| claims filed `interpreted` | **0** | **71** |
| claims filed `measured` | 8 | 8 |
| claims filed `inferred` | 52,736 | 52,736 |
| model descriptions filed as measurements | 0 | 0 |
| conflicting observations recorded | 20 | 20 |

Per source (claims; interpreted; model descriptions filed as
measurements):

| source digest | claims before | claims after | interpreted | defect |
|---|---:|---:|---:|---:|
| `157522998320` | 19,413 | 19,436 | 23 | 0 |
| `dca969b8ed90` | 23,920 | 23,954 | 34 | 0 |
| `f683a9759e06` | 4,217 | 4,227 | 10 | 0 |
| `c11218156a07` | 1,522 | 1,522 | 0 | 0 |
| `4fa282cea147` | 1,484 | 1,486 | 2 | 0 |
| `9b7fd11e02e5` | 1,150 | 1,150 | 0 | 0 |
| `d19007c31379` | 1,038 | 1,040 | 2 | 0 |

**The before-census reproduces PR 3137's numbers exactly** - 19,413 /
23,920 / 4,217 / 1,522 / 1,484 / 1,150 / 1,038 and 52,744 in total -
which is the check that the two measurements are of the same corpus.

**No claim was lost and no value altered.** A key-by-key diff of the
two world models of every source reports added keys `[]`, removed keys
`[]`, and changed keys only the new `judgements` on the five sources
that carry one. The +71 claims are exactly the 71 interpreted ones.

### The judgements that entered

The captain's project (`lucie/geo-podcast`) holds 145 distinct
recorded human judgements: 83 notes typed on a Resolve timeline and 62
corrections in the learned context.

| | count |
|---|---:|
| judgements recorded | 145 |
| attached to a source | 69 |
| unattached, with the reason recorded | 76 |
| judgement-source pairs (a judgement may bear on two sources) | 71 |
| sources carrying at least one judgement | 5 of 7 |

The 76 unattached are the 62 learned-context corrections (they name
timeline seconds and the project records no timeline-to-source map) and
14 notes whose moment carried no clip of a catalogued source (a note on
a caption card, a note on a project-built overlay).

### One judgement, checked against the record

The note the captain typed at frame 270 of Reel 01 - *"this cut here on
craig is a little jarring and does't actually make sense"* - is pulled
with the clip placement under it: `LCATL0011.MXF`, timeline frames
270..577, source frames 2572..2879. The note's frame 270 therefore sits
at source frame 2572, which is **107.274 s** of that source, and the
world model files the judgement there with the captain's words and the
pull it came from.

### What the numbers do not claim

- **No human REVIEW VERDICT was recorded in this project.** The
  `verdict` kind exists and is exercised by a test, but the corpus
  measured here is corrections only; a project that had recorded
  review verdicts would file them the same way.
- **The 62 unattached corrections are not lost.** They stay in the
  project's learned context, where the pipeline already reads them, and
  `python3 -m library.tools.human_judgement report <project>` names
  them and why they are not attached to a source.
- **The class does not make a judgement true.** `interpreted` says a
  person said it, not that the footage agrees. The census counts the
  class; it does not weigh the claim.

## Reproducing

```sh
# the machine's stored lanes, copied to a scratch root (never the store itself)
python3 -m library.tools.human_judgement report <project>
python3 -m library.tools.world_model build <project> --memory-root <scratch>
python3 -m library.tools.claim_provenance <scratch>/<digest>/world_model.json
```
