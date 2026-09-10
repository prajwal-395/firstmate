"""An input name Fusion does not have is a SILENT no-op, and it shipped.

Fusion ignores an input it does not know without a word: the tool keeps
its registry default and the picture is whatever that default draws. A
`.comp` file is text and its reader is a closed-source application, so
nothing in this repository could catch it - and four separate defects of
exactly that shape were live on the captain's own Reel 09 timeline on
2026-09-10, three of them for months.

Read off the live comp on that timeline, with `tool.GetInput`:

* ``Ellipse1.Invert = 0.0`` while ``Ellipse1.Inverted = 1.0``. Fusion
  parks an unknown input in the comp as inert data, which is why the
  wrong one read back as set. The mask stayed solid INSIDE the ellipse,
  so the black Background it gated drew as a DISC IN THE MIDDLE of every
  graded clip. The captain found it by eye. AGENTS.md 5 stated the wrong
  name in prose and `FusionNode._validate` REQUIRED the wrong name, so
  every guard this repository had agreed with the bug.
* ``FilmGrain1.MasterStrength = 0.1`` - the registry default - while
  ``FilmGrain1.Power = 0.35``, the declared value. Every grain this
  engine has ever declared was ignored, and the node sat at a strength
  nobody chose (AGENTS.md 10.5) arriving through a NAME rather than a
  `.get`.
* ``PowerCrop1`` drove ``CropTop``/``CropBottom``; Fusion's Crop has
  ``XOffset``/``YOffset``/``XSize``/``YSize``. With those unset the tool
  took its own frame size at offset (0, 0), and Fusion's origin is
  BOTTOM-LEFT, so a 3840x2160 source came out cropped to its bottom-left
  corner on every reel's first and last picture clip. The old-TV switch
  animation never drew once.
* ``ChromaticAberration`` is not a registered tool at all, and
  ``LensDistort`` has no bare ``Distortion``.

`library/tools/fusion/tool_inputs.py` is the gate: a table of what each
tool really has, dumped from a running Resolve by
`scripts/probe_fusion_tool_inputs.py`, and `FusionNode._validate` refuses
an authored node that drives anything else.
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


def test_the_table_says_which_resolve_it_was_read_from():
    """A gate whose provenance is unstated cannot be re-checked."""
    assert tool_inputs.PROBE_META.get("resolve_version")
    assert tool_inputs.PROBE_META.get("probed")
    assert "Resolve" in tool_inputs.describe_provenance()


def test_ellipse_mask_inverts_with_invert_not_inverted():
    assert "Invert" in tool_inputs.TOOL_INPUTS["EllipseMask"]
    assert "Inverted" not in tool_inputs.TOOL_INPUTS["EllipseMask"]


def test_crop_is_offset_and_size_not_edges():
    crop = tool_inputs.TOOL_INPUTS["Crop"]
    assert {"XOffset", "YOffset", "XSize", "YSize"} <= crop
    assert "CropTop" not in crop and "CropBottom" not in crop


def test_film_grain_has_no_power_or_size():
    grain = tool_inputs.TOOL_INPUTS["FilmGrain"]
    assert {"MasterStrength", "MasterXSize", "MasterYSize"} <= grain
    assert "Power" not in grain and "Size" not in grain


def test_a_tool_the_probe_asked_for_and_did_not_find_is_recorded_absent():
    assert tool_inputs.is_absent_tool("ChromaticAberration")
    assert not tool_inputs.is_absent_tool("Transform")


def test_an_unprobed_tool_is_unchecked_rather_than_refused():
    """The table is a partial probe. Refusing what nobody measured is a
    verdict invented from ignorance, which is worse than the silence it
    replaces."""
    assert not tool_inputs.is_known_tool("SomeFuseNobodyProbed")
    assert tool_inputs.unknown_inputs("SomeFuseNobodyProbed", ["Whatever"]) == []


# ── The gate can fail (AGENTS.md 10.4) ───────────────────────────────────


def test_an_authored_node_driving_an_unknown_input_is_refused():
    comp = FusionComp(duration=30)
    node = FusionNode("Ellipse1", "EllipseMask")
    for key, value in (("MaskWidth", 1080), ("MaskHeight", 1920),
                       ("PixelAspect", (1, 1)), ("Invert", 1)):
        node.set_input(key, value)
    node.set_input("Inverted", 1)
    comp.add_node(node)
    with pytest.raises(tool_inputs.UnknownFusionInput, match="Inverted"):
        comp.serialize()


def test_an_authored_node_naming_an_absent_tool_is_refused():
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
    assert "Tools = {" in build_effect_comp({}, 30)


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


def test_no_tool_type_the_engine_writes_is_one_resolve_does_not_have():
    written = _tool_types_the_engine_writes()
    absent = sorted(t for t in written if tool_inputs.is_absent_tool(t))
    assert not absent, f"{absent} do not exist in Fusion"
