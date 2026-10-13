"""Audit gap G8: `ren project-model project` plans onto headless engines.

The verb reaches both front doors (`ren/commands.py` AND
`native/ren-cli/src/commands.rs` - a verb the Python list knows but the
native list does not is unreachable), plans and prints by default, and
executes only with `--execute`. The Rust qualification summary
advertises `pm-e2e` exactly as the Python registry does.

The FilmCraft half carries no adapter logic of its own: it plans
through the pinned Rust adapter on `ren_native`, so the tests below
prove the verb's plan EQUALS what the adapter itself returns for the
same model (placements, losses, per-edit verdicts and refusal codes),
rather than pinning a second copy of those numbers. Tests needing the
extension skip naming the build when this tree has none, the way the
ren-core parity harness does.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.project_model_cli import (  # noqa: E402
    _mask_contour_paths, main, translate_filmcraft_masks,
)

FIXTURE_MODEL = REPO / "tests/fixtures/project_model_e2e/expected-project-model.json"

FILMCRAFT_FUNCTIONS = (
    "filmcraft_plan_projection",
    "filmcraft_plan_picture_effects",
    "filmcraft_add_tracks_operation",
    "filmcraft_plan_ren_edit",
    "filmcraft_describe_operation",
    "filmcraft_reconcile_clip_ids",
    "filmcraft_verify_media",
    "filmcraft_import_operation",
    "filmcraft_placement_operations",
    "filmcraft_plan_dual_mono_mapping",
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Test Show"\nslug: show\n', encoding="utf-8")
    return root


def _model(tmp_path: Path, mutate=None) -> Path:
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    if mutate:
        mutate(model)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    return path


def _native_or_skip():
    """This tree's ren_native build with the FilmCraft adapter, or skip
    naming the build (parity-harness pattern)."""
    from library.tools import ren_native_cache

    module = ren_native_cache.select(REPO)
    if module is not None and all(hasattr(module, name) for name in FILMCRAFT_FUNCTIONS):
        return module
    pytest.skip(
        "the ren_native extension is not built - run "
        "scripts/build_ren_native.sh <venv-bin-dir> <cache-dir>")


def _no_engine(monkeypatch) -> None:
    """Fail the test if the plan path ever touches a subprocess."""
    import subprocess

    def boom(*args, **kwargs):
        raise AssertionError(f"plan executed a subprocess: {args[0]!r}")

    monkeypatch.setattr(subprocess, "run", boom)


# ── Registration ────────────────────────────────────────────


def test_the_project_model_verb_is_registered():
    from ren.commands import VERBS

    by_name = {verb.name: verb for verb in VERBS}
    assert "project-model" in by_name, "ren lost its 'project-model' verb"
    assert by_name["project-model"].module_argv == (
        "library.tools.project_model_cli",)


def test_the_project_model_verb_is_registered_in_both_front_doors():
    """A verb the Python list knows but the native list does not is
    unreachable - the native front door refuses it before exec'ing
    the implementation."""
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "project-model"' in native


def test_the_rust_qualification_summary_advertises_pm_e2e():
    """`commands.rs` omitted `pm-e2e` while Python advertised it; the
    two summaries must agree verbatim."""
    from ren.commands import VERBS

    python_summary = next(
        verb.summary for verb in VERBS if verb.name == "qualification")
    assert "pm-e2e" in python_summary
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    line = next(line for line in native.splitlines()
                if 'name: "qualification"' in line)
    assert "pm-e2e" in line, (
        "native qualification summary still omits pm-e2e")


def test_unknown_project_refuses_like_other_verbs(tmp_path):
    """Outside a project root the verb exits 2, as `ren work-state`
    does for an unknown slug."""
    with pytest.raises(SystemExit) as exit_info:
        main(["project", "no-such-slug-xyz", "--engine", "filmcraft",
              "--out", str(tmp_path / "plan.json")])
    assert exit_info.value.code == 2


# ── The verb's plan is the adapter's plan ───────────────────


def test_filmcraft_plan_equals_the_adapter_projection(
        tmp_path, monkeypatch, capsys):
    """Placements, losses, clocks and versions come back from
    `plan_projection` itself: the verb adds the per-edit rows and the
    file/stdout shell, nothing else. No subprocess runs."""
    native = _native_or_skip()
    _no_engine(monkeypatch)
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    expected = native.filmcraft_plan_projection(model)

    root = _project(tmp_path)
    out = tmp_path / "plan.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out)]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["executed"] is False
    for key in ("adapter_version", "engine_revision", "timeline_clock",
                "imports", "placements", "losses"):
        assert plan[key] == expected[key], key
    assert not list(tmp_path.glob("*.fcproj"))
    printed = json.loads(capsys.readouterr().out)
    assert printed["placements"] == len(expected["placements"])
    assert printed["executed"] is False


def test_segmented_person_mask_projects_a_measured_polygon_and_native_track(tmp_path):
    """Only source-linked SAM2 mattes seed FilmCraft, with polygon loss measured."""
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    use = next(row for row in model["clip_uses"] if row["role"] == "a_roll")
    use["entity_links"] = ["person-craig"]
    source = use["source_interval"]
    width = height = 32
    source_clock = next(clock for clock in model["clocks"]
                        if clock["id"] == source["clock_id"])
    start = int(source["start"] * 2 / source_clock["ticks_per_second"])
    runs = []
    for y in range(height):
        for x in range(width):
            if 5 <= x < 21 and 4 <= y < 29:
                runs.append(y * width + x)
    rle = ",".join(f"{idx} 1" for idx in runs)
    segmentation = tmp_path / "segmentation.json"
    segmentation.write_text(json.dumps({
        "resolution": [height, width],
        "objects": [{"object_id": "person", "masks_rle": {str(start): rle}}],
    }), encoding="utf-8")
    mask = {
        "id": "mask-person", "entity_id": "person-craig",
        "interval": {**source, "start": 0},
        "representation": {"kind": "sam2_rle_track",
                           "segmentation_file": str(segmentation),
                           "object_id": "person", "sample_fps": 2,
                           "resolution": [height, width]},
    }
    model["masks"] = [mask]
    projection = {"placements": [
        {"clip_use_id": use["id"], "duration_ticks": 256 * 10594584},
    ]}

    class Native:
        @staticmethod
        def filmcraft_describe_operation(operation):
            return {"command": operation["kind"].replace("_", "."),
                    "params": operation, "save": True}

    commands = translate_filmcraft_masks(
        model, projection, {use["id"]: [44]}, str(tmp_path / "out.fcproj"),
        Native(), project_root=tmp_path)
    assert [row["command"] for row in commands] == ["mask.add", "mask.track"]
    seed = commands[0]["params"]["path"]
    assert len(seed) >= 4
    clock = next(row for row in model["clocks"]
                 if row["id"] == source["clock_id"])
    rate = clock["frame_rate"]
    expected_frames = round((source["end"] - source["start"])
                            * rate["numerator"]
                            / (clock["ticks_per_second"] * rate["denominator"]))
    assert commands[1]["params"]["frames"] == expected_frames


def test_m3d_region_mask_projects_from_source_memory(tmp_path):
    """A face-linked M3d region can seed a native mask from its sidecar."""
    import gzip
    import hashlib
    from itertools import groupby

    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    use = next(row for row in model["clip_uses"] if row["role"] == "a_roll")
    use["entity_links"] = ["person-craig"]
    source = use["source_interval"]
    clock = next(row for row in model["clocks"]
                 if row["id"] == source["clock_id"])
    ticks_per_second = clock["ticks_per_second"]
    frame_rate = clock["frame_rate"]
    frame_ticks = (ticks_per_second * frame_rate["denominator"]
                   // frame_rate["numerator"])
    source["start"] = 20 * ticks_per_second
    source["end"] = source["start"] + 256 * frame_ticks
    width = height = 32
    flat = []
    for y in range(height):
        flat.extend(1 if 5 <= x < 24 and 3 <= y < 30 else 0
                    for x in range(width))
    runs = [len(list(group)) for _, group in groupby(flat)]
    from library.tools.project_model_masks import REGION_RUNS_CODEC

    digest = "a" * 64
    memory_root = tmp_path / "memory"
    source_dir = memory_root / digest
    source_dir.mkdir(parents=True)
    payload = json.dumps({"codec": REGION_RUNS_CODEC,
                          "grid": [width, height],
                          "masks": [runs, runs]},
                         separators=(",", ":")).encode("utf-8")
    sidecar = "regions.masks.fixture.json.gz"
    (source_dir / sidecar).write_bytes(gzip.compress(payload))
    mask = {
        "id": "mask-region-person", "entity_id": "person-craig",
        "interval": {"clock_id": source["clock_id"], "start": 0,
                     "end": 40 * ticks_per_second},
        "representation": {
            "kind": "region_rle", "content_digest": digest,
            "sidecar_file": sidecar,
            "sidecar_sha256": hashlib.sha256(payload).hexdigest(),
            "codec": REGION_RUNS_CODEC, "grid": [width, height],
            "track_id": "region_001",
            "sample_ticks": [19_500, 20_000], "mask_indices": [0, 1],
        },
    }
    model["masks"] = [mask]
    projection = {"placements": [{"clip_use_id": use["id"]}]}

    class Native:
        @staticmethod
        def filmcraft_describe_operation(operation):
            return {"command": operation["kind"].replace("_", "."),
                    "params": operation, "save": True}

    commands = translate_filmcraft_masks(
        model, projection, {use["id"]: [44]},
        str(tmp_path / "out.fcproj"), Native(),
        project_root=tmp_path, memory_root=memory_root)
    assert [row["command"] for row in commands] == ["mask.add", "mask.track"]
    assert len(commands[0]["params"]["path"]) >= 4
    assert commands[1]["params"]["frames"] == 256


def test_mask_polygonization_reports_raster_agreement():
    """The polygon passed to the native tracker must closely match its seed."""
    import numpy as np

    image = np.zeros((64, 64), dtype=np.uint8)
    image[8:56, 16:48] = 255
    paths, agreement = _mask_contour_paths(image.tobytes(), 64, 64)
    assert len(paths) == 1
    assert agreement >= 0.99


def test_filmcraft_edit_rows_match_the_adapter_verdicts(tmp_path, monkeypatch):
    """Each row's executable/code is the adapter's own translation
    verdict on that edit (planned with placeholder ids), including a
    stepped ramp and a plan-level record."""

    def add_unexecutable(model):
        model["edits"].append({
            "id": "edit-ramp", "operation": "retime.segments",
            "target_ids": ["use-ada"], "before": None,
            "after": {"segments": []}, "intent_id": None,
            "evidence_ids": [], "author": "test",
            "parent_revision": None})
        model["edits"].append({
            "id": "edit-mix", "operation": "audio_mix",
            "target_ids": [], "before": None, "after": {},
            "intent_id": None, "evidence_ids": [], "author": "test",
            "parent_revision": None})

    native = _native_or_skip()
    _no_engine(monkeypatch)
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    add_unexecutable(model)
    projection = native.filmcraft_plan_projection(model)
    dummy_ids = {p["clip_use_id"]: [0] for p in projection["placements"]}
    expected_rows = []
    for edit in model["edits"]:
        try:
            native.filmcraft_plan_ren_edit(model, projection, edit, dummy_ids, "")
            expected_rows.append({"id": edit["id"], "operation": edit["operation"],
                                  "executable": True})
        except Exception as exc:  # noqa: BLE001 - the adapter's code is the verdict
            text = str(exc)
            code = text.split("(")[1].split(")")[0] if "(" in text else "adapter_refused"
            expected_rows.append({"id": edit["id"], "operation": edit["operation"],
                                  "executable": False, "code": code})

    root = _project(tmp_path)
    model_path = _model(tmp_path, add_unexecutable)
    out = tmp_path / "plan.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["edits"] == expected_rows
    ramp = next(row for row in expected_rows if row["id"] == "edit-ramp")
    assert ramp == {"id": "edit-ramp", "operation": "retime.segments",
                    "executable": False,
                    "code": "retime_segments_not_supported"}


def test_filmcraft_refusal_carries_the_adapter_code(tmp_path, monkeypatch, capsys):
    """An overlapping timeline refuses with the adapter's own code -
    the verb quotes it, it never invents one."""

    def overlap(model):
        for use in model["clip_uses"]:
            if use["id"] == "use-ben":
                use["timeline_interval"]["start"] = 30

    _native_or_skip()
    _no_engine(monkeypatch)
    root = _project(tmp_path)
    model_path = _model(tmp_path, overlap)
    out = tmp_path / "plan.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(out)]) == 4
    assert not out.exists()
    assert "timeline_overlap" in capsys.readouterr().err


def test_effectcraft_plan_projects_the_request_without_executing(
        tmp_path, monkeypatch, capsys):
    _no_engine(monkeypatch)
    root = _project(tmp_path)
    out = tmp_path / "plan.json"
    assert main(["project", str(root), "--engine", "effectcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent)]) == 0
    plan = json.loads(out.read_text(encoding="utf-8"))
    assert plan["executed"] is False
    assert plan["engine_revision"] == "813c7c4650f0d4c805da620c8d5837c9600ad2e3"
    kinds = [op["op"] for op in plan["request"]["operations"]]
    assert kinds[0] == "new_comp"
    assert "add_mask" in kinds and "apply_effect" in kinds
    assert not list(tmp_path.glob("*.ecproj"))
    printed = json.loads(capsys.readouterr().out)
    assert printed["operations"] == len(kinds)


# ── Fail-closed execution ────────────────────────────────


def test_ramp_execute_refuses_before_mutation(tmp_path, monkeypatch):
    """A stepped ramp plans (named loss) but --execute refuses with no
    engine project created - nothing is half-built."""

    def add_ramp(model):
        model["edits"].append({
            "id": "edit-ramp", "operation": "retime.segments",
            "target_ids": ["use-ada"], "before": None,
            "after": {"segments": []}, "intent_id": None,
            "evidence_ids": [], "author": "test",
            "parent_revision": None})

    _native_or_skip()
    monkeypatch.setattr("library.tools.project_model_cli._filmcraft_cli",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("no engine contact on refusal")))
    root = _project(tmp_path)
    model_path = _model(tmp_path, add_ramp)
    exec_out = tmp_path / "exec.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(exec_out),
                 "--execute"]) == 4
    assert not list(tmp_path.glob("*.fcproj"))
    assert not exec_out.exists()


def test_execute_needs_the_pinned_cli(tmp_path, monkeypatch):
    """A model with no edits still refuses when the pinned CLI is
    absent, before creating anything."""

    def drop_edits(model):
        model["edits"] = []

    _native_or_skip()
    root = _project(tmp_path)
    model_path = _model(tmp_path, drop_edits)
    monkeypatch.setenv("REN_FILMCRAFT_CLI", str(tmp_path / "no-cli"))
    out = tmp_path / "exec.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute"]) == 4
    assert not list(tmp_path.glob("*.fcproj"))


# ── Native treatments on the export path ────────────────────
#
# The PR 2310 composite, wired into the verb: --execute --export
# names the native render, so the treated picture lands AT the
# export path without a manual step. The engine is stubbed (its
# CLI never builds here); the composite runs for real on tiny
# ffmpeg renders, so the pixel assertions are behavioural.

STRONG_LOOK_YAML = """\
name: "Test Show"
slug: show
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


