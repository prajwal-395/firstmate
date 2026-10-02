"""Reel speaker counts come from the project, not a constant 2.

The defects these pin:

- select_reels refused anything under two voices ("cannot invent a
  second voice"), so a one-speaker project could never reach a first
  candidate. A declared single speaker now takes the monologue path:
  runs of that voice grown to length, measured with the same table
  minus the exchange structure.
- A declared-zero project (music / montage) offers no candidates
  with the reason stated, instead of the invented-second-voice
  refusal.
- `is_conversation` dropped every single-speaker moment and
  `check_plan_speakers` warned below two, whatever the project
  declares. Both read the declared count now; undeclared still
  reads as the historical two.
"""

from __future__ import annotations


from library.steps.step_3_04_select_reels.bridge import (
    build_context,
)
from library.tools.reel_exchange import monologue_windows
from library.tools.reel_conformance_verifier import (
    FindingClass,
    PlannedPlacement,
    check_plan_speakers,
)
from library.tools.reel_proposal import ReelMoment, is_conversation


def _segments(speaker, count=8, start=0.0, length=8.0, gap=12.0):
    """Bound speech segments with pauses wide enough to stay separate
    turns (turns merge under 2.5 s)."""
    out = []
    t = start
    for i in range(count):
        out.append({
            "text": "sentence number %d about the topic at hand here"
                    % i,
            "speaker": speaker,
            "timeline_start": t,
            "timeline_end": t + length,
            "resolve_item_id": "id%d" % i,
        })
        t += gap
    return out


def _transcript(speaker="Jo", count=8):
    segments = _segments(speaker, count)
    return {"segments": segments,
            "derived_from": {"duration_seconds": segments[-1]["timeline_end"]}}


def _moment(start, end, number=1):
    return ReelMoment(number=number, slug="s", reason="r",
                      timeline_start=start, timeline_end=end)


# ── the bridge ───────────────────────────────────────────────────

def test_monologue_project_reaches_candidates(tmp_path):
    """The defect: a one-speaker cut offered nothing. Now it offers
    measured windows of the one voice."""
    project = tmp_path / "p"
    project.mkdir()
    (project / "project.yaml").write_text(
        "source:\n  speakers:\n    - name: Jo\n")
    context = build_context({"timeline_transcript": _transcript(),
                             "project_folder": str(project)})
    assert context["lead_speaker"] == "Jo"
    assert "answering_speaker" not in context
    assert context["declared_speakers"] == [{"name": "Jo"}]
    assert len(context["reel_candidates"]) >= 1
    first = context["reel_candidates"][0]
    assert first["speakers"] == ["Jo"]
    assert first["alternations"] == 0


def test_zero_speaker_project_offers_nothing_with_a_reason(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "project.yaml").write_text("source:\n  speakers: []\n")
    context = build_context({"timeline_transcript": _transcript(),
                             "project_folder": str(project)})
    assert context["reel_candidates"] == []
    assert context["declared_speakers"] == []
    assert any("no speakers" in line
               for line in context["undetermined"])


def test_undeclared_project_keeps_the_old_refusal(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    context = build_context({"timeline_transcript": _transcript(),
                             "project_folder": str(project)})
    assert context["reel_candidates"] == []
    assert "declared_speakers" not in context
    assert any("second voice" in line
               for line in context["undetermined"])


def test_roster_mismatch_is_reported_not_smoothed(tmp_path):
    """A voice the roster does not name is said out loud; the count
    the thresholds read does not move."""
    project = tmp_path / "p"
    project.mkdir()
    (project / "project.yaml").write_text(
        "source:\n  speakers:\n    - name: Jo\n    - name: Bo\n")
    segments = (_segments("Jo", 4)
                + _segments("Zed", 4, start=100.0))
    transcript = {"segments": segments, "derived_from": {}}
    context = build_context({"timeline_transcript": transcript,
                             "project_folder": str(project)})
    assert any("Zed" in line for line in context["undetermined"])


# ── monologue windows ────────────────────────────────────────────

def test_monologue_windows_grow_runs_of_the_voice_and_ignore_others():
    from library.tools.reel_exchange import turns_from_transcript
    turns = turns_from_transcript(_transcript())
    windows = monologue_windows(turns, "Jo")
    assert len(windows) >= 1
    assert all(set(w.speakers) <= {"Jo"} for w in windows)
    assert all(w.duration >= 45.0 for w in windows)
    # another voice in the transcript is never folded into Jo's windows
    segments = (_segments("Jo", 8)
                + _segments("Bo", 8, start=200.0))
    turns = turns_from_transcript({"segments": segments,
                                   "derived_from": {}})
    windows = monologue_windows(turns, "Jo")
    assert windows and all(w.speakers == ["Jo"] for w in windows)


# ── is_conversation ──────────────────────────────────────────────

def test_is_conversation_reads_the_declared_speaker_count():
    transcript = _transcript()
    moment = _moment(0.0, 30.0)
    assert is_conversation(moment, transcript, min_speakers=2) is not None
    assert is_conversation(moment, transcript, min_speakers=1) is None
    # a speechless moment fails at any count above zero
    silent = {"segments": [], "derived_from": {}}
    assert is_conversation(moment, silent, min_speakers=1) is not None
    assert is_conversation(moment, silent, min_speakers=0) is None


# ── check_plan_speakers ──────────────────────────────────────────

def _placement(speaker, duration=50.0):
    return PlannedPlacement(
        track_index=1, speaker=speaker, record_seconds=0.0,
        source_in=0.0, source_out=duration,
        source_file="/m/a.MXF")


def test_check_plan_speakers_reads_the_declared_count():
    one_voice = (_placement("Jo"),)
    assert check_plan_speakers("Reel 01", one_voice, expected_speakers=1) == []
    assert check_plan_speakers("Reel 01", one_voice, expected_speakers=0) == []
    # undeclared still requires two
    findings = check_plan_speakers("Reel 01", one_voice)
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.PQ_SPEAKERS
