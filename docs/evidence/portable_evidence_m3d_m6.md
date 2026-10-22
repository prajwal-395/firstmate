# The M3d and M6 producers, lifted onto the portable record

Code: `library/tools/portable_evidence.py`.
Tests: `tests/unit/context/test_portable_evidence.py`.
Model: `docs/PORTABLE_EVIDENCE_MODEL.md`.
Continues: `docs/evidence/portable_evidence.md` (the M1 lane).

**The rule.** A portable evidence record names the SOURCE it was read
from, the epistemic CLASS of the claim and the COVERAGE behind it. A
reference that does not land inside the record's own span does not name
a place in the source, and it is DROPPED, by name: a wrong reference is
believed, and a believed wrong frame is worse than a missing one. A
standing between two sources is a RELATIONSHIP, and it names both
sides.

**What happened.** PR 3163 lifted the M1 transcript lane onto the
portable record and left the other producers of the same reel counted
and named. This lane lifts the next two: `m3d` (the person-region
frames) and `m6` (the multicam offsets). Reading the stored model of
**Reel 26 - write-for-the-question-your-customer-ask** of the podcast
project `lucie/geo-podcast` (read-only) found two things the earlier
lane's rule could not carry:

- **The M3d rows' references name an address in another space.** All
  18,064 of them are in the pre-contract spelling `source_frame`, and
  the index in that spelling is the ordinal of the SAMPLED frame
  (`frame_000001.jpg`, `frame_000002.jpg`, ... at 2 Hz), not the
  source's own frame number. The source runs at 24000/1001 fps, so the
  frame measured at t=0.083 s is the source's frame 2, while the
  reference says frame 0; by the last sample the two are ~90,000
  frames apart. Upgraded as they stand - the obvious reading of "the
  pre-contract spelling is upgraded and never written again" - every
  one of the 18,064 records would recover the WRONG original frames.
  The record's own span is the place in the source: it is the exact
  instant the region was measured at, on the source's own clock.
- **The M6 rows carry no reference at all, and they are not an
  interval of one source.** Their contract's `references` list is
  empty, and what they measure is a standing between two assets: how
  far this source's clock sits from its group's reference. Lifted as
  an interval they would name one source for a claim about two.

**The fix.** Three rules, each refusing by name:

| Rule | Refusal |
|---|---|
| A legacy frame index that does not land inside the record's own span | "the pre-contract frame index N is not a frame of the span it was measured over" |
| A span that ends more than one frame past the source's own length | "the span is not a span of this source" |
| A standing that names no second source | "the relationship names no other source" |

One frame is the tolerance, because one frame is the unit a source's
own length is measured in: the M6 rows' spans end 20 to 59 ticks
(0.2 to 0.5 ms) past the asset's measured duration, which is the
duration's own rounding, not a span on another grid. The overshoot is
recorded in the record (`migration.span_end_overshoot_ticks`).

**Measured** on that reel, before and after, per producer.

M3d - the person-region frames, 18,988 rows:

| Measure | Before | After |
|---|---:|---:|
| rows | 18,988 | 18,064 records (95.1%) |
| distinct record shapes | 1 | 1 |
| carry a source reference | 18,064 (95.1%) | 18,064 (100%) |
| ...in the pre-contract spelling | 18,064 (100%) | 0 (0.0%) |
| ...naming a place in the source | 0 (0.0%) | 18,064 (100%) |
| carry a provenance class | 18,064 (95.1%) | 18,064 (100%) |
| carry a coverage | 18,064 (95.1%) | 18,064 (100%) |
| carry all three | 18,064 (95.1%) | 18,064 (100%) |
| file a model's claim as a measurement | 18,064 (100% of the rows with a contract) | 0 (0.0%) |
| refused, by name | - | 924 |

The 924 refusals are the `region_track` summary rows: they carry no
evidence contract at all, so they have no class, no coverage and no
registry type to lift, and the model refuses them rather than
approximating them. The class the region rows now carry is the one
their producer DECLARED - `person.region_frame` is an inference, made
by a model - and the stored filing (`measured`) stays beside it as
`filed_class`, so the disagreement stays visible.

M6 - the multicam offsets, 3 rows:

| Measure | Before | After |
|---|---:|---:|
| rows | 3 | 3 records (100%) |
| distinct record shapes | 1 | 1 |
| carry a source reference | 0 (0.0%) | 3 (100%) |
| carry a provenance class | 3 (100%) | 3 (100%) |
| carry a coverage | 3 (100%) | 3 (100%) |
| carry all three | 0 (0.0%) | 3 (100%) |
| file a model's claim as a measurement | 0 (0.0%) | 0 (0.0%) |
| refused, by name | - | 0 |

Each one is a `relationship`: the subject is the source it was
measured on, the predicate is `sync.multicam_offset`, and the object
is the group's reference, named by its content digest - the same name
on every machine, under any path. The reference itself names the
AUDIO the offset was measured from (the words it matched are in the
sound), and the record says that the source carries four audio
channels and that the choice between them is a choice.

**Fidelity.** No value changed. Every migrated record carries the
stored contract's value verbatim, and the record's interval is the
stored interval, unchanged. What changed is the REFERENCE: 18,064
records that pointed at a sampled-frame ordinal now point at the exact
span on the source's clock, which is where the measurement was taken.
That is a fidelity repair, not a regression: before, the reference
named a frame that does not exist at that place in the source.

**The whole reel, before and after** (the stored model's 95,653
observations; the migrated producers' 94,446 of them):

| Measure | Before | After |
|---|---:|---:|
| records | 95,653 | 93,506 migrated (97.7% of 95,653) |
| distinct record shapes | 18 | 1 |
| carry a source reference | 18,064 (18.9%) | 93,506 (100%) |
| carry a provenance class | 94,282 (98.6%) | 93,506 (100%) |
| carry a coverage | 94,282 (98.6%) | 93,506 (100%) |
| carry all three | 18,064 (18.9%) | 93,506 (100%) |
| file a model's claim as a measurement | 79,173 (82.8%) | 0 (0.0%) |
| refused, by name | - | 940 |

The 940 refusals are 924 M3d track summaries and the 16
`cross_modal_disagreement` rows PR 3163 already named.

**The artifact.** `docs/benchmarks/2026-10-22-portable-evidence-m3d-m6.json`
is the census over the stored model, before and after, with the
per-producer denominators.

**How the artifact was produced, and the one difference.** The census
ran on the stored model itself (`project_model/snapshot.json`, 354 MB,
read-only) before and after this change, and every measure of the
`m3d`, `m6` and `m1` producers in the artifact is the stored model's
own. The artifact was then re-emitted from a COPY of that model -
taken on 2026-10-21 by another lane and left in the machine's scratch
area - because the reading process lost the machine's grant on the
project folder part-way through the run. The copy reproduces the
stored model's reel-26 scope exactly for every producer this lane
migrates; it carries 31 FEWER `reel-build` rows (431 against 400), so
the whole-reel `records` and `lossy` totals in the artifact read
95,622 and 47,217 against the stored model's 95,653 and 47,248. The
`reel-build` rows are refused by name either way - they carry no
evidence contract and their intervals reach 1e43 ticks - and they are
not one of the producers this lane migrates.

**Left.** Two producers of this reel's records are still counted and
named, not lifted: `m3b` (773 rows) and `m0` (3 rows), plus the reel
build's own 431 rows, which are refused rather than migrated. The
Project Model's events, relations, tracks, masks, signals and identity
links are still counted by the census and not yet lifted: a track
needs its own kind and its own registry type, and the Project Model's
schema has no `evidence` field on them yet.
