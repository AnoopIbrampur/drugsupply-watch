"""Build every feature table from the derived tables, in order.

NDC snapshot table -> history, supply, recall, price and inspection features,
each saved as data/derived/features_<name>.parquet. The NADAC aggregation is the slow
step (~5 min over 16M rows), so its family-weekly price table is reused when
present unless ``rebuild_prices`` is set.
"""

from __future__ import annotations

import glob
import json
import logging

import pandas as pd

from rxradar.features import history, inspections, market, prices, recalls
from rxradar.features.sources import load_listings, load_products
from rxradar.ingest.openfda import load_results
from rxradar.paths import data_root, derived_dir, latest_snapshot_dir
from rxradar.transform.ndc_snapshots import build_snapshot_table

log = logging.getLogger(__name__)


def inspection_sources() -> dict[str, pd.DataFrame] | None:
    """Inspection, action and factory tables, or None until all three exist."""
    links = derived_dir() / "label_factories.parquet"
    dash = sorted((data_root() / "raw" / "fda_dashboard").glob("*"))
    if not links.exists() or not dash:
        return None
    insp = json.loads((dash[-1] / "inspections_classifications.json").read_text())
    actions = json.loads((dash[-1] / "compliance_actions.json").read_text())
    return {
        "factories": pd.read_parquet(links),
        "inspections": inspections.inspection_source(insp),
        "actions": inspections.action_source(actions),
    }


def build_features(rebuild_prices: bool = False) -> None:
    out = derived_dir()
    weeks = pd.DatetimeIndex(
        pd.read_parquet(out / "panel.parquet", columns=["week"]).week.unique()
    ).sort_values()
    products = load_products()
    ndc = build_snapshot_table()
    rec = recalls.recall_source(
        load_results(latest_snapshot_dir("openfda_enforcement")),
        recalls.ndc_to_family_map(products, ndc),
    )
    rec.to_parquet(out / "recalls_mapped.parquet")

    price_path = out / "prices_family_weekly.parquet"
    if rebuild_prices or not price_path.exists():
        files = sorted(glob.glob(str(data_root() / "raw" / "nadac" / "*" / "nadac_*.csv")))
        prices.price_source(files, prices.package_family_map()).to_parquet(price_path)
    px = pd.read_parquet(price_path)

    sources = {"listings": load_listings(), "products": products, "ndc": ndc, "recalls": rec,
               "prices": px}  # fmt: skip
    for name, module in [("history", history), ("market", market), ("recalls", recalls),
                         ("prices", prices)]:  # fmt: skip
        table = module.build(sources, weeks)
        table.to_parquet(out / f"features_{name}.parquet")
        log.info("features_%s: %s rows", name, len(table))
    if (insp := inspection_sources()) is not None:
        inspections.build(insp, weeks).to_parquet(out / "features_inspections.parquet")
