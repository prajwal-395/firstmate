"""The effects verb reaches the installed-effects registry past both doors.

Defect caught: the provider registry #1813 populated is unreachable -
`data_capabilities`, `verified_entries` and `installed_entries` have no
in-repo caller, so no planner, recipe or agent could ask what is
installed. `ren effects` is the registry's first reader.
"""
from pathlib import Path

import ren.commands as ren_commands
from ren.commands import VERBS

from library.tools import resolvefx_effects

REPO = Path(__file__).resolve().parents[3]


def test_the_effects_verb_is_registered():
    by_name = {verb.name: verb for verb in VERBS}
    assert "effects" in by_name, "ren lost its 'effects' verb"
    assert "resolvefx_effects" in " ".join(
        by_name["effects"].module_argv), (
        "ren 'effects' no longer reaches the effects registry")


def test_the_effects_verb_is_registered_in_both_front_doors():
    """A verb the Python list knows but the native list does not is
    unreachable - the native front door refuses it before exec'ing
    the implementation (`test_schedule_verb_registration` pins the
    same for the fabric verbs)."""
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "effects"' in native


SUMMARY = {
    "version": "21.1",
    "effects": [
        {"id": "OfxGaussian", "name": "Gaussian Blur"},
        {"id": "OfxGrain", "name": "Film Grain"},
        {"id": "OfxMagic", "name": "Magic Mask"},
    ],
}


def _use_cache(tmp_path: Path, monkeypatch) -> None:
    resolvefx_effects.clear_cache()
    monkeypatch.setattr(resolvefx_effects, "CACHE_PATH",
                        tmp_path / "resolvefx_effects.json")


def _mock_fetch(monkeypatch, summary: dict = SUMMARY) -> None:
    monkeypatch.setattr(resolvefx_effects, "_fetch_registry_summary",
                        lambda: summary)


def test_search_names_the_route_and_the_verification_state(
        tmp_path, monkeypatch, capsys):
    """The apply step decides on the route and the verification state,
    so the verb prints both: an entry without them is a name the
    recipe cannot act on."""
    _use_cache(tmp_path, monkeypatch)
    _mock_fetch(monkeypatch)
    assert resolvefx_effects.main(["blur"]) == 0
    out = capsys.readouterr().out
    assert "Gaussian Blur" in out
    assert "[ui]" in out
    assert "unverified" in out


def test_search_shows_the_api_route_where_one_exists(
        tmp_path, monkeypatch, capsys):
    """An api-application effect names its measured scripting method -
    the one route that needs no UI recipe. A verb hiding it sends the
    planner to drive the UI for an effect the API reaches."""
    _use_cache(tmp_path, monkeypatch)
    _mock_fetch(monkeypatch)
    assert resolvefx_effects.main(["magic mask"]) == 0
    out = capsys.readouterr().out
    assert "Magic Mask" in out
    assert "TimelineItem.CreateMagicMask" in out


def test_empty_query_lists_everything(tmp_path, monkeypatch, capsys):
    _use_cache(tmp_path, monkeypatch)
    _mock_fetch(monkeypatch)
    assert resolvefx_effects.main([]) == 0
    out = capsys.readouterr().out
    assert "Gaussian Blur" in out
    assert "Film Grain" in out


def test_no_match_is_a_refusal_naming_the_query(
        tmp_path, monkeypatch, capsys):
    """A search that matches nothing exits nonzero naming the query -
    an empty listing would read as 'no effects installed' and send
    the planner to install plugins."""
    _use_cache(tmp_path, monkeypatch)
    _mock_fetch(monkeypatch)
    assert resolvefx_effects.main(["zzzznonexistent"]) == 1
    assert "zzzznonexistent" in capsys.readouterr().out


def test_no_broker_names_the_broker(tmp_path, monkeypatch, capsys):
    """With no broker serving there is no installed list to search -
    the verb refuses with the fix instead of tracing back."""
    _use_cache(tmp_path, monkeypatch)
    from library.tools.resolved import client
    monkeypatch.setattr(client, "serving", lambda *a, **k: False)
    assert resolvefx_effects.main(["blur"]) == 1
    assert "broker" in capsys.readouterr().out
