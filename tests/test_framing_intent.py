"""The framing intent declaration: where a clip's number comes from.

`library/tools/framing_intent.py` is the one enumeration. Four things
are tested here and nothing else:

1. the default is FILL, because the heuristic it replaced letterboxed
   every talking-head clip;
2. the precedence - spine block > project.yaml > brand template >
   default - and in particular that a project can still say "letterbox";
3. a malformed declaration RAISES rather than degrading to "no framing";
4. declared is not delivered - a source that already covers the frame
   fills at every intent, and `_conform_fields` records both numbers.
"""
import os
import sys
import textwrap

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "library")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from library.schemas.brand_template import BrandTemplate, StyleSlots
from library.tools.brand_registry import resolve_project_template
from library.tools.framing_intent import (
    DEFAULT_FRAMING_INTENT,
    FILL,
    LETTERBOX,
    delivered_framing_intent,
    source_covers_frame,
    project_framing_intent,
    resolve_framing_intent,
    template_framing_intent,
    validate_framing_intent,
)

TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "library", "templates")


def write_project(tmp_path, body: str) -> str:
    (tmp_path / "project.yaml").write_text(textwrap.dedent(body))
    return str(tmp_path)


def template(intent):
    return BrandTemplate(series_id="t", style=StyleSlots(framing_intent=intent))


class TestTheDefaultIsFill:
    """61.6% of project 001's pixels were black because "declares
    nothing" meant "letterbox". It means "fill the frame you declared"
    now."""

    def test_default_is_fill(self):
        assert DEFAULT_FRAMING_INTENT == FILL == 1.0

    def test_nothing_declared_anywhere(self, tmp_path):
        folder = write_project(tmp_path, """
            name: Nothing Declared
            slug: nothing
        """)
        assert resolve_framing_intent(project_folder=folder) == FILL

    def test_no_project_folder_at_all(self):
        assert resolve_framing_intent() == FILL

    def test_a_template_that_declares_none(self):
        assert template_framing_intent(template(None)) is None
        assert resolve_framing_intent(template=template(None)) == FILL


class TestPrecedence:

    def test_template_beats_the_default(self):
        assert resolve_framing_intent(template=template(0.3)) == 0.3

    def test_project_beats_the_template(self, tmp_path):
        folder = write_project(tmp_path, """
            name: Project Wins
            pipeline:
              framing_intent: 0.0
        """)
        assert resolve_framing_intent(
            project_folder=folder, template=template(1.0)) == LETTERBOX

    def test_spine_block_beats_the_project(self, tmp_path):
        folder = write_project(tmp_path, """
            name: Block Wins
            pipeline:
              framing_intent: 0.0
        """)
        assert resolve_framing_intent(
            block_intent=1.0, project_folder=folder,
            template=template(0.0)) == FILL

    def test_a_project_can_still_ask_for_letterbox(self, tmp_path):
        """The requirement that survives the default flip: 0.0 must stay
        expressible per project."""
        folder = write_project(tmp_path, """
            name: Bars Please
            pipeline:
              framing_intent: 0.0
        """)
        assert project_framing_intent(folder) == LETTERBOX
        assert resolve_framing_intent(project_folder=folder) == LETTERBOX

    def test_project_declaring_none_is_not_zero(self, tmp_path):
        folder = write_project(tmp_path, """
            name: Silent
            pipeline:
              brand_template: default_brand
        """)
        assert project_framing_intent(folder) is None


