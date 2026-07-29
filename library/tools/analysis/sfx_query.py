"""
SFX Query Module — Tool interface for LLM-driven SFX retrieval.

Provides structured query functions that an LLM can call to search,
filter, and retrieve SFX profiles without loading the entire library
into context.

Usage as a Python module:
    from sfx_query import SFXIndex
    idx = SFXIndex()
    results = idx.search("metallic crash", top_k=5)
    results = idx.filter(duration_max=2.0, envelope_shape="punchy")
    results = idx.search_and_filter("impact", duration_max=3.0)
    detail = idx.get_detail("metal-pipe-falling.mp3")

Usage from CLI:
    python3 sfx_query.py search "metallic impact"
    python3 sfx_query.py filter --duration-max 2.0 --shape punchy
    python3 sfx_query.py detail "metal-pipe-falling.mp3"
    python3 sfx_query.py hybrid "whoosh" --duration-max 5.0 --category Whoosh
    python3 sfx_query.py summary
"""

import os
import json
import argparse
import numpy as np
from pathlib import Path

DEFAULT_PROFILES_DIR = "/Users/prajwal/Documents/content_stuff/assets i used (just copied here for convenience)/sfx library/profiles"


class SFXIndex:
    """Searchable SFX library index with semantic + metadata querying."""

    def __init__(self, profiles_dir=DEFAULT_PROFILES_DIR):
        self.profiles_dir = profiles_dir
        self._profiles = None
        self._faiss_index = None
        self._embedder = None

    @property
    def profiles(self):
        """Lazy-load profiles from sfx_index.json."""
        if self._profiles is None:
            index_path = os.path.join(self.profiles_dir, "sfx_index.json")
            if not os.path.exists(index_path):
                # Fall back to loading individual JSONs
                self._profiles = []
                for f in sorted(Path(self.profiles_dir).glob("*.json")):
                    if f.name == "sfx_index.json":
                        continue
                    with open(f) as fp:
                        self._profiles.append(json.load(fp))
            else:
                with open(index_path) as f:
                    self._profiles = json.load(f)
        return self._profiles

    @property
    def faiss_index(self):
        """Lazy-load FAISS index."""
        if self._faiss_index is None:
            import faiss
            index_path = os.path.join(self.profiles_dir, "sfx.faiss")
            if os.path.exists(index_path):
                self._faiss_index = faiss.read_index(index_path)
        return self._faiss_index

    @property
    def embedder(self):
        """Lazy-load sentence transformer."""
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        return self._embedder

    def _compact_profile(self, profile, include_embedding=False):
        """Return a compact version of a profile suitable for LLM context."""
        tech = profile.get("technical", {})
        basic = tech.get("basic", {})
        loudness = tech.get("loudness", {})
        spectral = tech.get("spectral", {})
        energy = tech.get("energy_profile", {})
        tonal = tech.get("tonal", {})
        temporal = tech.get("temporal", {})

        compact = {
            "file": profile.get("file", ""),
            "path": profile.get("path", ""),
            "category": profile.get("folder_category", ""),
            "description": profile.get("description", ""),
            "duration_s": basic.get("duration", 0),
            "loudness_rms": loudness.get("rms_mean", 0),
            "brightness": spectral.get("spectral_centroid_mean", 0),
            "noisiness": spectral.get("spectral_flatness_mean", 0),
            "attack_ms": energy.get("attack_time_ms", 0),
            "envelope_shape": energy.get("envelope_shape", ""),
            "is_tonal": tonal.get("is_tonal", False),
            "pitch_hz": tonal.get("estimated_pitch_hz"),
            "num_onsets": temporal.get("num_onsets", 0),
            "is_one_shot": temporal.get("is_one_shot", False),
        }
        if include_embedding and "embedding" in profile:
            compact["embedding"] = profile["embedding"]
        return compact

    # ─── Core Query Methods (for LLM tool use) ───────────────────

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Semantic search: find SFX whose descriptions are most similar to the query.
        
        Args:
            query: Natural language description of desired sound
                   (e.g., "short punchy metallic impact")
            top_k: Number of results to return
            
        Returns:
            List of matching SFX profiles with similarity scores,
            ordered by relevance (highest first).
        """
        if self.faiss_index is None:
            return [{"error": "No FAISS index found. Run sfx_pipeline.py first."}]

        query_emb = self.embedder.encode([query], normalize_embeddings=True)
        D, I = self.faiss_index.search(np.array(query_emb), k=min(top_k, len(self.profiles)))

        results = []
        for i in range(len(I[0])):
            idx = I[0][i]
            if idx < len(self.profiles):
                entry = self._compact_profile(self.profiles[idx])
                entry["relevance_score"] = round(float(D[0][i]), 4)
                results.append(entry)
        return results

    def filter(
        self,
        duration_min: float = None,
        duration_max: float = None,
        envelope_shape: str = None,
        is_tonal: bool = None,
        is_one_shot: bool = None,
        category: str = None,
        min_onsets: int = None,
        max_onsets: int = None,
        brightness_min: float = None,
        brightness_max: float = None,
    ) -> list[dict]:
        """
        Filter SFX by technical properties.
        
        Args:
            duration_min/max: Duration in seconds
            envelope_shape: One of 'punchy', 'sustained', 'swelling', 'fading'
            is_tonal: True for pitched sounds, False for noise-like
            is_one_shot: True for single-hit sounds
            category: Folder category name (e.g., 'Action', 'Whoosh', 'Risers')
            min_onsets/max_onsets: Number of distinct sound events
            brightness_min/max: Spectral centroid (higher = brighter)
            
        Returns:
            List of matching SFX profiles.
        """
        results = []
        for p in self.profiles:
            tech = p.get("technical", {})
            basic = tech.get("basic", {})
            energy = tech.get("energy_profile", {})
            tonal_info = tech.get("tonal", {})
            temporal = tech.get("temporal", {})
            spectral = tech.get("spectral", {})

            dur = basic.get("duration", 0)
            if duration_min is not None and dur < duration_min:
                continue
            if duration_max is not None and dur > duration_max:
                continue
            if envelope_shape is not None and energy.get("envelope_shape") != envelope_shape:
                continue
            if is_tonal is not None and tonal_info.get("is_tonal") != is_tonal:
                continue
            if is_one_shot is not None and temporal.get("is_one_shot") != is_one_shot:
                continue
            if category is not None and category.lower() not in p.get("folder_category", "").lower():
                continue
            if min_onsets is not None and temporal.get("num_onsets", 0) < min_onsets:
                continue
            if max_onsets is not None and temporal.get("num_onsets", 0) > max_onsets:
                continue
            if brightness_min is not None and spectral.get("spectral_centroid_mean", 0) < brightness_min:
                continue
            if brightness_max is not None and spectral.get("spectral_centroid_mean", 0) > brightness_max:
                continue

            results.append(self._compact_profile(p))

        return results

    def search_and_filter(
        self,
        query: str,
        top_k: int = 10,
        **filter_kwargs,
    ) -> list[dict]:
        """
        Hybrid search: semantic search first, then apply metadata filters.
        
        This is the recommended method for LLM-driven SFX selection.
        It combines the strengths of semantic similarity with precise
        technical constraints.
        
        Args:
            query: Natural language description of desired sound
            top_k: Number of candidates to pull from semantic search
                   (filters are applied after, so set this higher than
                   your desired result count)
            **filter_kwargs: Same filters as filter() method
            
        Returns:
            List of matching SFX profiles, ordered by relevance.
        """
        # Get more candidates than needed since filtering will reduce count
        candidates = self.search(query, top_k=min(top_k * 3, len(self.profiles)))

        results = []
        for c in candidates:
            # Apply filters
            if "duration_min" in filter_kwargs and c["duration_s"] < filter_kwargs["duration_min"]:
                continue
            if "duration_max" in filter_kwargs and c["duration_s"] > filter_kwargs["duration_max"]:
                continue
            if "envelope_shape" in filter_kwargs and c["envelope_shape"] != filter_kwargs["envelope_shape"]:
                continue
            if "is_tonal" in filter_kwargs and c["is_tonal"] != filter_kwargs["is_tonal"]:
                continue
            if "is_one_shot" in filter_kwargs and c["is_one_shot"] != filter_kwargs["is_one_shot"]:
                continue
            if "category" in filter_kwargs and filter_kwargs["category"].lower() not in c["category"].lower():
                continue
            results.append(c)
            if len(results) >= top_k:
                break

        return results

    def get_detail(self, filename: str) -> dict:
        """
        Get the full profile for a specific SFX file.
        
        Args:
            filename: The SFX filename (e.g., "metal-pipe-falling.mp3")
            
        Returns:
            Complete SFX profile with all technical details.
        """
        for p in self.profiles:
            if p.get("file", "") == filename:
                return self._compact_profile(p)
        # Try partial match
        for p in self.profiles:
            if filename.lower() in p.get("file", "").lower():
                return self._compact_profile(p)
        return {"error": f"SFX '{filename}' not found"}

    def summary(self) -> dict:
        """
        Get a summary of the entire SFX library.
        
        Returns:
            Library statistics: total files, categories, described count,
            duration range, envelope shape distribution.
        """
        total = len(self.profiles)
        described = sum(1 for p in self.profiles if p.get("description", ""))
        categories = {}
        shapes = {}
        durations = []

        for p in self.profiles:
            cat = p.get("folder_category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1

            tech = p.get("technical", {})
            shape = tech.get("energy_profile", {}).get("envelope_shape", "unknown")
            shapes[shape] = shapes.get(shape, 0) + 1
            durations.append(tech.get("basic", {}).get("duration", 0))

        return {
            "total_files": total,
            "described_files": described,
            "needs_description": total - described,
            "categories": categories,
            "envelope_shapes": shapes,
            "duration_range": {
                "min_s": round(min(durations), 1) if durations else 0,
                "max_s": round(max(durations), 1) if durations else 0,
                "avg_s": round(sum(durations) / len(durations), 1) if durations else 0,
            },
        }

    # ─── Tool Descriptions (for LLM function calling) ────────────

    @staticmethod
    def get_tool_definitions() -> list[dict]:
        """
        Returns OpenAI-style tool/function definitions for LLM function calling.
        
        Pass these to your LLM's tools parameter so it knows how to query the SFX library.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_sfx",
                    "description": "Semantic search for sound effects by natural language description. Returns the most relevant SFX from the library based on description similarity.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Natural language description of the desired sound (e.g., 'short punchy metallic impact', 'deep rumbling bass', 'camera shutter click')"
                            },
                            "top_k": {
                                "type": "integer",
                                "description": "Number of results to return (default: 5)",
                                "default": 5
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "filter_sfx",
                    "description": "Filter sound effects by measurable technical properties like duration, envelope shape, tonal quality, etc.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "duration_max": {"type": "number", "description": "Maximum duration in seconds"},
                            "duration_min": {"type": "number", "description": "Minimum duration in seconds"},
                            "envelope_shape": {
                                "type": "string",
                                "enum": ["punchy", "sustained", "swelling", "fading"],
                                "description": "Energy envelope shape: punchy (fast attack+decay), sustained (steady), swelling (slow build), fading (gradual decrease)"
                            },
                            "is_tonal": {"type": "boolean", "description": "True for pitched/musical sounds, False for noise-like sounds"},
                            "is_one_shot": {"type": "boolean", "description": "True for single-hit sounds (≤2 onsets, <3s)"},
                            "category": {"type": "string", "description": "Folder category (e.g., 'Action', 'Whoosh', 'Risers', 'Glitch', 'Environment')"},
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_and_filter_sfx",
                    "description": "Hybrid search: semantic similarity search combined with technical property filters. Best method for finding specific SFX.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Natural language description of the desired sound"},
                            "duration_max": {"type": "number", "description": "Maximum duration in seconds"},
                            "duration_min": {"type": "number", "description": "Minimum duration in seconds"},
                            "envelope_shape": {"type": "string", "enum": ["punchy", "sustained", "swelling", "fading"]},
                            "is_tonal": {"type": "boolean"},
                            "is_one_shot": {"type": "boolean"},
                            "category": {"type": "string"},
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_sfx_detail",
                    "description": "Get the full profile of a specific SFX file, including all technical measurements and semantic description.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "description": "The SFX filename (e.g., 'metal-pipe-falling.mp3')"}
                        },
                        "required": ["filename"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "sfx_library_summary",
                    "description": "Get an overview of the SFX library: total files, categories, duration range, and how many files have been analyzed.",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
        ]


