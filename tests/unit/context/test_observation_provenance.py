"""Where an observation came from: the source file, and the exact seconds.

The defect these tests name, one at a time:

- an observation that cannot be placed answering with a file and a
  zero-length range, so a reader cannot tell "not here" from "at 0.0";
- a TIMELINE observation reporting the timeline's seconds as if they
  were the source's, so the reader opens the wrong part of the file;
- a fractional slope losing its precision on the way to seconds;
- the transform's uncertainty being dropped, so the range reads as
  exact when it is not;
- a non-invertible transform being inverted anyway;
- a file being found by NAME when the model's own record names no file;
- the holes being dropped from a whole-project answer.

The models here are small hand-built documents and the canonical
fixture. No real project is touched and no media is read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import observation_provenance
from library.tools.observation_provenance import (
    ObservationProvenanceError,
    resolve,
    resolve_all,
    resolve_in_model,
    source_placements,
)

FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(
        encoding="utf-8")
)


def _model(observations, *, transforms=(), asset_uri="/media/A.MOV",
           digest="sha256:a"):
    """A small model: one source asset, a source clock, a timeline clock."""
    return {
        "id": "proj",
        "schema_version": 1,
        "assets": [{"id": "asset-source", "kind": "video", "uri": asset_uri,
                    "content_digest": digest, "metadata": {},
                    "provenance": []}],
        "clocks": [
            {"id": "clock-source", "ticks_per_second": 1000,
             "domain": {"kind": "source", "asset_id": "asset-source"}},
            {"id": "clock-timeline", "ticks_per_second": 1000,
             "domain": {"kind": "project"}},
        ],
        "observations": observations,
        "time_transforms": list(transforms),
    }


def _observation(observation_id="obs-1", clock_id="clock-source",
                 start=0, end=1000):
    return {"id": observation_id, "asset_id": "asset-source",
            "interval": {"clock_id": clock_id, "start": start, "end": end},
            "modality": "speech", "payload": {"text": "hello"},
            "provenance_id": "prov-sha256:a-m1"}


def _transform(*, slope=(1, 1), offset=500, uncertainty=3,
               source=(0, 10000), target=(500, 10500), invertible=True):
    return {
        "id": "source-presentation",
        "from_clock": "clock-source",
        "to_clock": "clock-timeline",
        "provenance_id": "prov-sha256:a-m6",
        "segments": [{
            "from": {"clock_id": "clock-source", "start": source[0],
                     "end": source[1]},
            "to": {"clock_id": "clock-timeline", "start": target[0],
                   "end": target[1]},
            "slope": {"numerator": slope[0], "denominator": slope[1]},
            "offset_ticks": offset,
            "uncertainty_ticks": uncertainty,
            "invertible": invertible,
        }],
    }


def test_an_observation_on_a_source_clock_resolves_to_its_file_and_seconds():
    model = _model([_observation(start=1000, end=3500)])
    answer = resolve_in_model(model, model["observations"][0])
    assert answer["status"] == "resolved"
    assert answer["source_file"] == "/media/A.MOV"
    assert answer["content_digest"] == "sha256:a"
    assert answer["start_seconds"] == 1.0
    assert answer["end_seconds"] == 3.5
    assert answer["duration_seconds"] == 2.5
    assert answer["uncertainty_ticks"] is None


def test_a_timeline_observation_resolves_back_to_the_source_seconds():
    """The join put it on the timeline; the reader wants the source."""
    model = _model([_observation(clock_id="clock-timeline", start=1500,
                                 end=2500)],
                   transforms=[_transform()])
    answer = resolve_in_model(model, model["observations"][0])
    assert answer["status"] == "resolved"
    assert answer["source_file"] == "/media/A.MOV"
    # to = from + 500 ticks, so timeline 1500-2500 is source 1000-2000.
    assert answer["start_seconds"] == 1.0
    assert answer["end_seconds"] == 2.0
    assert answer["clock_id"] == "clock-timeline"
    assert answer["source_clock_id"] == "clock-source"


def test_the_transform_uncertainty_travels_with_the_range():
    model = _model([_observation(clock_id="clock-timeline", start=1500,
                                 end=2500)],
                   transforms=[_transform(uncertainty=7)])
    answer = resolve_in_model(model, model["observations"][0])
    assert answer["uncertainty_ticks"] == 7


def test_a_fractional_slope_keeps_its_precision_to_the_last_rounding():
    model = _model([_observation(clock_id="clock-timeline", start=300,
                                 end=600)],
                   transforms=[_transform(slope=(3, 2), offset=0,
                                         source=(0, 1000), target=(0, 1500))])
    answer = resolve_in_model(model, model["observations"][0])
    assert answer["status"] == "resolved"
    # to = from * 3/2, so timeline 300-600 is source 200-400.
    assert answer["start_seconds"] == 0.2
    assert answer["end_seconds"] == 0.401


def test_a_clock_with_no_path_to_a_source_is_unresolved_and_names_no_seconds():
    """An answer that carries a file and a zero-length range is worse
    than no answer: the reader cannot tell it from a real one."""
    model = _model([_observation(clock_id="clock-timeline", start=0, end=10)])
    answer = resolve_in_model(model, model["observations"][0])
    assert answer["status"] == "unresolved"
    assert answer["source_file"] is None
    assert answer["start_seconds"] is None
    assert answer["end_seconds"] is None
    assert "transform" in answer["reason"]


def test_an_observation_outside_the_transform_coverage_is_unresolved():
    model = _model([_observation(clock_id="clock-timeline", start=20000,
                                 end=20100)],
                   transforms=[_transform()])
    answer = resolve_in_model(model, model["observations"][0])
    assert answer["status"] == "unresolved"
    assert answer["source_file"] is None
    assert "coverage" in answer["reason"]


def test_a_non_invertible_transform_is_not_inverted_anyway():
    model = _model([_observation(clock_id="clock-timeline", start=1500,
                                 end=2500)],
                   transforms=[_transform(invertible=False)])
    answer = resolve_in_model(model, model["observations"][0])
    assert answer["status"] == "unresolved"
    assert answer["source_file"] is None
    assert "invertible" in answer["reason"]


def test_the_file_is_found_by_content_digest_when_the_model_names_none(tmp_path):
    """Identity, not a name: the catalog is asked by digest."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pipeline_data.json").write_text(json.dumps({
        "source_fingerprints": {"clip_001": {
            "path": str(project / "raw" / "A.MOV"),
            "size_bytes": 10,
            "content_digest": "sha256:a"}}}), encoding="utf-8")
    model = _model([_observation()], asset_uri="")
    answer = resolve_in_model(model, model["observations"][0],
                              project_folder=project)
    assert answer["status"] == "resolved"
    assert answer["source_file"] == str(project / "raw" / "A.MOV")


