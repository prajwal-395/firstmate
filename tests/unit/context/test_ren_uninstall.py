"""Ren uninstall removes app-owned paths without touching customer data.

The defect this names is an installer cleanup that recursively removes the
whole application home or user home, taking projects, footage, exports,
shared assets, configuration, or reusable model downloads with it.
"""

import json
from pathlib import Path

from ren import uninstall
from ren.setup import INSTALL_RECORD, PROFILE_END, PROFILE_START


def test_uninstall_keeps_customer_paths_config_and_models_by_default(
        tmp_path, monkeypatch):
    user_home = tmp_path / "User Home"
    app_home = user_home / "Library" / "Application Support" / "Ren"
    project = user_home / "Movies" / "Ren" / "projects" / "client"
    assets = user_home / "Movies" / "Ren" / "assets"
    config = user_home / ".config" / "ren"
    profile = user_home / ".zprofile"

    for path in (
        app_home / "versions" / "0.1.0+test",
        app_home / "venv-py312",
        app_home / "python",
        app_home / "node",
        app_home / "micromamba",
        app_home / "cache",
        app_home / "bin",
        app_home / "models" / "mfa",
        project,
        assets,
        config,
    ):
        path.mkdir(parents=True, exist_ok=True)
    (app_home / "current").symlink_to(Path("versions") / "0.1.0+test")
    (app_home / "bin" / "ren").write_text("# launcher\n", encoding="utf-8")
    (app_home / "models" / "mfa" / "model.bin").write_bytes(b"model")
    (project / "source-footage.mov").write_bytes(b"customer footage")
    (project / "export.mp4").write_bytes(b"customer export")
    (assets / "sound.wav").write_bytes(b"shared asset")
    (config / "config.env").write_text("PIPELINE_PROJECTS_ROOT=keep\n",
                                       encoding="utf-8")
    profile.write_text(
        "export USER_SETTING=keep\n\n"
        f"{PROFILE_START}\nexport PATH=/ren/bin:\"$PATH\"\n{PROFILE_END}\n"
        "export OTHER_SETTING=keep\n",
        encoding="utf-8")
    (app_home / INSTALL_RECORD).write_text(json.dumps({
        "schema_version": 1,
        "home": str(app_home.resolve()),
        "shell_profile": str(profile),
    }), encoding="utf-8")

    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(app_home))

    assert uninstall.main(["--yes"]) == 0

    for owned in (
        app_home / "current",
        app_home / "versions",
        app_home / "venv-py312",
        app_home / "python",
        app_home / "node",
        app_home / "micromamba",
        app_home / "cache",
        app_home / "bin" / "ren",
    ):
        assert not owned.exists()
    assert (app_home / "models" / "mfa" / "model.bin").is_file()
    assert (project / "source-footage.mov").is_file()
    assert (project / "export.mp4").is_file()
    assert (assets / "sound.wav").is_file()
    assert (config / "config.env").is_file()
    remaining_profile = profile.read_text(encoding="utf-8")
    assert PROFILE_START not in remaining_profile
    assert PROFILE_END not in remaining_profile
    assert "USER_SETTING=keep" in remaining_profile
    assert "OTHER_SETTING=keep" in remaining_profile
