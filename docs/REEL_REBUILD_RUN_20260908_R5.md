# Reel rebuild run 2026-09-08 R5 - full batch on fixed main: 17 clean, 3 new failures held by gate

Authority (firstmate brief `vep-rebuild-reels-run-3`): rebuild IN PLACE inside
`Reels/Current plan` only, one reel at a time, whole set without stopping for
permission; record failures and CONTINUE. Protected: `Archive`, `Earlier plans`,
every older generation, master `GEO Podcast - Synced`, anything outside Current
plan. Base: `origin/main` `a229910` (PRs 687, 691, 692 present, verified by
`git rev-parse`). Lane owns no code changes. No PR, local-only branch
`fm/vep-rebuild-reels-run-3`.

## BEFORE - read off the live Resolve project (not the build log)

Open project `Podcast (field test)`, `TIMELINE COUNT: 50`.
`MASTER: GEO Podcast - Synced start=0 end=63694 markers=59`.
`Archive`: 0 clips. `Earlier plans`: 20 timelines. `Unrecorded`: 8 timelines.
`Reels/Current plan` held 21 timelines - the 20 `(harvest)` reels below plus one
unsuffixed `Reel 01 - geo-is-comprehension-not-position` (origin unknown, NOT in
this lane's authority, left untouched):

```
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
Reel 01 - geo-is-comprehension-not-position (harvest)
Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest)
```

Setup note: `build-reels` REFUSED without `RESOLVE_SCRIPT_API` /
`RESOLVE_SCRIPT_LIB` in the environment (AGENTS.md 9). Exported to
`.../Developer/Scripting` and the existing `.../Fusion/fusionscript.so`
(environment-only, no code touched). Command per reel:
`python3 manage_project.py build-reels geo-podcast --only-reel N --name-suffix " (harvest)"`,
each polled to completion inside its own turn - no parking on render subprocesses.

## Per-reel results (17 pass, 3 fail, 0 skipped)

| Reel | Result | Note |
|---|---|---|
| 01 geo-is-comprehension | PASS 0 errors | 2 plan-quality warns (permitted) |
| 05 ai-cant-form-a-clear-picture | PASS 0 errors | R4's F8 GONE - PR 687 snap holds; hash now v1:ae05... (was v1:7784...) |
| 07 number-one-on-google | PASS 0 errors | R2/R4's F2 GONE - PR 691 rounding holds |
| 09 your-website-is-only-20-percent | PASS 0 errors | 4 permitted warns |
| 10 the-website-wasnt-broken | PASS 0 errors | F5 GONE - PR 692 stretched-word rule holds |
| 12 ai-isnt-making-things-up | PASS 0 errors | |
| 13 the-accounting-firm | FAIL (held) | NEW: F4 plan 1902f vs timeline 1903f (+1f) - refused, not promoted |
| 15 the-3d-nail-art-salon | FAIL (held) | NEW: caption seg 5 planned at 14.93s never placed; cards overlap 43f (1.79s) 'for small businesses google re' / 'google rewards' |
| 16 why-ai-trusts-one-brand | PASS 0 errors | |
| 17 the-first-step | PASS 0 errors | |
| 18 what-hallucinating | PASS 0 errors | |
| 20 your-competitor | PASS 0 errors | |
| 21 my-website-is-brand-new | PASS 0 errors | |
| 23 why-small-business-wins | PASS 0 errors | |
| 24 why-ai-trusts-youtube | FAIL (held) | NEW: card 'concise,' hangs 2.03s past speech (limit 1.0s) |
| 25 what-content-ai-rewards | PASS 0 errors | |
| 27 google-reviews | PASS 0 errors | |
| 28 the-nail-salon-query | PASS 0 errors | |
| 29 if-youre-not-in-the-four-or-five | PASS 0 errors | |
| 30 your-google-business-profile | PASS 0 errors | |

All three failures are first-seen failure modes (none matches the F8/F2/F5 the
three PRs fixed). PR 673 held every one: after each failure a live re-read
showed 50 timelines, no `(rebuild staging)` / `(pre-rebuild backup)` timeline
left behind, the failing reel's approved timeline unreplaced, master unchanged.

## AFTER - real output, read off the Resolve project (not the build log)

```
OPEN PROJECT: Podcast (field test)
TIMELINE COUNT: 50
1 GEO Podcast - Synced
2 Reel 01 - seo-ranks-geo-understands
3 Reel 02 - seo-that-hurts-your-ai-ranking
4 Reel 03 - search-didnt-change-the-question-did
5 Reel 04 - consistency-beats-size
6 Reel 05 - the-audit-that-was-eye-opening
7 Reel 06 - where-ai-is-reading-you
8 Reel 07 - website-is-resume
9 Reel 08 - why-ai-trusts-a-cited-brand
10 Reel 09 - first-step-is-understanding
11 Reel 10 - the-seven-modules
12 Reel 11 - what-hallucinating-actually-means
13 Reel 12 - not-a-content-problem
14 Reel 13 - a-score-is-not-a-fix
15 Reel 14 - small-business-beats-the-behemoths
16 Reel 15 - why-youtube-outranks-other-video
17 Reel 16 - how-people-actually-search-now
18 Reel 17 - four-slots-and-nothing-else
19 Reel 18 - your-google-business-profile
20 Reel 19 - can-you-game-ai
21 Reel 03 - search-didnt-change-the-question-did (pipeline rebuild)
22 Reel 03 - search-didnt-change-the-question-did (whole-take rebuild)
23 Reel 20 - search-didnt-change-the-question-did (selector redraw)
24 Reel 21 - google-gave-a-2023-list-chatgpt-gave-rea
25 Reel 22 - search-engine-versus-decision-engine (clean and complete)
26 Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (free selection)
27 Reel 24 - keyword-stuffing-flagged-as-thin-content (fragment fix)
28 Reel 25 - healthcare-company-that-sells-accounting (fragment fix)
29 Reel 23 - keyword-stuffing-is-hurting-your-ai-visi (fragment fix)
30 Reel 13 - the-accounting-firm-ai-called-healthcare (harvest)
31 Reel 15 - the-3d-nail-art-salon-beats-the-chains (harvest)
32 Reel 24 - why-ai-trusts-youtube (harvest)
33 Reel 01 - geo-is-comprehension-not-position
34 Reel 01 - geo-is-comprehension-not-position (harvest)
35 Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest)
36 Reel 07 - number-one-on-google-invisible-to-ai (harvest)
37 Reel 09 - your-website-is-only-20-percent (harvest)
38 Reel 10 - the-website-wasnt-broken-everything-else (harvest)
39 Reel 12 - ai-isnt-making-things-up (harvest)
40 Reel 16 - why-ai-trusts-one-brand-over-another (harvest)
41 Reel 17 - the-first-step-is-seeing-how-ai-sees-you (harvest)
42 Reel 18 - what-hallucinating-actually-means (harvest)
43 Reel 20 - your-competitor-is-ranking-nine-times-mo (harvest)
44 Reel 21 - my-website-is-brand-new-why-isnt-that-en (harvest)
45 Reel 23 - why-small-business-wins-on-ai (harvest)
46 Reel 25 - what-content-ai-actually-rewards (harvest)
47 Reel 27 - google-reviews-build-ai-trust (harvest)
48 Reel 28 - the-nail-salon-query-google-cant-answer (harvest)
49 Reel 29 - if-youre-not-in-the-four-or-five (harvest)
50 Reel 30 - your-google-business-profile-and-the-map (harvest)
MASTER: GEO Podcast - Synced start=0 end=63694 markers=59
```

(Index order shifted because each in-place rebuild deletes and recreates its
timeline, which lands at the end; every original name still exists, none added,
none deleted, none renamed.)

Confirmation: Earlier plans intact (indices 2-20 plus 24 = 20 timelines),
Unrecorded intact (21-23, 25-29 = 8), Archive still empty, master
`start=0 end=63694 markers=59` byte-identical to BEFORE, unsuffixed Reel 01
stray at 33 untouched. `plan_provenance.json` (`built_at 2026-09-08T08:17:00Z`)
carries `caption_content_hash` for all 20 harvest reels, e.g. reel 01
`v1:830f2f0e39b13f1`, reel 05 `v1:ae053bdecd0faa8`, reel 30
`v1:5758ad333529d2a`.

## Neighbour notes for code/content lanes (no code touched here)

1. Reel 13 F4: re-derived plan lays 1902 frames, timeline carries 1903 (+1f,
   +0.04s). One-frame excess at placement, same family as the R2 convention bug
   but the builder was already corrected by PR 662 - worth a look.
2. Reel 15: caption segment 5 (block 4, 'google rewards') planned but never
   placed, plus a 43-frame card overlap on the same cards - planner/placer
   disagreement, possibly adjacent to PR 691's rounding.
3. Reel 24: single-word card 'concise,' outlives its speech by 2.03s - hang
   limit 1.0s trips on a short word with a long tail.
4. Media-pool hygiene (captain's call, nothing deleted): each rebuild files
   displaced caption assets under `Reel subtitles/Not placed on any timeline`
   (now ~2.3 GiB, 148+ files still on disk, 21 files also used by placed items).
   A leftover asset bin `Reel 01 - ... (rebuild)` from the R1 ADD-ONLY run also
   remains under `Reel subtitles` - bins only, no timelines.
