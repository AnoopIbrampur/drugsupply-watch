"""Metrics on a toy ranking with hand-checkable answers."""

import numpy as np
import pandas as pd

from rxradar.model import metrics

WEEKS = pd.date_range("2021-01-04", periods=20, freq="W-MON")
ONSET = pd.Timestamp("2021-04-05")  # week index 13


def scored(good: bool):
    rows = []
    for w in WEEKS:
        for f in ["A", "B", "C", "D"]:
            y = f == "A" and ONSET - pd.Timedelta(days=90) <= w < ONSET
            score = {"A": 0.9 if good else 0.1, "B": 0.5, "C": 0.4, "D": 0.3}[f]
            rows.append((w, f, score, y))
    return pd.DataFrame(rows, columns=["week", "family", "score", "y90"])


ONSETS = pd.DataFrame({"family": ["A"], "start": [ONSET]})


def test_perfect_ranker_catches_the_onset_with_full_lead():
    hits = metrics.onset_hits(scored(True), ONSETS, k=1)
    assert hits.hit.all()
    assert hits.lead_days.iloc[0] == 84  # earliest scored week inside the 90-day window


def test_bad_ranker_misses_at_k1_but_not_at_k4():
    assert not metrics.onset_hits(scored(False), ONSETS, k=1).hit.any()
    assert metrics.onset_hits(scored(False), ONSETS, k=4).hit.all()


def test_weekly_precision_and_ceiling():
    wp = metrics.weekly_precision(scored(True), k=2)
    positive_weeks = wp[wp.ceiling > 0]
    assert np.allclose(positive_weeks.precision, 0.5)
    assert np.allclose(positive_weeks.ceiling, 0.5)


def test_summary_has_ci_ordering():
    s = metrics.summarize(scored(True), ONSETS, ks=(1,))
    p, lo, hi = s["prec@1"]
    assert lo <= p <= hi
    assert s["onset_recall@1"][0] == 1.0
