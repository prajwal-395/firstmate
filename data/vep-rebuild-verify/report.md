# Reel 03, rebuilt with the whole-take rule: what it opens on, and whether it is good

**Date:** 2026-09-06
**Project:** `Podcast (field test)` / master `GEO Podcast - Synced`
(`~/Documents/content_stuff/video_projects/lucie/geo-podcast`)
**Built:** `Reel 03 - search-didnt-change-the-question-did (whole-take rebuild)`
**Base:** `6cb9658` (`main`, with PR #580 the whole-take rule and PR #579 the
caption hash)
**Branch:** `fm/vep-rebuild-verify`

---

## 0. The answer in six lines

1. **It opens on the model's hook, at 0.00s.** Akshita, "Yeah, so search
   didn't change." First frame, first word, first caption card. The
   3.41-second regression is gone.
2. **The repeated take is STILL THERE, whole and deliberately.** The build
   said so on its own stdout. That is what the whole-take rule does when a
   run cannot go entirely.
3. **It is now the captain's approved Reel 03, four frames shorter.** 955
   frames against 959. Same opening, same span, same closer.
4. **The captions are worse than the captain's, and the 1.5s regression
   survived.** It survived for a reason that has nothing to do with the reel:
   the build reads a transcript cached 29.6 hours before the fix landed.
   Applying the fix to that file's own words takes it to zero.
5. **26 of the new timeline's 27 verifier errors are the instrument**, and I
   can name every one of them. The caption hash fix made F2/F14 gradeable and
   they immediately fire on correct output.
6. **Is the reel good? NO.** Not because of anything this build did wrong, but
   because the reel says its one sentence four times in forty seconds, and no
   rule in the engine may decide which take to drop. That decision is the
   captain's or the selector's, and the build says so by name.

---

## 1. The build: the commands, the guard, and the twenty-two

### 1.1 What was run

```
$ python3 manage_project.py build-reels \
    /Users/prajwal/Documents/content_stuff/video_projects/lucie/geo-podcast \
    --only-reel 3 --name-suffix " (whole-take rebuild)"
```

`cmd_build_reels` takes its node order off `library/processes/reels/dag.json`
and runs each node through `library/tools/operations.py`. **No direct module
call was made.** `reel_build.rebuild_reels_in_project` was reached only where
the `build_reels` step body calls it.

| # | Node | Operation | Result |
|---|---|---|---|
| 1 | `build_reels` | `reel.build` | **completed** |
| 2 | `verify_reels` | `reel.verify` | **raised** - 121 errors across 21 reels, 94 of them on timelines this build never touched (§3.5) |

One refusal happened first and is worth recording, because it is the
requirement layer doing its job: the first invocation exited 1 with

```
REFUSED: reel.build
This selection cannot run. Refusing before the run starts.
  DaVinci Resolve's scripting module can be found - set RESOLVE_SCRIPT_API ...
      needed by: build_reels, verify_reels
```

`RESOLVE_SCRIPT_API` / `RESOLVE_SCRIPT_LIB` were not set in this worktree.
Exporting them cleared it. (The message reads "can be found" where it means
"cannot"; a one-word bug in the refusal text, noted and not fixed here.)

### 1.2 The deletion guard deleted zero

Verbatim, the first line of the build's stdout:

```
Deleting nothing: none of ['Reel 03 - search-didnt-change-the-question-did (whole-take rebuild)'] exists yet.
```

That line is the `else` branch immediately after `assert_deletion_scope`, so
the guard ran and the delete set was empty.

### 1.3 Twenty-one before, twenty-two after, all twenty-one identical

Content-hashed from **copies** of
`~/Pictures/Davinci/.../Podcast (field test)/Project.db`, never opened in
place. The hash joins `Sm2Timeline -> Sm2Sequence -> Sm2SequenceContainer ->
Sm2TiTrack -> Sm2TiItem` and digests each item's name, start, duration, in
point, media path, media start and track type.

*How I know the instrument is right:* my per-timeline item counts reproduce
the previous lane's published counts exactly on all twenty-one rows (334, 34,
55, 35, 44, 82, 36, 29, 46, 34, 51, 52, 31, 47, 62, 43, 55, 76, 58, 61, and 31
for their rebuild), which pins the join to the same one. The md5 values differ
from theirs because the field set digested is mine, and only my own
before/after comparison is claimed.

