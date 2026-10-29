"""FDA warning letters (partial).

What is and isn't possible (see docs/data_audit.md):
- fda.gov's warning-letter table offers an xlsx export, but the export ignores
  paging and filter parameters and always returns the same 1,000 rows
  (letters posted roughly 2021-2026). It has no FEI, only company name.
- The FDA Data Dashboard "Compliance Actions" dataset carries warning letters
  keyed by FEI, but needs a (free) API credential that the project owner must
  request; see docs/data_audit.md.

We still snapshot the export daily: from today on, accumulating snapshots gives
a point-in-time record of letters as they are posted.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rxradar.http import PoliteClient
from rxradar.paths import raw_snapshot_dir

log = logging.getLogger(__name__)

SOURCE = "fda_warning_letters"
EXPORT_URL = (
    "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/"
    "compliance-actions-and-activities/warning-letters/datatables-data"
)


def ingest(client: PoliteClient) -> Path:
    out = raw_snapshot_dir(SOURCE)
    body = client.get_bytes(EXPORT_URL, params={"_format": "xlsx"}, use_cache=False)
    if not body.startswith(b"PK"):  # xlsx is a zip; anything else is an error page
        raise RuntimeError("warning letter export did not return an xlsx file")
    (out / "warning_letters.xlsx").write_bytes(body)
    log.info("warning letters -> %s", out)
    return out
