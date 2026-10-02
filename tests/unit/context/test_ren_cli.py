"""One refusal shape for every `ren` verb.

A refusal is what happened, why, and the fix (the exact next command
or edit) - rendered the same way at every CLI boundary, with exit code
4. These tests pin the shape, the enforcement (a refusal without a fix
fails at the raise site), the exit-code contract, the boundary
rendering, and the three refusals the audit named as carrying no next
step.
"""
import sys
from pathlib import Path
import pytest
from library.tools import machine_needs
from ren import doctor
import builtins
import importlib.util
import os
import subprocess
import textwrap
from library.tools.paths import load_configuration
import json
from library.tools import native_mcp
from library.tools.native_mcp import NativeMcpError, call


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.ren_refusal import (
    REFUSAL_EXIT_CODE,
    RenRefusal,
)


def test_the_shape_carries_what_why_and_fix_and_requires_all_three():
    """A refusal without a why or a fix fails at the raise site."""
    refused = RenRefusal("the plan names no reel 9",
                         "a touchup never invents one",
                         "run `ren propose <project>` first")
    rendered = refused.render()
    assert rendered.startswith("ren: refused - the plan names no reel 9")
    assert "\n  why: a touchup never invents one" in rendered
    assert "\n  fix: run `ren propose <project>` first" in rendered
    assert str(refused) == rendered

    with pytest.raises(ValueError, match="fix"):
        RenRefusal("something happened", "because", "")
    with pytest.raises(ValueError, match="fix"):
        RenRefusal("something happened", "", "do this")


def test_a_refusal_stays_catchable_as_before():
    """Adopting a class changes what its message carries, never who
    catches it: every existing `except ValueError` / `except
    RuntimeError` keeps working."""
    refused = RenRefusal("w", "y", "f")
    assert isinstance(refused, ValueError)
    assert isinstance(refused, RuntimeError)
    with pytest.raises(ValueError):
        raise refused
    with pytest.raises(RuntimeError):
        raise refused


def test_ren_config_init_refuses_in_shape_with_exit_4(tmp_path, monkeypatch):
    """The `ren` boundary renders the refusal and returns 4, not 1."""
    from ren import config as ren_config

    monkeypatch.setenv("REN_CONFIG", str(tmp_path / "config.env"))
    assert ren_config.main(["--init"]) == 0
    with pytest.raises(RenRefusal) as refused:
        ren_config.main(["--init"])
    assert refused.value.fix.startswith("edit ")
    assert "ren: refused - " in str(refused.value)

    from ren import cli as ren_cli
    assert ren_cli.main(["config", "--init"]) == REFUSAL_EXIT_CODE


def test_splice_stray_entries_carry_the_fix():
    """Audit site 1: subtitle_splice's stray-entries refusal named no
    next step."""
    from library.tools.subtitle_splice import SpliceRefused, splice_plan

    stored = [{"id": "a", "spine_block_position": 1, "timeline_start": 0.0}]
    with pytest.raises(SpliceRefused) as refused:
        splice_plan(stored, [{"id": "x", "spine_block_position": 9,
                              "timeline_start": 5.0}], [1])
    assert "not in the region" in str(refused.value)
    assert refused.value.fix, "the refusal must carry the fix"
    assert "splice again" in refused.value.fix


def test_craft_role_discipline_guard_carries_the_fix():
    """Audit site 3: craft_role's discipline guard named no next step."""
    import unittest.mock as mock

    from library.tools import craft_role

    role = craft_role.CraftRole(step_id="s", discipline="",
                                addressed_as="", reads_with=("r",),
                                decides=("d",), defers=("f",))
    with mock.patch.object(craft_role, "ROLES", {"s": role}), \
         mock.patch.object(craft_role, "WITHOUT_A_DECLARED_ROLE", ()), \
         mock.patch.object(craft_role, "_REACHES_A_MODEL", frozenset({"s"})):
        with pytest.raises(RenRefusal) as refused:
            craft_role._assert_roles_account_for_every_model_reaching_step()
    assert "no discipline" in refused.value.what
    assert refused.value.fix.endswith("library/tools/craft_role.py")


