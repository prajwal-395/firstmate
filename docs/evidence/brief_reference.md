# `library.tools.brief_reference` - the history behind its contract

This is the module docstring of `library/tools/brief_reference.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
A large document reaches a step as a REFERENCE, not as a copy.

`#256` wired the captain's channel brief into seven prompts on
2026-08-28 and it immediately became the largest single item in the
pipeline: 37.0%-84.3% of those seven contexts, 337,099 bytes per run,
46.9% of every byte the nine replayable steps send.  The step that picks
the bed under the whole video saw **84.3% brief, 11.7% creative
direction, 3.9% candidate data** - twenty-two times more brief than
material to choose from.  41.9% of the document (19,931 of 47,513 bytes)
is in sections no LLM planning step can act on: thumbnails, posting
cadence, the portfolio table, per-series typography, naming, growth.

The captain's ruling, 2026-08-28: *"we should just have the brief for
the LLM to be able to reference if it needs it... we can just tell it if
you need to reference something again you can look at this file and it
can grep and search."*

The models that answer this pipeline have a shell.  The degradation
investigation proved it on the run of record: the answering agent read
repository source, ran the aligner and measured audio files with ffmpeg.
A model that can run ffmpeg can read a file.

## The rule

**The map is always inline; a body is inline only when the map cannot
stand in for it.**  Applied per SECTION, in order, and to any document -
not per step, and not by a hand-tuned list of headings:

1. **The preamble is inline.**  Everything before the first heading is
   the document's own statement of what it is, and it is what tells the
   model whether to read on.
2. **A section whose body is shorter than `INLINE_WHEN_UNDER_BYTES` is
   inline.**  Quoting it costs about what describing it costs, so
   describing it is the worse trade.  This is byte economics, not taste.
3. **A section the PROJECT pins is inline.**  `pipeline.creative_brief_inline`
   in `project.yaml` names headings.  The judgement of which sections are
   about *this video* belongs to whoever owns the video; the engine does
   not guess it, and there is no default list.
4. **Everything else is a heading, a byte size, a LINE RANGE and a lede.**
   Nothing is filtered and nothing is summarised away - the whole
   document stays reachable, and the map says exactly where.

And one clause that is about the reader rather than the document:

5. **A harness that cannot read a file gets the document whole.**  A
   route the model cannot follow is a loss, not a saving.  `HARNESS_READS_FILES`
   is that enumeration and it is complete; an unknown harness raises,
   because a harness whose reach nobody has established is not a harness
   known to reach.

The line range is the load-bearing affordance.  `sed -n '19,44p' <file>`
is one command, exact, and costs the model nothing to get right - which
is the difference between a path a model *could* follow and one it does.

Nothing here decides anything creative.  The brief is prompt-only: no
`bridge.py`, no `step.py` and no post-bridge reads `creative_brief`, so
reshaping it changes what a model READS and never what the pipeline
computes.

## The documents it carries

