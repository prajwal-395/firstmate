"""Display drift: a hand edit announces itself before the rebuild.

For every class whose displays live in files, this test snapshots a
fixture project, makes the shallow edit by hand, and shows the
pre-run check flagging it with the owning class and the deep path -
then shows the untouched project checking silent. Resolve-side
displays (a moved timeline item, a hand-graded node) have no file
carrier the pipeline wrote, so no fingerprint can witness them:
those two classes refuse through `edit_depth` instead (proven in
`tests/unit/resolve/test_composer.py`), and the per-class reason is stated in
`display_drift`'s docstring.
"""
from __future__ import annotations
import json
import os
from library.tools import display_drift
import copy
from library.tools import transcript_corrections
from library.tools.display_respell import (
    apply_post_pass,
    respell_display,
)
import sys
from pathlib import Path
import pytest


# edit_class -> a display file the pipeline writes and a rebuild eats.
DRIFT_CASES = {
    "wording": "subtitle_plans/a_subtitles.json",
    "clip_timing": "pipeline_output/steps/5_04_compile_manifest/manifest.json",
    "overlay_position": "pipeline_output/steps/4_05_render_subtitles/box.json",
    "structure": "pipeline_output/review/reel_proposals_v2.json",
    "assets": "pipeline_output/steps/5_04_compile_manifest/manifest.json",
    "audio_levels": "pipeline_output/steps/5_02_audio_mix/mix.otio",
    "mg_content": "pipeline_output/steps/4_06_render_motion_graphics/mg.json",
}


def _write(root, relpath, document):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(document, (dict, list)):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
    else:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(document))
    return path


def test_hand_edit_flags_with_owner_and_deep_path(tmp_path, capsys):
    for edit_class in sorted(DRIFT_CASES):
        root = str(tmp_path / edit_class)
        relpath = DRIFT_CASES[edit_class]
        _write(root, relpath, {"value": "pipeline wrote this"})
        report = display_drift.snapshot(root)
        assert report["files"] >= 1
        # Silence while nothing moved.
        quiet = display_drift.check(root)
        assert quiet["drifted"] == [] and quiet["vanished"] == []
        assert capsys.readouterr().err == ""
        # The shallow edit: a hand on the display file.
        _write(root, relpath, {"value": "hand changed this"})
        flagged = display_drift.check(root)
        assert flagged["drifted"] == [relpath], edit_class
        err = capsys.readouterr().err
        assert "DISPLAY DRIFT" in err
        assert relpath in err and edit_class in err
        assert "deep path" in err


def test_vanished_file_is_named(tmp_path, capsys):
    root = str(tmp_path)
    _write(root, "subtitle_plans/a_subtitles.json", {"cards": []})
    display_drift.snapshot(root)
    os.remove(os.path.join(root, "subtitle_plans/a_subtitles.json"))
    report = display_drift.check(root)
    assert report["vanished"] == ["subtitle_plans/a_subtitles.json"]
    assert "gone since snapshot" in capsys.readouterr().err


# --------------------------------------------------------------------------
# From test_display_respell.py
#
# The wording correction reaches regenerated displays by mechanism.
#
# The source transcript is clean; the prompt half does not move copies
# made before the correction (the 192 lucy->Lucie divergences: judge
# quotes, selected moments, proposals, conformance verdicts). Each test
# below regenerates a display from a STALE copy with an ACTIVE
# correction on file and shows the post-pass carrying the corrected
# spelling - or, where the regen point cannot run here, shows the unit
# the regen point calls. Nothing here hand-edits a display: the
# correction lives in `learned_context` and every fix below is derived
# from it. Fixtures under `tmp_path` (AGENTS.md 8).

def _project_with_correction(root, heard="lucy", correct="Lucie"):
    return transcript_corrections.record_spelling(
        root, heard, correct, "vetting the respell post-pass")


