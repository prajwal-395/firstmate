"""Single-reel lanes keep their own planning and verification records.

All project data is under ``tmp_path``. The planning calls use the real
``write_reel_asks_for_project`` implementation with Resolve getters stubbed;
the process tests exercise the same locked writers used by the real runner.
"""

from __future__ import annotations

import json
import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from library.tools import reel_build
from library.tools.reel_proposal import (
    Approval,
    ReelMoment,
    proposal_path,
    write_proposal,
)


def _record_reel_lane(project: str, reel: int, barrier) -> None:
    """Process target for simultaneous pipeline state read/merge/writes."""
    from library.processes.reels.run_reels import record_project_output

    barrier.wait(timeout=20)
    name = f"Reel {reel:02d} - lane-{reel}"
    record_project_output(
        project, "build_reels",
        {
            "reel_ask": {
                "reel_asks": [{"number": reel, "reel": name,
                               "request": f"ask-{reel}"},
                              {"number": 99, "reel": "Reel 99 - foreign",
                               "request": "must be filtered"}],
            },
            "reel_build": {
                "reels_requested": [reel],
                "timelines_built": [f"{name} (staging)",
                                    "Reel 99 - foreign (staging)"],
                "staged_timelines": {name: f"{name} (staging)"},
                "caption_hashes": {name: f"hash-{reel}",
                                   "Reel 99 - foreign": "must be filtered"},
                "track_plans": {name: {"source": reel},
                                "Reel 99 - foreign": {"source": 99}},
                "awaiting_model_answers": {
                    "reels": [{"reel": name, "layers": ["semantic"]}],
                    "outstanding_answers": 1,
                },
                "proof_scope": {"stills": [name]},
                "pending_promotions": [{
                    "staging": f"{name} (staging)", "awaiting": name}],
                "coherence_summary": {"status": "skipped"},
            },
        },
        only_reels=[reel],
    )


def _write_conformance_lane(path: str, reel: int, barrier) -> None:
    """Process target for simultaneous scoped report replacement."""
    from library.tools.reel_conformance_verifier import _write_scoped_report

    name = f"Reel {reel:02d} (staging)"
    row = {"reel_name": name, "reel_number": reel, "errors": 0,
           "warnings": 0, "findings": []}
    incoming = {
        "project": "fixture",
        "master_timeline": "Master",
        "summary": {"reels_checked": 1, "total_errors": 0,
                    "total_warnings": 0, "passed": True},
        "by_class": {},
        "reels": [row],
        "read_only_proof": {
            "all_identical": True,
            "timelines_checked": 2,
            "timelines": {
                "Master": {"identical": True},
                name: {"identical": True},
            },
        },
        "motion_graphics_tightness": {"measured": "lane-local overwrite"},
        "quality_bar": {
            "verdicts": [{"reel": reel, "verdict": "pass"}],
            "not_read": [], "judged": True,
        },
    }
    barrier.wait(timeout=20)
    _write_scoped_report(path, incoming, [name])


def _moment(number: int) -> ReelMoment:
    return ReelMoment(
        number=number,
        slug=f"lane-{number}",
        reason="a complete exchange",
        timeline_start=number * 10.0,
        timeline_end=number * 10.0 + 8.0,
        approval=Approval.APPROVED,
    )


def _ask_project(root: Path) -> tuple[str, list[ReelMoment]]:
    root.mkdir(parents=True)
    (root / "project.yaml").write_text(
        "name: fixture\nresolve:\n"
        "  project_name: Fixture\n"
        "  timeline_name: Master\n", encoding="utf-8")
    moments = [_moment(21), _moment(25)]
    write_proposal(proposal_path(root), moments, {"derived_from": {}})
    return str(root), moments


