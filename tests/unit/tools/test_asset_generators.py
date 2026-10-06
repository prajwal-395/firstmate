"""The generative tier's behaviour: no provider hardcoded, refusal by
name when none is configured, and the best candidate chosen by score."""
from __future__ import annotations

import pytest

from library.tools import asset_generators, asset_routing
from library.tools.asset_routing import Need
from library.tools.ren_refusal import RenRefusal


class _FakeProvider(asset_generators.GeneratorProvider):
    def __init__(self, name, kinds, candidates=None):
        self.name = name
        self._kinds = set(kinds)
        self._candidates = candidates

    def kinds(self):
        return self._kinds

    def generate(self, need, context, count=1):
        if self._candidates is None:
            raise asset_generators.GeneratorRefused(
                f"{self.name} cannot generate", "not implemented",
                "implement generate")
        return self._candidates


@pytest.fixture(autouse=True)
def _clear_providers():
    from library.tools import asset_generators as ag
    saved = dict(ag._PROVIDERS)
    try:
        yield
    finally:
        ag._PROVIDERS.clear()
        ag._PROVIDERS.update(saved)


def _need(kind, params=None):
    return Need(kind, params)


def test_it_refuses_by_name_when_no_provider_is_configured():
    with pytest.raises(RenRefusal) as excinfo:
        asset_generators.generative_fill(
            _need("establishing_shot", {}), {})
    assert "no generative provider is configured" in excinfo.value.what


def test_it_refuses_by_name_when_no_provider_can_attempt_the_need():
    asset_generators.register_provider(
        _FakeProvider("ffmpeg", {"missing_frames"}))
    with pytest.raises(RenRefusal) as excinfo:
        asset_generators.generative_fill(
            _need("establishing_shot", {}), {})
    assert "no configured provider can attempt" in excinfo.value.what
    assert "ffmpeg" in excinfo.value.why


def test_it_returns_the_best_scoring_candidate():
    asset_generators.register_provider(_FakeProvider(
        "gen", {"establishing_shot"},
        candidates=[
            {"path": "/tmp/a.mp4", "score": 0.5,
             "provenance": {"derived_from": ["seed"]}},
            {"path": "/tmp/b.mp4", "score": 0.9,
             "provenance": {"derived_from": ["seed"]}},
        ]))
    outcome = asset_generators.generative_fill(
        _need("establishing_shot", {}), {})
    assert outcome["solved"] is True
    assert outcome["artifact"]["path"] == "/tmp/b.mp4"
    assert outcome["provenance"]["producer"] == "gen"
    assert outcome["provenance"]["derived_from"] == ["seed"]
    assert len(outcome["candidates"]) == 2


def test_it_refuses_when_the_provider_returns_no_candidates():
    asset_generators.register_provider(
        _FakeProvider("gen", {"establishing_shot"}, candidates=[]))
    with pytest.raises(RenRefusal) as excinfo:
        asset_generators.generative_fill(
            _need("establishing_shot", {}), {})
    assert "returned no candidates" in excinfo.value.what


def test_it_refuses_when_candidates_have_no_usable_path():
    asset_generators.register_provider(_FakeProvider(
        "gen", {"establishing_shot"},
        candidates=[{"score": 0.9}]))
    with pytest.raises(RenRefusal) as excinfo:
        asset_generators.generative_fill(
            _need("establishing_shot", {}), {})
    assert "no usable path" in excinfo.value.what


def test_registering_an_anonymous_provider_refuses():
    with pytest.raises(RenRefusal) as excinfo:
        asset_generators.register_provider(_FakeProvider("", {"image"}))
    assert "no name" in excinfo.value.what


def test_registering_a_duplicate_name_refuses():
    asset_generators.register_provider(
        _FakeProvider("gen", {"image"}))
    with pytest.raises(RenRefusal) as excinfo:
        asset_generators.register_provider(
            _FakeProvider("gen", {"image"}))
    assert "already registered" in excinfo.value.what


def test_a_provider_that_does_not_implement_generate_refuses():
    class _Bare(asset_generators.GeneratorProvider):
        name = "bare"

    with pytest.raises(RenRefusal):
        _Bare().generate(_need("image", {}), {})


def test_the_handler_is_registered_into_the_generative_tier():
    handlers = asset_routing.registered_handlers("generative")
    assert "generative_fill" in handlers
