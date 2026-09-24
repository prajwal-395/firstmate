"""Step 3.02 must not carry three views of one vision analysis again.

Measured on 001's frozen snapshot (`001-degradation-20260828`) with the
step-replay bench, at 75d3e84, over a context of 88,475 B:

    semantic_analysis_documents   35,813 B   40.5%
    view:picture                  10,250 B   11.6%
    broll_candidates_toon          7,613 B    8.6%
    ---------------------------------------------
    three views of one analysis   53,676 B   60.7%

All three are readings of the SAME per-clip vision documents, and the
share had grown rather than shrunk: #295 stopped copying the captain's
brief, so the denominator fell faster than the duplication.  This is the
step whose cutaway choices the captain complained about.

After the change the raw structure travels as a REFERENCE
(`library/tools/footage_reference.py`), through the mechanism #295 built
for the brief and #299 applied to the SFX catalogue:

    view:picture                  10,250 B   17.9%
    broll_candidates_toon          7,613 B   13.3%
    footage_analysis_reference     4,507 B    7.9%
    ---------------------------------------------
                                  22,370 B   39.1%   of 57,169 B

With #340's frame strips also in the context: 94,994 B -> 63,688 B, and
the three readings 56.5% -> 35.1%.  The fixture here draws no strips (its
clip paths do not exist), so the ratio below is measured on the analysis
alone, which is what it is about.

## What this file guards, and why in that shape

A share measured against a synthetic fixture would only ever be as
honest as the fixture, so the load-bearing assertion here is a RATIO
between two things that scale together: what the prompt spends on
readings of the analysis, against what the analysis itself would cost
carried inline.  On 001 that ratio was **1.499** before and **0.625**
after, and a re-added `semantic_analysis_documents.*.objects` alone -
62.8% of the structure's cells - puts it back over 1.

The structural half is the thing that actually regrew: a positive
`context_fields` path back into the raw documents.

Nothing here reaches a real project (AGENTS.md 8): the fixture is built
under `tmp_path`, shaped like 001 - seventeen clips, one scene segment
and one camera segment each, and objects at 001's own density.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BROLL_STEP = REPO / "library" / "steps" / "step_3_02_select_broll"

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    project_step_context,
)
from library.tools.brief_reference import (  # noqa: E402
    REFERENCED_INPUTS,
    reference_path,
)
from library.tools.context_projector import project_fields  # noqa: E402
from library.tools.footage_reference import DOCUMENT_NAME  # noqa: E402
from library.tools.toon_serializer import json_to_toon  # noqa: E402

# What the prompt may spend on READINGS of the vision analysis, as a
# multiple of what that analysis costs carried inline. 001 measured
# 1.499 before the change and 0.625 after; the ceiling leaves room for
# the map to grow with the catalogue and none for a fourth view.
READINGS_BUDGET = 0.80

# 001's own shape. Seventeen clips; one scene segment and one camera
# segment on all but one of them; 159 objects over the seventeen.
CLIPS = 17
OBJECTS_PER_CLIP = 9
ACTION_WINDOWS_PER_CLIP = 5


def _document(n: int) -> dict:
    """One v3 vision document, at 001's measured density."""
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
            "notable_features": ["Parked cars", "Concrete sidewalk",
                                 "Metal railing"],
        }],
        "camera": [{
            "start": 0, "end": 45, "mode": "handheld", "framing": "wide",
            "stability": "stable", "movement": "stationary",
        }],
        # As step 1.03 stores them: the v3 `actions[]` windows with
        # `vision_schema_adapter`'s `blocks` view already applied.
        "actions": [{
            "window": [w * 9.0, (w + 1) * 9.0],
            "actions": [{
                "start": w * 9.0, "end": (w + 1) * 9.0,
                "action": f"Clip {n} window {w}: the speaker walks past a "
                          f"row of parked cars and gestures at the building.",
                "speech_cue": None, "body_language": None,
            }],
        } for w in range(ACTION_WINDOWS_PER_CLIP)],
        "blocks": [{
            "timestamp_range": f"0:{w * 9:02d}-0:{(w + 1) * 9:02d}",
            "start": w * 9.0, "end": (w + 1) * 9.0,
            "label": f"Outdoor urban area {n}",
            "visual": f"Clip {n} window {w}: the speaker walks past a row "
                      f"of parked cars and gestures at the building.",
            "body_language": "Relaxed posture, open gestures throughout.",
            "speech_cue": None,
        } for w in range(ACTION_WINDOWS_PER_CLIP)],
        "objects": [{
            "label": f"object {o} in clip {n}, described at length",
            "appearances": [[0.0, 44.5]],
            "role": "background", "category": "structure",
            "readable_text": None,
        } for o in range(OBJECTS_PER_CLIP)],
        "assessment": {
            "content_type": "scenery" if n % 3 else "person_talking_to_camera",
            "clip_type": "b_roll",
            "keywords": ["outdoor", "scenery", "stationary", "wide"],
            "interest_score": 0.5,
            "camera_stability": "unknown",
            "usable_ranges": [[0, 45.0]],
            "usable_ranges_method": "unmeasured",
            "primary_subject_visible": [],
        },
    }


