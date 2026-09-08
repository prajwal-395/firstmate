# agy point-of-spend gate - verification record

Evidence backing the PostInvocation spend gate in `bin/fm-agy-descent-lib.sh`
(`fm_agy_spend_gate_verdict`, run by `bin/fm-agy-spend-gate.sh`), which ends a
running agy worker's turn the moment its own rung is proven exhausted, and the
plugin enablement in `bin/fm-agy-lib.sh` without which no agy hook fires at all.

Everything below is a VENDOR fact: which hooks agy fires, what their payloads
carry, which verdicts the loop honours, and what it takes for a plugin's hooks
to run. None of it is inferable from firstmate's own code, and all of it can
change in an agy release, so it is pinned here with the exact output rather
than assumed. `tests/fm-agy-spend-gate-live-e2e.test.sh` is the command that
refreshes the firing and verdict claims; run it after every agy upgrade.

Captured 2026-09-07 against agy 1.1.27 on macOS (`darwin-arm64`), in disposable
Herdr lab sessions on `Gemini 3.7 Flash (High)`. Nothing here spent Opus quota.

## The load-bearing claims

An entry-less global plugin never fires. A plugin under
`~/.gemini/config/plugins/` with a valid `plugin.json` and `hooks.json` (both
confirmed by `agy plugin validate`) loaded no hooks until it was recorded
enabled: every launch logged `loaded 2 named hooks from 1 hooks.json file(s)`
(the global `hooks.json` only), and a guarded probe hook logged zero firings
across a whole tool-using turn. After `agy plugin enable`, the same plugin
fired every event exactly once. The fleet's `fm-turn-end` plugin carries no
config entry, so on this build its SessionStart, PreInvocation, and Stop are
dark: the Stop-driven ladder evaluation and the semantic busy push do not run,
and the descent switch guard (idle-only) cannot be satisfied. `fm_agy_ensure_plugin_enabled` exists for exactly this.

PostInvocation fires after every tool batch with a payload the hook can
resolve. One prompt producing two tool batches logged, in order, PreInvocation
(`invocationNum` 0), PostInvocation, PreInvocation (`invocationNum` 1),
PostInvocation, then Stop with `fullyIdle: true`. Every payload carries
`workspacePaths`, `conversationId`, `modelName`, `invocationNum`, and
`initialNumSteps`. A second registration of the same command (global plugin
plus workspace `hooks.json`) fires twice per event; one registration fires
exactly once.

`{}` passes a tool-using turn untouched. With the probe answering `{}`, a
list-files-and-count prompt completed normally across two tool batches.

`terminate` ends the loop after the in-flight batch and Stop fires. With the
probe answering `{"terminationBehavior":"terminate",...}` on PostInvocation, a
three-step prompt executed only its first tool call, no further PreInvocation
fired, and Stop followed immediately with the worker idle at its composer.

Print mode loads only the global `hooks.json`. An `agy --print` run from the
lab workspace logged `loaded 2 named hooks from 1 hooks.json file(s)` and no
workspace hook fired, so print mode cannot carry the gate and the guard above
is interactive-only evidence.

## Exact hook contracts (from the binary's own embedded guide)

`PreInvocation` runs before the model is called and answers `injectSteps`
only: it cannot block. `PreToolUse` answers `allow`/`deny`/`ask`/`force_ask`
per tool call, but a deny gates the tool while the loop and its model spend
continue, so it is not the gate. `PostInvocation` answers `injectSteps` plus
`terminationBehavior` (`force_continue`, `terminate`, or omitted): terminate
forces the loop to stop. `Stop` answers `decision: continue` to re-enter the
loop and anything else to let the turn end; a non-zero exit is hook failure,
never a semantic signal, so every path must print a JSON object.
