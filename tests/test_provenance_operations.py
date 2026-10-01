"""An OPERATION that writes a file is recorded as truthfully as a step is.

Increment 2 of the LLM-native refactor
--------------------------------------
`docs/architecture/llm_native_design.md` establishes that the runner
wraps eighteen services around every step body and that every one of
them is keyed by a DAG node id, so anything invoked outside the DAG gets
none of them.  Provenance is the ONE of the eighteen that already
generalises, because it attributes a file by DIFFING A DIRECTORY
SNAPSHOT rather than by anything the producer declares - it does not
care what ran.  That is why it is the cheapest first thread, and this
is it.

What is actually being tested here
----------------------------------
Not that the happy path works - that was nearly free.  The properties
worth pinning are the REFUSALS, because the whole design rests on the
claim that the new layers are subject to AGENTS.md 10.4 rather than
exempt from it, and a contract never observed refusing is the
vacuous-gate defect this repository has already been bitten by three
times.

So every refusal below is paired with the same call SUCCEEDING once the
thing it complained about is supplied.  A gate that always says no is no
more evidence than one that always says yes.
"""


import pytest

from library.tools.project_layout import Area, ProjectLayout
from library.tools.provenance import (
    PRODUCER_OPERATION,
    PRODUCER_STEP,
    ProvenanceError,
    ProvenanceLedger,
)


@pytest.fixture
def project(tmp_path):
    """A project under tmp_path. Never a real one (AGENTS.md 8)."""
    ProjectLayout(tmp_path).ensure()
    (tmp_path / "project.yaml").write_text("slug: t\n", encoding="utf-8")
    return tmp_path


def _write(project, area, name, text="x"):
    path = ProjectLayout(project).write_path(area, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _observe(ledger, project, area, name, **kwargs):
    """Watch one file appear, the way the runner watches a step."""
    before = ledger.snapshot()
    _write(project, area, name)
    return ledger.observe(after=ledger.snapshot(), before=before, **kwargs)


# ── The generalisation ──────────────────────────────────────────────

def test_an_operation_is_recorded_as_an_operation(project):
    ledger = ProvenanceLedger(project, step_ids=["plan_subtitles"],
                              operation_ids=["subtitles.plan"])
    records = _observe(ledger, project, Area.SUBTITLE_SEGMENTS, "seg.mov",
                       step_id="plan_subtitles", run_id="r1",
                       operation_id="subtitles.plan")

    assert len(records) == 1
    rec = records[0]
    assert rec.producer_kind == PRODUCER_OPERATION
    assert rec.operation_id == "subtitles.plan"
    assert rec.is_attributed


def test_a_step_is_still_recorded_as_a_step(project):
    ledger = ProvenanceLedger(project)
    records = _observe(ledger, project, Area.PROSODY, "p.json",
                       step_id="prosody_analysis", run_id="r1")

    assert records[0].producer_kind == PRODUCER_STEP
    assert records[0].operation_id is None
    assert records[0].producer == "prosody_analysis"


# ── The refusals, each paired with the passing case ─────────────────

def test_an_operation_with_no_owning_node_is_refused(project):
    """A producer that resolves to no node loses its place in four
    step-keyed records while appearing to have been recorded."""
    ledger = ProvenanceLedger(project, step_ids=["plan_subtitles"],
                              operation_ids=["subtitles.plan"])

    with pytest.raises(ProvenanceError) as exc:
        _observe(ledger, project, Area.SUBTITLE_SEGMENTS, "a.mov",
                 step_id="", run_id="r1", operation_id="subtitles.plan")

    assert "subtitles.plan" in str(exc.value)
    assert "owning step" in str(exc.value)


def test_an_undeclared_operation_is_refused(project):
    """`UNKNOWN` already exists to admit a gap; inventing a producer is
    the one thing worse than admitting it."""
    ledger = ProvenanceLedger(project, step_ids=["plan_subtitles"],
                              operation_ids=["subtitles.plan"])

    with pytest.raises(ProvenanceError) as exc:
        _observe(ledger, project, Area.SUBTITLE_SEGMENTS, "c.mov",
                 step_id="plan_subtitles", run_id="r1",
                 operation_id="subtitles.invented")

    assert "subtitles.invented" in str(exc.value)


def test_an_operation_naming_an_unknown_owning_node_is_refused(project):
    ledger = ProvenanceLedger(project, step_ids=["plan_subtitles"],
                              operation_ids=["subtitles.plan"])

    with pytest.raises(ProvenanceError) as exc:
        _observe(ledger, project, Area.SUBTITLE_SEGMENTS, "e.mov",
                 step_id="not_a_node", run_id="r1",
                 operation_id="subtitles.plan")

    assert "not_a_node" in str(exc.value)


def test_a_ledger_that_declares_nothing_REFUSES_an_operation(project):
    """The correction. This test asserted the opposite and was wrong.

    It originally read `test_a_ledger_that_declares_nothing_does_not
    _refuse`, on the reasoning that a ledger with no declarations should
    stay usable rather than refuse everything. That reasoning produced a
    guard that was OFF BY DEFAULT - `if self.operation_ids and ...`
    checks nothing when the list is absent - which is the
    gate-that-cannot-fail defect this whole change exists to remove,
    sitting inside the change itself. Found by cross-PR measurement, not
    by this suite, which is the honest thing to record about it.

    A producer that cannot be CHECKED is not recorded as fact. That is
    the same reasoning `UNKNOWN` already rests on.
    """
    ledger = ProvenanceLedger(project)          # no declarations at all

    with pytest.raises(ProvenanceError) as exc:
        _observe(ledger, project, Area.SUBTITLE_SEGMENTS, "g.mov",
                 step_id="plan_subtitles", run_id="r1",
                 operation_id="totally.made.up")

    assert "no declared operations" in str(exc.value)
    assert "operations.names()" in str(exc.value)   # names the way out


def test_and_a_fully_declared_ledger_still_records(project):
    """The mirror. Both refusals above must not make the layer unusable."""
    ledger = ProvenanceLedger(project, step_ids=["plan_subtitles"],
                              operation_ids=["subtitles.plan"])
    records = _observe(ledger, project, Area.SUBTITLE_SEGMENTS, "i.mov",
                       step_id="plan_subtitles", run_id="r1",
                       operation_id="subtitles.plan")

    assert records[0].operation_id == "subtitles.plan"
    assert records[0].step_id == "plan_subtitles"




# ── The receipt a capability run leaves ─────────────────────────────

def test_running_a_capability_records_its_id_and_derives_the_node(
        project, monkeypatch):
    """The defect: no caller ever passed `operation_id`, so a capability
    run from the CLI or the reels process wrote files provenance never
    attributed. The id is the caller's only input; the node is derived."""
    from library.tools import operations

    def writes_a_segment(self, project_folder, scope=None, **overrides):
        _write(project, Area.SUBTITLE_SEGMENTS, "seg.mov")
        return operations.OperationResult(
            operation=self.name, owning_node=self.owning_node,
            scope=scope, status=operations.COMPLETED, payload={})

    monkeypatch.setattr(operations.Operation, "execute", writes_a_segment)
    assert operations.main(["subtitles.render", "--project",
                            str(project)]) == 0

    rows = ProvenanceLedger(project)._read("artifacts.jsonl")
    assert [(r["operation_id"], r["step_id"], r["producer_kind"])
            for r in rows] == [
        ("subtitles.render", "render_subtitles", PRODUCER_OPERATION)]
