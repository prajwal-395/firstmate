# Reel rebuild run 2026-09-08 R4 - resumed on fixed main, reel 5 staged but gate-failed, stopped

Authority (firstmate, 2026-09-08 06:32Z): rebuild IN PLACE inside `Reels/Current plan`
only; Archive, Earlier plans, older generations and master untouched. Reel 5 holds
gate-failing content from R3 - rebuild it too. Branch merged onto `origin/main`
`c3f1b4c` first (PRs 657, 658, 662, 670, 671, 672, 673, 681). Lane owns no code
changes. One reel at a time; report after the first before the second.

## Current plan membership - enumerated BEFORE building (live read)

Open project `Podcast (field test)`, 49 timelines. Current plan held exactly the 20
`(harvest)` reels; Earlier plans 20 (19 + one Reel 21), Unrecorded 8, root master.
Master fingerprinted before: `start=0 end=63694 markers=59`.

```
Reel 01 - geo-is-comprehension-not-position (harvest)   [R3 rebuilt CLEAN]
Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest) [R3 placed, gate-failed]
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

Setup note: `build-reels` REFUSED at first - `env.resolve_scripting` needs
`RESOLVE_SCRIPT_API` in the environment and this shell did not have it (AGENTS.md 9).
Exported `RESOLVE_SCRIPT_API=".../Developer/Scripting"` and `RESOLVE_SCRIPT_LIB` to
the existing `.../Fusion/fusionscript.so` (no `libfusionscript.dylib` ships with this
Resolve install) and the node ran. Environment-only, no code touched.

## Reel 5 - STAGED, verify FAILED (F8 error), approved timeline NOT replaced

`python3 manage_project.py build-reels geo-podcast --only-reel 5 --name-suffix " (harvest)"`
(open project re-confirmed `Podcast (field test)` immediately before the build)

```
[WARN] F17 ... (rebuild staging): 1 caption card(s) span simultaneous speech by two
       speakers - talk-over or mic bleed, not a turn, so no regrouping separates
       them: 'recommend you or your brand.' (Akshita/Craig)
[ERROR] F8 ... (rebuild staging): END at 413.85s cuts Craig mid-speech, through the
       word 'about' (412.77-414.03s, 1.26s long), in row 412.77-414.03s "about"
[WARN] QB-CTA-SHARED, [WARN] QB-NOT-FOLLOWABLE (plan-quality, both permitted)
FAILED: 1 error(s), 3 warning(s) across 1 reels.
RuntimeError: Reel build produced a defective timeline. Verification failed ...
```

What the fixes did, measured:

- PR 671 (F17 talkover vs turn): CONFIRMED WORKING. The R3 F17 error on
  'recommend you or your brand.' is now a WARN correctly naming simultaneous
  speech, not a sequential turn.
- PR 671 (boundaries snap out of word interiors): DID NOT move this boundary.
  The reel END is still at 413.85s inside the word 'about' (412.77-414.03s).
  See neighbour note.
- PR 673 (stage behind gate, swap in only on pass): CONFIRMED HOLDING. Live
  re-read after the failure: 49 timelines, no `(rebuild staging)` timeline left
  behind, `Reel 05 - ... (harvest)` still in `Reels/Current plan` at index 49,
  master `start=0 end=63694 markers=59` unchanged. The R3 gate-failing content
  in reel 5 was NOT overwritten and no new failing content was promoted.

caption_content_hash for reel 5 is PRESENT in `plan_provenance.json`
(`built_at 2026-09-08T06:41:03Z`):

```
Reel 05 - ai-cant-form-a-clear-picture-of-you (harvest) -> v1:778425e83cfea359f6a79ee645f9545f379ed0227c9d8f125f9a6320f12fa8f5
```

(identical value to R3 - the staged rebuild produced the same caption plan, so
the hash already covered it; the gate still refuses the timeline on F8.)

## Why stopped

Brief rule: first failure stops the batch. The failure is a genuine plan defect
(reel-end boundary through a word), not a verifier convention bug, and the fix
belongs to a plan/content lane. Continuing would stage-and-fail each remaining
reel one at a time at full render cost with no path to promotion for any reel
whose moment shares the shape. 18 reels untouched
(07/09/10/12/13/15/16/17/18/20/21/23/24/25/27/28/29/30).

## Done-check - real output, read off the Resolve project (not the build log)

BEFORE (read-only probe): `OPEN PROJECT: Podcast (field test)`, `TIMELINE COUNT: 49`,
Current plan 20 harvest (list above), `MASTER: start=0 end=63694 markers=59`.

AFTER (read-only probe): identical - 49 timelines, same names at the same indices
(30..47 reels 07-30, 48 reel 01, 49 reel 05), Earlier plans 20 intact, Unrecorded 8
intact, `MASTER: start=0 end=63694 markers=59`, MASTER UNCHANGED. No timeline added,
none deleted, none renamed. Every original name still exists.

## Counts

- Built clean and passing: 0 this run (reel 01's R3 clean rebuild stands).
- Staged but gate-failing, NOT promoted: 1 (Reel 05 - F8 end cuts mid-word).
- Skipped (stopped, untouched): 18.
- Differing in cut structure from original: none this run - nothing was promoted.

## Neighbour note - defect for a code/content lane (not fixed here)

Reel 05's proposal (`reel_proposals_v2.json`, moment 5) ends at 413.85s through the
word 'about' (412.77-414.03s). PR 671 snaps boundaries out of word interiors, yet
the staged rebuild kept END at exactly 413.85s - the snap did not move this
reel-end boundary (it may cover caption cards but not the reel END, or the end is
clamped to the proposal moment). Fix is a redraw of moment 5's end boundary past
414.03s - a plan/content fix. Until it lands, rebuilding reel 5 fails the same way,
and any other moment ending mid-word fails likewise. This lane changed no code.
