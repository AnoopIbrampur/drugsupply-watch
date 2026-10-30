"""Evaluation metrics (docs/decisions.md D8).

Positive family-weeks are not independent: one onset labels ~13 consecutive
weeks at a 90-day horizon. So the headline is *event-level*:

- onset recall@k at a fixed lead L (4, 8, 12 weeks): share of onsets whose
  family was in the top-k in the last scored week at least L weeks before
  the onset. This is the headline.
- "any-window" onset recall@k (top-k in *any* week of the 90 days before) is
  also reported, but only as a documented mistake: it rewards rankings that
  churn. A random ranking gets ~13 independent tries and beat every model on
  it in the first backtest (docs/decisions.md D8).

Row-level metrics are reported alongside: weekly precision@k (with its
ceiling, min(1, positives that week / k)), PR-AUC and Brier score. Confidence
intervals bootstrap over onsets (event metrics), or over 13-week blocks of
weeks (weekly precision), because neighbouring weeks are strongly correlated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss

RNG_SEED = 7
N_BOOT = 1000


def add_ranks(scored: pd.DataFrame) -> pd.DataFrame:
    """Rank within each week (1 = highest score); ties broken randomly but
    reproducibly so a constant-score model doesn't get credit from row order."""
    rng = np.random.default_rng(RNG_SEED)
    s = scored.assign(_tie=rng.random(len(scored)))
    s = s.sort_values(["week", "score", "_tie"], ascending=[True, False, False])
    s["rank"] = s.groupby("week").cumcount() + 1
    return s.drop(columns="_tie")


def weekly_precision(scored: pd.DataFrame, k: int, label: str = "y90") -> pd.DataFrame:
    s = scored if "rank" in scored else add_ranks(scored)
    top = s[s["rank"] <= k].groupby("week")[label].mean().rename("precision")
    pos = s.groupby("week")[label].sum()
    ceiling = (pos / k).clip(upper=1).rename("ceiling")
    return pd.concat([top, ceiling], axis=1).dropna()


def onset_hits(
    scored: pd.DataFrame, onsets: pd.DataFrame, k: int, horizon_days: int = 90
) -> pd.DataFrame:
    """Per onset: was the family in the top-k in any scored week in
    [start - horizon, start), and the lead time of the earliest such week.

    Only onsets with at least one scored week in that window are evaluable.
    """
    s = scored if "rank" in scored else add_ranks(scored)
    w = onsets[["family", "start"]].merge(s[["family", "week", "rank"]], on="family")
    w = w[(w.week < w.start) & (w.week >= w.start - pd.Timedelta(days=horizon_days))]
    g = w.assign(hit=w["rank"] <= k).groupby(["family", "start"])
    out = g.agg(
        weeks_scored=("week", "size"),
        hit=("hit", "any"),
        best_rank=("rank", "min"),
    ).reset_index()
    first_hit = w[w["rank"] <= k].groupby(["family", "start"]).week.min().rename("first_hit")
    out = out.merge(first_hit.reset_index(), on=["family", "start"], how="left")
    out["lead_days"] = (out.start - out.first_hit).dt.days
    return out


def onset_recall_at_lead(
    scored: pd.DataFrame, onsets: pd.DataFrame, k: int, lead_weeks: int
) -> pd.DataFrame:
    """Per onset: rank of the family in the last scored week at least
    ``lead_weeks`` before the onset (within the 90-day window), and whether
    that rank is <= k. Onsets without such a week are not evaluable."""
    s = scored if "rank" in scored else add_ranks(scored)
    w = onsets[["family", "start"]].merge(s[["family", "week", "rank"]], on="family")
    latest = w.start - pd.Timedelta(weeks=lead_weeks)
    w = w[(w.week <= latest) & (w.week >= w.start - pd.Timedelta(days=90))]
    w = w.sort_values("week").groupby(["family", "start"]).tail(1)
    return w.assign(hit=w["rank"] <= k)[["family", "start", "week", "rank", "hit"]]


def block_bootstrap_mean(
    values: pd.Series, block_weeks: int = 13, n: int = N_BOOT
) -> tuple[float, float, float]:
    """Mean of a weekly series with a CI from resampling contiguous blocks."""
    v = values.sort_index().to_numpy(dtype=float)
    if len(v) == 0:
        return (np.nan, np.nan, np.nan)
    blocks = [v[i : i + block_weeks] for i in range(0, len(v), block_weeks)]
    rng = np.random.default_rng(RNG_SEED)
    idx = rng.integers(0, len(blocks), size=(n, len(blocks)))
    boots = np.array([np.concatenate([blocks[j] for j in row]).mean() for row in idx])
    return float(v.mean()), float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))


def bootstrap_mean(values: np.ndarray, n: int = N_BOOT) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(RNG_SEED)
    boots = rng.choice(values, size=(n, len(values)), replace=True).mean(axis=1)
    return float(values.mean()), float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))


def summarize(
    scored: pd.DataFrame,
    onsets: pd.DataFrame,
    ks=(25, 50, 100),
    lead_weeks=(4, 8, 12),
    label: str = "y90",
) -> dict:
    s = add_ranks(scored)
    out = {
        "rows": len(s),
        "positives": int(s[label].sum()),
        "base_rate": float(s[label].mean()),
        "pr_auc": float(average_precision_score(s[label], s.score)) if s[label].any() else np.nan,
    }
    if s.score.between(0, 1).all():
        out["brier"] = float(brier_score_loss(s[label], s.score))
    for k in ks:
        wp = weekly_precision(s, k, label)
        out[f"prec@{k}"] = block_bootstrap_mean(wp.precision)
        out[f"prec@{k}_ceiling"] = float(wp.ceiling.mean()) if len(wp) else np.nan
        for lead in lead_weeks:
            hits = onset_recall_at_lead(s, onsets, k, lead)
            out[f"recall@{k}_lead{lead}w"] = bootstrap_mean(hits.hit.values)
            out[f"onsets_evaluable_lead{lead}w"] = len(hits)
        any_window = onset_hits(s, onsets, k)
        out[f"any_window_recall@{k}"] = bootstrap_mean(any_window.hit.values)
    return out
