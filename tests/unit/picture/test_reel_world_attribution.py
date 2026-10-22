"""VI-013b: the reel plan asks the world narrow speaker questions.

Every test names a behavioural defect the wiring could have: a guess
where nothing was measured, a cross-source misattribution, an absence
read as a negative, a budget overrun, and a report that moves the plan
it reports on.
"""

import json
from pathlib import Path

import pytest

from library.tools import reel_world_attribution as rwa

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
FILE_A = "/footage/cam_a.MXF"
FILE_B = "/footage/cam_b.MXF"


def _write_lane(memory_root: Path, digest: str, name: str, doc: dict) -> None:
    slot = memory_root / digest
    slot.mkdir(parents=True, exist_ok=True)
    (slot / name).write_text(json.dumps(doc), encoding="utf-8")


def _m1(digest: str, utterances: list) -> dict:
    return {
        "content_digest": digest,
        "status": "transcribed",
        "utterances": utterances,
    }


def _utterance(start: float, end: float, text: str) -> dict:
    words = [
        {"word": word, "start": start, "end": end}
        for word in text.split()
    ]
    return {"start": start, "end": end, "text": text, "words": words}


def _m7(digest: str, spans: list) -> dict:
    return {
        "content_digest": digest,
        "predicates": {"speaking": {"spans": spans}},
    }


def _span(voice: str, face: str, start: float, end: float) -> dict:
    return {"voice_track": voice, "face_track": face,
            "start": start, "end": end}


def _segment(speaker: str, timeline_start: float, timeline_end: float,
             source_file: str, source_start: float,
             source_end: float) -> dict:
    return {
        "speaker": speaker,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "source_file": source_file,
        "source_start": source_start,
        "source_end": source_end,
        "resolve_item_id": f"item-{timeline_start}",
        "text": "words here",
    }


def _catalog_and_recorded():
    catalog = [
        {"clip_id": "clip_a", "source_file": FILE_A,
         "duration_seconds": 100.0},
        {"clip_id": "clip_b", "source_file": FILE_B,
         "duration_seconds": 100.0},
    ]
    recorded = {
        "clip_a": {"path": FILE_A, "content_digest": DIGEST_A},
        "clip_b": {"path": FILE_B, "content_digest": DIGEST_B},
    }
    return catalog, recorded


def _write_state(project: Path, recorded: dict) -> None:
    """The runner's record: what digest each clip fingerprinted to.

    The module reads digests from state, never by re-fingerprinting
    media - planning must not hear the footage.
    """
    project.mkdir(parents=True, exist_ok=True)
    (project / "pipeline_data.json").write_text(
        json.dumps({"source_fingerprints": recorded}), encoding="utf-8")


def test_a_candidate_without_a_verified_span_is_skipped_not_guessed(tmp_path):
    """The defect: a span nothing can place answered as \"nobody spoke\".

    A candidate whose transcript segment names a file the catalog never
    fingerprinted has no identity to ask about. The finding is
    UNVERIFIED with the reason, never an attribution.
    """
    memory = tmp_path / "mem"
    catalog, _recorded = _catalog_and_recorded()
    _write_state(tmp_path / "proj", {})
    recorded = {}
    transcript = {"segments": [
        _segment("Zed", 0.0, 10.0, FILE_A, 0.0, 10.0)]}
    candidates = [{"start": 0.0, "end": 10.0, "speakers": ["Zed"],
                   "seconds_by_speaker": {"Zed": 10.0},
                   "share_by_speaker": {"Zed": 1.0}}]
    report = rwa.attribute_candidates(
        candidates, transcript, str(tmp_path / "proj"), catalog,
        memory_root=str(memory))
    assert report.candidates_asked == 1
    assert report.candidates_attributed == 0
    finding = report.findings[0]
    assert finding.status == "unverified_no_digest"
    assert finding.world_speakers == []
    assert finding.speakers_match is None


def test_no_catalog_is_a_skip_with_the_reason_not_a_refusal(tmp_path):
    """The defect: an uncatalogued run refused for an optional input.

    `clip_catalog` is optional: a run that never catalogued reports
    skipped with the reason and selects exactly as before.
    """
    transcript = {"segments": [
        _segment("Zed", 0.0, 10.0, FILE_A, 0.0, 10.0)]}
    candidates = [{"start": 0.0, "end": 10.0, "speakers": ["Zed"],
                   "seconds_by_speaker": {"Zed": 10.0},
                   "share_by_speaker": {"Zed": 1.0}}]
    report = rwa.attribute_candidates(
        candidates, transcript, str(tmp_path / "proj"), None,
        memory_root=str(tmp_path / "mem"))
    assert report.candidates_asked == 1
    assert report.candidates_attributed == 0
    assert report.findings[0].status == "skipped_no_catalog"
    assert "catalog" in report.findings[0].detail


