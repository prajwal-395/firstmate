# Agent lifecycle control plane

Firstmate talks to a running agent two ways, and they are not the same channel.

The **data plane** is [`bin/fm-send.sh`](../bin/fm-send.sh): conversational text for the agent to read.
For a `kind=secondmate` target it always prepends the from-firstmate routing marker, because a secondmate is itself a firstmate and its reply must come back through the status path rather than a chat nobody reads.

The **control plane** is [`bin/fm-control.sh`](../bin/fm-control.sh): allowlisted lifecycle verbs addressed to an exact task id.

The split exists because the data plane's marking is exactly right for a message and exactly wrong for a lifecycle command.
A routing-marked `/quit` arrives as ordinary chat - `[fm-from-firstmate] /quit` - which the agent reasons about instead of executing.
The failure repeated across harnesses and homes, and the workaround (remember to use an unmarked send for agent-control commands, and improvise the right key or command per harness) lived only in agent prose, so it failed again every time a session did not happen to recall it.

## What the control plane owns

`bin/fm-control-lib.sh` is the single executable owner of three capability tables, which have no side effects, so they can be read as a contract:

- The **verb allowlist**: `interrupt`, `exit`, `relaunch`, `rebind`.
  There is no arbitrary-text and no generic raw-key entry point.
  A caller either names an allowlisted verb or is refused.
- **Per-harness mechanics**: the key that cancels a running turn, how many times it must be delivered, whether the composer needs clearing afterwards, the command that exits the agent, and which task kinds the adapter is verified to run.
  These were previously carried only in the [`harness-adapters`](../.agents/skills/harness-adapters/SKILL.md) skill's tool references, which now point here.
  `bin/fm-send.sh`'s `--key` path reads the composer-clear table from this owner too, rather than keeping a second copy of it.
- **Per-backend capability**: which named keys a runtime backend can deliver, and whether it has a recovery-grade agent-state classifier able to prove an agent stopped.

