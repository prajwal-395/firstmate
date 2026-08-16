from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class PresetEntry:
    name: str
    description: str
    category: str  # fusion-macro, fairlight
    tags: List[str]
    compatibility: Dict[str, Any]
    file_path: str
    mood_match: List[str] = field(default_factory=list)
    energy_match: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, file_path: str) -> 'PresetEntry':
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            tags=data.get("tags", []),
            compatibility=data.get("compatibility", {}),
            file_path=file_path,
            mood_match=data.get("mood_match", []),
            energy_match=data.get("energy_match", [])
        )
