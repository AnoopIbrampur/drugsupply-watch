"""Factory-quality features: FDA inspections and compliance actions.

Why: a failed inspection or warning letter at the plant that makes a drug is a
direct supply risk, and it's the signal RxRadar has been missing.

Sources (each row carries ``published``, see decisions D10):
- ``inspections``: fei, code (NAI/VAI/OAI), country. FMD145 date + 30 days, else
  inspection end + that outcome's 90th-percentile delay.
- ``actions``: fei, action type. Action date + 30 days.
- ``factories``: family, set_id, version, fei, one row per factory per label
  version, ``published`` = DailyMed publication date. A version listing no factory
  has fei = NaN, so it replaces the older version instead of keeping stale factories.

At week *t* a family's factories are those on the latest version of each of its
labels published by *t*. Features are NaN for families with no linked factory:
"unknown" must not read as "clean record".
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from rxradar.features.common import NS, count_in_window, days_since_last, family_grid

LOOKBACK_DAYS = 3 * 365
FMD_LAG_DAYS = 30
ACTION_LAG_DAYS = 30
# 90th-percentile delay from inspection end to FMD-145, by outcome (D10).
UNDATED_LAG_DAYS = {"OAI": 877, "VAI": 292, "NAI": 197}
QUALITY_PROJECT = "Drug Quality Assurance"
FEATURES = [
    "insp_n_factories",
    "insp_n_inspected",
    "insp_oai_3y",
    "insp_actions_3y",
    "insp_share_bad_3y",
    "insp_days_since_bad",
    "insp_foreign_share",
]


def normalize_fei(fei) -> str:
    return str(fei).strip().lstrip("0")


def inspection_source(rows: list[dict]) -> pd.DataFrame:
    """Drug-quality inspections with a D10 publication date."""
    df = pd.DataFrame(rows).drop_duplicates()
    df = df[df.ProjectArea == QUALITY_PROJECT]
    end = pd.to_datetime(df.InspectionEndDate)
    fmd = pd.to_datetime(df.FMD145Date, utc=True, errors="coerce").dt.tz_localize(None)
    lag = pd.to_timedelta(df.ClassificationCode.map(UNDATED_LAG_DAYS), unit="D")
    published = (fmd.dt.normalize() + pd.Timedelta(days=FMD_LAG_DAYS)).fillna(end + lag)
    return pd.DataFrame(
        {
            "published": published.astype(NS),
            "fei": df.FEINumber.map(normalize_fei),
            "code": df.ClassificationCode,
            "country": df.CountryCode,
            "inspection_id": df.InspectionID,
        }
    ).drop_duplicates(["inspection_id", "code"])


def action_source(rows: list[dict]) -> pd.DataFrame:
    """Warning letters, injunctions and seizures, dated per D10."""
    df = pd.DataFrame(rows)
    taken = pd.to_datetime(df.ActionTakenDate) + pd.Timedelta(days=ACTION_LAG_DAYS)
    return pd.DataFrame(
        {
            "published": taken.astype(NS),
            "fei": df.FEINumber.map(normalize_fei),
            "action": df.ActionType,
        }
    )


def _factories_at_weeks(factories: pd.DataFrame, weeks: pd.DatetimeIndex) -> pd.DataFrame:
    """(week, family, fei) from each label's latest version published by week."""
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    con.register("f", factories)
    con.register("w", pd.DataFrame({"week": pd.DatetimeIndex(weeks).astype(NS)}))
    out = con.execute(
        """
        WITH versions AS (SELECT DISTINCT family, set_id, version, published FROM f),
        current AS (
            SELECT w.week, v.family, v.set_id, max(v.version) AS version
            FROM w JOIN versions v ON v.published <= w.week
            GROUP BY ALL
        )
        SELECT DISTINCT c.week, c.family, f.fei
        FROM current c JOIN f USING (family, set_id, version)
        WHERE f.fei IS NOT NULL
        """
    ).df()
    out["week"] = out.week.astype(NS)
    return out


def build(sources: dict[str, pd.DataFrame], weeks: pd.DatetimeIndex) -> pd.DataFrame:
    grid = family_grid(sources, weeks)
    factories = sources["factories"]
    if grid.empty or factories.empty:
        return grid.assign(**{c: np.nan for c in FEATURES})
    links = _factories_at_weeks(factories, weeks)
    if links.empty:
        return grid.assign(**{c: np.nan for c in FEATURES})

    insp, actions = sources["inspections"], sources["actions"]
    fw = links[["week", "fei"]].drop_duplicates().sort_values(["fei", "week"])
    fw = fw.reset_index(drop=True)
    oai = insp[insp.code == "OAI"]
    fw["inspected"] = count_in_window(fw, insp, None, by="fei") > 0
    fw["oai"] = count_in_window(fw, oai, LOOKBACK_DAYS, by="fei") > 0
    fw["action"] = count_in_window(fw, actions, LOOKBACK_DAYS, by="fei") > 0
    bad = pd.concat([oai[["published", "fei"]], actions[["published", "fei"]]])
    fw["days_bad"] = days_since_last(fw, bad, by="fei")
    # A factory's country only counts once an inspection of it is public.
    known = insp.sort_values("published").drop_duplicates("fei").set_index("fei").country
    foreign = fw.fei.map(known).map(lambda c: np.nan if pd.isna(c) else float(c != "US"))
    fw["foreign"] = foreign.where(fw.inspected)

    joined = links.merge(fw, on=["week", "fei"])
    joined["bad"] = joined.oai | joined.action
    agg = joined.groupby(["week", "family"]).agg(
        insp_n_factories=("fei", "nunique"),
        insp_n_inspected=("inspected", "sum"),
        insp_oai_3y=("oai", "sum"),
        insp_actions_3y=("action", "sum"),
        insp_share_bad_3y=("bad", "mean"),
        insp_days_since_bad=("days_bad", "min"),
        insp_foreign_share=("foreign", "mean"),
    )
    return grid.merge(agg.reset_index(), on=["week", "family"], how="left")
