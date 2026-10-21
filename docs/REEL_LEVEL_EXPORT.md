# Reel-level export: one timeline out of ANY Resolve project

The capability the unfamiliar-brief benchmark needed next
(`docs/benchmarks/2026-10-20-unfamiliar-brief-acceptance.md`): the
scratch import could copy a `.drt` into a Ren-owned scratch project,
but the `.drt` had to come from the project that was OPEN, so every
brief was measured against whichever reel happened to be open.

This copies a NAMED timeline out of any project - a captain's project
included - into a `.drt` and into a Ren-owned scratch project.

```sh
bin/resolve-axi project export-timeline \
    --source-project "Podcast (field test)" \
    --timeline "Reel 13" \
    --scratch "Ren Scratch - bench no-extra-footage" \
    --apply
```

`--source-folder` names the Project Manager folder the source lives in
when it is not the current one, `--archive` says where the `.drt` goes
(default: the machine's own `reel-exports` store, never a project
folder), and `--fps`/`--width`/`--height` override the shape - which
otherwise comes from the source project, so the copy is the reel and
not a re-framed version of it.  Without `--scratch` only the `.drt` is
written.

## The contract

- **The source project is read only.**  It is opened, one timeline is
  exported, and it is never saved, edited, renamed or written.  A
  project that is not Ren's is never saved, even when the machine has
  to leave it; what it held, and whether it held unsaved work, is in
  the receipt.
- **The origin comes back.**  The project, its current timeline (by
  unique id) and the Resolve page are restored and READ BACK - on the
  failure path as well as the success path.
- **One writer.**  The command takes the EXCLUSIVE Resolve lease and
  runs through the serving broker like every other AXI verb.
- **Exact names.**  The project and the timeline are named exactly.  A
  name that merely resembles one is refused, with the candidates named:
  the captain's `Podcast (field test)` and the untouchable `Podcast`
  beside it are different projects.

## What it refuses

- a source project or a timeline that is not there, by name;
- a project name that lives in more than one Project Manager folder,
  because Resolve resolves a name inside the CURRENT folder;
- a scratch project whose name is not `Ren Scratch - `;
- an origin that reports unsaved changes, before anything is written -
  the machine would have to leave that project, and Resolve discards
  unsaved work when it does.

## Where it lives

- `library/tools/reel_export.py` - the operation and its receipt.
- `library/tools/resolve_axi_core_commands.py` - the
  `project export-timeline` verb.
- `library/tools/versions/store.py` - the one `.drt` export path
  (`export_timeline_recovery_archive`, with an explicit destination).
- `library/tools/resolve_scratch_projects.py` - the one scratch import
  path (`import_project`, with `save_origin=False`).
- `library/tools/qualification_project.py` - the one in-repo
  `LoadProject` path, and `open_project_for_reading`.

`tests/unit/resolve/test_reel_export.py` - the defect-naming tests:
the source project unchanged, and the origin restored.
