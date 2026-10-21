"""`caption_fix` rewrites a rendered caption card and swaps it in place.

The defect this pins: every caption in a real Ren reel is a rendered
`.mov` card, and `caption_fix` refused before mutation - so the one
operation that answers "that caption says the wrong word" could not
address the medium the product actually uses.

The adapter therefore re-renders the card through the caption render
path and swaps the new file onto the placement, and it proves from
read-back that the TEXT changed, the TIMING did not, and nothing else
on the timeline moved.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from library.tools import edit_execution_adapters as adapters


def _cue(text="the wrong number"):
    """One caption cue, with the word windows it was measured with."""
    return {
        "text": text,
        "startFrame": 0,
        "endFrame": 48,
        "emphasisWords": ["number"],
        "words": [{"word": "the", "startFrame": 0, "endFrame": 8},
                  {"word": "wrong", "startFrame": 8, "endFrame": 24},
                  {"word": "number", "startFrame": 24, "endFrame": 48}],
        "fitScale": 1.0,
    }


def _project_style(project_folder, speaker="Akshita"):
    """The style the render entry expects for this project."""
    from library.tools.subtitle_style import resolve_subtitle_style

    return resolve_subtitle_style({}, {}, str(project_folder),
                                  speaker=speaker, reel_name=None)


def _card_props(project_folder, text="the wrong number",
                speaker="Akshita"):
    """One rendered caption card's render props - a TIGHT card.

    A real reel's captions are tight: the renderer draws the card
    natively at the tight canvas and writes the tight props beside it,
    so this is what a caption fix reads off a built reel.
    """
    from library.tools.tight_box import constant_caption_box

    full = {
        "subtitles": [_cue(text)],
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 60,
        "style": dict(_project_style(project_folder)),
        "_speaker": speaker,
    }
    box = constant_caption_box(full)
    tight = dict(box.props)
    tight["subtitles"] = full["subtitles"]
    tight["_speaker"] = full["_speaker"]
    return tight


def _clip(source_file, unique_id="card-1"):
    return {
        "unique_id": unique_id, "record_in": 96, "record_out": 144,
        "source_in_frame": 0, "source_out_frame": 48,
        "duration": 48, "source_file": str(source_file),
        "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0},
        "enabled": True, "clip_color": "", "flags": [],
        "track_type": "video", "track_index": 3,
        "linked_unique_ids": [],
    }


def _snapshot(clip, name="Subtitles"):
    return {"reported_fps": 30.0, "tracks": [
        {"type": "video", "index": 3, "name": name, "clips": [clip]},
    ]}


def _context(operation, snapshot):
    return SimpleNamespace(
        operation=operation,
        snapshot=snapshot,
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening",
                                operations=[operation]),
    )


@pytest.fixture
def project_dir(tmp_path):
    """A project that declares the delivery frame and the tight look."""
    (tmp_path / "project.yaml").write_text(
        "project_name: caption-fix-adapter\n"
        "pipeline:\n"
        "  delivery_format: vertical_1080x1920\n"
        "  subtitle_typography:\n"
        "    font: Montserrat\n"
        "    size: 51\n"
        "  subtitle_overlay_geometry: tight\n",
        encoding="utf-8")
    return tmp_path


@pytest.fixture
def card(tmp_path, project_dir):
    """A card on disk with its render props beside it."""
    old_file = tmp_path / "card.mov"
    old_file.write_bytes(b"old")
    (tmp_path / "card_props.json").write_text(
        json.dumps(_card_props(project_dir)), encoding="utf-8")
    return old_file


@pytest.fixture
def render(monkeypatch, tmp_path):
    """The caption render path, faked: it draws the new file and its
    own props, and swaps the placement."""
    from library.tools import operations as pipeline_operations

    calls = []

    def _run(project_folder, pairs, swap=True, **_kwargs):
        calls.append({"project_folder": project_folder, "pairs": pairs,
                      "swap": swap})
        report = {"ok": True, "rendered": 0, "reused": 0, "swapped": [],
                  "already_current": [], "failed": [], "map": {}}
        for pair in pairs:
            new_file = tmp_path / "card_new.mov"
            new_file.write_bytes(b"new")
            # The renderer draws the card TIGHT: it re-derives the tight
            # canvas from the full props it is handed, and the props it
            # writes beside the file carry that tight canvas.
            from library.tools.tight_box import constant_caption_box

            box = constant_caption_box(dict(pair["props"]))
            props = dict(box.props)
            props["subtitles"] = pair["props"]["subtitles"]
            (tmp_path / "card_new_props.json").write_text(
                json.dumps(props), encoding="utf-8")
            report["rendered"] += 1
            report["map"][pair["old_mov"]] = str(new_file)
        return report

    real_get = pipeline_operations.get

    def _get(name):
        if name == "subtitles.rerender_swap":
            return SimpleNamespace(run=_run)
        return real_get(name)

    monkeypatch.setattr(pipeline_operations, "get", _get)
    return calls


@pytest.fixture
def live(monkeypatch):
    """The live timeline read-back: the card plays the OLD file until
    the swap lands, and the NEW file after it."""
    from library.tools import resolve_axi_core

    state = {"reads": []}

    def _read(*_args):
        if not state["reads"]:
            raise AssertionError("the adapter read the live timeline "
                                 "more times than the test expected")
        return state["reads"].pop(0)

    monkeypatch.setattr(resolve_axi_core, "read_live_timeline", _read)
    return state


@pytest.fixture
def project(monkeypatch, tmp_path):
    """The project folder a caption fix renders into."""
    monkeypatch.setattr(adapters, "_project_folder",
                        lambda _context: str(tmp_path))
    return tmp_path


def test_a_caption_fix_re_renders_the_card_and_swaps_it_in_place(
        card, render, live, project):
    """The defect: the fix refused because the timeline inventory does
    not expose caption text. The card is re-rendered and swapped."""
    live["reads"] = [_snapshot(_clip(card)),
                     _snapshot(_clip(project / "card_new.mov"))]
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(card)))

    receipt = adapters._caption_fix(context)

    assert receipt["status"] == "committed"
    assert receipt["mutated"] is True
    # The render path drew the card with the new text.
    assert len(render) == 1
    pair = render[0]["pairs"][0]
    assert pair["old_mov"] == str(card)
    cue = pair["props"]["subtitles"][0]
    assert cue["text"] == "the right number"
    assert (cue["startFrame"], cue["endFrame"]) == (0, 48)
    # The placement plays the new file and kept its span.
    assert receipt["readback"]["new_file"] == str(project / "card_new.mov")
    assert receipt["readback"]["previous_file"] == str(card)
    assert receipt["readback"]["applied_text"] == "the right number"
    assert receipt["acceptance_contracts"][0]["passed"] is True
    assert receipt["execution_contract"]["verification"]["status"] == (
        "verified")


def test_a_caption_fix_refuses_a_target_that_is_not_a_caption_card(card):
    """A fix on a non-caption would mislabel the executed write."""
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(card), name="V2"))

    with pytest.raises(ValueError, match="not the 'Subtitles'"):
        adapters._caption_fix(context)


def test_a_caption_fix_refuses_before_mutation_when_the_text_does_not_fit(
        card, render, live, project):
    """A card whose new text cannot be timed inside its measured span
    is not drawn - and nothing is rendered."""
    live["reads"] = [_snapshot(_clip(card))]
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number of the year we lost"},
                       _snapshot(_clip(card)))

    with pytest.raises(RuntimeError, match="cannot be timed inside"):
        adapters._caption_fix(context)

    assert render == []


def test_a_caption_fix_refuses_a_card_that_already_reads_that_text(
        card, render, live, project):
    live["reads"] = [_snapshot(_clip(card))]
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the wrong number"},
                       _snapshot(_clip(card)))

    with pytest.raises(RuntimeError, match="already reads that text"):
        adapters._caption_fix(context)

    assert render == []


def test_a_caption_fix_refuses_without_a_card_or_a_text(card, render, live,
                                                         project):
    context = _context({"op": "caption_fix", "unique_id": "",
                        "text": "the right number"},
                       _snapshot(_clip(card)))
    with pytest.raises(ValueError, match="names no unique_id"):
        adapters._caption_fix(context)

    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "  "}, _snapshot(_clip(card)))
    with pytest.raises(ValueError, match="needs the card's new text"):
        adapters._caption_fix(context)


def test_a_caption_fix_that_moved_the_timing_is_refused(card, render, live,
                                                         project):
    """The timing is the thing a fix must never move."""
    live["reads"] = [_snapshot(_clip(card))]
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(card)))

    from library.tools import caption_rewrite

    def _moved(props, new_text):
        moved = dict(props)
        cue = dict(props["subtitles"][0])
        cue["startFrame"] = 12
        moved["subtitles"] = [cue]
        return moved

    original = caption_rewrite.rewrite_props
    caption_rewrite.rewrite_props = _moved
    try:
        with pytest.raises(RuntimeError, match="would move the caption's"):
            adapters._caption_fix(context)
    finally:
        caption_rewrite.rewrite_props = original

    assert render == []


def test_a_caption_fix_rebuilds_a_tight_card_as_the_projects_own_props(
        card, render, live, project, project_dir):
    """The defect a real reel exposed: the card's sidecar records the
    TIGHT canvas and the tight style geometry, while the render entry
    refuses anything that is not the project's own frame and geometry.
    A fix that handed the sidecar straight back refused on every reel."""
    live["reads"] = [_snapshot(_clip(card)),
                     _snapshot(_clip(project / "card_new.mov"))]
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(card)))

    adapters._caption_fix(context)

    props = render[0]["pairs"][0]["props"]
    assert (props["width"], props["height"]) == (1080, 1920)
    assert props["style"]["safeArea"] == _project_style(project_dir)[
        "safeArea"]
    # The card's own typography rides through: the approved styling is
    # what the re-render draws.
    assert props["style"]["fontSize"] == _card_props(project_dir)[
        "style"]["fontSize"]


def test_a_caption_fix_refuses_before_mutation_when_the_card_would_be_redrawn_on_another_canvas(
        card, render, live, project, tmp_path, project_dir):
    """Defect: the placement carries a transform computed for the
    canvas the card was drawn on, so a card redrawn on another canvas
    would sit in the wrong box."""
    props = json.loads((tmp_path / "card_props.json").read_text(
        encoding="utf-8"))
    props["width"] = 640
    (tmp_path / "card_props.json").write_text(json.dumps(props),
                                              encoding="utf-8")
    live["reads"] = [_snapshot(_clip(card))]
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(card)))

    with pytest.raises(RuntimeError, match="canvas"):
        adapters._caption_fix(context)

    assert render == []


def test_a_caption_fix_refuses_a_card_whose_wrap_no_longer_matches_the_project(
        card, render, live, project, tmp_path, project_dir):
    """A re-render at the project's width would redraw an approved card
    with different line breaks."""
    props = json.loads((tmp_path / "card_props.json").read_text(
        encoding="utf-8"))
    props["style"] = dict(props["style"], captionMaxWidth=400)
    (tmp_path / "card_props.json").write_text(json.dumps(props),
                                              encoding="utf-8")
    live["reads"] = [_snapshot(_clip(card))]
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(card)))

    with pytest.raises(RuntimeError, match="no longer matches"):
        adapters._caption_fix(context)

    assert render == []


def test_a_caption_fix_proves_the_approved_styling_survived(
        card, render, live, project):
    """The fix changes what the card says, never how it looks."""
    live["reads"] = [_snapshot(_clip(card)),
                     _snapshot(_clip(project / "card_new.mov"))]
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(card)))

    receipt = adapters._caption_fix(context)

    acceptance = receipt["acceptance_contracts"][0]
    assert "caption_styling_preserved" in acceptance["checks"]
    assert acceptance["passed"] is True


def test_the_project_folder_resolves_from_the_resolve_project_name(
        monkeypatch, tmp_path):
    """Defect: the production request names the RESOLVE project - that
    is what the broker keys on and what every AXI verb addresses - and
    the caption adapters could not resolve the project folder from it,
    so a caption fix could not run on a real project at all."""
    from library.tools import project_registry

    monkeypatch.setattr(project_registry, "project_folder_for_resolve_name",
                        lambda name: str(tmp_path / "scratch"))
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(tmp_path / "card.mov")))
    context.request.project = "Ren Scratch - a reel"

    assert adapters._project_folder(context) == str(tmp_path / "scratch")


def test_a_project_folder_reference_still_resolves(monkeypatch, tmp_path):
    """A request that names the folder itself still resolves the same
    way it always did."""
    from library.tools import project_registry

    (tmp_path / "project.yaml").write_text("project_name: x\n",
                                           encoding="utf-8")
    monkeypatch.setattr(project_registry, "project_folder_for_resolve_name",
                        lambda name: "")
    context = _context({"op": "caption_fix", "unique_id": "card-1",
                        "text": "the right number"},
                       _snapshot(_clip(tmp_path / "card.mov")))
    context.request.project = str(tmp_path)

    assert adapters._project_folder(context) == str(tmp_path)
