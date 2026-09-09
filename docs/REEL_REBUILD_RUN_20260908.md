# Reel rebuild run 2026-09-08 - stopped after the first reel

Task: rebuild all approved reels as NEW timelines alongside the existing ones
(ADD-ONLY; master timeline `GEO Podcast - Synced` untouchable; one at a time).
Branch: `fm/vep-rebuild-19-reels-run`. Lane owns no code changes.

## What happened

Built reel 1 only, into a distinct container so nothing existing is touched:

`python3 manage_project.py build-reels geo-podcast --only-reel 1 --name-suffix " (rebuild)"`

- The BUILD node succeeded: `Reel 01 - geo-is-comprehension-not-position (rebuild)`
  was placed (0-1235 frames, 2 V1 clips - cut-identical to the harvest original).
- The VERIFY node then FAILED the gate (`reels_checked: 49, total_errors: 1227`)
  and the command exited non-zero. Per the brief ("if the first one fails,
  stop"), no further reels were built.

## Done-check - real output, read off the Resolve project (not the build log)

```
OPEN PROJECT: Podcast (field test)
TIMELINES BEFORE: 49 AFTER: 50
NAMES ADDED: ['Reel 01 - geo-is-comprehension-not-position (rebuild)']
ORIGINALS MISSING: []
MASTER BEFORE: {"start": 0, "end": 63694, "markers": 59, "v1": 86, "a1": 86}
MASTER NOW   : {"start": 0, "end": 63694, "markers": 59, "v1": 86, "a1": 86}
MASTER UNCHANGED: True
```

Provenance (`pipeline_output/review/plan_provenance.json`, `built_at:
2026-09-08T03:41:26Z`, now 21 reels recorded, merged not replaced):

```
Reel 01 - geo-is-comprehension-not-position (rebuild) -> v1:830f2f0e39b13f1869fe9d29437902464396583655ad390bf2b05dc9972fad24
```

So: caption_content_hash IS now present for the rebuilt reel. First-and-last
built are the same reel - only one was built before the stop.

## Counts

Built: 1. Skipped (stopped): 19. Differing in cut structure from original: 0 -
reel 1 rebuild is frame-identical to its harvest original (same caption hash
`v1:830f...` as the harvest timeline, i.e. current code derives the same
caption plan for this reel).

Note: the plan actually carries 20 approved moments (reels
1,5,7,9,10,12,13,15,16,17,18,20,21,23,24,25,27,28,29,30), not 19.

## Neighbour note - defect for a code lane (not fixed here)

Two independent reasons a sequenced rebuild cannot go green, both in code:

1. `verify_built_reels` (`library/tools/reel_build.py:2106`) takes no reel
   filter: every `build-reels --only-reel N` invocation grades ALL timelines
   in the project and raises on ANY error. One-at-a-time rebuilding can only
   pass after the last reel lands, never incrementally.
2. The freshly built reel 1 itself carries 34 errors: 15 F2 + 19 F14.
   Exact mismatch, measured live on the rebuilt timeline:
   - BUILD side places captions at per-spine-BLOCK granularity: one V3 clip
     per rendered overlay (`reel_build.py:1441`, `trackIndex: 3`), one
     overlay per spine block (`generate_subtitle_props_per_block`: "one per
     spine block containing subtitles"). Reel 1: 15 V3 items.
   - VERIFY side expects captions at per-subtitle-ENTRY granularity: 34
     planned cards re-derived from `subtitles.plan` `subtitle_entries`
     (`reel_conformance_verifier.py:_derive_planned_captions`), paired to a
     placed item starting within 2 frames (`PAIRING_TOLERANCE_FRAMES`).
   - Each block's FIRST card pairs with its block-spanning item, so its
     duration mismatches (F2, placed = whole block vs planned = one card,
     deltas +22..+111 frames on reel 1); every NON-first card in a block
     finds no item starting at its frame (F14 "planned and never placed").
   - The harvest original carries the same 15 V3 items, so the mismatch is
     structural and predates this rebuild; it is not something the rebuild
     introduced. Either half is a code defect (place per-card items, or
     grade per-block spans); this lane changed no code.

Captain decision needed: rebuild the remaining 19 anyway (each will fail the
gate the same way, adding 19 failing timelines), or fix the F2/F14
granularity mismatch and the whole-project gate scope first and then rebuild.
