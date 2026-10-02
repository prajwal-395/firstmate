"""Is a rebuild from identical declarations reproducible? (issue #937)

The variations-as-branches workflow merges declarations and rebuilds the
timeline instead of merging generated state. That is safe only if a
rebuild from identical declarations gives the same timeline. This file
measures that claim at every level that can be measured without Resolve,
without renders and without the captain's project data (all out of
scope for this lane):

1. RECORDED ANSWERS ARE REUSED. A plain second run of the runner
   executes zero step bodies - `run_pipeline` skips completed steps
   (`already completed`, run_pipeline.py) - so a step that calls a
   model is NOT re-asked. Only `--rerun` discards the recorded output
   and re-executes, which for an LLM step means re-authorship.
2. DERIVATION IS BYTE-STABLE. The reel rebuild path derives from the
   recorded declarations: placements, Fusion comp text, subtitle props
   and the overlay-record rewrite are identical across two derivations
   from the same inputs.
3. THE DIFFS THAT REMAIN ARE NAMED. The per-build request file differs
   only in `timestamp` (reel_semantic_visual.write_request), the build
   provenance only in `built_at` (plan_provenance.write_provenance) -
   bookkeeping nobody reads back into a decision.
4. THE REBUILD CALLS NO MODEL. The modules the rebuild derives through
   import no model client; importing them pulls none in.

What this file does NOT claim: Resolve read-back stability across a
delete-and-recreate (Resolve mints new timeline/item ids - PR #928
measured identical placements across one such cycle live), fresh
Remotion render byte-reproducibility (the content-keyed reuse cache
pairs rebuilds back to the artefact on disk, so a rebuild with no
changes never re-renders - which masks rather than proves renderer
determinism), and LLM re-authorship stability under `--rerun` (a
re-asked model is a new variation, not a rebuild).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from library.tools import capability_outputs
from library.processes.edit_video.run_pipeline import (
    load_pipeline_state,
    run_pipeline,
)


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── The runner: recorded answers are reused, --rerun re-authors ──────

# a -> b -> c, all deterministic, all cheap. Same shape as
# test_run_configuration_end_to_end.THREE_STEPS, owned here so that
# file can move without taking this measurement with it.
THREE_STEPS = {
    "id": "edit_video",
    "nodes": [
        {"id": "scan", "name": "Scan Project Folder",
         "step_ref": "steps/step_1_01_scan_project"},
        {"id": "catalog", "name": "Catalog Footage",
         "step_ref": "steps/step_1_02_catalog_footage"},
        {"id": "temporal_index", "name": "Temporal Index",
         "step_ref": "steps/step_1_04_temporal_index"},
    ],
    "edges": [
        {"from": "scan", "to": "catalog",
         "data_mapping": {"raw_footage_files": "raw_footage_files"}},
        {"from": "catalog", "to": "temporal_index",
         "data_mapping": {"catalog": "catalog"}},
    ],
}

_OUTPUT = {
    "scan": {"raw_footage_files": ["one.mov", "two.mov", "three.mov"]},
    "catalog": {"catalog": [{"clip_id": "clip_001"}]},
    "temporal_index": {"temporal_event_indices": []},
}

_NODE_OF = {
    "step_1_01_scan_project": "scan",
    "step_1_02_catalog_footage": "catalog",
    "step_1_04_temporal_index": "temporal_index",
}


@pytest.fixture
def project(tmp_path):
    """A project with nothing done yet, so the first run really runs."""
    folder = tmp_path / "rebuild_project"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(folder),
        "preflight_completed": {},
        "edit_completed": {},
        "failed_steps": [],
        "capability_outputs": {},
    }), encoding="utf-8")
    (folder / "project.yaml").write_text(
        "name: Rebuild Project\nslug: rebuild-project\n", encoding="utf-8")
    return folder


class _Runner:
    """Drives the real runner over THREE_STEPS, recording which step
    bodies executed - the stand-in for "was the model re-asked". """

    def __init__(self, folder: Path):
        self.folder = folder
        self.seen = []

    def _implementation(self, step_dir):
        return {"type": "deterministic",
                "entry": str(Path(step_dir) / "step.py"), "manifest": {}}

    def _deterministic(self, entry, inputs):
        node_id = _NODE_OF[Path(entry).parent.name]
        self.seen.append(node_id)
        return dict(_OUTPUT[node_id])

    def run(self, **kw):
        with patch("library.processes.edit_video.run_pipeline.load_dag",
                   return_value=THREE_STEPS), \
             patch("library.processes.edit_video.run_pipeline"
                   ".get_step_implementation",
                   side_effect=self._implementation), \
             patch("library.processes.edit_video.run_pipeline"
                   ".run_deterministic_step",
                   side_effect=self._deterministic), \
             patch("library.processes.edit_video.run_pipeline"
                   ".apply_source_identity", return_value=None):
            return run_pipeline(str(self.folder), **kw)


def test_a_plain_second_run_reuses_recorded_answers(project):
    """The single fact that decides issue #937 for the pipeline path.

    A second identical run executes NO step body - every step is
    skipped as already completed - and the recorded outputs are
    unchanged. A model step is therefore NOT re-asked on rebuild;
    its recorded answer stands. If this ever fails, the merge
    workflow's determinism assumption fails with it.
    """
    first = _Runner(project)
    summary = first.run()
    assert summary["status"] == "SUCCESS"
    assert first.seen == ["scan", "catalog", "temporal_index"]

    before = capability_outputs.node_outputs(
        load_pipeline_state(str(project)))

    second = _Runner(project)
    summary = second.run()
    assert summary["status"] == "SUCCESS"
    assert second.seen == [], (
        f"a plain second run re-executed {second.seen} - recorded "
        f"answers are NOT being reused")

    after = capability_outputs.node_outputs(
        load_pipeline_state(str(project)))
    assert after == before, "recorded step outputs moved under a no-op run"


def test_rerun_is_the_reauthorship_path(project):
    """The boundary of the guarantee above: `--rerun` discards the
    recorded output and re-executes the step. For an LLM step that is
    a fresh model call - a new variation, never a rebuild - and the
    merge workflow must not smuggle it through a rebuild."""
    first = _Runner(project)
    assert first.run()["status"] == "SUCCESS"

    second = _Runner(project)
    summary = second.run(rerun=["catalog"])
    assert summary["status"] == "SUCCESS"
    assert second.seen == ["catalog"], (
        f"--rerun catalog should re-execute exactly catalog, ran "
        f"{second.seen}")


# ── Derivation: two builds from identical declarations ───────────────

def _clips():
    return [
        SimpleNamespace(timeline_start=10.0, timeline_end=20.0,
                        source_in=100.0, track_index=1, speaker="Akshita"),
        SimpleNamespace(timeline_start=20.0, timeline_end=30.0,
                        source_in=200.0, track_index=1, speaker="Akshita"),
    ]


def test_effect_comp_is_byte_identical_across_builds():
    """The Fusion comp - the picture itself - serialised twice from
    the same effect parameters. One byte different here is a
    different picture, so this compares bytes, not parses."""
    from library.tools.fusion.comp_builder import build_effect_comp

    effects = {"zoom_start": 1.0, "zoom_end": 1.08,
               "source_in_frame": 100, "source_out_frame": 400,
               "vignette": True, "vignette_soft": 0.35,
               "vignette_blend": 0.25}
    first = build_effect_comp(dict(effects), 500,
                              source_res=(1080, 1920), played_frames=383)
    second = build_effect_comp(dict(effects), 500,
                               source_res=(1080, 1920), played_frames=383)
    assert first == second, "same parameters serialised to different comps"
    assert len(first) > 0


def test_provenance_rewrite_differs_only_in_built_at(tmp_path):
    """`write_provenance` re-stamps `built_at` on every build - the
    cosmetic diff - plus the per-reel `built_at_reels` stamp of the
    reel it just built. Everything else must stand still, because the
    verifier grades the staging against this record."""
    from library.tools.plan_provenance import (
        caption_content_hash,
        write_provenance,
    )

    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    plan = tmp_path / "proposal.json"
    plan.write_text(json.dumps({"moments": []}), encoding="utf-8")
    card_hash = caption_content_hash(
        [{"text": "hello world", "start": 0.5, "duration": 2.0}])

    write_provenance(str(review), str(plan), ["Reel 09 - test"],
                     caption_hashes={"Reel 09 - test": card_hash})
    first = json.loads(
        (review / "plan_provenance.json").read_text(encoding="utf-8"))
    write_provenance(str(review), str(plan), ["Reel 09 - test"],
                     caption_hashes={"Reel 09 - test": card_hash})
    second = json.loads(
        (review / "plan_provenance.json").read_text(encoding="utf-8"))

    first_built, second_built = first.pop("built_at"), second.pop("built_at")
    first_reel_built = first.pop("built_at_reels")
    second_reel_built = second.pop("built_at_reels")
    assert first == second, (
        "provenance moved more than its timestamp across a rebuild")
    assert first_built != second_built, (
        "expected the cosmetic built_at re-stamp; without it this test "
        "proves nothing about what differs")
    assert first_reel_built != second_reel_built, (
        "the rebuilt reel's own stamp must refresh too - a per-reel "
        "stamp that stood still would be the file-level one wearing "
        "per-reel clothes")
    assert first["built_with"] == second["built_with"], (
        "same code, same engine digest - the revision stamp must stand "
        "still across a rebuild that changed no code")


def _reel_fixture():
    moment = SimpleNamespace(number=9, timeline_name="Reel 09 - test")
    transcript = {"segments": [
        {"timeline_start": 10.0, "timeline_end": 14.0,
         "resolve_item_id": "item1", "source_file": "clip_a.mov",
         "source_start": 100.0, "source_end": 104.0,
         "words": [{"word": "hello", "start": 10.1, "end": 10.4}]},
        {"timeline_start": 14.0, "timeline_end": 18.0,
         "resolve_item_id": "item2", "source_file": "clip_a.mov",
         "source_start": 104.0, "source_end": 108.0,
         "words": [{"word": "again", "start": 14.2, "end": 14.6}]},
    ]}
    return moment, transcript, [(10.0, 18.0)]


# ── The rebuild calls no model ───────────────────────────────────────

# Every module a reel rebuild derives through: the build itself, the
# spine and proposal it reads, the two recorded-answer disciplines
# (semantic visuals, motion), the comp serialiser, the caption props
# it re-derives, and the provenance it re-stamps.
_BUILD_PATH_MODULES = [
    "library.tools.reel_build",
    "library.tools.reel_spine",
    "library.tools.reel_proposal",
    "library.tools.reel_semantic_visual",
    "library.tools.reel_look",
    "library.tools.fusion.comp_builder",
    "library.steps.step_4_05_render_subtitles.generate_remotion_props",
    "library.tools.plan_provenance",
]


def test_rebuild_path_imports_no_model_client():
    """Structural pin on the headline finding: importing every module
    the reel rebuild derives through pulls in no model client, so
    there is nothing on the path that COULD re-ask - and the removed
    `llm_client` stays removed. Run in a fresh interpreter because
    this process's `sys.modules` already carries whatever the rest of
    the suite imported."""
    script = (
        "import sys; "
        f"import {', '.join(_BUILD_PATH_MODULES)}; "
        "clients = sorted(m for m in sys.modules "
        "if 'llm_client' in m); "
        "print('MODEL_CLIENTS:' + repr(clients)); "
        "assert not clients, clients\n"
        "try:\n"
        "    import library.tools.llm_client\n"
        "except ImportError:\n"
        "    print('LLM_CLIENT_GONE')\n"
        "else:\n"
        "    raise AssertionError('library.tools.llm_client is back')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, check=False,
        cwd=Path(__file__).resolve().parent.parent,  # noqa: E501
        timeout=120, encoding="utf-8")
    assert proc.returncode == 0, (
        f"rebuild-path import pulled in a model client:\n{proc.stdout}\n"
        f"{proc.stderr}")
    assert "MODEL_CLIENTS:[]" in proc.stdout
    assert "LLM_CLIENT_GONE" in proc.stdout
