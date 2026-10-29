"""Phase 3 check: do today's NDC files reconstruct the past honestly?

Supply features count labelers per family at week t from *today's* NDC files
(current + delisted) using self-reported marketing dates. If labelers back-fill
start dates, or delisted products get purged, that reconstruction differs from
what FDA actually published at t.

The Wayback Machine holds yearly copies of FDA's ndctext.zip (2016-2026). For
a few of them we compute labelers per family from the archived file itself
(the true point-in-time view) and compare with the reconstruction for the
same date.
"""

import io
import sys
import zipfile

import numpy as np
import pandas as pd

from rxradar.http import PoliteClient
from rxradar.ingest.fda_registry import read_tsv
from rxradar.ingest.ndc_archive import snapshot_root
from rxradar.paths import cache_dir, derived_dir, latest_snapshot_dir
from rxradar.transform.families import MoietyResolver, product_families
from rxradar.transform.substances import load_tables

CAPTURES = ["20160609215020", "20190618190437", "20220409231310"]
URL = "https://web.archive.org/web/{}id_/https://www.accessdata.fda.gov/cder/ndctext.zip"


def archived_products(client: PoliteClient, ts: str) -> pd.DataFrame:
    out = snapshot_root() / ts
    out.mkdir(exist_ok=True)
    path = out / "product.txt"
    if not path.exists():
        blob = client.get_bytes(URL.format(ts))
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith("product.txt"))
            path.write_bytes(zf.read(name))
    return read_tsv(path)


def main(captures=CAPTURES) -> None:
    client = PoliteClient(min_interval=3.0, cache_dir=cache_dir() / "wayback")
    resolver = MoietyResolver(*load_tables(latest_snapshot_dir("openfda_substance")))
    today = pd.read_parquet(derived_dir() / "product_families.parquet").dropna(subset=["family"])
    today["start"] = pd.to_datetime(today.STARTMARKETINGDATE, format="%Y%m%d", errors="coerce")
    today["end"] = pd.to_datetime(today.ENDMARKETINGDATE, format="%Y%m%d", errors="coerce")
    rows = []
    for ts in captures:
        d = pd.Timestamp(ts[:8])
        old = archived_products(client, ts)
        old = old[old.PRODUCTTYPENAME == "HUMAN PRESCRIPTION DRUG"]
        old = product_families(old, resolver).dropna(subset=["family"])
        truth = old.groupby("family").LABELERNAME.nunique().rename("published")
        active = today[(today.start <= d) & (today.end.isna() | (today.end > d))]
        recon = active.groupby("family").LABELERNAME.nunique().rename("reconstructed")
        both = pd.concat([truth, recon], axis=1).fillna(0)
        diff = both.reconstructed - both.published
        single_pub, single_rec = both.published == 1, both.reconstructed == 1
        rows.append(
            {
                "date": d.date(),
                "families_published": int((both.published > 0).sum()),
                "families_reconstructed": int((both.reconstructed > 0).sum()),
                "spearman": round(both.corr(method="spearman").iloc[0, 1], 3),
                "exact_match": f"{(diff == 0).mean():.0%}",
                "recon_more": f"{(diff > 0).mean():.0%}",
                "recon_fewer": f"{(diff < 0).mean():.0%}",
                "mean_abs_diff": round(np.abs(diff).mean(), 2),
                "single_source_agree": f"{(single_pub == single_rec).mean():.0%}",
            }
        )
        print(rows[-1], flush=True)
    pd.DataFrame(rows).to_csv(derived_dir() / "audit_ndc_backfill.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1:] or CAPTURES)
