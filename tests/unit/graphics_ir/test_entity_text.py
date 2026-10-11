"""G1 regression: copy containing & " < > ' draws literally, not as entities.

The FFrames renderer builds a nested tree directly - no XML
serialization sits between the scene and the shaper - so the old
``escape_xml`` on tree-API content drew ``&quot;`` as six glyphs for
one ``"`` (census G1; the PostHeader hook ``"Your brand-new ...`` read
``&quot;Your...`` on screen). The string tests pin the lowerings'
payloads; the pixel test renders a card through the real binary and
measures the ink width against both advances, so it fails while the
renderer escapes and passes once the tree carries literals.

The pixel test only means something against a binary built from this
tree, so it skips where the resolved binary is stale (naming the
refresh command) - a stale shipped binary would answer for other
sources, not these.

The pixel test renders through the full FFrames path, so it is
expensive - one two-frame card - and states that reason here. It is
skipped where the binary is not built.
"""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from library.graphics_ir.lowering import (
    lower_post_header,
    lower_subtitle_overlay,
    measure_text,
)
from library.tools import fframes_render

#: One line carrying every XML-significant character: & " < > and '.
SPECIAL = "A & \"B\" <C> 'd'"

SIZE = 58.0
WEIGHT = 800.0


def _subtitle_props(text=None, words=None):
    sub = {"startFrame": 0, "endFrame": 2}
    if words is not None:
        sub["words"] = words
    else:
        sub["text"] = text
    return {
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 2,
        "style": {
            "fontFamily": "Montserrat",
            "fontColor": "#FFFFFF",
            "accentColor": "#FBF0B8",
            "fontSize": SIZE,
            "fontWeight": WEIGHT,
            "position": "bottom",
            "safeArea": {"top": 0, "right": 0, "bottom": 0, "left": 0},
            "captionMaxWidth": 1000,
        },
        "subtitles": [sub],
    }


def _word_timings(words):
    return [
        {"word": w, "startFrame": 0, "endFrame": 2} for w in words
    ]


def _post_header_props():
    return {
        "avatarSrc": "",
        "avatarShape": "square",
        "displayName": "R&D \"Quoted\" <tag> it's",
        "handle": "a&b",
        "verified": False,
        "hook": "Your brand-new \"site\" & <more> it's live",
        "fontFamily": "Montserrat",
        "nameSize": 32,
        "handleSize": 24,
        "hookSize": 28,
        "box": {"left": 0, "top": 0, "width": 400},
        "fps": 30,
        "width": 480,
        "height": 270,
        "durationInFrames": 1,
    }


def _text_payloads(scene):
    return [n.text.text for n in scene.nodes if n.text is not None]


def _assert_literal(payloads):
    assert payloads, "lowering produced no text nodes"
    for payload in payloads:
        for entity in ("&amp;", "&quot;", "&lt;", "&gt;"):
            assert entity not in payload, (
                f"lowered text carries an XML entity: {payload!r}")
    joined = " ".join(payloads)
    for char in ("&", "\"", "<", ">", "'"):
        assert char in joined, (
            f"lowered text lost {char!r}: {payloads!r}")


class TestEntityCopyThroughLowerings:
    def test_subtitle_text_path_keeps_specials_literal(self):
        scene = lower_subtitle_overlay(_subtitle_props(text=SPECIAL))
        payloads = _text_payloads(scene)
        assert len(payloads) == 1
        assert payloads[0] == SPECIAL
        _assert_literal(payloads)

    def test_subtitle_words_path_keeps_specials_literal(self):
        words = ["Fish", "&", "\"hot\"", "<fresh>", "it's"]
        scene = lower_subtitle_overlay(
            _subtitle_props(words=_word_timings(words)))
        payloads = _text_payloads(scene)
        assert sorted(payloads) == sorted(words)
        _assert_literal(payloads)

    def test_post_header_keeps_specials_literal(self):
        scene = lower_post_header(_post_header_props())
        by_id = {n.id: n for n in scene.nodes}
        assert by_id["name"].text.text == "R&D \"Quoted\" <tag> it's"
        assert by_id["handle"].text.text == "@a&b"
        assert "Your brand-new \"site\" & <more> it's live" in \
            by_id["hook"].text.text.replace("\n", " ")
        _assert_literal(_text_payloads(scene))


