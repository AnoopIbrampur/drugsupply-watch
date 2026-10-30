"""Phase 3: run the walk-forward backtest and print the results tables.

Writes data/derived/backtest_scores.parquet and data/derived/backtest_summary.csv.
"""

import logging

import pandas as pd

from rxradar.model.backtest import fit_final, run_backtest
from rxradar.model.dataset import feature_columns, load_dataset
from rxradar.model.metrics import onset_hits, summarize
from rxradar.paths import derived_dir

logging.basicConfig(level=logging.INFO, format="%(message)s")

df = load_dataset()
scores = run_backtest(df)
scores.to_parquet(derived_dir() / "backtest_scores.parquet")

ep = pd.read_parquet(derived_dir() / "family_episodes.parquet")
onsets = ep[~ep.start_censored & ~((ep.start == ep.end) & ~ep.ongoing)]
onsets = onsets.rename(columns={"key": "family"})[["family", "start"]]

rows = []
for universe in ("all", "generic_or_injectable"):
    for model, s in scores.groupby("model"):
        if universe != "all":
            s = s[s.injectable | s.multisource_generic]
        o = onsets[onsets.start.dt.year.isin(s.year.unique())]
        summ = summarize(s, o)
        summ.update(universe=universe, model=model)
        rows.append(summ)
summary = pd.DataFrame(rows)
summary.to_csv(derived_dir() / "backtest_summary.csv", index=False)


def fmt(v):
    if isinstance(v, tuple):
        return f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"
    return f"{v:.4f}" if isinstance(v, float) else str(v)


cols = ["universe", "model", "rows", "positives", "base_rate", "pr_auc", "brier",
        "prec@25", "prec@25_ceiling", "prec@50", "prec@50_ceiling", "prec@100",
        "onset_recall@25", "onset_recall@50", "onset_recall@100", "onsets_evaluable@50",
        "median_lead_days@50"]  # fmt: skip
for _, r in summary.iterrows():
    print(" | ".join(f"{c}={fmt(r[c])}" for c in cols if c in r))

print("\nonset recall@50 by test year (all families):")
for model, s in scores.groupby("model"):
    per = {}
    for year, sy in s.groupby("year"):
        o = onsets[onsets.start.dt.year == year]
        h = onset_hits(sy, o, 50)
        per[year] = f"{h.hit.mean():.2f} (n={len(h)})" if len(h) else "-"
    print(f"  {model:9s}", per)

gbm = fit_final(df, pd.Timestamp("2024-01-01"))
imp = pd.Series(gbm.booster_.feature_importance("gain"), index=feature_columns(df))
print("\nLightGBM gain importance (trained to 2024-01-01), top 15:")
print((imp / imp.sum()).sort_values(ascending=False).head(15).round(3).to_string())
