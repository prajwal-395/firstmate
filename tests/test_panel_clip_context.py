"""The clip under the playhead, joined to what the pipeline measured.

The captain asked the panel "what does this clip where my playhead is at
show?" and was told the model could see a filename and could not watch
the video.  The model was not wrong: the panel had handed it a filename
and nothing else.

So the acceptance test is the one the captain wrote - standing on a clip
and asking what it shows gets a CONCRETE answer sourced from what the
pipeline already measured - and these hold the two halves of it:

* the JOIN reaches the vision document, the transcript of the seconds
  that PLAY, and the decision that put the clip there;
* the ORDERING puts what the captain is looking AT ahead of what they
  last clicked, which is the failure that made the model latch onto
  `0_01_validate_sfx_library` in the first place.
"""

from __future__ import annotations

from library.tools.panel import clip_context as cc


CATALOG = [
    {"clip_id": "clip_001", "filename": "IMG_1806.MOV",
     "path": "/p/raw/IMG_1806.MOV", "duration": 12.0},
    {"clip_id": "clip_011", "filename": "IMG_1816.MOV",
     "path": "/p/raw/IMG_1816.MOV", "duration": 189.0},
]

VISION = {"semantic_analysis_documents": [
    {"file_path": "/p/raw/IMG_1816.MOV",
     "scene": [{"start": 0.0, "end": 18.9, "location": "a parking lot",
                "setting": "outdoor", "lighting": "daylight"}],
     "camera": [{"start": 0.0, "end": 189.0, "framing": "close-up",
                 "stability": "shaky", "movement": "tilting_up"}],
     "objects": [{"label": "young man in a black cap"}],
     "assessment": {"content_type": "person_talking_to_camera",
                    "usable_ranges": [[0.0, 32.8]],
                    "usable_ranges_method": "deterministic_v1"}},
]}

TEMPORAL = {"temporal_event_indices": [
    {"clip_id": "clip_011", "speech_coverage": 0.61,
     "speech_coverage_method": "temporal_index",
     "speech_regions": [
         {"start": 0.84, "end": 3.23, "text": "i can feel the judgment"},
         {"start": 40.0, "end": 42.0, "text": "much later in the clip"},
     ]},
    {"clip_id": "clip_001", "speech_regions": []},
]}

MANIFEST = {"assembly_manifest": {"tracks": {"V1": {"clips": [
    {"source_file": "/p/raw/IMG_1816.MOV", "source_in": 0.836,
     "source_out": 3.234, "timeline_in": 0.0, "timeline_out": 2.398,
     "timeline_in_frame": 0, "timeline_out_frame": 72, "label": "hook_hook"},
]}}}}


def _state(**extra):
    outputs = {"catalog": {"clip_catalog": CATALOG},
               "semantic_analysis": VISION,
               "temporal_index": TEMPORAL,
               "compile_manifest": MANIFEST}
    outputs.update(extra)
    return {"project_folder": "/p", "step_outputs": outputs}


def _on_the_hook():
    return cc.ResolveContext(
        page="edit", project="Apple 001", timeline="Pipeline_Edit",
        timecode="00:00:01:00", fps=30.0,
        clip={"name": "IMG_1816.MOV", "start": 0, "end": 72, "duration": 72,
              "left_offset": 25, "file": "/p/raw/IMG_1816.MOV"})


# ── The join ─────────────────────────────────────────────────────────

def test_the_source_file_resolves_to_a_catalog_id():
    """Through the catalog, not a filename guess: the documents are keyed
    by FILE STEM and the catalog by clip_XXX (AGENTS.md 10.1)."""
    facts = cc.clip_facts(_state(), _on_the_hook())
    assert facts.clip_id == "clip_011"
    assert facts.resolved


def test_the_vision_document_is_read_as_observations_not_a_dump():
    facts = cc.clip_facts(_state(), _on_the_hook())
    assert "parking lot" in facts.observations["description"]
    assert facts.observations["framing"] == "close-up"
    assert facts.observations["content_type"] == "person_talking_to_camera"
    assert "0.0-32.8s" in facts.observations["usable_ranges"]


def test_only_the_seconds_this_placement_plays_are_transcribed():
    """Handing over a whole clip's transcript for a 2.4 s hook is the
    'attach more of the same' failure the steer names."""
    facts = cc.clip_facts(_state(), _on_the_hook())
    assert [r["text"] for r in facts.transcript] == [
        "i can feel the judgment"]
    assert facts.transcript_total == 2


