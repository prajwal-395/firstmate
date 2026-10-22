# The portable multimodal evidence model

INFRA-001, punchlist of 2026-10-20: *"Define a portable multimodal
evidence model."* One enumeration:
`library/tools/portable_evidence.py`.

The history behind this module - the incident, the measurement and the
numbers - is `docs/evidence/portable_evidence.md`.

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
migration     {from_version, migrated_from, dropped_references}
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
  and is refused rather than referenced.

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
  are upgraded to the modern `element` and are never written again;
- a record with no reference is given one: the span it was measured
  over, on its own clock;
- the class comes from the REGISTRY, not from the stored filing, and
  the stored filing is kept beside it as `filed_class` so the
  disagreement is visible;
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

The report: `docs/benchmarks/2026-10-21-portable-evidence.json`.

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
onto the portable model - one producer at a time, and this is the first:
**the M1 transcript lane**, which is 75,455 of the reel's 95,653
observations.

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

The 60,336 rows whose stored filing disagreed with their producer's
declaration keep that disagreement as `filed_class`: the correction is
in the record, and the evidence of the old filing stays beside it.

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
   intervals reach 1e43 ticks on a source 4,099.6 s long. Migrating
   that producer refuses all 431 by name, on the contract first and on
   the grid second: a reference to them would recover frames that are
   not there.
4. **The only rows that carried a reference used the pre-contract
   spelling** (`source_frame`), which PR 3116 says is read and never
   written. The migration upgrades them to the modern `element`.

## What is left undone

- One producer is migrated: the M1 transcript lane. The other
  producers of this reel's records - `m3b`, `m3d`, `m6`, `m0` and the
  reel build - are counted and named, not yet migrated. `m3d` is next:
  it is 18,988 rows, and it is the only producer that already carries
  references.
- The portable model covers the Project Model's observations. The
  events, relations, tracks, masks, signals and identity links are
  counted by the census but not yet lifted: a relationship and an event
  need their own kind and their own entity references, and the Project
  Model's schema has no `evidence` field on them yet.
- The reel build's own rows (431) are refused, not migrated. Their
  intervals are wrong, and no reference can be made from them.
