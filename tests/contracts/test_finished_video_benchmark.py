"""QA-010: the finished-video acceptance benchmark.

The benchmark's job is to measure a DELIVERED FILE against the brief it
was made for and against the engine's own gates, on two denominators
that are never combined.  Each test here names the defect it would
catch:

- a corpus that breaks its contract being read anyway;
- the two halves being averaged into one number;
- an unmeasured half reading as a zero;
- a technical failure hidden behind an acceptable creative score;
- a release gate that says "met" over a corpus that is not
  representative;
- a tracked number counted over the corpus instead of over the videos
  that recorded it;
- a reviewer's reading counted when the reviewer saw the plan;
- the acceptance gate forgetting how to answer for a delivered file
  alone.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import acceptance_state
from library.tools import finished_video_benchmark as benchmark

# ── helpers ───────────────────────────────────────────────────────────────

def _brief(criterion_id: str = "the-hook") -> dict:
    return {
        "id": "a-brief",
        "instruction": "Open on the moment she says what it costs.",
        "project_state": {"footage_profile": "two_camera_interview",
                          "existing_adjustments": [],
                          "media_conditions": []},
        "acceptance_criteria": [
            {"id": criterion_id,
             "statement": "The reel's first line is the claim."}],
    }


def _corpus(path: str, **extra) -> dict:
    entry = {
        "video_id": "reel-01",
        "brief": _brief(),
        "path": str(path),
        "reviewer": "a reviewer who did not build the reel",
        "saw_the_plan": False,
        "judgments": [{
            "id": "the-hook",
            "status": "pass",
            "rationale": "the reel opens on the claim.",
            "evidence": [{"artifact": str(path),
                          "observation": "the first words of the file"}],
        }],
    }
    entry.update(extra)
    return {"schema": benchmark.CORPUS_SCHEMA,
            "corpus_id": "a-corpus",
            "videos": [entry]}


def _delivered_file(tmp_path: Path) -> Path:
    """A real, tiny delivered file, made here rather than found."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH")
    target = tmp_path / "delivered.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=blue:s=360x640:r=25:d=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(target)],
        check=True, encoding="utf-8")
    return target


# ── the corpus ────────────────────────────────────────────────────────────

def test_a_corpus_that_breaks_its_contract_is_refused() -> None:
    """A corpus that says nothing about its videos is not a corpus."""
    problems = benchmark.validate_corpus({"schema": "something-else"})
    assert any("schema" in problem for problem in problems)
    assert any("no videos" in problem for problem in problems)

    duplicated = {"schema": benchmark.CORPUS_SCHEMA, "videos": [
        {"video_id": "same", "brief": _brief(), "path": "/tmp/a.mp4"},
        {"video_id": "same", "brief": _brief(), "path": "/tmp/b.mp4"}]}
    assert any("share the id" in problem
               for problem in benchmark.validate_corpus(duplicated))

    with pytest.raises(benchmark.CorpusError):
        benchmark.load_corpus(_write(Path("/tmp"), "broken.json",
                                     {"schema": benchmark.CORPUS_SCHEMA,
                                      "videos": []}))


def test_a_brief_that_carries_the_answer_key_is_refused() -> None:
    """A reviewer who can see the expected operations is not reading."""
    leaky = _brief()
    leaky["oracle"] = {"expected_operations": ["reel.opening"]}
    problems = benchmark.validate_corpus({
        "schema": benchmark.CORPUS_SCHEMA,
        "videos": [{"video_id": "reel-01", "brief": leaky,
                    "path": "/tmp/a.mp4"}]})
    assert any("answer key" in problem for problem in problems)


def test_a_history_that_records_nonsense_is_refused() -> None:
    """A revision count of zero attempts is not a count."""
    problems = benchmark.validate_corpus({
        "schema": benchmark.CORPUS_SCHEMA,
        "videos": [{
            "video_id": "reel-01",
            "brief": _brief(),
            "path": "/tmp/a.mp4",
            "history": {
                "attempts": 0,
                "human_correction_seconds": -5,
                "preserved_work": {"checks": {"grade": "yes"}},
            },
        }],
    })
    assert any("attempts" in problem for problem in problems)
    assert any("human_correction_seconds" in problem
               for problem in problems)
    assert any("preserved-work check" in problem for problem in problems)


# ── the two halves ────────────────────────────────────────────────────────