DOCUMENTS = [_document(n) for n in range(CLIPS)]
CATALOG = [{"clip_id": f"clip_{n:03d}", "filename": f"IMG_{1800 + n}.MOV",
            "path": f"/footage/IMG_{1800 + n}.MOV",
            "duration_seconds": 45.0, "width": 1920, "height": 1080,
            "rotation": 0, "frame_rate": 30.0}
           for n in range(CLIPS)]
A_ROLL = [{"segment_id": "block_1", "spine_block_position": 1,
           "video_segments": [{"clip_id": "clip_003"}]}]
SPINE = {"structure": [
    {"position": p, "block_type": "speech", "clip_id": "clip_003",
     "content": f"Spoken line {p} of the edit, as the spine records it.",
     "timeline_start": p * 5.0, "timeline_end": (p + 1) * 5.0,
     "visual_note": "the speaker, mid-sentence"}
    for p in range(11)]}


def _routed_inputs(project: Path) -> dict:
    """What the DAG routes 3.02, before the pre-bridge and the projection."""
    return {
        "project_folder": str(project),
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": DOCUMENTS,
        "temporal_event_indices": [{"clip_id": c["clip_id"],
                                    "scene_boundaries": [0.0, 22.5]}
                                   for c in CATALOG],
        "timed_spine": SPINE,
        "creative_direction": {"target_mood": "reflective",
                               "energy_arc": "building"},
    }


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def _bridge(project: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(BROLL_STEP / "bridge.py")],
        input=json.dumps(_routed_inputs(project)),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def _manifest() -> dict:
    with open(BROLL_STEP / "manifest.json", encoding="utf-8") as fh:
        return json.load(fh)


def _sections(project: Path) -> dict:
    """The prompt's context, split by the top-level key each part came from.

    The runner's OWN assembly: the real pre-bridge, the real projection
    and the real serializer, so this measures the prompt rather than a
    model of it.
    """
    pre = _bridge(project)
    inputs = dict(_routed_inputs(project))
    inputs.update(pre)
    projected = project_step_context(inputs, _manifest(), set(pre))
    context = json_to_toon(projected)

    out, current, buf = {}, None, []
    for line in context.split("\n"):
        if line and not line[0].isspace() and (":" in line or "[" in line):
            key = line.split(":")[0].split("[")[0].strip()
            if key and key.replace("_", "").isalnum():
                if current is not None:
                    out[current] = "\n".join(buf)
                current, buf = key, [line]
                continue
        buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf)
    return out


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


# ── The structural half: no path back into the raw documents ──────────

def test_the_prompt_declares_no_path_into_the_raw_vision_documents():
    """The 40.5% section, and the thing that would regrow it.

    A `-` drop path is not a reading, so only POSITIVE paths count.
    """
    declared = [p for p in _manifest()["context_fields"]
                if not p.startswith("-")]
    offenders = [p for p in declared
                 if p.split(".")[0] in ("semantic_analysis_documents",
                                        "semantic_analysis")]
    assert not offenders, (
        f"step 3.02 reads the raw vision documents in its prompt again "
        f"({offenders}). It already ships two renderings of them - "
        f"`broll_candidates_toon` and `view:picture` - and the structure "
        f"itself is at the path in `footage_analysis_reference`.")






# ── The measured half: what the prompt spends on the analysis ─────────

