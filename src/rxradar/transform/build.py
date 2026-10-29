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
from rxradar.paths import derived_dir, latest_snapshot_dir
from rxradar.transform.families import MoietyResolver, build_product_family_table
from rxradar.transform.listing_names import ListingNameMapper, openfda_name_families
from rxradar.transform.panel import build_panel
from rxradar.transform.substances import load_tables

log = logging.getLogger(__name__)


def build_listings() -> pd.DataFrame:
    arch = latest_snapshot_dir("fda_shortage_archive")
    lst = pd.DataFrame(load_listings(arch))
    lst.to_parquet(arch / "_listings.parquet")
    return lst


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


def build_all() -> None:
    listings = build_listings()
    log.info("listings: %s rows from %s captures", len(listings), listings.capture_ts.nunique())
    products = build_product_family_table()
    log.info("product families: %s products, %s families", len(products), products.family.nunique())
    names = build_listing_name_families(products, listings)
    log.info("listing names mapped: %.1f%%", 100 * names.family.notna().mean())
    build_panel()
