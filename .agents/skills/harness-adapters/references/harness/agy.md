# Antigravity CLI

Antigravity's `agy` TUI, verified end to end with agy 1.2.0+ on Linux through the Herdr backend.
Verified as a full Firstmate harness (Primary, Secondmate, and Crewmate/Scout).
`../../../../../docs/verification/agy.md` owns how every fact below was established and what is still unproven.

## Operating facts

| Fact | Value |
|---|---|
| Binary | Absolute `agy` from `PATH`, refused if absent; a Go-compiled single binary, so the live process name is exactly `agy` with `argv[0]=agy`. |
| Launch | `agy --prompt-interactive "Read the brief at <absolute-path> and follow it exactly." --model <id> --effort <level> --dangerously-skip-permissions`, with the resolved absolute binary; the pointer auto-submits with no extra Enter. The spawn checks (read-only) whether the worktree itself is already covered by an exact-path entry in the operator's trust store, then waits for a busy turn (answering the folder-trust dialog if it renders anyway) before reporting success. |
| Busy state | Owned lifecycle hooks: `PreInvocation` opens a turn and `Stop` closes it (`PostInvocation` is NOT a close - it fires after each tool batch inside a turn), delivered by the ONE firstmate-owned global plugin `bin/fm-spawn.sh` installs; the `agy-hook` semantic source in `../../../../../bin/fm-busy-lib.sh` trusts them. On Herdr the native `working` status also classifies busy, and the `agy-regex` rendered-tail fallback backstops the window before the first hook lands. |
| Rendered tail | Busy status row carries `esc to cancel` on the left; the idle row shows `? for shortcuts` instead. The `Generating...` word beside the braille spinner is free-floating output and is not a signal. |
| Turn end | Owned `Stop` hook through the same global plugin: it touches the task's turn-end marker and records the idle close, while the watcher's generic unified descent moves a worker whose recorded rung is proven exhausted. |
| Exit | `/quit`, one Enter; the process exits. |
| Interrupt | Single `Escape`, which prints the Interrupted row and leaves an idle composer with no repollution, so no clear key follows. |
| Skill | No verified slash-skill form; use natural language. |
| Autonomy | `--dangerously-skip-permissions` auto-approves tool calls for the run. |
| Marker | None; a live TUI carries no `AGY_*` or `ANTIGRAVITY_*` variable. |
| Resume | `--continue` and `--conversation` exist but carry no verified pane-resume contract; use deterministic relaunch. |
| Model | `--model <id>` with the bare catalog id from `agy models` (for example `gemini-3.8-flash-high`); `bin/fm-spawn.sh` refuses a requested id a reachable listing omits. The listing is a remote fetch, so the probe runs stdin-detached under the shared hard bound and an unreachable or hung listing launches unvalidated with a notice. |
| Effort | `--effort low\|medium\|high`; unsupported `xhigh` and `max` are reported and omitted from task metadata and launch flags. |
| Composer | Borderless bare `>` row, which the shared classifier reads as `unknown` under the dead-shell rule, never `empty`; steering confirms delivery through native agent-state and the delivery footer instead, the cursor precedent. |

## Trust, and where the decision persists

