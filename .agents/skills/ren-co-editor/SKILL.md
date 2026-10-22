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
| **What did you take this to mean** - "what did you understand?", "what have you actually done?", "did you change it yet?" | `ren assignment-state <p>` (section 7): what Ren understood, what it proposed, what it applied against what it only planned, what it could not determine, what it verified, and the open corrections |
| **Hold on / carry on** - "hang on", "OK, carry on" | `ren pause <p>` / `ren resume <p>` |
| **An episode of a series** - "edit Night 12 of 4th Wall" | `ren series list`, then `ren new <slug> --series <id> --episode <n>` (section 1) |
| **Where were we** - a fresh chat on an old project | `ren work-state <p>`, then `ren now <p>` |

How you talk while doing it:

- **The person never types a verb, edits a file, opens a JSON or learns a
  step id or flag.** You run the verbs; anything a verb writes (the reel
  proposals, `project.yaml`, a response file) is never theirs to edit.
  A file to watch is the one path you hand over.
- **Reply with the outcome**: what changed and what to watch. Ask only
  when a missing creative choice materially affects the result, and wait
  for its answer before the dependent edit.
- **Never ask what the footage, the series documents or the history
  already answer**; read them (`ren now`, `ren work-state`, `ren search`).
- **A refusal is relayed, not pasted**: its first line in plain words,
  then act on its recovery class (section 8). A refusal with recovery
  class `needs_question` carries an `ask_reason`: `ambiguous_intent`,
  `missing_authorization` or `insufficient_evidence`. It says which of
  the three genuinely demands the person - ask the smallest question
  that fits it, and ask no question at all when the reason is not one
  of the three.
- Under a chat harness, `ren edit` runs the agent handshake when a
  planning step needs your judgement. Do not choose a Resolve execution
  backend in the editorial plan. A run that ends posts one macOS
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
   `ren assignment-state <project>` is the other half of that read: what
   Ren understood about the request, what it proposed and on whose
   evidence, what it applied against what it only planned, what it could
   not determine, what it verified, and which corrections are still open.
   Its LEDGER is the task's own record: the evidence it rests on and the
   decisions it made and who approved them. Relay those six in your own
   words when the person asks what has happened; a plan is not an
   applied edit, and an applied edit is not a verified result.
   `ren work-state` and `ren now` name the TASK the project is on and
   count it in the four words the person uses - proposed, applied,
   verified, delivered - and `ren now` ranks the act that task waits on.
2. **Goal** - what should be different when you are done? Ask in chat
   when it is ambiguous; never guess a creative choice.
3. **Inspect state** - what exists already (section 2).
4. **Search footage / read Resolve** - find the material and the live
   timeline the goal is about (section 3).
5. **Choose a capability** - the smallest `ren` verb or scoped run that
   does the job (section 4). For a Resolve edit, discover the current AXI
   operation and route through `resolve-axi`; do not rely on a remembered
   command or backend list. Capability references distinguish production
   enabled operations from experimental, unqualified or unavailable ones.
   Treat a listed operation as discoverable vocabulary, not proof it can
   be used for this timeline. Use ordinary production routing; never
   request protected experimental execution to get past a qualification
   refusal.
6. **Execute** - run it, answering any handshake it raises (section 5).
   AXI prepares the semantic edit, resolves an eligible route, and
   refuses safely when the target, authorization, support or preservation
   evidence is insufficient.
7. **Verify** - read the merged AXI receipt and fresh target state against
   the goal (section 6). Report applied, verified, committed, promoted
   and delivered as distinct states; a receipt or successful command
   alone is not proof of the requested result.