| timeline | items | md5 before | md5 after | identical |
|---|---:|---|---|---|
| `GEO Podcast - Synced` | 334 | `9992165831d583eb…` | `9992165831d583eb…` | **yes** |
| `Reel 01 - seo-ranks-geo-understands` | 34 | `8bb48dba9fdf41a9…` | `8bb48dba9fdf41a9…` | **yes** |
| `Reel 02 - seo-that-hurts-your-ai-ranking` | 55 | `3ca0764e6913a9f4…` | `3ca0764e6913a9f4…` | **yes** |
| `Reel 03 - search-didnt-change-the-question-did` | 35 | `1eed9de5780bc60f…` | `1eed9de5780bc60f…` | **yes** |
| `Reel 03 - … (pipeline rebuild)` | 31 | `fd4fc86140cbbded…` | `fd4fc86140cbbded…` | **yes** |
| `Reel 04 - consistency-beats-size` | 44 | `f275dacbf92907d9…` | `f275dacbf92907d9…` | **yes** |
| `Reel 05 - the-audit-that-was-eye-opening` | 82 | `b13a318695fd946e…` | `b13a318695fd946e…` | **yes** |
| `Reel 06 - where-ai-is-reading-you` | 36 | `deda73cea14fc73a…` | `deda73cea14fc73a…` | **yes** |
| `Reel 07 - website-is-resume` | 29 | `3a95a64b9e9b78d7…` | `3a95a64b9e9b78d7…` | **yes** |
| `Reel 08 - why-ai-trusts-a-cited-brand` | 46 | `8ad0ab4c7c61d8e9…` | `8ad0ab4c7c61d8e9…` | **yes** |
| `Reel 09 - first-step-is-understanding` | 34 | `5299f840b73fcc57…` | `5299f840b73fcc57…` | **yes** |
| `Reel 10 - the-seven-modules` | 51 | `4c36a5772486f0d3…` | `4c36a5772486f0d3…` | **yes** |
| `Reel 11 - what-hallucinating-actually-means` | 52 | `032a3094f03ad4e1…` | `032a3094f03ad4e1…` | **yes** |
| `Reel 12 - not-a-content-problem` | 31 | `8ec13978d99d822d…` | `8ec13978d99d822d…` | **yes** |
| `Reel 13 - a-score-is-not-a-fix` | 47 | `b14e5642ceb7ea18…` | `b14e5642ceb7ea18…` | **yes** |
| `Reel 14 - small-business-beats-the-behemoths` | 62 | `ca2be0e0b2cc553b…` | `ca2be0e0b2cc553b…` | **yes** |
| `Reel 15 - why-youtube-outranks-other-video` | 43 | `fb970ed185b3c538…` | `fb970ed185b3c538…` | **yes** |
| `Reel 16 - how-people-actually-search-now` | 55 | `374034092c417843…` | `374034092c417843…` | **yes** |
| `Reel 17 - four-slots-and-nothing-else` | 76 | `7171a9a63df002e6…` | `7171a9a63df002e6…` | **yes** |
| `Reel 18 - your-google-business-profile` | 58 | `8ec40a4a64f6a5cb…` | `8ec40a4a64f6a5cb…` | **yes** |
| `Reel 19 - can-you-game-ai` | 61 | `14d0a79db6a54601…` | `14d0a79db6a54601…` | **yes** |
| `Reel 03 - … (whole-take rebuild)` | 30 | *(did not exist)* | `636c33108ddcd3ee…` | **new** |

```
before: 21 timelines    after: 22 timelines
CHANGED OR MISSING: NONE
ADDED: ['Reel 03 - search-didnt-change-the-question-did (whole-take rebuild)']
```

Three independent statements of the same fact: the table above, the guard's own
line, and the verifier's `Read-only proof: ALL 22 timelines identical
before/after.`

The only other writes were inside the project's own `pipeline_output/`: 16
rendered caption overlays and their props under
`steps/4_05_render_subtitles/`, a timestamped plan archive, a merged
`plan_provenance.json`, `review/conformance_report.json`, and
`step_outputs.build_reels` in `pipeline_data.json`.

---

## 2. What the model decided, and why

### 2.1 The selection, in the model's own words

From `pipeline_output/llm_responses/select_reels.json` - the model's text
before anything post-processed it. Its entry for reel 3, complete:

```json
{
  "start": 301.24,
  "end": 341.27,
  "slug": "search-didnt-change-the-question-did",
  "hook": "'Search didn't change. The question changed.' The whole thesis in six words, first line.",
  "reason": "The tightest thing said in the episode and it is already whole: Akshita lands the line, Craig takes it into why it decides who gets recommended, and Akshita closes on the invitation herself. No borrowed closer needed.",
  "value": "A stranger gets the entire premise in one repeatable sentence and a way to test it on their own brand.",
  "close": "Its own. Akshita at 333.80-341.27: 'And that's why we've been building the Lucy visibility system for months. And if you want to see how your brand appears, you should go check it out. The link's in our bio.'",
  "takes_dropped": ["Akshita re-records the thesis line twice inside the span (306.8 and 309.9)."]
}
```

Four decisions in there, each of them load-bearing:

- **It refused a borrowed closer.** The brief lets a reel end on a CTA lifted
  from anywhere in the episode. The model set none for this one - *"No
  borrowed closer needed"* - because the passage already ends on the
  invitation. Confirmed mechanically: `cta_range(moment)` returns `None`, so
  `reel_ranges` is the body window alone, and the proposal file records
  `"call_to_action": null`.
- **It heard the repetition before any measurement ran.** `takes_dropped` names
  the re-records at 306.8 and 309.9. That is the model reporting a flaw in the
  material it chose, not hiding it.
- **It named where the hook must sit** - *"first line"*. That claim is the one
  §3 has to judge the built timeline against, and this time the timeline meets
  it.
- **It refused to pad the batch.** Asked for 25 to 30 reels it returned 19,
  and accounted for the difference rather than filling it:

  > "The measurements found 27 two-hander windows in the whole episode; of
  > those, five are second and third takes of an exchange already proposed,
  > two have a transcript too garbled to read on screen, one is a monologue
  > in everything but name, one loses its hook to an unrelated aside, and one
  > contains a picture hole. [...] I have not padded the batch to reach the
  > number, and I have not dropped anything that clears the bar."

  Nine of those are named individually in `considered`, with the reason
  each was left out - including *"2494.6-2572.1 [...] the span contains the
  master timeline's picture hole at 2523.9-2528.0, so four seconds of it
  would play black"*, which is a measurement the model was given and used.

### 2.2 The captain's approval, verbatim in the plan

> "yeah i skimmed through and these are a lot better overall, i'm not gonna
> give rigerous feedback on the scripts, but im gonna approve them and want
> you to build it all out and then i'll give feedback on the actual timeline
> you create"

### 2.3 What the BUILD decided, and what it refused to decide

The span holds four consecutive Akshita lines that are a repeat of four later
ones. Under the old rule two of the four were cut and two were refused, which
removed the head of a take and stranded its tail. Under PR #580 the unit is
the RUN, and the build printed its own reasoning to stdout:

```
  repetition kept at 301.24-306.53s (Akshita): 2 of 4 lines in this repeated
  run could be paired safely and 2 could not, so cutting it would have removed
  part of a take and left the rest standing where its own opening used to be.
  A take is removed WHOLE or not at all, so this repetition is still in the
  reel.
