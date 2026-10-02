"""Copying a project must not invalidate its preflight analysis.

Finding 7, execution-frontier report 2026-09-24: source fingerprints
recorded the absolute path, so copying a project read every clip as
replaced and deleted its per-clip analysis. Same content at a new path
is a relocation; genuinely changed content at a new path is still
replaced.

This exercises the source check on a copied scratch project with one
cached semantic profile. No pipeline run, model, or real project.
"""

import json
import shutil
from pathlib import Path

from library.processes.edit_video import run_pipeline as runner
from library.tools import footage_identity, step_ledger

REPO_ROOT = Path(__file__).resolve().parents[3]
STEP_DIR = REPO_ROOT / "library" / "steps" / "step_1_03_semantic_analysis"
with open(STEP_DIR / "manifest.json", encoding="utf-8") as handle:
    SEMANTIC_MANIFEST = json.load(handle)
STAGES = {"semantic_analysis": step_ledger.PREFLIGHT}
MANIFESTS = {"semantic_analysis": SEMANTIC_MANIFEST}


def _build_project(root: Path):
    raw = root / "raw"
    raw.mkdir(parents=True)
    (raw / "clip.mov").write_bytes(b"same footage bytes" * 64)
    files, _skipped = footage_identity.enumerate_footage(str(root))
    fingerprints = footage_identity.fingerprints_for(files)

    clip_id = files[0]["clip_id"]
    profile_paths = step_ledger.artifact_paths(
        str(root),
        step_ledger.per_clip_artifacts(SEMANTIC_MANIFEST),
        clip_id,
        Path(files[0]["path"]).stem,
    )
    cached_profiles = {}
    for path in profile_paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"cached semantic analysis")
        cached_profiles[Path(path).relative_to(root)] = b"cached semantic analysis"

    state = {
        "project_folder": str(root),
        step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {
            "semantic_analysis": {
                "completed_at": "2026-08-20T10:00:00",
                "elapsed_s": 2400,
            },
        },
        step_ledger.SOURCE_FINGERPRINTS_KEY: fingerprints,
    }
    return state, cached_profiles


def test_copying_a_project_to_a_new_path_preserves_preflight(tmp_path):
    """Same-content paths keep the cache instead of marking it stale."""
    src = tmp_path / "original"
    state, cached_profiles = _build_project(src)
    dst = tmp_path / "elsewhere" / "copy"
    shutil.copytree(src, dst)

    delta = runner.apply_source_identity(str(dst), state, STAGES, MANIFESTS)

    assert not delta.stale_clip_ids
    assert step_ledger.is_completed(state, "semantic_analysis")
    for relative_path, contents in cached_profiles.items():
        assert (dst / relative_path).read_bytes() == contents


def test_changed_content_at_a_new_path_still_invalidates():
    """Different bytes at a new path cannot be mistaken for relocation."""
    recorded = {
        "clip_001": {"path": "/old/project/raw/a.mov",
                     "size_bytes": 100,
                     "content_digest": "abc"},
    }
    current = {
        "clip_001": {"path": "/new/project/raw/a.mov",
                     "size_bytes": 100,
                     "content_digest": "different-bytes"},
    }

    delta = footage_identity.compare(recorded, current)

    assert "clip_001" in delta.stale_clip_ids