def test_the_two_halves_are_never_combined(tmp_path: Path) -> None:
    """One number over two units is the defect this benchmark exists for."""
    delivered = _delivered_file(tmp_path)
    corpus = _corpus(delivered)
    report = benchmark.run_benchmark(corpus, align_transcript=False)

    assert report["the_halves_are_not_combined"] is True
    assert "technical" in report and "creative" in report
    # No key anywhere in the report may hold a number that mixes the
    # two halves' denominators, and a bare "score" may only live
    # inside one half.
    banned = {"combined_score", "overall_score", "total_score",
              "quality_score", "final_score", "acceptance_score"}
    def _walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in banned, (
                    f"{path}.{key} reads the two halves as one number")
                if key == "score":
                    assert ".creative" in f"{path}.{key}", (
                        f"{path}.{key} is a score outside a half")
                _walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                _walk(value, f"{path}[{index}]")
    _walk(report)


def test_a_technical_failure_is_not_hidden_by_an_acceptable_score(
        tmp_path: Path) -> None:
    """The punch list's sentence, as a rule the code keeps."""
    delivered = _delivered_file(tmp_path)
    corpus = _corpus(delivered)
    report = benchmark.run_benchmark(corpus, align_transcript=False)
    video = report["videos"][0]

    failed = [leg for leg in video["technical"]["legs"]
              if leg["status"] == "fail"]
    if failed:
        assert video["creative"]["status"] in ("pass", "fail",
                                               "not_measured")
        if video["creative"]["status"] == "pass":
            assert video["technical_failure_hidden"] is True
            assert video["outcome"] != acceptance_state.ACCEPTED
            gate = benchmark.release_gate(report["videos"])
            hidden = gate["conditions"][-1]
            assert hidden["met"] is False
    else:
        assert video["technical_failure_hidden"] is False


def test_an_unmeasured_half_is_never_a_zero(tmp_path: Path) -> None:
    """Nobody judged the video, so its creative half is not a zero."""
    delivered = _delivered_file(tmp_path)
    corpus = _corpus(delivered)
    corpus["videos"][0].pop("judgments")
    report = benchmark.run_benchmark(corpus, align_transcript=False)

    creative = report["videos"][0]["creative"]
    assert creative["status"] == "not_measured"
    assert creative["score"] is None
    assert creative["reason"]

    tracked = report["tracked"]
    for name in ("first_pass_acceptance", "revision_count",
                 "human_correction_seconds", "preserved_work"):
        row = tracked[name]
        assert row["status"] == "not_measured", name
        assert row["value"] is None, name


def test_a_reviewer_who_saw_the_plan_is_not_independent(
        tmp_path: Path) -> None:
    """The punch list asks for an INDEPENDENT assessment."""
    delivered = _delivered_file(tmp_path)
    corpus = _corpus(delivered, saw_the_plan=True)
    report = benchmark.run_benchmark(corpus, align_transcript=False)

    video = report["videos"][0]
    assert video["independence"]["independent"] is False
    assert video["creative"]["status"] == "not_measured"
    gate = benchmark.release_gate(report["videos"])
    reviewed = next(condition for condition in gate["conditions"]
                    if condition["id"] == "independently_reviewed")
    assert reviewed["met"] is False


# ── the gate ──────────────────────────────────────────────────────────────

def test_the_release_gate_refuses_a_corpus_that_is_not_representative(
        tmp_path: Path) -> None:
    """One finished video is a task, not a set."""
    delivered = _delivered_file(tmp_path)
    corpus = _corpus(delivered)
    report = benchmark.run_benchmark(corpus, align_transcript=False)

    gate = report["release_gate"]
    assert gate["met"] is False
    representative = next(condition for condition in gate["conditions"]
                          if condition["id"] == "representative_set")
    assert representative["met"] is False
    assert representative["required"] == benchmark.MIN_REPRESENTATIVE_VIDEOS
    assert representative["count"] == 1


def test_a_delivered_file_that_cannot_be_read_is_reported_not_dropped(
        tmp_path: Path) -> None:
    """A delivery nobody can open is a finding, not an absence."""
    missing = tmp_path / "not-there.mp4"
    corpus = _corpus(missing)
    report = benchmark.run_benchmark(corpus, align_transcript=False)

    video = report["videos"][0]
    assert video["delivery_status"] == benchmark.MISSING
    assert video["technical"]["status"] == "not_measured"
    assert video["creative"]["status"] == "not_measured"
    gate = benchmark.release_gate(report["videos"])
    delivered_condition = next(condition for condition in gate["conditions"]
                                if condition["id"] == "every_video_delivered")
    assert delivered_condition["met"] is False


