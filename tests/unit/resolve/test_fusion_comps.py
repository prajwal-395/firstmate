"""Keyframes land inside the PLAYED window, not across the whole source.

History: docs/evidence/resolve_test_history.md#test_fusion_keyframe_range.
"""
import re
import pytest
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.effects import _reset_counters, fx
from library.tools.fusion.played_window import played_range
import hashlib
import json
import pathlib
from library.tools.custom_asset_bank import (
    bank_comp,
    comp_asset_key,
    get_asset_bank_dir,
    save_custom_asset,
)
from library.tools import resolve_lock
from library.tools.execution.apply_fusion_comps import (  # noqa: E402
    DestinationMismatchError,
    assert_destination,
    verify_destination,
    _map_clips_to_items,
)
from library.tools.fusion.comp_builder import (
    MissingSourceFrame)
from library.tools import comp_media_window as window


# ── Helpers ──────────────────────────────────────────────────

def _extract_spline_keyframes(comp: str, spline_name: str) -> list[int]:
    """Frame numbers from a named BezierSpline in a serialized comp."""
    pattern = rf'{re.escape(spline_name)}\s*=\s*BezierSpline\s*\{{'
    match = re.search(pattern, comp)
    if not match:
        return []
    # Depth-based brace matching to find the full block
    block_start = match.end()
    depth = 1
    pos = block_start
    while depth > 0 and pos < len(comp):
        if comp[pos] == '{': depth += 1
        elif comp[pos] == '}': depth -= 1
        pos += 1
    block = comp[block_start:pos]
    return [int(m.group(1)) for m in re.finditer(r'\[(\d+)\]', block)]


def _extract_all_keyframes(comp: str) -> dict[str, list[int]]:
    """All named BezierSplines and their keyframe positions."""
    result = {}
    for m in re.finditer(r'(\w+)\s*=\s*BezierSpline\s*\{', comp):
        name = m.group(1)
        frames = _extract_spline_keyframes(comp, name)
        if frames:
            result[name] = frames
    return result


# ── Hook scenario from project 001 ──────────────────────────
# 5657-frame source, segment plays frames 25-97 (2.4 seconds at 30fps)

HOOK_CLIP_DUR = 5657
HOOK_SOURCE_IN = 25
HOOK_SOURCE_OUT = 97
# The same window in the comp's own frames, which is where keyframes go.
HOOK_FIRST, HOOK_LAST = played_range(
    HOOK_CLIP_DUR, HOOK_SOURCE_IN, HOOK_SOURCE_OUT)


def test_zoom_and_transition_keyframes_land_inside_the_played_window():
    """slow_zoom_in 1.0 -> 1.03 on the hook clip, and every tail and
    head transition: a tail must end at the last played frame, not
    clip_dur-1 (past it, it fires after the clip is gone and Fusion
    holds it across everything that plays); a head must start at the
    first played frame, not frame 0."""
    from library.tools.fusion.nodes import BezierSpline

    def frames_of(block, what):
        splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
        assert splines, f"{what} has no spline"
        return [kf.frame for kf in splines[0].keyframes]

    window = dict(source_in=HOOK_SOURCE_IN, source_out=HOOK_SOURCE_OUT)
    _reset_counters()
    frames = frames_of(fx.zoom(HOOK_CLIP_DUR, start=1.0, mid=1.015,
                               end=1.03, **window), "zoom")
    assert HOOK_FIRST <= min(frames) and max(frames) <= HOOK_LAST, frames
    for ttype in ("fade_to_black", "defocus", "flash"):
        _reset_counters()
        tail = frames_of(fx.transition_tail(
            HOOK_CLIP_DUR, ttype, dur_frames=7, res=(1080, 1920), **window),
            f"{ttype} tail")
        assert max(tail) <= HOOK_LAST, (ttype, tail)
        _reset_counters()
        head = frames_of(fx.transition_head(
            HOOK_CLIP_DUR, ttype, dur_frames=7, res=(1080, 1920), **window),
            f"{ttype} head")
        assert min(head) >= HOOK_FIRST, (ttype, head)


