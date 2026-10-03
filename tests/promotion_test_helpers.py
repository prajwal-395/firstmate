"""Small track plans for tests isolated from A-roll verification."""


def record_ren_owned_inventory(project_folder, project,
                               operation="prior test build"):
    """Seed a completed inventory for timelines known to be Ren-owned."""
    from pathlib import Path

    from library.tools import plan_provenance, reel_replace_guard

    review_dir = Path(project_folder) / "pipeline_output" / "review"
    inventory = reel_replace_guard.timeline_inventory(project)
    operation_id = plan_provenance.begin_timeline_inventory(
        str(review_dir), operation, inventory,
        ren_created_names={entry["name"] for entry in inventory})
    plan_provenance.finish_timeline_inventory(
        str(review_dir), operation_id, inventory)


def install_fake_timeline_snapshots(monkeypatch):
    """Give promotion fakes a minimal, readable preservation snapshot.

    These tests focus on promotion behavior such as marker carry,
    retirement, or bookkeeping. The separate replace-guard tests exercise
    complete item snapshots and editor-change detection.
    """
    from library.tools import reel_replace_guard

    def snapshot(timeline, _project, _project_folder=None):
        try:
            unique_id = timeline.GetUniqueId()
        except Exception:  # noqa: BLE001 - incomplete Resolve fake
            unique_id = None
        return {
            "timeline": {
                "name": timeline.GetName(),
                "unique_id": str(unique_id) if unique_id else None,
                "settings": {},
                "start_frame": 0,
                "end_frame": 0,
            },
            "items": [],
            "markers": [],
        }

    monkeypatch.setattr(reel_replace_guard, "full_timeline_snapshot", snapshot)


def install_measured_draw_gain_probe(monkeypatch, gain=None):
    """Stub the draw-gain probe as MEASURED for offline whole builds.

    No offline double has a renderer to calibrate against - the
    canonical double deliberately models no gallery stills - so the
    real probe always falls back there, and a fallback refuses the
    build rather than placing at an unmeasured gain (2026-10-02: a
    playhead past the end fell back to 2.0 against a measured 4.0 and
    halved every overlay transform). Tests driving whole builds
    offline therefore install this: it keeps the probe's own
    create-and-delete scratch pair, so the deletion-scope guards still
    see exactly what the real probe leaves behind, and answers a
    measured record. The default gain is the fallback's own value, so
    existing placement assertions read unchanged.
    """
    from library.tools import draw_gain_probe as probe_mod
    from library.tools.resolve_transform import FALLBACK_DRAW_GAIN

    measured = FALLBACK_DRAW_GAIN if gain is None else gain

    def _measured(resolve, project, frame_wh, workdir="",
                  project_folder=None):
        pool = project.GetMediaPool()
        scratch = pool.CreateEmptyTimeline(probe_mod.PROBE_TIMELINE_NAME)
        pool.DeleteTimelines([scratch])
        return {
            "gain": measured,
            "source": "measured",
            "disagrees_with_fallback": False,
            "warnings": [],
            "probe": {
                "timeline": probe_mod.PROBE_TIMELINE_NAME,
                "frame_wh": list(frame_wh),
                "fallback_gain": FALLBACK_DRAW_GAIN,
            },
        }

    monkeypatch.setattr(probe_mod, "calibrate", _measured)
    return _measured


def no_a_roll_track_plans(staged_to_final):
    """Return explicit plans with no A-roll rows for promotion unit tests."""
    plan = {"video_tracks": [], "audio_tracks": [], "material": {}}
    return {staging: plan for staging in staged_to_final.values()}
