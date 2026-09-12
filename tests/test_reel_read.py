"""The one read sees every marker level - especially the clip one.

The exact miss that started this task: a throwaway probe reading
`Timeline.GetMarkers()` only reported a reel as having no markers when
its notes sat on its clips. `reel_read.read_reel` must see the clip
marker alongside the timeline-level ones, because its marker half IS
`marker_feedback.read_notes`.
"""

import ast
from pathlib import Path

from library.tools import reel_read
from library.tools import reel_replace_guard as guard
from library.tools.timeline_serializer import serialize_timeline_state

REEL = "Reel 09 - moment"


class _Pool:
    def __init__(self, path):
        self._path = path
        self._markers = {
            150: {"color": "Blue", "name": "pool note",
                  "note": "typed on the file", "duration": 1,
                  "customData": ""},
        }

    def GetClipProperty(self, name):
        return {"File Path": self._path, "Frames": "5000",
                "Comments": ""}.get(name, "")

    def GetMarkers(self):
        return dict(self._markers)

    def GetMediaId(self):
        return "media-1"


class _Item:
    def __init__(self, name, start, end, left, pool, extra_markers=None,
                 fusion=0, source=None):
        self._name = name
        self._start, self._end, self._left = start, end, left
        self._pool = pool
        self._markers = {
            # The pool's own copy, already collected at pool level and
            # correctly not reported twice ...
            150: {"color": "Blue", "name": "pool note",
                  "note": "typed on the file", "duration": 1,
                  "customData": ""},
            # ... and the captain's own note ON THIS CLIP. This is the
            # marker the throwaway probe missed.
            200: {"color": "Red", "name": "fix the cut",
                  "note": "trim the head", "duration": 1,
                  "customData": ""},
        }
        if extra_markers is not None:
            self._markers = extra_markers
        self._fusion = fusion
        self._source = source or (pool.GetClipProperty("File Path")
                                  if pool else "")

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetSourceStartFrame(self):
        return self._left

    def GetSourceEndFrame(self):
        return self._left + (self._end - self._start)

    def GetLeftOffset(self):
        return self._left

    def GetRightOffset(self):
        return 0

    def GetUniqueId(self):
        return f"uid-{self._name}"

    def GetClipColor(self):
        return ""

    def GetFlagList(self):
        return []

    def GetClipEnabled(self):
        return True

    def GetProperty(self):
        return {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0,
                "Opacity": 100.0}

    def GetCDL(self):
        return None

    def GetColorGroup(self):
        return ""

    def GetFusionCompCount(self):
        return self._fusion

    def GetFusionCompNameList(self):
        return [f"comp-{self._name}"] if self._fusion else []

    def GetMarkers(self):
        return dict(self._markers)

    def GetMediaPoolItem(self):
        return self._pool


class _Timeline:
    def __init__(self, items_by_track):
        self._tracks = items_by_track
        self._markers = {
            50: {"color": "Green", "name": "overall",
                 "note": "nice pacing", "duration": 1, "customData": ""},
        }

    def GetName(self):
        return REEL

    def GetSetting(self, key):
        return {"timelineFrameRate": "24",
                "timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920"}.get(key, "")

    def GetStartFrame(self):
        return 108000

    def GetEndFrame(self):
        return 109000

    def GetStartTimecode(self):
        return "01:00:00:00"

    def GetTrackCount(self, track_type):
        return len(self._tracks.get(track_type, {}))

    def GetTrackName(self, track_type, index):
        names = list(self._tracks.get(track_type, {}))
        return names[index - 1]

    def GetItemListInTrack(self, track_type, index):
        names = list(self._tracks.get(track_type, {}))
        return list(self._tracks[track_type][names[index - 1]])

    def GetMarkers(self):
        return dict(self._markers)


def _reel(tmp_path):
    pool = _Pool("/footage/craig.mov")
    clip = _Item("craig-take", 108100, 108300, 100, pool)
    return _Timeline({"video": {"V1": [clip]}}), str(tmp_path)