class TestBuildEffectCompWithSourceWindow:
    """build_effect_comp places all keyframes within source_in..source_out."""

    def test_hook_zoom_and_defocus_within_played_window(self):
        """Reproduce the exact hook scenario from project 001.

        The hook is 2.4s (73 frames) taken from source seconds
        0.836-3.234 of a 5657-frame clip, i.e. source frames 25-97,
        which is comp frames 0-72.

        Two readings have been wrong here. Originally Transform1Size had
        keyframes at [0], [2828], [5656] and the defocus at [5646]..
        [5656] - across the whole source. Then they moved to [25]..[97],
        which is the source's numbering and still outside the 73 frames
        the comp renders.
        """
        effects = {
            'zoom_start': 1.0,
            'zoom_mid': 1.015,
            'zoom_end': 1.03,
            'tail_transition': 'defocus',
            'tail_transition_frames': 10,
            'source_in_frame': HOOK_SOURCE_IN,
            'source_out_frame': HOOK_SOURCE_OUT,
            'vignette': False,
        }
        comp = build_effect_comp(effects, HOOK_CLIP_DUR,
                                 source_res=(1920, 1080))

        all_kf = _extract_all_keyframes(comp)
        assert all_kf, "No keyframed splines found in comp"

        # The zoom spline must be entirely within the played window
        zoom_kf = all_kf.get("Transform1Size", [])
        assert zoom_kf, "No zoom keyframes"
        assert min(zoom_kf) >= HOOK_FIRST, (
            f"Zoom keyframe at comp frame {min(zoom_kf)} before the first "
            f"played frame ({HOOK_FIRST})"
        )
        assert max(zoom_kf) <= HOOK_LAST, (
            f"Zoom keyframe at comp frame {max(zoom_kf)} after the last "
            f"played frame ({HOOK_LAST})"
        )

        # The defocus transition's active frames must end at source_out.
        # BezierSpline.sampled may place a neutral hold_before at frame 0
        # (value=0.0 means no defocus), which is fine since frame 0 isn't
        # played and the held value is neutral.
        defocus_kf = all_kf.get("TransDefocus1Size", [])
        assert defocus_kf, "No defocus keyframes"
        assert max(defocus_kf) <= HOOK_LAST, (
            f"Defocus keyframe at comp frame {max(defocus_kf)} after the "
            f"last played frame ({HOOK_LAST}) - it fires after the clip "
            f"is gone"
        )
        # Active defocus frames (excluding the neutral hold at frame 0)
        # must start dur_frames back from the last played frame.
        active_kf = [f for f in defocus_kf if f > 0]
        assert active_kf, "Defocus has no active keyframes"
        assert min(active_kf) >= HOOK_LAST - 10, (
            f"Defocus active keyframe at {min(active_kf)} is far from the "
            f"last played frame ({HOOK_LAST})"
        )


class TestVfxLabelAtBoundary:
    """VFX at a clip boundary lands on the clip that starts there."""

    def test_float_boundary_prefers_next_clip(self):
        """Reproduce the speech_2/speech_3 bug.

        speech_2_seg0 ends at timeline_out=8.382000000000001.
        speech_3_seg0 starts at timeline_in=8.382.
        A VFX at timeline_start=8.38 should go to speech_3_seg0.

        Before the fix, _v1_label_at matched speech_2_seg0 because
        8.38 < 8.382000000000001 was True.
        """
        import library.steps.step_5_04_compile_manifest.step as cm
        _v1_label_at = cm._v1_label_at

        v1_clips = [
            {"label": "speech_2_seg0", "timeline_in": 5.553, "timeline_out": 8.382000000000001},
            {"label": "speech_3_seg0", "timeline_in": 8.382, "timeline_out": 18.397},
        ]

        # 8.38 is 2ms before speech_2's end - should match speech_3's start
        result = _v1_label_at(v1_clips, 8.38)
        assert result == "speech_3_seg0", (
            f"VFX at 8.38s matched {result!r} instead of speech_3_seg0 - "
            f"float boundary let the previous clip steal the assignment"
        )


class TestVfxCollisionAssertion:
    """The preservation assertion catches VFX collisions."""

    def test_collision_raises_and_absence_passes(self):
        """Two VFX on the same clip must fail the assertion; no
        collisions must not raise (B1 fold of `test_no_collision_passes`
        into its neighbouring collision test)."""
        import library.steps.step_5_04_compile_manifest.step as cm

        manifest = {
            "tracks": {
                "V2": {"clips": []},
                "A3": {"clips": []},
            },
            "fusion_effects": {"per_clip": {"clip_a": {}}},
        }
        with pytest.raises(ValueError, match="collision"):
            cm._assert_planner_output_preserved(
                manifest,
                broll_planned=0, sfx_planned=0, vfx_planned=2,
                broll_dropped_by_overlap=[],
                vfx_collisions=["vfx_002@8.38s collided on speech_2_seg0"],
            )

        manifest["fusion_effects"] = {"per_clip": {"clip_a": {}, "clip_b": {}}}
        cm._assert_planner_output_preserved(
            manifest,
            broll_planned=0, sfx_planned=0, vfx_planned=2,
            broll_dropped_by_overlap=[],
            vfx_collisions=[],
        )


# --------------------------------------------------------------------------
# From test_fusion_bank_reads_this_build.py
#
# The Fusion bank must hand back THIS build's comp, never a sibling's.
#
# History: docs/evidence/resolve_test_history.md#test_fusion_bank_reads_this_build.

STALE = "Composition { Version = \"a sibling build left this here\" }\n"


def _manifest():
    """One picture clip with a drift - the shape the reels ship."""
    return {
        "tracks": {"V1": {"clips": [
            {"source_file": "a_roll.mov", "label": "clip_0",
             "source_in": 12.0, "source_out": 15.0},
        ]}},
        "fusion_effects": {"per_clip": {
            "clip_0": {"_preset": "slow_zoom_in",
                       "zoom_start": 1.0, "zoom_end": 1.03},
        }},
    }