The [endpoint-absence proof](#reclaiming-a-task-whose-endpoint-is-gone) below is the only function here that runs backend reads; sourcing the file is still free.

A recorded `harness=` is not always an exact adapter name: a task launched from a raw command records that command's basename instead.
`fm_control_harness_family` is the one place that prefix rule is stated, and an unrecognized value resolves to no adapter rather than being guessed into one.

## Verbs

| Verb | Effect | Postcondition |
| --- | --- | --- |
| `interrupt` | Deliver the harness's verified interrupt sequence while leaving the agent running. | Delivery succeeds while the endpoint still exists and the agent is still alive where the backend can classify that; cancellation is confirmed only from an adapter-owned acknowledgement and otherwise reports `cancel=unconfirmed`. |
| `exit` | Stop the agent, preserving the endpoint, the worktree, and every uncommitted change, and declare the endpoint agent-free by design. Takes an optional `--reason <text>`. | The backend's recovery-grade classifier reports the agent gone. Already-stopped is idempotent success. |
| `relaunch` | Replace the running agent with a new one in the same worktree, on the exact recorded adapter or an explicitly chosen harness, model, and effort. | The replacement's own published endpoint exists, and the durable record names the harness that is actually running. Readiness is reported separately as `ready=confirmed` when its agent was observed running and `ready=starting` when it had not registered one yet. |
| `rebind` | Correct a record whose endpoint identifier stopped resolving while its agent kept running, by pointing it at the live endpoint that still carries the task's identity. Touches the record only, never the agent. | The rewritten record passes endpoint-identity validation and resolves to an endpoint the backend positively classifies. |

`rebind` is the one verb that acts on the record rather than on the agent, and it is the answer to a failure the other three could not reach.
A backend whose endpoint identifiers are generated re-issues them when it rebuilds its layout, so a recorded identifier can stop resolving while the agent behind it keeps running.
Firstmate then reads that endpoint as absent, which is one of the two verdicts that authorise a relaunch, and the relaunch would put a second agent on a live worktree.
[`bin/fm-backend.sh`](../bin/fm-backend.sh)'s `fm_backend_agent_state` now reports that case as `drifted` instead, and `rebind` is what clears it: before it existed, the only supported response was a fresh spawn, and recovering by hand meant closing a live agent's endpoint.

It acts only on a `drifted` endpoint, only when exactly one live endpoint carries the task's label and directory, and only when that endpoint holds an agent.
Two claimants refuse, because binding a task to the wrong endpoint would aim every later lifecycle command at a stranger.

An exit that delivers lifecycle input but cannot prove the agent stopped fails with `exit=unconfirmed`, reports the observed agent state and any interrupt cancellation claim, and never claims that nothing changed.
Interrupt never rewrites busy state as proof of its own success.
Claude exposes no lifecycle acknowledgement for a manual interrupt, so delivery succeeds with `cancel=unconfirmed` and its adapter-owned busy state remains as observed.
Devin emits no lifecycle hook for cancellation either, so after an armed interrupt the control plane invalidates the interrupted turn's busy record to `unknown` with `cancel=unconfirmed`; that invalidation is a conservative loss of knowledge, never a fabricated idle.
Devin's double Escape also opens its `/revert` picker on an idle agent, where Enter reverts file changes, so its second press is sent only after the first renders a running turn's armed hint and never sooner than the adapter's press gap.
An interrupt whose first press shows no running turn stops there and reports `cancel=not-running`, leaving busy state untouched; a picker a mistimed press opened is closed with one Escape and reported as `revert-picker=dismissed`, and `exit` refuses to type into an open picker.
[`bin/fm-control-lib.sh`](../bin/fm-control-lib.sh) owns the arm signal, press gap, and picker signal.
muse's session log records `terminal=cancelled` for the interrupted run, so the control plane reports `cancel=confirmed` only after observing that exact acknowledgement.

An interrupt is not complete until the composer is empty.
muse is the one verified adapter that restores the cancelled prompt back into its composer as real text, so its interrupt key is followed by a Ctrl+U clear; without it the next submitted line - including this plane's own exit command - would concatenate onto the restored prompt and submit both as one line.
The clear is refused before anything is sent when the recorded backend cannot deliver it.

## The declared stop

An `exit` that verifies its stop also writes [`state/<id>.stopped`](../bin/fm-stopped-lib.sh), a durable record saying this endpoint is agent-free **by design**, why, and which agent it applies to.
Supervision needs that record because an endpoint firstmate emptied on purpose is byte-identical to one a worker died in, and until it existed the fleet had no way to say "this worker is intentionally not running".
The measured cost of not saying it: two workers stopped for a four-hour quota reset on 2026-08-25 raised on the order of forty stale alerts between them, each indistinguishable at arrival from the alert that reports a genuinely dead worker.

The record is **not** a hold, and a hold is not this record.
A task can be legitimately held for a captain decision while its worker is genuinely wedged, and that must still surface; the discriminator is whether firstmate itself stopped the agent, which only the control plane knows at the moment it acts.

Three properties bound what the record can do:

- **It is bound to one incarnation.** The record carries the `spawn_gen` of the exact agent it stopped, and [`bin/fm-spawn.sh`](../bin/fm-spawn.sh) mints a fresh one on every spawn and every relaunch. A record left behind by a predecessor therefore matches nothing and silences nobody, so a replacement worker on the same task id is supervised normally from its first read.
- **It is never believed alone.** [`bin/fm-crew-state.sh`](../bin/fm-crew-state.sh) reports `state: stopped · source: declared-stop · …` only when the record still binds *and* the backend's recovery-grade classifier reports the agent gone. An agent that died on its own writes no record and still reports `exited` immediately; an agent that is alive is never called stopped.
- **Only that verdict absorbs anything.** The supervisors read the crew state, not the file. [`bin/fm-watch.sh`](../bin/fm-watch.sh)'s `declared_stop_absorb` takes such a pane out of stale detection silently, in every supervision mode, because a re-surface cadence exists to re-ask a question and firstmate holds both the reason and the resume decision here.

`relaunch` clears the declaration outright and would spend it anyway, since the replacement carries a new incarnation.
Pass `--reason` with the wait and the resume path (`--reason 'zero model quota until about 19:00; relaunch when it resets'`) whenever the stop is not self-evident: it is what the next read of that worker's state will say.

  **Teardown and discard are not verbs and will not become verbs.**
`exit` stops an agent and preserves everything else.
Removing a worktree, closing an endpoint, or discarding work stays with [`bin/fm-teardown.sh`](../bin/fm-teardown.sh), which owns the landed-work test.

**`resume` is not a verb.**
It is not deterministic across the verified adapters: codex, grok, gemini, and devin resume only from a session id printed at exit, opencode continues the most recent session for the cwd, and claude, pi, pi-signed, omp, kimi, and agy have no verified general pane-resume contract.
`relaunch` uses the brief on disk - not a harness-private session - as the durable instruction when the backend can prove the old agent stopped and the composer is empty; Devin on Herdr currently fails that composer check and refuses.
A relaunch does take one session reference when the endpoint's own runtime recorded it - see [the relaunch transaction](#transactional-relaunch) - but that is a relaunch input, not a caller-facing verb.

## Transactional relaunch

`relaunch` is the only verb that changes durable records, so it runs as a transaction with a journal at `state/<id>.control-relaunch`, the prior record preserved beside it, and a ship or scout's prior instructions preserved when a progress note is appended.

1. **Resolve the profile.**
   An explicit `--harness`, `--model`, or `--effort` wins.
   Otherwise a `kind=secondmate` task re-resolves its durable `config/secondmate-harness` pin, including that file's optional model and effort tokens, exactly as every other respawn does - so setting the pin and relaunching is the ordinary way to move a secondmate's runtime.
   A ship or scout keeps the harness already recorded for it, because that harness comes from firstmate's dispatch-profile judgment at intake and must not be silently re-read from configuration.
   A recorded raw-command basename that differs from its resolved adapter cannot reproduce the command actually running, so relaunch refuses before the checkpoint unless the caller passes an explicit `--harness` to choose the replacement runtime deliberately.
   A harness change resets model and effort unless they are named too, because a model chosen for one adapter does not transfer to another.
   A Claude or Pi replacement must also pass the home's [worker account pin](configuration.md#worker-account-pin-configclaude-account-configpi-account), so a pin that no longer resolves or is signed out refuses before the old agent stops.
   Ship and scout replacements also pass the [worker tool exclusion checks](configuration.md#worker-tool-exclusions-configcrew-exclude-tools) at this step.
2. **Safe checkpoint.**
   The recorded worktree must exist and be a worktree root; its head and dirty state are recorded.
   For a `kind=secondmate` task, the home's identity marker must match and its child records must be readable, so a relaunch can never strand child work behind an unreadable home.
   A secondmate's own crewmates run in their own endpoints and outlive its relaunch; the relaunched secondmate reconciles them from its home's durable records at startup.
3. **Record the note.**
   A ship or scout relaunch requires `--note`, because the replacement inherits the local copy but none of the conversation; the note is appended to the instructions it reads.
   A secondmate relaunch does not require one and never rewrites its standing charter.
4. **Stop the old agent** through the `exit` verb, with its postcondition.
5. **Launch the replacement** through its single owner, `bin/fm-spawn.sh --relaunch`, which keeps the recorded worktree instead of acquiring one, clears the previous harness's per-task wiring, and arms a fresh busy generation.
   It adopts the recorded endpoint when that endpoint is still there, and creates a replacement in the recorded worktree when the backend proves it gone; a created replacement carries its own endpoint identity, which the launch publishes.
6. **Read readiness from the endpoint the launch published**, identified by this transaction's own id, never from the handle the previous agent ran under.
   A retired endpoint is authoritatively absent, so watching it answers "missing" for as long as anyone waits - a wrong answer no timeout can correct, and one that used to report a healthy recovery as a lost one.
   `FM_CONTROL_LAUNCH_WAIT` therefore bounds how long the command watches, not how long the replacement is allowed to take: expiry with the endpoint present reports an unfinished start, and only an absent endpoint is a failure.

Switching harness is therefore one ordinary relaunch rather than a separate mechanism.

### Reclaiming a task whose endpoint is gone

A Herdr pane or workspace can be destroyed out from under a live task by churn or a session restart.
The task's worktree, branch, commits, and uncommitted changes all survive that; only its terminal does not.

**Reclaim is Herdr-only.** On tmux, both verbs refuse a `missing` endpoint, leaving it exactly as deadlocked as it was before this mechanism existed - deliberately, and with the reason stated rather than guessed past.

Two endpoint verdicts are agent-free, and both license a relaunch:

- `dead` - the endpoint exists and confidently holds no agent. It is **adopted**, so the task keeps its exact recorded address.
- gone, **proven** - there is no endpoint and therefore no agent, and it cannot be adopted, so the launch owner **creates one fresh endpoint in the recorded worktree** and the republished record rebinds the task to it.

That proof is its own step, because the classifier's `missing` is not one state: it conflates *the endpoint was destroyed* with *the endpoint is unreachable from here right now*.
An unreachable endpoint can still hold the live agent a rebind would duplicate, so absence is proven and never inferred from a failed read - and whether it is provable at all is a property of the backend:

- **Herdr can prove it.** Every read goes through the adapter's `--session <session>` CLI, so the recheck starts and reads the session the *record* names, through that session's own socket.
  It starts that server (only the server: no workspace and no tab are created) and **re-reads the recorded pane**.
  `dead` means the pane survived the restart and is adopted after all, with no second tab; `alive` means the agent came back and refuses; only a second `missing` proves the pane itself did not survive ([`docs/herdr-backend.md`](herdr-backend.md) "Restart and liveness behavior").
  That server start is a real side effect, and the parenthetical above does not cover it: when the recorded session's server no longer exists at all, the probe stands a fresh empty one up in order to ask, and nothing afterwards uses it.
  So in that state `exit` - which otherwise reads as a read-only inspection - leaves an idle herdr server behind.
- **tmux cannot.** `list-windows -a` describes only the tmux server the *current process* addresses (its `TMUX_TMPDIR`/socket), and a task record carries no socket identity for its endpoint.
  A different but running server would answer "not anywhere" about a window it was never able to see, so a server-wide read cannot tell a destroyed window from one on a server this process cannot address.
  There is no read available that closes that gap, so tmux always refuses - for a renamed session, a moved window, a foreign socket, and a dead server alike.

Every transient or self-contradicting read stays `unreadable` or `ambiguous` and still refuses, so a momentary backend failure can never be mistaken for absence.

That proof has one owner for the whole control plane (`fm_control_endpoint_absence_verdict` in `bin/fm-control-lib.sh`), so `exit` and `relaunch` cannot reach two different answers about one endpoint.
`exit` reports what the proof established and nothing more - see its row in the verb table above.

What a reclaim is not:

- It is **not a teardown**. The worktree is reused exactly as the previous agent left it; nothing unlanded is ever discarded, and the ordinary `--note` requirement still applies.
- It does **not** change the task's identity. The task id, its armed poll and registration, and its status log are untouched; only the endpoint binding in the record moves.
  Its instructions are the one exception, and only in the way an ordinary relaunch already changes them: a ship or scout reclaim appends the required `--note` under a `## Progress note (<timestamp>)` heading in `data/<id>/brief.md`, so re-read that brief rather than assuming it is byte-identical - a reclaim that failed and was retried leaves one block per attempt.
  A secondmate's standing charter is never rewritten.
- It is **not** a peer seat's operation. `fm-control` resolves an exact task id against **this** home's `state/`, so only the home that owns the task can reclaim it.
- It does **not** cover a secondmate. A secondmate whose endpoint is gone already has one recovery path - `bin/fm-spawn.sh <id> --secondmate`, driven by the session-start sweep or the watcher's liveness tick - so control-plane reclaim refuses and names it rather than becoming a second path to the same outcome.

The re-created tab is opened in the herdr session the record names, never in whichever session the recovering seat happens to sit in - relocating a task onto another herdr server would be an identity change published as a self-consistent but wrong record.
A seat that *claims* a herdr launcher pane belonging to a different session is refused rather than allowed to place the endpoint somewhere else, so reclaim such a task from a seat in the recorded session.
A seat with no herdr launcher pane at all - a plain ssh or cron shell, which is the ordinary way an operator reclaims - is not refused: placement falls back to the recorded session's labeled container, so the tab still lands in the session the record names.
The reclaim pins the recorded **session** but not the **workspace**: the container follows the reclaiming seat, so a reclaim run from a seat inside the recorded session places the new tab in *that seat's* workspace rather than the recorded `herdr_workspace_id`, even when the recorded workspace still exists and only the pane was destroyed.
The record is republished consistently and no work is lost, but the task's `herdr_workspace_id` moves with it.
The pane id necessarily changes (the pane did not survive), and the record follows it.
A Herdr reclaim deliberately uses the flat container shape rather than presentation projection: projection is a presentation-only layout that is never endpoint or ownership authority, and flat is already the documented fallback for every recovery it cannot bind exactly ([`docs/herdr-backend.md`](herdr-backend.md)).

**Known limitation - a refusal before the record is republished leaves a stray husk pane** (follow-up bead `fm-herdr-rebind-leak-20260913`).
The rebind registers no abort cleanup, so a refusal in the window between the new tab being created and the record being republished leaves that pane behind while the record still names the old, gone one.
The stray pane holds a bare shell - the harness is not delivered until after publication - so the next reclaim cleans up after it: the re-created tab carries the same `fm-<id>` label, `tab create` finds it, classifies it a husk, and closes and replaces it.
That self-heals only when the retry resolves the *same* workspace, which the placement rule above does not guarantee.
The worktree and the task's records are unaffected either way.

### Failure and rollback

- A refusal **before** the agent is stopped leaves the durable record and the instructions byte-identical.
- A launch failure **after** the agent is stopped restores the prior durable record, keeps the progress note so a later recovery still has it, marks the journal `failed:launching`, and reports plainly that no agent is running and where the work is preserved.
- If the launch owner already published the new record but no running agent can be confirmed, the new record is kept: the task is recorded on the new harness with no agent confirmed, which is exactly what recovery reconciles.
  Rewriting it back to the old harness would be a second, worse inaccuracy.

## Fail-closed boundaries

- Targeting is exact.
  Only a bare task id with a `state/<id>.meta` record in this home is accepted, and that record must pass the shared endpoint-identity validation.
  A legacy `fm-<id>` window label, an explicit `session:window` endpoint, and a record whose `endpoint_task_id` names another task are all refused.
- A remotely placed secondmate is refused by name.
  Its agent runs on another host, so none of the postconditions this plane verifies could be read for it here; local endpoint validation would refuse the record regardless, because `window=remote:<id>` can never match a local backend's required shape.
  Drive that lifecycle on its own host and reconcile it through the secondmate recovery path.
  For `relaunch`, drive the host through [`bin/fm-remote-secondmate-relaunch.sh`](../bin/fm-remote-secondmate-relaunch.sh), which runs `bin/fm-on.sh <id> fm-remote-secondmate-control.sh relaunch ...` and then republishes this home's route record from the identity the host confirmed; the host-local leg runs this same plane against a record that is ordinary and local there, so every checkpoint, journal, rollback, and postcondition below applies unchanged ([`docs/remote-secondmates.md`](remote-secondmates.md)); `interrupt` and `exit` have no such route.
- An unverified harness is refused rather than guessed at.
- An implicit relaunch from a prefixed raw-command basename is refused before the agent or durable state is touched because its original launch command cannot be reconstructed.
- An adapter that is not verified for this task's kind is refused **before** the running agent is stopped, not after.
  Muse is a crewmate and scout adapter only, so relaunching a secondmate onto it refuses while its agent is still up rather than leaving that secondmate with no agent when the launch owner refuses.
- A backend that cannot deliver the harness's interrupt key, or the composer clear that key needs, is refused rather than sent a different key.
  Orca's terminal API exposes only an interrupt and an Enter, so it can deliver neither Escape nor Ctrl+U.
- `exit` and `relaunch` require a backend with a recovery-grade agent-state classifier - tmux and herdr - because without one the "the agent stopped" postcondition cannot be proven.
  zellij, orca, and cmux are refused rather than reported as successful blind.
- An ambiguous or unreadable endpoint state refuses.
  Only a positively classified state acts.
- `fm-spawn --relaunch` independently refuses unless the recorded endpoint is positively agent-free, or positively absent, and the endpoint the replacement will run in has its shell in the recorded worktree, so a replacement can never join a live agent or start outside the copy holding the work.
- A replacement that is in place but has not registered an agent yet is reported as still starting rather than as a failure, because the two are indistinguishable from outside and only one of them is worth acting on.
  Reporting it as a failure invites a second relaunch onto an agent that is already coming up, or a teardown of work that is exactly where it belongs.
  It also requires the shell to be in the recorded worktree: tmux refuses immediately when it is not, while Herdr sends one `cd` to the recorded path and refuses unless a subsequent path read confirms the move.

## Capability matrix

Backend capability comes from each adapter's real surface, not from a policy choice.

| Backend | Escape | Enter | Ctrl+C | Ctrl+U | Recovery-grade agent state |
| --- | --- | --- | --- | --- | --- |
| tmux | yes | yes | yes | yes | yes |
| herdr | yes | yes | yes | yes | yes |
| zellij | yes | yes | yes | yes | no |
| cmux | yes | yes | yes | yes | no |
| orca | no | yes | yes | no | no |

Per-harness interrupt keys, repeat counts, composer clears, exit commands, and supported task kinds live in `bin/fm-control-lib.sh` and are exercised for every verified harness by `tests/fm-control.test.sh`, with adapters outside its lane pinning their control mechanics in their own harness suites.
The empirical basis for each adapter's value is the `harness-adapters` skill's verification record for that adapter.

## Verification

- `tests/fm-control.test.sh` - the adapter contract for its verified-harness lane (adapters outside the lane pin their control mechanics in their own harness suites), the backend capability matrix, exact-id scoping, the closed verb list, the busy, idle, dead, and idempotent lifecycle cases, and marker non-regression, all against a stubbed session provider.
- `tests/fm-control-relaunch.test.sh` - the relaunch transaction: identity preservation, harness switching, the progress note, checkpoint refusals, and rollback after a failed launch.
- `tests/fm-relaunch-missing-endpoint.test.sh` - recovery when the endpoint was destroyed outside teardown, including that readiness is read from the replacement's own endpoint and that an unfinished start is not reported as a failure.
- `tests/fm-control-herdr-smoke.test.sh` - the second state-verified backend against the real herdr binary, on an isolated throwaway lab session.
