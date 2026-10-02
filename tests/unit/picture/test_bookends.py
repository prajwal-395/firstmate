"""Q7: intros, outros and end cards, declared per template.

Captain's ruling Q7 (2026-08-16) was "wire them up, but only on some
videos", so the thing under test is a mechanism with nothing behind it by
default.  These cover the whole path a declared card takes:

- the declaration parses, and a malformed one raises rather than vanishes;
- ``mesh_spine`` turns a declaration into a spine block and shifts the
  edit around it, and drops any card the LLM invented;
- the spine contract rejects a card block that names no file;
- ``compile_manifest`` puts a declared end card on V1, with the timeline
  duration and the coverage assertion accounting for it;
- a template that declares nothing produces no block and no clip;
- the props a card renders from are deterministic, byte for byte.

The Remotion render itself is exercised by the pipeline, not here: what is
checkable without a browser is that the same declaration always produces
the same props file, which is what makes the same card come out.
"""
import copy
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_2_05_mesh_spine.post_bridge import enrich_spine
from library.tools.bookend_render import bookend_props
from library.tools.bookends import (
    MAX_BOOKEND_SECONDS,
    BookendDeclarationError,
    InventedBookendBlock,
    bookend_spine_block,
    declared_bookends,
    resolve_bookend,
)
from library.tools.spine_contract import (
    SpineContractError,
    validate_spine_blocks,
)

END_CARD_DECLARATION = {
    "bookends": {
        "end_card": {
            "asset": "assets/end_card.mov",
            "duration_seconds": 5.0,
        }
    }
}

COMPOSITION_DECLARATION = {
    "bookends": {
        "intro": {
            "composition": "ExampleIntro",
            "source": "compositions/ExampleIntro.tsx",
            "duration_seconds": 3.0,
            "props": {"accentColor": "#E8A33D"},
        }
    }
}


@pytest.fixture(autouse=True)
def mock_resolve_project_asset(monkeypatch):
    def fake_resolve(declared_path, project_folder):
        # Always return the joined path to pretend the file exists
        if os.path.isabs(declared_path):
            return os.path.normpath(declared_path)
        return os.path.normpath(os.path.join(project_folder or "", declared_path))
    monkeypatch.setattr("library.tools.bookends.resolve_project_asset", fake_resolve)

# ─────────────────────────────────────────────────────────
# The declaration
# ─────────────────────────────────────────────────────────

def test_a_template_declaring_nothing_gets_nothing():
    assert declared_bookends(None) == []
    assert declared_bookends({}) == []
    assert declared_bookends({"bookends": {}}) == []
    assert declared_bookends({"series_title": "x"}) == []


def test_an_asset_declaration_normalises():
    (decl,) = declared_bookends(END_CARD_DECLARATION)
    assert decl["slot"] == "end_card"
    assert decl["block_type"] == "end_card"
    assert decl["placement"] == "tail"
    assert decl["mode"] == "asset"
    assert decl["duration_seconds"] == 5.0
    # A card is silent unless the template says otherwise.
    assert decl["has_audio"] is False


def test_slots_are_emitted_head_then_tail():
    slots = [d["slot"] for d in declared_bookends({"bookends": {
        "end_card": {"asset": "a.mov", "duration_seconds": 5},
        "outro": {"asset": "b.mov", "duration_seconds": 2},
        "intro": {"asset": "c.mov", "duration_seconds": 3},
    }})]
    assert slots == ["intro", "outro", "end_card"]


@pytest.mark.parametrize("bad, fragment", [
    ({"nightcard": {"asset": "a.mov", "duration_seconds": 2}}, "unknown bookend slot"),
    ({"end_card": {"duration_seconds": 2}}, "exactly one of"),
    ({"end_card": {"asset": "a.mov", "composition": "X", "duration_seconds": 2}}, "exactly one of"),
    ({"end_card": {"asset": "a.mov", "source": "x.tsx", "duration_seconds": 2}}, "nothing to build it from"),
    ({"end_card": {"composition": "NotRegistered", "duration_seconds": 2}}, "does not register"),
    ({"end_card": {"asset": "a.mov"}}, "duration_seconds"),
    ({"end_card": {"asset": "a.mov", "duration_seconds": 0}}, "nothing on screen"),
    ({"end_card": {"asset": "a.mov", "duration_seconds": MAX_BOOKEND_SECONDS + 1}}, "longer than"),
    ({"end_card": {"asset": "a.mov", "duration_seconds": 2, "props": []}}, "props must be a mapping"),
    ({"end_card": "a.mov"}, "must be a mapping"),
])
def test_a_malformed_declaration_raises(bad, fragment):
    """A declaration nobody can honour must fail loudly, not vanish.

    A dropped declaration is a card the editor believes shipped.
    """
    with pytest.raises(BookendDeclarationError) as exc:
        declared_bookends({"bookends": bad})
    assert fragment in str(exc.value)


