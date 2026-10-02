"""A reel is a conversational EXCHANGE, measured and never scored.

The first batch of ten was rejected as "mostly just a single person
yapping and not really a convo". Nine of the ten had one speaker, and the
`speakers` field said so on every one - so the test that matters most
here is that a single-speaker window cannot become a candidate at all.

The captain's worked example, 0:00-3:13, is the fixture the numbers are
checked against: it must yield exactly TWO conversations, because they
described it as "a reel or two".
"""
from __future__ import annotations
import pytest
from library.tools.reel_exchange import (
    MAX_REEL_SECONDS,
    MIN_REEL_SECONDS,
    POSSIBLE_RETAKE,
    SAME_EXCHANGE,
    Exchange,
    Turn,
    collapse_overlapping,
    collapse_retakes,
    containment,
    exchange_windows,
    funnel,
    turns_from_transcript,
)
import pathlib
from library.tools import craft_role
from library.tools.undetermined import DECLARING_STEPS
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
import json
import os
import subprocess
import sys
from pathlib import Path
from library.tools.reel_opening import (
    observations,
    opening_words,
)


def _seg(speaker, text, start, end, uid="uid-1"):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": "/m/a.MXF",
            "source_start": start, "source_end": end,
            "resolve_item_id": uid}


def _turn(speaker, start, end, text="some words here about the topic"):
    return Turn(speaker=speaker, start=start, end=end, text=text)


# ── Turns ────────────────────────────────────────────────────────────

def test_consecutive_segments_from_one_speaker_are_one_turn():
    tx = {"segments": [_seg("Craig", "first part", 0.0, 4.0),
                       _seg("Craig", "second part", 4.2, 8.0, "uid-2"),
                       _seg("Akshita", "the answer", 9.0, 15.0, "uid-3")]}
    turns = turns_from_transcript(tx)
    assert [t.speaker for t in turns] == ["Craig", "Akshita"]
    assert turns[0].text == "first part second part"
    assert turns[0].duration == pytest.approx(8.0)


def test_a_long_pause_starts_a_new_turn():
    tx = {"segments": [_seg("Craig", "before", 0.0, 4.0),
                       _seg("Craig", "after", 20.0, 24.0, "uid-2")]}
    assert len(turns_from_transcript(tx)) == 2


def test_a_straddling_segment_is_not_a_turn():
    """Its text is not reliably in the cut at all."""
    tx = {"segments": [_seg("Craig", "real", 0.0, 4.0),
                       dict(_seg("Craig", "bridged", 20.0, 40.0),
                            resolve_item_id=None)]}
    assert len(turns_from_transcript(tx)) == 1


# ── The defect that killed batch one ─────────────────────────────────

def test_a_single_speaker_window_is_never_a_candidate():
    """Nine of the first ten proposals were one speaker. This is the
    check that makes that impossible rather than merely discouraged."""
    turns = [_turn("Akshita", 0.0, 30.0), _turn("Akshita", 31.0, 60.0)]
    assert exchange_windows(turns, "Craig", "Akshita") == []


# ── The captain's length brief ───────────────────────────────────────

def test_length_is_guidance_and_a_long_story_is_still_reported():
    """Was a HARD window until 2026-09-04, which silently withheld every
    stretch needing longer to finish. A story that runs 95s because that
    is how long it takes to close is a real answer."""
    turns = [_turn("Craig", 0.0, 10.0), _turn("Akshita", 11.0, 100.0)]  # 100s
    windows = exchange_windows(turns, "Craig", "Akshita")
    assert windows, "a 100s exchange must be offered, not withheld"
    assert windows[0].measurements()["within_length_guidance"] is False


# ── Concerns are raised, never enforced ──────────────────────────────

