from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def read_json_file(
    path: Path,
    *,
    name: str = "",
    default_factory: Callable[[], Any] = dict,
    expected_types: type | tuple[type, ...] | None = None,
) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_factory()
    if expected_types is not None and not isinstance(data, expected_types):
        return default_factory()
    return data


def read_json_object(path: Path, *, name: str = "") -> dict[str, Any]:
    data = read_json_file(path, name=name, default_factory=dict, expected_types=dict)
    return data if isinstance(data, dict) else {}


def write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
