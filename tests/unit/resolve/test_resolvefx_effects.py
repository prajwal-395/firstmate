"""Tests for the ResolveFX/OFX effects provider registry.

Pins behaviour this change creates: lookup by intent words and schema
validation. Uses a small fixture, not the full registry. Does not pin
counts or registry sizes.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from library.schemas.resolvefx_effect import (
    EffectEntry,
    EffectParameter,
    SchemaError,
    validate_registry,
)
from library.tools import resolvefx_effects


@pytest.fixture()
def fixture_path(tmp_path: Path) -> Path:
    """A small fixture registry for testing."""
    fixture = {
        "schema_version": "resolvefx_effects/1",
        "entries": [
            {
                "id": "gaussian_blur",
                "name": "Gaussian Blur",
                "category": "blur",
                "application": "ui",
                "description": "ResolveFX effect: Gaussian Blur",
                "api_method": None,
                "parameters": [],
                "installed": True,
                "installed_evidence": "Fusion.GetRegSummary(0) read back",
                "installed_resolve_versions": ["21.1"],
                "verified": False,
                "evidence": None,
                "resolve_versions": [],
                "intent_words": ["gaussian", "blur"],
            },
            {
                "id": "film_grain",
                "name": "Film Grain",
                "category": "damage",
                "application": "ui",
                "description": "ResolveFX effect: Film Grain",
                "api_method": None,
                "parameters": [],
                "installed": True,
                "installed_evidence": "Fusion.GetRegSummary(0) read back",
                "installed_resolve_versions": ["21.1"],
                "verified": False,
                "evidence": None,
                "resolve_versions": [],
                "intent_words": ["film", "grain"],
            },
            {
                "id": "magic_mask",
                "name": "Magic Mask",
                "category": "ai",
                "application": "api",
                "description": "ResolveFX effect: Magic Mask",
                "api_method": "TimelineItem.CreateMagicMask",
                "parameters": [],
                "installed": True,
                "installed_evidence": "Fusion.GetRegSummary(0) read back",
                "installed_resolve_versions": ["21.1"],
                "verified": False,
                "evidence": None,
                "resolve_versions": [],
                "intent_words": ["magic", "mask"],
            },
        ],
    }
    path = tmp_path / "registry.json"
    with open(path, 'w') as f:
        json.dump(fixture, f)
    return path


@pytest.fixture(autouse=True)
def _clear_cache():
    resolvefx_effects.clear_cache()
    yield
    resolvefx_effects.clear_cache()


class TestFindByIntent:
    def test_finds_blur_by_intent_word(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        results = resolvefx_effects.find_by_intent("blur", entries)
        ids = [e.id for e in results]
        assert "gaussian_blur" in ids

    def test_finds_film_grain_by_intent_word(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        results = resolvefx_effects.find_by_intent("film grain", entries)
        ids = [e.id for e in results]
        assert "film_grain" in ids

    def test_finds_magic_mask_by_intent_word(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        results = resolvefx_effects.find_by_intent("mask", entries)
        ids = [e.id for e in results]
        assert "magic_mask" in ids

    def test_empty_query_returns_all(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        results = resolvefx_effects.find_by_intent("", entries)
        assert len(results) == len(entries)

    def test_no_match_returns_empty(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        results = resolvefx_effects.find_by_intent("zzzznonexistent", entries)
        assert results == []

    def test_results_sorted_by_relevance(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        results = resolvefx_effects.find_by_intent("blur", entries)
        assert len(results) > 0
        top = results[0]
        assert "blur" in top.name.lower() or "blur" in top.category


class TestGetById:
    def test_gets_existing_entry(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        entry = resolvefx_effects.get_by_id("gaussian_blur", entries)
        assert entry is not None
        assert entry.name == "Gaussian Blur"

    def test_returns_none_for_missing(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        entry = resolvefx_effects.get_by_id("nonexistent_effect", entries)
        assert entry is None


class TestInstalledSplit:
    def test_installed_entries_have_evidence(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        for entry in resolvefx_effects.installed_entries(entries):
            assert entry.installed is True
            assert entry.installed_evidence is not None
            assert len(entry.installed_resolve_versions) > 0

    def test_uninstalled_entries_have_no_evidence(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        for entry in entries:
            if not entry.installed:
                assert entry.installed_evidence is None

    def test_api_entries_are_installed(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        entry = resolvefx_effects.get_by_id("magic_mask", entries)
        assert entry.installed is True
        assert entry.application == "api"

    def test_ui_entries_are_installed(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        entry = resolvefx_effects.get_by_id("gaussian_blur", entries)
        assert entry.installed is True
        assert entry.application == "ui"


class TestVerifiedSplit:
    def test_verified_entries_have_evidence(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        for entry in resolvefx_effects.verified_entries(entries):
            assert entry.verified is True
            assert entry.evidence is not None
            assert len(entry.resolve_versions) > 0

    def test_unverified_entries_have_no_evidence(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        for entry in entries:
            if not entry.verified:
                assert entry.evidence is None


class TestSchemaValidation:
    def test_valid_entry_passes(self):
        entry = EffectEntry(
            id="test_effect",
            name="Test Effect",
            category="test",
            application="api",
            api_method="Timeline.TestMethod",
            installed=True,
            installed_evidence="Fusion.GetRegSummary(0) read back",
            installed_resolve_versions=["21.1"],
            verified=True,
            evidence="Applied and read back",
            resolve_versions=["21.1"],
        )
        assert entry.id == "test_effect"

    def test_invalid_id_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="Invalid-Id",
                name="Test",
                category="test",
                application="ui",
            )

    def test_empty_name_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="test_effect",
                name="",
                category="test",
                application="ui",
            )

    def test_invalid_application_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="test_effect",
                name="Test",
                category="test",
                application="invalid",
            )

    def test_api_without_method_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="test_effect",
                name="Test",
                category="test",
                application="api",
                api_method=None,
            )

    def test_installed_without_evidence_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="test_effect",
                name="Test",
                category="test",
                application="ui",
                installed=True,
                installed_evidence=None,
            )

    def test_uninstalled_with_evidence_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="test_effect",
                name="Test",
                category="test",
                application="ui",
                installed=False,
                installed_evidence="some_ref",
            )

    def test_verified_without_evidence_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="test_effect",
                name="Test",
                category="test",
                application="ui",
                verified=True,
                evidence=None,
            )

    def test_unverified_with_evidence_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="test_effect",
                name="Test",
                category="test",
                application="ui",
                verified=False,
                evidence="some_ref",
            )

    def test_verified_without_resolve_versions_rejected(self):
        with pytest.raises(SchemaError):
            EffectEntry(
                id="test_effect",
                name="Test",
                category="test",
                application="ui",
                verified=True,
                evidence="some_ref",
                resolve_versions=[],
            )

    def test_duplicate_ids_rejected(self):
        entries = [
            EffectEntry(
                id="dup",
                name="First",
                category="test",
                application="ui",
            ),
            EffectEntry(
                id="dup",
                name="Second",
                category="test",
                application="ui",
            ),
        ]
        with pytest.raises(SchemaError):
            validate_registry(entries)

    def test_invalid_parameter_type_rejected(self):
        with pytest.raises(SchemaError):
            EffectParameter(name="param", type="invalid_type")

    def test_enum_without_values_rejected(self):
        with pytest.raises(SchemaError):
            EffectParameter(name="param", type="enum", values=None)


class TestRegistryLoads:
    def test_registry_loads_without_error(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        assert len(entries) > 0

    def test_all_entries_have_unique_ids(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        ids = [e.id for e in entries]
        assert len(ids) == len(set(ids))

    def test_all_entries_have_names(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        for entry in entries:
            assert entry.name != ""

    def test_all_entries_have_categories(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        for entry in entries:
            assert entry.category != ""

    def test_all_entries_have_evidence_when_verified(self, fixture_path):
        entries = resolvefx_effects.load_registry(fixture_path)
        for entry in entries:
            if entry.verified:
                assert entry.evidence is not None
                assert len(entry.resolve_versions) > 0


class TestInstalledCache:
    """The per-machine installed cache: one format, version-keyed.

    The broker fetch is mocked - these tests pin the cache behaviour
    this change creates, never the registry's contents.
    """

    @pytest.fixture()
    def summary(self) -> dict:
        return {
            "version": "21.1",
            "effects": [
                {"id": "OfxGaussian", "name": "Gaussian Blur"},
                {"id": "OfxGrain", "name": "Film Grain"},
            ],
        }

    def _use_cache(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(resolvefx_effects, "CACHE_PATH",
                            tmp_path / "resolvefx_effects.json")

    def _mock_fetch(self, monkeypatch, summary: dict) -> None:
        monkeypatch.setattr(resolvefx_effects, "_fetch_registry_summary",
                            lambda: summary)

    def test_second_load_equals_first(self, tmp_path, monkeypatch, summary):
        self._use_cache(tmp_path, monkeypatch)
        self._mock_fetch(monkeypatch, summary)
        first = resolvefx_effects._load_cached_installed()
        second = resolvefx_effects._load_cached_installed()
        assert [e.to_dict() for e in first] == [e.to_dict() for e in second]

    def test_cache_holds_entries_not_raw_rows(self, tmp_path, monkeypatch,
                                               summary):
        self._use_cache(tmp_path, monkeypatch)
        self._mock_fetch(monkeypatch, summary)
        resolvefx_effects._load_cached_installed()
        with open(tmp_path / "resolvefx_effects.json", encoding="utf-8") as f:
            cached = json.load(f)
        assert cached["resolve_version"] == "21.1"
        assert isinstance(cached["entries"], list)
        assert cached["entries"][0]["name"]

    def test_version_change_rebuilds(self, tmp_path, monkeypatch, summary):
        self._use_cache(tmp_path, monkeypatch)
        self._mock_fetch(monkeypatch, summary)
        first = resolvefx_effects._load_cached_installed()
        assert first[0].installed_resolve_versions == ["21.1"]
        newer = {"version": "21.2", "effects": summary["effects"] + [
            {"id": "OfxNew", "name": "New Plugin"}]}
        self._mock_fetch(monkeypatch, newer)
        second = resolvefx_effects._load_cached_installed()
        assert second[0].installed_resolve_versions == ["21.2"]
        assert any(e.name == "New Plugin" for e in second)

    def test_no_broker_raises(self, tmp_path, monkeypatch):
        self._use_cache(tmp_path, monkeypatch)
        from library.tools.resolved import client
        monkeypatch.setattr(client, "serving", lambda *a, **k: False)
        with pytest.raises(resolvefx_effects.EffectsCacheUnavailable) as exc:
            resolvefx_effects._load_cached_installed()
        assert "broker" in str(exc.value)

    def test_lookup_miss_refreshes_once(self, tmp_path, monkeypatch, summary):
        self._use_cache(tmp_path, monkeypatch)
        calls = []

        def fetch():
            calls.append(1)
            if len(calls) == 1:
                return {"version": "21.1", "effects": []}
            return summary

        monkeypatch.setattr(resolvefx_effects, "_fetch_registry_summary", fetch)
        resolvefx_effects.load_registry()
        found = resolvefx_effects.find_by_intent("film grain")
        assert [e.name for e in found] == ["Film Grain"]
        assert len(calls) == 2
        resolvefx_effects.find_by_intent("another missing")
        assert len(calls) == 2
