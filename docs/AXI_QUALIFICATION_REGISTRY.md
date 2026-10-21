# AXI qualification evidence registry

Live qualification owners record one result per operation/backend pair using
`ren-axi-qualification-result/v2`, then import each completed report with:

```sh
python3 -m library.tools.axi_qualification --import-result result.json
```

## What a report says

A qualification is a claim about a STATE, not about a place. The report
therefore states:

- **environment** - the Resolve version and the machine the evidence was
  earned on. A qualification earned on Resolve 18 is not a qualification on
  Resolve 21.
- **semantic boundary** - what the evidence applies to, and what it does
  not. Both halves are required: a boundary that lists only what it covers
  reads as if it covered everything else.
- **coverage** - the dimension values the runs exercised, per dimension,
  and the disposable configurations they were exercised in.

The dimensions are the one enumeration in
`library/tools/axi_qualification_dimensions.py`: media type, clip
composition, linked audio, track layout, track occupancy, timeline density,
timeline structure, project settings, frame rate, existing effects and the
Resolve version. Each has a closed vocabulary, and the runtime measurement
(`library/tools/axi_qualification_state.py`) produces every one of them.

The report's `qualification_predicates` may be omitted, in which case the
coverage IS the predicate set. When it is given, it must not reach past the
coverage: a predicate that names a value the coverage does not record is
refused, and so is one that names a project or a timeline, because a
predicate that names a place restricts an operation to the place the evidence
was earned rather than to the state it was earned in.

## What the registry stores

The registry stores observations, not qualification decisions. The editing
algebra derives qualification and production enablement from a passing record
with all required evidence. Historical test references remain visible as
incomplete evidence until a result meets this schema.

## The disposable configurations

A qualification earned on one timeline shape says nothing about another, so
the live run walks a matrix of configurations
(`library/tools/axi_qualification_configurations.py`): empty and populated
tracks, clips that already carry linked audio, a transition, a transform, a
grade, a retime or a Fusion composition, and the common frame rates in
portrait as well as landscape. Each configuration declares the shape it
builds, and the run MEASURES what it built and compares.

The walk itself is `tests/live/axi_qualification_matrix.py`: it builds a
configuration, measures the runtime state, runs the operation through its
production route, and compares the state before and after
(`library/tools/axi_qualification_snapshot.py`) for the exact requested
changes and the unintended modifications.

## Auditing the registry

```sh
python3 -m library.tools.axi_qualification --audit
```

The audit reports every registered row, whether its boundary is explicit,
and which rows still need a live re-measurement. It tells two states apart:

- **not qualified** - the row's evidence did not pass. It qualifies
  nothing, and that is an honest outcome rather than a defect.
- **ineligible** - the row's evidence PASSED and its boundary is broken: a
  predicate that names a place, a value the measurement cannot produce, or
  a missing environment, semantic boundary or coverage. Such a row reads
  as qualified and can never be routed.

The audit is a GATE: it exits non-zero when a row is ineligible, so
`python3 -m library.tools.axi_qualification --audit` fails on a registry
that claims a qualification it cannot keep. `tests/contracts/
test_axi_qualification_registry.py` holds the shipped registry to it.

Regenerate the AXI reference matrix after importing evidence with:

```sh
python3 -m library.tools.operations --emit-resolve-axi-references
```

That command prints generated references as JSON; write the returned file
contents to `.agents/skills/resolve-axi/references/`.
