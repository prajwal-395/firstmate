"""Lane B (audit G3) tests: titles project into EffectCraft text layers.

Pure-Python boundary tests: op contents, comp-space geometry, font
resolution, fail-closed refusals (non-text entries by name, captions
never, undeliverable and project-carried fonts), and the wiring into
``project_to_request``. No EffectCraft CLI is built or invoked here;
``tests/qualification/test_effectcraft_titles_proof.py`` drives the
pinned CLI and asserts rendered pixels, not just ops.
"""
import copy
import json
import re
from pathlib import Path

import pytest

from library.tools import project_model_effectcraft as ec
from library.tools import project_model_effectcraft_titles as titles
from library.tools.safe_area import resolve_safe_area

ROOT = Path(__file__).resolve().parents[3]


def _comp(width=1080, height=1920, fps=30.0, duration=3.0):
    return ec.CompSpec(name="Main", width=width, height=height,
                       frame_rate=fps, duration_seconds=duration)


def _safe(width=1080, height=1920):
    return resolve_safe_area(width=width, height=height)


def _timed_moment(**overrides):
    moment = {
        "text": "Night 1",
        "color": "#D4A34A",
        "fontSize": 64,
        "startFrame": 30,
        "durationFrames": 60,
        "x": 0.5,
        "y": 0.5,
        "fadeInFrames": 9,
        "fadeOutFrames": 12,
        "fontWeight": 400,
        "textShadow": "none",
        "textAlign": "center",
    }
    moment.update(overrides)
    return moment


def _timed_doc(moments, family="Montserrat", font_file=None):
    doc = {"timed_text": {"fontFamily": family, "moments": moments}}
    if font_file is not None:
        doc["timed_text"]["fontFile"] = font_file
    return doc


def _motion_moment(**overrides):
    moment = {
        "element": "title_lockup",
        "anchor": "top_centre",
        "row": 0,
        "runs": [{"text": "THE TITLE", "type_role": "display",
                  "uppercase": False}],
        "color": "#FFFFFF",
        "colorBasis": "stated by the plan",
        "entrance": "fade",
        "exit": "fade",
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "startFrame": 0,
        "durationFrames": 60,
        "footprint": None,
        "data": {},
    }
    moment.update(overrides)
    return moment


def test_timed_text_moment_projects_position_size_font_and_fades():
    projection = titles.project_titles(
        _timed_doc([_timed_moment()]), _comp(), 30.0, _safe())
    add = next(op for op in projection.operations
               if op["op"] == "add_text")
    assert add == {"op": "add_text", "name": "timed-text-0",
                   "text": "Night 1", "size": 64.0, "fill": "#D4A34A",
                   "position": [540.0, 960.0], "font": "Montserrat"}
    keys = [op for op in projection.operations if op["op"] == "keyframe"]
    assert [(k["time"], k["value"]) for k in keys] == [
        (1.0, 0.0), (1.3, 100.0), (2.6, 100.0), (3.0, 0.0)]
    assert all(k["layer"] == "timed-text-0"
               and k["path"] == "transform/opacity" for k in keys)
    assert [r.as_json()["layer"] for r in projection.titles] == \
        ["timed-text-0"]


def test_motion_title_projects_per_run_with_composition_sizes():
    moment = _motion_moment(
        runs=[{"text": "BIG", "type_role": "display", "uppercase": False},
              {"text": "small", "type_role": "supporting",
               "uppercase": False}])
    projection = titles.project_titles(
        {"motion_moments": [moment]}, _comp(), 30.0, _safe())
    adds = [op for op in projection.operations if op["op"] == "add_text"]
    assert [a["text"] for a in adds] == ["BIG", "small"]
    # Sizes quote the composition's TYPE_SIZE at footprint 1.
    assert [a["size"] for a in adds] == [56.0, 36.0]
    assert all(a["font"] == "Montserrat" for a in adds)
    # Centre of the usable span, not the frame middle: the insets are
    # asymmetric (left 118, right 212 at 1080 wide).
    assert adds[0]["position"][0] == pytest.approx(118 + (1080 - 118 - 212) / 2)
    # Baselines hung from the safe top by the face's own ascent, the
    # second run stacked below the first.
    ascent_0 = 56.0 * titles.MONT_ASCENT_RATIO
    descent_0 = 56.0 * titles.MONT_DESCENT_RATIO
    assert adds[0]["position"][1] == pytest.approx(277 + ascent_0)
    assert adds[1]["position"][1] == pytest.approx(
        277 + ascent_0 + descent_0 + titles.STACK_GAP_PX
        + 36.0 * titles.MONT_ASCENT_RATIO)


