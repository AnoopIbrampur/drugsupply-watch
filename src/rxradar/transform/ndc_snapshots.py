"""Family-keyed rows for every point-in-time NDC snapshot.

Output ``data/derived/ndc_snapshots.parquet``: one row per Rx product per
snapshot, with ``published`` = the snapshot's capture date (archived copies)
or fetch date (our own downloads). This is the ``ndc`` source for supply
features (features/market.py).
"""

from __future__ import annotations

import pandas as pd

from rxradar.features.common import NS
from rxradar.ingest.fda_registry import read_tsv
from rxradar.ingest.ndc_archive import snapshot_root
from rxradar.paths import data_root, derived_dir, latest_snapshot_dir
from rxradar.transform.families import MoietyResolver, product_families
from rxradar.transform.substances import load_tables

MIN_SNAPSHOT_GAP_DAYS = 7
KEEP = ["LABELERNAME", "MARKETINGCATEGORYNAME", "APPLICATIONNUMBER", "PRODUCTNDC", "family"]


def snapshot_files() -> list[tuple[pd.Timestamp, object]]:
    files = [
        (pd.Timestamp(p.parent.name[:8]), p) for p in sorted(snapshot_root().glob("*/product.txt"))
    ]
    own = data_root() / "raw" / "fda_ndc_text"
    for day in sorted(own.glob("*/product.txt")):
        files.append((pd.Timestamp(day.parent.name), day))
    return sorted(files, key=lambda x: x[0])


def build_snapshot_table(incremental: bool = True) -> pd.DataFrame:
    """Build (or extend) the table. With ``incremental``, snapshot dates
    already in the saved table are kept as they are and only new snapshot
    files are parsed. The nightly job relies on this: the 25 archived raw
    files (~900 MB) aren't on the runner, only the saved table is."""
    target = derived_dir() / "ndc_snapshots.parquet"
    existing = pd.read_parquet(target) if incremental and target.exists() else None
    done = set(existing.published.dt.normalize()) if existing is not None else set()
    resolver = MoietyResolver(*load_tables(latest_snapshot_dir("openfda_substance")))
    parts = [] if existing is None else [existing]
    last = max(done) if done else None
    for date, path in snapshot_files():
        if date.normalize() in done:
            continue
        # Our own daily NDC downloads are added at most weekly: daily rows
        # would grow the saved state ~0.4 MB/day for no modelling gain.
        if last is not None and date - last < pd.Timedelta(days=MIN_SNAPSHOT_GAP_DAYS):
            continue
        last = date
        df = read_tsv(path)
        df = df[df.PRODUCTTYPENAME == "HUMAN PRESCRIPTION DRUG"]
        fam = product_families(df, resolver).dropna(subset=["family"])
        parts.append(fam.reindex(columns=KEEP).assign(published=date))
    out = pd.concat(parts, ignore_index=True)
    out["published"] = out.published.astype(NS)
    out.to_parquet(target)
    return out