```

`refused_take_groups` also carries the fix it will not make itself:

> "decide which take to keep and redraw the span past the other, or leave the
> repetition in - the choice of take is a judgement and this is not the place
> that makes it"

That is the engine declining to invent a creative judgement (AGENTS.md 10.5),
and it is why the reel is what it is.

### 2.4 The cut list this build actually applied

Derived read-only from `redundant_takes` under `6cb9658`, and matching the
timeline frame for frame:

| dropped | kept instead | text | ratio |
|---|---|---|---|
| 309.920-310.120 (0.200s) | 310.120-310.380 | "Yeah, so search didn't change. The question changed. And whoever AI understands best, gets the answer." | 1.30 |

Everything else in the span is kept. Keep ranges:
`[(301.241, 309.920), (310.120, 341.270)]` = 39.829s = 954.9 frames.

The two lines the old rule cut (301.241-302.566 and 302.626-303.449) are back,
because the two it could not cut (303.549-306.400 at ratio 4.75, and
306.400-306.527 at ratio 2.13) hold the whole run.

### 2.5 The card boundaries step 4.01 decided

27 cards in 16 rendered segments, grouped by measured pixel width against the
safe area. The reel as it reads, in play order (caption text verbatim from the
plan the build hashed):

```
 0.00 A  yeah, so search didn't change.          <- the model's hook, first line
 1.39 A  the question changed.
 2.31 A  and whoever ai best
 3.69 A  understands, gets the answer.
 5.16 A  yeah
 5.56 A  so,yeah, so search didn't change        <- the thesis, 2nd time
 6.60 A  the question changed.
 7.60 A  so,yeah
      --- 7.87-8.94  NO CAPTION (Akshita, audible; §3.4) ---
 8.94 A  best will have the answers.
      --- 10.55-12.25 NO CAPTION (Craig: "search didn't change"; §3.4) ---
12.25 C  the questions change so that's
13.74 C  why this is so important that's
15.18 C  who ai is going to recommend
16.65 C  to be the answer it's exactly
18.58 C  why we've been building this platform
20.17 C  we're calling the lucy visibility
21.89 C  system we'd love
23.16 C  for you to go check it
24.40 C  out see how ai sees you
      --- 25.79-27.17 NO CAPTION (Craig: "the links in the bio"; §3.4) ---