def test_touchup_unknown_reel_carries_the_fix(tmp_path):
    """Audit site 2: reel_touchup's unknown-reel refusal named no next
    step."""
    import unittest.mock as mock

    from library.tools import reel_proposal, reel_touchup
    from library.tools.ren_refusal import RenRefusal as _RR

    with mock.patch.object(reel_proposal, "read_proposal",
                           return_value=[]), \
         mock.patch.object(reel_proposal, "proposal_path",
                           return_value=str(tmp_path / "proposal.json")):
        with pytest.raises(_RR) as refused:
            reel_touchup.resolve_final_name(str(tmp_path), 9)
    assert "no reel 9" in refused.value.what
    assert "ren propose" in refused.value.fix


# --------------------------------------------------------------------------
# From test_ren_doctor.py
#
# `ren doctor` refuses what Ren cannot run on, and never crashes doing it.
#
# Defects named here:
#
# * A FREE Resolve must FAIL every capability that drives Resolve, by
#   edition name - Studio only (captain, Q10). A doctor that only asked
#   "is Resolve installed" would pass a machine on which no timeline can
#   ever be built.
# * Resolve NOT RUNNING, or a probe that dies, must be a FAIL line and a
#   non-zero exit for those capabilities - not a traceback, which is what
#   a bare `scriptapp` call inside the doctor would give.
# * "Can this Mac run Ren?" was binary (punch list 22): a machine with no
#   Resolve failed the doctor though it could search, analyse and plan.
#   A missing need now fails only the capabilities that require it, and
#   only a need every capability requires fails the doctor.

def test_resolve_probe_takes_lease_before_connecting():
    lease = doctor._RESOLVE_PROBE.index("with resolve_lease(")
    connect = doctor._RESOLVE_PROBE.index(
        "scriptapp_preserving_locale(dvr)")
    assert lease < connect


def _all_else_passes(monkeypatch):
    """Every group but Resolve passes, one line per need it checks."""
    for name, attr, covers in doctor.GROUPS:
        if attr != "resolve_checks":
            monkeypatch.setattr(doctor, attr, lambda name=name, covers=covers: [
                doctor.Check(name, True, "stubbed", need=need) for need in covers])


_FREE = {"running": True, "module": True, "connected": True,
         "product": "DaVinci Resolve", "version": "21.1.0.14"}


def test_free_edition_fails_resolve_capabilities_by_name_not_the_machine(
        monkeypatch, capsys):
    """Asked for a Resolve capability, the FREE edition fails it by
    edition name and exits non-zero; asked about the machine, it limits
    the Resolve capabilities and leaves the rest available."""
    _all_else_passes(monkeypatch)

    code = doctor.main(["--for", "render.build"], probe=lambda: _FREE)
    out = capsys.readouterr().out
    edition = next(line for line in out.splitlines() if "Resolve edition" in line)
    assert code == 1
    assert edition.startswith("FAIL")
    assert "DaVinci Resolve 21.1.0.14 is the FREE edition" in edition
    assert "Studio" in edition
    assert "render.build: unavailable - missing resolve.studio" in out

    code = doctor.main([], probe=lambda: _FREE)
    out = capsys.readouterr().out
    assert code == 0
    assert any(line.startswith("MISS  Resolve edition") for line in out.splitlines())
    unavailable = [line.split()[1] for line in out.splitlines()
                   if line.strip().startswith("unavailable")]
    assert "render.build" in unavailable and "reel.touchup" in unavailable
    assert "footage.search" not in unavailable


def test_a_missing_baseline_need_fails_the_doctor(monkeypatch, capsys):
    _all_else_passes(monkeypatch)
    monkeypatch.setattr(doctor, "python_checks", lambda: [doctor.Check(
        "Python core", False, "1 not satisfied: pyyaml", need="python.core")])

    code = doctor.main([], probe=lambda: _FREE)

    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  Python core" in out
    assert "available    " not in out


def test_for_a_capability_that_needs_no_resolve_never_probes_it(monkeypatch, capsys):
    _all_else_passes(monkeypatch)

    def probe():
        raise AssertionError("footage search does not need Resolve")

    code = doctor.main(["--for", "footage.search"], probe=probe)

    assert code == 0
    assert "footage.search: available" in capsys.readouterr().out


def test_every_need_is_checked_by_some_doctor_line():
    checked = {need for _name, _fn, covers in doctor.GROUPS for need in covers}
    assert set(machine_needs.NEEDS) - checked == set(), (
        "a need no doctor line checks would always read as available")


