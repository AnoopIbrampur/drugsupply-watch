"""Which factories made a drug, and when: DailyMed label version history.

Why: NDC records name the labeler, often not the manufacturer, while FDA
inspections are keyed by facility. Each product label (SPL) lists its
establishments by DUNS, and DailyMed keeps old versions with publication dates,
so a label version published by date t shows what was public about the
manufacturer at t.

Scope is set by a rule that ignores outcomes: generic or injectable families, up
to LABELS_PER_FAMILY labelers each, reading the version in effect on each January
1 plus the latest. Fetching every version would be hundreds of thousands of requests.

Runs are slow (one request per second) and resumable. Results are appended to
data/raw/dailymed/{labels,versions}.jsonl, and finished work is skipped on restart.
Label files aren't cached: 300 KB each would take many gigabytes for a few DUNS numbers.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from pathlib import Path

import pandas as pd

from rxradar.http import PoliteClient
from rxradar.paths import data_root

log = logging.getLogger(__name__)

API = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
FILE_URL = "https://dailymed.nlm.nih.gov/dailymed/getFile.cfm"
DUNS_ROOT = "1.3.6.1.4.1.519.1"
LABELS_PER_FAMILY = 5
FIRST_YEAR = 2016


def output_dir() -> Path:
    path = data_root() / "raw" / "dailymed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def establishment_duns(xml: str) -> list[str]:
    """Establishment DUNS: every DUNS in the document after the labeler's first
    mention. A labeler that makes its own drug is listed again and stays in."""
    found = []
    for tag in re.findall(r"<id [^>]*/>", xml):
        if f'root="{DUNS_ROOT}"' in tag and (m := re.search(r'extension="(\d{9})"', tag)):
            found.append(m.group(1))
    return list(dict.fromkeys(found[1:]))


def versions_to_read(history: list[dict], last_year: int) -> list[dict]:
    """The version in effect each January 1, plus the newest, each once."""
    dated = sorted(
        ((pd.Timestamp(h["published_date"]), h["spl_version"]) for h in history),
        key=lambda x: x[1],
    )
    chosen: dict[int, pd.Timestamp] = {}
    for year in range(FIRST_YEAR, last_year + 1):
        eligible = [(d, v) for d, v in dated if d <= pd.Timestamp(year=year, month=1, day=1)]
        if eligible:
            date, version = eligible[-1]
            chosen[version] = date
    if dated:
        chosen[dated[-1][1]] = dated[-1][0]
    return [{"version": v, "published": d.date().isoformat()} for v, d in sorted(chosen.items())]


def select_products(products: pd.DataFrame, families: list[str]) -> pd.DataFrame:
    """Up to LABELS_PER_FAMILY labelers per family, those with the most products
    first, since repackagers tend to carry few. Ignores shortage outcomes."""
    p = products[products.family.isin(families)].copy()
    p["n"] = p.groupby(["family", "LABELERNAME"]).PRODUCTNDC.transform("size")
    p = p.sort_values(
        ["family", "n", "LABELERNAME", "PRODUCTNDC"], ascending=[True, False, True, True]
    )
    p = p.drop_duplicates(["family", "LABELERNAME"])
    return p.groupby("family").head(LABELS_PER_FAMILY)[["family", "PRODUCTNDC", "LABELERNAME"]]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(path: Path, row: dict) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def _label_xml(client: PoliteClient, set_id: str, version: int) -> str:
    params = {"setid": set_id, "type": "zip", "version": version}
    blob = client.get_bytes(FILE_URL, params=params, use_cache=False)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".xml"))
        return zf.read(name).decode("utf-8", "replace")


def latest_version(client: PoliteClient, set_id: str, known: int) -> dict | None:
    """The label's newest version if it's newer than ``known``, else None."""
    history = client.get_json(f"{API}/spls/{set_id}/history.json")["data"]["history"]
    newest = max(history, key=lambda h: h["spl_version"])
    version = newest["spl_version"]
    if version <= known:
        return None
    published = pd.Timestamp(newest["published_date"]).date().isoformat()
    duns = establishment_duns(_label_xml(client, set_id, version))
    return {"version": version, "published": published, "duns": duns}


def ingest(client: PoliteClient, selected: pd.DataFrame, last_year: int) -> None:
    out = output_dir()
    labels_path, versions_path = out / "labels.jsonl", out / "versions.jsonl"
    done_ndcs = {r["ndc"] for r in _read_jsonl(labels_path)}
    done_versions = {(r["set_id"], r["version"]) for r in _read_jsonl(versions_path)}
    for i, row in enumerate(selected.itertuples(), 1):
        if row.PRODUCTNDC in done_ndcs:
            continue
        try:
            params = {"ndc": row.PRODUCTNDC, "pagesize": 1}
            data = client.get_json(f"{API}/spls.json", params=params)["data"]
            label = {"family": row.family, "ndc": row.PRODUCTNDC, "labeler": row.LABELERNAME}
            if not data:
                _append(labels_path, {**label, "set_id": None})
                continue
            set_id = data[0]["setid"]
            history = client.get_json(f"{API}/spls/{set_id}/history.json")["data"]["history"]
            for v in versions_to_read(history, last_year):
                if (set_id, v["version"]) not in done_versions:
                    duns = establishment_duns(_label_xml(client, set_id, v["version"]))
                    _append(versions_path, {"set_id": set_id, **v, "duns": duns})
                    done_versions.add((set_id, v["version"]))
            # Written last, so an interrupted label is simply redone.
            _append(labels_path, {**label, "set_id": set_id})
        except Exception as exc:  # one bad label shouldn't stop an overnight run
            log.warning("label %s failed: %s", row.PRODUCTNDC, exc)
        if i % 100 == 0:
            log.info("%s/%s labels, %s versions", i, len(selected), len(done_versions))
