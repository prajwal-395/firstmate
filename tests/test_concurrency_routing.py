"""The routing rule, applied without judgement.

The captain, 2026-09-12: *"today that judgement is mine and I have got
it wrong twice today"*. So the table has to answer the dispatch
question on its own, and these are the answers a supervisor reads.
"""

import pytest

from library.tools import concurrency_routing as routing
from library.tools.concurrency_routing import (
    CLASSES,
    DECLARATION,
    FREE,
    OPERATIONS,
    RESOLVE_CURSOR,
    RESOLVE_READ,
    may_run_together,
    route,
)

BUILD = "library.tools.reel_build.rebuild_reels_in_project"
PROMOTE = "library.tools.reel_build.promote_staged_reels"
READ = "library.tools.timeline_ingest.snapshot_timeline"
RECORD = "library.tools.captain_edits.record_edit"
ANALYSE = "library.steps.step_1_03_semantic_analysis"


def test_every_operation_declares_a_known_class_and_a_reason():
    for op in OPERATIONS:
        assert op.exclusion in CLASSES, op.name
        assert op.why.strip(), op.name
        assert op.entry_point.strip(), op.name


def test_entry_points_are_unique():
    seen = [op.entry_point for op in OPERATIONS]
    assert len(seen) == len(set(seen))


def test_a_declaration_row_names_the_file_it_writes():
    for op in OPERATIONS:
        if op.exclusion == DECLARATION:
            assert op.declaration, op.name


# ── The rule itself ─────────────────────────────────────────────────

def test_two_cursor_operations_never_run_together():
    assert not may_run_together(BUILD, PROMOTE)
    assert not may_run_together(PROMOTE, BUILD)


def test_a_read_never_runs_beside_a_cursor_operation():
    """A handle survives a build only by luck: the build deletes timelines."""
    assert not may_run_together(BUILD, READ)
    assert not may_run_together(READ, BUILD)


def test_two_reads_run_together():
    assert may_run_together(READ, "library.tools.reel_read")


def test_free_work_runs_beside_anything():
    for other in (BUILD, PROMOTE, READ, RECORD):
        assert may_run_together(ANALYSE, other), other
        assert may_run_together(other, ANALYSE), other


def test_declaration_writes_are_dispatched_together():
    """They contend per KEY, in `declaration_keys`, not per dispatch.

    Serialising the dispatch would be the coarse answer the key scheme
    exists to avoid - two agents recording two different reels' edits
    have nothing to wait for.
    """
    assert may_run_together(RECORD, "library.tools.reel_signoff")
    assert may_run_together(RECORD, BUILD)


def test_an_unlisted_entry_point_reads_as_free_and_says_why():
    unknown = route("library.tools.nothing_in_particular")
    assert unknown.exclusion == FREE
    assert "Not in OPERATIONS" in unknown.why


# ── The classes mean what the table says they mean ──────────────────

@pytest.mark.parametrize("entry,exclusive,lease", [
    (BUILD, True, True),
    (READ, False, True),
    (RECORD, False, False),
    (ANALYSE, False, False),
])
def test_each_class_costs_what_it_declares(entry, exclusive, lease):
    op = route(entry)
    assert op.is_exclusive() is exclusive
    assert op.takes_lease() is lease


def test_the_table_reads_back(capsys):
    assert RESOLVE_CURSOR in routing.describe()
    assert RESOLVE_READ in routing.describe()
    assert BUILD in routing.describe()
