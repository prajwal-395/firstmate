# The quality bar's two defects, repaired

The harvest run (#593) measured two things wrong with `reel_quality_bar` and
correctly declined to change it. This is the repair. **No threshold in this
repository moved, and no band was widened.** Both defects were severity: two
properties that the captain's brief reports were being made to decide.

---

## Defect 1 - coherence failed everything, and now it records

`coherence_of` was `NOT_FOLLOWABLE if reading.assumes_known else FOLLOWABLE`.
Any observation at all flipped it, so it read `not_followable` on 31 of 31, and
a second reader that never saw the first reproduced that exactly. The
observations are sound and about the material; the quality could not
**discriminate**, and a column constant across a batch carries no information
about that batch.

AGENTS.md 10.4 says to give a model-judged gate a **deterministic half** and let
that carry the verdict. I looked for one. Four candidates, each measured against
the captain's own 31 reels before anything was written:

| candidate deterministic half | needs a model? | measured on the 31 | why it was rejected |
|---|---|---|---|
| the reel begins strictly inside a transcript segment | no | **0 of 31** | boundaries always snap to a segment start, so it is constant and could never fail |
| the reel opens at a sentence boundary, from punctuation | no | **401 of 940** segments carry any of `. ? !` | it would measure which transcription pass wrote the segment, not how the reel opens |
| the reel begins partway through the speaker's own turn | no | **9 of 31** - it does discriminate | it measures a *different* property: reel 11 opens on the words "so your" and still starts its speaker's turn cleanly, because an answer grammatically continues the question |
| a pronoun or demonstrative in the opening with no antecedent in the reel | no | **14 of 31** over the first ten words, **23 of 31** over the first twenty | the count is decided by a window width nobody can source, and it calls reel 11 - "so your", the plainest mid-sentence opening in the batch - clean |
| a dependency quoted from the reel's **first word**, derived from the checked reading | **yes** | **18 of 31** (reader 1) vs **13 of 31** (reader 2), agreeing on 26 | it discriminates, but which reels fail depends on which model read them |

**So there is no honest deterministic half, and coherence now RECORDS rather
than gating.** `QB-NOT-FOLLOWABLE` is a WARNING. That is the outcome AGENTS.md
10.4 prescribes for this shape, not a workaround for it. The reasoning and every
number above are kept in code as `reel_quality_bar.COHERENCE_DOES_NOT_GATE`, so
a later batch that finds a real deterministic half has a record to beat.

### The second direction, which is why the demotion is urgent rather than tidy

The recording got better instead of louder. `dependency_positions` places every
dependency at the word the reel says it - exact arithmetic over a quote
`check_reading` has already proved is in the reel:

| where the dependency is said | reader 1 | reader 2 |
|---|---|---|
| in the reel's **call to action** | 33 (on **26 of 31** reels) | 29 (on **26 of 31** reels) |
| in the body | 60 | 38 |
| at the reel's **first word** | 18 | 13 |

**26 of the 31 - identically under both independent readers - lean on something
inside their own declared call to action**, almost always "the Lucy visibility
system" arriving named in the closing pitch. That is the closer the captain
asked for. The gate was failing reels for carrying it, which is a gate that
FAILS CORRECT OUTPUT - the same defect as one that cannot fail, from the other
side (AGENTS.md 10.4).

---

## Defect 2 - the duration band is a preference, and it was deciding

I read the brief rather than picking a number. `creative_brief.md`, "Length":

> "no fixed target but preferably between 45-90 seconds (this is for short form
> content on social media)"
>
> No hard cap. What he rejected before was collage, not length - a coherent
> 90-second reel is right, a stitched 47-second one is not.

