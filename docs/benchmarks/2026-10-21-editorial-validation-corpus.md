# BASE-003 - a representative editorial validation corpus

2026-10-21. The punch list's BASE-003, measured against the shipped
acceptance harness.

## What this is

The shipped acceptance set asks Ren eight questions. Four of them name
something the footage does not contain - a statement of what it costs,
a moment of overlapping speech, a caption that misstates the speech, a
music bed - so a run was measured for failing to find what was never
there. The captain ruled that set stays scored as it is (headline
3/8): it is the record of what the engine did with premises it could
not check.

That leaves the other half of the punch list's direction unbuilt: *"Ren
is evaluated on its ability to complete meaningful edits, rather than
primarily on whether it correctly rejects inapplicable requests."* This
lane adds the briefs that measure that ability - briefs written against
footage that actually contains the event they ask for, with answer keys
verified by looking at the footage.

The corpus lives in `evaluation/` (see `evaluation/README.md`). The
answer key is sealed outside every tree the engine ships; the public
half is derived from it and carries nothing but the request.

## The measurement, and its denominators

Both numbers are counts of briefs, with the denominator stated. Nothing
here is combined into one score.

| | shipped set (before) | new corpus (after) | both |
|---|---|---|---|
| briefs | 8 | 13 | 21 |
| briefs that ask for an edit | 5 | 8 | 13 |
| edit briefs whose premise the footage **carries** | **0 of 5** | **8 of 8** | **8 of 13** |
| briefs that pair with footage of their own shape | **0 of 8** | **13 of 13** | **13 of 21** |
| briefs whose correct answer is to report back | 3 | 5 | 8 |
| ...of which are refusals / questions / reports | 3 / 0 / 0 | 3 / 1 / 1 | 6 / 1 / 1 |

The before column is measured with the harness's own functions, not by
eye: `footage.select_substrate` over the shipped catalogue returns no
substrate for any of the eight, and `footage.applicability` against the
one shipped substrate reports `premise_not_carried` for the four briefs
whose premise it was checked for and did not find. Reproduce with:

```
python3 - <<'PY'
from library.tools.editorial_acceptance import briefs, footage
bs = briefs.load_brief_set(); cat = footage.load_catalogue()
sel = footage.select_for_set(bs, cat)
print("paired:", sum(1 for s in sel["briefs"].values() if s["selected"]))
PY
python3 evaluation/tools/corpus.py --audit
```

The after column: `evaluation/tools/corpus.py --audit` reports every
brief, the substrate it pairs with, and the state of each premise it
rests on. All 13 are settled, and all 13 pair with footage of the shape
the brief declares.

## The corpus shape, per category

| category | briefs | ids |
|---|---|---|
| podcast_edit | 1 | `open-on-the-cost-line` |
| interview_edit | 2 | `cut-the-repeated-definition`, `use-the-other-camera-on-that-answer` |
| single_camera_edit | 1 | `take-out-the-pause-where-she-stops` |
| broll_heavy_edit | **0** | **not covered - see below** |
| caption_heavy_edit | 2 | `lift-the-caption-row-but-keep-every-card`, `hold-the-name-card-until-she-finishes` |
| heavily_modified_timeline | 1 | `keep-my-captions-where-i-put-them` |
| external_modification | 1 | `take-out-the-soft-focus-card-i-asked-about` |
| negative_refuse | 3 | `there-is-no-music-under-this`, `cover-the-answer-with-a-screen-recording`, `cut-to-the-wide-shot-of-both-of-them` |
| negative_decision | 1 | `make-this-reel-punchier` |
| negative_insufficient_evidence | 1 | `cut-the-bit-where-she-reads-out-the-numbers` |

By the footage each brief runs against: two-camera interview 6,
caption-heavy cut 5, single-camera take 2.