def _fframes_available() -> bool:
    try:
        usable, _ = fframes_render.fframes_available()
        return bool(usable)
    except Exception:
        return False


def _binary_freshness() -> tuple:
    """Whether the binary production rendering uses was built from this tree.

    Mirrors ``native_refresh.sidecar_path``: the fingerprint recorded
    beside the resolved binary must match this tree's. A stale binary
    answers for other sources, so the pixel test below refuses it
    rather than guessing.
    """
    from library.tools import native_refresh

    root = Path(__file__).resolve().parent.parent.parent.parent
    try:
        binary = Path(fframes_render.fframes_binary()).resolve()
    except Exception as exc:
        return False, f"FFrames binary not resolvable: {exc}"
    try:
        binary.relative_to(root)
    except ValueError:
        return False, (f"{binary} lives outside this checkout; its sources "
                       "are unprovable - build with "
                       "scripts/build_fframes_runtime.sh")
    if binary.parent.name in ("release", "debug"):
        sidecar = (binary.parent.parent / ".ren-fingerprints"
                   / "ren-graphics-fframes")
    else:
        sidecar = (binary.parent / ".ren-fingerprints"
                   / "ren-graphics-fframes")
    try:
        recorded = sidecar.read_text(encoding="utf-8").strip()
    except OSError:
        return False, f"no fingerprint beside {binary}"
    current = native_refresh.current_fingerprint(
        str(root), "ren-graphics-fframes")
    if current is None or recorded != current:
        return False, (f"{binary} was built from older sources; refresh it "
                       "with scripts/build_fframes_runtime.sh")
    return True, f"{binary} built from this tree"


_BINARY_FRESH, _BINARY_FRESH_REASON = _binary_freshness()


def _ink_bbox_width(png_path) -> int:
    rgba = np.asarray(Image.open(png_path).convert("RGBA")).astype(np.int32)
    cols = np.where(rgba[..., 3].max(axis=0) > 128)[0]
    if len(cols) == 0:
        return 0
    return int(cols[-1] - cols[0] + 1)


def _escape_xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


@pytest.mark.skipif(not _fframes_available(),
                    reason="FFrames binary not available")
@pytest.mark.skipif(not _BINARY_FRESH, reason=_BINARY_FRESH_REASON)
def test_fframes_draws_specials_as_literal_glyphs(tmp_path):
    """The ink width matches the literal copy, not the entity payload."""
    from library.tools.hyperframes_render import bundled_font_file

    font_file = str(bundled_font_file())
    props = _subtitle_props(text=SPECIAL)
    frames_dir = tmp_path / "frames"
    work_dir = tmp_path / "work"
    frames_dir.mkdir()
    work_dir.mkdir()
    rendered = fframes_render.render_one_card_frames(
        "SubtitleOverlay", props, str(frames_dir), str(work_dir), "")
    assert rendered["frames"], "FFrames rendered no frames"

    ink = _ink_bbox_width(rendered["frames"][0])
    assert ink > 0, "FFrames drew no glyphs for the entity copy"
    literal_advance = measure_text(SPECIAL, SIZE, font_file, WEIGHT)
    escaped_advance = measure_text(
        _escape_xml(SPECIAL), SIZE, font_file, WEIGHT)
    assert escaped_advance > 2 * literal_advance, (
        "fixture too weak: escaped copy is not clearly wider than literal")
    assert abs(ink - literal_advance) < abs(ink - escaped_advance), (
        f"ink width {ink}px matches the escaped payload "
        f"(advance {escaped_advance:.1f}px), not the literal copy "
        f"(advance {literal_advance:.1f}px): entities drawn literally")