def _mock_resolve(monkeypatch, imported):
    """Resolve, reduced to the one call this asks about: what was imported."""
    import library.tools.execution.apply_fusion_comps as afc

    class MockTool:
        def __init__(self, reg_id):
            self._reg_id = reg_id

        def GetAttrs(self):
            return {"TOOLS_RegID": self._reg_id}

        def GetInput(self, name, time=None):
            return None

        def Delete(self):
            return True

    class MockComp:
        def __init__(self):
            self.locked = False

        def Lock(self):
            self.locked = True

        def Unlock(self):
            self.locked = False

        def AddTool(self, name):
            assert self.locked, "Fusion node creation must hold comp.Lock()"
            return MockTool(name)

        def GetToolList(self):
            return {"MediaIn1": MockTool("MediaIn"),
                    "Transform1": MockTool("Transform"),
                    "MediaOut1": MockTool("MediaOut")}

        def FindTool(self, name):
            return None

    class MockClip:
        def GetStart(self): return 0
        def GetEnd(self): return 72
        def GetDuration(self): return 72
        def GetMediaPoolItem(self): return MockPool()
        # Production ImportFusionComp creates the comp (finding 15).
        def GetFusionCompNameList(self):
            return ["Comp1"] if imported else []
        def DeleteFusionCompByName(self, name): pass

        def GetFusionCompByName(self, name):
            return MockComp() if imported else None

        def ImportFusionComp(self, path):
            # Read it here, as Resolve does: what reached the timeline
            # is the bytes at import time, not the path afterwards.
            imported.append(
                pathlib.Path(path).read_text(encoding="utf-8"))
            return True

    class MockPool:
        def GetClipProperty(self, prop):
            if prop == "File Path":
                return "a_roll.mov"
            if prop == "Frames":
                return "600"
            # A real MediaPoolItem states its stored frame; the
            # applier refuses a comp where Resolve will not state
            # one, so the mock states one like production does.
            if prop == "Resolution":
                return "1080x1920"
            return None

    class MockTimeline:
        def GetSetting(self, name): return "30"
        def GetItemListInTrack(self, track_type, index):
            return [MockClip()] if index == 1 else []

    class MockProject:
        def GetCurrentTimeline(self): return MockTimeline()

    class MockPM:
        def GetCurrentProject(self): return MockProject()

    class MockResolve:
        def GetProjectManager(self): return MockPM()
        def OpenPage(self, page): return True

    monkeypatch.setattr(afc.dvr, "scriptapp", lambda x: MockResolve())
    return afc


def _legacy_key(label, effects, clip_dur, source_res, played_frames):
    """The key the bank used until 2026-09-10, restated here.

    It is restated rather than imported because the point of this file is
    that it is gone: a test that called the live function would pass
    again the moment somebody reintroduced it.
    """
    fingerprint = json.dumps(
        {"effects": effects, "clip_dur": clip_dur,
         "source_res": list(source_res) if source_res else None,
         "played_frames": played_frames},
        sort_keys=True, default=str,
    )
    return f"{label.lower()}_{hashlib.sha1(fingerprint.encode()).hexdigest()[:12]}"


def test_a_comp_banked_under_the_old_key_never_reaches_the_timeline(
        monkeypatch, tmp_path):
    """Reel 09's defect, on the path that shipped it.

    A stale comp sits in the bank under every name the previous scheme
    could have chosen for this clip.  The build must still import what it
    generated.
    """
    imported = []
    afc = _mock_resolve(monkeypatch, imported)

    effects = dict(_manifest()["fusion_effects"]["per_clip"]["clip_0"])
    # The applier normalises before keying, and the old key was taken
    # after that; plant every plausible spelling rather than guess which.
    for res in (None, (1920, 1080), (3840, 2160)):
        for played in (None, 72):
            save_custom_asset(
                str(tmp_path),
                _legacy_key("clip_0", effects, 600, res, played),
                STALE)
    planted = list(pathlib.Path(get_asset_bank_dir(str(tmp_path))).glob("*.comp"))
    assert planted, "the stale comps must actually be on disk"

    assert afc.apply_fusion_comps(_manifest(), str(tmp_path),
                                  step_id="build_reels") is True

    assert len(imported) == 1
    body = imported[0]
    assert STALE not in body
    assert "Transform1 = Transform {" in body, (
        "the drift this build declared has to be in what was imported"
    )


def test_the_imported_comp_is_byte_for_byte_what_this_build_generated(
        monkeypatch, tmp_path):
    """Not "equivalent to" - the same bytes `build_effect_comp` emitted."""
    imported = []
    afc = _mock_resolve(monkeypatch, imported)
    assert afc.apply_fusion_comps(_manifest(), str(tmp_path),
                                  step_id="build_reels") is True

    banked = sorted(pathlib.Path(get_asset_bank_dir(str(tmp_path))).glob("*.comp"))
    assert len(banked) == 1
    body = imported[0]
    assert body == banked[0].read_text(encoding="utf-8")
    assert banked[0].name == comp_asset_key("clip_0", body) + ".comp"
    # And it carries the drift this manifest declared, out to 1.03.
    assert "Transform1Size = BezierSpline {" in body
    assert "1.03" in body


