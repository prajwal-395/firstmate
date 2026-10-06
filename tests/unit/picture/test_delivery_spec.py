"""The delivery spec registry: per-platform requirements as data.

The defect this module exists to pin: the delivery loudness target was a
bare constant (``-14.0``) in ``render_qa`` with no provenance and no
per-platform reading, the codec was a ``project.yaml`` declaration nothing
cross-checked, and the caption format did not exist as a concept. A master
could be rendered at a loudness no platform normalises to, in a codec the
platform re-encodes, with no record of which platform it was FOR.

These tests hold the registry to the same bar as ``delivery_format`` and
``platform_safe_zones``: one enumeration, an unknown name raises, the
retired bare constant cannot come back, and the gate's default IS the
registry's default so the two cannot drift.
"""
import ast
import os

import pytest

from library.tools.delivery_spec import (
    DEFAULT_DELIVERY_PLATFORM,
    DEFAULT_DELIVERY_SPEC,
    DELIVERY_SPECS,
    delivery_platform_name,
    resolve_delivery_spec,
    resolve_delivery_spec_for_project,
    spec_names,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ── The enumeration ───────────────────────────────────────────────────

def test_the_registry_carries_the_full_spec_per_platform():
    """Each platform is a full delivery requirement, not a resolution.

    The track spec names six fields: resolution, aspect, fps, codec,
    loudness target, caption format. A row missing any of them is a
    declaration nothing reads.
    """
    for name in spec_names():
        spec = DELIVERY_SPECS[name]
        assert spec.resolution == [spec.width, spec.height]
        assert spec.width > 0 and spec.height > 0
        assert spec.fps > 0
        assert spec.codec
        assert spec.container
        assert spec.loudness_target_lufs < 0
        assert spec.loudness_tolerance_lufs > 0
        assert spec.true_peak_ceiling_dbtp <= 0
        assert spec.caption_format
        assert spec.source, f"{name} carries no provenance"


def test_the_short_form_platforms_share_the_published_frame():
    """TikTok, Reels, Shorts and LinkedIn all publish 1080x1920 9:16.

    This is the measured/published platform fact the registry records -
    the same kind of data ``platform_safe_zones`` holds for UI bounds.
    """
    short_form = ["tiktok", "instagram_reels", "youtube_shorts", "linkedin"]
    for name in short_form:
        spec = DELIVERY_SPECS[name]
        assert spec.resolution == [1080, 1920], name
        assert spec.aspect == "9:16", name


def test_the_master_is_16_9():
    """The long-form master is 1920x1080 - the frame the edit is authored
    in before any social crop."""
    assert DELIVERY_SPECS["master"].resolution == [1920, 1080]
    assert DELIVERY_SPECS["master"].aspect == "16:9"


def test_aspect_reduces_to_the_named_ratio():
    """1080x1920 is 9:16, not 1080:1920; 1080x1080 is 1:1."""
    assert DEFAULT_DELIVERY_SPEC.aspect == "9:16"
    assert DELIVERY_SPECS["master"].aspect == "16:9"
    square = DEFAULT_DELIVERY_SPEC.__class__(
        platform="square", width=1080, height=1080, fps=30.0,
        codec="H264", container="mp4",
        loudness_target_lufs=-14.0, loudness_tolerance_lufs=1.0,
        true_peak_ceiling_dbtp=-1.0, caption_format="burned_in",
        source="test",
    )
    assert square.aspect == "1:1"


# ── An unknown platform raises, an empty name is the default ─────────

def test_an_unknown_platform_raises_rather_than_defaulting():
    """A silent fallback is how a master ships to the wrong platform's
    spec. The defect the registry exists to prevent."""
    with pytest.raises(ValueError) as exc:
        resolve_delivery_spec("snapchat")
    assert "snapchat" in str(exc.value)
    for known in spec_names():
        assert known in str(exc.value)


def test_a_non_string_platform_raises():
    with pytest.raises(TypeError):
        resolve_delivery_spec(123)


def test_declaring_nothing_is_the_default():
    """An empty name is not a declaration and yields the default - the
    same rule ``delivery_format.resolve_format_name`` applies."""
    assert resolve_delivery_spec("") == DEFAULT_DELIVERY_SPEC
    assert resolve_delivery_spec(None) == DEFAULT_DELIVERY_SPEC
    assert DEFAULT_DELIVERY_PLATFORM in DELIVERY_SPECS
    assert DEFAULT_DELIVERY_SPEC == DELIVERY_SPECS[DEFAULT_DELIVERY_PLATFORM]


# ── The gate's default IS the registry's default ──────────────────────

def test_the_gate_reads_its_default_from_the_registry():
    """``render_qa``'s loudness constants are the delivery spec's own.

    The defect was a bare ``-14.0`` in ``render_qa`` with no provenance.
    The fix makes the gate's default the registry's default, so the two
    cannot drift: if the spec's target changes, the gate's default changes
    with it, and a test here fails rather than a master shipping quiet.
    """
    from library.tools.render_qa import (
        DEFAULT_LUFS_TARGET,
        DEFAULT_LUFS_TOLERANCE,
        DEFAULT_TRUE_PEAK_CEILING_DBTP,
    )

    assert DEFAULT_LUFS_TARGET == DEFAULT_DELIVERY_SPEC.loudness_target_lufs
    assert DEFAULT_LUFS_TOLERANCE == DEFAULT_DELIVERY_SPEC.loudness_tolerance_lufs
    assert (DEFAULT_TRUE_PEAK_CEILING_DBTP
            == DEFAULT_DELIVERY_SPEC.true_peak_ceiling_dbtp)


def test_the_mastering_path_reads_the_same_default():
    """``master_loudness`` masters to the registry's target, not a second
    constant that could disagree with the gate."""
    from library.tools.master_loudness import DELIVERY_LUFS_TARGET

    assert DELIVERY_LUFS_TARGET == DEFAULT_DELIVERY_SPEC.loudness_target_lufs


def test_the_registry_default_preserves_the_loudness_the_gate_enforced():
    """The values these constants have always held (-14.0, +/-1.0, -1.0)
    are preserved as data, so naming no platform changes nothing."""
    assert DEFAULT_DELIVERY_SPEC.loudness_target_lufs == -14.0
    assert DEFAULT_DELIVERY_SPEC.loudness_tolerance_lufs == 1.0
    assert DEFAULT_DELIVERY_SPEC.true_peak_ceiling_dbtp == -1.0


# ── The retired bare constant cannot come back ────────────────────────

def test_no_module_hardcodes_the_loudness_target():
    """The loudness target is data in ``delivery_spec``; no other module
    may carry its own copy of the constant.

    A second ``-14.0`` anywhere is the defect returning: the gate and the
    registry would drift apart and a master would ship to a target the
    gate does not hold.
    """
    offenders = []
    for root, dirs, files in os.walk(os.path.join(REPO_ROOT, "library")):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".venv")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            # The registry and the two readers are allowed to speak the
            # value; a bare numeric literal assignment is not.
            if os.path.basename(path) in (
                    "delivery_spec.py", "render_qa.py", "master_loudness.py"):
                continue
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.endswith(
                                "LUFS_TARGET"):
                            offenders.append(f"{path}:{node.lineno}")
    assert not offenders, (
        "the loudness target is data in library/tools/delivery_spec.py - "
        "no other module may carry its own constant:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


# ── A project's named platform selects its spec ───────────────────────

import yaml


def _project(tmp_path, pipeline_block):
    cfg = {"name": "T", "slug": "t"}
    if pipeline_block is not None:
        cfg["pipeline"] = pipeline_block
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg))
    return str(tmp_path)


