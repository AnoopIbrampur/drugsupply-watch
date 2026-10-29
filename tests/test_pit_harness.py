"""The look-ahead harness must pass honest builders and catch leaky ones."""

import pandas as pd
import pytest

from rxradar.features.pit import LookAheadError, check_point_in_time

WEEKS = pd.date_range("2020-01-06", periods=20, freq="W-MON")
RECALLS = pd.DataFrame(
    {
        "family": ["A", "A", "B", "A"],
        "initiated": pd.to_datetime(["2020-01-01", "2020-02-01", "2020-02-10", "2020-03-01"]),
        # Recalls are published weeks after initiation (median lag 47 days).
        "published": pd.to_datetime(["2020-02-15", "2020-03-20", "2020-03-01", "2020-04-10"]),
    }
)


def grid():
    return pd.MultiIndex.from_product([WEEKS, ["A", "B"]], names=["week", "family"]).to_frame(
        index=False
    )


def recalls_known_by_t(sources, weeks):
    """Honest: counts recalls *published* by week t."""
    r = sources["recalls"]
    g = grid()
    g = g[g.week.isin(weeks)]
    pairs = zip(g.week, g.family, strict=True)
    g["n_recalls"] = [int(((r.family == f) & (r.published <= w)).sum()) for w, f in pairs]
    return g


def recalls_by_initiation(sources, weeks):
    """Leaky: counts recalls *initiated* by t, including ones not yet public."""
    r = sources["recalls"]
    g = grid()
    g = g[g.week.isin(weeks)]
    pairs = zip(g.week, g.family, strict=True)
    g["n_recalls"] = [int(((r.family == f) & (r.initiated <= w)).sum()) for w, f in pairs]
    return g


def test_honest_builder_passes():
    check_point_in_time(recalls_known_by_t, {"recalls": RECALLS}, WEEKS, WEEKS)


def test_initiation_date_leak_is_caught():
    # Family A's first recall was initiated 2020-01-01 but published 02-15.
    with pytest.raises(LookAheadError, match="2020-01-06"):
        check_point_in_time(recalls_by_initiation, {"recalls": RECALLS}, WEEKS, WEEKS)


def test_sources_must_declare_publication_date():
    with pytest.raises(ValueError, match="published"):
        check_point_in_time(
            recalls_known_by_t, {"recalls": RECALLS.drop(columns="published")}, WEEKS, WEEKS[:1]
        )
