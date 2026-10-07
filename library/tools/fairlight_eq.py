"""The Fairlight EQ recipe: a dialogue chain intent as UI steps.

The Resolve Hands plan (section 9) says the route is OpenPage fairlight
-> locate the Dialogue track via AX/OCR -> open EQ -> set
frequency/gain, with AX values where exposed and Vision/OCR otherwise.
This module is that route as a deterministic `computer_use` Script,
built from a validated intent track (`audio_chain.validate_track_chain`
- its first production caller) plus a controls map the recon fills in:

- every step records its anchor's provenance (`source`): `probe`
  where a lane measured the route, `plan` where the string comes from
  the captain's plan and no run has confirmed it yet, `ren` where Ren
  measured the value. A live run that confirms a plan-stated anchor
  promotes it; a live run that refutes one fixes this file.
- every control the route touches but recon has not mapped is a BUILD
  refusal naming the gap - never a guessed label. The page switch
  (menu path, plan-stated; the page NAME is probe-measured in
  `probe_resolve_capabilities`) and the Dialogue track label (the
  plan's own words) are the only locators this file states.
- a set step carries the driver's own string (`actions.set_value`
  takes strings; the recipe formats, the runner never does).

What this module does NOT do, on purpose: sends and bus routing
(refused by name - no bus architecture to route on, never silently
dropped); EQ, dynamics or gate on master and a limiter anywhere but
master (each refused naming where it belongs); Q defaults and
non-default knee/makeup without mapped controls (an unmapped control
refuses rather than inventing one); the render or its measurement
(the proof is render -> `audio_verify` findings on a committed live
run, which promotes or fixes the plan-stated anchors).

Run it with the existing verb - no new entry point: emit the script
JSON (`emit_script_file`) and run `ren computer-use script <file>`.
"""

from __future__ import annotations

import json

try:
    from library.tools import audio_chain as _chain
    from library.tools.computer_use.scripts import (
        Anchor,
        Expect,
        Script,
        ScriptStep,
    )
except ImportError:  # imported as `tools.fairlight_eq` from inside library/
    from tools import audio_chain as _chain
    from tools.computer_use.scripts import (
        Anchor,
        Expect,
        Script,
        ScriptStep,
    )

#: The one role this executor covers, end to end. Any other role is
#: refused by name - a bus or master chain reaches the executor slice
#: that owns it, not this one.
EXECUTOR_ROLE = "dialogue"

#: Intent keys no EQ step can apply. Each is refused naming itself.
NON_EQ_KEYS = ("dynamics", "limiter", "gate", "sends")

#: Band types that carry no gain control on the EQ. A filter names
#: no gain (its schema 0.0 is the control's own absence); mapping one
#: is refused as a recon error, not silently ignored.
GAINLESS_TYPES = ("high_pass", "low_pass")


