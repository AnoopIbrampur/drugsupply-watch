"""Turn fetched label versions into a family-factory table.

Output ``data/derived/label_factories.parquet``: one row per factory per label
version (family, set_id, version, published, fei). A version listing no factory
keeps one row with fei = NaN, so it still replaces older versions.

DUNS numbers map to FDA facility IDs (FEI) through FDA's registration files,
current and excluded. Those are today's files, so a plant that closed long ago
may not resolve; the audit reports how many don't. The table is built locally and
carried in the saved state, because FDA blocks those files from GitHub's servers.
"""

from __future__ import annotations

import json

import pandas as pd

from rxradar.features.common import NS
from rxradar.features.inspections import normalize_fei
from rxradar.ingest.dailymed import output_dir
from rxradar.ingest.fda_registry import read_tsv
from rxradar.paths import derived_dir, latest_snapshot_dir


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
    out = link_rows(labels, read("versions.jsonl"), duns_to_fei())
    out.to_parquet(derived_dir() / "label_factories.parquet")
    return out