def _look_root(tmp_path: Path) -> Path:
    root = tmp_path / "show"
    root.mkdir()
    (root / "project.yaml").write_text(STRONG_LOOK_YAML, encoding="utf-8")
    return root


def _stub_native():
    """The FilmCraft adapter surface execute_filmcraft drives."""
    import types

    projection = {
        "adapter_version": "test-1",
        "engine_revision": "test-rev",
        "timeline_clock": "clock-project",
        "imports": [],
        "placements": [],
        "losses": [],
    }
    native = types.SimpleNamespace()
    native.filmcraft_plan_projection = lambda model: dict(projection)
    native.filmcraft_plan_picture_effects = lambda *args: []
    native.filmcraft_add_tracks_operation = lambda *args: None
    native.filmcraft_plan_ren_edit = lambda *args: []
    native.filmcraft_describe_operation = lambda op: {
        "command": op["kind"],
        "params": ({"path": op["path"]} if op.get("path") else {}),
        "save": False}
    native.filmcraft_verify_media = lambda _projection, _media_dir: {}
    native.filmcraft_import_operation = lambda *args: {"kind": "import"}
    native.filmcraft_placement_operations = lambda *args: []
    native.filmcraft_plan_dual_mono_mapping = lambda *args: None
    native.filmcraft_reconcile_clip_ids = lambda *args: {}
    return native