# ─────────────────────────────────────────────────────────
# Path resolution
# ─────────────────────────────────────────────────────────

def test_an_asset_path_resolves_against_the_project():
    (decl,) = declared_bookends(END_CARD_DECLARATION)
    resolved = resolve_bookend(decl, "/projects/example")
    assert resolved["asset_path"] == "/projects/example/assets/end_card.mov"
    assert resolved["source_path"] == ""


# ─────────────────────────────────────────────────────────
# The spine block
# ─────────────────────────────────────────────────────────

def _speech_spine():
    # The opening block references its passage by POSITION, like every
    # other block.  Step 2.2 emits one ordered body_sequence and names no
    # opener - the `hook_segment` this fixture used to address as the
    # literal "hook" was withdrawn with the closed role vocabulary on the
    # captain's ruling of 2026-09-02.  Which passage opens is decided in
    # mesh_spine, by which passage the `hook` BLOCK points at.
    return {
        "structure": [
            {"position": "hook", "block_type": "hook",
             "content": {"passage_ref": 1}, "duration_seconds": 3.0},
            {"position": 1, "block_type": "speech",
             "content": {"passage_ref": 2}, "duration_seconds": 4.0},
        ],
        "frame_rate": 30.0,
    }


def _speech_sequence():
    return {
        "body_sequence": [{
            "position": 1, "clip_id": "clip_001",
            "source_start": 1.234, "source_end": 4.234,
            "text": "what even is today?", "alignment_method": "whisperx",
            "word_timestamps": [
                {"word": "what", "source_start": 1.234, "source_end": 1.9}],
        }, {
            "position": 2, "clip_id": "clip_001",
            "source_start": 10.111, "source_end": 14.111,
            "text": "a small announcement", "alignment_method": "whisperx",
            "word_timestamps": [
                {"word": "a", "source_start": 10.111, "source_end": 10.5}],
        }],
    }


# The spine above runs 7.0s of speech. The zone is set to match it, so a
# failure here is about the card and not about the fixture's length.
SPEECH_ONLY_TARGET = {"target_duration_seconds": 7.0}


def _enriched(brand_content, project_folder="/projects/example"):
    return enrich_spine(
        _speech_spine(), _speech_sequence(), {},
        {"brand_content": brand_content, "project_folder": project_folder,
         "project_config": SPEECH_ONLY_TARGET},
    )["audio_spine"]


def test_a_declared_end_card_becomes_a_tail_block():
    spine = _enriched(END_CARD_DECLARATION)
    structure = spine["structure"]
    assert [b["block_type"] for b in structure] == \
        ["hook", "speech", "end_card"]

    card = structure[-1]
    assert card["position"] == "end_card"
    # The speech ahead of it is untouched, and the card is appended to it.
    assert card["timeline_start"] == structure[-2]["timeline_end"]
    assert card["timeline_end"] == card["timeline_start"] + 5.0
    assert spine["total_estimated_duration_seconds"] == pytest.approx(
        structure[-1]["timeline_end"])
    # 7.0s of speech against a 6.3-7.7s zone: the spine validated only
    # because the fixed 5s brand card is not charged against the target.
    assert spine["total_estimated_duration_seconds"] == pytest.approx(12.0)


def test_a_declared_intro_shifts_the_whole_edit():
    """An intro card cannot overlap the hook - it precedes it."""
    plain = _enriched({})["structure"]
    shifted = _enriched(COMPOSITION_DECLARATION)["structure"]

    assert shifted[0]["block_type"] == "intro_card"
    assert shifted[0]["timeline_start"] == 0.0
    assert shifted[0]["timeline_end"] == 3.0
    for before, after in zip(plain, shifted[1:]):
        assert after["timeline_start"] == before["timeline_start"] + 3.0
        assert after["timeline_end"] == before["timeline_end"] + 3.0


