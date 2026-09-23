"""Per-video preferences: one YAML shape in two layers.

The captain's Ren decision (2026-09-23, format A-YAML): a general
VIDEO preferences shape with two layers - PROJECT-level preferences
that can be LOCKED, and per-video preferences that may override only
what the project has not locked. A locked value a video tries to
override refuses by name rather than silently winning or losing.

The project layer is `style.yaml` beside `project.yaml` - the file
the approved board showed. Its top level declares the fields below
plus an optional `locked:` list of field names. The video layer is
an optional `video.yaml` in the same folder: shared fields on top,
plus an optional `reels:` mapping keyed by reel, so two reels of
one multi-reel project can differ on anything the project left
open. A single-video project (the edit_video process) addresses no
reel and reads the project layer plus the video top level only.

Resolution for reel R, weakest to strongest: style.yaml fields,
video.yaml top-level fields, video.yaml `reels[R]` fields. The lock
list is enforced against both video sub-layers. Only the loader and
schema ship here, never his values and never a house default. A
video that declares nothing keeps today's behaviour exactly:
`load_video_preferences` returns None.

Fields (each must be DECLAREABLE here; honouring them is follow-up
except where the PR body names a consumer):

- `style`: the style id `composer.select_operation` reads.
- `delivery_format`: a key of `delivery_format.DELIVERY_FORMATS`.
- `color_grade`: the `color.power_grade_drx` mapping shape (`path`
  required; deeper checks stay with `resolve_color_page_grade`).
- `subtitle_style`: a name `subtitle_style.get_subtitle_style`
  resolves.
- `target_length_seconds`: a SOFT target, never a gate. A longer or
  shorter video with defensible quality is allowed; a consumer that
  refuses on length mistakes a preference for a requirement.
- `content_rules`: `speakers_must_interact` (names), plus the
  `require_value_add` and `require_cta` flags.
"""

import os
from typing import Any, Dict, List, Optional

STYLE_FILENAME = "style.yaml"
VIDEO_FILENAME = "video.yaml"

VIDEO_PREF_FIELDS = (
    "style",
    "delivery_format",
    "color_grade",
    "subtitle_style",
    "target_length_seconds",
    "content_rules",
)

CONTENT_RULE_FIELDS = (
    "speakers_must_interact",
    "require_value_add",
    "require_cta",
)

COLOR_GRADE_KEYS = ("path", "provenance", "cdl_node")


class VideoPreferencesError(Exception):
    """A preferences declaration that refuses by name."""


class LockedVideoPreferenceError(VideoPreferencesError):
    """A video-layer value the project layer locked."""


def _read_yaml_mapping(path: str, what: str) -> Optional[Dict[str, Any]]:
    """The YAML mapping at `path`, None when the file is absent."""
    if not os.path.exists(path):
        return None
    try:
        import yaml
    except ImportError:
        raise VideoPreferencesError(
            f"{path} cannot be read: PyYAML is required.")
    with open(path, encoding="utf-8") as handle:
        try:
            data = yaml.safe_load(handle)
        except Exception as exc:
            raise VideoPreferencesError(
                f"{path} is not valid YAML ({exc}).") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise VideoPreferencesError(
            f"{what} in {path} must be a mapping, got "
            f"{type(data).__name__}.")
    return data


def _check_colour_grade(value: Any, where: str) -> dict:
    """The `color.power_grade_drx` shape, or a refusal naming it."""
    if not isinstance(value, dict):
        raise VideoPreferencesError(
            f"{where} declares `color_grade` as "
            f"{type(value).__name__}, not a mapping. It takes the "
            f"`color.power_grade_drx` shape: `path` plus `provenance`.")
    unknown = sorted(set(value) - set(COLOR_GRADE_KEYS))
    if unknown:
        raise VideoPreferencesError(
            f"{where} declares `color_grade` with {unknown}, which "
            "nothing reads. Known: `path`, `provenance`, `cdl_node`.")
    grade_path = value.get("path")
    if not grade_path or not isinstance(grade_path, str):
        raise VideoPreferencesError(
            f"{where} declares `color_grade` with no `path`. A grade "
            "nobody can find is a grade nobody can review.")
    return dict(value)