def _stub_engine(monkeypatch, tmp_path: Path) -> None:
    """Stub the pinned CLI; the export renders a tiny clip for real.

    The stub honours the export path suffix the verb names: a ``.mov``
    intermediate renders ProRes (the mezzanine a treated run exports),
    anything else renders H264 (an untreated delivery straight from
    the engine). The render is copied aside as the engine base the
    pixel assertions read back.
    """
    import subprocess

    import library.tools.project_model_cli as pmc

    def fake_fc_exec(cli, project, command, params, *,
                     save=False, save_as=None):
        if save_as is not None:
            Path(save_as).write_bytes(b"fake-project")
            return {}
        if command == "import":
            return {"items": [], "errors": []}
        if command == "sequence.inspect":
            return {}
        if command == "export":
            out = Path(params["path"])
            if out.suffix == ".mov":
                args = ["-c:v", "prores_ks", "-profile:v", "3",
                        "-pix_fmt", "yuv422p10le"]
                aside = tmp_path / "engine-base.mov"
            else:
                args = ["-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p"]
                aside = tmp_path / "engine-base.mp4"
            done = subprocess.run(
                ["ffmpeg", "-v", "error", "-y",
                 "-f", "lavfi", "-i",
                 "testsrc=size=64x112:rate=24:duration=2",
                 "-f", "lavfi", "-i",
                 "sine=frequency=440:sample_rate=48000:duration=2"]
                + args + ["-c:a", "aac", "-shortest", str(out)],
                capture_output=True, text=True, encoding="utf-8",
                check=False)
            assert done.returncode == 0, done.stderr[:300]
            aside.write_bytes(out.read_bytes())
            return {"exported": str(out)}
        return {}

    monkeypatch.setattr(pmc, "filmcraft_native", lambda: _stub_native())
    monkeypatch.setattr(pmc, "_filmcraft_cli",
                        lambda: tmp_path / "fake-cli")
    monkeypatch.setattr(pmc, "_check_cli_version",
                        lambda cli, expect, engine: None)
    monkeypatch.setattr(pmc, "_fc_exec", fake_fc_exec)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_execute_export_composites_the_declared_grade(tmp_path, monkeypatch):
    from library.tools import native_effects_composite as nec

    _stub_engine(monkeypatch, tmp_path)
    root = _look_root(tmp_path)
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["export"]["engine_format"] == "prores"
    assert receipt["export"]["intermediate"] == str(
        tmp_path / "reel.engine-base.mov")
    treatments = receipt["native_treatments"]
    assert treatments["grade_applied"] is True
    assert treatments["bezel_applied"] is False
    assert treatments["grade_name"] == "strong-grade"
    assert treatments["out"] == str(export)
    assert treatments["refused"] == []
    assert treatments["lossy_encodes"] == 1
    assert treatments["engine_format"] == "prores"
    assert treatments["intermediate_removed"] is True
    assert receipt["mastering"]["target"]["lufs"] == -14.0
    assert receipt["mastering"]["target"]["true_peak_ceiling"] == -1.0
    assert receipt["mastering"]["loudness_range_lu"]["input"] >= 0
    assert (tmp_path / "reel.pre-master.mp4").is_file()
    assert export.is_file()
    assert not list(tmp_path.glob("*.treatments-tmp.mp4"))
    assert not list(tmp_path.glob("*.engine-base.mov"))
    base = tmp_path / "engine-base.mov"
    scores = nec.measure_ssim(base, export, frames=8)
    assert scores["Y"] < 0.99  # the export carries the declared grade


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_treated_export_performs_one_lossy_encode(tmp_path, monkeypatch):
    """A treated H264 delivery goes through exactly one final encode.

    The engine renders a ProRes mezzanine intermediate (never an H264
    delivery), and the treatments pass encodes the delivery once: one
    composite call, one libx264 invocation, final bytes probing as
    H264, intermediate removed.
    """
    import subprocess

    import library.tools.project_model_cli as pmc
    from library.tools import native_effects_composite as nec

    _stub_engine(monkeypatch, tmp_path)
    export_ops: list[dict] = []
    native = _stub_native()
    real_op = native.filmcraft_describe_operation

    def recording_describe(op):
        if op.get("kind") == "export":
            export_ops.append(dict(op))
        return real_op(op)

    native.filmcraft_describe_operation = recording_describe
    monkeypatch.setattr(pmc, "filmcraft_native", lambda: native)

    composite_calls: list[dict] = []
    real_composite = nec.composite_declared_treatments

    def counting_composite(base, out, **kwargs):
        composite_calls.append({"base": str(base), "out": str(out),
                                "kwargs": dict(kwargs)})
        return real_composite(base, out, **kwargs)

    monkeypatch.setattr(nec, "composite_declared_treatments",
                        counting_composite)

    libx264_runs = 0
    real_run = subprocess.run

    def counting_run(argv, *args, **kwargs):
        nonlocal libx264_runs
        if (isinstance(argv, list) and "libx264" in list(argv)
                and str(tmp_path) in " ".join(str(a) for a in argv)):
            libx264_runs += 1
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting_run)
    # The nec module holds its own subprocess reference; count there too.
    monkeypatch.setattr(nec.subprocess, "run", counting_run)

    root = _look_root(tmp_path)
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 0
    assert len(export_ops) == 1
    assert export_ops[0]["format"] == "pro_res", (
        "engine must render a ProRes mezzanine, got "
        f"{export_ops[0]!r}")
    assert len(composite_calls) == 1
    assert composite_calls[0]["base"].endswith(".engine-base.mov")
    assert composite_calls[0]["out"] == str(export)
    assert libx264_runs == 1, (
        f"exactly one libx264 encode, saw {libx264_runs}")
    probe = real_run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "json", str(export)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert json.loads(probe.stdout)["streams"][0]["codec_name"] == "h264"
    assert not (tmp_path / "reel.engine-base.mov").exists()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_execute_export_leaves_engine_bytes_alone_without_declarations(
        tmp_path, monkeypatch):
    from library.tools import native_effects_composite as nec

    _stub_engine(monkeypatch, tmp_path)
    monkeypatch.setattr(
        nec, "composite_declared_treatments",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no declarations means no composite call")))
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    treatments = receipt["native_treatments"]
    assert treatments["grade_applied"] is False
    assert treatments["bezel_applied"] is False
    assert treatments["out"] == str(export)
    assert treatments["lossy_encodes"] == 1
    assert treatments["intermediate"] is None
    assert receipt["export"]["intermediate"] is None
    assert receipt["export"]["engine_format"] == "h264"
    assert receipt["mastering"]["target"]["lufs"] == -14.0
    assert (tmp_path / "reel.pre-master.mp4").read_bytes() == (
        tmp_path / "engine-base.mp4").read_bytes()
    assert export.read_bytes() != (tmp_path / "engine-base.mp4").read_bytes()


def test_execute_refuses_an_existing_pre_master_before_engine_work(
        tmp_path, monkeypatch):
    _stub_engine(monkeypatch, tmp_path)
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    raw = tmp_path / "reel.pre-master.mp4"
    raw.write_bytes(b"previous unmastered evidence")

    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 4
    assert raw.read_bytes() == b"previous unmastered evidence"
    assert not export.exists()
    assert not list(tmp_path.glob("*.fcproj"))


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_execute_without_export_composites_nothing(tmp_path, monkeypatch):
    from library.tools import native_effects_composite as nec

    _stub_engine(monkeypatch, tmp_path)
    monkeypatch.setattr(
        nec, "composite_declared_treatments",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no export means no composite call")))
    root = _look_root(tmp_path)
    out = tmp_path / "receipt.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute"]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["native_treatments"]["skipped"].startswith("no export")
    assert receipt["native_treatments"]["lossy_encodes"] == 0


# ── Batched FilmCraft sessions (`filmcraft-cli run`) ──────────
#
# Placements and translated edits run through one `run` session per
# phase instead of one `exec` process per command. The tests below
# prove the batch sends the same commands in order through a single
# invocation, names the failing command (per-command attribution), and
# refuses a short answer rather than pairing receipts wrongly.


def _ok_line(command: str, line: int, result) -> str:
    return json.dumps({"id": command, "line": line, "ok": True,
                       "result": result})


def _fail_line(command: str, line: int, error: str) -> str:
    return json.dumps({"id": command, "line": line, "ok": False,
                       "error": error})


def _run_result(stdout: str, returncode: int = 0, stderr: str = ""):
    import subprocess

    return subprocess.CompletedProcess(args=["filmcraft-cli"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_fc_run_batch_sends_one_session_in_order(tmp_path, monkeypatch):
    import subprocess

    import library.tools.project_model_cli as pmc

    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        script = Path(argv[argv.index("run") + 1])
        lines = script.read_text(encoding="utf-8").splitlines()
        out = "\n".join(
            _ok_line(json.loads(raw)["id"], number, {"n": number})
            for number, raw in enumerate(lines, start=1))
        return _run_result(out)

    monkeypatch.setattr(subprocess, "run", fake_run)
    commands = [{"command": "timeline.place", "params": {"item": 7}},
                {"command": "timeline.move", "params": {"clip": 11}}]
    results = pmc._fc_run_batch(tmp_path / "fake-cli", tmp_path / "p.fcproj",
                                commands, save=True)
    assert results == [{"n": 1}, {"n": 2}]
    assert len(seen) == 1, "one backend session for the whole phase"
    argv = seen[0]
    assert argv[argv.index("run") - 2:argv.index("run")] == [
        "--project", str(tmp_path / "p.fcproj")]
    assert argv[-2:] == ["--compact", "--save"]
    assert "exec" not in argv


def test_fc_run_batch_names_the_failing_command(tmp_path, monkeypatch):
    import subprocess

    import library.tools.project_model_cli as pmc

    def fake_run(argv, **kwargs):
        return _run_result("\n".join([
            _ok_line("timeline.place", 1, {"clips": [11]}),
            _fail_line("timeline.move", 2, "no such clip 999"),
        ]))

    monkeypatch.setattr(subprocess, "run", fake_run)
    commands = [{"command": "timeline.place", "params": {}},
                {"command": "timeline.move", "params": {}},
                {"command": "clip.link", "params": {}}]
    with pytest.raises(pmc.ProjectModelCliRefused) as exc_info:
        pmc._fc_run_batch(tmp_path / "fake-cli", tmp_path / "p.fcproj", commands)
    refused = exc_info.value
    assert refused.code == "REN_PM_CLI_FILMCRAFT_COMMAND"
    assert "'timeline.move'" in refused.what
    assert "line 2 of 3" in refused.what
    assert "no such clip 999" in refused.why


def test_fc_run_batch_refuses_a_short_answer(tmp_path, monkeypatch):
    import subprocess

    import library.tools.project_model_cli as pmc

    def fake_run(argv, **kwargs):
        # The CLI ended mid-batch with no failure line: the third command
        # never answered, so nothing may be paired with its receipt.
        return _run_result(_ok_line("timeline.place", 1, {"clips": [11]}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    commands = [{"command": "timeline.place", "params": {}},
                {"command": "timeline.move", "params": {}}]
    with pytest.raises(pmc.ProjectModelCliRefused) as exc_info:
        pmc._fc_run_batch(tmp_path / "fake-cli", tmp_path / "p.fcproj", commands)
    assert "'timeline.move'" in exc_info.value.what


def _batch_native():
    """The adapter surface with two placements and one translated edit."""
    import types

    projection = {
        "adapter_version": "test-1",
        "engine_revision": "test-rev",
        "timeline_clock": "clock-project",
        "imports": [{"asset_id": "a1", "uri": "a1.mp4"}],
        "placements": [
            {"clip_use_id": "u1"}, {"clip_use_id": "u2"},
        ],
        "losses": [],
    }
    native = types.SimpleNamespace()
    native.filmcraft_plan_projection = lambda model: dict(projection)
    native.filmcraft_plan_picture_effects = lambda *args: []
    native.filmcraft_add_tracks_operation = lambda *args: None
    native.filmcraft_plan_ren_edit = lambda *args: [{"kind": "timeline.move"}]
    native.filmcraft_describe_operation = lambda op: {
        "command": ("clip.audioChannels" if op["kind"] == "audio_channels"
                    else op["kind"]),
        "params": ({"items": op["items"]} if op["kind"] == "audio_channels"
                   else {"at": op["kind"]}),
        "save": (bool(op.get("format") or op.get("clips"))
                 if op["kind"] == "audio_channels" else True)}
    native.filmcraft_verify_media = lambda _projection, _media_dir: {"a1": "a1.mp4"}
    native.filmcraft_import_operation = lambda *args: {"kind": "import"}
    native.filmcraft_placement_operations = lambda *args: [
        ("u1", {"kind": "timeline.place"}), ("u2", {"kind": "timeline.place"})]
    native.filmcraft_plan_dual_mono_mapping = lambda *args: None
    native.filmcraft_reconcile_clip_ids = lambda *args: {"u1": [11], "u2": [12]}
    return native


def _batch_model(tmp_path: Path) -> Path:
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    model["edits"] = [{
        "id": "e1", "operation": "timeline.move",
        "target_ids": ["use-ada"], "before": None, "after": {},
        "intent_id": None, "evidence_ids": [], "author": "test",
        "parent_revision": None}]
    path = tmp_path / "batch-model.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    return path


def _batch_engine(monkeypatch, tmp_path: Path, calls: dict) -> None:
    """Stub the CLI boundary: batch sessions recorded, single execs live."""
    import library.tools.project_model_cli as pmc

    def fake_fc_exec(cli, project, command, params, *,
                     save=False, save_as=None):
        calls.setdefault("exec", []).append(command)
        calls.setdefault("exec_params", []).append((command, params))
        if save_as is not None:
            Path(save_as).write_bytes(b"fake-project")
            return {}
        if command == "import":
            return {"items": [7], "errors": []}
        if command == "clip.audioChannels":
            return {"items": [{"item": 7, "clips": [[0, 1], [2, 3]]}]}
        if command == "sequence.addTracks":
            return {}
        if command == "sequence.settings":
            return {}
        if command == "sequence.inspect":
            return {"settings": {"width": 1080, "height": 1920}}
        raise AssertionError(f"unexpected single exec: {command}")

    def fake_fc_run_batch(cli, project, commands, *, save=True):
        calls.setdefault("batches", []).append(
            [entry["command"] for entry in commands])
        return [{"clips": [11]}, {"clips": [12]}][:len(commands)]

    monkeypatch.setattr(pmc, "filmcraft_native", _batch_native)
    monkeypatch.setattr(pmc, "_filmcraft_cli", lambda: tmp_path / "fake-cli")
    monkeypatch.setattr(pmc, "_check_cli_version",
                        lambda cli, expect, engine: None)
    monkeypatch.setattr(pmc, "_fc_exec", fake_fc_exec)
    monkeypatch.setattr(pmc, "_fc_run_batch", fake_fc_run_batch)


def test_execute_batches_placements_and_edits_into_two_sessions(
        tmp_path, monkeypatch):
    import library.tools.project_model_cli as pmc

    calls: dict = {}
    _batch_engine(monkeypatch, tmp_path, calls)
    monkeypatch.delenv("REN_FILMCRAFT_BATCH", raising=False)
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(_batch_model(tmp_path)), "--out", str(out),
                 "--execute"]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["executed"] is True
    assert receipt["clip_ids"] == {"u1": [11], "u2": [12]}
    assert calls["batches"] == [["timeline.place", "timeline.place"],
                                ["timeline.move"]], calls
    assert "timeline.place" not in calls.get("exec", [])
    assert "timeline.move" not in calls.get("exec", [])
    assert pmc._batch_enabled() is True


def test_execute_queries_and_applies_native_dual_mono_before_placement(
        tmp_path, monkeypatch):
    import library.tools.project_model_cli as pmc

    calls: dict = {}
    _batch_engine(monkeypatch, tmp_path, calls)
    native = _batch_native()
    plans = []

    def plan(model, asset_id, item, project, mapping):
        plans.append((asset_id, item, mapping))
        return {"kind": "audio_channels", "project": project, "items": [item],
                "format": "stereo", "clips": [[0, 0]]}

    native.filmcraft_plan_dual_mono_mapping = plan
    monkeypatch.setattr(pmc, "filmcraft_native", lambda: native)
    monkeypatch.delenv("REN_FILMCRAFT_BATCH", raising=False)
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(_batch_model(tmp_path)), "--out", str(out),
                 "--execute"]) == 0
    assert plans and plans[0][1] == 7
    assert plans[0][2]["items"][0]["clips"] == [[0, 1], [2, 3]]
    assert calls["exec"].count("clip.audioChannels") == 2
    assert calls["exec"].index("import") < calls["exec"].index(
        "clip.audioChannels")
    assert calls["batches"][0] == ["timeline.place", "timeline.place"]


def test_missing_aroll_audio_streams_are_measured_for_the_planner(
        tmp_path, monkeypatch):
    import subprocess
    import types

    from library.tools.project_model_cli import _with_measured_aroll_audio_streams

    media = tmp_path / "quad-mono.mxf"
    media.write_bytes(b"fixture")
    model = {
        "streams": [{"id": "video-a", "asset_id": "a", "clock_id": "source-a",
                     "metadata": {"codec_type": "video"}}],
        "clip_uses": [{"asset_id": "a", "role": "a_roll"}],
    }
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        types.SimpleNamespace(
                            returncode=0,
                            stdout=json.dumps({"streams": [
                                {"index": 1, "channels": 1},
                                {"index": 2, "channels": 1},
                                {"index": 3, "channels": 1},
                                {"index": 4, "channels": 1},
                            ]}), stderr=""))

    augmented = _with_measured_aroll_audio_streams(model, {"a": media})
    audio = [stream for stream in augmented["streams"]
             if stream["metadata"]["codec_type"] == "audio"]
    assert [stream["metadata"]["channels"] for stream in audio] == [1, 1, 1, 1]
    assert all(stream["clock_id"] == "source-a" for stream in audio)
    assert len(model["streams"]) == 1  # execution enrichment is non-mutating


def test_execute_provisions_tracks_before_import_and_placement(tmp_path, monkeypatch):
    import library.tools.project_model_cli as pmc

    calls: dict = {}
    _batch_engine(monkeypatch, tmp_path, calls)
    native = _batch_native()
    native.filmcraft_add_tracks_operation = lambda *args: {"kind": "sequence.addTracks"}
    monkeypatch.setattr(pmc, "filmcraft_native", lambda: native)
    monkeypatch.delenv("REN_FILMCRAFT_BATCH", raising=False)
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"

    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(_batch_model(tmp_path)), "--out", str(out),
                 "--execute", "--sequence-size", "1080x1920"]) == 0
    assert calls["exec"][1:5] == ["sequence.settings", "sequence.inspect",
                                  "sequence.addTracks", "import"]
    assert calls["exec_params"][1] == (
        "sequence.settings", {"width": 1080, "height": 1920,
                               "scaleMotion": False})
    assert calls["batches"][0] == ["timeline.place", "timeline.place"]


