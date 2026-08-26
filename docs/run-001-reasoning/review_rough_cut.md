# review_rough_cut (step 3.03) - reasoning

Written before the answer. Request: `pipeline_output/llm_requests/review_rough_cut.json`
(prompt 8,341 chars; context 39,894 chars; **expected_schema 43 chars** - `cut_decisions`,
type `list`, and nothing else).

## 1. The thing I found first, and it changes how to read this whole step

**`cut_decisions` has no reader.** I grepped the entire repository (excluding `.git`,
`.venv`, `node_modules`) and the string appears in exactly one place:

```
library/steps/step_3_03_review_rough_cut/manifest.json:60:  "name": "cut_decisions",
```

That is the manifest declaring it as an output. No step, no tool, no test, no downstream
consumer reads it.

So this step has two halves with very different standing:

- **Part 1, the mechanical checks**, run in `step.py` before I am called, produce
  `rough_cut_review.passed`, and really do gate the pipeline. They ran, and all six passed:
  duration invariant, b-roll duration invariant, timeline continuity, source files exist, no
  duplicate ranges, total duration (59.44s inside 54-66s).
- **Part 2, the narrative review** - checks 5 through 9, which is the bulk of the prompt and
  which the prompt itself calls "the most important step" - is what I produce, and it goes
  into `pipeline_data.json` where nothing will ever read it.

This is worth stating in AGENTS.md's own terms (10.4): *"A gate that cannot fail is worse
than no gate, because it reads as coverage."* The narrative half of this gate cannot fail
the run no matter what I write. I could report a broken narrative and the pipeline would
proceed to render it.

**I am not treating that as licence to phone it in.** I did the review properly below,
because a human reading `pipeline_data.json` or the step summary is a real audience even if
no code is. But the standing of the output should be visible to whoever evaluates this run,
and it would not be visible from the answer alone.

## 2. What I actually read

- **`rough_cut_review`** - the mechanical results, in full. All six passed with empty
  violation lists, so Part 2 is licensed to run.
- **`a_roll_assignments`** - all 11 rows, and I expanded the `video_segments` JSON on every
  one to get real `video_in`/`video_out`, dimensions and rotation.
- **`b_roll_assignments`** and **`b_roll_interjections`** - all 7, same treatment.
- **`speech_sequence`** and **`creative_direction`** - for checks 8 and 9, which compare the
  cut against what 2.01 and 2.02 said they were doing.
- **`audio_spine`** - block order and music behaviour.

**I went outside the context once, deliberately:** the reconstructed cut carries
`needs_conform` and `rotation` per clip, and the assignments disagreed in a way I could not
explain from the context alone, so I read the catalog in `pipeline_data.json`. Section 4.

**Check 5 as specified is not quite what I did, and I should be exact about that.** The
prompt says to reconstruct the script by looking up the temporal index for the source range
of each block, *not* from the speech_sequence's intended text - the point being to catch a
case where the two disagree. The temporal index is not routed to this step's context (the
manifest declares `temporal_index` as an input, but what arrived is the assignments and the
speech sequence). What I have is the aligned `video_in`/`video_out` on each assignment,
which came from word-level alignment at 2.02 rather than from the intended text. That is
one remove better than the intended text and one remove worse than the prompt asks for, and
it is the strongest reconstruction available from what I was given.

## 3. The review itself

**Reconstructed script, in timeline order** (11 A-roll blocks, 46.03s of speech):

> *(hook)* i can feel the silent judgment of the people behind me. / and i have an
> announcement to make. / um, not really an announcement. this doesn't really concern anyone
> except me. / and so my very, very small announcement is that i just want to post every
> single day. / i get caught up in all the numbers and metrics and you know the idea of
> succeeding that i just kind of enter a state of paralysis where i don't do the thing in
> the first place / the goal of all this is just to get momentum going. / it doesn't matter
> what i'm using to record. / it doesn't matter if it's even edited. / it just matters that
> it gets posted. / i've been literally this week i've quit every single day and the only
> reason i'm recording today is because i told myself this is the last shot i got / even if
> it's bad, even if i hate it, i will post it.

**Check 6, sentence completion: PASS, with two blocks worth naming.** Nine of eleven blocks
end on a full stop in the transcript. The two that do not are the two cut out of long
run-on regions:

- Block 7 ends at 116.512s on "...where i don't do the thing in the first place". The source
  region continues "but just post the video so" to 118.23s. The cut lands on a complete
  clause.
- Block 14 ends at 40.120s on "...this is the last shot i got". The source continues
  "because like what other like you know how other way would it line up..." to 55.59s. Also
  a complete clause.

Both end exactly where the word alignment put the last word of the chosen text, so neither
is a mid-word cut. **What I cannot check is whether either lands mid-breath**, which is an
audio property and there is no prosody in this project.