def test_the_reader_sees_the_clip_marker_alongside_timeline_ones(tmp_path):
    timeline, project_folder = _reel(tmp_path)
    result = reel_read.read_reel(timeline, "Pipeline_Edit",
                                 project_folder=project_folder)
    by_source = {}
    for note in reel_read.markers_of(result):
        by_source.setdefault(note["source"], []).append(note)
    assert set(by_source) >= {"timeline_marker", "clip_marker",
                              "media_pool_marker"}
    clip_notes = by_source["clip_marker"]
    assert len(clip_notes) == 1
    assert clip_notes[0]["name"] == "fix the cut"
    assert clip_notes[0]["note"] == "trim the head"
    assert clip_notes[0]["frame"] == 108100 + (200 - 100)
    assert clip_notes[0]["color"] == "Red"
    assert clip_notes[0]["attached_clip"]["name"] == "craig-take"
    assert by_source["timeline_marker"][0]["frame"] == 108000 + 50


def test_clips_carry_ranges_source_ranges_and_transforms(tmp_path):
    timeline, project_folder = _reel(tmp_path)
    result = reel_read.read_reel(timeline, "Pipeline_Edit",
                                 project_folder=project_folder)
    clips = reel_read.clips_of(result)
    assert len(clips) == 1
    clip = clips[0]
    assert (clip["record_in"], clip["record_out"]) == (108100, 108300)
    assert clip["source_file"] == "/footage/craig.mov"
    assert clip["transform"]["ZoomX"] == 1.0
    assert clip["fusion"] == {"comp_count": 0, "comp_names": [],
                              # Where each comp's MediaIn reads from, and
                              # whether it covers the frames the item
                              # plays (`library/tools/comp_media_window.py`).
                              # No comps here, so no rows.
                              "media_windows": []}
    assert result["fps"] == 24
    assert (result["width"], result["height"]) == (1080, 1920)


def test_the_guard_takes_its_rows_from_the_one_reader(tmp_path):
    timeline, project_folder = _reel(tmp_path)
    result = reel_read.read_reel(timeline, "Pipeline_Edit",
                                 project_folder=project_folder)
    assert guard.snapshot_timeline(timeline, REEL) == reel_read.rows_of(result)
    rows = reel_read.rows_of(result)
    assert rows["video:V1"]["count"] == 1
    assert rows["video:V1"]["items"][0]["name"] == "craig-take"


def test_the_serializer_projects_the_same_reading(tmp_path):
    timeline, project_folder = _reel(tmp_path)

    class _Resolve:
        def GetProjectManager(self):
            return self

        def GetCurrentProject(self):
            return self

        def GetCurrentTimeline(self):
            return timeline

    state = serialize_timeline_state(resolve_mock=_Resolve())
    clips = state["tracks"][0]["clips"]
    assert clips[0]["name"] == "craig-take"
    assert clips[0]["transform"]["ZoomX"] == 1.0
    assert clips[0]["markers"] == [{
        "frame": 150, "color": "Blue", "name": "pool note",
        "note": "typed on the file", "duration": 1, "custom_data": "",
    }, {
        "frame": 200, "color": "Red", "name": "fix the cut",
        "note": "trim the head", "duration": 1, "custom_data": "",
    }]


