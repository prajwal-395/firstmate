import json
from datetime import datetime

from library.tools import transform_write_log
from library.tools.project_layout import Area, ProjectLayout


class _TimelineItem:
    def __init__(self):
        self.writes = []

    def SetProperty(self, key, value):
        self.writes.append((key, value))
        return True


def test_transform_setter_writes_a_durable_run_record(tmp_path):
    item = _TimelineItem()

    assert transform_write_log.set_property(
        item, "Tilt", -459.0,
        project_folder=tmp_path,
        project="Project One",
        timeline_name="Reel 13",
        timeline_id="timeline-uid-13",
        item_identity={"resolve_unique_id": "item-uid-7"},
        old_value=-918.0,
        run_id="run-20261003-1") is True

    path = (ProjectLayout(tmp_path).write_dir(Area.LOGS)
            / transform_write_log.LOG_FILENAME)
    rows = [json.loads(line) for line in path.read_text(
        encoding="utf-8").splitlines()]
    assert item.writes == [("Tilt", -459.0)]
    assert rows == [{
        "timestamp_utc": rows[0]["timestamp_utc"],
        "project": "Project One",
        "timeline_name": "Reel 13",
        "timeline_id": "timeline-uid-13",
        "item_identity": {"resolve_unique_id": "item-uid-7"},
        "property": "Tilt",
        "old_value": -918.0,
        "new_value": -459.0,
        "caller": {
            "file": "tests/unit/resolve/test_transform_write_log.py",
            "line": rows[0]["caller"]["line"],
            "function": "test_transform_setter_writes_a_durable_run_record",
        },
        "run_id": "run-20261003-1",
        "write_kind": "SetProperty",
    }]
    assert datetime.fromisoformat(
        rows[0]["timestamp_utc"]).utcoffset().total_seconds() == 0


def test_non_transform_setter_does_not_write_a_transform_record(tmp_path):
    item = _TimelineItem()

    assert transform_write_log.set_property(item, "Opacity", 100.0) is True

    path = (ProjectLayout(tmp_path).write_dir(Area.LOGS)
            / transform_write_log.LOG_FILENAME)
    assert item.writes == [("Opacity", 100.0)]
    assert not path.exists()
