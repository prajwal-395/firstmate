"""The rough-cut review's narrative answer goes somewhere, and it arrives.

Step 3.03 is asked for `cut_decisions` on every run of this pipeline.
Before this change, `grep -rn cut_decisions library/ tests/` returned the field's
own declaration in `step_3_03_review_rough_cut/manifest.json` and nothing
else - no DAG edge carried it, no manifest declared it as an input, and no
line of Python indexed it.  The model answered and the answer was dropped.

This file holds the wiring together in both directions:

  * the DAG carries it from the producer to the reader,
  * the reader DECLARES it, so `gather_step_inputs` will hand it over,
  * the reader's own bridge - run as a real subprocess over JSON stdin,
    the way the runner runs it - puts the verdict in the table the
    handoff tells the model to read, and
  * the verdict never invents itself: a cut the review did not judge
    reads `unjudged`, and the mild end of the scale is not borrowed for
    it (AGENTS.md 10.5).

The bridge is DRIVEN rather than modelled, for the reason
`tests/test_no_creative_floors.py` drives the real bridges: a table this
step builds in code is exactly where the last two defects of this class
hid.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.tools import cut_verdicts as cv

DAG = json.loads(
    (REPO / "library" / "processes" / "edit_video" / "dag.json").read_text())
STEPS = REPO / "library" / "steps"

PRODUCER = "review_rough_cut"
READER = "plan_transitions"
READER_DIR = STEPS / "step_4_02_plan_transitions"


def manifest(step_dir):
    return json.loads((STEPS / step_dir / "manifest.json").read_text())


# ── The wiring ───────────────────────────────────────────────────────

def test_the_producer_still_declares_it():
    outputs = manifest("step_3_03_review_rough_cut")["interface"]["outputs"]
    row = next(o for o in outputs if o["name"] == "cut_decisions")
    # A bare `{"name": ..., "type": "list"}` is what the schema injector
    # turned into `"cut_decisions": []` with no description, on every run
    # for the life of the step. The shape has to be stated or the answer
    # cannot be keyed to a cut.
    assert "cut_point_position" in row["description"]
    for word in cv.VERDICTS:
        assert word in row["description"]


def test_the_dag_carries_it_to_the_reader():
    edges = [e for e in DAG["edges"]
             if e["from"] == PRODUCER and e["to"] == READER]
    assert edges, f"no DAG edge {PRODUCER} -> {READER}"
    assert any(e["data_mapping"].get("cut_decisions") == "cut_decisions"
               for e in edges)


def test_the_reader_declares_it_and_declares_it_optional():
    row = next(i for i in manifest("step_4_02_plan_transitions")
               ["interface"]["inputs"] if i["name"] == "cut_decisions")
    # Optional, so a review that judged no cut does not refuse the run:
    # `gather_step_inputs` raises on a missing mapped key only when the
    # consumer declared it required.
    assert row["required"] is False


def test_it_is_not_also_sent_raw_beside_the_table():
    """Never a summary and the structure it was rendered from (10.1)."""
    cf = manifest("step_4_02_plan_transitions")["context_fields"]
    assert "cut_decisions" not in cf


def test_4_01_is_not_the_reader_because_it_has_no_prompt():
    """The DAG-backed reason the obvious candidate was rejected.

    `plan_subtitles` runs first of the four nodes downstream of the
    review, but it is `deterministic`: a step.py and no handoff.md, so
    `detect_implementation` gives it no prompt at all and it groups
    captions by measured pixels. A narrative verdict routed there would
    have no reader for a second time.
    """
    subtitles = STEPS / "step_4_01_plan_subtitles"
    assert (subtitles / "step.py").exists()
    assert not (subtitles / "handoff.md").exists()
    assert not (subtitles / "bridge.py").exists()


# ── The value arriving ───────────────────────────────────────────────

SPINE = {
    "structure": [
        {"position": "hook", "block_type": "hook", "timeline_start": 0.0,
         "timeline_end": 2.4, "clip_id": "clip_011"},
        {"position": 1, "block_type": "transition_slot",
         "timeline_start": 2.4, "timeline_end": 5.4},
        {"position": 2, "block_type": "speech", "timeline_start": 5.4,
         "timeline_end": 8.38, "clip_id": "clip_011"},
        {"position": 3, "block_type": "speech", "timeline_start": 8.38,
         "timeline_end": 18.37, "clip_id": "clip_011"},
    ]
}

CUT_DECISIONS = [
    {"cut_point_position": 2, "verdict": "jarring",
     "why": "the location\nchanges with no motivation"},
    {"cut_point_position": 3, "verdict": "smooth", "why": "same thought"},
]


def run_bridge(payload):
    # The runner launches a bridge with the repo importable, the same way
    # tests/test_no_creative_floors.py drives the real ones.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(READER_DIR / "bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def cuts_rows(cuts_toon):
    lines = [l for l in cuts_toon.strip().splitlines() if l]
    header = lines[0].split("{", 1)[1].rstrip("}").split(",")
    return header, [dict(zip(header, l.split("\t"))) for l in lines[1:]]


def test_the_verdict_is_in_the_table_the_handoff_names():
    out = run_bridge({"timed_spine": SPINE, "cut_decisions": CUT_DECISIONS})
    header, rows = cuts_rows(out["cuts_toon"])

    assert "narrative_verdict" in header and "verdict_note" in header
    by_cut = {r["cut_point_position"]: r for r in rows}

    assert by_cut["2"]["narrative_verdict"] == "jarring"
    # The newline is collapsed, not escaped: a cell is a row.
    assert by_cut["2"]["verdict_note"] == (
        "the location changes with no motivation")
    assert by_cut["3"]["narrative_verdict"] == "smooth"

    # The frozen handoff cannot name the columns, so the definition
    # travels as data beside the table.
    assert set(cv.CUT_VERDICT_LEGEND) <= set(out["cuts_legend"])


def test_an_unjudged_cut_does_not_borrow_the_mild_end_of_the_scale():
    out = run_bridge({"timed_spine": SPINE,
                      "cut_decisions": [CUT_DECISIONS[0]]})
    _, rows = cuts_rows(out["cuts_toon"])
    by_cut = {r["cut_point_position"]: r for r in rows}

    assert by_cut["2"]["narrative_verdict"] == "jarring"
    assert by_cut["3"]["narrative_verdict"] == cv.UNJUDGED
    assert by_cut["3"]["narrative_verdict"] not in cv.VERDICTS
    # And how much is missing is SAID, not left to be inferred from a
    # column of `unjudged`.
    assert "2 of 3" in out["cuts_unjudged"]


def test_no_review_at_all_leaves_every_cut_unjudged_and_says_so():
    out = run_bridge({"timed_spine": SPINE})
    _, rows = cuts_rows(out["cuts_toon"])
    assert {r["narrative_verdict"] for r in rows} == {cv.UNJUDGED}
    assert out["cuts_unjudged"]


def test_a_word_outside_the_vocabulary_is_carried_not_mapped():
    """A near match is a chooser (AGENTS.md 10.5)."""
    out = run_bridge({"timed_spine": SPINE, "cut_decisions": [
        {"cut_point_position": 2, "verdict": "rough", "why": "eh"}]})
    _, rows = cuts_rows(out["cuts_toon"])
    cell = {r["cut_point_position"]: r for r in rows}["2"]["narrative_verdict"]
    assert "rough" in cell and cv.UNRECOGNISED in cell
    assert "jarring" not in cell


def test_the_verdict_decides_nothing_by_itself():
    """The column is DATA. Nothing here plans a transition from it.

    `can_carry_drawn_transition` is unmoved by the verdict, and the row
    order is the spine's. Whatever selects or re-ranks becomes the
    chooser, which is the thing 10.5 keeps out of the bridge.
    """
    without = run_bridge({"timed_spine": SPINE})
    with_verdicts = run_bridge(
        {"timed_spine": SPINE, "cut_decisions": CUT_DECISIONS})
    _, a = cuts_rows(without["cuts_toon"])
    _, b = cuts_rows(with_verdicts["cuts_toon"])

    assert [r["cut_point_position"] for r in a] == [
        r["cut_point_position"] for r in b]
    assert [r["can_carry_drawn_transition"] for r in a] == [
        r["can_carry_drawn_transition"] for r in b]


# ── The reading ──────────────────────────────────────────────────────

def test_verdict_of_is_none_for_an_unjudged_cut():
    placed = cv.verdicts_by_cut(CUT_DECISIONS)
    assert cv.verdict_of(placed, 2) == "jarring"
    assert cv.verdict_of(placed, 99) is None


@pytest.mark.parametrize("key", ["cut_point_position",
                                 "spine_block_position", "position"])
def test_the_cut_is_found_under_the_names_the_pipeline_uses(key):
    placed = cv.verdicts_by_cut([{key: 2, "verdict": "broken"}])
    assert cv.verdict_of(placed, 2) == "broken"


def test_a_row_naming_no_cut_is_not_placed_anywhere():
    assert cv.verdicts_by_cut([{"verdict": "broken"}]) == {}


def test_the_withdrawn_readings_are_recorded():
    """A withdrawal without its reason gets re-added."""
    assert len(cv.WITHDRAWN_READINGS) == 2
    assert all(v.strip() for v in cv.WITHDRAWN_READINGS.values())


# ── The shape the run of record really used ──────────────────────────

# Verbatim rows from project 001's own `step_outputs.review_rough_cut.
# cut_decisions`, answered against the bare `"cut_decisions": []` schema.
# The model chose its own shape and used the handoff's own four words
# under `rating`, keyed on `to_block` - the block the cut leads INTO,
# which is what `cuts_toon` keys on. So the answer the pipeline HAS
# arrives at the reader without a re-run.
RUN_OF_RECORD = [
    {"decision": "approve", "scope": "rough_cut",
     "rationale": "All six mechanical checks passed"},
    {"decision": "flow", "scope": "part_2_check_7", "from_block": 10,
     "to_block": 12, "rating": "acceptable",
     "rationale": "The largest jump in the video: clip_013 to clip_015."},
    {"decision": "flow", "scope": "part_2_check_7", "from_block": 12,
     "to_block": 13, "rating": "smooth",
     "rationale": "The second explains why the first is not false modesty."},
    {"decision": "flag", "scope": "block_13_head_alignment",
     "severity": "warning",
     "rationale": "Block 13's aligned source range starts at 29.002s while "
                  "its first strongly matched word ('almost') is at 30.58s. "
                  "The clean fix belongs in step 2.2."},
]


def test_the_answer_the_pipeline_already_has_reaches_the_table():
    placed = cv.verdicts_by_cut(RUN_OF_RECORD)
    assert cv.verdict_of(placed, 12) == "acceptable"
    assert cv.verdict_of(placed, 13) == "smooth"
    assert "clip_013 to clip_015" in cv.note_column(placed, 12)


def test_a_row_that_is_about_the_whole_cut_claims_no_cut_point():
    """`approve` and `flag` name no cut, so they must not land on one."""
    placed = cv.verdicts_by_cut(RUN_OF_RECORD)
    assert set(placed) == {"12", "13"}


# ── The other half, and its reader ───────────────────────────────────

def test_every_row_reaches_one_reader_or_the_other():
    """Nothing in `cut_decisions` falls on the floor."""
    placed = cv.verdicts_by_cut(RUN_OF_RECORD)
    unplaced = cv.unplaced_findings(RUN_OF_RECORD)
    assert len(placed) + len(unplaced) == len(RUN_OF_RECORD)


def test_the_finding_that_named_the_mis_anchor_is_printed():
    lines = cv.summary_lines(RUN_OF_RECORD)
    text = "\n".join(lines)
    assert "block_13_head_alignment" in text
    assert "warning" in text
    assert "step 2.2" in text
    # The per-cut verdicts are NOT repeated here: they reach 4.02's own
    # table, and printing both is a summary beside its own source (10.1).
    assert "acceptable" not in text


def test_no_review_prints_nothing():
    assert cv.summary_lines(None) == []
    assert cv.summary_lines([]) == []


def test_nothing_is_filtered_by_decision_or_severity():
    """Whatever picks which findings matter becomes the reviewer."""
    rows = [{"decision": "note", "rationale": "a"},
            {"decision": "flag", "severity": "warning", "rationale": "b"},
            {"rationale": "c"}]
    assert len(cv.unplaced_findings(rows)) == 3


def test_the_runner_prints_them_after_the_status_is_decided():
    """Reading is not gating - the same ordering the QA block is pinned to.

    Pinned off the runner's own source so an edit that moves the block
    above the status fails here rather than quietly starting to block
    runs.
    """
    src = (REPO / "library" / "processes" / "edit_video"
           / "run_pipeline.py").read_text()
    marker = "from library.tools import cut_verdicts as _cv"
    assert marker in src
    assert src.index('summary = {\n        "status": status,') > src.index(
        marker), "the review findings must be read after status is decided"
