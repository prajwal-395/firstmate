# Cross-clip footage index

**Status: a footage-intelligence capability, not an editing-pipeline stage.** It began as a
prototype (this file keeps the name and what that prototype measured).

Agents and people query it through `ren search` / `ren search-index`, which reach `footage_query.py`
directly; `ren analyze` builds it as part of an analysis-only run. No DAG node runs it, and it imports
nothing from `library/steps/` or `library/processes/` - `tests/unit/picture/test_footage_query.py::test_search_does_not_import_the_pipeline`
holds that, so it answers on any analysed project with no run behind it. The 2026-08-26 dashboard view and
`footage_query_bridge.py` were retired in P2 (D3/Q11); the dashboard section below is kept for the
decisions it made.

    library/tools/analysis/footage_segments.py   what the unit of retrieval is
    library/tools/analysis/footage_query.py      build, search, filter, CLI, tool defs
    library/tools/analysis/footage_frames.py     frame-level CLIP search (`ren search --visual`)
    tests/unit/picture/test_footage_query.py                  retrieval, staleness, independence from the pipeline

It answers "where in all my footage does X happen", in the captain's words "to speed up both a person's
workflow and also help the LLM actually find what it is looking for."

## In the dashboard (retired)

    source .venv/bin/activate
    python3 manage_project.py dashboard <slug>            # or an absolute project path
    open http://127.0.0.1:8420/                           # sidebar -> Footage Search

Its own sidebar entry rather than a tab inside **Footage Library**, because
the two answer different questions about different units: the Library is a
browser over CLIPS (17 cards, one per file, clip-level tags), and this is a
search over SPANS INSIDE clips (409 segments, each with its own timecode,
score and reason). They are linked - a hit's clip id opens the Library's
inspector - not merged.

Three decisions the CLI never had to make:

- **Where the index lives, and when it is built.** In the project, at
  `pipeline_output/scratch/footage_index/`, and only when the reviewer
  presses **Build**. The view states the full path *before* the button.
  Building at server start would write into the captain's project every
  time the dashboard opens; building on first query would do it at the
  moment they are least expecting a wait.
- **The model is loaded once per server.** Cold start is ~2 s, a warm query
  2-8 ms. The banner reports `cold` / `loading` / `ready` / `unavailable`
  and names the backend, so a first load does not look like a hang and a
  degraded backend says so rather than quietly returning worse results.
- **A stale index is noticed.** `build` records an `ingest_fingerprint` of
  the files it read - contents, not mtimes - and the banner compares it on
  every visit. `pipeline_data.json` is fingerprinted by its `catalog`
  subtree ONLY, because the state writer rewrites the whole file after
  every step and a colour grade landing is not a change to the footage.

A hit carries the clip, the `MM:SS.mmm` range, a non-drop `HH:MM:SS:FF`
source timecode at the clip's own frame rate (or nothing at all, when the
catalog records no rate), the matching words' own sub-second timings, the
shot facets, and both raw scores with the half that found it named.

## Using it

    python3 -m library.tools.analysis.footage_query build   <project>
    python3 -m library.tools.analysis.footage_query search  <project> "the parking lot"
    python3 -m library.tools.analysis.footage_query search  <project> "..." --mode dense
    python3 -m library.tools.analysis.footage_query search  <project> "..." --floor 0    # no abstain
    python3 -m library.tools.analysis.footage_query filter  <project> --kind scene --framing wide
    python3 -m library.tools.analysis.footage_query filter  <project> --max-face-presence 0.1
    python3 -m library.tools.analysis.footage_query stale   <project>
    python3 -m library.tools.analysis.footage_query hybrid  <project> "cars" --kind scene
    python3 -m library.tools.analysis.footage_query detail  <project> 'clip_006#speech#001'
    python3 -m library.tools.analysis.footage_query transcript <project> clip_011
    python3 -m library.tools.analysis.footage_query summary <project>
    python3 -m library.tools.analysis.footage_query tools

`build` reads the project's ingest and writes into
`pipeline_output/scratch/footage_index/` - the area §8 defines as "working
files with no reader" - no pipeline step reads it.
`--index-dir` puts it anywhere else. Nothing else under the project is
written, and a test asserts that byte for byte.

## The unit of retrieval

