#!/usr/bin/env python3
"""Utilities for loading Codextendo's bundled OpenAI pricing catalogue."""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Dict

_REPO_ROOT = pathlib.Path(__file__).resolve().parent
_DEFAULT_RELATIVE = pathlib.Path("resources/pricing/openai_api_model_pricing.json")
_INSTALL_ROOT = pathlib.Path(os.environ.get("CODEXTENDO_HOME", pathlib.Path.home() / ".codextendo"))


def _candidate_paths() -> list[pathlib.Path]:
    """Return possible locations for the pricing JSON, newest first."""
    repo_path = _REPO_ROOT / _DEFAULT_RELATIVE
    install_path = _INSTALL_ROOT / _DEFAULT_RELATIVE
    return [repo_path, install_path]


def find_pricing_file() -> pathlib.Path:
    """Locate the pricing JSON file on disk.

    The function prefers a repo-local copy (useful during development) and falls
    back to the version installed under ``~/.codextendo``.
    """

    for path in _candidate_paths():
        if path.exists():
            return path
    raise FileNotFoundError(
        "Could not locate openai_api_model_pricing.json in the repository or the installation directory."
    )


def load_pricing() -> Dict[str, Any]:
    """Load and return the pricing catalogue as a Python dictionary."""

    path = find_pricing_file()
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


__all__ = ["find_pricing_file", "load_pricing"]
