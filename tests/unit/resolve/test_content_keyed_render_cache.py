"""The content-keyed render cache: one file per pixels, however many timelines place it.

History: docs/evidence/resolve_test_history.md#test_content_keyed_render_cache.
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import (  # noqa: E402
    RENDERED,
    REUSED,
    render_one_segment,
)
from library.tools.caption_asset_gc import (  # noqa: E402
    LIVE,
    RootResult,
    SweepRefused,
    collect_pipeline_roots,
    ledger_path_for,
    mark,
    record_rendered_segments,
    sweep,
)
from library.tools import render_cache as _render_cache  # noqa: E402
from tests.unit.captions.test_motion_graphics_render import (  # noqa: E402
    _el as _mg_el,
)
from tests.unit.captions.test_motion_graphics_render import (  # noqa: E402
    _planned as _mg_planned,
)
from tests.unit.captions.test_overlay_carriage import (  # noqa: E402
    _props as _caption_props,
)
from tests.unit.captions.test_overlay_carriage import (  # noqa: E402
    _StubRenderer,
)

VARIANTS = [
    "Reel 09 - your-website-is-only-20-percent (rebuild staging)",
    "Reel 09 - your-website-is-only-20-percent (j-cut)",
    "Reel 09 - your-website-is-only-20-percent (reaction-cutaway)",
]


def test_render_key_tracks_lock_engine_codec_and_local_asset_bytes(
        tmp_path, monkeypatch):
    remotion = _remotion_tree(tmp_path)
    base = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="qtrle/argb")
    assert base

    lock = os.path.join(remotion, "package-lock.json")
    with open(lock, "w", encoding="utf-8") as handle:
        json.dump({"packages": {"node_modules/remotion": {
            "version": "4.0.2"}}}, handle)
    lock_changed = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="qtrle/argb")
    assert lock_changed != base

    font = os.path.join(remotion, "public", "fonts",
                        "Montserrat-Variable.ttf")
    with open(font, "wb") as handle:
        handle.write(b"replacement font")
    font_changed = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="qtrle/argb")
    assert font_changed != lock_changed

    source = os.path.join(remotion, "src", "x.tsx")
    with open(source, "w", encoding="utf-8") as handle:
        handle.write("export const x = 2;\n")
    source_changed = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="qtrle/argb")
    assert source_changed != font_changed

    monkeypatch.setattr(
        _render_cache, "hyperframes_fingerprint", lambda _root: "same-engine-files")
    engine_changed = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", engine="hyperframes",
        codec="qtrle/argb")
    assert engine_changed and engine_changed != source_changed

    codec_changed = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="png-sequence/rgba")
    carriage_changed = _render_cache.content_key(
        "drawing", remotion, "carriage-v2", codec="qtrle/argb")
    assert codec_changed != source_changed
    assert carriage_changed != source_changed

    asset = tmp_path / "logo.svg"
    asset.write_bytes(b"<svg>first</svg>")
    first_asset_digest = _render_cache.local_asset_fingerprint(
        {"brand/logo.svg": asset})
    first_asset_key = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="qtrle/argb",
        assets_digest=first_asset_digest)
    asset.write_bytes(b"<svg>replacement</svg>")
    second_asset_digest = _render_cache.local_asset_fingerprint(
        {"brand/logo.svg": asset})
    second_asset_key = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="qtrle/argb",
        assets_digest=second_asset_digest)
    missing_asset_digest = _render_cache.local_asset_fingerprint(
        {"brand/logo.svg": tmp_path / "missing.svg"})
    missing_asset_key = _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="qtrle/argb",
        assets_digest=missing_asset_digest)
    assert first_asset_digest != second_asset_digest
    assert first_asset_key != second_asset_key
    assert missing_asset_digest == ""
    assert missing_asset_key == ""

    os.remove(lock)
    assert _render_cache.content_key(
        "drawing", remotion, "carriage-v1", codec="qtrle/argb") == ""


def test_hyperframes_fingerprint_covers_renderer_pin_and_requires_inputs(
        tmp_path):
    hyperframes = tmp_path / "hyperframes"
    (hyperframes / "compositions").mkdir(parents=True)
    (hyperframes / "vendor").mkdir()
    (hyperframes / "compositions" / "MotionGraphics.html").write_text(
        "<main>one</main>", encoding="utf-8")
    (hyperframes / "vendor" / "gsap.min.js").write_text(
        "window.gsap = 1", encoding="utf-8")
    adapter = tmp_path / "library" / "tools" / "hyperframes_render.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text("HYPERFRAMES_VERSION_PIN = '0.8.70'\n",
                       encoding="utf-8")
    font = tmp_path / "remotion-subtitles" / "public" / "fonts" / \
        "Montserrat-Variable.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"font")

    baseline = _render_cache.hyperframes_fingerprint(str(hyperframes))
    adapter.write_text("HYPERFRAMES_VERSION_PIN = '0.8.71'\n",
                       encoding="utf-8")
    updated_pin = _render_cache.hyperframes_fingerprint(str(hyperframes))
    assert baseline
    assert updated_pin != baseline

    font.unlink()
    assert _render_cache.hyperframes_fingerprint(str(hyperframes)) == ""


def _remotion_tree(root):
    """A renderer tree the fingerprint can read - no Remotion needed."""
    src = os.path.join(str(root), "remotion", "src")
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, "x.tsx"), "w", encoding="utf-8") as handle:
        handle.write("export const x = 1;\n")
    remotion = os.path.dirname(src)
    fonts = os.path.join(remotion, "public", "fonts")
    os.makedirs(fonts, exist_ok=True)
    with open(os.path.join(fonts, "Montserrat-Variable.ttf"), "wb") as handle:
        handle.write(b"synthetic font")
    with open(os.path.join(remotion, "package.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"dependencies": {"remotion": "4.0.1"}}, handle)
    with open(os.path.join(remotion, "package-lock.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"packages": {"node_modules/remotion": {
            "version": "4.0.1"}}}, handle)
    return remotion


def _render_caption(props, out_dir, timeline, remotion, stub):
    # Explicit full canvas: these tests pin content-key SHARING across
    # timelines, not the carrying - and the byte stub cannot feed the
    # tight path's decoded probe. Tight sharing is pinned by
    # `test_reuse_skips_identical_tight_frames`.
    return render_one_segment(
        props, str(out_dir), timeline, remotion_dir=remotion,
        reuse=True, renderer=stub, overlay_geometry="full")


# ── The shared hit ────────────────────────────────────────────────


def test_three_variants_render_once_and_pair_back_twice(tmp_path):
    """The core demonstration, with counts: three variant builds of the
    same words cost ONE render. Before this change the timeline in the
    filename split them into three files and three Chromium launches;
    the stub counts what the second and third builds no longer pay."""
    out_dir = tmp_path / "captions"
    out_dir.mkdir()
    remotion = _remotion_tree(tmp_path)
    stub = _StubRenderer()

    built = [_render_caption(_caption_props(), out_dir, variant,
                             remotion, stub)
             for variant in VARIANTS]

    assert [b["provenance"] for b in built] == \
        [RENDERED, REUSED, REUSED]
    assert stub.calls and len(stub.calls) == 1, (
        f"three variant builds must cost one render, paid "
        f"{len(stub.calls)}")
    paths = {b["overlay_path"] for b in built}
    assert len(paths) == 1, f"three variants must pair to one file: {paths}"
    path = built[0]["overlay_path"]
    assert os.path.isfile(path)
    # No timeline names the file: the stem is provenance (speaker +
    # source footage), the digest is content.
    name = os.path.basename(path)
    assert "reel" not in name and "timeline" not in name.lower(), name
    assert re.fullmatch(r"sub_nospeaker_clip-001_10000-12000_"
                        r"[0-9a-f]{8}\.mov", name), name
    # ...but every placing is still recorded: three ledger entries,
    # one path, each naming the timeline it serves.
    with open(ledger_path_for(str(out_dir)), encoding="utf-8") as handle:
        entries = json.load(handle)["subtitle_overlay"]["segments"]
    assert len(entries) == 3
    assert {e["overlay_path"] for e in entries} == {path}
    assert sorted(e["binding"]["timeline"] for e in entries) == \
        sorted(VARIANTS)


def test_placement_only_differences_still_hit(tmp_path):
    """Level 2 of the old splitter: the reuse key hashed the whole
    props object, so a variant numbering its blocks differently or
    holding the caption at different absolute seconds missed even
    where the filename agreed. Placement is not pixels: differing
    block ordinals and timeline bounds still share the file."""
    out_dir = tmp_path / "captions"
    out_dir.mkdir()
    remotion = _remotion_tree(tmp_path)
    stub = _StubRenderer()

    first = _render_caption(_caption_props(), out_dir, VARIANTS[0],
                            remotion, stub)
    assert first["provenance"] == RENDERED

    moved = _caption_props()
    moved["_block_position"] = 7
    moved["_timeline_start"] = 41.5
    moved["_timeline_end"] = 43.5
    second = _render_caption(moved, out_dir, VARIANTS[1], remotion, stub)

    assert second["provenance"] == REUSED
    assert second["overlay_path"] == first["overlay_path"]
    assert len(stub.calls) == 1


# ── Genuinely different pixels still render ───────────────────────


def test_different_words_render_different_files(tmp_path):
    """The old overwrite stays dead after the timeline left the name:
    a reel captioning the same source span with DIFFERENT words must
    never share - or overwrite - the master's file. Differing drawing
    inputs digest differently, so they cannot collide."""
    out_dir = tmp_path / "captions"
    out_dir.mkdir()
    remotion = _remotion_tree(tmp_path)
    stub = _StubRenderer()

    master = _render_caption(_caption_props(), out_dir, "Studio Chat",
                             remotion, stub)
    changed = _caption_props()
    changed["subtitles"] = [dict(changed["subtitles"][0],
                                 text="no, that's what everyone assumes.")]
    reel = _render_caption(changed, out_dir, VARIANTS[0], remotion, stub)

    assert master["provenance"] == RENDERED
    assert reel["provenance"] == RENDERED
    assert reel["overlay_path"] != master["overlay_path"]
    assert os.path.isfile(master["overlay_path"])
    assert os.path.isfile(reel["overlay_path"])
    assert len(stub.calls) == 2


def test_video_and_frames_carryings_never_share_a_file(tmp_path):
    """The container is in the digest AND the filename: a frames render
    of the same pixels must never pair back to - or overwrite the
    sidecars of - the stitched video."""
    out_dir = tmp_path / "captions"
    out_dir.mkdir()
    remotion = _remotion_tree(tmp_path)

    video = render_one_segment(
        _caption_props(), str(out_dir), VARIANTS[0],
        remotion_dir=remotion, reuse=True, renderer=_StubRenderer(),
        overlay_geometry="full")
    assert video["provenance"] == RENDERED
    assert video["overlay_path"].endswith(".mov")

    frames = render_one_segment(
        _caption_props(), str(out_dir), VARIANTS[0],
        remotion_dir=remotion, reuse=True, renderer=_StubRenderer(),
        overlay_geometry="full", overlay_container="frames")
    assert frames["provenance"] == RENDERED, (
        "the frames carrying must render, never reuse the video file")
    assert frames["overlay_path"] != video["overlay_path"]
    assert frames["frames"]["dir"].endswith("_frames")
    assert os.path.isdir(frames["frames"]["dir"])


def test_different_durations_render_different_files(tmp_path):
    """Duration stays in the content key: it is the file's frame count.
    Two variants holding one caption for genuinely different lengths
    must still render twice, because the pixels really differ."""
    out_dir = tmp_path / "captions"
    out_dir.mkdir()
    remotion = _remotion_tree(tmp_path)
    stub = _StubRenderer()

    first = _render_caption(_caption_props(), out_dir, VARIANTS[0],
                            remotion, stub)
    longer = _caption_props()
    longer["durationInFrames"] = 90
    longer["_source_out_frame"] = 90
    second = _render_caption(longer, out_dir, VARIANTS[1], remotion, stub)

    assert first["provenance"] == RENDERED
    assert second["provenance"] == RENDERED
    assert second["overlay_path"] != first["overlay_path"]
    assert len(stub.calls) == 2


def test_subtitle_key_tracks_frame_rate_geometry_and_container(tmp_path):
    from library.steps.step_4_05_render_subtitles import step as captions

    remotion = _remotion_tree(tmp_path)
    props = _caption_props()
    baseline = captions._reuse_key(props, remotion, "full", "video")
    changed_fps = dict(props, fps=24)
    changed_geometry = captions._reuse_key(props, remotion, "tight", "video")
    changed_container = captions._reuse_key(props, remotion, "full", "frames")

    assert baseline
    assert captions._reuse_key(changed_fps, remotion, "full", "video") != baseline
    assert changed_geometry != baseline
    assert changed_container != baseline


# ── Motion graphics: the reuse path it never had ──────────────────


def _render_graphic(monkeypatch, planned, out_dir, label, remotion,
                    project_folder, calls):
    from library.steps.step_4_06_render_motion_graphics import (
        post_bridge as mg,
    )

    def fake_render(props_path, dest_path, remotion_dir, name):
        from PIL import Image

        calls.append(dest_path)
        props = json.load(open(props_path, encoding="utf-8"))
        os.makedirs(dest_path, exist_ok=True)
        width, height = props["width"], props["height"]
        frames = props["durationInFrames"]
        for index in range(frames):
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            image.putpixel((10 + index % 3, 10), (255, 255, 255, 255))
            image.save(os.path.join(dest_path,
                                    f"frame-{index + 1:06d}.png"))
        return True

    monkeypatch.setattr(mg, "_render_motion_graphics_file", fake_render)
    return mg.render_one_segment(
        planned, str(out_dir), segment_name=label,
        remotion_dir=remotion, project_folder=project_folder, reuse=True)


def test_motion_graphics_reuse_across_variants(tmp_path, monkeypatch):
    """The same graphic under two `vox_<reel>_<index>` placing labels
    renders once. The filename is rooted in the PROJECT - no reel, no
    timeline - and each placing keeps its own label on its entry."""
    out_dir = tmp_path / "mg"
    out_dir.mkdir()
    remotion = _remotion_tree(tmp_path)
    project = tmp_path / "geo-podcast"
    project.mkdir()
    calls = []
    labels = ["vox_reel-09-j-cut_00", "vox_reel-09-reaction-cutaway_00"]

    built = [_render_graphic(monkeypatch, _mg_planned([_mg_el("title")]),
                             out_dir, label, remotion, str(project), calls)
             for label in labels]

    assert [b["provenance"] for b in built] == ["rendered", "reused"]
    assert len(calls) == 1, (
        f"two variant graphics must cost one render, paid {len(calls)}")
    assert built[0]["overlay_path"] == built[1]["overlay_path"]
    name = os.path.basename(built[0]["overlay_path"])
    assert re.fullmatch(r"mg_geo-podcast_[0-9a-f]{8}\.mov", name), name
    assert "vox" not in name and "reel" not in name
    assert [b["placement_label"] for b in built] == labels


def test_motion_graphics_asset_bytes_invalidate_and_hits_match_fresh(
        tmp_path, monkeypatch):
    from library.steps.step_4_06_render_motion_graphics import (
        post_bridge as mg,
    )

    monkeypatch.setenv("PIPELINE_GRAPHICS_RENDERER", "remotion")
    remotion = _remotion_tree(tmp_path)
    public_brand = tmp_path / "remotion" / "public" / "brand"
    public_brand.mkdir(parents=True)
    asset = public_brand / "logo.svg"
    asset.write_bytes(b"<svg>version one</svg>")
    out_dir = tmp_path / "graphics"
    out_dir.mkdir()
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    planned = _mg_planned([_mg_el("channel_bug")])
    planned["props"]["elements"][0]["asset"] = "brand/logo.svg"
    calls = []

    def fake_render(props_path, dest_path, _remotion_dir, _name):
        from PIL import Image

        calls.append(dest_path)
        with open(props_path, encoding="utf-8") as handle:
            rendered_props = json.load(handle)
        assert rendered_props["elements"][0]["asset"] == "brand/logo.svg"
        asset_color = tuple(asset.read_bytes()[:3]) + (255,)
        os.makedirs(dest_path, exist_ok=True)
        for index in range(int(rendered_props["durationInFrames"])):
            Image.new("RGBA", (4, 4), asset_color).save(
                os.path.join(dest_path, f"frame-{index + 1:06d}.png"))
        return True

    monkeypatch.setattr(mg, "_render_motion_graphics_file", fake_render)
    monkeypatch.setattr(
        mg, "_encode_motion_graphics_video",
        lambda *_args, **_kwargs: pytest.fail(
            "static asset pixels must remain a PNG still"))

    first = mg.render_one_segment(
        planned, str(out_dir), remotion_dir=remotion,
        project_folder=str(project), overlay_geometry="full", reuse=True)
    reused = mg.render_one_segment(
        planned, str(out_dir), remotion_dir=remotion,
        project_folder=str(project), overlay_geometry="full", reuse=True)
    reused_bytes = Path(reused["overlay_path"]).read_bytes()
    fresh = mg.render_one_segment(
        planned, str(fresh_dir), remotion_dir=remotion,
        project_folder=str(project), overlay_geometry="full", reuse=False)

    assert first["provenance"] == "rendered"
    assert reused["provenance"] == "reused"
    assert fresh["provenance"] == "rendered"
    assert reused_bytes == Path(fresh["overlay_path"]).read_bytes()
    assert len(calls) == 2

    asset.write_bytes(b"<svg>version two</svg>")
    changed = mg.render_one_segment(
        planned, str(out_dir), remotion_dir=remotion,
        project_folder=str(project), overlay_geometry="full", reuse=True)
    assert changed["provenance"] == "rendered"
    assert changed["overlay_path"] != first["overlay_path"]
    assert len(calls) == 3




# ── The GC hazard: a shared file has several protectors ───────────


def _bindings(timeline):
    return {"timeline": timeline, "speaker": "speakertwo",
            "block_position": "closer", "source_clip_id": "clip_004",
            "source_start": 742.1, "source_end": 746.9}


def _shared_setup(asset_dir):
    shared = os.path.join(asset_dir, "sub_speakertwo_clip-004_11111-22222_"
                                     "aaaaaaaa.mov")
    with open(shared, "wb") as handle:
        handle.write(b"pixels")
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_speakertwo_clip-004_11111-22222_aaaaaaaa",
        "overlay_path": shared, "provenance": "rendered",
        "superseded": [], "binding": _bindings(VARIANTS[0])}])
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_speakertwo_clip-004_11111-22222_aaaaaaaa",
        "overlay_path": shared, "provenance": "reused",
        "superseded": [], "binding": _bindings(VARIANTS[1])}])
    return shared


def test_gc_shared_file_stays_live_while_any_placing_names_it(tmp_path):
    """One placing moving on must not unprotect the other: the
    supersede-drop is per placing, so the shared file stays LIVE while
    any ledger entry still names it - and becomes an orphan candidate
    only once the last placing moves on."""
    project = str(tmp_path)
    asset_dir = os.path.join(
        project, "pipeline_output", "steps", "4_05_render_subtitles")
    os.makedirs(asset_dir, exist_ok=True)
    shared = _shared_setup(asset_dir)

    def _live_paths():
        roots = collect_pipeline_roots(project)
        result = mark(project, asset_dir, roots)
        return {a.path: a for a in result.assets}

    assert _live_paths()[shared].status == LIVE

    # Variant 0 re-renders with corrected words: its placing unpins the
    # shared file, variant 1's placing still names it.
    corrected = os.path.join(asset_dir, "sub_speakertwo_clip-004_11111-22222_"
                                        "bbbbbbbb.mov")
    with open(corrected, "wb") as handle:
        handle.write(b"pixels")
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_speakertwo_clip-004_11111-22222_bbbbbbbb",
        "overlay_path": corrected, "provenance": "rendered",
        "superseded": [shared], "binding": _bindings(VARIANTS[0])}])

    states = _live_paths()
    assert states[shared].status == LIVE, (
        "variant 1 still places the shared file - superseding variant "
        "0's placing must not orphan it")
    assert states[shared].saved_by == "pipeline:render_subtitles"

    # Variant 1 moves on too: now nothing names the shared file, and
    # only now is it an orphan candidate.
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_speakertwo_clip-004_11111-22222_cccccccc",
        "overlay_path": corrected, "provenance": "rendered",
        "superseded": [shared], "binding": _bindings(VARIANTS[1])}])
    assert _live_paths()[shared].status != LIVE




# ── Reuse across provenance stems ─────────────────────────────────


def test_the_same_pixels_under_another_stem_render_once(tmp_path):
    """A passage re-anchored a few milliseconds along its source moves the
    span in the filename and nothing that draws. Before the cross-name
    lookup it rendered again beside a byte-identical twin - 81 such
    duplicates, 126 MB, on geo-podcast."""
    out_dir = tmp_path / "captions"
    out_dir.mkdir()
    remotion = _remotion_tree(tmp_path)
    stub = _StubRenderer()

    first = _render_caption(_caption_props(), out_dir, VARIANTS[0],
                            remotion, stub)
    shifted = _caption_props()
    shifted["_source_start"] = 10.004
    second = _render_caption(shifted, out_dir, VARIANTS[1], remotion, stub)

    assert second["overlay_path"] != first["overlay_path"]
    assert second["provenance"] == REUSED
    assert len(stub.calls) == 1
    assert os.path.samefile(first["overlay_path"], second["overlay_path"])


def test_rerendering_an_adopted_name_never_writes_through_its_twin(
        tmp_path):
    """An adopted file is a hard link. A render truncates its output, so
    writing through the link would rewrite - mid-render, half-written -
    the file another placing plays."""
    out_dir = tmp_path / "captions"
    out_dir.mkdir()
    remotion = _remotion_tree(tmp_path)
    first = _render_caption(_caption_props(), out_dir, VARIANTS[0],
                            remotion, _StubRenderer())
    shifted = _caption_props()
    shifted["_source_start"] = 10.004
    second = _render_caption(shifted, out_dir, VARIANTS[1], remotion,
                             _StubRenderer())
    assert os.path.samefile(first["overlay_path"], second["overlay_path"])

    class _Repaints(_StubRenderer):
        def render(self, props_path, overlay_path, sequence=False):
            self.calls.append((props_path, overlay_path, sequence))
            with open(overlay_path, "wb") as handle:
                handle.write(b"repainted")
            return True, ""

    render_one_segment(shifted, str(out_dir), VARIANTS[1],
                       remotion_dir=remotion, reuse=False,
                       renderer=_Repaints(), overlay_geometry="full")

    with open(first["overlay_path"], "rb") as handle:
        assert handle.read() == b"pixels"
