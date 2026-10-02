"""`video_segment_analyzer analyze --cleanup` deletes the analysed video,
and without the flag the footage is left exactly where it was."""
import sys
from unittest.mock import MagicMock, patch

from library.tools import video_segment_analyzer


def _run(video, *flags):
    argv = ["video_segment_analyzer.py", "analyze", "--video", str(video),
            *flags]
    with patch("library.tools.video_segment_analyzer.run_analysis",
               return_value={"status": "ok"}), \
            patch.object(sys, "argv", argv), \
            patch("sys.stdout", new=MagicMock()):
        try:
            video_segment_analyzer.main()
        except SystemExit:
            pass


def test_only_cleanup_deletes_the_video(tmp_path):
    video = tmp_path / "dummy.mp4"
    video.write_text("dummy content")
    _run(video)
    assert video.exists(), "without --cleanup the footage must survive"
    _run(video, "--cleanup")
    assert not video.exists()
