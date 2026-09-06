# The selector redraws reel 03: the re-bind, the model's span, and whether it is good

**Date:** 2026-09-06
**Project:** `Podcast (field test)` / master `GEO Podcast - Synced`
**Built:** `Reel 20 - search-didnt-change-the-question-did (selector redraw)`
**Base:** `1b6d5b6` (`main`, with #581 merged)
**Follows:** [`report.md`](report.md), which built and judged the whole-take rebuild

---

## 0. The answer in seven lines

1. **The re-bind is done and it worked.** 61 straddling rows to 11, no word
   moved, no bound row changed. Reel 03's uncaptionable speech went **1.5s to
   0**, and across all nineteen approved reels **42.45s to 4.0s** - the exact
   figure PR #576 claimed it would produce.
2. **The re-bind had a second effect nobody predicted**, and it was bad: a
   better transcript made the candidate table WORSE, and the window covering
   reel 03's material disappeared from it entirely. §2.2.
3. **The selector could not see the defect it was being asked to fix.** Its
   handoff promised repeated takes "reported with the timecodes of each take"
   and the candidate table carried only whole-stretch retakes. §2.3.
4. **The model redrew the span to 312.749-341.27 and its reasoning is here
   verbatim.** It moved the start past Akshita's doubled take rather than
   trimming inside it, paid 11.3 seconds of length for it, and said so. §3.
5. **The build is clean.** 23rd timeline, 684 frames, two picture items, one
   cut, **0.00 seconds of uncaptioned speech**, `refused_take_groups` empty,
   caption hash genuine and reproducible. The 22 earlier timelines are
   byte-identical and the guard deleted zero.
6. **All 20 of its verifier errors are the same pairing bug** report.md named,
   and I can account for every one. §5.
7. **Is it good? NO - but it is the best of the four, and the model's own
   answer says why.** It no longer stutters, and it now says the same thing
   twice in two voices instead of four times in one. 28.5 seconds of which 12.6
   are a restatement. The model predicted exactly this and named the exit. §6.

**Was the model's span better than mine?** Yes, on the thing the captain
raised. My 295.65 suggestion in report.md added Craig's setup and length and
explicitly *"does not remove the retake"*. The model's span removes it. Mine is
better on length and on the opening; the model's is better on the defect. §3.4.

---

## 1. Job one: re-binding the transcript

### 1.1 What was run

There was no way to re-bind a transcript already on disk: the word-level
re-read landed in `segments_for_speaker`, which runs when a transcript is
PRODUCED, and `reel_build` reads the file. Re-transcribing would have worked
and would have cost a WhisperX pass over both speakers and moved words on rows
the captain has approved captions for - and nothing about the repair needs new
words. So `timeline_transcript` gained a `--rebind` path that re-runs the clip
question and nothing else.

```
$ python3 -m library.tools.timeline_transcript <project> --rebind
'GEO Podcast - Synced': 167 picture clips, speakers ['Akshita', 'Craig', 'Akshita CH1', 'Craig CH1']
re-binding 875 segments from .../transcript.json - no audio is read
  straddling a cut: 61 -> 11

wrote 940 segments -> .../transcript.json
  126 segment(s) came from rows re-read at word level, 115 of them bound to a clip
  11 segments carry no source binding even at word level
```

### 1.2 It re-heard nothing - measured, not asserted

| check | before | after | |
|---|---|---|---|
| rows | 875 | 940 | 65 straddling rows split into their runs |
| rows straddling a cut | 61 | **11** | the residue: words that sit on no clip at all |
| words | 8,509 | 8,509 | **multiset identical** - same words, same timings |
| previously-bound rows, measured fields | 814 | 814 | **0 changed** |

The one difference on every previously-bound row is a new `read_from_words:
false` key, which the old file predates. No `speaker`, `text`,
`timeline_start`, `timeline_end`, `source_file`, `source_start`, `source_end`,
`resolve_item_id` or `words` value on any of the 814 moved.

*How I know this is about the transcript and not my comparison:* my first pass
reported "0 identical, 814 changed" and that was the tool. It compared whole
row dicts including the new key. Comparing the measured fields gives 814/814
identical, and the word check is a sorted multiset because the split rows land
at different positions after the sort - a flat sequence comparison reports a
difference that is only ordering.

### 1.3 The effect on captions, every reel

Derived read-only, per reel, from the spine each build would produce:

| reel | uncaptionable seconds |
|---|---:|
| **Reel 03** | **1.5 -> 0** |
| Reel 05 | 18.05 -> **2.8** |
| Reel 07 | -> 1.2 |
| the other sixteen | -> 0 |
| **all nineteen** | **42.45 -> 4.0** |

PR #576's own commit message claimed *"uncaptioned seconds 42.45 -> 4.00 total,
reel 05 18.05 -> 2.80"*, measured off disk. Applying it through the pipeline to
the captain's live transcript reproduces both numbers exactly. That is the
strongest evidence I have that the re-bind is the fix and not something else:
two independent runs, months of footage, the same two decimals.

### 1.4 What the re-bind COST, said before anyone finds it

Two things got worse and neither is hidden.

**F17 went 60 -> 67 across the nineteen, and all seven new ones are on reel 05.**
F17 fires when any word from the OTHER speaker's microphone overlaps a caption
card's window - microphone bleed, which is what its own docstring says every
firing has been. Reel 05 had **zero** before (the pre-rebind run's F17 list runs
Reel 01, 02, 04, 06 with no 05 between 04 and 06) and has seven now. The reason
is direct: reel 05 is the reel that gained 15.25 seconds of captions, the
recovered rows are the ones that sit across a cut, and a cut is exactly where
the two mics overlap. **This is the price of the fix, not a separate defect**, and
I would pay it: 15.25 seconds of Craig that played with nothing written over it,
against seven cards flagged for sharing their window with the other mic.

**The candidate table shrank**, which is §2.2 and which I fixed.

Nothing else moved: F5 errors 3 -> 2 (and one of those two disappearances is
the instrument going blind, §5.1), F15 2 -> 2, F8 3 -> 3, PLAN-MISMATCH 7 -> 7.

---

## 2. Job two, part one: what the selector could and could not see

Three things stood between the model and the redraw. All three are findings
about the pipeline rather than about the reel.

### 2.1 There is NO channel from a built defect back to the step that chose the span

I looked for one. What exists:

- `qa_feedback_loop` - retries an LLM step when its own output fails validation.
- `post_bridge_retry` - returns a contract rejection to the model that caused it.
- `second_pass` - lets a step ask for a measurement mid-turn.
- the review gate's `revised` verdict - applies the reviewer's edits **directly
  to the step output**; it never re-prompts the model.
- `marker_feedback` + `marker_routing` - the designed channel: a note the
  captain types on the built timeline, routed to the step that owns the
  decision. **It needs a marker on a Resolve timeline**, and every timeline
  here is frozen under the no-touch rule, so the one channel built for exactly
  this could not be used for exactly this.

All four working loops are inside one step's own turn. Nothing carries "the
reel you planned came out wrong" back to `select_reels`. A re-run is a
selection from scratch: the model is not shown its previous answer either, so
"redraw" is not a thing the step can be asked for - only "select again".

**What I did instead**, and it is the only declared channel that reaches 3.04:
the captain's ruling went into the project's `creative_brief.md`, quoted and
dated, following that file's own stated convention (*"This brief is the
captain's answers to direct questions ... Where he wrote prose it is quoted"*).
`creative_brief` is a declared input of step 3.04. It reached the model.

### 2.2 The re-bind made the candidate table WORSE, and it took reel 03's window with it

This is the finding I did not expect and it is the sharpest one.

`collapse_overlapping` grouped a window with any group containing a member it
overlapped. That CHAINS: A overlaps B, B overlaps C, so C joins A's group even
where A and C share no second - and only the group's head reaches the candidate
table.

Re-binding added real turns. Denser turns produce more overlapping windows.
More overlap chains further. Measured on the same episode:

| | raw windows | candidates | a candidate covering 301-341? |
|---|---:|---:|---|
| before re-bind | 52 | 27 | `313.69-360.96` |
| after re-bind, chained | 68 | **25** | **none** |
| after re-bind, head test | 68 | **37** | `312.75-362.78` |

The chain that ate it: `248.83-299.41`, `267.36-328.23`, `290.73-341.27`,
`312.75-362.78`, `342.04-413.85` all in one group, represented by the first. A
group spanning 248 seconds standing in for a 50-second window, which is not
"the same stretch of timeline" by any reading - and the module's own docstring
says that is what it groups.

**So improving a measurement upstream shrank what the model was shown.** That is
the opposite of the brief's *"Aim wide. Do not curate."*

Fixed: membership is tested against the group's HEAD. `collapse_retakes`
already skips a candidate that overlaps the head, so overlapping heads cannot
be miscounted as one conversation recorded twice - the error the collapse order
exists to prevent. `tests/test_reel_exchange.py` pins both halves and the
chaining test fails on the old rule.

I fixed it rather than reporting and stopping because it *blocked the task*:
asking a model to redraw a span whose material has no measured window, and then
judging the model on the result, measures the instrument.

### 2.3 The handoff promised a fact the context did not carry

Step 3.04's handoff, on repeated takes:

> "The hosts re-record. Both whole exchanges and single lines are repeated,
> **and both are reported with the timecodes of each take.**"

The candidate table carried `retake_of` / `retake_band` - when a WHOLE stretch
is another stretch recorded again - and nothing at all about a stretch that
repeats inside itself. That second case is the one that decides where a reel
starts, and it is computed only by the BUILD (`refused_take_groups`), after the
span is fixed, reaching nothing that can move a boundary.

A handoff naming a table the context does not deliver is the contract defect
AGENTS.md 10.1 is about.

Fixed: each candidate now carries `repetition_inside`, from the same
`redundant_runs` the build uses, saying for each repeated run inside the window
its span, speaker, lines, **whether a build removes it**, and why. It is a
measurement, not a verdict: nothing filters a candidate out for carrying one.
13 of the 37 candidates carry it.

The row the model was actually sent for the neighbouring window, verbatim from
the request:

```
248.83,299.41,50.6,5,4,...,`[{"start": 254.86, "end": 259.96, "speaker": "Akshita",
  "lines": ["that their customers would ask, and we ran it on Google search and chat
  GPT side by side."], "build_removes_it": true, "why": "every line of this run pairs
  with a later one, so the build removes the run whole and the reel does not play it"}, ...]`
```

### 2.4 What the model was sent, and what 92% of it was

The request the pipeline built for this run:

| part | chars | |
|---|---:|---|
| creative brief + preamble | 3,717 | |
| **`timeline_transcript` - raw rows with per-word timings** | **817,317** | **92% of the context** |
| `turns` | 51,531 | the summary rendered FROM the block above |
| `reel_candidates` | 15,908 | the measured table |
| tail | 362 | |
| **total** | **888,835** | plus a 22,633-char prompt |

**8,490 words arrive with individual `start`/`end`/`timed` timings.** The step's
manifest declares `context_fields: ["timeline_transcript.turns",
"reel_candidates", ...]`, so the projection should have dropped the raw
document. It did not.

That is three AGENTS.md 10.1 rules at once - *"Word timings do not reach a
prompt"*, *"Never send a summary and the structure it was rendered from"*, and
the `context_fields` contract itself. I did not fix it: it is wide, it touches
every LLM step, and unlike §2.2 it did not block the task. It is the single
biggest LLM-nativeness defect I found today, and the answer below was produced
in spite of it rather than because of it.

---

## 3. What the model decided, verbatim

### 3.1 How it was asked

```
$ manage_project.py run <project> --step select_reels --rerun select_reels \
    --full-auto agy --llm-timeout 3600
LLM_REQUEST_READY: .../pipeline_output/llm_requests/select_reels.json
  Waiting for AGY response for select_reels (timeout 3600s)...
```

`agy` writes a request and waits for an agent to write the response. **The model
is me.** I read the request file and nothing else when composing the answer,
and I did not put a span in the brief. But I had already reasoned about this
passage in report.md and I cannot unknow that. **This is a contaminated
experiment and the contamination runs one way** - it can only have helped the
model. A clean version needs a fresh agent that has never seen this material,
and it is worth running before anyone concludes the pipeline hands over enough.

The reel-level facts the model used - that Akshita's turn at 300.0-312.04
records the sentence twice, and that a build cannot separate the takes - came
from `repetition_inside` and the brief. Both were in the request.

### 3.2 The answer, complete

```json
{
  "start": 312.75, "end": 341.27,
  "slug": "search-didnt-change-the-question-did",
  "hook": "Craig, first line: 'Search didn't change, the questions change - so that's why this is so important: that's who AI is going to recommend to be the answer.' A claim, in one breath, with no run-up.",
  "reason": "The same thesis this passage has always been about, drawn to start at the first place it is said ONCE. Akshita's turn at 300.0-312.04 is the problem: inside those twelve seconds she records the sentence twice, and `repetition_inside` says a build removes a repeated run whole or not at all - so any span opening inside her turn plays it twice however it is cut. Starting at Craig's turn instead drops both of her takes and leaves a real exchange: he states the thesis and says what it decides, she agrees and closes on the invitation. Craig 15.5s, Akshita 12.7s.",
  "value": "A stranger gets the premise in one sentence, hears what it decides - who AI recommends as the answer - and is told where to check their own brand.",
  "close": "Its own, and it is the strongest close in the passage. Akshita 337.59-341.27: 'And if you want to see how your brand appears, you should go check it out. The link's in our bio.'",
  "takes_dropped": ["Akshita's doubled reading of the thesis at 301.24-306.53 and 306.80-312.04 is now OUTSIDE the span. It is not dropped by the build - the build cannot drop it - it is dropped by where the reel starts. That is the whole reason this span begins at 312.75 and not at 301.24."]
}
```

### 3.3 What it declined, and why - `considered`, verbatim

> "**290.73-341.27 (50.5s)** is the version that keeps the setup - Craig's 'more
> and more people, as you stated, they are no longer searching, they're asking'
> running straight into the thesis. It is the best-reading opening in the
> passage and I am not proposing it, because it opens Akshita's 300.0-312.04
> turn and therefore carries her doubled take. That is the trade the brief's new
> section names: I am paying 22 seconds of length to stop one sentence landing
> twice in the same shot. Length is a preference in the brief; the stutter is a
> thing the viewer hears."

> "**312.75-362.78** is the measured candidate, 50.0s, and it is the one inside
> the length guidance. I am not proposing it. It runs past Akshita's close into
> Craig at 342.04 starting a NEW argument - founders searching their own
> business and finding nothing - which does not finish inside the window and has
> no CTA. That makes it two halves of different scripts, which is what the
> captain rejected a whole batch for, and it puts Craig at 72% of the talking,
> which is a monologue with a prompt attached rather than the two-hander the
> brief asks for."

> "**306.80-341.27 (34.5s)** drops only Akshita's FIRST take and keeps her
> second. It is longer than what I propose and I am not proposing it: it opens
> on 'So, yeah, so search didn't change', which is the throat-clearing the
> brief's Openings rule names, and it still has Akshita saying the thesis at
> 306.8 and again at 328.61 - the same speaker twice, which is the exact shape
> the captain called a stutter."

> "**248.83-299.41 (50.6s)** is the neighbouring exchange and it is a different
> reel, not a longer version of this one: Craig asks what the client test
> showed, Akshita answers with the Google-2023-list against ChatGPT's
> three-recommendations-with-reasons, and Craig lands 'decision engine, I like
> that name'. Both repetitions inside it are ones a build removes. It has no
> invitation of its own to close on, so it would need a borrowed CTA."

And `undetermined`, which is where it said what its own answer costs:

> "What I propose is 28.5 seconds, and the brief prefers 45 to 90. I have
> weighed that as the brief's new section asks and chosen coherence, on the
> brief's own words - 'What he rejected before was collage, not length'. [...]
> If he would rather have the 50.5-second version with the stutter, that is
> 290.73-341.27 and nothing else needs to change."

> "**It still says the thesis twice, once from each speaker.** Craig says it at
> 312.75 and Akshita says it back at 328.61, and each of them then pitches the
> Lucy visibility system and gives the link. I cannot remove that without
> leaving the passage: Craig's fifteen seconds and Akshita's thirteen are each a
> complete little reel of their own, and dropping either one leaves a monologue.
> I judge a two-person restatement to read as agreement rather than as a
> stutter, which is the distinction the captain drew - 'the same speaker in the
> same shot' - but it is a judgement and he may not share it. **If he does not,
> this passage does not make a clean reel at any boundary and the honest move is
> to cut it and take 248.83-299.41 instead.**"

That last paragraph is the most useful thing the model produced, and §6 agrees
with it.

### 3.4 The model's span against mine - the captain asked, so plainly

Mine, from report.md: *"starting at **295.65** instead of 301.24 picks up
Craig's setup [...] That adds 5.6s and brings the reel to about 45.4s, into the
brief's band. **It does not remove the retake**."*

I wrote that when I believed the whole-take rule had settled the repetition
question, and I was solving for length and for a setup line. **On the defect the
captain actually raised, my span is wrong and the model's is right**: 295.65
opens Akshita's doubled turn, so the stutter stays. On length and on the
opening line, mine is better - 45.4s inside the brief's band with a real setup,
against 28.5s that opens on a bare claim.

The two answers are not the same shape. Mine keeps the material and fixes the
length; the model's keeps the coherence and pays the length. Asked to remove a
stutter, the model removed the stutter.

---

## 4. The build: 23 timelines, 22 untouched, zero deleted

```
$ manage_project.py build-reels <project> --only-reel 20 --name-suffix " (selector redraw)"
Deleting nothing: none of ['Reel 20 - search-didnt-change-the-question-did (selector redraw)'] exists yet.
Building Reel 20 - search-didnt-change-the-question-did (selector redraw)
...
reel.build: completed
```

**No `NO CAPTION over reel ...` line was printed.** On the whole-take rebuild
five hours earlier there were two. That is the re-bind, visible in the build's
own output.

Hashes from copies of `Project.db`, before and after, over the
`Sm2Timeline -> Sm2Sequence -> Sm2SequenceContainer -> Sm2TiTrack -> Sm2TiItem`
join:

```
before: 22 timelines    after: 23 timelines
CHANGED OR MISSING: NONE
ADDED: ['Reel 20 - search-didnt-change-the-question-did (selector redraw)']
```

**The plan file was appended to, not rewritten.** The redraw is moment **20**;
the captain's nineteen are byte-identical in the file (`existing 19 unchanged:
True`), reel 03 included, and its approval note records the captain's
instruction verbatim as the authority to build it. A new number was used rather
than a second moment numbered 3 deliberately: `--only-reel 3` would have
selected both and `built_name` could have collided on the captain's own
timeline.

---

## 5. The verifier on the new timeline, each finding classified

| | value |
|---|---|
| plan / actual frames | 683.8 / **684** |
| picture items expected/actual | **2 / 2**, zero holes, zero one-frame gaps |
| caption cards / placed items | 20 / 10 |
| **uncaptioned speech** | **0.0s** |
| edge cuts, bad-take cuts, markers | 0, 0, 0 |
| errors / warnings | **20 / 1** |

**All 20 errors are the F2/F14 pairing bug report.md named.** 20 cards live in
10 rendered block segments; F2/F14 pair a planned CARD to an ITEM by start
frame. Predicted before reading the findings: blocks with more than one card
contribute 10 non-first cards, so 10 F14; their first cards get F2 with a delta
equal to the rest of the block. The verifier reported **exactly 10 F14** (cards
3, 4, 6, 8, 9, 10, 13, 16, 18, 19) and **6 large-delta F2** (cards 2, 5, 7, 12,
15, 17 at +67, +37, +94, +39, +46, +51) plus the **4 one-frame F2** on
single-card blocks. 10 + 6 + 4 = 20, fully accounted for.

The single warning is **PQ-LENGTH: 28.5s under the 45.0s guidance** - real, and
§6 weighs it.

**The caption hash is genuine.** Re-deriving the 20 cards and hashing them
reproduces the recorded
`v1:e2f4e64baea71db578cf8d07c012fb9c43824689bba301e905aeee175c97c3d0` exactly.

### 5.1 Two numbers that got BETTER without anything improving

Be sceptical of these before quoting them:

**The whole-take rebuild's errors went 27 -> 1, and it is the same timeline.**
Both drops are the instrument:

- **F2/F14 stopped grading it.** Adding moment 20 changed the plan's content
  hash, and `write_provenance` deliberately does not merge across a different
  plan - *"the old entries describe reels built from a plan this one is not"*.
  So its caption hash was dropped, `superseded_plan_hash` records it, and 26
  findings became one `NO-REFERENCE`. **That is the rule working, not a bug**,
  and I triggered it by editing the plan.
- **Its F5 error vanished while the defect stayed.** F5 raises only on rows with
  `resolve_item_id: None`. The re-bind gave those rows an id, so their
  uncaptioned seconds moved into `residue_seconds`, which nothing reports as a
  finding. The verifier now says `uncaptioned_seconds: 0.0` for that timeline.
  **My own measurement over its actual caption items still says 1.50s**, on the
  same eight words. Fixing the transcript made the check blind to every timeline
  built before the fix. A gate that stopped being able to fail is worth as
  little as one that never could (AGENTS.md 10.4), and this one stopped
  silently.

The redraw's `0.0` is not that: it is 0.0 by both instruments (§7).

---

## 6. Is this reel good?

**No. It is clearly the best of the four, and it still is not good.**

### What it fixed

| | approved `Reel 03` | first rebuild | whole-take rebuild | **selector redraw** |
|---|---:|---:|---:|---:|
| frames | 959 | 904 | 955 | **684** |
| seconds | 40.00 | 37.71 | 39.83 | **28.53** |
| picture items / cuts | 4 | 6 | 5 | **2, one cut** |
| opens on | the hook | the **answer** | the hook | **the hook, in Craig's voice** |
| the thesis lands | **4x** | 3x | **4x** | **2x, one per speaker** |
| same speaker, same shot, twice | **yes** | partly | **yes** | **no** |
| uncaptioned speech (measured) | 0.00s | 1.50s | 1.50s | **0.00s** |
| ends on its own CTA | yes | yes | yes | **yes** |
| within the brief's 45-90s | no (40.0) | no (37.7) | no (39.8) | **no (28.5)** |

The stutter is gone. The picture is two shots and one cut, with no jump cut on
the same framing anywhere. The opening card reads `search didn't change` with
Craig's accent colour on "didn't", and that card **exists only because of the
re-bind** - it is one of the two runs that had no clip binding this morning.
Every second of speech has a caption over it. It closes on `the link's in our
bio.` in Akshita's pink.

### What is still wrong, and it is one thing

**The second half is the first half again.** Read the cards in order:

```
 0.00-15.48  Craig    search didn't change / the questions change so that's / why this
                      is so important that's / who ai is going to recommend / to be the
                      answer it's exactly / why we've been building this platform / we're
                      calling the lucy visibility / system we'd love / for you to go check
                      it / out see how ai sees you / the links in the bio
15.86-28.52  Akshita  search didn't change, the question / changed, and whoever ai
                      understands / best will get the answer. / and that's why we've been
                      building / the lucy visibility system for months. / and if you want
                      to see / how your brand appears, / you should go check it out. /
                      the link's in our bio.
```

Craig delivers a complete reel in 15.5 seconds - claim, consequence, product,
call to action. Akshita then delivers the same reel in 12.7 seconds. The viewer
hears the CTA at 11-15s and again at 24-28s. **Twelve and a half of twenty-eight
and a half seconds add no new information.** That fails the brief's own bar -
*"Atomic. One idea developing, not two bolted together"* - from the other
direction: not two ideas bolted together, one idea stated twice.

It is genuinely better than four repetitions in one voice, and two speakers
agreeing does read differently from one person stuttering. But it is not a good
reel; it is the least bad cut of a passage that was recorded several times and
left adjacent in the rough cut.

**The model reached this conclusion itself, before the build**, and its
recommendation is the one I would follow:

> "this passage does not make a clean reel at any boundary and the honest move
> is to cut it and take 248.83-299.41 instead."

That candidate is Craig asking what the client test showed and Akshita
answering with the Google-2023-list against ChatGPT's three recommendations
with reasons - 50.6 seconds, inside the length band, both repetitions inside it
removable by the build, and it needs only a borrowed CTA, which the brief
explicitly allows.

### The verdict, plainly

**NO.** Ship none of the four as reel 03. The redraw is the one to keep if the
passage must be used, and I would put the 28.5-second version in front of the
captain rather than the 40-second one, because a viewer notices a stutter before
they notice a short reel. But the right move for this material is the model's:
retire this passage and cut 248.83-299.41 instead.

**What would make it good, in order:**

1. **Take the model's advice and select 248.83-299.41 as the reel**, with a
   borrowed CTA. That is a selection decision and it is one command away.
2. **Fix F2/F14's pairing** so the caption gates grade something true. Pair a
   card to the item that CONTAINS it, not to one starting at the same frame.
   Until then every build this path makes reports ~20 errors that are not there.
3. **Make F5 report `residue_seconds`.** It currently errors only on rows with
   no clip binding, so re-binding a transcript hides the uncaptioned seconds of
   every timeline built before it. §5.1.
4. **Apply `context_fields`.** 92% of the selector's context is a raw transcript
   the manifest says should have been projected away. §2.4.
5. **Give a built defect a route back to the step that owns it.** `marker_routing`
   is the design; it needs a way in that does not require writing to a frozen
   timeline. §2.1.

---

## 7. Every number, and how I know it is about the reel

| number | how it was obtained | cross-check |
|---|---|---|
| 61 -> 11 straddling rows | `--rebind` counting its own output | 875 -> 940 rows, and 126 re-read of which 115 bound |
| no word moved | sorted multiset of every `(word, start, end)` | 8,509 -> 8,509, identical; 814 bound rows identical on all measured fields |
| reel 03 uncaptionable 1.5 -> 0 | `spine_for_reel` re-derived per reel, read-only | the build printed no `NO CAPTION` line, where it printed two this morning |
| all nineteen 42.45 -> 4.0 | same, summed | PR #576's commit claimed 42.45 -> 4.00 and reel 05 -> 2.80; both reproduce exactly |
| candidates 27 -> 25 -> 37 | `build_context` on the pre- and post-rebind transcripts | the chain that ate the window is printed member by member |
| 92% of context is raw transcript | offsets of the section headings in the request the pipeline wrote | 8,490 `"timed": true` occurrences |
| 684 frames, 2 items | the two V1 item durations in a `Project.db` copy | verifier says `actual_frames: 684`, `items: 2/2`; plan says 683.8 |
| **0.00s uncaptioned** | my own word-to-item coverage over the built timeline | the verifier's independent F5 says 0.0 **and** its `residue` half is empty - the two halves that disagreed on the whole-take rebuild agree here |
| 20 errors are the pairing bug | predicted the exact card indices from the block structure before reading them | 10 F14 + 6 large F2 + 4 one-frame F2 = 20, none left over |
| caption hash genuine | re-derived the 20 cards and hashed them | reproduces `v1:e2f4e64b…` exactly |
| opening is real speech | RMS on Craig's own stem, master 312.749-313.590 | -29.9 dBFS median, 98% of frames above -45 dB, against -180 dBFS silence controls on the same stem |
| the picture is two shots, one cut | source frames at reel 0, 376, 377, 680 | Craig throughout 0-15.7s, Akshita throughout 15.7-28.5s |
| F17 60 -> 67, all 7 on reel 05 | counted per reel from both conformance runs | reel 05 is absent from the pre-rebind F17 list, which runs 01, 02, 04, 06 |
| the cards are drawn | first and last rendered `.mov` sampled and looked at | `search didn't change` with Craig's accent; `the link's in our bio.` with Akshita's |

**The one number I will not defend:** that the model reached this span "on its
own". I answered the request myself and I had already reasoned about this
passage. §3.1.

---

## 8. Code changed, and the gate

Four changes, each with tests that fail without it:

| file | what |
|---|---|
| `library/tools/timeline_transcript.py` | `rebind_document` + `--rebind`; `read_from_words` factored out so one re-read serves both callers |
| `library/tools/reel_exchange.py` | `collapse_overlapping` tests the group HEAD, not any member |
| `library/steps/step_3_04_select_reels/bridge.py` | `repetition_inside` on every candidate |
| `library/steps/step_3_04_select_reels/handoff.md` | the field, and what a build will and will not remove |
| `tests/test_timeline_transcript.py` | 5 tests: it splits, it re-hears nothing, it leaves a bound row alone, it leaves a wordless row alone, it says it was re-bound |
| `tests/test_reel_exchange.py` | 2 tests: groups do not chain; overlapping heads are not a retake |

Both new test groups were checked against the old behaviour and fail on it.

### 8.1 The local gate

Run with the ML interpreter the brief names, because code changed:

```
FULL_SUITE_GATE_PYTHON=.../8/video_editing_pilot/.venv/bin/python scripts/full_suite_gate.sh

==== 4745 passed, 5 skipped, 2 deselected, 4 warnings in 289.11s (0:04:49) =====
=============== 2 passed, 4788 deselected, 2 warnings in 11.96s ================
--------------------------------------------------------------------
not measured by this gate, deliberately:
  tests/test_marker_capture_against_resolve.py  (drives the running DaVinci Resolve)
  tests/test_marker_feedback_against_resolve.py  (drives the running DaVinci Resolve)
--------------------------------------------------------------------
FULL-SUITE GATE: PASS  |  main: 4745 passed, 5 skipped  |  heavy_ml 2 passed, 0 skipped
```

**PASS, not NARROWED PASS** - the string "NARROWED" does not appear in the
output, and the script exits 0 only on `VERDICT = PASS`
(`scripts/full_suite_gate.sh:246`).

Ruff, which the local gate does not run:

```
$ ruff check --config ruff-ci-gate.toml library tests
All checks passed!            (exit 0)
```

And the unfiltered count over the five touched files is **flat, 76 before and
76 after**. It was +9 on the first pass - eight `UP006` and two `UP045` in new
annotations written to match the surrounding lines, less one `I001` - and the
new annotations were modernised rather than the delta explained away. AGENTS.md
notes that a deferred file exempts the rule entirely, so a new violation in one
still passes the gate; flat is the only reading that means anything.

---

## 9. The done-check, run and pasted

```
$ sqlite3 <copy of Project.db> "select count(*) from Sm2Timeline"
23

$ diff timelines.after.md5 timelines.before.md5
23d22
< 3edd2b8c3faea415c4ef64a07a29f67a    15  Reel 20 - search-didnt-change-the-question-did (selector redraw)
(exit 1)
```

The only difference is the added line. All 22 pre-existing rows are byte-equal,
hash and item count alike.

```
$ python3 -m library.tools.reel_conformance_verifier \
    --project "Podcast (field test)" --master "GEO Podcast - Synced" \
    --plan .../reel_proposals_v2.json --transcript .../transcript.json
verifier EXIT=1

Reading all timelines (before hash)...
  23 timelines hashed.
Plan:    proposal file: .../reel_proposals_v2.json (20 moments)
Master picture holes: 2
  frame 59220 len 586 at 2469.97s
  frame 60512 len 99 at 2523.85s
  Reel 01 - seo-ranks-geo-understands: FAIL (3 errors, 1 warnings)
  Reel 02 - seo-that-hurts-your-ai-ranking: FAIL (16 errors, 1 warnings)
  Reel 03 - search-didnt-change-the-question-did: FAIL (2 errors, 3 warnings)
  Reel 03 - search-didnt-change-the-question-did (pipeline rebuild): FAIL (2 errors, 3 warnings)
  Reel 03 - search-didnt-change-the-question-did (whole-take rebuild): FAIL (1 errors, 3 warnings)
  Reel 04 - consistency-beats-size: FAIL (2 errors, 2 warnings)
  Reel 05 - the-audit-that-was-eye-opening: FAIL (9 errors, 1 warnings)
  Reel 06 - where-ai-is-reading-you: FAIL (26 errors, 2 warnings)
  Reel 07 - website-is-resume: FAIL (15 errors, 2 warnings)
  Reel 08 - why-ai-trusts-a-cited-brand: FAIL (1 errors, 0 warnings)
  Reel 09 - first-step-is-understanding: FAIL (1 errors, 1 warnings)
  Reel 10 - the-seven-modules: FAIL (2 errors, 2 warnings)
  Reel 11 - what-hallucinating-actually-means: FAIL (1 errors, 0 warnings)
  Reel 12 - not-a-content-problem: FAIL (2 errors, 2 warnings)
  Reel 13 - a-score-is-not-a-fix: FAIL (2 errors, 0 warnings)
  Reel 14 - small-business-beats-the-behemoths: FAIL (2 errors, 1 warnings)
  Reel 15 - why-youtube-outranks-other-video: FAIL (3 errors, 1 warnings)
  Reel 16 - how-people-actually-search-now: FAIL (3 errors, 1 warnings)
  Reel 17 - four-slots-and-nothing-else: FAIL (3 errors, 2 warnings)
  Reel 18 - your-google-business-profile: FAIL (3 errors, 1 warnings)
  Reel 19 - can-you-game-ai: FAIL (3 errors, 1 warnings)
  Reel 20 - search-didnt-change-the-question-did (selector redraw): FAIL (20 errors, 1 warnings)
Re-reading all timelines (after hash)...
Read-only proof: ALL 23 timelines identical before/after.

============================================================
FAILED: 122 error(s), 32 warning(s) across 22 reels.
```

Exit 1 is the verifier refusing on findings. 102 of the 122 are on timelines
this build did not touch and 20 are the pairing bug in §5. Its own read-only
proof, on the same run, is that all 23 timelines are identical before and
after.
