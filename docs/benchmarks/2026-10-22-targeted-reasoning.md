# VI-013: the world model answered narrowly, against the world model read whole

**Lane:** `p-vi013-targeted-reasoning` · **Date:** 2026-10-22 ·
**Question this lane answered:** the punchlist's VI-013 — *"Make the world
model useful for targeted reasoning."* The five bullets, and what each
one now has:

| Punchlist bullet | Where it landed |
|---|---|
| Retain `world_model.json` as a derived view of canonical records | Unchanged: `library/tools/world_model.py` still builds it, `project_graph` still reads it, and nothing in this lane writes it. |
| Expose interval and entity queries that retrieve only what a question requires | `library/tools/world_model_query.py` — `facts(start, end, aspects)`, `entity(id)`, `answer(question)`. |
| Include temporal relationships and provenance | Every fact carries `provenance` (lane, slot, reader, field, measured-vs-candidate) and the M10 relationship spans come back as facts. |
| Support evidence inspection at native timing resolution | Facts carry the canonical record's own `start`/`end` — the utterance's, the speaking span's, the candidate's — not the second's. |
| Avoid making every planning step consume the full per-second world model | The per-question context measured below is **404× smaller** than the document, with the same answers and more of them. |

---

## 1. The measurement

**Corpus (denominator 8 sources, 12,392 s of footage):** the seven GEO
podcast sources the machine's source memory carries, plus the M0 scratch
copy of Apple `IMG_1822` (48.0 s). Every source's per-second world model
was built into a **scratch** memory root (symlinks to the machine store's
lane files, `world_model.json` written fresh under `/tmp`); the machine
store and the captain's media were never written.

**Questions (denominator 32):** the M0 milestone's own cases, asked of
each source's own records:

| Case | The question | n |
|---|---|---:|
| M0-1 | the second time `<track>` covers their mouth (event resolution) | 6 |
| M0-2 | the boundary of that occurrence (within 3 frames = 0.125 s) | 6 |
| M0-3 | when does it happen, and which one is the second (repeat identity) | 6 |
| M0-P | what is `<track>` doing at `<t>`s — the control, one second | 6 |
| M0-8 | did the person ever cover their mouth (abstention honesty) | 8 |

**Reel plan (denominator 37 candidates):** `ren-scratch-l3wave`, a
Ren-owned scratch project with 37 real reel candidates
(`reel.candidates`), each a master-timeline span with the reel builder's
own speaker attribution (`seconds_by_speaker`, `share_by_speaker`). That
attribution was measured by the reel builder from the master transcript
and is the ground truth here — this lane did not produce it.

**Ground truth** is read straight off the canonical records (`events.json`)
by the harness, with no module of this lane involved.

### The three routes

- **today_api** — the surface that exists today: `world_model.json` read
  whole, `world_model.query_at_time` for a point. There is no interval
  query and no entity query.
- **whole_view** — an agent holding the whole per-second document and
  scanning it.
- **targeted** — this lane's query surface.

### The results

| Route | Answered | Correct | Facts carrying provenance | World-model context per planning call |
|---|---:|---:|---:|---:|
| today_api | 6/32 | 6/32 | 0/6 | 15,533,918 B · 5,610,489 tokens |
| whole_view | 32/32 | 26/32 | 0/1,300 | 15,533,918 B · 5,610,489 tokens |
| **targeted** | **32/32** | **32/32** | **2,178/2,178** | **38,415 B · 12,294 tokens** |

Tokens are `o200k_base` through `library/tools/token_measure.py`; the
byte counts are exact and are the same measurement the context audit
reports.

**Per case:**

| Case | today_api | whole_view | targeted |
|---|---|---|---|
| M0-1 event resolution | 0/6 | 6/6 | 6/6 |
| M0-2 boundary precision (≤ 0.125 s) | 0/6 | 6/6 | 6/6 |
| M0-3 repeat identity | 0/6 | 0/6 | 6/6 |
| M0-P the one-second control | 6/6 | 6/6 | 6/6 |
| M0-8 abstention honesty | 0/8 | 8/8 | 8/8 |

### What the numbers say

1. **The per-second view cannot answer a span or an entity question.**
   `today_api` answered the six one-second questions and nothing else:
   there is no route for a span, and no route for an entity. It answered
   6/32.
