# M2 frame cache integrity

## Finding

The round-two qualification failure is in source-memory frame cache
freshness, not the probe/hash identity cache from PR 2214. M2 obtains its
content digest with `footage_identity.fingerprint`; it does not use
`file_measurements`. The probe/hash cache only feeds catalog measurements
in step 1.02 and the reel rebuild check.

An M2 index could survive after its shared `frames/` directory or a
referenced thumbnail disappeared. The frame lane treated the JSON index as
fresh, and `build_frames` returned `reused: true` without restoring the
missing payload. The frame search then tried to open paths that were no
longer on disk. The precise event that removed `clip_002`'s directory was
not available in the qualification failure record; the stale-index state
reproduces the observed refusal.

## Change

M2 freshness now requires the shared frame directory and every indexed
thumbnail to exist. The lane and frame reader report an incomplete sample
as stale, and the builder decodes it again. A regression test writes a
valid-looking M2 index with a missing frame directory, confirms the lane
and reader reject it as fresh, then verifies the builder recreates the
thumbnail.

## Validation

- Dependent test selection: 479 passed with `pytest -n 2` through
  `vep-verify-run.sh`.
- Enforcing Ruff gate on the changed Python files: passed.
- The original failure was reconstructed with a local `tmp_path` regression
  fixture. No qualification project or Resolve timeline was used for this
  analysis-path fix.