def test_bank_comp_refuses_to_hand_back_bytes_it_was_not_given(tmp_path):
    """A file squatting on the key is overwritten, not trusted.

    Only a hash collision or a corrupted bank puts different bytes at a
    content-addressed name, and neither is a reason to import them.
    """
    fresh = build_effect_comp({"vignette": True, "vignette_blend": 0.25,
                            "vignette_soft": 0.35}, 120, (1920, 1080))
    key = comp_asset_key("clip_0", fresh)
    save_custom_asset(str(tmp_path), key, STALE)

    path, reused = bank_comp(str(tmp_path), "clip_0", fresh)
    assert reused is False
    assert pathlib.Path(path).read_text(encoding="utf-8") == fresh


def test_a_second_build_of_the_same_comp_reuses_the_banked_file(tmp_path):
    """The bank still dedupes - it just cannot lie about what it holds."""
    fresh = build_effect_comp({"vignette": True, "vignette_blend": 0.25,
                            "vignette_soft": 0.35}, 120, (1920, 1080))
    first, reused_first = bank_comp(str(tmp_path), "clip_0", fresh)
    second, reused_second = bank_comp(str(tmp_path), "clip_0", fresh)
    assert (reused_first, reused_second) == (False, True)
    assert first == second


def test_the_old_input_only_key_is_gone():
    """`clip_asset_key` fingerprinted the request, not the answer.

    It was repaired twice by adding a field (`source_res`,
    `played_frames`) and both repairs left the trap armed, because the
    builder is not a field.  Reintroducing it under any name that keys a
    bank lookup reopens Reel 09.
    """
    import library.tools.custom_asset_bank as bank

    assert not hasattr(bank, "clip_asset_key")


# --------------------------------------------------------------------------
# From test_fusion_destination_guard.py
#
# Fusion subprocess destination guard - wrong timeline / project is refused.
#
# The Fusion subprocess (apply_fusion_comps.py) runs in a SEPARATE process
# from the timeline builder.  Between launch and first mutation, any of the
# eleven live davinci-resolve-mcp server processes could call
# SetCurrentTimeline or change the current project.  The guard verifies
# that the current project and timeline match what the parent told the
# subprocess to expect, IMMEDIATELY before any mutation, and refuses
# loudly on mismatch rather than writing Fusion comps onto the captain's
# rough cut.
#
# These tests use plain mock objects - no Resolve writes.

# ── Mock helpers ────────────────────────────────────────────────

@pytest.fixture
def unguarded(monkeypatch):
    """Undo conftest's session-wide sole-writer declaration.

    The suite declares itself the sole writer because its Resolve is a
    mock. A test ABOUT the guard has to stand outside that (same shape
    as `unguarded` in test_resolve_lock.py)."""
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)

class MockTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return str(id(self))


class MockProject:
    def __init__(self, name, timeline=None, timelines=None):
        self._name = name
        self._timeline = timeline
        self._timelines = (list(timelines) if timelines is not None
                           else ([timeline] if timeline is not None else []))
        self.set_calls = []

    def GetName(self):
        return self._name

    def GetCurrentTimeline(self):
        return self._timeline

    def SetCurrentTimeline(self, tl):
        self.set_calls.append(tl.GetName() if tl else None)
        self._timeline = tl
        return True

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        if 1 <= index <= len(self._timelines):
            return self._timelines[index - 1]
        return None


class MockProjectManager:
    def __init__(self, project=None):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class MockResolve:
    def __init__(self, pm=None):
        self._pm = pm or MockProjectManager()

    def GetProjectManager(self):
        return self._pm


class MockMediaPoolItem:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, prop):
        if prop == "File Path":
            return self._path


class MockTimelineItem:
    def __init__(self, mpi):
        self._mpi = mpi

    def GetMediaPoolItem(self):
        return self._mpi


# ── verify_destination tests ─────────────────────────────────────

def test_verify_destination_passes_only_the_named_project_and_timeline():
    edit = "Pipeline_Edit_20260901_45s"
    tl = MockTimeline(edit)
    proj = MockProject("Podcast", timeline=tl)
    resolve = MockResolve(pm=MockProjectManager(project=proj))
    assert verify_destination(resolve, "Podcast", edit) == (proj, tl)
    rows = (
        (MockProject("Some Other Project", timeline=tl),
         "Wrong Resolve project"),
        (MockProject("Podcast", timeline=MockTimeline("Rough Cut v3")),
         "Wrong timeline"),
        (None, "No Resolve project"),
        (MockProject("Podcast", timeline=None), "No timeline is current"),
    )
    for project, match in rows:
        resolve = MockResolve(pm=MockProjectManager(project=project))
        with pytest.raises(DestinationMismatchError, match=match):
            verify_destination(resolve, "Podcast", edit)


def test_clips_map_to_items_by_full_path_only():
    """Basename-only match is REJECTED - the H2 guard. The basename
    fallback was the exact defect: the captain's rough cut uses the same
    footage, so basename matches succeed against the wrong timeline. A
    matcher that succeeds against wrong material is worse than one that
    fails. Clips without a source_file are skipped."""
    def items(*paths):
        return [MockTimelineItem(MockMediaPoolItem(p)) for p in paths]

    assert _map_clips_to_items(
        [{"source_file": "/footage/clip_A.mov"},
         {"source_file": "/footage/clip_B.mov"}],
        items("/footage/clip_A.mov", "/footage/clip_B.mov")) == {0: 0, 1: 1}
    assert _map_clips_to_items(
        [{"source_file": "/pipeline/output/clip_A.mov"}],
        items("/footage/raw/clip_A.mov")) == {}
    assert _map_clips_to_items(
        [{"label": "intro"}, {"source_file": "/footage/clip_A.mov"}],
        items("/footage/clip_A.mov")) == {1: 0}
    assert _map_clips_to_items(
        [{"source_file": "/footage/clip_A.mov"}], []) == {}


