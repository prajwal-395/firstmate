# Project 001, clean run of 2026-08-26: the reasoning behind each creative decision

These ten files are the **reasoning log** of a full `--full-auto agy` run of project 001,
written by the agent that answered every LLM call in it.

They exist because nothing in this pipeline has ever captured HOW a creative decision was
reached. `pipeline_output/llm_responses/` holds the answers and the runner records no
thinking at all, so a run can be audited for what it decided and not for why.

**Each file was written BEFORE that step's answer was submitted**, and covers five things:

1. What the agent actually read - including what it skimmed, ignored, or could not use.
2. What it considered.
3. **What it rejected, and why** - the line that distinguishes a decision from an assertion.
4. What it was missing: anything the prompt asked it to reason about that the context did
   not supply.
5. How confident it was, and what would have changed the answer.

Where something happened after the answer was submitted - a QA rejection, a compile failure -
it is recorded as a clearly marked **POSTSCRIPT** rather than folded back into the reasoning,
so a justification written afterwards is never mistaken for a reason given beforehand.

## The ten calls, in run order

| file | step | the decision |
|---|---|---|
| `creative_direction.md` | 2.01 | which of four narrative threads the video is about |
| `music_selection.md` | 2.04 | which track scores it, out of seven candidates |
| `speech_sequence.md` | 2.02 | which 46s of thirteen minutes of talking is kept |
| `mesh_spine.md` | 2.05 | where the gaps go, and where the music drops out |
| `select_broll.md` | 3.02 | which cutaway covers which gap |
| `review_rough_cut.md` | 3.03 | whether the assembled cut passes |
| `plan_transitions.md` | 4.02 | which cuts get decorated (2 of 15) |
| `plan_vfx.md` | 4.03 | why the answer is no effects at all |
| `plan_sfx.md` | 4.04 | which two moments earn a sound |
| `render.md` | 6.01 | what the delivered file actually measures |

Step 6.02 `validate` never issued an LLM request - its deterministic half fails first - so
there are ten logs for ten calls rather than eleven.

## What these are, and what they are not

These are a **run record**, in the same sense as `docs/RUN_001_END_TO_END.md`: prose about
one run, kept in the engine so it can be read. They are not project data, and the live
copies remain where the pipeline wrote them, at
`<project>/pipeline_output/reasoning/`.

The findings they contain about the pipeline itself - stub bridges, empty candidate tables,
a creative floor surviving in the output validator, a structural rule that never reaches the
step that must obey it - are collected in
[`docs/RUN_001_CLEAN_RUN_2026-08-26.md`](../RUN_001_CLEAN_RUN_2026-08-26.md).
