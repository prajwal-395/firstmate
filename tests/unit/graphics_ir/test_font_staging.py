"""Staging cuts one static instance per distinct run weight.

A text node whose runs carry several weights cannot draw them all from
one node-level face: usvg has no variable-font axis, so every run would
come out at the one staged weight (the bakeoff's Thin-100 class of
error, which the parity gate caught again on StagedScene). Staging
names each run's own face instead.
"""

from library.graphics_ir.model import Bounds, Node, NodeType, Scene, TextContent, TextRun
from library.tools import fframes_render
from library.tools.hyperframes_render import bundled_font_file


def _scene() -> Scene:
    return Scene(
        version=1, fps=30, width=1080, height=1920, duration=1.0,
        nodes=[Node(
            id="lead", type=NodeType.TEXT,
            text=TextContent(
                text="", font_family="Montserrat",
                font_file=str(bundled_font_file()),
                runs=[
                    TextRun(text="he plays ", font_size=54, font_weight=500,
                            color="#8A8578"),
                    TextRun(text="mind", font_size=172.8, font_weight=900,
                            color="#E8E4D8"),
                ],
            ),
            bounds=Bounds(x=0, y=0, width=1080, height=400),
        )],
    )


def test_runs_at_two_weights_stage_two_faces(tmp_path):
    scene = _scene()
    fframes_render.stage_scene_fonts(scene, str(tmp_path))
    families = {run.font_family for run in scene.nodes[0].text.runs}
    assert None not in families
    assert len(families) == 2
    assert len(list(tmp_path.glob("*.ttf"))) == 3  # both runs plus the node face
    files = {run.font_file for run in scene.nodes[0].text.runs}
    assert len(files) == 2
    assert all(f is not None and f != scene.nodes[0].text.font_file for f in files)
