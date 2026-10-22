# The recorded backend conformance set

Fourteen recorded requests, run through every execution backend and
compared on content, preserved state and timing. The check is
`library/tools/backend_conformance.py`; run it with:

    python3 scripts/backend_conformance.py

It exits 1 when a measured request DISAGREED, and it reports a backend
that could not be run as `unmeasured` - never as an agreement and never
as a disagreement.

## What is recorded here

    requests/<id>/request      the descriptor: the argv, the env, the files
    project_files/<name>/...        the project files a request's descriptor names

A descriptor is line based (`key: value`), so it reads in a diff and
needs no parser:

    id: check_valid            the request's name
    arg: check                 the argv, one key per argument
    arg: demo
    env: REN_OUTPUT=json       extra environment for this request
    file: project_files/demo/project.yaml    a fixture file, relative to this root
    file: project_files/demo/brief.md
    footage: raw/clip.mov | testsrc=duration=1:size=320x240:rate=30
    omit: raw                  a path to remove, for a project that lacks it
    note: what this request covers

Two details worth knowing:

- **The footage is synthesized, not stored.** A `footage:` line names a
  path and a lavfi source; the harness encodes it with ffmpeg, so the
  fixture carries no binary and no real media.
- **A path that cannot be known before the run is a placeholder.** A
  project that declares `source.footage_root: {{media}}` names the
  scratch media folder, whose path depends on the run. The media
  folder is written to by a `footage:` path under `media/`.

## What a request must not do

No request may touch Resolve, render anything, or read a project outside
the scratch directory. The harness points every path a backend reads -
the projects root, the two asset libraries, the per-user config
directory, the state directory the shadow store lives in - at a scratch
directory it creates and empties before each run. Both backends run at
the SAME scratch path, because the path is part of what a verb prints.
