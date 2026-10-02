"""DaVinci's own shipped presets must survive our Fusion harness.

Parse + serialize every `.setting` under `library/presets/resolve-builtin/`,
keep house rules for comps WE author only, and ship built-ins byte-identical.
History (56 of 143 failing, silent node loss): docs/evidence/fusion_parser.md.
"""
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


def test_every_preset_round_trips_with_its_nodes():
    """Parse + serialize must not raise, or lose nodes, for any preset.

    Each historical failure mode (authorship rules on foreign files, the
    anonymous nested table desync, namespaced Fuse types) raises here; the
    pinned node counts catch the silent desync that dropped nodes instead.
    """
    failures = {}
    for name in ALL_EFFECTS:
        try:
            comp = parse_setting(_source(name))
            if not comp.nodes:
                failures[name] = "parsed to an empty comp"
            elif not comp.serialize().startswith("Composition {"):
                failures[name] = "serialized without a Composition header"
        except Exception as exc:  # noqa: BLE001 - collected per preset
            failures[name] = repr(exc)
    assert not failures, failures

    # Pinned from the fixed parser; every one was failing or losing nodes.
    pinned = {
        "ambient_occlusion": 30,   # was 11 - lost 19 nodes, reported success
        "lightwrap": 23,           # was 8
        "circle_layout": 5,        # was 1
        "bokeh_edges": 21,         # was AttributeError
        "brick": 24,               # was ValueError: float('.')
        "anisotropic": 15,         # was Background missing GlobalOut
    }
    counts = {n: len(parse_setting(_source(n)).nodes) for n in pinned}
    assert counts == pinned


# ── Fix A: anonymous nested tables ───────────────────────────


# ── Fix B: namespaced Fuse plugin tool types ─────────────────


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


# ── Fix D: house rules are authorship rules ──────────────────


def _zooming_comp(authored):
    """A comp whose Transform is animated past the 1.04 zoom ceiling."""
    comp = FusionComp(duration=75, authored=authored)
    spline = BezierSpline("Transform1Size")
    spline.add_key(0, 1.0).add_key(74, 1.35)
    xf = FusionNode("Transform1", "Transform")
    xf.set_input("Size", spline)
    return comp.add_node(spline).add_node(xf)


def _merge_with_apply_mode():
    comp = FusionComp(duration=75)
    merge = FusionNode("Merge1", "Merge")
    merge.inputs["ApplyMode"] = {"_type": "value", "value": "Screen"}
    return comp.add_node(merge)


def _background_without_global_out():
    return FusionComp(duration=75).add_node(FusionNode("Background1", "Background"))


@pytest.mark.parametrize(
    "build,match",
    [
        (_merge_with_apply_mode, "ApplyMode on Merge CRASHES"),
        (_background_without_global_out, "missing GlobalOut"),
        # _validate_global is an authorship rule too; no preset trips it.
        (lambda: _zooming_comp(authored=True), "Too aggressive"),
    ],
)
def test_authored_comp_still_enforces_house_rules(build, match):
    """The rules stay exactly where they were earned: comps WE build."""
    with pytest.raises(ValueError, match=match):
        build().serialize()


def test_foreign_comp_skips_the_zoom_ceiling():
    assert _zooming_comp(authored=False).serialize()


# ── Fix E: built-ins reach Resolve byte-identical ────────────


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