**Check 7, narrative flow: PASS.** Zero `broken`, zero `jarring`. Rated per transition:
smooth x7, acceptable x3. The two softest joins, named honestly:

- *announcement -> diagnosis* (block 5 -> 7): a turn from what he is doing to why he has not
  been, with no connective word. `acceptable`. A 1.38s cutaway sits between them.
- *"it just matters that it gets posted" -> "i've quit every single day"* (block 12 -> 14):
  the deliberate reversal, and the only place the piece changes location. `acceptable`
  rather than `jarring` **because** a transition slot and a car-interior cutaway sit between
  them specifically to prepare it. Without that cutaway I would rate this `jarring`.

**One dangling reference, and it is mine.** Check 7 asks whether any block names something
not yet introduced. The script says "post every single day" (block 5) and then "it doesn't
matter what i'm **using to record**" (block 10) - but it never says what he is posting. At
2.02 I chose `clip_011` 63.135 ("my very, very small announcement is that i just want to
post every single day") over 66.69 ("i'm going to post **a video**, at least one, every
single day"), and I chose it for register. The version I rejected is the one that names the
medium. In practice the viewer is watching a man film himself and "record" arrives four
blocks later, so it resolves - I rate it acceptable, not a rejection. But it is a real cost
of that choice and I did not see it when I made it.

**Check 8, emotional arc: PASS, with one deliberate absence.** Opening establishes
vulnerability (hook plus the undercut), there is development, a climax and a resolution. The
absence: check 8 asks for "context, backstory, the 'why'", and the biographical why - school,
three and a half years at university, work that did not excite him - is entirely cut. That
was 2.01's decision, argued there, and executed at 2.02. The development this cut has is
thematic (metrics -> paralysis -> post anyway) rather than biographical. Justified, not
missing by accident.

**Check 9, key moments: PASS, all three present.**
- *The admission* -> block 14 (clip_017 31.454-40.120). Core preserved; the anniversary tail
  of the same source region deliberately excluded.
- *The diagnosis* -> block 7 (clip_011 100.519-116.512).
- *The commitment and its landing* -> both halves present: block 5 (63.135-66.675) and
  block 15 (119.234-121.940).

## 4. The observation the mechanical checks do not make

Reading `needs_conform` across the assignments: **every A-roll block is `true` and every
B-roll placement is `false`.** That looked wrong, so I checked the catalog. It is not wrong,
and the reason is a real property of this project:

| | clips | rotation | displays as | conform |
|---|---|---|---|---|
| A-roll (clip_011, 012, 017) | 3 | **0** | 1920x1080 landscape | needed |
| B-roll (clip_001, 002, 004, 005, 006, 008, 014) | 7 | **-90** | 1080x1920 portrait | not needed |

All ten clips are 1920x1080 on disk. Every clip carrying speech in this edit was shot
landscape; **every cutaway clip I selected was shot portrait.** I did not engineer that -
I chose those seven for content, and the split is a property of how the footage was filmed.

The consequence for the finished video is worth flagging even though nothing here can act on
it: the piece will alternate between A-roll that has been conformed from landscape into a
portrait frame (subject-tracked pan, or the synthesised blurred backdrop) and B-roll that
fills the delivery frame natively at full resolution. Those two halves will not look alike.
Every open question about A-roll framing - how much plate the blurred backdrop shows - lands
on the 46 seconds of speech and none of it on the 13.4 seconds of cutaway.

That is an observation, not a rejection. It is also the single most useful thing I learned
about this cut, and no mechanical check covers it.

## 5. What I was missing

- **The temporal index**, which check 5 explicitly requires for a faithful reconstruction
  (section 2). I reconstructed from the aligned assignment ranges instead and said so.
- **Any way to hear the cut.** Every check in Part 2 except 6 is about how it plays, and I
  am reading a table. Whether block 12 -> 14 actually feels jarring is a question about 200
  milliseconds of audio.
- **Any way to see it.** Same for the conform observation in section 4: I inferred a visual
  discontinuity from a rotation column.
- **A creative brief.** Seventh step.

## 6. Confidence

**High** on the verdict. The mechanical half passed 6/6 with empty violation lists, and the
narrative half has no broken transition, all three key moments present, and a complete arc.
I would pass this rough cut.

**High** on the conform observation - it is read straight off the catalog, not inferred.

**Medium** on check 6, because "complete clause" is a judgement about text and the real
question is whether the audio cut is clean.

**Low** on my own objectivity here, and it should be said: **I am reviewing my own work.**
2.01, 2.02, 2.04, 2.05 and 3.02 were all mine, and this step asks me to gate them. The
dangling-reference finding in check 7 is the one place I marked my own earlier decision
down, and I went looking for it deliberately for that reason. A reviewer who had not made
these choices would find more.
