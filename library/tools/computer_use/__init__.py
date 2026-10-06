"""Computer use above the native driver: Ren's hands, driven by a model.

The layer the captain asked for (2026-10-06): an LLM doing open-ended UI
tasks in Resolve from screenshots plus the AX tree, the LLM planning and
mapping deterministic UI scripts that adapt to window size, and the same
setup reused for apps that have no scripting API.

The executor is the native driver (`native/resolve-control`): it owns
every gate - the qualification project, background input, the locked
screen, permissions - and this package owns nothing but the client side
of its protocol:

    client.py    the driver's Python client: spawn, call, element refs,
                 error mapping. A refusal from the driver is surfaced
                 here, never worked around.
    observe.py   the model's observation: the capture at point size,
                 the projected AX surface, OCR labels.
    actions.py   the action vocabulary the model may return, translated
                 into driver calls.
    loop.py      the observe-decide-act loop with per-step verification,
                 the step budget, and the lease discipline.
    scripts.py   deterministic UI scripts: anchor resolution, per-step
                 verification, LLM fallback for a failed step.
    model.py     the decide half: the observation to one action through
                 an OpenAI-compatible endpoint, with token ledgering
                 and the dollar budget.
    targets.py   where the loop may act: the AppTarget abstraction, the
                 Resolve target (the driver executes), and the Canva
                 target (a scratch-gated declaration with no executor
                 yet).

The design report is `data/vep-computer-use-architecture/report.md`
(firstmate); the measured facts it rests on are in
`docs/RESOLVE_CONTROL_PROBE.md`.
"""
