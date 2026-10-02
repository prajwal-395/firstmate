# duration enforcement

Test: `tests/unit/picture/test_pacing.py`.

Tests for duration enforcement in review_rough_cut and creative_cohesion.

The zone is the PROJECT's declaration, and there is no longer a fallback.
`get_target_duration_zone` used to answer `(54.0, 60.0, 66.0)` whenever
nothing declared a target - and nothing ever did, because no state key
and no DAG edge carried `project_config` to any step.  So the captain's
own `target_duration_seconds: 60` in project 001's project.yaml governed
nothing, and every one of these gates ran against a minute the pipeline
made up.  `load_pipeline_state` now reads the declaration and the runner
broadcasts it; with nothing declared, a gate reports that it did not
check rather than judging the cut against an invented length.

`DECLARES_60` below is 001's own declaration.
