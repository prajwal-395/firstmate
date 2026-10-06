"""The deliverable matrix plans shared work once and true variants only.

Covers library/tools/deliverable_matrix.py: platforms whose registry
rows agree on every render-governing field share one encode, and the
16:9 master picture every reframe reads is named as shared work.
"""

import pytest

from library.tools.deliverable_matrix import (
    master_aspect,
    plan_deliverables,
    render_key,
)
from library.tools.delivery_spec import (
    DEFAULT_DELIVERY_PLATFORM,
    DELIVERY_SPECS,
    resolve_delivery_spec,
)


def test_four_identical_vertical_platforms_share_one_render():
    """The defect: four platforms, four identical encodes of the same
    picture. The registry rows agree on every render-governing field,
    so the plan produces ONE job serving all four."""
    plan = plan_deliverables(
        ["tiktok", "instagram_reels", "youtube_shorts", "linkedin"])
    assert plan.render_count == 1
    assert plan.jobs[0].serves == (
        "tiktok", "instagram_reels", "youtube_shorts", "linkedin")
    assert plan.jobs[0].needs_reframe is True
    assert plan.jobs[0].source == "reframe_of_master"


def test_master_and_vertical_are_two_jobs_off_one_shared_picture():
    """Different aspects are true variants: two encodes, with the master
    picture named as the shared source the reframe reads."""
    plan = plan_deliverables(["master", "tiktok"])
    assert plan.render_count == 2
    by_source = {job.source: job for job in plan.jobs}
    assert by_source["master_render"].serves == ("master",)
    assert by_source["master_render"].needs_reframe is False
    assert by_source["reframe_of_master"].serves == ("tiktok",)
    assert plan.requires_master_picture is True


def test_a_vertical_only_request_still_names_the_master_picture():
    """The shared work is named even when nobody requested the master
    deliverable: the reframe reads the 16:9 picture, so the plan says
    the master is rendered rather than leaving each variant looking
    like work from scratch."""
    plan = plan_deliverables(["tiktok"])
    assert plan.render_count == 1
    assert plan.requires_master_picture is True


def test_a_master_only_request_needs_no_other_picture():
    """No reframe means no unrequested work: the master render IS the
    deliverable and nothing else is rendered."""
    plan = plan_deliverables(["master"])
    assert plan.render_count == 1
    assert plan.requires_master_picture is False


def test_an_empty_request_yields_the_default_platform():
    """Requesting nothing is legitimate and yields the registry's
    default - the same rule resolve_delivery_spec applies."""
    plan = plan_deliverables([])
    assert plan.render_count == 1
    assert plan.jobs[0].serves == (DEFAULT_DELIVERY_PLATFORM,)


def test_repeated_and_blank_names_collapse_to_one_served_name():
    """A name said twice is one declaration, and a blank is not one."""
    plan = plan_deliverables(["tiktok", "tiktok", "  "])
    assert plan.render_count == 1
    assert plan.jobs[0].serves == ("tiktok",)


def test_an_unknown_platform_raises():
    """A silent fallback is how a master ships to the wrong platform's
    spec - the registry raises and the plan does not catch it."""
    with pytest.raises(ValueError):
        plan_deliverables(["snapchat"])


def test_the_key_ignores_name_and_provenance_not_requirements():
    """Only the render-governing fields group platforms: tiktok and
    instagram_reels are different names for the same render, while the
    master row's different frame keeps it apart."""
    assert render_key(resolve_delivery_spec("tiktok")) == render_key(
        resolve_delivery_spec("instagram_reels"))
    assert render_key(resolve_delivery_spec("tiktok")) != render_key(
        resolve_delivery_spec("master"))


def test_the_master_aspect_is_read_off_the_registry():
    """The authored picture is whatever the master row declares, never
    a second constant beside it that can drift."""
    assert master_aspect() == DELIVERY_SPECS["master"].aspect
