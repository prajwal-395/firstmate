"""The captain's edits survive a rebuild - as deltas, not replacements.

The captain (2026-09-09): *"if i ask to remove a piece of the video and
replace it with something else, and then ask you to rebuild the timeline,
those changes should persist"*. And: *"going into the subtitles and making
corrections that i ask of you so it's there"*.

Two gaps closed here, on top of `library/tools/external_inputs.py`
(state the pipeline did not produce, CHECKED never asserted):

1. Captions had NO external route. `captain_edits` is a supportable key
   whose check verifies each edit still corresponds to speech it names,
   so a supplied caption fix cannot silently drift from the audio.
2. Supply was whole-value. An edit is a small readable DELTA anchored
   to the spoken words - the one thing that survives a rebuild, where
   frame numbers (PR 847), source timecodes and pipeline ordinals do
   not. An edit that can no longer apply is reported STALE, loudly,
   never dropped silently.

Sibling lane `transcript_corrections` (in flight) owns CORRECTIONS - a
fact about the world, everywhere and forever ("Lucie not Lucy"). This
module owns EDITS - a decision about this one piece. Both are needed;
neither subsumes the other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import captain_edits, external_inputs
from library.tools.external_inputs import ExternalStateError
from library.tools.project_layout import ProjectLayout


# ── Helpers ──────────────────────────────────────────────────────────

def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _write_edits_file(project, edits, source="captain, 2026-09-09"):
    directory = project / "external"
    directory.mkdir(exist_ok=True)
    path = directory / "captain_edits.json"
    path.write_text(
        json.dumps({"key": "captain_edits", "source": source,
                    "value": edits}),
        encoding="utf-8")
    return path


def _fix(anchor="so what do they", replacement="so what do they keep",
         reason="captain: keep the setup, lose the stumble"):
    return {"kind": "caption_fix", "anchor_phrase": anchor,
            "replacement": replacement, "reason": reason}


def _drop(anchor="so what do they",
          reason="captain: drop the fragment at 22 seconds"):
    return {"kind": "drop_fragment", "anchor_phrase": anchor,
            "reason": reason}


def _words(text, start=10.0, step=0.4):
    out = []
    cursor = start
    for token in text.split():
        out.append({"word": token, "source_start": round(cursor, 3),
                    "source_end": round(cursor + step - 0.05, 3)})
        cursor += step
    return out


def _block(position, text, tl_start, src_id="clip_001"):
    words = _words(text)
    return {
        "position": position, "block_type": "speech",
        "content": {"text": text, "passage_ref": position},
        "clip_id": src_id, "source_clip_id": src_id,
        "source_start": words[0]["source_start"],
        "source_end": words[-1]["source_end"],
        "word_timestamps": [
            {"word": w["word"], "source_start": w["source_start"],
             "source_end": w["source_end"]} for w in words],
        "alignment_method": "test",
        "duration_seconds": round(words[-1]["source_end"]
                                  - words[0]["source_start"], 3),
        "timeline_start": tl_start,
        "timeline_end": round(tl_start + words[-1]["source_end"]
                              - words[0]["source_start"], 3),
    }


def _spine():
    blocks = [
        _block(1, "welcome back to the show", 0.0),
        _block(2, "so what do they want from us", 4.0),
        _block(3, "lucy built the whole thing herself", 9.0),
    ]
    return {"total_estimated_duration_seconds": 14.0,
            "frame_rate": 30.0, "structure": blocks}


def _entries():
    return [
        {"id": "sub_1_001", "timeline_start": 0.0, "timeline_end": 1.5,
         "text": "welcome back to the show", "word_count": 5,
         "emphasis_words": [], "spine_block_position": 1,
         "words": []},
        {"id": "sub_3_001", "timeline_start": 9.0, "timeline_end": 11.0,
         "text": "lucy built the whole thing herself",
         "word_count": 6, "emphasis_words": ["built"],
         "spine_block_position": 3, "words": []},
    ]


# ── 1. The delta validates ───────────────────────────────────────────

def test_a_caption_fix_and_a_drop_validate():
    edits = captain_edits.validate_edits([_fix(), _drop()])
    assert len(edits) == 2


def test_an_unknown_kind_is_refused():
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits(
            [{"kind": "color_grade", "anchor_phrase": "hello",
              "reason": "taste"}])
    assert "kind" in str(exc.value)


def test_an_empty_anchor_is_refused():
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits([_drop(anchor="  ")])


def test_a_caption_fix_without_a_replacement_is_refused():
    edit = _fix()
    del edit["replacement"]
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits([edit])
    assert "replacement" in str(exc.value)


def test_a_frame_anchored_edit_is_refused():
    """Frame numbers are not stable across a rebuild (PR 847 refused
    delete-by-moved-frame for the same reason), so an edit anchored to
    one is refused at write time rather than lost at rebuild time."""
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits(
            [{**_drop(), "anchor_frame": 528}])
    assert "frame" in str(exc.value).lower()


def test_an_edit_without_a_reason_is_refused():
    edit = _drop()
    del edit["reason"]
    with pytest.raises(captain_edits.CaptainEditError):
        captain_edits.validate_edits([edit])


# ── 2. The external key checks drift ─────────────────────────────────

def test_captain_edits_is_a_supportable_external_key():
    assert "captain_edits" in external_inputs.CHECKS


def test_supplied_edits_verify_and_name_what_was_checked(tmp_path):
    project = _project(tmp_path)
    _write_edits_file(project, [_fix(), _drop()])
    supplied = external_inputs.load(str(project))
    assert set(supplied) == {"captain_edits"}
    assert "2 edit(s)" in supplied["captain_edits"].checked


def test_a_caption_fix_drifted_from_its_speech_is_refused(tmp_path):
    """The check that captions have nowhere else: the anchor's words
    must still be in the speech they caption, where the pipeline has
    already measured it. A fix whose anchor is gone is drift, refused
    by name rather than applied to the wrong seconds."""
    project = _project(tmp_path)
    _write_edits_file(project, [_fix(anchor="zebras on mars")])
    state = {"step_outputs": {
        "mesh_spine": {"audio_spine": _spine()},
        "speech_sequence": {"body_sequence": [
            {"text": b["content"]["text"]} for b in _spine()["structure"]]}}}
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project), state)
    assert "zebras on mars" in str(exc.value)


def test_unverifiable_anchors_pass_structurally(tmp_path):
    """The check must reach a verdict without state: on a first run,
    before any spine exists, a well-formed edit verifies and the drift
    question moves to apply time, where it reports STALE loudly."""
    project = _project(tmp_path)
    _write_edits_file(project, [_fix(anchor="zebras on mars")])
    supplied = external_inputs.load(str(project), {})
    assert set(supplied) == {"captain_edits"}


# ── 3. Caption fixes apply, word-anchored ────────────────────────────

def test_a_caption_fix_rewrites_text_and_rederives_the_rest():
    entries = _entries()
    fixed, applied, stale = captain_edits.apply_caption_fixes(
        entries, [_fix(anchor="lucy built",
                       replacement="Lucie built")])
    assert stale == []
    assert len(applied) == 1
    assert "Lucie built" in fixed[1]["text"]
    assert "lucy" not in fixed[1]["text"].lower().replace("lucie", "")
    # Timing untouched: a text change must never move the picture/sound.
    assert fixed[1]["timeline_start"] == 9.0
    assert fixed[1]["timeline_end"] == 11.0
    # What depends on the text is re-derived, not left stale.
    assert fixed[1]["word_count"] == len(fixed[1]["text"].split())
    # Untouched entries pass through byte-identical.
    assert fixed[0]["text"] == "welcome back to the show"


def test_a_caption_fix_that_matches_nothing_reports_stale():
    entries = _entries()
    _, applied, stale = captain_edits.apply_caption_fixes(
        entries, [_fix(anchor="zebras on mars",
                       replacement="zebras")])
    assert applied == []
    assert len(stale) == 1
    assert "zebras on mars" in stale[0]["reason"]
    assert entries[1]["text"].startswith("lucy")


# ── 4. Drops remove speech and move everything after ─────────────────

def test_a_drop_removes_the_block_and_rederives_later_timings():
    spine = _spine()
    before = [b["timeline_start"] for b in spine["structure"]]
    blocks, applied, stale = captain_edits.apply_drop_fragments(
        spine["structure"], [_drop()])
    assert stale == []
    assert len(applied) == 1
    assert [b["position"] for b in blocks] == [1, 3]
    # Everything after the cut moved earlier by the removed duration -
    # picture and sound move TOGETHER, or a caption pinned to an old
    # time becomes a lie.
    removed = sum(float(b.get("duration_seconds", 0))
                  for b in spine["structure"] if b["position"] == 2)
    kept = spine["structure"][0]["duration_seconds"]
    assert blocks[1]["timeline_start"] == pytest.approx(kept, abs=0.01)
    assert blocks[1]["timeline_start"] < before[2] - removed + 0.01


def test_a_drop_that_matches_nothing_reports_stale_loudly():
    spine = _spine()
    blocks, applied, stale = captain_edits.apply_drop_fragments(
        spine["structure"], [_drop(anchor="zebras on mars")])
    assert applied == []
    assert len(blocks) == 3
    assert len(stale) == 1
    assert "no longer applies" in stale[0]["reason"]
    assert "zebras on mars" in stale[0]["reason"]


# ── 5. The captain can read what is in force ─────────────────────────

def test_edits_list_in_plain_language(capsys):
    captain_edits.describe_edits([_fix(), _drop()])
    out = capsys.readouterr().out
    assert "Lucie" not in out  # the fix text is quoted, not paraphrased
    assert "so what do they" in out
    assert "caption" in out.lower()
    assert "drop" in out.lower() or "remove" in out.lower()


def test_stale_edits_are_announced_not_silenced(capsys):
    captain_edits.report_stale(
        [{"anchor_phrase": "zebras on mars",
          "reason": "STALE: no longer applies"}])
    out = capsys.readouterr().err
    assert "STALE" in out
    assert "zebras on mars" in out


# ── 6. The captain's exact test: edit, rebuild, still there ──────────

def test_a_caption_fix_and_a_drop_survive_a_rebuild(tmp_path):
    """Make an edit, rebuild, show the edit is still there - both
    kinds, through the REAL step functions, twice."""
    from library.steps.step_4_01_plan_subtitles.step import (
        generate_subtitles)

    project = _project(tmp_path)
    edits = [_fix(anchor="lucy built",
                  replacement="Lucie built",
                  reason="captain: her name is Lucie"),
             _drop(anchor="so what do they",
                   reason="captain: drop the fragment at 22 seconds")]
    _write_edits_file(project, edits)

    def rebuild(spine):
        spine = json.loads(json.dumps(spine))  # a rebuild re-reads
        blocks, _, _ = captain_edits.apply_drop_fragments(
            spine["structure"],
            captain_edits.load_edits(str(project)))
        spine["structure"] = blocks
        plan = generate_subtitles(
            spine, caption_case="as_written",
            project_folder=str(project))["subtitle_plan"]
        plan["subtitle_entries"], _, _ = captain_edits.apply_caption_fixes(
            plan["subtitle_entries"],
            captain_edits.load_edits(str(project)))
        return spine, plan

    spine_a, plan_a = rebuild(_spine())
    spine_b, plan_b = rebuild(_spine())  # the rebuild after the rebuild

    assert [b["position"] for b in spine_a["structure"]] == [1, 3]
    assert [b["position"] for b in spine_b["structure"]] == [1, 3]
    texts = [e["text"] for e in plan_b["subtitle_entries"]]
    assert any("Lucie built" in t for t in texts)
    assert not any("so what do they" in t for t in texts)
    # The rebuilt timeline carries both: no dropped speech, corrected
    # captions, later blocks shifted onto the cut.
    assert spine_b["structure"][1]["timeline_start"] < 9.0
    assert plan_b["total_subtitles"] == plan_a["total_subtitles"]
