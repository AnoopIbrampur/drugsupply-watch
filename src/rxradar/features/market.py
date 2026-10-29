"""Supply-structure features from point-in-time NDC snapshots.

Why: shortages concentrate in drugs with few manufacturers, and manufacturer
exits often come before them.

Source ``ndc``: one row per Rx product per NDC snapshot, ``published`` = the
snapshot date (transform/ndc_snapshots.py). At week *t* we use the latest
snapshot published on or before *t*, which is exactly what FDA's directory
showed then. Reconstructing the past from today's files is not used: it
misses about a third of 2016 families (scripts/audit_ndc_backfill.py).

Features (prefix ``sup_``): labelers, generic labelers, products, applications,
brand present, top-labeler product share, HHI over product counts, and
labeler exits/entries between the previous snapshot and the current one.
``sup_snapshot_age_days`` says how stale the snapshot is (snapshots are 1-22
months apart). Values are NaN before the first snapshot (2016-06).

Caveat: concentration uses product counts, not market share (no public sales
volumes).
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from rxradar.features.common import NS, asof_capture, family_grid

GENERIC = ("ANDA", "NDA AUTHORIZED GENERIC")


def snapshot_stats(ndc: pd.DataFrame) -> pd.DataFrame:
    """Per (snapshot date, family) supply statistics, with exits/entries
    relative to the previous snapshot."""
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    con.register("n", ndc.assign(generic=ndc.MARKETINGCATEGORYNAME.isin(GENERIC)))
    stats = con.execute(
        """
        WITH lab AS (
            SELECT published, family, LABELERNAME AS labeler, count(*) AS k,
                   bool_or(generic) AS generic
            FROM n GROUP BY ALL
        ),
        snaps AS (
            SELECT published, lag(published) OVER (ORDER BY published) AS prev
            FROM (SELECT DISTINCT published FROM n)
        ),
        base AS (
            SELECT published, family,
                   count(*) AS n_labelers,
                   count(*) FILTER (WHERE generic) AS n_generic_labelers,
                   sum(k) AS n_products,
                   max(k) * 1.0 / sum(k) AS top_share,
                   sum(k * k) * 1.0 / (sum(k) * sum(k)) AS hhi
            FROM lab GROUP BY ALL
        ),
        apps AS (
            SELECT published, family, count(DISTINCT APPLICATIONNUMBER) AS n_applications,
                   bool_or(MARKETINGCATEGORYNAME = 'NDA') AS has_brand
            FROM n GROUP BY ALL
        ),
        exits AS (
            SELECT s.published, p.family, count(*) AS exits
            FROM snaps s JOIN lab p ON p.published = s.prev
            LEFT JOIN lab c ON c.published = s.published
                           AND c.family = p.family AND c.labeler = p.labeler
            WHERE c.labeler IS NULL GROUP BY ALL
        ),
        entries AS (
            SELECT s.published, c.family, count(*) AS entries
            FROM snaps s JOIN lab c ON c.published = s.published
            LEFT JOIN lab p ON p.published = s.prev
                           AND p.family = c.family AND p.labeler = c.labeler
            WHERE s.prev IS NOT NULL AND p.labeler IS NULL GROUP BY ALL
        )
        SELECT base.*, apps.n_applications, apps.has_brand,
               coalesce(exits.exits, 0) AS exits, coalesce(entries.entries, 0) AS entries
        FROM base JOIN apps USING (published, family)
        LEFT JOIN exits USING (published, family)
        LEFT JOIN entries USING (published, family)
        """
    ).df()
    # A family absent from the current snapshot but present in the previous
    # one has exited completely: keep that as a row with zero labelers.
    gone = con.execute(
        """
        WITH snaps AS (
            SELECT published, lag(published) OVER (ORDER BY published) AS prev
            FROM (SELECT DISTINCT published FROM n)
        )
        SELECT s.published, p.family, count(DISTINCT p.LABELERNAME) AS exits
        FROM snaps s JOIN n p ON p.published = s.prev
        WHERE NOT EXISTS (SELECT 1 FROM n c WHERE c.published = s.published AND c.family = p.family)
        GROUP BY ALL
        """
    ).df()
    stats = pd.concat([stats, gone], ignore_index=True)
    stats["published"] = stats.published.astype(NS)
    return stats


def build(sources: dict[str, pd.DataFrame], weeks: pd.DatetimeIndex) -> pd.DataFrame:
    ndc = sources["ndc"]
    grid = family_grid(sources, weeks)
    if ndc.empty or grid.empty:
        return grid
    stats = snapshot_stats(ndc)
    snaps = np.sort(stats.published.unique())
    grid["snapshot"] = asof_capture(grid.week, snaps)
    out = grid.merge(
        stats.rename(columns={"published": "snapshot"}), on=["snapshot", "family"], how="left"
    )
    has_snap = out.snapshot.notna()
    counts = [
        "n_labelers",
        "n_generic_labelers",
        "n_products",
        "n_applications",
        "exits",
        "entries",
    ]
    out.loc[has_snap, counts] = out.loc[has_snap, counts].fillna(0)
    out["has_brand"] = out.has_brand.astype("boolean").fillna(False) & has_snap
    out["snapshot_age_days"] = (out.week - out.snapshot).dt.days
    out = out.drop(columns="snapshot")
    return out.rename(columns={c: f"sup_{c}" for c in out.columns if c not in ("week", "family")})


def ndc_source() -> pd.DataFrame:
    from rxradar.paths import derived_dir

    return pd.read_parquet(derived_dir() / "ndc_snapshots.parquet")
