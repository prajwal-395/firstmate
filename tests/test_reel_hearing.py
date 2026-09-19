"""The hearing pass finds the Reel 26 defect, and gates nothing.

Reel 26 is the known-answer case: a defect that SHIPPED, traced to one
transcript row, with three separate consequences in the delivered file.
`tests/fixtures/reel_hearing/README.md` carries the whole story. A test
that pins it is worth more than any synthetic one, so the pinning tests
here run over that recorded evidence and assert the numbers the pass was
measured to produce.

Nothing here reaches a real project: the fixture is three trimmed JSON
documents in the repository, copied into `tmp_path` where a project is
needed, and the transcriber never runs - its output is REPLAYED through
the one function that maps it.
"""
import json
import shutil
from pathlib import Path

import pytest

from library.tools import heard_speech, qa_findings, reel_hearing

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reel_hearing"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def timeline():
    return _load("reel26.timeline.json")


@pytest.fixture
def transcript():
    return _load("reel26.transcript.json")


@pytest.fixture
def spoken():
    """The recorded transcription, through the mapper that owns its keys."""
    return heard_speech.read_payload(_load("reel26.heard.json"),
                                     "reel26.mp4")


@pytest.fixture
def project(tmp_path):
    """A project carrying only the spelling correction this reel needs.

    Recorded through the store's own writer rather than hand-written, so
    the fixture cannot drift from the shape the reader expects.
    """
    from library.tools import transcript_corrections

    folder = tmp_path / "geo_podcast"
    folder.mkdir()
    transcript_corrections.record_spelling(
        str(folder), "lucy", "Lucie",
        'captain, Reel 09 frame 1516: it is Lucie Content the company')
    return folder


@pytest.fixture
def hearing(timeline, transcript, spoken, project):
    return reel_hearing.hear(timeline, transcript, spoken,
                             project_folder=str(project),
                             timeline_path="reel26.timeline.json",
                             video_path=str(project / "reel26.mp4"))


# ── 1. The three consequences ────────────────────────────────────────

def test_the_late_caption_shows_up_as_a_drift_run(hearing):
    """Nine consecutive words a second early. Consequence 3."""
    worst = max(hearing.runs, key=lambda r: abs(r["median_drift_seconds"]))
    assert worst["words"] == 9, hearing.runs
    assert worst["text"].startswith("Make sure that you're coming up")
    assert worst["direction"] == "early"
    assert -1.1 < worst["median_drift_seconds"] < -0.9, worst
    # Four times the transcribers' own measured disagreement, which is
    # what makes it a finding rather than noise.
    assert abs(worst["median_drift_seconds"]) > \
        4 * reel_hearing.DRIFT_NOISE_FLOOR_SECONDS


def test_the_six_uncaptioned_words_are_named(hearing):
    """Speech with no caption on screen at all. Consequence 2."""
    zero = hearing.coverage["uncaptioned_words"]
    words = [row["word"] for row in zero]
    assert "make sure that you're coming up" in " ".join(words).lower(), words
    assert hearing.coverage["measured"] is True
    assert hearing.coverage["caption_cards"] == 13


def test_the_words_the_speaker_really_said_are_heard_and_unplanned(hearing):
    """`niche`, which the plan does not carry. Consequence 1."""
    extra = [row for row in hearing.divergences
             if row["kind"] == reel_hearing.EXTRA]
    assert "niche" in {row["heard"].lower().strip(",.") for row in extra}, extra


def test_the_upstream_cause_is_a_FINDING_not_a_note(hearing):
    """The one transcript row that produced all three.

    It was reported as context beside the findings when this pass
    landed. It is a finding now - the hybrid-alignment report's point
    was that forced alignment already discovers these and nothing reads
    them, and a note nobody has to act on is not a reading.
    """
    rows = hearing.unfitted_transcript_rows
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["text_words"] == 12
    assert row["untimed_words"] == 12
    assert row["kind"] == "whole_row_lost"
    assert row["span_seconds"] == pytest.approx(0.92, abs=0.01)
    assert "make sure that you're coming up" in row["text"]

    finding = next(f for f in hearing.findings
                   if f.metric == reel_hearing.FIT_METRIC)
    assert finding.passed is False
    assert finding.severity == "error"


def test_the_cause_is_owned_by_the_step_that_wrote_the_transcript(hearing):
    """Not by the one that placed the cards.

    Every other finding this pass makes is owned by `build_reels` or
    `plan_subtitles`, which are downstream of the defect and cannot fix
    it. Routing this one there would send a reader to the wrong place.
    """
    read = reel_hearing.read_findings(hearing)
    fit = next(f for f in read.reportable
               if f.metric == reel_hearing.FIT_METRIC)
    assert fit.owner == "temporal_index"


