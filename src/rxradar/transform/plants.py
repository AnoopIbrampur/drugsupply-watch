"""Plant directory: who each FDA-registered drug plant is and where it is.

The source is FDA's establishment registration files, which FDA blocks from GitHub's
servers, so the directory is built on a home connection and kept in the repository as
``data/plants.csv``. It keeps the firm, city, region, country and registered operations.
Street addresses, contact names and emails are left out: the simulator doesn't need
them, and a list of plants shouldn't be easier to misuse than it has to be.
"""

from __future__ import annotations

import re
from importlib import resources

import pandas as pd

from rxradar.features.inspections import normalize_fei
from rxradar.ingest.fda_registry import read_tsv
from rxradar.paths import latest_snapshot_dir

COUNTRY = re.compile(r"^(.*?)\s*\(([A-Z]{3})\)$")
REGION_CODE = re.compile(r"\s*\([A-Z]{2}\)")
POSTAL_END = re.compile(r"\s+[A-Z]{0,2}-?\d[\w-]*(\s+\w*\d\w*)?$")


def parse_place(address: str) -> dict:
    """City, region and country from the end of a registration address.

    Addresses end ``city, region postal, country (ISO)``. The street, which comes
    first, is never kept. An address too short to split keeps the country alone.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts or not (m := COUNTRY.match(parts[-1])):
        return {"city": "", "region": "", "country": ""}
    country = m.group(1)
    if m.group(2) == "KOR":  # written "Korea, South (KOR)", with a comma in the name
        country, parts = "South Korea", parts[:-1]
    region = REGION_CODE.sub("", parts[-2]) if len(parts) >= 3 else ""
    region = POSTAL_END.sub("", region).strip()
    city = parts[-3] if len(parts) >= 3 else ""
    return {"city": city, "region": region, "country": country}


def build_plant_directory() -> pd.DataFrame:
    est_dir = latest_snapshot_dir("fda_establishments")
    est = pd.concat(read_tsv(p) for p in sorted(est_dir.glob("drls_*.txt")))
    places = pd.DataFrame([parse_place(a) for a in est.ADDRESS], index=est.index)
    out = pd.DataFrame(
        {
            "fei": est.FEI_NUMBER.map(normalize_fei),
            "duns": est.DUNS_NUMBER,
            "name": est.FIRM_NAME.str.strip(),
            "city": places.city,
            "region": places.region,
            "country": places.country,
            "operations": est.OPERATIONS.fillna("").str.strip(),
            "registered": est.EXCLUSION_FLAG.eq("N"),
        }
    )
    out = out[out.fei.astype(bool)].sort_values(["registered", "fei"], ascending=[False, True])
    return out.drop_duplicates(["fei", "duns"]).reset_index(drop=True)


def load_plants() -> pd.DataFrame:
    path = resources.files("rxradar.transform") / "data" / "plants.csv"
    with path.open() as fh:
        return pd.read_csv(fh, dtype=str, keep_default_na=False)
