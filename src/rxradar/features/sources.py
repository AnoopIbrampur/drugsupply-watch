"""Load the real source tables for feature builders, each with ``published``.

What ``published`` means per source, i.e. the earliest date the row was public:
- listings: the archive capture date.
- products: the NDC marketing start date (product starts) or end date (exit
  events). These are self-reported and may be backfilled; scripts/audit_ndc_backfill.py
  measures how much.
- recalls: openFDA enforcement ``report_date`` (not recall initiation).
- prices: NADAC ``as_of_date``.
"""

from __future__ import annotations

import pandas as pd

from rxradar.features.common import NS
from rxradar.features.history import listings_source
from rxradar.paths import derived_dir


def load_listings() -> pd.DataFrame:
    lst = pd.read_parquet(derived_dir() / "listings.parquet")
    names = pd.read_parquet(derived_dir() / "listing_name_families.parquet")
    return listings_source(lst, dict(zip(names.name, names.family, strict=True)))


def load_products() -> pd.DataFrame:
    p = pd.read_parquet(derived_dir() / "product_families.parquet").dropna(subset=["family"])
    p = p.assign(
        start=pd.to_datetime(p.STARTMARKETINGDATE, format="%Y%m%d", errors="coerce").astype(NS),
        end=pd.to_datetime(p.ENDMARKETINGDATE, format="%Y%m%d", errors="coerce").astype(NS),
    ).dropna(subset=["start"])
    # A product row becomes "published" when it starts marketing; its end is
    # a separate fact that only counts from its own date (see market.py).
    return p.assign(published=p.start)
