"""A step that measures what the model NAMED, and asks it again.

Captain's choice, put to them as a design question and answered
verbatim: *"Two-pass: summaries first, envelope for the section the model
names."*

What was wrong
--------------
``music_measurement.measure_track`` computes the twelve-bucket
``window_envelope_dbfs`` for the played window - **seconds 0 to 60 of the
file, and nothing else**.  Every other span of the track gets
``track_sections``' two scalars, a mean level and a spread.  So the
richest evidence in the step existed exclusively for the head of the
track, which is the answer the section feature was built to let the model
move AWAY from.  ``music_section.UNSUPPORTED_BY_THE_MEASUREMENTS`` says so
in as many words: *"a model can see that section 60-120 is 20 dB steadier
and cannot see whether it rises or falls across the minute"*.

Measuring twelve buckets for every section of every candidate is what
AGENTS.md 10.1 rules out - roughly 120 numbers per candidate, most of them
about sections nobody is considering.  Measuring twelve buckets for the
sections the model SAYS it is considering is a handful of numbers about
the only spans that can still win.

The shape
---------
1. Pass one is the step exactly as it was: summaries, and the model
   answers.
2. The post-bridge reads a SHORTLIST off that answer, measures the
   envelope of each shortlisted section, and asks for another pass
   carrying those measurements.
3. Pass two answers with the measurements in front of it, and that answer
   is the step's output.

**A shortlist is SEVERAL sections, and it may span several tracks.**  It
was designed that way from the start rather than retrofitted, because the
bed is a sequence (``library/tools/music_bed.py``): if the bed is three
spliced pieces the shortlist is the pieces, not the piece.

The mechanism
-------------
``run_hybrid_step`` already loops: ``post_bridge_retry`` seeds
``present_llm_step``'s ``retry_feedback`` so a CONTRACT REJECTION reaches
the model that caused it.  This is the same plumbing carrying a different
cargo - a valid answer that is incomplete BY DESIGN, rather than an
invalid one - so the block is spelled differently and counted separately.
The post-bridge asks for the pass by returning
:data:`REQUEST_KEY`; nothing else in the pipeline reads that key.

**It is BOUNDED at one extra pass** (:data:`MAX_PASSES`), and the bound is
mechanical rather than a judgement: pass two is asked with the
measurements for what pass one named, and a third would be asking again
with nothing new.  At the bound the request is IGNORED and pass two's
answer stands - unlike a contract rejection, an unanswered second pass
leaves a perfectly valid output, so failing the step would throw away a
good answer to enforce a round trip.

What the extra round trip costs
-------------------------------
One more model call on a step that made one.  The context grows by the
measurement block alone - twelve numbers and a line per shortlisted
section - and the candidate table, which is the large half, is carried
once either way because the second pass reuses the same assembled
context.  Nothing is re-measured that pass one already measured: the
per-second RMS windows of a file are read once and every section's
envelope is bucketed out of them.

``tests/test_second_pass.py``.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**The envelope is measured for the sections the model SHORTLISTS, in a second pass.**
One enumeration, `library/tools/second_pass.py`. Captain's choice: *"Two-pass: summaries first, envelope for the section the model names."* `measure_track` buckets seconds 0 to the length of the edit and nothing else, so the richest evidence described only the head of every track - the answer the section feature exists to move away from.
- Pass one offers `track_sections`' scalars; the answer's `section_shortlist` names the sections it is weighing - **SEVERAL of them, across several tracks, because the bed is a sequence**; `music_measurement.section_envelopes` measures those and only those, one decode per FILE; pass two chooses with them in front of it and that answer stands.
- **A post-bridge ASKS by returning `REQUEST_KEY`**, which is SPLIT OUT of the answer before anything validates it. The runner carries it the way `post_bridge_retry` carries a violation - **extend that path; do not build a third one** - and tells the post-bridge which pass it is on via `PASS_KEY`.
- **Bounded at one extra pass, and at the bound the request is IGNORED and the answer STANDS.** Unlike a contract rejection, an unanswered second pass leaves a valid output.
- `tests/test_second_pass.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Total model calls one step may make because of a second-pass request,
# the first included.
MAX_PASSES = 2

# The key a post-bridge returns to ask for another pass. Reserved: it is
# split out of the answer before anything validates or reads it, the same
# way `undetermined` and `contradicts_direction` are.
REQUEST_KEY = "__second_pass"

# The heading the block carries. One spelling, here, so a test can look
# for the thing the model is shown.
HEADING = "Measured for the sections you named"

MAX_BLOCK_CHARS = 6000

# The key the runner puts the current pass number under in the data a
# post-bridge reads, so a post-bridge answering pass two knows it is and
# does not ask again (or re-measure).
PASS_KEY = "__pass"


def is_final_pass(data: Dict[str, Any]) -> bool:
    """Whether asking for another pass would be refused anyway."""
    try:
        return int((data or {}).get(PASS_KEY, 1)) >= MAX_PASSES
    except (TypeError, ValueError):
        return False


class SecondPassError(ValueError):
    """A second pass was requested in a shape the runner cannot act on."""


def request(reason: str, context: str) -> Dict[str, Any]:
    """What a post-bridge returns to ask for another pass."""
    return {REQUEST_KEY: {"reason": str(reason), "context": str(context)}}


def take(answer: Any) -> Tuple[Any, Optional[Dict[str, str]]]:
    """`(answer without the request, the request)`.

    The request is SPLIT OUT before anything validates or reads the
    answer, because it is a message to the runner and not one of the
    step's outputs - `validate_step_output` refuses an unexpected key.
    """
    if not isinstance(answer, dict) or REQUEST_KEY not in answer:
        return answer, None
    rest = {k: v for k, v in answer.items() if k != REQUEST_KEY}
    raw = answer[REQUEST_KEY]
    if not isinstance(raw, dict) or not str(raw.get("context") or "").strip():
        raise SecondPassError(
            f"a post-bridge returned {REQUEST_KEY} with no context to carry "
            f"back to the model. A pass that adds nothing is a resample, "
            f"which is what this mechanism exists to avoid.")
    return rest, {"reason": str(raw.get("reason") or ""),
                  "context": str(raw["context"])}


def block(request_body: Dict[str, str], pass_number: int) -> str:
    """The text appended to the next pass's context.

    Deliberately the same shape as `post_bridge_retry.feedback_block`:
    one heading, the verbatim material, one instruction.
    """
    body = request_body.get("context", "")
    if len(body) > MAX_BLOCK_CHARS:
        half = (MAX_BLOCK_CHARS - 20) // 2
        body = f"{body[:half]}\n  ... [elided] ...\n{body[-half:]}"
    reason = request_body.get("reason", "")
    return (
        f"\n\n{HEADING} (pass {pass_number}):\n"
        + (f"{reason}\n" if reason else "")
        + f"{body}\n"
        + "Answer again in the same schema, now deciding from these "
          "measurements. Everything else about the task is unchanged, and "
          "you are free to keep your previous answer where the "
          "measurements support it.\n"
    )


def carries_measurements(context: str) -> bool:
    """True when a context has already been given a second pass."""
    return HEADING in (context or "")


# ── The shortlist a music selection names ───────────────────────────

SHORTLIST_KEY = "section_shortlist"


def read_shortlist(selection: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The sections the model says it is CONSIDERING, in the order given.

    Several sections, possibly across several tracks - that is the shape,
    not a special case.  A malformed row RAISES: a shortlist is what the
    second pass measures, and quietly measuring fewer sections than the
    model named would hand it back a smaller menu than it asked for.
    """
    if not isinstance(selection, dict):
        return []
    raw = selection.get(SHORTLIST_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SecondPassError(
            f"music_selection.{SHORTLIST_KEY} must be a list of sections you "
            f"are considering; got {type(raw).__name__}")
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise SecondPassError(
                f"music_selection.{SHORTLIST_KEY}[{index}] must be an object "
                f"naming source_in and source_out; got "
                f"{type(row).__name__}")
        start, end = row.get("source_in"), row.get("source_out")
        for name, value in (("source_in", start), ("source_out", end)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SecondPassError(
                    f"music_selection.{SHORTLIST_KEY}[{index}].{name} must be "
                    f"a number of seconds; got {value!r}")
        if float(end) <= float(start):
            raise SecondPassError(
                f"music_selection.{SHORTLIST_KEY}[{index}] runs from "
                f"{start} to {end}, which is no time at all")
        out.append({
            "track": str(row.get("track") or "").strip(),
            "source_in": round(float(start), 2),
            "source_out": round(float(end), 2),
            "why": str(row.get("why") or "").strip(),
        })
    return out
