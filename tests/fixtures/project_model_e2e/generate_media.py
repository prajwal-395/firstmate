"""Rebuild the committed synthetic Project Model qualification media."""
from __future__ import annotations

import struct
import subprocess
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / "media"

PORTRAITS = {
    "person-ada": ("Ada", "#176b87", "#f0c49b", "#30251e", "en-us+f3"),
    "person-ben": ("Ben", "#8a4b24", "#d9a17b", "#251c18", "en-us+m3"),
}


def run(*args: str) -> None:
    subprocess.run(args, check=True, encoding="utf-8")


def write_portrait(path: Path, shirt: str, skin: str, hair: str) -> None:
    width, height = 320, 180
    pixels = bytearray([232, 241, 244] * width * height)

    def color(value: str) -> tuple[int, int, int]:
        return tuple(bytes.fromhex(value.lstrip("#")))

    def ellipse(cx: int, cy: int, rx: int, ry: int, fill: str) -> None:
        rgb = color(fill)
        for y in range(max(0, cy - ry), min(height, cy + ry + 1)):
            for x in range(max(0, cx - rx), min(width, cx + rx + 1)):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                    offset = (y * width + x) * 3
                    pixels[offset:offset + 3] = bytes(rgb)

    def rect(x0: int, y0: int, x1: int, y1: int, fill: str) -> None:
        rgb = bytes(color(fill))
        for y in range(y0, y1):
            for x in range(x0, x1):
                offset = (y * width + x) * 3
                pixels[offset:offset + 3] = rgb

    rect(0, 130, width, height, shirt)
    ellipse(160, 76, 43, 52, skin)
    ellipse(160, 38, 44, 22, hair)
    rect(116, 45, 126, 66, hair)
    rect(194, 45, 204, 66, hair)
    ellipse(145, 77, 4, 5, "#211b19")
    ellipse(176, 77, 4, 5, "#211b19")
    for x in range(145, 176):
        y = 101 + int(0.012 * (x - 160) ** 2)
        rect(x, y, x + 1, y + 3, "#7f3434")
    rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3]
                    for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" +
                     chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def portrait(entity_id: str, details: tuple[str, str, str, str, str]) -> Path:
    name, shirt, skin, hair, _voice = details
    svg = MEDIA / f"{entity_id}.svg"
    svg.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
<rect width="320" height="180" fill="#e8f1f4"/><rect y="130" width="320" height="50" fill="{shirt}"/>
<path d="M110 180 Q118 112 160 112 Q202 112 210 180" fill="{shirt}"/>
<ellipse cx="160" cy="76" rx="43" ry="52" fill="{skin}"/>
<path d="M117 70 Q112 18 160 18 Q207 20 203 69 Q187 48 160 48 Q134 48 117 70" fill="{hair}"/>
<circle cx="145" cy="77" r="4" fill="#211b19"/><circle cx="176" cy="77" r="4" fill="#211b19"/>
<path d="M145 99 Q160 110 176 98" fill="none" stroke="#7f3434" stroke-width="4" stroke-linecap="round"/>
<text x="160" y="164" text-anchor="middle" font-family="sans-serif" font-size="16" fill="white">{name}</text></svg>''',
        encoding="utf-8",
    )
    png = MEDIA / f"{entity_id}.png"
    write_portrait(png, shirt, skin, hair)
    return png


def speech_video(entity_id: str, details: tuple[str, str, str, str, str], text: str) -> None:
    _name, _shirt, _skin, _hair, voice = details
    wav = MEDIA / f".{entity_id}.wav"
    run("espeak-ng", "-v", voice, "-s", "145", "-w", str(wav), text)
    png = MEDIA / f"{entity_id}.png"
    out = MEDIA / f"{entity_id}-aroll.mp4"
    run("ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "24", "-i", str(png),
        "-i", str(wav), "-shortest", "-vf", "format=yuv420p", "-r", "24", "-c:v", "libx264",
        "-preset", "medium", "-crf", "30", "-threads", "1", "-c:a", "aac", "-b:a", "48k",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        "-map_metadata", "-1", "-metadata", "creation_time=1970-01-01T00:00:00Z",
        "-movflags", "+faststart", str(out))
    wav.unlink()


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    for entity_id, details in PORTRAITS.items():
        portrait(entity_id, details)
    speech_video("person-ada", PORTRAITS["person-ada"], "I am Ada. I explain the first idea.")
    speech_video("person-ben", PORTRAITS["person-ben"], "I am Ben. I show the second view.")
    # An intentionally silent scenic insert gives the model a distinct B-roll source.
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=24",
        "-frames:v", "48", "-r", "24", "-vf", "format=yuv420p", "-c:v", "libx264",
        "-preset", "medium", "-crf", "30", "-threads", "1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
        "-map_metadata", "-1", "-metadata", "creation_time=1970-01-01T00:00:00Z",
        "-an", str(MEDIA / "coast-broll.mp4"))


if __name__ == "__main__":
    main()
