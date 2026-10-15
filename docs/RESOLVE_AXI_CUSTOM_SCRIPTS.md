# Recorded custom Resolve scripts

Use a purpose-recorded run when Ren has no typed operation for a Resolve task:

```sh
resolve-axi run --purpose "Copy the approved ending to two reels" --file /path/to/script.py
```

The command routes through the shared `ren-resolved` broker when it is serving. A
run without a purpose remains available to developer shells with
`RESOLVE_AXI_ALLOW_RUN=1`.

## Records and safeguards

Ren writes each executed run's exact Python, purpose, project, timeline, caller,
timestamp, result or error, and normalized script and purpose hashes. Project
runs live under
`<project>/pipeline_output/review/resolve_scripts/runs/`; user runs live under
`vep_home()/resolve-scripts/runs/`, beside Ren's other per-user state. The store
versions only the named run record and recovery archive in the project's local
git repository. It never stages footage or unrelated project files. A project
without its own git repository still gets the record on disk, and the result
reports that it could not commit it.

Read scripts are read-only by default. A script with a detected Resolve writer
requires `--unsafe`. Pass `--unsafe` for an indirect or dynamic writer too; it
declares a write and triggers the same safeguards even when static analysis
cannot identify the mutator. Opaque Python attribute access or dynamic code
also requires `--acknowledge-copy-grades`, because Ren cannot establish that it
does not reach the grade-replacing `CopyGrades` call. Before a writing run, Ren requires the named
timeline to be current, reads its timeline and project inventories, and exports
a unique `.drt` recovery archive. It refuses to execute if that backup cannot
be made. After the script, Ren records the timeline changes and checks that no
other project timeline was added, removed, renamed, or changed. The script
cannot use the exposed `open_project` or project-manager switching calls; Ren
also checks the open project after execution and restores the original project
and timeline if an indirect API call switched it.

`CopyGrades` still requires `--acknowledge-copy-grades` in addition to
`--unsafe`. Do not use `--borrow` on a recorded run: recorded scripts cannot
switch projects.

## Scratch projects

Use the typed lifecycle for disposable projects; names must begin with
`Ren Scratch - `:

```sh
resolve-axi project scratch create "Ren Scratch - grade check" --apply
resolve-axi project scratch import /tmp/source.drp "Ren Scratch - imported check" --apply
resolve-axi project scratch open "Ren Scratch - grade check" --apply
resolve-axi project scratch restore --apply
resolve-axi project scratch delete "Ren Scratch - grade check" --apply
```

Create makes one empty current timeline. Import creates a new project under
the supplied name. Create, import and delete restore the starting project,
timeline and page and report the read-back. Open intentionally leaves the
scratch project current and records the starting project, timeline id and
page; `restore` returns to that exact state and clears the record. Every
operation saves the project it is leaving first - the `borrowed()` rule:
save where the captain's rule allows a borrow-shaped flow, never discard -
and refuses when the save fails. Ren never discards anyone's edits. Delete only
accepts a closed `Ren Scratch - ` project, including an explicit refusal for
`Podcast (field test)`.

## Promote a recurring script

After two runs with the same normalized Python syntax or purpose, Ren lists a
candidate in that run store:

```sh
resolve-axi workflow candidates
resolve-axi workflow promote <candidate-id> <run-id> <name>
resolve-axi workflow list
resolve-axi workflow run <name>
```

Use `--scope project|user` to select the store, or `--project-dir` when the
project is not the current directory. Promotion is an explicit human or agent
choice; recurrence never promotes a script automatically. A workflow run
repeats the saved Python and records a new run. Writing workflows also need
`--unsafe` on each run.

This follows the recorded-run then candidate then explicit-promotion shape of
`library/tools/computer_use/distill.py`. Computer-use recipes are anchored UI
actions with replay verification; arbitrary Resolve Python has different
execution and recovery requirements, so it has its own run store and does not
enter `RecipeStore`.
