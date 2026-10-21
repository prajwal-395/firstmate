"""The vision pass is not killed for being quiet.

The defect this closes, measured on a Ren-owned scratch copy
(scout report 2026-10-20, FIX 7):

* The semantic step watched its vision child for signs of life and read
  SILENCE as a wedge: no log line and no new profile for 600 s, so the
  child was killed and the clip was published as
  `"status": "missing"`. Two clips, 600 s each, of nothing.
* The vision pass is silent through its long phases - frame extraction
  (990 frames, 467 s measured), the model load, and a coarse sweep of
  hundreds of batches - so the rule that reads silence as a wedge kills
  exactly the footage that takes longest to measure.
* The child now reports what it is doing and how many units of work it
  has finished (`library/tools/vision_progress.py`), and the parent reads
  THAT. A child that is working is not killed however long it is quiet;
  a child that stops moving is still killed, and the reason names the
  unit it was inside.

These tests are the scout's proof in miniature: the record the semantic
stream publishes for a clip that took a long, quiet measurement is
`"complete"`, not `"missing"`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

# A child that does silent work: it reports finished units through the
# progress channel and says NOTHING on stdout. The work outlasts the
# stall bound, which is the whole point - a child that is quiet for
# 600 s while it works is not a wedged child.
QUIET_WORKING_CHILD = """
import sys, time
sys.path.insert(0, {repo!r})
from library.tools import vision_progress
progress = vision_progress.VisionProgress({progress_file!r})
for index in range(60):
    token = progress.unit_begin("model_call:batch:%d" % index)
    time.sleep(0.02)
    progress.unit_done(token)
progress.close()
with open({profile!r}, "w", encoding="utf-8") as handle:
    handle.write("{{}}")
