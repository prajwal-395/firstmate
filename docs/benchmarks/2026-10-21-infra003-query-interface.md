# INFRA-003: one multimodal query interface, measured before and after

**Lane:** `p-infra003-query-interface` · **Date:** 2026-10-21 ·
**Question this lane answered:** the punchlist's INFRA-003 — *"Define a
reusable multimodal query interface."* The four bullets, and what each
one now has:

| Punchlist bullet | Where it landed |
|---|---|
| Search by speech, appearance, action, identity, relation, and temporal order | `library/tools/world_model_query.py` — `SEARCH_MODES`, one enumeration; `World.search(query)` answers all six over the canonical records |
| Return grounded media intervals and confidence or uncertainty | Every interval carries `source_reference` (the universal source reference), `provenance.class` (the epistemic class its producer earned) and either a measured `confidence` or a named `uncertainty.reason` |
| Expose evidence inspection independently of Resolve | `World.evidence(start, end)` — the frames, the verdicts and the lane records behind a span, read out of the source memory; the module imports no Resolve client at all |
| Support future consumers such as animation, sound design, and creative asset applications | The interval carries no timeline, no reel, no Resolve handle: `docs/QUERY_INTERFACE.md` is the contract, and a test names the defect of a consumer-shaped row |

---

## 1. The measurement

**Corpus (denominator 7 sources, 10,895.1 s of footage):** the seven
GEO podcast sources the machine's source memory carries. Every lane
file was SYMLINKED into a scratch root under `/tmp` and the per-second
world models were built FRESH there; the machine's store and the
captain's media were never written.

**Questions (denominator 31):** the M0 milestone's own cases, asked of
each source's own records — the same questions and the same scoring as
the VI-013 harness, which this benchmark IMPORTS rather than copies, so
the two measurements speak the same language.

| Case | The question | n |
|---|---|---:|
| M0-1 | the second time `<track>` covers their mouth (event resolution) | 6 |
| M0-2 | the boundary of that occurrence (within 3 frames = 0.125 s) | 6 |
| M0-3 | when does it happen, and which one is the second (repeat identity) | 6 |
| M0-P | what is `<track>` doing at `<t>`s — the control, one second | 6 |
| M0-8 | did the person ever cover their mouth (abstention honesty) | 7 |

**Reel plan (denominator 37 candidates):** `ren-scratch-l3wave`, a
Ren-owned scratch project with 37 real reel candidates, each a
master-timeline span with the reel builder's own speaker attribution
(`seconds_by_speaker`, `share_by_speaker`). That attribution is the
ground truth; this lane did not produce it.

**The two routes**

- **before** — the world model's query surface as it stood on main:
  interval, entity and who/what/where questions over the canonical
  lanes, each fact carrying the lane, the slot, the reader and the
  field. No search modes, no source reference, no epistemic class, no
  confidence, no evidence inspection. The "before" rows are the
  "after" rows with the three added fields removed, which is exactly
  what main returned: the change is additive.
- **after** — the same surface extended: the six search modes over the
  same records, every interval carrying the source reference, the
  class and the confidence-or-uncertainty.

### The results

| Measure | before | after |
|---|---:|---:|
| M0 questions answered | 31/31 | **31/31** |
| M0 questions correct | 31/31 | **31/31** |
| the same questions asked as SEARCHES, answerable through the interface | **0/31** | **31/31** |
| facts carrying a source reference | 0/2,178 | **663/663** |
| facts carrying an epistemic class | 0/2,178 | **663/663** |
| facts carrying a measured confidence | 0/2,178 | 0/663 |
| facts carrying a NAMED uncertainty | 0/2,178 | **663/663** |
| world-model context per call | 39,654 B | **27,273 B** |

**Per case:** before and after are identical — M0-1 6/6, M0-2 6/6,
M0-3 6/6, M0-P 6/6, M0-8 7/7. **No fidelity regression.**

### What the numbers say

1. **The surface could answer, but it could not be SEARCHED.** Every M0
   case had to be phrased as a span or an entity question, and the
   consumer had to know which lane held the answer. Asked as the
   punchlist phrases them — search by action, by identity, by temporal
   order — **0 of 31** were answerable through the interface before
   this lane. They are 31/31 after.
2. **The answers named no source.** 0 of 2,178 facts carried a
   reference to the bytes they were read from, and 0 of 2,178 carried
   the epistemic class their producer earned. A consumer that wanted
   to cut the picture, animate it or score it had to re-derive where
   the moment was, and could not tell a model's description from an
   instrument's record. Now 663/663 carry both.
3. **A confidence is reported where a lane stored one, and its ABSENCE
   is named where none was.** The M0 cases are candidates and
   utterances, and no lane stored a score for either: 0/663 carry a
   confidence, and 663/663 carry the reason there is none. Over the
   whole corpus, **427** intervals do carry a measured score (the M3b
   detection scores of the face spans), and **6,018 of 6,018** carry
   either a score or a named uncertainty. The transcript's own
   `confidence: 0.0` is NOT reported as a score: the M1 document
   publishes none, and a 0.0 read as a confidence would be a false
   negative nobody measured.
