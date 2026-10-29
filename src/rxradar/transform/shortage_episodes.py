"""Turn weekly archive captures of FDA's shortage list into shortage episodes.

An *episode* is a run of consecutive captures in which a listing name has
status Current. Its start date is the capture where it first appears as
Current, which is point-in-time safe: FDA had published it by then.

Two things make this harder than a diff:
- Capture gaps. Wayback captures are not perfectly weekly (one gap is ~1
  year, 2024-09 to 2025-09). If a name is Current right after a long gap, we
  cannot know whether it started during the gap, so the start is flagged as
  ``start_censored`` rather than dated precisely.
- Flicker. Names sometimes drop off for a week and come back (page glitches,
  renames). Absences shorter than ``merge_gap_days`` are merged into one episode.

This module is a Phase 1 prototype used to count episodes for the data audit;
the final label definition is still open (docs/decisions.md).
"""

from __future__ import annotations

import re

import pandas as pd

MAX_CAPTURE_GAP_DAYS = 21
MERGE_GAP_DAYS = 56


def normalize_name(name: str) -> str:
    """Collapse cosmetic differences in listing names across years.

    Drops bracketed brand names ("Paliperidone (INVEGA) ..."), "USP" markers
    and punctuation, and lowercases. It deliberately keeps the dosage form
    words, which are part of the drug family.
    """
    s = re.sub(r"\([^)]*\)", " ", name)
    s = re.sub(r"\bUSP\b", " ", s, flags=re.I)
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return " ".join(s.split())


def build_episodes(
    listings: pd.DataFrame,
    max_capture_gap_days: int = MAX_CAPTURE_GAP_DAYS,
    merge_gap_days: int = MERGE_GAP_DAYS,
) -> pd.DataFrame:
    """Build episodes from rows of (capture_ts, name, status).

    Returns one row per episode with: key, start, end (last capture seen as
    Current), start_censored (could have started earlier than observed), and
    ongoing (still Current in the last capture).
    """
    df = listings.copy()
    df["date"] = pd.to_datetime(df["capture_ts"].str[:8], format="%Y%m%d")
    df["key"] = df["name"].map(normalize_name)
    captures = sorted(df["date"].unique())
    prev_capture = dict(zip(captures[1:], captures[:-1], strict=True))
    last_capture = captures[-1]

    current = df[df["status"] == "Current"].drop_duplicates(["key", "date"])
    episodes = []
    for key, grp in current.groupby("key"):
        dates = sorted(grp["date"])
        start = end = dates[0]
        for d in dates[1:]:
            if (d - end).days <= merge_gap_days:
                end = d
                continue
            episodes.append(_episode(key, start, end, prev_capture, max_capture_gap_days))
            start = end = d
        episodes.append(_episode(key, start, end, prev_capture, max_capture_gap_days))

    out = pd.DataFrame(episodes)
    out["ongoing"] = out["end"] == last_capture
    return out.sort_values(["start", "key"]).reset_index(drop=True)


def _episode(key, start, end, prev_capture, max_gap) -> dict:
    prev = prev_capture.get(start)
    # Censored if this is the first capture ever, or the previous capture is
    # too far back to rule out an earlier, unseen start.
    censored = prev is None or (start - prev).days > max_gap
    return {"key": key, "start": start, "end": end, "start_censored": censored}
