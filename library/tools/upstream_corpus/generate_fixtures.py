"""Generate the committed synthetic fixtures for the upstream engine corpus.

Stdlib only: solid-color PNG stills (8-bit RGB, no interlace) and a one-second
stereo sine WAV. Outputs are committed under ``fixtures/`` so weekly intake
never depends on network access or local synthesis tools to run its render
corpus. Re-run with ``python3 generate_fixtures.py`` and commit the result;
``corpus.json`` records each fixture's sha256, and the intake refuses to run
when a fixture on disk does not match the recorded hash.
"""
from __future__ import annotations

import math
import struct
import wave
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"

STILLS = {
    "still-red.png": (255, 0, 0),
    "still-green.png": (0, 255, 0),
    "still-blue.png": (0, 0, 255),
    "still-white.png": (255, 255, 255),
    "still-gray.png": (128, 128, 128),
}
STILL_WIDTH = 64
STILL_HEIGHT = 36

WAV_NAME = "stereo-1s.wav"
WAV_RATE = 44100
WAV_SECONDS = 1.0
WAV_LEFT_HZ = 440.0
WAV_RIGHT_HZ = 880.0
WAV_PEAK = 0.5


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(
        ">I", zlib.crc32(body) & 0xFFFFFFFF
    )


def write_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    pixels = bytes(rgb) * width
    raw = b"".join(b"\x00" + pixels for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def write_wav(path: Path) -> None:
    frames = int(WAV_RATE * WAV_SECONDS)
    samples = bytearray()
    for index in range(frames):
        moment = index / WAV_RATE
        left = int(WAV_PEAK * 32767 * math.sin(2 * math.pi * WAV_LEFT_HZ * moment))
        right = int(WAV_PEAK * 32767 * math.sin(2 * math.pi * WAV_RIGHT_HZ * moment))
        samples += struct.pack("<hh", left, right)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(WAV_RATE)
        handle.writeframes(bytes(samples))


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, rgb in sorted(STILLS.items()):
        write_png(FIXTURES / name, STILL_WIDTH, STILL_HEIGHT, rgb)
    write_wav(FIXTURES / WAV_NAME)
    print(f"wrote {len(STILLS)} stills and {WAV_NAME} into {FIXTURES}")


if __name__ == "__main__":
    main()
