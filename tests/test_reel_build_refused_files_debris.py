"""A refused reel build must not strand its caption imports.

The defect
----------
`build_reel_timeline` puts every caption clip through
`pool.ImportMedia`, which lands in whatever bin is CURRENT - on the
field test, `Reels/Current plan`. On the pass path
`promote_staged_reels(organise=True)` files those clips afterwards;
on the refusal path nothing did. Reel 05 of the 2026-09-08 rebuild
refused its gate (F17 + F8) and left its 26 caption renders loose in
`Reels/Current plan` - the staging timeline was deleted and its
sidecar entries dropped, but the pool items stayed where ImportMedia
left them.

The fix is in `discard_staged_reels` / `discard_staged_record`: when
the master timeline is named, the pool is filed after the discard, so
a refused build's imports land in `06 - Subtitle renders/Not placed on
any timeline` instead of staying in the current bin. The gate still
refuses - only the debris handling changes.

This test drives `rebuild_reels_in_project` with a placer that stages
a timeline AND imports a caption clip into the current bin, then fails
the gate. It asserts the stray is filed, the staging is gone and the
approved reel is untouched. Against the pre-fix code the stray stays
in `Reels/Current plan` and the filing assertion fails.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from library.tools.reel_build import STAGING_SUFFIX, rebuild_reels_in_project

MASTER = "GEO Podcast - Synced"
TARGET = "Reel 05 - the-audit-that-was-eye-opening"
STAGING = TARGET + STAGING_SUFFIX


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    yield


class FakePoolClip:
    def __init__(self, uid, name, kind="clip", path=""):
        self.uid, self._name, self.kind, self.path = uid, name, kind, path
        self.metadata, self.color = {}, ""

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self._name

    def GetClipProperty(self, key):
        if key == "Type":
            return "Timeline" if self.kind == "timeline" else "Video + Audio"
        if key == "File Path":
            return self.path
        if key == "Clip Color":
            return self.color
        return ""

    def GetMetadata(self, key=None):
        if key is None:
            return dict(self.metadata)
        return self.metadata.get(key, "")

    def SetMetadata(self, key, value):
        if key not in {"Comments", "Keywords", "Description"}:
            return False
        self.metadata[key] = value
        return True

    def SetClipColor(self, value):
        self.color = value
        return True

    def ClearClipColor(self):
        self.color = ""
        return True


class FakeFolder:
    def __init__(self, name, uid):
        self._name, self.uid, self.clips, self.subs = name, uid, [], []

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self.uid

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return list(self.subs)


class FakeResolveTimeline:
    """What the build creates, renames and deletes."""

    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetTrackCount(self, kind):
        return 0

    def GetItemListInTrack(self, kind, index):
        return []

    def GetUniqueId(self):
        if not getattr(self, "_uid", None):
            type(self)._seq = getattr(type(self), "_seq", 0) + 1
            self._uid = f"{type(self).__name__}-{type(self)._seq}"
        return self._uid


class FakePool:
    def __init__(self, root, current):
        self.root, self.current, self._n = root, root, 0
        self._import_n = 0

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self.current

    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def AddSubFolder(self, parent, name):
        self._n += 1
        folder = FakeFolder(name, f"f{self._n}")
        parent.subs.append(folder)
        self.current = folder
        return folder

    def _home(self, clip):
        def walk(folder):
            if clip in folder.clips:
                return folder
            for sub in folder.subs:
                found = walk(sub)
                if found:
                    return found
            return None

        return walk(self.root)

    def MoveClips(self, clips, folder):
        for clip in clips:
            home = self._home(clip)
            if home is not None:
                home.clips.remove(clip)
            folder.clips.append(clip)
        return True

    def ImportMedia(self, paths):
        made = []
        for path in paths:
            self._import_n += 1
            clip = FakePoolClip(f"import-{self._import_n}", Path(path).name,
                                "clip", path)
            self.current.clips.append(clip)
            made.append(clip)
        return made

    def CreateEmptyTimeline(self, name):
        # The pool item half is irrelevant to this test - the stray
        # clip is what must be filed - so only the Resolve timeline
        # is tracked here; the project object owns that list.
        raise NotImplementedError  # wired per test via project

    def DeleteTimelines(self, timelines):
        return True


class FakeProject:
    def __init__(self, pool):
        self._pool = pool
        self.timelines = [FakeResolveTimeline(MASTER),
                          FakeResolveTimeline(TARGET)]

    def GetName(self):
        return "Mock Project"

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    # A Resolve project HAS a cursor, and `resolve_lock`'s guard reads
    # it back by unique id - a fake without one cannot model the guard.
    def GetCurrentTimeline(self):
        # None until something sets it: a project that has not been
        # pointed anywhere has no cursor, and inventing one here would
        # hand the entry-unit guard a timeline nobody opened.
        return getattr(self, "_current", None)

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        f'resolve: {{project_name: "Mock Project", '
        f'timeline_name: "{MASTER}"}}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")
    return root


def _moment():
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = 5
    moment.timeline_name = TARGET
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


def _pool_tree():
    from library.tools import resolve_bin_layout as bins
    root = FakeFolder("Master", "root")
    reels = FakeFolder(bins.REELS_BIN, "reels")
    current = FakeFolder(bins.REEL_STATE_BINS["current"], "cur")
    subs = FakeFolder(bins.SUBTITLES_BIN, "subs")
    unplaced = FakeFolder(bins.UNPLACED_BIN, "unplaced")
    root.subs = [reels, subs]
    reels.subs = [current]
    subs.subs = [unplaced]
    return root, current, unplaced


def test_refused_build_files_its_caption_imports(project_dir):
    """The defect: ImportMedia debris left in the current bin."""
    root, current, unplaced = _pool_tree()
    pool = FakePool(root, current)
    resolve_project = FakeProject(pool)

    # CreateEmptyTimeline tracks Resolve timelines; ImportMedia is on
    # the pool and lands in the current folder (Current plan).
    def _create(name):
        timeline = FakeResolveTimeline(name)
        resolve_project.timelines.append(timeline)
        return timeline

    pool.CreateEmptyTimeline = _create

    def _delete(timelines):
        for timeline in list(timelines):
            if timeline in resolve_project.timelines:
                resolve_project.timelines.remove(timeline)
        return True

    pool.DeleteTimelines = _delete

    stray_path = str(project_dir / "pipeline_output" / "steps"
                      / "4_05_render_subtitles" / "sub_reel-05-a.mov")

    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name == STAGING, f"expected staging, got {name!r}"
        pool.CreateEmptyTimeline(name)
        # The caption import the real placer does - lands in CURRENT.
        pool.ImportMedia([stray_path])
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    def _gate_failed_report():
        path = (project_dir / "pipeline_output" / "review"
                / "conformance_report.json")
        path.write_text(json.dumps({
            "has_errors": True,
            "findings": [
                {"severity": "error", "finding_class": "F17",
                 "message": "mixed-speaker card"},
                {"severity": "error", "finding_class": "F8",
                 "message": "end cuts mid-word"},
            ],
            # The rows name the STAGED container with its error
            # count - the shape the real gate writes - so the
            # refusal attributes to the reel that failed.
            "reels": [
                {"reel_name": STAGING,
                 "reel_number": 5,
                 "errors": 2,
                 "warnings": 0,
                 "captions": "0/0",
                 "findings": [
                     {"finding_class": "F17", "severity": "error"},
                     {"finding_class": "F8", "severity": "error"},
                 ]},
            ],
        }), encoding="utf-8")

    _gate_failed_report()

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment()]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=1):
        with pytest.raises(RuntimeError, match="defective timeline"):
            rebuild_reels_in_project(str(project_dir), only=[5])

    names = [t.GetName() for t in resolve_project.timelines]
    assert TARGET in names, "the approved reel must survive a refusal"
    assert STAGING not in names, "the refused staging must be gone"
    assert sorted(names) == sorted([MASTER, TARGET])

    # The debris is what this test is about: the imported caption clip
    # must be filed under the canonical unplaced bin, not left in the
    # current bin.
    current_names = [c.GetName() for c in current.GetClipList()]
    unplaced_names = [c.GetName() for c in unplaced.GetClipList()]
    assert "sub_reel-05-a.mov" not in current_names, (
        "a refused build stranded its caption import in the current bin")
    assert "sub_reel-05-a.mov" in unplaced_names, (
        "a refused build must file its caption imports under "
        "'Not placed on any timeline' once no timeline places them")
