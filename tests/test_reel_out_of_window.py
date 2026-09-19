"""A placed range must touch its reel's declared windows - or else.

Reel 13 of the field test played 7.6 seconds drawn from another
moment's pool after its own closer: the stored closer ended at
342.03s, Reel 05's declared body opens at 342.038s, and the snap
widened the closer end to the bound segment edge at 349.54s.
`reel_ledger.audit_ranges` is the build-time assertion that catches
the next such range, and the ledger it files is the WHY on disk.
These tests pin both halves with the measured numbers - synthetic
windows, never a real project (tests never reach one).
"""

from types import SimpleNamespace

import pytest

from library.tools import reel_ledger
from library.tools.reel_ledger import (
    OutOfWindowRange,
    audit_ranges,
    file_reel_ledger,
    read_reel_ledger,
    stored_windows,
)


def _moment(body, closer=None):
    cta = None
    if closer is not None:
        cta = SimpleNamespace(timeline_start=closer[0],
                              timeline_end=closer[1])
    return SimpleNamespace(timeline_start=body[0], timeline_end=body[1],
                           call_to_action=cta)


# The measured shape: Reel 13 declared, Reel 05 declared, and what the
# snap made of Reel 13's closer (cta_end 342.03 -> 349.54 through
# Craig's opening "So").
R13_BODY = (889.92, 956.64)
R13_CLOSER = (328.608, 342.03)
R05_BODY = (342.038, 413.851)
R13_RANGES_UNENDED = [(889.89, 956.68), (328.54, 349.54)]

SIBLINGS = {
    13: {"body": R13_BODY, "closer": R13_CLOSER},
    5: {"body": R05_BODY, "closer": (179.83, 192.391)},
}


def test_stored_windows_reads_the_declaration():
    body, closer = stored_windows(_moment(R13_BODY, R13_CLOSER))
    assert body == R13_BODY
    assert closer == R13_CLOSER
    body, closer = stored_windows(_moment(R13_BODY))
    assert closer is None


def test_clean_ranges_pass_silently():
    ledger = audit_ranges(
        number=13, staging="s", final="f",
        stored_body=R13_BODY, stored_closer=R13_CLOSER,
        ranges=[(889.92, 956.64), (328.608, 342.03)],
        sibling_windows=SIBLINGS)
    assert ledger["disjoint"] == []
    assert all(row["overhang_seconds"] == {"before": 0.0, "after": 0.0}
               for row in ledger["ranges"])
    assert [row["origin"] for row in ledger["ranges"]] == ["body", "closer"]


def test_reel_13_overextension_is_kept_named_and_attributed():
    """The defect shape: kept (word-edge cover is legitimate), but the
    7.51s overhang is measured and Reel 05's pool is named."""
    ledger = audit_ranges(
        number=13, staging="s", final="f",
        stored_body=R13_BODY, stored_closer=R13_CLOSER,
        repaired_body=(889.89, 956.68),
        repaired_closer=(328.54, 349.54),
        ranges=R13_RANGES_UNENDED,
        sibling_windows=SIBLINGS,
        repair_moves=[{"boundary": "cta_end", "was": 342.03,
                       "now": 349.54, "through": "So"}])
    assert ledger["disjoint"] == []
    closer_row = ledger["ranges"][1]
    assert closer_row["origin"] == "closer"
    assert closer_row["overhang_seconds"]["after"] == pytest.approx(7.51)
    invaded = closer_row["invades"]
    assert len(invaded) == 1
    assert invaded[0]["reel"] == 5
    assert invaded[0]["window"] == "body"
    assert invaded[0]["seconds"] == pytest.approx([342.038, 349.54])
    # The repair move that did it is on the record.
    assert ledger["repaired"]["moves"][0]["boundary"] == "cta_end"


def test_small_word_edge_cover_is_not_an_invasion():
    """Reel 02's measured cover (closer end +0.22s over "bio.") reads
    as a small overhang into nobody's pool - kept, unattributed."""
    ledger = audit_ranges(
        number=2, staging="s", final="f",
        stored_body=(127.84, 160.83), stored_closer=(319.28, 328.231),
        ranges=[(127.84, 160.83), (319.28, 328.45)],
        sibling_windows={2: {"body": (127.84, 160.83),
                             "closer": (319.28, 328.231)}})
    assert ledger["disjoint"] == []
    assert ledger["ranges"][1]["overhang_seconds"]["after"] == (
        pytest.approx(0.219))
    assert ledger["ranges"][1]["invades"] == []


