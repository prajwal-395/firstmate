# MU-20 overlapped speech: acceptance measurement (2026-10-12)

Plan item MU-20: flag windows where two voices sound at once, from two
ECAPA clusters both scoring high or from per-ISO energies voiced at
once; acceptance is recall and precision against overlaps built from
the captain's ISO tracks (the entities report section 5 method).

Verdict first: **the per-camera energy arm ships, with precision 0.81
and recall 0.79-0.83 on the captain's synced ISO pair. The single-track
ECAPA arm does not ship: its best held-out balance is precision 0.42 /
recall 0.36.** A single-track transcript records why it carries no
measurement (`overlapped_speech.SINGLE_TRACK_REASON`).

## The finding that shaped the producer

The per-speaker tracks `timeline_transcript` rebuilds are NOT ISO
tracks on a cut timeline. Each holds its speaker's camera only while
that camera's picture plays. On geo-podcast the two rebuilt tracks are
never voiced at the same frame (Akshita-only frames: Craig's track at
-200 dBFS, and the reverse). Measured on those tracks, crosstalk is
unmeasurable by construction.

The cameras kept recording, though, and the M6 conversation clock
already says how they line up (LC4932 to LCATL0013: -3.8 s, 99.6% of
7471 n-gram matches agree). So `overlapped_speech.timeline_tracks` lays
each speaker's camera under every slot of the timeline:

- their own clips play as cut;
- under the other speaker's clip, the same instant plays from their own
  camera, mapped through M6 and read from the source's
  `program.16k.wav` in source memory.

On the captain's `GEO Podcast - Synced` cut this fills 2675 s
(Akshita) and 2290 s (Craig) of the 2657 s timeline. The two tracks
record together for 2242 s. The measurement takes 0.7 s once the
program audio is cached.

## The real pair

On the M6 reference clock, labelled by the timeline transcript's word
timings, frame dBFS reads (p25 / p50 / p75):

| Who talks (transcript) | Akshita's camera | Craig's camera | lead A-C |
|---|---|---|---|
| Akshita alone | -35.1 / -28.1 / -24.4 | -55.8 / -45.4 / -40.2 | 12.5 / 15.0 / 19.2 |
| Craig alone | -41.4 / -37.0 / -32.5 | -37.1 / -31.7 / -26.5 | -7.5 / -5.2 / -2.4 |

Akshita's camera hears Craig only ~5 dB below his own camera. Bleed is
real and asymmetric, so "both tracks loud" cannot be the rule. The
rule is the module docstring's: a pair's lead is bimodal, and the Otsu
split finds the two modes in the audio itself (-6.7 / +14.8 dB here).
A frame is a track's own voice when it clears the other talker's mode
by `ISO_MARGIN_DB`. Crosstalk is both tracks owning at least
`ISO_WINDOW_FRACTION` of an `ISO_WINDOW_S` window, in runs of at least
`MIN_OVERLAP_S`.

## Method (acceptance)

`docs/benchmarks/mu20_overlap_measure.py` follows the entities report
section 5 method: mix ISO tracks, and take the reference from the
per-ISO word timings. The overlap is constructed the way its E3 run
built one, scaled up and kept physical. Sixty spans where Craig talks
alone (0.6-3.0 s, no Akshita word within 1 s) are copied from BOTH
cameras, so his voice and its real bleed onto Akshita's camera travel
together. Each copy is added where Akshita talks alone.

- **Reference:** the shifted Craig words AND Akshita's words, plus the
  transcript's natural overlap (4.87 s).
- **Scoring:** inside the labelled region (within 1 s of any word,
  2408 s), using the module's own `score`. The NIST 0.25 s collar
  matches the section 5 scorer. A no-collar row is also given.
