"""Lane D (cascade report section 5): the cascade engine over the skips.

D1: `cascade_graph.py` holds the section 3.1 edge table plus the
per-reel `derived_registry.json` generalising `build_signatures` and
the caption hashes - the old stores keep being written (Lane E
retirements are out of scope).
D2: `mark_dirty` is wired into the three existing skip paths
(subtitle `content_key`, MG `find_by_key`, `reel_rebuild_need`
LEAVE_ALONE) - no new skip mechanism, only the shared walker.
D3: scoped QA from dirty spans (`run_scoped_render_qa` + hearing
drift re-check) is the default verify after any partial rebuild.

Every fixture is built under ``tmp_path``; no test reaches a real
project, Resolve, or ffmpeg. Each test names the divergence it would
leave.
"""

import inspect
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from library.tools import cascade_graph as cascade
from library.tools import plan_provenance as provenance


def _review(tmp_path: Path) -> Path:
    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    plan = review / "reel_proposals_v2.json"
    plan.write_text(json.dumps({"moments": []}), encoding="utf-8")
    return review


def test_registry_generalises_the_three_stores_without_retiring_them(tmp_path):
    """D1: one file per reel carries derivation + carried + both
    bindings, while `plan_provenance.json` keeps all three - a Lane E
    retirement smuggled into this lane would delete the baselines the
    current verifier grades against."""
    review = _review(tmp_path)
    plan = str(review / "reel_proposals_v2.json")
    provenance.write_provenance(
        str(review), plan, ["Reel 01 (staging)"],
        caption_hashes={"Reel 01 (staging)": "v1:abc"},
        footage_binding_hashes={"Reel 01 (staging)": "v1:def"},
        build_signatures={"Reel 01 (staging)":
                          {"derivation": "d" * 64, "carried": ""}})
    cascade.update_registry_from_build(
        str(review), "Reel 01 (staging)", derivation="d" * 64,
        caption_binding="v1:abc", footage_binding="v1:def")

    doc = provenance.read_provenance(str(review))
    assert doc["build_signatures"]["Reel 01 (staging)"]["derivation"] == \
        "d" * 64
    assert doc["caption_hashes"]["Reel 01 (staging)"] == "v1:abc"
    assert doc["footage_binding_hashes"]["Reel 01 (staging)"] == "v1:def"

    registry = cascade.read_registry(str(review), "Reel 01 (staging)")
    assert registry["hashes"]["reel derivation"] == "d" * 64
    assert registry["hashes"][
        "words+pixels+renderer -> caption binding"] == "v1:abc"
    assert registry["hashes"]["picture-under-words"] == "v1:def"


def test_registry_follows_promotion_and_refusal(tmp_path):
    """D1 lifecycle: the registry file renames staging -> final with
    the provenance record, closes its carried half at promotion, and
    leaves nothing behind on the gate-fail path - a registry that
    does not follow promotion would refuse every later skip against
    a stale name."""
    review = _review(tmp_path)
    cascade.update_registry_from_build(
        str(review), "Reel 01 (staging)", derivation="d" * 64,
        caption_binding="v1:abc", footage_binding="v1:def")
    plan = str(review / "reel_proposals_v2.json")
    provenance.write_provenance(
        str(review), plan, ["Reel 01 (staging)"],
        build_signatures={"Reel 01 (staging)":
                          {"derivation": "d" * 64, "carried": ""}})

    provenance.rename_reel_entries(
        str(review), {"Reel 01 (staging)": "Reel 01 - slug"})
    assert cascade.read_registry(str(review), "Reel 01 (staging)") is None
    moved = cascade.read_registry(str(review), "Reel 01 - slug")
    assert moved["hashes"]["reel derivation"] == "d" * 64

    provenance.record_carried_digests(
        str(review), {"Reel 01 - slug": "c" * 64})
    closed = cascade.read_registry(str(review), "Reel 01 - slug")
    assert closed["hashes"]["timeline as-built"] == "c" * 64

    provenance.drop_reel_entries(str(review), ["Reel 01 - slug"])
    cascade_path = Path(cascade.registry_path(
        str(review), "Reel 01 - slug"))
    # The drop hook removes the file; if a future edit weakens it to
    # a no-op, the stale hashes must at least not read as a record.
    assert (not cascade_path.is_file()
            or cascade.read_registry(str(review), "Reel 01 - slug") is None)