def test_wholly_foreign_range_refuses_with_ledger_attached():
    """A range no declared window touches is refused for this reel -
    and the refusal carries the ledger, because a skipped reel is
    exactly when the WHY is needed."""
    with pytest.raises(OutOfWindowRange) as caught:
        audit_ranges(
            number=13, staging="s", final="f",
            stored_body=R13_BODY, stored_closer=R13_CLOSER,
            ranges=[(889.92, 956.64), (500.0, 510.0)],
            sibling_windows=SIBLINGS)
    assert "500.00-510.00" in str(caught.value)
    ledger = caught.value.ledger
    assert ledger["disjoint"] == [1]
    assert ledger["ranges"][1]["invades"] == []


def test_reel_without_closer_audits_body_only():
    ledger = audit_ranges(
        number=7, staging="s", final="f",
        stored_body=(100.0, 160.0), stored_closer=None,
        ranges=[(100.0, 160.0)],
        sibling_windows={7: {"body": (100.0, 160.0), "closer": None}})
    assert [row["origin"] for row in ledger["ranges"]] == ["body"]
    with pytest.raises(OutOfWindowRange):
        audit_ranges(
            number=7, staging="s", final="f",
            stored_body=(100.0, 160.0), stored_closer=None,
            ranges=[(100.0, 160.0), (300.0, 310.0)],
            sibling_windows={7: {"body": (100.0, 160.0),
                                 "closer": None}})


def test_ledger_filing_merges_per_final_name(tmp_path):
    first = audit_ranges(
        number=13, staging="s", final="Reel 13",
        stored_body=R13_BODY, stored_closer=R13_CLOSER,
        ranges=R13_RANGES_UNENDED, sibling_windows=SIBLINGS)
    path = file_reel_ledger(tmp_path, "Reel 13", first)
    assert path is not None and path.exists()
    second = audit_ranges(
        number=5, staging="s", final="Reel 05",
        stored_body=R05_BODY, stored_closer=(179.83, 192.391),
        ranges=[(342.038, 413.851), (179.83, 192.391)],
        sibling_windows=SIBLINGS)
    file_reel_ledger(tmp_path, "Reel 05", second)
    merged = read_reel_ledger(tmp_path)
    assert set(merged["reels"]) == {"Reel 13", "Reel 05"}
    assert merged["reels"]["Reel 13"]["ranges"][1]["invades"][0][
        "reel"] == 5
    # Refiling one reel leaves the other alone.
    file_reel_ledger(tmp_path, "Reel 13", second)
    assert set(read_reel_ledger(tmp_path)["reels"]) == {
        "Reel 13", "Reel 05"}


def test_ledger_filing_survives_a_corrupt_file(tmp_path):
    ledger_file = (tmp_path / "pipeline_output" / "review"
                   / reel_ledger.FILENAME)
    ledger_file.parent.mkdir(parents=True)
    ledger_file.write_text("not json{", encoding="utf-8")
    ledger = audit_ranges(
        number=13, staging="s", final="Reel 13",
        stored_body=R13_BODY, stored_closer=R13_CLOSER,
        ranges=R13_RANGES_UNENDED, sibling_windows=SIBLINGS)
    assert file_reel_ledger(tmp_path, "Reel 13", ledger) is not None
    assert read_reel_ledger(tmp_path)["reels"]["Reel 13"]["format"] == (
        reel_ledger.FORMAT)


def test_skipped_out_of_window_is_a_filable_outcome():
    from library.tools import reel_phase_log
    assert "skipped_out_of_window" in reel_phase_log.OUTCOMES
    payload = reel_phase_log.assemble_summary(
        outcome="skipped_out_of_window", staging="s", final="f",
        decision="skipped out of window: reason")
    assert payload["outcome"] == "skipped_out_of_window"
