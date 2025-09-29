#!/usr/bin/env python3
"""Configuration helpers for Codextendo."""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Dict

_CODEXTENDO_HOME = pathlib.Path(
    os.environ.get("CODEXTENDO_HOME", pathlib.Path.home() / ".codextendo")
)
_CONFIG_FILENAME = "config.json"


def config_dir() -> pathlib.Path:
    return _CODEXTENDO_HOME


def config_path() -> pathlib.Path:
    return config_dir() / _CONFIG_FILENAME


def load_config() -> Dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        return {}


def save_config(data: Dict[str, Any]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = config_path()
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def get_config_value(key: str, default: Any = None) -> Any:
    return load_config().get(key, default)


def set_config_value(key: str, value: Any) -> None:
    data = load_config()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    save_config(data)


__all__ = [
    "config_dir",
    "config_path",
    "load_config",
    "save_config",
    "get_config_value",
    "set_config_value",
]
