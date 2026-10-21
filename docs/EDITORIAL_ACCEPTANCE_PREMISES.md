# The premise a brief rests on, and the footage it is measured against

An acceptance set is only meaningful if the footage it runs against
satisfies the briefs' premises. The set in
`library/tools/editorial_acceptance/brief_set.json` states the footage
profile each brief is written against; this document is the rest of
the contract - what the premise is, where the measurement lives, what
the harness does with it, and what has to change for every brief in
the set to be runnable.

The defect this closes: four of the eight briefs named something the
substrate reel did not contain - a statement of what it costs, a
moment of overlapping speech, a caption that names the wrong word, a
music bed - and the run was scored for failing to find what was never
there. See
[the 2026-10-20 benchmark](benchmarks/2026-10-20-unfamiliar-brief-acceptance.md).

## Three objects

**A feature** is a fact about a piece of footage, out of a closed
enumeration (`footage.FEATURES`): the footage states what something
costs; two speakers talk over each other; a caption names a word or
number the speech does not; a second camera angle covers a moment
already shot; a music bed runs under the dialogue; a lower third
carries a name; the workshop bench was filmed; the new logo is in the
project; and so on. A premise stated in words nobody can check is a
premise nobody can enforce, so the enumeration is closed.

**A requirement** is what a brief's request presupposes of the footage
it runs against. It lives in the brief's answer key
(`oracle.footage_requirements`), because it is what the request
implies rather than what the request says, and each one says whether
the feature must be `present` or `absent`. A brief whose correct
answer is "that footage does not exist" says so as a requirement with
`absent` presence.

**A substrate** is one reel, measured once, and recorded in
`library/tools/editorial_acceptance/substrates.json`:

```json
{
  "id": "podcast-reel-13",
  "profile": "two_camera_interview",
  "aliases": ["Reel 13 - the-accounting-firm-ai-called-healthcare"],
  "features": ["lower_third_overlay"],
  "checked_features": ["cost_statement", "overlapping_speech", "..."]
}
```

`features` are the facts the footage carries. `checked_features` are
the facts somebody looked for and did not find. A feature in neither
list was never looked for, so a requirement resting on it is
**unchecked** - neither satisfied nor missing.

## What the harness does with it

**The score stays the score.** A brief whose premise the substrate does
not carry is still scored, and still counts as the engine's result: the
engine still had to do the work the request asks for, and the headline
is the engine's headline. The premise is measured and reported
**ALONGSIDE** the score, never instead of it.

So the report carries two views of the same run:

- the **headline**: the stage metrics and the failure causes, over
  every scored brief, exactly as before;
- the **premise view**: for every brief, whether the substrate carried
  what the request presupposes (`premise_carried`,
  `premise_not_carried`, `premise_unchecked`), the premises that are
  missing by name, and a stage-metrics view in which the briefs whose
  premise was not carried count as `not_measured`.

A premise nobody looked for is `premise_unchecked`, and it says so
rather than guessing either way.

## The pairing: the fix is the substrate

A brief with no substrate that carries its premise is not a brief to
argue about; it is a brief waiting for footage that satisfies it. So
the report names, per brief, what such a substrate has to carry:

```sh
bin/ren accept --select
```

The choice prefers a substrate whose **profile** matches the brief's
declared profile among those that carry the brief's premise, and it
says which substrate it picked and why. When no substrate carries the
premise, the pairing names the exact premises a substrate has to
carry - which is the work list for whoever exports the reel.

A record names the substrate it ran against, in its `project` section;
the timeline's own name is honoured as an alias, because that is the
name a reel is known by.

## Adding a substrate

1. Measure the reel: what it carries, and what it was checked for and
   does not carry. A feature nobody looked for stays out of both
   lists.
2. Add an entry to `substrates.json` with its id, its profile, the
   aliases it is known by, and the evidence the measurement rests on.
   The catalogue is validated when it is loaded: a feature outside the
   enumeration, or a feature a substrate both carries and denies, is
   refused.
3. Re-run `bin/ren accept --records <dir> --report <path>` and the
   brief set is measured against the new reel.

The captain's projects, timelines and media are never written by this
harness: it reads records and reports, and the measurement of a
substrate is taken by whoever exports the reel.