def test_execute_refuses_invalid_sequence_size_before_project_creation(
        tmp_path, monkeypatch):
    calls: dict = {}
    _batch_engine(monkeypatch, tmp_path, calls)
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(_batch_model(tmp_path)), "--out", str(out),
                 "--execute", "--sequence-size", "vertical"]) == 4
    assert calls.get("exec") is None


def test_execute_hatch_restores_the_per_exec_loop(tmp_path, monkeypatch):
    calls: dict = {}
    _batch_engine(monkeypatch, tmp_path, calls)
    monkeypatch.setenv("REN_FILMCRAFT_BATCH", "0")
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"

    import library.tools.project_model_cli as pmc

    placed_queue = [[11], [12]]

    def legacy_exec(cli, project, command, params, *,
                    save=False, save_as=None):
        calls.setdefault("exec", []).append(command)
        if save_as is not None:
            Path(save_as).write_bytes(b"fake-project")
            return {}
        if command == "import":
            return {"items": [7], "errors": []}
        if command == "clip.audioChannels":
            return {"items": [{"item": 7, "clips": [[0, 1], [2, 3]]}]}
        if command == "timeline.place":
            return {"clips": placed_queue.pop(0)}
        if command == "timeline.move":
            return {}
        if command == "sequence.inspect":
            return {}
        raise AssertionError(f"unexpected single exec: {command}")

    monkeypatch.setattr(pmc, "_fc_exec", legacy_exec)
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(_batch_model(tmp_path)), "--out", str(out),
                 "--execute"]) == 0
    assert calls.get("batches") is None, "the hatch runs no batch session"
    assert calls["exec"].count("timeline.place") == 2
    assert "timeline.move" in calls["exec"]


