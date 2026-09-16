#!/usr/bin/env python3
"""Step 5.02 post-bridge: the mix engineer's answer becomes the automation.

Reads the separation the engineer named for each behaviour, puts it
through the one ladder (`library/tools/decided_value.decide`), solves the
clip gain that delivers it from the bed's and the speech's own
measurements, and writes `audio_mix_spec`.

**Three things this deliberately does not do**, and they are 5.01's three.

It does not substitute a level for a behaviour the answer left out.  The
ladder falls to the REGISTERED fallback, which is recorded AS a fallback
naming whose mix it is, or to UNDETERMINED - which carries no gain at all
and says so.

It does not clamp.  How far above the bed a voice sits is the engineer's,
the same way `series_look` puts no bound on a declared slope.  A bed
mastered 8.6 dB hotter than the speech needs a number a bounded scale
would have refused, which is the captain's own "it could very well be
possible that we need to use values outside of these bounds".

And it does not read an empty answer as agreement.  A run where nobody
decided records `fallback` or `undetermined` per window, and those are
different facts from a decision that happened to land on the same number.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from library.steps.step_5_02_audio_mix.mix import (
    SLOT,
    assemble,
    solve_automation,
)
from library.tools import decided_value


def _speech_reference(windows, scope):
    """The speech level a separation for `scope` is measured against.

    Returns `(lufs, basis, windows_measured)`.

    The loudest measured speech under that behaviour, because the window
    that has to stay intelligible is the one that decides whether the bed
    is too loud and a mean would hide it.

    `prominent` means music leads with no competing speech, so its
    windows usually carry none at all - and a scope with nothing to
    measure against would fall to the fallback on every run, which is the
    constant surviving in a new place. Its reference is therefore the
    PIECE's own speech: where the bed should sit when it leads, relative
    to the voice the rest of the video is carried by. That is still a
    measurement of this material, and the basis it was read on is
    recorded beside it rather than left for a reader to assume.
    """
    own = [w["speech_lufs"] for w in windows
           if w["music_behavior"] == scope
           and isinstance(w["speech_lufs"], (int, float))]
    if own:
        return (max(own),
                f"the loudest measured speech under a {scope!r} window",
                len(own))
    everywhere = [w["speech_lufs"] for w in windows
                  if isinstance(w["speech_lufs"], (int, float))]
    if everywhere:
        return (max(everywhere),
                (f"no {scope!r} window carries measured speech, so the "
                 f"reference is the loudest speech in the piece - where the "
                 f"bed sits when it leads, against the voice the rest of the "
                 f"video is carried by"),
                len(everywhere))
    return None, "no window in this edit carries measured speech", 0


def resolve_audio_mix(data: dict) -> dict:
    """Join the engineer's answer to the pre-bridge's measurements.

    `data` is the merged dict the runner hands a post-bridge: the step's
    inputs, the pre-bridge's output, and what the model answered.
    """
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")

    pre_output = {
        "bed_measurements": data.get("bed_measurements") or {},
        "mix_windows": data.get("mix_windows") or [],
        "mix_decision_legend": data.get("mix_decision_legend") or {},
    }
    bed = pre_output["bed_measurements"]

    # The measurements the judgement is made over, named exactly as the
    # slot names them so the record and the solver read the same keys.
    row = decided_value.slot(SLOT)
    decisions = []
    by_scope = {}
    for scope in row.scope_names():
        speech_lufs, basis, measured = _speech_reference(
            pre_output["mix_windows"], scope)
        measurements = {
            "bed_integrated_lufs": (bed.get("integrated_lufs")
                                    if bed.get("measured") else None),
            "speech_lufs": speech_lufs,
            "speech_reference_basis": basis,
            "windows_measured": measured,
        }
        decision = decided_value.decide(
            SLOT, scope=scope,
            project_folder=data.get("project_folder", ""),
            creative_direction=data.get("creative_direction"),
            model_answer=decided_value.answers_from(data, SLOT, scope),
            measurements=measurements,
            step="audio_mix")
        decisions.append(decision.as_record())
        by_scope[scope] = decision.as_record()

    automation, undetermined = solve_automation(pre_output, by_scope)

    for line in decided_value.summary_lines(decisions):
        print(f"  {line}", file=sys.stderr)
    for window in undetermined:
        print(f"  Window {window['spine_block_position']} carries no bed "
              f"level: {window['why']}", file=sys.stderr)

    return assemble(pre_output, decisions, automation, undetermined)


def main():
    data = json.loads(sys.stdin.read())
    json.dump(resolve_audio_mix(data), sys.stdout, indent=2)


if __name__ == "__main__":
    main()
