"""A model plan key no post-bridge reads is refused, never dropped.

Measured defect: a probe's SFX entry carried `at_word`, no post-bridge
key named it, and the whoosh landed 3.06 s early on the block start -
the plan's timing silently replaced by the block start. The same shape
as `unknown_effect_type` in step 4.03 and `UnplayableSfxPlan` in step
4.04: a plan written against something the engine does not read ships
an edit nobody decided on.

Every post-bridge that reads a model plan calls
`refuse_unknown_keys` over the plan list before resolving it, with the
exact set of top-level entry keys its own code reads (legacy aliases
included). The first unread key raises `UnreadPlanKey`, which travels
the existing post-bridge retry path (`run_hybrid_step` catches the
nonzero exit, carries the violation back as feedback, bounded at
`MAX_ATTEMPTS`) so the model re-plans instead of the entry landing
somewhere unasked.

A key the bridge FORWARDS opaquely into its own output counts as
known: it is not lost, and the output contract governs whether a
reader exists downstream. Only keys that are neither read nor
forwarded are refused here.
"""

from __future__ import annotations

from library.tools.ren_refusal import RenRefusal


class UnreadPlanKey(RenRefusal):
    """A plan entry carries a key the step neither reads nor forwards."""


def refuse_unknown_keys(entries: list, known: set, *, step: str,
                         plan: str) -> None:
    """Raise `UnreadPlanKey` on the first unread top-level entry key.

    `entries` is the model-plan list, `known` the exact set of entry
    keys the step reads or forwards, `step` the node id and `plan` the
    plan list's key (e.g. `sfx_creative`). Non-dict entries are skipped:
    each bridge already rejects those on its own terms. The refusal
    names the entry index, the key, and every known key, so the retry
    has something to re-plan against.
    """
    known = set(known)
    for index, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        for key in entry:
            if key not in known:
                raise UnreadPlanKey(
                    what=(
                        f"step {step} plan entry {index} carries key "
                        f"{key!r}, which this step does not read"
                    ),
                    why=(
                        f"an unread key is dropped silently: a probe's "
                        f"SFX planned 'at_word' landed 3.06 s early on "
                        f"the block start. Entry {index} of `{plan}` "
                        f"names {key!r} and nothing in this step reads "
                        f"or forwards it, so acting on the rest of the "
                        f"entry would ship timing nobody decided on."
                    ),
                    fix=(
                        f"re-plan entry {index} of `{plan}` using only "
                        f"these keys: {', '.join(sorted(known))}. Drop "
                        f"{key!r}, or express what it meant with a key "
                        f"above."
                    ),
                )
