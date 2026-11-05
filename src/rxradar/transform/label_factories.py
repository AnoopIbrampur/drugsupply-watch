"""Turn fetched label versions into a family-factory table.

Output ``data/derived/label_factories.parquet``: one row per factory per label
version (family, set_id, version, published, fei). A version listing no factory
keeps one row with fei = NaN, so it still replaces older versions.

DUNS numbers map to FDA facility IDs (FEI) through FDA's registration files,
current and excluded. Those are today's files, so a plant that closed long ago
may not resolve; the audit reports how many don't. The table is built locally and
carried in the saved state, because FDA blocks those files from GitHub's servers.

``refresh_batch`` keeps it current: each night it checks a few hundred labels for a
newer version and adds it. The DUNS map stays as last built locally, so a factory
that first appears later has no FEI until someone rebuilds it on a home connection.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from rxradar.features.common import NS
from rxradar.features.inspections import normalize_fei
from rxradar.http import PoliteClient
from rxradar.ingest.dailymed import latest_version, output_dir
from rxradar.ingest.fda_registry import read_tsv
from rxradar.paths import derived_dir, latest_snapshot_dir

log = logging.getLogger(__name__)
LABELS_PER_NIGHT = 200


def duns_to_fei() -> dict[str, set[str]]:
    est_dir = latest_snapshot_dir("fda_establishments")
    est = pd.concat(read_tsv(p) for p in sorted(est_dir.glob("drls_*.txt")))
    est = est.assign(fei=est.FEI_NUMBER.map(normalize_fei))
    return est.groupby("DUNS_NUMBER").fei.agg(set).to_dict()


def link_rows(labels: list[dict], versions: list[dict], mapping: dict) -> pd.DataFrame:
    """Expand label versions into family-factory rows."""
    by_set: dict[str, list[dict]] = {}
    for v in versions:
        by_set.setdefault(v["set_id"], []).append(v)
    rows = []
    for label in labels:
        for v in by_set.get(label["set_id"], []):
            feis = sorted({f for d in v["duns"] for f in mapping.get(d, ())})
            for fei in feis or [None]:
                rows.append((label["family"], v["set_id"], v["version"], v["published"], fei))
    df = pd.DataFrame(rows, columns=["family", "set_id", "version", "published", "fei"])
    df["published"] = pd.to_datetime(df.published).astype(NS)
    return df.drop_duplicates()


def build_label_factories() -> pd.DataFrame:
    def read(name: str) -> list[dict]:
        text = (output_dir() / name).read_text()
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    labels = [r for r in read("labels.jsonl") if r["set_id"]]
    mapping = duns_to_fei()
    out = link_rows(labels, read("versions.jsonl"), mapping)
    out.to_parquet(derived_dir() / "label_factories.parquet")
    pairs = [(d, f) for d, feis in mapping.items() for f in feis]
    pd.DataFrame(pairs, columns=["duns", "fei"]).to_parquet(derived_dir() / "duns_fei.parquet")
    return out


def refresh_batch(client: PoliteClient, n: int = LABELS_PER_NIGHT) -> int:
    """Check the next ``n`` labels in rotation; add any newer version. Returns
    how many versions were added. One bad label just waits for its next turn."""
    d = derived_dir()
    table = pd.read_parquet(d / "label_factories.parquet")
    pairs = pd.read_parquet(d / "duns_fei.parquet")
    mapping = pairs.groupby("duns").fei.agg(set).to_dict()
    cursor = d / "label_refresh_cursor.txt"
    set_ids = sorted(table.set_id.unique())
    start = int(cursor.read_text()) if cursor.exists() else 0
    batch = [set_ids[(start + i) % len(set_ids)] for i in range(min(n, len(set_ids)))]
    labels, versions = [], []
    for set_id in batch:
        rows = table[table.set_id == set_id]
        try:
            newest = latest_version(client, set_id, int(rows.version.max()))
        except Exception as exc:
            log.warning("label %s refresh failed: %s", set_id, exc)
            continue
        if newest:
            labels += [{"family": f, "set_id": set_id} for f in rows.family.unique()]
            versions.append({"set_id": set_id, **newest})
    if versions:
        added = link_rows(labels, versions, mapping)
        combined = pd.concat([table, added], ignore_index=True)
        combined.to_parquet(d / "label_factories.parquet")
    cursor.write_text(str((start + len(batch)) % len(set_ids)))
    return len(versions)
