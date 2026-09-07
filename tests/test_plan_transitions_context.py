"""What a transition planner reads about the footage, and where it comes from.

`plan_transitions` was handed the RAW vision document - fifteen columns
including `file_path`, `fps`, `resolution`, `vision_schema_version` and
`analysis_metadata` - which was 113 KB of its 162 KB context on project 001
and made it the largest prompt in the pipeline by a factor of two.

Its own handoff names ONE source for what is either side of a cut: the
`cuts_toon` table its pre-bridge builds.  That table was empty.  Two
key-name failures of the kind AGENTS.md 10.1 calls the dominant bug class:

  * the documents are keyed by file STEM (`IMG_1816`) and the spine speaks
    catalog ids (`clip_011`), so every footage cell read "none";
  * spine blocks carry `block_type`, not `type`, so every cut was
    classified "unknown-to-unknown".

So the raw document is no longer sent, and the table the handoff points at
carries what the vision pass actually measured.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.context_projector import project_fields

STEP = REPO / "library" / "steps" / "step_4_02_plan_transitions"
DAG = json.loads(
    (REPO / "library" / "processes" / "edit_video" / "dag.json").read_text(
        encoding="utf-8"))

WITHDRAWN_COLUMNS = ["file_path", "fps", "resolution",
                     "vision_schema_version", "analysis_metadata"]

DOC = {
    "clip_id": "IMG_1816_v3",
    "file_path": "/nowhere/raw/IMG_1816.MOV",
    "fps": 30.0,
    "resolution": "1920x1080",
    "vision_schema_version": "v3",
    "analysis_metadata": {"model": "gemma", "analysis_time_s": 41.2},
    "duration_s": 70.1,
    "camera": [{"start": 0, "end": 70, "framing": "close-up",
                "movement": "stationary", "stability": "stable",
                "mode": "handheld"}],
    "scene": [{"start": 0, "end": 70, "type": "outdoor",
               "setting": "car park"}],
    "assessment": {"keywords": ["selfie", "outdoor"],
                   "content_type": "person_talking_to_camera",
                   "camera_stability": "stable"},
}
CATALOG = [{"clip_id": "clip_011", "path": "/nowhere/raw/IMG_1816.MOV"},
           {"clip_id": "clip_008", "path": "/nowhere/raw/IMG_1813.MOV"}]
SPINE = {"structure": [
    {"position": "hook", "block_type": "hook", "clip_id": "clip_011",
     "timeline_start": 0.0},
    {"position": 1, "block_type": "transition_slot", "timeline_start": 2.4},
    {"position": 2, "block_type": "speech", "clip_id": "clip_011",
     "timeline_start": 5.4},
]}
BROLL = [{"spine_block_position": 1, "clip_id": "clip_008",
          "source_file": "/nowhere/raw/IMG_1813.MOV"}]
BROLL_DOC = {
    "clip_id": "IMG_1813_v3",
    "file_path": "/nowhere/raw/IMG_1813.MOV",
    "camera": [{"start": 0, "end": 9, "framing": "wide",
                "movement": "walking", "stability": "shaky"}],
    "assessment": {"keywords": ["sidewalk"], "content_type": "scenery"},
}


def manifest():
    return json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))


def run_bridge(payload):
    proc = subprocess.run([sys.executable, str(STEP / "bridge.py")],
                          input=json.dumps(payload), capture_output=True,
                          encoding="utf-8", cwd=str(REPO),
                          env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ── The raw document no longer reaches the prompt ─────────────────────

def test_the_vision_document_is_not_in_the_projection():
    fields = manifest()["context_fields"]
    assert "semantic_analysis" not in fields
    assert not any(f.startswith("semantic_analysis.") for f in fields)


def test_none_of_the_withdrawn_columns_survive_projection():
    projected = project_fields(
        {"semantic_analysis": [DOC], "timed_spine": SPINE},
        manifest()["context_fields"])
    serialised = json.dumps(projected)
    for column in WITHDRAWN_COLUMNS:
        assert column not in serialised, (
            f"{column!r} still reaches the transition planner's prompt")


def test_the_step_is_still_routed_the_document_it_summarises():
    """Nothing is LOST: projection narrows the prompt, never the inputs.

    `present_llm_step` projects; `bridge.py` is handed the unprojected
    inputs and is the reader that needs the whole document.
    """
    names = [i["name"] for i in manifest()["interface"]["inputs"]]
    assert "semantic_analysis" in names
    assert "clip_catalog" in names, (
        "the catalog is what joins a stem-keyed document to a clip_id")
    edges = [(e["from"], e["to"], e.get("data_mapping", {}))
             for e in DAG["edges"] if e["to"] == "plan_transitions"]
    catalog_edges = [mapping for src, _dst, mapping in edges
                     if src == "catalog"]
    assert len(catalog_edges) == 1
    # Containment, not equality: the edge also carries `project_fps`,
    # which `duration_frames` is computed from. What this test is about
    # is that the catalog still reaches the step at all.
    assert catalog_edges[0].get("clip_catalog") == "clip_catalog"
    assert any(src == "semantic_analysis" for src, _, _ in edges)


# ── The table the handoff points at carries the footage ───────────────

def test_the_cut_table_joins_the_documents_to_the_spine():
    out = run_bridge({"timed_spine": SPINE, "semantic_analysis": [DOC],
                      "clip_catalog": CATALOG, "b_roll_assignments": []})
    table = out["cuts_toon"]
    # The hook block names clip_011 and the document is keyed IMG_1816_v3.
    outgoing = table.split("\n")[1].split("\t")[4]
    assert outgoing != "none", table
    assert "Framing: close-up" in table
    assert "Camera: stationary" in table
    assert "selfie" in table


def test_the_cut_is_classified_by_the_spine_s_own_key():
    out = run_bridge({"timed_spine": SPINE, "semantic_analysis": [DOC],
                      "clip_catalog": CATALOG, "b_roll_assignments": []})
    assert "unknown-to-unknown" not in out["cuts_toon"]
    assert "hook-to-transition_slot" in out["cuts_toon"]
    assert "transition_slot-to-speech" in out["cuts_toon"]


def test_a_cutaway_block_is_attributed_through_its_assignment():
    """A cutaway block carries no clip_id - step 3.02 fills the slot - and
    a cut INTO one is the cut most likely to want a transition."""
    out = run_bridge({"timed_spine": SPINE,
                      "semantic_analysis": [DOC, BROLL_DOC],
                      "clip_catalog": CATALOG, "b_roll_assignments": BROLL})
    assert "Framing: wide" in out["cuts_toon"]
    assert "Camera: walking" in out["cuts_toon"]


def test_no_mood_is_invented():
    """v3 measures no mood and no energy (AGENTS.md 10.1). The table used to
    print `Mood: ` for every cut, which is a header over nothing."""
    out = run_bridge({"timed_spine": SPINE, "semantic_analysis": [DOC],
                      "clip_catalog": CATALOG, "b_roll_assignments": []})
    assert "Mood:" not in out["cuts_toon"]


def test_a_clip_with_no_document_says_so_rather_than_guessing():
    out = run_bridge({"timed_spine": SPINE, "semantic_analysis": [],
                      "clip_catalog": CATALOG, "b_roll_assignments": []})
    rows = [r for r in out["cuts_toon"].split("\n") if r.strip()][1:]
    assert all("none" in r for r in rows), out["cuts_toon"]
