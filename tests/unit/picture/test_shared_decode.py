"""The two lanes that measure the same frames decode the source once.

PERF-007.  The picture sampler (step 1.03's soft-picture pass) and the
VI-03 image-defects sampler (step 1.04's source sampler) both decoded
every source in full, at 5 Hz, as 180 px-short-side greyscale: the same
frames, decoded twice.  These tests name that defect and the rules that
keep the sharing honest - a frame is only shared when the source frame
and the geometry are the same, and the outputs must not change.
"""
import hashlib
import shutil
import subprocess

import pytest

from library.tools import shared_decode, source_memory
from library.tools.analysis import image_defects, picture_quality

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed")


def _synthesise(path, duration=4.0, size="320x240"):
    """A short clip with real frames to decode."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi",
         "-i", f"testsrc2=size={size}:rate=30:duration={duration}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path), "-y"],
        check=True, capture_output=True, timeout=120)


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """The shared decode cache lives under the test's own directory."""
    root = tmp_path / "memory"
    root.mkdir()
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


@pytest.fixture
def decodes(monkeypatch):
    """Every ffmpeg decode the shared cache ran, in order."""
    calls: list = []
    real_run = shared_decode.subprocess.run

    def counting_run(command, *args, **kwargs):
        if command and command[0] == "ffmpeg" and "rawvideo" in command:
            calls.append(list(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(shared_decode.subprocess, "run", counting_run)
    return calls


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@needs_ffmpeg
def test_the_picture_sampler_and_the_defects_sampler_ask_for_the_same_frames(
        tmp_path):
    """The sharing is only possible because the two recipes agree.

    A difference in the geometry - a pixel or a rate - would make the
    two lanes measure DIFFERENT frames, and the sharing would be a lie.
    """
    clip = tmp_path / "source.mp4"
    _synthesise(clip)

    width, height = picture_quality._display_dimensions(str(clip))
    picture = picture_quality._sample_dimensions(
        width, height, picture_quality.SAMPLE_SHORT_SIDE_PX)
    defects_width, defects_height, _ = image_defects._dimensions(str(clip))
    defects = image_defects._sample_size(defects_width, defects_height)

    assert tuple(picture) == tuple(defects)


@needs_ffmpeg
def test_two_lanes_measuring_the_same_frames_decode_the_source_once(
        tmp_path, memory_root, decodes):
    """THE DEFECT: the second lane decoded the source again.

    Before the shared decode, the picture sampler and the image-defects
    sampler each ran their own whole-source decode.  Now the second one
    reads the frames the first one wrote.
    """
    clip = tmp_path / "source.mp4"
    _synthesise(clip)

    picture_quality.sample_sharpness(str(clip))
    image_defects.measure_source(str(clip))

    assert len(decodes) == 1, (
        f"the source was decoded {len(decodes)} times for one set of frames")


@needs_ffmpeg
def test_the_frames_both_lanes_read_are_the_same_bytes(
        tmp_path, memory_root):
    """Sharing must not change a single pixel either lane measures."""
    clip = tmp_path / "source.mp4"
    _synthesise(clip)

    picture = shared_decode.sampled_frames(
        str(clip), shared_decode.Recipe(
            rate_hz=picture_quality.SAMPLE_RATE_HZ,
            width=image_defects._sample_size(
                *image_defects._dimensions(str(clip))[:2])[0],
            height=image_defects._sample_size(
                *image_defects._dimensions(str(clip))[:2])[1]))
    defects = shared_decode.sampled_frames(
        str(clip), shared_decode.Recipe(
            rate_hz=image_defects.SAMPLE_RATE_HZ,
            width=picture.width, height=picture.height))

    assert _sha256(picture.path) == _sha256(defects.path)
    assert picture.frame_count == defects.frame_count


@needs_ffmpeg
def test_sharing_off_gives_each_lane_its_own_decode(
        tmp_path, memory_root, decodes, monkeypatch):
    """The escape hatch: `REN_SHARED_DECODE=off` is the old behaviour."""
    clip = tmp_path / "source.mp4"
    _synthesise(clip)
    monkeypatch.setenv(shared_decode.ENV_DISABLE, "off")

    picture_quality.sample_sharpness(str(clip))
    image_defects.measure_source(str(clip))

    assert len(decodes) == 2


@needs_ffmpeg
def test_a_recipe_of_another_size_is_another_artifact(
        tmp_path, memory_root, decodes):
    """A different geometry is a different artifact, not a shared one.

    The frames of another size are not the frames this lane measured, so
    the cache must not serve them - that is the rule the punchlist states
    about artifacts that depend on analysis parameters.
    """
    clip = tmp_path / "source.mp4"
    _synthesise(clip)

    shared_decode.sampled_frames(
        str(clip), shared_decode.Recipe(rate_hz=5.0, width=320, height=180))
    shared_decode.sampled_frames(
        str(clip), shared_decode.Recipe(rate_hz=5.0, width=160, height=90))

    assert len(decodes) == 2


@needs_ffmpeg
def test_a_recipe_of_another_rate_is_another_artifact(
        tmp_path, memory_root, decodes):
    """A different rate samples different frames of the same source."""
    clip = tmp_path / "source.mp4"
    _synthesise(clip)

    shared_decode.sampled_frames(
        str(clip), shared_decode.Recipe(rate_hz=5.0, width=320, height=180))
    shared_decode.sampled_frames(
        str(clip), shared_decode.Recipe(rate_hz=2.0, width=320, height=180))

    assert len(decodes) == 2


@needs_ffmpeg
def test_another_source_never_serves_these_frames(
        tmp_path, memory_root, decodes):
    """The identity is the CONTENT, not the path."""
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    _synthesise(first)
    _synthesise(second, size="320x242")

    recipe = shared_decode.Recipe(rate_hz=5.0, width=320, height=180)
    shared_decode.sampled_frames(str(first), recipe)
    shared_decode.sampled_frames(str(second), recipe)

    assert len(decodes) == 2


@needs_ffmpeg
def test_the_same_content_under_another_path_is_the_same_source(
        tmp_path, memory_root, decodes):
    """A COPY of a source is the same source: its frames are the same."""
    first = tmp_path / "first.mp4"
    _synthesise(first)
    copy = tmp_path / "copy.mp4"
    shutil.copyfile(first, copy)

    recipe = shared_decode.Recipe(rate_hz=5.0, width=320, height=180)
    one = shared_decode.sampled_frames(str(first), recipe)
    two = shared_decode.sampled_frames(str(copy), recipe)

    assert len(decodes) == 1
    assert one.path == two.path


@needs_ffmpeg
def test_the_measurements_are_the_same_with_sharing_on_and_off(
        tmp_path, memory_root, monkeypatch):
    """The outputs must not move: the shared frames are the same frames."""
    clip = tmp_path / "source.mp4"
    _synthesise(clip)

    monkeypatch.setenv(shared_decode.ENV_DISABLE, "off")
    independent = picture_quality.sample_sharpness(str(clip))
    monkeypatch.setenv(shared_decode.ENV_DISABLE, "on")
    shared = picture_quality.sample_sharpness(str(clip))

    assert independent == shared


@needs_ffmpeg
def test_a_purged_source_is_decoded_again(tmp_path, memory_root, decodes):
    """The cache is removable: an operator can give the disk back."""
    clip = tmp_path / "source.mp4"
    _synthesise(clip)
    recipe = shared_decode.Recipe(rate_hz=5.0, width=320, height=180)

    shared_decode.sampled_frames(str(clip), recipe)
    assert shared_decode.purge(str(clip), recipe) is True
    shared_decode.sampled_frames(str(clip), recipe)

    assert len(decodes) == 2
    assert shared_decode.purge(str(clip), recipe) is True
    assert shared_decode.purge(str(clip), recipe) is False


def test_a_recipe_names_the_decode_it_asks_for():
    """The signature is the whole contract, and it says what it is."""
    recipe = shared_decode.Recipe(rate_hz=5.0, width=320, height=180)
    assert recipe.signature == "fps=5,scale=320x180,pix=gray"
    assert shared_decode.Recipe(rate_hz=5.0, width=320, height=180,
                                pixel_format="gray").signature == recipe.signature
    assert shared_decode.Recipe(rate_hz=5.0, width=320, height=180,
                                start_s=10.0, end_s=20.0).signature != (
        recipe.signature)


def test_a_recipe_that_asks_for_nothing_is_refused():
    with pytest.raises(ValueError):
        shared_decode.Recipe(rate_hz=0, width=320, height=180)
    with pytest.raises(ValueError):
        shared_decode.Recipe(rate_hz=5.0, width=0, height=180)
