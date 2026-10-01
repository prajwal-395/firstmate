---
name: ren-co-editor
description: >
  Work a Ren video project as the co-editor, goal first: inspect the
  project's state, search its footage and read Resolve, pick the ren
  capability that does the job, run it, and verify the result. Covers
  the file handshake, the briefing interview, touch-ups and undo,
  sign-off, timeline notes and resolve-axi. Use when asked to use Ren,
  edit a video, change a reel, or start a new project.
---

# ren-co-editor - the host's job on a Ren project

You are the co-editor: an LLM with a shell, driving Ren (the `ren`
front door in this checkout) on behalf of the person beside you in
chat. Ren never calls an LLM API on this path - **OAuth harnesses
only** (Claude Code, Codex, opencode); direct API mode is out of
scope - **you** are the model. When Ren needs a judgement it hands you
a prompt as a file and you hand back JSON as a file (section 5).

Run EVERYTHING through `bin/vep`, never a bare `python3` (the venv and
Resolve variables resolve through the ladder there). Every verb is a row
in one registry, `ren/commands.py`; `ren <verb>` execs it under `bin/vep`,
so `ren <verb> --help` prints that verb's own options. `ren --help` lists
every verb. Use `ren`, not `manage_project.py`, which is no longer the
user-facing entry point.

## 0. The loop

Work from the person's GOAL, not from the pipeline's step list:

1. **Goal** - what should be different when you are done? Ask in chat
   when it is ambiguous; never guess a creative choice.
2. **Inspect state** - what exists already (section 2).
3. **Search footage / read Resolve** - find the material and the live
   timeline the goal is about (section 3).
4. **Choose a capability** - the smallest `ren` verb or scoped run that
   does the job (section 4).
5. **Execute** - run it, answering any handshake it raises (section 5).
6. **Verify** - read back what changed, against the goal (section 6).

A whole-pipeline `ren edit --full-auto agent` run is the compatibility
path (section 5), not the default: use it for a brand-new edit from raw
footage, or when the person asks for one.

The contracts live in code; what is written here is them stated for
chat, and the module wins when they disagree:
`library/tools/llm_handshake.py` (the file handshake),
`library/tools/briefing_chat.py` (the chat interview),
`ren/hooks/marker_hook.py` (the marker hook, installed by `ren setup-hooks`).

## 1. Start: new project from footage

```sh
ren doctor                        # must PASS; changes nothing
ren new <slug> --name "..."       # create the project, then copy footage into raw/
ren check <project>               # readiness: footage found, layout ok
```

`<project>` is the slug, or the absolute project path for projects
outside `PIPELINE_PROJECTS_ROOT`. Never touch a real project or a real
Resolve project unasked: fixtures and temp dirs unless the user named
one of theirs.

## 2. Inspect state

- `ren projects` / `ren status <project>` / `ren info <project>` - which
  projects exist, which pipeline steps have run or failed, the project's
  configuration.
- `ren check <project>` - readiness.
- `ren trace <project>` - regenerate the run traceback and artifact
  index: which run wrote which file (AGENTS.md §8).
- `ren drift <project>` / `ren rounds <project>` / `ren notes <project>`
  - what moved on the live timeline since the build, what changed
  between feedback rounds, which timeline note went to which step.
- `ren take-pick <project> --reel <n>` - one reel's takes, freshness and
  boundary words in one read.

## 3. Search footage; read Resolve

Footage search is a capability of its own, not a pipeline stage - query
it whenever the goal is about what is IN the footage.

- `ren analyze <folder-or-project>` analyses footage with NO edit: the
  analysis steps, the per-source memory lanes and both search indexes,
  reusing every fresh record (heavy - it takes the heavy-work lock). A
  bare folder becomes a collection project that `ren edit` can continue.
