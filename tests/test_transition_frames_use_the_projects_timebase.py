"""`duration_frames` is computed from the timebase the catalog MEASURED.

The defect
----------
`step_4_02_plan_transitions/post_bridge.py` read
`data.get("frame_rate", 30.0)`.  **Nothing in this pipeline has ever
produced a key called `frame_rate` at the top level of a step's inputs**
- the catalog measures the timebase and calls it `project_fps` - and no
edge carried `project_fps` to this step either.  So the default won on
every run.

Step 4.04 was given the same edge and the same read in #124, whose
manifest line says it in as many words: *"Every consumer used to read its
own 30.0 default because no edge carried it."*  Two of the three
consumers were left behind.

What it cost, measured
----------------------
`duration_map` scales with `frame_rate / 30`, so at the wrong 30.0 a
transition gets 30-fps frame counts played at the project's real rate.
The captain's `lucie/geo-podcast` is 23.976 fps:

    frame_rate=30.0    quick=6  medium=10 slow=15 frames
                       -> played 250 / 417 / 626 ms
    frame_rate=23.976  quick=4  medium=7  slow=11 frames
                       -> played 167 / 292 / 459 ms

Every drawn transition on that project held roughly 50% longer than the
word the model wrote asked for.

Both directions, per AGENTS.md 10.4: the wrong timebase must CHANGE the
answer (or this test proves nothing), and the right one must produce the
frames the timebase implies.
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
    frames = _frames({"transition_creative": PLAN, "timed_spine": SPINE,
                      "music_selection": MUSIC, "project_fps": 23.976})
    assert frames == int(10 * (23.976 / 30)), frames


def test_a_different_timebase_gives_a_different_answer():
    """The direction that makes the test above mean something.

    If `duration_frames` came out the same at both rates, reading the
    timebase would be decorative and this file would be a gate that
    cannot fail.
    """
    at_ntsc = _frames({"transition_creative": PLAN, "timed_spine": SPINE,
                       "music_selection": MUSIC, "project_fps": 23.976})
    at_thirty = _frames({"transition_creative": PLAN, "timed_spine": SPINE,
                         "music_selection": MUSIC, "project_fps": 30.0})
    assert at_ntsc != at_thirty, (at_ntsc, at_thirty)
    assert at_thirty == 10


def test_the_key_nothing_produces_no_longer_decides_alone():
    """`project_fps` wins over `frame_rate`, which nothing writes.

    `frame_rate` is kept as a second reading rather than deleted only
    because step 4.04 reads it the same way; what matters is that the
    key the catalog really produces is consulted FIRST.
    """
    frames = _frames({"transition_creative": PLAN, "timed_spine": SPINE,
                      "music_selection": MUSIC, "project_fps": 23.976,
                      "frame_rate": 30.0})
    assert frames == int(10 * (23.976 / 30)), frames


def test_the_catalog_edge_carries_the_timebase():
    """A read is not enough: the value has to be routed to the step."""
    dag = json.loads((REPO / "library" / "processes" / "edit_video" /
                      "dag.json").read_text(encoding="utf-8"))
    mappings = [e.get("data_mapping", {}) for e in dag["edges"]
                if e["from"] == "catalog" and e["to"] == "plan_transitions"]
    assert mappings and mappings[0].get("project_fps") == "project_fps"

    manifest = json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))
    declared = {i["name"] for i in manifest["interface"]["inputs"]}
    assert "project_fps" in declared


def test_plan_vfx_threads_a_timebase_nothing_uses():
    """The finding this fix deliberately did NOT apply to 4.03.

    `plan_vfx` reads the same non-existent `frame_rate` key and passes
    the value to `resolve_vfx` and `resolve_generator_overlays` - both of
    which declare the parameter and never use it. Routing `project_fps`
    there would add a declaration nothing reads, which is the defect this
    lane is auditing for. The finding is that the parameter is dead, and
    this test pins it so the claim cannot go stale unnoticed.
    """
    import ast

    source = (REPO / "library" / "steps" / "step_4_03_plan_vfx" /
              "post_bridge.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for name in ("resolve_vfx", "resolve_generator_overlays"):
        func = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        assert any(a.arg == "frame_rate" for a in func.args.args), name
        used = {n.id for n in ast.walk(func) if isinstance(n, ast.Name)}
        assert "frame_rate" not in used, (
            f"{name} now uses frame_rate - route project_fps to plan_vfx "
            f"and read it, the way step 4.02 does")
