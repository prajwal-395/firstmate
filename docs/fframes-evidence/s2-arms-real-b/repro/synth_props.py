"""Synthetic props for the S2 real-b arms (real props are read-only).

The four real cases are the tight props files in the geo-podcast
project under `pipeline_output/steps/4_06_render_motion_graphics/
motion_graphics/` (`mg_geo-podcast_058e1d98`, `_0ad89bd3`, `_35ad5429`,
`_a6292208`). They are the project's files, so they are referenced by
name, never copied here. The two synthetic cases below exercise the
paths no real card takes: a `typewriter` entrance on the emblem's mark,
and a `review_panel` at a centre anchor (the card is a definite-width
flex item, so it sits at the container's left edge - `card_x = box.x` -
which only a centre anchor can show).
"""


def synth_emblem_typewriter():
    return {
        "elements": [{
            "element": "subject_emblem", "anchor": "centre", "row": 0,
            "runs": [
                {"text": "NEW", "type_role": "display", "uppercase": False},
                {"text": "a label under the mark", "type_role": "supporting",
                 "uppercase": False},
            ],
            "color": "#7CFC00", "entrance": "typewriter", "exit": "fade",
            "timelineProgressStart": 0.1, "timelineProgressEnd": 0.2,
            "startFrame": 0, "durationFrames": 40, "footprint": 1.0,
            "emphasis": None, "data": {},
        }],
        "fps": 30, "width": 540, "height": 960,
        "safeArea": {"top": 80, "right": 40, "bottom": 120, "left": 40},
        "durationInFrames": 40,
    }


def synth_review_centre():
    return {
        "elements": [{
            "element": "review_panel", "anchor": "centre", "row": 0,
            "runs": [
                {"text": "Maple Street Kitchen", "type_role": "display",
                 "uppercase": False},
                {"text": "Google reviews", "type_role": "micro",
                 "uppercase": False},
            ],
            "color": "", "entrance": "cut", "exit": "cut",
            "timelineProgressStart": 0.1, "timelineProgressEnd": 0.2,
            "startFrame": 0, "durationFrames": 30, "footprint": None,
            "emphasis": None,
            "data": {
                "rating": 4.8, "count": 214,
                "rows": [
                    {"author": "Dana R.", "stars": 5, "when": "2 weeks ago",
                     "body": "Best pasta on this side of town. Walked in on a "
                             "Tuesday and still got a table."},
                    {"author": "Marcus T.", "stars": 5, "when": "a month ago",
                     "body": "Found it at the top of the map and it did not "
                             "disappoint. Going back Friday."},
                ],
                "palette": {"surface": "#FFFFFF", "ink": "#202124",
                            "muted": "#5F6368", "star": "#FBBC04",
                            "rule": "#E8EAED"},
            },
        }],
        "fps": 30, "width": 1080, "height": 1920,
        "safeArea": {"top": 220, "right": 60, "bottom": 320, "left": 60},
        "durationInFrames": 30,
    }
