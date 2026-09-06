# The three short cards: what they actually were, and the reel rebuilt without them

**Date:** 2026-09-06
**Project:** `Podcast (field test)` / master `GEO Podcast - Synced`
**Built:** three NEW timelines, 26 pre-existing byte-identical
**Base:** `3dfd84d` (`main`, with #588 merged)
**Follows:** [`report-5-free-selection.md`](report-5-free-selection.md)

---

## 0. Contamination, declared

**I did not tell the answering model what it would be graded on.** Report-5 did, and
said so; this run does not, which is the one change to that protocol. The request was
copied to an isolated path and handed to a fresh agent with an explicit prohibition on
reading the repository, the project folder, any prior report or any prior answer. It
was given the pipeline's own prompt, the pipeline's own context, and the expected
schema. Nothing else. So there is no self-assessment to discount this time - and no
"the model says it passes the bar" claim anywhere below. Every verdict in §6 is mine,
read off the built timeline.

**I am contaminated.** I read report-5 before starting. I chose no span, wrote no
reason and ranked nothing. What I did was find the cause of the short cards, fix it,
run the selection, build, and measure.

**Two of my own instruments produced plausible numbers that were purely the tool**, and
neither reached a finding. Both are named in §1.1, because the last lane had the same
experience and the pattern is the point.

---

## 1. The answer in eight lines

1. **The three short cards were not a grouping fault and no grouping could reach them.**
   Each was a WHOLE spine block. §2.
2. **Each was half a sentence.** The reel spine makes one block per transcript row, and
   these three rows are sentences WhisperX wrote down as two - same speaker, same clip,
   an 80ms pause, nothing cut out between them. §2.1.
3. **The fix is in the block, because the block is the ceiling on the card.** A block
   too short to carry a legible card is given back to the block holding the rest of its
   sentence, and **the transcriber's own punctuation says which side that is**. §3.
4. **45 of 984 cards under the floor become 9, across the captain's 23 reels.** 44 of
   the 45 were their block's only card. §4.
5. **Both directions, on real data: 7 reels merge nothing and come back BYTE-IDENTICAL** -
   same card count, same texts, same times. 8 of 10 new tests fail with the pass
   disabled. §4.1.
6. **On the same span, built twice: 3 sub-half-second cards become 1.** Identical
   picture - 4 items, 1548 frames, 0 same-file adjacency, 0 holes. §5.1.
7. **The script-mismatch signal changed the answer, and it is traceable to the word.**
   The reader named the Hangul line and dropped the passage report-5's reader proposed
   and ranked last. §7.
8. **Is the reel good? Yes** - same yes as report-5, with one flashing card instead of
   three, and the same tail. §6.3.

### 1.1 Two instruments that were wrong first

| instrument | what it first said | what it was |
|---|---|---|
| `max_words` sensitivity in step 4.01 | identical output at 2, 6, 7, 999 - "not the binding constraint" | the operation registry loads a step body as its OWN module object (`_op_step_step_4_01_plan_subtitles_step`), so patching `library.steps...step` patched a module nothing calls. Patched on the loaded module it moves 962 cards to 566 at 999. §3.3 |
| timeline picture table | 22.083s where report-5 published 22.105s | 24.0 fps assumed. The sequence carries **23.976024**, read back off `Sm2Sequence.FrameRate`. With it, all four rows and both totals match report-5 to the millisecond |

Every instrument below was validated against a number a previous lane published FIRST:

| instrument | validation |
|---|---|
| timeline hasher | reproduces **all 25 published item counts** (334, 34, 55, 35, 44, 82, 36, 29, 46, 34, 51, 52, 31, 47, 62, 43, 55, 76, 58, 61, 31, 15, 24, 16, 30) as a multiset, and Reel 23's published **33 rows** |
| picture table | reproduces report-5's Reel 23 table exactly: `0.000+22.105 / 22.105+11.303 / 33.408+18.352 / 51.760+12.804`, `items=4 adjacencies=0`, `0.000 -> 64.564s` |
| offline spine+plan harness | reproduces the built card counts of every reel built today: Reel 20 **20/0**, Reel 21 **28/2**, Reel 22 **22/2**, Reel 23 **45/3**, and the 4.6% floor rate `reel_conformance_verifier` publishes |

---

## 2. What the three cards were

`F7` named them: `them.` 0.181s, `comes in.` 0.422s, `that's` 0.140s.

Read off the rendered props of the built Reel 23, each is a whole rendered segment -
`_block_position` 4, 16 and 19, one card each, and the segment's entire length IS the
card:

```
blk spk      tl_start   tl_end    dur    gap cards  text
  3 Akshita     7.761    8.605  0.844  0.060     1  it was definitely going to help
  4 Akshita     8.685    8.866  0.181  0.080     1  them.
 15 Akshita    45.001   46.125  1.124  0.381     1  but that's where the knowledge
 16 Akshita    46.205   46.627  0.422  0.080     1  comes in.
 18 Akshita    50.785   51.605  0.820  0.040     1  strategy for yourself.
 19 Craig      51.975   52.115  0.140  0.370     1  that's
```

### 2.1 Each is half a sentence, and the transcript says so exactly

The rows they came from, with their SOURCE seconds beside their reel seconds:

```
   i spk         tstart      tend    dur    gap clip                srcS      srcE  text
   3 Akshita    135.616   136.460  0.844  0.060 7124f8f5-360     197.970   198.814  It was definitely going to help
   4 Akshita    136.540   136.721  0.181  0.080 7124f8f5-360     198.894   199.075  them.
  16 Akshita    172.856   173.980  1.124  0.381 1cd53d33-8a1     235.168   236.292  But that's where the knowledge
  17 Akshita    174.060   174.482  0.422  0.080 1cd53d33-8a1     236.372   236.794  comes in.
  20 Craig      179.830   179.970  0.140  0.370 93f6b72c-d22     241.892   242.032  that's
  21 Craig      180.151   185.210  5.059  0.181 93f6b72c-d22     242.213   247.272  why i'm super excited about ...
```

**Same speaker, same clip, and the timeline gap equals the source gap in every pair** -
0.080 and 0.080, 0.080 and 0.080, 0.181 and 0.181. Nothing was removed between them.
One sentence, two rows.

### 2.2 Why the grouping cannot reach them, and why the floor could not either

Step 4.01 already does everything it can. `enforce_min_duration` IS called (#564's
finding is fixed), it extends a short card toward `MIN_DISPLAY_DURATION` and cascades -
and then **every entry in the block is clamped to the block's own range**:

```python
entry["timeline_end"] = round(min(entry["timeline_end"], block_end), 3)
```

So for a block of 0.140s carrying one word, the card is extended to 0.840s and clamped
straight back to 0.140s. **The block's length is the ceiling on every card in it, at
every partition.** With one word there is nothing to partition. `manifest_validator`'s
note - *"the fix is in the GROUPING (do not emit a one-word card)"* - is right about
the general case and cannot reach this one: the grouping never chose to emit a one-word
card, the spine handed it a one-word block.

### 2.3 So `F7`'s exemption was saying something false

`ends_with_its_block` holds a card that is the LAST card of its block and ends AT the
block's end, on the ground that *"no grouping and no extension can lengthen one"*. A
one-card block satisfies both clauses **trivially - it is last because it is alone**.

Measured across the 23 built reels: **45 of 984 cards sat under the floor and 44 of
the 45 were the only card of their block.** The exemption was not narrow. It was
swallowing the whole population and stating, in its own warning text, that nothing
could be done about it.

---

## 3. The fix

`library/tools/reel_spine._merge_fragment_blocks`, run after the bleed drop and the
cross-speaker edge cut - last of the three block-shape passes, because a block's final
length is only known once the other two have run.

- **A block under the caption floor is a FRAGMENT, not an utterance.** The floor is
  `manifest_validator.MIN_CAPTION_DISPLAY_SECONDS`, imported rather than restated, so
  the rule that REMOVES a flashing card and the gate that FAILS one cannot drift apart.
- **It may only join a neighbour it plays straight on from**: same speaker, same clip,
  and the reel advancing by exactly as much as the source does - which is to say
  nothing was cut out between them.
- **Which side is the transcriber's own punctuation**, a signal already in the data. If
  the preceding block ends a sentence the fragment opens the next one; otherwise it
  closes the previous one.
- **A fragment whose sentence runs onto a side nothing connects it to is LEFT ALONE and
  NAMED**, in `fragment_blocks_unmerged` and on stderr during the build.

**Nothing is padded, moved or invented.** A merged block spans exactly the union of two
real spans of one continuous take, and every word keeps its own measured timing. What
changes is how many blocks those words are divided into.

### 3.1 The one constant, and why it is not chosen

`CONTIGUITY_TOLERANCE_SECONDS = 1e-6`. Across the 23 reels there are **403 neighbouring
block pairs on one clip with one speaker**:

```
agree(<1e-6)=397   disagree=6
  max |timeline_gap - source_gap| among agreeing: 9.09e-13
  smallest disagreement: 0.100
```

397 agree to within **9.1e-13 seconds** - float noise, the same number computed twice.
The six that disagree do so by **0.100s at the very least**, which is a removed take. A
microsecond sits six orders above the noise and five below the smallest real cut, so no
value inside that range changes an answer. It is mechanical, and it is measured.

### 3.2 Why the punctuation and not "always merge backwards"

Merging always-backwards is simpler and it is wrong in a way that leaves the card short
anyway. `For` (0.100s) follows *"Google search and chat GPT side by side."* and opens
*"For Google search, it gave out a list from 2023"*. Appended backwards it becomes the
last card of the block it joined and stays short; appended forwards the grouping
absorbs it. Across the 50 episode-wide fragments where both neighbours are contiguous,
the punctuation reading puts `them.` and `comes in.` on the sentences they end and
`For` and `Why?` on the sentences they begin.

### 3.3 What I did NOT change, and why

One card remains under the floor on this reel: `help them.` at 0.402s. Its block is now
the real sentence, 1.105s and seven words, and no partition of those seven words gives
two cards both over 0.5s - the word boundaries offer no split point in the 0.105s window
that would allow it. It could be ONE card, and it is not, because `split_into_groups`
caps a card at `max_words = 6`.

Raising that cap to 7 removes this card and two others. It also takes the project from
**962 cards to 839**, and removing it entirely takes it to 566 with an 18-word card in
it. **That is a craft decision about how much text is on screen at once, it is the
captain's, and I did not take it.** The measurement is here so they can:

```
max_words=6    cards=962  under0.5s=9   longest=6w
max_words=7    cards=839  under0.5s=6   longest=7w
max_words=8    cards=759  under0.5s=6   longest=8w
max_words=999  cards=566  under0.5s=6   longest=18w
```

---

## 4. What it does, measured on the captain's own reels

```
                blocks   cards   cards under 0.5s
  before          490     984          45
  after           452     962           9
```

The nine survivors: six are blocks the reel genuinely holds alone - `yeah.` between two
turns of the other speaker, with nothing contiguous beside it - and three are now the
last of SEVERAL cards, which is the case `F7`'s exemption was written for.

### 4.1 Both directions, and the one that matters more

**It does not fire on correct output.** Seven of the 23 reels contain no fragment the
rule can act on. Every one of them comes back with the same number of cards, the same
texts and the same times - hashed:

```
  NO FRAGMENT  Reel 03 - search-didnt-change-the-question-did   identical=True  cards=29
  NO FRAGMENT  Reel 05 - the-audit-that-was-eye-opening         identical=True  cards=53
  NO FRAGMENT  Reel 08 - why-ai-trusts-a-cited-brand            identical=True  cards=41
  NO FRAGMENT  Reel 09 - first-step-is-understanding            identical=True  cards=33
  NO FRAGMENT  Reel 11 - what-hallucinating-actually-means      identical=True  cards=54
  NO FRAGMENT  Reel 13 - a-score-is-not-a-fix                   identical=True  cards=45
  NO FRAGMENT  Reel 20 - search-didnt-change-the-question-did   identical=True  cards=20
```

**It fires.** `tests/test_reel_fragment_blocks.py` is ten tests; with
`_merge_fragment_blocks` disabled, **8 of the 10 fail**. The two that pass in both
worlds are the two that assert nothing changed - which is what they are for.

**It introduces no invariant violation.** Six pre-existing violations across the 23
reels (overlapping words, a block whose two clocks disagree, text not matching its own
words). Run with the pass disabled: **the same six**, same reels, same descriptions,
only the block numbering shifted. None is mine.

### 4.2 And on the conformance verifier, every difference accounted for

Same 29 timelines, verifier run twice, only the pass toggled:

```
  merge OFF: FAILED: 194 error(s), 38 warning(s) across 27 reels.
  merge ON : FAILED: 237 error(s), 26 warning(s) across 27 reels.
```

- **-12 warnings**: twelve reels lose their `F7` held-card warning entirely, because
  they now have no card under the floor at all.
- **-2 errors**: Reels 06 and 07 each lose an `F7` ERROR - a short card that was not
  exempt and is now gone.
- **+45 errors, all on Reel 24**: with the pass OFF the re-derived caption plan no
  longer matches the hash recorded at build time, so `NO-REFERENCE` rises from 25 to
  26 and Reel 24's caption checks REFUSE. With it ON they run. **That the checks only
  run with the fix enabled is itself proof the timeline was built by the fixed code.**

`194 - 2 + 45 = 237`, and `38 - 12 = 26`. Nothing is unexplained.

---

## 5. The rebuild

Three new timelines, none of them overwriting anything. Both builds were driven through
the `reels` PROCESS (`manage_project.py build-reels`, which runs `build_reels` then
`verify_reels` off `library/processes/reels/dag.json` through the operation registry) -
no direct module call.

```
Deleting nothing: none of ['Reel 24 - keyword-stuffing-flagged-as-thin-content (fragment fix)',
                           'Reel 25 - healthcare-company-that-sells-accounting (fragment fix)'] exists yet.
Deleting nothing: none of ['Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (fragment fix)'] exists yet.
```

and, on each:

```
  Reel 24 - ... (fragment fix): rejoined 3 mid-sentence row(s) to their own sentence
  Reel 23 - ... (fragment fix): rejoined 3 mid-sentence row(s) to their own sentence
```

### 5.1 The same span, built twice - the defect measured end to end

`Reel 23 (free selection)` was built at 12:49 from the old code; `Reel 23 (fragment
fix)` at 14:35 from the new, on the identical span. Read off the built timelines and
the rendered caption props:

| | free selection | fragment fix |
|---|---:|---:|
| plan | 64.54s | 64.54s |
| frames plan / actual | 1547.3 / 1548 | 1547.3 / 1548 |
| picture items | 4 / 4 | 4 / 4 |
| same-file adjacencies | 0 | 0 |
| picture holes | 0 | 0 |
| rendered caption segments | 23 | **20** |
| caption cards | 45 | **44** |
| **cards under 0.5s** | **3** | **1** |
| which | `them.` 0.167s, `comes in.` 0.417s, `that's` 0.125s | `help them.` 0.417s |
| edge cuts | 0 | 0 |
| bound speech uncaptioned | 0.00s | 0.00s |

The picture is byte-identical between them - `0.000+22.105 / 22.105+11.303 /
33.408+18.352 / 51.760+12.804`. **The only thing that changed is the captions.**

The old reel's 23 overlay `.mov` files still carry their 12:49 timestamps: the new build
wrote 20 files under different names and rewrote none of them.

### 5.2 The two the new reader chose

| | Reel 24 `keyword-stuffing-flagged-as-thin-content` | Reel 25 `healthcare-company-that-sells-accounting` |
|---|---:|---:|
| span | 125.929-192.391 | 822.954-889.665 + closer 321.61-328.23 |
| built length | 66.483s | 73.365s |
| frames plan / actual | 1593.5 / 1594 | 1758.2 / 1759 |
| picture items / adjacencies | 5 / **0** | 4 / **0** |
| picture holes | 0 | 0 |
| caption cards | 46 | 48 |
| **cards under 0.5s** | **1** (`help them.` 0.402s, held) | **0** |
| edge cuts / bad-take cuts | 0 / 0 | 0 / 0 |
| uncaptioned bound speech | 0.00s | 0.00s |
| CTA | its own, in-window | **borrowed** (321.61-328.23) |
| within 45-90s | YES | YES |

---

## 6. All nine bar items, off the BUILT timeline

On `Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (fragment fix)` - the same span
report-5 judged, so the two are comparable line for line.

| the bar | verdict | evidence, read off the timeline |
|---|---|---|
| opens on something that is not throat-clearing | **YES** | `so we ran an audit last week on a client where we saw an seo team stuff all the h1 tags with keywords.` Substance in word three |
| one idea developing, not one idea repeated | **PARTIAL** | Unchanged, and it is footage. 0-33s develops cleanly; 33.88-51.60 restates the claim from 15.59 and the qualification from 33.88. Nothing is cut, so nothing stutters - the viewer hears the same point twice in different words |
| a stranger can follow it | **YES** | Nothing outside the window is needed. The single inward reference, Craig's "as you said", points 10 seconds back inside the reel |
| lands in the brief's 45-90s band | **YES** | 64.564s, from 1548 frames at the sequence's own 23.976 fps |
| its own CTA, not a borrowed one | **YES** | Craig 51.98-64.54, inside the body, contiguous. One keep range, `refused_take_groups: []`, so the reel has no closer seam at all |
| no jump cut on the same framing | **YES - zero** | 4 picture items, V1/V2/V1/V2, `same-file adjacencies=0`, measured on the media file each item points at |
| no speech cut at an edge | **YES - zero** | `edge_cuts: 0` |
| no uncaptioned speech | **YES for bound speech** | `uncaptioned_pct: 0.0`. The 2.3s `F5` is the same one-word 19-second `well` row report-5 documented, measured on an envelope rather than on speech |
| no sub-half-second caption cards | **BETTER, NOT CLEAN - 1 of 44** | Was 3 of 45. `help them.` 0.402s, and §3.3 says exactly what removing it would cost |

**Eight of nine as before, and the one that changed got two thirds better.**

### 6.1 The other two, briefly

**Reel 24** is the same passage with Craig's question in front of it. It passes six
items outright and **loses one report-5's version held**: it opens on *"give me an
example of that difference"*, and "that difference" points at something the viewer has
not heard. Report-5's reader declined that opening deliberately - *"coherence outranks
shape"* - and I think it was right. Its caption result is the same: 1 of 46.

**Reel 25**, the new reader's own top pick, is the cleanest CAPTIONED reel of the three -
**zero cards under the floor, 48 of them** - and the weakest reel. It borrows its
closer, and its own author wrote down why: *"From 852.86 to 889.66 - thirty-seven
seconds, more than half the body - Akshita is explaining a general principle ... and
never returns to the company that sells accounting software... Those same thirty-seven
seconds are a monologue."*

### 6.2 What a viewer sees, start to finish

Akshita, framed on the left camera, says a client's SEO team stuffed every H1 tag with
keywords - great for Google, and AI read it as thin content and stopped showing them at
all. She names the reversal: the thing you did for your Google ranking is hurting your
AI recommendations. At twenty-two seconds the shot cuts to Craig, who puts a name to it
- keyword stuffing, late nineties, and the two are working against each other now. Back
to Akshita, who refuses the scare: they only work against each other if you do not know
how to work them together. Then Craig again, straight into the invitation - go to the
website, see how AI sees you, links in the bio. Two people, four shots, no jump, no
black, captions under every word, and it ends on the ask rather than running out of
tape.

### 6.3 Is it good?

**Yes.** It is the same reel report-5 said yes to, with one flashing caption instead of
three and everything else identical to the millisecond. It is not clean: half a second
of `help them.` still flickers, and the last eighteen seconds say two things that were
already said. Neither is new and neither is a regression - one is now measured and
priced (§3.3), the other is in the recording.

---

## 7. Did the script signal change what the model chose?

**Yes, and it is traceable to the word.**

The request differs from report-5's by **+943 characters in the context** - two added
lines, `transcription_confidence` and `script_mismatch` - plus a reordering of sections
both requests already carried, and **+189 characters in the prompt**, which is #588's
one-line edit to how the `spoken_lines` table is described. Every other section is
byte-identical, and the expected schema is unchanged.

The signal, verbatim as it reached the prompt:

> `script_mismatch: 1 line(s) carry letters outside LATIN ... A speaker does not change
> writing system mid-sentence, so this is a TRANSCRIPTION failure signature and not a
> speech one: the words are wrong, and nothing here says the audio under them is ...
> 284.13-288.06 Akshita: 8 HANGUL character(s) in: 같이 라고 달 라는 가 on Google search,
> and chat GPT is a decision engine.`

Report-5's reader, without it, **proposed** that passage - `search-engine-versus-decision-engine`,
267.36-284.07 - and ranked it 31st of 31: *"Carries the single best coinage in the
episode and almost nothing else."*

This reader, with it, **refused** it, and said why:

> "The Google-versus-ChatGPT side-by-side test and the 'decision engine' line - one of
> the best formulations in the episode. The only take that reaches the punchline is
> followed by the one line the transcriber rendered in Hangul, and the punchline itself
> is a repeated run paired to that garbled line, so no boundary yields both a clean
> transcript and a complete idea."

The word "Hangul" appears **nowhere** in report-5's answer (0 occurrences) and exists in
this run's context only inside the new signal. The rejection quotes it. For this
passage the link is direct.

**It moved the passage the opposite way to #588's own conclusion.** #588 measured the
audio at 284.13-288.06 as clean and concluded *"the trade was wrong and 267.36-299.41
was the reel"*. The signal says the audio may be fine. The reader read it and excluded
the passage anyway, on the ground that it wants a clean TRANSCRIPT to draw a boundary
in. That is worth the captain's attention: the signal as worded tells a model a line is
untrustworthy, and a model's response to an untrustworthy line is to route around it.

**What the signal did NOT do is change the top pick, and I cannot claim it did.** Two
things changed between the runs - the signal, and a fresh reader. The new top pick
(`healthcare-company-that-sells-accounting`, 822.95-889.66) sits nowhere near the
flagged line, and neither does report-5's. **That difference is reader variance and I
am not attributing it to the signal.**

### 7.1 What the two readers agreed on, cold

They never saw each other's work, and both proposed the same passage with the same
ending second:

```
  report-5's reader:  127.855 - 192.391    keyword-stuffing-is-hurting-your-ai-visibility   (ranked 1 of 31)
  this reader:        125.929 - 192.391    keyword-stuffing-flagged-as-thin-content         (ranked 7 of 29)
```

They disagree by 1.9 seconds at the front, and they disagree ON PURPOSE. Report-5's:
*"'that difference' points at something the viewer has not heard, and coherence outranks
shape."* This one's: *"'give me an example of that difference' - a direct demand for
proof, which is the line a sceptical viewer is already thinking."* Two editors, one
passage, one real argument about where it starts.

### 7.2 What the new reader stopped asking for

Report-5's reader listed, in `could_not_determine`: *"A per-row confidence or an overlap
flag on `spoken_lines`"*. This reader does not ask for it. Its four
`could_not_determine` entries are all about the creative brief reaching it by
reference and about `not_a_boundary` - which is the same complaint report-5 raised and
which #588 did not address.

---

## 8. The done-check, run and pasted

```
$ sqlite3 <copy of Project.db> "select count(*) from Sm2Timeline"
29

$ diff timelines.final.26.md5 timelines.before.md5 && echo TIMELINES UNCHANGED
TIMELINES UNCHANGED
```

All 26 pre-existing timelines are byte-equal, hash and item count alike, hashed from
COPIES of `Project.db` taken before the first build and after the last, never opened in
place. The three added rows:

```
5f36cc002f9f67ec8e1e30131e8313e0     30 items  Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (fragment fix)
ed68d1f3e4a77c9f7b7e2bf42d5bdf70     33 items  Reel 24 - keyword-stuffing-flagged-as-thin-content (fragment fix)
3278f083a42370375b8faf4813389545     30 items  Reel 25 - healthcare-company-that-sells-accounting (fragment fix)
```

Reels 24 and 25 are also byte-identical between the second build and the third, so the
third build touched only its own timeline.

**The deletion guard reported zero on both builds**, by its own first line (§5).

```
$ python3 -m pytest tests/test_reel_conformance_verifier.py \
      tests/test_caption_min_duration_is_called.py \
      tests/test_reel_fragment_blocks.py tests/test_reel_spine.py -q
........................................................................ [ 48%]
........................................................................ [ 96%]
......                                                                   [100%]
150 passed in 0.21s

$ ruff check --config ruff-ci-gate.toml library/ tests/
All checks passed!
RUFF EXIT=0

$ FULL_SUITE_GATE_PYTHON=.../8/video_editing_pilot/.venv/bin/python ./scripts/full_suite_gate.sh
FULL-SUITE GATE: PASS  |  main: 4864 passed, 5 skipped  |  heavy_ml 2 passed, 0 skipped
GATE_EXIT=0
```

An unqualified PASS, not a NARROWED PASS. The two tests the gate deliberately does not
run are the two that drive a live Resolve, which it names.

---

## 9. Known instrument defects, unchanged and out of scope

- **`F2`/`F14` caption pairing.** Reel 23 (fragment fix) carries 20 `F2` and 24 `F14`
  for 44 cards over 20 segments; Reel 24 carries 21 and 25 for 46 over 21. In both the
  two numbers sum to the card count exactly, which is the signature report-5
  documented. Not a defect of any reel.
- **Provenance retains the last build only.** Building Reel 23 (fragment fix) moved the
  caption reference off Reels 24 and 25, which now read `NO-REFERENCE`. Watched moving
  for the fourth time today. Their caption findings are recorded in §5.2 from the run
  where they did have one.
- **`F5`/`F17` and the 19-second `well` row.** One transcript row, one word, no word
  timings, measured on its whole envelope. 2.3s here against report-5's 2.4s, and the
  difference is that the merge moved a card boundary - not that any speech went
  uncaptioned. `uncaptioned_pct` is 0.0.
- **`PLAN-MISMATCH` on seven old reels.** The verifier's own docstring explains it: the
  nineteen were built at 14:46 on 2026-09-05 and `MIN_TAKE_SECONDS` was removed at
  22:58 the same day, so today's cut rule re-derives a plan that never built them.
  Present with the fix disabled too.

## 10. Files

- fix: `library/tools/reel_spine.py` (`_merge_fragment_blocks`)
- tests: `tests/test_reel_fragment_blocks.py`
- request: `pipeline_output/llm_requests/select_reels.json` (98,711-char context)
- answer: `pipeline_output/llm_responses/select_reels.json` (29 moments, 19 considered,
  4 undetermined, 0 dropped by `post_bridge`)
- plan: `pipeline_output/review/reel_proposals_v2.json` (moments 24 and 25 appended;
  the first 23 asserted byte-identical before and after the write)
- conformance: `pipeline_output/review/conformance_report.json`
