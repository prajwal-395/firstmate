"""D9 regression: the export file must carry the timeline's timestamp.

PR 460 timestamps the Resolve timeline (`<base>_<strftime>_<dur>s`) so
drafts accumulate instead of colliding. But step 6.01's `_export_timeline`
recomputed the export filename from the manifest's base project name, so
every run overwrote `exports/<base>.mp4` in place while the timelines
piled up beside it.

The export name must be the timeline name the build just created - the
expression is identity, not a second timestamp computation that could
drift by a second from the timeline's own.
"""

import datetime
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# step.py does `from resolve_build_timeline import build_timeline` - a
# sibling import served by tests/conftest.py, which owns every non-root
# sys.path entry so collection order cannot change what it binds to.

# Full package path, never bare `import step`: several test modules insert
# different step directories at sys.path[0] (and collection order decides
# which one a bare name binds to), so the bare name resolves to the wrong
# step.py under full-directory collection.
from library.steps.step_6_01_render import step as render_step


def _timestamped_timeline_name(base_name, duration_seconds):
    """The same expression resolve_build_timeline uses for the timeline."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    duration_str = f"_{int(duration_seconds)}s" if duration_seconds else ""
    return f"{base_name}_{timestamp}{duration_str}"


def _capture_export_name(monkeypatch, tmp_path):
    """Run _export_timeline with the render and mastering stubbed.

    Resolve renders `<name>_pre_master` into scratch and mastering writes
    the delivery file into exports; `captured["delivered"]` is the path
    the mastered export is written to.
    """
    from library.tools import master_loudness

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"output_path": str(tmp_path / "raw.mp4"),
                             "size_bytes": 1, "job_id": "j",
                             "job_status": "Complete", "format": "mp4",
                             "codec": "H264"})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    def fake_master(report, output_path, **kwargs):
        captured["delivered"] = output_path
        return dict(report, output_path=output_path)

    monkeypatch.setattr(render_step.subprocess, "run", fake_run)
    monkeypatch.setattr(master_loudness, "master_render_report", fake_master)
    return captured


def test_export_filename_matches_timestamped_timeline(monkeypatch, tmp_path):
    """The --name passed to the render must equal the timeline just built."""
    base_name = "Pipeline_Edit_Replanned"
    timeline_name = _timestamped_timeline_name(base_name, 58)
    assert timeline_name != base_name  # the timestamp scheme actually fired

    manifest = {"project": {"name": base_name}}
    inputs = {"project_folder": str(tmp_path)}

    captured = _capture_export_name(monkeypatch, tmp_path)
    render_step._export_timeline(timeline_name, inputs, manifest)

    cmd = captured["cmd"]
    name_flag = cmd[cmd.index("--name") + 1]
    # Identity with the built timeline's name - not the manifest base name.
    assert name_flag == f"{timeline_name}_pre_master"
    assert os.path.basename(captured["delivered"]) == f"{timeline_name}.mp4"


def test_export_falls_back_to_manifest_when_no_timeline(monkeypatch, tmp_path):
    """Without a built timeline name there is nothing timestamped to match."""
    from library.tools.brand_registry import DEFAULT_TIMELINE_NAME

    manifest = {"project": {"name": "Some_Base"}}
    inputs = {"project_folder": str(tmp_path)}

    captured = _capture_export_name(monkeypatch, tmp_path)
    render_step._export_timeline("", inputs, manifest)

    cmd = captured["cmd"]
    name_flag = cmd[cmd.index("--name") + 1]
    assert name_flag == f"{manifest['project']['name']}_pre_master"
    assert os.path.basename(captured["delivered"]) == "Some_Base.mp4"

    captured2 = _capture_export_name(monkeypatch, tmp_path)
    render_step._export_timeline("", inputs, {})
    cmd2 = captured2["cmd"]
    assert cmd2[cmd2.index("--name") + 1] == \
        f"{DEFAULT_TIMELINE_NAME}_pre_master"
    assert os.path.basename(captured2["delivered"]) == \
        f"{DEFAULT_TIMELINE_NAME}.mp4"
