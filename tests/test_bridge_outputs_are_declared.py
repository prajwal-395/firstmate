"""Every key a pre-bridge emits is declared, so the validator knows it.

D8 (project 001 run-after-the-five audit): `validate_step_output`
reported bridge-supplied tables as "unexpected extra fields" on
`render_motion_graphics` and `validate` - and the sweep for the same
cause found four more steps emitting keys no manifest declares
(`plan_vfx`, `mesh_spine`, `select_reels`, `judge_reels`).

The mechanism: `run_hybrid_step` merges the pre-bridge's output back
into the step's final result, so anything a `bridge.py` emits reaches
`validate_step_output`. The validator's expected set is the manifest's
`interface.outputs`, which is why the established repair - here and in
`plan_sfx`, `select_broll`, `color_grade` - is to DECLARE each emitted
key rather than to silence the warning.

Both halves derive from the code, never from copied literals: the
emitted keys come from EXECUTING each bridge the way the runner does,
and the expected names come from the manifest the validator reads.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import (
    validate_step_output,
)
from library.tools import processes

STEPS_ROOT = REPO / "library" / "steps"


def _two_speaker_transcript():
    """Two voices trading short lines - enough for an exchange."""
    def seg(speaker, start, end, text):
        return {
            "resolve_item_id": f"r{start}",
            "timeline_start": start,
            "timeline_end": end,
            "speaker": speaker,
            "text": text,
            "source_start": 0.0,
            "source_end": round(end - start, 2),
        }

    return {"segments": [
        seg("A", 0.0, 2.0, "hello there friend"),
        seg("B", 2.5, 5.0, "hi back to you"),
        seg("A", 5.5, 8.0, "how are things today"),
        seg("B", 8.5, 11.0, "all good thanks mate"),
    ]}


def _cases(step_dir: str, tmp_path: Path):
    """One (inputs, env) pair per emission path of this step's bridge.

    Fixtures are INPUTS, not expectations: what the bridge emits under
    them is what the test reads off. Steps whose bridge runs on `{}` get
    no fixture; steps with a degenerate path get both.
    """
    transcript = _two_speaker_transcript()
    if step_dir == "step_2_02_speech_sequence":
        project = tmp_path / "proj_202"
        project.mkdir()
        index_dir = tmp_path / "index_202"
        index_dir.mkdir()
        (index_dir / "clip_001.json").write_text(json.dumps(
            {"speech_regions": [
                {"start": 0.0, "end": 1.5, "text": "hello world"}]}),
            encoding="utf-8")
        (project / "pipeline_data.json").write_text(json.dumps(
            {"step_outputs": {
                "temporal_index": {"index_dir": str(index_dir)}}}),
            encoding="utf-8")
        return [({"temporal_index": {"clips": 1},
                  "semantic_analysis_documents": [
                      {"clip_id": "clip_001",
                       "assessment": {"keywords": ["greeting"]}}],
                  "project_folder": str(project)}, {})]
    if step_dir == "step_2_05_mesh_spine":
        return [({"project_config": {"target_duration_seconds": 60}}, {}),
                ({}, {})]
    if step_dir == "step_3_02_select_broll":
        project = tmp_path / "proj_302"
        project.mkdir()
        return [({"clip_catalog": [
                      {"clip_id": "clip_001", "duration_seconds": 10.0}],
                  "a_roll_assignments": [],
                  "semantic_analysis_documents": [
                      {"clip_id": "clip_001",
                       "analysis": {
                           "scene": "a sunny plaza with people walking",
                           "camera": "static wide shot",
                           "actions": ["walking"]},
                       "assessment": {"keywords": ["plaza"]}}],
                  "project_folder": str(project),
                  "timed_spine": {}}, {})]
    if step_dir == "step_3_04_select_reels":
        return [({"timeline_transcript": transcript}, {}),
                ({}, {})]
    if step_dir == "step_3_05_judge_reels":
        moment = {"number": 1, "slug": "probe", "reason": "probe",
                  "timeline_start": 0.0, "timeline_end": 11.0}
        far_away = dict(moment, number=2,
                        timeline_start=100.0, timeline_end=110.0)
        return [({"timeline_transcript": transcript,
                  "reel_selection": {"moments": [moment]}}, {}),
                ({"timeline_transcript": transcript,
                  "reel_selection": {"moments": [far_away]}}, {})]
    if step_dir == "step_4_04_plan_sfx":
        lib = tmp_path / "sfxlib"
        lib.mkdir()
        candidate = lib / "boom.wav"
        candidate.write_bytes(b"\x00" * 100)
        (lib / "sfx_index.json").write_text(json.dumps(
            [{"file": "boom.wav", "path": str(candidate),
              "technical": {"basic": {"duration_seconds": 1.0},
                            "envelope": "sustained"}}]),
            encoding="utf-8")
        project = tmp_path / "proj_404"
        project.mkdir()
        return [({"project_folder": str(project)},
                 {"PIPELINE_SFX_LIBRARY": str(lib)})]
    return [({}, {})]


def _run_bridge(step_dir: str, inputs: dict, env: dict) -> dict:
    """The runner's own invocation: bridge.py with JSON on stdin."""
    run_env = dict(os.environ, PYTHONPATH=str(REPO))
    run_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(STEPS_ROOT / step_dir / "bridge.py")],
        input=json.dumps(inputs),
        capture_output=True,
        check=False,
        text=True, encoding="utf-8",
        cwd=str(REPO),
        timeout=120,
        env=run_env,
    )
    assert proc.returncode == 0, (
        f"{step_dir}/bridge.py refused the fixture inputs:\n"
        f"{proc.stderr[-2000:]}")
    emitted = json.loads(proc.stdout)
    assert isinstance(emitted, dict), (
        f"{step_dir}/bridge.py emitted {type(emitted).__name__}, "
        f"not the dict run_hybrid_step merges")
    return emitted