def test_resolve_down_or_a_dying_probe_is_a_fail_line_not_a_crash(
        monkeypatch, capsys):
    """A bare `scriptapp` inside the doctor would give a traceback."""
    monkeypatch.setattr(doctor, "resolve_running", lambda: False)
    probe = doctor.probe_resolve(python="/nonexistent/python3")
    connection, edition = doctor.resolve_checks(probe)
    assert not connection.ok and "not running" in connection.detail
    assert not edition.ok

    _all_else_passes(monkeypatch)

    def dies():
        raise RuntimeError("fusionscript segfaulted")

    code = doctor.main(["--for", "reel.touchup"], probe=dies)
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  Resolve scripting" in out and "fusionscript segfaulted" in out


def _stub_deepfilter(monkeypatch, usable, binary="/vep/bin/deep-filter",
                     version="deep_filter 0.5.6"):
    import subprocess

    import library.tools.shared_environment as se
    monkeypatch.setattr(se, "deepfilter_available", lambda: (usable, ""))
    monkeypatch.setattr(se, "deepfilter_binary", lambda *args: binary)

    def run(argv, **kwargs):
        assert argv[0] == binary
        if version is None:
            raise OSError("exec format error")
        return subprocess.CompletedProcess(argv, 0, version + "\n", "")

    monkeypatch.setattr(doctor.subprocess, "run", run)


def test_deepfilter_present_absent_or_broken(monkeypatch):
    """Present reports its version; absent DEGRADES `audio_mix.resolve`
    and never fails the doctor; installed but not runnable is a FAIL."""
    _stub_deepfilter(monkeypatch, True)
    check = doctor.deepfilternet_check()
    assert check.ok and "deep_filter 0.5.6" in check.detail

    _stub_deepfilter(monkeypatch, False)
    check = doctor.deepfilternet_check()
    assert not check.ok and "not installed" in check.detail
    assert "scripts/install_deepfilternet.sh" in check.fix
    assert doctor.required_failures([check]) == []
    verdict, _missing, degraded = doctor.capability_report([check])["audio_mix.resolve"]
    assert verdict == machine_needs.DEGRADED and "deepfilter" in degraded

    _stub_deepfilter(monkeypatch, True, version=None)
    check = doctor.deepfilternet_check()
    assert not check.ok and "would not run" in check.detail


def test_doctor_passes_without_obsolete_or_optional_models(tmp_path, monkeypatch,
                                                            capsys):
    from library.tools import heard_speech, shared_environment

    passing = doctor.Check("stub", True, "stubbed")
    for name in ("macos_check", "python_checks", "ffmpeg_check", "node_checks",
                 "deepfilternet_check", "config_checks", "harness_check"):
        monkeypatch.setattr(doctor, name, lambda: [passing])
    monkeypatch.setattr(doctor, "resolve_checks", lambda _probe: [passing])
    monkeypatch.setattr(doctor, "hf_hub_cache", lambda: tmp_path)
    gemma = "mlx-community/gemma-4-12b-it-4bit"
    monkeypatch.setattr(
        doctor, "hf_model_state",
        lambda ident, _cache: (ident == gemma, 100, "not cached"))
    monkeypatch.setattr(doctor, "torch_checkpoints", lambda: tmp_path / "torch")
    monkeypatch.setattr(doctor, "resolve_interpreter", lambda: ("python", ""))
    monkeypatch.setattr(shared_environment, "panns_available",
                        lambda: (False, "not installed"))
    monkeypatch.setattr(shared_environment, "panns_checkpoint",
                        lambda: tmp_path / "panns.ckpt")
    monkeypatch.setattr(heard_speech, "available", lambda: (True, "/bin/da"))
    monkeypatch.setattr(shared_environment, "mfa_available",
                        lambda: (True, "MFA available"))

    code = doctor.main([], probe=lambda: {})

    output = capsys.readouterr().out
    assert code == 0
    assert "model all-MiniLM-L6-v2" in output
    assert "lexical-only search remains available" in output
    assert "faster-whisper large-v3" not in output
    assert "wav2vec2 aligner" not in output
    assert "transcriber Voz (da)" in output
    assert "MFA aligner" in output


