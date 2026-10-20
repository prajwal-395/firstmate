# Agent environment inventory: AGENT-011 .. AGENT-017

Lane: `fm/cap-agent-decisions`. Source: "Ren - Detailed Capability Improvement Punch List"
(baseline 2026-10-20), section 8.2 "Reduce unnecessary agent decisions", items AGENT-011..017.

This is the survey that preceded implementation. It records, per item, **what already
exists** (so the next change connects rather than parallels), **the gap**, and **the
smallest change that closes it**. Every claim is a file:line in this tree at
commit `5aa6b82eb`.

## What shipped, and where

The "planned change" under each item is what landed. Each is its own PR:

| item | what shipped | PR |
|---|---|---|
| AGENT-011 | `library/tools/target_resolution.py` - one owner of what a described target means; `resolve-axi target` and `ren timeline target` are its two faces | https://github.com/prajwal-395/video_editing_pilot/pull/2935 |
| AGENT-012 | `preservation_invariant` owns the named preservation requirements; the plan carries them and the route constraints | https://github.com/prajwal-395/video_editing_pilot/pull/2950 |
| AGENT-013 | `worker_requirements.check_edit()` gates the two write paths that lacked one; `ren check` answers the target half | https://github.com/prajwal-395/video_editing_pilot/pull/2953 |
| AGENT-014 | `library/tools/edit_state_report.py` - one reader over the recorded generations; `ren timeline state` | https://github.com/prajwal-395/video_editing_pilot/pull/2951 |
| AGENT-015 | `library/tools/recovery_instruction.py` joins a failure category to its recovery action; the AXI failure receipt carries it | https://github.com/prajwal-395/video_editing_pilot/pull/2939 |
| AGENT-016 | `ren_refusal.ASK_REASONS` - a refusal that asks the person says which of the three things demands them | https://github.com/prajwal-395/video_editing_pilot/pull/2943 |
| AGENT-017 | `operations.reference_sections()` - the narrow read; `resolve-axi capabilities <op> --references` | https://github.com/prajwal-395/video_editing_pilot/pull/2947 |

Two gaps are named rather than papered over: the natural-language clause path
(`library/tools/edit_spec.py`) does not yet let a note STATE a preservation requirement
(PR 2950), and the AXI capability references are still flat tables, addressable through
the slice command rather than by heading (PR 2947).

Section's definition of done: an agent familiar with the user-facing task but unfamiliar
with Resolve internals can discover the correct procedures, plan a supported edit,
execute safely, recover from known failures, and report the actual outcome without
hand-authored backend choreography.

---

## AGENT-011 - Make deterministic target resolution a tool capability

### What exists

Six deterministic resolvers exist. None of them accepts a *description*.

| Vocabulary | Owner | Resolves to |
|---|---|---|
| `(source_file, in, out, role)` | `library/tools/timeline_addressing.py:134` | live Resolve item |
| `unique_id` | `library/tools/edit_preparation.py:263` | shadow clip |
| clip **name** + track | `library/tools/edit_preparation.py:282` | shadow clip (`assumed`) |
| `(row, ordinal)` | `library/tools/edit_preparation.py:316` | shadow clip (`assumed`) |
| words / beat / section / frame anchor | `library/tools/sub_block_anchor.py` | a frame **inside a spine block** |
| marker frame | `library/tools/marker_routing.py:796` | a clip (frame arithmetic) |

- The typed target contract is `library/tools/edit_request.py:63` (`EditTarget`), with
  `from_selector` at `:88`. Its input is already a resolved identity.
- The one resolver that turns a partly-described target into a concrete object is
  **private**: `edit_preparation._resolve_targets` (`:236`). It understands exactly three
  vocabularies (exact id / exact name / row+ordinal) and it is scoped to `EditRequest`
  operations.
- Live re-resolution before a write: `library/tools/resolve_axi_core.py:389` and `:639`
  call `timeline_addressing.resolve_item` + `verify_item` and raise `StaleTarget` on drift.
- AXI's own addressing surface is `resolve_axi_core_commands._edit_scope` (`:9105`):
  `--track video1 --index N`, `--target-unique-id`, or expected-frame staleness guards.
