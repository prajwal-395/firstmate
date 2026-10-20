"""VI-027: one source-time coordinate system for every modality.

The Project Model holds several kinds of row - observations, events,
signals, tracks, transforms, query scopes - and they all name a clock.
A clock's tick RATE is part of what its numbers mean, so these tests
pin the two rules that keep one clock readable:

- a rate change rescales every row that carries that clock's ticks, and
- one clock keeps one rate, whatever writes into the model.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from library.tools import evidence_contract
from library.tools import project_model_store
from library.tools import reel_project_model as rpm
from library.tools.project_model import validate_project_model

FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(
        encoding="utf-8"))

CLOCK = "clock-source"


def _model() -> dict[str, Any]:
    return copy.deepcopy(FIXTURE)


def _model_with_every_kind_of_row() -> dict[str, Any]:
    """One row of every kind that carries a clock's ticks."""
    model = _model()
    model["clocks"] = [row for row in model["clocks"]
                       if row["id"] == CLOCK]
    model["observations"] = [{"id": "obs-1", "asset_id": "asset-source",
                              "interval": {"clock_id": CLOCK,
                                           "start": 1000, "end": 2000},
                              "modality": "speech", "payload": {"text": "x"},
                              "confidence": 0.9, "provenance_id": "prov-1",
                              "certainty": "observed"}]
    model["events"] = [{"id": "event-1", "interval": {"clock_id": CLOCK,
                                                     "start": 3000,
                                                     "end": 4000},
                        "kind": "picking_up", "participant_ids": [],
                        "status": "candidate", "certainty": "inferred",
                        "evidence_ids": ["obs-1"], "confidence": None}]
    model["signals"] = [{"id": "signal-1", "asset_id": "asset-source",
                         "interval": {"clock_id": CLOCK,
                                      "start": 5000, "end": 6000},
                         "measurement_type": "audio.level", "unit": "dBFS",
                         "samples": [-12.0], "sample_clock_id": CLOCK,
                         "provenance_id": "prov-1",
                         "processing_status": "analyzed"}]
    model["tracks"] = [{"id": "track-1", "entity_id": "object-1",
                        "clock_id": CLOCK,
                        "samples": [{"tick": 7000, "confidence": 0.9,
                                     "coordinate_space": "normalized_frame"}],
                        "provenance_id": "prov-1", "kind": "object",
                        "continuity": "discontinuous",
                        "coverage": "analyzed"}]
    model["time_transforms"] = [{
        "id": "transform-1", "from_clock": CLOCK, "to_clock": CLOCK,
        "segments": [{"from": {"clock_id": CLOCK, "start": 8000, "end": 9000},
                      "to": {"clock_id": CLOCK, "start": 8000, "end": 9000},
                      "slope": {"numerator": 1, "denominator": 1},
                      "offset_ticks": 0, "uncertainty_ticks": 20,
                      "invertible": True}],
        "provenance_id": "prov-1"}]
    model["query_results"] = [{"status": "present",
                               "candidate_evidence_ids": ["obs-1"],
                               "required_missing_lanes": [],
                               "completeness_basis": [
                                   {"scope": {"interval": {
                                       "clock_id": CLOCK,
                                       "start": 10000, "end": 11000}}}]}]
    return model


def test_a_rate_change_rescales_every_row_that_carries_the_clocks_ticks():
    model = _model_with_every_kind_of_row()
    evidence_contract.rescale_clock_references(model, CLOCK, 120)
    assert model["observations"][0]["interval"] == {
        "clock_id": CLOCK, "start": 120000, "end": 240000}
    assert model["events"][0]["interval"]["start"] == 360000
    assert model["signals"][0]["interval"]["end"] == 720000
    assert model["tracks"][0]["samples"][0]["tick"] == 840000
    segment = model["time_transforms"][0]["segments"][0]
    assert segment["from"]["start"] == 960000
    assert segment["to"]["end"] == 1080000
    assert segment["uncertainty_ticks"] == 2400
    scope = model["query_results"][0]["completeness_basis"][0]["scope"]
    assert scope["interval"]["end"] == 1320000


