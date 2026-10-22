"""A human judgement enters the world model as an INTERPRETED claim.

PR 3137 gave every world-model claim a class - `measured`, `inferred`,
`interpreted`, `unknown` - and the `interpreted` class carried NO claim,
because nothing a person said ever entered the world model. The
captain's corrections lived in the project's learned context and on
markers in Resolve, while the model's own reading of the same footage
was filed as an inference.

Every test here names a behavioural defect the world model could have:

* a captain's correction filed as an inference, so a reader could not
  tell a person's judgement from a model's reading,
* a judgement that REPLACES the model's claim about the same seconds
  instead of standing beside it,
* a correction that cannot be placed against a source being attached
  to the nearest one anyway (the guess the punchlist forbids),
* a judgement with no recorded time acquiring one on the way in,
* a MODEL's verdict counted as a human judgement.
"""
import json

import pytest

from library.tools import (
    claim_provenance,
    human_judgement,
    source_memory,
    world_model,
)

DIGEST = "d" * 64
SOURCE_FILE = "/media/LCATL0011.MXF"


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """An isolated memory root: nothing touches the machine store."""
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project(tmp_path, *, notes=(), learnings=()):
    """A project holding the captain's recorded judgements."""
    project = tmp_path / "project"
    catalog = [{
        "clip_id": "clip_004", "filename": "LCATL0011.MXF",
        "source_file": SOURCE_FILE, "path": SOURCE_FILE,
        "duration_seconds": 600.0, "frame_rate": 23.976,
    }]
    _write(project / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": catalog}},
        "source_fingerprints": {
            "clip_004": {"path": SOURCE_FILE, "content_digest": DIGEST,
                         "size_bytes": 10}},
    })
    if notes:
        _write(project / "marker_feedback" / "Reel_01.20260919T000000Z.markers.json",
               {"format": "marker_feedback/1", "timeline": "Reel 01",
                "timeline_fps": 23.976, "pulled_at": "2026-09-19T00:00:00Z",
                "notes": list(notes)})
    if learnings:
        _write(project / "learned_context" / "learnings.json",
               list(learnings))
    return project


def _note(**overrides):
    note = {
        "source": "timeline_marker", "name": "feedback",
        "note": "this cut here is jarring, it does not make sense",
        "text": "feedback\n\nthis cut here is jarring, it does not make sense",
        "frame": 100, "frame_in_timeline_space": 100, "color": "Blue",
        "read_at": "2026-09-19T00:00:00+00:00",
        "clips": [{"clip_color": "", "flags": [], "name": "LCATL0011.MXF",
                   "source_end": 500, "source_start": 0,
                   "source_file": SOURCE_FILE,
                   "timeline_end": 500, "timeline_start": 0,
                   "track_index": 2, "track_type": "video"}],
    }
    note.update(overrides)
    return note


def _m0(digest=DIGEST, duration=600.0):
    return {"content_digest": digest, "size_bytes": 10,
            "observed_paths": [SOURCE_FILE], "duration_seconds": duration,
            "video_streams": [], "audio_streams": [],
            "measured_levels_db": {}, "gop_frames": None}


def _build_world(memory_root, digest=DIGEST, judgements=None):
    target = memory_root / digest
    _write(target / source_memory.SLOT_SOURCE, _m0(digest))
    return world_model.build_source_world_model(
        digest, SOURCE_FILE, memory_root, judgements)


# ── The judgement enters ───────────────────────────────────────────


def test_the_captains_correction_enters_as_an_interpreted_claim(
        tmp_path, memory_root):
    """The defect: the interpreted class carried NO claim, because no
    human judgement ever entered the world model - so the captain's
    correction and the model's reading of the same seconds were filed
    the same way.

    A person's judgement is `interpreted`, and it says who said it.
    """
    project = _project(tmp_path, notes=[_note()])
    report = human_judgement.project_judgements(str(project))
    assert len(report["judgements"]) == 1
    judgement = report["judgements"][0]
    assert judgement["kind"] == "correction"
    assert judgement["author"] == "captain"
    assert judgement["statement"] == \
        "this cut here is jarring, it does not make sense"
    assert judgement["recorded_at"] == "2026-09-19T00:00:00+00:00"

    grouped = human_judgement.judgements_by_digest(report)
    assert list(grouped) == [DIGEST]

    _build_world(memory_root, judgements=grouped[DIGEST])
    record = source_memory.read_world_model(DIGEST, memory_root)
    filed = record["judgements"]
    assert len(filed) == 1
    assert filed[0]["provenance"]["class"] == "interpreted"
    assert filed[0]["provenance"]["claim"] == "judgement"
    assert filed[0]["author"] == "captain"
    assert filed[0]["statement"] == judgement["statement"]


def test_a_judgement_stands_beside_the_models_claim_and_does_not_replace_it(
        tmp_path, memory_root):
    """The defect: a captain saying a segment is wrong would EDIT the
    model's account of that second, and the disagreement would vanish.

    The judgement is filed in its own class, and the model's claim
    about the same seconds is still there.
    """
    project = _project(tmp_path, notes=[_note()])
    report = human_judgement.project_judgements(str(project))
    grouped = human_judgement.judgements_by_digest(report)

    digest = DIGEST
    target = memory_root / digest
    _write(target / source_memory.SLOT_SOURCE, _m0(digest))
    _write(target / source_memory.SLOT_SCENES, {
        "content_digest": digest, "source_file": SOURCE_FILE,
        "status": "measured",
        "scenes": [{"start": 0.0, "end": 600.0, "location": "studio",
                    "type": "indoor", "lighting": "dim",
                    "notable_features": []}],
        "scene_boundaries": [], "scene_coverage": None,
        "instrument": {"scene_pass": "folded window call"},
    })
    world_model.build_source_world_model(digest, SOURCE_FILE, memory_root,
                                         grouped[DIGEST])
    record = source_memory.read_world_model(digest, memory_root)
    entry = record["timeline"][100]
    assert entry["scene"]["location"] == "studio"
    assert entry["provenance"]["scene"]["class"] == "inferred"
    assert record["judgements"][0]["provenance"]["class"] == "interpreted"


