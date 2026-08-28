"""Step 2.02's pre-bridge builds the transcript the prompt reads.

This is the file that replaces four tests in `tests/test_bridges.py`
which imported a `pre_bridge` function that has never existed.  The
bridge is a SUBPROCESS - `run_pipeline.run_subprocess` hands it JSON on
stdin and reads JSON off stdout - so that is what these drive, the same
way `tests/test_select_broll_bridge.py` drives 3.02's.

What is asserted here is what the step promises and what has gone wrong
before:

* the transcript comes off the per-clip index FILES, not off the routed
  `temporal_index` value, because the manifest projection never carried
  the per-word records (AGENTS.md 10.1, `view:transcript`);
* the columns are `clip_id,start,end,text` in that order - alphabetising
  them put `end` before `start` in two prompts;
* the rows are sorted, because `os.listdir` order is the filesystem's and
  the same project would otherwise present its clips differently on every
  run;
* the output is CONTEXT ONLY.  It used to also emit a `speech_sequence`
  built from every transcript region, which is the raw transcript wearing
  the output's name, and in `--auto` that became the step's answer.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.project_layout import Area, ProjectLayout  # noqa: E402

BRIDGE = REPO_ROOT / "library" / "steps" / "step_2_02_speech_sequence" / "bridge.py"

# Two clips, written to disk in the order that would come out WRONG if
# the bridge trusted `os.listdir`: clip_002 sorts after clip_001, and
# within clip_001 the later region is written first.
CLIP_INDICES = {
    "clip_002": {
        "speech_regions": [
            {"start": 0.5, "end": 2.25, "text": "and that is the whole trick"},
        ],
    },
    "clip_001": {
        "speech_regions": [
            {"start": 12.0, "end": 14.5, "text": "second thing I said"},
            {"start": 1.0, "end": 3.5, "text": "first thing\nI said"},
        ],
    },
}

SEMANTIC_DOCS = [
    {"clip_id": "clip_001", "assessment": {"keywords": ["coffee", "kitchen"]}},
    {"clip_id": "clip_002", "assessment": {"keywords": ["street"]}},
]


def _project(tmp_path: Path, *, record_index_dir: bool = True) -> Path:
    """A project carrying per-clip temporal-index files, and nothing else.

    The area is NAMED, never composed: a test builds a project path no
    more freely than a step does (AGENTS.md 8).
    """
    project = tmp_path / "project"
    project.mkdir()
    layout = ProjectLayout(project)
    index_dir = layout.write_dir(Area.TEMPORAL_INDEX, step="temporal_index")
    for clip_id, index in CLIP_INDICES.items():
        (index_dir / f"{clip_id}.json").write_text(
            json.dumps(index), encoding="utf-8")

    state = {"step_outputs": {"temporal_index": {}}}
    if record_index_dir:
        state["step_outputs"]["temporal_index"]["index_dir"] = str(index_dir)
    layout.pipeline_data_path.write_text(json.dumps(state), encoding="utf-8")
    return project


def run_bridge(payload: dict) -> subprocess.CompletedProcess:
    """Invoke the bridge the way the orchestrator does: JSON on stdin."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO_ROOT), env=env,
    )


def parse_table(toon: str):
    """Split a TOON table into (header_fields, [row dicts])."""
    lines = [line for line in toon.splitlines() if line.strip()]
    header = lines[0]
    fields = header[header.index("{") + 1:header.index("}")].split(",")
    rows = [dict(zip(fields, line.split("\t"))) for line in lines[1:]]
    return fields, rows


def _payload(project: Path) -> dict:
    return {
        "project_folder": str(project),
        # Routed but deliberately hollow: the real transcript is on disk.
        "temporal_index": {"temporal_event_indices": []},
        "semantic_analysis_documents": SEMANTIC_DOCS,
    }


# ── The transcript table ──────────────────────────────────────────────

def test_the_transcript_is_read_off_the_per_clip_index_files(tmp_path):
    proc = run_bridge(_payload(_project(tmp_path)))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _, rows = parse_table(json.loads(proc.stdout)["transcripts_toon"])
    assert len(rows) == 3
    assert {r["clip_id"] for r in rows} == {"clip_001", "clip_002"}