def test_respell_fixes_copy_and_keeps_identity():
    corrections = [{"id": "lc-1", "heard": "lucy", "correct": "Lucie"}]
    obj = {
        "reason": "visit lucy today",
        "cta": {"text": "say lucy now", "speaker": "Akshita"},
        "slug": "lucy-thing",
        "label": "mg_lucy",
        "timeline_name": "Reel 1 - lucy",
        "anchor_phrase": "say lucy",
        "source_file": "/x/lucy_takes/a.mov",
        "id": "sub_lucy_001",
    }
    report = respell_display(obj, corrections)
    assert report["replacements"] == 2
    assert obj["reason"] == "visit Lucie today"
    assert obj["cta"]["text"] == "say Lucie now"
    # Identity and reference never move.
    assert obj["slug"] == "lucy-thing"
    assert obj["label"] == "mg_lucy"
    assert obj["timeline_name"] == "Reel 1 - lucy"
    assert obj["anchor_phrase"] == "say lucy"
    assert obj["source_file"] == "/x/lucy_takes/a.mov"
    assert obj["id"] == "sub_lucy_001"
    assert obj["cta"]["speaker"] == "Akshita"


def _words(*tokens, start=10.0):
    words = []
    cursor = start
    for token in tokens:
        words.append({"word": token, "start": cursor,
                      "end": round(cursor + 0.4, 3), "timed": True})
        cursor = round(cursor + 0.5, 3)
    return words


def _two_speaker_transcript(stale_word="lucy"):
    return {
        "derived_from": {"duration_seconds": 60.0},
        "segments": [
            {"text": f"say {stale_word} to the camera hello friend",
             "timeline_start": 10.0, "timeline_end": 16.0,
             "speaker": "A", "resolve_item_id": "x1",
             "words": _words("say", stale_word, "to", "the", "camera",
                             "hello", "friend")},
            {"text": "yes indeed my friend absolutely correct words",
             "timeline_start": 16.0, "timeline_end": 22.0,
             "speaker": "B", "resolve_item_id": "x2",
             "words": _words("yes", "indeed", "my", "friend",
                             "absolutely", "correct", "words",
                             start=16.0)},
        ],
    }


def test_select_reels_regen_carries_correction(tmp_path):
    from library.steps.step_3_04_select_reels.post_bridge import resolve

    root = str(tmp_path)
    _project_with_correction(root)
    transcript = _two_speaker_transcript(stale_word="lucy")
    llm = {"moments": [{
        "start": 10.0, "end": 22.0, "slug": "t",
        "reason": "visit lucy today"}]}
    out = resolve(llm, {"timeline_transcript": transcript,
                        "project_folder": root})
    moments = out["reel_selection"]["moments"]
    assert len(moments) == 1
    assert "lucy" not in json.dumps(moments).replace("Lucie", "")
    assert moments[0]["slug"] == "t"
    assert "Lucie" in moments[0]["transcript_preview"]


def test_judge_bridge_lines_carry_correction(tmp_path):
    from library.steps.step_3_05_judge_reels.bridge import build_context
    from library.tools.reel_proposal import ReelMoment

    root = str(tmp_path)
    _project_with_correction(root)
    transcript = _two_speaker_transcript(stale_word="lucy")
    moment = ReelMoment.from_dict({
        "number": 1, "slug": "t", "reason": "r",
        "timeline_start": 10.0, "timeline_end": 22.0,
        "approval": "proposed"})
    from library.tools.reel_proposal import enrich
    moment = enrich(moment, transcript)
    out = build_context({
        "timeline_transcript": transcript,
        "reel_selection": {"moments": [moment.as_dict()]},
        "project_folder": root})
    says = [line["says"] for row in out["reels_to_read"]
            for line in row["lines"]]
    assert says and all("Lucie" in line or "lucy" not in line
                        for line in says)