def test_doctor_requires_voz_and_mfa_without_claiming_a_fallback(monkeypatch):
    from library.tools import heard_speech, shared_environment

    monkeypatch.setattr(heard_speech, "available",
                        lambda: (False, "da is not on PATH"))
    monkeypatch.setattr(
        shared_environment, "mfa_available",
        lambda: (False, shared_environment.mfa_missing_message()))

    checks = doctor.transcription_checks()
    by_name = {check.name: check for check in checks}

    assert not by_name["transcriber Voz (da)"].ok
    assert not by_name["MFA aligner"].ok
    assert "no wav2vec2 fallback" in by_name["MFA aligner"].detail
    assert "takes over" not in by_name["MFA aligner"].detail


# --------------------------------------------------------------------------
# From test_cli_ml_preflight.py
#
# The ML preflight belongs to the commands that need it.
#
# The captain could not open the review dashboard on 2026-08-26 because
# `manage_project.py` checked for `mlx_vlm`, `easyocr` and
# `torch` at IMPORT time, before argparse had seen the command.  The fix
# moved the check to the commands that need it.  (P2 retired the dashboard
# itself; the preflight design it forced stays. whisperx was a fourth
# member until 2026-09-24, when it left the venv and the manifest with
# its fallback arms.)
#
# These tests hold the halves of the fix that remain:
#
#    1. Every command except the ones in ML_DEPENDENT_COMMANDS is served
#       with the whole ML stack unimportable; both `run` and `build-reels`
#       refuse before entering their ML-dependent work.
#    2. The advice names a path that is really on disk.
#    3. A project kept outside PROJECTS_ROOT is openable by path, and a
#       slug that is not there says which root was searched and what was
#       in it.

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_cli():
    """Import manage_project.py under its own name.

    The point of the fix is that this import performs no dependency
    check, so importing it here is itself part of the assertion.
    """
    spec = importlib.util.spec_from_file_location(
        "manage_project_under_test", REPO_ROOT / "manage_project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


# ── The ML stack, made unimportable ─────────────────────────────

ML_PACKAGES = ("mlx_vlm", "easyocr", "torch", "torchaudio")


@pytest.fixture
def ml_stack_absent(monkeypatch):
    """Make every ML package raise ImportError, present or not.

    This machine really does have torch, easyocr and mlx_vlm installed,
    so blocking one alone would not exercise the case the fix is
    about.  Blocking all of them proves the CLI reaches none of them.
    """
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in ML_PACKAGES:
            raise ImportError(f"blocked by test: {root}")
        return real_import(name, *args, **kwargs)

    for pkg in ML_PACKAGES:
        for mod in [m for m in list(sys.modules) if m.split(".")[0] == pkg]:
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)
    return blocked


# ── 1. Only the ML-dependent commands are checked ───────────────


def test_preflight_passes_legitimate_commands_without_refusing(
        ml_stack_absent, monkeypatch):
    """B1 collapse: the thin preflight wrappers in one test - every
    non-ML command passes unchecked, and a compliant environment passes
    `run` too."""
    for command in [c for c in cli.ALL_COMMANDS
                    if c not in cli.ML_DEPENDENT_COMMANDS]:
        cli.preflight_check(command)
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("mlx_vlm",))
    monkeypatch.setattr(cli, "_missing_ml_packages", lambda: [])
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "0.7.2" if dist == "mlx-vlm" else "1.0.0")
    cli.preflight_check("run")  # must not raise
    cli.preflight_check("build-reels")  # same compliant interpreter


@pytest.mark.parametrize("command", ["run", "build-reels"])
def test_ml_dependent_commands_refuse_when_the_stack_is_absent(
        command, ml_stack_absent, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.preflight_check(command)
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "mlx_vlm" in out
    # It says which commands still work, so a reader is not stuck.
    assert "status" in out


def test_management_help_does_not_advertise_retired_full_auto_values():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "manage_project.py"), "run", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False)
    assert result.returncode == 0
    assert "--full-auto BACKEND" in result.stdout
    assert "agy" not in result.stdout
    assert "api" not in result.stdout


def test_management_parser_keeps_hidden_compatibility_values():
    assert cli._full_auto_backend("agy") == "agy"
    assert cli._full_auto_backend("api") == "api"


# ── 2. The advice names something real ──────────────────────────

