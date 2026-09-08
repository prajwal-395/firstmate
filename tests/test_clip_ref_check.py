"""D7: the clip-reference check fires on a step's own legend text.

Step 5.01's hybrid output carries `grade_terms_legend` - a dict whose
`clip_id` key maps to the legend's prose, not to a clip - and
`grade_pipeline`, whose `source` values name brand-template slots
("brand template style.house_look.cdl"), not files. The check walked the
whole step output with bare-key matching, so both read as unrecognized
references on every run.

The fix is in what counts as a reference: a `clip_id` value that is a
sentence is a definition of the term, not a use of it, and a `source`
value with no directory separator is a slot name or an enum, not a file.
A warning that fires on correct output is noise, and noise is how a real
one gets scrolled past.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.processes.edit_video import run_pipeline as runner  # noqa: E402
from library.steps.step_5_01_color_grade.grade import GRADE_PIPELINE  # noqa: E402
from library.tools.color_correction import CLIP_KEY, term_legend  # noqa: E402
from library.tools.house_look import describe_look  # noqa: E402
from library.tools.window_frames import STRIP_LEGEND  # noqa: E402


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


def test_a_term_legend_is_a_definition_not_a_reference():
    """`grade_terms_legend[clip_id]` and the frame-strip legend both map
    the key to prose. Prose under the key is not a clip going missing."""
    catalog_ids, catalog_paths = _catalog()
    for legend in (_grade_terms_legend(), STRIP_LEGEND):
        assert legend[CLIP_KEY].strip() != ""
        unknown_ids, _ = runner.unknown_clip_refs(
            {"legend": legend}, catalog_ids, catalog_paths)
        assert legend[CLIP_KEY] not in unknown_ids


def test_a_template_slot_is_not_a_file_path():
    """`grade_pipeline` sources name template slots. None carries a
    directory separator, and no catalog path could ever equal one."""
    catalog_ids, catalog_paths = _catalog()
    refs = _pipeline_slot_refs()
    assert refs, "the pipeline under test names no sources"
    assert all("/" not in ref and "\\" not in ref for ref in refs)
    _, unknown_paths = runner.unknown_clip_refs(
        {"grade_pipeline": GRADE_PIPELINE}, catalog_ids, catalog_paths)
    assert not (set(refs) & unknown_paths)


def test_an_unknown_clip_id_still_warns():
    """The check keeps its teeth: an id shaped like an id but naming no
    footage is exactly what the warning is for."""
    catalog_ids, catalog_paths = _catalog()
    missing = f"clip_{len(catalog_ids) + 1:03d}"
    assert missing not in catalog_ids
    unknown_ids, unknown_paths = runner.unknown_clip_refs(
        {"color_correction": [{"clip_id": missing}]},
        catalog_ids, catalog_paths)
    assert unknown_ids == {missing}
    assert unknown_paths == set()


def test_an_unknown_absolute_path_still_warns():
    """Same, for a path that names a file the catalog never saw."""
    catalog_ids, catalog_paths = _catalog()
    stray = "/elsewhere/ungraded_take.MOV"
    assert stray not in catalog_paths
    unknown_ids, unknown_paths = runner.unknown_clip_refs(
        {"node": {"source": stray}},
        catalog_ids, catalog_paths)
    assert unknown_ids == set()
    assert unknown_paths == {stray}
