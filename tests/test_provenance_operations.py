"""An OPERATION that writes a file is recorded as truthfully as a step is.

Provenance attributes a file by diffing a directory snapshot, so it
generalises to operations. The properties pinned are the REFUSALS, each
paired with the same call succeeding once the missing thing is supplied.
History: docs/evidence/provenance.md
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
    assert rec.step_id == "plan_subtitles"
    assert rec.is_attributed


def test_a_step_is_still_recorded_as_a_step(project):
    ledger = ProvenanceLedger(project)
    records = _observe(ledger, project, Area.PROSODY, "p.json",
                       step_id="prosody_analysis", run_id="r1")

    assert records[0].producer_kind == PRODUCER_STEP
    assert records[0].operation_id is None
    assert records[0].producer == "prosody_analysis"


# ── The refusals, each paired with the passing case ─────────────────

def test_an_unverifiable_producer_is_refused_by_name(project):
    """Each row is a producer the ledger cannot CHECK - no owning step,
    an undeclared operation, an unknown node, or a ledger that declares
    nothing at all (once off by default: `if self.operation_ids and ...`
    checked nothing). The passing mirror is
    `test_an_operation_is_recorded_as_an_operation`."""
    declared = dict(step_ids=["plan_subtitles"],
                    operation_ids=["subtitles.plan"])
    cases = [
        (declared, "", "subtitles.plan", ["subtitles.plan", "owning step"]),
        (declared, "plan_subtitles", "subtitles.invented",
         ["subtitles.invented"]),
        (declared, "not_a_node", "subtitles.plan", ["not_a_node"]),
        ({}, "plan_subtitles", "totally.made.up",
         ["no declared operations", "operations.names()"]),
    ]
    for n, (declarations, step_id, operation_id, words) in enumerate(cases):
        ledger = ProvenanceLedger(project, **declarations)
        with pytest.raises(ProvenanceError) as exc:
            _observe(ledger, project, Area.SUBTITLE_SEGMENTS, f"r{n}.mov",
                     step_id=step_id, run_id="r1",
                     operation_id=operation_id)
        for word in words:
            assert word in str(exc.value), (operation_id, str(exc.value))


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
            operation=self.name, legacy_node=self.legacy_node,
            scope=scope, status=operations.COMPLETED, payload={})

    monkeypatch.setattr(operations.Operation, "execute", writes_a_segment)
    assert operations.main(["subtitles.render", "--project",
                            str(project)]) == 0

    rows = ProvenanceLedger(project)._read("artifacts.jsonl")
    assert [(r["operation_id"], r["step_id"], r["producer_kind"])
            for r in rows] == [
        ("subtitles.render", "render_subtitles", PRODUCER_OPERATION)]
