---
name: resolve-axi
description: >-
  Use Resolve AXI when a Ren workflow must inspect or change DaVinci Resolve.
  Covers capability discovery, guarded execution, verification and recovery;
  it does not choose the editorial intent.
---

# Resolve AXI

Ren owns editorial intent. Resolve AXI resolves live state, selects an eligible execution path, applies the typed operation and reports what read-back proves. Use AXI's current discovery output rather than relying on remembered operation or backend coverage.

## Operating loop

1. **Inspect.** Confirm the exact project, timeline and target through AXI. `bin/resolve-axi target "<description>" --timeline "<name>"` resolves a DESCRIBED target - `id=<clip id>`, `name=<clip name>`, `row=V2 item=2`, or `start=<frame> end=<frame>` - to the clip it names, and prints the exact addressing arguments the mutating verbs take. A description that does not name exactly one clip is refused with the candidates named, so count no rows by hand. Stale, missing or ambiguous targets refuse mutation.
2. **Discover.** Start with `bin/resolve-axi --help` and the relevant command's `--help`. Ask the live registry with `bin/resolve-axi capabilities --search "<requested effect>"`, then read the canonical operation map: `bin/resolve-axi operation-map <operation>` answers one operation and `bin/resolve-axi operation-map` answers all of them, both as JSON. The map is where semantics, dependencies, backend capabilities, preservation constraints, qualification evidence and supported project states live, and its `how_to_request` says HOW the operation is reached - a semantic `edit-request` route, or the AXI command that performs it. Never choose a backend by name and never reach past AXI into the tree: two mechanisms can write the same operation and only one of them is qualified. For Resolve API surface discovery, use `bin/resolve-axi api search "<method or concept>"`, then `api stubs <Type>` or `api docs --section "<heading>"`; `bin/resolve-axi capabilities native_mcp.<tool>` reports the official MCP inventory entry. Discovery identifies evidence and constraints, not permission to bypass semantic edit authorization. An operation that is not production-enabled, and an id AXI does not hold, both come back with a `guidance` block: its `status` says which gap it is, `alternatives` names the operations that ARE production-enabled, and `fix` is the next step. Read that block; do not infer support from a failed lookup.
3. **Prepare and execute.** Express the requested change as an AXI semantic `EditRequest`; `bin/resolve-axi edit-request plan <request.json>` prepares and routes it without a Resolve write. Review its exact targets, planned changes, route and refusal reasons. Submit only when authorized: `bin/resolve-axi edit-request submit <request.json> --apply`; check asynchronous work with `bin/resolve-axi edit-request status <job-id> --wait 30`. AXI resolves the eligible execution path and owns authorization, transaction and the single-instance broker/lease. Never select or force a backend for a supported edit. The registry distinguishes production enabled, qualified but not production enabled, and unqualified or unavailable operations, and the operation map's `support_level` carries the same four bands for one operation: `production_enabled` (the only one ordinary routing may use), `context_qualified` (live evidence, inside the context it was earned in), `implemented` (a writer exists and no live qualification does) and `unsupported`. The operation matrix is evidence, not permission: ordinary requests use production routing. AXI may use an unqualified implementation only under a protected experimental policy with explicit guarantees; this does not qualify or enable it for production. If no production route is eligible, relay the refusal and its next action instead of silently substituting an edit or rebuild.
4. **Verify and preserve.** Treat the merged execution receipt as the account of what happened. Check its request/transaction identity, selected route, requested and actual changes, verification evidence, recovery status and lifecycle stage. The route's `selection` also states the **mutation scope** - `targeted` touches only what changed, `full_rebuild` reconstructs the timeline - and the **transaction ownership** - the broker kind that owns the write and the lease it runs under; a receipt that shows `full_rebuild` says that everything it did not carry over was disturbed. Then read the exact target back fresh and compare it with the user's requested effect, including item identity and exact timing or properties. Add visual or audio evidence when structural read-back cannot prove the result. Follow [recovery guidance](references/recovery.md) before retrying. `APPLIED` is not `VERIFIED`; `VERIFIED` is not `COMMITTED`; a staged timeline is not promoted; a committed or promoted change is not `DELIVERED` until the requested export and its checks exist. If the result is `recovery_required`, stop editing and follow the receipt's recovery path until state is restored or explicitly recoverable. Report only the highest stage the receipt and independent evidence establish.

### Refusals and next actions

Use the structured refusal and execution receipt, not the command's exit code, to decide what happens next. The receipt's `recovery.instruction` carries the next step as DATA - `category`, `recovery_class`, `action` and `reason` - so it is read, never improvised; `library/tools/recovery_instruction.py` owns the join between a failure category and its recovery action.

- `unsupported`: the requested semantics have no eligible production route. Keep the timeline unchanged and report the operation or qualification gap; do not retry through a different backend or rebuild unless the user explicitly authorized that route.
- `stale`: the request was based on an old timeline generation. Re-read the timeline, resolve the target again and prepare a fresh request.
- `conflict`: live state changed or disagrees with the request. Preserve the current state and ask for direction if the intended result is no longer clear.
- `backend_failure`: execution failed. Read the transaction receipt and recovery result before deciding whether another attempt is safe.
- `recovery_required`: compensation did not establish the original state. Do not submit more edits; use the named recovery action and verify its read-back first.

For user-facing refusals, follow `recovery_class`: `auto_recoverable` permits only the named safe recovery, `needs_question` requires the smallest blocking question, and `blocked` means explain the condition that must be resolved. A qualification gap is not an invitation to opt into experimental execution.

## Ownership

Use this skill for Resolve mechanics. Use `ren-co-editor` for the user's editorial intent, footage research, approval questions and delivery conversation. Use `reel_build` for the approved reel build workflow, and `verify_timeline` / `verify_render` for their specific gates. Resolve's native API, official MCPs and UI are discoverable execution mechanisms behind AXI, not alternate production write paths. Research Resolve changes with `bin/resolve-axi api whats-new --since <version>` and confirm methods with API search, stubs or docs. Monitor upstream third-party releases for useful capabilities, then check whether AXI already exposes an equivalent. Do not duplicate a native method or expose raw MCP scripting as edit authorization. New operations enter the existing AXI registry and execution path only after implementation and qualification. UI automation remains behind AXI and is not a bypass. The operation map is the registry a request is routed against: it is generated from the algebra and the implementations by `library/tools/operation_map.py`, so an operation's route is read there rather than chosen.

## Generated references

The capability references in `references/` are generated byte-for-byte by `bin/vep -m library.tools.operations --emit-resolve-axi-references` and checked for drift by `tests/tooling/test_skill_source.py`. Do not hand-edit their tables; change the owning registry and regenerate them. Treat the live capability query and the request's receipt as authoritative for the current route and outcome. Open only what the request needs:

- [capability matrix](references/capability-matrix.md) - operation availability, route constraints, recovery and measured cost.
- [concepts](references/concepts.md) - operation vocabulary and evidence meanings.
- [transactions](references/transactions.md) - compilable operations.
- [verification](references/verification.md) - operation read-back contracts.
- [recovery](references/recovery.md) - inverse metadata and limits.
- [backend limitations](references/backend-limitations.md) - execution-route evidence and cross-cutting constraints.

If discovery shows no eligible capability, state that AXI cannot currently perform the requested operation. Do not infer support from a raw Resolve method, a generated row, or a successful API call.
