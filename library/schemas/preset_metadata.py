from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class PresetEntry:
    name: str
    description: str
    category: str  # powergrade, fusion-macro, lut, dctl, fairlight
    tags: List[str]
    compatibility: Dict[str, Any]
    file_path: str

    @classmethod
    def from_dict(cls, data: dict, file_path: str) -> 'PresetEntry':
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            tags=data.get("tags", []),
            compatibility=data.get("compatibility", {}),
            file_path=file_path
        )
