"""Offline proof for named, selective reference inputs (plan slice 7)."""

import pytest

from library.tools import reference_collection as RC
from library.tools.scoped_intent import for_project


def test_dimensions_are_selected_explicitly_and_use_all_is_an_explicit_shortcut():
    measured = {"pacing": {"median_seconds": 2}, "palette": {"colours": ["blue"]}}
    assert RC.reference(reference_id="r1", name="Quiet", media="quiet.mp4",
                        measurements=measured)["dimensions"] == {}
    all_dims = RC.reference(reference_id="r2", name="Quiet", media="quiet.mp4",
                            measurements=measured, use_all=True)
    assert all_dims["selection_mode"] == "all"
    assert all_dims["dimensions"] == {"pacing": "like", "palette": "like"}
    with pytest.raises(RC.ReferenceCollectionError, match="use_all"):
        RC.reference(reference_id="r3", name="x", media="x.mp4",
                     measurements=measured, selections=["palette"], use_all=True)


def test_multiple_conflicting_references_negative_preference_and_provenance_reach_intent(tmp_path):
    first = RC.reference(reference_id="ref-a", name="Soft cuts", media="a.mp4",
                         measurements={"pacing": {"median_seconds": 2},
                                       "palette": {"colours": ["blue"]}},
                         selections=["pacing", "palette"])
    first = RC.set_preference(first, "palette", "dislike")
    second = RC.reference(reference_id="ref-b", name="Fast cuts", media="b.mp4",
                          measurements={"pacing": {"median_seconds": 0.5}},
                          selections=["pacing"])
    state = {"reference_collection": RC.add(RC.add(RC.empty_collection(), first), second)}
    view = for_project(str(tmp_path), "2_01", state=state,
                       now="2026-01-01T00:00:00+00:00")
    assert "reference.palette" not in view["conflicts"]
    assert "reference.pacing" in view["conflicts"]
    palette = next(row for row in view["declarations"] if row["key"] == "reference.palette")
    assert palette["value"]["preference"] == "dislike"
    assert palette["resolution"] == "eligible"
    assert "ref-a" in palette["provenance"] and "a.mp4" in palette["provenance"]


def test_missing_measurements_and_unselected_dimensions_do_not_project(tmp_path):
    missing = RC.reference(reference_id="gone", name="Missing", media="gone.mp4",
                           measurements=None, selections=["pacing"])
    cached = RC.reference(reference_id="cached", name="Cached", media="cached.mp4",
                          digest="sha256:abc",
                          measurements={"pacing": {"median_seconds": 3},
                                        "palette": {"colours": ["green"]}},
                          selections=["pacing"])
    collection = RC.add(RC.add(RC.empty_collection(), missing), cached)
    view = for_project(str(tmp_path), "2_01", state={"reference_collection": collection})
    assert list(key for key in view["values"] if key.startswith("reference.")) == ["reference.pacing"]
    declaration = next(row for row in view["declarations"] if row["key"] == "reference.pacing")
    assert "measurement_digest=sha256:abc" in declaration["provenance"]


def test_prompt_projection_is_stable_and_does_not_mutate_collection(tmp_path):
    entry = RC.reference(reference_id="r", name="Example", media="ref.mp4",
                         measurements={"framing": {"presence": 0.5}}, use_all=True)
    collection = RC.add(RC.empty_collection(), entry)
    before = repr(collection)
    state = {"reference_collection": collection}
    first = for_project(str(tmp_path), "2_01", state=state,
                        now="2026-01-01T00:00:00+00:00")
    second = for_project(str(tmp_path), "2_01", state=state,
                         now="2026-01-01T00:00:00+00:00")
    assert first == second
    assert first["values"]["reference.framing"]["measurement"] == {"presence": 0.5}
    assert repr(collection) == before


def test_measurement_reuses_digest_and_missing_media_is_explicit(monkeypatch, tmp_path):
    from library.tools import reference_style

    monkeypatch.setattr(reference_style, "reference_digest", lambda path: "same-digest")
    def forbidden(path):
        raise AssertionError("cached measurements must not be decoded again")
    monkeypatch.setattr(reference_style, "measure_reference_style", forbidden)
    existing = RC.reference(reference_id="cached", name="Cached", media="cached.mp4",
                            measurements={"pacing": {"median_seconds": 2}},
                            digest="same-digest", selections=["pacing"])
    assert RC.measure(existing, str(tmp_path))["measurement_status"] == "cached"

    monkeypatch.setattr(reference_style, "reference_digest", lambda path: "")
    missing = RC.measure(RC.reference(reference_id="gone", name="Gone",
                                       media="gone.mp4"), str(tmp_path))
    assert missing["measurement_status"] == "missing_media"
    assert missing["measurements"] is None
