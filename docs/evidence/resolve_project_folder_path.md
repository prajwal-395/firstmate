# The Project Manager folder a project is filed in

The evidence behind `library/tools/resolve_project_folders.py` - the
route that reaches a Ren-owned Resolve project in ANY Project Manager
folder, and the refusal that names the folder when this machine cannot
get there.

Punch list AXI-015, 2026-10-22. The lane's own report: `Ren Scratch -
breaktest is not in Resolve current folder; supported scratch open and
project-pinned session cannot open/read it`.

## What was wrong

The Project Manager's listing is FOLDER-SCOPED.
`GetProjectListInCurrentFolder()` answers the projects in the folder the
Project Manager is showing. So a project filed in another folder reads
as ABSENT, and Ren's scratch open and the project-pinned session
refused on that reading - a reading that says nothing about whether the
project exists, and a refusal that named no folder at all.

## Measured, 2026-10-22

On the machine's own broker (the serving broker, the `poetic` Disk
database), through the broker queue.

**The projects exist.** `Ren Scratch - breaktest` and `Ren Scratch -
vep-bench-demo2` are held by the Resolve database on disk at
`Lucie Content/Social Media/Podcast/`, and the database's ROOT holds
exactly the projects and the folders the API listed at the root, one
for one: the database mirrors the Project Manager tree.

**The folder cursor could not be moved through the API.**
`GotoRootFolder`, `OpenFolder` and `GotoParentFolder` answered `None`
and left the cursor where it was. `GetCurrentFolder()` kept answering
`None` and the folder listing kept answering the ROOT's folders - right
after the navigation, after settles of 0.5s, 1s, 2s and 3s, and again
on the media page. A lane's own navigation measured the same thing and
refused with "Resolve did not read back project folder 'trials'".

**`LoadProject` did not reach a project filed in another folder.** It
answered `None`, and the open project read back as the one that was
already open. A PATH was refused the same way: the folder path
("Lucie Content/Social Media/Podcast/<name>"), the absolute path of the
project's database, and the folder path with backslashes.

**The database read is not what Resolve reports.** `GetCurrentDatabase()`
answered NOTHING on this build, so the database root comes from the
machine's own database list (`dblist.conf`); a machine with several
disk databases gets no guess.

## The rule

A project is addressed by its FOLDER PATH, never by the listing of
whatever folder the Project Manager happens to be showing.

The path is RECORDED (the scratch-origin ledger) or DISCOVERED - the
Resolve database on disk mirrors the Project Manager tree, so the folder
a project is filed in is readable from it without opening it. The
Project Manager is navigated to that path, the project is loaded by its
exact name, and the OPEN project is read back by that exact name: the
one reading on this Resolve that says which project is open.

And a machine that cannot get there says so in a TYPED refusal that
NAMES THE FOLDER. "The project is filed in 'Lucie Content/Social
Media/Podcast' and this machine's Project Manager cannot reach that
folder" is a different fact from "the project is not there", and only
the first one can be acted on.

## The fix

- `library/tools/resolve_project_folders.py` owns the folder path and
  the route: the database discovery, the navigation, and the typed
  refusal (`ProjectFolderRefused`, code `REN_RESOLVE_PROJECT_FOLDER`).
- `qualification_project.switch_project` takes the folder and navigates
  AFTER it reads the current project - that read is measured to put the
  Project Manager back to the root, so a navigation done before it is
  undone by it. It is still the ONE in-repo `LoadProject` path.
- `qualification_project._open_project_folder_path` delegates to the
  same navigation, so there is one route and not two.
- `resolve_scratch_projects.open_project` and
  `resolve_session.open_project` reach the project by its folder path,
  and the scratch-origin ledger records the path so the next session
  starts from the recorded one.

Defect-naming tests: `tests/unit/resolve/test_resolve_project_folders.py`.

## Before and after, with denominators

| | Ren-owned scratch projects openable by the session | the refusal |
|---|---|---|
| before | 0/1 | flat `ScratchProjectError`: "is not in the current folder", no folder named |
| after | 0/1 on THIS machine (its Project Manager does not move - measured above), 1/1 where the Project Manager moves | 1/1 **correctly refused**, typed, naming `Lucie Content/Social Media/Podcast` |

The project is FOUND on this machine now - the folder path is read out
of the database - and the route is attempted; the open still fails here
because the machine's Project Manager cannot move. That is the
machine's limit, not the route's, and the refusal now says so by name.
The UI route (the AX driver) is the remaining way to open a project in
another folder on a machine whose Project Manager does not move.

## The live proof, job by job

Both jobs ran through the serving broker's queue, on the machine's own
Resolve, with the branch's own module loaded from the branch.

**BEFORE** - the shipped route, `resolve-axi project scratch open "Ren
Scratch - vep-bench-demo2" --apply` (broker job `5219939f50275519`):

```
error: scratch project operation failed: scratch project
'Ren Scratch - vep-bench-demo2' is not in the current folder
```

No folder named, and the project is not even looked for.

**AFTER** - the branch's route, run against the live Project Manager
(broker job `5123b0945f8218ec`):

```
projects_root: /Users/prajwal/Pictures/Davinci/Resolve Projects/Users/guest/Projects
Ren Scratch - breaktest       -> Lucie Content/Social Media/Podcast
Ren Scratch - vep-bench-demo2 -> Lucie Content/Social Media/Podcast
Ren Scratch - fm-p008c        -> Lucie Content/Social Media/Podcast
Ren Qualification             -> (the Project Manager root)
                                 Lucie Content/Social Media/Podcast
navigation to Lucie Content/Social Media/Podcast:
  ProjectFolderRefused (REN_RESOLVE_PROJECT_FOLDER)
  what: Resolve's Project Manager would not open the folder 'Lucie
        Content' on the way to 'Lucie Content/Social Media/Podcast'
  why:  OpenFolder('Lucie Content') was refused, so the folder the
        project is filed in was never entered
  fix:  open the project in Resolve's Project Manager by hand, then
        re-run the same command
```

Two projects answer to the name `Ren Qualification` - one at the
Project Manager root and one beside the captain's project - and the
route refuses that with both folders named rather than picking one.