# ── Subprocess CLI arg parsing ───────────────────────────────────


# ── assert_destination tests ─────────────────────────────────────

class TestAssertDestination:
    """The cursor is ASSERTED under lease, then read back - never assumed.

    2026-09-20: a duplicate-take rebuild died at its Fusion pass with
    a sibling lane's final current. Depending on ambient cursor state
    stalls every concurrent wave on a refusal; asserting it (exact
    name, same project) serialises lanes through the exclusive lease
    instead, while the read-back still refuses a mutator that never
    agreed to any lock.
    """

    @pytest.fixture(autouse=True)
    def _sole_writer(self):
        """The pass runs under its own exclusive lease in production;
        the mocks here have no instance to contend for."""
        from library.tools.resolve_lock import assume_sole_writer
        with assume_sole_writer(
                "test: mock project has no instance to contend for"):
            yield

    def _project(self, current, *timelines):
        proj = MockProject("Podcast", timeline=current,
                           timelines=list(timelines))
        return proj, MockResolve(MockProjectManager(project=proj))

    def test_sibling_timeline_current_asserts_onto_staging(self):
        """The measured incident: sibling's final current, staging set."""
        sibling = MockTimeline("Reel 05 - ai-cant-form-a-clear-picture-of-you")
        staging = MockTimeline("Reel 06 - size-doesnt-matter (rebuild staging)")
        proj, resolve = self._project(sibling, sibling, staging)
        returned_project, returned_timeline = assert_destination(
            resolve, "Podcast",
            "Reel 06 - size-doesnt-matter (rebuild staging)")
        assert returned_project is proj
        assert returned_timeline is staging
        assert proj.GetCurrentTimeline() is staging
        assert proj.set_calls == [staging.GetName()]
        # Idempotent: asserting the timeline already current verifies.
        proj, resolve = self._project(staging, staging)
        _, returned_timeline = assert_destination(
            resolve, "Podcast", staging.GetName())
        assert returned_timeline is staging

    def test_a_missing_staging_or_wrong_project_refuses_without_moving(self):
        """The staging is gone - refuse, and do not move the cursor."""
        sibling = MockTimeline("Reel 05 - ai-cant-form-a-clear-picture-of-you")
        proj, resolve = self._project(sibling, sibling)
        with pytest.raises(DestinationMismatchError, match="not found"):
            assert_destination(resolve, "Podcast", "Reel 06 - gone staging")
        assert proj.GetCurrentTimeline() is sibling
        assert proj.set_calls == []
        # Another project open: refuse before touching anything.
        tl = MockTimeline("Reel 06 - size-doesnt-matter (rebuild staging)")
        proj = MockProject("Some Other Project", timeline=tl,
                           timelines=[tl])
        resolve = MockResolve(MockProjectManager(project=proj))
        with pytest.raises(DestinationMismatchError,
                           match="Wrong Resolve project"):
            assert_destination(resolve, "Podcast", tl.GetName())
        assert proj.set_calls == []

    def test_mutator_between_set_and_verify_still_refuses(self):
        """The read-back stays: a move inside the assert-verify window fails.

        A mutator that never agreed to any lock cannot be serialised -
        the verify-immediately-before-mutation is what protects that
        write, and the assert must not swallow it.
        """
        staging = MockTimeline("Reel 06 - size-doesnt-matter (rebuild staging)")
        intruder = MockTimeline("Rough Cut v3")

        class RacyProject(MockProject):
            def SetCurrentTimeline(self, tl):
                super().SetCurrentTimeline(tl)
                self._timeline = intruder  # moved again before the read-back

        proj = RacyProject("Podcast", timeline=intruder,
                           timelines=[intruder, staging])
        resolve = MockResolve(MockProjectManager(project=proj))
        with pytest.raises(DestinationMismatchError, match="Wrong timeline"):
            assert_destination(resolve, "Podcast", staging.GetName())


class TestAssertDestinationNeedsALease:
    """The establishment goes through the guard, so without a lease it
    refuses BEFORE moving the cursor - the 2026-09-20 shape (an
    unleased move colliding with a sibling lane's pass) fails loud
    instead of landing somewhere wrong."""

    def test_assertion_without_a_lease_is_refused(self, unguarded):
        from library.tools import resolve_lock
        assert not resolve_lock.held()
        sibling = MockTimeline("Reel 05 - final")
        staging = MockTimeline("Reel 06 - staging")
        proj = MockProject("Podcast", timeline=sibling,
                           timelines=[sibling, staging])
        resolve = MockResolve(MockProjectManager(project=proj))
        with pytest.raises(resolve_lock.UnguardedPlacementError):
            assert_destination(resolve, "Podcast", staging.GetName())
        assert proj.GetCurrentTimeline() is sibling
        assert proj.set_calls == []