def test_resolve_all_reports_the_holes_too(tmp_path):
    """A reader that asked about everything learns about the holes too."""
    project = tmp_path / "proj"
    project.mkdir()
    model = dict(FIXTURE)
    model["observations"] = list(FIXTURE["observations"]) + [{
        "id": "obs-render", "asset_id": "asset-render",
        "interval": {"clock_id": "clock-render", "start": 0, "end": 24},
        "modality": "render", "payload": {"path": "renders/output.mov"},
        "provenance_id": "prov-1"}]
    from library.tools import project_model_store
    project_model_store.commit_project_model(
        project, model, expected_revision=0, reason="fixture")
    report = resolve_all(project)
    assert report["resolved"] == ["obs-1"]
    assert report["unresolved"] == ["obs-render"]
    assert report["resolutions"]["obs-render"]["reason"]
    assert report["resolutions"]["obs-render"]["source_file"] is None


def test_resolve_reads_the_canonical_fixture(tmp_path):
    """The fixture's observation sits on its source clock."""
    project = tmp_path / "proj"
    project.mkdir()
    from library.tools import project_model_store
    project_model_store.commit_project_model(
        project, FIXTURE, expected_revision=0, reason="fixture")
    answer = resolve(project, "obs-1")
    assert answer["status"] == "resolved"
    assert answer["source_file"].endswith("media/source.mov")
    assert answer["start_seconds"] == 0.0
    assert answer["end_seconds"] == 1.0
    missing = resolve(project, "obs-nope")
    assert missing["status"] == "unresolved"
    assert missing["source_file"] is None


def test_a_project_with_no_model_answers_unresolved_rather_than_raising(tmp_path):
    answer = resolve(tmp_path / "empty", "obs-1")
    assert answer["status"] == "unresolved"
    assert "Project Model" in answer["reason"]
    report = resolve_all(tmp_path / "empty")
    assert report["resolutions"] == {}


def test_an_empty_observation_id_is_refused():
    with pytest.raises(ObservationProvenanceError):
        resolve("/tmp/whatever", "")


def test_source_placements_resolves_several_observations_at_once():
    model = _model([_observation("obs-1"), _observation("obs-2", start=2000,
                                                        end=3000)])
    placements = source_placements(model, model["observations"])
    assert [row["observation_id"] for row in placements] == ["obs-1", "obs-2"]
    assert [row["start_seconds"] for row in placements] == [0.0, 2.0]
    assert observation_provenance.FORMAT == "observation_provenance/1"
