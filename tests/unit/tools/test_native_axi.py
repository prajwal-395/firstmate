"""native-axi: the agent-ergonomic shape holds, and the ops it prints run.

Three invariants get permanent coverage because sibling lanes will extend
these verbs while agents read them:

1. POSITIONAL PRIMARY ARGS. Every read verb takes its one obvious primary
   argument positionally, so the obvious invocation works first try. A verb
   added without a positional fails here instead of on first real use.
2. The output contract: TOON tables with counts on stdout, errors as
   `error:` + `help:` on stdout with nothing on stderr, exit 0/1/2, and
   definitive empty states. A whole-model dump or a traceback fails here.
3. The ops are EXECUTABLE, not prose: the hermetic parity test projects the
   committed fixture, translates through `op_to_commands`, runs the pinned
   EffectCraft CLI in a tmp dir, and checks the render - the same
   verbatim-run check the scout measured. It skips, naming the missing
   build, when the pinned CLI or ffprobe is absent. No real project, no
   Resolve (AGENTS.md 8).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import native_axi
from library.tools.native_axi import (
    _ModelError,
    build_parser,
    main,
    op_to_commands,
)

ROOT = Path(__file__).resolve().parents[3]
E2E_FIXTURE = ROOT / "tests/fixtures/project_model_e2e/expected-project-model.json"
E2E_MEDIA = ROOT / "tests/fixtures/project_model_e2e/media"


# ── 1. Positional primary args ─────────────────────────────────────────

READ_VERBS = ("model", "timeline", "masks", "ops")


@pytest.mark.parametrize("verb", READ_VERBS)
def test_positional_primary_args(verb):
    """Every read verb accepts its primary argument positionally."""
    parser = build_parser()
    args = parser.parse_args([verb, "dummy-path"])
    assert args.command == verb
    assert args.path == "dummy-path"
    assert callable(args.func)


def test_swap_plan_is_flag_based_by_design():
    """swap-plan takes measurements as flags; a bare positional refuses."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["swap-plan", "dummy-path"])
    assert exc.value.code == 2


# ── 2. Output contract ─────────────────────────────────────────────────

def test_version_answers_exactly(capsys):
    assert main(["-v"]) == 0
    out, err = capsys.readouterr()
    assert out == native_axi.VERSION + "\n"
    assert err == ""