# --------------------------------------------------------------------------
# From test_comp_source_resolution.py
#
# Fusion Background nodes are built at the SOURCE frame, not the delivery frame.
#
# History: docs/evidence/resolve_test_history.md#test_comp_source_resolution.

APPLY_FUSION_COMPS = (pathlib.Path(__file__).resolve().parents[3]
                      / "library" / "tools" / "execution"
                      / "apply_fusion_comps.py")

CLIP_DUR = 120
LANDSCAPE = (1920, 1080)
VERTICAL = (1080, 1920)

# Every effect whose block contains a Background node.
BACKGROUND_EFFECTS = {
    "vignette": {"vignette": True, "vignette_blend": 0.25,
                 "vignette_soft": 0.35},
    "fade": {"fade_in_frames": 8, "fade_out_frames": 8, "vignette": False},
    "tail_fade_to_black": {"tail_transition": "fade_to_black",
                           "tail_transition_frames": 7, "vignette": False},
    "head_fade_to_black": {"head_transition": "fade_to_black",
                           "head_transition_frames": 7, "vignette": False},
}


def _background_sizes(comp: str):
    """(Width, Height) of every Background node in a serialized comp."""
    sizes = []
    for block in re.split(r"\bBackground\b", comp)[1:]:
        w = re.search(r"Width\s*=\s*Input\s*{\s*Value\s*=\s*([\d.]+)", block)
        h = re.search(r"Height\s*=\s*Input\s*{\s*Value\s*=\s*([\d.]+)", block)
        if w and h:
            sizes.append((int(float(w.group(1))), int(float(h.group(1)))))
    return sizes


def test_backgrounds_match_the_landscape_source():
    """A Background smaller than the frame is a hard-edged rectangle in
    the picture."""
    for name in sorted(BACKGROUND_EFFECTS):
        comp = build_effect_comp(dict(BACKGROUND_EFFECTS[name]), CLIP_DUR,
                                 source_res=LANDSCAPE)
        sizes = _background_sizes(comp)
        assert sizes, f"{name} drew no Background node"
        assert all(s == LANDSCAPE for s in sizes), (
            f"{name} drew {sizes} over a {LANDSCAPE} source")


def test_an_unknown_source_refuses_rather_than_guessing():
    """None means "could not tell" - and an unstated frame refuses.

    The builder used to fall back to a documented vertical default;
    a 3840x2160 source built at that size carries a hard-edged
    rectangle down the middle of the picture, so the fallback was a
    defect that shipped silently. The renderer reads the size off the
    MediaPoolItem and refuses where Resolve will not state one.
    """
    with pytest.raises(MissingSourceFrame, match="source_res"):
        build_effect_comp(
            {"vignette": True, "vignette_blend": 0.25,
             "vignette_soft": 0.35},
            CLIP_DUR, source_res=None)


def test_the_asset_bank_key_changes_with_the_source_frame():
    """Same effects, different frame - different comp, so different key.

    Without this a comp banked for a 1920x1080 clip would be replayed on
    a 1080x1920 one, putting the rectangle straight back.  The key is
    taken over the comp the builder really emitted, so this reads the
    difference off the bytes rather than off a restatement of the
    inputs.
    """
    effects = {"vignette": True, "vignette_blend": 0.25,
               "vignette_soft": 0.35}
    landscape = comp_asset_key("speech_3_seg0", build_effect_comp(
        dict(effects), CLIP_DUR, source_res=LANDSCAPE))
    vertical = comp_asset_key("speech_3_seg0", build_effect_comp(
        dict(effects), CLIP_DUR, source_res=VERTICAL))
    assert landscape != vertical


def test_the_renderer_reads_the_source_frame_off_the_media_pool_item():
    """`_source_resolution` judges Resolve by what it RETURNS.

    `hasattr` is always True on a Resolve proxy, so the reader has to
    handle a property that answers nothing - and answer None rather than
    a fabricated size.
    """
    # The module imports DaVinciResolveScript at import time, so the
    # function is read out of the source rather than imported.
    source = APPLY_FUSION_COMPS.read_text()
    body = source.split("def _source_resolution(mpi):", 1)[1].split("\ndef ", 1)[0]
    namespace = {}
    exec("def _source_resolution(mpi):" + body, namespace)
    read = namespace["_source_resolution"]

    class Item:
        def __init__(self, value):
            self.value = value

        def GetClipProperty(self, key):
            assert key == "Resolution"
            return self.value

    assert read(Item("1920x1080")) == (1920, 1080)
    assert read(Item("1080x1920")) == (1080, 1920)
    assert read(Item("")) is None
    assert read(Item(None)) is None
    assert read(Item("unknown")) is None
    assert read(Item("0x0")) is None
    assert read(None) is None


# --------------------------------------------------------------------------
# From test_comp_media_window.py
#
# A comp whose MediaIn does not cover its item's played frames REFUSES.
#
# The measurement these tests pin is in `library/tools/comp_media_window.py`:
# six of the captain's eight built reels carried a freeze tail whose comp
# is rendered over comp frames 0..18 while its `MediaIn` begins at comp
# frame 1, and every one of them FAILED a Deliver render at the hold's
# first frame. Conforming the window to 0/18 rendered 19 of 19.
#
# The numbers below are those reels' real numbers, not invented ones.

