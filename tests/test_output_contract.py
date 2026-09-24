"""The mirror of `test_input_declarations_are_true.py`: who READS an output.

Nothing in this repository asked that question before
`library/tools/output_contract.py`, and it is the question behind the
most common real defect here - data computed and then not reaching where
it was needed.

Both directions are proven, because AGENTS.md 10.4 says a gate that
cannot fail and a gate that fails correct output are the same defect
seen from two sides:

* `test_an_output_no_route_carries_is_a_disagreement` - the mechanism
  FAILS on an output nothing reads.
* `test_a_stale_exemption_is_a_disagreement` - and it fails from the
  OTHER side, on an entry recorded as unread whose output has since
  found a reader, so the tables cannot become a way to go quiet.
* `test_a_bridge_table_is_credited_to_its_own_prompt` and
  `test_an_edge_routed_output_is_credited` - it does NOT fail correct
  output. Eight outputs reach only their own step's prompt, and a
  survey blind to that route would report all eight.
* `test_the_repository_agrees_with_its_own_tables` - the live ratchet.

The second pass, 2026-09-07
---------------------------
The `code` route used to match a key ANYWHERE in a module as a string
literal, and that over-credited six outputs to lines that are not reads
- a dict-literal key being WRITTEN, a `key != "total_failed"` carve-out
inside a generic checker, and a step id in four dispatch tables.  An
over-credit keeps an unread output OUT of both tables, which is a gate
declining to fail, so the route now requires a READ POSITION:

* `test_a_read_position_is_credited` - every shape a merged input dict
  is really opened with survives, so the tighter rule does not fail
  correct output;
* `test_a_name_that_is_not_a_read_is_not_credited` - one case per false
  credit that was found;
* `test_validate_step_output_is_not_credited_as_a_reader` -
  `run_pipeline.validate_step_output` touches every declared output and
  consumes none of them.  Crediting a declaration checker would make
  every output consumed by construction.
* `test_a_known_collision_leaves_the_output_unread` and
  `test_a_collision_with_nothing_left_to_subtract_is_a_disagreement` -
  `KNOWN_NAME_COLLISIONS` now SUBTRACTS a credit rather than recording a
  known-false one and leaving it standing, and it is stale from its own
  side too.

The calibration, and why it is not a test
-----------------------------------------
`uncalled_functions` was checked against a number a previous lane
published rather than against itself.  Issue #564 named
`enforce_min_duration` "defined, documented and never called"; the scan
run over the tree at `33c421c^` - the commit before the one that wired
it - reports `enforce_min_duration
library/steps/step_4_01_plan_subtitles/step.py:292`, and the same scan
over today's tree does not.  That is a check against a real historical
defect and it cannot be written as a test without checking out a second
tree, so it is recorded here and the two stable directions are tested
instead.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.tools.output_contract import (  # noqa: E402
    KNOWN_NAME_COLLISIONS,
    REPORTED_NOT_CONSUMED,
    ROUTE_CODE,
    ROUTE_EDGE,
    ROUTE_OWN_PROMPT,
    UNREAD_FINDINGS,
    OutputRow,
    _read_literals,
    disagreements,
    survey,
    uncalled_functions,
)


@pytest.fixture(scope="module")
def rows():
    return survey()


# ── The gate CAN fail ────────────────────────────────────────────────

def test_an_output_no_route_carries_is_a_disagreement():
    """A declared output nobody reads, in neither table, is refused.

    Driven through the real `disagreements`, not a re-implementation of
    it: the row is synthetic, the judgement is the shipped one.
    """
    orphan = OutputRow(process="edit_video", node="a_step_that_does_not_exist",
                       step_dir="step_9_99_nothing", name="a_value_nobody_reads")
    assert orphan.unread
    problems = disagreements([orphan])
    refusal = [p for p in problems if "a_value_nobody_reads" in p]
    assert len(refusal) == 1, problems
    assert "NO ROUTE" in refusal[0]




def test_an_entry_for_an_output_nobody_declares_is_a_disagreement():
    """The third staleness: the output has gone and the entry has not."""
    problems = disagreements([])
    assert problems, "an empty survey must not silently satisfy the tables"
    assert all("no step declares that output any more" in p
               for p in problems), problems


# ── The gate does NOT fail correct output ────────────────────────────



@pytest.mark.parametrize("node,name", [
    ("plan_transitions", "cuts_toon"),
])
def test_a_bridge_table_is_credited_to_its_own_prompt(rows, node, name):
    """A pre-bridge's one table is consumed by the step's own prompt.

    `run_pipeline.project_step_context` restores bridge-supplied keys BY
    NAME after projection, precisely because a pre-bridge's table cannot
    be projected away. A survey that only looked at edges would call all
    eight unread, which is a gate failing correct output.
    """
    by_key = {row.key: row for row in rows}
    row = by_key[(node, name)]
    assert row.routes[0] == ROUTE_OWN_PROMPT, row.routes
    assert "bridge.py" in row.own_prompt




# ── The live ratchet ─────────────────────────────────────────────────

def test_the_repository_agrees_with_its_own_tables(rows):
    """Every unread output is recorded, and every record is still true."""
    problems = disagreements(rows)
    assert problems == [], "\n".join(problems)








# ── The field-level half ─────────────────────────────────────────────





# ── The finding this survey found, and its fix ───────────────────────

def test_the_reel_judgement_reaches_a_reader(rows, tmp_path):
    """Step 3.05's answer is read from where the step actually writes it.

    Both readers of a judgement opened `review/reel_judgement.json`, and
    NOTHING in this repository wrote that file - so coherence and value
    read UNJUDGED on every verification. The step writes its output the
    way every step does.
    """
    from library.tools.reel_quality_bar import read_judgement

    project = tmp_path / "project"
    step_dir = project / "pipeline_output" / "steps" / "3_05_judge_reels"
    step_dir.mkdir(parents=True)

    absent, source = read_judgement(str(project))
    assert absent is None and source == ""

    import json
    (step_dir / "output.json").write_text(json.dumps({
        "reels_to_read": [],
        "reel_judgement": {"format": "reel_judgement/1", "readings": [],
                           "refused": [], "ordering": [], "not_read": []},
    }), encoding="utf-8")

    found, source = read_judgement(str(project))
    assert found is not None and found["format"] == "reel_judgement/1"
    assert source.endswith("output.json")

    row = {r.key: r for r in rows}[("judge_reels", "reel_judgement")]
    assert not row.unread, "the fix must make the survey credit it"


def test_a_hand_placed_judgement_still_wins(tmp_path):
    """A captain who put a file there meant it to be read."""
    import json

    from library.tools.reel_quality_bar import read_judgement

    project = tmp_path / "project"
    step_dir = project / "pipeline_output" / "steps" / "3_05_judge_reels"
    step_dir.mkdir(parents=True)
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)

    (step_dir / "output.json").write_text(json.dumps(
        {"reel_judgement": {"format": "from_the_step", "readings": []}}),
        encoding="utf-8")
    (review / "reel_judgement.json").write_text(json.dumps(
        {"format": "from_the_captain", "readings": []}), encoding="utf-8")

    found, source = read_judgement(str(project))
    assert found["format"] == "from_the_captain"
    assert source.endswith("reel_judgement.json")


def test_every_process_dag_is_contract_checked():
    """The edge checker reads every process, not `edit_video` by name."""
    import inspect

    from library.tools import validate_dag_contracts

    source = inspect.getsource(validate_dag_contracts.run_validation)
    assert "every_dag" in source
    assert "edit_video" not in source
    assert validate_dag_contracts.run_validation() == 0


# ── The `code` route is a READ, not a name ───────────────────────────

def _literals(tmp_path, source):
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    return _read_literals(path)


@pytest.mark.parametrize("source", [
    'value = data.get("wanted")',
    'value = data["wanted"]',
])
def test_a_read_position_is_credited(tmp_path, source):
    """Every shape a merged input dict is actually opened with.

    Dropping any of these would make the survey report an output that
    really is read, which AGENTS.md 10.4 calls the same defect as a gate
    that cannot fail.
    """
    assert "wanted" in _literals(tmp_path, source), source


@pytest.mark.parametrize("source,what", [
    ('result = {"wanted": total}', "a dict-literal key is a WRITE"),
    ('if key != "wanted":\n    pass', "a comparison is a carve-out"),
])
def test_a_name_that_is_not_a_read_is_not_credited(tmp_path, source, what):
    """The six false credits, one shape each.

    `scan.total_files` was credited to two modules WRITING their own
    `total_files`; `temporal_index.total_failed` to `key !=
    "total_failed"` inside a generic checker; `ocr_extraction` to
    `StepDir("ocr_extraction", ...)`.
    """
    assert "wanted" not in _literals(tmp_path, source), what




# ── A collision SUBTRACTS the credit ─────────────────────────────────







# ── What the second pass FIXED ───────────────────────────────────────



def _build_reels_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "manage_project_under_test", REPO / "manage_project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module




@pytest.mark.parametrize("payload", [
    {},
])
def test_the_reader_says_nothing_about_a_payload_that_is_not_a_verdict(
        capsys, payload):
    """It reads one key. The build node's own payload is not that key."""
    _build_reels_module()._report_reel_verification(payload)
    assert capsys.readouterr().err == ""






def test_zero_reused_clips_is_not_reported_as_empty():
    """A count whose correct value is 0 must not read as a defect.

    `total_reused` is how much of the temporal index came off cache, so
    zero is the right answer on every first run - and it was 0 on
    project 001's run of record. `validate_step_output` treats any
    `total_*` of 0 as semantically empty, so the manifest declares
    `may_be_empty` for this one key. The check still fires for
    `total_indexed`, where 0 IS a defect.
    """
    import json

    from library.processes.edit_video.run_pipeline import validate_step_output

    manifest = json.loads(
        (REPO / "library" / "steps" / "step_1_04_temporal_index" /
         "manifest.json").read_text(encoding="utf-8"))
    good = {"index_dir": "/i",
            "source": "fresh", "temporal_event_indices": [{"clip_id": "c"}],
            "total_failed": 0, "total_indexed": 1, "total_reused": 0}
    assert validate_step_output("temporal_index", good, manifest) == []

    nothing_indexed = dict(good, total_indexed=0)
    issues = validate_step_output("temporal_index", nothing_indexed, manifest)
    assert any("total_indexed" in issue for issue in issues), issues
