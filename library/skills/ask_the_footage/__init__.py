"""Skill entry point re-export."""
from library.skills.ask_the_footage.skill import (
    SKILL_NAME, ask_vision, capture_still, main, run,
)

__all__ = ["SKILL_NAME", "ask_vision", "capture_still", "main", "run"]
