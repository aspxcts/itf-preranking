"""
Points table loader.

The points_table.json file lives at the project root (alongside main.py).
Call load_points_table() once at startup and pass the dict to parse_drawsheet().
"""

from __future__ import annotations

import json
from pathlib import Path

# Default: project root/points_table.json  (src/ → parent → project root)
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "points_table.json"


def load_points_table(path: str | Path | None = None) -> dict:
    """
    Load and return the ITF junior points table.

    Args:
        path: Optional override path to the JSON file.
              Defaults to points_table.json in the project root.
    """
    resolved = Path(path) if path else _DEFAULT_PATH
    with open(resolved, encoding="utf-8") as f:
        return json.load(f)
