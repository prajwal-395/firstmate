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


def no_a_roll_track_plans(staged_to_final):
    """Return explicit plans with no A-roll rows for promotion unit tests."""
    plan = {"video_tracks": [], "audio_tracks": [], "material": {}}
    return {staging: plan for staging in staged_to_final.values()}