class TestMalformedDeclarationsRaise:

    # `None` is deliberately absent: it means "not declared", which is
    # tested above and is not a malformed declaration.  It used to be a
    # parameter with a `pytest.skip` inside the body, which is a case
    # that skips in every environment - see
    # `tests/test_no_unfailable_tests.py`.
    @pytest.mark.parametrize("bad", ["1.0", True, [1.0], {}])
    def test_non_numeric(self, bad):
        with pytest.raises(TypeError):
            validate_framing_intent(bad, "a test")

    @pytest.mark.parametrize("bad", [-0.1, 1.1, 100])
    def test_out_of_range(self, bad):
        with pytest.raises(ValueError):
            validate_framing_intent(bad, "a test")

    def test_a_bad_project_declaration_names_the_file(self, tmp_path):
        folder = write_project(tmp_path, """
            name: Broken
            pipeline:
              framing_intent: 100
        """)
        with pytest.raises(ValueError) as excinfo:
            project_framing_intent(folder)
        assert "project.yaml" in str(excinfo.value)

    def test_bounds_are_accepted(self):
        assert validate_framing_intent(0, "a test") == 0.0
        assert validate_framing_intent(1, "a test") == 1.0


class TestTheProjectConfigCarriesIt:
    """A declaration that project.yaml round-tripping would drop is a
    declaration that disappears the first time anything rewrites the file.
    `delivery_format` is in the dataclass for the same reason."""

    def test_round_trip_preserves_a_declaration(self, tmp_path):
        from library.schemas.project_config import (
            _dict_to_project_config, project_config_to_dict)
        cfg = _dict_to_project_config({
            "name": "Bars", "slug": "bars",
            "pipeline": {"brand_template": "default_brand",
                         "framing_intent": 0.0},
        })
        assert cfg.pipeline.framing_intent == 0.0
        assert project_config_to_dict(cfg)["pipeline"]["framing_intent"] == 0.0
        assert cfg.validate() == []

    def test_undeclared_stays_absent(self):
        from library.schemas.project_config import (
            _dict_to_project_config, project_config_to_dict)
        cfg = _dict_to_project_config({
            "name": "Silent", "slug": "silent",
            "pipeline": {"brand_template": "default_brand"},
        })
        assert cfg.pipeline.framing_intent is None
        assert "framing_intent" not in project_config_to_dict(cfg)["pipeline"]

    def test_out_of_range_is_a_validation_error(self):
        from library.schemas.project_config import _dict_to_project_config
        cfg = _dict_to_project_config({
            "name": "Broken", "slug": "broken",
            "pipeline": {"framing_intent": 100},
        })
        assert any("framing_intent" in e for e in cfg.validate())


class TestShippedTemplates:
    """What each brand template in the repo means, now that silence means
    fill. Named here so a change to any template is a change to a test."""

    def test_cinematic_narrative_still_letterboxes(self):
        tmpl = resolve_project_template("cinematic_narrative", TEMPLATES_DIR)
        assert template_framing_intent(tmpl) == LETTERBOX

    def test_shortform_energetic_still_fills(self):
        tmpl = resolve_project_template("shortform_energetic", TEMPLATES_DIR)
        assert template_framing_intent(tmpl) == FILL

    @pytest.mark.parametrize("name", [
        "default_brand", "interview_professional", "lucie_client",
    ])
    def test_the_silent_templates_now_fill(self, name):
        tmpl = resolve_project_template(name, TEMPLATES_DIR)
        assert template_framing_intent(tmpl) is None
        assert resolve_framing_intent(template=tmpl) == FILL

    def test_every_shipped_template_delivers_vertical(self):
        """A fill is only the right default because every template in the
        tree declares a 9:16 product. If one ever declares a landscape
        format, this default needs revisiting rather than inheriting."""
        from library.tools.delivery_format import resolve_format_name
        for entry in sorted(os.listdir(TEMPLATES_DIR)):
            name, ext = os.path.splitext(entry)
            if ext not in (".yaml", ".yml", ".json"):
                continue
            tmpl = resolve_project_template(name, TEMPLATES_DIR)
            width, height = resolve_format_name(
                getattr(tmpl, "delivery_format", "") or "")
            assert height > width, f"{name} is not a vertical product"


