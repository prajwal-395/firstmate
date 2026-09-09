# Reel rebuild run 2026-09-08 R3 - resumed on fixed main, stopped after reel 5's plan defects

Authority (firstmate, 2026-09-08 04:45Z): rebuild IN PLACE inside `Reels/Current plan`
only, replacing; nothing else (stray already deleted in R2). All three blockers fixed
on main - PR 657 (F2/F14 segment granularity), PR 658 (verify scopes to built reels),
PR 662 (caption fps convention). Branch rebased onto `origin/main` `52d91e0` first.
Lane owns no code changes. One reel at a time; report after the first before the second.

## Current plan membership - enumerated BEFORE replacing anything (live read)

Open project `Podcast (field test)`, 49 timelines. Current plan held exactly the 20
`(harvest)` reels (no stray - R2 deleted it). Earlier plans 20, Unrecorded 8, root master.

```
Reel 01 - geo-is-comprehension-not-position (harvest)
Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest)
Reel 07 - number-one-on-google-invisible-to-ai (harvest)
Reel 09 - your-website-is-only-20-percent (harvest)
Reel 10 - the-website-wasnt-broken-everything-else (harvest)
Reel 12 - ai-isnt-making-things-up (harvest)
Reel 13 - the-accounting-firm-ai-called-healthcare (harvest)
Reel 15 - the-3d-nail-art-salon-beats-the-chains (harvest)
Reel 16 - why-ai-trusts-one-brand-over-another (harvest)
Reel 17 - the-first-step-is-seeing-how-ai-sees-you (harvest)
Reel 18 - what-hallucinating-actually-means (harvest)
Reel 20 - your-competitor-is-ranking-nine-times-mo (harvest)
Reel 21 - my-website-is-brand-new-why-isnt-that-en (harvest)
Reel 23 - why-small-business-wins-on-ai (harvest)
Reel 24 - why-ai-trusts-youtube (harvest)
Reel 25 - what-content-ai-actually-rewards (harvest)
Reel 27 - google-reviews-build-ai-trust (harvest)
Reel 28 - the-nail-salon-query-google-cant-answer (harvest)
Reel 29 - if-youre-not-in-the-four-or-five (harvest)
Reel 30 - your-google-business-profile-and-the-map (harvest)
```

Master fingerprinted before: `start=0 end=63694 markers=59`.

## Reel 1 - rebuilt CLEAN (the first-reel gate: pass, so the batch proceeded)

`python3 manage_project.py build-reels geo-podcast --only-reel 1 --name-suffix " (harvest)"`

- BUILD replaced `Reel 01 - ... (harvest)` in place (15 caption segments rendered OK).
- VERIFY: `Reel 01 - geo-is-comprehension-not-position (harvest): ok (0 errors, 0 warnings)`.
  Grade row: `plan 51.48s expF 1234.2 actF 1235 items 4/4 1f-holes 0 caps 34/15
  uncap 0.0`. The R2 systematic off-by-one (15xF2 delta -1) is GONE - PR 662 holds.
- 2 plan-quality warnings only (QB-CTA-SHARED, QB-NOT-FOLLOWABLE), both permitted.
- caption_content_hash PRESENT (see provenance below).

## Reel 5 - placed, VERIFY FAILED on two plan defects (batch stopped here)

`python3 manage_project.py build-reels geo-podcast --only-reel 5 --name-suffix " (harvest)"`
(open project re-confirmed `Podcast (field test)` immediately before the build)

- BUILD replaced `Reel 05 - ... (harvest)` in place (21 caption segments rendered OK,
  3 mid-sentence rows rejoined). Placement itself clean: `items 4/4, 1f-holes 0,
  caps 55/21, uncap 0.0`.
- VERIFY: `FAIL (2 errors, 0 warnings)` - both PLANNING findings, not placement:

```
[ERROR] F17 Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest): caption card
        'recommend you or your brand.' mixes speakers: Akshita, Craig
[ERROR] F8  Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest): END at 413.85s
        cuts Craig mid-speech, through the word 'about' (412.77-414.03s, 1.26s long),
        in row 412.77-414.03s "about"
```

