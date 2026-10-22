# The portable multimodal evidence model

INFRA-001, punchlist of 2026-10-20: *"Define a portable multimodal
evidence model."* One enumeration:
`library/tools/portable_evidence.py`.

The history behind this module - the incident, the measurement and the
numbers - is `docs/evidence/portable_evidence.md` (the M1 lane) and
`docs/evidence/portable_evidence_m3d_m6.md` (the M3d and M6 lanes).

## What it is

One record shape for every piece of evidence Ren stores. A record says
what KIND of thing it is, which MODALITIES it is anchored into, whether
it is a LOSSY description of the media, and - the three attributes a
portable record must carry - the SOURCE it was read from, the epistemic
CLASS of the claim, and the COVERAGE behind it.

The model adds no second vocabulary. The source reference is the
universal one (`library/tools/source_reference.py`, PR 3116), the
epistemic class is the one the claim producers declare
(`library/tools/claim_provenance.py`, PR 3137), and the coverage kinds
are the evidence contract's (`library/tools/evidence_contract.py`).
What it adds is the part that had no home: the record's kind, its
modality anchors, its lossiness, and a version with a migration.

The tick rate is one enumeration too: the contract's `TICKS_PER_SECOND`
IS the source reference's `SOURCE_TICKS_PER_SECOND` (INFRA-003), so two
spellings of a source's clock cannot drift apart.

```
schema        ren.portable-evidence, version 1
kind          interval | entity | event | relationship
modality      video | audio | text | derived
asset_id      the immutable original media
stream_id     the stream inside it
clock_id      the clock that counts its elements
interval      {clock_id, start, end} on that clock
source_references   the universal references into the source
modality_refs       the same references, anchored per modality
provenance    {class, filed_class, producer, producer_version, method,
               measurement_type, provenance_id}
coverage      {kind, samples, scope, status, freshness}
lossy         true when the record DESCRIBES the media
semantic      the description, when it has one
value         the measurement, when it has one
entities      the entities the record is about
relation      {subject, predicate, object}, for a relationship
migration     {from_version, migrated_from, dropped_references,
               span_end_overshoot_ticks}
```

## The rules it refuses by

| Rule | Refusal |
|---|---|
| A record names the immutable original media | "a portable record names its asset_id" |
| A record carries a reference that NAMES THE SOURCE | "a portable record carries a reference that NAMES THE SOURCE" |
| A record carries a provenance class, a producer and its registry type | "provenance names its producer" |
| A record carries a coverage | "a portable record carries its coverage" |
| An unavailable coverage carries no value | "unavailable coverage carries no samples and no value" |
| A LOSSY description is never filed as a measurement | "a lossy description is not a measurement" |
| A record newer than this reader is never half-read | "this reader knows v1" |
| A span that does not fit its source is refused | "the span is not a span of this source" |
| A pre-contract index that does not land in the record's own span is dropped | "the pre-contract frame index N is not a frame of the span it was measured over" |
| A standing that names no second source is refused | "the relationship names no other source" |
| A portable document carries no local path | "a portable document names the media, not where this machine keeps it" |

## The two sections of a portable document

`sources` is the immutable original media - the asset by its content
digest, its streams, the clock that counts their elements, the rate
that clock counts in. `records` is the derived measurement, and every
record names the source it came from. A record never names another
record as its source: grounding a claim in a description of a frame is
grounding it in a caption.

The document is portable because the content digest is the name: the
same bytes carry the same name on another machine, under any path.

## Recovering the exact original frames and audio samples

A record's reference is an interval on the SOURCE's own clock, over
the stream of the modality the record was read from - a transcription
is read from the audio, a face box from the picture. The document
carries that clock's rate and the source's measured frame rate, so the
ticks resolve to frames.

Two honest limits, both visible in the document rather than hidden:

- a source with several streams of one modality (this podcast carries
  four audio channels) is a CHOICE, and the record says how many
  alternatives the clock holds (`streams_of_this_modality`);
- a span that ends past the source's own length is on another grid,
  and is refused rather than referenced. One frame of overshoot is
  the exception: a duration measured to the millisecond ends a
  fraction of a frame past the source it measured, and that is the
  source's own length, not another grid. The record says how far past
  it reached (`migration.span_end_overshoot_ticks`).

A pre-contract reference carries an index and no clock, so the index
may be an address in ANY space: a source frame, an ordinal in a
SAMPLED frame set, a number in a shot list. The record's own span
says where in the source it was measured, and an index that does not
land there does not name a place in the source: it is dropped, by
name, and the span stands alone. The M3d region rows are exactly this
case - their index is the ordinal of the sampled frame, and the
source's own frame at that moment is a different number.

## Semantic observations are explicitly lossy

A record is LOSSY when it describes the media rather than measuring it:
its payload carries a description under a descriptive key, or its
registry unit IS a description (`text`, `labels`, `regions`). A
transcription is lossy. A rate computed over a transcript is not - it
is a reading, and a reading compares.

