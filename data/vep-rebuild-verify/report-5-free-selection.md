# The whole episode, no passage pre-chosen: 31 reels, and the first one that is good

**Date:** 2026-09-06
**Project:** `Podcast (field test)` / master `GEO Podcast - Synced`
**Built:** `Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection)` (26th timeline)
**Base:** `08d0069` (`main`, with #583, #585 and #586 merged)
**Follows:** [`report.md`](report.md), [`report-2-selector-redraw.md`](report-2-selector-redraw.md),
[`report-3-clean-prompt.md`](report-3-clean-prompt.md), [`report-4-clean-and-complete.md`](report-4-clean-and-complete.md)
**The model's own ranking, verbatim:** [`report-5-free-selection-model-ranking.md`](report-5-free-selection-model-ranking.md)

---

## 0. Contamination, declared - and this time half of it is gone

Report-3 named the fix and could not use it: *"A clean answer still needs a reader
that has never seen this material."* This run used it.

**The selection is uncontaminated.** The request was copied to an isolated path and
handed to a fresh agent with an explicit prohibition on reading anything else - no
reports, no prior responses, no repository, no project folder. That agent is the
pipeline's own LLM: `--full-auto agy` means an agent answers, and reports 3 and 4
were answered by agents that had already read reels 03 and 20-22. This one had not.

**I am still contaminated**, and my role was different. I read all four reports before
starting. I did not choose a span, rank a candidate, or write a word of the answer. What
I did was run the pipeline, isolate the request, build the model's top pick, and measure
the result - and every measurement below is read off an instrument validated against
numbers a previous lane published (§1.2).

**One contamination vector I introduced, and it showed.** My instruction to the answerer
included the five-point bar this task is judged against. Those five points are already in
the handoff and the brief, so no new requirement was smuggled in - but telling a model
which boxes it will be graded on invites it to claim they are ticked. It did: it answered
*"One idea developing... Each step is new"* to question 2, and §5.3 shows the last 18
seconds restate two things already said. **Read the model's own bar check as a claim, not
a finding.** I re-checked all five off the built timeline and I disagree with it on one.

---

## 1. The answer in nine lines

1. **The pipeline was never the constraint, and that is measurable.** The candidate
   table in the request spans **0.18s to 2656.55s - 37 candidates, the whole
   episode** - and `spoken_lines` carries all 929 lines. Every run today had this. §2.
2. **The request was byte-identical to report-4's**, section for section, so the only
   variable changed is who read it. §2.1.
3. **31 moments, and the pipeline dropped ZERO of them.** No empty span, no picture
   hole, no monologue, no closer inside its own body. §3.1.
4. **No boundary was relocated.** The model named 62 body edges and 26 closer edges and
   `snap_to_speech` moved none of them by more than 5 ms. §3.2.
5. **The uncontaminated reader ranked the span reels 21 and 22 came from LAST of 31**,
   and said why: *"Carries the single best coinage in the episode and almost nothing
   else. 17-second body, and the hook is a warm-up. Bottom of the list honestly."* §3.3.
6. **Its top pick is a passage no run today has touched**: 127.86-192.39, 64.5s, closing
   on its own spoken CTA. §4.
7. **The build is the cleanest of the six.** 4 picture items alternating Akshita ->
   Craig -> Akshita -> Craig, **zero jump cuts, zero edge cuts, zero picture holes,
   1548 frames planned and 1548 placed**, and the first reel today inside the 45-90s
   band. §5.
8. **59 verifier errors, and NONE of them is an independent defect of this reel**: 45
   are the F2/F14 pairing bug at exactly 23+22 for 23 segments and 45 cards, and 14 are
   one diarisation row counted once per card. §6. **Reel 22 fell from 23 errors to 2
   with its timeline proven byte-identical** - the same instrument, moving. §6.4.
9. **Is it good? YES, with one real defect** - and that is the first yes in five builds.
   §7 says what the defect is and §8 attributes every one to footage, brief or pipeline.

---

## 2. What the selector was actually shown

**No passage was pre-chosen, and none ever had been by the pipeline.** The pinning in
reports 1-4 was an instruction given to the answering agent out of band, not a property
of the step. The step's handoff already says *"Propose every stretch that meets the bar
above, not the best few of them"*, and the brief already asks for 25-30.

| context section | chars | what it covers |
|---|---:|---|
| `reel_candidates` | 15,908 | **37 candidates, 0.18s -> 2656.55s** - the whole episode |
| `spoken_lines` | 74,127 | **929 rows**, every line of speech with its own seconds |
| `turns` | 3,263 | 136 speaker turns |
| `creative_brief` | 3,630 | by reference, with a section map |
| `picture_holes` | 95 | 2 holes, both after 2469s |
| the rest | 745 | speakers, length guidance, project folder |
| **total** | **97,768** | |

### 2.1 Single variable: the same request, a different reader

Both requests split at column 0 and compared section by section:

| section | report-4's run | this run | |
|---|---:|---:|---|
| `reel_candidates` | 15,908 | 15,908 | **identical** |
| `spoken_lines` | 74,127 | 74,127 | **identical** |
| `turns` | 3,263 | 3,263 | **identical** |
| `creative_brief` | 3,630 | 3,630 | **identical** |
| `picture_holes` | 95 | 95 | **identical** |
| `length_guidance_seconds` | 47 | 47 | **identical** |
| `lead_speaker` / `answering_speaker` / `who_leads_was_inferred` | 20 / 27 / 173 | 20 / 27 / 173 | **identical** |
| `project_folder` | 87 | 87 | **identical** |
| **total** | **97,768** | **97,768** | |

The prompt is byte-identical too. Only the timestamp and the key ORDER of two sections
differ. **Nothing about the request changed. The reader changed.**

### 2.2 How I know my numbers are about the reel and not the tool

Every instrument was validated against numbers a previous lane published, before it was
used - and two of mine were wrong first, in ways that looked like findings:

| instrument | validation | its first answer |
|---|---|---|
| timeline content hash | reproduces **all 25 published item counts** (334, 34, 55, 35, 44, 82, 36, 29, 46, 34, 51, 52, 31, 47, 62, 43, 55, 76, 58, 61, 31, 15, 24, 16, 30) | `d41d8cd9…` on every row - **a hash of nothing.** Tracks attach through `Sm2SequenceContainer`, not `Sm2Sequence` |
| seams / jump cuts | reproduces reports 2-4 exactly: Reel 03 **1/1**, Reel 20 **0/0**, Reel 21 **2/2**, Reel 22 **1/0** | said Reel 21 had 1 jump, not 2 - a first-match speaker lookup answered "Akshita" for a Craig turn, because of the 12.07s artefact report-3 documented. Fixed to the SHORTEST containing row |
| caption cards | reproduces report-4's Reel 22 card table **byte-for-byte**, both sub-0.5s cards | correct first time |
| track reader | 5 tracks, V1/V2/V3 in container order | reported **one** track - I iterated a sqlite cursor while executing on it, which silently truncates the outer loop |
| conformance verifier | baseline **137 errors / 36 warnings across 24 reels**, per-reel identical to report-4 | correct first time |

**Two of my four home-made instruments produced a plausible number that was purely the
tool.** Neither reached this report as a finding.

---

## 3. What the model chose

### 3.1 Thirty-one moments, and the pipeline dropped none

```
moments kept: 31   dropped: 0
```

`post_bridge` checks that every span is inside the timeline, is not empty, has measured
speech in it, does not overlap a picture hole, is a conversation, and does not name a
closer inside its own body. **All 31 passed all six.** For comparison, the batch of 19
this step produced before needed the same checks and this is the first run where nothing
was refused.

Durations 16.7-83.5s, mean 54.1s, **20 of 31 inside the 45-90s band**. Five reels close
on a CTA they already contain; the other 26 draw on **nine distinct closers, none used
more than three times.** The prior batch of nineteen put seven on one 4.4-second
sentence and drew all fifteen borrowed closers from four passages. Nothing asked the
model to spread them; the handoff reported the old batch's concentration as a fact and
it went looking.

### 3.2 The boundaries were already real

`snap_to_speech` moves a named boundary out to the nearest segment edge. Across 31
moments - **62 body edges and 26 closer edges** - the largest move was **5 milliseconds**,
which is the model quoting a table printed to two decimals. **Not one boundary was
relocated.** Every edge it named was already a cut the material supports.

### 3.3 It ranked today's best-built reel last, cold

Moment 31 of 31 is **267.36-284.07 with the closer at 321.61-328.23** - exactly Reel 22,
the reel report-4 called "the best-BUILT of the five". A reader who had never seen it,
never been told it existed, and never been told which passage anyone cared about, put it
at the bottom of 31 and wrote:

> "Carries the single best coinage in the episode and almost nothing else. 17-second
> body, and the hook is a warm-up. Bottom of the list honestly."

That is independent confirmation of report-4's own verdict on Reel 22 (*"Is it good?
NO"*), reached from the other direction by someone with no stake in it.

---

## 4. The top pick, and the model's reasoning verbatim

**`keyword-stuffing-is-hurting-your-ai-visibility`, 127.855-192.391, 64.54 seconds, no
borrowed closer.**

`hook`:

> "'So we ran an audit last week on a client where we saw an SEO team stuff all the H1
> tags with keywords.' - a dated, concrete finding about a real client in the first four
> seconds, with a tactic the audience recognises as their own."

`reason`:

> "A named audit finding turns into a general law and then into the invitation, inside
> one window with no borrowed parts: the SEO team stuffed the H1 tags, Google loved it,
> AI read it as thin content and dropped them, Craig names the tactic out loud, Akshita
> concedes the two can be reconciled if you know how, and Craig closes on the offer to go
> and check. Nothing outside the reel is needed to understand any step of it."

`close`:

> "Craig's own spoken CTA at 179.83-192.39: 'so if you want to see how you're ranking how
> ai sees you go to our website the links in the bio.' It is inside the window, so the
> reel needs no borrowed closer."

On why it did not open on Craig's question 127 seconds earlier, from the ranking:

> "I deliberately did not start at Craig's question 127.31 seconds earlier - 'give me an
> example of that difference' is a better hook shape but 'that difference' points at
> something the viewer has not heard, and coherence outranks shape."

On its second pick, and why it lost:

> "Three things separate it from the winner. It has to **borrow** its ending... Craig's
> turn contains 'we talked about earlier the keyword stuffing and whatnot' - a
> **back-reference** out of the reel... And its middle is a single 45-second explanation
> with **no second beat** in it; #1 turns over four times."

**I built #1 and not #2.** The task says to build both only if the top pick is clearly
weak on its own stated criteria and the second clearly stronger. It is not: #1 passes
four of its own five criteria and #2 fails self-containment, carries a back-reference,
and borrows its closer - which is the one bar item #1 passes outright. Building #2 would
not have changed the verdict, and substituting my ranking for the model's silently is
what this run exists to avoid.

---

## 5. The build

### 5.1 The command, the guard, and the 26 timelines

```
$ manage_project.py run <project> --step select_reels --rerun select_reels \
      --full-auto agy --llm-timeout 7200
LLM_REQUEST_READY: .../pipeline_output/llm_requests/select_reels.json
  Waiting for AGY response for select_reels (timeout 7200s)...
     ✓ Completed in 2870.5s

$ manage_project.py build-reels <project> --only-reel 23 \
      --name-suffix " (free selection)"
Deleting nothing: none of ['Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection)'] exists yet.
Building Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection)
reel.build: completed
```

**The deletion guard deleted zero, by its own first line.** All 23 moments in the plan
are approved, so `--only-reel 23` was load-bearing: without it the build's delete set
would have been every approved reel.

The plan file was **appended to, never rewritten**. `reel_proposal.write_from_step_output`
refuses here and is right to - the file carries 22 moments the captain has ruled on and
`--force` would discard their answers - so the new moment was appended and the first 22
asserted byte-identical, before and after, on re-read. Its md5 was unchanged by the
selector run itself (`b9ece9ae…` before and after).

**Read-only proof, twice and independently:**

- the verifier's own, on the same run: `Read-only proof: ALL 26 timelines identical before/after.`
- mine, hashing every track and item of every timeline from **copies** of `Project.db`
  taken before the build and after it - §9.

### 5.2 What the timeline actually plays

Read off a copy of `Project.db`, picture tracks only, in play order:

```
   0:    0.000s + 22.105s  V1   LC4931.MXF      (Akshita)
   1:   22.105s + 11.303s  V2   LCATL0012.MXF   (Craig)
   2:   33.408s + 18.352s  V1   LC4931.MXF      (Akshita)
   3:   51.760s + 12.804s  V2   LCATL0012.MXF   (Craig)
   picture items=4  same-file adjacencies=0
   picture runs 0.000s -> 64.564s
```

**Four shots, strictly alternating, no same-file adjacency anywhere, and the picture is
continuous from 0 to 64.564s with no gap between any two items.** Jump cuts are measured
here on the media file the timeline points at, not on the transcript's speaker labels, so
no diarisation row can change the answer.

The plan says one continuous keep range - `[(127.855, 192.391)]`, `refused_take_groups: []`
- so there is **no seam at all**: nothing was removed from the middle and no closer was
grafted on the end. Reels 03, 21 and 22 each had a splice; this one does not.

### 5.3 What it says, and the one thing wrong with it

Every card the build placed (45 cards across 23 segments), condensed to its speech:

```
  0.00-15.29  Akshita  the audit: SEO team stuffed all the H1 tags with keywords.
                       Great for Google. But AI flagged it as thin content, and
                       they stopped showing up on AI at all.
 15.59-21.64  Akshita  "the thing that you were doing to help your Google rankings
                       is actively hurting your AI recommendations on the spot."
 22.54-32.86  Craig    "late 90s early 2000s in marketing they call it keyword
                       stuffing... but as you said they are working against each
                       other now"
 33.88-38.56  Akshita  "It can work against each other if you don't know exactly
                       how they can work together."
 38.58-44.62  Akshita  "So something that you do for SEO can actively hurt your AI
                       rankings as well."                        <-- restates 15.59
 45.00-51.61  Akshita  "But that's where the knowledge comes in. If you know how to
                       work them together..."                    <-- restates 33.88
 51.98-64.54  Craig    "...so if you want to see how you're ranking how ai sees you
                       go to our website the links in the bio"
```

**The first 33 seconds are one idea developing, cleanly. The 18 seconds before the CTA
say two things that have already been said.** The reel's central claim lands at 15.6s
and again at 39.9s; the qualification lands at 33.9s and again at 46.7s.

This is the captain's reel-03 complaint in a much milder form - two claims landing twice
in 64 seconds, against *"the sentence lands four times in forty seconds"* - and unlike
reel 03 **it produces no visible stutter**, because Akshita is on one continuous 18.4
second shot throughout and nothing is cut. The viewer hears her make the same point
twice in different words. They do not see a jump.

**The model found this stretch and under-described it.** Its own "what is wrong" section
names `161.73-179.46` - reel-time 33.88-51.61, exactly these 17.7 seconds - and says it
*"softens the reel's own claim"*, adding that the sharpest version ends at 160.71 and
runs 34.8 seconds. It named the right seconds and called restatement softening. This is
the bar-check contamination of §0 showing: it had been told it would be graded on "one
idea developing" and answered that each step was new.

---

## 6. The verifier: 59 errors and 1 warning, every one accounted for

`FAILED: 175 error(s), 37 warning(s) across 25 reels.` Reel 23 carries 59 errors and 1
warning.

| class | count | what it is |
|---|---:|---|
| `F2` | 23 | caption card frame delta |
| `F14` | 22 | caption card "planned and never placed" |
| `F17` | 13 | caption card "mixes speakers" |
| `F5` | 1 | uncaptioned speech from straddling segments |
| `F7` | 1 (warn) | 3 of 45 cards under 0.5s, HELD |

### 6.1 Forty-five are the F2/F14 pairing bug, to the card

The build rendered **23 caption segments carrying 45 cards**. The verifier pairs each
planned CARD to a timeline ITEM and the timeline carries one item per SEGMENT, so:

- the first card of each of the 23 segments pairs and reports a delta: **23 F2**;
- the other 45 − 23 = **22 report "planned and never placed": 22 F14**.

**23 and 22, exactly.** Of the 23 F2, **12 carry the pairing bug's signature** - deltas
of +18, +35, +36, +64, +68, +76, +78, +79, +86, +86, +90, +96 frames, each the whole
segment's length measured against one card's - and **11 are a −1 frame rounding delta**
on single-card segments. The eleven are real and none is visible; report-4 found four of
the same kind on Reel 22.

### 6.2 Thirteen F17 and the one F5 are a single transcript row

There is exactly one straddling row inside this reel:

```
  Craig  160.75-179.79 (19.04s)  words=1  text='well'
         word 'well'  timeline_start=None  timeline_end=None
```

One word, spanning nineteen seconds, **with no word timings at all**. In reel time that
is **32.895s - 51.935s**. The 13 F17 cards span **34.378s - 52.118s**. Every one of them
overlaps that row, which is why each is reported as "mixes speakers: Akshita, Craig".

The F5 is the same row. Because its single word has no timings, the check falls back to
the row's whole 19-second envelope and measures how much of that envelope has no card
over it: **2.4 seconds**. That is not 2.4 seconds of speech. It is the gaps between
Akshita's caption cards during a window in which the transcript records that Craig said
one word, at an unknown moment. `uncaptioned_pct` is **0.0**, and every row the reel
plays that is bound to a clip is fully captioned.

The verifier's own docstring for this check says it: *"This measures the INSTRUMENT, not
the picture."* Report-3 catalogued the class - 11 segments carrying 88 characters total,
"well", "about", "Yeah.", "audits", WhisperX bridging silent gaps across cuts.

**I cannot rule out that there is a real murmur under Akshita there. I have not heard the
audio.** The model said the same thing about the same row, unprompted, and it is the only
claim in this report I would want a listen to settle.

### 6.3 The one real finding: three short caption cards

> `F7: 3 of 45 caption cards are under 0.5s and HELD, not failed ... card 7 'them.'
> 0.181s, card 32 'comes in.' 0.422s, card 37 "that's" 0.140s`

Real, confirmed by my own independent read of the props (0.167s / 0.417s / 0.125s - the
small differences are frame-boundary convention, not disagreement about which cards).
This is the known mechanism: a bound transcript segment becomes its own spine block, a
card cannot outlive its block, and 83 of the episode's 929 segments are under 0.5s.

As a rate it is the **lowest of the three reels the check has run on**: 3 of 45 (6.7%),
against Reel 03-approved's 2 of 29 (6.9%) and Reel 22's 2 of 22 (9.1%). As a count it is
the highest. Both are true and the count is what a viewer sees.

### 6.4 Reel 22 fell from 23 errors to 2, and its timeline did not change

This is the sharpest available proof that these counts measure the instrument. Every
reel's error count before and after this build, from the same verifier, same flags:

```
Reel 22 - search-engine-versus-decision-engine (clean and complete)   23e/2w  ->   2e/2w   <-- CHANGED
Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection)  (absent) ->  59e/1w   <-- CHANGED
```

**Every other one of the 24 is unchanged, and Reel 22's timeline is byte-identical
before and after (§9).** Its 21 vanished errors are the caption-plan provenance moving
to Reel 23: the caption checks can only ever grade the most recently built reel. Report-3
watched this happen once and report-4 watched it happen again; this is the third time,
and it is the same 20-odd findings walking from one reel to the next.

**So: do not read 59 against Reel 22's 2, or against reels 03/20/21's 2, 1 and 11.**
Reel 23 is the only reel in the project whose captions were graded at all. The other 24
carry `NO-REFERENCE`, which reads as a pass and is not one.

### 6.5 Run without a transcript, the same build reports 4 errors

The done-check's literal form omits `--transcript`, and the verifier says so rather than
reporting a clean sheet:

```
Transcript: NONE - F5 (caption coverage) and F8 (boundary speech) DID NOT RUN, and the
bad-take cuts are underived, so the plan is the uncut span. Pass --transcript to measure them.
  Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection): FAIL (4 errors, 1 warnings)
 23 Reel 23 ...   64.56  1548.0   1548    0/4     0      -   0/23   0.0  0.0  0.0   0   0   0   0
```

**1548 frames planned, 1548 placed.** With the transcript it is 1547.3 planned against
1548 placed, which is the same number rounded.

---

## 7. Six-way comparison, and the verdict

### 7.1 The table

| | Reel 03 approved | whole-take | Reel 20 redraw | Reel 21 starved | Reel 22 clean+complete | **Reel 23 free selection** |
|---|---:|---:|---:|---:|---:|---:|
| plan | 39.83s | 39.83s | 28.52s | 39.89s | 23.33s | **64.54s** |
| frames plan / actual | 954.9 / 959 | 954.9 / 955 | 683.8 / 684 | 956.3 / 957 | 559.4 / 560 | **1547.3 / 1548** |
| picture items exp/act | 5/4 | 5/5 | 2/2 | 5/5 | 3/3 | **4/4** |
| seams / same-framing jumps | 1 / **1** | 1 / 1 | 0 / 0 | 2 / **2** | 1 / 0 | **0 / 0** |
| picture holes / 1-frame holes | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | **0 / 0** |
| opens on | `yeah, so search didn't change.` | same | `search didn't change` | `well this has been fun recently` | `well this has been fun recently` | **`so we ran an audit last week on a client…`** |
| passage | the thesis | the thesis | the thesis | the client test | the client test | **the H1-tag audit** |
| lowest speaker share | 41.6% | - | 43.1% | 36.0% | 46.4% | **37.9%** |
| turns / alternations | 3 / 2 | - | 2 / 1 | 3 / 2 | 3 / 2 | **4 / 3** |
| caption cards / segments | 29/27 | 29/16 | 20/10 | 28/12 | 22/9 | **45/23** |
| cards under 0.5s | 2 | 2 | **0** | 2 | 2 | **3** (6.7%, lowest rate) |
| bound speech uncaptioned | 0.00s | 0.00s | 0.00s | 0.00s | 0.00s | **0.00s** |
| edge cuts (F8) | 0 | 0 | 0 | **2** | 0 | **0** |
| bad-take cuts | 1 | 1 | 0 | 1 | 0 | **0** |
| CTA | its own | its own | its own | borrowed | borrowed (6th use) | **its own, in-window** |
| within the brief's 45-90s | no (39.8) | no (39.8) | no (28.5) | no (39.9) | no (23.3) | **YES (64.5)** |
| verifier errors | 2 | 1 | 1 | 11 | 23 -> **2** | **59** |
| caption checks actually ran | no | no | no | no | **not any more** | **yes, only here** |

### 7.2 Is this reel good? **YES - with one defect, and it is in the tail.**

This is the first yes in six builds, and it is not a yes by comparison. Against the bar,
checked off the built timeline rather than off the model's claim:

| the bar | verdict | evidence |
|---|---|---|
| opens on something that is not throat-clearing | **YES** | `so we ran an audit last week on a client where we saw an seo team stuff all the h1 tags with keywords.` A dated, specific finding; substance arrives in word three. It begins with "so", and that is the whole of the objection available. Reels 21 and 22 spent 4.3 seconds on "well this has been fun recently" |
| one idea developing, not one idea repeated | **PARTIAL - the one real failure** | 0-33s develops cleanly. 33.9-51.6s restates the claim from 15.6s and the qualification from 33.9s. Nothing is cut and nothing stutters, but the viewer hears the same point twice. §5.3 |
| a stranger can follow it | **YES** | Nothing outside the window is needed. "H1 tags", "thin content" and "keyword stuffing" are each explained by their use. The single inward reference, Craig's "as you said", points 10 seconds back inside the reel |
| lands in the brief's length band | **YES** | 64.54s against 45-90. The only reel of the six that does, and the only one absent from the verifier's PQ-LENGTH list |
| its own CTA, not a borrowed one | **YES** | Craig 179.83-192.39, inside the body, contiguous. A complete atomic invitation. Nothing was grafted on the end, so the reel has no closer seam at all |
| no jump cut on the same framing | **YES - zero** | 4 picture items, strictly alternating, measured on the media file |
| no speech cut at an edge | **YES - zero** | `edge_cuts: 0`. Both boundaries are whole-segment edges the snapper did not have to move |
| no uncaptioned speech | **YES for bound speech** | `uncaptioned_pct: 0.0`. The 2.4s F5 is one word with no timings measured on a 19-second envelope (§6.2), and I have not heard the audio |
| no sub-half-second caption cards | **NO - 3 of 45** | `them.` 0.181s, `comes in.` 0.422s, `that's` 0.140s |

**Seven clear passes, one partial, one fail.** The fail is the sub-half-second cards,
which the captain's own approved reel also has two of. The partial is the tail.

**Would I put it in front of the captain? Yes.** Reports 3 and 4 both ended "I would not
ship any of these"; report-4's best answer was that Reel 20 was the one to show on craft
alone, and Reel 20 is fifteen seconds of one ad read twice. This is a 64-second
two-hander that opens on a real client's real mistake, explains a mechanism, and closes
on its own invitation, with no jump cut in it. The tail could be tightened by ending the
body at 160.71 and losing 30 seconds - the model costed that trade and declined it, and
its reasoning is in §4.

### 7.3 The captain's stop condition

The question was *"can this pipeline produce a GOOD reel from this footage at all?"*

**Yes. It could all along, and nothing needed to change to prove it.** The pipeline that
produced Reel 23 is the pipeline that produced Reel 22 four hours earlier, on a
byte-identical request. What changed is that nobody told the reader which forty seconds
to look at.

The model's own global answer, cold, before it knew any of this:

> "**Yes - one.** `keyword-stuffing-is-hurting-your-ai-visibility` is genuinely good, not
> merely the best available. The test I applied is whether it would work posted by an
> account I had never heard of, and it does... It is whole. Its ceiling is that the
> delivery is conversational and unhurried and there is no number in it - it will not
> outperform its category, but it is a real short rather than an excerpt."

---

## 8. Every defect attributed: footage, brief, or pipeline

| defect | which | why |
|---|---|---|
| the tail restates the claim and the qualification (§5.3) | **FOOTAGE** | Akshita says it twice in the recording, not verbatim, so `repetition_inside` does not flag it and the build removes nothing. No boundary keeps both the CTA and the sharp version: ending at 160.71 gives a clean 34.8s reel with no closer of its own. That is a real trade, not a fix |
| 3 caption cards under 0.5s | **PIPELINE, fixable** | A bound segment becomes its own spine block and a card cannot outlive its block. 83 of 929 segments are under 0.5s. The captain's approved Reel 03 carries two. Fix: merge a sub-floor block into its neighbour, or let a card reach the floor across a block edge |
| 45 F2/F14 errors | **PIPELINE, fixable** | A planned card is paired to the item that STARTS at its frame rather than the one that CONTAINS it. 23 segments, 45 cards, 23 + 22. Named in `report.md`; still open |
| 13 F17 + 1 F5 | **PIPELINE, fixable** | One transcript row - Craig, `well`, 19.04s, one word with no word timings - counted once per overlapping card. Two fixes, either sufficient: give a zero-timing row no envelope to be measured on, or report a row whose word count is 1 and whose span is 19 seconds as the bridge it is |
| 24 of 25 reels `NO-REFERENCE` | **PIPELINE, fixable** | Caption-plan provenance retains only the last build. Watched moving for the third time today (§6.4). A clean sheet on the other 24 reads as a pass and is not one |
| "so" as the first word | **FOOTAGE**, and negligible | Both takes of this passage open on a discourse marker. Substance arrives in word three |
| only 20 of 31 proposals in the length band | **FOOTAGE** | The hosts speak in 35-85 second units. The band is reachable - 20 of 31 reach it - but it is not free |
| six ideas across 44 minutes | **FOOTAGE** | The model's own finding: three of its proposals are about source consistency and two about the 20% figure. *"Volume was available; variety was not recorded"* |
| the brief reached the model by reference and one section cost it a reel | **PIPELINE** | Its first `could_not_determine`: the 1,002-byte "Repetition inside a reel" section was not inline, and *"I chose the strict reading throughout, which cost the strongest small-business hook in the episode (1186.94) and shortened three other windows"* |
| cannot tell crosstalk from a diarisation split | **PIPELINE** | Its second `could_not_determine`. It excluded 1186.94-1251.21 on this basis. A per-row overlap flag on `spoken_lines` would settle it |
| the full 31 proposals cannot reach the captain's review file | **PIPELINE** | `write_from_step_output` correctly refuses to overwrite 22 rulings, and the only alternatives are `--force` (discards them) or appending by hand. There is no "append the new proposals as PROPOSED" path, so 30 of 31 proposals live only in the step output |

**Nothing is attributed to the brief.** The model was explicit: *"Not the brief. The brief
is unusually clear about what a reel is, what a CTA is for, and that overlap is judged on
meaning rather than seconds, and I never had to guess at any of it."*

---

## 9. The done-check, run and pasted

```
$ sqlite3 <copy of Project.db> "select count(*) from Sm2Timeline"
26

$ diff timelines.after.md5 timelines.before.md5 && echo TIMELINES UNCHANGED
26d25
< 0135266c5ea6930f7224b8b5566f8646     33 rows  Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection)
(exit 1)

$ diff timelines.after.25.md5 timelines.before.md5 && echo TIMELINES UNCHANGED   # the 25 pre-existing rows
TIMELINES UNCHANGED
```

The only difference is the added line. **All 25 pre-existing rows are byte-equal, hash
and item count alike**, hashed from copies of `Project.db` taken before the build and
after it, never opened in place. The deletion guard reported zero:
`Deleting nothing: none of ['Reel 23 - ... (free selection)'] exists yet.`

```
$ python3 -m library.tools.reel_conformance_verifier --project "Podcast (field test)" \
      --master "GEO Podcast - Synced"
EXIT=1

Transcript: NONE - F5 (caption coverage) and F8 (boundary speech) DID NOT RUN, and the
bad-take cuts are underived, so the plan is the uncut span. Pass --transcript to measure them.
  26 timelines hashed.
  Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection): FAIL (4 errors, 1 warnings)
Read-only proof: ALL 26 timelines identical before/after.

============================================================
FAILED: 100 error(s), 36 warning(s) across 25 reels.
```

The done-check's literal form takes `--project` as the Resolve project NAME, which is
what the tool documents and what `project.yaml` binds; the project FOLDER is passed to
`--plan`/`--transcript`. Run with the transcript so F5 and F8 can fire:

```
$ python3 -m library.tools.reel_conformance_verifier --project "Podcast (field test)" \
      --master "GEO Podcast - Synced" \
      --plan .../reel_proposals_v2.json --transcript .../transcript.json
EXIT=1
  26 timelines hashed.
  Reel 22 - search-engine-versus-decision-engine (clean and complete): FAIL (2 errors, 2 warnings)
  Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection): FAIL (59 errors, 1 warnings)
Read-only proof: ALL 26 timelines identical before/after.

============================================================
FAILED: 175 error(s), 37 warning(s) across 25 reels.
```

Exit 1 is the verifier refusing on findings across the whole set; its own read-only
proof, on the same run, is that all 26 timelines are identical before and after.

---

## 10. What to fix next, in order

1. **Fix the F2/F14 pairing.** Three reels have now been graded and all three drowned in
   it: 28 findings on Reel 21, 22 on Reel 22, 45 here. Pair a planned card to the item
   that CONTAINS it.
2. **Stop the newest build evicting the previous one's caption plan.** 24 of 25 reels are
   unchecked and read as passing. This has now been observed moving three times.
3. **Give a zero-timing transcript row no envelope to be measured on**, or flag it. One
   such row produced 14 of this reel's 59 errors.
4. **Carry the brief's repetition section inline.** The model said in
   `could_not_determine` that reading it by reference cost it the strongest
   small-business hook in the episode and shortened three other windows.
5. **A path for publishing new proposals without discarding the captain's rulings.**
   30 of the 31 moments this run produced cannot reach the review file.
6. **Decide what a spine block shorter than the caption floor should do.** Unchanged from
   report-4, and still the only real craft defect in the built reel.

## 11. Files

- request: `pipeline_output/llm_requests/select_reels.json` (97,768-char context)
- answer: `pipeline_output/llm_responses/select_reels.json` (31 moments, 33 considered, 3 undetermined)
- the model's ranking, verbatim: [`report-5-free-selection-model-ranking.md`](report-5-free-selection-model-ranking.md)
- plan: `pipeline_output/review/reel_proposals_v2.json` (moment 23)
- conformance: `pipeline_output/review/conformance_report.json`
- captions: `pipeline_output/steps/4_05_render_subtitles/sub_reel-23-*`
