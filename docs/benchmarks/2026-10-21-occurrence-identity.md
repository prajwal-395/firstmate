# M0-L1 — occurrence identity in the canonical model

**Lane:** `g-m0-l1-occurrence-identity` · **Date:** 2026-10-21 · **Base:** `d87b5839b` ("QA-004: judge the delivered file's audio on the file, and gate delivery (#3046)")

**Question this lane answered:** can Ren name WHICH TIME a repeated event happened — "the second time the person brings the handheld device to their mouth" — and keep that name when the lane runs again?

**Short answer:** it can now, in the canonical model, and the answer carries its evidence. Before this lane the canonical model had no ordinal at all: `library/tools/project_model.py` held zero occurrences of the word, and the query layer refused every predicate but `measurement` and `event_span` ("this query answers 'measurement' and 'event_span' only"). "The second time" was not a hard question — it was an unaskable one.

**Scope note.** This lane owns the IDENTITY of an occurrence (D-11, A-03, B-12, E-05). It does not own the frame-exact boundary of one (Lane 2), the event lane that produces the occurrences (Lane 3, `g-m0-l3-spans-without-identity`), or the operation that cuts at the boundary (Lane 4). The M0 frozen cases M0-1, M0-3 and M0-4 are what this lane moves; M0-2, M0-5, M0-6 and M0-7 are other lanes'.

---

## 1. What was built

| Piece | Where | What it is |
|---|---|---|
| The occurrence axis | `native/ren-protocol/src/project_model.rs` (`EventOccurrence`), `library/tools/project_model.py` | An event may carry `occurrence: {class, subject_id?, index}` — its position in the series of its class and subject, 1-based. |
| The one enumeration | `library/tools/occurrence_identity.py` | Assigns the axis, resolves the n-th occurrence, and hands back the receipt. |
| The query verb | `library/tools/evidence_query.py` | `{"predicate": "nth_event", "event_class": ..., "nth": N}` — the n-th occurrence of a class, answered with its evidence. |
| The evidence ledger | `library/tools/project_model_evidence.py` (`_assign_occurrences`) | The observation that supports an occurrence says WHICH occurrence it supports (`payload.occurrences`). |
| The receipt | `library/tools/occurrence_identity.py` (`receipt`, `resolve_target`) | Which event, which occurrence, on what evidence, where — the thing a later reader checks without re-running the lane. |

**The scope of a series is one source.** The second occurrence of an action is a different moment in each take, so the series counts within one asset, and a query that does not say which take is REFUSED (inconclusive, with the reason) rather than answered from whichever take came first.

**The number, not the id, is what survives.** A re-measure rewrites the records and their ids; it does not move the second occurrence after the third. The occurrence KEY (`asset|class|subject|index`) is therefore stable across a re-measure while the event ids are not — which is what makes a receipt that names the second occurrence still name it after the lane ran again.

---

## 2. The measurement

```sh
python3 docs/benchmarks/occurrence_identity_bench.py \
    "$PWD/scratch/m0/projects/m0-pickup"
```

The corpus is frozen in `2026-10-21-occurrence-identity-fixtures.json`: **five fixtures, each with ≥2 occurrences of one class**, all built from the ONE real measurement this machine has — the three times the person brings the device to their mouth in `IMG_1822` (≈17–19 s, 33.3–33.7 s, 47 s), the moments the M0 baseline report adjudicated by eye. The five fixtures replay those measured moments under the five combinations the axis has to keep apart: one subject; a second subject; a second take; two classes; one class spread over two takes.

| Fixture | instances | recall | exactness | stability | the query verb |
|---|---:|---:|---:|---:|---|
| F1 one subject | 3 | 1.0 | 1.0 | 1.0 | 3 of 3 occurrences answered, all naming the right one |
| F2 second subject | 3 | 1.0 | 1.0 | 1.0 | 3 of 3 |
| F3 second take | 3 | 1.0 | 1.0 | 1.0 | 3 of 3 |
| F4 two classes | 3 | 1.0 | 1.0 | 1.0 | 2 of 2 (the class has two occurrences; the other class has one) |
| F5 one class, two takes | 4 | 1.0 | 1.0 | 1.0 | unscoped: **refused** (2 sources); scoped to one source: 2 of 2 |

