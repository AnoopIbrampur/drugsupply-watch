"""Production model: the backtested logistic regression, Platt-calibrated,
with exact per-feature explanations.

Why logistic and not LightGBM: in the walk-forward backtest they tie (CIs
overlap everywhere; logistic is slightly ahead on PR-AUC and recall), and
the logistic score is a sum of per-feature terms, so explanations are exact
rather than approximated (docs/model_card.md).

Calibration: Platt scaling fitted on the backtest's out-of-sample scores.
The production model trains on more data than any backtest model did, so
this is an approximation; the model card says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from rxradar.model.backtest import LABEL, _logistic_matrix, train_mask
from rxradar.model.calibrate import Calibrator

BASE_RATE_NOTE = (
    "A typical drug family has about a 0.25% chance of a new FDA-listed shortage in any 90 days."
)
# Bands in multiples of the backtest base rate (0.25% per 90 days).
BASE_RATE = 0.0025
BANDS = [(8.0, "high"), (4.0, "elevated"), (2.0, "moderate"), (0.0, "typical")]


def band(p: float) -> str:
    return next(name for mult, name in BANDS if p >= mult * BASE_RATE)


@dataclass
class ProductionModel:
    columns: list[str]
    medians: dict[str, float]
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float
    calibration: dict
    trained_through: str

    # ---- fitting -------------------------------------------------------
    @classmethod
    def fit(cls, df: pd.DataFrame, cutoff: pd.Timestamp, oos_scores: pd.DataFrame):
        tr = df[train_mask(df, cutoff)]
        x, med = _logistic_matrix(tr)
        scaler = StandardScaler().fit(x)
        lr = LogisticRegression(C=1.0, max_iter=2000).fit(scaler.transform(x), tr[LABEL])
        cal = Calibrator("platt").fit(oos_scores.score.values, oos_scores[LABEL].values)
        return cls(
            columns=list(x.columns),
            medians={k: float(v) for k, v in med.items()},
            mean=scaler.mean_,
            scale=scaler.scale_,
            coef=lr.coef_[0],
            intercept=float(lr.intercept_[0]),
            calibration=cal.to_dict(),
            trained_through=str(cutoff.date()),
        )

    # ---- scoring -------------------------------------------------------
    def contributions(self, df: pd.DataFrame) -> pd.DataFrame:
        x, _ = _logistic_matrix(df, pd.Series(self.medians))
        z = (x[self.columns].values - self.mean) / self.scale
        return pd.DataFrame(z * self.coef, columns=self.columns, index=df.index)

    def raw_score(self, df: pd.DataFrame) -> np.ndarray:
        logit = self.contributions(df).sum(axis=1).values + self.intercept
        return 1 / (1 + np.exp(-logit))

    def probability(self, df: pd.DataFrame) -> np.ndarray:
        raw = np.clip(self.raw_score(df), 1e-6, 1 - 1e-6)
        c = self.calibration
        logit = np.log(raw / (1 - raw))
        return 1 / (1 + np.exp(-(c["coef"] * logit + c["intercept"])))

    # ---- persistence ---------------------------------------------------
    def save(self, path: Path) -> None:
        d = {
            "columns": self.columns,
            "medians": self.medians,
            "mean": self.mean.tolist(),
            "scale": self.scale.tolist(),
            "coef": self.coef.tolist(),
            "intercept": self.intercept,
            "calibration": self.calibration,
            "trained_through": self.trained_through,
        }
        path.write_text(json.dumps(d, indent=1))

    @classmethod
    def load(cls, path: Path) -> ProductionModel:
        d = json.loads(path.read_text())
        return cls(
            columns=d["columns"],
            medians=d["medians"],
            mean=np.array(d["mean"]),
            scale=np.array(d["scale"]),
            coef=np.array(d["coef"]),
            intercept=d["intercept"],
            calibration=d["calibration"],
            trained_through=d["trained_through"],
        )


# ---- plain-English reasons ----------------------------------------------


def _plural(n: float, one: str, many: str) -> str:
    return one if int(n) == 1 else many


def reason_text(feature: str, row: pd.Series) -> str | None:
    """Sentence describing why a feature raised this family's risk, from the
    raw (untransformed) value. None for features with no plain reading."""
    v = row.get(feature)
    if feature == "hist_onsets_3y" and v > 0:
        return f"{int(v)} new FDA-listed {_plural(v, 'shortage', 'shortages')} in the past 3 years"
    if feature == "hist_onsets_all" and v > 0:
        return f"{int(v)} FDA-listed {_plural(v, 'shortage', 'shortages')} since 2014"
    if feature == "hist_days_since_listed" and pd.notna(v):
        weeks = int(v // 7)
        return f"last on FDA's shortage list {weeks} {_plural(weeks, 'week', 'weeks')} ago"
    if feature == "hist_siblings_short_now" and v > 0:
        return (
            f"{int(v)} other {_plural(v, 'form', 'forms')} of the same ingredient "
            f"{_plural(v, 'is', 'are')} in shortage now"
        )
    if feature == "hist_disc_notices_1y" and v > 0:
        return f"{int(v)} discontinuation {_plural(v, 'notice', 'notices')} in the past year"
    if feature == "sup_n_labelers" and pd.notna(v):
        who = _plural(v, "company lists", "companies list")
        return f"only {int(v)} {who} it in FDA's NDC directory"
    if feature == "sup_exits" and v > 0:
        return (
            f"{int(v)} {_plural(v, 'company', 'companies')} stopped listing it "
            "since the previous NDC directory snapshot"
        )
    if feature == "rec_family_1y" and v > 0:
        return f"{int(v)} {_plural(v, 'recall', 'recalls')} in the past year"
    if feature == "rec_labelers_1y" and v > 0:
        recalls = _plural(v, "recall", "recalls")
        return f"its makers had {int(v)} {recalls} (any product) in the past year"
    if feature == "px_chg_1y" and pd.notna(v):
        pct = (np.exp(v) - 1) * 100
        direction = "down" if pct < 0 else "up"
        return f"retail acquisition price {direction} {abs(pct):.0f}% over the past year"
    if feature == "injectable" and v:
        return "sterile injectable (the form most often in shortage)"
    if feature == "multisource_generic" and v:
        return "multi-source generic"
    return None


# True of thousands of drugs at once, so they're shown as context, not as reasons.
DRUG_TYPE_FEATURES = ("injectable", "multisource_generic")


def type_context(row: pd.Series) -> str | None:
    if row.get("injectable"):
        return "Sterile injectables are in shortage more often than other drugs."
    return None


def top_reasons(contrib: pd.Series, row: pd.Series, n: int = 3) -> list[str]:
    """Up to n sentences for this drug's own signals that raised risk the most.
    May be empty: then its risk reflects its drug type, and the site says so."""
    out = []
    for feature, c in contrib.sort_values(ascending=False).items():
        if c <= 0 or len(out) >= n:
            break
        if feature in DRUG_TYPE_FEATURES:
            continue
        text = reason_text(feature, row)
        if text:
            out.append(text)
    return out