def test_the_whole_episode_is_counted_from_the_one_reel(hearing):
    """The population is the transcript's, not this reel's.

    Fifteen rows exist on the captain's episode whether or not any reel
    plays one; the fixture transcript is trimmed to this reel's spans,
    so it carries the one. What is pinned is that the count is REPORTED
    rather than left to be discovered one delivered reel at a time.
    """
    episode = hearing.transcript_fit
    assert episode["rows"] > 0
    assert episode["unfitted_rows"] >= len(hearing.unfitted_transcript_rows)
    assert "rows_detail" not in episode
    assert any("whole transcript carries" in line
               for line in reel_hearing.summary_lines(hearing))


# ── 2. The normalisation that stops it crying wolf ───────────────────

def test_the_filed_spelling_correction_is_applied_to_the_heard_side(hearing):
    """Without it, every reel on this project reports a false Lucie->Lucy.

    The planned side already has it (`transcript_corrections.
    apply_to_document` rewrote the transcript); the heard side does not,
    and diffing them raw is the tool's first real run reporting a defect
    that is not there.
    """
    assert [c["heard"] for c in hearing.corrections_applied] == ["lucy"]
    assert [c["correct"] for c in hearing.corrections_applied] == ["Lucie"]
    substitutions = [row for row in hearing.divergences
                     if row["kind"] == reel_hearing.SUBSTITUTED]
    assert substitutions == [], substitutions
    assert "Lucie" in hearing.as_dict()["heard_script"]


def test_without_the_correction_the_false_divergence_comes_back(
        timeline, transcript, spoken, tmp_path):
    """The same reel through a project with nothing on file."""
    bare = tmp_path / "no_corrections"
    bare.mkdir()
    unnormalised = reel_hearing.hear(timeline, transcript, spoken,
                                     project_folder=str(bare),
                                     video_path=str(bare / "reel26.mp4"))
    substitutions = [row for row in unnormalised.divergences
                     if row["kind"] == reel_hearing.SUBSTITUTED]
    assert [(row["planned"], row["heard"]) for row in substitutions] == \
        [("Lucie", "Lucy")]


# ── 3. It reports, and it gates nothing ──────────────────────────────

def test_it_gates_nothing(hearing):
    assert reel_hearing.GATES is False
    assert hearing.as_dict()["gates"] is False


def test_no_gate_reads_the_hearing_record():
    """The record is written and nothing in the pipeline opens it.

    A new gate that blocks builds is the hard-to-reverse direction; the
    order is to prove this on real episodes and then ask the captain to
    promote it. This test is what has to change when he does.
    """
    root = Path(__file__).resolve().parents[1]
    # The producer chain: the module that writes the record, the skill
    # that calls it, and the CLI verb. Anything else naming the record
    # is a READER, and a reader is one step from a gate.
    PRODUCERS = {"library/tools/reel_hearing.py",
                 "library/skills/hear_the_reel/skill.py",
                 "library/skills/hear_the_reel/SKILL.md",
                 "manage_project.py"}
    readers = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        relative = str(path.relative_to(root))
        if relative in PRODUCERS:
            continue
        if reel_hearing.RECORD_SUFFIX in path.read_text(encoding="utf-8"):
            readers.append(relative)
    assert readers == [], (
        f"{readers} name the hearing record. Nothing may READ it into a "
        f"gate until the captain promotes these checks - see "
        f"reel_hearing.GATES.")
    # And no step declares it as an input, which is the other way in.
    for manifest in sorted((root / "library" / "steps").rglob("manifest.json")):
        body = manifest.read_text(encoding="utf-8")
        assert "hearing" not in body, manifest


def test_every_finding_carries_an_honest_verdict(hearing):
    """`passed` is the check's own verdict, not a constant true.

    Gating nothing is a property of who reads the record; a row that
    says clean when it measured a defect is the gate-that-cannot-fail
    turned inside out (AGENTS.md 10.4).
    """
    by_metric = {f.metric: f for f in hearing.findings}
    assert by_metric[reel_hearing.DRIFT_METRIC].passed is False
    assert by_metric[reel_hearing.COVERAGE_METRIC].passed is False
    assert by_metric[reel_hearing.SCRIPT_METRIC].passed is False
    assert set(by_metric) == set(reel_hearing.METRICS)


def test_the_findings_go_through_the_one_reader(hearing):
    """No private opinion about which measurements matter (AGENTS.md 10.4)."""
    read = reel_hearing.read_findings(hearing)
    assert read.unrouted == [], [f.metric for f in read.unrouted]
    assert {f.owner for f in read.findings} == {"build_reels",
                                                "plan_subtitles",
                                                "temporal_index"}
    # Four checks, four failures: the three consequences in the
    # delivered file and the transcript row that caused them.
    assert read.counts()[qa_findings.FAILING] == 4


