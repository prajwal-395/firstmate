"""What an audio operation IS, which route writes it, and what it says when it cannot.

Resolve's audio surface is not one thing.  There are operations Resolve
writes directly on the timeline item, operations that only exist as a
build-time value written through OpenTimelineIO, and operations Resolve
cannot write at all.  Ren used to describe all three with one sentence -
"the scripting API cannot set an audio level" - which was TRUE of the
spelling it tried (``SetProperty("Volume")``) and FALSE of the surface
Resolve 21.1 actually ships.

**This module is the ONE place that knows the difference.**  It is the
enumeration of the audio operations, the route each one takes, the exact
Resolve spelling that route uses, the read-back that proves the write
landed, the audible verification that proves the write is what was
asked for, and - for an operation with no route - the refusal it must
return, BY NAME.

Why it exists
-------------
A capability declaration that names the wrong route is worse than no
declaration: an agent reads "gain is implemented" and asks for a level,
and the request dies inside an executor that has been refusing since it
was written.  ``library/tools/edit_execution_adapters.py`` refuses for
exactly that reason today - its audio executor raises before it writes,
because the candidate receipt carries no independent audio-render
evidence - and the operation map still says the operation is
implemented.  The map and the executor must agree, and this module is
what they agree through.

What was measured, and where
----------------------------
The routes, the spellings and their audible results were MEASURED on
Resolve 21.1.0.14 (2026-10-21) on a Ren-owned scratch timeline: two
synthetic clips (a 440 Hz tone and a 660 Hz tone, placed linked A/V),
six states rendered by Resolve and measured in the decoded samples.  The
report is in ``tests/live/reports/axi012-audio-measurement.json`` and
the numbers are in ``MEASURED_EVIDENCE`` below.  The headline:

* an AUDIO item's property dictionary DOES carry the native audio
  properties in 21.1 - ``AudioVolume``, ``AudioPan`` and their
  ``...Enabled`` switches - so ``SetProperty("AudioVolume", -6)`` writes
  the level, reads back -6.0, and the render measures EXACTLY -6.00 dB;
* the spelling Ren used to try, ``SetProperty("Volume")``, returns
  False: the name is not in the dictionary, and a declaration naming it
  declares a call that answers False;
* ``SetClipEnabled(False)`` mutes an audio item AUDIBLY - the muted
  clip's window is digital zero - and ``SetFades`` fades audibly;
* the OTIO build carries a level and a keyframed curve, and the other
  clip's level and the linked A/V pair survive the rebuild.

The three routes
----------------

``native_direct``
    Resolve writes the value on the timeline item itself, in place.  No
    rebuild, no second timeline, the picture is untouched, and the
    read-back is the item's own state.

``otio_build``
    The value is a property of a BUILD.  Resolve's OTIO carries Fairlight
    clip volume in plain JSON, in dB, with keyframes
    (``library/tools/otio_mix.py``), and the import REBUILDS the
    timeline: placement, transform, markers and native transitions
    survive it; Fusion comps and CDL grades do not.  This is the only
    route that can carry a keyframed curve, and it is the route the
    build's mix already takes.

``refused``
    No route writes it.  The operation returns a precise refusal that
    names the operation and the surface that is missing, so the caller
    can act on it instead of retrying.

What was measured, and where
----------------------------
(The numbers are in ``MEASURED_EVIDENCE`` below, and the report they
come from is in the repository.)

The rule this module keeps
--------------------------
**A declared route is a route that was MEASURED, and an operation with
no measured route is refused by name.**  ``SetProperty("Volume")`` is
not a spelling Resolve 21.1 accepts; the property dictionary carries
``AudioVolume`` (with ``AudioVolumeEnabled``) and ``AudioPan``, and a
declaration that names the old spelling is a declaration of a call that
returns False.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── The routes ───────────────────────────────────────────────────────

ROUTE_NATIVE = "native_direct"
"""Resolve writes the value on the timeline item itself."""

ROUTE_OTIO = "otio_build"
"""The value is written into an OTIO export and imported back."""

ROUTE_REFUSED = "refused"
"""No route writes it; the operation returns a precise refusal."""

ROUTES = (ROUTE_NATIVE, ROUTE_OTIO, ROUTE_REFUSED)


@dataclass(frozen=True)
class AudioOperation:
    """One audio operation, and everything a caller needs to ask for it.

    `operation_id` is the ALGEBRA id - the vocabulary a request is
    written in - so a declaration here and a row in the operation map
    cannot drift apart silently: the map asks this module for the row.
    `audio_operation` is the audio name for it ("gain", "fade", "mute",
    "automation", "pan"), which is not always the same word: the mute is
    `clip.set_enabled` applied to an audio item, because that is the
    call Resolve has.

    `resolve_call` is the EXACT Resolve spelling, or "" when the route
    writes no Resolve call of its own (the OTIO route writes a JSON
    parameter).  `read_back` is what proves the write landed.
    `audible_verification` is what proves the write is what was ASKED
    for - a measurement of the rendered audio, never an API return value.

    `refusal` is what an unsupported operation says.  It is not empty
    for a refused operation, and it names the operation.
    """

    operation_id: str
    audio_operation: str
    intent: str
    route: str
    resolve_call: str = ""
    read_back: str = ""
    audible_verification: str = ""
    applies_to: str = ""
    preserves: tuple[str, ...] = ()
    rebuilds: bool = False
    refusal: str = ""
    evidence: tuple[str, ...] = field(default=())

    @property
    def supported(self) -> bool:
        """Whether a route writes this operation at all."""
        return self.route in (ROUTE_NATIVE, ROUTE_OTIO)


#: The shipped API surface this module's spellings come from.  The
#: installed Resolve's own scripting declarations, read on the machine:
#:
#:   ``DaVinciResolveScript.pyi`` - ``TimelineItemProperties`` carries
#:   ``AudioVolumeEnabled``/``AudioVolume`` (dB, -100.0 to 30.0),
#:   ``AudioPanEnabled``/``AudioPan``, ``AudioPitchEnabled``/
#:   ``AudioPitchSemiTones``/``AudioPitchCents`` and the voice-isolation
#:   and dialogue-leveler keys.  ``TimelineItem`` carries
#:   ``SetFades``/``GetFades`` and ``SetClipEnabled``/``GetClipEnabled``.
#:
#:   ``CHANGELOG.md`` (21.1) - "TimelineItem properties - Get/Set Property
#:   includes native audio properties and enabled states."
#:
#: The spelling Ren tried before this - ``SetProperty("Volume")`` - is
#: not one of them, which is why a declaration naming it declares a call
#: that answers False.
API_SOURCE = {
    "resolve_version": "21.1",
    "stubs": "DaVinciResolveScript.pyi (TimelineItemProperties, TimelineItem)",
    "changelog": "CHANGELOG.md 21.1: TimelineItem properties - Get/Set "
                 "Property includes native audio properties and enabled "
                 "states",
}

#: The MEASURED evidence: what a rendered file said about each write.
#: Every row here was read off a render, never off an API return value.
#: The report it comes from is in the repository, so a reader can check
#: the numbers instead of taking this module's word for them.
MEASURED_EVIDENCE = {
    "resolve_version": "21.1.0.14",
    "measured_on": "2026-10-21",
    "report": "tests/live/reports/axi012-audio-measurement.json",
    "method": "one scratch timeline in the qualification project, two "
              "synthetic clips (a 440 Hz tone and a 660 Hz tone) placed "
              "linked A/V, six states rendered by Resolve and measured in "
              "the decoded samples (window RMS, window peak, fraction of "
              "digital-zero samples)",
    "readings": {
        "the audio item's property dictionary":
            "GetProperty() on an AUDIO item reports the 21.1 native audio "
            "properties: AudioVolumeEnabled, AudioVolume, AudioPanEnabled, "
            "AudioPan, AudioPitch* and the voice-isolation and "
            "dialogue-leveler keys",
        "SetProperty('Volume')":
            "returns False and reads back absent: the name is not in the "
            "dictionary, so this spelling is not a writer",
        "SetProperty('AudioVolume', -6)":
            "returns True, reads back -6.0, and the render measures "
            "-6.00 dB on the clip's window (-21.09 -> -27.09 dBFS)",
        "SetProperty('AudioPan', 0)":
            "returns True and reads back 0.0; the AUDIBLE effect of a pan "
            "was not measured",
        "SetClipEnabled(False)":
            "returns True, reads back False, and the muted clip's window "
            "is DIGITAL ZERO (every sample zero)",
        "SetFades({'FadeIn': 48})":
            "returns True, reads back {'FadeIn': 48, 'FadeOut': 0}, and "
            "the fade's head window measures 6.5 dB below its tail",
        "the OTIO volume parameter at -6 dB":
            "the level reads back as -6.0 dB off a fresh export of the "
            "imported timeline, and the render measures 7.4 dB down over "
            "the clip's window",
        "the OTIO keyframed volume":
            "the curve is audible: the head window measures 15.8 dB below "
            "unity and the tail is back at unity",
        "the other clip":
            "-21.13 dBFS in EVERY state: the existing mix did not move, "
            "and the linked A/V pair's link groups survive the OTIO "
            "rebuild (link group ids 1 and 2 after the import)",
    },
}


# ── The enumeration ──────────────────────────────────────────────────

AUDIO_OPERATIONS: tuple[AudioOperation, ...] = (
    AudioOperation(
        operation_id="audio.gain",
        audio_operation="gain",
        intent="Set one clip's level, in dB.",
        route=ROUTE_OTIO,
        resolve_call="",
        read_back="the OTIO level read back off a fresh export of the "
                  "imported timeline (otio_mix.read_levels)",
        audible_verification="the rendered audio's measured level against "
                             "the requested dB",
        applies_to="one audio clip",
        preserves=("the clip's placement, transform, markers and native "
                   "transitions", "every other clip's level (the mix)"),
        rebuilds=True,
        evidence=("MEASURED (2026-10-21, Resolve 21.1.0.14): the OTIO "
                  "route's -6 dB reads back as -6.0 dB off a fresh export "
                  "and the render measures 7.4 dB down, with the other "
                  "clip unmoved at -21.13 dBFS",
                  "MEASURED: Resolve 21.1 also writes the level NATIVELY - "
                  "SetProperty('AudioVolume', -6) returns True, reads back "
                  "-6.0 and the render measures EXACTLY -6.00 dB. Ren has "
                  "no executor for that route yet, and the spelling it used "
                  "to try, SetProperty('Volume'), returns False",
                  "the OTIO import REBUILDS the timeline: Fusion comps and "
                  "CDL grades do not survive it (AGENTS.md 5)",
                  "the touch-up request path refuses before it writes: the "
                  "candidate receipt carries no independent audio-render "
                  "evidence (library/tools/edit_execution_adapters.py:"
                  "_audio_gain)"),
    ),
    AudioOperation(
        operation_id="audio.fade",
        audio_operation="fade",
        intent="Fade one clip in and out, in frames.",
        route=ROUTE_NATIVE,
        resolve_call="TimelineItem.SetFades({'FadeIn': n, 'FadeOut': n})",
        read_back="TimelineItem.GetFades() on the target and on every other "
                  "audio item (reel_touchup._audio_fades)",
        audible_verification="the rendered audio's level across the fade "
                             "window, against the fade's shape",
        applies_to="one audio clip",
        preserves=("the clip's record span and source range",
                   "every other audio item's fades"),
        rebuilds=False,
        evidence=("MEASURED (2026-10-21, Resolve 21.1.0.14): "
                  "SetFades({'FadeIn': 48}) returns True, reads back "
                  "{'FadeIn': 48, 'FadeOut': 0}, and the render's head "
                  "window measures 6.5 dB below the clip's tail - the fade "
                  "is audible, and the other clip did not move",
                  "one audio-only request; fade durations are frame-rounded "
                  "at the timeline rate",
                  "the adapter compares every audio item's native fades "
                  "before and after the write"),
    ),
    AudioOperation(
        operation_id="clip.set_enabled",
        audio_operation="mute",
        intent="Switch one clip's contribution off - the MUTE, on an "
               "audio item.",
        route=ROUTE_NATIVE,
        resolve_call="TimelineItem.SetClipEnabled(False)",
        read_back="TimelineItem.GetClipEnabled()",
        audible_verification="the rendered audio over the muted clip's span: "
                             "silence where the clip plays",
        applies_to="one audio item (on a video item the same call "
                   "switches the picture off, which is not a mute)",
        preserves=("the clip's record span and source range",
                   "the clip's link group"),
        rebuilds=False,
        evidence=("MEASURED (2026-10-21, Resolve 21.1.0.14): "
                  "SetClipEnabled(False) returns True, reads back False, "
                  "and the muted clip's window is DIGITAL ZERO - every "
                  "sample zero - while the other clip plays at -21.13 dBFS "
                  "exactly as before",
                  "the mute is the EditPatch `clip.set_enabled` operation "
                  "applied to an audio item; the touch-up's own "
                  "`set_enabled` is OVERLAY-only (V3+ video) and does not "
                  "reach an audio row",
                  "the link group survives the write: the clip's linked "
                  "partners are unchanged"),
    ),
    AudioOperation(
        operation_id="audio.automation",
        audio_operation="automation",
        intent="A keyframed level on one clip.",
        route=ROUTE_OTIO,
        resolve_call="",
        read_back="the OTIO keyframes read back off a fresh export of the "
                  "imported timeline",
        audible_verification="the rendered audio's level at each keyframe, "
                             "against the curve",
        applies_to="one audio clip",
        preserves=("the clip's placement, transform, markers and native "
                   "transitions", "every other clip's level (the mix)"),
        rebuilds=True,
        evidence=("MEASURED (2026-10-21, Resolve 21.1.0.14): the "
                  "keyframed volume is audible - the curve's head window "
                  "measures 15.8 dB below unity and the tail is back at "
                  "unity - and the other clip does not move",
                  "only the `volume` parameter has a measured spelling; "
                  "Resolve's OTIO carries no audio-pan parameter and no "
                  "per-item automation curve (docs/RULE_EVIDENCE.md)",
                  "the OTIO route is the only one that can carry a "
                  "keyframed curve, and it is a BUILD"),
    ),
    AudioOperation(
        operation_id="audio.pan",
        audio_operation="pan",
        intent="Place one clip in the stereo field.",
        route=ROUTE_REFUSED,
        resolve_call="",
        read_back="",
        audible_verification="",
        preserves=(),
        rebuilds=False,
        refusal=("audio.pan: Resolve's OTIO carries no measured audio-pan "
                 "parameter - the audio surface Ren writes is volume only "
                 "(docs/RULE_EVIDENCE.md)"),
        evidence=("the OTIO route refuses by name",
                  "MEASURED (2026-10-21, Resolve 21.1.0.14): "
                  "SetProperty('AudioPan', 0) returns True and reads back "
                  "0.0, so the ITEM can take a pan - but the audible "
                  "effect of a pan was NOT measured, and Ren has no "
                  "executor that writes one"),
    ),
)

BY_ID = {operation.operation_id: operation for operation in AUDIO_OPERATIONS}


def operation(operation_id: str) -> AudioOperation:
    """The row for one audio operation, or a refusal that names the miss."""
    row = BY_ID.get(operation_id)
    if row is None:
        raise KeyError(
            f"{operation_id!r} is not an audio operation Ren knows; the "
            f"enumeration is {sorted(BY_ID)}")
    return row


def route(operation_id: str) -> str:
    """Which route writes this operation."""
    return operation(operation_id).route


def supported(operation_id: str) -> bool:
    """Whether a route writes this operation at all."""
    return operation(operation_id).supported


def refusal(operation_id: str) -> str:
    """What the operation says when it cannot be executed.

    An operation with a route returns "" - there is nothing to refuse.
    A refused operation returns its refusal, which names the operation
    and the surface that is missing.
    """
    row = operation(operation_id)
    if row.supported:
        return ""
    return row.refusal or (
        f"{operation_id}: no route writes this operation "
        f"(library/tools/audio_operations.py)")


def rows() -> list[dict]:
    """Every audio operation, as a table the operation map can publish."""
    return [{
        "operation_id": row.operation_id,
        "audio_operation": row.audio_operation,
        "intent": row.intent,
        "route": row.route,
        "resolve_call": row.resolve_call,
        "read_back": row.read_back,
        "audible_verification": row.audible_verification,
        "applies_to": row.applies_to,
        "preserves": list(row.preserves),
        "rebuilds": row.rebuilds,
        "refusal": row.refusal,
        "evidence": list(row.evidence),
    } for row in AUDIO_OPERATIONS]
