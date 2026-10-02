"""The project's safe-zone policy reaches what lays a reel out.

The captain, 2026-09-25: the safe-area tooling must bound, "load in
preferences and configs for specific platforms, but also custom rules
that might be set". Each test names what breaks when that stops being
true.
"""

import pytest

from library.tools import safe_zone_policy as szp
from library.tools.safe_area import resolve_safe_area


def _project(tmp_path, block: str) -> str:
    (tmp_path / "project.yaml").write_text(
        "name: t\npipeline:\n  safe_zones:\n" + block, encoding="utf-8")
    return str(tmp_path)


def test_a_platform_and_phone_preference_changes_what_is_kept_clear(tmp_path):
    # Made for TikTok on iPhones only: no LinkedIn rail and no 21:9
    # crop, so the project gets its room back - and the insets every
    # caption and graphic lays out from say so.
    everyone = resolve_safe_area(None)
    folder = _project(tmp_path, "    platforms: [tiktok]\n"
                                "    devices: iphone\n")
    mine = resolve_safe_area(folder)
    assert mine.centered_usable_width > everyone.centered_usable_width
    assert szp.project_layout(folder).visible()[0] < \
        szp.resolve_layout().visible()[0]


def test_a_custom_rule_reaches_the_layout_consumers_read(tmp_path):
    folder = _project(
        tmp_path,
        "    keep_out:\n"
        "      - {rect: [0, 1300, 1080, 1500], reason: lower-third bar}\n")
    layout = szp.project_layout(folder)
    hits = layout.intrusions((400, 1350, 600, 1400))
    assert [h["platform"] for h in hits] == [szp.CUSTOM]
    assert hits[0]["ui"] == "lower-third bar"
    # A caption on those rows has no centred width at all.
    assert layout.centred_clear_width(1350, 1400)[0] == 0


def test_a_margin_grows_every_app_element_but_not_the_crop(tmp_path):
    plain = szp.resolve_layout()
    padded = szp.project_layout(_project(tmp_path, "    margin: 12\n"))
    assert padded.visible() == plain.visible()
    assert padded.insets()["right"] == plain.insets()["right"] + 12


UNHONOURABLE = [
    ("    platform: [tiktok]\n", "nothing reads"),
    ("    platforms: [myspace]\n", "myspace"),
    ("    devices: [Nokia 3310]\n", "Nokia"),
    ("    ignore: [tiktok.left]\n", "side strip"),
    ("    keep_out:\n      - {rect: [0, 0, 10, 10]}\n", "no reason"),
    ("    keep_out:\n      - {rect: [10, 0, 5, 10], reason: x}\n",
     "x1 > x0"),
]


def test_a_rule_that_cannot_be_honoured_is_refused_not_dropped(tmp_path):
    for block, says in UNHONOURABLE:
        with pytest.raises(szp.SafeZonePolicyError, match=says):
            szp.project_policy(_project(tmp_path, block))


def test_fit_scale_shrinks_a_picture_inside_what_every_phone_shows():
    layout = szp.resolve_layout()
    picture = (6, 480, 1069, 1880)  # the geo-podcast finals, 2026-09-25
    k = layout.fit_scale(picture)
    assert k < 1
    cx = (picture[0] + picture[2]) / 2
    x0 = cx + (picture[0] - cx) * k
    x1 = cx + (picture[2] - cx) * k
    vx0, _vy0, vx1, _vy1 = layout.visible()
    assert vx0 <= x0 and x1 <= vx1
    # ...and no smaller than it has to be.
    assert min(x0 - vx0, vx1 - x1) < 1
