# The finished-video acceptance benchmark (QA-010)

**What it is.** A benchmark whose unit is a FINISHED VIDEO: a file that
was delivered, the brief it was made for, and two readings of it that
are kept apart - the engine's own measurements of the file, and an
independent reading of the file against the brief.

**Why it exists.** The editorial acceptance harness
(`library/tools/editorial_acceptance/`) measures a run of briefs on five
stages, each on its own denominator. Four of them had a denominator. The
fifth - `final_quality`, *"does the delivered FILE satisfy the brief, and
pass its technical checks?"* - did not: in the committed acceptance
report every one of the eight briefs reads `not_measured` for it,
because a task record with no delivered file and no assessment has
nothing to measure. A benchmark that stops at the plan measures the
engine's reasoning about a video it never watched.

## The two halves, and the rule between them

| half | what it is | measured by |
|---|---|---|
| MECHANICAL | the delivered file's own gates | `editorial_acceptance.judgement.objective_legs` - QA-002 picture, QA-003 captions, QA-004 audio, each measurement NAMED |
| CREATIVE | the brief's own acceptance criteria, judged against the delivered file | `editorial_brief_scorer.score_brief`, with a reviewer who did not see the plan |

**They are never combined.** They are not the same unit: one is a
measurement and one is a reading. A file that failed its audio spec
reads as a seven-out-of-eight edit the moment the two are averaged, and
that is the defect this benchmark exists to prevent. So the report
carries two denominators side by side and no third number.

**An unmeasured half is never a zero.** A leg that could not run is
`not_measured`, with the reason, and it counts as neither a pass nor a
failure.

**A technical failure is not hidden by an acceptable subjective score.**
The per-video outcome is the engine's own acceptance gate
(`library/tools/acceptance_state.py`, QA-008) run over the four legs a
delivered file can answer for itself, so a technical failure is a
failure whatever the creative score says. The release gate checks the
HIDING per video, because a corpus-level average is exactly where it
would go.

## The corpus

A corpus is a JSON file (`ren-finished-video-corpus/1`) naming the
finished videos, the brief each was made for, and what is known about
how each one got there:

```json
{
  "schema": "ren-finished-video-corpus/1",
  "corpus_id": "ren-scratch-finished-videos-2026-10-21",
  "brief_set": "library/tools/editorial_acceptance/brief_set.json",
  "videos": [
    {
      "video_id": "ren-scratch-bench-demo-reel-13",
      "brief_id": "the-whole-job",
      "path": "/abs/path/to/the/exported/file.mp4",
      "platform": "tiktok",
      "reviewer": "who judged it",
      "saw_the_plan": false,
      "judgments": [{"id": "<criterion id>", "status": "pass|fail|not_measured",
                     "rationale": "...", "evidence": [{"artifact": "...", "observation": "..."}]}],
      "history": {"first_attempt_accepted": false, "attempts": null,
                  "human_correction_seconds": null, "preserved_work": null},
      "sources": {"first_attempt_accepted": "where that fact came from"}
    }
  ]
}
```

- The brief comes from the acceptance brief set by id, or from the
  corpus itself. Either way it is the PUBLIC half: a brief carrying the
  answer key is refused.
- `saw_the_plan` is the independence declaration. A reviewer who read
  the plan is not independent, and their reading is not counted.
- `history` is what the RUN recorded. A video that recorded nothing is
  `not_measured` for that number - never a zero, and never a failure.

## The four tracked numbers

Each is measured over the videos that RECORDED it, and each carries its
own denominator:

| number | denominator | reading |
|---|---|---|
| first-pass acceptance | the videos that recorded whether their first attempt was accepted | how many of those were |
| revision count | the videos that recorded an attempt count | mean and max revisions (attempts - 1) |
| human correction time | the videos that recorded a correction time | mean and max seconds |
| preserved work | the checks that were recorded | passed over recorded |

## The release gate