# ─── CLI Interface ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SFX Query Tool")
    sub = parser.add_subparsers(dest="command")

    # search
    p_search = sub.add_parser("search", help="Semantic search for SFX")
    p_search.add_argument("query", help="Natural language query")
    p_search.add_argument("-k", "--top-k", type=int, default=5)

    # filter
    p_filter = sub.add_parser("filter", help="Filter SFX by properties")
    p_filter.add_argument("--duration-min", type=float)
    p_filter.add_argument("--duration-max", type=float)
    p_filter.add_argument("--shape", choices=["punchy", "sustained", "swelling", "fading"])
    p_filter.add_argument("--tonal", action="store_true", default=None)
    p_filter.add_argument("--not-tonal", action="store_true")
    p_filter.add_argument("--one-shot", action="store_true", default=None)
    p_filter.add_argument("--category")

    # hybrid
    p_hybrid = sub.add_parser("hybrid", help="Semantic search + filters")
    p_hybrid.add_argument("query", help="Natural language query")
    p_hybrid.add_argument("-k", "--top-k", type=int, default=5)
    p_hybrid.add_argument("--duration-max", type=float)
    p_hybrid.add_argument("--duration-min", type=float)
    p_hybrid.add_argument("--shape", choices=["punchy", "sustained", "swelling", "fading"])
    p_hybrid.add_argument("--category")

    # detail
    p_detail = sub.add_parser("detail", help="Get full SFX profile")
    p_detail.add_argument("filename", help="SFX filename")

    # summary
    sub.add_parser("summary", help="Library overview")

    # tools
    sub.add_parser("tools", help="Print LLM tool definitions as JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    idx = SFXIndex()

    if args.command == "search":
        results = idx.search(args.query, args.top_k)
        for i, r in enumerate(results):
            print(f"\n{i+1}. {r['file']} (score: {r['relevance_score']:.4f})")
            print(f"   Category: {r['category']} | {r['duration_s']:.1f}s | {r['envelope_shape']} | tonal={r['is_tonal']}")
            print(f"   {r['description'][:150]}")

    elif args.command == "filter":
        is_tonal = True if args.tonal else (False if args.not_tonal else None)
        is_one_shot = True if args.one_shot else None
        results = idx.filter(
            duration_min=args.duration_min,
            duration_max=args.duration_max,
            envelope_shape=args.shape,
            is_tonal=is_tonal,
            is_one_shot=is_one_shot,
            category=args.category,
        )
        print(f"Found {len(results)} matching SFX:")
        for r in results:
            print(f"  {r['file']:40s} {r['duration_s']:5.1f}s  {r['envelope_shape']:10s}  {r['category']}")

    elif args.command == "hybrid":
        kwargs = {}
        if args.duration_max: kwargs["duration_max"] = args.duration_max
        if args.duration_min: kwargs["duration_min"] = args.duration_min
        if args.shape: kwargs["envelope_shape"] = args.shape
        if args.category: kwargs["category"] = args.category
        results = idx.search_and_filter(args.query, top_k=args.top_k, **kwargs)
        for i, r in enumerate(results):
            print(f"\n{i+1}. {r['file']} (score: {r['relevance_score']:.4f})")
            print(f"   Category: {r['category']} | {r['duration_s']:.1f}s | {r['envelope_shape']} | tonal={r['is_tonal']}")
            print(f"   {r['description'][:150]}")

    elif args.command == "detail":
        result = idx.get_detail(args.filename)
        print(json.dumps(result, indent=2))

    elif args.command == "summary":
        result = idx.summary()
        print(json.dumps(result, indent=2))

    elif args.command == "tools":
        print(json.dumps(SFXIndex.get_tool_definitions(), indent=2))


if __name__ == "__main__":
    main()
