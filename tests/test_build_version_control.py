"""Pin the per-project version-control contract (AGENTS.md 3).

The allow-list test names a binary directory the module never names:
if a future binary area is added to the engine, this is the test that
catches it being swallowed into the repo.
"""

import json
import subprocess
import sys

import pytest

from library.tools import build_version_control as bvc


def _git(project, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(project), capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _write(project, rel, content="x"):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


TEXT_FILES = [
    "project.yaml",
    "pipeline_data.json",
    "pipeline_run.json",
    "learned_context/learnings.json",
    "external/captain_edits.json",
    "context/look.md",
    "profiles/tight.json",
    "pipeline_output/steps/4_05_render_subtitles/output.json",
    "pipeline_output/steps/4_05_render_subtitles/summary.md",
    "pipeline_output/steps/5_04_compile_manifest/assembly_manifest.json",
    "pipeline_output/steps/6_01_render/fusion_comps/a.comp",
    "pipeline_output/steps/6_01_render/otio/cut.build.otio",
    "pipeline_output/steps/6_01_render/otio/cut.timeline.json",
    "pipeline_output/steps/6_01_render/otio/cut.BUILD-RECORD.md",
    "pipeline_output/llm_requests/r.json",
    "pipeline_output/llm_responses/r.json",
    "pipeline_output/gates/g.json",
    "pipeline_output/review/channel.json",
    "pipeline_output/review/reel_variants.json",
    "marker_feedback/pull.json",
    "timeline_captures/reel13-live-20260911/reel13_live_state.json",
    "pipeline_output/provenance/p.json",
    "pipeline_output/quarantine/mark_20240101.json",
    "pipeline_output/quarantine/mark_20240101.md",
    "pipeline_output/quarantine/sweep_20240101_manifest.md",
    "pipeline_output/RUN-TRACEBACK.md",
    "pipeline_output/ARTIFACTS.md",
]

# Binaries that must NEVER land in the repo - including
# 9_99_newthing/, a step directory this module never names, which is
# the regression shape for "the next new binary directory somebody
# adds".
BINARY_DECOYS = [
    "pipeline_output/steps/4_05_render_subtitles/seg_0001.mov",
    "pipeline_output/steps/9_99_newthing/big.bin",
    "pipeline_output/scratch/work.tmp",
    "pipeline_output/quarantine/moved_aside.mov",
    "pipeline_output/thumbnails/thumb.jpg",
    "pipeline_output/steps/7_01_build_reels/frame_overlays/f1.png",
    "exports/final.mp4",
    "audio/a.wav",
    "subtitle_overlays/o.mov",
]




# ── Declaration-store coverage ─────────────────────────────────────
#
# The class, enumerated 2026-09-12 when `learned_context/learnings.json`
# was found unversioned: every store the pipeline READS that controls
# the edit, checked against the GENERATED allow-list, with the verdict
# for each.
#
# Tracked (asserted below - each path is DERIVED from the reading
# module's own constants, never copied out of ALLOW_LIST, so deleting
# an allow-list entry breaks the assertion that names its reader):
#   project.yaml                       <- brand_registry, schemas
#   external/<key>.json                <- external_inputs, captain_edits
#   context/                           <- project_context
#   profiles/                          <- run_profile
#   learned_context/learnings.json     <- transcript_corrections,
#      project_context, reel_build, reel_conformance_verifier,
#      layer_coherence, captain_edits
#   marker_feedback/ (pulls, ledger, resolutions, stills)
#                                      <- marker_feedback, marker_routing,
#      feedback_ledger, marker_resolution, marker_capture
#   timeline_captures/                 <- hand-edit evidence (pinned above
#      in TEXT_FILES)
#   pipeline_output/provenance/creative_brief_snapshot.md (+ digest
#      sidecar)                       <- brief_snapshot, asserted in
#      tests/test_brief_snapshot.py rather than below
#
# Deliberately NOT tracked, and why:
#   creative_brief (the project.yaml-declared path; live: the
#      root-level creative_brief.md) - read by nine steps BY REFERENCE,
#      but the declaration is a free per-project path that may sit
#      outside the project folder entirely, so no static allow-list can
#      name it. The versioned project.yaml records WHERE it was - and
#      the run snapshots WHAT it said into
#      `pipeline_output/provenance/creative_brief_snapshot.md` plus its
#      digest sidecar (library/tools/brief_snapshot.py), which this
#      allow-list DOES version. The path stays free; the versioning
#      stopped being static. See tests/test_brief_snapshot.py.
#   brand_assets/, assets/, compositions/ - the captain's artwork and
#      source tree. Binary-capable (PNG, .drx, fonts, .mov), and the
#      allow-list's stated purpose is text-only; the versioned
#      project.yaml paths that REFERENCE them survive without the blobs.
#   subtitle_plans/, subtitle_overlays/ - the captain's standalone
#      scripts' area (props plus binary .mov renders); the pipeline
#      renders its own versioned copies under steps/4_05.
#   raw/, music/, audio/, transcripts/, fonts/ - source media and
#      derived caches: bulky, or reproducible by re-transcription.
#   pipeline_output/annotations/, reasoning/, logs/, backups/, scratch/
#      - dashboard chatter and recomputable output, not declarations.
#
# A test that merely restated ALLOW_LIST would pass just as happily
# with learned_context/ still missing. This one cannot: its input
# comes from the modules that read the stores.


def _declaration_stores():
    """Store paths derived from the readers, not from the allow-list."""
    from library.tools import captain_edits
    from library.tools import feedback_ledger
    from library.tools import learned_context
    from library.tools import marker_resolution
    from library.tools.project_layout import (
        AREAS, Area, PROJECT_CONFIG_FILE)

    def _rel(area):
        return AREAS[Area(area)].relpath

    return [
        # project.yaml: read by brand_registry and the config schema.
        (PROJECT_CONFIG_FILE, "project.yaml readers"),
        # external/: read by external_inputs (CHECKS) and captain_edits.
        (f"{_rel(Area.EXTERNAL_STATE)}/{captain_edits.CAPTAIN_EDITS_KEY}.json",
         "external_inputs / captain_edits"),
        # context/: read by project_context on every planning step.
        (f"{_rel(Area.CONTEXT)}/look.md", "project_context"),
        # profiles/: read by run_profile.
        (f"{_rel(Area.RUN_PROFILES)}/tight.json", "run_profile"),
        # learned_context/: the single JSON record learned_context.py
        # writes; read by transcript_corrections, project_context,
        # reel_build, reel_conformance_verifier, layer_coherence and
        # captain_edits.
        (f"{_rel(Area.LEARNED_CONTEXT)}/{learned_context.LEARNINGS_FILE}",
         "transcript_corrections / learned_context readers"),
        # marker_feedback/: pulls, the feedback ledger and resolutions.
        (f"{_rel(Area.MARKER_FEEDBACK)}/{feedback_ledger.LEDGER_FILENAME}",
         "feedback_ledger"),
        (f"{_rel(Area.MARKER_FEEDBACK)}/"
         f"{marker_resolution.RESOLUTIONS_SUBDIR}/x.json",
         "marker_resolution"),
    ]




def test_learned_context_crash_tmp_stays_out(tmp_path):
    """The allow-list names the record, not the directory.

    `learned_context._save` writes through a `.learnings.*.tmp` file
    beside the record; a crash leaves one behind. Whole-directory
    re-inclusion would sweep it into the repo, so the entry is the
    file - and this pins that the leftover stays ignored.
    """
    from library.tools import learned_context
    from library.tools.project_layout import AREAS, Area

    rel = AREAS[Area(Area.LEARNED_CONTEXT)].relpath
    _write(tmp_path, f"{rel}/{learned_context.LEARNINGS_FILE}", "[]\n")
    _write(tmp_path, f"{rel}/.learnings.abc123.tmp", "{}\n")
    assert bvc.init_project_repo(str(tmp_path))["initialised"] is True
    assert bvc.commit_build(str(tmp_path), message="first\n")["committed"]
    tracked = set(_git(tmp_path, "ls-files").splitlines())
    assert f"{rel}/{learned_context.LEARNINGS_FILE}" in tracked
    assert f"{rel}/.learnings.abc123.tmp" not in tracked








def test_build_record_names_the_blind_spot(tmp_path):
    written = bvc.write_build_record(
        str(tmp_path), "cut_01", "OTIO-body", {"schema_version": "1.0"})
    record = (tmp_path / f"pipeline_output/steps/6_01_render/otio/"
              "cut_01.BUILD-RECORD.md").read_text(encoding="utf-8")
    assert "Fusion comps empty" in record
    assert "CDL grades absent" in record
    assert "fusion_comps" in record
    assert written["otio"].endswith("cut_01.build.otio")
    assert written["timeline_json"].endswith("cut_01.timeline.json")


def test_record_without_repo_declines(tmp_path):
    class _Timeline:
        def Export(self, path, flag):
            raise AssertionError("must not reach Resolve without a repo")

    report = bvc.record_finished_timeline(
        object(), _Timeline(), str(tmp_path), "cut_01")
    assert report == {"committed": False, "reason": "no-repo", "files": []}




# ── The reels promotion record ─────────────────────────────────────
#
# The 6.01 hook never fired for reels, so no reel build committed its
# baseline (measured 2026-09-11). `record_reel_promotion` closes that:
# one snapshot per promoted timeline beside the declaration, then the
# commit. These pin the decline paths and the snapshot-then-commit
# shape; a Resolve that is absent or on another project records the
# reason instead of raising.


class _FakeTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class _FakeProject:
    def __init__(self, name, timelines):
        self._name = name
        self._timelines = list(timelines)
        self._current = self._timelines[0]

    def GetName(self):
        return self._name

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]

    def GetCurrentTimeline(self):
        return self._current

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