A lossy record may never be filed as a measurement, and the model
refuses to build one.

## Migration

A record carries its version. A stored record is migrated onto the
portable shape by `migrate_observations`:

- the evidence contract is read from where it lives - the observation's
  `evidence` field or the `payload.evidence_contract` - by the ONE dual
  read (`epistemic_vocabulary.contract_of`);
- the pre-contract reference spellings (`source_frame`, `source_sample`)
  are upgraded to the modern `element` and are never written again -
  and an upgraded index that does not land inside the record's own
  span is DROPPED, by name, because an index in another space is not a
  place in this source;
- a record with no reference is given one: the span it was measured
  over, on its own clock;
- the class comes from the REGISTRY, not from the stored filing, and
  the stored filing is kept beside it as `filed_class` so the
  disagreement is visible;
- a record whose span was re-based onto a finer clock is RECOVERED on
  the row's own integers: the recorded span is the measured span times
  a whole number of grid ratios, and the recovery undoes that
  multiplication. The candidates are the multiples whose span fits
  inside the source; where more than one fits, the source's own words
  say which (a passage is anchored by SEARCH, never by a hint), and
  where one fits, the row's own measurement has determined the span;
- the source's length is counted UP on the clock's own rate, and a
  span that ends ONE FRAME past it is the source's length, rounded by
  the duration it was measured from - the record says how far past it
  reached;
- a record that cannot be lifted is REFUSED with a named reason, never
  approximated.

## The measurement

One real reel: **Reel 26 - write-for-the-question-your-customer-ask**,
of the podcast project `lucie/geo-podcast`, read-only, on the machine's
own source memory. The reel's records are the 95,653 observations of
the three source assets it cuts from.

Reproduce:

```
python3 -m library.tools.portable_evidence census \
    --model <project>/project_model/snapshot.json --reel reel-26
```

The reports: `docs/benchmarks/2026-10-21-portable-evidence.json` (the
M1 lane), `docs/benchmarks/2026-10-22-portable-evidence-m3d-m6.json`
(the M3d and M6 lanes) and
`docs/benchmarks/2026-10-22-portable-evidence-m0-reel-build.json` (the
M0 lane and the reel build).

### Distinct evidence record shapes in production

| Collection | Rows | Distinct shapes |
|---|---:|---:|
| observations (top-level) | 95,653 | 2 |
| observations (payload) | 95,653 | 18 |
| observations (record shape = both) | 95,653 | **18** |
| relations | 869 | 1 |
| tracks | 924 | 1 |
| masks | 927 | 1 |
| signals | 26,226 | 1 |
| identity_links | 431 | 1 |

### The three attributes, before and after

The BEFORE is the stored model. The AFTER is the same records lifted
onto the portable model - one producer at a time. The M1 transcript
lane first (75,455 of the reel's 95,653 observations), then the M3d
person-region frames and the M6 multicam offsets, then the M0
source-metadata lane and the reel build's own rows (434 more).>>>>>>> 342c79670 (INFRA-001: the M0 lane and the reel build's own rows on the portable record)

| Measure | Before (reel) | Before (M1) | After (M1) |
|---|---:|---:|---:|
| records | 95,653 | 75,455 | 75,439 |
| distinct record shapes | 18 | 10 | **1** |
| carry a source reference | 18,064 (18.9%) | 0 (0.0%) | **75,439 (100%)** |
| carry a provenance class | 94,282 (98.6%) | 75,439 (99.98%) | 75,439 (100%) |
| carry a coverage | 94,282 (98.6%) | 75,439 (99.98%) | 75,439 (100%) |
| carry all three | 18,064 (18.9%) | 0 (0.0%) | **75,439 (100%)** |
| file a model's claim as a measurement | 79,173 (82.8%) | 60,336 (80.0%) | **0 (0.0%)** |
| refused (named) | - | - | 16 |

The 16 refusals are named, not dropped: they are the
`cross_modal_disagreement` rows, which carry no evidence contract at
all.

The M0 lane and the reel build, measured the same way:

| Measure | Before (M0 + reel build) | After (M0 + reel build) |
|---|---:|---:|
| records | 434 | 434 |
| distinct record shapes | 2 | **1** |
| carry a source reference | 0 (0.0%) | **434 (100%)** |
| carry a provenance class | 3 (0.7%) | **434 (100%)** |
| carry a coverage | 3 (0.7%) | **434 (100%)** |
| carry all three | 0 (0.0%) | **434 (100%)** |
| refused (named) | 434 | **0** |

The M1, M3d, M6, M0 and reel-build rows together are 93,940 records
in ONE shape, and 93,940 of 93,940 (100%) carry a source reference, a
class and a coverage. The 940 refusals that remain carry no evidence
contract at all: the M1 `cross_modal_disagreement` rows (16) and the
M3d track summaries (924).

