"""Edit-step input digests: declared, stamped, and reported - never skipped.

Covers `library/tools/edit_input_digest.py` (the spec reader, the digest,
the comparison lines) and the `run_control` stamp plumbing it rides on.
No pipeline run, no model, no Resolve: every digest here is computed over
synthetic inputs shaped like the real ones, plus the real manifests and
real step directories for the code half.

What this pins, beyond the functions' own contracts:

* the three most re-run model steps declare exactly the inputs this task
  evidenced (see each test's docstring for the run_history count), and
  the declarations stay narrowed to what each step actually reads;
* a re-run prints identical / changed / unknown and runs the step either
  way - there is no skip path to test because none was built;
* `tests/test_step_ledger.py` still passes unchanged (run separately -
  this file touches neither the preflight ledger nor its gate).
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.processes.edit_video import run_pipeline as runner
from library.tools import edit_input_digest as digest
from library.tools import run_control

PILOT_ROOT = Path(__file__).resolve().parents[1]
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

def test_steps_without_a_block_do_not_participate():
    """No block, no digest: most edit steps stay unstamped in v1 scope."""
    assert digest.digest_spec(
        _manifest("step_2_01_creative_direction"),
        "creative_direction") is None


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


def test_judge_reels_declares_its_two_required_inputs():
    """judge_reels is geo-podcast's live single-step run (the run record's
    current mode after nineteen select_reels re-runs) and the audit's
    second whole-timeline reel step. Its bridge reads exactly the two
    required inputs, and the digest names exactly those two."""
    spec = digest.digest_spec(
        _manifest("step_3_05_judge_reels"), "judge_reels")
    assert spec["inputs"] == ["timeline_transcript", "reel_selection"]
    assert spec["context"] == ["reels_to_read", "reels_not_readable"]


def test_render_motion_graphics_declares_what_it_reads():
    """render_motion_graphics is replan-001's most re-run model step (6
    single-step runs in its run_history). Unlike select_reels it CONSUMES
    audio_spine - the bridge lays the spine out as the planning context -
    so audio_spine is hashed here and not there."""
    spec = digest.digest_spec(
        _manifest("step_4_06_render_motion_graphics"),
        "render_motion_graphics")
    assert "audio_spine" in spec["inputs"]
    assert "motion_graphics_frame" in spec["context"]


def test_a_digest_input_must_already_be_a_declared_input():
    """A digest entry may only narrow what the manifest declares, never
    invent a new source - the silent-staleness failure, one step removed."""
    manifest = _manifest("step_3_05_judge_reels")
    manifest["classification"]["input_digest"]["inputs"] = [
        "timeline_transcript", "reel_selection", "whatever_is_lying_around"]
    with pytest.raises(digest.DigestError):
        digest.digest_spec(manifest, "judge_reels")


def test_malformed_blocks_raise():
    manifest = _manifest("step_3_05_judge_reels")
    bad_blocks = [
        "inputs",
        {"inputs": []},
        {"inputs": "timeline_transcript"},
        {"inputs": ["timeline_transcript"], "context": "reels_to_read"},
        {"inputs": ["timeline_transcript"],
         "context": ["timeline_transcript"]},
        {"inputs": ["timeline_transcript", "timeline_transcript"]},
        {"inputs": ["timeline_transcript"], "include_code": "yes"},
        {"inputs": ["timeline_transcript"], "extra_key": []},
    ]
    for block in bad_blocks:
        manifest["classification"]["input_digest"] = block
        with pytest.raises(digest.DigestError):
            digest.digest_spec(copy.deepcopy(manifest), "judge_reels")


# ── The digest ──────────────────────────────────────────────────────

def _spec():
    return digest.digest_spec(
        _manifest("step_3_04_select_reels"), "select_reels")


def _step_dir():
    return STEPS_ROOT / "step_3_04_select_reels"


def test_digest_is_deterministic():
    spec = _spec()
    first = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    second = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    assert first["digest"] is not None and first["digest"] == second["digest"]


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


def test_digest_moves_when_a_bridge_table_moves():
    """The shared-code vector: a fix to reel_exchange changes the tables
    without touching the inputs, and the hashed tables catch it."""
    spec = _spec()
    before = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    output = _select_reels_output()
    output["reel_candidates"].append({"start": 100.0, "end": 145.0})
    after = digest.compute_digest(spec, _inputs(), output, _step_dir())
    assert before["digest"] != after["digest"]


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


def test_an_absent_optional_is_recorded_not_skipped():
    """A run without a brand still stamps: absence is a value, and the
    digest must differ from a run that had one."""
    spec = _spec()
    without = _inputs()
    del without["brand_constraints"]
    first = digest.compute_digest(
        spec, without, _select_reels_output(), _step_dir())
    second = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    assert first["digest"] is not None
    assert first["digest"] != second["digest"]


def test_an_unhashable_value_is_recorded_as_failure_not_guessed():
    spec = _spec()
    inputs = _inputs()
    inputs["creative_brief"] = {"not": {"hashable", "a set"}}
    record = digest.compute_digest(
        spec, inputs, _select_reels_output(), _step_dir())
    assert record["digest"] is None
    assert "creative_brief" in record["error"]


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

def test_first_run_is_unknown_and_says_the_step_ran():
    lines = digest.comparison_lines(
        "select_reels",
        digest.compute_digest(
            _spec(), _inputs(), _select_reels_output(), _step_dir()),
        None)
    text = "\n".join(lines)
    assert "unknown" in text and "no prior stamp" in text
    assert "ran normally" in text


def test_identical_inputs_say_identical_and_run():
    spec = _spec()
    record = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    lines = digest.comparison_lines("select_reels", record,
                                    copy.deepcopy(record))
    text = "\n".join(lines)
    assert "identical inputs" in text
    assert "does not skip" in text


def test_changed_inputs_name_which_half_moved():
    spec = _spec()
    before = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    changed = _inputs()
    changed["creative_direction"] = {"narrative_theme": "different"}
    after = digest.compute_digest(
        spec, changed, _select_reels_output(), _step_dir())
    text = "\n".join(
        digest.comparison_lines("select_reels", after, before))
    assert "CHANGED" in text and "upstream inputs" in text
    assert "ran normally" in text


def test_unhashable_runs_compare_as_unknown():
    spec = _spec()
    good = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    inputs = _inputs()
    inputs["creative_brief"] = {"not": {"hashable", "a set"}}
    bad = digest.compute_digest(
        spec, inputs, _select_reels_output(), _step_dir())
    assert "unknown" in "\n".join(
        digest.comparison_lines("select_reels", bad, good))
    assert "unknown" in "\n".join(
        digest.comparison_lines("select_reels", good, bad))


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


def test_begin_carries_previous_stamps_forward():
    stamp = {"digest": "abc", "inputs_digest": "abc",
             "code_digest": None, "recorded_at": "2026-09-06T12:00:00",
             "step": "select_reels"}
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        from pathlib import Path as _P
        proj = _P(tmp) / "proj"
        proj.mkdir()
        (proj / run_control.RUN_STATUS_FILE).write_text(
            json.dumps({"mode": "old", "step_input_digests":
                        {"select_reels": stamp}}), encoding="utf-8")
        run_control.begin_run_status(
            str(proj), "single-step select_reels, manual LLM",
            ["select_reels"], argv=["--step", "select_reels"],
            profile=SimpleNamespace(name="", path="", source="",
                                    adopted=False, description=""),
            breakpoints={}, state={})
        record = run_control.read_run_status(str(proj))
        assert record["step_input_digests"] == {}
        assert record["previous_step_input_digests"] == {
            "select_reels": stamp}


def test_record_merges_one_step_without_touching_the_rest(tmp_path):
    project = _begin(tmp_path)
    run_control.record_step_input_digest(
        str(project), "select_reels", {"digest": "aaa"})
    run_control.record_step_input_digest(
        str(project), "judge_reels", {"digest": "bbb"})
    record = run_control.read_run_status(str(project))
    assert record["step_input_digests"] == {
        "select_reels": {"digest": "aaa"},
        "judge_reels": {"digest": "bbb"}}


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


def test_stamp_helper_with_no_block_stamps_nothing(tmp_path, capsys):
    project = _begin(tmp_path)
    impl = {"manifest": _manifest("step_2_01_creative_direction"),
            "step_dir": STEPS_ROOT / "step_2_01_creative_direction",
            "type": "llm_only"}
    runner._stamp_step_input_digest(
        str(project), "creative_direction", impl, {"a": 1}, {"b": 2})
    assert capsys.readouterr().err == ""
    assert (run_control.read_run_status(str(project))
            .get("step_input_digests") == {})
