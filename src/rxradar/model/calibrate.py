"""Probability calibration for the production model.

Why: the raw logistic scores are badly overconfident at the top (top 0.1%:
predicted 0.30, observed 0.019; docs/model_card.md). The site shows a
probability, so it has to mean what it says.

Method: fit a monotone map from raw score to observed rate on *out-of-sample*
scores only, i.e. backtest predictions from years before the one being
calibrated. Two candidates:
- Platt: logistic regression on logit(score); smooth, two parameters.
- isotonic: step function; flexible, but jumpy with ~2k positives.
``evaluate_walk_forward`` picks between them by Brier score on later years.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


class Calibrator:
    def __init__(self, method: str) -> None:
        if method not in ("platt", "isotonic", "none"):
            raise ValueError(method)
        self.method = method

    def fit(self, score: np.ndarray, y: np.ndarray) -> Calibrator:
        if self.method == "platt":
            self.model = LogisticRegression(C=1e6, max_iter=1000)
            self.model.fit(_logit(score).reshape(-1, 1), y)
        elif self.method == "isotonic":
            self.model = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")
            self.model.fit(score, y)
        return self

    def predict(self, score: np.ndarray) -> np.ndarray:
        if self.method == "platt":
            return self.model.predict_proba(_logit(score).reshape(-1, 1))[:, 1]
        if self.method == "isotonic":
            return self.model.predict(score)
        return np.asarray(score)

    def to_dict(self) -> dict:
        """Serializable parameters (for the site build and the nightly job)."""
        if self.method == "platt":
            return {
                "method": "platt",
                "coef": float(self.model.coef_[0, 0]),
                "intercept": float(self.model.intercept_[0]),
            }
        if self.method == "isotonic":
            return {
                "method": "isotonic",
                "x": self.model.X_thresholds_.tolist(),
                "y": self.model.y_thresholds_.tolist(),
            }
        return {"method": "none"}


def reliability(p: np.ndarray, y: np.ndarray, bins=(0, 0.002, 0.005, 0.01, 0.02, 0.05, 1.0)):
    """Mean predicted vs observed rate in fixed probability bins."""
    df = pd.DataFrame({"p": p, "y": y})
    df["bin"] = pd.cut(df.p, bins=list(bins), include_lowest=True)
    return df.groupby("bin", observed=False).agg(
        pred=("p", "mean"), obs=("y", "mean"), n=("y", "size")
    )


def evaluate_walk_forward(scores: pd.DataFrame, label: str = "y90") -> pd.DataFrame:
    """For each test year after the first, fit calibrators on all earlier
    test years' out-of-sample scores and score that year. Returns per-row
    calibrated probabilities for each method."""
    out = []
    years = sorted(scores.year.unique())
    for year in years[1:]:
        past = scores[scores.year < year]
        cur = scores[scores.year == year]
        row = cur[["week", "family", label, "year"]].copy()
        for method in ("none", "platt", "isotonic"):
            cal = Calibrator(method).fit(past.score.values, past[label].values)
            row[method] = cal.predict(cur.score.values)
        out.append(row)
    return pd.concat(out, ignore_index=True)


def compare(cal_rows: pd.DataFrame, label: str = "y90") -> pd.DataFrame:
    y = cal_rows[label].values
    res = []
    for method in ("none", "platt", "isotonic"):
        p = cal_rows[method].values
        top = p >= np.quantile(p, 0.999)
        res.append(
            {
                "method": method,
                "brier": brier_score_loss(y, p),
                "log_loss": log_loss(y, np.clip(p, EPS, 1 - EPS)),
                "mean_pred": p.mean(),
                "mean_obs": y.mean(),
                "top0.1%_pred": p[top].mean(),
                "top0.1%_obs": y[top].mean(),
            }
        )
    return pd.DataFrame(res)