- **Thresholds:** picked on seed 21 only, by the best min(P, R) over the
  grid. The grid was margin {3, 4.5, 6, 7.5, 9} dB x window {0.3, 0.5,
  0.8, 1.0} s x fraction {0.1, 0.2, 0.3, 0.4} x minimum run {0.2, 0.4} s.
  Winner: 7.5 dB, 0.5 s, 0.1, 0.4 s. Seed 22 was not looked at while
  choosing.

| Corpus | Reference s | Precision | Recall | False alarm s | Precision (no collar) | Recall (no collar) |
|---|---|---|---|---|---|---|
| seed 21 (calibration) | 88.07 | 0.8114 | 0.7934 | 7.07 | 0.7225 | 0.7861 |
| seed 22 (held out) | 94.88 | 0.8168 | 0.8315 | 7.16 | 0.7274 | 0.8165 |
| unmodified episode | 4.87 | 0.0 | 0.0 | 8.10 | 0.0 | 0.0 |

Commands, the branch at the head of this PR:

    bin/vep docs/benchmarks/mu20_overlap_measure.py --out <scratch>/acc_21 --seed 21
    bin/vep docs/benchmarks/mu20_overlap_measure.py --out <scratch>/acc_22 --seed 22
    bin/vep docs/benchmarks/mu20_overlap_measure.py --out <scratch>/acc_0 --seed 0 --count 0

On main all three stop at `ImportError: cannot import name
'overlapped_speech'`, and so does `tests/unit/audio/test_overlapped_speech.py`.

**The unmodified episode's 8.1 s, stated rather than explained away.**
These are 38 runs of about 0.5 s, all inside Craig's transcribed turns,
with no Akshita word within a second. They may be untranscribed
backchannels ("mm-hmm" was the eval pair's dominant miss in the
diarization study), or Craig's own peaks crossing the margin. Nobody
listened, so they are counted as false alarms in every row above. The
transcript's own 4.87 s of natural "overlap" is not detected. It is the
same words on both speakers' rows: the one unresolved mic-bleed
candidate at timeline 1730.5-1735.6 s, and a two-word "It's like" at
1667.1 s. That is one voice transcribed twice, not crosstalk.

## The single-track ECAPA arm (measured, not shipped)

Corpora: Craig-alone spans added into Akshita-alone moments on the
rebuilt per-speaker tracks, then mixed to mono (60 inserts, ~93 s of
reference, two seeds). The shipped `diarize_track` ran on each mix at
k = 2 on MPS in about 24 s.

- **Second-best centroid cosine (the plan's "both score high"):** it
  never rises far. Overlap windows read p50 0.075 / p90 0.16, against
  0.04 / 0.10 elsewhere (AUC 0.68-0.70). Its best frame-level operating
  point is precision 0.27 / recall 0.05.
- **Top-two margin:** better ranked (AUC 0.84-0.87), but with a 4% base
  rate no threshold is usable. At the threshold that balances the
  calibration seed (0.4), the held-out seed reads precision 0.42 /
  recall 0.36.

| Margin below | seed 11 P / R | seed 12 P / R |
|---|---|---|
| 0.2 | 0.52 / 0.09 | 0.53 / 0.11 |
| 0.3 | 0.42 / 0.16 | 0.50 / 0.21 |
| 0.4 | 0.34 / 0.27 | 0.42 / 0.36 |
| 0.5 | 0.24 / 0.47 | 0.28 / 0.61 |

Both readers act on a span: captions keep a pair they would otherwise
drop, and reel boundaries move. At these numbers more than half of what
the arm says would be wrong, so it says nothing.

## Limits

- One shoot, one camera pair. LC4931/LCATL0012 has an M6 clock but too
  few transcribed turns to build a corpus.
- The construction places a whole Craig span over Akshita's speech.
  Natural crosstalk is usually shorter (backchannels, turn-grabs), and
  the 0.4 s minimum run is a floor on what is reported.
- Program audio is each camera's loudest live channel (source memory
  M0), not a lav. A shoot with true lavs should only separate better.
