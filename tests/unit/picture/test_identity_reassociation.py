from library.tools.identity_reassociation import (
    promote_identity_scope,
    reassociate_tracks,
    score_identity_switches,
)


def _model():
    return {
        "assets": [{"id": "asset", "uri": "/media/IMG_1816.MOV"}],
        "clocks": [{"id": "clock", "domain": {"asset_id": "asset"}}],
        "entities": [
            {"id": "cup-a", "kind": "object", "object_class_id": "cup",
             "identity_scope": "asset"},
            {"id": "cup-b", "kind": "object", "object_class_id": "cup",
             "identity_scope": "asset"},
        ],
        "tracks": [
            {"id": "track-a", "local_track_id": "sam/1",
             "entity_id": "cup-a", "clock_id": "clock",
             "samples": [{"tick": 0,
                          "geometry": {"bbox": [0, 0, .2, .2]}}]},
            {"id": "track-b", "local_track_id": "sam/2",
             "entity_id": "cup-b", "clock_id": "clock",
             "samples": [{"tick": 500,
                          "geometry": {"bbox": [.1, .1, .3, .3]}}]},
        ],
        "observations": [
            {"id": "obs-a", "interval": {"clock_id": "clock", "start": 0,
                                               "end": 101},
             "payload": {"track_id": "track-a"}},
            {"id": "obs-b", "interval": {"clock_id": "clock", "start": 500,
                                               "end": 601},
             "payload": {"track_id": "track-b"}},
        ],
        "identity_links": [],
    }


def test_reassociation_across_occlusion_is_a_track_scoped_candidate():
    model = _model()
    result = reassociate_tracks(model, max_gap_ticks=500)
    assert len(result["links"]) == 1
    link = result["links"][0]
    assert link["entity_id"] == "cup-a"
    assert link["track_id"] == "track-b"
    assert link["observation_id"] == "obs-b"
    assert link["status"] == "candidate"
    assert link["supporting_observation_ids"] == ["obs-a", "obs-b"]


def test_reassociation_skips_a_same_class_track_far_from_the_occlusion_exit():
    model = _model()
    model["tracks"][1]["samples"][0]["geometry"]["bbox"] = [.7, .7, .9, .9]
    result = reassociate_tracks(model)
    assert result["links"] == []
    assert result["skipped"] == [{
        "earlier": "track-a", "later": "track-b",
        "reason": "endpoint-geometry-too-distant",
    }]


def test_reassociation_refuses_verified_claim_without_evidence():
    try:
        reassociate_tracks(_model(), verify=True)
    except ValueError as exc:
        assert "requires supporting observations" in str(exc)
    else:
        raise AssertionError("unsupported re-identification was verified")


def test_a_candidate_may_carry_no_support_at_all():
    """A proposal with no evidence behind it yet is expressible.

    The candidate path could not record "no support yet": the endpoint
    observations were always added as support, so the case was
    unreachable. Only a VERIFIED link must carry support.
    """
    result = reassociate_tracks(_model(), max_gap_ticks=500,
                                supporting_observation_ids=[])
    assert len(result["links"]) == 1
    link = result["links"][0]
    assert link["status"] == "candidate"
    assert link["supporting_observation_ids"] == []


def test_a_candidate_with_support_keeps_the_endpoint_observations():
    result = reassociate_tracks(_model(), max_gap_ticks=500,
                                supporting_observation_ids=["obs-a"])
    link = result["links"][0]
    assert link["supporting_observation_ids"] == ["obs-a", "obs-b"]


def test_scope_promotion_requires_verified_link_and_known_evidence():
    model = _model()
    entity = model["entities"][0]
    model["identity_links"].append({
        "entity_id": "cup-a", "status": "verified",
        "supporting_observation_ids": ["obs-a"],
    })
    assert promote_identity_scope(
        model, "cup-a", "project", supporting_observation_ids=["obs-a"]
    ) is entity
    assert entity["identity_scope"] == "project"


def test_identity_switch_metric_matches_boxes_and_scores_candidate_reassociation():
    model = _model()
    model["identity_links"].extend(reassociate_tracks(model)["links"])
    labels = {"identities": [{"label_id": "cup", "kind": "object",
                               "source_file": "IMG_1816.MOV",
                               "frames": [
                                   {"time_seconds": 0, "bbox": [0, 0, .2, .2]},
                                   {"time_seconds": .5,
                                    "bbox": [.1, .1, .3, .3]},
                                   {"time_seconds": 2, "bbox": [0, 0, .2, .2]},
                               ]}]}
    report = score_identity_switches(model, labels)
    assert report["switches_before"] == 1
    assert report["switches_after"] == 0
    assert report["misses"] == 1
    assert report["observations"] == 3

