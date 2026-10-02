# Test categories and timing telemetry

The full-suite gate classifies tests by what they exercise, not by how long a past run took.
The registered markers are `unit`, `scenario`, `resolve_live`, and `real_model`. Tests without
one of these markers are assigned `unit` during collection. Runtime is telemetry only.

## What the old measurement says

The former `heavy` set came from a serial run on 2026-09-23 against `097ec79a`:

```
9692 passed, 5 skipped in 388.85s
```

That is 9,697 collected cases. On `2f021420` (main on 2026-10-02),
`bin/vep -m pytest --collect-only -q` collected 6,961 cases, about 28% fewer. The old 1-second
cutoff and its 95-case set described a substantially different suite. The current gate no
longer carries or uses those test-level duration labels.

## Semantic markers

- `unit` is the default for a test without another semantic marker.
- `scenario` identifies a multi-component workflow or captured production failure.
- `resolve_live` identifies a test that drives the running Resolve instance. The full-suite
gate reports and excludes these tests because they move the captain's current timeline.
- `real_model` identifies a test that loads or measures with real ML dependencies. The full-suite
gate runs this category in a separate, serial phase after checking the interpreter's declared
ML dependencies. If it cannot run, the verdict is narrowed by name.

The gate runs unit and scenario tests together on every full run. Test duration does not move a
test between categories or change whether it runs.

## Timing telemetry for sharding

After a complete `PASS`, the gate replaces `~/.cache/ren/pytest-file-durations.json` with file
durations read from its JUnit reports. On the next run, `scripts/pytest_timing.py` orders longer
files first for pytest-xdist's `loadfile` scheduler. This gives long files more opportunity to
start early across workers; the timing data does not select, skip, or reclassify tests. A missing
or invalid cache leaves pytest's normal collection order in place. Set `VEP_TEST_TIMING_FILE` to
choose another cache path.

The timing file is local, replaceable operational data. It is not checked in, reviewed as policy,
or used to decide which tests the suite contains.
