"""`ren doctor` refuses what Ren cannot run on, and never crashes doing it.

Defects named here:

* A FREE Resolve must FAIL every capability that drives Resolve, by
  edition name - Studio only (captain, Q10). A doctor that only asked
  "is Resolve installed" would pass a machine on which no timeline can
  ever be built.
* Resolve NOT RUNNING, or a probe that dies, must be a FAIL line and a
  non-zero exit for those capabilities - not a traceback, which is what
  a bare `scriptapp` call inside the doctor would give.
* "Can this Mac run Ren?" was binary (punch list 22): a machine with no
  Resolve failed the doctor though it could search, analyse and plan.
  A missing need now fails only the capabilities that require it, and
  only a need every capability requires fails the doctor.
"""

from library.tools import machine_needs
from ren import doctor


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