This was the open design question, and the answer is that there is no single
right unit, so the index does not invent one. Each ingest source is cut at
the finest boundary IT actually measured, and the kinds sit side by side in
one searchable corpus:

| kind | boundary | source | typical span | covers of 001 |
|---|---|---|---|---|
| `speech` | one WhisperX utterance | 1.04 `speech_regions` | 1 - 8 s | 62.9% |
| `action` | one vision action window | 1.03 `actions[]` | ~10 s | 95.0% |
| `scene` | one scene observation | 1.03 `scene[]` | clip or part | 46.4% |
| `camera` | one camera observation | 1.03 `camera[]` | clip or part | 84.9% |
| `object` | one object appearance | 1.03 `objects[]` | seconds to clip | 94.5% |

**Retrieve at the utterance, snap at the word.** A speech segment keeps its
word timestamps, so a hit is returned at utterance granularity and the
matching words come back with their own source timings:

    1. [speech] clip_006 (IMG_1811.MOV) 00:17.349-00:18.974  (1.62s)
       just gotta find a place to park now.
         word 'place' at 17.891-18.071s
         word 'park' at 18.228-18.613s

Indexing single words instead would destroy retrieval - a lone "the" embeds
to nothing - while still leaving the caller to reassemble a sentence. This
is how the pipeline's finest unit survives being made searchable.

**Dense curves are reduced, never retrieved.** The 30 Hz / 5 Hz / 1 Hz
signals from step 1.04 are not cut into segments. They are averaged over
each segment's span into facets - `speech_ratio`, `motion`, `face_presence`,
`brightness`, `saturation` - each read at the curve's own declared
`sample_rate_hz`. A curve is a thing to filter by, not a thing to retrieve.
A facet that could not be measured is left out rather than defaulted: "no
face curve" and "no face" are different answers.

## Deviations from the `sfx_query.py` precedent

`library/tools/analysis/sfx_query.py` is the shape this follows -
`search` / `filter` / `search_and_filter` / `get_detail` / `summary`, a CLI
with the same verbs, `get_tool_definitions()`, and a bridge beside it. Four
things are different, and each is deliberate.

**No FAISS.** The precedent stores a `faiss.IndexFlatIP`, which *is* an
exhaustive dot product. At 409 segments the search is one 409x384 matmul in
numpy, measured below at 2.5 ms including query encoding. Dropping FAISS
removes a dependency and leaves one on-disk format instead of two that can
disagree. It would start to earn its place somewhere around 10^5 - 10^6
segments, which is roughly 250 - 2500 projects the size of 001.

**The embedder is optional, and loaded two ways.** `sentence-transformers`
is not in `requirements.txt` and is not installed in this environment, so
`sfx_query.search` raises `ModuleNotFoundError` here today - only its
`filter` half runs. This module loads the same checkpoint
(`all-MiniLM-L6-v2`) through `sentence-transformers` when present and
through plain `transformers` with mean pooling when not, producing the same
vectors, and degrades to lexical-only search when neither is available
rather than returning nothing.

**Search is hybrid by default.** Dense-only misses proper nouns and concrete
objects; lexical-only misses paraphrase. Both halves are computed, blended
0.6/0.4 after min-max normalisation, and `--mode dense|lexical|hybrid`
exposes each so a hit can be attributed to the half that found it. The
worked example is below.

**The index is per project.** SFX profiles are a shared library with one
index; footage belongs to one project, so the index lives with it.

## What it cost on 001

17 clips, 13.5 minutes of footage, on the machine that runs the pipeline.

| | | |
|---|---|---|
| segments cut | 409 | 176 object, 110 speech, 86 action, 19 scene, 18 camera |
| cutting the segments | 0.039 - 0.053 s | 4 runs |
| embedding 409 texts | 0.84 - 1.39 s | 4 runs |
| **total build** | **3.0 - 4.3 s** | most of it model load |
| on disk | **1,078,944 B** | 450,592 JSON + 628,352 embeddings (409x384 f32) |
| warm query, hybrid | median 2.2 - 2.5 ms | p95 2.6 - 2.7 ms, n=40 per run |
| warm query, dense | median 2.2 - 2.4 ms | |
| warm query, lexical | median 0.06 ms | |
| `filter` (no embedder) | 0.22 - 0.33 ms | |
| cold first query | 1,771 - 2,690 ms | all of it model load |