`REFERENCED_INPUTS` is the enumeration, and it is what `present_llm_step`
walks when clause 5 has to put one back inline:

  * `creative_brief` - the captain's channel brief, seven steps, the
    document this rule was written for.
  * `footage_analysis_reference` - step 3.02's per-clip vision
    analysis, 35,813 B and **40.5% of that step's whole context**, and
    the third of three views of one analysis the step was carrying
    (#F14). Step 3.02's `bridge.py` writes it and builds the reference;
    the shape is `footage_reference.footage_document`.
  * `sfx_catalog_reference` - step 4.04's SFX catalogue, 44,575 B and
    **44.1% of that step's whole context** once the brief stopped being
    copied (#299).  Step 4.04's `bridge.py` writes the catalogue to the
    step's own directory and builds the reference; the shape of the
    document is `sfx_library.catalog_document`, which fits the mechanism
    rather than forking it - one `##` section per sound, titled with the
    exact `sfx_id` an answer has to name, opening with the sound's
    measured facts so that line becomes the map's lede.
  * `reel_diagnostics_reference` - step 3.04's per-candidate
    repeated-take evidence (`repetition_inside`, `retake_candidates`,
    `possible_retellings`), 19,850 of that step's 49,777 tokens on the
    geo podcast. Step 3.04's `bridge.py` writes it; the shape is
    `reel_diagnostics_reference.diagnostics_document`, one `##` section
    per candidate titled with its own `start-end`.

**A second document costs a row here and nothing else.**  The rule, the
map, the line ranges and the harness clause are all the same; only the
two sentences of header naming the document differ, and they are
parameters (`document_name`, `why_referenced`).


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**The captain's creative brief is one per-project declaration that reaches NINE steps, BY REFERENCE, WHEN THE PROJECT ATTACHES IT.**
`project.yaml`'s `creative_brief` - top level or under `pipeline:` - names a markdown file, and `pipeline.attach_creative_brief` is the three-state choice about sending it (§3, "A step with no creative brief ASKS").
`load_pipeline_state` reads the PATH into state ONLY when the reading is ATTACHED; `gather_step_inputs` reads the FILE and hands the step a REFERENCE to it, and only if the step's own manifest declares the input.
A path that cannot be read or is empty RAISES. **A run that attaches none INTERVIEWS rather than going quiet.**
- Seven of them - `creative_direction`, `speech_sequence`, `music_selection`, `select_broll`, `plan_transitions`, `plan_vfx` and `plan_sfx` - are the ones whose handoffs tell the model to read one. `tests/contracts/test_context_contracts.py` fails if a handoff documents a brief its manifest does not declare.
- **The eighth is `mesh_spine`, and it is declared without a handoff line.** A step gets the brief because its manifest asked, not because its prompt mentions one. **The ninth is `color_grade`**, added when 5.01 grew a handoff on 2026-09-03 - its new handoff DOES name the brief, so it is in the first group; 001's brief carries a whole "Color System Philosophy" section no colour step had ever seen. [why](docs/RULE_EVIDENCE.md#the-brief-is-paid-seven-times)
- It is not a `context_fields` entry. Like `brand_template` it is restored around the projection BY NAME, so a step's allow-list neither has to list it nor can drop it.
- **The cost is per step, not per run.** [why](docs/RULE_EVIDENCE.md#the-brief-is-paid-seven-times)

**A reference is an ABSOLUTE PATH plus a MAP, and the rule for what still travels inline is in `library/tools/brief_reference.py`.**
[why](docs/RULE_EVIDENCE.md#the-brief-was-copied-seven-times)
- **The map carries a LINE RANGE per heading**, so following it is one `sed -n 'a,bp'` and not a search.
- **The rule is per SECTION, not per step**, so every step sees the same document: the preamble inline, a section under `INLINE_WHEN_UNDER_BYTES` inline, everything else a heading, a size, a range and a lede. Nothing is filtered or summarised away - the whole document is at the path.
- **Which sections are about THIS video is not the engine's judgement.** A project pins sections inline with `pipeline.creative_brief_inline` in its `project.yaml`, and there is no default list.
- **The mechanism carries FOUR documents, and a fifth costs a row.** `brief_reference.REFERENCED_INPUTS` is that enumeration - the brief, step 4.04's SFX catalogue, step 3.02's per-clip vision analysis (`library/tools/footage_reference.py`) and step 3.04's per-candidate repeated-take evidence (`library/tools/reel_diagnostics_reference.py`). Do not build a second by-reference mechanism.
- **`HARNESS_READS_FILES` is a complete enumeration and an unknown harness raises.** `agent` and `mock` reach a file; `api` does not, so under `api` the document is carried whole - a route the model cannot follow is a loss, not a saving. `present_llm_step` does that restore.
- `tests/unit/context/test_brief.py` FOLLOWS the reference rather than asserting its shape: it parses the path and the range out of the string the model reads and requires that what comes back was not in the prompt.
```

## The brief that never reached a prompt

Moved from the module docstring of `tests/contracts/test_context_contracts.py`.

Seven handoffs carried a paragraph telling the LLM to "read it in full
before making any creative decisions", and for the whole life of the
project not one step ever received one. It was broken in three
independent places at once - the loader gated on a manifest declaration
nobody had written, the key was in no whitelist so it could not reach
`inputs` anyway, and the process manifest had no entry to fall back on -
so writing `creative_brief:` into a `project.yaml` did nothing at all,
silently. See docs/RUN_001_END_TO_END.md section 5.

That is why those tests assert the CONTENT of the file lands in the text
handed to the model. A test that the key exists, or that the path is
carried, would have passed throughout the entire period the feature did
not work: the old code left the *path string* in `inputs["creative_brief"]`
whenever the file could not be read, so a step could "have a brief" that
was a filename. Since the brief travels as a REFERENCE, "the content lands"
is asserted by FOLLOWING the reference (`reference_path`) and requiring that
what it opens is the declared brief.

`mesh_spine` was the eighth consumer the two audits kept finding (round 2
F7, round 3 B9/R9): it sets every gap length and every `music_behavior`
and was the only planning step with no brief. Its manifest declared the
input while its `handoff.md` was frozen; the freeze lifted 2026-09-09 and
the prompt now names the brief too.

## Why the brief travels as a reference (test module history)

Moved from the module docstring of `tests/unit/context/test_brief.py`.
`#256` wired the captain's 47,903-byte channel brief into seven prompts
and it became 37.0%-84.3% of each of them - 46.9% of every byte the
pipeline's replayable steps send, with 41.9% of the document in sections
no LLM planning step can act on. The load-bearing property of the
replacement is REACHABILITY: a step that can no longer find the brief is a
regression, not a saving, so those tests FOLLOW the map (path and line
range parsed out of the reference, the printed command actually run)
rather than asserting its shape.