def test_a_card_the_llm_invented_refuses_the_step():
    """Which card a video shows is a brand decision, not a per-run one.

    It used to be dropped with a line on stderr. The plan around a card is
    written knowing the card is there, so the drop shipped an edit
    designed for a moment it no longer had, and nothing said so.
    """
    spine = _speech_spine()
    spine["structure"].append({
        "position": "outro", "block_type": "end_card",
        "content": {}, "duration_seconds": 6.0,
    })
    with pytest.raises(InventedBookendBlock) as exc:
        enrich_spine(
            spine, _speech_sequence(), {},
            {"brand_content": {}, "project_config": SPEECH_ONLY_TARGET},
        )
    # By name: which block, and which type.
    assert "'outro'" in str(exc.value)
    assert "'end_card'" in str(exc.value)


def test_a_breath_of_music_is_not_a_card_and_is_kept():
    """`intro` and `outro` are the plan's own non-speech beats.

    The refusal is on `intro_card`/`outro_card`/`end_card` only. An
    `intro` block is a breath of music and B-roll, which the handoff's
    block-type table offers and the spine contract carries.
    """
    spine = _speech_spine()
    spine["structure"].insert(1, {
        "position": 1, "block_type": "intro",
        "content": {}, "duration_seconds": 2.0,
        "music_behavior": "prominent",
    })
    structure = enrich_spine(
        spine, _speech_sequence(), {},
        {"brand_content": {},
         "project_config": {"target_duration_seconds": 9.0}},
    )["audio_spine"]["structure"]
    assert [b["block_type"] for b in structure] == ["hook", "intro", "speech"]


# ─────────────────────────────────────────────────────────
# The spine contract
# ─────────────────────────────────────────────────────────

def _valid_card_block():
    (decl,) = declared_bookends(END_CARD_DECLARATION)
    block = bookend_spine_block(resolve_bookend(decl, "/projects/example"))
    block["timeline_start"] = 10.0
    block["timeline_end"] = 15.0
    return block


@pytest.mark.parametrize("change, fragment", [
    # A card block with nothing behind it is a hole wearing a name.
    ({"content": {}}, "carries no content.bookend"),
    ({"intentional_black_beat": True,
      "black_beat_reason": "hold before the reveal"}, "cannot both be true"),
])
def test_the_contract_rejects_a_malformed_card_block(change, fragment):
    block = _valid_card_block()
    block.update(change)
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks([block])
    assert fragment in str(exc.value)


# ─────────────────────────────────────────────────────────
# The manifest: a declared card reaches the assembly
# ─────────────────────────────────────────────────────────

def _compile_inputs(tmp_path, structure, extra_files=()):
    aroll = tmp_path / "aroll.mov"
    aroll.write_text("dummy")
    for path in extra_files:
        path.write_text("dummy")
    return {
        "a_roll_assignments": [{
            "clip_id": "clip_1", "source_clip_id": "clip_1",
            "source_file": str(aroll),
            "video_in": 17.666, "video_out": 19.548,
            "timeline_start": 0.0, "timeline_end": 1.882,
        }],
        "b_roll_assignments": [],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []},
        "transition_spec": [],
        "enhancement_spec": [],
        "color_grade_spec": {},
        "audio_mix_spec": {},
        "music_selection": {},
        "audio_spine": {"structure": structure, "frame_rate": 30.0},
        "clip_catalog": [{
            "clip_id": "clip_1", "path": str(aroll),
            "width": 1080, "height": 1920,
        }],
        # A real document that joins: compile_manifest fails a semantic
        # analysis that carries documents none of which reach a clip.
        "semantic_analysis": {
            "semantic_analysis_documents": [{
                "clip_id": "clip_1",
                "analysis": {"scene": "A speaker on a city street."},
                "assessment": {"clip_type": "a-roll", "keywords": ["speaker"]},
            }]
        },
    }


def _speech_block():
    return {
        "block_type": "speech", "position": 1, "clip_id": "clip_1",
        "source_start": 17.666, "source_end": 19.548,
        "timeline_start": 0.0, "timeline_end": 1.882,
        "content": {"clip_id": "clip_1", "link_group_id": "lg_1"},
    }


