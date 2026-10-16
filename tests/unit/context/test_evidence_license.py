"""License provenance on evidence rows, against a fixture registry.

The registry file is engine truth owned by the registry lane and its
contents move as license evidence lands; these tests pin the
fail-closed boundary against a fixture instead, so a truthful status
change in the live snapshot cannot read as a regression here. The
fixture covers every approval status the boundary must handle:
approved, conditional (usable with its conditions recorded),
rejected (research-only), and unresolved (quarantined).
"""
import pytest

from library.tools import evidence_license
from library.tools.evidence_license import (
    authorized_for,
    implementation_license,
    reuse_allowed,
    stamp_license,
)

APPROVED = "fixture.approved_detector"
CONDITIONAL = "fixture.conditional_detector"
RESEARCH_ONLY = "fixture.research_detector"
QUARANTINED = "fixture.quarantined_detector"

PIN_CONDITION = "pin the loaded artifact revision before client work"


def _evidence(status, obligations=()):
    return {
        "upstream_url": "https://example.invalid/model",
        "exact_version": "rev-1",
        "retrieved_at": "2026-10-16",
        "license": "apache-2.0",
        "obligations": list(obligations),
        "approval_status": status,
    }


def _checkpoint():
    return {
        "version": "rev-1",
        "weights_hash": "sha256:abc",
        "provider": "example",
        "conversion": None,
        "runtime": "mlx",
    }


def _fixture_registry():
    return {
        "schema_version": 1,
        "capabilities": [
            {"id": "visual.detect", "description": "fixture detection"},
        ],
        "implementations": [
            {
                "id": APPROVED,
                "component_id": "model.clip_search",
                "label": "fixture approved",
                "capabilities": ["visual.detect"],
                "checkpoint": _checkpoint(),
                "license_evidence": _evidence("approved"),
                "purposes": ["commercial_client", "personal_noncommercial",
                             "permitted_research"],
                "editions": ["commercial", "personal"],
                "qualification": "qualified",
            },
            {
                "id": CONDITIONAL,
                "component_id": "model.clip_search",
                "label": "fixture conditional",
                "capabilities": ["visual.detect"],
                "checkpoint": _checkpoint(),
                "license_evidence": _evidence("conditional", [PIN_CONDITION]),
                "purposes": ["commercial_client", "personal_noncommercial",
                             "permitted_research"],
                "editions": ["commercial", "personal"],
                "qualification": "qualified",
            },
            {
                "id": RESEARCH_ONLY,
                "component_id": "model.audio_flamingo_next",
                "label": "fixture research-only",
                "capabilities": ["visual.detect"],
                "checkpoint": _checkpoint(),
                "license_evidence": _evidence("rejected",
                                             ["noncommercial research only"]),
                "purposes": ["personal_noncommercial", "permitted_research"],
                "editions": ["personal"],
                "qualification": "qualified",
            },
            {
                "id": QUARANTINED,
                "component_id": "model.clip_search",
                "label": "fixture quarantined",
                "capabilities": ["visual.detect"],
                "checkpoint": _checkpoint(),
                "license_evidence": _evidence("unresolved"),
                "purposes": ["permitted_research"],
                "editions": ["personal"],
                "qualification": "unknown",
            },
        ],
    }


def _rows():
    return [{"id": "o1", "producer": "fixture", "producer_version": "rev-1"},
            {"id": "o2", "producer": "fixture", "producer_version": "rev-1"}]


def test_verdict_names_status_editions_and_checkpoint():
    registry = _fixture_registry()
    verdict = implementation_license(APPROVED, registry)
    assert verdict["approval_status"] == "approved"
    assert "commercial" in verdict["editions"]
    assert "commercial_client" in verdict["purposes"]
    assert verdict["checkpoint"]["checkpoint"]["version"] == "rev-1"
    with pytest.raises(KeyError):
        implementation_license("no.such_model", registry)


def test_manifest_route_matches_registry_truth():
    registry = _fixture_registry()
    assert authorized_for(APPROVED, "commercial", "commercial_client",
                          registry) is True
    assert authorized_for(CONDITIONAL, "commercial", "commercial_client",
                          registry) is True
    assert authorized_for(RESEARCH_ONLY, "commercial", "commercial_client",
                          registry) is False
    assert authorized_for(RESEARCH_ONLY, "personal",
                          "permitted_research", registry) is True
    assert authorized_for(QUARANTINED, "personal", "permitted_research",
                          registry) is False


def test_stamp_copies_and_names_purpose():
    registry = _fixture_registry()
    rows = _rows()
    stamped = stamp_license(rows, APPROVED, "commercial_client", registry)
    assert rows[0].get("license_context") is None
    assert stamped[0]["license_context"]["generated_under_purpose"] == (
        "commercial_client")
    assert stamped[0]["license_context"]["approval_status"] == "approved"
    with pytest.raises(ValueError):
        stamp_license(rows, APPROVED, "nope", registry)


def test_conditional_stamp_carries_its_conditions():
    registry = _fixture_registry()
    stamped = stamp_license(_rows(), CONDITIONAL, "commercial_client",
                            registry)
    assert stamped[0]["license_context"]["approval_status"] == "conditional"
    assert stamped[0]["license_context"]["conditions"] == [PIN_CONDITION]


def test_reuse_is_fail_closed():
    registry = _fixture_registry()
    for implementation_id in (APPROVED, CONDITIONAL):
        permitted = reuse_allowed(
            stamp_license(_rows(), implementation_id, "commercial_client",
                          registry),
            "commercial_client", edition="commercial", registry=registry)
        assert permitted["verdict"] == "reuse_permitted"
        assert len(permitted["allowed"]) == 2
    missing = reuse_allowed(_rows(), "commercial_client",
                            edition="commercial", registry=registry)
    assert missing["verdict"] == "reuse_refused"
    assert missing["refused"][0]["reason"] == "no license context recorded"


def test_research_weights_never_cross_into_client_work():
    registry = _fixture_registry()
    stamped = stamp_license(_rows(), RESEARCH_ONLY, "permitted_research",
                            registry)
    verdict = reuse_allowed(stamped, "commercial_client",
                            edition="commercial", registry=registry)
    assert verdict["verdict"] == "reuse_refused"
    assert verdict["allowed"] == []
    research = reuse_allowed(stamped, "permitted_research",
                             edition="personal", registry=registry)
    assert research["verdict"] == "reuse_permitted"


def test_quarantined_weights_authorize_nothing():
    registry = _fixture_registry()
    stamped = stamp_license(_rows(), QUARANTINED, "permitted_research",
                            registry)
    for purpose, edition in (("permitted_research", "personal"),
                             ("commercial_client", "commercial")):
        verdict = reuse_allowed(stamped, purpose, edition=edition,
                                registry=registry)
        assert verdict["verdict"] == "reuse_refused"


def test_unknown_purpose_refuses_at_the_door():
    with pytest.raises(ValueError):
        reuse_allowed([], "clientish")


def test_fixture_registry_passes_registry_validation():
    from library.tools import model_registry

    registry = _fixture_registry()
    model_registry.validate_registry(registry)
    commercial = model_registry.manifest("commercial", "commercial_client",
                                         registry)
    personal = model_registry.manifest("personal", "commercial_client",
                                       registry)
    assert set(commercial["capabilities"]) <= set(personal["capabilities"]) \
        == {"visual.detect"}