def test_execute_refuses_an_empty_batch_without_contact(tmp_path, monkeypatch):
    import library.tools.project_model_cli as pmc

    with pytest.raises(pmc.ProjectModelCliRefused):
        pmc._fc_run_batch(tmp_path / "fake-cli", tmp_path / "p.fcproj", [])


def test_execute_refuses_a_malformed_look_before_mutation(tmp_path, monkeypatch):
    """A series_look that cannot be delivered refuses before the
    engine project exists - the export never renders half-treated."""
    import library.tools.project_model_cli as pmc

    monkeypatch.setattr(pmc, "filmcraft_native", lambda: _stub_native())
    monkeypatch.setattr(
        pmc, "_filmcraft_cli",
        lambda: (_ for _ in ()).throw(
            AssertionError("no engine contact on refusal")))
    root = tmp_path / "show"
    root.mkdir()
    (root / "project.yaml").write_text(
        STRONG_LOOK_YAML.replace("      saturation: 1.5\n", ""),
        encoding="utf-8")
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 4
    assert not list(tmp_path.glob("*.fcproj"))
    assert not export.exists()
    assert not out.exists()


# ── Pre-conform (opt-in, default off) ────────────────────
#
# `--preconform` wires library/tools/filmcraft_preconform.py into
# execute_filmcraft: used a_roll/b_roll spans transcode once to the
# project's scratch cache and the engine places the proxies. Fidelity
# is the captain's priority, so the flag defaults OFF: off means
# untouched (no transcode call, no cache dir) while on means
# content-addressed reuse (the second run is all cache hits).