def test_a_closing_pitch_is_measured_and_never_a_concern():
    """The captain's unit is "an atomic segment of conversation that
    provides value and then makes a little CTA at the end". Treating the
    pitch as a defect discarded ten candidates and removed the ending the
    format is built on."""
    turns = [_turn("Craig", 0.0, 20.0, "so why does that matter to a business"),
             _turn("Akshita", 21.0, 40.0, "because AI cannot describe you"),
             _turn("Craig", 41.0, 55.0,
                   "go check out the lucy visibility score on our website")]
    window = exchange_windows(turns, "Craig", "Akshita")[0]
    assert not any("pitch" in c for c in window.concerns), (
        "a closing CTA is the ending, not a defect")
    body = window.measurements()
    assert body["pitch_share"] > 0, "it is REPORTED"
    assert body["closes_on_pitch"] is True
    # Nothing is scored: which conversation matters is taste (AGENTS.md 10.5).
    assert "score" not in body and "rank" not in body


def test_one_sided_is_the_conjunction_of_low_share_and_speaking_once():
    """1:52-2:59 is one of the captain's own good examples and Craig
    holds only 9% of it - because he contributes twice, a real question
    and a real reaction. Alternation count alone discriminates nothing,
    so the concern is the CONJUNCTION."""
    once = [_turn("Craig", 0.0, 4.0, "what about that"),
            _turn("Akshita", 5.0, 60.0, "a very long answer indeed " * 5)]
    windows = exchange_windows(once, "Craig", "Akshita")
    assert any("speaks once" in c for c in windows[0].concerns)

    returns = [_turn("Craig", 0.0, 3.0, "so give me an example of that"),
               _turn("Akshita", 4.0, 35.0, "the audit example " * 6),
               _turn("Craig", 36.0, 41.0,
                     "they used to call that keyword stuffing"),
               _turn("Akshita", 42.0, 60.0, "and it works against you " * 4)]
    windows = exchange_windows(returns, "Craig", "Akshita")
    assert windows[0].share_for("Craig") < 0.25
    assert windows[0].alternations >= 3
    assert not any("speaks once" in c for c in windows[0].concerns)


# ── Same stretch versus recorded twice ───────────────────────────────

def test_a_conversation_recorded_twice_collapses_and_two_different_do_not():
    words = "seo convinces an algorithm to rank pages geo makes ai comprehend"
    a = Exchange(0.0, 55.0, (_turn("Craig", 0.0, 20.0, words),
                             _turn("Akshita", 21.0, 55.0, words)))
    b = Exchange(60.0, 118.0, (_turn("Craig", 60.0, 80.0, words),
                               _turn("Akshita", 81.0, 118.0, words)))
    groups = collapse_retakes([a, b])
    assert len(groups) == 1
    assert groups[0][1].retake_band == "same"

    c = Exchange(0.0, 55.0, (_turn("Craig", 0.0, 20.0, "seo algorithm ranking pages google"),
                             _turn("Akshita", 21.0, 55.0, "position versus comprehension")))
    d = Exchange(60.0, 118.0, (_turn("Craig", 60.0, 80.0, "competitors nine times visibility report"),
                               _turn("Akshita", 81.0, 118.0, "modules citations directories")))
    assert len(collapse_retakes([c, d])) == 2


def test_containment_not_jaccard():
    """Two takes of one exchange differ in LENGTH - the second is usually
    more complete - and Jaccard punishes exactly that."""
    short = Exchange(0.0, 50.0, (_turn("Craig", 0.0, 25.0, "algorithm ranking pages"),
                                 _turn("Akshita", 26.0, 50.0, "comprehension")))
    long = Exchange(60.0, 120.0, (
        _turn("Craig", 60.0, 90.0, "algorithm ranking pages"),
        _turn("Akshita", 91.0, 120.0,
              "comprehension plus directories citations linkedin reddit press")))
    assert containment(short, long) == 1.0


# ── The funnel reports what LEFT ─────────────────────────────────────


def test_a_flagged_window_is_reported_not_deleted():
    turns = [_turn("Craig", 0.0, 20.0,
                   "the lucy visibility score go to our website check it out"),
             _turn("Akshita", 21.0, 50.0)]
    report = funnel(turns, "Craig", "Akshita")
    assert report["flagged"], "a dropped window must still be reported"
    assert report["flagged"][0].concerns


