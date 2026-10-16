"""Read and validate Ren's research candidate watchlist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from datetime import date


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = ROOT / "data" / "model_watch" / "candidates.json"


def load_database(path: Path = DEFAULT_DATABASE) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["schema_version"] != 1:
        raise ValueError(f"unsupported model watch schema: {data['schema_version']}")
    ids: set[str] = set()
    for candidate in data["candidates"]:
        for field in ("id", "name", "release_date", "last_updated", "capability", "code", "weights", "apple_silicon", "performance", "license", "sources", "decision"):
            if field not in candidate:
                raise ValueError(f"{candidate.get('id', '<unknown>')}: missing {field}")
        if candidate["id"] in ids:
            raise ValueError(f"duplicate candidate id: {candidate['id']}")
        ids.add(candidate["id"])
        date.fromisoformat(candidate["last_updated"])
        if candidate["release_date"] is not None:
            date.fromisoformat(candidate["release_date"])
        if not isinstance(candidate["sources"], list) or not candidate["sources"]:
            raise ValueError(f"{candidate['id']}: at least one source URL is required")
        if candidate["performance"]["status"] not in {"unmeasured", "reported", "measured"}:
            raise ValueError(f"{candidate['id']}: invalid performance status")
        if candidate["decision"]["status"] not in {"watch", "candidate", "rejected", "adopted"}:
            raise ValueError(f"{candidate['id']}: invalid decision status")
    return data


def should_evaluate(candidate: dict[str, Any]) -> bool:
    """Flag relevant, actionable, not-yet-rejected candidates for evaluation."""
    return (
        candidate["relevance"] == "relevant"
        and candidate["decision"]["status"] in {"watch", "candidate"}
        and candidate["code"]["status"] == "available"
        and candidate["weights"]["status"] == "available"
        and candidate["apple_silicon"]["status"] in {"native", "compatible"}
        and candidate["license"]["status"] in {"permissive", "conditional"}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--refresh-date", help="Print candidates whose source review is due by this date (YYYY-MM-DD).")
    parser.add_argument("--refresh-after-days", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="Print full matching records as JSON.")
    args = parser.parse_args()
    database = load_database(args.database)
    candidates = database["candidates"]
    if args.refresh_date:
        from datetime import date, timedelta

        cutoff = date.fromisoformat(args.refresh_date) - timedelta(days=args.refresh_after_days)
        candidates = [c for c in candidates if date.fromisoformat(c["last_updated"]) <= cutoff]
        heading = f"Due for source refresh (last checked on or before {cutoff.isoformat()}):"
    else:
        candidates = [c for c in candidates if should_evaluate(c)]
        heading = "Flagged for evaluation:"
    if args.json:
        print(json.dumps(candidates, ensure_ascii=False, indent=2))
    else:
        print(heading)
        for candidate in candidates:
            print(f"{candidate['id']}\t{candidate['name']}\t{candidate['capability']}\t{candidate['decision']['status']}")
        print(f"{len(candidates)} candidate(s)")


if __name__ == "__main__":
    main()