def test_a_rate_change_leaves_another_clocks_rows_alone():
    model = _model_with_every_kind_of_row()
    model["events"] = [{"id": "event-other", "interval": {
        "clock_id": "clock-timeline", "start": 3000, "end": 4000},
        "kind": "picking_up", "participant_ids": [], "status": "candidate",
        "certainty": "inferred", "evidence_ids": ["obs-1"],
        "confidence": None}]
    evidence_contract.rescale_clock_references(model, CLOCK, 120)
    assert model["events"][0]["interval"] == {
        "clock_id": "clock-timeline", "start": 3000, "end": 4000}


def test_one_clock_cannot_carry_two_rates():
    model = _model()
    model["clocks"].append({"id": CLOCK, "domain": {
        "kind": "source", "asset_id": "asset-source",
        "stream_id": "stream-video"},
        "ticks_per_second": 120000, "frame_rate": None})
    with pytest.raises(ValueError, match="two tick rates"):
        validate_project_model(model)


def test_a_fresh_evidence_base_joins_a_model_on_the_stored_clocks_grid():
    model = _model()
    model["clocks"] = [row for row in model["clocks"]
                       if row["id"] == CLOCK]
    model["clocks"][0]["ticks_per_second"] = 120000
    base = _model_with_every_kind_of_row()
    base["clocks"][0]["ticks_per_second"] = 1000
    rpm._merge_clocks(model, base)
    # The base's rows are rescaled onto the stored grid, and the stored
    # clock row survives: one clock, one rate.
    assert base["observations"][0]["interval"]["start"] == 120000
    assert base["events"][0]["interval"]["end"] == 480000
    stored_clock = next(row for row in model["clocks"]
                        if row["id"] == CLOCK)
    assert stored_clock["ticks_per_second"] == 120000


def test_a_clock_whose_rates_do_not_divide_refuses_the_join():
    model = _model()
    model["clocks"] = [row for row in model["clocks"]
                       if row["id"] == CLOCK]
    model["clocks"][0]["ticks_per_second"] = 1500
    base = _model_with_every_kind_of_row()
    with pytest.raises(ValueError, match="one clock keeps one rate"):
        rpm._merge_clocks(model, base)


def test_merging_evidence_onto_a_finer_clock_keeps_one_rate(tmp_path):
    stored = _model()
    for clock in stored["clocks"]:
        if clock["id"] == CLOCK:
            clock["ticks_per_second"] = 24000
    stored["observations"].append(
        {"id": "obs-stored", "asset_id": "asset-source",
         "interval": {"clock_id": CLOCK, "start": 120000, "end": 240000},
         "modality": "speech", "payload": {"text": "x"},
         "confidence": 0.9, "provenance_id": "prov-1",
         "certainty": "observed"})
    project_model_store.commit_project_model(tmp_path, stored,
                                             expected_revision=0)
    incoming = _model()
    for clock in incoming["clocks"]:
        if clock["id"] == CLOCK:
            clock["ticks_per_second"] = 1000
    incoming["events"] = [{"id": "event-1", "interval": {
        "clock_id": CLOCK, "start": 3000, "end": 4000},
        "kind": "picking_up", "participant_ids": [], "status": "candidate",
        "certainty": "inferred", "evidence_ids": ["obs-1"], "confidence": None}]
    revision = project_model_store.merge_evidence_model(
        tmp_path, incoming, expected_revision=1, author="ren",
        reason="fresh evidence")
    merged = project_model_store.load_project_model(tmp_path)[1]
    assert revision == 2
    clock = next(row for row in merged["clocks"] if row["id"] == CLOCK)
    assert clock["ticks_per_second"] == 24000
    # The incoming row is rescaled onto the stored grid; left alone it
    # would read 24x early.
    event = next(row for row in merged["events"] if row["id"] == "event-1")
    assert event["interval"] == {"clock_id": CLOCK,
                                 "start": 72000, "end": 96000}


def test_a_point_maps_to_the_tick_at_or_before_the_instant():
    assert evidence_contract.point_to_tick(1.2349) == 1234
    assert evidence_contract.point_to_tick(0.0) == 0
    # An interval maps floor/ceil, so it covers the measured span.
    assert evidence_contract.sec_to_ticks(1.2345, 1.2349) == (1234, 1235)


def test_a_point_refuses_what_is_not_an_instant():
    with pytest.raises(evidence_contract.TemporalAlignmentError):
        evidence_contract.point_to_tick(float("nan"))
    with pytest.raises(evidence_contract.TemporalAlignmentError):
        evidence_contract.point_to_tick(-0.001)
