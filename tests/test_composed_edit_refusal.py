"""The condition the captain attached, and every attempt to get round it.

**A clip whose played length changes must have its Fusion comp
RE-DERIVED through the builder, never restored from the capture.**  A
per-clip comp is keyed to the window of footage the item plays, so a
trim invalidates it: restoring the captured comp verbatim across a
13-frame extension measured **wrong on 489 of 492 frames** (mean
1.09/255, max 88) on a cross-render floor of exactly 0.0000.  Nothing in
the timeline's readable state says so.  It looks right.

And the structural half: **a composed edit that changes a played length
and cannot reach the comp generator must REFUSE, not restore the old
comp.**  The first composed edit that quietly skips the re-derivation
produces a reel that is wrong on 99% of a clip's frames and looks right,
which is worse than the slow path it replaces.

Every test below is an ATTEMPT TO BYPASS the refusal, by the route a
future caller would plausibly take:

  1. run the edit with no comp generator at all
  2. hand it a generator that cannot actually be reached
  3. build the capture by hand with the comp in it
  4. mutate a legitimate capture to carry the comp
  5. reach past the accessor for the withheld artefact on disk
  6. let the generator report success and do nothing
  7. let the generator run and leave the clip with no comp
  8. wire the restore to the withheld comp in the source itself

The last one is a source-level assertion, because it is the only one of
the eight that a test driving the module cannot reach.
"""

from __future__ import annotations

import ast
import dataclasses
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composed_edit as ce  # noqa: E402
from library.tools import reel_read  # noqa: E402
from tests.composed_edit_harness import build_reel  # noqa: E402

CUT = 1069
RESTORE = 13

MODULE = REPO / "library" / "tools" / "composed_edit.py"


def _dirs(tmp_path):
    return (str(tmp_path / "comps"), str(tmp_path / "withheld"))


def _trimmed(timeline):
    """The plan for the in-clip trim, and the one item it lengthens."""
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), CUT, RESTORE)
    head = next(c for c in changes
                if c.row == "V1" and c.played_length_changes)
    return changes, head


class _Generator(ce.CompRederiver):
    """A comp generator whose behaviour each bypass test dials in."""

    def __init__(self, reason=None, ran=True, ok=True, on_run=None):
        self.reason, self.ran, self.ok, self.on_run = reason, ran, ok, on_run
        self.calls = 0

    def reachable_reason(self, changes):
        return self.reason

    def rederive(self, changes):
        self.calls += 1
        if self.on_run is not None:
            self.on_run(changes)
        return {"ran": self.ran, "ok": self.ok}


# ── 1. No comp generator at all ─────────────────────────────────────


def test_bypass_1_no_generator_refuses_before_anything_is_destroyed(tmp_path):
    timeline, pool, _media = build_reel(tmp_path)
    changes, head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)

    with pytest.raises(ce.CompRederivationUnreachable) as refusal:
        ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                               changes=changes, comp_dir=comp_dir,
                               withheld_dir=withheld, rederiver=None)

    assert "489 of 492 frames" in str(refusal.value)
    assert f"V1[{head.item_index}] 479->492" in str(refusal.value)
    # The refusal is worth nothing if it lands after the delete.
    assert timeline.delete_calls == []
    assert pool.calls == []
    assert not Path(comp_dir).exists() and not Path(withheld).exists(), (
        "it captured before it checked")
    assert [i.GetDuration() for i in timeline.rows["V1"]] == [479, 186, 19]


def test_an_edit_that_changes_no_played_length_needs_no_generator(tmp_path):
    """The refusal must not fail correct output (AGENTS.md 10.4).

    A pure shift changes no played length, so the captured comp IS the
    comp the edit implies and no generator is required. A gate that
    refused this would be no better than one that cannot fail.
    """
    timeline, pool, _media = build_reel(tmp_path)
    changes = [c for c in ce.plan_ripple(reel_read.read_tracks(timeline),
                                         CUT, RESTORE)
               if not c.played_length_changes and c.row == "V4"]
    assert changes, "the fixture stopped producing pure shifts"
    comp_dir, withheld = _dirs(tmp_path)

    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool, changes=changes,
        comp_dir=comp_dir, withheld_dir=withheld, rederiver=None)

    assert receipt.rederivation_required == {"required": False, "trimmed": 0,
                                             "with_comps": 0, "insertions": 0}
    assert [i.GetStart() for i in timeline.rows["V4"]] == [1092, 1142, 1192]


