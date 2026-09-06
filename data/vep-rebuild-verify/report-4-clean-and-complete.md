# The first prompt that was clean AND complete: what the selector chose, and whether the reel is good

**Date:** 2026-09-06
**Project:** `Podcast (field test)` / master `GEO Podcast - Synced`
**Built:** `Reel 22 - search-engine-versus-decision-engine (clean and complete)` (25th timeline)
**Base:** `d74a800` (`main`, with #585 merged - `view:spoken_lines`)
**Follows:** [`report.md`](report.md), [`report-2-selector-redraw.md`](report-2-selector-redraw.md), [`report-3-clean-prompt.md`](report-3-clean-prompt.md)

---

## 0. Read this first: the experiment is contaminated and I am the contamination

The answering agent was me, and I had already read reels 03, 20 and 21, their
reasoning, and all three prior reports before composing this one. Report-3 named
the fix and it was not available here: *"A clean answer still needs a reader that
has never seen this material."*

So of the captain's five questions, **question 2 has a factual half I can answer
and a counterfactual half I cannot.** I can show which boundaries this reel is
actually cut on and whether they exist in `turns` - that is arithmetic. I cannot
show that an uncontaminated reader would have reached for them, because I knew
before I started that sub-turn boundaries were the thing under test. **Read §3
as "the capability was used and here is what it bought", never as "the model
discovered it".**

Questions 1, 3, 4 and 5 are about what got BUILT and how it measures. Those are
read off the timeline and the verifier, and contamination does not touch them.

---

## 1. The answer in eight lines

1. **The request was clean AND complete, and the number was predicted exactly.**
   97,768 characters, which is what #585 said it would be to the character. No
   per-word timings, no source paths, no item ids; 929 sub-turn boundaries. §2.
2. **The model chose 267.36-284.07 - and the whole decision is the END.** It cut
   at 284.07 to leave the garbled restatement outside the span, so the build had
   no repeated pair to remove and the payoff line survived intact. §3.
3. **Reel 21's central defect is gone.** Its payoff card read
   `on google search, and / chat gpt is a decision engine.` Reel 22's reads
   `so one which is google is / a search engine, and the other / chat gpt is a
   decision engine.` `refused_take_groups` is empty and no Hangul reached the
   reel. §5.2.
4. **Two of this reel's four boundaries do not exist in `turns`** - 284.07 and
   321.61 - and they are the two carrying the editorial decisions. §3.2.
5. **Zero jump cuts, zero uncaptioned speech, zero edge cuts.** The closer was
   chosen for the SPLICE rather than the words, and the built timeline confirms
   it: one seam, and it is a speaker change. Reel 21 has two seams and both are
   jumps. §4.
6. **The one-frame "for" card came back, and I predicted it before the build.**
   It is not a regression and it never was: the captain's APPROVED Reel 03
   carries two shorter ones, 0.127s and 0.271s, measured by this same run. §6.3.
7. **23 errors, and 22 of them are the F2/F14 pairing bug** - and Reel 22 is the
   only reel the caption checks could run on, exactly as Reel 21 was last time.
   That is not Reel 22 being worse; the other 23 are `NO-REFERENCE`. §6.
8. **Is it good? NO** - and §7 separates what the pipeline can fix from what is
   the source material, because that distinction is the whole point of this run.

---

## 2. The request: clean and complete, measured

| section | after #583 (starved) | after #585 (this run) |
|---|---:|---:|
| `timeline_transcript` | 391 | 391 |
| `reel_candidates` | 15,908 | 15,908 |
| `creative_brief` | 3,630 | 3,630 |
| `picture_holes` | 95 | 95 |
| `lead_speaker` / `answering_speaker` | 20 / 27 | 20 / 27 |
| `who_leads_was_inferred` | 173 | 173 |
| `length_guidance_seconds` | 47 | 47 |
| `project_folder` | 87 | 87 |
| `turns` | 51,531 | **3,263** |
| `spoken_lines` | - | **74,127** |
| **total** | **71,909** | **97,768** |

**#585 predicted 97,768 and the live request measured 97,768.** Every section
outside the two that changed is byte-identical.

Asserted on the live request file, not argued: `"words"` absent, `.MXF` absent,
`resolve_item_id` absent, `source_file` absent. 929 rows carrying speaker, start,
end and text; 136 turn rows carrying speaker, start and end.

The brief reached the model by REFERENCE with a section map, and the sections
that decided this reel were read off disk: *"the transcription itself is garbled
so the script reads as nonsense. Skip those passages"* and *"Redrawing costs
something ... Weigh it and say what you decided."*

---

## 3. What the model chose, and why - verbatim

### 3.1 The choice

**Body 267.358-284.070 (16.71s) + borrowed closer 321.610-328.231 (6.62s) =
23.33s.**

`reason`, verbatim:

> "The two of them run a test and report what came back, and it ends on the
> sentence the whole episode rests on. Craig says they have been testing this on
> clients; Akshita says they took the exact query a customer would ask and ran it
> through Google and ChatGPT side by side; Google returned a list from 2023,
> ChatGPT returned three recommendations with the reasons for each; and she names
> the difference - 'Google is a search engine, and the other chat GPT is a
> decision engine'. Question, evidence, name, in two turns and one hand-off. I end
> it at 284.07 and not at 288.06, and that is the whole editorial decision in this
> reel: 284.13-288.06 is the SAME sentence recorded again and the transcript
> renders it as '같이 라고 달 라는 가 on Google search, and chat GPT is a decision
> engine'. Any span that contains both readings hands the build a repeated run,
> the build removes the EARLIER one whole, and what plays is the damaged one - so
> the reel's payoff line arrives as a fragment. Ending on 284.07 leaves the run
> with only one member inside the span, nothing is repeated, nothing is removed,
> and the clean reading is the one the viewer gets. The brief's coherence rule is
> explicit that a passage whose transcription is garbled should be skipped; this
> is the smallest cut that skips it without taking the payoff with it."

`hook`, verbatim - it names its own worst property:

> "Craig, 267.36: 'well this has been fun recently because we've actually been
> testing this on some of our clients'. I am naming it and I am not defending it:
> it opens on 'well', which the brief calls throat-clearing by name. I checked
> whether a better start exists at a line boundary and it does not. The line I
> want to open on is Akshita's 276.03 'Google search, it gave out a list from
> 2023, and from chat GPT, It gave three recommendations with specific' - a
> concrete claim with no run-up - but starting there drops the sentence that says
> what was asked (271.82, 'we took the exact query their customers would ask, and
> we ran it on Google search and chat GPT side by side'), and without it the
> numbers have no antecedent: a stranger does not know what the list is a list OF.
> Starting at 271.82 keeps the setup and loses Craig, and a body with one speaker
> is not this format. Craig's setup exists in two takes in this passage, 248.83
> and 267.36, and BOTH of them open on 'well'. So the weak opening here is the
> recording, not the boundary."

`cta.note`, verbatim - the closer was chosen for the splice, not the words:

> "Craig, 'we're calling the lucy visibility system we'd love for you to go check
> it out see how ai sees you / the links in the bio'. I chose it over the closer I
> would rather have used, and the reason is the splice rather than the words. The
> body ends on Akshita at 284.07; the only atomic closer in this episode that no
> other reel has taken is hers at 337.59-341.27 ... and cutting from Akshita to
> Akshita puts a jump on the same framing at the one seam this reel has. Craig's
> closer makes that seam a speaker change, which is the same hand-off the episode
> itself does at 312.75. ... The cost is real and I am recording it rather than
> hiding it - five of the twenty-one reels already close on these words, so this
> is the sixth, and if the captain wants closer variety across the set the swap is
> Akshita 337.59-341.27 at the price of that jump cut."

On the span Reel 21 chose, from `considered`:

> "267.36-299.41 (32.1s) is the same exchange plus Craig's pickup ... and it is
> the better ARGUMENT and the worse REEL. It is better because Craig generalises
> the name Akshita just coined, which turns a finding into a consequence and gives
> the reel a third beat. It is worse because it cannot be reached without crossing
> 284.13-288.06: the body is one window, the garbled restatement sits between the
> payoff and Craig's pickup, and `repetition_inside` says a run whose members both
> fall inside the span is removed WHOLE - so the reading that plays is the damaged
> one. I am trading Craig's consequence for an intact payoff line."

### 3.2 Does it use the sub-turn boundaries? The factual half

The four boundaries in this reel, against the `turns` table it would have had
before #585:

| boundary | in `turns`? | what it does |
|---|---|---|
| body start 267.358 | **yes** - Craig's turn starts here | nothing a turn boundary could not do |
| body end **284.070** | **NO** - Akshita's turn is 271.82-288.06 | leaves the garbled restatement outside the span, so nothing is removed |
| cta start **321.610** | **NO** - Craig's turn is 312.75-328.23 | takes the atomic closer out of the middle of a turn |
| cta end 328.231 | **yes** - Craig's turn ends here | |

**Two of four are sub-turn, and they are the two that carry the decisions.** The
turn-only alternatives are 288.06 for the body end - which is the span Reel 21
took, and which deletes the clean payoff - and 312.75 for the closer, which is
Craig's whole 15.5-second thesis-plus-CTA rather than the 6.6-second invitation.

**This is not evidence the capability would be discovered by a clean reader.**
See §0. It is evidence that when it is used, this is what it buys.

The starved run said, unprompted, *"every boundary I can name is a TURN boundary"*
and its `undetermined` already knew the shape of the problem: *"it is the reading
the build KEEPS - it removes the clean one at 281.07-284.07 ... If it is the
former this passage yields no reel."* It could see the defect and could not
reach the boundary that avoided it.

---

## 4. The build, the guard, and the 25 timelines

```
$ manage_project.py run <project> --step select_reels --rerun select_reels \
      --full-auto agy --llm-timeout 3600
LLM_REQUEST_READY: .../pipeline_output/llm_requests/select_reels.json
  Waiting for AGY response for select_reels (timeout 3600s)...
     ✓ Completed in 354.0s

$ manage_project.py build-reels <project> --only-reel 22 \
      --name-suffix " (clean and complete)"
Deleting nothing: none of ['Reel 22 - search-engine-versus-decision-engine (clean and complete)'] exists yet.
Building Reel 22 - search-engine-versus-decision-engine (clean and complete)
  [1/9] ... [9/9]
reel.build: completed
```

**The deletion guard deleted zero, by its own first line.** The plan file was
APPENDED to, never rewritten: the first 21 entries compare equal before and
after, asserted in the append itself.

**Read-only proof, twice and independently:**

- the build's own: `Read-only proof: ALL 25 timelines identical before/after.`
- mine, hashing every track and item of every timeline from a **copy** of
  `Project.db` taken before the run and after it:

```
before: 24  after: 25  new rows: 1
the 24 pre-existing timelines byte-identical: True
new: 176a1b4a94447d00c74f7d86217bc672     16 rows  Reel 22 - search-engine-versus-decision-engine (clean and complete)
```

Resolve was never restarted and no existing timeline was opened for editing.

**The seam, measured on the built timeline.** Keep ranges
`[(267.358, 284.070), (321.610, 328.231)]`, one splice:

```
seam 1: 284.07 (Akshita) -> 321.61 (Craig)   speaker change
seams=1  same-framing jumps=0
```

Against the others, computed the same way:

```
Reel 03  seam 309.92 (Akshita) -> 310.12 (Akshita)   JUMP     1 seam,  1 jump
Reel 20  no seams                                             0 seams, 0 jumps
Reel 21  281.07 (Akshita) -> 284.07 (Akshita)        JUMP
         299.41 (Craig)   -> 461.60 (Craig)          JUMP     2 seams, 2 jumps
Reel 22  284.07 (Akshita) -> 321.61 (Craig)          change   1 seam,  0 jumps
```

The `cta.note` said the closer was chosen for the splice. The timeline agrees.

---

## 5. What the reel actually says

### 5.1 Every card the build placed, in order

```
blk spk       tl_start    frames     dur  text
  0 Craig        0.000    0-29     1.208  well this has been fun recently
  0 Craig        0.000   31-58     1.125  because we've actually been
  0 Craig        0.000   61-84     0.958  testing this on some
  1 Craig        3.522   12-27     0.625  of our clients
  2 Akshita      4.467   12-44     1.333  yeah, we took the exact query
  2 Akshita      4.467   45-75     1.250  their customers would ask, and we
  2 Akshita      4.467   75-92     0.708  ran it on google search and
  2 Akshita      4.467   93-110    0.708  chat gpt side by side.
  3 Akshita      8.572   12-14     0.083  for
  4 Akshita      8.672   12-38     1.083  google search, it
  4 Akshita      8.672   39-69     1.250  gave out a list from 2023,
  4 Akshita      8.672   69-97     1.167  and from chat gpt, it gave
  4 Akshita      8.672   97-121    1.000  three recommendations with specific
  5 Akshita     13.232   12-23     0.458  reasons why.
  6 Akshita     13.715   12-33     0.875  so one which is google is
  6 Akshita     13.715   33-61     1.167  a search engine, and the other
  6 Akshita     13.715   61-84     0.958  chat gpt is a decision engine.
  7 Craig       16.712   12-52     1.667  we're calling the lucy visibility
  7 Craig       16.712   53-83     1.250  system we'd love
  7 Craig       16.712   84-112    1.167  for you to go check it
  7 Craig       16.712  114-147    1.375  out see how ai sees you
  8 Craig       22.472   12-33     0.875  the links in the bio
```

### 5.2 The payoff, against Reel 21

| | Reel 21 (starved clean prompt) | Reel 22 (clean and complete) |
|---|---|---|
| cards 13-17 / 4-6 | `three recommendations with specific` / `reasons why.` / **`on google search, and`** / `chat gpt is a decision engine.` | `three recommendations with specific` / `reasons why.` / **`so one which is google is`** / **`a search engine, and the other`** / `chat gpt is a decision engine.` |
| `refused_take_groups` | empty - the clean take was REMOVED | **empty - nothing was repeated to remove** |
| Hangul in the plan | yes, in the kept reading | **none** |

**Reel 21's viewer gets "…and ChatGPT is a decision engine" with nothing to
contrast it against. Reel 22's gets the whole sentence.** That is the single
result this run was for.

---

## 6. The verifier: 23 errors and 2 warnings, each classified

`FAILED: 137 error(s), 36 warning(s) across 24 reels.` Reel 22 carries 23 errors
and 2 warnings. Every one is accounted for.

### 6.1 Twenty-two of the twenty-three are the F2/F14 pairing bug

9 `F2` + 13 `F14`. The build rendered **9 caption segments carrying 22 cards**.
The verifier pairs each planned CARD to a timeline ITEM, and the timeline carries
one item per SEGMENT - so:

- the first card of each of the 9 segments pairs to that segment's item and
  reports a frame delta: **9 F2**;
- the other 22 − 9 = **13 cards report "planned and never placed": 13 F14**.

9 and 13, segment for segment. This is the defect `report.md` named. Of the 9
F2s, **5 are the pairing bug** (deltas +54, +65, +82, +50, +94 - each the whole
segment's length against one card's) and **4 are a one-frame rounding delta on
single-card segments** (`of our clients`, `for`, `reasons why.`,
`the links in the bio`, all −1 frame). The four are real and none is visible.

**Reel 22 is the only reel these checks could run on.** The other 23 carry
`NO-REFERENCE`: *"provenance records the moment plan but no caption plan for this
reel, so the card grouping cannot be checked"*. Last time that was true of Reel
21 and false of Reel 22; this build moved it. **The caption-plan provenance
retains only the most recently built reel**, so a clean sheet anywhere else in
this table is a check that did not run (AGENTS.md 10.4).

### 6.2 One is real: F17 at the seam, and it is three milliseconds

> `caption card 'chat gpt is a decision engine.' mixes speakers: Akshita, Craig`

Measured: the body ends at reel **16.712s**; Craig's first CTA word
(`we're`, master 321.610) starts at reel **16.712s** - the same instant. The
body's last card runs to 16.715. F17 has no tolerance, so a 3 ms overlap at a
borrowed-closer seam is reported as a card that mixes speakers.

It is not microphone bleed and it is not visible. Compare Reel 21's 8 F17s,
which were real: its borrowed closer sits inside a 12.07-second diarisation
artefact (`461.26-473.34 Akshita: "Yeah."`). **The `not_a_boundary` line #585
added names that row, and this run's closer was chosen clear of it - Reel 22 has
`straddling_within: []` and `edge_cuts: 0` against Reel 21's 2 F8 edge cuts.**

### 6.3 Two warnings, and the short-card one is NOT a regression

> `F7: 2 of 22 caption cards are under 0.5s and HELD, not failed ... card 9 'for'
> 0.100s, card 14 'reasons why.' 0.463s`

**I predicted this card before the build**, in `could_not_determine`: *"Akshita
275.93-276.03 is 0.10 seconds long and its whole text is 'For'. It sits in the
middle of the span I chose and there is no boundary that removes it without
removing the setup sentence or the payoff."* The build produced exactly that.

Report-3 called it "a straight craft regression" on the evidence that reels 03
and 20 had nothing under half a second. **That does not hold.** Every row below
is from THIS run's verifier, one instrument, one pass:

| reel | F7 cards held under 0.5s |
|---|---|
| **Reel 03 (the captain APPROVED this one)** | **`yeah` 0.127s, `so,yeah` 0.271s** |
| Reel 03 (whole-take rebuild) | `yeah` 0.127s, `so,yeah` 0.271s |
| Reel 20 (selector redraw) | none |
| Reel 21 (starved clean prompt) | `for` 0.100s, `reasons why.` 0.463s |
| Reel 22 (clean and complete) | `for` 0.100s, `reasons why.` 0.463s |

The approved reel carries two, and both are SHORTER than mine. 83 of the
episode's 929 bound segments are under 0.5s and 29 are under 0.25s; a segment
becomes its own spine block, and a card cannot outlive its block, so any span
containing a sub-floor segment that survives take removal produces one. Reel 20's
clean sheet is where its span fell, not a mechanism.

> `PQ-LENGTH: plan is 23.3s, under the 45.0s minimum guidance`

Real, and the largest single mark against this reel. §7.

---

## 7. Five-way comparison, and the verdict

### 7.1 The table

| | Reel 03 approved | whole-take rebuild | Reel 20 noisy redraw | Reel 21 starved clean | **Reel 22 clean+complete** |
|---|---:|---:|---:|---:|---:|
| plan | 39.83s | 39.83s | 28.52s | 39.89s | **23.33s** |
| frames plan / actual | 954.9 / **959** | 954.9 / **955** | 683.8 / **684** | 956.3 / **957** | 559.4 / **560** |
| picture items exp/act | 5/4 | 5/5 | 2/2 | 5/5 | **3/3** |
| seams / same-framing jumps | 1 / **1** | 1 / 1 | 0 / **0** | 2 / **2** | 1 / **0** |
| opens on | `yeah, so search didn't change.` | `yeah, so search didn't change.` | **`search didn't change`** | `well this has been fun recently` | `well this has been fun recently` |
| passage | the thesis | the thesis | the thesis | the client test | **the client test** |
| payoff line intact | n/a | n/a | n/a | **NO - fragment** | **YES** |
| caption cards / segments | 29/27 | 29/16 | 20/10 | 28/12 | **22/9** |
| cards held under 0.5s | 2 (0.127, 0.271) | 2 | **0** | 2 (0.100, 0.463) | 2 (0.100, 0.463) |
| uncaptioned speech | 0.00s | 0.00s | 0.00s | 0.00s | **0.00s** |
| edge cuts (F8) | 0 | 0 | 0 | **2** | **0** |
| CTA | its own | its own | its own | borrowed (1st use of that closer) | **borrowed (6th use of that closer)** |
| within the brief's 45-90s | no (39.8) | no (39.8) | no (28.5) | no (39.9) | **no (23.3)** |
| verifier errors | 2 | 1 | 1 | 11 | **23** (22 pairing, 1 seam) |
| caption checks actually ran | no | no | no | no | **yes** |

**On the error counts: do not read that row as quality.** Reel 22's 23 is the
price of being the only reel whose captions could be graded at all. Reels 03, 20
and 21 are 2, 1 and 11 because 22 of their possible findings could not fire.

### 7.2 Is this reel good? **NO.**

It is the best-BUILT of the five and it fails the first rule the format has.

What it gets right, and none of these were true of any earlier cut of this
material: the payoff sentence is whole; nothing is said twice; no take was
removed, so no fragment was stranded; the one splice is a speaker change rather
than a jump; the borrowed closer is clear of the diarisation artefact that put 10
findings on Reel 21; and no speech is uncaptioned.

What is wrong, in the order a viewer would notice it:

1. **It opens on "well this has been fun recently"**, which the brief names as
   throat-clearing. Reel 20 opens on a claim; this opens on a warm-up.
2. **23.3 seconds**, barely half the brief's 45-second preference and the
   shortest of the five.
3. **Craig speaks for 4.10 of the body's 16.71 seconds and says one sentence.**
   That is a 24.5% share against the engine's own 25% concern threshold with one
   alternation against its threshold of three - so by `reel_exchange`'s own
   predicate this body is half a percentage point from being flagged *"a
   monologue with a prompt attached"*. It passes `is_conversation` and it barely
   passes. With the borrowed closer the whole reel is Craig 46% / Akshita 54%,
   which is the honest number for what the viewer watches, but the body is the
   body.
4. **A one-frame card reading "for"** at 8.6s, in the middle of the strongest
   passage.
5. **The sixth reel of twenty-two to close on the same eight seconds of Craig.**
   Chosen deliberately over the only unused closer in the episode, to buy the
   speaker change at the seam. That trade is recorded and it is reversible.

**Is it worse than the noisy redraw (Reel 20)?** On craft alone, **Reel 20 still
has the better opening and no short cards, and it is the one I would put in
front of a viewer.** On what it delivers, Reel 22 is a two-hander that gives a
stranger a concrete finding, where Reel 20 is fifteen seconds of ad read once by
Craig and once by Akshita. They are not the same product and neither is good.

### 7.3 The distinction the captain asked for: pipeline, or source material?

| defect | which | why |
|---|---|---|
| opens on "well" | **SOURCE** | Craig's setup exists in two takes, 248.83 and 267.36, and **both open on "well"**. No boundary avoids it: starting later drops the setup sentence and the numbers lose their antecedent; starting at Akshita's line drops Craig and the body stops being a conversation. |
| 23.3 seconds | **SOURCE** | The passage yields 16.71 seconds of clean speech, bounded by a garbled restatement on one side and a repeated take on the other. Every second past 284.07 costs the payoff. |
| Craig speaks 4.1s | **SOURCE** | He asks once and hands off. There is no second Craig turn inside the passage. |
| the 0.100s "for" card | **PIPELINE, fixable** | A bound transcript segment becomes its own spine block and a caption card cannot outlive its block, so a sub-floor segment guarantees a sub-floor card. 83 of 929 segments are under 0.5s. The captain's approved Reel 03 has two. Fix: merge a sub-floor block into its neighbour, or let a card reach the floor across a block edge. Neither is small. |
| 22 F2/F14 errors | **PIPELINE, fixable** | A planned card is paired to the item that STARTS at its frame rather than the one that CONTAINS it. 9 segments, 22 cards, 9 + 13. |
| 23 of 24 reels `NO-REFERENCE` | **PIPELINE, fixable** | Caption-plan provenance retains only the last build, so the caption checks can only ever grade one reel. A clean sheet on the other 23 reads as a pass and is not one. |
| F17 at the seam | **PIPELINE, fixable** | A 3 ms overlap where a borrowed closer begins at the instant the body ends. F17 needs a tolerance, or the seam needs a frame of separation. |
| the 6th reuse of one closer | **BOTH** | The episode holds seven atomic closers and one of them has never been used; the reuse here was a deliberate trade for the speaker change. The set-level repetition is real and the swap is one field. |

**The three things that would most change the next reel are all source
constraints, and the pipeline cannot help with any of them.** What the pipeline
CAN still fix - the pairing bug, the provenance blindness, the sub-floor block,
the seam tolerance - would not change a single frame of what this reel shows a
viewer. It would only change what the verifier is able to tell the truth about.

---

## 8. What to fix next, in order

1. **Fix the F2/F14 pairing** so the caption checks grade something true. Reel 22
   is the second reel the check has ever run on and it is drowning in 22 false
   errors.
2. **Give every reel a caption plan in provenance**, or stop the newest build
   from evicting the previous one. 23 of 24 reels are unchecked and read as
   passing.
3. **Decide what a spine block shorter than the caption floor should do.** It is
   in the approved reel too, so this is not a new defect and it is not going away
   on its own.
4. **Give the selector a transcription-confidence signal.** This reel's entire
   boundary rests on reading Hangul in an English sentence as a transcription
   failure, and the model said in `could_not_determine` that it could not tell
   that from unintelligible speech. If it guessed wrong, the reel is 15 seconds
   shorter than it needed to be.
5. **A tolerance on F17**, or a frame of separation at a borrowed-closer seam.

## 9. Files

- request: `pipeline_output/llm_requests/select_reels.json` (97,768-char context)
- answer: `pipeline_output/llm_responses/select_reels.json`
- plan: `pipeline_output/review/reel_proposals_v2.json` (moment 22)
- conformance: `pipeline_output/review/conformance_report.json`
- captions: `pipeline_output/steps/4_05_render_subtitles/sub_reel-22-*`
