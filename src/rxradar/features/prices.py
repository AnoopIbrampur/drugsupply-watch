"""Price features from Medicaid NADAC (weekly retail acquisition cost).

Why: sustained price erosion pushes generic makers out of a market, and
sudden price jumps often accompany supply stress.

Source ``prices``: one row per (family, NADAC ``as_of_date``) with the median
per-unit price across the family's surveyed NDCs, ``published`` =
``as_of_date``. Features at week *t* use the latest row published on or before
*t*, compared with rows published by t-1y and t-3y.

Coverage caveat: NADAC surveys retail pharmacies. Only ~24% of injectable
shortage families have any price (docs/data_audit.md §5e), so
``px_has_price`` is itself a feature and the other ``px_`` values are NaN
when unpriced. The family median also moves when the NDC mix changes;
``px_n_ndcs`` exposes that.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from rxradar.features.common import NS, family_grid

NADAC_COLUMNS = [
    "description", "ndc", "price", "effective_date", "unit", "pharmacy_type", "otc",
    "explanation", "classification", "generic_price", "generic_effective", "as_of",
]  # fmt: skip


def ndc11(package_code: str) -> str | None:
    """FDA package code (4-4-2, 5-3-2 or 5-4-1) -> 11-digit 5-4-2 NDC."""
    parts = package_code.split("-")
    if len(parts) != 3:
        return None
    a, b, c = parts
    return a.zfill(5) + b.zfill(4) + c.zfill(2)


def price_source(csv_paths: list[str], package_family: pd.DataFrame) -> pd.DataFrame:
    """Family-level weekly median price from yearly NADAC files.

    ``package_family``: columns ndc11, family.
    """
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    con.register("pkg", package_family.drop_duplicates("ndc11"))
    files = ", ".join(f"'{p}'" for p in csv_paths)
    names = ", ".join(f"'{c}'" for c in NADAC_COLUMNS)
    df = con.execute(
        f"""
        WITH raw AS (
            SELECT ndc, TRY_CAST(price AS DOUBLE) AS price,
                   strptime(as_of, '%m/%d/%Y')::DATE AS as_of
            FROM read_csv([{files}], header = true, names = [{names}], all_varchar = true)
        )
        SELECT pkg.family, raw.as_of AS published,
               median(raw.price) AS price, count(DISTINCT raw.ndc) AS n_ndcs
        FROM raw JOIN pkg ON pkg.ndc11 = lpad(raw.ndc, 11, '0')
        WHERE raw.price > 0
        GROUP BY ALL
        """
    ).df()
    df["published"] = pd.to_datetime(df.published).astype(NS)
    return df.sort_values(["family", "published"]).reset_index(drop=True)


def _asof(left: pd.DataFrame, right: pd.DataFrame, on_left: str, cols: list[str]) -> pd.DataFrame:
    l2 = left[["row", "family", on_left]].sort_values(on_left)
    r2 = right[["family", "published", *cols]].sort_values("published")
    m = pd.merge_asof(l2, r2, left_on=on_left, right_on="published", by="family")
    return m.set_index("row")[cols]


def build(sources: dict[str, pd.DataFrame], weeks: pd.DatetimeIndex) -> pd.DataFrame:
    px = sources["prices"]
    grid = family_grid(sources, weeks)
    cols = ["px_has_price", "px_log_price", "px_chg_1y", "px_chg_3y", "px_max_jump_13w",
            "px_n_ndcs", "px_ndc_change_1y"]  # fmt: skip
    if grid.empty:
        return grid
    if px.empty:
        grid["px_has_price"] = False
        for c in cols[1:]:
            grid[c] = np.nan
        return grid
    px = px.astype({"published": NS}).sort_values(["family", "published"]).copy()
    px["logp"] = np.log(px.price)
    # Largest week-over-week log jump within the trailing 13 published weeks.
    px["jump"] = px.groupby("family").logp.diff()
    px["max_jump_13w"] = px.groupby("family").jump.transform(
        lambda s: s.rolling(13, min_periods=1).max()
    )
    g = grid[["week", "family"]].assign(row=np.arange(len(grid)))
    g["week"] = g.week.astype(NS)
    g["w1y"] = g.week - pd.Timedelta(days=365)
    g["w3y"] = g.week - pd.Timedelta(days=3 * 365)
    now = _asof(
        g, px.assign(pub_now=px.published), "week", ["logp", "n_ndcs", "max_jump_13w", "pub_now"]
    )
    y1 = _asof(g, px, "w1y", ["logp", "n_ndcs"])
    y3 = _asof(g, px, "w3y", ["logp"])
    now, y1, y3 = (x.reindex(range(len(grid))) for x in (now, y1, y3))
    # A price older than 8 weeks at t means the family dropped out of the survey.
    fresh = (g.week.values - now.pub_now.values) <= np.timedelta64(56, "D")
    grid["px_has_price"] = fresh
    grid["px_log_price"] = np.where(fresh, now.logp, np.nan)
    grid["px_chg_1y"] = np.where(fresh, now.logp - y1.logp, np.nan)
    grid["px_chg_3y"] = np.where(fresh, now.logp - y3.logp, np.nan)
    grid["px_max_jump_13w"] = np.where(fresh, now.max_jump_13w, np.nan)
    grid["px_n_ndcs"] = np.where(fresh, now.n_ndcs, 0)
    grid["px_ndc_change_1y"] = np.where(fresh, now.n_ndcs - y1.n_ndcs.fillna(0), np.nan)
    return grid


def package_family_map() -> pd.DataFrame:
    """ndc11 -> family from current and delisted NDC packages."""
    from rxradar.ingest.fda_registry import read_tsv
    from rxradar.paths import derived_dir, latest_snapshot_dir

    d = latest_snapshot_dir("fda_ndc_text")
    pkg = pd.concat(
        [read_tsv(d / "package.txt"), read_tsv(d / "Packages_excluded.txt")], ignore_index=True
    )[["PRODUCTNDC", "NDCPACKAGECODE"]]
    prod = pd.read_parquet(derived_dir() / "product_families.parquet").dropna(subset=["family"])
    m = pkg.merge(prod[["PRODUCTNDC", "family"]].drop_duplicates("PRODUCTNDC"), on="PRODUCTNDC")
    m["ndc11"] = m.NDCPACKAGECODE.map(ndc11)
    return m.dropna(subset=["ndc11"])[["ndc11", "family"]]