The punch list's quality release gate, computed rather than asserted:

1. `representative_set` - the corpus holds at least three finished
   videos (`MIN_REPRESENTATIVE_VIDEOS`);
2. `every_video_delivered` - every video in the corpus was delivered;
3. `independently_reviewed` - every video carries an independent review;
4. `no_hidden_technical_failure` - no video carries a technical failure
   behind an acceptable creative score.

The gate is met only when every condition is met, and each condition
carries its own count and its own reason.

## Running it

```
ren accept --finished-video tests/live/finished-video-corpus.json
```

The command prints the baseline table, the four tracked numbers with
their denominators, and the gate's verdict, and writes the report to
`tests/live/reports/finished-video-benchmark.json`.

## The second run (2026-10-22): the gate is met

Corpus `ren-scratch-finished-videos-2026-10-22`: the two launch-reel
exports plus the tightening baseline. The 2026-10-22 render came through
Ren's own deliver path (render Complete, mastered to -14.34 LUFS) on
`ren-scratch-bench-demo2`; the record step refused on the undeclared
ending, so the project ledger holds no entry and the file stands on its
render+master report. The corpus declares the intended silent ending per
video (`declared_silence`, `declared_black`, with the basis in each
entry), and the two launch-reel histories are derived from their
projects' own records (`history_from` -
`library/tools/finished_video_history.py`) instead of hand-typed. Report:
`tests/live/reports/finished-video-benchmark--finished_video_acceptance-4.json`
(the writer keeps the 2026-10-21 report beside it rather than
overwriting a differently-scoped one).

| finished video | delivered | tech legs measured | tech legs failed | tech legs not measured | creative measured | creative passed | creative failed | outcome | failure hidden | revisions | human correction s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ren-scratch-bench-demo-reel-13 | delivered | 2 | 1 | 1 | 7 | 2 | 5 | failed | False | 5 | 48652.8148 |
| vep-live-workflows-e2e-e2e-011-baseline | delivered | 2 | 1 | 1 | 2 | 1 | 1 | failed | False | | |
| ren-scratch-bench-demo2-reel-13-redeliver | delivered | 2 | 0 | 1 | 7 | 3 | 4 | editorially_rejected | False | 3 | | |

| number | denominator | value | status |
|---|---|---|---|
| first-pass acceptance | 2 | 0.0 | measured |
| revision count | 2 | mean 4.0 (total 8, max 5) | measured |
| human correction time | 1 | mean 48652.8s | measured |
| preserved work | 11 checks | 9 of 11 | measured |

The release gate: **MET** - 3 videos, all delivered, all independently
reviewed, no technical failure behind an acceptable creative score. The
gate measures the benchmark's completeness, not the product's success:
all three outcomes read `failed`/`editorially_rejected`, zero accepted.
The old export still fails its loudness (-23.18 LUFS); the declaration
fixed the ending findings and nothing else. The caption leg is still
`not_measured` on all three (no attributable caption intent), and
`ren deliver` still cannot record the timeline (the silent logo card the
motion-graphics layer places declares no silence project-side). Detail
and what is still open:
`docs/evidence/finished-video-acceptance.md`.

## The baseline (2026-10-21)

The corpus is every distinct finished render in Ren's own scratch
projects: the exported launch reel of `ren-scratch-bench-demo` (79.573s,
1080x1920) and the rendered baseline the `vep-live-workflows-e2e`
project kept as the before of its tightening workflow (75.776s). Both
are Ren-owned scratch; the captain's project is not in the corpus, and
neither is any file that is not a finished render.

| finished video | delivered | tech legs measured | tech legs failed | tech legs not measured | creative measured | creative passed | creative failed | outcome | failure hidden | revisions | human correction s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ren-scratch-bench-demo-reel-13 | delivered | 2 | 2 | 1 | 7 | 2 | 5 | failed | False | | |
| vep-live-workflows-e2e-e2e-011-baseline | delivered | 2 | 1 | 1 | 2 | 1 | 1 | failed | False | | |