class TestDeclaredIsNotDelivered:
    """A source that already covers the frame has no bars to give.

    This is project 001's whole edit: eleven landscape A-roll placements
    that letterbox under a declared 0.0, and seven portrait cutaways that
    cannot. Recording the declaration alone told `render_qa`'s occupancy
    gate the frame was barred where it is full, and that gate reads the
    manifest precisely to learn what the picture was supposed to be.
    """

    VERTICAL = (1080, 1920)

    def test_landscape_source_in_a_vertical_frame_has_bars_to_give(self):
        assert source_covers_frame(1920, 1080, *self.VERTICAL) is False

    def test_portrait_source_matching_the_frame_covers_it(self):
        assert source_covers_frame(1080, 1920, *self.VERTICAL) is True

    def test_a_taller_source_still_covers_the_frame(self):
        """4:5 into 9:16 crops the height; it never shows a bar."""
        assert source_covers_frame(1080, 1350, *self.VERTICAL) is False
        assert source_covers_frame(1080, 2400, *self.VERTICAL) is False

    def test_a_source_with_no_dimensions_covers_nothing(self):
        assert source_covers_frame(0, 0, *self.VERTICAL) is False
        assert source_covers_frame(None, None, *self.VERTICAL) is False

    def test_a_covering_source_fills_whatever_was_declared(self):
        assert delivered_framing_intent(LETTERBOX, True) == FILL
        assert delivered_framing_intent(0.4, True) == FILL
        assert delivered_framing_intent(FILL, True) == FILL

    def test_a_source_with_bars_delivers_what_it_was_told(self):
        assert delivered_framing_intent(LETTERBOX, False) == LETTERBOX
        assert delivered_framing_intent(0.4, False) == 0.4
        assert delivered_framing_intent(FILL, False) == FILL

    def test_a_malformed_resolved_intent_still_raises(self):
        with pytest.raises(ValueError):
            delivered_framing_intent(1.5, False)


class TestTheManifestRecordsBoth:
    """`_conform_fields` writes the declaration AND what it delivers."""

    @staticmethod
    def _conform(**kwargs):
        from library.steps.step_5_04_compile_manifest.step import _conform_fields
        meta = {
            "landscape": {"width": 1920, "height": 1080, "rotation": 0},
            "portrait": {"width": 1920, "height": 1080, "rotation": -90},
        }
        return _conform_fields(meta, kwargs.pop("clip"), (1080, 1920), **kwargs)

    def test_a_landscape_clip_told_to_letterbox_delivers_bars(self):
        got = self._conform(clip="landscape", framing_intent=LETTERBOX)
        assert got["framing_intent"] == LETTERBOX
        assert got["framing_delivered"] == LETTERBOX
        assert got["needs_conform"] is False
        assert "fill_zoom" not in got

    def test_a_portrait_clip_told_to_letterbox_delivers_a_full_frame(self):
        """The one that matters: it has no bars to give, and says so."""
        got = self._conform(clip="portrait", framing_intent=LETTERBOX)
        assert got["framing_intent"] == LETTERBOX
        assert got["framing_delivered"] == FILL

    def test_a_landscape_clip_told_to_fill_delivers_fill(self):
        got = self._conform(clip="landscape", framing_intent=FILL)
        assert got["framing_intent"] == FILL
        assert got["framing_delivered"] == FILL
        assert got["fill_zoom"] > 1.0

    def test_a_partial_punch_in_delivers_exactly_what_it_declared(self):
        """Below FILL the bars are narrower, not gone."""
        got = self._conform(clip="landscape", framing_intent=0.5)
        assert got["framing_delivered"] == 0.5

    def test_the_backdrop_route_delivers_a_full_frame(self):
        """Blurred backdrop behind an inset picture: every row is picture."""
        got = self._conform(clip="landscape", framing_intent=FILL,
                            subject_center_x=0.5, subject_width=0.8)
        assert "framing_backdrop" in got
        assert got["framing_delivered"] == FILL