27.17 A  search didn't change, the question      <- the thesis, 4th time
28.79 A  changed, and whoever ai understands
30.55 A  best will get the answer.
32.36 A  and that's why we've been building
33.68 A  the lucy visibility system for months.
36.15 A  and if you want to see
36.85 A  how your brand appears,
37.80 A  you should go check it out.
39.15 A  the link's in our bio.                  <- its own CTA
```

---

## 3. The six questions, answered on the BUILT timeline

Everything below is read off a copy of `Project.db` or off the rendered
artefacts, not off the plan.

### 3.1 What does it open on, at what timecode, and is that the model's hook?

**It opens on "Yeah, so search didn't change.", at 0.00s, and that is exactly
the model's written hook.**

Three independent readings agree:

- **Picture.** The first V1 item is `LC4932.MXF` with `In = 7417`.
  7417 / 23.976 = 309.332s into the source, which is the block whose master
  time is 301.241 - the first second of the span. The frame is Akshita, mid
  sentence, on camera.
- **Caption.** The first item on the `Captions` track starts at frame **0** and
  is `sub_..._akshita_0_309332-310657_...mov`, whose props carry one card:
  `"yeah, so search didn't change."`
- **Plan.** Card 1 spans -0.00 to 1.32s; card 2, `"the question changed."`,
  runs 1.39 to 2.21s. The model's six-word thesis is complete by 2.21 seconds.

The model asked for it *"first line"*. It is the first line.

*How I know this is about the reel and not the tool:* the `In` -> source-second
mapping is checked against the spine's own `source_start` for the same block
(309.3324s, which is 7416.6 frames), and against the master transcript row at
301.241. Three numbers derived three ways, agreeing to under a frame.

One caveat the brief itself raises. The brief says *"The first line has to earn
the next five seconds [...] Not throat-clearing, not somebody settling into a
sentence."* The line begins "Yeah, so". The claim lands immediately after it,
so this is a much smaller problem than opening on the answer, but it is not
nothing on a silent-autoplay vertical.

### 3.2 Is the repeated speech gone, still there, or partly there?

**Still there, and WHOLE - which is the rule working, not failing.** Nothing is
partly removed.

The build reported why, at build time, on its own stdout (quoted in §2.3): 2 of
the 4 lines in the repeated run could be paired safely and 2 could not, so the
run was held entire.

What a viewer hears, measured from the caption cards:

| # | reel time | who | the sentence |
|---|---|---|---|
| 1 | 0.00-5.16s | Akshita | "Yeah, so search didn't change. The question changed. And whoever AI best understands, gets the answer." |
| 2 | 5.56-7.87s | Akshita | "So, yeah, so search didn't change. The question changed. So, yeah" |
| 3 | ~11.3-12.15s | Craig | "search didn't change" (uncaptioned, §3.4) |
| 4 | 27.17-31.93s | Akshita | "Search didn't change, the question changed, and whoever AI understands best will get the answer." |

Statements 1 and 2 are the two takes the whole-take rule declined to separate.
Statement 4 is a different row that the plan's own `duplicate_takes` field
already flags as `band: "repeat", similarity: 1.0` against 310.12. Statement 3
is Craig picking the line up, which is conversation rather than a retake.

The cut between statement 1 and statement 2 is a **jump cut on the same shot**:
source 314.78s and source 460.36s are the same framing, same wardrobe, same
pose, because the retake was recorded later and the captain's rough cut placed
it adjacent. So the repetition has no visual change to hide behind.

Exactly one thing was removed: the 0.200s duplicate transcript row at
309.920-310.120, which is 4 frames.

### 3.3 Three-way comparison: frames, opening line, captions

All three read off the same `Project.db` copy, in one pass.

| | approved `Reel 03` | first rebuild `(pipeline rebuild)` | this build `(whole-take rebuild)` |
|---|---:|---:|---:|
| V1 picture items | 4 | 6 | 5 |
| frames placed | **959** | **904** | **955** |
| seconds @ 23.976 | 40.00 | 37.71 | 39.83 |
| first V1 `In` | 7417 | 7448 -> 7470 | 7417 |
| **opens on** | "Yeah, so search didn't change." | "And whoever AI best understands, gets the answer." | "Yeah, so search didn't change." |
| model's hook arrives at | 0.00s | **3.42s** | **0.00s** |
| caption items on the timeline | 27 | 14 | 16 |
| caption cards planned | 27 | 27 | 27 |
| word-seconds of speech played | 26.78 | 24.87 | 26.78 |
| **uncaptioned, word-measured** | **0.00s** | **1.50s** | **1.50s** |
| uncaptioned, envelope residue | 0.33s | 1.45s | 1.57s |
| delivery frame | 1080x1920 | 1080x1920 | 1080x1920 |

**On frames.** 959 - 955 = 4 frames = 0.167s, and that is the one 0.200s
duplicate row, quantised. The verifier reaches the same number independently
and states it as a defect in the *approved* reel: *"the plan re-derived for
this reel lays down 955 frames and the timeline carries 959 (+4 frames,
+0.17s)"*. The first rebuild's 904 is 51 frames below the plan for the same
reason in the other direction: it was built under the old rule.

**On the opening.** This build and the captain's approved reel open
identically. The first rebuild is the only one of the three that opens on the
answer before the question.

**On captions.** This is where the pipeline is behind. The captain's approved
reel captions everything the reel plays; both pipeline builds leave 1.5s of
Craig uncaptioned, and this build leaves a further ~1.07s of Akshita
uncaptioned that the approved reel covers with a single card
(`..._akshita_so-yeah-best-will-have-the-ans_...mov`, reel 7.59-10.80s).

**The honest short version of the three-way:** this build is the captain's
approved Reel 03, four frames shorter, with worse captions. On picture and
speech the pipeline has now reproduced what the captain already had. It has
not improved on it.

### 3.4 How many seconds of speech have no caption over it?

**1.50 seconds by the word-level measure, plus about 1.07 seconds the
word-level measure cannot see. Call it 2.4 to 2.6 seconds. It is a real
regression against the captain's version, and it did NOT get better today.**

*How I know these numbers are about the reel and not the tool:* I measured them
twice, by two routes that share no code.

- **Route A, mine.** For every word the master transcript timestamps inside the
  reel's kept ranges, map it into reel seconds and ask whether any item on the
  built timeline's `Captions` track covers it. Result: **1.50s**, on eight
  named words.
- **Route B, the verifier's.** `check_caption_coverage` clips each word to the
  reel independently and subtracts caption coverage. Result: **1.5s**, and its
  own detail records `residue_seconds: 1.6`, `total_uncaptioned_seconds: 3.1`.

Route A reproduces Route B's straddling figure exactly and its residue figure
to 0.03s (I get 1.57). Two instruments, one number.

The eight words, with their reel times:

```
  11.308-12.149  Craig   search  didn't  change
  25.929-26.790  Craig   the  links  in  the  bio