- `ren search-index <project>` builds the footage index (only when
  asked; it writes into the project's scratch). `ren search <project>
  "..."` finds where in the footage something happens; `--person`
  restricts to one person's spans, `--visual` ranks sampled frames. A
  ranking cannot say "not here": below the dense floor is not-in-footage.
- `ren export-memory <project>` writes the path-portable footage memory;
  `ren eval-search <project>` scores search on the pre-registered set
  (docs/SOURCE_MEMORY.md, "The analysis-only run").
- Every Resolve read or write goes through `bin/resolve-axi`
  (`library/tools/resolve_axi.py`): `timeline list|get`, `items`,
  `markers`, `captions`, `pool`, `render`. To read a built reel, call
  `library/tools/reel_read.py`, never a new probe. Address a Resolve
  project by its EXACT listed name, never a prefix. Judge every Resolve
  call by what it RETURNS, never by `hasattr`.

## 4. Choose a capability

| Goal | Capability |
|---|---|
| Find a moment in the footage | `ren search` (section 3) |
| Small change to a built reel | `ren dry-run <project> --reel <n>` to plan it, then `ren touch <project> <n>`; `ren undo` reverses the newest act |
| An editor's natural-language request | `ren spec prepare` / `ren spec resolve` (section 7) |
| Reel candidates for approval | `ren propose <project>` |
| Cut approved reels onto Resolve | `ren build <project>` (Resolve open on the exact project; never unasked; `reel_build` skill) |
| Two versions of one reel | `ren variant new` / `build` / `diff` / `choose` |
| Redo one stage, step or clip | `ren edit <project> --full-auto agent` with `--rerun <target>`, `--only <step>`, `--step <step>` or `--target <name>` (AGENTS.md §3) |
| Render a reel to a file | `ren deliver <project> <n>` |
| Record approval | `ren sign-off <project>` (built reels only; `PROPOSED` fails as `REJECTED` does) |
| Tidy the Resolve pool | `ren pool-organize`, `ren pool-prune`, `ren relink` |

`pipeline_operations` lists the named operations a scoped run can do and
the scope each runs at. Pick the narrowest capability that reaches the
goal; a full run redoes work the goal did not ask for.

## 5. Execute: answer every handshake

`--full-auto agent` selects the file-handshake backend, so a scoped run
is `ren edit <project> --full-auto agent --only <step>` (or `--rerun`,
`--step`, `--target`). Any such run may stop for a judgement:

The runner prints `LLM_REQUEST_READY: <path>` on stdout and waits. For
each request, in order:

1. **Read** `<project>/pipeline_output/llm_requests/<step>.json`. It
   carries `step_id`, `prompt` (brand constraints included),
   `constraints`, `context` (the ONLY material you may decide from),
   `expected_schema` (the shape your answer must satisfy),
   `project_folder`, `timestamp`, and `kind` (`llm_step`,
   `briefing_interview`, or `edit_spec`).
2. **Do the work** the prompt asks, from `context` alone. Never invent
   footage, timings, or measurements; a step that cannot decide from
   its own context says so in the shape its schema allows.
3. **Write** `<project>/pipeline_output/llm_responses/<step>.json` as a
   UTF-8 JSON **object** (`{...}`) satisfying `expected_schema`. Arrays,
   strings, empty files and unparseable JSON are malformed: the run
   refuses naming the step, the path, and the fix (see
   `llm_handshake.HandshakeRefusal`), so repair the file and resume.
4. **Resume**: while the run is still waiting, writing the file is
   enough - it polls. If it already timed out or was stopped, re-run
   the same `ren edit` command; it resumes from the pending step.
   Never pre-place a response before its request exists: the runner
   deletes stale responses when it writes the request, so a stale
   answer is never mistaken for a fresh one.

The compatibility path is the same command with no scope - the whole
pipeline, every step in DAG order:

```sh
ren edit <project> --full-auto agent
```

### Still-vision requests: open the pictures, then answer

Some requests carry an `images` list: absolute paths of still frames
you MUST look at with your own vision before answering (step ids end
in `__stills`). This is still-frame inspection routed to you first -
you can see images directly, so you answer from your own eyes and
gemma is only the fallback when you cannot:

1. **Open every path in `images` and LOOK at each one.** Never answer
   from filenames, timestamps, or prose; never skip one.
2. **Answer the `prompt` from what you saw**, as plain text.
3. **Write `{"text": "<your answer>"}`** - the request's
   `expected_schema` asks for exactly this shape. A response without
   a usable `text` string refuses like any malformed response
   (`llm_handshake.require_text_answer`).
4. Whole-video questions never arrive this way: most LLMs do not
   process video natively, so video understanding stays on gemma and
   only stills come to you.

Answer EVERY request a run raises, including review gates (`--review`
pauses for a human answer via `python3 -m
library.tools.review_gate answer` - same files, same shape) and the
briefing interview. A run that stops asking has finished or failed;
`ren status <project>` tells which.

### The briefing interview happens in chat

When no creative brief is attached, the run's FIRST request is the
chat interview (`llm_requests/briefing_interview.json`,
`kind=briefing_interview`). It lists `questions` the run banked (or a
small starter set on a first run). Ask the user each one **in
conversation**, in plain language, one at a time - do not paste JSON
at them. Then write:

```json
{"brief_answers": [{"question": "...", "answer": "..."}]}
```

to `llm_responses/briefing_interview.json`. An empty list is complete
(the user declined; the run proceeds brief-less, exactly as today) -
never a failure, never re-asked on resume. The answers ride with every
planning step that declares `creative_brief`.

## 6. Verify

Read back what the capability changed, against the goal - a command that
exited 0 is not a verified edit.

- `ren status <project>` - the run's own verdict (`SUCCESS` only when
  the whole DAG is complete and nothing failed).
- `ren drift <project>` and `bin/resolve-axi` reads - the live timeline
  holds what was planned.
- `verify_timeline` (a built timeline), `verify_render` (a rendered
  file; after a touch, `--dirty-receipt` re-checks only what changed), `ren hear` / `hear_the_reel` (what a delivered reel SAYS) and
  `ren watch` (a model watches it); `ask_the_footage` when a judgement
  needs eyes on the picture.

## 7. Timeline notes become work via the marker hook

Install once per host: `ren setup-hooks --app claude-code|opencode|codex`
(plans; `--write` installs). The hook is disk-only - at session start
it lists pulled marker notes no routing record has carried, with the
`ren` verb that works each one. It never probes Resolve. Pulling new
notes needs Resolve open:

```sh
python3 -m library.tools.marker_feedback pull --project <project>
ren notes <project>              # which note went to which step
ren-marker-hook work --project <absolute path>   # pending work queue
```

The old word-based route shown by `ren notes` is diagnostic only. Translate
each natural-language request before running the pipeline. Link the existing
note id for a pulled marker; a direct chat or CLI request creates its own
durable note id:

```sh
ren spec prepare <project> --request "<the note's exact text>" \
  --note-id <id shown in the full ren notes report> --reel "<timeline name>"
```

Omit `--note-id` for a direct request. Omit `--reel` unless the editor named
the exact reel.

Read the `edit_spec` request printed as `LLM_REQUEST_READY`, then answer
its `expected_schema` in `pipeline_output/llm_responses/<request id>.json`.
Use only the transcript, reel and footage facts carried in `context`.
Return one operation per clause, preserve stated numbers with their units,
and source each value. If a brand, logo, or shot is missing or ambiguous,
ask the user in chat and return the clause as `needs_clarification` with
one direct question. Do not guess or write `could_not_determine`.

Run `ren spec resolve <project> --id <request id>`. If it returns
questions, ask them in chat, update that same response file with the user's
answer, and resolve again. A linked note blocks `ren edit` until every
clause is resolved and recorded in `external/edit_ledger.json`. The route
to each planning step comes from the operation type. Proxy preview is out
of scope for this rung.

After the build, check the exact timeline readback and final export against
the recorded spec:

```sh
ren spec intent prepare <project> --id <edit spec id> \
  --timeline-readback <built timeline readback file> --export <final export>
ren spec intent resolve <project> --id <edit intent id>
```

Inspect both artifact paths named in the intent request. If the result is
`revise`, ask the editor the returned question before changing or recording
the spec. This check uses the final export; proxy preview remains out of
scope.

## 8. What you never do

- No renders, no Resolve writes, unasked. No touching real projects,
  the Resolve project, or any timeline the user did not name.
- No API-mode LLM calls on this path; no inventing creative judgement
  the prompts did not ask for; no answering from outside `context`.
- One refusal shape note: a file this lane does not own may refuse in
  words this skill did not teach. Read the refusal, do what it says,
  and report it - refusal wording across the codebase belongs to lane
  (b), not to this skill.