def test_overlap_groups_do_not_chain():
    """A overlaps B and B overlaps C does NOT put C in A's group. The
    head is the only window that reaches the candidate table, so a chain
    deletes everything it swallowed: measured on the field test, five
    windows spanning 248s collapsed to one 50s representative and the
    candidate covering the captain's approved reel 03 left the table."""
    a = Exchange(0.0, 50.0, (_turn("Craig", 0.0, 20.0),
                             _turn("Akshita", 21.0, 50.0)))
    b = Exchange(40.0, 90.0, (_turn("Craig", 40.0, 60.0),
                              _turn("Akshita", 61.0, 90.0)))
    c = Exchange(80.0, 130.0, (_turn("Craig", 80.0, 100.0),
                               _turn("Akshita", 101.0, 130.0)))
    groups = collapse_overlapping([a, b, c])
    # A and B overlap: one stretch, not two takes. C is its own group.
    assert [(g[0].start, g[0].end) for g in groups] == [(0.0, 50.0), (80.0, 130.0)]


# --------------------------------------------------------------------------
# From test_reel_selection_reaches_a_model.py
#
# Reel selection reaches a MODEL, and says which craft it is.
#
# Every guard aims at the ENGINE inventing taste; these look the other way
# and ask whether a model is actually reached. History:
# docs/evidence/select_reels.md.

REPO = pathlib.Path(__file__).resolve().parents[3]


# ── The step reaches a model ─────────────────────────────────────────

def test_reel_selection_is_a_declared_model_step_with_a_neutral_role():
    """The question that would have caught this on the first batch:
    which model chose, and where is its handoff? The role hands over
    capability and authority, never a count, a strength or a direction."""
    assert "select_reels" in DECLARING_STEPS
    assert craft_role.declares("select_reels")
    role = craft_role.role_for("select_reels")
    block = craft_role.prompt_block("select_reels")
    assert role.decides and role.defers
    assert craft_role.NEUTRALITY_LINE in block
    lowered = block.lower()
    for forbidden in ("pick 16", "choose 16", "at least 20", "aim for"):
        assert forbidden not in lowered, f"the role states taste: {forbidden!r}"


# ── Measurements are context, not gates ──────────────────────────────

def test_a_closing_pitch_is_never_a_reason_to_withhold_a_candidate():
    from library.tools.reel_exchange import Turn, exchange_windows

    turns = [Turn("Craig", 0.0, 20.0, "so why does that matter to a business"),
             Turn("Akshita", 21.0, 40.0, "because AI cannot describe you"),
             Turn("Craig", 41.0, 55.0,
                  "go check out the lucy visibility score on our website")]
    window = exchange_windows(turns, "Craig", "Akshita")[0]
    assert not any("pitch" in c for c in window.concerns)
    assert window.measurements()["closes_on_pitch"] is True


def test_a_long_story_is_offered_with_its_length_not_truncated():
    from library.tools.reel_exchange import Turn, exchange_windows

    turns = [Turn("Craig", 0.0, 10.0, "what is the story here"),
             Turn("Akshita", 11.0, 100.0, "a long answer that lands late")]
    windows = exchange_windows(turns, "Craig", "Akshita")
    assert windows, "a 100s exchange must be offered, not withheld"
    assert windows[0].measurements()["within_length_guidance"] is False


# ── The step can actually RUN ────────────────────────────────────────

STEP_DIR = REPO / "library/steps/step_3_04_select_reels"


def test_the_bridge_publishes_the_tables_the_handoff_names():
    """A handoff naming a table the bridge does not build is a prompt
    describing something that never arrives."""
    import json

    from library.steps.step_3_04_select_reels.bridge import build_context

    # a step that cannot execute is not a step: no handoff means no
    # System Context and nothing for the role to prepend to
    for name in ("manifest.json", "handoff.md", "bridge.py", "post_bridge.py"):
        assert (STEP_DIR / name).is_file(), f"select_reels has no {name}"
    manifest = json.loads((STEP_DIR / "manifest.json").read_text())
    # The manifest's TOP LEVEL, which is where the projection reads it.
    # This assertion used to name `interface`, which is where the
    # declaration sat and where nothing read it - so it passed while the
    # projection never ran. See tests/contracts/test_context_contracts.py.
    assert "reel_candidates" in " ".join(manifest["context_fields"])

    context = build_context({"timeline_transcript": {"segments": []}})
    for table in ("turns", "reel_candidates"):
        assert table in context, f"the handoff names {table!r}; the bridge must build it"


