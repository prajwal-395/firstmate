"""Proof in proportion to the edit: what verification this build owes.

The 2026-09-11 round proved a brand new mechanism (the CTA default)
with 35 stills, a full five-reel census and a double-build
persistence proof - and that full burden then stood as the standing
expectation for every small edit, costing 10-15 minutes a round. A
new mechanism earns the full burden; a re-run of an established one
earns stills for the regions that actually changed, and a census only
when something disagrees.

This is scope guidance, not a gate. It is computed from real state
the build already holds - the plan hash against provenance, the
declarations attached to this run, the pre-build census verdict - and
the build PRINTS it and records it. It cannot fail, so per
AGENTS.md 10.4 it must not be shaped as a pass/fail check; a scope
this module cannot compute from real inputs is returned as
`"unknown, prove fully"` rather than guessed at.

What does NOT scale: the kind of proof. The captain's standing rule
holds at every level - verification is on real pixels (Resolve
exports of the graded, conformed frame), never on a property
read-back. This module scales how MUCH is proven, never what counts
as proof.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Sequence


FULL = "FULL"
REDUCED = "REDUCED"


def build_inputs(review_dir: str, proposal_path: str,
                 finals: Sequence[str]) -> dict:
    """The scope inputs read off real state, before the build places.

    `plan_is_new` compares the plan's content hash against the
    provenance record's: a different hash (or no record at all) means
    this plan has never been built, which is the mechanical reading
    of "a new mechanism". `reels_without_prior_proof` are the finals
    with no caption baseline under the current plan hash - the
    regions that actually changed. Anything unreadable degrades to
    "new": no record of proof is not proof.
    """
    from library.tools.plan_provenance import (
        plan_content_hash, read_provenance)

    finals = list(finals or ())
    try:
        content_hash = plan_content_hash(proposal_path)
    except (OSError, ValueError):
        return {"plan_is_new": True,
                "reels_without_prior_proof": list(finals),
                "reason": "the plan cannot be hashed"}
    try:
        existing = read_provenance(review_dir) or {}
    except (OSError, ValueError):
        existing = {}
    if existing.get("plan_content_hash") != content_hash:
        return {"plan_is_new": True,
                "reels_without_prior_proof": list(finals),
                "reason": ("no provenance under this plan hash"
                           if existing.get("plan_content_hash")
                           else "no provenance record yet")}
    proven = set((existing.get("caption_hashes") or {}))
    return {"plan_is_new": False,
            "reels_without_prior_proof":
                [final for final in finals if final not in proven],
            "reason": "same plan hash as the recorded build"}


def scope_for_build(*, reels: Sequence[str], plan_is_new: bool,
                    shared_declarations: Sequence[str],
                    disagreement_reported: bool,
                    reels_without_prior_proof: Sequence[str]) -> dict:
    """What proof this build owes, from the inputs `build_inputs` read.

    FULL when the plan is new, when the run carries shared-state
    declarations (an allow-drop row, a keep insistence or exclusion,
    a redrawn closer, an overlay pin - anything that moves state
    every reel shares), or when the pre-build census disagreed.
    Otherwise REDUCED: stills for the reels without prior proof
    under this plan, and no post-build census beyond the pre-build
    report unless reels are newly proven (then touched-only).
    `reasons` names the trigger, so the printed scope says why.
    """
    reels = list(reels or ())
    shared_declarations = list(shared_declarations or ())
    unproven = list(reels_without_prior_proof or ())
    reasons: List[str] = []
    if plan_is_new:
        reasons.append("the plan has no recorded build")
    if shared_declarations:
        reasons.append("shared-state declarations attached: "
                       + ", ".join(sorted(set(shared_declarations))))
    if disagreement_reported:
        reasons.append("the pre-build census disagreed")
    if reasons:
        return {"level": FULL,
                "stills": list(reels),
                "census": "full",
                "reasons": reasons}
    if unproven:
        return {"level": REDUCED,
                "stills": list(unproven),
                "census": "touched-only",
                "reasons": ["re-run of the recorded plan; proof owed "
                            "only for reels without prior proof"]}
    return {"level": REDUCED,
            "stills": [],
            "census": "pre-build-only",
            "reasons": ["re-run of the recorded plan; every reel proven "
                        "under it - no new stills, no post-build census"]}


def render_scope(scope: Mapping) -> str:
    """The printable scope: what is owed, and why."""
    level = scope.get("level", FULL)
    lines = [f"── Proof owed by this build: {level} ──"]
    for reason in scope.get("reasons", ()):
        lines.append(f"  because {reason}.")
    stills = list(scope.get("stills", ()))
    if level == FULL:
        lines.append(f"  stills (real pixels, Resolve exports): every "
                     f"built reel ({len(stills)}).")
    elif stills:
        lines.append(f"  stills (real pixels, Resolve exports): changed "
                     f"regions only - {', '.join(stills)}.")
    else:
        lines.append(f"  stills (real pixels, Resolve exports): no reel "
                     f"owes new stills - every region already proven "
                     f"under this plan.")
    census = scope.get("census", "full")
    if census == "full":
        lines.append(f"  census: full cross-reel census after the build.")
    elif census == "touched-only":
        lines.append(f"  census: touched reels only.")
    else:
        lines.append(f"  census: none beyond the pre-build report - "
                     f"nothing disagrees.")
    return "\n".join(lines)


def report_scope(scope: Mapping) -> Dict[str, object]:
    """Print the scope and hand it back for the build record."""
    print(render_scope(scope), flush=True)
    return dict(scope)
