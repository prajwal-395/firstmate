# Editorial retrieval: a recall that cannot be gamed, a held-out set with a text lane, and five labelled categories (2026-10-21)

The optimisation scout's report (`scout-opt-retrieval`, 2026-10-20) closed with
F6: **the measurement itself, before any product change.**  Its own words:

> 1. **Make recall non-gameable.** Score a visual query against the *region* the
>    editor wants (a contiguous labelled region), and cap the credit a single
>    window can earn (or charge for the extra seconds it covers). Today a returned
>    range earns one hit per 10 s frame it spans, so "return the whole recording"
>    scores 0.33/0.50 on the held-out set.
> 2. **Label the five unlabelled held-out categories** (H11 action, H12
>    object_state, H13 identity, H14 location, H15 absent_event).
> 3. **Add text-lane present queries to the held-out set.** As it stands the
>    held-out set measures the frame lane only, so "held-out recall 0.110" says
>    nothing about the lane an editor asks in most.
> 4. **Enforce `footage_profile` in the brief set** and let the acceptance
>    harness score a correctly-reported-absent brief as a pass on that stage.

This is that work.  **No product code changed**: the search, the lanes, the
floors and the models are exactly as they were.  What changed is what the
benchmark measures, and the harness that scores the unfamiliar briefs.

## The metric that was gameable

A visual query's ground truth is a sequence of labelled **samples**: every ten
seconds of the footage where the annotator saw the subject.  The scorer counted
a hit per sample, so a returned range that spanned twenty samples earned twenty
hits - one for every sample it happened to cover.  The degenerate answer,
"return the whole recording", therefore scored **0.3295 recall and 0.5000
precision** on the held-out set: three times the incumbent's recall, for 22 181
seconds of footage nobody asked for.  A metric that pays for volume is a metric
an optimisation loop will satisfy by making the product worse.

**What the metric is now.**

| | before | after |
|---|---|---|
| the unit of credit | one hit per labelled sample a window spans | **one credit per window per region**: one window is one wanted thing, however many samples of it it covers |
| the wanted ground truth | a flat list of samples | the samples grouped into **contiguous labelled regions** (`library.tools.retrieval.regions`) |
| the seconds a window brings along | free | **charged**: the seconds outside every wanted span are false-positive seconds, and wrong seconds when the editor takes that window |
| the old number | `recall` | still reported, as `recall_uncredited` - a metric that changes its meaning silently is a metric nobody can compare against the artefacts that came before it |
| the lanes | one macro number over both | **one number per lane**, each on its own denominator |

Two angles of ONE wanted moment are one region, not two: the conversation
clock's offsets (`LC4932 -> [LCATL0013 -3.8, LCATL0014 -4103.39]`) say which
angles they are, and the set carries them.

## The numbers, before and after

**Re-scored offline from the committed artefacts** - the same windows the
shipped lanes returned on 2026-10-20, scored by the shipped scorer, then by the
capped one.  No search was re-run and no project was touched.

### The merged set (12 present queries)

| | old | capped | uncapped (= the old rule) |
|---|---|---|---|
| macro recall | 0.4630 | **0.4553** | 0.4630 |
| the frame lane alone (E09-E11) | 0.0465 | **0.0155** | 0.0465 |
| the text lane alone (E01-E08, E16) | 0.6019 | **0.6019** | 0.6019 |
| wrong seconds selected | 52.97 | 52.97 | 52.97 |
| false-positive seconds | 215.43 | 215.43 | 215.43 |

**The text lane does not move at all.**  A text moment is a passage, and a
window covering a passage was already one credit; the cap only touches the
frame lane, where the samples were the loophole.

**The consequence numbers do not move either, and that is a fact about the
shipped lane, not about the metric.**  The incumbent's windows lie INSIDE the
spans the editor asked for, so they bring no excess and there is nothing to
charge.  The charge bites on a policy that hands over more than it was asked
for - which is exactly the policy below.

### The held-out set (the six present queries the committed artefact carries)

| | old | capped | uncapped (= the old rule) |
|---|---|---|---|
| macro recall | 0.1095 | **0.0196** | 0.1095 |
| wrong seconds selected | 30.03 | 30.03 | 30.03 |
| false-positive seconds | 230.23 | 230.23 | 230.23 |

