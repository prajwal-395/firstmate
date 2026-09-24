"""Pin the series-shared brand library contract (AGENTS.md 14).

The gap: shared brand masters with no history, no undo and no
integrity record, while the declaration naming them IS versioned.
These tests pin the three halves - the store initialises as its own
repo, the manifest records what each file is, and drift is found -
plus the plan-time reader that keeps the manifest honest.  Every
project here is built under ``tmp_path``; no test reaches a real
store.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

from library.tools import brand_library as bl
from library.tools import full_frame_element as ffe

FPS_30 = 30.0


def _git(store, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(store), capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _store(tmp_path, files=None):
    """A series-shared store layout: brand-assets/motion/<files>."""
    motion = tmp_path / "brand-assets" / "motion"
    motion.mkdir(parents=True)
    for name, content in (files or {}).items():
        path = motion / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return tmp_path, motion


def _stub_measure(path):
    return {"width": 1080, "height": 1920, "fps": 30.0, "frames": 90,
            "duration_seconds": 3.0, "container": "mov",
            "video_codec": "prores", "has_audio": True}


def _clip(motion, name="logo.mov", seconds=1.0, fps=30):
    """A real movie file, because a clip is admitted on a MEASUREMENT."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    out = motion / name
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=red:s=1080x1920:r={fps}:d={seconds}",
         "-c:v", "prores_ks", "-profile:v", "3", str(out)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0 or not out.exists():
        pytest.skip("ffmpeg cannot encode a fixture here")
    return str(out)


# ── The store itself ───────────────────────────────────────────────

def test_a_directory_without_brand_assets_is_not_a_store(tmp_path):
    assert bl.is_shared_store(str(tmp_path)) is False
    with pytest.raises(bl.NotASharedStore):
        bl.init_shared_repo(str(tmp_path))
    with pytest.raises(bl.NotASharedStore):
        bl.write_manifest(str(tmp_path), measure=_stub_measure)






# ── The manifest ───────────────────────────────────────────────────

def test_write_manifest_records_hash_size_and_measurement(tmp_path):
    store, _ = _store(tmp_path, {"logo.mov": b"picture-bytes" * 64})
    report = bl.write_manifest(str(store), measure=_stub_measure)
    assert report["new"] == 1
    assert report["preserved"] == 0
    data = json.loads(Path(report["manifest"]).read_text(encoding="utf-8"))
    assert data["schema"] == bl.SCHEMA_VERSION
    entry = data["files"]["logo.mov"]
    assert entry["bytes"] == len(b"picture-bytes" * 64)
    assert len(entry["sha256"]) == 64
    assert entry["measured"]["frames"] == 90
    assert entry["role"] == "asset"




def test_write_manifest_tolerates_an_unmeasurable_file(tmp_path):
    store, _ = _store(tmp_path, {"notes.md": "# review notes"})

    def refuse(path):
        raise ValueError("not a picture")

    report = bl.write_manifest(str(store), measure=refuse)
    data = json.loads(Path(report["manifest"]).read_text(encoding="utf-8"))
    entry = data["files"]["notes.md"]
    # Integrity first: the hash lands even where no measurement can.
    assert len(entry["sha256"]) == 64
    assert "measured" not in entry
    assert "not a picture" in entry["unmeasured"]