The tracked numbers, with their denominators:

| number | denominator | value | status |
|---|---|---|---|
| first-pass acceptance | 1 | 0.0 | measured |
| revision count | 0 | none | not_measured |
| human correction time | 0 | none | not_measured |
| preserved work | 0 checks | none | not_measured |

The release gate: **NOT MET**.

| condition | verdict | why |
|---|---|---|
| representative_set | NOT MET | the corpus holds 2 finished videos; three is the smallest set that can carry a denominator |
| every_video_delivered | met | 2 of 2 delivered |
| independently_reviewed | met | 2 of 2 carry an independent review |
| no_hidden_technical_failure | met | no video carries a technical failure behind an acceptable creative score - both videos' creative readings failed too |

### What the baseline found

- **The delivered launch reel fails its own audio spec.** Integrated
  loudness -23.18 LUFS against the -14.0 LUFS delivery target
  (tolerance +/-1.0), true peak -4.49 dBTP, and 3.253s of picture plays
  over digital silence at 75.701-79.529s. The ending the captain
  designed - the TV switching off to black, then the logo card - IS in
  the file, but nothing declared it, so the export's own gate cannot
  tell an intended ending from a defect. The fix is a declaration
  (`render_qa.declared_ending_spans` reads the assembly manifest), not
  a re-render.
- **The same reel carries one undeclared black frame segment**, for the
  same reason.
- **The name bar does not sit inside the safe area.** Measured off the
  delivered still, its ink box is x 58-480, rows 1308-1402, and
  `platform_safe_zones.intrusions` puts that box inside the LEFT crop
  band of every vertical platform (tiktok x 0-76, instagram_reels
  x 0-81, youtube_shorts x 0-95, linkedin x 0-118). The collision with
  the captions IS gone - the bar's ink ends at row 1402 and the
  caption's begins at 1420 - so half of the criterion is met and half
  is not.
- **The reel's captions were never measured.** The caption leg is
  `not_measured` on both videos because no caption intent was supplied:
  the delivered captions had nothing to be compared against. This is
  the gap the punch list names, in the place it appears.
- **The creative reading fails on what the file can answer**: the
  repeated statement is still in the opening (0.00-3.20s and
  3.20-5.44s), no cutaway appears in any of the stills taken of the
  file, and the export does not carry every requested change. Three
  criteria - which angle was replaced, which number the caption
  misstated, and what the human had changed before the build - are
  `not_measured`, because the file alone cannot answer them.
- **Three of the four tracked numbers have no denominator at all.**
  Nobody recorded how many times the reel was built, how long the
  corrections took, or what the human's own edits were before and
  after. The benchmark reports them as `not_measured` rather than as
  zeros, which is the whole point: the numbers do not exist yet.

### What the baseline does not fix

- It does not make the corpus representative. Two finished videos of
  ONE reel is a corpus of one assignment with two renders, and the
  gate says so.
- It does not repair the delivered reel. It reports what the file is.
- It does not judge the four tracked numbers into existence. They are
  recorded by whoever runs a build, and the corpus is where that record
  lives.

## The modules

- `library/tools/finished_video_benchmark.py` - the corpus, the scoring,
  the tracked numbers, the gate and the table.
- `library/tools/acceptance_state.py` - the gate the per-video outcome
  comes from; `evaluate()` takes an optional subset of legs, and a leg
  it does not know raises.
- `library/tools/editorial_acceptance/__main__.py` - `ren accept
  --finished-video`.
- `tests/contracts/test_finished_video_benchmark.py` - the defect-naming
  tests.
- `tests/live/finished-video-corpus.json` - the corpus.
- `tests/live/reports/finished-video-benchmark.json` - the baseline
  report.

The incident and the measurements: `docs/RULE_EVIDENCE.md#the-benchmark-that-measured-nothing-at-the-end`.
