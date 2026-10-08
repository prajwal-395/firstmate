# Real-footage workflow qualification

Run the end-to-end qualification with a prebuilt footage index:

```sh
bin/ren qualification workflow --index \
  /path/to/project/pipeline_output/scratch/footage_index/footage_index.json
```

The index is read-only. Its search chooses one audio/video source file no longer
than five minutes. Ren copies the file byte-for-byte into the qualification
media store, checks its SHA-256, and leaves the source untouched. The report
records the source and copy paths, file hash, index query and chosen clip id.

The command runs two consecutive clean rounds. Each round runs semantic
analysis and the source-memory search lanes in a disposable collection
project, skipping `temporal.index` and `prosody.analyse` because the current
analysis orchestrator cannot supply the temporal operation's required
semantic documents. It uses the fresh text index to choose a timed passage,
builds an A/V edit with `EditPatch`, adds a scripted editor
marker, applies a later Ren clip insertion and reads the marker back, exports
through the Resolve broker, masters the audio and runs the deterministic
`verify_render` gate. Resolve work is a typed broker job and is confined to the
exact `Ren Qualification` project inside `qualification_project.borrowed()`.

Dated stage evidence and exports live outside the checkout at
`<qualification media dir>/workflow-runs/<UTC timestamp>/`. The aggregate
report is `qualification-report.json`; any failed stage leaves a durable
failure entry and a nonzero command result.