# Every section of 3.02's prompt that is a reading of the ONE vision
# analysis. The raw keys are in here so that re-declaring one is counted
# against the budget rather than slipping past a list of the three that
# happen to be there today.
# The paths 3.02 used to declare into the raw documents. The denominator
# of the ratio below is what THEY cost, because that is what the prompt
# really carried - not what the whole stored document weighs.
WITHDRAWN_PATHS = [
    "semantic_analysis_documents.*.clip_id",
    "semantic_analysis_documents.*.duration_s",
    "semantic_analysis_documents.*.assessment.interest_score",
    "semantic_analysis_documents.*.assessment.keywords",
    "semantic_analysis_documents.*.assessment.clip_type",
    "semantic_analysis_documents.*.scene",
    "semantic_analysis_documents.*.camera",
    "semantic_analysis_documents.*.objects",
    "semantic_analysis_documents.*.assessment.content_type",
    "semantic_analysis_documents.*.assessment.camera_stability",
    "semantic_analysis_documents.*.assessment.usable_ranges",
    "semantic_analysis_documents.*.assessment.usable_ranges_method",
    "semantic_analysis_documents.*.assessment.primary_subject_visible",
]

# `broll_window_frames` (#340) is deliberately NOT here. A frame strip is
# a picture of the footage, not a reading of the analysis document, and
# it is the thing this collapse was making room for - counting it against
# the budget would charge the frames for the duplication they exposed.
READINGS_OF_THE_ANALYSIS = (
    "broll_candidates_toon", "picture", "footage_analysis_reference",
    "semantic_analysis_documents", "semantic_analysis",
)






# ── Nothing the choice needs became unreachable ───────────────────────

# What the model is told to decide on, and where each of them now is.
# `cutaway_window.choose_window` matches the answer's `preferred_moment`
# against `blocks[].visual`, so the observed-action rows are the one
# reading the ANSWER is resolved against and they stay in the prompt.
IN_THE_PROMPT = {
    "content_type": "broll_candidates_toon",
    "usable_range": "broll_candidates_toon",
    "framing": "broll_candidates_toon",
    "stability": "broll_candidates_toon",
    "camera_move": "broll_candidates_toon",
    "subjects": "broll_candidates_toon",
    "description": "broll_candidates_toon",
    "used_as_aroll": "broll_candidates_toon",
    "duration_s": "broll_candidates_toon",
}

# What only the raw structure ever carried. Each must come back from
# FOLLOWING the reference, and must not be in the prompt.
ONLY_AT_THE_PATH = (
    # `camera[]`'s per-segment time bounds - the table dedupes them away.
    "mode: handheld",
    # every object, not the four the `subjects` column keeps.
    f"object {OBJECTS_PER_CLIP - 1} in clip 0",
    # the assessment fields the table has no column for.
    "usable_ranges_method",
    "interest_score",
    # `scene[]` as fields rather than as one prose line.
    "notable_features",
)




def test_the_answer_is_still_resolved_against_something_it_can_see(project):
    """`choose_window` matches `preferred_moment` against the action rows."""
    picture = _sections(project)["picture"]
    assert "window 0" in picture and "window 4" in picture
    assert picture.count("clip_") >= CLIPS


def test_following_the_reference_returns_what_the_prompt_left_behind(project):
    """The discipline of `tests/test_brief_reference.py`: FOLLOW it.

    The path is parsed out of the same string the model reads, and what
    comes back must not have been in the prompt.
    """
    sections = _sections(project)
    reference = sections["footage_analysis_reference"]
    path = reference_path(reference)
    assert path, reference[:400]
    assert Path(path).name == DOCUMENT_NAME

    document = Path(path).read_text(encoding="utf-8")
    context = "\n".join(sections.values())
    for needle in ONLY_AT_THE_PATH:
        assert needle in document, f"the document lost {needle!r}"
        assert needle not in context, (
            f"{needle!r} is in the prompt AND at the path - that is the "
            f"duplication this change removed")


def test_the_map_names_every_clip_by_the_id_an_answer_must_use(project):
    """A section titled with an id the rest of the context does not speak
    is a section the model cannot look up."""
    reference = _sections(project)["footage_analysis_reference"]
    for entry in CATALOG:
        assert f"## {entry['clip_id']}  [" in reference, entry["clip_id"]




def test_the_bridge_refuses_when_there_is_nowhere_to_write_it():
    """No project folder means no path, and no quiet copy instead."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    payload = {k: v for k, v in _routed_inputs(Path("/nowhere")).items()
               if k != "project_folder"}
    proc = subprocess.run(
        [sys.executable, str(BROLL_STEP / "bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env,
    )
    assert proc.returncode == 1
    assert "project_folder" in json.loads(proc.stdout)["error"]
