"""Rebuild every derived table from raw snapshots, in dependency order.

raw snapshots -> listings parquet -> product families -> listing-name
families -> family episodes + weekly panel. Each step reads only the latest
raw snapshots and earlier steps' outputs, so a rebuild is reproducible.
"""

from __future__ import annotations

import logging

import pandas as pd

from rxradar.ingest.fda_shortage_archive import load_listings
from rxradar.ingest.openfda import load_results
from rxradar.paths import data_root, derived_dir, latest_snapshot_dir
from rxradar.transform.families import MoietyResolver, build_product_family_table
from rxradar.transform.listing_names import ListingNameMapper, openfda_name_families
from rxradar.transform.panel import build_panel
from rxradar.transform.substances import load_tables

log = logging.getLogger(__name__)


def build_listings() -> pd.DataFrame:
    """One table of FDA shortage-list captures (data/derived/listings.parquet):

    - Internet Archive captures of FDA's page (history to 2014);
    - our own captures of the page (local runs; FDA blocks cloud servers);
    - daily openFDA shortage exports, kept incrementally in
      data/derived/openfda_listings.parquet and used only on days that have no
      page capture, so a day never gets two slightly different lists.
    """
    rows = load_listings(latest_snapshot_dir("fda_shortage_archive"))
    own_root = data_root() / "raw" / "fda_shortage_page"
    for day in sorted(own_root.glob("*")) if own_root.exists() else []:
        rows.extend(load_listings(day))
    page = pd.DataFrame(rows)
    ofda = update_openfda_listings()
    page_days = set(page.capture_ts.str[:8])
    ofda = ofda[~ofda.capture_ts.str[:8].isin(page_days)]
    lst = pd.concat([page, ofda], ignore_index=True)
    lst = lst.drop_duplicates(["capture_ts", "name", "status"], keep="last")
    lst.to_parquet(derived_dir() / "listings.parquet")
    return lst


def update_openfda_listings() -> pd.DataFrame:
    """Append any openFDA shortage snapshots not yet in the saved table."""
    from rxradar.ingest.openfda import shortage_listing_rows

    path = derived_dir() / "openfda_listings.parquet"
    saved = (
        pd.read_parquet(path)
        if path.exists()
        else pd.DataFrame(columns=["capture_ts", "name", "status"])
    )
    have = set(saved.capture_ts)
    root = data_root() / "raw" / "openfda_shortages"
    new = []
    for snap in sorted(root.glob("*")) if root.exists() else []:
        if (snap / "manifest.json").exists():
            rows = shortage_listing_rows(snap)
            if rows and rows[0]["capture_ts"] not in have:
                new.extend(rows)
                have.add(rows[0]["capture_ts"])
    out = pd.concat([saved, pd.DataFrame(new)], ignore_index=True) if new else saved
    out.to_parquet(path)
    return out


def build_listing_name_families(products: pd.DataFrame, listings: pd.DataFrame) -> pd.DataFrame:
    resolver = MoietyResolver(*load_tables(latest_snapshot_dir("openfda_substance")))
    openfda = openfda_name_families(
        load_results(latest_snapshot_dir("openfda_shortages")), products
    )
    mapper = ListingNameMapper(resolver, products.family.dropna().unique().tolist(), openfda)
    out = pd.DataFrame({"name": listings.name.unique()})
    mapped = out.name.map(mapper.map)
    out["family"], out["method"] = mapped.str[0], mapped.str[1]
    out.to_parquet(derived_dir() / "listing_name_families.parquet")
    return out


def build_all(features: bool = True) -> None:
    listings = build_listings()
    log.info("listings: %s rows from %s captures", len(listings), listings.capture_ts.nunique())
    ndc_dir = latest_snapshot_dir("fda_ndc_text")
    complete = ndc_dir is not None and all(
        (ndc_dir / f).exists() for f in ("product.txt", "Products_excluded.txt")
    )
    if complete:
        products = build_product_family_table()
    else:  # NDC download blocked/failed on this runner: reuse the saved table
        log.warning("no NDC text snapshot; reusing saved product_families.parquet")
        products = pd.read_parquet(derived_dir() / "product_families.parquet")
    log.info("product families: %s products, %s families", len(products), products.family.nunique())
    names = build_listing_name_families(products, listings)
    log.info("listing names mapped: %.1f%%", 100 * names.family.notna().mean())
    build_panel()
    if features:
        from rxradar.features.build import build_features

        build_features()
