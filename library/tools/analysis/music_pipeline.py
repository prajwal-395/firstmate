#!/usr/bin/env python3
"""
Music Analysis Pipeline — Deep Audio Feature Extraction

Extracts comprehensive music features for video editing:
- BPM/tempo tracking (madmom RNN+DBN or librosa fallback)
- Beat grid with downbeat detection
- Musical key detection (essentia KeyExtractor)
- Song structure segmentation (energy-based)
- Energy builds/drops detection
- Stem separation (Demucs, optional)

Input:  { "music_file": "/path/to/track.wav", "output_dir": "/path/to/output" }
Output: Comprehensive music_analysis.json

Runs entirely on-device (CPU + optional MPS for Demucs).
"""
import json
import os
import sys
import time
import numpy as np
from pathlib import Path


def analyze_tempo_beats(audio_path: str) -> dict:
    """Extract BPM and beat grid.
    
    Tries madmom (RNN+DBN, ±0.5 BPM accuracy) first,
    falls back to librosa (±2-5 BPM, octave errors possible).
    """
    result = {"method": None, "bpm": None, "beats": [], "downbeats": []}
    
    # Try madmom first (superior accuracy)
    try:
        from madmom.features.beats import RNNBeatProcessor, DBNBeatTrackingProcessor
        from madmom.features.downbeats import (
            RNNDownBeatProcessor, DBNDownBeatTrackingProcessor
        )
        
        print("  Using madmom RNN+DBN for beat tracking...", file=sys.stderr)
        
        # Beat detection
        beat_proc = RNNBeatProcessor()
        beat_act = beat_proc(audio_path)
        beat_tracker = DBNBeatTrackingProcessor(fps=100, transition_lambda=100)
        beats = beat_tracker(beat_act)
        
        # Downbeat detection
        try:
            db_proc = RNNDownBeatProcessor()
            db_act = db_proc(audio_path)
            db_tracker = DBNDownBeatTrackingProcessor(
                beats_per_bar=[3, 4], fps=100
            )
            downbeat_result = db_tracker(db_act)
            # downbeat_result is (time, beat_position) pairs
            downbeats = [
                round(float(t), 3) 
                for t, pos in downbeat_result if int(pos) == 1
            ]
        except Exception as e:
            print(f"  WARNING: downbeat detection failed: {e}", file=sys.stderr)
            # Estimate downbeats from beats (every 4th beat)
            downbeats = [round(float(beats[i]), 3) for i in range(0, len(beats), 4)]
        
        # Calculate BPM from beat intervals
        if len(beats) >= 2:
            intervals = np.diff(beats)
            bpm = round(float(60.0 / np.median(intervals)), 1)
        else:
            bpm = None
        
        # Instantaneous tempo curve
        tempo_curve = []
        for i in range(1, len(beats)):
            interval = beats[i] - beats[i-1]
            inst_bpm = round(float(60.0 / interval), 1)
            tempo_curve.append({
                "time": round(float(beats[i-1]), 3),
                "bpm": inst_bpm
            })
        
        result = {
            "method": "madmom-rnn-dbn",
            "bpm": bpm,
            "beats": [round(float(b), 3) for b in beats],
            "downbeats": downbeats,
            "tempo_curve": tempo_curve,
            "beat_count": len(beats),
            "tempo_stable": bool(
                np.std([t["bpm"] for t in tempo_curve]) < 3.0
            ) if tempo_curve else True,
        }
        print(f"  madmom: {bpm} BPM, {len(beats)} beats, {len(downbeats)} downbeats",
              file=sys.stderr)
        return result
        
    except ImportError:
        print("  madmom not available, falling back to librosa...", file=sys.stderr)
    except Exception as e:
        print(f"  madmom failed: {e}, falling back to librosa...", file=sys.stderr)
    
    # Fallback: librosa
    try:
        import librosa
        
        y, sr = librosa.load(audio_path)
        
        # Tempo and beats
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units='time')
        # Handle both scalar and array tempo returns
        if hasattr(tempo, '__len__'):
            bpm = round(float(tempo[0]), 1)
        else:
            bpm = round(float(tempo), 1)
        
        # Estimate downbeats (every 4th beat)
        downbeats = [round(float(beats[i]), 3) for i in range(0, len(beats), 4)]
        
        result = {
            "method": "librosa-beat-track",
            "bpm": bpm,
            "beats": [round(float(b), 3) for b in beats],
            "downbeats": downbeats,
            "tempo_curve": [],
            "beat_count": len(beats),
            "tempo_stable": True,
            "warning": "librosa has ±2-5 BPM accuracy and frequent octave errors"
        }
        print(f"  librosa: {bpm} BPM, {len(beats)} beats (⚠ less accurate)", 
              file=sys.stderr)
        return result
        
    except Exception as e:
        print(f"  ERROR: beat tracking failed entirely: {e}", file=sys.stderr)
        return result


