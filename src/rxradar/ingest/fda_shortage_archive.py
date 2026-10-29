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

import httpx

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


# The same list page has been captured under other URL forms (site root,
# old "Drugshortages.cfm" link, tracking query strings). They are only worth
# fetching inside gaps of the main capture series.
ALT_URLS = [
    (PAGE, "prefix"),
    ("accessdata.fda.gov/scripts/drugshortages/", "exact"),
    ("accessdata.fda.gov/scripts/drugshortages/Drugshortages.cfm", "exact"),
]


def find_gap_captures(
    client: PoliteClient, timestamps: list[str], min_gap_days: int = 22
) -> list[tuple[str, str]]:
    """Return (timestamp, original_url) captures of alternate URL forms that
    fall inside gaps of ``timestamps`` longer than ``min_gap_days``."""
    days = sorted({dt.datetime.strptime(t[:8], "%Y%m%d") for t in timestamps})
    found: dict[str, str] = {}
    for a, b in zip(days[:-1], days[1:], strict=True):
        if (b - a).days < min_gap_days:
            continue
        for url, match in ALT_URLS:
            params = {
                "url": url,
                "matchType": match,
                "from": (a + dt.timedelta(days=1)).strftime("%Y%m%d"),
                "to": (b - dt.timedelta(days=1)).strftime("%Y%m%d"),
                "output": "json",
                "fl": "timestamp,original",
                "filter": "statuscode:200",
            }
            try:
                rows = client.get_json(CDX_URL, params=params)[1:]
            except (ValueError, httpx.HTTPStatusError) as exc:
                # Empty body, HTML error page, or the CDX server is overloaded
                # (persistent 503s seen 2026-10-29). Skip this window; a rerun
                # picks it up.
                log.warning("CDX failed for %s %s..%s: %s", url, params["from"], params["to"], exc)
                continue
            for ts, original in rows:
                if "dsp_" not in original:  # detail pages are not the full list
                    found.setdefault(ts, original)
    return sorted(found.items())


def ingest_gap_fill(client: PoliteClient, snapshot_dir: Path) -> int:
    """Download alternate-URL captures inside gaps (weekly at most) into the
    same snapshot directory. Returns the number of new captures saved."""
    have = [p.name.split(".")[0] for p in snapshot_dir.glob("*.html.gz")]
    extra = find_gap_captures(client, have)
    keep = set(weekly(ts for ts, _ in extra))
    saved = 0
    for ts, original in extra:
        path = snapshot_dir / f"{ts}.html.gz"
        if ts not in keep or path.exists():
            continue
        url = f"https://web.archive.org/web/{ts}id_/{original}"
        try:
            body = client.get_bytes(url)
        except Exception as exc:
            log.warning("gap capture %s failed: %s", ts, exc)
            continue
        if parse_listing(body.decode("utf-8", errors="replace")):
            path.write_bytes(gzip.compress(body))
            saved += 1
    return saved
