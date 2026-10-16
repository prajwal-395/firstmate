"""License provenance on evidence rows, against the real registry.

The registry file is engine truth owned by the registry lane; these
tests consume it read-only and pin the fail-closed boundary.
"""
import pytest

from library.tools import evidence_license
from library.tools.evidence_license import (
    authorized_for,
    implementation_license,
    reuse_allowed,
    stamp_license,
)

COMMERCIAL = "minilm.all_minilm_l6_v2"
RESEARCH_ONLY = "audio_flamingo.nvidia_next"


def _rows():
    return [{"id": "o1", "producer": "minilm", "producer_version": "l6-v2"},
            {"id": "o2", "producer": "minilm", "producer_version": "l6-v2"}]


def test_verdict_names_status_editions_and_checkpoint():
    verdict = implementation_license(COMMERCIAL)
    assert verdict["approval_status"] == "approved"
    assert "commercial" in verdict["editions"]
    assert "commercial_client" in verdict["purposes"]
    with pytest.raises(KeyError):
        implementation_license("no.such_model")


def test_manifest_route_matches_registry_truth():
    assert authorized_for(COMMERCIAL, "commercial", "commercial_client") is True
    assert authorized_for(RESEARCH_ONLY, "commercial", "commercial_client") is False
    assert authorized_for(RESEARCH_ONLY, "personal",
                          "permitted_research") is True


def test_stamp_copies_and_names_purpose():
    rows = _rows()
    stamped = stamp_license(rows, COMMERCIAL, "commercial_client")
    assert rows[0].get("license_context") is None
    assert stamped[0]["license_context"]["generated_under_purpose"] == (
        "commercial_client")
    assert stamped[0]["license_context"]["approval_status"] == "approved"
    with pytest.raises(ValueError):
        stamp_license(rows, COMMERCIAL, "nope")


def test_reuse_is_fail_closed():
    permitted = reuse_allowed(
        stamp_license(_rows(), COMMERCIAL, "commercial_client"),
        "commercial_client", edition="commercial")
    assert permitted["verdict"] == "reuse_permitted"
    assert len(permitted["allowed"]) == 2
    missing = reuse_allowed(_rows(), "commercial_client", edition="commercial")
    assert missing["verdict"] == "reuse_refused"
    assert missing["refused"][0]["reason"] == "no license context recorded"


def test_research_weights_never_cross_into_client_work():
    stamped = stamp_license(_rows(), RESEARCH_ONLY, "permitted_research")
    verdict = reuse_allowed(stamped, "commercial_client", edition="commercial")
    assert verdict["verdict"] == "reuse_refused"
    assert verdict["allowed"] == []
    research = reuse_allowed(stamped, "permitted_research", edition="personal")
    assert research["verdict"] == "reuse_permitted"


def test_unknown_purpose_refuses_at_the_door():
    with pytest.raises(ValueError):
        reuse_allowed([], "clientish")
