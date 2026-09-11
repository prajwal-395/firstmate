"""The reel build CONSULTS the ending and the caption-timing owners.

A router nobody is forced to call is a document, not a mechanism
(`library/tools/edit_depth.py`). `tests/test_reel_ending.py` and
`tests/test_caption_timing.py` prove each owner does what it says;
this file proves the BUILD asks them, and fails the day a refactor
stops asking. The reel build path needs Resolve, a project and a
rendered caption set, so the consultation is asserted structurally -
on the call graph of `rebuild_reels_in_project` and on the seam each
call sits at - which is the same evidence
`tests/test_orphan_wiring.py::test_builder_threading_names_ranges`
accepts for the trims beside it.
"""

import ast
import inspect

import pytest

from library.tools import reel_build
from library.tools import reel_ending


SOURCE = inspect.getsource(reel_build)


def _function(name):
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not in reel_build")


def _calls(node):
    """Every `a.b(...)` call spelled inside a function, as 'a.b'."""
    out = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and isinstance(
                func.value, ast.Name):
            out.append(f"{func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name):
            out.append(func.id)
    return out


# ── The ending owner ───────────────────────────────────────────────

def test_the_rebuild_resolves_applies_and_checks_the_ending():
    calls = _calls(_function("rebuild_reels_in_project"))
    for spelled in ("_reel_ending.load_endings",
                    "_reel_ending.resolve_ending",
                    "_reel_ending.apply_ending",
                    "_reel_ending.report",
                    "_reel_ending.assert_tail_fits"):
        assert spelled in calls, (
            f"{spelled} is no longer called by rebuild_reels_in_project: "
            f"the ending owner has become a document again.")


def test_the_ending_lands_on_the_ranges_before_anything_derives():
    """An ending is a decision about the last keep range, so it must
    land where the trims land - before cards, captions, explainers and
    placements read the ranges. Asserted by ORDER in the source, the
    way the trim seam's own comment states the rule."""
    body = SOURCE[SOURCE.index("def rebuild_reels_in_project"):]
    apply_at = body.index("_reel_ending.apply_ending")
    assert apply_at < body.index("cards = plan_cards(")
    assert apply_at < body.index("reel_subtitle_segments(")
    assert body.index("_edits.retime_ranges(") < apply_at, (
        "the ending must be applied AFTER the captain's trims: both "
        "move the same ranges and the trims are the finer edit.")


def test_the_declared_ending_reaches_the_fusion_pass():
    """The tail element is DECLARED, not a side effect of whichever
    clip sorts last: `fusion_manifest` is handed the ending."""
    assert "ending" in inspect.signature(
        __import__("library.tools.reel_look", fromlist=["x"])
        .fusion_manifest).parameters
    assert "ending" in inspect.signature(
        __import__("library.tools.reel_look", fromlist=["x"])
        .power_effects).parameters
    source = inspect.getsource(
        __import__("library.tools.reel_look", fromlist=["x"])
        .fusion_manifest)
    assert "ending=ending" in source
    body = SOURCE[SOURCE.index("def rebuild_reels_in_project"):]
    manifest_at = body.index("_look.fusion_manifest(")
    window = body[manifest_at:manifest_at + 1200]
    assert "ending=" in window
    # And the declared FREEZE joins the manifest's picture, or the tail
    # element arms on the live tail the hold was moved off.
    assert "_with_freeze(" in window


def test_the_tail_element_decides_what_draws_over_the_tail():
    """The behavioural half of the wiring, run for real: a declared
    `none` draws nothing on the last clip, a declared switch-off draws
    it, and no declaration keeps the unconditional arm that shipped
    before this existed."""
    from library.tools import reel_look

    unconditional = reel_look.power_effects({}, "first", "last")
    assert unconditional["last"]["tv_power_tail"] is True
    declared = reel_look.power_effects(
        {}, "first", "last",
        ending={"reel": "R", "ends_on": {"anchor_phrase": "x"},
                "tail_element": "tv_power_tail", "reason": "r"})
    assert declared["last"]["tv_power_tail"] is True
    silent = reel_look.power_effects(
        {}, "first", "last",
        ending={"reel": "R", "ends_on": {"anchor_phrase": "x"},
                "tail_element": "none", "reason": "r"})
    assert "last" not in silent
    # The switch-ON is never the ending's business.
    for out in (unconditional, declared, silent):
        assert out["first"]["tv_power_head"] is True


def test_reel13s_defect_cannot_pass_the_check_that_now_runs():
    """The exact numbers off the captain's timeline: Craig's twelve
    frames could not carry the eighteen-frame switch-off, and the
    build now refuses instead of dropping it to stderr."""
    ending = {"reel": "Reel 13", "ends_on": {"anchor_phrase": "our bio"},
              "tail_element": "tv_power_tail", "reason": "captain"}
    fps = 24000 / 1001
    craig = [{"source_in": 0.0, "source_out": 12 / fps}]
    with pytest.raises(reel_ending.TailElementHasNoRoom):
        reel_ending.assert_tail_fits(craig, ending, fps)
    akshita = [{"source_in": 0.0, "source_out": 310 / fps}]
    assert reel_ending.assert_tail_fits(
        akshita, ending, fps)["shot_frames"] == 310


# ── The caption-timing owner ───────────────────────────────────────

def test_the_rebuild_loads_and_applies_the_caption_pins():
    calls = _calls(_function("rebuild_reels_in_project"))
    for spelled in ("_caption_timing.load_pins",
                    "_caption_timing.apply_pins",
                    "_caption_timing.report"):
        assert spelled in calls, (
            f"{spelled} is no longer called by rebuild_reels_in_project: "
            f"a caption-only trim has nothing holding it again.")


def test_the_pins_are_applied_after_rendering_and_before_placing():
    body = SOURCE[SOURCE.index("def rebuild_reels_in_project"):]
    apply_at = body.index("_caption_timing.apply_pins")
    assert body.index("reel_subtitle_segments(") < apply_at, (
        "the pins move RENDERED segments; applying them before the "
        "render would move nothing.")
    assert apply_at < body.index("build_reel_timeline("), (
        "the pins must land before the placer reads timeline_start.")


def test_both_declarations_are_read_once_before_any_timeline_exists():
    """A malformed declaration must stop the whole build, not the
    twelfth reel of nineteen - the rule every other declaration in
    this builder follows."""
    body = SOURCE[SOURCE.index("def rebuild_reels_in_project"):]
    for spelled in ("_reel_ending.load_endings(",
                    "_caption_timing.load_pins("):
        assert body.index(spelled) < body.index("for moment in building:"), (
            f"{spelled} is read inside the per-reel loop")


# ── Promotion says what it is about to discard ─────────────────────

def test_promotion_reads_carries_and_reports_the_captains_markers():
    calls = _calls(_function("promote_staged_reels"))
    for spelled in ("_markers.read_markers", "_markers.plan_carry",
                    "_markers.report", "_markers.place"):
        assert spelled in calls, (
            f"{spelled} is no longer called by promote_staged_reels: a "
            f"promotion can silently destroy the captain's words again.")


def test_the_markers_are_read_before_anything_is_renamed():
    body = SOURCE[SOURCE.index("def promote_staged_reels"):]
    assert body.index("_markers.read_markers") < body.index(
        "SetName(backups[final])"), (
        "the captain's words must be in hand before the object that "
        "carries them is moved aside.")
    assert body.index("_markers.report(") < body.index(
        "SetName(backups[final])"), (
        "a promotion about to discard a note must say so even if the "
        "rename below then refuses.")


# ── The verifier reads the same owners the builder does ────────────

def test_the_verifier_derives_the_plan_through_both_owners():
    """A reel cannot be built to one rule and CHECKED against another -
    the conformance verifier's own premise. Without this the re-derived
    plan still reaches past the master's cut (PLAN-MISMATCH) and every
    pinned caption reads as "planned and never placed" (F2/F14)."""
    from library.tools import reel_conformance_verifier as verifier

    derive = inspect.getsource(verifier._derive_plan_from_master)
    assert "_ending.resolve_ending(" in derive
    assert "_ending.apply_ending(" in derive
    # At the same seam the builder uses: after the closer joins the
    # ranges, before placements and cards read them.
    assert derive.index("_ending.apply_ending(") < derive.index(
        "compute_cards(")
    assert derive.index("kr = list(kr) + [closer]") < derive.index(
        "_ending.apply_ending(")

    retime = inspect.getsource(verifier._retime_planned_captions)
    assert "_caption_timing.load_pins(" in retime
    # The OWNER's join and applier, on step 4.01's own entry shape -
    # the same call the builder makes before it digests the caption
    # provenance hash, so plan and record are retimed by one piece of
    # code rather than two that can drift.
    assert "_caption_timing.retime_entries(" in retime
    assert "_caption_timing.retime_entries(" in inspect.getsource(
        reel_build.rebuild_reels_in_project)
    assert "_retime_planned_captions(" in inspect.getsource(
        verifier._derive_planned_captions)


def test_a_declared_short_card_is_reported_and_an_authored_one_fails():
    """The captain trimmed his last closer card to three frames and
    recorded why. A gate that FAILS correct output is no more coverage
    than one that cannot fail - but only a RECORDED pin reaches the
    exemption."""
    from library.tools import reel_conformance_verifier as verifier

    fps = 24000 / 1001
    cards = [{"text": "the link's in our bio", "reel_start": 1900 / fps,
              "reel_end": 1903 / fps, "frames": 3},
             {"text": "a flash nobody chose", "reel_start": 500 / fps,
              "reel_end": 503 / fps, "frames": 3}]
    findings = verifier.check_short_captions(
        "Reel 13", cards, fps, declared_short=((1900, 3),))
    assert len(findings) == 2
    by_text = {f.detail["text"][:10]: f for f in findings}
    declared = by_text["the link's"]
    assert declared.severity == "warning" and declared.detail["declared"]
    assert "caption_timing.json" in declared.message
    authored = by_text["a flash no"]
    assert authored.severity == "error" and not authored.detail["declared"]
    # With no declaration at all, the floor is exactly as hard as it was.
    assert all(f.severity == "error"
               for f in verifier.check_short_captions("Reel 13", cards, fps))


# ── The freeze reaches the timeline, the comp and the plan ─────────

def test_the_build_plans_renders_places_and_inherits_the_freeze():
    """The captain's ruling was that the freeze be DECLARED, not
    hand-placed. These are the five things the build must do with the
    declaration; a refactor that drops any of them puts the reel back
    to a switch-off over the closing line."""
    build = inspect.getsource(reel_build.build_reel_timeline)
    for spelled in ("_ending_owner.plan_freeze(",
                    "_ending_owner.render_freeze(",
                    "_ending_owner.freeze_placement(",
                    "_inherit_freeze_treatment("):
        assert spelled in build, (
            f"{spelled} is no longer called by build_reel_timeline: the "
            f"declared freeze is a document again.")
    # The artefact reaches the POOL before the picture loop asks for it:
    # that loop finds media by path and skips what it cannot find.
    assert build.index("import_pool_item(\n                pool, freeze_tail") \
        < build.index("for p in placements_list:")
    # And the hold joins the placements BEFORE the track plan, so the
    # TV frame carries over it and the placer puts it on the right row.
    assert build.index("placements_list.append(") < build.index(
        "angles = reel_angles(master_clips)")


def test_the_freeze_inherits_rather_than_recomputing_its_treatment():
    """A freeze IS the ending shot's last frame. Its own aim would be a
    fresh face probe and the captain's word-anchored Pan hold cannot
    reach it - Reel 13 would jump from Pan -12 to Pan 46 on its final
    frames. Inherited, read back, and the grade copied whole."""
    source = inspect.getsource(reel_build._inherit_freeze_treatment)
    assert "shot.CopyGrades(" in source
    assert "held.SetProperty(" in source and "held.GetProperty()" in source
    # Read back and REFUSED on a mismatch, never assumed.
    assert "raise ReelBuildError" in source
    assert "GRADE NOT COPIED" in source


def test_the_verifier_counts_the_held_frames_in_the_plan():
    """The hold is a picture clip the viewer watches, so a derived plan
    that leaves it out reports the reel 18 frames and one item short
    and PLAN-MISMATCH refuses the whole grading."""
    from library.tools import reel_conformance_verifier as verifier

    derive = inspect.getsource(verifier._derive_plan_from_master)
    assert "_ending.plan_freeze(" in derive
    assert "_ending.freeze_placement(" in derive
    assert "freeze_seconds" in derive
    # Re-derived, never RENDERED: the verifier reads.
    assert "render_freeze" not in derive


def test_the_tail_element_arms_on_the_hold_not_the_live_tail():
    """The join that decides whether the switch-off plays over her last
    words or after them."""
    from library.tools import reel_look

    ending = {"reel": "R", "ends_on": {"anchor_phrase": "x"},
              "tail_element": "tv_power_tail", "tail_hold": "freeze",
              "reason": "r"}
    live = {"clip": type("C", (), {"source_file": "/f/shot.mov",
                                   "track_index": 1, "speaker": "A",
                                   "track_type": "video"})(),
            "source_in": 0.0, "source_out": 310 / 24.0,
            "record": 0.0, "snapped_record": 0}
    freeze = reel_ending.plan_freeze([live], ending, 24.0)
    with_hold = reel_build._with_freeze([live], freeze, 24.0)
    assert len(with_hold) == 2 and with_hold[-1]["freeze"] is True
    manifest = reel_look.fusion_manifest(with_hold, {}, [], 24.0,
                                         ending=ending)
    per_clip = manifest["fusion_effects"]["per_clip"]
    # The HOLD is clip 01 and carries the switch-off; the live tail
    # (clip 00) carries only the switch-on, because it is also first.
    assert per_clip["reel_picture_01"]["tv_power_tail"] is True
    assert "tv_power_tail" not in per_clip["reel_picture_00"]
    assert per_clip["reel_picture_00"]["tv_power_head"] is True
    # Without the hold the element falls back onto the live tail, which
    # is what the captain saw across his closing line.
    alone = reel_look.fusion_manifest([live], {}, [], 24.0, ending=ending)
    assert alone["fusion_effects"]["per_clip"][
        "reel_picture_00"]["tv_power_tail"] is True


def test_no_declaration_adds_no_freeze_anywhere():
    assert reel_build._with_freeze([{"a": 1}], None, 24.0) == [{"a": 1}]


def test_a_declared_treatment_that_will_not_draw_refuses_the_build():
    """The deeper half of the captain's ruling. An element nobody
    declared may be undone - it was the look's own offer. One the
    project DECLARED may not: Reel 13's switch-off was dropped twice
    with one line in a build log nobody read, and both times the reel
    shipped without the thing that was asked for."""
    from library.tools import reel_ending
    from library.tools.execution import apply_fusion_comps

    # The plan carries the declaration beside the key, so the renderer
    # can tell the two apart AFTER the undo has stripped the key.
    keys = reel_ending.tail_effects(
        {"tail_element": "tv_power_tail"})
    assert keys["tv_power_tail_declared"] is True
    source = inspect.getsource(apply_fusion_comps)
    assert "declared_treatments" in source
    assert "REFUSING to build" in source
    # Read BEFORE the undo, or the key it keys on is already gone.
    assert source.index("declared_treatments = {") < source.index(
        "effects, tv_rows = verify_and_undo(")


def test_the_hold_is_one_frame_longer_than_its_ramp():
    """Measured 2026-09-11: an 18-frame switch-off on an 18-frame hold
    is undone as `never_settles`; 19 draws. The arithmetic lives with
    the check that enforces it, not in the owner that mints the room."""
    from library.tools.fusion.played_window import frames_for_ramp
    from library.tools.treatment_verify import verify_treatment
    from library.tools.tv_power import switch_off_frames

    timing = dict(switch_off_frames())
    ramp = sum(timing[k] for k in
               ("collapse_frames", "dot_frames", "decay_frames"))
    effects = {"tv_power_tail": True, "tv_power_tail_timing": timing}
    assert not verify_treatment(
        effects, "tv_power_tail", clip_dur=ramp, played_frames=ramp,
        source_res=(3840, 2160))["passed"]
    needed = frames_for_ramp(ramp)
    assert verify_treatment(
        effects, "tv_power_tail", clip_dur=needed, played_frames=needed,
        source_res=(3840, 2160))["passed"]