def test_home_is_content_first(capsys):
    assert main([]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "bin: " in out
    assert "description: " in out
    assert "commands[5]{command,reads}:" in out
    assert "help[" in out


def test_model_summarizes_without_dumping(capsys):
    assert main(["model", str(E2E_FIXTURE)]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "clips[3]{clip,role,source,timeline}:" in out
    assert "edits[1]{edit,operation}:" in out
    assert "renders[1]{render,frames}:" in out
    assert "use-ada,a_roll,0..48,0..36" in out
    # The whole model never reaches stdout: bounded summary only.
    assert len(out) < 2000
    assert "content_digest" not in out


def test_masks_uses_minimal_schema(capsys):
    assert main(["masks", str(E2E_FIXTURE)]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "masks[2]{mask,entity,kind}:" in out
    assert "mask-ada,person-ada,ellipse" in out
    assert "effects[1]{effect,target}:" in out


def test_empty_model_states_zero_definitively(capsys, tmp_path):
    import copy
    model = json.loads(E2E_FIXTURE.read_text(encoding="utf-8"))
    empty = copy.deepcopy(model)
    for key in ("clip_uses", "edits", "renders", "effects", "masks"):
        empty[key] = []
    path = tmp_path / "empty-model.json"
    path.write_text(json.dumps(empty), encoding="utf-8")
    assert main(["model", str(path)]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "clips: 0 rows" in out
    assert "edits: 0 rows" in out


def test_bad_model_fails_on_stdout_with_fix(capsys):
    assert main(["model", "/no/such/model.json"]) == 1
    out, err = capsys.readouterr()
    assert err == ""
    assert out.startswith("error: ")
    assert "help: native-axi model <model.json>" in out


def test_unknown_flag_fails_loud_on_stdout(capsys):
    assert main(["model", str(E2E_FIXTURE), "--stat"]) == 2
    out, err = capsys.readouterr()
    assert err == ""
    assert out.startswith("error: ")
    assert "help: " in out


def test_unknown_command_fails_loud_on_stdout(capsys):
    assert main(["frobnicate"]) == 2
    out, err = capsys.readouterr()
    assert err == ""
    assert "error: " in out


# ── op_to_commands unit mapping ────────────────────────────────────────

def test_op_mapping_covers_every_projectable_kind():
    identity = {"effects": {"fx": {"layer": "L", "effectcraft_id": "ec.x",
                                   "effect_index": 3}}}
    cases = [
        ({"op": "new_comp", "name": "C", "width": 96, "height": 64,
          "frame_rate": 10.0, "duration": 0.5}, "comp.new"),
        ({"op": "import_footage", "path": "m/a.mp4"}, "file.import"),
        ({"op": "add_footage_layer", "footage": "a.mp4"}, "layer.addItem"),
        ({"op": "add_mask", "layer": "L", "shape": "ellipse",
          "rect": [0, 0, 1, 1]}, "layer.addMask"),
        ({"op": "interpret_footage", "frame_rate": 12.0},
         "file.interpretFootage"),
        ({"op": "set_track_matte", "layer": "L", "matte": "M",
          "kind": "alpha"}, "layer.setTrackMatte"),
        ({"op": "apply_effect", "layer": "L", "effect_id": "ec.x",
          "params": {"saturation": -20.0}}, "effect.apply"),
        ({"op": "keyframe", "layer": "L", "path": "p", "time": 1.0,
          "value": 2.0}, "prop.addKey"),
    ]
    for op, first_command in cases:
        commands = op_to_commands(op, identity)
        assert commands[0][0] == first_command
    # apply_effect fans out: one apply plus one prop.set per param.
    applied = op_to_commands(cases[6][0], identity)
    assert [name for name, _ in applied] == ["effect.apply", "prop.set"]
    assert applied[1][1]["path"] == "effects/#3/saturation"


def test_unknown_op_refuses_fail_closed_naming_the_op():
    with pytest.raises(_ModelError, match="no CLI mapping for op 'teleport'"):
        op_to_commands({"op": "teleport"}, {})


def test_ops_refuses_unmapped_op_in_the_table(capsys, tmp_path):
    projection = {"projection": {
        "request": {"operations": [{"op": "teleport"}]},
        "identity_map": {}, "losses": [],
        "project_id": "p", "comp": {}, "renders": []}}
    path = tmp_path / "projection.json"
    path.write_text(json.dumps(projection), encoding="utf-8")
    assert main(["ops", str(path)]) == 1
    out, err = capsys.readouterr()
    assert err == ""
    assert "refused[1]{op,reason}:" in out
    assert "teleport" in out


# ── 3. Hermetic ops-execution parity ───────────────────────────────────

def _effectcraft_cli():
    from library.tools.project_model_qualification import _effectcraft_cli
    try:
        return _effectcraft_cli()
    except Exception as exc:
        pytest.skip(f"pinned EffectCraft CLI is absent ({exc})")


def test_ops_output_runs_verbatim_on_the_pinned_cli(tmp_path):
    """The printed translation executes unchanged and renders RGBA.

    Hermetic: fixture media is copied under tmp_path, the project and the
    render land under tmp_path, nothing reaches a real project or Resolve.
    """
    from library.tools.project_model_effectcraft import (
        CompSpec,
        RenderSpec,
        project_to_request,
    )
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not on this machine")
    cli = _effectcraft_cli()
    media = tmp_path / "media"
    shutil.copytree(E2E_MEDIA, media)
    model = json.loads(E2E_FIXTURE.read_text(encoding="utf-8"))
    comp = CompSpec(name="NAXI", width=96, height=64, frame_rate=10.0,
                    duration_seconds=0.5)
    projection = project_to_request(
        model, comp, [RenderSpec(format="rgba_png_sequence",
                                 output="naxi_#####.png")],
        media_root=str(tmp_path), project_id="naxi-parity")
    assert not projection.losses
    commands = []
    for op in projection.request["operations"]:
        commands.extend(op_to_commands(op, projection.identity_map))
    assert commands, "fixture must project to at least one command"
    project = tmp_path / "naxi.ecproj"
    argv = [str(cli), "run"]
    for name, params in commands:
        argv += [name, json.dumps(params)]
    argv += ["--empty", "--save-as", str(project), "--json"]
    saved = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", timeout=600, check=False)
    assert saved.returncode == 0, saved.stderr.strip()[-500:]
    assert project.is_file()
    render_out = tmp_path / "naxi_#####.png"
    rendered = subprocess.run(
        [str(cli), "render", "--project", str(project), "--comp", "NAXI",
         "--out", str(render_out), "--format", "png",
         "--start", "0", "--end", "0.5", "--channels", "rgba", "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
        check=False)
    assert rendered.returncode == 0, rendered.stderr.strip()[-500:]
    frames = sorted(tmp_path.glob("naxi_*.png"))
    assert len(frames) == comp.expected_frames()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=pix_fmt",
         "-of", "json", str(frames[0])],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        check=False)
    pix_fmt = json.loads(probe.stdout)["streams"][0]["pix_fmt"]
    assert "a" in pix_fmt


def test_timeline_reads_back_a_placed_clip(tmp_path, capsys):
    """`timeline` reports the placed clip without the inspect dump."""
    from library.tools.project_model_qualification import _filmcraft_cli
    try:
        cli = _filmcraft_cli()
    except Exception as exc:
        pytest.skip(f"pinned FilmCraft CLI is absent ({exc})")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not on this machine")
    source = tmp_path / "aroll.mp4"
    shutil.copy2(E2E_MEDIA / "person-ada-aroll.mp4", source)
    project = tmp_path / "naxi.fcproj"
    created = subprocess.run(
        [str(cli), "exec", "file.newSequence",
         json.dumps({"name": "NAXI", "width": 320, "height": 180,
                     "fps": 24.0}), "--compact",
         "--save-as", str(project)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        check=False)
    assert created.returncode == 0, created.stderr.strip()[-500:]
    ticks = 254016000000 // 24 * 48
    imported = subprocess.run(
        [str(cli), "--project", str(project), "exec", "file.import",
         json.dumps({"paths": [str(source)]}), "--compact", "--save"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        check=False)
    assert imported.returncode == 0, imported.stderr.strip()[-500:]
    item = json.loads(imported.stdout)["items"][0]
    placed = subprocess.run(
        [str(cli), "--project", str(project), "exec", "timeline.place",
         json.dumps({"item": item, "track": "V1", "frame": 0,
                     "sourceIn": 0, "duration": ticks}),
         "--compact", "--save"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        check=False)
    assert placed.returncode == 0, placed.stderr.strip()[-500:]
    clips = json.loads(placed.stdout)["clips"]
    assert main(["timeline", str(project)]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "clips[2]{clip,track,start,duration}:" in out
    assert f"{clips[0]},Video 1,0,48" in out
    # The full inspect dump never reaches stdout.
    assert "durationFrames" not in out
