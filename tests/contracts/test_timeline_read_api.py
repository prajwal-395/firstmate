"""The shared timeline API owns identity and structural read shapes."""

from pathlib import Path

from library.tools import timeline_read, timeline_shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)


def test_shadow_and_live_refresh_adapters_share_identity_and_typed_rows(
        tmp_path, monkeypatch):
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    timeline.AddMarker(10, "Blue", "note", "keep", 1)
    project.SetCurrentTimeline(timeline)
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.sqlite3")

    with assume_sole_writer("timeline read API contract"):
        live = timeline_read.refresh(project, timeline, store=store)

    def forbidden_live_read(*_args, **_kwargs):
        raise AssertionError("recorded reads must not call Resolve")

    monkeypatch.setattr(timeline_shadow, "observe", forbidden_live_read)
    recorded = timeline_read.read("Podcast", "Reel 09", store=store)

    assert live.source == "live_refresh"
    assert recorded.source == "recorded_generation"
    assert live.identity == recorded.identity
    assert live.identity.timeline_id == timeline.GetUniqueId()
    assert live.rows == recorded.rows
    assert live.rows and live.rows[0].media_type == "video"
    assert live.rows[0].clips == recorded.rows[0].clips
    assert live.markers == recorded.markers
    assert live.markers[0].level == "timeline"
    assert live.markers[0].frame == 10
    assert store.head("Podcast", live.identity.timeline_id).generation == 1
    assert store.read_requests_since(0) == {
        "shadow_hits": 1, "live_refreshes": 1, "misses": 0}


def test_live_identity_primitive_has_one_shared_api_owner():
    root = Path(__file__).resolve().parents[2]
    readers = [root / "library/tools/timeline_read.py",
               root / "library/tools/reel_read.py",
               root / "library/tools/timeline_shadow.py"]
    owners = [path.name for path in readers
              if "GetUniqueId(" in path.read_text(encoding="utf-8")]
    assert owners == ["timeline_read.py"]