def test_the_census_counts_the_interpreted_claims(tmp_path, memory_root):
    """The defect: a census that could not see a human judgement could
    not say that the world model holds one.

    The interpreted class is counted, and the defect count (a model
    description filed as a measurement) stays at zero.
    """
    project = _project(tmp_path, notes=[_note()])
    report = human_judgement.project_judgements(str(project))
    grouped = human_judgement.judgements_by_digest(report)
    _build_world(memory_root, judgements=grouped[DIGEST])
    record = source_memory.read_world_model(DIGEST, memory_root)
    census = claim_provenance.census(record)
    assert census["by_filed_class"]["interpreted"] == 1
    assert census["model_descriptions_filed_as_measurements"] == 0
    assert census["with_a_class"] == census["claims"]


# ── What a judgement may not do ────────────────────────────────────


def test_a_correction_that_names_no_source_is_not_placed(tmp_path):
    """The defect: a correction the project cannot place against a
    source would be attached to the nearest one anyway, and a guess
    that reads like a record is worse than an absence.

    It stays unattached, with the reason, and no world model claims it.
    """
    project = _project(tmp_path, learnings=[{
        "id": "lc-0002", "kind": "correction",
        "statement": "Keep exclusion: timeline 653.38..654.47s is struck",
        "said_by": "the captain", "status": "active",
        "created_at": "2026-09-09T22:43:02Z",
        "source": {"correction_type": "keep_exclusion",
                   "start": 653.38, "end": 654.47},
    }])
    report = human_judgement.project_judgements(str(project))
    assert report["judgements"] == []
    assert len(report["unattached"]) == 1
    assert report["unattached"][0]["unattached_reason"]
    assert human_judgement.judgements_by_digest(report) == {}


def test_a_judgement_with_no_recorded_time_keeps_no_time(tmp_path):
    """The defect: a judgement with no recorded time would acquire one
    on the way in - the time the file was read, say - and a reader
    would take it for when the captain said it.

    Unknown stays unknown.
    """
    project = _project(tmp_path, notes=[_note(read_at=None)])
    report = human_judgement.project_judgements(str(project))
    assert report["judgements"][0]["recorded_at"] is None


def test_a_models_verdict_is_not_a_human_judgement(tmp_path, memory_root):
    """The defect: a MODEL's verdict is a description of the picture
    (inferred). Counting it as an interpretation would put a model's
    reading into the class that means a person judged it.
    """
    digest = "e" * 64
    target = memory_root / digest
    _write(target / source_memory.SLOT_SOURCE, _m0(digest, 4.0))
    record = world_model.build_source_world_model(digest, SOURCE_FILE,
                                                  memory_root)
    census = claim_provenance.census(record)
    assert census["by_filed_class"].get("interpreted", 0) == 0
    assert claim_provenance.claim_class("verdict") == "inferred"
    assert claim_provenance.claim_class("judgement") == "interpreted"


def test_a_malformed_judgement_is_refused():
    """The defect: a judgement with no words, an unknown kind or an
    unknown speaker would be filed as one anyway.
    """
    with pytest.raises(human_judgement.HumanJudgementError):
        human_judgement.make_judgement("x", "correction", "captain", "  ",
                                       "origin")
    with pytest.raises(human_judgement.HumanJudgementError):
        human_judgement.make_judgement("x", "guess", "captain", "words",
                                       "origin")
    with pytest.raises(human_judgement.HumanJudgementError):
        human_judgement.make_judgement("x", "correction", "robot", "words",
                                       "origin")


def test_a_note_pulled_twice_is_one_judgement(tmp_path):
    """The defect: the engine pulls the captain's notes every round, so
    one note arrives again and again. Counting each pull would make one
    correction look like a dozen, and the census would be a count of
    rounds rather than of judgements.

    The FIRST pull is when the captain said it; a later pull records
    when the engine looked again.
    """
    first = _note(read_at="2026-09-11T03:08:43+00:00")
    later = _note(read_at="2026-09-19T04:32:07+00:00")
    project = _project(tmp_path, notes=[first, later])
    report = human_judgement.project_judgements(str(project))
    assert len(report["judgements"]) == 1
    assert report["judgements"][0]["recorded_at"] == \
        "2026-09-11T03:08:43+00:00"


def test_the_query_at_a_second_carries_the_judgement_about_it(
        tmp_path, memory_root):
    """The defect: a claim nobody can ask about is a claim nobody reads.
    The world model is queried at a second; the captain's judgement
    about that second is in the answer, with its class.
    """
    project = _project(tmp_path, notes=[_note()])
    report = human_judgement.project_judgements(str(project))
    grouped = human_judgement.judgements_by_digest(report)
    _build_world(memory_root, judgements=grouped[DIGEST])
    record = source_memory.read_world_model(DIGEST, memory_root)
    entry = world_model.query_at_time(record, 4.0)
    assert entry["judgements"][0]["provenance"]["class"] == "interpreted"
    assert entry["judgements"][0]["statement"] == \
        "this cut here is jarring, it does not make sense"
