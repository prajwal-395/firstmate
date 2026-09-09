# Reel rebuild run 2026-09-08 R2 - in-place rebuild, stopped after the first reel

Task relaunch with NEW AUTHORITY (firstmate, 2026-09-08): rebuild IN PLACE inside
the `Reels/Current plan` folder only, replacing rather than adding; delete exactly
ONE timeline by name - `Reel 01 - geo-is-comprehension-not-position (rebuild)`,
the stray the previous run created. Everything else protected (Archive, Earlier
plans, Unrecorded, master `GEO Podcast - Synced`, any timeline outside Current plan).
Both defects that stopped R1 are fixed on main (PR 657 segment-granularity F2/F14
pairing, PR 658 verify scopes to built reels). Lane owns no code changes.
Branch: `fm/vep-rebuild-19-reels-run`, rebased onto `origin/main` (`420593c`) first.

## Current plan membership - enumerated BEFORE deleting or replacing anything

Read off the live project `Podcast (field test)` (50 timelines) via the scripting
API, read-only. Current plan held 21 timelines: the 20 `(harvest)` reels plus the
1 `(rebuild)` stray. Earlier plans 20, Unrecorded 8, Root 1 (master).

```
30 | Reel 01 - geo-is-comprehension-not-position (harvest)
31 | Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest)
32 | Reel 07 - number-one-on-google-invisible-to-ai (harvest)
33 | Reel 09 - your-website-is-only-20-percent (harvest)
34 | Reel 10 - the-website-wasnt-broken-everything-else (harvest)
35 | Reel 12 - ai-isnt-making-things-up (harvest)
36 | Reel 13 - the-accounting-firm-ai-called-healthcare (harvest)
37 | Reel 15 - the-3d-nail-art-salon-beats-the-chains (harvest)
38 | Reel 16 - why-ai-trusts-one-brand-over-another (harvest)
39 | Reel 17 - the-first-step-is-seeing-how-ai-sees-you (harvest)
40 | Reel 18 - what-hallucinating-actually-means (harvest)
41 | Reel 20 - your-competitor-is-ranking-nine-times-mo (harvest)
42 | Reel 21 - my-website-is-brand-new-why-isnt-that-en (harvest)
43 | Reel 23 - why-small-business-wins-on-ai (harvest)
44 | Reel 24 - why-ai-trusts-youtube (harvest)
45 | Reel 25 - what-content-ai-actually-rewards (harvest)
46 | Reel 27 - google-reviews-build-ai-trust (harvest)
47 | Reel 28 - the-nail-salon-query-google-cant-answer (harvest)
48 | Reel 29 - if-youre-not-in-the-four-or-five (harvest)
49 | Reel 30 - your-google-business-profile-and-the-map (harvest)
50 | Reel 01 - geo-is-comprehension-not-position (rebuild)   <-- the one stray
```

Because the folder's reel names carry the ` (harvest)` suffix, "replacing rather
than adding" means building with `--name-suffix " (harvest)"`: `built_name` is
`moment.timeline_name + suffix`, so those 20 targets are exactly the 20 timelines
above. A default (no-suffix) build would have ADDED 20 new timelines beside them.

## The one authorised deletion

Stray had 0 markers (no captain notes to lose). Deleted by exact name only, with
an exact-match guard refusing on anything but precisely one hit:

```
open='Podcast (field test)' timelines_before=50 exact_matches=1
{"before": 50, "after": 49, "stray_remaining": 0}
```

## Reel 1 rebuilt in place

`python3 manage_project.py build-reels geo-podcast --only-reel 1 --name-suffix " (harvest)"`
(with `RESOLVE_SCRIPT_API`/`RESOLVE_SCRIPT_LIB` exported per AGENTS.md 9).

- BUILD node succeeded: `Reel 01 - ... (harvest)` deleted and re-placed
  (plan 1234.2 frames -> 1235 placed, items 4/4, one_frame_holes 0,
  uncaptioned 0.0s, `bad_take_cuts: 1`).
- VERIFY node FAILED the gate (`reels_checked: 1, total_errors: 15` -
  the scoping fix works; only the built reel was graded).
- Per the brief ("if the first one fails, stop"), no further reels were built.

All 15 errors are F2 with delta exactly -1: every placed caption segment is one
frame shorter than planned (e.g. planned 115 / placed 114, 140/139, 93/92...).
The R1 failure mode (34 card-level F2+F14) is gone - PR 657's segment grouping
holds (captions `34/15`, zero F14, zero missing). What remains is a systematic
off-by-one on every segment: a planned-vs-placed frame-count convention
(inclusive vs exclusive), identical on all 15 segments. Content is clean:
no holes, no uncaptioned speech, no short captions, no edge cuts.

