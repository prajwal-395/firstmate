---
name: grillme
description: >
  Interview the captain about what a video should look like. Reads the
  project's brief, context folder, and learned context first, then asks
  only about the gaps - never what the project already answers. Answers
  land in the captain's context/ folder. Use when starting a video with
  an unclear look, when planning steps keep asking briefing questions,
  or when the captain asks to be grilled.
---

# grillme - the interview that fills the gaps

Conducted WITH the captain, OUTSIDE any pipeline run. Its twin
`library/tools/briefing_interview.py` is the interview that is
COLLECTED: the pipeline is not interactive, so steps write briefing
questions and the captain answers by extending the brief. grillme is
the interactive counterpart - you ask, they answer, now. The two must
not become two spellings of the same thing: collected asks for later,
grillme conducts now.

## Placement

This skill lives in `.agents/skills/`, the one skill source, and has no
entry point under `library/skills/` and no row in the pipeline-runtime
catalogue (`library/tools/pipeline_skills.py`): it is interactive (you
plus the captain), not pipeline-runtime. What it produces goes into the
captain's own area (`context/`), because their answers are their input:
you write there as their scribe, the way they would drop a file in by
hand. The pipeline never writes there (`project_layout.Kind.INPUT`).

## Procedure

### 1. Read before you ask

Run the deterministic half first - it is what keeps an answered
question from ever being asked:

```sh
python3 -c "
from library.tools import grillme
import json
project = '<project folder>'
print('## Already answered:')
print('\n'.join(grillme.known_list(project)) or '(nothing)')
print('## Gaps:')
print(json.dumps(grillme.gaps(project), indent=2))
"
```

This reads, in order: the attached brief (`brief_attachment`'s
ATTACHED reading - a declined or missing brief is itself a gap, not a
failure), every readable file in `context/`, and every active learning
in `learned_context/`. State the "already answered" list to the captain
up front, with sources - it proves you read, and it is the receipt for
every question you decline to ask.

### 2. Ask only the gaps, in the captain's words

For each gap, write ONE question per turn, in plain language, saying
why it matters (`why_it_matters` tells you what breaks without it).
Ask about what you do not know rather than reading down a list: the
gaps ARE the list, and a topic already covered gets at most one
follow-up about depth ("your brief says X - is that the whole of it?"),
never a fresh question.

Practical rules:

- One topic per question. Do not batch five gaps into a survey - the
  captain answers one thing well and five things thinly.
- Offer what you already suspect ("should the bed sit under speech
  like your last video, or drive this one?") but never presume it. A
  question with only one acceptable answer is a decision wearing a
  question's clothes.
- Images and links welcome: if the captain points at a reference,
  save the file or the URL into `context/` rather than paraphrasing
  it. A look pointed at holds; a look paraphrased drifts.
- Stop when the gaps are closed or the captain says stop. An
  unanswered gap is reported ("pacing: still unanswered"), not filled
  by invention - the pipeline never invents a creative judgement
  (AGENTS.md 10.5), and neither do you.

### 3. Write their answers into their folder

Append one dated file per session - `context/grillme-YYYYMMDD.md` -
headed by what you read (`known_list` output, so the file shows its
own basis) followed by each question and the captain's answer in their
words. Their words, not your summary: this file becomes prompt context
on every later run, and a summary is where their meaning goes to be
misremembered.

```md
# grillme 2026-09-09

Read first: music (brief), series (brief), captions (context/captions.md).

## Pacing
Q: ...
A (captain): ...
```

### 4. Record what got settled

If an answer settles something future runs must not re-ask (a series
identity, a caption style, a correction of a past mistake), record it
as a learning as well as filing it:

```sh
python3 -m library.tools.learned_context <project> record \
  --kind settled_decision --read-by mesh_spine plan_subtitles \
  --statement "..." --source-note "grillme 2026-09-09"
```

The context file is the captain's words; the learning is the
pipeline's routing (`read_by` names the steps that must act on it, and
`record` refuses a learning nothing consumes). Both, because they do
different jobs.

## What done looks like

- `grillme.gaps()` is empty, or every remaining gap is marked
  "captain declined / still unanswered" in the session file.
- One new dated file in `context/`, captain's words, basis stated.
- Settled decisions recorded as learnings with readers, not just prose.
- Nothing asked that `known_list` already answered - if the captain
  catches you asking one, that is the bug this skill exists to not have.
