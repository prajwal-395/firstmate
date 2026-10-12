---
name: ren-co-editor
description: >
  Work a Ren video project as the co-editor, intent first: the person
  says an outcome in chat ("make 2 reels about pricing from this
  episode", "the captions are too small", "ship it") and you run the
  ren verbs that deliver it, then answer with what changed and the file
  to watch. Covers new projects from footage, reels, notes and
  revisions, undo, variants, delivery, footage search, the file
  handshake, the briefing interview and resolve-axi. Use when asked to
  use Ren, edit a video, change a reel, or start a new project.
---

# ren-co-editor - the host's job on a Ren project

You are the co-editor: an LLM with a shell, driving Ren (the `ren`
front door in this checkout) on behalf of the person beside you in
chat. The person speaks outcomes and watches files. You own every
command, file, app and convention between the two. Ren never calls an
LLM API on this path - **OAuth harnesses only** (Claude Code, Codex,
opencode) - **you** are the model. When Ren needs a judgement it hands
you a prompt as a file and you hand back JSON as a file (section 5).

## What the person says -> what you do

Find the row for what the person asked, run its verbs, and reply in
their words. `<p>` is the project's slug (or its absolute path).

| What the person says | What you do |
|---|---|
| **New video** - "make a video from this footage" | `ren new <slug> --footage <the folder they gave>`, then `ren edit <slug>`; ask the briefing interview's questions in chat (section 5) |
| **Reels about X** - "make 2 reels about pricing from this episode" | `ren new <slug> --footage <the file or folder>` (skip it if the project exists), `ren edit <slug> --reels 2 --about "pricing"`, then `ren propose <slug>` and list the reels in chat: number, what each says, length |
| **Pick reels** - "keep both", "drop the second one" | `ren approve <p> 1 3` (or `--all`); `ren reject <p> 2 --note "<their reason>"` |
| **Let me see it** - "show me", "can I watch it?" | `ren build <p>` (Resolve open on the exact project; `reel_build` skill), then `ren preview <p>` and hand over the `Watch:` path |
| **A note at a time** - "the captions are too small", "at 0:12 the cut is early" | `ren revise <p> "<their exact words>"`; with a time, `ren revise <p> --at 0:12 --reel <N> "<their words>"` (section 7) |
| **Undo** - "undo that", "actually, bring it back" | `ren undo <p>` reverses the newest act; `ren redo <p> --act <id>` re-applies a reversed one (`ren undo <p> --list` shows the ids) |
| **Two versions** - "give me two versions of the opening" | `ren variant <p> new <N> --suffix ' (NAME)'`, `ren variant <p> build`, `ren variant <p> diff`; "go with the second" is `ren variant <p> choose <N> ' (NAME)' --why "<their words>"` |
| **Ship it** - "ship it", "that's the one" | `ren sign-off <p> "<reel timeline name>"` for each reel they approved (`ren reels <p>` names them), then `ren deliver <p>` (every signed-off reel) and name each file it wrote |
| **Where is X** - "where does she talk about pricing?", "where is Craig?" | `ren search <p> "pricing"`; for a person, `ren where <p> Craig` (section 3). Below the floor means "not in this footage" - say so |
| **Where is my file** - "where did it go?" | `ren reels <p>` (each reel's last delivery and build), and the `Watch:` path `ren preview <p>` prints |
| **How is it going** - "is it done?", "where are we at?" | `ren now <p>` (state plus the next action); during a run `ren progress <p>`, or `ren wait <p>` for the next thing it needs |
| **Hold on / carry on** - "hang on", "OK, carry on" | `ren pause <p>` / `ren resume <p>` |
| **An episode of a series** - "edit Night 12 of 4th Wall" | `ren series list`, then `ren new <slug> --series <id> --episode <n>` (section 1) |
| **Where were we** - a fresh chat on an old project | `ren work-state <p>`, then `ren now <p>` |

How you talk while doing it:

- **The person never types a verb, edits a file, opens a JSON or learns a
  step id or flag.** You run the verbs; anything a verb writes (the reel
  proposals, `project.yaml`, a response file) is never theirs to edit.
  A file to watch is the one path you hand over.
- **Reply with the outcome**: what changed, what to watch, and at most one
  question - with the default you will take if they do not mind.
- **Never ask what the footage, the series documents or the history
  already answer**; read them (`ren now`, `ren work-state`, `ren search`).
- **A refusal is relayed, not pasted**: its first line in plain words,
  then act on its recovery class (section 8).
- Under a chat harness `ren edit` already runs the agent backend (the
  interview fires, the handshake is yours); pass `--full-auto none` only
  when the person wants a manual run. A run that ends posts one macOS
  notification, and its last stdout line names the files it made.

The rest of this skill is the reference: the loop, every verb by goal,
and the handshake.

Run EVERYTHING through `bin/vep`, never a bare `python3` (the venv and
Resolve variables resolve through the ladder there). Every verb is a row
in one registry, `ren/commands.py`; `ren <verb>` execs it under `bin/vep`,
so `ren <verb> --help` prints that verb's own options. `ren --help` lists
every verb (`ren --help --json`: the catalog, each verb's effects).

When a pipeline run is active, poll its host-facing progress with
`ren progress <project>`, or block on `ren wait <project>` (one JSON
event: an ask, a gate, a hold, the end) instead of sleeping. Relay the
returned `message`, completed work, current work, any history-backed
estimate, and `available_capabilities` in plain language. Do not infer an
estimate when the report omits one. `ren edit <project> --detach` starts a
run in the background and returns; `ren wait` then picks it up.

## 0. The loop

Work from the person's GOAL, not from the pipeline's step list:

1. **Work state** - at the start of every fresh host session, run
   `ren work-state <project>` before asking what the person wants, and
   `ren now <project>` for the ranked next actions. This
   read-only command gives a plain-language summary; use
   `ren work-state <project> --json` when the structured fields are
   useful. It composes existing project records and makes no model call
   or Resolve connection. Treat its records as the project's durable
   history, so continuing does not depend on this chat surviving.
   Each request (a proposal or an edit run) has an assignment that the
   verbs move: `ren assignment <project>` prints its state, history and
   `resume_point.command`, which is the next verb to run. A refusal with
   recovery class `needs_question` is a question for the person. On yes,
   run the `--authorize-replacement` command it names, then repeat.
2. **Goal** - what should be different when you are done? Ask in chat
   when it is ambiguous; never guess a creative choice.
3. **Inspect state** - what exists already (section 2).
4. **Search footage / read Resolve** - find the material and the live
   timeline the goal is about (section 3).
5. **Choose a capability** - the smallest `ren` verb or scoped run that
   does the job (section 4).
6. **Execute** - run it, answering any handshake it raises (section 5).
7. **Verify** - read back what changed, against the goal (section 6).

A whole-pipeline `ren edit` run is the compatibility path (section 5),
not the default: use it for a brand-new edit from raw footage, or when
the person asks for one.

The contracts live in code; what is written here is them stated for
chat, and the module wins when they disagree:
`library/tools/llm_handshake.py` (the file handshake),
`library/tools/briefing_chat.py` (the chat interview),
`ren/hooks/marker_hook.py` (the marker hook, installed by `ren setup-hooks`).

## 1. Start: new project from footage

```sh
ren doctor                        # no required FAIL; lists what each capability can do
ren doctor --for <capability>     # what ONE operation needs (e.g. footage.search, render.build)
ren new <slug> --footage <path>   # create the project; footage is read where it lies
ren check <project>               # readiness: footage found, layout ok
```

`--footage` takes the files or folders the person handed you
(repeatable) and nothing is copied. Shape, language and speakers are
then measured from the footage, not asked. A missing projects root is
created. The `brief.md` that `ren new` scaffolds counts as no brief
until something is written into it, so the first run interviews in
chat (section 5) rather than planning blind.

When the person names a series or an episode ("episode 12 of 4th Wall"),
run `ren series list`, then `ren new <slug> --series <id> --episode <n>`:
the series document and the episode's row in its episode database ARE
the brief, attached by reference, so do not ask what the row already
says. `ren series show <id> --episode <n>` prints the row, its status
and what the interview would ask. A series not listed is registered once
with `ren series add <id> --dir <series folder>`. Ren reports the
episode's status (Filmed -> Edited) and never writes the database; tell
the person to update it.

`<project>` is the slug, or the absolute project path for projects
outside `PIPELINE_PROJECTS_ROOT`. Never touch a real project or a real
Resolve project unasked: fixtures and temp dirs unless the user named
one of theirs.

## 2. Inspect state

- `ren now <project>` - state plus ranked next actions in one read;
  `ren reels <project>` - every reel's approval, build, sign-off and
  delivery.
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
- `ren where <project> <person>` lists that person's region tracks
  (`--at <seconds>`: the region at that moment), so "where is Craig?"
  is answered off the footage memory, never a guess.
- `ren export-memory <project>` writes the path-portable footage memory;
  `ren eval-search <project>` scores search on the pre-registered set
  (docs/SOURCE_MEMORY.md, "The analysis-only run").
- Every Resolve read or write goes through `bin/resolve-axi`
  (`library/tools/resolve_axi.py`): `timeline list|get`, `items`,
  `markers`, `captions`, `pool`, `render`. To read a built reel, call
  `library/tools/reel_read.py`, never a new probe. Address a Resolve
  project by its EXACT listed name, never a prefix. Judge every Resolve
  call by what it RETURNS, never by `hasattr`.
- Every native-engine read goes through `bin/native-axi`
  (`library/tools/native_axi.py`): `model` (a Project Model's clips,
  edits, renders + counts), `timeline` (a FilmCraft project's clips),
  `masks` (masks + effects), `ops` (a projection as effectcraft-cli
  command lines), `swap-plan` (a swap-back plan from measured numbers).
  Never dump a whole model, projection or inspect JSON to answer a
  summary question; never hand-roll the op-to-CLI mapping.
- When a Resolve read has a measured queue delay and you have independent
  file or planning work, submit it without holding the agent on the
  receipt. Keep this for reads with real waiting; near-zero-wait reads
  should stay synchronous:

  ```sh
  bin/resolve-axi --submit pool "Footage/Day 1"
  ```

  Save the returned `id`, continue only with work that does not depend on
  that read, then collect it with `ren resolved result <id> --wait 60`.
  Repeat the result command if its state is still `queued` or `running`.
  Resume dependent work only after `done`; a `failed` or `rejected` receipt
  means the read did not produce an answer. This works for any
  `resolve-axi` command, including the measured `pool` and `captions`
  reads.

## 4. Verb reference: choose a capability

| Goal | Capability |
|---|---|
| State plus the next action, in one read | `ren now <project>` (`--json` for the structure); `ren reels <project>` for every reel's approval, build, sign-off and delivery |
| Find a moment in the footage | `ren search` / `ren where` (section 3) |
| Small change to a built reel | `ren dry-run <project> --reel <n>` to plan it, then `ren touch <project> <n>`; `ren undo` reverses the newest act, `ren redo` re-applies it |
| An editor's note ("the captions are too small", "move the price answer first") | `ren revise <project> "<note>"`, or `--at 0:12 --reel <n>` for a moment in a delivered file (section 7) |
| "N reels about X" from footage | `ren edit <project> --reels N --about "X"`: hears the transcript from the footage when no Resolve cut exists (reused on later runs), selects and judges reels only; then `ren propose <project>` |
| Reel candidates for approval | `ren propose <project>` |
| Rule on the proposed reels | After `ren propose`, show the list in chat and approve what the person says: `ren approve <project> 1 3` (or `--all`), `ren reject <project> 2 --note "why"` |
| Cut approved reels onto Resolve | `ren build <project>` (Resolve open on the exact project; never unasked; `reel_build` skill) |
| "Let me see it" after a build or a revision | `ren preview <project>` (renders only when asked; an unchanged reel is a cache hit) and hand the person the `Watch:` path it prints |
| Two versions of one reel | `ren variant <project> new` / `build` / `diff` / `choose` |
| Record approval | `ren sign-off <project> "<reel timeline name>"` (built reels only; `PROPOSED` fails as `REJECTED` does) |
| Render reels to files | `ren deliver <project>` (every signed-off reel, with a receipt and QC; the folder is revealed) or `ren deliver <project> <n>` for one |
| Pause or release a run | `ren pause <project>` / `ren resume <project>`; a staged timeline held from the sweep: `ren holds release <project> <timeline>` |
| Redo one stage, step or clip | `ren edit <project>` with `--rerun <target>`, `--only <step>`, `--step <step>` or `--target <name>` (AGENTS.md §3) |
| Tidy the Resolve pool | `ren pool-organize`, `ren pool-prune`, `ren relink` |

`pipeline_operations` lists the named operations a scoped run can do and
the scope each runs at. Pick the narrowest capability that reaches the
goal; a full run redoes work the goal did not ask for.

## 5. Execute: answer every handshake

`--full-auto agent` selects the file-handshake backend, and it is the
default whenever a chat harness drives `ren edit` (the run says so on
stderr). A scoped run is `ren edit <project> --only <step>` (or
`--rerun`, `--step`, `--target`). A missing producer is added to the
run and named in one line, so you do not chase step ids. Any such run
may stop for a judgement:

The runner prints `LLM_REQUEST_READY: <path>` on stdout and waits. For
each request, in order:

1. **Read** it with `ren asks <project>`: every owed request, grouped
   by prompt, each shared text printed ONCE (a reels batch asks the
   same prompt and roster of every reel). Do not read the request JSON
   raw: on disk a request carries `prompt_ref` (`{sha256, path}` of a
   shared `llm_requests/prompts/<sha256>.md`), not the prompt, and
   constant context tables are cut out into `context_refs`. What you
   answer is the prompt, then `constraints`, then `context` with every
   `<<shared HASH>>` read as that text - exactly
   `llm_handshake.read_request`. A request also names its
   `expected_schema` (the shape your answer must satisfy), `step_id`,
   `project_folder`, `timestamp` and `kind` (`llm_step`,
   `briefing_interview`, or `edit_spec`). `--json` gives the same.
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
pauses for a human answer via `bin/vep -m
library.tools.review_gate answer` - same files, same shape) and the
briefing interview. A run that stops asking has finished or failed;
`ren status <project>` tells which.

### The briefing interview happens in chat

When no creative brief is attached - or a series episode's row has an
empty cell, which is then the only thing asked - the chat interview
(`llm_requests/briefing_interview.json`, `kind=briefing_interview`)
starts after the quick footage catalog is committed and before deeper
analysis. The request includes a compact inventory, scoped intent, and
only unresolved, decision-changing questions. State the visible assumed
default, ask about goals or outcomes rather than editing methods, and
let the creator correct methods later. Ask in conversation, one at a
time - do not paste JSON at them. Then write:

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
  `ren watch` (a model watches it; `--dirty-receipt` draws only the
  picture a touch changed, as the conformance verifier's grades only the
  reel it touched); `ask_the_footage` when a judgement
  needs eyes on the picture.

## 7. Timeline notes become work via the marker hook

Install once per host: `ren setup-hooks --app claude-code|opencode|codex`
(plans; `--write` installs). The hook is disk-only - at session start
it lists pulled marker notes no routing record has carried, with the
`ren` verb that works each one. It never probes Resolve. Pulling new
notes needs Resolve open:

```sh
ren notes pull <project>          # collect new notes off Resolve
ren notes <project>              # which note went to which step
ren-marker-hook work --project <absolute path>   # pending work queue
```

The old word-based route shown by `ren notes` is diagnostic only. A note -
typed in chat or pulled off a marker - becomes a change through ONE verb:

```sh
ren revise <project> "<the note's exact words>" [--reel "<timeline name>"]
ren revise <project> --at 0:12 --reel <N> "<the note>"  # a moment in a delivered file
ren revise <project> --note-id <id from ren notes>     # a pulled marker
ren revise <project>               # continue the newest open revision
```

It prints JSON; relay its `reply` line to the person. `status` says what
happened next:

| `status` | What you do |
|---|---|
| `awaiting_translation` | Answer the `LLM_REQUEST_READY` file exactly as below, then run `ren revise <project>` again |
| `question` | Ask the person `reply` (it is exactly one question), then `ren revise <project> --answer "<their words>"` |
| `patched` / `touched` | Done: the reel was changed in place; `reply` names the reel, rows and seconds |
| `rebuilt` | Done: the owning steps re-ran and only that reel was rebuilt |
| `incomplete` | The rebuild stopped; fix what it printed, then `ren revise <project>` |
| `refused` | Nothing changed; tell the person `reply` |

A change to things already on a reel (a caption's or overlay's size or
position, an overlay off, a marker) translates as an `edit_program` over the
`reel_timeline` items the request carries; Ren generates the patch and its
scope, applies it guarded (on Ren Qualification) or as a `ren touch`
elsewhere, and records the verified outcome. A change to what plays or in
what order translates as clauses, and Ren re-runs only their owners and
rebuilds only that reel. `--plan` stops after showing what would change.
Changing a signed-off reel asks first (`needs_question`), like a rebuild.

The steps `ren revise` composes remain available one by one. Link the
existing note id for a pulled marker; a direct chat or CLI request creates
its own durable note id:

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

Before recording or executing a translated request, show its proposal with
`ren spec proposal <project> --id <request id>`. Present the planned edits,
their anchors and any categories the typed operations prove will stay
untouched. If the creator corrects the plan, update the same response and
show the proposal again before `ren spec resolve` or a build. Then run
`ren spec resolve <project> --id <request id>`. If it returns questions,
ask them in chat, update that same response file with the user's answer,
show the proposal again, and resolve again. A linked note blocks `ren edit`
until every clause is resolved and recorded in `external/edit_ledger.json`.
The route to each planning step comes from the operation type. After the
revision, report the result and its `ren commits <project> --show <commit>`
creative summary: changed regions, jump timecodes when the history has a
recorded frame rate, and category status. Unknown means the timeline history
cannot prove that category stayed untouched. For something to watch, run
`ren preview <project> --reel <N>` and hand over its `Watch:` path.

After the build, check the exact timeline readback and final export against
the recorded spec:

```sh
ren spec intent prepare <project> --id <edit spec id> \
  --timeline-readback <built timeline readback file> --export <final export>
ren spec intent resolve <project> --id <edit intent id>
```

Inspect both artifact paths named in the intent request. If the result is
`revise`, ask the editor the returned question before changing or recording
the spec. This check uses the final export, never a preview.

## 8. What you never do

- No renders, no Resolve writes, unasked. No touching real projects,
  the Resolve project, or any timeline the user did not name.
- No API-mode LLM calls on this path; no inventing creative judgement
  the prompts did not ask for; no answering from outside `context`.
- When `ren` refuses, relay the first-line user-facing consequence in
  plain language. Use its recovery class to decide what happens next:
  `auto_recoverable` means perform only the named safe recovery,
  `needs_question` means ask the smallest question in the message, and
  `blocked` means explain what must be resolved before continuing. Do
  not expose the technical `why` and `fix` unless the editor asks; keep
  them available for that explanation. Structured refusal consumers
  read `user_message` and `recovery_class` from `RenRefusal.to_dict()`.
