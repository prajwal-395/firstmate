# AXI-012b: the gain executes, verified in the rendered audio

2026-10-21. What PR 3053 left refusing, and the request-time render
that lets the gain execute: the native write, the measurement, and
what was also measured (the pan's scale) on the way.

**The rule:** An audio operation's route is the one that was MEASURED in the
RENDER, and a gain that cannot be heard is rolled back to the level it found.
`library/tools/audio_operations.py` is the ONE enumeration of the audio
operations, their route, their exact Resolve spelling, their read-back and
their audible verification; `library/tools/audio_render_evidence.py` is the
measurement that decides the verdict.

**What happened.** AXI-012 (PR 3053) measured the audio ROUTES on Resolve
21.1.0.14 and found that the item's property dictionary carries the native
audio keys: `SetProperty("AudioVolume", -6)` returns True, reads back -6.0,
and the render measures EXACTLY -6.00 dB; `SetClipEnabled(False)` mutes the
clip to DIGITAL ZERO; `SetFades` fades audibly. The same lane left the
executor REFUSING, and the refusal named its own reason: "the candidate
receipt carries no independent audio-render evidence". Nothing rendered the
audio at REQUEST time, so no gain could be said to execute, and the operation
map still said the gain was implemented - a declaration an agent would read
and a RuntimeError it would meet.

The gap was the RENDER, and the render was the easy half: PR 3053's own
measurement had already rendered the timeline and measured the samples. What
was missing was a render AT THE MOMENT OF THE WRITE, with the level read
back and the mix measured around it.

**The measurement (2026-10-21, Resolve 21.1.0.14, through the serving broker
on a Ren-owned scratch timeline, `tests/live/reports/axi012b-audio-gain.json`):**

| measurement | before | after |
|---|---|---|
| the native gain write | refused before mutation | reads back -6.0, and the RENDER measures EXACTLY -6.00 dB on the clip's window (-22.11 -> -28.11 dBFS) |
| the other clip's window | -21.09 dBFS | -21.09 dBFS (delta 0.0: the mix did not move) |
| the linked A/V group | one partner | the same partner before and after |
| the level afterwards | -6.0 | restored to 0.0 (the probe restores what it found) |
| the PAN (never measured before) | "the audible effect was not measured" | `AudioPan` reads back exactly and the render follows it: -100 moves the left channel 12.0 dB up, +100 the right, 0 leaves the channels identical (their difference is digital silence), and the scale is -100..100 (+/-50 gives +/-9.5 dB) |

**Where it is.** The executor is `resolve-axi edit audio-gain`
(`library/tools/resolve_axi_core_commands.py:cmd_edit_audio_gain`), bound to
the semantic request as `audio.gain` on the `resolve_axi` backend
(`library/tools/edit_execution_adapters.py:_audio_gain`). It renders the
timeline before and after the write, measures the decoded samples
(`library/tools/audio_render_evidence.py`), and judges the change against
the requested dB AND every other audio window's level. A write that does not
verify is ROLLED BACK: the previous level is written back and read back, and
the verb fails. The acceptance contract the executor fills in is the one the
operation already declared (`library/tools/operation_acceptance.py`): the
target identity, the audio span, the parameters, the unrelated items, the
RENDER against the request, no clipping and no unintended silence.

**What it does not fix.** The PAN still has no executor: its route is now
MEASURED (the sweep above), and `audio.pan` refuses BY NAME, naming the
measured spelling and scale for whoever writes it. The AUTOMATION still has
no executor: the OTIO build carries the curve and REBUILDS the timeline, and
that is the route it stays on. The gain writes IN PLACE, so the target
timeline is the one that changes - a reel's own build-and-propose gate is
not part of this operation. And the audible verification costs TWO renders
of the whole timeline per request, which is why the fade and the mute still
travel without one.