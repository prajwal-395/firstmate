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
import hashlib
import json
import os
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_2_05_mesh_spine.post_bridge import enrich_spine
from library.tools.bookend_render import bookend_props
from library.tools.bookends import (
    BOOKEND_SLOTS,
    MAX_BOOKEND_SECONDS,
    BookendDeclarationError,
    InventedBookendBlock,
    block_bookend,
    bookend_blocks,
    bookend_render_path,
    bookend_spine_block,
    declared_bookends,
    insert_bookend_blocks,
    resolve_bookend,
)
from library.tools.spine_contract import (
    BOOKEND_BLOCK_TYPES,
    SpineContractError,
    validate_spine_blocks,
)

TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "library", "templates")

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
            "composition": "LucieLogoAnimation",
            "source": "compositions/LucieLogoAnimation.tsx",
            "duration_seconds": 3.0,
            "props": {"accentColor": "#FFAA4D"},
        }
    }
}


def _template(name):
    with open(os.path.join(TEMPLATE_DIR, f"{name}.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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


def test_a_composition_declaration_normalises():
    (decl,) = declared_bookends(COMPOSITION_DECLARATION)
    assert decl["mode"] == "composition"
    assert decl["composition"] == "LucieLogoAnimation"
    assert decl["placement"] == "head"
    assert decl["props"] == {"accentColor": "#FFAA4D"}


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


def test_an_engine_composition_needs_no_source():
    (decl,) = declared_bookends({"bookends": {
        "end_card": {"composition": "TimedTextOverlay", "duration_seconds": 4},
    }})
    assert decl["composition"] == "TimedTextOverlay"
    assert decl["source"] == ""


# ─────────────────────────────────────────────────────────
# Path resolution
# ─────────────────────────────────────────────────────────

def test_an_asset_path_resolves_against_the_project():
    (decl,) = declared_bookends(END_CARD_DECLARATION)
    resolved = resolve_bookend(decl, "/projects/lucie")
    assert resolved["asset_path"] == "/projects/lucie/assets/end_card.mov"
    assert resolved["source_path"] == ""


def test_an_absolute_asset_path_is_left_alone():
    (decl,) = declared_bookends({"bookends": {
        "end_card": {"asset": "/vol/cards/end.mov", "duration_seconds": 5},
    }})
    assert resolve_bookend(decl, "/projects/lucie")["asset_path"] == \
        "/vol/cards/end.mov"


def test_a_composition_renders_to_the_path_the_compiler_reads():
    """One helper owns the filename, so the two steps cannot disagree."""
    (decl,) = declared_bookends(COMPOSITION_DECLARATION)
    resolved = resolve_bookend(decl, "/projects/lucie")
    assert resolved["asset_path"] == bookend_render_path(
        "/projects/lucie", "intro")
    assert resolved["source_path"] == \
        "/projects/lucie/compositions/LucieLogoAnimation.tsx"


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


def _enriched(brand_content, project_folder="/projects/lucie"):
    return enrich_spine(
        _speech_spine(), _speech_sequence(), {},
        {"brand_content": brand_content, "project_folder": project_folder,
         "project_config": SPEECH_ONLY_TARGET},
    )["audio_spine"]


def test_a_template_declaring_nothing_adds_no_block():
    structure = _enriched({})["structure"]
    assert bookend_blocks(structure) == []
    assert [b["block_type"] for b in structure] == ["hook", "speech"]


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


def test_the_refusal_names_every_invented_card_not_just_the_first():
    spine = _speech_spine()
    spine["structure"].insert(0, {
        "position": "opening", "block_type": "intro_card",
        "content": {}, "duration_seconds": 3.0,
    })
    spine["structure"].append({
        "position": "closing", "block_type": "end_card",
        "content": {}, "duration_seconds": 6.0,
    })
    with pytest.raises(InventedBookendBlock) as exc:
        enrich_spine(
            spine, _speech_sequence(), {},
            {"brand_content": {}, "project_config": SPEECH_ONLY_TARGET},
        )
    message = str(exc.value)
    assert "'opening'" in message and "'closing'" in message
    assert "2 card block(s)" in message


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


def test_a_declared_card_does_not_excuse_an_invented_one():
    """The declaration is a separate thing; the plan still wrote a card."""
    spine = _speech_spine()
    spine["structure"].append({
        "position": "outro", "block_type": "end_card",
        "content": {}, "duration_seconds": 6.0,
    })
    with pytest.raises(InventedBookendBlock):
        enrich_spine(
            spine, _speech_sequence(), {},
            {"brand_content": END_CARD_DECLARATION,
             "project_folder": "/projects/lucie",
             "project_config": SPEECH_ONLY_TARGET},
        )


def test_a_card_does_not_spend_the_duration_target():
    """A fixed brand card must not fail a spine that hit its target.

    The zone measures the content the spine planned; the card is not
    something it chose.
    """
    data = {
        "brand_content": END_CARD_DECLARATION,
        "project_folder": "/projects/lucie",
        # 7.0s of speech against a zone of 6.3-7.7s: the spine hit its
        # target exactly, and the 5s card would blow through the ceiling
        # if it were charged against it.
        "project_config": SPEECH_ONLY_TARGET,
    }
    spine = enrich_spine(
        _speech_spine(), _speech_sequence(), {}, data)["audio_spine"]
    # The card is on the timeline...
    assert spine["total_estimated_duration_seconds"] == pytest.approx(12.0)
    # ...and the spine still validated, which it could not have done if
    # the card's 5s counted against an 8s ceiling.
    assert len(bookend_blocks(spine["structure"])) == 1


# ─────────────────────────────────────────────────────────
# The spine contract
# ─────────────────────────────────────────────────────────

def _valid_card_block():
    (decl,) = declared_bookends(END_CARD_DECLARATION)
    block = bookend_spine_block(resolve_bookend(decl, "/projects/lucie"))
    block["timeline_start"] = 10.0
    block["timeline_end"] = 15.0
    return block


def test_the_contract_accepts_a_complete_card_block():
    validate_spine_blocks([_valid_card_block()])


def test_the_contract_rejects_a_card_that_names_no_file():
    block = _valid_card_block()
    block["content"]["bookend"]["asset_path"] = ""
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks([block])
    assert "no asset_path" in str(exc.value)


def test_the_contract_rejects_a_card_with_no_declaration():
    """A card block with nothing behind it is a hole wearing a name."""
    block = _valid_card_block()
    block["content"] = {}
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks([block])
    assert "carries no content.bookend" in str(exc.value)


def test_the_contract_rejects_a_card_with_no_duration():
    block = _valid_card_block()
    block["duration_seconds"] = 0
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks([block])
    assert "a card with no duration" in str(exc.value)


def test_a_card_cannot_also_be_a_black_beat():
    block = _valid_card_block()
    block["intentional_black_beat"] = True
    block["black_beat_reason"] = "hold before the reveal"
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks([block])
    assert "cannot both be true" in str(exc.value)


def test_card_block_types_do_not_collide_with_pacing_beats():
    """`intro`/`outro` already mean a breath of music and B-roll."""
    assert "intro" not in BOOKEND_BLOCK_TYPES
    assert "outro" not in BOOKEND_BLOCK_TYPES
    assert set(BOOKEND_BLOCK_TYPES) == {
        s["block_type"] for s in BOOKEND_SLOTS.values()}


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


def test_a_template_declaring_nothing_puts_no_card_on_v1(tmp_path):
    manifest = _compile(_compile_inputs(tmp_path, [_speech_block()]))
    assert [c for c in manifest["tracks"]["V1"]["clips"]
            if c.get("bookend")] == []
    assert manifest["project"]["duration_seconds"] == pytest.approx(1.882)


def test_the_card_is_inside_the_coverage_assertion(tmp_path):
    """The compile fails if the card's stretch has no clip under it.

    This is the check that makes the manifest route worth having: the
    retired import_endcard.py appended a card AFTER compilation, so no
    gate ever saw the seconds it added.
    """
    from library.steps.step_5_04_compile_manifest.step import (
        _assert_timeline_fully_covered,
    )
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

    # Take the card's clip away and the same manifest no longer covers
    # its own timeline.
    stripped = copy.deepcopy(manifest)
    stripped["tracks"]["V1"]["clips"] = [
        c for c in stripped["tracks"]["V1"]["clips"] if not c.get("bookend")]
    with pytest.raises(ValueError) as exc:
        _assert_timeline_fully_covered(stripped)
    assert "render as black frames" in str(exc.value)


def test_a_card_is_not_a_fabricated_source_range(tmp_path):
    """Whole seconds are what a correct card looks like."""
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
    assert validate_manifest(manifest) == []


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

def _props_digest(decl):
    resolved = resolve_bookend(decl, "/projects/lucie")
    payload = json.dumps(
        bookend_props(resolved, 30, 1080, 1920), indent=2, sort_keys=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def test_render_props_are_deterministic():
    """Same declaration -> byte-identical props file -> the same card."""
    (decl,) = declared_bookends(COMPOSITION_DECLARATION)
    baseline = _props_digest(decl)
    for _ in range(10):
        assert _props_digest(decl) == baseline


def test_declared_props_survive_and_frame_geometry_is_added():
    (decl,) = declared_bookends(COMPOSITION_DECLARATION)
    props = bookend_props(resolve_bookend(decl, "/p"), 30, 1080, 1920)
    assert props["accentColor"] == "#FFAA4D"
    assert props["durationInFrames"] == 90
    assert props == {
        "accentColor": "#FFAA4D", "durationInFrames": 90,
        "fps": 30, "width": 1080, "height": 1920,
    }


def test_a_different_declaration_gives_a_different_card():
    (a,) = declared_bookends(COMPOSITION_DECLARATION)
    other = copy.deepcopy(COMPOSITION_DECLARATION)
    other["bookends"]["intro"]["props"]["accentColor"] = "#00BFFF"
    (b,) = declared_bookends(other)
    assert _props_digest(a) != _props_digest(b)


def test_the_spine_block_is_deterministic():
    (decl,) = declared_bookends(END_CARD_DECLARATION)
    resolved = resolve_bookend(decl, "/projects/lucie")
    first = json.dumps(bookend_spine_block(resolved), sort_keys=True)
    second = json.dumps(bookend_spine_block(resolved), sort_keys=True)
    assert first == second


# ─────────────────────────────────────────────────────────
# The shipped templates
# ─────────────────────────────────────────────────────────

def test_every_shipped_template_declares_a_parseable_bookend_set():
    import glob
    for path in sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            template = yaml.safe_load(f) or {}
        declared_bookends(template.get("content") or {})


def test_only_the_client_template_declares_a_card():
    """Q7: a general mechanism, nothing by default."""
    import glob
    declaring = set()
    for path in sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            template = yaml.safe_load(f) or {}
        if declared_bookends(template.get("content") or {}):
            declaring.add(os.path.basename(path)[:-5])
    assert declaring == {"lucie_client"}


def test_the_client_template_declares_the_kept_compositions():
    """The captain's premade cards, kept and usable (2026-08-17 scope)."""
    slots = {d["slot"]: d for d in
             declared_bookends(_template("lucie_client")["content"])}
    assert set(slots) == {"intro", "end_card"}
    assert slots["intro"]["composition"] == "LucieLogoAnimation"
    assert slots["end_card"]["composition"] == "LucieEndCard"
    # The compositions live with the client's project, not in the engine.
    for decl in slots.values():
        assert decl["source"].startswith("compositions/")
        assert not os.path.isabs(decl["source"])


def test_no_template_names_a_deleted_fusion_title_macro():
    """The red slate and the "Subscribe!" card are gone for good."""
    import glob
    for path in sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            content = (yaml.safe_load(f) or {}).get("content") or {}
        assert "intro_template" not in content
        assert "outro_template" not in content
    macros = os.path.join(PROJECT_ROOT, "library", "presets", "fusion-macros")
    assert glob.glob(os.path.join(macros, "*.setting")) == []


def test_the_out_of_band_importer_is_gone():
    """The end card enters through the manifest, not a side door."""
    assert not os.path.exists(os.path.join(
        PROJECT_ROOT, "library", "tools", "execution", "import_endcard.py"))


def test_insert_places_head_and_tail_around_the_spine():
    resolved = [resolve_bookend(d, "/p") for d in declared_bookends({
        "bookends": {
            "intro": {"asset": "i.mov", "duration_seconds": 3},
            "end_card": {"asset": "e.mov", "duration_seconds": 5},
        }})]
    blocks = insert_bookend_blocks([{"block_type": "speech"}], resolved)
    assert [b["block_type"] for b in blocks] == [
        "intro_card", "speech", "end_card"]