def test_an_insertion_with_no_declared_treatment_refuses(tmp_path):
    """A new item comes back at IDENTITY, so its treatment is DECLARED.

    Measured on exported pixels: a 24-frame ending appended beside three
    shots carrying `ZoomX 2.307` rendered at `ZoomX 1.0` and diverged
    from a rebuild of the same edit on 100% of its own frames, mean
    21.8/255 - while all 264 frames before it were byte-identical. It
    placed, it verified, and it looked like a deliberate wide shot.

    The engine may not invent the framing (AGENTS.md 10.5), so the
    caller declares it - and `properties={}` is how identity is chosen
    on purpose, which is why this is a declaration and not a default.
    """
    timeline, pool, media = build_reel(tmp_path)
    comp_dir, withheld = _dirs(tmp_path)
    changes = ce.plan_ripple(reel_read.read_tracks(timeline), 1274, 24,
                             exclude=[("V1", 2)])
    undeclared = ce.Insertion(track_type="video", track_index=1,
                              media_pool_item=media["aroll"],
                              left_offset=4000, duration=24,
                              record_frame=1274, name="new ending")

    with pytest.raises(ce.InsertionUndeclared) as refusal:
        ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                               changes=changes, insertions=[undeclared],
                               comp_dir=comp_dir, withheld_dir=withheld,
                               rederiver=_Generator())
    assert "comes back" in str(refusal.value)
    assert "'ZoomX': 2.307" in str(refusal.value), (
        "the refusal does not say what its neighbours carry")
    assert timeline.delete_calls == []

    # Identity, chosen on purpose, is accepted.
    chosen = dataclasses.replace(undeclared, properties={})
    ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                           changes=changes, insertions=[chosen],
                           comp_dir=comp_dir, withheld_dir=withheld,
                           rederiver=_Generator())
    assert [i.GetStart() for i in timeline.rows["V1"]] == [590, 1069, 1255, 1274]




# ── 2. A generator that cannot be reached ───────────────────────────


def test_bypass_2_an_unreachable_generator_refuses_by_its_own_reason(tmp_path):
    timeline, pool, _media = build_reel(tmp_path)
    changes, _head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)

    with pytest.raises(ce.CompRederivationUnreachable) as refusal:
        ce.apply_composed_edit(
            timeline=timeline, media_pool=pool, changes=changes,
            comp_dir=comp_dir, withheld_dir=withheld,
            rederiver=_Generator(reason="the manifest declares no per-clip "
                                        "Fusion effects"))
    assert "no per-clip Fusion effects" in str(refusal.value)
    assert timeline.delete_calls == []




def test_a_trimmed_comp_on_a_row_the_pass_never_writes_is_refused(tmp_path):
    """The pass writes comps on V1 and V2 (`execution/fusion_tracks.py`).

    A comp on any other row cannot be re-derived through it, so an edit
    that trims one has no route to the generator for that clip and must
    say so - rather than running the pass, watching it succeed, and
    leaving the stale comp in place.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    changes, head = _trimmed(timeline)
    on_v4 = dataclasses.replace(head, track_index=4, item_index=0,
                                comp_count=1)
    manifest = {"tracks": {"V1": {"clips": [{"source_file": "/lab/a.mov"}] * 3}},
                "fusion_effects": {"per_clip": {"a_roll_0": {"glow_gain": 1.4}}}}
    rederiver = ce.ReelLookRederiver(manifest, str(tmp_path), "P", "T")

    assert rederiver.reachable_reason(changes) is None
    reason = rederiver.reachable_reason(list(changes) + [on_v4])
    assert "V4 carries a comp on a clip this edit trims" in reason


# ── 3 and 4. Putting the comp into the capture by hand ──────────────


def test_bypass_3_a_hand_built_capture_with_the_comp_is_refused(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    _changes, head = _trimmed(timeline)
    comp = ce.CapturedComp(index=1, path=str(tmp_path / "stale.comp"),
                           window={"GlobalIn": 0})

    with pytest.raises(ce.CompRestoreRefused) as refusal:
        ce.ItemCapture(change=head, properties={}, geometry={},
                       media_pool_item=None, restorable_comps=(comp,))
    assert "489 of 492 frames" in str(refusal.value)
    assert "RE-DERIVED through the builder" in str(refusal.value)


def test_bypass_4_mutating_a_real_capture_to_carry_the_comp_is_refused(tmp_path):
    """`dataclasses.replace` is the obvious route, and it goes through
    `__post_init__` like any other construction."""
    timeline, _pool, _media = build_reel(tmp_path)
    _changes, head = _trimmed(timeline)
    capture = ce.capture_item(timeline.rows["V1"][head.item_index], head,
                              *_dirs(tmp_path))
    assert capture.restorable_comps == ()
    assert len(capture.withheld_comps) == 1

    with pytest.raises(ce.CompRestoreRefused):
        dataclasses.replace(capture,
                            restorable_comps=capture.withheld_comps)


# ── 5. Reaching for the artefact on disk ────────────────────────────


def test_bypass_5_the_stale_comp_is_not_in_the_restore_directory(tmp_path):
    """The restore looks in `comp_dir`. A stale comp is never written there.

    Withholding the ARTEFACT, not only the accessor: there is nothing in
    the directory the restore reads for it to find, whatever a caller
    believes about the data model.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    _changes, head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)
    capture = ce.capture_item(timeline.rows["V1"][head.item_index], head,
                              comp_dir, withheld)

    assert sorted(Path(comp_dir).iterdir()) == []
    written = sorted(Path(withheld).iterdir())
    assert len(written) == 1
    assert "WITHHELD_stale_across_trim" in written[0].name
    assert capture.withheld_comps[0].path == str(written[0])
    # And a shift's comp DOES land in the restore directory, so the
    # withholding is about the length change and not about comps.
    shift = next(c for c in _changes
                 if not c.played_length_changes and c.comp_count)
    ce.capture_item(timeline.rows[shift.row][shift.item_index], shift,
                    comp_dir, withheld)
    assert len(sorted(Path(comp_dir).iterdir())) == 1