def test_judge_post_bridge_corrects_quote_rather_than_refusing(tmp_path):
    from library.steps.step_3_05_judge_reels.post_bridge import resolve

    root = str(tmp_path)
    _project_with_correction(root)
    # Clean transcript (post-correction), reader quoting the OLD
    # spelling: without the post-pass the quote check refuses the
    # reading; with it the quote is corrected and grounded.
    transcript = _two_speaker_transcript(stale_word="Lucie")
    moment_dict = {
        "number": 1, "slug": "t", "reason": "r",
        "timeline_start": 10.0, "timeline_end": 22.0,
        "approval": "proposed",
        "transcript_preview": transcript["segments"][0]["text"],
        "speakers": ["A", "B"]}
    llm = {"readings": [{
        "reel": 1, "rank": 1,
        "claim": "the closer names lucy",
        "claim_quote": "say lucy to the camera",
        "opening_quote": "say lucy to the camera",
        "closing_quote": "absolutely correct words"}]}
    data = {"timeline_transcript": transcript,
            "reel_selection": {"moments": [moment_dict]},
            "reels_to_read": [{"reel": 1, "runs_for_seconds": 12.0}],
            "project_folder": root}
    out = resolve(llm, data)
    assert out["reel_judgement"]["refused"] == []
    readings = out["reel_judgement"]["readings"]
    assert len(readings) == 1
    assert "lucy" not in json.dumps(readings).replace("Lucie", "")


def test_judge_post_bridge_holds_grounding_on_stale_transcript(tmp_path):
    from library.steps.step_3_05_judge_reels.post_bridge import resolve

    root = str(tmp_path)
    _project_with_correction(root)
    # Stale transcript, reader who read the correction and wrote it
    # right: without the post-pass the corrected quote is UNGROUNDED
    # against stale words and refused; with it both sides respell and
    # the reading stands.
    transcript = _two_speaker_transcript(stale_word="lucy")
    moment_dict = {
        "number": 1, "slug": "t", "reason": "r",
        "timeline_start": 10.0, "timeline_end": 22.0,
        "approval": "proposed",
        "transcript_preview": transcript["segments"][0]["text"],
        "speakers": ["A", "B"]}
    llm = {"readings": [{
        "reel": 1, "rank": 1,
        "claim": "the closer names Lucie",
        "claim_quote": "say Lucie to the camera",
        "opening_quote": "say Lucie to the camera",
        "closing_quote": "absolutely correct words"}]}
    data = {"timeline_transcript": transcript,
            "reel_selection": {"moments": [moment_dict]},
            "reels_to_read": [{"reel": 1, "runs_for_seconds": 12.0}],
            "project_folder": root}
    out = resolve(llm, data)
    assert out["reel_judgement"]["refused"] == []
    assert len(out["reel_judgement"]["readings"]) == 1


# --------------------------------------------------------------------------
# From test_display_suppression.py
#
# A pronounced token the reader never sees: mark, don't strike.
#
# `library/tools/transcript_corrections.py` (Gap 1 of the 2026-09-19
# field-test brief): a display suppression removes the token from
# caption/subtitle text and quoted copy while the audio, the spine and
# every timing stand untouched. These tests prove the three halves -
# the transcript pass, the timed-word pass the caption planner reads,
# and survival across a re-transcription - plus the guardrails (an
# anchored "i" never touches the pronoun "I"; a caption's provenance
# identity never re-keys).

sys.path.insert(0, ".")

from library.tools import transcript_corrections as tc


def _project(tmp_path):
    import json
    import os
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "learned_context"), exist_ok=True)
    with open(os.path.join(root, "learned_context", "learnings.json"),
              "w", encoding="utf-8") as handle:
        json.dump([], handle)
    return root


def _doc():
    return {"segments": [
        {"speaker": "Akshita", "text": "best CRMs, um s best",
         "source_clip_id": "LC4932.MXF",
         "source_start": 162.0, "source_end": 174.0,
         "words": [
             {"word": "best", "start": 162.0, "end": 162.3},
             {"word": "CRMs,", "start": 162.3, "end": 162.7},
             {"word": "um", "start": 162.7, "end": 162.9},
             {"word": "s", "start": 162.9, "end": 163.1},
             {"word": "best", "start": 163.1, "end": 163.4}]},
        {"speaker": "Craig", "text": "I think I know",
         "source_clip_id": "LCATL0012.MXF",
         "source_start": 10.0, "source_end": 12.0,
         "words": [
             {"word": "I", "start": 10.0, "end": 10.2},
             {"word": "think", "start": 10.2, "end": 10.6},
             {"word": "I", "start": 10.6, "end": 10.7},
             {"word": "know", "start": 10.7, "end": 11.0}]},
    ]}


