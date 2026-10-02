# `library.tools.dialogue_cleanup` - the history behind its contract

This is the module docstring of `library/tools/dialogue_cleanup.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Dialogue cleanup: plan-requested noise treatment for speech, never a default.

Fidelity rung R5d (Ears): EQ, dynamics and noise reduction were a no-op
stub returning True (`fairlight_presets.py`, rung 1 removed the fake
success), and the `audio_mix` model call was skipped on an empty schema
so no brief could shape the mix. The research answer for speech and mix
says DeepFilterNet first (dual MIT/Apache-2.0, Apple Silicon binaries,
offline) plus Resolve Voice Isolation via
`Timeline.SetVoiceIsolationState` (amount 0-100, Studio) as the in-app
path; rung 3a measured the track call returning True and reading back.

That research is landscape evidence. What ships here was measured
locally first, on real dialogue from the captain's projects (16 s
excerpts, 48 kHz mono, this machine, CPU, 2026-09-24):

================= ==================== ==================== ============
clip              floor before->after  speech before->after WER vs orig
================= ==================== ==================== ============
akshita (podcast) -44.1 -> -50.1 dBFS  -27.10 -> -27.23     0.000 (46 w)
craig (podcast)   unmeasurable (dense) -26.49 -> -26.70     0.019 (53 w)
img1816 (vlog)    -29.7 -> -34.5 dBFS  -27.80 -> -29.86     see note
img1822 (vlog)    -54.1 -> -58.2 dBFS  -23.72 -> -24.74     0.000 (54 w)
================= ==================== ==================== ============

DeepFilterNet3 (`deepfilternet==0.5.6`, `deepfilterlib==0.5.6` cp311
macOS-arm64 wheel, torch 2.8/torchaudio 2.8 pair): ~0.3 s inference
per 16 s clip (RT factor ~0.02), words intact. img1816 note: 10 token
edits over 28 words, all number verbalization ("25th, 2026" vs
"twenty fifth, twenty twenty six") and two uncertain function words;
same sentences, same timestamps within 0.2 s. The Voz self-rerun
control on the uncleaned file is 0 edits, so the delta comes from the
cleanup's spectral change, not ASR noise - and re-measuring after
loudness-normalising the cleaned file to the original gives the same
10, so it is not the level either. Meaning preserved, tokens shifted
where Voz was already uncertain.

Voice Isolation (`Timeline.SetVoiceIsolationState` on audio track 1,
the rung-3a verb with read-back), rendered from a throwaway project on
the same 16 s img1816 excerpt, same day, same machine (renders ~1.2 s
each; the off render reproduces the source at floor -29.8 dBFS, speech
-27.83 LUFS, WER 0.000, so the render path itself is transparent):

================= ==================== ==================== ============
amount            floor                speech               WER vs orig
================= ==================== ==================== ============
off               -29.8 dBFS           -27.83 LUFS          0.000 (28 w)
30                -33.0 dBFS           -29.56 LUFS          0.321
60                -33.2 dBFS           -29.67 LUFS          0.321
90                -33.3 dBFS           -29.77 LUFS          0.393
================= ==================== ==================== ============

Two readings the plan should know: the floor drop is ~3.4 dB with
almost no difference between 30 and 90 (diminishing returns across the
scale, not a linear dial), and the word deltas are the same shape as
DeepFilterNet's - number verbalization plus uncertain function words,
same sentences, same timestamps. On this clip DeepFilterNet lowered
the floor further (-34.5); on dense speech neither tool has a gap to
measure and the speech level moves ~0.2 dB either way.

Two things that conditioning measured, and they constrain the product:

 1. The pip install does NOT belong in `requirements.txt`. The shared
   ML venv is Python 3.12 with numpy 2.5.3; `deepfilternet` pins
   `numpy>=1.22,<2.0` and `deepfilterlib` ships no cp312 macOS-arm64
   wheel (cp310/cp311 only). Pinning it would downgrade numpy fleet-wide
   and break the torch pair every lane runs on. The first
   torch 2.14/torchaudio 2.11 attempt failed exactly the way
   AGENTS.md 9 documents (`torchaudio.backend` removed); the working
   pair is torch 2.8/torchaudio 2.8 with Python 3.11. So the engine
   invokes DeepFilterNet opportunistically - `df` import, else the
   `deep-filter` Rust binary at the shared-environment location
   (`<vep_home>/bin/deep-filter`, `PIPELINE_DEEPFILTER_BINARY` to name
   another, `scripts/install_deepfilternet.sh` to fill it), else the
   same binary on PATH - and REFUSES BY NAME when none answers,
   instead of shipping a pin that breaks the build.
2. Voice Isolation's audio effect (floor drop per amount, word safety)
   is measured in Resolve at build-prove time; rung 3a measured the
   call itself. Amounts are Resolve's own 0..100 scale - the engine
   offers no scale of its own (AGENTS.md 10.5).

What this module owns (one enumeration, like every vocabulary here):

- `TOOLS`: cleanup and effect tools the build can stage or apply.
  Anything else is refused by name in `validate_cleanup_request`, never
  dropped.
- Availability probes with the reason attached, so the plan context
  states what the build can actually do.
- `run_deepfilternet`: source range in, stem file out, with wall time
  and before/after measurements on the record.
- `apply_voice_isolation`: the track call with Get read-back, the same
  discipline as the `audio isolate` resolve-axi verb (rung 3a). The
  per-clip variant stays out: the probe ranks the track call first.
- `rewrite_clip_media_to_stem`: the OTIO half of a DeepFilterNet stem -
  the clip's media reference becomes the stem file, which IS the played
  range, so the source start resets to zero. Pure function, unit-tested.

A cleanup is PLAN-REQUESTED or it does not happen. There is no engine
default, no threshold that turns it on, and no fallback tool: an entry
without `why` is dropped like every other undecided creative value,
and a tool nothing here names refuses through the post-bridge retry
path so the model re-plans instead of the mix carrying a key nobody
reads.
```