The whole-pipeline `ren edit` route is legacy (section 5), not the
default: use it for a brand-new edit from raw footage, or when the person
explicitly asks for a full run. For edits to an existing Resolve timeline,
choose the narrow Ren capability and let AXI discover and route the edit.

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
- `ren assignment <project>` - the TASK for the project's open request,
  in one read. `result` is the task record: the request, the project
  state, the evidence it rests on, the decisions it made, the plan, the
  operations, the acceptance contract and the delivery. `result.task_state`
  answers what Ren understood, proposed, applied, could not determine
  and verified, plus the two verdicts: `task_state.blocking` is the
  SMALLEST thing in the way - one dependency, one criterion or one
  question, never the whole unfinished workflow - and
  `task_state.completed.complete` is the ONLY claim of done-ness. It
  is true only when the task was delivered, every acceptance criterion
  is evidenced and the delivery receipt is clean: a compiled plan, a
  submitted mutation and a written timeline are stages on the way, never
  completion. `--pause <reason>`, `--resume`, `--replan <reason>`,
  `--cancel <reason>` and `--reopen` act on that task; each names
  `--id <assignment id>`.
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
- `ren search <project> --visual --fidelity coarse` asks the CHEAP tier
  of the frame lane: the ranked candidates, which say where to look and
  not whether the shot shows it. That answer carries the ESCALATION -
  what the verified tier would establish and what it is measured to
  cost - so ask for `--fidelity detailed` (the default) only when you
  need the verdict. Every answer says which resolution produced it.
  The coarse answer is KEPT beside the index (derived data in the
  project's scratch), so a LATER `--fidelity detailed` call escalates
  from it and does not pay for the ranking again; `--no-coarse-store`
  declines to keep it.
- `ren search <project> --temporal "<question>"` asks the TYPED EVENT
  layer instead of the ranking: "the second time she opens the box"
  resolves to one event with its own interval, its uncertainty and how
  sure the reading is. It reports what it could NOT read (a pronoun it
  cannot turn into a person, an ordinal with no such repetition) rather
  than the nearest plausible moment, and a causality-like pair is
  always marked inferred. A question that names a RELATION - "what
  happened before/after/while the second time she opens the laptop" -
  is answered against the events' own intervals: the anchor is the
  event the ordinal names, and the events before, after or during it
  are the ones whose spans say so. An anchor that matches several
  events is REFUSED, not guessed.
- `ren where <project> <person>` lists that person's region tracks
  (`--at <seconds>`: the region at that moment), so "where is Craig?"
  is answered off the footage memory, never a guess.
- `ren evidence <project> <plan.json>` answers one compiled typed query
  plan against the project's canonical Project Model: measurements, event
  spans, or the model's own entity, identity-link, relation,
  interpretation, event and track rows. The answer says which RESOLUTION
  it was reached at: a claim about a span of the source is at the
  detailed tier and carries the way up to the media behind it, and a
  claim about the model's own structure is at the coarse tier.
- `ren evidence <project> --trace <entity|event|observation|track>` is the
  WHY question: it follows one thing through its observations, its
  possible identity links, its relations, the interpretations resting on
  it, and the source file and EXACT seconds behind every leaf. It names
  what produced each leaf (the derivation, the method, the licence
  context) and the contradictions that touch it. A reference the model
  does not hold is refused, never answered with the nearest match.
- `ren export-memory <project>` writes the path-portable footage memory;
  `ren eval-search <project>` scores search on the pre-registered set
  (docs/SOURCE_MEMORY.md, "The analysis-only run").
- For Resolve state or edits, load `resolve-axi` and discover the current
  operation from the live AXI registry and its generated references. Keep
  the user's editorial intent here; AXI owns target resolution, route
  selection, Resolve API/MCP/UI mechanics, transactions and recovery.
  Confirm the exact project and timeline from live state. Resolve a
  DESCRIBED target with `bin/resolve-axi target "<description>" --timeline
  "<name>"` - `id=`, `name=`, `row=V2 item=2` or `start=<frame>
  end=<frame>` - and it returns the clip plus the addressing arguments
  the mutating verbs take; it refuses when the description does not name
  one clip, so never count rows by hand. A command
  return is provisional: inspect the merged receipt's request identity,
  selected route, actual changes, verification evidence and lifecycle
  stage, then read back the requested target and effect. Add visual or
  audio evidence where structure cannot prove it. If AXI refuses, relay
  the user-facing consequence and follow its recovery class; do not force
  a backend, retry blindly or substitute a different edit. An incomplete,
  refused or unverified change is never reported as delivered. Resolve
  mechanics and their invariants live in
  `.agents/skills/resolve-axi/SKILL.md`.
- To read a built reel as a report, call `library/tools/reel_read.py`,
  never invent a new probe.
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
  that read, then collect it with `ren resolved result <id> --until-done`.
  That is the whole procedure - it waits for the job to reach a terminal
  state, so you are not asked to poll. Resume dependent work only after
  `done`; a `failed` or `rejected` receipt means the read did not produce
  an answer. This works for any `resolve-axi` command, including the
  measured `pool` and `captions` reads.

## 4. Verb reference: choose a capability

| Goal | Capability |
|---|---|
| State plus the next action, in one read | `ren now <project>` (`--json` for the structure); `ren reels <project>` for every reel's approval, build, sign-off and delivery |
| Find a moment in the footage | `ren search` / `ren where` (section 3) |
| Find WHEN it happened ("the second time she opens the box") | `ren search <project> --temporal "<question>"` (section 3) |
| Why Ren believes something, and what original evidence supports it | `ren evidence <project> --trace <entity\|event\|observation>` (section 3) |
| Small change to a built reel | `ren dry-run <project> --reel <n>` to plan it, then `ren touch <project> <n>`; `ren undo` reverses the newest act, `ren redo` re-applies it |
| An editor's note ("the captions are too small", "move the price answer first") | `ren revise <project> "<note>"`, or `--at 0:12 --reel <n>` for a moment in a delivered file (section 7) |
| What Ren understood about the request, and what it has done with it | `ren assignment-state <project>` (read only): the request's own words, the canonical intent, the ops Ren read out of them, the proposed changes and the evidence behind them, what landed against what is only planned, the uncertainties and blocked capabilities, what was verified against what was not, each open correction with the command that continues it, and the LEDGER - the task's own evidence and the decisions it made and who approved them |
| What the brief asked for, and what Ren decided | `bin/vep -m library.tools.editorial_intent <project>` prints the canonical intent: what the commission wants (its goals), what was required, what was preferred, what Ren inferred, and for each request what it acts on, what it must produce, what must not change, the latitude it was given and which evidence it still needs. `bin/vep -m library.tools.editorial_decisions check <decisions.json>` checks a decision record; `... compile <decisions.json> --project <p> --timeline <t>` compiles the APPROVED decisions into the request AXI executes |
| "N reels about X" from footage | `ren edit <project> --reels N --about "X"`: hears the transcript from the footage when no Resolve cut exists (reused on later runs), selects and judges reels only; then `ren propose <project>` |
| Reel candidates for approval | `ren propose <project>` |
| Rule on the proposed reels | After `ren propose`, show the list in chat and approve what the person says: `ren approve <project> 1 3` (or `--all`), `ren reject <project> 2 --note "why"` |
| Cut approved reels onto Resolve | `ren build <project>` (Resolve open on the exact project; never unasked; `reel_build` skill) |
| "Let me see it" after a build or a revision | `ren preview <project>` (renders only when asked; an unchanged reel is a cache hit) and hand the person the `Watch:` path it prints |
| Two versions of one reel | `ren variant <project> new` / `build` / `diff` / `choose` |
| Record approval | `ren sign-off <project> "<reel timeline name>"` (built reels only; `PROPOSED` fails as `REJECTED` does) |
| Render reels to files | `ren deliver <project>` (every signed-off reel, with a receipt and QC; the folder is revealed) or `ren deliver <project> <n>` for one |
| Deliver a committed edit (a timeline no reel plan names) | `ren deliver <project> --timeline "<name>"` - rendered, mastered to the delivery loudness, verified, and recorded beside the file; `ren revise <project> "<note>" --deliver` delivers the timeline the edit landed on |
| Creative QA after delivery | `ren qa <project>` (one reversible repair for a measurable defect through `ren revise`, then re-QC; editorial calls land on the review page as questions; clean passes untouched) |
| Pause or release a run | `ren pause <project>` / `ren resume <project>`; a staged timeline held from the sweep: `ren holds release <project> <timeline>` |
| Redo one stage, step or clip | `ren edit <project>` with `--rerun <target>`, `--only <step>`, `--step <step>` or `--target <name>` (AGENTS.md §3) |
| What the measurements say about this cut | `python3 -m library.tools.editorial_report <project>`: the project's declared editorial objective and whether the cut satisfies it, what a silence or a filler would cost the rhythm, the camera angles to choose between, the shot sequences that measure as weak, the moments that should be held, and what a reduction took with it. It is an ANALYSIS - it changes nothing and gates nothing |
| Tidy the Resolve pool | `ren pool-organize`, `ren pool-prune`, `ren relink` |

`pipeline_operations` lists the named operations a scoped run can do and
the scope each runs at. Pick the narrowest capability that reaches the
goal; a full run redoes work the goal did not ask for.

`ren capabilities` is the machine-discoverable catalog: every capability
id, what it is for, what it needs, and whether THIS Mac can do it.
`ren capabilities <id>` describes one in full and says how to run it;
an id Ren does not hold comes back with the nearest real ids, so ask
before you guess.

## Generated references

- [capabilities](references/capabilities.md) - every capability id, what it
  does, what it needs and the command that runs it.

It is GENERATED byte-for-byte by
`python3 -m library.tools.capability_discovery --emit-reference` and
checked for drift by `tests/tooling/test_skill_source.py`. Do not
hand-edit it; change the owning registry and regenerate it. Read a
capability's needs or its command from there, never from memory, and
never from a list written out here.

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
   `request_layout.stable_prefix` first, then its named `sections` in order.
   The prefix contains host-stable instructions and `expected_schema`; its
   `stable_prefix_sha256` fingerprints those exact UTF-8 bytes. The sections
   carry task instructions, constraints, context and any evidence. Keep this
   order when forming the model request; each section names the request field
   that supplies its content, avoiding another copy in the wire file.
   `expected_schema` (the shape your answer must satisfy), `step_id`,
   `project_folder`, `timestamp` and `kind` (`llm_step`,
   `briefing_interview`, or `edit_spec`). `--json` gives the same.
2. **Do the work** the prompt asks, from `context` alone. Never invent
   footage, timings, or measurements; a step that cannot decide from
   its own context says so in the shape its schema allows.
   **A `context` section that names a `FILE:` is a REFERENCE: open it.**
   A large table (the picture table, the spine, the assignments) is
   delivered as a path plus a map of the rows and the line range that
   holds each one, because the same table was being sent to several
   steps at once. Read the rows you need, in full, before you decide
   anything that turns on them - `sed -n 'a,bp' '<the path>'`, or grep
   it. Nothing is withheld: every byte of the table is at that path.
   A `context` that says a request and an answer are NOT copied into
   this prompt is a RETRY: read both files, then answer again with the
   same JSON shape.
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

The legacy full-run form is the same command with no scope - the whole
pipeline, every step in DAG order. Use it only for the cases above:

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
only unresolved, decision-changing questions. Ask about goals or outcomes
rather than editing methods; do not invent a creative default for a
missing answer. Ask in conversation, one at a
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

- `ren assignment <project>` - the task's own verdict:
  `task_state.completed` says whether the task is COMPLETE and which
  criterion is missing when it is not, and `task_state.blocking` says
  the one thing still in the way. Never report a task complete because a
  plan compiled, a mutation was submitted or a file was rendered; report
  `task_state.completed.complete`, and quote the criterion that is
  missing when it is false.
- `ren status <project>` - the run's own verdict (`SUCCESS` only when
  the whole DAG is complete and nothing failed).
