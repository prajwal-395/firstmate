"""Corrections that survive re-transcription and every regeneration.

The captain's question: "where does a correction live so that a
re-render carries it?" Editing a caption's props is undone by the next
render; editing the transcript is undone by the next transcription.
Neither is the root. A correction lives in the project's
`learned_context/` (pipeline-owned, never scratch) as a `correction`
kind with a machine-readable `source`, and is applied deterministically
at the transcript root - so every downstream consumer (captions,
explainer stages, motion-graphics anchors, the model-read context)
reads the corrected words with no changes of its own.

Fail-before: this module does not exist yet, and `transcribe_audio`
takes no bias arguments.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _doc_with_lucy():
    return {
        "segments": [
            {
                "speaker": "Craig",
                "text": "we're calling the lucy visibility system",
                "timeline_start": 10.0,
                "timeline_end": 13.0,
                "source_file": "LCATL0013.MXF",
                "source_start": 100.0,
                "source_end": 103.0,
                "resolve_item_id": "clip-1",
                "words": [
                    {"word": "we're", "start": 10.0, "end": 10.3,
                     "timed": True},
                    {"word": " calling", "start": 10.3, "end": 10.7,
                     "timed": True},
                    {"word": " the", "start": 10.7, "end": 10.9,
                     "timed": True},
                    {"word": " lucy", "start": 10.9, "end": 11.3,
                     "timed": True},
                    {"word": " visibility", "start": 11.3, "end": 11.9,
                     "timed": True},
                    {"word": " system", "start": 11.9, "end": 12.4,
                     "timed": True},
                ],
                "read_from_words": False,
                "avg_logprob": -0.2,
            },
            {
                "speaker": "Craig",
                "text": "Lucy helps teams ship",
                "timeline_start": 20.0,
                "timeline_end": 22.0,
                "source_file": "LCATL0013.MXF",
                "source_start": 200.0,
                "source_end": 202.0,
                "resolve_item_id": "clip-2",
                "words": [
                    {"word": "Lucy", "start": 20.0, "end": 20.4,
                     "timed": True},
                    {"word": " helps", "start": 20.4, "end": 20.8,
                     "timed": True},
                ],
                "read_from_words": False,
                "avg_logprob": -0.1,
            },
        ],
    }


def test_spelling_correction_is_recorded_as_a_learned_correction(tmp_path):
    from library.tools import transcript_corrections as tc
    rec = tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516: the company Lucie Content, "
        "not a person")
    assert rec["kind"] == "correction"
    assert rec["status"] == "active"
    assert rec["source"]["heard"] == "lucy"
    assert rec["source"]["correct"] == "Lucie"
    store = json.loads(
        (tmp_path / "learned_context" / "learnings.json").read_text(
            encoding="utf-8"))
    assert len(store) == 1


def test_apply_rewrites_segment_text_and_words(tmp_path):
    from library.tools import transcript_corrections as tc
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    doc = _doc_with_lucy()
    report = tc.apply_to_document(doc, str(tmp_path))
    assert report["replacements"] >= 2
    assert "lucy" not in doc["segments"][0]["text"].lower().replace(
        "lucie", "")
    assert "Lucie" in doc["segments"][0]["text"]
    words = [w["word"] for w in doc["segments"][0]["words"]]
    assert any("Lucie" in w for w in words)
    assert not any(w.strip().lower() == "lucy" for w in words)
    # Title-case heard is corrected too.
    assert "Lucie" in doc["segments"][1]["text"]
    # Timings untouched: a correction respells, never re-times.
    assert doc["segments"][0]["words"][3]["start"] == 10.9
    # Idempotent: a second pass finds nothing to do.
    again = tc.apply_to_document(doc, str(tmp_path))
    assert again["replacements"] == 0


def test_apply_preserves_possessives_and_punctuation():
    from library.tools import transcript_corrections as tc
    corrections = [{"heard": "lucy", "correct": "Lucie", "id": "lc-0001"}]
    out, n = tc.apply_spelling("we love lucy's work, lucy!", corrections)
    assert out == "we love Lucie's work, Lucie!"
    assert n == 2
    out, n = tc.apply_spelling("LUCY SHOUTS", corrections)
    assert out == "LUCIE SHOUTS"
    # Substrings are not words: "lucy's" handled, "hallucinate" untouched.
    out, _ = tc.apply_spelling("do not hallucinate", corrections)
    assert out == "do not hallucinate"


def test_transcribe_forwards_bias_arguments():
    """What this stack honours is tested at the wiring, not read off
    docs: `transcribe_audio` must hand `initial_prompt`/`hotwords` to
    faster-whisper's decoder. A stubbed model records what it got."""
    import types
    seen = {}

    class FakeSegment:
        start, end, text, avg_logprob = 0.0, 1.0, "lucy systems", -0.1

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            info = types.SimpleNamespace(language="en")
            return iter([FakeSegment()]), info

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = lambda *a, **k: FakeModel()
    fake_wx = types.ModuleType("whisperx")
    fake_wx.load_audio = lambda p: [0.0]
    fake_wx.load_align_model = lambda **k: (None, None)
    fake_wx.align = lambda segments, *a, **k: {
        "segments": [{"start": s["start"], "end": s["end"],
                      "text": s["text"], "words": []}
                     for s in segments],
        "language": "en"}
    sys.modules["faster_whisper"] = fake_fw
    sys.modules["whisperx"] = fake_wx
    try:
        import torch  # noqa: F401 - real torch is fine if present
    except ImportError:
        fake_torch = types.ModuleType("torch")
        fake_backends = types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: False))
        fake_torch.backends = fake_backends
        sys.modules["torch"] = fake_torch
    try:
        from importlib import reload
        import library.tools.timeline_transcript as tt
        reload(tt)
        tt.transcribe_audio(
            Path("/tmp/nowhere.wav"), initial_prompt="Lucie Content",
            hotwords="Lucie Content")
    finally:
        sys.modules.pop("faster_whisper", None)
        sys.modules.pop("whisperx", None)
    assert seen.get("initial_prompt") == "Lucie Content"
    assert seen.get("hotwords") == "Lucie Content"