def test_staged_explainer_reveals_runs_by_bracketed_cut_at_stage_seconds():
    moment = _motion_moment(
        element="list_build", anchor="centre",
        runs=[{"text": "one", "type_role": "supporting", "uppercase": False},
              {"text": "two", "type_role": "supporting", "uppercase": False}],
        data={"stage_offsets": [0.0, 1.0]})
    projection = titles.project_titles(
        {"motion_moments": [moment]}, _comp(), 30.0, _safe())
    keys = [op for op in projection.operations if op["op"] == "keyframe"]
    period = 1 / 30.0
    first = [k for k in keys if k["layer"] == "list_build-0-0"]
    # Offset 0 stages with the entry: a bracketed cut over the span.
    assert [(k["time"], k["value"]) for k in first] == [
        (0.0, 100.0), (2.0 - period, 100.0), (2.0, 0.0)]
    second = [k for k in keys if k["layer"] == "list_build-0-1"]
    assert [(k["time"], k["value"]) for k in second] == [
        (0.0, 0.0), (1.0 - period, 0.0), (1.0, 100.0),
        (2.0 - period, 100.0), (2.0, 0.0)]


def test_cut_envelope_is_bracketed_for_linear_interpolation():
    """A lone (appear, shown) pair would ramp across the hold (measured:
    100 at 0s and 0 at 0.5s reads 50 at 0.25s), so cuts bracket."""
    moment = _motion_moment(entrance="cut", exit="cut", startFrame=30,
                            durationFrames=30)
    projection = titles.project_titles(
        {"motion_moments": [moment]}, _comp(), 30.0, _safe())
    keys = [op for op in projection.operations if op["op"] == "keyframe"]
    period = 1 / 30.0
    assert [(k["time"], k["value"]) for k in keys] == [
        (1.0 - period, 0.0), (1.0, 100.0),
        (2.0 - period, 100.0), (2.0, 0.0)]


def test_non_text_entries_are_refused_by_name():
    for key in ("beat_accent", "progress_bar", "frame_accents",
                "channel_bug"):
        with pytest.raises(titles.NonTextElement, match=key):
            titles.project_titles(
                {"motion_moments": [_motion_moment(element=key)]},
                _comp(), 30.0, _safe())


def test_unknown_elements_carry_the_vocabulary_refusal():
    with pytest.raises(titles.UnknownTitleElement,
                       match="not a motion-graphics element"):
        titles.project_titles(
            {"motion_moments": [_motion_moment(element="spin_to_win")]},
            _comp(), 30.0, _safe())
    with pytest.raises(titles.CaptionNotProjected, match="caption system"):
        titles.project_titles(
            {"motion_moments": [
                _motion_moment(element="caption_word_emphasis")]},
            _comp(), 30.0, _safe())


def test_captions_and_subtitles_are_never_projected():
    with pytest.raises(titles.CaptionNotProjected, match="captions"):
        titles.project_titles({"captions": [{"text": "hi"}]},
                              _comp(), 30.0, _safe())
    with pytest.raises(titles.CaptionNotProjected, match="subtitles"):
        titles.project_titles({"subtitles": [{"text": "hi"}]},
                              _comp(), 30.0, _safe())
    with pytest.raises(titles.CaptionNotProjected, match="caption"):
        titles.project_titles(
            {"motion_moments": [_motion_moment(element="my_caption")]},
            _comp(), 30.0, _safe())


def test_fonts_refuse_rather_than_substitute():
    with pytest.raises(titles.UndeliverableFont, match="NoSuchFace"):
        titles.project_titles(
            _timed_doc([_timed_moment()], family="NoSuchFace"),
            _comp(), 30.0, _safe())
    with pytest.raises(titles.ProjectCarriedFont, match="font_file"):
        titles.project_titles(
            _timed_doc([_timed_moment()], family="Nanum Pen Script",
                       font_file="brand/NanumPenScript-Regular.ttf"),
            _comp(), 30.0, _safe())
    with pytest.raises(titles.FontMetricsUnmeasured, match="Helvetica"):
        titles.project_titles(
            _timed_doc([_timed_moment()], family="Helvetica"),
            _comp(), 30.0, _safe())