def test_suppression_hides_token_but_keeps_audio_and_timings(tmp_path):
    project = _project(tmp_path)
    tc.record_display_suppression(project, "um", "reel 24 ums")
    tc.record_display_suppression(
        project, "s", "reel 17 stray s",
        scope={"speaker": "Akshita", "surface": "s",
               "prev": "um", "next": "best"})
    doc = _doc()
    before = copy.deepcopy(doc)
    report = tc.apply_to_document(doc, project)
    assert report["suppressed"] == 2  # "um" and the anchored "s"
    seg = doc["segments"][0]
    assert seg["text"] == "best CRMs, best"
    marked = [w for w in seg["words"] if w.get("display") is False]
    assert [w["word"] for w in marked] == ["um", "s"]
    # Every timing byte-identical, neighbours included.
    for after_seg, before_seg in zip(doc["segments"], before["segments"]):
        assert after_seg["source_start"] == before_seg["source_start"]
        assert after_seg["source_end"] == before_seg["source_end"]
        for after_w, before_w in zip(after_seg["words"],
                                     before_seg["words"]):
            assert after_w["start"] == before_w["start"]
            assert after_w["end"] == before_w["end"]
    # The audio span is untouched: nothing retimed, nothing struck.
    assert seg["source_start"] == 162.0 and seg["source_end"] == 174.0


def test_anchored_stray_never_touches_the_pronoun(tmp_path):
    project = _project(tmp_path)
    tc.record_display_suppression(
        project, "i", "reel 18 stray i",
        scope={"speaker": "Craig", "surface": "i",
               "prev": "uh", "next": "kind"})
    doc = _doc()
    tc.apply_to_document(doc, project)
    # Both pronouns stand: neither is a lowercase "i" after "uh".
    pronouns = [w for w in doc["segments"][1]["words"]
                if w["word"] == "I"]
    assert len(pronouns) == 2
    assert all(w.get("display") is not False for w in pronouns)
    assert doc["segments"][1]["text"] == "I think I know"


def test_cased_anchor_hits_lowercase_spine_words():
    # Step 1.04 lowercases every temporal word, so the caption
    # planner meets "probably" where the transcript said "Probably".
    # The anchor (speaker + neighbours) is the guard, never the
    # casing: one anchor fixes both, or the transcript and the
    # caption silently disagree (Reel 12, 2026-09-19).
    words = [
        {"word": "which", "start": 1.0, "end": 1.2},
        {"word": "probably", "start": 1.2, "end": 1.9},
        {"word": "probably", "start": 1.9, "end": 2.1},
        {"word": "most", "start": 2.1, "end": 2.4},
    ]
    anchor = {"id": "x", "heard": "Probably",
              "scope": {"speaker": "Akshita", "surface": "Probably",
                        "prev": "probably", "next": "most"}}
    kept, dropped = tc.filter_words(words, "Akshita", [anchor])
    assert [w["word"] for w in kept] == ["which", "probably", "most"]
    assert [w["word"] for w in dropped] == ["probably"]


def test_filter_words_partitions_without_retiming():
    words = [
        {"word": "same", "start": 1.0, "end": 1.3},
        {"word": "f", "start": 1.3, "end": 1.5},
        {"word": "um,", "start": 1.5, "end": 1.8},
        {"word": "same", "start": 1.8, "end": 2.0},
    ]
    suppressions = [
        {"id": "x", "heard": "um", "scope": None},
        {"id": "y", "heard": "f",
         "scope": {"speaker": "Akshita", "surface": "f",
                   "prev": "same", "next": "um"}},
    ]
    kept, dropped = tc.filter_words(words, "Akshita", suppressions)
    assert [w["word"] for w in kept] == ["same", "same"]
    assert [w["word"] for w in dropped] == ["f", "um,"]
    # Partitioned, never edited: the survivors keep their timings.
    assert kept[0]["start"] == 1.0 and kept[1]["start"] == 1.8
    # Another speaker's identical words stand.
    kept2, dropped2 = tc.filter_words(words, "Craig", suppressions)
    assert [w["word"] for w in kept2] == ["same", "f", "same"]
    assert [w["word"] for w in dropped2] == ["um,"]


