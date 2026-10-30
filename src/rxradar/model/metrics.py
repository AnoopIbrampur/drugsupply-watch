"""Evaluation metrics (docs/decisions.md D8).

Positive family-weeks are not independent: one onset labels ~13 consecutive
weeks at a 90-day horizon. So the headline is *event-level*:

- onset recall@k: share of onsets whose family ranked in the week's top-k at
  least once during the 90 days before the onset (excluding the onset week);
- lead time: days from the first such top-k week to the onset.

Row-level metrics are reported alongside: weekly precision@k (with its
ceiling, min(1, positives that week / k)), PR-AUC and Brier score. Confidence
intervals bootstrap over onsets (event metrics) or over weeks (weekly
precision).
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


def bootstrap_mean(values: np.ndarray, n: int = N_BOOT) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(RNG_SEED)
    boots = rng.choice(values, size=(n, len(values)), replace=True).mean(axis=1)
    return float(values.mean()), float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))


def summarize(
    scored: pd.DataFrame, onsets: pd.DataFrame, ks=(25, 50, 100), label: str = "y90"
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
        p, lo, hi = bootstrap_mean(wp.precision.values)
        out[f"prec@{k}"] = (p, lo, hi)
        out[f"prec@{k}_ceiling"] = float(wp.ceiling.mean()) if len(wp) else np.nan
        hits = onset_hits(s, onsets, k)
        r, rlo, rhi = bootstrap_mean(hits.hit.values)
        out[f"onset_recall@{k}"] = (r, rlo, rhi)
        out[f"onsets_evaluable@{k}"] = len(hits)
        out[f"median_lead_days@{k}"] = float(hits.lead_days.median()) if hits.hit.any() else np.nan
    return out
