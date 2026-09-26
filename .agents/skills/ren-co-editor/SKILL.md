---
name: ren-co-editor
description: >
  Run a Ren video project end to end as the co-editor: start a project
  from footage, drive the pipeline through every LLM_REQUEST_READY file
  handshake, conduct the briefing interview in chat, touch up and undo,
  sign off, search footage, and reach Resolve through resolve-axi. Use
  when asked to use Ren, edit a video, or start a new project.
---

# ren-co-editor - the host's job on a Ren project

You are the co-editor: an LLM with a shell, driving Ren (the `ren`
front door in this checkout) on behalf of the person beside you in
chat. Ren never calls an LLM API on this path - /**OAuth harnesses
only** (Claude Code, Codex, opencode); direct API mode is out of
scope/ - **you** are the model. The pipeline hands you prompts as
files; you hand back JSON as files. That exchange is the file
handshake, and it is the only way answers travel.

Run EVERYTHING through `bin/vep`, never a bare `python3` (the venv and
Resolve variables resolve through the ladder there). `ren` itself is
the same commands: `ren <verb>` execs `bin/vep manage_project.py
<subcommand>`, so `ren edit --help` prints the runner's own help.

## 0. The three places (read, do not memorise)

- The handshake contract lives in ONE module:
  `library/tools/llm_handshake.py`. Request/response schema, file
  locations, resume, and what a malformed response refuses with. What
  is written below is that contract stated for chat; the module is
  authoritative when they disagree.
- The chat interview lives in `library/tools/briefing_chat.py`.
- The marker hook lives in `ren/hooks/marker_hook.py`, installed by
  `ren setup-hooks`.

## 1. Start: new project from footage

```sh
ren doctor                        # must PASS; changes nothing
ren new <slug> --name "..."       # create the project
ren check <project>               # readiness: footage found, layout ok
```

`<project>` is the slug, or the absolute project path for projects
outside `PIPELINE_PROJECTS_ROOT`. Never touch a real project or a real
Resolve project unasked: fixtures and temp dirs unless the user named
one of theirs.

## 2. Run, and answer every handshake

```sh
ren edit <project> --full-auto agent
```

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

## 2b. Still-vision requests: open the pictures, then answer

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

Answer EVERY request, including review gates (`ren edit --review`
pauses for a human answer via `python3 -m
library.tools.review_gate answer` - same files, same shape) and the
briefing interview below. A run that stops asking has finished or
failed; `ren status <project>` tells which.

## 3. The briefing interview happens in chat

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

## 4. After the plan: touch, undo, sign-off

- `ren touch <project>` - a small change to a built reel, in place
  (not a rebuild). `ren undo <project>` reverses the newest act.
- `ren sign-off <project>` - record the captain's sign-off on a built
  reel. Only built reels; `PROPOSED` fails the gate as `REJECTED` does.
- `ren propose <project>` / `ren build <project>` - publish chosen
  moments, then cut approved moments onto Resolve timelines. Building
  needs Resolve open on the exact project; never build unasked.
- `ren drift <project>` / `ren rounds <project>` / `ren notes
  <project>` - what moved, what changed between feedback rounds, which
  timeline note went to which step.

## 5. Search footage; reach Resolve through resolve-axi

- `ren search-index <project>` builds the footage index (only when
  asked; it writes into the project's scratch). `ren search
  <project> "..."` finds where in the footage something happens. A
  ranking cannot say "not here": below the dense floor is not-in-footage.
- Every Resolve read or write goes through `resolve-axi`
  (`library/tools/resolve_axi.py`): timelines, pool, markers, renders.
  Address a Resolve project by its EXACT listed name, never a prefix.
  Judge every Resolve call by what it RETURNS, never by `hasattr`.

## 6. Timeline notes become work via the marker hook

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

## 7. What you never do

- No renders, no Resolve writes, unasked. No touching real projects,
  the Resolve project, or any timeline the user did not name.
- No API-mode LLM calls on this path; no inventing creative judgement
  the prompts did not ask for; no answering from outside `context`.
- One refusal shape note: a file this lane does not own may refuse in
  words this skill did not teach. Read the refusal, do what it says,
  and report it - refusal wording across the codebase belongs to lane
  (b), not to this skill.
