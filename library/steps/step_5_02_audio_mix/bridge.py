#!/usr/bin/env python3
"""Step 5.02 pre-bridge: measure the bed and the speech, once.

The mix engineer is shown what this bed measures, what the speech under
each window measures, and what separation the level the engine used to
hold would actually deliver on this material.  Nothing here concludes
anything: the columns are defined in `mix.legend` and the judgement is
asked for in `handoff.md`.

The loudness passes run HERE and nowhere else.  `post_bridge.py` receives
this output from the runner and joins the answer to it, so the two halves
of the step cannot measure separately and disagree - 5.01's rule for the
same reason (`library/steps/step_5_01_color_grade/post_bridge.py`).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from library.steps.step_5_02_audio_mix.mix import measure


def main():
    data = json.loads(sys.stdin.read())
    output = measure(
        data.get("audio_spine", {}) or {},
        data.get("music_selection", {}) or {},
        data.get("a_roll_assignments"))

    bed = output["bed_measurements"]
    if bed.get("measured"):
        print(f"  Bed: {bed.get('title') or 'untitled'} at "
              f"{bed.get('integrated_lufs')} LUFS integrated, speech-band "
              f"ratio {bed.get('speech_band_ratio_db')} dB.", file=sys.stderr)
    else:
        print(f"  Bed level unknown: {bed['measurement_note']}",
              file=sys.stderr)

    measured = sum(1 for w in output["mix_windows"]
                   if w["speech_lufs"] is not None)
    print(f"  Speech measured on {measured} of "
          f"{len(output['mix_windows'])} windows.", file=sys.stderr)

    noisy = [(s.get("source_file", "?"), (s.get("floor") or {}).get("level_dbfs"))
             for s in (output.get("cleanup_context") or {}).get("sources", [])]
    for source, floor in noisy:
        print(f"  Noise floor {source.split('/')[-1]}: "
              f"{floor if floor is not None else 'unmeasured'} dBFS.",
              file=sys.stderr)

    json.dump(output, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