- The read that feeds it, `cmd_items` (`:1632`), prints a TOON table of
  `track, name, record_in, record_out, duration, source_in, source_out, source_file`.
  **`unique_id` is not in that table** (`:1648-1673`), so an agent reading `items` can
  only count positions.

### Gap

There is no single owner of "described target -> concrete timeline object". The agent
hand-counts rows in a TOON table, or asks the model to interpolate.

Two named cases have no resolver at all:
- a **timeline time range** ("replace the segment from 12s to 18s") - `sub_block_anchor`
  resolves a *plan entry's* anchor inside a spine block, not a clip on a timeline;
- an **ordinal among a class** ("the second interview B-roll") - `ren search` /
  `library/tools/analysis/footage_query.py` searches *footage*, not the built timeline.

There is no `ren` verb for it, no AXI subcommand, and no Rust equivalent
(`native/ren-cli/src/commands.rs` has `locate_project` and `resolve_template_reference`
only).

### Planned change

A single owner, `library/tools/target_resolution.py`, holding ONE selector enumeration
and ONE entry point, composed from the resolvers above rather than replacing them:

- parse a description into a typed selector (`ordinal` / `time_range` / `named_row` /
  `words_anchor` / `reel` / `identity`);
- resolve it against the recorded generation, reusing `edit_preparation`'s name and
  position matching rather than re-implementing it;
- refuse with the **existing** `timeline_addressing` vocabulary (`TargetNotFound`,
  `AmbiguousTarget`, `StaleTarget`), and return `assumed` for a positional match so the
  assumption is recorded, never hidden;
- be reachable from the agent: an AXI subcommand next to `edit-request`
  (`resolve_axi_core_commands.py:9275`) and a `ren` verb - registered **twice**, in
  `ren/commands.py` and in `native/ren-cli/src/commands.rs`, per AGENTS.md section 2.

A positional resolution is not a stable identity (`edit_request.py:81-84` says so), so a
resolved-by-position target must be reported as an assumption, and the returned identity
should prefer `editorial_identity` (survives a rebuild) over `unique_id` (does not).

---

## AGENT-012 - Make preservation requirements and backend selection part of planning contracts

### What exists

**Backend selection is already deterministic and already model-free.** The requirement is
met at the execution layer:

- `library/tools/execution_router.py:574` `route_compiled_edit` is the one router.
- `ExecutionDecision` (`:97-131`) records the machine-readable evidence of the choice in
  `selection` (`:131-160`): `semantics`, `qualification`, `media_type`, `timeline_state`,
  `preservation_constraints` (`:154`), `safety_class`, `cost`, `minimum_mutation`.
- `DIRECT_BYPASS_FLAGS` (`:45`) refuses any caller flag that asks for a direct route
  (`:589-594`).
- The router's choice is recorded in the plan at
  `library/tools/resolve_axi_core.py:1374-1391` (`backend`, `backend_reason`, `routing`).
- The model is explicitly forbidden to choose: `.claude/skills/ren-co-editor/SKILL.md:62`
  and `.agents/skills/resolve-axi/SKILL.md:17`.

So the remaining half of AGENT-012 is **expressible constraints on the choice**, not a
model-chosen backend.

**Preservation is machine-checkable but derived from the op, not carried in the plan.**
`library/tools/operation_acceptance.py:28` `contract_for(op_id)` names the per-op check
set - `approved_edits_preserved` (`:49`), `unrelated_items_unchanged` (`:36`),
`linked_audio_preserved` (`:50`), `record_span_preserved` (`:22`),
`duration_preserved` (`:20`) and so on. `evaluate()` (`:136`) reports a check as
`missing` when it was not measured - never an implicit pass (`:151-157`).

The only requester-expressible preservation today is `EditRequest.preconditions`
(`library/tools/edit_request.py:288`) and `EditOperation.preconditions`/`postconditions`
(`:200-201`), judged by the nine kinds in `library/tools/edit_patch.py:341`
(`clip_present`, `marker_present`, `clip_transform_equals`, `duration_unchanged`,
`clip_count`, ...). These reach execution:
`resolve_axi_core.py:773` flattens `request.preconditions` into
`ExecutionInput.preservation_constraints` (`library/tools/transaction_orchestration.py:80`),
and the router shows them in `selection`.

### Gap