def _check_content_rules(value: Any, where: str) -> dict:
    """The content-rules mapping, or a refusal naming it."""
    if not isinstance(value, dict):
        raise VideoPreferencesError(
            f"{where} declares `content_rules` as "
            f"{type(value).__name__}, not a mapping.")
    unknown = sorted(set(value) - set(CONTENT_RULE_FIELDS))
    if unknown:
        raise VideoPreferencesError(
            f"{where} declares `content_rules` with {unknown}, which "
            "nothing reads. Known: "
            "`speakers_must_interact`, `require_value_add`, "
            "`require_cta`.")
    checked = dict(value)
    if "speakers_must_interact" in checked:
        names = checked["speakers_must_interact"]
        if (not isinstance(names, list) or not names
                or any(not isinstance(n, str) or not n.strip()
                       for n in names)):
            raise VideoPreferencesError(
                f"{where} declares `content_rules."
                "speakers_must_interact` as "
                f"{names!r}. It names the speakers that must interact "
                "- a non-empty list of names.")
    for flag in ("require_value_add", "require_cta"):
        if flag in checked and not isinstance(checked[flag], bool):
            raise VideoPreferencesError(
                f"{where} declares `content_rules.{flag}` as "
                f"{checked[flag]!r}, not true or false.")
    return checked


def _check_fields(prefs: dict, where: str) -> Dict[str, Any]:
    """One mapping's preference fields, validated by name."""
    unknown = sorted(set(prefs) - set(VIDEO_PREF_FIELDS))
    if unknown:
        raise VideoPreferencesError(
            f"{where} declares {unknown}, which nothing reads. "
            f"Known: {list(VIDEO_PREF_FIELDS)}.")
    checked: Dict[str, Any] = {}
    if "style" in prefs:
        style = prefs["style"]
        if not style or not isinstance(style, str):
            raise VideoPreferencesError(
                f"{where} declares `style` as {style!r}. It is the id "
                "the selector reads - a non-empty string.")
        checked["style"] = style
    if "delivery_format" in prefs:
        from library.tools.delivery_format import DELIVERY_FORMATS
        name = prefs["delivery_format"]
        if name not in DELIVERY_FORMATS:
            raise VideoPreferencesError(
                f"{where} declares `delivery_format` {name!r}, which "
                "names no delivery frame. Known: "
                f"{sorted(DELIVERY_FORMATS)}.")
        checked["delivery_format"] = name
    if "color_grade" in prefs:
        checked["color_grade"] = _check_colour_grade(
            prefs["color_grade"], where)
    if "subtitle_style" in prefs:
        from library.tools.subtitle_style import get_subtitle_style
        name = prefs["subtitle_style"]
        try:
            get_subtitle_style(name)
        except Exception as exc:
            raise VideoPreferencesError(
                f"{where} declares `subtitle_style` {name!r}, which "
                f"resolves nothing ({exc}).") from None
        checked["subtitle_style"] = name
    if "target_length_seconds" in prefs:
        length = prefs["target_length_seconds"]
        if (isinstance(length, bool)
                or not isinstance(length, (int, float))
                or length <= 0):
            raise VideoPreferencesError(
                f"{where} declares `target_length_seconds` as "
                f"{length!r}. It is a SOFT target in seconds - a "
                "positive number, never a gate.")
        checked["target_length_seconds"] = length
    if "content_rules" in prefs:
        checked["content_rules"] = _check_content_rules(
            prefs["content_rules"], where)
    return checked


def _check_locked(locked: Any, where: str) -> List[str]:
    """The project lock list, or a refusal naming it."""
    if locked is None:
        return []
    if (not isinstance(locked, list)
            or any(not isinstance(k, str) for k in locked)):
        raise VideoPreferencesError(
            f"{where} declares `locked` as "
            f"{locked!r}. It is a list of field names.")
    unknown = sorted(set(locked) - set(VIDEO_PREF_FIELDS))
    if unknown:
        raise VideoPreferencesError(
            f"{where} locks {unknown}, which names no preference. "
            f"Lockable: {list(VIDEO_PREF_FIELDS)}.")
    return list(locked)


