"""Medicaid NADAC (National Average Drug Acquisition Cost) weekly price files.

Why NADAC: sustained price erosion on old generics is a known shortage driver
(manufacturers exit when margins vanish). NADAC is a free, NDC-level weekly
survey of retail pharmacy acquisition cost published by CMS.

data.medicaid.gov publishes one dataset per calendar year (2013 onward). We
discover them from the DKAN metastore rather than hard-coding URLs because the
current-year file is replaced every week under a new filename.

Caveat: NADAC surveys *retail* pharmacies, so hospital injectables (a large
share of shortages) are thinly covered. The audit measures that coverage.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from rxradar.http import PoliteClient
from rxradar.paths import raw_snapshot_dir

log = logging.getLogger(__name__)

SOURCE = "nadac"
METASTORE = "https://data.medicaid.gov/api/1/metastore/schemas/dataset/items"
_TITLE = re.compile(r"^NADAC \(National Average Drug Acquisition Cost\) (\d{4})$")


def list_year_files(client: PoliteClient) -> dict[int, str]:
    """Map calendar year -> CSV download URL for every yearly NADAC dataset."""
    out = {}
    for item in client.get_json(METASTORE):
        m = _TITLE.match(item.get("title", "").strip())
        if not m:
            continue
        urls = [d.get("downloadURL") for d in item.get("distribution", []) if d.get("downloadURL")]
        if urls:
            out[int(m.group(1))] = urls[0]
    return dict(sorted(out.items()))


def ingest(client: PoliteClient, years: list[int] | None = None) -> Path:
    """Download yearly NADAC CSVs. Past years are immutable, so they are
    skipped if already present in any earlier snapshot; the current year is
    always re-fetched."""
    out = raw_snapshot_dir(SOURCE)
    files = list_year_files(client)
    latest = max(files)
    for year, url in files.items():
        if years and year not in years:
            continue
        target = out / f"nadac_{year}.csv"
        earlier = list(out.parent.glob(f"*/nadac_{year}.csv"))
        if year != latest and earlier:
            continue
        target.write_bytes(client.get_bytes(url, use_cache=False))
        log.info("NADAC %s -> %s", year, target)
    return out