- The build exits 1 AFTER replacing the timeline, so Reel 05 now carries the new
  (gate-failing) content at index 49. Reels 7-30 were NOT touched (script stopped).
- caption_content_hash PRESENT for reel 5 (see provenance below) - the hash covers
  the caption plan, which the gate still refuses on F17/F8.

## Why stopped rather than continuing through 18 more

Continuing would delete-and-replace each remaining Current-plan timeline with new
content that may carry its own F8/F17-class plan defects, converting up to 18
currently-live timelines into verify-failed ones with no code/content lane fix in
hand. Reel 5's failures look like genuine plan defects (mixed-speaker card under
the new single-speaker spine; an end boundary through a word), not a verifier
convention bug - the placement row is perfect. That decision belongs above this
lane, so the sequenced run stopped with 18 untouched.

## Done-check - real output, read off the Resolve project (not the build log)

BEFORE (read-only probe, open project `Podcast (field test)`):

```
TIMELINE COUNT: 49
MASTER: start=0 end=63694 markers=59
Current plan: 20 harvest (list above)
```

AFTER reel 1 (clean) + reel 5 (placed, gate-failed):

```
OPEN PROJECT: Podcast (field test)
TIMELINE COUNT: 49
 1 | GEO Podcast - Synced
 2..21 | Earlier plans (20, intact)
 22..29 | Unrecorded (8, intact)
 30..47 | Current plan harvest reels 07/09/10/12/13/15/16/17/18/20/21/23/24/25/27/28/29/30 (18 untouched, in place)
 48 | Reel 01 - geo-is-comprehension-not-position (harvest) [REBUILT CLEAN]
 49 | Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest) [REBUILT, VERIFY FAILED F17+F8]
MASTER: start=0 end=63694 markers=59
MASTER UNCHANGED: True
```

No timeline added, none deleted, none renamed. Every original name still exists -
the two rebuilt names are byte-identical and still file under `Reels/Current plan`.
Earlier plans (20), Unrecorded (8) and the root master untouched. Repair bins only
FILED caption renders under `Reel subtitles/...`; nothing deleted.

Provenance (`pipeline_output/review/plan_provenance.json`,
`built_at: 2026-09-08T04:54:42Z`, merged not replaced):

```
Reel 01 - geo-is-comprehension-not-position (harvest) -> v1:830f2f0e39b13f1869fe9d29437902464396583655ad390bf2b05dc9972fad24
Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest) -> v1:778425e83cfea359f6a79ee645f9545f379ed0227c9d8f125f9a6320f12fa8f5
```

(`built_reels` still lists 21 names including the R1 `(rebuild)` stray's stale entry -
a record of a timeline that no longer exists; the live project has 49 timelines.)

## Counts

- Built clean and passing: 1 (Reel 01).
- Built but gate-failing: 1 (Reel 05 - F17 mixed-speaker card, F8 end cuts mid-word).
- Skipped (stopped, untouched): 18 (reels 07/09/10/12/13/15/16/17/18/20/21/23/24/25/27/28/29/30).
- Differing in cut structure from original: Reel 01's rebuild report carries its own
  `bad_take_cuts` count in the build log; Reel 05's grade row is above. Side-by-side
  against the replaced harvest originals is not re-measurable (replaced, not copied).

## Neighbour note - defect for a code/content lane (not fixed here)

Reel 05's proposal (`reel_proposals_v2.json`, moment 5) ends at 413.85s through the
word 'about' (412.77-414.03s) and groups 'recommend you or your brand.' across
Akshita+Craig into one card. Under the current pipeline (single-speaker spine blocks,
per-speaker styling) both are errors. Fix is a redraw of moment 5's end boundary
past the word and a re-group of that card - a plan/content fix, not a verifier
tolerance change. Until it lands, rebuilding reel 5 (or any reel with the same shape)
fails the same way. This lane changed no code.
