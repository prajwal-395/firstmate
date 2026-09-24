"""select_broll keeps the scene prose inline and the scene structure at a path.

Issue #679: "select_broll still reads the scene prose and the scene
structure". Written against the pre-#339 prompt, where BOTH travelled
inline in one context:

    semantic_analysis_documents   35,813 B   40.5%   (the structure:
                                     `scene[]`/`camera[]`/`objects[]` as
                                     fields, plus every assessment key)
    broll_candidates_toon           7,613 B    8.6%   (the prose: the
                                     `description` column is
                                     `vision_schema_adapter.scene_prose`)

#339 (11498a6) moved the structure to `footage_analysis.md`, reached by
the map in `footage_analysis_reference`, and withdrew every positive
`semantic_analysis_documents.*` path from the manifest. The prose column
stayed: it is the place axis `test_picture_view.py` pins to the
pre-bridge table, on a step whose handoff tells the model to match
content against it.

This test pins that end state on the ASSEMBLED prompt (real bridge +
real projection + real serializer, the shape
`tests/test_broll_context_share.py` uses): the prose is inline, no
scene structure is, and the structure is one followed path away. Run it
at 11498a6^ and it fails - the bridge emits no reference, and the raw
documents arrive inline carrying every marker below.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BROLL_STEP = REPO / "library" / "steps" / "step_3_02_select_broll"

sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    project_step_context,
)
from library.tools.brief_reference import reference_path  # noqa: E402
try:  # Absent before #339 moved the structure to a reference document.
    from library.tools.footage_reference import DOCUMENT_NAME  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - pre-fix trees only
    DOCUMENT_NAME = "footage_analysis.md"
from library.tools.toon_serializer import json_to_toon  # noqa: E402


def _document(n: int) -> dict:
    stem = f"IMG_{1800 + n}"
    return {
        "clip_id": f"{stem}_v3",
        "file_path": f"/footage/{stem}.MOV",
        "duration_s": 45.0,
        "vision_schema_version": "3.0",
        "scene": [{
            "start": 0.0, "end": 18.9,
            "location": f"Outdoor urban area {n} with a parking lot",
            "type": "outdoor", "lighting": "Overcast daylight",
            "notable_features": ["Parked cars", "Concrete sidewalk"],
        }],
        "camera": [{
            "start": 0, "end": 45, "mode": "handheld", "framing": "wide",
            "stability": "stable", "movement": "stationary",
        }],
        "actions": [{
            "window": [w * 9.0, (w + 1) * 9.0],
            "actions": [{
                "start": w * 9.0, "end": (w + 1) * 9.0,
                "action": f"Clip {n} window {w}: the speaker walks past parked cars.",
                "speech_cue": None, "body_language": None,
            }],
        } for w in range(5)],
        "blocks": [{
            "timestamp_range": f"0:{w * 9:02d}-0:{(w + 1) * 9:02d}",
            "start": w * 9.0, "end": (w + 1) * 9.0,
            "label": f"Outdoor urban area {n}",
            "visual": f"Clip {n} window {w}: the speaker walks past parked cars.",
            "body_language": "", "speech_cue": None,
        } for w in range(5)],
        "objects": [{
            "label": f"object {o} in clip {n}",
            "appearances": [[0.0, 44.5]],
            "role": "background", "category": "structure",
            "readable_text": None,
        } for o in range(3)],
        "assessment": {
            "content_type": "scenery",
            "clip_type": "b_roll",
            "keywords": ["outdoor"],
            "interest_score": 0.5,
            "camera_stability": "unknown",
            "usable_ranges": [[0, 45.0]],
            "usable_ranges_method": "unmeasured",
            "primary_subject_visible": [],
        },
    }


CLIPS = 3
DOCUMENTS = [_document(n) for n in range(CLIPS)]
CATALOG = [{"clip_id": f"clip_{n:03d}", "filename": f"IMG_{1800 + n}.MOV",
            "path": f"/footage/IMG_{1800 + n}.MOV",
            "duration_seconds": 45.0, "width": 1920, "height": 1080,
            "rotation": 0, "frame_rate": 30.0}
           for n in range(CLIPS)]


def _routed_inputs(project: Path) -> dict:
    return {
        "project_folder": str(project),
        "clip_catalog": CATALOG,
        "a_roll_assignments": [{"segment_id": "block_1",
                                "spine_block_position": 1,
                                "video_segments": [{"clip_id": "clip_000"}]}],
        "semantic_analysis_documents": DOCUMENTS,
        "temporal_event_indices": [{"clip_id": c["clip_id"],
                                    "scene_boundaries": [0.0, 22.5]}
                                   for c in CATALOG],
        "timed_spine": {"structure": [
            {"position": p, "block_type": "speech", "clip_id": "clip_000",
             "content": f"Spoken line {p} of the edit.",
             "timeline_start": p * 5.0, "timeline_end": (p + 1) * 5.0,
             "visual_note": "the speaker, mid-sentence"}
            for p in range(3)]},
        "creative_direction": {"target_mood": "reflective",
                               "energy_arc": "building"},
    }


def _assembled_prompt(project: Path) -> tuple:
    """(projected dict, prompt text) via the real bridge and projection."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(BROLL_STEP / "bridge.py")],
        input=json.dumps(_routed_inputs(project)),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), env=env, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pre = json.loads(proc.stdout)
    with open(BROLL_STEP / "manifest.json", encoding="utf-8") as fh:
        manifest = json.load(fh)
    inputs = dict(_routed_inputs(project))
    inputs.update(pre)
    projected = project_step_context(inputs, manifest, set(pre))
    return projected, json_to_toon(projected)


# Keys that only exist in the scene/camera/object STRUCTURE - the field
# names of `scene[]`, `objects[]` and the assessment method. The prose
# rendering (`scene_prose`) carries the VALUES (locations, features) as
# running text, never these keys.
STRUCTURE_MARKERS = (
    "notable_features",
    "seen [",
    "usable_ranges_method",
)


def test_no_scene_structure_travels_inline(tmp_path):
    """The structure half of #679: fields, not values, must not be inline."""
    _, context = _assembled_prompt(tmp_path)
    for marker in STRUCTURE_MARKERS:
        assert marker not in context, (
            f"{marker!r} is in the prompt AND is the structure the "
            f"candidate table was rendered from - the pair #679 reports")


def test_the_structure_is_at_the_reference_path_not_in_the_prompt(tmp_path):
    """#339's half: every byte moved, reached by the map, not inline."""
    projected, context = _assembled_prompt(tmp_path)
    assert "footage_analysis_reference" in projected, (
        "the bridge emits no reference to the vision pass's own analysis")
    path = reference_path(projected["footage_analysis_reference"])
    assert path, projected["footage_analysis_reference"][:400]
    assert Path(path).name == DOCUMENT_NAME
    document = Path(path).read_text(encoding="utf-8")
    assert "Where it is, per scene segment:" in document
    for marker in STRUCTURE_MARKERS:
        assert marker in document, f"the document lost {marker!r}"
        assert marker not in context, (
            f"{marker!r} is in the prompt AND at the path")
