"""Lane vep-mu-reid AUD-047: fuse face/voice/body/temporal identity evidence.

Quorum fusion without premature collapse: a verified identity needs the
strong face measurement (ArcFace cosine >= FACE_MATCH_THRESHOLD, the
FAR-biased floor from person_entity) PLUS one supporting second signal.
A single modality above threshold only ever reaches candidate; voice
alone never merges, matching the never-merge rule in
person_entity.join_voices_across_sources. Absence (None) contributes
nothing, never a default score. An uncalibrated body signal (no measured
threshold passed) is reported in `seen` but excluded from the decision.
"""

from __future__ import annotations

try:
    from library.tools.person_entity import (
        FACE_MATCH_THRESHOLD,
        VOICE_MATCH_THRESHOLD,
    )
except ImportError:  # imported as `tools.*` from inside library/
    from tools.person_entity import (
        FACE_MATCH_THRESHOLD,
        VOICE_MATCH_THRESHOLD,
    )

CONTRADICTION_MARGIN = 0.15
"""How far below threshold a measured second modality must fall to count
as weak-signal disagreement (listed in `contradicting_signals`)."""


def fuse_identity_evidence(
    face_cosine=None,
    voice_cosine=None,
    body_cosine=None,
    cooccurrence=False,
    face_threshold=FACE_MATCH_THRESHOLD,
    voice_threshold=VOICE_MATCH_THRESHOLD,
    body_threshold=None,
) -> dict:
    """Fuse optional per-modality cosines into verified/candidate/rejected.

    Verified needs face >= face_threshold AND a supporting second
    signal (voice pass, calibrated body pass, or cooccurrence). A far
    below-threshold second measurement (more than CONTRADICTION_MARGIN
    under its threshold) vetoes verification: the verdict drops to
    candidate with that modality in `contradicting_signals`. That veto
    is weak-signal disagreement only; contradiction records are the
    job of identity_contradictions, never this module.
    """
    seen = {
        "face": face_cosine,
        "voice": voice_cosine,
        "body": body_cosine,
        "cooccurrence": cooccurrence,
    }
    basis: list[str] = []
    supporting: list[str] = []
    contradicting: list[str] = []

    face_pass = face_cosine is not None and face_cosine >= face_threshold
    voice_pass = voice_cosine is not None and voice_cosine >= voice_threshold
    body_calibrated = body_threshold is not None
    body_pass = (
        body_calibrated and body_cosine is not None and body_cosine >= body_threshold
    )

    if face_cosine is None:
        basis.append("face: no measurement (absence, not evidence)")
    else:
        basis.append(
            f"face cosine {face_cosine:.4f} vs threshold "
            f"{face_threshold} "
            f"({'pass' if face_pass else 'below'})"
        )
    if voice_cosine is None:
        basis.append("voice: no measurement (absence, not evidence)")
    else:
        basis.append(
            f"voice cosine {voice_cosine:.4f} vs threshold "
            f"{voice_threshold} "
            f"({'pass' if voice_pass else 'below'})"
        )
    if body_cosine is None:
        basis.append("body: no measurement (absence, not evidence)")
    elif not body_calibrated:
        basis.append(
            f"body cosine {body_cosine:.4f} reported but "
            f"uncalibrated (no measured body threshold passed), "
            f"so excluded from the decision"
        )
    else:
        basis.append(
            f"body cosine {body_cosine:.4f} vs threshold "
            f"{body_threshold} "
            f"({'pass' if body_pass else 'below'})"
        )

    if face_pass:
        supporting.append("face")
    if voice_pass:
        supporting.append("voice")
    if body_pass:
        supporting.append("body")

    if face_pass:
        if (
            voice_cosine is not None
            and voice_cosine < voice_threshold - CONTRADICTION_MARGIN
        ):
            contradicting.append("voice")
        if (
            body_calibrated
            and body_cosine is not None
            and body_cosine < body_threshold - CONTRADICTION_MARGIN
        ):
            contradicting.append("body")

    if face_pass and not contradicting and (voice_pass or body_pass or cooccurrence):
        if cooccurrence and not (voice_pass or body_pass):
            supporting.append("cooccurrence")
            basis.append("cooccurrence supports the passing face measurement")
        decision = "verified"
        basis.append("verified: passing face plus a supporting second signal")
    elif (
        face_cosine is None
        and voice_cosine is None
        and body_cosine is None
        and cooccurrence
    ):
        decision = "rejected"
        basis.append(
            "rejected: co-occurrence without identity measurement proves nothing"
        )
    elif (
        face_cosine is None
        and voice_cosine is None
        and body_cosine is None
        and not cooccurrence
    ):
        decision = "rejected"
        basis.append("rejected: no identity measurement to decide on")
    elif face_pass or voice_pass or body_pass or body_cosine is not None:
        decision = "candidate"
        if face_pass and contradicting:
            basis.append(
                "candidate: face passes but "
                f"{'/'.join(contradicting)} disagree; "
                "no premature collapse to verified"
            )
        elif face_pass:
            basis.append("candidate: face passes alone; one modality never verifies")
        elif voice_pass:
            basis.append("candidate: voice passes alone; voice never merges")
        elif body_pass:
            basis.append("candidate: body passes alone; one modality never verifies")
        else:
            basis.append("candidate: uncalibrated body only; reported, not decided on")
    else:
        decision = "rejected"
        basis.append("rejected: every measured modality is below threshold")

    return {
        "decision": decision,
        "basis": basis,
        "supporting": supporting,
        "contradicting_signals": contradicting,
        "seen": seen,
    }


def verdict_to_link(
    verdict: dict,
    entity_id: str,
    observation_ids: list[str],
    method: str = "multimodal_fusion",
) -> dict | None:
    """Shape a fusion verdict into the mim-l3 identity-link vocabulary.

    Use the Project Model's IdentityLink field names. Rejected evidence
    produces no link; a candidate stays a candidate. Evidence references
    are retained on both candidate and verified links, as in
    identity_reassociation.
    """
    decision = verdict["decision"]
    if decision == "rejected":
        return None
    if decision not in {"candidate", "verified"}:
        raise ValueError(f"unknown identity fusion decision: {decision!r}")
    ids = list(observation_ids)
    if not ids:
        raise ValueError("identity links require supporting observations")
    return {
        "id": f"link-{entity_id}-{ids[0]}-{method}",
        "entity_id": entity_id,
        "observation_id": ids[0],
        "confidence": None,
        "method": method,
        "status": decision,
        "supporting_observation_ids": ids,
        "contradicting_observation_ids": [],
        "provenance_id": None,
        "scope": None,
        "uncertainty": None,
    }