- A requester has **nowhere to put** "do not touch the music bed" / "keep the approved
  captions" / "preserve this marker". The clause schema
  (`library/tools/edit_spec.py:1095-1125`) has `anchor`, `values`, `status`,
  `missing_referent`, `question` - no preservation field.
- `edit_patch.CONDITIONS` can only express predicates over one clip or one frame.
  `duration_unchanged` (`:352`) takes no arguments at all. There is no "this row is
  untouched" or "this marker survives" predicate.
- The only "broke nothing" notion is **model-judged and post hoc**:
  `edit_spec.intent_check` (`:794`) runs after the build and reads booleans out of an
  LLM observation. AGENT-012 wants this **in the plan, before the write**.

### Planned change

1. One named preservation vocabulary. It belongs *with* `edit_patch.CONDITIONS`
   (one enumeration), not beside it - `operation_acceptance` and `edit_patch.CONDITIONS`
   are already two related lists and a third would be worse.
2. Carry it on the plan: `EditRequest.preservation` and `EditOperation.preservation`,
   plus a `preservation` array in the `edit_spec` clause schema, validated against the
   enumeration (an unknown name is refused by name, as `edit_patch.py:470-475` refuses an
   unknown condition kind).
   `edit_request.from_dict` refuses unknown keys (`:345-353`), so the shape change must
   be deliberate: bump `REQUEST_VERSION` (`:38`) or accept the key on purpose, and say
   which.
3. Judge it on the fresh live snapshot, and report it `missing` when unmeasured.
4. Backend: add `backend_constraints` that may only **narrow** the router's choice and
   never widen it, in the same shape as `DIRECT_BYPASS_FLAGS`. An unsatisfiable
   constraint refuses **naming the constraint and the eligible alternatives**, so the
   agent does not retry blindly.

---

## AGENT-013 - Automate routine readiness checks

### What exists

`ren doctor` is the one readiness report: `ren/doctor.py:84-99` (`Check`),
`:1038-1063` (18 check groups), `:1082-1096` (`run_checks`), `:1112-1124`
(`capability_report`), `:1229-1257` (`render_for`). It is machine-scoped, read-only, and
capability-aware: `library/tools/machine_needs.py:51-94` (the `NEEDS` vocabulary),
`:150-228` (`CAPABILITY_NEEDS` per capability), `:256-288` (`availability`).

The matcher that turns needs into named refusals is
`library/tools/worker_requirements.py:88-149` (`match_requirements`); `is_refusal`
(`:62-70`) makes an `unknown:` entry report and never gate.

Only **two** call sites auto-invoke a readiness check today:
`manage_project.py:2660-2681` (`check_build`) and `library/tools/reel_deliver.py:876-887`
(`check_render`). Every other verb - `ren edit`, `ren touch`, `ren revise`, `ren undo`,
`ren preview` - discovers a missing prerequisite by failing.

`ren check <project>` (`manage_project.py:2094+`) is project-scoped and **never touches
Resolve**: no "is Resolve open", no "is the right project open", no broker/lease, no
media pool.

`library/tools/resolve_health.py:4` `check_resolve_connection` is the only callable that
answers "is Resolve up and is a project open" - and **nothing calls it** (it is listed in
`tests/contracts/test_resolve_guard_wiring.py:124` as "connects but is not dispatched").

### Gap

The five facts a readiness check should cover are split across five owners and none of
them is joined:

| Fact | Owner today |
|---|---|
| Is Resolve open? | `ren/doctor.py:165-208`; `resolve_health.py:4` (uncalled) |
| Is the *right* project open? | only inside AXI dispatch, after the command was issued |
| Is the broker / lease free? | `resolve_lock.lease_status()` (`resolve_lock.py:707`); `resolved.client.ping()` |
| Is the media pool present? | **no check exists** |
| Is the model available? | `ren/doctor.py:508-576` |

`ren doctor` answers "can this Mac do kind X", not "can I do *this* operation, against
*this* project, right now".

### Planned change

No new module - extend the existing enumerations:

1. `machine_needs.NEEDS` gains the four implicit facts (`resolve.project_open`,
   `resolve.broker`, `resolve.lease_free`, `resolve.media_pool`), each with a row in
   `CAPABILITY_NEEDS`. `tests/contracts/test_capabilities.py` holds that table to the
   registry.
2. `worker_requirements` gains one operation-scoped entry point that returns the same
   **named** refusal strings, so `is_refusal()` keeps working and `unknown:` still never
   gates.