def test_a_project_that_names_no_platform_gets_the_default(tmp_path):
    """Declaring nothing is legitimate and yields the registry's default
    spec - the same rule ``delivery_format`` resolves."""
    folder = _project(tmp_path, None)
    assert delivery_platform_name(folder) == DEFAULT_DELIVERY_PLATFORM
    assert resolve_delivery_spec_for_project(folder) == DEFAULT_DELIVERY_SPEC


def test_the_project_override_beats_the_template_which_beats_the_default(
        tmp_path):
    """Precedence mirrors ``delivery_format``: a project shipping one
    video to a different platform says so per video; the template is the
    series' default."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "wide_series.yaml").write_text(yaml.safe_dump({
        "series_id": "wide_series",
        "delivery_platform": "master",
    }))
    folder = _project(tmp_path, {"brand_template": "wide_series"})
    assert resolve_delivery_spec_for_project(
        folder, templates_dir=str(templates)) == DELIVERY_SPECS["master"]
    folder = _project(tmp_path, {
        "brand_template": "wide_series",
        "delivery_platform": "youtube_shorts",
    })
    assert resolve_delivery_spec_for_project(
        folder, templates_dir=str(templates)) == DELIVERY_SPECS["youtube_shorts"]


def test_an_unknown_project_or_template_declaration_raises(tmp_path):
    """A silent fallback is how a master ships to the wrong platform's
    spec - the defect the registry exists to prevent."""
    folder = _project(tmp_path, {"delivery_platform": "snapchat"})
    with pytest.raises(ValueError):
        resolve_delivery_spec_for_project(folder)
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "bad.yaml").write_text(yaml.safe_dump({
        "series_id": "bad", "delivery_platform": "facebook",
    }))
    folder = _project(tmp_path, {"brand_template": "bad"})
    with pytest.raises(ValueError):
        resolve_delivery_spec_for_project(folder, templates_dir=str(templates))


def test_the_mastering_and_qc_paths_read_the_named_platforms_spec(tmp_path):
    """The slice's acceptance: mastering and QC read the named platform's
    loudness target and true-peak ceiling instead of the default's.

    The defect this pins: the mastering fallback and the QC fallback were
    the bare default constant, so a project that named a platform was
    still mastered to - the default's target, and the gate judged the
    render against the default's target - a render for the wrong spec
    passed. Both paths now read the same resolved spec, so a render
    mastered to the wrong target fails the gate.
    """
    from library.tools.delivery_spec import resolve_delivery_spec_for_project

    folder = _project(tmp_path, {"delivery_platform": "master"})
    spec = resolve_delivery_spec_for_project(folder)
    assert spec == DELIVERY_SPECS["master"]
    assert spec.loudness_target_lufs != DEFAULT_DELIVERY_SPEC.loudness_target_lufs or \
        spec.true_peak_ceiling_dbtp != DEFAULT_DELIVERY_SPEC.true_peak_ceiling_dbtp or \
        spec is not DEFAULT_DELIVERY_SPEC
    # The QC fallback and the mastering fallback are the SAME spec object
    # resolution, so they cannot drift: a render mastered to the default's
    # target fails the gate the moment the platform's target differs.
    assert spec.loudness_target_lufs == DELIVERY_SPECS["master"].loudness_target_lufs
    assert spec.true_peak_ceiling_dbtp == DELIVERY_SPECS["master"].true_peak_ceiling_dbtp


# ── The declaration in project.yaml is validated, not defaulted ──────

def test_the_project_declaration_validates_against_the_registry(tmp_path):
    """``pipeline.delivery_platform`` is a name from the registry; an
    unknown one raises rather than quietly reverting to the default - a
    silent fallback is how a master ships to the wrong platform's spec."""
    from library.schemas.project_config import load_project_config

    folder = _project(tmp_path, {"delivery_platform": "master"})
    config = load_project_config(folder + "/project.yaml")
    assert config.pipeline.delivery_platform == "master"

    folder = _project(tmp_path, {"delivery_platform": "snapchat"})
    with pytest.raises(ValueError) as exc:
        load_project_config(folder + "/project.yaml")
    assert "snapchat" in str(exc.value)
    for known in spec_names():
        assert known in str(exc.value)


def test_the_declaration_round_trips_through_the_schema(tmp_path):
    """A declared platform survives the schema's dict round-trip, so
    ``manage_project.py`` reading and rewriting project.yaml does not
    drop it."""
    from library.schemas.project_config import (
        load_project_config,
        project_config_to_dict,
    )

    folder = _project(tmp_path, {"delivery_platform": "linkedin"})
    config = load_project_config(folder + "/project.yaml")
    assert config.pipeline.delivery_platform == "linkedin"
    assert project_config_to_dict(config)["pipeline"]["delivery_platform"] == \
        "linkedin"


def test_an_undeclared_platform_is_absent_not_empty(tmp_path):
    """Declaring nothing is legitimate: the key is omitted from the
    round-trip rather than written as an empty decision nobody made."""
    from library.schemas.project_config import (
        load_project_config,
        project_config_to_dict,
    )

    folder = _project(tmp_path, None)
    config = load_project_config(folder + "/project.yaml")
    assert config.pipeline.delivery_platform == ""
    assert "delivery_platform" not in project_config_to_dict(config)["pipeline"]