def test_the_summary_says_it_does_not_gate(hearing):
    printed = "\n".join(reel_hearing.summary_lines(hearing))
    assert "this is a report, not a gate" in printed
    assert "NO caption on screen" in printed


# ── 4. What it refuses rather than passing ───────────────────────────

def test_a_timeline_with_no_transcribed_speech_refuses(timeline, spoken,
                                                       project):
    """An unheard reel is never a clean one."""
    with pytest.raises(reel_hearing.NothingWasHeard) as refused:
        reel_hearing.hear(timeline, {"segments": []}, spoken,
                          project_folder=str(project))
    assert "nothing to hear this render against" in str(refused.value)


def test_a_timeline_with_no_frame_rate_refuses(timeline, transcript, spoken):
    """Every reel-time number is that division; a guessed rate scales
    every finding silently."""
    timeline["metadata"]["fps"] = 0
    with pytest.raises(ValueError) as refused:
        reel_hearing.hear(timeline, transcript, spoken)
    assert "no frame rate" in str(refused.value)


def test_caption_coverage_is_skipped_openly_when_there_are_no_captions(
        timeline, transcript, spoken, project):
    """A reel that declares no captions is not a reel with late ones."""
    timeline["tracks"] = [t for t in timeline["tracks"]
                          if t["name"] != "Subtitles"]
    quiet = reel_hearing.hear(timeline, transcript, spoken,
                             project_folder=str(project))
    assert quiet.coverage["measured"] is False
    assert [row["check"] for row in quiet.skipped] == \
        [reel_hearing.COVERAGE_METRIC]
    assert reel_hearing.COVERAGE_METRIC not in {f.metric
                                                for f in quiet.findings}


# ── 5. The parts, separately ─────────────────────────────────────────

def test_speech_spans_are_measured_not_read_off_a_track_name(timeline,
                                                             transcript):
    """Music and SFX sit on audio tracks too.

    A file the transcriber never heard speech in cannot be the speech
    row, so adding one changes nothing.
    """
    before = reel_hearing.speech_spans(timeline, transcript)
    timeline["tracks"].append({
        "type": "audio", "index": 99, "name": "Music",
        "clips": [{"record_in": 0, "record_out": 1000, "source_in": 0,
                   "file_path": "/music/a_bed.wav", "name": "a_bed.wav"}]})
    assert reel_hearing.speech_spans(timeline, transcript) == before


def test_a_caption_card_is_identified_by_its_NAME_not_its_track(timeline):
    """A card on the wrong row is still a card the viewer reads."""
    windows = reel_hearing.caption_windows(timeline)
    assert len(windows) == 13
    for track in timeline["tracks"]:
        if track["name"] == "Subtitles":
            track["name"] = "Somewhere Else"
    assert reel_hearing.caption_windows(timeline) == windows


def test_normalise_folds_spelling_and_nothing_more():
    assert reel_hearing.normalise("You're,") == reel_hearing.normalise("you’re")
    assert reel_hearing.normalise("Lucie.") == "lucie"
    # NOT folded - both are reported, on purpose.
    assert reel_hearing.normalise("gonna") != reel_hearing.normalise("going")
    assert reel_hearing.normalise("fifty") != reel_hearing.normalise("50")


def test_align_is_an_edit_script():
    ops = reel_hearing.align(["a", "b", "c"], ["a", "x", "c", "d"])
    kinds = [kind for kind, _, _ in ops]
    assert kinds.count(reel_hearing.EQUAL) == 2
    assert reel_hearing.SUBSTITUTED in kinds
    assert reel_hearing.EXTRA in kinds


def test_a_drift_run_ends_at_a_sign_change():
    """Late then early is two things happening, not one."""
    words = [reel_hearing.Word(word=str(i), token=str(i), start=float(i),
                               end=float(i) + 0.1) for i in range(8)]
    pairs = [(i, i, +0.5 if i < 4 else -0.5) for i in range(8)]
    runs = reel_hearing.drift_runs(pairs, words, words)
    assert [r["words"] for r in runs] == [4, 4]
    assert [r["direction"] for r in runs] == ["late", "early"]


def test_an_isolated_late_word_is_not_a_run():
    words = [reel_hearing.Word(word=str(i), token=str(i), start=float(i),
                               end=float(i) + 0.1) for i in range(5)]
    pairs = [(i, i, 0.9 if i == 2 else 0.01) for i in range(5)]
    assert reel_hearing.drift_runs(pairs, words, words) == []


