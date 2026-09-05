"""DaVinci's own 143 shipped presets must survive our Fusion harness.

56 of the 143 `.setting` files under `library/presets/resolve-builtin/`
used to fail a parse + serialize round-trip, and the failure was read as
"the effect crashes". It was ours in every case:

- 38 were our own authorship rules (FusionNode._validate) refusing
  Blackmagic's macros. 20 built-ins carry a Background with no GlobalOut,
  14 an EllipseMask with no Inverted, 4 an ApplyMode on Merge. Those rules
  describe comps THIS pipeline writes; a foreign file is not bound by them.
- 14 were an anonymous nested table (`Curves = { { Points = ... } }`) whose
  opening brace `_parse_table` swallowed instead of recursing into, so the
  matching `}` closed the parent table early and the desync cascaded.
- 4 were a namespaced Fuse plugin tool type (`Fuse.RealFastNoiseFuse`),
  where the identifier stopped at the dot and left a bare '.' that
  `_parse_number` then tried to read as a number.

Worse, the 87 that "passed" were quietly corrupted by the same desync:
ambient_occlusion was losing 19 of its 30 nodes and reporting success.

No test parsed a single real `.setting` file, which is why none of this
was visible. These do.
"""
import json
import pathlib

import pytest

from library.tools.builtin_effect_loader import BUILTIN_DIR, list_builtin_effects
from library.tools.fusion.nodes import BezierSpline, FusionComp, FusionNode
from library.tools.fusion.parser import parse_setting

ALL_EFFECTS = sorted(list_builtin_effects())


def _source(name: str) -> str:
    meta = list_builtin_effects()[name]
    return (BUILTIN_DIR / meta["path"]).read_text(encoding="utf-8")


# ── The whole library round-trips ────────────────────────────


def test_every_shipped_preset_is_advertised():
    """Guards the sample below against silently shrinking."""
    on_disk = len(list(BUILTIN_DIR.rglob("*.setting")))
    assert len(ALL_EFFECTS) == on_disk == 143


@pytest.mark.parametrize("name", ALL_EFFECTS)
def test_preset_round_trips(name):
    """Parse + serialize must not raise for any of DaVinci's presets.

    This is the assertion the harness never made. Each of the four
    historical failure modes shows up here as an exception.
    """
    comp = parse_setting(_source(name))
    assert comp.nodes, f"{name} parsed to an empty comp"
    assert comp.serialize().startswith("Composition {")


# Node counts pinned from the fixed parser. Every one of these files was
# either failing outright or silently losing nodes before the fix; a drop
# here means the table desync is back.
@pytest.mark.parametrize(
    "name,expected_nodes",
    [
        ("ambient_occlusion", 30),   # was 11 - lost 19 nodes, reported success
        ("lightwrap", 23),           # was 8
        ("circle_layout", 5),        # was 1
        ("bokeh_edges", 21),         # was AttributeError
        ("brick", 24),               # was ValueError: float('.')
        ("anisotropic", 15),         # was Background missing GlobalOut
    ],
)
def test_preset_node_count_preserved(name, expected_nodes):
    assert len(parse_setting(_source(name)).nodes) == expected_nodes


# ── Fix A: anonymous nested tables ───────────────────────────


def test_anonymous_table_as_array_element():
    """`{ { ... }, { ... } }` must not swallow the sibling that follows.

    Fusion writes this for every ColorCurves / LUTBezier control. The
    swallowed brace closed the enclosing table early, so node `B` here
    vanished along with everything after it in the real files.
    """
    src = (
        "{ Tools = { "
        "A = Foo { Inputs = { Curves = { { Points = { {0,1} } } }, }, }, "
        "B = Bar { Inputs = {}, }, "
        "} }"
    )
    comp = parse_setting(src)
    assert [n.name for n in comp.nodes] == ["A", "B"]


def test_anonymous_table_contents_are_indexed():
    parsed = parse_setting(
        "{ Tools = { A = Foo { Inputs = { "
        "Ranges = Input { Value = ColorCurves { Curves = { "
        "{ Points = { { 0, 1 }, { 1, 0 } } }, "
        "{ Points = { { 0, 0 } } } "
        "} } }, }, }, } }"
    )
    curves = parsed.nodes[0].inputs["Ranges"]["value"]["Curves"]
    assert sorted(curves) == [0, 1], "both anonymous curve tables survive"


# ── Fix B: namespaced Fuse plugin tool types ─────────────────