def test_the_speech_coverage_travels_with_its_method():
    facts = cc.clip_facts(_state(), _on_the_hook())
    assert "0.61" in facts.speech_coverage
    assert "temporal_index" in facts.speech_coverage


def test_the_placement_names_the_step_that_chose_the_clip():
    """V1 A-roll is `speech_sequence`, not `assign_aroll`, and
    `timeline_decisions` is what knows that."""
    facts = cc.clip_facts(_state(), _on_the_hook())
    assert facts.placement["track"] == "V1"
    assert facts.placement["decided_by"] == "speech_sequence"
    assert facts.placement["basis"] == "chosen"
    # `output` and `summary` are DECLARED by the layout; `prompt` and
    # `answer` appear only when the file is really on disk, which this
    # fixture project has none of.
    assert facts.reasoning_routes["output"].endswith(
        "2_02_speech_sequence/output.json")


# ── An absence is stated, never filled in ────────────────────────────

def test_a_file_the_catalog_does_not_know_says_so():
    context = _on_the_hook()
    context.clip = dict(context.clip, file="/elsewhere/HOLIDAY.MOV")
    facts = cc.clip_facts(_state(), context)
    assert not facts.resolved
    assert facts.absences == [cc.NOT_IN_THE_CATALOG]


def test_an_empty_speech_region_list_is_not_a_measurement_of_silence():
    """AGENTS.md 10.3: `[]` reads the same whether nothing was said or
    WhisperX raised. Saying "no speech" would be the panel answering a
    question nobody measured."""
    context = _on_the_hook()
    context.clip = dict(context.clip, file="/p/raw/IMG_1806.MOV",
                        name="IMG_1806.MOV")
    facts = cc.clip_facts(_state(), context)
    assert facts.transcript == []
    assert any("not a measurement of" in a for a in facts.absences)
    assert any("speech_coverage_method" in a for a in facts.absences)


def test_a_placement_that_matches_nothing_is_unresolved_not_guessed():
    context = _on_the_hook()
    context.clip = dict(context.clip, left_offset=900, duration=30)
    facts = cc.clip_facts(_state(), context)
    assert facts.placement == {}
    assert any("unresolved" in a for a in facts.absences)


def test_no_catalog_at_all_says_which_step_has_not_run():
    facts = cc.clip_facts({"step_outputs": {}}, _on_the_hook())
    assert any("1.02" in a for a in facts.absences)


def test_a_missing_vision_document_is_named():
    state = _state()
    state["step_outputs"]["semantic_analysis"] = {}
    facts = cc.clip_facts(state, _on_the_hook())
    assert any("1.03" in a and "LOOKS like" in a for a in facts.absences)


# ── The ordering, which is where the model went wrong ────────────────

def test_what_the_editor_is_looking_at_comes_first():
    """The model latched onto the step the editor happened to have open
    in another tab, because that was the most concrete thing in the
    prompt. So the clip leads and the open step trails, labelled."""
    facts = cc.clip_facts(_state(), _on_the_hook())
    block = cc.prompt_block(_on_the_hook(), facts,
                            open_step="0_01_validate_sfx_library")
    assert block.startswith(cc.LOOKING_AT)
    assert block.index("IMG_1816.MOV") < block.index("validate_sfx_library")
    assert cc.LAST_CLICKED in block
    assert "NOT what they are asking about" in block


def test_the_block_carries_the_measurements_not_the_documents():
    facts = cc.clip_facts(_state(), _on_the_hook())
    block = cc.prompt_block(_on_the_hook(), facts)
    for expected in ("clip_011", "close-up", "person_talking_to_camera",
                     "i can feel the judgment", "speech_sequence"):
        assert expected in block, expected
    assert "semantic_analysis_documents" not in block, "no raw document"


def test_the_block_is_bounded_and_says_what_was_cut():
    facts = cc.clip_facts(_state(), _on_the_hook())
    block = cc.prompt_block(_on_the_hook(), facts, budget=400)
    assert len(block) < 700
    assert "were not sent" in block
    assert "was summarised" in block


def test_a_marker_contributes_both_typed_fields():
    """`name` and `note` are two pieces of typed text and reading only
    one loses everything typed into the other (AGENTS.md section 15)."""
    context = _on_the_hook()
    context.markers = [{"frame": 30, "color": "Blue", "name": "FRAMING",
                        "note": "cropped too tight on the left",
                        "custom": ""}]
    block = cc.prompt_block(context, cc.clip_facts(_state(), context))
    assert "FRAMING" in block
    assert "cropped too tight on the left" in block


