"""Focused regressions for the standing eval's reporting invariants.

Each case names a defect that would mis-score a request or start unsafe
work. No Resolve or real project is reached; fixtures use `tmp_path`.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import eval_corpus, eval_harness, heavy_work_lock


def test_unknown_filter_keys_raise():
    """An unknown id, domain, level, rung, cluster or verdict raises.

    Catches: `ren eval run --rung 12` (or a typo'd id) selecting zero
    requests and printing a successful empty report.
    """
    with pytest.raises(KeyError):
        eval_corpus.select(request_id="ST9.9")
    with pytest.raises(KeyError):
        eval_corpus.select(domain="ZZ")
    with pytest.raises(KeyError):
        eval_corpus.select(level=4)
    with pytest.raises(KeyError):
        eval_corpus.select(rung=12)
    with pytest.raises(KeyError):
        eval_corpus.select(cluster="K10")
    with pytest.raises(KeyError):
        eval_corpus.select(verdict="Q")


def test_rung_selections_match_the_roadmap():
    """Each rung selects the clusters its roadmap entry names.

    Catches: a rung edit that silently re-points a rung at the wrong
    requests, so "run per rung" stops measuring the rung.
    """
    assert {r["id"] for r in eval_corpus.select(rung=7)} == set(
        eval_corpus.CLUSTERS[0]["requests"])
    rung8 = {r["id"] for r in eval_corpus.select(rung=8)}
    assert rung8 == set(eval_corpus.CLUSTERS[2]["requests"]) | set(
        eval_corpus.CLUSTERS[4]["requests"])
    assert {r["id"] for r in eval_corpus.select(rung=9)} == set(
        eval_corpus.CLUSTERS[1]["requests"])
    rung10 = {r["id"] for r in eval_corpus.select(rung=10)}
    assert rung10 == set(eval_corpus.CLUSTERS[5]["requests"]) | set(
        eval_corpus.CLUSTERS[6]["requests"])
    assert {r["id"] for r in eval_corpus.select(rung=11)} == set(
        eval_corpus.CLUSTERS[7]["requests"])
    assert len(eval_corpus.select(rung=0)) == 40
    assert len(eval_corpus.select(rung=6)) == 120


def test_derive_verdict_never_blends():
    """A track A miss cannot be averaged into a pass.

    Catches: one missed operation being hidden by another followed
    operation or by a favorable export judgement.
    """
    assert eval_harness.derive_verdict(["followed", "followed"]) == "F"
    assert eval_harness.derive_verdict(["missed", "missed"]) == "X"
    assert eval_harness.derive_verdict(["missed"]) == "X"
    assert eval_harness.derive_verdict(["followed", "missed"]) == "P"
    assert eval_harness.derive_verdict(["followed", "part"]) == "P"
    assert eval_harness.derive_verdict(["part"]) == "P"


def test_judgement_requires_the_layer_on_a_miss():
    """A part/missed op without the first layer that broke is incomplete.

    Catches: a miss recorded as bare disappointment, which the report
    cannot route to a rung.
    """
    judgement = {
        "ops": [{"op": "12-frame dissolve", "outcome": "part",
                 "evidence": "10f placed", "layer": ""}],
        "hunks": [], "looks_good": {"verdict": "unjudged"}}
    problems = eval_harness.check_judgement_complete(judgement, [])
    assert any("layer" in p for p in problems)
    judgement["ops"][0]["layer"] = "-"
    assert any("layer" in p for p in
               eval_harness.check_judgement_complete(judgement, []))
    judgement["ops"][0]["layer"] = "V"
    assert eval_harness.check_judgement_complete(judgement, []) == []


def test_judgement_requires_evidence_hunk_attribution_and_judge_agreement(
        tmp_path):
    """A judgement cannot invent evidence, op attribution, or judge agreement.

    Catches: unsupported ops, arbitrary prose laundering a hunk as clean,
    and an LLM visual judgement with no captain-label calibration.
    """
    judgement = {
        "ops": [{"op": "12-frame dissolve", "outcome": "followed",
                 "evidence": "", "layer": "-"}],
        "hunks": [], "looks_good": {"verdict": "unjudged"}}
    assert any("evidence" in p
               for p in eval_harness.check_judgement_complete(judgement, []))
    hunks = [{"index": 0, "header": "@@ -1 +1 @@", "lines": ["-a", "+b"]}]
    judgement["ops"][0]["evidence"] = "readback line 12"
    problems = eval_harness.check_judgement_complete(judgement, hunks)
    assert any("hunk 0" in p for p in problems)
    judgement["hunks"] = [{"index": 0,
                           "explained_by": "not an actual op",
                           "break_reason": ""}]
    problems = eval_harness.check_judgement_complete(judgement, hunks)
    assert any("exactly match" in p for p in problems)
    judgement["hunks"][0]["explained_by"] = "12-frame dissolve"
    judgement["looks_good"] = {
        "verdict": "yes", "looked_by": "judge:gemma-4",
        "note": "clear export", "captain_label_agreement": ""}
    problems = eval_harness.check_judgement_complete(judgement, hunks)
    assert any("captain labels" in p for p in problems)
    judgement["looks_good"]["captain_label_agreement"] = "agree"
    assert eval_harness.check_judgement_complete(judgement, hunks) == []
    report = eval_harness.render_request_report(
        eval_corpus.select(request_id="TR3.1")[0], {}, {"hunks": hunks},
        {**judgement, "difference": "calibration fixture"},
        "F", "differ-explained")
    assert "Judge agreement with captain labels: **agree**" in report
    entry = {
        "request_id": "TR3.1", "domain": "TR", "level": 3,
        "scout_verdict": "P", "harness_verdict": "F",
        "agreement": "differ-explained", "broke_nothing": "clean",
        "looks_good": "yes", "looks_good_agreement": "agree"}
    aggregate = eval_harness.aggregate([entry])
    cell = aggregate["matrix"]["TR L3"]
    assert (cell["F"], cell["P"], cell["X"], cell["looks_yes"],
            cell["judge_agree"], cell["judge_disagree"],
            cell["judge_unjudged"]) == (1, 0, 0, 1, 1, 0, 0)
    summary = eval_harness.render_aggregate_report([entry], aggregate)
    assert "| Looks | Judge agreement |" in summary
    assert "| yes | agree |" in summary
    assert "| Judge agree | Judge disagree | Judge unjudged |" in summary
    assert "| TR L3 | 1 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 |" in summary

    out = _finalisable_out(tmp_path, "TR3.1")
    judgement_path = out / "TR3.1" / "judgement.json"
    stored = json.loads(judgement_path.read_text(encoding="utf-8"))
    stored["looks_good"] = judgement["looks_good"]
    stored["difference"] = "the tested build differs from the scout run"
    judgement_path.write_text(json.dumps(stored), encoding="utf-8")
    eval_harness.finalize_request(str(out), "TR3.1")
    assert eval_harness.main(["report", "--out", str(out)]) == 0
    aggregate_report = (out / "REPORT.md").read_text(encoding="utf-8")
    assert ("| TR3.1 | P | F | differ-explained | clean | yes | agree |"
            in aggregate_report)
    assert ("| TR L3 | 1 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 |"
            in aggregate_report)


def test_agreement_needs_the_reason_for_a_difference():
    """Match is agree; differ with reason is data; differ bare is failure.

    Catches: the proof "agreeing" with the scout by leaving the
    difference column blank.
    """
    assert eval_harness.agreement("F", "F") == "agree"
    assert eval_harness.agreement("P", "X", "behind-subject landed in "
                                  "#1389") == "differ-explained"
    assert eval_harness.agreement("P", "X") == "differ-unexplained"


def test_empty_project_path_does_not_corrupt_the_base_readback():
    """An absent path is not an empty string to replace everywhere.

    Catches: build_measures turning each base line into alternating
    `<rundir>` text, so every baseline item appears changed.
    """
    text = "timeline EVAL_BASE\nV1 clip_017 987-1204\n"
    assert eval_harness.normalise_readback(text, "", "") == (
        "timeline <evaltimeline>\nV1 clip_017 987-1204\n")


def test_diff_normalises_the_project_that_built_the_base_reference(tmp_path):
    """The readback diff compares two clones, not a clone and its source.

    Catches: baseline media paths surviving normalization because the
    harness replaced the pristine fixture path instead of BASE/run.
    """
    reference = tmp_path / "reference" / "BASE"
    base_project = reference / "run"
    base_project.mkdir(parents=True)
    (base_project / "pipeline_data.json").write_text("{}", encoding="utf-8")
    base_readback = reference / "readback.txt"
    base_readback.write_text(
        "timeline: EVAL_BASE\n"
        f"source_file: {base_project / 'raw' / 'a.mov'}\n",
        encoding="utf-8")

    request_out = tmp_path / "eval" / "TR2.1"
    request_out.mkdir(parents=True)
    run_project = request_out / "run"
    run_project.mkdir()
    (request_out / "readback.txt").write_text(
        "timeline: EVAL_RUN\n"
        f"source_file: {run_project / 'raw' / 'a.mov'}\n",
        encoding="utf-8")

    measures = eval_harness.build_measures(
        "TR2.1", request_out, str(base_readback), "",
        {"clone": {"base": str(tmp_path / "pristine-base"),
                   "dest": str(run_project)}})
    assert measures["hunks"] == []


def test_frame_stats_selects_video_dimensions_from_ffprobe_json(monkeypatch):
    """A second stream must not make export pixels unmeasurable.

    Catches: CSV dimensions containing an extra stream value and failing
    luma/chroma measurement on a valid video with attached data.
    """
    from types import SimpleNamespace

    probe_calls = 0

    def fake_run(argv, **kwargs):
        nonlocal probe_calls
        if argv[0] == "ffprobe":
            probe_calls += 1
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"streams": [
                    {"codec_type": "data", "width": 1920, "height": 1080},
                    {"codec_type": "video", "width": 2, "height": 2},
                ]}), stderr="")
        return SimpleNamespace(returncode=0,
                               stdout=bytes([255, 0, 0] * 4), stderr=b"")

    monkeypatch.setattr(eval_harness.subprocess, "run", fake_run)
    row = eval_harness.frame_stats("sample.mp4", [0.5])[0]

    assert probe_calls == 1
    assert row["luma"] == 76.2
    assert row["rgb"] == [255, 0, 0]
    assert row["chroma"] > 100


def test_export_report_keeps_pixel_audio_and_zoom_measures_separate(
        tmp_path, monkeypatch):
    """Track B reports rendered luma/chroma/zoom, LUFS and separation.

    Catches: an export report silently omitting one of the measurements
    the standing eval promises, or reading LUFS from a nonexistent field.
    """
    from types import SimpleNamespace

    from library.tools import render_qa

    pixels = [{"t": 1.0, "luma": 90.0, "rgb": [90, 80, 70],
               "chroma": 13.0, "saturation": 0.2}]
    monkeypatch.setattr(eval_harness, "_ffprobe_duration", lambda path: 10.0)
    monkeypatch.setattr(eval_harness, "frame_stats", lambda video, stamps: pixels)
    monkeypatch.setattr(
        eval_harness, "estimate_relative_zoom",
        lambda base, run, stamps: {"measured": True,
                                   "rows": [{"t": 1.0, "measured": True,
                                             "relative_scale": 1.08,
                                             "inliers": 18, "matches": 30}]})
    monkeypatch.setattr(
        render_qa, "measure_lufs",
        lambda video: SimpleNamespace(value={"input_i": -18.5}))
    monkeypatch.setattr(
        eval_harness, "_measure_export_separation",
        lambda video, project: {"measured": True, "judged": 1,
                                "windows": [{"margin_db": 12.0}]})

    measures = eval_harness.measure_exports(
        "base.mp4", "run.mp4", str(tmp_path / "base"),
        str(tmp_path / "run"))
    assert measures["frames"]["rows"][0]["base"]["chroma"] == 13.0
    assert measures["zoom"]["rows"][0]["relative_scale"] == 1.08
    assert measures["lufs"]["base_lufs"] == -18.5
    assert measures["separation"]["base"]["windows"][0]["margin_db"] == 12.0

    request = eval_corpus.select(request_id="ST1.1")[0]
    report = eval_harness.render_request_report(
        request, {"batch": "T1"}, measures,
        {"ops": [], "hunks": [], "looks_good": {"verdict": "unjudged"}},
        "F", "agree")
    assert "luma 90.0 -> 90.0" in report
    assert "chroma 13.0 -> 13.0" in report
    assert "1.08x" in report
    assert "-18.5 LUFS" in report
    assert "12.0 dB" in report


def test_separation_uses_the_selected_track_section_offset(tmp_path):
    """The export fit uses the music file time the manifest actually plays.

    Catches: measuring against second zero when a splice starts later in
    the source, which makes correlation and speech-over-bed margins false.
    """
    music = tmp_path / "music.wav"
    music.touch()
    state = {
        "step_outputs": {
            "compile_manifest": {"assembly_manifest": {
                "audio_mix": {
                    "bed": {"audio_path": str(music)},
                    "music_automation": [{"timeline_start": 0.0,
                                          "timeline_end": 4.0}]},
                "_spine_blocks": [{"position": 0,
                                   "block_type": "speech"}]}},
            "music_selection": {"music_selection": {
                "splices": [{"source_in": 35.0, "source_out": 39.0}]}}}}
    (tmp_path / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    inputs = eval_harness._separation_inputs(str(tmp_path))
    assert inputs["offset"] == 35.0
    assert inputs["music_path"] == str(music)


def test_clone_preserves_fixture_and_routes_request_note(tmp_path, monkeypatch):
    """Cloning preserves analysis, answers and a routable request note.

    Catches: dead host paths surviving a fresh-machine clone, or a `/tmp`
    alias making source identity discard analysis, stale answer paths, or
    the request being refused as an unknown marker source.
    """
    from library.tools import footage_identity

    host = tmp_path / "previous-machine" / "Apple001"
    base = tmp_path / "base"
    (base / "pipeline_output" / "steps").mkdir(parents=True)
    (base / "exports").mkdir()
    (base / "raw").mkdir()
    (base / "music").mkdir()
    footage = base / "raw" / "a.mov"
    footage.write_bytes(b"footage")
    fingerprint = footage_identity.fingerprint(str(footage))
    (base / "pipeline_data.json").write_text(json.dumps(
        {"source_fingerprints": {"clip_001": {
             "path": str(host / "raw" / "a.mov"), **fingerprint}},
         "preflight_completed": {"semantic_analysis": {"status": "SUCCESS"}},
         "preflight_code_hashes": {"semantic_analysis": "fixture-hash"},
         "catalog": {"clip_001": {"path": str(host / "raw" / "a.mov")}},
         "music": {"bed": str(host / "music" / "bed.wav")},
         "analysis": str(host / "pipeline_output" / "steps" / "analysis.json"),
         "export": str(host / "exports" / "base.mp4")}),
        encoding="utf-8")
    (base / "project.yaml").write_text(
        f'footage: "{host / "raw"}"\nmusic_library: "{host / "music"}"\n'
        'timeline_name: "X"\nresolve:\n'
        '  project_name: "host-resolve-project"\n'
        '  timeline_name: "X"\n', encoding="utf-8")
    (base / "music" / "bed.wav").write_bytes(b"bed")
    (base / "pipeline_output" / "steps" / "analysis.json").write_text(
        "{}", encoding="utf-8")
    (base / "exports" / "base.mp4").write_bytes(b"export")
    assert not host.exists()
    assert eval_harness.detect_recorded_root(str(base)) == str(host)
    monkeypatch.setattr(eval_harness, "current_preflight_code_hashes",
                        lambda: {"semantic_analysis": "current-hash"})
    canonical_dest = tmp_path / "run"
    alias = tmp_path / "run-alias"
    alias.symlink_to(canonical_dest, target_is_directory=True)
    ledger = eval_harness.clone_base(str(base), str(alias), "T1")
    data_path = canonical_dest / "pipeline_data.json"
    data = data_path.read_text(
        encoding="utf-8")
    assert str(host) not in data
    assert str(canonical_dest) in data
    state = json.loads(data)
    recorded = state["source_fingerprints"]
    current = footage_identity.fingerprints_for(
        footage_identity.enumerate_footage(str(canonical_dest))[0])
    assert footage_identity.compare(recorded, current).footage_changed is False
    assert ledger["dest"] == str(canonical_dest)
    assert (state["preflight_code_hashes"]["semantic_analysis"]
            == "current-hash")
    assert ledger["preflight_hashes_pinned"] == [{
        "step_id": "semantic_analysis", "fixture_hash": "fixture-hash",
        "eval_hash": "current-hash"}]
    cloned_config = (canonical_dest / "project.yaml").read_text(
        encoding="utf-8")
    assert 'project_name: "ren-eval-scratch-T1"' in cloned_config
    assert 'timeline_name: "EVAL_T1"' in cloned_config
    answers_src = tmp_path / "answers"
    answers_src.mkdir()
    (answers_src / "music_selection.json").write_text(
        json.dumps({"music_path": str(host / "music" / "bed.wav")}),
        encoding="utf-8")
    answers_dest = tmp_path / "eval-answers"
    eval_harness.seed_answers(
        str(answers_src), str(answers_dest), ledger["base"],
        ledger["dest"],
        path_rewrites=((ledger["recorded_root"], ledger["dest"]),))
    seeded = (answers_dest / "music_selection.json").read_text(
        encoding="utf-8")
    assert str(host) not in seeded
    assert str(canonical_dest / "music" / "bed.wav") in seeded
    report = eval_harness.render_request_report(
        eval_corpus.select(request_id="ST1.1")[0], {"clone": ledger}, {},
        {"ops": [], "hunks": [],
         "looks_good": {"verdict": "unjudged"}}, "F", "agree")
    assert "preflight hashes pinned for: semantic_analysis" in report
    from library.tools import marker_routing

    transition_request = eval_corpus.select(request_id="TR3.1")[0]
    eval_harness.inject_request(str(canonical_dest), transition_request,
                                "EVAL_T1")
    routed = marker_routing.route_project(str(canonical_dest))
    assert len(routed) == 1
    assert routed[0].source == "timeline_marker"
    assert routed[0].outcome == "routed"
    assert routed[0].steps == ["plan_transitions"]
    assert ledger["missing_refs"] == []


def _finalisable_out(tmp_path, request_id="ST1.1", *,
                     outcome="followed", layer="-"):
    out = tmp_path / "eval" / request_id
    out.mkdir(parents=True)
    (out / "run.json").write_text(json.dumps(
        {"batch": "T1", "timeline": "EVAL_T1"}), encoding="utf-8")
    (out / "measures.json").write_text(json.dumps({"hunks": []}),
                                       encoding="utf-8")
    (out / "judgement.json").write_text(json.dumps({
        "request_id": request_id,
        "ops": [{"op": "open on the quitting line", "outcome": outcome,
                 "evidence": "hook V1 0-216 = clip_017 src 987-1204",
                 "layer": layer}],
        "hunks": [],
        "looks_good": {"verdict": "yes", "looked_by": "human:proof",
                       "note": "hook lands"},
        "difference": ""}), encoding="utf-8")
    return tmp_path / "eval"


def test_a_looks_good_export_cannot_turn_a_track_a_miss_into_followed(tmp_path):
    """A favorable export judgement stays separate from operation follow-through.

    Catches: track B's visual approval changing a missed track A operation
    into F, which would blend two separate evaluation questions.
    """
    out = _finalisable_out(tmp_path, outcome="missed", layer="V")
    entry = eval_harness.finalize_request(str(out), "ST1.1")
    assert entry["harness_verdict"] == "X"
    assert entry["looks_good"] == "yes"


def test_broken_hunk_stays_separate_from_followed_verdict(tmp_path):
    """An unwanted timeline hunk reports BROKE while its op can still pass.

    Catches: a side effect either being counted clean because it has prose,
    or preventing the harness from reporting the other score independently.
    """
    out = _finalisable_out(tmp_path)
    request_out = out / "ST1.1"
    hunk = {"index": 0, "header": "@@ -1 +1 @@", "lines": ["-a", "+b"]}
    (request_out / "measures.json").write_text(
        json.dumps({"hunks": [hunk]}), encoding="utf-8")
    judgement_path = request_out / "judgement.json"
    judgement = json.loads(judgement_path.read_text(encoding="utf-8"))
    judgement["hunks"] = [{"index": 0, "explained_by": "",
                           "break_reason": ""}]
    judgement_path.write_text(json.dumps(judgement), encoding="utf-8")
    with pytest.raises(ValueError, match="choose exactly one"):
        eval_harness.finalize_request(str(out), "ST1.1")

    judgement["hunks"][0]["break_reason"] = "unrequested V2 placement"
    judgement_path.write_text(json.dumps(judgement), encoding="utf-8")
    entry = eval_harness.finalize_request(str(out), "ST1.1")
    assert entry["harness_verdict"] == "F"
    assert entry["broke_nothing"] == "BROKE"
    matrix = eval_harness.aggregate([entry])["matrix"]["ST L1"]
    assert (matrix["F"], matrix["BROKE"], matrix["looks_yes"]) == (1, 1, 1)
    assert (matrix["P"], matrix["X"], matrix["clean"],
            matrix["looks_no"], matrix["looks_unjudged"]) == (0, 0, 0, 0, 0)
    report = (request_out / "report.md").read_text(encoding="utf-8")
    assert "Broke nothing: **BROKE**" in report
    aggregate = eval_harness.render_aggregate_report(
        [entry], eval_harness.aggregate([entry]))
    assert "| Cell | F | P | X | Clean | Broke |" in aggregate
    assert "| ST L1 | 1 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 |" in aggregate


def test_refused_stored_answer_escalates_to_the_brain():
    """A step asking again after being served never gets the same file.

    Catches: the loop re-feeding a contract-refused answer forever (the
    base `audio_mix` answer predates the `cleanup_plan` key, so the step
    re-requests) - the refusal must reach the brain, not loop.
    """
    assert eval_harness.decide_answer("audio_mix", False, True, set()) == (
        "copy-stored")
    assert eval_harness.decide_answer("audio_mix", False, True,
                                      {"audio_mix"}) == "needs-brain"
    assert eval_harness.decide_answer("plan_vfx", False, False, set()) == (
        "needs-brain")
    assert eval_harness.decide_answer("mesh_spine", True, True, set()) == (
        "have-target")


def test_fresh_upstream_answer_invalidates_dependent_cached_answers(
        tmp_path):
    """A changed producer invalidates its cached consumers transitively.

    Catches: serving a cached `select_broll` or `mesh_spine` answer after a
    fresh upstream answer changed the inputs it was based on.
    """
    dag = {
        "nodes": [
            {"id": "speech_sequence",
             "step_ref": "steps/step_2_02_speech_sequence"},
            {"id": "mesh_spine", "step_ref": "steps/step_2_05_mesh_spine"},
            {"id": "assign_aroll",
             "step_ref": "steps/step_3_01_assign_aroll"},
            {"id": "select_broll",
             "step_ref": "steps/step_3_02_select_broll"},
            {"id": "color_grade",
             "step_ref": "steps/step_5_01_color_grade"},
            {"id": "creative_direction",
             "step_ref": "steps/step_2_01_creative_direction"},
        ],
        "edges": [
            {"from": "speech_sequence", "to": "mesh_spine"},
            {"from": "mesh_spine", "to": "assign_aroll"},
            {"from": "assign_aroll", "to": "select_broll"},
            {"from": "select_broll", "to": "color_grade"},
        ],
    }
    answers = tmp_path / "answers"
    answers.mkdir()
    cached = {
        "speech_sequence.json",
        "mesh_spine.json",
        "assign_aroll.json",
        "select_broll.json",
        "step_5_01_color_grade__stills.json",
        "creative_direction.json",
    }
    for name in cached:
        (answers / name).write_text("{}", encoding="utf-8")

    invalidated = eval_harness.invalidate_dependent_answers(
        answers, ("speech_sequence",), dag=dag)

    assert set(invalidated) == {
        name[:-5] for name in cached - {"creative_direction.json"}}
    assert {path.name for path in answers.iterdir()} == {
        "creative_direction.json"}


def test_answer_loop_drops_cached_consumer_before_its_request_is_served(
        tmp_path, monkeypatch, capsys):
    """A brain answer for an upstream request clears cache before the next.

    Catches: a downstream LLM_REQUEST_READY racing ahead of the answer
    loop's next poll and receiving an answer authored for older inputs.
    """
    import time as _time
    from types import SimpleNamespace

    project = tmp_path / "project"
    requests = project / "pipeline_output" / "llm_requests"
    responses = project / "pipeline_output" / "llm_responses"
    requests.mkdir(parents=True)
    responses.mkdir(parents=True)
    answers = tmp_path / "answers"
    answers.mkdir()
    stale_spine = answers / "mesh_spine.json"
    stale_spine.write_text('{"stale": true}', encoding="utf-8")
    log = tmp_path / "edit.log"
    speech_request = requests / "speech_sequence.json"
    spine_request = requests / "mesh_spine.json"
    log.write_text(f"LLM_REQUEST_READY: {speech_request}\n",
                   encoding="utf-8")
    completed = json.dumps({
        "status": "SUCCESS", "completed": [], "failed": [],
        "outstanding_failures": [], "stranded_failures": [],
        "skipped": [],
    }, indent=2)
    sleeps = 0

    def advance_run(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            (responses / "speech_sequence.json").write_text(
                '{"fresh": true}', encoding="utf-8")
            with log.open("a", encoding="utf-8") as stream:
                stream.write(f"LLM_REQUEST_READY: {spine_request}\n")
        elif sleeps == 2:
            (responses / "mesh_spine.json").write_text(
                '{"fresh": true}', encoding="utf-8")
            with log.open("a", encoding="utf-8") as stream:
                stream.write(completed + "\n")

    monkeypatch.setattr(eval_harness, "time", SimpleNamespace(
        time=_time.time, sleep=advance_run))

    result = eval_harness.answer_loop(
        log, str(project), str(answers), set(), answer_timeout_seconds=10)

    output = capsys.readouterr().out
    assert "NEEDS BRAIN (no stored answer): mesh_spine" in output
    assert "eval: auto-answered mesh_spine" not in output
    assert result["fresh_answers"] == ["speech_sequence", "mesh_spine"]
    assert result["invalidated_cached_answers"] == ["mesh_spine"]
    assert not stale_spine.exists()


def test_failed_summary_is_not_a_done_run():
    """Unscoped FAILED summaries raise with the log tail.

    Catches: a failed edit stage marching on to the Resolve build - the
    proof's base run did exactly this before the fix.
    """
    def summary(status):
        return json.dumps({
            "status": status, "completed": [], "failed": [],
            "outstanding_failures": [], "stranded_failures": [],
            "skipped": [],
        }, indent=2)

    assert eval_harness.parse_run_status(summary("SUCCESS")) == "SUCCESS"
    assert eval_harness.parse_run_status(summary("FAILED")) == "FAILED"
    assert eval_harness.parse_run_status("still running\n") is None


def test_prior_failed_run_history_does_not_end_the_current_edit():
    """A prior failure is not the current invocation's terminal summary.

    Catches: restart history's `status: FAILED` making the eval leave an
    in-flight edit replay and strand its pipeline process at an LLM handoff.
    """
    stale_record = json.dumps({
        "status": "FAILED", "current_step": "review_rough_cut",
        "run_history": [{"status": "FAILED"}],
    })
    assert eval_harness.parse_run_status(stale_record) is None


def test_recovered_post_bridge_traceback_does_not_end_answer_loop(
        tmp_path, monkeypatch):
    """A rejected answer can be retried while the same run keeps going.

    Catches: the eval answer loop treating a recovered post-bridge traceback
    as terminal, abandoning later handshakes before the pipeline summary.
    """
    log = tmp_path / "edit.log"
    summary = json.dumps({
        "status": "SUCCESS", "completed": [], "failed": [],
        "outstanding_failures": [], "stranded_failures": [],
        "skipped": [],
    }, indent=2)
    log.write_text(
        "Traceback (most recent call last):\npost-bridge rejected attempt 1\n",
        encoding="utf-8")

    def finish_after_poll(_seconds):
        log.write_text(
            "Traceback (most recent call last):\n"
            "post-bridge rejected attempt 1\n" + summary + "\n",
            encoding="utf-8")

    # The harness's own `time` name, never the global module: a stray
    # thread calling time.sleep must not run this stub
    # (`tests/test_agent_wait_narrowing.py`).
    import time as _time
    from types import SimpleNamespace
    monkeypatch.setattr(eval_harness, "time", SimpleNamespace(
        time=_time.time, sleep=finish_after_poll))
    result = eval_harness.answer_loop(
        log, str(tmp_path), str(tmp_path / "answers"), set())

    assert result["run_status"] == "SUCCESS"


def test_answer_loop_can_return_partial_only_for_a_scoped_stage(tmp_path):
    """The expected scoped status reaches the stage-specific verifier.

    Catches: rejecting every PARTIAL status before checking whether it only
    reflects render and validate being intentionally skipped.
    """
    log = tmp_path / "edit.log"
    def summary(status):
        return json.dumps({
            "status": status, "completed": [], "failed": [],
            "outstanding_failures": [], "stranded_failures": [],
            "skipped": [],
        }, indent=2)

    log.write_text(summary("PARTIAL") + "\n", encoding="utf-8")

    result = eval_harness.answer_loop(
        log, str(tmp_path), str(tmp_path / "answers"), set(),
        allow_partial=True)

    assert result["run_status"] == "PARTIAL"
    with pytest.raises(RuntimeError, match="run ended PARTIAL"):
        eval_harness.answer_loop(
            log, str(tmp_path), str(tmp_path / "answers"), set())

    log.write_text(summary("FAILED") + "\n", encoding="utf-8")
    result = eval_harness.answer_loop(
        log, str(tmp_path), str(tmp_path / "answers"), set(),
        allow_partial=True)
    assert result["run_status"] == "FAILED"
    with pytest.raises(RuntimeError, match="run ended FAILED"):
        eval_harness.answer_loop(
            log, str(tmp_path), str(tmp_path / "answers"), set())


def test_edit_stage_accepts_partial_when_only_render_and_validate_are_skipped(
        tmp_path):
    """PARTIAL is usable only when its declared edit work really completed.

    Catches: treating the edit's intentional render/validate skips as failure,
    or allowing a failed or incomplete edit to proceed to the Resolve build.
    """
    log_path = tmp_path / "edit.log"
    summary = {
        "status": "partial",
        "completed": list(eval_harness.EDIT_RERUN_CHAIN),
        "failed": [],
        "outstanding_failures": [],
        "stranded_failures": [],
        "skipped": ["render", "validate"],
    }

    def write_log():
        log_path.write_text(
            "pipeline output\n" + json.dumps(summary, indent=2)
            + "\nRunning pipeline for: fixture\n", encoding="utf-8")

    write_log()

    result = eval_harness.validate_scoped_run(
        str(log_path), "edit", eval_harness.EDIT_RERUN_CHAIN,
        ("render", "validate"), "PARTIAL")

    assert result["skipped_steps"] == ["render", "validate"]
    summary["completed"].remove("compile_manifest")
    write_log()
    with pytest.raises(RuntimeError, match="did not complete required steps"):
        eval_harness.validate_scoped_run(
            str(log_path), "edit", eval_harness.EDIT_RERUN_CHAIN,
            ("render", "validate"), "PARTIAL")

    summary["completed"].append("compile_manifest")
    summary["failed"] = ["plan_transitions"]
    write_log()
    with pytest.raises(RuntimeError, match="recorded failures"):
        eval_harness.validate_scoped_run(
            str(log_path), "edit", eval_harness.EDIT_RERUN_CHAIN,
            ("render", "validate"), "PARTIAL")


def test_edit_eval_carries_validation_failure_outside_its_scope(tmp_path):
    """A base validation failure does not hide a completed edit-stage run.

    Catches: the eval refusing its edit chain because validation is skipped
    and an earlier validation failure remains recorded on the fixture.
    """
    log_path = tmp_path / "edit.log"
    summary = {
        "status": "failed",
        "completed": list(eval_harness.EDIT_RERUN_CHAIN),
        "failed": [],
        "outstanding_failures": ["validate"],
        "stranded_failures": [],
        "skipped": ["render", "validate"],
    }
    log_path.write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    result = eval_harness.validate_scoped_run(
        str(log_path), "edit", eval_harness.EDIT_RERUN_CHAIN,
        ("render", "validate"), "FAILED")

    assert result["carry_forward_failures"] == ["validate"]
    assert result["stranded_failures"] == []
    assert result["scope_status"] == "COMPLETED"

    summary["outstanding_failures"] = ["compile_manifest"]
    log_path.write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="outstanding_in_scope"):
        eval_harness.validate_scoped_run(
            str(log_path), "edit", eval_harness.EDIT_RERUN_CHAIN,
            ("render", "validate"), "FAILED")

    summary["outstanding_failures"] = ["validate"]
    summary["stranded_failures"] = ["retired_step"]
    log_path.write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="stranded"):
        eval_harness.validate_scoped_run(
            str(log_path), "edit", eval_harness.EDIT_RERUN_CHAIN,
            ("render", "validate"), "FAILED")


def test_heavy_gate_matches_local_heavy_processes_not_remote_replays(
        monkeypatch):
    """Only local heavy work blocks; API-bound pipeline replays may overlap.

    Catches: an eval waiting for a remote-bound semantic-analysis run even
    though neither job needs the machine's local model, render or Resolve.
    """
    from types import SimpleNamespace

    monkeypatch.setattr(
        eval_harness.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=("100 python3 manage_project.py run /tmp/other\n"
                    "101 python3 library/processes/edit_video/run_pipeline.py\n"
                    "102 /opt/homebrew/bin/ffmpeg -i input.mov output.mov\n"
                    "103 /venv/bin/python3 -m mlx_lm.server\n"
                    "104 python3 -m library.tools.eval_harness run "
                    "--base x --out other\n"
                    "105 python3 vision_pipeline_v3.py --api\n"
                    "106 python3 manage_project.py run /tmp/other "
                    "--step render\n"
                    "107 sh scripts/full_suite_gate.sh\n"
                    f"{os.getpid()} python3 -m library.tools.eval_harness "
                    "run --base x --out own\n"
                    "108 codex --model gpt-6-luna 'brief mentions ffmpeg "
                    "mlx_lm llama-server full_suite_gate.sh'\n")))
    jobs = eval_harness.heavy_jobs()
    assert len(jobs) == 4
    assert "ffmpeg" in jobs[0]
    assert "mlx_lm.server" in jobs[1]
    assert "--step render" in jobs[2]
    assert "full_suite_gate.sh" in jobs[3]
    assert all("--out own" not in job for job in jobs)


def test_memory_gate_counts_reclaimable_macos_pages():
    """Inactive and purgeable cache count as available memory.

    Catches: a healthy Mac waiting forever for free pages while most usable
    memory sits in reclaimable file cache.
    """
    vm_stat = ("Pages free: 10.\nPages inactive: 20.\n"
               "Pages purgeable: 30.\nPages speculative: 1000.")

    assert eval_harness.memory_free_mb(vm_stat) == 60 * 16384 / 1048576


def test_heavy_work_lock_records_owner_and_releases_by_removing_directory(
        tmp_path, monkeypatch):
    """The local heavy-work mutex names its owner and is fully released.

    Catches: an eval leaving an ownerless or stale lock that blocks the next
    lane after its Resolve job has finished.
    """
    lock_dir = tmp_path / "heavy-work.lock"
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR", lock_dir)

    eval_harness.take_heavy_lock("task-123 resolve render")
    jobs = heavy_work_lock._scheduler().jobs()
    assert [(job["owner"], job["state"]) for job in jobs] == [
        ("task-123 resolve render", "running")]
    assert lock_dir.exists()

    eval_harness.release_heavy_lock()
    assert heavy_work_lock._scheduler().jobs() == []
    assert not lock_dir.exists()


def test_eval_bracket_switch_and_timeline_restore_use_resolve_guards(
        monkeypatch):
    """Project switches and timeline restoration hold the shared guards.

    Catches: the eval moving Resolve's shared cursor outside a lease or
    restoring the captain's timeline with an unregistered direct setter.
    """
    from contextlib import contextmanager

    from library.tools import marker_feedback, resolve_lock

    events = []
    lease_active = False

    @contextmanager
    def fake_lease(purpose, **kwargs):
        nonlocal lease_active
        assert not lease_active
        lease_active = True
        events.append(f"lease:{purpose}")
        try:
            yield None
        finally:
            lease_active = False

    @contextmanager
    def fake_cursor_fence(project, timeline, purpose):
        assert lease_active
        events.append(f"fence:{purpose}")
        project.current_timeline = timeline
        yield None

    monkeypatch.setattr(resolve_lock, "resolve_lease", fake_lease)
    monkeypatch.setattr(resolve_lock, "cursor_fence", fake_cursor_fence)

    class Timeline:
        def __init__(self, name):
            self.name = name

        def GetName(self):
            return self.name

    class Project:
        def __init__(self, name, timeline=None):
            self.name = name
            self.current_timeline = timeline
            self.timelines = [timeline] if timeline else []

        def GetName(self):
            return self.name

        def GetCurrentTimeline(self):
            return self.current_timeline

        def GetTimelineCount(self):
            return len(self.timelines)

        def GetTimelineByIndex(self, index):
            return self.timelines[index - 1]

    class Manager:
        def __init__(self, captain):
            self.current = captain
            self.projects = {}

        def GetCurrentProject(self):
            return self.current

        def SaveProject(self):
            assert lease_active
            events.append(f"save:{self.current.GetName()}")
            return True

        def GetProjectListInCurrentFolder(self):
            return list(self.projects)

        def CreateProject(self, name):
            assert lease_active
            events.append(f"create:{name}")
            project = Project(name)
            self.projects[name] = project
            self.current = project
            return project

        def LoadProject(self, name):
            assert lease_active
            events.append(f"load:{name}")
            self.current = self.projects.get(name, captain)
            return self.current

        def DeleteProject(self, name):
            assert lease_active
            events.append(f"delete:{name}")
            self.projects.pop(name, None)
            return True

    timeline = Timeline("captain timeline")
    captain = Project("captain project", timeline)
    manager = Manager(captain)

    class Resolve:
        def GetProjectManager(self):
            return manager

    def connect_under_lease():
        assert lease_active
        events.append("connect")
        return Resolve()

    monkeypatch.setattr(marker_feedback, "connect_resolve",
                        connect_under_lease)
    scratch = f"{eval_harness.SCRATCH_PREFIX}T1"

    saved = eval_harness.resolve_bracket_start(scratch)
    assert saved == {"project": "captain project",
                     "timeline": "captain timeline", "saved": True}
    assert events.index("save:captain project") < events.index(
        f"create:{scratch}")
    assert events.index(f"lease:open eval scratch {scratch}") < events.index(
        "connect")

    restored = eval_harness.resolve_bracket_end(scratch, saved)
    assert restored == {"project_restored": True,
                        "timeline_restored": True,
                        "scratch_deleted": True}
    assert manager.current is captain
    assert captain.current_timeline is timeline
    assert "fence:restore eval timeline captain timeline" in events
    assert f"delete:{scratch}" in events
    assert events.index(f"lease:restore after eval scratch {scratch}") < \
        max(i for i, event in enumerate(events) if event == "connect")


def test_eval_bracket_start_failure_restores_captain_and_removes_scratch(
        monkeypatch):
    """A partial Resolve project switch cannot strand the captain elsewhere.

    Catches: CreateProject succeeding before SaveProject fails, leaving the
    shared Resolve cursor on a scratch project with no restoration attempt.
    """
    from contextlib import contextmanager

    from library.tools import marker_feedback, resolve_lock

    events = []

    class Timeline:
        def __init__(self, name):
            self.name = name

        def GetName(self):
            return self.name

    class Project:
        def __init__(self, name, timeline=None):
            self.name = name
            self.current_timeline = timeline
            self.timelines = [timeline] if timeline else []

        def GetName(self):
            return self.name

        def GetCurrentTimeline(self):
            return self.current_timeline

        def GetTimelineCount(self):
            return len(self.timelines)

        def GetTimelineByIndex(self, index):
            return self.timelines[index - 1]

    timeline = Timeline("captain timeline")
    captain = Project("captain project", timeline)

    class Manager:
        def __init__(self):
            self.current = captain
            self.projects = {}

        def GetCurrentProject(self):
            return self.current

        def SaveProject(self):
            events.append(f"save:{self.current.GetName()}")
            return not self.current.GetName().startswith(
                eval_harness.SCRATCH_PREFIX)

        def GetProjectListInCurrentFolder(self):
            return list(self.projects)

        def CreateProject(self, name):
            events.append(f"create:{name}")
            project = Project(name)
            self.projects[name] = project
            self.current = project
            return project

        def LoadProject(self, name):
            events.append(f"load:{name}")
            self.current = self.projects.get(name, captain)
            return self.current

        def DeleteProject(self, name):
            events.append(f"delete:{name}")
            self.projects.pop(name, None)
            return True

    manager = Manager()

    class Resolve:
        def GetProjectManager(self):
            return manager

    @contextmanager
    def fake_lease(purpose, **kwargs):
        events.append(f"lease:{purpose}")
        yield None

    @contextmanager
    def fake_cursor_fence(project, selected, purpose):
        events.append(f"fence:{purpose}")
        project.current_timeline = selected
        yield None

    monkeypatch.setattr(resolve_lock, "resolve_lease", fake_lease)
    monkeypatch.setattr(resolve_lock, "cursor_fence", fake_cursor_fence)
    monkeypatch.setattr(marker_feedback, "connect_resolve",
                        lambda: Resolve())

    scratch = f"{eval_harness.SCRATCH_PREFIX}failed-start"
    with pytest.raises(RuntimeError, match="captain project and timeline "
                       "were restored"):
        eval_harness.resolve_bracket_start(scratch)

    assert manager.current is captain
    assert captain.current_timeline is timeline
    assert scratch not in manager.projects
    assert events.index("save:captain project") < events.index(
        f"create:{scratch}")
    assert f"delete:{scratch}" in events


def test_eval_bracket_refuses_unknown_captain_state_on_scratch_project(
        monkeypatch):
    """A leftover scratch project is never mistaken for the captain.

    Catches: the bracket recording an eval scratch name as the captain and
    later deleting the only open project without knowing what to restore.
    """
    from contextlib import contextmanager

    from library.tools import marker_feedback, resolve_lock

    scratch_current = f"{eval_harness.SCRATCH_PREFIX}orphan"

    class Project:
        def GetName(self):
            return scratch_current

    project = Project()

    class Manager:
        def GetCurrentProject(self):
            return project

        def SaveProject(self):
            raise AssertionError("the unknown scratch must not be saved")

        def CreateProject(self, name):
            raise AssertionError("no new scratch may be opened")

    manager = Manager()

    class Resolve:
        def GetProjectManager(self):
            return manager

    @contextmanager
    def fake_lease(purpose, **kwargs):
        yield None

    monkeypatch.setattr(resolve_lock, "resolve_lease", fake_lease)
    monkeypatch.setattr(marker_feedback, "connect_resolve",
                        lambda: Resolve())

    with pytest.raises(RuntimeError, match="captain's saved project and "
                       "timeline are unknown"):
        eval_harness.resolve_bracket_start(
            f"{eval_harness.SCRATCH_PREFIX}next")

    assert manager.GetCurrentProject() is project


def test_run_serializes_model_decisions_and_resolve_build(
        tmp_path, monkeypatch):
    """The shared mutex covers model decisions through readback.

    Catches: overlapping another lane's model run with the edit, or letting
    a render begin without serializing the edit that decides what it builds.
    """
    events = []
    out = tmp_path / "eval"
    base = tmp_path / "base"
    dest = out / "ST1.1" / "run"

    def clone(base_arg, dest_arg, batch, extra_rewrites=()):
        Path(dest_arg).mkdir(parents=True)
        return {"base": str(base), "dest": str(dest),
                "timeline": "EVAL_T1", "answers": str(dest / "eval_answers")}

    monkeypatch.setattr(eval_harness, "clone_base", clone)
    monkeypatch.setattr(eval_harness, "inject_request",
                        lambda *args: events.append("note") or "pull.json")
    monkeypatch.setattr(eval_harness, "prepare_edit_spec",
                        lambda *args, **kwargs: events.append("translate") or {
                            "status": "recorded", "routes": []})
    monkeypatch.setattr(eval_harness, "wait_for_quiet",
                        lambda: events.append("quiet"))
    monkeypatch.setattr(eval_harness, "run_pipeline_edit",
                        lambda *args, **kwargs: events.append("edit") or {
                            "run_status": "SUCCESS"})
    monkeypatch.setattr(eval_harness, "take_resolve_lock",
                        lambda *args: events.append("lock"))

    @contextmanager
    def resolve_lease(*args, **kwargs):
        events.append("resolve-lease")
        try:
            yield None
        finally:
            events.append("resolve-unlock")

    monkeypatch.setattr(eval_harness, "resolve_lease", resolve_lease)
    monkeypatch.setattr(eval_harness, "take_heavy_lock",
                        lambda *args: events.append("heavy-lock"))
    monkeypatch.setattr(eval_harness, "resolve_bracket_start",
                        lambda *args: events.append("save-and-open") or {
                            "project": "captain", "timeline": "master"})
    monkeypatch.setattr(eval_harness, "run_pipeline_render",
                        lambda *args: events.append("build") or {
                            "run_status": "SUCCESS"})
    monkeypatch.setattr(eval_harness, "_built_timeline_name",
                        lambda *args: "EVAL_T1")

    def readback(*args):
        events.append("readback")
        Path(args[2]).write_text("items\n", encoding="utf-8")

    monkeypatch.setattr(eval_harness, "readback_timeline", readback)
    monkeypatch.setattr(eval_harness, "find_export",
                        lambda *args: "export.mp4")
    monkeypatch.setattr(eval_harness, "resolve_bracket_end",
                        lambda *args: events.append("restore-and-delete") or {
                            "project_restored": True, "timeline_restored": True,
                            "scratch_deleted": True})
    monkeypatch.setattr(eval_harness, "release_resolve_lock",
                        lambda: events.append("unlock"))
    monkeypatch.setattr(eval_harness, "release_heavy_lock",
                        lambda: events.append("heavy-unlock"))
    monkeypatch.setattr(eval_harness, "build_measures",
                        lambda *args: {"hunks": []})

    request = eval_corpus.select(request_id="ST1.1")[0]
    eval_harness.run_request(request, str(base), str(out), "T1")
    assert events == [
        "note", "quiet", "heavy-lock", "translate", "edit",
        "heavy-unlock", "resolve-lease", "heavy-lock", "lock",
        "save-and-open", "build", "readback", "restore-and-delete",
        "unlock", "heavy-unlock", "resolve-unlock",
    ]


def test_run_releases_heavy_lock_when_edit_stage_fails(tmp_path, monkeypatch):
    """A refused model decision cannot strand the shared work lock.

    Catches: an exception in the edit replay leaving other lanes waiting on
    a heavy-work lock after this request has already stopped.
    """
    events = []
    out = tmp_path / "eval"
    base = tmp_path / "base"
    dest = out / "ST1.1" / "run"

    def clone(base_arg, dest_arg, batch, extra_rewrites=()):
        Path(dest_arg).mkdir(parents=True)
        return {"base": str(base), "dest": str(dest),
                "timeline": "EVAL_T1", "answers": str(dest / "answers")}

    def fail_edit(*args, **kwargs):
        events.append("edit")
        raise RuntimeError("test edit refusal")

    monkeypatch.setattr(eval_harness, "clone_base", clone)
    monkeypatch.setattr(eval_harness, "inject_request",
                        lambda *args: events.append("note") or "pull.json")
    monkeypatch.setattr(eval_harness, "prepare_edit_spec",
                        lambda *args, **kwargs: events.append("translate") or {
                            "status": "recorded", "routes": []})
    monkeypatch.setattr(eval_harness, "wait_for_quiet",
                        lambda: events.append("quiet"))
    monkeypatch.setattr(eval_harness, "take_heavy_lock",
                        lambda *args: events.append("heavy-lock"))
    monkeypatch.setattr(eval_harness, "run_pipeline_edit", fail_edit)
    monkeypatch.setattr(eval_harness, "release_heavy_lock",
                        lambda: events.append("heavy-unlock"))

    request = eval_corpus.select(request_id="ST1.1")[0]
    with pytest.raises(RuntimeError, match="test edit refusal"):
        eval_harness.run_request(request, str(base), str(out), "T1")

    assert events == ["note", "quiet", "heavy-lock", "translate", "edit",
                      "heavy-unlock"]


def test_eval_translation_records_typed_rows_before_pipeline_edit(
        tmp_path, monkeypatch):
    """The report-only runner uses the same ledger-backed translation.

    Catches: eval silently bypassing the new spec handshake and running
    each branch request through word matching instead.
    """
    from library.tools import edit_ledger, edit_spec, llm_handshake
    from library.tools.project_layout import ProjectLayout

    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    pull = tmp_path / "eval-pull.json"
    raw = {"source": "timeline_marker", "name": "eval request",
           "note": "isolate the host mic by 60 percent",
           "text": "isolate the host mic by 60 percent", "frame": None,
           "custom_data": {"eval_request_id": "ST1.1"}}
    payload = {"timeline": "EVAL_T1", "notes": [raw]}
    pull.write_text(json.dumps(payload), encoding="utf-8")

    original_prepare = edit_spec.prepare_request

    def prepare_and_answer(project_path, request_text, note_id="", **kwargs):
        request_id, request_path, linked_id = original_prepare(
            project_path, request_text, note_id=note_id, **kwargs)
        response = {
            "format": edit_spec.FORMAT,
            "request": request_text,
            "clauses": [{
                "id": "op-1", "text": request_text,
                "op": "voice_isolation", "op_source": "model",
                "status": "resolved",
                "anchor": {"kind": "reel"},
                "anchor_source": "requester",
                "values": {
                    "track": {"value": 1, "unit": "track index",
                              "stated_by": "model"},
                    "amount": {"value": 60, "unit": "percent",
                               "stated_by": "requester"},
                },
            }],
        }
        Path(llm_handshake.response_path(project_path, request_id)).write_text(
            json.dumps(response), encoding="utf-8")
        return request_id, request_path, linked_id

    monkeypatch.setattr(edit_spec, "prepare_request", prepare_and_answer)
    result = eval_harness.prepare_edit_spec(
        str(project), str(pull),
        {"id": "ST1.1", "text": raw["text"]})

    assert result["status"] == "recorded"
    assert result["routes"] == [{
        "op": "voice_isolation", "owner": "render",
        "ledger_action": "recorded"}]
    assert edit_ledger.load_rows(str(project))[0]["source_note_id"] == \
        result["note_id"]


def test_eval_records_clarification_and_skips_guessing_a_build(tmp_path):
    """An editor question is scored as asking; eval never builds around it.

    Catches: the benchmark treating a correct referent question as a failed
    pipeline or manufacturing a run readback for an edit it did not make.
    """
    out = tmp_path / "eval" / "RT1.2"
    out.mkdir(parents=True)
    base_run = out.parent / "BASE" / "run"
    base_run.mkdir(parents=True)
    (base_run / "pipeline_data.json").write_text("{}", encoding="utf-8")
    base_readback = out.parent / "BASE" / "readback.txt"
    base_readback.write_text("same timeline\n", encoding="utf-8")
    question = {"clause_id": "op-1", "question": "Which shot?"}
    ledger = {
        "clone": {"base": str(base_run), "dest": str(out / "run"),
                  "timeline": "EVAL_T1"},
        "edit_spec": {"status": "needs_clarification",
                      "questions": [question]},
    }

    eval_harness._write_clarification_result(
        {"id": "RT1.2"}, out, ledger, str(base_readback))

    judgement = json.loads(
        (out / "judgement.json").read_text(encoding="utf-8"))
    measures = json.loads(
        (out / "measures.json").read_text(encoding="utf-8"))
    assert ledger["execution_status"] == "NEEDS_EDITOR_CLARIFICATION"
    assert judgement["clarification_questions"] == [question]
    assert judgement["hunks"] == []
    assert measures["hunks"] == []
    assert measures["frames"]["measured"] is False


def test_a_translated_owner_outside_the_chain_is_replanned():
    """A typed music request re-runs music_selection in the edit stage.

    Catches: the translation routing "swap the track" to music_selection
    while the eval's rerun chain skipped that step, so a correctly
    translated request was scored as not followed.
    """
    ledger = {"edit_spec": {"status": "recorded", "routes": [
        {"op": "music_selection", "owner": "music_selection"},
        {"op": "transition", "owner": "plan_transitions"},
        {"op": "voice_isolation", "owner": "render"},
        {"op": "redraw_closer", "owner": "select_reels"},
    ]}}
    assert eval_harness._translated_owner_reruns(ledger) == (
        "music_selection", "select_reels")
    assert eval_harness._translated_owner_reruns(
        {"edit_spec": {"status": "needs_clarification"}}) == ()
