"""What a machine needs to perform each capability - `ren doctor --for <id>`.

"Can this Mac run Ren?" is too binary (punch list 22): a machine can
search footage, analyse sources without identity, touch up a reel or
inspect a project without being able to render through Resolve. This
module answers "what do I need to perform this operation?" by
capability id, so doctor can report a required failure apart from a
capability that is merely unavailable.

Two halves:

    NEEDS        the vocabulary: one id per machine fact doctor checks
    CAPABILITY_NEEDS  per capability id, the needs it REQUIRES and the
                 needs it can run WITHOUT, each naming what degrades

Every capability also requires `BASELINE` - the interpreter and the
core dependency group nothing runs without. A baseline failure is the
only failure that stops every capability, so it is the only one doctor
calls REQUIRED.

Keyed by `library/tools/capabilities.py`'s ids plus `FRONT_DOOR`, the
capabilities a `ren` verb serves outside the operation registry
(footage search, source analysis, project inspection). The table is
declared, not derived, for one reason: importing the registry imports
torch, and doctor must answer before the ML environment exists.
`tests/test_machine_needs.py` holds the table to the registry - every
capability id covered, every environment requirement the registry
derives (`CapabilitySpec.assumes_machine`) mapped to a required need,
and every capability that needs a model's answer requiring a chat
harness.

Standard library only, for the same reason as `dependency_groups.py`.

    python3 -m library.tools.machine_needs --for footage.search
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from library.tools.dependency_groups import RUNTIME_GROUPS


@dataclass(frozen=True)
class Need:
    id: str
    summary: str


NEEDS: dict = {n.id: n for n in (
    Need("macos", "macOS - Ren runs on a Mac only"),
    Need("python.venv", "the Python 3.12 pipeline interpreter"),
    Need("python.core", "the core dependency group (requirements/core.txt)"),
    Need("python.graphics", "the graphics dependency group: cv2 with its "
         "Haar face detector, Pillow (requirements/graphics.txt)"),
    Need("python.analysis", "the analysis dependency group: torch, mlx-vlm, "
         "librosa, parselmouth, ... (requirements/analysis.txt)"),
    Need("python.identity", "the identity dependency group: speechbrain, "
         "insightface (requirements/identity.txt)"),
    Need("ffmpeg", "ffmpeg and ffprobe on PATH"),
    Need("node", "Node.js on PATH"),
    Need("remotion", "the Remotion dependencies bound to this checkout"),
    Need("resolve.scripting", "a scripting connection to a running "
         "DaVinci Resolve"),
    Need("resolve.studio", "DaVinci Resolve Studio - the free edition "
         "accepts no external scripting"),
    Need("model.vision", "the local vision model (gemma-4-12b-it-4bit)"),
    Need("model.search_embedding", "the footage-search embedding model "
         "(all-MiniLM-L6-v2)"),
    Need("transcriber.voz", "the on-device Voz transcriber (`da` on PATH)"),
    Need("model.mfa", "the Montreal Forced Aligner"),
    Need("model.beat_this", "the beat_this downbeat checkpoint"),
    Need("model.panns", "the PANNs sound-event checkpoint"),
    Need("deepfilter", "the DeepFilterNet binary"),
    Need("config.projects_root", "PIPELINE_PROJECTS_ROOT exists"),
    Need("config.sfx_library", "PIPELINE_SFX_LIBRARY names a library"),
    Need("config.music_library", "PIPELINE_MUSIC_LIBRARY names a library"),
    Need("chat_harness", "a chat harness signed in with a subscription, "
         "to answer the model's half"),
)}

BASELINE = ("macos", "python.venv", "python.core")
"""Required by every capability."""

ENV_REQUIREMENT_NEED = {
    # `requirements.py`'s environment requirements, as needs.
    "env.npx": "node",
    "env.remotion_installed": "remotion",
    "env.parselmouth": "python.analysis",
    "env.sfx_library": "config.sfx_library",
    "env.resolve_scripting": "resolve.scripting",
    "env.face_detector": "python.graphics",
    "env.reel_build_libraries": "python.core",
}


@dataclass(frozen=True)
class CapabilityNeeds:
    requires: tuple = ()
    """Needs it refuses without, beyond `BASELINE`."""
    degrades: dict = field(default_factory=dict)
    """Need -> what the capability does without it."""


_MODEL = ("chat_harness",)
_RESOLVE = ("resolve.scripting", "resolve.studio")
_REEL = _RESOLVE + ("python.graphics",)
_REMOTION = ("node", "remotion")
_TRANSCRIBE = CapabilityNeeds(
    ("ffmpeg", "python.analysis", "transcriber.voz", "model.mfa"),
    {"python.identity": "no speaker identity: voices are not told apart",
     "model.panns": "sound events recorded as unmeasured; an event anchor "
                    "refuses by name"})
_SEARCH = CapabilityNeeds(
    (),
    {"python.analysis": "lexical-only search: no dense half, so "
                        "paraphrases are missed",
     "model.search_embedding": "lexical-only search: no dense half, so "
                               "paraphrases are missed"})

CAPABILITY_NEEDS: dict = {
    "sfx_library.validate": CapabilityNeeds(("config.sfx_library",)),
    "footage.scan": CapabilityNeeds(("ffmpeg",)),
    "footage.catalog": CapabilityNeeds(("ffmpeg",)),
    "semantics.analyse": CapabilityNeeds(
        ("ffmpeg", "python.analysis", "python.graphics", "model.vision")),
    "prosody.analyse": CapabilityNeeds(("python.analysis",)),
    "ocr.extract": CapabilityNeeds(
        ("ffmpeg", "python.analysis", "python.graphics")),
    "creative.direct": CapabilityNeeds(_MODEL),
    "speech.enrich": CapabilityNeeds(_MODEL),
    "music.resolve": CapabilityNeeds(_MODEL + ("config.music_library",)),
    "duration_zone.build": CapabilityNeeds(),
    "aroll.assign": CapabilityNeeds(),
    "broll.resolve": CapabilityNeeds(_MODEL),
    "reel.candidates": CapabilityNeeds(),
    "reel.select": CapabilityNeeds(_MODEL),
    "reel.build": CapabilityNeeds(_REEL),
    "reel.touchup": CapabilityNeeds(_REEL),
    "reel.entry_motion": CapabilityNeeds(_REEL),
    "reel.set_properties": CapabilityNeeds(_REEL),
    "reel.ask": CapabilityNeeds(_REEL),
    "reel.verify": CapabilityNeeds(_RESOLVE),
    "reel.gate_stills": CapabilityNeeds(_RESOLVE),
    "music.analyse": CapabilityNeeds(
        ("python.analysis", "config.music_library"),
        {"model.beat_this": "downbeats fall back to the librosa "
                            "every-4th-beat estimate, labelled estimated"}),
    "subtitles.plan": CapabilityNeeds(),
    "subtitles.splice": CapabilityNeeds(),
    "rough_cut.review": CapabilityNeeds(),
    "transitions.resolve": CapabilityNeeds(_MODEL),
    "vfx.resolve": CapabilityNeeds(_MODEL),
    "sfx.resolve": CapabilityNeeds(_MODEL + ("config.sfx_library",)),
    "transcript.reindex": _TRANSCRIBE,
    "transcript.splice": _TRANSCRIBE,
    "subtitles.render": CapabilityNeeds(_REMOTION),
    "subtitles.render_segment": CapabilityNeeds(_REMOTION),
    "subtitles.rerender_swap": CapabilityNeeds(_REMOTION),
    "motion_graphics.render": CapabilityNeeds(_REMOTION + _MODEL),
    "motion_graphics.render_segment": CapabilityNeeds(_REMOTION),
    "color_grade.resolve": CapabilityNeeds(_MODEL),
    "audio_mix.resolve": CapabilityNeeds(
        _MODEL,
        {"deepfilter": "a deepfilternet request refuses by name and the "
                       "model re-plans with voice_isolation"}),
    "render.build": CapabilityNeeds(_RESOLVE + ("ffmpeg",)),
    "validation.resolve": CapabilityNeeds(_MODEL),

    # FRONT_DOOR: served by a `ren` verb, not by the operation registry.
    "footage.search": _SEARCH,
    "footage.search_index": _SEARCH,
    "footage.analyse": CapabilityNeeds(
        ("ffmpeg", "python.analysis", "python.graphics", "model.vision",
         "transcriber.voz", "model.mfa"),
        {"python.identity": "no identity lane: faces and voices are not "
                            "resolved to people"}),
    "project.inspect": CapabilityNeeds(),
    "project.create": CapabilityNeeds(("config.projects_root",)),
}

FRONT_DOOR = {
    "footage.search": "ren search",
    "footage.search_index": "ren search-index",
    "footage.analyse": "ren analyze",
    "project.inspect": "ren status / ren info / ren check",
    "project.create": "ren new",
}
"""Capabilities a `ren` verb serves outside the operation registry."""


class UnknownCapability(KeyError):
    pass


def needs_of(capability_id: str) -> CapabilityNeeds:
    """`BASELINE` plus the capability's own requirements, and what degrades."""
    try:
        own = CAPABILITY_NEEDS[capability_id]
    except KeyError:
        raise UnknownCapability(
            f"no capability {capability_id!r}; known: "
            f"{', '.join(sorted(CAPABILITY_NEEDS))}") from None
    required = BASELINE + tuple(n for n in own.requires if n not in BASELINE)
    return CapabilityNeeds(required, dict(own.degrades))


