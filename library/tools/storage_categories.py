"""ONE category vocabulary, and ONE budget per category, for Ren's storage.

The punchlist (PROD-005) asks for retention policies for "scratch media,
temporary exports, analysis caches, and obsolete render generations",
for practical storage budgets, and for usage that is VISIBLE. This
module is that vocabulary; `library/tools/storage_lifecycle.py` reports
the usage and compares it with the budget.

Why a category and not the existing ones
----------------------------------------
Ren already has two vocabularies, and neither answers this question:

- `artifact_policy.RETENTION_CLASSES` says whether a file may be NAMED
  by a cleanup. It is a permission, not a size.
- `retention.CATEGORIES` says which class a project purge removes. It
  is a removal set, not a size.

A budget needs a third thing: a named PLACE whose bytes can be counted
and compared with a number. That is what a category is, and it is
derived from the layout's areas - the one reading every lane already
agrees on - rather than from a file's name or its age.

The four budgeted categories are the punchlist's four. A fifth,
`protected`, holds everything no cleanup may ever name, and carries NO
budget: a budget that covered protected bytes would be a licence to
delete them.

The invariant that makes this safe
---------------------------------
**An area whose policy says a cleanup may never name it is in
`protected`, never in a budgeted category.** So a budget can never
name something protected, and the categories and the ownership policy
cannot disagree. `assert_categories_are_complete` refuses to let a new
area arrive without one.

Budgets
-------
A default per category, overridable per machine with
`REN_STORAGE_BUDGET_<CATEGORY>_BYTES` (the category name upper-cased,
e.g. `REN_STORAGE_BUDGET_SCRATCH_MEDIA_BYTES`). A budget is REPORTED
usage against a number; it is not an eviction policy. The lanes that
actually remove files keep their own guards, and this module never
decides that anything may be deleted.
"""

from __future__ import annotations

import os

from library.tools import artifact_policy
from library.tools.project_layout import AREAS, Area

SCRATCH_MEDIA = "scratch_media"
TEMPORARY_EXPORTS = "temporary_exports"
ANALYSIS_CACHES = "analysis_caches"
OBSOLETE_RENDER_GENERATIONS = "obsolete_render_generations"
PROTECTED = "protected"

BUDGETED_CATEGORIES = (
    SCRATCH_MEDIA,
    TEMPORARY_EXPORTS,
    ANALYSIS_CACHES,
    OBSOLETE_RENDER_GENERATIONS,
)
"""The punchlist's four. Each carries a budget."""

CATEGORIES = (*BUDGETED_CATEGORIES, PROTECTED)
"""COMPLETE. A category that is not here is not a category."""

DEFAULT_BUDGETS: dict[str, int | None] = {
    SCRATCH_MEDIA: 5 * 1024**3,
    TEMPORARY_EXPORTS: 10 * 1024**3,
    ANALYSIS_CACHES: 20 * 1024**3,
    OBSOLETE_RENDER_GENERATIONS: 10 * 1024**3,
    PROTECTED: None,
}
"""Practical defaults, in bytes. `protected` has NO budget: it is the
category a cleanup may never name, so a number against it would read
as permission."""

_AREA_CATEGORY: dict[Area, str] = {
    # Scratch media: working copies of media, and what a sweep moved aside.
    Area.SCRATCH: SCRATCH_MEDIA,
    Area.QUARANTINE: SCRATCH_MEDIA,
    Area.ACQUIRED_MEDIA: SCRATCH_MEDIA,

    # Temporary exports: the things a render or a review produced.
    Area.PREVIEWS: TEMPORARY_EXPORTS,
    Area.CARRIERS: TEMPORARY_EXPORTS,
    Area.TIMELINE_INTERCHANGE: TEMPORARY_EXPORTS,
    Area.FUSION_COMPS: TEMPORARY_EXPORTS,
    Area.SUBTITLE_SEGMENTS: TEMPORARY_EXPORTS,
    Area.MOTION_GRAPHICS_SEGMENTS: TEMPORARY_EXPORTS,
    Area.TIMED_TEXT_SEGMENTS: TEMPORARY_EXPORTS,
    Area.QA_FRAMES: TEMPORARY_EXPORTS,
    Area.GATE_STILLS: TEMPORARY_EXPORTS,
    Area.SHOT_STILLS: TEMPORARY_EXPORTS,
    Area.VFX_STILLS: TEMPORARY_EXPORTS,
    Area.WINDOW_FRAMES: TEMPORARY_EXPORTS,
    Area.ROUGHCUT_FRAMES: TEMPORARY_EXPORTS,
    Area.REEL_FRAME_OVERLAYS: TEMPORARY_EXPORTS,
    Area.REEL_POST_HEADERS: TEMPORARY_EXPORTS,
    Area.REEL_SAFE_ZONE_GUIDES: TEMPORARY_EXPORTS,
    Area.REEL_CARDS: TEMPORARY_EXPORTS,

    # Analysis caches: what the measurements leave behind.
    Area.VISION_ANALYSIS: ANALYSIS_CACHES,
    Area.TEMPORAL_INDEX: ANALYSIS_CACHES,
    Area.AUDIO_CACHE: ANALYSIS_CACHES,
    Area.PROSODY: ANALYSIS_CACHES,
    Area.SEGMENTATION: ANALYSIS_CACHES,
    Area.OCR: ANALYSIS_CACHES,
    Area.MUSIC_ANALYSIS: ANALYSIS_CACHES,
    Area.FOOTAGE_ANALYSIS: ANALYSIS_CACHES,
    Area.REEL_CANDIDATE_DIAGNOSTICS: ANALYSIS_CACHES,
    Area.SFX_CATALOGUE: ANALYSIS_CACHES,
    Area.LLM_REQUESTS: ANALYSIS_CACHES,
    Area.LLM_RESPONSES: ANALYSIS_CACHES,
    Area.REASONING: ANALYSIS_CACHES,
    Area.ASSEMBLY_MANIFEST: ANALYSIS_CACHES,

    # Obsolete render generations: what an older run left behind.
    Area.RUN_ARCHIVES: OBSOLETE_RENDER_GENERATIONS,
    Area.BACKUPS: OBSOLETE_RENDER_GENERATIONS,
    Area.LLM_RESPONSES_BAK: OBSOLETE_RENDER_GENERATIONS,
    Area.OUTPUT_ROOT: OBSOLETE_RENDER_GENERATIONS,
    Area.STEPS_ROOT: OBSOLETE_RENDER_GENERATIONS,
}
"""The CLEANABLE areas, and the category each one is counted in.

An area whose policy class is in `NEVER_CLEANABLE` is absent here on
purpose: it is `protected`, and a budgeted category can never hold
something a cleanup may not name."""

