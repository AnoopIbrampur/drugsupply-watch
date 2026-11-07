"""Plant directory: who each FDA-registered drug plant is and where it is.

The source is FDA's establishment registration files, which FDA blocks from GitHub's
servers, so the directory is built on a home connection and kept in the repository as
``data/plants.csv``. It keeps the firm, city, region, country and registered operations.
Street addresses, contact names and emails are left out: the simulator doesn't need
them, and a list of plants shouldn't be easier to misuse than it has to be.

``build_plant_map`` joins it to the label data from ingest/spl_sites.py. Labels show
which plants may make a drug, not how much each one makes, so the result supports
"exposed", never "will go short" or a share of supply (decisions D13).
"""

from __future__ import annotations

import hashlib
import re
from importlib import resources

import pandas as pd

from rxradar.features.inspections import normalize_fei
from rxradar.ingest.fda_registry import read_tsv
from rxradar.paths import derived_dir, latest_snapshot_dir

COUNTRY = re.compile(r"^(.*?)\s*\(([A-Z]{3})\)$")
REGION_CODE = re.compile(r"\s*\([A-Z]{2}\)")
POSTAL_END = re.compile(r"\s+[A-Z]{0,2}-?\d[\w-]*(\s+\w*\d\w*)?$")
HELENE_FEI = "1025114"  # Baxter, Marion, North Carolina (the North Cove plant)
HELENE_DATE = "2024-09-26"  # the storm's landfall; the plant flooded in the days after


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


ROLES = {
    "MANUFACTURE": "finished",
    "POSITRON EMISSION TOMOGRAPHY DRUG PRODUCTION": "finished",
    "API MANUFACTURE": "ingredient",
}
INSPECTION_YEARS = 3


def plant_slug(name: str, city: str, duns: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", f"{name} {city}".lower()).strip("-")[:60]
    return f"{base}-{hashlib.sha1(duns.encode()).hexdigest()[:6]}"


def plant_family_rows(sites: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """One row per plant, family and role. Only making the drug or its active ingredient
    counts: labs, labelers, packers and repackagers can usually be replaced far faster
    than a production line, and counting them would hide the sole sources."""
    ndc_family = products.drop_duplicates("PRODUCTNDC").set_index("PRODUCTNDC").family
    df = sites.assign(role=sites.operation.str.upper().str.strip().map(ROLES))
    df = df.dropna(subset=["role", "ndc"]).assign(family=lambda d: d.ndc.map(ndc_family))
    df = df.dropna(subset=["family"])
    return df[["plant_duns", "plant_name", "family", "role"]].drop_duplicates(
        ["plant_duns", "family", "role"]
    )


def inspection_counts(plants: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """Plain counts by plant from the same tables the drug pages use. Results that
    were never linked stay unknown (``inspected`` False), not clean."""
    since = today - pd.DateOffset(years=INSPECTION_YEARS)
    insp = pd.read_parquet(derived_dir() / "inspection_events.parquet")
    actions = pd.read_parquet(derived_dir() / "action_events.parquet")
    bad = insp[(insp.code == "OAI") & (insp.published >= since)]
    recent = actions[actions.published >= since]
    links = plants[["duns", "fei"]].drop_duplicates()
    out = links.assign(
        inspected=links.fei.isin(set(insp.fei)),
        failed=links.fei.map(bad.groupby("fei").size()).fillna(0).astype(int),
        actions=links.fei.map(recent.groupby("fei").size()).fillna(0).astype(int),
    )
    return out.groupby("duns", as_index=False).agg(
        inspected=("inspected", "max"), failed=("failed", "sum"), actions=("actions", "sum")
    )


def helene_families(when: str = HELENE_DATE) -> list[str]:
    """Families whose labels, as published by ``when``, named the Marion plant."""
    links = pd.read_parquet(derived_dir() / "label_factories.parquet")
    seen = links[links.published <= pd.Timestamp(when)]
    latest = seen[seen.version == seen.groupby("set_id").version.transform("max")]
    return sorted(set(latest[latest.fei == HELENE_FEI].family))


def build_plant_map(families: list[str], today: pd.Timestamp | None = None) -> dict | None:
    """Everything the plant pages need, for the families on the site. None until
    the plant-map workflow has published label data."""
    snapshot = latest_snapshot_dir("spl_sites")
    if snapshot is None or not (snapshot / "sites.parquet").exists():
        return None
    today = today or pd.Timestamp.today().normalize()
    sites = pd.read_parquet(snapshot / "sites.parquet")
    products = pd.read_parquet(derived_dir() / "product_families.parquet")
    products = products[(products.listing == "current") & products.family.notna()]
    rows = plant_family_rows(sites, products)
    rows = rows[rows.family.isin(set(families))]
    directory = load_plants()
    place = directory.drop_duplicates("duns").set_index("duns")
    counts = inspection_counts(directory, today).set_index("duns")
    plants = []
    for duns, group in rows.groupby("plant_duns"):
        known = duns in place.index
        name = place.name[duns] if known else group.plant_name.iloc[0]
        city = place.city[duns] if known else ""
        plants.append(
            {
                "slug": plant_slug(name, city, duns),
                "name": name,
                "city": city,
                "region": place.region[duns] if known else "",
                "country": place.country[duns] if known else "",
                "finished": sorted(group.family[group.role == "finished"]),
                "ingredient": sorted(group.family[group.role == "ingredient"]),
                "inspected": bool(counts.inspected.get(duns, False)),
                "failed_3y": int(counts.failed.get(duns, 0)),
                "actions_3y": int(counts.actions.get(duns, 0)),
            }
        )
    return {
        "as_of": snapshot.name,
        "labels": int(sites.set_id.nunique()),
        "plants": plants,
        "helene": helene_families(),
    }
