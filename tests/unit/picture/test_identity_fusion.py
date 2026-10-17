from library.tools.identity_fusion import (
    fuse_identity_evidence,
    verdict_to_link,
)
from library.tools.person_entity import (
    FACE_MATCH_THRESHOLD,
    VOICE_MATCH_THRESHOLD,
)

FACE = FACE_MATCH_THRESHOLD + 0.05
VOICE = VOICE_MATCH_THRESHOLD + 0.05
BODY_T = 0.40
BODY = BODY_T + 0.05
FAR_BELOW_VOICE = VOICE_MATCH_THRESHOLD - 0.15 - 0.05


def test_face_plus_voice_verifies():
    verdict = fuse_identity_evidence(face_cosine=FACE, voice_cosine=VOICE)
    assert verdict["decision"] == "verified"
    assert verdict["supporting"] == ["face", "voice"]
    assert verdict["contradicting_signals"] == []


def test_face_alone_is_candidate_never_verified():
    verdict = fuse_identity_evidence(face_cosine=FACE)
    assert verdict["decision"] == "candidate"
    assert verdict["supporting"] == ["face"]


def test_voice_alone_is_candidate_voice_never_merges():
    verdict = fuse_identity_evidence(voice_cosine=VOICE)
    assert verdict["decision"] == "candidate"
    assert verdict["supporting"] == ["voice"]


def test_body_alone_without_threshold_is_candidate_and_ignores_value():
    for body in (0.01, 0.50, 0.99):
        verdict = fuse_identity_evidence(body_cosine=body)
        assert verdict["decision"] == "candidate"
        assert verdict["seen"]["body"] == body
        assert "body" not in verdict["supporting"]
        assert any("uncalibrated" in line for line in verdict["basis"])


def test_body_alone_with_threshold_is_candidate():
    verdict = fuse_identity_evidence(body_cosine=BODY, body_threshold=BODY_T)
    assert verdict["decision"] == "candidate"
    assert verdict["supporting"] == ["body"]


def test_cooccurrence_alone_is_rejected():
    verdict = fuse_identity_evidence(cooccurrence=True)
    assert verdict["decision"] == "rejected"
    assert verdict["supporting"] == []
    assert any("without identity measurement" in line for line in verdict["basis"])


def test_no_evidence_is_rejected():
    assert fuse_identity_evidence()["decision"] == "rejected"


def test_face_pass_with_far_below_voice_is_candidate_with_signal():
    verdict = fuse_identity_evidence(face_cosine=FACE, voice_cosine=FAR_BELOW_VOICE)
    assert verdict["decision"] == "candidate"
    assert verdict["contradicting_signals"] == ["voice"]


def test_face_pass_with_slightly_below_voice_is_candidate_without_signal():
    verdict = fuse_identity_evidence(
        face_cosine=FACE, voice_cosine=VOICE_MATCH_THRESHOLD - 0.05
    )
    assert verdict["decision"] == "candidate"
    assert verdict["contradicting_signals"] == []


def test_far_below_voice_vetoes_body_supported_verification():
    verdict = fuse_identity_evidence(
        face_cosine=FACE,
        voice_cosine=FAR_BELOW_VOICE,
        body_cosine=BODY,
        body_threshold=BODY_T,
    )
    assert verdict["decision"] == "candidate"
    assert verdict["contradicting_signals"] == ["voice"]


def test_face_plus_cooccurrence_verifies():
    verdict = fuse_identity_evidence(face_cosine=FACE, cooccurrence=True)
    assert verdict["decision"] == "verified"
    assert "cooccurrence" in verdict["supporting"]


def test_all_below_threshold_is_rejected():
    verdict = fuse_identity_evidence(
        face_cosine=FACE_MATCH_THRESHOLD - 0.2, voice_cosine=VOICE_MATCH_THRESHOLD - 0.2
    )
    assert verdict["decision"] == "rejected"


def test_verdict_to_link_carries_verified_with_support():
    verdict = fuse_identity_evidence(face_cosine=FACE, voice_cosine=VOICE)
    link = verdict_to_link(verdict, "person_001", ["obs-a", "obs-b"])
    assert link["status"] == "verified"
    assert link["entity_id"] == "person_001"
    assert link["observation_id"] == "obs-a"
    assert link["supporting_observation_ids"] == ["obs-a", "obs-b"]
    assert link["contradicting_observation_ids"] == []
    assert link["method"] == "multimodal_fusion"
    assert link["confidence"] is None
    assert link["scope"] is None


def test_verdict_to_link_keeps_candidates_as_candidates():
    for verdict in (
        fuse_identity_evidence(face_cosine=FACE),
        fuse_identity_evidence(voice_cosine=VOICE),
    ):
        link = verdict_to_link(verdict, "person_001", ["obs-a"])
        assert link["status"] == "candidate"
        assert link["supporting_observation_ids"] == ["obs-a"]


def test_rejected_evidence_produces_no_link():
    assert (
        verdict_to_link(
            fuse_identity_evidence(cooccurrence=True), "person_001", ["obs-a"]
        )
        is None
    )


def test_link_requires_evidence_references():
    import pytest

    with pytest.raises(ValueError, match="supporting observations"):
        verdict_to_link(fuse_identity_evidence(face_cosine=FACE), "person_001", [])