def test_covered_fraction_counts_the_union_of_cards():
    assert reel_hearing.covered_fraction(0.0, 1.0, [(0.0, 0.5)]) == 0.5
    assert reel_hearing.covered_fraction(0.0, 1.0, []) == 0.0
    assert reel_hearing.covered_fraction(0.0, 1.0,
                                         [(0.0, 0.6), (0.4, 1.0)]) == 1.0


def test_the_hearing_id_is_stable_for_one_unchanged_render(hearing, tmp_path):
    """So hearing the same render twice does not tell the captain twice."""
    render = tmp_path / "a_render.mp4"
    render.write_bytes(b"not really an mp4")
    hearing.video_path = str(render)
    first = reel_hearing.hearing_id(hearing)
    assert first == reel_hearing.hearing_id(hearing)
    render.write_bytes(b"a different render entirely")
    assert reel_hearing.hearing_id(hearing) != first


def test_the_record_lands_beside_the_render(hearing, tmp_path):
    hearing.video_path = str(tmp_path / "exports" / "Reel 26.mp4")
    path = reel_hearing.write_record(hearing)
    assert path.endswith("Reel 26" + reel_hearing.RECORD_SUFFIX)
    written = json.loads(Path(path).read_text(encoding="utf-8"))
    assert written["gates"] is False
    assert written["planned_word_count"] == 140
    assert written["heard_script"] and written["planned_script"]


# ── 6. Announcing a finding ──────────────────────────────────────────

def _declare_a_steer(project, text):
    """The project arms one hook on the condition hooks.py declares."""
    from library.tools import hooks

    (project / hooks.DECLARATION_FILENAME).write_text(json.dumps({"hooks": [{
        "name": "hearing_to_the_channel",
        "describe": "A reel that does not say what the plan says reaches "
                    "the captain's review channel.",
        "when": "qa_finding_raised",
        "action": {"kind": "steer", "text": text,
                   "anchor_step": "build_reels"},
    }]}), encoding="utf-8")


def test_announcing_fires_nothing_when_the_project_declares_no_hook(
        hearing, project):
    """Report-only by construction: a finding reaches the review channel
    only where the captain armed a hook for it."""
    assert reel_hearing.announce(str(project), hearing) == []


def test_announcing_reaches_the_review_channel_through_the_declared_hook(
        hearing, project, monkeypatch):
    """`qa_finding_raised` is the condition hooks.py already declares."""
    from library.dashboard import review_channel
    from library.tools import hooks

    (project / "pipeline_output" / "review").mkdir(parents=True)
    _declare_a_steer(project, "the render does not say what the plan says")
    monkeypatch.delenv(hooks.ENV_DEPTH, raising=False)

    failing = [f for f in hearing.findings if not f.passed]
    fired = reel_hearing.announce(str(project), hearing)
    assert [f.outcome for f in fired] == [hooks.FIRED] * len(failing), fired
    notes = review_channel.list_notes(str(project))
    assert len(notes) == len(failing) == 4
    assert {note["origin"] for note in notes} == {review_channel.ORIGIN_HOOK}
    assert all(note["anchor"]["selector"] for note in notes)


def test_hearing_the_same_render_twice_does_not_announce_twice(
        hearing, project, monkeypatch):
    from library.tools import hooks

    (project / "pipeline_output" / "review").mkdir(parents=True)
    _declare_a_steer(project, "diverged")
    monkeypatch.delenv(hooks.ENV_DEPTH, raising=False)
    render = project / "reel26.mp4"
    render.parent.mkdir(parents=True, exist_ok=True)
    render.write_bytes(b"a render")
    hearing.video_path = str(render)

    reel_hearing.announce(str(project), hearing)
    again = reel_hearing.announce(str(project), hearing)
    assert {f.outcome for f in again} == {hooks.ALREADY_FIRED}, again


# ── 7. The fixture is what it claims to be ───────────────────────────

def test_the_fixture_carries_no_real_project_path():
    """The captain's directory layout does not belong in the repository.

    The prefixes come from the guard that enforces the same rule on test
    SOURCE (`tests/test_tests_never_reach_real_projects.py`), so this
    holds the fixtures to that one enumeration rather than a second
    spelling of it - which is also why this file cannot write them out.
    """
    from tests.test_tests_never_reach_real_projects import (
        HOME_ABSOLUTE_PREFIXES,
    )

    for name in ("reel26.timeline.json", "reel26.transcript.json",
                 "reel26.heard.json"):
        body = (FIXTURES / name).read_text(encoding="utf-8")
        for prefix in HOME_ABSOLUTE_PREFIXES:
            assert prefix not in body, f"{name} carries {prefix}"


def test_the_fixture_copies_into_a_tmp_project(tmp_path):
    """No test reaches a real project (AGENTS.md section 8)."""
    destination = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, destination)
    assert (destination / "reel26.timeline.json").exists()
