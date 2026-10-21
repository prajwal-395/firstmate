"""The ONE owner of the protected experimental routing policy.

An operation whose executor is IMPLEMENTED but not PRODUCTION-ENABLED may
still run on a protected staged candidate when the request comes from a
front door that owns that guarantee. Which operations are covered, which
originators may use the route, and the guarantees the route requires are
stated HERE, because two places needed them and they disagreed:

- `resolve_axi_core` granted the protected route to `ren revise` and
  `ren touch` for a named set of operations.
- `feedback_patch`, the front door's own classifier, refused every
  unqualified operation BEFORE the planner was asked - so the grant was
  unreachable from `ren revise`, and only the three production-enabled
  operations could ever be edited through the front door.

Both now read this module, so the classifier and the planner agree. The
policy widens nothing on its own: an operation not named here is refused
by the production policy exactly as before, and the guarantees below are
what the router checks before it selects the protected backend.

An operation is named here only when it HAS AN EXECUTOR. A protected
route may name a path that exists, never a vocabulary entry, so a named
operation with no implemented executor is refused by this module rather
than granted a route that runs nothing.

The live unfamiliar-brief benchmark (PR 2989) measured operations that have
an executor and NO route: `reel.opening`, a lone `clip.trim`,
`multicam.select_angle`, `broll.insert` and `caption_fix` were refused
by the router under the production policy and were not covered by this
policy either, and a lone `clip.replace` was unroutable too - so
"tighten the opening" and "swap this clip's source" had no route at
all. They are named here now, and they stay out of production routing
until live evidence qualifies them for it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: The front doors that own the protected staging guarantee.
PROTECTED_ORIGINATORS = frozenset({"ren revise", "ren touch"})

#: Operations that may run alone under the protected experimental policy.
PROTECTED_EXPERIMENTAL_SINGLE_OPS = frozenset({
    "audio.gain",
    "audio.fade",
    "transition",
    "overlay.resize",
    "overlay.position",
    "clip.split",
    "clip.slide",
    "clip.roll",
    # The operations the live unfamiliar-brief benchmark (PR 2989) found
    # with an executor and no route at all. Each runs on the protected
    # route; none is production-qualified, and nothing here grants that.
    "broll.insert",
    "caption_fix",
    "clip.replace",
    "clip.trim",
    "multicam.select_angle",
    "reel.opening",
})

#: Operation PAIRS that may run under the protected experimental policy.
PROTECTED_EXPERIMENTAL_COMPOUNDS = (
    frozenset({"clip.trim", "clip.insert"}),
    frozenset({"clip.replace", "caption.style"}),
)

#: The guarantees the router requires before it selects a protected route.
PROTECTED_EXPERIMENTAL_GUARANTEES = (
    "protected_staging",
    "preserve_approved_state",
    "verify_after_write",
)


def has_implemented_executor(operation_id: str) -> bool:
    """Whether an executor for this operation exists at all.

    The protected route may only name a path that exists. An operation
    with no implemented executor is not covered by this policy, so a
    grant can never reach a vocabulary entry that nothing runs.
    """
    from library.tools import edit_algebra

    entry = edit_algebra.status(str(operation_id))
    if entry is None:
        return False
    return any(backend.implementation_evidence
               for backend in entry.backends)


def is_protected_experiment(operation_ids: Iterable[str], *,
                             originator_name: str = "") -> bool:
    """Whether this operation set is covered by the protected policy."""
    return protected_experimental_context(
        operation_ids, originator_name=originator_name) is not None


def protected_experimental_context(operation_ids: Iterable[str], *,
                                   originator_name: str = "",
                                   ) -> dict | None:
    """The routing context for a protected experimental run, or None.

    None means the request is NOT covered: either the originator does not
    own the guarantee, or the operation set is not one the policy names.
    The caller then routes under the production policy unchanged.
    """
    if str(originator_name or "").strip().casefold() not in PROTECTED_ORIGINATORS:
        return None
    ids = [str(op_id) for op_id in operation_ids]
    single = len(ids) == 1 and ids[0] in PROTECTED_EXPERIMENTAL_SINGLE_OPS
    compound = (len(ids) == 2
                and frozenset(ids) in PROTECTED_EXPERIMENTAL_COMPOUNDS)
    if not (single or compound):
        return None
    if not all(has_implemented_executor(op_id) for op_id in ids):
        return None
    return {
        "qualification_policy": "protected_experimental",
        "protected_staging": True,
        "preserve_approved_state": True,
        "verify_after_write": True,
    }


def policy_description() -> Mapping[str, object]:
    """The policy as data, for a receipt or a refusal that names it."""
    return {
        "originators": sorted(PROTECTED_ORIGINATORS),
        "single_ops": sorted(PROTECTED_EXPERIMENTAL_SINGLE_OPS),
        "compounds": sorted(
            " + ".join(sorted(pair)) for pair in PROTECTED_EXPERIMENTAL_COMPOUNDS),
        "guarantees": list(PROTECTED_EXPERIMENTAL_GUARANTEES),
    }
