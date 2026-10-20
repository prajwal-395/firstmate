"""The diarization licence provenance must actually build.

`single_track_diarization._license_provenance` asked
`model_registry` for `output_reuse_terms`, a function that has never
lived there: the per-evidence reuse gate put it in
`model_evidence_provenance` (PR #2669) and the diarization caller was
never updated. Every diarization run therefore died with

    AttributeError: module 'library.tools.model_registry' has no
    attribute 'output_reuse_terms'

and `ren build` crashed on it while gathering the source transcript.
"""

from __future__ import annotations

import pytest


def test_the_licence_provenance_builds():
    """Catches: the provenance step raising AttributeError, so every
    diarization run crashed before it spoke."""
    from library.tools import model_registry, single_track_diarization

    registry = model_registry.load_registry()
    implementation_id = next(
        row["id"] for row in registry["implementations"]
        if "sortformer" in row["id"])
    records = single_track_diarization._license_provenance(
        [implementation_id])
    assert len(records) == 1
    record = records[0]
    assert record["implementation_id"] == implementation_id
    assert record["output_reuse"]["class"]
    assert record["license_status"]


def test_the_output_reuse_terms_came_from_the_module_that_owns_them():
    """Catches: the provenance step calling a function on a module that
    does not have it - the key-name/module mismatch that crashed it."""
    from library.tools import model_evidence_provenance, model_registry

    assert hasattr(model_evidence_provenance, "output_reuse_terms")
    assert not hasattr(model_registry, "output_reuse_terms")


def test_the_terms_the_caller_reads_are_the_recorded_ones():
    """Catches: the provenance step substituting its own terms for the
    registry's, so a licence verdict and the reuse terms could disagree."""
    from library.tools import model_evidence_provenance, model_registry

    registry = model_registry.load_registry()
    implementation_id = next(
        row["id"] for row in registry["implementations"]
        if "sortformer" in row["id"])
    recorded = model_evidence_provenance.output_reuse_terms(
        implementation_id, registry)
    implementation = next(row for row in registry["implementations"]
                          if row["id"] == implementation_id)
    assert recorded["class"] == implementation["output_reuse"]["class"]
    assert recorded["reuse_scope"] == implementation["output_reuse"]["reuse_scope"]