def test_dotted_fuse_tool_type():
    """`Fuse.RealFastNoiseFuse` is a tool type, not an identifier then a dot."""
    comp = parse_setting(
        '{ Tools = { X = Fuse.RealFastNoiseFuse { Inputs = { '
        'Width = Input { Value = 1068, }, }, }, } }'
    )
    assert [(n.name, n.tool_type) for n in comp.nodes] == [
        ("X", "Fuse.RealFastNoiseFuse")
    ]


def test_quoted_key_containing_a_dot_still_parses():
    """The dotted-identifier fix must not touch quoted keys."""
    comp = parse_setting(
        '{ Tools = { A = Foo { Inputs = { '
        '["MediaIn1.GlobalStart"] = Input { Value = 3, }, }, }, } }'
    )
    assert comp.nodes[0].inputs["MediaIn1.GlobalStart"]["value"] == 3


# ── Fix C: values go through _lua_value ──────────────────────


def test_boolean_input_serializes_as_lua_boolean():
    """`Value = True` is Python. Lua reads it as an undefined global."""
    node = FusionNode("Grain1", "Custom")
    node.inputs["Enabled"] = {"_type": "value", "value": True}
    out = node.serialize()
    assert "Value = true," in out
    assert "Value = True" not in out


def test_string_input_is_quoted():
    node = FusionNode("Text1", "TextPlus")
    node.inputs["StyledText"] = {"_type": "value", "value": "hello"}
    assert 'Value = "hello",' in node.serialize()


def test_numeric_input_is_unchanged():
    node = FusionNode("Transform1", "Transform")
    node.inputs["Size"] = {"_type": "value", "value": 1.04}
    node.inputs["Count"] = {"_type": "value", "value": 3}
    out = node.serialize()
    assert "Value = 1.04," in out
    assert "Value = 3," in out


# ── Fix D: house rules are authorship rules ──────────────────


def test_parsed_comp_is_not_authored():
    assert parse_setting(_source("posterize")).authored is False


def test_comps_we_build_are_authored_by_default():
    assert FusionComp(duration=75).authored is True


def test_authored_comp_still_rejects_apply_mode_on_merge():
    """The rule stays exactly where it was earned."""
    comp = FusionComp(duration=75)
    merge = FusionNode("Merge1", "Merge")
    merge.inputs["ApplyMode"] = {"_type": "value", "value": "Screen"}
    comp.add_node(merge)
    with pytest.raises(ValueError, match="ApplyMode on Merge CRASHES"):
        comp.serialize()


def test_authored_comp_still_requires_global_out_on_background():
    comp = FusionComp(duration=75)
    comp.add_node(FusionNode("Background1", "Background"))
    with pytest.raises(ValueError, match="missing GlobalOut"):
        comp.serialize()


@pytest.mark.parametrize(
    "name", ["posterize", "anisotropic", "burning_engine"]
)
def test_foreign_comp_skips_the_house_rules(name):
    """posterize uses ApplyMode, anisotropic omits GlobalOut,
    burning_engine omits Inverted - all legitimate in DaVinci's own files."""
    assert parse_setting(_source(name)).serialize()


def _zooming_comp(authored):
    """A comp whose Transform is animated past the 1.04 zoom ceiling."""
    comp = FusionComp(duration=75, authored=authored)
    spline = BezierSpline("Transform1Size")
    spline.add_key(0, 1.0).add_key(74, 1.35)
    xf = FusionNode("Transform1", "Transform")
    xf.set_input("Size", spline)
    return comp.add_node(spline).add_node(xf)


def test_authored_comp_still_enforces_the_zoom_ceiling():
    """_validate_global is an authorship rule too, not just _validate.

    No shipped preset happens to trip this one, so without an explicit
    case the comp-level gate would be scoped but unverified.
    """
    with pytest.raises(ValueError, match="Too aggressive"):
        _zooming_comp(authored=True).serialize()


def test_foreign_comp_skips_the_zoom_ceiling():
    assert _zooming_comp(authored=False).serialize()


def test_foreign_flag_does_not_leak_to_a_hand_built_comp():
    comp = FusionComp(duration=75, authored=False)
    comp.add_node(FusionNode("Background1", "Background"))
    assert comp.serialize()  # skipped
    assert FusionComp(duration=75).authored is True  # default unchanged


# ── Fix E: built-ins reach Resolve byte-identical ────────────


