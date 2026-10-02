"""Step 1.03 must not re-analyse footage it has already analysed.

Both the "already analysed?" check and the analyser's cache key use the
media file's stem (`clip_profile_<stem>_v3.json`). Incident (every clip
re-analysed every run): `docs/evidence/semantic_analysis_step.md`.
"""

import os
import subprocess
import sys

import pytest

from library.steps.step_1_03_semantic_analysis import step as semantic_step
from library.steps.step_1_03_semantic_analysis.step import (
    _analyse_missing,
    _profile_stems,
    _run_clip_vision,
)


def _touch(d, name):
    open(os.path.join(d, name), "w").write("{}")


def test_v3_profiles_are_recognised(tmp_path):
    """The regression. `_v3` is the suffix the analyser actually writes."""
    _touch(tmp_path, "clip_profile_IMG_1806_v3.json")
    _touch(tmp_path, "clip_profile_IMG_1812_v3.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806", "IMG_1812"}
    # Partial video-only profiles do not count, and the empty-id artifact
    # the old rename wrote (`clip_profile_.json`) is ignored.
    _touch(tmp_path, "clip_profile_IMG_1807_video_only.json")
    _touch(tmp_path, "clip_profile_.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806", "IMG_1812"}


def test_clip_vision_chatter_never_reaches_step_stdout(capfd):
    """A per-clip vision child prints progress to its own stdout
    ("Model loaded/bound in ...", window lines). Run uncaptured, that
    chatter inherits the step's stdout and is prepended to the step's
    own JSON, so the runner rejects the whole step as "Step produced
    invalid JSON" after every clip was analysed (rung-0a proof run:
    11 profiles collected, step failed). The helper captures both
    streams and forwards them to stderr, where the runner streams them
    as the step's log - the step's stdout stays empty until its own
    `json.dump`.

    `capfd`, not `capsys`: a child inherits the OS file descriptor,
    bypassing Python-level `sys.stdout` replacement, so only an
    fd-level capture sees the pollution - with `capsys` this test
    would pass against the buggy uncaptured call.
    """
    child = [
        sys.executable, "-c",
        "import sys;"
        " print('  Model loaded/bound in 0.0s');"
        " print('{\"profiles\": 1}');"
        " print('a warning line', file=sys.stderr)",
    ]
    _run_clip_vision(child)
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "Model loaded/bound in 0.0s" in captured.err
    assert '{"profiles": 1}' in captured.err
    assert "a warning line" in captured.err
    # `check` semantics are unchanged: a nonzero exit raises, so the
    # step skips the clip exactly as before.
    with pytest.raises(subprocess.CalledProcessError):
        _run_clip_vision(
            [sys.executable, "-c", "import sys; sys.exit(1)"])


def _fake_vision(analysis_dir, dies_on=()):
    """A stand-in vision child: works its --clip list in order, writes a
    profile per clip, and dies on any clip named in `dies_on`."""
    calls = []

    def run(cmd, progress=None):
        clips = cmd[cmd.index("--clip") + 1:]
        calls.append(clips)
        for clip in clips:
            stem = os.path.splitext(os.path.basename(clip))[0]
            if stem in dies_on:
                raise subprocess.CalledProcessError(1, cmd)
            _touch(analysis_dir, f"clip_profile_{stem}_v3.json")

    return run, calls


def test_missing_clips_share_one_vision_child(tmp_path):
    """The defect: one child per clip loaded Gemma once per clip
    (measured 2026-10-01, three 6s clips: three model loads). All
    missing clips now go to one child, so the model loads once."""
    run, calls = _fake_vision(str(tmp_path))
    clips = [f"/raw/A{i}.MOV" for i in range(3)]
    _analyse_missing(clips, ["vision"], str(tmp_path), run=run)
    assert calls == [clips]
    assert _profile_stems(str(tmp_path)) == {"A0", "A1", "A2"}


def test_a_clip_that_kills_the_child_costs_only_itself(tmp_path):
    """Per-clip children isolated a bad clip; the batch keeps that: the
    clip the child died on is skipped and a fresh child takes the rest."""
    run, calls = _fake_vision(str(tmp_path), dies_on={"A1"})
    clips = [f"/raw/A{i}.MOV" for i in range(4)]
    _analyse_missing(clips, ["vision"], str(tmp_path), run=run)
    assert calls == [clips, clips[2:]]
    assert _profile_stems(str(tmp_path)) == {"A0", "A2", "A3"}


def test_the_wedge_ceiling_restarts_on_each_finished_clip(monkeypatch):
    """One child now runs N clips, so the ceiling must stay per CLIP:
    a child that keeps landing profiles is never killed, one that
    stalls is."""
    monkeypatch.setattr(semantic_step, "CLIP_ANALYSIS_TIMEOUT_S", 0.6)
    ticks = iter(range(1000))
    sleeper = [sys.executable, "-c", "import time; time.sleep(1.5)"]
    _run_clip_vision(sleeper, progress=lambda: next(ticks), poll_s=0.1)
    with pytest.raises(subprocess.TimeoutExpired):
        _run_clip_vision(sleeper, progress=lambda: 0, poll_s=0.1)
