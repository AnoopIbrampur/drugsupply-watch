"""Phase 1 audit 5c (part 2): product label (SPL) establishment -> FEI bridge.

Each SPL on DailyMed (NLM) lists the establishments that make the product,
identified by DUNS, with operations like MANUFACTURE or API MANUFACTURE. The
registration file (DECRS) maps DUNS -> FEI, and FEI is what inspections use.
This script samples shortage SPLs and measures how often that chain resolves.
"""

import re
import sys

import pandas as pd

from rxradar.http import PoliteClient
from rxradar.ingest.fda_registry import read_tsv
from rxradar.ingest.openfda import load_results
from rxradar.paths import cache_dir, latest_snapshot_dir

DUNS_ROOT = "1.3.6.1.4.1.519.1"
SPL_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{}.xml"


def establishments(xml: str) -> list[tuple[str, str]]:
    """(DUNS, name) for each establishment in the SPL's establishment section(s).
    The labeler's own DUNS (first in the document) is excluded."""
    # Attribute order differs between labels, so match the <id> tag as a whole.
    found = []
    for tag, name in re.findall(r"(<id [^>]*/>)\s*<name>([^<]+)</name>", xml):
        if f'root="{DUNS_ROOT}"' in tag and (m := re.search(r'extension="(\d{9})"', tag)):
            found.append((m.group(1), name))
    return list(dict.fromkeys(found[1:]))


def main(n: int = 30, seed: int = 7) -> None:
    sh = pd.json_normalize(load_results(latest_snapshot_dir("openfda_shortages")))
    sets = sh["openfda.spl_set_id"].dropna().map(lambda v: v[0]).drop_duplicates()
    sample = sets.sample(min(n, len(sets)), random_state=seed)
    est_dir = latest_snapshot_dir("fda_establishments")
    est = pd.concat([read_tsv(p) for p in est_dir.glob("drls_*.txt")])
    duns_to_fei = est.groupby("DUNS_NUMBER").FEI_NUMBER.apply(set).to_dict()

    client = PoliteClient(min_interval=1.0, cache_dir=cache_dir() / "dailymed")
    rows = []
    for set_id in sample:
        try:
            xml = client.get_bytes(SPL_URL.format(set_id)).decode("utf-8", "replace")
        except Exception as exc:
            rows.append({"set_id": set_id, "n_est": None, "n_with_fei": None, "err": str(exc)[:60]})
            continue
        est_list = establishments(xml)
        hits = [d for d, _ in est_list if d in duns_to_fei]
        rows.append({"set_id": set_id, "n_est": len(est_list), "n_with_fei": len(hits), "err": ""})
    out = pd.DataFrame(rows)
    ok = out[out.n_est.notna()]
    print(f"SPLs sampled: {len(out)}, fetched: {len(ok)}")
    print(f"SPLs listing >=1 establishment: {(ok.n_est > 0).mean():.0%}")
    print(f"SPLs with >=1 establishment DUNS found in DECRS: {(ok.n_with_fei > 0).mean():.0%}")
    print(f"establishment DUNS resolved to FEI: {ok.n_with_fei.sum()}/{ok.n_est.sum()}")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main(*map(int, sys.argv[1:]))
