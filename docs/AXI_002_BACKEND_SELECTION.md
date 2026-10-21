# AXI-002: one semantic request, one routing decision

**Lane:** `fm/p-axi002-backend-selection`
**Punch list:** AXI-002, 2026-10-20 ("Eliminate conflicting backend
selection logic"), measured against the tree at `a84073bc8`.
**Acceptance:** "One semantic request has one auditable routing
decision."

## What the punch list asked

- Keep `execution_router.py` authoritative.
- Audit the secondary backend guards in `resolve_axi_core.py`.
- Remove or consolidate duplicate backend eligibility judgments WITHOUT
  removing their safety checks.
- Ensure the agent never chooses a physical execution backend to obtain
  broader capabilities.
- Ensure router decisions state why one implementation is preferred.

## What was there

Eight places answered "which execution backend runs this request".
Seven of them answered it with a set of route names of their own, and
one of them - inside the router - answered it twice.

| # | Site | What it decided |
|---|---|---|
| 1 | `execution_router.route_compiled_edit` | the route (the selector) |
| 2 | `execution_router._route_compiled_edit`, a second Resolve AXI arm | the same route, with the compound-shape check on top |
| 3 | `resolve_axi_core._execute_registered_backend` | whether the route admits the operation, and which physical adapter runs it |
| 4 | `resolve_axi_core.submit_edit_request_async` | which broker job carries the route |
| 5 | `resolve_axi_core.register_edit_executor` | which backend an executor may be registered on |
| 6 | `resolve_axi_core._plan_transaction_patch` | the legacy typed path's route |
| 7 | `resolved.jobs.prepare` | which routes a semantic job may carry |
| 8 | `feedback_patch_apply.guarded_apply` | which routes the guarded path accepts |

Sites 3, 4, 5, 7 and 8 each carried their own set of route names:
`{"incremental", "resolve_native_invocation"}`,
`{"reel_touchup", "resolve_axi"}`, `{"edit_patch", "edit_patch.property"}`,
`{"edit_patch", "edit_patch.property", "no_op"}` and
`{"edit_patch", "edit_patch.property"}`. The router carried the
protected single-operation list and the protected compound pairs as
literals of its own, and the protected policy
(`library/tools/edit_qualification_policy.py`) carried them a third
time.

Site 2 was unreachable: every backend row of a declared operation is
`compilable`, so the first Resolve AXI arm had already taken every
request the second could have taken. It was still a second answer to
the same question, and it answered with a DIFFERENT rule - it added the
compound-shape check - so it would have chosen differently for every
multi-operation request whose shape has no declared compound scenario.
Measured: **35** such shapes among the six operations that are
production-enabled on the AXI adapter.

Site 3 was a second ELIGIBILITY judgment: it asked the matrix whether
an operation was production-enabled on the route, using its own two
frozensets, and could therefore disagree with the router's answer
about the very route it had been handed.

## The recorded evidence

The machine broker's own job records hold every semantic edit the
engine has run. Reading them read-only on 2026-10-21:

- **38** `timeline.semantic_edit` jobs, and **129** recorded `ren revise`
  receipts in the Ren-owned scratch projects - **167** recorded
  requests in total.
- **1** of them (`53ed6ee53eb3caab`, a `clip.delete` planned against
  `resolve_axi` and executed against `edit_patch`) had **two routing
  decisions**: the plan named one route and the execution chose
  another. The request died before it published a receipt, so the
  broker's record of it was a bare error string and an EMPTY result
  column - the receipt trail showed nothing at all.
- On the other **166** recorded requests the duplicate judgments
  happened to agree. They were copies, not a designed agreement.

## What changed

The router is now the only thing that decides, and it publishes the
answers the other sites used to re-derive:

- `ROUTE_TRANSPORT` - the route, the broker job kind that carries it,
  and the shape of the request that job carries. The dispatcher, the
  broker's submission gate and the front door read this table.
- `ROUTE_ADMISSION` - how a route admits an operation: every operation
  production-enabled on it, or the protected experimental policy plus a
  recorded qualification gap. `route_admission_reasons` is the ONE
  eligibility judgment; the dispatcher asks it instead of asking the
  matrix a second time.
- `ROUTE_ADAPTER` - the physical adapter a route runs. The router names
  the adapter in the decision (`selection.adapter`), so the dispatcher
  runs the named one instead of re-deriving a path from the operation
  set. A batch of deletes names the delete-batch adapter; a protected
  compound names the compound touch-up adapter.
- The protected single-operation list and the protected compound pairs
  are read from `edit_qualification_policy` - the policy that already
  owned them - instead of being copied into the router.
- The unreachable second Resolve AXI arm is deleted.
- `REQUESTABLE_ROUTES` - a caller may ask for a STRATEGY (`full_rebuild`,
  `no_op`) and never for a physical backend. A request that named a
  backend used to be IGNORED, which leaves the caller believing it
  chose the implementation; it is now refused by name.
- One request, one decision: the executor CONFIRMS the route the
  request carries against the measured runtime instead of choosing a
  second one. A route the runtime no longer authorizes is REFUSED, with
  a receipt that records the route the request carried and why - the
  recorded case above died with no receipt at all.

## The measurement

`scripts/measure_backend_selection.py` computes both numbers over the
same corpus, before and after, and prints every denominator. On
2026-10-21:

```
requests measured: 167 (broker 38, project 129)
selection sites that DECIDE: before 8, after 1 (all sites after: 8)
requests where two sites would have chosen differently: before 1/167, after 0/167
  (the dispatcher's guard was evaluable for 151/167 requests; the route change was observable for 121/167)
requests whose route changed between the plan and the execution: 1/121 observable (of which 1 recorded no decision at all)
shapes on which the selector's two Resolve AXI arms disagreed: 35
```

The BEFORE numbers are computed from an explicit model of the deleted
sets (in the script, with the file and line each was on); the AFTER
numbers are computed from the consolidated code. Nothing is asserted.

**Fidelity.** The old and the new router were run side by side over
the whole operation vocabulary:

- **144** single-operation decisions (84 operations x their backends x
  the production and protected policies): **0** differences.
- **6972** two-operation decisions (every pair of the 84 operations x
  the two policies): **0** differences.

Every safety check the dispatcher used to make survived the move: an
operation that is not production-enabled on its route is still refused,
an unqualified operation on a protected route still needs the protected
policy, and it still needs the router's own recorded qualification gap.
`tests/unit/resolve/test_backend_selection.py` names each of those.

## What was left undone

- The router does not require a DECLARED COMPOUND SHAPE before it
  selects the AXI adapter for a multi-operation request. It routes the
  request to `resolve_axi`, and the transaction planner refuses the
  shape before the first write. That is a refusal, not a second
  choice, so it is not counted above - but the router still names a
  route that cannot execute the shape it was given. Making the router
  require the declared shape would push those requests onto the
  EditPatch arm, which admits operations that are only COMPILABLE there
  and not production-enabled, so it needs its own decision about
  qualification. Not done in this lane.
- The legacy typed EditPatch path (`_plan_transaction_patch`) is a
  FIXED route, not a choice: a typed EditPatch transaction has one
  executor, so there is nothing to compare. It now records the router's
  declaration for that route, so its plan is auditable in the same
  vocabulary, but it was not made to consult the router for
  eligibility - the router's per-operation identity checks would
  refuse a patch whose targets are addressed by record frame, and
  closing that path would be a capability regression.
- The Rust/Python computational switch (`edit_backend.use_rust`) is a
  different axis from the execution backend, and it was already a
  single switch. It is named in the enumeration as a `switch` and was
  not changed.
- The recorded corpus is the broker's own job records plus the
  `ren revise` receipts in the Ren-owned scratch projects. The
  captain's projects were not read.
