"""The weekly (family, week) table that features and models build on.

Rows: every family with at least one Rx product marketed at week *t*,
judged from NDC marketing start/end dates (current and delisted products).
Columns: D7 tags, ``in_shortage_now``, labels ``y30``/``y90``, and their
``known`` flags (see transform/labels.py).

Point-in-time caveat, documented in docs/data_audit.md: marketing dates are
self-reported, and a product listed in 2020 may carry a 2015 start date. The
universe for 2015 can therefore include products FDA's directory didn't show
in 2015. That affects which rows exist, not labels. Phase 3 manufacturer-count
features need a stricter treatment.
"""

from __future__ import annotations

import logging

import duckdb
import pandas as pd

from rxradar.paths import derived_dir
from rxradar.transform.labels import label_rows, week_coverage, week_grid
from rxradar.transform.shortage_episodes import build_episodes

log = logging.getLogger(__name__)

GENERIC_CATEGORIES = ("ANDA", "NDA AUTHORIZED GENERIC")


def family_universe(products: pd.DataFrame, weeks: pd.DatetimeIndex) -> pd.DataFrame:
    """(week, family) rows with labeler counts for families marketed that week."""
    p = products.dropna(subset=["family"]).copy()
    p["start"] = pd.to_datetime(p.STARTMARKETINGDATE, format="%Y%m%d", errors="coerce")
    p["end"] = pd.to_datetime(p.ENDMARKETINGDATE, format="%Y%m%d", errors="coerce")
    p = p.dropna(subset=["start"])
    p["generic"] = p.MARKETINGCATEGORYNAME.isin(GENERIC_CATEGORIES)
    p = p[["family", "LABELERNAME", "generic", "start", "end"]]
    w = pd.DataFrame({"week": weeks})
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    con.register("p", p)
    con.register("w", w)
    return con.execute(
        """
        SELECT w.week, p.family,
               count(DISTINCT p.LABELERNAME) AS n_labelers,
               count(DISTINCT p.LABELERNAME) FILTER (WHERE p.generic) AS n_generic_labelers
        FROM w JOIN p ON p.start <= w.week AND (p.end IS NULL OR p.end > w.week)
        GROUP BY 1, 2
        """
    ).df()


def build_panel() -> pd.DataFrame:
    lst = pd.read_parquet(derived_dir() / "listings.parquet")
    names = pd.read_parquet(derived_dir() / "listing_name_families.parquet")
    name_to_family = dict(zip(names.name, names.family, strict=True))
    products = pd.read_parquet(derived_dir() / "product_families.parquet")

    episodes = build_episodes(lst, key_fn=name_to_family.get)
    episodes.to_parquet(derived_dir() / "family_episodes.parquet")

    lst = lst.assign(
        capture_date=pd.to_datetime(lst.capture_ts.str[:8], format="%Y%m%d"),
        family=lst.name.map(name_to_family),
    )
    current = lst.loc[(lst.status == "Current") & lst.family.notna(), ["capture_date", "family"]]
    captures = lst.capture_date.drop_duplicates()
    weeks = week_grid(captures)

    rows = family_universe(products, weeks)
    # Families on the shortage list but with no marketed product that week
    # (e.g. every NDC delisted) still belong in the table: a shortage of a
    # drug nobody lists is still a shortage.
    extra = current.assign(week=current.capture_date + pd.offsets.Week(weekday=0))
    extra = extra.loc[extra.week.isin(weeks), ["week", "family"]].drop_duplicates()
    rows = rows.merge(extra, on=["week", "family"], how="outer")
    rows[["n_labelers", "n_generic_labelers"]] = (
        rows[["n_labelers", "n_generic_labelers"]].fillna(0).astype(int)
    )
    rows["injectable"] = rows.family.str.split("|").str[1].str.startswith("INJECTION")
    rows["multisource_generic"] = rows.n_generic_labelers >= 2

    panel = label_rows(rows, week_coverage(weeks, captures), current, episodes)
    panel.to_parquet(derived_dir() / "panel.parquet")
    log.info(
        "panel: %s rows, %s families, %s weeks", len(panel), panel.family.nunique(), len(weeks)
    )
    return panel
