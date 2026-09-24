"""Per-video preferences: the loader, the locks, and the selector seam.

The captain's Ren decision (2026-09-23, format A-YAML): one YAML
shape in two layers - project-level preferences that can be LOCKED
(`style.yaml` beside project.yaml, with a `locked:` list) and
per-video overrides (`video.yaml`: shared fields on top, per-reel
sections under `reels:`) that win only where the project did not
lock. Synthetic projects under `tmp_path`; nothing reaches a real
project.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composer as C  # noqa: E402
from library.tools import video_prefs as V  # noqa: E402


def _project(tmp_path, style_body=None, video_body=None):
    if style_body is not None:
        (tmp_path / "style.yaml").write_text(
            textwrap.dedent(style_body), encoding="utf-8")
    if video_body is not None:
        (tmp_path / "video.yaml").write_text(
            textwrap.dedent(video_body), encoding="utf-8")
    return str(tmp_path)


# ── The merge ─────────────────────────────────────────────────────

def test_video_layer_overrides_only_what_the_project_left_open(tmp_path):
    """An unlocked video value wins; a project-only value survives.

    If the merge ran the other way, the per-video file would be
    decoration - declared, read, and silently overruled.
    """
    folder = _project(
        tmp_path,
        """\
        style: calm-explainer
        delivery_format: vertical_1080x1920
        target_length_seconds: 60
        """,
        """\
        style: punchy-hook
        target_length_seconds: 45
        """)
    merged = V.load_video_preferences(folder)
    assert merged["style"] == "punchy-hook"
    assert merged["target_length_seconds"] == 45
    assert merged["delivery_format"] == "vertical_1080x1920"


def test_two_reels_of_one_project_resolve_different_overrides(tmp_path):
    """Each reel carries its own overrides over shared locked values.

    The failure it pins: a single video.yaml forcing every reel of a
    multi-reel project onto identical preferences - a second project
    layer in disguise, on which two reels of one footage pool could
    never differ.
    """
    folder = _project(
        tmp_path,
        """\
        delivery_format: vertical_1080x1920
        target_length_seconds: 60
        locked:
          - delivery_format
        """,
        """\
        reels:
          1:
            style: reel-one-hook
            target_length_seconds: 45
          2:
            style: reel-two-hook
        """)
    first = V.load_video_preferences(folder, reel=1)
    second = V.load_video_preferences(folder, reel=2)
    assert first["style"] == "reel-one-hook"
    assert first["target_length_seconds"] == 45
    assert second["style"] == "reel-two-hook"
    assert second["target_length_seconds"] == 60
    assert first["delivery_format"] == second["delivery_format"] == \
        "vertical_1080x1920"


def test_reel_with_no_section_contributes_nothing(tmp_path):
    """A reel that declares no overrides reads the shared layers.

    The failure it pins: an undeclared reel refusing (or inventing)
    rather than reading what the project and video declared for
    everybody - the reel key comes from machine data, not typing,
    so absence is ordinary, not an error.
    """
    folder = _project(
        tmp_path,
        "delivery_format: vertical_1080x1920\n",
        "reels:\n  1:\n    style: reel-one-hook\n")
    assert V.load_video_preferences(folder, reel=9) == \
        V.load_video_preferences(folder) == {
            "delivery_format": "vertical_1080x1920"}


# ── Absence ───────────────────────────────────────────────────────

def test_nothing_declared_means_none_and_todays_selection(tmp_path):
    """No files, no context, no routing change.

    If absence produced an empty-but-present context, every current
    selection would carry a style it never asked for - and a future
    branch on "context present" would fire on videos that declared
    nothing.
    """
    folder = _project(tmp_path)
    assert V.load_video_preferences(folder) is None
    assert V.build_style_context(folder) is None
    for node in ("catalog", "verify_reels", "select_reels"):
        plain = C.select_operation(node)
        assert plain.style is None
        assert C.select_operation(node, None, None, None) == plain


# ── Refusal ───────────────────────────────────────────────────────

def test_unknown_field_refuses_by_name(tmp_path):
    """A typo is a refusal naming the file and the field, not a
    silent drop - a dropped key reads as "the default stands" while
    the captain believes he declared otherwise."""
    folder = _project(tmp_path, None, "target_lenght_seconds: 60\n")
    with pytest.raises(V.VideoPreferencesError, match="video.yaml"):
        V.load_video_preferences(folder)


def test_unknown_delivery_format_refuses_by_name(tmp_path):
    """A format naming no delivery frame refuses with what exists -
    otherwise the run renders a frame nobody described."""
    folder = _project(
        tmp_path, "delivery_format: vertical-ish\n")
    with pytest.raises(V.VideoPreferencesError,
                       match="delivery_format.*vertical-ish"):
        V.load_video_preferences(folder)


def test_locked_field_refuses_by_name(tmp_path):
    """A video value on a locked field refuses naming the key and
    both files. The failure it pins: the lock silently losing (the
    video winning) or silently ignored (the project winning without
    saying the video tried)."""
    folder = _project(
        tmp_path,
        """\
        delivery_format: vertical_1080x1920
        locked:
          - delivery_format
        """,
        "delivery_format: horizontal_1920x1080\n")
    with pytest.raises(V.LockedVideoPreferenceError,
                       match="delivery_format"):
        V.load_video_preferences(folder)


def test_reel_override_of_a_locked_key_refuses(tmp_path):
    """The lock holds per reel too: a reel section setting a locked
    field refuses naming the key. The failure it pins: the project
    lock guarding the shared top level while each reel walks around
    it - locked for everybody means everybody."""
    folder = _project(
        tmp_path,
        """\
        delivery_format: vertical_1080x1920
        locked:
          - delivery_format
        """,
        """\
        reels:
          1:
            delivery_format: horizontal_1920x1080
        """)
    with pytest.raises(V.LockedVideoPreferenceError,
                       match="delivery_format"):
        V.load_video_preferences(folder, reel=1)


def test_locking_what_nothing_declares_refuses_by_name(tmp_path):
    """A lock naming no preference refuses - a lock on a typo would
    otherwise guard nothing while reading as protection."""
    folder = _project(tmp_path, "locked:\n  - delivery_formt\n")
    with pytest.raises(V.VideoPreferencesError,
                       match="delivery_formt"):
        V.load_video_preferences(folder)


# ── The selector seam ─────────────────────────────────────────────

def test_selector_echoes_declared_style_without_rerouting(tmp_path):
    """The context reaches the selection and routing stands.

    The failure it pins: the style argument accepted at the door
    and dropped before the selector - plumbed nowhere, honoured
    nowhere. Every node shape is covered: the sole-route return,
    a change-gate handler, and the representative fallback.
    """
    folder = _project(tmp_path, None, "style: calm-explainer\n")
    context = V.build_style_context(folder)
    assert context == {"style": "calm-explainer",
                       "preferences": {"style": "calm-explainer"},
                       "reel": None}
    for node in ("catalog", "verify_reels", "select_reels"):
        plain = C.select_operation(node)
        styled = C.select_operation(node, style=context)
        assert styled.style == context
        assert styled.operation == plain.operation
        assert styled.decided_by == plain.decided_by
        assert styled.reason == plain.reason


def test_compose_threads_style_to_every_selection(tmp_path):
    """`compose_with_change` carries the context to each node - a
    thread that stopped at the entry point would leave the per-node
    selector choosing blind while the signature promised otherwise.
    """
    folder = _project(tmp_path, None, "style: calm-explainer\n")
    context = V.build_style_context(folder)
    comp = C.compose_with_change("state.judge_reels.reel_selection",
                                 style=context)
    assert comp.completed
    (selection,) = comp.selection
    assert selection.operation == "reel.candidates"
    assert selection.style == context


def test_style_context_carries_the_reel_identity(tmp_path):
    """The addressed reel rides the context to the selection.

    The failure it pins: the reel known at the call site (the change
    spec's `reel`) never reaching the selector - a follow-up that
    branches per reel would have to re-derive what the loader
    already knew.
    """
    folder = _project(
        tmp_path, None,
        "reels:\n  1:\n    style: reel-one-hook\n")
    context = V.build_style_context(folder, reel=1)
    assert context["reel"] == 1
    assert context["style"] == "reel-one-hook"
    styled = C.select_operation("verify_reels", style=context)
    assert styled.style == context
    assert styled.operation == "reel.verify"
    comp = C.compose_with_change("state.judge_reels.reel_selection",
                                 style=context)
    (selection,) = comp.selection
    assert selection.style["reel"] == 1


# ── The soft target ───────────────────────────────────────────────

def test_target_length_is_carried_not_gated(tmp_path):
    """Roughly-60-seconds arrives as a number, never a verdict: a
    longer or shorter video with defensible quality is allowed, so
    the loader carries the seconds and refuses nothing on them."""
    folder = _project(
        tmp_path, None,
        "target_length_seconds: 60\n"
        "content_rules:\n"
        "  speakers_must_interact: [akshita, craig]\n"
        "  require_value_add: true\n"
        "  require_cta: true\n")
    merged = V.load_video_preferences(folder)
    assert merged["target_length_seconds"] == 60
    assert merged["content_rules"] == {
        "speakers_must_interact": ["akshita", "craig"],
        "require_value_add": True, "require_cta": True}


# ── Consumers: a declared preference wins, absence changes nothing ──

def test_delivery_format_pref_wins_over_project_yaml(tmp_path):
    """A declared frame beats the project.yaml fallback.

    The failure it pins: the preference loading but nothing reading
    it - the run rendering vertical while the project declared
    horizontal.
    """
    folder = _project(
        tmp_path, "delivery_format: horizontal_1920x1080\n")
    (tmp_path / "project.yaml").write_text(
        "pipeline:\n  delivery_format: vertical_1080x1920\n",
        encoding="utf-8")
    from library.tools.delivery_format import delivery_format_name
    assert delivery_format_name(folder) == "horizontal_1920x1080"


def test_no_pref_keeps_todays_delivery_default(tmp_path):
    """Nothing declared still ships vertical.

    The failure it pins: the wiring moving a current project off the
    frame it always rendered.
    """
    folder = _project(tmp_path)
    from library.tools.delivery_format import (
        DEFAULT_DELIVERY_FORMAT, delivery_format_name)
    assert delivery_format_name(folder) == DEFAULT_DELIVERY_FORMAT == \
        "vertical_1080x1920"


def test_subtitle_style_pref_wins_over_brand_effect(tmp_path):
    """A declared caption shape beats the template's.

    The failure it pins: the style loading but the captions rendering
    the template's shape anyway - `bold_large` (192) declared,
    `minimal` (120) drawn.
    """
    folder = _project(tmp_path, "subtitle_style: bold_large\n")
    from library.tools.subtitle_style import resolve_subtitle_style
    got = resolve_subtitle_style({"subtitle_style": "minimal"}, {}, folder)
    assert got["fontSize"] == 192


def test_no_pref_keeps_legacy_subtitle_shape(tmp_path):
    """Nothing declared still renders the legacy look."""
    folder = _project(tmp_path)
    from library.tools.subtitle_style import resolve_subtitle_style
    assert resolve_subtitle_style({}, {}, folder)["fontSize"] == 160


def test_color_grade_pref_resolves_like_project_yaml(tmp_path):
    """A declared grade is applied, preferring the video preference.

    The failure it pins: the grade loading but no clip ever wearing
    it - declared in style.yaml, resolved to disk here, applied by
    the same path the project.yaml grade takes.
    """
    (tmp_path / "pref.drx").write_bytes(b"DRX")
    (tmp_path / "proj.drx").write_bytes(b"DRX")
    folder = _project(
        tmp_path,
        "color_grade:\n"
        "  path: pref.drx\n"
        "  provenance:\n"
        "    source: GUI-built\n"
        "    authorised_by: captain\n"
        "    licence: own\n")
    import yaml
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"color": {"power_grade_drx": {
            "path": str(tmp_path / "proj.drx"),
            "provenance": {"source": "GUI-built",
                           "authorised_by": "captain",
                           "licence": "own"}}}}),
        encoding="utf-8")
    from library.tools.color_page_grade import resolve_color_page_grade
    assert resolve_color_page_grade(folder)["path"] == \
        str(tmp_path / "pref.drx")


def test_no_pref_grade_applies_nothing(tmp_path):
    """Nothing declared still grades nothing."""
    folder = _project(tmp_path)
    (tmp_path / "project.yaml").write_text(
        "pipeline: {}\n", encoding="utf-8")
    from library.tools.color_page_grade import resolve_color_page_grade
    assert resolve_color_page_grade(folder) is None


def test_soft_target_reaches_the_music_bridge(tmp_path):
    """The music catalogue serves the declared soft target.

    The failure it pins: the target loading but the choice still
    judged against the 60 s default - a 45 s video offered tracks
    that cannot cover it, or refused ones that can.
    """
    import importlib.util
    bridge_path = (REPO / "library" / "steps"
                   / "step_2_04_music_selection" / "bridge.py")
    spec = importlib.util.spec_from_file_location(
        "music_selection_bridge", str(bridge_path))
    music_bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(music_bridge)
    folder = _project(tmp_path, None, "target_length_seconds: 45\n")
    assert music_bridge._target_duration(
        {"project_folder": folder}) == 45.0


def test_select_reels_bridge_publishes_rules_and_soft_target(tmp_path):
    """The reel prompt sees the project's own rules and soft target.

    The failure it pins: content rules declared but the chooser never
    told - judging monologues as failed two-handers and lengths
    against 45-90 s while the project asked for something else.
    """
    import importlib.util
    bridge_path = (REPO / "library" / "steps"
                   / "step_3_04_select_reels" / "bridge.py")
    spec = importlib.util.spec_from_file_location(
        "select_reels_bridge", str(bridge_path))
    reels_bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reels_bridge)
    folder = _project(
        tmp_path, None,
        "target_length_seconds: 45\n"
        "content_rules:\n"
        "  speakers_must_interact: [akshita]\n"
        "  require_value_add: true\n"
        "  require_cta: false\n")
    out = reels_bridge.build_context(
        {"timeline_transcript": {}, "project_folder": folder})
    assert out["target_length_seconds"] == 45.0
    assert out["content_rules"] == {
        "speakers_must_interact": ["akshita"],
        "require_value_add": True, "require_cta": False}


def test_nothing_declared_publishes_nothing(tmp_path):
    """A video with no preferences offers the prompt no new tables."""
    import importlib.util
    bridge_path = (REPO / "library" / "steps"
                   / "step_3_04_select_reels" / "bridge.py")
    spec = importlib.util.spec_from_file_location(
        "select_reels_bridge_absence", str(bridge_path))
    reels_bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reels_bridge)
    folder = _project(tmp_path)
    out = reels_bridge.build_context(
        {"timeline_transcript": {}, "project_folder": folder})
    assert "target_length_seconds" not in out
    assert "content_rules" not in out



