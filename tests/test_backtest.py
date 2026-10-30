"""The walk-forward split must never train on labels unresolved at the cutoff."""

import pandas as pd

from rxradar.model.backtest import train_mask, usable


def frame():
    weeks = pd.date_range("2020-06-01", "2021-03-01", freq="W-MON")
    return pd.DataFrame(
        {
            "week": weeks,
            "known90": True,
            "known30": True,
            "status_known": True,
            "in_shortage_now": [i % 10 == 0 for i in range(len(weeks))],
        }
    )


def test_training_rows_have_resolved_labels():
    df = frame()
    cutoff = pd.Timestamp("2021-01-01")
    tr = df[train_mask(df, cutoff)]
    assert (tr.week + pd.Timedelta(days=90) < cutoff).all()
    assert tr.week.max() < pd.Timestamp("2020-10-03")


def test_rows_in_shortage_are_never_used():
    df = frame()
    assert not df[usable(df)].in_shortage_now.any()