Ranges rather than single figures, because the machine was also running the
pipeline. One latency run taken while it was busiest measured a hybrid
median of 11.4 ms and a p95 of 33.8 ms against 2.2 ms on a quiet one; the
ordering between the three modes held in every run.

Build cost is linear in segment count and the corpus is small, so the honest
reading is that **cost is not the constraint here and this measurement does
not prove it scales.** The one number that would change shape is the cold
start: ~2 s of model load per CLI invocation dwarfs every query by three
orders of magnitude, which is why a long-lived process or a lexical-only
default matters more at this size than any index structure.

## Queries against 001 - wins, near-misses and failures

### Win: `"where is the parking lot"`

    query: 'where is the parking lot'  mode=hybrid  409 segments

    1. [scene] clip_002 (IMG_1807.MOV) 00:00.000-00:15.000  (15.00s)
       score 0.9257  dense 0.543  bm25 8.993
       shot: close-up handheld shaky
       Inside a vehicle parked in a parking lot. vehicle. Daylight. Parking lot lines. Other parked cars
    2. [scene] clip_010 (IMG_1815.MOV) 00:00.000-00:05.500  (5.50s)
       score 0.9132  dense 0.568  bm25 8.256
       Outdoor parking lot and building exterior. outdoor. Daylight. Brick building with windows...
    3. [scene] clip_009 (IMG_1814.MOV) 00:00.000-00:46.000  (46.00s)
       score 0.9035  dense 0.591  bm25 7.631
       Outdoor parking lot and construction site. outdoor. Daylight. Construction site with steel frame...

All five results are real parking lots in five different clips. This is the
query the captain asked for and it works.

### Win: `"topgolf"` - a proper noun, found to the word

    1. [speech] clip_007 (IMG_1812.MOV) 00:17.525-00:23.146  (5.62s)
       score 1.0000  dense 0.855  bm25 7.730
       well, there's a topgolf right there.
         word 'topgolf' at 22.013-22.486s

Rank 1 is exact and carries a 473 ms word span. Ranks 2-5 are noise
("SCUFFLEWA Brewing Co. sign", "paved ground") at scores 0.23 and below -
see "no abstain" below.

### Win: `"he talks about finding a place to park"` - paraphrase

    1. [speech] clip_006 (IMG_1811.MOV) 00:17.349-00:18.974  (1.62s)
       score 1.0000  dense 0.602  bm25 13.242
       just gotta find a place to park now.
         word 'place' at 17.891-18.071s
         word 'park' at 18.228-18.613s
    2. [object] clip_004 (IMG_1809.MOV) 00:08.000-00:17.000  (9.00s)
       sign for 'L' PARK. structure. background. L PARK

### Near-miss: `"where does he laugh"`

    1. [action] clip_011 (IMG_1816.MOV) 01:50.000-02:00.000  (10.00s)
       score 0.6000  dense 0.422  bm25 0.000
       The man is looking towards the left side of the frame while his mouth moves as if he is
       speaking. ... He maintains a neutral to slightly...
    2. [action] clip_011 (IMG_1816.MOV) 02:00.000-02:10.000  (10.00s)
       score 0.5835  dense 0.409
       ... while smiling and then returning to a neutral expression. ... He smiles broadly, sho...
    3. [action] clip_016 (IMG_1821.MOV) 00:40.000-00:41.940  (1.94s)
       score 0.5556  dense 0.386
       The person is looking upward and smiling. ... They have a wide smile with their mouth
       slightly open and eyes squinted/closed.

Ranks 2 and 3 are genuinely the right moments; rank 1 is not, and it is
ranked first. The cause is upstream, not in the index: **the word "laugh"
appears nowhere in 001's entire ingest.** The vision analyser reports
"smiling", "wide smile", "eyes squinted"; the only audio event class step
1.04 ever emitted across all 17 clips is `silence` (11 occurrences). Nothing
in the ingest measures laughter, so nothing can retrieve it. The dense half
gets close via "smiling", which is why this is a near-miss rather than a
failure - and the two 10-second action windows are a worse answer than a
1.94 s one, which is the granularity of `actions[]` showing through.