def analyze_key(audio_path: str) -> dict:
    """Detect musical key using essentia KeyExtractor.
    
    Returns key, scale, and confidence. ~70-80% accuracy.
    """
    try:
        import essentia.standard as es
        
        print("  Using essentia KeyExtractor...", file=sys.stderr)
        
        # Load audio
        loader = es.MonoLoader(filename=audio_path)
        audio = loader()
        
        # Global key detection
        key_extractor = es.KeyExtractor()
        key, scale, strength = key_extractor(audio)
        
        # Also try windowed detection for key changes
        key_changes = []
        window_size = 5  # seconds
        hop_size = 2.5  # seconds
        sr = 44100  # essentia default
        
        win_samples = int(window_size * sr)
        hop_samples = int(hop_size * sr)
        
        for i in range(0, len(audio) - win_samples, hop_samples):
            chunk = audio[i:i + win_samples]
            try:
                k, s, st = key_extractor(chunk)
                key_changes.append({
                    "time": round(i / sr, 2),
                    "key": k,
                    "scale": s,
                    "strength": round(float(st), 3)
                })
            except Exception:
                pass
        
        # Check if key is consistent
        all_keys = [kc["key"] + " " + kc["scale"] for kc in key_changes]
        if all_keys:
            from collections import Counter
            most_common = Counter(all_keys).most_common(1)[0]
            consistency = round(most_common[1] / len(all_keys), 2)
            window_note = ""
        else:
            # No windowed estimate survived (or the track is shorter
            # than one window): 1.0 would report a perfect stability
            # nothing measured (AGENTS.md 10.3 - a default presented
            # as a measurement).
            consistency = None
            window_note = "no windowed key estimates survived"

        result = {
            "method": "essentia-key-extractor",
            "key": key,
            "scale": scale,
            "strength": round(float(strength), 3),
            "key_label": f"{key} {scale}",
            "consistency": consistency,
            "key_changes": key_changes[:20],  # Limit for JSON size
            "note": window_note,
        }
        consistency_display = (f"{consistency:.0%}" if consistency is not None
                               else "unmeasured")
        print(f"  essentia: {key} {scale} (strength: {strength:.2f}, consistency: {consistency_display})",
              file=sys.stderr)
        return result
        
    except ImportError:
        print("  essentia not available, skipping key detection", file=sys.stderr)
        return {"method": None, "key": None, "scale": None,
                "note": "essentia not installed"}
    except Exception as e:
        print(f"  ERROR: key detection failed: {e}", file=sys.stderr)
        return {"method": None, "key": None, "scale": None, "error": str(e)}