def test_the_post_bridge_leaves_every_moment_proposed():
    from library.tools.reel_proposal import Approval, read_proposal  # noqa
    from library.steps.step_3_04_select_reels.post_bridge import resolve

    seg1 = {"speaker": "Craig", "text": "a line about the topic here",
            "timeline_start": 10.0, "timeline_end": 30.0,
            "resolve_item_id": "u", "source_file": "/m/a.MXF",
            "source_start": 10.0, "source_end": 30.0}
    seg2 = {"speaker": "Akshita", "text": "and here is the response",
            "timeline_start": 30.5, "timeline_end": 55.0,
            "resolve_item_id": "u2", "source_file": "/m/b.MXF",
            "source_start": 30.5, "source_end": 55.0}
    out = resolve({"moments": [{"start": 12.0, "end": 55.0, "slug": "x",
                                "reason": "because it lands"}]},
                  {"timeline_transcript": {"segments": [seg1, seg2],
                                           "derived_from": {"duration_seconds": 600.0}}})
    moments = out["reel_selection"]["moments"]
    assert moments and all(m["approval"] == "proposed" for m in moments)


# --------------------------------------------------------------------------
# From test_select_reels_speakers.py
#
# Reel speaker counts come from the project, not a constant 2.
#
# The defects these pin:
#
# - select_reels refused anything under two voices ("cannot invent a
#   second voice"), so a one-speaker project could never reach a first
#   candidate. A declared single speaker now takes the monologue path:
#   runs of that voice grown to length, measured with the same table
#   minus the exchange structure.
# - A declared-zero project (music / montage) offers no candidates
#   with the reason stated, instead of the invented-second-voice
#   refusal.
# - `is_conversation` dropped every single-speaker moment and
#   `check_plan_speakers` warned below two, whatever the project
#   declares. Both read the declared count now; undeclared still
#   reads as the historical two.

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


# --------------------------------------------------------------------------
# From test_select_reels_post_bridge_subprocess.py
#
# The select_reels approval line names THIS project's transcript, or none.
#
# It once carried one project's absolute path as a literal, so every other
# project was told its transcript lived in the podcast field test; the
# engine states no series' own paths (AGENTS.md 14).

