"""Phase 3b: does inspection data improve the model? Decision rule in decisions D11.

Prints paired onset recall with and without inspection features.
"""

import pandas as pd

from rxradar.features.inspections import FEATURES
from rxradar.model.backtest import run_lightgbm, run_logistic
from rxradar.model.dataset import feature_columns, load_dataset
from rxradar.model.metrics import bootstrap_mean, onset_recall_at_lead
from rxradar.paths import derived_dir

df = load_dataset()
ep = pd.read_parquet(derived_dir() / "family_episodes.parquet")
onsets = ep[~ep.start_censored & ~((ep.start == ep.end) & ~ep.ongoing)]
onsets = onsets.rename(columns={"key": "family"})[["family", "start"]]

linked = df[df.insp_n_factories.notna()][["week", "family"]].drop_duplicates()
base = feature_columns(df)
runs = {
    "logistic": (run_logistic(df, False), run_logistic(df, True)),
    "lightgbm": (run_lightgbm(df, base), run_lightgbm(df, base + FEATURES)),
}


def compare(without, with_, k, only_linked=False):
    o = onsets[onsets.start.dt.year.isin(without.year.unique())]
    a = onset_recall_at_lead(without, o, k, 4)
    b = onset_recall_at_lead(with_, o, k, 4)
    m = a.merge(b, on=["family", "start"], suffixes=("_a", "_b"))
    if only_linked:
        m = m.merge(linked, left_on=["week_a", "family"], right_on=["week", "family"])
    diff = m.hit_b.astype(float) - m.hit_a.astype(float)
    return m, bootstrap_mean(diff.to_numpy())


for name, (without, with_) in runs.items():
    for k in (100, 50):
        for only_linked in (False, True):
            m, (d, lo, hi) = compare(without, with_, k, only_linked)
            print(f"{name:9s} k={k:<3d} linked_only={only_linked!s:5s} n={len(m):3d} "
                  f"without={m.hit_a.mean():.3f} with={m.hit_b.mean():.3f} "
                  f"diff={d:+.3f} [{lo:+.3f}, {hi:+.3f}]")  # fmt: skip
