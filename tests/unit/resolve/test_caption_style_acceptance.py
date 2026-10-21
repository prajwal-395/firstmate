"""The live acceptance adapter must exercise caption.style, not refuse it."""

import ast

from library.tools import edit_request
from library.tools.subtitle_style import caption_style_operation
from tests.live import editorial_live_adapter as live_adapter


def test_restyle_captions_live_failure_is_not_a_hardcoded_design_refusal(
        monkeypatch):
    """Defect: the live run rejected restyle_captions before execution."""
    assert "restyle_captions" not in live_adapter._REFUSAL_NOTES
    monkeypatch.setattr(live_adapter, "_route_executing", lambda *args,
                        **kwargs: {"selected": "resolve_axi",
                                   "selection_correct": True})
    operations = [
        {"op": "caption.style", "unique_id": "cap-1", "style": "minimal"},
        {"op": "caption.style", "unique_id": "cap-2", "style": "minimal"},
    ]
    case = {
        "operations": operations,
        "receipt": {"status": "committed"},
        "per_caption": [
            {"unique_id": "cap-1", "verified": True,
             "text_preserved": True, "timing_preserved": True,
             "style_applied": True},
            {"unique_id": "cap-2", "verified": True,
             "text_preserved": True, "timing_preserved": True,
             "style_applied": True},
        ],
        "collateral_changes": [],
    }

    result = live_adapter._restyle_captions_evidence(
        case, 1.0, "Apply style", {"operation": "caption.style"}, {})

    assert all(result["checks"].values())
    assert result["receipt"]["status"] == "committed"
    assert len(result["observed"]["per_caption"]) == 2


def test_a_caption_style_operation_names_the_style_it_applies():
    """The builder states the style, and the style is one that exists."""
    operation = caption_style_operation("cap-1", "bold_large")

    assert operation["op"] == "caption.style"
    assert operation["unique_id"] == "cap-1"
    assert operation["style"] == "bold_large"


def test_a_caption_style_operation_without_a_style_is_still_refused():
    """The required-parameter check is the contract; it is not relaxed.

    The fix belongs at the BUILDER, so this refusal must stay: an
    operation that names no style never becomes a request.
    """
    try:
        edit_request.from_axi_operations(
            [{"op": "caption.style", "unique_id": "cap-1"}],
            project="Ren Qualification", timeline="fm-scratch qualify")
    except edit_request.EditRequestError as exc:
        assert "style" in str(exc)
    else:
        raise AssertionError(
            "caption.style without a style was accepted - the "
            "required-parameter check was relaxed")


def test_the_request_builder_accepts_the_caption_operations_it_records():
    """Defect: the recorded caption operation refused the AXI request.

    The live adapter's caption operation carried no `style`, so
    `edit_request.from_axi_operations` refused it and the request never
    reached the router.
    """
    request = edit_request.from_axi_operations(
        [caption_style_operation("cap-1", "bold_large")],
        project="Ren Qualification", timeline="fm-scratch qualify")

    operation = request.operations[0].to_algebra_op()
    assert operation["style"] == "bold_large"
    assert operation["unique_id"] == "cap-1"


def test_the_recorded_caption_operations_route_rather_than_refuse():
    """The routing of the recorded operations is a decision, not a refusal.

    A request that could not even be BUILT recorded a router refusal
    instead of a routing decision; the operations the fixture records
    must reach the router.
    """
    routed = live_adapter._route_executing(
        [caption_style_operation("cap-1", "bold_large")],
        entry="resolve_axi_core.execute_semantic_edit_job",
        basis="each caption.style card is rendered by the existing "
              "subtitles.rerender_swap path and independently verified")

    assert "router_refusal" not in routed
    routes = routed["router_routes"]
    assert {"production", "protected_experimental"} <= set(routes)
    assert "EditRequestError" not in str(routes)


def _script_sources() -> list[str]:
    """Every Python source string the live adapter module carries.

    The broker script is a string the fixture runs inside Resolve, so
    the operations it builds are read out of that string - whichever
    shape the module keeps it in.
    """
    import pathlib

    path = pathlib.Path(live_adapter.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sources = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        if ("caption.style" in node.value
                or "caption_style_operation" in node.value):
            sources.append(node.value)
    return sources


def test_the_broker_script_builds_its_caption_operations_through_the_builder():
    """The recorded operation is built by the ONE builder, not by hand.

    The builder is what states the style the algebra requires, so the
    broker script that records the operation goes through it - a dict
    literal spelled out beside it is how the style went missing.
    """
    builders = 0
    for source in _script_sources():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # A script that NAMES the builder and cannot be parsed is a
            # script no reader can read: the count below would skip it
            # and the gate would pass on nothing.  The broker template is
            # parseable Python, so this is a defect, not a shape.
            assert "caption_style_operation" not in source, (
                "the live broker script names the shared builder and does "
                "not parse as Python")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", "") or getattr(func, "attr", "")
            if name.endswith("caption_style_operation"):
                builders += 1
    assert builders >= 1, (
        "the live broker script builds no caption.style operation through "
        "the shared builder")