def test_refresh_preserves_curation_and_rehashes_bytes(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"first-bytes" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    manifest_file = motion / bl.MANIFEST_FILENAME
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["files"]["logo.mov"]["role"] = "conform"
    data["files"]["logo.mov"]["recipe"] = "minterpolate blend"
    data["files"]["logo.mov"]["note"] = "captain 2026-09-17"
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    (motion / "logo.mov").write_bytes(b"second-bytes" * 64)
    report = bl.write_manifest(str(store), measure=_stub_measure)
    assert report["preserved"] == 1
    assert report["new"] == 0
    entry = json.loads(manifest_file.read_text(encoding="utf-8"))
    entry = entry["files"]["logo.mov"]
    assert entry["role"] == "conform"
    assert entry["recipe"] == "minterpolate blend"
    assert entry["note"] == "captain 2026-09-17"
    assert entry["bytes"] == len(b"second-bytes" * 64)


def test_a_hand_edited_unknown_role_raises_not_defaults(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    manifest_file = motion / bl.MANIFEST_FILENAME
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["files"]["logo.mov"]["role"] = "masterpiece"
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(bl.UnknownRole):
        bl.write_manifest(str(store), measure=_stub_measure)


def test_write_manifest_records_which_declarations_point_at_it(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    project = tmp_path / "geo-podcast"
    project.mkdir()
    yaml_path = project / "project.yaml"
    yaml_path.write_text(
        f"asset: {motion / 'logo.mov'}\n", encoding="utf-8")
    elsewhere = tmp_path / "other.yaml"
    elsewhere.write_text("asset: /nowhere/nothing.mov\n", encoding="utf-8")
    missing = tmp_path / "gone.yaml"
    report = bl.write_manifest(
        str(store), measure=_stub_measure,
        project_yaml_paths=[str(yaml_path), str(elsewhere), str(missing)])
    assert report["unreadable_yaml"] == [str(missing)]
    data = json.loads(Path(report["manifest"]).read_text(encoding="utf-8"))
    assert data["files"]["logo.mov"]["declared_by"] == [str(yaml_path)]


def test_a_manifest_from_a_newer_writer_is_refused_not_rewritten(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    manifest_file = motion / bl.MANIFEST_FILENAME
    manifest_file.write_text(json.dumps({"schema": 999, "files": {}}),
                             encoding="utf-8")
    before = manifest_file.read_text(encoding="utf-8")
    with pytest.raises(bl.BrandLibraryError):
        bl.write_manifest(str(store), measure=_stub_measure)
    assert manifest_file.read_text(encoding="utf-8") == before


# ── Verification ───────────────────────────────────────────────────

def test_verify_passes_a_clean_store(tmp_path):
    store, _ = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    report = bl.verify_manifest(str(store))
    assert report["verified"] is True
    assert report["checked"] == 1
    assert report["mismatches"] == []
    assert report["untracked"] == []
    assert bl.require_clean(report) is None


def test_verify_finds_a_changed_file(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    (motion / "logo.mov").write_bytes(b"y" * 64)
    report = bl.verify_manifest(str(store))
    assert report["verified"] is False
    assert report["mismatches"][0]["kind"] == "changed"
    assert report["mismatches"][0]["path"] == "logo.mov"
    with pytest.raises(bl.ManifestDrift) as raised:
        bl.require_clean(report)
    assert "logo.mov" in str(raised.value)
    assert "changed" in str(raised.value)




def test_verify_reports_new_work_without_refusing_it(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    (motion / "bumper.mov").write_bytes(b"new" * 64)
    report = bl.verify_manifest(str(store))
    assert report["untracked"] == ["bumper.mov"]
    # Landing new work is not drift: the strict surface stays silent.
    assert report["verified"] is True
    assert bl.require_clean(report) is None


def test_verify_without_a_manifest_declines_by_name(tmp_path):
    store, _ = _store(tmp_path, {"logo.mov": b"x" * 64})
    report = bl.verify_manifest(str(store))
    assert report["verified"] is False
    assert report["reason"] == "no-manifest"
    with pytest.raises(bl.ManifestDrift):
        bl.require_clean(report)


# ── The plan-time reader ───────────────────────────────────────────





def test_a_changed_covered_file_is_said_by_name(tmp_path):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    (motion / "logo.mov").write_bytes(b"y" * 64)
    note = bl.library_note_for_asset(str(motion / "logo.mov"))
    assert "logo.mov" in note
    assert "changed" in note




def test_an_unlisted_file_is_said_not_silent(tmp_path):
    """A generated conform with no integrity record is the exact file
    this task was opened for - the reader must not pass it quietly."""
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    bl.write_manifest(str(store), measure=_stub_measure)
    (motion / "conform.mov").write_bytes(b"new" * 64)
    note = bl.library_note_for_asset(str(motion / "conform.mov"))
    assert "conform.mov" in note
    assert "no integrity record" in note




# ── The CLI ────────────────────────────────────────────────────────



def test_cli_verify_reports_drift_with_exit_3(tmp_path, capsys):
    store, motion = _store(tmp_path, {"logo.mov": b"x" * 64})
    assert bl.main(["--init", str(store)]) == 0
    capsys.readouterr()
    assert bl.main(["--write-manifest", str(store)]) == 0
    capsys.readouterr()
    (motion / "logo.mov").write_bytes(b"y" * 64)
    assert bl.main(["--verify", str(store)]) == 3
    err = capsys.readouterr().err
    assert "logo.mov" in err








# ── The plan-time reader, end to end ──────────────────────────────

def _plan(store, asset_path):
    declaration = {"element": "full_frame_clip", "placement": "tail",
                   "asset": asset_path, "reason": "captain marker"}
    cards = ffe.plan_reel_cards(
        ffe.declared_elements({"full_frame_elements": [declaration]}),
        facts=None, body_frames=0, fps=FPS_30,
        project_folder=str(store), width=1080, height=1920)
    assert len(cards) == 1
    return cards[0]


def test_a_planned_card_is_silent_with_no_store_behind_it(tmp_path, capsys):
    """Most clips: no manifest anywhere above them, so the reader
    costs one silent lookup and the card carries nothing."""
    motion = tmp_path / "footage"
    motion.mkdir()
    path = _clip(motion)
    card = _plan(tmp_path, path)
    assert card.library_note == ""
    assert "brand library" not in capsys.readouterr().err




def test_a_planned_card_says_a_moved_master(tmp_path, capsys):
    """The hazard this closes: the manifest says one thing, the disk
    another, and the reel would otherwise bake the move into Resolve.
    The lie is written into the manifest (not the movie) so the clip
    still measures and the card still plans - REPORTED, never a gate.
    """
    store, motion = _store(tmp_path)
    path = _clip(motion)
    bl.write_manifest(str(store))
    manifest_file = motion / bl.MANIFEST_FILENAME
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["files"]["logo.mov"]["sha256"] = "0" * 64
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    card = _plan(store, path)
    assert "logo.mov" in card.library_note
    assert "changed" in card.library_note
    assert card.duration_frames > 0, "the card still plans"
    assert "brand library" in capsys.readouterr().err


# ── The default measurer against a real file ───────────────────────

