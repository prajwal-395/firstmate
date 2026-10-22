# Finished-video acceptance: the third video, and the three measured numbers

QA-010b, 2026-10-22. PR 3151 built the finished-video benchmark and left
its release gate NOT MET for two reasons: the corpus held 2 finished
videos where three is the smallest set that can carry a denominator, and
revision count, human correction time and preserved work were
`not_measured` on a denominator of 0. This run produces the missing
finished scratch delivery through Ren, measures those three, and reports
the gate.

## What was delivered

`ren deliver ren-scratch-bench-demo2 --timeline "Reel 13 -
the-accounting-firm-ai-called-healthcare"` rendered and mastered the
reel's live timeline on 2026-10-22 through Ren's own deliver path:

- render job `1f283717-4ed8-4550-94e9-ce302c70ce` Complete, 110,020,711
  bytes;
- master: input -20.88 LUFS / -2.19 dBTP to **-14.34 LUFS / -1.41 dBTP**
  against the -14.0 LUFS target (tolerance +/-1.0, true-peak ceiling
  -1.0) - the earlier export failed this same check at -23.18 LUFS;
- file: 79.529s, 1080x1920 at 23.976fps, H264 + stereo AAC, sha256
  `2671b97eda72a6d7f9b2c5a8f76e42a54e8b8e16e59b1ff0ccda45129ffb2298`.

The record step refused (`silence_under_picture=False`), so the project
ledger holds no entry. The refusal names the 75.7-79.5s tail: audio ends
at frame 1815, the freeze holds 1815-1834, and the Motion Graphics row
holds `logo_bulb_lines_safe_23976.mov` at 1834-1906. Stills at 77.50s
("See your brand the way AI does" + logo + luciecontent.com) and 79.20s
show the card playing: the tail is the captain-designed silent ending
(TV switch-off into the logo card), not missing dialogue. The fix is a
declaration, not a re-render - exactly the finding PR 3151 recorded.

The corpus declares it per video (`declared_silence 75.70:79.53`,
`declared_black 75.99:76.49`, the switch-off collapse inside the
19-frame freeze), with the basis in the entry: `external/reel_ending.json`
declares the `tv_power_tail` freeze for this reel on a 2026-09-11
captain marker, and the timeline rows carry the card. The same
declaration is applied to the earlier export (75.70:79.57 there).

What was NOT fixed by declaring: the earlier export still fails its
audio leg (-23.18 LUFS), the repeated opening statement is still in both
renders, no cutaway appears in either, and the name bar sits at the same
inset (x 58) inside every vertical platform's left crop band. A
declaration excuses an intended ending; it repairs nothing else, and the
report shows that: the old video still reads `failed` on its loudness.

## The three numbers, measured

They are derived from the projects' own records, not typed in:
`library/tools/finished_video_history.py` reads the version ledger (or
the creative commit ledger where the version ledger holds none), the
unified edit history, and the delivery acceptances, and derives the
history block the benchmark scores. A corpus entry carries either a
hand-written `history` or a `history_from` naming the project and reel;
both at once is refused. An unmeasured number is `None`, never 0.

| number | before (denominator) | after (denominator) |
|---|---|---|
| first-pass acceptance | 0.0 (1 recorded) | 0.0 (2 recorded) |
| revision count | not_measured (0) | mean 4.0, total 8, max 5 (2 recorded) |
| human correction time | not_measured (0) | mean 48652.8s (1 recorded) |
| preserved work | not_measured (0 checks) | 9 of 11 checks (11 recorded) |

- The two launch-reel videos are both first-pass `false`: a hand
  correction is recorded on each reel. The e2e-011 baseline is the
  before-state of its assignment, so it records neither acceptance nor
  refusal.
- Revision counts: 6 recorded acts (5 revisions) on ren-scratch-bench-demo
  (creative commit ledger, staging containers included - each was a build
  attempt) and 4 recorded acts (3 revisions) on ren-scratch-bench-demo2
  (version ledger).
- Correction time: 48652.8s on ren-scratch-bench-demo only - the window
  between the last recorded Ren act and the captain's correction. It is
  an UPPER BOUND (the window holds the time the reel was watched), and
  the basis travels with the number. ren-scratch-bench-demo2's
  correction carries no Ren act with a timestamp before it, so no window
  was measured there.
- Preserved work: 11 checks off the version ledger's own rows - 9 of 10
  rows survived the last revision unchanged; `row:video:Subtitles` lost
  an item, so it and `no_item_was_dropped` read false.

## The gate

`ren accept --finished-video tests/live/finished-video-corpus.json`,
report `tests/live/reports/finished-video-benchmark.json` (2026-10-21)
beside `tests/live/reports/finished-video-benchmark--finished_video_acceptance-4.json`
(the writer never overwrites a differently-scoped report):

| condition | verdict |
|---|---|
| representative_set | MET - 3 finished videos |
| every_video_delivered | MET - 3 of 3 delivered |
| independently_reviewed | MET - 3 of 3 independently reviewed |
| no_hidden_technical_failure | MET - no video carries a technical failure behind an acceptable creative score |

**Release gate: MET.** What that means and what it does not: the gate
measures the benchmark's completeness, not the product's success. All
three videos read `failed`/`editorially_rejected` on their outcomes -
zero accepted. The two loudness failures sit behind creative failures,
and the one video whose tech legs all pass fails on its creative
criteria, so nothing is hidden - but nothing is approved either.

## What is still open

- The caption leg is `not_measured` on all 3 videos: no caption intent
  was attributable (`delivered_caption_intent.caption_plan` unattributable
  - no assembly manifest in the scratch project), so the delivered
  captions had nothing to be compared against. Same gap PR 3151 named.
- `ren deliver` still cannot RECORD this timeline: the project declares
  the 19-frame freeze but not the 72-frame silent logo card the
  motion-graphics layer places, so the record step fails the file the
  render+master produced. The corpus-side declaration scores the file
  honestly, but the project-side gap (a silent card placed outside the
  card mechanism declares no silence) is real follow-up.
- Two tasks, one episode: the corpus is two renders of the launch reel
  plus the tightening baseline. A third distinct editorial task built
  and delivered would make the "representative set" representative in
  fact as well as in count.
