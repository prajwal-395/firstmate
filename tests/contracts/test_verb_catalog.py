"""The verb catalog: every `ren` verb says what it touches, and a host can read it.

Defect caught: a host could not learn what a verb does to the machine
without running it or reading its source - `ren --help` was prose for a
person, so an agent guessed whether `progress` pings Resolve or `search`
reaches the network. `ren --help --json` prints each verb's `effects`,
`json` and `dry_run` off the one registry, and a verb registered without
`effects` fails here rather than shipping as an unknown.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from library.tools import operations
from ren import cli as ren_cli
from ren.commands import EFFECTS, VERBS, Verb

REPO = Path(__file__).resolve().parents[2]
NATIVE = REPO / "native" / "ren-cli" / "src"


def _undeclared(verbs) -> list:
    """Each verb whose `effects` is empty, repeats an effect, or names one
    outside the vocabulary."""
    return [verb.name for verb in verbs
            if not verb.effects
            or len(set(verb.effects)) != len(verb.effects)
            or not set(verb.effects) <= set(EFFECTS)]


def test_every_verb_declares_its_effects_from_the_vocabulary():
    assert _undeclared(VERBS) == []


def test_a_verb_added_without_effects_is_refused():
    """The check above can fail: a row with no `effects`, or an effect
    the vocabulary does not define, is named."""
    rows = (Verb("fresh", "Projects", "a verb nobody classified"),
            Verb("odd", "Projects", "an invented effect", effects=("teleport",)))
    assert _undeclared(rows) == ["fresh", "odd"]


def test_help_json_prints_the_catalog(monkeypatch, capsys):
    monkeypatch.setenv("REN_ENGINE_ROOT", str(REPO))
    assert ren_cli.main(["--help", "--json"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert set(catalog["effects"]) == set(EFFECTS)
    rows = catalog["verbs"]
    assert [row["name"] for row in rows] == [verb.name for verb in VERBS]
    assert len(rows) >= 87
    for row in rows:
        assert row["effects"], row["name"]
        assert isinstance(row["json"], bool) and isinstance(row["dry_run"], bool)
        assert row["read_only"] == (set(row["effects"]) <= {"read", "resolve_read"})


def test_ops_json_lists_every_operation(capsys):
    """`ren ops --json` IS `operations --list --json`: the verb execs
    that argv, and the listing carries every registered operation."""
    ops = {verb.name: verb for verb in VERBS}["ops"]
    assert ops.module_argv == ("library.tools.operations", "--list")
    assert ops.json
    assert operations.main([*ops.module_argv[1:], "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    names = [row["name"] for row in listing["operations"]]
    assert names == list(operations.names())
    assert listing["count"] == len(names) >= 53


def _native_rows() -> list:
    source = (NATIVE / "commands.rs").read_text(encoding="utf-8")
    return re.findall(r'Verb \{ name: "([^"]+)", group: "([^"]+)", summary: "((?:[^"\\]|\\.)*)" \}',
                      source)


def test_the_native_list_mirrors_names_and_ignores_the_catalog_fields():
    """`commands.rs` carries name, group and summary in the same order
    (its `--help` must match Python's byte for byte) and deliberately
    NOT the catalog fields: `ren --help --json` is handed to Python, so
    the catalog has one source."""
    assert [(name, group) for name, group, _ in _native_rows()] == [
        (verb.name, verb.group) for verb in VERBS]
    assert [summary.replace('\\"', '"') for _, _, summary in _native_rows()] == [
        verb.summary for verb in VERBS]
    struct = re.search(r"struct Verb \{(.*?)\}", (NATIVE / "commands.rs").read_text(
        encoding="utf-8"), re.DOTALL).group(1)
    assert "effects" not in struct and "dry_run" not in struct
    main = (NATIVE / "main.rs").read_text(encoding="utf-8")
    assert 'arg == "--json"' in main and "exec_python" in main