LEGACY_CATEGORY: dict[str, str] = {
    "abandoned_scratch": SCRATCH_MEDIA,
    "quarantine": SCRATCH_MEDIA,
    "qualification": SCRATCH_MEDIA,
    "native_cache": SCRATCH_MEDIA,
    "qualification_export": TEMPORARY_EXPORTS,
    "workflow_run": TEMPORARY_EXPORTS,
    "review": PROTECTED,
}
"""The legacy stores, and the category each one's bytes are counted in.

A legacy category nobody named is `protected`: an unknown store is an
admitted unknown, never a lesser one."""


def area_category(area: Area) -> str:
    """The category one area's bytes are counted in."""
    policy = artifact_policy.policy_for_area(area)
    if artifact_policy.is_never_cleanable(policy.retention_class):
        return PROTECTED
    try:
        return _AREA_CATEGORY[area]
    except KeyError:  # pragma: no cover - completeness is asserted below
        raise ValueError(f"no cleanup category for area: {area}") from None


AREA_CATEGORY: dict[Area, str] = {area: area_category(area) for area in AREAS}


def assert_categories_are_complete() -> None:
    """Refuse a category table that does not cover the layout."""
    missing = [area.value for area in AREAS if area not in AREA_CATEGORY]
    unknown = [
        area.value for area in AREA_CATEGORY
        if area_category(area) not in CATEGORIES
    ]
    if missing or unknown:
        raise ValueError(
            "the storage category table is not complete: "
            f"missing={missing} unknown={unknown}"
        )
    protected_leak = [
        area.value for area in AREAS
        if area_category(area) != PROTECTED
        and artifact_policy.is_never_cleanable(
            artifact_policy.policy_for_area(area).retention_class)
    ]
    if protected_leak:
        raise ValueError(
            "an area a cleanup may never name sits in a budgeted "
            f"category: {protected_leak}"
        )


assert_categories_are_complete()


def category_for_path(project_folder: str | os.PathLike,
                      path: str | os.PathLike) -> str:
    """The category one path's bytes are counted in.

    A path outside the project, or in no named area of it, is
    `protected`: there is no area to read, so ownership cannot be
    established, and an unknown place is never a lesser one.
    """
    area = artifact_policy.area_for_path(project_folder, path)
    if area is None:
        return PROTECTED
    return AREA_CATEGORY[area]


def budget_bytes(category: str) -> int | None:
    """The budget for one category, from the environment or the default.

    `protected` has no budget under any configuration: it is the
    category no cleanup may name.
    """
    if category not in CATEGORIES:
        raise ValueError(f"unknown storage category: {category}")
    if category == PROTECTED:
        return None
    name = f"REN_STORAGE_BUDGET_{category.upper()}_BYTES"
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return DEFAULT_BUDGETS[category]
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"{name} is not an integer: {raw}") from None
    if value < 0:
        raise ValueError(f"{name} cannot be negative: {value}")
    return value


def usage_rows(categories: dict[str, dict]) -> dict[str, dict]:
    """The reported usage row per category, with its budget attached."""
    rows = {}
    for category in CATEGORIES:
        totals = categories.get(category, {})
        budget = budget_bytes(category)
        total_bytes = int(totals.get("total_bytes", 0))
        rows[category] = {
            **totals,
            "budget_bytes": budget,
            "over_budget": (
                bool(budget is not None and total_bytes > budget)
            ),
        }
    return rows


__all__ = [
    "ANALYSIS_CACHES",
    "AREA_CATEGORY",
    "BUDGETED_CATEGORIES",
    "CATEGORIES",
    "DEFAULT_BUDGETS",
    "LEGACY_CATEGORY",
    "OBSOLETE_RENDER_GENERATIONS",
    "PROTECTED",
    "SCRATCH_MEDIA",
    "TEMPORARY_EXPORTS",
    "area_category",
    "assert_categories_are_complete",
    "budget_bytes",
    "category_for_path",
    "usage_rows",
]