def _compile(inputs):
    from unittest.mock import patch

    from library.steps.step_5_04_compile_manifest.step import compile_manifest
    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        return compile_manifest("dummy")


def test_a_declared_end_card_is_in_the_assembly_manifest(tmp_path):
    from library.steps.step_5_04_compile_manifest.step import (
        _assert_timeline_fully_covered,
    )
    from library.tools.manifest_validator import validate_manifest
    card_file = tmp_path / "end_card.mov"
    (decl,) = declared_bookends({"bookends": {
        "end_card": {"asset": str(card_file), "duration_seconds": 5.0}}})
    card = bookend_spine_block(resolve_bookend(decl, str(tmp_path)))
    card["timeline_start"] = 1.882
    card["timeline_end"] = 6.882
    card["timeline_start_frame"] = 56
    card["timeline_end_frame"] = 206

    manifest = _compile(_compile_inputs(
        tmp_path, [_speech_block(), card], extra_files=[card_file]))

    v1 = manifest["tracks"]["V1"]["clips"]
    cards = [c for c in v1 if c.get("bookend")]
    assert len(cards) == 1
    clip = cards[0]
    assert clip["source_file"] == str(card_file)
    assert clip["label"] == "bookend_end_card"
    assert clip["source_in"] == 0.0
    assert clip["source_out"] == 5.0
    assert clip["timeline_in"] == 1.882
    assert clip["timeline_out"] == 6.882
    # A silent card is not audio: it must not claim a slot on A1.
    assert clip["video_only"] is True
    assert all(not c.get("bookend") for c in manifest["tracks"]["A1"]["clips"])
    # The card is part of the video's length, so render QA can see it.
    assert manifest["project"]["duration_seconds"] == pytest.approx(6.882)
    # Whole seconds are what a correct card looks like, not a fabricated
    # source range.
    assert validate_manifest(manifest) == []

    # The card is inside the coverage assertion (the retired
    # import_endcard.py appended it after compilation, past every gate):
    # take its clip away and the manifest no longer covers its timeline.
    stripped = copy.deepcopy(manifest)
    stripped["tracks"]["V1"]["clips"] = [
        c for c in stripped["tracks"]["V1"]["clips"] if not c.get("bookend")]
    with pytest.raises(ValueError) as exc:
        _assert_timeline_fully_covered(stripped)
    assert "render as black frames" in str(exc.value)


def test_broll_may_not_be_laid_over_a_card():
    """V2 sits above V1, so a cutaway on a card hides it completely."""
    from library.steps.step_5_04_compile_manifest.step import (
        _assert_nothing_covers_a_bookend,
    )
    card = {"label": "bookend_end_card", "bookend": "end_card",
            "timeline_in": 10.0, "timeline_out": 15.0}
    broll = {"label": "broll_3", "timeline_in": 12.0, "timeline_out": 14.0}
    with pytest.raises(ValueError) as exc:
        _assert_nothing_covers_a_bookend([card], [broll])
    assert "hides it completely" in str(exc.value)

    # Abutting B-roll is not covering anything.
    _assert_nothing_covers_a_bookend(
        [card], [{"label": "broll_3", "timeline_in": 5.0,
                  "timeline_out": 10.0}])


def test_the_house_film_look_is_not_painted_over_a_card(tmp_path):
    """A client's finished card must not be regraded by the engine."""
    card_file = tmp_path / "end_card.mov"
    (decl,) = declared_bookends({"bookends": {
        "end_card": {"asset": str(card_file), "duration_seconds": 5.0}}})
    card = bookend_spine_block(resolve_bookend(decl, str(tmp_path)))
    card["timeline_start"] = 1.882
    card["timeline_end"] = 6.882
    card["timeline_start_frame"] = 56
    card["timeline_end_frame"] = 206

    inputs = _compile_inputs(
        tmp_path, [_speech_block(), card], extra_files=[card_file])
    inputs["color_grade_spec"] = {
        "fusion_look": {"glow_gain": 2.0, "grain_strength": 0.4}}
    manifest = _compile(inputs)

    per_clip = manifest["fusion_effects"]["per_clip"]
    assert "speech_1" in per_clip
    assert per_clip["speech_1"]["glow_gain"] == 2.0
    assert "bookend_end_card" not in per_clip


