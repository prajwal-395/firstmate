#!/usr/bin/env python3
"""
Step 2.06: Music Analysis

Per-project step that analyzes the selected music track to produce:
  - Beat grid (beat timestamps, bar boundaries)
  - BPM (tempo)
  - Key detection
  - Song structure (intro, verse, chorus, etc.)

Delegates to library/tools/analysis/music_pipeline.py for the actual
analysis, wrapping it in the step interface (JSON stdin → JSON stdout).

Consumed by step 4.04 (plan_sfx), which snaps SFX to bar boundaries
(post_bridge.py:483, `beat_grid.downbeat_positions`), and by step 4.02
(plan_transitions), whose bridge (bridge.py:27-33) and post-bridge
(post_bridge.py:212-219) both read the real grid through
`library/tools/beat_grid.py`. Step 2.05 (mesh_spine) declares the whole
analysis in context minus the raw lists. `structure` and `key` are
measured and carried, and no step reads them in code yet. `section_grid`
(bar-aligned functional labels from allin1) IS read: the `sectiongrid`
context view carries it to mesh_spine and the cut planners, and the
`section` anchor form in `library/tools/sub_block_anchor.py` resolves
it to exact frames at post-bridge time.

Classification: Deterministic / Data Transformation
Idempotent: Yes (same track → same analysis)

The work is `analyse_music`, which takes its inputs as arguments and
returns the payload.  `main()` owns the process: stdin and stdout.
Every branch here is a SUCCESS - an unavailable analysis is a reported
fact, not a failure, because SFX placement works without a beat grid -
so the function returns on all of them and never exits.  See AGENTS.md 3.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.tools.project_layout import Area, ProjectLayout
from library.tools import code_identity


def _method_hash() -> str | None:
    """The identity of the analysis method behind the cached grid.

    The step directory (this file, the manifest) plus the declared
    measurement implementation (`music_pipeline.py`, via
    `STEP_IMPLEMENTATION_DEPS`) - the same rule the ledger applies to
    preflight steps, applied here because this edit-stage step's cache
    outlives its method otherwise (finding 8, execution-frontier
    report 2026-09-24: an August librosa grid reused after beat_this
    landed). None when there is nothing to hash: it matches nothing.
    """
    return code_identity.current_code_hash(
        os.path.dirname(os.path.abspath(__file__)))


def _audio_content_digest(track_path: str) -> str:
    """The exact bytes behind a reusable music-analysis result.

    Empty means the file could not be fingerprinted and must never be a
    cache hit. Streaming keeps this safe for long tracks without loading
    one into memory.
    """
    digest = hashlib.sha256()
    try:
        with open(track_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def analyse_music(music_selection: dict, project_folder: str = "") -> dict:
    """Analyse the selected track for beat grid, BPM, key and structure.

    Returns `{"music_analysis": {...}}`.  `available: False` with a
    reason is a normal return: music analysis is optional and the run
    continues without it.
    """
    # Extract track path from music selection
    track_path = music_selection.get("audio_path", "")

    if not track_path or not os.path.exists(track_path):
        print(f"ERROR: Music track not found: {track_path}", file=sys.stderr)
        # Return empty analysis rather than failing — music analysis
        # is optional (SFX placement works without beat grid)
        return {
            "music_analysis": {
                "available": False,
                "error": f"Track not found: {track_path}"
            }
        }

    # Output directory for analysis results.
    # See library/tools/project_layout.py.
    output_dir = str(ProjectLayout(project_folder).write_dir(
        Area.MUSIC_ANALYSIS, step="music_analysis"))

    # Path to the music pipeline tool (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    MUSIC_PIPELINE = os.path.join(
        PILOT_ROOT, "library", "tools", "analysis", "music_pipeline.py")

    if not os.path.exists(MUSIC_PIPELINE):
        print(f"ERROR: Music pipeline not found at {MUSIC_PIPELINE}",
              file=sys.stderr)
        return {
            "music_analysis": {
                "available": False,
                "error": f"music_pipeline.py not found"
            }
        }

    # Check if analysis already exists for this track
    analysis_path = os.path.join(output_dir, "music_analysis.json")
    method_hash = _method_hash()
    audio_digest = _audio_content_digest(track_path)
    if os.path.exists(analysis_path):
        try:
            with open(analysis_path) as f:
                existing = json.load(f)
            # Verify it's for the same track (music_pipeline.py emits "file")
            # AND was written by the current method. A grid from an older
            # method is not a hit even for the same file: the beat grid,
            # downbeat source and section labels are what changed under
            # it, and reusing them reports success while the new work
            # did not happen. A cache from before hashes were stamped
            # carries no "method_hash" and reads as unknown, never as a
            # match - it is re-analyzed once, then stamped.
            if (existing.get("file") == track_path and method_hash
                    and existing.get("method_hash") == method_hash
                    and audio_digest
                    and existing.get("audio_content_digest") == audio_digest):
                print(f"Music analysis already exists for {os.path.basename(track_path)}, "
                      f"reusing cached result", file=sys.stderr)
                existing["available"] = True
                return {"music_analysis": existing}
            elif existing.get("file") == track_path:
                method_matches = (method_hash and
                                  existing.get("method_hash") == method_hash)
                if method_matches:
                    reason = "audio contents changed or cannot be fingerprinted"
                else:
                    reason = "was written by an older method"
                print(f"Music analysis for {os.path.basename(track_path)} "
                      f"{reason} - re-analyzing", file=sys.stderr)
            else:
                print(f"Music analysis cache is for a different track - "
                      f"re-analyzing {os.path.basename(track_path)}",
                      file=sys.stderr)
        except (json.JSONDecodeError, IOError):
            print(f"Music analysis cache for {os.path.basename(track_path)} "
                  f"is unreadable - re-analyzing", file=sys.stderr)

        # The pipeline writes this same path. Remove any cache that was
        # not a verified hit before launching it: if analysis fails or
        # exits without writing, reading the old file afterward would
        # silently stamp a stale grid with the current method hash.
        # Finding 8, execution-frontier report 2026-09-24: cache reuse
        # after the analysis method changed.
        try:
            os.remove(analysis_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return {
                "music_analysis": {
                    "available": False,
                    "error": ("Cannot invalidate stale music analysis "
                              f"cache: {exc}"),
                }
            }

    print(f"Analyzing music track: {os.path.basename(track_path)}",
          file=sys.stderr)

    try:
        result = subprocess.run(
            [sys.executable, MUSIC_PIPELINE, track_path,
             "--output-dir", output_dir],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            # allin1's section grid separates stems on CPU (~85 s for a
            # 200 s track) on top of the beat/key/structure passes.
            timeout=600,
        )

        if result.returncode != 0:
            # Report the exception, not the first 200 characters of a
            # traceback. Truncating from the top yielded
            # "Traceback (most recent call last):\n  File ..." and hid the
            # actual cause (a missing dependency) for an entire run.
            stderr = result.stderr.strip()
            cause = stderr.splitlines()[-1] if stderr else "no stderr"
            print(f"Music pipeline failed: {cause}", file=sys.stderr)
            print(stderr[-2000:], file=sys.stderr)
            return {
                "music_analysis": {
                    "available": False,
                    "error": cause,
                    "traceback_tail": stderr[-2000:],
                }
            }

        # Load the generated analysis
        if os.path.exists(analysis_path):
            with open(analysis_path) as f:
                analysis = json.load(f)
            analysis["available"] = True
            # Stamp the method that wrote this grid, so the cache check
            # above can tell it from an older method's output. Written
            # back to the file: a stamp kept only in memory would
            # re-analyze on every run.
            if method_hash:
                analysis["method_hash"] = method_hash
            # Stamp only when the bytes stayed stable across analysis. If
            # the source changed while the subprocess was reading it, the
            # result is useful for this run but cannot be reused later.
            if audio_digest and \
                    _audio_content_digest(track_path) == audio_digest:
                analysis["audio_content_digest"] = audio_digest
            else:
                analysis.pop("audio_content_digest", None)
            if method_hash or "audio_content_digest" in analysis:
                try:
                    with open(analysis_path, "w") as f:
                        json.dump(analysis, f, indent=2)
                except OSError:
                    pass
            # BPM lives under `tempo`, not at the top level - this line
            # read analysis['bpm'] and printed "BPM=?" on every run.
            _tempo = analysis.get("tempo") or {}
            _key = analysis.get("key") or {}
            print(f"Music analysis complete: "
                  f"BPM={_tempo.get('bpm', '?')}, "
                  f"beats={len(_tempo.get('beats') or [])}, "
                  f"downbeats={len(_tempo.get('downbeats') or [])}, "
                  f"Key={_key.get('key', '?')}",
                  file=sys.stderr)
            return {"music_analysis": analysis}
        else:
            # Pipeline ran but didn't produce expected output
            return {
                "music_analysis": {
                    "available": False,
                    "error": "Pipeline completed but music_analysis.json not found"
                }
            }

    except subprocess.TimeoutExpired:
        print("Music analysis timed out after 5 minutes", file=sys.stderr)
        return {
            "music_analysis": {
                "available": False,
                "error": "Analysis timed out"
            }
        }


def main():
    data = json.loads(sys.stdin.read())
    result = analyse_music(
        music_selection=data.get("music_selection", {}),
        project_folder=data.get("project_folder", ""),
    )
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
