"""Synthetic OCR fixtures with known ground truth (GEO-018..021 calibration).

Renders text images with PIL so the labels are exact by construction -
no agent labelling needed for this split. Covers numbers, currency,
units, dates (GEO-020), a two-column table (GEO-021) and a lower-third
style band (GEO-014). Real-footage frames get agent labels separately;
this file is the always-runnable half of the benchmark.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES = [
    {"id": "syn-currency-01", "kind": "label", "number_kind": "currency",
     "lines": ["$1,250.00"]},
    {"id": "syn-units-01", "kind": "label", "number_kind": "unit",
     "lines": ["1080p 60fps 24mm"]},
    {"id": "syn-date-01", "kind": "caption", "number_kind": "date",
     "lines": ["Oct 15, 2026"]},
    {"id": "syn-lower-third-01", "kind": "lower_third", "number_kind": None,
     "lines": ["AKSHITA SHARMA", "Founder, Lucie"]},
    {"id": "syn-table-01", "kind": "chart", "number_kind": None,
     "lines": ["Q1 12 18", "Q2 09 22"],
     "layout": [{"layout_id": "row-q1", "text": "Q1 12 18"},
                {"layout_id": "row-q2", "text": "Q2 09 22"}]},
    {"id": "syn-slide-01", "kind": "slide", "number_kind": None,
     "lines": ["REN PIPELINE", "recall 0.85 latency 2.5ms"]},
]


def render(out_dir: Path, width: int = 1280, height: int = 720,
           font_size: int = 64) -> dict:
    """Render fixtures. Returns a corpus document fragment."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise SystemExit("PIL required: pip install pillow") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc",
                                  font_size)
        small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc",
                                   font_size // 2)
    except OSError:
        font = ImageFont.load_default()
        small = font
    frames = []
    for fixture in FIXTURES:
        image = Image.new("RGB", (width, height), "black")
        draw = ImageDraw.Draw(image)
        y = 120
        boxes = []
        for line in fixture["lines"]:
            use = small if len(line) > 20 else font
            bbox = draw.textbbox((160, y), line, font=use)
            draw.text((160, y), line, font=use, fill="white")
            boxes.append({
                "text": line,
                "bbox": [bbox[0] / width, bbox[1] / height,
                         (bbox[2] - bbox[0]) / width,
                         (bbox[3] - bbox[1]) / height],
                "kind": fixture["kind"],
                **({"number_kind": fixture["number_kind"]}
                   if fixture["number_kind"] else {}),
            })
            y = bbox[3] + 30
        path = out_dir / f"{fixture['id']}.png"
        image.save(path)
        layout_rows = fixture.get("layout") or []
        for row, box in zip(layout_rows, boxes):
            row["bbox"] = box["bbox"]
        frames.append({
            "id": fixture["id"], "split": "calibration",
            "image": str(path),
            "labels": boxes,
            "predictions": [],
            **({"layout_rows": layout_rows} if layout_rows else {}),
        })
    return {"schema": "ren_ocr_corpus/1", "seed": "syn-v1",
            "frames": frames}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    args = parser.parse_args()
    corpus = render(args.images)
    args.out.write_text(json.dumps(corpus, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({"fixtures": len(corpus["frames"])}))


if __name__ == "__main__":
    main()
