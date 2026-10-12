"""`ren setup-hooks` persists `REN_OUTPUT=json` in host sessions (LE-03).

Every install runs against a fake HOME, so no real host config is
read or written. Claude Code's settings gain the variable under its
`env`; the opencode plugin defaults it; codex's `hooks.json` carries
commands, not environment, so it relies on the runtime default and
must not invent an env channel.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ren import setup_hooks  # noqa: E402


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def test_claude_install_writes_hook_and_json_output(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)

    assert setup_hooks.install("claude-code", write=True) == 0

    settings = json.loads(
        (home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "ren-marker-hook" in json.dumps(settings["hooks"])
    assert settings["env"]["REN_OUTPUT"] == "json"


def test_claude_install_keeps_existing_env_and_hook(tmp_path, monkeypatch,
                                                    capsys):
    home = _home(tmp_path, monkeypatch)
    target = home / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "env": {"REN_OUTPUT": "", "OTHER": "kept"},
        "hooks": {"SessionStart": [
            {"hooks": [{"type": "command",
                        "command": "ren-marker-hook check"}]}]}},
    ), encoding="utf-8")

    assert setup_hooks.install("claude-code", write=True) == 0

    settings = json.loads(target.read_text(encoding="utf-8"))
    assert settings["env"] == {"REN_OUTPUT": "", "OTHER": "kept"}
    assert capsys.readouterr().out.count("ren-marker-hook check") == 1


def test_claude_rerun_is_unchanged(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)

    assert setup_hooks.install("claude-code", write=True) == 0
    before = (tmp_path / "home" / ".claude" / "settings.json").read_text(
        encoding="utf-8")

    assert setup_hooks.install("claude-code", write=True) == 0
    out = capsys.readouterr().out
    assert "unchanged:" in out
    after = (tmp_path / "home" / ".claude" / "settings.json").read_text(
        encoding="utf-8")
    assert after == before


def test_opencode_plugin_defaults_output_unless_set():
    """The shipped plugin is the persistence for opencode: it defaults
    the variable only when the user set nothing (even empty wins)."""
    template = Path(setup_hooks._templates_dir()) / "opencode.js"
    body = template.read_text(encoding="utf-8")
    assert 'if (process.env.REN_OUTPUT === undefined)' in body
    assert 'process.env.REN_OUTPUT = "json"' in body


def test_codex_install_carries_no_env_channel(tmp_path, monkeypatch):
    """`hooks.json` carries commands, not environment: the install must
    not invent one there; codex sessions use the runtime default."""
    home = _home(tmp_path, monkeypatch)

    assert setup_hooks.install("codex", write=True) == 0

    data = json.loads(
        (home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "ren-marker-hook" in json.dumps(data)
    assert "REN_OUTPUT" not in json.dumps(data)
