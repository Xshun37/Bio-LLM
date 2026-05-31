"""Utilities for loading and normalizing Bioextract result JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(data: Any, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def relation_list(entry: Any) -> list[dict[str, Any]]:
    """Return relation dicts from supported result entry shapes."""
    if isinstance(entry, list):
        return [item for item in entry if isinstance(item, dict)]
    if isinstance(entry, dict):
        result = entry.get("result", [])
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
    return []


def normalize_results_map(data: Any, default_id: str = "input") -> dict[str, Any]:
    """Normalize common Bioextract JSON shapes to {paper_id: entry}.

    Supported shapes:
    - {paper_id: [relations]}
    - {paper_id: {"result": [relations], ...}}
    - {"result": [relations], ...}
    - [relations]
    """
    if isinstance(data, list):
        return {default_id: data}
    if not isinstance(data, dict):
        return {default_id: []}
    if "result" in data and not any(isinstance(value, (list, dict)) for value in data.values()):
        return {default_id: data}
    if "result" in data and isinstance(data.get("result"), list):
        structural_keys = {"result", "round1_analysis", "round2_raw", "round2_clean", "round1_usage", "round2_usage", "normalization_log", "error"}
        if set(data).issubset(structural_keys):
            return {default_id: data}
    return {str(key): value for key, value in data.items()}
