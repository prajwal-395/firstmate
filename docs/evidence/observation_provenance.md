# Where an observation came from: the source file, and the exact seconds

Code: `library/tools/observation_provenance.py`.
Tests: `tests/unit/context/test_observation_provenance.py`.

The punch list's **VI-037**: "Maintain stable provenance from observations
to source files and exact time ranges."

## What was already there

Each piece of provenance exists and each is load-bearing:

- an **observation** names its asset and its interval on a named clock
  (`project-observation.schema.json`);
- an **asset** names its URI and its content digest
  (`project-asset.schema.json`);
- a **clock** names its domain - source, project, timeline, render - and
  its tick rate (`project-clock.schema.json`);
- a **time transform** maps one clock onto another with an exact
  rational slope, an offset and an uncertainty bound
  (`project-time-transform.schema.json`);
- `project_model_contract.TimeMap` is the one reading of that algebra.

## What was missing

The ONE place that puts them together. A reader that wanted "which
file, and which seconds" had to know the model's four collections, the
transform algebra, and the difference between a source clock and a
timeline one - and every reader that assembled that answer assembled it
differently.

`observation_provenance.resolve` answers for one observation;
`resolve_all` answers for a project's evidence in one pass;
`source_placements` answers for a list a caller already holds.

## The rules

**Absent is absent.** An observation whose clock has no measured path
back to a source answers with a reason and NO file and NO seconds. It
never answers with a zero, with the asset's whole duration, or with a
nearest-neighbour interval: a reader that cannot place an observation
must be able to see that it cannot.

**Exact, and then rounded once.** The mapping is `Fraction` arithmetic
throughout and the seconds are rounded at the very end, so an interval
that starts at a third of a frame does not drift by rounding on the way
there. A transform with a slope of 3/2 maps timeline 300-600 ms onto
source 200-401 ms, not onto source 200-400: the range covers the
mapped moment rather than truncating it.

**The uncertainty travels.** A source interval reached through a
measured transform carries that transform's uncertainty bound, widened
when the chain is longer than one segment. A reader that wants to know
how sure the seconds are is told, rather than left to assume they are
exact.

**Identity, not a name.** The asset's own URI wins - it is the model's
record of the file. A relative URI is resolved against the project
folder, because a model's URI is project-relative by construction. When
the model names no file, the project's catalog is asked by CONTENT
DIGEST, which is identity rather than a name.

## The surface

`ren evidence` now carries the placement on every answer row, one entry
per contributing observation:

    ren evidence <project> <plan.json>

Each row's `sources` block names the file and the exact source seconds.
A row placed on a TIMELINE clock is placed there for the join; a reader
who wants to open the footage needs the source, and the placement is
resolved through the measured transform.

The module also has its own CLI:

    python3 -m library.tools.observation_provenance resolve <project> <observation_id>
    python3 -m library.tools.observation_provenance resolve-all <project>

## What is still missing

- The `sources` block is per contributing OBSERVATION, and a joined row
  can have several. A reader that wants the union of their ranges has
  to read them; the union is not computed here.
- The transform chain is searched breadth-first, so the shortest chain
  wins. Two chains of equal length that disagree are not detected: the
  first is used.
- A model with no Project Model has no observations to place, and says
  so.