def test_render_path_never_calls_the_round_trip(monkeypatch):
    """The destructive round-trip must not be reachable from the renderer.

    AST, not a substring: the module explains in a comment why it does not
    import import_customized_effect, and a text search would match that.
    """
    import ast

    import library.tools.execution.apply_fusion_comps as afc

    tree = ast.parse(pathlib.Path(afc.__file__).read_text(encoding="utf-8"))
    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "import_customized_effect" not in referenced
    assert "import_effect_to_clip" in referenced


def test_builtin_effect_reaches_resolve_byte_identical(monkeypatch, tmp_path):
    """A built-in preset plus film-look params still imports the shipped file.

    compile_manifest merges the film look onto every V1/V2 clip, so the old
    `if effects: import_customized_effect(...)` branch sent every built-in
    through the parser. The round-trip dropped the GroupOperator wrapper and
    every InstanceInput - including MainInput1, the macro's image input.
    """
    import library.tools.execution.apply_fusion_comps as afc

    from library.tools.builtin_effect_loader import get_effect_path

    imported = []

    class MockTimelineClip:
        def GetStart(self):
            return 0

        def GetEnd(self):
            return 100

        def GetMediaPoolItem(self):
            return MockMediaPoolItem()

        def GetFusionCompNameList(self):
            return ["Fusion Composition 1"] if imported else []

        def ImportFusionComp(self, path):
            imported.append(path)
            return True

        def DeleteFusionCompByName(self, name):
            pass

    class MockMediaPoolItem:
        def GetClipProperty(self, prop):
            return {"File Path": "test.mov", "Frames": "100"}.get(prop)

    class MockTimeline:
        def GetUniqueId(self): return str(id(self))
        def GetSetting(self, name):
            return "30"

        def GetItemListInTrack(self, track_type, index):
            return [MockTimelineClip()]

    class MockProject:
        def __init__(self):
            self._current_timeline = MockTimeline()

        def GetCurrentTimeline(self):
            return self._current_timeline

        def SetCurrentTimeline(self, tl):
            self._current_timeline = tl
            return True
    class MockProjectManager:
        def GetCurrentProject(self):
            return MockProject()

    class MockResolve:
        def GetProjectManager(self):
            return MockProjectManager()

    monkeypatch.setattr(afc.dvr, "scriptapp", lambda _: MockResolve())

    manifest = {
        "project_dir": str(tmp_path),
        "tracks": {"V1": {"clips": [{"source_file": "test.mov",
                                     "label": "clip_0"}]}},
        # The film look compile_manifest merges onto every clip. These are
        # our comp-engine names; no Resolve tool reads them.
        "vfx": [{
            "timeline_start": 0,
            "effect_type": "chromatic_aberration",
            "params": {"glow_gain": 0.125, "film_grain": True,
                       "vignette": True},
        }],
    }

    afc.apply_fusion_comps(manifest, str(tmp_path))

    shipped = str(get_effect_path("chromatic_aberration").resolve())
    assert imported == [shipped], (
        f"expected DaVinci's own file, got {imported}"
    )
    assert pathlib.Path(imported[0]).read_text(encoding="utf-8") == _source(
        "chromatic_aberration"
    )


def test_import_effect_to_clip_passes_the_shipped_path(tmp_path):
    from unittest.mock import MagicMock

    from library.tools.builtin_effect_loader import (
        get_effect_path,
        import_effect_to_clip,
    )

    clip = MagicMock()
    import_effect_to_clip(clip, "chromatic_aberration")

    (passed,), _ = clip.ImportFusionComp.call_args
    assert passed == str(get_effect_path("chromatic_aberration").resolve())
    assert pathlib.Path(passed).read_text(encoding="utf-8") == _source(
        "chromatic_aberration"
    ), "Resolve must receive the file byte-identical"


def test_shipped_macros_still_declare_their_image_input():
    """MainInput1 is what makes a macro an effect ON the shot.

    The round-trip deleted it. Nothing in the import path may now.
    """
    with_image_input = [
        n for n in ALL_EFFECTS if "MainInput1 = InstanceInput" in _source(n)
    ]
    assert "chromatic_aberration" in with_image_input
    assert "advanced_camera_shake" in with_image_input
    assert len(with_image_input) == 32


def test_index_paths_all_resolve():
    index = json.loads((BUILTIN_DIR / "index.json").read_text(encoding="utf-8"))
    missing = [n for n, m in index.items() if not (BUILTIN_DIR / m["path"]).is_file()]
    assert not missing
