"""openFDA bulk downloads: drug shortages, NDC directory, drug enforcement (recalls).

Why bulk files instead of paging the API: the API caps ``skip`` at 25,000 and
allows ~1,000 requests/day without a key, while each bulk file is one zipped
JSON document listed in https://api.fda.gov/download.json. The manifest also
carries an ``export_date``, which we record next to the snapshot.

Important: these endpoints are *current-state* datasets. The shortage file only
holds shortages FDA currently lists (Current / To Be Discontinued / recently
Resolved), and the NDC file only holds currently listed products. Saving a
dated snapshot every day is the only way to build their history going forward.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

from rxradar.http import PoliteClient
from rxradar.paths import raw_snapshot_dir

log = logging.getLogger(__name__)

MANIFEST_URL = "https://api.fda.gov/download.json"
# dataset -> (manifest category, raw source folder)
DATASETS = {
    "shortages": ("drug", "openfda_shortages"),
    "ndc": ("drug", "openfda_ndc"),
    "enforcement": ("drug", "openfda_enforcement"),
}
# Large, slow-changing reference data, fetched on demand rather than nightly.
# The substance file (GSRS) links salts and hydrates to their active moiety,
# which is how drug families collapse salts (docs/decisions.md D3).
REFERENCE_DATASETS = {"substance": ("other", "openfda_substance")}
# Above this size we trust the manifest's record count instead of parsing
# the whole file just to count it.
COUNT_LIMIT_BYTES = 300_000_000


def fetch_manifest(client: PoliteClient) -> dict:
    """Return the openFDA download manifest, keyed by category (drug, other, ...)."""
    return client.get_json(MANIFEST_URL)["results"]


def ingest_dataset(client: PoliteClient, dataset: str, out_dir: Path | None = None) -> Path:
    """Download every partition of one openFDA drug dataset into a raw snapshot.

    Writes the unzipped JSON partitions plus ``manifest.json`` (export date,
    record counts) so the snapshot is self-describing. Returns the snapshot dir.
    """
    category, source = {**DATASETS, **REFERENCE_DATASETS}[dataset]
    entry = fetch_manifest(client)[category][dataset]
    out = out_dir or raw_snapshot_dir(source)
    total: int | None = 0
    for part in entry["partitions"]:
        blob = client.get_bytes(part["file"], use_cache=False)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                data = zf.read(name)
                (out / name).write_bytes(data)
                if len(data) > COUNT_LIMIT_BYTES:
                    total = None
                elif total is not None:
                    total += len(json.loads(data)["results"])
    if total is not None and total != entry["total_records"]:
        # Trust the data we actually got, but make the mismatch visible.
        log.warning(
            "%s: manifest says %s records, files hold %s", dataset, entry["total_records"], total
        )
    (out / "manifest.json").write_text(json.dumps({**entry, "records_in_files": total}, indent=2))
    log.info("%s: %s records -> %s", dataset, total, out)
    return out


def load_results(snapshot_dir: Path) -> list[dict]:
    """Read all records from a saved openFDA snapshot directory."""
    records: list[dict] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        records.extend(json.loads(path.read_text())["results"])
    return records
