"""The routing rule, applied without judgement.

The captain, 2026-09-12: *"today that judgement is mine and I have got
it wrong twice today"*. So the table has to answer the dispatch
question on its own, and these are the answers a supervisor reads.
"""


from library.tools.concurrency_routing import (
    FREE,
    may_run_together,
    route,
)

BUILD = "library.tools.reel_build.rebuild_reels_in_project"
PROMOTE = "library.tools.reel_build.promote_staged_reels"
RENDER_EDIT = "library.steps.step_6_01_render.resolve_build_timeline"
READ = "library.tools.timeline_ingest.snapshot_timeline"
RECORD = "library.tools.captain_edits.record_edit"
ANALYSE = "library.steps.step_1_03_semantic_analysis"


# ── The rule itself ─────────────────────────────────────────────────

def test_a_cursor_operation_runs_beside_no_other_cursor_operation_or_read():
    """Either order. A read's handle survives a promotion only by luck:
    the promotion deletes timelines."""
    assert not may_run_together(PROMOTE, RENDER_EDIT)
    assert not may_run_together(RENDER_EDIT, PROMOTE)
    assert not may_run_together(PROMOTE, READ)
    assert not may_run_together(READ, PROMOTE)


def test_a_build_runs_beside_reads_and_other_builds():
    """The ceiling this table exists to raise.

    The build holds the instance only around its own cursor
    sections - one exclusive hold per placed reel, shared holds for
    the gate and the surveys - so a second build's derivation and a
    reader's read proceed while the first build derives. The holds
    inside serialise the placements; the dispatch does not
    serialise the builds.
    """
    assert may_run_together(BUILD, READ)
    assert may_run_together(READ, BUILD)
    assert may_run_together(BUILD, BUILD)
    assert may_run_together(BUILD, PROMOTE)
    assert may_run_together(PROMOTE, BUILD)


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

def test_each_class_costs_what_it_declares():
    for entry, exclusive, lease in (
        (BUILD, False, False),
        (PROMOTE, True, True),
        (READ, False, True),
        (RECORD, False, False),
        (ANALYSE, False, False),
    ):
        op = route(entry)
        assert op.is_exclusive() is exclusive, entry
        assert op.takes_lease() is lease, entry
