"""Walk-forward backtest: retrain each Jan 1, score every week of that year.

Leakage guard: a training row for week t is only used if its 90-day label
was already resolved at the cutoff (t + 90 days < cutoff). Without this, the
newest training rows would carry outcomes from after the cutoff.

Models, simplest first:
- ``random``: uniform scores (the base rate).
- ``rule``: hand-written ranking, no training: recent own shortage, then
  injectable, then few labelers, then sibling shortages.
- ``logistic``: L2 logistic regression on ~10 transformed features.
- ``lightgbm``: gradient boosting on all features. Hyperparameters are fixed
  in advance and *not* tuned on test years: with ~270 onsets, tuning would
  quietly overfit the evaluation.
"""

from __future__ import annotations

import logging

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from rxradar.model.dataset import feature_columns

log = logging.getLogger(__name__)

TEST_YEARS = (2017, 2020, 2021, 2022, 2023, 2024, 2026)
HORIZON_DAYS = 90
LABEL = "y90"

LGB_PARAMS = dict(
    objective="binary",
    learning_rate=0.03,
    n_estimators=400,
    num_leaves=15,
    min_child_samples=500,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    random_state=7,
    verbose=-1,
)

LOGISTIC_FEATURES = [
    "hist_onsets_3y", "hist_onsets_all", "hist_days_since_listed", "hist_siblings_short_now",
    "hist_disc_notices_1y", "sup_n_labelers", "sup_exits", "rec_family_1y", "rec_labelers_1y",
    "px_chg_1y", "injectable", "multisource_generic",
]  # fmt: skip


def usable(df: pd.DataFrame, label: str = LABEL) -> pd.Series:
    known = "known90" if label == "y90" else "known30"
    return df[known] & df.status_known & ~df.in_shortage_now


def train_mask(df: pd.DataFrame, cutoff: pd.Timestamp, horizon_days: int = HORIZON_DAYS):
    return usable(df) & (df.week + pd.Timedelta(days=horizon_days) < cutoff)


# Tested against the current model, chosen before seeing results (decisions D11).
INSPECTION_LOGISTIC_FEATURES = ["insp_oai_3y", "insp_actions_3y", "insp_foreign_share"]


def _logistic_matrix(df: pd.DataFrame, medians: pd.Series | None = None, inspections: bool = False):
    extra = INSPECTION_LOGISTIC_FEATURES if inspections else []
    x = df[LOGISTIC_FEATURES + extra].astype(float).copy()
    for c in x.columns:
        if c.startswith(("hist_onsets", "hist_disc", "hist_siblings", "sup_", "rec_")):
            x[c] = np.log1p(x[c].clip(lower=0))
    x["hist_days_since_listed"] = np.log1p(x.hist_days_since_listed)
    x["never_listed"] = df.hist_days_since_listed.isna().astype(float)
    x["no_price"] = df.px_chg_1y.isna().astype(float)
    if inspections:
        x["insp_oai_3y"] = np.log1p(x.insp_oai_3y)
        x["insp_actions_3y"] = np.log1p(x.insp_actions_3y)
        x["no_factory_link"] = df.insp_n_factories.isna().astype(float)
    if medians is None:
        medians = x.median()
    return x.fillna(medians).fillna(0), medians


def run_logistic(df: pd.DataFrame, inspections: bool, years=TEST_YEARS) -> pd.DataFrame:
    """Logistic-only walk-forward run, with or without inspection features."""
    out = []
    for year in years:
        cutoff = pd.Timestamp(f"{year}-01-01")
        tr = df[train_mask(df, cutoff)]
        te = df[usable(df) & (df.week.dt.year == year)]
        if te.empty or tr[LABEL].sum() < 20:
            continue
        xtr, med = _logistic_matrix(tr, inspections=inspections)
        xte, _ = _logistic_matrix(te, med, inspections=inspections)
        lr = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
        lr.fit(xtr, tr[LABEL])
        base = te[["week", "family", LABEL, "injectable", "multisource_generic"]].assign(year=year)
        out.append(base.assign(score=lr.predict_proba(xte)[:, 1]))
    return pd.concat(out, ignore_index=True)


def rule_score(df: pd.DataFrame) -> np.ndarray:
    recent = (df.hist_onsets_3y > 0).astype(float)
    few = (df.sup_n_labelers.fillna(99) <= 2).astype(float)
    return (
        8 * recent
        + 4 * df.injectable.astype(float)
        + 2 * few
        + 1 * (df.hist_siblings_short_now > 0).astype(float)
        - 0.001 * df.sup_n_labelers.fillna(0)
    ).values


def run_backtest(df: pd.DataFrame, years=TEST_YEARS) -> pd.DataFrame:
    """Return scored test rows: week, family, y90, subset tags, year, model, score."""
    feats = feature_columns(df)
    rng = np.random.default_rng(7)
    out = []
    for year in years:
        cutoff = pd.Timestamp(f"{year}-01-01")
        tr = df[train_mask(df, cutoff)]
        te = df[usable(df) & (df.week.dt.year == year)]
        if te.empty or tr[LABEL].sum() < 20:
            log.warning("skip %s: %s test rows, %s train positives", year, len(te), tr[LABEL].sum())
            continue
        log.info("%s: train %s rows (%s pos), test %s rows (%s pos)",
                 year, len(tr), int(tr[LABEL].sum()), len(te), int(te[LABEL].sum()))  # fmt: skip
        base = te[["week", "family", LABEL, "injectable", "multisource_generic"]].assign(year=year)

        out.append(base.assign(model="random", score=rng.random(len(te))))
        out.append(base.assign(model="rule", score=rule_score(te)))

        xtr, med = _logistic_matrix(tr)
        xte, _ = _logistic_matrix(te, med)
        lr = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
        lr.fit(xtr, tr[LABEL])
        out.append(base.assign(model="logistic", score=lr.predict_proba(xte)[:, 1]))

        gbm = lgb.LGBMClassifier(**LGB_PARAMS)
        gbm.fit(tr[feats], tr[LABEL], categorical_feature=["form_group"])
        out.append(base.assign(model="lightgbm", score=gbm.predict_proba(te[feats])[:, 1]))
    return pd.concat(out, ignore_index=True)


def fit_final(df: pd.DataFrame, cutoff: pd.Timestamp) -> lgb.LGBMClassifier:
    """LightGBM trained on everything resolved by ``cutoff`` (for importances)."""
    tr = df[train_mask(df, cutoff)]
    gbm = lgb.LGBMClassifier(**LGB_PARAMS)
    gbm.fit(tr[feature_columns(df)], tr[LABEL], categorical_feature=["form_group"])
    return gbm


def run_lightgbm(df: pd.DataFrame, feats: list[str], years=TEST_YEARS) -> pd.DataFrame:
    """LightGBM-only walk-forward run on a feature subset (for ablations)."""
    out = []
    cats = ["form_group"] if "form_group" in feats else []
    for year in years:
        cutoff = pd.Timestamp(f"{year}-01-01")
        tr = df[train_mask(df, cutoff)]
        te = df[usable(df) & (df.week.dt.year == year)]
        if te.empty or tr[LABEL].sum() < 20:
            continue
        gbm = lgb.LGBMClassifier(**LGB_PARAMS)
        gbm.fit(tr[feats], tr[LABEL], categorical_feature=cats)
        base = te[["week", "family", LABEL, "injectable", "multisource_generic"]].assign(year=year)
        out.append(base.assign(score=gbm.predict_proba(te[feats])[:, 1]))
    return pd.concat(out, ignore_index=True)