LINKED_AV = REPO / "native/ren-filmcraft/tests/fixtures/linked-av.mp4"
ENGINE_TPS = 254016000000


def _stub_rich_engine(monkeypatch, tmp_path: Path) -> None:
    """Stub CLI that pairs with NON-empty projections: one item per
    import path, one clip per place, a tiny mp4 per export.

    Placements run through one batched `run` session per phase (PR
    2316), not one `exec` process per command, so the batch boundary is
    stubbed too: each batched entry answers exactly what the single
    `exec` path answers for the same command, in order."""
    import subprocess

    import library.tools.project_model_cli as pmc

    def _answer(command: str, params: dict):
        if command == "file.import":
            paths = params.get("paths") or []
            return {"items": list(range(101, 101 + len(paths))),
                    "errors": []}
        if command == "timeline.place":
            return {"clips": [201]}
        if command == "sequence.inspect":
            return {}
        return {}

    def fake_fc_exec(cli, project, command, params, *,
                     save=False, save_as=None):
        if save_as is not None:
            Path(save_as).write_bytes(b"fake-project")
            return {}
        if command == "file.exportMedia":
            out = Path(params["path"])
            done = subprocess.run(
                ["ffmpeg", "-v", "error", "-y",
                 "-f", "lavfi", "-i",
                 "testsrc=size=64x112:rate=24:duration=0.3333",
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-pix_fmt", "yuv420p", str(out)],
                capture_output=True, text=True, encoding="utf-8",
                check=False)
            assert done.returncode == 0, done.stderr[:300]
            return {"exported": str(out)}
        return _answer(command, params)

    def fake_fc_run_batch(cli, project, commands, *, save=True):
        return [_answer(entry["command"], entry["params"])
                for entry in commands]

    monkeypatch.setattr(pmc, "_filmcraft_cli",
                        lambda: tmp_path / "fake-cli")
    monkeypatch.setattr(pmc, "_check_cli_version",
                        lambda cli, expect, engine: None)
    monkeypatch.setattr(pmc, "_fc_exec", fake_fc_exec)
    monkeypatch.setattr(pmc, "_fc_run_batch", fake_fc_run_batch)


def _mini_model():
    """One a_roll use (source 30..60 of the 72-frame linked fixture)
    plus one overlay use: the first proxies, the second stays direct."""
    clock = {"ticks_per_second": 24, "frame_rate": {"numerator": 24,
                                                   "denominator": 1}}
    return {
        "clocks": [
            {"id": "clock-av", "domain": {"kind": "source", "asset_id": "asset-av"},
             **clock},
            {"id": "clock-project", "domain": {"kind": "project"}, **clock},
        ],
        "clip_uses": [
            {"id": "use-av", "asset_id": "asset-av",
             "source_interval": {"clock_id": "clock-av", "start": 30, "end": 60},
             "timeline_interval": {"clock_id": "clock-project", "start": 0, "end": 30},
             "entity_links": [], "role": "a_roll", "intent_id": None},
            {"id": "use-cap", "asset_id": "asset-cap",
             "source_interval": {"clock_id": "clock-av", "start": 0, "end": 30},
             "timeline_interval": {"clock_id": "clock-project", "start": 0, "end": 30},
             "entity_links": [], "role": "subtitle_overlay", "intent_id": None},
        ],
    }


def _mini_projection():
    source_in = 30 * ENGINE_TPS // 24
    duration = 30 * ENGINE_TPS // 24
    return {
        "adapter_version": "test-1",
        "engine_revision": "test-rev",
        "timeline_clock": "clock-project",
        "imports": [{"asset_id": "asset-av", "uri": "media/av.mp4"},
                    {"asset_id": "asset-cap", "uri": "media/cap.mov"}],
        "placements": [
            {"clip_use_id": "use-av", "asset_id": "asset-av", "role": "a_roll",
             "track": "V1", "audio_track": "A1", "frame": 0,
             "source_in_ticks": source_in, "duration_ticks": duration,
             "timeline_start": 0, "timeline_end": 30},
            {"clip_use_id": "use-cap", "asset_id": "asset-cap",
             "role": "subtitle_overlay", "track": "V3", "audio_track": None,
             "frame": 0, "source_in_ticks": 0, "duration_ticks": duration,
             "timeline_start": 0, "timeline_end": 30},
        ],
        "losses": [],
    }


def test_execute_without_preconform_never_preconforms(tmp_path, monkeypatch):
    """Default off means untouched: no transcode call, no cache dir,
    and the receipt says so."""
    import library.tools.filmcraft_preconform as fcp

    def boom(*args, **kwargs):
        raise AssertionError("pre-conform ran with the option off")

    monkeypatch.setattr(fcp, "preconform_source", boom)
    _stub_engine(monkeypatch, tmp_path)
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"
    export = tmp_path / "reel.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export)]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["preconform"] == {"enabled": False}
    assert not (root / "pipeline_output").exists()
    assert export.is_file()


def test_preconform_size_refusals_fire_before_mutation(tmp_path, monkeypatch):
    """`--preconform` without a size, and a size without the flag,
    both refuse with no engine project."""
    import library.tools.project_model_cli as pmc

    monkeypatch.setattr(pmc, "filmcraft_native", lambda: _stub_native())
    monkeypatch.setattr(
        pmc, "_filmcraft_cli",
        lambda: (_ for _ in ()).throw(
            AssertionError("no engine contact on refusal")))
    root = _project(tmp_path)
    out = tmp_path / "receipt.json"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--preconform"]) == 4
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--preconform-size", "1080x1920"]) == 4
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(FIXTURE_MODEL), "--out", str(out),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--preconform", "--preconform-size", "wide"]) == 4
    assert not list(tmp_path.glob("*.fcproj"))
    assert not out.exists()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_preconform_plan_marks_spine_ready_and_overlays_direct(tmp_path):
    """The a_roll span plans to a proxy (handles clamped to the 72-frame
    source); the overlay keeps its original with the reason named."""
    import library.tools.project_model_cli as pmc

    model = _mini_model()
    projection = _mini_projection()
    spec = pmc._preconform_spec(model, projection, 160, 90)
    planned = pmc.plan_preconform(
        model, projection,
        {"asset-av": LINKED_AV, "asset-cap": Path("/media/cap.mov")},
        spec=spec)
    ready = planned["assets"]["asset-av"]
    assert ready["status"] == "ready"
    assert (ready["use_start_frame"], ready["use_end_frame"]) == (30, 60)
    assert (ready["proxy_start_frame"], ready["proxy_end_frame"]) == (6, 72)
    assert ready["proxy_start_ticks"] == 6 * ENGINE_TPS // 24
    direct = planned["assets"]["asset-cap"]
    assert direct["status"] == "direct"
    assert "subtitle_overlay" in direct["reason"]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_preconform_execute_swaps_rebases_and_reuses_cache(tmp_path):
    """The proxy imports in place of the camera original, placements
    rebase onto its origin, and the second run is a cache hit."""
    import library.tools.project_model_cli as pmc

    model = _mini_model()
    projection = _mini_projection()
    spec = pmc._preconform_spec(model, projection, 160, 90)
    resolved = {"asset-av": LINKED_AV, "asset-cap": Path("/media/cap.mov")}
    planned = pmc.plan_preconform(model, projection, resolved, spec=spec)
    cache = tmp_path / "preconform"
    swapped, rebased, report = pmc.execute_preconform(
        planned, resolved, projection["placements"],
        spec=spec, cache_dir=cache)
    assert report["enabled"] is True
    assert report["cache_dir"] == str(cache)
    av = report["assets"]["asset-av"]
    assert av["status"] == "preconformed"
    assert av["cache_hit"] is False
    assert av["source_range_frames"] == [6, 72]
    assert Path(av["path"]).is_file()
    assert swapped["asset-av"] == Path(av["path"])
    assert swapped["asset-cap"] == Path("/media/cap.mov")
    placed = {p["clip_use_id"]: p for p in rebased}
    assert placed["use-av"]["source_in_ticks"] == (
        30 * ENGINE_TPS // 24 - 6 * ENGINE_TPS // 24)
    assert placed["use-cap"]["source_in_ticks"] == 0
    entry = report["swapback"]["preconformed"]["asset-av"]
    assert entry["original_path"] == str(LINKED_AV)
    assert entry["original_range_frames"] == [6, 72]
    _, _, second = pmc.execute_preconform(
        planned, resolved, projection["placements"],
        spec=spec, cache_dir=cache)
    rerun = second["assets"]["asset-av"]
    assert rerun["status"] == "preconformed"
    assert rerun["cache_hit"] is True
    assert rerun["path"] == av["path"]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not on PATH")
def test_execute_preconform_end_to_end_uses_cache_on_second_run(
        tmp_path, monkeypatch):
    """`--execute --preconform` through the verb: the first run
    transcodes, the second reuses every entry, and the swap-back still
    names the camera originals."""
    _native_or_skip()
    _stub_rich_engine(monkeypatch, tmp_path)
    root = _project(tmp_path)
    model = json.loads(FIXTURE_MODEL.read_text(encoding="utf-8"))
    model["edits"] = []
    model_path = tmp_path / "model-notrim.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    first = tmp_path / "first.json"
    export_one = tmp_path / "one.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(first),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export_one),
                 "--preconform", "--preconform-size", "320x180"]) == 0
    receipt = json.loads(first.read_text(encoding="utf-8"))
    report = receipt["preconform"]
    assert report["enabled"] is True
    assert report["cache_dir"] == str(
        root / "pipeline_output" / "scratch" / "preconform")
    assert {a for a, e in report["assets"].items()
            if e["status"] == "preconformed"} == {
        "asset-ada", "asset-ben", "asset-broll"}
    assert all(e["cache_hit"] is False
               for e in report["assets"].values())
    assert receipt["mastering"] == {
        "skipped": "native video export contains no audio stream"}
    for entry in report["swapback"]["preconformed"].values():
        assert entry["original_path"].endswith(".mp4")
        assert "preconform" not in entry["original_path"]
    assert export_one.is_file()
    cached = sorted((root / "pipeline_output" / "scratch"
                     / "preconform").glob("preconform-*.mov"))
    assert len(cached) == 3

    second = tmp_path / "second.json"
    export_two = tmp_path / "two.mp4"
    assert main(["project", str(root), "--engine", "filmcraft",
                 "--model", str(model_path), "--out", str(second),
                 "--media-root", str(FIXTURE_MODEL.parent),
                 "--execute", "--export", str(export_two),
                 "--preconform", "--preconform-size", "320x180"]) == 0
    rerun = json.loads(second.read_text(encoding="utf-8"))["preconform"]
    assert all(e["cache_hit"] is True
               for e in rerun["assets"].values())
    assert [e["path"] for e in rerun["assets"].values()] == [
        e["path"] for e in report["assets"].values()]
    assert export_two.is_file()