AVAILABLE = "available"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"


def availability(capability_id: str, failing) -> tuple:
    """`(verdict, missing, degraded)` given the ids of the needs that FAIL.

    `missing` are required needs that fail (UNAVAILABLE); `degraded` is
    `{need: what degrades}` for optional needs that fail (DEGRADED).
    """
    failing = set(failing)
    needs = needs_of(capability_id)
    missing = tuple(n for n in needs.requires if n in failing)
    degraded = {n: why for n, why in needs.degrades.items() if n in failing}
    if missing:
        return UNAVAILABLE, missing, degraded
    return (DEGRADED if degraded else AVAILABLE), missing, degraded


def problems() -> list:
    """Every way the table breaks its own vocabulary. Empty when sound."""
    out = []
    for name, own in CAPABILITY_NEEDS.items():
        for need in own.requires + tuple(own.degrades):
            if need not in NEEDS:
                out.append(f"{name}: names need {need!r}, which NEEDS "
                           f"does not declare")
        both = set(own.requires) & set(own.degrades)
        if both:
            out.append(f"{name}: {sorted(both)} both required and optional")
    for need in BASELINE + tuple(ENV_REQUIREMENT_NEED.values()):
        if need not in NEEDS:
            out.append(f"need {need!r} is used but not declared")
    for group in RUNTIME_GROUPS:
        if f"python.{group}" not in NEEDS:
            out.append(f"dependency group {group!r} has no python.{group} need")
    for name in FRONT_DOOR:
        if name not in CAPABILITY_NEEDS:
            out.append(f"FRONT_DOOR names {name!r}, which has no needs row")
    return out


def describe(capability_id: str) -> str:
    needs = needs_of(capability_id)
    lines = [f"{capability_id} requires:"]
    lines += [f"  {n:<24} {NEEDS[n].summary}" for n in needs.requires]
    if needs.degrades:
        lines.append("runs without, degraded:")
        lines += [f"  {n:<24} {why}" for n, why in needs.degrades.items()]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.machine_needs",
        description=__doc__.splitlines()[0])
    parser.add_argument("--for", dest="capability", metavar="CAPABILITY")
    parser.add_argument("--check", action="store_true",
                        help="print every way the table breaks its vocabulary")
    args = parser.parse_args(argv)
    if args.check:
        found = problems()
        print("\n".join(found) or "sound")
        return 1 if found else 0
    if args.capability:
        try:
            print(describe(args.capability))
        except UnknownCapability as exc:
            parser.error(exc.args[0])
        return 0
    print("\n".join(sorted(CAPABILITY_NEEDS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
