# Test authoring

See [AGENTS.md 10.4](../AGENTS.md) for the test-authoring rule. The guidance below expands on
that rule.

A test file goes where what it ESTABLISHES puts it (`tests/layers.py`): `tests/unit/<subsystem>/`
for one module's behaviour, `tests/contracts/` for an invariant over the whole engine,
`tests/scenarios/` for a multi-component workflow, `tests/qualification/` for real Resolve or real
models, `tests/tooling/` for the CI and test machinery. Add to the subsystem's existing file for that
behaviour before starting a new one. The per-change loop is
`scripts/select_dependent_tests.py --loop <changed-file>`: the reached subsystems' `unit/` plus
`contracts/`.

Never write a test whose failure mode is a correct edit.
A test that asserts a count, a registry size, a file tally, or a snapshot of structure fires on every
legitimate change and teaches everyone to update the number instead of reading the failure.
These are churn, not coverage, and they cost more than the defects they would catch.

Never write a test that asserts a thing exists.
That a module imports, that a constant equals itself, that a registry contains what the same registry
reports, that a dictionary survives a round trip - none of these can fail for a reason anyone cares
about.

Expensive tests need an explicit reason, stated in the test.
Anything that renders pixels, encodes media, measures audio, loads an ML stack, or surveys the whole
tree costs real time on a real machine that someone is using for something else.
The cost is paid by every run forever; the defect it catches must be worth that, and a test that
takes a second or more is an expensive test.
If the same claim can be checked without executing the expensive path, check it that way instead.

While a subsystem is being rebuilt, do not pin its current structure.
Tests written against the arrangement a rebuild is deliberately replacing become work to delete,
and worse, they argue for keeping the arrangement.
Pin the behaviour the rebuild must preserve, never the shape it is meant to change.

Deleting a test is not a loss to be justified.
When a test no longer names a defect it would catch, it goes, and the line recording what is no
longer checked goes with it.
