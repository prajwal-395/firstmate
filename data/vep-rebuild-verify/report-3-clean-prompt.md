# The selector chooses with a clean prompt: what changed, and what the fix cost

**Date:** 2026-09-06
**Project:** `Podcast (field test)` / master `GEO Podcast - Synced`
**Built:** `Reel 21 - google-gave-a-2023-list-chatgpt-gave-rea` (24th timeline)
**Base:** `20dde19` (`main`, with #583 merged - `context_fields` binds)
**Follows:** [`report.md`](report.md), [`report-2-selector-redraw.md`](report-2-selector-redraw.md)

---

## 0. The answer in seven lines

1. **The prompt really is clean.** 888,835 chars to **71,909**. The two requests
   were diffed section by section: `turns`, `reel_candidates`, `creative_brief`,
   `picture_holes`, `length_guidance_seconds` and both speaker fields are
   **byte-identical**, and the 22,633-char prompt is byte-identical. Exactly one
   thing changed: `timeline_transcript` went 817,317 to 391. §1.
2. **The model chose differently** - 312.75-341.27 became **267.36-299.41 with a
   borrowed CTA** - but I cannot claim the clean prompt caused it. The noisy run
   had already written that the neighbouring exchange was "worth proposing", and
   I had read that before composing. §2 states the contamination and what
   survives it.
3. **What survives it is uncomfortable: the noise was costing less than 92%
   suggests.** Buried in it, the previous run found `repetition_inside`, read the
   candidate table correctly, and reached the right analysis of the passage. §2.3.
4. **And the fix has a real cost the measurement did not predict.** The raw
   transcript was load-bearing for one thing: the previous run timed its closer
   to `Akshita 337.59-341.27`, a **sub-turn** boundary that exists only in
   `segments`. With `segments` gone the model can only cut on turn boundaries -
   it said so in `could_not_determine`, unprompted - and it opened this reel on
   "well this has been fun recently", which the brief names as throat-clearing. §3.
5. **The same loss produced ten real verifier errors.** `turns` is built from
   `bound_segments`, which drops the 11 segments straddling a cut. One of them is
   a 12.07-second Akshita row at 461.26-473.34 sitting across the exact CTA turn
   the model borrowed. It could not see it. The verifier could. §5.2.
6. **The build is clean and the guard deleted zero.** *"Deleting nothing: none of
   ['Reel 21 - ...'] exists yet."* 24 timelines, the other 23 byte-identical,
   proved twice - by the build's own read-only proof and by my hashes off a copy
   of `Project.db`. §6.
7. **Is it good? NO.** It is the best-argued of the four and the worst-built.
   §7 answers the six questions and gives the plain verdict.

---

## 1. What actually changed in the request

Both requests, split at column 0 of the serialised context and diffed:

| section | noisy run (09:05) | clean run (10:22) | |
|---|---:|---:|---|
| `timeline_transcript` | 817,317 | **391** | the whole document, then one sentence |
| `turns` | 51,531 | 51,531 | **byte-identical** |
| `reel_candidates` | 15,908 | 15,908 | **byte-identical** |
| `creative_brief` | 3,630 | 3,630 | **byte-identical** |
| `picture_holes` | 95 | 95 | **byte-identical** |
| `length_guidance_seconds` | 47 | 47 | **byte-identical** |
| `lead_speaker` / `answering_speaker` | 20 / 27 | 20 / 27 | **byte-identical** |
| `who_leads_was_inferred` | 172 | 173 | one trailing newline |
| `project_folder` | 88 | 87 | one trailing newline |
| **total context** | **888,835** | **71,909** | |
| prompt | 22,633 | 22,633 | **byte-identical** |

The two newline differences are the last key in each document taking the trailing
newline; the text is the same. **This is a single-variable manipulation.** The
predicted "after" from the PR was 71,908; the measured one is 71,909.

Per-word timing records reaching the model: **8,509 to 0.**
What `timeline_transcript` now carries, in full:

> Speech transcribed by WhisperX from audio REBUILT out of the source spans the
> timeline plays. Resolve was not opened and nothing was rendered. Times are
> timeline time. Clip binding RE-DERIVED from the timeline's own clip list
> without re-hearing the audio (`timeline_transcript.rebind_document`); every
> word timing is the one the transcribe pass produced.

---

## 2. Did the clean prompt change what the model chose?

### 2.1 The two answers, side by side

| | noisy prompt (888,835) | clean prompt (71,909) |
|---|---|---|
| span | 312.75-341.27 | **267.36-299.41** |
| length | 28.5s | 32.1s body + 10.8s CTA = **42.9s** |
| passage | the thesis sentence | **the client test that produced it** |
| CTA | its own, inside the span | **borrowed**, Craig 461.6-472.43 |
| `considered` entries | 4 | 6 |
| `could_not_determine` | `[]` | **3 entries** |

### 2.2 The reasoning, verbatim, both times

**Noisy prompt, `reason`:**

> "The same thesis this passage has always been about, drawn to start at the
> first place it is said ONCE. Akshita's turn at 300.0-312.04 is the problem:
> inside those twelve seconds she records the sentence twice, and
> `repetition_inside` says a build removes a repeated run whole or not at all -
> so any span opening inside her turn plays it twice however it is cut. Starting
> at Craig's turn instead drops both of her takes and leaves a real exchange: he
> states the thesis and says what it decides, she agrees and closes on the
> invitation. Craig 15.5s, Akshita 12.7s."

**Clean prompt, `reason`:**

> "It is the only stretch in this passage where the two of them actually do
> something to each other. Craig says they ran a test, Akshita reports what it
> returned - Google answered with a list from 2023, ChatGPT answered with three
> recommendations and the reasons for each - and names the difference: one is a
> search engine, the other is a decision engine. Craig then takes the name off
> her and generalises it: 'decision engine, I like that name... more and more
> people, as you stated, they are no longer searching, they're asking.' Question,
> finding, name, consequence, in three turns and two hand-offs. Craig 12.8s to
> Akshita 16.2s is a conversation rather than a monologue with a prompt attached,
> and nothing inside it is said twice at a level a viewer notices."

**Clean prompt, on the span the noisy run chose:**

> "312.75-341.27 (28.5s) is the span this step chose last time and I am not
> choosing it. Craig at 312.75-328.23 delivers a complete reel on his own -
> 'search didn't change, the questions change', what it decides, the product, and
> 'we'd love for you to go check it out, the link's in the bio' - and Akshita at
> 328.61-341.27 then delivers THE SAME SCRIPT: same claim, same product, same
> invitation, in her own words. It is not a conversation, it is one fifteen-second
> ad read twice by two people. The brief's rule is that both speakers say
> something that MATTERS to the reel; her turn adds no fact, no objection and no
> development."

**Clean prompt, on why no cut of the thesis works:**

> "The thesis passage as a whole: there is no boundary in 300.0-341.27 that
> produces a two-hander which does not say 'search didn't change, the question
> changed' at least twice. Akshita's 300.0-312.04 says it four times; Craig's
> 312.75-328.23 says it once; Akshita's 328.61-341.27 says it once. Any two of
> those is two sayings; all three is six. That is a property of what was recorded,
> not of where the line is drawn."

### 2.3 Why I cannot bank this, and what survives

**The experiment is contaminated and the contamination is documented.** The noisy
run's own fourth `considered` entry, which I had read in report-2 before composing:

> "248.83-299.41 (50.6s) is the neighbouring exchange and it is a different reel
> ... Worth proposing on its own merits; it is not this reel and I have not
> proposed it here because this run was asked for one span."

So "the model moved to the neighbouring exchange" is **not** independent evidence
that the clean prompt did anything. Both runs were answered by an agent in this
lineage; report-2 §3.1 declared the same defect from the other side. A clean
answer still needs a reader that has never seen this material.

Three things do survive the contamination, because they are not judgements:

- **The noisy run reasoned correctly despite the noise.** It found
  `repetition_inside`, quoted the right timecodes, weighed the length trade
  explicitly, and named the better passage. Whatever the 92% cost, it did not
  stop the step working. **That is the finding, and it is the deflating one.**
- **The clean run contradicted the prior it was contaminated with.** Report-2
  recommended `248.83-299.41` on the grounds that "both repetitions inside it are
  ones a build removes". Read off the same table, that is **wrong**: the candidate
  carries both takes of Craig's question and both of Akshita's answer, and
  `repetition_inside` only removes 8.1s of the ~19s that doubles, because the two
  takes of the question are not verbatim and nothing paired them. The clean run
  moved the start to 267.36 for exactly that reason and said so. A pure copy of
  the prior would have taken 248.83.
- **The noisy run demonstrably used the raw document.** Its `close` field reads
  *"Akshita 337.59-341.27"*. There is no 337.59 in `turns`; her turn is
  328.61-341.27. That timing exists only in `segments`. §3.

---

## 3. What the projection cost, measured

**The raw transcript was load-bearing for one thing, and the PR that removed it
did not know that.** The PR argued the bridge is the only reader of
`timeline_transcript`. The bridge is the only reader *in code*. The model was
reading it too, for boundaries `turns` cannot express.

The clean run said so itself, unprompted, in `could_not_determine`:

> "Where inside a turn a sentence starts and ends. Every boundary I can name is a
> TURN boundary, because `turns` is the only speech table I was given. ... The
> line that should open this reel is Akshita's 'For Google search, it gave out a
> list from 2023, and from chat GPT, it gave three recommendations with specific
> reasons why' - a concrete claim with no run-up, which is exactly what the hook
> rule asks for. It sits inside her 271.82-288.06 turn. I could only start at
> 267.36, on Craig's 'well this has been fun recently', which the brief calls
> throat-clearing by name. ... I could have estimated the seconds; an estimated
> boundary cuts mid-word, and guessing is not something this step should do."

and it named the fix, which is **not** putting the document back:

> "Segment-level start/end for the speech inside each turn - the `segments` the
> turn table is built from - or a `sentences` column on `turns` carrying one row
> per sentence with its own timecode. Per-WORD timings are not what is needed and
> should stay out."

The three `could_not_determine` entries are recorded in the run summary and in
`pipeline_data.json`. The noisy run returned `[]`.

---

## 4. The 88 characters I was wrong about

The PR judged the `bound_segments` exclusion negligible on this evidence:

> 11 of 940 segments carrying **88 characters in total**: "well", "about",
> "Yeah.", "audits" - WhisperX bridging silent gaps across cuts.

**Character count was the wrong measure.** The right one is span coverage, and
the same script printed it in the same breath: those 11 segments span **254.06
seconds of the master timeline**. One of them is

```
461.26 - 473.34   Akshita: "Yeah."          (12.07s, one word)
```

and the CTA this run borrowed is Craig 461.60-472.43 - **inside it**. `turns`
showed a clean whole Craig turn. The transcript has an Akshita row across the
whole of it, and the verifier reads the transcript. Ten of the reel's errors come
from that one row (§5.2).

This is a diarisation artefact, not real speech - but the step that draws
boundaries could not see it, and the step that grades them could.

---

## 5. The verifier's findings on Reel 21, each classified

`FAILED: 141 error(s), 34 warning(s) across 23 reels.` Reel 21 carries 38 errors
and 2 warnings. Every one is accounted for.

### 5.1 Twenty-eight are the pairing bug, and Reel 21 is the only reel it could fire on

`F2` (12) and `F14` (16) fire on **Reel 21 and no other reel**. That is not Reel
21 being worse - it is the only reel the check could run on. The other 22 carry
`NO-REFERENCE`:

> "provenance records the moment plan but no caption plan for this reel, so the
> card grouping cannot be checked"

Reel 20's single error is that line. **Its clean sheet is a check that did not
run, not a check that passed** (AGENTS.md 10.4). The 28 are the F2/F14 pairing
defect report.md named: a card is paired to the item that STARTS at its frame
rather than the one that CONTAINS it, so on a 12-segment / 28-card reel the first
card of each segment reports a frame delta and the rest report "never placed".
The pattern matches segment-for-segment.

### 5.2 Ten are real, and they are mine

- **8 x `F17` "caption card mixes speakers: Akshita, Craig"** - every card of the
  borrowed CTA. Caused by the 12.07s Akshita "Yeah." row across Craig's turn (§4).
  `F17` also fires on 15 other reels including the captain's approved ones, so the
  class is endemic; these 8 are specifically the CTA I chose blind.
- **2 x `F8` edge cuts** - `START at 461.60s cuts Akshita mid-speech, through the
  word 'Yeah.' (461.26-473.34s, 12.07s long)` and the same at END. Same row.

### 5.3 Two warnings, and one of them is a real defect the gate holds rather than fails

> "2 of 28 caption cards are under 0.5s and HELD, not failed: each is the last
> card of its spine block and ends where that block ends, so no grouping can
> lengthen them"

Measured off the built timeline:

| reel | caption segments | shortest | under 0.5s |
|---|---:|---:|---:|
| Reel 03 (approved) | 27 | 0.500s | 0 |
| Reel 20 (selector redraw) | 10 | 0.626s | 0 |
| **Reel 21 (clean prompt)** | **12** | **0.042s** | **2** |

**A one-frame caption card reading "for".** It exists because the build removed
the 3.0s repeated run mid-turn and split what was left. Reels 03 and 20 have
nothing under half a second. This is a straight craft regression.

---

## 6. The build, the guard, and the 24 timelines

```
$ manage_project.py run <project> --step select_reels --rerun select_reels \
    --full-auto agy --llm-timeout 3600
LLM_REQUEST_READY: .../pipeline_output/llm_requests/select_reels.json
  Waiting for AGY response for select_reels (timeout 3600s)...
     Completed in 725.4s

$ manage_project.py build-reels <project> --only-reel 21
Deleting nothing: none of ['Reel 21 - google-gave-a-2023-list-chatgpt-gave-rea'] exists yet.
Building Reel 21 - google-gave-a-2023-list-chatgpt-gave-rea
  [1/12] ... [12/12]
reel.build: completed
```

**The deletion guard deleted zero**, by its own first line. The 19 approved
moments and the redraw were left in `reel_proposals_v2.json` untouched - the new
moment was appended as #21, approved, and the first 20 entries compare equal
before and after.

**Read-only proof, twice, independently:**

- the build's own: `Read-only proof: ALL 24 timelines identical before/after.`
- mine, hashing the structure of every track and item from a **copy** of
  `Project.db` taken before the run and after it:

```
$ diff timelines.run.after.23.md5 timelines.run.before.md5 && echo "ALL 23 UNCHANGED"
ALL 23 UNCHANGED

$ grep "Reel 21 - " timelines.run.after.md5
38ad65138594e32833f1b0378d559e63     24 rows  Reel 21 - google-gave-a-2023-list-chatgpt-gave-rea
```

23 before, 24 after, the 23 byte-identical. Resolve was never restarted and no
existing timeline was opened for editing.

---

## 7. The six questions, answered on the BUILT timeline

Everything below is read off a copy of `Project.db` or off the rendered caption
props, not off the plan.

### 7.1 What does it open on, at what timecode, and is that the model's hook?

**It opens on Craig, at 0.00s, on "well this has been fun recently", and yes -
that is the model's written hook, which the model itself refused to defend.**

- **Picture.** First V1 item is `LCATL0013.MXF` with `In = 6310`.
  6310 / 23.976 = 263.19s of source, which is master 267.36 - the first frame of
  the span.
- **Caption.** First card on the caption track starts at frame 0:
  `sub_reel-21-..._craig_0_263187-266669_...mov`, card 1 = `well this has been
  fun recently`.
- **Plan.** The model wrote, in `hook`: *"I am naming it as the hook and I am not
  defending it - it opens on 'well' and it is a setup, not a claim."*

Compare: Reel 20 opens on `search didn't change` and Reel 03 on `yeah, so search
didn't change.` **Reel 21 has the weakest opening of the four, and §3 is why.**

### 7.2 Is the repeated speech gone, still there, or partly there?

**Gone from the reel, and it took the payoff line with it.**

`refused_take_groups` is empty. The build removed the 3.0s run at 281.07-284.07 -
Akshita's *"So one which is Google is a search engine, and the other chat GPT is a
decision engine."* - because `repetition_inside` paired it with a later reading.
The later reading is the one the transcript renders with Hangul in it.

Read the cards the build actually placed, 13-17:

```
13  Akshita  three recommendations with specific
14  Akshita  reasons why.
15  Akshita  on google search, and
16  Akshita  chat gpt is a decision engine.
17  Craig    decision engine i like that name
```

**No Hangul reached the reel** - the caption path dropped it, and I cannot tell
from here whether by design or by luck. But card 15 is a fragment: the half of
the contrast that says *Google is a search engine* is the half the build removed.
The viewer gets "…and ChatGPT is a decision engine" with nothing to contrast it
against, and then Craig says "decision engine, I like that name".

**The reel's central idea is half-deleted.** The model predicted this in
`takes_dropped` - *"the build keeps the damaged reading and removes the clean
one"* - and could not prevent it.

### 7.3 Four-way comparison

| | approved `Reel 03` | whole-take rebuild | selector redraw (noisy) | **clean prompt** |
|---|---:|---:|---:|---:|
| plan | 39.83s | 39.83s | 28.52s | **42.89s -> 39.89s** |
| frames (plan / actual) | 954.9 / **959** | 954.9 / **955** | 683.8 / **684** | 956.3 / **957** |
| picture items | 4 | 5 | 2 | **5** |
| cuts | 3 | 4 | 1 | **4** |
| opens on | `yeah, so search didn't change.` | `yeah, so search didn't change.` | `search didn't change` | **`well this has been fun recently`** |
| passage | the thesis | the thesis | the thesis | **the client test** |
| the thesis lands | 4x | 4x | 2x, one per speaker | **0x - different passage** |
| same speaker twice in one shot | yes | yes | no | **no** |
| jump cut on the same framing | - | - | none | **2 (13.72s, 29.07s)** |
| caption segments | 27 | 16 | 10 | **12** |
| shortest caption | 0.500s | - | 0.626s | **0.042s** |
| uncaptioned speech | 0.00s | 0.00s | 0.00s | **0.00s** |
| ends on a CTA | its own | its own | its own | **borrowed** |
| within the brief's 45-90s | no (39.8) | no (39.8) | no (28.5) | **no (39.9)** |
| verifier errors | 2 | 1 | 1 | **38 (28 uncheckable elsewhere, 10 real)** |

### 7.4 How many seconds of speech have no caption over it?

**0.00 seconds**, and `uncaptioned_pct` is 0.0. All four are 0.00s. The caption
track has gaps at 13.64-14.43s and 17.68-20.35s; both fall where the removed take
left silence, which is why the speech-aware measure reads zero.

### 7.5 The verifier's findings, classified

Done in full in §5: 28 of 38 are the F2/F14 pairing bug and Reel 21 is the only
reel that check could run on; 8 are `F17` from the CTA's hidden Akshita row; 2 are
`F8` edge cuts from the same row; 2 warnings, one of which is a genuine one-frame
caption card.

### 7.6 Is this reel good?

**NO.**

It is the best *argued* of the four and the worst *built*. What it gets right: it
is a real conversation - question, finding, name, consequence, over two hand-offs
with Akshita at 16.2s to Craig's 12.8s - and it delivers something a stranger did
not know. Nothing in it is said twice. It is the only one of the four that is
about anything other than a sentence being repeated.

What is wrong, in order of how much a viewer would notice:

1. **The payoff is half-deleted.** "on google search, and chat gpt is a decision
   engine" is a fragment, and it is the line the whole reel builds to (§7.2).
2. **A one-frame caption card reading "for"** (§5.3). Reels 03 and 20 have
   nothing under half a second.
3. **It opens on "well"**, which the brief names as the thing not to open on -
   and the model could not move the start to the line that should have opened it,
   because the boundary it needed is inside a turn (§3).
4. **Two jump cuts on the same framing**, at 13.72s (Akshita to Akshita, take
   removal) and 29.07s (Craig to Craig, the borrowed CTA splice). Reel 20 has none.
5. The borrowed CTA sits across a diarisation artefact (§4). Probably inaudible;
   I have not heard it, and I am not going to claim it is fine.

**Would I ship any of the four? No.** Of the four, Reel 20 remains the one to put
in front of the captain if this material must be used, on craft alone. Reel 21 is
the better editorial answer built badly, and three of its five defects are fixable
without another selection run.

---

## 8. What to fix, in order

1. **Give `turns` sentence-level rows.** One row per transcript segment inside the
   turn, with its own start/end and no `words` array. That restores the only
   capability the projection removed, at a fraction of 817,317 characters. The
   model asked for exactly this.
2. **Stop `turns` hiding 254 seconds of the timeline.** `bound_segments` drops the
   11 straddling segments for a good reason, but dropping them SILENTLY is what
   let the CTA land inside one. Report them beside the table - a `straddling`
   column, or a line saying which spans carry one - the same way `picture_holes`
   is reported.
3. **Fix the F2/F14 pairing** so caption checks grade something true. Reel 21 is
   the first reel the check has ever actually run on, and it is drowning in 28
   false errors.
4. **Give every reel a caption plan in provenance.** 22 of 23 reels are
   `NO-REFERENCE`, which reads as a pass and is not one.
5. **Re-open the take-removal rule where it damages the surviving reading.**
   Removing the clean take and keeping the garbled one is the wrong way round.

## 9. Files

- request (clean): `pipeline_output/llm_requests/select_reels.json`
- answer: `pipeline_output/llm_responses/select_reels.json`
- plan: `pipeline_output/review/reel_proposals_v2.json` (moment 21)
- conformance: `pipeline_output/review/conformance_report.json`
- captions: `pipeline_output/steps/4_05_render_subtitles/sub_reel-21-*`