The frame lane's recall falls because the credit it was earning was the
volume it covered, not the moment it found.  H01 says it plainly: the shipped
lane returned five windows, two of which pointed at wanted samples of the same
region - **two credits, not fourteen**.

### The degenerate answer, measured both ways

"Return the whole recording of the asset the wanted moment is on", scored by
the shipped scorer and by the capped one.  The wrong seconds are over the
present queries of each set:

| policy | merged: uncapped / capped / wrong seconds | held-out: uncapped / capped / wrong seconds |
|---|---|---|
| the shipped incumbent | 0.4630 / 0.4553 / 52.97 | 0.1095 / 0.0196 / 30.03 |
| "return the whole recording" | 0.9176 / 0.7180 / **34 775.90** | 0.9112 / 0.4757 / **887.60** |

* **The volume no longer buys credit.**  On the frame lane the degenerate answer
  scores 0.0233 on H01 where the shipped lane scores 0.0465: one window that
  spans twenty-one samples earns ONE credit, and the twenty seconds of samples
  it swallowed earn nothing.
* **The seconds it hands over are priced.**  34 776 wrong seconds against the
  incumbent's 53 - and `library.tools.retrieval.incumbent.compare` requires the
  recall gain AND no rise in wrong seconds, so the degenerate answer is
  `incumbent_better`, not a candidate.

**What this does NOT claim.**  Where the label is **asset-granular** - the whole
asset IS the wanted region - the excess charge is zero, because the whole
recording really does hold what the editor asked for.  There the cap is the only
guard, and a TIGHTER label is what closes the gap.  That is annotation work,
and it is the corpus's next step, not a metric change.

## The held-out set now measures BOTH lanes

The held-out set carried **no text-lane present query at all**, so its recall
was a frame-lane number read as a retrieval number.  Three text-lane queries
were added, on the held-out sources, labelled from those sources' own
transcript, each carrying the passage on both angles:

| entry | the moment | on LC4930 | on LCATL0011 |
|---|---|---|---|
| H16 | "seo convinces an algorithm to rank you and geo is about ai understanding the company" | 134.40 - 151.51 | 123.10 - 140.19 |
| H17 | "the moment they say to check the audio levels from craig" | 55.26 - 58.29 | 43.94 - 46.97 |
| H18 | "the moment where one of them says she would just read it from there" | 181.81 - 186.60 | 170.51 - 175.26 |

They are **new labels**, and the set says so: `amendments` records that they
were added after the incumbent was measured, and each entry's provenance names
the transcript it was marked from.  The summary now reports the lanes
separately, so a set that carries one lane cannot be read as a number about
both.

## The five categories are labelled, and the labels are SEALED

H11 (action), H12 (object_state), H13 (identity), H14 (location) and H15
(absent_event) carried no label at all, so the corpus had no negative or
relational query for a retrieval floor or an action refusal to be measured
against.

**The labels live outside this repository**, in the custodian's store, and the
harness reads them at scoring time through `REN_EDITORIAL_EVAL_LABELS`.  They
are sealed on purpose: these are the queries a retrieval floor is tuned
against, and a label anybody can read is a label the floor can be tuned on.  A
checkout without the store scores the set exactly as before - the entries stay
`unlabelled`, are reported as unlabelled, and are never counted as a miss.

| entry | what the label says | how it was measured |
|---|---|---|
| H11 | the host **reaches for the laptop**, LC4930 5.9 - 7.1 | LC4930 sampled at 0.5 s over 03-12 s: her arm extends toward the laptop at 6.0-7.0 s and is back at the table by 7.5 s |
| H12 | **absent**: no drinking vessel appears in the sampled frames of either held-out asset, so no cup can have been emptied | 25 frames of each held-out asset, every 9 s |
| H13 | **present**: the ten intervals where the HOST's camera carries speech, LC4930 0.74-7.32, 14.43-14.70, 18.40-33.63, 37.23-48.24, 55.26-154.03, 156.26-160.74, 164.48-178.40, 181.11-187.30, 192.77-212.77, 216.03-224.42 | the measured speech segments, read with a 2 s pause threshold; Craig is never in LC4930's frame, so the query's second half holds wherever the first does |
| H14 | **absent**: one setting per asset - the same dark geometric wall, the same table, the same plant - so the scene never changes room | 25 frames of each held-out asset, every 9 s |
| H15 | **absent**: 160.4 s of speech across LC4930's 223.7 s span and 157.0 s across LCATL0011's 207.1 s span; the longest measured silence is 7.11 s and 6.93 s, so no minute passes without a word | the measured speech segments of both held-out assets |

