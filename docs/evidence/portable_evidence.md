# The evidence that could not say where it came from

Code: `library/tools/portable_evidence.py`.
Tests: `tests/unit/context/test_portable_evidence.py`.
Model: `docs/PORTABLE_EVIDENCE_MODEL.md`.

**The rule.** A portable evidence record names the SOURCE it was read
from, the epistemic CLASS of the claim and the COVERAGE behind it, and
it is LOSSY when it describes the media rather than measuring it. A
lossy record is never filed as a measurement. One enumeration:
`library/tools/portable_evidence.py`; the model is
`docs/PORTABLE_EVIDENCE_MODEL.md`.

**What happened.** The punchlist of 2026-10-20 asked for a portable
multimodal evidence model (INFRA-001), and the measurement of the
production model on one real reel - Reel 26 of the podcast project
`lucie/geo-podcast`, read-only, on the machine's own source memory -
found three defects the existing pieces did not cover between them:

- the 95,653 observations of that reel came in **18 record shapes**,
  each modality nesting its own evidence contract in its own payload
  key set, and 18,064 of them (18.9%) carried a source reference at
  all;
- **every contract in the stored model said `measurement` /
  `measured`**, including the types the registry says a MODEL
  produced - a transcribed word, a scene description, a sound event, a
  face box. In that reel's scope, 79,173 of 95,653 records (82.8%)
  filed a model's claim as an instrument's reading, so a reader could
  not tell a VLM's description of a room from the device's own record
  of the frame rate. PR 3137 gave the producers a way to declare their
  class; the stored model predates it;
- the 431 rows the reel build itself wrote carry no evidence contract
  at all, and their intervals reach 1e43 ticks on a source 4,099.6 s
  long: spans on another grid, from which no frame can be recovered.

**The fix.** The portable model converges on what already exists rather
than adding a vocabulary: the universal source reference (PR 3116), the
declared claim class (PR 3137) and the contract's coverage kinds. It
adds the three things that had no home - the record's KIND, its
per-modality anchors, and its LOSSLINESS - and it reads the class from
the producer's DECLARATION, keeping the stored filing beside it as
`filed_class` so the disagreement stays visible.

**Measured** on that reel, before and after, one producer at a time -
this is the M1 transcript lane, 75,455 of the reel's 95,653
observations:

| Measure | Before (M1) | After (M1) |
|---|---:|---:|
| distinct record shapes | 10 | 1 |
| carry a source reference | 0 (0.0%) | 75,439 (100%) |
| carry a provenance class | 75,439 (99.98%) | 75,439 (100%) |
| carry a coverage | 75,439 (99.98%) | 75,439 (100%) |
| carry all three | 0 (0.0%) | 75,439 (100%) |
| file a model's claim as a measurement | 60,336 (80.0%) | 0 (0.0%) |
| refused, by name | - | 16 |

The whole reel, before: 18 record shapes, 18,064 of 95,653 records
(18.9%) carrying all three attributes, 82.8% filing a model's claim as
a measurement. The report:
`docs/benchmarks/2026-10-21-portable-evidence.json`; the defect-naming
tests: `tests/unit/context/test_portable_evidence.py`.

**Left.** One producer is migrated. The others - `m3b`, `m3d`, `m6`,
`m0` and the reel build - are counted and named, not yet lifted. The
Project Model's events, relations, tracks, masks, signals and identity
links are counted by the census and not yet migrated: a relationship
and an event need their own kind and their own entity references, and
the Project Model's schema has no `evidence` field on them yet.