def test_advice_names_the_activate_script_that_exists(tmp_path):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "activate").write_text("# activate\n", encoding="utf-8")

    lines = cli._venv_advice(tmp_path)
    named = [tok for line in lines for tok in line.split() if ".venv" in tok]
    assert named, lines
    for token in named:
        assert Path(token).exists(), f"advice names a path that does not exist: {token}"


def test_advice_sends_the_reader_to_the_ONE_location(tmp_path, monkeypatch):
    """The old message told the captain to build a venv per checkout.

    That is 4 GB of the same packages per lane, it dies with the lane,
    and `docs/ML_ENVIRONMENT.md` had already moved the real environment
    outside every checkout for exactly that reason - the advice simply
    had not followed it (docs/SHARED_ENVIRONMENT.md).
    """
    from library.tools import shared_environment

    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    lines = cli._venv_advice(tmp_path)
    text = "\n".join(lines)
    durable = str(tmp_path / "vep" / shared_environment.DURABLE_VENV_DIRNAME)
    assert durable in text, "the advice does not name the per-machine location"
    assert text.index(durable) < text.index(str(tmp_path / ".venv")), (
        "the per-checkout venv is offered before the shared one")
    assert "ONCE PER MACHINE" in text
    assert "ML_ENVIRONMENT.md" in text
    # 3.12 EXPLICITLY. Bare `python3` is 3.14 on the build machine, and
    # following that instruction rebuilds the environment
    # requirements.txt forbids - see test_advice_does_not_send_anyone_to
    # _a_forbidden_interpreter below.
    assert "python3.12 -m venv" in text
    assert "requirements.txt" in text
    # It must NOT hand over a bare `source .venv/bin/activate`: that is
    # the instruction that produced the captain's second error.
    assert "    source .venv/bin/activate" not in text


# ── 3. The child-interpreter blocker ──────────────────────────────

# The guard the child interpreters install, as the first thing they run.
#
# NOT a sitecustomize.py on PYTHONPATH: that shadows the interpreter's
# own sitecustomize, and on a Homebrew python that is the module which
# puts /opt/homebrew/lib/pythonX.Y/site-packages on sys.path - so the
# blocker silently took unrelated packages away with it and the test
# failed for a reason that had nothing to do with the ML stack.
#
# A meta path finder rather than a wrapped builtins.__import__, because
# it covers importlib.import_module too.
_BLOCKER = textwrap.dedent(f"""
    import sys
    _blocked = {ML_PACKAGES!r}
    class _Block:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in _blocked:
                raise ImportError("blocked by test: " + name)
            return None
    sys.meta_path.insert(0, _Block())
    for _m in [m for m in list(sys.modules) if m.split(".")[0] in _blocked]:
        del sys.modules[_m]
""")


def _child(code: str, *argv, cwd=None, env=None):
    """Run `code` in a fresh interpreter with the ML stack unimportable."""
    child_env = dict(env or os.environ)
    child_env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), child_env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-c", _BLOCKER + textwrap.dedent(code), *argv],
        capture_output=True, encoding="utf-8", env=child_env,
        cwd=str(cwd or REPO_ROOT), timeout=180)


def test_the_blocker_really_blocks():
    """Guard the guard: a blocker that stopped working would pass everything."""
    for package in ML_PACKAGES:
        proc = _child(f"import {package}")
        assert proc.returncode != 0, package
        assert f"blocked by test: {package}" in proc.stderr, proc.stderr


# ── 4. A project outside PROJECTS_ROOT ──────────────────────────


# ── 4. Importable is not the same as USABLE ─────────────────────
#
# Added 2026-09-05.  Every ML environment on the build machine carried
# whisperx 3.2.0 against a declared `whisperx>=3.8,<4`, because they were
# built on Python 3.14 - which requirements.txt forbids in its header.
# 3.2.0 imports perfectly and raises TypeError on every transcribe call;
# step 1.04 catches that per clip, so a real run produced no transcript,
# no spine and no subtitles and reported success.  The import check above
# passed the whole time, because a wrong version is not a missing one.
# (whisperx left the venv on 2026-09-24; the tests below exercise the
# same machinery with the live mlx_vlm floor, which has the same shape.)