def test_montserrat_metrics_match_the_bundled_file():
    """The quoted ascent/descent is the bundled file's, or this fails."""
    pil = pytest.importorskip("PIL.ImageFont", reason="Pillow absent")
    from library.tools.render_fonts import measurable_font_path
    path = measurable_font_path("Montserrat", None, None)
    assert path is not None
    font = pil.truetype(path, 100)
    ascent, descent = font.getmetrics()
    assert titles.MONT_ASCENT_RATIO == pytest.approx(ascent / 100, abs=0.02)
    assert titles.MONT_DESCENT_RATIO == pytest.approx(descent / 100, abs=0.02)


def test_sub_frame_spans_and_overlong_ramps_are_refused():
    with pytest.raises(titles.TitleOutsideComp, match="sub-frame"):
        titles.project_titles(
            {"motion_moments": [_motion_moment(
                startFrame=0, durationFrames=1, entrance="cut",
                exit_="cut")]},
            ec.CompSpec(name="Main", width=1080, height=1920,
                        frame_rate=30.0, duration_seconds=3.0),
            300.0, _safe())
    with pytest.raises(titles.TitleProjectionError, match="outlasts"):
        titles.project_titles(
            {"motion_moments": [_motion_moment(
                startFrame=0, durationFrames=6, entrance="fade",
                exit_="fade")]},
            _comp(), 30.0, _safe())


def test_side_and_tracked_anchors_are_refused():
    with pytest.raises(titles.SideAnchorNotProjected, match="bottom_left"):
        titles.project_titles(
            {"motion_moments": [_motion_moment(anchor="bottom_left")]},
            _comp(), 30.0, _safe())
    with pytest.raises(titles.SideAnchorNotProjected, match="tracked"):
        titles.project_titles(
            {"motion_moments": [_motion_moment(anchor="tracked")]},
            _comp(), 30.0, _safe())


def test_unsafe_and_out_of_comp_titles_are_refused():
    with pytest.raises(titles.TitleOutsideSafeArea, match="interface"):
        titles.project_titles(
            _timed_doc([_timed_moment(x=0.02, y=0.97)]),
            _comp(), 30.0, _safe())
    with pytest.raises(titles.TitleOutsideComp, match="outside"):
        titles.project_titles(
            _timed_doc([_timed_moment(startFrame=90, durationFrames=60)]),
            _comp(), 30.0, _safe())
    with pytest.raises(titles.TitleOutsideComp, match="outside"):
        titles.project_titles(
            {"motion_moments": [_motion_moment(startFrame=0,
                                               durationFrames=6000)]},
            _comp(), 30.0, _safe())


def test_bad_fills_and_empty_documents_are_refused():
    with pytest.raises(titles.TitleProjectionError, match="fill"):
        titles.project_titles(
            _timed_doc([_timed_moment(color="red")]),
            _comp(), 30.0, _safe())
    with pytest.raises(titles.TitleProjectionError, match="no text layer"):
        titles.project_titles({}, _comp(), 30.0, _safe())
    with pytest.raises(titles.TitleProjectionError, match="mapping"):
        titles.project_titles([], _comp(), 30.0, _safe())  # type: ignore[arg-type]
    with pytest.raises(titles.TitleProjectionError, match="render spec"):
        titles.build_titles_request(
            _timed_doc([_timed_moment()]), _comp(), 30.0, _safe(),
            renders=[])


def test_standalone_request_carries_comp_render_and_identity():
    request = titles.build_titles_request(
        _timed_doc([_timed_moment()]), _comp(), 30.0, _safe(),
        renders=[{"format": "rgba_png_sequence", "comp": "Main",
                  "output": "titles_#####.png"}],
        project_id="titles-standalone")
    assert request["operations"][0] == {
        "op": "new_comp", "name": "Main", "width": 1080, "height": 1920,
        "frame_rate": 30.0, "duration": 3.0}
    assert request["operations"][1]["op"] == "add_text"
    assert request["project_id"] == "titles-standalone"
    assert request["identity_map"]["titles"][0]["text"] == "Night 1"