**`broll_heavy_edit` is not covered, and the corpus says so.** The
measured footage holds no cutaway recording: the media pool's source
bin is the seven camera and recorder files and the rendered graphics,
its music bin is empty, and no reel in the project carries a b-roll row
(the track plan's b-roll flag is false for all fourteen). A brief for
that category would have been a brief the footage cannot answer, which
is the defect this corpus exists to remove. The category is named as
missing in `evaluation/corpus/public/manifest.json` and a test fails if
the corpus ever claims a category it has no brief for.

## How each premise was settled

Every premise was settled by reading the footage, never by inferring it
from the request. The full trail is in
`evaluation/corpus/sealed/evidence.json`; the shape of it:

- **the master** ("GEO Podcast - Synced", the scratch copy): 334 clips
  on two video rows and two audio rows, read through the recorded
  generation. video1 carries LC4930/LC4931/LC4932 and video2 carries
  LCATL0011 to LCATL0014 - two camera chains on the same conversation,
  which is how the second angle of a stretch is known to exist.
- **the transcript** of that master: 477 segments, every word timed,
  every segment naming the file it was spoken in. The cost statement,
  the sentence said three times, the eleven number passages and the
  1.22-second stop inside one continuous camera clip all come from it.
- **frames**: pulled from the media at the source times the transcript
  names and looked at. Craig speaking at the cost line (LCATL0011 at
  109.14s), Akshita speaking at both tellings of the definition
  (LC4930 at 134.40s, LC4931 at 67.87s), one person speaking and the
  other listening at the two moments the transcript calls overlapping.
- **the cut's own items**: 65 clips on eight rows - two camera rows,
  45 caption cards, a name card holding 3.50 seconds, the corner mark,
  the header, the freeze frame.
- **the delivered file**: 79.57 seconds, looked at frame by frame. Two
  angles of the same conversation, a caption under nearly every line,
  and no cutaway shot anywhere in it.
- **the notes the user typed onto the timeline**: the corpus carries
  one brief whose request is a note left outside Ren.

## What is refused, and what is asked

The five briefs whose correct answer is to report back are not one
kind of report:

- **refuse** - the thing the request names is not in the project. No
  music bed (the master has two microphone tracks and the music bin is
  empty), no cutaway to cover the line with (the pool holds none), no
  second angle to cut to (the project holds one camera file).
- **ask** - "make this reel punchier" names no target, no span and no
  measure. The run may propose; the brief is measured on the question
  being asked and on nothing changing first.
- **report** - "cut the bit where she reads out the numbers" names a
  place the footage does not settle: she reads numbers out in eleven
  places, and no one of them is the one she means. The correct answer
  names them and asks, rather than cutting the first one found.

## The separation, and the gates that hold it

- The answer key is at `evaluation/corpus/sealed/`, outside `library/`,
  `ren/` and `native/`. `tests/contracts/test_editorial_corpus.py`
  fails if any module under those trees names it. The gate was checked
  against a deliberately planted reference and does fail.
- The public half is DERIVED from the sealed half by the harness's own
  `public_view`, so it cannot carry the key; the harness's own leak
  check is run against every brief, and the committed public file is
  compared with the derived one.
- The harness's validator is the gate a new brief has to pass: the
  corpus's brief set is a `ren-editorial-brief-set/1` set, and
  `briefs.validate_brief_set` refuses it if it names an operation the
  engine does not have, a premise outside the enumeration, or a public
  view that carries its key.
- The shipped eight-brief set is untouched, and its digest is pinned in
  the contract test so a lane cannot quietly rescore it.
- The engine's own vocabulary grew by three features and two profiles
  (`library/tools/editorial_acceptance/footage.py`): the facts the new
  briefs rest on. Nothing else in the harness changed.

## What was left undone

- **`broll_heavy_edit` has no brief.** The footage available to this
  lane holds no cutaway recording, so a brief for that category would
  have been a brief the footage cannot answer. Closing it needs footage
  with cutaways in it.
- **Overlapping speech is not a premise.** Two segments in the
  transcript carry overlapping times under two speakers, but the frames
  at those moments show one person speaking and one listening, so the
  overlap is the transcription's. It is recorded as looked-for-and-not-
  found, and no brief rests on it.
- **No run against the new corpus yet.** This lane measured the
  corpus, not Ren's performance on it: there are no task records for
  the thirteen briefs, so the corpus carries no pass or fail. The
  scorer (`evaluation/tools/score_corpus.py`) reads records in the same
  shape `ren accept` reads, and reports with the harness's own writer.
- **The corpus was measured against scratch copies only.** No captain
  project, timeline or media was written; the podcast media was read.
