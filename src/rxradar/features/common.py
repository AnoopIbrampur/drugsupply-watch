"""Shared helpers for point-in-time feature builders.

Contract (enforced by features/pit.py in tests): a builder receives
``sources`` (dict of DataFrames, each with a ``published`` date column) and
``weeks``, and returns rows keyed by (week, family). It may only emit a row
for week *t* once the family has some data published by *t*, and every value
must be computable from rows with ``published <= t``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NS = "datetime64[ns]"


def family_grid(sources: dict[str, pd.DataFrame], weeks: pd.DatetimeIndex) -> pd.DataFrame:
    """(week, family) for every family once its first record (product,
    listing or NDC snapshot row) is published."""
    firsts = []
    for name in ("products", "listings", "ndc", "prices"):
        df = sources.get(name)
        if df is not None and len(df):
            firsts.append(df.dropna(subset=["family"]).groupby("family").published.min())
    if not firsts:
        return pd.DataFrame(columns=["week", "family"])
    first = pd.concat(firsts).groupby(level=0).min()
    w = pd.DataFrame({"week": pd.DatetimeIndex(weeks).astype(NS)})
    f = first.rename("first").reset_index()
    f["first"] = f["first"].astype(NS)
    grid = w.merge(f, how="cross")
    grid = grid[grid.week >= grid["first"]].drop(columns="first")
    return grid.sort_values(["family", "week"]).reset_index(drop=True)


def count_in_window(
    grid: pd.DataFrame, events: pd.DataFrame, days: int | None, by: str = "family"
) -> np.ndarray:
    """For each grid row, the number of events for its ``by`` key with
    published in (week - days, week]; ``days=None`` means all history."""
    out = np.zeros(len(grid), dtype=np.int64)
    if events is None or len(events) == 0:
        return out
    ev = events.dropna(subset=[by]).sort_values("published")
    groups = {k: g.published.values.astype(NS) for k, g in ev.groupby(by)}
    keys = grid[by].values
    t = grid["week"].values.astype(NS)
    order = np.argsort(keys, kind="stable")
    sk = keys[order]
    bounds = np.flatnonzero(np.r_[True, sk[1:] != sk[:-1], True])
    for lo, hi in zip(bounds[:-1], bounds[1:], strict=True):
        idx = order[lo:hi]
        arr = groups.get(sk[lo])
        if arr is None:
            continue
        upto = np.searchsorted(arr, t[idx], side="right")
        if days is None:
            out[idx] = upto
        else:
            start = np.searchsorted(arr, t[idx] - np.timedelta64(days, "D"), side="right")
            out[idx] = upto - start
    return out


def days_since_last(grid: pd.DataFrame, events: pd.DataFrame, by: str = "family") -> np.ndarray:
    """Days from the latest event published on or before each week (NaN if none)."""
    out = np.full(len(grid), np.nan)
    if events is None or len(events) == 0:
        return out
    ev = events.dropna(subset=[by])
    groups = {k: np.sort(g.published.values.astype(NS)) for k, g in ev.groupby(by)}
    t = grid["week"].values.astype(NS)
    for k, idx in grid.groupby(by).indices.items():
        arr = groups.get(k)
        if arr is None:
            continue
        pos = np.searchsorted(arr, t[idx], side="right") - 1
        ok = pos >= 0
        out[idx[ok]] = (t[idx[ok]] - arr[pos[ok]]).astype("timedelta64[D]").astype(float)
    return out


def asof_capture(weeks: pd.Series, captures: np.ndarray) -> np.ndarray:
    """Latest capture date on or before each week (NaT if none)."""
    caps = np.sort(np.asarray(captures, dtype=NS))
    pos = np.searchsorted(caps, weeks.values.astype(NS), side="right") - 1
    out = np.full(len(weeks), np.datetime64("NaT", "ns"), dtype=NS)
    out[pos >= 0] = caps[pos[pos >= 0]]
    return out