def test_bias_strings_carry_every_correction(tmp_path):
    from library.tools import transcript_corrections as tc
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    prompt, hotwords = tc.bias_strings(str(tmp_path))
    assert "Lucie" in prompt
    assert "Lucie" in hotwords


def test_keep_exclusion_trims_a_fragment_off_a_moment():
    """Frame 528: "so what do they" at the range head goes, the rest of
    the audio stays. A trim, not a drop."""
    from library.tools import transcript_corrections as tc
    moments = [{"start": 20.0, "end": 40.0, "slug": "reel-09"}]
    exclusions = [{"start": 20.0, "end": 23.5, "id": "lc-0002",
                   "reason": "captain: feels like a mistake"}]
    kept, dropped = tc.apply_keep_exclusions(moments, exclusions)
    assert len(kept) == 1 and not dropped
    assert kept[0]["start"] == 23.5
    assert kept[0]["end"] == 40.0
    assert kept[0]["trimmed_by"] == ["lc-0002"]


def test_keep_exclusion_in_the_middle_drops_and_says_so():
    """An interior exclusion would split one reel into two - a new
    editorial decision, not an enforcement. Dropped with the reason."""
    from library.tools import transcript_corrections as tc
    moments = [{"start": 20.0, "end": 40.0, "slug": "reel-09"}]
    exclusions = [{"start": 28.0, "end": 30.0, "id": "lc-0003",
                   "reason": "captain: mistake in the middle"}]
    kept, dropped = tc.apply_keep_exclusions(moments, exclusions)
    assert not kept
    assert len(dropped) == 1
    assert "lc-0003" in dropped[0]["reason"]


def _reel_transcript():
    segments = []
    for i in range(4):
        start = 20.0 + i * 5.0
        segments.append({
            "speaker": "Craig" if i % 2 == 0 else "Akshita",
            "text": f"line number {i} here",
            "timeline_start": start,
            "timeline_end": start + 5.0,
            "source_file": "LCATL0013.MXF",
            "source_start": start,
            "source_end": start + 5.0,
            "resolve_item_id": f"clip-{i}",
            "words": [{"word": "here", "start": start + 1.0,
                       "end": start + 1.5, "timed": True}],
            "read_from_words": False,
        })
    return {"derived_from": {"duration_seconds": 100.0},
            "segments": segments}


