"""Anchor generality: raw coordinates are flagged, never silent.

Each test names the defect it catches: a recipe that survives only on
the layout it was mapped on. The anchor audit is the check the
captain's brief orders - a step with a raw coordinate is flagged and
replaced, so a recipe survives window moves, panel layouts, display
scale and another Mac.
"""

from __future__ import annotations

from library.tools.computer_use.anchors import (
    TemplateProvenance,
    audit_anchors,
    window_point,
)
from library.tools.computer_use.scripts import Anchor, Script, ScriptStep


def _script(*steps: ScriptStep) -> Script:
    return Script(goal="test", steps=list(steps))


def _step(*anchors: Anchor) -> ScriptStep:
    return ScriptStep(action="click", anchors=list(anchors))


def test_a_raw_coordinate_with_no_why_is_flagged():
    """A position anchor with no `why` breaks the anchor's own rule -
    the last resort is allowed only with its reason stated. The
    audit flags it with the fix, instead of the recipe silently
    depending on one layout."""
    findings = audit_anchors(_script(_step(
        Anchor(via="position", params={"x": 100.0, "y": 100.0}))))
    assert len(findings) == 1
    assert findings[0].step == 0
    assert "no why" in findings[0].defect
    assert "AX" in findings[0].fix or "menu" in findings[0].fix


def test_a_taught_coordinate_is_flagged_for_remapping():
    """The LLM fallback teaches `position` with `why: llm` - a real
    coordinate the next run clicks blind. The audit flags every
    taught coordinate: the next mapping run owes it a measured
    anchor plus an expect."""
    findings = audit_anchors(_script(_step(
        Anchor(via="position",
               params={"x": 100.0, "y": 100.0, "why": "llm"}))))
    assert len(findings) == 1
    assert "taught" in findings[0].defect
    assert "re-map" in findings[0].fix


def test_a_measured_coordinate_with_a_why_passes():
    """A position the recipe measured at run time (the qualifier's
    image-rect mapping, the seed's scale division) with its `why`
    stated is the documented last resort - the audit passes it."""
    assert audit_anchors(_script(_step(
        Anchor(via="position", params={
            "x": 100.0, "y": 100.0,
            "why": "mapped from the measured image rect"})))) == []


def test_relative_anchors_pass_clean():
    """Menu, AX path, OCR, key, type and wait anchors resolve
    relative to what is on screen - the audit passes them: they
    survive the window moving."""
    script = _script(
        _step(Anchor(via="menu", params={"path": ["Color", "Nodes"]})),
        _step(Anchor(via="ax_path", params={"path": ["Workspace"]})),
        _step(Anchor(via="ocr", params={"text": "Master"})),
        ScriptStep(action="key",
                   anchors=[Anchor(via="key", params={"key": "return"})]),
        ScriptStep(action="wait",
                   anchors=[Anchor(via="wait", params={"seconds": 1.0})]),
    )
    assert audit_anchors(script) == []


def test_a_template_without_a_threshold_is_flagged():
    """A template match without a threshold matches at whatever the
    library default is - an unmeasured margin. The audit flags it:
    the threshold sits between the measured hit and the impostor."""
    findings = audit_anchors(_script(_step(
        Anchor(via="template", params={"image": "qpick.png"}))))
    assert len(findings) == 1
    assert "threshold" in findings[0].defect


def test_a_registry_template_with_provenance_passes():
    """A `template:` anchor naming a measured registry entry carries
    its provenance with it - threshold, cut scale, source capture -
    so the audit passes it: the entry IS the measurement."""
    findings = audit_anchors(_script(_step(
        Anchor(via="template", params={"template": "magic-mask-toolbar"}))))
    assert findings == [], [finding.defect for finding in findings]


def test_an_unknown_registry_template_is_flagged():
    """A `template:` anchor naming nothing registered resolves to
    nothing live - the audit flags it with the registry to fix."""
    findings = audit_anchors(_script(_step(
        Anchor(via="template", params={"template": "no-such-icon"}))))
    assert len(findings) == 1
    assert "unknown registry" in findings[0].defect


def test_an_offset_off_a_raw_base_is_flagged():
    """An offset is only as general as its base: offsetting off a
    raw coordinate inherits the coordinate's fragility. The audit
    flags the pair."""
    findings = audit_anchors(_script(_step(
        Anchor(via="offset", params={
            "anchor": {"via": "position",
                       "params": {"x": 1.0, "y": 2.0}},
            "dx": 55.0, "dy": 5.0}))))
    assert len(findings) == 1
    assert "non-relative" in findings[0].defect


def test_window_point_maps_the_rect_and_refuses_chrome():
    """Normalized picture coordinates map onto the measured rect -
    and a point outside 0..1 is refused instead of clicking
    chrome. One helper, so a scale bug fails in one place."""
    assert window_point((10.0, 20.0, 110.0, 60.0), 0.5, 0.5) == (60.0, 40.0)
    for bad in ((-0.1, 0.5), (0.5, 1.1), ("x", 0.5)):
        try:
            window_point((10.0, 20.0, 110.0, 60.0), *bad)
            assert False, f"should have raised for {bad}"
        except ValueError as exc:
            assert "0..1" in str(exc) or "numbers" in str(exc)


def test_template_provenance_margin_is_checked():
    """A provenance whose threshold does not sit between the impostor
    and the hit is a template that matches wrong - or never. The
    margin check says so before the recipe runs."""
    good = TemplateProvenance(image="qpick.png", threshold=0.9,
                              best_hit=1.0, best_false=0.851)
    assert good.margin_ok()
    assert not TemplateProvenance(
        image="qpick.png", threshold=0.851,
        best_hit=1.0, best_false=0.851).margin_ok()
    assert not TemplateProvenance(
        image="qpick.png", threshold=0.99,
        best_hit=0.9, best_false=0.851).margin_ok()
    assert good.to_dict()["image"] == "qpick.png"


def test_a_flagged_coordinate_is_replaced_with_its_label():
    """Flagged then REPLACED: a mapping run that read the label
    beside the raw coordinate puts the measured OCR anchor first,
    keeping the raw point as the last resort. The next replay
    resolves without touching the coordinate."""
    from library.tools.computer_use.anchors import (
        audit_anchors,
        reanchor_with_ocr,
    )

    step = _step(Anchor(
        via="position",
        params={"x": 100.0, "y": 100.0, "why": "llm"}))
    assert audit_anchors(_script(step)) != []
    reanchor_with_ocr(step, "Master")
    assert [anchor.via for anchor in step.anchors] == ["ocr", "position"]
    assert step.anchors[0].params == {"text": "Master"}
    assert audit_anchors(_script(step)) == []


def test_a_replacement_names_a_label_or_nothing():
    """A replacement with a blank label, or on a step with no raw
    coordinate, is refused - the anchor must name something the
    screen shows, never a guess."""
    from library.tools.computer_use.anchors import reanchor_with_ocr

    raw = _step(Anchor(via="position",
                       params={"x": 1.0, "y": 2.0, "why": "llm"}))
    for bad in ("", "   "):
        try:
            reanchor_with_ocr(raw, bad)
            assert False, "should have raised ValueError"
        except ValueError as exc:
            assert "label" in str(exc)
    measured = _step(Anchor(via="ocr", params={"text": "Master"}))
    try:
        reanchor_with_ocr(measured, "Master")
        assert False, "should have raised ValueError"
    except ValueError as exc:
        assert "no raw coordinate" in str(exc)