# ── the tracked numbers ───────────────────────────────────────────────────

def test_the_first_pass_acceptance_is_counted_over_what_recorded_it(
        ) -> None:
    """The denominator is the videos that recorded it, not the corpus."""
    videos = [
        {"video_id": "a", "history": {"first_attempt_accepted": True}},
        {"video_id": "b", "history": {"first_attempt_accepted": False}},
        {"video_id": "c", "history": {}},
    ]
    row = benchmark._first_pass_acceptance(videos)
    assert row["denominator"] == 2
    assert row["passed"] == 1
    assert row["value"] == 0.5
    assert row["videos_without_a_record"] == ["c"]


def test_the_preserved_work_counts_only_the_checks_that_were_recorded(
        ) -> None:
    """A check nobody recorded is not a check that failed."""
    videos = [
        {"video_id": "a", "history": {"preserved_work": {
            "checks": {"grade": True, "linked_audio": True}}}},
        {"video_id": "b", "history": {"preserved_work": {
            "checks": {"grade": False}}}},
        {"video_id": "c", "history": {}},
    ]
    row = benchmark._preserved_work(videos)
    assert row["denominator"] == 3
    assert row["passed"] == 2
    assert row["checks"]["grade"]["denominator"] == 2
    assert row["checks"]["grade"]["passed"] == 1


def test_the_revision_count_is_not_measured_when_nothing_recorded_it(
        ) -> None:
    """Nobody counted the revisions, so the number is not zero."""
    row = benchmark._revision_count([{"video_id": "a", "history": {}}])
    assert row["status"] == "not_measured"
    assert row["value"] is None
    assert row["total_revisions"] is None


# ── the acceptance gate, narrowed ─────────────────────────────────────────

def test_the_acceptance_gate_can_be_narrowed_to_a_delivered_file() -> None:
    """A finished video is judged by the legs a file can answer for."""
    facts = {"has_delivered_file": True, "has_captions": None,
             "requires_approval": False}
    results = {
        "delivered_audio": {"status": acceptance_state.PASSED,
                            "observation": "the audio spec passed"},
        "delivered_picture": {"status": acceptance_state.PASSED,
                              "observation": "the picture spec passed"},
        "editorial_review": {"status": acceptance_state.PASSED,
                             "observation": "the brief's criteria passed"},
    }
    evaluated = acceptance_state.evaluate(
        facts, results, legs=benchmark.FINISHED_VIDEO_LEGS)

    assert evaluated["outcome"] == acceptance_state.OUTCOME_UNMEASURED
    captions = next(leg for leg in evaluated["legs"]
                    if leg["id"] == "delivered_captions")
    assert captions["status"] == acceptance_state.UNMEASURED
    # The legs about the WRITES are not in a finished video's gate.
    assert all(leg["id"] in benchmark.FINISHED_VIDEO_LEGS
               for leg in evaluated["legs"])


def test_the_gate_refuses_a_leg_it_does_not_know() -> None:
    """A leg that is not a leg is not silently ignored."""
    with pytest.raises(acceptance_state.AcceptanceError):
        acceptance_state.evaluate({}, {}, legs=("no_such_leg",))


# ── the table ─────────────────────────────────────────────────────────────

def test_the_baseline_table_keeps_the_halves_in_separate_columns(
        tmp_path: Path) -> None:
    """A column that held their average would be the hiding place."""
    delivered = _delivered_file(tmp_path)
    corpus = _corpus(delivered)
    corpus["videos"][0]["history"] = {"attempts": 2,
                                       "first_attempt_accepted": False}
    report = benchmark.run_benchmark(corpus, align_transcript=False)

    rows = benchmark.baseline_table(report)
    assert len(rows) == 1
    row = rows[0]
    assert "technical_legs_failed" in row
    assert "creative_criteria_passed" in row
    assert row["revisions"] == 1
    markdown = benchmark.table_markdown(report)
    assert "tech legs failed" in markdown
    assert "creative passed" in markdown


def _write(directory: Path, name: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target