Two warnings (not errors): QB-CTA-SHARED (CTA reused across reels 1/8/15/23 -
permitted) and QB-NOT-FOLLOWABLE (model judgement on reel 1's cold open).

## Provenance - caption_content_hash IS present for the rebuilt reel

`plan_provenance.json` (`built_at: 2026-09-08T04:21:50Z`, merged not replaced):

```
Reel 01 - geo-is-comprehension-not-position (harvest) -> v1:830f2f0e39b13f1869fe9d29437902464396583655ad390bf2b05dc9972fad24
```

Same hash as the harvest original derives: current code produces the same caption
plan for reel 1. (The stale `(rebuild)` provenance entry for the deleted stray
remains in the merged record - a record of a timeline that no longer exists.)

## Done-check - real output, read off the Resolve project (not the build log)

```
OPEN PROJECT: Podcast (field test)
TIMELINES BEFORE: 50 AFTER: 49
CURRENT PLAN MEMBERSHIP AFTER (20, all harvest names, reel 1 rebuilt at index 49):
  Reel 05/07/09/10/12/13/15/16/17/18/20/21/23/24/25/27/28/29/30 (harvest) +
  Reel 01 - geo-is-comprehension-not-position (harvest) [REBUILT]
EARLIER PLANS: 20 (intact)  UNRECORDED: 8 (intact)  ROOT: master only
STRAY PRESENT: False
MASTER BEFORE: {"start": 0, "end": 63694, "markers": 59}
MASTER NOW   : {"start": 0, "end": 63694, "markers": 59}
MASTER UNCHANGED: True
```

Reel 1 sits at index 49 because delete-then-recreate appends at the end; its
folder is still `Reels/Current plan` and its name is byte-identical. No
pre-existing timeline outside Current plan was deleted, renamed or modified.

## Counts

Built (replaced in place): 1. Authorised deletion: 1 stray. Skipped (stopped):
19. Differing in cut structure from original: UNKNOWN for certain - the harvest
original was replaced, so no side-by-side remains; the rebuild's own report says
`bad_take_cuts: 1`, i.e. duplicate-take removal fired once on reel 1, which the
captain's hypothesis predicts would tighten the cut. Whether the harvest original
lacked that cut cannot be re-measured now.

## Neighbour note - defect for a code lane (not fixed here)

`check_caption_duration` fails every placed caption segment by exactly one frame
(15/15 on reel 1, all delta -1, planned > placed). Uniform sign and magnitude
across all segments of a correct build points at a frame-counting convention
between the plan (`compile_manifest` card durations) and the placed V3 items,
not at content. Either the plan counts inclusively and the measurement
exclusively, or a rounding boundary shaves one frame per segment. Rebuilding the
remaining 19 reels will fail the same way 19 more times; a 1-frame tolerance or
a convention fix in the verifier (or the placer, if the frame is truly missing)
unblocks the batch. This lane changed no code.

## Final accounting - fix-first stop (no further reels built)

Firstmate ruling: fix-first; remaining 19 reels NOT rebuilt. Worktree untouched
since the R2 report commit except for this section. Final live read below.

### Full timeline list BEFORE (50) and AFTER (49)

BEFORE (read-only probe, open project `Podcast (field test)`):

```
 1 | Root                  | GEO Podcast - Synced
 2 | Earlier plans         | Reel 01 - seo-ranks-geo-understands
 3 | Earlier plans         | Reel 02 - seo-that-hurts-your-ai-ranking
 4 | Earlier plans         | Reel 03 - search-didnt-change-the-question-did
 5 | Earlier plans         | Reel 04 - consistency-beats-size
 6 | Earlier plans         | Reel 05 - the-audit-that-was-eye-opening
 7 | Earlier plans         | Reel 06 - where-ai-is-reading-you
 8 | Earlier plans         | Reel 07 - website-is-resume
 9 | Earlier plans         | Reel 08 - why-ai-trusts-a-cited-brand
10 | Earlier plans         | Reel 09 - first-step-is-understanding
11 | Earlier plans         | Reel 10 - the-seven-modules
12 | Earlier plans         | Reel 11 - what-hallucinating-actually-means
13 | Earlier plans         | Reel 12 - not-a-content-problem
14 | Earlier plans         | Reel 13 - a-score-is-not-a-fix
15 | Earlier plans         | Reel 14 - small-business-beats-the-behemoths
16 | Earlier plans         | Reel 15 - why-youtube-outranks-other-video
17 | Earlier plans         | Reel 16 - how-people-actually-search-now
18 | Earlier plans         | Reel 17 - four-slots-and-nothing-else
19 | Earlier plans         | Reel 18 - your-google-business-profile
20 | Earlier plans         | Reel 19 - can-you-game-ai
21 | Unrecorded            | Reel 03 - search-didnt-change-the-question-did (pipeline rebuild)
22 | Unrecorded            | Reel 03 - search-didnt-change-the-question-did (whole-take rebuild)
23 | Unrecorded            | Reel 20 - search-didnt-change-the-question-did (selector redraw)
24 | Earlier plans         | Reel 21 - google-gave-a-2023-list-chatgpt-gave-rea
25 | Unrecorded            | Reel 22 - search-engine-versus-decision-engine (clean and complete)
26 | Unrecorded            | Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection)
27 | Unrecorded            | Reel 24 - keyword-stuffing-flagged-as-thin-content (fragment fix)
28 | Unrecorded            | Reel 25 - healthcare-company-that-sells-accounting (fragment fix)
29 | Unrecorded            | Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (fragment fix)
30 | Current plan          | Reel 01 - geo-is-comprehension-not-position (harvest)
31 | Current plan          | Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest)
32 | Current plan          | Reel 07 - number-one-on-google-invisible-to-ai (harvest)
33 | Current plan          | Reel 09 - your-website-is-only-20-percent (harvest)
34 | Current plan          | Reel 10 - the-website-wasnt-broken-everything-else (harvest)
35 | Current plan          | Reel 12 - ai-isnt-making-things-up (harvest)
36 | Current plan          | Reel 13 - the-accounting-firm-ai-called-healthcare (harvest)
37 | Current plan          | Reel 15 - the-3d-nail-art-salon-beats-the-chains (harvest)
38 | Current plan          | Reel 16 - why-ai-trusts-one-brand-over-another (harvest)
39 | Current plan          | Reel 17 - the-first-step-is-seeing-how-ai-sees-you (harvest)
40 | Current plan          | Reel 18 - what-hallucinating-actually-means (harvest)
41 | Current plan          | Reel 20 - your-competitor-is-ranking-nine-times-mo (harvest)
42 | Current plan          | Reel 21 - my-website-is-brand-new-why-isnt-that-en (harvest)
43 | Current plan          | Reel 23 - why-small-business-wins-on-ai (harvest)
44 | Current plan          | Reel 24 - why-ai-trusts-youtube (harvest)
45 | Current plan          | Reel 25 - what-content-ai-actually-rewards (harvest)
46 | Current plan          | Reel 27 - google-reviews-build-ai-trust (harvest)
47 | Current plan          | Reel 28 - the-nail-salon-query-google-cant-answer (harvest)
48 | Current plan          | Reel 29 - if-youre-not-in-the-four-or-five (harvest)
49 | Current plan          | Reel 30 - your-google-business-profile-and-the-map (harvest)
50 | Current plan          | Reel 01 - geo-is-comprehension-not-position (rebuild)   <-- stray
```

AFTER (final live read, 49 timelines): identical except the stray (index 50) is
gone and the rebuilt `Reel 01 - ... (harvest)` sits at index 49 (delete-then-
recreate appends at the end; folder still `Reels/Current plan`, name
byte-identical). Every other name at every other index is unchanged.

### Stray gone

No timeline whose name contains `(rebuild)` exists among the 49. One leftover:
an EMPTY media-pool bin `Reel subtitles/Reel 01 - ... (rebuild)` holding 0 clips
(the organiser files captions per reel and never deletes bins, by design). No
timeline, no clips, nothing placed. Left in place - deleting anything further is
outside this lane's authority.

### Archive / Earlier plans / master unchanged

- `Master/Archive` bin: present, 0 clips - never touched (this lane's only
  writes were `DeleteTimelines` on the exact stray name and the reel-1
  build's own delete-then-place of its target).
- Earlier plans: 20 timelines, same names as before. Unrecorded: 8, same.
  `Source footage`: 10 clips. Master at Root.
- Master `GEO Podcast - Synced`: start 0, end 63694, markers 59 - identical
  before and after.

### Example caption segment - placer vs verifier frames

Block 0 of rebuilt reel 1 (3 cards, "okay so i'm hearing just from..."):

- Plan (`.../steps/4_05_render_subtitles/sub_reel-01-geo-is-comprehension-not_craig_0_95485-100264_9dad0a52_props.json`):
  cards span overlay-local frames 0 -> 115; `_timeline_end: 4.779` s;
  `_source_out_frame: 115`. Source audio span 95485-100264 ms = 4779 ms =
  114.696 frames at 24 fps.
- Placer wrote: V3 item at timeline frames **0-114 (dur 114)**.
- Verifier expected: **115 frames** -> F2 "planned 115 frames, placed 114
  (delta -1)".

So the plan rounds 114.696 up to 115 while the placed item carries 114
(truncation). The same -1 repeats on all 15 segments. Evidence for the fix
lane: fractional-frame rounding at the plan/placer boundary, not content.