def test_a_version_outside_the_declared_range_is_reported(monkeypatch):
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("mlx_vlm",))
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "0.3.9" if dist == "mlx-vlm" else "1.0.0")
    problems = cli._noncompliant_ml_packages(REPO_ROOT)
    assert [p[0] for p in problems] == ["mlx_vlm"]
    assert problems[0][1] == "0.3.9"


def test_a_version_inside_the_declared_range_is_not_reported(monkeypatch):
    """The check must be capable of PASSING, or it is not a check."""
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("mlx_vlm",))
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "0.7.2" if dist == "mlx-vlm" else "1.0.0")
    assert cli._noncompliant_ml_packages(REPO_ROOT) == []


def test_run_refuses_a_wrong_version_and_says_both_numbers(monkeypatch, capsys):
    monkeypatch.setattr(cli, "ML_REQUIRED_PACKAGES", ("mlx_vlm",))
    monkeypatch.setattr(cli, "_missing_ml_packages", lambda: [])
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda dist: "0.3.9" if dist == "mlx-vlm" else "1.0.0")

    with pytest.raises(SystemExit) as exit_info:
        cli.preflight_check("run")
    assert exit_info.value.code == 1

    out = capsys.readouterr().out
    assert "mlx_vlm" in out
    assert "0.3.9" in out, "the refusal must say what IS installed"
    assert ">=0.7" in out, "the refusal must say what is REQUIRED"
    # It explains the consequence, because "wrong version" reads as
    # cosmetic and this one breaks vision on every real project.
    assert "fails inside the step" in out


# --------------------------------------------------------------------------
# From test_user_config_precedence.py
#
# The per-user config beats the checkout's .env, and the environment beats both.
#
# The defect this names: with the user file loaded AFTER .env (or with
# both allowed to override), a stale per-checkout .env would silently win
# over the machine's one config, and `ren config` would report the wrong
# source.

def test_environment_then_user_file_then_dotenv(tmp_path):
    user = tmp_path / "config.env"
    user.write_text('PIPELINE_PROJECTS_ROOT="/from/user"\n'
                    "PIPELINE_SFX_LIBRARY=/from/user/sfx\n", encoding="utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text("PIPELINE_PROJECTS_ROOT=/from/dotenv\n"
                      "PIPELINE_SFX_LIBRARY=/from/dotenv/sfx\n"
                      "PIPELINE_MUSIC_LIBRARY=/from/dotenv/music\n", encoding="utf-8")
    environ = {"PIPELINE_SFX_LIBRARY": "/from/environment"}

    sources = load_configuration((user, dotenv), environ)

    assert environ["PIPELINE_SFX_LIBRARY"] == "/from/environment"
    assert sources["PIPELINE_SFX_LIBRARY"] == "environment"
    assert environ["PIPELINE_PROJECTS_ROOT"] == "/from/user"
    assert sources["PIPELINE_PROJECTS_ROOT"] == str(user)
    assert environ["PIPELINE_MUSIC_LIBRARY"] == "/from/dotenv/music"
    assert sources["PIPELINE_MUSIC_LIBRARY"] == str(dotenv)
    assert "PIPELINE_SHARED_ASSETS" not in environ  # left to the code default


# --------------------------------------------------------------------------
# From test_dotenv_encoding.py
#
# The .env must load under an ASCII locale (Resolve's Fusion
# subprocess runs Python with one). A locale-decoded .env with box-drawing
# headers once killed `apply_fusion_comps` at import and shipped a render
# with no Fusion effects. History: docs/evidence/dotenv_encoding.md.

# The exact bytes that broke it: BOX DRAWINGS LIGHT HORIZONTAL is 0xe2 0x94 0x80.
ENV_WITH_NON_ASCII = (
    "# ─── Pipeline Configuration ───\n"
    "PIPELINE_TEST_DOTENV_PROBE=/tmp/sfx\n"
    "# café - a stray accent is enough too\n"
    "PIPELINE_MUSIC_LIBRARY=/tmp/music\n"
)


