"""Assemble the modelling table: weekly panel + all point-in-time features.

Rows come from the panel (data/derived/panel.parquet); each feature table is
left-joined on (week, family). A family-week with no row in a feature table
had no data published yet for that source. Counts become 0, and recency or
price features stay NaN (LightGBM handles NaN; the linear baseline imputes).
"""

from __future__ import annotations

import pandas as pd

from rxradar.features.common import NS
from rxradar.paths import derived_dir

FEATURE_TABLES = ["features_history", "features_market", "features_recalls", "features_prices"]
ZERO_IF_MISSING = (
    "hist_current_captures", "hist_onsets_all", "hist_onsets_3y", "hist_disc_notices_1y",
    "hist_siblings_short_now", "hist_sibling_onsets_1y", "rec_family_1y", "rec_family_90d",
    "rec_family_class1_1y", "px_n_ndcs",
)  # fmt: skip


def load_dataset() -> pd.DataFrame:
    df = pd.read_parquet(derived_dir() / "panel.parquet")
    df["week"] = df.week.astype(NS)
    for name in FEATURE_TABLES:
        f = pd.read_parquet(derived_dir() / f"{name}.parquet")
        f["week"] = f.week.astype(NS)
        df = df.merge(f, on=["week", "family"], how="left")
    for c in ZERO_IF_MISSING:
        df[c] = df[c].fillna(0)
    df["px_has_price"] = df.px_has_price.fillna(False).astype(bool)
    df["sup_has_brand"] = df.sup_has_brand.fillna(False).astype(bool)
    # Market-wide stress is the same for every family in a week.
    for c in ("mkt_families_short_now", "mkt_onsets_13w"):
        df[c] = df.groupby("week")[c].transform("max")
    form = df.family.str.split("|").str[1]
    df["form_group"] = form.astype("category")
    df["n_moieties"] = df.family.str.split("|").str[0].str.count(r"\+") + 1
    return df


def feature_columns(df: pd.DataFrame) -> list[str]:
    prefixes = ("hist_", "sup_", "rec_", "px_", "mkt_")
    cols = [c for c in df.columns if c.startswith(prefixes)]
    return cols + ["injectable", "multisource_generic", "form_group", "n_moieties"]