def test_non_fade_motion_lands_a_loss_not_a_different_motion():
    moment = _motion_moment(entrance="slide", exit="cut")
    projection = titles.project_titles(
        {"motion_moments": [moment]}, _comp(), 30.0, _safe())
    assert {"reason": "entrance_motion_not_carried",
            "detail": "motion-graphics moment 'title_lockup' entrance='slide': "
                      "only its timing is keyframed (opacity over 8 "
                      "composition frames); the spatial character is not "
                      "carried"} in projection.losses
    # The timing is still exact: the ramp lasts the quoted 8 frames.
    keys = [op for op in projection.operations if op["op"] == "keyframe"]
    assert keys[1]["time"] == pytest.approx(8 / 30.0)


def test_type_size_quote_matches_the_composition():
    """The quoted TYPE_SIZE is the composition's, or this fails loudly."""
    source = (ROOT / "remotion-subtitles/src/compositions/MotionGraphics"
              / "index.tsx").read_text(encoding="utf-8")
    block = re.search(r"const TYPE_SIZE[^;]*?\{([^}]*)\}", source,
                      re.DOTALL).group(1)
    quoted = dict(titles.TYPE_SIZE_BY_ROLE)
    for role, value in re.findall(r"(\w+)\s*:\s*([\d.]+)", block):
        assert quoted[role] == pytest.approx(float(value)), role
    assert set(quoted) == {"display", "supporting", "micro"}
    ramps = re.search(r"const RAMP_FRAMES[^;]*?\{([^}]*)\}", source,
                      re.DOTALL).group(1)
    for character, frames in re.findall(r"(\w+)\s*:\s*(\d+)", ramps):
        assert titles.RAMP_FRAMES[character] == int(frames), character


def test_project_to_request_carries_titles_after_footage():
    model = json.loads(
        (ROOT / "tests/fixtures/effectcraft_fframes_conformance"
         / "model-masked.json").read_text(encoding="utf-8"))
    comp = ec.CompSpec(name="Main", width=1080, height=1920,
                       frame_rate=30.0, duration_seconds=3.0)
    renders = [ec.RenderSpec(format="rgba_png_sequence",
                             output="titled_#####.png")]
    safe = _safe()
    document = _timed_doc([_timed_moment()])
    projection = ec.project_to_request(
        model, comp, renders, project_id="ec-titles-wired",
        titles=document, titles_fps=30.0, titles_safe_area=safe)
    kinds = [op["op"] for op in projection.request["operations"]]
    assert kinds[:4] == ["new_comp", "import_footage",
                         "add_footage_layer", "add_mask"]
    assert kinds[4] == "add_text"
    assert projection.request["operations"][4]["text"] == "Night 1"
    assert projection.identity_map["titles"] == [
        {"layer": "timed-text-0", "kind": "timed_text", "text": "Night 1",
         "position": [540.0, 960.0], "appear_seconds": 1.0,
         "disappear_seconds": 3.0}]
    assert projection.identity_map["masks"] == \
        {"mask-checker": "checker.bmp"}


def test_project_to_request_titles_need_fps_and_safe_area():
    model = json.loads(
        (ROOT / "tests/fixtures/effectcraft_fframes_conformance"
         / "model-masked.json").read_text(encoding="utf-8"))
    comp = ec.CompSpec(name="Main", width=1080, height=1920,
                       frame_rate=30.0, duration_seconds=3.0)
    renders = [ec.RenderSpec(format="rgba_png_sequence",
                             output="titled_#####.png")]
    with pytest.raises(ec.EffectCraftAdapterError, match="titles_fps"):
        ec.project_to_request(model, comp, renders,
                              titles=_timed_doc([_timed_moment()]))
    # Titles alone (no masks or effects) still project: an overlay comp
    # needs no footage. Footage layers still import (the model carries
    # clip uses); the titles ride after them.
    bare = copy.deepcopy(model)
    bare["masks"] = []
    projection = ec.project_to_request(
        bare, comp, renders, titles=_timed_doc([_timed_moment()]),
        titles_fps=30.0, titles_safe_area=_safe())
    kinds = [op["op"] for op in projection.request["operations"]]
    assert kinds[0] == "new_comp"
    assert "add_text" in kinds
    assert projection.identity_map["titles"][0]["layer"] == "timed-text-0"