**H11's query changed with its label.**  It asked for a moment that does not
exist in this footage ("the moment she opens the box"); it now names the action
that does.  A query and its label have to be the same event, and a query whose
premise is false is a query the corpus cannot score.

H11 is also the **action refusal measured against a truth**: the frame lane
refuses action queries by design, so this entry shows that refusal as the miss
it is, instead of as a refusal nobody could check.

## The unfamiliar briefs: the profile is enforced, and a correct refusal passes

`library.tools.editorial_acceptance` measured every brief against reel 13
whatever its premise, because the profile the brief declares was never
enforced, and because a run that searched, found nothing and **said so** was
scored as a retrieval failure.

* `briefs.validate_brief_set` refuses a brief whose declared footage profile is
  outside the enumeration.
* `footage.applicability` reports a substrate of another shape as
  `premise_profile_mismatch`: the footage is the wrong shape for the brief, and
  the report says so instead of carrying the substrate's profile silently.
* `footage.select_substrate` never proposes a substrate of another shape.
* `stages._measure_retrieval` **passes** a brief whose premise the footage does
  not carry and whose run reported the absence - and still fails a brief whose
  footage DOES carry the premise when the run said it was not there.

The committed acceptance report, regenerated over the committed records:

| | before | after |
|---|---|---|
| the retrieval stage | 0 passed / 4 measured | **6 passed / 6 measured** |
| the premise view | 4 briefs not carried, 4 carried | 2 not carried, **6 profile mismatches**, 0 carried - the catalogue holds ONE substrate, a two-camera interview, and six briefs are written against other shapes |
| the headline | 3 passed / 5 failed | 3 passed / 5 failed - the headline is still the engine's headline, and the retrieval stage is no longer the bottleneck for a brief that was measured against the wrong footage |

## How to reproduce

```sh
# the two editorial sets, scored by the shipped lanes
P=~/Documents/content_stuff/video_projects/ren-scratch-retrieval-bench
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_set.json --families editorial \
    --out-json /tmp/f6-merged.json
bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_heldout_set.json --families editorial \
    --out-json /tmp/f6-heldout.json

# with the sealed labels applied (the store is the custodian's)
REN_EDITORIAL_EVAL_LABELS=<the store> bin/vep -m library.tools.retrieval_eval "$P" \
    --set library/tools/editorial_retrieval_heldout_set.json --families editorial \
    --out-json /tmp/f6-heldout-sealed.json

# the unfamiliar briefs, over the committed records
bin/ren accept --records tests/live/reports/editorial-bench-records \
    --report tests/live/reports/editorial-brief-acceptance.json
```

The numbers in this report were NOT produced by a live run: they were
re-scored offline from the committed artefacts
(`docs/benchmarks/2026-10-20-editorial-retrieval-incumbent-{merged,heldout}.json`),
which is why the new entries - H16-H18 and the five sealed categories - carry
no number here.  They are measured by the next live run.

## The artefacts

| | |
|---|---|
| the capped credit | `library/tools/retrieval/regions.py` |
| the charge for the extra seconds | `library/tools/retrieval/consequence.py` |
| the scorer and the per-lane summary | `library/tools/retrieval_eval.py` |
| the sealed label store and its loader | `library/tools/editorial_eval_labels.py` |
| the profile enforcement and the correctly-reported-absent pass | `library/tools/editorial_acceptance/{briefs,footage,stages,runner}.py` |
| the held-out set with its text lane, its amendments and its five sealed entries | `library/tools/editorial_retrieval_heldout_set.json` |
| the corpus manifest's annotation state | `docs/benchmarks/2026-10-15-editorial-eval-corpus.json` |
| the acceptance report | `tests/live/reports/editorial-brief-acceptance.json` |
