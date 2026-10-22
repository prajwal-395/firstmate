# INFRA-002 - progressive fidelity: the coarse answer first, and the way up

**What was measured.**  A planning question about the picture, asked of one
real reel's real footage index, before and after the ladder landed.

**The reel.**  The GEO podcast project (`lucie/geo-podcast`) - seven camera
files, 3840x2160 MXF at 23.976 fps, the footage the series actually shot -
and its built frame index over them: 1092 sampled frames, one every 10 s,
embedded with CLIP ViT-L/14.  The captain's media was READ; the media was
not re-encoded, not copied and not touched.

**The question.**  Three, each the kind of thing a planner asks about a
cut: "a laptop on a desk", "a phone on the table", "a coffee cup on the
desk".

**The arms.**

| arm | what it does |
|---|---|
| **before** | the lane as it was: `search_ranges(temporal=True)` - the ranking, then the local vision model's verdict on the candidates, and only then an answer.  One call, one answer, and the answer is the VERIFIED one. |
| **after** | the ladder: `search_ranges(temporal=False)` answers at the COARSE tier, and `verify_ranges` computes the DETAILED tier from that answer's candidates - no re-ranking, no second payment for the ranking. |

Each arm ran in its own process, so each paid its own model load, and each
arm got its OWN cold verdict store (the real frames, symlinked; the verdict
file left out), so the timing is the real cost of a first question.  The
verification verdicts are recorded per source and per statement, so a
second run of the same question is served the recorded verdicts instead of
asking the model again - which is why the EQUALITY was measured a second
time, on a SHARED store, where the two arms are compared on the same
evidence.

The bench is `docs/benchmarks/progressive_fidelity_bench.py`.  It exits
non-zero if the two paths do not end on the same answer.

```
bin/vep docs/benchmarks/progressive_fidelity_bench.py \
    --project ~/Documents/content_stuff/video_projects/lucie/geo-podcast \
    --queries "a laptop on a desk;a phone on the table;a coffee cup on the desk" \
    --top-k 3
```

## The result

COLD stores - every arm pays for the verdicts it needs:

| question | before: first usable answer | before: full fidelity | after: first usable answer | after: full fidelity | final answers identical |
|---|---:|---:|---:|---:|:--:|
| a laptop on a desk | 446.5 s | 446.5 s | **62.0 s** | 378.6 s | yes |
| a phone on the table | 435.9 s | 435.9 s | **25.4 s** | 410.3 s | yes |
| a coffee cup on the desk | 472.6 s | 472.6 s | **19.1 s** | 355.0 s | yes |

**Denominators.**  3 questions asked, 3 supported (the two paths ended on
the same answer), 0 failed, 0 refused, 0 unmeasured.  On the shared stores:
3 of 3 answers byte-identical, window by window and verdict by verdict.

**Time to a first usable answer: 446.5 s -> 25.4 s at the median, 17.6x.**
The coarse tier is a CLIP query encode over the built index, and it says
where to look.

**Time to full fidelity: 446.5 s -> 378.6 s at the median, 15 % LESS.**
The escalation is not a second payment for the ranking, so the after-arm's
full-fidelity answer costs no more than the before-arm's - it costs less,
because the after-arm's second half starts from candidates it already has.

**The final answers are the same.**  The same windows (camera, span, score)
and the same verdicts (the model's yes/no and its reason) on all three
questions, on cold stores and on shared stores alike.

## What the coarse answer is, and is not

The coarse answer and the verified answer are NOT the same windows, and the
measurement shows why they must not be confused.  For "a phone on the
table" the coarse tier's three best candidates were `clip_003` at 4779.9 s,
`clip_004` at 199.8 s and `clip_007` at 829.9 s; the verified answer is
`clip_004` at 140.2 s and `clip_001` at 0.1 s.  Two of the coarse tier's
three candidates were REJECTED by the evidence, and the verified answer's
two windows were not in the coarse tier's presented three at all.

That is the whole point of the ladder, and it is why the coarse answer
carries its own words:

- `fidelity.tier`: `coarse`;
- `fidelity.establishes`: "where to look";
- `fidelity.does_not_establish`: "that the candidate establishes the claim";
- `escalation`: what the detailed tier would establish, and its measured
  cost.

A ranking that does not say it is a ranking reads as a finding.  The
coarse answer is a place to look; the escalation is what turns it into a
place that was looked at.

## The two defects the measurement found

**The escalation verified a SMALLER pool than the one-call path.**  The
coarse answer kept the windows it PRESENTED and dropped the merged
candidates behind them, so `verify_ranges` judged the presented windows
only - fewer calls, a cheaper answer, and a DIFFERENT answer.  The first
run of the bench caught it: the two arms' answers did not match.  The
coarse answer now carries the candidate set (`candidate_ranges`) and the
escalation judges it.  `tests/unit/picture/test_progressive_fidelity.py`
names the defect.

**The escalation's cost estimate counted windows as candidates.**  It said
21 calls where the pass judges 21 candidates across seven cameras - the
same number by luck, but for the wrong reason, and it would have been
wrong for any other pool.  It now counts the candidates per camera and
says that it is an UPPER BOUND: the pass visits the cameras round robin
and stops as soon as the caller's list is full.

## The cost of a verification call

The figure the escalation quotes is
`span_verification.MEASURED_SECONDS_PER_INTERVAL_VERDICT` = 81.0 s per
call.  That is the SLOWEST measured: the first run on this reel measured
81.0 s per call over ten calls, and later runs measured 13.1-17.8 s per
call.  The number a caller is shown is therefore the conservative end of
the range, and the estimate is an upper bound on the calls as well.

The cost is in the MODEL, not in the footage: a call shows four frames of
one interval and asks one question about them, and the model's answer is
the only thing that can establish the claim.

## What was left undone

- The typed Project Model path (`ren evidence`) still answers without
  declaring its resolution.  The ladder reaches the frame lane and the
  retrieval service today; the evidence lane is next.
- The coarse answer is not persisted between calls, so an escalation run
  as a SECOND command pays the ranking again.  `ren search --visual
  --fidelity detailed` does both in one call and pays it once.  A shared
  on-disk coarse report was NOT built: `ren search` is a read verb, and
  writing into the project's scratch from it is a change of contract that
  belongs above this lane.
- The ladder's `original` tier is reachable rather than answered at: every
  window names the recording and the extracted frames of it, and no lane
  yet ANSWERS a question at that tier.