def test_cli_projects_titles_off_a_document(tmp_path):
    model_path = (ROOT / "tests/fixtures/effectcraft_fframes_conformance"
                  / "model-masked.json")
    titles_path = tmp_path / "titles.json"
    titles_path.write_text(json.dumps(_timed_doc([_timed_moment()])),
                           encoding="utf-8")
    out_path = tmp_path / "projection.json"
    rc = ec.main(["project", str(model_path), "--out", str(out_path),
                  "--width", "1080", "--height", "1920", "--fps", "30.0",
                  "--duration", "3.0", "--render-output",
                  "titled_#####.png", "--titles", str(titles_path),
                  "--titles-fps", "30.0",
                  "--project-id", "ec-titles-cli"])
    assert rc == 0
    stored = json.loads(out_path.read_text(encoding="utf-8"))["projection"]
    assert stored["request"]["operations"][4] == {
        "op": "add_text", "name": "timed-text-0", "text": "Night 1",
        "size": 64.0, "fill": "#D4A34A", "position": [540.0, 960.0],
        "font": "Montserrat"}
    assert stored["identity_map"]["titles"][0]["layer"] == "timed-text-0"


# Census S5: the real geo-podcast title_lockup copy overflows the comp.
# Runs quoted from geo-podcast's 4.06 props
# (mg_geo-podcast_03cfd15b_tight_props.json: 844x480, 144f): the display
# run inks 898px at 56px as one unbroken line and clipped at both frame
# edges on the census's 540-wide probe comp.
GEO_DISPLAY_COPY = "SHOW UP MOST WINS GOOGLE"
GEO_SUPPORTING_COPY = "MATTER MOST WINS AI"


def _geo_motion_moment(**overrides):
    moment = _motion_moment(
        runs=[{"text": GEO_DISPLAY_COPY, "type_role": "display",
               "uppercase": False},
              {"text": GEO_SUPPORTING_COPY, "type_role": "supporting",
               "uppercase": False}],
        entrance="cut", exit_="cut", startFrame=0, durationFrames=15)
    moment.update(overrides)
    return moment


def test_copy_width_measures_the_bundled_face_at_run_size():
    pytest.importorskip("PIL.ImageFont", reason="Pillow absent")
    width = titles._copy_width_px(GEO_DISPLAY_COPY, 56.0)
    assert width == pytest.approx(898.0, abs=5.0)
    assert titles._copy_width_px("Night 1", 64.0) < 540.0
    # Multi-line copy is bounded by its longest line.
    assert titles._copy_width_px(
        f"AB\n{GEO_DISPLAY_COPY}", 56.0) == pytest.approx(width)


def test_overflowing_geo_podcast_copy_is_refused_by_name():
    comp = _comp(width=540, height=960, fps=30.0, duration=3.0)
    with pytest.raises(titles.CopyOverflowsCompWidth,
                       match="SHOW UP MOST WINS GOOGLE"):
        titles.project_titles(
            {"motion_moments": [_geo_motion_moment()]},
            comp, 30.0, _safe(width=540, height=960))


def test_overflowing_timed_text_is_refused_by_name():
    comp = _comp(width=540, height=960, fps=30.0, duration=3.0)
    moment = _timed_moment(text=GEO_DISPLAY_COPY, fontSize=64,
                           x=0.5, y=0.5)
    with pytest.raises(titles.CopyOverflowsCompWidth,
                       match="540px comp"):
        titles.project_titles(
            _timed_doc([moment]), comp, 30.0,
            _safe(width=540, height=960))


def test_fitting_copy_projects_with_no_overflow_bookkeeping():
    projection = titles.project_titles(
        {"motion_moments": [_motion_moment(
            runs=[{"text": GEO_SUPPORTING_COPY,
                   "type_role": "supporting", "uppercase": False}])]},
        _comp(width=540, height=960, fps=30.0, duration=3.0), 30.0,
        _safe(width=540, height=960))
    assert [op for op in projection.operations
            if op["op"] == "add_text"]
    assert all(loss["reason"] != "copy_width_unmeasured"
               for loss in projection.losses)


def test_unmeasurable_copy_records_a_loss_and_still_projects(monkeypatch):
    monkeypatch.setattr(titles, "_copy_width_px",
                        lambda text, size: None)
    projection = titles.project_titles(
        {"motion_moments": [_motion_moment()]},
        _comp(), 30.0, _safe())
    assert [op for op in projection.operations
            if op["op"] == "add_text"]
    assert any(loss["reason"] == "copy_width_unmeasured"
               for loss in projection.losses)