class _FakeManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class _FakeResolve:
    def __init__(self, project):
        self._project = project

    def GetProjectManager(self):
        return _FakeManager(self._project)


class _FakeDvr:
    def __init__(self, resolve):
        self._resolve = resolve

    def scriptapp(self, name):
        return self._resolve


def _promotion_project(name="Podcast (field test)"):
    project = _FakeProject(name, [
        _FakeTimeline("GEO Podcast - Synced"),
        _FakeTimeline("Reel 28 - the-nail-salon-query-google-cant-answer"),
    ])
    return project


def test_reel_promotion_without_repo_declines(tmp_path):
    report = bvc.record_reel_promotion(
        str(tmp_path), "Podcast (field test)", ["Reel 28"])
    assert report["committed"] is False
    assert report["reason"] == "no-repo"




def test_reel_promotion_commits_the_declarations_when_the_snapshot_fails(
        tmp_path, monkeypatch):
    """A snapshot the build could not take must NOT swallow the commit.

    Measured 2026-09-11 on the captain's project: the snapshot half
    returned early on any Resolve trouble - closed, busy, another
    project open - and the declarations, run state and step outputs
    already on disk went uncommitted with it. A declaration nothing in
    git remembers is one that goes missing the next time somebody edits
    the file, which is the failure this whole store exists to stop.
    """
    bvc.init_project_repo(str(tmp_path))
    (tmp_path / "external").mkdir()
    (tmp_path / "external" / "reel_ending.json").write_text(
        '{"version": 1, "endings": []}', encoding="utf-8")
    project = _promotion_project(name="Something else")
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        _FakeDvr(_FakeResolve(project)))

    report = bvc.record_reel_promotion(
        str(tmp_path), "Podcast (field test)", ["Reel 28"])

    assert report["committed"] is True
    assert report["snapshots"] == []
    # The failure is NAMED rather than dropped, in the report and in
    # the commit message - so a reader of the log can tell which
    # commits have no timeline snapshot behind them.
    assert "Something else" in report["snapshot_failed"]
    assert "snapshot failed" in report["reason"]
    log = subprocess.run(["git", "log", "-1", "--format=%B"],
                         cwd=str(tmp_path), capture_output=True,
                         text=True, encoding="utf-8", check=False)
    assert "NO TIMELINE SNAPSHOT" in log.stdout
    assert "external/reel_ending.json" in report["files"]