def test_edge_punctuation_survives_a_word_level_respell():
    # Reel 16, 2026-09-19: respelling "chronicle," to "Chronicle"
    # must not eat the comma the caption proving the fix carries.
    words = [
        {"word": "the", "start": 1.0, "end": 1.1},
        {"word": "atlanta", "start": 1.1, "end": 1.4},
        {"word": "business", "start": 1.4, "end": 1.7},
        {"word": "chronicle,", "start": 1.7, "end": 2.1},
        {"word": "as", "start": 2.1, "end": 2.2},
    ]
    out, made = tc.apply_spelling_to_words(
        words, [{"id": "a", "heard": "atlanta business chronicle",
                 "correct": "Atlanta Business Chronicle"}])
    assert [w["word"] for w in out] == [
        "the", "Atlanta Business Chronicle,", "as"]
    # The merged entry spans the run it replaced - timings stand.
    assert (out[1]["start"], out[1]["end"]) == (1.1, 2.1)
    assert made == {"a": 1}


# --------------------------------------------------------------------------
# From test_do_not_draw.py
#
# A deleted graphic stays deleted through a rebuild.
#
# 2026-09-13, wipe and rebuild: the captain had deleted the planned
# graphic `mg_geo-podcast_a072b160.mov` from Reel 01's timeline by hand
# and the fresh build placed it again - nothing recorded the act at
# all. `external/do_not_draw.json` (`library/tools/do_not_draw.py`) is
# the store, enforced at placement: the plan keeps the record of what
# was intended, the timeline does not play it, and a rebuild that
# re-renders under a new content hash holds the same deletion without
# being told again.
#
# Fail-before: `library.tools.do_not_draw` has no `load_rules` and
# `place_overlay_segments` draws every segment it is given - every test
# here errors on the attribute, not on an assertion.

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.tools import do_not_draw


REEL = "Reel 01 - the-cta"
LABEL = "vox_reel_01_the_cta_00"
SEGMENT_ID = "mg_geo-podcast_a072b160"
ELEMENTS = ["stat_callout"]
REASON = "captain 2026-09-13: deleted by hand in Resolve"


def _rule(**over):
    rule = {"reel": REEL, "placement_label": LABEL,
            "segment_id": SEGMENT_ID, "elements": list(ELEMENTS),
            "reason": REASON}
    rule.update(over)
    return rule


def _segment(label=LABEL, segment_id=SEGMENT_ID, elements=ELEMENTS,
             path=None):
    return {
        "placement_label": label,
        "segment_id": segment_id,
        "elements": list(elements),
        "overlay_path": path or f"/renders/{segment_id}.mov",
        "timeline_start": 9.092,
        "timeline_end": 11.595,
        "total_frames": 60,
        "lane": 0,
    }


def _project_2(tmp_path):
    project = tmp_path / "project"
    (project / "external").mkdir(parents=True)
    return project


def _write_rules(project, body):
    (project / "external" / "do_not_draw.json").write_text(
        json.dumps(body), encoding="utf-8")


# ── 1. The declaration validates, loudly ─────────────────────────────


def test_a_malformed_file_refuses_rather_than_building_past(tmp_path):
    project = _project_2(tmp_path)
    _write_rules(project, {"version": 1,
                           "suppressions": [{"reel": REEL}]})
    with pytest.raises(do_not_draw.DoNotDrawError):
        do_not_draw.load_rules(str(project))


# ── 2. Matching: the label survives the re-render ────────────────────

def test_a_label_or_segment_id_hit_suppresses():
    held, why = do_not_draw.should_suppress([_rule()], REEL, _segment())
    assert held and "stays undrawn" in why
    # A segment id hit suppresses without a label.
    held, _ = do_not_draw.should_suppress(
        [_rule(placement_label=None)], REEL,
        _segment(label=None))
    assert held


def test_a_shifted_plan_refuses_the_suppression_and_says_so():
    """The input that would delete the WRONG graphic: the plan
    re-ordered under the label, so it now wears another element.
    Suppression refuses - the graphic plays until re-transcribed -
    and the reason names both sides."""
    held, why = do_not_draw.should_suppress(
        [_rule()], REEL, _segment(elements=["quote_card"]))
    assert not held
    assert "NOT suppressed" in why
    assert "stat_callout" in why and "quote_card" in why