def test_select_reels_post_bridge_trims_a_recorded_exclusion(tmp_path):
    """Durability for keep ranges, by demonstration: with the exclusion
    on file, a regenerated proposal opens past the struck fragment -
    nobody re-argues it, nobody touches the render."""
    from library.tools import transcript_corrections as tc
    tc.record_keep_exclusion(
        str(tmp_path), 20.0, 23.5,
        reason="captain frame 528: 'so what do they' feels like a mistake")
    import importlib.util
    path = (REPO_ROOT / "library" / "steps" / "step_3_04_select_reels"
            / "post_bridge.py")
    spec = importlib.util.spec_from_file_location(
        "select_reels_post_bridge", str(path))
    post_bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(post_bridge)
    out = post_bridge.resolve(
        {"moments": [{"start": 21.0, "end": 39.0, "slug": "reel-09",
                      "reason": "the website point"}]},
        {"timeline_transcript": _reel_transcript(),
         "project_folder": str(tmp_path)})
    moments = out["reel_selection"]["moments"]
    assert len(moments) == 1
    # 23.5 sits inside the 20-25s segment, so the trimmed edge moves
    # onto the segment grid away from the struck seconds: 25.0.
    assert moments[0]["timeline_start"] == 25.0
    assert out["reel_selection"]["dropped"] == []
    # The struck line never reaches the preview the captain approves.
    assert "line number 0" not in moments[0]["transcript_preview"]


def test_played_speech_reads_lucie_after_correction(tmp_path):
    """The captain's demonstration at unit level: the SAME list the
    builder, the caption pass and the verifier all read
    (`reel_quality_bar.played_speech`) says "lucy" before the fix and
    "Lucie" after - with no render prop touched."""
    from library.tools import transcript_corrections as tc
    from library.tools.reel_quality_bar import played_speech

    class Moment:
        timeline_start, timeline_end = 9.0, 14.0
        call_to_action = None

    doc = _doc_with_lucy()
    before = played_speech(Moment(), doc)
    assert any("lucy" in line["text"].lower() for line in before)
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    tc.apply_to_document(doc, str(tmp_path))
    after = played_speech(Moment(), doc)
    assert after
    assert all("lucy" not in line["text"].lower().replace("lucie", "")
               for line in after)
    assert any("Lucie" in line["text"] for line in after)
    with_words = played_speech(Moment(), doc, with_words=True)
    assert any("Lucie" in w.get("word", "")
               for line in with_words for w in line.get("words", []))


def test_render_for_model_names_the_verdict(tmp_path):
    from library.tools import transcript_corrections as tc
    assert tc.render_for_model(str(tmp_path)) == ""
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    note = tc.render_for_model(str(tmp_path))
    assert "lucy" in note and "Lucie" in note


def test_correction_reaches_the_motion_graphics_planner_prompt(tmp_path):
    """The authored-copy half: a `read_by: ["*"]` correction is routed
    to the MG planner through `project_context`, so model-written copy
    spells it right. 4.06 declares the input; the runner restores it by
    name into every prompt."""
    import json
    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    prompt_text = lc.render_for_prompt(
        str(tmp_path), "render_motion_graphics")
    assert "Lucie" in prompt_text
    manifest = json.loads(
        (REPO_ROOT / "library" / "steps"
         / "step_4_06_render_motion_graphics" / "manifest.json")
        .read_text(encoding="utf-8"))
    declared = [i.get("name")
                for i in manifest["interface"]["inputs"]]
    assert "project_context" in declared


def test_reel_semantic_request_carries_the_correction(tmp_path):
    """The reel path builds its own ask outside the runner, so the
    correction is written into the request context directly - same
    store, same verdict, no second mechanism."""
    from library.tools import reel_semantic_visual as sem
    from library.tools import transcript_corrections as tc
    spine = {"structure": [{
        "position": 1, "block_type": "speech",
        "timeline_start": 0.0, "timeline_end": 4.0,
        "content": {"text": "we're calling the lucy visibility system"},
    }]}
    plain = sem.bridge_context(spine, "", 23.976)
    assert plain["transcript_corrections"] == ""
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    context = sem.bridge_context(spine, str(tmp_path), 23.976)
    assert "Lucie" in context["transcript_corrections"]