# ── 6 and 7. A generator that runs and does not deliver ─────────────


def test_bypass_6_a_generator_that_reports_success_and_does_nothing(tmp_path):
    """The timeline is the verdict, not the pass's own report."""
    timeline, pool, _media = build_reel(tmp_path)
    changes, _head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)
    generator = _Generator()          # reports ran/ok, touches nothing

    with pytest.raises(ce.CompRederivationNotProven) as refusal:
        ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                               changes=changes, comp_dir=comp_dir,
                               withheld_dir=withheld, rederiver=generator)
    assert generator.calls == 1
    assert "carries no comp after the pass" in str(refusal.value)
    assert "keyed to the length it used to play" in str(refusal.value)


def test_bypass_7_a_generator_that_leaves_an_uncovered_window(tmp_path):
    """A comp whose MediaIn does not cover the frames the item now plays.

    Resolve does not draw black there: it FAILS the whole render job at
    the first uncovered frame (`comp_media_window`).
    """
    timeline, pool, _media = build_reel(tmp_path)
    changes, _head = _trimmed(timeline)
    comp_dir, withheld = _dirs(tmp_path)

    def _short(_changes):
        from tests.composed_edit_harness import FakeComp, FakeTool
        for row in ("V1", "V3"):
            for item in timeline.rows[row]:
                item.comps = [FakeComp({"MediaIn1": FakeTool("MediaIn", {
                    "MediaSource": "MediaPool", "MediaID": "x",
                    "AudioTrack": "No_Audo_Track", "GlobalIn": 1,
                    "GlobalOut": 400, "ClipTimeStart": 1,
                    "ClipTimeEnd": 400})})]

    with pytest.raises(ce.CompRederivationNotProven) as refusal:
        ce.apply_composed_edit(timeline=timeline, media_pool=pool,
                               changes=changes, comp_dir=comp_dir,
                               withheld_dir=withheld,
                               rederiver=_Generator(on_run=_short))
    assert "GlobalIn 1 is past comp frame 0" in str(refusal.value)




# ── 8. The one bypass only the source can rule out ──────────────────


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name)


def test_bypass_8_the_restore_imports_only_from_restorable_comps():
    """The one `ImportFusionComp` in the restore iterates the accessor.

    A future edit that pointed it at `withheld_comps`, or at a path
    computed some other way, would reinstate exactly the defect this
    whole path exists to avoid - and every behavioural test above would
    still pass, because the data model would be satisfied. So it is
    pinned at the source.
    """
    restore = _function("restore_item")

    imports = [node for node in ast.walk(restore)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "ImportFusionComp"]
    assert len(imports) == 1, "the restore has more than one route to a comp"

    loops = [node for node in ast.walk(restore)
             if isinstance(node, ast.For)
             and any(isinstance(inner, ast.Call)
                     and isinstance(inner.func, ast.Attribute)
                     and inner.func.attr == "ImportFusionComp"
                     for inner in ast.walk(node))]
    assert len(loops) == 1
    iterated = loops[0].iter
    assert isinstance(iterated, ast.Attribute)
    assert iterated.attr == "restorable_comps", (
        f"the comp import iterates {ast.dump(iterated)}, not the accessor "
        f"that is empty across a played-length change")

    names = {node.attr for node in ast.walk(restore)
             if isinstance(node, ast.Attribute)}
    assert "withheld_comps" not in names, (
        "the restore path reads the withheld comp - the artefact is "
        "kept for diagnosis and must stay unreachable from here")


def test_the_refusal_runs_before_the_capture_in_the_orchestrator():
    """Order is the guarantee: a refusal after the delete is no refusal.

    Pinned at the source because the behavioural test above can only
    show that the timeline survived ONE refusal - this shows the check
    cannot be moved below the destructive calls without failing.
    """
    body = _function("apply_composed_edit").body
    positions = {}
    for index, node in enumerate(body):
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                if inner.func.id in ("assert_rederivation_reachable",
                                     "capture_item", "delete_all",
                                     "place_all"):
                    positions.setdefault(inner.func.id, index)
    assert set(positions) == {"assert_rederivation_reachable", "capture_item",
                              "delete_all", "place_all"}
    assert (positions["assert_rederivation_reachable"]
            < positions["capture_item"] < positions["delete_all"]
            < positions["place_all"]), positions