Every task worktree is a path agy has never seen, so an unregistered launch stops on `Do you trust the contents of this project?` with the safe choice `Yes, I trust this folder` preselected, and an unanswered dialog sends the turn into agy's scratch directory instead of the worktree.
There is no launch flag that suppresses the dialog, but agy honours a `trustedWorkspaces` entry in the captain's own `~/.gemini/antigravity-cli/settings.json` written ahead of launch (verified live). Firstmate deliberately does NOT write that entry (`../../../../../bin/fm-agy-trust.sh` is not called): agy matches entries by exact path, so a broad parent entry never covers a fresh pool worktree and the dialog fires on every new pool until that exact path is registered, and editing the operator's agy settings file is not a write firstmate takes on. The spawn only READS the store to decide whether the exact path is already covered.
The post-launch readiness gate is the backstop: it answers a dialog that renders anyway with a single Enter, then requires a busy verdict (Herdr's native `working` status or the pinned `esc to cancel` row) before the spawn reports success, and on an uncovered path it never counts a busy verdict as ready until the dialog has been answered, because Herdr's native verdict can precede the dialog.
A pane whose brief cannot be confirmed to run in the worktree fails the spawn, records the failure in the task status, and closes the endpoint.
Never steer into a pane still showing the dialog; a spawn that reported success has already cleared it.

## Credential precondition

A verified agy worker ran under a signed-in Google account with no key export and no dialog.
The unauthenticated failure mode was not observed, so treat any auth prompt or refusal as a credential blocker under `../../../../../AGENTS.md` section 9, fix the environment, and retire the endpoint rather than typing into it.

## Detection

Detected by ancestry alone: `../../../../../bin/fm-harness.sh` matches the anchored process name `agy`, never `*agy*`.
No environment marker is promoted: `AGENT=1` observed on a live TUI is an inherited launcher value, not an agy identity, and agy does not clear an inherited `CLAUDECODE` - but a structural agy ancestor now outranks that retained marker, which `../../../../../bin/fm-harness.sh` decides without depending on the spawn's own launch-boundary marker clearing.
agy is included in the session-lock name vocabulary in `../../../../../bin/fm-session-lock-lib.sh`, allowing an agy session to hold a home session lock.

## Worker busy state and turn end

`../../../../../bin/fm-spawn.sh` arms a busy generation for agy and installs the ONE firstmate-owned global hook plugin (`~/.gemini/config/plugins/fm-turn-end/`, per-task registry entry): `PreInvocation` opens the turn, `Stop` closes it and touches the turn-end marker, `SessionStart` records the conversation sidecar, and `PostInvocation` carries the point-of-spend gate. The plugin is a guarded no-op for every agy session whose workspace firstmate did not register.
`fm_busy_agy_tail_busy` matches the pinned `esc to cancel` status row alone, hardcoded with no environment override, and `fm_busy_classify` reports `unknown agy-regex` rather than idle when it is absent, because a long turn can scroll the marker out of the captured tail. It is the backstop beneath the hook record, not the source.
Teardown removes the worktree pointer, the registry entry, the session sidecar, and the ladder pin.

## Primary and secondmate integration

Antigravity CLI loads `<workspace>/.agents/hooks.json` for hook lifecycle events, running each command from `.agents/` itself.
agy 1.4.9 reads every top-level key as a named hook spec (firstmate ships one, `firstmate`), with flat `SessionStart` and `Stop` handler lists and `PreToolUse` grouped under `matcher` and `hooks`; any other top-level key, such as a `version` field, silently disables the whole file (verified live).
A PreToolUse handler that prints nothing allows the call, while a printed object without a `decision` denies it, so the seatbelts print only on deny.
SessionStart executes `bin/fm-sessionstart-agy.sh` to inject the session-start digest into model context.
PreToolUse invokes `bin/fm-arm-pretool-check.sh --agy`, `bin/fm-cd-pretool-check.sh --agy`, and `bin/fm-subagent-pretool-check.sh --agy`.
Stop executes `bin/fm-turnend-guard-agy.sh`, which parks on a home-scoped watcher cycle through `bin/fm-watch-arm.sh` (or `bin/fm-supervision-host.sh park` on a home that runs the host) under the registered 28800s hook timeout and delivers actionable wake output; only an optional `FM_AGY_PARK_TIMEOUT` bound re-arms via `{"decision":"continue","reason":"..."}` with `[supervision-park]`, and `FM_AGY_TURNEND_LOOP_CEILING` (default 180) bounds hook-driven turns.
A bounded 3-block repair nag is enforced via `bin/fm-turnend-guard.sh --agy` before failing open with `{}`.
See `../../../../../docs/supervision-protocols/agy.md` for the complete supervision protocol.