def _format(value) -> str:
    """The driver's own string for a schema number.

    Integers bare (`80`, not `80.0` - a Fairlight field reads what it
    is shown); anything else as Python renders it. The runner takes
    strings only, so formatting lives here, once, where the tests pin
    every string it emits.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"EQ controls are numbers, got {value!r}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"EQ control is not finite: {value!r}")
    if number.is_integer():
        return str(int(number))
    return str(number)


def dialogue_track(intent: dict) -> dict:
    """The dialogue track of a chain intent, or a refusal naming roles."""
    tracks = (intent or {}).get("tracks", []) if isinstance(intent,
                                                             dict) else []
    for track in tracks:
        if isinstance(track, dict) and track.get("role") == EXECUTOR_ROLE:
            return track
    roles = sorted({t.get("role", "?") for t in tracks
                    if isinstance(t, dict)})
    raise ValueError(
        f"no {EXECUTOR_ROLE!r} track in this intent (roles: {roles}) - "
        f"the EQ executor covers the dialogue bus only")


def _locator(name: str, spec) -> Anchor:
    """One recon-mapped locator: an AX path or an OCR label, plan-marked.

    `spec` is `{"path": [...]}` (AX menu/element path) or `{"text":
    ...}` (on-screen label). Anything else is a malformed controls map
    and fails naming the control, because a locator that is neither is
    a guess with nowhere to resolve.
    """
    if isinstance(spec, dict) and spec.get("path"):
        return Anchor(via="ax_path", params={
            "path": list(spec["path"]), "source": "plan",
            "why": (f"{name}: AX path from recon; unconfirmed until a "
                    f"live run resolves it"),
        })
    if isinstance(spec, dict) and spec.get("text"):
        return Anchor(via="ocr", params={
            "text": spec["text"], "source": "plan",
            "why": (f"{name}: on-screen label; unconfirmed until a "
                    f"live run clicks it"),
        })
    raise ValueError(
        f"no locator for {name}: map an AX path or an OCR label - "
        f"the recipe guesses neither")


#: The roles one chain script covers. Dialogue carries EQ and
#: dynamics; the master carries the limiter and nothing else. Any
#: other role is refused by name - its executor slice owns it.
CHAIN_ROLES = ("dialogue", "master")

#: Dynamics field -> the set action that writes it. Explicit, so a
#: renamed field cannot silently mint a new action string.
DYNAMICS_ACTIONS = {
    "threshold_db": "set_dynamics_threshold",
    "ratio": "set_dynamics_ratio",
    "attack_ms": "set_dynamics_attack",
    "release_ms": "set_dynamics_release",
    "knee_db": "set_dynamics_knee",
    "makeup_gain_db": "set_dynamics_makeup",
}


def _page_step() -> ScriptStep:
    """The Fairlight page switch, shared by every chain script."""
    return ScriptStep(
        action="page_fairlight",
        anchors=[Anchor(via="ax_path", params={
            "path": ["Workspace", "Switch to Page", "Fairlight"],
            "source": "plan",
            "why": ("plan section 9 route; the page name is "
                    "probe-measured, the full path unconfirmed"),
        })],
        expect=Expect(via="pixels_changed", params={"source": "plan"}),
    )


def _track_step(controls: dict, label: str, default_text: str) -> ScriptStep:
    """Focus one bus by its recon locator, defaulting to the plan's
    own words for the bus name."""
    return ScriptStep(
        action="locate_track",
        anchors=[_locator(label, (controls.get("track") or {
            "text": default_text}))],
    )


def _eq_open_step(controls: dict) -> ScriptStep:
    """Open the track EQ panel."""
    return ScriptStep(
        action="open_eq",
        anchors=[_locator("track EQ", controls.get("eq_open"))],
        expect=Expect(via="pixels_changed", params={"source": "plan"}),
    )


def eq_script(track: dict, controls: dict) -> Script:
    """The UI steps that apply one dialogue EQ track in Fairlight.

    `track` is a validated intent track (`validate_track_chain`
    refuses an impossible band before any step is built). `controls`
    is the recon map: `{"track": locator, "eq_open": locator,
    "bands": [{"frequency": locator, "gain": locator|None, "q":
    locator|None}, ...]}` aligned by index to the track's EQ bands.
    A high-pass takes no gain step (its control does not exist); any
    other band type needs frequency, gain AND Q mapped, else the whole
    script is refused naming the gap - a half-applied EQ is worse than
    none. Steps beyond the page switch carry no expectations the route
    cannot keep: acceptance (the driver took the action) is the check,
    and the render measurement is the proof (slice 2b).
    """
    checked = _chain.validate_track_chain(track)
    if checked.role != EXECUTOR_ROLE:
        raise ValueError(
            f"EQ executor covers {EXECUTOR_ROLE!r}, not "
            f"{checked.role!r} - a {checked.role} chain reaches the "
            f"executor slice that owns it")
    for key in NON_EQ_KEYS:
        if getattr(checked, key, None):
            raise ValueError(
                f"EQ executor does not apply {key!r} - refused, never "
                f"silently dropped")
    if not checked.eq:
        raise ValueError(
            "no EQ bands on this track - a script that drives UI must "
            "do something")
    if not isinstance(controls, dict):
        raise ValueError("controls must be the recon map, not "
                         f"{type(controls).__name__}")
    band_controls = controls.get("bands", [])
    if len(band_controls) != len(checked.eq):
        raise ValueError(
            f"controls map {len(band_controls)} band(s) for "
            f"{len(checked.eq)} intent band(s) - align them by index")

    steps = [
        _page_step(),
        _track_step(controls, "dialogue track", "Dialogue"),
        _eq_open_step(controls),
    ]
    for index, band in enumerate(checked.eq):
        spec = band_controls[index]
        if not isinstance(spec, dict):
            raise ValueError(
                f"no controls mapped for band {index} "
                f"({band.type} {band.frequency_hz:g} Hz)")
        steps.extend(_band_steps(index, band, spec))
    return Script(
        goal=(f"Apply the dialogue EQ ({len(checked.eq)} band(s)) on "
              f"the Fairlight page"),
        steps=steps,
    )


def chain_script(track: dict, controls: dict) -> Script:
    """The UI steps that apply one chain track in Fairlight.

    The executor's whole vocabulary beyond EQ: a dialogue track with
    EQ bands, declared dynamics and/or a declared gate, a master track
    with a declared limiter. Sends, a limiter anywhere but master, EQ,
    dynamics or gate on master are refused by name - stranded or owned
    elsewhere, never silently dropped. Dynamics needs its four mapped
    controls plus knee/makeup wherever the intent leaves the schema
    default; the gate needs all six mapped; the limiter needs ceiling
    and release mapped. The recon map gains `dynamics_open`,
    `dynamics`, `gate` and `limiter` beside the EQ keys; everything
    unmapped refuses naming the gap, exactly like the EQ half. Steps
    carry no expectations the route cannot keep: acceptance is the
    check, the render measurement the proof.
    """
    checked = _chain.validate_track_chain(track)
    if checked.role not in CHAIN_ROLES:
        raise ValueError(
            f"chain executor covers {list(CHAIN_ROLES)}, not "
            f"{checked.role!r} - a {checked.role} chain reaches the "
            f"executor slice that owns it")
    if getattr(checked, "sends", None):
        raise ValueError(
            "chain executor does not apply 'sends' - refused, "
            "never silently dropped")
    if not isinstance(controls, dict):
        raise ValueError("controls must be the recon map, not "
                         f"{type(controls).__name__}")
    if checked.role == "dialogue":
        if checked.limiter is not None:
            raise ValueError(
                "chain executor does not apply a dialogue limiter - "
                "the limiter lives on master, refused")
        if (not checked.eq and checked.dynamics is None
                and checked.gate is None):
            raise ValueError(
                "no EQ bands, no dynamics and no gate on this track - "
                "a script that drives UI must do something")
        steps = [
            _page_step(),
            _track_step(controls, "dialogue track", "Dialogue"),
        ]
        if checked.eq:
            band_controls = controls.get("bands", [])
            if len(band_controls) != len(checked.eq):
                raise ValueError(
                    f"controls map {len(band_controls)} band(s) for "
                    f"{len(checked.eq)} intent band(s) - align them by "
                    f"index")
            steps.append(_eq_open_step(controls))
            for index, band in enumerate(checked.eq):
                spec = band_controls[index]
                if not isinstance(spec, dict):
                    raise ValueError(
                        f"no controls mapped for band {index} "
                        f"({band.type} {band.frequency_hz:g} Hz)")
                steps.extend(_band_steps(index, band, spec))
        if checked.dynamics is not None or checked.gate is not None:
            steps.append(_dynamics_open_step(controls))
        if checked.dynamics is not None:
            steps.extend(_dynamics_steps(checked.dynamics,
                                         controls.get("dynamics")))
        if checked.gate is not None:
            steps.extend(_gate_steps(checked.gate,
                                     controls.get("gate")))
        parts = []
        if checked.eq:
            parts.append(f"{len(checked.eq)} EQ band(s)")
        if checked.dynamics is not None:
            parts.append("dynamics")
        if checked.gate is not None:
            parts.append("gate")
        goal = (f"Apply the dialogue chain ({', '.join(parts)}) on "
                f"the Fairlight page")
    else:
        for key in ("eq", "dynamics", "gate"):
            if getattr(checked, key, None):
                raise ValueError(
                    f"chain executor does not apply master {key!r} - "
                    f"refused, never silently dropped")
        if checked.limiter is None:
            raise ValueError(
                "no limiter on this master track - a script that "
                "drives UI must do something")
        steps = [
            _page_step(),
            _track_step(controls, "master bus", "Master"),
        ]
        steps.extend(_limiter_steps(checked.limiter,
                                    controls.get("limiter")))
        goal = "Apply the master limiter on the Fairlight page"
    return Script(goal=goal, steps=steps)


def _dynamics_open_step(controls: dict) -> ScriptStep:
    """Open the track Dynamics panel."""
    return ScriptStep(
        action="open_dynamics",
        anchors=[_locator("track Dynamics", controls.get(
            "dynamics_open"))],
        expect=Expect(via="pixels_changed", params={"source": "plan"}),
    )


def _dynamics_steps(dynamics, spec) -> list:
    """One set step per mapped dynamics control: threshold, ratio,
    attack, release, then knee/makeup wherever the intent leaves the
    schema default. An unmapped required control refuses the whole
    script; an unmapped defaulted one is omitted (nothing to set).
    Every value is the driver's own string."""
    if not isinstance(spec, dict):
        raise ValueError(
            "no dynamics controls mapped - recon has not mapped the "
            "Dynamics panel, refusing")
    out = []
    for field in ("threshold_db", "ratio", "attack_ms", "release_ms"):
        control = spec.get(field)
        if not isinstance(control, dict) or not control.get("path"):
            raise ValueError(
                f"no {field} control mapped - refusing compression "
                f"that would land at an unknown setting")
        out.append(ScriptStep(
            action=DYNAMICS_ACTIONS[field],
            anchors=[_value_locator(
                f"compression {field}", control,
                _format(getattr(dynamics, field)))],
        ))
    for field in ("knee_db", "makeup_gain_db"):
        value = float(getattr(dynamics, field))
        control = spec.get(field)
        if value == 0.0 and not control:
            continue
        if not isinstance(control, dict) or not control.get("path"):
            raise ValueError(
                f"no {field} control mapped for a non-default "
                f"{value:g} - refusing compression that would land at "
                f"an unknown shape")
        out.append(ScriptStep(
            action=DYNAMICS_ACTIONS[field],
            anchors=[_value_locator(
                f"compression {field}", control, _format(value))],
        ))
    return out


