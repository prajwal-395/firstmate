# Rebuilding the captain's nineteen reels, in place - run report

Measured and written 2026-09-08. Authorised by the captain the same day:
"yes go for it, i want to have the videos as polished as we can make them.
so if you can safely rebuild them, then go for it".

## In one paragraph

Fifteen of the nineteen reels are rebuilt in place inside `Current plan`,
each through the pipeline's own build gate, each carrying the recorded
caption receipt none of them had before. The other four refused the gate
for reasons that are decisions, not defects: reels 05 and 09 end with no
call to action because their approved plans declare none, and reels 15
and 17 each carry one caption card that hangs past its speech (2.03s and
2.09s). Their existing timelines are untouched. No second of speech plays
with no caption on any rebuilt reel - 0.0 seconds of straddling speech
uncaptioned, 4.1 seconds of card-edge dust across 615.5 seconds of played
speech, worst single fragment 0.32s. The file count on the caption track
roughly halved (28 files to 13 on reel 01), and that is packaging, not
lost captions: each file now spans a whole passage and plays its cards in
turn. Same cards, same pace, fewer files.

## What changed per reel

Picture delta is old vs new timeline, in frames at 24fps. Cards are the
caption cards the viewer reads (derived from the build plan, hash-verified
against what was placed). Segments are the overlay files on the V3 track.
Coverage is seconds of played speech with no card on screen (straddling /
residue, measured the way the current-state report measured it).

| Reel | Picture | Cards old → new | Segments old → new | Uncaptioned (s) |
|---|---|---|---|---|
| 01 | -13f (-0.54s, repeated take out) | 28 → 32 | 28 → 13 | 0.0 / 0.1 |
| 02 | +3f (+0.12s, boundary re-snap) | 39 → 43 | 39 → 20 | 0.0 / 0.4 |
| 03 | -4f (-0.17s, repeated "Yeah" out) | 27 → 29 | 27 → 18 | 0.0 / 0.2 |
| 04 | identical | 30 → 32 | 30 → 15 | 0.0 / 0.1 |
| 05 | REFUSED, untouched (see below) | - | 54 → 54 | - |
| 06 | +11f (+0.46s, boundary re-snap) | 28 → 31 | 28 → 14 | 0.0 / 0.6 |
| 07 | +1f (+0.04s, boundary re-snap) | 21 → 25 | 21 → 13 | 0.0 / 0.7 |
| 08 | identical | 40 → 41 | 40 → 17 | 0.0 / 0.2 |
| 09 | REFUSED, untouched (see below) | - | 30 → 30 | - |
| 10 | identical | 41 → 48 | 41 → 20 | 0.0 / 0.2 |
| 11 | identical | 46 → 54 | 46 → 21 | 0.0 / 0.1 |
| 12 | identical | 25 → 30 | 25 → 12 | 0.0 / 0.1 |
| 13 | identical | 41 → 45 | 41 → 19 | 0.0 / 0.2 |
| 14 | identical | 56 → 65 | 56 → 32 | 0.0 / 0.4 |
| 15 | REFUSED, untouched (see below) | - | 33 → 33 | - |
| 16 | -24f (-1.00s, repeated take out) | 49 → 55 | 49 → 27 | 0.0 / 0.3 |
| 17 | REFUSED, untouched (see below) | - | 68 → 68 | - |
| 18 | -29f (-1.21s, repeated take out) | 52 → 59 | 52 → 31 | 0.0 / 0.2 |
| 19 | -3f (-0.12s, repeated "Yeah" out) | 55 → 64 | 55 → 34 | 0.0 / 0.3 |

The five picture cuts land on the predicted frames exactly (01: -13,
03: -4, 16: -24, 18: -29, 19: -3). The three sub-half-second shifts
(02: +3, 06: +11, 07: +1) are the boundary snap settling word edges,
not content changes. Cards regrouped everywhere and run marginally
shorter than before (medians 0.99-1.34s vs 1.11-1.79s; maxes 1.77-3.15s
vs 2.09-3.67s). "Cards old" counts the old per-card overlay files, which
each carry one card's text; "cards new" counts the build plan's entries,
hash-verified against what was placed.