### Failure, since fixed: `"find the shot with the cup"`

    1. [camera] clip_016 (IMG_1821.MOV) 00:00.000-00:20.000  (20.00s)
       score 0.7704  dense 0.330  bm25 3.956
       close-up selfie stationary shaky shot
    2. [camera] clip_015 (IMG_1820.MOV) 00:00.000-00:53.000  (53.00s)   score 0.7704  (identical)
    3. [camera] clip_013 (IMG_1818.MOV) 00:00.000-00:16.000  (16.00s)   score 0.7704  (identical)

Two failures in one, and this is the most important result on the page.

1. **There is no cup in 001.** The correct answer is "nothing". The index
   has no abstain: it always returns `top_k` rows, so it returned three.
2. **It matched on the query's grammar, not its content.** "the shot with
   the cup" scores every `camera` segment because they all end in the word
   "shot". The three top hits are byte-identical text with identical scores,
   which is the shape of a match on a stop-phrase.

A search that cannot say "not here" is dangerous for the LLM caller in
particular: a planner handed three confident-looking rows has no way to
know the answer set was empty.

**Fixed, 2026-08-26.** The same query now:

    query: 'find the shot with the cup'  mode=hybrid  floor=0.40  409/409 segments considered
    backend: transformers+mean-pooling

    NOTHING in this footage cleared the floor - that is the answer, not an empty result set.
      closest: 0.391 < 0.40  [object] clip_016 :: black baseball cap. object. background

      5 weak match(es) between 0.28 and 0.40 - not confident:
    1. [object] clip_016 (IMG_1821.MOV) 00:00.000-00:42.000  (42.00s)
       score 0.6000  dense 0.391  bm25 0.000
       black baseball cap. object. background

See **The floor, and the abstain** below for how the number was chosen.

### The floor, and the abstain

Retention is judged on the RAW signals - the dense cosine, and a BM25 that
is exactly zero when no query term appeared - never on the blended score.
The blend is min-max normalised over the candidate set, so its top row
scores ~1.0 for **every** query ever asked, which is exactly why the
unfloored index answered the cup query with three confident rows.

Measured on 001, 409 segments, `transformers+mean-pooling` over
`all-MiniLM-L6-v2`. Twelve queries whose subject is in the footage, eight
whose subject is not:

| subject present | best cosine | kept at >=0.40 |
|---|---|---|
| brick building | 0.878 | 36 |
| someone walking | 0.745 | 5 |
| motorcycle | 0.731 | 12 |
| the pollen | 0.721 | 2 |
| trees | 0.665 | 10 |
| the brewery sign | 0.645 | 4 |
| he says look at me | 0.640 | 2 |
| talking about goals and momentum | 0.631 | 1 |
| wide shot of the parking lot | 0.601 | 36 |
| driving the car | 0.578 | 39 |
| where does he talk about parking | 0.553 | 9 |
| he laughs | 0.347 | **0** |

| subject absent | best cosine | kept at >=0.40 |
|---|---|---|
| a birthday cake with candles | 0.396 | 0 |
| find the shot with the cup | 0.391 | 0 |
| cup | 0.325 | 0 |
| snowboarding down a mountain | 0.266 | 0 |
| a horse galloping on a beach | 0.264 | 0 |
| someone playing the piano | 0.255 | 0 |
| the president gives a speech about taxes | 0.211 | 0 |
| underwater coral reef | 0.204 | 0 |

`DENSE_SCORE_FLOOR = 0.40` separates them: every absent subject retains
nothing, and eleven of the twelve present ones retain at least one segment.

The twelfth is the reason there is a WEAK BAND as well as a floor.
`"he laughs"` tops out at 0.347 - and that is the honest failure, because
the vision pass wrote *smiling* and never wrote *laughing*. Between
`DENSE_WEAK_FLOOR = 0.28` and the floor, a hit is reported as "the closest
thing was this, and it is not confident", which is a third answer distinct
from both silence and a confident wrong row. The reviewer can show the band
or drop the floor to zero; the CLI takes `--floor`.

Without an embedder the floor degrades to "at least one query term appears
in this text", and both the CLI and the dashboard say so in as many words -
a keyword floor cannot tell "absent" from "phrased differently".

