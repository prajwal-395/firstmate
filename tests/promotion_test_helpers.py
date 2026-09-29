"""Small track plans for tests isolated from A-roll verification."""


def no_a_roll_track_plans(staged_to_final):
    """Return explicit plans with no A-roll rows for promotion unit tests."""
    plan = {"video_tracks": [], "audio_tracks": [], "material": {}}
    return {staging: plan for staging in staged_to_final.values()}
