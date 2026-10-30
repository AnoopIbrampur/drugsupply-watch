"""Phase 3: summarize saved backtest scores (no retraining).

Reads data/derived/backtest_scores.parquet (from run_backtest.py); writes
data/derived/backtest_summary.csv and prints the report tables.
"""

import pandas as pd

from rxradar.model.metrics import onset_recall_at_lead, summarize
from rxradar.paths import derived_dir

scores = pd.read_parquet(derived_dir() / "backtest_scores.parquet")
ep = pd.read_parquet(derived_dir() / "family_episodes.parquet")
onsets = ep[~ep.start_censored & ~((ep.start == ep.end) & ~ep.ongoing)]
onsets = onsets.rename(columns={"key": "family"})[["family", "start"]]
MODELS = ["random", "rule", "logistic", "lightgbm"]


def fmt(v):
    if isinstance(v, tuple):
        return f"{v[0]:.3f} [{v[1]:.3f}-{v[2]:.3f}]"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


rows = []
for universe in ("all", "generic_or_injectable"):
    for model in MODELS:
        s = scores[scores.model == model]
        if universe != "all":
            s = s[s.injectable | s.multisource_generic]
        o = onsets[onsets.start.dt.year.isin(s.year.unique())]
        summ = summarize(s, o)
        summ.update(universe=universe, model=model)
        rows.append(summ)
summary = pd.DataFrame(rows)
summary.to_csv(derived_dir() / "backtest_summary.csv", index=False)

tables = {
    "Row level": ["rows", "positives", "base_rate", "pr_auc", "brier"],
    "Weekly precision@k (ceiling = all positives in top-k)": [
        "prec@25", "prec@25_ceiling", "prec@50", "prec@50_ceiling", "prec@100", "prec@100_ceiling",
    ],
    "Onset recall at fixed lead (headline)": [
        "recall@50_lead4w", "recall@50_lead8w", "recall@50_lead12w",
        "recall@100_lead4w", "recall@100_lead8w", "onsets_evaluable_lead4w",
        "onsets_evaluable_lead8w", "onsets_evaluable_lead12w",
    ],
    "Any-window onset recall (flawed, kept for transparency)": [
        "any_window_recall@50", "any_window_recall@100",
    ],
}  # fmt: skip
for title, cols in tables.items():
    print(f"\n## {title}")
    for _, r in summary.iterrows():
        print(f"{r.universe:22s} {r.model:9s} " + " | ".join(f"{c}={fmt(r[c])}" for c in cols))

print("\n## Onset recall@100 at 4-week lead, by test year (all families)")
for model in MODELS:
    s = scores[scores.model == model]
    per = {}
    for year, sy in s.groupby("year"):
        h = onset_recall_at_lead(sy, onsets[onsets.start.dt.year == year], 100, 4)
        per[int(year)] = f"{h.hit.mean():.2f}/{len(h)}" if len(h) else "-"
    print(f"{model:9s}", per)

print("\n## Calibration (pooled, all families): mean predicted vs observed by score bin")
for model in ("logistic", "lightgbm"):
    s = scores[scores.model == model]
    bins = pd.qcut(s.score.rank(method="first"), 10, labels=False)
    top = s.score >= s.score.quantile(0.999)
    cal = s.groupby(bins).agg(pred=("score", "mean"), obs=("y90", "mean"), n=("y90", "size"))
    print(model, "deciles:")
    print(cal.round(5).to_string())
    print(f"  top 0.1%: pred {s[top].score.mean():.4f} obs {s[top].y90.mean():.4f} n={top.sum()}")
    pred, obs = s.score.mean(), s.y90.mean()
    print(f"  overall: pred {pred:.5f} obs {obs:.5f}  ratio {pred / obs:.2f}")

print("\n## Score correlation between models (Spearman, pooled)")
wide = scores.pivot_table(index=["week", "family"], columns="model", values="score")
print(wide[MODELS].corr(method="spearman").round(2).to_string())
print("\nseed 7; 1000 bootstrap draws; weekly CIs resample 13-week blocks")
