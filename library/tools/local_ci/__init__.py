"""This Mac's local CI service and PR merge queue: status, check, ready, install.

Replaces the firstmate scratch scripts (batch.sh/batchloop.sh) and the
GitHub-hosted PR CI entirely, so the project spends zero Actions minutes.
Two tiers (captain's 2026-10-07 refinement):

- TIER-1 (per PR, immediate, parallel): every new head runs the static
  gate plus its dependent selection and the always-run cross-cutting
  tests, as many at once as the machine can take right now
  (load.targets). The fast signal and the gate into the queue.
- TIER-2 (integration, timeboxed): ready PRs cluster within an adaptive
  window and the FULL suite runs once on the exact stacked tree that
  would land (main + cluster); green merges in order with freshness
  chaining, red bisects to the culprit and merges the rest. No PR
  merges without that pass.

Results post back as commit statuses (free API, no Actions). Dispatch
is load-aware (quiet: more jobs, short windows; busy: fewer jobs, long
windows), never starving foreground work, always deferring to the
heavy-work lock. Latency per PR (seen->Tier-1, ready->merged) is in
`ren ci status`, so the tuning is measurable.

Why this is NOT an extension of the variant merge queue (PR 1924):
`library/tools/undo_journal.py`'s queue merges RECORDED timeline commits
inside one video project, judged by `timeline_diff` previews, with
per-candidate verdicts that ARE the batch split (each candidate merges
against its master independently, so no bisect is ever needed). This
queue merges GIT branches judged by PYTEST, where a PR green alone can
still be red on top of the queue - which is exactly why it bisects on
red, pins the head SHA, and re-checks freshness against the main that
will actually land. Different merge domain, different verdict source,
different freshness clock: sharing code would be sharing names, not
logic. What it reuses is the PATTERN: one evaluation pass, ordered
merges, freshness before merge, kick only the culprit with the reason.

Entry points: `ren ci <status|check|ready|process-once|install|uninstall>`
(`ren/commands.py`, dual-registered in `native/ren-cli/src/commands.rs`),
or `python3 -m library.tools.local_ci <same>`. The SOP is docs/LOCAL_CI.md.
"""

from __future__ import annotations