def analyze_structure(audio_path: str) -> dict:
    """Segment music into structural sections using energy/spectral analysis.
    
    Uses librosa's self-similarity matrix + spectral clustering
    to identify intro, verse, chorus, bridge, outro.
    """
    try:
        import librosa
        from scipy.signal import find_peaks
        from scipy.ndimage import uniform_filter1d
        
        print("  Analyzing song structure...", file=sys.stderr)
        
        y, sr = librosa.load(audio_path)
        duration = librosa.get_duration(y=y, sr=sr)
        
        # Compute features for segmentation
        # 1. Chroma (harmonic content)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
        
        # 2. MFCC (timbral content)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=512)
        
        # 3. RMS energy
        rms = librosa.feature.rms(y=y, hop_length=512)[0]
        
        # 4. Spectral contrast
        contrast = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=512)
        
        # Combine features
        features = np.vstack([chroma, mfcc, contrast])
        
        # Self-similarity matrix (SSM)
        # Downsample to ~1Hz for reasonable computation
        hop_per_sec = sr // 512
        chunk_size = max(1, hop_per_sec)
        n_chunks = features.shape[1] // chunk_size
        
        if n_chunks < 4:
            # Too short for structural analysis
            return {
                "method": "energy-based",
                "sections": [{"type": "full", "start": 0.0, "end": round(duration, 2)}],
                "note": "Track too short for structural segmentation"
            }
        
        # Average features per second
        feat_avg = np.zeros((features.shape[0], n_chunks))
        for i in range(n_chunks):
            feat_avg[:, i] = features[:, i*chunk_size:(i+1)*chunk_size].mean(axis=1)
        
        # Normalize
        feat_norm = librosa.util.normalize(feat_avg, axis=0)
        
        # Self-similarity via cosine similarity
        ssm = np.dot(feat_norm.T, feat_norm)
        
        # Novelty curve: detect boundaries via checkerboard kernel on SSM
        kernel_size = min(8, n_chunks // 4)
        if kernel_size < 2:
            kernel_size = 2
        
        novelty = np.zeros(n_chunks)
        half_k = kernel_size // 2
        for i in range(half_k, n_chunks - half_k):
            # Checkerboard: compare top-left/bottom-right (same) vs 
            # top-right/bottom-left (different)
            tl = ssm[i-half_k:i, i-half_k:i].mean()
            br = ssm[i:i+half_k, i:i+half_k].mean()
            tr = ssm[i-half_k:i, i:i+half_k].mean()
            bl = ssm[i:i+half_k, i-half_k:i].mean()
            novelty[i] = max(0, (tl + br) / 2 - (tr + bl) / 2)
        
        # Smooth novelty
        novelty_smooth = uniform_filter1d(novelty, size=3)
        
        # Find peaks (section boundaries)
        min_section_duration = max(4, int(duration * 0.05))  # At least 5% of track
        peaks, properties = find_peaks(
            novelty_smooth, 
            prominence=np.percentile(novelty_smooth[novelty_smooth > 0], 50) if np.any(novelty_smooth > 0) else 0.1,
            distance=min_section_duration
        )
        
        # Build section boundaries
        boundaries = [0] + list(peaks) + [n_chunks]
        
        # Classify each section based on energy profile
        rms_per_sec = np.zeros(n_chunks)
        for i in range(n_chunks):
            seg = rms[i*chunk_size:(i+1)*chunk_size]
            rms_per_sec[i] = seg.mean() if len(seg) > 0 else 0
        
        rms_median = np.median(rms_per_sec)
        
        sections = []
        for i in range(len(boundaries) - 1):
            start_sec = boundaries[i]
            end_sec = boundaries[i + 1]
            
            section_energy = rms_per_sec[start_sec:end_sec].mean()
            section_var = rms_per_sec[start_sec:end_sec].var()
            
            # Heuristic classification
            if i == 0 and section_energy < rms_median * 0.7:
                section_type = "intro"
            elif i == len(boundaries) - 2 and section_energy < rms_median * 0.7:
                section_type = "outro"
            elif section_energy > rms_median * 1.3:
                section_type = "chorus"
            elif section_energy > rms_median * 0.8:
                section_type = "verse"
            elif section_var > np.median(rms_per_sec) * 0.5:
                section_type = "build"
            else:
                section_type = "bridge"
            
            sections.append({
                "type": section_type,
                "start": round(float(start_sec), 2),
                "end": round(float(end_sec), 2),
                "duration": round(float(end_sec - start_sec), 2),
                "energy": round(float(section_energy), 4),
                "relative_energy": round(float(section_energy / rms_median), 2) if rms_median > 0 else 0,
            })
        
        result = {
            "method": "ssm-novelty-curve",
            "sections": sections,
            "section_count": len(sections),
            "novelty_peaks": [round(float(p), 2) for p in peaks],
        }
        print(f"  structure: {len(sections)} sections detected", file=sys.stderr)
        return result
        
    except Exception as e:
        print(f"  ERROR: structure analysis failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {"method": None, "sections": [], "error": str(e)}


def analyze_energy_dynamics(audio_path: str) -> dict:
    """Detect energy builds, drops, and dynamics.
    
    Uses onset strength envelope gradient to find tension builds
    and energy drops — crucial for video editing pacing.
    """
    try:
        import librosa
        from scipy.signal import find_peaks
        from scipy.ndimage import uniform_filter1d
        
        print("  Analyzing energy dynamics...", file=sys.stderr)
        
        y, sr = librosa.load(audio_path)
        duration = librosa.get_duration(y=y, sr=sr)
        
        # Onset strength envelope
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        
        # Smooth envelope (2-second window)
        fps = sr / 512  # default hop_length
        window = max(1, int(fps * 2))
        smoothed = uniform_filter1d(onset_env.astype(float), size=window)
        
        # Gradient (rate of energy change)
        gradient = np.gradient(smoothed)
        gradient_smooth = uniform_filter1d(gradient, size=window)
        
        # Energy at 1Hz for the output curve
        hop_per_sec = max(1, int(fps))
        n_secs = len(smoothed) // hop_per_sec
        energy_1hz = []
        for i in range(n_secs):
            seg = smoothed[i*hop_per_sec:(i+1)*hop_per_sec]
            energy_1hz.append(round(float(seg.mean()), 4))
        
        # Normalize energy curve to 0-1
        e_max = max(energy_1hz) if energy_1hz else 1
        if e_max > 0:
            energy_norm = [round(e / e_max, 3) for e in energy_1hz]
        else:
            energy_norm = energy_1hz
        
        # Detect builds (sustained positive gradient)
        builds = []
        in_build = False
        build_start = 0
        for i in range(len(gradient_smooth)):
            t = i / fps
            if gradient_smooth[i] > np.percentile(gradient_smooth, 75):
                if not in_build:
                    in_build = True
                    build_start = t
            else:
                if in_build:
                    build_duration = t - build_start
                    if build_duration > 1.0:  # Min 1s build
                        builds.append({
                            "start": round(build_start, 2),
                            "end": round(t, 2),
                            "duration": round(build_duration, 2),
                            "intensity": round(float(
                                smoothed[int(build_start*fps):int(t*fps)].max() -
                                smoothed[int(build_start*fps):int(t*fps)].min()
                            ), 3)
                        })
                    in_build = False
        
        # Detect drops (sharp negative gradient)
        drop_threshold = np.percentile(gradient_smooth, 10)
        drops = []
        drop_peaks, _ = find_peaks(-gradient_smooth, prominence=abs(drop_threshold) * 0.5)
        for peak in drop_peaks:
            t = peak / fps
            drops.append({
                "time": round(t, 2),
                "magnitude": round(float(-gradient_smooth[peak]), 3)
            })
        
        # Overall dynamics
        dynamics = {
            "dynamic_range_db": round(float(
                20 * np.log10(max(energy_1hz) / (min(e for e in energy_1hz if e > 0) + 1e-10))
            ), 1) if energy_1hz and max(energy_1hz) > 0 else 0,
            "energy_arc": (
                "building" if energy_norm and energy_norm[-1] > energy_norm[0] + 0.2
                else "declining" if energy_norm and energy_norm[-1] < energy_norm[0] - 0.2
                else "crescendo-decrescendo" if energy_norm and max(energy_norm[len(energy_norm)//3:2*len(energy_norm)//3]) > max(energy_norm[0], energy_norm[-1]) + 0.2
                else "steady"
            ),
        }
        
        result = {
            "energy_curve_1hz": energy_norm,
            "builds": builds[:10],
            "drops": drops[:10],
            "dynamics": dynamics,
            "build_count": len(builds),
            "drop_count": len(drops),
        }
        print(f"  dynamics: {len(builds)} builds, {len(drops)} drops, arc={dynamics['energy_arc']}",
              file=sys.stderr)
        return result
        
    except Exception as e:
        print(f"  ERROR: energy dynamics failed: {e}", file=sys.stderr)
        return {"energy_curve_1hz": [], "builds": [], "drops": [], "error": str(e)}


def analyze_chord_progression(audio_path: str) -> dict:
    """Extract chord progression using essentia.
    
    Uses HPCP (Harmonic Pitch Class Profile) for chord estimation.
    """
    try:
        import essentia.standard as es
        
        print("  Analyzing chord progression...", file=sys.stderr)
        
        loader = es.MonoLoader(filename=audio_path, sampleRate=44100)
        audio = loader()
        
        # Windowed chord detection
        window_size = 2.0  # seconds
        hop_size = 0.5     # seconds
        sr = 44100
        
        win_samples = int(window_size * sr)
        hop_samples = int(hop_size * sr)
        
        chords = []
        chord_map = {}  # Track chord frequencies
        
        for i in range(0, len(audio) - win_samples, hop_samples):
            chunk = audio[i:i + win_samples]
            t = i / sr
            
            try:
                # Use ChordsDetection
                key_ext = es.KeyExtractor()
                key, scale, strength = key_ext(chunk)
                
                chord_label = f"{key}:{scale[:3]}"
                chords.append({
                    "time": round(t, 2),
                    "chord": chord_label,
                    "strength": round(float(strength), 3)
                })
                
                chord_map[chord_label] = chord_map.get(chord_label, 0) + 1
            except Exception:
                pass
        
        # Deduplicate consecutive same chords
        if chords:
            deduped = [chords[0]]
            for c in chords[1:]:
                if c["chord"] != deduped[-1]["chord"]:
                    deduped.append(c)
            chords = deduped
            chord_count: int | None = len(chords)
            chord_note = ""
        else:
            # No windowed estimate survived (or the track is shorter
            # than one window): 0 would report "no chord changes" as
            # a measured fact nothing measured (AGENTS.md 10.3).
            chord_count = None
            chord_note = "no windowed chord estimates survived"

        # Sort by frequency
        chord_freq = sorted(chord_map.items(), key=lambda x: -x[1])

        result = {
            "method": "essentia-key-windowed",
            "chord_progression": chords[:50],  # Limit for size
            "chord_count": chord_count,
            "most_common_chords": [
                {"chord": c, "occurrences": n} for c, n in chord_freq[:8]
            ],
            "note": chord_note,
        }
        print(f"  chords: {len(chords)} changes, most common: {chord_freq[0][0] if chord_freq else 'none'}",
              file=sys.stderr)
        return result
        
    except ImportError:
        print("  essentia not available for chord analysis", file=sys.stderr)
        return {"method": None, "chord_progression": [],
                "note": "essentia not installed"}
    except Exception as e:
        print(f"  ERROR: chord analysis failed: {e}", file=sys.stderr)
        return {"method": None, "chord_progression": [], "error": str(e)}


def separate_stems(audio_path: str, output_dir: str) -> dict:
    """Separate audio into stems using Demucs (Meta).
    
    Produces: vocals, drums, bass, other (accompaniment).
    Runs on CPU or MPS.
    """
    try:
        import demucs.separate
        import demucs.api
        
        print("  Separating stems with Demucs...", file=sys.stderr)
        
        stems_dir = os.path.join(output_dir, "stems")
        os.makedirs(stems_dir, exist_ok=True)
        
        # Use the API for better control
        separator = demucs.api.Separator(model="htdemucs")
        
        # Load and separate
        origin, separated = separator.separate_audio_file(audio_path)
        
        stem_files = {}
        for stem_name, stem_audio in separated.items():
            stem_path = os.path.join(stems_dir, f"{stem_name}.wav")
            # Save stem
            import torchaudio
            torchaudio.save(stem_path, stem_audio.cpu(), separator.samplerate)
            stem_files[stem_name] = stem_path
            print(f"    Saved {stem_name} → {stem_path}", file=sys.stderr)
        
        result = {
            "method": "demucs-htdemucs",
            "stems": stem_files,
            "stem_names": list(stem_files.keys()),
        }
        print(f"  Demucs: separated into {len(stem_files)} stems", file=sys.stderr)
        return result
        
    except ImportError:
        print("  demucs not available, skipping stem separation", file=sys.stderr)
        return {"method": None, "stems": {}, "note": "demucs not installed"}
    except Exception as e:
        print(f"  ERROR: stem separation failed: {e}", file=sys.stderr)
        return {"method": None, "stems": {}, "error": str(e)}


def analyze_music(music_file: str, output_dir: str = None, skip_stems: bool = False) -> dict:
    """Run the complete music analysis pipeline.
    
    Args:
        music_file: Path to music audio file
        output_dir: Where to save stems and analysis JSON
        skip_stems: Skip Demucs stem separation (saves time)
    
    Returns:
        Complete music analysis dict
    """
    import librosa
    
    if output_dir is None:
        output_dir = os.path.dirname(music_file)
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Music Analysis: {os.path.basename(music_file)}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    
    start_time = time.time()
    
    # Basic info
    y, sr = librosa.load(music_file)
    duration = librosa.get_duration(y=y, sr=sr)
    print(f"  Duration: {duration:.1f}s, Sample rate: {sr}Hz", file=sys.stderr)
    
    # Run all analyses
    tempo = analyze_tempo_beats(music_file)
    key = analyze_key(music_file)
    structure = analyze_structure(music_file)
    dynamics = analyze_energy_dynamics(music_file)
    chords = analyze_chord_progression(music_file)
    
    stems = {}
    if not skip_stems:
        stems = separate_stems(music_file, output_dir)
    else:
        stems = {"method": None, "stems": {}, "note": "skipped"}
    
    elapsed = time.time() - start_time
    
    analysis = {
        "file": music_file,
        "filename": os.path.basename(music_file),
        "duration_s": round(duration, 2),
        "sample_rate": sr,
        "tempo": tempo,
        "key": key,
        "structure": structure,
        "energy_dynamics": dynamics,
        "chords": chords,
        "stems": stems,
        "analysis_time_s": round(elapsed, 1),
        "analysis_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    
    # Save to file
    output_path = os.path.join(output_dir, "music_analysis.json")
    with open(output_path, "w") as f:
        json.dump(analysis, f, indent=2)
    
    print(f"\n  ✓ Analysis complete in {elapsed:.1f}s", file=sys.stderr)
    print(f"  ✓ Saved to {output_path}", file=sys.stderr)
    
    return analysis


def main():
    """Entry point for stdin/stdout orchestrator interface."""
    if len(sys.argv) > 1:
        # CLI mode
        import argparse
        parser = argparse.ArgumentParser(description="Analyze music for video editing")
        parser.add_argument("music_file", help="Path to music file")
        parser.add_argument("--output-dir", help="Output directory")
        parser.add_argument("--skip-stems", action="store_true",
                          help="Skip Demucs stem separation")
        args = parser.parse_args()
        
        result = analyze_music(
            args.music_file,
            args.output_dir,
            args.skip_stems
        )
        json.dump(result, sys.stdout, indent=2)
    else:
        # Orchestrator mode: read from stdin
        data = json.loads(sys.stdin.read())
        music_file = data.get("music_file")
        output_dir = data.get("output_dir")
        skip_stems = data.get("skip_stems", False)
        
        if not music_file:
            print(json.dumps({"error": "Missing: music_file"}))
            sys.exit(1)
        
        result = analyze_music(music_file, output_dir, skip_stems)
        json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
