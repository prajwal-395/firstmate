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
