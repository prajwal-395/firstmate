"""Ren host hooks: the marker-feedback hook lives here."""

from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = HOOK_DIR / "templates"
HOOK_NAME = "ren-marker-hook"