The 60,336 rows whose stored filing disagreed with their producer's
declaration keep that disagreement as `filed_class`: the correction is
in the record, and the evidence of the old filing stays beside it.

### The M3d and M6 lanes

The next two producers of the same reel: **the M3d person-region
frames** (18,988 rows) and **the M6 multicam offsets** (3 rows). The
detail is `docs/evidence/portable_evidence_m3d_m6.md`.

| Measure | Before (M3d) | After (M3d) | Before (M6) | After (M6) |
|---|---:|---:|---:|---:|
| records | 18,988 | 18,064 | 3 | 3 |
| distinct record shapes | 1 | **1** | 1 | **1** |
| carry a source reference | 18,064 (95.1%) | **18,064 (100%)** | 0 (0.0%) | **3 (100%)** |
| ...in the pre-contract spelling | 18,064 (100%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| ...naming a place in the source | 0 (0.0%) | **18,064 (100%)** | 0 (0.0%) | **3 (100%)** |
| carry a provenance class | 18,064 (95.1%) | 18,064 (100%) | 3 (100%) | 3 (100%) |
| carry a coverage | 18,064 (95.1%) | 18,064 (100%) | 3 (100%) | 3 (100%) |
| carry all three | 18,064 (95.1%) | **18,064 (100%)** | 0 (0.0%) | **3 (100%)** |
| file a model's claim as a measurement | 18,064 (100%) | **0 (0.0%)** | 0 (0.0%) | 0 (0.0%) |
| refused (named) | - | 924 | - | 0 |

The 924 refusals are the `region_track` summary rows, which carry no
evidence contract at all: no class, no coverage and no registry type
to lift. The M6 rows are `relationship` records - a standing between
two sources - and they name the audio they were measured from.

### What the measurement found, beyond the numbers (continued)

5. **The M3d rows' references name an address in another space.** All
   18,064 of them are in the pre-contract spelling, and the index in
   that spelling is the ordinal of the SAMPLED frame, not the source's
   own frame number: the source runs at 24000/1001 fps, so the frame
   measured at t=0.083 s is the source's frame 2 while the reference
   says frame 0. Upgraded as they stand, every one of them would
   recover the WRONG original frames. The record's own span is the
   place in the source, and the mis-addressed index is dropped, by
   name.
6. **The M6 rows are a standing between two sources, not an interval
   of one.** Their contracts carry no reference at all, and what they
   measure is how far one source's clock sits from its group's
   reference. Lifted as an interval they would name one source for a
   claim about two.

### What the measurement found, beyond the numbers

1. **Production files a model's description as a measurement.** Every
   contract in the stored model says `evidence_type: measurement` /
   `certainty: measured` - including the types the registry says a
   MODEL produced: a transcribed word, a scene description, a sound
   event, a face box. In this reel's scope that is 79,173 of 95,653
   records (82.8%). A reader could not tell a VLM's description of a
   room from a device's own record of the frame rate. PR 3137 gave the
   producers a way to declare their class; the stored model predates
   it, and the portable model reads the DECLARATION, not the filing.
2. **The M1 lane's rows carried no source reference at all** - 0 of
   75,455. The reference the universal vocabulary asks for was
   synthesised here from the span each row was measured over.
3. **The reel build writes rows that are not evidence at all.** The 431
   `prov-reel-build` rows carry no evidence contract, and their
   intervals reach 1e43 ticks on a source 4,099.6 s long. The store's
   own event log says why: the reel build re-based its rows onto the
   clock upgrade once per build, and wrote them only once, so a row
   left on an older grid stayed in the model forever. The recovery
   undoes that re-basing on the row's own integers, and the recovered
   spans are IDENTICAL to the spans the store's first commit carried
   (304 of 304 rows the two share). The history of this, and the
   measurement: `docs/evidence/portable_evidence_m0_reel_build.md`.
4. **The only rows that carried a reference used the pre-contract
   spelling** (`source_frame`), which PR 3116 says is read and never
   written. The migration upgrades them to the modern `element`, and
   drops the ones whose index does not land in the record's own span
   (see below).

## What is left undone

- Five producers are migrated: the M1 transcript lane, the M3d
  person-region frames, the M6 multicam offsets, the M0 source-metadata
  lane and the reel build's own rows. The one producer of this reel's
  records still counted and named is `m3b` (773 rows).
- The portable model covers the Project Model's observations. The
  events, relations, tracks, masks, signals and identity links are
  counted by the census but not yet lifted: a relationship and an event
  need their own kind and their own entity references, and the Project
  Model's schema has no `evidence` field on them yet. The M3d track
  summaries (924 rows) are refused for the same reason: they carry no
  evidence contract, so they have no class, no coverage and no
  registry type to lift.
- The reel build's rows are recovered in the model that is stored
  today. The next reel build writes them again, correctly, with the
  contract they now carry.