| Metric | Target | Measured |
|---|---:|---:|
| repeat-event recall | ≥ 0.9 | **1.0** |
| occurrence-index exactness | 1.0 | **1.0** |
| index stability across a re-measure | 1.0 | **1.0** |

**What each number means.** *Recall* is the share of repeated instances the canonical model numbers at all. *Exactness* is the share whose number is the occurrence's true time order inside its class and subject (not the order the lane happened to write). *Stability* is the share whose occurrence key is unchanged when the lane rewrites every record and every id.

**The live model.** The bench also migrates the real Ren-owned scratch project (`scratch/m0/projects/m0-pickup`, the M0 fixture) and reports what the model carries:

```json
{"state": "measured", "events": 0, "numbered": 0,
 "notes": ["f6e702c51906: M3b/M1b absent; no face or voice observations",
           "f6e702c51906: M4 absent; no scene observations",
           "f6e702c51906: M7 absent; no event spans"]}
```

**Zero, and why.** The event lane (M7) never ran on that fixture: it refuses without the identity lane (M3b), whose model is licence-blocked for commercial work. That is Lane 3's gap (`g-m0-l3-spans-without-identity`), not this lane's — and it is reported rather than hidden, because a zero that says nothing is worse than a zero that names its cause.

---

## 3. Before and after

| | before (main, `d87b5839b`) | after |
|---|---|---|
| The canonical model's ordinal | none — 0 hits for `occurrence` in `project_model.py` | `EventOccurrence {class, subject_id?, index}`, 1-based, per source |
| A model carrying an occurrence | REFUSED by the schema (`additionalProperties: false`) | accepted, and validated: one series, one number per position |
| "the second time" | not askable — the query layer refused the predicate | `nth_event(class, subject, n)` answers with the event, the occurrence and its evidence |
| The evidence ledger | an observation said what it measured, never WHICH time | the supporting observation carries `occurrences` |
| A receipt | nothing named an occurrence | `occurrence_receipt/1`: event id, class, subject, index, key, interval, evidence ids |
| A re-measure | — | the occurrence KEY is unchanged; the record ids are not |

**Fidelity.** No behaviour changed for anything that does not ask an ordinal question: the occurrence axis is additive (`Option` in Rust, optional in the schema), the query layer's existing four states are unchanged, and the migration writes one extra key into the observations that support an event. The dependent-test run (55 files, `scripts/select_dependent_tests.py`) is **970 passed, 6 skipped, 1 failed** — and that one failure (`tests/unit/picture/test_region_stats.py::test_a_replay_of_color_grade_sources_the_subject_reading`) fails identically on main with this lane's changes stashed. It is not this lane's.

---

## 4. What is NOT proved here

- **The onset frames are agent-adjudicated.** The M0 baseline report records the occurrence-2 band as 33.30–33.70 s (±6 frames) and says a human must re-freeze it. This corpus inherits that status: it measures WHICH occurrence is named, not how exact an onset is. The captain's re-adjudication is still owed.
- **The frame-exact boundary is Lane 2's.** The occurrence names the event; it does not say how many frames the event's edge is worth.
- **The event lane is Lane 3's.** No real occurrence was measured on this machine while this lane ran, so the corpus starts from moments already measured rather than from moments found.
- **The cut is Lane 4's.** No operation was compiled or executed here; nothing touched Resolve, and no broker was started.
- **Five fixtures, one measurement.** The corpus exercises the axis, not the detection: it cannot show that the engine FINDS the second time, and it does not claim to.

---

## 5. Reproduction

```sh
cd /Users/prajwal/.treehouse/video-editing-pilot-9487f5/57/video-editing-pilot
python3 -m pytest tests/unit/picture/test_occurrence_identity.py -q
python3 docs/benchmarks/occurrence_identity_bench.py \
    "$PWD/scratch/m0/projects/m0-pickup"
```
