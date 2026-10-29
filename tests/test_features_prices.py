"""NADAC price features: NDC normalization, changes, staleness, no look-ahead."""

import numpy as np
import pandas as pd

from rxradar.features import prices
from rxradar.features.pit import check_point_in_time

F = "X|TABLET|ORAL"
WEEKS = pd.date_range("2019-01-07", periods=220, freq="W-MON")


def test_ndc11():
    assert prices.ndc11("0409-4888-01") == "00409488801"
    assert prices.ndc11("12345-678-90") == "12345067890"
    assert prices.ndc11("12345-6789-1") == "12345678901"
    assert prices.ndc11("bad") is None


def price_rows():
    # Price halves over the first 2 years, then jumps 3x; survey stops after.
    dates = pd.date_range("2019-01-03", periods=200, freq="7D")
    p = np.where(np.arange(200) < 150, np.linspace(1.0, 0.5, 200)[:200], 1.5)
    df = pd.DataFrame({"family": F, "published": dates, "price": p, "n_ndcs": 3})
    return df[df.published < "2022-10-01"]


def sources():
    return {"prices": price_rows()}


def test_changes_and_jump():
    out = prices.build(sources(), WEEKS).set_index("week")
    t = pd.Timestamp("2021-06-07")
    assert out.loc[t, "px_has_price"]
    assert out.loc[t, "px_chg_1y"] < 0  # erosion
    after = pd.Timestamp("2022-01-17")
    assert out.loc[after, "px_max_jump_13w"] > np.log(2)


def test_stale_price_is_treated_as_unpriced():
    out = prices.build(sources(), WEEKS).set_index("week")
    late = out.loc[pd.Timestamp("2023-02-06")]
    assert not late.px_has_price and np.isnan(late.px_chg_1y)


def test_no_look_ahead():
    check_point_in_time(prices.build, sources(), WEEKS, WEEKS[::15])