def _limiter_steps(limiter, spec) -> list:
    """Ceiling and release writes on the master bus. Both mapped, or
    the script refuses - a limiter at an unknown ceiling is not a
    limiter."""
    if not isinstance(spec, dict):
        raise ValueError(
            "no limiter controls mapped - recon has not mapped the "
            "master limiter, refusing")
    out = []
    for field, label in (("ceiling_dbTP", "ceiling"),
                        ("release_ms", "release")):
        control = spec.get(label)
        if not isinstance(control, dict) or not control.get("path"):
            raise ValueError(
                f"no limiter {label} control mapped - refusing a "
                f"limiter that would land at an unknown {label}")
        out.append(ScriptStep(
            action=f"set_limiter_{label}",
            anchors=[_value_locator(
                f"limiter {label}", control,
                _format(getattr(limiter, field)))],
        ))
    return out


#: Gate field -> the set action that writes it. Explicit, like the
#: dynamics map, so a renamed field cannot silently mint an action.
GATE_ACTIONS = {
    "threshold_db": "set_gate_threshold",
    "ratio": "set_gate_ratio",
    "attack_ms": "set_gate_attack",
    "release_ms": "set_gate_release",
    "hold_ms": "set_gate_hold",
    "range_db": "set_gate_range",
}


def _gate_steps(gate, spec) -> list:
    """One set step per gate control, all six mapped or the script
    refuses - a gate at an unknown threshold is not a gate. The
    schema names no defaults, so unlike knee/makeup there is nothing
    safe to omit."""
    if not isinstance(spec, dict):
        raise ValueError(
            "no gate controls mapped - recon has not mapped the gate "
            "panel, refusing")
    out = []
    for field, action in GATE_ACTIONS.items():
        control = spec.get(field)
        if not isinstance(control, dict) or not control.get("path"):
            raise ValueError(
                f"no gate {field} control mapped - refusing gating "
                f"that would land at an unknown setting")
        out.append(ScriptStep(
            action=action,
            anchors=[_value_locator(
                f"gate {field}", control,
                _format(getattr(gate, field)))],
        ))
    return out