def test_speech_is_joined_within_its_own_source_only(tmp_path):
    """The defect: source-seconds compared numerically across sources.

    Two sources whose clocks overlap numerically are different clocks:
    a Craig segment on cam B must not claim cam A's face track. The
    join the whole-view scan makes (every segment against every span)
    misattributes candidate 4 of the scratch reel plan this way; the
    wired path joins within one source.
    """
    memory = tmp_path / "mem"
    catalog, recorded = _catalog_and_recorded()
    _write_state(tmp_path / "proj", recorded)
    _write_lane(memory, DIGEST_A, "transcript.words.json", _m1(DIGEST_A, [
        _utterance(4.95, 7.57, "a turn")]))
    _write_lane(memory, DIGEST_A, "events.json", _m7(DIGEST_A, [
        _span("voice_001", "face_001", 4.5, 8.5)]))
    _write_lane(memory, DIGEST_B, "transcript.words.json", _m1(DIGEST_B, [
        _utterance(4.15, 14.12, "another turn")]))
    _write_lane(memory, DIGEST_B, "events.json", _m7(DIGEST_B, [
        _span("voice_009", "face_009", 4.0, 15.0)]))
    transcript = {"segments": [
        _segment("Zed", 0.0, 3.0, FILE_A, 4.95, 7.57),
        _segment("Yara", 3.0, 13.0, FILE_B, 4.15, 14.12),
    ]}
    candidates = [{"start": 0.0, "end": 13.0,
                   "speakers": ["Zed", "Yara"],
                   "seconds_by_speaker": {"Zed": 2.62, "Yara": 9.97},
                   "share_by_speaker": {"Zed": 0.208, "Yara": 0.792}}]
    report = rwa.attribute_candidates(
        candidates, transcript, str(tmp_path / "proj"), catalog,
        memory_root=str(memory))
    finding = report.findings[0]
    assert finding.status == "attributed"
    # face_001 speaks on cam A, where Zed's segment is - not on cam B,
    # where Yara's numerically-larger overlap sits.
    assert finding.speakers_match is True
    assert set(finding.world_speakers) == {"Zed", "Yara"}


def test_an_unmeasured_lane_is_absent_never_a_negative(tmp_path):
    """The defect: \"no M7\" read as \"nobody spoke\".

    A digest with a transcript but no event lane has no speaking
    measurement. The finding names the absent lane; the speakers stay
    empty rather than asserted empty.
    """
    memory = tmp_path / "mem"
    catalog, recorded = _catalog_and_recorded()
    _write_state(tmp_path / "proj", recorded)
    _write_lane(memory, DIGEST_A, "transcript.words.json", _m1(DIGEST_A, [
        _utterance(0.0, 10.0, "a turn")]))
    transcript = {"segments": [
        _segment("Zed", 0.0, 10.0, FILE_A, 0.0, 10.0)]}
    candidates = [{"start": 0.0, "end": 10.0, "speakers": ["Zed"],
                   "seconds_by_speaker": {"Zed": 10.0},
                   "share_by_speaker": {"Zed": 1.0}}]
    report = rwa.attribute_candidates(
        candidates, transcript, str(tmp_path / "proj"), catalog,
        memory_root=str(memory))
    finding = report.findings[0]
    assert finding.status == "unattributed_no_speech"
    assert "m7" in finding.lanes_absent
    assert finding.speakers_match is None
    assert finding.world_speakers == []


def test_every_fact_names_its_lane(tmp_path):
    """The defect: an answer whose facts carry no provenance.

    The query surface promises a provenance row per fact; the report
    counts them, and a fact without one fails the count, not the
    silence.
    """
    memory = tmp_path / "mem"
    catalog, recorded = _catalog_and_recorded()
    _write_state(tmp_path / "proj", recorded)
    _write_lane(memory, DIGEST_A, "transcript.words.json", _m1(DIGEST_A, [
        _utterance(0.0, 10.0, "a turn")]))
    _write_lane(memory, DIGEST_A, "events.json", _m7(DIGEST_A, [
        _span("voice_001", "face_001", 0.5, 9.5)]))
    transcript = {"segments": [
        _segment("Zed", 0.0, 10.0, FILE_A, 0.0, 10.0)]}
    candidates = [{"start": 0.0, "end": 10.0, "speakers": ["Zed"],
                   "seconds_by_speaker": {"Zed": 9.0},
                   "share_by_speaker": {"Zed": 1.0}}]
    report = rwa.attribute_candidates(
        candidates, transcript, str(tmp_path / "proj"), catalog,
        memory_root=str(memory))
    finding = report.findings[0]
    assert finding.status == "attributed"
    assert finding.facts > 0
    assert finding.facts_with_provenance == finding.facts
    assert set(finding.lanes_used) == {"m1", "m7"}
    assert finding.query_bytes > 0