2. **Reading the whole document answers more, at the price of the whole
   document.** `whole_view` answered 32/32 and got 26 right; it misses
   M0-3 (6/6) because the view names no ordinal — "the second time" is a
   question the document cannot answer, only a scan of it can. And
   0/1,300 of its facts say which lane produced them.
3. **The targeted query answers everything, at 404× less context.**
   15,533,918 B → 38,415 B (404×); 5,610,489 → 12,294 tokens (456×).
   Every one of its 2,178 facts names its lane, its slot, the reader
   that produced it and whether the lane MEASURED the fact or only
   CANDIDATED it. And the six M0-3 answers carry the ordinal the view
   lacks.

### The reel plan

The 37 real reel candidates, asked of the world model over the source
spans their master seconds map onto:

| Measure | Result |
|---|---|
| Candidates asked | 37 |
| Candidates the world model answered | 37/37 |
| Speaker set matches the reel plan's own attribution | **37/37** |
| Speaker SHARE within 0.10 of the plan's | 32/37 |

The speaker set agrees on every candidate: the world model, asked "who
speaks in this reel's source span", names the same two people the reel
plan recorded. On five candidates the SHARE differs by more than 0.10 —
the world model attributes more of the span to one speaker than the
master transcript does, which is what a source-side diarization and a
timeline-side diarization disagree about, and it is reported rather than
smoothed away.

### The per-source document sizes the context comes from

| Source | Duration | `world_model.json` | tokens |
|---|---:|---:|---:|
| LC4932.MXF | 4,941.1 s | 47,133,943 B | 17,005,795 |
| LCATL0013.MXF | 4,099.6 s | 36,722,044 B | 13,278,002 |
| LCATL0014.MXF | 838.0 s | 8,139,877 B | 2,947,688 |
| LC4931.MXF | 287.6 s | 2,394,747 B | 862,747 |
| LCATL0012.MXF | 289.0 s | 2,514,173 B | 906,180 |
| LC4930.MXF | 225.2 s | 2,129,592 B | 768,491 |
| LCATL0011.MXF | 214.5 s | 1,871,880 B | 676,802 |
| M0 scratch copy (IMG_1822) | 48.0 s | 41,633 B | 14,346 |

## 2. What changed

- **New:** `library/tools/world_model_query.py` — the world's query
  surface. `World` reads the canonical lanes lazily (a question about
  speech never pays for M3's faces), answers interval and entity
  questions at native timing, and names every fact's provenance. The
  entity query numbers each row's **occurrence** of its kind, so "the
  second time" is a nameable answer.
- **Extended:** `library/tools/world_model.py` — `interval`, `entity` and
  `ask` subcommands, delegating to the query module. The per-second
  view, its build and its readers are untouched.
- **New:** `docs/benchmarks/2026-10-22-targeted-reasoning.py` and its
  `.json` output; `tests/unit/picture/test_world_model_query.py`.

## 3. What was left undone

- **No planning step consumes the query surface yet.** The measurement
  shows what a planning call would carry; the wiring — a named context
  view a step declares, over a routed input — is the next step, and it
  is a DAG change rather than a query change.
- **The world model's own file is still built per second.** This lane
  made the world ANSWERABLE narrowly; it did not make the VIEW smaller,
  because the punchlist says to retain the view.
- **The M0 fixture's own event lane was never built** (no M7 for the
  48 s copy), so its M0-8 answer is an abstention: the missing lane is
  named, and the occurrence questions were not asked of it.
- **The reel-plan share disagreement (5/37) is not explained.** The
  speaker SET agrees everywhere; the split differs on five candidates.
  This lane measured the disagreement and did not attribute it.

## 4. Reproducing

```sh
# A scratch memory root: symlink the lane files, build the world models there.
PIPELINE_SOURCE_MEMORY_ROOT=/tmp/fm-vi013/memory \
  python3 -m library.tools.world_model build <scratch project>

python3 docs/benchmarks/2026-10-22-targeted-reasoning.py \
  --memory-root /tmp/fm-vi013/memory \
  --project ~/Documents/content_stuff/video_projects/ren-scratch-l3wave
```

Nothing outside the repository was written, no footage was touched, and
the captain's projects and timelines were not read.