### Where the two halves disagree: `"a sign that says reserved"`

    --mode dense
    1. [scene] clip_009 00:00.000-00:46.000  dense 0.523
       ... Parking lot with brick pavers. Building with 'RESERVED' sign          <- correct

    --mode lexical
     1. [object] clip_009 bm25 5.937  sign with text. sign. background
     2. [object] clip_013 bm25 5.714  sign with text. sign. background. Credit
     3. [object] clip_013 bm25 5.714  sign with text. sign. background. Credit
     4. [object] clip_008 bm25 5.314  white sign with black text. ... SPECIALS
     ...
    12. [object] clip_015 bm25 3.534  SCUFFLEWA Brewing Co. sign. ... SCUFFLEWA BREWING CO
    13. [scene]  clip_008 bm25 3.004  Outdoor sidewalk and storefront area. ...
    14. [scene]  clip_009 bm25 2.491  Outdoor parking lot and construction site. ...  <- correct

Dense puts the sign that actually says RESERVED first. Lexical puts it
**fourteenth**, behind thirteen segments whose only merit is repeating the
common term "sign" - BM25 cannot see that "RESERVED" is the discriminating
word because the vision pass buried it in a `notable_features` sentence
rather than in the object's `readable_text`. Neither half alone is
sufficient, which is why the default blends them.

(Ranks 2 and 3 are the same object seen twice, which is the segmentation
working as designed: two appearances are two timecodes.)

### The facet half: filtering with no query at all

    $ ... filter <project> --kind scene --framing wide
    6 matching segments:
      clip_001#scene#000    00:00.000-00:02.000    2.00s  Parking lot and sidewalk. outdoor...
      clip_004#scene#000    00:00.000-00:27.000   27.00s  Inside a vehicle driving on a road...
      clip_006#scene#000    00:00.000-00:23.000   23.00s  Urban street with multi-lane road...
      clip_008#scene#000    00:00.000-00:09.000    9.00s  Outdoor sidewalk and storefront area...
      (2 more)

`--min-face-presence`, `--max-face-presence`, `--max-motion`,
`--min-duration` and the rest read the reduced 1.04 curves, so "steady wide
footage with nobody on screen" is a filter rather than a search:

    $ ... filter <project> --framing wide --stability stable --max-face-presence 0.1
    65 matching segments:
      clip_001#action#000   00:00.000-00:03.570   3.57s  The camera pans and tilts downward...
      clip_001#scene#000    00:00.000-00:02.000   2.00s  Parking lot and sidewalk. outdoor...
      ...

An UNMEASURED facet fails its bound rather than passing it: a clip whose
face curve step 1.04 never wrote is not evidence that nobody is on screen.
The dashboard offers exactly these as controls, and builds each dropdown
from the values the index really holds, so a reviewer cannot pick a framing
nothing was shot at.

## Would this have improved a real pipeline decision?

**For `mesh_spine` (2.05), no - and the reason matters.** Its LLM request on
001 is 1,212,805 bytes of context, of which **1,176,177 bytes (97.0%) is
`temporal_index`**: every clip's 30 Hz energy curve, 5 Hz optical flow and
5 Hz camera-motion decomposition, flattened into text. But mesh_spine is not
choosing footage. Step 2.02 already chose the passages; 2.05 orders them
into spine blocks and assigns durations and music behaviour. There is no
question it would put to a search index, because it is not looking for
anything - it has been handed everything and cannot use most of it. Its
problem is **reduction, not retrieval**, and the two are not the same tool.

The segmentation half of this prototype does address that, and the number is
concrete: reducing 001's `temporal_index` to per-clip facets plus scene
boundaries, speech regions and motion peaks - the same reduction
`curve_facets` performs - is **17,730 bytes against 1,176,177, a factor of
66**, with nothing mesh_spine's prompt asks for removed. That is a change
worth making on its own and it needs no index, no embeddings and no search.
`review_rough_cut` carries the identical 1,176,177-byte payload and
`creative_direction` carries 93,246 bytes of the same thing.