def test_the_columns_are_clip_start_end_text_in_that_order(tmp_path):
    """Alphabetising these put `end` before `start` in two prompts."""
    proc = run_bridge(_payload(_project(tmp_path)))
    fields, _ = parse_table(json.loads(proc.stdout)["transcripts_toon"])
    assert fields == ["clip_id", "start", "end", "text"]


def test_rows_are_sorted_by_clip_then_start(tmp_path):
    """`os.listdir` order is the filesystem's, so it may not be trusted."""
    proc = run_bridge(_payload(_project(tmp_path)))
    _, rows = parse_table(json.loads(proc.stdout)["transcripts_toon"])
    assert [(r["clip_id"], r["start"]) for r in rows] == [
        ("clip_001", "1.00"), ("clip_001", "12.00"), ("clip_002", "0.50"),
    ]


def test_a_newline_in_a_region_does_not_break_the_row(tmp_path):
    """A row IS a line, so the cell may not contain one."""
    proc = run_bridge(_payload(_project(tmp_path)))
    table = json.loads(proc.stdout)["transcripts_toon"]
    _, rows = parse_table(table)
    by_start = {r["start"]: r for r in rows}
    assert by_start["1.00"]["text"] == "first thing I said"
    # One header line plus one line per region, and no more.
    assert len([l for l in table.splitlines() if l.strip()]) == 4


def test_the_index_dir_is_found_without_a_recorded_index_dir(tmp_path):
    """A run predating the recorded `index_dir` still has the files."""
    project = _project(tmp_path, record_index_dir=False)
    proc = run_bridge(_payload(project))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _, rows = parse_table(json.loads(proc.stdout)["transcripts_toon"])
    assert len(rows) == 3


# ── The topics table ──────────────────────────────────────────────────

def test_topics_come_from_the_assessment_keywords(tmp_path):
    proc = run_bridge(_payload(_project(tmp_path)))
    _, rows = parse_table(json.loads(proc.stdout)["topics_toon"])
    assert {r["clip_id"]: r["topics"] for r in rows} == {
        "clip_001": "coffee, kitchen",
        "clip_002": "street",
    }


def test_semantic_documents_keyed_by_clip_are_read_too(tmp_path):
    """State stores the documents as a mapping; a fresh run as a list."""
    payload = _payload(_project(tmp_path))
    payload["semantic_analysis_documents"] = {
        doc["clip_id"]: doc for doc in SEMANTIC_DOCS
    }
    proc = run_bridge(payload)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _, rows = parse_table(json.loads(proc.stdout)["topics_toon"])
    assert {r["clip_id"] for r in rows} == {"clip_001", "clip_002"}


# ── What the bridge must NOT do ───────────────────────────────────────

def test_the_bridge_emits_context_only(tmp_path):
    """It used to emit a `speech_sequence` built from every region.

    That is the raw transcript wearing the output's name, and with
    `--auto` the runner takes the pre-bridge output as the step's answer -
    so the edit became "play the whole transcript".
    """
    proc = run_bridge(_payload(_project(tmp_path)))
    assert set(json.loads(proc.stdout)) == {"transcripts_toon", "topics_toon"}


def test_no_transcript_fails_the_step_rather_than_sending_an_empty_table(
        tmp_path):
    project = tmp_path / "empty"
    project.mkdir()
    ProjectLayout(project).write_dir(Area.TEMPORAL_INDEX, step="temporal_index")
    proc = run_bridge({
        "project_folder": str(project),
        "temporal_index": {},
        "semantic_analysis_documents": SEMANTIC_DOCS,
    })
    assert proc.returncode == 1
    assert "nothing to build a speech sequence from" in (
        json.loads(proc.stdout)["error"])


def test_a_missing_required_input_is_refused(tmp_path):
    """`require_keys` names the step, not a KeyError three frames down."""
    proc = run_bridge({"project_folder": str(_project(tmp_path))})
    assert proc.returncode != 0
    assert "temporal_index" in proc.stdout + proc.stderr
