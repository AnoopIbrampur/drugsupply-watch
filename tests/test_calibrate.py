"""Calibration: maps are monotone, recover a known miscalibration, serialize."""

import numpy as np
import pandas as pd

from rxradar.model.calibrate import Calibrator, evaluate_walk_forward


def overconfident(n=200_000, seed=0):
    rng = np.random.default_rng(seed)
    true_p = rng.beta(0.5, 150, n)  # rare events, mean ~0.3%
    raw = np.clip(true_p * 5, 0, 0.99)  # model claims 5x the true rate
    y = rng.random(n) < true_p
    return raw, y


def test_platt_fixes_a_constant_overconfidence():
    raw, y = overconfident()
    cal = Calibrator("platt").fit(raw, y)
    p = cal.predict(raw)
    assert abs(p.mean() - y.mean()) < 0.2 * y.mean()
    assert raw.mean() > 3 * y.mean()


def test_maps_are_monotone():
    raw, y = overconfident()
    grid = np.linspace(0.0001, 0.5, 200)
    for method in ("platt", "isotonic"):
        out = Calibrator(method).fit(raw, y).predict(grid)
        assert np.all(np.diff(out) >= -1e-12), method


def test_to_dict_roundtrip_platt():
    raw, y = overconfident()
    cal = Calibrator("platt").fit(raw, y)
    d = cal.to_dict()
    logit = np.log(raw[:5] / (1 - raw[:5]))
    manual = 1 / (1 + np.exp(-(d["coef"] * logit + d["intercept"])))
    assert np.allclose(manual, cal.predict(raw[:5]))


def test_walk_forward_only_uses_earlier_years():
    raw, y = overconfident(30_000)
    years = np.repeat([2020, 2021, 2022], 10_000)
    scores = pd.DataFrame(
        {"week": pd.Timestamp("2020-01-06"), "family": "F", "score": raw, "y90": y, "year": years}
    )
    out = evaluate_walk_forward(scores)
    assert set(out.year) == {2021, 2022}  # first year has no past to fit on
