"""Do fewer known plants go with more new shortages? The rule is fixed in decisions D13.

Prints a Mantel-Haenszel rate ratio, few plants against many, within strata of drug
type and company count, with a 95% CI from resampling families.
"""

import numpy as np
import pandas as pd

from rxradar.model.backtest import TEST_YEARS
from rxradar.model.dataset import load_dataset

N_BOOT = 1000
df = load_dataset()
df = df[
    df.week.dt.year.isin(TEST_YEARS)
    & df.status_known
    & df.known90
    & ~df.in_shortage_now
    & (df.insp_n_factories >= 1)
].copy()
companies = pd.cut(df.sup_n_labelers.fillna(0), [-1, 1, 3, np.inf], labels=["1", "2-3", "4+"])
df["stratum"] = df.injectable.astype(str) + "/" + companies.astype(str)
df["y"] = df.y90.astype(float)


def rate_ratio(d: pd.DataFrame, few: pd.Series) -> float:
    g = d.assign(few=few).groupby(["stratum", "few"], observed=True).y.agg(["sum", "size"])
    table = g.unstack("few").dropna()
    n1, n0 = table[("size", True)], table[("size", False)]
    a, b = table[("sum", True)], table[("sum", False)]
    n = n1 + n0
    return float((a * n0 / n).sum() / (b * n1 / n).sum())


def crude(d: pd.DataFrame, few: pd.Series) -> float:
    return float(d.y[few].mean() / d.y[~few].mean())


def by_family_bootstrap(cut: int) -> tuple[float, float, float]:
    """Resample whole families, since a family's weeks aren't independent."""
    groups = {k: v[["stratum", "y", "insp_n_factories"]] for k, v in df.groupby("family")}
    keys = np.array(list(groups))
    rng = np.random.default_rng(13)
    stats = []
    for _ in range(N_BOOT):
        d = pd.concat([groups[k] for k in rng.choice(keys, len(keys))], ignore_index=True)
        stats.append(rate_ratio(d, d.insp_n_factories <= cut))
    estimate = rate_ratio(df, df.insp_n_factories <= cut)
    return estimate, float(np.quantile(stats, 0.025)), float(np.quantile(stats, 0.975))


print(f"family-weeks {len(df):,}, families {df.family.nunique():,}, shortages {int(df.y.sum()):,}")
for cut, label in ((2, "PRIMARY 1-2 vs 3+"), (1, "secondary 1 vs 2+")):
    few = df.insp_n_factories <= cut
    print(f"{label}: few weeks {few.sum():,} rate {df.y[few].mean():.4f}; "
          f"many weeks {(~few).sum():,} rate {df.y[~few].mean():.4f}; "
          f"crude RR {crude(df, few):.2f}")  # fmt: skip
    rr, lo, hi = by_family_bootstrap(cut)
    print(f"  Mantel-Haenszel RR {rr:.2f} [{lo:.2f}, {hi:.2f}]")