3. `ren/doctor.py` gains the matching check functions and registers them in `GROUPS`.
   `resolve_health.check_resolve_connection` is wired in rather than left uncalled.
4. One call site: the `ren` verb dispatch, gated on the verb's declared effects, so every
   verb inherits the check.

Constraints that must hold: `ren doctor` stays **read-only**; a check that cannot be
determined sets `undetermined=True`, never `ok=False`; a live Resolve read on the CLI's
own thread can hang (the 818 s incident in `library/tools/resolve_watchdog.py:1-28`), so
the probe runs in a child process with a timeout, as `doctor.py:104-162` already does;
and any new Resolve caller must go through the leased
`resolve_locale.scriptapp_preserving_locale` boundary or
`tests/contracts/test_resolve_guard_wiring.py` fails it.

A readiness gate that refuses correct work is a production incident - the
`python.graphics` history is recorded at `worker_requirements.py:196-214`.

---

## AGENT-014 - Automate safe state inspection before and after execution

### What exists

**Pre/post capture is already universal.** It is simply not handed to the agent.

| Path | Pre | Post |
|---|---|---|
| `library/tools/edit_patch.py` | live pre-read under `cursor_fence`, `snapshot_role="pre_write"` (`:867-873`); staleness refusal (`:874-900`) | post-read with one retry; if both fail, `PatchRefused` says *"The edit may have landed, and I could not read the timeline to confirm it"* (`:1162-1196`) |
| `library/tools/resolve_axi_core.py` | shadow generation must equal the prepared generation (`:900-911`) | `read_live_timeline` (`:962-964`) then `_verify_registered_full_request` (`:150-190`) |
| `library/tools/resolved/ui_transaction.py` | `observed_before` (`:1126`) | `observed_after` + `restored` (`:1158-1195`) |
| `library/tools/reel_touchup.py` | `timeline_inventory_before` (`:3685-3690`) | in the receipt |
| `library/tools/undo_journal.py` | `preservation_before` (`:2191-2215`) | `preservation_after` (`:2311-2338`) |

The stores are `library/tools/timeline_shadow.py` (the recorded generation store;
`head` `:690`, `record` `:915`, `observe` `:1214`, `diff` `:1267`) and
`library/tools/plan_provenance.py:1279-1318` (`record_timeline_snapshot`, with `sha256`,
`recorded_at`, `action`, `history`).

The verifiers are `library/tools/transaction_orchestration.py` (`verify_readback` `:225`,
`unrelated_clip_changes` `:192`), `library/tools/operation_acceptance.py`,
`library/tools/timeline_conformance.py` (`CHECKS` `:34-42`),
`library/tools/timeline_diff.py`, `library/tools/drift_check.py` and
`library/tools/timeline_oracle.py`.

### Gap

1. Capture is universal but **invisible**: no single reader turns "what I did" plus the
   recorded before/after into one verdict.
2. `.claude/skills/ren-co-editor/SKILL.md` section 6 still lists eight or nine separate
   verification verbs for the agent to choose between by hand.
3. A *partial* edit is detected per path, not systemically.
4. `timeline_read` answers from the shadow; a **fresh** read requires the caller to say
   `refresh` (`library/tools/timeline_read.py:5-9`).

### Planned change

One reader over the artifacts that already exist - **not** a second capture path. The
shadow is the substrate (`timeline_shadow.diff` at `:1267-1284` is the diff, so the
shadow never disagrees with `versions.rounds.diff_reel`), joined with
`plan_provenance`'s snapshot table. It emits a before/after pair with a verdict of
`landed` / `partial` / `unreadable`, and is reachable as a `ren` verb - registered in
`ren/commands.py` **and** `native/ren-cli/src/commands.rs`.

The co-editor skill's verification section is then rewritten to point at that one report,
with `verify_timeline` / `render inspect` reserved for the cases the report says the
structure alone cannot prove.

Constraints: the shadow is the timeline *as of* a generation, not the live timeline, so
the report must name the generation and its time or it over-claims; the shadow is never
a precondition for a write (`timeline_shadow.py:53-56`); a read that fails is not a
"no change" verdict; a live read must go through `resolve_lock.cursor_excursion` and be
proven by `assert_current_timeline` (`drift_check.py:20-31`), or it digests the wrong
timeline.