# ─────────────────────────────────────────────────────────
# Determinism: same declaration, same card
# ─────────────────────────────────────────────────────────

def test_declared_props_survive_and_frame_geometry_is_added():
    (decl,) = declared_bookends(COMPOSITION_DECLARATION)
    props = bookend_props(resolve_bookend(decl, "/p"), 30, 1080, 1920)
    assert props["accentColor"] == "#E8A33D"
    assert props["durationInFrames"] == 90
    assert props == {
        "accentColor": "#E8A33D", "durationInFrames": 90,
        "fps": 30, "width": 1080, "height": 1920,
    }


# ─────────────────────────────────────────────────────────
# The synthetic templates
# ─────────────────────────────────────────────────────────

def test_only_the_client_template_declares_a_card():
    """Q7: a general mechanism, nothing by default.

    B1 note: the deleted `test_every_synthetic_template_declares_a_
    parseable_bookend_set` is subsumed here - this loop calls
    `declared_bookends` on every synthetic template, so an unparseable
    set fails here too."""
    from tests.brand_fixtures import ALL_SYNTHETIC
    declaring = set()
    for name, template in sorted(ALL_SYNTHETIC.items()):
        if declared_bookends(template.get("content") or {}):
            declaring.add(name)
    assert declaring == {"synthetic_client"}


# ─────────────────────────────────────────────────────────
# Render route: engine compositions batch, project ones stay CLI
# ─────────────────────────────────────────────────────────

from library.tools import bookend_render as br


ENGINE_DECLARATION = {
    "bookends": {
        "intro": {
            "composition": "TimedTextOverlay",
            "duration_seconds": 0.5,
            "props": {"moments": [], "fontFamily": "Montserrat"},
        },
        "outro": {
            "composition": "TimedTextOverlay",
            "duration_seconds": 0.5,
            "props": {"moments": [], "fontFamily": "Montserrat"},
        },
    }
}


def _engine_structure(tmp_path):
    resolved = [resolve_bookend(d, str(tmp_path))
                for d in declared_bookends(ENGINE_DECLARATION)]
    assert all(r["source_path"] == "" for r in resolved)
    return [bookend_spine_block(r) for r in resolved]


def test_a_failed_engine_bookend_raises_by_slot(monkeypatch, tmp_path):
    """A card that fails is named, not warned past: a hole in the picture."""
    def fake_batch(jobs, **kwargs):
        return [{"ok": False, "out": jobs[0].out_path,
                 "error": "chromium exploded"}
                for job in jobs]

    monkeypatch.setattr(br, "render_batch", fake_batch)
    with pytest.raises(br.BookendRenderError, match="intro"):
        br.render_declared_bookends(
            _engine_structure(tmp_path), str(tmp_path),
            fps=30, width=320, height=568)


def test_a_project_composition_stays_on_the_cli(monkeypatch, tmp_path):
    """A staged project composition is a different bundle root, so it
    cannot join the batch - and stays on `npx remotion render` openly
    rather than silently keeping the slow path."""
    source_dir = tmp_path / "compositions"
    source_dir.mkdir()
    (source_dir / "ExampleIntro.tsx").write_text(
        "export const ExampleIntro = () => null;\n")
    (decl,) = declared_bookends(COMPOSITION_DECLARATION)
    resolved = resolve_bookend(decl, str(tmp_path))
    assert resolved["source_path"] != ""
    structure = [bookend_spine_block(resolved)]

    seen = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        # [..., <composition>, <out>, "--props", ...]: the file follows
        # the composition and precedes the flags.
        out = command[command.index("--props") - 1]
        with open(out, "wb") as handle:
            handle.write(b"not empty")
        return _Result()

    monkeypatch.setattr(br.subprocess, "run", fake_run)

    def no_batch(*args, **kwargs):
        raise AssertionError("a project composition must not reach the batch")

    monkeypatch.setattr(br, "render_batch", no_batch)
    (record,) = br.render_declared_bookends(
        structure, str(tmp_path), fps=30, width=320, height=568)
    assert seen["command"][:3] == ["npx", "remotion", "render"]
    assert "staged" in seen["command"][3], (
        "the CLI remainder renders through the generated staged entry "
        "point, which is why it cannot join the shared bundle")
    assert record["bytes"] > 0
