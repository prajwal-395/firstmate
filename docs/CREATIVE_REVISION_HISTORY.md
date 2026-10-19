# Creative revision history

`library/tools/creative_revision.py` owns the identity and lifecycle of a
creative revision. Its `revision_id` is distinct from the Project Model's
global event revision. The record is intentionally compact: operations are
semantic descriptions, while snapshots and other large evidence stay in their
own stores and are referenced.

| Fact | Authority | Revision link |
|---|---|---|
| Creative revision identity, parentage, author, intent, approval and lifecycle | `creative_revisions.json` | The canonical record itself |
| Per-reel ordered versions and state digests | `reel_versions.json` | `revision_id`; rows are the legacy per-version projection |
| Attributed reel acts and manual edits | `plan_provenance.json` / `reel_edit_history` | `revision_id` and existing source refs |
| Exact before/after state and in-place recovery | `undo/<entry>/entry.json` | `revision_id`; journal remains the recovery authority |
| Semantic creative commit, changes and commit ancestry | `commits/<id>/commit.json` | `revision_id`; commit parents are mapped to parent revision ids |
| Resolve observed generations and machine-local live reads | Timeline Shadow SQLite | Shadow generations remain observations, linked through refs where recorded |
| Structured editorial state and global event sequence | Project Model event history | `project_model_revision` and edit operation refs |
| Round grouping and variant declarations | `rounds.json` and variant declarations | These describe grouping/branch intent; they do not mint applied revisions |

The revision ledger never replaces existing history and never embeds journal,
timeline, or Project Model snapshots. Existing readers continue using their
established projections. New writers attach the same revision id across the
touch journal, reel version, edit history and creative commit. A touch records
its intent as `prepared` before Resolve mutation; prepared records remain valid
when no approval or promotion follows.

`creative_revision.reconcile(project, repair=False)` checks version,
edit-history, creative-commit and journal references, then checks linked
Project Model event revisions, Timeline Shadow generations and round numbers.
It also reports the number of declared variants; those are branch declarations,
not applied timeline revisions. With `repair=True`, it imports missing legacy
facts as compact revisions and writes the resulting ids back into their fast
projections. Missing Project Model events or Shadow generations are reported,
not recreated, because their source stores own those facts.

Legacy authority boundary: Resolve is the authority for what is live,
Timeline Shadow is a machine-local observation index, Project Model events are
the authority for structured editorial state, and version/journal/commit files
remain the authority for their historical facts. `reel_edit_history`, rounds
and variants are useful projections and groupings, not independent approvals
or substitutes for the exact recovery journal.
