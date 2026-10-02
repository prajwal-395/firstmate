# `library.tools.output_contract` - the history behind its contract

This is the module docstring of `library/tools/output_contract.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
One question, asked of every declared output: WHO READS IT.

`library/tools/input_contract.py` asks who REFUSES when a declared input
is absent.  This is the mirror, and the mirror was missing: nothing in
this repository ever asked whether a value a step PRODUCES reaches
anybody.

The defect class this exists to catch
-------------------------------------
Data computed and then not reaching where it was needed is the most
common real defect in this codebase.  Seven were found in two working
days and every one is the same shape:

* the transcriber's per-line confidence was computed and DISCARDED AT
  WRITE TIME in two places, so no transcript on disk carried one;
* `caption_content_hash` was fed rendered segments instead of caption
  cards, so it digested the segment COUNT and nothing else;
* step 3.04's allow-list was declared one level deeper than the code
  read it, so 92% of its prompt was raw data it was meant to drop;
* `enforce_min_duration` was defined, documented and NEVER CALLED;
* `duplicate_takes` detected repeated speech that nothing consumed;
* captions carried no binding to the footage they were computed against;
* the reels path read no picture at all.

Four shapes, and the reason a mirror is the right mechanism: a one-off
fix for each leaves the eighth to be found by accident.

  1. computed then dropped before it is stored
  2. stored then never read
  3. declared then not delivered
  4. delivered then silently ignored - a wrong key, a shadowed variable,
     a rebuilt dict that omits fields

Shape 3 is `input_contract`'s and `requirements`'s.  Shapes 1, 2 and 4
are this module's, and 2 is the one a manifest can answer mechanically:
**a declared output with no reader.**

The routes, and why there are three
-----------------------------------
An output is CONSUMED when at least one route carries it.  There are
three, and leaving any of them out makes the survey fail correct output
- which AGENTS.md 10.4 calls the same defect as a gate that cannot fail,
from the other side.

* ``edge``       - a `data_mapping` on an edge OUT of the producing node
  names it.  The strongest route: `run_pipeline.gather_step_inputs`
  raises when the key is missing, so the consumption is enforced.
* ``own prompt`` - the producing step's OWN `bridge.py` emits it, or its
  `context_fields` names it.  `run_pipeline.project_step_context`
  restores bridge-supplied keys BY NAME after projection, precisely so a
  pre-bridge's one table cannot be projected away.  Six outputs travel
  only this way (`cuts_toon`, `vfx_candidates_toon`,
  `sfx_candidates_toon`, `topics_toon`, `transcripts_toon`,
  `reel_candidates`) and a survey blind to it would report all of them.
* ``code``       - a module outside the producing step's own directory
  READS the key: `d.get("k")`, `d["k"]`, `"k" in d`, `d.pop("k")`, or
  the key inside a list handed to a call such as `require_keys(data,
  [...])`.  `compile_manifest` reads `pipeline_data.json` directly
  rather than taking the edges' word (AGENTS.md 10.1), so this route is
  not optional either.

  It is a READ position and not a bare name match, and that distinction
  found six more unread outputs than the first survey did.  A key
  appearing as a dict-literal KEY is a WRITE (`{"total_files": total}`
  in an unrelated module credited `scan.total_files`); as a comparison
  operand it is a carve-out, not a read (`key != "total_failed"` in
  `run_pipeline.validate_step_output` was `temporal_index.total_failed`'s
  ONLY credit); and as a call argument it is usually a step id
  (`StepDir("ocr_extraction", ...)` credited `ocr_extraction`'s output
  while `run_scope.DESELECTED_BY_DEFAULT` said in as many words that
  nothing consumes it).

What this survey CANNOT see, stated rather than left implicit
-------------------------------------------------------------
Even in read position a key-name match can land on an unrelated dict,
and two keys in this tree are too generic to survive that:
`temporal_index.source` and, until it was deleted,
`creative_cohesion.step`.  `KNOWN_NAME_COLLISIONS` is where those are
recorded, and it SUBTRACTS the credit rather than apologising for it -
an entry there means "these readers are not reading this output", so
the output falls back into the unread set and has to be adjudicated
like any other.  A collision entry whose output has no readers left is
stale and `disagreements` says so.

It cannot see a value that only reaches `summary.md`.
`step_exporter.generate_summary` renders whatever keys an output
happens to carry, generically, so every output is "rendered" and no
output is READ that way.  A count that reaches only the summary is
REPORTED, not consumed, and `REPORTED_NOT_CONSUMED` is where that is
said.

And `run_pipeline.validate_step_output` is NOT a consumer, though it
touches every declared output there is.  It checks that a declared
output is present, of the declared type and not empty - the mirror of
`input_contract` at the producing end, a check on the DECLARATION being
honoured rather than a use of the value.  Crediting it would make every
output consumed by construction, which is a gate that cannot fail
(AGENTS.md 10.4).  Its one name-shaped read, `key != "total_failed"`,
is an exclusion from a generic rule and is treated as the write it is.

And it is an OUTPUT-level question.  A field INSIDE an output that
nothing reads - which is what the confidence and the caption hash both
were - is not visible here.  `uncalled_functions` is the one field-level
half that is mechanical: a function defined and never called is shape 1
with a name, and it is what `enforce_min_duration` was.

**`library/tools/data_map.py` is the field-level half**, and
`library/tools/field_flow.py` is what makes it possible: it follows the
VALUE rather than matching the name, which is the only way to ask this
question of `start`, `end`, `text` or `clip_id`.  It also covers the
data that is not a declared step output at all - the 39 documents in
`data_map.DOCUMENTS`.  `docs/DATA_MAP.md` is the prose half, and
`tests/test_data_map.py` cross-checks the two surveys against each
other: every output this one reports as carried by an edge must have at
least one FIELD with a reader over there.

    python3 -m library.tools.output_contract          # the survey
    python3 -m library.tools.output_contract --bad    # disagreements
    python3 -m library.tools.output_contract --uncalled

`tests/test_output_contract.py`.
```
