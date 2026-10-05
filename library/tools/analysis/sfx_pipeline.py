import os
import json
import argparse
import numpy as np
import librosa
import torch
import time
from pathlib import Path
from tqdm import tqdm
import glob

# HF token should be set via environment variable or .env file
if "HF_TOKEN" not in os.environ:
    print("WARNING: HF_TOKEN not set. Audio Flamingo Next requires a HuggingFace token.", file=__import__('sys').stderr)
    print("  Set it via: export HF_TOKEN=hf_your_token_here", file=__import__('sys').stderr)

def analyze_librosa(audio_path):
    try:
        y, sr = librosa.load(audio_path, sr=None)
        duration = librosa.get_duration(y=y, sr=sr)
        
        rms = librosa.feature.rms(y=y)[0]
        rms_mean = float(np.mean(rms))
        rms_max = float(np.max(rms))
        peak_amplitude = float(np.max(np.abs(y)))
        
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
        rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
        flatness = librosa.feature.spectral_flatness(y=y)[0]
        zcr = librosa.feature.zero_crossing_rate(y)[0]
        
        # Energy profile
        attack_frame = np.argmax(rms)
        attack_time_ms = float(librosa.frames_to_time(attack_frame, sr=sr) * 1000)
        
        # Find decay time (-20dB below peak)
        db_envelope = librosa.amplitude_to_db(rms, ref=np.max)
        decay_frames = np.where(db_envelope[attack_frame:] <= -20)[0]
        if len(decay_frames) > 0:
            decay_time_ms = float(librosa.frames_to_time(decay_frames[0], sr=sr) * 1000)
        else:
            decay_time_ms = float(duration * 1000 - attack_time_ms)
            
        peak_pos = attack_time_ms / (duration * 1000) if duration > 0 else 0
        if peak_pos < 0.15 and decay_time_ms < 500:
            envelope_shape = 'punchy'
        elif peak_pos > 0.3:
            envelope_shape = 'swelling'
        elif peak_pos < 0.15 and decay_time_ms > 1000:
            envelope_shape = 'sustained'
        else:
            envelope_shape = 'fading'
            
        is_tonal = float(np.mean(flatness)) < 0.1
        estimated_pitch_hz = None
        if is_tonal:
            f0, voiced_flag, voiced_probs = librosa.pyin(y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
            valid_f0 = f0[voiced_flag]
            if len(valid_f0) > 0:
                estimated_pitch_hz = float(np.nanmedian(valid_f0))
                
        onsets = librosa.onset.onset_detect(y=y, sr=sr)
        num_onsets = len(onsets)
        is_one_shot = num_onsets <= 2 and duration < 3.0
        
        return {
            "basic": {"duration": float(duration), "sample_rate": int(sr)},
            "loudness": {"rms_mean": rms_mean, "rms_max": rms_max, "peak_amplitude": peak_amplitude},
            "spectral": {
                "spectral_centroid_mean": float(np.mean(centroid)),
                "spectral_bandwidth_mean": float(np.mean(bandwidth)),
                "spectral_rolloff_mean": float(np.mean(rolloff)),
                "spectral_flatness_mean": float(np.mean(flatness)),
                "zero_crossing_rate_mean": float(np.mean(zcr))
            },
            "energy_profile": {
                "attack_time_ms": attack_time_ms,
                "decay_time_ms": decay_time_ms,
                "envelope_shape": envelope_shape
            },
            "tonal": {
                "is_tonal": is_tonal,
                "estimated_pitch_hz": estimated_pitch_hz
            },
            "temporal": {
                "num_onsets": num_onsets,
                "is_one_shot": is_one_shot
            }
        }
    except Exception as e:
        print(f"Error in librosa analysis for {audio_path}: {e}")
        return None

def analyze_afnext(audio_path, model, processor):
    try:
        y, sr = librosa.load(audio_path, sr=16000)
        target_length = 30 * 16000
        if len(y) < target_length:
            y = np.pad(y, (0, target_length - len(y)))
        else:
            y = y[:target_length]

        conversation = [{"role": "user", "content": [
            {"type": "audio", "audio": y},
            {"type": "text", "text": "Describe this sound effect in detail. What does it sound like, what could have made this sound, and what type of content would it be used in?"},
        ]}]

        text = processor.apply_chat_template(conversation, add_generation_prompt=True, tokenize=False)
        inputs = processor(text=text, audio=[y], sampling_rate=16000, return_tensors="pt", padding=True)

        # Cast float32 inputs to float16 to match model dtype
        for k, v in inputs.items():
            if hasattr(v, 'dtype') and v.dtype == torch.float32:
                inputs[k] = v.to("mps").half()
            elif hasattr(v, 'to'):
                inputs[k] = v.to("mps")

        with torch.no_grad():
            output_ids = model.generate(**inputs, max_new_tokens=200)

        response = processor.batch_decode(output_ids, skip_special_tokens=True)[0]
        if "assistant" in response.lower():
            response = response.split("assistant")[-1].strip()
        return response.strip()
    except Exception as e:
        print(f"Error in AF-Next analysis for {audio_path}: {e}")
        import traceback; traceback.print_exc()
        return ""

def load_afnext_model():
    from ren.edition import EditionError, require_component, select_component

    try:
        provider = select_component(
            "SFX profiling",
            personal_component="model.audio_flamingo_next",
            public_component="model.laion_clap")
    except EditionError as exc:
        raise RuntimeError(str(exc)) from exc
    require_component(provider, action="fetch or load")
    if provider == "model.laion_clap":
        return (*load_clap_model(), provider)
    if provider != "model.audio_flamingo_next":
        raise RuntimeError(
            f"SFX profiler {provider!r} has no model loader registered")
    from transformers import MusicFlamingoForConditionalGeneration, AutoProcessor, QuantoConfig
    import transformers.models.musicflamingo.modeling_musicflamingo as mf_module

    # Monkey-patch: MPS doesn't support float64, use float32 instead
    def _patched_apply_rotary(hidden_states, cos, sin):
        original_dtype = hidden_states.dtype
        hidden_states = hidden_states.to(torch.float32)
        cos = cos.to(hidden_states)
        sin = sin.to(hidden_states)
        rot_dim = cos.shape[-1]
        passthrough = hidden_states[..., rot_dim:]
        rotated = hidden_states[..., :rot_dim]
        rotated = (rotated * cos) + (mf_module.rotate_half(rotated) * sin)
        return torch.cat((rotated, passthrough), dim=-1).to(original_dtype)
    mf_module.apply_rotary_time_emb = _patched_apply_rotary
    print("Applied MPS float64->float32 patch")

    model_id = os.environ.get(
        "AF_NEXT_MODEL_PATH",
        os.path.join(str(Path.home()), ".cache", "huggingface", "af_next_local"),
    )
    processor_id = "nvidia/audio-flamingo-next-captioner-hf"
    quant_config = QuantoConfig(weights="int4")

    print("Loading AF-Next processor...")
    processor = AutoProcessor.from_pretrained(processor_id)

    print("Loading AF-Next model (Quanto int4)...")
    model = MusicFlamingoForConditionalGeneration.from_pretrained(
        model_id,
        quantization_config=quant_config,
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
        device_map="cpu",
    )
    model.eval()
    model = model.to("mps")
    print(f"Model on MPS! GPU memory: {torch.mps.current_allocated_memory() / 1024**3:.1f} GB")

    print("Skipping warmup (first file will be warmup).")

    return model, processor, provider


def load_clap_model():
    """Load LAION-CLAP for the public edition's SFX profiling."""
    import torch.nn.functional as F
    from transformers import AutoProcessor, ClapModel

    print("Loading LAION-CLAP model...")
    model = ClapModel.from_pretrained("laion/clap-htsat-unfused")
    model.eval()
    if torch.backends.mps.is_available():
        model = model.to("mps")
    processor = AutoProcessor.from_pretrained("laion/clap-htsat-unfused")
    print("LAION-CLAP loaded.")
    return model, processor


def analyze_clap(audio_path, model, processor):
    """Profile one SFX asset with CLAP; return a label-string description."""
    import torch
    import torch.nn.functional as F

    try:
        y, sr = librosa.load(audio_path, sr=48000)
        inputs = processor(audio=[y], sampling_rate=48000, return_tensors="pt", padding=True)
        device = next(model.parameters()).device
        input_values = inputs["input_features"].to(device)
        attention_mask = inputs.get("attention_mask")
        if attention_mask is not None:
            attention_mask = attention_mask.to(device)
        with torch.no_grad():
            audio_out = model.get_audio_features(input_values, attention_mask=attention_mask)
            audio_embeds = audio_out.pooler_output if hasattr(audio_out, "pooler_output") else audio_out
            audio_embeds = F.normalize(audio_embeds, dim=-1)

        labels = _clap_candidate_labels()
        text_inputs = processor(
            text=[f"This is a sound of {label}." for label in labels],
            return_tensors="pt", padding=True, truncation=True,
        )
        text_inputs = {k: v.to(device) for k, v in text_inputs.items()}
        with torch.no_grad():
            text_out = model.get_text_features(**text_inputs)
            text_embeds = text_out.pooler_output if hasattr(text_out, "pooler_output") else text_out
            text_embeds = F.normalize(text_embeds, dim=-1)
        sims = (audio_embeds @ text_embeds.T)[0]
        topk = torch.topk(sims, 5)
        tags = [labels[int(i)] for i in topk.indices]
        return ", ".join(tags)
    except Exception as e:
        print(f"Error in CLAP analysis for {audio_path}: {e}")
        import traceback; traceback.print_exc()
        return ""


_CLAP_LABELS = None


def _clap_candidate_labels():
    """AudioSet label set used as CLAP zero-shot candidates (lazy-loaded)."""
    global _CLAP_LABELS
    if _CLAP_LABELS is None:
        from transformers import AutoModelForAudioClassification
        ast = AutoModelForAudioClassification.from_pretrained(
            "MIT/ast-finetuned-audioset-10-10-0.4593")
        _CLAP_LABELS = [ast.config.id2label[i] for i in sorted(ast.config.id2label)]
    return _CLAP_LABELS

def build_index(profiles_dir):
    from sentence_transformers import SentenceTransformer
    import faiss
    
    print("Building index...")
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    profiles = []
    texts = []
    
    json_files = glob.glob(os.path.join(profiles_dir, "*.json"))
    for jf in json_files:
        if os.path.basename(jf) == "sfx_index.json": continue
        with open(jf, "r") as f:
            try:
                data = json.load(f)
                profiles.append(data)
                texts.append(data.get("description", ""))
            except Exception as e:
                print(f"Error loading {jf}: {e}")
            
    if not texts:
        print("No descriptions to index.")
        return
        
    embeddings = model.encode(texts, normalize_embeddings=True)
    dimension = embeddings.shape[1]
    
    index = faiss.IndexFlatIP(dimension)
    index.add(np.array(embeddings))
    
    faiss.write_index(index, os.path.join(profiles_dir, "sfx.faiss"))
    
    for i, prof in enumerate(profiles):
        prof["embedding"] = embeddings[i].tolist()
        
    with open(os.path.join(profiles_dir, "sfx_index.json"), "w") as f:
        json.dump(profiles, f, indent=2)
        
    print("Index built and saved.")

def search_index(query, profiles_dir):
    from sentence_transformers import SentenceTransformer
    import faiss
    
    index_path = os.path.join(profiles_dir, "sfx.faiss")
    profiles_path = os.path.join(profiles_dir, "sfx_index.json")
    
    if not os.path.exists(index_path) or not os.path.exists(profiles_path):
        print("Index not found.")
        return
        
    model = SentenceTransformer('all-MiniLM-L6-v2')
    index = faiss.read_index(index_path)
    with open(profiles_path, "r") as f:
        profiles = json.load(f)
        
    query_emb = model.encode([query], normalize_embeddings=True)
    D, I = index.search(np.array(query_emb), k=5)
    
    print(f"Search results for '{query}':")
    for i in range(len(I[0])):
        idx = I[0][i]
        if idx < len(profiles):
            print(f"{i+1}. {profiles[idx]['file']} (Score: {D[0][i]:.4f})")
            print(f"   {profiles[idx].get('description', '')[:100]}...")

def main():
    parser = argparse.ArgumentParser(description="SFX Analysis Pipeline")
    # The default is `paths.SFX_LIBRARY` - the one place that reads
    # PIPELINE_SFX_LIBRARY from the environment, the user config and .env.
    _repo_root = str(Path(__file__).resolve().parents[3])
    if _repo_root not in __import__('sys').path:
        __import__('sys').path.insert(0, _repo_root)
    from library.tools.paths import SFX_LIBRARY
    _sfx_default = str(SFX_LIBRARY)
    parser.add_argument("--sfx-dir", default=_sfx_default)
    parser.add_argument("--output-dir", default=os.path.join(_sfx_default, "profiles"))
    parser.add_argument("--file", help="Analyze a single file")
    parser.add_argument("--fast", action="store_true", help="Skip AF-Next analysis (librosa only)")
    parser.add_argument("--search", help="Query the index")
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild FAISS index from existing profiles")
    
    args = parser.parse_args()
    
    if args.search:
        search_index(args.search, args.output_dir)
        return

    if args.rebuild_index:
        build_index(args.output_dir)
        return
        
    os.makedirs(args.output_dir, exist_ok=True)
    
    model = None
    processor = None
    provider = None
    if not args.fast:
        try:
            model, processor, provider = load_afnext_model()
        except RuntimeError as exc:
            print(f"SFX profiling refused: {exc}", file=__import__('sys').stderr)
            return 2
        
    if args.file:
        files = [args.file]
    else:
        files = []
        for ext in ('*.mp3', '*.wav', '*.aif', '*.flac'):
            files.extend(glob.glob(os.path.join(args.sfx_dir, "**", ext), recursive=True))
            
    print(f"Found {len(files)} files to analyze.")
    
    if not files:
        print("No files found!")
        return
        
    analyzed = 0
    cached = 0
    errors = 0
    t_start = time.time()

    for i, file_path in enumerate(files):
        filename = os.path.basename(file_path)
        print(f"[{i+1}/{len(files)}] {filename}", end="")
        
        out_path = os.path.join(args.output_dir, f"{filename}.json")
        
        # Smart caching: skip if profile exists AND has description (or in fast mode)
        if os.path.exists(out_path):
            with open(out_path, "r") as f:
                existing = json.load(f)
            if args.fast or existing.get("description", ""):
                print(" — cached")
                cached += 1
                continue
            else:
                print(" — needs description", end="")
                tech_features = existing.get("technical")
        else:
            tech_features = None
        
        t_file = time.time()
        if not tech_features:
            tech_features = analyze_librosa(file_path)
        if not tech_features:
            print(" — ERROR")
            errors += 1
            continue
            
        description = ""
        if not args.fast:
            if provider == "model.laion_clap":
                description = analyze_clap(file_path, model, processor)
            else:
                description = analyze_afnext(file_path, model, processor)
            
        folder_category = os.path.basename(os.path.dirname(file_path))
        
        profile = {
            "file": filename,
            "path": file_path,
            "folder_category": folder_category,
            "technical": tech_features,
            "description": description
        }
        
        with open(out_path, "w") as f:
            json.dump(profile, f, indent=2)

        elapsed = time.time() - t_file
        analyzed += 1
        print(f" — done ({elapsed:.1f}s)")
            
    if not args.fast:
        build_index(args.output_dir)

    total_time = time.time() - t_start
    print(f"\n{'='*50}")
    print(f"SFX Analysis Complete!")
    print(f"  Analyzed: {analyzed}")
    print(f"  Cached:   {cached}")
    print(f"  Errors:   {errors}")
    print(f"  Total:    {total_time:.1f}s")

if __name__ == "__main__":
    main()