def test_concurrent_only_reel_asks_run_only_the_selected_reel(
        tmp_path, monkeypatch):
    """The real reel.ask step snaps, derives and asks for N only."""
    project, _moments = _ask_project(tmp_path / "project")
    master = SimpleNamespace(GetName=lambda: "Master")

    class FakeProject:
        def GetName(self):
            return "Fixture"

        def GetTimelineCount(self):
            return 1

        def GetTimelineByIndex(self, index):
            assert index == 1
            return master

    monkeypatch.setattr(reel_build, "_connect_resolve_project",
                        lambda name: FakeProject())
    monkeypatch.setattr(
        "library.tools.timeline_ingest.snapshot_timeline",
        lambda timeline, project_name: SimpleNamespace(clips=[]))
    monkeypatch.setattr(reel_build, "reel_resolution",
                        lambda folder: (1080, 1920))
    monkeypatch.setattr(
        "library.tools.reel_look.resolve_look",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(reel_build, "declared_cards", lambda folder: {})
    monkeypatch.setattr(
        "library.tools.transcript_corrections.keep_exclusions",
        lambda folder: [])
    monkeypatch.setattr(
        "library.tools.transcript_corrections.keep_insistences",
        lambda folder: [])
    monkeypatch.setattr(
        reel_build, "moment_cuts_and_insistences",
        lambda *args, **kwargs: ([], []))

    snapped = []
    derived = []
    asked = []

    def snap(moment, transcript, **kwargs):
        snapped.append(int(moment.number))
        return moment, []

    def derive(moment, *args, **kwargs):
        derived.append(int(moment.number))
        time.sleep(0.01)
        return [], [], None

    def write(moment, *args, **kwargs):
        asked.append(int(moment.number))
        return {"reel_semantic": "semantic", "reel_span": "span",
                "reel_motion": "motion"}

    monkeypatch.setattr("library.tools.reel_proposal.snap_moment_to_speech",
                        snap)
    monkeypatch.setattr(reel_build, "derive_reel_ranges_and_cards", derive)
    monkeypatch.setattr(reel_build, "write_visual_asks", write)

    from library.tools import reel_ledger
    monkeypatch.setattr(reel_ledger, "stored_windows",
                        lambda moment: ((moment.timeline_start,
                                         moment.timeline_end), None))
    monkeypatch.setattr(reel_ledger, "audit_ranges", lambda **kwargs: {
        "ranges": [], "reel": kwargs["final"]})

    from library.tools.operations import load_step_module
    step = load_step_module("step_7_01_build_reels", "step.py")

    def plan(number):
        return step.ask_reels({
            "project_folder": project,
            "timeline_transcript": {"segments": []},
            "only_reels": [number],
        })["reel_ask"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(plan, (21, 25)))

    assert sorted(snapped) == [21, 25]
    assert sorted(derived) == [21, 25]
    assert sorted(asked) == [21, 25]
    assert [result["reel_asks"][0]["number"] for result in results] == [21, 25]
    ledger = json.loads(reel_ledger.ledger_path(project).read_text(
        encoding="utf-8"))
    assert set(ledger["reels"]) == {"Reel 21 - lane-21", "Reel 25 - lane-25"}


def test_single_reel_build_suppresses_sibling_override_notices(
        tmp_path, monkeypatch):
    from library.tools import captain_edits

    edit = {"kind": "transform_override", "anchor_phrase": "a phrase",
            "property": "Pan", "value": -5}
    sibling_edit = {**edit, "reel": "Reel 21 - sibling"}
    sibling = {**edit, "scope": "reel",
               "reason": "override belongs to Reel 21"}
    lost = {**edit, "scope": "transcript",
            "reason": "anchor no longer occurs"}
    notices = []
    lost_inputs = []
    matcher_edits = []
    monkeypatch.setattr(captain_edits, "load_edits",
                        lambda project: [sibling_edit, edit])

    def match(places, transcript, edits, **kwargs):
        matcher_edits.extend(edits)
        return [], [sibling, lost]

    monkeypatch.setattr(captain_edits, "match_transform_overrides", match)
    monkeypatch.setattr(captain_edits, "report_stale",
                        lambda rows: notices.append(list(rows)))

    def lost_overrides(rows):
        lost_inputs.append(list(rows))
        return [row for row in rows if row.get("scope") == "transcript"]

    monkeypatch.setattr(captain_edits, "lost_overrides", lost_overrides)
    reel_build.apply_transform_overrides(
        "Reel 25 - target", None, {}, [], None, {"segments": []},
        str(tmp_path), 1080, 1920, report_sibling_stale=False)

    assert matcher_edits == [edit]
    assert notices == [[lost]]
    assert lost_inputs == [[lost]]


def test_pending_notice_identifies_unowned_sibling_staging(tmp_path):
    from library.tools import staging_holds

    project = str(tmp_path / "project")
    staging_holds.take_hold(
        project, "Reel 21 - sibling (rebuild staging)",
        awaiting="Reel 21 - sibling",
        reason="other lane", taken_by="lane-21")
    staging_holds.take_hold(
        project, "Reel 25 - target (rebuild staging)",
        awaiting="Reel 25 - target",
        reason="this lane", taken_by="lane-25")

    notice = staging_holds.report_pending(
        project,
        owned_staging_names={"Reel 25 - target (rebuild staging)"})

    assert ("Reel 21 - sibling (rebuild staging)" in notice
            and "another reel's staging; this build does not own it"
            in notice)
    target_line = next(line for line in notice.splitlines()
                       if "Reel 25 - target (rebuild staging)" in line)
    assert "another reel's staging" not in target_line

    stale = staging_holds.report_pending(
        project, timeline_names=[],
        owned_staging_names={"Reel 25 - target (rebuild staging)"})
    stale_line = next(line for line in stale.splitlines()
                      if "Reel 21 - sibling (rebuild staging)" in line)
    assert "another reel's staging; this build does not own it" in stale_line


def test_concurrent_runner_records_preserve_each_reels_entries(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state_path = project / "pipeline_data.json"
    seed = {
        "project_folder": str(project),
        "step_outputs": {
            "build_reels": {
                "reel_ask": {"reel_asks": [
                    {"number": 3, "reel": "Reel 03", "request": "old"}]},
                "reel_build": {
                    "reels_requested": [3],
                    "timelines_built": ["Reel 03 (staging)"],
                    "staged_timelines": {"Reel 03": "Reel 03 (staging)"},
                    "caption_hashes": {"Reel 03": "hash-3"},
                    "track_plans": {"Reel 03": {"source": 3}},
                    "awaiting_model_answers": {
                        "reels": [{"reel": "Reel 03", "layers": ["span"]}],
                        "outstanding_answers": 1,
                    },
                    "proof_scope": {"stills": ["Reel 03"]},
                    "pending_promotions": [
                        {"staging": "Reel 03 (staging)",
                         "awaiting": "Reel 03"}],
                    "coherence_summary": {"status": "whole-project",
                                          "owned_total": 8},
                },
            },
        },
    }
    state_path.write_text(json.dumps(seed), encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    workers = [
        context.Process(target=_record_reel_lane,
                        args=(str(project), reel, barrier))
        for reel in (21, 25)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert [worker.exitcode for worker in workers] == [0, 0]

    stored = json.loads(state_path.read_text(encoding="utf-8"))
    node = stored["step_outputs"]["build_reels"]
    assert {row["number"] for row in node["reel_ask"]["reel_asks"]} == {
        3, 21, 25}
    assert 99 not in {
        row["number"] for row in node["reel_ask"]["reel_asks"]}
    build = node["reel_build"]
    assert set(build["reels_requested"]) == {3, 21, 25}
    assert {"Reel 03 (staging)", "Reel 21 - lane-21 (staging)",
            "Reel 25 - lane-25 (staging)"} == set(build["timelines_built"])
    assert set(build["caption_hashes"]) == {
        "Reel 03", "Reel 21 - lane-21", "Reel 25 - lane-25"}
    assert set(build["track_plans"]) == set(build["caption_hashes"])
    assert "Reel 99 - foreign" not in build["caption_hashes"]
    assert {row["reel"] for row in
            build["awaiting_model_answers"]["reels"]} == {
        "Reel 03", "Reel 21 - lane-21", "Reel 25 - lane-25"}
    assert build["awaiting_model_answers"]["outstanding_answers"] == 3
    assert set(build["proof_scope"]["stills"]) == {
        "Reel 03", "Reel 21 - lane-21", "Reel 25 - lane-25"}
    assert {row["staging"] for row in build["pending_promotions"]} == {
        "Reel 03 (staging)", "Reel 21 - lane-21 (staging)",
        "Reel 25 - lane-25 (staging)"}
    assert build["coherence_summary"] == {
        "status": "whole-project", "owned_total": 8}


def test_verify_node_filters_aggregated_build_record_to_its_lane(monkeypatch):
    from library.tools.operations import load_step_module

    module = load_step_module("step_7_02_verify_reels", "step.py")
    final_21 = "Reel 21 - lane-21"
    final_25 = "Reel 25 - lane-25"
    staged_21 = f"{final_21} (staging)"
    staged_25 = f"{final_25} (staging)"
    data = {
        "project_folder": "/tmp/project",
        "only_reels": [25],
        "timeline_transcript": {"segments": []},
        "reel_build": {
            "timelines_built": [staged_21, staged_25],
            "staged_timelines": {final_21: staged_21,
                                 final_25: staged_25},
            "track_plans": {final_21: {"reel": 21},
                            final_25: {"reel": 25}},
            "allow_drops": {final_21: [], final_25: []},
            "supersede": [final_21, final_25],
            "retain": [final_21, final_25],
            "resolve_project_name": "Fixture",
            "master_timeline_name": "Master",
            "plan_path": "/tmp/project/plan.json",
        },
    }

    import unittest.mock

    with (unittest.mock.patch.object(module, "record_uncarried_notes",
                                      return_value=None),
          unittest.mock.patch(
              "library.tools.reel_build.verify_built_reels") as gate,
          unittest.mock.patch(
              "library.tools.reel_build.promote_staged_reels",
              return_value={"promoted": [final_25], "organised": None}
          ) as promote,
          unittest.mock.patch(
              "library.tools.reel_build.sweep_all_reels_informational"),
          unittest.mock.patch(
              "library.tools.timeline_transcript.transcript_path",
              return_value="/tmp/project/transcript.json")):
        result = module.verify_reels(data)

    assert gate.call_args.kwargs["only_reels"] == [staged_25]
    assert promote.call_args.args[3] == {final_25: staged_25}
    assert promote.call_args.kwargs["supersede"] == [final_25]
    assert promote.call_args.kwargs["retain"] == [final_25]
    assert result["reel_verification"]["timelines_verified"] == [final_25]


def test_concurrent_scoped_conformance_writes_preserve_sibling_reports(
        tmp_path):
    path = tmp_path / "review" / "conformance_report.json"
    path.parent.mkdir(parents=True)
    old_rows = [
        {"reel_name": f"Reel {number:02d} - final", "reel_number": number,
         "errors": 0, "warnings": 0, "findings": []}
        for number in (3, 21, 25)
    ]
    seed = {
        "project": "fixture", "master_timeline": "Master",
        "motion_graphics_tightness": {"measured": "project-wide"},
        "reels": old_rows,
        "read_only_proof": {
            "all_identical": True, "timelines_checked": 4,
            "timelines": {
                "Master": {"identical": True},
                **{row["reel_name"]: {"identical": True}
                   for row in old_rows},
            },
        },
        "quality_bar": {
            "verdicts": [{"reel": number, "verdict": "pass"}
                         for number in (3, 21, 25)],
            "not_read": [],
        },
    }
    path.write_text(json.dumps(seed), encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    workers = [
        context.Process(target=_write_conformance_lane,
                        args=(str(path), reel, barrier))
        for reel in (21, 25)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert [worker.exitcode for worker in workers] == [0, 0]

    report = json.loads(path.read_text(encoding="utf-8"))
    assert {row["reel_name"] for row in report["reels"]} == {
        "Reel 03 - final", "Reel 21 (staging)", "Reel 25 (staging)"}
    assert set(report["read_only_proof"]["timelines"]) == {
        "Master", "Reel 03 - final", "Reel 21 (staging)",
        "Reel 25 (staging)"}
    assert {row["reel"] for row in report["quality_bar"]["verdicts"]} == {
        3, 21, 25}
    assert report["motion_graphics_tightness"] == {
        "measured": "project-wide"}
