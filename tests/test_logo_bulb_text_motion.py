"""The animated closing lines arrive and leave instead of sitting static.

One test naming the defect: the two bottom lines' opacity and position
were constant across all 72 frames (up with the cut, out on the fade).
With a declared motion they rise in staggered, hold at full legibility,
and leave together before the final black.
"""

import numpy as np

from library.tools.logo_bulb import (
    ClosingProfile,
    ClosingText,
    ClosingTextMotion,
    parse_ground,
    text_full_frames,
    text_layer,
    text_element_bounds,
    text_motion_layer,
    text_motion_state,
)

RATE = 23.976
"""The conform every reel already built holds a slot for."""


def test_animated_lines_arrive_and_leave_instead_of_sitting_static():
    profile = ClosingProfile()
    motion = ClosingTextMotion(
        style="rise",
        line1_in=(0, 7),
        line2_in=(4, 11),
        lines_out=(61, 66),
        rise_px=28,
        exit_px=14,
    )
    text = ClosingText(
        lines=("See your brand the way AI does", "luciecontent.com"),
        color=parse_ground("#F5F5F5"),
        motion=motion,
    )

    states1 = [text_motion_state(index, motion, 1) for index in range(72)]
    states2 = [text_motion_state(index, motion, 2) for index in range(72)]
    # The defect: presence stuck at one value and travel at zero on
    # every frame.
    assert {presence for presence, _ in states1} != {1.0}
    assert {offset for _, offset in states1} != {0.0}
    assert {presence for presence, _ in states2} != {1.0}
    assert {offset for _, offset in states2} != {0.0}
    # Staggered: line 1 sits while line 2 is still arriving.
    assert states1[8] == (1.0, 0.0)
    assert states2[8][0] < 1.0
    # A shared hold, then a shared exit that is over before the black.
    assert all(presence == 1.0 and offset == 0.0
               for presence, offset in states1[12:61] + states2[12:61])
    assert states1[66] == (0.0, 14.0)
    assert states2[66] == (0.0, 14.0)
    # The full-legibility stand clears the 2.0s floor inside 72 frames.
    full = text_full_frames(text, [1.0] * 72, [1.0] * 72)
    assert (full[0], full[-1], len(full)) == (11, 61, 51)
    assert len(full) / RATE >= 2.0
    # The frames differ on real pixels, and the hold IS the static layer.
    layers = [text_motion_layer(text, 1080, 1920, profile, index)
              for index in (0, 8, 30, 63, 67)]
    inks = [layer[..., 3].sum() for layer in layers]
    assert inks[0] < inks[2]
    assert inks[3] < inks[2]
    assert inks[4] == 0.0
    static = text_layer(
        ClosingText(lines=text.lines, color=text.color), 1080, 1920,
        profile)
    assert np.array_equal(layers[2], static)


def test_every_closing_text_line_stays_inside_all_shortform_safe_zones():
    from library.tools.safe_zone_policy import default_policy, resolve_layout

    profile = ClosingProfile()
    motion = ClosingTextMotion(
        style="rise",
        line1_in=(0, 7),
        line2_in=(4, 11),
        lines_out=(61, 66),
        rise_px=28,
        exit_px=14,
    )
    text = ClosingText(
        lines=("See your brand the way AI does", "luciecontent.com"),
        color=parse_ground("#F5F5F5"),
        motion=motion,
    )
    safe = resolve_layout(default_policy(), frame=(1080, 1920))

    assert safe.policy.platforms == (
        "tiktok", "instagram_reels", "youtube_shorts", "linkedin")
    for frame in range(72):
        boxes = text_element_bounds(text, 1080, 1920, profile, frame)
        assert len(boxes) == 2
        assert boxes[0][3] < boxes[1][1], (
            f"line order crossed at frame {frame}: {boxes}")
        for line, box in zip(text.lines, boxes):
            intrusions = safe.intrusions(box)
            assert not intrusions, (
                f"closing line {line!r} leaves the safe zone at frame "
                f"{frame}, bounds={box}, intrusions={intrusions}")
