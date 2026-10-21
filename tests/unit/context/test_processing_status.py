"""B-04: every observation says whether it was analysed.

The defect this names: an observation carried no processing status, so
a reader could not tell a lane that ran and failed from a lane that
never ran, and both read as silence. Silence read as absence.
"""
from library.tools import processing_status


def _contract_row(status=None):
    contract = {"processing_status": status}
    return {"id": "obs-1", "payload": {"evidence_contract": contract}}


def _payload_row(status=None):
    return {"id": "obs-2", "payload": {"processing_status": status}}


def test_a_row_that_says_nothing_reads_as_silent_not_as_analysed():
    assert processing_status.processing_status_of({"id": "obs-0"}) is None
    assert processing_status.processing_status_of(
        {"id": "obs-0", "payload": {}}) is None


def test_the_status_is_read_from_the_contract_the_row_carries():
    assert processing_status.processing_status_of(
        _contract_row("failed")) == "failed"
    assert processing_status.processing_status_of(
        _payload_row("not_analyzed")) == "not_analyzed"


def test_the_status_is_read_from_the_schema_shaped_contract_too():
    row = {"id": "obs-3", "evidence": {"processing_status": "unavailable"}}
    assert processing_status.processing_status_of(row) == "unavailable"


def test_stamping_writes_onto_the_contract_the_row_carries():
    row = _contract_row()
    processing_status.stamp_processing_status(row, "analyzed")
    assert row["payload"]["evidence_contract"]["processing_status"] == "analyzed"
    plain = _payload_row()
    processing_status.stamp_processing_status(plain, "failed")
    assert plain["payload"]["processing_status"] == "failed"


def test_an_unknown_status_is_refused_rather_than_stored():
    import pytest

    with pytest.raises(ValueError):
        processing_status.stamp_processing_status(_payload_row(), "probed")


def test_the_status_follows_the_coverage_status_the_envelope_measured():
    assert processing_status.status_from_coverage_status(
        "failed") == processing_status.FAILED
    assert processing_status.status_from_coverage_status(
        "not_yet_analysed") == processing_status.NOT_ANALYZED
    assert processing_status.status_from_coverage_status(
        "unavailable") == processing_status.UNAVAILABLE
    assert processing_status.status_from_coverage_status(
        "inconclusive") == processing_status.INCONCLUSIVE
    assert processing_status.status_from_coverage_status(
        "complete") == processing_status.ANALYZED
    # A coverage status nobody measured leaves the row analysed: the
    # row exists because a producer wrote it.
    assert processing_status.status_from_coverage_status(
        "unknown") == processing_status.ANALYZED
    assert processing_status.status_from_coverage_status(
        None) == processing_status.ANALYZED


def test_a_lane_that_failed_is_named_by_the_lane_reader():
    model = {"observations": [
        {"id": "obs-1", "provenance_id": "prov-digest-m7",
         "payload": {"evidence_contract": {"processing_status": "failed"}}},
        {"id": "obs-2", "provenance_id": "prov-digest-m1",
         "payload": {"processing_status": "analyzed"}},
    ]}
    statuses = processing_status.lane_processing_statuses(model)
    assert statuses == {"m7": {"failed"}, "m1": {"analyzed"}}
