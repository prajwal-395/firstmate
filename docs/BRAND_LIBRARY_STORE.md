# The series-shared brand library: its own store

**Status: SELECTED by the implementing lane, 2026-09-22; the store
creation itself awaits the captain.** The where-question is the
captain's (fleet task `vep-brand-masters-live-outside-version-control`):
this document establishes the answer with evidence so the decision can
be taken, and ships the tooling the answer needs. Nothing here touches
the captain's data - the `git init` and the first commit run on their
machine, by their hand, with the exact commands in section 4.

## 1. The gap

Found 2026-09-17 while verifying the logo frame-rate fix. The Lucie
series library at `lucie/_shared/brand-assets/motion/` - the 30fps logo
master, its 23.976 conform, the 30fps bumper, its candidate conform,
the eye-test evidence (plus two bulb-ending folders since) - is not a
git repository and is not inside one. As of the geo-podcast store's
`eea1cb5`, every reel plans its closing logo at the GENERATED conform:
a file with a documented-but-unreproduced recipe, no history, no undo
and no integrity record. The declaration naming it IS versioned, which
is what makes the asymmetry easy to miss: delete or overwrite the file
and every reel build loses its ending with nothing to restore from.

## 2. The three places it could live

**Its own store, beside the projects that share it - SELECTED.** The
declaration already says it: geo-podcast's `project.yaml` calls the
closing logo "the SERIES' own file, beside the projects that share
it", named by absolute path because a reference is a path plus a map
(AGENTS.md 10.1). One master, many referencing projects, one history.

**Inside each project's data store - REJECTED.** The asset is one
series' file shared across projects; a copy per project store is a
copy that diverges, with nothing saying which is the master. And the
project store is a TEXT-ONLY allow-list by design
(`library/tools/build_version_control.py`): binaries are ignored by
default, deliberately. Filing ProRes masters there fights that ruling.

**In the engine repo - REJECTED.** A Lucie logo fails the substitution
question (AGENTS.md 14), so the engine stays series-neutral; and PR
1181 states it outright - "Asset files are project data, so they never
enter a pipeline-repo commit."

("Somewhere else entirely" - a cloud asset manager, a NAS with its own
versioning - has no reader in this pipeline and is machinery ahead of
a need. If the store ever outgrows plain git, that is the moment to
revisit, with the sizes that forced it.)

## 3. Layout

The store root is the `_shared/` directory itself - recognised by what
it holds (`brand-assets/`), never by its name. Inside it:

- `brand-assets/motion/` (today), a future `stills/` beside it: the
  masters, their conforms, review copies, per-folder READMEs.
- One `MANIFEST.json` per asset directory, living inside the
  directory it covers - no grand index to update when a folder gains
  a file.
- A deny-list `.gitignore`: the inverse polarity of the project
  store, because here the binaries ARE the record. Only machine
  droppings (`.DS_Store`, `*~`, `*.tmp`) are ignored.

Plain git, no LFS: the whole motion folder is ~60 MB. The project
store's own `.git` is 221 MB; a 60 MB asset store is ordinary git,
and LFS would be machinery ahead of a need.

## 4. Bringing a store under version control

On the captain's machine, with the engine checkout on PATH:

```
python3 -m library.tools.brand_library --init "/path/to/lucie/_shared"
python3 -m library.tools.brand_library --write-manifest "/path/to/lucie/_shared" \
    --project-yaml "/path/to/lucie/geo-podcast/project.yaml" \
    --project-yaml "/path/to/lucie/podcast-roughcut/project.yaml"
git -C "/path/to/lucie/_shared" add -A
git -C "/path/to/lucie/_shared" commit -m "brand library: initial store record"
python3 -m library.tools.brand_library --verify "/path/to/lucie/_shared"
```

`--init` is purely additive (a `.git` directory, a `.gitignore`,
repo-local identity) and stages nothing; the first commit stays the
captain's explicit act. `--write-manifest` measures each file with
the engine's own instrument (`brand_motion.measure_source` - one
measurement, so manifest and renderer cannot disagree) and records
which declarations point at it. Thereafter a new conform lands, the
manifest refreshes (curation is preserved per file - roles, recipes,
notes survive a re-measure), and the refresh commits beside it.

## 5. What the record guarantees

- **History and undo** are git's: `git log` on the store answers what
  the file was; `git show` brings it back.
- **Integrity** is the manifest's: whole-file sha256 plus size per
  file (whole-file, not the footage fingerprint's window - these are
  tens of megabytes, and a middle-edit that keeps the length is
  exactly the overwrite this catches). Verification compares bytes
  only, never re-measures: the measurement is description, the digest
  is identity, and an ffprobe version skew must never read as drift.
- **The reader** is plan-time: a reel plan placing a
  manifest-covered asset reports a missing, changed or unrecorded
  file on the card and on stderr (`full_frame_element.library_note`,
  via `brand_library.library_note_for_asset`) - REPORTED, never a
  gate. The strict surface is `--verify` plus `require_clean`.
  Untracked files never refuse: landing new work is not drift.

## 6. The conform recipe, where it lives

The recipe for `logo_reveal_23976.mov` today exists in exactly two
places: PR 1181's body (minterpolate blend, one tpad-cloned tail
frame, settb/setpts re-stamp, ProRes 4444, pcm kept - plus the three
rejected approaches with measurements) and the provenance comment in
the geo-podcast `project.yaml`. The manifest's per-file `recipe` and
`derived_from` fields are where that record moves next: stated once,
versioned beside the file it built, preserved across refreshes.

## 7. What remains the captain's

1. **Run section 4.** Nothing in this repo can `git init` their
   data for them, and nothing here presumes to.
2. **Classify the files.** Every entry lands as role `asset`; the
   master/conform/candidate/evidence call is a one-line edit per
   file in versioned text.
3. **Wire the bumper or don't.** `transition_bumper_23976.mov` is a
   verified candidate no declaration names - that call was already
   theirs in PR 1181 and is unchanged by this.
