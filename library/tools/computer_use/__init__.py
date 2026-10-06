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

The design report is `data/vep-computer-use-architecture/report.md`
(firstmate); the measured facts it rests on are in
`docs/RESOLVE_CONTROL_PROBE.md`.
"""
