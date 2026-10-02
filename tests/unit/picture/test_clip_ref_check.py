"""D7: the clip-reference check fires on a step's own legend text.

A legend's `clip_id` sentence and a slot-name `source` are not references;
an unknown id or absolute path still warns.
History: `docs/evidence/clip_ref_check.md`.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.processes.edit_video import run_pipeline as runner  # noqa: E402
from library.steps.step_5_01_color_grade.grade import GRADE_PIPELINE  # noqa: E402
from library.tools.color_correction import term_legend  # noqa: E402
from library.tools.series_look import describe_look  # noqa: E402


def _catalog(n=2):
    """What the check reads: `clip_XXX` ids and absolute source paths.

    The id shape is the one `footage_identity` mints
    (`f"clip_{i + 1:03d}"`); the paths are absolute, which is why a
    genuine file reference always carries a separator.
    """
    ids = {f"clip_{i + 1:03d}" for i in range(n)}
    paths = {f"/footage/IMG_{1806 + i}.MOV" for i in range(n)}
    return ids, paths


def _grade_terms_legend():
    # The legend's own `clip_id` entry, read off the code that emits it -
    # never a copied literal.
    return term_legend()


def _pipeline_slot_refs():
    # The `source` constants the designed pipeline carries, read off the
    # structure that declares them.
    return [node["source"] for node in GRADE_PIPELINE.values()
            if isinstance(node, dict) and "source" in node]


def test_step_5_01_deterministic_output_names_nothing_unknown():
    """The D7 reproduction: legend prose plus slot refs warn on no one."""
    output = {
        "grade_terms_legend": _grade_terms_legend(),
        "color_grade_spec": {
            "grade_pipeline": GRADE_PIPELINE,
            "look_notes": describe_look(None),
        },
    }
    catalog_ids, catalog_paths = _catalog()
    assert runner.unknown_clip_refs(output, catalog_ids, catalog_paths) == (
        set(), set())


def test_an_unknown_clip_id_or_absolute_path_still_warns():
    """The check keeps its teeth: an id shaped like an id, or a path,
    naming no footage is exactly what the warning is for."""
    catalog_ids, catalog_paths = _catalog()
    missing = f"clip_{len(catalog_ids) + 1:03d}"
    assert missing not in catalog_ids
    unknown_ids, unknown_paths = runner.unknown_clip_refs(
        {"color_correction": [{"clip_id": missing}]},
        catalog_ids, catalog_paths)
    assert unknown_ids == {missing}
    assert unknown_paths == set()

    stray = "/elsewhere/ungraded_take.MOV"
    assert stray not in catalog_paths
    unknown_ids, unknown_paths = runner.unknown_clip_refs(
        {"node": {"source": stray}},
        catalog_ids, catalog_paths)
    assert unknown_ids == set()
    assert unknown_paths == {stray}