def test_an_exhausted_budget_leaves_later_candidates_unasked(tmp_path):
    """The defect: a budget checked after the work.

    A budget of zero asks nothing: every candidate is UNASKED with the
    reason, never attributed and never wrong.
    """
    memory = tmp_path / "mem"
    catalog, recorded = _catalog_and_recorded()
    _write_state(tmp_path / "proj", recorded)
    transcript = {"segments": [
        _segment("Zed", 0.0, 10.0, FILE_A, 0.0, 10.0)]}
    candidates = [{"start": 0.0, "end": 10.0, "speakers": ["Zed"],
                   "seconds_by_speaker": {"Zed": 10.0},
                   "share_by_speaker": {"Zed": 1.0}}]
    report = rwa.attribute_candidates(
        candidates, transcript, str(tmp_path / "proj"), catalog,
        memory_root=str(memory), budget_seconds=0.0)
    assert report.candidates_attributed == 0
    assert report.findings[0].status == "skipped_budget"


def _resolve_fixture(tmp_path: Path, memory: Path):
    """The smallest transcript `resolve` survives, with two voices."""
    _write_lane(memory, DIGEST_A, "transcript.words.json", _m1(DIGEST_A, [
        _utterance(0.0, 5.0, "zed speaks first"),
        _utterance(5.0, 10.0, "yara answers back"),
    ]))
    _write_lane(memory, DIGEST_A, "events.json", _m7(DIGEST_A, [
        _span("voice_001", "face_001", 0.0, 5.0),
        _span("voice_002", "face_002", 5.0, 10.0),
    ]))
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    (project / "pipeline_data.json").write_text(json.dumps({
        "source_fingerprints": {
            "clip_a": {"path": FILE_A, "content_digest": DIGEST_A}},
    }), encoding="utf-8")
    transcript = {
        "derived_from": {"duration_seconds": 60.0},
        "segments": [
            _segment("Zed", 0.0, 5.0, FILE_A, 0.0, 5.0),
            _segment("Yara", 5.0, 10.0, FILE_A, 5.0, 10.0),
        ],
    }
    candidates = [{"start": 0.0, "end": 10.0,
                   "speakers": ["Zed", "Yara"],
                   "seconds_by_speaker": {"Zed": 5.0, "Yara": 5.0},
                   "share_by_speaker": {"Zed": 0.5, "Yara": 0.5}}]
    catalog = [{"clip_id": "clip_a", "source_file": FILE_A,
                "duration_seconds": 100.0}]
    data = {"timeline_transcript": transcript,
            "reel_candidates": candidates,
            "clip_catalog": catalog,
            "project_folder": str(project)}
    return project, data


def _scoped_write(memory: Path, real):
    """The real write, retargeted at the fixture memory root.

    `real` is the unpatched `attribute_and_write`: capture it before
    any patch, or the wrapper calls the patch it was built under.
    """
    def scoped(candidates, transcript, project_folder, catalog, **kwargs):
        kwargs["memory_root"] = str(memory)
        return real(candidates, transcript, project_folder, catalog,
                    **kwargs)
    return scoped


def test_the_report_never_moves_the_selection(tmp_path, monkeypatch):
    """The defect: evidence that rewrites the plan it reports on.

    `resolve` returns the identical selection whether the world answers
    or raises mid-sweep. The report lands in the review area; the
    return value carries no world keys.
    """
    from library.steps.step_3_04_select_reels import post_bridge
    import library.tools.reel_world_attribution as rwa_mod
    memory = tmp_path / "mem"
    project, data = _resolve_fixture(tmp_path, memory)
    llm_output = {"moments": [], "considered": [], "undetermined": []}

    def raising(*args, **kwargs):
        raise RuntimeError("the world is down")

    real_write = rwa_mod.attribute_and_write
    monkeypatch.setattr(rwa_mod, "attribute_and_write", raising)
    failed = post_bridge.resolve(dict(llm_output), dict(data))

    monkeypatch.setattr(rwa_mod, "attribute_and_write",
                        _scoped_write(memory, real_write))
    worked = post_bridge.resolve(dict(llm_output), dict(data))

    assert failed == worked
    assert "reel_selection" in worked
    assert not any("world" in key for key in worked)
    assert not any("world" in key
                   for key in worked["reel_selection"])
    stored = rwa.read_report(str(project))
    assert stored is not None
    assert stored.get("format") == rwa.FORMAT


def test_a_working_world_files_an_attributed_report(tmp_path, monkeypatch):
    """The defect: a sweep that answers but files nothing.

    With lanes on disk, the review file exists, names the format, and
    attributes the candidate - beside the selection, never inside it.
    """
    from library.steps.step_3_04_select_reels import post_bridge
    import library.tools.reel_world_attribution as rwa_mod
    memory = tmp_path / "mem"
    project, data = _resolve_fixture(tmp_path, memory)
    llm_output = {"moments": [], "considered": [], "undetermined": []}

    monkeypatch.setattr(rwa_mod, "attribute_and_write",
                        _scoped_write(memory, rwa_mod.attribute_and_write))
    result = post_bridge.resolve(dict(llm_output), dict(data))
    assert "reel_selection" in result
    stored = rwa.read_report(str(project))
    assert stored is not None
    assert stored["candidates_asked"] == 1
    assert stored["candidates_attributed"] == 1
    assert stored["speaker_set_match"] == "1/1"
