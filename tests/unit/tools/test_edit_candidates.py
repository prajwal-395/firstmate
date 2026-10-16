import pytest

from library.tools import edit_candidates as ec


def _rows():
    return [
        {"evidence_id": "w1", "plan": "speech_attributed"},
        {"evidence_id": "w2", "plan": "speech_attributed"},
        {"evidence_id": "s1", "plan": "speaker_camera"},
    ]


def _predicates():
    return [
        {"predicate": "speech_present",
         "evidence_names": ["speech_attributed"], "minimum_rows": 2},
        {"predicate": "camera_mapped",
         "evidence_names": ["speaker_camera"], "minimum_rows": 1},
    ]


def test_retrieval_hits_become_planning_moments():
    out = ec.candidates_from_retrieval([
        {"source_id": "IMG_1808", "start_seconds": 10.0,
         "end_seconds": 22.0, "rank": 0},
        {"source_id": "IMG_1808", "start_seconds": 30.0,
         "end_seconds": 30.0},
    ])
    assert len(out[0]["moments"]) == 1
    moment = out[0]["moments"][0]
    assert moment["retrieval_rank"] == 0
    assert "score" not in moment
    assert len(out[0]["dropped"]) == 1


def test_strong_candidate_needs_every_predicate():
    candidate = ec.build_candidate("m1", 10.0, 22.0, _rows(),
                                   _predicates(),
                                   coherence={"status": "ok",
                                              "reason": ""})
    assert candidate["strength"] == "strong"
    assert candidate["evidence_ids"] == ["s1", "w1", "w2"]
    assert all(v["status"] == "verified"
               for v in candidate["predicates"])


def test_failed_predicate_grades_uncertain_with_reason():
    predicates = [{"predicate": "speech_present",
                   "evidence_names": ["speech_attributed"],
                   "minimum_rows": 5}]
    candidate = ec.build_candidate("m1", 10.0, 22.0, _rows(), predicates)
    assert candidate["strength"] == "uncertain"
    assert any("speech_present" in reason
               for reason in candidate["strength_reasons"])


def test_veto_and_incoherence_grade_uncertain():
    vetoed = ec.build_candidate("m1", 10.0, 22.0, _rows(),
                                _predicates(), vetoes=["mid-word"])
    assert vetoed["strength"] == "uncertain"
    incoherent = ec.build_candidate(
        "m1", 10.0, 10.5, _rows(), _predicates(),
        coherence={"status": "refused", "reason": "isolated_word"})
    assert incoherent["strength"] == "uncertain"


def test_unknown_policy_fails_loudly():
    with pytest.raises(KeyError):
        ec.build_candidate("m1", 0.0, 1.0, [], [], policy="cinematic")
    with pytest.raises(KeyError):
        ec.alternative_plans([], policies=["cinematic"])


def test_alternative_plans_rank_same_candidates_differently():
    long_strong = ec.build_candidate("m1", 0.0, 30.0, _rows(),
                                     _predicates())
    short_strong = ec.build_candidate("m2", 40.0, 45.0, _rows(),
                                      _predicates())
    uncertain = ec.build_candidate(
        "m3", 50.0, 90.0, _rows(),
        [{"predicate": "action_present",
          "evidence_names": ["verified_action"], "minimum_rows": 1}])
    assert uncertain["strength"] == "uncertain"
    plans = ec.alternative_plans([short_strong, uncertain, long_strong])
    assert plans["longest_verified"][0] == long_strong["candidate_id"]
    assert plans["earliest"][0] == long_strong["candidate_id"]
    assert plans["latest"][0] == short_strong["candidate_id"]
    for ordering in plans.values():
        assert ordering[-1] == uncertain["candidate_id"]


# ── EDIT-010 consumer: F06 owns speaker/camera mapping ──────────────
#
# vep-pl-f06-multicam maps speakers to cameras (`mapped` only when M7
# links voice to a visible face track; M10 suggestions stay
# `candidate`). This layer never re-derives that: it consumes
# `ground_cuts`-shaped rows (each carrying its query `plan`) and only
# verifies what F06 already decided.


def _f06_mapped_rows():
    return [
        {"evidence_id": "w1", "plan": "speech_attributed",
         "measurement_type": "speech.word"},
        {"evidence_id": "sp1", "plan": "speaker_camera",
         "measurement_type": "event.speaking_span"},
        {"evidence_id": "ff1", "plan": "speaker_camera",
         "measurement_type": "person.face_frame"},
    ]


def test_mapped_speaker_camera_grounds_a_multicam_candidate():
    candidate = ec.build_candidate(
        "mc1", 40.0, 65.0, _f06_mapped_rows(),
        [{"predicate": "speech_present",
          "evidence_names": ["speech_attributed"], "minimum_rows": 1},
         {"predicate": "camera_mapped",
          "evidence_names": ["speaker_camera"], "minimum_rows": 2}])
    assert candidate["strength"] == "strong"


def test_unverified_suggestions_never_verify_a_mapping():
    rows = [
        {"evidence_id": "w1", "plan": "speech_attributed",
         "measurement_type": "speech.word"},
        {"evidence_id": "lr1", "plan": "speaker_camera_suggestion",
         "measurement_type": "event.speaking_span"},
    ]
    candidate = ec.build_candidate(
        "mc2", 40.0, 65.0, rows,
        [{"predicate": "speech_present",
          "evidence_names": ["speech_attributed"], "minimum_rows": 1},
         {"predicate": "camera_mapped",
          "evidence_names": ["speaker_camera"], "minimum_rows": 1}])
    assert candidate["strength"] == "uncertain"
    assert any("camera_mapped" in reason
               for reason in candidate["strength_reasons"])
