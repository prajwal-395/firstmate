"""`ren doctor` refuses what Ren cannot run on, and never crashes doing it.

Two defects named here:

* A FREE Resolve must FAIL the doctor, by edition name - Studio only
  (captain, Q10). A doctor that only asked "is Resolve installed" would
  pass a machine on which no timeline can ever be built.
* Resolve NOT RUNNING, or a probe that dies, must be a FAIL line and a
  non-zero exit - not a traceback, which is what a bare `scriptapp` call
  inside the doctor would give.
"""

from ren import doctor


def test_resolve_probe_takes_lease_before_connecting():
    lease = doctor._RESOLVE_PROBE.index("with resolve_lease(")
    connect = doctor._RESOLVE_PROBE.index(
        "scriptapp_preserving_locale(dvr)")
    assert lease < connect


def _all_else_passes(monkeypatch):
    passing = doctor.Check("stub", True, "stubbed")
    for name in ("macos_check", "python_checks", "ffmpeg_check", "node_checks",
                 "model_checks", "deepfilternet_check", "config_checks",
                 "harness_check"):
        monkeypatch.setattr(doctor, name, lambda: [passing])


def test_free_edition_fails_by_name_and_exits_nonzero(monkeypatch, capsys):
    _all_else_passes(monkeypatch)
    free = {"running": True, "module": True, "connected": True,
            "product": "DaVinci Resolve", "version": "21.1.0.14"}

    code = doctor.main([], probe=lambda: free)

    out = capsys.readouterr().out
    edition = next(line for line in out.splitlines() if "Resolve edition" in line)
    assert code == 1
    assert edition.startswith("FAIL")
    assert "DaVinci Resolve 21.1.0.14 is the FREE edition" in edition
    assert "Studio" in edition


def test_resolve_not_running_is_a_fail_line_not_a_crash(monkeypatch):
    monkeypatch.setattr(doctor, "resolve_running", lambda: False)

    probe = doctor.probe_resolve(python="/nonexistent/python3")
    connection, edition = doctor.resolve_checks(probe)

    assert not connection.ok and "not running" in connection.detail
    assert not edition.ok


def test_a_probe_that_raises_is_a_fail_line(monkeypatch, capsys):
    _all_else_passes(monkeypatch)

    def dies():
        raise RuntimeError("fusionscript segfaulted")

    code = doctor.main([], probe=dies)

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


def test_deepfilter_reports_presence_without_failing(monkeypatch):
    _stub_deepfilter(monkeypatch, True)
    check = doctor.deepfilternet_check()
    assert check.ok and "deep_filter 0.5.6" in check.detail


def test_deepfilter_absence_is_reported_never_a_fail(monkeypatch):
    _stub_deepfilter(monkeypatch, False)
    check = doctor.deepfilternet_check()
    assert check.ok and "not installed" in check.detail
    assert "scripts/install_deepfilternet.sh" in check.fix


def test_deepfilter_that_would_not_run_is_a_fail_line(monkeypatch):
    _stub_deepfilter(monkeypatch, True, version=None)
    check = doctor.deepfilternet_check()
    assert not check.ok and "would not run" in check.detail
