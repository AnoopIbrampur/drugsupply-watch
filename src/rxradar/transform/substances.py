"""Substance reference tables from the openFDA substance (GSRS) file.

Why: drug families are keyed by active moiety (docs/decisions.md D3), but
the NDC text files give ingredient *names* only ("BUPIVACAINE HYDROCHLORIDE"),
and delisted products have no openFDA UNII block at all. The substance file
gives us both missing links:
- every known name and synonym -> UNII, and
- UNII -> active moiety UNII (e.g. dextrose monohydrate -> anhydrous dextrose).

The raw file is one very large JSON document, so we stream it with ijson
and write two small parquet tables next to the raw snapshot.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

NAMES_FILE = "_substance_names.parquet"
MOIETY_FILE = "_substance_moiety.parquet"


def normalize_substance_name(name: str) -> str:
    """Uppercase and collapse whitespace; GSRS and NDC differ only in case
    and spacing for most names."""
    return " ".join(name.upper().split())


def build_tables(snapshot_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stream every substance record and return (names, moiety) tables.

    names:  name_norm, unii, preferred (bool)
    moiety: unii, moiety_unii   (one row per ACTIVE MOIETY relationship)
    """
    import ijson

    names, moiety = [], []
    for path in sorted(snapshot_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        with path.open("rb") as fh:
            for rec in ijson.items(fh, "results.item"):
                unii = rec.get("unii")
                if not unii:
                    continue
                for n in rec.get("names") or []:
                    if n.get("name"):
                        names.append(
                            (normalize_substance_name(n["name"]), unii, bool(n.get("preferred")))
                        )
                for rel in rec.get("relationships") or []:
                    target = (rel.get("related_substance") or {}).get("unii")
                    if rel.get("type") == "ACTIVE MOIETY" and target:
                        moiety.append((unii, target))
    names_df = pd.DataFrame(names, columns=["name_norm", "unii", "preferred"]).drop_duplicates()
    moiety_df = pd.DataFrame(moiety, columns=["unii", "moiety_unii"]).drop_duplicates()
    log.info("substances: %s names, %s moiety links", len(names_df), len(moiety_df))
    return names_df, moiety_df


def load_tables(snapshot_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load cached tables, building them on first use."""
    n, m = snapshot_dir / NAMES_FILE, snapshot_dir / MOIETY_FILE
    if not (n.exists() and m.exists()):
        names, moiety = build_tables(snapshot_dir)
        names.to_parquet(n)
        moiety.to_parquet(m)
    return pd.read_parquet(n), pd.read_parquet(m)
