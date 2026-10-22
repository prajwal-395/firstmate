# The identity lane, lifted onto the portable record

Code: `library/tools/portable_evidence.py`.
Tests: `tests/unit/context/test_portable_evidence.py`.
Model: `docs/PORTABLE_EVIDENCE_MODEL.md`.
Report: `docs/benchmarks/2026-10-22-portable-evidence-m3b.json`.

**The rule.** A portable evidence record names the SOURCE it was read
from, the epistemic CLASS of the claim and the COVERAGE behind it, and
it is LOSSY when it describes the media rather than measuring it. A
lossy record is never filed as a measurement. One enumeration:
`library/tools/portable_evidence.py`.

**What happened.** PR 3163 put the M1 transcript lane on the portable
record and named the others as pending. This lane takes the next one:
the **M3b identity lane**, which writes two collections - the
observations it measured (a face box, a diarized voice turn) and the
identity links that say who each of them belongs to.

Measuring the stored model of Reel 26 (the podcast project
`lucie/geo-podcast`, read-only, on the machine's own Project Model
snapshot) found three things the M1 migration did not have to face:

- the lane's **773 observations** carried a class and a coverage but
  **no source reference at all** - 0 of 773 - so none of them could
  say where they were read from;
- **every one of the 773** was filed as a measurement. A face box
  measured by a detector and a voice turn decided by an embedding model
  are the model's own claims, and the stored filing said `measured`
  for both;
- the lane's **11,130 identity links were invisible to the census**.
  The census scoped the links by a producer lane that named one lane
  (`m1`) plus the reel build's own, so the bulk of the identity
  evidence of the reel was counted as if it did not exist. The reel's
  identity links were reported as **431 rows in 1 shape**; they are
  **11,859 rows in 4 shapes**.

**The fix.** The lane is declared migrated (`MIGRATED_PRODUCERS`), and
two things it needs are lifted with it:

- the observations, as `interval` records, each naming the entities
  the lane's own links say it is about - a face box that says nothing
  about WHO it measured is half a record;
- the links, as **`relationship`** records - the kind the portable
  model has always declared and nothing filled. A relationship names a
  subject, a predicate and an object: the SUBJECT is the thing the
  media was read from (this face track), the OBJECT is the entity it
  stands to (this person), the same way a standing between two
  sources names the other source as its object. A relationship is
  LOSSY (who a face belongs to is a description of the media, not a
  reading of it), and it is grounded in the SOURCE it was measured
  over rather than in the observation it rests on.

One fidelity correction came with it: a **voice turn is a reading of
the SOUND, not of words**. It was carried as a `text` record - the
modality of a transcription - while carrying no words at all, so a
reader looking for the transcript would find none. The 449 voice turns
of this reel are `audio` records now, pointing at the audio stream they
were heard in. The M1 transcription lane is untouched: a word is still a
text record read from audio.

**Measured** on that reel, before and after, one collection at a time.
The two collections keep their own denominators and are never added
together.

The lane's observations - 773 rows, 324 face boxes and 449 voice turns:

| Measure | Before (M3b) | After (M3b) |
|---|---:|---:|
| records | 773 | 773 |
| distinct record shapes | 2 | **1** |
| carry a source reference | 0 (0.0%) | **773 (100%)** |
| carry a provenance class | 773 (100%) | 773 (100%) |
| carry a coverage | 773 (100%) | 773 (100%) |
| carry all three | 0 (0.0%) | **773 (100%)** |
| file a model's claim as a measurement | 773 (100%) | **0 (0.0%)** |
| name the entity they are about | 0 (0.0%) | **773 (100%)** |
| refused, by name | - | 0 |

The lane's identity links - 11,130 rows, over the same 773
observations:

| Measure | Before (M3b links) | After (M3b links) |
|---|---:|---:|
| records | 11,130 | 11,130 |
| distinct record shapes | 2 | **1** |
| carry a source reference | 0 (0.0%) | **11,130 (100%)** |
| carry a provenance class | 0 (0.0%) | **11,130 (100%)** |
| carry a coverage | 0 (0.0%) | **11,130 (100%)** |
| carry all three | 0 (0.0%) | **11,130 (100%)** |
| refused, by name | - | 0 |

The 11,130 relationships are 10,347 `speech_face_cooccurrence`, 449
`diarization`, 324 `face_embedding_cluster` and 10
`cross_source_voice_match_candidate` claims, and they name seven
entities between them.

**No regression.** The other lanes, measured on the same reel in the
same run, are unchanged: the M1 transcript lane is 75,455 stored rows
becoming 75,439 portable records (the same 16
`cross_modal_disagreement` rows refused, by name, for carrying no
evidence contract), and the M3d and M6 lanes are 18,064 and 3, exactly
as `docs/evidence/portable_evidence_m3d_m6.md` measured them. The link
refusals are counted on their own denominator: the **298** links of the
M3d lane are refused by name (43 of them because the M3d lane declares
no single registry type for its claims, 255 because their observation
names no track), and they do not enter the observation refusal count
the other lane measured.

**Left.** The M3b lane is migrated; `m0` and the reel build are
counted and named, not yet lifted. The Project Model's events,
relations, tracks, masks and signals are counted by the census and not
yet migrated - a relationship now has its own kind, and the Project
Model's own `relations` collection is the next thing that can use it.
The stored model predates the contract that names the pixels a face
box was measured on, so the 324 face boxes of this reel carry no
resolution until the model is rebuilt; the portable record cannot
invent one, and says so by carrying the value as it was measured.