def test_the_three_skip_paths_call_the_shared_walker():
    """D2: no second spelling of the dirty test. A skip path that
    stops calling the walker silently forks the cascade - this pins
    the call sites, and the unit suite pins that the walker refuses
    what the inline compare used to refuse."""
    step_405 = (REPO / "library/steps/step_4_05_render_subtitles"
                / "step.py").read_text(encoding="utf-8")
    assert "cascade_graph" in step_405
    assert "caption_reuse_allowed" in step_405

    mg_bridge = (REPO / "library/steps/step_4_06_render_motion_graphics"
                 / "post_bridge.py").read_text(encoding="utf-8")
    assert "cascade_graph" in mg_bridge
    assert "overlay_reuse_allowed" in mg_bridge

    build = (REPO / "library/tools/reel_build.py").read_text(
        encoding="utf-8")
    assert "gate_rebuild_decision" in build

    from library.tools import reel_rebuild_need as need
    decision = need.decide("Reel 01", "d" * 64, "c" * 64,
                           {"derivation": "d" * 64, "carried": "c" * 64})
    assert decision.leave_alone
    registry = {"format": cascade.REGISTRY_FORMAT, "reel": "Reel 01",
                "hashes": {"reel derivation": "d" * 64}}
    assert cascade.gate_rebuild_decision(
        decision, fresh_derivation="d" * 64,
        recorded={"derivation": "d" * 64, "carried": "c" * 64},
        registry=registry).leave_alone


def test_scoped_qa_is_the_default_verify_and_reports_only(tmp_path):
    """D3: after a partial rebuild every rebuilt reel is either scoped
    or deferred with the reason said - a reel that reads as verified
    with neither a scoped pass nor the full gate standing is the gate
    that cannot fail wearing a new name."""
    from library.steps.step_7_02_verify_reels import step as verify_step
    assert "scoped_verify_partial_build" in inspect.getsource(
        verify_step.verify_reels)
    assert "cascade_scoped_qa" in inspect.getsource(verify_step.verify_reels)

    exports = tmp_path / "exports"
    exports.mkdir()
    video = exports / "Reel 01 - slug.mp4"
    video.write_bytes(b"not a real render")
    # The drift runs arrive in memory - the record file stays
    # write-only, and no gate opens it
    # (`test_no_gate_reads_the_hearing_record`).
    runs = [{"planned_start": 10.5, "planned_end": 11.0, "text": "moved"},
            {"planned_start": 40.0, "planned_end": 41.0, "text": "kept"}]
    dirty = {"whole_reel": False, "whole_reel_reason": "",
             "dirty_domains": ["picture"],
             "dirty_spans": [{"domain": "picture",
                              "start_seconds": 10.0, "end_seconds": 12.0,
                              "core_start_seconds": 10.0,
                              "core_end_seconds": 12.0}]}

    report = cascade.scoped_verify_partial_build(
        str(tmp_path), ["Reel 01 - slug", "Reel 02 - slug"],
        dirty_by_reel={"Reel 01 - slug": dirty},
        hearing_runs_by_reel={"Reel 01 - slug": runs},
        exports_dir=str(exports))
    assert report["checked"] == ["Reel 01 - slug"]
    assert {row["reel"] for row in report["deferred"]} == {"Reel 02 - slug"}
    scoped = report["findings"][0]["report"]
    assert scoped["drift_total"] == 2
    assert [row["text"] for row in scoped["drift_in_scope"]] == ["moved"]
    assert "report-only" in scoped["detail"]