def _read_style_layer(project_folder: str) -> tuple:
    """The project layer: `(fields, locked)` from style.yaml."""
    path = os.path.join(project_folder, STYLE_FILENAME)
    raw = _read_yaml_mapping(path, "style preferences")
    if not raw:
        return {}, []
    locked = _check_locked(raw.get("locked"), path)
    fields = {k: v for k, v in raw.items() if k != "locked"}
    return _check_fields(fields, path), locked


def _read_video_layers(project_folder: str) -> tuple:
    """The video layer: `(shared fields, {reel: fields})`."""
    path = os.path.join(project_folder, VIDEO_FILENAME)
    raw = _read_yaml_mapping(path, "video preferences")
    if not raw:
        return {}, {}
    shared = {k: v for k, v in raw.items() if k != "reels"}
    checked_shared = _check_fields(shared, path)
    reels = raw.get("reels")
    if reels is None:
        return checked_shared, {}
    if not isinstance(reels, dict):
        raise VideoPreferencesError(
            f"{path} declares `reels` as {type(reels).__name__}, "
            "not a mapping of reel to preferences.")
    checked_reels = {}
    for key, section in reels.items():
        where = f"{path} reel {key!r}"
        if not isinstance(section, dict):
            raise VideoPreferencesError(
                f"{where} must be a mapping of preferences, got "
                f"{type(section).__name__}.")
        checked_reels[str(key)] = _check_fields(section, where)
    return checked_shared, checked_reels


def _refuse_locked(video_prefs: dict, locked: list,
                   video_path: str, style_path: str) -> None:
    """A video value on a locked field refuses naming key and files."""
    clashes = sorted(set(video_prefs) & set(locked))
    if clashes:
        raise LockedVideoPreferenceError(
            f"{video_path} sets {clashes}, which {style_path} "
            "locks. A locked value stays the project's: change the "
            "lock, not the video.")


def load_video_preferences(
        project_folder: str, reel=None) -> Optional[Dict[str, Any]]:
    """The merged video preferences, or None when none are declared.

    Weakest to strongest: style.yaml fields, video.yaml top-level
    fields, video.yaml `reels[R]` fields when `reel` names reel R -
    the reel identity the caller has (the Ren change spec's `reel`
    where a change drives the read; absent for a single-video
    project). The style.yaml lock list is enforced against both
    video sub-layers. A reel with no section contributes nothing
    rather than refusing: it declares no overrides.
    """
    if not project_folder:
        return None
    style_path = os.path.join(project_folder, STYLE_FILENAME)
    video_path = os.path.join(project_folder, VIDEO_FILENAME)
    project_prefs, locked = _read_style_layer(project_folder)
    shared, per_reel = _read_video_layers(project_folder)
    _refuse_locked(shared, locked, video_path, style_path)
    reel_prefs: Dict[str, Any] = {}
    if reel is not None:
        reel_prefs = per_reel.get(str(reel), {})
    _refuse_locked(reel_prefs, locked, video_path, style_path)

    merged = {**project_prefs, **shared, **reel_prefs}
    if not merged:
        return None
    return merged


def build_style_context(
        project_folder: str, reel=None) -> Optional[Dict[str, Any]]:
    """What `composer.select_operation` reads, or None when undeclared.

    The merged preferences with the `style` id and the addressed
    `reel` on top (both None when undeclared). Loaded from the
    project folder alone - no command, no arguments beyond where the
    project lives and which reel is addressed - so the pipeline reads
    it on every run without the captain invoking anything.
    """
    merged = load_video_preferences(project_folder, reel)
    if merged is None:
        return None
    return {"style": merged.get("style"), "preferences": merged,
            "reel": reel}
