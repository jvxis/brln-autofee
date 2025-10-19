from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict


@lru_cache()
def _load_presets() -> Dict[str, Dict[str, Any]]:
    path = Path(__file__).resolve().parent / "presets_modes.payload_json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("presets_modes.payload_json must contain a JSON object at the root")
    return data


def get_mode_presets(mode: str) -> Dict[str, Dict[str, Any]]:
    presets = _load_presets()
    if not presets:
        return {}
    if mode in presets:
        return presets[mode]
    return presets.get("conservador", {})