Four statements, and the last settles it: **a 47-second reel is inside the band
and can still be wrong.** Length was never the thing that disqualifies; collage
is. `reel_exchange.LENGTH_GUIDANCE` - the constant the bar imports - already
says so in its own docstring ("GUIDANCE THE MODEL WEIGHS, never a boundary this
module enforces") and records that it stopped being a hard window on 2026-09-04
because enforcing it "silently withheld every stretch needing longer to finish".
The bar imported that constant and made it an ERROR again, re-creating four days
later the exact defect that had been removed from its source.

**No number changed.** `duration_reading` measures what it measured before;
`within_guidance` still reads False for the same ten reels; the finding still
fires and still names the side. Only the severity moved, in both places that
held the band - `QB-DURATION` and the verifier's `PQ-LENGTH`.

The ERROR that remains is `reel_exchange.ABSURD_SECONDS`, imported and not
restated: past five minutes a "reel" is most of the episode. That is MECHANICAL
rather than editorial (AGENTS.md 10.5), and it is the only length bound the
brief leaves standing. **It fired on 0 of the 31** - said here rather than left
to read as coverage. The brief declares no floor, so the bar holds none.

---

## The bar re-run over the same 31 proposals

Same proposals, same transcript, same two readings, same instrument.

| | OLD (harvest, #593) | NEW |
|---|---|---|
| reels | 31 | 31 |
| **PASS** | **0** | **30** |
| **FAIL** | **31** | **1** |

| finding | OLD error | OLD warning | NEW error | NEW warning |
|---|---|---|---|---|
| `QB-DURATION` | **10** | 0 | 0 | **10** |
| `QB-NOT-FOLLOWABLE` | **31** | 0 | 0 | **31** |
| `QB-NO-TAKEAWAY` | 1 | 0 | **1** | 0 |
| `QB-CTA-SHARED` | 0 | 31 | 0 | 31 |

Every measurement is byte-identical across the two runs - `duration`,
`delivered_seconds`, the coherence reading and the value reading all compare
equal, and `within_guidance` is False on the same ten reels. **Only what decides
pass/fail changed.**

The one remaining failure is **reel 22**, on `QB-NO-TAKEAWAY`: nothing in it can
be quoted as something a listener could repeat or act on. Both independent
readers found that reel and only that reel, and the selector had already flagged
it - "the first thing to cut if the batch should stay instructional". Three
independent judgements agreeing is the strongest signal in the harvest run, and
it is what the bar still fails on.

### Stability - the new counts do not depend on which model read the reels

| | reader 1 (run of record) | reader 2 (blind control) |
|---|---|---|
| OLD bar | 0 pass / 31 fail | 0 pass / 31 fail |
| NEW bar | **30 pass / 1 fail (reel 22)** | **30 pass / 1 fail (reel 22)** |

The old bar was stable and uninformative. The new one is stable *and*
discriminating, because the quality that could not tell reels apart no longer
decides anything.

---

## Both directions, on the captain's real footage

**It still fails bad output** - real reel 01, the reel both readers ranked
first, perturbed one property at a time:

| reel 01 | runs | verdict | error |
|---|---|---|---|
| as the captain has it | 51.5s | **pass** | - |
| with its call to action removed | 51.5s | **fail** | `QB-CTA-ABSENT` |
| with its closer moved inside its body | - | **fail** | `QB-CTA-IN-BODY` |
| body stretched to 200s | 195.9s | **pass** | none - `QB-DURATION` warns |
| body stretched to 330s | 314.6s | **fail** | `QB-ABSURD-LENGTH` |

**It no longer fails correct output** - the ten reels outside the preference are
still measured and still reported, and none of them is now failed for its length:

| reel | runs | outside by | verdict |
|---|---|---|---|
| 04 | 18.1s | 26.9s | pass |
| 14 | 29.6s | 15.4s | pass |
| 02 | 39.5s | 5.5s | pass |
| 26 | 39.6s | 5.4s | pass |
| 08 | 40.3s | 4.7s | pass |
| 11 | 40.4s | 4.6s | pass |
| 06 | 43.1s | 1.9s | pass |
| 19 | 43.8s | 1.2s | pass |
| 03 | 44.7s | **0.3s** | pass |
| 31 | 93.1s | 3.1s | pass |

---

## How I know these numbers are about the reels and not about the tool

Every large finding in this project so far has been the instrument, so:

1. **The baseline was reproduced before anything was touched.** Running the
   unmodified bar over the proposals on disk produced a report **byte-identical**
   to `data/vep-harvest/bar_report.json` (`json.dumps(..., sort_keys=True)`
   compares equal). Same instrument, same inputs, same published answer - so the
   "before" column is not my reconstruction of the harvest, it *is* the harvest.
2. **The before/after diff was checked field by field, not just in the totals.**
   `duration`, `delivered_seconds`, `coherence` and `value` compare equal on all
   31 verdicts. A repair that moved a measurement would have shown up there.
3. **The shipped code was checked against the throwaway analysis harness.** The
   scratch script that found "26 of 31 in the call to action, 18 opening" and the
   `dependency_positions` that shipped agree exactly. Two implementations, one
   answer.
4. **Every rejected candidate was rejected by a measurement, not by argument** -
   0 of 31, 401 of 940, 9 of 31, 14-versus-23 of 31. The anaphor candidate was
   killed by a *specific case it got backwards* (reel 11), not by a general
   worry.
5. **The control reader was run through the new bar too**, and gives the same
   verdict. A repair that had smuggled in reader-dependence would have diverged
   there, which is precisely what the rejected fifth candidate does.

What I did **not** verify: whether `value` has the same discrimination problem
coherence had. It separated 30/1 on this batch and both readers picked the same
reel, but `FIRST_MEASUREMENT` records it reading 25/0 on the previous batch. One
batch cannot settle it, and nothing here was changed on that basis.

## Files

| file | what it is |
|---|---|
| `report.md` | this |
| `bar_report_after.json` | the new bar over all 31, reader 1 |
| `bar_report_after_control_reader.json` | the same 31 under the blind control reader |

Compare against `data/vep-harvest/bar_report.json` and
`bar_report_control_reader.json`, which are unchanged.
