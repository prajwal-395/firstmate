#!/usr/bin/env python3
"""
Step 2.4 Tool: Search YouTube for Music

Searches YouTube for music tracks matching a query string.
Returns metadata for each result (title, URL, duration, channel).

Usage (stdin/stdout JSON):
    echo '{"query": "lo-fi motivational beat no copyright", "max_results": 5}' | python3 search_youtube.py

Requires: yt-dlp (pip install yt-dlp)
"""
import json
import subprocess
import sys


def search_youtube(query: str, max_results: int = 5) -> dict:
    """
    Search YouTube for videos matching the query.
    Returns structured results using yt-dlp's search feature.
    """
    search_url = f"ytsearch{max_results}:{query}"

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--flat-playlist",
                "--no-download",
                search_url,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp not found. Install it: pip install yt-dlp"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("YouTube search timed out")

    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp search failed: {result.stderr[:300]}"
        )

    results = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            results.append({
                "title": entry.get("title", "Unknown"),
                "url": entry.get("url") or entry.get("webpage_url")
                or f"https://www.youtube.com/watch?v={entry.get('id', '')}",
                "duration_seconds": entry.get("duration"),
                "duration_string": entry.get("duration_string", ""),
                "channel": entry.get("channel", entry.get("uploader", "Unknown")),
                "view_count": entry.get("view_count"),
            })
        except json.JSONDecodeError:
            continue

    if not results:
        raise ValueError(
            f"No results found for query: {query}"
        )

    return {
        "query": query,
        "results": results,
        "total_results": len(results),
    }


def main():
    input_data = json.loads(sys.stdin.read())
    query = input_data.get("query")
    max_results = input_data.get("max_results", 5)

    if not query:
        print(json.dumps({
            "error": "Missing required input: query",
            "tool": "search_youtube"
        }))
        sys.exit(1)

    try:
        result = search_youtube(query, max_results)
    except (RuntimeError, ValueError) as e:
        print(json.dumps({
            "error": str(e),
            "tool": "search_youtube"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
