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
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.schemas.brand_template import BrandTemplate, StyleSlots
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
              brand_template: synthetic_default
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


class TestSyntheticCopies:
    """What each synthetic project copy means, now that silence means
    fill. Named here so a change to any copy is a change to a test."""

    def test_every_synthetic_copy_delivers_vertical(self):
        """A fill is only the right default because every copy declares a
        9:16 product. If one ever declares a landscape format, this
        default needs revisiting rather than inheriting."""
        from library.tools.delivery_format import resolve_format_name
        from tests.brand_fixtures import ALL_SYNTHETIC
        for name, data in sorted(ALL_SYNTHETIC.items()):
            tmpl = BrandTemplate.from_dict(data)
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

    def test_a_covering_source_fills_whatever_was_declared(self):
        assert delivered_framing_intent(LETTERBOX, True) == FILL
        assert delivered_framing_intent(0.4, True) == FILL
        assert delivered_framing_intent(FILL, True) == FILL

    def test_a_source_with_bars_delivers_what_it_was_told(self):
        assert delivered_framing_intent(LETTERBOX, False) == LETTERBOX
        assert delivered_framing_intent(0.4, False) == 0.4
        assert delivered_framing_intent(FILL, False) == FILL


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


class TestAProjectDeclarationEscapesThePunchIn:
    """Issue #277: every landscape clip shipped punched in 3.1605x,
    showing 31.6% of its width, because 001 declared nothing and the
    default is FILL. #311 fixed 001 by declaration -
    ``pipeline.framing_intent: 0.0`` - plus the ``framing_delivered``
    record that says what each clip actually gives. The resolve step
    and the conform step are tested separately above; what nothing
    pinned is that the two meet - that a 001-style project declaration
    really reaches the conform and no landscape clip keeps a fill_zoom.
    Break the wiring between them and both halves still pass while the
    fix silently unravels."""

    @staticmethod
    def _conform_project_clip(project_folder, clip_id, width, height,
                              rotation=0):
        from library.steps.step_5_04_compile_manifest.step import (
            _conform_fields,
        )
        intent = resolve_framing_intent(project_folder=project_folder)
        meta = {clip_id: {"width": width, "height": height,
                          "rotation": rotation}}
        return _conform_fields(meta, clip_id, (1080, 1920),
                               framing_intent=intent)

    def test_declared_letterbox_still_fills_a_portrait_cutaway(
            self, tmp_path):
        """001's seven cutaways are shot portrait: no bars to give, so
        the same declaration delivers a full frame and says so."""
        folder = write_project(tmp_path, """
            name: Project 001 Shape
            slug: project-001-shape
            pipeline:
              framing_intent: 0.0
        """)
        got = self._conform_project_clip(folder, "cutaway.MOV", 1920, 1080,
                                         rotation=90)
        assert got["framing_intent"] == LETTERBOX
        assert got["framing_delivered"] == FILL
        assert got["needs_conform"] is False
