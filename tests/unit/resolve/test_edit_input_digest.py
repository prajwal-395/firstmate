"""Edit-step input digests: declared, stamped, and reported - never skipped.

History: docs/evidence/resolve_test_history.md#test_edit_input_digest.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.processes.edit_video import run_pipeline as runner
from library.tools import edit_input_digest as digest
from library.tools import run_control

PILOT_ROOT = Path(__file__).resolve().parents[3]
STEPS_ROOT = PILOT_ROOT / "library" / "steps"


def _manifest(step_dir_name):
    with open(STEPS_ROOT / step_dir_name / "manifest.json",
              encoding="utf-8") as f:
        return json.load(f)


def _inputs(**overrides):
    base = {
        "timeline_transcript": {
            "measurement": {"segments": [
                {"speaker": "A", "text": "hello", "start": 0.0, "end": 1.0},
                {"speaker": "B", "text": "hi", "start": 1.0, "end": 2.0},
            ]},
        },
        "creative_direction": {"narrative_theme": "craft"},
        "creative_brief": "make three reels",
        "project_context": "context map",
        "brand_constraints": {"palette": ["#000000"]},
    }
    base.update(overrides)
    return base


def _select_reels_output():
    return {
        "turns": [{"speaker": "A", "start": 0.0, "end": 1.0}],
        "reel_candidates": [{"start": 0.0, "end": 45.0}],
        "length_guidance_seconds": [45, 90],
        "picture_holes": [],
        "lead_speaker": "A",
        "answering_speaker": "B",
        "who_leads_was_inferred": "A asks more",
        "reel_selection": {"moments": [], "considered": []},
    }


# ── The declaration ─────────────────────────────────────────────────


def test_select_reels_declares_what_it_reads():
    """19 of geo-podcast's last 20 run_history entries re-ran select_reels.

    The digest narrows the manifest's inputs to what reaches the bridge
    or the prompt. `audio_spine` is deliberately NOT among them: the
    manifest's own input description says nothing reads it - the DAG edge
    that carries it only orders this step after the spine - and hashing
    it would report "changed" on runs the model saw identically.
    """
    spec = digest.digest_spec(
        _manifest("step_3_04_select_reels"), "select_reels")
    assert spec == {
        "inputs": ["timeline_transcript", "creative_direction",
                   "creative_brief", "project_context",
                   "brand_constraints"],
        "context": ["turns", "reel_candidates", "length_guidance_seconds",
                    "picture_holes", "lead_speaker", "answering_speaker",
                    "who_leads_was_inferred"],
        "include_code": True,
    }


# ── The digest ──────────────────────────────────────────────────────

def _spec():
    return digest.digest_spec(
        _manifest("step_3_04_select_reels"), "select_reels")


def _step_dir():
    return STEPS_ROOT / "step_3_04_select_reels"


def test_digest_moves_when_an_upstream_value_moves():
    spec = _spec()
    before = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    changed = _inputs()
    changed["timeline_transcript"]["measurement"]["segments"][0]["text"] = \
        "hello, edited"
    after = digest.compute_digest(
        spec, changed, _select_reels_output(), _step_dir())
    assert before["digest"] != after["digest"]
    assert before["inputs_digest"] != after["inputs_digest"]
    assert before["code_digest"] == after["code_digest"]


def test_digest_moves_when_the_prompt_moves_but_code_identity_would_not():
    """The departure from code_identity, pinned: the handoff prompt is
    executable for a model step, so editing it moves the digest even
    though code_identity (which excludes .md as prose) would not see it."""
    import shutil
    import tempfile

    from library.tools import code_identity
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "step_3_04_select_reels"
        shutil.copytree(_step_dir(), clone,
                        ignore=shutil.ignore_patterns("__pycache__"))
        spec = _spec()
        before = digest.compute_digest(
            spec, _inputs(), _select_reels_output(), clone)
        code_before = code_identity.step_code_hash(str(clone))
        (clone / "handoff.md").write_text(
            (clone / "handoff.md").read_text(encoding="utf-8")
            + "\n\nAlso prefer owls.\n")
        after = digest.compute_digest(
            spec, _inputs(), _select_reels_output(), clone)
        assert code_before == code_identity.step_code_hash(str(clone))
        assert before["digest"] != after["digest"]
        assert before["code_digest"] != after["code_digest"]
        assert before["inputs_digest"] == after["inputs_digest"]


def test_a_model_answer_is_not_an_input():
    """Changing only the model's own answer must NOT move the digest -
    hashing one would report 'changed' on every re-run by construction."""
    spec = _spec()
    before = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    output = _select_reels_output()
    output["reel_selection"] = {"moments": [{"slug": "new-answer"}],
                                "considered": []}
    after = digest.compute_digest(spec, _inputs(), output, _step_dir())
    assert before["digest"] == after["digest"]


# ── The comparison: report, never act ───────────────────────────────


# ── The stamp in the run record ─────────────────────────────────────

def _begin(tmp_path, previous=None):
    project = tmp_path / "proj"
    project.mkdir()
    if previous is not None:
        (project / run_control.RUN_STATUS_FILE).write_text(
            json.dumps(previous), encoding="utf-8")
    run_control.begin_run_status(
        str(project), "single-step select_reels, manual LLM",
        ["select_reels"], argv=["--step", "select_reels"],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={})
    return project


def test_stamp_helper_reports_and_never_skips(tmp_path, capsys):
    """The runner helper stamps, prints the comparison, and returns None -
    there is no skip path because none was built. The second identical run
    prints 'identical' while stamping again."""
    project = _begin(tmp_path)
    impl = {"manifest": _manifest("step_3_04_select_reels"),
            "step_dir": _step_dir(), "type": "hybrid"}
    runner._stamp_step_input_digest(
        str(project), "select_reels", impl, _inputs(),
        _select_reels_output())
    first_out = capsys.readouterr().err
    assert "no prior stamp" in first_out

    # A second run carries the first run's stamp as its baseline.
    run_control.begin_run_status(
        str(project), "single-step select_reels, manual LLM",
        ["select_reels"], argv=["--step", "select_reels"],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={})
    assert runner._stamp_step_input_digest(
        str(project), "select_reels", impl, _inputs(),
        _select_reels_output()) is None
    second_out = capsys.readouterr().err
    assert "identical inputs" in second_out
    record = run_control.read_run_status(str(project))
    assert record["step_input_digests"]["select_reels"]["digest"]
