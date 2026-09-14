# PR 1107 live-Resolve demo: what the scratch project actually returned

Two-round census of `library/tools/comparison_retirement.py` (PR 1107,
head `9077c7e`, byte-identical to the file exercised here) against a REAL
running DaVinci Resolve on the scratch project, 2026-09-14. The PR's own
census demo used fake Resolve objects; this is the real-object evidence
firstmate held the merge for.

## Verdict for the merge decision

**Do not merge as-is.** The deletion path reports timelines "collected"
that Resolve never deleted: `_collect_archived` ignores the return value
of `pool.DeleteTimelines` (`comparison_retirement.py:617`), so a falsy
delete reads as success. The demo measured exactly that: round three's
report lists `FM1107-DEMO Reel A (take-1 comparison) (archived round 001)`
under `collected`, and the immediate census off
`GetTimelineByIndex`/`GetName` still shows it present. The inherited
pattern has the same hole (`reel_retirement.collect_superseded`,
`reel_retirement.py:308`). Everything else the demo touched worked,
including both refusal halves.

## The window and the boundary

- Brief names `sep_scratch`; no project of that exact name exists in the
  open database (`poetic`). Open and untouched by the captain was
  `SEP_SCRATCH`, a scratch project holding `SEP_P2_p220`, `SEP_P2_p480`,
  `SEP_P2_pfull`, `SEP_P3_zoom`. The demo ran there and nowhere else.
- The captain's `Podcast (field test)` was not open; the 2026-09-13
  refusal condition no longer held.
- The demo created 8 throwaway timelines (`FM1107-DEMO…`, `FM1107-STALE…`),
  touched no pre-existing timeline, restored the open timeline to
  `SEP_P2_pfull`, and deleted every timeline it made. Final census is
  exactly the 4 pre-existing names. Leftover: empty bins `05 - Reels` /
  `Archive` (Resolve offers no folder delete; the retire path created the
  first, the follow-up probe the second).
- Harness-supplied, as in the PR's own demo: plan finals
  (`FM1107-DEMO Reel A/B`) and the version record. Every Resolve call and
  every other file read (holds, sign-offs, variants) was real.
- Guard functions exercised are identical on HEAD and the PR branch
  (`timelines_to_replace`, `assert_deletion_scope` - diffed, no drift).

## Round by round, what Resolve returned

**Round 1** - created base reels plus `(take-1 comparison)` each, drove
`collect_for_bases` with round 1 recorded. `retired: {}`, `collected: []`,
`refused: ""`. Census: 8 timelines, both comparisons kept as newest.
`CreateEmptyTimeline` + `GetName` answered every name back exactly.

**Round 2** - created `(take-2 comparison)` for reel A, drove with rounds
1-2 recorded. `retired: {take-1: take-1 (archived round 001)}`,
`collected: []`, `refused: ""`. Census: the live take-1 name gone, the
archived name present - the rename half works on real objects. BUT
`unfiled: [take-1 (archived round 001)]`: `MoveClips` into the archive
failed and `_archive_folder` left only `05 - Reels` behind (the `Archive`
level was created later by the follow-up probe, through fresh handles).

**Guard refusal (real objects, the load-bearing half)** - both forms
refused with nothing deleted:
- Direct: `assert_deletion_scope([live take-2 object], {archived name})`
  raised `ReelBuildError: REFUSING to build: it would delete 1
  timeline(s) this build never planned to place - ['FM1107-DEMO Reel A
  (take-2 comparison)']…`.
- Wired: the fake-test's smuggle (`timelines_to_replace` returning the
  live object inside the delete set) into `comp._collect_archived`
  raised the same refusal. Census after: both timelines still present.

**Round 3** - created `(take-3 comparison)`, drove with rounds 1-3.
`retired: {take-2: take-2 (archived round 002)}`,
`collected: [take-1 (archived round 001)]`, `refused: ""` - and the
census still lists take-1's archived timeline. `DeleteTimelines`
returned falsy and the report kept the name under `collected` anyway.
The demo's own cleanup delete of 6 timelines failed the same silent way
through the same pool handle. Fresh-handle deletes (3 probes + final
cleanup, each `True`) removed everything. Side confirmation, measured
twice: a really-deleted timeline answers `GetName()` with `None`, as the
code comment in `_collect_archived` claims.

## What this means (and what it does not)

- The defect is the unchecked return, not the census logic: planning,
  retiring, scoping and both refusals behaved on real objects exactly as
  the fake demo said. The fix is one judgement - read
  `DeleteTimelines`' answer and refuse/report on falsy (the repo's own
  AGENTS.md 5 rule) - in `_collect_archived` and, inherited, in
  `collect_superseded`. Not repaired here: report, do not repair.
- Suspected but NOT proven: the falsy deletes followed a bin-tree
  mutation (`AddSubFolder`) through the same pool handle
  (`promote_staged_reels` holds one handle across the whole promote,
  `reel_build.py:6534`, so this would reach production, not just the
  demo). A controlled A/B (old handle vs fresh handle across move/delete
  mutations, no bin creation) REFUTED simple handle age: both deleted
  `True`. Bin-creation poisoning was deliberately left untested - proving
  it costs another un-removable bin. Mechanism undetermined; the
  unchecked return is the finding either way, because whatever makes a
  delete fail, the report must not call it collected.
- Raw per-call evidence: `/tmp/demo1107/evidence.json` (outside the repo,
  per scaffolding rules); scripts `/tmp/demo1107/*.py`.
