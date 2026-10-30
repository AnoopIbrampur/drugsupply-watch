"""Our own daily capture of FDA's public drug-shortage list page.

Why: label history so far comes from Internet Archive captures, which have
gaps (one lasted a year). From now on the nightly job saves FDA's page
itself, so future labels and features don't depend on the Archive's
schedule. Files are named by fetch timestamp (UTC) like Wayback captures,
and parsed by the same code (fda_shortage_archive.parse_listing).
"""

from __future__ import annotations

import datetime as dt
import gzip
import logging
from pathlib import Path

from rxradar.http import PoliteClient
from rxradar.ingest.fda_shortage_archive import PAGE, parse_listing
from rxradar.paths import raw_snapshot_dir

log = logging.getLogger(__name__)

SOURCE = "fda_shortage_page"
URL = f"https://www.{PAGE}"
MIN_ROWS = 50  # FDA's list has had 118-575 rows; fewer means an error page


def ingest(client: PoliteClient, now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now(dt.UTC)
    body = client.get_bytes(URL, use_cache=False)
    rows = parse_listing(body.decode("utf-8", errors="replace"))
    if len(rows) < MIN_ROWS:
        raise RuntimeError(f"FDA shortage page parsed to only {len(rows)} rows; not saving")
    out = raw_snapshot_dir(SOURCE, now.date()) / f"{now:%Y%m%d%H%M%S}.html.gz"
    out.write_bytes(gzip.compress(body))
    log.info("FDA shortage page: %s listings -> %s", len(rows), out)
    return out
