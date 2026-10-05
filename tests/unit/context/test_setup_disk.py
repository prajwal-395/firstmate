"""`ren setup` refuses a disk that cannot hold Ren, before it downloads.

The defect this names (G5): a nearly full disk let `ren setup` start, and
the venv sync or a model download failed mid-way with a generic I/O
error, leaving a half-populated Ren home and no guidance. The refusal now
happens at the decision point, naming the required space and the exact
shortfall.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ren import doctor, setup


def test_the_setup_refusal_names_the_required_space_and_the_shortfall():
    """A disk that cannot hold the footprint refuses with the exact
    shortfall - not a generic I/O error from inside a download."""
    with pytest.raises(setup.SetupFailure) as refused:
        setup._check_disk_space(4_000_000_000, 14_800_000_000,
                                "Ren's runtime, models and scratch")
    message = str(refused.value)
    assert "4.0 GB free" in message
    assert "14.8 GB needed" in message
    assert "10.8 GB" in message
    assert "ren setup" in message


def test_the_setup_refuses_before_downloading_anything(tmp_path, monkeypatch,
                                                       capsys):
    """The refusal precedes every download: not one install step runs
    when the disk cannot hold the footprint."""
    called = []

    def recorded(name):
        def _record(*args, **kwargs):
            called.append(name)
        return _record

    monkeypatch.setattr(setup, "_home", lambda: tmp_path)
    monkeypatch.setattr(doctor, "free_bytes", lambda _path: 4_000_000_000)
    for name in ("_ensure_system_tools", "_runtime_python",
                 "_install_launcher", "_install_record", "_setup_runtime",
                 "_make_user_folders", "_run_packs", "_run"):
        monkeypatch.setattr(setup, name, recorded(name))

    code = setup.main([])

    assert code == 1
    assert called == [], "no install step may run after a disk refusal"
    err = capsys.readouterr().err
    assert "not enough free disk space" in err
    assert "short" in err


def test_each_pack_refuses_when_the_disk_cannot_hold_it(tmp_path, monkeypatch):
    """Before a pack's download the disk is re-checked: a pack that
    cannot fit refuses with its own shortfall rather than failing
    mid-download."""
    monkeypatch.setattr(setup, "_home", lambda: tmp_path)
    monkeypatch.setattr(doctor, "free_bytes", lambda _path: 100_000_000)
    ran = []
    monkeypatch.setattr(setup, "_run", lambda *args, **kwargs: ran.append(args))

    with pytest.raises(setup.SetupFailure) as refused:
        setup._run_packs(Path("/engine"), ["panns"], Path("/python"), {})

    assert "the panns pack" in str(refused.value)
    assert "327 MB" in str(refused.value) or "0.3 GB" in str(refused.value)
    assert ran == [], "the pack install must not start"