- `ren timeline state <project> --reel <n>` - what the recorded
  generations say happened: the state before the last write, the state
  after it, and whether they differ, with the diff. It is the ONE read
  of "did my edit land, and what did it change?"; reach for
  `verify_timeline` or a render only when the state alone cannot prove
  it.
- `ren drift <project>` and `bin/resolve-axi` reads - the live timeline
  holds what was planned.
- `resolve-axi render inspect --out <dir> --apply` - render the OPEN
  timeline to a scratch file so you can LOOK at what changed. It
  changes nothing in Resolve (no timeline, no clip, no pool item), so
  the broker may run it and the render queues with every other Resolve
  job. This is the route for a timeline Ren did not build; for a reel
  the plan knows, `ren preview <project>` is the reel-shaped one. The
  file it writes is a PRE-MASTER render - it has not been brought to
  the delivery loudness, so it is for LOOKING, not for shipping;
  `ren deliver <project> --timeline "<name>"` delivers one.
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
ren revise <project> --assignment <id> "<the note>"  # update ONE named request
ren revise <project>               # continue the newest open revision
```

`--assignment <id>` names the request the correction updates. Without it
Ren attaches the note to the request that owns the reel, which is right
until the person means a different one. `ren assignment-state <project>`
names the requests on record and shows every correction already on them,
so a correction updates the existing task instead of starting a second
one.

It prints JSON; relay its `reply` line to the person. `status` says what
happened next:

| `status` | What you do |
|---|---|
| `awaiting_translation` | Answer the `LLM_REQUEST_READY` file exactly as below, then run `ren revise <project>` again |
| `question` | Ask the person `reply` (it is exactly one question), then `ren revise <project> --answer "<their words>"` |
| `no_change` | Ren looked, and the existing material already achieves the request. Nothing was written to the timeline; say so, and say why |
| `patched` / `touched` | A change was applied. Check its AXI receipt and requested result before reporting completion |
| `rebuilt` | The owning steps rebuilt that reel. Check the operation receipt and requested result before reporting completion |
| `incomplete` | The request did not complete. Inspect actual state and the refusal or failure; do not report delivery or retry until its recovery path is clear |
| `refused` | Nothing changed; tell the person `reply` |

A change to a reel's contents or arrangement is an editorial revision;
use `ren revise` for the user's request. For any Resolve operation,
discover and execute through AXI; do not choose a backend or raw Resolve
command in the editorial plan. A plan is not an applied edit, an applied
edit is not verified completion, a staged timeline is not a promoted
reel, and a promoted reel is not delivered until the signed-off export
and its checks exist. `--plan` stops after showing what would change.
Changing a signed-off reel asks first (`needs_question`), like a rebuild.

**A subjective request may come back as choices, not a plan.** When the
editor asks for something the footage can answer in more than one honest
way ("the opening feels slow", "make it punchier"), the translation
returns SEVERAL candidate treatments and `ren revise` asks which one to
use. Show the person each candidate's name and what it changes, in their
words, and answer with `ren revise <project> --answer "<their words>"`.
Every candidate and the reasoning behind it is kept on file, including
the ones that lost, so a later question about why this cut was chosen has
an answer.

**"Nothing needs changing" is an answer.** When the material already
achieves what was asked for, the translation returns a no-change
conclusion with its reason and the evidence it rests on, `ren revise`
settles it, and nothing is written to the timeline. Report it as what it
is: Ren looked and left it alone.

**What the editor decided is recorded.** When they answer a question
about a proposal, the translation reports whether they accepted, rejected
or MODIFIED it, and why, in their own words. A modification is not a
rejection: the plan as proposed did not stand. If they said the
preference is theirs in general, it is recorded as editor-wide - and it
still does not reach another project on its own.

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
- For AXI execution failures, inspect the receipt before retrying. A
  `stale` refusal needs fresh timeline state and a newly prepared request;
  `conflict` needs the current state preserved and the intent clarified;
  `unsupported` means no eligible production route; and
  `backend_failure` requires checking compensation evidence. If the
  receipt says `recovery_required`, stop edits and follow its named
  recovery path until read-back proves the state restored or explicitly
  recoverable. Never treat a successful command or a retry as proof that
  an edit completed.
