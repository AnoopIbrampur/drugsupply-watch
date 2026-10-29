"""Point-in-time NDC directory snapshots from Internet Archive copies of
FDA's ``ndctext.zip``.

Why: rebuilding "who sold this drug at week t" from *today's* NDC files is
unreliable. Delisted products get purged, and marketing dates are
self-reported. Compared with archived copies, the reconstruction misses about
a third of 2016 families and agrees on single-source status only 61% of the
time (scripts/audit_ndc_backfill.py). The archived files are exactly what FDA
published on their capture date, so supply features use them: at week t, the
latest snapshot captured on or before t.

About 25 captures exist (one per month at most), 2016-06 to today, with a
gap from 2022-04 to 2024-02.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from rxradar.http import PoliteClient
from rxradar.paths import data_root

log = logging.getLogger(__name__)

SOURCE = "fda_ndc_text_archive"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
TARGET = "accessdata.fda.gov/cder/ndctext.zip"
CAPTURE_URL = "https://web.archive.org/web/{}id_/https://www.accessdata.fda.gov/cder/ndctext.zip"


def snapshot_root() -> Path:
    # One folder per *capture* timestamp (not fetch date): the capture date
    # is the point-in-time anchor for these files.
    path = data_root() / "raw" / SOURCE
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_captures(client: PoliteClient) -> list[str]:
    params = {
        "url": TARGET,
        "output": "json",
        "fl": "timestamp",
        "filter": "statuscode:200",
        "collapse": "timestamp:6",
    }
    return [r[0] for r in client.get_json(CDX_URL, params=params)[1:]]


def ingest(client: PoliteClient) -> list[Path]:
    """Download every monthly capture's product.txt (skips ones on disk)."""
    saved = []
    for ts in list_captures(client):
        out = snapshot_root() / ts
        path = out / "product.txt"
        if path.exists():
            saved.append(path)
            continue
        try:
            blob = client.get_bytes(CAPTURE_URL.format(ts))
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                member = next(n for n in zf.namelist() if n.lower().endswith("product.txt"))
                out.mkdir(exist_ok=True)
                path.write_bytes(zf.read(member))
        except Exception as exc:  # a truncated capture shouldn't sink the run
            log.warning("NDC capture %s failed: %s", ts, exc)
            continue
        saved.append(path)
        log.info("NDC snapshot %s", ts)
    return saved
