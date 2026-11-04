"""Phase 3b pilot: can drug families be linked to inspected factories over time?

For a random sample of generic or injectable families (chosen without looking
at shortages), follow: NDC product -> DailyMed label -> label version in effect
on a date -> establishment DUNS -> FDA facility ID (FEI) -> inspection records.
Prints coverage at two dates, plus the numbers needed to size a full run.
"""

import glob
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd

from rxradar.http import PoliteClient
from rxradar.ingest.fda_registry import read_tsv
from rxradar.paths import cache_dir, derived_dir

API = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
FILE = "https://dailymed.nlm.nih.gov/dailymed/getFile.cfm"
DUNS_ROOT = "1.3.6.1.4.1.519.1"
DATES = [pd.Timestamp("2019-01-01"), pd.Timestamp("2026-01-01")]
LABELS_PER_FAMILY = 3


def duns_in(xml: str) -> list[str]:
    """Establishment DUNS: every DUNS in the document except the labeler's
    first mention. A labeler that makes its own drug is listed again."""
    found = []
    for tag in re.findall(r"<id [^>]*/>", xml):
        if f'root="{DUNS_ROOT}"' in tag and (m := re.search(r'extension="(\d{9})"', tag)):
            found.append(m.group(1))
    return list(dict.fromkeys(found[1:]))


def version_at(history: list[dict], date: pd.Timestamp) -> int | None:
    dated = [(pd.Timestamp(h["published_date"]), h["spl_version"]) for h in history]
    eligible = [v for d, v in dated if d <= date]
    return max(eligible) if eligible else None


def label_xml(client: PoliteClient, set_id: str, version: int) -> str:
    blob = client.get_bytes(FILE, params={"setid": set_id, "type": "zip", "version": version})
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".xml"))
        return zf.read(name).decode("utf-8", "replace")


def main(establishments: str, inspections: str, n: int = 200, seed: int = 7) -> None:
    client = PoliteClient(min_interval=1.0, cache_dir=cache_dir() / "dailymed")
    est = pd.concat(read_tsv(Path(p)) for p in glob.glob(f"{establishments}/drls_*.txt"))
    duns_fei = est.groupby("DUNS_NUMBER").FEI_NUMBER.agg(set).to_dict()
    insp = pd.DataFrame(json.loads(Path(inspections).read_text()))
    inspected = {f.lstrip("0") for f in insp.FEINumber}

    panel = pd.read_parquet(derived_dir() / "panel.parquet")
    latest = panel[panel.week == panel.week.max()]
    pool = latest[latest.multisource_generic | latest.injectable].family.sort_values()
    sample = pool.sample(min(n, len(pool)), random_state=seed)
    prod = pd.read_parquet(derived_dir() / "product_families.parquet")
    prod = prod[(prod.listing == "current") & prod.family.isin(sample)]

    rows, requests, started = [], 0, time.time()
    for i, fam in enumerate(sample, 1):
        ndcs = (
            prod[prod.family == fam]
            .drop_duplicates("LABELERNAME")
            .PRODUCTNDC.head(LABELS_PER_FAMILY)
        )
        for ndc in ndcs:
            try:
                data = client.get_json(f"{API}/spls.json", params={"ndc": ndc, "pagesize": 1})[
                    "data"
                ]
                requests += 1
                if not data:
                    rows.append({"family": fam, "ndc": ndc, "found": False})
                    continue
                set_id = data[0]["setid"]
                hist = client.get_json(f"{API}/spls/{set_id}/history.json")["data"]["history"]
                requests += 1
                rec = {"family": fam, "ndc": ndc, "found": True, "n_versions": len(hist)}
                for d in DATES:
                    key = str(d.year)
                    v = version_at(hist, d)
                    rec[f"label_{key}"] = v is not None
                    if v is None:
                        continue
                    duns = duns_in(label_xml(client, set_id, v))
                    requests += 1
                    feis = {f.lstrip("0") for x in duns for f in duns_fei.get(x, ())}
                    rec[f"estab_{key}"] = len(duns)
                    rec[f"fei_{key}"] = len(feis)
                    rec[f"inspected_{key}"] = len(feis & inspected)
                rows.append(rec)
            except Exception as exc:
                rows.append({"family": fam, "ndc": ndc, "found": False, "error": str(exc)[:80]})
        if i % 25 == 0:
            print(
                f"{i}/{len(sample)} families, {requests} requests, {time.time() - started:.0f}s",
                flush=True,
            )

    df = pd.DataFrame(rows)
    df.to_csv(derived_dir() / "pilot_label_link.csv", index=False)
    print(f"\nfamilies {df.family.nunique()}, labels tried {len(df)}, found {df.found.sum()}")
    print("errors:", df.get("error", pd.Series(dtype=str)).notna().sum())
    print(
        "versions per label: median", df.n_versions.median(), "mean", round(df.n_versions.mean(), 1)
    )
    for d in DATES:
        k = str(d.year)
        fam = df.groupby("family")
        print(
            f"{k}: families with a label {(fam[f'label_{k}'].max() == True).mean():.0%}, "  # noqa: E712
            f"an establishment {(fam[f'estab_{k}'].max() > 0).mean():.0%}, "
            f"an FEI {(fam[f'fei_{k}'].max() > 0).mean():.0%}, "
            f"an inspected FEI {(fam[f'inspected_{k}'].max() > 0).mean():.0%}"
        )
    print(f"sample pool {len(pool)} families, {prod.PRODUCTNDC.nunique()} sampled NDCs")


if __name__ == "__main__":
    main(*sys.argv[1:3], *map(int, sys.argv[3:]))