def _band_steps(index: int, band, spec: dict) -> list:
    """One script step per control the band carries: frequency always,
    gain and Q where the control exists. A peaking-family band with
    gain or Q unmapped refuses the whole script - the cut would land
    at an unknown level or width, and a half-applied EQ is worse than
    none. Every step names its band, so the receipt reads per control.
    """
    label = f"band {index} ({band.type} {band.frequency_hz:g} Hz)"
    wants_gain = band.type not in GAINLESS_TYPES
    gain_spec = spec.get("gain")
    q_spec = spec.get("q")
    if wants_gain and not gain_spec:
        raise ValueError(
            f"no gain control mapped for {label} - refusing a cut "
            f"that would land at an unknown level")
    if wants_gain and not q_spec:
        raise ValueError(
            f"no Q control mapped for {label} - refusing a cut that "
            f"would land at an unknown width")
    if not wants_gain and gain_spec:
        raise ValueError(
            f"a gain control mapped for {label}, which names no gain - "
            f"the recon map is wrong, refusing")
    out = [ScriptStep(
        action="set_band_frequency",
        anchors=[_value_locator(f"{label} frequency",
                                spec.get("frequency"),
                                _format(band.frequency_hz))],
    )]
    if wants_gain:
        out.append(ScriptStep(
            action="set_band_gain",
            anchors=[_value_locator(f"{label} gain", gain_spec,
                                    _format(band.gain_db))],
        ))
        out.append(ScriptStep(
            action="set_band_q",
            anchors=[_value_locator(f"{label} Q", q_spec,
                                    _format(band.q))],
        ))
    return out


def _value_locator(name: str, spec, value: str) -> Anchor:
    """An AX value write: the path recon mapped plus the driver's own
    string. OCR labels cannot take values - a control found by pixels
    alone has no field to set, so a text-only map refuses here."""
    if isinstance(spec, dict) and spec.get("path"):
        return Anchor(via="ax_path", params={
            "path": list(spec["path"]), "value": value,
            "source": "plan",
            "why": (f"{name} = {value}: AX path from recon; "
                    f"unconfirmed until a live run sets it"),
        })
    raise ValueError(
        f"no AX path for {name} - a value needs a field, and an OCR "
        f"label is not one")


def emit_script_file(track: dict, controls: dict, path: str) -> str:
    """Write the recipe for the existing script verb to run.

    `ren computer-use script <path>` reads exactly what
    `Script.from_dict` parses, so the file round-trips through the
    verb's own gate before anything drives UI. The whole chain
    travels (EQ, dynamics, limiter); an EQ-only dialogue track emits
    exactly what `eq_script` builds.
    """
    script = chain_script(track, controls)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(script.to_dict(), handle, indent=2)
        handle.write("\n")
    return path
