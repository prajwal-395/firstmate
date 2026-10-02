# `library.tools.replay_bench` - incident history behind its tests

Moved out of `tests/unit/context/test_replay_bench.py` docstrings (test-suite halving,
2026-10-02). The tests keep the invariant; this keeps how it was found.

## The replay that wrote the live project

The snapshot REFERENCES `pipeline_output/` by symlink, so a replay that ran
against the snapshot directory wrote straight into the live project: 3.04's
diagnostics, 3.02's footage analysis and frames, 4.04's catalogue. Measured
2026-10-01 on 001 - 395 files written into a project whose last run was
2026-08-30. A replay gets a clone instead, and every path in it - including
one the state names absolutely - resolves outside the source.
(`test_no_path_in_a_replay_workspace_resolves_inside_the_source_project`)

## The brief the replay dropped

`load_pipeline_state` is the runner's whole state assembly: it parses
`pipeline_data.json` and then overlays the values that belong to the run
rather than to any upstream step - `creative_brief` and `brand_template` off
`project.yaml`, the asset libraries off the environment. The bench used to
`json.load` the frozen state file and stop there, and that omission fails in
the one direction that matters: a project pointing at a brief reconstructed
as a project pointing at none, so the routing change read as a no-op.
Measured on 001 the day it was pointed at the channel document (#214) -
seven steps declare `creative_brief` and all seven replayed without it.
(`test_a_declared_creative_brief_reaches_the_reconstructed_context`)

## The brief the snapshot did not carry

Ten steps declare `creative_brief` and the runner RAISES rather than
degrading when a declared brief cannot be read - correctly, because a step
that reported success having read a filename was the defect that rule
replaced. So a snapshot that did not carry the brief could not reconstruct
ANY of those ten, which is every step that makes a creative judgement.

Found 2026-09-05: step 3.4 regained its declaration and
`replay_bench compare select_reels` stopped working entirely, with
"declares creative_brief and the project points at
<snapshot>/project/creative_brief.md, which cannot be read".
(`test_capture_freezes_the_creative_brief_a_project_declares`)