4. **The search route is NARROWER than the span route**, so the
   context per call fell (39,654 B → 27,273 B) while the answers
   stayed correct: a search asks one question where the span route
   asks every aspect at once.

### The six modes, over the corpus

| Mode | Intervals | with a source reference | with a class | with a measured confidence | with a named uncertainty | lane absent |
|---|---:|---:|---:|---:|---:|---|
| speech | 3,366 | 3,366 | 3,366 | 0 | 3,366 | — |
| appearance | 476 | 476 | 476 | 427 | 476 | m3c (7/7 sources) |
| action | 639 | 639 | 639 | 0 | 639 | — |
| identity | 1,531 | 1,531 | 1,531 | 0 | 1,531 | m10 (7/7 sources) |
| relation | **0** | 0 | 0 | 0 | 0 | m10 (7/7 sources) |
| order | 6 | 6 | 6 | 0 | 6 | — |
| **corpus** | **6,018** | **6,018** | **6,018** | **427** | **6,018** | — |

**The relation mode is UNMEASURED on this corpus, not empty.** The M10
lane was never built for any of these seven sources, so the mode
answers with no intervals and NAMES the missing lane in
`lanes_consulted_absent` and in `unanswered`. "Nobody held anything"
was never measured, and the answer says so.

The appearance mode reports m3c (the expression classifier) as absent
on all seven sources, and the identity mode reports m10 as absent:
both can still answer, and both say which part of the answer is
missing.

### The reel plan

The 37 real reel candidates, asked of the source spans their master
seconds map onto:

| Measure | before | after |
|---|---:|---:|
| candidates answered | 37/37 | **37/37** |
| speaker set matches the plan's own attribution | 37/37 | **37/37** |
| speaker SHARE within 0.10 of the plan's | 32/37 | **32/37** |
| intervals returned | — | 1,143 |
| intervals carrying a source reference | — | **1,143/1,143** |

The reel plan's numbers are the VI-013 numbers, unchanged: the search
route agrees with the span route on every candidate, and every interval
it returns now names the source bytes it came from. **No fidelity
regression on the reel plan either.**

### The evidence, inspected without Resolve

`world_model_query evidence <project> --clip clip_001 --start 2 --end 6`
returns the span's intervals, the FRAMES sampled inside it (each with
its path on disk), the VERDICTS on the candidate spans and the lane
RECORD each fact was read from. It asks Resolve for nothing, and the
module imports no Resolve client — a test asserts the import graph.

Placing an interval reference in the actual media file needs the
canonical Project Model, and this project's model REFUSES: its clock
carries no measured frame rate, so the resolver answers
*"incompatible: the clock carries no measured frame rate, so a tick
range cannot be placed in the file"* for every interval. The refusal
is named, not silent, and the frames on disk are still there to open.
(Loading that model costs 1 m 42 s; it is opt-in behind `--model`.)

## 2. What changed

- **Extended:** `library/tools/world_model_query.py` — the world's query
  surface gained `SEARCH_MODES` (the six modes), `World.search(query)`,
  `World.evidence(start, end)`, and the grounding every fact now
  carries: `source_reference`, `provenance.class`, `confidence` and
  `uncertainty`. The existing interval, entity and ask commands answer
  with the same grounding, so there is ONE vocabulary.
- **Extended:** `library/tools/source_reference.py` — the one spelling
  of a digest's asset, stream and clock ids, and the interval reference
  built on that source's millisecond clock. The canonical Project
  Model's private helpers are pinned to it by a test, so the two
  spellings cannot drift.
- **New:** `docs/benchmarks/2026-10-21-infra003-query-interface.py`,
  its `.json` output and this report;
  `tests/unit/picture/test_world_model_query_search.py`;
  `docs/QUERY_INTERFACE.md`.

## 3. What was left undone

- **No consumer reads the interface yet.** The shape is published for an
  animation, sound-design or asset consumer, and none is built. This
  lane made the surface reusable; it did not make it used.
- **The relation mode is unmeasured here.** The M10 lane was never
  built for these seven sources, so the mode's FIDELITY on a corpus
  with relationships is UNMEASURED, not passed.
- **No planning step consumes the search surface.** The measurement
  shows what a planning call would carry; the wiring — a named context
  view a step declares, over a routed input — is a DAG change.
- **Byte-level placement is refused for this project** (no measured
  frame rate on its clock). The reference is right; the model's clock
  is not yet measurable enough to place it in the file.
- **The appearance mode's expression half is unmeasured** (m3c absent
  on every source of the corpus).

## 4. Reproducing

```sh
python3 docs/benchmarks/2026-10-21-infra003-query-interface.py \
    --machine-root ~/.local/share/vep/source_memory \
    --scratch-root /tmp/fm-infra003/memory \
    --project ~/Documents/content_stuff/video_projects/ren-scratch-l3wave \
    --out-json docs/benchmarks/2026-10-21-infra003-query-interface.json
```

Nothing outside the repository and the scratch root was written, no
footage was touched, no Resolve was asked for anything, and the
captain's projects and timelines were not read.
