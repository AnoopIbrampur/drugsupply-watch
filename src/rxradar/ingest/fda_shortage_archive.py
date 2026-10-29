"""Historical FDA shortage lists, rebuilt from Internet Archive captures.

Why this exists: FDA does not publish shortage history. The openFDA shortage
file and FDA's own Drug Shortages page both list only current shortages,
discontinuations and a handful of recently resolved entries; old episodes
drop off. (A "current_and_historical.xlsx" link on FDA's page returns 404.)

The Wayback Machine has captured FDA's public list page
(accessdata.fda.gov/scripts/drugshortages/default.cfm) roughly daily since
mid-2014. Each capture is an exact copy of what FDA published that day, so
"first capture where X is listed as Current" is a point-in-time-safe start
date (late by at most the gap between captures). We take one capture per ISO
week, which matches our weekly modelling grain and keeps load on the Archive
low.
"""

from __future__ import annotations

import datetime as dt
import gzip
import logging
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from rxradar.http import PoliteClient
from rxradar.paths import raw_snapshot_dir

log = logging.getLogger(__name__)

SOURCE = "fda_shortage_archive"
PAGE = "accessdata.fda.gov/scripts/drugshortages/default.cfm"
CDX_URL = "https://web.archive.org/cdx/search/cdx"

# Each listed drug links to a detail page whose query string carries the
# ingredient/presentation name (AI=) and a status code (st=c|r|d).
_ROW = re.compile(r"dsp_ActiveIngredientDetails\.cfm\?AI=([^&\"]+)&(?:amp;)?st=([a-z])")
STATUS_CODES = {"c": "Current", "r": "Resolved", "d": "To Be Discontinued"}


@dataclass(frozen=True)
class ListingRow:
    name: str
    status: str


def list_captures(client: PoliteClient, start: str = "2010", end: str | None = None) -> list[str]:
    """Return 14-digit capture timestamps, one per ISO week (the earliest that week)."""
    params = {
        "url": PAGE,
        "output": "json",
        "fl": "timestamp,statuscode",
        "filter": "statuscode:200",
        "collapse": "timestamp:8",
        "from": start,
    }
    if end:
        params["to"] = end
    rows = client.get_json(CDX_URL, params=params)[1:]
    return weekly(r[0] for r in rows)


def weekly(timestamps) -> list[str]:
    """Keep the first timestamp in each ISO (year, week)."""
    seen: dict[tuple[int, int], str] = {}
    for ts in sorted(timestamps):
        iso = dt.datetime.strptime(ts[:8], "%Y%m%d").isocalendar()
        seen.setdefault((iso.year, iso.week), ts)
    return list(seen.values())


def capture_url(ts: str) -> str:
    # The "id_" suffix asks for the original bytes, without Wayback's toolbar.
    return f"https://web.archive.org/web/{ts}id_/https://www.{PAGE}"


def parse_listing(html: str) -> list[ListingRow]:
    """Extract (name, status) for each drug listed on one capture of the page.

    A drug appears several times (alphabetical tab, therapeutic-category tabs),
    so rows are de-duplicated. Unknown status codes are kept verbatim so that a
    format change shows up in the audit instead of silently vanishing.
    """
    rows = set()
    for raw_name, code in _ROW.findall(html):
        name = " ".join(urllib.parse.unquote_plus(raw_name).split())
        rows.add(ListingRow(name=name, status=STATUS_CODES.get(code, code)))
    return sorted(rows, key=lambda r: (r.name, r.status))


def ingest(client: PoliteClient, start: str = "2010", out_dir: Path | None = None) -> Path:
    """Download weekly captures (gzipped HTML named by capture timestamp).

    Captures already present in ``out_dir`` or in the client's cache are not
    re-downloaded, so the job can be resumed after interruption.
    """
    out = out_dir or raw_snapshot_dir(SOURCE)
    captures = list_captures(client, start=start)
    log.info("%s weekly captures to fetch", len(captures))
    for i, ts in enumerate(captures, 1):
        path = out / f"{ts}.html.gz"
        if path.exists():
            continue
        try:
            body = client.get_bytes(capture_url(ts))
        except Exception as exc:  # one bad capture should not sink the run
            log.warning("capture %s failed: %s", ts, exc)
            continue
        path.write_bytes(gzip.compress(body))
        if i % 50 == 0:
            log.info("%s/%s captures", i, len(captures))
    return out


def load_listings(snapshot_dir: Path) -> list[dict]:
    """Parse every saved capture into flat rows: capture_ts, name, status."""
    out = []
    for path in sorted(snapshot_dir.glob("*.html.gz")):
        ts = path.name.split(".")[0]
        html = gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
        for row in parse_listing(html):
            out.append({"capture_ts": ts, "name": row.name, "status": row.status})
    return out