## The four refusals, and what each needs

All four were refused by the gate, staging discarded, existing timeline
untouched. None is a build defect; each is a decision that sits with
the captain.

- **Reel 05, reel 09 - no call to action (QB-CTA-ABSENT).** Their approved
  plans declare `call_to_action: null`, so the faithful rebuild ends
  without one and the quality bar demands one. Reel 05's earlier caption
  failures (mid-word truncation, ten-second hang) are gone - its lane's
  fix held; what remains is only the missing closer. To unblock either:
  approve a closing passage for the moment, then rebuild that reel alone.
- **Reel 15 - card `concise,` hangs 2.03s past one spoken word.
  Reel 17 - card `five recommendations.` hangs 2.09s past two words
  (F15).** Both are genuine long cards from the new grouping, not
  instrument noise. To unblock either: accept the card, re-cut the
  passage, or rule the hang acceptable - then rebuild that reel alone.

## Coverage: the captain's question, measured

Method, identical to the current-state report: transcript words clipped
to the played keep ranges, aligner-stretched words over 3.0s excluded,
uncovered word-span seconds summed against the placed V3 segments.

- **Straddling speech with no card: 0.0s on all fifteen rebuilt reels.**
- Residue (card-edge rounding dust): 4.1s total over 615.5s played;
  worst single fragment 0.32s (reel 02). Largest reel totals: 07: 0.7s,
  06: 0.6s - both dust, no gap a viewer could notice.
- For comparison the same method on the old timelines gave 21.0s residue
  over 807.8s played ( nineteen, straddling 0.0s there too). Coverage got
  better, nowhere worse.

## The packaging change, in plain language

The caption track used to hold one video file per caption card
(28 files on reel 01, each named for its card's text). It now holds one
file per spoken passage (13 files on reel 01, each named for its block),
and each file plays its passage's two or three cards in turn. The viewer
reads the same cards at the same pace - card counts match the plan
exactly (32 on reel 01, 65 on reel 14) and run slightly shorter than
before. Nothing was lost; the files were consolidated. The card texts
themselves regrouped under the current grouping rules, which is the
polish the rebuild was for.

## Safety proof (done-check output, real)

Before (50 timelines hashed from a copy of Project.db):

```
timelines found: 50
hashed 50 timelines -> hashes_rebuild_before.json
master present: True
reel timelines: 49
```

After (50 timelines hashed from a fresh copy):

```
timelines found: 50
hashed 50 timelines -> hashes_rebuild_after.json
master present: True
reel timelines: 49
```

Diff: 35 identical, 15 changed - exactly the fifteen rebuilt reels, no
others. The master `GEO Podcast - Synced` is byte-identical. The four
refused reels are byte-identical. No timeline added or removed. The live
plan file and the live judgement file were restored byte-identical
(md5-verified) after the runs.

Tests: no repository code was changed by this lane (worktree clean), so
no covering tests exist to run; the full suite is firstmate's per batch.
The gate runs were the verification: fifteen passes, four refusals with
the gate's own reasons, zero overrides, zero widened tolerances.

## How the unblock worked (for the record)

The first pass refused all nineteen on stale model judgements - readings
written against different cuts that quoted lines no build ever placed
(reel 01's quoted closer sits at master ~340s, outside its moment ranges
and absent from all 28 cards the old timeline placed). The stored
judgement was backed up to
`review/reel_judgement_20260908T170314Z.json`, all nineteen moments were
re-read from their own played words through the step's own pre-bridge
and post-bridge (19 kept, 0 refused), the fresh judgement served the
rebuilds, and the live files were restored afterwards with the fresh
judgement kept as `review/reel_judgement_snapshot19_20260908T172700Z.json`.
Reel 05 went last. Build order: 01-04, 06-19, then 05.