---

## AGENT-015 - Use typed recovery instructions instead of asking the model to improvise retry strategies

### What exists

Three typed vocabularies exist and **none of them is joined to the others**:

| Question | Vocabulary | Owner |
|---|---|---|
| Should the host act, ask, or wait? | `auto_recoverable` / `needs_question` / `blocked` | `library/tools/ren_refusal.py:97-99`, validated at `:122-125` |
| What kind of failure was this? | `unsupported` / `stale` / `conflict` / `backend_failure` / `verification_failed` / `recovery_required` | `library/tools/transaction_orchestration.py:95-97` (a narrower copy at `execution_router.py:55-60`) |
| What is the next concrete action? | ten kinds -> ten action verbs | `library/tools/transaction_receipt.py:10-21`, `recovery()` at `:24-28` |

`recovery()` produces exactly the right shape - `{"type", "action", "reason"}` - but it is
only reached from `standardize()` (`transaction_receipt.py:87-95`) and
`edit_patch.StalePatch` (`library/tools/edit_patch.py:138-173`).

The **consumer** is already in place: `.claude/skills/ren-co-editor/SKILL.md:485-501`
and `.agents/skills/resolve-axi/SKILL.md:20-30` both read `recovery_class` and the AXI
categories and name the next action for each.

### Gap

1. The AXI dispatcher's own recovery block is **weaker** than `recovery()`:
   `resolve_axi_core.refused` (`:778-831`) builds `{"status", "completed_operations", ...}`
   inline (`:815-827`) with **no `type` and no `action`** - the concrete next step is lost.
2. `retryable` is hard-coded `False` at `resolve_axi_core.py:781`. Nothing in the AXI path
   can say "this one is safe to retry the same way".
3. The error/timeout/degraded path emits **bare strings**, so the model has to improvise
   a retry:
   - `library/tools/resolve_deadline.py:58-63` - `ResolveCallTimeout`,
     `ResolutionNotApplied` are `RuntimeError`s with a prose message, although the
     module docstring (`:20-22`) says the caller refuses the build *by name*;
   - `library/tools/resolve_watchdog.py:59-66` - `ResolveDegraded` already carries
     `reason`, `holder` and `job_id`, and is still an untyped `RuntimeError`;
   - `library/tools/resolve_axi_core_commands.py:267-281` - `AxiError` prints
     `error:` / `help:`, not the `ren: refused` shape, so the host gets no
     `recovery_class` from the AXI CLI's own failure path;
   - `library/tools/execution_router.py:71-88` - `RoutingRefused` carries a typed
     `category` but has no `recovery_class` and no `fix`.
4. There is **no link table** from a failure category to a recovery action, so the three
   vocabularies cannot answer "what do I do now" without a human reading all three.

### Planned change

One module that **binds** the three, rather than adding a fourth:
`library/tools/recovery_instruction.py` re-exports the three vocabularies and owns the
missing `category -> action` table. `instruction(category, reason=...)` returns
`{category, recovery_class, action, reason}`.

Then, at the call sites:
- `transaction_orchestration.ExecutionRefusal` gains the instruction;
- `resolve_axi_core.refused` emits the typed instruction into the receipt and derives
  `retryable` from the category instead of hard-coding `False`;
- `RoutingRefused` carries a recovery class and a fix;
- `ResolveCallTimeout` / `ResolutionNotApplied` / `ResolveDegraded` become typed
  refusals (they already hold the ingredients);
- `AxiError` prints the `ren: refused` shape.

The **binding**, not a merge: `execution_router.py:51-54` and
`transaction_orchestration.py` already carry a written rule that the failure categories
must stay distinct end to end.

Caution: `RenRefusal.__init__` is strict (`ren_refusal.py:103-111`) - a subclass that
forgets `what`/`why`/`fix` raises at the raise site - and
`ren_refusal.render()` / `to_dict()` are pinned by
`tests/unit/context/test_ren_result.py`, `tests/contracts/test_mcp_server.py` and
`ren/mcp_server.py:451-462`.

---

## AGENT-016 - Require human input only when the intent, authorization, or available evidence genuinely demands it

### What exists

Seven distinct ask surfaces, none of them joined to the others:

| Surface | Owner |
|---|---|
| `needs_question` recovery class | `library/tools/ren_refusal.py:98` |
| Pipeline review gate | `library/tools/review_gate.py:29` (`GateFeedback.action`) |
| Edit-spec clarification | `library/tools/edit_spec.py:57-66`, `questions_for_spec` `:418-460` |
| `ren revise` question | `library/tools/revise.py:72-73` - takes exactly ONE question (`:24-26`) |
| Assignment replacement authorization | `library/tools/assignment_spine.py:88-110` |
| Reel build approval | `library/tools/reel_proposal.py:128-132` |
| Model-facing "ask" (not the human) | `library/tools/briefing_interview.py`, `undetermined.py`, `direction_contradiction.py`, `nothing_to_decide.py` |

The "nothing to decide" case is expressed well, twice: `library/tools/nothing_to_decide.py`
(stand the *model* down, with a reason, `:37-66`) and
`library/tools/briefing_interview.py` (a *human* is asked, but it reports and never gates,
`:30-33`, with three readings `ASKED` / `NOTHING_TO_ASK` / `NOT_DECLARED` at `:53-66`).

A single place for the *intended* single decision point already exists as an **empty
stub**: `library/tools/requirements.py:1659` `UNAUTHORISED: Dict[str, str] = {}`, printed
at `:2140-2143` and read by nothing.

### Gap

1. No single ask-decision point, so "did we ask more than we needed to?" is not a
   countable property of a run.
2. **No typed ASK REASON.** `needs_question` says *that* a question is needed, never
   *why*. A host cannot distinguish "the intent is ambiguous" from "authorization is
   missing" from "the evidence is insufficient" - which is exactly the three-way
   distinction AGENT-016 names.
3. `requirements.KINDS` (`:116-118`) has no human/authorization kind; an approval on file
   is modelled as a `predicate` that reads a file.
4. Over-ask is unmeasured. `briefing_interview` asks the brief-declaring steps on every
   brief-less run; `undetermined` asks every model-reaching step on every run.

### Planned change

1. `library/tools/ren_refusal.py` gains an **ask reason** vocabulary
   (`ambiguous_intent` / `missing_authorization` / `insufficient_evidence`) beside
   `recovery_class`, and `to_dict()` emits it **only when non-empty** so the pinned
   `REN_RESULT` shape does not change for a refusal that does not ask.
   The reason is **optional** in the constructor: making it mandatory would break eight
   modules that already pass `recovery_class="needs_question"`. The "a question must
   name its reason" rule belongs in a test, not in a constructor raise.
2. The existing stub becomes real: `requirements.UNAUTHORISED` is the registry of
   requirements held pending a captain decision, each naming its ask reason, and
   `requirements.KINDS` gains a human/authorization kind.
3. One helper that decides - the same shape as `nothing_to_decide`, for the human case:
   given "intent unambiguous / authorized / evidence sufficient", return the ask reason
   or empty. "Nothing to decide" then has one spelling for the human and one for the
   model, deliberately kept separate.
4. The over-ask audit: `briefing_interview` asks on every brief-less run; it should ask
   only where a brief would change the outcome.