# ── 3. Unmatched: a rule the plan left behind says so ────────────────


# ── 4. Survival: the deletion holds across a re-render ───────────────

class _PlacedItem:
    def __init__(self):
        self.props = {}

    def GetStart(self):
        return 218

    def SetProperty(self, prop, value):
        self.props[prop] = value
        return True

    def GetProperty(self, prop):
        return self.props.get(prop)


class _Timeline:
    def __init__(self):
        self.items = [_PlacedItem()]

    def GetUniqueId(self):
        return "timeline-1"

    def GetName(self):
        return "fake reel"

    def GetItemListInTrack(self, kind, index):
        return list(self.items)


class _Folder:
    def __init__(self, name="Master"):
        self._name = name
        self.subs = []

    def GetName(self):
        return self._name

    def GetClipList(self):
        return []

    def GetSubFolderList(self):
        return list(self.subs)


class _Pool:
    """Records what the placer asked Resolve to do."""

    def __init__(self):
        self.appended = []
        self._root = _Folder()
        self._current = self._root

    def GetRootFolder(self):
        return self._root

    def GetCurrentFolder(self):
        return self._current

    def SetCurrentFolder(self, folder):
        self._current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = _Folder(name)
        parent.subs.append(folder)
        self._current = folder
        return folder

    def ImportMedia(self, paths):
        return [object()]

    def AppendToTimeline(self, clips):
        self.appended.extend(clips)
        return [{"placed": True}]


class _Project:
    def __init__(self, timeline):
        self._timeline = timeline

    def SetCurrentTimeline(self, timeline):
        pass

    def GetCurrentTimeline(self):
        return self._timeline

    def GetMediaPool(self):
        return _Pool()


def _build_once(rules, segments):
    """One placement pass through the real funnel - what a rebuild
    does with the segments it rendered, minus Resolve itself."""
    from library.tools.reel_build import place_overlay_segments

    pool = _Pool()
    timeline = _Timeline()
    held = place_overlay_segments(
        pool, _Project(timeline), timeline, REEL, 24000 / 1001,
        segments, 6, kind="explainer", check="F21",
        project_folder="", do_not_draw=rules)
    return pool, held


def test_a_deleted_graphic_stays_deleted_through_a_rerender_only_if_declared():
    """The rebuild equivalent: build 1 places the plan minus the
    deletion; build 2 re-renders (new content hash, same label) and
    the deletion holds again - by value on what Resolve was asked
    to append, not asserted."""
    rules = do_not_draw.validate_rules([_rule()])
    control = _segment(label="vox_reel_01_the_cta_01",
                       segment_id="mg_geo-podcast_11111111",
                       elements=["quote_card"],
                       path="/renders/mg_geo-podcast_11111111.mov")
    pool1, held1 = _build_once(rules, [_segment(), control])
    assert len(pool1.appended) == 1
    assert held1 == [SEGMENT_ID]

    rerendered = _segment(segment_id="mg_geo-podcast_bbbb2222",
                          path="/renders/mg_geo-podcast_bbbb2222.mov")
    pool2, held2 = _build_once(rules, [rerendered, control])
    assert len(pool2.appended) == 1
    assert held2 == ["mg_geo-podcast_bbbb2222"]

    # The failing input this guards: the same two builds with no
    # declaration place the graphic both times - what the 2026-09-13
    # rebuild did.
    pool1, held1 = _build_once(None, [_segment(), control])
    pool2, held2 = _build_once(
        [], [_segment(segment_id="mg_geo-podcast_bbbb2222",
                       path="/renders/mg_geo-podcast_bbbb2222.mov"),
             control])
    assert len(pool1.appended) == 2 and held1 == []
    assert len(pool2.appended) == 2 and held2 == []


def test_a_full_round_trip_through_the_file(tmp_path):
    """Write the file the captain writes, read it the way the build
    reads it, suppress through the funnel."""
    project = _project_2(tmp_path)
    _write_rules(project, {"version": 1, "suppressions": [_rule()]})
    rules = do_not_draw.load_rules(str(project))
    pool, held = _build_once(rules, [_segment()])
    assert pool.appended == [] and held == [SEGMENT_ID]
