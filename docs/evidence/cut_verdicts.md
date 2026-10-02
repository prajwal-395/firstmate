# `library.tools.cut_verdicts` - the history behind its contract

This is the module docstring of `library/tools/cut_verdicts.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The rough-cut review's per-cut verdict, and the one reading of it.

Step 3.03 ``review_rough_cut`` is asked for two things.  ``step.py``
computes ``rough_cut_review`` - the mechanical half - and the model is
asked for ``cut_decisions``, which is the narrative half: the judgement
its ``handoff.md`` describes in Check 7, *"Rate each transition as:
``smooth`` | ``acceptable`` | ``jarring`` | ``broken``"*.

Until this change nothing read it, and nothing could.  ``grep -rn cut_decisions
library/ tests/`` returned the field's own declaration in
``step_3_03_review_rough_cut/manifest.json`` and one line of a test's
printed blind-spot note - no reader anywhere.  The declaration carried a
type and no description, so ``generate_output_schema_text`` injected::

    // (required)
    "cut_decisions": []

into the prompt.  A bare list, named nowhere in the handoff, asked of the
model on every run of the pipeline and discarded on every one.

Where it goes now, and why there
--------------------------------
``plan_transitions`` (4.02).  Four nodes are downstream of the review -
4.01, 4.02, 4.03, 4.04 - and only one of them makes a decision AT a cut.

* **4.01 ``plan_subtitles`` cannot be the reader.**  It is
  ``deterministic``: a ``step.py`` and no ``handoff.md``, so it has no
  prompt at all.  Its grouping is measured pixels against the caption box
  (:mod:`library.tools.safe_area`), and a narrative verdict about a cut
  moves nothing in it.  Routing a judgement to a step with no reader is
  the defect this module exists to close, not a way to close it.
* **4.02 ``plan_transitions`` already holds the matching table.**  Its
  pre-bridge builds ``cuts_toon``, one row per cut, keyed on
  ``cut_point_position`` - the position of the INCOMING spine block,
  the same identifier :func:`transition_carriers.cut_carriers` keys on
  and the same one ``transitions_toon`` carries to 4.04.  So the verdict
  needs no join: it is two more columns on a table the model already
  reads by name.
* 4.03 and 4.04 decide over a BLOCK and over a moment; neither is
  addressed per cut, so neither is wired.  Adding one is a row here and a
  ``data_mapping`` key, not a new mechanism.

Both columns are DEFINED in step 4.02's ``handoff.md``, under "Context
data available", and that is where the attribution lives too: the prose
says in as many words that ``narrative_verdict`` is the rough-cut
review's JUDGEMENT rather than a measurement, so a downstream reading of
it is a reading of prose whose author is named.  The definitions
travelled beside the table as a ``CUT_VERDICT_LEGEND`` dict only while
``handoff.md`` was under the captain's freeze; the freeze was lifted
2026-09-09.

What this module refuses to do
------------------------------
**An unjudged cut reads as UNJUDGED, never as ``smooth``.**  A cut the
review did not name gets :data:`UNJUDGED`, spelled differently from every
word in the vocabulary so a reader can tell an absence from a judgement -
the same rule ``passage_engagement`` holds for a passage the model
declined to rank, and ``cutaway_window`` holds for a window nothing
discriminated.  :func:`verdict_of` returns ``None``; nothing coerces it to
the mild end of the scale.

**An unrecognised word is carried VERBATIM and marked, not dropped and not
mapped.**  ``smooth``/``acceptable``/``jarring``/``broken`` is the frozen
handoff's own vocabulary, so a fifth word means the review answered off
its own schema.  Dropping the row would hide a judgement the reviewer
made; mapping ``rough`` onto ``jarring`` would be this module deciding what
the reviewer meant, which is the substitution AGENTS.md 10.5 forbids.  It
is reported as unrecognised and the model reads both the word and that
fact.  ``WITHDRAWN_READINGS`` records the two readings that were
considered and are not taken, so nobody re-derives them.

**Nothing acts on the verdict but the model.**  This states a fact per cut.
It does not filter, rank or re-order ``cuts_toon``, and no rule anywhere
turns ``jarring`` into a transition - which cut gets an effect stays the
model's decision (AGENTS.md 10.5), exactly as ``can_carry_drawn_transition``
left it.

The half of the answer that names no cut
----------------------------------------
A ``cut_decisions`` list carries two kinds of row, and only one of them is
about a single cut.  On 001's run of record the model answered the bare
``"cut_decisions": []`` schema with nineteen rows: **eight** per-cut flow
verdicts, and **eleven** findings about the cut as a whole - the
reconstructed script, the sentence-completion and arc passes, four key
moments, two notes, and one ``flag``.

That flag is why this half is not allowed to fall on the floor::

    Block 13's aligned source range starts at 29.002s while its first
    strongly matched word ('almost') is at 30.58s.  The alignment
    anchored on the word 'i' inside the preceding phrase ... the viewer
    hears roughly 1.5s of audio before the captioned text begins ...
    The clean fix belongs in step 2.2.

The review found the passage mis-anchor, named its cause, named its owning
step and named the fix - and the pipeline discarded it, along with the
other ten.  So :func:`unplaced_findings` is the other reading, and it goes
to the reader a finding nobody downstream can act on already has: the run
summary prints it, the way ``qa_findings`` is printed (AGENTS.md 10.4).

**These are NOT routed to 4.02.**  A finding whose owner is step 2.02 is
not made applicable by putting it in the transitions prompt; that is the
defect ``cohesion_scope.OWNED_UPSTREAM`` exists to name.  A route BACK to
the owning step does not exist and is not built here - it is the same
open shape as `OWNED_UPSTREAM`, stated rather than quietly closed.

The shape the run of record actually used
-----------------------------------------
The declaration carried no description, so the model chose its own shape:
``{decision, scope, from_block, to_block, rating, rationale}``, with the
per-cut rows marked ``decision: "flow"`` and the verdict under ``rating``.
The manifest now states the shape, and the reader still accepts that one -
``to_block`` is the incoming block and therefore the same cut
``cut_point_position`` names, and ``rating`` carries the same four words.
Reading the answer the pipeline really produced is not the same as mapping
a synonym onto a vocabulary word: the KEY moves, the VALUE is untouched.


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**The rough-cut review's own answer has a reader, and it has two halves.**
`review_rough_cut` (3.03) is asked for `cut_decisions` on every run.
One enumeration, `library/tools/cut_verdicts.py`. [why](docs/RULE_EVIDENCE.md#the-review-answered-and-nobody-read-it)
- **A row that names a CUT goes to step 4.02**, folded onto `cuts_toon` as `narrative_verdict` and
  `verdict_note` keyed on `cut_point_position` - the identifier both tables already share, so there
  is no join. **Step 4.02's `handoff.md` defines both columns in its prose**, including that a
  verdict is the review's JUDGEMENT and not a measurement - the attribution
  `direction_contradiction` reads. **4.01 `plan_subtitles` cannot be the reader**: it is
  `deterministic` and has no prompt at all.
- **A row that names NO cut goes to the run summary**, printed after `status` is decided. A route
  back to the owning step does not exist and is stated rather than quietly closed.
  **Nothing is filtered by `decision`, `scope` or severity**: whatever picks which findings matter
  becomes the reviewer.
- **An unjudged cut reads `unjudged`, never `smooth`.** `verdict_of` returns None, and how many cuts
  went unjudged is SAID (`cuts_unjudged`) rather than inferred from a column. A word outside
  `smooth`/`acceptable`/`jarring`/`broken` is carried VERBATIM and marked `unrecognised` -
  `WITHDRAWN_READINGS` records why dropping it and why mapping it onto the nearest word are both out.
- **The verdict decides nothing.** No rule turns `jarring` into a transition; the column is data and
  the model still chooses (10.5).
- **3.03's one input that is a MEASUREMENT rather than an upstream decision is `temporal_index`**, and
  it reads it as `view:transcript` - its Check 5 requires a script "derived from actual temporal
  index data, not from the speech_sequence's intended text", so the projection must not delete it.
  **The remaining self-review is not in the DAG - it is that one agent answers 2.02, 2.05, 3.02 and
  then 3.03 under `--full-auto agent` (10.1). Closing that needs a different answerer, not an edge.**
- `tests/test_cut_decisions_reach_a_reader.py`.
```

## `tests/test_cut_decisions_reach_a_reader.py` (moved from its module docstring, 2026-10-02)

The rough-cut review's narrative answer goes somewhere, and it arrives.

Step 3.03 is asked for `cut_decisions` on every run of this pipeline.
Before this change, `grep -rn cut_decisions library/ tests/` returned the field's
own declaration in `step_3_03_review_rough_cut/manifest.json` and nothing
else - no DAG edge carried it, no manifest declared it as an input, and no
line of Python indexed it.  The model answered and the answer was dropped.

This file holds the wiring together in both directions:

  * the DAG carries it from the producer to the reader,
  * the reader DECLARES it, so `gather_step_inputs` will hand it over,
  * the reader's own bridge - run as a real subprocess over JSON stdin,
    the way the runner runs it - puts the verdict in the table the
    handoff tells the model to read, and
  * the verdict never invents itself: a cut the review did not judge
    reads `unjudged`, and the mild end of the scale is not borrowed for
    it (AGENTS.md 10.5).

The bridge is DRIVEN rather than modelled: a table this step builds in
code is exactly where the last two defects of this class hid. This test
drives the bridge subprocess and asserts its observable output.
