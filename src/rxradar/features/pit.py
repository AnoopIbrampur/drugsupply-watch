"""Point-in-time (no look-ahead) check for feature builders.

The brief's non-negotiable rule: a feature for week *t* may only use data
that was public at *t*. Every source table we build features from carries a
``published`` date column: the earliest day the row was publicly
available (an openFDA ``report_date``, a Data Dashboard ``FMD145Date``, a
NADAC ``as_of_date``, an archive capture date...).

A feature builder is any function ``build(sources, weeks) -> DataFrame`` with
``week`` and ``family`` columns. ``check_point_in_time`` recomputes the
features for each checked week *t* from sources cut off at *t* and compares
them with the same rows from the full-history run. Any difference means the
builder read something published after *t*, and the check fails with the
offending rows.

Phase 3 tests run every builder through this check.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pandas as pd

Builder = Callable[[dict[str, pd.DataFrame], pd.DatetimeIndex], pd.DataFrame]


class LookAheadError(AssertionError):
    pass


def truncate(sources: dict[str, pd.DataFrame], t: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Keep only rows published on or before t, in every source."""
    out = {}
    for name, df in sources.items():
        if "published" not in df.columns:
            raise ValueError(f"source {name!r} has no 'published' column")
        out[name] = df[df["published"] <= t]
    return out


def check_point_in_time(
    build: Builder,
    sources: dict[str, pd.DataFrame],
    weeks: pd.DatetimeIndex,
    check_weeks: Iterable[pd.Timestamp],
    keys: tuple[str, ...] = ("week", "family"),
) -> None:
    """Raise LookAheadError if any feature at a checked week changes when
    data published after that week is removed."""
    for name, df in sources.items():
        if "published" not in df.columns:
            raise ValueError(f"source {name!r} has no 'published' column")
    full = build(sources, weeks).set_index(list(keys)).sort_index()
    for t in check_weeks:
        t = pd.Timestamp(t)
        cut = build(truncate(sources, t), weeks[weeks <= t]).set_index(list(keys)).sort_index()
        a = (
            full.xs(t, level="week", drop_level=False)
            if t in full.index.get_level_values("week")
            else full.iloc[0:0]
        )
        b = (
            cut.xs(t, level="week", drop_level=False)
            if t in cut.index.get_level_values("week")
            else cut.iloc[0:0]
        )
        a, b = a.align(b, join="outer")
        diff = ~((a == b) | (a.isna() & b.isna())).all(axis=1)
        if diff.any():
            bad = pd.concat({"full_history": a[diff], "cut_at_t": b[diff]}, axis=1)
            raise LookAheadError(f"features at {t.date()} use data published after it:\n{bad}")