The engine's rule is already written in the skill
(`.claude/skills/ren-co-editor/SKILL.md:52-56`: *"Ask only when a missing creative choice
materially affects the result"*; *"Never ask what the footage, the series documents or the
history already answer"*). AGENT-016 is about the **engine** enforcing it, not about the
prose.

---

## AGENT-017 - Provide concise, task-relevant context rather than exposing entire operational manuals on every call

### What exists

The step-context layer already does this, and does it well:

- `library/tools/context_projector.py:110-234` - `project_fields(data, dot_paths)`; a
  `-` prefix drops; `"-"` alone means everything minus those.
- `library/tools/context_views.py` (1452 lines) - the named views
  (`view:transcript`, `view:picture`, `view:beatgrid`, ...); an unknown name **raises**.
- Enforcement: `tests/contracts/test_context_contracts.py` - a declaration nothing reads
  is refused, not ignored.
- `library/tools/brief_reference.py:39-120` - by-reference documents: preamble inline,
  sections under 400 bytes inline, everything else a heading + byte size + **line range**
  + lede. *"The line range is the load-bearing affordance."*
- `library/tools/empty_table_guard.py` - reports a table that arrived with zero rows.
- `library/tools/toon_serializer.py` - how the projected context is rendered.

The **reference/skill** layer does not have an equivalent:

| Artefact | Size |
|---|---|
| `python3 -m library.tools.operations --emit-skill` | 7,661 chars, 53 operations |
| `.agents/skills/resolve-axi/references/` (6 files) | **141,691 bytes**, 84 operations |
| `capability-matrix.md` | 72,895 bytes, 84 rows |
| `backend-limitations.md` | 24,648 bytes, 70 rows |
| `concepts.md` / `verification.md` / `transactions.md` / `recovery.md` | 14,077 / 13,791 / 9,359 / 6,921 bytes |

A typical call needs roughly **one row out of 84** in each of the per-operation tables -
about 0.9 KB of 141 KB - plus the cross-cutting `backend-limitations.md` constraints.

### Gap

1. The six AXI reference files are **flat tables with no per-operation headings**, so
   there is no `## clip.trim` to jump to. `.agents/skills/resolve-axi/SKILL.md:38-45` says
   *"Open only what the request needs"* and lists all six - a directive the agent applies
   by hand, against 141 KB of flat text.
2. `operations.emit_skill()` (`library/tools/operations.py:2510-2558`) is a full dump of
   all 53 operations, with no per-task slice.
3. There is **no context budget** for the skill/reference layer, the way `context_fields`
   and `view:` provide one for step context.

### Planned change

1. **Per-operation headings in the generated references.** The generator
   (`operations.py:2561-2719`) already holds the per-operation rows; `operation_rows()`
   (`:2573-2586`) emits one section per operation instead of one flat table. The agent
   then uses the same affordance `brief_reference.py:31-32` already standardises on.
   `backend-limitations.md` is cross-cutting and may not be per-operation at all - check
   its structure before assuming.
2. **One slice command.** `resolve_axi_core_commands.cmd_capabilities` (`:401`) already
   ranks ops by `--search` and prints one op's detail (`:423-475`); add a mode that
   prints **only that operation's reference sections**, pulled from the same generator, so
   the narrow read is machine-routed instead of hand-judged.
3. `operations.emit_skill()` gains an optional `only=` filter, keeping the current
   default output byte-identical.

Constraints: the reference files are byte-pinned by
`tests/tooling/test_skill_source.py:79-90` against the generator, so they must be
regenerated in the same commit
(`bin/vep -m library.tools.operations --emit-resolve-axi-references`); the checked-in
`.claude/skills` is a symlink to `.agents/skills`, so the generator must write to
`.agents/skills/resolve-axi/references/`; and a slice must say it is a slice - the AXI
skill warns that support must not be inferred from a partial row
(`.agents/skills/resolve-axi/SKILL.md:47`).

---

## Cross-cutting constraints this lane works under

- **One enumeration.** Each item above reuses the existing owner (`edit_patch.CONDITIONS`,
  `machine_needs`, `transaction_receipt.RECOVERY_ACTIONS`, `ren_refusal.RECOVERY_CLASS`,
  `timeline_shadow`, `operation_acceptance`) rather than adding a parallel list. Where a
  new file is unavoidable it **binds** the existing ones.
- **A declared output has a READER** (AGENTS.md 10.1, `library/tools/output_contract.py`).
- **A new `ren` verb is registered twice** - `ren/commands.py` and
  `native/ren-cli/src/commands.rs` (AGENTS.md section 2);
  `tests/contracts/test_verb_catalog.py` and
  `tests/unit/tools/test_ci_verb_registration.py` hold them in step.
- **AGENTS.md is an index with two ratchets** (`scripts/check_agents_md_size.py`). New
  index rows land inside an existing section budget; both ratchets only ever move down.
- **A rule that comes from an incident** goes in `docs/RULE_EVIDENCE.md` with a `[why]`
  link from AGENTS.md.
- **No test reaches a real project** - a test builds its project under `tmp_path` or it
  skips (`tests/tooling/test_tests_never_reach_real_projects.py`).
- Test layout: `tests/unit/<subsystem>/` for one module's behaviour, `tests/contracts/`
  for cross-engine invariants, `tests/layers.py` is the authority. Likely homes:
  `tests/unit/resolve/` for the target-resolution, recovery and state-report work;
  `tests/unit/context/` for the ask-reason and readiness work.
