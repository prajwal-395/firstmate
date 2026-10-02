"""The rough-cut review's per-cut verdict, and the one reading of it.

Step 3.03 ``review_rough_cut`` is asked for two things.  ``step.py``
computes ``rough_cut_review`` - the mechanical half - and the model is
asked for ``cut_decisions``, the narrative half: Check 7 of its
``handoff.md``, a rating per transition from :data:`VERDICTS`
(``smooth`` | ``acceptable`` | ``jarring`` | ``broken``).  This module is
the one reading of that answer, and it splits it in two.

A row that names a cut goes to 4.02
-----------------------------------
:func:`verdicts_by_cut` maps ``str(cut_point_position)`` to the verdict,
its note and whether the word was recognised.  ``plan_transitions`` (4.02)
is the reader because it already holds the matching table: its pre-bridge
builds ``cuts_toon`` keyed on ``cut_point_position``, the same identifier
:func:`transition_carriers.cut_carriers` keys on, so the verdict is two
more columns (:func:`verdict_column`, :func:`note_column`) and needs no
join.  4.01 ``plan_subtitles`` is ``deterministic`` with no prompt, and
4.03 / 4.04 are not addressed per cut, so none of them is wired.  Both
columns are DEFINED in step 4.02's ``handoff.md``, including that
``narrative_verdict`` is the review's JUDGEMENT rather than a measurement.

A row is placed by ``cut_point_position``, or by ``spine_block_position``,
``to_block`` or ``position`` (``_position``): the same cut under the names
other plans, and the run of record's own ``{decision, scope, from_block,
to_block, rating, rationale}`` shape, give it.  The KEY moves; the VALUE is
untouched.  A later row for an already-named cut wins.

What this module refuses to do
------------------------------
**An unjudged cut reads as UNJUDGED, never as ``smooth``.**  A cut the
review did not name gets :data:`UNJUDGED`, spelled differently from every
vocabulary word; :func:`verdict_of` returns ``None`` and nothing coerces it
to the mild end of the scale.  :func:`unjudged_summary` says how many cuts
carry no verdict.

**An unrecognised word is carried VERBATIM and marked, not dropped and not
mapped** (:data:`UNRECOGNISED`).  Mapping ``rough`` onto ``jarring`` would
be this module deciding what the reviewer meant (AGENTS.md 10.5).
:data:`WITHDRAWN_READINGS` records the two readings considered and not
taken, with their reasons.

**Nothing acts on the verdict but the model.**  This states a fact per cut.
It does not filter, rank or re-order ``cuts_toon``, and no rule turns
``jarring`` into a transition.

A row that names no cut goes to the run summary
-----------------------------------------------
:func:`unplaced_findings` returns, verbatim and in order, every row
:func:`verdicts_by_cut` could not place - findings about the cut as a
whole.  Nothing is filtered by ``decision``, ``scope`` or severity.
:func:`summary_lines` prints them under :data:`UNPLACED_LEGEND`, the way
``qa_findings`` is printed (AGENTS.md 10.4), and is empty when the review
wrote nothing.  They are NOT routed to 4.02: a finding owned by an
upstream step is not made applicable by the transitions prompt
(``cohesion_scope.OWNED_UPSTREAM``).  A route back to the owning step does
not exist and is stated rather than quietly closed.


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

The measurements and rulings behind these rules (the unread bare-list
schema, the run of record's nineteen rows and its mis-anchor flag):
docs/evidence/cut_verdicts.md.
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
