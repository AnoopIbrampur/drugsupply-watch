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

KEEP = ["LABELERNAME", "MARKETINGCATEGORYNAME", "APPLICATIONNUMBER", "PRODUCTNDC", "family"]


def snapshot_files() -> list[tuple[pd.Timestamp, object]]:
    files = [
        (pd.Timestamp(p.parent.name[:8]), p) for p in sorted(snapshot_root().glob("*/product.txt"))
    ]
    own = data_root() / "raw" / "fda_ndc_text"
    for day in sorted(own.glob("*/product.txt")):
        files.append((pd.Timestamp(day.parent.name), day))
    return sorted(files, key=lambda x: x[0])


def build_snapshot_table() -> pd.DataFrame:
    resolver = MoietyResolver(*load_tables(latest_snapshot_dir("openfda_substance")))
    parts = []
    for date, path in snapshot_files():
        df = read_tsv(path)
        df = df[df.PRODUCTTYPENAME == "HUMAN PRESCRIPTION DRUG"]
        fam = product_families(df, resolver).dropna(subset=["family"])
        parts.append(fam.reindex(columns=KEEP).assign(published=date))
    out = pd.concat(parts, ignore_index=True)
    out["published"] = out.published.astype(NS)
    out.to_parquet(derived_dir() / "ndc_snapshots.parquet")
    return out
