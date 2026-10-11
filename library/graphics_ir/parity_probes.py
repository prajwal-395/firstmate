"""The FFrames parity probe set: 12 crisp pass/fail checks, one score.

Report item 11 of ``data/vep-fframes-parity-gap/report.md`` (firstmate
home): the scout defined 12 probes scored 2/12 on main and asked for
them to be tracked as tests so the number moves as fixes land. This
module is the single definition of that set: each probe's id, name,
kind, pass criterion, threshold, last recorded measurement and the
C-item that closes it.

Kinds: ``static`` probes run here in-process with no renderer, no
Resolve and no suite - pure lowering calls plus source reads. ``pixel``
probes compare rendered frames; their thresholds and the census's
lock-held recorded measurements live here, and the recorded-vs-threshold
evaluation runs with no render. Re-measure a pixel probe with a few
small frames only (never the 90-frame parity gate), update the recorded
value with its source, and the score moves.

Nothing here changes engine behaviour: the static checks assert the
DESIRED end state and fail until the C-item lands. Failing probes are
marked expected-fail by id in ``EXPECTED_FAIL_TODAY`` - ``parity_score``
prints the label and the test files assert each probe's outcome matches
its label - so the score moves as fixes land. When a check starts
passing, its test fails with the removal instruction; remove the id
then. ``python3 -m library.tools.parity_score`` prints the single
``parity: N/12`` number from these same functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


MG_ARMS_18 = (
    "title_lockup", "quote_card", "progress_bar", "frame_accents",
    "lower_third", "channel_bug", "context_stamp", "review_panel",
    "stat_callout", "subject_emblem", "beat_accent", "pointer_annotation",
    "counter_roll", "digit_counter", "list_build", "comparison_bars",
    "step_counter", "website_panel",
)
"""The 18 MotionGraphics arms, in ``MotionGraphics/index.tsx`` order."""


def _mg_base_props() -> dict[str, Any]:
    return {
        "fps": 30, "width": 1080, "height": 1920, "durationInFrames": 300,
        "fontFamily": "Montserrat",
        "safeArea": {"top": 40, "right": 40, "bottom": 40, "left": 40},
    }


def _text_element(arm: str) -> dict[str, Any]:
    return {
        "element": arm, "startFrame": 0, "durationFrames": 30,
        "anchor": "middle_left",
        "runs": [{"text": "Hello world", "type_role": "display"}],
    }


def check_p4_entity_copy() -> str:
    """Copy with ``& " < >`` must reach the tree API unescaped.

    Ends with C1: stop escaping content passed to the nested-tree API.
    The ``font-family`` attribute escaping stays - an attribute is a
    real serialization boundary; element text handed to the tree API
    is not.
    """
    src = (_repo_root() / "native/ren-graphics-fframes/src/main.rs").read_text(
        encoding="utf-8")
    offenders = [
        line.strip() for line in src.splitlines()
        if "escape_xml" in line
        and "fn escape_xml" not in line
        and "family" not in line
    ]
    if offenders:
        raise AssertionError(
            "P4 entity-copy: tree-API text still escaped: "
            + "; ".join(offenders))
    return "tree-API text carries no escape_xml call"


def check_p6_mg_all_arms() -> str:
    """Every one of the 18 MG arms lowers to a non-None element. Ends with C2."""
    from PIL import Image

    from library.graphics_ir import lowering

    work = _repo_root() / ".parity-probe-tmp"
    work.mkdir(exist_ok=True)
    bug = work / "channel-bug.png"
    if not bug.is_file():
        Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(bug)
    try:
        missing: list[str] = []
        for arm in MG_ARMS_18:
            if arm == "channel_bug":
                el: dict[str, Any] = {
                    "element": arm, "startFrame": 0, "durationFrames": 30,
                    "anchor": "top_right", "asset": str(bug), "footprint": 0.1,
                }
            else:
                el = _text_element(arm)
            scene = lowering.lower_motion_graphics(
                dict(_mg_base_props(), elements=[el]))
            if not any(n.id.startswith("element_") for n in scene.nodes):
                missing.append(arm)
        if missing:
            raise AssertionError(
                f"P6 mg-all-arms: {len(missing)} of 18 arms still lower to "
                f"None: {', '.join(missing)}")
        return "all 18 arms lower to non-None scenes"
    finally:
        try:
            bug.unlink()
            work.rmdir()
        except OSError:
            pass


def check_p7_channel_bug_bounds() -> str:
    """channel_bug emits a bounded image on the template's anchor rule,
    and an unreadable asset refuses loudly instead of a silent None.
    Ends with C3."""
    from PIL import Image

    from library.graphics_ir import lowering

    work = _repo_root() / ".parity-probe-tmp"
    work.mkdir(exist_ok=True)
    bug = work / "channel-bug.png"
    if not bug.is_file():
        Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(bug)
    try:
        scene = lowering.lower_motion_graphics(dict(
            _mg_base_props(),
            elements=[{
                "element": "channel_bug", "startFrame": 0,
                "durationFrames": 30, "anchor": "top_right",
                "asset": str(bug), "footprint": 0.1,
            }],
        ))
        images = [n for n in scene.nodes if n.type.value == "image"]
        if not images or images[0].bounds is None:
            raise AssertionError(
                "P7 channel-bug-bounds: no bounded image node emitted")
        b = images[0].bounds
        # Usable width 1080-40-40=1000 at footprint 0.1 -> 100 wide,
        # 200x100 asset -> 50 high, top-right anchored at the safe inset.
        if (b.x, b.y, b.width, b.height) != (940.0, 40.0, 100.0, 50.0):
            raise AssertionError(
                "P7 channel-bug-bounds: placement "
                f"({b.x}, {b.y}, {b.width}, {b.height}) is not the "
                "template's anchor rule (940, 40, 100, 50)")
        try:
            bad = lowering.lower_motion_graphics(dict(
                _mg_base_props(),
                elements=[{
                    "element": "channel_bug", "startFrame": 0,
                    "durationFrames": 30, "anchor": "top_right",
                    "asset": "brand/missing-bug.png", "footprint": 0.1,
                }],
            ))
            silent = not any(
                n.id.startswith("element_") for n in bad.nodes)
        except Exception:
            silent = False
        if silent:
            raise AssertionError(
                "P7 channel-bug-bounds: unreadable asset still drops "
                "silently instead of refusing by name")
        return "bounded image on the anchor rule; unreadable asset refuses"
    finally:
        try:
            bug.unlink()
            work.rmdir()
        except OSError:
            pass


def check_p9_video_composite() -> str:
    """BrandMotion props lower and composite video with alpha. Ends with C7."""
    from library.tools import fframes_render

    if not fframes_render.fframes_has_form("BrandMotion"):
        raise AssertionError(
            "P9 video-composite: BrandMotion has no FFrames form")
    scene = fframes_render.lower_composition("BrandMotion", {
        "fps": 30, "width": 1080, "height": 1920, "durationInFrames": 30,
        "src": "brand/sting.mp4",
    })
    kinds = {n.type.value for n in scene.nodes}
    if "video" not in kinds:
        raise AssertionError(
            "P9 video-composite: BrandMotion lowering carries no video node")
    return "BrandMotion lowers with a video node"


def check_p11_conic_honest() -> str:
    """Conic gradients draw via the modelled shader route, or the model
    stops declaring them. Ends with C8."""
    from library.graphics_ir import model

    declares = hasattr(model, "ConicGradient")
    src = (_repo_root() / "native/ren-graphics-fframes/src/main.rs").read_text(
        encoding="utf-8")
    draws = "conic" in src.lower()
    if declares and not draws:
        raise AssertionError(
            "P11 conic-honest: model declares ConicGradient but the "
            "renderer never mentions conic (warn-skip by dead vocabulary)")
    return "conic draws or is undeclared"


def check_p12_unknown_refuses_loudly() -> str:
    """Unknown arms and boundless images refuse by name at lowering
    time; nothing ships blank silently. Ends with the C3 guard. (An
    ``html`` node already refuses by name inside the renderer.)"""
    from library.graphics_ir import lowering

    silent: list[str] = []
    try:
        scene = lowering.lower_motion_graphics(dict(
            _mg_base_props(), elements=[dict(
                _text_element("totally_made_up_arm"),
                element="totally_made_up_arm")],
        ))
        if not any(n.id.startswith("element_") for n in scene.nodes):
            silent.append("unknown arm drops to None silently")
    except Exception:
        pass
    try:
        scene = lowering.lower_motion_graphics(dict(
            _mg_base_props(),
            elements=[{
                "element": "channel_bug", "startFrame": 0,
                "durationFrames": 30, "anchor": "top_right",
                "asset": "brand/missing-bug.png", "footprint": 0.1,
            }],
        ))
        if not any(n.id.startswith("element_") for n in scene.nodes):
            silent.append("boundless image drops to None silently")
    except Exception:
        pass
    if silent:
        raise AssertionError(
            "P12 unknown-refuses-loudly: " + "; ".join(silent))
    return "unknown content refuses by name"


def eval_p1_subtitle_settled() -> str:
    """FF-vs-HF settled-frame parity within census floors."""
    if not (P1_INK_IOU >= P1_INK_IOU_MIN and P1_SSIM >= P1_SSIM_MIN):
        raise AssertionError(
            f"P1 subtitle-settled: recorded ink IoU {P1_INK_IOU} / SSIM "
            f"{P1_SSIM} below floors {P1_INK_IOU_MIN} / {P1_SSIM_MIN}")
    return (f"recorded ink IoU {P1_INK_IOU} >= {P1_INK_IOU_MIN}, SSIM "
            f"{P1_SSIM} >= {P1_SSIM_MIN} (census 2.1)")


def eval_p2_mg_text_settled() -> str:
    """Same words, same reading order, settled IoU >= 0.5."""
    if not P2_SETTLED_IOU >= P2_SETTLED_IOU_MIN:
        raise AssertionError(
            f"P2 mg-text-settled: recorded settled IoU {P2_SETTLED_IOU} "
            f"below {P2_SETTLED_IOU_MIN}")
    return (f"recorded settled IoU {P2_SETTLED_IOU} >= {P2_SETTLED_IOU_MIN} "
            "(census triangle)")


def eval_p3_mg_tracking_exact() -> str:
    """Systematic <=1px tracking/advance offset corrected (C4)."""
    if not P3_OFFSET_CORRECTED:
        raise AssertionError(
            "P3 mg-tracking-exact: per-word-box HF-vs-FF measurement not "
            "run; systematic ~1px offset uncorrected (census G5)")
    return "systematic tracking offset corrected"


def eval_p5_ramp_endpoint() -> str:
    """Fade ramps agree to <=1 frame at both ends (C5)."""
    if not P5_RAMPS_AGREE:
        raise AssertionError(
            "P5 ramp-endpoint: census 2.1 TimedText probe f3 opacity ratio "
            "2.0, f59 nonzero-vs-zero; fencepost unfixed (census G6)")
    return "fade ramps agree within one frame at both ends"


def eval_p8_pixel_flip_honest() -> str:
    """Pixel half of P8: no silent-2D frame may ship (C6)."""
    if not P8_HONEST:
        raise AssertionError(
            "P8 flip-honest: flip still draws silent 2D rotate "
            "(lowering 899-904, 967-972); no perspective IR field")
    return "flip draws perspective or refuses by name"


def eval_p10_audio_carry() -> str:
    """Declared brand sound reaches the artefact, or its absence is
    refused - never silent (C7-adjacent mux decision)."""
    if not P10_CARRIED:
        raise AssertionError(
            "P10 audio-carry: renderer audio() returns AudioMap::none() "
            "(main.rs audio arm); overlay paths encode png-sequences, "
            "which carry no sound")
    return "brand sound reaches the artefact or is refused loudly"


P1_INK_IOU = 0.876
P1_SSIM = 0.975
P1_INK_IOU_MIN = 0.85
P1_SSIM_MIN = 0.97
P2_SETTLED_IOU = 0.55
P2_SETTLED_IOU_MIN = 0.5
P3_OFFSET_CORRECTED = False
P5_RAMPS_AGREE = False
P8_HONEST = False
P10_CARRIED = False


@dataclass(frozen=True)
class Probe:
    """One row of the parity scoreboard."""

    pid: str
    name: str
    kind: str
    criterion: str
    recorded: str
    closes_with: str
    run: Callable[[], str]


PROBES: tuple[Probe, ...] = (
    Probe("P1", "subtitle-settled", "pixel",
          "FF-vs-HF settled-frame parity within census floors "
          f"(ink IoU >= {P1_INK_IOU_MIN}, SSIM >= {P1_SSIM_MIN})",
          f"ink IoU {P1_INK_IOU}, SSIM {P1_SSIM} (census 2.1)", "done",
          eval_p1_subtitle_settled),
    Probe("P2", "mg-text-settled", "pixel",
          "same words, same reading order, settled IoU "
          f">= {P2_SETTLED_IOU_MIN} FF-vs-either-browser",
          f"settled IoU {P2_SETTLED_IOU} FF vs both, HF-vs-RM 0.92 "
          "(census triangle)", "done", eval_p2_mg_text_settled),
    Probe("P3", "mg-tracking-exact", "pixel",
          "systematic <=1px tracking/advance offset corrected "
          "(per-word-box HF-vs-FF)",
          "uncorrected ~1px shift (census G5); C4 measurement not run",
          "C4", eval_p3_mg_tracking_exact),
    Probe("P4", "entity-copy", "static",
          'copy containing & " < > draws literally identical across engines',
          "escape_xml on tree-API content (main.rs escape arm)", "C1",
          check_p4_entity_copy),
    Probe("P5", "ramp-endpoint", "pixel",
          "fade ramps agree to <=1 frame at both ends",
          "f3 opacity ratio 2.0, f59 nonzero-vs-zero (census 2.1, G6)",
          "C5", eval_p5_ramp_endpoint),
    Probe("P6", "mg-all-arms", "static",
          "all 18 MG arms lower to non-None scenes",
          "3 of 18 lower (lowering _MG_TEXT_ARMS)", "C2",
          check_p6_mg_all_arms),
    Probe("P7", "channel-bug-bounds", "static",
          "channel_bug emits bounded image + placement matches template",
          "boundless asset drops to None silently (census G3)", "C3",
          check_p7_channel_bug_bounds),
    Probe("P8", "flip-honest", "pixel",
          "flip draws true perspective or refuses by name (no silent 2D)",
          "silent 2D transform.rotate (lowering flip arm)", "C6",
          eval_p8_pixel_flip_honest),
    Probe("P9", "video-composite", "static",
          "BrandMotion props lower and composite with alpha",
          "no lowering; lower_composition refuses BrandMotion", "C7",
          check_p9_video_composite),
    Probe("P10", "audio-carry", "pixel",
          "declared brand sound reaches the artefact, or the absence is "
          "refused - never silent",
          "AudioMap::none; png-sequence paths carry no sound", "C7-mux",
          eval_p10_audio_carry),
    Probe("P11", "conic-honest", "static",
          "conic gradient draws via the modelled shader route, or the "
          "model stops declaring it",
          "model declares ConicGradient; renderer never mentions conic",
          "C8", check_p11_conic_honest),
    Probe("P12", "unknown-refuses-loudly", "static",
          "content with no lowering refuses by name; nothing ships blank "
          "silently",
          "None/skips are silent at lowering time; only html exits 1",
          "C3-guard", check_p12_unknown_refuses_loudly),
)
"""All 12 probes in report order: six static, six pixel (P10 rides with
the pixel half - the brief's pixel list names five, and the twelfth
probe needs a home; it is artefact-measured, so pixel it is)."""

EXPECTED_FAIL_TODAY = frozenset({
    "P3", "P5", "P6", "P7", "P8", "P10", "P11", "P12",
})
"""Probes failing today (score 4/12: P1, P2, P4, P9). ``parity_score`` labels
these expected-fail and the per-probe tests assert each outcome matches
its label. When a C-item lands, its probe's check passes and the score
moves - then remove the id here; the probe's test fails until you do,
with this instruction."""


def _by_id(pid: str) -> Probe:
    for probe in PROBES:
        if probe.pid == pid:
            return probe
    raise KeyError(f"unknown probe id {pid!r}")


def probe_status(pid: str) -> tuple[str, str]:
    """One probe's scoreboard row: ``(status, detail)``.

    ``PASS`` when the check holds; ``EXPECTED-FAIL`` when it fails and
    the probe is marked. Anything else raises with the repair step: an
    unmarked probe failing means a regression, a marked probe passing
    means remove its id from ``EXPECTED_FAIL_TODAY``. A check may only
    return a non-empty string or raise ``AssertionError`` - any other
    exception is a broken probe, not a result, and propagates.
    """
    probe = _by_id(pid)
    try:
        detail = probe.run()
    except AssertionError as exc:
        if pid in EXPECTED_FAIL_TODAY:
            if pid not in str(exc):
                raise AssertionError(
                    f"{pid} {probe.name}: failure does not name its probe "
                    f"id: {exc}") from exc
            return "EXPECTED-FAIL", str(exc)
        raise AssertionError(
            f"{pid} {probe.name}: unmarked probe failing (regression?): "
            f"{exc}") from exc
    if not detail:
        raise AssertionError(
            f"{pid} {probe.name}: check passed with empty detail - "
            "a probe that cannot say what passed cannot fail")
    if pid in EXPECTED_FAIL_TODAY:
        raise AssertionError(
            f"{pid} {probe.name}: now passes - remove {pid} from "
            "EXPECTED_FAIL_TODAY in library/graphics_ir/parity_probes.py")
    return "PASS", detail