def test_full_mode_measures_ink_from_pixels_not_from_the_gain(tmp_path):
    from PIL import Image

    artefact = tmp_path / "caption.png"
    canvas = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    pixels = canvas.load()
    for x in range(50, 120):
        for y in range(10, 40):
            pixels[x, y] = (255, 255, 255, 255)
    canvas.save(artefact)

    pool = _Pool("/footage/craig.mov")
    clip = _Item("craig-take", 108100, 108300, 100, pool)
    overlay_pool = _Pool(str(artefact))
    overlay = _Item("caption", 108100, 108200, 0, overlay_pool,
                    extra_markers={}, fusion=2)
    timeline = _Timeline({"video": {"V1": [clip], "V2": [overlay]}})

    quick = reel_read.read_reel(timeline, "Pipeline_Edit",
                                project_folder=str(tmp_path))
    assert reel_read.overlays_of(quick) == []

    full = reel_read.read_reel(timeline, "Pipeline_Edit",
                               mode=reel_read.FULL,
                               project_folder=str(tmp_path))
    overlays = reel_read.overlays_of(full)
    by_clip = {row["clip"]: row for row in overlays}
    assert by_clip["craig-take"]["ink"]["measured"] is False
    assert by_clip["craig-take"]["ink"]["reason"] == "artefact not on disk"
    ink = by_clip["caption"]["ink"]
    assert ink["measured"] is True
    assert ink["ink_box_xyxy"] == [50, 10, 120, 40]
    assert reel_read.fusion_of(full)[0]["comp_count"] == 2
    # The gain is gone (PR 1005 removed `tight_box.draw_gain` and deleted
    # the script that derived from it) and must never creep back in: a
    # measurement derived from the value it checks cannot contradict it.
    # (Docstring *mentions* of the prohibition are fine; code uses are not.)
    tree = ast.parse(Path(reel_read.__file__).read_text(encoding="utf-8"))
    code_uses = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in node.names:
                if "tight_box" in (name.name or ""):
                    code_uses.add(name.name)
            if isinstance(node, ast.ImportFrom) and "tight_box" in (
                    node.module or ""):
                code_uses.add(node.module)
        if isinstance(node, ast.Name) and node.id == "draw_gain":
            code_uses.add("draw_gain")
        if (isinstance(node, ast.Attribute)
                and node.attr == "draw_gain"):
            code_uses.add("draw_gain")
    assert code_uses == set()


# ── The enforceable half: no new probe ────────────────────────────
#
# A rule nobody can check loses to whatever is quicker, which is exactly
# what happened. A direct `GetMarkers` / `GetItemListInTrack` call outside
# the modules below is a new probe by another name: take a slice of
# `reel_read` instead. `reel_replace_guard` is deliberately ABSENT - it
# takes its rows from the one reader, and this test proves it.
#
# Deferred by name (a full migration is too large for this task): every
# other module below still reads Resolve objects directly.
# Write-path confirmation reads (`marker_resolution`, `mark_master`)
# re-resolve a single key before deleting, which is not a probe.
READER_MODULES = {
    "library/tools/reel_read.py",
    "library/tools/marker_feedback.py",
    "library/tools/marker_resolution.py",
    "library/tools/timeline_ingest.py",
    "library/tools/timeline_serializer.py",
    "library/tools/marker_capture.py",
    "library/tools/marker_carry.py",
    "library/tools/timeline_decisions.py",
    "library/tools/timeline_conformance.py",
    "library/tools/timeline_qa.py",
    "library/tools/captain_edits.py",
    "library/tools/capture_fusion_comps.py",
    "library/tools/overlay_placement.py",
    "library/tools/reel_build.py",
    "library/tools/reel_look.py",
    "library/tools/qa/timeline_sync_qa.py",
    "library/tools/execution/apply_fusion_comps.py",
    "library/tools/execution/mark_master.py",
    "library/tools/execution/organise_media_pool.py",
    "library/steps/step_6_01_render/probe_resolve_capabilities.py",
    "library/steps/step_6_01_render/resolve_build_timeline.py",
}

_WATCHED = ("GetMarkers", "GetItemListInTrack")


def _direct_reads(path: Path) -> set:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _WATCHED
    }


def test_no_module_outside_the_readers_touches_resolve_directly():
    root = Path(__file__).resolve().parents[1]
    violators = {}
    for path in sorted((root / "library").rglob("*.py")):
        reads = _direct_reads(path)
        if reads and str(path.relative_to(root)) not in READER_MODULES:
            violators[str(path.relative_to(root))] = sorted(reads)
    assert violators == {}, (
        "new direct Resolve reads outside the reader modules - take a "
        f"slice of reel_read instead: {violators}")


def test_the_guard_takes_no_direct_read():
    root = Path(__file__).resolve().parents[1]
    assert _direct_reads(root / "library/tools/reel_replace_guard.py") == set()
