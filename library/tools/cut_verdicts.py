"""The rough-cut review's per-cut verdict, and the one reading of it.

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
"""

#: The vocabulary, in the order step 3.03's ``handoff.md`` states
#: it - Check 7, *"Rate each transition as: smooth | acceptable | jarring
#: | broken"*.  Ordered mildest to worst because the handoff's own
#: rejection rule is ordinal (*"Any broken transition is a rejection.
#: More than two jarring transitions is a rejection"*), and NOT because
#: anything here compares them: no reader in this repository does.
VERDICTS = ("smooth", "acceptable", "jarring", "broken")

#: What a cut the review did not judge reads as.  Deliberately not a
#: member of :data:`VERDICTS`, and deliberately not the mild end of it:
#: the absence of a judgement is not the judgement "fine".
UNJUDGED = "unjudged"

#: What a cut carrying a word outside the vocabulary reads as, beside the
#: word itself.  See the module docstring.
UNRECOGNISED = "unrecognised"

#: Readings that were considered for an out-of-vocabulary verdict and are
#: NOT taken.  A withdrawal without its reason gets re-added.
WITHDRAWN_READINGS = {
    "drop the row":
        "Silently loses a judgement the reviewer really made. The whole "
        "defect being fixed here is a narrative answer that goes "
        "nowhere; a reader that discards the rows it does not recognise "
        "reproduces it for a subset of them.",
    "map it onto the nearest vocabulary word":
        "'rough' onto 'jarring', 'fine' onto 'smooth'. That is this "
        "module deciding what the reviewer meant, and it is the same "
        "substitution `sfx_library.match_sfx_file` was deleted for: a "
        "near match is a chooser (AGENTS.md 10.5). The word is carried "
        "as written and marked, so the model reads the judgement AND the "
        "fact that it is off-schema.",
}

def _rows(cut_decisions):
    """The list of per-cut records, however the answer was shaped.

    The model may return the list itself or a dict wrapping it under its
    own key, and both have been seen from ``present_llm_step``.  Anything
    that is not a list of dicts contributes nothing.
    """
    if isinstance(cut_decisions, dict):
        cut_decisions = cut_decisions.get("cut_decisions")
    if not isinstance(cut_decisions, list):
        return []
    return [row for row in cut_decisions if isinstance(row, dict)]


def _position(row):
    """The cut this row is about, as ``cuts_toon`` spells it.

    ``cut_point_position`` is the name the table uses and the one the
    manifest asks for.  ``spine_block_position`` is accepted because it
    is the same identifier under the name every OTHER plan in this
    pipeline gives it - the A-roll assignments, the SFX plan and the
    transition plan all key on it - and a reviewer reaching for it is
    naming the same cut, not a different one.  ``to_block`` is the name
    001's own run used: a cut is stated as ``from_block`` -> ``to_block``,
    and the block a cut leads INTO is exactly what ``cuts_toon`` keys on.
    """
    for key in ("cut_point_position", "spine_block_position", "to_block",
                "position"):
        value = row.get(key)
        if value is not None and value != "":
            return str(value)
    return None


def verdicts_by_cut(cut_decisions) -> dict:
    """Map ``str(cut_point_position)`` -> ``{verdict, note, recognised}``.

    A row naming no cut is not placed - there is no cut it could be
    about.  A later row for a cut an earlier one already named wins, the
    way the last thing an answer says about something is what it means.
    """
    placed = {}
    for row in _rows(cut_decisions):
        position = _position(row)
        if position is None:
            continue
        verdict = row.get("verdict")
        if verdict is None:
            verdict = row.get("rating", row.get("narrative_verdict"))
        verdict = "" if verdict is None else str(verdict).strip()
        note = row.get("why")
        if note is None:
            note = row.get("note", row.get("rationale", ""))
        placed[position] = {
            "verdict": verdict,
            "note": _one_line(note),
            "recognised": verdict.lower() in VERDICTS,
        }
    return placed


def _one_line(note) -> str:
    """A cell is a row, so a note has to be one line (AGENTS.md 10.1)."""
    if not isinstance(note, str):
        return ""
    return " ".join(note.split())


def verdict_of(placed: dict, position) -> str:
    """The verdict for one cut, or **None** when the review judged none.

    None, never :data:`VERDICTS`\\ [0].  A reader that wants something to
    print asks :func:`verdict_column`.
    """
    row = placed.get(str(position))
    if not row or not row["verdict"]:
        return None
    return row["verdict"]


def verdict_column(placed: dict, position) -> str:
    """What the ``narrative_verdict`` cell says for one cut."""
    verdict = verdict_of(placed, position)
    if verdict is None:
        return UNJUDGED
    if placed[str(position)]["recognised"]:
        return verdict.lower()
    return f"{verdict} ({UNRECOGNISED})"


def note_column(placed: dict, position) -> str:
    """What the ``verdict_note`` cell says for one cut."""
    row = placed.get(str(position))
    return row["note"] if row else ""


def unjudged_summary(placed: dict, positions) -> str:
    """One line for how many of these cuts carry no verdict.

    Empty when every cut was judged.  A reader prints this rather than
    letting a table of ``unjudged`` rows read as a review that ran.
    """
    positions = [str(p) for p in positions]
    missing = [p for p in positions if verdict_of(placed, p) is None]
    if not missing or not positions:
        return ""
    return (f"unjudged: {len(missing)} of {len(positions)} cut(s) carry no "
            f"verdict from the rough-cut review - the review named no "
            f"judgement for them, which is not the judgement 'smooth'")


# ── The half of the answer that names no cut ─────────────────────────

#: One line saying what these are, printed above them.  They are the
#: review's own findings about the cut as a whole; nothing downstream is
#: addressed by them, which is the point of printing them.
UNPLACED_LEGEND = (
    "rough-cut review findings that name no single cut - printed because "
    "nothing downstream reads them and a finding with no reader is the "
    "defect this exists to close. Not a gate: none of these failed a run.")


def unplaced_findings(cut_decisions) -> list:
    """The rows of ``cut_decisions`` that are about the cut as a WHOLE.

    Everything :func:`verdicts_by_cut` could not place, verbatim and in
    the order the review wrote it.  Nothing is filtered by ``decision``,
    ``scope`` or severity: whatever picks which of the reviewer's
    findings matter becomes the reviewer.
    """
    return [row for row in _rows(cut_decisions) if _position(row) is None]


def _finding_line(row) -> str:
    """One row as one line, its own words, nothing summarised."""
    label = str(row.get("decision") or row.get("scope") or "finding")
    scope = row.get("scope")
    if scope and scope != label:
        label = f"{label} ({scope})"
    severity = row.get("severity")
    if severity:
        label = f"{label} [{severity}]"
    body = _one_line(row.get("rationale") or row.get("why")
                     or row.get("note") or "")
    return f"{label}: {body}" if body else label


def summary_lines(cut_decisions) -> list:
    """What the run summary prints about the rough-cut review.

    Empty when the review wrote nothing, so a run with no review is
    silent rather than printing a heading with nothing under it.  The
    per-cut verdicts are NOT repeated here - they reach step 4.02's own
    table, and printing both would be a summary beside the structure it
    was rendered from (AGENTS.md 10.1).
    """
    unplaced = unplaced_findings(cut_decisions)
    if not unplaced:
        return []
    lines = [f"  Rough-cut review - {UNPLACED_LEGEND}"]
    for row in unplaced:
        line = _finding_line(row)
        lines.append(f"    - {line[:400]}")
    return lines