```

Both are load-bearing: the first is the reel's own title line, the second is
the call to action. On a silent-autoplay vertical an uncaptioned CTA is a lost
CTA. **The captain's approved Reel 03 captions both**, to the frame
(`..._craig_search-didn-t-change-the-quest_...mov` at 11.51-13.39s and
`..._craig_in-the-bio_...mov` at 26.48-26.99s).

**Did the word-level rebinding of PR #576 fix it? No, and the reason is not
the fix.**

The build reads `pipeline_output/scratch/timeline_transcript/transcript.json`,
written **2026-09-05 01:01**. PR #576 landed **2026-09-06 06:38**, 29.6 hours
later, and it changed `timeline_transcript`, which runs at transcript
PRODUCTION time. The cached file this build read still holds the old, unsplit
straddling rows. A file on disk is not a measurement (AGENTS.md 10.3), and this
is that rule biting.

I proved both halves of that rather than asserting either:

1. **The fix works on this footage.** Running today's `clip_runs` over the two
   straddling rows against the master timeline's real 334-clip list splits both
   cleanly, and every run lands on `LCATL0013.MXF`:

   ```
   row 295.65-313.59 Craig  words=15  source_file=None
      run of 12 words 295.650-299.414 -> LCATL0013.MXF : more people as you stated they are no longer searching they're asking
      run of  3 words 312.749-313.590 -> LCATL0013.MXF : search didn't change
   row 327.37-346.08 Craig  words=23  source_file=None
      run of  5 words 327.370-328.231 -> LCATL0013.MXF : the links in the bio
      run of 18 words 342.038-346.080 -> LCATL0013.MXF : so what we're hearing a lot from people that we talk to ...
   ```

2. **Applying it to the cached file's own words clears the reel entirely.**
   Re-binding in memory (126 rows newly bound across the episode) and
   re-deriving reel 03's spine:

   ```
   CACHED (what this build read):  blocks 16   unbindable_seconds 1.5
   REBOUND (#576 applied):         blocks 18   unbindable_seconds 0
   ```

   The two extra blocks are exactly the two Craig runs. **1.5 -> 0.**

So the fix for this is cheap and it is not a code change: the words are already
in the cached transcript, only the clip binding is stale. Either re-run
`python3 -m library.tools.timeline_transcript <project>` (the 283 cached audio
spans survive, so it is a WhisperX pass rather than an extraction), or add a
rebind-only path that re-runs `attribute_to_clip` over an existing transcript.
Then rebuild. I did neither here: it is a change to the captain's project data
and it would have made this build incomparable to the previous one.

**The other ~1.07 seconds, which no finding names as an error.** The reel plays
reel 7.87-8.94 with no caption item over it. Those seconds come from two
transcript rows (master 309.320-309.920 and 310.120-310.380) that carry a
`source_file` but **zero word timings**, so the spine drops them silently and
the word-level coverage measure counts them as nothing. The verifier's own
`residue_seconds: 1.6` includes them, but the headline message reports only the
straddling half and the summary table's `uncap` column shows only 1.5.

*Is there really speech there, or is this the instrument again?* I measured the
audio. On `akshita.wav`, whose duration (2656.57s) matches the master
timeline's last transcript row (2656.55s) to two decimal places, so its
timebase is master seconds:

| window | median dBFS | frames above -45 dB |
|---|---:|---:|
| **the gap, 309.111-310.380** | **-28.1** | **0.90** |
| control speech, 308.840-309.111 ("So,yeah") | -29.8 | 1.00 |
| control speech, 310.380-312.041 ("best will have the answers") | -34.5 | 0.75 |
| control silence, 312.5-313.5 (Craig's turn) | -180.0 | 0.00 |
| control silence, 322.0-323.0 (Craig's turn) | -180.0 | 0.00 |

The stem is digitally silent where this speaker has no clip, so the controls
are unambiguous. The gap is as loud as confirmed speech. **Akshita is talking
there and nothing is written over it.** The approved reel covers it with one
card.

### 3.5 The verifier's findings on the new timeline, each classified

`reel.verify` graded all 21 reel timelines and raised. Its own read-only proof:
`Read-only proof: ALL 22 timelines identical before/after.`

| | new timeline | the 20 others |
|---|---:|---:|
| errors | **27** | 94 |
| warnings | 4 | 37 |

**Do not read 27 as a verdict.** Twenty-six of them are one bug in the
instrument, and I can account for every finding individually.

#### The instrument: F14 x11 and F2 x7 - the caption pairing bug, again

`check_caption_duration` pairs each planned **card** to a placed **item** by
start frame. The pipeline caption path renders **one item per spine BLOCK**,
with the block's cards animated inside it. 27 cards, 16 items. So:

- every card that is not first in its block has no item at its start frame ->
  **F14 "planned and never placed"**;
- every first-in-block card of a multi-card block is paired with an item as long
  as the whole block -> **F2 with a large positive delta**.

Predicted from the block structure, before reading the findings: blocks 2, 8,
9, 10, 11, 13 and 14 hold more than one card, contributing 1+2+1+3+1+1+2 = **11**
non-first cards. The verifier reported **exactly 11 F14 findings**, on cards 4,
11, 12, 14, 16, 17, 18, 20, 23, 25 and 26 - exactly the non-first cards. And it
reported **exactly 7 large-delta F2 findings**, on cards 3, 10, 13, 15, 19, 22
and 24 - exactly the first cards of those same seven blocks, with deltas
(+35, +67, +37, +94, +39, +46, +51) equal to the rest of each block.

*How I know this is the instrument and not lost captions.* Three checks:

1. The 16 props files list **27 cards in total**, one row per planned card,
   with per-card `startFrame`/`endFrame` inside the segment.
2. I sampled four frames out of the 4-card segment
   `..._craig_10_483563-489183_...mov` at frames 30, 65, 95 and 130 and the
   four cards appear in order: *"we're calling the lucy visibility"* ->
   *"system we'd love"* -> *"for you to go check it"* -> *"out see how ai sees
   you"*, with Craig's accent colour on the emphasis word. Nothing is missing.
3. The item's placed start (484) is `round(20.169 x 23.976)` and its duration
   (134) covers the block's whole 5.62s.

This is the same class as the "701 caption errors" that turned out to be a
pairing bug, and it is here because PR #579 did its job: the caption hash is no
longer hollow, so F2 and F14 now RUN, and the first thing they do is fail
correct output. **A gate that fails correct output is no more coverage than one
that cannot fail** (AGENTS.md 10.4). This is the third data-shaped defect in
this chain and it needs fixing before F2/F14 mean anything.

#### Real, but one frame: F2 x8 with delta -1

Cards 1, 2, 5, 6, 7, 9, 21 and 27 are the only card in their block and are
placed one frame shorter than planned. Reading the numbers off the timeline,
the placer computes `round(start x fps)` and `floor(end x fps)` while the plan
rounds the duration, so the two disagree by at most one frame whenever the end
lands mid-frame. Card 8 lands on exact frames and matches.

Real, systematic, present on every reel this path builds, 41.7ms, and invisible
to a viewer. The placer's convention is the defensible one - flooring the end
is what stops consecutive cards overlapping - so the fix belongs in how the
plan states `frames`, or in giving F2 the same one-frame tolerance its pairing
already uses.

#### Real and viewer-visible: F5 x1

`1.5s of speech from straddling segments has no caption` - **a real defect**,
confirmed independently in §3.4, plus ~1.07s more that the finding's headline
does not carry. This is the one error on the list a viewer would notice.

#### The four warnings

| class | message | classification |
|---|---|---|
| F5 | 2 rows carried no word timings, so their envelope was counted as speech; the seconds are an upper bound | **honest self-limit, and it under-reports.** The seconds it is hedging about are not in the headline number at all. See §3.4. |
| F7 | 2 of 27 cards under 0.5s and **HELD, not failed**: each is the last card of its block | **correct behaviour.** They are `'yeah'` (0.127s) and `'so,yeah'` (0.271s) - real short utterances. |
| F8 | 2 boundary/row overlaps landed between two words of a straddling row and **cut no speech** | **not a defect**, and it says so. |
| PQ-LENGTH | plan is 39.8s, under the 45.0s minimum guidance | **real, and it matters.** The brief asks for 45-90s. See §3.6. |

#### The 94 errors on the other twenty timelines - not caused by this build

Listed so they are not mistaken for damage. All are read-only findings about
timelines this build never touched, and their hashes are unchanged:

- **60x F17** and **2x F15** - caption defects, the classes PR #573 added.
- **20x NO-REFERENCE** - 19 are *"provenance records the moment plan but no
  caption plan"* on reels built before caption hashes existed; the twentieth is
  new and is PR #579 working: *"the recorded caption hash is a hollow v0 digest
  [...] Treated as absent"*, on the first rebuild. The hollow hash is now
  detected and refuses to grade instead of falsely passing.
- **7x PLAN-MISMATCH** - reels whose built length no longer matches what today's
  `redundant_takes` says the plan is. Reel 03's +4 frames and the first
  rebuild's -51 are both in here, and both are the instrument telling the truth
  about old output.
- **3x F8, 2x F5** - boundary and caption-coverage findings on the originals
  (`Reel 02`, `Reel 06`, `Reel 07` cut a speaker mid-word; `Reel 02` and
  `Reel 06` leave 2.2s and 4.1s uncaptioned).

#### The new caption hash: it records something real

PR #579's fix is confirmed on the artefact. The build recorded
`v1:001cdc340365d65e31d3becda8236cc3bd955285c65516b521746d898091ccb7`, and
re-deriving the plan's 27 cards and hashing them reproduces that digest
**exactly**. Sensitivity checks:

```
base                          v1:001cdc340365d65e...
one card's text changed by 1  v1:101f655940b79bc3...   differs: True
one card 0.5s longer          v1:7d0c64bb300da783...   differs: True
27 contentless dicts          v1:97ae7526b4905010...   differs from base
```

Under the old code, 14 contentless dicts reproduced the recorded digest
byte for byte. They no longer do. **The verifier now has a real baseline to
grade against - which is precisely why the F2/F14 pairing bug became visible
today.**

### 3.6 Is this reel good?

**No.**

Not because the build did anything wrong. The build did the right thing at
every step it controls, and it said so out loud. The reel is not good because
of what is in the forty seconds.

**1. It says its one sentence four times in forty seconds, and the picture does
not change.** "Search didn't change, the question changed, and whoever AI
understands best gets the answer" lands at 0.0s, again at 5.6s, again from
Craig at 11.3s, and again from Akshita at 27.2s. The 0.0s and 5.6s statements
are the same speaker in the same shot with the same framing, so the cut between
them reads as a stutter rather than an edit. A stranger watching this hears one
idea repeated, not one idea developing, which is the brief's own bar
(*"Atomic. One idea developing"*, *"Coherent. Someone who has never heard the
episode can follow it"*). **This is the single reason the answer is no.**

The engine is right not to fix it. `refused_take_groups` names the repetition,
names what could have gone, names what stopped it, and hands the decision back:
*"decide which take to keep and redraw the span past the other [...] the choice
of take is a judgement and this is not the place that makes it."* That is a
correct refusal. It also means **the pipeline cannot make reel 03 good on its
own**, and nobody should expect a rebuild to.

**2. Two load-bearing lines play with no subtitle**, and one of them is the
CTA. §3.4. This one IS fixable without any creative decision, and the fix is
identified and proven.

**3. It is 39.8s, under the brief's own 45-90s preference.** The approved reel
was already under it at 40.0s and this is 0.17s shorter.

**What is genuinely good about it:**

- The opening is correct and matches the model's written intent to the frame.
- The picture is clean end to end: 955 frames, five items, zero holes, zero
  one-frame gaps, the delivery frame right at 1080x1920, and every cut landing
  on a speaker change or a real boundary. The verifier raises nothing about
  the picture.
- It ends on its own CTA, as the brief requires, with no borrowed closer.
- The build is honest: every choice it made and every choice it declined to
  make is printed, hashed and reproducible.

**What would make it good, in the order I would do it:**

1. **Redraw the span, which is the captain's call or the selector's.** The
   material for it is in the transcript. Starting at **295.65** instead of
   301.24 picks up Craig's *"more people, as you stated, they are no longer
   searching, they're asking"* - a setup that makes Akshita's *"Search didn't
   change, the question changed"* a payoff rather than a cold open. That adds
   5.6s and brings the reel to about 45.4s, into the brief's band. It does not
   remove the retake; removing that means starting at 306.801 instead, which
   loses the first take but drops the reel to 34.5s. **Both are creative
   choices and neither belongs to the engine.**
2. **Re-bind the transcript and rebuild** (§3.4). Clears 1.5s of the caption
   gap and needs no judgement at all.
3. **Caption the two zero-word rows, or say why they cannot be.** They are
   real, audible speech with no per-word timing. Either re-align them or make
   the coverage check report them in its headline number rather than only in
   its detail - it currently says 1.5 where the reel is missing about 2.6.
4. **Fix F2/F14's pairing** so the newly working caption hash grades something
   true. Pair a planned card to the ITEM THAT CONTAINS IT, not to an item
   starting at the same frame, and compare the card's span against the card's
   own animation window inside that item.

**If I had to ship one of the three today I would ship the captain's**, for the
captions alone. But I would not ship any of them without redrawing the span,
and that is the same conclusion the first rebuild reached from the other
direction.

---

## 4. The done-check, run and pasted

```
$ sqlite3 <copy of Project.db> "select count(*) from Sm2Timeline"
22

$ diff timelines.after.md5 timelines.before.md5
6d5
< 636c33108ddcd3eeabe760a7a1044df7    30  Reel 03 - search-didnt-change-the-question-did (whole-take rebuild)
(exit 1)
```

The only difference is the added line. All 21 pre-existing rows are byte-equal,
hash and item count alike.

```
$ python3 -m library.tools.reel_conformance_verifier \
    --project "Podcast (field test)" --master "GEO Podcast - Synced" \
    --plan .../reel_proposals_v2.json --transcript .../transcript.json
EXIT=1

Transcript: 875 rows
Reading all timelines (before hash)...
  22 timelines hashed.
Plan:    proposal file: .../reel_proposals_v2.json (19 moments)
Master picture holes: 2
  frame 59220 len 586 at 2469.97s
  frame 60512 len 99 at 2523.85s
  Reel 01 - seo-ranks-geo-understands: FAIL (3 errors, 2 warnings)
  Reel 02 - seo-that-hurts-your-ai-ranking: FAIL (16 errors, 2 warnings)
  Reel 03 - search-didnt-change-the-question-did: FAIL (2 errors, 4 warnings)
  Reel 03 - search-didnt-change-the-question-did (pipeline rebuild): FAIL (2 errors, 4 warnings)
  Reel 03 - search-didnt-change-the-question-did (whole-take rebuild): FAIL (27 errors, 4 warnings)
  Reel 04 - consistency-beats-size: FAIL (2 errors, 3 warnings)
  Reel 05 - the-audit-that-was-eye-opening: FAIL (2 errors, 2 warnings)
  Reel 06 - where-ai-is-reading-you: FAIL (26 errors, 2 warnings)
  Reel 07 - website-is-resume: FAIL (15 errors, 2 warnings)
  Reel 08 - why-ai-trusts-a-cited-brand: FAIL (1 errors, 1 warnings)
  Reel 09 - first-step-is-understanding: FAIL (1 errors, 2 warnings)
  Reel 10 - the-seven-modules: FAIL (2 errors, 2 warnings)
  Reel 11 - what-hallucinating-actually-means: FAIL (1 errors, 1 warnings)
  Reel 12 - not-a-content-problem: FAIL (2 errors, 2 warnings)
  Reel 13 - a-score-is-not-a-fix: FAIL (2 errors, 0 warnings)
  Reel 14 - small-business-beats-the-behemoths: FAIL (2 errors, 2 warnings)
  Reel 15 - why-youtube-outranks-other-video: FAIL (3 errors, 1 warnings)
  Reel 16 - how-people-actually-search-now: FAIL (3 errors, 1 warnings)
  Reel 17 - four-slots-and-nothing-else: FAIL (3 errors, 2 warnings)
  Reel 18 - your-google-business-profile: FAIL (3 errors, 1 warnings)
  Reel 19 - can-you-game-ai: FAIL (3 errors, 1 warnings)
Re-reading all timelines (after hash)...
Read-only proof: ALL 22 timelines identical before/after.
JSON written to .../pipeline_output/review/conformance_report.json

...
============================================================
FAILED: 121 error(s), 41 warning(s) across 21 reels.
```

Exit 1 is the verifier refusing on findings, 94 of which are on the twenty
timelines this build did not touch and 26 of which are the pairing bug in §3.5.
It is not a statement about damage: its own read-only proof, on the same run,
is that all 22 timelines are identical before and after.

---

## 5. Tests and CI

**No pipeline code was changed.** `git diff origin/main` over the repository is
empty apart from this report, so the local full-suite gate was not run and
nothing about it is claimed. Everything above is a measurement of `main` at
`6cb9658` against the captain's project.

GitHub's build service is still refusing to start jobs, so no CI verdict is
expected on the PR.

---

## 6. Every number in this report, and how I know it is about the reel

| number | how it was obtained | cross-check |
|---|---|---|
| 22 timelines / 21 identical | md5 over the item join, from copies of `Project.db` | item counts reproduce the previous lane's 21 rows exactly; the verifier's own before/after proof agrees |
| 955 frames | sum of the five V1 item durations in the db | plan says 954.9; verifier says `actual_frames: 955` |
| opens at 0.00s on the hook | first V1 item `In=7417` -> source 309.332s -> master 301.241 | first caption item starts at frame 0 and its props carry that text; spine block 0 has `source_start` 309.3324 |
| 4 frames removed | one cut, 309.920-310.120 = 0.200s | 959 - 955 = 4; verifier's PLAN-MISMATCH says +4 on the approved reel |
| 1.50s uncaptioned | my own word-to-item coverage over the built timeline | the verifier's independent `check_caption_coverage` reports 1.5s and names the same 8 words |
| 1.57s residue | same measure, rows that DO carry a clip binding | verifier's finding detail records `residue_seconds: 1.6` |
| ~1.07s of that is audible speech | RMS in 20ms frames on `akshita.wav` over master 309.111-310.380 | -28.1 dBFS median against -180 dBFS silence controls on the same stem |
| 1.5 -> 0 after re-binding | #576's `clip_runs` applied to the cached words, spine re-derived | the two rows split onto `LCATL0013.MXF`, spine gains exactly 2 blocks |
| 11 F14 + 7 large F2 are the pairing bug | predicted the exact card indices from the block structure before reading the findings | 27 cards live in 16 props files; 4 sampled frames show all 4 cards of one segment drawing in order |
| 8 F2 at -1 frame | placer uses `round(start)`/`floor(end)`, plan rounds duration | reproduced on every one of the 8, and card 8 with exact frames matches |
| caption hash is genuine | re-derived the 27 cards and hashed them | reproduces the recorded `v1:001cdc34…` exactly; changing one word or one card length changes it |

---

## 7. Files

- `data/vep-rebuild-verify/report.md` - this report. No code change.

Working files (hash scripts, coverage measurement, extracted frames) were kept
outside the repository and are not part of the deliverable.
