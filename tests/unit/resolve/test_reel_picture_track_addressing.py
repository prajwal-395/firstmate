from library.tools.reel_post_header import picture_runs


def test_picture_runs_use_track_roles_instead_of_fixed_video_indices():
    tracks = [
        {"type": "video", "index": 1, "name": "Subject Grade",
         "clips": [{"record_in": 0, "record_out": 20}]},
        {"type": "video", "index": 2, "name": "Camera A",
         "clips": [{"record_in": 0, "record_out": 10}]},
        {"type": "video", "index": 3, "name": "Subtitles",
         "clips": [{"record_in": 10, "record_out": 20}]},
        {"type": "video", "index": 4, "name": "B-Roll",
         "clips": [{"record_in": 10, "record_out": 20}]},
        {"type": "video", "index": 5, "name": "Post Header",
         "clips": [{"record_in": 0, "record_out": 20}]},
    ]

    assert picture_runs(tracks) == [(0, 20)]