**For `select_broll` (3.02), plausibly yes, but 001 cannot demonstrate it.**
That step is handed 40,191 bytes of all 17 clips' semantic documents and
must pick cutaways - a genuine "find me footage of X" question. But the
whole 409-segment corpus renders to **60,810 bytes** of readable text, and
its vision-derived part to **47,892 bytes**. On a project this size the
entire index fits in the context window, so retrieval buys nothing that
handing over the whole (better-formatted) corpus would not. `plan_transitions`
receives 113,602 bytes of `semantic_analysis` - twice the size of the whole
index - which says the same thing from the other direction.

**So: 17 clips is not enough to tell.** The index looks good on 001 partly
because 001 is small enough that every query has few competitors and every
answer is verifiable by hand. The claim that retrieval beats
give-them-everything only starts to be testable somewhere past the point
where the corpus stops fitting in a prompt - call it a few hours of footage,
or 20-50 projects of this size in one searchable pool. **The cross-project
case is the one that was never in doubt and is also the one not built here**:
`build_index` takes one project folder, and "where in all my footage" across
the captain's whole archive would need the index keyed by project.

## What the ingest does not support, and what would change the design

- **Nothing measures laughter, tone or emotion.** No audio event classifier
  worth the name ran: `audio_events` reports only `silence` in all 17 clips.
- **Prosody produced nothing on 001.** All 17 files record
  `"parselmouth not installed"`, so pitch, intensity and rate are not
  facets, and the `coverage` block reports that as zero rather than as
  success (§10.3). `requirements.txt` now pins `praat-parselmouth`, so a
  rebuilt environment would change this.
- **The vision pass is coarser than the brief assumed.** Not ~0.5 s windows:
  `actions[]` is ~10 s, and `scene[]`/`camera[]` are usually one observation
  per clip. `scene` covers only 46.4% of 001's footage, so "where is the
  parking lot" is answered over less than half the timeline. Denser scene
  sampling in step 1.03 would improve this index more than anything done to
  the index itself.
- **Vision text was written to inform an edit, not to be searched.** It is
  descriptive enough to embed usefully - "Parking lot with brick pavers",
  "Building with 'RESERVED' sign" - but its object labels are long noun
  phrases ("young man in black baseball cap and light-colored button-down
  shirt") that dilute a short query's similarity.
- **Steps 1.06 (SAM 2 masks) and 1.07 (OCR) are not in the DAG and produce
  nothing** (#187). They would change this design in one specific way:
  1.07's OCR gives per-frame text with timings, which is a *finer* unit than
  anything here and the natural way to answer "find the shot with the
  RESERVED sign" exactly rather than through a vision paraphrase. 1.06's
  masks give boxes, not language, so they would add facets (subject size,
  position) rather than a new retrievable kind. Neither is wired here.
- **Duplicate vision profiles.** 001 carries `*_v3.json` and `*_v3__2.json`
  for every clip. `load_vision_profiles` keeps the newest per clip; without
  that the corpus would be doubled and every result duplicated.

## If it goes further

In the order the measurements argue for:

1. ~~**A score floor and an honest empty answer.** The cup query is the
   case.~~ **Done, 2026-08-26**, alongside the dashboard view - the
   captain was going to hit it on their first afternoon.
2. **The reduction, separately and first.** 66x off `temporal_index` for
   three steps, with no index involved.
3. **Denser scene sampling in 1.03**, which is where the retrieval ceiling
   actually is.
4. **Cross-project scope**, which is the version of the question that
   retrieval is unambiguously the right answer to.
5. Only then a decision about whether any step should call it.

## Frame index (CLIP) companion, 2026-10-01

`library/tools/analysis/footage_frames.py` sits beside this text index:
CLIP ViT-L/14 over frames sampled at 1/10 s, reached through the same two
verbs (`ren search-index --frames` builds it, `ren search --visual` ranks
time ranges per clip).  Same independence from the pipeline, same guard.

Design follows its own eval (firstmate `data/vep-clip-frame-search/eval/`,
1092 frames over ~181 min of real footage, 11 pre-registered queries):
objects/people/scenes only (P@5 5/5, 5/5, 3/5); action and gesture queries
refused to the pose/hand lane (mouth-cover 1/5, drinking 0/5 - measured at
chance); every answer ranked top-k with scores, every hit unverified, and
no abstention floor - none separates present from absent (absent max 0.191
inside the present band; an all-miss query topped everything at 0.232).