def _approval(input_data):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd())
    proc = subprocess.run(
        [sys.executable, "library/steps/step_3_04_select_reels/post_bridge.py"],
        input=json.dumps(input_data), capture_output=True,
        encoding="utf-8", env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["reel_selection"]["approval"]


def test_the_approval_line_names_only_this_projects_transcript(tmp_path):
    approval = _approval({
        "project_folder": str(tmp_path),
        "timeline_transcript": {"segments": [],
                                "derived_from": {"duration_seconds": 10.0}},
        "reel_selection": {"moments": []},
    })
    assert "geo-podcast" not in approval, (
        f"one project's path is still baked into the engine: {approval}")
    assert str(tmp_path) in approval, (
        f"the approval names no transcript for the project it ran for: "
        f"{approval}")

    # No project folder, no claim about where a file is: a path derived
    # from an empty folder would print `/pipeline_output/scratch/...` and
    # read as a real location.
    approval = _approval({
        "timeline_transcript": {"segments": [],
                                "derived_from": {"duration_seconds": 1.0}},
        "reel_selection": {"moments": []}})
    assert "transcript" not in approval.lower().replace(
        "reel_proposal.assert_approved", ""), approval


# --------------------------------------------------------------------------
# From test_reel_opening.py
#
# The opening of a reel, measured - and the four it found.
#
# The handoff for step 3.4 has always stated the hook rule: "The first line
# has to earn the next five seconds: a question, a claim, or a provocation.
# Not throat-clearing, not a speaker settling into a sentence." Nothing
# measured whether a proposal obeyed it, and four of the nineteen reels the
# captain approved on 2026-09-05 opened on exactly what it forbids.
#
# These tests pin what is reported, what is deliberately NOT reported, and
# that a good opening produces nothing - the last of which is what stops
# this becoming another gate that fires on correct output.

def _words(text: str, speaker: str = "Akshita") -> list:
    return [{"word": w, "speaker": speaker} for w in text.split()]


# ── What it reports ──────────────────────────────────────────────────

def test_a_back_reference_is_reported():
    """Reel 04 opened on the word 'earlier'."""
    observed = observations(
        _words("earlier it doesn't even matter if you're a 15 man shop"))

    assert len(observed) == 1
    assert observed[0]["observation"] == "back_reference"
    assert observed[0]["matched"] == "earlier"
    assert "outside this reel" in observed[0]["why"]
    assert observed[0]["the_fix"]
    # taste is the model's: an observation, never a score or a verdict
    assert set(observed[0]) == {"observation", "opening", "matched",
                                "why", "the_fix"}
    # Reel 07 opened on 'Absolutely', answering nothing the viewer heard.
    observed = observations(
        _words("Absolutely. And just like a hiring manager would check both"),
        span_text="Absolutely. And just like a hiring manager would check "
                  "both. So your website is your resume.")

    assert [o["observation"] for o in observed] == ["answer_with_no_question"]
    assert observed[0]["matched"] == "Absolutely"


# ── What it does NOT report, which is the harder half ────────────────

def test_a_good_or_merely_plain_opening_produces_nothing():
    """The check must be able to pass, or it is not a check.

    An acknowledgement inside a reel that opens on the question it answers
    is ordinary conversation; a question opening is the hook; and
    throat-clearing (reel 01, "okay so i'm hearing...") is deliberately
    NOT matched - that is a judgement about writing, and taste belongs to
    the model, so the omission is a decision rather than a miss.
    """
    assert observations(
        _words("Absolutely. So they're completely different systems"),
        span_text="So what do marketing directors get wrong about geo? "
                  "Absolutely. So they're completely different systems.",
    ) == []
    assert observations(_words("Why do AI platforms love video content?")) == []
    assert observations(
        _words("okay so i'm hearing just from a lot of different "
               "marketing directors")) == []


# ── The words it measures ────────────────────────────────────────────

def _transcript_2(segments):
    return {"segments": segments}


def _seg_2(speaker, text, start, step=0.3):
    words = [{"word": w, "start": start + i * step,
              "end": start + (i + 1) * step, "timed": True}
             for i, w in enumerate(text.split())]
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": start + len(text.split()) * step,
            "source_file": "/m/a.MXF", "words": words}


def test_opening_words_are_read_through_the_keep_ranges():
    """A take cut out of the opening takes its words with it."""
    tx = _transcript_2([_seg_2("Akshita", "this whole take was flubbed", 0.0),
                      _seg_2("Akshita", "Yeah so we ran an audit", 10.0)])

    # Only the second range plays.
    got = opening_words([(10.0, 20.0)], tx)

    assert [w["word"] for w in got][:3] == ["Yeah", "so", "we"]
    assert "flubbed" not in [w["word"] for w in got]


def test_untimed_words_never_reach_the_opening():
    """A segment carrying text with no word timings is an artefact.

    Reel 03 carries three of them - 40 words of text inside 1.06s, up to
    80 words a second. Placing those in an opening would report a hook
    nobody spoke.
    """
    tx = _transcript_2([
        {"speaker": "Akshita", "text": "a whole sentence with no timings",
         "timeline_start": 0.0, "timeline_end": 0.2,
         "source_file": "/m/a.MXF",
         "words": [{"word": "a", "start": 0.0, "end": 0.1, "timed": False}]},
        _seg_2("Akshita", "Why do AI platforms love video", 1.0),
    ])

    got = opening_words([(0.0, 30.0)], tx)

    assert [w["word"] for w in got][0] == "Why"


@pytest.mark.parametrize("opener", ["Yes"])
def test_every_answer_shape_is_matched_at_the_start_only(opener):
    assert observations(_words(f"{opener} and here is why"))
    assert observations(_words(f"the answer is {opener.lower()} and here is why")) == []
