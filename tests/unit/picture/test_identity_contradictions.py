from library.tools.identity_contradictions import (
    detect_all,
    detect_face_voice_disagreement,
    detect_same_frame_copresence,
    surface_voice_conflicts,
)


def _span(digest, start, end, clip="clip-a"):
    return {"clip_id": clip, "content_digest": digest, "start": start, "end": end}


def _person(pid, face_spans=(), face_tracks=(), links=(), voice_matches=()):
    return {
        "person_id": pid,
        "face_spans": list(face_spans),
        "face_tracks": list(face_tracks),
        "speech_face_links": list(links),
        "voice_matches": list(voice_matches),
    }


def _track(clip, digest, track_id):
    return {"clip_id": clip, "content_digest": digest, "track_id": track_id}


def test_overlapping_same_digest_spans_detected():
    roster = {
        "persons": [
            _person(
                "person_001",
                face_spans=[_span("digest-a", 0.0, 5.0), _span("digest-a", 4.0, 9.0)],
            )
        ]
    }
    found = detect_same_frame_copresence(roster)
    assert len(found) == 1
    assert found[0]["kind"] == "same_frame_copresence"
    assert found[0]["person_id"] == "person_001"
    assert found[0]["digest"] == "digest-a"
    assert found[0]["span_a"]["start"] == 0.0
    assert found[0]["span_b"]["start"] == 4.0
    assert "basis" in found[0] and found[0]["basis"]


def test_non_overlapping_spans_not_flagged():
    roster = {
        "persons": [
            _person(
                "person_001",
                face_spans=[_span("digest-a", 0.0, 5.0), _span("digest-a", 5.0, 9.0)],
            )
        ]
    }
    assert detect_same_frame_copresence(roster) == []


def test_cross_digest_overlap_not_flagged():
    roster = {
        "persons": [
            _person(
                "person_001",
                face_spans=[_span("digest-a", 0.0, 5.0), _span("digest-b", 2.0, 7.0)],
            )
        ]
    }
    assert detect_same_frame_copresence(roster) == []


def test_face_voice_disagreement_detected_when_voice_owned_elsewhere():
    roster = {
        "persons": [
            _person(
                "person_001",
                face_tracks=[_track("clip-a", "digest-a", "face-1")],
                links=[
                    {
                        "clip_id": "clip-a",
                        "content_digest": "digest-a",
                        "face_track": "face-1",
                        "voice_track": "voice-9",
                    }
                ],
            ),
            _person(
                "person_002",
                voice_matches=[
                    {
                        "content_digest": "digest-a",
                        "voice_track": "voice-9",
                        "matched": [],
                        "basis": "cross-source",
                    }
                ],
            ),
        ]
    }
    found = detect_face_voice_disagreement(roster)
    assert len(found) == 1
    assert found[0]["kind"] == "face_voice_disagreement"
    assert found[0]["person_id"] == "person_001"
    assert found[0]["other_person_id"] == "person_002"


def test_face_voice_links_consistent_when_voice_unclaimed():
    roster = {
        "persons": [
            _person(
                "person_001",
                face_tracks=[_track("clip-a", "digest-a", "face-1")],
                links=[
                    {
                        "clip_id": "clip-a",
                        "content_digest": "digest-a",
                        "face_track": "face-1",
                        "voice_track": "voice-9",
                    }
                ],
            ),
            _person("person_002"),
        ]
    }
    assert detect_face_voice_disagreement(roster) == []


def test_voice_conflicts_surfaced_verbatim():
    roster = {
        "persons": [],
        "voice_match_conflicts": [
            {
                "voice_tracks": [
                    {"content_digest": "digest-a", "voice_track": "v1"},
                    {"content_digest": "digest-b", "voice_track": "v2"},
                ],
                "persons": ["person_001", "person_002"],
            }
        ],
    }
    found = surface_voice_conflicts(roster)
    assert len(found) == 1
    assert found[0]["kind"] == "voice_group_multi_owner"
    assert found[0]["persons"] == ["person_001", "person_002"]
    assert len(found[0]["voice_tracks"]) == 2
    assert "joins nobody" in found[0]["basis"]


def test_detect_all_empty_roster_is_clean():
    for roster in ({}, {"persons": []}, {"persons": [], "voice_match_conflicts": []}):
        report = detect_all(roster)
        assert report["contradictions"] == []
        assert report["checked"] == {
            "copresence": 0,
            "face_voice_disagreement": 0,
            "voice_group_multi_owner": 0,
        }


def test_detect_all_combines_detectors_and_counts():
    roster = {
        "persons": [
            _person(
                "person_001",
                face_spans=[_span("digest-a", 0.0, 5.0), _span("digest-a", 4.0, 9.0)],
                face_tracks=[_track("clip-a", "digest-a", "face-1")],
                links=[
                    {
                        "clip_id": "clip-a",
                        "content_digest": "digest-a",
                        "face_track": "face-1",
                        "voice_track": "voice-9",
                    }
                ],
            ),
            _person(
                "person_002",
                voice_matches=[
                    {
                        "content_digest": "digest-a",
                        "voice_track": "voice-9",
                        "matched": [],
                        "basis": "cross-source",
                    }
                ],
            ),
        ],
        "voice_match_conflicts": [
            {
                "voice_tracks": [{"content_digest": "digest-a", "voice_track": "v1"}],
                "persons": ["person_001", "person_002"],
            }
        ],
    }
    report = detect_all(roster)
    kinds = sorted(c["kind"] for c in report["contradictions"])
    assert kinds == [
        "face_voice_disagreement",
        "same_frame_copresence",
        "voice_group_multi_owner",
    ]
    assert report["checked"] == {
        "copresence": 1,
        "face_voice_disagreement": 1,
        "voice_group_multi_owner": 1,
    }