"""

# A child that declares one unit of work and never finishes it: the
# wedge the phase bound exists to catch.
WEDGED_CHILD = """
import sys, time
sys.path.insert(0, {repo!r})
from library.tools import vision_progress
progress = vision_progress.VisionProgress({progress_file!r})
progress.unit_begin("model_call:the_one_that_never_returns")
time.sleep(120)
"""


def _child(script, **fields):
    return [sys.executable, "-c", script.format(**fields)]


def test_a_quiet_child_that_is_working_is_not_killed(tmp_path, monkeypatch):
    """The defect: the child is silent for longer than the stall bound
    and is doing real work. It used to be killed, and its clip was
    published as a missing measurement."""
    from library.capabilities.semantics import analyse as sem_step

    monkeypatch.setattr(sem_step, "VISION_STALL_TIMEOUT_S", 0.4)
    monkeypatch.setattr(sem_step, "CLIP_ANALYSIS_TIMEOUT_S", 60)
    progress_file = tmp_path / "vision_progress.json"
    profile = tmp_path / "clip_profile_A_v3.json"

    sem_step._run_clip_vision(
        _child(QUIET_WORKING_CHILD, repo=str(REPO),
               progress_file=str(progress_file), profile=str(profile)),
        progress=lambda: 0, poll_s=0.05, progress_file=str(progress_file))

    assert profile.exists(), "the quiet child was killed mid-measurement"

    # The same child, with no channel to report through: the old rule
    # reads its silence as a wedge and kills it. That difference IS the
    # defect.
    try:
        sem_step._run_clip_vision(
            _child(QUIET_WORKING_CHILD, repo=str(REPO),
                   progress_file=str(tmp_path / "unused.json"),
                   profile=str(tmp_path / "never_written.json")),
            progress=lambda: 0, poll_s=0.05)
    except sem_step.VisionStalled:
        pass
    else:
        raise AssertionError(
            "a child with no progress channel is expected to be killed "
            "for its silence - the test above is what the fix changed")


def test_a_child_that_never_finishes_its_unit_is_killed_and_named(
        tmp_path, monkeypatch):
    """The wedge-break survives the fix, and the reason names the unit
    the child was inside instead of reporting plain silence."""
    from library.capabilities.semantics import analyse as sem_step

    monkeypatch.setattr(sem_step, "VISION_STALL_TIMEOUT_S", 30)
    monkeypatch.setattr(sem_step, "CLIP_ANALYSIS_TIMEOUT_S", 60)
    monkeypatch.setattr(sem_step, "VISION_PHASE_TIMEOUT_S", 0.4)
    progress_file = tmp_path / "vision_progress.json"

    try:
        sem_step._run_clip_vision(
            _child(WEDGED_CHILD, repo=str(REPO),
                   progress_file=str(progress_file)),
            progress=lambda: 0, poll_s=0.05,
            progress_file=str(progress_file))
    except sem_step.VisionStalled as exc:
        assert "the_one_that_never_returns" in str(exc), str(exc)
    else:
        raise AssertionError("a wedged child was left running")


def test_a_child_that_says_nothing_at_all_is_still_killed(tmp_path,
                                                          monkeypatch):
    """A child too old to write the progress channel is still caught by
    the silence bound: the fix narrows the wedge-break, it does not
    remove it."""
    from library.capabilities.semantics import analyse as sem_step

    monkeypatch.setattr(sem_step, "VISION_STALL_TIMEOUT_S", 0.4)
    monkeypatch.setattr(sem_step, "CLIP_ANALYSIS_TIMEOUT_S", 60)

    try:
        sem_step._run_clip_vision(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            progress=lambda: 0, poll_s=0.05)
    except sem_step.VisionStalled as exc:
        assert "no log or completed profile" in str(exc), str(exc)
    else:
        raise AssertionError("a silent child was left running")


def test_a_long_measurement_is_published_as_complete_not_missing(
        tmp_path, monkeypatch):
    """The scout's proof: the record the semantic stream publishes for a
    clip whose measurement took a long, quiet while is `"complete"`."""
    from library.capabilities.semantics import analyse as sem_step
    from library.tools.project_layout import Area, ProjectLayout

    clip = tmp_path / "A.MOV"
    clip.write_bytes(b"fixture-bytes")
    layout = ProjectLayout(str(tmp_path))
    analysis_dir = str(layout.write_dir(
        Area.VISION_ANALYSIS, step="semantic_analysis"))

    def quiet_working_child(cmd, *args, **kwargs):
        """A child that measures for a long time, quietly."""
        from library.tools import vision_progress
        progress = vision_progress.VisionProgress(
            kwargs.get("progress_file") or "")
        paths = cmd[cmd.index("--clip") + 1:]
        for path in paths:
            stem = os.path.splitext(os.path.basename(path))[0]
            for index in range(3):
                token = progress.unit_begin(f"model_call:{index}")
                progress.unit_done(token)
            with open(os.path.join(analysis_dir,
                                   f"clip_profile_{stem}_v3.json"),
                      "w", encoding="utf-8") as handle:
                json.dump({"file_path": os.path.realpath(path),
                           "assessment": {"content_type": "interview"}},
                          handle)
        return 0

    monkeypatch.setattr(sem_step, "_run_clip_vision", quiet_working_child)
    sem_step.analyse_semantics(
        raw_footage_files=[str(clip)],
        project_folder=str(tmp_path),
        clip_catalog=[{"clip_id": "clip_001", "path": str(clip)}],
        stream_run_id="run-test",
    )

    record = json.loads(
        (Path(analysis_dir) / "temporal_profile_stream_v1" /
         "clip_001.json").read_text(encoding="utf-8"))
    assert record["status"] == "complete", record


def test_the_progress_record_counts_finished_work(tmp_path):
    """The channel's own contract: the counter counts FINISHED units, and
    a unit in flight is visible as such."""
    from library.tools import vision_progress

    progress = vision_progress.VisionProgress(
        tmp_path / "vision_progress.json")
    token = progress.unit_begin("model_call:1")
    record = vision_progress.read_progress(progress.path)
    assert vision_progress.units_of(record) == 0
    assert vision_progress.inflight_of(record)[0]["label"] == "model_call:1"

    progress.unit_done(token)
    record = vision_progress.read_progress(progress.path)
    assert vision_progress.units_of(record) == 1
    assert vision_progress.inflight_of(record) == []

    # A unit that is finished twice is counted once: the counter is a
    # count of work, not a count of calls.
    progress.unit_done(token)
    record = vision_progress.read_progress(progress.path)
    assert vision_progress.units_of(record) == 1


def test_a_record_that_is_not_there_reads_as_no_progress(tmp_path):
    """A child that never wrote a record reads as silence, not as a
    crash: the parent falls back to the rule that applies to it."""
    from library.tools import vision_progress

    assert vision_progress.read_progress(tmp_path / "nothing.json") is None
    assert vision_progress.units_of(None) == 0
    assert vision_progress.inflight_of(None) == []


def test_a_batch_refusal_is_reported_for_every_clip_at_once(tmp_path):
    """The defect: the child waited for the model's memory, was killed,
    and the parent started a FRESH child for the next clip - so a
    machine that cannot host the model cost one wait PER CLIP (600 s x
    2 clips of nothing).

    A refusal is a refusal of the whole batch: every clip needs the same
    model. It is reported once, for every clip, and no second child is
    started.
    """
    from library.capabilities.semantics import analyse as sem_step
    from library.tools import vision_progress

    progress_file = tmp_path / "vision_progress.json"
    started = []
    failures = []

    def refusing_child(cmd, *args, **kwargs):
        started.append(cmd)
        progress = vision_progress.VisionProgress(
            kwargs.get("progress_file") or str(progress_file))
        progress.refuse("the vision model could not be reserved: this "
                        "machine did not free the memory the weights need",
                        scope="batch")
        raise subprocess.CalledProcessError(1, cmd)

    sem_step._analyse_missing(
        ["/raw/A.mov", "/raw/B.mov", "/raw/C.mov"], ["vision"],
        str(tmp_path), run=refusing_child,
        on_clip_failure=lambda path, reason: failures.append((path, reason)),
        progress_file=str(progress_file))

    assert len(started) == 1, "a second child was started for the same refusal"
    assert [path for path, _ in failures] == [
        "/raw/A.mov", "/raw/B.mov", "/raw/C.mov"]
    assert all("could not be reserved" in reason for _, reason in failures)


def test_the_semantic_step_passes_the_channel_to_the_child(tmp_path,
                                                           monkeypatch):
    """The parent and the child must agree on where the record lives,
    or the parent watches a file nobody writes."""
    from library.capabilities.semantics import analyse as sem_step
    from library.tools import vision_progress

    clip = tmp_path / "A.MOV"
    clip.write_bytes(b"fixture-bytes")
    seen = {}

    def capture(cmd, *args, **kwargs):
        seen["cmd"] = cmd
        seen["progress_file"] = kwargs.get("progress_file")
        return 0

    monkeypatch.setattr(sem_step, "_run_clip_vision", capture)
    sem_step.analyse_semantics(
        raw_footage_files=[str(clip)], project_folder=str(tmp_path))

    assert seen["progress_file"], "the child was given no channel"
    assert "--progress-file" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--progress-file") + 1] == (
        seen["progress_file"])
    assert os.path.basename(seen["progress_file"]) == (
        vision_progress.PROGRESS_FILENAME)
