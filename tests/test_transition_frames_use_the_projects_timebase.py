"""`duration_frames` is computed from the timebase the catalog MEASURED
(`project_fps`), never a 30.0 default nothing produces. History and the
measured cost: `docs/evidence/transition_frames_timebase.md`.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STEP = REPO / "library" / "steps" / "step_4_02_plan_transitions"

def _block(position, clip, start, end):
    """A spine block that satisfies the contract in AGENTS.md 6."""
    return {"position": position, "block_type": "speech",
            "timeline_start": start, "timeline_end": end,
            "clip_id": clip, "source_start": 0.0,
            "source_end": end - start, "alignment_method": "test",
            "word_timestamps": [], "content": {"clip_id": clip}}


SPINE = {"structure": [_block(1, "clip_001", 0.0, 4.0),
                       _block(2, "clip_002", 4.0, 8.0)]}
PLAN = [{"cut_point_position": 2, "type": "defocus",
         "duration_feel": "medium", "rationale": "a held breath"}]
MUSIC = {"title": "nothing", "audio_path": "", "duration_seconds": 60.0}


def _run(payload):
    proc = subprocess.run(
        [sys.executable, str(STEP / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True,
        encoding="utf-8", cwd=str(REPO),
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
        check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _frames(payload):
    spec = _run(payload)["transition_spec"]
    drawn = [t for t in spec if t.get("duration_frames")]
    return drawn[0]["duration_frames"] if drawn else None


def test_the_projects_own_timebase_decides_the_frames():
    """Both directions (AGENTS.md 10.4): the right timebase gives the frames
    it implies, a different one CHANGES the answer, and `project_fps` wins
    over `frame_rate`, which nothing writes."""
    base = {"transition_creative": PLAN, "timed_spine": SPINE,
            "music_selection": MUSIC}
    at_ntsc = _frames(dict(base, project_fps=23.976))
    assert at_ntsc == int(10 * (23.976 / 30)), at_ntsc
    assert _frames(dict(base, project_fps=30.0)) == 10
    assert _frames(dict(base, project_fps=23.976, frame_rate=30.0)) == at_ntsc
