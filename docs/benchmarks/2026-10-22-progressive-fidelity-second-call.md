# INFRA-002, the second call: the ranking is paid once

**What was measured.**  The SECOND call of the progressive-fidelity
ladder, on one real reel's real footage index, before and after the
coarse answer was kept.

**The reel.**  The GEO podcast project (`lucie/geo-podcast`) - seven
camera files, 3840x2160 MXF at 23.976 fps, the footage the series
actually shot - and its built frame index over them: 1092 sampled
frames, one every 10 s, embedded with CLIP ViT-L/14.  The captain's
media was READ; the media was not re-encoded, not copied and not
touched.  The frame index was COPIED into the bench's scratch area
and the verdicts were written into a scratch store, so the run wrote
nothing into the captain's project.

**The question.**  Three, each the kind of thing a planner asks about
a cut: "a laptop on a desk", "a phone on the table", "a coffee cup on
the desk" - the same three PR 3166 measured.

**The arms.**  Two trees, one command:

| arm | what it does |
|---|---|
| **before** | the code as it was: the tree at the base commit.  The second call ranks the index and verifies the candidates. |
| **after** | the code as it is: the second call reads the coarse answer the first call kept, and verifies ITS candidates. |

Each arm runs in its own process, so each pays its own model load, and
both arms are given the SAME verdict store, so the two are compared on
the same evidence: a difference in the answers is the LADDER's, and a
difference in the seconds is the RANKING's.

**Two stores, two claims.**  The verification verdicts are recorded per
source and per statement, so a second run of the same question is
served the recorded verdicts instead of asking the model again.

* the TIMING is measured on a WARM store: the store is filled first, so
  both second-call arms pay for the ranking and the verdict lookups,
  and the difference between them is the ranking the after-arm does not
  pay.
* the first-question cost is measured on a COLD store as well: each arm
  gets its own empty store and pays for the verdicts it needs.  A
  model's yes/no is not a deterministic function of the footage, so the
  cold answers are compared and any difference is REPORTED.

The bench is `docs/benchmarks/progressive_fidelity_second_call_bench.py`.
It exits non-zero if the two paths do not end on the same answer.

```
bin/vep docs/benchmarks/progressive_fidelity_second_call_bench.py \
    --project ~/Documents/content_stuff/video_projects/lucie/geo-podcast \
    --queries "a laptop on a desk;a phone on the table;a coffee cup on the desk" \
    --top-k 3 --before-tree <the base tree> --after-tree .
```

## The result

COLD stores for the first question, WARM stores for the second call:
the warm store carries the machine's recorded verdicts, so both
second-call arms pay for the ranking and the verdict lookups, and the
difference between them is the ranking the after-arm does not pay.
The cold pair pays fresh model calls on both arms.  Full report:
`/tmp/pf_second_call3.json` (bench run 2026-10-22, EXIT=0).

| question | coarse (first call) | warm second call before | warm second call after | cold second call before | cold second call after | answers identical |
|---|---|---:|---:|---:|---:|:--:|
| a laptop on a desk | 19.2 s | 25.8 s | 14.9 s | 585.5 s | 520.6 s | yes |
| a phone on the table | 31.8 s | 11.8 s | 7.7 s | skipped (one cold pair is the claim) | skipped | yes |
| a coffee cup on the desk | 8.1 s | 8.7 s | 4.9 s | skipped | skipped | yes |

**Denominators.**  3 questions asked, 3 second-call answers identical
(windows, verdicts and reasons), 0 failed, 0 refused, 0 unmeasured.
The cold pair: 1 asked, 1 identical, both arms paying 3 fresh model
calls at ~150-180 s each - the model, not the ladder, sets that price.

**What the seconds say.**  The after-arm reuses the stored coarse
report (`coarse_report.status: reused`) and never re-ranks the index:
the warm delta (25.8 -> 14.9 s, 11.8 -> 7.7 s, 8.7 -> 4.9 s) is the
ranking the second call no longer pays.  The cold delta (585.5 ->
520.6 s) is the same ranking saved inside a call dominated by fresh
model verdicts.  After <= before on every pair; no pair changed the
answer.

**What the costs say.**  The pass reports its own cost, and both arms
agree on it: laptop warm 0 fresh / 3 cached calls both arms; laptop
cold 3 fresh / 0 cached both arms; phone and coffee 0 fresh / 21
cached, 2 verified and 19 rejected, both arms.  The escalation did not
narrow the pool and did not widen it.

## The typed Project Model path, before and after

The second gap PR 3166 left was not a cost: it was a silence.  The
typed Project Model path (`ren evidence`) answered without declaring
its resolution, so a claim about a span of the source and a claim about
the model's own structure were printed in the same voice.

The bench is `docs/benchmarks/typed_path_resolution_bench.py`.  It runs
the SAME command in two trees - the tree at the base commit and the
tree as it is - against a Ren-owned scratch project
(`ren-scratch-share-pair-a`), read with `--no-record` so the run writes
nothing to it.

| question | before declares | after declares | after tier | answers identical |
|---|---|:--:|:--:|:--:|
| measurement plan ("Clicking" sound event) | no | yes | detailed | yes |
| measurement plan, absent label ("Explosion") | no | yes | coarse | yes |
| graph plan (entity) | no | yes | coarse | yes |
| trace of one observation | no | yes | coarse | yes |

**Denominators.**  4 questions asked, 0 declared a resolution before,
4 declare one after, 4 answers identical apart from the declaration
(the declaration is added; the rows, status and reasons are untouched).
A claim about a span of the source answers at the detailed tier and
carries the media behind it; a claim about the model's own structure -
and an answer whose lanes never ran - answers at the coarse tier with
the way up to the missing evidence.

## What the coarse answer is, and is not

The stored coarse answer is a RANKING: it says where to look, and the
`fidelity` block says so in the answer itself.  It is not a finding,
and the block that says it is stored beside it, so a later call that
escalates from it cannot present it as one.

The store is DERIVED data: it lives in the project's scratch area
beside the index, it is safe to delete at any moment, and deleting it
costs one ranking - never an answer that cannot be had again.  A
caller that does not want it says so: `ren search --visual
--fidelity coarse --no-coarse-store`.

## What was left undone

- The `original` tier is reachable rather than answered at: a detailed
  answer names the recording and the span, and no lane yet ANSWERS a
  question at that tier.
- The store is not shared between machines and it is not part of the
  Project Model: a coarse answer kept on one machine does not travel
  to another.
