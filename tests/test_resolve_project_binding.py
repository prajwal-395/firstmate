"""The build opens the bound Resolve project, never the timeline's name.

Step 6.01 passed the manifest's timeline name (`Main Edit`) as the
Resolve PROJECT to open, so every build refused on a listing that
never contained it ("No project named exactly 'Main Edit'") - the
reason no P4 run reached a first timeline. The manifest now carries
the bound project name beside the timeline name, and the render opens
that or refuses naming the missing declaration.

Each test names the defect its assertion prevents. Fixtures only.
"""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from library.steps.step_6_01_render.step import (  # noqa: E402
    _resolve_build_project_name,
)
from library.tools.brand_registry import resolve_project_name  # noqa: E402
from library.tools.qa.timeline_sync_qa import (  # noqa: E402
    _caption_track_index,
)
from library.tools.ren_refusal import RenRefusal  # noqa: E402


def test_build_opens_the_bound_project_not_the_timeline():
    """The render address is the project name, not the timeline name.

    Defect prevented: the timeline's name opened as a project, so a
    project bound to `ren-p4d-scratch-speech-two` building a `Main
    Edit` timeline refused with "No project named exactly 'Main
    Edit'".
    """
    manifest = {"project": {
        "name": "Main Edit",
        "resolve_project_name": "ren-p4d-scratch-speech-two",
    }}
    assert (_resolve_build_project_name(manifest)
            == "ren-p4d-scratch-speech-two")


def test_build_without_a_bound_project_refuses_by_name():
    """A build with nowhere bound says so instead of opening current.

    Defect prevented: falling back to whatever project happens to be
    open - on the captain's machine, his live edit. The refusal travels
    in the one refusal shape, carrying what, why and the fix.
    """
    with pytest.raises(RenRefusal) as first:
        _resolve_build_project_name({"project": {"name": "Main Edit"}})
    assert "resolve.project_name" in str(first.value)
    with pytest.raises(RenRefusal, match="resolve.project_name"):
        _resolve_build_project_name(
            {"project": {"name": "Main Edit",
                         "resolve_project_name": "  "}})


def test_resolve_project_name_reads_off_the_project(tmp_path):
    """The binding reads resolve.project_name, exactly as declared.

    Defect prevented: a reader that trims, defaults or prefixes the
    name - the address is exact or it lands on somebody else's
    project.
    """
    project = tmp_path / "p"
    project.mkdir()
    assert resolve_project_name(str(project)) == ""
    (project / "project.yaml").write_text(
        "resolve:\n  project_name: 'ren-p4d-scratch-music'\n"
        "  timeline_name: Main Edit\n")
    assert (resolve_project_name(str(project))
            == "ren-p4d-scratch-music")


def _plan_with_caption_row(index: int) -> dict:
    return {"video_tracks": [
        {"index": 1, "media_type": "video", "role": "a-roll",
         "name": "A-Roll"},
        {"index": index, "media_type": "video", "role": "captions",
         "name": "Subtitles"},
    ]}


def test_sync_qa_reads_captions_off_the_builds_own_row():
    """The checker grades the row the builder laid, not a hardcoded V3.

    Defect prevented: captions placed on V2 (no B-roll row above
    them) failed sync QA as "V3 missing" - the check and the build
    disagreeing about where the picture is.
    """
    assert _caption_track_index(_plan_with_caption_row(2)) == 2
    assert _caption_track_index(_plan_with_caption_row(3)) == 3


def test_sync_qa_without_a_plan_stays_on_v3():
    """An older build result with no track plan still checks V3.

    Defect prevented, the other direction: a fallback that refuses
    instead of reading what every recorded build used.
    """
    assert _caption_track_index(None) == 3
    assert _caption_track_index({}) == 3
