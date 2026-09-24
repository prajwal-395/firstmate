"""The version model: one account of what a project has been, and how it is read back.

Six modules used to answer this between them, each with its own git
helper, its own atomic JSON writer and its own reading of "a version".
They are one model, and this package is it.

The model
---------
ONE SUBSTRATE. The project's own git repo, over an allow-list of text
(`store`). Every build commits into it; every record below that must
outlive one disk is written under `pipeline_output/review/`, which the
allow-list versions. The store is the only durable history - the
Resolve project is a derived artefact, rebuilt from declarations, never
merged or restored (captain's ruling 2026-09-10).

ONE UNIT: THE REEL VERSION. What a reel's timeline carried, as the row
snapshot `reel_read.rows_of` returns, with its provenance
(`promoted_at`/`built_at`, `built_with`, and whether it was `stamped`
at the moment or `reconstructed` from the store afterwards). A reel
version is either PROMOTED - it holds the reel's own name - or a
CANDIDATE - a variant built beside it. Both carry the same rows, so ONE
diff (`rounds.diff_reel`, the promotion guard's own
`reel_replace_guard.diff_rows` with the added rows put back) compares
any two of them: round against round, variant against variant.

THREE GROUPINGS of that unit, each answering one question:

- ROUND (`rounds`) - which promoted versions belong together. A round
  is DISCOVERED from the interleaving of the captain's asks and the
  promotions that answered them, never declared.
  `pipeline_output/review/rounds.json`.
- VARIANT (`variants`) - which candidate versions are alive beside a
  promoted one, and which one was CHOSEN. A variant's declarations live
  on a store BRANCH (`variant/rNN-<slug>`), its picture on a Resolve
  timeline whose name derives from the branch both ways. Choosing
  promotes the candidate into the current round, carrying the choice
  and its reason. `reel_variants.json` (declared) and
  `reel_variant_builds.json` (built, alive).
- RUN (`runs`) - what one pipeline invocation was told and answered:
  a per-run copy of the last-write-wins traces, taken before the next
  run overwrites them. `pipeline_output/run_archives/<run_id>/`, off
  the allow-list - traces are evidence, not declarations.

What the model leaves room for
------------------------------
UNDO and RETENTION are the next phase's, and are not built here. The
model carries both: a promoted reel version names the round, the
moment and (when reconstructed) the store commit it came from, so
"go back to round N" is a checkout of that commit's declarations plus
a rebuild - the one restore route the ruling allows. Retention is
already stated per grouping (`variants.RETAINED_UNCHOSEN`, the
archive bound in `reel_retirement`, run archives unbounded) and is
the place a policy attaches.

Modules
-------
- `store`    the git substrate: allow-list, per-build commit, finished
             timeline and reel-promotion records, the shared `git` and
             `write_record` helpers.
- `rounds`   rounds: discover, stamp, backfill, read; and the one diff.
- `variants` variants: spec, branch, merge; built record, compare, choose.
- `runs`     the per-run trace archive.
"""