def test_no_markers_is_said_rather_than_left_out():
    block = cc.prompt_block(_on_the_hook(),
                            cc.clip_facts(_state(), _on_the_hook()))
    assert "typed no markers" in block


def test_no_clip_under_the_playhead_is_said():
    context = cc.ResolveContext(page="edit", timecode="00:00:00:00")
    block = cc.prompt_block(context, cc.clip_facts(_state(), context))
    assert "no video clip under the playhead" in block


# ── The capture button's records travel too ──────────────────────────

def test_an_attachment_on_a_marker_is_surfaced():
    from library.tools import marker_payload

    envelope = marker_payload.merge_record(marker_payload.new_envelope(), {
        "kind": "still", "writer": "capture_frame", "writer_version": 1,
        "id": marker_payload.new_id("still"), "at": marker_payload.utc_now(),
        "path": "marker_feedback/stills/f000070.png"})
    context = _on_the_hook()
    context.markers = [{"frame": 70, "name": "GRADE", "note": "too green",
                        "color": "Red",
                        "custom": marker_payload.dumps(envelope)}]
    block = cc.prompt_block(context, cc.clip_facts(_state(), context))
    assert "capture_frame" in block
    assert "f000070.png" in block


def test_customdata_the_panel_cannot_read_is_ignored_not_crashed():
    context = _on_the_hook()
    context.markers = [{"frame": 1, "name": "n", "note": "x",
                        "custom": "not json at all"}]
    block = cc.prompt_block(context, cc.clip_facts(_state(), context))
    assert "frame 1" in block


# ── The picture under the playhead is not the topmost item ───────────

ITEMS = [
    {"track": 1, "name": "IMG_1816.MOV", "start": 0, "end": 72,
     "file": "/p/raw/IMG_1816.MOV"},
    {"track": 1, "name": "IMG_1822.MOV", "start": 72, "end": 200,
     "file": "/p/raw/IMG_1822.MOV"},
    {"track": 2, "name": "IMG_1806.MOV", "start": 90, "end": 150,
     "file": "/p/raw/IMG_1806.MOV"},
    {"track": 3, "name": "sub_block_2.mov", "start": 0, "end": 200,
     "file": "/p/pipeline_output/steps/4_05_render_subtitles/sub_block_2.mov"},
]


def test_the_picture_is_the_footage_not_the_subtitle_card():
    """`GetCurrentVideoItem()` answers with the HIGHEST video track, and
    on a finished build that is V3 - a subtitle card. Joining that to the
    footage catalog answers about the wrong clip entirely; on 001's real
    build it is what Resolve returned at the playhead."""
    assert cc.picture_at(ITEMS, 10)["name"] == "IMG_1816.MOV"
    assert cc.picture_at(ITEMS, 10)["track"] == 1


def test_a_cutaway_on_v2_is_the_picture_where_it_covers():
    assert cc.picture_at(ITEMS, 100)["name"] == "IMG_1806.MOV"
    assert cc.picture_at(ITEMS, 100)["track"] == 2
    assert cc.picture_at(ITEMS, 180)["name"] == "IMG_1822.MOV"


def test_what_is_drawn_over_the_picture_is_reported_separately():
    overlays = cc.overlays_at(ITEMS, 100)
    assert [o["name"] for o in overlays] == ["sub_block_2.mov"]
    assert cc.overlays_at(ITEMS, 900) == []


def test_an_overlay_is_told_apart_by_where_it_lives():
    """Everything the pipeline RENDERS lands under `pipeline_output/` and
    everything the captain SHOT is elsewhere - a fact about the layout,
    not a guess about a filename."""
    assert cc.is_overlay("/p/pipeline_output/steps/4_05/sub.mov")
    assert not cc.is_overlay("/p/raw/IMG_1806.MOV")
    assert not cc.is_overlay("")


def test_no_frame_and_no_items_answer_nothing_rather_than_guessing():
    assert cc.picture_at(ITEMS, None) is None
    assert cc.picture_at([], 10) is None
    assert cc.overlays_at(ITEMS, None) == []


def test_the_block_says_when_resolve_called_a_different_clip_current():
    context = _on_the_hook()
    context.topmost = {"name": "sub_block_2.mov", "track": 3}
    context.overlays = [{"name": "sub_block_2.mov", "track": 3}]
    block = cc.prompt_block(context, cc.clip_facts(_state(), context))
    assert "is an overlay the pipeline rendered" in block
    assert "Drawn over the picture" in block