#: What the captain's built reels carry on the freeze tail, and what a
#: freshly imported comp of the same file reads back.
DRIFTED = {"MediaSource": "Timeline", "MediaID": "",
           "AudioTrack": "Timeline Audio", "GlobalIn": 1.0, "GlobalOut": 19.0,
           "ClipTimeStart": 0.0, "ClipTimeEnd": 18.0}
CONFORMED = dict(DRIFTED, GlobalIn=0.0, GlobalOut=18.0)
FREEZE_FRAMES = 19


# ── The predicate ───────────────────────────────────────────────────


def test_the_shipped_freeze_window_does_not_cover_its_first_frame():
    reason = window.uncovered_reason(DRIFTED, FREEZE_FRAMES)
    assert reason is not None
    assert "GlobalIn 1" in reason
    assert not window.covers(DRIFTED, FREEZE_FRAMES)


def test_a_long_clip_whose_window_starts_far_negative_is_covered():
    """Reel 01's head clip: left offset 3151 of a 5400-frame source.

    `GlobalIn` is well before comp frame 0 and `GlobalOut` well past the
    479 frames it plays. This is the shape the predicate must NOT flag,
    or it fails every correct A-roll clip on every reel.
    """
    a_roll = dict(CONFORMED, GlobalIn=-3151.0, GlobalOut=2248.0,
                  ClipTimeStart=-3151.0, ClipTimeEnd=2248.0)
    assert window.uncovered_reason(a_roll, 479) is None


def test_an_unreadable_window_is_not_a_finding():
    """A handle that would not answer is an absence, not a defect.

    AGENTS.md 10.4: a gate that FAILS correct output is no more coverage
    than one that cannot fail. `conform_item` reports the absence
    instead.
    """
    assert window.uncovered_reason(None, FREEZE_FRAMES) is None
    assert window.uncovered_reason({"GlobalIn": None, "GlobalOut": None},
                                   FREEZE_FRAMES) is None


# ── The conform: read, compare, repair, verify ──────────────────────


class _Tool:
    def __init__(self, values):
        self.values = values

    def GetAttrs(self, key):
        return "MediaIn" if key == "TOOLS_RegID" else None

    def GetInput(self, key):
        return self.values.get(key)


class _Comp:
    def __init__(self, values):
        self.tool = _Tool(values)

    def GetToolList(self, selected):
        return {1: self.tool}


class _Item:
    """A timeline item whose comp window is whatever the last import left.

    `repairs` is the window each successive `ImportFusionComp` produces,
    so a test can model the real repair (a re-import conforms it) and the
    one that must refuse (a re-import that changes nothing).
    """

    def __init__(self, windows):
        self.windows = list(windows)
        self.imported = []
        self.deleted = []

    def GetFusionCompByIndex(self, index):
        return _Comp(self.windows[0]) if self.windows[0] else None

    def GetFusionCompCount(self):
        return 1

    def GetFusionCompNameList(self):
        return ["Fusion Composition 1"]

    def DeleteFusionCompByName(self, name):
        self.deleted.append(name)

    def ImportFusionComp(self, path):
        self.imported.append(path)
        if len(self.windows) > 1:
            self.windows.pop(0)
        return True


def test_a_drifted_window_is_repaired_by_re_importing_the_banked_comp():
    item = _Item([DRIFTED, CONFORMED])
    receipt = window.conform_item(item, FREEZE_FRAMES, "banked.comp",
                                  label="freeze")
    assert receipt["repaired"] is True
    assert item.imported == ["banked.comp"]
    assert receipt["before"]["GlobalIn"] == 1.0
    assert receipt["after"]["GlobalIn"] == 0.0
    assert receipt["reason_after"] is None


def test_a_repair_that_did_not_take_REFUSES_rather_than_shipping():
    """The verify is a re-read, and it is allowed to fail.

    A conform that trusted its own repair would promote exactly the reel
    this module exists to stop.
    """
    item = _Item([DRIFTED])
    with pytest.raises(window.CompWindowUncovered) as caught:
        window.conform_item(item, FREEZE_FRAMES, "banked.comp",
                            label="Reel 01 freeze")
    assert "Reel 01 freeze" in str(caught.value)
    assert "GlobalIn 1" in str(caught.value)


# ── The diagnosis half: reading a built reel without rendering it ───


