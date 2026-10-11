"""Unit 9 qualification: FREE failure-injection and planning tests.

No Resolve, no broker, no engine CLI, no real project: every case runs
on the committed synthetic fixture (copied under tmp_path where the
stage writes) or on inline model dicts. The live half (engine CLIs +
``resolve.qualification_session``) is proved by the recorded
``ren qualification pm-e2e`` run, not here.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import project_model
from library.tools import project_model_qualification as pm9

ROOT = Path(__file__).resolve().parents[3]
E2E_MODEL = ROOT / "tests/fixtures/project_model_e2e/expected-project-model.json"

FFMPEG = shutil.which("ffmpeg")

STRONG_LOOK_YAML = """\
name: pm9-decl
slug: pm9-decl
status: draft
source:
  language: en
style:
  series_look:
    name: strong-grade
    cdl:
      slope: [1.2, 1.2, 1.2]
      offset: [0.1, 0.1, 0.1]
      power: [1.0, 1.0, 1.0]
      saturation: 1.5
    contrast: 0.3
"""


def _tiny_mp4(path: Path, frames: int = 8) -> Path:
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i",
         f"testsrc=size=64x112:rate=24:duration={frames / 24}",
         "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr[:300]
    return path


def _declarations(tmp_path: Path, yaml_text: str = STRONG_LOOK_YAML) -> str:
    decl = tmp_path / "decl"
    decl.mkdir(exist_ok=True)
    (decl / "project.yaml").write_text(yaml_text, encoding="utf-8")
    return str(decl)


def _model() -> dict:
    return json.loads(E2E_MODEL.read_text(encoding="utf-8"))


def _work_with_model(tmp_path: Path) -> Path:
    work = tmp_path / "pm9"
    work.mkdir()
    (work / "model.ingested.json").write_text(
        json.dumps(_model()), encoding="utf-8")
    return work


# ── Refusal names ───────────────────────────────────────────────────

@pytest.mark.parametrize(("cls", "code"), [
    (pm9.StaleRevision, "REN_PM9_STALE_REVISION"),
    (pm9.UnknownSchema, "REN_PM9_UNKNOWN_SCHEMA"),
    (pm9.AmbiguousMap, "REN_PM9_AMBIGUOUS_MAP"),
    (pm9.MissingIdentity, "REN_PM9_MISSING_IDENTITY"),
    (pm9.UnsupportedEffect, "REN_PM9_UNSUPPORTED_EFFECT"),
])
def test_each_injection_refusal_carries_its_own_code(cls, code):
    assert cls.CODE == code
    assert issubclass(cls, pm9.Pm9Refused)


def test_unknown_schema_refuses_by_name():
    with pytest.raises(pm9.UnknownSchema) as caught:
        pm9.inject_unknown_schema(_model())
    assert caught.value.code == "REN_PM9_UNKNOWN_SCHEMA"


def test_ambiguous_map_refuses_by_name():
    with pytest.raises(pm9.AmbiguousMap) as caught:
        pm9.inject_ambiguous_map()
    assert caught.value.code == "REN_PM9_AMBIGUOUS_MAP"


def test_missing_identity_refuses_by_name(tmp_path):
    work = _work_with_model(tmp_path)
    model = json.loads((work / "model.ingested.json").read_text(encoding="utf-8"))
    with pytest.raises(pm9.MissingIdentity) as caught:
        pm9.inject_missing_identity(model)
    assert caught.value.code == "REN_PM9_MISSING_IDENTITY"
    # And the stage wrapper records the named refusal, not a pass.
    outcomes = pm9.stage_failure_injection(work)
    assert outcomes["missing_identity"]["refused_as"] == "REN_PM9_MISSING_IDENTITY"


def test_unsupported_effect_refuses_by_name():
    with pytest.raises(pm9.UnsupportedEffect) as caught:
        pm9.inject_unsupported_effect(_model())
    assert caught.value.code == "REN_PM9_UNSUPPORTED_EFFECT"


def test_stale_revision_names_the_upstream_refusal(tmp_path):
    work = _work_with_model(tmp_path)
    model = json.loads((work / "model.ingested.json").read_text(encoding="utf-8"))
    with pytest.raises(pm9.StaleRevision) as caught:
        pm9.inject_stale_revision(model)
    assert caught.value.code == "REN_PM9_STALE_REVISION"
    outcomes = pm9.stage_failure_injection(work)
    assert outcomes["stale_revision"]["refused_as"] == "REN_PM9_STALE_REVISION"


def test_all_five_injections_refuse_by_name(tmp_path):
    work = _work_with_model(tmp_path)
    outcomes = pm9.stage_failure_injection(work)
    assert set(outcomes) == {"stale_revision", "unknown_schema", "ambiguous_map",
                             "missing_identity", "unsupported_effect"}
    for name, outcome in outcomes.items():
        assert outcome["refused_as"] == f"REN_PM9_{name.upper()}", name


# ── Identity and ingest stages (FREE) ───────────────────────────────

def test_ingest_verifies_fixture_digests_into_tmp_only(tmp_path):
    work = tmp_path / "pm9"
    work.mkdir()
    evidence = pm9.stage_ingest(work)
    assert evidence["model_id"] == "pm-e2e-fixture"
    assert evidence["schema_version"] == 1
    assert len(evidence["assets"]) == 3
    assert all(entry["digest"].startswith("sha256:") for entry in evidence["assets"])
    assert (work / "model.ingested.json").is_file()


def test_identity_resolves_both_people_to_rendered_ranges(tmp_path):
    work = tmp_path / "pm9"
    work.mkdir()
    pm9.stage_ingest(work)
    resolved = pm9.stage_identity(work)
    assert set(resolved) == {"person-ada", "person-ben"}
    for person in resolved.values():
        assert {"face", "speech"} <= set(person["modalities"])
        assert person["masks"] and person["a_roll"]
        assert person["rendered_ranges"]


# ── Animated track-matte mask (FREE: rasterize + project, no CLI) ──

def _anim_model():
    import copy
    model = _model()
    moved = next(m for m in model["masks"] if m["id"] == "mask-ada")
    moved = copy.deepcopy(moved)
    moved["id"] = "mask-ada-anim"
    moved["representation"]["keyframes"][1]["bbox"] = [0.45, 0.15, 0.25, 0.60]
    return model, moved


def test_derived_person_mask_is_genuinely_animated():
    from library.tools.project_model_effectcraft import _mask_is_static
    model, moved = _anim_model()
    ada = next(m for m in model["masks"] if m["id"] == "mask-ada")
    assert _mask_is_static(ada) is True
    assert _mask_is_static(moved) is False


def test_animated_mask_rasterizes_and_takes_the_matte_path(tmp_path):
    from library.tools.project_model_effectcraft import (
        CompSpec,
        RenderSpec,
        project_to_request,
        write_matte_sequence,
    )
    model, moved = _anim_model()
    anim = dict(model)
    anim["masks"] = [moved]
    anim["clip_uses"] = [u for u in model["clip_uses"] if u["id"] == "use-ada"]
    anim["effects"] = [{"id": "effect-blur-anim",
                        "class_id": "blur.gaussian",
                        "target_id": "mask-ada-anim",
                        "interval": {"clock_id": "clock-ada",
                                     "start": 0, "end": 48},
                        "parameters": {"blurriness": 2.0}, "keyframes": []}]
    comp = CompSpec(name="PM9Anim", width=160, height=90, frame_rate=12.0,
                    duration_seconds=2.0)
    sequence = write_matte_sequence(moved, anim, comp, tmp_path,
                                    stem="anim-matte")
    assert len(sequence.files) == comp.expected_frames() == 24
    assert all(Path(p).is_file() for p in sequence.files)
    projection = project_to_request(
        anim, comp, [RenderSpec(format="rgba_png_sequence",
                                output="pm9anim_#####.png")],
        media_root="/tmp", project_id="pm9-effectcraft-anim",
        mattes={moved["id"]: sequence})
    ops = [op["op"] for op in projection.request["operations"]]
    assert "set_track_matte" in ops
    assert "add_mask" not in ops
    assert projection.losses == []


# ── Moves stage (FREE: mint + project + keyframe-op asserts, no CLI) ──

def test_moves_stage_mints_one_effect_per_class_with_keyframe_ops():
    """The pm-e2e moves stage's mint+project path, without the CLI.

    Shares ``mint_and_project_moves`` with the live stage: one minted
    ``move.*`` effect per plan_vfx toolkit class, each carrying its
    clip use's timeline interval and keying its expected transform
    path. The live run additionally renders each record.
    """
    from library.tools.project_model_effectcraft_moves import (
        MOVE_CLASS_IDS,
    )
    records = pm9.mint_and_project_moves(_model(), media_root="/tmp")
    assert ([record["effect"]["class_id"] for record in records]
            == list(MOVE_CLASS_IDS))
    for record in records:
        effect = record["effect"]
        assert effect["id"] == f"move-{record['entry']['vfx_id']}"
        assert effect["target_id"] in {
            use["id"] for use in _model()["clip_uses"]}
        use = next(use for use in _model()["clip_uses"]
                   if use["id"] == effect["target_id"])
        assert effect["interval"] == use["timeline_interval"]
        assert effect["keyframes"] == []
        assert record["keyed"], effect["class_id"]
        want = ("transform/position"
                if effect["class_id"] == "move.screen_shake"
                else "transform/scale")
        assert want in {op["path"] for op in record["keyed"]}
    # The drift recovered the easing the bridge strips for Fusion.
    drift = next(record for record in records
                 if record["effect"]["class_id"] == "move.slow_zoom_in")
    assert drift["effect"]["parameters"]["zoom_easing"] == "Sine"
    # The punch kept the bridge's derived frame counts, not the plan's
    # seconds.
    punch = next(record for record in records
                 if record["effect"]["class_id"] == "move.zoom_emphasis")
    assert punch["effect"]["parameters"] == {
        "zoom_start": 1.0, "zoom_mid": 1.08, "zoom_end": 1.0,
        "zoom_in_duration_frames": 3,
        "zoom_release_offset_frames": 6,
        "zoom_out_duration_frames": 3}


# ── Swap-back plan stage (FREE) ─────────────────────────────────────

def test_swapback_plan_remaps_keys_inside_the_trimmed_render(tmp_path):
    work = tmp_path / "pm9"
    work.mkdir()
    filmcraft = {"measured_start_frame": 12,
                 "measured_timeline_frames": 36,
                 "render": {"path": "/tmp/pm9-ada-trimmed.mp4", "frames": 36},
                 "frame_map": {
                     "id": "map-pm9-filmcraft", "from_clock": "clock-project",
                     "to_clock": "clock-pm9-render",
                     "segments": [{
                         "from": {"clock_id": "clock-project",
                                  "start": 12, "end": 48},
                         "to": {"clock_id": "clock-pm9-render",
                                "start": 0, "end": 36},
                         "mapping_kind": "one_to_one",
                         "rate": {"numerator": 1, "denominator": 1},
                         "source_clip_use": "use-ada"}]}}
    evidence = pm9.stage_swapback_plan(work, filmcraft)
    # The head cut shifts every kept key by -12: an identity map fails.
    assert [(m["from_frame"], m["to_frame"]) for m in evidence["key_moves"]] == [
        (12, 0), (30, 18), (47, 35)]
    assert all(m["to_frame"] != m["from_frame"]
               for m in evidence["key_moves"])
    assert evidence["grade_ranges"] == [
        {"clock_id": "clock-pm9-trimmed", "start": 0, "end": 36}]
    assert "clip.replace" in evidence["patch_operations"]
    assert (work / "swapback-plan.json").is_file()


def test_swapback_plan_records_unknown_schema_fixture_rejection():
    model = _model()
    model["schema_version"] = 999
    with pytest.raises(pm9.UnknownSchema):
        pm9.inject_unknown_schema(model)
    # The committed fixture itself validates clean.
    assert project_model.validate_fixture()["id"] == "pm-e2e-fixture"


# ── Fusion key comp + swap script (FREE: pure string building) ───

def test_key_spec_moves_every_key_by_the_head_cut():
    spec = pm9._pm9_key_spec()
    assert [frame for frame, _ in spec] == [12, 30, 47]
    assert [value for _, value in spec] == [1.012, 1.03, 1.047]


def test_baked_comp_wires_the_spline_to_transform_size():
    text = pm9._pm9_comp_text()
    assert "Transform1 = Transform" in text
    assert "Transform1Size = BezierSpline" in text
    for frame, value in pm9._pm9_key_spec():
        assert f"[{frame}] = {{ {value} }}" in text
    size_block = text[text.find("Size = Input"):text.find("Size = Input") + 200]
    assert 'SourceOp = "Transform1Size"' in size_block


def test_swap_script_runs_the_adapter_and_imports_nothing():
    plan = {"unique_id": "pm9-swap-1",
            "prior_render_id": "render-pm9-full",
            "new_render_id": "render-pm9-trimmed",
            "new_source": "pm9-ada-trimmed.mp4",
            "key_moves": [{"comp": "PM9Comp", "tool": "Transform1",
                           "param": "Size", "from_frame": 12, "to_frame": 0,
                           "value": 1.012}],
            "grade": {"luts": [], "cdl": {}, "ranges": []}}
    script = pm9._swap_script(scratch="PM9 Scratch X", plan=plan,
                              v1_uid_hint="uid-1", stamp="stamp-1",
                              v2_path="/tmp/pm9-ada-trimmed.mp4")
    assert "swap.commit" in script
    assert "swap.accept" in script
    assert "pm9-swap-1" in script
    assert "pm9-swap:stamp-1" in script
    assert "/tmp/pm9-ada-trimmed.mp4" in script
    for primitive in ("ImportFusionComp", "AddFusionComp",
                      "CreateEmptyTimeline", "DuplicateTimeline",
                      "CreateProject", "DeleteTimeline", "DeleteTimelines",
                      "SetCurrentTimeline", "LoadProject", "open_project",
                      "CreateSequence", "AppendToTimeline"):
        assert primitive not in script, primitive
    namespace: dict = {"timeline": None, "project": None}
    tree = compile(script, "<pm9-swap-script>", "exec")
    assert tree is not None
    assert namespace["timeline"] is None


def test_run_result_parses_through_broker_escaping():
    inner = [{"span": [216, 264], "accept": {"status": "accepted"}}]
    stdout = ("run:\n  timeline: PM9 Scratch X (current)\n"
              f"{json.dumps(inner)}\n"
              "  journal: /tmp/pm9-qual/runs/x.json\n"
              "  inventory_guard: true\n")
    # The broker receipt embeds this whole stdout with JSON escapes...
    escaped = json.dumps({"steps": [{"stdout": stdout}]})
    step = json.loads(escaped)["steps"][0]
    assert pm9._parse_run_result(step)[0]["accept"]["status"] == "accepted"
    with pytest.raises(pm9.Pm9Refused):
        pm9._parse_run_result({"stdout": "no table here"})


# ── No live imports at module scope ─────────────────────────────────

def test_qualification_module_imports_without_broker_or_resolve():
    # A fresh interpreter importing only the qualification module must
    # not pull the broker or the Resolve lease: the live half imports
    # them lazily inside stage_resolve_live. The repo root rides
    # PYTHONPATH explicitly so the probe holds wherever the runner
    # checks the tree out.
    import os
    import subprocess
    import sys
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    probe = subprocess.run(
        [sys.executable, "-c",
         ("import sys, library.tools.project_model_qualification as q; "
          "bad = [m for m in sys.modules if m == 'library.tools.resolved' "
          "or m.startswith('library.tools.resolved.') "
          "or m == 'library.tools.resolve_lock']; "
          "print(','.join(sorted(bad)))")],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        check=False, cwd=str(ROOT), env=env)
    assert probe.returncode == 0, probe.stderr[-500:]
    assert probe.stdout.strip() == "", probe.stdout


def test_run_report_schema_keys_are_stable():
    report = {"schema": pm9.REPORT_SCHEMA, "stages": {},
              "filmcraft_pin": pm9.FILMCRAFT_REVISION,
              "effectcraft_pin": pm9.EFFECTCRAFT_REVISION}
    assert report["schema"] == "ren-pm9-qualification.v1"
    assert len(pm9.FILMCRAFT_REVISION) == 40
    assert len(pm9.EFFECTCRAFT_REVISION) == 40
    assert copy.deepcopy(report)["schema"].startswith("ren-pm9-")


# ── Native treatments stage (FREE: tiny renders under tmp_path) ───

def _filmcraft_evidence(base: Path) -> dict:
    return {"render": {"path": str(base), "frames": 8}}


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_native_treatments_pass_through_without_declarations(tmp_path):
    work = tmp_path / "pm9"
    work.mkdir()
    base = _tiny_mp4(work / "pm9-ada-trimmed.mp4")
    evidence = pm9.stage_native_treatments(work, _filmcraft_evidence(base))
    assert evidence["out"] == str(base)
    assert evidence["grade_applied"] is False
    assert evidence["bezel_applied"] is False
    assert evidence["refused"] == []
    assert not (work / "pm9-ada-trimmed.treated.mp4").exists()
    assert json.loads((work / "native-treatments.json").read_text(
        encoding="utf-8"))["out"] == str(base)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_native_treatments_composite_the_declared_grade(tmp_path):
    from library.tools import native_effects_composite as nec

    work = tmp_path / "pm9"
    work.mkdir()
    base = _tiny_mp4(work / "pm9-ada-trimmed.mp4")
    evidence = pm9.stage_native_treatments(
        work, _filmcraft_evidence(base),
        project_folder=_declarations(tmp_path))
    assert evidence["grade_applied"] is True
    assert evidence["grade_name"] == "strong-grade"
    assert evidence["bezel_applied"] is False
    assert Path(evidence["out"]).is_file()
    scores = nec.measure_ssim(base, evidence["out"], frames=8)
    assert scores["Y"] < 0.99  # the treated render carries the grade


def test_native_treatments_refuse_an_absent_base(tmp_path):
    work = tmp_path / "pm9"
    work.mkdir()
    missing = work / "pm9-ada-trimmed.mp4"
    with pytest.raises(pm9.Pm9Refused) as caught:
        pm9.stage_native_treatments(work, _filmcraft_evidence(missing))
    assert caught.value.code == "REN_PM9_RENDER_UNREADABLE"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_native_treatments_refuse_a_malformed_look(tmp_path):
    work = tmp_path / "pm9"
    work.mkdir()
    base = _tiny_mp4(work / "pm9-ada-trimmed.mp4")
    broken = STRONG_LOOK_YAML.replace("      saturation: 1.5\n", "")
    with pytest.raises(pm9.Pm9Refused) as caught:
        pm9.stage_native_treatments(
            work, _filmcraft_evidence(base),
            project_folder=_declarations(tmp_path, broken))
    assert caught.value.code == "REN_PM9_NATIVE_TREATMENTS"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_run_records_the_native_treatments_stage(tmp_path, monkeypatch):
    """run() itself reaches the stage: every heavy stage is stubbed,
    the treatments stage runs for real over a tiny render, and the
    report carries its pass-through evidence."""
    work_base = tmp_path / "pm9"
    work_base.mkdir()
    base = _tiny_mp4(work_base / "pm9-ada-trimmed.mp4")

    monkeypatch.setattr(pm9, "stage_ingest", lambda work: {"stub": True})
    monkeypatch.setattr(pm9, "stage_identity", lambda work: {"stub": True})
    monkeypatch.setattr(
        pm9, "stage_filmcraft", lambda work: _filmcraft_evidence(base))
    monkeypatch.setattr(pm9, "stage_effectcraft", lambda work: {"stub": True})
    monkeypatch.setattr(
        pm9, "stage_swapback_plan", lambda work, filmcraft: {"stub": True})
    monkeypatch.setattr(
        pm9, "stage_failure_injection", lambda work: {"stub": True})

    report = pm9.run(tmp_path / "out", with_resolve=False)
    assert report["passed"] is True
    stage = report["stages"]["native_treatments"]
    assert stage["status"] == "passed"
    assert stage["evidence"]["grade_applied"] is False
    assert stage["evidence"]["out"] == str(base)
