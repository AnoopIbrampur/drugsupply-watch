"""Where data lives on disk.

Raw snapshots are stored as ``data/raw/<source>/<YYYY-MM-DD>/`` where the date
is the day *we* fetched them. That fetch date is the point-in-time anchor:
anything first seen in a snapshot dated D was public on D at the latest.
``RXRADAR_DATA`` overrides the root so tests can use a temp directory.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path


def data_root() -> Path:
    return Path(os.environ.get("RXRADAR_DATA", "data"))


def raw_snapshot_dir(source: str, fetch_date: dt.date | None = None) -> Path:
    """Directory for today's (or the given day's) raw snapshot of a source."""
    day = (fetch_date or dt.date.today()).isoformat()
    path = data_root() / "raw" / source / day
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_snapshot_dir(source: str) -> Path | None:
    """Most recent raw snapshot directory for a source, or None if never fetched."""
    base = data_root() / "raw" / source
    if not base.exists():
        return None
    days = sorted(p for p in base.iterdir() if p.is_dir())
    return days[-1] if days else None


def cache_dir() -> Path:
    return data_root() / "cache"