def test_reel_read_reports_the_uncovered_window_as_a_slice():
    """The reader owns the read (AGENTS.md 15); this module owns the law.

    `reel_read` already walks every clip on every track, so the
    diagnosis is a SLICE of that one read rather than a second walk -
    which is the rule `tests/unit/reels/test_reel_read.py` enforces.
    """
    from library.tools import reel_read

    result = {"tracks": [{"clips": [
        {"name": "LC4930.MXF", "track_type": "video", "track_index": 1,
         "record_in": 590, "record_out": 1069, "duration": 479,
         "fusion": {"comp_count": 1, "comp_names": ["c"], "media_windows": [
             {"comp_index": 1,
              "window": dict(CONFORMED, GlobalIn=-3151.0, GlobalOut=2248.0),
              "uncovered_reason": None}]}},
        {"name": "reel_freeze_6681969f12.mov", "track_type": "video",
         "track_index": 1, "record_in": 1255, "record_out": 1274,
         "duration": 19,
         "fusion": {"comp_count": 1, "comp_names": ["c"], "media_windows": [
             {"comp_index": 1, "window": DRIFTED,
              "uncovered_reason": window.uncovered_reason(
                  DRIFTED, FREEZE_FRAMES)}]}},
    ]}]}
    rows = reel_read.uncovered_comp_windows(result)
    assert len(rows) == 1
    assert rows[0]["clip"] == "reel_freeze_6681969f12.mov"
    assert rows[0]["record_in"] == 1255
    assert "GlobalIn 1" in rows[0]["uncovered_reason"]


# ── The wiring: the comp pass conforms what it imports ──────────────


def _manifest_2():
    return {
        "tracks": {"V1": {"clips": [
            {"source_file": "a_roll.mov", "label": "clip_0",
             "source_in": 12.0, "source_out": 15.0},
        ]}},
        "fusion_effects": {"per_clip": {
            "clip_0": {"_preset": "slow_zoom_in",
                       "zoom_start": 1.0, "zoom_end": 1.03},
        }},
    }


def _drive_comp_pass(monkeypatch, tmp_path, after_import):
    """Run the real comp pass against a clip whose window is what the LAST
    `ImportFusionComp` left behind - `after_import[0]` after the pass's own
    import, `after_import[1]` after the conform's repair."""
    import library.tools.execution.apply_fusion_comps as afc

    state = {"windows": [None], "pending": list(after_import), "imports": []}

    class MockTool:
        def __init__(self, reg_id):
            self._reg_id = reg_id

        def GetAttrs(self):
            return {"TOOLS_RegID": self._reg_id}

        def GetInput(self, name, time=None):
            return None

        def Delete(self):
            return True

    class MockComp:
        def __init__(self):
            self.locked = False

        def Lock(self):
            self.locked = True

        def Unlock(self):
            self.locked = False

        def AddTool(self, name):
            assert self.locked, "Fusion node creation must hold comp.Lock()"
            return MockTool(name)

        def Delete(self):
            return True

        def GetToolList(self):
            return {"MediaIn1": MockTool("MediaIn"),
                    "Transform1": MockTool("Transform"),
                    "MediaOut1": MockTool("MediaOut")}

        def FindTool(self, name):
            return None

    class MockClip:
        def GetStart(self): return 0
        def GetEnd(self): return 72
        def GetDuration(self): return 72
        def GetMediaPoolItem(self): return MockPool()
        # Production ImportFusionComp creates the comp: the name list
        # reads back what the import put there (finding 15 - an
        # import that leaves no comp behind fails the pass by name).
        def GetFusionCompNameList(self):
            return ["Comp1"] if state["imports"] else []
        def DeleteFusionCompByName(self, name): pass
        def GetFusionCompCount(self): return 1

        def GetFusionCompByIndex(self, index):
            return _Comp(state["windows"][0]) if state["windows"][0] else None

        def GetFusionCompByName(self, name):
            return MockComp() if state["imports"] else None

        def ImportFusionComp(self, path):
            state["imports"].append(pathlib.Path(path).name)
            if state["pending"]:
                state["windows"][0] = state["pending"].pop(0)
            return True

    class MockPool:
        def GetClipProperty(self, prop):
            if prop == "Resolution":
                # A real MediaPoolItem states its stored frame; the
                # applier refuses a comp where Resolve will not state
                # one, so the mock states one like production does.
                return "1080x1920"
            return {"File Path": "a_roll.mov", "Frames": "600"}.get(prop)

    class MockTimeline:
        def GetSetting(self, name): return "30"
        def GetItemListInTrack(self, kind, index):
            return [MockClip()] if index == 1 else []

    class MockProject:
        def GetCurrentTimeline(self): return MockTimeline()

    class MockPM:
        def GetCurrentProject(self): return MockProject()

    class MockResolve:
        def GetProjectManager(self): return MockPM()
        def OpenPage(self, page): return True

    monkeypatch.setattr(afc.dvr, "scriptapp", lambda name: MockResolve())
    return afc, state


def test_the_comp_pass_conforms_a_drifted_window_it_just_imported(
        monkeypatch, tmp_path):
    """Remove the conform from `apply_fusion_comps` and this test fails.

    The clip plays 72 frames and its freshly imported comp comes back
    covering 1..72 - the shipped freeze tail's defect, on the pass that
    writes it. One re-import conforms it, and the pass must make it.
    """
    drifted = dict(DRIFTED, GlobalIn=1.0, GlobalOut=72.0)
    conformed = dict(DRIFTED, GlobalIn=0.0, GlobalOut=71.0)
    afc, state = _drive_comp_pass(monkeypatch, tmp_path,
                                  [drifted, conformed])

    assert afc.apply_fusion_comps(_manifest_2(), str(tmp_path),
                                  step_id="build_reels") is True
    assert len(state["imports"]) == 2, (
        "the pass imported once and never read the window back - a comp "
        "whose MediaIn misses the frames its item plays fails the whole "
        "render job")