def test_a_non_ascii_env_loads_under_an_ascii_locale(tmp_path):
    """Run the loader in a subprocess pinned to an ASCII locale.

    Importing `library.tools.paths` at all is half the test: the module
    loads the REPO's own .env at import time, and that file is the one
    with the box-drawing headers. Under LC_ALL=C the unfixed loader
    raises before this test's own file is ever read.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_WITH_NON_ASCII, encoding="utf-8")

    program = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, r"{REPO_ROOT}")
        from library.tools.paths import _load_dotenv
        from pathlib import Path
        import os
        _load_dotenv(Path(r"{env_file}"))
        print(os.environ.get("PIPELINE_TEST_DOTENV_PROBE", "MISSING"))
    """)

    proc = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        # PYTHONUTF8=0 is what makes this bite: without it CPython's
        # UTF-8 mode keeps locale.getpreferredencoding() at utf-8 even
        # under LC_ALL=C, and the test cannot fail. Verified: with it,
        # the preferred encoding is US-ASCII, which is what Resolve's
        # Fusion subprocess had.
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C",
             "PYTHONUTF8": "0"},
    )

    assert proc.returncode == 0, (
        f"the loader failed under an ASCII locale:\n{proc.stderr}")
    assert "UnicodeDecodeError" not in proc.stderr
    assert proc.stdout.strip() == "/tmp/sfx"


# --------------------------------------------------------------------------
# From test_native_mcp.py
#
# native_mcp: the wrapper is driven, never trusted.
#
# Every test here fakes the node child process - no Resolve, no bundle,
# no real project (AGENTS.md 8). Two behaviours get permanent coverage
# because the verbs above depend on them:
#
# 1. A refusal from the server (or silence, or a missing bundle)
#    surfaces as `NativeMcpError` carrying the server's own words, so
#    the axi layer can print `error:` plus a fix instead of hanging or
#    dumping a traceback.
# 2. The request actually sent is `tools/call` with the tool name and
#    arguments the verb asked for - the shape Blackmagic's wrapper
#    routes to its binary.

class _FakeStdin:
    def __init__(self):
        self.written = []

    def write(self, text):
        self.written.append(text)

    def flush(self):
        pass


class _FakeProc:
    def __init__(self, lines):
        self.stdin = _FakeStdin()
        self.stdout = iter(lines)
        self.killed = False
        self.argv = None

    def kill(self):
        self.killed = True


def _result(text=None, error=None, is_error=False):
    if error is not None:
        return {"jsonrpc": "2.0", "id": 2, "error": error}
    return {"jsonrpc": "2.0", "id": 2,
            "result": {"content": [{"type": "text", "text": text or ""}],
                       "isError": is_error}}


def _patch(monkeypatch, lines):
    monkeypatch.setattr(native_mcp, "wrapper_path",
                        lambda: "/fake/server/index.js")
    procs = []

    def factory(argv, **_kwargs):
        proc = _FakeProc(lines)
        proc.argv = argv
        procs.append(proc)
        return proc

    monkeypatch.setattr(native_mcp.subprocess, "Popen", factory)
    return procs


def test_call_sends_tools_call_and_returns_text(monkeypatch):
    procs = _patch(monkeypatch, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps(_result("hello")),
    ])
    assert call("get_resolve_status", {}) == "hello"
    sent = [json.loads(line) for line in procs[0].stdin.written]
    assert sent[0]["method"] == "initialize"
    assert sent[2] == {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "get_resolve_status",
                                  "arguments": {}}}
    assert procs[0].killed


def test_call_surfaces_a_refusal_in_its_own_words(monkeypatch):
    _patch(monkeypatch, [json.dumps(_result("needs a version",
                                            is_error=True))])
    with pytest.raises(NativeMcpError, match="needs a version"):
        call("get_whats_new", {})


def test_call_names_silence_instead_of_hanging(monkeypatch):
    _patch(monkeypatch, [])
    with pytest.raises(NativeMcpError, match="no answer"):
        call("get_resolve_status", {}, timeout=2)


def test_a_missing_bundle_or_node_is_a_sentence_not_a_traceback(monkeypatch):
    monkeypatch.setattr(native_mcp, "BUNDLE_PATH",
                        "/nonexistent/DaVinciResolve.mcpb")
    with pytest.raises(NativeMcpError, match="no native MCP bundle"):
        call("get_resolve_status", {})

    monkeypatch.setattr(native_mcp, "wrapper_path",
                        lambda: "/fake/server/index.js")
    monkeypatch.setattr(native_mcp.shutil, "which", lambda _name: None)
    with pytest.raises(NativeMcpError, match="no `node`"):
        call("get_resolve_status", {})
