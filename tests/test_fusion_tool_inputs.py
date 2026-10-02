"""An input name Fusion does not have is a SILENT no-op, and it shipped.

History: docs/evidence/resolve_test_history.md#test_fusion_tool_inputs.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from library.tools.fusion import tool_inputs
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.nodes import FusionComp, FusionNode

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── The table itself ─────────────────────────────────────────────────────


def test_the_probed_names_are_resolves_not_the_guessed_ones():
    """EllipseMask inverts with `Invert`, Crop is offset+size not edges,
    FilmGrain has no Power or Size."""
    assert "Invert" in tool_inputs.TOOL_INPUTS["EllipseMask"]
    assert "Inverted" not in tool_inputs.TOOL_INPUTS["EllipseMask"]
    crop = tool_inputs.TOOL_INPUTS["Crop"]
    assert {"XOffset", "YOffset", "XSize", "YSize"} <= crop
    assert "CropTop" not in crop and "CropBottom" not in crop
    grain = tool_inputs.TOOL_INPUTS["FilmGrain"]
    assert {"MasterStrength", "MasterXSize", "MasterYSize"} <= grain
    assert "Power" not in grain and "Size" not in grain


def test_absent_is_recorded_and_unprobed_is_unchecked():
    """A tool the probe asked for and did not find is recorded absent.
    The table is a partial probe: refusing what nobody measured is a
    verdict invented from ignorance, worse than the silence it replaces."""
    assert tool_inputs.is_absent_tool("ChromaticAberration")
    assert not tool_inputs.is_absent_tool("Transform")
    assert not tool_inputs.is_known_tool("SomeFuseNobodyProbed")
    assert tool_inputs.unknown_inputs("SomeFuseNobodyProbed", ["Whatever"]) == []


# ── The gate can fail (AGENTS.md 10.4) ───────────────────────────────────


def test_an_authored_node_driving_an_unknown_input_or_tool_is_refused():
    comp = FusionComp(duration=30)
    node = FusionNode("Ellipse1", "EllipseMask")
    for key, value in (("MaskWidth", 1080), ("MaskHeight", 1920),
                       ("PixelAspect", (1, 1)), ("Invert", 1)):
        node.set_input(key, value)
    node.set_input("Inverted", 1)
    comp.add_node(node)
    with pytest.raises(tool_inputs.UnknownFusionInput, match="Inverted"):
        comp.serialize()
    comp = FusionComp(duration=30)
    comp.add_node(FusionNode("CA1", "ChromaticAberration"))
    with pytest.raises(tool_inputs.UnknownFusionTool,
                       match="ChromaticAberration"):
        comp.serialize()


def test_a_foreign_comp_is_exempt():
    """DaVinci's own macros are free to use tools and inputs nobody here
    probed - the same authorship carve-out every other house rule takes."""
    comp = FusionComp(duration=30, authored=False)
    node = FusionNode("Ellipse1", "EllipseMask")
    node.set_input("Inverted", 1)
    comp.add_node(node)
    assert comp.serialize().startswith("Composition {")


def test_the_media_in_global_range_idiom_is_not_judged():
    """`["MediaIn1.GlobalStart"]` is instance-scoped and never appears in
    `GetInputList()`; judging it would refuse a comp Resolve authored."""
    assert "Tools = {" in build_effect_comp({}, 30,
                                             source_res=(1080, 1920))


# ── Every tool this engine writes has been probed ────────────────────────


def _tool_types_the_engine_writes() -> set[str]:
    """Every literal tool type passed to `FusionNode(...)` in library/."""
    found: set[str] = set()
    for path in (REPO / "library").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a file we do not own
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "FusionNode"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                found.add(node.args[1].value)
    return found


def test_every_tool_type_the_engine_writes_was_probed():
    """A new tool type must be probed, not assumed.

    Without this the gate quietly stops applying to whatever was added
    last - which is the shape of every defect it exists to catch.
    """
    written = _tool_types_the_engine_writes()
    assert written, "the AST walk found no FusionNode(...) calls"
    unprobed = sorted(t for t in written
                      if not tool_inputs.is_known_tool(t))
    assert not unprobed, (
        f"{unprobed} are written into comps and were never probed. Add "
        f"them to scripts/probe_fusion_tool_inputs.py and re-run it "
        f"against a running Resolve.")
    absent = sorted(t for t in written if tool_inputs.is_absent_tool(t))
    assert not absent, f"{absent} do not exist in Fusion"