def _manifest(step_dir: str) -> dict:
    return json.loads(
        (STEPS_ROOT / step_dir / "manifest.json").read_text(
            encoding="utf-8"))


def _declared_names(manifest: dict) -> set:
    """The names the validator treats as expected, plus the model's own.

    `validate_step_output` reads `interface.outputs`; the model's answer
    keys are declared beside them as `interface.llm_outputs`. A bridge
    key matching either is known.
    """
    interface = manifest.get("interface", {})
    return ({o.get("name") for o in interface.get("outputs", [])}
            | {o.get("name") for o in interface.get("llm_outputs", [])})


def _node_id(step_dir: str) -> str:
    """The DAG node id for this step directory, for validator messages."""
    for dag in processes.every_dag().values():
        for node in dag.get("nodes", []):
            if node.get("step_ref", "").split("/")[-1] == step_dir:
                return node["id"]
    return _manifest(step_dir).get("id", step_dir)


def _dummy_for(declared_type: str):
    """A non-empty value of the manifest's own declared type.

    Non-empty on purpose: the emptiness and type checks are other
    tests' business, and a probe that trips them would read as this
    test failing for the wrong reason.
    """
    table = {
        "dict": {"_probe": 1}, "object": {"_probe": 1},
        "list": [{"_probe": 1}], "array": [{"_probe": 1}],
        "str": "_probe_", "string": "_probe_",
        "int": 1, "float": 1.0, "number": 1.0,
        "bool": True, "boolean": True,
    }
    return table.get((declared_type or "").lower(), "_probe_")


def _bridge_steps():
    return sorted(p.name for p in STEPS_ROOT.iterdir()
                  if (p / "bridge.py").is_file())


def test_every_bridge_key_is_declared(tmp_path):
    """Each bridge's emissions are a subset of its manifest's names.

    Fails while any bridge emits a key its manifest does not declare -
    the D8 shape, on whichever step introduces it next.
    """
    problems = []
    for step_dir in _bridge_steps():
        manifest = _manifest(step_dir)
        declared = _declared_names(manifest)
        for inputs, env in _cases(step_dir, tmp_path):
            emitted = _run_bridge(step_dir, inputs, env)
            unknown = sorted(set(emitted) - declared)
            if unknown:
                problems.append(f"{step_dir}: {unknown}")
    assert not problems, (
        "bridge.py emits keys no manifest declares - "
        "validate_step_output will report them as unexpected extra "
        f"fields: {problems}")


def test_no_bridge_key_is_an_unexpected_extra_field(tmp_path):
    """D8's reproduction, generalised to every hybrid step.

    A synthetic final step output - the manifest's own declared outputs
    plus the values the bridge actually emitted - must not trip the
    extra-fields check. Before the fix this is the report verbatim:
    `unexpected extra fields: motion_axes_toon, ...` on
    render_motion_graphics and `deterministic_validation` on validate.
    """
    problems = []
    for step_dir in _bridge_steps():
        manifest = _manifest(step_dir)
        interface = manifest.get("interface", {})
        output = {o.get("name"): _dummy_for(o.get("type", ""))
                  for o in interface.get("outputs", [])
                  if o.get("name")}
        for inputs, env in _cases(step_dir, tmp_path):
            output.update(_run_bridge(step_dir, inputs, env))
        issues = validate_step_output(_node_id(step_dir), output, manifest)
        extra = [i for i in issues if "unexpected extra fields" in i]
        if extra:
            problems.append(f"{step_dir}: {extra}")
    assert not problems, (
        "validate_step_output flags bridge-supplied tables as "
        f"unexpected: {problems}")
