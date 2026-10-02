"""Both speakers centred on body pose, bounded by the face - captain, 2026-10-01.

See `docs/evidence/body_pose_framing.md` for the incident and invariant.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.subject_framing import (
    BODY_JOINT_MIN_LANDMARKS,
    SUBJECT_HEADROOM,
    _aim_center_x,
    _body_centroid_x,
    _nearest_body_joints,
    _probe_body_centroids,
)


class TestAimCenterX:
    """The aim favours the body, never past the face's own headroom."""

    def test_body_within_headroom_wins_outright(self):
        # Akshita's measured case (report.md 2.3): body sits ~0.022 of
        # source width right of her face, well inside a 0.3-wide face's
        # SUBJECT_HEADROOM (0.15 * 0.3 = 0.045) budget.
        aim, basis = _aim_center_x(face_cx=0.40, face_width=0.30,
                                   body_cx=0.422)
        assert basis == "body_pose"
        assert aim == 0.422

    def test_body_past_headroom_is_clamped_not_ignored(self):
        face_cx, face_width = 0.40, 0.10
        bound = SUBJECT_HEADROOM * face_width
        aim, basis = _aim_center_x(face_cx=face_cx, face_width=face_width,
                                   body_cx=0.80)
        assert basis == "body_pose"
        assert aim == face_cx + bound

    def test_no_body_measurement_keeps_the_old_face_aim(self):
        aim, basis = _aim_center_x(face_cx=0.52, face_width=0.20,
                                   body_cx=None)
        assert (aim, basis) == (0.52, "face")

class TestBodyCentroid:
    def test_needs_the_minimum_landmark_count(self):
        joints = {
            "left_shoulder_1_joint": [0.40, 0.5, 0.9],
            "right_shoulder_1_joint": [0.60, 0.5, 0.9],
        }
        assert len(joints) < BODY_JOINT_MIN_LANDMARKS + 2
        assert _body_centroid_x(joints) is None  # only 2 of 4 joints

    def test_low_confidence_joints_do_not_count(self):
        # Three of the four joints clear the confidence floor - still
        # enough to average - but the low-confidence forearm must be
        # excluded from the mean, not merely ignored as "missing".
        joints = {
            "left_shoulder_1_joint": [0.40, 0.5, 0.9],
            "right_shoulder_1_joint": [0.60, 0.5, 0.9],
            "left_forearm_joint": [0.0, 0.6, 0.1],  # below the floor
            "right_forearm_joint": [0.80, 0.6, 0.9],
        }
        assert _body_centroid_x(joints) == (0.40 + 0.60 + 0.80) / 3

    def test_mean_of_the_four_upper_body_joints(self):
        joints = {
            "left_shoulder_1_joint": [0.40, 0.5, 0.9],
            "right_shoulder_1_joint": [0.60, 0.5, 0.9],
            "left_forearm_joint": [0.35, 0.6, 0.9],
            "right_forearm_joint": [0.65, 0.6, 0.9],
            "head_joint": [0.50, 0.1, 0.9],  # not one of the four - ignored
        }
        assert _body_centroid_x(joints) == 0.5


class TestNearestBody:
    def test_picks_the_body_whose_neck_is_closest_to_the_speaker(self):
        bodies = [
            {"joints": {"neck_1_joint": [0.80, 0.4, 0.9]}},  # background
            {"joints": {"neck_1_joint": [0.42, 0.4, 0.9]}},  # the speaker
        ]
        joints = _nearest_body_joints(bodies, near_x=0.40)
        assert joints == bodies[1]["joints"]

    def test_a_body_with_no_neck_is_skipped(self):
        bodies = [{"joints": {"left_shoulder_1_joint": [0.5, 0.5, 0.9]}}]
        assert _nearest_body_joints(bodies, near_x=0.5) is None


class TestProbeBodyCentroids:
    def test_vision_unavailable_returns_none_not_a_crash(self, monkeypatch):
        import library.steps.step_1_04_temporal_index.vision_measure as vm

        monkeypatch.setattr(vm, "ensure_helper", lambda: (None, "no swiftc"))
        assert _probe_body_centroids(["/x/f0.png"], [0.5]) is None

    def test_per_frame_alignment_with_the_nearest_face(self, monkeypatch):
        import library.steps.step_1_04_temporal_index.vision_measure as vm

        monkeypatch.setattr(vm, "ensure_helper", lambda: ("/bin/true", None))

        def _fake_measure_frames(paths, helper_path):
            return [
                {"bodies": [{"joints": {
                    "neck_1_joint": [0.40, 0.3, 0.9],
                    "left_shoulder_1_joint": [0.36, 0.45, 0.9],
                    "right_shoulder_1_joint": [0.46, 0.45, 0.9],
                    "left_forearm_joint": [0.34, 0.6, 0.9],
                    "right_forearm_joint": [0.48, 0.6, 0.9],
                }}]},
                {"bodies": []},  # no body this frame -> None, not a crash
            ]

        monkeypatch.setattr(vm, "measure_frames", _fake_measure_frames)
        out = _probe_body_centroids(["/x/f0.png", "/x/f1.png"], [0.40, 0.50])
        assert out[0] == (0.36 + 0.46 + 0.34 + 0.48) / 4
        assert out[1] is None
