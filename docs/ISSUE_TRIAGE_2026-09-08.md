# Issue backlog triage, 2026-09-08

28 open issues reviewed against what actually landed. Nothing deleted.
No issue closed by hand except where noted; no comments posted on any
issue (fleet comments wake firstmate - see `fm-tracker.sh` header).

## Tracker-owned tickets (22): NOT TOUCHED

Every issue below carries an `<!-- fm-task: ... -->` marker and an
`fm:task` label: it mirrors a firstmate working-queue row. The tracker's
`sync` re-opens/re-bodies these on every dispatch while the task row
exists, and `complete` closes them on teardown - closing one by hand
fights that contract and risks a reopen. Proof the mechanism works:
#729 closed itself mid-triage. Assessments only; the tracker closes.

| # | Assessment | One-line reason |
|---|-----------|-----------------|
| 176 | live (tracker) | Variant generation outstanding; needs captain choices. Blocked by nothing; blocks #177. |
| 177 | live (tracker) | Hand-cut reference outstanding; blocked by #176. |
| 317 | live (tracker) | On hold; waiting on the captain to choose a clip. |
| 365 | live (tracker) | Panel region-marking work outstanding. |
| 375 | live (tracker) | Re-plan on a real API backend outstanding. |
| 381 | live (tracker) | Affect-capability probe outstanding. |
| 473 | appears done (tracker to close) | AGENTS.md is ~53k chars vs the 150k limit the issue names; the 09-01 condensation dissolved the premise. Verified `CEILING = 53084` in `scripts/check_agents_md_size.py`. |
| 505 | live (tracker) | GEO field test outstanding. |
| 523 | live (tracker) | Reel-moment evidence investigation outstanding. |
| 633 | live (tracker) | On hold; captain's own checkout fix needs the captain. |
| 638 | live (tracker) | On hold; dated-audit recheck. |
| 639 | live (tracker) | Batch integration gate outstanding. |
| 660 | appears done (tracker to close) | #715 "Rebuild the captain's nineteen reels in place - run report" merged (commit `aa748f4`); appears to answer it. Tracker confirms/closes. |
| 665 | live (tracker) | Animated reel over existing spine outstanding. |
| 678 | live (tracker) | Channel-brief series routing outstanding. |
| 682 | live (tracker) | Rebuild run-2; earlier than run-3 #693, possible supersession - tracker's call, not closed by title match. |
| 686 | live (tracker) | Stored-proposal mid-word data; #735 refuses keep-range mid-word edges (different layer, does not name stored proposals) - NOT evidence. |
| 693 | live (tracker) | Rebuild run-3 outstanding. |
| 706 | live (tracker) | Blended conform samples outstanding; ends in a captain choice. |
| 716 | appears done (tracker to close) | #722 "File refused reel caption imports instead of stranding them" merged (commit `600e6d3`) from branch `vep-reel-bins-and-subtitle-strays` - the exact task id in #716's marker. Tracker confirms/closes. |
| 727 | live (tracker) | Vox semantic visuals; today's animation merges are audio-driven text, not visuals chosen from what is said. |
| 729 | DONE (tracker closed it) | Fixed by #730, commit `996be6a` "Declare overlay_mode.py in scan's step-ledger shared-code row". Verified `library/tools/overlay_mode.py` has exactly one ledger commit and the issue reads `state: closed`. |

## Hand-written issues (6): triaged, all stay open

| # | Outcome | One-line reason |
|---|---------|-----------------|
| 156 | STILL LIVE | Destination unmet: the captain has not posted an untouched pipeline output. |
| 157 | STILL LIVE | Its own resolution condition - measured evidence that framed decisions beat unframed ones at same step/same input, "not an argument" - is unmet; #463 built the mechanism but no such comparison exists. Also captain-led per its body. |
| 158 | STILL LIVE | Resolves on the captain's verdict on an all-seven-clearing output; no such verdict exists. Waiting on captain + walkthrough. |
| 163 | STILL LIVE | No depth-estimation code in the repo (grep: no MiDaS/DepthAnything/depth-map); future scope, behind the craft sequence. |
| 164 | STILL LIVE | No Higgsfield code in the repo; future scope, behind the craft sequence. |
| 169 | STILL LIVE | Strategic reframing for the captain; nothing in the repo addresses it; explicitly not ahead of #156. Waiting on captain. |

(#736 is this task's own tracker ticket; it closes on teardown.)

## Rejected closures (title-match traps avoided)

- #686 is NOT answered by #735: mid-word keep edges (R07/R02) vs stored proposals predating the boundary snap are different layers.
- #682 is NOT closed as superseded by #693 on titles alone: run-2/run-3 ordering suggests it, but the tracker owns both rows.
- #157 is NOT closed by #463: the merge is verified real (`7a7138d`, `craft_role.py` prepended for every model-reaching step at `run_pipeline.py:1727`, pinned by `tests/test_craft_role.py`) but the issue demands decision-quality evidence, not the mechanism.

## Counts

DONE 1 (by tracker) / SUPERSEDED 0 / STILL LIVE 27 / NO LONGER MATTERS 0.
Waiting on the captain explicitly: #158, #317, #633, #706 (+ #156 destination, #169 strategy, #176/#177 craft sequence need them).
