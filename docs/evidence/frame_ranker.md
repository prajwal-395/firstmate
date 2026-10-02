# The LAION aesthetic frame ranker

Tests: `tests/unit/picture/test_picture_quality.py`.

FIRSTMATE VERDICT 2026-09-18 over the spike's "do not adopt": pairs 1 and
3 show a clear win in the same direction, and what the ranker replaces is
a systematically bad rule (about one second into a clip, reliably
mid-movement) rather than a good one.  The spike's measurements all stand
- these tests reuse its own numbers, never re-derived ones:

- blur-blindness: Laplacian variance 65 outscored a sharp pipeline pick
  4.55 to 4.09 - so composition alone must never choose a frame;
- hold-point noise: +0.04 over the current hold, preferring the softer
  frame - so the ranker stays out of the ending-freeze path entirely.

No test here runs the real head (1.6 GB, CPU-heavy - the captain's
standing rule pauses exactly that work): scorers, extraction and
sharpness are all stubs.  Nothing here touches ffmpeg, renders,
transcription or a real project - every path builds under `tmp_path`.
